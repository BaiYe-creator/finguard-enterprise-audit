# FinGuard Streamlit Community Cloud 部署上线指南

本指南按“完全没有部署经验”的场景编写。目标是：

1. 把 `finguard` 项目上传到一个私有 GitHub 仓库；
2. 在 Streamlit Community Cloud 免费工作区配置 DeepSeek 密钥；
3. 发布一个可通过公网网址访问的 FinGuard 网站。

## 0. 先确认文件结构

要上传的是下面这个目录里的“内容”：

```text
D:\FinGuard_Project
```

GitHub 仓库根目录必须直接看到：

```text
app.py
requirements.txt
finguard/
sample_data/
tests/
.streamlit/
README.md
```

不要上传整个 `outputs` 目录，否则 Streamlit 中的入口文件会变成
`finguard/app.py`，容易配错。仓库根目录必须是 `app.py` 所在目录。

上传前必须确认没有下面这些文件：

```text
.venv/
venv/
__pycache__/
.pytest_cache/
.ruff_cache/
.streamlit/secrets.toml
```

仓库中允许保留 `.streamlit/secrets.toml.example`，但不能保留真正含有密钥的
`.streamlit/secrets.toml`。

## 1. 准备 DeepSeek API Key

1. 登录 DeepSeek 开放平台并创建 API Key。
2. 将密钥临时保存到可信的密码管理器。
3. 不要把密钥粘贴到 GitHub、README、聊天截图、Excel 或代码文件。
4. 本项目的代码只通过下面的方式读取密钥：

```python
st.secrets["DEEPSEEK_API_KEY"]
```

应用默认使用的模型是 `deepseek-chat`。如果账户开通了其他模型，可以在
Streamlit Secrets 中修改 `DEEPSEEK_MODEL`。

## 2. 创建 GitHub 私有仓库

### 方法 A：在 GitHub 网页创建

1. 打开 <https://github.com/> 并登录。
2. 点击右上角 `+`，选择 `New repository`。
3. 填写：
   - `Repository name`：例如 `finguard-enterprise-audit`
   - `Description`：例如 `FinGuard enterprise financial anomaly audit`
   - `Visibility`：选择 **Private**
   - `Initialize this repository with`：**全部不要勾选**
4. 点击 `Create repository`。

创建完成后，先不要直接在网页手工输入代码。建议使用 GitHub Desktop 或
Git 命令上传整个目录。

### 方法 B：直接使用 GitHub Desktop 创建

1. 安装并登录 GitHub Desktop：<https://desktop.github.com/>。
2. 点击 `File` → `Add local repository`。
3. 选择本指南开头的 `outputs\finguard` 目录。
4. 如果提示该目录还不是 Git 仓库，选择在这里创建仓库。
5. 提交说明填写 `Initial FinGuard release`。
6. 点击 `Publish repository`。
7. 在发布窗口中勾选 `Keep this code private`，再点击 `Publish repository`。

## 3. 把代码传到 GitHub

### 方案 A：GitHub Desktop，推荐新手

1. 在 GitHub Desktop 左侧选择 FinGuard 本地仓库。
2. 检查 `Changes` 列表，确认只包含代码、测试、README 和合成样例。
3. 搜索确认没有 `.streamlit/secrets.toml`。
4. 在左下角填写提交说明，例如 `Deploy FinGuard to Streamlit Cloud`。
5. 点击 `Commit to main`。
6. 点击右上角 `Push origin`。
7. 点击 `Repository` → `View on GitHub`，确认网页上能看到 `app.py` 和
   `requirements.txt`。

### 方案 B：Git 命令

先安装 Git：<https://git-scm.com/download/win>。然后打开 PowerShell：

```powershell
cd D:\FinGuard_Project
git init
git add .
git status --short
git commit -m "Initial FinGuard release"
git branch -M main
git remote add origin https://github.com/你的GitHub用户名/finguard-enterprise-audit.git
git push -u origin main
```

执行 `git status --short` 后，一旦看到 `.streamlit/secrets.toml`，立即停止：
不要提交，先从待提交内容中移除该文件，并确认 `.gitignore` 生效。

