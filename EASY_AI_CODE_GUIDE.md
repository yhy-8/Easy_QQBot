# `easy_ai.py` 代码与架构说明

> 文档目的：帮助维护者快速理解 `easy_ai.py` 的整体结构、完整调用链、数据库不变量、消息格式、模型协议、搜索行为和异常边界。  
> 对应代码：当前单文件版 `easy_ai.py`。  
> 最后核对日期：2026-07-29。

## 1. 项目定位与设计约束

`easy_ai.py` 是一个 NoneBot2 + OneBot V11 群聊 AI 插件，主要负责：

1. 记录白名单群的实时消息，并在机器人连接时同步一批历史消息。
2. 将 OneBot 消息段转换成适合数据库保存或适合 AI 理解的文本。
3. 根据群聊活跃度动态决定读取多少条历史记录。
4. 根据消息前缀选择模型和严肃/随性模式。
5. 支持 OpenAI 兼容协议和 Gemini `generateContent` 协议。
6. 支持视觉图片、模型原生搜索和第三方博查搜索。
7. 统一生成固定回复头，发送回复，并把机器人消息写回数据库。

当前架构遵守以下约束：

- 继续保持单文件部署，不要求用户安装项目内部包。
- 数据库表结构、SQL、事务顺序和消息保存规则保持不变。
- 事件 Handler 只负责入口校验和结果发送，复杂逻辑下沉到解析器、辅助函数和 `ChatService`。
- OpenAI 兼容模型的原生搜索差异集中在两个函数中。
- 第三方搜索拥有独立工作流，不把搜索轮询堆回主 Handler。
- 暂不自动探测上游的实际 API 类型；仍由 `MODELS_CONFIG[*]["api_type"]` 指定。

## 2. 一分钟理解整体架构

```text
NoneBot / OneBot 事件
│
├─ 启动阶段
│  ├─ init_http_session()       创建共享 aiohttp 会话
│  ├─ validate_configuration()  报告配置问题
│  └─ init_db()                 初始化 SQLite 表
│
├─ Bot 连接
│  └─ sync_history_on_startup()
│     └─ parse_message_content() → insert_message_to_db()
│
├─ 普通群消息
│  └─ record_chat_history()
│     └─ parse_message_content() → insert_message_to_db()
│
└─ @机器人
   └─ handle_ai_chat()
      ├─ 先保存触发消息
      ├─ ChatService.select_model()
      ├─ extract_text_and_image_ids()
      ├─ 可选发送 Waiting……
      └─ ChatService.complete()
         ├─ load_base64_images()
         ├─ build_prompts()
         │  ├─ get_dynamic_history_length()
         │  ├─ _load_history_rows()
         │  ├─ _load_user_display_map()
         │  └─ _format_history_text()
         ├─ build_model_request()
         │  ├─ _enable_openai_native_search()
         │  └─ 或注册 THIRD_SEARCH_TOOL
         ├─ send_model_request()
         │  ├─ _parse_openai_native_search_response()
         │  ├─ _run_openai_third_search_workflow()
         │  └─ parse_gemini_reply()
         └─ 返回 ChatCompletion
      ├─ _format_reply_prefix()
      └─ send_and_save()
```

单文件内部可以理解为六层：

| 层 | 主要对象 | 职责 |
|---|---|---|
| 配置层 | 全局配置、提示词、`MODELS_CONFIG` | 决定白名单、模型、超时、视觉与搜索能力 |
| 领域对象层 | `ParsedMessage`、`SearchResult`、`ModelReply` 等 | 用明确对象传递结果，减少 `bool/int/dict` 混用 |
| 基础设施层 | HTTP 会话、SQLite、OneBot API | 管理外部资源和持久化 |
| 解析/协议层 | `OneBotMessageParser`、搜索解析函数 | 适配消息段和不同上游回包 |
| 业务编排层 | `ChatService` | 组织图片、历史、提示词、请求和模型回复 |
| 事件入口层 | 三个 Handler | 响应框架事件、校验、发送和结束事件 |

## 3. 配置区域

### 3.1 基础配置

| 配置 | 作用 | 重要细节 |
|---|---|---|
| `ALLOWED_GROUPS` | 允许记录和使用 AI 的群号列表 | 每个群对应一张 `group_{group_id}` 表 |
| `DB_PATH` | SQLite 文件路径 | 初始化、查询和写入都使用同一路径 |
| `ENABLE_QUICK_ACK` | 是否先回复 `Waiting……` | 快速回复也会在发送成功后存库 |
| `ENABLE_AI_HISTORY_DECISION` | 是否让前置 AI 决定历史条数 | 关闭后使用纯统计随机算法 |
| `DYNAMIC_HISTORY_MODEL` | 前置 AI 使用的模型键 | 不存在时沿用原逻辑回退 `default` |
| `DYNAMIC_HISTORY_TIMEOUT` | 前置 AI 请求超时 | 超时或回包无数字时使用默认 80 条 |
| `AI_CHAT_TIMEOUT` | 正式模型与第三方搜索后续模型轮次的超时 | `aiohttp` 超时会被主 Handler 单独处理 |
| `IMAGE_BASE_DIR` | NapCat 图片目录映射 | 留空信任 NapCat 绝对路径；非空时只取文件名再拼接 |
| `DEFAULT_MODE` | 无模型前缀时的模式 | 必须是 `serious` 或 `casual` |

### 3.2 第三方搜索配置

| 配置 | 作用 |
|---|---|
| `THIRD_SEARCH_API_KEY` | 博查 API Key；为空时第三方搜索不会启用 |
| `THIRD_SEARCH_API_URL` | 博查搜索端点 |
| `THIRD_SEARCH_COUNT` | 单次最多请求的结果数 |
| `THIRD_SEARCH_TIMEOUT` | 单次博查请求超时 |
| `MAX_SEARCH_ROUNDS` | 模型最多产生的搜索批次总数；小于等于 0 时禁用第三方工具 |
| `ENABLE_THIRD_SEARCH` | 第三方搜索总开关 |

第三方搜索的启用条件必须同时满足：

