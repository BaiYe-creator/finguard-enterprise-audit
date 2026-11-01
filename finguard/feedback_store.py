"""Persistent manual-review feedback used for conservative risk adjustments."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_FEEDBACK_PATH = Path(__file__).resolve().parents[1] / "data" / "review_feedback.json"


class FeedbackStore:
    """Store verified vouchers and match later records by summary and amount."""

    def __init__(
        self,
        path: str | Path = DEFAULT_FEEDBACK_PATH,
        *,
        similarity_threshold: float = 0.75,
        amount_tolerance: float = 0.10,
        max_entries: int = 5_000,
    ) -> None:
        self.path = Path(path)
        self.similarity_threshold = similarity_threshold
        self.amount_tolerance = amount_tolerance
        self.max_entries = max_entries
        self._lock = threading.RLock()

    @staticmethod
    def normalize_summary(summary: Any) -> str:
        text = unicodedata.normalize("NFKC", str(summary or "")).casefold()
        return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)

    def load(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.path.exists():
                return []
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                return []
            entries = payload.get("entries", []) if isinstance(payload, dict) else []
            return [entry for entry in entries if isinstance(entry, dict)]

    def _write(self, entries: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"version": 1, "entries": entries[-self.max_entries :]}
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def add(
        self,
        *,
        voucher_id: Any,
        summary: Any,
        amount: Any,
        risk_features: Any = "",
        user: Any = "",
    ) -> dict[str, Any]:
        normalized = self.normalize_summary(summary)
        try:
            amount_value = round(float(amount), 2)
        except (TypeError, ValueError):
            amount_value = 0.0
        identity = hashlib.sha256(f"{normalized}|{amount_value:.2f}".encode()).hexdigest()[:24]
        entry = {
            "id": identity,
            "voucher_id": str(voucher_id),
            "summary": str(summary),
            "normalized_summary": normalized,
            "amount": amount_value,
            "user": str(user),
            "risk_features": str(risk_features),
            "verified_at": datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
            entries = self.load()
            entries = [item for item in entries if item.get("id") != identity]
            entries.append(entry)
            self._write(entries)
        return entry

    def find_match(
        self,
        *,
        summary: Any,
        amount: Any,
    ) -> dict[str, Any] | None:
        normalized = self.normalize_summary(summary)
        try:
            amount_value = abs(float(amount))
        except (TypeError, ValueError):
            return None

        best: tuple[float, dict[str, Any]] | None = None
        for entry in reversed(self.load()):
            try:
                saved_amount = abs(float(entry.get("amount", 0)))
            except (TypeError, ValueError):
                continue
            denominator = max(amount_value, saved_amount, 1.0)
            amount_delta = abs(amount_value - saved_amount) / denominator
            if amount_delta > self.amount_tolerance:
                continue
            saved_summary = str(entry.get("normalized_summary") or "")
            similarity = SequenceMatcher(None, normalized, saved_summary).ratio()
            if similarity >= self.similarity_threshold and (best is None or similarity > best[0]):
                best = (similarity, entry)
        return best[1] if best else None

    def clear(self) -> None:
        with self._lock:
            if self.path.exists():
                self.path.unlink()

    def count(self) -> int:
        return len(self.load())

    def to_frame(self) -> pd.DataFrame:
        entries = self.load()
        if not entries:
            return pd.DataFrame(
                columns=[
                    "voucher_id",
                    "summary",
                    "amount",
                    "user",
                    "verified_at",
                ]
            )
        return pd.DataFrame(entries)
