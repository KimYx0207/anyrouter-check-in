# GitHub Actions 配置指南

本文档说明如何配置 GitHub Actions 实现自动签到。

## 📋 前置准备

1. Fork 本项目到你的 GitHub 账号
2. 获取账号的 session cookie（见下方说明）

## 🔑 获取 Session Cookie

### 方法一：浏览器开发者工具

1. 登录 AnyRouter 网站
2. 按 F12 打开开发者工具
3. 切换到 "Application" 或 "存储" 标签
4. 左侧找到 "Cookies" → 选择网站域名
5. 找到名为 `session` 的 cookie，复制其值

### 两个 GitHub 登录账号

分别在独立浏览器配置或无痕窗口中，通过 GitHub 登录 AnyRouter。在 `anyrouter.top` 域名下获取 `session`，再从该站点的请求头获取同一账号的 `new-api-user`，填入 `api_user`。

这里需要的是 **AnyRouter 的 Cookie**。GitHub 域名下的 Cookie、GitHub Token 或 GitHub 密码不能代替它。脚本不会自动刷新 GitHub OAuth 登录后已经过期的 AnyRouter session。

如果已在站点绑定独立的邮箱密码，也可以配置 `email` + `password`，由浏览器登录后取得 session；仅有 GitHub 登录的账号继续使用 Cookie 方式。

### AgentRouter 的 GitHub 登录账号

AgentRouter 查询 `/api/user/self` 成功只证明会话有效，不能作为每日奖励到账证明。对于 GitHub 登录账号，脚本先读取平台额度，再通过 GitHub OAuth 重新登录，并核验返回的平台账号 ID。只有“剩余额度 + 已用额度”增加时才记录奖励并开启 24 小时冷却；未观察到奖励则明确失败，不写入新的成功历史。旧版本由普通查询产生的冷却记录不再用于跳过该平台。

如果本地历史丢失，脚本会先核验已认证账号的服务端签到奖励日志，仅对 24 小时内明确记录的正数奖励恢复原始冷却时间。恢复记录不计为本轮新增奖励。某个账号失败时，其他已完成账号的历史仍会保存，避免下一轮重复登录。

在对应的 `ANYROUTER_ACCOUNTS` 项中添加 `github_cookies`，保留原有平台 `cookies` 和 `api_user`：

```json
{
  "provider": "agentrouter",
  "name": "AgentRouter主账号",
  "api_user": "12345",
  "cookies": {"session": "该平台账号的有效session"},
  "github_cookies": {
    "user_session": "对应GitHub账号的登录Cookie",
    "__Host-user_session_same_site": "同一GitHub账号的对应Cookie"
  }
}
```

GitHub 登录 Cookie 具有账号登录权限，需要账号所有者明确授权后才能保存到云端 Secret。两个账号必须分别配置，`gh` 的 OAuth token 不能代替浏览器 Cookie。代码只向 `github.com` 注入必要的 Cookie，使用临时浏览器上下文，不保存 GitHub profile、截图、OAuth 回调地址或响应正文。登录失效或遇到 GitHub 人工验证时，工作流明确失败，需要更新对应会话。原平台 session 也需有效，才能在重新登录前读取奖励基线。

完成 GitHub 设备验证后，`github_cookies` 可同时包含同一浏览器会话的 `_device_id`，以保留与该登录态对应的设备标识。

每轮运行上传 `proxy-refresh-运行编号` 和 `checkin-proof-运行编号` 两份脱敏回执。前者证明节点刷新，后者按配置顺序记录结果、额度和 `rewardVerified`；Actions 绿灯或冷却状态均不能单独证明本轮获得了奖励。

## ⚙️ 配置 GitHub Secrets

进入你 Fork 的仓库，依次点击：`Settings` → `Environments` → `production` → `Environment secrets`，更新 `ANYROUTER_ACCOUNTS`。

Workflow 使用 `production` 环境；同名环境 Secret 会覆盖仓库 Secret，应在这里更新已有的两个账号配置。

### 必需配置

#### ANYROUTER_ACCOUNTS

账号配置，JSON 数组格式：

```json
[
  {
    "provider": "anyrouter",
    "api_user": "你的API用户ID",
    "cookies": {
      "session": "你的session cookie值"
    },
    "name": "AnyRouter主账号"
  }
]
```

**多账号示例：**

