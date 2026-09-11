"""
任务进度与僵尸自愈测试（阶段 1′ 1.7 / 1.8）

## 为什么这些测试重要

文件系统 broker 没有 visibility timeout，worker 崩溃后任务**永久**停在
未确认状态，笔记则永久卡在 converting / cleaning / learning。
这是"用户看到一个永远转圈的界面，既没有失败提示也没有重试入口"的直接原因。

自愈逻辑的价值全在**判定边界**上，因此这里重点测边界而不是happy path：

1. 心跳超时的真僵尸 → 标记 stale + 释放笔记
2. **心跳新鲜的任务 → 绝不能被误杀**（那是正在正常干活的任务）
3. 笔记已被后续任务推进 → 只标记任务，**不改笔记状态**
   （否则"已清洗完成的笔记"会被僵尸扫描标成失败）
4. 用户主动取消 → cancelled 而非 failed（主动停止不是故障）
5. 任务归属校验 → 不能用 task_id 探测他人任务
"""

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.note import Note, NoteStatus, SourceType
from app.models.task_run import ACTIVE_TASK_STATUSES, TaskRun, TaskStatus
from app.models.user import User
from app.services import task_run_service

CONVERT_TASK = "app.tasks.convert_tasks.convert_document_task"


async def _make_user(session_factory, email: str = "task@example.com") -> str:
    uid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(User(
            id=uid, email=email, username=f"u{uid[:8]}",
            hashed_password="x", is_active=True,
        ))
        await db.commit()
    return uid


async def _make_note(
    session_factory, user_id: str, status: NoteStatus = NoteStatus.converting,
) -> str:
    nid = str(uuid.uuid4())
    async with session_factory() as db:
        db.add(Note(
            id=nid, user_id=user_id, title="t",
            source_type=SourceType.pdf, status=status,
        ))
        await db.commit()
    return nid


async def _add_run(
    session_factory,
    *,
    task_id: str | None = None,
    task_name: str = CONVERT_TASK,
    note_id: str | None = None,
    user_id: str | None = None,
    status: TaskStatus = TaskStatus.running,
    heartbeat_ago_seconds: int = 0,
    attempt: int = 1,
    max_attempts: int = 3,
) -> str:
    """插入一条任务记录；heartbeat_ago_seconds 控制心跳有多旧"""
    tid = task_id or f"task-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    async with session_factory() as db:
        db.add(TaskRun(
            task_id=tid,
            task_name=task_name,
            note_id=note_id,
            user_id=user_id,
            status=status,
            progress=0.3,
            stage="正在解析文档",
            attempt=attempt,
            max_attempts=max_attempts,
            heartbeat_at=now - timedelta(seconds=heartbeat_ago_seconds),
            started_at=now - timedelta(seconds=heartbeat_ago_seconds),
        ))
        await db.commit()
    return tid


async def _note_status(session_factory, note_id: str) -> NoteStatus:
    async with session_factory() as db:
        note = (await db.execute(
            select(Note).where(Note.id == note_id)
        )).scalars().first()
        return note.status


async def _run_status(session_factory, task_id: str) -> TaskStatus:
    async with session_factory() as db:
        run = (await db.execute(
            select(TaskRun).where(TaskRun.task_id == task_id)
        )).scalars().first()
        return run.status


