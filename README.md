# 每日自动 AI 投资早报（GitHub Actions + OpenAI Responses API + 企业微信群机器人）

这个仓库用于：
- 每天自动生成一份《每日投资情报早报》（中文）
- 自动推送到企业微信群机器人（WeCom webhook）
- 支持 GitHub Actions 手动触发
- 支持三种输出模式：`markdown`、`image` 和 `card`

> 触发方式仅包含：`schedule`（每天一次）+ `workflow_dispatch`（手动触发）。

---

## 1. 功能概览

- **定时触发**：每天 `00:30 UTC` 执行（即新加坡/北京时间 `08:30`）。
- **手动触发**：可在 Actions 页面点击 **Run workflow**，并选择：
  - `mode=markdown`：发送 Markdown 排版消息
  - `mode=image`：先生成 PNG 图片并发送；若图片发送失败，自动降级发送 Markdown
  - `mode=card`：生成网页并发送企业微信图文卡片（news），点击后查看网页
  - `force=true/false`：是否强制生成（会传递给生成逻辑）
- **模型与检索**：固定使用 `GPT-5.2` + `web_search` 工具生成，避免依赖 Tasks 邮件。

---

## 2. 仓库结构

```text
.github/workflows/briefing.yml   # 工作流（定时 + 手动）
src/main.py                      # 主程序：生成、压缩、渲染图片、推送企业微信
prompts/daily_briefing.md        # 可编辑 Prompt（强烈建议按你的策略迭代）
assets/latest_briefing.png       # 最近一次生成的图片（mode=image时产出）
assets/latest_briefing.html      # 最近一次生成的网页（mode=card时产出）
requirements.txt                 # Python 依赖
```

---

## 3. 快速开始（一步步）

### 第一步：Fork/克隆并推送到你的 GitHub 仓库

确保默认分支包含本项目文件。

### 第二步：配置 GitHub Secrets

进入仓库 **Settings → Secrets and variables → Actions → New repository secret**，添加：

1. `OPENAI_API_KEY`：OpenAI API Key
2. `WECOM_WEBHOOK`：企业微信群机器人 webhook（完整 URL）

> 如果你使用 OpenAI 协议兼容平台（如自建网关/第三方模型平台），可以改用：
> - `OPENAI_COMPAT_API_KEY`：兼容平台 API Key（优先级高于 `OPENAI_API_KEY`）
> - `OPENAI_COMPAT_BASE_URL`：兼容平台 Base URL（例如 `https://your-endpoint.example.com/v1`）

### 第三步（可选）：配置 GitHub Variables

在 **Settings → Secrets and variables → Actions → Variables** 添加：

- `DEFAULT_MODE`：`markdown` / `image` / `card`（默认建议 `markdown`）
- `BRIEFING_PUBLIC_URL`：`mode=card` 时卡片跳转的公网网页地址
- `BRIEFING_COVER_URL`：`mode=card` 可选封面图 URL
- `MAX_CHARS`：Markdown 最大长度，默认 `1500`
- `OPENAI_BASE_URL`（可选）：OpenAI 官方 SDK 支持的 Base URL（若未设置 `OPENAI_COMPAT_BASE_URL`，会读取此变量）

### 第四步：手动触发一次验证

1. 打开 **Actions → Daily AI Briefing → Run workflow**
2. 选择：
   - `mode=markdown`（建议先测这个）
   - `force=false`
3. 点击运行，检查日志与企业微信群消息是否正常。

---

## 4. 如何修改 Prompt（重点）

Prompt 在：`prompts/daily_briefing.md`。

你可以直接编辑该文件来调整：
- 关注主题权重（例如更偏宏观/更偏科技）
- 输出口吻（更交易化/更策略化）
- 风险评分规则
- 字数控制策略

主程序每次运行都会读取这个文件内容作为系统提示词，因此无需改代码即可调整策略。

---

## 5. 如何切换输出模式

### 方式 A：手动触发时选择
在 `workflow_dispatch` 的 `mode` 输入里选择：
- `markdown`
- `image`
- `card`

### 方式 B：设置默认模式
配置仓库 Variable：
- `DEFAULT_MODE=image`（或 `markdown`）

