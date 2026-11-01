from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from streamlit.testing.v1 import AppTest

from finguard.identity import IdentityService
from finguard.workspace_store import WorkspaceStore


def test_auth_page_login_logout_and_role_based_home(monkeypatch) -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        database = Path(directory) / "auth.db"
        identity = IdentityService(WorkspaceStore(database))
        admin = identity.create_initial_admin(
            organization_name="登录测试企业",
            username="manager",
            display_name="测试经理",
            password="manager-password",
        )
        identity.create_member(
            admin,
            username="viewer",
            display_name="只读人员",
            password="viewer-password",
            role="viewer",
        )
        monkeypatch.setenv("FINGUARD_DB_PATH", str(database))
        monkeypatch.setenv("FINGUARD_AUTH_REQUIRED", "1")

        app = AppTest.from_file(
            Path(__file__).resolve().parents[1] / "app.py",
            default_timeout=90,
        ).run()
        assert not app.exception
        assert [item.label for item in app.text_input] == ["用户名", "密码"]
        markup = "\n".join(item.value for item in app.markdown)
        assert "fg-auth-gavel-stage" in markup
        assert "data:image/png;base64," in markup
        assert "opacity: .52" in markup
        assert 'button[kind="primary"]' in markup
        assert "#12161B" in markup

        app.text_input[0].set_value("viewer")
        app.text_input[1].set_value("viewer-password")
        app.run()
        next(button for button in app.button if button.label == "登录").click().run()

        assert not app.exception
        assert app.session_state["auth_user_id"] is not None
        labels = [button.label for button in app.button]
        assert "7. 审计项目与证据链" in labels
        assert "1. 财务审计" not in labels
        assert "账号安全" in labels

        next(button for button in app.button if button.label == "账号安全").click().run()
        assert not app.exception
        assert app.session_state["current_page"] == "account"
        assert "登录邮箱" in [item.label for item in app.tabs]
        next(button for button in app.button if button.label == "← 返回主页").click().run()

        next(button for button in app.button if button.label == "退出登录").click().run()
        assert not app.exception
        assert app.session_state["auth_user_id"] is None
        assert [item.label for item in app.text_input] == ["用户名", "密码"]
        next(button for button in app.button if button.label == "忘记密码").click().run()
        assert not app.exception
        assert "用户名" in [item.label for item in app.text_input]
        assert "邮箱验证码" in [item.label for item in app.text_input]
