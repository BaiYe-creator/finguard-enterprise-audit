from __future__ import annotations

import io
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
from pptx import Presentation
from streamlit.elements.widgets.data_editor import DataEditorState
from streamlit.testing.v1 import AppTest

from finguard.feedback_store import FeedbackStore


def _button(app: AppTest, label: str):
    matches = [button for button in app.button if button.label == label]
    assert matches, f"Button not found: {label}; available={[item.label for item in app.button]}"
    return matches[0]


def test_page_navigation_data_persistence_and_no_stop() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    source = app_path.read_text(encoding="utf-8")
    simulator_path = Path(__file__).resolve().parents[1] / "finguard" / "simulator_page.py"
    simulator_source = simulator_path.read_text(encoding="utf-8")
    assert simulator_source.count("st.stop(") == 2
    assert "st.stop(" not in source

    app = AppTest.from_file(app_path, default_timeout=90).run()
    assert not app.exception
    assert app.session_state["current_page"] == "home"
    assert len(app.get("file_uploader")) == 0
    assert len(app.error) == 0

    _button(app, "2. 生成高管 PPT").click().run()
    assert not app.exception
    assert any("上传凭证" in error.value for error in app.error)
    assert app.session_state["current_page"] == "ppt"

    _button(app, "← 返回主页").click().run()
    _button(app, "1. 财务审计").click().run()
    assert not app.exception
    assert app.session_state["current_page"] == "finance"
    assert len(app.get("file_uploader")) == 1

    _button(app, "载入合成演示数据").click().run()
    assert not app.exception
    assert app.session_state["analysis"] is not None
    assert len(app.get("plotly_chart")) == 2

    _button(app, "← 返回主页").click().run()
    assert not app.exception
    assert app.session_state["current_page"] == "home"
    assert app.session_state["analysis"] is not None

    _button(app, "2. 生成高管 PPT").click().run()
    assert not app.exception
    assert app.session_state["current_page"] == "ppt"
    _button(app, "一键生成高管PPT").click().run()
    assert not app.exception
    assert app.session_state["ppt_bytes"]
    downloads = [item.label for item in app.get("download_button")]
    assert any(
        "FinGuard高管简报_极简版_" in label and label.endswith(".pptx") for label in downloads
    )


def test_temporary_api_key_persists_in_session_and_never_enters_widgets_on_home() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90).run()
    assert len(app.text_input) == 0

    _button(app, "API 密钥配置").click().run()
    assert app.session_state["current_page"] == "settings"
    assert not app.exception
    assert len(app.text_input) == 1
    assert app.text_input[0].label == "DeepSeek API Key"

    app.text_input[0].set_value("sk-session-test").run()
    _button(app, "保存到当前会话").click().run()
    assert app.session_state["deepseek_api_key"] == "sk-session-test"
    assert not app.exception

    _button(app, "← 返回主页").click().run()
    _button(app, "API 密钥配置").click().run()
    assert app.session_state["deepseek_api_key"] == "sk-session-test"
    assert app.text_input[0].value == "sk-session-test"

    _button(app, "清除临时密钥").click().run()
    assert app.session_state["deepseek_api_key"] == ""


def test_home_background_magnifier_stays_below_content_and_buttons_are_frosted() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90).run()

    assert not app.exception
    magnifier_markup = [
        markdown.value for markdown in app.markdown if "fg-magnifier-image" in markdown.value
    ]
    assert len(magnifier_markup) == 1
    markup = magnifier_markup[0]
    assert "data:image/png;base64," in markup
    assert "position: fixed" in markup
    assert "z-index: 0" in markup
    assert "pointer-events: none" in markup
    assert "height: min(118vh, 1080px)" in markup
    assert "opacity: 0.12" in markup
    assert "align-items: flex-start" in markup
    assert "justify-content: flex-start" in markup
    assert "translate3d(-2vw, -14vh" in markup
    assert "rgba(23,25,28" not in markup
    assert "backdrop-filter: blur(14px)" in markup
    assert "z-index: 2" in markup
    assert ".st-key-open_workspace button:hover" in markup
    assert ".st-key-workspace_card button:hover" in markup
    assert "box-shadow: 0 17px 36px rgba(35,45,56,.1) !important" in markup
    assert "transform: translateY(-3px)" not in markup
    assert all(
        label in [button.label for button in app.button]
        for label in (
            "1. 财务审计",
            "2. 生成高管 PPT",
            "3. 业财转换引擎",
            "4. 产能推演沙盘",
            "5. 经营模拟器",
            "6. 智能化引擎",
            "7. 审计项目与证据链",
        )
    )


