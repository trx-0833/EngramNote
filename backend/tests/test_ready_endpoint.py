"""阶段 0.10：`/ready` 就绪端点与队列深度

## 这份测试要证明什么

`/health` 早就存在了，再加一个"也返回 ok 的端点"没有任何价值。这份测试盯的是
**两者的分工真的成立**（这也是计划里 0.10 与"队列深度"并列的原因）：

| 要证明的事 | 对应测试 |
|---|---|
| 一切正常 → 200 ready | `test_ready_when_database_is_usable` |
| 数据库**连不上** → 503（不是 500，更不是 ok） | `test_not_ready_when_database_is_unreachable` |
| 连得上但 **schema 没就绪** → 503 | `test_not_ready_when_schema_is_missing` |
| 503 响应不泄露库路径/异常文本 | `test_not_ready_does_not_leak_internals` |
| 队列深度字段存在、类型正确 | `test_queue_shape_is_stable` |
| 深度与 `task_runs` 的真实行数一致 | `test_task_run_counts_are_reported` |
| broker 积压被计入，**控制消息不算** | `test_broker_backlog_counts_only_real_queue_messages` |
| Redis 后端如实报"测不到"（None）而不是 0 | `test_redis_backend_reports_unknown_depth` |
| **忙 ≠ 坏**：积压不改变状态码 | `test_deep_backlog_does_not_flip_readiness` |
| 数据库挂了时 `/health` 仍 200（存活探针不引发重启风暴） | `test_health_is_unaffected_by_database_outage` |

## 为什么用 ASGITransport 而不是 TestClient

本文件的用例大多是 `async`（要自己往 `test_db` 里写行）：`TestClient` 是同步的，
在 async 用例里调用它会阻塞事件循环、并引入第二个事件循环里跑数据库的风险。
进程内 ASGI 传输是**同一个**事件循环，且不发真实网络请求（conftest 的网络守卫
把 `ASGITransport` 明确列为进程内传输）。
"""

import re
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings

#: 断言 503 响应体里不得出现的片段（都是"实现细节"，不是给探针看的东西）
_LEAKY_FRAGMENTS = (
    "engramnote.db", "D:\\", "sqlite3", "RuntimeError", "unable to open", "SELECT",
)


def _client() -> AsyncClient:
    """进程内 ASGI 客户端（不起服务、不占端口、不外呼）"""
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _unreachable_session_factory():
    """模拟"数据库连不上"：**不碰任何文件**，只是让会话工厂抛异常

    刻意不改 `DATABASE_URL`、不去动真实库：就绪探针的失败路径必须能
    在不制造任何数据损坏的前提下被验证（真实库是用户数据）。
    """
    raise RuntimeError("simulated outage: unable to open database file")


async def _seed_task_runs(session_factory, statuses) -> None:
    """往 `task_runs` 里写若干行（只为了给深度一个可数的分母）"""
    from app.models.task_run import TaskRun

    async with session_factory() as session:
        for status in statuses:
            session.add(TaskRun(
                task_id=f"ready-test-{uuid.uuid4().hex[:12]}",
                task_name="app.tasks.convert_tasks.convert_document_task",
                status=status,
            ))
        await session.commit()


async def _seed_chunks(session_factory, *, embedded: int, pending: int) -> None:
    """往 `chunks` 里写 `embedded` 条已嵌入 + `pending` 条未嵌入的行

    需要先有 user + note（按依赖顺序显式插入，理由同 `test_chunks.py` 的同一手法）。
    这个分母是这条测试的**核心**：只有"已嵌入的不算进积压"才能证明
    `pending_embeddings` 数的是待补向量，而不是 chunk 总数。
    """
    import uuid as _uuid

    from sqlalchemy import insert

    from app.models.chunk import Chunk
    from app.models.note import Note, NoteStatus, SourceType
    from app.models.user import User

    uid = str(_uuid.uuid4())
    nid = str(_uuid.uuid4())
    async with session_factory() as db:
        await db.execute(insert(User).values(
            id=uid, email=f"{uid[:8]}@example.com", username=f"u{uid[:8]}",
            hashed_password="x", is_active=True,
        ))
        await db.execute(insert(Note).values(
            id=nid, user_id=uid, title=f"笔记 {nid[:6]}",
            source_type=SourceType.pdf.value, status=NoteStatus.cleaned.value,
        ))
        for i in range(embedded + pending):
            db.add(Chunk(
                user_id=uid, note_id=nid, index=i, content=f"chunk {i}",
                char_start=i, char_end=i + 1, line_start=i, line_end=i,
                char_count=1, has_embedding=i < embedded,
            ))
        await db.commit()


