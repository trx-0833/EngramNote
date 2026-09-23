# 升级指南（UPGRADING）

面向**已经在跑 EngramNote 的人**。回答三个问题：

1. 换代码之后，我的数据库会不会被改坏？
2. 升级前该做什么？
3. 有没有"启动就报错"的坑要提前处理？

> 一句话结论：**schema 是前滚的（只加列、不删数据），启动路径不调用 alembic。**
> 但**升级前请备份**——这是你唯一的回退手段。

---

## 1. schema 是怎么演进的

### 1.1 启动路径只做两件安全的事

`backend/app/database.py:260-276` 的 `init_db()` 明确写了它的职责：

1. `Base.metadata.create_all()` —— **只创建不存在的表**，不触碰已有表；
2. `_migrate_sqlite()`（`backend/app/database.py:437`）—— **只补加缺失的列**
   （`create_all()` 不会对已有表做 `ALTER TABLE`），外加防御性建表与
   **只读**的孤儿检查（只报告，不删）。

此外还有一步：SQLite 上创建 FTS5 全文索引虚拟表（幂等，`database.py:293-297`）。

**这三步都是无损的**（`database.py:262`、`:289` 的注释把判据写在代码里）。

### 1.2 破坏性操作已移出启动路径（需显式闸门）

`_rebuild_dangling_tables()`（把若干张表的**外键端改为可空**，SQLite 只能
"建新表 → 拷数据 → 改名"）与 `card_relations` 的全局同键去重，**不再随进程启动执行**：

- 判据函数 `_destructive_migration_allowed()`：`backend/app/database.py:390-399`；
- 默认行为：**不执行**（未设置该环境变量时返回 `False`）；
- 放行取值：环境变量 `ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION` 等于
  `1` / `true` / `True`（**大小写敏感**，`database.py:399`）；
- 涉及的两处：`card_relations` 同键去重（`database.py:1057-1083`）、
  破坏性表重建（`database.py:1163`、`:1228-1235`）。

**为什么默认禁止**：本项目同时跑 API / Celery worker / Celery beat 三个进程，
原先它们**每个进程启动时都无条件执行**这两个操作；任何一次中断（断电、OOM、Ctrl-C）
落在 `DROP TABLE` 与 `RENAME` 之间，就会丢掉用户的知识图谱数据
（`database.py:272-275`）。启动路径必须是无损的。

### 1.3 需要显式执行的破坏性迁移：用脚本，不要手改环境变量乱跑

如果你的库是**很早的版本**建的，可能停留在旧的 `NOT NULL` schema 上
（典型症状是 `review_logs.quiz_id` 相关的报错）。项目提供了一次性入口
（`backend/scripts/reconcile_schema.py`，文件头 `:1-33` 写明了原委）：

```bash
cd backend
python scripts/reconcile_schema.py --check     # 只探测，不改动（先跑这个）
python scripts/reconcile_schema.py             # 实际重建（会自动先备份）
```

它是"显式、单次、可校验"的：**备份 → 探测 → 重建 → 逐项校验 → 失败可回退**
（`scripts/reconcile_schema.py:19-21`）。注意它**不是** `init_db()` 的一部分，
而且这是**有意的**：把重建放回启动路径会重新引入"一次断电丢数据"的风险
（`scripts/reconcile_schema.py:23-27`）。

### 1.4 alembic 是历史遗留：**启动路径不调用它**

- `backend/alembic/` 与 `backend/alembic.ini` 保留在仓库里，但**启动路径不调用**
  （`backend/requirements.txt:69-72`）。
- `backend/alembic.ini` 里的 `sqlalchemy.url` 仍指向 `postgresql+asyncpg://...`，
  与当前的 SQLite 路线**不匹配**（`docs/overhaul-plan.md:2982`）。