def test_capacity_sandbox_uses_isolated_v04_state() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90).run()
    assert app.session_state["analysis"] is None
    assert app.session_state["biz_history"] == []

    _button(app, "4. 产能推演沙盘").click().run()
    assert app.session_state["current_page"] == "capacity"
    assert not app.exception
    assert [row["产线名称"] for row in app.session_state["v04_capacity_assets"]] == [
        "割草机产线 A",
        "割草机产线 B",
    ]

    app.session_state["v04_capacity_order_quantity"] = 1000
    app.session_state["v04_capacity_required_days"] = 3
    _button(app, "计算产能").click().run()

    result = app.session_state["v04_capacity_result"]
    assert result is not None
    assert not result["sufficient"]
    assert result["bottleneck_line"] == "割草机产线 A"
    assert result["required_completion_days"] == 17
    assert any("严重超出产能极限" in error.value for error in app.error)
    assert any("产能瓶颈提示" in warning.value for warning in app.warning)
    assert app.session_state["analysis"] is None
    assert app.session_state["biz_history"] == []

    _button(app, "← 返回主页").click().run()
    _button(app, "4. 产能推演沙盘").click().run()
    assert app.session_state["v04_capacity_result"] == result


def test_capacity_asset_csv_upload_drives_manual_calculation() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90).run()
    _button(app, "4. 产能推演沙盘").click().run()

    csv_bytes = (
        "产线名称,产线数量,单台日产能\n开料机,2,500\n封边机,1,600\n喷漆线,1,200\n组装台,2,150\n"
    ).encode("utf-8-sig")
    uploader = app.get("file_uploader")[0]
    uploader.set_value(("固定资产台账.csv", csv_bytes, "text/csv")).run()

    assert not app.exception
    assert [row["产线名称"] for row in app.session_state["v04_capacity_assets"]] == [
        "开料机",
        "封边机",
        "喷漆线",
        "组装台",
    ]
    assert app.session_state["v04_capacity_assets"][0]["日总产能"] == 1000.0

    app.session_state["v04_capacity_order_quantity"] = 100000
    app.session_state["v04_capacity_required_days"] = 30
    _button(app, "计算产能").click().run()

    result = app.session_state["v04_capacity_result"]
    assert result["bottleneck_line"] == "喷漆线"
    assert result["bottleneck_capacity"] == 200.0
    assert result["total_limit_capacity"] == 6000.0
    assert result["required_completion_days"] == 500
    assert any("需至少 500 天" in error.value for error in app.error)
    assert app.session_state["analysis"] is None
    assert app.session_state["biz_history"] == []


def test_business_simulator_handles_zero_margin_loss_and_capacity_linkage() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90).run()
    _button(app, "5. 经营模拟器").click().run()

    assert app.session_state["current_page"] == "simulator"
    assert not app.exception
    assert any("推演通过" in message.value for message in app.success)
    assert app.session_state["v05_order_qty"] == 1000
    assert app.session_state["v05_unit_price"] == 100.0

    app.session_state["v05_unit_cost"] = 95.0
    app.run()
    assert any("利润预警" in message.value for message in app.warning)

    app.session_state["v05_order_qty"] = 0
    app.run()
    assert not app.exception
    assert any(error.value == "输入参数不合法，数量与单价不可为 0" for error in app.error)

    app.session_state["v05_order_qty"] = 1000
    app.session_state["v05_unit_price"] = 100.0
    app.session_state["v05_unit_cost"] = 120.0
    app.run()
    assert not app.exception
    assert any("推演否决" in error.value for error in app.error)

    app.session_state["v05_unit_cost"] = 70.0
    app.session_state["v04_capacity_result"] = {"bottleneck_capacity": 10.0}
    app.run()
    assert any(message.value == "注意：交付周期超 30 天，资金占用极大。" for message in app.info)
    assert app.session_state["analysis"] is None
    assert app.session_state["biz_history"] == []


def test_business_simulator_slider_and_number_input_are_bidirectionally_synced() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90).run()
    _button(app, "5. 经营模拟器").click().run()

    assert len(app.slider) == 3
    assert len(app.number_input) == 3

    app.slider[0].set_value(2400).run()
    assert app.session_state["v05_order_qty"] == 2400
    assert app.session_state["v05_order_qty_input"] == 2400

    app.number_input[1].set_value(123.45).run()
    assert app.session_state["v05_unit_price"] == 123.45
    assert app.session_state["v05_unit_price_input"] == 123.45

    result = app.session_state["v05_last_sim_result"]
    assert result["order_quantity"] == 2400
    assert result["unit_price"] == 123.45
    assert result["revenue"] == 296280.0


