# FinGuard 企业财务异常合规审计系统

当前版本：**V0.8.0 内测基线（Data Governance）**。V0.8 不开放公网发布；
V0.9 的发布闸门见 [docs/V0.9_RELEASE_CHECKLIST.md](docs/V0.9_RELEASE_CHECKLIST.md)。

> 把原始财务凭证转化为可解释、可复核、可交付的审计风险线索。
> 规则硬约束 × 统计异常检验 × 群体交叉验证 × LLM 辅助研判。

Streamlit Community Cloud 公网部署请直接按照
[《Streamlit Cloud 部署上线指南》](STREAMLIT_CLOUD_DEPLOY_GUIDE_CN.md) 操作。

## D 盘本地启动

最终运行代码位于 `D:\FinGuard_Project\current`，D 盘根目录提供启动器：

```text
D:\FinGuard_Project\Run_FinGuard_D.bat
```

双击启动器即可运行网站；脚本最后包含 `pause`，发生错误时窗口不会立即关闭。

FinGuard 是一个面向企业内审、纪检监察、代理记账、公益组织和中小机构的开源审计工作台。它不是“把几条关键词规则包装成 AI”，而是一条从多源数据接入、确定性清洗、证据分层评分、AI 研判到 Excel 工作底稿交付的完整链路。

## 为什么值得做

财务舞弊与基层微腐败往往不是一笔惊天大案，而是长期、小额、碎片化地侵蚀组织资产：虚假办公用品、重复报销、周末突击录入、关联供应商套现、把招待费拆成多笔“耗材”。大型集团可能买得起昂贵的审计中台，基层单位、公益组织和小企业却常常只能靠人工翻凭证。

FinGuard 的社会价值在于降低“发现异常”的门槛，让有限的专业审计力量更快聚焦高风险样本；商业价值在于降低抽凭成本、缩短审计周期、形成标准化底稿，并把审计经验沉淀为可测试、可版本化的风险策略。

同时必须强调：**FinGuard 输出的是线索，不是结论。** 高分不等于舞弊，低分也可能漏报。任何问责、处分、移送或公开披露都必须经过有权限的专业人员复核、证据交叉验证和符合法律程序的判断。

## 核心能力

| 能力 | 实现 |
| --- | --- |
| 多源接入 | CSV、XLSX、XLSM；UTF-8、GB18030、GBK；CSV 自动分隔符识别 |
| 字段理解 | 摘要/说明/事由、借方/借、贷方/贷、制单人/经办人/报销人等中英文别名 |
| 深度清洗 | 剔除货币符号、千分位、全角字符；日期和金额强类型转换；空行与合计行过滤；异常行隔离并留痕 |
| 规则矩阵 | 金额极值、同日近似金额、周末/节假日/凌晨、模糊大额摘要 |
| 统计检验 | `scipy.stats` 分布统计；稳健 IQR，可切换 Z-score；小样本保护 |
| 交叉验证 | 同一制单人周末凌晨提交多笔接近整数的大额费用 |
| 拆分报销 | 同一制单人同一天出现 3 笔及以上 4,999 元报销时，固定触发 80 分专项预警 |
| 退款隔离 | 负数标记为“退款/红字冲销”，保留明细但不参与常规风险统计与评分 |
| 资产价格锚定 | 1,000–10,000 元的单次资产采购豁免极值与 IQR，按 45 分中风险提示核验入库单 |
| 高频采购 | 同一制单人 30 天内采购同一类物资至少 3 次；普通物资 80 分，大额资产 90 分 |
| 薪酬反欺诈 | 个人提交大额薪酬固定 90 分；财务部/行政部统一制单且金额在常规区间时按中风险处理 |
| 人工反馈 | 人工确认无误后写入本地 JSON，仅对摘要相似且金额接近的后续记录降低 20 分 |
| 审计项目归档 | 使用 SQLite 保存项目、状态、证据附件、审计源文件、风险明细和报告，刷新后可恢复 |
| 证据链留痕 | 自动记录项目创建、状态修改、附件上传和成果归档；附件保存 SHA-256 摘要 |
| 登录与权限 | 企业空间、PBKDF2 密码哈希、管理员/审计员/会计/只读角色和页面级权限 |
| 账号安全 | SMTP 邮箱验证码、邮箱绑定、修改密码、忘记密码、发送限流、失败锁定和会话过期 |
| 自动备份 | 每日自动快照、SHA-256 完整性校验、保留策略和损坏检测 |
| 浏览器验收 | Playwright 驱动真实 Edge 完成初始化、邮箱绑定、项目创建和财务审计 |
| 业财转换 | 制造业业务经 DeepSeek 生成标准借贷分录，无 Key 时使用轻量本地模板 |
| 轻量报表 | 原生 CSV 输出资产与负债变动表、利润表变动，不依赖重型图表库 |
| AI 研判 | 只对 `Risk_Score > 60` 的凭证调用 DeepSeek API，生成两段式风险与核查建议 |
| 交互分析 | KPI、制单人异常金额饼图、日期趋势、风险等级筛选、深红/黄色行高亮 |
| 审计交付 | 多工作表 Excel：管理摘要、异常明细、全部凭证、数据质量、隔离记录、原生图表 |
| 高管 PPT | 纯 `python-pptx` 生成执行摘要、风险结构、重点凭证、AI 核查行动；支持空数据、大金额和超长中文摘要 |