def _pin_broker(monkeypatch, tmp_path: Path, *, backend: str = "local") -> Path:
    """把 broker 目录钉到 `tmp_path`，并固定 broker 后端

    `get_celery_broker_dir()` 的唯一来源是 `config.DATA_DIR`，所以把 DATA_DIR
    指到临时目录即可 —— 比伪造一个 Settings 实例更接近真实路径
    （测试要验证的正是"真实实现怎么数这个目录"）。

    Returns:
        broker 目录（已创建）
    """
    import app.config as config_mod
    import app.main as main_mod

    monkeypatch.setattr(config_mod, "DATA_DIR", tmp_path)
    cfg = Settings(
        app_env="prod", debug=False, jwt_secret_key="ready-test-key",
        celery_backend=backend,
    )
    # 就绪端点自己调 `get_settings()`（而不是复用 import 时冻结的模块属性），
    # 所以这里patch 的是 app.main 里的那个名字
    monkeypatch.setattr(main_mod, "get_settings", lambda: cfg)

    broker_dir = tmp_path / "celery" / "broker"
    broker_dir.mkdir(parents=True, exist_ok=True)
    return broker_dir


@pytest.mark.asyncio
class TestReadinessWithHealthyDatabase:

    async def test_ready_when_database_is_usable(self, test_db):
        """★ 一切正常：200 + ready，数据库检查为 ok"""
        async with _client() as client:
            resp = await client.get("/ready")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ready"
        assert body["database"] == {"status": "ok", "reason": None}
        assert body["app"], "响应里没有应用名（与 /health 的字段对齐）"

    async def test_queue_shape_is_stable(self, test_db):
        """队列深度必须**存在且形状固定**（探针/看板靠字段名取值）"""
        async with _client() as client:
            body = (await client.get("/ready")).json()

        queue = body["queue"]
        assert set(queue) == {"depth", "running", "pending", "source"}, queue
        assert queue["depth"] is None or isinstance(queue["depth"], int)
        assert isinstance(queue["running"], int), "库已答话时 running 必须是数字（空表即 0）"
        assert isinstance(queue["pending"], int), "库已答话时 pending 必须是数字（空表即 0）"
        assert isinstance(queue["source"], str) and queue["source"]

    async def test_task_run_counts_are_reported(self, test_db):
        """★ 深度不是装饰：`task_runs` 里有几行在执行/在等，就要报几"""
        from app.models.task_run import TaskStatus

        await _seed_task_runs(test_db, [
            TaskStatus.running,
            TaskStatus.running,
            TaskStatus.pending,
            # 终态不计入"在跑"：已结束的任务不是积压
            TaskStatus.succeeded,
            TaskStatus.failed,
        ])

        async with _client() as client:
            body = (await client.get("/ready")).json()

        assert body["queue"]["running"] == 2, body
        assert body["queue"]["pending"] == 1, body

    async def test_empty_table_reports_zero_not_unknown(self, test_db):
        """空表 = 0，不是"测不到"：把二者混起来，监控上就分不清
        "没有任务"与"指标坏了"
        """
        async with _client() as client:
            body = (await client.get("/ready")).json()

        assert body["queue"]["running"] == 0
        assert body["queue"]["pending"] == 0

    async def test_index_backlog_shape_is_stable(self, test_db):
        """索引积压必须**存在且形状固定**（看板靠字段名取值）"""
        async with _client() as client:
            body = (await client.get("/ready")).json()

        index = body["index"]
        assert set(index) == {"pending_embeddings", "source"}, index
        assert isinstance(index["pending_embeddings"], int), "库已答话时必须是数字（空表即 0）"
        assert isinstance(index["source"], str) and index["source"]

    async def test_index_backlog_counts_only_unembedded_chunks(self, test_db):
        """★ 积压数的是**待补向量**的行，不是 chunk 总数

        这个指标的用处就在这个分母上：2026-09-14 的取证发现，新导入的笔记
        在有人跑 `scripts/embed_chunks.py` 之前**只被 BM25 检索到**（附录 BN.4）。
        3 条已嵌入 + 2 条未嵌入必须报 2。
        """
        await _seed_chunks(test_db, embedded=3, pending=2)

        async with _client() as client:
            body = (await client.get("/ready")).json()

        assert body["index"]["pending_embeddings"] == 2, body["index"]
        assert body["index"]["source"] == "chunks_table"

    async def test_index_backlog_does_not_flip_readiness(self, test_db):
        """★ 积压是**报告项**：有积压照样 200

        与"深队列不翻转就绪"同一条判据（忙/积压 ≠ 坏）。若把它做成就绪判据，
        任何一次"刚导入还没补向量"都会让实例被判为未就绪 —— 那是把
        "检索质量暂时降级"错当成"这个实例不能干活"。
        """
        await _seed_chunks(test_db, embedded=0, pending=5)

        async with _client() as client:
            resp = await client.get("/ready")

        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "ready"
        assert resp.json()["index"]["pending_embeddings"] == 5


