# Anthropic 协议分支设计（引入 DeepSeek 官方联网搜索）

- 状态：**设计稿，尚未实现**
- 最后更新：2026-10-14
- 适用范围：`easy_ai.py`、`pz.py`（本地真实配置）
- 实现完成后的去向：结论并入 `EASY_AI_CODE_GUIDE.md`，本文件可删除或降级为附录

---

## 0. 一句话结论

新增一个正式的 `api_type: "anthropic"` 协议分支，把 DeepSeek 模型直接指向官方 Anthropic 兼容端点
`https://api.deepseek.com/anthropic/v1/messages`。在该分支里：

- `search: True` → 挂官方服务端搜索工具 `web_search_20260209`（**一次请求闭环**，无需客户端工具循环）；
- `search: False` → 复用现有博查第三方搜索的"工具循环"语义（需要新增 anthropic 版工作流）；
- 二者互斥，由 `search` 开关二选一，与现有 openai 分支的规则完全一致。

改动集中在 `easy_ai.py` 的协议层，Handler、数据库、回复头格式、会话延续逻辑均不感知。

---

## 1. 为什么必须新增 `api_type`，而不是复用现有分支

### 1.1 官方搜索只在一个地方暴露

| 入口 | 官方联网搜索 |
|---|---|
| `POST /anthropic/v1/messages`（Anthropic 兼容） | ✅ 可用，工具名 `web_search_20260209` |
| `POST /chat/completions`（OpenAI 兼容） | ❌ 无此能力 |
| Responses API 的 `web_search` | ❌ 官方文档明确标注 **Ignored** |

因此不能像适配 GLM / Moonshot 那样在 `_enable_openai_native_search()` 里加分支——那条路根本不通。

### 1.2 协议差异足够大，`gemini` 分支也装不下

| 维度 | openai 分支 | anthropic 分支 |
|---|---|---|
| 端点 | `api_url` 即完整端点 | `api_url` + `/v1/messages` |
| 认证头 | `Authorization: Bearer` | `x-api-key` + `anthropic-version` |
| 系统提示 | `messages[0].role=system` | 顶层 `system` 字段 |
| 输出上限 | 可省略（服务端默认） | 协议要求 `max_tokens` |
| 用户内容 | 字符串或 `image_url` 块 | `content` 块数组（`text` / `image`） |
| 工具调用 | `tool_calls`（`arguments` 是 JSON 字符串） | `tool_use` 块（`input` 已是对象） |
| 工具结果回填 | `role:"tool"` + `tool_call_id` | `role:"user"` + `tool_result` 块 |
| 回包正文 | `choices[0].message.content` | `content[]` 中 `type=="text"` 的块拼接 |
| 搜索证据 | 各家字段不一，靠启发式猜 | 规范字段，可精确读取 |

结论：这是一个**新的正式 API 协议**，正好命中 `EASY_AI_CODE_GUIDE.md` §17「新增正式 API 协议」的既定扩展路径（Handler 不感知协议细节）。

---

## 2. 协议实测记录（2026-10-14，真实 key，可复现）

> 复现脚本见 §9 附录。本机需经代理 `http://127.0.0.1:7890`。

### 2.1 端点与请求头

```
POST https://api.deepseek.com/anthropic/v1/messages
content-type: application/json
x-api-key: <DEEPSEEK_API_KEY>
anthropic-version: 2023-06-01
```

- `anthropic-version` 官方文档称"忽略"，但按 Anthropic 生态惯例保留。
- `anthropic-beta` 不需要。
- 实测**省略 `max_tokens` 不报错**（DeepSeek 实现未强制），但协议层面它是必填项，本设计显式传值。

### 2.2 请求形状

```json
{
  "model": "deepseek-v4-pro",
  "max_tokens": 65536,
  "system": "你是本群的AI助手……",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "……"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "<b64>"}}
      ]
    }
  ],
  "tools": [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 10}
  ],
  "thinking": {"type": "disabled"}
}
```

要点：

