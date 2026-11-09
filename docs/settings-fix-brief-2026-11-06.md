# 设置功能修复任务书（交接给执行智能体）

> **交接对象**：另一个编码智能体 ｜ **基准提交**：`bb859a6`（main） ｜ **写于**：2026-11-06
> **执行状态（2026-11-06）**：N4/N5/N6/N8/N9/N10/N11/N12/N13 已修；N1/N2/N3/N7/N14 待人工确认。
> **先读**：`AGENTS.md`、`docs/settings-plan.md` §9–§10、`docs/sqlite-single-writer.md`
> **一句话**：设置功能本身已经能用、门禁全绿；剩 14 条问题，其中 **2 条是发布路径上的硬伤（迁移）**、2 条是旧 Major、其余是边界与文档。按 §5 逐条修，不扩大范围。

---

## 0. 30 秒版

- 能用的部分：`/settings` 六组、`GET/PATCH /api/settings`、`GET /api/settings/usage`、`user_settings` 表与 014/015 迁移、每日上限/留存率/最长间隔/卡片上限/输出语言/清洗/邮件时间全部接线。
- 不能直接发的部分：**N1**（先启动应用再跑迁移会 `duplicate column name`）、**N2**（015 迁移漏 4 个 CHECK，与新库 schema 不一致）。
- 旧账：**N3**（整点精确匹配会丢信）、**N4**（`email_configured` 漏 SMTP 密码）。
- N1/N2/N7/N14 属"改数据/改语义"，**先出方案、标待人工确认，不要自己拍板**。

---

## 1. 现状事实（已实测）

| 项 | 值 |
|---|---|
| HEAD | `bb859a6 fix(ci): 让 ASR 配置测试不再依赖音频运行时` |
| 工作区 | `.gitignore` 有 17 行未提交改动（`research/` 忽略，**与本任务无关，禁止提交**）；另有本任务书 + `docs/README.md` 的索引行（本次新增，可单独提交） |
| 后端全量 | `1722 passed / 30 skipped`（约 457 s） |
| 前端全量 | `731 passed / 62 files`（约 50 s） |
| 类型 / 契约 | `tsc --noEmit` exit 0；`gen:api:drift` → pathMatched 121 / missing 0 / conflict 0 |
| 迁移实测 | 014→015 成功且数据保留，但**只剩 2 个 CHECK**；先运行时补列再迁移 → **失败** |

六组落点：学习节奏 / 提醒 / 内容与生成 / 外观与语言 / 账号与安全 / 数据与关于（`frontend/src/pages/Settings.tsx:49-58`）。

---

## 2. 环境与命令（照抄即可）

```powershell
# 后端（Python 3.11.17）
cd D:\engramnote\backend
& D:\conda\envs\mineru_env\python.exe -m pytest -q                       # 全量，约 7.5 分钟
& D:\conda\envs\mineru_env\python.exe -m pytest tests/test_user_settings.py tests/test_settings_api.py -q
& D:\conda\envs\mineru_env\python.exe -m ruff check <changed files>

# 前端
cd D:\engramnote\frontend
npm.cmd test                                                           # 全量
npm.cmd test -- src/pages/Settings.test.tsx                            # 单项
npx.cmd tsc --noEmit
npx.cmd eslint <changed files>
npm.cmd run gen:api:drift
```

> ⚠️ **不要前后端全量并发跑**：实测并发时 vitest 会打印 731 全过但 exit 1（资源竞争），容易被误判成红灯。串行跑。

---

## 3. 硬约束（违反即回退）

1. **不改对外 API**：路径、字段、状态码、429 语义保持不变。
2. **不改数据库结构**——例外只有 N1/N2 这两条迁移问题：**先按 §5 出方案 + diff 草案，标「待人工确认」，确认后再动**。
3. 不引新依赖、不改 CI、不重构设置页视觉、不动 e2e 的 stub 结构。
4. 每项一条分支：`fix/settings-<ID>-<slug>`；**先加一条会红的复现用例**，再修；单测 + 全量 + lint + tsc + drift 全绿才提交。
5. 不 push、不合并 `main`。
6. 提交信息：`fix(settings): <ID> <一句话>`，正文写「复现 / 修复 / 验证」三段。
7. 凡涉及 API / 数据 / 权限 / 依赖：**停止并输出「待人工确认」清单**，不要自行决定。

---

## 4. 修复顺序

```
N1 + N2（同一迁移面，先定方案、等确认）
  → N3 / N4（行为可信度）
  → N5 / N6 / N10（低风险边界）
  → N7 / N8 / N9 / N11
  → N12 / N13 / N14（文档与口径）
  → Nit（可选）
```

