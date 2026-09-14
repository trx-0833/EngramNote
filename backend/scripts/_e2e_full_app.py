"""全链路 E2E 后端进程启动器（uvicorn 的 `--factory` 目标）

为什么要单独一个启动器，而不是 `uvicorn app.main:app` 多加几个环境变量：

1. `_e2e_full_bootstrap.apply()` 必须在 `app.tasks.celery_app` 被 import **之前**
   生效（broker 目录在它的模块层就被写进 Celery 配置），因此本模块先引导、
   再 import 应用；
2. `app/api/upload.py` 把暂存目录绑成了模块属性，需要在 import 之后回填一次
   （`patch_upload_module`）。

`uvicorn --factory` 的语义正好是"import 我给的路径、调用它拿 app"，
所以这里用一个函数返回 FastAPI 实例，而不是在模块层 import。

未设置 `ENGRAMNOTE_E2E_BROKER_DIR` 时引导是空操作，本模块与生产启动路径无关
（生产用 `uvicorn app.main:app`，不会走到这里）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _e2e_full_bootstrap  # noqa: E402  （与启动器同目录；sys.path 已含 backend/scripts）

_e2e_full_bootstrap.apply()


def create_app():
    """给 `uvicorn --factory` 用的应用工厂"""
    from app.main import app

    _e2e_full_bootstrap.patch_upload_module()
    return app


__all__ = ["create_app"]
