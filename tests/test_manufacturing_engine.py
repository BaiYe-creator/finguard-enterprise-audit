from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from finguard.manufacturing_engine import (
    FONT_NAME,
    MONEY_FORMAT,
    SYSTEM_PROMPT,
    AmountUnitValidationError,
    SaleStructureValidationError,
    VatSplitValidationError,
    _coerce_amount_value,
    _extract_amount,
    _validate_explicit_wan_amount,
    _validated_ai_records,
    add_history_records,
    build_balance_sheet_xlsx,
    build_income_statement_xlsx,
    convert_business,
    petty_cash_balance,
)


def _response(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps(payload, ensure_ascii=False),
                )
            )
        ]
    )


def _business_from_call(kwargs: dict) -> tuple[int, str]:
    user_content = kwargs["messages"][1]["content"]
    payload = json.loads(user_content.split("业务行：\n", 1)[1])
    assert len(payload) == 1
    return payload[0]["line"], payload[0]["business"]


def _report_rows(blob: bytes) -> dict[str, object]:
    worksheet = load_workbook(io.BytesIO(blob), data_only=False).active
    return {
        str(worksheet.cell(row, 1).value): worksheet.cell(row, 2).value
        for row in range(1, worksheet.max_row + 1)
        if worksheet.cell(row, 1).value
    }


def _records_for_line(records: list[dict], line: int) -> list[dict]:
    return [record for record in records if record.get("line") == line]


def test_ai_mapping_is_isolated_per_line_and_rejects_non_manufacturing() -> None:
    calls: list[dict] = []

    class DynamicCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            line, business = _business_from_call(kwargs)
            if "App" in business or "网站" in business:
                return _response(
                    {
                        "records": [],
                        "rejected": [
                            {
                                "line": line,
                                "business": business,
                                "reason": "非制造业业务，已拒绝转换",
                            }
                        ],
                        "summary": "已拒绝非制造业业务。",
                    }
                )
            return _response(
                {
                    "records": [
                        {
                            "line": line,
                            "business": business,
                            "debit": "原材料",
                            "credit": "银行存款",
                            "amount": 100000,
                            "description": "采购制造用钢材",
                        }
                    ],
                    "rejected": [],
                    "summary": "已生成采购分录。",
                }
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=DynamicCompletions()))
    result = convert_business(
        "采购钢材10吨，金额100000元\n开发一个手机App，金额50000元",
        api_key="test-key",
        client=client,
    )

    assert not result.used_ai
    assert len(calls) == 1
    assert len(result.records) == 3
    assert len(result.rejected) == 1
    assert result.records[0]["line"] == 1
    assert result.rejected[0]["line"] == 2
    assert result.records[0]["debit"] == "原材料"


def test_local_rules_override_ai_false_rejections_for_daily_business() -> None:
    class OverRejectingCompletions:
        def create(self, **kwargs):
            _, business = _business_from_call(kwargs)
            return _response(
                {
                    "records": [],
                    "rejected": [
                        {
                            "line": 1,
                            "business": business,
                            "reason": "非制造业业务，已拒绝转换",
                        }
                    ],
                    "summary": "已拒绝。",
                }
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=OverRejectingCompletions()))
    text = "\n".join(
        (
            "2026-10-30，收到银行结息单，本季度银行存款利息收入800元。",
            "2026-10-22，公司高管报销业务招待费（餐饮费），共计12000元，银行存款支付。",
            "2026-10-02，银行转账支付上述钢材货款及税金，金额50000元。",
        )
    )
    result = convert_business(text, api_key="test-key", client=client)

    assert len(result.records) == 3
    assert not result.rejected
    assert not result.used_ai
    assert "2 行日常业务误判" in result.fallback_reason
    assert [(item["debit"], item["credit"]) for item in result.records] == [
        ("银行存款", "财务费用"),
        ("管理费用", "银行存款"),
        ("应付账款", "银行存款"),
    ]


def test_local_mapping_and_standard_xlsx_reports_are_balanced() -> None:
    result = convert_business(
        "销售产品一批，金额50000元，收到银行款\n"
        "采购设备一台，金额30000元，银行付款\n"
        "采购钢材10吨，金额100000元，银行付款\n"
        "结转成本，金额20000元\n"
        "销售费用，金额1000元",
        api_key="",
    )
    records = add_history_records(result)
    assert len(records) == 11
    assert any(record["credit"] == "主营业务收入" for record in records)
    assert any(record["debit"] == "固定资产" for record in records)
    assert any(record["debit"] == "销售费用" for record in records)

    balance_sheet = _report_rows(build_balance_sheet_xlsx(records))
    income_statement = _report_rows(build_income_statement_xlsx(records))
    assert balance_sheet["货币资金"] == -81000.0
    assert balance_sheet["固定资产"] == 26548.67
    assert balance_sheet["资产总计"] == balance_sheet["负债及所有者权益总计"]
    assert balance_sheet["平衡校验：资产总计 - 负债及所有者权益总计"] == 0.0
    assert income_statement["一、营业收入"] == 44247.79
    assert income_statement["减：营业成本"] == 20000.0
    assert income_statement["    销售费用"] == 1000.0
    assert income_statement["二、营业利润"] == 23247.79