---

## 5. 问题清单

> 模板：**现象 → 证据 → 复现 → 修复方向 → 验收 → 确认级别**
> 验收里的用例名是建议值，但**断言必须覆盖"修复前会红"**。

### N1 迁移与启动补列互斥（P0｜迁移｜需人工确认）

- **现象**：先启动新应用（启动流程自动补列），再执行文档要求的 `alembic upgrade head` → `sqlite3.OperationalError: duplicate column name: desired_retention`。
- **证据**：`backend/app/database.py:286-291`（`init_db()` 每次启动都跑 `_migrate_sqlite`）、`:467-505`（015 对应的 7 条 `ALTER TABLE ... ADD COLUMN`）；`backend/alembic/versions/015_settings_expansion.py:24-46`。
- **复现**：附录 A 脚本 1（造 014 基线 → 跑运行时 DDL → `command.upgrade(cfg, 'head')` → 捕获异常）。
- **方案（二选一，等人拍板）**：
  - A. **让 015 幂等**：`upgrade()` 里先 `sa.inspect(op.get_bind()).get_columns('user_settings')`，只加缺失列；再配 016 给已升级库补 CHECK（与 N2 合并做）。
  - B. 运行时补列后写 `alembic_version='015_settings_expansion'`——**不推荐**：CHECK 永远补不上，等于把 N2 固化。
- **验收**：新增 `backend/tests/test_settings_migration.py::test_upgrade_after_runtime_columns_is_idempotent`：造 014 库 → 模拟补列 → `upgrade(head)` 不抛；连跑两次仍不抛。
- **注意**：SQLite batch 重建必须保留数据、唯一约束、外键、索引（附录 A 脚本 2 已断言）。

### N2 015 漏掉 4 个 CHECK（P0｜迁移｜需人工确认）

- **现象**：迁移后的库只有 `daily_review_limit`、`email_hour` 两个 CHECK；模型定义了 6 个。新库（`create_all`）有 6 个 → 两套 schema。
- **证据**：`backend/app/models/user_settings.py:37-60`（6 个 CHECK）；`backend/alembic/versions/015_settings_expansion.py:24-46`（只 `add_column`）；`backend/app/database.py:470-471` 注释声称"生产库由 Alembic 015 负责"（实测不成立）；`docs/settings-plan.md:275-280,600` 把 6 个 CHECK 当既有事实。
- **复现**：附录 A 脚本 2（014 库 → `upgrade head` → 打印 DDL，`CHECK` 计数 = 2）。
- **方案**：015 里用 `batch_op.create_check_constraint(...)` 补 4 个（留存率/最长间隔/output_locale/卡片上限）；**另加 016** 处理"已经跑过 015"的库（先 `get_check_constraints` 判断，幂等，SQLite 需 batch 重建）。
- **验收**：迁移测试断言 6 个 CHECK 存在、数据未丢、`ix_user_settings_user_id` 仍在；再断言坏值被拒（`INSERT ... desired_retention=0.5` → `IntegrityError`）。
- **为什么不是小事**：缺 CHECK 时坏值能进库，而 `GET /api/settings` 的响应模型带 `Literal`/范围校验 → 整页 500；`_effective()` 还被排期/清洗路径复用。

### N3 每小时任务"整点精确匹配"导致丢信（P1｜bug｜需产品确认）

- **证据**：`backend/app/tasks/reminder_tasks.py:63-64`（`preferred_hour != current_business_hour` → `False`）；`select_due_recipients`（`:71-91`）没有跳过原因日志。
- **复现**：用户设 9 点、`now=北京 10:05` → `should_send_now(...) = False`；Beat/worker 延迟超过 60 分钟，该用户当天永远收不到。
- **方案**：
  - A. 当日未发 + `now_hour >= preferred`（当天晚些时候补发）；
  - B. 宽限窗口（如 2h）+ 单独记录 `skipped_delayed` 指标。
- **验收**：`test_reminder_catch_up_after_delay`——10:05 应选中 9 点用户；已发过（`last_reminded_at` 同日）不选中；原有精确匹配/去重用例保持绿。
- **风险**：改变"什么时候发"属产品语义 → 让人拍板 A/B，不要自己选。

### N4 `email_configured` 漏 SMTP 密码（P1｜bug｜可直接修）

