"""对象存储快照的**调度与接线**测试（阶段 6.6 第三项，附录 AW）

能力测试在 `test_storage_snapshot.py`（29 条）。这里盯的是另一类失效：
**工具写好了、没人调用** —— 本项目已经因此踩过三次
（AF.10 的账本清理、AU.1 的限流规则、`audit_vault` 没有调度）。

因此这里要证明：

| 要证明的事 | 对应测试 |
|---|---|
| 任务真的挂在 Beat 上 | `test_scheduled_in_beat` |
| 任务名在 Celery 注册表里（Beat 按名查找，找不到会静默不跑） | `test_task_registered` |
| 周一早上三个任务的**顺序**符合设计（先备文件、再验备份、后查一致性） | `test_monday_chain_order` |
| 任务真的调用 `create_storage_snapshot` | `test_task_runs_snapshot` |
| 快照不完整是 error 级，且不抛异常 | `test_incomplete_is_logged_as_error` |
| 任务内部报错不抛异常（不阻塞 Beat） | `test_failure_does_not_raise` |
| 默认路径指向**真实**的 storage 与 `_backup`（写错就静默备到别处） | `test_default_roots` |
| 保留份数由配置控制 | `test_retention_follows_settings` |
"""

import logging

from app.services import storage_snapshot as ss


class TestWiring:
    def test_scheduled_in_beat(self):
        from app.tasks.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        assert "storage-snapshot-weekly" in schedule, "对象存储快照没有挂到 Beat 上"
        assert (schedule["storage-snapshot-weekly"]["task"]
                == "app.tasks.maintenance_tasks.storage_snapshot")

    def test_task_registered(self):
        from app.tasks import maintenance_tasks as mt
        from app.tasks.celery_app import celery_app

        assert hasattr(mt, "storage_snapshot_task")
        assert "app.tasks.maintenance_tasks.storage_snapshot" in celery_app.tasks

    def test_monday_chain_order(self):
        """★ 周一早上的顺序是有理由的，不能被打乱

            04:45 快照文件   →  05:00 演练备份  →  05:30 审计线上一致性

        先留下文件的第二份，再确认数据库备份可用，最后核对线上库盘是否一致。
        三者是**不同任务**（不是同一件事的三个名字）。
        """
        from app.tasks.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        keys = ("storage-snapshot-weekly", "restore-drill-weekly", "vault-audit-weekly")
        for key in keys:
            assert key in schedule, f"{key} 不在 Beat 调度里"

        def when(key):
            cron = schedule[key]["schedule"]
            # celery 的 crontab 把 hour/minute 存成集合（可写 "1,3"），取唯一值
            return (min(cron.hour), min(cron.minute), set(cron.day_of_week))

        assert when("storage-snapshot-weekly") == (4, 45, {1})
        assert when("restore-drill-weekly") == (5, 0, {1})
        assert when("vault-audit-weekly") == (5, 30, {1})
        tasks = {schedule[k]["task"] for k in keys}
        assert len(tasks) == 3, "三个任务必须是不同的实现"

    def test_default_roots(self):
        """默认指向真实目录：写错就会把文件备到没人知道的地方（静默失效）"""
        from app.config import PROJECT_ROOT
        from app.services import backup_service

        assert ss.STORAGE_ROOT == PROJECT_ROOT / "data" / "storage"
        # 与数据库快照**同一个备份根**：否则 `_backup/` 里只有一半东西
        assert ss.BACKUP_ROOT == backup_service.BACKUP_ROOT

    def test_retention_follows_settings(self):
        from app.config import get_settings

        assert get_settings().storage_backup_keep == 3
        assert ss.default_storage_retention() == 3


class TestTaskBehaviour:
    """⚠️ 全部是**同步**用例

    Celery 任务内部用 `task_loop()` 自建事件循环；若在 pytest-asyncio 的事件
    循环里调用，`run_until_complete` 会报 "Cannot run the event loop while
    another loop is running" —— 真实 worker 没有外层 loop，因此按真实情形写。
    """

    def test_task_runs_snapshot(self, monkeypatch):
        """任务真的调用 `create_storage_snapshot`，并把结果原样报出来

        ⚠️ 必须替换掉真实实现：缺省参数会去复制**真实**的 `data/storage/`
        并写进**真实** `_backup/`（约 82 MB）。测试不该有这种副作用。
        """
        from app.tasks import maintenance_tasks as mt

        calls = []

        def spy(**kwargs):
            calls.append(kwargs)
            return {"ok": True, "snapshot": "X", "files": 3, "bytes": 30,
                    "unstable": [], "failed": [], "pruned": [], "reason": None}

        monkeypatch.setattr(ss, "create_storage_snapshot", spy)
        result = mt.storage_snapshot_task()

        assert len(calls) == 1, "任务没有调用 create_storage_snapshot"
        assert calls[0] == {}, "任务不应改写缺省参数（须由配置驱动保留策略）"
        assert result["ok"] is True and result["files"] == 3

    def test_incomplete_is_logged_as_error(self, monkeypatch, caplog):
        """★ 快照不完整（有文件没复制成功）必须是 error 级 —— 不能静默降级"""
        from app.tasks import maintenance_tasks as mt

        monkeypatch.setattr(ss, "create_storage_snapshot", lambda **kw: {
            "ok": False, "snapshot": "X", "files": 1, "bytes": 10,
            "unstable": [{"path": "u1/a.md", "error": "哈希不一致"}],
            "failed": [], "pruned": [], "reason": None,
        })
        with caplog.at_level(logging.ERROR):
            result = mt.storage_snapshot_task()

        assert result["ok"] is False
        assert "存储快照不完整" in caplog.text

    def test_empty_storage_is_not_an_error(self, monkeypatch, caplog):
        """全新部署（还没有任何上传）不应刷 error 日志"""
        from app.tasks import maintenance_tasks as mt

        monkeypatch.setattr(ss, "create_storage_snapshot", lambda **kw: {
            "ok": True, "snapshot": None, "files": 0, "bytes": 0,
            "unstable": [], "failed": [], "pruned": [],
            "reason": "存储目录不存在，跳过",
        })
        with caplog.at_level(logging.ERROR):
            result = mt.storage_snapshot_task()

        assert result["ok"] is True
        assert "存储快照不完整" not in caplog.text

    def test_failure_does_not_raise(self, monkeypatch):
        """任务内部报错时返回错误摘要而不是抛异常（否则 Beat 会堆积失败状态）"""
        from app.tasks import maintenance_tasks as mt

        def boom(**kwargs):
            raise RuntimeError("备份盘不可写")

        monkeypatch.setattr(ss, "create_storage_snapshot", boom)
        result = mt.storage_snapshot_task()

        assert result["ok"] is False
        assert "备份盘不可写" in result["error"]