def test_non_manufacturing_only_input_is_rejected() -> None:
    result = convert_business("开发一个电商网站，金额80000元", api_key="")
    assert not result.records
    assert result.rejected


def test_all_normal_manufacturing_operations_survive_multi_line_local_mapping() -> None:
    text = "\n".join(
        (
            "采购钢材10吨，金额100000元，银行付款",
            "生产领料，金额20000元",
            "发放工资，金额30000元",
            "销售产品一批，金额50000元",
            "费用报销，金额500元",
            "计提折旧，金额8000元",
            "结转成本，金额12000元",
            "银行利息，金额1000元",
            "收到客户货款，金额60000元",
        )
    )
    result = convert_business(text, api_key="")
    assert len(result.records) == 13
    assert not result.rejected
    assert sorted({record["line"] for record in result.records}) == list(range(1, 10))


def test_required_offline_interest_collection_and_machine_purchase_rules() -> None:
    cases = (
        ("计提银行借款利息，金额1200元", "财务费用", "应付利息", 1200.0),
        ("收到银行存款利息，金额300元", "银行存款", "财务费用", 300.0),
        ("收到客户欠款，金额50000元", "银行存款", "应收账款", 50000.0),
        ("采购机床2台，单价15000元/台", "固定资产", "银行存款", 30000.0),
    )
    for business, debit, credit, amount in cases:
        result = convert_business(business, api_key="")
        assert not result.rejected, business
        if business.startswith("采购"):
            assert len(result.records) == 3, business
            assert result.records[0]["debit"] == debit, business
            assert result.records[-1]["credit"] == credit, business
            assert result.records[-1]["amount"] == amount, business
        else:
            assert len(result.records) == 1, business
            record = result.records[0]
            assert record["debit"] == debit, business
            assert record["credit"] == credit, business
            assert record["amount"] == amount, business


def test_common_manufacturing_offline_rules_cover_inventory_tax_and_expenses() -> None:
    cases = (
        ("销售结转成本，金额36000元", "主营业务成本", "库存商品"),
        ("支付税费，金额8000元", "应交税费", "银行存款"),
        ("报销招待费，金额1500元", "管理费用", "银行存款"),
        ("退回材料，金额6000元", "原材料", "生产成本"),
        ("发放工资，金额30000元", "应付职工薪酬", "银行存款"),
        ("结转制造费用，金额12000元", "生产成本", "制造费用"),
        ("完工入库，金额90000元", "库存商品", "生产成本"),
        ("支付车间水电费，金额5000元", "制造费用", "银行存款"),
        ("预收货款，金额40000元", "银行存款", "合同负债"),
        ("收到银行短期贷款，金额200000元", "银行存款", "短期借款"),
        ("计提企业所得税，金额20000元", "所得税费用", "应交税费"),
        ("采购办公用品，金额600元", "管理费用", "银行存款"),
        ("采购机床配件，金额2000元", "原材料", "银行存款"),
        ("支付已计提利息，金额1000元", "应付利息", "银行存款"),
    )
    for business, debit, credit in cases:
        result = convert_business(business, api_key="")
        assert not result.rejected, business
        record = result.records[0]
        assert record["debit"] == debit, business
        if len(result.records) == 1:
            assert record["credit"] == credit, business
        else:
            assert any(item["debit"] == "应交税费-应交增值税(进项)" for item in result.records), (
                business
            )
            assert result.records[-1]["credit"] == credit, business


def test_realistic_date_prefixed_descriptions_are_recognized_without_ai() -> None:
    text = "\n".join(
        (
            "2026-10-28，月末结转本月已销售割草机的成本（假设每台成本1500元）。",
            "2026-10-31，计提本月企业向银行借入的短期借款利息，共计2000元。",
            "2026-10-20，车间领用低值易耗品（润滑油、螺丝配件等），共计3000元。",
            "2026-10-10，计提本月生产车间使用的厂房及数控车床折旧费，共计15000元。",
            "2026-10-05，车间领用钢材20吨，用于生产新款割草机。",
        )
    )
    result = convert_business(text, api_key="")

    assert len(result.records) == 4
    assert len(result.rejected) == 1
    records = {record["line"]: record for record in result.records}
    assert (records[1]["debit"], records[1]["credit"], records[1]["amount"]) == (
        "主营业务成本",
        "库存商品",
        1500.0,
    )
    assert (records[2]["debit"], records[2]["credit"], records[2]["amount"]) == (
        "财务费用",
        "应付利息",
        2000.0,
    )
    assert (records[3]["debit"], records[3]["credit"], records[3]["amount"]) == (
        "制造费用",
        "原材料",
        3000.0,
    )
    assert (records[4]["debit"], records[4]["credit"], records[4]["amount"]) == (
        "制造费用",
        "累计折旧",
        15000.0,
    )
    assert result.rejected[0]["reason"] == "缺少具体金额，请补充"
    assert _extract_amount("2026-10-05，车间领用钢材20吨，用于生产新款割草机。") is None


