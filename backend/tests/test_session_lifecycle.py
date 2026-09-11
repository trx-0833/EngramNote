"""
会话生命周期与事务边界测试（阶段 1′ 第 2 项：长事务短化）

## 排查结论（先于代码）

文档把这一项写成"禁止把外部 API 调用包在 DB 事务里"。本轮用
`scripts/_audit_long_tx.py` 做了 AST 审计（扫描"写操作之后、commit 之前
是否夹着对象存储 / LLM / Celery 往返 / sleep"），**命中 0 处**。
任务侧与 RAG 侧的外部调用要么不接 `db`，要么在调用前就结束了 session。

真正存在的机制是另一个，也更隐蔽：

- `get_db()` 给每个请求一个 session，**作用域是整个请求**
  （FastAPI 的 yield 依赖在响应结束后才收尾）；
- SQLAlchemy 是 `autobegin` 语义，一次 SELECT 就开启事务；
- 因此用 `StreamingResponse` 的 SSE 端点在**整个流式输出期间**持有
  这个 session。

WAL 只保证"读不阻塞写"，所以只读的流式端点不构成写锁风险；但若某个
流式端点在 `yield` 之前写过且未提交，写锁会被持有数十秒 —— 这才是
本项要防的东西。本文件的用例把这条规则**锁死**。
"""

import asyncio

import pytest
from sqlalchemy import text

from app.database import get_db


class TestGetDbTransactionBoundary:
    """`get_db` 的事务边界契约"""

    def test_rolls_back_uncommitted_writes_on_exception(self, test_db):
        """路由抛异常时，未提交的写入必须回滚

        这是"HTTP 500 之后库里留下半截数据"的直接防线。

        注意用什么模拟异常：**不能**用 `agen.aclose()`。
        `aclose()` 触发的是 `GeneratorExit`（继承自 BaseException），
        走的是 `finally` 分支，等价于"请求正常结束但路由忘了 commit"。
        真实的异常会被 FastAPI 通过 `athrow` 抛进生成器，因此这里也必须
        用 `athrow` —— 第一版测试用错了方式，得出了与实际相反的结论。
        """

        async def _scenario():
            agen = get_db()
            session = await agen.__anext__()
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS _tx_probe (v INTEGER)"
            ))
            await session.execute(text("INSERT INTO _tx_probe VALUES (1)"))
            await session.flush()  # 已写入但未提交
            assert session.in_transaction() is True

            # 模拟路由抛业务异常
            try:
                await agen.athrow(RuntimeError("路由失败"))
            except RuntimeError:
                pass
            except StopAsyncIteration:
                pass

            agen2 = get_db()
            session2 = await agen2.__anext__()
            try:
                return (await session2.execute(
                    text("SELECT COUNT(*) FROM _tx_probe")
                )).scalar()
            finally:
                await agen2.aclose()

        assert asyncio.run(_scenario()) == 0, (
            "路由异常时未提交的写入残留在库里 —— 事务边界失效"
        )

    def test_commits_writes_the_route_forgot_to_commit(self, test_db):
        """路由忘记 commit 时，`get_db` 兜底提交

        这条防的是"HTTP 200 但数据没落库"—— 最难排查的一类静默失败。
        """

        async def _scenario():
            agen = get_db()
            session = await agen.__anext__()
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS _tx_probe2 (v INTEGER)"
            ))
            await session.execute(text("INSERT INTO _tx_probe2 VALUES (7)"))
            await session.flush()
            # 刻意不 commit，直接结束依赖（模拟路由漏写 commit）
            await agen.aclose()

            agen2 = get_db()
            session2 = await agen2.__anext__()
            try:
                return (await session2.execute(
                    text("SELECT COUNT(*) FROM _tx_probe2")
                )).scalar()
            finally:
                await agen2.aclose()

        assert asyncio.run(_scenario()) == 1, (
            "路由漏写 commit 时数据丢失 —— get_db 的兜底提交未生效"
        )

    def test_explicit_commit_is_not_broken_by_extra_commit(self, test_db):
        """路由自己 commit 之后，get_db 的兜底提交不应报错或回退数据

        SQLAlchemy 在 commit 后事务已结束，再 commit 是空操作。
        若这里出错，说明兜底逻辑写错了（例如无条件 commit）。
        """

        async def _scenario():
            agen = get_db()
            session = await agen.__anext__()
            await session.execute(text(
                "CREATE TABLE IF NOT EXISTS _tx_probe3 (v INTEGER)"
            ))
            await session.execute(text("INSERT INTO _tx_probe3 VALUES (9)"))
            await session.commit()
            assert session.in_transaction() is False
            await agen.aclose()

            agen2 = get_db()
            session2 = await agen2.__anext__()
            try:
                return (await session2.execute(
                    text("SELECT COUNT(*) FROM _tx_probe3")
                )).scalar()
            finally:
                await agen2.aclose()

        assert asyncio.run(_scenario()) == 1


