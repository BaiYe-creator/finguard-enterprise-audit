"""Audit project, evidence, and audit-trail page."""

from __future__ import annotations

import json
from collections.abc import Callable

import pandas as pd
import streamlit as st

from finguard.ai_governance import AIGovernanceService
from finguard.backup_manager import BackupManager
from finguard.config import RiskConfig, risk_config_from_profile, risk_config_profile
from finguard.identity import (
    ASSIGNABLE_ROLES,
    PERM_EVIDENCE_WRITE,
    PERM_TEAM_MANAGE,
    ROLE_LABELS,
    AuthContext,
    AuthenticationError,
    IdentityService,
)
from finguard.postgres_store import PostgresWorkspaceStore
from finguard.workspace_store import (
    CASE_STATUS_LABELS,
    CASE_STATUS_ORDER,
    WorkspaceStore,
    WorkspaceStoreError,
)

PageHeader = Callable[[str, str, str], None]


def _format_size(size_bytes: int) -> str:
    size = float(size_bytes)
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):,.2f} MB"
    if size >= 1024:
        return f"{size / 1024:,.1f} KB"
    return f"{int(size):,} B"


def _case_label(case: dict) -> str:
    status = CASE_STATUS_LABELS.get(str(case.get("status", "")), str(case.get("status", "")))
    return f"{case.get('title', '')} · {case.get('period', '')} · {status}"


def _set_active_case(case_id: str) -> None:
    st.session_state.active_case_id = case_id


