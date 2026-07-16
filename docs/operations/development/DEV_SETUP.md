# Dev Setup

> 从零开始在 Windows 11 上把 Aletheia 跑起来。

---

## 先决条件

| 工具 | 用途 | 安装 |
|------|------|------|
| `uv` | Python 包管理（禁用 pip/poetry）| `winget install astral-sh.uv` |
| Node.js 20+ | 前端构建 | `winget install OpenJS.NodeJS.LTS` |
| Git | 版本控制 | 已有 |
| Ollama（可选）| 本地 LLM | `winget install Ollama.Ollama` |

---

## 首次安装

```powershell
# 1. 克隆 + 进入
git clone https://github.com/iridite/aletheia-trading-os.git
cd aletheia-trading-os

# 2. 安装 Python 依赖
uv sync

# 3. 验证后端可导入
uv run python -c "import aletheia.risk.engine"

# 4. 构建前端（必须，否则 gateway 启动报错）
cd frontend && npm install && npm run build && cd ..

# 5. 配置凭证（通过 CLI，不要手动编辑文件）
uv run aletheia configure

# 6. 验证安装
uv run aletheia doctor
```

---

## 凭证配置

凭证存在 Windows 凭据管理器中，**不写入任何文件/git**：

```powershell
# 通过 Web UI Settings 页面配置（推荐），或：
uv run aletheia configure
```

需要配置的项目：
- Bitget API Key / Secret / Passphrase（demo + live 各一套）
- LLM provider API key（OpenAI / Anthropic / Ollama 本地无需）

---

## 日常开发

```powershell
# pull 后同步依赖（幂等、无变更时 <1s）
uv sync

# 启动 gateway（后台三进程）
uv run aletheia gateway start --no-browser

# 开发模式（Vite watch + 后端热重载）
.\scripts\dev.ps1

# 查看健康状态
uv run aletheia doctor

# 跑测试（必须 uv run，裸 python 可能缺依赖）
uv run pytest tests/ -q --tb=line
```

访问 `http://127.0.0.1:8787/` — **不要用 :5173 作为日常入口**。

---

## 代码风格

| 项目 | 规则 |
|------|------|
| Python | 无强制 formatter，遵循现有代码风格 |
| 类型 | Pydantic models 定义 schema，不写裸 dict |
| 注释 | 只在 WHY 非显然时写；禁止写 WHAT 注释 |
| 异步 | `async def` handler 禁止同步阻塞（见 AGENTS.md 性能规则）|
| Symbol | 内部 canonical（`BTC/USDT`），显示走 `fmtSymbol()`|

---

## 提交规范

格式：`type(scope): subject`

| type | 用途 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档变更 |
| `chore` | 工具/脚本/依赖 |
| `refactor` | 重构（无行为变更）|
| `test` | 测试 |
| `spec` | SDD claim 变更 |

Body 写**根因 + 策略 + 逐文件改动**（见 feedback-detailed-commit-messages 记忆）。

---

## 数据库迁移

项目无正式迁移框架（SQLite，schema 在 `storage/sqlite.py` 中定义）。

- 新增表：在 `storage/sqlite.py` 的 `_create_tables()` 里加 `CREATE TABLE IF NOT EXISTS`
- 新增列：同上，加 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
- **必须同步更新** `docs/design/data/data_model.md`（SDD Rule §7）
- 禁止 DROP TABLE / DROP COLUMN（数据安全）

---

## 环境变量 / 配置

运行时配置通过当前用户的 `~/.aletheia/config.yaml` 管理（仓库外，通过 Web UI 修改）。

新增 config 字段：
1. 在 `aletheia/config/schema.py` 加字段
2. 同步在前端 Settings 页面加控件（Config ↔ UI 铁律）

**不得**直接编辑 `~/.aletheia/config.yaml`——用 Web UI 或 API。