## 风险评分口径

风险分由三层证据构成。**每层最多计分一次**，但同一层中的每个触发特征都会保留在 `Risk_Features`，避免因为重复计数造成分数失控。

| 证据层 | 触发条件 | 分值 |
| --- | --- | --- |
| 硬规则 | 任一硬规则触发 | +30 |
| 统计异常 | IQR 离群或 Z-score 超阈值 | +40 |
| 交叉验证 | 同日同人周末凌晨多笔近似整数大额凭证 | +30 |

风险总分封顶 100：

- `0 ≤ Risk_Score < 30`：低风险
- `30 ≤ Risk_Score ≤ 60`：中风险，黄色高亮
- `Risk_Score > 60`：高风险，深红色高亮并触发 AI

因此：

- 仅硬规则：30 分，中风险
- 仅统计异常：40 分，中风险
- 仅交叉验证：30 分，中风险
- 规则 + 统计：70 分，高风险
- 规则 + 交叉：60 分，中风险
- 规则 + 统计 + 交叉：100 分，高风险
- 统计 + 交叉：70 分，高风险

### 硬规则解释

1. **金额极值**：单笔金额大于本次样本均值的 3 倍。
2. **同日近似金额**：同一日期、同一制单人存在两笔以上金额相对差异小于 1% 的凭证。算法按金额排序，仅比较相邻值，避免全量两两比较导致 O(n²) 性能灾难。
3. **敏感时间**：
   - 星期六、星期日；
   - 中国法定节假日；
   - 日期字段确有显式时间且位于 00:00–05:00。
4. **模糊摘要大额**：
   - 摘要包含“办公用品”“劳保”“招待”“礼品”等模糊词；
   - 金额不低于 `max(样本均值 × 3, 样本 P90)`；
   - 摘要缺少发票、明细、合同、项目、数量、出库等可追溯信息。

### 统计异常解释

默认使用截去最高 5% 和最低 5% 金额后的动态 IQR，乘数为 3.0：

```text
上界 = Q3 + 3.0 × IQR
下界 = max(0, Q1 - 3.0 × IQR)
```

样本量少于 8 笔时禁用统计层，避免小样本把正常差异误判为异常。IQR 为 0 时自动回退到 Z-score 绝对值大于 3。

系统还计算均值、标准差、Q1、中位数、Q3、IQR、MAD 和 `scipy.stats.describe` 结果，保留统计来源供审计复核。对右偏的财务金额，IQR 通常比普通 Z-score 更稳健；你可以在 `RiskConfig(statistical_method="zscore")` 中切换。