# ---------------------------------------------------------------------------
# 1. 进度写入与读取
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestProgressReporting:

    async def test_ensure_creates_then_reuses_row(self, test_db):
        """同一 task_id 重复调用应复用记录并递增 attempt（重试语义）"""
        uid = await _make_user(test_db)
        tid = f"task-{uuid.uuid4().hex[:12]}"

        first = await task_run_service.ensure_task_run(tid, CONVERT_TASK, note_id=None, user_id=uid)
        assert first is not None
        assert first.attempt == 1
        assert first.status == TaskStatus.running

        second = await task_run_service.ensure_task_run(tid, CONVERT_TASK, note_id=None, user_id=uid)
        assert second is not None
        assert second.attempt == 2, "重试必须递增 attempt，而不是新增行"

        async with test_db() as db:
            total = len((await db.execute(
                select(TaskRun).where(TaskRun.task_id == tid)
            )).scalars().all())
        assert total == 1, "同一 task_id 只应有一行"

    async def test_progress_is_clamped(self, test_db):
        """越界进度被夹到 [0,1]，避免前端进度条画到容器外"""
        tid = await _add_run(test_db)

        await task_run_service.report_progress(tid, 1.7, stage="超额")
        async with test_db() as db:
            run = (await db.execute(select(TaskRun).where(TaskRun.task_id == tid))).scalars().first()
        assert run.progress == 1.0

        await task_run_service.report_progress(tid, -0.5, stage="负数")
        async with test_db() as db:
            run = (await db.execute(select(TaskRun).where(TaskRun.task_id == tid))).scalars().first()
        assert run.progress == 0.0

    async def test_terminal_task_ignores_late_progress(self, test_db):
        """已完成的任务不该被迟到的进度上报改回 running"""
        tid = await _add_run(test_db)
        await task_run_service.mark_succeeded(tid)

        await task_run_service.report_progress(tid, 0.4, stage="迟到的上报")

        async with test_db() as db:
            run = (await db.execute(select(TaskRun).where(TaskRun.task_id == tid))).scalars().first()
        assert run.status == TaskStatus.succeeded
        assert run.progress == 1.0, "成功任务的进度应保持 1.0"

    async def test_failure_error_is_truncated(self, test_db):
        """超长错误摘要被截断：数据库不吞整份堆栈（可能含用户内容）"""
        tid = await _add_run(test_db)
        await task_run_service.mark_failed(tid, "x" * 5000)

        async with test_db() as db:
            run = (await db.execute(select(TaskRun).where(TaskRun.task_id == tid))).scalars().first()
        assert run.status == TaskStatus.failed
        assert run.error is not None
        assert len(run.error) < 5000
        assert "已截断" in run.error


# ---------------------------------------------------------------------------
# 2. 僵尸自愈的判定边界
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestStaleReaping:

    async def test_stale_task_is_reaped_and_note_released(self, test_db):
        """心跳超时 + 笔记卡在 converting → 标记 stale 并把笔记改判失败"""
        uid = await _make_user(test_db)
        nid = await _make_note(test_db, uid, NoteStatus.converting)
        tid = await _add_run(test_db, note_id=nid, user_id=uid, heartbeat_ago_seconds=3600)

        result = await task_run_service.reap_stale_tasks(stale_after_seconds=900)

        assert result["reaped"] == 1
        assert result["notes_released"] == 1
        assert await _run_status(test_db, tid) == TaskStatus.stale
        assert await _note_status(test_db, nid) == NoteStatus.failed, (
            "被僵尸任务卡住的笔记必须被释放，否则用户永远看不到可重试的失败态"
        )

    async def test_fresh_heartbeat_is_never_reaped(self, test_db):
        """**关键负向测试**：心跳新鲜的任务绝不能被误杀

        这是自愈功能最危险的失效模式 —— 把正在正常工作、只是耗时较长的
        任务（大 PDF 转换、嵌入模型首次加载）判定为僵尸，会把用户的笔记
        标成失败，且任务其实还在写数据，状态互相打架。
        """
        uid = await _make_user(test_db)
        nid = await _make_note(test_db, uid, NoteStatus.converting)
        tid = await _add_run(test_db, note_id=nid, user_id=uid, heartbeat_ago_seconds=5)

        result = await task_run_service.reap_stale_tasks(stale_after_seconds=900)

        assert result["reaped"] == 0, "心跳新鲜的任务被误判为僵尸"
        assert await _run_status(test_db, tid) == TaskStatus.running
        assert await _note_status(test_db, nid) == NoteStatus.converting, (
            "正常进行中的笔记状态被改动"
        )

    async def test_advanced_note_status_is_not_rolled_back(self, test_db):
        """笔记已被后续任务推进 → 只标记任务，不动笔记

        真实场景：转换任务的心跳记录没刷新（worker 卡过），但笔记其实
        已经被清洗任务推进到 cleaned。此时若按"僵尸任务的笔记一律改失败"，
        会把一个已完成的笔记标成失败 —— 比不修更糟。
        """
        uid = await _make_user(test_db)
        nid = await _make_note(test_db, uid, NoteStatus.cleaned)
        tid = await _add_run(test_db, note_id=nid, user_id=uid, heartbeat_ago_seconds=3600)

        result = await task_run_service.reap_stale_tasks(stale_after_seconds=900)

        assert result["reaped"] == 1
        assert result["notes_released"] == 0, "笔记状态已被推进，不应被回滚"
        assert await _run_status(test_db, tid) == TaskStatus.stale
        assert await _note_status(test_db, nid) == NoteStatus.cleaned

    async def test_task_without_note_is_reaped_safely(self, test_db):
        """没有关联笔记的任务（如 Beat 任务）也能被清理，且不报错"""
        tid = await _add_run(
            test_db,
            task_name="app.tasks.reminder_tasks.send_daily_review_email",
            note_id=None,
            heartbeat_ago_seconds=3600,
        )
        result = await task_run_service.reap_stale_tasks(stale_after_seconds=900)
        assert result["reaped"] == 1
        assert await _run_status(test_db, tid) == TaskStatus.stale

    async def test_terminal_tasks_are_not_rescanned(self, test_db):
        """已成功的任务不会因为心跳旧而被改判为僵尸（幂等）"""
        tid = await _add_run(test_db, heartbeat_ago_seconds=99999, status=TaskStatus.succeeded)
        result = await task_run_service.reap_stale_tasks(stale_after_seconds=900)
        assert result["reaped"] == 0
        assert await _run_status(test_db, tid) == TaskStatus.succeeded

    async def test_reaping_is_idempotent(self, test_db):
        """重复扫描不会重复计数"""
        uid = await _make_user(test_db)
        nid = await _make_note(test_db, uid, NoteStatus.converting)
        await _add_run(test_db, note_id=nid, user_id=uid, heartbeat_ago_seconds=3600)

        first = await task_run_service.reap_stale_tasks(stale_after_seconds=900)
        second = await task_run_service.reap_stale_tasks(stale_after_seconds=900)

        assert first["reaped"] == 1
        assert second["reaped"] == 0, "第二次扫描不应再处理同一个任务"
        assert second["notes_released"] == 0

    async def test_null_heartbeat_counts_as_stale(self, test_db):
        """heartbeat_at 为 NULL（记录建好后从未心跳）也应按超时处理"""
        tid = f"task-{uuid.uuid4().hex[:12]}"
        async with test_db() as db:
            db.add(TaskRun(
                task_id=tid, task_name=CONVERT_TASK, status=TaskStatus.running,
                progress=0.0, attempt=1, max_attempts=3, heartbeat_at=None,
            ))
            await db.commit()

        result = await task_run_service.reap_stale_tasks(stale_after_seconds=900)
        assert result["reaped"] == 1
        assert await _run_status(test_db, tid) == TaskStatus.stale

    async def test_active_statuses_are_the_scan_scope(self):
        """扫描范围必须只含进行中状态；终态一旦混进来就会被反复改判"""
        assert TaskStatus.pending in ACTIVE_TASK_STATUSES
        assert TaskStatus.running in ACTIVE_TASK_STATUSES
        for terminal in (TaskStatus.succeeded, TaskStatus.failed,
                         TaskStatus.stale, TaskStatus.cancelled):
            assert terminal not in ACTIVE_TASK_STATUSES


