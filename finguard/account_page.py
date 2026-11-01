"""Signed-in account security page."""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from finguard.account_security import AccountSecurityError, AccountSecurityService
from finguard.identity import AuthContext, IdentityService

PageHeader = Callable[[str, str, str], None]


def _logout_after_password_change(notice: str) -> None:
    st.session_state.auth_user_id = None
    st.session_state.auth_authenticated_at = None
    st.session_state.auth_last_seen_at = None
    st.session_state.auth_notice = notice
    st.session_state.current_page = "home"
    st.rerun()


def render_account_page(
    render_header: PageHeader,
    *,
    user: AuthContext,
    identity: IdentityService,
    security: AccountSecurityService,
) -> None:
    render_header("账号安全", "ACCOUNT SECURITY", "back_from_account")
    member = identity.store.get_member(user.user_id) or {}
    email = str(member.get("email", "") or "").strip()
    verified = bool(member.get("email_verified"))

    st.caption("管理登录邮箱和密码。验证码不会写入普通日志，也不会在正式页面明文显示。")
    email_tab, password_tab = st.tabs(["登录邮箱", "修改密码"])

    with email_tab:
        st.markdown("### 登录邮箱")
        bind_notice = st.session_state.pop("security_bind_notice", "")
        if bind_notice:
            st.success(bind_notice)
        if email and verified:
            st.success(f"当前已绑定：{email}")
        else:
            st.info("当前账号尚未绑定验证邮箱，暂时无法使用忘记密码。")
        with st.container(border=True):
            new_email = st.text_input(
                "验证邮箱",
                value=email,
                key="security_bind_email",
                placeholder="name@example.com",
            )
            if st.button(
                "发送验证码",
                type="primary",
                width="stretch",
                key="security_send_email_code",
            ):
                try:
                    result = security.request_email_binding(user, new_email)
                except AccountSecurityError as exc:
                    st.error(str(exc))
                else:
                    if result.success:
                        st.session_state.security_email_notice = result.message
                        st.session_state.security_dev_code = result.dev_code
                    else:
                        st.error(result.message)

            email_notice = st.session_state.pop("security_email_notice", "")
            if email_notice:
                st.success(email_notice)
            dev_code = st.session_state.pop("security_dev_code", "")
            if dev_code:
                st.warning(f"开发测试验证码：{dev_code}。正式环境不会显示此内容。")

            verification_code = st.text_input(
                "邮箱验证码",
                key="security_email_code",
                placeholder="6 位验证码",
                max_chars=6,
            )
            if st.button(
                "验证并绑定邮箱",
                width="stretch",
                key="security_confirm_email",
            ):
                try:
                    bound_email = security.confirm_email_binding(
                        user,
                        verification_code,
                    )
                except AccountSecurityError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.security_bind_notice = f"邮箱已绑定：{bound_email}"
                    st.rerun()

    with password_tab:
        st.markdown("### 修改密码")
        st.caption("修改成功后，当前会话会退出，需要使用新密码重新登录。")
        current_password = st.text_input(
            "当前密码",
            type="password",
            key="security_current_password",
        )
        new_password = st.text_input(
            "新密码",
            type="password",
            key="security_new_password",
        )
        confirm_password = st.text_input(
            "确认新密码",
            type="password",
            key="security_confirm_password",
        )
        if st.button(
            "保存新密码",
            type="primary",
            width="stretch",
            key="security_change_password",
        ):
            if new_password != confirm_password:
                st.error("两次输入的新密码不一致")
            else:
                try:
                    security.change_password(
                        user,
                        current_password=current_password,
                        new_password=new_password,
                    )
                except AccountSecurityError as exc:
                    st.error(str(exc))
                else:
                    _logout_after_password_change("密码已修改，请使用新密码重新登录。")