### 交叉验证解释

交叉验证要求同时满足：

1. 同一制单人、同一日期；
2. 周末；
3. 00:00–05:00 的显式时间；
4. 至少两笔；
5. 单笔金额不低于 `max(1,000, 样本 P75)`；
6. 金额接近整数，误差不超过 `max(1, 金额 × 0.1%)`。

这一条不是简单重复规则，而是在“非工作时间 + 群体提交 + 整数金额”三个弱信号上做组合，减少单一条件造成的告警疲劳。

## 金额口径

同一条凭证如果借、贷金额都可能存在，FinGuard 使用：

```text
amount = max(abs(debit), abs(credit))
```

这是为了在未知记账方向的情况下衡量凭证规模，避免同一笔分录的借贷双边被重复相加。若你的组织使用“原币 + 本币”或“发生额 + 方向”结构，建议在接入前明确口径；通用“金额 + 借贷方向”字段已支持自动拆分。

将借方金额视为正的资产/费用流出，贷方金额视为贷方发生额。原表中的正负号会保留在 `debit`、`credit`，风险分析使用绝对值。

## 架构

```text
CSV / Excel
    │
    ▼
DataIngestionService
    ├─ 编码/工作表识别
    ├─ 字段别名统一映射
    ├─ 空行/合计行过滤
    ├─ 日期、金额强类型化
    └─ 无效行隔离 + SHA-256 指纹
    │
    ▼
RiskEngine
    ├─ Rule Layer             +30
    ├─ Statistical Layer      +40  (IQR / Z-score)
    └─ Cross-validation       +30
    │
    ▼
Risk_Score (0–100) + Risk_Features + Risk_Level
    │
    ├──> Streamlit Dashboard / Plotly
    ├──> AIAuditor (仅 >60 分，DeepSeek API)
    ├──> XlsxWriter 审计底稿
    └──> python-pptx 高管演示文稿
```

### 模块拆解

| 文件 | 职责 |
| --- | --- |
| `app.py` | Streamlit 入口、跨模块会话状态、明确路由与自动 AI 编排 |
| `finguard/accounting_core.py` | 会计科目分类、试算平衡、资产负债表和利润表的唯一事实源 |
| `finguard/capacity_engine.py` | 资产台账解析、木桶瓶颈测算与隔离的沙盘状态 |
| `finguard/capacity_page.py` | 产能沙盘前端交互 |
| `finguard/simulator_page.py` | 滑块与精确输入双向绑定的经营推演页面 |
| `finguard/intelligent_page.py` | Isolation Forest 异常展示与财报摘要 NLP 页面 |
| `finguard/settings_page.py` | 仅当前会话有效的 DeepSeek 临时密钥配置 |
| `finguard/account_page.py` | 登录邮箱绑定与密码修改 |
| `finguard/account_security.py` | 验证码签发、限流、密码重置和账号安全流程 |
| `finguard/email_delivery.py` | QQ 邮箱等 SMTP 验证码投递适配器 |
| `finguard/release_readiness.py` | 密钥、认证、SMTP、数据库和发布阻断检查 |
| `finguard/backup_manager.py` | 自动备份、SHA-256 校验和保留策略 |
| `finguard/version.py` | 版本与发布通道元数据 |
| `finguard/workspace_store.py` | SQLite 审计项目、证据附件和操作日志持久化 |
| `finguard/workspace_page.py` | 审计项目、证据资料与操作日志前端 |
| `finguard/config.py` | 接入与风险策略的可审计配置 |
| `finguard/data_ingestion.py` | 文件读取、字段识别、金额/日期清洗、隔离记录 |
| `finguard/risk_engine.py` | 规则层、统计层、交叉验证、评分与解释字段 |
| `finguard/ai_auditor.py` | 最小化提示词、DeepSeek Chat Completions 调用、批量结果写回 |
| `finguard/reporting.py` | 商业级 Excel 报告、颜色格式、原生图表、公式注入防护 |
| `finguard/ppt_generator.py` | 高管 PPT、容错摘要、动态图表配色、中文长文本自适应 |
| `finguard/manufacturing_engine.py` | 制造业边界校验、DeepSeek 分录映射、强制核算与增值税拦截 |
| `finguard/advanced_business.py` | 固定资产处置等非标业务的独立本地规则 |
| `finguard/v04_hitl.py` | 人工复核草稿、借贷配平校验与纯本地报表重算 |
| `finguard/ml_risk_engine.py` | 本地孤立森林与 AI 财报摘要分析适配 |
| `finguard/ui.py` | 视觉系统、KPI、Plotly、风险表格、质量证据 |
| `finguard/demo.py` | 完全合成的演示数据，覆盖三层风险 |
| `tests/` | 清洗、评分、AI 调用边界、报告输出测试 |

