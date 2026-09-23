"""单一版本来源（阶段 7.3）

## 为什么单独一个模块

版本号此前硬编码在**两处**、互不关联：`backend/app/main.py` 的
`FastAPI(version="0.1.0")` 与 `frontend/package.json` 的 `"version"`。
于是"我装的是哪个版本"这个问题没有唯一答案 —— 后端日志、`/openapi.json`
的 info.version、前端包元数据可能各说各话。

现在后端只有这一处常量；前端包的版本由 `backend/tests/test_version_single_source.py`
断言与它一致（两边都是 0.1.0，改一处忘另一处会红）。

## 为什么不读 `importlib.metadata`

`pip install -e .` 之后确实可以读已安装发行版的版本，但那有两个问题：
  1. **未安装时读不到**（直接 `python -m uvicorn app.main:app` 的用法很常见，
     README 的手动启动就是这种）—— 版本会退化成 "unknown"；
  2. 会引入"运行环境决定应用自述版本"的耦合，而版本是**代码的属性**。

因此常量写在这里，并由测试保证它与打包元数据（`pyproject.toml`）一致。
"""

from __future__ import annotations

#: 应用版本（语义化版本；打 tag 时请同步 `pyproject.toml` 与 `frontend/package.json`）
__version__ = "0.1.0"

#: 版本号的三处落点（供测试与脚本引用，避免在断言里再抄一遍字面量）
VERSION_LOCATIONS = (
    "backend/app/version.py",
    "pyproject.toml",
    "frontend/package.json",
)