def test_intelligent_risk_page_isolates_anomalies_and_handles_missing_ai_key() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90)
    app.query_params["load"] = "real_sap"
    app.run()
    _button(app, "← 返回主页").click().run()
    _button(app, "6. 智能化引擎").click().run()

    assert app.session_state["current_page"] == "mlrisk"
    assert not app.exception
    anomaly_frame = app.session_state["v05_ml_anomaly_frame"]
    assert anomaly_frame is not None
    assert anomaly_frame["是否异常"].any()
    assert set(anomaly_frame["预测值"].unique()).issubset({-1, 1})

    app.text_area[0].set_value("管理层表示经营完全稳定，所有风险完全可控。").run()
    _button(app, "分析财报摘要").click().run()

    assert not app.exception
    narrative = app.session_state["v05_ml_nlp_result"]
    assert narrative["status"] == "disabled"
    assert narrative["content"] == "未配置 AI 密钥"
    assert len(app.error) == 0


def test_real_dirty_csv_can_be_uploaded_and_analyzed() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    csv_path = Path(__file__).resolve().parents[1] / "sample_data" / "dirty_vouchers_100.csv"
    app = AppTest.from_file(app_path, default_timeout=90).run()
    _button(app, "1. 财务审计").click().run()

    uploader = app.get("file_uploader")[0]
    uploader.set_value(("dirty_vouchers_100.csv", csv_path.read_bytes(), "text/csv"))
    uploader.run()

    assert not app.exception
    analysis = app.session_state["analysis"]
    assert analysis is not None
    assert analysis["ingestion_report"]["raw_rows"] == 104
    assert analysis["ingestion_report"]["summary_rows_removed"] == 4
    assert analysis["ingestion_report"]["refund_count"] == 1
    assert analysis["metrics"]["analyzed_count"] == 98
    assert int(analysis["data"]["is_split_reimbursement"].sum()) == 6
    assert len(app.error) == 0


def test_dirty_dataset_deep_link_opens_audit_page() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90)
    app.query_params["load"] = "dirty_100"
    app.run()

    assert not app.exception
    assert app.session_state["current_page"] == "finance"
    analysis = app.session_state["analysis"]
    assert analysis is not None
    assert analysis["ingestion_report"]["raw_rows"] == 104
    assert analysis["metrics"]["analyzed_count"] == 98
    assert int(analysis["data"]["is_split_reimbursement"].sum()) == 6


def test_real_sap_deep_link_opens_audit_page() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90)
    app.query_params["load"] = "real_sap"
    app.run()

    assert not app.exception
    assert app.session_state["current_page"] == "finance"
    analysis = app.session_state["analysis"]
    assert analysis is not None
    assert analysis["ingestion_report"]["raw_rows"] == 67
    assert analysis["metrics"]["analyzed_count"] == 67


def test_feedback_button_persists_verified_voucher(monkeypatch) -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        feedback_path = Path(directory) / "feedback.json"
        monkeypatch.setenv("FINGUARD_FEEDBACK_PATH", str(feedback_path))
        app_path = Path(__file__).resolve().parents[1] / "app.py"
        app = AppTest.from_file(app_path, default_timeout=90)
        app.query_params["load"] = "real_sap"
        app.run()

        feedback_buttons = [button for button in app.button if button.label == "标记为已核实无误"]
        assert feedback_buttons
        feedback_buttons[0].click().run()

        assert not app.exception
        store = FeedbackStore(feedback_path)
        assert store.count() == 1
        assert app.session_state["analysis"]["data"]["is_human_verified_match"].any()


def test_business_page_keeps_history_across_navigation() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90).run()
    _button(app, "3. 业财转换引擎").click().run()
    assert app.session_state["current_page"] == "biz"
    assert not app.exception

    app.text_area[0].set_value(
        "采购钢材10吨，金额100000元，银行付款\n开发一个电商网站，金额80000元"
    ).run()
    _button(app, "生成标准会计分录").click().run()
    assert not app.exception

    history = app.session_state["biz_history"]
    assert len(history) == 4
    assert any("3 笔分录" in markdown.value for markdown in app.markdown)
    assert any(item["status"] == "generated" for item in history)
    assert any(item["status"] == "rejected" for item in history)
    assert len(app.get("download_button")) == 2

    _button(app, "← 返回主页").click().run()
    _button(app, "3. 业财转换引擎").click().run()
    assert app.session_state["current_page"] == "biz"
    assert app.session_state["biz_history"] == history


def test_business_csv_upload_fills_text_area_and_allows_manual_edit() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90).run()
    _button(app, "3. 业财转换引擎").click().run()

    csv_bytes = (
        "凭证号,业务描述,金额\n"
        "B001,采购钢材10吨，金额11300元，先欠着,11300\n"
        "B002,员工报销打车费500元，银行付款,500\n"
    ).encode("utf-8-sig")
    uploader = app.get("file_uploader")[0]
    uploader.set_value(("业务导入.csv", csv_bytes, "text/csv")).run()

    assert not app.exception
    imported = app.text_area[0].value
    assert "采购钢材10吨" in imported
    assert "员工报销打车费" in imported
    assert "凭证号" not in imported

    manually_edited = imported + "\n办公室订了20桶纯净水，花了400块"
    app.text_area[0].set_value(manually_edited).run()
    assert app.text_area[0].value == manually_edited

    _button(app, "生成标准会计分录").click().run()
    assert not app.exception
    assert app.session_state["biz_history"]