所有风险阈值集中在 [finguard/config.py](finguard/config.py)，不要在业务代码里散落 magic number。
所有资产与利润报表统一从 [finguard/accounting_core.py](finguard/accounting_core.py)
读取科目分类和报表口径，V0.3 导出与 V0.4 人工复核重算不得各自维护一套会计逻辑。

审计项目默认保存到 `data/finguard.db`。本地部署可通过 `FINGUARD_DB_PATH`
改用其他路径；Streamlit Community Cloud 的文件系统不保证长期持久化，正式部署应将该
SQLite 适配层替换为 PostgreSQL 或挂载持久化磁盘。

设置 `FINGUARD_AUTH_REQUIRED=1`（或 Streamlit Secret
`FINGUARD_AUTH_REQUIRED = true`）后启用登录。首次启动会创建企业空间和第一个管理员；
管理员可在“审计项目与证据链 → 企业成员”中创建审计员、会计和只读成员。

## 快速开始

### 环境要求

- Python 3.11 或 3.12
- 建议 2 GB 以上可用内存
- 25 MB 以下文件可直接用默认 UI 配置；大批量导入请走批处理并增加资源限制
- AI 功能需要可用的 DeepSeek API Key；没有密钥时，清洗、评分、图表、本地会计规则和 Excel 仍可运行

### Windows PowerShell

```powershell
cd outputs\finguard
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
```

编辑 `.streamlit/secrets.toml`，填入真实密钥和当前账户可用的模型 ID，然后运行：

```powershell
streamlit run app.py
```

AI 密钥只从 Streamlit Secrets 读取。本地开发时复制示例文件：

```powershell
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
notepad .streamlit\secrets.toml
```

在生成的 `.streamlit/secrets.toml` 中填写真实 DeepSeek 密钥。该文件已被
`.gitignore` 排除，禁止提交 GitHub。

### macOS / Linux

```bash
cd outputs/finguard
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
streamlit run app.py
```

### Docker

```bash
docker build -t finguard:1.0 .
docker run --rm -p 8501:8501 \
  -v "$(pwd)/.streamlit/secrets.toml:/app/.streamlit/secrets.toml:ro" \
  finguard:1.0
```

浏览器打开 `http://localhost:8501`。生产环境应放在 HTTPS 反向代理之后，并接入身份认证、RBAC 和审计日志。

## AI 配置

FinGuard 使用 OpenAI Python SDK 访问 DeepSeek 的 OpenAI 兼容接口：

```python
client = OpenAI(
    api_key=st.secrets["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)
response = client.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ],
    max_tokens=700,
)
```

