"""
FastAPI 应用入口模块

本模块是 EngramNote 后端服务的启动入口，负责创建和配置 FastAPI 应用实例。

主要职责：
- 定义应用生命周期管理（启动时初始化数据库）
- 配置 CORS 中间件（允许前端开发服务器跨域访问）
- 注册 API 路由（统一挂载到 /api 前缀下）
- 提供健康检查（/health，存活）与就绪检查（/ready，可服务）端点
- 按运行环境决定交互式 schema 端点（/docs、/openapi.json、/redoc）是否注册

设计决策：
- 使用 lifespan 上下文管理器替代 on_event 装饰器（FastAPI 推荐方式）
- CORS 仅允许开发服务器域名，生产环境应配置为实际前端域名
- 所有 API 路由统一挂载到 /api 前缀，便于反向代理和版本管理
- 应用由 `create_app()` 工厂构造（阶段 0.10）：应用的一部分行为取决于
  运行环境（schema 端点是否注册），而"模块级直接构造"会把这件事冻结在
  import 那一刻 —— 一个进程只能验证一种姿态。工厂让测试显式传入另一份
  配置、构造出另一种姿态的应用来断言，同时模块级 `app` 保持原样。

Celery 启动说明（独立进程，不嵌入 FastAPI）：
- 启动 Celery Worker（处理异步任务）：
    celery -A app.tasks.celery_app worker --loglevel=info
- 启动 Celery Beat（定时任务调度器）：
    celery -A app.tasks.celery_app beat --loglevel=info
- Beat 必须作为独立进程运行，不能嵌入 FastAPI 进程，
  否则在多 worker 部署时会导致重复调度。
"""

from contextlib import asynccontextmanager
import logging
import os
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .api.router import api_router
from .version import __version__
from .config import Settings, get_settings
from .core import context
from .core.logging_config import setup_logging
from .core.tempfile_compat import apply_tempfile_compat
from .database import get_session_factory, init_db
from .middleware.error_handler import ErrorHandlerMiddleware
from .middleware.rate_limit import RateLimitMiddleware
from .middleware.request_context import RequestContextMiddleware
from .schemas.common import HealthResponse
from .services.llm_accounting_service import LLMQuotaExceeded

# 获取全局配置
settings = get_settings()
logger = logging.getLogger(__name__)


def _enforce_single_writer() -> None:
    """SQLite 路线下的单写者守卫（overhaul-plan 决策 D1=B / 附录 E.6 方案 A）

    ## 为什么必须显式拦住

    SQLite 全库只有一个写锁。`uvicorn --workers N`（N>1）会起 N 个**独立进程**，
    每个进程各有一个连接池，于是：

    - WAL 只允许"一写多读"，多进程写会在 `busy_timeout`（30 秒）耗尽后
      抛 `database is locked`。30 秒是很长的等待 —— 表现为请求莫名卡半分钟
      然后 500，而不是干脆失败。
    - 更隐蔽的是 `RateLimitMiddleware` 的滑动窗口、`tasks/common.py` 的
      引擎缓存都是**进程内**状态。多进程下每个进程各算各的，
      限流阈值实际变成 N 倍，看起来"限流没生效"。

    这类问题不会被单元测试发现（测试是单进程的），只会在部署后以
    "偶发 500 / 限流失效"的形态出现，排查成本极高。所以在启动时直接拒绝
    比事后调试便宜得多。

    ## 为什么是拒绝而不是自动降为 1

    静默把 N 改成 1 会让运维以为多进程部署成功了，实际只跑了一个 worker。
    宁可启动失败并说清楚原因，也不要一个"看起来正常但能力被悄悄削掉"的服务。

    Raises:
        RuntimeError: 使用 SQLite 且检测到多 worker 配置
    """
    import os

    from .database import _sqlite

    if not _sqlite():
        return

    raw = (
        os.environ.get("WEB_CONCURRENCY")
        or os.environ.get("UVICORN_WORKERS")
        or ""
    ).strip()
    if not raw:
        # 未显式配置：默认单进程（uvicorn 默认即 1），安全
        return

    try:
        workers = int(raw)
    except ValueError:
        raise RuntimeError(
            f"无法解析 worker 数量环境变量: WEB_CONCURRENCY={raw!r}。"
            "请设为整数 1，或改用 PostgreSQL。"
        ) from None

    if workers > 1:
        raise RuntimeError(
            f"检测到 {workers} 个 worker，但当前数据库是 SQLite。\n"
            "SQLite 全库只有一个写锁，多进程写会在 busy_timeout 耗尽后抛 "
            "database is locked；且限流/引擎缓存等进程内状态会各自为政"
            "（限流阈值实际放大为 N 倍）。\n"
            "请选择其一：\n"
            "  1) 固定单写者（本项目当前路线）：WEB_CONCURRENCY=1；\n"
            "  2) 迁移到 PostgreSQL（overhaul-plan 手术刀 1）。\n"
            "详见 docs/overhaul-plan.md 附录 E.6 与 docs/sqlite-single-writer.md。"
        )