- `tools` 里**不传**时行为与不带搜索能力一致；传了由模型自行决定是否搜索。
- `thinking`：省略 = 思考开启（默认）；`{"type": "disabled"}` = 关闭，实测有效。
- 客户端自定义工具的形状是 `{name, description, input_schema}`，**不是** OpenAI 的 `{type:"function", function:{...}}`。

### 2.3 回包形状

顶层：`{id, type, role, model, content[], stop_reason, stop_sequence, usage}`

`content[]` 可能出现的块：

| `type` | 关键字段 | 说明 |
|---|---|---|
| `thinking` | `thinking`, `signature` | 思考链。**会出现在回包里，绝不能当成正文** |
| `server_tool_use` | `id`, `name`, `input.query` | 官方服务端搜索的一次调用 |
| `web_search_tool_result` | `tool_use_id`, `content[{type:"web_search_result", title, url, page_age, encrypted_content}]` | 官方搜索结果，每条 10 个左右 |
| `tool_use` | `id`, `name`, `input`（对象） | 客户端工具调用（博查切入口用这条） |
| `text` | `text` | 正文。**可能存在多个块，也可能一个都没有** |

`usage`：

```json
{"input_tokens": 18122, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 384,
 "output_tokens": 720, "server_tool_use": {"web_search_requests": 2}}
```

- `usage.server_tool_use.web_search_requests` 是**权威搜索次数**（与 Gemini 的 `webSearchQueries` 计数语义一致）。
- **没发生搜索时该键整个不存在**（实测）。
- `cache_read_input_tokens` 正常命中 → 不影响已有的高缓存命中优化。

### 2.4 官方搜索：一次请求闭环

实测（`deepseek-v4-flash-vision-exp` + `search: True`）：

| 场景 | 耗时 | 输出 tokens | 搜索次数 | 正文 |
|---|---|---|---|---|
| 思考开 | 8.4~14.2s | 626~1040 | 1~2 | ✅ |
| 思考关 | **1.6s** | 83 | 1 | ✅ 干净 |

`content` 顺序实测为 `server_tool_use → web_search_tool_result → [thinking] → text`。
服务端搜索**无需客户端回填工具结果**，这一点与博查完全不同。

### 2.5 客户端工具循环（博查切入口）

用假工具手写了一次完整循环，结论：

| 验证点 | 结果 |
|---|---|
| 模型调用客户端工具 | ✅ `stop_reason="tool_use"`，返回 `tool_use` 块 |
| `input` 的类型 | **对象**（不是 JSON 字符串）→ 不需要 `json.loads` |
| 回填 `{"type":"tool_result","tool_use_id":...,"content":"纯文本"}` | ✅ 接受，可继续多轮 |
| assistant 的 `thinking` 块原样回填 | ✅ 接受（DeepSeek 不校验 `signature`），但会重复计入输入 token |

### 2.6 图片输入

实测：32×32 纯红 PNG（base64）+ `deepseek-v4-flash-vision-exp` → 回答"红色"，HTTP 200。

**这一条是必需的，不是可选优化**：生产配置 `pz.py` 里 `default` 是 `vision: True`，
anthropic 分支若不实现 `image` 块，生产的默认模型会静默丢图。

### 2.7 思考模式与 `max_tokens`（含"为什么 OpenAI 格式没这个问题"的答案）

同一个"只输出一个数字"的判定任务，同一模型：

| 协议 | max_tokens | 耗时 | 输出 tokens | 结果 |
|---|---|---|---|---|
| anthropic | 256 | — | 256 | ⚠️ `stop_reason=max_tokens`，**只有 thinking 块、零 text 块** |
| anthropic | 1024 | — | 1024 | ⚠️ 同上 |
| anthropic | 8192 | 24.4s | 4756（含思考） | ✅ 正文 `125` |
| openai | **省略** | 26.6s | 4948（reasoning 4946） | ✅ 正文 `125` |
| openai | 256 | 2.0s | 256（reasoning 256） | ⚠️ `finish_reason=length`，**content 为空** |

