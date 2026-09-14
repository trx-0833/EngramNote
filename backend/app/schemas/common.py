"""跨域共用的最小响应模型

## 为什么需要这个模块

阶段 5.1 的前置修复要求给 22 个此前没写 `response_model=` 的端点补上响应模型，
其中有好几个端点的**全部**返回内容就是一句操作结果说明：

- `DELETE /api/folders/{folder_id}`        → `{"message": "文件夹已删除"}`
- `POST   /api/graph/confirm` 等关系操作    → `{"success": ..., "relation_id": ...}`
- `POST   /api/knowledge/cards/{id}/generate-questions` → 触发类回执

如果每个域各自抄一份 `{"message": str}`，这些声明迟早会彼此漂移，
而漂移的表现是"同一个形状在两个端点生成出两个不同的 TS 类型"
（前端就得为同一件事维护两个名字）。因此共用的部分放在这里，
各域只放自己**特有**的字段。

⚠️ 本模块只放**确实被两个以上域共用**的模型。只被一个端点使用的形状
（例如 `PrepareUploadResponse`）应留在自己域的 schema 模块里 ——
否则这个文件会退化成"所有不想归类的东西都扔这儿"。
"""

from pydantic import BaseModel
from starlette.responses import StreamingResponse


class MessageResponse(BaseModel):
    """只带一句人类可读操作结果的响应

    刻意**不**给 `message` 设默认值：调用方必须显式给出文案，
    否则会生成一个"永远是空字符串"的字段 —— 那种字段在契约里
    看起来存在，实际没有任何信息。
    """

    message: str


class HealthResponse(BaseModel):
    """`GET /health` 的响应

    出处：`app/main.py` 的 `health_check`：`{"status": "ok", "app": settings.app_name}`

    ⚠️ 这个端点**没有前端调用方**（漂移报告把它列在"schema 里无调用方的 8 个操作"里），
    补它纯粹是为了让"22 个端点之外"的同类缺口也一并收口 —— 它同样属于
    "schema 里是空壳 → 生成类型是 `unknown`"。它不在前端本轮要切换的 22 个里，
    因此**不计入** 22 的分子。

    刻意**不**把 `app` 改名为 `app_name`：字段名是对外契约的一部分，
    `/health` 的消费方（负载均衡探针、监控脚本）读的是 `app`。
    """

    status: str
    app: str


class EventStreamResponse(StreamingResponse):
    """SSE（`text/event-stream`）响应的 media type 声明载体

    ## 为什么需要它

    FastAPI 从路由的 `response_class` 推导响应 media type。路由**不声明**时
    它默认成 `application/json` —— 于是两个 SSE 端点在 `openapi.json` 里长成
    「200 → 空 JSON schema」，生成的 TS 类型是 `unknown`，而客户端照着它去
    `response.json()` 会在真实响应上抛错。**问题不是"缺类型"，而是"类型说错了"。**

    ## 为什么不能改用 `response_model=`

    `response_model=` 的语义是"把返回值当 JSON 数据校验并序列化"。SSE 处理函数
    返回的是 `StreamingResponse` 对象，套上它只有两种结局：校验失败，
    或者被包装成 `JSONResponse` —— 后者会**把流变成一次性 JSON**，属于行为破坏。
    结论：`response_model` 在 SSE 上是**错误的工具**。正确做法是如实声明 media type，
    并把事件契约写进文档（见 `app/api/notes/ask.py` 与 `app/api/understanding.py`
    的模块 docstring）。

    ## 为什么不改行为

    处理函数仍然显式 `return StreamingResponse(...)`；Starlette 见到返回值
    已经是 `Response` 实例就直接用它，不会经过这个类。本类只在**生成 OpenAPI 时**
    被读取 `media_type`。
    """

    media_type = "text/event-stream"


class Mp4StreamResponse(StreamingResponse):
    """视频流响应的 media type 声明载体（`GET /api/notes/{note_id}/video`）

    与 `EventStreamResponse` 同一类问题、同一个处理方式：该端点此前没有声明
    `response_class`，于是 OpenAPI 里它是「200 → 空 JSON schema」。
    实际上它返回的是 **`video/mp4` 的字节流**（本地存储模式下支持
    `Range` 请求，命中 Range 时返回 **206**，否则 200），MinIO 模式下则是
    **307 重定向到预签名 URL**。

    ⚠️ 因此这个端点的响应**三种形态**（200 流 / 206 部分内容 / 307 重定向），
    `response_class` 只能把 200/206 的 media type 声明正确，
    重定向那一支在 OpenAPI 里无法表达（FastAPI 不会为它自动生成 307 条目）。
    详见 `frontend/docs/openapi-client.md` 的对应小节。
    """

    media_type = "video/mp4"
