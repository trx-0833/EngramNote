"""
Celery 应用配置模块

本模块负责创建和配置 Celery 异步任务应用实例，
用于处理文档转换等耗时操作，避免阻塞 HTTP 请求。

主要职责：
- 创建 Celery 应用实例
- 配置序列化方式、时区、任务追踪等参数
- 配置文件系统 broker 的数据目录
- 自动发现任务模块

设计决策：
- 默认使用文件系统作为 broker 和结果后端，实现零外部依赖启动
- 使用 JSON 序列化，确保任务参数和结果可读且跨语言兼容
- task_acks_late=True：任务完成后才确认，避免任务执行中 worker 崩溃导致任务丢失
- worker_prefetch_multiplier=1：每次只预取一个任务，避免长任务阻塞短任务
- 时区设为 Asia/Shanghai，但启用 UTC 以确保时间戳一致性
"""

from celery import Celery
from pathlib import Path

from ..config import get_settings

settings = get_settings()

# 文件系统 broker 的消息目录，确保启动前已创建
# 注意：Windows 上 kombu 文件系统传输的 data_folder_in 和 data_folder_out
# 必须指向同一目录，否则跨目录文件移动操作会失败（os.rename 不支持跨盘符）
_broker_dir = Path(settings.get_storage_dir().parent / "celery" / "broker")
_broker_dir.mkdir(parents=True, exist_ok=True)

# 创建 Celery 应用实例
# broker: 消息队列，用于分发任务
# backend: 结果存储，用于查询任务状态和结果
celery_app = Celery(
    "engramnote",
    broker=settings.get_celery_broker_url(),
    backend=settings.get_celery_result_backend(),
)

celery_app.conf.update(
    # 序列化配置：统一使用 JSON，确保任务参数和结果可读且跨语言兼容
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # 时区配置：内部使用 UTC，显示使用上海时区
    timezone="Asia/Shanghai",
    enable_utc=True,
    # 任务追踪：记录任务开始时间，便于监控
    task_track_started=True,
    # 延迟确认：任务执行完成后才确认（而非接收后确认），
    # 防止 worker 崩溃时任务丢失
    task_acks_late=True,
    # 预取倍数：设为 1 表示每次只预取一个任务，
    # 避免长任务（如大文件转换）阻塞后续短任务
    worker_prefetch_multiplier=1,
    # 结果过期时间：1小时后自动清理结果文件，避免磁盘堆积
    result_expires=3600,
    # 文件系统 broker 配置：指定消息的输入/输出目录
    # Windows 上 in/out 必须相同，否则 os.rename 跨目录失败
    broker_transport_options={
        "data_folder_in": str(_broker_dir),
        "data_folder_out": str(_broker_dir),
    },
)

# 直接包含任务模块，注册所有 Celery 任务
# 注意：autodiscover_tasks 查找的是子包中的 tasks.py，
# 而我们的任务直接定义在 app.tasks.convert_tasks 中，
# 所以使用 include 参数显式导入
celery_app.conf.update(
    include=["app.tasks.convert_tasks", "app.tasks.clean_tasks", "app.tasks.understand_tasks"],
)
