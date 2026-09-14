"""全链路 E2E 的 Celery worker 启动器

## 为什么必须在 Python 里启动 worker，而不是 `celery -A app.tasks.celery_app worker`

`app/tasks/celery_app.py` 在**模块层**就把 broker / 结果后端定死了：

    broker=settings.get_celery_broker_url()        # -> DATA_DIR/celery/broker
    backend=settings.get_celery_result_backend()   # -> DATA_DIR/celery/results

`DATA_DIR` 是 `backend/data`（硬编码，无环境变量入口）。直接起 worker 会让它
监听**真实数据目录**的 broker —— 而人类的 `start.bat` 也会起一个同样的 worker。
文件系统 broker 没有所有权标记（谁先 rename 谁拿到消息），于是 E2E 的任务可能
被生产 worker 取走、用**真实数据库**执行。这是必须避免的。

因此这里：

    1. 先 import `_e2e_full_bootstrap` 并应用隔离（broker/结果目录换到临时根）；
    2. 帮 Celery 把任务模块 import 进来（`@celery_app.task` 注册到**同一个**
       app 实例上 —— 换一个 Celery 实例不会注册这些任务，所以只能改配置、
       不能换 app）；
    3. 再把这**同一个** app 的 broker/backend 改到隔离目录；
    4. 用 `celery.__main__`（与 `celery` 命令行**同一个入口**）启动 worker。

第 3 步是关键：改的是 app 的配置，不是 app 本身，因此任务名、
`task_always_eager` 之类语义全部保持不变。

未设置 `ENGRAMNOTE_E2E_BROKER_DIR` 时本脚本退化为"原样启动一个普通 worker"，
不会被生产路径使用（生产用的是 `celery -A app.tasks.celery_app`）。
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
for entry in (str(BACKEND_ROOT), str(SCRIPTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import _e2e_full_bootstrap  # noqa: E402

_e2e_full_bootstrap.apply()

from app.tasks.celery_app import celery_app  # noqa: E402

# 任务模块必须"现在"被 import：`celery_app.conf.include` 是**懒加载**列表，
# worker 启动时才生效；而我们要在它之前把 broker 改掉，所以显式先 import 一遍。
# 这里的 import 同时完成 `@celery_app.task` 的注册（app 是同一个实例）。
import app.tasks.clean_tasks  # noqa: E402,F401
import app.tasks.convert_tasks  # noqa: E402,F401
import app.tasks.embedding_tasks  # noqa: E402,F401
import app.tasks.maintenance_tasks  # noqa: E402,F401
import app.tasks.reminder_tasks  # noqa: E402,F401
import app.tasks.understand_tasks  # noqa: E402,F401
from app.config import get_settings  # noqa: E402

settings = get_settings()
isolated = bool(os.environ.get(_e2e_full_bootstrap.ENV_BROKER_DIR, "").strip())

if isolated:
    broker_url = settings.get_celery_broker_url()
    result_backend = settings.get_celery_result_backend()
    celery_app.conf.broker_url = broker_url
    celery_app.conf.result_backend = result_backend
    celery_app.conf.broker_transport_options = {
        "data_folder_in": str(settings.get_celery_broker_dir()),
        "data_folder_out": str(settings.get_celery_broker_dir()),
    }
    print(
        f"[e2e-full worker] 隔离生效 | broker={broker_url} | "
        f"results={result_backend} | data_folder={settings.get_celery_broker_dir()}",
        flush=True,
    )

# 走与 `celery` 命令行完全相同的入口（参数来自 E2E_WORKER_ARGS，空格分隔）
argv = ["celery"] + (os.environ.get("E2E_WORKER_ARGS", "worker --loglevel=info --pool=solo").split())
sys.argv = argv
runpy.run_module("celery", run_name="__main__", alter_sys=True)