@asynccontextmanager
def _log_security_posture() -> None:
    """启动时打印**生效的**安全姿态（阶段 6.8）

    ## 为什么需要它

    阶段 4.10 把 `debug` 拆成了三个开关之后，"当前跑在什么姿态下"不再是一个
    布尔值能看出来的。而**部署事故的典型形态正是姿态不对**：
    生产环境误用 `APP_ENV=dev` → 异常会回吐 traceback（泄露堆栈与路径）、
    JWT 密钥在缺失时自动生成（多实例之间密钥不一致，且换容器即失效）；
    或者误开 `LOG_SQL=true` → 日志里出现 bcrypt 哈希与卡片正文。

    这些都不会报错，只会静静地降低安全性。因此在启动日志里**明确写一行**
    生效姿态，并在危险组合下额外告警 —— 让"配错了"在启动那一刻就可见，
    而不是等到出事。

    ⚠️ 它只记录，不阻止启动：把"生产环境不能用 dev"硬编码成拒绝启动，
    会让本地开发与演示环境无法运行（本项目正是本地跑为主）。
    可观测 + 显式告警是这里更合适的手段。
    """
    from .config import get_settings

    cfg = get_settings()
    logger.info(
        "安全姿态 | app_env=%s | LLM=%s | SQL 日志=%s | traceback 回吐=%s | "
        "JWT 密钥=%s",
        cfg.app_env,
        cfg.get_llm_config()["provider"],
        "开" if cfg.log_sql else "关",
        "开" if cfg.is_dev else "关",
        "已配置" if cfg.jwt_secret_key else "缺失",
    )
    if cfg.is_dev:
        logger.warning(
            "当前为开发环境（APP_ENV=dev）：异常会回吐 traceback，"
            "JWT 密钥在缺失时会自动生成到 data/.jwt-secret。"
            "**生产部署务必设为 APP_ENV=prod 并显式配置 JWT_SECRET_KEY**"
        )
    if cfg.log_sql:
        logger.warning(
            "已开启 SQL 日志（LOG_SQL=true）：日志包含 bcrypt 哈希与知识卡片/题目正文，"
            "请勿用于生产"
        )

    # 缺 LLM Key 时**显式说明哪些功能不可用**（阶段 2.3 起）
    #
    # ## 为什么要在这里说，而不是等第一次调用失败
    #
    # 没有 Key 时 config 层不做拦截（这是对的：检索、清洗、复习、图谱的
    # 非 LLM 部分都还能用），但用户第一次点"AI 理解"只会看到一句
    # "API key not configured"，不知道**范围**有多大 —— 于是会以为整个应用坏了。
    # 启动时把边界一次讲清楚，比在十几个入口各报一次错便宜得多。
    configured = [name for name, key in (
        ("DEEPSEEK_API_KEY", cfg.deepseek_api_key),
        ("GLM_API_KEY", cfg.glm_api_key),
    ) if (key or "").strip()]
    if not configured:
        logger.warning(
            "未配置任何 LLM API Key（DEEPSEEK_API_KEY / GLM_API_KEY）："
            "**AI 理解、自动出题、RAG 问答、图谱关系推断、选中文本提问**将不可用；"
            "上传转换、规则清洗、检索（BM25/向量）、间隔重复复习、学习报告**不受影响**。"
            "配置方法见 backend/.env.example。"
        )
    if not (cfg.mineru_api_token or "").strip():
        logger.info(
            "未配置 MINERU_API_TOKEN：PDF **云端解析**不可用"
            "（MINERU_BACKEND=vlm-http-client 时需要它）；"
            "改用本地 pipeline 模式需另装 requirements-pdf-local.txt 并下载约 7GB 模型。"
            "Markdown / 图片 / Office 不受影响。"
        )


