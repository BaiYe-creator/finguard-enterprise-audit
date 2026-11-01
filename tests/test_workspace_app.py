from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from streamlit.testing.v1 import AppTest

from finguard.workspace_store import WorkspaceStore


def _button(app: AppTest, label: str):
    matches = [button for button in app.button if button.label == label]
    assert matches, f"Button not found: {label}; available={[item.label for item in app.button]}"
    return matches[0]


def test_workspace_ui_creates_project_uploads_evidence_and_restores_project(monkeypatch) -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        database = Path(directory) / "workspace.db"
        monkeypatch.setenv("FINGUARD_DB_PATH", str(database))
        app_path = Path(__file__).resolve().parents[1] / "app.py"

        app = AppTest.from_file(app_path, default_timeout=90).run()
        _button(app, "7. 审计项目与证据链").click().run()

        assert not app.exception
        assert app.session_state["current_page"] == "workspace"
        app.text_input[0].set_value("2026 年费用报销专项审计")
        app.text_input[1].set_value("2026-10")
        app.text_area[0].set_value("核查拆分报销和敏感时间录入")
        app.run()
        _button(app, "创建审计项目").click().run()

        assert not app.exception
        case_id = app.session_state["active_case_id"]
        assert case_id
        store = WorkspaceStore(database)
        saved_case = store.get_case(case_id)
        assert saved_case is not None
        assert saved_case["title"] == "2026 年费用报销专项审计"

        uploader = next(
            item for item in app.get("file_uploader") if item.label == "上传证据或工作底稿"
        )
        uploader.set_value(("发票证据.pdf", b"%PDF-evidence", "application/pdf")).run()
        _button(app, "保存上传的证据").click().run()

        assert not app.exception
        artifacts = store.list_artifacts(case_id)
        assert [item["filename"] for item in artifacts] == ["发票证据.pdf"]
        assert any(log["action"] == "artifact.added" for log in store.list_logs(case_id=case_id))

        _button(app, "← 返回主页").click().run()
        _button(app, "1. 财务审计").click().run()
        _button(app, "载入合成演示数据").click().run()
        _button(app, "归档当前审计成果").click().run()

        artifact_kinds = {item["kind"] for item in store.list_artifacts(case_id)}
        assert {"evidence", "source_voucher", "risk_csv", "audit_excel"} <= artifact_kinds
        assert any(log["action"] == "analysis.archived" for log in store.list_logs(case_id=case_id))

        _button(app, "← 返回主页").click().run()
        _button(app, "7. 审计项目与证据链").click().run()
        assert not app.exception
        assert app.session_state["active_case_id"] == case_id
        assert store.get_case(case_id) is not None


def test_new_session_can_restore_persisted_project(monkeypatch) -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        database = Path(directory) / "workspace.db"
        store = WorkspaceStore(database)
        case = store.create_case(title="采购付款审计", period="2026-12")
        monkeypatch.setenv("FINGUARD_DB_PATH", str(database))

        app = AppTest.from_file(
            Path(__file__).resolve().parents[1] / "app.py",
            default_timeout=90,
        ).run()
        _button(app, "7. 审计项目与证据链").click().run()

        assert not app.exception
        assert app.session_state["active_case_id"] == case["id"]
        titles = [item["title"] for item in WorkspaceStore(database).list_cases()]
        assert titles == ["采购付款审计"]