```json
[
  {
    "provider": "anyrouter",
    "api_user": "12345",
    "cookies": {"session": "abc123..."},
    "name": "主账号"
  },
  {
    "provider": "anyrouter",
    "api_user": "67890",
    "cookies": {"session": "def456..."},
    "name": "备用账号"
  }
]
```

### 可选配置（通知）

根据需要配置以下任意通知渠道：

| Secret 名称 | 说明 | 获取方式 |
|------------|------|---------|
| `DINGDING_WEBHOOK` | 钉钉机器人 Webhook | 钉钉群设置 → 智能群助手 → 添加机器人 |
| `FEISHU_WEBHOOK` | 飞书机器人 Webhook | 飞书群设置 → 群机器人 → 添加机器人 |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | 与 @BotFather 对话创建 |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID | 与 @userinfobot 对话获取 |
| `WEIXIN_WEBHOOK` | 企业微信 Webhook | 企业微信群 → 添加群机器人 |
| `PUSHPLUS_TOKEN` | PushPlus Token | [pushplus.plus](http://www.pushplus.plus/) 注册获取 |
| `SERVERPUSHKEY` | Server酱 Key | [sct.ftqq.com](https://sct.ftqq.com/) 注册获取 |
| `GOTIFY_URL` | Gotify 服务器地址 | 自建 Gotify 服务器 |
| `GOTIFY_TOKEN` | Gotify Token | Gotify 管理面板创建 |
| `EMAIL_USER` | 发件邮箱地址 | 你的邮箱 |
| `EMAIL_PASS` | 邮箱授权码 | 邮箱设置中获取 |
| `EMAIL_TO` | 收件邮箱地址 | 接收通知的邮箱 |

## 🚀 启用 GitHub Actions

1. 进入仓库的 `Actions` 标签
2. 如果看到提示，点击 "I understand my workflows, go ahead and enable them"
3. 找到 "AnyRouter 自动签到" workflow
4. 点击 "Enable workflow"

## ⏰ 运行时间

默认配置为每 6 小时运行一次（UTC 时间 00:00、06:00、12:00、18:00）

对应北京时间：08:00、14:00、20:00、02:00

### 修改运行时间

编辑 `.github/workflows/checkin.yml` 文件中的 cron 表达式：

```yaml
schedule:
  - cron: "0 */6 * * *"  # 每6小时
  # - cron: "0 0,12 * * *"  # 每天00:00和12:00（UTC）
  # - cron: "0 2 * * *"  # 每天02:00（UTC）
```

## 🧪 手动测试

配置完成后，可以手动触发一次测试：

1. 进入 `Actions` 标签
2. 选择 "AnyRouter 自动签到" workflow
3. 点击 "Run workflow" → "Run workflow"
4. 等待执行完成，查看日志

## ❓ 常见问题

### Q: 为什么签到失败？

**A:** 检查以下几点：
1. Session cookie 是否过期（需要重新获取）
2. API_USER 是否正确（在网站个人中心查看）
3. Secrets 配置格式是否正确（JSON 格式）

### Q: 如何查看执行日志？

**A:** `Actions` 标签 → 选择具体的运行记录 → 点击 "Run check-in" 查看详细日志

### Q: Cookie 多久会过期？

**A:** 通常约一个月，也可能提前失效。若两个接口均返回 HTTP 401，应重新登录 AnyRouter 并更新同一账号的 session 和 api_user；同步代码无法刷新失效凭据。

### Q: 可以添加其他平台吗？

**A:** 可以。通过 `PROVIDERS` Secret 添加服务商配置，再在 `ANYROUTER_ACCOUNTS` 中引用其名称。内置服务商允许只覆盖部分字段，例如 `{"agentrouter":{"use_proxy":true}}`。

## 🔒 安全说明

- 账号凭据配置在 GitHub Secrets；Cookie 签到使用临时浏览器上下文。
- 邮箱密码登录可按 provider 的 `persist_profile` 设置缓存浏览器登录状态。
- ✅ 代码中不包含任何敏感信息
- ✅ 运行日志会自动脱敏，不会泄露完整 cookie
- ⚠️ 不要将 Secrets 内容分享给他人
- ⚠️ 定期更新 cookie（建议每月重新获取一次）

## 📞 获取帮助

遇到问题？

1. 查看 [README.md](README.md) 了解项目详情
2. 查看 [Issues](../../issues) 搜索类似问题
3. 提交新的 Issue 描述你的问题
