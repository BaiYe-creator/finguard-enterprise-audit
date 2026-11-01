"""FinGuard Streamlit entry point with explicit page state navigation."""

from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from finguard.account_page import render_account_page
from finguard.account_security import AccountSecurityService
from finguard.ai_auditor import AIAuditor, AIAuditResult
from finguard.ai_governance import AIGovernanceService
from finguard.auth_page import render_auth_page
from finguard.backup_manager import BackupManager
from finguard.capacity_page import render_capacity_page
from finguard.config import (
    IngestionConfig,
    RiskConfig,
    risk_config_from_profile,
)
from finguard.data_ingestion import DataIngestionError, DataIngestionService
from finguard.demo import demo_csv_bytes
from finguard.email_delivery import EmailDeliveryConfig, EmailDeliveryService
from finguard.feedback_store import FeedbackStore
from finguard.identity import (
    PERM_BUSINESS_CONVERT,
    PERM_FINANCE_AUDIT,
    PERM_OPERATIONS_VIEW,
    PERM_PPT_GENERATE,
    PERM_RISK_ANALYZE,
    PERM_SETTINGS_AI,
    PERM_WORKSPACE_VIEW,
    AuthContext,
    IdentityService,
)
from finguard.intelligent_page import render_intelligent_page
from finguard.manufacturing_engine import (
    add_history_records,
    convert_business,
    petty_cash_balance,
)
from finguard.postgres_store import PostgresWorkspaceStore
from finguard.ppt_generator import (
    generate_executive_ppt,
    ppt_report_filename,
)
from finguard.reporting import ReportGenerationError, build_excel_report, report_filename
from finguard.risk_engine import RiskEngine, RiskEngineError
from finguard.settings_page import render_settings_page
from finguard.simulator_page import render_simulator_page
from finguard.ui import FinGuardUI
from finguard.v04_hitl import (
    DISPLAY_COLUMNS,
    balance_totals,
    editable_frame,
    merge_source_entries,
    recalculate_reports,
    report_xlsx,
    sanitize_editor_dataframe,
    source_entries,
)
from finguard.workspace_page import render_workspace_page
from finguard.workspace_store import WorkspaceStore

