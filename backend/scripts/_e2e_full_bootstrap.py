"""全链路 E2E（Playwright `e2e-full`）的**进程内隔离引导**

## 为什么需要这个文件（而不是"多传几个环境变量就行"）

`app/config.py` 的 `Settings` 能覆盖数据库、存储、日志，但有两处
**在 import 时就被算成常量**、且没有任何环境变量入口：

1. `Settings.get_celery_broker_dir()` / `get_celery_result_dir()` 返回的是
   `DATA_DIR / "celery" / ...`，其中 `DATA_DIR = backend/data`（硬编码）。
   `app/tasks/celery_app.py` 在**模块层**调用它们，把结果写进 Celery 的
   `broker_transport_options` 与 `backend`。于是一个用 `DATABASE_URL` 指向
   临时库的 E2E 后端，仍会把任务投递到 **真实库那套 broker 目录**。
   若此时人类手上的 dev worker 正在跑（`start.bat` 就会起一个），那个 worker
   会**抢走** E2E 的任务，并用**真实数据库**去执行它 —— 一次 E2E 就能污染
   生产知识库。文件系统 broker 是"谁先 rename 谁拿到"，没有任何所有权标记。
2. `app/api/upload.py` 把 `TMP_UPLOAD_DIR` 以 `from ..config import TMP_UPLOAD_DIR`
   的形式绑成**模块属性**（两阶段上传的暂存目录）。改 `config` 模块属性改不到它。

`DATABASE_URL` / `STORAGE_DIR` / `LOG_DIR` 这类有环境变量入口的项**不在这里处理**，
由 `_e2e_full_runner.py` 直接写进子进程环境（那是本项目已有的、经过测试的机制）。

## 为什么必须**在任何 `app.*` import 之前**调用

两处的值都在 `celery_app.py` / `upload.py` 的模块层求值：一旦 `app.tasks.celery_app`
被 import 过，broker 目录就已经进了 Celery 配置，再改函数也不生效。
因此本模块只允许作为"第一件事"被调用（runner 与 worker launcher 都是这么用的）。

## 只认显式开关

`ENGRAMNOTE_E2E_BROKER_DIR` 未设置时本模块**什么都不做**并返回 False。
这样它绝不可能在生产启动路径上被误用：普通 `uvicorn app.main:app` 永远不会经过这里。

## 明确**不**改的东西

`DATA_DIR` 本身不动：`services/embedding_service.py` 从 `DATA_DIR/models` 读
已缓存的嵌入模型（bge-m3，~2.2GB），把 DATA_DIR 挪走会让清洗阶段退化成
无模型兜底去重、问答退化成 BM25-only —— 那测的就不是真实链路了。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("engramnote.e2e_full")

#: 隔离开关：E2E 的 broker/结果/暂存目录根（由 runner 写进子进程环境）
ENV_BROKER_DIR = "ENGRAMNOTE_E2E_BROKER_DIR"

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _ensure_app_importable() -> None:
    """把 backend/ 放到 sys.path 首位（本文件位于 backend/scripts/）"""
    root = str(BACKEND_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def apply(force: bool = False) -> bool:
    """应用隔离；返回是否真的改了什么

    Args:
        force: True 时即使没设置开关也应用（仅供探针/调试使用）
    """
    raw = os.environ.get(ENV_BROKER_DIR, "").strip()
    if not raw and not force:
        return False

    root = Path(raw) if raw else Path(os.environ.get("TEMP", ".")) / "engramnote-e2e-broker"

    _ensure_app_importable()
    from app import config as config_mod

    # 1) Celery broker / 结果目录：改成隔离根下的子目录
    #
    # ⚠️ 只能用 `object.__setattr__`：`Settings` 是 pydantic-settings 的模型，
    # 它的 `__setattr__` 会拒绝"不是字段"的名字：
    #     ValueError: "Settings" object has no field "get_celery_broker_dir"
    # （实测踩到过）。绕过 `__setattr__` 挂实例属性是有效的：属性查找先看
    # `__dict__`，而函数对象放在实例 `__dict__` 里不会被绑定成方法，
    # `get_celery_broker_dir()` 因此原样调用我们给的 lambda。
    settings = config_mod.get_settings()
    broker_dir = root / "broker"
    result_dir = root / "results"
    broker_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    object.__setattr__(settings, "get_celery_broker_dir", lambda: broker_dir)
    object.__setattr__(settings, "get_celery_result_dir", lambda: result_dir)

    # 2) 两阶段上传的暂存目录：config 属性 + upload 模块里已经绑定的那个名字
    tmp_upload = root / "tmp" / "upload"
    tmp_upload.mkdir(parents=True, exist_ok=True)
    config_mod.TMP_UPLOAD_DIR = tmp_upload

    # 3) 开发模式下自动生成的 JWT 密钥落盘位置（本仓库 .env 已配置密钥，
    #    这里只是不让任何分支有机会往 backend/data 写文件）
    config_mod.JWT_SECRET_FILE = root / ".jwt-secret"

    # 4) 日志目录也挪走：`LOG_DIR` 环境变量本身就是本项目的正式入口，
    #    这里只是兜住"子进程环境没带上它"的情况（runner 一定会带）
    os.environ.setdefault("LOG_DIR", str(root / "logs"))

    logger.info(
        "E2E 隔离引导已生效 | broker=%s | results=%s | tmp_upload=%s",
        broker_dir, result_dir, tmp_upload,
    )
    return True


def patch_upload_module() -> bool:
    """把 `app.api.upload` 里已绑定的 `TMP_UPLOAD_DIR` 指向隔离目录

    必须在 `app.api.upload` **被 import 之后**调用（CLI 场景下 uvicorn 才 import 它），
    所以与 `apply()` 分开：`apply()` 改 config 常量，本函数改模块属性。
    未设置开关时是空操作。
    """
    if not os.environ.get(ENV_BROKER_DIR, "").strip():
        return False

    from app import config as config_mod

    target = config_mod.TMP_UPLOAD_DIR
    module = sys.modules.get("app.api.upload")
    if module is None:
        import app.api.upload as module  # type: ignore[no-redef]

    if getattr(module, "TMP_UPLOAD_DIR", None) != target:
        module.TMP_UPLOAD_DIR = target  # type: ignore[attr-defined]
        logger.info("E2E 隔离引导：upload.TMP_UPLOAD_DIR -> %s", target)
    return True


__all__ = ["apply", "patch_upload_module", "ENV_BROKER_DIR"]
