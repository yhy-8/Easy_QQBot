# `easy_ai.py` 代码与架构说明

> 文档目的：帮助维护者快速理解 `easy_ai.py` 的整体结构、完整调用链、数据库不变量、消息格式、模型协议、搜索行为和异常边界。  
> 对应代码：当前单文件版 `easy_ai.py`。  
> 最后同步时间：2026-08-22 20:15（Asia/Shanghai）。

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

### 2.1 原文件从头到尾的架构树

```text
easy_ai.py
│
├─ 导入、配置、提示词与全局状态
│  ├─ 基础配置 / 第三方搜索配置 / MODELS_CONFIG
│  ├─ SERIOUS_SYSTEM_PROMPT / CASUAL_SYSTEM_PROMPT / MODE_PROMPTS
│  └─ _bot_nickname
│
├─ 结构化领域对象
│  ├─ VisualAttachment / ParsedMessage / ResolvedVisual
│  │  └─ ResolvedVisual.sendable
│  ├─ _visual_placeholder_token()
│  ├─ SearchResult / ModelReply / ModelSelection
│  │  ├─ SearchResult.prefix_value()
│  │  ├─ ModelSelection.api_type / api_url
│  │  ├─ ModelSelection.vision_enabled / search_enabled
│  │  └─ ModelSelection.use_third_search / information
│  ├─ PreparedModelRequest / ChatCompletion
│  └─ UnsupportedAPITypeError / ModelHTTPError
│
├─ API 回包与 OpenAI 原生搜索适配
│  ├─ _get_openai_message()
│  ├─ _extract_api_reply_text()
│  ├─ _enable_openai_native_search()
│  └─ _parse_openai_native_search_response()
│
├─ Driver、HTTP 生命周期、配置检查与数据库初始化
│  ├─ driver / _http_session
│  ├─ init_http_session() / close_http_session() / get_http_session()
│  ├─ validate_configuration()
│  └─ init_db()
│
├─ 动态历史条数
│  └─ get_dynamic_history_length()
│
├─ OneBotMessageParser
│  ├─ __init__()
│  ├─ _segment_type_and_data()
│  ├─ _image_placeholder() / _image_file_id() / _render_ai_image()
│  ├─ _format_member_at()
│  ├─ _render_storage_segment()
│  ├─ _render_quoted_ai_segment() / _render_ai_reply()
│  ├─ _render_ai_segment()
│  └─ parse()
│
├─ 数据库存储、QQ 发送与写库
│  ├─ parse_message_content()
│  ├─ send_and_save()
│  │  └─ _send_loop()
│  └─ insert_message_to_db()
│
├─ 视觉附件与 AI 富文本
│  ├─ _append_visual_notice()
│  ├─ _detect_image_format()
│  ├─ resolve_visual_attachment()
│  │  ├─ read_header()
│  │  └─ read_file()
│  └─ extract_ai_content()
│
├─ 第三方博查搜索
│  ├─ THIRD_SEARCH_FRESHNESS_VALUES / _is_valid_search_freshness()
│  ├─ THIRD_SEARCH_TOOL
│  ├─ bocha_search()
│  │  └─ clean_text()
│  ├─ _execute_web_search()
│  └─ _run_openai_third_search_workflow()
│
├─ 历史读取、格式化与回复头
│  ├─ _load_history_rows()
│  ├─ _load_user_display_map()
│  ├─ _format_history_text()
│  │  ├─ convert_reply_time()
│  │  └─ convert_at()
│  └─ _format_reply_prefix()
│
├─ ChatService
│  ├─ select_model()
│  ├─ resolve_visuals() / apply_visual_placeholders()
│  ├─ build_prompts()
│  ├─ build_model_request()
│  ├─ parse_gemini_reply()
│  ├─ send_model_request()
│  └─ complete()
│
├─ chat_service 实例
│
└─ NoneBot 生命周期与事件入口
   ├─ is_message_to_bot()     事件入口层共享触发判定规则
   ├─ sync_history_on_startup()
   ├─ record_handler → record_chat_history()
   └─ chat_handler → handle_ai_chat()
```

### 2.2 Bot 调用流程树

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
├─ Bot 关闭
│  └─ close_http_session()
│
├─ 普通群消息
│  └─ record_chat_history()
│     ├─ is_message_to_bot() 为真 → 跳过（交由 chat_handler 处理）
│     └─ 否则 parse_message_content() → insert_message_to_db()
│
└─ @机器人 / 提及机器人
   └─ is_message_to_bot() 通过 → handle_ai_chat()
      ├─ 先保存触发消息
      ├─ ChatService.select_model()
      ├─ extract_ai_content()
      ├─ 可选发送 Waiting……
      └─ ChatService.complete()
         ├─ resolve_visuals()
         ├─ apply_visual_placeholders()
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
         └─ _send_loop()
