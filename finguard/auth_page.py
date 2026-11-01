"""Warm, cinematic enterprise login and first-run setup page."""

from __future__ import annotations

import base64
import html
import time
from pathlib import Path

import streamlit as st

from finguard.account_security import AccountSecurityError, AccountSecurityService
from finguard.identity import AuthenticationError, IdentityService

AUTH_ASSET_DIR = Path(__file__).resolve().parents[1] / "assets"


def _image_data_uri(path: Path, mime_type: str) -> str:
    if not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _inject_auth_styles() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            overflow-x: hidden;
            background:
                radial-gradient(circle at 9% 10%, rgba(176,140,90,.13), transparent 26rem),
                radial-gradient(circle at 92% 84%, rgba(18,22,27,.055), transparent 30rem),
                linear-gradient(135deg, #F7F5F0 0%, #FCFBF8 52%, #F1EDE6 100%);
        }
        .stApp::before {
            content: "";
            position: fixed;
            inset: 0;
            pointer-events: none;
            opacity: .35;
            background-image:
                linear-gradient(rgba(18,22,27,.025) 1px, transparent 1px),
                linear-gradient(90deg, rgba(18,22,27,.025) 1px, transparent 1px);
            background-size: 54px 54px;
            mask-image: linear-gradient(90deg, #000, transparent 72%);
            animation: fg-auth-grid-drift 18s linear infinite;
        }
        [data-testid="stHeader"] { background: transparent; }
        .block-container {
            max-width: 1380px;
            padding-top: clamp(2rem, 7vh, 5.2rem);
            padding-bottom: 3rem;
        }
        .fg-auth-brand {
            position: relative;
            min-height: 610px;
            padding: 1rem 2.4rem 1rem 0;
            animation: fg-auth-rise .75s cubic-bezier(.2,.75,.25,1) both;
        }
        .fg-auth-rule {
            width: 70px;
            height: 2px;
            background: linear-gradient(90deg, #B08C5A, rgba(176,140,90,.12));
            margin-bottom: 1.7rem;
        }
        .fg-auth-kicker {
            color: #94764D;
            font-size: .7rem;
            font-weight: 680;
            letter-spacing: .22em;
            text-transform: uppercase;
        }
        .fg-auth-brand h1 {
            position: relative;
            z-index: 2;
            margin: 1rem 0 1.1rem;
            color: #12161B;
            font-family: "Noto Serif SC", "Songti SC", serif;
            font-size: clamp(3.3rem, 6.2vw, 6.8rem);
            font-weight: 320;
            letter-spacing: -.07em;
            line-height: 1.01;
        }
        .fg-auth-brand h1 span {
            color: #9D8056;
            font-style: normal;
        }
        .fg-auth-brand p {
            max-width: 36rem;
            margin: 2.15rem 0 0;
            color: #5F625F;
            font-size: 1rem;
            line-height: 1.9;
        }
        .fg-auth-points {
            display: flex;
            flex-wrap: wrap;
            gap: .55rem;
            margin-top: 2rem;
        }
        .fg-auth-points span {
            border: 1px solid rgba(155,124,80,.24);
            border-radius: 999px;
            background: rgba(255,255,255,.48);
            color: #655E54;
            font-size: .73rem;
            padding: .44rem .75rem;
            backdrop-filter: blur(8px);
        }
        .fg-auth-gavel-stage {
            position: relative;
            z-index: 1;
            width: min(100%, 670px);
            height: 255px;
            margin: -.9rem 0 .2rem;
            pointer-events: none;
        }
        .fg-auth-gavel {
            position: absolute;
            left: 50%;
            top: 0;
            width: min(96%, 650px);
            max-height: 250px;
            object-fit: contain;
            opacity: .52;
            transform: translateX(-50%);
            filter:
                grayscale(8%)
                contrast(1.02)
                drop-shadow(0 24px 30px rgba(53,35,19,.13));
            animation: fg-auth-gavel-float 6s ease-in-out infinite;
            will-change: transform;
        }
        .fg-auth-lens {
            position: absolute;
            z-index: 3;
            right: 7%;
            bottom: 4%;
            width: 92px;
            height: 92px;
            border: 2px solid rgba(176,140,90,.62);
            border-radius: 50%;
            background: rgba(255,255,255,.12);
            box-shadow:
                inset 0 0 18px rgba(255,255,255,.5),
                0 12px 30px rgba(70,51,27,.12);
            backdrop-filter: blur(2px);
            animation: fg-auth-lens-breathe 5s ease-in-out infinite;
        }
        .fg-auth-lens::after {
            content: "";
            position: absolute;
            width: 58px;
            height: 7px;
            border-radius: 999px;
            background: #7B5D38;
            right: -49px;
            bottom: -21px;
            transform: rotate(47deg);
            box-shadow: 0 5px 12px rgba(53,35,19,.18);
        }
        .st-key-auth_card {
            width: min(100%, 690px);
            min-height: 555px;
            margin-left: auto;
            border: 1px solid rgba(255,255,255,.72);
            border-radius: 30px;
            background:
                linear-gradient(145deg, rgba(255,255,255,.72), rgba(255,255,255,.48));
            box-shadow:
                inset 0 1px 0 rgba(255,255,255,.86),
                0 38px 100px rgba(49,39,26,.14);
            padding: 2.6rem 2.55rem 2rem;
            backdrop-filter: blur(28px) saturate(1.16);
            -webkit-backdrop-filter: blur(28px) saturate(1.16);
            animation: fg-auth-card-in .8s .08s cubic-bezier(.2,.75,.25,1) both;
        }
        .fg-auth-card-head { margin-bottom: 1.45rem; }
        .fg-auth-card-head h2 {
            margin: 0 0 .55rem;
            color: #171A1E;
            font-size: 1.65rem;
            font-weight: 540;
            letter-spacing: -.03em;
        }
        .fg-auth-card-head p {
            margin: 0;
            color: #777A78;
            font-size: .82rem;
            line-height: 1.65;
        }
        .st-key-auth_card label p {
            color: #505551 !important;
            font-size: .78rem !important;
            font-weight: 540 !important;
        }
        .st-key-auth_card input,
        .st-key-auth_card [data-baseweb="select"] > div {
            min-height: 2.8rem;
            border-color: #DDD7CD !important;
            border-radius: 10px !important;
            background: rgba(250,249,246,.9) !important;
            color: #171A1E !important;
        }
        .st-key-auth_card input:focus {
            border-color: #B08C5A !important;
            box-shadow: 0 0 0 1px rgba(176,140,90,.22) !important;
        }
        .st-key-auth_primary button[kind="primary"] {
            min-height: 3.15rem;
            margin-top: .65rem;
            border: 1px solid #12161B !important;
            border-radius: 11px !important;
            background: #12161B !important;
            color: #F7F5F0 !important;
            font-weight: 560 !important;
            letter-spacing: .035em;
            transition:
                transform .2s ease,
                box-shadow .2s ease,
                background .2s ease !important;
        }
        .st-key-auth_primary button[kind="primary"]:hover {
            border-color: #2A2E33 !important;
            background: #2A2E33 !important;
            transform: translateY(-2px);
            box-shadow: 0 15px 34px rgba(18,22,27,.2);
        }
        .fg-auth-foot {
            margin-top: 1rem;
            color: #96958F;
            font-size: .69rem;
            line-height: 1.6;
        }
        .fg-auth-signature {
            margin-top: 2.8rem;
            color: rgba(100,92,80,.46);
            font-size: .64rem;
            font-weight: 620;
            letter-spacing: .19em;
            text-transform: uppercase;
        }
        @keyframes fg-auth-rise {
            from { opacity: 0; transform: translateY(22px); }
            to { opacity: 1; transform: translateY(0); }
        }
        @keyframes fg-auth-card-in {
            from { opacity: 0; transform: translateY(28px) scale(.985); }
            to { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes fg-auth-gavel-float {
            0%, 100% { transform: translate3d(-50%, 0, 0) rotate(-1deg); }
            50% { transform: translate3d(-50%, -12px, 0) rotate(.7deg); }
        }
        @keyframes fg-auth-lens-breathe {
            0%, 100% { transform: translate3d(0, 0, 0) scale(1); opacity: .62; }
            50% { transform: translate3d(-4px, -5px, 0) scale(1.03); opacity: .82; }
        }
        @keyframes fg-auth-grid-drift {
            from { transform: translate3d(0, 0, 0); }
            to { transform: translate3d(54px, 54px, 0); }
        }
        @media (max-width: 900px) {
            .block-container { padding-top: 1.6rem; }
            .fg-auth-brand { min-height: auto; padding: .5rem 0 1rem; }
            .fg-auth-brand h1 { font-size: 3rem; }
            .fg-auth-gavel-stage {
                width: 100%;
                height: 190px;
                margin-top: 1rem;
            }
            .fg-auth-gavel { width: 88%; left: 50%; }
            .fg-auth-lens { width: 65px; height: 65px; left: 10%; bottom: 18%; }
            .st-key-auth_card {
                width: 100%;
                margin-top: .5rem;
                padding: 1.4rem 1.15rem 1rem;
                border-radius: 18px;
            }
        }
        @media (prefers-reduced-motion: reduce) {
            .fg-auth-gavel,
            .fg-auth-lens,
            .fg-auth-brand,
            .st-key-auth_card,
            .stApp::before {
                animation: none !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_card_header(title: str, description: str) -> None:
    st.markdown(
        f"""
        <div class="fg-auth-card-head">
          <h2>{html.escape(title)}</h2>
          <p>{html.escape(description)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_login_form(
    identity: IdentityService,
    organizations: list[dict],
) -> None:
    _render_card_header(
        "登录审计工作台",
        "使用所属企业空间的管理员或成员账号登录。",
    )
    organization_labels = {
        organization["name"]: organization["id"] for organization in organizations
    }
    organization_name = st.selectbox("企业空间", list(organization_labels))
    username = st.text_input("用户名", placeholder="输入登录用户名")
    password = st.text_input("密码", type="password", placeholder="输入登录密码")
    login_clicked = st.button(
        "登录",
        type="primary",
        width="stretch",
        key="auth_primary",
    )
    if login_clicked:
        context = identity.authenticate(
            organization_id=organization_labels[organization_name],
            username=username,
            password=password,
        )
        if context is None:
            st.error("企业空间、用户名或密码不正确")
        else:
            st.session_state.auth_user_id = context.user_id
            st.session_state.auth_authenticated_at = time.time()
            st.session_state.auth_last_seen_at = time.time()
            st.session_state.auth_notice = f"欢迎，{context.display_name}。"
            st.rerun()
    if st.button("忘记密码", width="stretch", key="auth_forgot_password"):
        st.session_state.auth_mode = "forgot"
        st.rerun()


def _render_forgot_password(
    identity: IdentityService,
    security: AccountSecurityService,
    organizations: list[dict],
) -> None:
    _render_card_header(
        "找回密码",
        "验证码只会发送到账号已经绑定并验证的邮箱。",
    )
    organization_labels = {
        organization["name"]: organization["id"] for organization in organizations
    }
    organization_name = st.selectbox(
        "企业空间",
        list(organization_labels),
        key="forgot_organization",
    )
    username = st.text_input(
        "用户名",
        placeholder="输入需要找回的用户名",
        key="forgot_username",
    )
    if st.button(
        "发送重置验证码",
        width="stretch",
        key="forgot_send_code",
    ):
        try:
            result = security.request_password_reset(
                organization_id=organization_labels[organization_name],
                username=username,
            )
        except AccountSecurityError as exc:
            st.error(str(exc))
        else:
            st.session_state.forgot_notice = result.message
            st.session_state.forgot_dev_code = result.dev_code
            if not result.success:
                st.error(result.message)

    notice = st.session_state.pop("forgot_notice", "")
    if notice:
        st.info(notice)
    dev_code = st.session_state.pop("forgot_dev_code", "")
    if dev_code:
        st.warning(f"开发测试验证码：{dev_code}。正式环境不会显示此内容。")

    code = st.text_input(
        "邮箱验证码",
        placeholder="6 位验证码",
        max_chars=6,
        key="forgot_code",
    )
    new_password = st.text_input(
        "新密码",
        type="password",
        placeholder="至少 8 位",
        key="forgot_new_password",
    )
    confirm_password = st.text_input(
        "确认新密码",
        type="password",
        placeholder="再次输入新密码",
        key="forgot_confirm_password",
    )
    if st.button(
        "重置密码",
        type="primary",
        width="stretch",
        key="forgot_reset_password",
    ):
        if new_password != confirm_password:
            st.error("两次输入的新密码不一致")
        else:
            try:
                security.reset_password(
                    organization_id=organization_labels[organization_name],
                    username=username,
                    code=code,
                    new_password=new_password,
                )
            except (AccountSecurityError, AuthenticationError) as exc:
                st.error(str(exc))
            else:
                st.session_state.auth_mode = "login"
                st.session_state.auth_notice = "密码已重置，请使用新密码登录。"
                st.rerun()
    if st.button("返回登录", width="stretch", key="forgot_back_login"):
        st.session_state.auth_mode = "login"
        st.rerun()


def render_auth_page(
    identity: IdentityService,
    security: AccountSecurityService,
) -> None:
    _inject_auth_styles()
    organizations = identity.list_organizations()
    gavel_uri = _image_data_uri(AUTH_ASSET_DIR / "gavel-cutout.png", "image/png")
    brand_column, form_column = st.columns(
        [1.0, 1.0],
        gap="large",
        vertical_alignment="center",
    )

    with brand_column:
        gavel_markup = (
            f'<img class="fg-auth-gavel" src="{gavel_uri}" alt="审计法槌">' if gavel_uri else ""
        )
        st.markdown(
            f"""
            <div class="fg-auth-brand">
              <div class="fg-auth-gavel-stage">
                {gavel_markup}
                <div class="fg-auth-lens"></div>
              </div>
              <div class="fg-auth-rule"></div>
              <div class="fg-auth-kicker">FINGUARD · TRUSTED FINANCE</div>
              <h1>看清风险。<br><span>守住边界。</span></h1>
              <p>
                企业级财务异常审计与证据协作工作台。以可信身份进入，
                在独立企业空间中完成审计、复核、归档与报告交付。
              </p>
              <div class="fg-auth-points">
                <span>企业空间隔离</span>
                <span>角色权限控制</span>
                <span>证据链留痕</span>
              </div>
              <div class="fg-auth-signature">AUDIT · CONTROL · EVIDENCE</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with form_column:
        with st.container(key="auth_card"):
            if not organizations:
                _render_card_header(
                    "首次初始化",
                    "创建第一个企业空间与管理员。密码仅保存为 PBKDF2 哈希。",
                )
                name_columns = st.columns(2)
                with name_columns[0]:
                    organization_name = st.text_input(
                        "企业名称",
                        placeholder="例如：远山制造有限公司",
                    )
                with name_columns[1]:
                    display_name = st.text_input(
                        "管理员姓名",
                        placeholder="例如：李经理",
                    )
                username = st.text_input(
                    "登录用户名",
                    placeholder="英文字母或数字，不含空格",
                )
                password_columns = st.columns(2)
                with password_columns[0]:
                    password = st.text_input(
                        "登录密码",
                        type="password",
                        placeholder="至少 8 位",
                    )
                with password_columns[1]:
                    confirm_password = st.text_input(
                        "确认密码",
                        type="password",
                        placeholder="再次输入",
                    )
                initialize_clicked = st.button(
                    "创建企业空间",
                    type="primary",
                    width="stretch",
                    key="auth_primary",
                )
                if initialize_clicked:
                    if password != confirm_password:
                        st.error("两次输入的密码不一致")
                    else:
                        try:
                            context = identity.create_initial_admin(
                                organization_name=organization_name,
                                username=username,
                                display_name=display_name,
                                password=password,
                            )
                        except (AuthenticationError, ValueError) as exc:
                            st.error(str(exc))
                        else:
                            st.session_state.auth_user_id = context.user_id
                            st.session_state.auth_authenticated_at = time.time()
                            st.session_state.auth_last_seen_at = time.time()
                            st.session_state.auth_notice = "企业空间已创建。"
                            st.rerun()
            else:
                if st.session_state.get("auth_mode") == "forgot":
                    _render_forgot_password(identity, security, organizations)
                else:
                    _render_login_form(identity, organizations)

            st.markdown(
                '<div class="fg-auth-foot">仅授权人员可访问。登录失败不会透露账号是否存在。</div>',
                unsafe_allow_html=True,
            )
