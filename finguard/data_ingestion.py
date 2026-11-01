"""Multi-source financial voucher ingestion and deterministic cleaning."""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
import warnings
from dataclasses import dataclass, field
from datetime import datetime as datetime_type
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
import pandas as pd

from .config import IngestionConfig

CANONICAL_COLUMNS = ["voucher_id", "date", "user", "summary", "debit", "credit"]

# Aliases are normalized before lookup. Add organization-specific aliases here
# or pass an extra_aliases mapping to the ingestion service.
DEFAULT_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "voucher_id": (
        "voucher_id",
        "voucherid",
        "voucher_no",
        "voucher_number",
        "id",
        "凭证号",
        "凭证编号",
        "凭证字号",
        "记账凭证号",
        "业务单号",
        "单据号",
        "单据编号",
        "单号",
        "交易单号",
        "流水号",
        "编号",
    ),
    "date": (
        "date",
        "voucher_date",
        "transaction_date",
        "business_date",
        "记账日期",
        "凭证日期",
        "制单日期",
        "发生日期",
        "业务日期",
        "交易日期",
        "报销日期",
        "日期",
    ),
    "time": (
        "time",
        "transaction_time",
        "created_time",
        "录入时间",
        "制单时间",
        "发生时间",
        "创建时间",
        "提交时间",
        "交易时间",
        "时间",
    ),
    "user": (
        "user",
        "employee",
        "employee_name",
        "preparer",
        "operator",
        "created_by",
        "制单人",
        "经办人",
        "创建人",
        "录入人",
        "报销人",
        "申请人",
        "职工姓名",
        "员工姓名",
        "员工",
        "操作员",
    ),
    "summary": (
        "summary",
        "description",
        "memo",
        "details",
        "business_summary",
        "摘要",
        "说明",
        "事由",
        "费用说明",
        "业务摘要",
        "用途",
        "内容",
        "备注",
        "交易说明",
    ),
    "debit": (
        "debit",
        "debit_amount",
        "dr",
        "借方",
        "借方金额",
        "借",
        "借金额",
        "借方发生额",
        "借方本币金额",
    ),
    "credit": (
        "credit",
        "credit_amount",
        "cr",
        "贷方",
        "贷方金额",
        "贷",
        "贷金额",
        "贷方发生额",
        "贷方本币金额",
    ),
    "amount": (
        "amount",
        "transaction_amount",
        "total_amount",
        "expense_amount",
        "金额",
        "发生额",
        "交易金额",
        "报销金额",
        "费用金额",
        "合计金额",
        "本币金额",
        "原币金额",
    ),
    "direction": (
        "direction",
        "debit_credit",
        "借贷方向",
        "方向",
        "借贷标志",
        "借贷",
        "收付方向",
        "收支方向",
    ),
}

_SUMMARY_ROW_PATTERN = re.compile(
    r"^\s*(?:本页|本日|本期|本月|本年|累计|其中)?"
    r"(?:合计|小计|总计|余额|结转下期|承前页|过次页)"
    r"(?:\s|[:：]|$)",
    flags=re.IGNORECASE,
)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TIME_PATTERN = re.compile(r"(?:^|[T\s])\d{1,2}[:：]\d{2}")


class DataIngestionError(ValueError):
    """Raised when a file or schema cannot be safely processed."""


@dataclass
class IngestionResult:
    clean_data: pd.DataFrame
    rejected_data: pd.DataFrame
    report: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and np.isnan(value):
        return ""
    return str(value)


def normalize_header(value: Any) -> str:
    """Normalize messy spreadsheet headers without transliterating content."""

    text = unicodedata.normalize("NFKC", _safe_str(value)).lstrip("\ufeff")
    text = text.strip().casefold()
    return re.sub(r"[\s_\-—–/\\:：()（）\[\]【】{}<>《》]+", "", text)