def test_inventory_issue_amount_is_inferred_from_same_batch_purchase_cost() -> None:
    result = convert_business(
        "2026-10-05，采购钢材20吨，金额100000元，银行付款。\n"
        "2026-10-20，车间领用钢材10吨，用于生产新款割草机。",
        api_key="",
    )

    assert len(result.records) == 4
    assert not result.rejected
    issue = _records_for_line(result.records, 2)[0]
    assert issue["debit"] == "生产成本"
    assert issue["credit"] == "原材料"
    assert issue["amount"] == 44247.79
    assert "同物料、同单位" in issue["description"]


def test_daily_business_total_amounts_context_issue_and_profit_closing() -> None:
    text = "\n".join(
        (
            "11月15日，客户还了之前欠的3万块。",
            "11月8日，办公室买了几个文件柜，花了1200。",
            "10月31日，把本月赚的利润，直接结转到未分配利润里。",
            "10月28日，车间领用了昨天买的那批不锈钢，全部投入生产。",
            "10月27日，买了不锈钢，金额8000元，银行付款。",
            "10月25日，公司账户收到了银行结息（利息收入），80块钱。",
            "10月16日，交了第三季度的企业所得税，一共1.2万，银行扣款。",
            "10月11日，结转销售成本，金额60000元。",
            "10月10日，销售产品，金额100000元，银行收款。",
            "10月4日，车间的老李领了5吨钢材去开机器干活了。",
            "10月3日，采购钢材10吨，金额50000元，银行付款。",
        )
    )
    result = convert_business(text, api_key="")

    assert not result.rejected
    records = {
        line_number: _records_for_line(result.records, line_number) for line_number in range(1, 12)
    }
    assert (records[1][0]["debit"], records[1][0]["credit"], records[1][0]["amount"]) == (
        "银行存款",
        "应收账款",
        30000.0,
    )
    assert (records[2][0]["debit"], records[2][-1]["credit"], records[2][-1]["amount"]) == (
        "固定资产",
        "银行存款",
        1200.0,
    )
    assert (records[3][0]["debit"], records[3][0]["credit"], records[3][0]["amount"]) == (
        "本年利润",
        "未分配利润",
        28575.58,
    )
    assert records[4][0]["amount"] == 7079.65
    assert records[7][0]["amount"] == 12000.0
    assert records[10][0]["amount"] == 22123.90


def test_profit_and_material_issue_can_use_previous_business_history() -> None:
    prior_result = convert_business(
        "销售产品，金额100000元，银行收款。\n"
        "结转销售成本，金额60000元。\n"
        "采购钢材10吨，金额50000元，银行付款。",
        api_key="",
    )
    prior_history = add_history_records(prior_result)

    result = convert_business(
        "把本月赚的利润结转到未分配利润。\n车间领用钢材5吨，用于生产。",
        api_key="",
        prior_history=prior_history,
    )

    assert not result.rejected
    closing, issue = result.records
    assert (closing["debit"], closing["credit"], closing["amount"]) == (
        "本年利润",
        "未分配利润",
        28495.58,
    )
    assert issue["amount"] == 22123.90
    assert "历史采购单价" in issue["description"]


def test_connection_failure_is_reported_once_and_falls_back_locally() -> None:
    class APIConnectionError(Exception):
        pass

    class OfflineCompletions:
        def create(self, **kwargs):
            raise APIConnectionError("network unavailable")

    client = SimpleNamespace(chat=SimpleNamespace(completions=OfflineCompletions()))
    result = convert_business(
        "计提利息，金额1000元\n收到欠款，金额5000元",
        api_key="test-key",
        client=client,
    )

    assert len(result.records) == 2
    assert "无法连接 DeepSeek" in result.fallback_reason
    assert "2 行" in result.fallback_reason
    assert result.fallback_reason.count("无法连接 DeepSeek") == 1
    assert "第 1 行" not in result.fallback_reason


