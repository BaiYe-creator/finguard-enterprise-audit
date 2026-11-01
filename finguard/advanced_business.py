"""Isolated non-standard asset disposal accounting rules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

CLEARING_ACCOUNT = "固定资产清理"
GAIN_LOSS_ACCOUNT = "资产处置损益"


class AssetDisposalError(ValueError):
    """Raised when an asset disposal description cannot be valued safely."""


@dataclass(frozen=True)
class AssetDisposalResult:
    original_value: float
    accumulated_depreciation: float
    net_book_value: float
    proceeds: float
    gain_loss: float
    entries: list[dict[str, Any]] = field(default_factory=list)


def is_asset_disposal_business(text: str) -> bool:
    return "原值" in text and "折旧" in text


def _parse_number(number: str, unit: str | None) -> float:
    multiplier = {
        "万元": 10_000,
        "万": 10_000,
        "千元": 1_000,
        "千": 1_000,
        "百元": 100,
        "百": 100,
        "元": 1,
    }.get(unit or "", 1)
    return round(float(number.replace(",", "")) * multiplier, 2)


def _extract_labeled_amount(text: str, label_pattern: str, field_name: str) -> float:
    match = re.search(
        rf"(?:{label_pattern})\s*(?:为|是|金额|合计|共计|[:：])?\s*"
        r"[¥￥]?\s*(\d+(?:\.\d+)?)\s*(万元|万|千元|千|百元|百|元)?",
        text.replace(",", ""),
    )
    if not match:
        raise AssetDisposalError(f"无法从资产处置描述中提取{field_name}")
    return _parse_number(match.group(1), match.group(2))


def _entry(
    business: str,
    *,
    debit: str,
    credit: str,
    amount: float,
    description: str,
) -> dict[str, Any]:
    return {
        "business": business,
        "debit": debit,
        "credit": credit,
        "amount": round(amount, 2),
        "description": description,
        "calculation_source": "资产处置专用规则",
    }


def build_asset_disposal_entries(business: str) -> AssetDisposalResult:
    if not is_asset_disposal_business(business):
        raise AssetDisposalError("业务描述未同时包含“原值”和“折旧”")

    original_value = _extract_labeled_amount(
        business,
        r"资产原值|设备原值|原值|原始价值",
        "资产原值",
    )
    accumulated_depreciation = _extract_labeled_amount(
        business,
        r"已提折旧|累计折旧|已折旧|折旧",
        "累计折旧",
    )
    proceeds = _extract_labeled_amount(
        business,
        r"收款总额|收款金额|收到转账|收款|收到|转让款|处置款|售价|卖价|成交价",
        "收款总额",
    )

    if original_value <= 0:
        raise AssetDisposalError("资产原值必须大于 0")
    if accumulated_depreciation < 0 or accumulated_depreciation > original_value:
        raise AssetDisposalError("累计折旧必须在 0 和资产原值之间")
    if proceeds < 0:
        raise AssetDisposalError("收款总额不能为负数")

    net_book_value = round(original_value - accumulated_depreciation, 2)
    gain_loss = round(proceeds - net_book_value, 2)
    entries = [
        _entry(
            business,
            debit=CLEARING_ACCOUNT,
            credit="",
            amount=net_book_value,
            description="结转固定资产账面净值",
        ),
        _entry(
            business,
            debit="累计折旧",
            credit="",
            amount=accumulated_depreciation,
            description="冲减已计提累计折旧",
        ),
        _entry(
            business,
            debit="",
            credit="固定资产",
            amount=original_value,
            description="转出固定资产原值",
        ),
        _entry(
            business,
            debit="银行存款",
            credit="",
            amount=proceeds,
            description="收到资产处置款项，默认无票且不拆分销项税",
        ),
        _entry(
            business,
            debit="",
            credit=CLEARING_ACCOUNT,
            amount=proceeds,
            description="结转资产处置收款",
        ),
    ]

    if gain_loss > 0:
        entries.append(
            _entry(
                business,
                debit=CLEARING_ACCOUNT,
                credit=GAIN_LOSS_ACCOUNT,
                amount=gain_loss,
                description="结转资产处置净收益",
            )
        )
    elif gain_loss < 0:
        entries.append(
            _entry(
                business,
                debit=GAIN_LOSS_ACCOUNT,
                credit=CLEARING_ACCOUNT,
                amount=abs(gain_loss),
                description="结转资产处置净损失",
            )
        )

    return AssetDisposalResult(
        original_value=original_value,
        accumulated_depreciation=accumulated_depreciation,
        net_book_value=net_book_value,
        proceeds=proceeds,
        gain_loss=gain_loss,
        entries=entries,
    )