GitHub 现在不接受账户密码进行 Git 推送。若提示认证失败，请使用：

- GitHub Desktop 登录；
- 或创建 GitHub Personal Access Token，并把 Token 当作密码；
- 或在 GitHub 网页上传文件作为最后备选方案。

### 方案 C：GitHub 网页上传

1. 打开刚创建的私有仓库。
2. 点击 `Add file` → `Upload files`。
3. 打开 Windows 文件资源管理器中的 `outputs\finguard`。
4. 按 `Ctrl+A` 选择全部文件和文件夹，再拖入 GitHub 上传区域。
5. 确认文件列表包含 `app.py`、`requirements.txt`、`finguard`、
   `.streamlit`、`sample_data` 和 `tests`。
6. 提交说明填写 `Initial FinGuard release`。
7. 点击 `Commit changes`。

网页上传后务必打开仓库根目录检查：`app.py` 必须直接位于根目录。

## 4. 注册并连接 Streamlit Community Cloud

1. 打开 <https://share.streamlit.io/>。
2. 点击使用 GitHub 登录。
3. 授权 GitHub OAuth 应用访问 Community Cloud。
4. 首次授权时通常先获得公开仓库访问权限。
5. 因为本项目使用私有仓库，必须继续授予私有仓库访问权限：
   - 在 GitHub 授权流程中允许 Streamlit 访问私有仓库；或
   - 打开 GitHub `Settings` → `Applications` → `Authorized OAuth Apps`；
   - 找到 `Streamlit`，进入授权设置并授予目标私有仓库访问权限。
6. 如果仓库属于 GitHub Organization，Organization Owner 可能还需要批准
   Streamlit 访问该组织。
7. 部署者必须对仓库具有 **Admin** 权限。普通只读权限不能发布应用。

如果 Streamlit 的仓库下拉框看不到私有仓库，优先回到这一步检查 OAuth 权限。

## 5. 创建 Streamlit 应用

1. 在 Streamlit Community Cloud 工作区点击 `Create app` 或 `New app`。
2. 选择 `Yup, I have an app`。
3. 填写：
   - `Repository`：选择刚创建的私有仓库
   - `Branch`：`main`
   - `Main file path`：`app.py`
4. 应用子域名会以仓库名和入口文件为基础生成，可以稍后在设置中修改。

## 6. 配置 DeepSeek Secrets

在点击最终发布按钮前，打开 `Advanced settings`，找到 `Secrets` 输入框。

粘贴以下内容，并把占位符替换成真实密钥：

```toml
DEEPSEEK_API_KEY = "粘贴你的DeepSeek密钥"
DEEPSEEK_MODEL = "deepseek-chat"
FINGUARD_AUTH_REQUIRED = true
FINGUARD_SESSION_HOURS = 8
FINGUARD_IDLE_MINUTES = 30
SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465
SMTP_USER = "你的QQ邮箱"
SMTP_AUTH_CODE = "QQ邮箱SMTP授权码"
SMTP_FROM = "你的QQ邮箱"
SMTP_SECURITY = "ssl"
```

格式要求：

- 键名是 `DEEPSEEK_API_KEY`，必须完全一致，区分大小写。
- 值必须使用双引号。
- 不要在密钥值中换行。
- 不要把密钥写进 `app.py`。
- 不要把真实 `.streamlit/secrets.toml` 提交 GitHub。
- 首次启动会要求创建企业空间和管理员；数据库若不挂载持久化磁盘，Community Cloud 重启后可能丢失。

保存 Secrets 后，如果界面允许选择 Python 版本，优先选择 Python 3.12。

## 7. 点击发布

1. 点击 `Deploy`。
2. 等待安装 `requirements.txt` 中的依赖。
3. 首次构建通常需要几分钟。
4. 发布成功后会得到类似下面的网址：

```text
https://你的应用子域名.streamlit.app
```

5. 打开网址，依次检查：
   - 看不到 Python Traceback；
   - 可以点击“载入合成演示数据”；
   - 两个 Plotly 图表正常显示；
   - 风险表显示深红和黄色高亮；
   - 能下载 Excel 报告；
   - 侧栏显示“已从 Streamlit Secrets 读取 DeepSeek 密钥”；
   - 高风险凭证能生成 AI 审计意见。