- **证据**：`backend/app/api/settings.py:41-48` 只判 `email_reminder_enabled + smtp_host + smtp_user`；真实发信失败在 `backend/app/services/notification_service.py:199-203` 被吞成 `logger.error`。
- **复现**：monkeypatch 配置（host/user 有、password 空）→ `GET /api/settings` 仍返回 `email_configured=true`；任务每天失败、用户侧无感。
- **修复**：判定里加 `smtp_password`（最小改动，不改 API 形状）。
- **验收**：`test_email_configured_requires_password`（有密码 true / 无密码 false）；保留 `smtp_user` 为空 → false 的既有语义。

### N5 三处只读数据没有错误态（P2｜UX）

- **证据**：`frontend/src/pages/Settings.tsx:450-462`（今日额度）、`:979-985`（存储用量）、`:997-1012`（LLM 用量）——都只判 `data` 有无，接口失败就永久"加载中"。
- **修复**：加 `isError` 分支 + 重试按钮；中英文案一起补（`i18n.test.tsx` 会卡键）。
- **验收**：`Settings.test.tsx` 新增用例，让三个 query reject，断言出现错误文案与重试按钮，而不是"加载中"。

### N6 `cancelQueries` 前缀误伤另外三个查询（P2｜bug）

- **证据**：`Settings.tsx:149` 的 key 是 `['settings']`；`:45-47` 的 usage / review-stats / llm-usage 都是它的子前缀。
- **修复**：`cancelQueries({ queryKey: SETTINGS_KEY, exact: true })`。
- **验收**：单测 spy `queryClient.cancelQueries`，断言带 `exact: true`。

### N7 首次建行冻结 9 个字段（P2｜语义｜需确认）

- **证据**：`backend/app/services/user_settings_service.py:99-105`（建行时 `**deployment_defaults()` 全量写入）。
- **影响**：用户只改了每日上限，运维之后改 `FSRS_REQUEST_RETENTION` / 清洗开关，该用户不跟随——与 `models/user_settings.py:12-14`、`docs/settings-plan.md:5-10` 的"用户覆盖 → env 默认"只在无行时成立。
- **方案**：
  - A. 列改可空 + 读取时 `COALESCE`（**DB 结构变更 → 需确认**）；
  - B. 承认"行 = 快照"，改文档 + 加显式测试固定该行为（不动结构）。
- **验收**：按选定方案给用例；A 要断言"env 改后未显式设置的字段跟随"，B 要断言"快照不跟随"并在文档写明。

### N8 双写非原子（P2｜bug）

- **证据**：`backend/app/api/settings.py:128-138`：settings 一次 commit，`email_enabled` 再一次。
- **修复**：合并为一次 commit（先赋值，统一提交），失败一起回滚。
- **验收**：monkeypatch 让第二次 commit 抛错 → 两处都不落库（或响应 500 时数据保持一致）。

### N9 并发首次建行 → 唯一键 500（P2｜bug）

- **证据**：`user_settings_service.py:99-105`（`SELECT` 与 `INSERT` 之间无锁/无重试）。
- **修复**：捕获 `IntegrityError` → `rollback()` → 重取行 → 再应用字段 → 提交（重试一次）。
- **验收**：两个 session 交错（第二个在第一个 flush 之后插入）→ 不抛 500，最终只有一行。

### N10 env 越界 → `GET /api/settings` 500（P2｜健壮性）

- **证据**：`backend/app/config.py:279-287,459,500,507,554` 无范围约束；`backend/app/schemas/settings.py:18-33` 有范围/Literal。
- **修复**：`deployment_defaults()` 里 clamp + `logger.warning`（或启动时校验配置，二选一）。
- **验收**：monkeypatch `daily_review_limit=4` / `card_max_new_per_run=20` → GET 返回 200 且值为夹紧后的合法值。

### N11 下拉框在 env 默认不在档位内时空白（P3｜边界）

- **证据**：`Settings.tsx:478-495`（留存率 0.8/0.85/0.9/0.95）、`:506-522`（最长间隔 365/1825/3650）；`value` 不在 options 里 → 空白。
- **修复**：把当前值临时并入选项（显示"部署默认 87%"），或 clamp 到最近档位。
- **验收**：mock 响应 `desired_retention=0.87` → select 有可见选中项（非空）。

### N12 文档 §0 自相矛盾（P3｜文档）

- **证据**：`docs/settings-plan.md:16` 仍写"现在没有设置页"，`:19` 同节写"六组已全部落地"。
- **修复**：改为"开工前：没有设置页"或删除该条；顺手核对 §1.3 的"死配置"表述是否还成立。

### N13 联动矩阵证据不实（P3｜文档/测试）

