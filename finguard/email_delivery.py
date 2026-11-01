"""SMTP delivery adapter for account verification codes."""

from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage


@dataclass(frozen=True)
class EmailDeliveryConfig:
    host: str = ""
    port: int = 465
    username: str = ""
    auth_code: str = ""
    sender: str = ""
    security: str = "ssl"
    timeout_seconds: float = 12.0
    dev_mode: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.host and self.username and self.auth_code)


@dataclass(frozen=True)
class EmailDeliveryResult:
    success: bool
    message: str
    dev_code: str = ""


PURPOSE_LABELS = {
    "bind_email": "绑定邮箱",
    "reset_password": "重置密码",
}


class EmailDeliveryService:
    """Send one-time verification codes without exposing credentials or codes in logs."""

    def __init__(self, config: EmailDeliveryConfig) -> None:
        self.config = config

    def send_verification_code(
        self,
        *,
        recipient: str,
        code: str,
        purpose: str,
    ) -> EmailDeliveryResult:
        clean_recipient = str(recipient or "").strip()
        if "@" not in clean_recipient:
            return EmailDeliveryResult(False, "邮箱格式不正确")
        if self.config.dev_mode:
            return EmailDeliveryResult(
                True,
                "开发模式：验证码未实际发送。",
                dev_code=str(code),
            )
        if not self.config.configured:
            return EmailDeliveryResult(False, "邮件服务尚未配置")

        purpose_label = PURPOSE_LABELS.get(purpose, "账号验证")
        message = EmailMessage()
        message["Subject"] = f"FinGuard {purpose_label}验证码"
        message["From"] = self.config.sender or self.config.username
        message["To"] = clean_recipient
        message.set_content(
            f"你的 FinGuard {purpose_label}验证码是：{code}\n\n"
            "验证码 5 分钟内有效，仅可使用一次。\n"
            "如果不是你本人操作，请立即忽略本邮件并检查账号安全。"
        )
        try:
            if self.config.security.casefold() == "ssl":
                with smtplib.SMTP_SSL(
                    self.config.host,
                    self.config.port,
                    timeout=self.config.timeout_seconds,
                ) as smtp:
                    smtp.login(self.config.username, self.config.auth_code)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(
                    self.config.host,
                    self.config.port,
                    timeout=self.config.timeout_seconds,
                ) as smtp:
                    smtp.starttls()
                    smtp.login(self.config.username, self.config.auth_code)
                    smtp.send_message(message)
        except Exception as exc:
            return EmailDeliveryResult(
                False,
                f"邮件发送失败（{type(exc).__name__}）",
            )
        return EmailDeliveryResult(True, "验证码已发送，请检查邮箱。")