```text
当前模型 search=False
AND ENABLE_THIRD_SEARCH=True
AND THIRD_SEARCH_API_KEY 非空
AND MAX_SEARCH_ROUNDS > 0
```

模型原生搜索优先于第三方搜索，两者不会同时注册。

### 3.3 `MODELS_CONFIG` 字段

| 字段 | 作用 |
|---|---|
| `api_key` | 上游认证密钥 |
| `api_url` | OpenAI 完整端点，或 Gemini 模型端点前缀 |
| `name` | 回复头和错误提示中显示的名称 |
| `api_type` | 当前支持 `openai`、`gemini` |
| `model_id` | 实际发送给上游的模型 ID |
| `vision` | 是否允许处理图片 |
| `search` | 是否尝试启用模型原生搜索 |

模型选择规则：

- 无前缀：使用 `default` 和 `DEFAULT_MODE`。
- `/A`、`/B` 等大写前缀：选择对应键，并强制严肃模式。
- `/a`、`/b` 等小写前缀：选择对应大写键，并强制随性模式。
- 只有键名全为大写的非 `default` 项参与前缀匹配。
- 当前按字典插入顺序匹配；若以后同时存在 `A` 和 `AB`，应注意短前缀可能先匹配。

### 3.4 提示词

- `SERIOUS_SYSTEM_PROMPT`：强调客观、禁止猜测、具备联网能力时优先搜索。
- `CASUAL_SYSTEM_PROMPT`：允许轻松语气，但不塑造独立人设。
- 两者都禁止模型仿造程序生成的 `模型/记录/图片/搜索` 回复头。
- `{bot_identity}` 在运行时替换为机器人群名片和 QQ 昵称。
- `MODE_PROMPTS` 负责从模式名映射到提示词。

## 4. 领域对象与异常类型

### 4.1 `ParsedMessage`

OneBot 消息解析结果：

- `text`：数据库存储文本或 AI 富文本。
- `image_ids`：需要进一步下载并发送给视觉模型的图片 `file_id`。

数据库解析通常只使用 `text`；AI 解析同时使用二者。

### 4.2 `SearchResult`

统一表达一次回复的搜索状态：

| 字段 | 含义 |
|---|---|
| `performed` | `True`=确认发生；`False`=确认未发生；`None`=中转没有提供可核实信息 |
| `count` | 可核实的搜索/来源/工具数量 |

`prefix_value()` 将内部状态转换成回复头：

1. `count > 0` → `搜索：具体数字`。
2. `performed=True, count=0` → `搜索：False`。
3. 其他情况不显示搜索字段，包括已启用搜索但中转没有提供可核实信息的 `performed=None, count=None`。

因此，“已开启搜索”不等于真的发生了搜索；程序只显示可核实的正数，或搜索实际调用后所有轮次均未取得结果的状态。某一轮失败不会覆盖其他轮已经取得的结果数。

### 4.3 `ModelReply`

- `text`：模型最终正文。
- `search`：对应的 `SearchResult`。

### 4.4 `ModelSelection`

保存一次用户消息的模型选择：

- `mode`：`serious` 或 `casual`。
- `prefix_to_remove`：需要从 AI 富文本中去掉的 `/A`、`/a` 等前缀。
- `config`：模型配置字典。

属性说明：

- `api_type`：缺省为 `openai`。
- `api_url`：Gemini 自动拼成 `.../{model_id}:generateContent`。
- `vision_enabled`、`search_enabled`：读取能力开关。
- `use_third_search`：集中判断第三方搜索的四个必要条件。
- `information`：生成快速回复使用的 `模型名，SER/CAS`。

### 4.5 `PreparedModelRequest`

表示已组装完、可直接发送的请求：

- 通用部分：`api_type`、`api_url`、`headers`、`payload`。
- 第三方搜索需要保留：`system_prompt`、`user_message_content`。
- 原生搜索需要保留：`native_search_adapter`。
- `use_third_search` 表示发送首轮后是否进入工具调用工作流。

### 4.6 `ChatCompletion`

交回 Handler 的完整结果：

- `reply`：正文和搜索状态。
- `history_count`：实际传入提示词的历史消息行数。
- `image_count`：成功读取并实际发给模型的图片数。

### 4.7 自定义异常

- `UnsupportedAPITypeError`：模型 `api_type` 不是 `openai` 或 `gemini`。
- `ModelHTTPError`：正式模型首轮请求返回非 200。

使用自定义异常后，Handler 可以给用户展示不同错误文案，而不必解析字符串。

## 5. HTTP 生命周期与启动检查

### 5.1 `driver`

`driver = get_driver()` 获取 NoneBot Driver，用于注册启动、关闭和 Bot 连接钩子。

### 5.2 `_http_session`

插件级共享 `aiohttp.ClientSession`。模型请求、动态历史模型和博查搜索共用该会话，避免每次请求都建立并销毁连接池。

### 5.3 `init_http_session()`

NoneBot 启动时创建共享会话。只有会话不存在或已关闭时才新建。

### 5.4 `close_http_session()`

NoneBot 关闭时关闭共享会话，并把全局变量恢复为 `None`，避免未关闭会话警告和连接泄漏。

### 5.5 `get_http_session()`

返回共享会话。若启动钩子尚未执行或会话意外关闭，会惰性创建一个新会话，以兼容特殊插件加载顺序。

### 5.6 `validate_configuration()`

启动时检查：

- 是否存在 `default` 模型。
- `DEFAULT_MODE` 是否能在 `MODE_PROMPTS` 中找到。
- `DYNAMIC_HISTORY_MODEL` 是否存在。
- 每个模型是否包含七个必要字段。
- `api_type` 是否属于当前支持集合。

该函数只写日志，不修改配置，也不阻止启动，保持原有容错方式。

## 6. 数据库架构与不变量

### 6.1 数据库设计

启动时开启：

```sql
PRAGMA journal_mode=WAL;
```

WAL 有利于读写并发，但并不取代事务；每次写入仍显式 `commit()`。

#### `user_info`

| 字段 | 类型/约束 | 含义 |
|---|---|---|
| `user_id` | `TEXT PRIMARY KEY` | QQ 用户 ID |
| `qq_nickname` | `TEXT` | QQ 昵称 |
| `last_global_speak_time` | `INTEGER` | 全局最后发言时间 |