**答案：OpenAI 格式不是"没这个问题"，而是项目根本没传 `max_tokens`。**
官方文档写明：`max_tokens` 取值范围 1 ~ 384K，**未设置时非思考模式默认 8K、思考模式默认 64K**。
项目走 OpenAI 路径时省略该字段 → 自动拿到 64K 预算，所以从不暴露。
而 Anthropic 协议要求传 `max_tokens`，**一旦给得比思考长度小，正文就会被挤掉**。

实测 `max_tokens` 上限：anthropic 端点 **100000 通过**（32000 / 65536 / 100000 均 200），
官方 OpenAI 协议文档给出的上限是 384K（393216）。**成本只按实际输出计，不受 `max_tokens` 影响**，
它的实际作用是"最坏情况的生成上限"，进而决定最坏耗时是否会撞上 `AI_CHAT_TIMEOUT=120`。

### 2.8 官方搜索未触发时的行为

问 `1+1等于几` 但带上搜索工具：模型直接回答，`usage` 中**没有** `server_tool_use` 键。
→ 可精确判定"提供了搜索能力但没搜"，回复头按决策 4 处理为**不显示搜索字段**。

---

## 3. 已拍板的决策

| # | 决策点 | 结论 | 依据 |
|---|---|---|---|
| 1 | 正式问答是否关闭思考 | **保持开启** | 质量更好；8192 预算下实测 24.4s，`AI_CHAT_TIMEOUT=120` 有富余 |
| 2 | `ANTHROPIC_MAX_TOKENS` | **65536**（可上调，≤384K） | 对齐 OpenAI 侧"思考模式默认 64K"，与现状一致；实测 100000 亦通过 |
| 3 | 官方搜索 `max_uses` | **10** | 每次搜索都会额外产生模型请求与 token 费用，需要护栏 |
| 4 | 有搜索能力但未搜索时的回复头 | **不显示搜索字段** | 与 Gemini 原生搜索语义一致，避免噪音 |
| 5 | `default` / `A` 的 `search` | **均设 `True`** | 引入官方搜索的目的即在此；博查退化为只服务其他 openai 模型 |
| 6 | 前置 AI（动态历史条数） | **关闭思考** | 见 §4.10：不关则 24s 贴着 `DYNAMIC_HISTORY_TIMEOUT=30` 跑 |

> 决策 6 未在对话中明确拍板，属于本设计稿的**建议值**，见 §8 Q8。

---

## 4. 改动清单

### 协议分支（5 处）

**4.1 常量区（`easy_ai.py` 顶部配置区，第三方搜索配置之后）**

新增：

```
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MAX_TOKENS = 65536      # 见 §3 决策 2
ANTHROPIC_WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 10}
```

同时派生第三方工具的 anthropic 版，**复用同一份 schema，避免两处漂移**：

```
THIRD_SEARCH_TOOL_ANTHROPIC = {
    "name": THIRD_SEARCH_TOOL["function"]["name"],
    "description": THIRD_SEARCH_TOOL["function"]["description"],
    "input_schema": THIRD_SEARCH_TOOL["function"]["parameters"],
}
```

**4.2 `MODELS_CONFIG`（`easy_ai.py:46` 起；`pz.py` 同步）**

`default` 与 `A` 改为：

```
"api_url": "https://api.deepseek.com/anthropic",
"api_type": "anthropic",
"search": True,
```

其余字段（`name` / `model_id` / `vision` / `api_key`）不变。
`vision` 保持 `pz.py` 的现值（`default` 为 `True`），新分支已支持 image 块。

**4.3 `ModelSelection.api_url`（`easy_ai.py:183`）**

加一支：`anthropic` → `f"{api_url}/v1/messages"`，与 `gemini` 的"端点前缀"约定一致。

**4.4 `_extract_api_reply_text()`（`easy_ai.py:255`）**

加 `anthropic` 分支：拼接所有 `type == "text"` 的块，**显式跳过 `thinking`**。
不跳过 = 思维链直接进群，这是本分支最高危的细节。

**4.5 `build_model_request()`（`easy_ai.py:1987`）**

新增 anthropic 分支：