st.set_page_config(
    page_title="FinGuard · 财务智能与高管报告",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

UI = FinGuardUI()
UI.inject_styles()
MAGNIFIER_PATH = Path(__file__).resolve().parent / "assets" / "magnifier.png"
SESSION_COOKIE_NAME = "finguard_session"

FEEDBACK_PATH = Path(
    os.environ.get(
        "FINGUARD_FEEDBACK_PATH",
        str(Path(__file__).resolve().parent / "data" / "review_feedback.json"),
    )
)
WORKSPACE_DB_PATH = Path(
    os.environ.get(
        "FINGUARD_DB_PATH",
        str(Path(__file__).resolve().parent / "data" / "finguard.db"),
    )
)
BACKUP_DIR = Path(
    os.environ.get(
        "FINGUARD_BACKUP_DIR",
        str(WORKSPACE_DB_PATH.parent / "backups"),
    )
)


@st.cache_resource(show_spinner=False)
def _workspace_store(database_path: str) -> WorkspaceStore:
    use_postgres = _secret("FINGUARD_USE_POSTGRES", "false").strip().casefold()
    if use_postgres in {"1", "true", "yes", "on"}:
        database_url = _secret("FINGUARD_DATABASE_URL", "")
        if database_url:
            return PostgresWorkspaceStore(database_url)
    return WorkspaceStore(database_path)


@st.cache_resource(show_spinner=False)
def _identity_service(database_path: str) -> IdentityService:
    return IdentityService(_workspace_store(database_path))


def _backup_manager() -> BackupManager:
    try:
        retention = int(_secret("FINGUARD_BACKUP_RETENTION", "14"))
    except ValueError:
        retention = 14
    return BackupManager(
        _workspace_store(str(WORKSPACE_DB_PATH)),
        BACKUP_DIR,
        retention=retention,
    )


@st.cache_resource(show_spinner=False)
def _ensure_daily_backup(day_key: str) -> dict[str, Any]:
    return _backup_manager().ensure_daily_backup().to_dict()


def _email_delivery_service() -> EmailDeliveryService:
    dev_mode = os.environ.get("FINGUARD_EMAIL_DEV_MODE", "").strip().casefold()
    if not dev_mode:
        dev_mode = _secret("FINGUARD_EMAIL_DEV_MODE", "false").strip().casefold()
    try:
        port = int(_secret("SMTP_PORT", "465"))
    except ValueError:
        port = 465
    return EmailDeliveryService(
        EmailDeliveryConfig(
            host=_secret("SMTP_HOST"),
            port=port,
            username=_secret("SMTP_USER"),
            auth_code=_secret("SMTP_AUTH_CODE"),
            sender=_secret("SMTP_FROM", _secret("SMTP_USER")),
            security=_secret("SMTP_SECURITY", "ssl"),
            dev_mode=dev_mode in {"1", "true", "yes", "on"},
        )
    )


def _account_security_service() -> AccountSecurityService:
    return AccountSecurityService(
        _workspace_store(str(WORKSPACE_DB_PATH)),
        _identity_service(str(WORKSPACE_DB_PATH)),
        _email_delivery_service(),
    )


def _ai_governance_service() -> AIGovernanceService:
    try:
        daily_calls = int(_secret("AI_DAILY_CALL_LIMIT", "100"))
        daily_tokens = int(_secret("AI_DAILY_TOKEN_LIMIT", "200000"))
        cost_per_1k = float(_secret("AI_COST_PER_1K_TOKENS", "0"))
    except ValueError:
        daily_calls, daily_tokens, cost_per_1k = 100, 200_000, 0.0
    return AIGovernanceService(
        _workspace_store(str(WORKSPACE_DB_PATH)),
        daily_call_limit=daily_calls,
        daily_token_limit=daily_tokens,
        cost_per_1k_tokens=cost_per_1k,
    )


def _ai_input_text(data: pd.DataFrame) -> str:
    columns = [
        column
        for column in ("voucher_id", "user", "summary", "Risk_Features")
        if column in data.columns
    ]
    return (
        data.loc[:, columns]
        .head(100)
        .fillna("")
        .astype(str)
        .agg(" | ".join, axis=1)
        .str.cat(sep="\n")
    )


def _active_risk_config() -> tuple[RiskConfig, dict[str, Any] | None]:
    user = _current_user()
    if not user.user_id:
        return RiskConfig(), None
    profile = _workspace_store(str(WORKSPACE_DB_PATH)).get_active_rule_profile(user.organization_id)
    if profile is None:
        return RiskConfig(), None
    try:
        config = risk_config_from_profile(json.loads(profile["config_json"]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return RiskConfig(), profile
    return config, profile


def _auth_required() -> bool:
    value = os.environ.get("FINGUARD_AUTH_REQUIRED", "").strip().casefold()
    if not value:
        value = _secret("FINGUARD_AUTH_REQUIRED", "false").strip().casefold()
    return value in {"1", "true", "yes", "on"}


def _session_limits() -> tuple[int, int]:
    try:
        absolute_hours = max(1, int(_secret("FINGUARD_SESSION_HOURS", "8")))
        idle_minutes = max(5, int(_secret("FINGUARD_IDLE_MINUTES", "30")))
    except ValueError:
        absolute_hours, idle_minutes = 8, 30
    return absolute_hours, idle_minutes


def _empty_auth_context() -> AuthContext:
    return AuthContext(
        organization_id="",
        organization_name="",
        user_id="",
        display_name="",
        role="viewer",
        username="",
    )


def _clear_auth_state(*, notice: str = "") -> None:
    session_id = str(st.session_state.get("auth_browser_session_id", "") or "")
    if session_id:
        try:
            _identity_service(str(WORKSPACE_DB_PATH)).revoke_browser_session(session_id)
        except Exception:
            pass
    st.session_state.auth_user_id = None
    st.session_state.auth_authenticated_at = None
    st.session_state.auth_last_seen_at = None
    st.session_state.auth_browser_session_id = None
    st.session_state.auth_session_touched_at = None
    st.session_state.auth_cookie_clear_pending = True
    if notice:
        st.session_state.auth_notice = notice


def _establish_auth_session(context: AuthContext) -> None:
    identity = _identity_service(str(WORKSPACE_DB_PATH))
    absolute_hours, _ = _session_limits()
    try:
        user_agent = str(st.context.headers.get("user-agent", ""))
    except Exception:
        user_agent = ""
    token, session = identity.create_browser_session(
        context.user_id,
        ttl_seconds=absolute_hours * 3600,
        user_agent=user_agent,
    )
    now = time.time()
    st.session_state.auth_user_id = context.user_id
    st.session_state.auth_authenticated_at = float(session.get("created_at", now))
    st.session_state.auth_last_seen_at = now
    st.session_state.auth_browser_session_id = str(session.get("id", "") or "")
    st.session_state.auth_session_touched_at = now
    st.session_state.auth_pending_cookie_token = token
    st.session_state.auth_cookie_clear_pending = False


def _restore_auth_session(identity: IdentityService) -> None:
    if st.session_state.get("auth_user_id") or st.session_state.get("auth_cookie_checked"):
        return
    st.session_state.auth_cookie_checked = True
    try:
        token = str(st.context.cookies.get(SESSION_COOKIE_NAME, "") or "").strip()
    except Exception:
        token = ""
    if not token:
        return
    _, idle_minutes = _session_limits()
    restored = identity.restore_browser_session(token, idle_minutes=idle_minutes)
    if restored is None:
        st.session_state.auth_cookie_clear_pending = True
        return
    context, session = restored
    now = time.time()
    st.session_state.auth_user_id = context.user_id
    st.session_state.auth_authenticated_at = float(session.get("created_at", now))
    st.session_state.auth_last_seen_at = now
    st.session_state.auth_browser_session_id = str(session.get("id", "") or "")
    st.session_state.auth_session_touched_at = now


def _render_pending_cookie_change() -> None:
    clear_pending = bool(st.session_state.get("auth_cookie_clear_pending"))
    token = str(st.session_state.pop("auth_pending_cookie_token", "") or "")
    if not clear_pending and not token:
        return
    absolute_hours, _ = _session_limits()
    if clear_pending:
        cookie_value = (
            f"{SESSION_COOKIE_NAME}=; Path=/; Max-Age=0; SameSite=Lax"
        )
        st.session_state.auth_cookie_clear_pending = False
    else:
        cookie_value = (
            f"{SESSION_COOKIE_NAME}={token}; Path=/; Max-Age={absolute_hours * 3600}; "
            "SameSite=Lax"
        )
    encoded_cookie = json.dumps(cookie_value)
    components.html(
        f"""
        <script>
        (() => {{
          const value = {encoded_cookie};
          document.cookie = value;
          try {{
            const parentWindow = window.parent;
            const secure = parentWindow.location.protocol === "https:" ? "; Secure" : "";
            parentWindow.document.cookie = value + secure;
          }} catch (error) {{
            // The cookie is still applied in the component origin.
          }}
        }})();
        </script>
        """,
        height=0,
        scrolling=False,
    )


def _current_user() -> AuthContext:
    identity = _identity_service(str(WORKSPACE_DB_PATH))
    if not _auth_required():
        return identity.local_admin()
    user_id = str(st.session_state.get("auth_user_id", "") or "")
    if user_id:
        now = time.time()
        authenticated_at = st.session_state.get("auth_authenticated_at")
        last_seen_at = st.session_state.get("auth_last_seen_at")
        absolute_hours, idle_minutes = _session_limits()
        expired = (
            authenticated_at is None
            or last_seen_at is None
            or now - float(authenticated_at) > absolute_hours * 3600
            or now - float(last_seen_at) > idle_minutes * 60
        )
        if expired:
            _clear_auth_state(notice="登录已过期，请重新登录。")
            st.session_state.session_user_id = None
            return _empty_auth_context()
        context = identity.get_context(user_id)
        if context is not None:
            st.session_state.auth_last_seen_at = now
            touched_at = float(st.session_state.get("auth_session_touched_at", 0) or 0)
            session_id = str(st.session_state.get("auth_browser_session_id", "") or "")
            if session_id and now - touched_at >= 300:
                try:
                    identity.touch_browser_session(session_id)
                except Exception:
                    pass
                st.session_state.auth_session_touched_at = now
            return context
        _clear_auth_state()
    return _empty_auth_context()


def _active_workspace_case() -> dict[str, Any] | None:
    case_id = str(st.session_state.get("active_case_id", "") or "")
    if not case_id:
        return None
    user = _current_user()
    if not user.user_id:
        return None
    return _workspace_store(str(WORKSPACE_DB_PATH)).get_case(
        case_id,
        user.organization_id,
    )


def _archive_workspace_file(
    *,
    kind: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> dict[str, Any] | None:
    case = _active_workspace_case()
    if case is None or not content:
        return None
    user = _current_user()
    store = _workspace_store(str(WORKSPACE_DB_PATH))
    try:
        return store.add_artifact(
            case_id=case["id"],
            kind=kind,
            filename=filename,
            content=content,
            content_type=content_type,
            user_id=user.user_id,
        )
    except Exception as exc:
        st.session_state.workspace_notice = f"项目归档失败：{exc}"
        return None


def _log_workspace_event(
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    case_id: str,
    details: dict[str, Any],
) -> None:
    user = _current_user()
    if not user.user_id:
        return
    _workspace_store(str(WORKSPACE_DB_PATH)).log_event(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        case_id=case_id,
        organization_id=user.organization_id,
        user_id=user.user_id,
        details=details,
    )


def _feedback_store() -> FeedbackStore:
    config = RiskConfig()
    return FeedbackStore(
        FEEDBACK_PATH,
        similarity_threshold=config.feedback_similarity_threshold,
        amount_tolerance=config.feedback_amount_tolerance,
    )


def _secret(name: str, default: str = "") -> str:
    disable_ai = os.environ.get("FINGUARD_DISABLE_AI", "").strip().casefold()
    if name == "DEEPSEEK_API_KEY" and disable_ai in {"1", "true", "yes", "on"}:
        return ""
    if name in os.environ:
        return str(os.environ.get(name, "")).strip()
    try:
        return str(st.secrets[name]).strip()
    except Exception:
        return default


def _deepseek_key() -> str:
    """Prefer the current browser session key, then fall back to Streamlit Secrets."""

    session_key = str(st.session_state.get("deepseek_api_key", "")).strip()
    return session_key or _secret("DEEPSEEK_API_KEY")


def _fingerprint(payload: bytes, filename: str, sheet_name: str | None) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    return hashlib.sha256(f"{digest}|{filename}|{sheet_name or ''}".encode()).hexdigest()


def _initialize_state() -> None:
    defaults: dict[str, Any] = {
        "current_page": "home",
        "analysis": None,
        "analysis_fingerprint": None,
        "analysis_error": "",
        "ppt_bytes": None,
        "ppt_filename": None,
        "ppt_fallback_reason": "",
        "ai_state_key": None,
        "query_dataset_loaded": None,
        "biz_history": [],
        "biz_business_text": "",
        "biz_upload_signature": None,
        "biz_upload_notice": "",
        "v04_capacity_result": None,
        "v04_capacity_assets": [],
        "v04_capacity_asset_source": "",
        "v04_capacity_asset_signature": None,
        "v04_capacity_asset_notice": "",
        "v04_editable_entries": [],
        "v04_hitl_deleted_entry_ids": [],
        "v04_hitl_revision": 0,
        "v04_hitl_notice": "",
        "v04_hitl_reports": {},
        "v04_hitl_balance_sheet_xlsx": b"",
        "v04_hitl_income_statement_xlsx": b"",
        "v04_hitl_last_saved_at": "",
        "v05_last_sim_result": None,
        "v05_ml_summary_text": "",
        "v05_ml_nlp_result": None,
        "v05_ml_anomaly_frame": None,
        "deepseek_api_key": "",
        "api_key_notice": "",
        "active_case_id": None,
        "workspace_notice": "",
        "auth_user_id": None,
        "auth_authenticated_at": None,
        "auth_last_seen_at": None,
        "auth_browser_session_id": None,
        "auth_session_touched_at": None,
        "auth_cookie_checked": False,
        "auth_cookie_clear_pending": False,
        "auth_pending_cookie_token": "",
        "auth_notice": "",
        "auth_access_error": "",
        "session_user_id": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


PAGE_PERMISSIONS = {
    "account": PERM_WORKSPACE_VIEW,
    "workspace": PERM_WORKSPACE_VIEW,
    "finance": PERM_FINANCE_AUDIT,
    "biz": PERM_BUSINESS_CONVERT,
    "ppt": PERM_PPT_GENERATE,
    "mlrisk": PERM_RISK_ANALYZE,
    "capacity": PERM_OPERATIONS_VIEW,
    "simulator": PERM_OPERATIONS_VIEW,
    "settings": PERM_SETTINGS_AI,
}


def _has_page_permission(user: AuthContext, page: str) -> bool:
    permission = PAGE_PERMISSIONS.get(page)
    if permission is None:
        return page == "home"
    return _identity_service(str(WORKSPACE_DB_PATH)).has_permission(user, permission)


def _reset_user_scoped_state() -> None:
    keep = {
        "auth_user_id",
        "auth_authenticated_at",
        "auth_last_seen_at",
        "auth_browser_session_id",
        "auth_session_touched_at",
        "auth_cookie_checked",
        "auth_cookie_clear_pending",
        "auth_pending_cookie_token",
        "session_user_id",
        "auth_notice",
        "auth_access_error",
        "current_page",
    }
    for key in list(st.session_state.keys()):
        if key not in keep:
            st.session_state.pop(key, None)
    st.session_state.current_page = "home"
    _initialize_state()


def _logout_user() -> None:
    _clear_auth_state()
    st.session_state.session_user_id = None
    _reset_user_scoped_state()


def _request_page(page: str) -> None:
    if page not in {
        "finance",
        "biz",
        "ppt",
        "capacity",
        "simulator",
        "mlrisk",
        "settings",
        "workspace",
        "account",
    }:
        st.session_state.current_page = "home"
        return
    st.session_state.current_page = page


def _go_home() -> None:
    st.session_state.current_page = "home"


def _feedback_version() -> int:
    try:
        return FEEDBACK_PATH.stat().st_mtime_ns
    except OSError:
        return 0


@st.cache_data(show_spinner=False, max_entries=6)
def _analyze_payload(
    payload: bytes,
    filename: str,
    sheet_name: str | None,
    risk_config: RiskConfig,
    feedback_version: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    ingestion = DataIngestionService(IngestionConfig()).load_and_clean(
        payload,
        filename=filename,
        sheet_name=sheet_name,
    )
    assessment = RiskEngine(
        risk_config,
        feedback_store=_feedback_store(),
    ).evaluate(ingestion.clean_data)
    return (
        assessment.data,
        ingestion.rejected_data,
        ingestion.report,
        assessment.metrics,
        assessment.distribution,
        [*ingestion.warnings, *assessment.warnings],
    )


@st.cache_data(show_spinner=False, max_entries=3)
def _build_report_cached(
    data: pd.DataFrame,
    metrics: dict[str, Any],
    ingestion_report: dict[str, Any],
    distribution: dict[str, Any],
    rejected_data: pd.DataFrame,
) -> bytes:
    return build_excel_report(
        data,
        metrics,
        ingestion_report,
        distribution,
        rejected_data,
    )


def _store_analysis(
    payload: bytes,
    filename: str,
    sheet_name: str | None,
) -> bool:
    fingerprint = _fingerprint(payload, filename, sheet_name)
    if (
        st.session_state.get("analysis_fingerprint") == fingerprint
        and st.session_state.get("analysis") is not None
    ):
        return True

    try:
        risk_config, rule_profile = _active_risk_config()
        with st.spinner("正在安全清洗凭证并运行三层风险矩阵…"):
            (
                data,
                rejected_data,
                ingestion_report,
                risk_metrics,
                distribution,
                warnings,
            ) = _analyze_payload(
                payload,
                filename,
                sheet_name,
                risk_config,
                _feedback_version(),
            )
    except (DataIngestionError, RiskEngineError, ValueError) as exc:
        st.session_state.analysis_error = f"数据处理失败：{exc}"
        st.session_state.analysis = None
        st.session_state.analysis_fingerprint = None
        return False

    st.session_state.analysis = {
        "data": data,
        "rejected_data": rejected_data,
        "ingestion_report": ingestion_report,
        "metrics": risk_metrics,
        "distribution": distribution,
        "warnings": warnings,
        "filename": filename,
        "sheet_name": sheet_name,
        "source_payload": bytes(payload),
        "rule_profile": (
            {
                "id": rule_profile["id"],
                "name": rule_profile["name"],
                "version": rule_profile["version"],
            }
            if rule_profile
            else {"name": "系统默认规则", "version": 0}
        ),
    }
    st.session_state.analysis_fingerprint = fingerprint
    st.session_state.analysis_error = ""
    st.session_state.ppt_bytes = None
    st.session_state.ppt_filename = None
    st.session_state.ppt_fallback_reason = ""
    st.session_state.ai_state_key = None
    if _active_workspace_case() is not None:
        artifact = _archive_workspace_file(
            kind="source_voucher",
            filename=filename,
            content=payload,
            content_type="application/octet-stream",
        )
        if artifact is not None:
            st.session_state.analysis["workspace_source_artifact_id"] = artifact["id"]
    return True


def _render_subpage_header(title: str, kicker: str, button_key: str) -> None:
    back_column, title_column = st.columns([1, 6], vertical_alignment="center")
    with back_column:
        st.button(
            "← 返回主页",
            on_click=_go_home,
            width="stretch",
            key=button_key,
        )
    with title_column:
        st.markdown(
            f"""
            <div class="fg-subpage-head">
              <div class="fg-kicker">{kicker}</div>
              <h2>{title}</h2>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_upload_panel() -> None:
    st.markdown("### 1. 导入财务数据")
    st.caption("支持 CSV、XLSX、XLSM；数据仅保存在当前 Streamlit 会话中。")
    uploaded = st.file_uploader(
        "上传凭证文件",
        type=["csv", "xlsx", "xlsm"],
        label_visibility="collapsed",
        key="home_uploader",
    )
    demo_clicked = st.button(
        "载入合成演示数据",
        key="load_demo",
        width="stretch",
    )

    if uploaded is not None:
        payload = uploaded.getvalue()
        filename = uploaded.name
        st.session_state.analysis_error = ""
        if len(payload) > IngestionConfig().max_file_mb * 1024 * 1024:
            st.error(f"文件超过 {IngestionConfig().max_file_mb:g} MB 安全上限。")
            return
        sheet_name: str | None = None
        if filename.casefold().endswith((".xlsx", ".xlsm")):
            try:
                sheets = DataIngestionService().list_sheets(payload, filename)
                if not sheets:
                    st.error("Excel 文件中没有可读取的工作表。")
                    return
                sheet_name = st.selectbox("选择待审计工作表", sheets)
            except DataIngestionError as exc:
                st.error(str(exc))
                return
        _store_analysis(payload, filename, sheet_name)

    if demo_clicked:
        _store_analysis(
            demo_csv_bytes(),
            "FinGuard_合成演示凭证.csv",
            None,
        )

    if st.session_state.analysis_error:
        st.error(st.session_state.analysis_error)
    if st.session_state.get("analysis") is not None:
        analysis = st.session_state.analysis
        metrics = analysis["metrics"]
        st.markdown(
            f"""
            <div class="fg-data-state">
              已载入：{html.escape(str(analysis["filename"]))} · {int(metrics["total_count"]):,} 笔 ·
              高风险 {int(metrics["high_risk_count"]):,} 笔
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_home_magnifier() -> None:
    if not MAGNIFIER_PATH.exists():
        return
    encoded = base64.b64encode(MAGNIFIER_PATH.read_bytes()).decode("ascii")
    st.markdown(
        f"""
        <style>
        .fg-magnifier-stage {{
            position: fixed;
            inset: 0;
            z-index: 0;
            display: flex;
            align-items: flex-start;
            justify-content: flex-start;
            overflow: hidden;
            pointer-events: none;
        }}
        .fg-magnifier-image {{
            display: block;
            width: auto;
            height: min(118vh, 1080px);
            max-width: none;
            opacity: 0.12;
            transform-origin: 72% 50%;
            filter:
                blur(1.5px)
                contrast(1.08)
                grayscale(35%)
                drop-shadow(0 24px 30px rgba(18,22,27,.12));
            animation: fg-magnifier-background-drift 10s ease-in-out infinite;
            will-change: transform, opacity, filter;
        }}
        @keyframes fg-magnifier-background-drift {{
            0%, 100% {{
                transform: translate3d(-2vw, -14vh, 0) rotate(-25deg) scale(1.06);
                opacity: 0.115;
            }}
            50% {{
                transform: translate3d(-4vw, -16vh, 0) rotate(-23deg) scale(1.09);
                opacity: 0.135;
            }}
        }}
        .fg-minimal-home,
        .st-key-home_actions,
        .st-key-open_api_settings,
        .st-key-open_workspace,
        .st-key-workspace_card {{
            position: relative;
            z-index: 2;
        }}
        .st-key-open_finance button,
        .st-key-open_ppt button,
        .st-key-open_biz button,
        .st-key-open_capacity button,
        .st-key-open_simulator button,
        .st-key-open_mlrisk button,
        .st-key-open_workspace button,
        .st-key-workspace_card button {{
            background: rgba(255,255,255,.58) !important;
            border-color: rgba(255,255,255,.72) !important;
            color: #20242A !important;
            backdrop-filter: blur(14px) saturate(1.12);
            -webkit-backdrop-filter: blur(14px) saturate(1.12);
            cursor: pointer;
            transition:
                transform .18s ease,
                box-shadow .18s ease,
                background .18s ease,
                border-color .18s ease !important;
        }}
        .st-key-open_workspace button,
        .st-key-workspace_card button {{
            box-shadow: 0 12px 30px rgba(35,45,56,.06) !important;
        }}
        .st-key-open_finance button {{
            background: rgba(255,255,255,.58) !important;
            border-color: rgba(255,255,255,.72) !important;
            color: #20242A !important;
        }}
        .st-key-open_finance button:hover {{
            background: rgba(255,255,255,.74) !important;
        }}
        .st-key-open_ppt button:hover,
        .st-key-open_biz button:hover,
        .st-key-open_capacity button:hover,
        .st-key-open_simulator button:hover,
        .st-key-open_mlrisk button:hover {{
            background: rgba(255,255,255,.74) !important;
        }}
        .st-key-open_workspace button:hover,
        .st-key-workspace_card button:hover {{
            background: rgba(255,255,255,.74) !important;
            transform: translateY(-2px) !important;
            box-shadow: 0 17px 36px rgba(35,45,56,.1) !important;
        }}
        @media (prefers-reduced-motion: reduce) {{
            .fg-magnifier-image {{
                animation: none;
                transform: rotate(-25deg) scale(1.06);
            }}
        }}
        </style>
        <div class="fg-magnifier-stage">
          <img
            class="fg-magnifier-image"
            src="data:image/png;base64,{encoded}"
            alt="FinGuard audit magnifier"
          />
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_home() -> None:
    user = _current_user()
    if _auth_required():
        spacer, account_column, key_column, logout_column = st.columns(
            [4.65, 1.45, 1.45, 1.45],
            vertical_alignment="center",
        )
    else:
        spacer, key_column = st.columns(
            [8, 1.55],
            vertical_alignment="center",
        )
        account_column = None
        logout_column = None
    with spacer:
        if user.user_id:
            st.caption(f"{user.organization_name} · {user.display_name} · {user.role_label}")
    if account_column is not None:
        with account_column:
            st.button(
                "账号安全",
                on_click=_request_page,
                args=("account",),
                width="stretch",
                key="open_account_security",
            )
    with key_column:
        if _has_page_permission(user, "settings"):
            st.button(
                "API 密钥配置",
                on_click=_request_page,
                args=("settings",),
                width="stretch",
                key="open_api_settings",
            )
    if logout_column is not None:
        with logout_column:
            st.button(
                "退出登录",
                on_click=_logout_user,
                width="stretch",
                key="logout_auth",
            )
    st.markdown(
        """
        <div class="fg-minimal-home">
          <div class="fg-minimal-line"></div>
          <h1>FinGuard <span>智能审计</span></h1>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _render_home_magnifier()
    access_error = st.session_state.pop("auth_access_error", "")
    if access_error:
        st.error(access_error)
    auth_notice = st.session_state.pop("auth_notice", "")
    if auth_notice:
        st.success(auth_notice)
    with st.container(key="home_actions"):
        first_row = st.columns(3, gap="large")
        with first_row[0]:
            if _has_page_permission(user, "finance"):
                st.button(
                    "1. 财务审计",
                    on_click=_request_page,
                    args=("finance",),
                    width="stretch",
                    key="open_finance",
                )
        with first_row[1]:
            if _has_page_permission(user, "ppt"):
                st.button(
                    "2. 生成高管 PPT",
                    on_click=_request_page,
                    args=("ppt",),
                    width="stretch",
                    key="open_ppt",
                )
        with first_row[2]:
            if _has_page_permission(user, "biz"):
                st.button(
                    "3. 业财转换引擎",
                    on_click=_request_page,
                    args=("biz",),
                    width="stretch",
                    key="open_biz",
                )
        second_row = st.columns(3, gap="large")
        with second_row[0]:
            if _has_page_permission(user, "capacity"):
                st.button(
                    "4. 产能推演沙盘",
                    on_click=_request_page,
                    args=("capacity",),
                    width="stretch",
                    key="open_capacity",
                )
        with second_row[1]:
            if _has_page_permission(user, "simulator"):
                st.button(
                    "5. 经营模拟器",
                    on_click=_request_page,
                    args=("simulator",),
                    width="stretch",
                    key="open_simulator",
                )
        with second_row[2]:
            if _has_page_permission(user, "mlrisk"):
                st.button(
                    "6. 智能化引擎",
                    on_click=_request_page,
                    args=("mlrisk",),
                    width="stretch",
                    key="open_mlrisk",
                )
        if _has_page_permission(user, "workspace"):
            with st.container(key="workspace_card"):
                st.button(
                    "7. 审计项目与证据链",
                    on_click=_request_page,
                    args=("workspace",),
                    width="stretch",
                    key="open_workspace",
                )


def _render_capacity_page() -> None:
    render_capacity_page(_render_subpage_header)


def _render_simulator_page() -> None:
    render_simulator_page(_render_subpage_header)


def _render_ml_risk_page() -> None:
    user = _current_user()
    governance = _ai_governance_service()

    def transform_summary(text: str) -> tuple[str, int]:
        result = governance.redact(text)
        return result.text, result.replacement_count

    def record_analysis(text: str, redactions: int, status: str) -> None:
        governance.record(
            organization_id=user.organization_id,
            user_id=user.user_id,
            module="financial_narrative",
            model=_secret("DEEPSEEK_MODEL", "deepseek-chat"),
            input_text=text,
            redaction_count=redactions,
            status=status,
        )

    def check_budget() -> tuple[bool, str]:
        decision = governance.decide(user.organization_id)
        return decision.allowed, decision.message

    render_intelligent_page(
        _render_subpage_header,
        deepseek_key=_deepseek_key,
        model=_secret("DEEPSEEK_MODEL", "deepseek-chat"),
        text_transform=transform_summary,
        analysis_callback=record_analysis,
        budget_check=check_budget,
    )


def _render_settings_page() -> None:
    render_settings_page(
        _render_subpage_header,
        deepseek_key=_deepseek_key,
        secret=_secret,
    )


def _render_workspace_page() -> None:
    render_workspace_page(
        _render_subpage_header,
        store=_workspace_store(str(WORKSPACE_DB_PATH)),
        identity=_identity_service(str(WORKSPACE_DB_PATH)),
        user=_current_user(),
        ai_governance=_ai_governance_service(),
        backup_manager=_backup_manager(),
    )


def _render_account_page() -> None:
    render_account_page(
        _render_subpage_header,
        user=_current_user(),
        identity=_identity_service(str(WORKSPACE_DB_PATH)),
        security=_account_security_service(),
    )


def _render_finance_page() -> None:
    _render_subpage_header(
        "财务审计工作台",
        "FINANCIAL AUDIT",
        "back_from_finance",
    )
    _render_upload_panel()
    analysis = st.session_state.analysis
    if analysis is None:
        st.info("请先导入凭证文件或载入演示数据。")
        return

    default_model = _secret("DEEPSEEK_MODEL", "deepseek-chat")
    controls = UI.sidebar(default_model=default_model)
    api_key = _deepseek_key()
    if not api_key:
        with st.sidebar:
            st.warning("未配置 DEEPSEEK_API_KEY，AI 研判将跳过。")

    data = analysis["data"].copy()
    risk_config, _ = _active_risk_config()
    high_risk_mask = data["Risk_Score"] > risk_config.ai_trigger_threshold
    model = controls["model"] or default_model
    user = _current_user()
    ai_governance = _ai_governance_service()
    ai_decision = ai_governance.decide(user.organization_id)
    raw_ai_input = _ai_input_text(data.loc[high_risk_mask])
    redacted_ai_input = ai_governance.redact(raw_ai_input)
    redactor = lambda text: ai_governance.redact(text).text  # noqa: E731

    if controls["auto_ai"] and high_risk_mask.any() and api_key and ai_decision.allowed:
        key_digest = hashlib.sha256(f"{api_key}|{model}".encode()).hexdigest()[:16]
        ai_state_key = f"{st.session_state.analysis_fingerprint}:{key_digest}"
        if st.session_state.get("ai_state_key") != ai_state_key:
            with st.spinner("DeepSeek 正在逐笔生成高风险凭证审计建议…"):
                result: AIAuditResult = AIAuditor(
                    api_key=api_key,
                    model=model,
                    text_redactor=redactor,
                ).audit_batch(
                    data,
                    trigger_threshold=risk_config.ai_trigger_threshold,
                )
            ai_governance.record(
                organization_id=user.organization_id,
                user_id=user.user_id,
                module="financial_audit",
                model=model,
                input_text=redacted_ai_input.text,
                redaction_count=redacted_ai_input.replacement_count,
                status="success" if result.failed_count == 0 else "partial_failure",
            )
            data = result.data
            analysis["data"] = data
            st.session_state.analysis = analysis
            st.session_state.ai_state_key = ai_state_key
    elif high_risk_mask.any() and not api_key:
        st.warning(
            f"发现 {int(high_risk_mask.sum())} 笔高风险凭证。"
            "规则引擎和分析图表已完成，配置 DeepSeek Key 后可补充 AI 研判。"
        )
    elif high_risk_mask.any() and not ai_decision.allowed:
        st.warning(ai_decision.message)

    UI.render_metrics(analysis["metrics"])
    feedback_notice = st.session_state.pop("feedback_notice", "")
    if feedback_notice:
        st.success(feedback_notice)
    refund_count = int(analysis["metrics"].get("refund_count", 0))
    refund_amount = float(analysis["metrics"].get("refund_amount", 0))
    if refund_count:
        st.caption(
            f"已识别 {refund_count:,} 笔退款/红字冲销，合计 {refund_amount:,.2f} 元；"
            "这些记录保留在明细中，但不参与常规风险评分。"
        )
    note_column, action_column = st.columns([3, 1], vertical_alignment="bottom")
    with note_column:
        st.caption(
            f"文件：{analysis['filename']}"
            + (f" · 工作表：{analysis['sheet_name']}" if analysis.get("sheet_name") else "")
            + f" · 平均风险分 {analysis['metrics'].get('average_risk_score', 0):.1f}"
        )
    with action_column:
        rerun_ai = st.button(
            "继续研判未完成项",
            width="stretch",
            disabled=not bool(api_key),
        )

    if rerun_ai:
        rerun_decision = ai_governance.decide(user.organization_id)
        if not rerun_decision.allowed:
            st.warning(rerun_decision.message)
        else:
            with st.spinner("正在处理此前未完成的高风险凭证…"):
                result = AIAuditor(
                    api_key=api_key,
                    model=model,
                    text_redactor=redactor,
                ).audit_batch(
                    st.session_state.analysis["data"],
                    trigger_threshold=risk_config.ai_trigger_threshold,
                )
            ai_governance.record(
                organization_id=user.organization_id,
                user_id=user.user_id,
                module="financial_audit_retry",
                model=model,
                input_text=redacted_ai_input.text,
                redaction_count=redacted_ai_input.replacement_count,
                status="success" if result.failed_count == 0 else "partial_failure",
            )
            analysis["data"] = result.data
            st.session_state.analysis = analysis

    current_data = st.session_state.analysis["data"]
    UI.render_charts(current_data)
    UI.render_table(current_data)
    _render_feedback_review(st.session_state.analysis)
    UI.render_quality(
        analysis["ingestion_report"],
        analysis["rejected_data"],
        analysis["warnings"],
    )

    st.markdown("### 审计报告交付")
    report_column, button_column = st.columns([2, 1], vertical_alignment="center")
    report_bytes = b""
    with report_column:
        st.caption("导出包含管理摘要、异常明细、全部凭证、质量记录和 AI 建议的 Excel 底稿。")
    with button_column:
        with st.spinner("准备 Excel 报告…"):
            try:
                report_bytes = _build_report_cached(
                    current_data,
                    analysis["metrics"],
                    analysis["ingestion_report"],
                    analysis["distribution"],
                    analysis["rejected_data"],
                )
                st.download_button(
                    "下载审计 Excel 报告",
                    data=report_bytes,
                    file_name=report_filename(),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch",
                )
            except ReportGenerationError as exc:
                st.error(str(exc))

    active_case = _active_workspace_case()
    if active_case is not None and report_bytes:
        archive_column, archive_button_column = st.columns([2, 1], vertical_alignment="center")
        with archive_column:
            st.caption(
                f"当前项目：{active_case['title']} · 可把原始凭证、风险明细和 Excel 报告一并归档。"
            )
        with archive_button_column:
            archive_clicked = st.button(
                "归档当前审计成果",
                width="stretch",
                key="archive_audit_result",
            )
        if archive_clicked:
            archived = 0
            source_payload = analysis.get("source_payload")
            if isinstance(source_payload, bytes):
                if _archive_workspace_file(
                    kind="source_voucher",
                    filename=str(analysis.get("filename", "source_voucher.csv")),
                    content=source_payload,
                    content_type="application/octet-stream",
                ):
                    archived += 1
            risk_bytes = current_data.to_csv(index=False).encode("utf-8-sig")
            if _archive_workspace_file(
                kind="risk_csv",
                filename=f"FinGuard_风险明细_{pd.Timestamp.now():%Y%m%d}.csv",
                content=risk_bytes,
                content_type="text/csv",
            ):
                archived += 1
            if _archive_workspace_file(
                kind="audit_excel",
                filename=report_filename(),
                content=report_bytes,
                content_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ):
                archived += 1
            _log_workspace_event(
                action="analysis.archived",
                entity_type="audit_case",
                entity_id=active_case["id"],
                case_id=active_case["id"],
                details={
                    "filename": analysis.get("filename", ""),
                    "artifact_count": archived,
                    "high_risk_count": int(analysis["metrics"].get("high_risk_count", 0)),
                },
            )
            st.session_state.workspace_notice = f"审计成果已归档，共处理 {archived} 个附件。"
            st.rerun()


def _render_ppt_page() -> None:
    _render_subpage_header(
        "高管 PPT 生成",
        "EXECUTIVE PRESENTATION",
        "back_from_ppt",
    )
    analysis = st.session_state.analysis
    if analysis is None:
        st.error("当前没有财务数据，请返回主页进入“财务审计”并上传凭证。")
        return

    UI.render_metrics(analysis["metrics"])
    st.markdown(
        """
        <div class="fg-data-state">
          将基于当前风险分析结果生成 16:9 高管演示文稿，包含执行摘要、风险结构、
          异常明细和核查行动建议。
        </div>
        """,
        unsafe_allow_html=True,
    )
    api_key = _deepseek_key()
    model = _secret("DEEPSEEK_MODEL", "deepseek-chat")
    user = _current_user()
    governance = _ai_governance_service()
    redacted_input = governance.redact(_ai_input_text(analysis["data"]))
    ai_decision = governance.decide(user.organization_id) if api_key else None
    if ai_decision is not None and not ai_decision.allowed:
        st.warning(ai_decision.message)
        api_key = ""
    generate_clicked = st.button(
        "一键生成高管PPT",
        type="primary",
        width="stretch",
    )

    if generate_clicked:
        with st.spinner("正在生成高管 PPT，复杂图表与长文本正在自适应排版…"):
            try:
                result = generate_executive_ppt(
                    st.session_state.analysis["data"],
                    st.session_state.analysis["metrics"],
                    api_key=api_key,
                    model=model,
                    business_history=st.session_state.get("biz_history", []),
                    text_redactor=lambda text: governance.redact(text).text,
                )
                if result.used_ai:
                    governance.record(
                        organization_id=user.organization_id,
                        user_id=user.user_id,
                        module="executive_ppt",
                        model=model,
                        input_text=redacted_input.text,
                        redaction_count=redacted_input.replacement_count,
                        status="success",
                    )
                st.session_state.ppt_bytes = result.content
                st.session_state.ppt_filename = ppt_report_filename()
                st.session_state.ppt_fallback_reason = result.fallback_reason
                active_case = _active_workspace_case()
                if active_case is not None:
                    artifact = _archive_workspace_file(
                        kind="executive_ppt",
                        filename=st.session_state.ppt_filename,
                        content=result.content,
                        content_type=(
                            "application/vnd.openxmlformats-officedocument."
                            "presentationml.presentation"
                        ),
                    )
                    if artifact is not None:
                        _log_workspace_event(
                            action="ppt.generated",
                            entity_type="artifact",
                            entity_id=artifact["id"],
                            case_id=active_case["id"],
                            details={"filename": artifact["filename"]},
                        )
            except Exception as exc:
                st.session_state.ppt_bytes = None
                st.error(f"PPT 生成失败：{type(exc).__name__}: {exc}")

    if st.session_state.ppt_fallback_reason:
        st.info(
            f"AI 摘要使用安全备用文本：{st.session_state.ppt_fallback_reason}",
            icon="ℹ️",
        )
    if st.session_state.ppt_bytes:
        st.success("高管 PPT 已生成，可下载。")
        st.download_button(
            f"下载 {st.session_state.ppt_filename}",
            data=st.session_state.ppt_bytes,
            file_name=st.session_state.ppt_filename,
            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            width="stretch",
            type="primary",
        )


def _refresh_analysis_with_feedback(analysis: dict[str, Any]) -> None:
    previous = analysis["data"]
    previous_advice = previous["ai_advice"].copy() if "ai_advice" in previous else None
    assessment = RiskEngine(
        _active_risk_config()[0],
        feedback_store=_feedback_store(),
    ).evaluate(previous)
    current = assessment.data
    if previous_advice is not None:
        current["ai_advice"] = previous_advice.reindex(current.index, fill_value="")
        has_advice = current["ai_advice"].astype(str).str.strip().ne("")
        current.loc[has_advice, "ai_status"] = "已完成"
    analysis["data"] = current
    analysis["metrics"] = assessment.metrics
    analysis["distribution"] = assessment.distribution
    st.session_state.analysis = analysis


def _render_feedback_review(analysis: dict[str, Any]) -> None:
    data = analysis["data"]
    reviewable = data.loc[
        pd.to_numeric(data["Risk_Score"], errors="coerce").fillna(0).ge(30)
    ].sort_values(["Risk_Score", "amount"], ascending=[False, False])
    store = _feedback_store()
    if reviewable.empty:
        return

    with st.expander("人工复核反馈", expanded=False):
        st.caption(
            "确认一笔凭证无误后，系统仅对摘要相似且金额接近的后续凭证降低风险分。"
            f"当前已保存 {store.count():,} 条反馈。"
        )
        for _, row in reviewable.head(20).iterrows():
            columns = st.columns([1.1, 1, 3.2, 1, 1.3])
            columns[0].write(str(row.get("voucher_id", "")))
            columns[1].write(str(row.get("user", "")))
            columns[2].write(str(row.get("summary", ""))[:90])
            columns[3].write(f"{float(row.get('Risk_Score', 0)):.0f} 分")
            key_digest = hashlib.sha256(str(row.get("voucher_id", "")).encode()).hexdigest()[:10]
            if columns[4].button(
                "标记为已核实无误",
                key=f"verified_{key_digest}",
                width="stretch",
            ):
                store.add(
                    voucher_id=row.get("voucher_id", ""),
                    summary=row.get("summary", ""),
                    amount=row.get("amount", 0),
                    risk_features=row.get("Risk_Features", ""),
                    user=row.get("user", ""),
                )
                _refresh_analysis_with_feedback(analysis)
                st.session_state.feedback_notice = (
                    f"已保存 {row.get('voucher_id', '')} 的人工复核反馈；"
                    "同类摘要和相近金额的风险分已下调。"
                )
                st.rerun()


BUSINESS_COLUMN_ALIASES = (
    "业务描述",
    "业务内容",
    "业务事项",
    "摘要",
    "说明",
    "事项",
    "业务",
    "description",
    "business_description",
    "business",
    "summary",
    "text",
)


def _excel_sheet_names(payload: bytes) -> list[str]:
    workbook = pd.ExcelFile(io.BytesIO(payload))
    return [str(name) for name in workbook.sheet_names]


def _business_column(dataframe: pd.DataFrame) -> str:
    if dataframe.empty:
        raise ValueError("文件中没有可读取的数据行")
    normalized = {
        str(column): str(column).strip().casefold().replace(" ", "") for column in dataframe.columns
    }
    for alias in BUSINESS_COLUMN_ALIASES:
        alias_key = alias.casefold().replace(" ", "")
        for column, column_key in normalized.items():
            if column_key == alias_key:
                return column
    for alias in BUSINESS_COLUMN_ALIASES:
        alias_key = alias.casefold().replace(" ", "")
        for column, column_key in normalized.items():
            if alias_key in column_key or column_key in alias_key:
                return column
    for column in dataframe.columns:
        if dataframe[column].dtype == object:
            return str(column)
    return str(dataframe.columns[0])


def _business_file_to_text(
    payload: bytes,
    *,
    filename: str,
    sheet_name: str | None = None,
) -> tuple[str, str, int]:
    suffix = Path(filename).suffix.casefold()
    if suffix == ".csv":
        last_error: Exception | None = None
        dataframe = None
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                dataframe = pd.read_csv(
                    io.BytesIO(payload),
                    encoding=encoding,
                    sep=None,
                    engine="python",
                )
                break
            except (UnicodeDecodeError, pd.errors.ParserError) as exc:
                last_error = exc
        if dataframe is None:
            raise ValueError(f"CSV 解析失败：{last_error}")
    elif suffix == ".xlsx":
        dataframe = pd.read_excel(
            io.BytesIO(payload),
            sheet_name=sheet_name or 0,
        )
    else:
        raise ValueError("仅支持 CSV 或 XLSX 文件")

    column = _business_column(dataframe)
    values: list[str] = []
    for value in dataframe[column].dropna().tolist():
        text = str(value).strip()
        if not text or text.casefold() == "nan":
            continue
        lines = [line.strip() for line in text.replace("\r\n", "\n").splitlines()]
        values.append("\n".join(line for line in lines if line))
    if not values:
        raise ValueError(f"业务描述列“{column}”中没有有效内容")
    return "\n".join(values), column, len(values)


def _store_v04_reports(entries: list[dict[str, Any]]) -> None:
    reports = recalculate_reports(entries)
    st.session_state.v04_hitl_reports = reports
    st.session_state.v04_hitl_balance_sheet_xlsx = report_xlsx(
        reports["balance_sheet"],
        "资产负债表",
    )
    st.session_state.v04_hitl_income_statement_xlsx = report_xlsx(
        reports["income_statement"],
        "利润表",
    )


def _sync_v04_entries(history: list[dict[str, Any]]) -> None:
    incoming = source_entries(history)
    saved_entries = st.session_state.get("v04_editable_entries", [])
    deleted_ids = set(st.session_state.get("v04_hitl_deleted_entry_ids", []))
    merged = merge_source_entries(incoming, saved_entries, deleted_ids)
    previous_ids = {str(entry.get("entry_id", "")) for entry in saved_entries}
    merged_ids = {str(entry.get("entry_id", "")) for entry in merged}
    entries_changed = previous_ids != merged_ids

    if merged != saved_entries:
        st.session_state.v04_editable_entries = merged
    if entries_changed and saved_entries:
        st.session_state.v04_hitl_revision += 1
        st.session_state.v04_hitl_reports = {}
    if not st.session_state.v04_hitl_reports:
        _store_v04_reports(merged)
        st.session_state.v04_hitl_last_saved_at = pd.Timestamp.now().isoformat(timespec="seconds")


def _render_v04_hitl(history: list[dict[str, Any]]) -> None:
    _sync_v04_entries(history)
    entries = st.session_state.get("v04_editable_entries", [])
    if not entries:
        st.info("当前没有可进入人工复核的已生成分录。")
        return

    st.markdown("### V0.4 人工复核")
    st.caption(
        "编辑只停留在草稿层。只有点击“保存修改并重算”后才会写入会话状态；"
        "未保存刷新不会改变上次保存的报表。"
    )
    notice = st.session_state.pop("v04_hitl_notice", "")
    if notice:
        st.success(notice)
    if st.session_state.get("v04_hitl_last_saved_at"):
        st.caption(f"上次保存：{st.session_state.v04_hitl_last_saved_at}")

    editor_key = f"v04_entry_editor_{st.session_state.get('v04_hitl_revision', 0)}"
    with st.form("v04_hitl_form"):
        edited = st.data_editor(
            editable_frame(entries),
            key=editor_key,
            width="stretch",
            hide_index=True,
            num_rows="dynamic",
            column_order=list(DISPLAY_COLUMNS),
            disabled=["entry_id", "状态"],
            column_config={
                "entry_id": st.column_config.TextColumn("分录 ID"),
                "业务描述": st.column_config.TextColumn("业务描述", width="large"),
                "借方科目": st.column_config.TextColumn("借方科目"),
                "贷方科目": st.column_config.TextColumn("贷方科目"),
                "金额": st.column_config.NumberColumn("金额", format="%.2f"),
                "说明": st.column_config.TextColumn("说明", width="large"),
                "状态": st.column_config.TextColumn("状态"),
            },
        )
        save_clicked = st.form_submit_button(
            "保存修改并重算",
            type="primary",
            width="stretch",
        )

    if save_clicked:
        try:
            clean_entries, invalid_amounts = sanitize_editor_dataframe(edited)
        except ValueError:
            st.error("借贷不配平，请检查修改后的分录！")
            return
        debit_total, credit_total, balanced = balance_totals(clean_entries)
        if invalid_amounts or not balanced or not clean_entries:
            st.error("借贷不配平，请检查修改后的分录！")
            return

        previous_ids = {str(entry.get("entry_id", "")) for entry in entries}
        current_ids = {str(entry.get("entry_id", "")) for entry in clean_entries}
        deleted_ids = set(st.session_state.get("v04_hitl_deleted_entry_ids", []))
        deleted_ids.update(previous_ids - current_ids)

        st.session_state.v04_editable_entries = clean_entries
        st.session_state.v04_hitl_deleted_entry_ids = sorted(deleted_ids)
        _store_v04_reports(clean_entries)
        st.session_state.v04_hitl_last_saved_at = pd.Timestamp.now().isoformat(timespec="seconds")
        st.session_state.v04_hitl_revision += 1
        st.session_state.v04_hitl_notice = (
            f"修改已保存并重算：借方 {debit_total:,.2f}，贷方 {credit_total:,.2f}。"
        )
        st.rerun()

    reports = st.session_state.get("v04_hitl_reports") or {}
    if not reports:
        return
    st.caption("报表由保存后的分录通过本地 Pandas 重算，未调用任何 AI 或外部 API。")
    trial_tab, balance_tab, income_tab = st.tabs(["试算平衡表", "资产负债表", "利润表"])
    with trial_tab:
        st.dataframe(
            reports["trial_balance"],
            width="stretch",
            hide_index=True,
        )
    with balance_tab:
        st.dataframe(
            reports["balance_sheet"],
            width="stretch",
            hide_index=True,
        )
    with income_tab:
        st.dataframe(
            reports["income_statement"],
            width="stretch",
            hide_index=True,
        )

    download_left, download_right = st.columns(2)
    with download_left:
        st.download_button(
            "下载最新资产负债表",
            data=st.session_state.v04_hitl_balance_sheet_xlsx,
            file_name="FinGuard_V04_资产负债表.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
    with download_right:
        st.download_button(
            "下载最新利润表",
            data=st.session_state.v04_hitl_income_statement_xlsx,
            file_name="FinGuard_V04_利润表.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )
    active_case = _active_workspace_case()
    if active_case is not None:
        if st.button(
            "归档复核后财务报表",
            width="stretch",
            key="archive_hitl_reports",
        ):
            saved = 0
            for kind, filename, content in (
                (
                    "balance_sheet",
                    "FinGuard_V04_资产负债表.xlsx",
                    st.session_state.v04_hitl_balance_sheet_xlsx,
                ),
                (
                    "income_statement",
                    "FinGuard_V04_利润表.xlsx",
                    st.session_state.v04_hitl_income_statement_xlsx,
                ),
            ):
                if _archive_workspace_file(
                    kind=kind,
                    filename=filename,
                    content=content,
                    content_type=(
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    ),
                ):
                    saved += 1
            _log_workspace_event(
                action="hitl_reports.archived",
                entity_type="audit_case",
                entity_id=active_case["id"],
                case_id=active_case["id"],
                details={"artifact_count": saved},
            )
            st.success(f"复核后财务报表已归档，共处理 {saved} 个附件。")


def _render_biz_page() -> None:
    _render_subpage_header(
        "制造业业财转换引擎",
        "MANUFACTURING FINANCE",
        "back_from_biz",
    )
    st.caption("将制造业业务描述转换为标准借贷分录，并同步生成轻量财务报表。")
    industry = st.selectbox("行业", ["制造业"], key="biz_industry")
    uploaded = st.file_uploader(
        "上传业务文件",
        type=["csv", "xlsx"],
        key="biz_file_uploader",
        help="系统会识别“业务描述、摘要、说明”等列，并自动填充到下方输入框。",
    )
    if uploaded is not None:
        payload = uploaded.getvalue()
        sheet_name: str | None = None
        if Path(uploaded.name).suffix.casefold() == ".xlsx":
            try:
                sheets = _excel_sheet_names(payload)
            except Exception as exc:
                st.error(f"Excel 工作表读取失败：{exc}")
                sheets = []
            if len(sheets) > 1:
                sheet_name = st.selectbox(
                    "选择工作表",
                    sheets,
                    key="biz_upload_sheet",
                )
            elif sheets:
                sheet_name = sheets[0]

        signature = hashlib.sha256(
            f"{uploaded.name}|{sheet_name or ''}|".encode() + payload
        ).hexdigest()
        if signature != st.session_state.get("biz_upload_signature"):
            try:
                imported_text, description_column, row_count = _business_file_to_text(
                    payload,
                    filename=uploaded.name,
                    sheet_name=sheet_name,
                )
            except Exception as exc:
                st.error(f"业务文件解析失败：{exc}")
            else:
                st.session_state.biz_business_text = imported_text
                st.session_state.biz_upload_signature = signature
                st.session_state.biz_upload_notice = (
                    f"已从 {uploaded.name} 的“{description_column}”列提取 "
                    f"{row_count:,} 行业务，可继续手动修改。"
                )
        if st.session_state.get("biz_upload_notice"):
            st.caption(st.session_state.biz_upload_notice)

    business_text = st.text_area(
        "业务描述（每行一行业务）",
        height=170,
        key="biz_business_text",
        placeholder=("采购钢材10吨，金额100000元，银行付款\n销售产品一批，金额50000元，收到银行款"),
    )
    generate_clicked = st.button(
        "生成标准会计分录",
        type="primary",
        width="stretch",
    )
    if generate_clicked:
        if not business_text.strip():
            st.error("请输入至少一行业务描述。")
        else:
            api_key = _deepseek_key()
            model = _secret("DEEPSEEK_MODEL", "deepseek-chat")
            user = _current_user()
            governance = _ai_governance_service()
            redacted_input = governance.redact(business_text)
            ai_decision = governance.decide(user.organization_id) if api_key else None
            if ai_decision is not None and not ai_decision.allowed:
                st.warning(ai_decision.message)
                api_key = ""
            with st.spinner("正在按制造业口径生成标准分录…"):
                result = convert_business(
                    business_text,
                    industry=industry,
                    api_key=api_key,
                    model=model,
                    prior_history=st.session_state.get("biz_history", []),
                    ai_text_redactor=lambda text: governance.redact(text).text,
                )
            if result.used_ai:
                governance.record(
                    organization_id=user.organization_id,
                    user_id=user.user_id,
                    module="manufacturing_conversion",
                    model=model,
                    input_text=redacted_input.text,
                    redaction_count=redacted_input.replacement_count,
                    status="success",
                )
            new_records = add_history_records(result, industry=industry)
            st.session_state.biz_history.extend(new_records)
            st.session_state.ppt_bytes = None
            st.session_state.ppt_filename = None
            st.session_state.ppt_fallback_reason = ""
            if result.used_ai:
                st.success("DeepSeek 与本地准则校验已完成制造业分录映射。")
            if result.fallback_reason:
                st.info(result.fallback_reason)
            active_case = _active_workspace_case()
            if active_case is not None and new_records:
                artifact = _archive_workspace_file(
                    kind="business_entries",
                    filename=f"FinGuard_业财分录_{pd.Timestamp.now():%Y%m%d_%H%M%S}.json",
                    content=json.dumps(
                        new_records,
                        ensure_ascii=False,
                        indent=2,
                    ).encode("utf-8"),
                    content_type="application/json",
                )
                if artifact is not None:
                    _log_workspace_event(
                        action="business.generated",
                        entity_type="artifact",
                        entity_id=artifact["id"],
                        case_id=active_case["id"],
                        details={"record_count": len(new_records)},
                    )

    history = st.session_state.get("biz_history", [])
    if not history:
        st.markdown(
            '<div class="fg-data-state">尚无转换记录。输入业务后将保存在当前会话中。</div>',
            unsafe_allow_html=True,
        )
        return

    if any(record.get("petty_cash_event") for record in history):
        st.markdown(
            f"""
            <div class="fg-card" style="min-height:auto;margin-bottom:.8rem">
              <div class="fg-card-label">备用金可用余额</div>
              <div class="fg-card-value" style="font-size:1.35rem">
                ¥{petty_cash_balance(history):,.2f}
              </div>
              <div style="color:#6B6F73;font-size:.82rem;margin-top:.25rem">
                提取备用金自动增加，下午茶等支用自动扣减；余额不足时仅要求人工复核，不进入正式报表。
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("### 标准分录")
    grouped_history: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for record in history:
        group_key = (
            record.get("created_at", ""),
            record.get("entry_group", record.get("business", "")),
            record.get("business", ""),
            record.get("status", ""),
        )
        grouped_history.setdefault(group_key, []).append(record)

    for group in reversed(list(grouped_history.values())):
        first_record = group[0]
        if first_record.get("status") == "rejected":
            st.error(
                f"{first_record.get('business', '')}："
                f"{first_record.get('description', '非制造业业务，已拒绝')}"
            )
            continue
        if first_record.get("status") == "review_required":
            st.warning(first_record.get("review_reason", "该业务需要人工复核"))

        rows = "".join(
            f"""
            <tr>
              <td>{html.escape(str(record.get("debit", "") or "—"))}</td>
              <td>{html.escape(str(record.get("credit", "") or "—"))}</td>
              <td style="text-align:right;white-space:nowrap">
                ¥{float(record.get("amount", 0)):,.2f}
              </td>
              <td>{html.escape(str(record.get("description", "")))}</td>
            </tr>
            """
            for record in group
        )
        st.markdown(
            f"""
            <div class="fg-card" style="margin-bottom:.7rem;min-height:auto">
              <div class="fg-card-label">{html.escape(str(first_record.get("industry", "")))} ·
              {html.escape(str(first_record.get("created_at", "")))} ·
              {len(group)} 笔分录</div>
              <div class="fg-card-value" style="font-size:1rem;font-weight:500">
                {html.escape(str(first_record.get("business", "")))}
              </div>
              <div style="overflow-x:auto;margin-top:.8rem">
                <table style="width:100%;border-collapse:collapse;color:#4F5660;font-size:.86rem">
                  <thead>
                    <tr style="text-align:left;color:#8A8F96;font-weight:500">
                      <th style="padding:.45rem .5rem;border-bottom:1px solid #E8E5DF">借</th>
                      <th style="padding:.45rem .5rem;border-bottom:1px solid #E8E5DF">贷</th>
                      <th style="padding:.45rem .5rem;border-bottom:1px solid #E8E5DF;text-align:right">金额</th>
                      <th style="padding:.45rem .5rem;border-bottom:1px solid #E8E5DF">说明</th>
                    </tr>
                  </thead>
                  <tbody>{rows}</tbody>
                </table>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    _render_v04_hitl(history)


def _load_query_dataset() -> None:
    dataset = st.query_params.get("load", "")
    dataset_files = {
        "dirty_100": "dirty_vouchers_100.csv",
        "real_sap": "Real_Data_Clean.csv",
    }
    if dataset not in dataset_files:
        return
    if st.session_state.get("query_dataset_loaded") == dataset:
        return
    project_root = Path(__file__).resolve().parent
    sample_path = (
        project_root / "sample_data" / dataset_files["dirty_100"]
        if dataset == "dirty_100"
        else project_root / dataset_files[dataset]
    )
    if not sample_path.exists():
        st.session_state.analysis_error = f"内置数据文件不存在：{sample_path.name}"
        return
    if _store_analysis(sample_path.read_bytes(), sample_path.name, None):
        st.session_state.current_page = "finance"
    st.session_state.query_dataset_loaded = dataset


_initialize_state()
_render_pending_cookie_change()
try:
    st.session_state.daily_backup = _ensure_daily_backup(pd.Timestamp.now().strftime("%Y%m%d"))
except Exception as exc:
    st.session_state.daily_backup_error = f"自动备份失败：{type(exc).__name__}"
identity_service = _identity_service(str(WORKSPACE_DB_PATH))
auth_required = _auth_required()
current_user: AuthContext | None = None
if auth_required:
    _restore_auth_session(identity_service)
    auth_user_id = str(st.session_state.get("auth_user_id", "") or "")
    if auth_user_id:
        resolved_user = _current_user()
        current_user = resolved_user if resolved_user.user_id else None
        if current_user is None:
            st.session_state.auth_user_id = None
else:
    current_user = identity_service.local_admin()

if auth_required and current_user is None:
    render_auth_page(
        identity_service,
        _account_security_service(),
        on_authenticated=_establish_auth_session,
    )
else:
    assert current_user is not None
    if st.session_state.get("session_user_id") != current_user.user_id:
        _reset_user_scoped_state()
        st.session_state.session_user_id = current_user.user_id

    _load_query_dataset()
    current_page = st.session_state.current_page
    if not _has_page_permission(current_user, current_page):
        st.session_state.current_page = "home"
        st.session_state.auth_access_error = "当前角色无权访问该功能。"
        current_page = "home"

    if current_page == "home":
        _render_home()
    elif current_page == "simulator":
        _render_simulator_page()
    elif current_page == "mlrisk":
        _render_ml_risk_page()
    elif current_page == "capacity":
        _render_capacity_page()
    elif current_page == "settings":
        _render_settings_page()
    elif current_page == "workspace":
        _render_workspace_page()
    elif current_page == "account":
        _render_account_page()
    elif current_page == "finance":
        _render_finance_page()
    elif current_page == "biz":
        _render_biz_page()
    elif current_page == "ppt":
        _render_ppt_page()
    else:
        st.session_state.current_page = "home"
        _render_home()

    if current_page != "home":
        UI.footer()