- **证据**：`docs/settings-plan.md:673` 声称"第 26 道 429"，但 `backend/tests/test_settings_api.py` 只有 2 条用例、无 429；`:675` 把纯函数 `test_max_interval_is_enforced_from_user_view` 当"用户视图"证据。
- **修复**：补 HTTP 429 用例（`test_settings_api.py`），或改证据列；`max_interval` 改成经 API 断言。

### N14 "AI 输出语言"口径（P3｜需确认）

- **证据**：只覆盖理解 worker（`backend/app/tasks/understand_tasks.py:114-115`）；问答与语义评分仍按 `Accept-Language`（`backend/app/services/rag_service.py:839`、`backend/app/services/sm2_service.py:411`）。
- **方案**：A. 文案限定为"卡片/题目生成语言"（双语）；B. 让问答也读用户设置（要把设置读进请求热路径，性能/缓存先想清楚）→ 待确认。
- **验收**：按选定方案。

### Nit（可选）

- `Settings.tsx` 1123 行单文件（可拆 section 组件）。
- 通知权限状态只初始化一次（`Settings.tsx:137`），浏览器里改权限后页面不更新。
- ADHD 修复用 `document.body` + `subtree: true` 观察直到容器挂载（`frontend/src/hooks/useAdhdReader.ts`），页面不挂载容器时会一直观察。
- `Settings.module.css` 仍 7 处硬编码颜色（`:310,321,341,346,354,391,411`）。
- `user_settings_service.py:131-135` 的 `as_dict()` 无调用方。

---

## 6. 不要做

- 不要提交 `.gitignore` 里那 17 行 `research/` 改动。
- 本任务书与 `docs/README.md` 的索引行可以单独提交，但**不要和 `.gitignore` 混进同一个提交**。
- 不要删/改 `user_settings` 的已有列，不要 drop 表。
- 不要把 `GET /api/settings` 改成写库。
- 不要加"按留存率重排历史卡片"（明确不做）。
- 不要移除侧栏语言入口、Dashboard 的 `/settings#reminders` 链接。
- 不要顺手重构设置页视觉或改文案口味（N12/N14 除外）。

---

## 7. 每项完成的标准（DoD）

- [ ] 复现用例修复前红、修复后绿（提交里贴命令与输出）
- [ ] 该项相关的既有用例全绿
- [ ] 后端全量 ≥ **1722 passed / 30 skipped**；前端全量 ≥ **731 passed / 62 files**
- [ ] `tsc --noEmit` exit 0；`gen:api:drift` `pathMissing 0 / conflict 0`
- [ ] `ruff` / `eslint` 干净
- [ ] 若改到数字/事实，同步 `docs/settings-plan.md` §9.3/§9.4
- [ ] 提交信息含「复现 / 修复 / 验证」三段

---

## 附录 A：迁移复现脚本（直接可跑）

### A1 先启动补列、再迁移（复现 N1）

```python
# python A1.py  （需 sys.path 指向 D:\engramnote\backend）
import os, sqlite3, tempfile, sys
sys.path.insert(0, r"D:\engramnote\backend")
from alembic.config import Config
from alembic import command

tmp = os.path.join(tempfile.gettempdir(), "mig-audit-015c.db")
if os.path.exists(tmp):
    os.remove(tmp)
con = sqlite3.connect(tmp)
con.executescript("""
CREATE TABLE users (id VARCHAR NOT NULL PRIMARY KEY, email VARCHAR, username VARCHAR);
CREATE TABLE user_settings (
  id VARCHAR NOT NULL, user_id VARCHAR NOT NULL,
  daily_review_limit INTEGER DEFAULT '10' NOT NULL,
  email_hour INTEGER DEFAULT '9' NOT NULL,
  created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
  updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
  CONSTRAINT uq_user_settings_user_id UNIQUE (user_id),
  PRIMARY KEY (id)
);
INSERT INTO users (id, email, username) VALUES ('u1', 'a@b.c', 'u1');
CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL,
  CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num));
INSERT INTO alembic_version (version_num) VALUES ('014_user_settings');
""")
# 与 backend/app/database.py:467-505 的 DDL 逐字一致
con.executescript("""
ALTER TABLE user_settings ADD COLUMN desired_retention FLOAT DEFAULT 0.9 NOT NULL;
ALTER TABLE user_settings ADD COLUMN max_interval_days INTEGER DEFAULT 3650 NOT NULL;
ALTER TABLE user_settings ADD COLUMN output_locale VARCHAR(10) DEFAULT 'follow' NOT NULL;
ALTER TABLE user_settings ADD COLUMN card_max_new_per_run INTEGER DEFAULT 500 NOT NULL;
ALTER TABLE user_settings ADD COLUMN clean_simplify_formula BOOLEAN DEFAULT 0 NOT NULL;
ALTER TABLE user_settings ADD COLUMN clean_normalize_spacing BOOLEAN DEFAULT 1 NOT NULL;
ALTER TABLE user_settings ADD COLUMN clean_normalize_heading_levels BOOLEAN DEFAULT 1 NOT NULL;
""")
con.commit(); con.close()

cfg = Config(r"D:\engramnote\backend\alembic.ini")
cfg.set_main_option("script_location", r"D:\engramnote\backend\alembic")
cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp}")
try:
    command.upgrade(cfg, "head")
    print("RESULT: upgrade succeeded")
except Exception as exc:  # 期望走到这里
    print("RESULT: upgrade FAILED ->", type(exc).__name__)
    print(str(exc).splitlines()[0][:200])
# 实测输出：RESULT: upgrade FAILED -> OperationalError
#           (sqlite3.OperationalError) duplicate column name: desired_retention
```