- 请求头：`x-api-key` + `anthropic-version`；
- `system` 走**顶层**字段（不是 system 消息）；
- `max_tokens` 必填，取 `ANTHROPIC_MAX_TOKENS`；
- `content` 块数组：`text` 块 + 逐张 `image` 块（`vision_enabled` 且有可发送图片时）；
- `search_enabled` → `payload["tools"] = [ANTHROPIC_WEB_SEARCH_TOOL]`；
- `use_third_search` → `payload["tools"] = [THIRD_SEARCH_TOOL_ANTHROPIC]`（返回值里带上 `use_third_search` 标记，与 openai 分支一致的 `PreparedModelRequest` 契约）。

### 回包解析与发送（3 处）

**4.6 新增 `ChatService.parse_anthropic_reply(data, search_enabled)`（参照 `parse_gemini_reply`，`easy_ai.py:2084`）**

```
requests = data["usage"]["server_tool_use"]["web_search_requests"]
search_enabled 且 requests 是正整数 → SearchResult(performed=True, count=requests)
其余情况（未搜索 / 无该字段）→ SearchResult()（默认不显示，决策 4）
```

**4.7 `send_model_request()`（`easy_ai.py:2120`）**

新增分支：

- `use_third_search` → 调用新的 anthropic 版工作流；
- 否则 → `parse_anthropic_reply()`。

**4.8 新增 anthropic 版博查工作流（参照 `_run_openai_third_search_workflow`，`easy_ai.py:1574`）**

结构对齐 openai 版，但消息形状不同：

| 环节 | openai 版 | anthropic 版 |
|---|---|---|
| 首轮 messages | system 消息 + user 消息 | 顶层 `system` + 一条 `role:"user"` |
| 判断是否需要工具 | `messages.tool_calls` 非空 | `content[]` 中有 `type=="tool_use"` |
| 追加 assistant | `{role:"assistant", tool_calls:[...]}` | `{role:"assistant", content:[...原始块，含 thinking/tool_use]}` |
| 回填结果 | 多条 `{role:"tool", tool_call_id, content}` | 一条 `{role:"user", content:[{type:"tool_result", ...}]}` |
| 末轮强制回答 | 改写 `messages[0]` 的 system 内容 | 改写顶层 `system` |
| 轮数 | `MAX_SEARCH_ROUNDS`，工具在非末轮提供 | 同 |

搜索计数与 `SearchResult` 语义（`performed=True, count=总数`；全部为 0 时显示 `搜索：False`）与 openai 版**完全保持一致**。

**建议同时做一次小重构**：把 `_execute_web_search()`（`easy_ai.py:1425`）里的"参数校验 + 调用博查 + 结果文本"抽成一个公共协程（例如 `_bocha_from_tool_input(args)`），
openai 版与 anthropic 版共用。否则 freshness/参数校验、`搜索无结果`/`搜索失败` 文案会在两处漂移。
`_execute_web_search` 的对外行为（错误文案、计数、抛错策略）保持字节级不变。

### 健壮性与配置（2 处）

**4.9 `validate_configuration()`（`easy_ai.py:409`）**

支持集合加 `"anthropic"`，否则启动时会打一条"未知 api_type"的误导告警。

**4.10 `get_dynamic_history_length()`（`easy_ai.py:588`）——本设计稿最重要的漏项**

现状：

```
if api_type == "openai": ...
else:  # Gemini 格式兼容
    ...拼 Gemini 请求...
```

把 `default` 改成 `anthropic` 后，前置 AI 会**静默掉进 Gemini 分支**，请求形状全错 → 失败 → 回退
`DEFAULT_LIMIT=80`，用户与日志都只能看到"动态条数失效"的结果，而不是"协议没实现"的原因。

改法（两部分）：

1. 新增 anthropic 分支：`x-api-key` / `anthropic-version` 头、顶层 `system` 或无 system 的 user 消息、
   `max_tokens` 取**小值**（判定任务只需几十 token）、不带 `tools`（前置 AI 不需要搜索）、
   **`thinking: {"type": "disabled"}`**。
   - 实测依据：同一判定任务，思考开 24.4s / 输出 4756 tokens；思考关 **0.7s / 输出 1 token**。
   - 不关思考时，OpenAI 路径今天就已经在 26.6s 附近贴着 `DYNAMIC_HISTORY_TIMEOUT=30` 跑（实测），
     属于现存的隐性超时风险。