def render_workspace_page(
    render_header: PageHeader,
    *,
    store: WorkspaceStore,
    identity: IdentityService,
    user: AuthContext,
    ai_governance: AIGovernanceService,
    backup_manager: BackupManager,
) -> None:
    render_header("审计项目与证据链", "AUDIT WORKSPACE", "back_from_workspace")
    st.caption(
        "项目、附件和操作记录保存在本地 SQLite 数据库中。"
        f"当前身份：{user.display_name}（{user.role_label}） · 企业空间：{user.organization_name}"
    )

    cases = store.list_cases(user.organization_id)
    active_case_id = st.session_state.get("active_case_id")
    if cases and not any(case["id"] == active_case_id for case in cases):
        active_case_id = cases[0]["id"]
        st.session_state.active_case_id = active_case_id

    summary_columns = st.columns(3)
    with summary_columns[0]:
        st.metric("审计项目", len(cases))
    with summary_columns[1]:
        active_case = (
            store.get_case(active_case_id, user.organization_id) if active_case_id else None
        )
        st.metric("当前项目附件", int(active_case["artifact_count"]) if active_case else 0)
    with summary_columns[2]:
        st.metric(
            "操作日志",
            len(
                store.list_logs(
                    case_id=active_case_id,
                    organization_id=user.organization_id,
                    limit=500,
                )
            ),
        )

    overview_tab, evidence_tab, team_tab, governance_tab, log_tab = st.tabs(
        ["项目概览", "证据资料", "企业成员", "数据治理", "操作日志"]
    )

    with overview_tab:
        if cases:
            options = [case["id"] for case in cases]
            selected = st.selectbox(
                "当前审计项目",
                options,
                index=options.index(active_case_id),
                format_func=lambda case_id: _case_label(
                    next(case for case in cases if case["id"] == case_id)
                ),
                key="workspace_case_selector",
            )
            if selected != st.session_state.get("active_case_id"):
                _set_active_case(selected)
                store.log_event(
                    action="case.selected",
                    entity_type="audit_case",
                    entity_id=selected,
                    case_id=selected,
                    organization_id=user.organization_id,
                    user_id=user.user_id,
                )
                st.rerun()

        title = st.text_input(
            "项目名称",
            placeholder="例如：2026 年费用报销专项审计",
            key="workspace_new_case_title",
        )
        period = st.text_input(
            "审计期间",
            value="2026-10",
            key="workspace_new_case_period",
        )
        description = st.text_area(
            "项目说明",
            height=90,
            key="workspace_new_case_description",
        )
        create_clicked = st.button(
            "创建审计项目",
            type="primary",
            width="stretch",
        )
        if create_clicked:
            try:
                case = store.create_case(
                    title=title,
                    period=period,
                    description=description,
                    organization_id=user.organization_id,
                    owner_id=user.user_id,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state.active_case_id = case["id"]
                st.session_state.workspace_notice = "审计项目已创建。"
                st.rerun()

        active_case = (
            store.get_case(st.session_state.get("active_case_id"), user.organization_id)
            if cases
            else None
        )
        if active_case:
            st.markdown("### 当前项目")
            details = {
                "项目编号": active_case["id"],
                "项目名称": active_case["title"],
                "审计期间": active_case["period"],
                "状态": CASE_STATUS_LABELS.get(active_case["status"], active_case["status"]),
                "负责人": active_case["owner_name"],
                "创建时间": active_case["created_at"],
                "更新时间": active_case["updated_at"],
                "项目说明": active_case["description"] or "未填写",
            }
            st.dataframe(
                pd.DataFrame([{"字段": key, "内容": value} for key, value in details.items()]),
                width="stretch",
                hide_index=True,
            )

            with st.form("workspace_update_case"):
                status_index = CASE_STATUS_ORDER.index(active_case["status"])
                status = st.selectbox(
                    "项目状态",
                    CASE_STATUS_ORDER,
                    index=status_index,
                    format_func=lambda value: CASE_STATUS_LABELS[value],
                )
                note = st.text_area(
                    "补充说明",
                    value=active_case["description"],
                    height=90,
                )
                update_clicked = st.form_submit_button(
                    "保存项目状态",
                    width="stretch",
                )
            if update_clicked:
                try:
                    store.update_case(
                        active_case["id"],
                        status=status,
                        description=note,
                        organization_id=user.organization_id,
                        user_id=user.user_id,
                    )
                except (ValueError, WorkspaceStoreError) as exc:
                    st.error(str(exc))
                else:
                    st.session_state.workspace_notice = "项目状态与说明已更新。"
                    st.rerun()

        else:
            st.info("尚未创建审计项目。创建后，上传的凭证、报告和证据可以归档到这里。")

    with governance_tab:
        if user.role not in {"admin", "local_admin"}:
            st.info("数据治理仅对企业管理员开放。")
        else:
            backup_tab, rules_tab, ai_tab, integrity_tab = st.tabs(
                ["备份恢复", "规则版本", "AI 台账", "日志校验"]
            )
            with backup_tab:
                integrity = store.database_integrity()
                st.markdown("### 数据库备份")
                st.caption(
                    "恢复备份前会自动创建一份当前数据库快照。"
                    "上传文件只接受通过 SQLite 完整性检查的数据库。"
                )
                metrics = st.columns(3)
                metrics[0].metric(
                    "数据库状态",
                    "正常" if integrity["valid"] else "异常",
                )
                metrics[1].metric("审计项目", integrity["counts"]["audit_cases"])
                metrics[2].metric("证据附件", integrity["counts"]["case_artifacts"])
                try:
                    backup_bytes = store.create_backup_bytes()
                except Exception as exc:
                    backup_bytes = b""
                    st.warning(f"当前数据库备份：{exc}")
                if backup_bytes:
                    is_postgres = isinstance(store, PostgresWorkspaceStore)
                    st.download_button(
                        ("下载 PostgreSQL 备份" if is_postgres else "下载当前数据库备份"),
                        data=backup_bytes,
                        file_name=(
                            f"FinGuard_backup_{pd.Timestamp.now():%Y%m%d_%H%M%S}."
                            f"{'dump' if is_postgres else 'db'}"
                        ),
                        mime=(
                            "application/octet-stream" if is_postgres else "application/vnd.sqlite3"
                        ),
                        width="stretch",
                    )
                automatic_backups = backup_manager.list_backups()
                if automatic_backups:
                    st.markdown("**本地自动备份**")
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "文件": item.filename,
                                    "时间": item.created_at,
                                    "大小": _format_size(item.size_bytes),
                                    "SHA-256": item.sha256[:16],
                                    "校验": "正常" if item.valid else "异常",
                                }
                                for item in automatic_backups
                            ]
                        ),
                        width="stretch",
                        hide_index=True,
                    )
                uploaded_backup = st.file_uploader(
                    "恢复数据库备份",
                    type=["db", "sqlite", "sqlite3"],
                    key="governance_restore_upload",
                )
                confirm_restore = st.checkbox(
                    "我确认恢复会覆盖当前数据库，并已下载必要备份。",
                    key="governance_restore_confirm",
                )
                if st.button(
                    "执行数据库恢复",
                    width="stretch",
                    disabled=not (uploaded_backup and confirm_restore),
                    key="governance_restore_button",
                ):
                    try:
                        snapshot = store.restore_backup_bytes(
                            uploaded_backup.getvalue(),
                            user_id=user.user_id,
                        )
                    except Exception as exc:
                        st.error(f"恢复失败：{exc}")
                    else:
                        st.session_state.workspace_notice = (
                            f"数据库已恢复，恢复前快照：{snapshot.name}"
                        )
                        st.rerun()

            with rules_tab:
                st.markdown("### 风险规则版本")
                profiles = store.list_rule_profiles(user.organization_id)
                active_profile = store.get_active_rule_profile(user.organization_id)
                if active_profile:
                    try:
                        active_config = risk_config_from_profile(
                            json.loads(active_profile["config_json"])
                        )
                    except Exception:
                        active_config = RiskConfig()
                    st.success(f"当前规则：{active_profile['name']} · V{active_profile['version']}")
                else:
                    active_config = RiskConfig()
                    st.info("尚未建立企业规则版本，当前使用系统默认值。")

                name = st.text_input(
                    "规则版本名称",
                    value=f"企业规则 V{len(profiles) + 1}",
                    key="governance_rule_name",
                )
                rule_columns = st.columns(3)
                with rule_columns[0]:
                    rule_points = st.number_input(
                        "硬规则分值",
                        min_value=1.0,
                        max_value=100.0,
                        value=float(active_config.rule_points),
                    )
                with rule_columns[1]:
                    statistical_points = st.number_input(
                        "统计层分值",
                        min_value=1.0,
                        max_value=100.0,
                        value=float(active_config.statistical_points),
                    )
                with rule_columns[2]:
                    cross_points = st.number_input(
                        "交叉层分值",
                        min_value=0.0,
                        max_value=100.0,
                        value=float(active_config.cross_validation_points),
                    )
                threshold_columns = st.columns(3)
                with threshold_columns[0]:
                    medium_threshold = st.number_input(
                        "中风险阈值",
                        min_value=0.0,
                        max_value=99.0,
                        value=float(active_config.medium_risk_threshold),
                    )
                with threshold_columns[1]:
                    high_threshold = st.number_input(
                        "高风险阈值",
                        min_value=1.0,
                        max_value=100.0,
                        value=float(active_config.high_risk_threshold),
                    )
                with threshold_columns[2]:
                    ai_threshold = st.number_input(
                        "AI 研判阈值",
                        min_value=1.0,
                        max_value=100.0,
                        value=float(active_config.ai_trigger_threshold),
                    )
                if st.button(
                    "创建规则版本",
                    type="primary",
                    width="stretch",
                    key="governance_create_rule",
                ):
                    try:
                        values = risk_config_profile(active_config)
                        values.update(
                            {
                                "rule_points": rule_points,
                                "statistical_points": statistical_points,
                                "cross_validation_points": cross_points,
                                "medium_risk_threshold": medium_threshold,
                                "high_risk_threshold": high_threshold,
                                "ai_trigger_threshold": ai_threshold,
                            }
                        )
                        validated = risk_config_from_profile(values)
                        store.create_rule_profile(
                            organization_id=user.organization_id,
                            user_id=user.user_id,
                            name=name,
                            config=risk_config_profile(validated),
                        )
                    except Exception as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.workspace_notice = "规则版本已创建。"
                        st.rerun()

                if profiles:
                    labels = {
                        f"V{item['version']} · {item['name']}"
                        + (" · 当前" if item["active"] else ""): item["id"]
                        for item in profiles
                    }
                    selected_rule = st.selectbox(
                        "选择规则版本",
                        list(labels),
                        key="governance_rule_select",
                    )
                    if st.button(
                        "启用所选规则",
                        width="stretch",
                        key="governance_activate_rule",
                    ):
                        store.activate_rule_profile(
                            organization_id=user.organization_id,
                            profile_id=labels[selected_rule],
                            user_id=user.user_id,
                        )
                        st.session_state.workspace_notice = "规则版本已启用。"
                        st.rerun()

            with ai_tab:
                st.markdown("### AI 用量与数据最小化")
                usage = ai_governance.summary(user.organization_id)
                metric_columns = st.columns(4)
                metric_columns[0].metric("调用次数", int(usage.get("calls", 0)))
                metric_columns[1].metric(
                    "估算 Token",
                    f"{int(usage.get('estimated_tokens', 0)):,}",
                )
                metric_columns[2].metric(
                    "脱敏字段",
                    int(usage.get("redactions", 0)),
                )
                metric_columns[3].metric(
                    "估算费用",
                    f"¥{float(usage.get('estimated_cost', 0)):.4f}",
                )
                events = store.list_ai_usage(user.organization_id, limit=100)
                if events:
                    st.dataframe(pd.DataFrame(events), width="stretch", hide_index=True)
                else:
                    st.info("当前企业还没有 AI 调用记录。")

            with integrity_tab:
                st.markdown("### 审计日志完整性")
                chain = store.verify_audit_chain(user.organization_id)
                if chain["valid"]:
                    st.success(
                        f"哈希链校验通过：{chain['checked']} 条；"
                        f"历史旧日志 {chain['legacy']} 条未纳入哈希链。"
                    )
                else:
                    st.error(f"哈希链异常，日志 #{chain['broken_id']}：{chain['reason']}")
                st.caption("哈希链用于发现日志内容修改或中间删除，不能替代外部不可篡改存储。")
    with evidence_tab:
        active_case = store.get_case(
            st.session_state.get("active_case_id"),
            user.organization_id,
        )
        if active_case is None:
            st.warning("请先在“项目概览”中创建并选择审计项目。")
        else:
            uploads = st.file_uploader(
                "上传证据或工作底稿",
                type=["pdf", "png", "jpg", "jpeg", "csv", "xlsx", "xls", "docx", "txt"],
                accept_multiple_files=True,
                key="workspace_evidence_uploader",
                help="单个文件上限 25 MB；相同文件不会重复写入同一项目。",
            )
            can_write_evidence = identity.has_permission(user, PERM_EVIDENCE_WRITE)
            if st.button(
                "保存上传的证据",
                type="primary",
                width="stretch",
                disabled=not bool(uploads) or not can_write_evidence,
            ):
                saved = 0
                for upload in uploads or []:
                    try:
                        store.add_artifact(
                            case_id=active_case["id"],
                            kind="evidence",
                            filename=upload.name,
                            content=upload.getvalue(),
                            content_type=upload.type or "application/octet-stream",
                            user_id=user.user_id,
                        )
                    except (ValueError, WorkspaceStoreError) as exc:
                        st.error(f"{upload.name}：{exc}")
                    else:
                        saved += 1
                if saved:
                    st.session_state.workspace_notice = f"已保存 {saved} 个证据文件。"
                    st.rerun()

            artifacts = store.list_artifacts(active_case["id"])
            if not artifacts:
                st.info("当前项目还没有证据或成果附件。")
            else:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "类型": item["kind"],
                                "文件名": item["filename"],
                                "大小": _format_size(int(item["size_bytes"])),
                                "SHA-256": item["sha256"][:16],
                                "保存时间": item["created_at"],
                            }
                            for item in artifacts
                        ]
                    ),
                    width="stretch",
                    hide_index=True,
                )
                labels = {
                    f"{item['filename']} · {item['sha256'][:8]}": item["id"] for item in artifacts
                }
                selected_label = st.selectbox("选择附件下载", list(labels))
                package = store.get_artifact(labels[selected_label])
                if package is not None:
                    st.download_button(
                        f"下载 {package['filename']}",
                        data=package["content"],
                        file_name=package["filename"],
                        mime=package["content_type"],
                        width="stretch",
                    )

    with team_tab:
        can_manage_team = identity.has_permission(user, PERM_TEAM_MANAGE)
        if not can_manage_team:
            st.info("当前角色仅可查看自己的工作台，企业成员管理由管理员负责。")
        else:
            with st.form("workspace_create_member"):
                member_name = st.text_input("成员姓名")
                member_username = st.text_input("登录用户名")
                member_password = st.text_input("初始密码", type="password")
                member_role = st.selectbox(
                    "成员角色",
                    ASSIGNABLE_ROLES,
                    format_func=lambda role: ROLE_LABELS[role],
                )
                create_member_clicked = st.form_submit_button(
                    "添加企业成员",
                    type="primary",
                    width="stretch",
                )
            if create_member_clicked:
                try:
                    identity.create_member(
                        user,
                        username=member_username,
                        display_name=member_name,
                        password=member_password,
                        role=member_role,
                    )
                except (AuthenticationError, ValueError) as exc:
                    st.error(str(exc))
                else:
                    st.session_state.workspace_notice = "企业成员已创建。"
                    st.rerun()

            members = identity.list_members(user)
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "姓名": member["display_name"],
                            "用户名": member.get("username") or "本地账号",
                            "角色": ROLE_LABELS.get(member["role"], member["role"]),
                            "状态": "启用" if member["active"] else "停用",
                            "创建时间": member["created_at"],
                        }
                        for member in members
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
            editable_members = [member for member in members if member["role"] in ASSIGNABLE_ROLES]
            if editable_members:
                member_labels = {
                    f"{member['display_name']} · {member.get('username') or member['id']}": member
                    for member in editable_members
                }
                selected_member_label = st.selectbox(
                    "选择成员",
                    list(member_labels),
                )
                selected_member = member_labels[selected_member_label]
                role_index = ASSIGNABLE_ROLES.index(selected_member["role"])
                updated_role = st.selectbox(
                    "角色",
                    ASSIGNABLE_ROLES,
                    index=role_index,
                    format_func=lambda role: ROLE_LABELS[role],
                )
                updated_active = st.toggle(
                    "启用账号",
                    value=bool(selected_member["active"]),
                )
                update_member_clicked = st.button(
                    "保存成员设置",
                    width="stretch",
                )
                if update_member_clicked:
                    try:
                        identity.update_member(
                            user,
                            selected_member["id"],
                            role=updated_role,
                            active=updated_active,
                        )
                    except (AuthenticationError, ValueError) as exc:
                        st.error(str(exc))
                    else:
                        st.session_state.workspace_notice = "成员设置已更新。"
                        st.rerun()

    with log_tab:
        active_case_id = st.session_state.get("active_case_id")
        logs = store.list_logs(
            case_id=active_case_id,
            organization_id=user.organization_id,
            limit=200,
        )
        if not logs:
            st.info("当前项目暂无操作日志。")
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "时间": item["created_at"],
                            "操作人": item["user_name"],
                            "动作": item["action"],
                            "对象": item["entity_type"],
                            "对象编号": item["entity_id"],
                            "详情": item["details"],
                        }
                        for item in logs
                    ]
                ),
                width="stretch",
                hide_index=True,
            )

    notice = st.session_state.pop("workspace_notice", "")
    if notice:
        st.success(notice)
