# 公益站 多账号自动签到

多平台多账号自动签到工具，支持所有基于 NewAPI、OneAPI 的平台。内置支持 AnyRouter 与 AgentRouter，其他平台可根据文档自定义配置。

支持 Claude Sonnet 4.5、GPT-5-Codex、Claude Code 百万上下文、Gemini-2.5-Pro 等模型。

📢 **注册链接**：[AnyRouter](https://anyrouter.top/register?aff=0FzF)（限时送 100 美金） | [AgentRouter](https://agentrouter.org/register?aff=rLco)

**如果本项目对你有帮助，请点个 Star，感谢支持！⭐**

---

## 更新日志

### v2.7.0 (2026-01-02)

- 🐛 **修复 AgentRouter 签到机制**
  - 修正 AgentRouter 配置：移除错误的 WAF 绕过设置
  - AgentRouter 签到通过访问 `/api/user/self` 自动触发，无需独立签到 API
  - 仅需 session cookie，无需浏览器，本地和 GitHub Actions 均完全支持
- 📝 **更新技术文档**
  - 明确区分 AnyRouter（显式 API 签到）和 AgentRouter（自动签到）的机制差异
  - 更新 CLAUDE.md 开发说明，记录正确的签到实现方式

### v2.6.0 (2025-12-31)

- 📝 **GitHub Actions 运行说明**
  - 两个平台都优先使用 HTTP 方式签到（无需浏览器）
  - 只要 session cookie 有效，Actions 环境下 AnyRouter 和 AgentRouter 均可正常签到
  - 仅当 HTTP 签到失败时才回退到浏览器（AnyRouter: WAF 绕过；AgentRouter: OAuth 登录）
  - **注意**：浏览器回退在 Actions 中不可用，请确保 session cookie 有效
- 🧹 **仓库清理**
  - 删除重复的 `.env.template`（保留简洁版 `env.template`）
  - 删除调试文档和 Chrome 调试脚本（仅供开发用）
  - 精简项目结构，减少用户困惑

### v2.5.1 (2025-12-27)

- 🧹 **仓库清理**
  - 删除调试截图、缓存目录、重复 workflow 文件
  - 调试截图统一写入 `data/debug_screenshots/`（避免污染仓库根目录）
- 🪟 **Windows 脚本恢复**
  - 恢复 `scripts/run_checkin.bat`、`scripts/run_checkin_manual.bat`
  - 恢复 `scripts/setup_task.bat`、`scripts/setup_task.ps1`
- 🌐 **原生 Chrome 复用（CDP）**
  - 默认端口以 9222 为主，并自动探测 9222/9022
  - `scripts/start_chrome_debug.bat` 支持选择本机 Chrome Profile
  - 新增 `scripts/start_chrome_debug_mcp_profile.bat`（复用 chrome-devtools-mcp profile）

### v2.5.0 (2025-12-22)

- ✨ **新增 SQLite 数据库存储**
  - 账号配置、Provider 配置、签到记录统一存储到 `data/checkin.db`
  - 首次运行自动从环境变量和 `providers.json` 迁移数据
  - 保留环境变量加载作为后备（GitHub Actions 兼容）
- 🔒 **敏感数据脱敏**
  - 新增 `utils/masking.py` 脱敏模块
  - Session cookie 显示为 `abc...xyz` 格式
  - 密码显示为 `***`
- 🔧 **AnyRouter HTTP 优先策略**
  - 新增 `try_direct_http_signin()` 直连签到（无需 WAF cookies）
  - 被 WAF 拦截时自动回退到浏览器获取 cookies
  - 减少浏览器启动次数，提升效率

### v2.4.1 (2025-12-21)

- 🔧 **通知格式优化**
  - 通知内容与日志格式统一，信息更完整
  - 新增上次签到时间显示
  - 显示余额变化详情（$X → $Y (+$Z)）
  - 跳过账号显示剩余冷却时间

### v2.4.0 (2025-12-21)

- ✨ **新增 HTTP 签到方式**
  - 新增 `trigger_signin_via_http()` 函数，使用 httpx 直接发送请求
  - OAuth 账号优先使用 HTTP 方式签到，无需启动浏览器
  - 失败时自动回退到 Playwright 浏览器方式
- 🚀 **GitHub Actions 完全支持**
  - HTTP 签到无浏览器依赖，可在任何 CI/CD 环境运行
  - 解决了 GitHub Actions 中无法使用 OAuth 登录的问题
- 🔧 **签到流程优化**
  - 简化 AgentRouter 签到：访问登录页 + session cookie → 自动重定向 → 签到触发
  - 减少不必要的浏览器启动，提升执行效率

### v2.3.0 (2025-12-20)

- 🧹 **项目结构优化**
  - 运行时数据统一存放到 `data/` 目录（balance_hash.txt、signin_history.json、task_run.log）
  - 移除冗余的单文件目录（config/、docs/）
  - 提供 `env.template` 配置模板（复制为 `.env`）
  - 简化 `.gitignore`，整个 `data/` 目录被忽略
- 🔧 **代码清理**
  - 删除所有缓存目录（.pytest_cache、.ruff_cache、__pycache__）
  - 更新 `constants.py` 中的文件路径常量

### v2.2.0 (2025-12-18)

- 🔧 **修复定时任务执行问题**
  - 使用 uv 完整路径，解决系统环境变量找不到命令的问题
  - 启用批处理延迟变量扩展，正确处理错误码
  - 添加详细的诊断日志和环境检查
- ✨ **优化签到机制**
  - 修正 anyrouter 配置：改为登录自动签到模式（与 agentrouter 一致）
  - 增强浏览器模拟：访问 `/console/token` 和 `/console` 页面触发真实签到
  - 修改请求 Referer 为 `/console/token`，模拟 OAuth 回调
- 📊 **增加余额变化监控**
  - 签到前后自动对比余额变化
  - 显示详细的余额增减信息
  - 优化通知触发逻辑
- 🧹 **代码优化**
  - 简化日志输出，移除冗余调试信息
  - 清理临时测试文件
  - 改进错误处理和异常捕获

### v2.1.0 (2025-12-16)

- 🎨 重组项目文件结构，提升可维护性
  - 配置文件移至 `config/` 目录
  - 脚本文件移至 `scripts/` 目录
  - 文档文件移至 `docs/` 目录
  - 测试文件移至 `tests/` 目录
- 🗑️ 删除冗余脚本，只保留最优方案
- 📝 优化 README 文档，添加快速使用指南

### v2.0.0 (2025-12-16)

- ✨ 新增本地运行支持
- ✨ 全面汉化界面
- 🐛 修复 JSON 多行解析问题
- 🐛 修复 AgentRouter 签到逻辑错误
- 🐛 修复环境变量加载顺序问题（先加载 dotenv 再导入 notify）
- 📝 完善文档和配置说明
- 🔧 新增 Windows 定时任务自动配置脚本

### v1.0.0

- 🎉 初始版本
- ✅ GitHub Actions 支持
- ✅ 多账号签到
- ✅ WAF 绕过

---

## 📁 项目结构

```
anyrouter-check-in/
├── checkin.py                主程序
├── providers.json            Provider 配置
├── pyproject.toml            项目依赖
├── env.template              配置模板（复制为 .env）
├── scripts/                  脚本文件夹
│   ├── run_checkin.bat           运行脚本（定时任务用）
│   ├── run_checkin_manual.bat    手动运行（不闪退）
│   ├── setup_task.bat            一键设置定时任务
│   └── setup_task.ps1            定时任务创建逻辑
├── tests/                    测试文件夹
│   ├── test_config.py        配置验证
│   ├── test_notify.py        通知模块测试
│   └── test_result.py        结果模块测试
├── utils/                    工具模块
│   ├── config.py             配置管理
│   ├── constants.py          常量定义
│   ├── database.py           SQLite 数据库操作
│   ├── masking.py            敏感数据脱敏
│   ├── notify.py             通知模块
│   └── result.py             签到结果管理
└── data/                     运行时数据（自动生成，已忽略）
    ├── balance_hash.txt      余额哈希
    ├── checkin.db            SQLite 数据库（本地）
    ├── signin_history.json   签到历史
    └── task_run.log          运行日志
```

---

## 🚀 快速使用（4步）

```bash
# 1. 安装依赖
uv sync --dev

# 2. 复制配置模板并编辑（敏感文件不要上传）
copy env.template .env
notepad .env

# 3. 手动运行测试（不会闪退）
scripts\run_checkin_manual.bat

# 4. 右键“以管理员身份运行”设置定时任务
scripts\setup_task.bat
```

**详细教程请继续阅读下文** ↓

---

## 功能特性

- ✅ 多平台支持（兼容 NewAPI 与 OneAPI）
- ✅ 多账号批量签到
- ✅ 本地运行（24小时开机用户推荐，定时精准）
- ✅ GitHub Actions（免费云端运行，无需服务器）
- ✅ 自动绕过 WAF 防护
- ✅ 多种通知方式（钉钉、飞书、Telegram 等）
- ✅ 中文界面，清晰易懂

---

## 快速开始

### 方式一：本地运行（推荐 24 小时开机用户）

**适合场景**：

- 电脑 24 小时开机
- 需要精准定时执行
- 希望完全掌控运行环境

**5 分钟完成配置：**

```bash
# 1. 克隆项目
git clone https://github.com/KimYx0207/anyrouter-check-in.git
cd anyrouter-check-in

# 2. 安装 uv（Python 包管理器）
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 3. 安装依赖
uv sync --dev
uv run playwright install chromium

# 4. 配置账号
copy env.template .env
notepad .env

# 5. 测试运行
uv run checkin.py

# 6. 设置定时任务（右键"以管理员身份运行"）
scripts\setup_task.bat
```

**配置完成后**：Windows 任务计划程序将每 6 小时自动运行签到（00:00、06:00、12:00、18:00）

**注意事项**：

- 首次运行会弹出 Chrome 浏览器窗口（用于获取 WAF cookies），这是正常现象
- 签到失败或余额变化时会自动发送通知（如已配置通知渠道）
- `.env` 文件包含敏感信息，不要分享或上传到公共平台

---

### 方式二：GitHub Actions（推荐无固定电脑用户）

**适合场景**：

- 电脑不是 24 小时开机
- 希望完全免费自动化
- 不想占用本地资源

**步骤：**

#### 1. Fork 本仓库

点击页面右上角的 "Fork" 按钮

#### 2. 配置 GitHub Secrets

1. 进入你 Fork 的仓库，点击 `Settings` → `Environments`
2. 点击 `New environment`，创建名为 `production` 的环境
3. 在 `production` 环境中，点击 `Add environment secret`
4. 添加 Secret：
   - **Name**: `ANYROUTER_ACCOUNTS`
   - **Value**: 你的账号配置（JSON 格式，见下方示例）

#### 3. 启用 Actions

1. 点击仓库的 `Actions` 标签
2. 如果提示启用，点击 `Enable workflow`
3. 找到 "公益站 自动签到" workflow
4. 点击 `Run workflow` 进行首次测试

---

## 配置说明

### 获取账号信息

访问 [anyrouter.top](https://anyrouter.top) 或 [agentrouter.org](https://agentrouter.org)，登录后按 `F12` 打开开发者工具：

**获取 Session Cookie：**

1. 切换到 `Application` 标签
2. 左侧选择 `Cookies` → 选择当前网站
3. 找到 `session` 项，复制其 `Value` 值

**获取 API User：**

1. 切换到 `Network` 标签
2. 过滤类型选择 `Fetch/XHR`
3. 刷新页面，点击任意 API 请求
4. 在请求头中找到 `new-api-user` 或 `New-Api-User`
5. 复制该值（通常是 5 位数字）

### 账号配置格式

**单账号示例：**

```json
[{"name":"我的账号","provider":"anyrouter","cookies":{"session":"你的session值"},"api_user":"你的api_user"}]
```

**多账号示例：**

```json
[{"name":"账号1","provider":"anyrouter","cookies":{"session":"session1"},"api_user":"12345"},{"name":"账号2","provider":"agentrouter","cookies":{"session":"session2"},"api_user":"67890"}]
```

**字段说明：**

- `name`（可选）：账号显示名称，用于日志和通知
- `provider`（可选）：平台类型，默认 `anyrouter`，可选 `agentrouter`
- `cookies`（必需）：包含 session 的对象
- `api_user`（必需）：API 用户标识符

**重要提示：**

- GitHub Actions 中配置时，JSON 必须是**单行格式**
- 本地 `.env` 文件中现已支持多行格式（会自动清理）

---

## 通知配置（可选）

编辑 `.env` 文件，取消对应服务的注释并填入 token：

```bash
# 钉钉机器人
DINGDING_WEBHOOK=https://oapi.dingtalk.com/robot/send?access_token=你的token

# 飞书机器人
FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/你的token

# Telegram Bot
TELEGRAM_BOT_TOKEN=你的bot_token
TELEGRAM_CHAT_ID=你的chat_id

# 企业微信机器人
WEIXIN_WEBHOOK=你的webhook地址

# PushPlus
PUSHPLUS_TOKEN=你的token

# Server酱
SERVERPUSHKEY=你的sendkey

# Gotify
GOTIFY_URL=你的服务器地址
GOTIFY_TOKEN=你的token
GOTIFY_PRIORITY=9

# 邮件通知
EMAIL_USER=发件人邮箱
EMAIL_PASS=邮箱授权码
EMAIL_TO=收件人邮箱
CUSTOM_SMTP_SERVER=smtp.gmail.com:587
```

**通知触发条件：**

- 签到失败
- 账号余额发生变化
- 首次运行

---

## 自定义 Provider（可选）

内置的 `anyrouter` 和 `agentrouter` 无需额外配置。如需添加其他平台：

**基础配置（仅需域名）：**

```json
{
  "customrouter": {
    "domain": "https://custom.example.com"
  }
}
```

**完整配置（自定义路径）：**

```json
{
  "customrouter": {
    "domain": "https://custom.example.com",
    "login_path": "/auth/login",
    "sign_in_path": "/api/checkin",
    "user_info_path": "/api/profile",
    "api_user_key": "x-user-id",
    "bypass_method": "waf_cookies",
    "waf_cookie_names": ["acw_tc", "cdn_sec_tc"]
  }
}
```

将配置添加到环境变量 `PROVIDERS` 中（GitHub Actions）或 `.env` 文件中（本地运行）。

---

## 定时任务说明

### 本地运行

- 使用 Windows 任务计划程序
- 默认每 6 小时执行一次
- 右键运行 `scripts\setup_task.bat` 可自动配置

### GitHub Actions

- 默认每 6 小时触发一次
- 可能延迟 1-2 小时（GitHub 服务限制）
- 编辑 `.github/workflows/checkin.yml` 可调整 cron 表达式

---

## 常见问题

### 1. 签到成功但余额没有增加

**原因**：每24小时只能签到一次

**解决方案**：

1. 检查上次签到时间（手动登录网站查看）
2. 等待24小时冷却期后再次运行脚本
3. 查看脚本输出的余额变化提示：
   - `[成功] 余额增加了 $X` - 签到成功
   - `[警告] 余额未变化，可能今日已签到` - 24小时内重复签到

### 2. 脚本运行成功但未收到飞书通知

**原因**：通知仅在失败或余额变化时发送

**正常情况**：

- 如果所有账号签到成功且余额无变化，不会发送通知
- 只有首次运行、签到失败、或余额变化时才发送通知

### 3. 签到失败，提示 401 错误

**原因**：Cookies 已过期（有效期约 1 个月）

**解决方案**：

1. 重新登录 anyrouter.top 或 agentrouter.org
2. 按 F12 获取新的 session 值
3. 更新 `.env` 文件中的 session

### 4. JSON 解析错误

**原因**：配置中包含未转义的控制字符

**解决方案**：

- 确保 JSON 配置在一行内（GitHub Actions）
- 本地 `.env` 文件现已支持多行格式
- 使用在线工具验证 JSON 格式：https://jsonlint.com/

### 5. 浏览器窗口一直不关闭

**原因**：网络延迟或 WAF 检测

**解决方案**：

- 等待 30 秒，通常会自动完成
- 如果超时，手动关闭浏览器即可
- 检查网络连接是否正常

### 6. 定时任务未运行

**检查步骤**：

1. 按 `Win + R`，输入 `taskschd.msc` 打开任务计划程序
2. 找到"公益站自动签到"任务
3. 查看"上次运行结果"和"下次运行时间"
4. 右键任务 → 运行，测试是否正常
5. 查看日志文件：`anyrouter-check-in\data\task_run.log`

**常见原因**：

- uv命令未找到（已修复：使用完整路径）
- 权限不足（使用"最高权限"运行）
- 工作目录错误（已修复：切换到项目根目录）

---

## 测试验证

运行配置测试脚本，验证配置是否正确：

```bash
uv run python tests/test_config.py
```

成功输出示例：

```
[成功] 成功加载 2 个 provider 配置
[成功] 成功加载 3 个账号配置
[成功] 所有配置验证通过！
```

---

## 手动运行测试

```bash
# 方式1：使用 Python 直接运行
uv run checkin.py

# 方式2：使用批处理脚本
scripts\run_checkin.bat manual
```

**预期结果：**

- 浏览器窗口自动打开（获取 WAF cookies）
- 显示各账号签到状态和余额信息
- 输出 `[成功] 所有账号签到成功！`

---

## 技术细节

### 签到机制说明

**重要：AnyRouter 和 AgentRouter 采用不同的签到机制**

| 平台 | 签到方式 | 签到 API | WAF 绕过 | 浏览器依赖 |
|------|---------|---------|---------|-----------|
| **AnyRouter** | 显式 API 调用 | `/api/user/sign_in` | 需要（acw_tc, cdn_sec_tc, acw_sc__v2） | 需要（Playwright） |
| **AgentRouter** | 自动触发 | 无（通过 `/api/user/self` 触发） | 不需要 | 不需要 |

**AnyRouter 签到流程：**
1. 使用 Playwright 访问登录页获取 WAF cookies
2. 合并 WAF cookies + session cookie
3. 调用 `/api/user/sign_in` 接口完成签到

**AgentRouter 签到流程：**
1. 仅需 session cookie
2. 访问 `/api/user/self`（用户信息接口）时自动触发签到
3. 无需浏览器，本地和 GitHub Actions 均完全支持

**通用说明：**
- **签到周期**：每24小时可签到一次
- **奖励发放**：签到成功后余额自动增加（约$0.01-$25不等）
- **结果判断**：基于余额变化判断签到是否成功

### 脚本工作原理

本脚本根据不同平台采用不同策略：

**AnyRouter（需要 WAF 绕过）：**
1. 使用 Playwright 访问登录页获取 WAF cookies
2. 合并 WAF cookies 与用户 session cookie
3. 调用 `/api/user/sign_in` 接口
4. 对比签到前后余额验证结果

**AgentRouter（自动签到）：**
1. 直接使用 session cookie 发起 HTTP 请求
2. 访问 `/api/user/self` 接口（自动触发签到）
3. 对比签到前后余额验证结果

### WAF 绕过机制

- 使用 Playwright 自动化浏览器
- 访问登录页面获取 WAF cookies（如 acw_tc）
- 将 WAF cookies 与用户 cookies 合并后发起请求

### 通知策略

- 仅在签到失败或余额变化时发送通知
- 使用 SHA256 hash 跟踪余额变化
- 支持多种通知渠道并发推送

---

## 注意事项

1. **Cookies 有效期**：约 1 个月，过期后需重新获取
2. **定时间隔**：AnyRouter 签到周期为 24 小时，每 6 小时运行可确保不漏签
3. **浏览器窗口**：签到时会短暂弹出 Chrome 窗口（用于绕过 WAF），属于正常现象
4. **GitHub Actions 延迟**：可能延迟 1-2 小时，但不影响签到有效性
5. **安全性**：不要在公共场合分享 `.env` 文件或 session 值

---

## 故障排查

### 查看运行日志

**本地运行：**

```
Win + R → taskschd.msc → 找到"公益站自动签到" → 查看历史记录
```

**GitHub Actions：**

```
仓库 → Actions 标签 → 点击最近的运行记录
```

### 取消定时任务

**本地运行：**

```
Win + R → taskschd.msc → 右键"公益站自动签到" → 删除
```

**GitHub Actions：**

```
仓库 → Actions → 选择 workflow → Disable workflow
```

---

## 开发与测试

```bash
# 安装开发依赖
uv sync --dev

# 运行测试
uv run pytest tests/

# 代码格式化
uv run ruff check .
```

---

## 许可证

MIT License

---

## 免责声明

本项目仅用于学习和研究目的，使用前请确保遵守相关网站的服务条款。

---

## 常用命令速查

```bash
# 复制配置文件
copy env.template .env

# 手动运行签到
uv run checkin.py

# 测试配置是否正确
uv run python tests/test_config.py

# 设置定时任务（右键"以管理员身份运行"）
scripts\setup_task.bat
```

---

## 相关项目

- [Auo](https://github.com/millylee/auo) - 支持任意 Claude Code Token 切换的工具