def test_prompt_is_exact_and_every_line_gets_a_separate_api_call() -> None:
    calls: list[dict] = []

    class DynamicCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            line, business = _business_from_call(kwargs)
            return _response(
                {
                    "records": [
                        {
                            "line": line,
                            "business": business,
                            "debit": "管理费用",
                            "credit": "银行存款",
                            "amount": 1000,
                            "description": "按业务实质生成的测试分录",
                        }
                    ],
                    "rejected": [],
                    "summary": "单行业务已处理。",
                }
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=DynamicCompletions()))
    text = "发薪，金额30000元\n费用报销，金额500元\n银行利息，金额1000元"
    result = convert_business(text, api_key="test-key", client=client)

    assert result.used_ai
    assert len(result.records) == 3
    assert not result.rejected
    assert len(calls) == 2
    assert all(call["messages"][0]["content"] == SYSTEM_PROMPT for call in calls)
    assert SYSTEM_PROMPT == (
        "你是百强制造业财务总监。必须按行独立分析。"
        "有明确总金额时直接使用总金额；缺少总金额时，才根据单价乘数量或上下文采购成本推算。"
        "车间领料贷方必须是原材料；买设备借方必须是固定资产；"
        "银行利息、招待费、税费、折旧、领料、结转成本、借款利息、收回欠款、结转利润"
        "均属于制造业日常业务，"
        "不得以非制造业为由拒绝；"
        "结转利润没有金额时，汇总本批其他分录的净利润；领料没有金额时，"
        "查找最近一次同种材料采购金额或单价进行推算；"
        "确实缺少金额时只回答：缺少具体金额，请补充。"
        "遇到金额带有‘万’、‘千’、‘百’等中文单位时，必须在 JSON 中输出换算后的纯数字"
        "（如 2.1万 必须输出 21000.00）。绝对禁止输出带单位的字符串或未换算的小数！"
        "凡涉及采购或销售，默认按13%增值税拆分；给出的是含税总额。采购必须拆分原材料或资产、"
        "进项税额、银行存款或应付账款；销售必须拆分银行存款或应收账款、不含税收入、销项税额。"
        "同一业务行必须返回多条 records 分录，不得只返回净额或只返回单价。"
        "员工报销、打车费、差旅、招待、油费、快递、水费、电费属于费用，"
        "严禁生成主营业务收入或销项税额；借方只能为管理费用或销售费用，"
        "贷方只能为银行存款、其他货币资金-微信或应付账款。"
        "销售业务必须且只能有一笔借方银行存款或应收账款，"
        "贷方必须拆分为主营业务收入和销项税额，严禁多笔借方重复挂账。"
        "绝不可以使用万能模板！"
    )


def test_accounting_guardrails_correct_amount_and_rigid_accounts() -> None:
    class SloppyCompletions:
        def create(self, **kwargs):
            _, business = _business_from_call(kwargs)
            if "领料" in business:
                debit, credit = "制造费用", "银行存款"
            else:
                debit, credit = "管理费用", "银行存款"
            return _response(
                {
                    "records": [
                        {
                            "line": 1,
                            "business": business,
                            "debit": debit,
                            "credit": credit,
                            "amount": 5000,
                            "description": "待系统校验",
                        }
                    ],
                    "rejected": [],
                    "summary": "已处理。",
                }
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SloppyCompletions()))
    material = convert_business(
        "车间领料，单价10元/件，数量5件",
        api_key="test-key",
        client=client,
    ).records[0]
    equipment = convert_business(
        "购买生产设备一台，金额30000元",
        api_key="test-key",
        client=client,
    ).records[0]

    assert material["amount"] == 50.0
    assert material["credit"] == "原材料"
    assert equipment["debit"] == "固定资产"


def test_wan_unit_is_converted_before_journal_entry() -> None:
    business = "11月14日，计提本月设备折旧费，共计2.1万。"
    local_result = convert_business(business, api_key="")
    assert local_result.records[0]["amount"] == 21000.0

    class StringAmountCompletions:
        def create(self, **kwargs):
            return _response(
                {
                    "records": [
                        {
                            "line": 1,
                            "business": business,
                            "debit": "制造费用",
                            "credit": "累计折旧",
                            "amount": "2.1万",
                            "description": "计提折旧",
                        }
                    ],
                    "rejected": [],
                    "summary": "完成",
                }
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=StringAmountCompletions()))
    ai_result = convert_business(business, api_key="test-key", client=client)
    assert ai_result.records[0]["amount"] == 21000.0
    assert ai_result.used_ai


