"""Dynamic, isolated capacity simulation rules for FinGuard V0.4."""

from __future__ import annotations

import io
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_ASSET_LINES: tuple[dict[str, Any], ...] = (
    {"产线名称": "割草机产线 A", "数量": 1, "单台日产能": 60.0},
    {"产线名称": "割草机产线 B", "数量": 1, "单台日产能": 60.0},
)

NAME_ALIASES = (
    "产线名称",
    "资产名称",
    "生产线名称",
    "设备名称",
    "产线",
    "资产",
    "asset_name",
    "line_name",
)
QUANTITY_ALIASES = (
    "数量",
    "产线数量",
    "生产线数量",
    "设备台数",
    "台数",
    "设备数量",
    "资产数量",
    "quantity",
    "qty",
    "count",
)
CAPACITY_ALIASES = (
    "单台日产能",
    "单台日产量",
    "单线日产能",
    "单线日产",
    "日产能",
    "日产量",
    "产能",
    "daily_capacity",
    "capacity_per_unit",
)


class CapacityInputError(ValueError):
    """Raised when capacity simulation input is invalid."""


@dataclass(frozen=True)
class CapacityAsset:
    line_name: str
    quantity: int
    daily_capacity_per_unit: float

    @property
    def daily_capacity(self) -> float:
        return round(self.quantity * self.daily_capacity_per_unit, 2)

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "产线名称": self.line_name,
            "数量": self.quantity,
            "单台日产能": self.daily_capacity_per_unit,
            "日总产能": self.daily_capacity,
        }


@dataclass(frozen=True)
class CapacityResult:
    asset_lines: tuple[CapacityAsset, ...]
    order_quantity: int
    required_days: int
    bottleneck_line: str
    bottleneck_capacity: float
    total_limit_capacity: float
    sufficient: bool
    required_completion_days: int
    capacity_shortfall: float

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["asset_lines"] = [asset.to_dict() for asset in self.asset_lines]
        return payload


def default_assets() -> list[CapacityAsset]:
    return [
        CapacityAsset(
            line_name=str(item["产线名称"]),
            quantity=int(item["数量"]),
            daily_capacity_per_unit=float(item["单台日产能"]),
        )
        for item in DEFAULT_ASSET_LINES
    ]


def _normalized(value: Any) -> str:
    return str(value).strip().casefold().replace(" ", "")


def _find_column(dataframe: pd.DataFrame, aliases: tuple[str, ...]) -> str:
    columns = {str(column): _normalized(column) for column in dataframe.columns}
    for alias in aliases:
        alias_key = _normalized(alias)
        for original, normalized in columns.items():
            if normalized == alias_key:
                return original
    for alias in aliases:
        alias_key = _normalized(alias)
        for original, normalized in columns.items():
            if alias_key in normalized or normalized in alias_key:
                return original
    raise CapacityInputError(f"固定资产台账缺少必要字段：{aliases[0]}")


def _parse_positive_number(value: Any, field_name: str, row_number: int) -> float:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        parsed = float(value)
    else:
        match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
        if not match:
            raise CapacityInputError(f"第 {row_number} 行的“{field_name}”不是有效数字")
        parsed = float(match.group())
    if parsed <= 0:
        raise CapacityInputError(f"第 {row_number} 行的“{field_name}”必须大于 0")
    return parsed


def parse_asset_dataframe(dataframe: pd.DataFrame) -> list[CapacityAsset]:
    if dataframe.empty:
        raise CapacityInputError("固定资产台账没有可读取的数据行")

    name_column = _find_column(dataframe, NAME_ALIASES)
    quantity_column = _find_column(dataframe, QUANTITY_ALIASES)
    capacity_column = _find_column(dataframe, CAPACITY_ALIASES)
    assets: list[CapacityAsset] = []

    for position, (_, row) in enumerate(dataframe.iterrows(), start=2):
        name = str(row.get(name_column, "")).strip()
        if not name or name.casefold() == "nan":
            continue
        try:
            quantity_value = _parse_positive_number(
                row.get(quantity_column),
                "数量",
                position,
            )
            capacity_value = _parse_positive_number(
                row.get(capacity_column),
                "单台日产能",
                position,
            )
        except CapacityInputError:
            continue
        quantity = int(quantity_value)
        if quantity_value != quantity:
            continue
        assets.append(
            CapacityAsset(
                line_name=name,
                quantity=quantity,
                daily_capacity_per_unit=capacity_value,
            )
        )

    if not assets:
        raise CapacityInputError("固定资产台账中没有有效的产线数据")
    return assets