#### `user_group_info`

| 字段 | 类型/约束 | 含义 |
|---|---|---|
| `user_id` | `TEXT` | QQ 用户 ID |
| `group_id` | `INTEGER` | 群号 |
| `group_nickname` | `TEXT` | 群名片 |
| `last_group_speak_time` | `INTEGER` | 在该群最后发言时间 |

联合主键是 `(user_id, group_id)`。

#### `group_{group_id}`

每个白名单群一张表：

| 字段 | 类型/约束 | 含义 |
|---|---|---|
| `message_id` | `TEXT UNIQUE` | OneBot 消息 ID，用于去重 |
| `timestamp` | `INTEGER` | Unix 时间戳 |
| `user_id` | `TEXT` | 发言者 ID |
| `content` | `TEXT` | 规范化后的消息文本 |

表名只来自配置中的群号或已通过白名单的事件群号，不接受用户消息直接提供任意表名。

### 6.2 `init_db()`

启动时：

1. 连接 `DB_PATH`，连接超时 15 秒。
2. 设置 WAL。
3. 创建两张用户信息表。
4. 为 `ALLOWED_GROUPS` 中每个群创建消息表。
5. 提交事务。
6. 记录初始化完成日志。

`CREATE TABLE IF NOT EXISTS` 不会清空已有数据。

### 6.3 `insert_message_to_db()`

入口条件：

- `content` 为空则不写。
- `group_id` 不在白名单则不写。

同一数据库连接、同一事务内严格依次执行：

1. `INSERT OR IGNORE` 写群消息表。相同 `message_id` 不重复插入。
2. `user_info` 使用 UPSERT 更新 QQ 昵称和全局最后发言时间。
3. `user_group_info` 使用 UPSERT 更新群名片和群内最后发言时间。
4. `commit()`。

即使第 1 步因重复消息被 `IGNORE`，后两步用户资料更新仍会继续执行。

异常被记录后不向上抛出，避免一次存库失败中断消息处理。

### 6.4 数据写入入口

| 场景 | 入口 | 规则 |
|---|---|---|
| Bot 连接后的历史同步 | `sync_history_on_startup()` | 每条历史分别解析和插入 |
| 普通白名单群消息 | `record_chat_history()` | 跳过 `@机器人`，防止与 AI Handler 竞争 |
| 触发 AI 的用户消息 | `handle_ai_chat()` | 在任何模型调用前先强制保存 |
| `Waiting……` 快速回复 | `send_and_save()` | 发送成功且获得 `message_id` 才保存 |
| 正式回复或错误提示 | `send_and_save()` | 同上 |

### 6.5 `_load_history_rows()`

读取当前群的历史：

1. 连接群消息表。
2. 左连接全局昵称和本群名片。
3. 排除当前触发消息的 `message_id`。
4. 按 `timestamp DESC, rowid DESC` 获取最近 `dynamic_limit` 条。
5. 查询结束后 `reverse()`，恢复成从旧到新的提示词顺序。

排除当前触发消息很重要：该消息已经提前存库，但会在提示词最后作为“当前问题”单独加入，不能在历史中重复出现。

### 6.6 `_load_user_display_map()`

读取当前群用户的显示名映射：

- 群名片与 QQ 昵称不同：`群名片（QQ昵称：QQ昵称）`。
- 两者相同：只显示一个名称。
- 缺少群名片时依次回退 QQ 昵称、用户 ID。

该映射用于把历史消息中的数字 `@` 和引用发言人 ID 转换成可读名称。

### 6.7 数据库未改动的核查结论

本轮架构重构前后已逐一比较实际传给 `execute()` 的 10 个 SQL 模板：

- 1 条 WAL。
- 3 条建表语句。
- 1 条近期时间戳查询。
- 3 条消息/用户写入语句。
- 2 条历史与昵称查询。

这些 SQL 模板与重构前版本完全一致。事务顺序、白名单判断、去重规则、查询排序、当前消息排除和保存入口也保持不变。

后续若修改数据库相关代码，应至少复核本章列出的不变量。

## 7. OneBot 消息解析

### 7.1 为什么有两种解析模式

同一条 OneBot 消息有两个用途：

1. 数据库存储：文本应稳定、简洁，引用只保存元信息，不能把大量媒体内容写入数据库。
2. 当前 AI 提问：需要更丰富的引用内容、可读昵称和图片 ID。

`OneBotMessageParser` 统一遍历消息段，但使用两个渲染分支：

- `for_ai=False` → `_render_storage_segment()`。
- `for_ai=True` → `_render_ai_segment()`。

### 7.2 `_segment_type_and_data()`

兼容两种消息段表示：

- 普通字典：读取 `segment["type"]` 和 `segment["data"]`。
- OneBot `MessageSegment` 对象：读取 `.type` 和 `.data`。

如果 `data` 不是字典，统一回退为空字典。空 `seg_type` 被视为畸形段并跳过；未知但非空类型必须保留占位符。

### 7.3 `_format_member_at()`

把 QQ ID 转成可读 `@`：

- `all` → `[@全体成员]`。
- 非数字 → 原样 `[@值]`。
- 数字 → 调用 `get_group_member_info()`。
- 有不同的群名片和 QQ 昵称 → `[@群名片（QQ昵称：昵称）]`。
- 查询失败 → `[@QQ号]`。

### 7.4 消息段行为对照

