from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from finguard.ai_governance import AIGovernanceService, redact_sensitive_text
from finguard.config import RiskConfig, risk_config_from_profile, risk_config_profile
from finguard.workspace_store import WorkspaceStore


def test_ai_redaction_and_usage_accounting() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        store = WorkspaceStore(Path(directory) / "governance.db")
        governance = AIGovernanceService(
            store,
            daily_call_limit=2,
            daily_token_limit=10_000,
            cost_per_1k_tokens=0.01,
        )
        text = "联系人 13800138000，邮箱 audit@example.com，身份证 110101199001011234。"
        redacted = governance.redact(text)
        assert "13800138000" not in redacted.text
        assert "audit@example.com" not in redacted.text
        assert "110101199001011234" not in redacted.text
        assert redacted.replacement_count == 3

        governance.record(
            organization_id="local-organization",
            user_id="local-admin",
            module="test",
            model="deepseek-chat",
            input_text=redacted.text,
            redaction_count=redacted.replacement_count,
            status="success",
        )
        summary = governance.summary("local-organization")
        assert summary["calls"] == 1
        assert summary["redactions"] == 3
        assert summary["estimated_tokens"] >= 1
        assert summary["estimated_cost"] > 0


def test_rule_profiles_are_versioned_and_activated() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        store = WorkspaceStore(Path(directory) / "governance.db")
        config = RiskConfig(high_risk_threshold=70.0, ai_trigger_threshold=70.0)
        first = store.create_rule_profile(
            organization_id="local-organization",
            user_id="local-admin",
            name="严格规则",
            config=risk_config_profile(config),
            activate=True,
        )
        second = store.create_rule_profile(
            organization_id="local-organization",
            user_id="local-admin",
            name="宽松规则",
            config=risk_config_profile(RiskConfig()),
        )
        assert first["version"] == 1
        assert second["version"] == 2
        active = store.get_active_rule_profile("local-organization")
        assert active is not None
        assert active["id"] == first["id"]

        store.activate_rule_profile(
            organization_id="local-organization",
            profile_id=second["id"],
            user_id="local-admin",
        )
        active = store.get_active_rule_profile("local-organization")
        assert active is not None
        assert active["id"] == second["id"]
        restored = risk_config_from_profile(risk_config_profile(config))
        assert restored.high_risk_threshold == 70.0
        assert restored.ai_trigger_threshold == 70.0


def test_backup_integrity_and_restore_round_trip() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        database = Path(directory) / "governance.db"
        store = WorkspaceStore(database)
        first = store.create_case(title="备份前项目", period="2026-10")
        backup = store.create_backup_bytes()
        assert backup.startswith(b"SQLite format 3")
        assert store.database_integrity()["valid"]

        store.create_case(title="备份后项目", period="2026-11")
        assert len(store.list_cases("local-organization")) == 2

        snapshot = store.restore_backup_bytes(backup, user_id="local-admin")
        assert snapshot.exists()
        cases = store.list_cases("local-organization")
        assert [case["id"] for case in cases] == [first["id"]]


def test_audit_hash_chain_detects_tampering() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        database = Path(directory) / "governance.db"
        store = WorkspaceStore(database)
        for index in range(3):
            store.log_event(
                action=f"test.action.{index}",
                entity_type="test",
                entity_id=str(index),
                details={"index": index},
            )
        chain = store.verify_audit_chain("local-organization")
        assert chain["valid"]
        assert chain["checked"] == 3

        connection = sqlite3.connect(database)
        try:
            connection.execute("UPDATE audit_logs SET details_json = '{\"index\":99}' WHERE id = 2")
            connection.commit()
        finally:
            connection.close()
        chain = store.verify_audit_chain("local-organization")
        assert not chain["valid"]
        assert chain["broken_id"] == 2


def test_redaction_does_not_change_amounts_dates_or_business_text() -> None:
    text = "2026-10-31 张三采购钢材40吨，金额400000元。"
    assert redact_sensitive_text(text).text == text