```

### 2.3 可跳转的源码顺序导航

以下顺序与 `easy_ai.py` 一致；函数、方法和属性名称均可点击跳到详细说明。

- 导入、配置与提示词
  - 导入 `aiohttp`、`aiosqlite`、NoneBot/OneBot、数据类、路径、编码、时间、正则和异步工具。
  - [基础、第三方搜索与模型配置](#configuration)决定白名单、数据库、超时、模式、视觉和搜索能力。
  - [系统提示词与全局机器人昵称](#prompts-and-global-state)提供严肃/随性约束和运行时身份。
- 结构化领域对象
  - [`VisualAttachment`](#class-visual-attachment)保存图片标签及候选文件来源。
  - [`ParsedMessage`](#class-parsed-message)统一返回文本和视觉附件。
  - [`ResolvedVisual`](#class-resolved-visual)保存格式判断结果；[`sendable`](#prop-resolved-visual-sendable)判断能否发送。
  - [`_visual_placeholder_token()`](#fn-visual-placeholder-token)保留视觉附件在富文本中的原位置。
  - [`SearchResult`](#class-search-result)统一搜索状态；[`prefix_value()`](#method-search-result-prefix-value)生成回复头字段。
  - [`ModelReply`](#class-model-reply)组合模型正文和搜索状态。
  - [`ModelSelection`](#class-model-selection)保存模型选择，并通过 [`api_type`](#prop-model-selection-api-type)、[`api_url`](#prop-model-selection-api-url)、[`vision_enabled`](#prop-model-selection-vision-enabled)、[`search_enabled`](#prop-model-selection-search-enabled)、[`use_third_search`](#prop-model-selection-use-third-search) 和 [`information`](#prop-model-selection-information) 暴露运行时决策。
  - [`PreparedModelRequest`](#class-prepared-model-request)保存已组装的协议请求。
  - [`ChatCompletion`](#class-chat-completion)把正文、历史数和图片数交给事件层。
  - [`UnsupportedAPITypeError`](#exception-unsupported-api-type)与 [`ModelHTTPError`](#exception-model-http)区分配置错误和首轮 HTTP 错误。
- API 回包与 OpenAI 原生搜索适配
  - [`_get_openai_message()`](#fn-get-openai-message)取得第一条 OpenAI message。
  - [`_extract_api_reply_text()`](#fn-extract-api-reply-text)统一提取 OpenAI/Gemini 正文。
  - [`_enable_openai_native_search()`](#fn-enable-openai-native-search)按模型特征写入联网参数。
  - [`_parse_openai_native_search_response()`](#fn-parse-openai-native-search-response)从回包证据统计搜索。
- Driver、HTTP 生命周期、配置检查与数据库初始化
  - [`driver`](#driver)注册生命周期钩子；[`_http_session`](#http-session)保存共享会话。
  - [`init_http_session()`](#fn-init-http-session)启动时建会话；[`close_http_session()`](#fn-close-http-session)关闭会话；[`get_http_session()`](#fn-get-http-session)提供惰性兜底。
  - [`validate_configuration()`](#fn-validate-configuration)非阻断地报告配置问题。
  - [`init_db()`](#fn-init-db)设置 WAL 并创建用户表和白名单群表。
- 动态历史条数
  - [`get_dynamic_history_length()`](#fn-get-dynamic-history-length)按原 2 小时固定算法，或结合最近 6 小时消息密度与当前消息富文本的前置模型，决定 50～500 条历史。
- OneBot 消息解析
  - [`OneBotMessageParser`](#class-onebot-message-parser)统一数据库文本和 AI 富文本两条解析分支。
    - [`__init__()`](#method-parser-init)保存 Bot 与群号上下文。
    - [`_segment_type_and_data()`](#method-parser-segment-type-and-data)兼容字典和 `MessageSegment`。
    - [`_image_placeholder()`](#method-parser-image-placeholder)取得任意 `summary` 标签。
    - [`_image_file_id()`](#method-parser-image-file-id)选择 `get_image()` 文件标识。
    - [`_render_ai_image()`](#method-parser-render-ai-image)登记视觉附件并写入内部位置标记。
    - [`_format_member_at()`](#method-parser-format-member-at)把 QQ ID 转成可读 `@`。
    - [`_render_storage_segment()`](#method-parser-render-storage-segment)渲染数据库单段。
    - [`_render_quoted_ai_segment()`](#method-parser-render-quoted-ai-segment)渲染一层引用内部单段。
    - [`_render_ai_reply()`](#method-parser-render-ai-reply)获取并展开外层引用一次。
    - [`_render_ai_segment()`](#method-parser-render-ai-segment)渲染当前提问单段。
    - [`parse()`](#method-parser-parse)遍历消息并返回 `ParsedMessage`。
  - [`parse_message_content()`](#fn-parse-message-content)提供数据库存储兼容入口。
- QQ 发送与数据库写入
  - [`_send_loop()`](#fn-send-loop)无限重发一条消息直到成功，日志含重试编号；由外层超时统一收口。
  - [`send_and_save()`](#fn-send-and-save)统一重试发送、发送超时/彻底失败改发备用提示、保存机器人消息并结束 Handler。
  - [`insert_message_to_db()`](#fn-insert-message-to-db)在一个事务内写消息和两类用户资料。
- 视觉附件与 AI 富文本
  - [`_append_visual_notice()`](#fn-append-visual-notice)把未发送原因写回原标签。
  - [`_detect_image_format()`](#fn-detect-image-format)按文件头识别格式和可用 MIME。
  - [`resolve_visual_attachment()`](#fn-resolve-visual-attachment)取得路径、等待落地、识别格式并按需编码；内部 [`read_header()`](#local-read-header)只读文件头，[`read_file()`](#local-read-file)读取完整文件并转 Base64。
  - [`extract_ai_content()`](#fn-extract-ai-content)提供 AI 富文本与视觉附件兼容入口。
- 第三方博查搜索
  - [`THIRD_SEARCH_FRESHNESS_VALUES`](#third-search-freshness-values)列出预设时间范围；[`_is_valid_search_freshness()`](#fn-is-valid-search-freshness)同时校验单日和日期范围。
  - [`THIRD_SEARCH_TOOL`](#third-search-tool)定义模型可调用的 `web_search` 工具。
  - [`bocha_search()`](#fn-bocha-search)调用并格式化博查结果；内部 [`clean_text()`](#local-clean-text)清理可选文本字段。
  - [`_execute_web_search()`](#fn-execute-web-search)校验并执行一批工具调用。
  - [`_run_openai_third_search_workflow()`](#fn-run-openai-third-search-workflow)管理多轮搜索和最后强制回答。
- 历史读取与回复头
  - [`_load_history_rows()`](#fn-load-history-rows)查询、排除当前消息并恢复旧到新顺序。
  - [`_load_user_display_map()`](#fn-load-user-display-map)生成群名片/QQ 昵称映射。
  - [`_format_history_text()`](#fn-format-history-text)格式化历史；内部 [`convert_reply_time()`](#local-convert-reply-time)处理引用，[`convert_at()`](#local-convert-at)处理数字 `@`。
  - [`_format_reply_prefix()`](#fn-format-reply-prefix)生成固定正式回复头。
- `ChatService` 业务编排
  - [`select_model()`](#method-chat-service-select-model)解析模型前缀和模式。
  - [`resolve_visuals()`](#method-chat-service-resolve-visuals)并行解析图片。
  - [`apply_visual_placeholders()`](#method-chat-service-apply-visual-placeholders)把格式结果放回原位置。
  - [`build_prompts()`](#method-chat-service-build-prompts)准备身份、历史和提示词。
  - [`build_model_request()`](#method-chat-service-build-model-request)组装 OpenAI/Gemini 请求。
  - [`parse_gemini_reply()`](#method-chat-service-parse-gemini-reply)解析 Gemini 正文和 grounding 证据。
  - [`send_model_request()`](#method-chat-service-send-model-request)发送首轮并按协议/搜索方式分流。
  - [`complete()`](#method-chat-service-complete)串联一次正式 AI 调用并返回 `ChatCompletion`。
  - [`chat_service`](#chat-service-instance)是事件层复用的服务实例。
- NoneBot 生命周期与事件入口
  - [`is_message_to_bot()`](#fn-is-message-to-bot)事件入口层共享触发判定规则；`record_chat_history()` 与 `chat_handler` 共用。
  - [`sync_history_on_startup()`](#fn-sync-history-on-startup)在 Bot 连接后同步白名单群历史。
  - [`record_handler`](#record-handler)注册被动记录器；[`record_chat_history()`](#fn-record-chat-history)保存普通群消息。
  - [`chat_handler`](#chat-handler)注册 `@机器人` 处理器；[`handle_ai_chat()`](#fn-handle-ai-chat)完成入口校验、业务调用、回复和异常分流。

### 2.4 六层职责

| 层 | 主要对象 | 职责 |
|---|---|---|
| 配置层 | [全局配置、提示词、`MODELS_CONFIG`](#configuration) | 决定白名单、模型、超时、视觉与搜索能力 |
| 领域对象层 | [`ParsedMessage`](#class-parsed-message)、[`SearchResult`](#class-search-result)、[`ModelReply`](#class-model-reply) 等 | 用明确对象传递结果，减少 `bool/int/dict` 混用 |
| 基础设施层 | [HTTP 会话](#http-session)、[SQLite](#database-design)、OneBot API | 管理外部资源和持久化 |
| 解析/协议层 | [`OneBotMessageParser`](#class-onebot-message-parser)、[搜索解析函数](#fn-parse-openai-native-search-response) | 适配消息段和不同上游回包 |
| 业务编排层 | [`ChatService`](#class-chat-service) | 组织图片、历史、提示词、请求和模型回复 |
| 事件入口层 | [三个 Handler/钩子](#nonebot-events) | 响应框架事件、校验、发送和结束事件 |

<a id="configuration"></a>

## 3. 配置、提示词与全局状态

<a id="basic-configuration"></a>

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
| `SEND_RETRY_TIMEOUT` | 单条 QQ 消息发送总超时 | 期间无限重发，超时判定为发送失败（如敏感词被拦截）并进入备用流程 |
| `IMAGE_BASE_DIR` | NapCat 图片目录映射 | 留空信任 NapCat 绝对路径；非空时只取文件名再拼接 |
| `DEFAULT_MODE` | 无模型前缀时的模式 | 必须是 `serious` 或 `casual` |

<a id="third-search-configuration"></a>

### 3.2 第三方搜索配置

| 配置 | 作用 |
|---|---|
| `THIRD_SEARCH_API_KEY` | 博查 API Key；为空时第三方搜索不会启用 |
| `THIRD_SEARCH_API_URL` | 博查搜索端点 |
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

<a id="models-config"></a>

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
- B 的显示名称为 `gemini-3-flash`，实际模型 ID 为
  `gemini-3-flash-preview`。
- C 的显示名称为 `gemini-3.1-pro`，实际模型 ID 为
  `gemini-3.1-pro-preview`。

<a id="prompts-and-global-state"></a>

### 3.4 提示词与全局状态

- `SERIOUS_SYSTEM_PROMPT`：强调客观、禁止猜测、具备联网能力时优先搜索。
- `CASUAL_SYSTEM_PROMPT`：允许轻松语气，但不塑造独立人设。
- 两者都禁止模型仿造程序生成的 `模型/记录/图片/搜索` 回复头。
- `{bot_identity}` 在运行时替换为机器人群名片和 QQ 昵称。
- `MODE_PROMPTS` 负责从模式名映射到提示词。

`_bot_nickname` 初始为 `AI助手`，Bot 连接后由 `sync_history_on_startup()` 尝试更新；群内身份组装失败时仍以它回退。

<a id="domain-objects"></a>

## 4. 领域对象与异常类型

<a id="class-visual-attachment"></a>

### 4.1 `VisualAttachment`

`VisualAttachment` 保留 OneBot `image` 段的必要信息：

- `placeholder`：原消息 `summary` 转成字符串并去除首尾空白后的标签；结果为空时
  为 `[图片]`。
- `file_id`：优先供 `get_image()` 查询最终本地文件。
- `local_path`：消息段自带的 `path`，在 `get_image()` 无法提供路径时回退使用。

<a id="class-parsed-message"></a>

### 4.2 `ParsedMessage`

OneBot 消息解析结果：

- `text`：数据库存储文本或 AI 富文本。
- `visual_attachments`：需要进一步判断格式的视觉附件。

数据库解析通常只使用 `text`；AI 解析同时使用二者。

<a id="class-resolved-visual"></a>

### 4.3 `ResolvedVisual`

`ResolvedVisual` 表示完成本地路径和文件头判断后的视觉附件：

- `placeholder`：最终放入 AI 文本的原标签或未发送原因。
- `mime_type`：支持格式对应的 MIME；不发送时为 `None`。
- `base64_data`：成功读取后的图片数据；不发送时为 `None`。

<a id="prop-resolved-visual-sendable"></a>

- `sendable`：同时具有 MIME 和 Base64 数据时为真。

<a id="fn-visual-placeholder-token"></a>

### 4.4 `_visual_placeholder_token()`

按附件索引生成只在富文本组装阶段使用的内部标记。它保留图片在原消息中的位置，随后由 `ChatService.apply_visual_placeholders()` 替换，不会进入最终提示词。

<a id="class-search-result"></a>

### 4.5 `SearchResult`

统一表达一次回复的搜索状态：

| 字段 | 含义 |
|---|---|
| `performed` | `True`=确认发生；`False`=确认未发生；`None`=中转没有提供可核实信息 |
| `count` | 可核实的搜索/来源/工具数量 |

<a id="method-search-result-prefix-value"></a>

`prefix_value()` 将内部状态转换成回复头：

1. `count > 0` → `搜索：具体数字`。
2. `performed=True, count=0` → `搜索：False`。
3. 其他情况不显示搜索字段，包括已启用搜索但中转没有提供可核实信息的 `performed=None, count=None`。

因此，“已开启搜索”不等于真的发生了搜索；程序只显示可核实的正数，或搜索实际调用后所有轮次均未取得结果的状态。某一轮失败不会覆盖其他轮已经取得的结果数。

<a id="class-model-reply"></a>

### 4.6 `ModelReply`

- `text`：模型最终正文。
- `search`：对应的 `SearchResult`。

<a id="class-model-selection"></a>

### 4.7 `ModelSelection`

保存一次用户消息的模型选择：

- `mode`：`serious` 或 `casual`。
- `prefix_to_remove`：需要从 AI 富文本中去掉的 `/A`、`/a` 等前缀。
- `config`：模型配置字典。

<a id="prop-model-selection-api-type"></a>
<a id="prop-model-selection-api-url"></a>
<a id="prop-model-selection-vision-enabled"></a>
<a id="prop-model-selection-search-enabled"></a>
<a id="prop-model-selection-use-third-search"></a>
<a id="prop-model-selection-information"></a>

属性说明：

- `api_type`：缺省为 `openai`。
- `api_url`：Gemini 自动拼成 `.../{model_id}:generateContent`。
- `vision_enabled`、`search_enabled`：读取能力开关。
- `use_third_search`：集中判断第三方搜索的四个必要条件。
- `information`：生成快速回复使用的 `模型名，SER/CAS`。

<a id="class-prepared-model-request"></a>

### 4.8 `PreparedModelRequest`

表示已组装完、可直接发送的请求：

- 通用部分：`api_type`、`api_url`、`headers`、`payload`。
- 第三方搜索需要保留：`system_prompt`、`user_message_content`。
- 原生搜索需要保留：`native_search_adapter`。
- `use_third_search` 表示发送首轮后是否进入工具调用工作流。

<a id="class-chat-completion"></a>

### 4.9 `ChatCompletion`

交回 Handler 的完整结果：

- `reply`：正文和搜索状态。
- `history_count`：实际传入提示词的历史消息行数。
- `image_count`：成功读取并实际发给模型的图片数。

<a id="custom-exceptions"></a>

### 4.10 自定义异常

<a id="exception-unsupported-api-type"></a>
<a id="exception-model-http"></a>

- `UnsupportedAPITypeError`：模型 `api_type` 不是 `openai` 或 `gemini`。
- `ModelHTTPError`：正式模型首轮请求返回非 200。

使用自定义异常后，Handler 可以给用户展示不同错误文案，而不必解析字符串。

<a id="api-response-and-native-search"></a>

## 5. API 正文与 OpenAI 原生搜索

<a id="fn-get-openai-message"></a>

### 5.1 `_get_openai_message()`

按标准 OpenAI 兼容响应取得：

```text
data.choices[0].message
```

字段缺失、列表为空或首项不是对象时通常返回空字典。这里默认服务器遵守标准
响应结构；若服务器以 HTTP 200 返回严重畸形的 `choices` 类型，异常会交给主
Handler 的通用错误边界处理，不额外兼容这种服务端错误。Gemini 的
`candidates` 采用相同原则。

<a id="fn-extract-api-reply-text"></a>

### 5.2 `_extract_api_reply_text()`

- OpenAI：读取第一条 `message.content`，必须是字符串。
- Gemini：读取第一条 candidate 的 `content.parts`，从后向前寻找最后一个非空 `text`。
- 数据格式不合法或无正文时返回空字符串。

`ChatService.complete()` 会把最终空正文替换成 `（模型API拒绝回复）`。

<a id="fn-enable-openai-native-search"></a>

### 5.3 `_enable_openai_native_search()`

该函数只负责修改 OpenAI 兼容请求，并返回“适配器标识”：

| `model_id` 特征 | 请求参数 | 适配器 |
|---|---|---|
| 包含 `glm` | 带 `enable=True` 的 `web_search` 工具 | `glm_web_search` |
| 包含 `moonshot` | `$web_search` 内置函数 | `moonshot_web_search` |
| 其他 | `web_search=True, network=True` | `generic_search` |

以后适配新 OpenAI 兼容中转时，应优先在此扩展请求格式，不要在 `handle_ai_chat()` 增加模型判断。

<a id="fn-parse-openai-native-search-response"></a>

### 5.4 `_parse_openai_native_search_response()`

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

<a id="native-search-tradeoffs"></a>

### 5.5 原生搜索适配的取舍

不同中转站对同一模型可能要求完全不同的请求字段，用户也难以理解手工 `api_type` 或搜索适配器名称。当前仍采用配置加 `model_id` 特征判断，尚未实现自动试探。

若以后实现自动探测，应考虑：

- 只在何种错误码或回包特征下重试。
- 如何避免一次用户提问产生多个计费请求。
- 探测结果是否按模型/端点缓存。
- OpenAI 与 Gemini 请求能否安全互试。
- 原生搜索不生效但请求成功时如何识别。
- 如何避免把正常“没有搜”误判为“不支持搜索”。

这部分应单独设计，不应直接在主 Handler 中堆叠重试。

<a id="runtime-and-database-init"></a>

## 6. HTTP 生命周期、启动检查与数据库初始化

<a id="driver"></a>

### 6.1 `driver`

`driver = get_driver()` 获取 NoneBot Driver，用于注册启动、关闭和 Bot 连接钩子。

<a id="http-session"></a>

### 6.2 `_http_session`

插件级共享 `aiohttp.ClientSession`。模型请求、动态历史模型和博查搜索共用该会话，避免每次请求都建立并销毁连接池。

<a id="fn-init-http-session"></a>

### 6.3 `init_http_session()`

NoneBot 启动时创建共享会话。只有会话不存在或已关闭时才新建。

<a id="fn-close-http-session"></a>

### 6.4 `close_http_session()`

NoneBot 关闭时关闭共享会话，并把全局变量恢复为 `None`，避免未关闭会话警告和连接泄漏。

<a id="fn-get-http-session"></a>

### 6.5 `get_http_session()`

返回共享会话。若启动钩子尚未执行或会话意外关闭，会惰性创建一个新会话，以兼容特殊插件加载顺序。

<a id="fn-validate-configuration"></a>

### 6.6 `validate_configuration()`

启动时检查：

- 是否存在 `default` 模型。
- `DEFAULT_MODE` 是否能在 `MODE_PROMPTS` 中找到。
- `DYNAMIC_HISTORY_MODEL` 是否存在。
- 每个模型是否包含七个必要字段。
- `api_type` 是否属于当前支持集合。

该函数只写日志，不修改配置，也不阻止启动，保持原有容错方式。

<a id="database-design"></a>

### 6.7 数据库设计

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

> 索引：每张聊天记录表都配有 `timestamp` 索引 `idx_{table}_timestamp`，由 `init_db()`
> 在启动时创建，用于加速按时间过滤和排序的历史查询。

<a id="fn-init-db"></a>

### 6.8 `init_db()`

启动时：

1. 连接 `DB_PATH`，连接超时 15 秒。
2. 设置 WAL。
3. 创建两张用户信息表。
4. 为 `ALLOWED_GROUPS` 中每个群创建消息表。
5. 提交事务。
6. 记录初始化完成日志。

`CREATE TABLE IF NOT EXISTS` 不会清空已有数据。

<a id="dynamic-history"></a>

## 7. 动态历史记录长度

<a id="fn-get-dynamic-history-length"></a>

### 7.1 `get_dynamic_history_length()`

共同条数范围：

- 最少 50 条。
- 最多 500 条。
- 失败默认 80 条。

数据库查询按 `timestamp DESC, rowid DESC` 排序。固定算法只查询最近 2 小时，AI 决策查询最近 6 小时。对应窗口内无消息时直接返回 80，不调用前置模型。

#### 固定算法模式

当 `ENABLE_AI_HISTORY_DECISION=False`：

1. 最近 1 小时消息全部计入。
2. 1～2 小时消息随机计入 50%～100%。
3. 最终限制到 50～500。

随机比例使上下文长度不会每次机械固定，但也意味着相同活跃度可能得到不同条数。

#### AI 决策模式

当 `ENABLE_AI_HISTORY_DECISION=True`：

1. 统计最近 10 分钟、10～30 分钟、30～60 分钟、1～2 小时、2～4 小时和 4～6 小时的消息量。
2. 把当前消息的 AI 富文本表示附在评估提示中，并在提示词中告知当前时间，用于让前置模型正确判断新话题、指代、引用（特别是引用回复中出现的具体时间）和对前文的依赖程度。
3. 当前消息只作为待评估数据，提示明确禁止执行其中的指令。前置模型只接收富文本字符串，不附带图片或其他视觉输入。
4. 让 `DYNAMIC_HISTORY_MODEL` 只回复一个数字。
5. OpenAI 使用简化的 `messages` 请求；Gemini 使用 `contents/parts`。
6. 从正文中提取第一个数字，再将结果限制到 50～500。
7. 非 200、异常或无数字均返回 80。

前置模型只决定条数：它不读取历史群聊正文，仅接收 6 小时分段计数、当前消息富文本和当前时间。该统计窗口不会限制正式上下文的时间范围；正式历史仍按前置模型返回的最近 50～300 条读取。

<a id="class-onebot-message-parser"></a>

## 8. OneBot 消息解析

<a id="parser-two-modes"></a>

### 8.1 为什么有两种解析模式

同一条 OneBot 消息有两个用途：

1. 数据库存储：文本应稳定、简洁，引用只保存元信息，不能把大量媒体内容写入数据库。
2. 当前 AI 提问：需要更丰富的引用内容、可读昵称和视觉附件来源。

`OneBotMessageParser` 统一遍历消息段，但使用两个渲染分支：

- `for_ai=False` → `_render_storage_segment()`。
- `for_ai=True` → `_render_ai_segment()`。

<a id="method-parser-init"></a>

### 8.2 `OneBotMessageParser.__init__()`

保存当前 `Bot` 和群号，供成员查询、引用查询、图片路径查询及两条渲染分支复用。

<a id="method-parser-segment-type-and-data"></a>

### 8.3 `_segment_type_and_data()`

兼容两种消息段表示：

- 普通字典：读取 `segment["type"]` 和 `segment["data"]`。
- OneBot `MessageSegment` 对象：读取 `.type` 和 `.data`。

如果 `data` 不是字典，统一回退为空字典。空 `seg_type` 被视为畸形段并跳过；未知但非空类型必须保留占位符。

<a id="method-parser-image-placeholder"></a>

### 8.4 `_image_placeholder()`

把 `image.summary` 转为字符串并去除首尾空白；结果为空时返回 `[图片]`。它不枚举或特殊处理任何具体标签。

<a id="method-parser-image-file-id"></a>

### 8.5 `_image_file_id()`

通常优先返回消息段 `file`，为空时回退 `file_id`；当 `file == "marketface"` 且存在 `file_id` 时改用 `file_id`，供 `get_image()` 查询。

<a id="method-parser-render-ai-image"></a>

### 8.6 `_render_ai_image()`

取得标签和文件标识，按当前附件数量生成内部位置标记，把标签、文件标识和消息段 `path` 登记为 `VisualAttachment`，最后返回该标记。此处不读取图片文件。

<a id="method-parser-format-member-at"></a>

### 8.7 `_format_member_at()`

把 QQ ID 转成可读 `@`：

- `all` → `[@全体成员]`。
- 非数字 → 原样 `[@值]`。
- 数字 → 调用 `get_group_member_info()`。
- 有不同的群名片和 QQ 昵称 → `[@群名片（QQ昵称：昵称）]`。
- 查询失败 → `[@QQ号]`。

<a id="message-segment-matrix"></a>

### 8.8 消息段行为对照

| 段类型 | 数据库存储 | 当前 AI 消息 | AI 引用内容 |
|---|---|---|---|
| `text` | 原文本 | 原文本 | 原文本 |
| `at` 机器人自己 | `[@机器人ID]` | 删除，避免把触发动作当问题 | 按普通成员格式化 |
| `at` 其他成员 | `[@QQ号]` | 尽量解析昵称 | 尽量解析昵称 |
| `at all` | `[@全体成员]` | `[@全体成员]` | `[@全体成员]` |
| `image` 有 `summary` | 去除首尾空白后保存摘要 | 以同一摘要为标签登记视觉附件，随后按可用路径和文件头处理 | 同当前 AI 消息 |
| `image` 无 `summary` | `[图片]` | 以 `[图片]` 为标签登记视觉附件，随后按可用路径和文件头处理 | 同当前 AI 消息 |
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

<a id="method-parser-render-storage-segment"></a>

### 8.9 `_render_storage_segment()`

将单个段转换为适合长期保存的稳定文本。

引用段会调用一次 `get_msg()`，但只提取原消息的 Unix 时间戳和发言人 ID，输出：

```text
[引用回复(时间：时间戳，发言人：用户ID)]
```

此格式随后可由 `_format_history_text()` 转换成可读时间和昵称。

<a id="method-parser-render-quoted-ai-segment"></a>

### 8.10 `_render_quoted_ai_segment()`

渲染“被引用消息内部”的单个段：

- 图片始终读取 `summary` 作为标签；没有 `summary` 时使用 `[图片]`。
- 每个 `image` 段都与当前消息共用同一个 `visual_attachments` 列表；文件标识和
  消息段 `path` 都不可用时仍在原位置保留标签并追加未发送原因。
- 支持格式的引用视觉附件会实际发送；格式不支持、无法识别或无法取得文件时，
  会在原标签上追加对应的未发送原因。
- 文件、语音、视频等保留明确占位。
- 若引用内容里再次出现 `reply`，只输出 `[引用回复（未继续展开）]`。
- 不调用 `_render_ai_reply()`，因此不会继续套娃或无限请求。
- 最后的显式 `else` 保证未来新增的 OneBot 类型不会静默丢失。

<a id="method-parser-render-ai-reply"></a>

### 8.11 `_render_ai_reply()`

只展开外层引用一次：

1. 用外层引用 ID 调用一次 `bot.get_msg()`。
2. 格式化原消息时间。
3. 组合原发言人的群名片和 QQ 昵称。
4. 逐段调用 `_render_quoted_ai_segment()`。
5. 将引用图片加入当前视觉图片列表。
6. 生成包含时间、发言人和完整内容的富文本。

获取失败时返回 `[引用回复(获取信息失败)]`，而不是中断整个问题。

<a id="method-parser-render-ai-segment"></a>

### 8.12 `_render_ai_segment()`

渲染当前用户消息的单个顶层段。主要特殊点：

- 删除用户对机器人的 `at`。
- 对其他成员的 `at` 尽量补全昵称。
- 外层 `reply` 进入 `_render_ai_reply()`。
- 所有图片段都以 `summary`（缺省为 `[图片]`）作为标签并加入待处理列表。
- 优先通过文件标识调用 `get_image()`，无法取得最终路径时回退到消息段 `path`。
- 读取真实文件头；JPEG、PNG、WebP 可以发送，识别出的其他格式在原标签上追加
  “暂不支持该格式”的说明性文字。
- 文件头无法识别或文件无法取得时，同样在原标签上追加对应的未发送原因。
- 未知类型使用 `[seg_type]` 兜底。

<a id="method-parser-parse"></a>

### 8.13 `parse()`

解析器总入口：

- 输入是普通字符串且用于数据库：把所有 CQ 码替换为 `[媒体/表情]`。
- 输入是普通字符串且用于 AI：返回空文本；正常实时事件应传 OneBot 消息对象。
- 输入可迭代：逐段解析并拼接。
- 最后对整体文本 `strip()`。
- 返回 `ParsedMessage(text, visual_attachments)`。

<a id="fn-parse-message-content"></a>

### 8.14 `parse_message_content()`

数据库存储兼容入口，创建 `OneBotMessageParser` 并使用默认的 `for_ai=False` 解析，只返回 `ParsedMessage.text`。数据库流程无需直接操作结构化解析结果。

<a id="send-and-database-write"></a>

## 9. QQ 发送与数据库写入

<a id="fn-send-loop"></a>

### 9.1 `_send_loop()`

职责是“无限重发一条消息直到成功”：

1. 循环调用 `matcher.send(msg)`，任意一次成功立即返回 send 结果（含 `message_id` 的字典）。
2. 失败写 `warning`，日志含发送对象（`label`）与当前尝试编号（第 N 次）。
3. 失败后不等待、立即重试，不限制重试次数。
4. 本函数不自行结束循环；由外层 `asyncio.wait_for` 以 `SEND_RETRY_TIMEOUT`（默认 10 秒）超时取消，超时视为发送失败（典型场景：敏感词被 NapCat 拦截，单次发送需约 10 秒才返回失败）。
5. 被超时取消后由调用方决定后续（改发备用提示或放弃）。

`send_and_save()` 对正式回复、`Waiting……` 快速回复和错误提示统一使用本函数，不区分 `is_finish`。

<a id="fn-send-and-save"></a>

### 9.2 `send_and_save()`

职责是“发送 QQ 消息 + 成功后保存机器人消息 + 可选结束 Handler”：

1. 调用 `_send_loop()`（见 9.1）并用 `asyncio.wait_for(..., timeout=SEND_RETRY_TIMEOUT)` 统一收口发送所有消息，不区分 `is_finish`；快速失败立即重发，超过超时判定为失败。
2. 发送超时/彻底失败且调用方提供了 `fallback_msg`（仅正式 AI 回复）时，改发备用提示（同样受 `SEND_RETRY_TIMEOUT` 超时收口）；备用提示也失败才最终放弃，不存库、无更多输出。
3. 只有返回字典且带 `message_id` 时才尝试存库，存库内容跟随实际送达的消息（原回复或备用提示）。
4. 机器人身份优先从登录信息和群成员信息获取。
5. 身份查询失败时回退 `AI助手` 和 `bot.self_id`。
6. 使用数据库消息解析器规范化机器人发出的 `MessageSegment`。
7. 调用 `insert_message_to_db()`（见 9.3）。
8. `is_finish=True` 时调用 `matcher.finish()`。

即使所有发送尝试都失败，最后仍会结束当前 Handler；不会让同一事件无限挂起。

<a id="fn-insert-message-to-db"></a>

### 9.3 `insert_message_to_db()`

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

<a id="database-write-entries"></a>

### 9.4 数据写入入口

| 场景 | 入口 | 规则 |
|---|---|---|
| Bot 连接后的历史同步 | `sync_history_on_startup()` | 每条历史分别解析和插入 |
| 普通白名单群消息 | `record_chat_history()` | `is_message_to_bot()` 为真时跳过，防止与 AI Handler 竞争 |
| 触发 AI 的用户消息 | `handle_ai_chat()` | 在任何模型调用前先强制保存 |
| `Waiting……` 快速回复 | `send_and_save()` | 发送成功且获得 `message_id` 才保存 |
| 正式回复或错误提示 | `send_and_save()` | 同上 |
| 正式回复被拦截 | `send_and_save()` | 原回复彻底发送失败后改发备用提示（保留 `@` 与消息头，正文替换为“（回复被拦截，发送失败）”） |

<a id="database-audit"></a>

### 9.5 数据库未改动的核查结论

当前数据库相关代码实际使用 11 个 SQL 模板：

- 1 条 WAL。
- 3 条建表语句。
- 1 条建时间戳索引语句。
- 1 条近期时间戳查询。
- 3 条消息/用户写入语句。
- 2 条历史与昵称查询。

本次视觉附件修改没有改动这些 SQL。事务顺序、白名单判断、去重规则、查询排序、
当前消息排除和保存入口也没有变化。

后续若修改数据库相关代码，应至少复核本文数据库相关章节列出的不变量。

<a id="visual-processing"></a>

## 10. 视觉附件与 AI 富文本

<a id="image-summary-label"></a>

### 10.1 `summary` 是通用标签

代码不枚举 `summary` 的具体内容，也不把某一种标签当作特殊类型：

- `image.summary` 转为字符串并去除首尾空白后非空：作为该视觉附件的文字标签。
- 转换后的 `image.summary` 为空：使用 `[图片]`。
- 能取得 `get_image()` 路径或消息段 `path`：无论标签内容是什么，都继续判断
  本地文件头。
- 两种路径都无法取得：在原标签上追加
  `（未发送：无法获取视觉内容）`。

因此 `[动画表情]` 只是可能出现的一种 `summary`，其他标签完全沿用相同流程。
数据库仍保存原 `summary` 或 `[图片]`；当前消息和引用消息则在保留该标签的同时，
尽可能把支持格式的视觉内容交给模型。

<a id="visual-send-spec"></a>

### 10.2 视觉附件发送规范

只把以下格式的图片内容发送给模型：

- JPEG/JPG：`image/jpeg`。
- PNG：`image/png`。
- WebP：`image/webp`。

`.jpg` 只是 `.jpeg` 扩展名的常见缩写，两者表示同一种 JPEG 编码，文件头和 MIME
都相同。当前代码不读取扩展名，所以识别结果统一称为 `JPEG`。

格式完全以实际文件内容为准，不读取或信任文件名后缀。程序等待文件落地后读取
最多 64 字节文件头，识别 JPEG、PNG、WebP、GIF、BMP、TIFF、ICO、AVIF、HEIC、
HEIF 和 SVG。JPEG、PNG、WebP 映射到对应 MIME 并继续读取完整文件；识别出的其他
格式不发送视觉内容，而是在原 `summary` 标签上追加说明：

```text
[图片（未发送：暂不支持 GIF 格式）]
[动画表情（未发送：暂不支持 GIF 格式）]
[其他 summary（未发送：暂不支持 BMP 格式）]
```

文件头无法匹配任何已知格式时使用：

```text
[图片（未发送：无法识别图片格式）]
[动画表情（未发送：无法识别图片格式）]
```

若原标签以 `[` 开头并以 `]` 结尾，说明会插入右方括号之前；其他标签则直接在
末尾追加，例如 `表情（未发送：无法识别图片格式）`。

模型不会看到 NapCat 哈希缓存名、本地路径或 URL。支持格式保留原标签并附带视觉
数据；不支持格式使用追加了说明的标签，且不附带视觉数据。

无法取得路径、文件未落地或读取失败时，在原标签上追加
`（未发送：无法获取视觉内容）`，不附带视觉数据，也不计入回复头的图片数量。

文件名可以没有后缀，也可以与真实格式不一致，不会影响识别结果。只有成功识别
为 JPEG、PNG 或 WebP 的内容才进入 OpenAI/Gemini 视觉协议。

<a id="fn-append-visual-notice"></a>

### 10.3 `_append_visual_notice()`

把 `（未发送：原因）` 追加到原视觉标签。标签以 `[` 开头、`]` 结尾时把说明插入右方括号之前；其他标签直接在末尾追加。

<a id="fn-detect-image-format"></a>

### 10.4 `_detect_image_format()`

只依据最多 64 字节文件头识别 JPEG、PNG、WebP、GIF、BMP、TIFF、ICO、AVIF、HEIC、HEIF 和 SVG，返回格式名及受支持格式的 MIME。只有 JPEG、PNG、WebP 返回 MIME；未知内容返回 `(None, None)`。

<a id="fn-resolve-visual-attachment"></a>

### 10.5 `resolve_visual_attachment()`

处理流程：

1. 解析器通常优先以 `file`、其次以 `file_id` 作为文件标识；当
   `file == "marketface"` 且存在 `file_id` 时改用 `file_id`。消息段 `path` 另存
   为本地路径后备，不传给 `get_image()`。
2. 有文件标识时调用 `bot.get_image(file=...)`，读取返回对象的 `file` 路径。
3. `get_image()` 失败、返回值不是对象或没有有效 `file` 时，回退消息段 `path`。
4. 两种来源都没有路径时，在原标签上追加“无法获取视觉内容”并记录警告。
5. `IMAGE_BASE_DIR` 为空：直接使用所得路径。
6. `IMAGE_BASE_DIR` 非空：只取所得路径的文件名，与映射目录拼接。
7. 默认最多检查 5 次文件是否已落地且为非空普通文件，每次失败等待 1 秒。
8. 在线程池读取最多 64 字节文件头，以真实内容识别格式。
9. 不支持或无法识别时在原标签上追加对应说明，不读取完整文件。
10. 支持时在线程池读取完整文件并转换为 Base64。
11. 返回 `ResolvedVisual`。

<a id="local-read-header"></a>

#### `read_header()`

`resolve_visual_attachment()` 的局部函数，在线程池中打开文件并读取最多 64 字节，只用于真实格式识别。

<a id="local-read-file"></a>

#### `read_file()`

`resolve_visual_attachment()` 的局部函数，仅在确认 MIME 受支持后在线程池中读取完整文件并转换为 Base64。

<a id="fn-extract-ai-content"></a>

### 10.6 `extract_ai_content()`

AI 富文本兼容入口，创建 `OneBotMessageParser` 并使用 `for_ai=True` 解析，返回 `(text, visual_attachments)` 元组。AI Handler 无需直接操作 `ParsedMessage`。

<a id="third-party-search"></a>

## 11. 第三方博查搜索

<a id="third-search-freshness-values"></a>

### 11.1 `THIRD_SEARCH_FRESHNESS_VALUES` 与 `_is_valid_search_freshness()`

<a id="fn-is-valid-search-freshness"></a>

`THIRD_SEARCH_FRESHNESS_VALUES` 列出 `noLimit`、`oneDay`、`oneWeek`、`oneMonth`、`oneYear` 五个预设值。`_is_valid_search_freshness()` 只接受这些预设、真实存在的 `YYYY-MM-DD` 日期，或起始日期不晚于结束日期的 `YYYY-MM-DD..YYYY-MM-DD` 范围；其他类型、无效日期和倒序范围返回 `False`。

<a id="third-search-tool"></a>

### 11.2 `THIRD_SEARCH_TOOL`

提供给 OpenAI 兼容模型的函数工具：

- 工具名：`web_search`。
- 必填参数：`query`。
- 可选参数：`freshness`、`include`、`exclude`、`count`、`summary`。
- `freshness` 可选 `noLimit`、`oneDay`、`oneWeek`、`oneMonth`、`oneYear`，
  也可使用 `YYYY-MM-DD` 指定一天，或使用
  `YYYY-MM-DD..YYYY-MM-DD` 指定日期范围；不限制时间时省略，程序按
  `noLimit` 请求。
- `include`、`exclude` 只在需要限定或排除网站时填写，无需限制时省略。
- `count`：本轮希望返回的搜索结果条数（1–50 的整数），按问题复杂程度由
  AI 自行决定；不填默认 25，后端始终限制在 1–50，非法类型回退默认 25。
- `summary`：是否要求博查为每条结果生成正文概要；不填默认 `true`。

<a id="fn-bocha-search"></a>

### 11.3 `bocha_search()`

请求包含：

- `query`。
- 模型选择的 `freshness`；未提供时为 `"noLimit"`。
- `summary`：AI 通过工具参数指定，未指定时默认 `True`。
- `count`：AI 通过工具参数指定，限制在 1–50；未指定时默认 25，非法类型回退默认 25。

`include`、`exclude` 仅在非空时传入。

返回三元组：

```text
(格式化搜索文本, 结果数量, 是否成功)
```

状态区别：

- 没有结果：`("", 0, True)`，请求成功但结果为空。
- 缺 Key、缺查询词、非 200、API 失败码、超时或异常：成功标记为 `False`。

HTTP 失败会记录状态码和响应正文；API 失败会记录 `code`、`msg/message` 与
`log_id`；成功码同时兼容整数 `200` 和字符串 `"200"`。

每条结果可包含标题、链接、来源、时间、摘要和与摘要不同的全文概要。响应中的结果会逐条校验；格式异常或没有任何有效正文信息的条目会被跳过，不会导致同批其他有效结果丢失。标题或链接缺失时不会输出对应的空字段，返回数量只统计可用条目。当前只处理网页结果，只有图片结果时按没有可用结果处理。

<a id="local-clean-text"></a>

#### `clean_text()`

`bocha_search()` 的局部函数。字段值是字符串时去除首尾空白，否则返回空字符串，用于逐条规范化标题、链接、摘要、来源和发布时间，不让单个可选字段的类型异常破坏整批结果。

<a id="fn-execute-web-search"></a>

### 11.4 `_execute_web_search()`

批量执行一轮 `tool_calls`：

1. 校验 `tool_calls` 数组及每项的 `id/type/function/name/arguments` 结构。
2. 结构完整的调用以规范化 assistant 工具调用写入 `messages`。
3. 未知工具名通过对应的 `role=tool` 结果反馈给模型。
4. 解析 `function.arguments` JSON。
5. 参数不是对象、类型错误、查询词为空或 `freshness` 不在允许范围内时，
   向 `messages` 追加明确工具错误。
6. 调用 `bocha_search()`并累计结果数量。
7. 将搜索文本、`搜索无结果` 或 `搜索失败` 作为 `role=tool` 结果追加。
8. 无法组成合法工具消息的结构错误直接抛出，由主 Handler 的统一异常边界向用户提示。
   存在任何结构错误时，本轮不再执行任何合法搜索调用，直接抛出；不反馈给模型修正。
   这类错误由 API 服务端生成结构保证，正常厂商下几乎不可能出现；模型也看不到原始错误对象、无法据此修正。

只返回本批取得的结果数。单次搜索是否成功仅用于向模型反馈 `搜索无结果` 或 `搜索失败`，不再向工作流累计失败状态。

参数格式错误（`arguments` JSON、参数类型、`freshness` 等）会反馈给模型修正，并占用当前搜索轮次，但不直接算作博查 API 失败。若后续仍有轮次且模型再次请求工具，则继续进入下一轮。

<a id="fn-run-openai-third-search-workflow"></a>

### 11.5 `_run_openai_third_search_workflow()`

处理完整工具循环：

1. 从首轮回包读取 `tool_calls`。
2. 首轮没有调用工具：直接返回正文，标记“已提供搜索能力但未搜索”。
3. 有工具调用：执行第一批搜索，追加合法的 assistant/tool 消息；参数错误以
   `role=tool` 错误反馈让模型修正；外层结构错误直接抛出。
4. 继续调用模型，让模型阅读结果、回答或修正搜索词。
5. 非最后一轮继续提供搜索工具。
6. 最后一轮移除工具，并向系统提示词追加“搜索轮次已用完，必须直接回答”。
7. 累计所有批次的结果数量；总数大于 0 时显示实际总数，即使中间有轮次失败。
8. 只有所有批次最终都没有取得结果时，才以 `performed=True, count=0` 显示 `搜索：False`，包括空结果、网络错误、请求失败和工具参数格式错误。

在默认 `MAX_SEARCH_ROUNDS=3` 时，搜索批次最多为三批：首轮一批，加上后续最多两批；最后一次模型请求被强制生成文本。

`MAX_SEARCH_ROUNDS <= 0` 时，模型首轮请求不会注册第三方搜索工具，也不会进入该工作流。

后续模型请求非 200 会向上抛出，由主 Handler 的通用异常边界处理。

<a id="history-and-prefix"></a>

## 12. 历史读取、格式化与回复头

<a id="fn-load-history-rows"></a>

### 12.1 `_load_history_rows()`

读取当前群的历史：

1. 连接群消息表。
2. 左连接全局昵称和本群名片。
3. 排除当前触发消息的 `message_id`。
4. 按 `timestamp DESC, rowid DESC` 获取最近 `dynamic_limit` 条。
5. 查询结束后 `reverse()`，恢复成从旧到新的提示词顺序。

排除当前触发消息很重要：该消息已经提前存库，但会在提示词最后作为“当前问题”单独加入，不能在历史中重复出现。

每张消息表都为 `timestamp` 建立了索引 `idx_{table}_timestamp`（由 `init_db()`
启动时创建）。该索引能大幅减少按时间排序和筛选时的全表扫描，使百万条级别下
`LIMIT` 的耗时也与总记录数基本解耦。
代码仍不承诺固定耗时：极端情况下耗时仍可能受磁盘、SQLite 缓存及机器性能影响。

<a id="fn-load-user-display-map"></a>

### 12.2 `_load_user_display_map()`

读取当前群用户的显示名映射：

- 群名片与 QQ 昵称不同：`群名片（QQ昵称：QQ昵称）`。
- 两者相同：只显示一个名称。
- 缺少群名片时依次回退 QQ 昵称、用户 ID。

该映射用于把历史消息中的数字 `@` 和引用发言人 ID 转换成可读名称。

<a id="fn-format-history-text"></a>

### 12.3 `_format_history_text()`

<a id="local-convert-reply-time"></a>
<a id="local-convert-at"></a>

内部包含两个局部转换函数：

- `convert_reply_time()`：把数据库中的 Unix 时间戳转换为 `月-日 时:分:秒`，并把引用发言人 ID 转成显示名。
- `convert_at()`：把 `[@数字QQ号]` 转成 `[@可读显示名]`。

每条历史最终格式：

```text
[月-日 时:分] 群名片（QQ昵称：昵称）: 消息内容
```

历史发言人、当前提问者和当前引用的原发言人使用相同的显示规则：群名片与
QQ 昵称不同时显示 `群名片（QQ昵称：昵称）`，相同时只显示一个名称。

历史顺序是从旧到新。

<a id="fn-format-reply-prefix"></a>

### 12.4 `_format_reply_prefix()`

基础格式：

```text
模型：模型名，记录：历史条数
```

按需追加：

- 至少一张图片成功发送给模型 → `，图片：n`。
- `SearchResult.prefix_value()` 有值 → `，搜索：n/False`。

最后追加换行，模型正文紧随其后。

<a id="class-chat-service"></a>

## 13. `ChatService` 业务编排

<a id="method-chat-service-select-model"></a>

### 13.1 `select_model()`

只根据 `event.get_plaintext().strip()` 判断模型和模式，不处理图片或引用。返回 `ModelSelection`。

<a id="method-chat-service-resolve-visuals"></a>

### 13.2 `resolve_visuals()`

`resolve_visuals()` 使用 `asyncio.gather()` 并行解析各附件，避免多个待落地文件的
等待时间逐项累加；`gather()` 的返回结果仍保持原消息顺序。

<a id="method-chat-service-apply-visual-placeholders"></a>

### 13.3 `apply_visual_placeholders()`

解析器先在富文本原位置写入内部标记，并把同一个图片段的 `file_id`、`path` 与
`summary` 标签放进 `VisualAttachment`。`apply_visual_placeholders()` 在格式判断
完成后，将每个内部标记替换为对应的原标签或未发送说明。这样不需要根据
`[图片]`、`[动画表情]`、`[臭]` 或其他标签做字符串猜测，也不会写死任何
`summary`。

之后只把 `sendable=True` 的视觉附件交给协议层；其他附件的失败原因已经直接显示
在原位置，不再额外生成重复清单。只要存在实际发送的附件，系统提示就会说明：
附件按当前提问（包括一层引用）中的出现顺序排列，并且不来自历史记录。

用户手写 `[图片]` 等文字不会触发视觉流程。只有解析器实际遇到结构化 `image`
消息段时才会生成内部标记并登记附件。

最终回复头的图片数是成功读取并实际发送的数量，而不是原消息中的图片段数量。

<a id="method-chat-service-build-prompts"></a>

### 13.4 `build_prompts()`

完整步骤：

1. 生成当前本地时间。
2. 组合当前提问者的群名片和 QQ 昵称。
3. 将当前消息富文本传给动态历史决策，由固定算法或前置模型决定历史条数。
4. 查询历史记录并生成昵称映射。
5. 格式化历史文本。
6. 查询机器人在当前群的名片，失败时使用启动时取得的 QQ 昵称。
7. 用机器人身份填充模式提示词。
8. 将历史、当前时间、提问者名称和当前问题组成用户提示词。
9. 若模型支持视觉且至少一个附件成功读取，在系统提示词中说明附件顺序和来源。

有历史时的结构：

```text
--- 真实群聊历史记录 ---
历史内容
------------------------