| 段类型 | 数据库存储 | 当前 AI 消息 | AI 引用内容 |
|---|---|---|---|
| `text` | 原文本 | 原文本 | 原文本 |
| `at` 机器人自己 | `[@机器人ID]` | 删除，避免把触发动作当问题 | 按普通成员格式化 |
| `at` 其他成员 | `[@QQ号]` | 尽量解析昵称 | 尽量解析昵称 |
| `at all` | `[@全体成员]` | `[@全体成员]` | `[@全体成员]` |
| `image` 有 `summary` | 保存摘要 | 保存摘要，不进入视觉列表 | 保存摘要，不进入视觉列表 |
| `image` 无 `summary` | `[图片]` | `[图片]`，收集 `file` | `[图片]`，收集 `file` |
| `face/mface/bface` | 摘要或 `[表情包]` | 摘要或 `[表情包]` | 摘要或 `[表情包]` |
| `record` | `[语音]` | `[语音]` | `[语音]` |
| `video` | `[视频]` | `[视频]` | `[视频]` |
| `file` | 文件名占位 | 文件名占位 | 文件名占位 |
| `forward` | `[聊天记录]` | `[聊天记录]` | `[聊天记录]` |
| `node` | `[合并转发节点]` | `[合并转发节点]` | `[合并转发节点]` |
| `json/xml` | `[分享了卡片/链接]` | 同左 | 同左 |
| `reply` | 只保存时间和发言人 ID | 取回并展开一层完整内容 | `[引用回复（未继续展开）]` |
| 未知非空类型 | `[seg_type]` | `[seg_type]` | `[seg_type]` |

文件名回退：

- 数据库存储：`name → file → id → 未知文件`。
- AI 引用：`name → file → id → 未知文件`。
- 当前 AI 顶层文件：`name → file → 未知文件`。

### 7.5 `_render_storage_segment()`

将单个段转换为适合长期保存的稳定文本。

引用段会调用一次 `get_msg()`，但只提取原消息的 Unix 时间戳和发言人 ID，输出：

```text
[引用回复(时间：时间戳，发言人：用户ID)]
```

此格式随后可由 `_format_history_text()` 转换成可读时间和昵称。

### 7.6 `_render_quoted_ai_segment()`

渲染“被引用消息内部”的单个段：

- 图片与当前消息共用同一个 `image_ids` 列表，因此引用图片可以实际发送给视觉模型。
- 文件、语音、视频等保留明确占位。
- 若引用内容里再次出现 `reply`，只输出 `[引用回复（未继续展开）]`。
- 不调用 `_render_ai_reply()`，因此不会继续套娃或无限请求。
- 最后的显式 `else` 保证未来新增的 OneBot 类型不会静默丢失。

### 7.7 `_render_ai_reply()`

只展开外层引用一次：

1. 用外层引用 ID 调用一次 `bot.get_msg()`。
2. 格式化原消息时间。
3. 组合原发言人的群名片和 QQ 昵称。
4. 逐段调用 `_render_quoted_ai_segment()`。
5. 将引用图片加入当前视觉图片列表。
6. 生成包含时间、发言人和完整内容的富文本。

获取失败时返回 `[引用回复(获取信息失败)]`，而不是中断整个问题。

### 7.8 `_render_ai_segment()`

渲染当前用户消息的单个顶层段。主要特殊点：

- 删除用户对机器人的 `at`。
- 对其他成员的 `at` 尽量补全昵称。
- 外层 `reply` 进入 `_render_ai_reply()`。
- 无摘要图片加入视觉列表。
- 未知类型使用 `[seg_type]` 兜底。

### 7.9 `parse()`

解析器总入口：

- 输入是普通字符串且用于数据库：把所有 CQ 码替换为 `[媒体/表情]`。
- 输入是普通字符串且用于 AI：返回空文本；正常实时事件应传 OneBot 消息对象。
- 输入可迭代：逐段解析并拼接。
- 最后对整体文本 `strip()`。
- 返回 `ParsedMessage(text, image_ids)`。

### 7.10 两个兼容入口

- `parse_message_content()`：数据库存储入口，只返回 `.text`。
- `extract_text_and_image_ids()`：AI 富文本入口，返回 `(text, image_ids)` 元组。

这两个函数保留旧调用方式，外部代码无需认识 `ParsedMessage`。

## 8. 动态历史记录长度

### 8.1 `get_dynamic_history_length()`

固定范围：

- 最少 50 条。
- 最多 500 条。
- 失败默认 80 条。
- 只统计最近 2 小时的消息。

数据库查询按 `timestamp DESC, rowid DESC` 排序。无近期消息时直接返回 80，不调用前置模型。

#### 固定算法模式

当 `ENABLE_AI_HISTORY_DECISION=False`：

1. 最近 1 小时消息全部计入。
2. 1～2 小时消息随机计入 50%～100%。
3. 最终限制到 50～500。

随机比例使上下文长度不会每次机械固定，但也意味着相同活跃度可能得到不同条数。

#### AI 决策模式

当 `ENABLE_AI_HISTORY_DECISION=True`：

1. 统计最近 10 分钟、10～30 分钟、30～60 分钟、1～2 小时的消息量。
2. 让 `DYNAMIC_HISTORY_MODEL` 只回复一个数字。
3. OpenAI 使用简化的 `messages` 请求；Gemini 使用 `contents/parts`。
4. 从正文中提取第一个数字。
5. 将结果限制到 50～500。
6. 非 200、异常或无数字均返回 80。

前置模型只决定条数，不读取具体群聊正文。

## 9. 图片处理

### 9.1 `get_local_image_as_base64()`

处理流程：

1. `file_id` 为空时返回 `None`。
2. 调用 `bot.get_image(file=file_id)` 获取 NapCat 本地路径。
3. `IMAGE_BASE_DIR` 为空：直接使用返回路径。
4. `IMAGE_BASE_DIR` 非空：只取返回路径的文件名，与映射目录拼接。
5. 轮询等待文件存在、是普通文件且大小大于 0。
6. 在线程池读取文件，避免同步磁盘读取阻塞事件循环。
7. 转为 Base64 字符串。

失败或等待超时的图片会被跳过，最终回复头的图片数是“成功读取并实际发送”的数量，而不是原消息中的图片段数量。

当前 OpenAI 和 Gemini 请求都把图片声明为 JPEG MIME/Data URL；这是保留的既有行为，即使底层文件扩展名可能不是 `.jpg`。

### 9.2 `ChatService.load_base64_images()`

按顺序逐张调用 `get_local_image_as_base64()`，只保留成功结果。当前是串行读取，以保持行为简单和顺序稳定。

## 10. API 正文解析

### 10.1 `_get_openai_message()`

安全取得：

```text
data.choices[0].message
```

