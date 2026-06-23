# easy_QQBot
部署环境Debian13.3 x64  

基于**nonebot**+**napcat**实现
## 功能说明
一个nonebot插件  
简单接入ai的QQBot  
具备简单的模型可选能力  
具备一定的图片识别能力（需要模型支持）   
具备联网搜索能力（支持模型原生搜索 + 第三方博查API搜索）  
依据时间尺度的对话频率，结合ai动态调整聊天记录浏览范围

---

## 对话示例

### 基础问答
```
用户 @机器人 今天天气怎么样
机器人 @用户 （ds-v4-flash，CAS）Waiting……
机器人 @用户
模型：ds-v4-flash，记录：35
抱歉，我无法获取实时天气信息，建议你查看天气预报APP。
```

### 切换模型（大写=严肃模式，小写=随性模式）
```
用户 @机器人 /A 解释一下什么是量子纠缠
机器人 @用户 （ds-v4-pro，SER）Waiting……
机器人 @用户
模型：ds-v4-pro，记录：42
量子纠缠是量子力学中的一种现象，指两个或多个粒子...
```

### 图片识别（需模型支持 vision）
```
用户 @机器人 [图片：一只猫]
机器人 @用户 （gemini-3-flash，CAS）Waiting……
机器人 @用户
模型：gemini-3-flash，记录：28，图片：1
这是一只橘猫，正趴在沙发上晒太阳，表情看起来很惬意。
```

### 原生搜索（模型自带搜索能力，如 Gemini）
```
用户 @机器人 /b 2026年世界杯最新赛况
机器人 @用户 （gemini-3-flash，CAS）Waiting……
机器人 @用户
模型：gemini-3-flash，记录：30，搜索：3
根据最新信息，2026年世界杯小组赛...
```

### 第三方搜索（模型无搜索能力，启用博查API兜底）
```
用户 @机器人 /a 最近有什么科技新闻
机器人 @用户 （ds-v4-pro，CAS）Waiting……
机器人 @用户
模型：ds-v4-pro，记录：38，搜索：5
最近科技圈主要有以下动态：
1. NVIDIA发布了新一代GPU...
2. 苹果宣布将在秋季推出...
```

### 引用回复 — 针对某条消息追问
```
群聊：
  张三：我昨天去了那家新开的川菜馆
  李四：味道怎么样
  张三：水煮鱼绝了，但辣度有点高
  用户 引用回复 张三的"水煮鱼绝了，但辣度有点高" + @机器人 有多辣，具体形容一下

机器人 @用户 （ds-v4-flash，CAS）Waiting……
机器人 @用户
模型：ds-v4-flash，记录：45
根据聊天记录，张三提到"辣度有点高"，考虑到川菜馆的辣度标准，
可能是比普通麻辣再高一个等级，一般人需要配两碗米饭的程度。
```

### 引用回复 — 追问图片内容
```
群聊：
  王五：[发了一张风景照]
  用户 引用回复 王五的风景照 + @机器人 这是哪里

机器人 @用户 （gemini-3-flash，CAS）Waiting……
机器人 @用户
模型：gemini-3-flash，记录：32，图片：1
从照片中的建筑风格和山峦轮廓来看，这很可能是云南丽江古城附近，
远处可以看到玉龙雪山。照片里的石板路和红灯笼也是丽江的典型特征。
```

---

## 回复格式说明

正式回复前缀格式为：`模型：{模型名}，记录：{上下文条数}[，图片：{n}][，搜索：{n}]`

- **记录**：始终显示，表示本次携带的历史消息条数
- **图片**：仅当用户本次上传了图片才显示（无图片不显示）
- **搜索**：仅当实际发生了搜索才显示（未搜索不显示）

| 场景 | 示例回复前缀 |
|------|-------------|
| 纯文字提问 | `模型：ds-v4-flash，记录：35` |
| 上传图片提问 | `模型：gemini-3-flash，记录：28，图片：1` |
| 触发搜索 | `模型：ds-v4-pro，记录：38，搜索：5` |
| 图片+搜索 | `模型：gemini-3.1-pro，记录：22，图片：2，搜索：3` |

---

## 搜索机制说明

