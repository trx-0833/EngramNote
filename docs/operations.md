# 运维手册（Operations）

> 面向：部署/维护本实例的人（含未来的你）
> 现状：**单机本地进程**运行，SQLite 单写者，无容器编排
> 相关决策：`docs/sqlite-single-writer.md`、`docs/decisions.md`、`docs/architecture.md`

---

## 1. 运行形态

| 组件 | 端口 | 启动方式 | 说明 |
|---|---|---|---|
| 后端 API | 8001 | `python -m uvicorn app.main:app` | 实际进程 |
| 前端 | 5173 | `npm run dev`（或构建后由 nginx 托管） | 开发形态 |
| Celery Worker | — | `celery -A app.tasks.celery_app:celery_app worker` | 嵌入模型隔离在此进程 |
| Celery Beat | — | `celery -A app.tasks.celery_app:celery_app beat` | 定时任务（备份/刷新/复习邮件） |

**硬约束**：SQLite 只允许**一个** worker（每多一个就多加载一份嵌入模型）。详见 `docs/sqlite-single-writer.md`。

---

## 2. 日志：四路输出，各管一件事

启动时 `app/core/logging_config.py` 会挂四个 handler（`logging_config.py:setup_logging`）：

| 输出 | 文件 | 格式 | 用途 |
|---|---|---|---|
| 控制台 | — | 文本，带上下文 | 交互式排障 |
| 全量文本 | `engramnote.log` | 文本，10MB 轮转 × **7** 份 | 人读的主日志 |
| 错误专档 | `errors.log` | 文本，仅 ERROR+，10MB × **10** | 快速定位异常 |
| **结构化** | `engramnote.json.log` | **JSON Lines**，10MB × **30** | 机器可查、可导入分析 |

轮转份数按用途分层（2026-11-08）：文本主日志留一周足够、错误专档量最小、
JSON 是机器排障主通道所以留最久。合计上限从 **900MB**（改造前三份都是 30 份）
降到约 **470MB**。配置项：`LOG_MAX_BYTES`（三份共用）+ `LOG_TEXT_BACKUP_COUNT` /
`LOG_JSON_BACKUP_COUNT` / `LOG_ERROR_BACKUP_COUNT`；老键 `LOG_BACKUP_COUNT`
仍然有效（显式设了它、又没设新键时，三份都回落到它）。

日志目录由 `LOG_DIR` 决定，默认在 `backend/data/logs/`（`config.py:get_log_dir`）。

**每条日志自动携带上下文**（`core/context.py` 的 contextvars）：
```
request_id / user_id / task_id / task_name / biz_id / locale
```
格式串见 `logging_config.py:LOG_FORMAT`：
```
%(asctime)s | %(levelname)-8s | %(name)s | %(ctx)s | %(message)s
```

**两条可机读的硬约定**（2026-11-08 起，`backend/tests/test_logging_contract.py` 守着）：

- **时间戳带毫秒**：`2026-11-07 20:43:00.227 | INFO | …`。以前只有秒，
  同一秒内的多条只能靠文件行号排序。
- **一条记录一行**：消息里的换行被转义成字面量 `\n`。以前上游 4xx 的响应体
  会把一条日志拆成两条物理行（实测占 29.36%），`grep` 只拿到前半句。
  异常堆栈仍占多行 —— 那是它本体；JSON 日志里统一收进单行的 `exc` 字段。

**⚠️ 测试不会再写这两个目录**：`tests/conftest.py` 把 `LOG_DIR` 会话级重定向到
临时目录（否则跑一次 `pytest` 会往真实日志里写几百行从未发生过的"请求"）。

---

## 3. 排障入口：从 request_id 开始

这是本系统最重要的排障路径，因为**错误响应体里已经带了 request_id**。

**用户报错时**，让他提供：
- 界面上的错误提示（前端会把 `request_id` 展示出来）
- 或者浏览器 Network 面板里响应体的 `request_id` 字段
- 或者响应头 `X-Request-ID`

**然后在日志里查**：
```bash
# 文本日志（"请求完成"这一行现在也带 rid/uid —— 2026-11-07 之前它一条都没有）
grep "rid=<request_id>" backend/data/logs/engramnote.log

# 结构化日志（更好：能精确取字段）
# 字段名以 JsonLinesFormatter 为准 —— ts/level/logger/message/ctx/exc，
# 另有业务 extra 白名单：event/status/duration_ms/method/path/error_code/scene/provider/model
grep "<request_id>" backend/data/logs/engramnote.json.log \
  | jq -c '{ts, level, logger, msg: .message, rid: .ctx.request_id, status, duration_ms}'
```