任何一级类型不正确或缺失都返回空字典，避免大量直接索引导致异常。

### 10.2 `_extract_api_reply_text()`

- OpenAI：读取第一条 `message.content`，必须是字符串。
- Gemini：读取第一条 candidate 的 `content.parts`，从后向前寻找最后一个非空 `text`。
- 数据格式不合法或无正文时返回空字符串。

`ChatService.complete()` 会把最终空正文替换成 `（模型API拒绝回复）`。

## 11. 模型原生搜索

### 11.1 `_enable_openai_native_search()`

该函数只负责修改 OpenAI 兼容请求，并返回“适配器标识”：

| `model_id` 特征 | 请求参数 | 适配器 |
|---|---|---|
| 包含 `glm` | 带 `enable=True` 的 `web_search` 工具 | `glm_web_search` |
| 包含 `moonshot` | `$web_search` 内置函数 | `moonshot_web_search` |
| 其他 | `web_search=True, network=True` | `generic_search` |

以后适配新 OpenAI 兼容中转时，应优先在此扩展请求格式，不要在 `handle_ai_chat()` 增加模型判断。

### 11.2 `_parse_openai_native_search_response()`

该函数统一解析 OpenAI 兼容回包中的搜索证据，优先级如下：

1. OpenAI `message.tool_calls` 数量。
2. `server_side_tool_usage` 中键名包含 `SEARCH` 的整数合计。
3. `num_server_side_tools_used`。
4. `num_sources_used`。
5. `citations` 或 `sources` 列表长度。

如果搜索已启用，但中转没有返回任何可核实字段，则构造：

```text
performed=None, count=None
```

最终回复头不显示搜索字段。

### 11.3 Gemini 原生搜索

请求通过：

```json
{"tools": [{"googleSearch": {}}]}
```

`ChatService.parse_gemini_reply()` 解析：

1. 优先使用 `groundingMetadata.webSearchQueries` 数量。
2. 没有查询列表时，统计 `groundingChunks` 中包含 `web` 的条目。
3. 无 grounding 证据时不显示搜索数字。

### 11.4 当前待讨论点

不同中转站对同一模型可能要求完全不同的请求字段，用户也难以理解手工 `api_type` 或搜索适配器名称。当前仍采用配置加 `model_id` 特征判断，尚未实现自动试探。

若以后实现自动探测，应考虑：

- 只在何种错误码或回包特征下重试。
- 如何避免一次用户提问产生多个计费请求。
- 探测结果是否按模型/端点缓存。
- OpenAI 与 Gemini 请求能否安全互试。
- 原生搜索不生效但请求成功时如何识别。
- 如何避免把正常“没有搜”误判为“不支持搜索”。

这部分应单独设计，不应直接在主 Handler 中堆叠重试。

## 12. 第三方博查搜索

### 12.1 `THIRD_SEARCH_TOOL`

提供给 OpenAI 兼容模型的函数工具：

- 工具名：`web_search`。
- 必填参数：`query`。
- 可选参数：`include`、`exclude`。
- 返回数量和是否生成摘要不交给模型控制。

### 12.2 `bocha_search()`

请求固定包含：

- `query`。
- `freshness="noLimit"`。
- `summary=True`。
- `count=THIRD_SEARCH_COUNT`。

`include`、`exclude` 仅在非空时传入。

返回三元组：

```text
(格式化搜索文本, 结果数量, 是否成功)
```

状态区别：

- 没有结果：`("", 0, True)`，请求成功但结果为空。
- 缺 Key、缺查询词、非 200、API 失败码、超时或异常：成功标记为 `False`。

每条结果可包含标题、链接、来源、时间、摘要和与摘要不同的全文概要。响应中的结果会逐条校验；格式异常或没有任何有效正文信息的条目会被跳过，不会导致同批其他有效结果丢失。返回数量只统计可用条目。

### 12.3 `_execute_web_search()`

批量执行一轮 `tool_calls`：

1. 校验 `tool_calls` 数组及每项的 `id/type/function/name/arguments` 结构。
2. 结构完整的调用以规范化 assistant 工具调用写入 `messages`。
3. 未知工具名通过对应的 `role=tool` 结果反馈给模型。
4. 解析 `function.arguments` JSON。
5. 参数不是对象、类型错误或查询词为空时，向 `messages` 追加明确工具错误。
6. 调用 `bocha_search()`并累计结果数量。
7. 将搜索文本、`搜索无结果` 或 `搜索失败` 作为 `role=tool` 结果追加。
8. 无法组成合法工具消息的结构错误通过系统反馈文本交给模型修正。

只返回本批取得的结果数。单次搜索是否成功仅用于向模型反馈 `搜索无结果` 或 `搜索失败`，不再向工作流累计失败状态。

工具调用结构错误和参数格式错误都会反馈给模型修正，并占用当前搜索轮次，但不直接算作博查 API 失败。若后续仍有轮次且模型再次请求工具，则继续进入下一轮。

### 12.4 `_run_openai_third_search_workflow()`

处理完整工具循环：

1. 从首轮回包读取 `tool_calls`。
2. 首轮没有调用工具：直接返回正文，标记“已提供搜索能力但未搜索”。
3. 有工具调用：执行第一批搜索，追加合法的 assistant/tool 消息或结构错误反馈。
4. 继续调用模型，让模型阅读结果、回答或修正搜索词。
5. 非最后一轮继续提供搜索工具。
6. 最后一轮移除工具，并向系统提示词追加“搜索轮次已用完，必须直接回答”。
7. 累计所有批次的结果数量；总数大于 0 时显示实际总数，即使中间有轮次失败。
8. 只有所有批次最终都没有取得结果时，才以 `performed=True, count=0` 显示 `搜索：False`，包括空结果、网络错误、请求失败和工具调用格式错误。

在默认 `MAX_SEARCH_ROUNDS=3` 时，搜索批次最多为三批：首轮一批，加上后续最多两批；最后一次模型请求被强制生成文本。

`MAX_SEARCH_ROUNDS <= 0` 时，模型首轮请求不会注册第三方搜索工具，也不会进入该工作流。

