"""Email verification, password reset, and password-change workflows."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from finguard.email_delivery import EmailDeliveryResult, EmailDeliveryService
from finguard.identity import AuthContext, IdentityService
from finguard.workspace_store import WorkspaceStore

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CODE_TTL_SECONDS = 300
CODE_RESEND_SECONDS = 60
CODE_HOURLY_LIMIT = 5


class AccountSecurityError(ValueError):
    """Safe-to-display verification or password workflow error."""


def _hash_code(code: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(code).encode("utf-8"),
        salt,
        120_000,
    )
    return base64.b64encode(digest).decode("ascii")


def _normalize_email(email: str) -> str:
    clean = str(email or "").strip().casefold()
    if not EMAIL_PATTERN.fullmatch(clean):
        raise AccountSecurityError("请输入有效的邮箱地址")
    return clean


class AccountSecurityService:
    def __init__(
        self,
        store: WorkspaceStore,
        identity: IdentityService,
        email_delivery: EmailDeliveryService,
    ) -> None:
        self.store = store
        self.identity = identity
        self.email_delivery = email_delivery

    def request_email_binding(
        self,
        user: AuthContext,
        email: str,
    ) -> EmailDeliveryResult:
        destination = _normalize_email(email)
        code = self._issue_code(
            member_id=user.user_id,
            purpose="bind_email",
            destination=destination,
        )
        result = self.email_delivery.send_verification_code(
            recipient=destination,
            code=code,
            purpose="bind_email",
        )
        if not result.success:
            self.store.invalidate_auth_tokens(user.user_id, "bind_email")
        return result

    def confirm_email_binding(
        self,
        user: AuthContext,
        code: str,
    ) -> str:
        token = self._verified_token(
            member_id=user.user_id,
            purpose="bind_email",
            code=code,
        )
        destination = str(token["destination"])
        self.store.set_member_email(
            user.user_id,
            email=destination,
            verified=True,
        )
        self.store.mark_auth_token_used(str(token["id"]))
        self.store.log_event(
            action="member.email_bound",
            entity_type="member",
            entity_id=user.user_id,
            organization_id=user.organization_id,
            user_id=user.user_id,
            details={"email": destination},
        )
        return destination

    def request_password_reset(
        self,
        *,
        organization_id: str,
        username: str,
    ) -> EmailDeliveryResult:
        member = self.identity.find_member_for_password_reset(
            organization_id=organization_id,
            username=username,
        )
        generic = EmailDeliveryResult(
            True,
            "如果账号已绑定邮箱，验证码将发送至该邮箱。",
        )
        if member is None:
            return generic
        email = str(member.get("email", "") or "").strip()
        if not email or not member.get("email_verified"):
            return generic
        code = self._issue_code(
            member_id=str(member["id"]),
            purpose="reset_password",
            destination=email,
        )
        result = self.email_delivery.send_verification_code(
            recipient=email,
            code=code,
            purpose="reset_password",
        )
        if not result.success:
            self.store.invalidate_auth_tokens(str(member["id"]), "reset_password")
            return result
        return EmailDeliveryResult(
            True,
            generic.message,
            dev_code=result.dev_code,
        )

    def reset_password(
        self,
        *,
        organization_id: str,
        username: str,
        code: str,
        new_password: str,
    ) -> None:
        member = self.identity.find_member_for_password_reset(
            organization_id=organization_id,
            username=username,
        )
        if member is None:
            raise AccountSecurityError("验证码无效或已过期")
        token = self._verified_token(
            member_id=str(member["id"]),
            purpose="reset_password",
            code=code,
        )
        self.identity.set_member_password(str(member["id"]), new_password)
        self.store.mark_auth_token_used(str(token["id"]))
        self.store.log_event(
            action="auth.password_reset",
            entity_type="member",
            entity_id=str(member["id"]),
            organization_id=organization_id,
            user_id=str(member["id"]),
        )

    def change_password(
        self,
        user: AuthContext,
        *,
        current_password: str,
        new_password: str,
    ) -> None:
        if not self.identity.verify_member_password(user.user_id, current_password):
            raise AccountSecurityError("当前密码不正确")
        if hmac.compare_digest(str(current_password), str(new_password)):
            raise AccountSecurityError("新密码不能与当前密码相同")
        self.identity.set_member_password(user.user_id, new_password)
        self.store.log_event(
            action="auth.password_changed",
            entity_type="member",
            entity_id=user.user_id,
            organization_id=user.organization_id,
            user_id=user.user_id,
        )

    def _issue_code(
        self,
        *,
        member_id: str,
        purpose: str,
        destination: str,
    ) -> str:
        recent_cutoff = datetime.now(UTC).astimezone() - timedelta(seconds=CODE_RESEND_SECONDS)
        if (
            self.store.count_recent_auth_tokens(
                member_id=member_id,
                purpose=purpose,
                since=recent_cutoff,
            )
            > 0
        ):
            raise AccountSecurityError("验证码发送过于频繁，请稍后再试")
        one_hour_ago = datetime.now(UTC).astimezone() - timedelta(hours=1)
        if (
            self.store.count_recent_auth_tokens(
                member_id=member_id,
                purpose=purpose,
                since=one_hour_ago,
            )
            >= CODE_HOURLY_LIMIT
        ):
            raise AccountSecurityError("验证码请求次数过多，请一小时后再试")

        code = f"{secrets.randbelow(1_000_000):06d}"
        salt = secrets.token_bytes(16)
        self.store.create_auth_token(
            member_id=member_id,
            purpose=purpose,
            destination=destination,
            code_hash=_hash_code(code, salt),
            code_salt=base64.b64encode(salt).decode("ascii"),
            expires_at=time.time() + CODE_TTL_SECONDS,
        )
        return code

    def _verified_token(
        self,
        *,
        member_id: str,
        purpose: str,
        code: str,
    ) -> dict[str, Any]:
        token = self.store.get_active_auth_token(
            member_id=member_id,
            purpose=purpose,
        )
        if token is None:
            raise AccountSecurityError("验证码无效或已过期")
        if float(token["expires_at"]) < time.time():
            self.store.mark_auth_token_used(str(token["id"]))
            raise AccountSecurityError("验证码已过期，请重新获取")
        attempts = self.store.increment_auth_token_attempts(str(token["id"]))
        if attempts > int(token["max_attempts"]):
            self.store.mark_auth_token_used(str(token["id"]))
            raise AccountSecurityError("验证码尝试次数过多，请重新获取")
        try:
            salt = base64.b64decode(str(token["code_salt"]).encode("ascii"), validate=True)
        except (ValueError, TypeError) as exc:
            raise AccountSecurityError("验证码状态异常，请重新获取") from exc
        candidate = _hash_code(str(code).strip(), salt)
        if not hmac.compare_digest(candidate, str(token["code_hash"])):
            raise AccountSecurityError("验证码不正确")
        return token