当定时任务运行时，若没有手动输入，会使用 `DEFAULT_MODE`，否则默认 `markdown`。

---

## 6. 如何修改定时生成时间（cron 与时区换算）

工作流在 `.github/workflows/briefing.yml` 中使用 cron（**UTC 时区**）：

```yaml
schedule:
  - cron: '30 0 * * *'
```

含义：每天 UTC 00:30 运行。

时区换算：
- 新加坡（SGT, UTC+8）= 08:30
- 北京时间（CST, UTC+8）= 08:30

如果你想改为北京时间每天 07:00，则对应 UTC 前一天/当天 `23:00`，即：

```yaml
cron: '0 23 * * *'
```

---

## 7. 成本预估（按月，人民币区间）

按“每天 1 次生成、至少 5 次 web_search、典型输出 <=1500字”估算：

- **保守区间**：约 **¥40 ~ ¥180 / 月**
- **中高波动区间**（搜索抓取文本更长、推理更深）：约 **¥180 ~ ¥450 / 月**

> 说明：实际费用取决于 OpenAI 实时计费策略与模型单价，以上为经验估算区间而非承诺价格。

主要成本变量：
1. `web_search` 次数（5 次 vs 6~8 次）
2. 每次检索抓取到的文本长度（长文本会抬高输入 token）
3. 输出字数与结构复杂度
4. 重试次数（网络抖动/API失败导致重复请求）

---

## 8. 常见故障排查

### 8.1 web_search 失败
- 现象：日志显示 OpenAI 调用失败。
- 已实现：自动重试 2 次（指数退避）。
- 处理建议：
  1. 检查 `OPENAI_API_KEY` 是否有效
  2. 若使用兼容平台，检查 `OPENAI_COMPAT_BASE_URL` 是否为 OpenAI 协议兼容的 `/v1` 接口
  3. 稍后重跑 workflow（可能是临时网络波动）

### 8.2 企业微信发送失败
- 现象：日志报 `errcode != 0` 或 HTTP 非 200。
- 已实现：
  - `mode=image` 失败时自动降级 `markdown`
  - 最终发送失败会让 job 直接失败（便于告警）
- 处理建议：
  1. 检查 `WECOM_WEBHOOK` 是否完整
  2. 检查群机器人是否仍可用、是否被安全策略限制

### 8.3 内容超长被截断
- 现象：消息过长，阅读体验差。
- 已实现：
  - 自动长度控制（默认 `<=1500`）
  - 超长优先压缩 B，再压缩 A，尽量保留证据链接
- 可调：设置 `MAX_CHARS` 或优化 `prompts/daily_briefing.md`

### 8.4 图片模式显示异常
- 现象：中文字体/排版不理想或图片发送受限。
- 处理建议：
  1. 优先用 `markdown` 模式稳定运行
  2. 根据环境补充可用字体（本项目会自动尝试常见字体）

---

## 9. 已知限制

1. 企业微信群机器人图片消息对 payload 大小敏感，内容太长可能失败（已做 markdown 降级）。
2. 链接过多会影响可读性，当前策略每条重点最多保留 1-2 个来源。
3. 市场极端波动日，1500 字内可能无法覆盖全部细节，程序会优先保留证据链接。

---

## 10. 本地运行（可选）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY='xxx'
export WECOM_WEBHOOK='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx'
python src/main.py --mode markdown
```

### 使用 OpenAI 协议兼容平台（自定义 URL + API Key）

```bash
export OPENAI_COMPAT_BASE_URL='https://your-endpoint.example.com/v1'
export OPENAI_COMPAT_API_KEY='your_compat_api_key'
export WECOM_WEBHOOK='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx'
python src/main.py --mode markdown
```

变量优先级：
- API Key：`OPENAI_COMPAT_API_KEY` > `OPENAI_API_KEY`
- Base URL：`OPENAI_COMPAT_BASE_URL` > `OPENAI_BASE_URL` > OpenAI SDK 默认地址

兼容性说明：
- 若 Base URL 以 `/chat/completions` 结尾（用户写死完整接口地址），程序会直接调用该 Chat Completions 接口，不再拼接 `/responses`，避免出现 `.../chat/completions/responses` 的 404 错误。
- 其他情况默认走 OpenAI Responses API。