class TestLegacySchemaReconciliation:
    """存量库的 schema 必须能被对齐到模型定义

    ## 为什么需要这组测试

    `create_all()` 只创建**缺失的表**，不会给已有表加列；
    `_migrate_sqlite()` 也只加它显式写出的列。也就是说，
    **新增一个模型字段时必须在迁移里登记**，否则：

        全新库（create_all）→ 有这一列
        存量库（真库）      → 永远没有这一列

    本轮实测踩到两次（`grading_method`、`card_id`），两次都是
    "全新库测试全绿、真库运行时报 no such column"。
    """

    def test_every_model_column_is_registered_in_migration(self):
        """新增的模型列必须在 `_migrate_sqlite` 中登记迁移

        判据：模型列必须落在「初始建表就有的列」或「已登记的迁移」之一。

        这套"必须二选一"的约束正是要抓的缺陷：新增模型字段时若忘了登记迁移，
        全新库（`create_all`）有该列、存量真库永远没有，只在真库运行时报
        `no such column` —— 而所有测试用的都是全新临时库，永远发现不了。

        本轮实测踩到两次（`grading_method`、`card_id`），都是这个形态。
        """
        import ast
        from pathlib import Path

        from app import database as db_mod
        from app.models.review_log import ReviewLog

        #: `review_logs` **初始建表**就存在的列。
        #:
        #: 这份清单是刻意硬编码的：它记录的是一段历史事实（首次
        #: `create_all` 时表长什么样），而不是从当前模型推导出来的 ——
        #: 从模型推导会让这个测试变成恒真的空测试。
        baseline_columns = {
            "id", "created_at", "updated_at",          # BaseModel 通用列
            "user_id", "quiz_id", "note_id", "user_answer",
            "is_correct", "quality", "time_spent_ms", "review_at",
        }

        # 从 database.py 源码中收集已登记的加列迁移
        tree = ast.parse(Path(db_mod.__file__).read_text(encoding="utf-8"))
        migrated: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if "ALTER TABLE review_logs ADD COLUMN" in text:
                    migrated.add(
                        text.split("ADD COLUMN", 1)[1].split()[0].strip('"')
                    )

        model_columns = {c.name for c in ReviewLog.__table__.columns}
        unregistered = model_columns - baseline_columns - migrated

        assert not unregistered, (
            f"模型有列 {sorted(unregistered)}，既不在初始 schema 基线里、"
            "也没有登记迁移。\n"
            "后果：全新库有该列、存量真库永远没有 —— 只在真库运行时报 "
            "`no such column`，且所有测试（用全新临时库）都发现不了。\n"
            "修法：在 `database.py::_migrate_sqlite` 中补 "
            "`ALTER TABLE review_logs ADD COLUMN ...`；"
            "若该列确实是初始建表就有的，把它加进本测试的 baseline_columns。"
        )

        # 反向检查：迁移清单里不应有模型已删除的列（避免迁移对着幽灵列执行）
        stale = migrated - model_columns
        assert not stale, (
            f"迁移里登记了模型中已不存在的列 {sorted(stale)} —— 应当清理"
        )

    def test_migration_is_idempotent_for_existing_columns(self, test_db):
        """对已是新 schema 的库重复 init_db 不应报错"""
        import asyncio

        from app import database as db_mod

        for _ in range(2):
            asyncio.run(db_mod.init_db())

        from sqlalchemy import text
        async def _columns():
            async with db_mod.get_engine().connect() as conn:
                return [c[1] for c in (await conn.execute(
                    text("PRAGMA table_info(review_logs)")
                )).fetchall()]

        columns = asyncio.run(_columns())
        assert "card_id" in columns
        assert "grading_method" in columns