def test_business_xlsx_upload_selects_business_description_column() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90).run()
    _button(app, "3. 业财转换引擎").click().run()

    output = io.BytesIO()
    pd.DataFrame(
        {
            "序号": [1, 2],
            "业务内容": [
                "销售产品，金额113000元，银行收款",
                "计提设备折旧，金额21000元",
            ],
        }
    ).to_excel(output, index=False)

    uploader = app.get("file_uploader")[0]
    uploader.set_value(
        (
            "业务导入.xlsx",
            output.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    ).run()

    assert not app.exception
    imported = app.text_area[0].value
    assert imported.splitlines() == [
        "销售产品，金额113000元，银行收款",
        "计提设备折旧，金额21000元",
    ]
    assert "序号" not in imported


def test_hitl_entries_only_update_reports_and_downloads_after_balanced_save() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90).run()
    _button(app, "3. 业财转换引擎").click().run()
    app.text_area[0].set_value("销售产品，金额11300元，银行收款").run()
    _button(app, "生成标准会计分录").click().run()
    assert not app.exception

    saved_before = app.session_state["v04_editable_entries"]
    assert len(saved_before) == 3
    assert len({entry["entry_id"] for entry in saved_before}) == 3
    initial_revenue = (
        app.session_state["v04_hitl_reports"]["income_statement"]
        .loc[
            lambda frame: frame["项目"] == "一、营业收入",
            "金额",
        ]
        .iloc[0]
    )
    assert initial_revenue == 10000.0

    revision = app.session_state["v04_hitl_revision"]
    editor_key = f"v04_entry_editor_{revision}"
    changed_rows = {
        0: {"金额": 12000.0},
        1: {"金额": 10700.0},
    }
    app.session_state[editor_key] = DataEditorState(edited_rows=changed_rows)
    app.run()
    assert app.session_state["v04_editable_entries"] == saved_before

    app.session_state[editor_key] = DataEditorState(edited_rows=changed_rows)
    _button(app, "保存修改并重算").click().run()
    assert not app.exception
    saved_after = app.session_state["v04_editable_entries"]
    assert [entry["amount"] for entry in saved_after] == [12000.0, 10700.0, 1300.0]
    saved_revenue = (
        app.session_state["v04_hitl_reports"]["income_statement"]
        .loc[
            lambda frame: frame["项目"] == "一、营业收入",
            "金额",
        ]
        .iloc[0]
    )
    assert saved_revenue == 10700.0
    assert app.session_state["v04_hitl_balance_sheet_xlsx"].startswith(b"PK")
    assert app.session_state["v04_hitl_income_statement_xlsx"].startswith(b"PK")
    assert len(app.get("download_button")) == 2

    saved_bytes = app.session_state["v04_hitl_balance_sheet_xlsx"]
    next_editor_key = f"v04_entry_editor_{app.session_state['v04_hitl_revision']}"
    app.session_state[next_editor_key] = DataEditorState(edited_rows={0: {"金额": 13000.0}})
    _button(app, "保存修改并重算").click().run()
    assert any(error.value == "借贷不配平，请检查修改后的分录！" for error in app.error)
    assert app.session_state["v04_editable_entries"] == saved_after
    assert app.session_state["v04_hitl_balance_sheet_xlsx"] == saved_bytes


def test_business_history_flows_into_ppt() -> None:
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(app_path, default_timeout=90)
    app.query_params["load"] = "real_sap"
    app.run()
    _button(app, "← 返回主页").click().run()
    _button(app, "3. 业财转换引擎").click().run()
    app.text_area[0].set_value("销售产品一批，金额50000元，收到银行款").run()
    _button(app, "生成标准会计分录").click().run()
    assert app.session_state["biz_history"]

    _button(app, "← 返回主页").click().run()
    _button(app, "2. 生成高管 PPT").click().run()
    _button(app, "一键生成高管PPT").click().run()
    assert not app.exception
    presentation = Presentation(io.BytesIO(app.session_state["ppt_bytes"]))
    assert len(presentation.slides) == 6
    text = "\n".join(
        shape.text
        for slide in presentation.slides
        for shape in slide.shapes
        if getattr(shape, "has_text_frame", False)
    )
    assert "业财转换明细" in text
    assert "销售产品一批" in text