2. 把 `else` 改成显式 `elif api_type == "gemini"`，未知类型时 `logger.error` 明确报出协议未实现，
   再回退 `DEFAULT_LIMIT`。**这是"正确的反馈"要求**：未知协议永远不该被误当成 Gemini。

---

## 5. `api_type` 配错时的现有反馈面（健壮性核查）

| 触发点 | 未知 `api_type` 下的行为 | 反馈是否明确 |
|---|---|---|
| 启动 `validate_configuration()` | 只 `logger.warning("使用未知 api_type=...")`，不阻断 | 日志，易漏 |
| `@` 聊天 `build_model_request()` | 抛 `UnsupportedAPITypeError` → `handle_ai_chat` 捕获 | ✅ 用户看到"（模型：X）API格式设置错误！" |
| 前置 AI `get_dynamic_history_length()` | 落入 `else`（Gemini 形状）→ 请求失败 → 回退 80 条 | ❌ **静默降级**，改法见 4.10 |

结论：用户"一 @ 就会报错"的记忆是对的（聊天路径会明确报错），但前置 AI 那条路目前是静默的。
两处都要按 §4.9 / §4.10 修。

---

## 6. 已知风险与边界

| 风险 | 现状/对策 |
|---|---|
| 思维链泄漏进群 | §4.4 按 `type=="text"` 过滤；验证清单里专项检查 |
| 正文被思考挤空 → `（模型API拒绝回复）` | 由 §3 决策 2（65536）覆盖；极端情况仍可能出现（`stop_reason=max_tokens` 且无 text 块） |
| `stop_reason=max_tokens` 时无正文，但提示语是"（模型API拒绝回复）"，语义不准 | 见 §8 Q4 |
| 官方搜索自身的失败形态（key 无权限、搜索后端异常）**未实测** | 见 §8 Q5，实现时需抓一次失败样本 |
| `deepseek-v4-flash-vision-exp` 在 anthropic 端点的长期有效性 | 官方文档称"未知模型名会映射到 `deepseek-flash`"；实测该名字被原样接受并回显。建议上线后抽样核对回包 `model` 字段 |
| 博查在 anthropic 分支的 assistant 消息含 thinking 块，会重复计入输入 token | 有缓存命中（`cache_read_input_tokens`）兜底；是否裁剪见 §8 Q2 |
| 两条协议并存带来的维护面 | 只新增分支，openai/gemini 现有路径零改动；回滚成本 = 改 `MODELS_CONFIG` 两行 |

---

## 7. 验证清单

实现后必须全部通过：

```bash
python -m py_compile easy_ai.py pz.py
```

**裸探针（§9 脚本）**

1. `search: True` + 思考开：HTTP 200，`usage.server_tool_use.web_search_requests >= 1`，
   正文可从 `type=="text"` 块取得，且正文中不出现思考痕迹。
2. `tools` 不带搜索的普通问答：HTTP 200，正文正常。
3. 图片输入：PNG base64 → 正确识别。
4. 客户端工具循环：`stop_reason=="tool_use"` → 回填 `tool_result` → 次轮拿到正文。
5. 判定任务（模拟前置 AI）：思考关，`max_tokens` 小值，能在 1s 级返回纯数字。

**群内实测（生产 `pz.py` 配置）**

6. `/A 今天有什么AI新闻` → 回复头出现 `搜索：N`（N≥1），正文正常。
7. `/a 1+1等于几` → 回复头**不出现**搜索字段。
8. 引用图片提问 → 回复头 `图片：1`，识别内容正确。
9. `search: False` + `ENABLE_THIRD_SEARCH=True` 的模型（如临时把 `B`/其他 openai 模型）→ 博查路径不受影响。

**回归**

10. `default` 的 AI 动态历史条数不再失败：日志中不出现 `获取动态上下文API响应失败`，
    且耗时明显下降（预期 1s 级）。