def _alias_lookup(extra_aliases: dict[str, tuple[str, ...]] | None) -> dict[str, str]:
    aliases = {key: list(values) for key, values in DEFAULT_COLUMN_ALIASES.items()}
    if extra_aliases:
        for canonical, values in extra_aliases.items():
            if canonical not in CANONICAL_COLUMNS and canonical not in {
                "time",
                "amount",
                "direction",
            }:
                raise DataIngestionError(f"不支持的扩展字段映射: {canonical}")
            aliases.setdefault(canonical, []).extend(values)

    lookup: dict[str, str] = {}
    for canonical, values in aliases.items():
        for value in (canonical, *values):
            lookup[normalize_header(value)] = canonical
    return lookup


def _find_source_columns(
    frame: pd.DataFrame,
    extra_aliases: dict[str, tuple[str, ...]] | None = None,
) -> tuple[dict[str, str], list[str]]:
    lookup = _alias_lookup(extra_aliases)
    found: dict[str, str] = {}
    unknown: list[str] = []
    for source in frame.columns:
        canonical = lookup.get(normalize_header(source))
        if canonical is None:
            unknown.append(str(source))
            continue
        if canonical in found:
            warnings.warn(
                f"字段“{source}”与“{found[canonical]}”都映射到 {canonical}，采用首次出现的字段。",
                UserWarning,
                stacklevel=2,
            )
            continue
        found[canonical] = source
    return found, unknown


def _format_voucher_id(value: Any, fallback: str) -> str:
    text = _safe_str(value).strip()
    return text if text else fallback


def rename_columns(
    frame: pd.DataFrame,
    extra_aliases: dict[str, tuple[str, ...]] | None = None,
) -> pd.DataFrame:
    """Map varied voucher headers to FinGuard's canonical schema.

    Returns the six canonical columns plus the internal flag
    ``_has_explicit_time``. The latter distinguishes a date-only value from an
    actual post-midnight timestamp so date-only imports are not falsely marked
    as night-time entries.
    """

    if not isinstance(frame, pd.DataFrame):
        raise DataIngestionError("输入必须是 pandas.DataFrame")
    if frame.empty:
        raise DataIngestionError("文件中没有可读取的数据")
    if len(frame.columns) > 500:
        raise DataIngestionError("字段数量超过 500，疑似错误文件")

    lookup, unknown = _find_source_columns(frame, extra_aliases)
    missing_core = [
        field_name
        for field_name in ("date", "user", "summary")
        if field_name not in lookup and not (field_name == "date" and "time" in lookup)
    ]
    if missing_core:
        readable = {
            "date": "日期",
            "user": "制单人",
            "summary": "摘要",
        }
        raise DataIngestionError(
            "缺少必要字段："
            + "、".join(readable[item] for item in missing_core)
            + "。可调整源表字段名或在 extra_aliases 中扩展映射。"
        )
    if not any(key in lookup for key in ("debit", "credit", "amount")):
        raise DataIngestionError("缺少金额字段（借方、贷方或通用金额）")

    row_count = len(frame)
    result = pd.DataFrame(index=frame.index)

    if "voucher_id" in lookup:
        result["voucher_id"] = [
            _format_voucher_id(value, f"FG-{position:06d}")
            for position, value in enumerate(frame[lookup["voucher_id"]], start=1)
        ]
    else:
        result["voucher_id"] = [f"FG-{position:06d}" for position in range(1, row_count + 1)]

    source_date = frame[lookup["date"]] if "date" in lookup else frame[lookup["time"]]
    source_time = frame[lookup["time"]] if "time" in lookup else None
    date_text = source_date.map(_safe_str).str.strip()
    has_explicit_time = date_text.str.contains(_TIME_PATTERN, regex=True, na=False)

    # Excel date cells are frequently materialized as datetime objects at
    # midnight even when the user only entered a date. Treat zero-clock
    # datetime objects as date-only unless a separate time column or an
    # explicit clock string supplies the distinction.
    if pd.api.types.is_datetime64_any_dtype(source_date):
        has_explicit_time = source_date.dt.hour.ne(0) | source_date.dt.minute.ne(0)
    elif source_date.map(lambda value: isinstance(value, datetime_type)).any():
        has_explicit_time = source_date.map(
            lambda value: (
                isinstance(value, datetime_type)
                and (value.hour != 0 or value.minute != 0 or value.second != 0)
            )
        ).astype(bool) | date_text.str.contains(_TIME_PATTERN, regex=True, na=False)

    if source_time is not None:
        time_text = source_time.map(_safe_str).str.strip()
        has_explicit_time = has_explicit_time | time_text.ne("")
        if "date" in lookup and str(lookup["date"]) == str(lookup["time"]):
            result["date"] = date_text
        else:
            date_has_clock = date_text.str.contains(_TIME_PATTERN, regex=True, na=False)
            result["date"] = np.where(
                date_has_clock | time_text.eq(""),
                date_text,
                date_text.str.cat(time_text, sep=" "),
            )
    else:
        result["date"] = source_date

    result["user"] = frame[lookup["user"]]
    result["summary"] = frame[lookup["summary"]]

    if "amount" in lookup:
        generic_amount = frame[lookup["amount"]]
    else:
        generic_amount = None

    if "direction" in lookup and generic_amount is not None:
        direction = frame[lookup["direction"]].map(_safe_str).str.strip()
        debit_mask = direction.str.contains("借|debit|dr", case=False, regex=True)
        credit_mask = direction.str.contains("贷|credit|cr", case=False, regex=True)
        result["debit"] = generic_amount.where(debit_mask, 0)
        result["credit"] = generic_amount.where(credit_mask, 0)
        unresolved = ~(debit_mask | credit_mask)
        if unresolved.any():
            result.loc[unresolved, "debit"] = generic_amount.loc[unresolved]
    else:
        result["debit"] = frame[lookup["debit"]] if "debit" in lookup else 0
        result["credit"] = frame[lookup["credit"]] if "credit" in lookup else 0
        if generic_amount is not None and "debit" not in lookup and "credit" not in lookup:
            result["debit"] = generic_amount

    result["_has_explicit_time"] = has_explicit_time.astype(bool)
    result.attrs["unknown_columns"] = unknown
    result.attrs["source_mapping"] = lookup.copy()
    return result