class TestWorkflowYaml:
    """GitHub Actions workflow 必须是合法 YAML

    ## 为什么需要这组测试

    本仓库的 CI 首次启用时**根本没跑起来**：GitHub 报
    `Invalid workflow file: You have an error in your yaml syntax on line 48`，
    原因是 `- name: Ruff lint (app: strict)` 里**未加引号的冒号加空格** ——
    YAML 把 `name: Ruff lint (app` 当成键值对，剩下的 `: strict)` 成了多余的映射符。

    这种错误的表现极具误导性：workflow run 显示 failure，但 `jobs` 数组为空、
    `runner_name` 为空、没有任何 check-run —— 看起来像"runner 没分配"或
    "账单限制"，实际是文件根本没被解析。

    而它**不会被 pytest / ruff / eslint 中的任何一个发现**：那些工具只看
    Python 与 TS，没人校验 YAML。所以这里补上。
    """

    def _workflow_files(self) -> list:
        from pathlib import Path

        root = Path(__file__).resolve().parents[2] / ".github" / "workflows"
        return sorted(root.glob("*.yml")) + sorted(root.glob("*.yaml"))

    def test_at_least_one_workflow_exists(self):
        assert self._workflow_files(), "找不到任何 workflow 文件"

    def test_pyyaml_is_installed(self):
        """PyYAML 必须可用（CI 的依赖安装里必须包含它）

        否则下面的解析测试会静默跳过，这道防线形同虚设。
        """
        try:
            import yaml  # noqa: F401
        except ImportError:  # pragma: no cover
            pytest.fail(
                "PyYAML 未安装 —— workflow YAML 校验会被跳过。"
                "请在 .github/workflows/ci.yml 的依赖安装中加入 pyyaml。"
            )

    def test_all_workflows_parse(self):
        """每个 workflow 都必须能被 YAML 解析器接受

        断言消息里带上解析器给出的**具体行号**，否则排查这种错误要花很久
        （本轮实测：从"runner 没分配"一路追到"YAML 第 48 行"）。
        """
        import yaml

        failures: list[str] = []
        for path in self._workflow_files():
            try:
                yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                failures.append(f"{path.name}: {exc}")
        assert not failures, "workflow YAML 解析失败：\n" + "\n".join(failures)

    def test_workflow_structure_is_sane(self):
        """解析后结构必须合理：每个 job 有 runs-on，每个 step 有 run 或 uses"""
        import yaml

        for path in self._workflow_files():
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            # YAML 1.1 把裸 `on` 解析为布尔 True，GitHub 读的是字符串 'on'
            trigger_key = "on" if "on" in data else True
            assert trigger_key in data, f"{path.name} 缺少 on 触发器"

            jobs = data.get("jobs") or {}
            assert jobs, f"{path.name} 没有任何 job"
            for job_name, job in jobs.items():
                assert job.get("runs-on"), f"{path.name}:{job_name} 缺少 runs-on"
                for step in job.get("steps", []):
                    assert "run" in step or "uses" in step, (
                        f"{path.name}:{job_name} 的 step 既无 run 也无 uses: {step}"
                    )


class TestStreamingEndpoints:
    """流式端点的事务规则

    规则：**在 `yield` 第一个 SSE 事件之前完成所有写操作并 commit**。

    为什么是硬规则：SSE 响应会持续数十秒，而 `get_db` 的 session 要到
    响应体发送完毕才收尾。若写事务跨越整段流式输出，SQLite 的写锁就被
    占住数十秒，其他写请求会撞 `busy_timeout`（30 秒）后 500。
    WAL 只解决"读不阻塞写"，不解决这个。
    """

    def test_streaming_handlers_commit_before_first_yield(self):
        """静态审计：所有 SSE 端点不得在首个 yield 之前有未提交的写操作

        用 AST 检查，而不是靠人记规则：新增流式端点时最容易忘掉这条。
        """
        import ast
        from pathlib import Path

        api_dir = Path(__file__).resolve().parent.parent / "app" / "api"
        write_calls = ("db.add", "session.add", "db.flush", "session.flush")

        offenders: list[str] = []
        checked = 0

        for path in api_dir.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                # 只看含 yield 的（生成器/SSE 端点）
                if not any(isinstance(n, ast.Yield) for n in ast.walk(func)):
                    continue
                checked += 1

                committed = False
                for stmt in func.body:
                    src = ast.unparse(stmt)
                    if ".commit()" in src:
                        committed = True
                    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Yield):
                        # 首个 yield：此前若写过但没提交，即为违规
                        if not committed and any(w in src for w in write_calls):
                            offenders.append(f"{path.name}:{func.lineno} {func.name}()")
                        break
                    if not committed and any(w in src for w in write_calls):
                        offenders.append(f"{path.name}:{func.lineno} {func.name}()")
                        break

        # 至少要扫到东西，否则这个测试是空的
        assert checked > 0, "没有扫到任何含 yield 的函数，审计逻辑失效"
        assert not offenders, (
            "以下流式端点在首次 yield 前存在未提交的写操作，"
            f"会持有 SQLite 写锁直到流结束: {offenders}"
        )
