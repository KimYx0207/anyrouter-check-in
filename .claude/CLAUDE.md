# AnyRouter Auto Check-in - Claude Code 项目指令

## Git Commit 前置检查（必须执行）

在执行任何 `git commit` 命令之前，**必须**完成以下步骤：

### 1. 批判性思考：评估本次修改

在更新 README 之前，先回答以下问题：

**重要性评估：**
- 这是功能性修改还是仅仅是代码清理？
- 用户会从这个修改中获得什么价值？
- 这个修改是否会影响用户的使用方式？

**风险评估：**
- 这个修改是否可能破坏现有功能？
- 是否需要用户更新配置？
- 是否有向后兼容性问题？

**文档必要性：**
- 这个修改是否需要更新 README？
- 如果只是微小的代码优化（typo、注释、格式），可以跳过 README 更新
- 如果是功能性修改、bug 修复、新特性，必须更新 README

### 2. 更新 README.md（如果需要）

如果评估结果显示需要更新 README，按以下格式在 `## 更新日志` 部分的**最前面**添加新版本：

```markdown
### vX.X.X (YYYY-MM-DD)

- [类型] **修改标题**
  - 具体修改内容1
  - 具体修改内容2
```

**类型标记：**
- ✨ 新功能 (feat)
- 🐛 Bug 修复 (fix)
- 🔧 配置/优化 (chore)
- 📝 文档 (docs)
- 🧹 代码清理 (refactor)
- 🚀 性能优化 (perf)
- 🔒 安全修复 (security)

**版本号规则：**
- 新功能：中版本号 +1（如 2.3.0 → 2.4.0）
- Bug 修复/优化：小版本号 +1（如 2.3.0 → 2.3.1）
- 破坏性变更：大版本号 +1（如 2.3.0 → 3.0.0）

### 3. 生成 Commit Message

使用约定式提交格式：

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

**示例：**
```
feat(signin): 添加 HTTP 签到方式，支持 GitHub Actions

- 新增 trigger_signin_via_http() 函数
- 优先使用 HTTP 请求，失败时回退到浏览器
- 移除对 Playwright 的强依赖
```

---

## 项目概览

**AnyRouter Auto Check-in** - 多平台多账号自动签到工具

### 核心模块

| 文件 | 职责 |
|------|------|
| `checkin.py` | 主程序入口，签到流程控制，WAF 绕过 |
| `utils/config.py` | 配置管理（账号、Provider） |
| `utils/result.py` | 签到结果、历史记录管理 |
| `utils/notify.py` | 多渠道通知推送 |
| `utils/constants.py` | 常量定义 |
| `utils/database.py` | SQLite 数据库操作 |
| `utils/masking.py` | 敏感数据脱敏 |

### 签到机制

| 平台 | 签到方式 | 签到 API | WAF 绕过 |
|------|---------|---------|---------|
| **AnyRouter** | 显式 API 调用 | `/api/user/sign_in` | 需要（acw_tc, cdn_sec_tc, acw_sc__v2） |
| **AgentRouter** | 自动触发 | 无（通过 `/api/user/self` 触发） | 不需要 |

### 签到策略

```
AnyRouter:
1. Playwright 获取 WAF cookies
2. 合并 WAF cookies + session cookie
3. 调用 /api/user/sign_in 接口

AgentRouter:
1. 仅需 session cookie
2. 访问 /api/user/self 自动触发签到
```

---

## 代码规范

- 类型提示：所有函数必须有参数和返回值类型
- 文档字符串：所有公共函数必须有 docstring
- 错误处理：使用具体异常，记录上下文
- 日志格式：`[标签] 账号名: 消息内容`