### 搜索方式优先级
1. **模型原生搜索**（`search: true`）→ 模型自带联网能力，直接调用模型内置搜索
2. **第三方搜索**（`search: false` + 全局 `ENABLE_THIRD_SEARCH = True`）→ 注册搜索工具给AI，AI自主决定搜索关键词，由博查API执行搜索，结果返回AI整合
3. **无搜索**（`search: false` + `ENABLE_THIRD_SEARCH = False`）→ 纯文本对话

### 第三方搜索流程
```
用户提问 "查找科技新闻（只看官媒）"
    │
    ├─ 第 1 轮 ─→ AI收到问题 + 搜索工具
    │      AI决定搜索词："2026年6月科技新闻"
    │      AI决定 include："xinhuanet.com|people.com.cn"（限定官媒）
    │      博查返回 8 条结果
    │
    ├─ 第 2 轮 ─→ AI看到搜索结果，发现不够精确
    │      AI修正搜索词："2026年6月 AI芯片 科技新闻"
    │      AI决定 exclude："zhihu.com"（排除问答站点）
    │      博查返回 5 条结果
    │
    └─ 第 3 轮 ─→ AI收到两轮累计 13 条结果
           整合信息，给出最终回复（显示 搜索：13）
```

关键点：
- **AI 自己决定搜什么关键词**，不是机械地用用户原话搜索
- **AI 可以多轮修正搜索词**，第一轮搜偏了还有补救机会（受 `MAX_SEARCH_ROUNDS` 限制）
- **AI 可控制搜索范围**：支持传入 `include`（限定网站）和 `exclude`（排除网站），由 AI 根据场景自主决定
- **`summary` 由程序硬编码开启**，始终返回网页文本摘要，AI 无需关心
- **`count` 由配置项 `THIRD_SEARCH_COUNT` 固定**，AI 不可调整，防止超量
- **搜索数累计**：回复中 "搜索：n" 是所有轮次的搜索结果总数

---

## 搭建说明

### napcat安装及配置
官方文档：https://napneko.github.io/guide/napcat  

运行`curl -o napcat.sh https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh && bash napcat.sh --docker n --cli y`  
参数解释：安装TUI-CLI、不使用docker  
安装完成后直接运行`napcat`进入可视化界面，配置QQ号，启用ws反代，设置地址、端口和token即可（这里需要和下面的nonebot一致）  
比如：`ws://127.0.0.1:8082/onebot/v11/ws`

### nonebot安装及配置
官方文档：https://nonebot.dev/docs/  

**安装说明**  
选一个文件夹作为安装文件夹  
安装虚拟环境:`python3 -m venv venv`  
激活虚拟环境：`source venv/bin/activate`  
安装：`pip install nb-cli nonebot2 nonebot-adapter-onebot`  
创建项目：`nb create`   
创建项目时候是可视化交互，选择**OneBot V11**协议；然后选择**Current project**，也就是当前目录，其他自行研究  

**配置说明**  
去到nonebot安装目录下，找到`.env`文件,在其中添加
```commandline
HOST=127.0.0.1
PORT=8082
ONEBOT_ACCESS_TOKEN="这里填你刚才在NapCat里写的那个13位以上的Token"
COMMAND_START=["/", ""]
```

### 插件安装及配置
额外安装依赖库  

`pip install "nonebot2[fastapi]" aiohttp aiosqlite`

在**easy_ai.py**内完成以下配置，然后将文件放入 nonebot 的 **plugins** 文件夹，执行 `nb run` 即可。

#### 全部配置项

