# EngramNote LLM 服务详解（零基础版）

> **面向对象**：对"如何调用大模型"完全陌生的初学者
> **学习目标**：读完本文后，你能回答下面这些问题——
> 1. 调用 LLM 到底是在做什么？（本质就是发一个 HTTP 请求）
> 2. 这个项目的 LLM 代码在哪些文件里？每一行在干什么？
> 3. 如果我想自己写代码调用 DeepSeek / GLM，该怎么写？
> 4. 调用失败（报错、超时、限流）时怎么排查？

---

## 目录

- [第一章：零基础理解"调用 LLM"这件事](#第一章零基础理解调用llm这件事)
  - [1.1 LLM 是什么](#11-llm-是什么)
  - [1.2 调用 LLM = 发一个 HTTP 请求](#12-调用-llm--发一个-http-请求)
  - [1.3 API Key 是什么](#13-api-key-是什么)
  - [1.4 OpenAI 兼容 API 是什么意思](#14-openai-兼容-api-是什么意思)
  - [1.5 必懂的 5 个专业名词](#15-必懂的-5-个专业名词)
- [第二章：动手写你的第一次 LLM 调用](#第二章动手写你的第一次-llm-调用)
  - [2.1 用 curl 调一次（看本质）](#21-用-curl-调一次看本质)
  - [2.2 用 Python httpx 调一次（本项目的方式）](#22-用-python-httpx-调一次本项目的方式)
  - [2.3 用 openai SDK 调一次（更省事的方式）](#23-用-openai-sdk-调一次更省事的方式)
  - [2.4 读懂响应结果](#24-读懂响应结果)
- [第三章：本项目如何配置 LLM](#第三章本项目如何配置-llm)
  - [3.1 配置都在 config.py](#31-配置都在-configpy)
  - [3.2 debug 模式自动选择提供商](#32-debug-模式自动选择提供商)
  - [3.3 .env 文件怎么填](#33-env-文件怎么填)
- [第四章：LLMService 核心类逐段精讲](#第四章-llmservice-核心类逐段精讲)
  - [4.1 文件总览](#41-文件总览)
  - [4.2 模块级共享客户端（get_llm_client）](#42-模块级共享客户端get_llm_client)
  - [4.3 限流器 RateLimiter（令牌桶）](#43-限流器-ratelimiter令牌桶)
  - [4.4 并发闸门 Semaphore](#44-并发闸门-semaphore)
  - [4.5 初始化：服务怎么知道调哪家](#45-初始化服务怎么知道调哪家)
  - [4.6 chat()：最核心的通用聊天接口](#46-chat最核心的通用聊天接口)
  - [4.7 chat_stream()：流式输出](#47-chat_stream流式输出)
- [第五章：封装好的业务方法（拿来即用）](#第五章封装好的业务方法拿来即用)
  - [5.1 summarize_chapter — 章节摘要](#51-summarize_chapter--章节摘要)
  - [5.2 extract_knowledge_points — 提取知识点](#52-extract_knowledge_points--提取知识点)
  - [5.3 generate_questions — 生成题目](#53-generate_questions--生成题目)
  - [5.4 rag_answer — 智能问答](#54-rag_answer--智能问答)
  - [5.5 其他方法一览](#55-其他方法一览)
- [第六章：多轮对话会话（Session）机制](#第六章多轮对话会话session机制)
  - [6.1 为什么需要会话](#61-为什么需要会话)
  - [6.2 ConversationSession — 普通多轮对话](#62-conversationsession--普通多轮对话)
  - [6.3 UnderstandingSession — 知识提取专用会话](#63-understandingsession--知识提取专用会话)
  - [6.4 CombinedAnalysisSession — 联合分析会话](#64-combinedanalysissession--联合分析会话)
- [第七章：LLM 在项目中的实际调用场景](#第七章-llm-在项目中的实际调用场景)
  - [7.1 场景一：理解管道（章节→摘要→知识点）](#71-场景一理解管道章节摘要知识点)
  - [7.2 场景二：RAG 智能问答（流式）](#72-场景二rag-智能问答流式)
  - [7.3 场景三：ASR 标点恢复（另一种调用方式）](#73-场景三asr-标点恢复另一种调用方式)
- [第八章：常见报错与排查指南](#第八章常见报错与排查指南)
- [第九章：新手调参指南](#第九章新手调参指南)
- [附录：快速自测脚本](#附录快速自测脚本)

---

## 第一章：零基础理解"调用 LLM"这件事

### 1.1 LLM 是什么

**LLM（Large Language Model，大语言模型）** 就是"ChatGPT 背后的那种 AI"。像 DeepSeek、智谱 GLM、OpenAI 的 GPT 都是 LLM。

对程序员来说，可以把 LLM 想象成一个**超强力的"文字处理工厂"**：

- 你给它一段文字（**输入**），比如"帮我总结这段话"
- 它给你一段文字（**输出**），比如"这段话主要讲了……"

这个工厂不在你的电脑里，而是运行在**别人公司的服务器**上。你想使用它，就必须通过网络访问它——这就叫"**调用 API**"。

> 注意：项目里还有一个概念叫"嵌入模型（Embedding）"（如 BGE-M3），那是把文字变成向量用的，不是聊天用的。本篇文章只讲**聊天/生成类**的 LLM 调用，也就是 `llm_service.py` 干的事。

### 1.2 调用 LLM = 发一个 HTTP 请求

"调用 LLM"听起来很高级，其实底层就一句话：

> **你的程序向 LLM 提供商的服务器发一个 HTTP POST 请求，服务器把 AI 生成的结果作为 HTTP 响应返回给你。**

HTTP 请求大家应该不陌生——浏览器打开网页就是发 HTTP 请求。区别在于：

| | 打开网页 | 调用 LLM |
|---|---|---|
| 方法 | GET | POST |
| 请求内容 | 无 | 一段 JSON（模型名 + 你的文字） |
| 返回内容 | HTML 网页 | JSON（包含 AI 生成的结果） |

JSON 是程序之间传递数据的通用格式，长这样：

```json
{
  "model": "deepseek-v4-flash",
  "messages": [
    {"role": "user", "content": "你好，请介绍一下你自己"}
  ]
}
```

### 1.3 API Key 是什么

**API Key（密钥）** 是 LLM 提供商的"门禁卡"。

- 你去 DeepSeek 官网（platform.deepseek.com）注册，充值，它会给你一串密钥，形如 `sk-xxxxxxxxxxxx`
- 调用时把密钥放进请求头（Header）里，服务器就知道"是哪个用户在调用、该扣谁的钱"
- 密钥是**机密**，泄露了别人就能用你的额度花钱。所以它只放在 `backend/.env` 文件里（该文件已被 `.gitignore` 排除，不会上传到 GitHub）

请求头长这样：

```
Authorization: Bearer sk-你的密钥
```

### 1.4 OpenAI 兼容 API 是什么意思

OpenAI 最早制定了"如何用 HTTP 调用大模型"的行业标准格式（叫 **Chat Completions API**）。

后来几乎所有大模型厂商（DeepSeek、智谱 GLM、通义千问、Kimi……）都宣布**兼容这个格式**——即"请求的 JSON 结构、接口路径都一样，只是服务器地址和密钥不同"。

这对开发者是巨大的好消息：

> **写一次代码，换不同的 base_url 和 api_key，就能调用任何一家大模型。**

本项目的 `llm_service.py` 正是利用这一点：同一份代码，debug 模式调 GLM，生产模式调 DeepSeek，代码完全不用改。

接口路径统一为：`{base_url}/chat/completions`

- DeepSeek：`https://api.deepseek.com/chat/completions`
- 智谱 GLM：`https://open.bigmodel.cn/api/paas/v4/chat/completions`

### 1.5 必懂的 5 个专业名词

#### （1）Messages（消息列表）和 Role（角色）

发给 LLM 的"文字"不是一段字符串，而是一个**消息列表**，每条消息有一个角色：

```json
"messages": [
  {"role": "system",    "content": "你是一个专业的出题助手。"},   // 系统提示词：给 AI 定人设和规则
  {"role": "user",      "content": "请根据以下知识点出3道选择题……"}, // 用户：你的指令
  {"role": "assistant", "content": "好的，以下是……"}               // 助手：AI 之前的回答（多轮对话时才有）
]
```

- **system**：设定 AI 的角色、行为准则（提示词工程的核心战场）
- **user**：用户说的话
- **assistant**：AI 之前说过的话。多轮对话时，把历史消息全部带上，AI 才能"记住"上下文

#### （2）Token（令牌）

LLM 不是按"字"处理文字的，而是按 **token**。1 个 token 大约是 0.5~1.5 个汉字（中文一般 1 个汉字 ≈ 1~2 个 token）。

- **输入 token**：你发给它的所有文字（含 system + 历史消息）
- **输出 token**：它生成的内容
- **计费**：按 token 计费，所以代码里经常看到"截断内容""节省 token"的优化

#### （3）Temperature（温度）

控制 AI 回答的**随机性**，取值范围 0~2：

- 接近 0：稳定、严谨、每次回答差不多（适合出题、提取知识点、摘要）
- 接近 1~2：有创造力、每次回答都不一样（适合写作文、头脑风暴）

本项目里摘要/提取用 `0.3`，出题用 `0.5`，就是要"稳定输出"。

#### （4）Max Tokens（最大生成长度）

限制 AI **最多输出多少 token**。防止 AI 长篇大论刷你的钱，也防止输出超长。

#### （5）流式输出（Stream / SSE）

普通调用：AI 生成完**全部内容**才一次性返回（可能要等十几秒）。

流式调用：AI **边生成边返回**，像 ChatGPT 网页那样一个字一个字蹦出来。前端体验更好（用户不用干等）。底层协议叫 **SSE（Server-Sent Events）**，就是服务器持续推送多行 `data: ...` 文本。

本项目 RAG 问答就用流式（`chat_stream`），所以网页上回答是逐字显示的。

---

## 第二章：动手写你的第一次 LLM 调用

这一章教你三种调用方式，从"看本质"到"最省事"。建议先拿 DeepSeek 的密钥试（也可以用 GLM 的，把地址换掉即可）。

### 2.1 用 curl 调一次（看本质）

curl 是命令行工具，用来发 HTTP 请求。不需要任何编程知识：

```bash
curl https://api.deepseek.com/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-你的密钥" \
  -d '{
    "model": "deepseek-chat",
    "messages": [
      {"role": "system", "content": "你是一个友好的助手"},
      {"role": "user", "content": "你好，请用一句话介绍你自己"}
    ],
    "temperature": 0.7,
    "max_tokens": 100
  }'
```

你会收到一个 JSON 响应（下一节详细解读）。**这一条命令就是"调用 LLM"的全部本质。**

### 2.2 用 Python httpx 调一次（本项目的方式）

本项目**不用**任何大模型 SDK，而是直接用 `httpx` 库发 HTTP 请求（见 `llm_service.py`）。好处：依赖最少、代码完全可控。

```python
import httpx

async def call_llm(api_key: str, base_url: str, model: str):
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个知识渊博的助手。"},
            {"role": "user", "content": "什么是间隔重复记忆法？"},
        ],
        "temperature": 0.5,
        "max_tokens": 500,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()          # 状态码不是 2xx 就抛异常
        data = resp.json()               # 把响应体解析成字典
        return data["choices"][0]["message"]["content"]  # 取出 AI 的回复

import asyncio
print(asyncio.run(call_llm(
    api_key="sk-你的密钥",
    base_url="https://api.deepseek.com",
    model="deepseek-chat",
)))
```

跑通之后，你就已经掌握了本项目 `chat()` 方法 80% 的原理。

### 2.3 用 openai SDK 调一次（更省事的方式）

官方 SDK 帮你封装了 HTTP 细节，代码更短。项目里 ASR 服务（`asr_engine.py`）就是用这种方式：

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-你的密钥",
    base_url="https://api.deepseek.com",   # 换 GLM 的地址和密钥即可调用 GLM
)

response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[
        {"role": "system", "content": "你是一个友好的助手"},
        {"role": "user", "content": "你好"},
    ],
    temperature=0.7,
    max_tokens=100,
)

print(response.choices[0].message.content)
```

> **为什么本项目主服务不用 SDK，而用 httpx？** 因为 `LLMService` 需要精细控制：重试、限流、并发闸门、日志脱敏、共享连接池。用 SDK 这些控制反而绕。两种方式没有对错，看需求。ASR 场景简单，就直接用 SDK。

### 2.4 读懂响应结果

调用成功的响应 JSON 长这样（关键部分）：

```json
{
  "id": "chatcmpl-xxx",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "间隔重复记忆法是一种……"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 60,
    "total_tokens": 85
  }
}
```

- `choices[0].message.content`：**AI 生成的文字**（我们只要这个）
- `usage`：本次消耗的 token 数（记录日志、统计成本用）
- `choices` 是数组：因为可以请求多个候选回答（本项目只用第 0 个）

流式响应则是一行行 `data:` 开头的 SSE 文本，每个 `data:` 里是一个小片段：

```
data: {"choices":[{"delta":{"content":"间隔"}}]}

data: {"choices":[{"delta":{"content":"重复"}}]}

data: {"choices":[{"delta":{"content":"记忆法"}}]}

data: [DONE]
```

`[DONE]` 是结束标记。本项目 `chat_stream()` 就是逐行解析这些 `data:` 的。

---

## 第三章：本项目如何配置 LLM

### 3.1 配置都在 config.py

**文件**：`backend/app/config.py`

所有 LLM 相关配置集中在两处：

**（1）提供商密钥与地址（第 95~109 行）**

```python
# ---- DeepSeek AI API 配置 ----
deepseek_api_key: str = ""
deepseek_model: str = "deepseek-v4-flash"          # 模型名
deepseek_base_url: str = "https://api.deepseek.com"  # 服务器地址

# ---- GLM API 配置 ----
glm_api_key: str = ""
glm_model: str = "glm-4.7-flash"                   # 注意：GLM 模型名必须全小写
glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
```

**（2）调用行为参数（第 148~156 行）**

```python
llm_max_retries: int = 5        # 最多重试次数
llm_retry_delay: float = 1.0    # 基础重试间隔（秒），指数退避
llm_max_rpm: int = 10           # 每分钟最多请求数（限流，0 = 不限）
llm_timeout_seconds: float = 120.0  # 单次请求超时（秒）
```

这些字段通过 pydantic-settings 自动从 `backend/.env` 读取。`.env` 里写 `LLM_MAX_RPM=30`，`settings.llm_max_rpm` 就是 30。

### 3.2 debug 模式自动选择提供商

**方法**：`config.py` 的 `get_llm_config()`（第 256~278 行）

```python
def get_llm_config(self) -> dict:
    if self.debug:                      # 开发模式
        return {
            "api_key": self.glm_api_key,
            "model": self.glm_model,
            "base_url": self.glm_base_url,
            "provider": "glm",          # ← 用 GLM
        }
    return {                            # 生产模式
        "api_key": self.deepseek_api_key,
        "model": self.deepseek_model,
        "base_url": self.deepseek_base_url,
        "provider": "deepseek",         # ← 用 DeepSeek
    }
```

**设计思路**：

- 开发调试时（`DEBUG=true`）用 **GLM**：智谱有免费额度，调试不花钱
- 生产部署时（`DEBUG=false`）用 **DeepSeek**：效果更稳定

切换提供商只需改 `.env` 里的 `DEBUG`，**业务代码一行都不用改**——这就是"OpenAI 兼容 API"的好处。

### 3.3 .env 文件怎么填

**文件**：`backend/.env`（从 `.env.example` 复制而来）

```env
# 二选一填一个即可（按 DEBUG 决定用哪个）
DEBUG=true                # true → 用 GLM；false → 用 DeepSeek

# 选项 A：用 DeepSeek 时填（https://platform.deepseek.com/ 注册）
DEEPSEEK_API_KEY=sk-你的密钥

# 选项 B：用 GLM 时填（https://open.bigmodel.cn/ 注册，有免费额度）
GLM_API_KEY=你的glm密钥

# 可选：换模型 / 换地址 / 调参
# DEEPSEEK_MODEL=deepseek-v4-flash
# DEEPSEEK_BASE_URL=https://api.deepseek.com
# GLM_MODEL=glm-4.7-flash
# GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
# LLM_MAX_RETRIES=5
# LLM_MAX_RPM=10
# LLM_TIMEOUT_SECONDS=120
```

> **新手常踩的坑**：改了 `.env` 必须**重启后端服务**才生效（配置是启动时加载的）。另外 `.env` 里键名是全大写下划线，对应 config.py 里的小写字段（`DEEPSEEK_API_KEY` ↔ `deepseek_api_key`），这是 pydantic-settings 的自动映射规则。

---

## 第四章：LLMService 核心类逐段精讲

### 4.1 文件总览

**文件**：`backend/app/services/llm_service.py`（共 1336 行，是项目里最重要的服务之一）

整个文件包含 6 个部分：

| 部分 | 行号 | 作用 |
|---|---|---|
| 模块级共享客户端 | 40~98 | `get_llm_client()` / `close_llm_client()` |
| RateLimiter 限流器 | 112~146 | 令牌桶算法，控制请求频率 |
| ConversationSession | 149~234 | 普通多轮对话会话 |
| UnderstandingSession | 237~349 | 知识提取专用会话（省 token 优化） |
| CombinedAnalysisSession | 352~472 | 联合分析专用会话 |
| **LLMService 主类** | 475~1336 | 所有 LLM 调用方法的家 |

下面按顺序讲，重点在 `LLMService` 主类。

### 4.2 模块级共享客户端（get_llm_client）

**行号**：40~98

```python
_shared_llm_client: Optional[httpx.AsyncClient] = None

def get_llm_client() -> httpx.AsyncClient:
    global _shared_llm_client, _shared_llm_client_loop
    current_loop = _current_loop()
    if _shared_llm_client is not None and _shared_llm_client_loop is not current_loop:
        # 事件循环已变化（如 Celery 每个任务新建 loop）：旧连接全部失效，丢弃重建
        logger.warning("检测到 LLM 客户端跨事件循环复用，重建共享客户端 ...")
        _shared_llm_client = None
        _shared_llm_client_loop = None
    if _shared_llm_client is None:
        _shared_llm_client = httpx.AsyncClient(timeout=settings.llm_timeout_seconds)
        _shared_llm_client_loop = current_loop
    return _shared_llm_client
```

**为什么需要它？** 创建一个 `httpx.AsyncClient` 会建立连接池、做 TLS 握手，成本不低。旧代码每次调用都新建一个，白白浪费。现在整个进程**共享一个客户端**，第一次调用时惰性创建（懒加载），之后复用。

**为什么记录 event loop？** 这是踩坑后的修复（代码注释里写了 "Event loop is closed" 问题）：

- httpx 连接池里的连接"绑定"在创建时的事件循环上
- Celery worker 里每个任务用 `asyncio.run()` 创建**全新的事件循环**
- 跨任务复用旧连接会报 "Event loop is closed"

解决：保存创建客户端时的 loop 对象，检测到 loop 变了就**丢弃旧客户端重建**。注意用对象引用比较（`is not`）而不是 `id()`，因为 loop 被 GC 后 id 可能被新 loop 复用造成误判。

**`close_llm_client()`**（83~97 行）：应用关闭时优雅关闭共享客户端，在 `main.py` 的 lifespan 关闭阶段调用：

```python
# backend/app/main.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    from .services.llm_service import close_llm_client
    close_llm_client()
```

### 4.3 限流器 RateLimiter（令牌桶）

**行号**：112~146

LLM 提供商都限制每分钟最大请求数（比如 DeepSeek 免费档 10 RPM），超过就返回 **429 Too Many Requests**。本项目用**令牌桶算法**控制自己的请求节奏：

```python
class RateLimiter:
    def __init__(self, max_rpm: int = 10):
        self.max_rpm = max_rpm
        self._tokens = float(max_rpm)      # 桶里初始有 max_rpm 个令牌
        self._last_refill = time.monotonic()  # 上次补充令牌的时间
        self._lock = asyncio.Lock()

    async def acquire(self):
        if self.max_rpm <= 0:              # 0 = 不限流
            return
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                # 令牌按速率补充：max_rpm/60 个/秒，最多攒满 max_rpm 个
                self._tokens = min(self.max_rpm, self._tokens + elapsed * (self.max_rpm / 60.0))
                self._last_refill = now
                if self._tokens >= 1:      # 有令牌 → 拿走一个，放行
                    self._tokens -= 1
                    return
                wait_time = (1.0 - self._tokens) * (60.0 / self.max_rpm)
            await asyncio.sleep(wait_time)  # 没令牌 → 算出要等多久，睡一觉再试
```

**通俗理解**：桶里每 6 秒"滴"进一个令牌（10 RPM 时）。每次请求要拿走一个令牌；桶空了就等着，直到滴进新令牌。这样请求速率永远不会超过 10 次/分钟。

> **新手注意**：限流器是**类级共享**的（`LLMService._rate_limiter`），不是每个实例一份。否则代码里创建 10 个 `LLMService()` 实例，每个实例各限各的，总请求数照样超限——这是 F-05 修复的另一个 bug。

### 4.4 并发闸门 Semaphore

**行号**：489~508

```python
# 类级共享并发闸门（所有实例共享同一信号量）
_rate_limiter: Optional["RateLimiter"] = None
_semaphore: Optional[asyncio.Semaphore] = None

def __init__(self):
    ...
    if LLMService._rate_limiter is None:
        LLMService._rate_limiter = RateLimiter(max_rpm=settings.llm_max_rpm)
    if LLMService._semaphore is None:
        LLMService._semaphore = asyncio.Semaphore(3)   # 最多 3 个请求同时在飞
    self._rate_limiter = LLMService._rate_limiter
    self._semaphore = LLMService._semaphore
```

`asyncio.Semaphore(3)` 是**并发闸门**：最多允许 3 个 LLM 请求同时进行，第 4 个要等前面有请求完成。

**限流和并发是两回事**：

- 限流（RateLimiter）：控制"**频率**"——每分钟最多 10 次
- 闸门（Semaphore）：控制"**同时**有几个在飞"——最多 3 个并行

两者配合：既不会瞬间并发爆炸，也不会高频轰炸 API。

### 4.5 初始化：服务怎么知道调哪家

**行号**：494~508

```python
def __init__(self):
    llm_config = settings.get_llm_config()   # 从 config.py 拿到当前提供商配置
    self._api_key = llm_config["api_key"]    # 密钥
    self._model = llm_config["model"]        # 模型名
    self._base_url = llm_config["base_url"]  # 服务器地址
    self._provider = llm_config["provider"]  # "glm" 或 "deepseek"（仅用于日志）
    self._max_retries = settings.llm_max_retries
    self._retry_delay = settings.llm_retry_delay
    ...
```

**使用方式**（项目里到处可见）：

```python
from ..services.llm_service import LLMService

llm_service = LLMService()          # 每次创建实例很便宜，配置是共享的
result = await llm_service.chat([{"role": "user", "content": "你好"}])
```

### 4.6 chat()：最核心的通用聊天接口

**行号**：510~613。这是整个文件的心脏，其他所有方法最终都调用它。

#### 第一步：构造请求（url / headers / payload）

```python
url = f"{self._base_url}/chat/completions"          # OpenAI 兼容接口路径
headers = {
    "Authorization": f"Bearer {self._api_key}",      # 门禁卡
    "Content-Type": "application/json",
}
payload = {
    "model": self._model,                            # 用哪个模型
    "messages": messages,                            # 消息列表（system/user/assistant）
    "temperature": temperature,                      # 随机性
    "max_tokens": max_tokens,                        # 输出上限
}
if response_format:                                  # 要求返回 JSON 时加这个
    payload["response_format"] = response_format     # 如 {"type": "json_object"}
```

#### 第二步：记录请求日志（脱敏）

```python
logger.debug(
    f"LLM 请求 | scene={scene} | provider={self._provider} | model={self._model} | ...\n"
    f"messages={json.dumps(_truncate_messages(messages), ensure_ascii=False)}"
)
```

`_truncate_messages()`（100~109 行）把每条消息内容**截断到 200 字符**再写日志——防止把用户上传的整篇文档全量打进日志（F-20 修复）。

#### 第三步：并发闸门 + 限流

```python
async with self._semaphore:          # 最多 3 个并发
    await self._rate_limiter.acquire()   # 每分钟最多 10 次
```

#### 第四步：发送请求 + 重试循环

```python
for attempt in range(self._max_retries):   # 默认最多尝试 5 次
    try:
        resp = await get_llm_client().post(url, json=payload, headers=headers)
        resp.raise_for_status()            # 非 2xx 抛 HTTPStatusError
        data = resp.json()
        content = data["choices"][0]["message"]["content"]  # 提取回答
        # 记录耗时与 token 消耗
        logger.info(f"LLM 响应 | scene={scene} | ... prompt_tokens=... | total_tokens=... | elapsed=...ms")
        return content
    except httpx.HTTPStatusError as e:
        last_error = e
        # 4xx 客户端错误（400/401/403/404）不重试，直接抛——重试也没用
        if e.response is not None and 400 <= e.response.status_code < 500 and e.response.status_code != 429:
            raise
        # 429（限流）和 5xx（服务器错误）：可重试
        if attempt < self._max_retries - 1:
            delay = min(30 * (attempt + 1), 120)          # 30s, 60s, 90s, 120s...
            delay = delay * (0.5 + random.random() * 0.5) # 加随机抖动
            await asyncio.sleep(delay)
    except Exception as e:
        last_error = e
        if attempt < self._max_retries - 1:
            delay = min(self._retry_delay * (2 ** attempt), 60)  # 1s, 2s, 4s... 指数退避
            delay = delay * (0.5 + random.random() * 0.5)        # 加抖动
            await asyncio.sleep(delay)

raise Exception(f"LLM API 调用失败，重试 {self._max_retries} 次后仍出错 ...")
```

**这一段值得仔细读，它浓缩了工业级 API 调用的所有智慧**：

1. **指数退避（Exponential Backoff）**：第 1 次失败等 1 秒，第 2 次等 2 秒，第 3 次等 4 秒……给服务器喘息时间
2. **抖动（Jitter）**：等待时间乘一个 0.5~1.0 的随机数。防止多个请求同时重试、同时撞上去（"惊群效应"）
3. **区分错误类型**：`400/401/403/404` 是**你的问题**（密钥错、参数错），重试一万次也没用，直接抛；`429/5xx` 是**临时问题**（限流、服务器繁忙），值得重试
4. **最终兜底**：重试耗尽后抛一个带场景信息的异常，上层（Celery 任务）捕获后标记任务失败

### 4.7 chat_stream()：流式输出

**行号**：615~717。和 `chat()` 的区别：

- `payload` 里加 `"stream": True`，服务器用 SSE 逐块推送
- **不做重试**（流式重试语义复杂，失败直接抛，由调用方处理）
- temperature 固定 0.3
- 返回的是**异步生成器**，调用方用 `async for` 逐块接收

```python
async with self._semaphore:
    await self._rate_limiter.acquire()
    try:
        client = get_llm_client()
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():   # 逐行读 SSE
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[len("data:"):].strip()  # 去掉 "data:" 前缀
                if data_str == "[DONE]":                # 结束标记
                    break
                chunk = json.loads(data_str)            # 解析每个片段
                ...
                delta = choices[0].get("delta") or {}   # delta 是"增量"内容
                content = delta.get("content")
                if content:
                    total_content.append(content)
                    yield content                        # 逐 token 交给调用方
    except httpx.HTTPError as e:
        logger.warning(...)
        raise
```

**使用方式**（见第七章的 RAG 问答场景）：

```python
async for chunk in llm_service.chat_stream(messages, scene="rag_answer_stream"):
    print(chunk, end="")   # 一个字一个字地打印
```

---

## 第五章：封装好的业务方法（拿来即用）

`chat()` 是"裸接口"，直接用它需要自己拼 system prompt、自己解析 JSON。`LLMService` 把项目里常用的任务都封装成了现成方法。**新手写业务时优先调用这些方法，不要自己造轮子。**

### 5.1 summarize_chapter — 章节摘要

**行号**：719~749

```python
async def summarize_chapter(self, chapter_title: str, chapter_content: str) -> str:
    # 限制输入长度，避免超出上下文窗口
    max_content = 8000
    if len(chapter_content) > max_content:
        chapter_content = chapter_content[:max_content] + "\n...(内容过长已截断)"

    messages = [
        {"role": "system", "content": "你是一个专业的学术助手。请为给定章节生成简洁准确的摘要。..."},
        {"role": "user", "content": f"章节标题：{chapter_title}\n\n章节内容：\n{chapter_content}"},
    ]
    return await self.chat(messages, temperature=0.3, max_tokens=1024, scene="summarize_chapter")
```

**三个细节值得学习**：

1. **输入截断**：章节可能远超上下文窗口，先截到 8000 字符，末尾注明"内容过长已截断"，让 AI 知道内容不完整
2. **scene 参数**：给这次调用起名字，写日志时一眼看出是哪个业务场景
3. **temperature=0.3**：摘要要忠实原文，随机性要低

### 5.2 extract_knowledge_points — 提取知识点

**行号**：751~827。这是"**让 LLM 输出结构化数据**"的典型范例。

```python
async def extract_knowledge_points(self, chapter_title, chapter_content) -> List[Dict]:
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个专业的知识提取助手。请从给定章节中提取关键知识点。\n\n"
                "知识点类型说明：\n"
                "- concept: 概念类...\n"
                "- formula: 公式类...\n"
                "- qa: 问答对...\n"
                "- definition: 定义类...\n\n"
                "请以 JSON 数组格式返回，每个元素包含：\n"
                '- card_type: 类型（concept/formula/qa/definition）\n'
                "- title: 知识点标题...\n"
                ...
                "4. 只返回 JSON 数组，不要其他文字"
            ),
        },
        {"role": "user", "content": f"章节标题：{chapter_title}\n\n章节内容：\n{chapter_content}"},
    ]
    response = await self.chat(
        messages,
        temperature=0.3,
        max_tokens=20000,
        response_format={"type": "json_object"},   # ← 要求模型只输出 JSON
        scene="extract_knowledge",
    )

    try:
        result = json.loads(response)              # 把字符串解析成 Python 对象
        if isinstance(result, dict):               # 兼容 {"points": [...]} 等包装格式
            for key in ["points", "knowledge_points", "items", "data"]:
                if key in result:
                    return result[key]
            for v in result.values():
                if isinstance(v, list):
                    return v
            return []
        if isinstance(result, list):
            return result
        return []
    except json.JSONDecodeError:
        logger.warning(f"知识点提取结果 JSON 解析失败: {response[:200]}")
        return []                                   # 解析失败返回空列表，不抛异常
```

**让 AI 输出 JSON 的三板斧**（写类似功能时照抄）：

1. **system prompt 里写死 JSON 格式**：给一个完整的 JSON 示例 + "只返回 JSON，不要其他文字"
2. **`response_format={"type": "json_object"}`**：API 层面的强制约束（DeepSeek/GLM 都支持）
3. **容错解析**：AI 偶尔不听话。`json.loads` 失败时**记日志降级**（返回空列表），绝不让一个解析错误搞崩整个管道

### 5.3 generate_questions — 生成题目

**行号**：829~917。结构同 5.2：JSON 格式的 system prompt + `json_object` + 容错解析。支持三种题型：`choice`（选择题）、`fill_blank`（填空题）、`short_answer`（简答题），每题型 1~2 道。

**行号**：919~1037 是 `generate_questions_batch()`——**批量**版本，把多个知识点合并到一个请求里，每个生成 1 道题：

```python
# 把多个卡片拼成一段文本，一次请求搞定
cards_text = ""
for i, card in enumerate(cards):
    cards_text += f"\n--- 知识点 {i+1} ---\n"
    cards_text += f"标题：{card['title']}\n"
    cards_text += f"内容：{card['content'][:500]}\n"   # 每个卡片内容截断到 500 字
```

**为什么要批量？** 减少 API 调用次数 = 省钱 + 不触发限流。10 张卡片单独调 10 次，合并调 1 次。这是工程上的常见取舍（代价是单次请求的 token 变多）。

### 5.4 rag_answer — 智能问答

**行号**：1039~1071

```python
async def rag_answer(self, question: str, context: str) -> str:
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个知识渊博的学习助手。请根据提供的参考资料回答用户问题。\n\n"
                "回答原则：\n"
                "1. 优先使用参考资料...\n"
                "2. 自主知识补充：如果参考资料不足...可以结合你自己的知识来回答，但请说明...\n"
                "3. 诚实标注...\n"
                ...
            ),
        },
        {"role": "user", "content": f"参考资料：\n{context}\n\n问题：{question}"},
    ]
    return await self.chat(messages, temperature=0.5, max_tokens=2048, scene="rag_answer")
```

**这就是 RAG（检索增强生成）的最后一步**。RAG 的完整链路是：

```
用户提问 → 从向量数据库检索相关笔记片段（检索，不调 LLM）
        → 把检索结果拼进 prompt（"参考资料：..."）
        → 调用 LLM 生成回答（生成，调 LLM）
```

**为什么这么设计？** LLM 不知道用户笔记的内容（它没训练过这些数据）。把相关片段"喂"给它，它就能基于用户自己的资料回答。注意 system prompt 里特意写了"参考资料不足时可以说实话，而不是胡编"——这是为了减少 **AI 幻觉**（一本正经地胡说八道）。

### 5.5 其他方法一览

| 方法 | 行号 | 用途 |
|---|---|---|
| `generate_extension_knowledge` | 1209~1269 | 基于已掌握知识点生成进阶拓展知识点 |
| `infer_card_relations` | 1271~1336 | 批量推断知识卡片间关系（前置/后续/对比），用于知识图谱 |
| `create_understanding_session` | 1073~1119 | 创建知识提取会话（见第六章） |
| `create_question_session` | 1121~1158 | 创建题目生成会话 |
| `create_combined_analysis_session` | 1160~1207 | 创建联合分析会话（资料 vs 用户笔记） |

---

## 第六章：多轮对话会话（Session）机制

### 6.1 为什么需要会话

单次调用 `chat()` 是"失忆"的：这次发的内容，下次调用时 LLM 完全不记得。但理解管道需要**跨章节的连续性**：

- 第 1 章提取了知识点 A
- 第 2 章提取时，要**避免重复提取 A**

解决办法：把历史消息全带上。这就是 `ConversationSession` 做的事——在 Python 对象里维护消息列表，每次 `ask()` 自动追加历史。

### 6.2 ConversationSession — 普通多轮对话

**行号**：149~234

```python
class ConversationSession:
    def __init__(self, llm_service, system_prompt, temperature=0.3, max_tokens=4096,
                 response_format=None, max_context_pairs=30, scene=None):
        self._messages = [{"role": "system", "content": system_prompt}]  # 第一层：system
        ...

    async def ask(self, user_content: str) -> str:
        self._messages.append({"role": "user", "content": user_content})     # 追加用户消息
        response = await self._llm.chat(self._messages, ...)                 # 带全量历史调用
        self._messages.append({"role": "assistant", "content": response})    # 追加 AI 回复
        self._trim_if_needed()                                               # 超长裁剪
        return response

    def _trim_if_needed(self):
        max_msg_count = self._max_context_pairs * 2 + 1    # 30 轮对话 + 1 条 system
        if len(self._messages) > max_msg_count:
            system_msg = self._messages[0]                 # 保留 system
            recent = self._messages[-(self._max_context_pairs * 2):]  # 保留最近 30 轮
            self._messages = [system_msg] + recent         # 中间的扔掉
```

**裁剪的意义**：对话轮次多了，历史消息会撑爆上下文窗口（还烧钱）。只保留 system + 最近 30 轮，这是工程上的"滑动窗口"。

### 6.3 UnderstandingSession — 知识提取专用会话

**行号**：237~349。这是项目里最精巧的优化之一，值得细读。

**问题**：理解管道要对 17 章文档逐章提取知识点。如果用 `ConversationSession`，第 6 轮时请求里要带前 5 章的全部原文——token 消耗呈线性爆炸（实测 17 章文档第 6 轮约 **72000 tokens**）。

**优化思路**：LLM 不需要记住每章原文，只需要知道"**已经提取了哪些知识点标题**"就够了。

```python
class UnderstandingSession(ConversationSession):
    def __init__(self, llm_service, system_prompt, **kwargs):
        super().__init__(llm_service, system_prompt, **kwargs)
        self._extracted_titles: List[str] = []   # 只存标题，不存原文

    async def ask(self, user_content: str) -> str:
        context_hint = ""
        if self._extracted_titles:
            titles = self._extracted_titles[-self.MAX_TITLES:]   # 最多 200 个
            context_hint = (
                "\n\n[已提取知识点标题(请勿重复提取以下知识点)]:\n"
                + "\n".join(f"- {t}" for t in titles)
            )

        # 关键：每次只发 system + 当前章节 + 已提取标题，不带历史原文
        messages = [
            self._messages[0],   # system
            {"role": "user", "content": user_content + context_hint},
        ]
        response = await self._llm.chat(messages, ...)
        self._extract_new_titles(response)   # 从本次响应中提取新标题，加入列表
        return response
```

**效果**：第 N 轮的输入从 `O(N × 章节长度)` 降为 `O(章节长度 + N × 标题长度)`。实测同样场景 token 从 ~72000 降到 ~8100，**节省 89%**。

**给新手的启示**：调 LLM 要时刻想着"**什么信息是必须让 AI 看到的**"。很多时候一个摘要、一个标题列表，就比整篇原文更有用且便宜得多。

### 6.4 CombinedAnalysisSession — 联合分析会话

**行号**：352~472。思路与 UnderstandingSession 相同（标题去重 + 不累积原文），区别是：

- `ask()` 接收两个参数：`material_chapter_content`（学习资料章节）+ `personal_note_content`（用户笔记全文）
- 输出分为 `regular_points`（资料和笔记都覆盖到的 = 已掌握）和 `blind_spots`（资料有但笔记没有的 = 盲点）

```python
async def ask(self, material_chapter_content: str, personal_note_content: str) -> str:
    user_content = (
        f"## 本章节学习资料：\n{material_chapter_content}\n\n"
        f"## 用户笔记（全文）：\n{personal_note_content}\n\n"
        f"请针对本章节资料与用户笔记做联合分析。"
        + context_hint
    )
    ...
```

---

## 第七章：LLM 在项目中的实际调用场景

### 7.1 场景一：理解管道（章节→摘要→知识点）

**入口**：`backend/app/tasks/understand_tasks.py`（Celery 异步任务，209~266 行）

**流程**：

```
文档转换完成 → Celery 任务触发 → 章节切分 → UnderstandingSession 逐章调用 LLM
                                                              ↓
                                            每章得到摘要 + 知识点列表（JSON）
                                                              ↓
                                            存入数据库 → 前端展示知识卡片
```

核心代码模式：

```python
from ..services.llm_service import LLMService

llm_service = LLMService()
# 创建会话：system prompt 内置，要求 JSON 输出 {"chapters": [...]}
session = llm_service.create_understanding_session()

for chapter in chapters:
    result = await session.ask(chapter_content)   # 逐章问，自动避免重复提取
    # result 是 JSON 字符串，解析后入库
```

### 7.2 场景二：RAG 智能问答（流式）

**入口**：`backend/app/api/understanding.py`（550~624 行，SSE 流式接口）

**流程**：

```
用户提问 → /api/understanding/rag/stream → 检索向量库拿相关片段（不调 LLM）
        → 组装 messages（参考资料 + 问题）→ llm_service.chat_stream()
        → SSE 逐 token 推给前端 → 结束发 sources（引用来源）和 done
```

核心代码模式：

```python
llm_service = LLMService()
async for chunk in llm_service.chat_stream(messages, scene="rag_answer_stream"):
    # 每个 chunk 是 AI 生成的一个片段
    yield f"event: token\ndata: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
```

**SSE 事件协议**（前端按这个解析）：

```
event: token   data: {"content": "..."}      每个片段
event: sources data: {"sources": [...], "provider": "..."}  引用来源
event: done    data: {}                       结束
event: error   data: {"message": "..."}       出错
```

### 7.3 场景三：ASR 标点恢复（另一种调用方式）

**文件**：`backend/app/services/asr/asr_engine.py`（199~233 行）

这是项目里**第二种** LLM 调用方式——直接用 `openai` SDK（见 2.3 节）。用途：语音转写出的文字没有标点，用 LLM 恢复标点并生成标题。

```python
from openai import OpenAI

def _get_openai_client(api_key, base_url):
    cache_key = f"{api_key}:{base_url}"
    if cache_key not in _client_cache:
        _client_cache[cache_key] = OpenAI(api_key=api_key, base_url=base_url)  # 客户端也做了缓存
    return _client_cache[cache_key]

def restore_punctuation(raw_text, api_key, base_url, model):
    client = _get_openai_client(api_key, base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": PUNCTUATION_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        temperature=0.1,     # 标点恢复要确定性，温度极低
        max_tokens=4096,
    )
    return response.choices[0].message.content.strip()
```

**对比总结**：

| | LLMService（httpx 直连） | ASR（openai SDK） |
|---|---|---|
| 文件 | `services/llm_service.py` | `services/asr/asr_engine.py` |
| 优点 | 可精细控制重试/限流/日志 | 代码简洁 |
| 场景 | 高频、复杂、需要容错 | 低频、简单 |

---

## 第八章：常见报错与排查指南

### 8.1 401 Unauthorized（密钥无效）

**现象**：日志出现 `LLM 客户端错误不重试 | status=401`。

**排查**：

1. `backend/.env` 里密钥是否填了？填对了吗？（注意 DeepSeek 密钥以 `sk-` 开头）
2. DEBUG 模式和密钥是否匹配？`DEBUG=true` 用 `GLM_API_KEY`，`DEBUG=false` 用 `DEEPSEEK_API_KEY`
3. 改了 `.env` 后**重启后端**了吗？
4. GLM 的密钥格式是 `id.secret` 两段点分格式，复制时别漏后半段

### 8.2 429 Too Many Requests（限流）

**现象**：日志出现 `LLM 调用失败 | attempt 1/5`，错误是 429。

**原因**：请求太频繁。免费额度通常每分钟只有几次~十几次。

**解决**：

- 调低 `.env` 的 `LLM_MAX_RPM`（如 5），让限流器提前自我约束
- 或者等一会儿再试（代码会自动重试，指数退避）
- 批量任务（如生成题目）用 `generate_questions_batch` 合并请求

### 8.3 超时（Timeout）

**现象**：`httpx.TimeoutException` 或请求卡了很久才失败。

**排查**：

- 内容太长？`chat()` 默认超时 120 秒，超长输入生成时间会久
- 网络问题？国内访问部分境外 API 不稳定，DeepSeek/GLM 都是国内服务，一般没问题
- 确认 `LLM_TIMEOUT_SECONDS` 是否被改小了

### 8.4 JSON 解析失败

**现象**：日志出现 `XX 结果 JSON 解析失败: ...`（如知识点提取、题目生成）。

**原因**：模型没按 prompt 要求输出纯 JSON（偶尔发生），或输出被截断（max_tokens 不够）。

**解决**：

- 这不是致命错误：代码已容错降级（返回空列表）。看日志里的响应前 200 字符，判断是哪种情况
- 若频繁发生：调大 `max_tokens`，或把 system prompt 里的 JSON 示例写得更死

### 8.5 Event loop is closed

**现象**：Celery 任务里调用 LLM 时报这个错。

**原因**：httpx 连接跨事件循环复用了（每个 Celery 任务新建 loop）。

**解决**：这是 F-05 已修复的问题，代码会自动重建客户端。如果你在**自己的新代码**里也遇到，检查是否复用了跨 loop 创建的 httpx 客户端。

### 8.6 怎么快速定位 LLM 问题

所有 LLM 调用都有结构化日志，logger 名是 `engramnote.llm`。日志格式：

```
LLM 请求 | scene=extract_knowledge | provider=glm | model=glm-4.7-flash | temperature=0.3 | ...
LLM 响应 | scene=extract_knowledge | provider=glm | model=glm-4.7-flash | prompt_tokens=... | completion_tokens=... | total_tokens=... | elapsed=...ms
```

排查时看三个信息：

1. **scene**：哪个业务场景（提取/出题/问答……）
2. **provider + model**：当前调的是哪家哪个模型
3. **elapsed + tokens**：耗时和消耗（异常慢或 token 暴增都是线索）

---

## 第九章：新手调参指南

### 9.1 常用参数速查表

| 参数 | 推荐值 | 说明 |
|---|---|---|
| `temperature` | 摘要/提取 0.3，出题 0.5，闲聊 0.7 | 越高越有创意，越低越稳定 |
| `max_tokens` | 摘要 1024，出题 8192，问答 2048 | 够用就好，越大越贵 |
| `LLM_MAX_RETRIES` | 5 | 临时故障重试次数 |
| `LLM_MAX_RPM` | 10（免费额度小心超限） | 每分钟请求上限 |
| `LLM_TIMEOUT_SECONDS` | 120 | 单次请求超时 |

### 9.2 想换模型 / 换提供商

**换模型**（同提供商）：改 `.env` 的 `DEEPSEEK_MODEL` 或 `GLM_MODEL`。

**换提供商**（如从 DeepSeek 换到 Kimi/通义）：这些厂商都兼容 OpenAI 格式，只需在 `config.py` 的 `get_llm_config()` 里换成新提供商的 api_key/model/base_url，或新增一组配置字段。

### 9.3 给新手的 5 条建议

1. **先跑通 2.1 的 curl**，理解"调用 = 发 HTTP 请求"后再看代码，一切豁然开朗
2. **所有 LLM 调用都走 `LLMService`**，不要自己另起炉灶——重试、限流、日志都替你做好了
3. **想要结构化输出**（JSON），牢记：写死格式的 system prompt + `response_format` + 容错解析，三板斧缺一不可
4. **注意 token 成本**：长文本先截断/摘要再发，批量任务合并请求
5. **改配置后重启服务**，看日志用 `scene=` 过滤，报错先查第八章

---

## 附录：快速自测脚本

把下面的脚本存为 `test_llm.py` 放在 `backend/` 目录下，运行 `python test_llm.py`（需已配置好 `.env`），验证你的 LLM 配置是否通：

```python
"""快速验证 LLM 配置是否可用（放在 backend/ 目录下运行）"""
import asyncio
import sys
import os

# 确保能 import 到 app 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.llm_service import LLMService


async def main():
    service = LLMService()
    print(f"当前提供商: {service._provider}")
    print(f"当前模型:   {service._model}")
    print(f"API 地址:   {service._base_url}")

    if not service._api_key:
        print("\n❌ 没有配置 API Key！请检查 backend/.env 的 GLM_API_KEY 或 DEEPSEEK_API_KEY")
        return

    try:
        # 测试 1：通用聊天
        reply = await service.chat(
            [{"role": "user", "content": "请回答：1+1等于几？只回答数字"}],
            temperature=0.3,
            max_tokens=50,
            scene="self_test",
        )
        print(f"\n✅ 聊天测试通过，回答: {reply.strip()}")

        # 测试 2：JSON 结构化输出
        result = await service.extract_knowledge_points(
            "测试章节",
            "间隔重复（spaced repetition）是一种利用心理学间隔效应，"
            "通过不断复习所学内容并逐渐增加复习间隔来提升记忆效率的学习技巧。",
        )
        print(f"✅ 知识点提取测试通过，提取到 {len(result)} 个知识点")

        # 测试 3：流式输出
        print("\n✅ 流式输出测试（逐字显示）:")
        async for chunk in service.chat_stream(
            [{"role": "user", "content": "用一句话介绍间隔重复"}],
            scene="self_test",
        ):
            print(chunk, end="", flush=True)
        print()

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        print("请对照《LLM服务详解》第八章排查")


if __name__ == "__main__":
    asyncio.run(main())
```

预期输出（GLM 为例）：

```
当前提供商: glm
当前模型:   glm-4.7-flash
API 地址:   https://open.bigmodel.cn/api/paas/v4

✅ 聊天测试通过，回答: 2
✅ 知识点提取测试通过，提取到 2 个知识点
✅ 流式输出测试（逐字显示）:
间隔重复是一种……
```

---

## 附：相关文件索引

| 文件 | 作用 |
|---|---|
| `backend/app/services/llm_service.py` | **LLM 服务本体**（本文主角） |
| `backend/app/config.py` | LLM 配置定义与提供商选择（95~109、148~156、256~278 行） |
| `backend/.env.example` | 环境变量模板（密钥、DEBUG、LLM 调参） |
| `backend/app/services/asr/asr_engine.py` | 第二种调用方式：openai SDK 标点恢复/标题生成 |
| `backend/app/tasks/understand_tasks.py` | 理解管道异步任务（调用 UnderstandingSession） |
| `backend/app/api/understanding.py` | RAG 流式问答接口（调用 chat_stream + SSE） |
| `backend/app/services/rag_service.py` | RAG 检索 + 非流式问答 |
| `backend/app/services/assessment_service.py` | 学情评估（调用 chat） |
| `backend/app/services/graph_service.py` | 知识图谱关系推断（调用 infer_card_relations） |
| `backend/app/services/knowledge_link_service.py` | 卡片关联与拓展（调用 generate_extension_knowledge） |
| `backend/app/main.py` | 应用入口（关闭时调用 close_llm_client） |