默认模型是 `deepseek-chat`，可通过 Streamlit Secrets 中的
`DEEPSEEK_MODEL` 修改。上线前请按
[DeepSeek API 官方文档](https://api-docs.deepseek.com/)
核对账户实际可用的模型与计费规则。

AI 调用边界：

1. 只处理 `Risk_Score > 60` 的凭证；
2. 只发送凭证号、日期、制单人、摘要、金额、风险分和触发特征；
3. 摘要经过字符长度限制与 HTML 转义，并明确标记为“不可信数据”；
4. 模型返回两段自然语言，写入 `ai_advice`；
5. API 失败不覆盖原有风险分数，状态写入 `ai_status`；
6. 已成功生成的建议不会在普通重跑中重复计费，除非主动强制重算。

模型回答不能直接作为处分依据，也不能被写入“已确认舞弊”字段。建议在工作底稿中记录模型 ID、调用时间、提示词版本和人工复核人。

## 数据格式

最小要求：日期、制单人、摘要，以及借方/贷方/通用金额中的至少一个。缺少凭证号时自动生成 `FG-000001` 形式编号。

示例：

```csv
凭证号,日期,时间,制单人,说明,借方,贷方
PZ-001,2026-08-03,09:12,陈晨,服务器配件采购｜发票 INV-001｜数量 4,"¥1,280.00",0
PZ-002,2026-08-04,10:20,李敏,客户差旅交通费｜项目 P-108,860.00,0
```

完整可运行样例见 [sample_data/example_vouchers.csv](sample_data/example_vouchers.csv)。

包含乱码金额、负数退款、汇总行和 4,999 元拆分报销的 100 笔回归样例见
[sample_data/dirty_vouchers_100.csv](sample_data/dirty_vouchers_100.csv)。

合成数据生成的报告排版样例见
[sample_data/FinGuard_示例审计报告.xlsx](sample_data/FinGuard_示例审计报告.xlsx)，其中不包含任何真实组织或个人信息。

已内置的常见别名包括：

| Canonical | 示例别名 |
| --- | --- |
| `voucher_id` | 凭证编号、凭证号、业务单号、单据号、流水号 |
| `date` | 记账日期、凭证日期、制单日期、发生日期、业务日期 |
| `user` | 制单人、经办人、创建人、录入人、报销人、申请人 |
| `summary` | 摘要、说明、事由、用途、业务摘要、备注 |
| `debit` | 借方、借、借金额、借方发生额 |
| `credit` | 贷方、贷、贷金额、贷方发生额 |

组织特有字段无需修改核心代码：

```python
service = DataIngestionService(
    extra_aliases={
        "summary": ("费用事项", "交易描述"),
        "user": ("发起员工",),
    }
)
```

### 时间识别说明

日期列和可选时间列会自动合并。只有存在显式时间（例如 `02:16`、`02:16:00` 或含非零时分秒的 datetime）时，才会触发凌晨规则；仅有日期、被 Excel 自动补成 `00:00:00` 的单元格不会被误判为凌晨。

如果源系统把“凌晨 00:00 的日期时间”只保存为无时区 datetime 且时分秒恰为零，建议同时提供独立“录入时间”列，或在上游保存可区分的时间字段。

## 报告说明

Excel 包含：

- **管理摘要**：总笔数、总金额、高风险笔数/金额、风险分布、审计口径；
- **异常明细**：所有 30 分以上凭证，按风险分和金额降序，高风险深红、中风险黄色；
- **全部凭证**：支持复核低分样本与算法全貌；
- **数据质量**：原始行数、隔离数量、SHA-256、质量结论；
- **隔离记录**：空值/汇总/无效日期/无效金额/零金额行及原因；
- **原生图表**：异常金额制单人分布和发生日期趋势；
- **AI 建议**：仅有 `ai_advice` 的高风险行展示模型输出；未配置 AI 时明确标注人工研判。

导出时会对以 `=`, `+`, `-`, `@` 开头的文本加前缀，降低 Excel 公式注入风险。报告仍可能包含个人姓名、供应商与业务信息，必须按组织数据分类分级制度加密存储并设置到期删除。

## 测试与质量检查

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
```

测试覆盖：

- 中文字段别名映射；
- CSV 编码、金额/日期清洗；
- 空行、汇总行和异常行隔离；
- 三层风险评分和小样本保护；
- 仅 >60 分凭证调用 AI；
- 提示注入转义；
- Excel 工作表与关键列完整性。

本项目不把测试通过等同于审计结论有效。上线前应使用经过脱敏的历史样本做：

- 混淆矩阵、召回率、精确率和审计人员复核耗时评估；
- 按部门、岗位、地区和金额分层的偏差测试；
- 阈值敏感性分析；
- 供应商/模型版本变更后的回归；
- 人工标注一致性（Cohen's kappa 等）评估。

## 生产部署必须补齐的能力

这是一个可运行的业务应用，不是“部署即合规”的最终产品。真实生产环境至少需要：

1. **身份与权限**：当前已提供企业空间、密码哈希和角色权限；正式生产仍需 SSO/MFA、部门级数据权限、审批与双人复核；
2. **数据保护**：传输 TLS、静态加密、字段最小化、去标识化、密钥托管与轮换；
3. **网络边界**：不要将 Streamlit 裸露在公网；API 域名和出口策略显式白名单；
4. **审计日志**：上传、查看、筛选、AI 调用、报告下载和模型版本变更；
5. **AI 治理**：供应商 DPA、数据保留设置、区域合规、成本上限、提示词与模型版本登记；
6. **人工复核**：任何风险结论都应有证据链、复核人、处理状态和申诉渠道；
7. **可用性**：限流、超时、重试、缓存、作业隔离、备份和灾难恢复；
8. **偏见与公平**：避免因岗位、部门或录入习惯形成系统性歧视，定期做分层误报审计。

`server.maxUploadSize = 25` 只是 UI 层限制，反向代理和 API 网关仍应独立限流。

`requirements.txt` 固定为本次完整测试通过的版本，避免生产环境因上游大版本更新发生静默行为漂移。升级依赖时请同时重跑 `pytest`、`ruff check` 和真实脱敏样本回归。

## 已知边界

- 规则阈值依赖样本结构和业务口径；跨组织直接复用默认阈值可能产生误报。
- IQR 对极度右偏、样本极少或金额高度同质的数据并不总是有效；系统已做小样本保护，但专业抽样仍不可替代。
- 法定节假日依赖 `holidays` 日历版本，政策临时调休可能需要更新依赖或自定义日历。
- 凭证号重复目前保留并告警，不自动判定为重复报销；应结合发票号、付款流水和业务系统核销状态。
- `amount = max(abs(debit), abs(credit))` 不代表会计准则中的净额或现金流口径。
- AI 可能遗漏、幻觉、表述过度或受提示注入影响，不能替代审计判断。

## 路线图

- 发票、银行流水与凭证三方勾稽；
- 供应商与员工关联关系图谱；
- 预算/科目维度的行为基线；
- 本地化模型和 PII 脱敏网关；
- 人工标注反馈闭环；
- 多租户、任务队列和百万行批处理；
- 规则版本管理、可复现实验与模型评估报告。

## 开源与贡献

项目采用 MIT License。欢迎提交：

- 脱敏的字段别名；
- 可解释但不易误伤的新规则；
- 反偏见测试；
- 真实部署中的性能和安全加固；
- 中文审计术语与核查指引的校准建议。

提交前请移除一切真实客户数据、账号、身份证号、银行卡号、发票号、供应商名称和内部路径。

## 参考资料

- [DeepSeek API 官方文档](https://api-docs.deepseek.com/)
- [Streamlit Community Cloud 部署文档](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
- [Streamlit Cloud Secrets 管理](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
- [Streamlit 官方文档](https://docs.streamlit.io/)
- [SciPy stats 官方文档](https://docs.scipy.org/doc/scipy/reference/stats.html)
- [Plotly Python 官方文档](https://plotly.com/python/)

审计线索有社会价值，但只有程序正义、证据规则和人工复核都到位，技术才不会把“发现异常”变成“制造冤案”。