### A2 014 基线直接迁移（复现 N2）

```python
# python A2.py
import os, sqlite3, tempfile, sys
sys.path.insert(0, r"D:\engramnote\backend")
from alembic.config import Config
from alembic import command

tmp = os.path.join(tempfile.gettempdir(), "mig-audit-015b.db")
if os.path.exists(tmp):
    os.remove(tmp)
con = sqlite3.connect(tmp)
con.executescript("""
CREATE TABLE users (id VARCHAR NOT NULL PRIMARY KEY, email VARCHAR, username VARCHAR);
CREATE TABLE user_settings (
  id VARCHAR NOT NULL, user_id VARCHAR NOT NULL,
  daily_review_limit INTEGER DEFAULT '10' NOT NULL,
  email_hour INTEGER DEFAULT '9' NOT NULL,
  created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
  updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
  CONSTRAINT uq_user_settings_user_id UNIQUE (user_id),
  CONSTRAINT ck_user_settings_daily_review_limit CHECK (daily_review_limit BETWEEN 5 AND 100),
  CONSTRAINT ck_user_settings_email_hour CHECK (email_hour BETWEEN 0 AND 23),
  FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
  PRIMARY KEY (id)
);
CREATE INDEX ix_user_settings_user_id ON user_settings (user_id);
INSERT INTO users (id, email, username) VALUES ('u1', 'a@b.c', 'u1');
INSERT INTO user_settings (id, user_id, daily_review_limit, email_hour)
  VALUES ('s1', 'u1', 42, 7);
CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL,
  CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num));
INSERT INTO alembic_version (version_num) VALUES ('014_user_settings');
""")
con.commit(); con.close()

cfg = Config(r"D:\engramnote\backend\alembic.ini")
cfg.set_main_option("script_location", r"D:\engramnote\backend\alembic")
cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp}")
command.upgrade(cfg, "head")

con = sqlite3.connect(tmp)
ddl = con.execute(
    "select sql from sqlite_master where type='table' and name='user_settings'"
).fetchone()[0]
print("CHECK count:", ddl.upper().count("CHECK"))       # 实测 2（期望 6）
print("row:", con.execute(
    "select user_id, daily_review_limit, email_hour, desired_retention, "
    "max_interval_days, output_locale, card_max_new_per_run, clean_simplify_formula "
    "from user_settings").fetchall())
# 实测：CHECK count: 2
#       row: [('u1', 42, 7, 0.9, 3650, 'follow', 500, 0)]
```

> 注意：**不能**用空库从 001 跑 `upgrade head`——001 的 `ALTER TABLE notes` 假设表已存在，链子的设计前提是"已有库 + 增量迁移"。迁移测试必须按 A1/A2 的方式造 014 基线。

---

## 附录 B：审计证据摘要

| 证据 | 结果 |
|---|---|
| 014→015 后的 `user_settings` DDL | 新增 7 列；仅保留 `ck_user_settings_daily_review_limit`、`ck_user_settings_email_hour` |
| 数据保留 | `('u1', 42, 7, 0.9, 3650, 'follow', 500, 0, 1, 1)` |
| 索引/唯一/FK | `ix_user_settings_user_id`、`uq_user_settings_user_id`、外键都在 |
| 启动补列后再迁移 | `OperationalError: duplicate column name: desired_retention` |
| 后端全量 | 1722 passed / 30 skipped（457 s） |
| 前端全量 | 731 passed / 62 files（单独跑 exit 0；与后端并发时曾 exit 1 但全过） |
| 类型/契约 | `tsc` 0；drift 121/0/0 |