def excel_sheet_names(payload: bytes) -> list[str]:
    return [str(name) for name in pd.ExcelFile(io.BytesIO(payload)).sheet_names]


def load_asset_ledger(
    payload: bytes,
    filename: str,
    *,
    sheet_name: str | None = None,
) -> list[CapacityAsset]:
    suffix = Path(filename).suffix.casefold()
    if suffix == ".csv":
        last_error: Exception | None = None
        dataframe = None
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                dataframe = pd.read_csv(
                    io.BytesIO(payload),
                    encoding=encoding,
                    sep=None,
                    engine="python",
                )
                break
            except (UnicodeDecodeError, pd.errors.ParserError) as exc:
                last_error = exc
        if dataframe is None:
            raise CapacityInputError(f"CSV 解析失败：{last_error}")
    elif suffix == ".xlsx":
        dataframe = pd.read_excel(io.BytesIO(payload), sheet_name=sheet_name or 0)
    else:
        raise CapacityInputError("固定资产台账仅支持 CSV 或 XLSX")
    return parse_asset_dataframe(dataframe)


def simulate_dynamic_capacity(
    assets: list[CapacityAsset] | list[dict[str, Any]],
    order_quantity: int | float,
    required_days: int | float,
) -> CapacityResult:
    normalized_assets: list[CapacityAsset] = []
    for item in assets:
        if isinstance(item, CapacityAsset):
            normalized_assets.append(item)
        else:
            normalized_assets.append(
                CapacityAsset(
                    line_name=str(item["产线名称"]).strip(),
                    quantity=int(item["数量"]),
                    daily_capacity_per_unit=float(item["单台日产能"]),
                )
            )
    if not normalized_assets:
        raise CapacityInputError("至少需要一条有效产线")

    try:
        order = float(order_quantity)
        days_value = float(required_days)
    except (TypeError, ValueError) as exc:
        raise CapacityInputError("订单数量和天数必须是数字") from exc
    if not math.isfinite(order) or order <= 0:
        raise CapacityInputError("订单数量必须大于 0")
    if not math.isfinite(days_value) or days_value <= 0:
        raise CapacityInputError("要求天数必须大于 0")
    if days_value != int(days_value):
        raise CapacityInputError("要求天数必须是整数")

    order_count = int(order)
    days = int(days_value)
    if order != order_count:
        raise CapacityInputError("订单数量必须是整数")

    bottleneck = min(normalized_assets, key=lambda asset: asset.daily_capacity)
    bottleneck_capacity = round(bottleneck.daily_capacity, 2)
    if bottleneck_capacity <= 0:
        raise CapacityInputError("瓶颈产能必须大于 0")
    total_limit_capacity = round(bottleneck_capacity * days, 2)
    required_completion_days = math.ceil(order_count / bottleneck_capacity)
    sufficient = total_limit_capacity >= order_count
    capacity_shortfall = round(max(order_count - total_limit_capacity, 0), 2)

    return CapacityResult(
        asset_lines=tuple(normalized_assets),
        order_quantity=order_count,
        required_days=days,
        bottleneck_line=bottleneck.line_name,
        bottleneck_capacity=bottleneck_capacity,
        total_limit_capacity=total_limit_capacity,
        sufficient=sufficient,
        required_completion_days=required_completion_days,
        capacity_shortfall=capacity_shortfall,
    )
