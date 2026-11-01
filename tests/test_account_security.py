from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from finguard.account_security import AccountSecurityError, AccountSecurityService
from finguard.email_delivery import EmailDeliveryConfig, EmailDeliveryService
from finguard.identity import IdentityService
from finguard.workspace_store import WorkspaceStore


def _services(database: Path) -> tuple[IdentityService, AccountSecurityService]:
    store = WorkspaceStore(database)
    identity = IdentityService(store)
    delivery = EmailDeliveryService(EmailDeliveryConfig(dev_mode=True))
    return identity, AccountSecurityService(store, identity, delivery)


def test_email_binding_password_change_and_reset_flow() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        identity, security = _services(Path(directory) / "security.db")
        admin = identity.create_initial_admin(
            organization_name="账号安全测试",
            username="admin",
            display_name="管理员",
            password="old-password-123",
        )

        binding = security.request_email_binding(admin, "admin@example.com")
        assert binding.success
        assert len(binding.dev_code) == 6
        with pytest.raises(AccountSecurityError, match="过于频繁"):
            security.request_email_binding(admin, "admin@example.com")

        bound_email = security.confirm_email_binding(admin, binding.dev_code)
        assert bound_email == "admin@example.com"
        assert identity.store.get_member(admin.user_id)["email_verified"] == 1

        security.change_password(
            admin,
            current_password="old-password-123",
            new_password="new-password-456",
        )
        assert (
            identity.authenticate(
                organization_id=admin.organization_id,
                username="admin",
                password="old-password-123",
            )
            is None
        )
        assert (
            identity.authenticate(
                organization_id=admin.organization_id,
                username="admin",
                password="new-password-456",
            )
            is not None
        )

        reset = security.request_password_reset(
            organization_id=admin.organization_id,
            username="admin",
        )
        assert reset.success
        assert len(reset.dev_code) == 6
        security.reset_password(
            organization_id=admin.organization_id,
            username="admin",
            code=reset.dev_code,
            new_password="reset-password-789",
        )
        assert (
            identity.authenticate(
                organization_id=admin.organization_id,
                username="admin",
                password="reset-password-789",
            )
            is not None
        )


def test_login_locks_after_repeated_failures() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        identity, _ = _services(Path(directory) / "security.db")
        admin = identity.create_initial_admin(
            organization_name="锁定测试",
            username="admin",
            display_name="管理员",
            password="correct-password",
        )

        for _ in range(5):
            assert (
                identity.authenticate(
                    organization_id=admin.organization_id,
                    username="admin",
                    password="wrong-password",
                )
                is None
            )
        assert (
            identity.authenticate(
                organization_id=admin.organization_id,
                username="admin",
                password="correct-password",
            )
            is None
        )


def test_unconfigured_email_reports_failure_instead_of_fake_delivery() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        store = WorkspaceStore(Path(directory) / "security.db")
        identity = IdentityService(store)
        delivery = EmailDeliveryService(EmailDeliveryConfig())
        security = AccountSecurityService(store, identity, delivery)
        admin = identity.create_initial_admin(
            organization_name="邮件测试",
            username="admin",
            display_name="管理员",
            password="password-123",
        )

        result = security.request_email_binding(admin, "admin@example.com")
        assert not result.success
        assert result.message == "邮件服务尚未配置"


def test_browser_session_can_be_restored_and_is_revoked_on_password_change() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        identity, security = _services(Path(directory) / "session.db")
        admin = identity.create_initial_admin(
            organization_name="会话测试",
            username="admin",
            display_name="管理员",
            password="old-password-123",
        )
        token, session = identity.create_browser_session(
            admin.user_id,
            ttl_seconds=3600,
            user_agent="test-browser",
        )

        restored = identity.restore_browser_session(token, idle_minutes=30)
        assert restored is not None
        assert restored[0].user_id == admin.user_id
        assert restored[1]["id"] == session["id"]

        security.change_password(
            admin,
            current_password="old-password-123",
            new_password="new-password-456",
        )
        assert identity.restore_browser_session(token, idle_minutes=30) is None


def test_browser_session_expires_after_idle_timeout(monkeypatch) -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        identity, _ = _services(Path(directory) / "idle.db")
        admin = identity.create_initial_admin(
            organization_name="空闲测试",
            username="admin",
            display_name="管理员",
            password="password-123",
        )
        token, session = identity.create_browser_session(
            admin.user_id,
            ttl_seconds=3600,
        )

        monkeypatch.setattr(
            "finguard.identity.time.time",
            lambda: float(session["last_seen_at"]) + 31 * 60,
        )
        assert identity.restore_browser_session(token, idle_minutes=30) is None