@pytest.mark.asyncio
class TestNotReady:

    async def test_not_ready_when_database_is_unreachable(self, test_db, monkeypatch):
        """★ 数据库不可用 → 503（而不是 500：500 与"应用本身崩了"无法区分）"""
        import app.main as main_mod

        monkeypatch.setattr(main_mod, "get_session_factory", _unreachable_session_factory)

        async with _client() as client:
            resp = await client.get("/ready")

        assert resp.status_code == 503, resp.text
        body = resp.json()
        assert body["status"] == "not_ready"
        assert body["database"] == {"status": "error", "reason": "database_unavailable"}
        # 库没答话 → 行数**未知**（None），不能填 0 冒充"没有任务"
        assert body["queue"]["running"] is None
        assert body["queue"]["pending"] is None
        # 索引积压同一条原则：0 会让"没测"与"真的没有积压"长得一模一样
        assert body["index"]["pending_embeddings"] is None
        assert body["index"]["source"] == "database_unavailable"

    async def test_not_ready_when_schema_is_missing(self, test_db, tmp_path, monkeypatch):
        """★ 连得上、但表还没建 → 也是"不能干活"

        这是本项目真实踩过的形态（CI 上临时库没建表 → 所有请求 500
        `no such table`），因此就绪判据用的是**真查业务表**而不是 `SELECT 1`。
        这里连的是一个**空的临时库**（tmp_path 下），不触碰任何真实数据。
        """
        import app.main as main_mod
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        engine = create_async_engine(
            f"sqlite+aiosqlite:///{(tmp_path / 'empty.db').as_posix()}"
        )
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        monkeypatch.setattr(main_mod, "get_session_factory", lambda: factory)
        try:
            async with _client() as client:
                resp = await client.get("/ready")
        finally:
            await engine.dispose()

        assert resp.status_code == 503, resp.text
        assert resp.json()["database"]["reason"] == "database_unavailable"

    async def test_not_ready_does_not_leak_internals(self, test_db, monkeypatch):
        """★ 无人认证的失败响应里不能有库路径、SQL、异常类名

        `/ready` 与 `/health` 一样不能要求认证（探针不会带 Token），
        因此它只能回稳定的原因码 —— 判据与 `test_error_leakage.py` 同源。
        """
        import app.main as main_mod

        def _leaky():
            raise RuntimeError(
                "unable to open database file: "
                "D:\\engramnote\\backend\\data\\db\\engramnote.db "
                "(sqlite3.OperationalError) while executing SELECT count(*)"
            )

        monkeypatch.setattr(main_mod, "get_session_factory", _leaky)

        async with _client() as client:
            resp = await client.get("/ready")

        assert resp.status_code == 503
        for fragment in _LEAKY_FRAGMENTS:
            assert fragment not in resp.text, f"503 响应泄露了内部信息: {fragment}\n{resp.text}"

    async def test_health_is_unaffected_by_database_outage(self, test_db, monkeypatch):
        """★ `/health` 与 `/ready` 的分工：同一个故障下，一个 200、一个 503

        这是"要不要重启"与"要不要摘流量"的区别。若 `/health` 也跟着 503，
        编排器会把一次数据库抖动变成全体实例重启 —— 重启不修复数据库，
        却会打断正在执行的任务（SQLite 单写者下还会留下锁与半截事务）。
        """
        import app.main as main_mod

        monkeypatch.setattr(main_mod, "get_session_factory", _unreachable_session_factory)

        async with _client() as client:
            health = await client.get("/health")
            ready = await client.get("/ready")

        assert health.status_code == 200, "存活探针不该因为依赖挂了而失败"
        assert health.json()["status"] == "ok"
        assert set(health.json()) == {"status", "app"}, "/health 的响应体不得改动"
        assert ready.status_code == 503, "就绪探针必须反映依赖不可用"