现在是 当前时间，用户 显示名 正在向你提问：
当前问题
```

无历史时省略历史区块。

<a id="visual-request-example"></a>

### 13.5 发给 AI 的完整视觉示例

假设数据库历史中已有一条图片占位；用户当前发送一个 `summary=[臭]`、文件内容
为 WebP 的附件，引用了一条 `summary=[动画表情]`、文件内容为 GIF 的附件，随后
又带上一个 `summary=[姑姑嘎嘎]`、但没有任何可用路径的附件，以及一张没有
`summary`、文件内容为 PNG 的图片。程序发给 AI 的系统提示词会在所选模式提示词
后追加：

```text
[系统重要提示：用户本次提问附带了视觉内容。视觉附件按它们在当前提问（包括一层引用）中的出现顺序排列；请结合实际视觉内容回答。这些附件只属于当前提问，不来自历史聊天记录。]
```

用户提示词的完整结构为：

```text
--- 真实群聊历史记录 ---
[07-31 14:00] 李四（QQ昵称：小李）: 之前那张[图片]挺有意思
[07-31 14:01] 王五: 我看到的是一张静态图
[07-31 14:03] 赵六（QQ昵称：小六）: 也发给机器人看看吧
------------------------

现在是 2026-07-31 14:05:00，用户 群名片（QQ昵称：小明） 正在向你提问：
帮我看看这个[臭]，再比较引用里的内容
[引用回复（时间：07-31 14:04:20，发言人：李四（QQ昵称：小李），内容：[动画表情（未发送：暂不支持 GIF 格式）]）]
另外这个[姑姑嘎嘎（未发送：无法获取视觉内容）]是什么
最后再看看这张[图片]
```

历史中的 `[图片]` 只是数据库文字，不对应视觉内容；用户手写同样文字也不会触发
视觉流程。`[臭]` 虽然不是固定标签，但它来自当前 `image.summary`，且文件头识别
为 WebP，所以对应第一个实际附件。引用 GIF 直接在原位置说明格式不支持；
`[姑姑嘎嘎]` 因为无法取得任何路径，直接在原位置说明无法获取视觉内容。最后一张
图片没有 `summary`，因此使用 `[图片]`，文件头识别为 PNG 后对应第二个实际附件。

OpenAI 兼容请求的用户内容为：

```text
[
  {"type": "text", "text": "<上面的完整用户提示词>"},
  {"type": "image_url", "image_url": {
    "url": "data:image/webp;base64,<图片数据省略>"
  }},
  {"type": "image_url", "image_url": {
    "url": "data:image/png;base64,<图片数据省略>"
  }}
]
```

Gemini 请求表达相同信息，但协议字段不同：

```text
[
  {"text": "<上面的完整用户提示词>"},
  {"inlineData": {
    "mimeType": "image/webp",
    "data": "<图片数据省略>"
  }},
  {"inlineData": {
    "mimeType": "image/png",
    "data": "<图片数据省略>"
  }}
]
```

Base64 数据属于协议附件，不会直接拼进模型看到的文本。若 `[臭]` 的文件头实际
识别为 GIF，则其原位置会变为 `[臭（未发送：暂不支持 GIF 格式）]`，并且请求中
不会有对应的 `image_url` 或 `inlineData`。若文件头无法识别，则原位置会变为
`[臭（未发送：无法识别图片格式）]`。

<a id="method-chat-service-build-model-request"></a>

### 13.6 `build_model_request()`

#### OpenAI 兼容格式

- 认证：`Authorization: Bearer ...`。
- 基础字段：`model`、`messages`、`stream=False`。
- 无视觉图片时，用户 `content` 是字符串。
- 有视觉图片时，用户 `content` 是多段列表：
  - 第一段是文本。
  - 后续是使用实际 MIME 类型的 `image_url` Data URL。
- 原生搜索开启时调用 `_enable_openai_native_search()`。
- 无原生搜索但满足第三方条件时注册 `THIRD_SEARCH_TOOL`。

#### Gemini 格式

- 认证：`x-goog-api-key`。
- 系统提示词放在 `systemInstruction.parts`。
- 用户文本和图片放在 `contents[0].parts`。
- 图片使用 `inlineData`，`mimeType` 与实际 JPEG、PNG 或 WebP 格式一致。
- 原生搜索使用 `googleSearch` 工具。

未知 `api_type` 抛出 `UnsupportedAPITypeError`。

<a id="method-chat-service-parse-gemini-reply"></a>

### 13.7 `parse_gemini_reply()`

同时提取正文和 grounding 搜索计数；原生搜索证据的解析规则如下。

请求通过：

```json
{"tools": [{"googleSearch": {}}]}
```

`ChatService.parse_gemini_reply()` 解析：

1. 优先使用 `groundingMetadata.webSearchQueries` 数量。
2. 没有查询列表时，统计 `groundingChunks` 中包含 `web` 的条目。
3. 无 grounding 证据时不显示搜索数字。

<a id="method-chat-service-send-model-request"></a>

### 13.8 `send_model_request()`

1. 使用共享会话发送首轮请求。
2. 首轮非 200 抛出 `ModelHTTPError`，保留上游响应文本。
3. OpenAI + 第三方搜索：进入独立搜索工作流。
4. OpenAI + 原生搜索：提取正文后统一解析搜索信息。
5. 普通 OpenAI：只返回正文。
6. Gemini：调用 `parse_gemini_reply()`。

<a id="method-chat-service-complete"></a>

### 13.9 `complete()`

一次正式对话的总编排：

1. 按文件头识别图片真实格式，并对支持格式进行 Base64 编码。
2. 把格式判断结果写入最终用户提示词，只保留可发送图片。
3. 构建系统提示词、用户提示词和历史行。
4. 构建对应协议请求。
5. 获取共享 HTTP 会话。
6. 发送请求并解析。
7. 空正文替换为 `（模型API拒绝回复）`。
8. 返回正文、实际历史数和实际图片数。

`complete()` 不直接发送 QQ 消息，也不直接操作数据库，使业务层可单独测试。

<a id="nonebot-events"></a>

## 14. 服务实例与 NoneBot 事件入口

<a id="chat-service-instance"></a>

### 14.1 `chat_service`

模块加载时创建一个 `ChatService` 实例，供 `handle_ai_chat()` 复用；服务对象本身不保存单次对话状态。

<a id="fn-is-message-to-bot"></a>

### 14.2 `is_message_to_bot()`

事件入口层共享的触发判定规则，`record_chat_history()` 与 `chat_handler` 共用：

- 仅在群聊事件上生效；私聊/非消息事件返回 `False`。
- 第一优先沿用服务端 `event.to_me` 字段（正常 `@` 机器人的消息都可靠），命中即返回 `True`。
- `event.to_me` 不可靠时回退到直接遍历原消息段：存在 `at` 段且 `qq == bot.self_id` 即返回 `True`（与图片等段的前后顺序无关）。
- 消息纯文本中提及机器人昵称 `_bot_nickname` 时返回 `True`。
- 遍历/解析异常时返回 `False`，不影响其它规则。

同时保留了 `to_me()` / `event.to_me` 的可靠路径，又在其不可靠的场景（如图片在前、
`@` 在后）回退到直接检查消息段，保证 `@机器人` 一定能触发。

<a id="fn-sync-history-on-startup"></a>

### 14.3 `sync_history_on_startup()`

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

<a id="record-handler"></a>

### 14.4 `record_handler` 与 `record_chat_history()`

<a id="fn-record-chat-history"></a>

`priority=1, block=False`，用于被动记录：

- 非群消息跳过。
- 非白名单群跳过。
- `is_message_to_bot()` 为真时跳过，避免和 AI Handler 重复处理触发消息。
- 保存昵称、群名片、用户 ID 和规范化消息。

<a id="chat-handler"></a>

### 14.5 `chat_handler` 与 `handle_ai_chat()`

<a id="fn-handle-ai-chat"></a>

`rule=is_message_to_bot(), priority=50, block=True`，主处理流程：

1. 私聊等非群消息：回复“仅限群聊”并结束。
2. 非白名单群：直接返回。
3. 在调用 AI 前先保存用户触发消息。
4. 再检查原消息中是否真的存在对机器人的 `at`。
   - 只引用机器人、但没有手动 `@` 时，保存消息后结束，不触发 AI。
5. 从纯文本选择模型和模式。
6. 提取 AI 富文本和视觉附件来源。
7. 从富文本删除一次模型前缀。
8. 空文本且无图片：回复“何意味”。
9. 有图片但模型不支持视觉：回复能力错误。
10. 若开启快速提示：发送并保存 `Waiting……`。
11. 调用 `chat_service.complete()`。
12. 构建固定回复头。
13. `@` 提问者，发送正式回复并保存。

第 9 步发生在 `get_image()`、读取文件头、Base64 编码、历史查询和模型请求之前。
只要解析器遇到真实的结构化 `image` 段，就会对不支持视觉的模型直接拦截；用户
手写 `[图片]` 等普通文本不会触发该判断。

异常分流：

- `UnsupportedAPITypeError` → API 格式配置错误。
- `ModelHTTPError` → 首轮 HTTP 请求失败，并显示上游错误文本。
- `asyncio.TimeoutError` → 请求超时。
- `FinishedException` → 必须重新抛出，不能被通用异常吞掉。
- 其他异常 → 记录完整堆栈并回复“调用出错”。

## 15. 日志与异常策略

### 15.1 `logger.exception` 的意义

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

### 15.2 为什么不应全部使用 `logger.exception`

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

### 15.3 当前 `logger.exception` 边界

| 位置 | 为什么需要 traceback | 失败后的行为 |
|---|---|---|
| 动态历史数据库查询 | 可能是路径、锁、表或 SQL 环境问题 | 使用默认条数 |
| 动态历史模型调用 | 可能是网络、JSON、协议或会话问题 | 使用默认条数 |
| 机器人消息存库 | 发送已成功，但身份解析或存库失败 | 不影响 Handler 结束 |
| 数据库写入 | 需要定位连接、表、锁或类型问题 | 当前消息不入库 |
| 图片读取/Base64 | 涉及 OneBot、路径、权限和线程池 | 不发送图片数据，在原标签上说明无法获取 |
| 博查搜索通用异常 | 涉及网络、JSON 和数据结构 | 标记搜索失败 |
| 历史查询 | 需要区分表、连接或数据问题 | 使用空历史 |
| 昵称映射查询 | 需要定位数据库问题 | 使用原 ID/默认昵称 |
| 单条/单群启动同步 | 单条数据结构和群接口故障都可能发生 | 隔离失败继续同步 |
| 最终模型通用异常 | 是所有未分类故障的最后边界 | 给用户发送错误提示 |

当前这些位置都位于有效的 `except` 块中，使用方式正确。日志字符串中已经包含 `{e}`，而 `logger.exception` 还会在 traceback 末尾显示一次异常；这略有重复但便于单行检索，不影响功能。

## 16. 关键容错与边界行为

1. 配置检查只报告，不阻止启动。
2. 数据库读失败时使用空历史或默认历史条数。
3. 单条历史同步失败不影响其他记录。
4. 图片读取失败只丢弃对应二进制数据，并在原 `summary` 标签（缺省为 `[图片]`）
   上追加“无法获取视觉内容”，不中断文字问题。
5. 引用获取失败保留占位文本。
6. 引用只展开一层，避免套娃请求。
7. 未知非空消息段保留 `[seg_type]`。
8. 模型空正文转换为明确占位。
9. 所有 QQ 消息发送由 `_send_loop()` 无限重发、失败立即重试，外层按 `SEND_RETRY_TIMEOUT`（默认 10 秒）超时收口；正式回复超时/彻底失败时改发备用提示（同样受超时收口）。
10. `FinishedException` 必须重新抛出，维持 NoneBot 的流程控制。
11. 模型原生搜索无可核实统计字段时不显示搜索字段。
12. 第三方搜索最后一轮强制回答，避免模型一直要求继续搜索。

## 17. 扩展代码时应改哪里

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

## 18. 维护和回归检查建议

每次修改后至少执行：

```bash
python -m py_compile easy_ai.py pz.py
git diff --check
```

按改动范围增加检查：

| 改动区域 | 建议验证 |
|---|---|
| 消息解析 | 文本、at、无 summary 图片、任意 summary 图片、文件、语音、视频、引用、未知类型 |
| 引用解析 | `get_msg` 只调用一次；所有 image 段进入视觉列表；嵌套引用不展开 |
| 视觉附件 | 任意 summary、`get_image`、path 回退、无来源、文件头识别、格式拦截、占位回写 |
| 数据库 | 比较 SQL 模板、事务顺序和四类保存入口 |
| 模型选择 | 默认、大写、小写、未知前缀 |
| OpenAI | 纯文本、JPEG/PNG/WebP Data URL、格式拦截、原生搜索、第三方搜索 |
| Gemini | 纯文本、JPEG/PNG/WebP `inlineData`、格式拦截、`googleSearch`、grounding 计数 |
| 第三方搜索 | 无工具调用、单轮、多轮、无结果、失败、参数错误 |
| 发送 | 无限重发 + 10 秒超时收口、拦截备用提示、存库失败、`finish()` |

## 19. 后续接手的推荐阅读顺序

若只想快速定位问题：

1. 先读本文第 2 节的两棵架构树和可跳转导航。
2. 用户消息理解错误：读第 8 节和 `OneBotMessageParser`。
3. 历史不正确：读第 6、7、12、13 节。
4. 图片不工作：读第 10 节。
5. 模型请求不兼容：读第 5、13 节。
6. 博查搜索异常：读第 11 节。
7. QQ 不回复或重复保存：读第 9、14 节。
8. 线上出现堆栈：按第 15 节判断是可恢复失败还是非预期故障。

若准备修改代码：

1. 明确改动属于解析、协议、业务还是 Handler。
2. 尽量在对应层扩展，不跨层复制逻辑。
3. 对照第 6、9、12 节的数据库不变量和第 16 节的关键容错，确认旧功能没有丢失。
4. 特别保护数据库表结构、事务顺序、去重、排序、当前消息排除和四类保存入口。
5. 运行第 18 节的回归检查。
