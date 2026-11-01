"""Session-only DeepSeek credential configuration page."""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from finguard.ai_auditor import probe_deepseek_connection

PageHeader = Callable[[str, str, str], None]
SecretReader = Callable[[str, str], str]


def render_settings_page(
    render_header: PageHeader,
    *,
    deepseek_key: Callable[[], str],
    secret: SecretReader,
) -> None:
    render_header(
        "API 密钥配置",
        "SESSION CREDENTIALS",
        "back_from_settings",
    )
    st.caption(
        "临时密钥仅保存在当前 Streamlit 会话中，关闭或刷新会话后失效；"
        "系统不会将密钥写入项目文件，也不会显示完整密钥。"
    )
    if "api_key_draft" not in st.session_state:
        st.session_state.api_key_draft = str(st.session_state.get("deepseek_api_key", ""))
    draft = st.text_input(
        "DeepSeek API Key",
        type="password",
        key="api_key_draft",
        placeholder="sk-...",
        help="留空时自动使用 Streamlit Secrets 中配置的 DEEPSEEK_API_KEY。",
    )
    test_column, save_column, clear_column = st.columns(3)
    with test_column:
        if st.button("测试连接", width="stretch"):
            candidate_key = draft.strip() or deepseek_key()
            model = secret("DEEPSEEK_MODEL", "deepseek-chat")
            if not candidate_key:
                st.error("未提供 API Key：请输入后测试，或在 Secrets 中配置。")
            else:
                with st.spinner("正在验证 DeepSeek Key 与模型…"):
                    connected, message = probe_deepseek_connection(
                        candidate_key,
                        model=model,
                    )
                if connected:
                    st.success(message)
                else:
                    st.error(message)
    with save_column:
        if st.button("保存到当前会话", type="primary", width="stretch"):
            cleaned = draft.strip()
            if not cleaned:
                st.error("请输入有效的 DeepSeek API Key。")
            else:
                st.session_state.deepseek_api_key = cleaned
                st.session_state.api_key_notice = "临时 API Key 已保存到当前会话。"
                st.rerun()
    with clear_column:
        if st.button("清除临时密钥", width="stretch"):
            st.session_state.deepseek_api_key = ""
            st.session_state.api_key_draft = ""
            st.session_state.api_key_notice = "临时 API Key 已清除，将回退使用 Secrets。"
            st.rerun()

    notice = st.session_state.pop("api_key_notice", "")
    if notice:
        st.success(notice)
    if st.session_state.get("deepseek_api_key"):
        st.success("当前会话临时 Key 已配置。")
    elif secret("DEEPSEEK_API_KEY", ""):
        st.info("当前使用 Streamlit Secrets 中的 DEEPSEEK_API_KEY。")
    else:
        st.warning("当前尚未配置 DeepSeek API Key；业财转换将使用本地准则校验兜底。")