@pytest.mark.asyncio
class TestQueueDepthFromBroker:
    """队列深度的**上半段**：broker 里还没被 worker 取走的消息

    `task_runs` 的行是 worker 接手时才建的，所以"还在排队"的那一段只能问 broker。
    这里验证的是文件系统 broker 的计数口径与 kombu 自身一致。
    """

    async def test_broker_backlog_counts_only_real_queue_messages(
        self, test_db, tmp_path, monkeypatch,
    ):
        """★ 只数业务队列的消息，**不数控制消息**

        broker 目录里还住着 `celery@主机.celery.pidbox.msg` 这类控制消息
        （本机真实目录里残留了 15 个）。把它们算进去会让深度凭空多出十几，
        而"指标虚高"一旦被当成基线，真正的积压就再也看不出来了。
        """
        broker = _pin_broker(monkeypatch, tmp_path)
        (broker / "313019359_aaaa1111-1111-2222-3333-444455556666.celery.msg").write_bytes(b"{}")
        (broker / "313020125_bbbb2222-1111-2222-3333-444455556666.celery.msg").write_bytes(b"{}")
        (broker / "313024671_cccc3333-1111-2222-3333-444455556666.celery@HOST.celery.pidbox.msg").write_bytes(b"{}")
        (broker / "not-a-message.txt").write_text("noise", encoding="utf-8")

        async with _client() as client:
            body = (await client.get("/ready")).json()

        assert body["queue"]["depth"] == 2, body
        assert body["queue"]["source"] == "filesystem_broker"

    async def test_deep_backlog_does_not_flip_readiness(self, test_db, tmp_path, monkeypatch):
        """★ 忙 ≠ 坏：积压 50 条仍然 200（深度只报告，不决定状态码）

        把深度做成就绪判据的后果是：高峰期所有实例一起被判未就绪，
        负载均衡器于是把流量发给空无一物的实例（或直接 502）。
        """
        from app.models.task_run import TaskStatus

        broker = _pin_broker(monkeypatch, tmp_path)
        for i in range(50):
            name = f"31{i:07d}_dddd4444-1111-2222-3333-444455556666.celery.msg"
            (broker / name).write_bytes(b"{}")
        await _seed_task_runs(test_db, [TaskStatus.running])

        async with _client() as client:
            resp = await client.get("/ready")

        assert resp.status_code == 200, resp.text
        assert resp.json()["queue"] == {
            "depth": 50, "running": 1, "pending": 0, "source": "filesystem_broker",
        }

    async def test_redis_backend_reports_unknown_depth(self, test_db, tmp_path, monkeypatch):
        """★ Redis 后端：如实报"测不到"（None），而不是回一个假的 0

        量 Redis 队列需要可选依赖 `redis`（requirements.txt 里是注释掉的）。
        回 0 会让一份坏掉的监控看起来一切正常 —— 那比"没有这个指标"更糟。
        """
        _pin_broker(monkeypatch, tmp_path, backend="redis")

        async with _client() as client:
            resp = await client.get("/ready")

        assert resp.status_code == 200, resp.text
        assert resp.json()["queue"]["depth"] is None
        assert resp.json()["queue"]["source"] == "redis_broker_not_probed"

    async def test_missing_broker_dir_means_zero(self, test_db, tmp_path, monkeypatch):
        """broker 目录不存在 = 一条消息都没投递过 → 0（而不是"测不到"）"""
        import app.config as config_mod
        import app.main as main_mod

        monkeypatch.setattr(config_mod, "DATA_DIR", tmp_path)
        cfg = Settings(
            app_env="prod", debug=False, jwt_secret_key="ready-test-key",
            celery_backend="local",
        )
        monkeypatch.setattr(main_mod, "get_settings", lambda: cfg)
        # 刻意**不**创建 broker 目录

        async with _client() as client:
            body = (await client.get("/ready")).json()

        assert body["queue"]["depth"] == 0, body
        assert body["queue"]["source"] == "filesystem_broker"


