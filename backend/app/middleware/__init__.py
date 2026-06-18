"""
中间件模块

从 error_handler 模块导出 ErrorHandlerMiddleware，保持向后兼容。
"""

from .error_handler import ErrorHandlerMiddleware

__all__ = ["ErrorHandlerMiddleware"]