11. 高缓存命中优化未受影响：日志/回包中 `cache_read_input_tokens` 仍持续命中。
12. `gemini` 模型（`B`/`C`）行为与改动前一致。

---

## 8. 待确认问题（Open Questions）

- **Q1** `pz.py`（本地真实配置，未入 git、`easy_ai.py` 不引用它）是否需要同步改？
  不改则生产仍走 openai，改动只停留在仓库默认配置里。
  *建议：同步改，且本文件给出改法（§4.2）。*
- **Q2** 博查多轮里 assistant 的 `thinking` 块是否原样回填？
  *建议：原样回填（规范要求，实测通过）。裁剪可省 token，但违反 Anthropic 规范，需确认 DeepSeek 能容忍。*
- **Q3** §4.8 的公共校验函数重构是否接受？（会触及现有 `_execute_web_search` 内部实现，但对外行为不变）
  *建议：接受，否则博查参数校验会分裂成两份。*
- **Q4** `stop_reason == "max_tokens"` 且正文为空时，要不要单独提示（如"输出被截断"）而不是现有的"（模型API拒绝回复）"？
  *需要决定是否扩 `ModelReply`/错误边界，成本不小。*
- **Q5** 官方搜索失败（key 无权限 / 服务异常）时 DeepSeek 的回包到底是 200 但无搜索结果，还是 4xx/5xx？
  目前只有成功样本，**未实测失败形态**，影响"给用户什么反馈"。
- **Q6** 是否同步更新 `README.md`（功能说明、回复格式说明）与 `EASY_AI_CODE_GUIDE.md`？
  *建议：实现完成后一并更新，且 GUIDE §17「新增正式 API 协议」需补上 `get_dynamic_history_length()` 这个漏项。*
- **Q7** 前置 AI 顺带改用 anthropic 协议（同一 `default` 配置）是否接受？
  *影响：前置 AI 的请求形状与正式问答一起切换；好处是修掉 §4.10 的静默降级并大幅提速。*
- **Q8** 决策 6（前置 AI 关闭思考）未明确拍板，是否照此实现？

---

## 9. 附录：可复现探针

```python
import json, urllib.request

KEY = "<DEEPSEEK_API_KEY>"
URL = "https://api.deepseek.com/anthropic/v1/messages"
HEADERS = {
    "content-type": "application/json",
    "x-api-key": KEY,
    "anthropic-version": "2023-06-01",
}
# 本机需代理；服务器直连时去掉 ProxyHandler
OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": "http://127.0.0.1:7890",
                                 "https": "http://127.0.0.1:7890"})
)

SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 10}

def call(payload):
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), headers=HEADERS)
    with OPENER.open(req, timeout=180) as resp:
        return json.load(resp)

def answer_text(data):
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

# 1. 官方搜索一次闭环
d = call({"model": "deepseek-v4-flash-vision-exp", "max_tokens": 65536,
          "system": "你是AI助手，纯文本回答。",
          "messages": [{"role": "user", "content": "2026年10月13日有什么AI新闻？一句话"}],
          "tools": [SEARCH_TOOL]})
assert d["usage"]["server_tool_use"]["web_search_requests"] >= 1
assert answer_text(d)

# 2. 客户端工具循环（博查切入口的形状验证）
CLIENT_TOOL = {"name": "web_search", "description": "联网搜索",
               "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                                "required": ["query"]}}
msgs = [{"role": "user", "content": [{"type": "text", "text": "DeepSeek 昨天有什么新闻？"}]}]
d = call({"model": "deepseek-v4-pro", "max_tokens": 8192, "messages": msgs, "tools": [CLIENT_TOOL]})
assert d["stop_reason"] == "tool_use"
msgs.append({"role": "assistant", "content": d["content"]})
msgs.append({"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": b["id"], "content": "1. 示例结果 —— 来源 2026-10-13"}
    for b in d["content"] if b["type"] == "tool_use"]})
d = call({"model": "deepseek-v4-pro", "max_tokens": 8192, "messages": msgs, "tools": [CLIENT_TOOL]})
print(answer_text(d))
```