- 结论：**schema 演进只有 `init_db()` + `_migrate_sqlite()` 这一条通道**，
  不要试图用 `alembic upgrade head` 来升级你的库
  （`docs/overhaul-plan.md:2981-2982` 记录了迁移链"从未生效过"的三条独立证据）。

---

## 2. 升级步骤（推荐顺序）

```bash
# 0) 停掉进程（API + Celery worker + beat）—— 三个进程都可能写库
#    停止方式取决于你怎么起的：start.bat / start.sh，或三个终端 Ctrl-C

# 1) 备份（见下一节，必做）
cd backend
python scripts/backup_db.py --label pre-upgrade

# 2) 取新代码（本仓库不使用 alembic，所以没有"upgrade"这一步）
#    git pull / 覆盖文件均可

# 3) 更新依赖
python -m pip install -r requirements.txt

# 4) 起服务 —— 启动时会自动补缺失的列（无损）
python -m uvicorn app.main:app --port 8001

# 5) 自查：存活与就绪探针
#    GET http://localhost:8001/health   存活（零依赖，不检查 DB）
#    GET http://localhost:8001/ready    就绪（用真实业务查询验证 DB 与 schema）
#    见 README.md:228-231
```

前端同样：`cd frontend && npm ci && npm run build`（开发模式下 `npm run dev`）。

---

## 3. 备份与恢复（升级前必做）

两个脚本都已存在，用法写在各自文件头：

### 3.1 备份：`backend/scripts/backup_db.py`

```bash
cd backend
python scripts/backup_db.py                 # 备份到仓库根 _backup/<时间戳>/（:17）
python scripts/backup_db.py --label pre-selfrating   # 打标签，便于识别（:18）
python scripts/backup_db.py --keep 5        # 只保留最近 5 份 db 快照（:19）
python scripts/backup_db.py --list          # 列出已有快照（含完整性状态）（:20）
```

**为什么用 `VACUUM INTO` 而不是复制文件**（`scripts/backup_db.py:9-14`）：

1. **原子一致**：源库正被 API / worker 写入时，直接 `copy` 可能拿到"半写完"的页组合
   （WAL 模式下还有未 checkpoint 的 `-wal` 文件）；`VACUUM INTO` 由 SQLite 保证一致快照；
2. **自动整理**：输出是紧凑无碎片的库文件；
3. **零依赖**：不需要装任何包，也不需要停服务（但升级前建议停，见第 2 节）。

### 3.2 恢复：`backend/scripts/restore_db.py`

```bash
cd backend
python scripts/restore_db.py --list                        # 列出可选快照
python scripts/restore_db.py --from 20260911-003512-pre-selfrating
python scripts/restore_db.py --from <快照目录名> --yes      # 跳过二次确认（脚本化场景）
```

⚠️ **为什么必须用脚本而不是"停服务后覆盖文件"**（`scripts/restore_db.py:4-15`）：

1. **WAL 兄弟文件**：只覆盖主库、留下旧的 `-wal` / `-shm`，SQLite 启动时会把
   旧 WAL 里的**过期事务**重放到新库上——得到的既不是快照状态，也不是覆盖前状态。
   脚本会先删干净（`_WAL_SIBLINGS`，`scripts/restore_db.py:42-44`）；
2. **不校验就恢复**：静默损坏的快照覆盖上去，等于用坏数据换掉好数据。
   脚本恢复前会在快照上跑 `integrity_check`，不通过就中止；
3. **不可回退**：恢复是破坏性操作。脚本会在恢复前**自动给当前库再存一份快照**，
   万一选错了源还能退回去。

### 3.3 还需要一起备份的东西

数据库不是全部。存储根目录（默认 `backend/data/storage`，或你配置的
`VAULT_DIR` / `STORAGE_DIR`，见 `backend/app/config.py:106-118`）里是原始文件与
Markdown 副本；`backend/data/models/` 是模型缓存（可重新下载，不必备份）；
`backend/data/logs/` 是日志（**可能含 `LOG_SQL=true` 时的业务数据明文**，
见 `SECURITY.md`）。

