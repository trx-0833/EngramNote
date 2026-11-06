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

启动时 `app/core/logging_config.py` 会挂四个 handler（`logging_config.py:136-143`）：

| 输出 | 文件 | 格式 | 用途 |
|---|---|---|---|
| 控制台 | — | 文本，带上下文 | 交互式排障 |
| 全量文本 | `engramnote.log` | 文本，10MB 轮转 × 30 份 | 人读的主日志 |
| 错误专档 | `errors.log` | 文本，仅 ERROR+ | 快速定位异常 |
| **结构化** | `engramnote.json.log` | **JSON Lines** | 机器可查、可导入分析 |

日志目录由 `LOG_DIR` 决定，默认在 `backend/data/logs/`（`config.py:get_log_dir`）。

**每条日志自动携带上下文**（`core/context.py` 的 contextvars）：
```
request_id / user_id / task_id / task_name / biz_id
```
格式串见 `logging_config.py:37`：
```
%(asctime)s | %(levelname)-8s | %(name)s | %(ctx)s | %(message)s
```

---

## 3. 排障入口：从 request_id 开始

这是本系统最重要的排障路径，因为**错误响应体里已经带了 request_id**。

**用户报错时**，让他提供：
- 界面上的错误提示（前端会把 `request_id` 展示出来）
- 或者浏览器 Network 面板里响应体的 `request_id` 字段
- 或者响应头 `X-Request-ID`

**然后在日志里查**：
```bash
# 文本日志
grep "<request_id>" backend/data/logs/engramnote.log

# 结构化日志（更好：能精确取字段）
grep "<request_id>" backend/data/logs/engramnote.json.log | jq -c '{ts:.asctime, lvl:.levelname, msg:.message}'
```

**排查顺序**：
1. `errors.log` 里按 request_id 找异常堆栈
2. `engramnote.json.log` 里看同一 rid 的完整调用链（含 LLM 调用耗时）
3. 若涉及异步任务，再用 `task_id` 查 worker 侧日志

---

## 4. SLO

| 指标 | 目标 | 测量方式 |
|---|---|---|
| 可用性 | `/ready` 返回 200 的比例 ≥ 99%（7 天滚动） | 外部探针或手工抽查 |
| 核心接口延迟 | `/api/notes` P95 < 800ms | 从 `engramnote.json.log` 的访问日志聚合 |
| 异步任务成功率 | 清洗/嵌入任务失败率 < 2% | worker 日志中 `cleaning_failed` / 重试次数 |
| 备份新鲜度 | 最近一次成功备份 < 26 小时 | 检查 `_backup/` 最新目录时间 |

**超阈值的第一动作**：
1. `/ready` 不 200 → 看它返回的原因码（`app/main.py` 里 `/ready` 用稳定原因码，如 db_unreachable / schema_missing）
2. 延迟劣化 → 看 `engramnote.json.log` 里慢请求的 endpoint 分布
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