```python
# ===== 基础配置 =====
ALLOWED_GROUPS = [12345678]        # 白名单群号列表
DB_PATH = "/qqbot/chat_history.db" # SQLite 数据库文件路径
ENABLE_QUICK_ACK = True            # 收到提问后立刻回复"Waiting……" (True/False)

ENABLE_AI_HISTORY_DECISION = True  # AI 动态决定历史记录条数 (True/False)
DYNAMIC_HISTORY_MODEL = "default"  # 决定上下文条数所用的模型 (对应 MODELS_CONFIG 键名)
DYNAMIC_HISTORY_TIMEOUT = 30       # 前置AI超时时间（秒）
AI_CHAT_TIMEOUT = 120              # 正式聊天超时时间（秒）

IMAGE_BASE_DIR = ""                # 图片缓存目录。同机部署留空；跨Docker填挂载路径
DEFAULT_MODE = "casual"            # 无前缀默认模式："serious"(严肃) 或 "casual"(随性)

# ===== 第三方搜索（博查AI）=====
THIRD_SEARCH_API_KEY = ""                                    # 博查AI API Key (https://open.bocha.cn)
THIRD_SEARCH_API_URL = "https://api.bocha.cn/v1/web-search"  # 博查AI搜索端点
THIRD_SEARCH_COUNT = 25                                      # 单次搜索返回条数 (1-50)
MAX_SEARCH_ROUNDS = 3                                        # 最大搜索轮数（AI可多次修正搜索词）
THIRD_SEARCH_TIMEOUT = 30                                   # 第三方搜索请求超时（秒）
ENABLE_THIRD_SEARCH = False                                 # 第三方搜索总开关，仅当模型无原生搜索时生效

# ===== 模型配置 =====
MODELS_CONFIG = {
    "default": {                     # 无前缀时的默认模型
        "api_key": "",               # API 密钥
        "api_url": "...",            # API 端点地址
        "name": "ds-v4-flash",       # 显示名称（回复中展示）
        "api_type": "openai",        # API 格式："openai" 或 "gemini"
        "model_id": "deepseek-v4-flash",  # 实际传入 API 的 model 参数
        "vision": False,             # 是否支持图片识别
        "search": False,             # 是否自带联网搜索
    },
    "A": { ... },                    # /A 大写 = 严肃模式；/a 小写 = 随性模式
    "B": { ... },                    # 同上。B/C 通常配置支持 vision+search 的模型
    "C": { ... },
}
```

| 配置项 | 说明 |
|--------|------|
| `ALLOWED_GROUPS` | 允许机器人响应的群号列表 |
| `DB_PATH` | SQLite 数据库路径，用于存储聊天记录和用户信息 |
| `ENABLE_QUICK_ACK` | 是否先回复"Waiting……"提示，缓解等待焦虑 |
| `ENABLE_AI_HISTORY_DECISION` | 开启后由AI根据群聊活跃度动态决定读取多少条历史 |
| `DYNAMIC_HISTORY_MODEL` | 上述决策使用的模型，通常用最便宜的模型 |
| `DYNAMIC_HISTORY_TIMEOUT` | 前置决策AI超时秒数 |
| `AI_CHAT_TIMEOUT` | 正式聊天(首轮请求)超时秒数 |
| `THIRD_SEARCH_TIMEOUT` | 第三方搜索超时秒数|
| `IMAGE_BASE_DIR` | 图片路径：同机留空；跨Docker填容器内挂载的绝对路径 |
| `DEFAULT_MODE` | 无 /A /a 前缀时的默认模式 |
| `THIRD_SEARCH_API_KEY` | 博查AI密钥，留空则不启用第三方搜索 |
| `THIRD_SEARCH_COUNT` | 单次搜索返回给AI的结果条数 |
| `MAX_SEARCH_ROUNDS` | 最大搜索轮数。AI 每轮可自行决定发起一次或多次搜索，轮次用尽后强制文本回复 |
| `MODELS_CONFIG.*.api_type` | `"openai"` 兼容绝大多数国产模型和中转站；`"gemini"` 用于 Google Gemini |
| `MODELS_CONFIG.*.vision` | 开启后图片消息会转为 base64 传给模型 |
| `MODELS_CONFIG.*.search` | 模型自带搜索 → 传原生搜索参数；不勾选 + 开启 `ENABLE_THIRD_SEARCH` → 走博查兜底 |
| `ENABLE_THIRD_SEARCH` | 第三方搜索总开关。仅模型 `search: false` 时生效，开启后注册搜索工具让AI自行决定搜索词。需同时填写 `THIRD_SEARCH_API_KEY` |

等待nonebot和napcat通信成功后，at对应qq即可触发ai回复。

**注意：挂载服务（systemctl）的时候需要留意虚拟环境，建议指定虚拟环境运行，本质还是 `nb run`**