async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    在应用启动时执行初始化操作（如创建数据库表），
    yield 之后为应用关闭时的清理逻辑（当前无需清理）。

    Args:
        app: FastAPI 应用实例
    """
    # 启动时：运行环境兼容（替换 tempfile.mkdtemp，详见 core/tempfile_compat.py）
    apply_tempfile_compat()
    # 启动时：初始化日志配置
    setup_logging()
    # 启动时：拒绝会破坏 SQLite 单写者假设的部署配置
    _enforce_single_writer()
    # 启动时：把生效的安全姿态写进日志（阶段 6.8）
    _log_security_posture()
    logger.info("EngramNote 应用启动")
    # 启动时：初始化数据库，创建所有数据表
    await init_db()
    yield
    # 关闭时：释放共享 LLM HTTP 客户端，见 docs/decisions.md#F-05
    try:
        from .services.llm_service import close_llm_client
        close_llm_client()
        logger.info("EngramNote 应用关闭：已释放 LLM 共享客户端")
    except Exception as e:
        logger.warning(f"关闭 LLM 共享客户端失败: {e}")


# ===========================================================================
# 阶段 0.10：交互式 schema 端点的环境闸门
# ===========================================================================

def _schema_endpoint_kwargs(cfg: Settings) -> dict[str, Any]:
    """按运行环境决定 `/docs`、`/openapi.json`、`/redoc` 是否注册（阶段 0.10）

    ## 生产姿态：**整体关闭**（不注册路由），而不是"加保护"

    这三条路由只服务一种人：正在读这份 API 的开发者。它们不是产品功能，
    所以在生产姿态下直接不注册（FastAPI 原生开关：三个都传 `None`，
    其 `setup()` 里每一段都被 `if self.openapi_url` 守着，于是一条也不留）。

    **为什么不选"加认证"**（计划原文是"生产关闭**或**加保护"，这里明确选前者）：

    1. `/docs` 是**浏览器**去取 `/openapi.json` 的（Swagger UI 的 JS 自行发请求），
       给它套 Bearer 认证需要额外的 OAuth 转发/代理；而"在浏览器里看文档"
       这件事在生产部署里本来就没有需求（要读文档去 dev 环境读）。
    2. 本项目**没有管理员角色**这一层 —— 任何已登录用户都能拿到同一份 schema，
       于是"加保护"实际只是把"公开"变成"对每个注册用户公开"，
       安全收益接近于零，却多出一个需要维护与验证的中间件。
    3. `/openapi.json` 对攻击者是**侦察**：一次匿名请求即可拿到全部路由、
       参数名与认证方案。要挡住侦察，最省事且**不会被配错**的做法就是让它
       不存在 —— `None` 是声明式的，没有"忘了加依赖"或"鉴权分支写反了"的形态。

    ## 为什么不破坏本项目的工具链

    `backend/scripts/dump_openapi.py` 走的是**进程内**的 `app.openapi()`：
    它从已注册的路由对象生成 schema，是纯内存计算，**不经过 HTTP 路由**，
    因此 `openapi_url=None` 对它没有任何影响（前端的类型/客户端生成照旧）。
    配套测试
    `tests/test_env_switches.py::TestSchemaEndpointGating::test_in_process_schema_survives_production_posture`
    把这件事钉住了 —— 否则"为了关文档而弄坏前端生成"会是一次静默的倒退。

    ## dev 姿态保持原样

    三条路由都在（`/docs` 能点、`/openapi.json` 能取）：那是人操作 API 的方式，
    也是本项目日常工作的方式。判断依据**沿用既有的** `settings.is_dev`
    （`app_env` / 遗留 `debug` 折叠而来，见 `config.py`），不另立一套"是不是生产"。
    """
    if cfg.is_dev:
        return {
            "docs_url": "/docs",
            "redoc_url": "/redoc",
            "openapi_url": "/openapi.json",
        }
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


# ===========================================================================
# 阶段 0.10：/ready —— "这个实例现在能不能干活"，与 /health 分工不同
# ===========================================================================
#
# ## 为什么不能只留 /health
#
# `/health` 回答的是"**进程还活着吗**"：不碰数据库、不碰 broker、不碰外部服务，
# 只要事件循环还能回一个 JSON 就 200。这个语义是有意的 —— 它是**存活探针**
# （liveness），进程管理器用它决定"要不要重启"。
#
# 正因为用它决定重启，它**绝不能**因为某个依赖不可用而失败：一次数据库抖动
# 会让编排器把所有实例判定为死、成批重启；而重启既不修复数据库，还会打断
# 正在执行的任务（SQLite 单写者下还会留下锁与半截事务）。**"忙"和"坏"必须
# 分开表达** —— 这正是要同时有 /health 与 /ready 的原因，不是重复建设。
#
# `/ready` 回答的是"**现在能不能服务请求**"：数据库连得上、schema 在（表齐），
# 否则把流量从这个实例上摘掉（readiness 探针），但**不**重启它。
#
# ## 队列深度为什么只报告、不决定就绪
#
# broker 里堆了一万条消息说明"慢"，不说明"坏"：实例本身完全有能力接单。
# 把深度做成就绪判据会有两个后果 —— (1) 高峰期所有实例一起被判为未就绪，
# 负载均衡器于是把流量发给**空无一物**的实例（或直接 502）；
# (2) 若该判据被接到存活探针上，就是上面那种重启风暴。
# 背压是另一个机制（限流/扩容），不该混进就绪判定。因此 `/ready` 只把
# 深度**如实报出来**，状态码只由"能力"（数据库）决定。
#
# ## 索引积压为什么也只报告、不决定就绪（2026-09-14 加）
#
# "有多少 chunk 还没有向量"属于同一类信息：它说明**检索质量暂时降级**
# （这些内容只被 BM25 检索到），不说明实例坏了。
#
# 这个数字是补上来的：2026-09-14 的全链路取证（附录 BN.4）发现，
# 清洗路径**刻意不写向量**（B 半要加载 4.3GB 模型），补嵌入只有人工脚本
# `scripts/embed_chunks.py`，而 Beat 调度表里没有这个任务 —— 于是"新导入的
# 资料什么时候进向量通道"取决于**有没有人记得跑脚本**，界面上也看不出来。
# 在决定要不要挂定时任务之前，先让它**可见**：一个恒为 0 的指标能证明"没有积压"，
# 一个没人看得见的指标什么都证明不了。


class DatabaseCheck(BaseModel):
    """`/ready` 的数据库检查结果

    `reason` 是**稳定原因码**而不是异常文本：`/ready` 与 `/health` 一样不能
    要求认证（探针不会带 Token），而异常文本里会有库路径、SQL 片段等内部信息
    （见 `tests/test_error_leakage.py` 的判据）。细节进服务端日志。
    """

    status: str
    reason: Optional[str] = None


class QueueDepth(BaseModel):
    """`/ready` 的队列深度快照

    三个数字的**各自含义**（不要相加，它们来自两个不同的存储）：

    - `depth`：broker 里**等待 worker 接手**的消息数 —— 这才是"还有多少在排队"；
    - `running`：`task_runs` 里正在执行的任务数；
    - `pending`：`task_runs` 里已建记录但尚未开始的任务数。

    `depth` 为 `None` 表示**当前后端测不到**（原因见 `source`），
    而不是"零"。用 `None` 而不是 0 是刻意的：0 会让"没测"与"真的没人排队"
    在监控面板上长得一模一样（与 `llm_calls.cost` 记 NULL 而非 0 同一条原则）。
    """

    depth: Optional[int] = None
    running: Optional[int] = None
    pending: Optional[int] = None
    #: `depth` 的来源；测不到时是原因码（如 `redis_broker_not_probed`）
    source: str


class IndexBacklog(BaseModel):
    """`/ready` 的索引积压快照（阶段 5.1 的取证发现的缺口）

    `pending_embeddings` = `chunks` 表里 `has_embedding = false` 的行数：
    这些内容**只被 BM25 检索到**，向量通道里还看不见它们
    （清洗路径刻意不写向量，补嵌入是人工脚本，见本模块 `/ready` 上方说明）。

    `None` 表示**当前测不到**（库不可用），而不是 0 —— 与 `QueueDepth.depth`
    同一条原则：0 会让"没测"与"真的没有积压"在监控面板上长得一模一样。
    """

    pending_embeddings: Optional[int] = None
    #: 来源；测不到时是原因码（如 `chunks_unavailable`）
    source: str


class ReadinessResponse(BaseModel):
    """`GET /ready` 的响应（200 与 503 **共用同一形状**）

    失败时保持同样的字段结构，探针与看板才能用一套解析逻辑同时处理两种情况
    （需要分支的只有 `status` 与状态码本身）。
    """

    status: str
    app: str
    database: DatabaseCheck
    queue: QueueDepth
    index: IndexBacklog


#: Celery 默认队列名。`tasks/celery_app.py` **没有**覆盖 `task_default_queue`
#: （它只设了 broker/结果后端与重试策略），所以消息文件的后缀是 `.celery.msg`。
#: 队列名一旦改动，这里与 kombu 的计数口径要一起改（`_broker_queue_depth` 的说明）。
_CELERY_DEFAULT_QUEUE = "celery"


async def _task_status_counts() -> tuple[Optional[dict[str, int]], Optional[str]]:
    """按状态统计 `task_runs`；失败时返回 `(None, 原因码)`

    ## 为什么这一次查询同时充当"数据库就绪"判据

    判据不是 `SELECT 1` —— 那只证明"连得上"，而**连得上但表不存在**的库
    恰恰是本项目真实踩过的形态（CI 上临时库没建表 → 每个请求 500
    `no such table: users`，见 `tests/conftest.py` 的会话级隔离说明）。
    改成真查业务表之后，"连不上"与"schema 没就绪"都会在这里失败，
    而这正是"能不能干活"这个问题的两个主要否定答案。

    ⚠️ 查询本身是只读的，且不写任何东西：就绪探针会被高频调用，
    探针**绝不能**成为数据变更的来源。

    Returns:
        (状态 → 行数 的映射, 失败原因码)；成功时原因码为 None
    """
    from sqlalchemy import func, select

    from .models.task_run import TaskRun

    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            rows = (await session.execute(
                select(TaskRun.status, func.count()).group_by(TaskRun.status)
            )).all()
    except Exception as exc:
        # 细节只进日志：响应里回的是稳定的原因码（见 DatabaseCheck 的说明）
        logger.error(
            "就绪检查失败：数据库不可用或 schema 未就绪 | %s: %s",
            type(exc).__name__, exc, exc_info=True,
        )
        return None, "database_unavailable"

    counts: dict[str, int] = {}
    for status, count in rows:
        # `TaskRun.status` 是 SAEnum：正常情况下拿到的是 TaskStatus 成员，
        # 但驱动/方言差异下也可能是裸字符串 —— 两种都归一成 status 的取值。
        key = getattr(status, "value", None) or str(status)
        counts[key] = int(count)
    return counts, None


async def _pending_embedding_count() -> tuple[Optional[int], str]:
    """还没补嵌入的 chunk 数（`has_embedding = false`）；失败时返回 `(None, 原因码)`

    ⚠️ 与 `_task_status_counts` 一样是**只读**查询：就绪探针会被高频调用，
    探针绝不能成为数据变更的来源。失败**不**影响状态码 —— 它是报告项，
    与队列深度同一条判据（忙/积压 ≠ 坏）。

    Returns:
        (待嵌入行数, 来源)；测不到时行数为 `None`、来源是原因码
    """
    from sqlalchemy import func, select

    from .models.chunk import Chunk

    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            total = (
                await session.execute(
                    select(func.count()).select_from(Chunk).where(Chunk.has_embedding.is_(False))
                )
            ).scalar_one()
    except Exception as exc:
        # 细节只进日志：响应里回稳定原因码（与 DatabaseCheck 的说明同一口径）
        logger.warning("就绪检查：chunk 待嵌入数查询失败 | %s: %s", type(exc).__name__, exc)
        return None, "chunks_unavailable"
    return int(total), "chunks_table"


def _broker_queue_depth(cfg: Settings) -> tuple[Optional[int], str]:
    """broker 里等待投递的消息数（`task_runs` 看不到的那一段）

    ## 为什么必须问 broker，而不能只数 `task_runs`

    `task_runs` 的行是 **worker 接手时**才建的
    （`tasks/common.py::begin_task_run` → `task_run_service.ensure_task_run`，
    建出来就是 `running`）。也就是说消息在 broker 里排队的那段时间，
    数据库里**一行都没有** —— 只数 DB 会得到一个几乎恒为 0 的假指标，
    而"用户不知道还有多少任务在排队"正是计划（附录 E-7）要解决的问题。
    反过来 `pending` 这个状态在当前代码里基本不会出现（只有测试或将来
    "API 侧预建记录"才会写），所以它只作为辅助信息如实报出。

    ## 文件系统 broker 的计数口径

    kombu 的文件系统传输把每条消息写成一个 `{时刻}_{uuid}.{队列}.msg` 文件，
    消费者取走时**把文件移出该目录**（`Channel._get` 用 `shutil.move`），
    所以"目录里剩下的 `.{队列}.msg` 文件数"就等于"还没被取走的消息数"。
    这里与 kombu 自己的 `Channel._size(queue)` 是同一口径。

    ⚠️ 只按**后缀**匹配队列名：broker 目录里还住着控制消息
    （`...celery@主机名.celery.pidbox.msg`，本机实测残留了 15 个），
    把它们算进业务队列会让深度凭空多出十几 —— 而这类"指标虚高"
    一旦被当成基线，之后就再也没人看得出真正的积压。

    本项目把 `data_folder_in` / `data_folder_out` 都指向 `get_celery_broker_dir()`
    （Windows 上必须相同，见 `tasks/celery_app.py`），因此这一个目录就是整个
    等待队列；两者分开的部署需要同时数两个目录，届时这里要跟着改。

    ## 为什么 Redis 后端返回 None

    量 Redis 队列需要 `redis` 客户端，而它在 `requirements.txt` 里是**注释掉的**
    可选依赖。宁可如实报"测不到"（`None` + 原因码），也不要回一个假装是 0
    的数字 —— 后者会让一份坏掉的监控看起来一切正常。
    """
    if (cfg.celery_backend or "").strip().lower() == "redis":
        return None, "redis_broker_not_probed"

    folder = cfg.get_celery_broker_dir()
    suffix = f".{_CELERY_DEFAULT_QUEUE}.msg"
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        # 目录还不存在 = 一条消息也没投递过（broker 目录由生产者/worker 创建）
        return 0, "filesystem_broker"
    except OSError as exc:
        logger.warning("就绪检查：broker 目录不可读（%s）: %s", folder, exc)
        return None, "broker_dir_unreadable"
    return sum(1 for name in names if name.endswith(suffix)), "filesystem_broker"


# ---- 全局异常处理器：让所有 HTTP 错误响应体都携带 request_id ----
# 说明：路由层抛出的 HTTPException / RequestValidationError 由 FastAPI
# 内部的 ExceptionMiddleware 处理，不经过 ErrorHandlerMiddleware，
# 因此在这里注册处理器，保证 4xx/5xx 响应体统一为
# {"detail", "error_code", "request_id"} 格式，客户端可凭 request_id 定位日志。

def _error_payload(status_code: int, detail: "str | list", error_code: str) -> dict:
    """统一错误信封

    `detail` 的类型**随错误种类而不同**，这是刻意的：

    - 业务错误（`AppError` / `HTTPException`）：`detail` 是**一句面向用户的中文说明**；
    - 参数校验错误（422）：`detail` 是**数组**，与 `openapi.json` 里
      `HTTPValidationError.detail: List[ValidationError]` 一致 —— 见
      `validation_exception_handler` 的说明（按契约生成的客户端会当数组解析）。

    调用方一律按 `error_code` 分流，不要依赖 `detail` 的形状或文案。
    """
    return {
        "detail": detail,
        "error_code": error_code,
        "request_id": context.get_request_id() or None,
    }


async def http_exception_handler(request: Request, exc: HTTPException):
    logger.warning(
        "HTTP 错误 | %s %s | status=%d | detail=%s",
        request.method, request.url.path, exc.status_code,
        exc.detail if isinstance(exc.detail, str) else str(exc.detail),
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(
            exc.status_code,
            exc.detail if isinstance(exc.detail, str) else str(exc.detail),
            f"HTTP_{exc.status_code}",
        ),
        # 必须转发 exc.headers：否则路由里精心设置的
        # `headers={"WWW-Authenticate": "Bearer"}` 到不了客户端，
        # 401 响应会缺少 RFC 7235 要求的质询头，标准客户端无法据此重新认证。
        # 之前正是这里漏了一行，让 auth.py 里两处 401 的头形同虚设。
        headers=getattr(exc, "headers", None),
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """参数校验错误 → 统一信封，且 `detail` 保持 FastAPI 的**数组**形状

    ## 为什么 `detail` 必须是数组而不是一句 str（2026-09-23 修正）

    `openapi.json` 按 FastAPI 规范把 422 声明为
    `HTTPValidationError{ detail: List[ValidationError] }` —— 于是**任何**
    按契约生成的客户端都会把 `detail` 当数组解析。而这里此前塞的是
    `str(exc)`，形状与声明不符：生成的客户端在参数错误时会
    **在解析响应体时抛异常**，把"422 参数错误"变成"未知错误"。

    这也是"契约产物"的意义所在：`openapi.json` 是前端的类型来源，
    后端手写一个不同形状的响应，等于让那份契约在错误路径上撒谎。
    """
    errors = [
        {
            "type": str(err.get("type", "value_error")),
            "loc": [str(part) for part in err.get("loc", ())],
            "msg": str(err.get("msg", "")),
            # 刻意**不**回填 `input`：校验错误的输入里可能有口令、令牌、
            # 私有笔记正文，而 422 会进前端错误提示与日志。
        }
        for err in exc.errors()
    ]
    logger.warning(
        "参数校验错误 | %s %s | %d 处 | 首处=%s",
        request.method, request.url.path, len(errors),
        (errors[0]["loc"], errors[0]["msg"]) if errors else None,
    )
    return JSONResponse(
        status_code=422,
        content=_error_payload(422, errors, "VALIDATION_ERROR"),
    )


# LLM 配额耗尽（阶段 4.3）
#
# 为什么单独注册处理器，而不是在路由里 try/except：
# LLM 调用散布在理解任务、问答、语义判分等多条路径上（其中一部分在
# Celery 任务里），逐个包 try 必然有漏。配额异常从 `llm_service` 统一抛出，
# 在这里转成稳定错误码，客户端只需认 `error_code`（不随文案变化）。
async def llm_quota_exceeded_handler(request: Request, exc: LLMQuotaExceeded):
    logger.warning(
        "LLM 配额耗尽 | %s %s | %s", request.method, request.url.path, exc.detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(exc.status_code, exc.detail, exc.code),
    )


def create_app(config: Optional[Settings] = None) -> FastAPI:
    """构造并配置 FastAPI 应用实例

    ## 为什么是工厂，而不是模块级直接 `app = FastAPI(...)`

    阶段 0.10 之后，应用的一部分**构造期**行为取决于运行环境
    （schema 端点是否注册）。模块级构造会把这件事冻结在 import 那一刻，
    于是"验证生产姿态"与"验证开发姿态"必须在两个进程里各做一次 ——
    测试没法在一个进程内同时钉住两种姿态。
    工厂把姿态变成一个**显式入参**：默认仍是进程配置（`get_settings()`），
    测试可以传入另一份配置构造出另一种姿态来断言（见
    `tests/test_env_switches.py::TestSchemaEndpointGating`）。

    Args:
        config: 应用配置；缺省用进程级单例 `get_settings()`

    Returns:
        配置完成的 FastAPI 应用实例
    """
    cfg = config if config is not None else settings

    # 创建 FastAPI 应用实例
    # `debug=` 决定异常时是否把 traceback 回吐给客户端（阶段 4.10 起只看 app_env，
    # 与 SQL 日志、LLM 供应商无关）
    # `docs_url` / `redoc_url` / `openapi_url` 由运行环境决定（阶段 0.10）：
    # 生产姿态下三条路由都不注册 —— 理由见 `_schema_endpoint_kwargs`
    app = FastAPI(
        title=cfg.app_name,
        description="AI 驱动的学习笔记管理与知识库工具",
        version=__version__,  # 单一来源：app/version.py（见该文件说明）
        debug=cfg.is_dev,
        lifespan=lifespan,
        **_schema_endpoint_kwargs(cfg),
    )

    # ===================================================================
    # 中间件注册 —— 次序是这里最容易搞错的东西，先读这段再动
    # ===================================================================
    #
    # ## starlette 的次序语义
    #
    # `add_middleware` 是**前插**（`user_middleware.insert(0, ...)`），build 时
    # 用 `reversed()` 逐层包裹 —— 于是**后注册的在外层**，请求先到它，
    # 响应最后从它出去。（这条语义由
    # `tests/test_cors_middleware_order.py::test_starlette_prepend_semantics_are_what_we_assume`
    # 钉住，别在这里凭印象推。）
    #
    # ## 生效的嵌套（外 → 内，实测）
    #
    #     ServerErrorMiddleware          starlette 自带，永远在最外
    #       RateLimitMiddleware          限流（最外层用户中间件）
    #         RequestContextMiddleware   request_id / user_id / 访问日志
    #           CORSMiddleware           ← 必须在错误渲染器**外侧**
    #             ErrorHandlerMiddleware 把 AppError / 未知异常渲染成统一信封
    #               ExceptionMiddleware  starlette 自带（HTTPException / 校验错误）
    #                 APIRouter
    #
    # ## 为什么 CORS 必须紧贴 ErrorHandler 的外侧（本次修的缺陷）
    #
    # 一个中间件**自己造出来**的响应，只会经过它外侧那些中间件的 send 包装。
    # `AppError` 是 `ErrorHandlerMiddleware` 自己构造响应的（异常走到它那里就被
    # 吃掉了），所以 CORS 只要在它里侧，这个响应就永远不会被加上
    # `Access-Control-Allow-Origin`；而 `HTTPException` 由最内层的
    # `ExceptionMiddleware` 渲染，响应必须穿过 CORS 才出得去，于是一直带着该头。
    #
    # 阶段 0.11 把 152 处 `HTTPException` 迁到 `AppError`，等于把**所有**业务错误
    # 从"带头的那条渲染路径"搬到了"不带头的那条"：状态码与响应体一模一样，
    # 服务端日志里也看不出差别，只有跨域时浏览器会把它们变成不可读的 CORS 失败
    # （前端连 4xx 状态码与 error_code 都拿不到）。今天前端走 Vite 同源代理，
    # 所以没坏 —— 这正是它值得现在就修的原因：它是一颗静默的雷。
    # 判据、证据与结构断言见 `tests/test_cors_middleware_order.py`。
    #
    # ## 为什么 CORS 不干脆放到最外层（另一个真实取舍）
    #
    # 放到最外层会顺带改掉两件**本不属于本次修复**的事：
    #   1. 预检（OPTIONS）由 CORSMiddleware 自己应答、不经过路由，放最外层后它
    #      就不再经过 RequestContextMiddleware —— 预检响应会丢 `X-Request-ID`、
    #      也不再进访问日志；
    #   2. 限流中间件短路返回的 429 会从"没有 ACAO"变成"有 ACAO"（它今天是
    #      最外层用户中间件，同样绕过了 CORS）。
    # 因此这里取**最小位移**：只把 CORS 从 ErrorHandler 里侧挪到外侧，
    # 其余三个中间件的相对次序一个都没动 —— 于是"哪个中间件看到哪个异常"
    # 完全没变（ErrorHandler 仍是 AppError 唯一的捕获者；RequestContext 仍在
    # 外侧把 4xx/5xx 记进访问日志）。
    #
    # ⚠ 已知不一致（**本次未修**，另立任务）：RateLimitMiddleware 注册得比
    # RequestContextMiddleware 更晚 ⇒ 它实际在最外层，跑在请求上下文注入**之前**。
    # 后果是 `rate_limit._client_key` 读不到 `context.get_user_id()`，
    # "按已认证用户计数"那条分支从未生效（一直按 IP 计数，即上面的注释原文
    # "限流器因此能读到已认证用户"与事实相反）；它返回的 429 里
    # `request_id` 也恒为 None。属行为变更，需要单独评估再动。
    # ===================================================================

    # 最内层用户中间件：统一错误渲染器
    app.add_middleware(ErrorHandlerMiddleware)
    # ↓ 紧贴其外：错误响应必须穿过它，否则丢掉 Access-Control-Allow-Origin
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.get_cors_origins(),  # 从配置解析（默认 Vite/CRA 本地端口）
        # 默认 False：本项目不用 Cookie，认证走 Authorization 头（见 config.py 说明）
        allow_credentials=cfg.cors_allow_credentials,
        allow_methods=["*"],       # 允许所有 HTTP 方法
        allow_headers=["*"],       # 允许所有请求头
    )
    app.add_middleware(RequestContextMiddleware)
    # 最外层用户中间件：限流（越早拒绝越省事）
    app.add_middleware(RateLimitMiddleware)

    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(LLMQuotaExceeded, llm_quota_exceeded_handler)

    # 注册所有 API 路由，统一挂载到 /api 前缀下
    app.include_router(api_router, prefix="/api")

    @app.get("/health", response_model=HealthResponse)
    async def health_check():
        """
        健康检查端点（**存活**探针，不检查任何依赖）

        用于监控和负载均衡器检测服务是否正常运行。
        不需要认证，返回应用名称和状态。

        ⚠️ 刻意**不**在这里检查数据库/broker：本端点会被用来决定"要不要重启
        进程"，任何依赖抖动都会被翻译成重启（见 `/ready` 上方的说明）。
        "能不能干活"是 `/ready` 的问题。

        Returns:
            dict: 包含 status 和 app 名称的字典
        """
        return {"status": "ok", "app": cfg.app_name}

    @app.get(
        "/ready",
        response_model=ReadinessResponse,
        responses={
            503: {
                "model": ReadinessResponse,
                "description": "未就绪：数据库不可达或 schema 未就绪（响应体形状与 200 相同）",
            },
        },
    )
    async def readiness_check():
        """
        就绪检查端点（**readiness** 探针，含义见本模块 `/ready` 上方说明）

        判据只有一条：**数据库连得上且 schema 就绪**（用一次真实业务查询验证，
        而不是 `SELECT 1`）。就绪时 200，否则 **503**，两种情况的响应体形状相同。

        队列深度是**报告项**，不参与状态码：忙 ≠ 坏，详见上面
        "队列深度为什么只报告、不决定就绪"。

        **索引积压**（`index.pending_embeddings`：还没补向量的 chunk 数）同样是报告项：
        它说明检索质量暂时降级（这些内容只被 BM25 检索到），不说明实例坏了。

        不需要认证（探针不会带 Token），因此响应里只有稳定的状态与原因码，
        不含异常文本、库路径等内部信息。
        """
        # 重新取一次配置，而不是用构造期的 `cfg`：`/ready` 读的是**当前生效**的
        # broker 后端与目录，而模块级 `settings` 是 import 那一刻冻结的那一份
        # （测试还会清 `get_settings` 的缓存换一个实例）。
        cfg_now = get_settings()
        counts, db_reason = await _task_status_counts()
        db_ok = counts is not None
        depth, depth_source = _broker_queue_depth(cfg_now)
        # 库都没连上时给 `None` 而不是 0："没测到"与"真的没有积压"必须能分开
        pending_embeddings, index_source = (
            await _pending_embedding_count() if db_ok else (None, "database_unavailable")
        )

        payload = ReadinessResponse(
            status="ready" if db_ok else "not_ready",
            app=cfg.app_name,
            database=DatabaseCheck(
                status="ok" if db_ok else "error",
                reason=db_reason,
            ),
            queue=QueueDepth(
                depth=depth,
                # 数据库已给出答案时，"没有这种状态的行"就是 0，不是"测不到"：
                # 空表意味着此刻确实没有任务在跑/在等（`None` 只留给
                # "库都没连上，无从统计"这一种情况）。
                running=None if not db_ok else counts.get("running", 0),
                pending=None if not db_ok else counts.get("pending", 0),
                source=depth_source,
            ),
            index=IndexBacklog(
                pending_embeddings=pending_embeddings,
                source=index_source,
            ),
        )
        return JSONResponse(
            status_code=200 if db_ok else 503,
            content=payload.model_dump(),
        )

    return app


# 模块级应用实例：uvicorn 的入口（`app.main:app`）与全部既有导入点都指向它。
# 它由工厂构造，因此与测试里 `create_app(cfg)` 走的是**同一条**代码路径。
app = create_app()