def test_unconverted_ai_wan_amount_is_rejected_then_recovered_locally() -> None:
    business = "11月14日，计提本月设备折旧费，共计2.1万。"

    class BadAmountCompletions:
        def create(self, **kwargs):
            return _response(
                {
                    "records": [
                        {
                            "line": 1,
                            "business": business,
                            "debit": "制造费用",
                            "credit": "累计折旧",
                            "amount": 2.1,
                            "description": "错误金额",
                        }
                    ],
                    "rejected": [],
                    "summary": "完成",
                }
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=BadAmountCompletions()))
    result = convert_business(business, api_key="test-key", client=client)

    assert result.records[0]["amount"] == 21000.0
    assert "金额单位规则校正" in result.fallback_reason
    assert _coerce_amount_value("2.1万") == 21000.0
    assert _coerce_amount_value("3.5千") == 3500.0
    assert _coerce_amount_value("4百") == 400.0
    with pytest.raises(AmountUnitValidationError, match="金额疑似单位解析错误"):
        _validate_explicit_wan_amount(business, 2.1)


def test_quantity_times_unit_price_overrides_incorrect_ai_amount() -> None:
    business = "10月6日，销售部的小王卖出去50台割草机，一台卖2000，客户痛快，钱直接打进公户了。"

    class UnitPriceCompletions:
        def create(self, **kwargs):
            return _response(
                {
                    "records": [
                        {
                            "line": 1,
                            "business": business,
                            "debit": "银行存款",
                            "credit": "主营业务收入",
                            "amount": 2000,
                            "description": "销售收入",
                        }
                    ],
                    "rejected": [],
                    "summary": "完成",
                }
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=UnitPriceCompletions()))
    result = convert_business(business, api_key="test-key", client=client)

    assert len(result.records) == 3
    assert result.records[0]["debit"] == "银行存款"
    assert result.records[0]["amount"] == 100000.0
    assert result.records[1]["credit"] == "主营业务收入"
    assert result.records[1]["amount"] == 88495.58
    assert result.records[2]["credit"] == "应交税费-应交增值税(销项)"
    assert result.records[2]["amount"] == 11504.42


def test_purchase_total_is_split_into_net_input_vat_and_payable() -> None:
    result = convert_business(
        "2026-10-01，向宝钢采购原材料钢材50吨，金额400000元，增值税率13%，款项暂未支付。",
        api_key="",
    )

    assert not result.rejected
    assert len(result.records) == 3
    assert [(record["debit"], record["credit"], record["amount"]) for record in result.records] == [
        ("原材料", "", 353982.30),
        ("应交税费-应交增值税(进项)", "", 46017.70),
        ("", "应付账款", 400000.00),
    ]
    assert (
        sum(
            float(record["amount"])
            for record in result.records
            if record["debit"] in {"原材料", "应交税费-应交增值税(进项)"}
        )
        == 400000.00
    )


def test_sales_total_is_split_into_receivable_revenue_and_output_vat() -> None:
    result = convert_business("销售100台割草机，总额30万。", api_key="")

    assert len(result.records) == 3
    assert [(record["debit"], record["credit"], record["amount"]) for record in result.records] == [
        ("应收账款", "", 300000.00),
        ("", "主营业务收入", 265486.73),
        ("", "应交税费-应交增值税(销项)", 34513.27),
    ]
    assert (
        sum(
            float(record["amount"])
            for record in result.records
            if record["credit"] in {"主营业务收入", "应交税费-应交增值税(销项)"}
        )
        == 300000.00
    )


def test_tax_exclusive_purchase_adds_vat_to_payable() -> None:
    result = convert_business(
        "采购钢材，不含税金额100000元，增值税率13%，银行付款。",
        api_key="",
    )

    assert [(item["debit"], item["credit"], item["amount"]) for item in result.records] == [
        ("原材料", "", 100000.00),
        ("应交税费-应交增值税(进项)", "", 13000.00),
        ("", "银行存款", 113000.00),
    ]


def test_tax_free_sale_does_not_create_fake_output_vat() -> None:
    result = convert_business("销售产品，金额100000元，免税，银行收款。", api_key="")

    assert [(item["debit"], item["credit"], item["amount"]) for item in result.records] == [
        ("银行存款", "", 100000.00),
        ("", "主营业务收入", 100000.00),
    ]


def test_unpaid_purchase_never_uses_bank_and_is_vat_split() -> None:
    result = convert_business(
        "采购钢材，金额11300元，先欠着。",
        api_key="",
    )

    assert [(item["debit"], item["credit"], item["amount"]) for item in result.records] == [
        ("原材料", "", 10000.00),
        ("应交税费-应交增值税(进项)", "", 1300.00),
        ("", "应付账款", 11300.00),
    ]
    assert all(item["credit"] != "银行存款" for item in result.records)


