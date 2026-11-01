from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from finguard.workspace_store import WorkspaceStore


def test_project_evidence_and_logs_persist_across_store_instances() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        database = Path(directory) / "workspace.db"
        store = WorkspaceStore(database)
        case = store.create_case(
            title="费用报销专项审计",
            period="2026-10",
            description="检查拆分报销和周末录入",
        )

        reopened = WorkspaceStore(database)
        saved_case = reopened.get_case(case["id"])
        assert saved_case is not None
        assert saved_case["title"] == "费用报销专项审计"
        assert saved_case["status"] == "draft"

        artifact = reopened.add_artifact(
            case_id=case["id"],
            kind="evidence",
            filename="发票.pdf",
            content=b"%PDF-test-evidence",
            content_type="application/pdf",
        )
        same_artifact = reopened.add_artifact(
            case_id=case["id"],
            kind="evidence",
            filename="重复上传.pdf",
            content=b"%PDF-test-evidence",
            content_type="application/pdf",
        )
        package = reopened.get_artifact(artifact["id"])

        assert same_artifact["id"] == artifact["id"]
        assert package is not None
        assert package["content"] == b"%PDF-test-evidence"
        assert package["sha256"] == artifact["sha256"]

        updated = reopened.update_case(case["id"], status="active", description="进入现场核查")
        assert updated["status"] == "active"
        assert updated["description"] == "进入现场核查"

        logs = reopened.list_logs(case_id=case["id"])
        actions = {log["action"] for log in logs}
        assert {"case.created", "artifact.added", "case.updated"} <= actions
        assert reopened.list_cases()[0]["artifact_count"] == 1


def test_workspace_rejects_empty_files_and_unknown_cases() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        store = WorkspaceStore(Path(directory) / "workspace.db")
        case = store.create_case(title="存货审计", period="2026-11")

        try:
            store.add_artifact(
                case_id=case["id"],
                kind="evidence",
                filename="empty.txt",
                content=b"",
            )
        except ValueError as exc:
            assert "不能为空" in str(exc)
        else:
            raise AssertionError("empty artifact should be rejected")

        try:
            store.add_artifact(
                case_id="missing-case",
                kind="evidence",
                filename="test.txt",
                content=b"test",
            )
        except RuntimeError as exc:
            assert "不存在" in str(exc)
        else:
            raise AssertionError("unknown case should be rejected")