后续模型请求非 200 会向上抛出，由主 Handler 的通用异常边界处理。

## 13. 历史文本和提示词

### 13.1 `_format_history_text()`

内部包含两个局部转换函数：

- `convert_reply_time()`：把数据库中的 Unix 时间戳转换为 `月-日 时:分:秒`，并把引用发言人 ID 转成显示名。
- `convert_at()`：把 `[@数字QQ号]` 转成 `[@可读显示名]`。

每条历史最终格式：

```text
[月-日 时:分] 群名片（QQ昵称：昵称）: 消息内容
```

历史顺序是从旧到新。

### 13.2 `ChatService.build_prompts()`

完整步骤：

1. 生成当前本地时间。
2. 组合当前提问者的群名片和 QQ 昵称。
3. 动态决定历史条数。
4. 查询历史记录并生成昵称映射。
5. 格式化历史文本。
6. 查询机器人在当前群的名片，失败时使用启动时取得的 QQ 昵称。
7. 用机器人身份填充模式提示词。
8. 将历史、当前时间、提问者名称和当前问题组成用户提示词。
9. 若模型支持视觉且至少一张图片成功读取，给系统提示词追加“这些是当前提问附件”的说明。

有历史时的结构：

```text
--- 真实群聊历史记录 ---
历史内容
------------------------

现在是 当前时间，用户 显示名 正在向你提问：
当前问题
```

无历史时省略历史区块。

## 14. `ChatService` 业务编排

### 14.1 `select_model()`

只根据 `event.get_plaintext().strip()` 判断模型和模式，不处理图片或引用。返回 `ModelSelection`。

### 14.2 `build_model_request()`

#### OpenAI 兼容格式

- 认证：`Authorization: Bearer ...`。
- 基础字段：`model`、`messages`、`stream=False`。
- 无视觉图片时，用户 `content` 是字符串。
- 有视觉图片时，用户 `content` 是多段列表：
  - 第一段是文本。
  - 后续是 `image_url` Data URL。
- 原生搜索开启时调用 `_enable_openai_native_search()`。
- 无原生搜索但满足第三方条件时注册 `THIRD_SEARCH_TOOL`。

#### Gemini 格式

- 认证：`x-goog-api-key`。
- 系统提示词放在 `systemInstruction.parts`。
- 用户文本和图片放在 `contents[0].parts`。
- 图片使用 `inlineData`。
- 原生搜索使用 `googleSearch` 工具。

未知 `api_type` 抛出 `UnsupportedAPITypeError`。

### 14.3 `parse_gemini_reply()`

同时提取正文和 grounding 搜索计数，详见原生搜索章节。

### 14.4 `send_model_request()`

1. 使用共享会话发送首轮请求。
2. 首轮非 200 抛出 `ModelHTTPError`，保留上游响应文本。
3. OpenAI + 第三方搜索：进入独立搜索工作流。
4. OpenAI + 原生搜索：提取正文后统一解析搜索信息。
5. 普通 OpenAI：只返回正文。
6. Gemini：调用 `parse_gemini_reply()`。

### 14.5 `complete()`

一次正式对话的总编排：

1. 下载并编码图片。
2. 构建系统提示词、用户提示词和历史行。
3. 构建对应协议请求。
4. 获取共享 HTTP 会话。
5. 发送请求并解析。
6. 空正文替换为 `（模型API拒绝回复）`。
7. 返回正文、实际历史数和实际图片数。

`complete()` 不直接发送 QQ 消息，也不直接操作数据库，使业务层可单独测试。

## 15. 回复头与消息发送

### 15.1 `_format_reply_prefix()`

基础格式：

```text
模型：模型名，记录：历史条数
```

按需追加：

- 至少一张图片成功发送给模型 → `，图片：n`。
- `SearchResult.prefix_value()` 有值 → `，搜索：n/False`。

最后追加换行，模型正文紧随其后。

### 15.2 `send_and_save()`

职责是“发送 QQ 消息 + 成功后保存机器人消息 + 可选结束 Handler”：

1. 首次调用 `matcher.send()`。
2. 发送失败写 `warning`。
3. 正式消息 `is_finish=True` 时，间隔 1 秒最多重试 3 次。
4. 快速回复不重试。
5. 只有返回字典且带 `message_id` 时才尝试存库。
6. 机器人身份优先从登录信息和群成员信息获取。
7. 身份查询失败时回退 `AI助手` 和 `bot.self_id`。
8. 使用数据库消息解析器规范化机器人发出的 `MessageSegment`。
9. 调用 `insert_message_to_db()`。
10. `is_finish=True` 时调用 `matcher.finish()`。

即使所有正式发送尝试都失败，最后仍会结束当前 Handler；不会让同一事件无限挂起。

## 16. NoneBot 事件入口

### 16.1 `sync_history_on_startup()`

注册在 `driver.on_bot_connect`：

1. 尝试取得机器人 QQ 昵称，保存到 `_bot_nickname`。
2. 遍历所有白名单群。
3. 每群调用一次 `get_group_msg_history()`。
4. 每条消息提取 ID、时间、发言者、群名片和内容。
5. 使用数据库解析模式转换消息。
6. 调用 `insert_message_to_db()`。
7. 单条失败不影响同群其他消息。
8. 单群接口失败不影响其他群。

由于群消息表按 `message_id` 唯一，重复连接同步不会重复插入同一消息。

### 16.2 `record_chat_history()`

`priority=1, block=False`，用于被动记录：

- 非群消息跳过。
- 非白名单群跳过。
- `event.is_tome()` 跳过，避免和 AI Handler 重复处理触发消息。
- 保存昵称、群名片、用户 ID 和规范化消息。

### 16.3 `handle_ai_chat()`

`rule=to_me(), priority=50, block=True`，主处理流程：

1. 私聊等非群消息：回复“仅限群聊”并结束。
2. 非白名单群：直接返回。
3. 在调用 AI 前先保存用户触发消息。
4. 再检查原消息中是否真的存在对机器人的 `at`。
   - 只引用机器人、但没有手动 `@` 时，保存消息后结束，不触发 AI。