**访问日志已结构化**，不要再拿正则啃 message：
```bash
# 最近的 4xx/5xx 请求（含耗时与路径）
jq -c 'select(.event == "http.request" and .status >= 400)
       | {ts, path, status, duration_ms, rid: .ctx.request_id}' \
   backend/data/logs/engramnote.json.log

# 某接口的耗时分布（SLO 第 4 节那条 P95 就是用它算的）
jq -s '[.[] | select(.event == "http.request" and .path == "/api/notes") | .duration_ms]
       | sort | .[length * 95 / 100 | floor]' backend/data/logs/engramnote.json.log
```

**时区**：JSON 的 `ts` 是**本地时间且带偏移**（`2026-11-07T20:43:00.227+08:00`），
而数据库里的时间（如 `llm_calls.created_at`）是 **UTC** —— 跨库对时间时记得换算，
别再把 8 小时差当成数据错乱。

**排查顺序**：
1. `errors.log` 里按 request_id 找异常堆栈
2. `engramnote.json.log` 里看同一 rid 的完整调用链（含 LLM 调用耗时）
3. 若涉及异步任务，再用 `task_id` 查 worker 侧日志

---

## 4. SLO

| 指标 | 目标 | 测量方式 |
|---|---|---|
| 可用性 | `/ready` 返回 200 的比例 ≥ 99%（7 天滚动） | 外部探针或手工抽查 |
| 核心接口延迟 | `/api/notes` P95 < 800ms | `engramnote.json.log` 里 `event="http.request"` 的 `duration_ms`（jq 配方见 §3） |
| 异步任务成功率 | 清洗/嵌入任务失败率 < 2% | worker 日志中 `cleaning_failed` / 重试次数 |
| 备份新鲜度 | 最近一次成功备份 < 26 小时 | 检查 `_backup/` 最新目录时间 |

**超阈值的第一动作**：
1. `/ready` 不 200 → 看它返回的原因码（`app/main.py` 里 `/ready` 用稳定原因码，如 db_unreachable / schema_missing）
2. 延迟劣化 → `jq 'select(.event == "http.request")'` 按 `path` 分组看 `duration_ms` 的分布（字段已结构化，不用再解析 message）
3. 任务失败率上升 → 看 worker 是否卡在模型加载（内存）或被限流

**不要**：把 `/ready` 的失败当成"重启一下就好"。它区分了"进程活着"与"能服务请求"，重启会掩盖原因。

---

## 5. 常见故障 → 处置

| 现象 | 先查 | 处置 |
|---|---|---|
| 页面能开但接口全 500 | `/ready` 原因码、`errors.log` | 数据库文件是否存在/可写；`PRAGMA integrity_check` |
| 上传大文件返回 413 | 是否走了 nginx | nginx 需 `client_max_body_size`（见 `frontend/nginx.conf`） |
| 流式问答一次性吐出 | 反代是否缓冲 | nginx 需 `proxy_buffering off` |
| 复盘提醒没发 | Beat 是否在跑、SMTP 配置 | 看 `celery-beat` 进程与 `task_id` 日志 |
| 备份很久没更新 | `_backup/` 最新目录时间 | Beat 未跑则手动跑 `scripts/backup.ps1`（见第 6 节） |
| 内存持续上涨 | worker 是否多开、模型是否重复加载 | 只保留 1 个 worker；确认嵌入模型在 worker 内 |
| LLM 调用超时 | `engramnote.json.log` 里的 provider 耗时 | 检查 Key 配额与网络；确认是否命中降级路径 |

---

## 6. 备份与恢复

**两套并存，写不同目录，不冲突**：

| 触发方式 | 目录 | 依赖 | 状态 |
|---|---|---|---|
| 应用内 `backup_service.py` | `_backup/<日期>-daily/` | Celery Beat 在跑 | 最新快照 `20261018`（已停摆） |
| 系统计划任务 `scripts/backup.ps1` | `_backup/scheduled/<时间戳>/` | 注册计划任务 | **未启用**（脚本已就位） |

**手动备份**（任何时候都能做）：
```powershell
powershell -NoProfile -File scripts\backup.ps1
```

**恢复**：
```powershell
# 恢复到临时库，先验证再覆盖
python backend\scripts\restore_db.py --src <备份目录>\engramnote.db --dst .tmp\restore-test.db
# 验证条数
python -c "import sqlite3;print(sqlite3.connect(r'.tmp\restore-test.db').execute('select count(*) from notes').fetchone())"
```

**红线**：`backend/.env` 含真实配置（与 `.env.example` 不同），**绝不随备份一起分发**；本手册与 `scripts/backup.ps1` 都刻意不复制它。

---

## 7. 升级

见 `UPGRADING.md`。要点：
- 升级前先备份数据库
- alembic 迁移先用 `--sql` 预演
- 迁移不可逆时，保留旧库副本

---

## 8. 已知缺口（诚实记录）