def test_internal_material_transfer_has_no_vat_and_cannot_credit_bank() -> None:
    result = convert_business(
        "车间领用钢材20吨，金额22600元。\n车间消耗库存商品，金额5000元。",
        api_key="",
    )

    assert not result.rejected
    assert len(result.records) == 2
    assert [(item["debit"], item["credit"], item["amount"]) for item in result.records] == [
        ("生产成本", "原材料", 22600.00),
        ("生产成本", "库存商品", 5000.00),
    ]
    assert all(
        "应交税费-应交增值税" not in item["debit"] and "应交税费-应交增值税" not in item["credit"]
        for item in result.records
    )


def test_workshop_machine_repair_remains_management_expense() -> None:
    result = convert_business(
        "2026-10-09，车间机器维修，金额5000元，银行付款。",
        api_key="",
    )

    assert len(result.records) == 1
    assert (result.records[0]["debit"], result.records[0]["credit"]) == (
        "管理费用",
        "银行存款",
    )


def test_ai_bank_credit_is_overridden_for_unpaid_and_internal_transfers() -> None:
    class WrongFundingCompletions:
        def create(self, **kwargs):
            _, business = _business_from_call(kwargs)
            return _response(
                {
                    "records": [
                        {
                            "line": 1,
                            "business": business,
                            "debit": "管理费用",
                            "credit": "银行存款",
                            "amount": 5000,
                            "description": "错误地将内部业务记为银行付款",
                        }
                    ],
                    "rejected": [],
                    "summary": "完成",
                }
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=WrongFundingCompletions()))
    result = convert_business(
        "支付办公费5000元，先欠着。\n车间领用钢材，金额5000元。",
        api_key="test-key",
        client=client,
    )

    assert len(result.records) == 2
    assert (result.records[0]["debit"], result.records[0]["credit"]) == (
        "管理费用",
        "应付账款",
    )
    assert (result.records[1]["debit"], result.records[1]["credit"]) == (
        "生产成本",
        "原材料",
    )
    assert all(item["credit"] != "银行存款" for item in result.records)


def test_office_and_workshop_purchases_do_not_fall_through_to_ai() -> None:
    result = convert_business(
        "11月13日，销售部买了台投影仪，2500块。\n"
        "11月2日，车间买了5台电风扇，一台300，银行打款。\n"
        "10月18日，车间买了一批手套和安全帽，花了2500。\n"
        "10月7日，行政买了几箱打印纸和笔，一共花了500。",
        api_key="",
    )

    assert not result.rejected
    assert len(result.records) == 12
    pages = {
        line_number: _records_for_line(result.records, line_number) for line_number in range(1, 5)
    }
    assert pages[1][0]["debit"] == "固定资产"
    assert pages[1][-1]["amount"] == 2500.0
    assert pages[2][0]["amount"] == 1327.43
    assert pages[2][-1]["amount"] == 1500.0
    assert pages[3][0]["debit"] == "周转材料"
    assert pages[4][0]["debit"] == "管理费用"


def test_colloquial_interest_and_membership_fee_are_not_rejected() -> None:
    result = convert_business(
        "11月16日，付了招聘网站的年度会员费，2000块。\n"
        "11月18日，算了这个月借银行的钱的利息，大概2500块，先记上。",
        api_key="",
    )

    assert not result.rejected
    assert len(result.records) == 2
    assert (result.records[0]["debit"], result.records[0]["credit"]) == (
        "管理费用",
        "银行存款",
    )
    assert (result.records[1]["debit"], result.records[1]["credit"]) == (
        "财务费用",
        "应付利息",
    )


def test_office_purified_water_is_a_management_expense_without_vat() -> None:
    result = convert_business(
        "办公室订了20桶纯净水，花了400块。",
        api_key="",
    )

    assert len(result.records) == 1
    assert (
        result.records[0]["debit"],
        result.records[0]["credit"],
        result.records[0]["amount"],
    ) == ("管理费用", "银行存款", 400.0)
    assert all(
        "应交税费-应交增值税" not in record["debit"]
        and "应交税费-应交增值税" not in record["credit"]
        for record in result.records
    )


def test_employee_reimbursement_never_creates_output_vat() -> None:
    result = convert_business(
        "员工报销打车费，金额80元。\n销售部员工报销差旅费，金额1000元，银行付款。",
        api_key="",
    )

    assert not result.rejected
    assert len(result.records) == 2
    assert all("应交税费-应交增值税(销项)" not in record["credit"] for record in result.records)