class TestReadyContract:
    """契约层面的三件事（不需要数据库）"""

    @staticmethod
    def _spec() -> dict:
        from app.main import app

        return app.openapi()

    def test_ready_is_declared_in_the_schema(self):
        """`/ready` 必须出现在 OpenAPI 契约里（与 /health 同等对待）"""
        op = self._spec()["paths"].get("/ready", {}).get("get")
        assert op is not None, "/ready 不在 schema 里"
        assert not op.get("security"), "/ready 不能要求认证（探针不会带 Token）"

    def test_both_status_codes_are_documented(self):
        """200 与 503 都要声明，且 503 的形状与 200 相同"""
        responses = self._spec()["paths"]["/ready"]["get"]["responses"]
        assert "200" in responses and "503" in responses, sorted(responses)
        ok = responses["200"]["content"]["application/json"]["schema"]
        not_ready = responses["503"]["content"]["application/json"]["schema"]
        assert ok.get("$ref") and not_ready.get("$ref"), (ok, not_ready)
        assert ok["$ref"] == not_ready["$ref"], (
            "200 与 503 的响应模型必须是同一个（探针用一套解析逻辑处理两种情况）"
        )

    def test_health_body_is_unchanged(self):
        """既有端点的响应体不得被本次改动顺手改掉"""
        schema = self._spec()["paths"]["/health"]["get"]["responses"]["200"]
        ref = schema["content"]["application/json"]["schema"].get("$ref", "")
        assert ref.endswith("/HealthResponse"), ref

    def test_no_frontend_source_references_ready(self):
        """`/ready` 是运维端点，不该被前端调用（防"顺手接进 UI"）

        不是安全边界，只是把意图写下来：它返回的是部署内部状态，
        前端页面需要的是业务数据。
        """
        frontend = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
        if not frontend.is_dir():  # pragma: no cover - 前端目录不在时跳过
            pytest.skip("前端目录不存在")
        offenders = []
        scanned = 0
        for path in frontend.rglob("*.ts*"):
            # 排除**机械生成物** `src/api/generated/**`（`npm run gen:api` 的产物）：
            # 它由后端 OpenAPI 契约生成，契约里有的每个路径都会出现在它里面 ——
            # 包括 `/ready`。它**描述**端点，不代表前端**调用**端点。
            # 不排除它，本守卫会在每次重新生成契约后误报；而同一测试类里的
            # `test_ready_in_openapi_contract` 恰恰要求 `/ready` **必须**在契约里，
            # 两条断言只有排除生成物之后才相容（2026-09-14 由重新生成契约暴露）。
            # ⚠️ 排除面刻意只写 `api/generated` 这一个**具体**目录，不用
            # "看起来像生成物"这类宽判据 —— 宽判据会在有人新增另一个生成目录时
            # 把守卫悄悄放空，而放空的守卫比没有守卫更糟。
            if "api/generated" in path.as_posix():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            scanned += 1
            if re.search(r"['\"`]/ready['\"`]", text):
                offenders.append(str(path.relative_to(frontend)))
        # 反空转：这类"扫描型"守卫最危险的失效形态是**扫了 0 个文件却永远通过**
        # （源根改名、目录搬家、`rglob` 模式写错都会造成它），
        # 与 `test_rate_limit_coverage` 里那条"防检查空转"的守卫同一口径。
        assert scanned > 0, "扫描到 0 个前端源文件 —— 守卫已空转，先检查扫描路径"
        assert not offenders, f"前端调用了 /ready（运维端点）: {offenders}"
