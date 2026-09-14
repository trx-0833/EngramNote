"""上传模块 Pydantic Schema

阶段 5.1：两阶段上传的 **prepare** 阶段此前没有声明 `response_model=`，
生成的前端类型是 `unknown`。本模块补齐它。

⚠️ 这里**只有** prepare 一个模型，因为上传模块的其余端点早就有模型了：

- `POST /api/upload` / `POST /api/upload/commit` → `schemas.note.NoteResponse`
- `GET  /api/upload/{note_id}/status` / `POST /api/upload/{note_id}/retry`
  → `schemas.note.NoteStatusResponse`

（`uploadFile` 与 `commitUpload` 都走 multipart，OpenAPI 只能表达
"这些是 Form 字段"，表达不了"`project_ids` 是 JSON 编码的数组字符串"。
那属于另一个问题，见 `frontend/docs/openapi-client.md` 的风险清单。）
"""

from typing import Optional

from pydantic import BaseModel, Field

from ..models.note import SourceType


class PrepareUploadResponse(BaseModel):
    """两阶段上传阶段 1（`POST /api/upload/prepare`）的响应

    出处：`api/upload.py:682-687`：

        {"temp_id": temp_id, "filename": filename,
         "source_type": source_type.value, "page_count": page_count}

    `page_count` 的语义**不是"未知"而是"该格式没有页的概念"**：
    实现里只有 `source_type == SourceType.pdf` 才去解析页数
    （`upload.py:651-655`），其余格式一律 `None`（代码注释写的是
    "其他格式返回 null（本轮不支持分页）"）。前端 `PreparedUpload.page_count`
    也写成 `number | null` —— 两边一致。

    `temp_id` 是服务端生成的 UUID，落盘目录名就是它，
    commit 阶段会**先做 UUID 格式校验**再拼路径（防路径穿越）。
    """

    temp_id: str = Field(description="临时上传标识（UUID），commit 阶段原样回传")
    filename: str = Field(description="服务端保存时使用的文件名（已剥离路径分隔符）")
    source_type: SourceType = Field(description="按扩展名判定的来源类型")
    page_count: Optional[int] = Field(
        default=None,
        description="PDF 的页数；非 PDF 格式恒为 null（该格式没有页的概念）",
    )
