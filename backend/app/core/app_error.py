"""
应用层错误契约模块
================

定义统一的业务异常类型 AppError 与常用错误码常量，作为后端错误响应的契约设施。

AppError 由 middleware/error_handler.py 统一透传：中间件捕获后把
code -> error_code、message -> detail、http_status -> HTTP 状态码，
保证业务错误以统一 JSON 结构 {"detail", "error_code", "request_id"} 返回。

设计决策：
- AppError 直接继承 Exception（不继承 ValueError / HTTPException）：
  业务代码抛出后不被 API 层的 `except ValueError` 兜底吞掉，也不会被
  FastAPI ExceptionMiddleware 当作 HTTPException 处理，而是逐层上抛，
  最终由 ErrorHandlerMiddleware 捕获并转换为统一错误响应。
- data 为可选附带数据（默认 None），供错误响应需要携带结构化信息时使用，
  由中间件在响应体中以 data 字段透传（仅当非 None）。
- 新增错误码常量时在下方常量区追加，保持 code 全大写、语义化命名。
"""

from typing import Any, Optional


# ---- 常用错误码常量 ----

# 版本缺失（版本记录不存在或版本内容文件已丢失）
VERSION_NOT_FOUND = "VERSION_NOT_FOUND"


class AppError(Exception):
    """
    业务异常：携带错误码、人读消息、HTTP 状态码与可选附带数据

    Attributes:
        code: 稳定错误码（如 VERSION_NOT_FOUND），前端据此做程序化判断
        message: 面向用户的可读错误详情（对应响应体 detail）
        http_status: 对应的 HTTP 状态码
        data: 可选附带数据，透传到响应体 data 字段（默认 None）
    """

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 500,
        data: Optional[Any] = None,
    ) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        self.data = data
        super().__init__(message)

    def __str__(self) -> str:
        return self.message