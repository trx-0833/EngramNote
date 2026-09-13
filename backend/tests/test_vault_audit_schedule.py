"""存储审计的调度测试（阶段 6.6 的另一半：库 ↔ 磁盘）

## 这份测试要证明什么

`audit_vault` 本身已有 14 条测试（`test_vault_audit.py`），能力是够的。
缺的从来不是能力，而是**有人调用它**：它此前只被 `scripts/verify_vault.py`
手工调用过，没有任何调度 —— 于是运行期等于不存在。

因此这里盯的是"接上"这件事：

| 要证明的事 | 对应测试 |
|---|---|
| 任务真的挂在 Beat 上（周频） | `test_scheduled_in_beat` |
| 任务名在 Celery 注册表里（Beat 按名字查找，找不到会静默不跑） | `test_task_registered` |
| 任务真的会调用 `audit_vault` 并把结果报出来 | `test_task_runs_audit` |
| 审计发现不一致时是 error 级（不是被忽略的 warning） | `test_mismatch_is_logged_as_error` |
| 审计失败不抛异常（不阻塞 Beat） | `test_failure_does_not_raise` |
| 与恢复演练**互补而非重复**：演练只查数据库，审计查磁盘 | `test_complements_restore_drill` |
"""

import logging


class TestScheduledInBeat:
    def test_scheduled_in_beat(self):
        from app.tasks.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        assert "vault-audit-weekly" in schedule, "存储审计没有挂到 Beat 上"
        assert schedule["vault-audit-weekly"]["task"] == "app.tasks.maintenance_tasks.vault_audit"

    def test_task_registered(self):
        from app.tasks import maintenance_tasks as mt
        from app.tasks.celery_app import celery_app

        assert hasattr(mt, "vault_audit_task")
        assert "app.tasks.maintenance_tasks.vault_audit" in celery_app.tasks

    def test_complements_restore_drill(self):
        """★ 两个任务的分工必须都在调度里：一个查库、一个查磁盘

        恢复演练只看数据库；数据实际分布在 db + storage 两处。
        只恢复库、丢了文件，用户看到的是"笔记都在但每一篇都打不开" ——
        与丢数据没有区别。
        """
        from app.tasks.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        assert "restore-drill-weekly" in schedule
        assert "vault-audit-weekly" in schedule
        # 两者是不同任务（不是同一件事的两个名字）
        assert (schedule["restore-drill-weekly"]["task"]
                != schedule["vault-audit-weekly"]["task"])


class TestTaskBehaviour:
    """⚠️ 全部是**同步**用例

    Celery 任务是同步函数，内部用 `task_loop()` 自建事件循环。
    若在 pytest-asyncio 提供的事件循环里调用它，`run_until_complete` 会报
    "Cannot run the event loop while another loop is running" ——
    任务在真实 worker 里不会遇到这个情形（那里没有外层 loop），
    因此测试必须按真实情形写：**同步调用**。
    """

    @staticmethod
    def _run_in_fresh_loop(coro_factory):
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro_factory())
        finally:
            loop.close()

    def test_task_runs_audit(self, test_db, monkeypatch):
        """任务真的调用 `audit_vault`，并把结果原样报出来"""
        from app.services import vault_audit_service
        from app.tasks import maintenance_tasks as mt

        called = {"n": 0}
        real = vault_audit_service.audit_vault

        async def spy(db, **kwargs):
            called["n"] += 1
            return await real(db, **kwargs)

        monkeypatch.setattr(vault_audit_service, "audit_vault", spy)
        result = mt.vault_audit_task()

        assert called["n"] == 1, "任务没有调用 audit_vault"
        assert "ok" in result and "counts" in result

    def test_empty_database_passes(self, test_db, monkeypatch):
        """空库 + 空存储 → 通过（无引用即无不一致）

        ⚠️ 必须**隔离真实存储目录**：`test_db` 只换数据库、不换 `data/storage/`。
        不隔离的话，真实目录里的文件在空库视角下全是"孤儿"，用例会随环境
        时红时绿（本轮实测：77 个孤儿）。这里把孤儿扫描的前缀置空 ——
        本用例要验的是"任务跑通并返回结构化结果"。
        """
        from app.services import vault_audit_service
        from app.tasks import maintenance_tasks as mt

        monkeypatch.setattr(vault_audit_service, "_orphan_scan_prefixes", lambda user_id: [])
        result = mt.vault_audit_task()
        assert result["ok"] is True, result
        assert result["notes_scanned"] == 0
        assert result["db_objects"] == 0

    def test_mismatch_is_logged_as_error(self, test_db, caplog):
        """★ 不一致必须是 error 级：它意味着"界面能看到、但打不开"的内容已存在"""
        import uuid

        from app.models.note import Note, NoteStatus, SourceType
        from app.models.user import User
        from app.tasks import maintenance_tasks as mt

        async def _seed():
            uid = str(uuid.uuid4())
            async with test_db() as db:
                db.add(User(id=uid, email=f"{uid[:8]}@e.com", username=f"u{uid[:8]}",
                            hashed_password="x", is_active=True))
                await db.flush()
                # 指向一个**不存在**的文件
                db.add(Note(
                    id=str(uuid.uuid4()), user_id=uid, title="缺文件的笔记",
                    source_type=SourceType.pdf, status=NoteStatus.cleaned, file_size=1,
                    original_file_path=f"{uid}/missing.pdf",
                    original_md_path=f"{uid}/missing.md",
                ))
                await db.commit()

        self._run_in_fresh_loop(_seed)

        with caplog.at_level(logging.ERROR):
            result = mt.vault_audit_task()

        assert result["ok"] is False
        assert result["counts"].get("missing_file", 0) >= 1
        assert "存储审计发现不一致" in caplog.text

    def test_failure_does_not_raise(self, monkeypatch):
        """审计内部报错时任务返回错误摘要而不是抛异常（否则 Beat 会堆积失败）"""
        from app.services import vault_audit_service
        from app.tasks import maintenance_tasks as mt

        async def boom(db, **kwargs):
            raise RuntimeError("磁盘不可读")

        monkeypatch.setattr(vault_audit_service, "audit_vault", boom)
        result = mt.vault_audit_task()
        assert result["ok"] is False
        assert "磁盘不可读" in result["error"]