5. 从纯文本选择模型和模式。
6. 提取 AI 富文本和图片 ID。
7. 从富文本删除一次模型前缀。
8. 空文本且无图片：回复“何意味”。
9. 有图片但模型不支持视觉：回复能力错误。
10. 若开启快速提示：发送并保存 `Waiting……`。
11. 调用 `chat_service.complete()`。
12. 构建固定回复头。
13. `@` 提问者，发送正式回复并保存。

异常分流：

- `UnsupportedAPITypeError` → API 格式配置错误。
- `ModelHTTPError` → 首轮 HTTP 请求失败，并显示上游错误文本。
- `asyncio.TimeoutError` → 请求超时。
- `FinishedException` → 必须重新抛出，不能被通用异常吞掉。
- 其他异常 → 记录完整堆栈并回复“调用出错”。

## 17. 全部函数与方法速查

| 名称 | 类型 | 作用 |
|---|---|---|
| `SearchResult.prefix_value` | 方法 | 把搜索状态转成回复头值 |
| `ModelSelection.api_type` | 属性 | 获取协议类型，默认 OpenAI |
| `ModelSelection.api_url` | 属性 | 生成实际请求 URL |
| `ModelSelection.vision_enabled` | 属性 | 判断是否支持视觉 |
| `ModelSelection.search_enabled` | 属性 | 判断是否启用原生搜索 |
| `ModelSelection.use_third_search` | 属性 | 判断是否使用博查工具 |
| `ModelSelection.information` | 属性 | 生成快速提示的模型/模式文本 |
| `_get_openai_message` | 函数 | 安全读取 OpenAI 第一条 message |
| `_extract_api_reply_text` | 函数 | 统一提取 OpenAI/Gemini 正文 |
| `_enable_openai_native_search` | 函数 | 按模型特征组装原生搜索请求 |
| `_parse_openai_native_search_response` | 函数 | 统一解析 OpenAI 兼容回包中的搜索证据 |
| `init_http_session` | 启动钩子 | 创建共享 HTTP 会话 |
| `close_http_session` | 关闭钩子 | 关闭共享 HTTP 会话 |
| `get_http_session` | 函数 | 返回或惰性创建共享会话 |
| `validate_configuration` | 启动钩子 | 非阻断地报告配置问题 |
| `init_db` | 启动钩子 | 设置 WAL 并初始化表 |
| `get_dynamic_history_length` | 异步函数 | 算法或 AI 决定历史条数 |
| `OneBotMessageParser.__init__` | 方法 | 保存 Bot 与群号上下文 |
| `OneBotMessageParser._segment_type_and_data` | 静态方法 | 兼容字典和 MessageSegment |
| `OneBotMessageParser._format_member_at` | 方法 | 把 QQ ID 转成可读 `@` |
| `OneBotMessageParser._render_storage_segment` | 方法 | 渲染数据库单段 |
| `OneBotMessageParser._render_quoted_ai_segment` | 方法 | 渲染一层引用内部单段 |
| `OneBotMessageParser._render_ai_reply` | 方法 | 获取并展开一层引用 |
| `OneBotMessageParser._render_ai_segment` | 方法 | 渲染当前 AI 消息单段 |
| `OneBotMessageParser.parse` | 方法 | 消息解析总入口 |
| `parse_message_content` | 兼容函数 | 返回数据库存储文本 |
| `send_and_save` | 异步函数 | 发送、重试、保存机器人消息并结束 |
| `insert_message_to_db` | 异步函数 | 在单事务内写消息和用户资料 |
| `get_local_image_as_base64` | 异步函数 | 获取、等待、读取并编码图片 |
| `read_file` | 局部函数 | 在线程池中读取图片并转 Base64 |
| `extract_text_and_image_ids` | 兼容函数 | 返回 AI 富文本和图片 ID |
| `bocha_search` | 异步函数 | 调用并格式化博查结果 |
| `_execute_web_search` | 异步函数 | 执行一批工具调用 |
| `_run_openai_third_search_workflow` | 异步函数 | 管理多轮第三方搜索 |
| `_load_history_rows` | 异步函数 | 查询并正序返回历史 |
| `_load_user_display_map` | 异步函数 | 生成当前群昵称映射 |
| `convert_reply_time` | 局部函数 | 格式化历史引用时间/发言人 |
| `convert_at` | 局部函数 | 格式化历史中的数字 `@` |
| `_format_history_text` | 函数 | 生成模型可读群聊历史 |
| `_format_reply_prefix` | 函数 | 生成固定正式回复头 |
| `ChatService.select_model` | 静态方法 | 解析模型前缀和对话模式 |
| `ChatService.load_base64_images` | 静态异步方法 | 读取所有有效图片 |
| `ChatService.build_prompts` | 静态异步方法 | 准备身份、历史和提示词 |
| `ChatService.build_model_request` | 静态方法 | 生成 OpenAI/Gemini 请求 |
| `ChatService.parse_gemini_reply` | 静态方法 | 解析 Gemini 正文和搜索数 |
| `ChatService.send_model_request` | 静态异步方法 | 首轮请求和协议分流 |
| `ChatService.complete` | 异步方法 | 完成一次正式 AI 调用 |
| `sync_history_on_startup` | Bot 连接钩子 | 同步白名单群历史 |
| `record_chat_history` | Handler | 被动保存普通群消息 |
| `handle_ai_chat` | Handler | 处理完整 `@机器人` 对话 |

## 18. 日志与异常策略

### 18.1 `logger.exception` 的意义

`logger.exception("说明")` 应在 `except` 块中使用。它相当于错误级别日志，并自动附带当前异常的完整 traceback。

相比：

```python
logger.error(f"失败: {e}")
```

`logger.exception(...)` 还能看到：

- 异常具体发生在哪个文件和哪一行。
- 完整函数调用链。
- 异步任务经过了哪些协程。
- 原始异常类型和上下文异常。

这对“Handler 最后只看到调用失败，但真正错误发生在数据库、图片或 HTTP 解析深处”的问题非常有价值。

### 18.2 为什么不应全部使用 `logger.exception`

代价包括：

