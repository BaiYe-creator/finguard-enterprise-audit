from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from finguard.identity import (
    PERM_FINANCE_AUDIT,
    PERM_TEAM_MANAGE,
    AuthenticationError,
    IdentityService,
    create_password_record,
)
from finguard.workspace_store import WorkspaceStore


def test_authentication_roles_and_organization_isolation() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        store = WorkspaceStore(Path(directory) / "identity.db")
        identity = IdentityService(store)

        admin = identity.create_initial_admin(
            organization_name="甲方制造",
            username="admin-a",
            display_name="甲管理员",
            password="secure-pass-a",
        )
        assert (
            identity.authenticate(
                organization_id=admin.organization_id,
                username="admin-a",
                password="secure-pass-a",
            )
            == admin
        )
        assert (
            identity.authenticate(
                organization_id=admin.organization_id,
                username="admin-a",
                password="wrong-password",
            )
            is None
        )
        assert identity.has_permission(admin, PERM_TEAM_MANAGE)

        viewer_member = identity.create_member(
            admin,
            username="viewer-a",
            display_name="甲只读",
            password="viewer-pass-a",
            role="viewer",
        )
        viewer = identity.get_context(viewer_member["id"])
        assert viewer is not None
        assert not identity.has_permission(viewer, PERM_FINANCE_AUDIT)
        try:
            identity.create_member(
                viewer,
                username="blocked",
                display_name="Blocked",
                password="blocked-pass",
                role="viewer",
            )
        except AuthenticationError as exc:
            assert "无权" in str(exc)
        else:
            raise AssertionError("viewer must not create members")

        second_organization = store.create_organization(name="乙方制造", slug="party-b")
        password_hash, password_salt = create_password_record("secure-pass-b")
        store.create_member(
            organization_id=second_organization["id"],
            username="admin-b",
            display_name="乙管理员",
            role="admin",
            password_hash=password_hash,
            password_salt=password_salt,
        )
        second_admin = identity.authenticate(
            organization_id=second_organization["id"],
            username="admin-b",
            password="secure-pass-b",
        )
        assert second_admin is not None

        case_a = store.create_case(
            title="甲项目",
            period="2026-10",
            organization_id=admin.organization_id,
            owner_id=admin.user_id,
        )
        case_b = store.create_case(
            title="乙项目",
            period="2026-10",
            organization_id=second_admin.organization_id,
            owner_id=second_admin.user_id,
        )

        assert [case["id"] for case in store.list_cases(admin.organization_id)] == [case_a["id"]]
        assert [case["id"] for case in store.list_cases(second_admin.organization_id)] == [
            case_b["id"]
        ]
        assert store.get_case(case_b["id"], admin.organization_id) is None
        assert store.get_case(case_a["id"], second_admin.organization_id) is None


def test_password_validation_and_duplicate_member_rejection() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        identity = IdentityService(WorkspaceStore(Path(directory) / "identity.db"))
        admin = identity.create_initial_admin(
            organization_name="测试制造",
            username="admin",
            display_name="管理员",
            password="password-123",
        )
        identity.create_member(
            admin,
            username="auditor",
            display_name="审计员",
            password="password-456",
            role="auditor",
        )

        try:
            identity.create_member(
                admin,
                username="auditor",
                display_name="重复用户",
                password="password-789",
                role="viewer",
            )
        except ValueError as exc:
            assert "同名用户" in str(exc)
        else:
            raise AssertionError("duplicate usernames must be rejected")

        try:
            create_password_record("short")
        except AuthenticationError as exc:
            assert "至少需要 8 位" in str(exc)
        else:
            raise AssertionError("short passwords must be rejected")
