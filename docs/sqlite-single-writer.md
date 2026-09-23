# SQLite 单写者路线：决策记录与运维约束

> 本文是 `docs/overhaul-plan.md` 第六部分决策 **D1 / D2 / D5** 的落地说明。
> 该表原本推荐 PostgreSQL + pgvector，但受"不使用 Docker（磁盘空间不足）"
> 约束，实际执行路线为 **D1=B / D2=本地单用户 / D5=保留 Chroma**。
> ⚠️ **D5 这一半已作废（2026-09-23 核对）**：Chroma 已在阶段 2.4 收尾时移除 ——
> 向量与定位字段同存 `chunks` 表（`backend/app/models/chunk.py:89,154`）、
> 词法检索改用 SQLite FTS5（`backend/app/models/chunk.py:142-149`），
> 依赖也已摘掉（`backend/requirements.txt:60`；`backend/app/config.py` 的
> `chroma_dir` 移除说明段，核对时为 `:283-292`）。
> 本文其余部分（单写者约束）与 D5 无关，不受影响。
> 本文把这条路线下**必须遵守的约束**写成可执行的规则，避免每次推进都重新争论。

## 决策

| 编号 | 原推荐 | 实际选择 | 理由 |
|---|---|---|---|
| **D1** 数据库 | PostgreSQL | **SQLite + WAL** | 无 Docker 环境，PG 无法部署 |
| **D2** 部署形态 | 服务端多用户 | **本地单用户自托管** | 与 D1 一致；单用户下大量并发问题自然消失 |
| **D3** 调度算法 | FSRS | **暂缓**（仍 SM-2 + 四档自评） | 复习记录不足以拟合参数（见下）→ **已作废**：阶段 3.6 已换 FSRS-5（公开默认参数，个人化拟合仍未做），`config.py` 的 `review_scheduler` 默认 `"fsrs"`（核对时为 `:448`）（2026-09-23 核对） |
| **D4** 简答判分 | LLM 语义判分 | **用户四档自评**（已落地） | 自评是最准的信号，且零 token 成本 |
| **D5** 向量库 | pgvector | **保留 Chroma**（已修相似度公式）→ **Chroma 已移除**（阶段 2.4 收尾）：向量改存 `chunks` 表、词法检索改 SQLite FTS5 | 无 PG；`sqlite-vec` 评估留待阶段 2（2026-09-23 核对） |
| **D6** 前端框架 | 保留 React | **保留 React** | 问题在架构不在框架 |

## 约束：只允许一个写者

**这是本路线下唯一不可协商的约束。**

SQLite 全库只有一个写锁。WAL 模式允许"一写多读"并发，但**不允许多写**。

### 为什么不能靠 `busy_timeout` 兜住

`busy_timeout` 已设为 30 秒。多写者场景下它的表现不是"干脆失败"，而是：

- 请求莫名卡 30 秒然后 500 —— 用户看到的是"页面转半天然后报错"
- 更隐蔽的是**进程内状态各自为政**：
  - `RateLimitMiddleware` 的滑动窗口是进程内字典，N 个进程 = 限流阈值放大 N 倍
  - `tasks/common.py` 的引擎与进度缓存也是进程内的
  - 于是"限流明明配了 5/min 却能被刷 5×N 次"

这类问题**不会被单元测试发现**（测试是单进程的），只在部署后以"偶发 500 /
限流失效"的形态出现。

### 强制手段

`app/main.py::_enforce_single_writer()` 在 FastAPI 启动阶段检查
`WEB_CONCURRENCY` / `UVICORN_WORKERS`，**大于 1 且数据库是 SQLite 时直接启动失败**，
并在错误信息里给出两个选项（固定单写者 / 迁 PG）。

刻意选择"拒绝启动"而不是"自动降为 1"：静默降级会让运维以为多进程部署成功了，
实际只跑一个 worker —— 一个"看起来正常但能力被悄悄削掉"的服务比启动失败更糟。

### 部署要求

```bash
# 正确：单进程（uvicorn 默认即 1，无需显式设置）
uvicorn app.main:app --host 0.0.0.0 --port 8001

# 显式声明也可以
WEB_CONCURRENCY=1 uvicorn app.main:app --port 8001

# 错误：启动即失败
uvicorn app.main:app --workers 4      # -> RuntimeError，附带修复指引
```