# ---------------------------------------------------------------------------
# 3. 取消语义
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestCancellation:

    async def test_cancel_marks_cancelled_not_failed(self, test_db):
        """用户主动取消是 cancelled，不是 failed（主动停止不是故障）"""
        uid = await _make_user(test_db)
        tid = await _add_run(test_db, user_id=uid)

        async with test_db() as db:
            ok = await task_run_service.request_cancel(db, tid)
        assert ok is True
        assert await _run_status(test_db, tid) == TaskStatus.cancelled

    async def test_cancel_is_idempotent_on_terminal(self, test_db):
        """已结束的任务重复取消不报错，也不改写状态"""
        uid = await _make_user(test_db)
        tid = await _add_run(test_db, user_id=uid)
        await task_run_service.mark_succeeded(tid)

        async with test_db() as db:
            ok = await task_run_service.request_cancel(db, tid)
        assert ok is True
        assert await _run_status(test_db, tid) == TaskStatus.succeeded

    async def test_is_cancelled_visible_to_task(self, test_db):
        """任务内可查询取消标志（长任务据此在阶段边界主动退出）"""
        uid = await _make_user(test_db)
        tid = await _add_run(test_db, user_id=uid)

        assert await task_run_service.is_cancelled(tid) is False
        async with test_db() as db:
            await task_run_service.request_cancel(db, tid)
        assert await task_run_service.is_cancelled(tid) is True


# ---------------------------------------------------------------------------
# 4. HTTP 契约
# ---------------------------------------------------------------------------

