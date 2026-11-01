"""Authentication, organization membership, and role-based permissions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

from finguard.workspace_store import (
    DEFAULT_ORGANIZATION_ID,
    DEFAULT_USER_ID,
    DEFAULT_USER_NAME,
    WorkspaceStore,
)

PASSWORD_ITERATIONS = 210_000
ROLE_LABELS = {
    "admin": "企业管理员",
    "auditor": "审计员",
    "accountant": "会计人员",
    "viewer": "只读成员",
    "local_admin": "本地管理员",
}
ASSIGNABLE_ROLES = ("admin", "auditor", "accountant", "viewer")

PERM_WORKSPACE_VIEW = "workspace.view"
PERM_CASE_MANAGE = "case.manage"
PERM_EVIDENCE_WRITE = "evidence.write"
PERM_TEAM_MANAGE = "team.manage"
PERM_FINANCE_AUDIT = "finance.audit"
PERM_BUSINESS_CONVERT = "business.convert"
PERM_RISK_ANALYZE = "risk.analyze"
PERM_PPT_GENERATE = "ppt.generate"
PERM_OPERATIONS_VIEW = "operations.view"
PERM_SETTINGS_AI = "settings.ai"

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "local_admin": frozenset({"*"}),
    "admin": frozenset({"*"}),
    "auditor": frozenset(
        {
            PERM_WORKSPACE_VIEW,
            PERM_CASE_MANAGE,
            PERM_EVIDENCE_WRITE,
            PERM_FINANCE_AUDIT,
            PERM_RISK_ANALYZE,
            PERM_PPT_GENERATE,
            PERM_OPERATIONS_VIEW,
            PERM_SETTINGS_AI,
        }
    ),
    "accountant": frozenset(
        {
            PERM_WORKSPACE_VIEW,
            PERM_CASE_MANAGE,
            PERM_EVIDENCE_WRITE,
            PERM_BUSINESS_CONVERT,
            PERM_PPT_GENERATE,
            PERM_OPERATIONS_VIEW,
            PERM_SETTINGS_AI,
        }
    ),
    "viewer": frozenset({PERM_WORKSPACE_VIEW}),
}


class AuthenticationError(ValueError):
    """Safe-to-display authentication or membership error."""


@dataclass(frozen=True)
class AuthContext:
    organization_id: str
    organization_name: str
    user_id: str
    display_name: str
    role: str
    username: str

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)


def _hash_password(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return base64.b64encode(digest).decode("ascii")


def create_password_record(password: str) -> tuple[str, str]:
    clean_password = str(password or "")
    if len(clean_password) < 8:
        raise AuthenticationError("密码至少需要 8 位")
    salt = secrets.token_bytes(16)
    return (
        _hash_password(clean_password, salt),
        base64.b64encode(salt).decode("ascii"),
    )


def verify_password(password: str, password_hash: str, password_salt: str) -> bool:
    try:
        salt = base64.b64decode(password_salt.encode("ascii"), validate=True)
    except (ValueError, TypeError):
        return False
    candidate = _hash_password(str(password or ""), salt)
    return hmac.compare_digest(candidate, str(password_hash or ""))


class IdentityService:
    """Authentication facade backed by the shared workspace repository."""

    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store

    @staticmethod
    def has_permission(user: AuthContext, permission: str) -> bool:
        permissions = ROLE_PERMISSIONS.get(user.role, frozenset())
        return "*" in permissions or permission in permissions

    def local_admin(self) -> AuthContext:
        organization = self.store.get_organization(DEFAULT_ORGANIZATION_ID) or {}
        return AuthContext(
            organization_id=DEFAULT_ORGANIZATION_ID,
            organization_name=str(organization.get("name", "本地企业空间")),
            user_id=DEFAULT_USER_ID,
            display_name=DEFAULT_USER_NAME,
            role="local_admin",
            username="local-admin",
        )

    def list_organizations(self) -> list[dict[str, Any]]:
        return self.store.list_login_organizations()

    def create_initial_admin(
        self,
        *,
        organization_name: str,
        username: str,
        display_name: str,
        password: str,
    ) -> AuthContext:
        if self.list_organizations():
            raise AuthenticationError("系统已经完成初始化，请直接登录")
        slug = f"org-{uuid.uuid4().hex[:10]}"
        organization = self.store.create_organization(
            name=organization_name,
            slug=slug,
        )
        password_hash, password_salt = create_password_record(password)
        member = self.store.create_member(
            organization_id=organization["id"],
            username=username,
            display_name=display_name,
            role="admin",
            password_hash=password_hash,
            password_salt=password_salt,
        )
        self.store.log_event(
            action="organization.initialized",
            entity_type="organization",
            entity_id=organization["id"],
            organization_id=organization["id"],
            user_id=member["id"],
            details={"organization_name": organization["name"]},
        )
        return self._context_from_member(member)

    def authenticate(
        self,
        *,
        organization_id: str,
        username: str,
        password: str,
    ) -> AuthContext | None:
        credentials = self.store.get_member_credentials(
            organization_id=organization_id,
            username=username,
        )
        if credentials is None:
            return None
        locked_until = credentials.get("locked_until")
        if locked_until is not None and float(locked_until) > time.time():
            return None
        if not verify_password(
            password,
            str(credentials.get("password_hash", "")),
            str(credentials.get("password_salt", "")),
        ):
            self.store.record_login_failure(str(credentials["id"]))
            return None
        context = self._context_from_member(credentials)
        self.store.record_login_success(context.user_id)
        self.store.log_event(
            action="auth.login",
            entity_type="member",
            entity_id=context.user_id,
            organization_id=context.organization_id,
            user_id=context.user_id,
        )
        return context

    def find_member_for_password_reset(
        self,
        *,
        organization_id: str,
        username: str,
    ) -> dict[str, Any] | None:
        return self.store.get_member_credentials(
            organization_id=organization_id,
            username=username,
        )

    def verify_member_password(self, member_id: str, password: str) -> bool:
        member = self.store.get_member(member_id)
        if member is None or not member.get("active"):
            return False
        credentials = self.store.get_member_credentials(
            organization_id=str(member["organization_id"]),
            username=str(member.get("username", "")),
        )
        if credentials is None:
            return False
        return verify_password(
            password,
            str(credentials.get("password_hash", "")),
            str(credentials.get("password_salt", "")),
        )

    def set_member_password(self, member_id: str, password: str) -> None:
        password_hash, password_salt = create_password_record(password)
        self.store.set_member_password(
            member_id,
            password_hash=password_hash,
            password_salt=password_salt,
        )
        self.store.invalidate_auth_tokens(member_id)
        self.store.revoke_member_browser_sessions(member_id)

    def get_context(self, user_id: str) -> AuthContext | None:
        member = self.store.get_member(user_id)
        if member is None or not member.get("active"):
            return None
        return self._context_from_member(member)

    @staticmethod
    def _session_token_hash(token: str) -> str:
        return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()

    def create_browser_session(
        self,
        member_id: str,
        *,
        ttl_seconds: int,
        user_agent: str = "",
    ) -> tuple[str, dict[str, Any]]:
        token = secrets.token_urlsafe(32)
        now = time.time()
        session = self.store.create_browser_session(
            member_id=str(member_id),
            token_hash=self._session_token_hash(token),
            created_at=now,
            expires_at=now + max(300, int(ttl_seconds)),
            user_agent=user_agent,
        )
        return token, session

    def restore_browser_session(
        self,
        token: str,
        *,
        idle_minutes: int,
    ) -> tuple[AuthContext, dict[str, Any]] | None:
        clean_token = str(token or "").strip()
        if not clean_token:
            return None
        session = self.store.get_browser_session_by_token_hash(
            self._session_token_hash(clean_token)
        )
        if session is None:
            return None
        now = time.time()
        revoked = session.get("revoked_at") is not None
        expired = (
            revoked
            or float(session.get("expires_at", 0) or 0) <= now
            or now - float(session.get("last_seen_at", 0) or 0)
            > max(5, int(idle_minutes)) * 60
        )
        if expired:
            self.store.revoke_browser_session(str(session["id"]), now)
            return None
        context = self.get_context(str(session["member_id"]))
        if context is None:
            self.store.revoke_browser_session(str(session["id"]), now)
            return None
        self.store.touch_browser_session(str(session["id"]), now)
        session["last_seen_at"] = now
        return context, session

    def touch_browser_session(self, session_id: str) -> None:
        if session_id:
            self.store.touch_browser_session(str(session_id), time.time())

    def revoke_browser_session(self, session_id: str) -> None:
        if session_id:
            self.store.revoke_browser_session(str(session_id))

    def list_members(self, user: AuthContext) -> list[dict[str, Any]]:
        if not self.has_permission(user, PERM_WORKSPACE_VIEW):
            raise AuthenticationError("当前角色无权查看企业成员")
        return self.store.list_members(user.organization_id)

    def create_member(
        self,
        actor: AuthContext,
        *,
        username: str,
        display_name: str,
        password: str,
        role: str,
    ) -> dict[str, Any]:
        if not self.has_permission(actor, PERM_TEAM_MANAGE):
            raise AuthenticationError("当前角色无权添加企业成员")
        if role not in ASSIGNABLE_ROLES:
            raise AuthenticationError("成员角色无效")
        password_hash, password_salt = create_password_record(password)
        member = self.store.create_member(
            organization_id=actor.organization_id,
            username=username,
            display_name=display_name,
            role=role,
            password_hash=password_hash,
            password_salt=password_salt,
        )
        self.store.log_event(
            action="member.created",
            entity_type="member",
            entity_id=member["id"],
            organization_id=actor.organization_id,
            user_id=actor.user_id,
            details={"role": role, "display_name": display_name},
        )
        return member

    def update_member(
        self,
        actor: AuthContext,
        member_id: str,
        *,
        role: str,
        active: bool,
    ) -> dict[str, Any]:
        if not self.has_permission(actor, PERM_TEAM_MANAGE):
            raise AuthenticationError("当前角色无权修改企业成员")
        if role not in ASSIGNABLE_ROLES:
            raise AuthenticationError("成员角色无效")
        if member_id == actor.user_id and (not active or role != actor.role):
            raise AuthenticationError("不能停用自己或修改自己的管理员角色")
        member = self.store.update_member(
            member_id,
            role=role,
            active=active,
            organization_id=actor.organization_id,
        )
        if not active:
            self.store.revoke_member_browser_sessions(member_id)
        self.store.log_event(
            action="member.updated",
            entity_type="member",
            entity_id=member_id,
            organization_id=actor.organization_id,
            user_id=actor.user_id,
            details={"role": role, "active": active},
        )
        return member

    def _context_from_member(self, member: dict[str, Any]) -> AuthContext:
        organization = self.store.get_organization(str(member["organization_id"])) or {}
        return AuthContext(
            organization_id=str(member["organization_id"]),
            organization_name=str(organization.get("name", "企业空间")),
            user_id=str(member["id"]),
            display_name=str(member["display_name"]),
            role=str(member["role"]),
            username=str(member.get("username", "")),
        )
