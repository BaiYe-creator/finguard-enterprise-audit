"""Data minimization and usage accounting for external AI calls."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from finguard.workspace_store import WorkspaceStore

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
BANK_CARD_RE = re.compile(r"(?<!\d)\d{16,19}(?!\d)")


@dataclass(frozen=True)
class RedactionResult:
    text: str
    replacement_count: int


@dataclass(frozen=True)
class AICallDecision:
    allowed: bool
    message: str
    calls_today: int
    tokens_today: int


def redact_sensitive_text(text: str) -> RedactionResult:
    output = str(text or "")
    count = 0
    for pattern, replacement in (
        (EMAIL_RE, "[邮箱]"),
        (ID_CARD_RE, "[身份证号]"),
        (PHONE_RE, "[手机号]"),
        (BANK_CARD_RE, "[银行账号]"),
    ):
        output, replacements = pattern.subn(replacement, output)
        count += replacements
    return RedactionResult(output, count)


class AIGovernanceService:
    def __init__(
        self,
        store: WorkspaceStore,
        *,
        daily_call_limit: int = 100,
        daily_token_limit: int = 200_000,
        cost_per_1k_tokens: float = 0.0,
    ) -> None:
        self.store = store
        self.daily_call_limit = max(1, int(daily_call_limit))
        self.daily_token_limit = max(1, int(daily_token_limit))
        self.cost_per_1k_tokens = max(0.0, float(cost_per_1k_tokens))

    def decide(self, organization_id: str) -> AICallDecision:
        day_start = (
            datetime.now()
            .astimezone()
            .replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        )
        usage = self.store.ai_usage_summary(
            organization_id,
            since=day_start.isoformat(timespec="seconds"),
        )
        calls = int(usage.get("calls", 0) or 0)
        tokens = int(usage.get("estimated_tokens", 0) or 0)
        if calls >= self.daily_call_limit:
            return AICallDecision(False, "今日 AI 调用次数已达上限", calls, tokens)
        if tokens >= self.daily_token_limit:
            return AICallDecision(False, "今日 AI Token 预算已达上限", calls, tokens)
        return AICallDecision(True, "", calls, tokens)

    def redact(self, text: str) -> RedactionResult:
        return redact_sensitive_text(text)

    def record(
        self,
        *,
        organization_id: str,
        user_id: str,
        module: str,
        model: str,
        input_text: str,
        redaction_count: int,
        status: str,
    ) -> int:
        estimated_tokens = max(1, int(len(input_text) / 2))
        estimated_cost = estimated_tokens / 1000 * self.cost_per_1k_tokens
        return self.store.record_ai_usage(
            organization_id=organization_id,
            user_id=user_id,
            module=module,
            model=model,
            input_chars=len(input_text),
            redaction_count=redaction_count,
            status=status,
            estimated_cost=estimated_cost,
        )

    def summary(self, organization_id: str) -> dict[str, Any]:
        return self.store.ai_usage_summary(organization_id)