| 缺口 | 影响 | 计划 |
|---|---|---|
| 无外部错误聚合（无 Sentry/OTel） | 只能靠用户报 request_id 才能定位 | 决策为"暂不接"，见 `_governance/04-待决策清单.md` D-05 |
| 无 SLO 自动告警 | 靠手工抽查 | 未排期 |
| 备份触发器在应用内 | 不用 `start.sh` 起服务就不备份 | 已备 `scripts/backup.ps1`，待注册计划任务 |
| 无生产启动脚本 | 当前 `start.sh` 是开发模式（`--reload`） | 治理计划 T4.1 |
| 覆盖率与变异测试未接入 | 测试有效性无数据 | 变异测试基线进行中 |

---

## 9. 快速排障 FAQ

> 三条最常被误判成"系统坏了"的现象。按"现象 → 判据 → 依据"排，先排除这三条，再进第 5 节。

### Q1. `/health` 返回 200，页面接口却大面积 500 —— 该拿哪个端点当判据？

`/health` 只回答"进程还活着"：它刻意不碰数据库、broker 与外部服务（`backend/app/main.py:284-291`、`main.py:833-835`），事件循环能回 JSON 就是 200，所以接口全挂时它照样绿。**能否服务请求看 `/ready`**：它用一次真实业务查询（`task_runs` 分组统计）验证"连得上**且**表齐"，失败回 503、成功回 200，两种响应的字段结构相同（`main.py:407-448`、`main.py:890-917`）。

| 端点 | 回答的问题 | 探针用途 | 失败时 |
|---|---|---|---|
| `/health` | 进程还活着吗 | 存活（liveness），决定"重启与否" | 恒 200，依赖抖动不反映 |
| `/ready` | 现在能不能服务 | 就绪（readiness），决定"摘不摘流量" | 503 + `database.reason`（如 `database_unavailable`） |

`queue.depth` 与 `index.pending_embeddings` 是**报告项**，不参与状态码（`main.py:296-303`、`main.py:862-863`）：积压说明慢，不说明坏。500 的堆栈在 `errors.log`，它只收 ERROR 及以上（`backend/app/core/logging_config.py:141`），用响应体里的 `request_id` 检索。

### Q2. 响应体里的 `request_id` 是 `null`（429 最常见）—— 日志系统坏了吗？

不是日志的问题，是中间件次序：`RateLimitMiddleware` 注册得最晚 ⇒ 实际在**最外层**，跑在 `RequestContextMiddleware` 注入上下文之前，它短路返回的 429 里 `context.get_request_id()` 取到空串，于是 `request_id` 落成 `null`、响应头也没有 `X-Request-ID`（`backend/app/middleware/rate_limit.py:235-242`；次序见 `main.py:804-816` 与 `main.py:795-800` 的已知不一致说明）。

正常路径：`RequestContextMiddleware` 优先透传请求头 `X-Request-ID`（≤64 字符、仅字母数字与连字符），否则生成 uuid4 hex，并对每个响应写 `X-Request-ID`（`backend/app/middleware/request_context.py:94-99`、`request_context.py:138`）；错误响应体的 `request_id` 取自同一份 context（`backend/app/middleware/error_handler.py:113-117`）。

定位这类 429 改用 `限流触发` 日志行，`key=ip:<ip>:<rule>` 即计数键，窗口 60 秒（`rate_limit.py:231-234`、`rate_limit.py:43`）。

### Q3. `verify.sh fast` 全绿，推到 CI 却红 —— 为什么本地测不出来？

因为 `fast` 档**完全不跑后端测试**。它只做三件事：`ruff check`、`eslint`、`vitest`（见 `scripts/verify.sh` 的 `MODE=fast` 分支），pytest 被有意排除在外。

后端只要改了逻辑，就必须单独跑：

```bash
cd backend && python -m pytest -q        # 1760 用例 / 约 32 分钟
```

CI 比你多三道关卡：

| 关卡 | 本地 `fast` | CI |
|---|---|---|
| ruff / eslint / vitest | ✅ | ✅ |
| pytest 全量（1760 用例） | ❌ | ✅ |
| 覆盖率门槛 59% | ❌ | ✅ `--cov-fail-under=59` |
| 契约漂移检查 | ❌ | ✅ |

**判据**：改过 `backend/app/**` 而没跑 pytest，就不能用 `fast` 的绿灯下结论。跑不动全量时，至少跑相关文件：

```bash
cd backend && python -m pytest tests/test_<相关模块>.py -q
```

顺带一个反例：`scripts/backup.ps1` 的保留策略曾在 2026-11-07 出现过「变量未赋值 → 循环一次不进 → 脚本 exit 0 但旧备份永不删除」的静默失败。**"脚本不报错"从来不等于"逻辑执行了"**，判据要落在行为上（那次是靠造 8 个不同年龄的快照、逐条核对是否保留下来的）。