> （2026-09-23 核对）上面的端口原先写作 **8000**，本项目实际是 **8001**
> —— 见 `start.bat:16`、`start.sh:14`、`frontend/vite.config.ts:34`（proxy target）。

Celery worker 侧同理：`celery worker -c 1`。
（原因不只是单写者 —— 每个 worker 进程会各加载一份约 2.3GB 的嵌入模型，
prefork 默认按 CPU 核数会直接 OOM。这一点在 `config.py:228` 已有注释。）
> （2026-09-23 核对：该注释原引 `config.py:141`，现实际在 `:228`。）
> ⚠️ 同时核对出一个**脚本与要求的缺口**：`start.bat:167` 用 `--pool=solo`
> （Windows 单进程，等效 1 个 worker），但 `start.sh:149` **没有**传 `-c 1`，
> 走的是 prefork 默认池（= CPU 核数）—— 即这条"单写者"要求在 Linux 启动脚本上
> 并未落实（`celery_app.py` 也未设置 `worker_concurrency`）。
> 本文件不改启动脚本，仅记录该事实。

## 本路线下**接受**的取舍

不再试图消除以下问题，它们是路线的固有代价：

| 取舍 | 影响 | 触发迁移的条件 |
|---|---|---|
| 不支持高并发写入 | 定位为单用户/小团队自托管 | 需要多用户并发写时迁 PG |
| 无水平扩展 | 只能单机 | 单机性能不足时迁 PG |
| 限流是进程内的 | 多进程部署时限流不准（已被启动守卫挡住） | 同 D1 |
| 无跨进程任务队列 | 文件 broker 无 visibility timeout，靠 DB 侧心跳自愈补偿 | 需要可靠队列时引入 Redis |

## 已落地的补偿机制

因为选了"更弱的存储"，所以必须在应用层补回可靠性：

| 机制 | 位置 | 补的是什么 |
|---|---|---|
| WAL + `busy_timeout=30s` + `synchronous=NORMAL` | `database.py` | 读写不再互相阻塞 |
| 启动零破坏性迁移 | `database.py`（破坏性变更需 `ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION=1`） | 防"启动时 DROP 表"造成数据丢失 |
| 孤儿数据只报告不删除 | `database.py` | `review_logs` 是不可再生的学习证据 |
| 任务进度与心跳（`task_runs` 表） | `models/task_run.py`、`services/task_run_service.py` | 文件 broker 没有可见性超时，UI 至少能看到真实进度 |
| 僵尸任务自愈（Beat 每 5 分钟） | `tasks/maintenance_tasks.py` | worker 崩溃后笔记不再永久卡在 converting |
| 定时备份 + 保留策略（Beat 每日 03:30） | `services/backup_service.py` | "需要人记得"的备份等于没有备份 |
| 恢复演练入口 | `scripts/restore_db.py` | 没有恢复演练的备份不算备份 |
| 测试禁止写真实库 | `tests/conftest.py` 的 `before_cursor_execute` 守卫 | 防止测试静默污染生产数据 |

## 何时应当放弃这条路线

出现以下任一情况，应回到 `overhaul-plan.md` 手术刀 1（迁移 PostgreSQL）：

1. 需要**多用户同时写**（不是"多人用"，而是"多人同时提交"）
2. 需要水平扩展（多实例负载均衡）
3. 单机写入吞吐成为瓶颈（实测：当前 1058 道题、194 条复习记录量级下远未达到）
4. 需要 pgvector / `pg_bigm` 这类只有 PG 生态才有的能力

迁移路径在 `overhaul-plan.md` 阶段 1（1.1–1.14）中已完整写明，包括
"丢弃现有 10 个迁移、以模型为唯一源重建 Alembic 基线"这个关键决策 ——
（2026-09-23 核对：该数字是计划写作时的口径（`overhaul-plan.md:101` 写 `001–010`）；
`backend/alembic/versions/` 现存 **11** 份，001–011，多出 `011_refresh_tokens.py`。）
**那条路径仍然有效**，本路线的所有应用层改动（自评闭环、任务追踪、
备份、契约修复）在 PG 上同样成立。