@pytest.mark.parametrize(
    ("business", "debit", "credit"),
    (
        ("员工报销打车费500元，银行付款", "管理费用", "银行存款"),
        ("销售部员工报销差旅费1000元，微信支付", "销售费用", "其他货币资金-微信"),
        ("车间报销油费300元，银行付款", "制造费用", "银行存款"),
        ("行政快递费200元，银行付款", "管理费用", "银行存款"),
        ("办公室水费400元，银行付款", "管理费用", "银行存款"),
    ),
)
def test_general_reimbursement_interceptor_blocks_revenue_and_vat(
    business: str,
    debit: str,
    credit: str,
) -> None:
    result = convert_business(business, api_key="")

    assert not result.rejected
    assert len(result.records) == 1
    record = result.records[0]
    assert (record["debit"], record["credit"]) == (debit, credit)
    assert record["credit"] not in {"主营业务收入", "应交税费-应交增值税(销项)"}


def test_petty_cash_balance_deducts_and_flags_insufficient_balance() -> None:
    withdraw = convert_business("提取备用金，金额1000元。", api_key="")
    withdraw_history = add_history_records(withdraw)
    assert petty_cash_balance(withdraw_history) == 1000.0
    assert (
        withdraw.records[0]["debit"],
        withdraw.records[0]["credit"],
    ) == ("库存现金", "银行存款")

    tea = convert_business(
        "发下午茶，金额300元。",
        api_key="",
        prior_history=withdraw_history,
    )
    tea_history = [*withdraw_history, *add_history_records(tea)]
    assert (
        tea.records[0]["debit"],
        tea.records[0]["credit"],
    ) == ("其他应收款-备用金", "库存现金")
    assert petty_cash_balance(tea_history) == 700.0

    insufficient = convert_business(
        "发下午茶，金额800元。",
        api_key="",
        prior_history=tea_history,
    )
    record = insufficient.records[0]
    assert record["status"] == "review_required"
    assert "备用金可用余额不足" in record["review_reason"]
    assert record["petty_cash_delta"] == 0.0


def test_screenshot_mixed_daily_batch_has_no_false_rejections() -> None:
    result = convert_business(
        "11月15日，客户还了之前欠的3万块。\n"
        "11月16日，付了招聘网站的年度会员费，2000块。\n"
        "11月17日，结转这个月卖出去机器的成本，一台成本1000。\n"
        "11月18日，算了这个月借银行的钱的利息，大概2500块，先记上。\n"
        "11月13日，销售部买了台投影仪，2500块。\n"
        "11月2日，车间买了5台电风扇，一台300，银行打款。\n"
        "10月18日，车间买了一批手套和安全帽，花了2500。\n"
        "10月7日，行政买了几箱打印纸和笔，一共花了500。",
        api_key="",
    )

    assert not result.rejected
    assert len(result.records) == 16
    assert sorted({record["line"] for record in result.records}) == list(range(1, 9))


def test_vat_multi_entries_keep_balance_sheet_balanced() -> None:
    result = convert_business(
        "采购钢材50吨，金额400000元，增值税率13%，款项暂未支付。",
        api_key="",
    )
    records = add_history_records(result)
    sheet = _report_rows(build_balance_sheet_xlsx(records))

    assert sheet["存货"] == 353982.30
    assert sheet["其他资产"] == 0.0
    assert sheet["应付账款"] == 400000.00
    assert sheet["资产总计"] == sheet["负债及所有者权益总计"]
    assert sheet["平衡校验：资产总计 - 负债及所有者权益总计"] == 0.0


def test_ai_vat_output_without_tax_entries_is_rejected() -> None:
    business = "采购钢材50吨，金额400000元"
    payload = {
        "records": [
            {
                "line": 1,
                "business": business,
                "debit": "原材料",
                "credit": "应付账款",
                "amount": 400000,
                "description": "未拆税的错误分录",
            }
        ],
        "rejected": [],
    }
    with pytest.raises(VatSplitValidationError, match="未按要求拆分增值税"):
        _validated_ai_records(payload, [business])


def test_ai_sales_output_with_multiple_debits_is_rejected() -> None:
    business = "销售产品，金额113000元，银行收款"
    payload = {
        "records": [
            {
                "line": 1,
                "business": business,
                "debit": "银行存款",
                "credit": "",
                "amount": 100000,
                "description": "错误借方一",
            },
            {
                "line": 1,
                "business": business,
                "debit": "应收账款",
                "credit": "",
                "amount": 13000,
                "description": "错误借方二",
            },
            {
                "line": 1,
                "business": business,
                "debit": "",
                "credit": "主营业务收入",
                "amount": 100000,
                "description": "收入",
            },
            {
                "line": 1,
                "business": business,
                "debit": "",
                "credit": "应交税费-应交增值税(销项)",
                "amount": 13000,
                "description": "销项税",
            },
        ],
        "rejected": [],
    }
    with pytest.raises(SaleStructureValidationError, match="只能有一笔"):
        _validated_ai_records(payload, [business])