def _blank_mask(series: pd.Series) -> pd.Series:
    return series.isna() | series.map(_safe_str).str.strip().eq("")


def _parse_amount(value: Any) -> float:
    """Parse a Chinese/English finance-formatted amount into a float."""

    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool):
        return float(value)

    original = unicodedata.normalize("NFKC", _safe_str(value)).strip()
    if not original:
        return np.nan
    if original.casefold() in {"-", "--", "—", "无", "null", "none", "nan", "n/a"}:
        return np.nan

    negative = original.startswith("(") and original.endswith(")")
    if negative:
        original = original[1:-1]
    original = original.replace(",", "").replace("，", "")
    original = re.sub(
        r"(?i)CNY|RMB|人民币|¥|￥|\$|元|圆|块钱|块",
        "",
        original,
    )
    multiplier = 1.0
    match = re.search(r"(万|千|百)\s*$", original)
    if match:
        multiplier = {"万": 10_000.0, "千": 1_000.0, "百": 100.0}[match.group(1)]
        original = original[: match.start()]
    original = re.sub(r"\s+", "", original)
    if original.endswith("-"):
        negative = True
        original = original[:-1]

    # Dirty exports frequently contain currency suffixes, control characters,
    # replacement glyphs, or separator fragments between digits. Keep only
    # digits, one decimal separator, and the leading sign.
    cleaned = re.sub(r"[^0-9.\-+]", "", original)
    if "-" in cleaned:
        negative = True
    cleaned = cleaned.replace("-", "").replace("+", "")
    if not cleaned or not any(character.isdigit() for character in cleaned):
        return np.nan
    parts = cleaned.split(".")
    if len(parts) > 2:
        cleaned = parts[0] + "." + "".join(parts[1:])
    elif len(parts) == 2:
        cleaned = parts[0] + "." + parts[1]
    try:
        parsed = float(cleaned) * multiplier
    except ValueError:
        return np.nan
    return -abs(parsed) if negative else parsed