class TestTaskAPI:
    """HTTP 契约测试

    每个用例用**不同的客户端 IP**：限流规则是「注册 5 次 / 60 秒 / IP」，
    而 rate limiter 的状态是进程级共享的。共用 IP 会让第 6 个用例开始
    收到 429，表现为"莫名其妙的顺序相关失败"（本轮实测踩到过）。
    """

    #: 每个用例自增，保证 IP 不重复
    _ip_seq = 0

    def _client(self) -> TestClient:
        from app.main import app

        type(self)._ip_seq += 1
        return TestClient(app, client=(f"198.51.100.{100 + type(self)._ip_seq}", 9201))

    def _auth(self, email: str = "taskapi@example.com") -> dict:
        """注册一个用户并返回认证头

        用户名/邮箱都加随机后缀：数据库在同一个测试的 fixture 生命周期内
        是共享的，固定文案会在多个用例间撞"该邮箱或用户名已被使用"。
        """
        suffix = uuid.uuid4().hex[:8]
        local, _, domain = email.partition("@")
        resp = self._client().post("/api/auth/register", json={
            "email": f"{local}+{suffix}@{domain}",
            # username 只允许 [a-zA-Z0-9]，不能直接截取 email 前缀（含 @ .）
            "username": re.sub(r"[^a-zA-Z0-9]", "", local) + suffix,
            "password": "TaskApiPass123!",
        })
        assert resp.status_code in (200, 201), resp.text
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    def test_requires_auth(self, test_db):
        """任务接口必须要求认证

        需要 test_db：否则本用例会把单例指向真实库，而真实库不该被测试读到
        （`test_db` fixture 的 setup/teardown 同时承担"重置单例"的职责，
        不接它的用例会继承上一个用例留下的状态 —— 本轮实测踩到过
        `no such table: task_runs` 打到真实库）。
        """
        resp = self._client().get("/api/tasks/does-not-exist")
        assert resp.status_code == 401

    def test_unknown_task_returns_404(self, test_db):
        headers = self._auth()
        resp = self._client().get("/api/tasks/does-not-exist", headers=headers)
        assert resp.status_code == 404

    def test_owner_can_read_progress(self, test_db):
        """任务的进度/阶段名/可重试标志能被前端读到"""
        import asyncio

        headers = self._auth("owner@example.com")
        me = self._client().get("/api/auth/me", headers=headers).json()
        uid = me["id"]

        tid = f"task-{uuid.uuid4().hex[:12]}"
        asyncio.get_event_loop_policy()
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(task_run_service.ensure_task_run(
                tid, CONVERT_TASK, user_id=uid,
            ))
            loop.run_until_complete(task_run_service.report_progress(
                tid, 0.42, stage="正在解析文档",
            ))
        finally:
            loop.close()

        resp = self._client().get(f"/api/tasks/{tid}", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["progress"] == 0.42
        assert body["stage"] == "正在解析文档"
        assert body["status"] == "running"
        assert body["retryable"] is False, "进行中的任务不可重试"

    def test_other_user_cannot_read_task(self, test_db):
        """**归属校验**：不能用 task_id 探测他人任务（IDOR）"""
        import asyncio

        owner_headers = self._auth("owner2@example.com")
        owner_id = self._client().get("/api/auth/me", headers=owner_headers).json()["id"]

        tid = f"task-{uuid.uuid4().hex[:12]}"
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(task_run_service.ensure_task_run(
                tid, CONVERT_TASK, user_id=owner_id,
            ))
        finally:
            loop.close()

        other_headers = self._auth("intruder@example.com")
        resp = self._client().get(f"/api/tasks/{tid}", headers=other_headers)
        assert resp.status_code == 404, "他人任务必须不可见（404 而非 403，不泄露存在性）"

    def test_retryable_flag_for_failed_task(self, test_db):
        """失败且未超尝试上限 → retryable=true（前端据此显示重试按钮）"""
        import asyncio

        headers = self._auth("retry@example.com")
        uid = self._client().get("/api/auth/me", headers=headers).json()["id"]

        tid = f"task-{uuid.uuid4().hex[:12]}"
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(task_run_service.ensure_task_run(
                tid, CONVERT_TASK, user_id=uid, max_attempts=3,
            ))
            loop.run_until_complete(task_run_service.mark_failed(tid, "转换失败：文件损坏"))
        finally:
            loop.close()

        body = self._client().get(f"/api/tasks/{tid}", headers=headers).json()
        assert body["status"] == "failed"
        assert body["retryable"] is True
        assert "文件损坏" in body["error"]

    def test_cancel_endpoint_reports_honestly(self, test_db):
        """取消接口必须如实说明"未强制终止"，不能谎称已杀进程"""
        import asyncio

        headers = self._auth("cancel@example.com")
        uid = self._client().get("/api/auth/me", headers=headers).json()["id"]

        tid = f"task-{uuid.uuid4().hex[:12]}"
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(task_run_service.ensure_task_run(
                tid, CONVERT_TASK, user_id=uid,
            ))
        finally:
            loop.close()

        resp = self._client().post(f"/api/tasks/{tid}/cancel", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "cancelled"
        assert body["terminated"] is False, (
            "文件 broker 无法强制终止执行中的任务，接口不应声称已终止"
        )