def test_cost_transfer_uses_quantity_times_unit_cost_not_unit_cost_alone() -> None:
    result = convert_business(
        "10月29日，财务结转本月已经卖出去的60台割草机的成本，一台成本算1000。",
        api_key="",
    )

    assert len(result.records) == 1
    record = result.records[0]
    assert (record["debit"], record["credit"], record["amount"]) == (
        "主营业务成本",
        "库存商品",
        60000.0,
    )
    assert record["calculation_source"] == "本地强制核算"
    assert "60" in record["description"]


def test_required_cost_transfer_and_purchase_vat_split_together() -> None:
    result = convert_business(
        "结转60台割草机成本，一台成本算1000。\n采购50吨钢材，金额400000元，税率13%，款项暂未支付。",
        api_key="",
    )

    assert not result.rejected
    cost_records = _records_for_line(result.records, 1)
    purchase_records = _records_for_line(result.records, 2)
    assert len(cost_records) == 1
    assert cost_records[0]["amount"] == 60000.0
    assert cost_records[0]["calculation_source"] == "本地强制核算"
    assert [
        (record["debit"], record["credit"], record["amount"]) for record in purchase_records
    ] == [
        ("原材料", "", 353982.30),
        ("应交税费-应交增值税(进项)", "", 46017.70),
        ("", "应付账款", 400000.00),
    ]


@pytest.mark.parametrize(
    ("business", "expected"),
    (
        ("销售50台割草机，一台卖2000", 100000.0),
        ("数量50台，单价2千元/台", 100000.0),
        ("数量1.5万件，单价2元", 30000.0),
        ("采购2万件，每件3元", 60000.0),
        ("销售10万件，一件5元", 500000.0),
        ("2000元/台 × 50台", 100000.0),
        ("2000元×50台", 100000.0),
        ("2000/台×50", 100000.0),
        ("销售50台，2000元一台", 100000.0),
        ("销售50台，2000一台", 100000.0),
        ("50*2000", 100000.0),
        ("买2套，一套3000", 6000.0),
        ("采购三台设备，每台5千元", 15000.0),
        ("共计2万3千元", 23000.0),
        ("共计3万5", 35000.0),
        ("金额2千5", 2500.0),
        ("金额为一百二十元", 120.0),
        ("花了1,200.50元", 1200.5),
        ("原价10000元，实付8000元", 8000.0),
        ("总价10000元，收入9千元", 9000.0),
        ("金额10000元，税额1300元", 10000.0),
        ("金额10000元, 税额1300元", 10000.0),
        ("原价12000元，实付10000元，税额1300元", 10000.0),
        ("实付8000元原价10000元", 8000.0),
        ("1,234,567.89元", 1234567.89),
        ("人民币贰万元整", 20000.0),
        ("三点五万元", 35000.0),
        ("8千块钱", 8000.0),
        ("买笔50", 50.0),
        ("2026-10-30 采购10台设备，每台售价2千元", 20000.0),
    ),
)
def test_amount_parser_audit_covers_common_numeric_forms(
    business: str,
    expected: float,
) -> None:
    assert _extract_amount(business) == expected
    assert _coerce_amount_value(str(expected)) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("2.1万", 21000.0),
        ("2万3千元", 23000.0),
        ("3.5千", 3500.0),
        ("4百", 400.0),
        ("壹萬贰仟元", 12000.0),
        ("￥1,200.50", 1200.5),
    ),
)
def test_ai_amount_field_normalization_audit(value: str, expected: float) -> None:
    assert _coerce_amount_value(value) == expected


@pytest.mark.parametrize(
    "business",
    (
        "税率13%，金额未提供",
        "10月6日，采购10台设备",
        "发票号INV-20261030，采购钢材",
        "车间领用钢材20吨",
    ),
)
def test_amount_parser_does_not_turn_dates_tax_rates_or_quantities_into_money(
    business: str,
) -> None:
    assert _extract_amount(business) is None


def test_statement_workbooks_use_required_font_money_format_and_rules() -> None:
    records = add_history_records(
        convert_business(
            "销售产品一批，金额50000元，收到银行款\n采购设备一台，金额30000元，银行付款",
            api_key="",
        )
    )
    for blob in (
        build_balance_sheet_xlsx(records),
        build_income_statement_xlsx(records),
    ):
        worksheet = load_workbook(io.BytesIO(blob)).active
        assert worksheet.sheet_view.showGridLines is False
        for row in worksheet.iter_rows(min_row=4, max_row=worksheet.max_row, min_col=1, max_col=2):
            for cell in row:
                if cell.value is not None:
                    assert cell.font.name == FONT_NAME
                    assert cell.border.left is None
                    assert cell.border.right is None
        for row in range(5, worksheet.max_row + 1):
            value_cell = worksheet.cell(row, 2)
            if isinstance(value_cell.value, (int, float)):
                assert value_cell.number_format == MONEY_FORMAT