def _parse_datetime_series(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    numeric_mask = series.notna() & numeric.between(20_000, 80_000)
    parsed = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    if numeric_mask.any():
        parsed.loc[numeric_mask] = pd.to_datetime(
            numeric.loc[numeric_mask],
            unit="D",
            origin="1899-12-30",
            errors="coerce",
        )

    remaining = ~numeric_mask
    if remaining.any():
        text = series.loc[remaining].map(_safe_str).str.strip()
        eight_digit = text.str.fullmatch(r"\d{8}", na=False)
        if eight_digit.any():
            parsed.loc[text.index[eight_digit]] = pd.to_datetime(
                text.loc[eight_digit],
                format="%Y%m%d",
                errors="coerce",
            )
        rest_mask = ~eight_digit
        if rest_mask.any():
            rest = text.loc[rest_mask]
            try:
                converted = pd.to_datetime(rest, errors="coerce", format="mixed")
            except (TypeError, ValueError):
                converted = pd.to_datetime(rest, errors="coerce")
            parsed.loc[rest.index] = converted
    return parsed


def _summary_row_mask(frame: pd.DataFrame) -> pd.Series:
    summary = frame["summary"].map(_safe_str).str.strip()
    voucher = frame["voucher_id"].map(_safe_str).str.strip()
    pattern_hit = summary.str.match(_SUMMARY_ROW_PATTERN, na=False)
    summary_keyword_hit = summary.str.contains("合计|汇总", regex=True, na=False)
    voucher_keyword_hit = voucher.str.contains("合计|汇总", regex=True, na=False)
    amount_blank = _blank_mask(frame["debit"]) & _blank_mask(frame["credit"])
    voucher_hit = voucher.str.fullmatch(
        r"(?:合计|小计|总计|本页合计|本期合计|本年合计|承前页|过次页)",
        flags=re.IGNORECASE,
        na=False,
    )
    # A real line whose narrative merely contains "合计" is retained unless it
    # also lacks normal accounting attributes.
    return pattern_hit | voucher_hit | voucher_keyword_hit | (summary_keyword_hit & amount_blank)


class DataIngestionService:
    """Read, map, validate, and clean voucher files."""

    CSV_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk")

    def __init__(
        self,
        config: IngestionConfig | None = None,
        extra_aliases: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.config = config or IngestionConfig()
        self.extra_aliases = extra_aliases

    def _read_payload(self, payload: bytes | bytearray) -> bytes:
        data = bytes(payload)
        if not data:
            raise DataIngestionError("上传文件为空")
        if len(data) > self.config.max_file_mb * 1024 * 1024:
            raise DataIngestionError(f"文件超过 {self.config.max_file_mb:g} MB 安全上限")
        return data

    def list_sheets(
        self,
        source: bytes | bytearray | str | Path | BinaryIO,
        filename: str | None = None,
    ) -> list[str]:
        source_name, payload = self._materialize(source, filename)
        if not source_name.casefold().endswith((".xlsx", ".xlsm")):
            return []
        try:
            with pd.ExcelFile(io.BytesIO(payload), engine="openpyxl") as workbook:
                return list(workbook.sheet_names)
        except Exception as exc:
            raise DataIngestionError(f"无法读取 Excel 工作表列表：{exc}") from exc

    def _materialize(
        self,
        source: bytes | bytearray | str | Path | BinaryIO,
        filename: str | None,
    ) -> tuple[str, bytes]:
        if isinstance(source, (str, Path)):
            path = Path(source)
            if not path.exists():
                raise DataIngestionError(f"文件不存在：{path}")
            if path.stat().st_size > self.config.max_file_mb * 1024 * 1024:
                raise DataIngestionError(f"文件超过 {self.config.max_file_mb:g} MB 安全上限")
            return filename or path.name, path.read_bytes()
        if isinstance(source, (bytes, bytearray)):
            if not filename:
                raise DataIngestionError("读取字节流时必须提供 filename")
            return filename, self._read_payload(source)
        if hasattr(source, "read"):
            payload = source.read()
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
            return filename or str(getattr(source, "name", "uploaded")).split("/")[
                -1
            ], self._read_payload(payload)
        raise DataIngestionError("不支持的文件来源类型")

    def _read_frame(
        self,
        payload: bytes,
        filename: str,
        sheet_name: str | int | None = None,
    ) -> pd.DataFrame:
        lower_name = filename.casefold()
        try:
            if lower_name.endswith(".csv"):
                last_error: Exception | None = None
                for encoding in self.CSV_ENCODINGS:
                    try:
                        return pd.read_csv(
                            io.BytesIO(payload),
                            encoding=encoding,
                            sep=None,
                            engine="python",
                            dtype=object,
                            skip_blank_lines=False,
                        )
                    except (UnicodeDecodeError, pd.errors.ParserError) as exc:
                        last_error = exc
                raise DataIngestionError(f"CSV 编码或分隔符无法识别：{last_error}")
            if lower_name.endswith((".xlsx", ".xlsm")):
                return pd.read_excel(
                    io.BytesIO(payload),
                    sheet_name=0 if sheet_name is None else sheet_name,
                    engine="openpyxl",
                    dtype=object,
                )
            if lower_name.endswith(".xls"):
                raise DataIngestionError(
                    "出于宏与解析安全考虑，不支持旧版 .xls；请另存为 .xlsx 后上传"
                )
            raise DataIngestionError("仅支持 .csv、.xlsx 或 .xlsm 文件")
        except DataIngestionError:
            raise
        except Exception as exc:
            raise DataIngestionError(f"文件读取失败：{exc}") from exc

    def load_and_clean(
        self,
        source: bytes | bytearray | str | Path | BinaryIO,
        filename: str | None = None,
        sheet_name: str | int | None = None,
    ) -> IngestionResult:
        source_name, payload = self._materialize(source, filename)
        digest = hashlib.sha256(payload).hexdigest()
        raw = self._read_frame(payload, source_name, sheet_name)
        if raw.empty:
            raise DataIngestionError("文件中没有数据行")
        if len(raw) > self.config.max_rows:
            raise DataIngestionError(
                f"数据行数 {len(raw):,} 超过 {self.config.max_rows:,} 行处理上限"
            )

        renamed = rename_columns(raw, self.extra_aliases)
        original_row_numbers = pd.Series(
            [
                int(index) + 2 if isinstance(index, (int, np.integer)) else str(index)
                for index in renamed.index
            ],
            index=renamed.index,
            name="source_row",
        )
        renamed["_source_row"] = original_row_numbers

        report: dict[str, Any] = {
            "source_file": source_name,
            "source_sha256": digest,
            "raw_rows": len(raw),
            "summary_rows_removed": 0,
            "blank_rows_removed": 0,
            "invalid_dates_removed": 0,
            "invalid_amounts_removed": 0,
            "zero_amount_removed": 0,
            "clean_rows": 0,
            "unknown_columns": renamed.attrs.get("unknown_columns", []),
        }
        warning_messages: list[str] = []

        # voucher_id may have been auto-generated in rename_columns(), so it
        # cannot participate in detecting an otherwise blank source row.
        core_present = ["date", "user", "summary", "debit", "credit"]
        blank = renamed[core_present].apply(_blank_mask).all(axis=1)
        report["blank_rows_removed"] = int(blank.sum())
        working = renamed.loc[~blank].copy()

        summary_rows = _summary_row_mask(working)
        report["summary_rows_removed"] = int(summary_rows.sum())
        summary_rejected = working.loc[summary_rows].copy()
        summary_rejected["_reject_reason"] = "汇总/合计行"
        working = working.loc[~summary_rows].copy()

        cleaned = working.copy()
        cleaned["date"] = _parse_datetime_series(cleaned["date"])
        invalid_date = cleaned["date"].isna()
        invalid_date_rejected = cleaned.loc[invalid_date].copy()
        invalid_date_rejected["_reject_reason"] = "日期无法识别"
        report["invalid_dates_removed"] = int(invalid_date.sum())
        cleaned = cleaned.loc[~invalid_date].copy()

        amount_errors = pd.Series(False, index=cleaned.index)
        for column in ("debit", "credit"):
            original_nonblank = ~_blank_mask(cleaned[column])
            parsed = cleaned[column].map(_parse_amount)
            amount_errors |= original_nonblank & parsed.isna()
            cleaned[column] = pd.to_numeric(parsed, errors="coerce").fillna(0.0)

        invalid_amount_rejected = cleaned.loc[amount_errors].copy()
        invalid_amount_rejected["_reject_reason"] = "金额无法识别"
        report["invalid_amounts_removed"] = int(amount_errors.sum())
        cleaned = cleaned.loc[~amount_errors].copy()

        cleaned["amount"] = np.maximum(
            cleaned["debit"].abs().to_numpy(dtype=float),
            cleaned["credit"].abs().to_numpy(dtype=float),
        )
        cleaned["is_refund"] = cleaned["debit"].lt(0) | cleaned["credit"].lt(0)
        cleaned["transaction_type"] = np.where(
            cleaned["is_refund"],
            "退款/红字冲销",
            "常规",
        )
        zero_amount = cleaned["amount"].le(0)
        zero_rejected = cleaned.loc[zero_amount].copy()
        if not zero_rejected.empty:
            zero_rejected["_reject_reason"] = "金额为零"
        report["zero_amount_removed"] = int(zero_amount.sum())
        cleaned = cleaned.loc[~zero_amount].copy()

        cleaned["user"] = (
            cleaned["user"].map(_safe_str).str.strip().replace("", self.config.default_missing_user)
        )
        cleaned["summary"] = (
            cleaned["summary"]
            .map(_safe_str)
            .str.replace(_CONTROL_CHARS, "", regex=True)
            .str.strip()
            .replace("", self.config.default_missing_summary)
        )
        cleaned["voucher_id"] = cleaned["voucher_id"].map(_safe_str).str.strip()

        if cleaned["voucher_id"].duplicated().any():
            duplicate_count = int(cleaned["voucher_id"].duplicated(keep=False).sum())
            warning_messages.append(
                f"发现 {duplicate_count} 行重复凭证号；系统保留原始记录并参与分析。"
            )
        if invalid_date_rejected.empty and invalid_amount_rejected.empty:
            report["quality"] = "通过"
        else:
            report["quality"] = "部分隔离"
        warning_messages.extend(
            f"未识别字段被忽略：{name}" for name in report["unknown_columns"][:10]
        )

        rejected_parts = [
            part
            for part in (
                summary_rejected,
                invalid_date_rejected,
                invalid_amount_rejected,
                zero_rejected,
            )
            if not part.empty
        ]
        normalized_rejected: list[pd.DataFrame] = []
        for part in rejected_parts:
            safe_part = part.astype(object)
            for column in safe_part.columns:
                safe_part[column] = safe_part[column].map(
                    lambda value: "" if pd.isna(value) else str(value)
                )
            normalized_rejected.append(safe_part)
        rejected = (
            pd.concat(normalized_rejected, ignore_index=False)
            if normalized_rejected
            else pd.DataFrame(columns=[*CANONICAL_COLUMNS, "_reject_reason"])
        )
        if rejected.empty:
            rejected = pd.DataFrame(columns=[*CANONICAL_COLUMNS, "_reject_reason"])

        clean_columns = [
            *CANONICAL_COLUMNS,
            "amount",
            "is_refund",
            "transaction_type",
            "_has_explicit_time",
            "_source_row",
        ]
        cleaned = cleaned[clean_columns].sort_values(["date", "voucher_id"]).reset_index(drop=True)
        report["clean_rows"] = len(cleaned)
        report["refund_count"] = int(cleaned["is_refund"].sum())
        report["refund_amount"] = float(cleaned.loc[cleaned["is_refund"], "amount"].sum())
        report["total_amount"] = float(cleaned.loc[~cleaned["is_refund"], "amount"].sum())

        if cleaned.empty:
            raise DataIngestionError(
                "清洗后没有有效凭证。请检查日期与金额列；隔离原因可在原始字段中核对。"
            )
        return IngestionResult(
            clean_data=cleaned,
            rejected_data=rejected,
            report=report,
            warnings=warning_messages,
        )

    def load_path(self, path: str | Path, sheet_name: str | int | None = None) -> IngestionResult:
        return self.load_and_clean(path, sheet_name=sheet_name)