- 每次都会打印多行堆栈，日志量明显增加。
- 对超时、非 200、发送重试失败等预期场景，堆栈通常没有额外价值。
- 高频可恢复错误若使用 exception 会淹没真正故障。
- 异常文本可能带上游返回内容；部署时仍应控制日志访问权限。

因此当前采用：

| 级别 | 使用场景 |
|---|---|
| `info` | 数据库初始化、历史同步完成 |
| `warning` | 配置问题、非 200、超时、发送重试、图片落地超时等可预期失败 |
| `exception` | 无法预知原因的数据库、图片、搜索、同步或最终调用异常 |
| `error` | 缺少 `default`、默认模式无效等明显基础配置错误 |

### 18.3 当前 `logger.exception` 边界

| 位置 | 为什么需要 traceback | 失败后的行为 |
|---|---|---|
| 动态历史数据库查询 | 可能是路径、锁、表或 SQL 环境问题 | 使用默认条数 |
| 动态历史模型调用 | 可能是网络、JSON、协议或会话问题 | 使用默认条数 |
| 机器人消息存库 | 发送已成功，但身份解析或存库失败 | 不影响 Handler 结束 |
| 数据库写入 | 需要定位连接、表、锁或类型问题 | 当前消息不入库 |
| 图片读取/Base64 | 涉及 OneBot、路径、权限和线程池 | 跳过该图 |
| 博查搜索通用异常 | 涉及网络、JSON 和数据结构 | 标记搜索失败 |
| 历史查询 | 需要区分表、连接或数据问题 | 使用空历史 |
| 昵称映射查询 | 需要定位数据库问题 | 使用原 ID/默认昵称 |
| 单条/单群启动同步 | 单条数据结构和群接口故障都可能发生 | 隔离失败继续同步 |
| 最终模型通用异常 | 是所有未分类故障的最后边界 | 给用户发送错误提示 |

当前这些位置都位于有效的 `except` 块中，使用方式正确。日志字符串中已经包含 `{e}`，而 `logger.exception` 还会在 traceback 末尾显示一次异常；这略有重复但便于单行检索，不影响功能。

## 19. 关键容错与边界行为

1. 配置检查只报告，不阻止启动。
2. 数据库读失败时使用空历史或默认历史条数。
3. 单条历史同步失败不影响其他记录。
4. 图片读取失败只丢弃对应图片，不中断文字问题。
5. 引用获取失败保留占位文本。
6. 引用只展开一层，避免套娃请求。
7. 未知非空消息段保留 `[seg_type]`。
8. 模型空正文转换为明确占位。
9. 正式 QQ 回复发送失败最多重试三次。
10. `FinishedException` 必须重新抛出，维持 NoneBot 的流程控制。
11. 模型原生搜索无可核实统计字段时不显示搜索字段。
12. 第三方搜索最后一轮强制回答，避免模型一直要求继续搜索。

## 20. 扩展代码时应改哪里

### 新增 OneBot 消息段

根据用途修改：

- 数据库存储格式：`_render_storage_segment()`。
- 当前 AI 顶层格式：`_render_ai_segment()`。
- 引用内部格式：`_render_quoted_ai_segment()`。

三处都应保留未知类型 `else`，不要删除兜底。

### 新增 OpenAI 兼容原生搜索格式

1. 在 `_enable_openai_native_search()` 增加请求适配。
2. 在 `_parse_openai_native_search_response()` 增加回包证据解析。
3. 不要在 `handle_ai_chat()` 增加厂商分支。

### 新增正式 API 协议

1. 扩展 `ModelSelection.api_url`。
2. 扩展 `ChatService.build_model_request()`。
3. 扩展 `ChatService.send_model_request()` 或新增独立解析函数。
4. 扩展 `_extract_api_reply_text()`。
5. 扩展 `validate_configuration()` 支持集合。
6. 保持 Handler 不感知协议细节。

### 更换第三方搜索服务

优先只替换 `bocha_search()` 的请求和结果格式，维持其三元组契约。这样 `_execute_web_search()` 和多轮工作流无需改动。

### 修改数据库

数据库属于高风险区域。修改前先确认：

- 是否需要迁移已有数据库。
- 表名是否仍只来自白名单群。
- `message_id` 去重是否保留。
- 三类数据是否仍在一个事务内提交。
- 历史排序和当前消息排除是否保留。
- 启动同步、被动记录、触发消息和机器人回复是否仍全部覆盖。

## 21. 维护和回归检查建议

每次修改后至少执行：

```bash
python -m py_compile easy_ai.py pz.py
git diff --check
```

按改动范围增加检查：

| 改动区域 | 建议验证 |
|---|---|
| 消息解析 | 文本、at、图片、表情、文件、语音、视频、引用、未知类型 |
| 引用解析 | `get_msg` 只调用一次；引用图片进入视觉列表；嵌套引用不展开 |
| 数据库 | 比较 SQL 模板、事务顺序和四类保存入口 |
| 模型选择 | 默认、大写、小写、未知前缀 |
| OpenAI | 纯文本、视觉、原生搜索、第三方搜索 |
| Gemini | 纯文本、`inlineData`、`googleSearch`、grounding 计数 |
| 第三方搜索 | 无工具调用、单轮、多轮、无结果、失败、参数错误 |
| 发送 | 快速回复、正式回复重试、存库失败、`finish()` |

## 22. 后续接手的推荐阅读顺序

若只想快速定位问题：

1. 先读本文第 2 节调用图。
2. 用户消息理解错误：读第 7 节和 `OneBotMessageParser`。
3. 历史不正确：读第 6、8、13 节。
4. 图片不工作：读第 9 节。
5. 模型请求不兼容：读第 10、11、14 节。
6. 博查搜索异常：读第 12 节。
7. QQ 不回复或重复保存：读第 15、16 节。
8. 线上出现堆栈：按第 18 节判断是可恢复失败还是非预期故障。

若准备修改代码：

1. 明确改动属于解析、协议、业务还是 Handler。
2. 尽量在对应层扩展，不跨层复制逻辑。
3. 对照本文的数据库不变量、关键容错和边界行为，确认旧功能没有丢失。
4. 特别保护第 6 节数据库不变量。
5. 运行第 21 节的回归检查。