如果暂时没有 DeepSeek 密钥，规则评分和报告仍可用，但侧栏会明确提示缺少
`DEEPSEEK_API_KEY`，高风险凭证不会自动生成 AI 建议。

## 8. 发布后修改 Secrets

1. 打开 Streamlit 工作区。
2. 进入该应用的管理菜单。
3. 点击 `Settings`。
4. 找到 `Secrets`。
5. 修改后点击 `Save`。
6. 若密钥没有立即生效，使用 `Reboot app` 重启应用。

不要把修改后的 Secrets 截图发到公开群组。

## 9. 以后更新代码

GitHub Desktop：

1. 修改本地代码。
2. 在 GitHub Desktop 填写提交说明。
3. `Commit to main`。
4. `Push origin`。

Git 命令：

```powershell
git add .
git commit -m "Describe the update"
git push
```

Streamlit Community Cloud 会跟踪仓库的 `main` 分支并重新部署。

## 10. 常见错误与解决方法

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| 下拉框没有私有仓库 | GitHub OAuth 未授权私有仓库 | GitHub Settings → Applications → Streamlit，补授权 |
| 提示没有部署权限 | 不是仓库 Admin | 让仓库管理员提升权限或 fork 后部署 |
| `ModuleNotFoundError` | 仓库根目录不对或缺 `requirements.txt` | 确认根目录直接存在 `app.py` 和 `requirements.txt` |
| `KeyError: DEEPSEEK_API_KEY` | Secret 键名拼错或未保存 | 检查 `Advanced settings` / App Settings → Secrets |
| AI 返回 401 | DeepSeek Key 无效、失效或额度耗尽 | 在 DeepSeek 平台重新生成并更新 Secret |
| AI 返回模型不存在 | 模型 ID 不可用 | 修改 `DEEPSEEK_MODEL` 为账户实际可用模型 |
| 应用能打开但上传失败 | 文件超过上传限制 | 默认上限 25 MB，缩小文件或修改配置 |
| 次日首次访问很慢 | 免费实例空闲休眠 | 等待其自动唤醒；这是 Community Cloud 的正常行为 |
| 修改 Secrets 后仍报旧错误 | Streamlit 仍使用旧运行状态 | 点击 `Reboot app` |
| GitHub 推送要求密码 | GitHub 已禁用账户密码认证 | 使用 GitHub Desktop 或 Personal Access Token |

## 11. 上线前的安全检查

在 GitHub 仓库根目录执行：

```powershell
git status --short
git ls-files
```

确认输出中没有：

```text
.streamlit/secrets.toml
.env
.venv/
__pycache__/
*.pem
*.key
```

再到 GitHub 网页搜索仓库中的 `sk-`，确认真实密钥没有进入提交历史。

如果密钥曾经被提交：

1. 立即在 DeepSeek 平台撤销该密钥；
2. 创建新密钥；
3. 更新 Streamlit Secrets；
4. 仅在确有需要时清理 Git 提交历史；
5. 不要误以为删除当前文件就能从历史中删除密钥。

## 12. 免费版使用边界

- Community Cloud 免费工作区有资源、并发和空闲休眠限制，适合演示和小规模使用。
- 不要直接上传未经授权的真实财务和个人数据。
- 公网应用必须依法取得数据处理授权，限制上传权限，并告知 AI 字段会发送给 DeepSeek。
- 私有 GitHub 仓库只保护源代码可见性，不等于应用自动具备企业级 SSO、RBAC 或数据隔离。
- AI 结果是审计线索，必须人工复核，不能作为处分、移送或公开指控的唯一依据。

## 官方参考

- [连接 GitHub 与私有仓库授权](https://docs.streamlit.io/deploy/streamlit-community-cloud/get-started/connect-your-github-account)
- [部署 Community Cloud 应用](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)
- [Community Cloud Secrets 管理](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management)
- [DeepSeek API 文档](https://api-docs.deepseek.com/)