> 定时备份：项目自带 Celery Beat 每日任务做 db 快照与恢复演练
> （阶段 6.6，`docs/overhaul-plan.md:3321`；演练记录见附录 AS，`:8163`；
> 对象存储快照见附录 AW，`:8459`），
> 但**自己升级前手动备份一次**仍然是最省事的那条保险。

---

## 4. 升级时可能"启动就报错"的两个已知坑

### 4.1 `APP_ENV=prod`（默认）下 `JWT_SECRET_KEY` 缺失或为公开占位值

`backend/app/config.py:563` 的 `app_env` 默认值是 `prod`。
prod 下若 `JWT_SECRET_KEY` 为空，或等于历史文档里示范过的公开占位值
（`config.py:59-67` 的 7 个值，含 `engramnote-dev-secret-change-in-production`），
`Settings` 会直接抛 `ValidationError`，**应用拒绝启动**
（`config.py:627-670`；`README.md:165-169`）。

处理方式：

```bash
python -c "import secrets; print(secrets.token_hex(32))"   # 生成后写进 backend/.env
```

若你只是本地开发，也可以显式写 `APP_ENV=dev`——此时允许空密钥，
应用会自动生成随机密钥并持久化到 `data/.jwt-secret`，重启复用
（`config.py:633-634`、`:691-712`）。⚠️ **多实例部署不要用 `dev`**：
每个实例会生成不同密钥，换容器即全员掉线。

### 4.2 `DEBUG=true` **不再**打开 SQL 日志（有意的行为变化）

单一 `debug` 开关已拆成 `APP_ENV` / `LOG_SQL` / `LLM_PROVIDER` 三个互不相干的开关
（`backend/app/config.py:554-592`）。`DEBUG` 保留为遗留等价开关，但**只**折叠进
`APP_ENV`（`config.py:608-624`）；要 SQL 日志必须显式 `LOG_SQL=true`。

⚠️ 打开后日志里会有 **bcrypt 哈希与知识卡片/题目正文**
（`config.py:566-572`、`database.py:41`、`main.py:161-165`）——**生产不要开**。
详见 `SECURITY.md`。

---

## 5. 升级后自查

- [ ] `GET /health` 返回 200（存活；它**刻意不检查依赖**，`README.md:230`）；
- [ ] `GET /ready` 返回 200（就绪：一次真实业务查询验证 DB 连得上且 **schema 就绪**，
      否则 503；`README.md:231`）；
- [ ] 启动日志里的**安全姿态行**符合预期（`app_env` / LLM / SQL 日志 /
      traceback 回吐 / JWT 密钥来源，`backend/app/main.py:146-165`）；
- [ ] 打开一篇旧笔记、跑一次复习 —— 这两条路径最容易暴露 schema 缺口；
- [ ] 如果碰到 `review_logs.quiz_id` 一类的旧 schema 报错：
      先 `python scripts/reconcile_schema.py --check`，再按第 1.3 节处理。

---

## 6. 从很旧的版本升级？

`docs/overhaul-plan.md` 是逐阶段的执行台账（11709 行，附实测证据）：
每个阶段标题下都有"这条做了什么、还没做什么"。

- 阶段与附录索引：`docs/overhaul-plan.md:11-34`（目录）、`:2923-3353`（路线图）；
- **未做事项总表**："哪些不影响现在的使用"——附录 BN，`docs/overhaul-plan.md:11145`；
- 每次改动的实测记录：附录 A 起的各附录标题（`docs/overhaul-plan.md:3458` 之后）。

⚠️ 该文件里的 `文件:行号` 引用是**当时**的行号，代码演进后会漂移
（例如 `docs/overhaul-plan.md:2980` 引用的 `requirements.txt:39/44-45`，
在当前的 `backend/requirements.txt`（76 行）里已不是那个位置）。
**以代码实测为准**（`AGENTS.md:11`）。
