# EngramNote 重构方案（Overhaul Plan）

> **性质**：本文件是 EngramNote 的**破坏性重构方案**，不是增量修补。
> 目标不是"把现有代码修得更好"，而是**保留产品命题、替换其承重结构**。
> 撰写依据：对 `backend/app`（约 20,000 行）、`frontend/src`（约 16,000 行）、
> 22 次提交历史、`docs/architecture.md`、`docs/decisions.md`（F-01…F-36 + D-01…D-04）
> 的逐行源码审计。

---

## 目录

- [第一部分 · 结论：为什么必须重构，而不是继续修](#第一部分--结论为什么必须重构而不是继续修)
- [第二部分 · 现状诊断](#第二部分--现状诊断)
  - [2.1 结构性诊断](#21-结构性诊断一个用-37-个补丁粘起来的原型)
  - [2.2 数据与并发地基](#22-数据与并发地基最致命的一层)
  - [2.3 AI 管道：看起来很强，实际是空转](#23-ai-管道看起来很强实际是空转)
  - [2.4 学习算法：本项目的立身之本，也是最大的空转](#24-学习算法本项目的立身之本也是最大的空转)
  - [2.5 工程与安全](#25-工程与安全)
  - [2.6 迁移与数据完整性](#26-迁移与数据完整性深度审计新增)
  - [2.7 AI 管道补充](#27-ai-管道深度审计补充)
  - [2.8 前端](#28-前端深度审计补充)
  - [2.9 多租户与 API 契约](#29-多租户与-api-契约)
  - [2.10 运行期发现：LLM 调用从未成功过](#210--运行期发现这个应用的-llm-调用从未成功过)
- [第三部分 · 四把手术刀](#第三部分--四把手术刀)
- [第四部分 · 目标架构](#第四部分--目标架构)
- [第五部分 · 分阶段整改路线图](#第五部分--分阶段整改路线图)
- [第六部分 · 需要拍板的不可逆决策](#第六部分--需要拍板的不可逆决策)
- [第七部分 · 验收标准与量化指标](#第七部分--验收标准与量化指标)
- [第八部分 · 必须放弃的东西](#第八部分--必须放弃的东西)
- [附录 A · 现场实测证据](#附录-a--现场实测证据)
- [附录 B · 立即可以做的 10 件事](#附录-b--立即可以做的-10-件事)
- [附录 C · 与现有文档的关系](#附录-c--与现有文档的关系)

---

## 第一部分 · 结论：为什么必须重构，而不是继续修

### 1.1 一句话诊断

**EngramNote 不是"实现了学习闭环的工具"，而是"三条互不相接的流水线 + 一个能发出漂亮 UI 的空转外壳"。**

三条流水线各自都在跑，但它们之间没有形成闭环：

| 流水线 | 实际做的事 | 断在哪里 |
|---|---|---|
| **A. 加工线** | 上传 → MinerU → 清洗 → LLM 抽卡 | 抽出卡片后**原文就退场了**，后续全部基于 LLM 的压缩摘要运转 |
| **B. 检索线** | 向量 + BM25 + n-gram → RRF → LLM | 三路检索的是**两套不同粒度的语料**；引用可以被凭空编造 |
| **C. 复习线** | SM-2 调度 → 答题 → 掌握度 | 判分器**对语义反转无感**；掌握度**91% 恒为 0** |

结果：**系统生产内容的能力 ≫ 系统验证学习的能力**。
它能把一本 500 页的 PDF 变成 200 张卡片，
但它无法回答"用户到底记住了多少"这个唯一重要的问题。

### 1.2 最强的一条证据：调度器从未前进过

> ⚠️ **动手修复后新增的发现（详见 §2.10）**：查这一节的动机是"为什么调度器不前进"，
> 结果发现**更根本的原因** —— 这个应用的 **LLM 调用从未成功过**：
> 网关（OpenCode）要求 `x-opencode-session` 头，代码没带，**所有请求 400**。
> 理解、出题、图谱推断、RAG 问答全部失败，且失败被静默吞成"零结果"。
> 所以下面这组数字不是"SM-2 调参问题"，而是"**整条 AI 管道从未连通**"。

我用只读方式直接查询了本项目**真实的数据库**（`backend/data/db/engramnote.db`，3.1 MB）：

```sql
SELECT MIN(interval), MAX(interval), AVG(interval) FROM quiz_items;
→ (1, 1, 1.0)

SELECT MIN(repetition), MAX(repetition) FROM quiz_items;
→ (0, 1)

SELECT COUNT(*) FROM quiz_items WHERE next_review_at IS NULL;
→ 867 / 1058   （82%）
```

**1058 道题目，`interval` 无一例外全部等于 1；`repetition` 最大值为 1。**

SM-2 的正常轨迹是 `1 天 → 6 天 → 15 天 → 38 天 …`（`sm2_service.py:93-98`）。
`interval` 全部为 1 意味着：**每一次复习要么被判为失败（重置为 1），
要么从未进入第二轮。间隔重复这个核心功能，在实际数据上从未真正运作过。**

再加上掌握度分布实测（`mastery_level`：1183 张卡片中 **1078 张恒为 0**，
其余 103 张**全部挤在 70–79 这一个 10 分宽的桶里**），
可以给出一个不留情面的结论：

> **本项目宣传的三个卖点（清洗、AI 理解、间隔重复）里，
> 唯一无法被"看起来在跑"掩盖的那个 —— 间隔重复 —— 实测是坏的。**



### 1.3 为什么"继续修"不可行

代码里已经有 **149 处 `F-xx` 修复编号注释**、**148 处 `except Exception`**、
**19 处静默吞异常**、**36 条决策记录**。这个密度说明的不是"项目很严谨"，而是：

> **每一处修复都在增加系统的表面积，而没有任何一处修复在减少它。**

具体证据：

- **数据库迁移有 3 套并存，且其中"正规"的那套从未生效**：
  Alembic（001–010）+ `database.py::init_db()` 无条件 `create_all()`
  + `_migrate_sqlite()` 手写 ALTER + `_rebuild_dangling_tables()` 用裸 sqlite3 **DROP/重建 5 张表**。
  后者在**每个进程启动时都会执行**（`backend/app/database.py:169-195`），
  并且包含一条**每次启动都运行的破坏性全局去重 SQL**（`database.py:580-587`）。
  而 Alembic 那条链**在一个空库上第一步就会失败**（详见 §2.6 M-1）。
- **演进方向自相矛盾**：项目早期设计了"项目隔离"的 Vault 目录树
  （`{vault}/{user_id}/{project_slug}/`，README:81-88），
  后来又做了"项目纯标签化"重构，把物理路径塌缩成 `{user_id}/inbox/`
  （`backend/app/services/vault_path.py:70-80` 注释直言"标签化后不再有项目 slug 目录"）。
  **旧数据仍躺在项目目录里，新数据全进 inbox**，`derive_prefix()` 对所有笔记一律返回 `inbox`
  （`vault_path.py:120-137`）—— 而迁移 006 顺手 `drop_column('notes','project_id')`
  且**自陈放弃了数据搬运**（`006_project_tags_refactor.py:47-53`）。
  即"笔记属于哪个项目"这批数据**在任何路径上都不会被迁移**。
- **文档与代码已经脱节到无法互信**：
  `architecture.md:78` 称 `client.ts` 是 **2221 行**，实测是 **298 行**（已拆分）；
  `architecture.md:84` 称有 `styles/global.css` **2906 行**，实测**该文件不存在**，
  样式已拆成 14 个文件共约 2700 行；
  `architecture.md:206` 称 `KnowledgeGraph.tsx` **1547 行**，实测 **868 行**；
  `architecture.md:193` 称"alembic 被注释"，实测 `requirements.txt:18` **已启用**。
  **这些数字全部是"写了但没回头改"的残留。**
- **零测试闭环**：无 `.github/`（无 CI）、无覆盖度量、
  `backend/tests/` 混入一次性调试脚本，
  `backend/` 根目录还有 **15 个** `test_*.py` / `verify_*.py` 脚本
  （`architecture.md:208` 自述"pytest 收集即烧真实 API/改生产库"）。

**结论**：这套代码的维护成本曲线已经是超线性的。
**继续修 = 在一个"文档不可信、迁移不可用、测试不可跑"的地基上盖楼。**

### 1.4 但是：产品命题是对的，必须保留

必须把两件事分开评价：

**要保留的（产品判断，很有价值）：**
1. "资料清洗 / 知识提取 / 间隔重复三件事不该割裂在三个软件里" —— 真痛点
2. "原始数据不可篡改，AI 只做减法不做加法" —— 正确的信任模型（README:37、参赛贴文）
3. "零外部依赖、国内源可用、任何学习者零配置启动" —— 正确的分发策略
4. "处理链分层、AI 不能越界加工原文" —— 正确的架构直觉

**要重做的（工程实现，全盘皆输）：**
数据持久化选型、任务队列选型、检索层设计、评分算法、事件循环模型、错误契约、前端架构。

**所以本方案的性质是**：保留命题与领域模型，**替换全部承重结构**。

---

## 第二部分 · 现状诊断

> 每条问题都给出 `文件:行号` 与代码证据。分四级：
> 🔴 blocker（会让产品在真实使用中失败） / 🟠 high / 🟡 medium / ⚪ low

### 2.1 结构性诊断：一个用 37 个补丁粘起来的原型

#### 🔴 S-1 修复编号文化：149 处 `F-xx` 注释 = 技术债的化石层

```bash
# 实测
backend/app/**  中匹配 F-\d+ 的注释：149 处
except Exception：148 处
静默吞异常（except + pass/continue）：19 处
```

`docs/decisions.md` 用 F-01…F-36 记录了 36 条"修复决策"，
`architecture.md:204` 自述"修复编号注释 F-01~F-35（180+ 处）"需要"迁移到 decisions.md"。

**后果**：新读者无法判断一段代码是"设计如此"还是"为绕开某个 bug 而长成这样"。
`architecture.md:212` 建议的修复顺序（文档 → 契约 → 拆文件 → 测试 → 迁移 → decisions）
是**正确的直觉但无法执行**：在一次重构中同时搬 5 个巨型文件 + 改错误契约 + 收敛迁移 + 整顿测试，
等价于重写，且没有任何一步能被独立验证。

**修复方向**：停止给旧代码打补丁。F-xx 编号机制整体作废，`docs/decisions.md` 归档为历史文档。

#### 🟠 S-2 巨型文件与职责失守

| 文件 | 行数 | 问题 |
|---|---|---|
| `backend/app/services/graph_service.py` | 908 | 嵌入相似度 + LLM 推断 + 三处幻觉防御 + 关系 CRUD 全在一个模块 |
| `backend/app/services/note_service.py` | 940 | 笔记 CRUD + 回收站 + 悬挂引用 + 路径推导 |
| `backend/app/api/upload.py` | 754 | 路由层里直接做配额校验、路径解析、文件名去重、版本快照 |
| `backend/app/api/understanding.py` | 700 | 路由层里做卡片 CRUD、去重检测、SSE 推流 |
| `backend/app/services/goal_service.py` | 698 | 目标 CRUD + 进度计算 + 每日推荐 |
| `backend/app/services/cleaning_service.py` | 571 | 规则去噪 + 分块 + 向量去重 + 版本快照 |

`architecture.md:69` 自述："当前 `notes.py`、`upload.py`、`understanding.py` 有大量业务逻辑直接写在路由层"。
**后果**：任何业务规则变更都要动 700+ 行文件；无法为单条规则写单测。

#### 🟠 S-3 同一件事有两套实现，且互相不知道对方存在

**证据 A —— 两套分块算法：**
- `backend/app/services/markdown_segmenter.py`（314 行）：结构感知分块，正确处理标题/表格/代码块/列表，
  明确写了"避免截断"（F-34）
- `backend/app/services/cleaning_service.py:184-435`：另一套 `split_into_chunks()`，
  按段落 + 字符数贪心打包，`chunk_size=500, overlap=50`

**同一份文档，在"清洗/存向量"和"送 LLM 理解"两条路上被切成不同的块。**
`markdown_segmenter.py` 精心避免的"表格行/代码块从中间截断"，在
`cleaning_service.split_into_chunks` 这条被真正用于向量入库的路径上完全没有生效。

**证据 B —— 两套相似度算法：**
- `embedding_service.py:304-326` `compute_similarity()`：正确的余弦相似度
- `tasks/embedding_tasks.py:246` 向量检索：`similarity = 1.0 / (1.0 + distance)`

见 §2.3 的详细分析。

#### 🟡 S-4 文档与代码严重不同步，文档本身已成为负债

- `architecture.md:193` 称"`requirements.txt` 中 alembic 被注释" —— 实际
  `backend/requirements.txt:18` 是 `alembic~=1.13.0`，**已启用**
- `architecture.md:62` 称"ORM（15 张表）" —— `database.py` 的防御性建表实际涉及
  projects / note_projects / note_material_links / note_annotations / note_versions /
  learning_goals / daily_plans 等至少 19 张表
- `README.md:238` 写后端地址 `http://localhost:8001`，而 `README.md:213` 写"Backend API（端口 8000）"
- `README.md:37` 声称"三视图：原始/清洗/行级 Diff 对比"，`architecture.md:78` 说 `client.ts` 是 2221 行，
  实际 `frontend/src/api/client.ts` 是 298 行（已拆分，文档未更新）

**后果**：文档不可信 → 没人读 → 更不可信。

---

### 2.2 数据与并发地基（最致命的一层）

#### 🔴 D-1 SQLite 承担了它无法承担的角色：多进程 + 长任务 + 高并发写

**证据**：`docker-compose.yml` 定义了 **3 个进程共享同一个 SQLite 文件**：

```yaml
backend:       volumes: [backend-data:/app/data]     # 跑 init_db()
celery-worker: volumes: [backend-data:/app/data]     # 跑 init_db()
celery-beat:   volumes: [backend-data:/app/data]     # 跑 init_db()
```

`backend/app/config.py:305-310` 默认 `sqlite+aiosqlite:///data/db/engramnote.db`。

`database.py:101-112` 的连接级 PRAGMA：

```python
cursor.execute("PRAGMA foreign_keys=ON")
cursor.execute("PRAGMA busy_timeout=5000")   # 只等 5 秒
# 缺失：PRAGMA journal_mode=WAL
```

**关键缺陷**：
1. **没有开 WAL**。SQLite 默认 `journal_mode=DELETE`，写操作会阻塞全部读操作。
   开 WAL 后读写可并发 —— 这是一行代码的修复，但没做。
2. **`busy_timeout=5000` 太短**。`config.py:164` 的 LLM 超时是 **600 秒**；
   一次理解任务的写事务可能持续很久，而 API 侧只等 5 秒就抛 `database is locked`。
3. **`max_upload_size_mb: int = 500`**（`config.py:127`），单文件 500MB，
   配合 `--pool=solo` 串行 worker，写锁持有时间可达分钟级。

**后果**：单用户尚可忍受；**2 个用户同时上传 + 1 个用户在复习 = 必然出现
`sqlite3.OperationalError: database is locked`**。这不是"可能的边界情况"，是设计必然。

#### 🔴 D-2 每个进程启动都对 5 张表做 DROP/重建，且带一条破坏性全局 SQL

`backend/app/database.py:649-761` `_rebuild_dangling_tables()`：

```python
targets = (
    ("card_relations", "card_id_1"),
    ("note_material_links", "personal_note_id"),
    ("knowledge_cards", "note_id"),
    ("quiz_items", "note_id"),
    ("review_logs", "note_id"),
)
...
for table_name in todo:
    raw.execute(f'DROP TABLE IF EXISTS "{temp_name}"')
    raw.execute(create_ddl)                                       # CREATE TABLE __trash_rebuild
    raw.execute(f'INSERT INTO "{temp_name}" ({col_list}) SELECT {col_list} FROM "{table_name}"')
    raw.execute(f'DROP TABLE "{table_name}"')                     # ← 原表被删
    raw.execute(f'ALTER TABLE "{temp_name}" RENAME TO "{table_name}"')
```

并且 `_migrate_sqlite` 里有一条**无条件的破坏性去重**（`database.py:580-587`）：

```sql
DELETE FROM card_relations WHERE id NOT IN (
    SELECT MIN(id) FROM card_relations
    GROUP BY user_id, card_id_1, card_id_2, relation_type, status
)
```

**后果**：
- 每次启动都是一次**未备份的 schema 迁移 + 数据删除**。任何一次中断（断电、OOM、Ctrl-C）
  落在 `DROP TABLE` 与 `RENAME` 之间 = **用户知识图谱部分丢失**。
- 多进程并发时靠一个文件锁 `data/db/.schema-lock` 串行化（`database.py:695-701`），
  但锁只保护重建，不保护 `_migrate_sqlite` 的 ALTER 与孤儿清理。
- 这是"生产环境不应存在的代码"。它不是技术债，是**持续的数据风险**。

#### 🔴 D-3 学习调度状态挂在 `QuizItem` 上 = 重新理解一次就抹掉学习历史

`backend/app/models/quiz_item.py:100-116`：

```python
# ---- SM-2 间隔重复调度参数 ----
interval:         Mapped[int]   = mapped_column(Integer, default=1,   nullable=False)
repetition:       Mapped[int]   = mapped_column(Integer, default=0,   nullable=False)
easiness_factor:  Mapped[float] = mapped_column(Float,   default=2.5, nullable=False)
next_review_at:   Mapped[Optional[datetime]] = mapped_column(..., index=True)
last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(...)
review_count:     Mapped[int]   = mapped_column(Integer, default=0,   nullable=False)
```

调度状态（记忆强度）是**"用户 × 题目"的联合状态**，却存在**题目实体**上。

**后果链**：
1. 用户对一篇 300 页教材做了理解 → 生成 200 张卡 + 200 道题 → 复习 3 个月，累积了真实记忆强度
2. 用户点了"重新理解"（或换了 API key 重跑、或原文更新后重跑）
3. `UnderstandingSession` 重新抽卡；旧卡片及其 `QuizItem` 被替换
4. **`interval/repetition/easiness_factor/next_review_at` 全部归零** —— 3 个月的记忆模型蒸发

更糟的是，`review_logs` 表仍在（`database.py:537-544` 的孤儿清理会把
`quiz_id` 已不存在的 `review_logs` **直接 DELETE**）。**学习记录被当作垃圾清掉了。**

#### 🔴 D-4 "状态旁载"双写：没有事务，没有真相源，失败静默

架构声称（architecture.md:179）：

> meta 镜像由 `services/vault_meta.py` 在状态变更时写穿（**失败仅记日志**）

即：**磁盘镜像是尽力而为的**。而 DB 侧又写着"DB 仍是权威源"。
但同时，`vault_path.derive_prefix()` 会**从 `note.original_file_path` 反推前缀**
（`vault_path.py:133-137`）—— **在路径无法从 DB 得到时，用磁盘当真相源**。

**同一份数据有两个真相源，且没写清楚谁是权威，崩在中间没有补偿机制。**

具体的发散点（至少 8 处）：
1. 上传：先写文件再插 DB，或反之，中间崩溃
2. 清洗：写 `{base}.clean.md` + 更新 `note.clean_md_path` + 写 meta json
3. 用户编辑笔记：写文件 + 更新 DB + 创建版本快照
4. 回收站：搬文件到 `{user_id}/trash/{note_id}/` + 置 `trashed_at`
5. 恢复：搬回 + 清 `trashed_at`
6. purge：删文件 + 删 DB 行 + 置空悬挂引用
7. 版本恢复：读版本文件 + 覆盖工作副本 + 写新版本
8. 扫描导入（`POST /projects/{id}/scan`）：读磁盘 → 建 DB 行
9. 项目重命名 / 删除：目录与标签的对应关系

#### 🟠 D-5 物理路径塌缩成单层 inbox，`base` 几乎必然冲突

`vault_path.py:78-80`：

```python
def inbox_prefix(user_id: str) -> str:
    return f"{user_id}/{INBOX_SLUG}"     # "{user_id}/inbox"
```

所有笔记 → `{user_id}/inbox/source/{base}{ext}`。
`base` 来自文件名（`derive_base()` → `PurePosixPath(note.original_file_path).stem`）。

**后果**：
- 用户上传第 2 份 `第一章.pdf`（不同课程）→ 必须靠 `_resolve_unique_base` 改名，
  磁盘上的名字与用户认知脱节
- 用户上传 `lecture.pdf` 与 `lecture.pdf` 的**不同内容**，系统静默改名，用户无从知晓
- `Projects` 页面展示每个项目的 **Vault 路径**（README:87）实际是同一个 inbox —— UI 在说谎
- 万级文件单目录：Windows 上 `readdir` 与杀毒软件扫描都会显著拖慢

#### 🟠 D-6 一次 Celery 任务 = 一个事件循环；异步引擎、信号量、限流锁全部跨 loop 复用

`backend/app/tasks/{convert,clean,understand,embedding,reminder}_tasks.py` 中：

```python
# understand_tasks.py:415-417
asyncio.run(_update_note_status(note_id, NoteStatus.learning))
asyncio.run(_understand_document(note_id))
```

实测 `asyncio.run(` 出现 **20 处**。

同时 `backend/app/database.py:46-94` 的 `get_engine()` / `get_session_factory()` 是
**`@lru_cache` 进程级单例**。SQLAlchemy `AsyncEngine` 的连接池是**按事件循环分桶**的。

**后果**：
1. 每次 `asyncio.run()` 建新 loop → 引擎为该 loop 建一个新连接池桶 →
   loop 关闭时桶里的连接**不会归还/关闭** → **文件描述符与内存泄漏**。
   一次理解任务有 6+ 次 `asyncio.run()`，跑 100 篇笔记 = 数百个泄漏的连接。
2. `backend/app/services/llm_service.py:61-80` 的限流器与信号量是**类级**的：
   ```python
   LLMService._rate_limiter: Optional["RateLimiter"] = None
   LLMService._semaphore: Optional[asyncio.Semaphore] = None
   if LLMService._semaphore is None:
       LLMService._semaphore = asyncio.Semaphore(3)
   ```
   `asyncio.Semaphore` 与 `RateLimiter` 内部的 `asyncio.Lock`
   （`backend/app/services/llm/rate_limit.py:25`）都在**contention 时绑定当时的事件循环**。
   第一个任务创建 loop A → lock 绑 A；第二个任务建 loop B → 竞争时 `await lock.acquire()`
   在 B 上抛 `RuntimeError: ... is bound to a different event loop`。
   **限流器要么崩，要么在无竞争时静默失效** —— 后者意味着**限流根本没生效**。
3. 作者已经知道部分现象并在 `backend/app/services/llm/client.py:26-31` 打了补丁
   （检测 loop 变化就丢弃 httpx client），但**只补了 HTTP 客户端，没补信号量、没补限流锁、
   没补数据库引擎**。这是典型的"补一个漏三个"。

#### 🟠 D-7 Celery 配置：任务会丢，且长任务会永久占死 worker

`backend/app/tasks/celery_app.py`：

```python
task_acks_late=True,                       # 好的
worker_prefetch_multiplier=1,              # 好的
result_expires=3600,
broker_transport_options={"data_folder_in": ..., "data_folder_out": ...},
# 缺失：visibility_timeout / task_time_limit / soft_time_limit /
#       acks_on_failure_or_timeout / task_reject_on_worker_lost（仅 embedding 任务单独设了）
```

关键点：
1. **文件系统 broker 本质上不可靠**。它是"往目录里放文件、worker 移动文件"。
   `task_acks_late=True` + 文件 broker + worker 崩溃 = **消息已被移出队列，但任务未完成 → 任务永久丢失**。
   `task_reject_on_worker_lost` 只在 `embedding_tasks.py:96,268` 两个任务上设了，
   convert / clean / understand **三个关键任务都没设**。
2. **无 `task_time_limit` / `soft_time_limit`**。`config.py:164` 的 LLM 超时是 600 秒，
   一个批次 3 章 × 多批次 + 重试 = 单个任务可能跑 1 小时。
   配合 `--pool=solo`（README:226、start.bat）**一次只能跑一个任务**：
   一个用户传一本厚书 → **全站所有用户的转换/清洗/理解全部排队等待**。
3. `understand_document_task` 还额外加了 `rate_limit="3/m"`（`understand_tasks.py:403`），
   在所有用户之间共享 —— **全站理解吞吐上限 = 3 篇/分钟**。
4. **失败是静默的、不完整的**。`generate_questions_task`（`understand_tasks.py:453-462`）：

```python
except Exception as exc:
    logger.error(f"题目生成任务异常 (note_id={note_id}): {exc}", exc_info=True)
    try:
        self.retry(exc=exc)
    except Retry:
        raise
    except Exception:
        logger.error(f"题目生成任务重试失败 (note_id={note_id}): {exc}")   # ← 只记日志
```

   重试耗尽后**只打日志**：笔记状态停在"已理解"，**卡片有、题目没有**，
   用户在 UI 上看到的是一个"看起来成功但功能缺失"的页面，**没有任何错误提示**。

#### 🟡 D-8 无进度、无重试入口、无自愈

`backend/app/tasks/common.py:26` 只有 `update_note_status` 的字段白名单，
**没有进度模型**。`understanding.py:206` 的 `GET /{note_id}/status` 只能返回状态枚举。

**后果**：一本厚书的理解过程可能持续 40+ 分钟，UI 只能显示"学习中"转圈，
用户不知道是 10% 还是 90%，也无法判断是不是卡死了。没有"取消任务"。
没有"清理僵尸任务"的定时任务（worker 崩溃后笔记永久停在 `converting`/`cleaning`/`learning`）。

#### 🟡 D-9 48 个 Chroma collection：每篇笔记一个，检索要遍历全部

`backend/app/services/embedding_service.py:366-373`：collection 名 = `note_{note_id}`。
`backend/app/tasks/embedding_tasks.py:196-262`：

```python
result = await session.execute(select(Note).where(Note.user_id == user_id, Note.trashed_at.is_(None)))
notes = result.scalars().all()
for note in notes:                                        # ← O(笔记数)
    def _search_note_collection(note_id=note.id, ...):
        collection = vector_store._client.get_collection(collection_name)
        if not _collection_matches_current_model(collection): return []
        count = collection.count()
        query_results = collection.query(query_embeddings=[question_embedding], n_results=min(3, count), ...)
    note_results = await asyncio.to_thread(_search_note_collection)   # ← 每条笔记一次线程 + 一次 Chroma 查询
    all_results.extend(note_results)
```

**后果**：一次 RAG 问答 = 1 次 Celery 编码往返 + **N 次 Chroma 集合加载 + N 次查询**。
实测 `backend/data/chroma/` 下已有 **90+ 个 collection 目录**。
用户有 200 篇笔记时，每次提问要做 200 次向量查询 —— 秒级延迟，且随笔记数线性劣化。

#### 🟡 D-10 检索结果去重键设计错误

`backend/app/services/rag_service.py:342-343`：

```python
content = item.get("content", "") or ""
dedupe_key = (note_id, content[:200])
```

用"内容前 200 字符"当身份。两块内容前 200 字相同但后半不同（模板化文本、表格续表、
定理重复陈述）会被**误判为同一块**；两块内容前 200 字不同但语义相同则**不去重**。

`rag_service.py:518-524` 又按 `note_id` 二次去重，**同一篇笔记只能出现一个引用来源** →
引用粒度退化为"笔记级"，用户无法定位到段落。

#### 🟡 D-11 表结构强制 nullable 与 FK 语义靠"启动时重建"来对齐

对比 `backend/app/models/quiz_item.py:85-89`：

```python
card_id: Mapped[str] = mapped_column(String, ForeignKey("knowledge_cards.id"), index=True, nullable=False)
note_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("notes.id", ondelete="SET NULL"), ..., nullable=True)
```

与 `database.py:683-689` 的重建目标表里包含 `quiz_items`（探测 `note_id` 的 notnull 位）。
即：**如果 `note_id` 是 NOT NULL，启动时就把整张表 DROP 重建**。
schema 的"真实定义"分散在 model、`init_db` 的 create_all、`_migrate_sqlite` 的手写 DDL、
`_rebuild_dangling_tables` 的 metadata-驱动 DDL、以及 alembic 迁移 —— **五处**。

#### ⚪ D-12 时间语义不统一

- `models/base.py:29-53` `TZDateTime`：SQLite 上把 aware → naive UTC 存，读回补 UTC
- `models/note.py:152`：`trashed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), ...)`
  —— 用的是原生 `DateTime`，**不是 `TZDateTime`**
- `database.py:259` 手写 DDL：`created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL`
  —— SQLite 的 `CURRENT_TIMESTAMP` 是 naive UTC，且**秒级精度**
- `backend/app/utils/timeutil.py` 又引入了"业务日界 = Asia/Shanghai 零点"

三套时间语义混用，注定在跨日界、跨时区、跨 DB 方言时出错。

---

### 2.3 AI 管道：看起来很强，实际是空转

#### 🔴 A-1 向量相似度公式是错的，导致 RRF 的"向量路"退化为噪声

`backend/app/tasks/embedding_tasks.py:244-246`：

```python
distance = query_results["distances"][0][i]
similarity = 1.0 / (1.0 + distance)
```

Chroma 默认距离度量是 **L2（平方欧氏距离）**，且
`embedding_service.py:424-426` 创建 collection 时**没有指定 `hnsw:space`**：

```python
collection = self._client.get_or_create_collection(
    name=collection_name,
    metadata=collection_metadata,
)
```

BGE-M3 输出**已归一化**（见下方"归一化核实"），所以向量间平方 L2 距离 ∈ [0, 4]，
且 `d = 2(1 − cos)`，即**正确的相似度应为 `sim = 1 − d/2`**。

**代入验证**（对比代码实际算法与正确算法）：

| 语义关系 | 真实 cos | L2² = d | 正确 `1−d/2` | 代码 `1/(1+d)` |
|---|---|---|---|---|
| 完全相同 | 1.00 | 0 | 1.000 | 1.000 |
| 高度相关 | 0.80 | 0.4 | 0.800 | 0.714 |
| **完全无关（正交）** | **0.00** | **2.0** | **0.000** | **0.333** |
| 完全相反 | −1.00 | 4.0 | −1.000 | 0.200 |

**结论：代码把"完全无关"算成了 0.333，把余弦 0.4 的弱相关内容算成 0.4 ——两者几乎无法区分。**
所有结果被压缩到 `[0.2, 1.0]`，且**不可反向映射回余弦**，因此：
- 不能用于阈值过滤（"相似度 > 0.5"这个判断在语义上不成立）
- `retrieval_status` 里"向量通道完整返回"（`rag_service.py:474-479`）无法反映检索质量
- RRF 只用**排名**而非分数，所以没有被这个问题直接污染 —— 但排序本身是错的
  （两个高度相关的块和一个完全无关的块可能被排成相邻名次）

> **附注 · 归一化核实（已定论）**：审计过程中曾出现两种相反的说法，最终通过
> 逐行阅读已安装的 `sentence-transformers` 源码确定结论：
>
> - `SentenceTransformer.encode()`（`sentence_transformer/model.py:481-493`）把
>   `normalize_embeddings: bool = False` 声明为**具名参数**，因此它
>   **不会**被 `**kwargs` 捕获，也就**不会**传给 `self.forward(features, **kwargs)`（`:657`）。
> - `BaseModel.forward()`（`base/model.py:477-493`）**无条件遍历全部子模块**：
>   ```python
>   for module_name, module in self.named_children():
>       module_kwargs = {k: v for k, v in kwargs.items()
>                        if k in module_kwarg_keys
>                        or (hasattr(module, "forward_kwargs") and k in module.forward_kwargs)}
>       input = module(input, **module_kwargs)
>   ```
> - `Normalize`（`sentence_transformer/modules/normalize.py`）只定义
>   `forward(self, features)`，**未声明 `forward_kwargs`**，其基类
>   `base/modules/module.py:74` 的默认值是 `forward_kwargs: set[str] = set()`（空集）。
>
> **结论**：`2_Normalize` 模块**始终执行**，两个候选模型的输出**都是单位向量**。
> `encode()` 里 `if normalize_embeddings:`（`:685-686`）只是**第二次**归一化（幂等）。
> 两个模型 `modules.json` 也确实逐字节相同（SHA256 均为 `84E40C8E...F642CF`）。
>
> **因此这条从"致命"降为轻微**，但保留一个真实隐患：
> **相似度的正确性隐式依赖"模型仓库带尾部 Normalize 模块 + 该模块不接受该 kwarg"这个组合**。
> 换用不带尾部 Normalize 的模型时，`compute_similarity`（自带范数）仍正确，
> 但任何新增的"点积即相似度"代码会静默出错；0.92 / 0.75 两个阈值也必须重新校准。
> **修复方向**：在 `encode()` 显式写出意图并加注释锁定，
> 把"是否归一化"纳入 collection metadata 的版本标识。

更严重的是**同一份数据在两处用了两套度量**：
- 清洗去重用 `embedding_service.compute_similarity()` → **正确余弦相似度**（阈值 0.92）
- RAG 检索用 `1/(1+distance)` → **错误的伪相似度**

**修复方向**：建 collection 时显式 `metadata={"hnsw:space": "cosine"}`，
检索时 `similarity = 1.0 - distance`；或干脆统一用 `compute_similarity`。
但注意：**改度量需要重建全部 90+ 个 collection**。

#### 🔴 A-2 RAG 的检索语料是"LLM 的摘要"，不是原文

`backend/app/services/rag_service.py:93-106`（BM25 与 n-gram 两路的语料来源）：

```python
result = await session.execute(
    select(KnowledgeCard).where(
        KnowledgeCard.user_id == user_id,
        or_(KnowledgeCard.note_id.is_(None), select(Note.id).where(...).exists()),
    )
)
cards = list(result.scalars().all())
```

`_search_bm25`（`rag_service.py:226`）与 `_search_relevant_cards`（`rag_service.py:389`）
**都只检索 `KnowledgeCard`**。只有向量路（从 Chroma）可能命中原文分块。

**后果**：
1. 当向量路降级（`retrieval_status = "bm25_only"`，见 `rag_service.py:474-475`），
   **RAG 完全在卡片上检索** —— 等于"用 LLM 的摘要去回答关于原文的问题"。
2. 卡片是 LLM 抽取的**有损压缩**。用户问"这个定理的证明第三步是什么"，
   卡片里没有；系统会回答"没有找到相关信息"或**用 LLM 自己的知识编一个**。
3. **产品的核心承诺（"基于你的资料回答"）在最常见的降级路径上不成立。**

#### 🔴 A-3 `rag_answer` 的提示词主动允许模型脱离资料回答

`backend/app/services/llm_service.py:679-687`：

```
"回答原则：\n"
"1. 优先使用参考资料：如果参考资料中包含相关信息，请以其为主要依据\n"
"2. 自主知识补充：如果参考资料不足或没有相关信息，可以结合你自己的知识来回答，"
"但请说明这部分是基于你的知识补充的\n"
"3. 诚实标注：...\n"
"4. 回答应详细有用：不要简单地回复没有相关信息，而是尽力提供有价值的回答\n"
"5. 适当引用：回答中可引用参考资料中的原文来增强可信度"
```

**三条独立的反 RAG 指令**：
- 第 2 条：允许引入模型自身知识（污染）
- 第 4 条：**明确禁止说"没有相关信息"** ← 直接命令模型**编造**
- 第 5 条：鼓励"可引用原文" —— 没有要求"必须逐字来自上下文"，也没有要求标注来源

再叠加 `rag_service.py:590-609`：上下文为空时直接切换成"用你自己的知识回答"，
但返回体里**仍然带着 `sources: []` 和一个 `retrieval_status`**，前端 `QA.tsx`
难以区分"基于你的笔记回答"和"完全靠模型常识回答"。

**后果**：对一个学习工具，"看起来有引用、实际是模型幻觉"是最严重的失败模式。
用户会记住错的答案，并且以为它来自自己的教材。

#### 🟠 A-4 中文 BM25 的分词器有实质性缺陷

`backend/app/services/rag_service.py:169-195`：

```python
# 提取中文字符的 2-gram
chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)     # ← 先剥掉所有非中文字符
for i in range(len(chinese_chars) - 1):
    tokens.append(chinese_chars[i] + chinese_chars[i + 1])
```

**缺陷**：`re.findall(r"[\u4e00-\u9fff]", text)` 把所有中文字符**拉平成一个无边界序列**，
**跨越了原有的分隔符**。

举例：`"学习 Python 编程"` →
`chinese_chars = ['学','习','编','程']` → bigram = `['学习', '习编', '编程']`

其中 **`'习编'` 是一个不存在的词**，而且它把"习"和"编"这两个原本被 `Python` 隔开的字
错误地粘在了一起。同理 `"机器 学习"` → `['机器','器学','学习']`，
`'器学'` 是噪声词元，会与任何含"器"和"学"相邻的文档产生**假匹配**。

另外：**中文同义词、繁简、英文大小写以外的形态变化完全没有归一化**；
BM25 的 IDF 在全量卡片上算（每次问答重算，见下）。

#### 🟠 A-5 BM25 每次提问都全量重建索引，且用 Python 循环打分

`rag_service.py:226-301`：`_search_bm25` 每次调用都
1. 拉取该用户**全部卡片**（`_get_user_cards`，有 60 秒 TTL 缓存）
2. 对每张卡片重新分词（`self._tokenize(doc_text)`）
3. 重建 `df` / `idf` 字典
4. 对每个 (doc, query_token) 组合做 Python 层循环打分

**复杂度**：O(N × L) 分词 + O(N × |Q|) 打分，N = 卡片数，L = 平均卡片长度。
1000 张卡片时，**每次提问要做几十万次 Python 字符串操作**。

且 `_kb_cache` 是**模块级无上限字典**（`rag_service.py:47`）：
`_kb_cache: Dict[str, tuple[float, List[KnowledgeCard]]] = {}` —— 缓存**完整 ORM 对象**，
多用户长时间运行会持续增长，无淘汰策略。

#### 🟠 A-6 n-gram 通道是与 BM25 高度重复的弱检索器，却获得同等 RRF 权重

`rag_service.py:394-419`：

```python
for length in range(4, 1, -1):        # 4字、3字、2字
    for i in range(len(question_lower) - length + 1):
        question_keywords.add(question_lower[i:i+length])
question_keywords = {kw for kw in question_keywords if len(kw) >= 2}
...
score += len(kw)                       # 更长的匹配给更高分
```

这是一个纯字符子串计数打分器，**与 BM25 检索的是同一批卡片**
（`_get_user_cards` 被两个方法各调一次，靠 60s 缓存共享）。

而 RRF 融合（`rag_service.py:338-361`）：

```python
for result_list in [vector_results, bm25_results, ngram_results]:
    for rank_idx, item in enumerate(result_list):
        rrf_score = 1.0 / (k + rank_idx + 1)      # 等权，k=60
        fused[dedupe_key]["similarity"] += rrf_score
```

**三路等权**。但 n-gram 是比 BM25 **更弱**的关键词匹配（无 IDF、无长度归一化），
把它与向量、BM25 等权融合 = **给最终排序注入系统性噪声**。
标准做法是给三路不同权重（如向量 0.5 / BM25 0.35 / n-gram 0.15），或干脆去掉 n-gram。

#### 🟠 A-7 两套 Key 体系不一致，RRF 结果无法与引用对齐

向量路返回的 key 是 `(note_id, content[:200])`，BM25/n-gram 路返回的 item
带有额外的 `card_id` / `title` / `chapter_title`（`rag_service.py:295-299, 357-359`）。

融合时取**先到者**的字段（`rag_service.py:348-360`）：如果向量路先插入，
后续 BM25 命中同一 key 的 `card_id` / `chapter_title` 会被**丢弃**（只累加分数）。

**后果**：`sources` 里的 `chapter_title` 时有时无，
用户看到"来源：某笔记（章节：无）"—— 引用质量不稳定。

#### 🟠 A-8 嵌入模型降级会让整库向量静默失效

`backend/app/services/embedding_service.py:66-93` `_pick_embedding_model_name()`：
内存不足时自动从 `BAAI/bge-m3`（**1024 维**）降级到 `BAAI/bge-small-zh-v1.5`（**512 维**）。

`backend/app/tasks/embedding_tasks.py:139-164`：

```python
def _collection_matches_current_model(collection) -> bool:
    collection_meta = collection.metadata or {}
    stored_model = collection_meta.get("embedding_model")
    if stored_model is None:
        return True                       # 旧数据无记录：尝试查询
    current_model = EmbeddingService().loaded_model_name
    if not current_model:
        return True                       # 模型未加载：无从比对
    return stored_model == current_model
```

`embedding_tasks.py:219-228`：**模型不匹配时 `return []` —— 静默跳过该 collection。**

**后果**：机器内存降到 4GB 阈值以下（`config.py:142` `embedding_min_free_memory_gb: float = 4.0`）
→ 加载降级模型 → **所有用 bge-m3 建的向量全部被跳过** → 用户提问时
`retrieval_status` 变成 `hybrid`，向量路贡献 0，**用户完全不会被告知"你的向量库暂时失效了"**。

而且 `loaded_model_name` 在 `_ensure_model()` 之前是 `None`，
此时 `_collection_matches_current_model` **无条件 `return True`** ——
第一次检索会用错误维度去查，靠"下层异常兜底"（代码注释原话）。

#### 🟠 A-9 相似度计算全部在 Python 层做 O(n²)

`backend/app/services/embedding_service.py:453-535` `find_duplicates()`：

```python
all_data = collection.get(include=["embeddings", "metadatas", "documents"])
...
similarity = EmbeddingService.compute_similarity(       # Python 层余弦
    embeddings[i], embeddings[j]
)
if similarity >= threshold: ...
```

对 300 页 PDF（约 500-800 个 chunk）：
- `collection.get(include=["embeddings", ...])` 把**全部 1024 维向量拉进内存**
- 然后 **O(n²) 双重循环**，每个 pair 一次 1024 维点积 + 两次模长

约 800 个 chunk → 320,000 次 1024 维点积 ≈ **3 亿次浮点乘加，且是纯 Python 循环**
（`sum(a*b for a,b in zip(...))`，非 numpy 向量化）。这是**分钟级**的 CPU 占用。

Chroma 本身就支持 `cosine` 空间 + ANN 索引 —— **完全没用上**。

#### 🟡 A-10 卡片抽取无质量门、无去重、无再跑幂等性

`backend/app/services/understanding_service.py:226-280` `save_knowledge_cards()`：

```python
for point in knowledge_points:
    card_type_str = point.get("card_type", "concept")
    try:
        card_type = CardType(card_type_str)
    except ValueError:
        card_type = CardType.concept          # ← 非法类型静默降级
    source_text = point.get("source_text", "")
    if len(source_text) > 5000:
        source_text = source_text[:5000]      # ← 截断，无提示
    card = KnowledgeCard(
        user_id=user_id, note_id=note_id, card_type=card_type,
        title=point.get("title", "未命名知识点"),      # ← 空标题静默兜底
        content=point.get("content", ""),              # ← 空内容也直接入库
        ...
    )
    db.add(card)
await db.commit()
for card in cards:
    await db.refresh(card)                     # ← N 次额外往返
```

**缺陷清单**：
- **无内容校验**：`content` 可以为空字符串，卡片照样入库
- **无长度/数量上限**：一次 LLM 调用生成多少张卡就存多少，无上限
- **无跨批次去重**：`process_note_understanding` 按 `batch_size = 3` 分批
  （`understanding_service.py:393`），**不同批次可能抽出同一张卡**（章节重叠处尤其容易）
- **无幂等**：重跑理解 = 追加新卡片，旧卡片不会被清理，用户会看到重复卡片
- **无质量评估**：没有任何机制判断"这张卡是否值得复习"（可答性、原子性、自足性）
- **`await db.refresh(card)` 在循环里** → N 次 round-trip

#### 🟡 A-11 全站 LLM 限流 10 RPM，且是进程级的

`backend/app/config.py:160`：`llm_max_rpm: int = 10`
`backend/app/services/llm_service.py:78`：`asyncio.Semaphore(3)`

**注意 `docker-compose.yml` 里 worker 是单进程**，所以"类级共享"实际就是"全站共享"：
**整个系统对 LLM 的调用上限 = 10 RPM**。

而理解一篇文档的调用次数：
- 30 章 ÷ batch_size 3 = **10 次** LLM 调用
- 题生成：每批卡片一次 → 若干次
- 图谱关系推断：若干次

**一本 300 页教材 ≈ 15-20 次 LLM 调用，按 10 RPM 计算纯限流耗时 ≈ 2 分钟**，
但这只是限流下界；实际每次调用是分钟级（`config.py:164` 超时 600s），
**单篇文档端到端 30-60 分钟**。且**所有用户共享这 10 RPM**。

#### 🟡 A-12 没有 token / 成本账本

`backend/app/services/llm_service.py:178-183`：

```python
logger.info(
    f"LLM 响应 | scene={scene} | provider={self._provider} | model={self._model} | "
    f"prompt_tokens={usage.get('prompt_tokens')} | completion_tokens={usage.get('completion_tokens')} | "
    f"total_tokens={usage.get('total_tokens')} | finish_reason={finish_reason} | "
    f"truncated={truncated} | elapsed={elapsed_ms:.0f}ms"
)
```

`usage` **只进日志**。没有写库、没有按用户/按笔记聚合、没有配额、没有告警。

**后果**：
- 用户无法知道"处理这篇 PDF 花了多少钱"
- 无法回答"哪个功能最烧钱"
- 无法设配额（`config.py:129` 有 `max_storage_per_user_mb` 磁盘配额，
  **但没有任何 token/成本配额** —— 而 LLM 成本远高于磁盘成本）
- `generate_questions` 里还留着调试日志（`llm_service.py:634-654`）：
  ```python
  logger.info(f"批量题目生成原始响应类型: {type(result)}, 键: {list(result.keys()) if ...}")
  logger.info(f"键 '{key}' 的值类型: {type(items)}, 长度: {len(items) if ...}")
  logger.info(f"第一个元素是字典，键: {list(actual.keys())}")
  ```
  **生产代码里的开发期调试输出**。

#### 🟡 A-13 知识图谱是 O(n²) 嵌入比较 + 高成本 LLM 推断

`backend/app/services/graph_service.py`（908 行）：
- `suggest_semantic_relations()` 对卡片做**两两嵌入相似度**（O(n²)）
- 超过阈值的 pair 再送 LLM 做关系类型推断（prerequisite / subsequent / contrast）

**规模问题**：500 张卡片 → 124,750 个 pair。即使只在候选集上做 LLM 推断，
成本与延迟都是**不可控的**（且用户点了按钮之后，没有进度反馈，见 D-8）。

#### 🟡 A-14 RRF 的 `k=60` 是照搬的默认值，没有针对场景调参

`rag_service.py:309` `k: int = 60`，`rag_service.py:497` 传 `k=60`。
k=60 来自 Cormack 等人 2009 年的 TREC 论文，用于**多个独立系统的排名融合**。
本项目里三路高度相关（同一批卡片、同一批 chunk），
`k=60` 会让 `1/(60+1)` 到 `1/(60+5)` 的分数差极小 → **头部排序几乎由"哪路先返回"决定**。

#### ⚪ A-15 MinerU 路径的健壮性未验证

`backend/app/services/mineru/converter.py` 956 行，含云端 API 与本地 pipeline 双模式。
`config.py:121` 默认 `mineru_server_url = "https://mineru.net/api/v4/extract/task"`。
云端模式下，**上游 API 变更/限流/长时间排队**没有任何可观测性，
`converting` 状态可能无限期停留。

---

### 2.4 学习算法：本项目的立身之本，也是最大的空转

> 这一节是最重要的。前面所有问题都是"工程实现不好，可以重写"。
> 这一节的问题是：**即使工程完美，产品也不成立。**

#### 🔴 L-1 判分器无法识别语义反转：**逻辑完全相反的答案被判为"回忆成功"**

> 这一条是**实机验证**的，不是推断。我用 `sm2_service.py` 的原始算法
> 对真实题目做了回归测试，结果如下。

`backend/app/services/sm2_service.py:196-252` `_score_short_answer()` 用
**无序字符 n-gram 集合的交并比**判分：

```python
def extract_keywords(text: str) -> set:
    keywords = set()
    for length in range(4, 1, -1):                    # 4/3/2 字
        for i in range(len(text) - length + 1):
            segment = text[i:i + length]
            if any('\u4e00' <= c <= '\u9fff' for c in segment):
                keywords.add(segment)                 # ← 所有连续子串都算"关键词"

matched = len(user_keywords & correct_keywords)       # ← 集合交集：完全不看词序
coverage = matched / total
if   coverage >= 0.8: return 5
elif coverage >= 0.6: return 4
elif coverage >= 0.4: return 3     # ← quality >= 3 即 SM-2 判为"回忆成功"
```

**实机回归结果**（正确答案 62 字 → 125 个 n-gram）：

| 用户答案 | coverage | quality | SM-2 判定 |
|---|---|---|---|
| 完全正确答案 | 100.0% | 5 | ✅ 通过 |
| 语义正确的改写 | 78.4% | 4 | ✅ 通过 |
| **把正确答案的逻辑全部反转**（"不需要从数据学习…必须人为编写规则…"） | **69.6%** | **4** | **✅ 通过** |
| 字序完全打乱 | 0.8% | 1 | 拒绝 |
| 常用汉字随机串 | 0.0% | 1 | 拒绝 |
| 正确但极短的答案（"机器学习"） | 4.8% | 1 | **❌ 误杀** |
| 长而空洞的同主题套话 | 9.6% | 1 | 拒绝 |

**这个结果比"能被随机文本骗过"更糟 —— 它精确地错在最危险的地方：**

1. **语义反转得 69.6%，判为 `quality=4`（"正确但有些犹豫"）**。
   因为 n-gram 是**无序集合交集**，`不需要` / `必须` / `不是` / `不能`
   这些反转词反而**增加了覆盖率**（它们是正确答案里真实出现过的子串）。
   **系统对"否定"完全无感。**
2. **一字不差的正确答案得 5，逻辑完全相反的答案得 4** —— 只差 1 分。
   在 SM-2 里 `quality=4` 与 `quality=5` 会得到**同样的间隔**（见 L-4 第 1 点），
   **所以正确答案与相反答案的调度结果完全相同。**
3. **正确但简短的答案被误杀**（coverage 4.8% → `quality=1` → 间隔重置为 1 天）。
   这惩罚了"记得牢、能精炼表达"的学习者 —— 与学习科学完全相反。

**后果**：用户写出与事实**完全相反**的答案 → 系统认为他记住了 → 把卡片推到远期 →
**用户在错误的认知上建立了信心，而且系统主动帮他遗忘正确答案。**
对一个学习工具，这比崩溃严重得多。

**填空题判分同样确证有缺陷**（`sm2_service.py:169-189`）：

```python
user_words = set(user_lower)          # ← 单字符集合，不是词集合
correct_words = set(correct_lower)
if len(user_words & correct_words) / len(correct_words) >= 0.5:
    return 3                          # 判为正确
```

实机验证（正确答案 `机器学习`，4 个字）：
`学器` → 3 ✅ / `机学` → 3 ✅ / `器学` → 3 ✅ / `学习` → 3 ✅ / `机器` → 3 ✅

**任意两个正确字的乱序组合都判为正确。** 代码注释自陈其成因
（`sm2_service.py:181-182`）：

> 阈值 >= 0.5（原 > 0.5 会把"机器"vs"机器学习"的 2/4=0.5 前缀部分匹配挡在 3 分之外，
> 误判为 1 分，见 docs/decisions.md#F-35）

**为了让"机器"="机器学习"通过而把阈值降到 0.5，附带把"学器"也放行了。**
这就是"打补丁式修复引入新缺陷"的教科书案例。

#### 🔴 L-2 "掌握度"公式不度量掌握，且几乎没有方差

`backend/app/services/mastery_service.py:1-8`（模块 docstring 原文）：

```
公式（Q8 已确认）：mastery = 正确率 × 60% + 标准化 SM-2 状态 × 40%
- 正确率 = 最近 5 次 ReviewLog 中正确次数 / min(5, 实际次数)
- 标准化 SM-2 = (easiness_factor - 1.3) / (2.8 - 1.3) × 0.5 + min(repetition / 10, 1) × 0.5，裁剪到 0-1
- 未复习过的卡片 mastery_level = 0
```

代码实现（`mastery_service.py:56-81`）：

```python
actual_count = len(recent_reviews)
correct_count = sum(1 for r in recent_reviews if r.is_correct)
accuracy = correct_count / min(5, actual_count)

ef_norm = (avg_ef - 1.3) / (2.8 - 1.3)      # 注意：2.8 是硬编码上界
ef_norm = max(0.0, min(1.0, ef_norm))
rep_norm = avg_rep / 10
rep_norm = max(0.0, min(1.0, rep_norm))
normalized_sm2 = ef_norm * 0.5 + rep_norm * 0.5
mastery = (accuracy * 0.6 + normalized_sm2 * 0.4) * 100
```

**问题**：

1. **它是"调度器状态"的复述，不是"记忆强度"的估计。**
   `easiness_factor` 和 `repetition` 是 SM-2 的**内部中间变量**，
   把它们线性映射再加权，得到的只是"SM-2 走到哪一步了"。
   它**不包含时间维度** —— 一张 3 个月前复习过的卡片和昨天复习过的卡片，
   只要 `EF` 和 `repetition` 相同，掌握度**完全相同**。而"记忆随时间衰减"
   正是间隔重复要建模的唯一现象。

2. **下限虚高**：`easiness_factor` 默认 2.5，代入
   `ef_norm = (2.5-1.3)/(2.8-1.3) = 0.8`，`normalized_sm2 ≥ 0.8×0.5 = 0.4`
   → 即使 `accuracy = 0`、`repetition = 0`，
   `mastery = 0 × 0.6 + 0.4 × 0.4 = 0.16` → **16 分**。
   **从未答对的卡片，掌握度是 16 而不是 0。**

3. **上限虚低**：卡片的 `EF` 会被 SM-2 公式持续调低（低于 2.5 是常态），
   所以 `ef_norm` 长期低于 0.8 → `normalized_sm2 < 0.4`
   → 即使 5 次全对（`accuracy = 1`），`mastery ≤ 0.6 + 0.4×0.4 = 76 分`。
   **满分复习也到不了 80。**

4. **`2.8` 这个上界没有依据**：`EF` 理论上无上界（SM-2 原始论文 EF 可 > 2.8）。
   用 2.8 归一化会让高分区间被压缩。

5. **`avg_ef` / `avg_rep` 是跨题目平均**（`mastery_service.py:61-69`）：
   ```python
   select(func.avg(QuizItem.easiness_factor), func.avg(QuizItem.repetition))
       .where(QuizItem.card_id == card_id)
   ```
   一张卡有 3 道题，其中一道反复答错、两道一直答对 → 平均后**错误被稀释**。

6. **"正确率"依赖 L-1 的错误判分** → 整个公式的地基是错的。

**结论**：`mastery_level` 这个字段，**既不能反映记忆强度，也不能反映正确率，
数值范围还被压缩到 [16, 76]**。它是仪表盘上一个好看的、但**没有任何预测效度**的数字。

#### 🔴 L-3 没有"学习效果"的度量 —— 整个产品无法验证自己是否有效

搜索整个 backend：

```
grep -r "retention\|recall_rate\|calibration\|confidence\|evaluation\|metric" backend/app/
→ 无任何学习效果度量
```

现有的"评估"（`assessment_service.py`）是**另一套 LLM 打分**：

```python
"coverage_score": <0-100>,   # 覆盖率
"depth_score": <0-100>,      # 深度
"clarity_score": <0-100>,    # 清晰度
"overall_score": <0-100>,
```

这是**让 LLM 给用户自己写的笔记打分**（"笔记 vs 资料"比对），
分数是 LLM 主观生成的，**没有 ground truth，没有校准，没有复测**。

同样，`submit_answers` 里的 `accuracy_score / completeness_score / depth_score /
question_score` 也是 LLM 打分（`assessment_service.py:296-299`）。

**根本缺失**：
- ❌ 没有"学完 N 天后的保持率"（真正的学习效果指标）
- ❌ 没有"预测正确率 vs 实际正确率"的校准曲线（confidence calibration）
- ❌ 没有 A/B 能力（无法验证"AI 抽卡是否真的帮助了记忆"）
- ❌ 没有"卡片质量"的下游反馈（哪张卡总是答错 → 可能是卡片写得不好）
- ❌ 没有间隔预测误差（SM-2 预测 R=0.9 时实际正确率是多少？）

**后果**：这个产品**无法回答"它有没有用"**。
它只能报告"你复习了 30 次""你掌握了 76%"——都是活动量，不是效果。

#### 🟠 L-4 SM-2 本身已被 FSRS 取代，且实现有多处偏离

**算法选型问题**：SM-2 是 1987 年的算法。现代 SRS（Anki 23.10+ 默认）已迁移到
**FSRS（Free Spaced Repetition Scheduler）**，它用 DSR 三参数模型
（Difficulty / Stability / Retrievability）拟合真实复习日志，
在公开数据集上**同等保持率下减少 20-30% 的复习量**。

**实现层面的偏离**（`sm2_service.py:52-112`）：

```python
if quality >= 3:
    new_repetition = repetition + 1
    if new_repetition == 1:   new_interval = 1
    elif new_repetition == 2: new_interval = 6
    else:                     new_interval = round(interval * new_ef)
else:
    new_repetition = 0
    new_interval = 1
```

1. **忽略 `quality` 的强度差异**：`quality=3`（勉强记起）和 `quality=5`（轻松）
   得到**完全相同的间隔**。这是 SM-2 的已知弱点，但至少应该乘一个
   `quality` 相关的系数（SM-2 变体 SM-2+ / SM-15 都做了）。
2. **`next_review_at = datetime.now(timezone.utc) + timedelta(days=new_interval)`**
   （`sm2_service.py:105`）：
   - 用 **now** 而不是**计划复习时间**作为基准。用户提前 5 天复习，
     新间隔从"提前的那天"起算 → **间隔被系统性缩短**
   - 加上 `days`（24 小时的整数倍）而不是对齐到"用户的学习时段"（通常凌晨/早晨）
     → 复习时间会漂移，用户可能需要在凌晨 2 点复习
3. **无 leech 处理**：反复答错的卡片永远不会被识别出来。
   Anki 的 leech 机制（连续答错 8 次 → 暂停 + 提示重写卡片）在这里完全缺失。
4. **无 `interval` 上限**，也无 fuzz（随机抖动）。
   大量卡片会在同一天到期 → "复习雪崩"。
5. **`quality_from_answer` 不接收"用户自评"**。
   学习者对自己回忆难度的主观评估（Anki 的 Again/Hard/Good/Easy）
   是 SRS 最有价值的信号之一，这里完全丢弃（只对选择题/填空题做机器判分）。

#### 🟠 L-5 复习状态挂在题目上，无法复习"没有题目的卡片"

`mastery_service.py:39-41`：

```python
quiz_id_result = await db.execute(select(QuizItem.id).where(QuizItem.card_id == card_id))
quiz_ids = [row[0] for row in quiz_id_result.all()]
if not quiz_ids:
    return 0.0                      # ← 卡片下无题目，无法计算
```

`review_service.get_due_quizzes()` 也是从 `QuizItem` 查
（`review_service.py:111-129`）。

**后果**：
- 没有生成题目的卡片（题目生成失败的、用户手动新建的、从图谱提升的）
  **永远无法被复习，掌握度恒为 0**
- 而题目生成会静默失败（见 D-7 第 4 点）→ **卡片有、题目没有 = 死卡片**
- 用户无法对一张卡片直接做"自评复习"（"我记得/我忘了"）

#### 🟠 L-6 每日限额 10 题会把复习需求积压成永久债务

`backend/app/config.py:203`：`daily_review_limit: int = 10`

`review_service.py:79-87`：

```python
today_done = today_done_result.scalar() or 0
remaining = daily_max - today_done
if remaining <= 0:
    return []
actual_limit = min(remaining, limit)
```

**后果**：
- 用户理解了一本 30 章教材 → 200 道题全部 `next_review_at = NULL`（立即可复习）
- 每天 10 题 → **需要 20 天才能"过一遍"**
- 20 天里，先复习的题已经到期第二轮，后面的题还在排队
- 到期队列**单调增长，永不收敛** —— 这是 SRS 的经典设计失误
  （Anki 用 `new/day` 与 `review/day` **分开**限额，且新卡引入速率可调）

#### 🟡 L-7 复习只考"LLM 出的题"，不考"原文"

`review_service.py` 的整个流程基于 `QuizItem`，而 `QuizItem` 由 LLM
从 `KnowledgeCard` 生成，`KnowledgeCard` 由 LLM 从章节文本抽取。

**信息衰减链**：原文 → 章节摘要（LLM 压缩）→ 卡片（LLM 抽取）→ 题目（LLM 改写）

**四跳之后**，用户复习的是"LLM 对 LLM 摘要的改写"。
原文里的推导过程、限定条件、反例、图表全部丢失。
**用户测的是"记没记住 LLM 的措辞"，不是"懂不懂这门学问"。**

且复习时用户**看不到卡片内容**（只有题目 + 选项），
`QuizItem` 与 `KnowledgeCard` 的 `source_text` 无关联展示路径 ——
**答错了也无法一键跳回原文对应段落**。

#### 🟡 L-8 "薄弱点优先"的排序信号过弱

`review_service.py:91-108`：

```python
error_subq = (
    select(QuizItem.card_id,
           func.coalesce(func.sum(case((ReviewLog.is_correct.is_(False), 1), else_=0)), 0).label("error_count"))
    .join(ReviewLog, ReviewLog.quiz_id == QuizItem.id, isouter=True)
    .where(QuizItem.user_id == user_id, ...)
    .group_by(QuizItem.card_id)
    .subquery()
)
```

排序按 `error_count` **历史累计错误数**降序。

**缺陷**：
- **累计错误数**没有时间衰减：半年前错了 10 次的卡片，即使最近 10 次全对，
  仍排在最前面（持续霸占队列）
- 没有考虑 `overdue`（逾期天数）、`difficulty`、卡片重要性
- 与 `next_review_at ASC NULLS FIRST` 组合（`review_service.py:123-126`）：
  `NULL` 的**新题永远排最前** → 用户每天都在做新题，
  **真正需要巩固的到期旧题被无限推迟**（这正是"复习雪崩"的直接成因）

---

### 2.5 工程与安全

#### 🔴 E-1 登录接口无任何速率限制 —— 密码可无限爆破

`backend/app/api/auth.py:126-155`：

```python
@router.post("/login", response_model=TokenResponse)
async def login(req: UserLoginRequest, db: AsyncSession = Depends(get_db)):
    user = await authenticate_user(db, req.email, req.password)
```

没有限流、没有账号锁定、没有失败计数、没有验证码、没有指数退避。

`backend/app/services/auth_service.py:20` `password: str = Field(min_length=6, max_length=100)`
—— **密码最短 6 位，无复杂度要求**。

**后果**：6 位纯数字密码 = 100 万组合，
**离线/在线爆破在无防护下是分钟级的事**。而 JWT 有效期
`config.py:99` `jwt_expire_minutes: int = 1440`（24 小时），
且**无 token 撤销机制**（`auth_service.py:70-89` 的 payload 只有 `sub` + `exp`，
无 `jti`、无黑名单、无 refresh token 轮换）—— **一旦泄露，24 小时内无法吊销**。

#### 🔴 E-2 用户枚举 + 无恒定时间比较

`backend/app/services/auth_service.py:136-144`：

```python
result = await db.execute(select(User).where(User.email == email))
if result.scalars().first():
    raise ValueError("该邮箱已被注册")        # ← 明确告知邮箱存在

result = await db.execute(select(User).where(User.username == req.username))
if result.scalars().first():
    raise ValueError("该用户名已被使用")      # ← 明确告知用户名存在
```

注册接口直接枚举已注册用户。

`auth_service.py:177-182`：

```python
user = result.scalars().first()
if not user:
    return None                                  # ← 立即返回，不做 bcrypt
if not verify_password(password, user.hashed_password):   # ← bcrypt 耗时 ~100ms
    return None
```

**邮箱不存在 → 立即返回；邮箱存在 → 100ms bcrypt。**
通过响应时间差异**可以精确枚举有效邮箱**。

同类问题：`README.md` 与 `参赛/参赛贴文.md` 里公开了演示账号
`learner@demo.com`。

#### 🟠 E-3 无 LLM 调用配额 —— 认证用户可无限烧钱

`config.py:129` 有磁盘配额 `max_storage_per_user_mb: int = 5000`，
`upload.py:303` 有对应的校验：

```python
select(func.coalesce(func.sum(Note.file_size), 0)).where(Note.user_id == current_user.id)
```

**但没有任何 token/成本配额。** 且这些端点全部无速率限制：
- `POST /api/understanding/{note_id}/start`（触发全文档 LLM 理解）
- `POST /api/understanding/{note_id}/generate-questions`
- `POST /api/assessment/*`（LLM 打分）
- `POST /api/graph/*`（LLM 关系推断）
- `POST /api/understanding/ask/stream`（流式问答）

**后果**：一个注册用户（或被盗的账号）可以**脚本化循环调用理解接口**，
每一次都是几十次 LLM 调用 × 真实的钱。
而全站限流只有 10 RPM（A-11）—— **攻击者用 10 RPM 就能持续烧钱，
并且顺带把正常用户饿死**。

#### 🟠 E-4 上传限额 500MB、无文件数限制、无内容嗅探

`config.py:127`：`max_upload_size_mb: int = 500`
`config.py:131`：`allowed_extensions: str = ".pdf,.png,...,.md"`

`upload.py` 声称做了"流式读取 + SHA-256"（注释），需要确认：
- **是否按内容（magic bytes）验证类型，还是只看扩展名**
- **是否有 PDF 页数上限**（一个 500MB 的 PDF 可能是 10 万页，
  送进 MinerU 与 LLM 会产生**灾难性成本**）
- **是否有解压炸弹防护**（`.docx`/`.pptx`/`.xlsx` 是 zip）

#### 🟠 E-5 CORS 与 debug 默认值的组合风险

`backend/app/main.py:87-93`：

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),   # 默认 localhost:5173,3000
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

`config.py:230`：`debug: bool = True`（**默认开启**）
`main.py:81`：`debug=settings.debug` → FastAPI debug 模式
`database.py:65`：`"echo": settings.debug` → **SQL 全量打印到日志**

**后果**：
- 忘记改 `.env` 就部署 → **debug 模式 + SQL 日志（含用户数据内容）**
- `config.py:322-334` `get_llm_config()`：`debug=True` 走 **GLM**，
  `debug=False` 走 **DeepSeek** —— **一个布尔值同时控制"调试模式""SQL 日志""LLM 供应商"**。
  生产环境为了用 DeepSeek 必须关 debug，但关了 debug 又无法调试。
  这是**把三个正交的关注点耦合在一个开关上**。

#### 🟡 E-6 无 CI、无覆盖率、测试目录混入危险脚本

- **无 `.github/` 目录** → 无 CI
- 无 `pyproject.toml`、无 `pytest.ini`、无覆盖率配置
- `backend/tests/` 有 24 个测试文件，其中包含
  `test_final_verify.py`、`test_real_pipeline.py`、`test_understanding_only.py`、
  `test_fix*.py` 等一次性脚本
- `backend/` 根目录有 **15 个一次性脚本**：
  `test.py`, `test_api.py`, `test_cleaning_failed.py`, `test_cleaning_pipeline.py`,
  `test_clean_failed_api.py`, `test_clean_failed_db.py`, `test_clean_failed_quick.py`,
  `test_clean_quick.py`, `test_convert_direct.py`, `test_e2e.py`, `test_pdf_pipeline.py`,
  `verify_clean.py`, `e2e_cleanup.py`, `reset_cleaning.py`, `restore_note.py`

> **2026-09-14 更正（计划 0.2 的搬迁已执行）**：上面这 15 个文件**已不在
> `backend/` 根目录**，现在都在 `backend/scripts/dev/`；根目录已无任何 `.py`。
> 引用同步情况与遗留项见 `backend/scripts/dev/README.md`。
> 本段其余内容（E-6 的问题描述）保留作历史对照。

`architecture.md:208` 自述："tests/ 混入调试脚本（**pytest 收集即烧真实 API/改生产库**）"。

**后果**：任何人跑 `pytest` 都可能**花真钱、改生产数据**。这比没有测试更危险。

#### 🟡 E-7 无 metrics、无 tracing、无成本可观测性

`main.py:149-160` 只有 `/health`：

```python
@app.get("/health")
async def health_check():
    return {"status": "ok", "app": settings.app_name}
```

**缺失**：
- ❌ 无 `/metrics`（Prometheus）
- ❌ 无 `/ready`（依赖就绪检查：DB 可写、broker 可达、LLM 可达）
- ❌ 无 OpenTelemetry / 分布式追踪（一次上传跨越 3 个进程 + 4 个外部服务）
- ❌ 无任务队列深度指标（用户无法知道"还有多少任务在排队"）
- ❌ 无 token/成本指标
- ❌ 无检索质量指标

> **2026-09-14 更正（附录 BK.2）**：上面第 2 条（无 `/ready`）与第 4 条（无任务队列深度指标）
> **已经过期** —— 阶段 0.10 已收口：`/ready` 已存在（200/503 同一响应体形状），
> 队列深度按 broker 里的 `*.celery.msg` 文件数如实报出（**只报告、不决定状态码**）。
> ⚠️ 但本轮实现的判据**只有数据库**：这一行括号里写的"broker 可达、LLM 可达"**没有做**
> （broker 只报深度；把 LLM 抖动翻译成"未就绪"是另一类错误）。
> `/metrics`、OpenTelemetry、token/成本指标、检索质量指标**仍然没有** —— 本条的其余部分照旧成立。

`architecture.md:183-187` 提到的可观测性是：**request_id + 结构化日志 + errors.log**。
这只够事后查一个请求，**不够运维**。

#### 🟡 E-8 前端：18 个页面全部静态导入，无懒加载

`frontend/src/App.tsx:11-30`：

```typescript
import Login from './pages/Login'
import Register from './pages/Register'
import Dashboard from './pages/Dashboard'
// ... 共 18 个
import KnowledgeGraph from './pages/KnowledgeGraph'
```

全部是**静态 import**，没有 `React.lazy` / `Suspense` / 路由级分割。

**后果**：首屏需要下载并解析 `react-force-graph-2d`（+ d3 依赖树）、
`katex`、`marked`、`highlight.js` 以及全部 18 个页面 ——
用户只想看 Dashboard，却要等整个图谱引擎加载完。

`package.json` 依赖清单也印证了架构缺失：

```json
"dependencies": {
  "dompurify", "highlight.js", "katex", "marked", "marked-highlight",
  "react", "react-dom", "react-force-graph-2d", "react-router-dom"
}
```

**没有**：状态管理（zustand/redux/jotai）、数据获取（react-query/swr）、
表单（react-hook-form）、校验（zod）、
**UI 组件库**、虚拟列表、i18n、测试（vitest/playwright）。

**全部状态靠 `useState` + `useEffect` + 手写 fetch**（architecture.md:86 自述：
"无状态管理库，页面内 useState/useEffect 自管"）。

#### 🟡 E-9 路由兜底把 404 变成登录页

`frontend/src/App.tsx:45`（未认证分支）与 `App.tsx:82`（已认证分支）：

```tsx
<Route path="*" element={<Login />} />                     // 未认证
<Route path="*" element={<Navigate to="/" replace />} />   // 已认证
```

**后果**：任何拼错的 URL 都会静默重定向。
用户点了一个失效的分享链接 → 看到登录页 → **以为自己的会话没了**。

#### 🟠 E-10 全站无 i18n，中文硬编码在组件与后端提示中

前端所有文案是硬编码中文；后端错误提示也是中文
（如 `auth.py:68` `detail="未提供认证令牌"`）。
且 `architecture.md:205` 自述"**前端匹配中文错误文案**"：

> 错误契约混乱：... 前端匹配中文错误文案

**后果**：后端改一句提示文案 → **前端逻辑静默失效**。
这是把错误码和展示层耦合在一起的反模式。

#### ⚪ E-11 无数据库备份与恢复流程

`backend/data/` 下有 `db/`、`chroma/`、`storage/`、`models/`、`tmp/`、`celery/`。
`docker-compose.yml` 只有一个 `backend-data` 卷，**没有备份服务、没有快照策略、
没有导出/导入功能**。结合 D-2（每次启动可能重建表），
**用户数据随时可能丢失且无法恢复**。

---

### 2.6 迁移与数据完整性（深度审计新增）

> 本节来自对 `alembic/`、`database.py` 与线上 SQLite 文件的直接核对。

#### 🔴 M-1 Alembic **从未生效过**，迁移链从设计上就跑不起来

**证据链（三重独立确认）**：

1. **迁移链无基线**。`backend/alembic/versions/001_add_note_role_to_notes.py:23`：
   ```python
   def upgrade() -> None:
       op.add_column('notes', sa.Column('note_role', sa.VARCHAR(), server_default='material', nullable=False))
   ```
   这是链的**第一个**迁移（`down_revision = None`），却直接 `ADD COLUMN`。
   **整条链（001→010）没有任何一个迁移创建 `users` / `notes` / `folders` /
   `knowledge_cards` / `quiz_items` / `review_logs` / `card_relations` 等基础表。**
   在一个空库上执行 `alembic upgrade head` 会在第一步就失败。

2. **配置与 env.py 不兼容**。`backend/alembic.ini:6`：
   ```ini
   sqlalchemy.url = postgresql+asyncpg://engram:engram123@localhost:5432/engramnote
   ```
   而 `backend/alembic/env.py:35` 用同步 API：
   ```python
   connectable = engine_from_config(
       config.get_section(config.config_ini_section, {}),
       prefix="sqlalchemy.",
       poolclass=pool.NullPool,
   )
   ```
   `engine_from_config` 是**同步**的，无法使用 `asyncpg` 驱动 —— 执行即报错。

3. **线上库根本没有 `alembic_version` 表**（实测 `backend/data/db/engramnote.db`：
   15 张表，无 `alembic_version`）。
   **schema 100% 由 `database.py:_migrate_sqlite()` 的手写 ALTER 维护。**

**后果**：
- `docs/architecture.md:193` 称"requirements.txt 中 alembic 被注释"，
  实际 `requirements.txt:18` 是 `alembic~=1.13.0`（**已启用**）。
  **依赖装了、文档说要用、迁移文件有 10 个，但没有一条能跑。**
- 任何"部署到新环境 / 接上 Alembic / 做 CI 数据库测试"的尝试**都会立刻失败**。
- 这意味着 **`docs/architecture.md:195` 规划的"收敛到 Alembic 单一通道"
  不是重构，而是从零新建**（见 Stage 1 修订）。

#### 🔴 M-2 `006` 迁移无条件丢弃用户数据

`backend/alembic/versions/006_project_tags_refactor.py:51-58`：

```python
op.drop_index('ix_notes_project_id', table_name='notes')
op.drop_constraint('fk_notes_project_id_projects', 'notes', type_='foreignkey')
op.drop_column('notes', 'project_id')          # ← 用户的笔记归属，直接丢弃
```

代码注释（`:47-50`）**自陈放弃了数据迁移**：

> 旧列可空，直接 INSERT ... SELECT，UUID 主键由应用默认生成，这里用随机十六进制无法
> 可靠保证 36 位格式，因此**保留数据迁移交给应用启动 SQLite 迁移或人工脚本**；
> 本迁移只负责 schema 对齐，避免旧列继续误导查询。

而"应用启动 SQLite 迁移"（`_migrate_sqlite`）**只做加列与建表，不做列到关联表的数据搬运**。
即：**"笔记属于哪个项目"这个信息在任何一条路径上都不会被迁移。**
（`downgrade()` 也自陈"标签关联无法无损还原为单值 project_id"。）

#### 🔴 M-3 `card_relations` 唯一约束可能压根不存在于生产库

去重与建索引的键**不一致**：

```python
# database.py:580-587  GROUP BY 含 status
DELETE FROM card_relations WHERE id NOT IN (
    SELECT MIN(id) FROM card_relations
    GROUP BY user_id, card_id_1, card_id_2, relation_type, status
)

# database.py:590-593  唯一索引只有 4 列，漏了 status
CREATE UNIQUE INDEX IF NOT EXISTS uq_card_relations_pair_type
ON card_relations (user_id, card_id_1, card_id_2, relation_type)
```

**当同一对卡片存在不同 `status` 的两行（如 `rejected` 与 `confirmed`）时：
去重 GROUP BY 认为它们是不同组 → 都保留 → 建唯一索引时冲突 → 抛异常。**

而异常被 `database.py:595-596` 吞掉：

```python
except Exception as e:
    logger.warning(f"创建 card_relations 唯一索引失败（忽略）: {e}")
```

且 ORM（`models/card_relation.py`）**没有声明这个唯一约束**，
`create_all()` 也不会创建它。

**后果**：这条唯一约束在生产**可能压根不存在**，而所有依赖它的
"重复关系防护"（`docs/decisions.md#F-01`、`F-17`）实际上没有生效。
且失败只留一行 WARNING。

#### ~~🔴 M-4 物理删除笔记会因外键违约而失败~~（**已实测证伪，2026-09-11**）

> ⚠️ **本条结论不成立。** 保留原文以便对照，但请以本框内结论为准。
>
> 原文断言：`purge_note` "只处理了待删卡片**作为父**的引用，没处理作为子"，
> 因此其他笔记的拓展卡片指向本笔记卡片时，DELETE 会抛
> `FOREIGN KEY constraint failed`，用户永远删不掉该笔记。
>
> **实测推翻了它。** `note_service.py:607-611` 的
> `UPDATE knowledge_cards SET parent_card_id = NULL WHERE parent_card_id IN (待删卡片)`
> —— **没有 `note_id` 限定**，所以它匹配的正是"**父**是被删卡片"的全部行，
> 跨笔记的拓展卡片恰好命中。同理 `quiz_items` 的选取范围
> （`:585-591`）是 `QuizItem.card_id.in_(card_ids) OR (note_id == :nid AND card_id IS NULL)`，
> 第一个条件同样不带 note_id 限定，跨笔记题目会被一并删除。
>
> 证据：`tests/test_purge_note_integrity.py`（5 用例）。该文件**自己断言
> 测试前提成立**（跨笔记引用确实建立），并用**真实生效的外键约束**验证
> （对照实验：绕过 purge 直接 `DELETE` 会正确抛 `FOREIGN KEY constraint failed`，
> 证明约束不是摆设）。
>
> **保留的残余风险**：这个正确行为依赖一个不显眼的事实 ——
> `WHERE parent_card_id IN (...)` 的作用域是**全表**。若将来有人给它加上
> `KnowledgeCard.note_id == note_id` 限定（看起来很合理），就会**真的引入 M-4**。
> 上述测试就是为锁住这一点而写的。
>
> **另一处未做的加固**：`parent_card_id` 与 `quiz_items.card_id` 仍然
> **没有 `ondelete`**。目前靠应用层保证一致性；若将来有代码绕过
> `purge_note` 直接删卡片，就会撞上约束。补 `ondelete="SET NULL"` 是更稳的
> 做法，但需要重建表（`quiz_items.card_id` 还是 NOT NULL，只能靠应用层删），
> 故本轮未做。

#### 🟠 M-5 重新生成题目**先删光学习记录**，再生成；批次异常静默跳过

`backend/app/tasks/understand_tasks.py:241-259`：

```python
# 2. 清理该笔记旧题目（含复习记录），恢复"重新生成"语义，
#    防止重复触发 generate-questions 累积重复题目，见 docs/decisions.md#F-29
async with session_factory() as session:
    old_quiz_ids = (await session.execute(
        select(QuizItem.id).where(QuizItem.note_id == note_id))).scalars().all()
    if old_quiz_ids:
        await session.execute(delete(ReviewLog).where(ReviewLog.quiz_id.in_(old_quiz_ids)))
        await session.execute(delete(QuizItem).where(QuizItem.note_id == note_id))
        await session.commit()          # ← 先提交删除
```

**然后**（`:268-299`）才开始分批调用 LLM 生成新题，且批次异常被 `continue` 静默跳过。

**后果**：**删除是不可逆的，生成是不保证的。**
- 一次 LLM 失败 / API 欠费 / 网络中断 = **用户所有的复习历史（`ReviewLog`）
  与 SM-2 调度参数（`interval`/`EF`/`repetition`/`next_review_at`）永久丢失，
  且新题也没生成出来。**
- 触发门槛极低：用户在 UI 上点一次"重新生成题目"即可。
- 这不只是"可能发生"——它是**默认路径**（成功也要经过删除）。

同类问题：`api/understanding.py:696` 的 `generate_questions_task.delay(...)`
**没有 try/except**（队列不可用时直接 500），且
`POST /{note_id}/generate-questions` **没有 archived 状态确认门禁**
（而 `POST /{note_id}/start` 有）。

#### 🟠 M-6 转换任务先置"已转换"状态，后传入 Markdown —— DB 可指向不存在的文件

`backend/app/tasks/convert_tasks.py:142-146` 先把状态置为 `converted`，
`:238-248` 才把 Markdown 写入存储。

**后果**：中间崩溃 / 写入失败 → **DB 记录 `status=converted` 且
`original_md_path` 指向一个不存在的文件**。前端认为转换完成，
用户点开笔记看到空白，且状态机不会自愈（没有一致性校验任务）。

#### 🟠 M-7 存储配额口径错误且检查无锁

- `api/upload.py:302-310` 的配额只统计 `notes.file_size` 之和
- **回收站里的笔记仍占用配额**（用户删了文件却发现空间没释放）
- **版本快照不计数**（`history/versions/` 可无限增长）
- **Chroma 向量不计数**
- 检查与写入之间**无锁** → 并发上传可绕过配额（TOCTOU）

#### 🟠 M-8 路径与命名安全：直传端点缺净化；`base` 唯一化是无锁 TOCTOU；命名空间冲突

1. **`/upload` 直传端点缺文件名净化**（`api/upload.py`），
   而 `/upload/prepare` 有 —— 同一功能两条路径，安全等级不一致
2. **`_resolve_unique_base`（`api/upload.py:224-249`）是无锁 TOCTOU**：
   并发上传同名文件 → 两次都检测到"可用" → **两篇笔记共用同一份 markdown，
   后上传覆盖先上传**
3. **Vault 命名空间冲突**（`services/vault_path.py` 与 `vault_meta.py`）：
   - `output/meta/{base}.json` vs 用户级项目清单 `output/meta/projects.json`
     → 上传一个名为 `projects.pdf` 的文件会**覆盖项目标签镜像**
   - `output/markdown/{base}.clean.md` vs 某笔记 `a` 的 `clean_md_path`
     → 上传名为 `a.clean.md` 的文件会**覆盖另一篇笔记的清洗副本**
   - 以上冲突 `_resolve_unique_base` **检测不到**（它只比对 `source/` 下的前缀）

#### 🟡 M-9 版本层四连缺陷

1. 缺 `(note_id, source, version_number)` 复合索引（只有 `(note_id, version_number)` 唯一索引）
2. `delete_file` 逐条调用（清理 N 个版本 = N 次 IO）
3. `save_note_content` 读取失败时**创建一个空内容的版本快照**
   （用户会看到一个"合法的空版本"并可能恢复它）
4. `get_version_content` 在文件缺失时**直接删除 DB 记录**
   （拒绝服务式数据销毁：一次磁盘故障会静默清空版本历史）

#### 🟡 M-10 文件删除失败被降级为 warning，文件被**永久孤立**（结论已修正）

> ⚠️ **原文的两个具体机制都已被实测证伪**，但**同一个后果确实存在，
> 载体是第三个机制**。原文保留在下方对照。

**原文**："双写顺序全是'先动文件，后 commit DB'，且失败降级为 warning"，
枚举 5 种磁盘/DB 分歧场景（trash / restore / purge）。

**实测结论（2026-09-11）**：

1. **`trash_note` 搬家失败不产生孤儿** —— 代码在失败时**把路径字段保留为旧值**
   （`note_service.py:293-299`，注释自陈"失败保留原值避免丢信息"）。
   因此 `purge_note` 按 `note.original_file_path` 删文件时仍指向**原处**，
   文件照样被删掉。
2. **meta 旁载不会漏删** —— 一度怀疑 `purge_note` 按 trash 前缀反推
   `{prefix}/output/meta/{base}.json` 会漏掉 inbox 下的 meta。
   实测不会：它用 `parts[:-2]` 从**当前实际路径**取前缀，trash 路径取出的
   是 `{user_id}/trash/{note_id}`，正确指向 trash 下的 meta，而 trash 搬家
   已把 meta 一并搬走。

**真正的缺陷（已修复）**：`purge_note` 的 7 处 `delete_file` 全部是

```python
try:
    delete_file(bucket, path)
except Exception as e:
    logger.warning(...)        # ← 失败只记日志
...
await db.delete(note)          # ← 笔记记录照删
await db.commit()
```

于是任意**瞬时**故障（Windows 文件被占用、杀软扫描、权限抖动、网络盘抖动）
都会让文件留在磁盘上，而 DB 记录被删除 —— 之后**再没有任何机制知道它存在**，
清理无从谈起。这不是"分歧"，是**不可发现、不可恢复的泄漏**，
恰好命中 M-10 描述的后果："用户看到已彻底删除，但文件仍在磁盘上"。

**修复**：`storage_service.delete_file` 增加有界重试
（3 次、间隔 50ms）。选这一层而不是逐个调用点，是因为所有调用方
（purge / trash / restore / 版本清理）都受益，且删除本身是**幂等**操作，
重试代价极低。

**关键约束（两条测试成对锁住）**：
- 瞬时失败必须重试成功 → 文件不留残留
- 持续失败时**必须继续删 DB 记录** —— 重试是有界的，耗尽后不能让用户
  卡在"删不掉笔记"。留下一个孤儿文件比让用户永远删不掉更轻。

证据：`tests/test_purge_file_consistency.py`（8 用例，含"文件不存在时不无谓重试"）。


#### 🟡 M-11 未分页端点、N+1 与全表扫描（后端侧）

- `api/notes/trash.py:52` 回收站列表**逐条**调 `_build_note_response`
  （每项 5 次统计查询）—— **批量版本已存在但没被使用**
- `api/projects.py:70-85`、`api/graph.py:45-59`（全部节点+边）、
  `api/notes/versions.py`、`api/cleaning.py`（整篇 diff）**均无分页**
- `api/knowledge.py:186-255` 盲点列表**先查出全部盲点卡片再在 Python 端切片**
- `api/understanding.py:498-528` 的卡片去重是 **O(n²)**：
  对笔记内每张卡各调一次 `detect_card_duplicates`，后者每次拉 500 张卡做 n-gram 内层循环
- `api/graph.py:78-99` 的 `limit` **无上界**，且 `services/graph_service.py:898-899`
  的 `ilike(f"%{keyword}%")` **未转义 LIKE 通配符**
  （项目其他搜索路径都转义了）→ `?q=%&limit=100000000` 可触发全表 ILIKE

#### 🟡 M-12 Worker 从不调用 `init_db()`

`init_db()` 只在 `backend/app/main.py:65` 的 FastAPI lifespan 中调用。
Celery worker 直接 `import` 模型并使用 `tasks/common.py` 的会话工厂。

**后果**：**API 未先启动时，worker 里的任务会因表不存在而静默失败**
（异常被 `except Exception` 吞掉并记日志）。
`docker-compose.yml` 用 `depends_on: service_healthy` 掩盖了这个问题，
但本地手动启动 worker（README:224-226 的顺序）或 worker 先于 API 重启时会暴露。

#### ⚪ M-13 垃圾残留（实测）

- `backend/data/tmp/worker/` 下 **8 个 `engram_convert_*` 残留目录**
  （转换任务异常退出未清理）
- `note_material_links` 表已有 **1 行两端皆 NULL 的死行**
- `backend/data_backup_e2e/` 是完整的数据目录副本（含 models / chroma / storage）
- `backend/` 根目录 15 个一次性脚本 —— **2026-09-14 已移入 `backend/scripts/dev/`**（根目录现无任何 `.py`，见计划 0.2 与**附录 BK.1**）

---

### 2.7 AI 管道（深度审计补充）

#### 🔴 A-16 RAG 可以**凭空编造引用来源**

`backend/app/services/rag_service.py:614-618` 与 `:662-694`：

`answer_question()` 在**有检索结果**时调用 `llm_service.rag_answer()`，
然后**无条件返回 `sources` 列表**：

```python
answer = await llm_service.rag_answer(question, context)
return {"answer": answer, "sources": sources, "provider": ..., "retrieval_status": ...}
```

而 `rag_answer` 的 system prompt **明确允许模型脱离资料**：

```
"2. 自主知识补充：如果参考资料不足或没有相关信息，可以结合你自己的知识来回答，"
"但请说明这部分是基于你的知识补充的\n"
"4. 回答应详细有用：不要简单地回复没有相关信息，而是尽力提供有价值的回答\n"
```

**关键点**：检索为空时走的是**另一条分支**（`rag_service.py:590-609`，返回 `sources: []`），
所以"检索空 → 无引用"这条保护是有的。
**但"检索有结果、问题却答非所问"这个区间——也就是最常见的区间——
模型会用自身知识作答，而 `sources` 照常返回并被前端渲染成"引用来源"。**

**后果**：用户看到"根据你的笔记"的回答 + 一个看起来可信的来源列表，
**而实际内容来自模型常识甚至幻觉**。这是学习工具最严重的失败模式：
用户会把错误内容当作自己教材的结论记住。

#### 🟠 A-17 三路混合检索实际是"两套不同粒度的语料"，RRF 融合退化

| 通道 | 语料 | 粒度 | 位置 |
|---|---|---|---|
| 向量 | Chroma，**每篇笔记一个 collection** 的 chunk | **chunk 级**（`n_results=min(3,count)`） | `embedding_tasks.py:206-265` |
| BM25 | `knowledge_cards` 表 | **整卡级** | `rag_service.py:226-302` |
| n-gram | `knowledge_cards` 表 | **整卡级** | `rag_service.py:389-419` |

**后果**：RRF 的去重键 `(note_id, content[:200])`
（`rag_service.py:342-343`）**永远无法合并同一事实**：
向量路给的是 chunk 文本前缀，BM25/n-gram 给的是整卡 `content` 前缀 ——
**同一段内容被当成两个不同文档，各计一次 `1/(60+rank)`。**
融合结果因此**系统性地偏袒"同时在卡片和原文里出现"的内容**（通常是最不具体的概述句），
而**精确的原文细节永远排在后面**。

#### 🟠 A-18 用户编辑笔记后向量**永久陈旧**

`backend/app/services/note_service.py:1021-1089` 保存笔记内容时**只覆盖 md 文件**。
全仓 grep 显示 `delete_note_chunks` **只在 `purge_note`（`note_service.py:790-796`）被调用**；
`api/notes/list_detail.py:249` 保存内容后**不触发 clean / embedding**。

**后果**：
1. **RAG 持续召回用户已经删掉的旧文本** —— 用户改了笔记，AI 却还在引用修改前的内容，
   并且**用户完全无法察觉**（引用只显示笔记标题，不显示版本）
2. `metadata["duplicates_detail"]` 的 `start_line` / `end_line` **仍指向旧版本行号** ——
   用户点"恢复被去重的块"或"删除重复块"会**在错误的位置操作当前文本**，
   导致**内容错乱**（这是数据破坏，不只是显示错误）

#### 🟠 A-19 `debug=True` 默认值把 bcrypt 哈希与全部学习内容写入明文日志

`backend/app/config.py:230` `debug: bool = True`
→ `backend/app/database.py:65` `"echo": settings.debug`
→ SQLAlchemy 把**全部 SQL 与绑定参数**写入 `data/logs/engramnote.log`。

日志里因此包含：
```sql
INSERT INTO users (email, hashed_password, ...) VALUES (?, '$2b$12$...', ...)
INSERT INTO knowledge_cards (title, content, source_text, ...) VALUES (?, '...', ...)
INSERT INTO quiz_items (question, answer, ...) VALUES (?, '...', '...')
```

**后果**：**磁盘上的日志文件是用户口令哈希 + 全部学习内容的明文副本。**
任何能读 `data/` 的人、备份系统、日志采集器都可获得。
（`.env` 本身未被 git 跟踪，也未发现硬编码密钥 —— 这条是合规与隐私问题，不是凭据泄漏。）

#### 🟠 A-20 图谱检索的 LIKE 通配符未转义 + `limit` 无上界

`backend/app/services/graph_service.py:898-899`：
```python
or_(
    KnowledgeCard.title.ilike(f"%{keyword}%"),
    KnowledgeCard.content.ilike(f"%{keyword}%"),
),
```
对比项目其他地方**都做了转义**（`api/understanding.py:329`、`:770`、
`services/note_service.py:112` 均为 `.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")` + `escape="\\"`）。
且 `api/graph.py:78-99` 的 `limit: int = Query(20, ...)` **没有 `le=` 上界**
（`api/knowledge.py:193` 有 `le=100`，`api/review.py:52` 有 `le=200`）。

**后果**：`GET /api/graph/search?q=%25&limit=100000000` → 对
`title` + `content` 两大文本列做**全表 ILIKE** + 每行关联计数子查询，
**一次请求即可造成分钟级 CPU 占用与巨大响应体**。

#### 🟡 A-21 卡片抽取质量门完全缺失 + 无幂等

`backend/app/services/understanding_service.py:226-280` `save_knowledge_cards()`：

```python
title=point.get("title", "未命名知识点"),   # ← 空标题静默兜底
content=point.get("content", ""),         # ← 空内容照样入库
...
await db.commit()
for card in cards:
    await db.refresh(card)                 # ← N 次额外往返
```

- **无内容校验**：`content` 可为空字符串
- **无数量上限**：一次 LLM 调用抽出多少就存多少
- **无跨批次去重**：按 `batch_size = 3` 分批（`:393`），**章节重叠处会抽出重复卡片**
- **无幂等**：重跑理解 = 追加新卡片，旧卡片不清理 → 用户看到重复卡片
- **无质量评估**：没有机制判断卡片是否"可答、原子、自足"
- **注**：`source_text` **没有**被截断（此前误记为截断），只有长度超过 5000 时截断

#### 🟡 A-22 300 页 PDF 的成本与耗时会**击穿**当前架构

| 阶段 | 规模 | 依据 |
|---|---|---|
| 章节理解 | **~50 次** LLM（30 章 ÷ batch 3） | `understanding_service.py:393-397` |
| 题生成 | **~25 次** LLM（÷ batch 30 卡） | `understand_tasks.py:266-268` |
| 重试放大 | ×(1~2) | `llm_json_max_tokens` 逐级放大后重试 |
| 清洗嵌入 | **600~900 次单条 encode**（800 chunk） | `cleaning_service.py:184-219` chunk_size=500；`embedding_tasks.py:118-136` **逐条** encode，未批处理 |
| 清洗去重 | **18 万~80 万对**纯 Python 余弦 | `cleaning_service.py:661-686`，O(n²) |

**在 `llm_max_rpm=10`（`config.py:160`，全站共享）下，仅 LLM 排队就 ≥ 8 分钟**；
叠加每次调用的真实生成时间（超时设为 600s）与 `--pool=solo` 串行执行，
**单篇 300 页教材端到端 30-60 分钟，且期间全站其他用户的任务全部排队。**
（此表为代码推算；嵌入与去重的实际耗时需实测确认。）

---

### 2.8 前端（深度审计补充）

#### 🔴 F-1 生产环境 **>1MB 的上传必然 413**，流式回答必然失效

`frontend/nginx.conf` 全文**没有** `client_max_body_size`。

```nginx
location /api/ {
    proxy_pass http://backend:8000/api/;
    proxy_set_header Host $host;
    proxy_read_timeout 300s;
    proxy_connect_timeout 60s;
}
```

nginx 默认 `client_max_body_size 1m`。而产品核心是上传 PDF / 视频 / 音频
（`Upload.tsx:17-19` 的 `ALLOWED_EXTENSIONS` 含 `.pdf .mp4 .wav`），几乎都 >1MB。

同时该 location **没有 `proxy_buffering off`**，nginx 默认开启缓冲，
会把 SSE 响应缓冲后成块下发 ——
`api/client.ts:314` 与 `api/notes.ts:316` 两个流式端点、
`QA.tsx:94-152` 与 `NoteAskPanel.tsx:126-164` 的逐 chunk 解析全部失效：
**"AI 正在思考"会一直显示到上游结束，然后整段答案一次性出现。**

**最阴险的地方**：开发环境走 Vite 代理（`vite.config.ts:8-13`），
**这两个 bug 都不会出现**。它们**只在 Docker/生产部署时暴露**，
而 README 的推荐路径正是 `docker compose up -d`（README:249）。

#### 🔴 F-2 全站无 ErrorBoundary + Markdown 在 render 期同步渲染 → 单条坏数据整站白屏

`frontend/src` 58 个文件中 grep `ErrorBoundary|componentDidCatch|getDerivedStateFromError`
**0 命中**。`main.tsx:20-25` 直接 `createRoot(...).render(<App/>)`。

而 `utils/markdown.ts:188-191` 的渲染是**渲染阶段同步执行的重逻辑**：

```typescript
export function renderMarkdown(text: string): string {
  if (!text) return ''
  const parsed = marked.parse(text) as string
  return renderMathInHtml(sanitizeHtml(parsed))
}
```

`NoteDetail.tsx:668` 与 `:671` 都在组件函数体内调用它（**未 `useMemo`**）。
此外 `sanitize.ts:25-35` 注册了**模块级** DOMPurify hook，
`markdown.ts:53-86` 用 `marked.use()` 做**全局单例**配置。

**后果**：一份含畸形 HTML/LaTeX 的笔记，或一条被截断的 SSE
（`QA.tsx:114` 的 `JSON.parse` 无保护），会让 React **卸载整棵树 → 整个应用白屏**。
刷新后回到同一份笔记**再次白屏，形成死循环**，用户无法自救。

#### 🔴 F-3 `QuestionSets` 无上限分页循环 → 正常使用即可打爆后端

`frontend/src/pages/QuestionSets.tsx:65-94`：

```typescript
const allItems: QuizItem[] = []
let page = 1
const pageSize = 100
let totalCount = 0
do {
  const data = await getQuestions(page, pageSize, noteId, keyword)
  allItems.push(...data.items)
  totalCount = data.total
  page++
} while (allItems.length < totalCount)       // ← 无页数上限、无空结果兜底
```

终止条件**完全依赖后端 `total` 与 `items` 长度自洽**。
只要 `total` 大于实际可返回条数（分页越界、软删过滤不一致、并发写入），
**`allItems.length` 永远追不上 `totalCount` → 前端无限循环请求后端**，
页面永久 loading，服务端被打满。且 effect 依赖含防抖后的 `searchKeyword`，
每次搜索重跑整条链，**旧链不取消**。

#### 🔴 F-4 `Dockerfile` 无 `.dockerignore`，宿主 `node_modules` 覆盖镜像内依赖

`frontend/Dockerfile:13-22`：

```dockerfile
COPY package.json ./
RUN npm install
COPY . .                    # ← frontend/node_modules 在本地实际存在
RUN npm run build
```

`frontend/` 下**没有 `.dockerignore`**（也没有 `.gitignore`）。
`COPY . .` 会把宿主 **Windows** 的 `node_modules`
（含 `@esbuild/win32-x64`、`@rollup/rollup-win32-x64-msvc`）
覆盖到刚装好的 Linux 依赖之上。

**后果**：Linux 镜像构建报 `You installed esbuild for another platform` 而失败；
即使侥幸通过也把几百 MB 打进镜像。
**同一份 Dockerfile 在开发者机器与 CI 上行为不同。**
另：仓库已有 125KB `package-lock.json`，却被 `npm install` 忽略（应用 `npm ci`）。

#### 🟠 F-5 `DailyMaterials` 上传轮询**完全没有清理**，卸载后仍跑 10 分钟

`frontend/src/pages/DailyMaterials.tsx:329-368`：

```typescript
async function pollUploadStatus(noteId: string) {
  const maxAttempts = 120
  ...
      setTimeout(check, 5000)     // ← 句柄不存任何 ref
```

该文件 grep `clearTimeout` **0 命中**，无 effect cleanup。
（对比 `Upload.tsx:87,90-96` 有 `pollTimerRef` 与卸载清理 ——
**同一模式在 `Upload.tsx` 修过（F-15），在 `DailyMaterials.tsx` 漏了**。）

**后果**：用户上传后切页 → 递归链继续跑 120×5s = **10 分钟**；
连续上传两个文件会产生**两条并行且互不感知**的链，
且旧链会刷新**错误的文件夹详情**（`expandedFolderId` 已变）。

#### 🟠 F-6 `LearningAssessment` 首屏 N+1 请求风暴（一次并发最多 100 个请求）

`frontend/src/pages/LearningAssessment.tsx:102-129`：

```typescript
const checked = await Promise.all(
  allPersonalNotes.map(async (n) => {
    try {
      const links = await getNoteLinks(n.id)         // ← 每条笔记一个请求
      return links.linked_materials.length > 0 ? n : null
    } catch { return null }                          // ← 失败静默丢弃
  })
)
```

**无并发上限、无 AbortController、无缓存**，且 effect 依赖 `[mode, compareMode]`
（每次切 tab 重跑全量）。

**后果**：100 条个人笔记 → 100 个并发请求；排在后面的撞上
`client.ts:151` 的 `REQUEST_TIMEOUT_MS = 30000` 被 abort →
`catch { return null }` **静默丢弃** →
**列表里少了一批本该出现的笔记，用户看不到任何错误提示**。

#### 🟠 F-7 零路由级代码分割；`highlight.js` 完整语言包进首屏

`App.tsx:11-30` **静态 import 19 个页面**，grep `React.lazy|Suspense` **0 命中**。
重量级依赖全在模块顶层进入口：
- `KnowledgeGraph.tsx:3` → `GraphCanvas.tsx:2` → `react-force-graph-2d`（连带 d3 生态）
- `utils/markdown.ts:9` → `highlight.js` **完整构建**，且 `:20` 用 `hljs.highlightAuto(code)`
  —— 该 API 要求注册全部约 190 种语言，**这正是无法裁剪的原因**
- `utils/markdown.ts:10` → `katex`
- `NoteDetail.tsx:14-15` → `highlight.js/styles/github-dark.css`、`katex/dist/katex.min.css`

`vite.config.ts` **完全没有 `build` 段**（无 `manualChunks`、无 `rollupOptions`）。

**后果**：只想看"今日学习"的用户，首屏必须下载并解析整包 highlight.js + katex + 字体 + 整个力导向图引擎。

#### 🟠 F-8 无全局 toast：**49 处 `alert()`、10 处 `confirm()`、约 20 处静默 catch**

`alert(` 共 49 处（`NoteDetail.tsx` 15、`LearningAssessment.tsx` 7、
`KnowledgeGraph.tsx` 6、`DailyMaterials.tsx` 6、`Trash.tsx` 5 等），
`confirm(` 10 处。典型：

```typescript
// Dashboard.tsx:466-476 —— 邮件提醒开关
} catch {
  // 容错：更新失败保持原值（请求层已抛出可读错误，此处静默）
}
```

**后果**：用户点"邮件复习提醒"开关 → 请求失败 → **复选框弹回原位、零提示** →
用户以为开关坏了而反复点击。
另外 `NoteDetail.tsx:203/223`、`KnowledgeGraph.tsx:172/206` 等 `catch {}`
只清空数据 → 用户看到"内容为空"而非"加载失败"，**与真实空状态无法区分**。

#### 🟠 F-9 `NoteDetail` 两份 `setInterval` 轮询 + 每次渲染全量重算 Markdown

`NoteDetail.tsx:188-209`（cleaning）与 `:212-229`（learning）两份**几乎逐行重复**的
`setInterval(async () => {...}, 5000)`。`setInterval` **不等待 async 回调**，
`:291` 的 `mutatingRef` 只挡"块操作进行中"，**挡不住上一轮请求未返回** → 慢网络下重叠发起；
失败分支完全静默且**永不退避、永不放弃**。

叠加 `:668`/`:671` 两个未 memo 的 `renderMarkdown`：
**编辑模式是实时分屏预览（`:895-916`），每敲一个字符触发两条全量 Markdown 管线**
（`marked.parse` + DOMPurify + `createTreeWalker` 全文遍历 + 逐节点 KaTeX）→ 长笔记打字卡顿。

#### 🟠 F-10 流式渲染 **O(n²)**：每个 token 全量重跑 Markdown + DOMPurify + KaTeX

`NoteAskPanel.tsx:156` 与 `:268-272`：

```tsx
setAnswer(prev => prev + (data.content || ''))      // 每个 token 一次 setState
...
<div dangerouslySetInnerHTML={{ __html: renderMarkdown(answer) }} />   // render 内全量重算
```

第 k 个 token 要重新解析长度约 k 的全文 → **O(n²)**。

`QA.tsx:216` 更糟，**React key 里含流式内容片段**：

```tsx
<div key={record.question + idx + record.answer.slice(0, 20)} ...>
```

**答案每增长约 20 字符，key 就变化一次** → React 把整条记录
（问题气泡 + AI 卡片 + 引用列表）**卸载重建**。

**后果**：长回答时页面卡死、输入框失去响应；
流式过程中**焦点、文本选区、滚动位置全部丢失**；无法选中/复制正在生成的文本。

另：SSE 解析器是同一段约 60 行代码**复制到 `QA.tsx` 与 `NoteAskPanel.tsx` 两份**，
且都只保留最后一行 `data:`（SSE 规范要求按 `\n` 拼接）、不识别 `\r\n`、
`JSON.parse` 无 try/catch（**坏 JSON 直接终结整个流**）。
两份已漂移：`QA.tsx` 处理 `sources` 事件，`NoteAskPanel.tsx` 不处理。

#### 🟠 F-11 图谱页重渲风暴：鼠标每次移动都 `setState`；`GraphSidebar` 40 个 props 无 memo

`KnowledgeGraph.tsx:928/930`：

```tsx
onNodeHover={(node) => setHoverNode(node)}      // 指针移动即触发
onLinkHover={(link) => setHoverLink(link)}
```

`nodeCanvasObject` 的 `useCallback` 依赖含 `hoverNode`（`:474`），
身份变化 → `ForceGraph2D` 收到新 prop → **整张画布重绘**。
同时 `:936-975` 把 **40 个 props** 一次传给 `GraphSidebar`（`:19-58`），
其中一半是函数与 `Dispatch<SetStateAction<...>>`，**全部未 memo、未稳定引用**。

**后果**：鼠标在图谱上移动时，868 行页面 + 665 行侧边栏**每帧重渲**，
叠加力导向布局本身的每帧物理计算 → 中低端设备明显掉帧。

#### 🟠 F-12 类型系统空洞：**60 个手写类型中只有 1 个联合类型**

`any` 用量为 **0**（这点做得很好），但真正的漏洞在别处：
- `api/goals.ts:17` 是**全项目唯一**的联合类型
- 其余枚举语义字段（`status`/`source_type`/`note_role`/`card_type`/
  `relation_type`/`question_type`/`difficulty`）**全是 `string`**（25+ 处）
- `utils/labels.ts` 用 `Record<string, string>` → **拼错/新增状态不会编译报错**
- `QuizState` 在 `Review.tsx:12`、`QuickReview.tsx:20`、`TodayLearn.tsx:21` **三处逐字重复**
- `NoteGroup` 在 `KnowledgeCards.tsx:15` 与 `QuestionSets.tsx:24` 重复
- **无 OpenAPI 生成类型**（grep `openapi|schema.d.ts` 0 命中）
  —— ⚠️ 这是**审计当时**的现状（2026-09-14 补注）：本轮已生成
  `frontend/src/api/generated/schema.ts`，且这条路的**阻塞项不在前端**
  （22 个端点缺 `response_model`），见附录 BH
- `eslint-disable` **27 处**，其中 `react-hooks/set-state-in-effect` **15 处**
  —— 说明"在 effect 里同步 setState"已成全项目习惯

#### 🟠 F-13 状态标签样式与状态值漂移：三个状态**完全没有样式**

四处用模板拼接类名：`NoteDetail.tsx:737`、`Dashboard.tsx:430`、
`NotesList.tsx:233`、`DailyMaterials.tsx:629`：

```tsx
className={`status-${note.status}`}       // ← 无任何 fallback
```

CSS 侧只有 7 个 `.status-*` 类，而 `labels.ts:12-16` 的状态集合含
`learning`、`learning_failed`、`archived` —— **这三个 CSS 里都不存在**
（且命名自相矛盾：CSS 用连字符 `status-cleaning-failed`，运行时值是下划线 `learning_failed`）。

`Projects.tsx:43-54` **正因为发现了这个问题而手写映射绕过**：

```typescript
learning_failed: 'status-failed',   // 全局无 .status-learning-failed，复用失败红
archived: 'status-converted',       // 全局无 .status-archived，归档视为完成态，复用成功绿
```

**后果**：同一状态下，`Projects` 页显示红色"学习失败"，
而笔记列表/详情/仪表盘/今日资料**四个页面显示完全无样式的裸文本**；
"已完成转换"与"已审阅"在四处渲染成同样的绿色，无法区分。

#### 🟠 F-14 `ReminderBanner`：数据展示被**通知权限**绑架

`ReminderBanner.tsx:81-88` 与 `:153-158`：

```typescript
useEffect(() => {
  if (permission === 'granted') { startPolling() }    // 只有授权才轮询
  return () => stopPolling()
}, [permission])
...
if (permission !== 'granted') {
  return ( /* 永远渲染"开启桌面通知"提示条 */ )
}
```

`reminders` 只由 `pollOnce()` 填充，而它只在 `startPolling()` 里调用。

**后果**：用户点一次"稍后"或曾拒绝通知权限 →
**待复习数、1 小时内到期数、薄弱点数永远不渲染**。
"看复习提醒"与"开桌面通知"本是两件事，却被打包在一起。
且 `dismissed` 只在组件内存（`:32`），**刷新后横幅回来反复骚扰**。

#### 🟡 F-15 可访问性缺失（可量化）

- **`:focus-visible` 全项目 0 命中**；6 处 CSS + **5 处内联 `outline: 'none'`**
  无任何替代（`KnowledgeCards.tsx:235`、`DailyMaterials.tsx:471`、
  `QuestionSets.tsx:148`、`NoteDetail.tsx:756/908`）
- **色彩对比度不达标**：`--color-text-tertiary: #9a9ab0` 白底 **2.75:1**
  （AA 正文需 4.5:1），却用于 `::placeholder`、`.status-uploading`、
  diff 行号、`.state-description`；`--color-accent: #c9a959` 作正文 **2.26:1**，
  用在**登录页"注册"链接**（`auth.css:122-123`）、全局 `a:hover`
- **大量可点击 `div`/`span` 无 `role`/`tabIndex`/`onKeyDown`**：
  全项目仅 30 处 `aria-`、20 处 `role=`、9 处 `tabIndex`，
  而 `cursor:'pointer'` 出现 **32 次**，多数落在非交互元素上。
  `Dashboard.tsx:143-150` 写全了 `role+tabIndex+onKeyDown`，
  **同一文件 130 行后**的待复习卡片（`:284-295`）**漏了 `onKeyDown`**
- **5 个手写 Modal** 无 `role="dialog"`/`aria-modal`/Escape/焦点陷阱/焦点归还

#### 🟡 F-16 响应式仅 2 个断点覆盖 8 个选择器；三处固定宽度完全无适配

`responsive.css` 共 51 行，全项目仅 4 个 `@media` 块。未被覆盖的：

```tsx
// Dashboard.tsx:141 与 :281 —— 手机上永远两列
gridTemplateColumns: '1fr 1fr'
// GraphSidebar.tsx:581 —— 固定 320px
width: 320, flexShrink: 0
// NoteAskPanel.tsx:26/232 —— 固定 480px，且 left 按 PANEL_WIDTH/2 计算
const PANEL_WIDTH = 480
```

**后果**：375px 屏上仪表盘两列各不到 170px（中文标题被逐字挤断）；
**知识图谱侧边栏吃掉 320px，力导向图变成一条约 55px 的竖缝，完全无法使用**；
AI 提问浮层被推出视口右侧。
（同页 `:234` 已经写了 `repeat(auto-fit, minmax(280px,1fr))` —— **属漏改**。）

#### 🟡 F-17 未接线功能与死代码（可枚举）

**111 个导出 API 函数中 16 个从未被引用（14.4%）**，其中值得注意的：
- **`updateNote`（`api/notes.ts:44`）从未被调用** →
  `NoteDetail.tsx:622-637` 的编辑只走 `updateNoteContent`（改正文），
  **用户在 UI 里无法重命名笔记**，而接口一直在那里
- **`getBlindSpots` / `getMasteryOverview` 未使用** →
  存在**两套并行的"薄弱/掌握度"概念**，其中一套从未接线
- `getMe` 未使用 → 见 §2.5 E-3 / F-12（认证状态从不校验）

**CSS 死代码**：18 个类零引用（整个遗留导航栏块 `layout.css:3-53`、
`.card-accent-top`、`.glass-effect` 等），
`refinements.css` 中 8 个**自陈"预留待 tsx 接入"**却从未接线的类
（约 60 行 CSS 写给了**不存在的 DOM 契约**），4 个死 `@keyframes`，13 个死 CSS 变量。

**未接线的交互**：
- `Review.tsx:29-30` 的 `inputRef`/`textareaRef` **从未挂到任何元素**
  （grep `ref=` 零命中）→ `:111-115` 的"下一题自动聚焦"**永远不执行**；
  而 `:121-131` 的 Enter 提交依赖焦点在输入框内 → **键盘流程断裂**
- `Review.tsx:222`、`QuickReview.tsx:209`、`TodayLearn.tsx:212`
  的 `submitting={submittingRef.current}` **恒为 `false`**（ref 变化不触发重渲染）
  → `QuizAnswerCard.tsx:179` 的禁用态**永不生效**，
  提交期间按钮可连点且**无任何加载反馈**

#### 🟡 F-18 CSS 补丁层用**内联样式字符串**做选择器

`refinements.css:316-469`（约 150 行）：

```css
[style*="rgba(0,0,0,0.5)"] > .card { ... }
[style*="rgba(0,0,0,0.5)"] .card .btn-primary { ... }
```

注释（`:308`）声明只为"管理关联资料弹窗"服务，
但**同一个内联串出现在 4 个组件**（`DeleteNoteDialog.tsx:19`、
`VersionHistory.tsx:170`、`NoteDetail.tsx:1011`、`Trash.tsx:216`）→
**破坏性操作的确认弹窗被套上"资料列表行"样式**，
`.btn-primary` 的渐变背景被这层改写。
且浏览器把 `rgba(0,0,0,0.5)` 重新序列化为 `rgba(0, 0, 0, 0.5)` 就会**整块静默失效**。

更深的问题：全项目 **765 处内联样式**（`Projects.tsx` 75、`Dashboard.tsx` 67、
`NoteDetail.tsx` 61、`GraphSidebar.tsx` 57），
**71 个色值绕过 token**（`rgba(15,52,96,…)` 硬编码 18 次，
而 `--color-primary-rgb` 存在的唯一目的就是拼 alpha），
**3 个变量被使用但从未定义**（`--color-primary-soft`、`--color-bg-hover`、`--color-bg-subtle`），
且 fallback 值与真 token 矛盾（`--color-primary-soft` 的 fallback 写 `#2563eb`，
而 `--color-primary` 实际是 `#0f3460`）。

#### 🟡 F-19 用中文字符串匹配判定业务状态

`Dashboard.tsx:59-66` 与 `Review.tsx:88-93`：

```typescript
if (!msg.includes('active goals')) { failedCount++ }     // Dashboard
if (msg.includes('每日上限')) { ... }                     // Review —— 匹配后端中文文案
```

而 `client.ts:216-219` **已经提供了稳定的 `error_code`**。

**后果**：后端改一句文案或做 i18n，"无活跃目标"就被当成加载失败，
"每日已达上限"就走成普通错误 → **用户被卡在答题页反复提交被拒**。

#### 🟡 F-20 无长列表虚拟化；`page_size` 硬编码绕过分页，导致**功能缺陷**

- `KnowledgeCards.tsx:78` `getKnowledgeCards(1, 999, ...)` —— 一次拉 999 张卡全量渲染
  （每张含徽章、进度条、操作菜单）
- **`LearningAssessment.tsx:59` `getNotes(1, 100)` 之后在客户端过滤状态**
  → **笔记超过 100 条时，第 101 条之后的笔记在选择列表里彻底消失，
  且没有任何"还有更多"的提示**（这是功能性缺陷，不只是性能问题）
- `VersionHistory.tsx:274-295`、`DiffView.tsx:83-89` 的 diff 行全量渲染

#### ⚪ F-21 nginx 缺缓存头与安全头；`<Link>` 全项目从未用于导航

- 无 `gzip_vary on` → 中间代理可能把 gzip 内容发给不支持压缩的客户端
- `location /` **完全没有缓存指令** → Vite 的
  `assets/index-<hash>.js` 本可 `immutable` 缓存一年却无指令；
  **`index.html` 也没有 `no-cache`** → SPA 发版后用户可能拿到旧 HTML
  引用已删除的 hash 资源 → **白屏**
- 无 `X-Content-Type-Options` / `X-Frame-Options` / `Referrer-Policy` / CSP
- `NoteDetail.tsx:829` 与 `:867` 用**裸 `<a href>`** 且无 `preventDefault`
  → 点击关联笔记会**整页硬刷新**（丢失 SPA 状态、重新下载全部 JS）
- 23 处 `console.*` 进入生产；`vite.config.ts` 后端地址
  **硬编码 `http://localhost:8001`**，且**不在任何 tsconfig 检查范围内**
  （`tsconfig.json:20` 是 `include: ["src"]`，无 `tsconfig.node.json`）
- **日期格式化三套并存**；无 i18n；无 `prefers-reduced-motion`
  （而有 5 个无限动画）；无 print 样式；无暗色模式

---

### 2.9 多租户与 API 契约

> **先说正面结论**：审计逐一核对了 `backend/app/api/` 下 **22 个文件、107 个路由端点**，
> **全部声明了 `Depends(get_current_user_dependency)`**，不存在未鉴权的业务端点
> （仅 `GET /health` 匿名）。note / card / quiz / project / folder / link / goal /
> assessment / version / relation 的**主查询均已带 `user_id`**。
> **本轮未发现可直接跨租户读写的 IDOR。**
> 另外 `storage_service._resolve_path`（`:70-85`）的 `resolve()` + parents 包含判断
> **路径遍历防护正确**；所有 `text()` 原生 SQL **均使用绑定参数**（未发现 SQL 注入）；
> `.env` **未被 git 跟踪**，**未发现硬编码密钥**。
> 这些是本项目做得对的地方，重构时应保留。

#### 🟠 S-5 但有 6 处"按 id 查询缺 `user_id` 断言"的纵深防御缺口

这些当前**不可直接利用**（依赖入口处已校验归属），
但**一旦某个前置校验在重构中被移动或删除，立即变成真实 IDOR**。
而这类重构在本项目历史上**已经发生过多次**（见 `docs/decisions.md#F-08`、`F-09`）。

| # | 位置 | 缺少 `user_id` 的查询 | 恶化后的后果 |
|---|---|---|---|
| 1 | `services/note_service.py:923-925` | `select(NoteMaterialLink).where(NoteMaterialLink.personal_note_id == ...)`，随后 `:934-936` **删除**不匹配行 | **删除他人的链接行** |
| 2 | `api/understanding.py:115-137` | `sql_delete(QuizItem).where(QuizItem.note_id == note_id)`、`sql_delete(KnowledgeCard).where(...)` | **删除他人的卡片与题目** |
| 3 | `api/understanding.py:460-474` | `sql_update(KnowledgeCard).where(parent_card_id == card_id)` —— **全库更新** | **改写他人的卡片** |
| 4 | `api/understanding.py:388-390, 429-431` | `select(Note.title).where(Note.id == card.note_id)` | 读取他人笔记标题 |
| 5 | `services/note_service.py:892-916` | `get_linked_materials` / `get_linked_personal_notes` 只按 `link.user_id`，未限定 `Note.user_id` | 数据脏化时读取他人笔记 |
| 6 | `api/notes/_common.py:26-30`、`api/quick_review.py:51-57`、`services/version_service.py:108` | 按 `note_id` / `material_id` 查询无 `user_id` | 低危，但应统一 |

**修复方向**：把租户断言**下沉为不可绕过的模式** ——
所有按资源 id 的查询统一为 `where(Model.id == x, Model.user_id == current_user.id)`
（含批量删改），或提供带 `user_id` 的复合查询辅助函数；
并在 CI 中 grep 禁止裸 `Model.id ==`（这个模式当前**不存在**，是漏洞复发的根因）。

#### 🟠 S-6 OpenAPI 完全没有 `securitySchemes`

`api/auth.py:43-92` 的 `get_current_user_dependency` **手工读取原始请求头**：

```python
auth_header = request.headers.get("Authorization")
```

FastAPI 因此**无法感知鉴权要求** —— 生成的 `openapi.json` 里
**既没有 `components.securitySchemes`，各端点也没有 `security` 声明**，
401 响应也未登记。

**后果**：
- **Swagger UI 上没有 "Authorize" 按钮**，无法在文档里调试任何受保护接口
- 用 OpenAPI 生成的 SDK / 前端类型**不会附带 Authorization 头**，
  接入方按文档实现**必然 401**
- 契约层面"哪些接口需要认证"**不可机器判定**，安全评审与自动化测试失去依据
- 这**直接阻断了 §阶段 5 的"从 OpenAPI 生成前端客户端"计划** —— ⚠️ **这一条已经修复**（见本节末的复核）

**修复方向**：改用 `fastapi.security.HTTPBearer`，为受保护路由统一声明
`responses={401: ..., 403: ...}`。**这是阶段 5 的前置条件。**

> ✅ **已经兑现（阶段 0 的第 11 号动作；2026-09-14 复核）**：`openapi.json` 现有
> `components.securitySchemes.HTTPBearer`，118 个操作里 **114 个带 `security` 声明**
> （另外 4 个是 `/health` 与注册/登录/刷新这类免认证端点），`test_auth_contract.py`
> 26 条用例锁死。所以 **S-6 不再是 5.1 的阻塞项** ——
> 5.1 真正的阻塞在**响应侧**（22 个端点缺 `response_model`，见附录 BH）：
> ★ **同一件事两年前卡在"请求侧的鉴权声明"，现在卡在"响应侧的类型"** ——
> 这类"前置条件已经满足、但没人回头改掉那句断言"的残留，正是本文件最容易被误读的地方。

#### 🟡 S-7 `limit` / `page_size` / 请求体普遍缺少上界

- `api/understanding.py:269, 311`：`page_size` 默认 **999**、上限 **9999**
  （每条含 `content` + `source_text` + `metadata_` 全文 → 响应体可达几十 MB）
- `api/graph.py:78-99`：`limit` **无 `le=` 上界**
- `api/graph.py` 的 `keyword` 长度无上限
- **LLM 入参完全无长度限制**：`schemas/note_ask.py:4-9` 的
  `question` / `selected_text` / `context_before` / `context_after` **都是裸 `str`**
  （注释称"前端只截取前后 1500 字符"—— **服务端完全不校验**）；
  `schemas/knowledge.py:140-142` 的 `question: str`；`schemas/assessment.py:6-18`
  的 `material_note_ids: List[str]` 无上限（而 `_merge_note_contents` 会把
  这些 id 指向的**整篇内容**拼进 prompt）
- ASGI 层**无请求体大小上限**

**后果**：单次请求即可把数 MB 文本塞进 LLM prompt，**一次调用烧掉大量 token**；
配合 E-3（无 LLM 配额）形成**确定性的成本攻击**。

#### 🟡 S-8 错误响应回传内部实现细节（与自身设计矛盾）

- `api/assessment.py:39-40, 57-58, 77-78`：`raise HTTPException(500, detail=str(e))`
- `api/understanding.py:659-661`、`api/notes/ask.py:115-117`：
  SSE 直接把 `str(e)` 推给前端
- `api/upload.py:440, 459, 854`：把异常写进 `error_message` 并回传客户端
- `api/knowledge.py:97-98`：回显 LLM 错误

> **2026-09-14 更正（附录 BK.5）**：第 1 行那三处已经不是 `raise HTTPException(500, detail=str(e))`，
> 而是 `raise AppError(ASSESSMENT_*_FAILED, str(e), 500)`（阶段 0.11 的迁移**只加错误码、不改文案**）
> —— **本条的 `str(e)` 泄漏仍然成立**，只是载体换了；这是那次迁移**刻意**的取舍
> （"改文案就是行为变更"，所以留到单独一轮处理）。

**后果**：客户端可拿到 httpx 上游异常、MinIO/S3 错误、
**本地绝对路径（Windows 路径 + 用户名）**、SQL 片段、第三方 API 主机与状态码。

**讽刺的是**：`middleware/error_handler.py:104-118` 对未知异常
**正确地不返回堆栈** —— 是别处用 `str(e)` 自行泄漏的。
**同一项目里两套相反的做法。**

#### 🟡 S-9 `WWW-Authenticate` 头被全局异常处理器丢弃

`api/auth.py:64-81` 两处精心设置了 `headers={"WWW-Authenticate": "Bearer"}`：

```python
raise HTTPException(..., headers={"WWW-Authenticate": "Bearer"})
```

而 `main.py:116-130` 重建响应时**没有传 `headers=exc.headers`**：

```python
return JSONResponse(status_code=exc.status_code, content=_error_payload(...))
```

**后果**：实际响应中**不存在该头**，HTTP 认证语义不完整
（客户端/网关无法据此触发认证流程），且**违反 `auth.py:16` 自己的文档承诺**。
另外该路径也不返回 `X-Request-ID` 响应头（只有中间件路径 setdefault）。

#### 🟡 S-10 `422` 校验错误体破坏 OpenAPI 契约

`main.py:133-143`：

```python
return JSONResponse(status_code=422, content=_error_payload(422, str(exc), "VALIDATION_ERROR"))
```

FastAPI/OpenAPI 规定 422 的 `detail` 是**结构化数组**
（`HTTPValidationError.detail: List[ValidationError]`），此处压成 `str(exc)`。
前端 `client.ts:212-215` 的 `Array.isArray(error.detail)` 分支**永远不成立**，
用户看到一长串 pydantic 内部文本（**含字段路径与回显的输入值**）。

**后果**：所有按规范生成的客户端/测试断言在参数错误时失败；错误提示不可读、不可 i18n。

#### 🟡 S-11 内部路径与任务 ID 回传客户端

`schemas/note.py:35-41` 的 `NoteDetailResponse` 包含
`original_file_path` / `original_md_path`；
`api/cleaning.py:233-239` 返回整份 `metadata_`（含 `clean_task_id`、`file_hash`、
`duplicates_detail`）。

**后果**：暴露服务端目录结构、用户 ID 布局与 **Celery 任务 ID**
（结合 broker 暴露面可被用于任务探测），对攻击者测绘有价值。

#### 🟡 S-12 死代码中含危险能力

- `services/storage_service.py:105-161` 的 `remove_project_dir`（**无调用方**）
  会执行 `shutil.rmtree(root / prefix)` ——
  若未来被调用方传入用户可控的 `prefix`，**即成为目录删除型路径穿越**
- `note_service.py:952-958` 的 `delete_note_material_links`（无调用方）
  按 `note_id` 删链接且**无 `user_id`**
- `config.py:97` 的 `jwt_algorithm: str = "HS256"` **来自环境变量**，
  `auth_service.py:106` 直接 `algorithms=[settings.jwt_algorithm]` ——
  误配为 `none` 会让 python-jose 接受无签名令牌（**待实机验证** jose 对 `none` 的处理）

#### 🟡 S-13 上传临时区未按用户隔离

`api/upload.py:595-601`：`temp_dir = TMP_UPLOAD_DIR / temp_id`（**无 user 维度**）；
`:686-694` 的 commit 只校验 UUID 正则，**不校验该 temp 是否由当前用户创建**。

**后果**（**待确认**，UUID4 不可枚举）：若 temp_id 经日志/浏览器历史泄漏，
攻击者可将**受害者已暂存的文件** commit 进自己账号。

#### 🟡 S-14 依赖与安全响应头

- `requirements.txt:14` `python-jose[cryptography]~=3.3.0`
  受 **CVE-2024-33663**（算法混淆）与 **CVE-2024-33664**（JWE 压缩炸弹 DoS）影响。
  本项目用对称 HS256，影响有限，但应升级到 `>=3.4.0` 或迁移 PyJWT
- 无 HSTS / CSP / `X-Content-Type-Options` / `Referrer-Policy`
  （后端 `main.py:85-100` 只有 CORS 与两个自定义中间件）
- **无邮箱验证、无改密、无找回密码入口**（`api/auth.py` 仅 5 个端点）——
  口令泄漏后用户**无法自助轮换**
- `bcrypt` 只取**前 72 字节且静默截断**：`max_length=100` 的**中文字符**
  （3 字节/字）意味着**第 25 个字之后全部无效**，用户以为的长密码实际被截断

---

### 2.10 🔴 运行期发现：**这个应用的 LLM 调用从未成功过**

> 这一节是**动手修复时实测发现**的，静态审查看不出来，
> 但它的严重性超过前面所有条目 —— 因为它意味着**整条 AI 管道从未运行过**。

#### 🔴 R-1 OpenCode 网关要求 `x-opencode-session` 头，缺失即 400

`backend/.env:35` 把 LLM 网关指向了 OpenCode：
```env
DEEPSEEK_BASE_URL=https://opencode.ai/zen/go/v1
```

**实测**：不带该头调用 `/chat/completions`：
```json
HTTP 400
{"type":"error","error":{"type":"MissingSessionID",
 "message":"Error from provider (Console Go): Request is missing x-opencode-session
  and cannot be routed efficiently. Please see https://opencode.ai/docs/go/?..."}}
```
加上 `x-opencode-session: <任意 UUID>` 后**同一请求立即 200**。

而 `llm_service.py` 的**两条**请求路径（`chat_detailed` 与 `chat_stream`）
都只设了 `Authorization` + `Content-Type`。

**后果（全链路）**：
- 清洗的向量去重（若走 LLM 场景）、**理解抽卡、自动出题、知识图谱语义推断、
  RAG 问答、选中文本提问、学习评估**——**全部 400 失败**
- `extract_knowledge_points` 抛 `HTTPStatusError` 后被上层捕获 →
  记一条 warning → `return []` → **笔记仍然流转到 `archived`**，用户看到"理解完成"但零知识点
- 这解释了 §1.2 的实测：**1058 道题的 `interval` 全部为 1、82% 的题目从未排过复习**
  —— 因为题目生成与判分链路从未真正跑通

**为什么难发现**：`llm_service.py:199-208` 的 4xx 分支只记
`error={e}`，而 `str(httpx.HTTPStatusError)` 只有
`Client error '400 Bad Request' for url ...`，**不含响应体**，
所以网关真正的原因（`MissingSessionID`）从不进日志。

**修复**（本节已实施）：
1. `services/llm/client.py` 新增 `build_llm_headers()`，
   对 `opencode.ai` 域自动附加 `x-opencode-session`；会话 ID **按事件循环缓存**，
   避免每次业务调用新建会话（`LLMService` 是"每调用 new 一个"的用法）
2. `chat_detailed` 与 `chat_stream` **两处**都改用该函数
3. 新增 `_response_snippet()`，4xx 日志**打印响应体**（截断 500 字符），
   让网关原因可见
4. `config.py:164` 的 `llm_timeout_seconds = 600` 是**总超时**；
   推理模型单次可达分钟级，配置本身可接受，但需与 `max_tokens` 一同评估（见 R-2）

**验证结果**（修复后实测）：

| 路径 | 结果 |
|---|---|
| `chat()` 非流式 | ✅ 200，2.6 s |
| `extract_knowledge_points()` | ✅ 200，7.2 s，**返回 7 个结构化知识点** |
| `chat_stream()` SSE | ✅ 200，3.0 s，9 chunks |
| `rag_answer()` | ✅ 200，5.9 s |
| session 在同一事件循环内稳定 | ✅ |

#### 🟠 R-2 网关模型是 **reasoning 模型**，`reasoning_tokens` 与 `content` 争抢 `max_tokens`

响应中实测存在 `reasoning_content` 字段与
`usage.completion_tokens_details.reasoning_tokens`：

```
max_tokens=512    content=63   reasoning=266   reasoning_tokens=66   finish=stop
```

**即：模型先产出思维链，再产出正文，两者共享同一个 `max_tokens` 预算。**

**后果**：当 `max_tokens` 偏小时（例如某些调用点传 1024），
可能**全部预算被思维链消耗、`content` 为空**，
而 `finish_reason` 为 `stop`（不是 `length`）——
此时 `chat()` 返回空字符串，`json.loads("")` 抛错，
走 `except json.JSONDecodeError: return []` → **静默零结果**。

> **诚实说明**：我最初用 `max_tokens=64` 观察到了这个现象，
> 但后续验证表明那次的触发条件里也包含我自己构造的提示词中
> JSON 示例字面量与需求重叠。用**项目真实提示词**复测时
> `max_tokens` 从 64 到 16384 **全部返回有效 JSON**。
> 因此这条**不是已确认的故障**，而是一条**必须防御的脆弱点**：
> 在长文档 / 复杂批次下，思维链显著变长，空 `content` 是可达的。

**修复方向**：
1. `chat_detailed` 已返回 `truncated`（`finish_reason == "length"`）；
   应**再补一条**：`content` 为空时视为失败（而非成功返回空串），
   并纳入截断重试逻辑（放大 `max_tokens` 后重试一次）
2. 面向 JSON 的结构化调用，`max_tokens` 应预留思维链开销
   （当前 `llm_json_max_tokens = 16384` 足够，但需要显式注释这一约束）
3. 把 `reasoning_tokens` 计入日志与成本统计

#### 🟠 R-3 `response_format` 确实被网关限制，但当前用法可用

实测：
- `response_format={"type":"json_object"}` → **200**（可用）
- `response_format={"type":"json_schema", ...}` → **400**
  `"This response_format type is unavailable now"`

当前代码只用 `json_object`（`llm_service.py` 多处），**因此不受影响**。
但阶段 4 若计划迁移到严格 JSON Schema 结构化输出，**该网关不支持**，
需改用"提示词约束 + 本地 Pydantic 校验 + 重试"的方案。

---

## 第三部分 · 四把手术刀

诊断完了。但"修 60 个问题"不是方案。必须找出**根因**，
用最少的动作消除最多的症状。

### 手术刀 1：把 SQLite + 文件 broker 换成 PostgreSQL + Redis

> ⚠️ **2026-09-10 路线变更（据用户决策）**
> 用户明确表示**磁盘空间不足，暂不采用 Docker 容器方案**。
> 因此本手术刀**降级为可选路径**，当前执行的是下面的
> **「手术刀 1′：保留 SQLite，但把它做对」**。
> 原方案内容保留，供将来有条件时迁移参考。

**消除的症状**：D-1（database is locked）、D-2（启动重建表）、
D-7（任务丢失、长任务阻塞全站）、D-8（无进度，部分）、
A-5（无 FTS）、E-1（无分布式限流）、E-7（无队列深度指标）

**为什么这是根因而不是症状**：
EngramNote 的架构天然是**多进程、长任务、外部 API 密集**的：

```
3 个常驻进程（API / Worker / Beat）+ N 个外部服务
+ 单任务耗时 600 秒级 + 写操作密集（状态机 10 个状态）
```

SQLite 的设计前提是"单写者、短事务、单进程（或少量进程）"。
把它放在这个场景里，**每一个上层问题都是它的投影**：
- 怕锁 → `busy_timeout=5000` → 不够 → 只能靠重试
- 怕并发改 schema → 发明文件锁 + 启动重建表
- 文件 broker → 任务丢失 → 靠 `acks_late` + 手写状态机补偿

**换掉之后，D-1/D-2/D-7 直接消失，不需要任何补偿逻辑。**

**代价与迁移路径**：
- `config.py:305-310` 已经有 `database_url` 配置项并支持 PostgreSQL 分支
  （`database.py:68-71` 已有 `pool_size` / `max_overflow` 分支）—— **结构已备好，只差落地**
- `docker-compose.yml:76-100` 已把 postgres/redis 写好并注释掉 —— **只需取消注释**
- `asyncpg` 在 `requirements.txt:31` 已注明（注释状态）

**唯一真正的工作量**：Table 类型的地基（D-4/D-5/D-11）。
`JSON` 列在 PG 上应改 `JSONB`；`Enum` 应改原生 enum 或 CHECK；
`_migrate_sqlite` / `_rebuild_dangling_tables` **整体删除**（约 400 行代码净减少）。

---

### 手术刀 1′：保留 SQLite，但把它做对（**当前执行路径**）

**约束**：不使用 Docker（磁盘空间不足），因此没有 PG / Redis。
**目标**：在不换存储的前提下，把 SQLite 从"随时可能锁死、随时可能丢数据"
修成"单机可用、行为可预期"。

#### 已完成（阶段 0 实测）

| # | 动作 | 位置 | 实测结果 |
|---|---|---|---|
| 1 | **启用 WAL** | `database.py:_set_sqlite_pragma` | `journal_mode` 由 `delete` → **`wal`**；读写不再互相阻塞 |
| 2 | **busy_timeout 5s → 30s** | 同上 | LLM 相关写事务可跨越外部 API 调用，5 秒过短必然抛 `database is locked` |
| 3 | **`synchronous=NORMAL`** | 同上 | WAL 下仍保证崩溃一致性，显著降低长任务链路的写延迟 |
| 4 | **启动不再重建表** | `init_db()` / `_rebuild_dangling_tables` | 破坏性 DROP/重建改为需 `ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION=1` 显式放行；默认仅**探测并告警**。实测：启动前后行数与 `review_logs` 内容哈希**完全一致** |
| 5 | **启动不再删学习记录** | `_migrate_sqlite` 孤儿检查 | 8 条 `DELETE` 改为 `SELECT COUNT` + 告警。`review_logs` 是用户最不可再生的资产，不能当垃圾清理 |
| 6 | **`card_relations` 唯一索引口径对齐** | `_migrate_sqlite` | 去重 GROUP BY 含 `status` 而索引只有 4 列 → 建索引必冲突且被 `except` 吞成 warning，导致该约束**在生产可能压根不存在**。现统一为 5 列，且失败改为 `logger.error` |

#### 已完成（阶段 1.5′ 追加，2026-09-11）

| # | 动作 | 位置 | 实测结果 |
|---|---|---|---|
| 7 | **备份机制**：`VACUUM INTO` 原子快照脚本，支持 `--label` / `--keep` / `--list`，落盘后自动 `integrity_check` | `scripts/backup_db.py` | 本轮每次改库前都已备份（`_backup/20260911-*`），多次实跑均 `integrity=ok` |
| 8 | **非破坏性加列通道**：`review_logs` 加 `self_rating` / `grading_method`，纯加列、nullable/带默认值，**不经过 `ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION` 闸门** | `database.py:_migrate_sqlite` | 真实库 194 行历史全部回填 `legacy`；行数、`integrity` 均不变 |
| 9 | **测试真隔离**：根因是 `database.py` 在 import 时冻结了 `database_url`/`_is_sqlite`，重绑定模块属性改不到 `lru_cache` 的 `get_engine()` | `database.py` / `tests/conftest.py` | 旧 `test_db` fixture **从未真正隔离过**——临时库与 API 实际连的库是两个对象。新增 `test_db_isolation.py`（6 用例）锁死该契约 |
| 10 | **`verify_fix_*.py` 修复**：三个脚本同样用"重绑定 `db_mod.engine`"换库，因此验证结论是假的、且对真实库有写权限 | `scripts/_tmpdb.py` | 收敛为唯一入口 `bootstrap_temp_db()`，**内置自检**：engine 与 session factory 不一致、或未指向 `tmp_test` 时直接抛错拒绝运行 |
| 11 | **API 契约修复**：`HTTPBearer` 替代手写 `Authorization[7:]`；自定义 `http_exception_handler` 转发 `exc.headers` | `api/auth.py` / `main.py` | 修复前 `WWW-Authenticate` **从未到达客户端**（handler 重建响应时丢掉了 `exc.headers`），`openapi.json` 也无 `securitySchemes`。`test_auth_contract.py`（26 用例）锁死 |
| 12 | **限流**：登录 10/min、注册 5/min、LLM 端点独立配额 | `middleware/rate_limit.py` | 进程内滑动窗口，已在 `main.py` 注册，顺序 `RequestContext → ErrorHandler → RateLimit → CORS` |

#### 仍需完成（阶段 2′）

> 状态列于 2026-09-11 更新：第 1/3/4/5 项已完成，详见下方「方案 A 定案」。

| # | 动作 | 目的 |
|---|---|---|
| 1 | ~~**收拢为单写者**：把 API 侧的写操作（状态流转、配额、版本快照）通过 Celery 任务串行化，或将 uvicorn 固定为 `--workers 1` 并在文档中写明~~ | ✅ **已落地**（方案 A，见下） |
| 2 | ~~**给所有长事务加显式短事务约束**：禁止把"外部 API 调用"包在 DB 事务里~~ | ✅ **已排查并处理**（审计命中 0 处；真实风险在 `get_db` 会话作用域，见下） |
| 3 | ~~**任务可靠性（不依赖 Redis）**：为 convert/clean/understand 三个任务补 `task_reject_on_worker_lost=True`，并新增"僵尸任务自愈" Beat 任务~~ | ✅ **已落地**（全局 reject_on_worker_lost + soft/hard 超时 + Beat 每 5 分钟自愈） |
| 4 | ~~**进度与取消**：新增 `task_runs` 表 + `GET /api/tasks/{id}` + 取消接口~~ | ✅ **已落地**（取消如实返回 `terminated=false`：文件 broker 无法强杀） |
| 5 | ~~**备份调度化**：把 `scripts/backup_db.py` 挂到 Celery Beat + 保留策略 + 一键恢复脚本~~ | ✅ **已落地**（Beat 每日 03:30；新增 `scripts/restore_db.py`） |
| 6 | **全文检索**：SQLite FTS5（`pg_bigm` 的替代）替换纯 Python BM25 | 免维护索引、支持中文分词扩展；这是 A-5 在 SQLite 路线下的正解 |
| 7 | **向量检索**：评估 `sqlite-vec` 扩展（单文件、无需 Docker）替代 Chroma 的多 collection | 消除"每篇笔记一个 collection、检索要遍历全部"的 O(n) 问题 |

##### 方案 A 定案：单写者（2026-09-11）

「收拢为单写者」原有两个方案，**已选方案 A**：固定单写者，
并把项目定位为**本地单用户自托管**。上表第 1/3/4/5 项均已完成。

**强制手段**：`app/main.py::_enforce_single_writer()` 在启动阶段检查
`WEB_CONCURRENCY` / `UVICORN_WORKERS`，>1 且数据库为 SQLite 时**直接启动失败**。
刻意不自动降为 1 —— 静默降级会让运维以为多进程部署成功、实际只跑一个 worker，
比启动失败更糟。

**为什么必须拦**：WAL 只允许"一写多读"。多进程写时
`RateLimitMiddleware` 的滑动窗口、`tasks/common.py` 的引擎缓存都是**进程内**状态，
N 个进程 = 限流阈值放大 N 倍，表现为"限流配了却能被刷 N 倍"。
这类问题**不会被任何单元测试发现**（测试是单进程的），只在部署后以
"偶发 500 / 限流失效"的形态出现。

完整约束、接受的取舍、以及"何时应放弃该路线改迁 PG"见
**`docs/sqlite-single-writer.md`**。该文件同时把第六部分的 D1/D2/D5 标注为
**已定路线**，解决"7 阶段执行计划仍按 PostgreSQL 写、与既定路线矛盾"的问题。

##### 第 2 项的排查结论（与文档假设不同）

文档假设的症状是"把外部 API 调用包在 DB 事务里"。用 AST 审计
（扫描"写操作之后、commit 之前是否夹着对象存储 / LLM / Celery 往返 / sleep"）
实测**命中 0 处** —— 本仓库的任务与 RAG 链路要么不接 `db`，
要么在外部调用前就结束了 session。

真正存在的机制是另一个：`get_db()` 的 session **作用域是整个请求**
（FastAPI 的 yield 依赖在响应结束后才收尾），而 SQLAlchemy 是 `autobegin`
语义，一次 SELECT 就开启事务。因此：

- 用 `StreamingResponse` 的 SSE 端点在**整个流式输出期间**持有 session。
  只读的 SSE 在 WAL 下不阻塞写，**但**若某端点在 `yield` 之前写过且未提交，
  写锁会被持有数十秒。
- 规则已写成测试锁死：`tests/test_session_lifecycle.py`
  用 AST 静态审计"所有含 yield 的函数不得在首个 yield 前存在未提交写操作"。
- `get_db()` 同时补上显式回滚与**兜底提交**（覆盖"路由忘了 commit"
  导致 HTTP 200 但数据没落库的静默失败）。
  注意兜底提交必须放在 `finally`：生成器被 `aclose()` 收尾时抛的是
  `GeneratorExit`（继承自 BaseException），`except Exception` 接不到。


#### SQLite 路线下**接受**的取舍（不再试图消除）

- 不支持高并发写入 → 定位为**单用户/小团队自托管**
- 无水平扩展能力 → 需要时再迁 PG（手术刀 1 的路径仍然有效）
- 无跨进程限流 → 限流用进程内实现（已落地），多进程部署时各进程独立计数

### 手术刀 2：把"学习调度状态"从 `QuizItem` 抽到 `ReviewState`（用户 × 知识项）

**消除的症状**：D-3（重跑理解丢学习历史）、L-2（掌握度无意义）、
L-5（无题目的卡片无法复习）、L-8（薄弱点信号弱，部分）

**建模修正**：

```
现在（错）：
  QuizItem { 题目内容, answer, interval, repetition, EF, next_review_at, ... }
             ↑内容与调度混在一张表

目标（对）：
  QuizItem   { id, card_id, question_type, question, answer, options, explanation }
             —— 纯内容，可重新生成、可替换

  ReviewState { user_id, item_type, item_id,      ← item_type ∈ {card, quiz}
                stability, difficulty,             ← FSRS 参数
                due_at, last_review_at,
                reps, lapses, state,               ← new/learning/review/relearning
                UNIQUE(user_id, item_type, item_id) }

  ReviewLog   { user_id, item_type, item_id, rating, elapsed_ms,
                predicted_retention, actual_correct, reviewed_at }
                —— 不可变事件流，永不删除
```

**这一改直接带来四个能力**：
1. **重新理解不丢历史**：题目被替换，但 `ReviewState` 按 `card_id` 保留
   （`item_type='card'`），新的题目继承卡片的调度状态
2. **卡片可以直接复习**（`item_type='card'`）→ 解决 L-5 的"死卡片"
3. **`ReviewLog` 变成可分析的事件流** → 可以算真实保持率、可以做 FSRS 参数拟合
4. **`reps` / `lapses` 成为一等公民** → leech 检测、难度建模都变得可能

**顺带修正 D-3 里的孤儿清理**：`database.py:537-544` 删除
"`quiz_id` 已不存在"的 `review_logs` 必须**改为保留**（或迁移为 `item_id` 悬挂）。
**学习记录是用户最宝贵的资产，绝不能当垃圾清掉。**

### 手术刀 3：把检索层重建成"原文优先的单一索引"

**消除的症状**：A-1（错误相似度）、A-2（检索摘要而非原文）、
A-4（中文分词缺陷）、A-5（每次重建 BM25）、A-9（O(n²) 相似度）、
D-9（90+ collection）、D-10（去重键错误）、A-6/A-7/A-14（RRF 设计问题）

**重建后的检索层**：

```
┌─ 索引源（唯一）─────────────────────────────────┐
│  清洗后的 Markdown，用 markdown_segmenter        │
│  （结构感知分块，唯一的分块实现）                │
│  每个 chunk 记录：note_id, heading_path,         │
│                   char_start, char_end           │
└──────────────┬──────────────────────────────────┘
               │
      ┌────────┴────────┐
      ▼                 ▼
┌─────────────┐  ┌──────────────────────┐
│ 向量索引     │  │ 全文索引              │
│ PG + pgvector│  │ PG tsvector +        │
│ HNSW, cosine │  │ pg_bigm / zhparser   │
│ 单表，非 90  │  │ （中文真正的分词）    │
│ 个 collection│  │                      │
└──────┬──────┘  └──────────┬───────────┘
       └────────┬───────────┘
                ▼
        RRF / 加权融合
        （去掉 n-gram 路）
                ▼
        引用 = (note_id, heading_path, char_range)
        —— 可精确回跳到原文段落
```

**关键决策**：
1. **唯一分块实现** = `markdown_segmenter.py`。
   删除 `cleaning_service.split_into_chunks()`，两者合一。
2. **索引原文，不索引卡片**。卡片是"学习单元"，原文是"证据来源"，两者职责不同。
   卡片可以额外建一个小索引用于"卡片搜索"，但**RAG 的证据必须来自原文**。
3. **相似度用余弦**：`metadata={"hnsw:space": "cosine"}` 或
   `pgvector` 的 `vector_cosine_ops`。**统一度量，统一阈值口径**。
4. **引用可回跳**：chunk 存 `char_start/char_end` + `heading_path`，
   前端点击引用 → 打开笔记 → **滚动并高亮该段落**。
   这是"AI 只做减法、原文是证据"这个产品承诺的**技术落地**。
5. **删除 n-gram 通道**。它是 BM25 的更差版本；保留它只会注入噪声。
   若担心 BM25 的中文效果，用 `pg_bigm` / `zhparser` 做真正的分词，而不是叠加字符匹配。

### 手术刀 4：把"打分"从规则猜测换成"自评 + LLM 语义判分 + 校准闭环"

**消除的症状**：L-1（判分器可被骗）、L-3（无学习效果度量）、
L-4（SM-2 过时，部分）、L-7（只考 LLM 出的题）

**三层替代方案**：

**第 1 层 —— 用户自评（最高优先级，零成本，最准）**

Anki 四档：`Again / Hard / Good / Easy`。记忆是一个**主观**现象，
"你觉得自己想起来了没有"比任何自动判分都准。
**当前的 `quality_from_answer` 完全丢弃了这个信号。**

**第 2 层 —— 客观题用确定性判分，主观题用 LLM 语义判分**

- **选择题 / 填空题**：确定性判分（保留现有逻辑，但**删除单字符集合的模糊匹配**，
  填空必须精确或白名单同义词）
- **简答题**：调用 LLM 做语义等价判定，输出结构化结果：
  ```json
  {
    "verdict": "correct | partial | incorrect",
    "missing_points": ["未提到收敛性条件", "把充分条件说成了必要条件"],
    "misconceptions": ["混淆了方差与偏差"],
    "confidence": 0.92
  }
  ```
  **关键**：不是让 LLM 打个 0-100 的分，而是**指出具体缺了什么、错在哪**。
  这才对学习有指导价值，也能直接驱动"重新讲解该知识点"。
- **禁止用字符 n-gram 集合交并比判分**。

**第 3 层 —— 校准闭环（这是当前完全缺失的部分）**

每次复习都记录：
```
predicted_retention  ← 调度器在复习前预测的"此刻能想起的概率"
rating               ← 用户自评 / 判分结果
实际是否正确
```

然后：
- **校准曲线**：预测 0.9 的那批卡片，实际正确率是多少？
  如果实际是 0.6，说明调度器**高估了用户的记忆**，间隔太长。
- **保持率报告**：给用户看"你的 30 天保持率 = 87%"，
  这是**唯一能证明产品有效的指标**。
- **参数拟合**：用累积的 `ReviewLog` 拟合 FSRS 参数（个人化），
  而不是所有用户共用一套硬编码常数。

**顺带把 SM-2 升级为 FSRS**：
- FSRS 用 `(difficulty, stability)` 显式建模，天然包含"时间维度"
  → **直接解决 L-2 的"掌握度不随时间变化"**
- 有公开的参考实现与参数优化器
- 同等保持率下更少的复习量

---

## 第四部分 · 目标架构

### 4.1 八条不可违背的原则

| # | 原则 | 当前违反点 |
|---|---|---|
| **P1** | **单一真相源**：PostgreSQL 是唯一权威存储。文件系统是**派生物**，可随时从 DB 重建 | D-4 双写、D-2 启动重建 |
| **P2** | **原文不可变，AI 产物可重建**：任何 LLM 产物（卡片/题目/摘要/向量）都必须能从原文+提示词版本重建，且重建不丢用户数据 | D-3 重跑丢历史、A-10 无幂等 |
| **P3** | **学习数据是不可变事件流**：`ReviewLog` 只追加，永不删除、永不改写 | D-3 孤儿清理删 review_logs |
| **P4** | **引用必须可回溯到原文段落**：任何 AI 生成的答案/卡片/题目都要能一键跳回原文对应位置 | A-2/A-7 引用到笔记级 |
| **P5** | **成本是一等公民**：每次 LLM 调用都记账（用户、笔记、场景、token、金额），有配额、有告警、有报表 | A-12 只进日志 |
| **P6** | **一切长任务有进度、可取消、可重试、可自愈**：无"转圈 40 分钟" | D-8 只有状态枚举 |
| **P7** | **失败必须响亮**：禁止 `except: pass`，禁止静默降级。降级要在 API 响应和 UI 上显式表达 | 19 处静默吞异常、A-8 静默失效、D-7 静默部分失败 |
| **P8** | **可验证**：核心算法（调度、判分、检索）必须有指标和测试；任何"效果"声明必须有数据支撑 | E-6/E-7 无 CI 无指标、L-3 无效果度量 |

### 4.2 目标技术栈

| 层 | 现在 | 目标 | 理由 |
|---|---|---|---|
| **数据库** | SQLite + aiosqlite | **PostgreSQL 16 + asyncpg + pgvector** | 手术刀 1；向量与关系数据同库，去掉 Chroma 的 90+ collection |
| **全文检索** | 纯 Python BM25（每次重算） | **PG `tsvector` + `pg_bigm`**（中文） | 索引持久化、CJK 真正分词 |
| **任务队列** | Celery + 文件 broker + `--pool=solo` | **Celery + Redis**（或 arq/Dramatiq） | 手术刀 1；任务不丢、真并发、可观测 |
| **调度器** | SM-2（自实现） | **FSRS**（`fsrs` 库或自实现 + 个人化参数拟合） | 手术刀 4；现代算法、有保持率模型 |
| **判分** | 字符 n-gram 集合交并比 | **用户自评 + 确定性判分 + LLM 语义判分** | 手术刀 4 |
| **迁移** | 3 套并存 | **仅 Alembic**，启动不建表 | P1；删除 ~400 行手写迁移 |
| **LLM 网关** | `llm_service.py`（831 行）+ 类级限流 | **独立 `LLMGateway`**：预算、记账、缓存、熔断、结构化输出校验、prompt 版本化 | P5、P7 |
| **可观测性** | request_id + 日志 | **+ Prometheus `/metrics` + `/ready` + OTel trace + 成本看板** | P5、P8 |
| **前端状态** | `useState`/`useEffect` 自管 | **TanStack Query**（服务端状态）+ **Zustand**（UI 状态）+ **路由级懒加载** | E-8 |
| **前端类型** | 手写 **111** 个 API 函数（**原文写 90 —— 2026-09-14 实测修正**，见附录 BH）+ 手写类型 | **从后端的 OpenAPI 生成客户端与类型**（选型已定 `openapi-typescript`：schema 与类型已生成、111 个函数的分歧已量出；**切换卡在后端补响应模型**，见附录 BH） | 消除契约漂移 |
| **测试** | 无 CI、混入危险脚本 | **pytest + testcontainers + Vitest + Playwright + GitHub Actions** | P8 |
| **错误契约** | 中文文案 + 5 态返回 | **稳定错误码 + `AppError` + RFC 7807 problem+json** | E-10 |
| **配置** | 一个 `debug` 开关控三件事 | **拆开**：`env` / `log_sql` / `llm_provider` 独立 | E-5 |

### 4.3 目标数据模型（核心变更）

```
users ──┬── projects ── note_projects ──┐
        │                                │
        └── notes ──────────────────────┬┘
             │  (id, user_id, title, source_type, status, ...)
             │  ❌ 删除：original_md_path / clean_md_path（路径由约定推导，不入库）
             │  ✅ 新增：content_hash, pipeline_version, error_code
             │
             ├── note_versions          （保留，改为内容寻址）
             │
             ├── chunks                 ★ 新：原文分块（单一分块实现）
             │     (id, note_id, ordinal, heading_path, text,
             │      char_start, char_end, token_count)
             │     └─ embedding VECTOR(1024)   ← pgvector，hnsw cosine
             │     └─ tsv TSVECTOR             ← 全文检索
             │
             ├── cards                  ★ 由 knowledge_cards 演化
             │     (id, note_id, chunk_id, card_type, title, content,
             │      source_quote, prompt_version, model, generated_at)
             │     ↑ source_quote 必须逐字来自 chunk → P4 可回溯
             │     ↑ prompt_version/model → P2 可重建/可评估
             │
             ├── card_relations         （保留，加 confidence）
             │
             └── quiz_items             ★ 纯内容，去掉调度字段
                   (id, card_id, question_type, difficulty,
                    question, answer, options, explanation,
                    prompt_version, model, generated_at)

review_states  ★ 新（手术刀 2）
  (id, user_id, item_type ∈ {card,quiz}, item_id,
   stability, difficulty, due_at, last_review_at,
   reps, lapses, state ∈ {new,learning,review,relearning},
   UNIQUE(user_id, item_type, item_id))

review_logs  ★ 重建为不可变事件流
  (id, user_id, item_type, item_id,
   rating ∈ {again,hard,good,easy},
   predicted_retention,       ← 校准闭环的输入
   was_correct,
   elapsed_ms, reviewed_at)   ← 只追加，永不删改

llm_calls  ★ 新（P5 成本账本）
  (id, user_id, note_id, scene, provider, model,
   prompt_version, prompt_tokens, completion_tokens,
   cached_tokens, cost_usd, latency_ms, status, created_at)

task_runs  ★ 新（P6 进度与自愈）
  (id, task_id, kind, target_id, user_id,
   state, progress_pct, stage, message,
   started_at, heartbeat_at, finished_at, error_code)

vault_files  ★ 新（P1 文件系统降级为派生索引）
  (id, user_id, note_id, kind, path, sha256, size, created_at)
  —— 有了它，"磁盘 vs DB 不一致"可以被**检测和修复**，而不是靠祈祷
```

**净变化**：表从 ~19 张 → ~15 张（删掉 5 张防御性建表的影子表，
新增 5 张承担明确职责的表），**代码减少，能力增加**。

### 4.4 目标学习核心（产品差异化所在）

这是**必须做对的部分**。当前的三条流水线要合并成一个真正的闭环：

```
                    ┌──────────────────────────────────┐
                    │  1. 摄入（Ingest）                │
   上传 PDF ───────►│  MinerU → Markdown → 清洗        │
                    │  产出：原文 + chunks（带位置）    │
                    └──────────────┬───────────────────┘
                                   ▼
                    ┌──────────────────────────────────┐
                    │  2. 拆解（Decompose）             │
                    │  LLM 抽出 cards，每张卡带         │
                    │  source_quote（逐字来自原文）     │
                    │  ✅ 用户可审核/编辑/拒绝          │
                    │  ✅ 每张卡可回跳原文              │
                    └──────────────┬───────────────────┘
                                   ▼
        ┌──────────────────────────┴──────────────────────────┐
        ▼                                                     ▼
┌───────────────────┐                            ┌───────────────────────┐
│ 3a. 主动回忆       │                            │ 3b. 深度理解（可选）   │
│  （复习，核心）    │                            │  RAG 问答             │
│ FSRS 调度          │                            │  ✅ 只从原文检索       │
│ 卡片正面 → 用户回想 │                            │  ✅ 引用可回跳段落     │
│ 翻面 → 自评 4 档    │                            │  ✅ 无据则明说"没有"   │
│ 或 LLM 语义判分    │                            └───────────────────────┘
└─────────┬─────────┘
          ▼
┌──────────────────────────────────────────────────────────┐
│ 4. 度量（当前完全缺失 —— 这是产品的护城河）                │
│  · 真实保持率曲线（30/90/180 天）                          │
│  · 校准曲线（预测保留率 vs 实际正确率）                     │
│  · 卡片质量反馈（高 lapse 卡片 → 提示重写）                 │
│  · FSRS 个人化参数拟合                                     │
│  · 误判检测（LLM 判分与用户自评不一致 → 人工复核样本）       │
└──────────────────────────────────────────────────────────┘
```

**关键差异点**（这是本项目相对 Anki / Notion / Obsidian 的真正价值）：

1. **从资料到卡片全自动，但每一步可审核**
   （现在是"全自动且不可审核"，用户被动接受 LLM 的抽取质量）
2. **每张卡、每个引用都能回跳原文段落**
   （现在引用只到笔记级，卡片与原文无位置关联）
3. **学习效果可度量、可展示**
   （现在只有活动量：复习了多少次、掌握了 76%）
4. **答错时告诉用户"错在哪"，而不是只给一个分数**
   （现在只给对/错，或用错误的 n-gram 判分）

---

## 第五部分 · 分阶段整改路线图

**总原则**：每个阶段**独立可验证、独立可回退**。
不允许跨阶段并行（这是当前项目失败的核心教训 —— 同时改 5 件事）。

### 阶段 0 · 止血（1 周）—— 不动架构，只消除正在流血的风险

> ⚠️ **本表原无状态列**（表头是 `| # | 动作 | 解决 | 验收 |`）。
> **2026-09-14 逐条核对代码后补入 `状态` 列**（附录 BJ）——
> 缺口是"从来没有过"，不是"跟丢了"，因此这里补的是**证据**而不是一列标记。
> 派工说明：`✅` 已完成 / `🟡` 部分完成 / `⏸` 未做 / `⛔` 按既定路线不适用。

| # | 动作 | 解决 | 验收 | 状态（2026-09-14 核对） |
|---|---|---|---|---|
| 0.1 | **加 CI**：GitHub Actions 跑 `ruff` + `eslint` + `pytest -m "not integration"` | E-6 | 每个 PR 必过 | ✅ **已落地**（`.github/workflows/ci.yml`）：后端 ruff（app/tests/scripts 三段）+ 离线 pytest、前端 lint + tsc/build + Vitest + Playwright（阻断）、依赖安全扫描（建议性）、nginx/`.dockerignore` 配置守卫。**不只是存在** —— 它当场抓到过真缺陷（登录成功落 404、CI 依赖清单漂移 64 failed） |
| 0.2 | **测试隔离**：`conftest.py` 加守卫，禁止测试触网/写生产库；把 `backend/` 根目录 15 个脚本移入 `scripts/dev/`；`backend/tests/` 只留正式测试 | E-6 | `pytest` 离线可跑、零 API 调用 | ✅ **已落地（搬迁已于 2026-09-14 执行，本条由此收口）**。守卫：`tests/conftest.py:128-142` 网络阻断（`ENGRAMNOTE_ALLOW_NETWORK_TESTS=1` 才放行）、`:313-370` **真实生产库写入守卫**（`before_cursor_execute` 层拦截）、`pytest.ini` 的 `norecursedirs = tests/integration`（8 个联网脚本已归位，`src` 收集期 8 errors → 0）。**搬迁**：15 个一次性脚本（`test*.py` × 11 / `verify_clean.py` / `e2e_cleanup.py` / `reset_cleaning.py` / `restore_note.py`）已全部移入 `backend/scripts/dev/`，`backend/` 根目录**已无任何 `.py`**；`test_clean_failed_api` 三个脚本里硬编码的库路径、`test_e2e.py` / `test_pdf_pipeline.py` / `test_convert_direct.py` 里按 `__file__` 推项目根的写法都已同步（否则搬迁会当场崩）。⚠️ **仍未做的一半**：`backend/tests/` 根下**仍有 9 个脚本式文件**（`test_full_e2e.py` / `test_full_flow.py` / `test_week5_6_integration.py` / `test_week8_e2e.py` / `test_week8_review.py` / `test_week8_review_existing.py` / `test_week9_10_e2e.py` / `test_week11_e2e.py` / `test_week12_e2e.py` —— 实测每个都是 0 条 `def test_`、只有 `main()` / `log_step()` 这类脚本结构，"`backend/tests/` 只留正式测试"这半未达成），且 14 个原被 `.gitignore` 忽略的文件移到新位置后**不再被忽略**（`.gitignore:63-69` 的收口没做，需人工决定是提交还是继续忽略）—— 两件都登记在 `backend/scripts/dev/README.md`；⚠️ **2026-09-14 后续**：那 14 个文件**已随搬迁入库**（`git ls-files backend/scripts/dev/` 共 **16** 个文件），"提交还是继续忽略"这个决定**已经做出**，剩下的是 `.gitignore:64-69` 那六条模式**不再命中任何文件**（一行收口，见**附录 BK.1 / BK.7**）；搬迁的完整对账（**59** 条 ruff 历史问题、两个疑似死文件、报告了但没改的旧引用清单）见**附录 BK.1** |
| 0.3 | **关掉启动时的破坏性迁移**：`init_db()` 中 `_rebuild_dangling_tables()` 与 `database.py:580-587` 的全局去重改为**显式脚本 + 先备份**，不再随进程启动执行 | D-2 | 启动不再 DROP 表、不再删数据 | ✅ **已落地**：`database.py:260-301` 的 `init_db()` 只做 `create_all()` + `_migrate_sqlite()`（只加不删）+ FTS5 建表；`_rebuild_dangling_tables()`（`:1150`）与 `card_relations` 同键去重（`:1057-1059`）都在 `_destructive_migration_allowed()`（`:390-399`，需显式 `ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION=1`）之后；孤儿检查（`:1002-1036`）已改为**只报告不删除**，针对 `review_logs` 的两条 `DELETE` 不复存在 |
| 0.4 | **SQLite 开 WAL**：`_set_sqlite_pragma` 加 `PRAGMA journal_mode=WAL`（实测当前为 `delete`）；`busy_timeout` 从 5000 提到 30000 | D-1 | 并发读写不再报 locked | ✅ **已落地**：`database.py:138` `PRAGMA journal_mode=WAL`、`:141` `busy_timeout=30000`、`:144` `synchronous=NORMAL`（另 `:131` `foreign_keys=ON`） |
| 0.5 | **修正向量相似度公式**：`embedding_tasks.py:246` 的 `1/(1+distance)` 在归一化向量上等价于错误的度量（无关内容得 0.333，见 A-1）。collection 建时指定 `hnsw:space=cosine` 并改 `1.0 - distance`；**写一个重建全部 collection 的迁移脚本**（当前 `backend/data/chroma/` 有 90+ 个） | A-1 | 相似度落在 [0,1] 且可区分 | ✅ **已落地，但该路径此后已被整体替换**（⚠️ 读者勿误记为"待做"）：公式已在 `embedding_service.py:304-333` 改为 `cos = 1 - d/2`（并附"旧实现得 0.333 错在哪"的推导），单测曾逐行对齐理论余弦。**但它依赖的 Chroma 已在 2.4/2.4′ 被删除**：`chromadb` 移出 `requirements.txt`、`VectorStore` 删除、运行期改为 `chunks` 表 + 纯 Python 单位向量点积（`chunk_search_service.py:100-127`，含模型一致性检查），因此 `similarity_from_l2_distance()` 现在是**全仓零调用方的死代码**（`grep` 命中仅定义处）。"重建全部 collection 的迁移脚本"**从未写**，因为集合被**迁移到 `chunks` 表**（608 行、单一 `bge-m3`/1024 维、100% 可检索，附录 O/P）而不是被重建；`backend/data/chroma/` 目录仍在磁盘上（**实测 114 个子目录**），但已无任何运行期读取方 |
| 0.6 | **停止删除 `review_logs`**：删除孤儿清理中针对 `review_logs` 的两条 SQL；并把 `generate_questions` 的"先删后建"改为**影子生成 + 原子替换**（见 §2.6 M-5） | L-3 / D-3 / M-5 | 学习记录不再被删 | ✅ **前半已落地，后半按记录的理由不执行**。前半：启动路径的 `review_logs` 孤儿 `DELETE` 已删除，改为只报告（`database.py:1002-1036`）。⚠️ 后半（"影子生成 + 原子替换"）**没有做**：重新生成题目时**仍然删除该笔记旧题目对应的复习记录**（`understand_tasks.py:274`、`api/understanding.py:155`、`api/understanding.py:518`、`note_service.py:597`），这是"重新生成"语义的**有意**取舍（见 §2.6 M-5 与 `docs/decisions.md#F-29`），不是待修缺陷 —— 但把它记成"已完成"会让下一个人以为这条链路已无删除 |
| 0.7 | **限流兜底**：`/auth/login`、`/auth/register` 加 IP 级限流；加登录失败计数与锁定；补恒定时间比较（用户不存在时也跑一次 bcrypt） | E-1 / E-2 | 爆破与用户枚举被阻断 | 🟡 **限流与时序对齐已落地，登录失败计数与锁定从未实现**。已落地：`middleware/rate_limit.py:59-105` 的规则表（`^/api/auth/login$` 10/min、`^/api/auth/register$` 5/min，超额 429 + `Retry-After`）；`auth_service.py:404-408` 的 `_DUMMY_HASH` 时序对齐（`:128`）。**缺的是"加登录失败计数与锁定"**：全仓 `grep` 三个关键词（`lockout`、`failed_attempt`、`login_attempt`）**零命中**（`database.py:1116` 的 `msvcrt.locking` 是文件锁，与登录无关），`users` 表也没有相关列。当前的爆破防护**只有**那两道 60 秒滑动窗口（≈10 次/分钟/IP），窗口一过即可继续 —— 计划要的"锁定"这一层不存在 |
| 0.8 | **停止用户枚举**：注册对已占用邮箱/用户名返回统一文案 | E-2 | 无法枚举已注册用户 | ✅ **已落地**：`auth_service.py:363-369` 两种冲突合并为同一句 `"该邮箱或用户名已被使用，请更换后重试"`，且服务端日志才区分 `email_taken` / `username_taken`；登录侧同为统一失败（`:384-410`） |
| 0.9 | **`debug` 默认改为 `False`**；`.env.example:113` 同步；`echo` 拆成独立开关 `db_echo`（当前 `debug=True` 把 bcrypt 哈希与全部学习内容写进明文日志，见 A-19） | E-5 / A-19 | 生产日志无用户内容 | ✅ **已落地**：`config.py:509` `debug: bool = False`；SQL echo 已拆成**独立开关** `log_sql`（`config.py:494` 默认 `False`，`database.py:90` `"echo": settings.log_sql`，不再跟随 `debug`）；命名是 `log_sql` 而不是计划写的 `db_echo`。⚠️ **`.env.example` 未同步干净**：`:39-41` 已有 `# DEBUG=false` 的遗留说明，但文件**末尾 `:142` 仍是裸的 `DEBUG=true`**，照抄模板会把 dev 姿态打开（应用内**不**开 SQL 日志，所以不再是 A-19 那个泄露，但仍是模板与代码默认值的矛盾） |
| 0.10 | **`/ready` 端点 + 队列深度**；`/docs`、`/openapi.json` 生产关闭或加保护 | E-7 | 可判断服务是否可用 | ✅ **已落地（2026-09-14，本条由此收口）**：生产姿态下 `/docs`、`/openapi.json`、`/redoc` **根本不注册**（FastAPI 原生三个 `None`，404 与未知路径不可区分），判据**沿用既有的** `cfg.is_dev`（没有第二套"是不是生产"）；新增 **`/ready`**（200/503、两种状态**同一响应体形状**）。此前记的是"⏸ **未做**。`app/main.py` 全仓只有 `/health`（`:292`，返回 `{status, app}`）；`FastAPI(...)`（`:194-199`）**没有设 `docs_url` / `openapi_url` / `redoc_url`**，因此 `/docs`、`/openapi.json`、`/redoc` 在任何环境（含生产）都是**公开**的；没有任何队列深度端点"——**这段已过期**（旧句要点保留对照）。落地要点：`_schema_endpoint_kwargs()`（`main.py:239-245`）、模块级 `app` 改为工厂 `create_app(config=None)`（`main.py:486`，**此前根本没有工厂**）、`dump_openapi.py` 走**进程内** `app.openapi()` 因此在生产姿态下照旧可用；`/ready` 的判据**只有数据库**（一次**只读聚合查询**，不是 `SELECT 1`），队列深度**只报告、不决定状态码**，`/health` 保持**零依赖**（DB 挂 ⇒ `/health` 200 + `/ready` 503；`main.py:548-616`）。★ 改掉设计的那次实测：`task_runs` 的行是 **worker 接手时**才建的（只数 DB 的"pending"几乎恒为 0，是**假指标**），真实积压在 broker ⇒ 按 `*.{queue}.msg` 文件数计（与 kombu `Channel._size` 同口径，**排除** `*.pidbox.msg`），测不到时 `depth=None`（不是 0）。完整记录见**附录 BK.2** |
| 0.11 | **统一错误契约**：引入 `AppError(code, http_status, message)`，前端改按 `error_code` 分支（不再匹配中文，见 F-19）；停止用 `str(e)` 回传内部异常（H5） | E-10 | 后端改文案不破坏前端 | ✅ **后端侧已落地（2026-09-14，采用面 2/154 → 151/154）**：全仓 `raise HTTPException` **152 → 3**、`raise AppError` **2 → 151**（**AST** 实测）。**3 处存活的都是书面豁免**，全在 `app/api/auth.py`（`:103` / `:115` / `:227`），理由统一：必须带 `WWW-Authenticate: Bearer`，而 `ErrorHandlerMiddleware._error_response()` **不接受也不转发 `headers=`**（`middleware/error_handler.py:52-63`；路由层 `HTTPException` 走的是 `main.py:454` 转发了 `exc.headers` 的那个处理器）—— 豁免语法是紧邻 raise 的 `# error-contract: exempt — <理由>`。**错误码 80 个**（`core/app_error.py:40-212`，name == value、0 重复、0 未使用），**粒度规则**也写在表里（同一个 code 只要在"一个端点内"可区分即可，`app_error.py:53-56`）。守卫 `tests/test_error_contract_adoption.py`（516 行 / **16** 用例 / AST）实测账目：`app/api` **24** 个文件 / **3** 处 raise / **0** 违规 / **3** 条豁免；反空转有**合成源码**与**真源**两道证明；另有一个 diff 脚本对 **149** 个已迁移出口比对 `(status, message)` 多重集 ⇒ **零状态/文案漂移**。此前记的是"🟡 **契约设施已落地，采用面很窄** … `raise AppError` **只有 2 处**（`version_service.py:208/226`），而 `raise HTTPException` **有 152 处** … 所以"后端改文案不破坏前端"这条验收**尚未成立**"——**采用面那半已过期**（旧句要点保留对照）。⚠️ **仍未收口的一半是前端**：HEAD 上 `frontend/src/pages/TodayLearn.tsx:146` 与 `pages/Review.tsx:126` **仍是** `message.includes('每日上限')`；本轮把结构化的码交给了前端（`api/client.ts:314` 的 `ApiError.code`），两个页面的分流切换见**附录 BK.3 / BK.7** |
| 0.12 | **修 nginx 生产配置**：`client_max_body_size 500m`（当前默认 1MB → **所有 >1MB 上传必然 413**）、`proxy_buffering off`（当前 SSE 被缓冲 → 流式失效）、`gzip_vary on`、静态资源缓存头 + `index.html no-cache`、安全响应头 | F-1 / F-21 | Docker 部署下上传与流式可用 | ⛔ **按既定路线不适用**（容器化已放弃，见 `README.md:265-276` 的"Docker 部署未经验证"横幅与 `docs/sqlite-single-writer.md`）。⚠️ **但文件是真的、内容是对的**：`frontend/nginx.conf:11` `client_max_body_size 500m`、`:50` `proxy_buffering off`、`:71` `gzip_vary on`、`:24-33` 静态资源 `immutable` + `index.html no-cache`、`:74-76` 安全头，且 CI 有回归守卫（`.github/workflows/ci.yml:351-366`）。**验收条件"Docker 部署下可用"无法在本项目的运行路径上验证**（这一轮也没有验证过）—— 所以这条是"不适用"，不是"已验证" |
| 0.13 | **加 `frontend/.dockerignore`**（`node_modules`、`dist`）；`Dockerfile` 改 `npm ci` | F-4 | 镜像可在 Linux/CI 上构建 | ⛔ **按既定路线不适用**，且**前半已落地、后半未做**：`frontend/.dockerignore` 存在（22 行，排除 `node_modules` / `dist` / `.git` / `*.log` 等），CI 有守卫（`ci.yml:368-374`）；但 `frontend/Dockerfile:16` **仍是 `RUN npm install`**，不是计划要的 `npm ci`。整体判 ⛔ 是因为"镜像可在 Linux/CI 上构建"这条验收**在本项目的实际部署路径下不被检验** |
| 0.14 | **加 ErrorBoundary**（`main.tsx` 全局 + 路由级 `key={pathname}`）；`renderMarkdown` 调用包 `useMemo` 与 try/catch 兜底 | F-2 | 单条坏数据不再整站白屏 | ✅ **已落地**：全局 `main.tsx:49-55`；路由级 `App.tsx:116-150` 用 `<ErrorBoundary resetKey={location.pathname}>`（`key` 语义的等价实现）；`utils/markdown.ts:265-288` 的 `renderMarkdown` 整体 try/catch，失败降级为**转义后的纯文本**；流式两处（`NoteAskPanel.tsx:71`、`QA.tsx` + `hooks/useStreamAnswer.ts`）已用 `useMemo` / 节流。⚠️ **未完全覆盖**：`LearningAssessment.tsx`（`:486/498/508/598/621/627/646`）与 `NoteDetail.tsx:160/163` **仍在渲染体里直接调用** `renderMarkdown(...)`（每渲染一次即重算）。有 try/catch 兜底，所以 F-2 的"整站白屏"验收成立；但"包 `useMemo`"这一半**没有全覆盖** |
| 0.15 | **`QuestionSets` 分页循环加硬上限**：`page <= MAX_PAGES` + `if (data.items.length === 0) break` | F-3 | 不再无限请求后端 | ✅ **已落地**：`pages/QuestionSets.tsx:75` `MAX_PAGES = 100`、`:82` `while (page <= MAX_PAGES)`、`:89` 空页即停、`:91` 取够 `total` 即停，`:96-101` 截断时 `console.warn` 并如实改 `total` |
| 0.16 | **`DailyMaterials` 轮询加清理**（`pollTimerRef` + effect cleanup 的 `cancelled` 标志） | F-5 | 卸载后不再发请求 | ✅ **已落地**：`pages/DailyMaterials.tsx:144` `pollTimerRef`、`:146` `unmountedRef`（即计划写的 `cancelled` 标志）、`:149-154` `stopPolling()`、`:156-162` 卸载 effect 同时置标志并清定时器（`Upload.tsx:90-96` 早已是同款实现） |
| 0.17 | **补 CSS `.status-learning` / `.status-learning-failed` / `.status-archived`**，统一 `statusClass()` 函数（当前四个页面渲染成无样式裸文本，见 F-13） | F-13 | 状态样式全站一致 | ✅ **已落地**：`utils/labels.ts:113-115` 三个类名 + `:119` `statusClass()` 单一出口；`styles/components.css:262-264` 三条规则齐备；6 个消费页面全部改走 `statusClass()`（`DailyMaterials` / `Dashboard` / `NotesList` / `AddNotesPanel` / `ProjectNotesList` / `NoteDetailHeader`），`projects/helpers.ts:29-30` 记录了此处曾经的绕过表 |

**阶段 0 的价值**：不改变架构，但把"随时可能丢数据/被爆破/静默失效"的风险按下去，
为后续重构赢得时间。

> **2026-09-14 补记**：本阶段 17 条的**逐条状态此前从未存在过**（表头就没有 `状态` 列）。
> 上表是按代码逐条核对后补的，**当时的结论是：✅ 11 / 🟡 3（0.2、0.7、0.11）/ ⏸ 1（0.10），
> 共 15 条；另 2 条（0.12、0.13）判"⛔ 按既定路线不适用"**（旧句保留作对照）。
> **同日稍后，0.2 / 0.10 / 0.11 三条已经收口**（见**附录 BK**）⇒
> **现结论：✅ 14 / 🟡 1（0.7）/ ⏸ 0 / ⛔ 2** —— ✅ 新增的三条是
> 0.2（15 个脚本搬出 `backend/` 根）、0.10（`/ready` + 三条 schema 路由生产姿态不注册）、
> 0.11（采用面 2/154 → 151/154）。**0.7 那半没有变**（限流有、锁定无），
> **0.11 那半已经变了**（`AppError` 有、采用无 → 采用面 151/154；
> 前端两页的分流切换是另一件事，见 **BK.7**）。0.10 那一格原有的
> "⚠️ 与 0.11 是两件事"仍然成立。0.12 / 0.13 仍判"⛔ 按既定路线不适用"
> （⚠️ 0.13 实际是"不适用 + 前半已落地"的复合状态，见该行）。
> 派工依据见**附录 BJ.1**，三条的收口证据见**附录 BK**。

### 阶段 1 · 换地基（2 周）—— 手术刀 1

> ⚠️ **本表原为 PostgreSQL 路线设计，其中 1.1–1.5 依赖 Docker/PG，在本项目
> 已定路线（SQLite 单机，见 `docs/sqlite-single-writer.md`）下**不执行**。
> 与存储无关的 1.6–1.14 已通过 SQLite 路线落地，状态逐行标注如下。
> 将来若迁 PG，本表依然有效。

| # | 动作 | 验收 | 状态 |
|---|---|---|---|
| 1.1 | 引入 PostgreSQL：`docker-compose` 启用 postgres，`DATABASE_URL` 切 `postgresql+asyncpg://` | 全部测试在 PG 上通过 | ⛔ 不执行（无 Docker）——**2026-09-14 补记**：路线依据见 `docs/sqlite-single-writer.md` 与 `README.md:122`（"曾因磁盘空间不足明确放弃容器化与 PG/Redis"）；`requirements.txt:39` 的 `asyncpg` 已注释、`:44-45` 的 `redis` 亦为注释。**没有任何东西存活下来**：`database.py` 的 `_sqlite()` 分支是运行期的唯一分支 |
| 1.2 | **删除手写迁移**：移除 `_migrate_sqlite()` / `_rebuild_dangling_tables()`（约 -400 行）；`init_db()` 只做连通性检查，**不再建表、不再改 schema** | 启动不再改 schema | ⛔ 不执行（手写迁移是 SQLite 路线的唯一通道）。**原始关切里"启动零破坏性"这一半已达成**：重建函数已移出启动路径（需显式闸门）。**仍成立的部分**：`_migrate_sqlite()`（`database.py:437`，`init_db()` 在 `:291` 调用它）**依旧存在、依旧在每个进程启动时执行**，Schema 演进**只有这一条通道**（Alembic 见 1.3）；所以计划写的"约 -400 行删除"在 SQLite 路线下**不是未做，是不该做** |
| 1.3 | ⚠️ **Alembic 从零重建**。现有迁移链（001→010）**从未生效过**（见 §2.6 M-1） | 空库 → `alembic upgrade head` → 全表 + `alembic_version` | ⛔ 不执行（无 PG）。**但 M-1 的结论仍成立，且本轮逐条复核为"从未运行过"**（2026-09-14，三条独立证据）：① 真库 26 张表里**没有 `alembic_version`**（`sqlite_master` 只读查询）；② `backend/alembic.ini:6` 的 `sqlalchemy.url` 仍是 `postgresql+asyncpg://engram:…`，与 SQLite 路线**不匹配**，`alembic/env.py:35-39` 用同步 `engine_from_config` 读它；③ **当前 Python 环境里 `alembic` 包根本没装**（`import alembic` → `ModuleNotFoundError`，尽管 `requirements.txt:26` 声明了它）。另：`versions/` 已有 **011 个**迁移文件（比本行写的 001→010 多一个 `011_refresh_tokens.py`，即 6.3 那个，当时也**只单独跑过它一个**）。**结论：迁移链至今不是可用路径，schema 继续由手写迁移维持** |
| 1.4 | **数据迁移脚本**：SQLite → PG（含 chroma → pgvector 的向量搬运） | 迁移后行数/内容哈希一致；项目归属不丢 | ⛔ 不执行（无 PG）。**"向量搬运"这件事以另一种方式做过**：向量已从 90+ Chroma collection 搬进 `chunks` 表（608 行、单一 `bge-m3`/1024 维、100% 可检索，附录 O/P）；行数/内容哈希一致这条验收**没有对象**（没有目标库） |
| 1.5 | 引入 Redis：Celery broker/backend 切 Redis | 多 worker 并发跑任务且不 OOM | ⛔ 不执行（无 Redis）。**但"每 worker 加载一份 2.3GB 嵌入模型"的约束已写进 `sqlite-single-writer.md`**（`-c 1`）；限流器刻意保留**进程内**实现（`middleware/rate_limit.py:15-16` 与 `services/llm/rate_limit.py` 都写明"将来水平扩展时再换 Redis，接口不变"） |
| 1.6 | **任务可靠性**：全局 `task_reject_on_worker_lost=True`、`task_time_limit`、`soft_time_limit` | 杀 worker → 任务自动重投 | ✅ 已落地（附录 F.2；文件 broker 无 visibility timeout，改用 DB 心跳自愈补偿） |
| 1.7 | 新增 `task_runs` 表 + 进度上报 + `GET /api/tasks/{id}` + 取消接口 | UI 显示真实百分比 | ✅ 已落地（附录 F.2，含 IDOR 校验与诚实的 `terminated=false`） |
| 1.8 | **僵尸任务自愈**：Beat 定时扫描 `heartbeat_at` 超时的 `task_runs` | worker 崩溃后笔记不再永久卡住 | ✅ 已落地（Beat 每 5 分钟） |
| 1.9 | **worker 启动时校验 schema** | worker 独立启动也能正常工作或明确报错 | ✅ 已落地（`_ensure_worker_schema()`） |
| 1.10 | **修正 Celery broker 目录推导** | broker 目录与 `config.py:397` 保持一致 | ✅ 已落地（收敛为 `get_celery_broker_dir()`） |
| 1.11 | 新增 `vault_files` 表 + 一致性校验任务（比对磁盘与 DB 的 sha256） | 可检测并修复发散 | ✅ **已落地**（附录 I）：`vault_audit_service` + `verify_vault.py`（14 用例）。**未建 `vault_files` 表** —— 改为每次扫盘，避免引入第三份需同步的真相源；且**只报告不修改**（自动"修复"会把误判变成不可逆删除） |
| 1.12 | ~~**消除双写**：统一"先写文件成功 → 再 commit DB → 失败则补偿回滚"的顺序~~ | 崩溃注入测试下无分歧 | ⛔ **不适用（原表述的两个具体机制已实测证伪，条目随之撤回）** —— 见 §2.6 M-10 修正框与附录 H.3。**同一后果的真实载体已修**：`purge_note` 的删除失败此前只记 warning、DB 记录照删，现已改为 `storage_service.delete_file` 有界重试（3 次 / 50ms），持续失败仍删 DB 记录（不让用户卡死）。⚠️ 这一格**不是**「按原文做完」—— 原文要消除的那个"双写顺序"经复核并不存在 |
| 1.13 | ~~**修复物理删除笔记的 FK 违约**（见 §2.6 M-4）~~ | 用过"拓展卡片"后仍能 purge | ⛔ **不适用（实测证伪：该缺陷不存在）** —— 见 §2.6 M-4 修正框（该节标题亦已划掉）与附录 H。已补 `tests/test_purge_note_integrity.py`（5 用例）锁住正确行为，防止将来给 UPDATE 加 `note_id` 限定而真的引入它。⚠️ 这一格**不是**「已完成修复」（本就没有缺陷可修） |
| 1.14 | **修复 `card_relations` 唯一索引**（见 §2.6 M-3） | 索引确定存在 | ✅ 已落地（附录 D；去重口径统一为 5 列） |

**1.2′ 补充（SQLite 路线下的"启动零破坏性"）**：
`_rebuild_dangling_tables()` 已移出启动路径（破坏性变更需显式闸门），
但**它此后没有任何调用入口** —— 本轮实测因此导致 `review_logs.quiz_id`
的可空迁移从未执行。已新增 `scripts/reconcile_schema.py` 作为显式、
自动备份、逐项校验、幂等的入口（见附录 G.4）。

**阶段 1 之后**：D-1 / D-2 / D-7 / D-8 / D-11 / M-1 / M-4 / M-6 / M-12 消除。
（在 SQLite 路线下：D-1/D-2/D-7/D-11/M-6 已消除；M-1 已定性；
M-4 与 1.13 的复核结论见附录 H（M-4 实测证伪、1.13 随之撤回）—— 原句写的"仍未做"**已过期**（2026-09-14 更正）。）

### 阶段 2 · 重建检索层（2 周）—— 手术刀 3

> ⚠️ **本表原为 PostgreSQL 路线设计。** 在本项目已定路线
> （SQLite 单机，见 `docs/sqlite-single-writer.md`）下：
> **2.2（`chunks` 表 + pgvector）、2.5（`pg_bigm`）不执行** —— 它们依赖 PG 扩展，
> 在 SQLite 上没有对应物。SQLite 路线的等价物见下方「阶段 2′ 的 SQLite 等价项」。
> 其余各项与存储无关，已按 SQLite 路线落地，状态逐行标注。

| # | 动作 | 验收 | 状态 |
|---|---|---|---|
| 2.1 | **统一分块**：`markdown_segmenter` 成为唯一实现；删除 `cleaning_service.split_into_chunks`（-250 行） | 同一文档全链路同一套 chunk | ✅ **已落地**（附录 M）：`segment_with_offsets` 成为唯一分块器，`split_into_chunks` 已删除（**-253 行**，实测零调用方）；两个调用点均改走同一函数、同一 `chunk_size` |
| 2.7 | **引用可回跳**：chunk 存 `char_start/char_end/heading_path`；前端点击引用 → 定位并高亮 | 引用能跳到段落 | ✅ **已落地**（附录 S）：后端贯通 `AnswerSource`（含 OpenAPI 契约）；前端 `citationJump.ts` + QA 页跳转 + `NoteDetail` 定位高亮。**含必要的失败退化**（见 S.4） |
| 2.2 | 新增 `chunks` 表 + `pgvector` 列（HNSW, `vector_cosine_ops`） + `tsvector` 列 | 一次迁移建好 | ⛔ 不执行（无 PG） |
| 2.3 | 索引源改为**清洗后的原文 Markdown**（不再索引卡片） | 检索命中原文段落 | ✅ **已落地**（附录 R）：两路统一跑 chunk 语料。生产路径实测严格 Recall@5 **59.74%**（旧卡片语料 37.52%） |
| 2.4 | 删除 Chroma 依赖与 90+ collection 目录；删除跨 collection 遍历逻辑（`embedding_tasks.py:196-262`） | 查询从 N 次降到 1 次 SQL | ✅ **检索侧已彻底删除**（附录 R）：`_search_vectors_async` + `search_vectors` 任务 + `_collection_matches_current_model`，共 **−169 行**。**Chroma 仍被清洗去重使用**，故依赖与目录保留（见 R.4） |
| 2.5 | 中文全文检索用 `pg_bigm`（或 `zhparser`），替换纯 Python BM25 | 中文召回质量可测 | ⛔ 不执行（无 PG）。SQLite 等价物是 **FTS5**，见下方 |
| 2.6 | **删除 n-gram 通道**；RRF 改为加权融合（向量 / BM25 可配权重） | 少一路噪声 | ✅ **已落地**（附录 J + P.4）：通道已删；**加权融合已落地并经实测定参**（k=1 / w=0.65 / 池 20 → 严格 Recall@5 60.87%，原等权 k=60 为 57.37%） |
| 2.8 | **重写 RAG 提示词**：强制"仅依据给定资料"；无据则明确回答"资料中没有"；要求逐条标注引用编号 | 无据问题不再被编造 | ✅ 已落地（提交 `5da6d8a`） |
| 2.9 | **检索质量评测集**：造 50 条 (问题, 期望命中 chunk) 的离线评测，纳入 CI | Recall@5 / MRR 有基线 | ✅ **已落地**（附录 K）：用真库 **1058 条**真实 (问题, 原文真值) 建集，规模远超计划的 50 条；`scripts/eval_retrieval.py`。**刻意不纳入 CI**（理由见 K.5） |
| 2.10 | 删除 `_kb_cache` 模块级无界缓存 | 内存不随用户增长 | ✅ **已落地**（附录 J）：缓存与失效入口一并删除，语料改为按需查询 |

#### 阶段 2′ 的 SQLite 等价项（替代 2.2 / 2.5）

| # | 动作 | 目的 | 状态 |
|---|---|---|---|
| 2.2′ | `chunks` 表（纯 SQLite，无扩展）承载 chunk 文本 + `char_start/char_end/heading_path` + 向量 BLOB | 支撑 2.7 回跳与 2.3 语料切换 | ✅ **已落地**（附录 O/P）：表已建、608 个 chunk 与**全部向量**落库，单一模型 `bge-m3`、单一维度 1024、**100% 可检索**。三条验收标准全达成 |
| 2.5′ | **FTS5** 全文索引（SQLite 内置）替换纯 Python BM25 | 免维护索引；这是 A-5 在 SQLite 路线下的正解 | ✅ **已落地**（附录 T）：FTS5 + bigram 预切词，生产路径实测严格 Recall@5 **60.21%**（Python BM25 59.74%），无结果比例 **0.00%** |
| 2.4′ | 评估 `sqlite-vec` 扩展替代 Chroma 的多 collection | 消除"每篇笔记一个 collection、检索要遍历全部" | 🟡 **B 半已用一次 SQL 取代遍历 N 个 collection**（附录 P）；`sqlite-vec` 本身未引入（当前规模下纯 Python 点积足够，接口已预留） |

> **2.2′ 的验收标准**（按附录 N 扩充）：
> 1. ✅ `chunks` 表按 `note_id` 落库 chunk 文本 + `char_start/char_end/heading_path`
> 2. ✅ **全部向量由同一个模型生成、维度一致、100% 可检索**
>    （608/608 为 `bge-m3`/1024 维；此前 1190 条里只有 681 条可取到）
> 3. ✅ 落库内容满足不变量 `text[char_start:char_end] == content`
>    （`--verify` 断言，且**写入前**先自证 —— 错误的偏移宁可拒绝落库）

#### 🔴 但 B 半顺带测出一个更严重的结论：**RRF 融合比任一单通道都差**

同一批 608 chunk 语料、同一评测集（1058 条），严格 Recall@5：

| 候选池 | 向量 | BM25 | **RRF 融合** | 融合 − 最好单通道 |
|---|---|---|---|---|
| 5（线上口径） | 48.39% | 59.74% | **32.04%** | **−27.69%** |
| 10 | 48.39% | 59.74% | 31.38% | −28.36% |
| 20 | 48.39% | 59.74% | 30.34% | −29.40% |

候选池越大越差，说明**不是候选池口径问题**。只算"只要某路命中就算命中"的
上界是 `(488+24+144)/1058 = 62.0%`，而融合只做到 32% —— 融合把
**本已命中的结果挤掉了**。

机制：RRF 的 `1/(k+rank)` 在 `k=60` 时，第 1 名与第 5 名的权重只差
`1/61` vs `1/65`（**4%**）。于是"某路第 1 名"与"另一路第 5 名"几乎等价，
分数主要由**两路是否同时出现**决定 —— RRF 实际在奖励**共识**而非相关性。
两路强弱悬殊时（此处 59.74% vs 48.39%），共识偏向等于**把强通道拉向弱通道**。

这是 §2.6 遗留的"RRF 改为加权融合"的直接依据：
**加权不再是"锦上添花的调参"，而是修一个正在造成 −28% 的缺陷。**

详见附录 P.4。

#### 2.1 为什么不是"删掉一个函数"那么简单（已按此判断执行完毕）

原表把它写成一处删除（-250 行）。动手前实测发现**两个分块器的职责不同、
不能直接砍掉任一个**：

| | `cleaning_service.split_into_chunks`（**已删除**） | `markdown_segmenter` |
|---|---|---|
| 产出 | `{index, content, start_line, end_line, heading_context}` | 纯文本段列表 |
| 用途 | **检索 chunk**（`clean_tasks` → 嵌入 → Chroma） | **LLM 抽取的填充边界**（`understanding_service`） |
| 特性 | 段落级、带 overlap、带标题路径与行号 | 结构块原子性（不切破表格/代码块/列表） |

所以真正的目标不是"删掉一个"，而是**一个同时具备两侧能力的 chunker**。
`segment_with_offsets()` 就是这个形态（附录 M 已接线并删除旧实现）：
结构块原子性 + 字符偏移 + 标题路径 + 行号 + 可选块级 overlap。

**顺序约束（已按实测修正两次）**：`2.9 → 2.1 → 2.2′ → 2.3/2.4 → 2.7`。

第一次修正：原约束是 `2.1 → 2.7 → 2.3`。执行到 2.7 时实测发现**它做不了**：
位置信息在检索链路四层里丢了三层，且 BM25 通道的卡片语料**根本没有原文位置**
（见附录 N.4）。因此 2.7 的前置是 **2.2′（`chunks` 一等实体）**，不是 2.1。

#### 当前状态与"下一步"

| 项 | 状态 | 说明 |
|---|---|---|
| 2.9 评测基线 | ✅ | 1058 条真实真值（附录 K） |
| 2.1 统一分块 | ✅ | `split_into_chunks` 已删（附录 M） |
| 2.2′ 表 + 偏移落库 | ✅ | 608 个 chunk（附录 O） |
| 2.2′ 向量 | ✅ | 608/608 单一模型 `bge-m3`（附录 P） |
| 2.4′ 一次 SQL 取代 N collection | ✅ | `chunk_search_service`（附录 P） |
| 2.6 加权 RRF | ✅ | k=1 / w=0.65 / 池 20（附录 P.4，已实测定参并锁测试） |
| 2.3 语料切换 | ✅ | 两路统一 chunk 语料（附录 R） |
| 2.4 删除跨 collection 遍历 | ✅ | −169 行（附录 R） |
| 2.7 引用回跳 | ✅ | 四层贯通 + 前端定位高亮（附录 S） |
| 2.5′ FTS5 全文索引 | ✅ | 附录 T |
| **Chroma 彻底移除** | ✅ **已完成**（附录 U） | `VectorStore` 删除（−224 行）、`chromadb` 移出依赖；**顺带发现并修掉一个真实回归**：删除笔记会撞 `chunks` 外键 |
| 行为测试补全（Q.3） | ⬜ | `embed_chunks.py`；`citationJump.ts` 无前端测试 |

**阶段 2（含 2′ 等价项）的检索层重建已完成**：单一分块器、单一语料、
单一模型向量、一次 SQL、已定标加权融合、FTS5 内置词法索引。

#### 一个尚未解决的方法论问题：向量通道的语料是**碎的**

`scripts/eval_retrieval.py` 目前只测 BM25（词法）通道。但比"没测"更严重的问题
是：**向量语料本身处于不可用的分裂状态**。实测（2026-09-11，见附录 N）：

| collection 标记的模型 | collection 数 | 向量数 | 维度 | 当前运行时能否检索 |
|---|---|---|---|---|
| `BAAI/bge-small-zh-v1.5` | 11 | 509 | 512 | ❌ 维度不匹配 |
| **（无 `embedding_model` 记录）** | 9 | 477 | 1024 | ⚠️ 靠异常兜底 |
| `BAAI/bge-m3` | 4 | 204 | 1024 | ✅ |

运行时只加载一个模型（本机是 `bge-m3`，1024 维），因此
**1190 条向量里只有 681 条（57%）可检索，509 条（43%）永久取不到**。

后果：向量通道的数字**不能与词法通道直接比较** —— 它的语料只覆盖了
评测集的一部分。必须同时报"覆盖率上限"才能读懂（见附录 N.3）。

**这一条改变了 2.3/2.4 的性质**：它们不只是"把语料换成原文"，
而是"把碎成三种模型的向量索引重建成一个一致的索引"。
因此 2.3/2.4 的验收标准必须包含：**重建后所有向量由同一模型生成、
全部可检索**。否则换语料只是把一个坏索引换成另一个坏索引。

**阶段 2 之后**：A-1 / A-2 / A-4 / A-5 / A-6 / A-7 / A-9 / A-14 / D-9 / D-10 全部消除。
**产品核心承诺（"基于你的资料回答，可追溯"）首次成立。**

### 阶段 3 · 重写学习核心（3 周）—— 手术刀 2 + 4

> **这是本项目最有价值、也最被低估的一段。** 建议单独排期，不要与其他阶段并行。
>
> ⚠️ **本表原无状态列，2026-09-11 实测核对后补入**（附录 E/F/G 记录过部分
> 已做的工作，但表格本身从未更新，读者无法判断进度）。核对结论是
> **已完成的多是"表结构"、未完成的多是"算法"** —— 这正是本项目一贯的形态：
> schema 先行，行为留空。
>
> 📌 **表内所有"194 条"与"真库 FSRS 记录数为 0"的计数为 2026-09-11 快照。**
> **2026-09-14 复核**：真库 `review_logs` 已是 **195 条**，其中 **1 条是 FSRS 时期
> 产生的真实复习记录**（`rating` / `self_rating` 非空、`grading_method='self_rating'`、
> `item_type='card'`、`review_at='2026-09-14 08:28:25'`，对应 `review_states` 行
> `stability` 非空）—— "样本还没开始积累"变成了"样本刚开始积累"，
> 但 **n=1 不足以支撑 3.10 / 3.11 / 3.14 的任何调参或拟合**，三条的结论不变。
> 行数快照同见附录 BJ.4。

| # | 动作 | 验收 | 状态（2026-09-11 核对） |
|---|---|---|---|
| 3.1 | 新增 `review_states` 表；把 `interval/repetition/EF/next_review_at` 从 `quiz_items` 迁出 | 数据无损迁移 | ✅ `review_states` 2241 行；键为 `(user_id, item_type, item_id)` |
| 3.2 | `review_logs` 重建为不可变事件流（加 `rating`、`predicted_retention`、`item_type`） | 历史日志迁移保留 | ✅ **已落地**（随 3.6 一起，见附录 W.8）：三列均为纯加列；194 行历史 `item_type` 全部回填 `quiz`；`rating`/`predicted_retention` 保持 NULL（SM-2 时期没有这两个量，填数就是发明） |
| 3.3 | **删除字符 n-gram 判分**（`_score_short_answer` 整段移除） | 随机文本不再得高分 | ✅ 已删除；改用 Levenshtein 比率 |
| 3.4 | **引入用户自评四档**（Again/Hard/Good/Easy）作为主评分来源 | UI 有四个按钮 | 🟡 字段与 API 已就位（`self_rating`），且 3.6 已把四档**原样**用作 FSRS 的 rating（`{0,3,4,5} → {Again,Hard,Good,Easy}`）。但 **194 条历史日志中 0 条有值** —— 无人用过，效果仍未验证。**2026-09-14 复核更新**：真库现在是 **195 条日志，其中 1 条 `self_rating` / `rating` 非空**（`grading_method='self_rating'`、`item_type='card'`、`review_at='2026-09-14 08:28:25'`）—— **第一次真实自评已经发生**，"无人用过"这句话从此不再成立；但 n=1 仍然说明不了任何效果，验收（"效果可验证"）依旧未达成 |
| 3.5 | **简答题 LLM 语义判分**：输出 `verdict + missing_points + misconceptions` | 答错时给出具体缺失点 | ✅ **已落地**（附录 V）：`grade_short_answer` + 三档 verdict + 置信度门槛；`review_logs.grading_detail` 存结构化明细。**默认关闭、显式请求**（含实测理由） |
| 3.6 | **把 SM-2 换成 FSRS**（含 `stability`/`difficulty` 建模） | 同等保持率下复习量下降 | ✅ **已落地**（附录 W）：`fsrs_service`（FSRS-5，19 个公开默认参数）+ `scheduler_service` 门面；`review_states.stability/difficulty` 新列；旧行按 `S := interval` 接管不清零；`review_scheduler` 可回退 SM-2。62 个新测试。**复习量下降这一条尚无实测** —— 真库还没有一行 FSRS 产生的记录 |
| 3.7 | **修正 `next_review_at` 基准**：以计划到期日为基准（非 now）；加 fuzz | 间隔不被提前复习缩短 | ✅ **已落地**（附录 X）：基准问题由 3.6 结构性解决（间隔由 S 解出，不再 `interval * EF`）；本轮补上 **fuzz**（同批卡片摊开）与 **到期时刻对齐**（锚到业务时区凌晨 4 点）。另修掉一个更隐蔽的错误：`elapsed` 从**连续小时差**改为**业务日差**（23:00→次日 08:00 曾被判成"同日"而走短时公式） |
| 3.8 | **leech 检测**：`lapses >= 8` → 标记并要求重写卡片 | 顽固卡片被识别 | 🟡 有 `lapses` 字段与相关代码；**实际效果未见度量**。3.6 之后 `lapses` 的口径未变（仍是"任何 quality<3 都 +1"），而 FSRS 的 `state` 已能区分"没学会"与"学过又忘了" |
| 3.9 | **修掉 `mastery` 公式**：基于 FSRS `retrievability` 的"当前可回忆概率" | 3 个月未复习的卡片掌握度下降 | ✅ **已落地**（附录 Y）：曲线由指数 `2^(-t/S)` 换成 **FSRS 幂律**，S 取自 `review_states.stability`（缺失时按 `S := interval_days` 换算，与调度器同一条规则）；顺带修掉"卡片级复习永远不计入成功率"的查询缺陷。真库重算：**1183 张卡从全 0 变成 132 张有分** |
| 3.10 | **新卡与复习卡分开限额**（`new_per_day` / `review_per_day` 可配） | 到期队列收敛 | ⏸ **未做（2026-09-14 逐条核对确认）**：全仓 `grep` `new_per_day\|review_per_day` **零命中** —— 两个配置项不存在。现在**只有一个**限额 `config.daily_review_limit` → `review_service.DAILY_REVIEW_LIMIT`（`review_service.py:43`，类型侧单一出口 `schemas/review.py:308` 的 `daily_limit: int = 10`），且它是**新卡与复习卡合用**的一个平数。3.6 之后 `R` 与 `state` 都在库里，两个口径都可直接计数 —— 卡的是**产品参数**（"新卡多少张合适"要实测），不是技术前提 |
| 3.11 | **薄弱点排序加时间衰减** + overdue 权重 | 老错误不再霸榜 | ⏸ **未做（2026-09-14 核对完毕，此前写的是"未核对"）**：`review_service.py:111-149` 的排序只有两个键 —— `next_review_at ASC NULLS FIRST`，然后 `error_count DESC`（`error_subq` 对 `ReviewLog.is_correct IS FALSE` 求和）。**没有时间衰减，也没有 overdue 权重**：`error_count` 是**该卡片历次错误的总和**，没有任何时间窗或半衰期，所以一条一年前的错误与昨天的错误权重完全相同 —— 这正是"老错误霸榜"的机制本身。全仓 `grep` `time_decay` 零命中。（另：`learning_metrics_service.py:342-365` 有 `overdue` **计数**，但那是度量层，不参与排序） |
| 3.12 | **卡片可直接复习**（`item_type='card'`），不再依赖是否出过题 | 死卡片消失 | ✅ `ITEM_TYPE_CARD` / `ITEM_TYPE_QUIZ` 均已存在；3.6 起两条复习路径**共用同一个调度入口**。**前端在 2026-09-11 才补上**（附录 AA）：后端接口早已齐备，但前端一直没有调用方，用户一次也没法用 |
| 3.13 | **复习页显示原文上下文**：答错时一键展开 `source_quote` 与原文段落 | 答错能立刻回看原文 | ✅ **已落地**（附录 Z）：`components/quiz/SourceContext.tsx` 懒加载卡片 `source_text`，答错后可一键展开并跳回笔记原文。真库实测：191 张被复习过的卡片 **100%** 有 `source_text`（全库覆盖率 99.8%），所以这个功能有真实内容可显示 |
| 3.14 | **度量层**：保持率曲线、校准曲线、lapse 分布、FSRS 参数拟合报表 | 用户能看到"我 30 天保持率 87%" | 🟡 已落地（附录 G），但**返回 `insufficient_data=true`**。3.6 起 `rating` 与 `predicted_retention` 开始积累，但真库当前 FSRS 记录数为 **0** —— 诚实但暂时无用 |

**阶段 3 之后**：L-1 … L-8 全部消除。
**这是产品从"看起来很酷的 demo"变成"真的能帮助记忆的工具"的分水岭。**

#### 阶段 3 的依赖关系（决定先做哪一项）

核对后发现这几项**不是并列**的：

```
3.4 用户自评（收集标签）──┬──→ 3.6 FSRS（需要评分数据才拟合得出）
                          └──→ 3.14 度量层（需要配对数据才画得出曲线）
3.6 FSRS ──→ 3.9 mastery（retrievability 依赖 FSRS）
3.6 FSRS ──→ 3.10 分开限额（需要到期预测）
3.6 FSRS ──→ 3.14 拟合（需要 `rating` + `predicted_retention` 的原始数据）
```

**3.6 已完成**（附录 W），因此 3.9 / 3.10 / 3.14 的**技术前提**都已具备。
但它们现在都卡在同一个非技术前提上：**真库里还没有任何 FSRS 复习记录**
（194 条历史日志全是 SM-2 时期，`rating` / `predicted_retention` 全为 NULL）。
3.9 可以立刻做（换公式不需要数据），3.10 / 3.14 需要真实使用。
**先做 3.7 的 fuzz**（不需要数据、立刻影响体验），再做 3.9。

**3.7 也已完成**（附录 X），**3.9 也已完成**（附录 Y），
**3.13 与前端接线也已完成**（附录 Z），**卡片复习 UI 也已完成**（附录 AA）。
**阶段 3 里不需要真实使用数据的项已经全部做完**：

| 项 | 卡在哪 |
|---|---|
| 3.10 分开限额 | 技术前提已具备（`S`/`R`/`state` 都在库里），但"新卡多少张合适"要靠实测调 |
| 3.11 薄弱点排序加时间衰减 | 同上，排序权重需要真实错误分布 |
| 3.14 校准曲线 / 参数拟合 | 真库 FSRS 记录数为 **0**，样本还没开始积累 |
| **前端测试框架**（AA.9） | 不需要数据 —— 但它是新的基础设施，不属于阶段 3 原表 |

**因此下一步应当离开阶段 3**。按第七部分的路线图，不需要数据、也不依赖
新基础设施的候选有两类：

1. **阶段 4 的一部分（LLM 网关与成本治理）** —— 其中 4.2「`llm_calls` 记账表」
   与 4.8「抽取幂等」都不需要用户数据即可设计与验证；
2. **文档与对外描述的一致性**（§2.1 S-4）：`README.md` / `docs/architecture.md`
   里关于 Vault 目录、端口、行数、alembic 的描述仍是过时的，
   `docs/decisions.md` 尚未转为只读归档。

⚠️ 无论选哪条，都建议先补**前端测试框架**：附录 Z 与 AA 这两轮改的都是界面，
而验证手段只有 `tsc` + `eslint` + `build` 三道静态检查 ——
"交互正确性未经运行时验证"这个缺口会随着每次改动继续扩大。
→ ✅ **已完成**（附录 AB，2026-09-11）：Vitest + Testing Library + jsdom，
**50 个用例**，并接进 CI 作为**阻断性**步骤。

**因此现在的下一步**（截至附录 AG）：

1. **Playwright 端到端**（AB.8）—— 前端测试只到组件/页面级，
   而"登录 → 导入 → 复习 → 引用回跳"这条全链路正是历史缺陷最集中的地方。
   ⚠️ 需要下载浏览器二进制（数百 MB），**需要先确认磁盘与是否允许**
   （实测 D 盘可用空间已从 28.7 GB 降到 **10.4 GB**，且 `backend/data/models`
   的 BGE-M3 占 4.5 GB 不能删）；
2. **文档与对外描述的一致性**（§2.1 S-4）：`README.md` / `docs/architecture.md`
   里关于 Vault 目录、端口、行数、alembic 的描述仍是过时的，
   `docs/decisions.md` 尚未转为只读归档 —— **不需要任何数据或新设施**；
3. **`llm_calls` / `llm_cache` 的清理定时任务**：`purge_expired()` 与
   `llm_call_retention_days` 都写好了，但**没有任何定时任务真的调用它们**，
   长期运行下两张表会持续增长（见 AF.10）；
4. **4.10 `debug` 开关解耦**：拆成 `app_env` / `log_sql` / `llm_provider`；
5. **消费 `prompt_version` 的报表**（4.6 的下游）：现在能按版本分组，
   但没有任何界面/接口展示"哪一版提示词产出的卡片复习表现更好"（属阶段 6）。

✅ 已完成：3.5 / 3.6 / 3.7 / 3.9 / 3.12（含前端）/ 3.13 / 3.2 /
**阶段 4 全部条目**：4.1（含收尾）/ 4.2 / 4.3 / 4.4 / 4.5 / 4.6 / 4.7 / 4.8 /
4.9 / 4.10 / 4.11。
⏸ 需要真实使用数据：3.10 / 3.11 / 3.14。

**阶段 4 剩下的项**：无。结构化输出校验（4.1 原文的最后一条）已在附录 AN 落地。

**下一阶段（阶段 5 前端）的真实状态**（本轮逐项核对代码后更新，见附录 AO.5）：

| 状态 | 项 |
|---|---|
| ✅ 已完成（此前的止血/前端轮次） | 5.4 路由级懒加载、5.7 真 404、5.8 三态组件 + Toast、5.11 任务进度 UI（本轮补） |
| ✅ 已完成 | 5.12 复习 UI（两页的共享交互件已统一）、5.13 测试（Vitest 与 Playwright 都已落地，**两层都进 CI 阻断**，见附录 AB/BE/BF.6） |
| ✅ 已完成（**2026-09-14 更正：此前写"5.9 的修复一半除外"**） | 5.5 拆分巨型页面（三页全部拆完，附录 AX/AZ/BB）、5.10 响应式（附录 BC）**已完成**；5.9 可访问性**审计 + 修复 + 覆盖 + 收尾四轮都已落盘并清零 —— 修复已完成，不再有"修复的一半"**（审计与覆盖轮见附录 BG，覆盖轮把扫描场景扩到 15 个（BG.11）；收尾轮把场景推到 26 个、**违规 0 组 / 0 个节点、`REGISTRY` 已空**，见**附录 BI.1**）；5.13 的字面链路也已跑通（见**附录 BI.2**） |
| 🟡 第一期完成、切换被后端阻塞 | 5.1 OpenAPI 生成（**选型已定**：`openapi-typescript@7.13.0`，不再是"待拍板"）：schema 与生成类型已落盘、111 个手写函数的分歧已机械判定；但**前置全在后端**（22 个端点缺 `response_model`），见**附录 BH** |
| ⚠️ 需先确认 | 5.2 TanStack Query、5.3 Zustand（**选型即决策**）。5.6 CSS 体系重建的**选型与机制已定、9 个样式表已迁**（剩 6 个，批次顺序见 `frontend/docs/css-migration-plan.md` §7） |
⚠️ 4.6 与 4.7 的关系要澄清：**4.7 并不依赖 4.6**。计划里写的缓存键是
`(prompt_version, input_hash)`，但缓存键取**完整输入**（messages 里就有提示词）
已经覆盖了"提示词变了"这件事；`prompt_version` 的真正用途是**溯源**
（哪张卡是哪个版本的提示词产出的），属于独立的一件事。

**因此最该先做的是 3.5（简答语义判分）+ 3.6（FSRS）**：
3.5 让判分可信、3.6 让调度可信，两者共同决定 3.4/3.14 能否产出有意义的数据。
3.10 / 3.7 是 3.6 的下游。

⚠️ **但 3.6 有一个数据前提**：现有 194 条 `review_logs` 的 `interval` 长期为 1、
且最后复习时间是 2026-06-23（约 14 个月前），**样本不足以拟合 FSRS 参数**。
因此 3.6 的现实做法是**先接入 FSRS 的默认参数与状态机**（让调度逻辑正确），
参数拟合作 3.14 的一部分留待数据积累后再做 —— 而不是等数据齐了再动手。

**3.6 完成后的实际状态**（附录 W）：默认参数与状态机已接入
（`DEFAULT_W` 是预留的拟合插槽），3.2 顺带完成，3.7 的一半被结构性解决。
但**"同等保持率下复习量下降"这个验收指标无法在此刻验证** ——
真库还没有一行由 FSRS 产生的复习记录，而复习量下降本来就要跨月观察。
现在的状态是"调度逻辑正确且可回退"，而不是"已经更省复习量"。

**因此下一步该做的不是 3.9/3.14，而是 3.7（fuzz）**：
fuzz 是唯一能立刻改善真实体验的一项（同批导入的卡片会在同一天到期），
而 3.9/3.14 的价值取决于用户真的开始按 FSRS 复习（需要真实使用数据）。


### 阶段 4 · LLM 网关与成本治理（1.5 周）

| # | 动作 | 验收 | 状态 |
|---|---|---|---|
| 4.1 | 抽出独立 `LLMGateway`：统一入口、重试/退避/限流/并发/配额/缓存/记账、**结构化输出校验（Pydantic）** | `llm_service.py` 从 831 行降到 <300 | ✅ **已落地**（附录 AG + AI + AN）：调用策略 → `services/llm/gateway.py`（625 行），提示词 → `services/llm/prompts.py`（381 行），场景方法 → `services/llm/scenes.py`（556 行），结构化校验 → `services/llm/structured.py`。`llm_service.py` **1273 → 221 行**，对外 API 一行未变（15 个调用方零改动） |

| 4.2 | 新增 `llm_calls` 表：每次调用记账（user/note/scene/model/tokens/cost/latency） | 成本可按用户/笔记/场景聚合 | ✅ **已落地**（附录 AC）：新表 + `llm_accounting_service`（上下文/计价/记账/聚合）+ `GET /api/llm/usage`；`chat_detailed` 与 `chat_stream` 两个真实出口都接上。**失败也记账**；价格未配置时 `cost` 记 **NULL 而不是 0** |
| 4.3 | **配额与告警**：用户级 token/金额配额；超限拒绝并给出明确错误码 | 无法无限烧钱 | ✅ **已落地**（附录 AD）：每日 token/金额配额（默认关）；检查在**发起请求之前**，超限抛 `LLMQuotaExceeded` → 全局处理器转成 `429 / LLM_QUOTA_EXCEEDED`。**配了金额上限但没配单价时，该上限无法执行并会告警**，不假装生效 |
| 4.4 | **限流重构**：从"进程级 10 RPM"改为"按用户 + 按供应商"的令牌桶 | 单用户不饿死他人 | ✅ **已落地**（附录 AH）：`KeyedRateLimiter` 按 key 分桶，网关串成**三层**（用户 → 供应商 → 总闸门，总闸门最后取以免被个人限额扣住）。⚠️ **改用进程内桶，未引 Redis** —— 单写者已被强制，Redis 的差别只剩"重启清零"；无用户上下文的调用共用一个 `__anonymous__` 桶（不是免检）。⚠️ 后两层**默认关闭**（`LLM_USER_MAX_RPM=0`），理由见附录 AH.4 |
| 4.5 | **修复事件循环问题**：Celery 任务改为 `asyncio.run()` **一次**包裹整个任务体（而非 6 次）；限流器/信号量改为**每 loop 惰性创建** | 无线程/loop 冲突，无连接泄漏 | ✅ **已落地**（附录 AH）：`tasks/loop.py` 的 `task_loop()` + `run_async()` 让**一个任务一个 loop**（任务体仍是同步函数，见 AH.2 对计划字面要求的偏离）；网关资源改为以 loop 对象为键的 `WeakKeyDictionary` 惰性创建，顺带修掉"配置被冻结在首次实例化"的根因 —— **附录 AE.8 那个测试侧 fixture 因此被删除** |
| 4.6 | **Prompt 版本化**：每个 prompt 有 `prompt_version`，写入 cards/quiz_items | 可评估 prompt 改动的效果 | ✅ **已落地（但"可评估"有一处明确缺口，见本条末尾）**（附录 AJ）：`prompts.PROMPT_VERSIONS` + `prompt_version(name)`（**未登记返回 None，不猜**），`knowledge_cards` / `quiz_items` 各加一列并写入。⚠️ 历史行**不回填**（"未知"≠"第一版"）。版本号与提示词摘要**绑在测试里**：改文本必须同时升版本，否则 `test_prompt_version` 失败。**写入侧已核实接通**，并且是**四处**生产写入点（`understanding_service.py:472` 的 `understanding_session`、`knowledge_link_service.py:130` 的 `combined_analysis_session`、`:510` 的 `generate_extension_knowledge`、`understand_tasks.py:366` 的 `question_session` —— 来源扫描见 `prompt_version_report_service.py:33-45`）。**真库现状（2026-09-14 复核）**：`knowledge_cards` 1181 行、`quiz_items` 1058 行**全部为 `NULL`** —— 与"历史行不回填"一致，**不是缺陷**。读取侧**已经补上**（阶段 6 的那半）：`GET /api/llm/prompt-versions`（`api/llm.py:418`）+ `prompt_version_report_service`，且它把三种"没有数据"分开讲（已登记无产出 / 数据里有登记表里没有 / NULL 未知）。<br>⏸ **未做的一项（2026-09-14 补记，本轮实测）**：**`llm_calls` 记账表上没有 `prompt_version`**。只读实测：`llm_calls` **20 列**（`user_id / note_id / task / scene / provider / model / prompt_tokens / completion_tokens / total_tokens / cached_tokens / cost / currency / latency_ms / success / error / id / created_at / updated_at / cached / saved_tokens`），**没有 `prompt_version`**；该表 0 行。这一列只存在于 `knowledge_cards` 与 `quiz_items`。**后果**：`llm_calls` 能回答"谁、哪篇笔记、哪个场景、花了多少"，**不能**回答"这笔钱是哪一版提示词花的" —— 而后者正是 4.6 的验收（"可评估 prompt 改动的效果"）在**成本**这一侧的落点：今天的报表只能按版本比较"产出了多少内容、内容后来表现如何"，无法按版本比较"这一版更贵还是更便宜"。**补法不是加一列那么简单**：`llm_calls` 的写入点在网关（`services/llm/gateway.py`），而"当前正在跑哪个 prompt 版本"在**场景方法**层才知道，需要把版本号沿调用链传进网关（或让网关按 `scene` 反查登记表）；这也是它至今没做的原因。**这是一条台账口径的缺口，不是数据错误** —— 已写入附录 BJ.3 |
| 4.7 | **LLM 响应缓存**：相同 (prompt_version, input_hash) 命中缓存，不重复付费 | 重跑理解成本大降 | ✅ **已落地**（附录 AF）：`llm_cache` 表 + `chat_detailed` 出口缓存，键 = 完整输入（provider/base_url/model/messages/采样参数）的 sha256。命中时**照样记一行 `llm_calls`**（`cached=True`、`cost=0`、`saved_tokens=N`），因此节省**可见**。默认开，TTL 30 天 |
| 4.8 | **抽取幂等**：重跑理解时按 `content_hash` 复用已存在的卡片，只补差集 | 重跑不再产生重复卡片 | ✅ **已落地**（附录 AE）：`knowledge_cards.content_hash` + `card_intake_service`。**只补差集、永不删除**（删卡会把 `review_states` 变成孤儿行）。真库实测：跨天重跑过的笔记数为 **0**，所以这是**预防性**修复 |
| 4.9 | **卡片质量门**：空内容/过短/无 `source_quote` 的卡片不入库；单次抽取数量上限 | 脏卡片无法入库 | ✅ **已落地**（附录 AE）：正文 <10 字 / 标题 <2 字 / 无 `source_text` 一律拒收，**被拒的数量与原因全部上报**（不静默丢弃）；单次新建上限 500。真库实测只会拦下 4 + 2 张存量卡 |
| 4.10 | **修正 `debug` 开关耦合**：拆成 `app_env` / `log_sql` / `llm_provider` 三个独立配置 | 生产可用 DeepSeek 且不打印 SQL | ✅ **已落地**（附录 AK）：三个开关互不影响；`debug` 保留为遗留别名（只折叠进 `app_env`）。⚠️ 一处**有意**的行为变化：`DEBUG=true` **不再**打开 SQL 日志（那些日志含 bcrypt 哈希与卡片正文），要日志须显式 `LOG_SQL=true` |
| 4.11 | 删除 `generate_questions` 中的调试日志（`llm_service.py:634-654`） | 生产日志干净 | ✅ **已落地**（附录 AI.4）：删掉 3 条每次出题都打印的 `logger.info` 脚手架（响应类型、候选键的值类型与长度、首个元素的键）；保留 2 条指向"模型返回结构不符合约定"的 warning —— 那是需要有人知道的异常，不是噪音 |

### 阶段 5 · 前端重建（2.5 周）

| # | 动作 | 验收 | 状态 |
|---|---|---|---|
| 5.1 | **从 OpenAPI 生成类型与客户端**，替换手写 API 函数（**111** 个 —— **原文写"90 个"，2026-09-14 实测修正**，见附录 BH）与重复类型 | 前后端契约不可能漂移 | 🟡 **S1 / S2 / S3a / S3b / S4 已完成，S5 与 P3 未做**（**2026-09-14 续记**，见附录 BL / BM）。历史：第一期只生成与对比 —— `backend/openapi.json`（现已 103 路径 / 119 操作 / 184 schema）与 `src/api/generated/schema.ts` 落盘，漂移检查器（**TypeScript 编译器 API，不是正则**）判定 111/111 端点全部命中，当时分布 IDENTICAL 19 / HW_NARROWER 28 / HW_WIDER 4 / CONFLICT 34 / SCHEMA_UNTYPED 21。**续记**：P1–P3 补完（`schemaUntyped 21 → 0`）→ **S2 换响应类型**（`identical 19 → 104`）→ **S3a 换请求体**（34 个 JSON 请求体接入契约；`bodyFindings 25 → 0`、`hwDiscardsBody 1 → 0`、`compilerDiagnostics 2 → 0`、`identical 104 → 105`）→ **S3b 换查询串**（18 个调用点；参数名改为编译期检查，仪器三处升级 + 两个新分母，两次变异验证）→ **S4 收敛 `client.ts`**（三处重复错误解析合一；"删除重复类型"经清点在 S2 已完成）→ **P4 接进 CI**（`dump_openapi.py --check` + `gen:api` 幂等性两条配对步骤）。全程**分母未动**：判定对象 111、被比过的请求体 34、参数名检出 54。**未做**：**S5**（5.2 / 5.3，计划已出，见附录 BM.6）、**P3**（logout body 是否可选）。逐点记录见 `frontend/docs/openapi-client.md` §14 / §15 |
| 5.2 | 引入 **TanStack Query**：替换手写 fetch + `setInterval` 轮询 | 有缓存/重试/取消/去重 | ⏸ **未做（计划已出，待批）**：`frontend/package.json` 里没有 `@tanstack/react-query`（`swr` / `jotai` / `redux` 同样没有），`src/**` 零命中；**28 个文件在 `useEffect` 里取数、3 处 `setInterval` 轮询**（实测）。迁移计划（4 个批次、5 条不变量、行为变化清单、四个待决策项）见 `frontend/docs/query-and-state-plan.md` 与附录 BM.6 |
| 5.3 | 引入 **Zustand** 管理 UI 状态；消除 prop drilling | 页面组件行数减半 | ⏸ **未做（计划已出，待批）**：`frontend/package.json` 里没有 `zustand`，`from 'zustand'` 零命中；今天是 2 个 context（`AuthContext` / `Toast`）+ props 传递。⚠️ 验收口径需改：最大的页面是 `DailyMaterials.tsx` **682** 行 / `GraphSidebar.tsx` **679** / `Dashboard.tsx` **676**，"全部减半"在不重写 UI 的前提下做不到 —— 计划里换成三条可测口径（详见计划文档 §3.3） |
| 5.4 | **路由级懒加载**：18 个页面全部 `React.lazy` + 按需分包 | 首屏不含 force-graph/katex | ✅ **已落地**（阶段 0 的 F-7 止血项，本轮核对确认）：`App.tsx` 18 个登录后页面全部 `lazy()`，构建产物中 `graph-*.js` 186KB / `markdown-*.js` 393KB 均为**独立 chunk**，入口 `index-*.js` 仅 22KB |
| 5.5 | 拆分巨型页面：`NoteDetail.tsx`(1030) / `KnowledgeGraph.tsx`(868→文档称 1547) / `Projects.tsx`(728) | 单文件 <300 行 | ✅ **三页全部完成**（附录 AX / AZ / BB）：`NoteDetail` 1184→**282**、`KnowledgeGraph` 1055→**248**、`Projects` 765→**113**；本轮复核三页为 **290 / 249 / 135** 行（拆分时记的是 282 / 248 / 113 —— 之后各页又陆续长了几行，前提"单文件 <300 行"仍成立），三个拆分目录共 **51 个模块、最大 263 行、超过 300 行的 0 个**。⚠️ 关键前提是"先补安全网再拆"：另两页是先补了 53 条页面级用例（并经变异验证）才动的手 |
| 5.6 | **CSS 体系重建**：14 个全局 CSS → CSS Modules 或 Tailwind + design token 层 | 样式可预测、无覆盖战争 | 🟡 **部分落地（已迁 9 个样式表，剩 6 个）**：**机制 + 试点 + 三批迁移**已落地（附录 BB 与 `frontend/docs/css-migration-plan.md`）。**9 个样式表动过**（`auth` / `cleaning` / `diff` / `dashboard` / `markdown` / `learning` / `markdown-extras` / `responsive` / `base` 令牌层；其中 `markdown` 是核实后的**停**、`base` 只加令牌），**108 条规则逐条核对、丢失 0**（试点 7 + 第一/二/三批 60 / 20 / 21，每批三份证据），`main.tsx` 的导入顺序重排根治了"模块 CSS 排在全局样式表之前"的级联反转。⚠️ 一条**有记录的停**：`markdown.css` 的 5 条 `.adhd-*` 规则全是 `.markdown-body.adhd-reader-active …` 后代选择器，而 4 个类名的写入点在 `src/hooks/useAdhdReader.ts`（当时不在可改范围），硬搬只能写成 `:global(...)`。**剩 6 个**（序 5 / 8 / 9 / 10 / 12 / 13：`assessment` / `components` / `layout` / `graph` / `refinements` / `responsive`）互相咬合、必须**成批**处理 —— **每一批的剩余顺序与前置条件写在 `frontend/docs/css-migration-plan.md` §7**（不读那份文件不要动这几张表） |
| 5.7 | **修 404**：新增真实 404 页面，不再静默重定向到登录页 | 错链有明确提示 | ✅ **已落地**（阶段 0，本轮核对确认）：`App.tsx` 的 `NotFound` 组件 + `path="*"`；未登录时的 `*` 才回登录页 |
| 5.8 | **错误/空/加载三态组件化** + 全局 Toast | 无"白屏卡住" | ✅ **已落地**（阶段 0/Z，本轮核对确认）：`EmptyState.tsx` / `ErrorDisplay.tsx` / `LoadingSpinner.tsx` / `Toast.tsx` / `ErrorBoundary.tsx`，全仓 147 处使用 |
| 5.9 | **可访问性**：键盘导航、focus trap（模态）、ARIA、对比度 | axe 无 critical | ✅ **已完成（审计 / 修复 / 覆盖 / 收尾四轮都已落盘）**（**2026-09-14 更正：此前标 🟡，记的是"部分落地、修复进行中"**）：收尾轮见**附录 BI.1** —— `npm run a11y` **26 passed / 26 个场景**、违规 **0 组 / 0 个节点**、`REGISTRY` **5 条 → 0 条**（空表 = 零容忍）；新增两道常驻门禁（**每个场景都跑**的键盘可达性零容忍扫描 + **真的按 Tab** 的走查断言），**18 处**逐处改用**真控件**（`<button>` / `<Link>`，**没有一处**用 `role` + `tabIndex` 去补）；`REGISTRY` 里最后 **5 条 `heading-order` 全部清掉**，其中 ★ **F-08 此前记的成因是错的** —— 违规节点是本页自己的 `<h4>清洁统计</h4>`、**不是用户的 Markdown 内容**，而 `H4 → H1` 是**上移**、**上移对 axe 永远合法**，因此**没有动一个字的用户内容、也不需要任何豁免**（完整记录见 `frontend/docs/a11y-audit.md` **§11**）。审计一半见**附录 BG** —— axe-core 4.13 + 真 Chromium（`npm run a11y`；**覆盖轮后 15 条用例覆盖 15 个场景**（收尾轮后为 **26 个场景**），非空夹具、未定义 `/api` 一律 501、"axe 真的跑了"四道断言），CI 里**顾问式**接入（`continue-on-error: true`，升级条件写在 job 注释里）。⚠️ **计划的原验收"axe 无 critical"当场不成立**：审计轮实测 **30 组 / 68 节点**违规（critical **2** / serious 39 / moderate 21 / minor 6）—— **应用不是无障碍合格的**；而"没报出来"也不等于"没问题"（Tab 顺序、屏幕阅读器语义、动态区域它都看不见，**根本没有触控目标尺寸规则**，且**单字符文本的对比度它按设计不判**，见 BG.11.5）。⚠️ **违规数变过两次，都不是回归**：修复轮 30 / 68 → **4 / 4**（2 条 critical 已清），覆盖轮 4 / 4 → **12 / 15** —— 后者是**扫描场景从 10 个扩到 15 个**带来的可见度（11 个新节点全在新场景上，旧场景一个都没变，`upload` 0 / 0）。逐条发现与归属见 `frontend/docs/a11y-audit.md`，三轮判断见 BG.5（基线）/ BG.10（审计验收）/ **BG.11（覆盖轮）**，**修复轮已完成**（改动在 `frontend/src/**`；**2026-09-14 更正：此前写"修复轮正在进行"**），收尾轮判断见**附录 BI.1** |
| 5.10 | **响应式**：`responsive.css` 只有 41 行 → 补齐移动端布局 | 手机可用 | ✅ **已落地**（附录 BC）：断点只加两个（768 / 480），其余挤压交给内在尺寸（`min()` / `auto-fit` / `flex-wrap`）；`responsive.css` 51 → **443 行**（本轮复核 **430 行**，差额是 5.6 从它抽走的 7 条规则）。修的是真缺陷不只是"看着挤"：`NoteAskPanel` 浮层被推出屏幕外、图谱页 375px 上画布只剩约 55px 竖缝、长 URL 顶出整页横向滚动、自评按钮偏小；另 +3 文件 / +20 用例。⚠️ jsdom 不加载样式表、不做布局 —— 抽屉实际宽度、触控真实高度、`env(safe-area-inset-*)`、`100dvh` **只有规则声明，没有渲染结果**（BC.6） |
| 5.11 | **任务进度 UI**：真实进度条 + 阶段名 + 取消按钮 | 取代"转圈" | ✅ **已落地**（附录 AO）：新增 `api/tasks.ts` + `components/TaskProgress.tsx`，接在笔记详情的转换中状态与清洗面板上。**后端契约从阶段 1′ 起就有，前端此前一次都没消费过** |
| 5.12 | **复习 UI 重做**：四档自评 + 答错展开原文 + 引用回跳 | 核心流程体验 | ✅ **已落地**（阶段 3.12 / 附录 AA：卡片复习页、四档自评、原文折叠/解析；引用回跳见附录 Z 的 `citationJump`）。⚠️ 原写的"剩余：答题复习页与卡片复习页的统一"**已经不做剩下了**（`b037771`）：两条流程共用同一套交互件（`SelfRatingButtons` / `ReviewProgress` / `useReviewKeyboard` / `SourceContext`），刻意**不**统一的三处（评分来源、提交路径、调度依据面板）各自写了理由 —— 见 `frontend/src/pages/CardReview.tsx` 文件头 |
| 5.13 | **Vitest + Playwright**：核心流程 E2E（上传→理解→复习→问答） | CI 里有 E2E | ✅ **已完成（2026-09-14 更正：此前标 🟡，记的是"两层都已落地并接入 CI，但计划字面的链路仍缺"）**：计划字面的**上传→理解→复习→问答**已经真的跑通 —— 对着**真实后端 + 真实 LLM**（走 **OpenCode 的网关**：`base_url=https://opencode.ai/zen/go/v1`，按 `backend/.env` 配置 —— ⚠️ **变量名里写的是 DEEPSEEK，端点却是 OpenCode**），**6 passed × 3 runs**；这一层**只在 `ENGRAMNOTE_E2E_FULL=1` 时跑**，与阻断的 `npm run e2e`（**仍是 10 passed**）隔离，用一次性 DB / 存储 / broker，并实测**真库未被触碰**（附录 BI.2）。⚠️ **它挖出一条会污染生产库的危险**：`get_celery_broker_dir()` **硬编码 `backend/data/celery`、没有环境变量入口**，只配 `DATABASE_URL` 的测试后端会把任务投到**生产的文件系统 broker**，被人类的 `start.bat` worker 抢去用**真库**执行 —— **一次 E2E 就能污染生产知识库**；实际用的缓解是**在 import `celery_app` 之前、进程内完成隔离**（BI.2.1）。⚠️ 仍然不覆盖：PDF / Office / 音频上传路径、**全部失败路径**、多 worker / 多用户（`frontend/docs/e2e.md` §8.6）。另外两层照旧：**Vitest 21 文件 / 276 用例**（附录 AB；本轮复核 `npm test` 退出 0）、**Playwright 4 个 spec / 10 passed / 0 skipped**（附录 BE.3 + BF.5/BF.6），并当场抓到一条真缺陷（从 `/login` 登录成功落 404，已修）。验收写的"CI 里有 E2E"**已经满足**；**2026-09-14 更正** —— 此前接着写的是"覆盖的却仍是"外壳 + 认证入口"级别的冒烟 —— 计划字面的"上传→理解→复习→问答"**没有做**"，那句**已经过期**（字面链路已跑通，见**附录 BI.2**），旧句保留在此以对照；两层冒烟的边界仍见 `frontend/docs/e2e.md` §4，全链路层不覆盖的部分见 §8.6 |

### 阶段 6 · 安全与合规收口（1 周）

| # | 动作 | 验收 | 状态 |
|---|---|---|---|
| 6.1 | 密码策略（长度/复杂度）+ bcrypt cost 提升 | 弱密码被拒 | ✅ **已落地**（附录 AT）：新增 `services/password_policy.py`（纯函数，7 条判据）+ 注册接口与服务层两道校验 + `bcrypt_rounds=12` 显式配置。⚠️ 两条刻意的取舍：**不做强制字符组合**（会把人推向 `Password1!` 这种可预测形态）、**只约束注册不追溯存量**（追溯等于把老用户锁在门外）。⚠️ 这是**用户可见变化**：6 位密码的注册请求现在会收到 422 与具体理由 |
| 6.2 | 恒定时间登录（邮箱不存在也跑一次 bcrypt）；注册不再泄露存在性 | 时间侧信道关闭 | ✅ **已在实现中，本轮补齐测试并核对**（附录 AT）：`_DUMMY_HASH` 时序对齐 + 注册冲突统一文案（`§2.5 E-2` 时期就已修）。此前**只有注释守护、没有任何测试** —— 本轮补了 3 条（未知邮箱必跑一次 bcrypt、两种冲突文案必须相同、假哈希 cost 必须与真实一致） |
| 6.3 | **Refresh token + 轮换 + `jti` 黑名单 + 登出撤销** | token 可吊销 | ✅ **已落地**（附录 BA）：新增 `refresh_tokens` 表（`jti` 唯一 + `family_id` 轮换链 + `revoked_at` —— 计划里说的 `jti` 黑名单就是这张表）、令牌类型声明 `typ`（**缺 `typ` 的存量令牌仍按访问令牌放行**，升级不踢人）、`POST /api/auth/refresh`（轮换；**重放已撤销的令牌 ⇒ 撤销整条链**）、`POST /api/auth/logout`（免认证 + 幂等 + 可选 `all_devices`）、`refresh_token_expire_days=30`、每日 04:15 的过期行清理任务（有界分批）、前端「401 → 刷新一次 → 重放一次」（并发 401 共享同一次刷新）。⚠️ 三条刻意取舍：**访问令牌仍无状态**（登出后到 `exp` 前仍可用，被撤销的是"会话无法再延长"）、**登出免认证但只信签名可验证的令牌**（否则就是"任意注销他人会话"的入口）、**只清理已过期行**（"已撤销但未过期"是重放检测唯一的证据）。⚠️ 迁移 `011` 的验证方式见 BA.6：**只跑这一个迁移**（整链在空库上跑不通是既有问题 M-1），并额外断言「迁移 ↔ 模型」一致 |
| 6.4 | 上传按 magic bytes 验类型；PDF 页数上限；zip 炸弹防护；单用户文件数上限 | 恶意文件被拒 | ✅ **已落地**（附录 AR）：magic bytes 校验**此前已存在**（阶段 0 的 `_MAGIC_RULES`）；本轮补上**PDF 页数上限**（2000）、**zip 炸弹三道判据**（解压后体积 / 压缩比 / 条目数，只读中央目录**不解压**）、**单用户笔记数上限**（5000，与容量配额互补）。⚠️ 阈值都给得很宽，是护栏不是产品限制 |
| 6.5 | 全端点补齐用户级 rate limit（尤其 LLM 端点） | 无法脚本刷接口 | ✅ **已落地**（附录 AU）：中间件本已按"已认证用户 → 否则 IP"计数，但**匹配方式是 `path.endswith(suffix)`**，规则里写的 `/understanding/start`、`/cleaning/start` 与真实路由 `/api/understanding/{note_id}/start` **永不相等** —— 两条规则从未生效。改为正则匹配，并补上 4 条被漏掉的昂贵端点（笔记级流式问答、卡片出题、转换重试、项目扫描）。新增"规则 ↔ 真实路由"对照测试，把这类**静默失效**变成红灯 |
| 6.6 | 备份与恢复：定时 `pg_dump` + 对象存储快照 + 一键恢复演练 | 演练成功 | ✅ **已落地**（附录 AS）：备份侧**早已具备**（`VACUUM INTO` 原子快照 + 落盘即校验 + 保留策略 + Beat 每日 03:30 + `scripts/restore_db.py`）；本轮补上**恢复演练**（`inspect_snapshot` / `run_restore_drill` + 每周一 05:00 的 Beat 任务）。⚠️ 计划写的 `pg_dump` 不适用（本项目是 SQLite），改用等价能力。真备份实测：**演练通过**，仅 1 条"悬挂外键"warning（已知有意保留）。**对象存储快照**（第三半，附录 AW）与**存储审计**（附录 AV）均已落地并接上调度 |
| 6.7 | 依赖与镜像扫描（`pip-audit` / `npm audit` / Trivy）纳入 CI | 无高危 CVE | 🟡 **扫描已落地并接入 CI，但刻意不阻断**（附录 BE.2 + BF.6）：34 条原始发现（去重 19 条公告）+ npm 11 条 + misconfig 4 条，全部归档可见。⚠️ 验收写的**"无高危 CVE"没有被断言过、也不该被断言** —— 其中一批是"上游明确不修"，另一批只存在于 `requirements.txt` 的**声明路径**上（BE.2 / BF.3）。⏸ 阈值门禁（`--fail-on`）与镜像扫描**未做**（BF.10） |
| 6.8 | 错误响应不再泄露内部路径/SQL；生产 `debug=False` 断言 | 无信息泄露 | ✅ **已落地**（附录 AQ）：两处把底层异常文本写进 `detail` 的上传路由已改为"用户可见的通用说明 + 详情只进服务端日志"；启动时新增**安全姿态**日志行（app_env / LLM / SQL 日志 / traceback 回吐 / JWT 密钥来源），dev 模式与 `LOG_SQL=true` 各有一条显式告警。⚠️ 计划写的"生产 `debug=False` 断言"改为**告警**而非拒绝启动（理由见 AQ.3） |

### 阶段 7 · 效果验证与产品化（1 周）

| # | 动作 |
|---|---|
| 7.1 | 灰度：老库只读 + 新库并行，双跑 2 周验证一致性 |
| 7.2 | 真实用户 A/B：FSRS vs SM-2、LLM 判分 vs 自评，用保持率作为指标 |
| 7.3 | 用真实数据拟合 FSRS 个人化参数，发布"你的记忆模型"页面 |
| 7.4 | 发布"学习效果报告"：保持率、校准、薄弱领域、卡片质量榜 |
| 7.5 | 文档重写：`architecture.md` 从零重写，删除全部 `F-xx` 引用与归档文档 |

> **2026-09-14 记（只登记，不加状态列）**：本表**与阶段 0 一样从来没有 `状态` 列**
> （表头 `| # | 动作 |`，2 列），因此这 5 条**从未有过逐条的状态跟踪**；
> 本轮**没有**给它们补 `状态` 列 —— 补一列只会多出五个空格，
> 那正是 BH.12 与 BI.4 已经否过一次的做法（理由见该两处）。
>
> 本轮只登记一个**可核对的事实**（读的是这五条动作本身有没有对应物，不是凭印象）：
> 其一，7.3 的"个人化参数拟合"**没有**任何实现 —— `fsrs_service` 用的是
> **19 个公开默认参数** `DEFAULT_W`，该处注释明确写着它是"预留的拟合插槽"
> （计划正文把参数拟合归给 3.14，而 3.14 目前返回 `insufficient_data=true`）；
> 其二，7.2 的 A/B 与 7.1 的"灰度双跑"在代码里**没有对应物**
> （`grep` `ab_test` / `gray` / `canary` 零命中）。
> 除此之外**不给出**任何"已完成/未完成"的判断 —— 剩下三条（7.4 的对外发布形态、
> 7.5 的文档重写范围）需要人来定，不是代码能替他们回答的。
> 其余四条与 4.6 / 6.6 / 5.13 的产物有交集（如 `GET /api/llm/prompt-versions` 报表、
> 学习度量层），哪些算"7.4 的一部分落地"属于判断，本轮不做。

**总工期**：约 **12 周**（单人 + AI 辅助）。若要求更短，
可砍阶段 7 与 6.6-6.7，但**阶段 0/1/3 不可压缩**。

---

## 第六部分 · 需要拍板的不可逆决策

以下 6 个决策一旦执行，回退成本很高。**请在动手前明确表态**。

| # | 决策 | 选项 A（推荐） | 选项 B | 影响 |
|---|---|---|---|---|
| **D1** | 数据库 | **PostgreSQL**（+pgvector） | 继续 SQLite（只做 WAL + busy_timeout） | 选 B 则 D-1/D-2/D-7 只能缓解不能消除；多用户场景下必然复发 |
| **D2** | 部署形态 | **服务端多用户**（PG + Redis + 对象存储） | **本地单用户**（保留 SQLite，做成桌面应用） | 决定阶段 1 的全部工作。若是本地单用户，很多并发问题消失，但"零配置启动"要另做方案（如内置 PG 或 DuckDB） |
| **D3** | 调度算法 | **FSRS**（含个人化参数拟合） | 继续 SM-2（但至少修判分与基准时间） | 选 B 则"学习效果"指标只能粗糙；A 是产品差异化的核心 |
| **D4** | 简答题判分 | **LLM 语义判分 + 用户自评** | 只保留用户自评（省 token） | 选 B 成本低、且自评本身最准；但失去"错在哪"的反馈能力 |
| **D5** | 向量库 | **pgvector**（与关系数据同库） | 保留 Chroma（修相似度公式 + 改单 collection） | 选 B 则 D-9（多 collection 遍历）要另行重构，且要维护两套存储 |
| **D6** | 前端框架 | **保留 React，重建内部架构** | 换框架（Svelte/Solid 等） | 建议保留 React —— 问题在架构不在框架，换框架无法解决 18 个静态导入与 2906 行 CSS |

**我的建议**：D1=A, D2=服务端多用户, D3=A, D4=A, D5=A, D6=保留 React。

---

## 第七部分 · 验收标准与量化指标

重构不能只看"代码变好看了"。以下是**可测量的验收标准**。

### 7.1 可靠性

| 指标 | 现状（估） | 目标 |
|---|---|---|
| 并发上传时 `database is locked` 次数 | 必然出现 | **0** |
| worker 被 kill 后任务丢失率 | 100%（文件 broker + 无 reject_on_lost） | **0**（自动重投） |
| 启动时 schema 变更次数 | 每次启动（DROP/重建 5 表） | **0** |
| 单次 `pytest` 触发的真实 API 调用 | > 0 | **0** |
| 崩溃后笔记卡死在中间态的概率 | 高（无自愈） | **0**（Beat 自愈） |

### 7.2 性能与成本

| 指标 | 现状（估） | 目标 |
|---|---|---|
| RAG 单次问答的向量查询次数 | O(笔记数)，实测 90+ collection | **1 次 SQL** |
| 单篇 300 页 PDF 端到端耗时 | 30-60 分钟（10 RPM 全局限流 + solo worker） | **< 8 分钟** |
| 全站 LLM 并发上限 | 10 RPM（全站共享） | 按用户配额，供应商级可配 |
| 重跑理解的成本 | 100%（全量重抽） | **< 20%**（缓存 + 幂等） |
| 成本可见性 | 无（只进日志） | 可按用户/笔记/场景聚合 |

### 7.3 质量（最重要）

| 指标 | 现状（实测/估） | 目标 |
|---|---|---|
| **`quiz_items.interval` 的取值分布** | **实测：1058 行全部 = 1** | 分布覆盖 1→6→15→38→… |
| **掌握度分布的标准差** | **实测：91% 恒为 0，其余全挤在 70–79** | 覆盖 [0,100] 且随时间衰减 |
| 判分器对**语义反转**答案的处理 | **实测：逻辑全反得 `quality=4`（判为正确）** | 判为 incorrect |
| 判分器对**正确但简短**答案的处理 | **实测：得 `quality=1`（误杀）** | 正常通过 |
| 填空题 `学器` vs `机器学习` | **实测：判为正确** | 判为错误 |
| 向量相似度是否可用于阈值过滤 | **否**（`1/(1+d)` 在归一化向量上 ≠ 余弦：无关内容得 0.333） | 是（余弦，[0,1]） |
| RAG 检索语料来自原文的比例 | 部分（仅向量路；BM25/n-gram 检索的是卡片；降级后为 0） | **100%** |
| 引用能否被凭空生成 | **能**（检索有结果但答非所问时 `sources` 照常返回） | 引用必须由回答中的编号反查得出 |
| 引用可回溯到段落 | 否（只到笔记级） | 是（`char_start/end` + 可高亮回跳） |
| 掌握度是否随时间衰减 | **否**（无时间维度） | 是（FSRS retrievability） |
| 用户编辑笔记后向量是否更新 | **否**（永久陈旧，且行号元数据错位致误删风险） | 自动异步重建索引 |
| 是否存在学习效果度量 | **否** | 保持率 + 校准曲线 |
| 检索质量评测集 | 无 | 50 条 + Recall@5 / MRR 基线 |

### 7.4 工程质量

| 指标 | 现状 | 目标 |
|---|---|---|
| CI | 无 | 每个 PR 必过（lint + unit + e2e + 检索评测） |
| 后端测试覆盖率 | 未知（无度量） | 核心服务 ≥ 70% |
| 单文件最大行数 | 实测：`note_service.py` 940 / `goal_service.py` 698 / `upload.py` 754 | **< 400** |
| `except Exception` 数量 | 148 | < 30（且每处有明确理由与日志级别） |
| 静默吞异常 | 19 | **0** |
| 手写迁移代码 | ~400 行 | **0** |
| Alembic 是否可用 | **实测：从未运行过（无 `alembic_version` 表）** | `upgrade head` 可从空库建全表 |
| SQLite `journal_mode` | **实测：`delete`** | PG（或 WAL，若保留 SQLite） |
| 前端静态导入页面数 | 18（`React.lazy` 0 命中） | 0（全部懒加载） |
| 前端 `alert()` 数量 | 49 | **0**（改用 toast） |
| 前端 ErrorBoundary | 0 | 全局 + 路由级 |
| 首屏 JS 体积 | 含完整 highlight.js（~190 语言）+ katex + force-graph | 减半以上 |

---

## 第八部分 · 必须放弃的东西

重构的定义包含"删除"。以下是**明确建议放弃**的部分 —— 保留它们只会拖累新架构。

| # | 放弃项 | 位置/规模 | 理由 |
|---|---|---|---|
| 1 | **`F-xx` 修复编号注释体系** | 149 处 | 它是"补丁史"的化石，对新架构无意义。归档 `docs/decisions.md` 为历史 |
| 2 | **`database.py` 手写迁移** | `_migrate_sqlite` + `_rebuild_dangling_tables`，约 400 行 | Alembic 是唯一通道。**这是最该删的一段代码** |
| 3 | **Chroma 向量库** | `embedding_service.VectorStore` + `data/chroma/`（90+ collection） | pgvector 同库、单表、ANN 索引、与全文检索可 JOIN |
| 4 | **纯 Python BM25 + n-gram 双路** | `rag_service._tokenize/_search_bm25/_search_relevant_cards`，约 230 行 | PG `tsvector`/`pg_bigm` 更快更准；n-gram 路是噪声 |
| 5 | **`cleaning_service.split_into_chunks`** | 约 250 行 | 与 `markdown_segmenter` 重复；保留结构感知的那一个 |
| 6 | **字符 n-gram 判分** | `sm2_service._score_short_answer` + 填空题字符集合匹配 | 可被随机文本骗过；这是产品可信度的最大威胁 |
| 7 | **SM-2 自实现** | `sm2_service.py` 的调度部分 | 换 FSRS；判分部分保留但重写。✅ **已换**（阶段 3.6，附录 W）：调度走 FSRS-5，SM-2 只作为 `review_scheduler=sm2` 的回退路径保留 |
| 8 | **`debug` 单一开关** | `config.debug` 同时控 SQL 日志 / FastAPI debug / LLM 供应商 | 拆成三个 |
| 9 | **`_kb_cache` 模块级无界缓存** | `rag_service.py:47` | 有 DB 索引后不需要；无界字典是内存泄漏 |
| 10 | **手写 API 函数与手写类型**（原写"`client.ts` 手写 90 个 API 函数"；**2026-09-14 实测修正**：是 **111** 个，而且 `client.ts` 现为 **490 行、只剩 1 个端点函数**，其余 110 个早已按域拆走 —— 要放弃的是**这一层手写契约**，不是某个文件的体积，见附录 BH） | `frontend/src/api/`（11 个域模块 + `client.ts` 的基础设施层） | 从 OpenAPI 生成（**第一期已生成并对比**；切换被后端 22 个缺 `response_model` 的端点阻塞，见附录 BH） |
| 11 | **14 个全局 CSS 文件** | 约 2700 行 | 无作用域、互相覆盖、补丁式追加 |
| 12 | **`backend/` 根目录 15 个一次性脚本** | `test*.py` / `verify_clean.py` / `e2e_cleanup.py` / `reset_cleaning.py` / `restore_note.py` 等 | 移入 `scripts/dev/` 并加环境守卫，或直接删。→ **2026-09-14：搬迁已执行**（15 个脚本在 `backend/scripts/dev/`、根目录 0 个 `.py`，并已入库）；**"加环境守卫"这半没有做**（它们靠 `pytest.ini` 的 `testpaths = tests` 不被收集，其中 `test_cleaning_failed.py` 被点名时仍会打 `localhost:8000`）—— 见 0.2 与**附录 BK.1** |
| 13 | **`backend/data_backup_e2e/`** | 完整的数据目录副本（含 models、chroma、storage） | 不应进版本库；加 `.gitignore` 并删除 |
| 14 | **`docs/archive/`（约 390KB）与过期 `eslint-report*.txt`（约 470KB）** | `docs/` | 归档文档明确"内容可能过时"，但仍在被引用；清理后只留 `architecture.md`（重写）+ `decisions.md`（只读归档） |
| 15 | **Vault 的"项目目录"承诺** | README:81-88、Projects 页展示的 Vault 路径 | 项目已是纯标签，磁盘只有 `inbox`。要么恢复目录隔离（则项目不能是纯标签），要么**从 UI 上诚实移除 Vault 路径展示**。**不能继续说谎** |

---

## 附录 A · 现场实测证据

> 本方案中标注"实测"的数字，均来自对**本机真实数据库与真实模型文件**的直接读取，
> 以及用项目**原始算法**做的回归测试。这是本方案区别于"代码审阅感想"的部分。

### A.1 线上数据库实测（`backend/data/db/engramnote.db`，3.1 MB）

```
表数量：15（无 alembic_version 表）
journal_mode：delete          ← 不是 WAL
notes：22                     知识卡片：1183
quiz_items：1058              review_logs：194
note_material_links：6（含 1 行两端皆 NULL 的死行）
```

### A.2 🔴 **1058 道题的 `interval` 全部等于 1** —— 调度器从未前进过

```sql
SELECT MIN(interval), MAX(interval), AVG(interval) FROM quiz_items;
→ (1, 1, 1.0)                     -- 1058 行，无一例外
SELECT MIN(repetition), MAX(repetition), AVG(repetition) FROM quiz_items;
→ (0, 1, 0.125)                   -- 平均 0.12 次连续正确
SELECT COUNT(*) FROM quiz_items WHERE next_review_at IS NULL;
→ 867                             -- 82% 的题目从未排过复习
```

**这组数字的含义**：`interval` 全部为 1，`repetition` 最大值为 1 ——
**SM-2 算法在真实数据上一次都没有真正推进过复习间隔。**
「间隔重复」这个功能在本项目的实际使用中**从未生效**。
（与 L-1 判分缺陷、L-6 每日限额、L-4 调度实现的问题互为印证。）

### A.3 掌握度分布：**1183 张卡片中 1078 张恒为 0**

```
   0–9   : 1078        ← 91%
  50–59  :    1
  70–79  :  103        ← 唯一"有值"的区间
  90–99  :    1
   MIN/MAX/AVG = 0.0 / 90.0 / 7.32
```

**理论上应落在 [16, 76]**（见 L-2 推导），实际观测到 **1078 张恒等于 0**
（因这些卡片下无题目 → `mastery_service.py:40-41` 直接 `return 0.0`），
其余 103 张**全部挤在 70–79 一个 10 分宽的桶里**。

**结论：`mastery_level` 是一个几乎没有方差、且 91% 恒为 0 的字段。**
它在 UI 上画进度条、在 `knowledge_link_service.py:45` 当"掌握度 ≥ 80
才能生成拓展知识点"的门槛 —— **但这个数字不度量任何东西**（详见 L-2）。

### A.4 判分器回归测试（用 `sm2_service.py` 原始算法复现）

**正确答案**（62 字，产生 125 个 n-gram）：
> 机器学习是让计算机从数据中自动学习规律并不断改进性能的方法，它不需要人为编写明确的规则。

| 用户答案 | coverage | quality | SM-2 判定 |
|---|---|---|---|
| 完全正确答案 | 100.0% | 5 | ✅ 通过 |
| 语义正确的改写 | 78.4% | 4 | ✅ 通过 |
| **逻辑全部反转**（"不需要从数据学习…必须人为编写规则…因此不是一种方法"） | **69.6%** | **4** | **✅ 通过** |
| 字序完全打乱 | 0.8% | 1 | 拒绝 |
| 常用汉字随机串 | 0.0% | 1 | 拒绝 |
| **正确但极短**（"机器学习"） | 4.8% | 1 | **❌ 误杀** |
| 长而空洞的同主题套话 | 9.6% | 1 | 拒绝 |

**填空题**（正确答案 `机器学习`，逐字符集合交并比，阈值 0.5）：

```
用户答 '学器' → q=3 ✅     用户答 '机学' → q=3 ✅
用户答 '器学' → q=3 ✅     用户答 '学习' → q=3 ✅
用户答 '机器' → q=3 ✅
```

**即：任意两个正确字的乱序组合都判为"回忆成功"，
而逻辑完全相反的答案被判为 4 分（"正确但有些犹豫"）。**

> **诚实说明**：我最初推测"随机中文文本可骗过判分器"，**实测证明该推测是错的**
> （随机汉字串 coverage 0.0%，被正确拒绝）。
> **真实的缺陷是"语义反转无感"与"长度偏置"** —— 后者更隐蔽也更危险，
> 因为它恰好在"用户答错"与"答对但简洁"两侧同时给出错误信号。
> 本方案保留实测结论，撤回原先未经证实的说法。

### A.5 向量归一化（源码级定论）

```
sentence-transformers 5.5.1
sentence_transformer/model.py:493   normalize_embeddings: bool = False   ← 具名参数
base/model.py:477-493               forward() 无条件遍历全部子模块
base/modules/module.py:74           forward_kwargs: set[str] = set()     ← Normalize 未声明
→ 结论：2_Normalize 模块始终执行，两个模型输出都是单位向量
```

两份 `modules.json` SHA256 均为 `84E40C8E006C9B1D6C122E02CBA9B02458120B5FB0C87B746C41E0207CF642CF`。

**因此 A-1 的结论精确表述为**：向量**是**归一化的，
但 `1/(1+distance)` 在归一化向量上**不等于余弦相似度**（无关内容得 0.333 而非 0）。

### A.6 迁移体系（三重确认）

- 迁移链首个文件即 `op.add_column('notes', ...)`（`001:23`），**全链无建表**
- `alembic.ini:6` 用 `postgresql+asyncpg`，`env.py:35` 用**同步** `engine_from_config`
- 线上库**无 `alembic_version` 表**（见 A.1）

**即：Alembic 装好了、迁移文件有 10 个、文档说要用，但从未运行过一次。**

### A.7 垃圾残留

- `backend/data/tmp/worker/` 下 **8 个 `engram_convert_*` 残留目录**
- `note_material_links` 有 **1 行两端皆 NULL 的死行**
- `backend/data_backup_e2e/` 是完整数据目录副本（含 models / chroma / storage）
- `backend/` 根目录 **15 个**一次性调试脚本

---

## 附录 D · 阶段 0 执行记录（2026-09-10）

> 本附录记录**实际做了什么**以及**如何验证**，与前面的"应当做什么"区分开。

### 已交付并验证

| 项 | 变更 | 验证方式与结果 |
|---|---|---|
| **R-1 修复** | `llm/client.py` 新增 `build_llm_headers()`（OpenCode 网关必需的 `x-opencode-session`），`chat_detailed` / `chat_stream` / ASR 三处接入 | **真实调用**：`chat()` 200 / 2.6s；`extract_knowledge_points()` 200 / 7.2s 返回 **7 个知识点**；`chat_stream()` 200 / 3.0s / 9 chunks；`rag_answer()` 200 / 5.9s |
| **诊断改进** | 4xx 日志补打响应体（`_response_snippet`） | 原先只记 `str(e)` = `Client error '400 Bad Request'`，网关真实原因从不进日志 |
| **数据备份** | `_backup/20260910-225138/`（db + storage + .env，已 gitignore） | `PRAGMA integrity_check` = **ok**，行数一致（notes 22 / cards 1183 / quiz 1058 / logs 194） |
| **WAL + 超时** | `journal_mode=WAL`、`busy_timeout=30000`、`synchronous=NORMAL` | 实测 `journal_mode` 由 `delete` → **`wal`** |
| **非破坏性启动** | 破坏性重建与全局去重改为门控；孤儿检查改为只报告 | 启动前后**行数与 `review_logs` 哈希完全一致** |
| **相似度修正** | `similarity_from_l2_distance()`：`1-d/2`（正确余弦）+ 0.35 相似度地板 | 单测：与理论余弦**逐行相等**；正交内容由 0.333（旧，错误）→ **0.000** 并被地板拒绝 |
| **判分修正** | 填空改归一化精确匹配 + 有界编辑距离；简答**不再伪造判分**，改 `needs_self_assessment` | `学器`/`机学`/`学习`/`机器` 对 `机器学习` 由**判对** → **全部判错**；标点/尾字容错保留 |
| **复习服务** | `submit_answer` 支持 `self_rating`；占位分**不推进 SM-2** | 避免"未自评"被误当成"答错并重置间隔"而销毁进度 |
| **限流** | 新增 `middleware/rate_limit.py`（登录 10/min、注册 5/min、LLM 端点按需） | **实机验证**：第 11 次登录请求返回 429 + `Retry-After: 56`；`/health`、`/auth/me` 不受影响 |
| **安全加固** | 注册不再泄露账号存在性；登录加 bcrypt 时序对齐；`debug` 默认 `False`；`bcrypt` 72 字节截断显式化 | 时序对齐实测单次登录 ~422ms（此前不存在邮箱时立即返回） |
| **nginx** | `client_max_body_size 500m`、`proxy_buffering off`、缓存头、安全头 | CI 增加配置守卫（缺任一项即失败） |
| **CI** | `.github/workflows/ci.yml`（后端离线测试 + lint、前端 lint/typecheck/build、nginx 配置守卫） | 此前**无任何 CI** |
| **测试隔离** | `tests/conftest.py` 网络守卫 + `pytest.ini` + 8 个联网脚本移入 `tests/integration/` | 收集阶段由 **8 errors → 0**；`pytest` 从 29 failed/136 passed → **168 passed / 3 skipped / 0 failed** |
| **前端 B1** | 新增 `ErrorBoundary`（含按路由重置）+ `renderMarkdown` try/catch 降级 | `tsc` 通过；坏数据不再导致整站白屏且刷新复现 |
| **前端 B3/B4** | `QuestionSets` 分页加上限与空页兜底；`DailyMaterials` 轮询加 cleanup | 修掉"可无限请求后端"与"卸载后仍跑 10 分钟" |
| **前端 B5** | `statusClass()` 单一数据源 + 补齐 `.status-learning/-learning-failed/-archived/-unknown` | 4 个页面由**无样式裸文本**恢复；`Projects` 的绕过表已删除 |
| **前端 B6** | 共享 `utils/sse.ts`（多行 data / CRLF / JSON 容错）+ `useThrottledStream` + `QA` key 稳定化 | 消除流式 O(n²) 与"每 20 字符卸载重建整条卡片" |
| **前端 B7** | 18 个页面全部 `React.lazy`、`manualChunks`、`hljs` 改 `lib/core` + 22 种语言按需注册、真实 404 页 | 依赖分层已验证：`Sidebar` 不引入重依赖，`markdown.ts` 仅被懒加载页面引用 |
| **前端 B8** | 图谱 hover 仅在节点 id 变化时 setState | 消除"鼠标一动整页重渲 + 整画布重绘" |
| **前端 B9** | 新增 `ToastProvider`，**49 处 `alert()` 全部替换**，并修掉邮件提醒开关的静默失败 | `alert` 残留 **0**；50 处 toast 调用；11 个组件注入 hook |

### 过程中的关键发现（已回写正文）

1. **§2.10 R-1**：LLM 全链路 400（`x-opencode-session`）—— 静态审查看不出，只有真跑才发现
2. **`test_db` fixture 泄漏**：恢复 `DATABASE_URL` 后未清 `get_settings()` 缓存，
   导致**后续所有用例**拿到 `no such table: users` 的 500。此前被"收集阶段就报错、
   整个文件不执行"掩盖
3. **`split_into_chapters` 短章节合并过宽**：把**带标题**的短小节并入前一章，
   使 3.1 的摘要与知识点挂到 3.2 名下，并沿卡片→题目→复习记录全链传播。
   改为只合并无标题的零散文本
4. **既有测试大量停留在旧契约**：`client.ts` 已拆分却被 grep 单文件；
   `chat_detailed` 迁移后 mock 仍指向 `chat`；`_max_tokens` 由 4096 调至 16384。
   这些是"测试从未真正运行"的直接证据

### 未完成（下一阶段）

- 前端 **B10**：引入 TanStack Query / Zustand、拆分 1030 行的 `NoteDetail.tsx`
- 后端 **阶段 1′**：单写者收拢、长事务约束、任务可靠性、`task_runs` 进度、备份恢复
- 后端 **阶段 2**：FTS5 全文检索、`sqlite-vec` 评估、`chunks` 表统一分块
- 后端 **阶段 3**：`ReviewState` 抽取、FSRS
  （注：**自评 UI 已于阶段 1.5′ 接线完成**，见附录 E）

---

## 附录 E · 阶段 1.5′ 执行记录（2026-09-11）

> 承接附录 D。本阶段的目标是**把"学习闭环"真正接通**，并修掉三处
> "文档声称已修、实际从未生效"的问题。同样区分"做了什么"与"如何验证"。

### E.1 四档自评闭环（原计划阶段 3 的 L-1，提前到本阶段）

**问题**：附录 A 实测 1058/1058 道题 `interval=1` —— SM-2 从未推进过。
根因有两层，**第二层是本阶段才发现的**：

1. 简答题无法用字符匹配可靠判分，此前对逻辑完全相反的答案也给 `quality=4`
   （附录 D 已修为"不再伪造判分 + `needs_self_assessment`"）；
2. **但即使有了 `self_rating` 参数，自评也永远写不进库**：
   `submit_answer` 的"同日同题幂等"守卫在自评那一次提交上直接返回旧结果，
   自评请求被当成重复提交挡掉了。附录 D 交付时**没有测到这一层**
   —— 参数存在 ≠ 链路通。

**做法**：把提交拆成两种明确语义，并补一列区分它们。

| 提交 | `grading_method` | 推进调度？ | 占每日额度？ |
|---|---|---|---|
| 简答题首答（无自评） | `ungraded` | 否 | 否（占位不计入 `today_done`） |
| 带自评提交（补完占位） | `self_rating` | 是 | **否**（在结算已答的题，不是在开新题） |
| 选择/填空自动判分 | `choice` / `fill_blank` | 是 | 是 |
| 本列引入前的历史行 | `legacy` | — | 是 |

- `ReviewLog` 新增 `self_rating`（nullable）与 `grading_method`
  （`NOT NULL DEFAULT 'legacy'`），走**非破坏性加列通道**，真实库 194 行全部回填。
- **为什么 `self_rating` 不复用 `quality`**：一次自评会产生两条记录
  （`ungraded` 占位 + `self_rating` 最终）。只有两条都在，才能统计
  「自动判分 vs 用户自评」的不一致率 —— 这正是阶段 3.14 校准曲线的原始信号。
  合并成一列会不可逆地丢掉它。
- 前端：`QuizAnswerCard` 新增四档自评区（0 完全忘记 / 3 勉强想起 / 4 想起来了 /
  5 轻松想起），`useSelfRating` hook 统一三页（Review / QuickReview / TodayLearn）。
  等待自评时**不显示"回答错误"**（占位分恒为 1，照常渲染会把每道未自评的简答题
  都标成答错），也不放行"下一题"（否则调度未推进就永久搁置）。
- 另留**跳过自评**逃生口：自评是第二个网络请求，若它持续失败，
  "必须自评才放行"会把用户永久卡死在该题上 —— 一个功能堵死整页，
  比丢掉一次自评信号糟得多。跳过不写库，占位记录如实保留为负样本。

**验证**（临时库探针，9 个场景全绿，随后固化为 `tests/test_self_rating.py` 29 用例）：
占位不推进 → 自评补完并推进 → 同分重复幂等 → 改判需等下次到期（F-14 设计）→
选择题自动判分 → 额度边界（剩 1 时第 N 道简答题必须能结账）→
落库记录与 `grading_method` 分布。

### E.2 过程中发现并修复的缺陷

按发现顺序，每条都是**负向测试抓出来的**：

1. **幂等守卫挡掉自评**（E.1 第 2 层根因）——"参数有了但链路不通"。
2. **每日限额检查排在幂等守卫之前**：用户答满今日额度后，最后一道题的
   自评会收到 429「今日已达上限」，与事实无关。已改为
   `到期校验 → 限额校验`，且幂等守卫排在最前。
3. **限额被简答题绕过**：第一版修复写成"简答题豁免限额"，负向测试
   立刻证明那是错的 —— 简答题因此可以无限作答，限额形同虚设。
   正确判据是"**这次要不要结算一道题**"，而不是"这道题是什么类型"。
   最终规则只有一条：**补完占位时不检查限额，其余一律检查**。
4. **`get_review_stats().today_done` 与限额口径不一致**：前者把占位记录
   算作"今日已完成"，后者不算。于是页面显示"今日 10/10"而实际只结算了 9 道。
   根因是同一个计数 SQL 在三处各写了一遍，已收敛为 `_count_settled_today()`。

> 第 3 条值得单独记一笔：**它是被自动化负向测试抓住的，不是被代码审查抓住的。**
> 我连续两版修复都错了同一个地方，两版都能"通过"正向用例。

### E.3 `WWW-Authenticate` 从未生效（B 块）

`app/api/auth.py` 里两处 401 一直写着 `headers={"WWW-Authenticate": "Bearer"}`，
但 `main.py` 的自定义 `http_exception_handler` 在重建 `JSONResponse` 时
**丢掉了 `exc.headers`** —— 这个头从来没出现在任何响应里。
只有"断言响应头"的测试能发现它，断言状态码的测试发现不了。

同时把手写 `request.headers.get("Authorization")[7:]` 换成 FastAPI 的
`HTTPBearer`：手写解析不会在 OpenAPI 里注册 `securitySchemes`，
于是 `openapi.json` 完全不体现"这些接口需要认证"，`/docs` 也没有 Authorize 按钮。

**验证**：`tests/test_auth_contract.py`（26 用例）—— 7 种畸形认证输入全部
401 + `WWW-Authenticate: Bearer`；10 个受保护路径声明 `security`；
3 个公开路径不声明；`bearer` 大小写不敏感；有效 Token 放行。

### E.4 阶段 0 遗留的两处"假修复"

| 项 | 表面 | 实际 |
|---|---|---|
| `test_db` fixture | 建的临时库、表都在，用例也过 | **从未真正隔离过**。`database.py` 在 import 时冻结了 `database_url`/`_is_sqlite`，而 engine 与 session factory 是 `lru_cache` 单例：`init_db()` 在临时库建表，**走 `get_db` 的 API 测试一直连的是真实生产库**。已删除冻结常量，改 `_db_url()`/`_sqlite()` 调用时求值；新增 `test_db_isolation.py`（6 用例）把该契约锁死 |
| `scripts/verify_fix_*.py` | 都打印"全部验证通过" | 三个脚本用同样的错误方式换库（重绑定 `db_mod.engine`），**验证结论是假的**，而且对真实库有写权限。已收敛为 `scripts/_tmpdb.py::bootstrap_temp_db()` 唯一入口，**内置自检**：换库失败时直接抛错拒绝运行，而不是继续跑出假结论 |

### E.5 阶段 1.5′ 交付清单

| 项 | 变更 | 验证 |
|---|---|---|
| 自评列 | `ReviewLog.self_rating` + `grading_method` + 非破坏性迁移通道 | 真实库 194 行回填 `legacy`，行数/`integrity` 不变 |
| 提交语义 | 占位提交 / 自评补完两种语义；限额、幂等、到期三者顺序重排 | `test_self_rating.py` 29 用例 |
| 计数口径 | `_count_settled_today()` 单一出口（占位不计入） | 同上 + `get_review_stats` 断言 |
| 认证契约 | `HTTPBearer` + 转发 `exc.headers` | `test_auth_contract.py` 26 用例 |
| 前端自评 UI | 四档自评 + 跳过逃生口；`useSelfRating` 统一三页 | `tsc --noEmit` 0 错、`eslint src/` 0 错 |
| 测试隔离 | 删除冻结常量；`test_db_isolation.py` | 229 passed / 3 skipped / 0 failed（此前 174） |
| 备份 | `scripts/backup_db.py`（`VACUUM INTO` + 完整性校验 + 保留策略） | 本轮 3 次实跑均 `integrity=ok` |

**真库终态**：15 张表 / `integrity=ok` / `journal_mode=wal`；
`users 4 / notes 22 / knowledge_cards 1183 / quiz_items 1058 / review_logs 194 /
card_relations 252` —— 与阶段 0 开始时**完全一致**，全程零数据损失。

### E.6 未完成（阶段 2′）

> 状态更新（2026-09-11 第二轮，见附录 F）：单写者已定案并落地、
> 长事务已排查处理、`ReviewState` 已抽取、备份调度化已完成。
> 下面保留原始清单以便对照。

- ~~**单写者收拢**（附录 D 遗留）仍未决，**它阻塞阶段 2/3**：
  方案 A = 固定 `--workers 1` 并接受单用户定位；方案 B = 写操作经 Celery 串行化。~~
  → ✅ 已选方案 A 并落地（启动守卫 + `docs/sqlite-single-writer.md`）
- FTS5 全文检索、`sqlite-vec` 评估、`chunks` 表统一分块 ⬜ 仍待做
- ~~`ReviewState` 抽取~~ → ✅ 已落地（附录 F）
- FSRS（**建议在积累足够复习记录后再做**：
  当前 194 条记录且 `interval` 长期为 1，还没有可用于拟合参数的调度数据）⬜ 仍待做
  → ✅ **算法与状态机已接入**（阶段 3.6，附录 W）：默认参数 + 完整 DSR 模型，
  旧行按 `S := interval` 接管。**参数拟合**仍待数据积累（3.14）；
  "复习量下降"这一验收指标也仍无法验证。当时"等数据齐了再动手"的判断
  被修正为：**先让调度逻辑正确，再等数据**（默认参数本来就是可用的）。
- ~~备份调度化（挂 Celery Beat + 一键恢复脚本）~~ → ✅ 已落地（附录 F）
- `backend/data_backup_e2e/`（4.67 GB）删除 **仍待确认**

---

## 附录 F · 阶段 1′ 收尾 + 阶段 3 核心执行记录（2026-09-11）

> 承接附录 E。本轮完成阶段 1′ 剩余全部项，并执行阶段 3 的五项核心
> （跳过 FSRS 与参数拟合，理由见 F.5）。

### F.1 方案 A 定案：单写者

`app/main.py::_enforce_single_writer()` 在启动阶段检查
`WEB_CONCURRENCY` / `UVICORN_WORKERS`，>1 且数据库为 SQLite 时**直接启动失败**，
错误信息给出两条出路。四种场景实测：默认通过 / =1 通过 / =4 拒绝 / 非法值拒绝。

刻意不自动降为 1：静默降级会让运维以为多进程部署成功、实际只跑一个 worker。
完整约束见 `docs/sqlite-single-writer.md`（同时把第六部分 D1/D2/D5 标为已定路线）。

### F.2 任务可靠性、进度与取消

| 项 | 变更 | 验证 |
|---|---|---|
| 全局任务可靠性 | `task_reject_on_worker_lost=True`、`task_time_limit=1800`、`task_soft_time_limit=1680` | `tests/test_backup_and_single_writer.py` 断言全局配置生效且 soft < hard |
| 进度可见 | 新增 `task_runs` 表 + `GET /api/tasks/{id}`、`/api/tasks/note/{id}`、取消接口 | `tests/test_task_runs.py`（21 用例，含 IDOR、心跳边界、取消语义） |
| 僵尸自愈 | Beat 每 5 分钟扫描心跳超时 → 标 stale + 释放被卡住的笔记 | 同上，含"心跳新鲜绝不误杀""笔记已推进不回滚"两条负向测试 |
| broker 目录 | `celery_app` 改走 `config.get_celery_broker_dir()` | 此前用 `storage_dir.parent` 反推，配 `vault_dir` 会指向**用户主目录** |
| worker 建表 | worker 启动阶段校验 schema | 此前 `init_db()` 只在 API 侧调用，worker 独立启动会静默失败 |

**取消是诚实的**：文件 broker 无法强杀执行中的任务，接口返回
`terminated=false` 并说明"任务会在下一个阶段边界自行退出"，不谎称已终止。

### F.3 长事务：排查结论与文档假设不同

AST 审计"写后 commit 前是否夹慢操作"**命中 0 处**。真实机制是
`get_db()` 的 session 作用域等于整个请求，配合 SQLAlchemy 的 autobegin，
流式端点会在整段 SSE 输出期间持有 session。

- `get_db()` 补显式回滚 + 兜底提交（防"HTTP 200 但数据没落库"）。
  兜底提交必须放 `finally` —— `aclose()` 抛的是 `GeneratorExit`
  （继承 BaseException），`except Exception` 接不到。**本轮实测踩到这个坑**。
- 规则写进测试：`tests/test_session_lifecycle.py` 用 AST 静态审计
  "所有含 yield 的函数不得在首个 yield 前有未提交写操作"。

### F.4 阶段 3 核心

| 项 | 旧行为（缺陷） | 新契约 |
|---|---|---|
| **3.9 掌握度** | 单调不减、无时间衰减；无题目的卡片恒为 0；`review_logs` 未按用户过滤 | `正确率 × 2^(-elapsed/interval)`；卡片级状态可支撑；全部查询带 `user_id` |
| **3.1 ReviewState** | 调度状态与题目内容同表，重跑理解即丢学习历史 | 独立表 + `(user, item_type, item_id)` 唯一约束；旧字段**双写**保回退 |
| **3.12 卡片复习** | 没出过题的卡片永远无法复习 | `GET /review/cards/due` + `POST /review/cards/{id}/submit`（四档自评） |

**真库迁移结果**（`scripts/migrate_review_states.py`，幂等）：
`review_states` 共 2241 条（quiz 1058 + card 1183），掌握度重算 1183 张。
业务表行数与迁移前完全一致。

**掌握度仍为 0 是正确行为，不是缺陷**：库里 191 道题的复习时间在
2026-06-23（约 14 个月前），而 `interval` 恒为 1。按遗忘曲线
`2^(-444/1) ≈ 0`。旧公式给这些卡片 70-79 分才是 bug —— 它让"一年前
看过一次"看起来像"已经掌握"。

### F.5 本轮发现的三个"静默失效"

1. **`_rebuild_dangling_tables()` 从未被调用**：阶段 0 把它移出 `init_db()`
   （正确，防启动丢数据），但没接回任何显式通道 → `review_logs.quiz_id`
   的可空迁移从未执行。已新增 `scripts/reconcile_schema.py` 作为显式、
   带备份与逐项校验的入口。
2. **`Base.metadata` 为空导致重建崩溃**：`database.py` 从不导入 `app.models`，
   独立脚本里 `Base.metadata` 是空的。`init_db()` 与本函数都已补副作用导入。
3. **`init_db()` 在 metadata 为空时静默什么都不建**（上一轮发现，同源）。

### F.6 本轮未完成

- FTS5 全文检索、`sqlite-vec` 评估、`chunks` 表统一分块（阶段 2′）
- FSRS 与个性化参数拟合：**数据不足**。194 条复习记录且 `interval` 长期为 1，
  没有可用于拟合的调度数据
- 阶段 3.14 度量层的**前端呈现**（后端接口已就绪，见附录 G）
- `quiz_items` 上调度字段的最终删除：待到期队列、统计、前端全部切到
  `review_states` 后再做（当前双写保留回退能力）
- `backend/data_backup_e2e/`（4.67 GB）删除 **仍待确认**

---

## 附录 G · 阶段 3 收尾：事件流化与度量层（2026-09-11）

> 承接附录 F。本附录补齐阶段 3 的 3.2（事件流化）与 3.14（度量层），
> 并记录度量层在**真实数据**上的诚实表现。

### G.1 3.2 `review_logs` 事件流化：加 `card_id`

文档原文要求加 `rating` / `predicted_retention` / `item_type`。实施时按
"是否解决真实问题"逐项取舍：

| 文档字段 | 处置 | 理由 |
|---|---|---|
| `rating` | **不加** | 已由 `quality`（进入调度的分）+ `self_rating`（用户自评）承担；再加一列是重复存储 |
| `item_type` | **不加** | `ReviewLog` 里由 `quiz_id`/`card_id` 是否为空无歧义区分，不像 `ReviewState` 那样两个 id 都是 UUID 需要显式判别 |
| `predicted_retention` | **暂不加** | 当前调度器不产出逐项预测值；加一列只能全为空。校准曲线改用自评 vs 实际表现（数据源已存在） |
| **`card_id`** | **新增** ✅ | 解决两个真实问题，见下 |

`card_id` 的必要性：

1. **卡片级复习（3.12）没有题目**，`quiz_id` 为空时记录无法归到任何卡片 ——
   度量层按卡片聚合会整批丢掉这类记录。
2. **题目会被"重新理解"整批替换**（`generate_questions`），届时 `quiz_id`
   指向的行消失，历史复习记录再也找不到归属。`card_id` 是稳定的 ——
   这正是症状 **D-3「重跑理解丢学习历史」** 的根因。

真库迁移：加列走非破坏性通道，**194/194 条历史记录全部回填**
（`review_logs.quiz_id → quiz_items.card_id`）。

### G.2 3.14 度量层

新增 `services/learning_metrics_service.py` + `GET /api/report/learning-metrics`。

| 度量 | 定义 | 关键实现决策 |
|---|---|---|
| **保持率曲线** | 复习后经过 t 天仍能回忆的比例 | 只配对**相邻**两次（用首次配末次会跨越中间复习，间隔失去记忆强度含义）；排除前次未通过的对；**排除间隔 < 0.5 天的对** |
| **校准曲线** | 自评与实际表现的偏差 | 需要"自评过 + 有后续复习"的配对 |
| **遗忘分布** | leech 候选 | 统计**当前连续**失败，不是累计 —— 用累计会让列表只增不减，用户很快对它免疫 |
| **复习负载** | 未来 1/24h/7d/30d 到期量 | `next_review_at IS NULL` 计为"立即可复习"，与 `review_state_service` 判定一致 |

**最小间隔取 0.5 天而不是 `> 0`**：用户答错后立刻重做，间隔可能是
5 分钟（0.0035 天）。它经过了时间，但不足以检验记忆；计入会系统性
**高估**保持率（刚看完答案当然答得对）。这个阈值是被测试抓出来的 ——
第一版写 `> 0`，测试构造 5 分钟间隔后立刻失败。

### G.3 在真实数据上的表现（诚实性验收）

`GET /api/report/learning-metrics` 在该库真实数据上的实际输出：

    retention:   buckets=[],     sample_size=0, insufficient_data=true
    calibration: tiers=[],       sample_size=0, insufficient_data=true
    lapses:      leech_candidates=[], max_consecutive_lapses=2, items=191
    load:        due_now=2239（其余窗口为 0）
    data_quality: total_reviews=194, distinct_items=191, review_pairs=3,
                  pairs_with_time_gap=0, self_rated_reviews=0,
                  card_level_reviews=0, tracked_items=2239
    notes: ["保持率曲线需要「同一内容间隔一天以上复习两次」的记录，
             目前只有 0 条（需 20 条）。按复习计划正常复习几天后即可看到。",
            "校准曲线需要「自评 + 后续实际表现」配对，目前只有 0 条（需 20 条）。"]

**这是设计目标，不是缺陷**：194 条记录里 188 道题只复习过 1 次，
仅有的 3 组重复复习间隔接近 0 天，且 `self_rating` 全为 NULL。
在这份数据上画出任何曲线都是编造。因此接口返回
`insufficient_data` + 真实样本量 + **面向用户的说明**（缺什么、怎么才会有）。

`notes` 面向用户，必须说清"怎么才会有"，否则用户会以为功能坏了。

### G.4 本轮抓到的第四个"静默失效"

**新增模型字段必须在 `_migrate_sqlite` 里登记迁移，否则只在真库报错。**

`create_all()` 只建缺失的**表**，不给已有表加列；`_migrate_sqlite()` 也只加
它显式写出的列。于是 `grading_method` 与 `card_id` 两次都是
"全新库测试全绿、真库 `no such column`"。

已加防回归测试 `test_session_lifecycle.py::TestLegacySchemaReconciliation`：
用 AST 提取迁移源码里登记的列，断言"模型列必须落在初始 schema 基线或已登记迁移之一"，
并反向检查迁移里没有模型已删除的幽灵列。

---

## 附录 H · 阶段 1′ 收尾（二）：M-4/M-10 复核与修复（2026-09-11）

> 承接附录 G。本轮目标是把阶段 1 表格里剩下的 `1.12` 与 `1.13` 做掉。
> **结果与预期相反：两条都是文档写错了，但底下藏着一个真实的缺陷。**

### H.1 方法：先证伪，再动手

两条缺陷描述都声称"已用最小用例复现"。逐条实测后发现，
**按文档描述的机制都不成立**。这轮的教训是：
**"文档说已复现"不等于"现在仍然存在"** —— 中间可能已被别的改动顺带修掉，
或者当初的复现本身就理解错了。所以第一步是写测试尝试复现，
而不是直接照着描述改代码。

### H.2 M-4（物理删除笔记外键违约）—— **不存在**

文档说 `purge_note` "只处理了待删卡片**作为父**的引用，没处理作为子"。

实测：`note_service.py` 的

```sql
UPDATE knowledge_cards SET parent_card_id = NULL
 WHERE parent_card_id IN (待删卡片)
```

**没有 `note_id` 限定**，匹配的正是"**父**是被删卡片"的全部行 ——
跨笔记的拓展卡片（其 `parent_card_id` 指向待删卡片）恰好命中。
`quiz_items` 的选取范围同理，第一个条件 `card_id.in_(card_ids)` 也不带 note_id 限定。

**验证方式（关键）**：`tests/test_purge_note_integrity.py` 做了两件事，
确保这不是"测试根本没触发"：

1. **先断言测试前提成立** —— 跨笔记引用确实建立成功（否则断言失效）；
2. **对照实验证明外键约束真在生效** —— 绕过 `purge_note` 直接
   `DELETE FROM knowledge_cards WHERE note_id = ...` 会正确抛
   `FOREIGN KEY constraint failed`。

若约束是摆设（例如 `PRAGMA foreign_keys=OFF`），这个测试会静默通过而毫无价值。

**保留的残余风险**：正确行为依赖"UPDATE 作用域是全表"这个不显眼的事实。
将来若有人给它加 `note_id == note_id` 限定（看起来很像"修 bug"），
就会**真的引入 M-4**。上述测试就是为锁住这一点而写。

**未做的加固**：`parent_card_id` 与 `quiz_items.card_id` 仍无 `ondelete`。
补它需要重建表，而 `quiz_items.card_id` 是 NOT NULL（只能靠应用层删），
故留待必要时一并处理。

### H.3 M-10（文件删除失败降级为 warning）—— 机制错，后果对

文档枚举的 5 种"磁盘/DB 分歧场景"实测不成立：

- `trash_note` 搬家失败时**路径字段保留旧值**，purge 仍能找到并删除文件；
- `purge_note` 的 meta 前缀用 `parts[:-2]` 从**当前实际路径**推导，
  trash 路径取出的前缀正确指向 trash 下的 meta，而 trash 搬家已把 meta 搬走。

**但同一个后果确实存在，载体是第三个机制**：

```python
try:
    delete_file(bucket, path)
except Exception as e:
    logger.warning(...)      # ← 失败只记日志
...
await db.delete(note)        # ← 笔记记录照删
await db.commit()
```

任意**瞬时**故障（Windows 文件被占用、杀软扫描、权限抖动）都会让文件留在
磁盘上，而 DB 记录被删除 —— 之后**再没有任何机制知道它存在**。
不是"分歧"，是**不可发现、不可恢复的泄漏**。

**修复**：`storage_service.delete_file` 加有界重试（3 次 / 50ms）。
放这一层而不是逐个调用点：所有调用方都受益，且删除是**幂等**操作，
重试代价极低；而失败一次就永久孤立的代价极高。

**两条测试成对锁住边界**：
- 瞬时失败 → 必须重试成功，不留残留
- 持续失败 → **必须继续删 DB 记录**（重试有界，不能让用户卡在"删不掉"）
- 附带：文件本就不存在时**不得**无谓重试（否则每次 purge 白等 150ms）

### H.4 本轮同时修掉的测试污染

新增的落盘测试会真实写入 `backend/data/storage/{user_id}/`。第一版没有清理，
**一轮就跑出 26 个以随机 user_id 命名的目录**，与用户真实数据混在一起，
只能靠创建时间人工分辨 —— 而真实用户目录不可再生，误删即数据损失。

已加 autouse fixture：记录用例前**已存在**的顶层目录，结束后只删新增的。
实测：清理后跑完整套件，`data/storage/` 仍只有原有的 2 个真实用户目录。

### H.5 交付

| 项 | 文件 | 验证 |
|---|---|---|
| M-4 复核（证伪 + 锁行为） | `tests/test_purge_note_integrity.py`（5 用例） | 含前提校验与 FK 生效对照实验 |
| M-10 真实缺陷修复 | `app/services/storage_service.py`（`delete_file` 重试） | 重试/不回退/不无谓重试 三条边界 |
| M-10 复核 + 修复验证 | `tests/test_purge_file_consistency.py`（8 用例） | 含存储目录隔离 fixture |
| 文档更正 | 本文件 §2.6 M-4 / M-10 + 阶段 1 表格 1.12/1.13 | — |

测试总数：342 → **355 passed / 3 skipped**，`ruff app tests` 全绿，真库零改动。

### H.6 仍未完成

- **1.11** `vault_files` 表 + 磁盘/DB sha256 一致性校验
- **阶段 2′** FTS5 全文检索、`sqlite-vec` 评估、`chunks` 表统一分块
- **FSRS**：数据不足（194 条记录、`interval` 长期为 1）
- `backend/data_backup_e2e/`（4.67GB）删除待确认

---

## 附录 I · 阶段 1.11 交付 + 校验器误报根因（2026-09-11）

### I.1 交付：Vault 一致性校验

补齐 H.6 里挂着的 **1.11**。存储层是"DB 记路径 + 文件系统存内容"的双写结构，
两侧都可能单独出问题，而**三种失效都是静默的**，只能等用户报障：

| 失效 | 用户感知 | 成因 |
|---|---|---|
| DB 有、磁盘无 | 界面看得到笔记，点开是空的 | 外部误删、迁移丢失、上传中断（先 commit DB 再写文件） |
| 磁盘有、DB 无 | **无感知**：文件永久占空间，看不到也删不掉 | 删除时文件层失败但 DB 记录已删（M-10 的另一面） |
| 大小/哈希不符 | 内容错乱或无感知 | 磁盘故障、并发写同一路径 |

| 项 | 文件 | 说明 |
|---|---|---|
| 校验服务 | `app/services/vault_audit_service.py` | `audit_vault(db, user_id=, deep=, include_orphans=)`；**只报告不修改** |
| CLI | `scripts/verify_vault.py` | `--user/--deep/--json/--no-orphans`；退出码 0=一致 1=有问题 2=校验本身失败 |
| 测试 | `tests/test_vault_audit.py`（14 用例） | 重点测判定边界：漏报与误报 |

**设计取舍**：哈希是可选的（`--deep` 才逐文件比对）。全部 markdown 算 sha256
在大库上很慢，默认只比大小。**只报告不修改**与 `_migrate_sqlite` 的孤儿检查同原则 ——
校验器一旦自动"修复"，就把一次误判变成不可逆的数据删除。

### I.2 真库实测：第一版校验器报了 41 条孤儿，**其中 0 条是真的**

在真实数据（用户 `7775422b…`，20 篇笔记）上跑第一版，输出 41 条 `orphan_file`。
逐条核对后：**41 条全部是误报**，分两类。

**类 1：bucket 别名（20 条，最危险）**

本地模式下 bucket **没有区分能力**：`_resolve_path` 对已含
`source/output/history/cache` 段的 Vault 路径不加 bucket 前缀，
整棵 `data/storage` 是**一个命名空间**，`data/storage/{user}/inbox/source/x.pdf`
同时是 `original-files` 与 `markdown` 两个"桶"里的对象（见 `list_object_names` 文档）。

第一版却按 MinIO 语义工作：用 `(bucket, name)` 建"应存在"集合，而孤儿扫描
**只遍历 markdown 桶**。于是每篇笔记的原文件（在 `original-files` 键下）在枚举
markdown 桶时都匹配不上，**全部被报成孤儿**。诊断数据：

```
expected 77 条 = markdown 57 + original-files 20
磁盘枚举 98 条（全在 markdown 命名空间）
孤儿 41 = 20（名字在 expected 里，桶不同）+ 21（真正的无引用文件）
```

这个 bug 的危害不是数字难看，而是 `verify_vault.py` 的输出**会被用来指导清理** ——
误报的原文件是用户不可再生的原始资料。

**类 2：`output/meta/` 写穿镜像（21 条）**

`vault_meta.write_note_meta()` 在每次状态变更时把笔记全量状态写到
`{P}/output/meta/{base}.json`，`write_project_meta()` 写用户级 `projects.json`。
这些镜像**有意不进数据库**（DB 才是状态权威源，镜像只是让 Vault 脱离 DB 也能读懂），
因此永远不可能被 DB 引用。按"无引用即孤儿"判定，**每一篇笔记都会多报一个孤儿**。

21 条噪声把 0 条真孤儿彻底淹没 —— 校验器的价值全在信噪比，噪声即失效。

**修复后**：真库 22 篇笔记 / 81 条 DB 记录 / 77 个磁盘对象，
仅剩 **4 条 `missing_file`**（`u1`/`u2` 两个 2026-08 的旧测试夹具），
**孤儿 0**，`--deep` 哈希模式亦无 `hash_mismatch`。

### I.3 修复：让"应存在"与"磁盘枚举"用同一种 key

新增 `_vault_key()` / `_objects_are_namespaced()`：

- MinIO：桶确有区分能力 → key 为 `(bucket, name)`，两个桶分别枚举
- 本地：桶无区分能力 → key 为 `("", name)`，只枚举一次（否则每个对象数两遍）

另加 `_is_mirror_only()` 跳过 `output/meta/` 镜像。
**注意一个自摆乌龙的实现细节**：第一版 `_is_mirror_only` 取路径**末尾两段**比对，
而 meta 对象是 `…/inbox/output/meta/{base}.json`，末尾两段是 `meta/x.json` ——
谓词恒为 False，21 条噪声原样漏出。改为判断路径中是否**连续包含** `output/meta` 段。

### I.4 顺带修掉：校验器测试**静默跑在真实 Vault 上**

写回归用例时发现：`tests/test_vault_audit.py` 的落盘**发生在真实
`data/storage`** 里。根因与 conftest 里记录过的缺陷同源 ——
`storage_service.settings` 是**模块级冻结引用**（`settings = get_settings()`
在 import 时求值），而 `conftest._refresh_module_settings()` 的刷新名单里**没有它**。

H.4 加的"只删本次新增目录"fixture 是**事后补救**：它保护了真实数据不被留下垃圾，
但用例确实在真实 Vault 里创建/删除了文件。

现改为**重定向 Vault 根**：`Settings.vault_dir` 从环境变量 `VAULT_DIR` 读取
（无 env 前缀），优先级 `vault_dir > storage_dir > 默认`，
因此清 settings 缓存 → 设 `VAULT_DIR` → 重绑 `storage_service.settings` 即可彻底隔离。
新增 `test_audit_never_touches_the_real_vault` 自检隔离本身。
实测：跑完整套件后 `data/storage/` 仍只有原有的 **2 个真实用户目录**。

### I.5 验收方式：先证伪，再确认检出能力

修完不是"测试绿了就算"。**把 bug 重新注入**（临时让 key 带 bucket、
让镜像判定恒为 False）后重跑，确认有 3 个用例失败，且失败信息里明确多出
`…/inbox/source/ok.pdf` 这条误报：

```
FAILED test_consistent_vault_reports_ok
FAILED test_absent_clean_path_is_not_an_issue
FAILED test_detects_orphan_file
  → 未检出孤儿文件: [never-registered.md, …/inbox/source/ok.pdf]
```

即：**测试确实锁住了这个缺陷**，而不是恰好通过。随后还原修复版并连跑 3 遍确认非 flaky。

### I.6 交付

| 项 | 文件 | 验证 |
|---|---|---|
| 一致性校验服务 | `app/services/vault_audit_service.py` | 真库 0 误报、0 孤儿 |
| 校验 CLI | `scripts/verify_vault.py` | 全库 + 单用户 + `--deep` 实跑 |
| 列表能力 | `app/services/storage_service.py::list_object_names` | 本地模式忽略 bucket（已修过一次"扫错基准目录"的漏报） |
| 测试 | `tests/test_vault_audit.py`（14 用例） | 含 3 条误报防线 + 1 条隔离自检；bug 重注入验证检出能力 |

测试总数：355 → **369 passed / 3 skipped**，真库零改动。

### I.7 仍未完成

- **阶段 2′**：FTS5 全文检索、`sqlite-vec` 评估、`chunks` 表统一分块
- **1.11 的 `vault_files` 表**：本次以"每次扫盘"实现校验（无新表）。
  收益是不引入需要同步的第三份真相源；代价是大库上耗时线性增长。
  若 Vault 规模上到万级对象，再考虑物化 `vault_files`。
- `backend/data_backup_e2e/`（4.67GB）删除待确认
- 旧夹具 `u1`/`u2` 的 4 条 `missing_file` 待处理（历史包袱，非本轮引入）

---

## 附录 J · 阶段 2 起步：删除类改动与一处计划修正（2026-09-11）

本仓库的检索层几乎没有行为断言 —— `TestRAGService` 只有三个
"类存在 / 方法存在 / 是 async"的断言，**在实现完全错误时也会通过**。
检索层却是产品承诺（"基于你的资料回答，可追溯"）的唯一承载。
因此本轮先把两处**删除类**改动做完并锁死，再动语料。

删除类改动的回归风险特别高：代码删掉了、没有测试守着，
"将来某次重构顺手加回来"很容易发生，而加回来之后没人记得当初为什么删。

### J.1 2.6 删除 n-gram 检索通道

原实现有三路检索：向量 + BM25 + n-gram（`_search_relevant_cards`）。
问题在于 **n-gram 与 BM25 的语料完全相同**（都来自 `_get_user_cards`），
只是打分方式更粗糙：

| | BM25 | n-gram |
|---|---|---|
| 打分 | `IDF × tf×(k1+1) / (tf + k1×(1-b+b×\|D\|/avgdl))` | `Σ 命中子串长度` |
| IDF 加权 | 有 | 无 |
| 长度归一化 | 有 | 无 |
| 实质 | 有校准的相关性 | **未归一化的词频计数** |

而 RRF 给三路**同等权重**（都乘 `1/(k+rank)`），于是一路明显更弱的检索器
与 BM25、向量通道拥有相同的投票权 —— 它实际在做的是**把噪声顶进 top-5**。

两路语料相同、其中一路纯噪声，是"看起来更强、实际更弱"的典型。
删除后 RRF 只融合向量与 BM25，两者语料不同（原文块 vs 卡片），互补性才是真的。

RRF 的**加权可配**没有一起做：权重必须由评测数据给出依据，
凭空设一组权重只是把一个未调参的默认值换成另一个（见 A-14）。

### J.2 2.10 删除 `_kb_cache` 无界缓存

原实现是 `_kb_cache: Dict[str, tuple[float, List[KnowledgeCard]]]`，
60 秒 TTL、**没有任何容量上界**、缓存的是**完整 ORM 实例**。三个问题：

1. **内存随用户数无界增长** —— 多用户场景下每个问过的用户都留下一份完整
   卡片列表（含 `content`/`source_text` 等 Text 字段）。实测本库单用户
   1183 张卡片，多用户即线性叠加且**永不主动回收**。
2. **缓存 ORM 实例** —— 与 session 生命周期绑定，session 关闭后访问未加载
   属性会抛 `DetachedInstanceError`，属于"平时不出现、并发时偶发"的失败。
3. **正确性靠调用方记得失效** —— `invalidate_kb_cache()` 需要在 5 处被正确
   调用（卡片增删、笔记 purge、理解流程、联合分析、拓展生成）。
   漏掉任何一处，用户就会在最长 60 秒内看到**已删除的卡片**参与问答。
   这类"靠约定维持正确性"的设计在本项目**已经出过事**：
   `test_db` 隔离曾经也靠约定，结果测试静默写进了真实库（见 conftest 记录）。

改为每次问答按需查询，**只取检索真正需要的 5 列**（不取 `source_text` 等大字段）。
代价说清楚：每次问答多一次带索引的 SELECT。这是有意的取舍 ——
本项目定位本地单用户自托管（`docs/sqlite-single-writer.md`），卡片量在千级，
一次 SELECT 完全可接受；"内存无界 + 正确性靠约定"不可接受。
若语料上到十万级，正解是 FTS5（2.5′）让 BM25 下沉到数据库，
而不是在进程里缓存全量。

`invalidate_kb_cache` 及其 4 处调用点一并删除，并加了一条**静态检查**用例：
任何 `app/` 下的模块再引用这两个名字就失败（`ImportError` 只在那个端点被
访问时才暴露，静态检查能提前拦住）。

### J.3 新增 `tests/test_rag_retrieval.py`（17 用例）

| 组 | 锁什么 |
|---|---|
| `TestNGramChannelRemoved` | 方法不得存在；`_rrf_fusion` 参数恰好是两路；**融合分数必须恰为两路 rank 贡献之和**（若三路回归会算成 `3/61`）；去重与排序 |
| `TestNoUnboundedCache` | `_kb_cache` / `invalidate_kb_cache` 不得存在；`app/` 全局静态检查无残留引用 |
| `TestCardCorpus` | 返回**普通 dict 而非 ORM 实例**；回收站笔记的卡片排除；独立卡片（`note_id` 为 NULL）保留；跨用户隔离 |
| `TestBM25Tokenizer` | 中文 2-gram、英文小写切分、中英混合、单汉字无 bigram（已知局限，记录成断言） |

其中"融合分数 = `2/61`"是**行为级**验证，比"方法不存在"更强：
它不依赖实现细节，即使将来把 RRF 重写成别的形式，只要偷偷多融一路就会失败。

### J.4 计划修正：2.1 不是"删掉一个函数"

原表把 2.1 写成一处删除（`markdown_segmenter` 成为唯一实现，-250 行）。
实测两个分块器**职责不同，不能直接砍掉任一个**：

- `cleaning_service.split_into_chunks` → **检索 chunk**（`clean_tasks.py:224`
  → 嵌入 → Chroma），带 `start_line/end_line/heading_context` 与 overlap
- `markdown_segmenter` → **LLM 抽取的填充边界**（`understanding_service.py:166`），
  强在结构块原子性（不切破表格/代码块/列表）

真正的目标是**一个同时具备两侧能力的 chunker**，而
`char_start/char_end/heading_path` 正是 2.7 回跳的前提。
当时补的顺序约束是 **2.1 → 2.7 → 2.3**（且 2.9 先于 2.3/2.4）——
**该约束已被附录 N.4 的实测修正为 `2.9 → 2.1 → 2.2′ → 2.3/2.4 → 2.7`**：
执行到 2.7 时发现位置信息在检索链路上被丢弃，它的真正前置是 2.2′。
此处保留原文以反映当时的判断过程，**当前有效约束以正文阶段 2 为准**。

同时确认 **2.2 / 2.5 在 SQLite 路线上不可执行**（依赖 pgvector / pg_bigm），
正文已列出 SQLite 等价项 2.2′ / 2.4′ / 2.5′（`chunks` 表 + `sqlite-vec` + FTS5）。

### J.5 交付

| 项 | 文件 | 验证 |
|---|---|---|
| n-gram 通道删除 | `app/services/rag_service.py` | RRF 只融合两路 |
| 无界缓存删除 | `app/services/rag_service.py` | 语料按需查询、只取 5 列 |
| 调用点清理 | `api/knowledge.py`、`api/understanding.py`、`services/note_service.py` | 静态检查无残留 |
| 回归测试 | `tests/test_rag_retrieval.py`（17 用例） | 含行为级融合分数断言 |
| 计划修正 | 本文件阶段 2 表格 + 2.1 风险说明 | — |

测试总数：369 → **386 passed / 3 skipped**；ruff app tests scripts 全绿；真库零改动。

---

## 附录 K · 阶段 2.9：检索质量基线（2026-09-11）

### K.1 为什么先做评测而不是先改语料

阶段 2 的核心动作之一是 **2.3「索引源从卡片换成清洗后的原文」**。
只凭"原文当然比摘要好"的直觉去做，改完后无法判断成败 ——
可能变好、变差、或对一半问题变好。**必须先有基线**。
同理适用于 RRF 权重（2.6 未做的部分）、chunk_size 调参、2.1 统一分块。

### K.2 真值从真实数据来，不造假数据

计划里写的是"造 50 条 (问题, 期望命中 chunk)"。实测发现真库里
已有更好的东西，**不需要造**：

```
quiz_items.question            ← LLM 基于某张卡片出的题
quiz_items.card_id             → knowledge_cards
knowledge_cards.source_text    ← 该知识点的**原文出处**（不是卡片摘要）
```

实测覆盖率 **1058/1058**（全部 1058 条 quiz 都有 `card_id`，且对应卡片的
`source_text` 非空）。规模是计划目标的 21 倍，且完全是真实数据。

**关键点：真值必须是"原文段落"而不是"该卡片"。** 若用"卡片出题 → 期望命中
该卡片"，卡片语料会拿到接近满分，**无法公平比较语料切换** —— 而这正是
2.3 要回答的问题。用 `source_text` 作真值，对两套语料同样适用。

### K.3 基线（真库 1058 条问题，BM25 通道）

| 指标 | 卡片语料（当前线上） | 原文 chunk 语料（2.3 目标） |
|---|---|---|
| 语料规模 | 1183 张卡片 | 1190 个 chunk（Chroma） |
| **Recall@5 严格** | 37.52% | **57.47%**（+19.95%） |
| **Recall@5 宽松** | 80.15% | **83.93%**（+3.78%） |
| MRR 严格 | 0.3387 | **0.5048**（+0.1661） |
| MRR 宽松 | 0.7562 | 0.7542（−0.0020） |
| 无候选比例 | 0.00% | 0.00% |

**结论一（支持 2.3）**：原文 chunk 语料在**两档判据上都更好或持平**，
严格档领先近 20 个百分点。2.3 的语料切换**有实测支撑，不是直觉**。

**结论二（指向下一个瓶颈）**：宽松档已经 80~84%，说明真值基本都在
top-5 之内 —— 剩下的空间主要不在**召回**而在**排序**。
这为 2.6 遗留的"RRF 加权可配"给出了方向：收益在重排序，不在增加通道。

**结论三（卡片语料比预期强）**：`card.content` 与 `source_text` 的长度比
中位数 **0.93**，即卡片几乎就是原文摘录而非概括。这解释了卡片语料
37.5% 的严格召回从何而来，也说明**卡片不是"LLM 摘要"这个假设需要修正**
（§2.3 A-2 的表述偏重）。真正的差别是"覆盖范围"而非"保真度"。

### K.4 判据选错了一次，靠数据发现

第一版宽松判据用字符 3-gram **Jaccard**，结果一边倒：
卡片 64% vs 原文 chunk **9%** —— 与严格档的结论**完全相反**。
没有接受这个"结论"，而是去查判据本身，发现它**对长度不对称有系统性偏差**：

```
真值 200 字，检索内容 2000 字（原文 chunk 常态）
  → 交集 ≈ 200，并集 ≈ 2000 → Jaccard ≈ 0.10（判未命中）
真值 200 字，检索内容 186 字（卡片常态，长度比 0.93）
  → 交集 ≈ 180，并集 ≈ 206 → Jaccard ≈ 0.87（判命中）
```

同一个"正确命中"只因为检索单元更大就被判失败 —— 分母被并集主导，
与被检索内容是否相关**无关**。

改为**包含度**（分母固定为真值），并用三组数据验证判据本身：

| 对象（真库实测） | 包含度中位数 | ≥0.5 占比 |
|---|---|---|
| 来源卡片 vs 自己的真值 | 0.773 | 83.0% |
| 语料中最佳 chunk vs 真值 | **1.000** | **94.2%** |
| **随机无关 chunk（对照组）** | **0.000** | **0.0%** |

对照组 0% 证明它不会给无关内容送分；最佳 chunk 中位数 1.0 证明原文
chunk 通常**完整包含**真值。**一个会给对照组送分、或对正确命中给低分的
判据都不能用** —— 这张表就是为了排除这两种情况。

判据修好后再看两档：结论一致（都支持 2.3），数字才可信。

### K.5 为什么不放进 CI

计划里写了"纳入 CI"，实测后**刻意不这样做**，理由是具体的：

- CI 上没有真实资料库（`backend/data/` 被 `.gitignore` 忽略），
  评测集为空、脚本退出码 2。加 `|| true` 让它永不失败 —— 那种步骤只是装饰。
- 绝对指标取决于真实资料内容。写成阈值门禁会在资料更新时误报失败，
  结果是这个评测**被绕过**，比没有更糟。

改为把守护放进 `TestRetrievalEvalHarness`（锁定判据行为：对无关内容 0 分、
对包含内容满分、**长度中立**、换行归一化、过短内容不送分），
并在本机真实库上端到端跑一次 CLI 防"语法正确但跑不起来"。
**刻意不把绝对指标写死成断言** —— 那会在资料变化时误报。

### K.6 顺带修掉 A-5：BM25 索引改为建一次查多次

评测要求在 ~1000 条问题上跑指标，而原实现每次调用都对**全量语料重新分词**
并重算 df/IDF —— 逐条重建意味着同一份语料重复分词 1000 次。

拆成 `BM25Index`（建索引 + `search`）。线上用 `_get_bm25_index` 复用，
失效靠**语料指纹**（覆盖全部 card_id）而非 TTL：

- TTL 到期前语料变了 → 用到旧索引
- TTL 到期时语料没变 → 白重建一次
- 指纹两种情况都对

缓存槽位是**单个 tuple**（`(user_id, signature, index)`）而不是
`Dict[user_id, ...]`，结构上就不可能随用户数增长 —— 这是与刚删掉的
`_kb_cache` 的关键区别。另存的是**派生统计量**（词频/IDF）而非 ORM 实例，
因此没有 `DetachedInstanceError`。

**动手时踩到的坑**：第一版先 `BM25Index(cards)` 再比较指纹 ——
构建（也就是全量分词）已经发生，优化等于没做。改为**先算指纹再决定是否构建**
（`signature_of` 只遍历 id，比建索引便宜得多），并加测试用计数分词器
断言"语料未变时第二次取索引不再分词"。

重构打分代码的风险是**分数悄悄变了**，因此加了一条等价性测试：
把重构前的算法**原样抄写**为 `_legacy_bm25`，在 7 个查询上逐条比对
分数与字段（`rel=1e-12`）。参照实现刻意保留低效写法 ——
它要证明的是"行为未变"，不是"写法好看"。

### K.7 交付

| 项 | 文件 | 验证 |
|---|---|---|
| 评测 harness | `scripts/eval_retrieval.py` | 真库 1058 条实跑；`--corpus cards/chunks/both`、`--limit`、`--json` |
| BM25 索引化 | `app/services/rag_service.py`（`BM25Index`） | 等价性测试 7 例；索引复用断言 |
| 回归测试 | `tests/test_rag_retrieval.py`（34 用例） | 含判据行为 5 例 + CLI 端到端 1 例 |
| 基线记录 | 本附录 K.3 | — |

测试总数：386 → **403 passed / 3 skipped**；ruff app tests scripts 全绿；真库零改动。

### K.8 仍未完成

- **2.1 统一分块**（`char_start/char_end` 是 2.7 回跳的前提）
- **2.3 语料切换**：已有基线支撑，但需要 chunk 带定位信息后才能做回跳
- **2.4 去掉 Chroma 多 collection**（24 个 collection，检索要遍历全部）
- **2.7 引用可回跳**（前端 + 后端联动）
- RRF **加权可配**：K.3 结论二指出方向在重排序，需在 2.3 落地后重新测权重

---

## 附录 L · 阶段 2.1/2.7 后端：带定位信息的分段（2026-09-11）

### L.1 交付

`markdown_segmenter.segment_with_offsets(text, limit, overlap_blocks=0)` →
`List[Segment]`，`Segment = {content, char_start, char_end, heading_path, block_index}`。

它把此前两套实现各自的强项合到一起（这是 2.1 的目标形态）：

| 能力 | 来自 |
|---|---|
| 结构块原子性（不切破表格/代码块/列表） | `markdown_segmenter` |
| 字符偏移、标题路径、可选 overlap | `cleaning_service.split_into_chunks` |

**尚未接线**：检索路径仍走 `split_into_chunks`（见 L.4），前端也未展示。
本附录只交付"能产出正确的定位信息"这一层。

### L.2 核心不变量，以及它**两次**被违反

```
text[char_start:char_end] == content
```

前端拿偏移去原文切片并高亮，切出来的必须就是检索到的那段。所以这条不变量
是本功能的全部基础，也必须是**属性测试**而不是几个手写样例。

**第一次违反（10926 段中 1318 段，12%）**：实现用 `text.find(block, cursor)`
事后搜索块位置。块文本被 `strip("\n")` 过，`find` 可能失败；一旦失败就回退
找首行，而 `cursor` 仍按**整块长度**推进 —— 此后每块的搜索起点都偏了，
错误沿文档**累积**。首个分段的 `char_start=0` 却切出了文件后半部分的内容。
只抽查 1 个文件时恰好没触发。

**第二次违反（1318 段，同样的数量）**：改成切块时记录偏移后，仍然违反。
根因与第一次完全不同 —— **多块拼接**时用 `"\n\n"` 连接，而块间在原文里
可能只隔 **1 个换行**（实测 `<!-- page=1 -->\n# 标题`）。于是
`content` 比原文切片多 1 个字符，`char_end = char_start + len(content)` 偏大。

修法：**从原文直接切片**（`content = text[start_off:end_off]`），
而不是 join 块文本。不变量由构造保证，不再依赖"join 的分隔符恰好等于原文"。
代价是分段内容含块间原始换行，与旧实现的 `"\n\n"` 拼接略有差异 ——
多一个换行不改变语义，换来定位绝对正确。

最终：61 个真实 markdown × 5 种 limit × 2 种 overlap = **11026 个分段，0 违反**。

### L.3 只有不变量测试是不够的：静默丢内容

第一版对超限块先 `split_oversized_block()` 拆成**字符串**、再 `text.find()`
反查位置，反查失败就 `continue`。实测 **61 个文件中 10 个（16%）内容丢失
8%~16%**，最严重的一个 41962 字的文件丢了 **3410 字**。

丢掉的内容**不违反不变量** —— 所以 L.2 的属性测试**全绿**，
缺陷完全静默。

根因是子块文本被**改写**过：段落按句拆分时用 `" "` 重新拼接，
而中文原文句间**没有空格**（`"第0句。第1句。"` → `"第0句。 第1句。"`），
`find` 一律返回 -1。

修法：`_split_block_into_spans()` 在原文上**算边界并直接切片**，
返回字符区间。切出来的东西按定义与原文一致，不存在"找不到"的可能。

新增 `test_no_content_is_silently_dropped`（覆盖率断言）。这条断言是
唯一能抓住这类缺陷的 —— **只测不变量会漏掉"整段消失"**。

### L.4 为什么还没删 `split_into_chunks`

`cleaning_service.split_into_chunks` 现在有两个用途：

1. `clean_tasks.py:224` → 检索 chunk（→ 嵌入 → Chroma）—— **这个会被本函数替代**
2. `generate_clean_copy()` 内部 → 取 `start_line/end_line` 做"行 → 所属块"映射，
   用于标记重复行

第 2 项需要的是**行号**，而 `Segment` 提供的是**字符偏移**。两者可以互换
（行号 ↔ 偏移可互相换算），但那是一次独立的改动，且它落在清洗流程的
去重标记上 —— 那是最不该和检索层改动混在一起的地方。
因此本附录**不删**它，把它留给"接线"那一步一起做，避免半途状态。

### L.5 交付与验收

| 项 | 文件 | 验证 |
|---|---|---|
| 定位分段 | `app/services/markdown_segmenter.py`（`Segment`、`segment_with_offsets`、`_split_blocks_with_offsets`、`_split_block_into_spans`、`_heading_path_at`） | 真库 11026 段 0 违反；覆盖率 ≥98% 全部通过 |
| 测试 | `tests/test_markdown_segmenter.py`（24 用例） | 含真实语料属性测试、覆盖率测试、40 组随机文档、标题路径栈、overlap、既有 API 兼容性 |

测试总数：403 → **427 passed / 3 skipped**；ruff app tests scripts 全绿；真库零改动。

### L.6 仍未完成

- ~~**接线**：检索路径与 `generate_clean_copy` 改用 `segment_with_offsets`，
  然后删除 `split_into_chunks`~~ → **已在附录 M 完成**
- **2.3** 语料切换（基线已备好，见附录 K）
- **2.4** 去掉 Chroma 多 collection
- **2.7 前端**：引用点击 → 按 `char_start/char_end` 定位并高亮

---

## 附录 M · 阶段 2.1 接线与死代码清理（2026-09-11）

补齐附录 L.6 第一条：把 `segment_with_offsets` 真正接进调用链，
并删掉被它取代的实现。

### M.1 `split_into_chunks` 已删除（-253 行）

接线后全仓库**零调用方**，`cleaning_service.py` 由 699 行降到 446 行。
它原先的第二个用途（`generate_clean_copy` 里的"行 → 所属块"映射）
改由 `Segment.line_start/line_end` 提供。

### M.2 行号与偏移都要（新增 `line_start/line_end`）

`Segment` 原先只有字符偏移，但检索 chunk 的下游按**行**定位：

- Chroma 的向量元数据存 `start_line/end_line`
- 前端按 `block_index` 恢复/删除重复块，注释标记里的行区间来自这里

所以 `Segment` 补齐了 `line_start/line_end`（二分查找由偏移换算）。
`to_retrieval_chunks()` 产出的字段与旧实现**逐字段兼容**
（`index/content/start_line/end_line/char_count/heading_context`），
另加 `char_start/char_end`。**不改名**旧字段，因为它们已经流进了
历史 Chroma 元数据与前端协议。

### M.3 接线时踩到并修掉的问题

1. **`to_retrieval_chunks` 的 `overlap` 参数是错的**。第一版实现是
   "把 `char_start` 往前挪 n 个字符但内容不变" —— 那会**直接破坏核心
   不变量**（`text[char_start:char_end] == content`），前端按偏移高亮
   就会多选一段。重叠必须在**分段时**做（区间与内容一起变长），
   正解是 `segment_with_offsets(..., overlap_blocks=n)`。
   参数已删除，改为在文档里写明原因。
2. **`chunk_overlap` 变成死配置**。它原先只被 `split_into_chunks` 读取，
   删掉那个函数后就没人用了 —— 配置写着 50、实际不生效，属于
   "配置撒谎"。已在 `config.py` 就地标注 **⚠️ 当前不生效**并说明原因，
   **没有**擅自把它接成字符级重叠：统一分块器只有块级重叠，
   而"块级 vs 字符级重叠哪个更好"必须先有评测依据（阶段 2.9），
   否则又是一次凭直觉调参。详见 M.4。
3. **CLI 测试在 Windows 上的编码陷阱**。`eval_retrieval.py` 输出含中文，
   而 `subprocess.run(text=True)` 在 Windows 默认用 GBK 解码子进程输出，
   在**读取线程**里抛 `UnicodeDecodeError` —— 报错形态与真实问题毫无关系。
   已显式指定 `encoding="utf-8"`。

### M.4 为什么 `chunk_overlap` 先不接线

接线它有三种做法，都需要先有依据：

| 做法 | 问题 |
|---|---|
| 字符级重叠（旧 `split_into_chunks` 的行为） | 与现在的块级分段语义不同，等于回退到旧分块策略 |
| 块级重叠（`overlap_blocks=n`） | 需要确定 n；且会改变 chunk 数量与检索结果 |
| 保持不重叠 | 行为可预期，但失去了"避免边界断裂"的原意 |

判断重叠是否有益，正确方式是**在基线（附录 K）上对比**：
同一套 1058 条评测题，分别用不重叠与块级重叠跑 Recall@5 / MRR。
在那之前保持不重叠 —— **可预期优于未经验证的调参**。

### M.5 交付与验收

| 项 | 文件 | 验证 |
|---|---|---|
| 统一分块器接线 | `app/tasks/clean_tasks.py`、`app/services/cleaning_service.py` | 61 个真实文件端到端跑通（分块→去重→生成副本），0 异常 |
| 删除被取代的实现 | `app/services/cleaning_service.py`（-253 行） | 全仓库零调用方 |
| chunk 契约 | `app/services/markdown_segmenter.py::to_retrieval_chunks` | 字段逐一兼容旧实现；1870 个 chunk 不变量 0 违反 |
| 行号支持 | `markdown_segmenter.py`（`line_start/line_end`） | 行号与偏移一致性测试（多种 limit） |
| 接线一致性 | `tests/test_markdown_segmenter.py::TestRetrievalChunkContract`（6 用例） | 两个调用点分块边界必须逐块相同 |
| 死代码清理 | `rag_service.py`（删 `_search_bm25` 薄包装）、`config.py` 注释更正 | — |

测试总数：427 → **433 passed / 3 skipped**；ruff app tests scripts 全绿；真库零改动。

**关于"接线一致性"这条测试为什么必须有**：`generate_clean_copy` 内部
**自己重新分块**来建立"行 → 所属块"映射，而重复块的 `block_index` 来自
检索侧的分块。两者若用了不同的分块实现或不同的 `chunk_size`，
`block_index` 会指向**另一块内容** —— 用户点"恢复"恢复错段落，且不报错。
历史上正是两套独立实现（`split_into_chunks` vs `markdown_segmenter`），
所以这条断言把"两处必须同源"固定了下来。

### M.5.1 改动效果的实测验证（不是"应该会更好"）

新分块器产出的 chunk 数量明显少于旧实现，必须先确认**不是召回退化**。

**chunk 数量**（61 个真实 markdown，767360 字符）：

| 实现 | chunk 数 | 平均长度 |
|---|---|---|
| 旧 `split_into_chunks` | 2616 | ~293 字符 |
| 新 `segment_with_offsets` | 1872 | ~410 字符 |
| 差异 | **−744（−28.4%）** | |

原因：旧实现对超长标题块会再切碎并重复标题前缀，把块切到远低于配置的
`chunk_size=500`。新实现更贴合配置，块更完整。

**检索质量**（同一套 1058 条评测题、同一真值，BM25 通道）：

| 语料 | Recall@5 严格 | MRR 严格 | Recall@5 宽松 |
|---|---|---|---|
| 卡片语料（当前线上） | 37.52% | 0.3387 | 80.15% |
| 旧分块器 chunk（Chroma 实际索引，1190 块） | 57.47% | 0.5048 | 83.93% |
| **新统一分块器 chunk（608 块，清洗原文）** | **60.30%** | **0.5152** | **85.73%** |

**三个指标全面领先**，且是在块数更少（608 vs 1190）的情况下取得的 ——
说明质量提升来自分块边界更合理，而不是"切得更碎所以更容易撞上答案"。

比较口径说明：新分块器语料由**当前的 clean.md** 分块得到（608 块），
而 Chroma 里是历史分块结果（1190 块，源自当时的分块器）。两者不是同一次
嵌入的产物，因此这里比较的是**同一评测集下的语料质量**，不是端到端延迟
或向量通道表现 —— 向量通道需重新嵌入后才能测（见 2.3/2.4）。

### M.6 仍未完成

- **2.3** 语料切换（需重新嵌入 1190 个 chunk）
- **2.4** 去掉 Chroma 多 collection
- **2.7 前端**：引用点击 → 定位并高亮
- **`chunk_overlap`**：等 2.3 落地后用评测确定是否启用及用哪种重叠

---

## 附录 N · 向量通道实测：一个碎成三种模型的索引（2026-09-11）

本来要接着做 2.7 前端。动手前先查"向量结果里有没有可跳转的位置信息"，
结果查出一个**比 2.7 更严重的问题**。

### N.1 发现：向量索引是按两种模型嵌入的

| collection 标记的模型 | collection 数 | 向量数 | 维度 |
|---|---|---|---|
| `BAAI/bge-small-zh-v1.5` | 11 | 509 | 512 |
| **（无 `embedding_model` 记录）** | 9 | 477 | 1024 |
| `BAAI/bge-m3` | 4 | 204 | 1024 |

（`config.py` 的当前配置是 `embedding_model = BAAI/bge-m3`，
fallback = `BAAI/bge-small-zh-v1.5`。）

### N.2 运行时到底能检索到多少：实测 57%

不是推断，是实测 —— 用真实模型编码一个问题，逐个 collection 查询：

| | 数量 |
|---|---|
| 可用 collection | 13（9 个无记录 + 4 个 bge-m3） |
| **查询报错 `InvalidArgumentError`** | **11（全是 bge-small 的 512 维）** |
| 可检索向量数 | **681 / 1190（57%）** |
| 永久取不到的向量 | **509（43%）** |

**一个额外的脆弱点**：`_collection_matches_current_model()` 在
`loaded_model_name` 为 `None` 时返回 `True`（"无从比对，交由查询阶段决定"）。
实测 `loaded_model_name` 在 `encode()` **之前**确实是 `None`，
所以那道检查在很多时序下会被绕过，实际靠"维度不匹配抛异常"兜底。
两条路径的结果相同（都取不到），但后者是**静默的**：
异常被 `except` 吞掉后 `return []`，日志里才有一行 warning。

`bge-m3` 本身能加载：本机可用内存 **4.51GB**，门槛 4.0GB —— **余量只有 0.5GB**。
这意味着"临时换个模型跑一下"是不安全的（见 N.5）。

### N.3 向量通道的质量（分模型测，且必须报覆盖率上限）

用本地模型分别编码 1058 条评测问题，纯向量检索 top-5：

| 通道 | 语料 | 覆盖率上限 | Recall@5 严格 | Recall@5 宽松 | 上限达成率 |
|---|---|---|---|---|---|
| BM25（新分块器 chunk） | 608 | — | **60.30%** | 85.73% | — |
| BM25（卡片语料） | 1183 | — | 37.52% | 80.15% | — |
| 向量 `bge-small-zh-v1.5` | 509 | 48.49% | 17.86% | 29.30% | 60.4% |
| 向量 `bge-m3` | 204 | 26.37% | 12.38% | 21.93% | **83.2%** |

**"覆盖率上限"这一列是必须的**，否则会得出错误结论。
第一版没有这一列，看到 `bge-m3` 只有 12.38% 就差点写下
"向量检索远差于 BM25" —— 那是**误读**：它的语料只覆盖评测集的 26.37%，
答案根本不在里面的问题，检索再准也不可能命中。

按"相对可达上限的达成率"看，两个向量模型其实都不差
（`bge-m3` 83.2%、`bge-small` 60.4%）。**向量通道的问题不是"不准"，
而是"只索引了一小部分、且碎成多个不兼容的模型"。**

顺带确认了一个技术前提：`Chroma` 存的是单位向量
（`similarity_from_l2_distance` 的 `cos = 1 - d/2` 换算成立），
因此"用当前模型重新嵌入并对比余弦"在方法上是有效的。

### N.4 2.7 前端为什么被阻断

回跳需要"这条引用来自笔记的哪个位置"，而位置的传递链是断的：

```
chunk 有 char_start/char_end/heading_path/line_start/line_end
  └→ Chroma 元数据只存了 block_index / char_count / start_line / end_line
       └→ search_vectors 只往上传 block_index，start_line/end_line 被丢掉
            └→ _rrf_fusion 只保留固定字段（note_id/note_title/content/
               similarity/block_index + card_id/title/chapter_title）
                 └→ AnswerSource = {note_id, note_title, chapter_title, relevant_text}
```

四层里丢了三层。而且 **BM25 通道的语料是知识卡片，卡片本身没有原文位置**
（只有 `card_id` 与 `source_text`），两路结果无法用同一套定位字段表达。

因此 2.7 的正解不是"往前端加个跳转按钮"，而是 **2.2′：让 `chunks` 成为
一等实体**（带 note_id + 偏移 + 标题路径 + 向量），检索各通道都引用 chunk_id。
那时回跳就是"按 chunk_id 查偏移"，不再需要从卡片反推位置。

**结论：2.7 前端应排在 2.2′/2.3/2.4 之后。** 顺序约束据此修正为
`2.9 → 2.1 → 2.2′ → 2.3/2.4 → 2.7`。

### N.5 一个操作安全提醒

`bge-m3` 模型文件 **4356MB**，本机可用内存 **4.51GB**，门槛 4.0GB ——
**余量 0.5GB**。重新嵌入 1872 个 chunk 需要加载该模型并分批编码，
在这台机器上属于高风险操作（内存耗尽会中断，而中断在一次重建中途
会留下半新半旧的索引）。

做 2.2′/2.3/2.4 之前应先：
1. 备份 `data/chroma`（已有 `_backup/` 机制，但 Chroma 目录不在其中）
2. 关闭占内存的程序，把可用内存抬到明显高于 4.0GB
3. 或显式降级到 `bge-small-zh-v1.5`（183MB）并把全文向量按该模型统一重建

### N.6 本次未改动任何代码

本附录是**纯测量**：没有改数据、没有改检索逻辑。真库 `integrity=ok`，
`data/chroma` 未被写入（全部以只读方式打开）。测试仍为
**433 passed / 3 skipped**。

目的只是把"下一步该做什么、为什么"用数据确定下来 ——
原本以为下一步是 2.7 前端，实测发现它的前置条件是 2.2′。

---

## 附录 O · 阶段 2.2′ A 半：`chunks` 一等实体（2026-09-11）

### O.1 交付

**表**：`app/models/chunk.py`（`chunks`），按 `note_id` 落库
chunk 文本 + `char_start/char_end` + `heading_path` + `line_start/line_end`
+ `content_hash` + 向量列（B 半填）。

**落库脚本**：`scripts/index_chunks.py`
（`--dry-run` / `--verify` / `--only-stale` / `--user`）。

**实测落库结果**（真库）：

| 项 | 值 |
|---|---|
| chunk 行数 | **608** |
| 覆盖笔记 | 20 篇（22 篇中 2 篇是 `u1`/`u2` 旧夹具，Markdown 本就缺失） |
| 已嵌入 | 0（A 半不写向量，见 O.3） |
| 孤儿行 / 空内容 / `char_count` 不符 / 行号倒置 / 区间倒置 | **全部 0** |

**幂等性**：连续跑两次行数仍为 608；`--only-stale` 第二次正确跳过全部 20 篇。

### O.2 为什么"落库前先自证不变量"

核心不变量 `text[char_start:char_end] == content` 在开发过程中被违反过
**两次**（各 1318 个分段，见附录 L.2）。落库与前者不同：
分段算错只是当次结果不对，**偏移写进库就成了长期契约** ——
2.7 的回跳会一直按它切片，而错误是静默的（前端只是高亮到错位置）。

因此脚本在**写入前**逐 chunk 校验不变量，任何一处不符就**拒绝整篇落库**
（`return 2`），而不是跳过那一行。理由：偏移错位往往是整篇性的
（L.2 的两次失效都是系统性漂移），只丢一行会掩盖问题。

`--verify` 则重新读库并与原文比对，另加**覆盖率**断言 ——
覆盖率是唯一能抓住"整段内容被静默丢弃"的检查（见 L.3 的教训：
丢掉的内容**不违反**不变量）。

### O.3 B 半（向量）为什么单独做

重新嵌入 608 个 chunk 需加载 `bge-m3`（4356MB），本机可用内存 4.51GB、
门槛 4.0GB —— **余量 0.5GB**（附录 N.5）。在余量这么小的情况下跑重建，
中途 OOM 会留下**半新半旧**的索引，而那种状态比"完全没有向量"更难排查。

A 半不碰模型，所以先做完并把表结构与偏移契约固定下来。
B 半需要一个明确的内存决定（抬高可用内存 / 换 `bge-small` / 暂缓）。

### O.4 与 2.7 的关系

A 半做完后，**"按 chunk_id 查偏移"的能力已经具备**。
但 2.7 仍未完成，因为还差两条接线：

1. 检索各通道返回 `chunk_id`（或至少 `note_id + char_start/char_end`），
   而 `_rrf_fusion` 目前只保留固定字段
2. `AnswerSource` 增加定位字段，前端据此滚动 + 高亮

这两条现在**不再被"数据不存在"阻断**（表里有了），
属于纯接线工作，但仍需与 2.3 一起排（2.3 会把检索语料换成 chunk 语料，
那时"检索结果就是 chunk 行"是自然的，接线也更短）。
先做 2.3 再做 2.7 的接线，比反过来少改一遍。

### O.5 验收

| 项 | 文件 | 验证 |
|---|---|---|
| `chunks` 模型 | `app/models/chunk.py` | 唯一约束 `(note_id, index)`；向量打包/解包契约 |
| 落库脚本 | `scripts/index_chunks.py` | 608 行落库；`--verify` 不变量与覆盖率全绿；幂等 |
| 测试 | `tests/test_chunks.py`（15 用例） | 含向量维度不符返回 `None`、`(note_id,index)` 唯一、重建不追加、跨用户隔离、指纹三态 |

测试总数：433 → **448 passed / 3 skipped**；ruff app tests scripts 全绿；
真库 `integrity=ok`，落库前已备份（`_backup/20260911-124746-pre-chunks-tests`）。

### O.6 仍未完成，以及它们之间**不是并列**关系

初稿把下面几项写成并列清单，核对后修正 —— 它们有真实依赖：

- **2.2′ B 半（向量列）与 2.3（语料切换）是同一次操作**
  都要"把 608 个 chunk 用**一个**模型重新嵌入"，区别只是写到哪里。
  分两次做 = 嵌入两遍（内存吃紧时尤其不该）；合一次 = 嵌入一遍同时满足两者。
- **2.4′（`sqlite-vec`）共用同一批向量** —— 它决定的是"向量存哪里、怎么查"，
  与 B 半同时做才不浪费那次嵌入。
- **2.7 接线依赖 2.3** —— 语料换成 chunk 后"检索结果就是 chunk 行"，
  定位字段自然贯通；先接线再换语料要改两遍。
- **2.5′（FTS5）独立** —— 不碰嵌入模型，可随时插入。

所以**下一步是一个决定而不是一项任务**：嵌入用哪个模型
（`bge-m3` 质量好但内存余量仅 0.5GB / `bge-small` 稳定但质量较低 /
暂缓向量先做 2.5′）。决定之后，2.2′ B + 2.3 + 2.4′ 可作为一次操作完成。

> **该决定已拍板并执行完毕**（选 `bge-m3`，实测可用内存抬到 6.35GB 后
> 加载仅占 1.7GB，全程稳定）。结果与**顺带测出的 RRF 缺陷**见附录 P。
> 本节的"依赖分析"仍然成立且有用，保留原文。

---

## 附录 P · 阶段 2.2′ B 半：统一向量索引，以及 RRF 的实测缺陷（2026-09-11）

### P.1 交付

**重嵌入**：608 个 chunk 全部用 `BAAI/bge-m3` 重新编码并落库。

| 项 | 结果 |
|---|---|
| `has_embedding = 1` | **608 / 608** |
| 向量按模型分布 | `BAAI/bge-m3` × 608（**单一模型**） |
| 向量按维度分布 | 1024 × 608（**单一维度**） |
| BLOB 长度与 `embedding_dim` 不符 | 0 |
| 模长偏离 1.0 超过 1e-3 | **0 / 608**（全部为单位向量，余弦 = 点积成立） |
| 零向量 | 0 |

对比修复前：1190 条向量里 509 条（43%）因维度不匹配**永久取不到**。

**检索层**：新增 `app/services/chunk_search_service.py` —— **一次 SQL**
取回用户全部已嵌入 chunk 并在内存里算余弦，取代"遍历 24 个 collection"
（D-9 / 2.4′）。定位字段（`char_start/char_end/heading_path/line_*`）
与向量**同源返回**，回跳不再依赖元数据穿过 Chroma 的层层转换。

**脚本**：`scripts/embed_chunks.py`（`--check` / `--batch-size` / `--limit`），
设计为**可中断续跑**：只处理 `has_embedding=0` 的行、每批单独提交、
每批检查可用内存，低于安全线主动中止而非等 OOM ——
半新半旧的混合索引比"没有向量"更难排查。

### P.2 关于内存：实测比预估宽松得多

附录 N.5 按"模型文件 4356MB"预估余量只有 0.5GB。实测：

| 时点 | 可用内存 |
|---|---|
| 加载前 | 6.35 GB（此前 4.51GB，用户释放后） |
| 加载 `bge-m3` 后 | 5.04 GB（**模型仅占约 1.3~1.7GB**） |
| 608 条编码过程中 | 稳定在 3.2~4.6 GB，未接近 0.6GB 安全线 |

即"文件 4356MB"不等于"常驻内存 4356MB"。**但保守设计仍然是对的**：
脚本的内存检查与分批提交在这次运行中没被触发，而它们是"若被触发就能
保住已完成的 592/608"的那层保障。

### P.3 向量通道质量（统一语料，可比）

附录 N.3 只能在 204 / 509 条的**碎语料**上测（覆盖率上限 26% / 48%），
数字不可比。现在同一批 608 chunk、同一模型、同一评测集：

| 通道 | 语料 | Recall@5 严格 | MRR 严格 | Recall@5 宽松 |
|---|---|---|---|---|
| 向量 `bge-m3` | 608（旧碎语料 204） | **48.39%**（旧 12.38%） | **0.3681**（旧 0.0973） | 72.78% |
| BM25 | 同一批 608 | 59.74% | 0.5183 | 85.73% |

上限达成率 `48.39 / 48.49 = 99.8%` —— 向量通道**几乎把可达上限做满了**。
它的绝对数字低于 BM25，但那不是缺陷（见 P.5）。

### P.4 🔴 RRF 融合的问题（**结论已修正两次，务必读完**）

> **⚠️ 本节初版的数字是错的。** 初版报告"RRF 融合严格 Recall@5 仅 32.04%，
> 比最好单通道低 28 个百分点"。**该结论已被自己推翻** —— 见 P.4.1。
> 保留下面的原始记录，是为了记住这个错误是怎么产生的。

#### P.4.1 初次测量（错误）与根因

初版测量结果：

| 候选池 | 向量 | BM25 | RRF 融合 | 融合 − 最好单通道 |
|---|---|---|---|---|
| 5 | 48.39% | 59.74% | 32.04% | −27.69% |
| 10 | 48.39% | 59.74% | 31.38% | −28.36% |
| 20 | 48.39% | 59.74% | 30.34% | −29.40% |

**根因：测量脚本把"截断到 200 字的去重键"当成了文档返回。**

融合实现以 `(note_id, content[:200])` 为去重键。测量脚本直接把这个**键**
当作文档列表返回给评测函数，于是评测看到的是每篇 200 字的字符串 ——
而完整 chunk 平均 400+ 字，答案常常落在被截掉的后半段。
同一份融合输出，**还原为完整内容后**：严格 `32.04% → 59.64%`。

定位过程（三种假设逐一排除）：
1. 融合键用内容还是下标 → **两者输出逐条相同**（同样的 idx、同样的分数）
2. 查询向量按下标取还是 `eval_set.index(case)` → 无差异
3. 候选池 5 / 10 / 20 → 都有 ~30% 与 ~57% 两个数，取决于取内容方式

排除前两条后，打印第一条查询的输出结构立刻暴露问题：
`融合输出 5 条，每条长度: [200, 200, 200, 200, 200]`。

**教训**：一个"反直觉且幅度巨大"的结论，先怀疑测量本身。
这次如果直接写进结论并据此删掉向量通道，就是拿一个测量 bug 当证据。

#### P.4.2 修正后的真实情况

同一批 608 chunk 语料、1058 条评测集，严格 Recall@5：

| 配置 | 严格 Recall@5 | MRR |
|---|---|---|
| 单通道 向量 | 48.39% | 0.3681 |
| 单通道 BM25 | 59.74% | 0.5183 |
| **等权 k=60（原实现）** | **57.37%** | 0.4441 |
| **加权 k=1 w=0.65（已落地）** | **60.87%** | **0.5185** |

所以真实情况是：

- 原实现的 RRF **略低于** BM25 单通道（57.37% vs 59.74%），
  但**远没有**初版说的 −28 个百分点 —— 是"小幅拖后腿"，不是"腰斩"
- 加权后**首次超过任一单通道**（+1.13% 相对 BM25，+3.50% 相对原实现）

即便如此，原实现的缺陷是真实的：`k=60` 把名次差异压平到 4%
（`1/61` vs `1/65`），分数几乎只反映"是否两路同时出现"，
RRF 退化为奖励**共识**而非相关性；两路强弱悬殊时等于把强通道拉向弱通道。
这与 A-14（"`k=60` 是照搬的默认值，没有针对场景调参"）一致。

#### P.4.3 参数标定（已落地）

在 k × w 网格上扫描（评测集 1058 条），选**稳健区域**而非网格尖点：

```
  k/w       0.5        0.6       0.65        0.7       0.75        0.8        0.9
    1  60.40%     60.68%     60.87%     60.87%     60.59%     60.40%     60.21%
    3  60.49%     60.59%     60.87%     60.87%     60.40%     60.40%     60.11%
    5  60.11%     60.68%     60.68%     60.78%     60.59%     60.40%     60.11%
   10  57.94%     60.11%     60.68%     60.59%     60.68%     60.40%     60.11%
   20  57.56%     58.79%     58.88%     59.36%     60.21%     60.59%     60.11%
   60  57.37%     58.22%     58.41%     58.51%     58.22%     58.32%     60.21%
```

`k∈[1,10]`、`w∈[0.6,0.8]` 全部在 60% 以上 —— 是一个**平台**，不是孤立尖点。
取 **k=1, w=0.65**（邻域均值最优）。另经实测：候选池 5 → 20 也贡献了提升
（池太小时融合只能在那 10 条里排序，答错就出局）。

> ⚠️ **这两个值绑定两路当前的相对强弱**（BM25 59.74% > 向量 48.39%）。
> 换嵌入模型、换语料或分块策略后**必须重扫**，否则会反过来压制变强的那一路。
> 重扫流程见附录 Q。

#### P.4.4 顺序影响

初版据此把"RRF 加权"提为下一项，理由写的是"正在造成 −28% 的损害"。
损害幅度修正为 −2.4 个百分点（相对 BM25 单通道），
但**加权仍然值得先做**：它是 +3.5 个百分点的确定性收益，
且是做 2.3/2.7 之前把链路调准的自然时机。

### P.5 这个发现**不**意味着"向量通道没用"

必须说清楚，避免走到另一个错误结论：

- 向量通道有 **24 条 BM25 完全命中不到**的结果（占 2.3%），这是真实的互补性
- 向量通道的上限达成率 99.8%，说明它**本身**工作正常
- 问题不在"要不要向量"，而在**怎么融合**

因此下一步是修融合，而不是删通道。可选方向（需用评测逐个验证）：

| 方向 | 说明 |
|---|---|
| **加权 RRF** | 给两路不同权重（如 BM25 权重更高）。最直接 —— 原文 2.6 就写的是"加权融合" |
| **调 `k`** | 减小 `k` 会拉开名次差异（`k=1` 时第 1 名 vs 第 5 名是 2.5 倍）。但要小心：`k` 小则单路噪声的头部也会被放大 |
| **分数归一化后融合** | 两路分数尺度不同（BM25 无上界、余弦有界），归一化后相加比 RRF 更能保留"某一路上非常强"的信号 |
| **只取最强通道 + 兜底** | 最简单：以 BM25 为主，向量只补 BM25 空手的情况 |

选哪个**必须用同一套评测决定**（`scripts/eval_retrieval.py` 已具备该能力），
不能凭直觉挑 —— 这正是本附录的方法论意义。

### P.6 验收

| 项 | 文件 | 验证 |
|---|---|---|
| 向量重嵌入 | `scripts/embed_chunks.py` | 608/608 单一模型单一维度；模长全部为 1.0 |
| 向量检索 | `app/services/chunk_search_service.py` | 一次 SQL；维度不符跳过而非硬算；定位字段同源返回 |
| 2.4′ 目标 | — | "查询从 N 次降到 1 次 SQL"达成（原 24 个 collection） |
| 加权 RRF | `app/services/rag_service.py`（`_rrf_fusion`） | 生产代码实测 60.87%；定位字段穿透融合层 |
| 融合参数 | `app/config.py` | k=1 / w=0.65 / 池 20，含取值依据注释 |
| 回归测试 | `tests/test_rag_retrieval.py`（38 用例） | 加权行为、k 的锐化效应、**去重键不得截断返回内容** |

测试总数 448 → **452 passed / 3 skipped**。
真库 `integrity=ok`；重嵌入前已单独备份 `data/chroma`
（`_backup/20260911-125750-pre-reembed`，54MB / 457 文件 ——
该目录**不在**原自动备份范围内，是本轮补上的）。

### P.7 仍未完成

- **2.3** 语料切换正式接进 `retrieve_context` 的向量路
- **2.7 接线**：定位字段贯通到 `AnswerSource` 与前端
- **2.5′** FTS5
- `embed_chunks.py` / `chunk_search_service.py` 的**回归测试**
  （本轮以实测数据验证，但缺少可重复的回归测试 —— 见附录 Q.3）

---

## 附录 Q · 融合参数标定流程与遗留项（2026-09-11）

### Q.1 标定流程（语料/模型变化后必须重跑）

`rag_rrf_k` / `rag_rrf_bm25_weight` / `rag_candidate_pool` 这三个值是
**在特定语料与模型上量出来的**，不是普适常数。以下任一变化后应重跑：

- 换嵌入模型（`bge-m3` → 其它，或反向）
- 换语料（卡片 → chunk，即 2.3）
- 改分块策略（`chunk_size`、是否启用 overlap）
- BM25 实现变化（如 2.5′ 换成 FTS5）

重跑方式（当前为一次性脚本，流水如下）：

1. 建两路候选池（各 `rag_candidate_pool` 条），**保留完整内容与下标**
2. 在 k × w 网格上逐点计算严格 Recall@5 / MRR
3. **选邻域均值最优的点**，而不是网格最大值 —— 后者可能是过拟合尖点
4. 把结果写入 `config.py` 的注释（含评测集规模与日期），便于下次对照

> ⚠️ 第 1 步的"保留完整内容与下标"是硬要求。附录 P.4.1 的假结论
> 正是因为测量脚本拿截断后的去重键当文档 —— 这个坑必须写进流程。

### Q.2 当前已落地的值与其含义

| 配置 | 值 | 含义 | 依据 |
|---|---|---|---|
| `rag_rrf_k` | 1 | RRF 平滑常数。越小名次差异越大 | k∈[1,10] 是平台，取邻域最优 |
| `rag_rrf_bm25_weight` | 0.65 | BM25 路权重（向量路 0.35） | w∈[0.6,0.8] 是平台 |
| `rag_candidate_pool` | 20 | 每路送入融合的候选数 | 池 5→20 有实测提升；池太小时融合没得排 |

权重 `0.65 > 0.35` 反映**当前** BM25（59.74%）强于向量（48.39%）。
这不是"BM25 本质上更好"，而是本语料上的实测相对强弱。

### Q.3 已知遗留：两个新模块缺回归测试

`scripts/embed_chunks.py` 与 `app/services/chunk_search_service.py`
本轮以实测数据验证（608/608 单一模型、模长全为 1.0、一次 SQL 检索），
但**没有可重复的回归测试**。风险具体在：

| 模块 | 无测试的风险 |
|---|---|
| `embed_chunks.py` | 续跑逻辑（只处理 `has_embedding=0`）、内存中止分支、模型降级检测 —— 这些路径只在异常时走到，手动测不到 |
| `chunk_search_service.py` | 维度不符时"返回 None 而非硬算"、跨用户隔离、模型混杂检测 |

已锁定的是**契约层**（`tests/test_chunks.py`：向量打包/解包、维度不符返回
None、`(note_id,index)` 唯一）。缺的是这两个脚本/服务的**行为**测试。

补测优先级：`chunk_search_service` 高于 `embed_chunks`
（前者在检索热路径上，后者是一次性运维脚本）。

---

## 附录 R · 阶段 2.3 / 2.4：统一语料与删除遍历逻辑（2026-09-11）

### R.1 交付

**2.3 语料切换**：两路（向量 + BM25）现在跑**同一份 chunk 语料**。

在此之前，向量路跑原文 chunk、BM25 路跑知识卡片 —— 两路粒度不同，
融合去重与引用回跳都无法自洽（A-17「三路混合检索实际是两套不同粒度的语料」）。
实测依据（附录 K，同一套 1058 条评测集、BM25 通道）：

```
chunk 语料 严格 Recall@5 = 59.74%（生产路径实测）
卡片语料   严格 Recall@5 = 37.52%
```

新增：

| 模块 | 作用 |
|---|---|
| `app/services/chunk_service.py` | 分块落库服务：`index_note_chunks` / `index_note_from_storage` / `get_user_chunks` / `chunk_hash` |
| `RAGService._get_user_chunks` | 取代 `_get_user_cards`，取 chunk 语料（**不过滤 `has_embedding`**） |
| `RAGService._search_chunk_vectors` | 在 `chunks` 表上做向量检索，**不再走 Celery** |

**2.4 删除跨 collection 遍历**：`embedding_tasks.py` 从 312 行降到 143 行
（**−169 行**），删掉 `_search_vectors_async`、`search_vectors` 任务、
`_collection_matches_current_model`。检索不再遍历 24 个 Chroma collection。

### R.2 关键设计决定

**向量检索不再走 Celery。** 旧路径要把 query 向量发给 worker、由 worker
遍历 24 个 collection。现在向量就存在本地 `chunks` 表里，检索只是点积，
**不需要模型** —— 省掉一次 worker 往返与 N 次 collection 查询。
模型加载仍隔离在 Celery（`_encode_via_celery`），那部分隔离是为避免
主进程段错误，与检索无关。

**`get_user_chunks` 刻意不过滤 `has_embedding`。** BM25 是纯词法检索，
不需要向量。这样"清洗完成但尚未跑嵌入"的窗口期里，新资料仍可被 BM25
检索到 —— **降级而非不可用**。若过滤掉，那个窗口里用户会看到
"新上传的资料完全问不出来"。

**清洗流程自动建索引**（`clean_tasks` 第 11 步）。`chunks` 是**派生数据**，
派生数据最常见的失效方式是"原文变了、索引没重建"（A-18 那一类）。
先前只有手动脚本 `index_chunks.py`，意味着**新笔记清洗后不会进索引**，
直到有人记得跑脚本。现在清洗完成后自动重建；失败**不抛异常**
（清洗的主产物是 clean.md 与状态，索引缺失时只退化为单通道检索）。

**写入前自证不变量**：`text[char_start:char_end] == content` 校验不过
**拒绝整篇落库**，不跳过单行 —— L.2 的两次失效都是整篇性系统漂移，
只丢一行会掩盖问题。

### R.3 端到端验证（真实数据，走生产路径）

```
1) _get_user_chunks        : 608 个 chunk，定位字段齐全
2) _search_chunk_vectors   : 5 条；自查询命中自身（相似度 1.0）
                             相似度 [1.0, 0.7691, 0.6839, 0.6597, 0.6443]
3) BM25（生产路径）        : 严格 Recall@5 59.74%  MRR 0.5183
   对照 卡片语料（旧）      : 严格 37.52%  MRR 0.3387
```

与附录 M.5.1 预测的 60.30% 有 **0.56 个百分点**差异，未查明来源。
已排除「回收站过滤」（实测 0 个 chunk 属于回收站笔记，两条路径都是 608 条）。
可能来源：评测脚本读 clean.md 时对 `clean_md_path` 缺失的笔记会回退到
`original_md_path`，而库里的 chunk 是按当时那份内容生成的。
差异小且方向一致（都远好于卡片语料），如实记录不做定论。

### R.4 为什么 Chroma 没有一起删掉

2.4 的表述是"删除 Chroma 依赖与 90+ collection 目录"。本轮删掉了
**检索侧的遍历逻辑**，但 `VectorStore` 仍被**清洗去重**使用
（`clean_tasks` 用它做 embedding 去重），因此依赖与 `data/chroma` 保留。

要彻底删除 Chroma，需要先把清洗去重也迁走（例如改用 `chunks` 向量算
块间相似度）。那是一次独立改动，且清洗去重目前工作正常 ——
不在本轮范围，记录为后续项。

### R.5 环境事故与恢复（必须记录）

本轮执行过程中发现 **`mineru_env` conda 环境被整体删除**
（`C:\Users\admin\anaconda3\envs\mineru_env` 不复存在，其余 7 个环境正常）。
`import asyncio` 都会失败，报 `DLL load failed while importing _socket`，
症状极具误导性 —— 看起来像 Python 装坏了，实际是解释器路径不存在。

损失清点：**项目数据零损失**（`engramnote.db`、4.3GB 嵌入模型、
`data/chroma`、`_backup` 全部完好），仅测试环境丢失。

恢复方式：以现有 `aiforlearn` 环境为基座建 `--system-site-packages` venv，
补装 `pytest` / `pytest-asyncio` / `ruff`，落地在 `backend/.venv`
（该路径已在 `.gitignore` 中）。

**恢复后测试结果 454 passed / 3 skipped，与删除前最后一次运行完全一致** ——
这既确认了 2.3 改动无回归，也确认了恢复的环境可用于本项目。

> ⚠️ 该 venv 基于 `aiforlearn` 的依赖版本（Python 3.10.20），
> 与 `requirements.txt` 的 `~=` 约束不保证逐项一致。
> 它足以跑测试，但**不要**把它当作部署环境。
> 若需要与项目完全一致的环境，应按 `requirements.txt` 重建。

### R.6 验收

| 项 | 文件 | 验证 |
|---|---|---|
| 分块服务 | `app/services/chunk_service.py` | 自动索引接入清洗流程；写入前自证不变量 |
| 语料统一 | `rag_service.py`（`_get_user_chunks`） | 生产路径 608 chunk；严格 R@5 59.74% |
| 向量检索换代 | `rag_service.py`（`_search_chunk_vectors`） | 自查询命中自身；定位字段齐全；回收站过滤有测试 |
| 删除遍历逻辑 | `embedding_tasks.py`（−169 行） | 全仓库无 `search_vectors` 调用方 |
| 测试 | `tests/test_rag_retrieval.py`（40 用例） | 含未嵌入 chunk 仍入词法语料、向量路回收站过滤、定位字段 |

测试总数 452 → **454 passed / 3 skipped**；ruff app tests scripts 全绿。

### R.7 仍未完成

- **2.7 引用回跳接线**（下一项）：定位字段已贯通到 `_search_chunk_vectors`，
  差接到 `AnswerSource` 与前端
- **2.5′** FTS5
- **Chroma 彻底移除**（R.4）：需先迁移清洗去重
- `embed_chunks.py` / `chunk_search_service.py` 的行为测试（附录 Q.3）

---

## 附录 S · 阶段 2.7：引用可回跳（2026-09-11）

### S.1 贯通链路（四层里原先丢了三层）

```
chunks 表          char_start/char_end/heading_path/line_*     ✅ 已有
  └→ 检索结果        _search_chunk_vectors 原样返回              ✅ 已有
       └→ RRF 融合    附加字段白名单已扩为定位字段集合            ✅ 附录 P.4.2
            └→ sources  build_context_and_sources 带上定位字段    ✅ 本次
                 └→ AnswerSource（Pydantic + OpenAPI）            ✅ 本次
                      └→ 前端 interface + 跳转 + 高亮             ✅ 本次
```

`AnswerSource` 新增 7 个可选字段：`chunk_id` / `chunk_index` /
`char_start` / `char_end` / `heading_path` / `line_start` / `line_end`，
已验证进入 OpenAPI 契约（前端类型据此更新）。

### S.2 修掉一个会直接误导用户的不一致

引用编号 `[N]` 原先按**融合排名**生成，而 sources 列表若按**原文位置**排序，
回答里的 `[1]` 就会指向 sources 的另一项 —— 用户点"第 1 条引用"跳到别处。
这类错位不报错，只会让人以为系统在胡说。

改为**同一次遍历**产出上下文与 sources：编号即 `sources` 下标 + 1。
该逻辑抽成纯函数 `build_context_and_sources`，因为原先内嵌在
`retrieve_context` 里、要调用 LLM 才能跑完，**关键一致性无法被任何测试覆盖**。

同时把去重键从 `note_id` 改为 `chunk_id`：按笔记去重会让同一篇笔记只保留
第一个 chunk，而用户看到的可能是该笔记第 3 段 —— 点过去跳到另一段。

### S.3 前端实现的关键难点：偏移无法直接映射到 DOM

后端给的是**源文**里的字符下标，而正文是 `renderMarkdown()` 产出的 HTML
（Markdown 标记已被去掉、段落被包进标签）。所以"源文第 1000~1500 字符"
**不能**直接换算成 DOM 位置 —— 这是本功能真正的难点。

采用的办法（`utils/citationJump.ts`）：

1. 用 `char_start/char_end` 从 Markdown 源文切出 chunk（精确，后端保证一致）
2. `stripMarkdown()` 把切片规范化成"可在渲染文本里搜索的指纹"
   （去行内代码/链接/标题号/列表号、压缩空白 —— 渲染后这些标记不存在了）
3. `TreeWalker` 在容器纯文本里找指纹，用 `Range.surroundContents` 包 `<mark>`
4. 逐级退化的候选：完整指纹 → 40 字 → 20 字

**`view=clean` 是必需的**：chunk 偏移基于 clean 副本计算，
若页面显示 original 副本，同一组偏移指向**另一段文字**。
`NoteDetail` 的跳转 effect 因此强制切到 clean 视图。

### S.4 必要的失败退化（不给错误的高亮）

指纹可能因行内格式差异而搜不到。此时**必须**有明确失败路径：

| 情况 | 行为 |
|---|---|
| 找不到指纹 | 滚动到容器开头，并 toast 提示"未能定位到引用段落" |
| `surroundContents` 跨元素抛错 | 同上（区间跨多个元素时必然抛错） |
| 引用缺 `char_start/char_end` | **不提供跳转**，显示"（无定位）"并置灰 |

第二、三条是刻意的：**给出错误的高亮比不给更糟** —— 用户会据此以为
"引用内容就是被高亮的那段"，从而得出错误结论。

### S.5 交付与验收

| 项 | 文件 | 验证 |
|---|---|---|
| 引用构建（可测纯函数） | `app/services/rag_service.py`（`build_context_and_sources`） | 编号一致性、按 chunk 去重、定位字段、标题回退 |
| API 契约 | `app/schemas/knowledge.py`、`api/understanding.py` | 7 字段进 OpenAPI |
| 高亮工具 | `frontend/src/utils/citationJump.ts` | 指纹构建、Markdown 剥离、失败退化 |
| QA 页跳转 | `frontend/src/pages/QA.tsx` | 带 `?view=clean&cs=&ce=`；缺定位时才不给跳转 |
| 详情页定位 | `frontend/src/pages/NoteDetail.tsx` | 强制 clean 视图；高亮后清理 URL 参数 |
| 面板内跳转 | `frontend/src/components/NoteAskPanel.tsx` | 当前笔记内直接定位（不跳路由） |
| 高亮样式 | `frontend/src/styles/markdown-extras.css` | `.citation-highlight`，与批注高亮刻意区分 |

测试：后端 **463 passed / 3 skipped**；`ruff app tests scripts` 全绿；
前端 `tsc --noEmit` / `eslint` / `npm run build` 全部通过。

### S.6 仍未完成

- **前端缺少单元测试**：`citationJump.ts` 的指纹与退化逻辑只有实现、
  没有测试（前端工程当前无测试框架，属既有缺口，见 §2.8 F-16 一类）
- **未做真实浏览器验证**：无法在此环境里点开页面确认高亮视觉效果，
  仅验证了类型、lint、构建与后端字段贯通
- **2.5′** FTS5（见附录 T）
- `embed_chunks.py` / `chunk_search_service.py` 的行为测试（附录 Q.3）

---

## 附录 T · 阶段 2.5′：FTS5 全文索引（2026-09-11）

### T.1 交付

把词法检索从"每次查询在 Python 里全量建 BM25 索引"换成 SQLite 内置的
FTS5 倒排索引（A-5 在 SQLite 路线下的正解；PG 路线的 `pg_bigm` 不执行）。

| 实测（1058 条评测集、608 chunk 语料，生产路径） | Recall@5 | MRR | 无结果比例 |
|---|---|---|---|
| **FTS5 + bigram（已落地）** | **60.21%** | **0.5258** | **0.00%** |
| Python BM25（旧） | 59.74% | 0.5183 | — |

新增 `app/services/fts_search_service.py`；`rag_service._lexical_search`
FTS5 优先、Python BM25 兜底（FTS5 是编译期选项，精简构建可能没有 ——
词法通道不该因一个可选扩展缺失而整体不可用）。

### T.2 为什么用 bigram 预切词而不是内置的 `trigram`

SQLite 内置的 `trigram` 是唯一原生支持中文的分词器，但实测**更差**：

| 实现 | Recall@5 | MRR | 无结果比例 |
|---|---|---|---|
| FTS5(`trigram`) | 57.84% | 0.5052 | 0.76% |
| FTS5(bigram 预切词) | **60.21%** | **0.5258** | **0.00%** |

中文三元组重叠严重、区分度弱；而现有实现本来就用 2-gram
（`rag_service._tokenize`）。因此**保持 2-gram 口径不变**，只把索引与排序
下沉到 SQLite —— 这样比较的是**实现**，而不是顺带改了分词策略。

FTS5 没有内置中文 bigram 分词器，做法是写入侧切词、结果存进
`chunks.grams`（空格分隔），FTS 用内置 `unicode61`（按空白切）索引它。

### T.3 查询表达式：踩了两次"0 结果"

失败模式都**不报错**，只是返回 0 条：

1. **整句加引号 → 0 条**：FTS5 里引号表示**短语匹配**，要求整串逐字出现。
   自然语言问句不会逐字出现在资料里 ——
   `"截至2013年底，我国并网风电装机容量为多少？"` 返回 0，而 `"截至2013"` 返回 1。
2. **全部 bigram 用 AND → 0 条**：那等于要求问句的每个字都出现在同一段资料，
   而问句含"是多少"、"如何处理"这类疑问语气词，资料中不会有。

正解：**OR 组合召回、`bm25()` 排序** —— 词法检索的标准形态（召回与排序分离），
也正是"索引下沉"想要达到的形态。

### T.4 接线踩到的两个坑（都不报错）

**(1) `content_rowid='rowid'` 指向 VARCHAR 主键的别名 → JOIN 静默返回 0 条**

`BaseModel` 的 `id` 是 VARCHAR 主键，SQLite 会把它当作 `rowid` 的别名，
于是 `content_rowid='rowid'` 实际指向**字符串 id**，而倒排索引的 rowid 是整数。
症状极具误导性：`MATCH "浮充"` 单独查 FTS 表返回 30 条，
但 `chunks_fts JOIN chunks ON c.rowid = f.rowid` 返回 **0 条**。

修法：给 `chunks` 加显式整数主键 `chunk_rowid`，`content_rowid` 指向它。
注意 `id` 必须降为普通唯一列 —— 否则 `id` + `chunk_rowid` 构成**复合主键**，
而 SQLite 不支持复合主键上的 autoincrement（建表直接失败）。

**(2) 外部内容表的索引不能用 `INSERT INTO fts(rowid, col) SELECT ...` 建**

那只写倒排索引、不认内容表，查询返回 0 条。必须用官方
`INSERT INTO fts(fts) VALUES('rebuild')`。

### T.5 同步策略：整表 rebuild（以及为什么不用增量）

FTS5 外部内容表的**单条同步命令形式全都不可用或不安全**，逐条实测过：

| 做法 | 结果 |
|---|---|
| `INSERT INTO fts(fts, grams) SELECT 'delete', grams ...` | `SQL logic error` |
| `INSERT INTO fts(fts, rowid, grams) SELECT 'delete', chunk_rowid, ...` | delete 成功、insert 报 `SQL logic error` |
| `DELETE FROM fts WHERE rowid IN (...)` + 直接 INSERT | **`database disk image is malformed`** —— 直接改倒排索引破坏索引结构 |
| `INSERT INTO fts(fts) VALUES('rebuild')` | ✅ 正确：删除与新增都生效 |

因此 `reindex_note` 就是整表 `rebuild`。**代价如实说明**：每篇笔记清洗完成后
重建全表索引（当前 608 行、毫秒级；清洗本身是秒级以上重任务，相对开销可忽略）。
语料上到十万级时应重新评估。

这个取舍换的是**语义正确**：整表重建不可能留下指向已删除内容的悬空索引项，
而"索引与正文漂移"是最难发现的一类检索缺陷。

### T.6 另一个性能坑：不要在 `init_db` 里无脑 rebuild

第一版每次 `init_db()` 都 rebuild，而测试套件会创建上百个临时库 ——
整个套件从 **70 秒膨胀到 190 秒**。改为**只在首次建表时** rebuild；
稳态下索引由 `chunk_service.index_note_chunks` 维护。

### T.7 验收

| 项 | 文件 | 验证 |
|---|---|---|
| FTS5 服务 | `app/services/fts_search_service.py` | 分词、查询表达式、检索、同步 |
| 建表与迁移 | `app/database.py`（`_ensure_fts_index`、`_bigrams_for_migration`） | 全新库与真库迁移均验证；真库 608 行已回填并建索引 |
| 检索接线 | `rag_service._lexical_search` | FTS5 优先 + Python BM25 兜底 |
| 测试 | `tests/test_fts_search.py`（15 用例） | 含 **rowid JOIN 一致性**、两处 bigram 实现一致、特殊字符查询、索引残留 |

测试总数 463 → **478 passed / 3 skipped**；ruff app tests scripts 全绿；
真库 `integrity=ok`，`chunks` 608 行、`grams` 全部回填、FTS 索引 608 条。

### T.8 换掉词法通道后重新标定融合权重（附录 Q.1 要求的步骤）

换检索实现后必须重扫融合参数。实测（同一评测集，`k=1` 固定）：

| 配置 | Recall@5 | MRR |
|---|---|---|
| 单通道 向量 | 48.39% | 0.3681 |
| 单通道 词法（FTS5） | 60.21% | 0.5258 |
| **融合（w=0.65，即当前配置）** | **60.68%** | **0.5210** |

权重扫描（w 0.30→0.90）：严格 Recall@5 在 **w∈[0.55, 0.80] 全部落在
60.4%~60.7%**，是一个平台；MRR 在 w≥0.65 后趋于平坦（0.521~0.527）。

**结论：当前 `rag_rrf_bm25_weight=0.65` 无需改动。** 融合比最好单通道
（词法 60.21%）高 **+0.47 个百分点** —— 融合终于产生了正收益
（附录 P.4 时等权实现是负收益）。

### T.9 仍未完成

- **Chroma 彻底移除**：需先迁移清洗去重（附录 R.4）
- `embed_chunks.py` 行为测试、`citationJump.ts` 前端测试
- **环境说明**：本轮在 `aiforlearn` 环境执行（`mineru_env` 被其他任务覆盖、
  待恢复）。为加载嵌入模型，向该环境装了 `sentence-transformers 5.7.0`
  并升级 `huggingface_hub` 到 0.36.2（二者版本必须匹配：旧版 ST 需要
  已被移除的 `huggingface_hub.cached_download`）。
  该环境**原本就有依赖冲突**（`datasets` / `mineru` / `qwen-asr` 等），
  与本轮改动无关。若需恢复原版本：
  `pip install sentence-transformers==2.2.2 huggingface_hub==0.16.4`。

---

## 附录 U · 阶段 2.4 收尾：彻底移除 Chroma（2026-09-11）

### U.1 前置：清洗去重不再依赖向量库

原以为"删除 Chroma"卡在"清洗去重需要它"（附录 R.4）。读代码后发现
**这个依赖是虚的**：`VectorStore.find_duplicates` 把向量写进 Chroma，
随后 `collection.get()` 把**全部向量读回来**，然后在 Python 里做两两循环 ——
它**根本没有用到向量库的检索能力**（不是 HNSW 近邻查询，就是两个嵌套 for）。

整趟 Chroma 往返只是为了"把刚算好的向量存起来又取回来"。

改为 `cleaning_service.find_duplicates_by_embedding(chunks, embeddings)`：
**数学完全相同**（同一个 `compute_similarity`、同样的两两循环、
同样的 overlap 跳过规则、同样的"保留首次出现"策略），
但直接消费 `clean_tasks` 里已算好的 `embeddings`，省掉一次写库 + 一次读库。

### U.2 🔴 顺带发现并修掉一个真实回归：删除笔记会撞外键

`chunks.note_id` 的外键是 **NO ACTION**（不是 CASCADE），而
`PRAGMA foreign_keys=ON` 已在每个连接上生效。引入 `chunks` 表（2.2′）时
忘记在删除路径里处理它 —— 实测：

```
删除笔记（未删 chunk）: 失败 -> IntegrityError: FOREIGN KEY constraint failed
```

也就是说**上一轮上线 `chunks` 表之后，删除任何有 chunk 的笔记都会报错**。
这是"新功能把老功能弄坏"的典型，且只在真正删笔记时才暴露。

值得与 M-4 对照记下：M-4 经复核**不存在**（那条 UPDATE 没有 `note_id` 限定，
跨笔记引用已被覆盖）；**这一条是真实存在的**，由本轮引入、由本轮发现。

修法：在 `purge_note` 里与 `KnowledgeCard` 并列显式删除 chunk。
模型上另加 `ondelete="CASCADE"` 作纵深防御，但**不依赖它** ——
已有库的旧表不会因模型改了 ondelete 就重建，`CREATE TABLE` 里的
ON DELETE 子句才是实际生效的那个。

测试（`TestPurgeNoteChunks`）含**对照实验**：先断言 `PRAGMA foreign_keys=1`
且"不删 chunk 直接删笔记会被拒绝"，再断言 purge 能成功 —— 与 M-4 那组
用同一个手法：**先证明约束是活的，再证明代码满足了它**。
并已验证"撤掉修复后测试确实失败"。

### U.3 删除清单

| 项 | 变化 |
|---|---|
| `embedding_service.VectorStore` | **删除整个类，−224 行**（文件 582 → 358 行） |
| `note_service` 的 Chroma 清理块 | 删除（向量已随 `chunks` 表在第 4.5 步一并删除） |
| `config.chroma_dir` | 删除配置项，就地留注说明为何移除 |
| `logging_config` 的 chromadb 降噪 | 删除 |
| `requirements.txt` | `chromadb~=0.4.0` 注释掉并说明 |

**验证**：应用在**未安装 chromadb** 的环境里正常导入运行（118 个路由）——
这本身就是"依赖确实已可选/无用"的证明；此前 `note_service` 那段清理
一直在静默失败（日志里的 `No module named 'chromadb'`）。

`data/chroma/`（54MB）已成为历史数据，可删除；删除前建议保留
`_backup/20260911-125750-pre-reembed` 那份副本。**本轮未删除它** ——
磁盘操作等确认。

### U.4 验收

| 项 | 验证 |
|---|---|
| 去重迁移 | 61 个真实 markdown 端到端跑通（分块→去重→生成副本），0 异常 |
| 长度不一致防护 | `chunks` 与 `embeddings` 数量不符时**返回空并告警**，不错配向量与文本 |
| 删除笔记 | `TestPurgeNoteChunks`（3 用例）含 FK 对照实验；已验证撤掉修复即失败 |
| 依赖可选 | 未装 chromadb 时应用正常导入与运行 |

测试总数 478 → **481 passed / 3 skipped**；ruff app tests scripts 全绿；
真库 `integrity=ok`，`chunks` 608 行、FTS 索引 608 条、**孤儿 chunk 0**。

### U.5 仍未完成

- **行为测试补全**：`embed_chunks.py`；`citationJump.ts` 无前端测试（Q.3/S.6）
- **`data/chroma/`（54MB）与 `backend/data_backup_e2e/`（4.67GB）的删除**：
  等确认
- **阶段 3**（学习核心）尚未开始

---

## 附录 V · 阶段 3.5：简答题 LLM 语义判分（2026-09-11）

### V.1 交付

| 部件 | 位置 | 说明 |
|---|---|---|
| 语义判分 | `llm_service.grade_short_answer` | 返回 `verdict` / `missing_points` / `misconceptions` / `confidence` / `reason` |
| 口径转换 | `sm2_service.grade_short_answer_semantically` | 转成与 `grade_answer` **同构**的 dict（`method="semantic"`） |
| 调度接入 | `review_service.submit_answer` | 简答题 + 未自评 + **显式请求**时启用 |
| 落库 | `review_logs.grading_detail`（新 JSON 列） | 结构化明细，供 UI 展示与复盘 |
| API | `SubmitAnswerRequest.use_semantic_grading` | 默认 `false` |

### V.2 输出为什么不是 0-100 分

计划明确要求 `verdict + missing_points + misconceptions`。一个 0-100 的数字
**没有可校准的语义** —— 模型给 62 分和 58 分意味着什么？没人说得清，
也无法据它改进。而"缺了哪一点、误解了哪一点"是**可展示、可核对**的。

三档 verdict 而非"对/错"：`partial`（说对部分）单独存在是必要的 ——
二档会把"说对一半"强行归到某一侧，而它对"该不该缩短间隔"有实质影响。

### V.3 两个关键映射决定

**`partial` → quality 3（不是 2）。** SM-2 里 `quality >= 3` 才算答对。
把"说对一半"判成 2 会让它算作答错、**重置间隔** —— 用户明明记住了一半
却被当作完全没记住重新开始。这比"把半分当及格"更伤：过早重置会让
长期复习永远推进不下去。

**`correct` 分 4/5 两档。** 高置信度（≥0.85）给 5，一般置信度给 4。
这样"模型很确定"与"判断对但把握一般"不会得到相同的调度后果 ——
后者增长慢一点，是廉价的纠错余量。

### V.4 降级路径：`None` 不等于"答错"

判分调用失败、JSON 解析失败、`verdict` 不在三档内、置信度 < 0.7 ——
任一情况都返回 **None**，调用方退回自评占位。

**这一点是本模块最重要的正确性要求**：把 None 当"答错"会让用户答对的题
被重置间隔，比"没判分"严重得多。置信度门槛的存在同理 ——
勉强采信会让不确定的判定**进入调度**，而间隔一旦改变**无法事后纠正**。

### V.5 🔴 一个被实测拦下的设计错误：默认调用会让每次提交等 ~10 秒

第一版把语义判分设为**无条件尝试**（简答题 + 未自评即调用）。
接入后测试套件从 **70 秒涨到 183 秒**，`test_self_rating.py` 每个用例
10~14 秒。排查发现：

- 这些用例正是「简答题 + 未自评」组合 → 每次都在调 LLM
- 全局 `llm_timeout_seconds=600`、`llm_max_retries=5`（每次 1s 退避）
  → 即使网络立刻失败也要 **~10 秒**（5 次退避），生产里若网关慢则更久

而这是**用户提交答案的同步路径** —— 复习是高频操作，
让它每次都等一次外部 LLM 往返是明显的得不偿失。

修法两步：

1. 给判分加独立超时 `SEMANTIC_GRADE_TIMEOUT_SECONDS = 20`（远小于全局 600），
   避免"挂死"。但这**只护住极端情况**，挡不住 5 次重试的 ~10s 延迟。
2. **改为默认关闭、由客户端显式请求**（`use_semantic_grading=true`）。

第 2 步才是正解：两阶段流程本来就以**用户自评为主评分来源**，
自动判分是增强而非前提。改完后 `test_self_rating.py` 从 135 秒回落到
**3.7 秒**（29 个用例）。

这个取舍也让 3.5 与 3.4 的关系更清楚：**自评是主、语义判分是辅**。
若将来实测证明语义判分足够可靠、可以取代自评，再把它改为默认开启 ——
那需要先有校准数据（正是 3.14 要做的事）。

### V.6 验收

| 项 | 文件 | 验证 |
|---|---|---|
| LLM 判分 | `llm_service.grade_short_answer` | 三档校验、置信度裁剪、异常返回 None |
| 口径转换 | `sm2_service.grade_short_answer_semantically` | 与 `grade_answer` 同构；阈值边界 |
| 落库与 API | `review_logs.grading_detail`、`SubmitAnswerRequest` | 真库迁移成功（194 行不变、`integrity=ok`） |
| 测试 | `tests/test_semantic_grading.py`（14 用例） | 含"失败必须返回 None 而非答错"、partial 映射、缺失点露出 |

测试总数 481 → **495 passed / 3 skipped**；ruff app tests scripts 全绿。

### V.7 仍未完成

- **3.6 FSRS**：下一步。`review_states` 仍无 `stability`/`difficulty` 字段
  → ✅ **已完成**（附录 W，2026-09-11）
- **前端**：`grading_detail` 已随响应返回，但**未接 UI**（复习页尚未展示
  "缺了哪一点 / 误解了哪一点"）
  → ⚠️ **这句话在 2026-09-11 核对时被推翻：它当时根本没随响应返回。**
  `review_service` 确实把 `result["grading_detail"]` 填好了，但
  `SubmitAnswerResponse` **没有声明这个字段**，Pydantic 静默丢弃 ——
  "计算了、存了、就是没返回"。详见附录 Z.2。
  前端 UI 也已在附录 Z 补齐。
- **`_finish_*` 的前端开关**：`use_semantic_grading` 默认 false，
  前端尚未提供入口
  → ✅ **已补齐**（附录 Z.4）

---

## 附录 W · 阶段 3.6：SM-2 → FSRS-5（2026-09-11）

### W.1 交付

| 部件 | 位置 | 说明 |
|---|---|---|
| FSRS-5 算法 | **新增** `services/fsrs_service.py` | 纯函数；19 个公开默认参数；R/S/D 三类公式 + 状态机 |
| 调度门面 | **新增** `services/scheduler_service.py` | `advance(state, quality, method, now) → ScheduleOutcome`；算法选择与口径转换 |
| 落库 | `review_state_service.apply_schedule_result` | 统一口径落库 + 旧字段镜像；`apply_sm2_result` 降级为兼容包装 |
| 接线 | `review_service.submit_answer`、`api/review.py::submit_card_review` | **两条路径合并到一个入口**（原先各有一份手抄的 SM-2 调用） |
| 模型 | `review_states.stability` / `.difficulty`（新列，nullable） | 记忆状态；NULL = 尚未被 FSRS 调度 |
| 模型 | `review_logs.rating` / `.predicted_retention` / `.item_type`（新列） | **顺带完成 3.2 的事件流化**（理由见 W.8） |
| 配置 | `review_scheduler`（`fsrs`/`sm2`）、`fsrs_request_retention`（0.9）、`fsrs_max_interval_days`（3650） | 回退开关 + 两个产品旋钮 |
| 测试 | `tests/test_fsrs.py`（**62 用例**） | 公式对拍 / 性质 / 状态机 / 接管 / 口径 / 端到端 / 回退 |

真库迁移实测：两个表的新列已就位，`review_logs.item_type` 回填 194 行
全部为 `quiz`，行数**一行未变**（users 4 / notes 22 / cards 1183 /
quiz_items 1058 / review_logs 194 / review_states 2241 / chunks 608，
FTS 608），`integrity=ok`、`journal_mode=wal`；
`stability` 非空 **0 行** —— 符合设计，历史行在各自下一次复习时自行接管。

### W.2 为什么非换不可：SM-2 的三条缺陷里只有一条能靠打补丁修

改造前的 `calculate_sm2` 里，间隔完全由 `repetition` 与 `EF` 决定：

```python
if quality >= 3:
    new_repetition = repetition + 1
    if new_repetition == 1:   new_interval = 1
    elif new_repetition == 2: new_interval = 6
    else:                     new_interval = interval * new_ef
```

1. **`quality` 的强度不影响本次间隔** —— 补一个系数可以修
   （SM-2+ / SM-15 就是这么做的）；
2. **没有时间维度**：模型里不存在"当前能想起的概率"，
   所以掌握度只能用"答对过几次"近似（见 `mastery_service` 的说明）。
   这**不是**调参能修的：SM-2 的状态里根本没有这个量；
3. **不看"隔了多久才复习"**：间隔效应是记忆科学里最稳的结论之一，
   而 SM-2 的公式里没有它的位置。

FSRS 引入 `S`（记忆强度）与 `R`（当前可回忆概率）后，2 与 3 同时消失：
`R` 正是 3.9 需要的那个量，而"隔得久还答对 → S 涨得更多"是公式里的一项
（`exp(w10*(1-R)) - 1`）。三条方向各有独立的单调性测试盯着，因为
**它们就是换算法的理由本身** —— 如果这些性质不成立，换过来也没有意义。

### W.3 参数从哪来：19 个数字是**拟合结果**，不是旋钮

```python
DEFAULT_W = (0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604,
             0.0046, 1.54575, 0.1192, 1.01925, 1.9395, 0.11, 0.29605,
             2.2698, 0.2315, 2.9898, 0.51655, 0.6621)
```

取自 open-spaced-repetition 的公开算法说明（FSRS-5，19 参数）。
模块注释里写死了一条纪律：**不要凭记忆改这些数字** ——
手改它们等于宣称自己比公开数据集更懂用户的记忆。要个人化就应该走
参数拟合（3.14），而不是猜一个"看起来更合理"的值。

同理没有引入 `fsrs` 第三方包：公式固定、实现是纯算术，
项目其余部分（`_levenshtein`、`to_bigrams`、分段器）同样自实现。
真正需要外部工具的是**拟合器**，那是 3.14 的事，
且它产出的仍然只是"另一组 19 个浮点数"，`DEFAULT_W` 就是预留的插槽。

### W.4 🔴 口径转换：`quality`(0-5) → `rating`(1-4) 取决于**谁给的分**

这是全篇最容易搞错、后果也最直接的一处。

| quality | SM-2 语义 | rating | 说明 |
|---|---|---|---|
| 0,1,2 | 没想起来（`quality < 3`） | Again | SM-2 的及格线就是 3 |
| 3 | 勉强正确，很费力 | Hard | |
| 4 | 正确但有些犹豫 | Good | |
| 5 | 完美、毫不费力 | Easy | **仅当用户自评时** |

⚠️ **机器判分得到的 `quality=5` 不等于 Easy**。`_grade_choice` 只要选对
就给 5，但"选对了"里没有"毫不费力"这层信息（可能是蒙对的）。
若照搬成 Easy，`new + Easy` 会直接进长期复习并排到 **16 天后**
（`S0(Easy) = 15.69`）—— 而用户只见过这张卡一次。

所以机器判分（choice / fill_blank / semantic / ungraded / legacy）
一律**封顶 Good**。这是 `_MACHINE_QUALITY_TO_RATING` 存在的唯一理由，
并有专门的测试盯着（`test_machine_grade_never_yields_easy`、
端到端的 `test_choice_correct_is_good_not_easy`）。

这个取舍与 3.5 里"不给 LLM 判 0-100 分"是同一个原则：
**只使用信号里真正存在的信息**。

（用答题耗时推断"轻松程度"是可能的改进方向 —— `time_spent_ms` 一直在记 ——
但那是新的启发式，需要单独验证，不能顺手塞进口径转换里。）

### W.5 旧行**接管**而不是清零（症状 D-3 的同型风险）

真库里有 2241 行 SM-2 时期的状态，它们的 `stability` 都是 NULL。
把"没有 S"当成"新卡"会让**所有历史进度归零** —— 这正是
overhaul-plan 症状 D-3（重跑理解丢学习历史）的形态，只是这次由换算法触发。

换算用的是 FSRS 对 S 的**定义本身**，不是近似：

```
I(0.9, S) == S   ⟹   S := 当前 interval_days
```

SM-2 的 `interval` 语义是"下次复习间隔"，而它隐含的目标就是
"到那时还记得的概率约 90%"（EF 的调整规则围绕及格线转），
两者指的是同一件事。难度由 EF 反解（W.7）。
两个方向都是可逆映射，所以这次交接**不丢信息**，也不需要一次性数据迁移 ——
每行在它下一次被复习时自行完成换算。

对应的关键回归测试是 `test_legacy_progress_is_not_reset`：
一张间隔 15 天的 SM-2 卡，首次 FSRS 复习后间隔**必须 > 15 天**。
这个 bug 在界面上完全看不出来（卡片照常出现，只是排得比以前早），
只有这条断言能拦住它。

### W.6 状态机：哪部分是 FSRS，哪部分是本项目的策略

必须分清，否则以后没人知道"新卡答对隔 1 天"到底能不能改：

| 规则 | 归属 |
|---|---|
| S/D 的更新公式、R 的定义、间隔反解 | **FSRS 的规定部分**，有公开公式可逐条对拍 |
| `new + Easy` 直接进长期复习 | 本项目策略 |
| `new/learning + Again/Hard/Good` → 学习步 **1 天** | 本项目策略 |
| `learning/relearning + ≥Hard` → 毕业进 review，间隔由 S 解出 | 本项目策略 |
| `review + Again` → relearning，1 天 | 本项目策略（`stability_after_forget` 保证残余强度不丢） |
| 间隔 ≥ 1 天、≤ `fsrs_max_interval_days` | 数值护栏 |

学习步取 1 天的理由：与 SM-2 时期"首次答对隔 1 天"保持一致 ——
新学的东西当天记住、第二天确认一次，比隔三天更符合直觉，
也让"学完立刻复习"这条最常用的路径不因换算法而突变。
`fsrs_service._next_state_and_interval` 的注释里逐条写了这些理由，
它们是被测对象而不是被测前提。

**`schedule()` 里还有一个容易漏的分支**：同日复习（`elapsed < 1`）
必须走 `stability_short_term`。否则 t=0 → R=1 →
`exp(w10*(1-R)) - 1 = 0` → 成功复习给出的 S' **恰好等于 S**，
于是"当天没想起来、当天再看一遍"对记忆状态毫无影响。
这条有专门的回归测试。

### W.7 EF 桥接：为什么不把 `easiness_factor` 冻住

换算法后 `easiness_factor` 仍在被读取（卡片复习列表、掌握度、
以及回滚后的 SM-2 路径），但它不再被 SM-2 更新。留着一个停更的字段
有两种坏结果：它看起来是最新值却其实过期（**无法与"正确"区分**），
以及回滚后带着一个几年前的 EF 继续跑。

因此按 `EF = clamp(2.5 + (5 - D) * 0.15, 1.3, 2.8)` 做单调桥接，
保住这一列的**语义**（越大越容易、范围 [1.3, 2.8]）——
而不是把 FSRS 的难度直接塞进一个叫 `easiness_factor` 的列里让列名说谎。
D=5 ↔ EF=2.5 取在两者的默认值上，接管时难度因此不会凭空跳变。

⚠️ 这个映射**不是满射**：D < 3 的部分全部落到 EF=2.8，反向只能得到 3.0，
即有损。这可以接受：真库里的 EF 本来就被 SM-2 夹在 [1.3, 2.8] 内，
"取回来"不会比原来更糟；而 D < 3 只可能由 FSRS 自己产生，
那时 `stability/difficulty` 已非 NULL，走直通分支，
根本不经过这个桥。两条边界都有测试记录。

### W.8 顺带完成 3.2：`predicted_retention` 是一扇**单向门**

阶段 3.2 列的三列本来可以留到以后再补，但 `predicted_retention` 不行：
它是"调度器**在复习发生之前**预测的可回忆概率"，只能在复习那一刻写下。
事后重建需要当时的 S，而 S 已被这次复习更新 —— 没写就是永远没有。
校准曲线（3.14：预测 0.9 的那批卡实际答对多少）完全依赖它，
所以哪怕 3.14 还没做，也必须现在开始积累。

三个刻意的选择：

1. **SM-2 路径下留 NULL，而不是用 SM-2 的近似公式填数**。
   SM-2 没有保持率模型，填进去的数字是我们的发明而不是那个算法的输出，
   而校准曲线一旦混入两种来源的数字，分母里就混着不是"预测"的东西。
2. **`rating` 不复用 `quality`**：两者是不同尺度（W.4），
   要在同一列塞两种尺度就必须再加一列记"这行属于哪个尺度"，反而更贵。
   FSRS 参数拟合直接消费 `rating`，它必须无歧义。
3. **历史行回填 `item_type`**：判据是"`quiz_id` 是否为空"，
   这是**如实**的判断（那一列本来就是这么用的），不是猜测；
   两个 id 都为空的异常行保持 NULL。

配套的回归测试是 `test_predicted_retention_is_pre_review_value`：
一张 S=10 天的卡隔 10 天复习，预测值必须恰为 0.9。
若把 R 的计算挪到落库之后，得到的是"用复习后的 S 算出来的事后数字"，
它总在 1.0 附近 —— 校准曲线随即变成一句永远乐观的空话。

### W.9 回退开关：切回 SM-2 时**清空** S/D

`config.review_scheduler` 默认 `fsrs`，可切 `sm2`。留这个开关的理由：
换调度算法会改变**每个用户**的复习节奏，而节奏错了要过几天才察觉
（卡片迟迟不再出现 / 间隔突然暴涨）。一个配置项就能在不回滚版本的前提下
退回已验证多年的旧行为。

切回 SM-2 时 `stability`/`difficulty` 被**清空**而不是保留。两个理由：

- 保留会让"NULL ⟺ 当前由 FSRS 调度"这条不变量失效，
  而 `schedule()` 恰好用它选分支（是否要用 interval/EF 接管）；
- SM-2 改了 `interval` 却不会改 S，留下的 S 与刚写的 interval
  **互相矛盾**（S 的定义就是 interval 在 R=90% 时的值）。

清空后语义重新干净，切换成本只是"下一次 FSRS 复习时重新接管一次"。
`test_switch_clears_fsrs_state` 覆盖这条。

另外，SM-2 路径的阶段推导在 `scheduler_service.sm2_kind` 里
**逐字保留**了改造前的规则（成功 → review，失败 → relearning）——
回退开关的意义是行为完全不变，而不是"顺便变得更合理"。
`derive_kind`（另一条规则，用于从旧字段补建状态）因此没有被复用，
两者的差异在代码注释里写明了。

### W.10 顺带修掉的一处分叉：两条复习路径合并

改造前 `api/review.py::submit_card_review` 里有一份**手抄的 SM-2 调用**，
与 `review_service.submit_answer` 各算一次。平时它们一致，
所以问题不会显形；但换算法时只要漏改一处，
卡片复习会继续按 SM-2 排期 —— 而两条路径写的是**同一批**
`review_states` 行，同一张卡在两条路径间来回切换就会得到互相矛盾的间隔。

现在两条路径都调 `scheduler_service.advance`，并有
`TestCardReviewUsesSameScheduler` 盯着（断言卡片复习也写出了 FSRS 状态）。

同类的另一处修正：`review_service` 原先在拿不到复习状态时会**静默不推进调度**
（`if sm2_result is not None` 包住整段），并只打一条 warning。
现在改成直接抛错 —— 题目刚查出来存在却拿不到状态是数据不一致，
"用户看到已复习、调度其实没动"是一种没有任何痕迹的静默失败（原则 P7）。

### W.11 实测拦下的两个错误

| # | 错误 | 怎么发现的 | 修法 |
|---|---|---|---|
| 1 | **NaN 会穿透数值护栏** | 性质测试 `test_guard_rails_on_degenerate_input` | Python 的 `max(nan, 0.01)` 返回 **nan**（`0.01 > nan` 恒 False），于是各处写的 `max(stability, MIN_STABILITY)` 根本挡不住 NaN，`S ** -w9` 再把它扩散进库。改为 `safe_stability()`（NaN/None → 下限） |
| 2 | **SM-2 路径的阶段被算错** | 全量测试 `test_failure_increments_lapses` | 我一度让 SM-2 也复用 `derive_kind`，它在"失败后 repetition=0"时给出 `learning`，而改造前的契约是 `relearning` —— 回退开关就不再是"行为不变"了。抽出 `sm2_kind` 逐字保留旧规则 |

第 1 条尤其值得记：它不是靠"读代码"发现的，而是靠**一条不依赖任何魔数的
性质断言**（"任何输入都不得产出 NaN"）发现的。
第 2 条则是全量回归的价值 —— 单跑新测试文件时它是绿的。

### W.12 验收

| 项 | 数字 |
|---|---|
| `tests/test_fsrs.py` | **62 passed**：公式对拍 12 / 性质 9 / 状态机 12 / 旧状态接管 6 / 口径转换 6 / 端到端落库 7 / 算法选择 3 / 回退开关 2 / 卡片路径 2 / 契约 3 |
| 全量测试 | 495 → **557 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |
| 真库迁移 | 列已加、行数未变、`integrity=ok`、`item_type` 全部回填 |
| 备份 | `_backup/20260911-3-6-pre-fsrs/engramnote.db`（10.5MB） |

### W.13 仍未完成 / 下一步

- **3.7 fuzz 与复习时段对齐**：FSRS 没有改变"同一批卡片会在同一天到期"
  这件事（同一次导入的卡片 S 相同 → 间隔相同）。fuzz 仍未做。
  ⚠️ 但 3.7 的另一半**已被 FSRS 结构性解决**：SM-2 的"提前复习会缩短间隔"
  来自 `interval * EF`（间隔是乘出来的），而 FSRS 的间隔由 S 解出、
  S 由实际 elapsed 与 R 决定，所以提前复习不再系统性地压缩间隔。
  剩下要做的只有 fuzz 与"对齐到用户的学习时段"。
  → ✅ **已完成**（附录 X，2026-09-11）
- **3.9 掌握度**：`mastery_service.compute_retrievability` 用的是
  **指数**曲线 `2^(-t/S)`，而 FSRS 用的是**幂律**曲线。
  两者差别很大：S=20 天的卡放一年，指数模型给 0.03，FSRS 给 0.44。
  现在 `stability` 已经在库里了，3.9 应该直接换成 FSRS 的 `retrievability`，
  否则"到期预测"与"掌握度"会在界面上互相矛盾。
  **这也是本轮的一个具体发现**，写进了 `test_retrievability_monotone_and_bounded`。
  → ✅ **已完成**（附录 Y，2026-09-11）。
  **而且真库实测把这个问题推到了极端**：191 张有复习记录的卡片
  `interval_days` 全部是 1、最近复习在 80 天前，指数曲线给 `2^(-80) ≈ 8e-25`
  —— 1183 张卡的掌握度**全部为 0.0**。附录 G 补上了时间维度，
  但指数曲线的尾部太陡，在真实数据上退化成了常数 0。
- **3.10 新卡/复习卡分开限额**：`S` 与 `R` 现已可用，
  可以按 `R < 0.9` 与 `state == new` 两个口径分别计数。
  → 仍未做（附录 X 只做了到期时刻，附录 Y 只换了掌握度曲线，都没有动限额）。
- **3.14 参数拟合**：`review_logs.rating` / `predicted_retention` 已开始积累，
  但真库现在没有任何一行是 FSRS 产生的（历史 194 行全是 SM-2 时期），
  **样本量为 0**。校准曲线仍会返回 `insufficient_data=true`，
  这是如实的。
- **前端**：卡片复习页仍显示 `interval_days` / `easiness_factor`，
  没有展示 S/D/R；`3.13`（答错时展开原文）与 3.5 的
  `grading_detail` 展示也仍未接线。
  → ⚠️ **这句话里有一半是错的**：核对后发现**根本没有卡片复习页** ——
  后端 3.12 的 `/review/cards` 与 `/review/cards/{id}/submit` 两个接口
  在前端**没有任何调用方**（`api/review.ts` 里没有对应函数，也没有页面）。
  "没展示 S/D/R" 的前提不成立，真实情况是"卡片复习功能整个没有界面"。
  `3.13` 与 `grading_detail` 展示已在附录 Z 补齐；卡片复习 UI 仍是空白。
- **`review_states.repetition` / `easiness_factor` 的最终去向**：
  现在是"SM-2 兼容镜像"，等 3.7/3.9/3.10 全部切到 `review_states` 之后，
  连同 `quiz_items` 上的四个旧字段一起删除（这是阶段 3 的收尾项）。

---

## 附录 X · 阶段 3.7：到期时刻的调度策略（2026-09-11）

### X.1 交付

| 部件 | 位置 | 说明 |
|---|---|---|
| 业务日算术 | `utils/timeutil.py`：`business_day_index` / `days_between_business_days` | elapsed 从"连续小时差"改为"业务日差" |
| 到期时刻对齐 | `utils/timeutil.py`：`align_to_hour_of_day` | 锚到业务时区（Asia/Shanghai）的固定整点 |
| 间隔抖动 | `fsrs_service.fuzz_interval` | 纯函数；`rand` 可注入以便测试 |
| 调度策略 | `scheduler_service.apply_scheduling_policy` | 抖动 + 对齐，**与算法无关**（FSRS / SM-2 共用） |
| 配置 | `review_fuzz_ratio`（0.05）、`review_due_hour`（4；负值关闭） | 两个旋钮 + 两个关闭开关 |
| 测试 | `tests/test_review_scheduling.py`（**29 用例**） | 业务日算术 5 / 对齐 5 / 抖动 8 / 策略 7 / 雪崩验收 2 / 端到端 2 |

### X.2 🔴 顺带修掉的一个更隐蔽的错误：`elapsed` 用错了计量单位

这是本轮真正的重点，而它**不在 3.7 的原文里** —— 是在实现"对齐到期时刻"
时才浮出来的。

改造前（以及 3.6 刚落地时）算的是**连续时间差**：

```python
elapsed = (now - last_reviewed_at).total_seconds() / 86400   # 1.71 天
```

而 FSRS 需要的是**业务日差**：

```python
elapsed = business_day_index(now) - business_day_index(last_reviewed_at)   # 2
```

两者在业务上不等价，而差别恰好落在最常见的场景上：

| 场景 | 连续差 | 业务日差 | FSRS 走的分支 |
|---|---|---|---|
| 晚上 23:00 复习 → 次日 08:00 再看 | **0.375** | **1** | 连续差 → "同日"→ 短时公式 ❌ |
| 同一时段内隔 40 分钟再复习 | 0.028 | 0 | 两者都是"同日" ✅ |

也就是说：**隔夜复习会被判成"同日复习"**，于是走
`stability_short_term`（只按评分档位加减）而不是
`stability_after_recall`（含难度、间隔效应、S 的幂衰减）。
长期记忆强度因此完全不增长 —— 用户每天老老实实隔夜复习，S 却几乎不动。

这条与"抖动的下限"是同一类错误：**把连续量当成离散量用**。
FSRS 的公式里 `elapsed_days` 一直是**整数天**（Anki 的实现也是按天号相减），
只有我们这里图省事用了 `timedelta.total_seconds()`。

`test_overnight_review_counts_as_one_day` 专门盯着这条：
它断言 `23:00 → 次日 08:00` 的连续差确实是 0.375，而业务日差必须是 1。

### X.3 抖动：让同批卡片不再挤在同一天

同一次导入产生的卡片初始状态**完全相同**（同一批 `S0(rating)`、
同一批 `D0(rating)`），此后永远在同一天到期。一篇笔记 20 张卡，
用户就反复经历"0 张"与"20 张"—— 这是 SM-2 时期就存在的老问题
（§2.4 L-4 第 4 条），不是换算法带来的。

| 参数 | 取值 | 理由 |
|---|---|---|
| 幅度 | `max(1, round(间隔 × 0.05))` | 按比例让长间隔摊得更开（100 天 ±5 天）；`max(1, …)` 让短间隔也真的动起来 |
| 下限 | 间隔 < 3 天不抖 | 1 天粒度下最小抖动是 ±1 天（±33%）。那不是摊负载，是**改写学习节奏** —— 一张 3 天的卡变 4 天，与"答对后涨到 4 天"在界面上无法区分 |
| 方向 | 对称（±delta） | 抖动只改变"哪一天"，不改变"多久之后"。**平均间隔不变**这一点有测试（`test_batch_is_still_centered_on_the_algorithm_interval`），否则它会悄悄偷走 3.6 的"复习量下降" |

**为什么抖动不放进 `schedule()`**：FSRS 的公开公式不含抖动（Anki 也在
FSRS 之外施加）。放进 `schedule()` 会让"公式对拍"测试失效 ——
同输入不再同输出，而公式正确性恰恰是那个模块最需要被证明的东西。
所以 `schedule()` 保持确定，抖动由 `scheduler_service` 施加：
那里本来就是"把间隔变成到期日"的策略层，且 SM-2 回退路径同样需要它。

### X.4 到期时刻对齐：卡片要在用户开始学习之前到期

改造前 `next_review_at = now + interval 天`，到期时刻等于"上次复习的钟点"。
两个后果：

1. **时刻漂移**：今晚 23:40 复习的卡，下次就在 23:40 到期，再下次还是 ——
   用户被要求在凌晨刷新页面；
2. **漏掉一整天**（更要命）：用户习惯早上 08:00 复习，而卡片 09:00 到期，
   于是今天看不到它、明天才出现，间隔凭空多一天。

现在到期时刻锚定业务时区（Asia/Shanghai，与 `today_start_utc` 同一时区）
的**凌晨 4 点**，与 Anki 的 rollover hour 同源：无论用户几点开始学习，
当天到期的卡都已经到期。

**向下取整，不是向上**：向上取整（"下一个整点"）会把间隔系统性拉长近
一天 —— 用户晚上复习、到期时刻在凌晨，向上取整必然落到后天。
向下取整让跨度落在 `(interval − 1, interval]` 天内，与"间隔 N 天"的直觉
一致。有测试断言这个窗口（`test_due_window_never_exceeds_the_interval`）。

⚠️ 向下取整在旧设计里是危险的（可能让 `elapsed < 1` 而误入同日分支）——
**这正是 X.2 必须先做的原因**。两处改动是一组：`elapsed` 不改成按天算，
就无法安全地把到期时刻往前拨。

另有一道 `not_before` 守卫：结果必须**严格晚于**本次复习，
否则间隔 1 天 + 对齐会算出"今天 04:00"（已经是过去），
卡片立刻再次到期 —— 与"到期 → 复习 → 仍到期"的死循环等价。

### X.5 三条不变量

| # | 不变量 | 由谁保证 | 测试 |
|---|---|---|---|
| 1 | 间隔 ≥ 1 天 | `fuzz_interval` 的钳制 + `_interval_days` 的下限 | `test_never_below_one_day`、`test_interval_always_at_least_one_day` |
| 2 | 到期时刻严格晚于本次复习 | `align_to_hour_of_day(not_before=now)` | `test_not_before_guard_pushes_one_day`、`test_due_at_is_strictly_after_now` |
| 3 | 抖动不改变记忆状态（S/D/R） | 抖动作用在 `interval_days` 上，与 `schedule()` 的输出**并列**而非嵌套 | `test_fuzz_does_not_touch_memory_state` |

第 3 条值得单独说：如果把抖动加到 S 上（比如"抖动后的间隔反推一个 S"），
同一张卡在两次相同评分下会得到不同的记忆状态，而记忆状态是
**不可事后纠正**的（间隔一经写入，用户就按它安排复习了）。
抖动只该决定"哪一天到期"，不该让模型以为这张卡变难或变易。

### X.6 顺带修正：SM-2 回退路径此前忽略了传入的 `now`

`calculate_sm2` 内部用 `datetime.now(timezone.utc)` 自己取当前时间。
`_advance_sm2` 改为**统一用调用方传入的 `now`** 计算到期时刻
（`next_review_at = now + interval`，再走策略）。

这**不是**行为倒退：`calculate_sm2` 内部的时钟读取本身就是个缺陷 ——
它让这个"纯函数"变得不可测（同一个输入在不同时刻给出不同结果），
而 `advance` 的契约是"用我给你的时刻算"。`calculate_sm2` 本身保持原样，
`test_week8_sm2.py` 仍覆盖它的旧行为。

### X.7 验收

| 项 | 数字 |
|---|---|
| `tests/test_review_scheduling.py` | **29 passed** |
| 全量测试 | 557 → **586 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |

雪崩验收（3.7 的原文验收点）：30 张初始状态完全相同的卡片，
改造前**全部**落在同一天（1 个到期日），现在至少落在 3 个不同的日子，
且平均间隔与算法给的间隔相差不超过 1 天。

### X.8 仍未完成

- **用户级时区**：`User` 表没有时区字段，到期时刻与日界都锚在
  Asia/Shanghai。这不是 3.7 的遗漏，而是一个独立的产品决定 ——
  要么加字段，要么明确"只服务单一时区"。
  → 仍未做（附录 AA 也没有碰它）。
- **学习时段是配置常量，不是学出来的**：`review_due_hour` 固定 4 点。
  "从用户的复习历史推断他通常几点学习"是更好的做法，但它需要
  足够的样本（真库现在没有），且要有冷启动策略。
- **3.7 的验收指标"间隔不被提前复习缩短"**：结构上已成立，
  但同样**没有实测数据** —— 真库还没有一行由 FSRS 产生的复习记录。
  这条与 3.6 的"复习量下降"一样，只能等真实使用。

---

## 附录 Y · 阶段 3.9：掌握度换用 FSRS 遗忘曲线（2026-09-11）

### Y.1 交付

| 部件 | 位置 | 说明 |
|---|---|---|
| 记忆强度换算 | `fsrs_service.stability_or_interval` | 有 S 用 S，否则 `S := interval_days`（与调度器接管旧行**同一条规则**） |
| 回忆概率 | `mastery_service.compute_retrievability(t, interval, stability=None)` | 改为委托 `fsrs_service.retrievability`（幂律曲线） |
| 锚点查询 | `mastery_service._latest_review_anchor` | 改为以 `review_states` 为**主来源**（它才带 `stability`），`quiz_items` 旧字段退为回退 |
| 成功率统计 | `mastery_service._recent_quality_stats` | 修掉"卡片级复习永远不命中"的查询缺陷 |
| 时间口径 | `mastery_service` 的 elapsed | 由连续小时差改为**业务日差**（与调度器一致） |
| 迁移 | `recalibrate_all_mastery`（已有） | 真库重算 1183 张卡 |

### Y.2 🔴 真库实测：指数曲线让掌握度**全部退化成 0**

这一条比"两条曲线差一个数量级"更严重，也是本次改动最硬的证据。

重算前实测：**1183 张卡片的 `mastery_level` 全部是 0.0**。
查下去发现根因不在"没有复习记录"：

| 事实 | 实测值 |
|---|---|
| 有复习记录的卡片 | **191** 张 |
| 这些卡片的 `interval_days` | **全部是 1**（SM-2 时期的间隔长期为 1，见附录 A 的 1058 行 `interval=1`） |
| 最近一次复习 | 80 天前（2026-08-21 起） |
| 旧公式给出的 R | `2^(-80/1) ≈ 8e-25` → 掌握度 **0.0** |

也就是说：附录 G 把公式**方向**改对了（补上了时间维度、去掉了跨用户串扰、
让没题目的卡片也能拿分），但**指数曲线的尾部太陡**，
在真实数据（interval≈1 且长期未复习）上退化成常数 0 ——
"一个几乎不携带信息的字段"这个 L-2 诊断**依然成立**，只是原因换了。

换成 FSRS 幂律后 `R(80, S=1) = 0.225`，重算结果：

| 掌握度区间 | 卡片数 |
|---|---|
| 0 | 1051 |
| 0-20 | 2 |
| 20-40 | 47 |
| 40-60 | 83 |
| 非零合计 | **132** |

132 = 130 张（最近 5 次复习成功率 1.0）+ 2 张（成功率 0.5）。
另有 59 张卡最近 5 次**全错**，掌握度**正确地**保持 0
—— 这一条同样重要：换曲线不是"把所有分数抬上去"。

### Y.3 为什么必须与调度器同曲线

3.6 之后调度器按 FSRS 排期，`fsrs_request_retention = 0.9` 的含义是
**"到期那一刻模型认为你还有 90% 能想起来"**。若掌握度仍用指数曲线，
同一张卡会在同一个界面上被同时判成"90% 能想起来"（到期队列）和
"2%"（掌握度），而用户无从知道该信哪个。

更实际的是 3.14：校准曲线的定义就是"预测保持率 vs 实际正确率"。
预测值来自 `review_logs.predicted_retention`（FSRS 曲线算的），
若掌握度用另一条曲线，两条曲线永远对不上，校准报表会显示
调度器系统性高估 —— 而那是假的。

### Y.4 旧行怎么办：`S := interval_days`

真库 2241 行 `review_states` 里绝大多数 `stability` 仍是 NULL
（它们还没被 FSRS 复习过）。掌握度不能因此不显示，于是用
**与调度器接管旧行完全相同**的换算：

    S := interval_days        （I(0.9, S) == S，见附录 W.5）

这一点是刻意的：换算规则只写一处（`fsrs_service.stability_or_interval`），
调度器与掌握度都调它。两边各写一遍的话，将来改了一处就会出现
"调度器按 S=17 排、掌握度按 S=10 显示"这种没人能解释的偏差。

### Y.5 顺带修掉的两处查询缺陷

| # | 缺陷 | 后果 | 修法 |
|---|---|---|---|
| 1 | `_recent_quality_stats` 只查 `quiz_id IN (卡片下的题)` | 卡片级复习（3.12）的 `quiz_id` 是 **NULL**，**永远不命中** —— "没有题目的卡片"的真实答题历史被完全忽略：一张卡片级复习全错的卡与全对的卡得到**同样的分数**。而函数 docstring 写着"卡片自身的复习记录都算"，即**注释与实现对不上** | 改用 `card_id`（3.2 加的稳定归属列，历史行已回填）**或** `quiz_id IN (...)` |
| 2 | 锚点要求 `next_review_at IS NOT NULL` | `next_review_at` 是**调度**字段（NULL 的语义是"立即可复习"），它的有无并不表示"复习过没有"。要求它非空会把"已复习但排期被清空"的卡算成 0 | `review_states` 来源只要求 `last_reviewed_at IS NOT NULL`；旧字段来源保留原判据（那是 SM-2 时期判断"排过期"的唯一痕迹） |

第 1 条有专门的测试（`test_card_level_reviews_count_toward_success_ratio`）：
构造两张调度状态完全相同、只有复习记录对错不同的卡，断言掌握度不同。

### Y.6 一个必须说清楚的语义变化

| 量 | 旧（指数） | 新（FSRS 幂律） |
|---|---|---|
| `R(0, S)` | 1.0 | 1.0 |
| **`R(S, S)`（到期时刻）** | **0.5** | **0.9** |
| `R(9S, S)`（约 3 个月，S=10） | 0.002 | **0.567** |
| `R(1000S, S)` | ≈0 | 0.202 |

到期时刻的回忆概率从 0.5 变成 0.9，**这是 FSRS 的设计意图**：
它按"到期时还有 90% 记得"来排期（`request_retention`），
所以 0.9 才是自洽的值。旧实现选 0.5 的理由是"到期了就该复习"的直觉，
但那个直觉与调度器实际在优化的事情不是一回事。

后果要说在前面：**用户看到的掌握度整体变高了**
（三个月未复习从"约 0"变成"约 57"）。这不是口径放松，
而是把一条与调度器无关的曲线换成了同一条。

### Y.7 验收

| 项 | 数字 |
|---|---|
| 全量测试 | 586 → **590 passed / 3 skipped** |
| `TestRetrievability` | 由 6 条改为 7 条，**全部改为断言性质而非旧公式数值**（锚点 `R(S,S)=0.9`、单调、长缺席衰减但不归零、S 优先于 interval、数值护栏） |
| `TestMasteryFormula` | 新增 3 条：S 驱动掌握度（interval 相同、S 差十倍）、卡片级复习计入成功率、已复习无排期仍有分 |
| 真库重算 | 1183 张 → 132 张非零；`integrity=ok`；行数一行未变 |
| 备份 | `_backup/20260911-3-9-pre-mastery/engramnote.db` |

⚠️ `test_mastery_decays_over_time` 的阈值由"stale < 10% fresh"放宽为
"stale < 70% fresh"。**这不是为了让测试通过而调阈值** ——
10% 那个数字来自指数曲线的尾部（0.002），把它写进验收标准等于
把实现细节当成了需求。测试仍然盯着原本的意图（"三个月未复习必须显著更低"），
并额外断言 `stale > 0`（不能与"从未学过"混为一谈）。

### Y.8 仍未完成

- **3.14 的校准曲线依然拿不到数据**：真库 FSRS 复习记录数仍是 **0**
  （194 条历史日志全是 SM-2 时期）。掌握度现在自洽了，
  但"预测得准不准"仍要等真实使用。
- **成功率仍是 5 条窗口的简单比例**：它与 `R` 有部分重复计数
  （答错会同时降低 S 与成功率）。是否需要去重、窗口取多少，
  应当由 3.14 的校准数据决定，现在改就是拍脑袋。
- **前端**：掌握度数字变了，但复习页/卡片页仍没有展示 S、D、R，
  用户看不到"为什么是 57 而不是 100"。
  → 部分补齐（附录 Z.3）：额度提交响应现在带 `rating` 与
  `predicted_retention`，复习页展示了"档位 + 复习前预测还能想起"。
  **S/D 仍未展示**，且发现**卡片复习功能根本没有前端**（见 W.13 的更正）。

---

## 附录 Z · 阶段 3.13 + 前端接线：把"算好了但没人看见"的三件事接上（2026-09-11）

### Z.1 交付

| 部件 | 位置 | 说明 |
|---|---|---|
| 原文语境 | **新增** `components/quiz/SourceContext.tsx` | 懒加载卡片 `source_text`，一键展开；附"在笔记中查看完整原文"链接（3.13） |
| 判分明细展示 | `components/quiz/QuizAnswerCard.tsx` | 渲染 `grading_detail` 的遗漏点 / 误解点（3.5 的落地处） |
| 调度依据展示 | `QuizAnswerCard.tsx` | 把看不懂的 `EF: 2.5` 换成"档位 + 复习前预测还能想起 N%"（3.6/3.9） |
| 语义判分开关 | `QuizAnswerCard.tsx` + 三个页面 | 简答题可勾选"让 AI 先判一次"（3.5 的前端入口） |
| 响应补字段 | `schemas/review.py`、`review_service._build_submit_result` | `grading_detail`、`sm2.rating`、`sm2.predicted_retention` |
| 标签 | `utils/labels.ts` | `ratingLabels`（与自评四档文案一致）、`verdictLabels` |
| 测试 | `test_semantic_grading.py` 新增 2 例 | 断言**响应对象**上有明细，而不是 service 的 dict |

### Z.2 🔴 一个"计算了、存了、就是没返回"的缺陷

附录 V.7 当时写着"`grading_detail` 已随响应返回，但未接 UI"。
本轮核对发现**前半句就是错的**：

```python
# review_service.submit_answer（改造前）
if grade.get("detail"):
    result["grading_detail"] = grade["detail"]      # ← 填进了 dict
```

```python
# schemas/review.py（改造前）
class SubmitAnswerResponse(BaseModel):
    quiz_id: str
    ...
    grading_reason: Optional[str] = None
    # ← 没有 grading_detail
```

`from_service_result` 用 `cls(...)` 显式列字段构造响应，FastAPI 再按响应模型
序列化 —— 于是这个字段在**两层**上都被丢掉。症状是"功能看起来做好了，
界面毫无反应"，而 service 层的单测**一直是绿的**（dict 里确实有）。

修法两条，缺一不可：
1. 响应模型声明 `grading_detail`，`from_service_result` 带上它；
2. service 改为**只以落库的那一份为准**（`review_log.grading_detail`），
   不再从内存变量补 —— 幂等命中（重复提交）时根本没有内存变量，
   而用户刷新后重新拉取也必须看到同样的明细。

回归测试断言的是 `SubmitAnswerResponse.from_service_result(result).grading_detail`
（**响应对象**），而不是 `result["grading_detail"]`（dict）——
后者一直是有的，正因如此才没人发现。

### Z.3 调度依据：把 `EF` 换掉

复习页原本显示：

    下次复习: 6 天后 | EF: 2.5 | 评分: 4

`EF` 是 SM-2 的旋钮，用户看不懂；而且阶段 3.6 之后它已经**不再参与调度**
（现在由难度 D 桥接而来，见附录 W.7）—— 继续展示它是在解释一件没发生的事。

换成真正决定间隔的两个量：

    下次复习: 6 天后 | 评分: 4 | 档位: 想起来了 | 复习前预测还能想起: 62%

- **档位**：FSRS 的四档（与自评按钮文案完全一致，用户看到的是同一套说法）；
- **复习前预测还能想起**：`predicted_retention`，也就是"为什么是这个间隔"。

首次复习的 `predicted_retention` 恒为 1（"从未复习过，必然想得起来"），
显示成"预测还能想起 100%"没有信息量、反而像敷衍，因此 `< 0.999` 才展示。

`rating` / `predicted_retention` 都取自 **`review_log`** 而不是本次调用的
内存变量，因此刷新、重放、幂等命中三条路径给出的解释完全一致。

### Z.4 语义判分开关：为什么状态放在页面而不是卡片里

第一版把开关做成了卡片内部状态，由 `onSubmit(semanticGrading)` 带出去。
TypeScript 通过了（`() => void` 可以赋给 `(b: boolean) => void`，参数少是合法的），
但**运行时是错的**：三个页面都用回车键提交

```tsx
if (current?.submitted) handleNext()
else handleSubmit()          // ← 这里没有参数
```

于是"勾了框再按回车"会静默按**不判分**提交。这类"选了但没生效"是最难被
发现的不一致。修法是把状态提升到页面：`semanticGrading` + `onToggleSemanticGrading`
两个 prop，回车与按钮走同一个 `handleSubmit()`。

⚠️ 顺带记一条：**TypeScript 通过不等于行为正确**。函数参数少是合法的，
所以"回调签名加参数"这种重构不会产生任何编译错误，只会静默地什么都不做。

### Z.5 3.13 的实现选择

| 决策 | 选择 | 理由 |
|---|---|---|
| 数据从哪来 | **懒加载** `GET /understanding/cards/{id}` | 一次复习 10~50 题，多数题答对、不会展开原文；把它塞进到期列表意味着每次复习都多传几十段正文，换一个多数时候用不到的字段。**后端零改动** |
| 何时展开 | 默认折叠，点"查看原文语境" | 需求原文就是"一键展开" |
| 加载失败 | 显示错误 + 重试按钮 | 一个永远转圈的折叠区比没有这个功能更糟 —— 用户会以为原文不存在 |
| 没有原文时 | 明确说"生成过程没有记下原文段落" | 与"加载失败"是两回事，不能共用一句提示 |
| 跳回笔记 | `?view=clean`，**不带**字符偏移 | `source_text` 只有文本、没有在 Markdown 源文里的下标；硬凑一个偏移去高亮就是"给出错误的高亮"，比不高亮更糟（`citationJump.ts` 的设计原则）。精确回跳需要把卡片关联到 `chunks` 的 `char_start/char_end`，是后续工作 |

真库实测这个功能有真实内容可显示：191 张**被复习过**的卡片 100% 有
`source_text`；全库覆盖率 99.8%（1181/1183），中位长度 56 字。

### Z.6 验收

| 项 | 结果 |
|---|---|
| 后端测试 | **592 passed / 3 skipped**（新增 2 例） |
| 后端 ruff | `app tests scripts` 全绿 |
| 前端 | `tsc --noEmit` 通过、`eslint src/` 通过、`vite build` 成功 |
| 前端格式检查 | ⚠️ `prettier --check src/` 报 **77 个文件**不合规 —— 这是**改造前就存在**的（用 `git show HEAD:<file>` 逐个核对过，`labels.ts` / `Review.tsx` / `api/review.ts` 的 HEAD 版本同样报错），CI 里 `format:check` 一直是 `continue-on-error`。本附录**不**顺手全仓格式化：那会产生一个与本次改动无关的、覆盖 77 个文件的 diff |

### Z.7 新发现：卡片复习功能**根本没有前端**

附录 W.13 曾写"卡片复习页仍显示 `interval_days` / `easiness_factor`，
没有展示 S/D/R"。本轮核对发现这句话的前提不成立：

- 后端 3.12 提供 `GET /review/cards` 与 `POST /review/cards/{id}/submit`；
- 前端 `api/review.ts` 里**没有任何函数**调用它们；
- `pages/` 下也**没有**卡片复习页面。

也就是说：**没有题目的卡片可以直接复习**这件事，从后端做完（附录 F）
到现在，用户**一次也没法用**。这不是"没展示 S/D/R"，而是整块功能没有入口。

这条与 Z.2 是同一类问题的两种形态：
**"做完了"与"用户能用上"之间的那一步，最容易在清单上被勾掉。**
两个接口的测试都是齐的、也是绿的 —— 它们测的是后端行为，不是可达性。

### Z.8 仍未完成

- **卡片复习 UI**（3.12 的前端一半）：接口齐备、无数据依赖，是下一步。
  → ✅ **已完成**（附录 AA，2026-09-11）
- **S / D 的展示**：`predicted_retention` 已经能让用户理解间隔，
  但 `stability`（记忆强度）与 `difficulty` 仍没有出现在任何界面上。
  → ✅ **已补齐**（附录 AA.4：卡片复习页把 S / D / R 三个量一起展示）
- **精确回跳原文**：见 Z.5 的说明，需要卡片 → `chunks` 的位置关联。
- **`EF` 的最终去留**：现在它只是 SM-2 兼容镜像（附录 W.7），
  界面上已不再展示；连同 `quiz_items` 上的四个旧字段一起删除是阶段 3 的收尾项。

---

## 附录 AA · 卡片复习 UI：把一个"做完但用不上"的功能接通（2026-09-11）

### AA.1 交付

| 部件 | 位置 | 说明 |
|---|---|---|
| API 客户端 | `api/review.ts` | `getDueCards` / `submitCardReview` + 三个类型（此前**完全没有**卡片复习的调用方） |
| 页面 | **新增** `pages/CardReview.tsx` | 先回忆 → 翻面 → 四档自评 → 看调度依据 → 下一张 |
| 路由 | `App.tsx` | `/review/cards`（懒加载） |
| 导航 | `components/Sidebar.tsx` | "学习"组新增「卡片复习」 |
| 接口改进 | `api/review.py` | `total` 由"本页条数"改为**真正到期的总数** |
| 测试 | `test_phase3_learning_core.py` +2 | 队列 backlog 与空页两种情况的 `total` 语义 |

### AA.2 这一页补的是什么

附录 Z.7 发现的事实：后端 3.12 提供 `GET /review/cards/due` 与
`POST /review/cards/{id}/submit`，**前端没有任何调用方**。
也就是说"没有生成过题目的卡片可以直接复习"（症状 L-5 的解法）
从做完到现在，用户一次也没法用 —— 而两个接口的测试一直是齐的、绿的。

它们测的是**后端行为**，不是**可达性**。这一页补的就是可达性：
API 函数、页面、路由、侧边栏入口，四样缺一不可。

### AA.3 交互：先回忆 → 再翻面 → 才自评

卡片正文**默认隐藏**。这不是装饰：

- 卡片复习的全部价值在于"先自己想一遍"。一开始就把内容摊开等于直接看答案，
  用户会把它当成快速浏览而不是回忆练习；
- 那样产生的自评数据是**假的**，而它**会真的改变调度** ——
  间隔一经写入就无法事后纠正（原则 P3 的同型约束）。

同样地，"未自评不许跳到下一张"：此刻调度尚未推进，放行会让这张卡停在
"看了但没结账"的状态，而且界面上看不出来。

### AA.4 第一次把 S / D / R 摆给用户看

自评之后展示：

```
已记录：想起来了
下次复习: 6 天后 （9月17日）
复习前模型认为你还能想起: 62% —— 越接近遗忘，这次答对后间隔涨得越多
记忆强度 5.9 天 · 难度 5.3 · 掌握度 57 → 63
```

这正是贯穿 3.6/3.7/3.9 的那条线：**只给一个"6 天后"等于要求用户盲信调度器**。
`stability` 是"下次复习 N 天"的直接依据，`predicted_retention` 是"为什么涨这么多"
的依据，两者都是 3.14 校准曲线将来要给用户看的东西 —— 这里先把原始量露出来。

这也是附录 Z.8 里"S / D 仍未展示"的收尾：卡片复习响应从 3.6 起就带着
`stability` / `difficulty` / `predicted_retention`，只是没有页面消费它们。

### AA.5 `total` 必须说真话（一处接口语义修正）

`CardReviewListResponse.total` 原本是 `len(items)` —— 本页条数。
真库实测 **1183 张卡片全部处于到期状态**，而接口默认 `limit=20`：

    用户复习完 20 张 → 看到"完成" → 以为清空了 → 实际还剩 1163 张

卡片复习**不受每日答题限额约束**（这是 3.12 的既定设计，见 `submit_card_review`
的说明），所以队列可能是上千张，`total` 是用户判断"要不要继续"的**唯一**依据。
改为 `review_state_service.count_due_cards` 的真实计数。

空页也走同一条路：即使本页为空（可能全被回收站过滤掉），也要给出真实总数，
否则前端无法区分"没有到期卡片"与"这一页恰好没有"。

### AA.6 顺带踩到的一个 lint 陷阱（值得记）

新页面的初始加载写成了常规的 `useEffect(() => { void loadCards() }, [...])`，
被 `react-hooks/set-state-in-effect` 拦下。第一反应是"确实不该同步 setState"，
于是把 `setState` 全部挪到 `await` 之后 —— **仍然报错**。
再试去掉 `void`、再试 `Promise.resolve(...)` 包一层 —— **都报错**。

结论：这条规则**不建模 `await`**，只要 effect 体内调用的函数里含 `setState`
就报，无论它是否在同步路径上。也就是说在这里它是**误报**。

最终改法不是加 `eslint-disable`，而是换一种同样自然的写法：把落地放进
`.then()/.catch()` 回调

```ts
const fetchCards = useCallback(
  () => getDueCards(PAGE_SIZE).then(applyLoaded).catch(() => setError('加载到期卡片失败')),
  [applyLoaded],
)
useEffect(() => { void fetchCards().finally(() => setLoading(false)) }, [fetchCards])
```

`setState` 落在回调里，语义完全一样，静态分析也能看出它不在同步路径上。
**没有为了让 lint 闭嘴而改变行为**，也没有把一条真实的规则关掉。

### AA.7 顺带修掉：快捷键"时灵时不灵"

只把 `onKeyDown` 挂在容器 div 上是不够的：键盘事件从**获得焦点的元素**冒泡，
而点击按钮之后焦点留在被卸载的按钮上、最终回到 `body` ——
事件根本到不了那个 div，于是"回车翻面 / 回车下一张"会时灵时不灵。

修法：容器加 `tabIndex={-1}`（只允许程序聚焦，不插进 Tab 顺序），
每次换卡或换阶段时把焦点收回容器。同时保留一条守卫：事件目标本身就是
按钮时直接跳过 —— 让按钮走自己的原生回车行为，避免一次回车触发两件事。

### AA.8 验收

| 项 | 结果 |
|---|---|
| 后端测试 | **594 passed / 3 skipped**（新增 2 例） |
| 后端 ruff | `app tests scripts` 全绿 |
| 前端 | `tsc --noEmit` 通过、`eslint src/` 通过、`vite build` 成功（`CardReview` 单独成 chunk，7.9 kB） |
| 真库队列 | 卡片级状态 **1183 条，全部到期** —— 队列是满的，不需要先跑迁移脚本 |

⚠️ **这一页没有自动化 UI 测试**：仓库里没有前端测试框架（Vitest/Playwright
都未接入，见 §2.8 E-6/E-7）。因此"页面能渲染、按钮能点"目前只有
`tsc` + `eslint` + `build` 三道静态保证，**交互正确性未经运行时验证**。
这是已知缺口，不是本附录遗漏。

### AA.9 仍未完成

- **前端测试框架**：上面那条缺口的根因。接入 Vitest 后，本页至少有
  三件值得测的事：翻面前正文不可见、未自评时"下一张"被禁用、
  `totalDue > items.length` 时确实显示"还有更多"。
  → ✅ **已完成**（附录 AB，2026-09-11）—— 这三件事现在都有测试
- **精确回跳原文**：卡片复习页复用了 3.13 的 `SourceContext`，
  因此同样只有文本级展示、没有字符级高亮（需要卡片 → `chunks` 的位置关联）。
- **卡片复习的限额策略**：现在完全不限额（既定设计）。真库 1183 张全到期
  意味着队列理论上无限长 —— 是否需要"每天最多 N 张"是**产品问题**，
  不在阶段 3 的范围内，但 3.10（新卡/复习卡分开限额）落地时应当一并考虑。

---

## 附录 AB · 前端单元测试框架：把"只有静态检查"这个缺口补上（2026-09-11）

### AB.1 交付

| 部件 | 位置 | 说明 |
|---|---|---|
| 测试运行器 | `vitest` 2.1 + `jsdom` + Testing Library | `npm test` / `npm run test:watch` |
| 配置 | `vite.config.ts` 的 `test` 段 | jsdom 环境、setup 文件、`src/**/*.test.{ts,tsx}` |
| 启动文件 | **新增** `src/test/setup.ts` | jest-dom 断言、用例后卸载、补齐 jsdom 缺失的 DOM API |
| 测试 | `citationJump.test.ts`（12） | 纯函数：纯文本化、指纹构造、退化路径 |
| 测试 | `SourceContext.test.tsx`（8） | 懒加载、只请求一次、三种终态各说各话 |
| 测试 | `QuizAnswerCard.test.tsx`（19） | 判分明细、调度依据、语义判分开关、自评阶段行为 |
| 测试 | `CardReview.test.tsx`（11） | 翻面门禁、自评门禁、队列 backlog、in-flight 锁 |
| CI | `.github/workflows/ci.yml` | 新增 `Vitest` 步骤，**阻断性**（与 `format:check` 不同） |

合计 **50 个用例，4 个文件，5 秒跑完**。

### AB.2 为什么现在必须补：静态检查发现不了这三类错误

附录 Z 与 AA 两轮改的都是界面，而验证手段只有 `tsc` + `eslint` + `build`。
回看那两轮**真实踩到的坑**，没有一个是静态检查能发现的：

| 坑 | tsc | eslint | build | 测试能发现吗 |
|---|---|---|---|---|
| 语义判分开关放在组件内部，回车提交静默不生效 | ✅ 通过 | ✅ 通过 | ✅ 通过 | ✅ 本附录的 `QuizAnswerCard` 用例直接盯"回调收到什么" |
| 快捷键"时灵时不灵"（焦点在 body，事件到不了容器） | ✅ 通过 | ✅ 通过 | ✅ 通过 | 🟡 需要真实键盘事件，目前靠人工 |
| `grading_detail` 被 Pydantic 静默丢弃 | — | — | — | ✅ 后端侧已有（附录 Z.2），前端侧现在也有 |

第一条尤其说明问题：**TypeScript 完全通过** —— `() => void` 可以赋给
`(b: boolean) => void`，参数少是合法的，所以"回调签名加参数"这种重构
不产生任何编译错误，只会静默地什么都不做。

### AB.3 用例的选择标准：只测"错了会静默"的行为

50 个用例里没有一条是"渲染出来就算过"。挑的都是**失败时不会报错、
只会悄悄做错事**的行为：

- **翻面前正文不可见**（`CardReview`）：否则"先回忆再自评"退化成看答案，
  用户产生的自评数据是假的 —— 而它会**真的改变调度**，且不可事后纠正。
- **未自评时"下一张"禁用**：否则卡片停在"看了但没结账"的状态，界面上看不出来。
- **`total > items.length` 时必须提示还有更多**：真库 1183 张全到期、
  接口默认只给 20 张，不提示就会让用户以为清空了。
- **`grading_detail` 为 null 时什么都不显示**：渲染成"没有发现遗漏"是
  把"没判分"说成"判过分且无问题"，是两回事。
- **"没有原文"与"加载失败"必须是两句不同的话**：前者该放弃，后者该重试。
- **折叠时**不**请求原文**、展开后收起再展开只请求一次：一次复习几十道题，
  每题都拉一遍正文是实打实的浪费。

### AB.4 两个环境适配（都放在测试侧，不改产品代码）

| 问题 | 现象 | 处理 |
|---|---|---|
| jsdom 不实现滚动 | `Element.prototype.scrollIntoView` **不存在**，`highlightCitation` 走到滚动那一步就抛 `TypeError`，失败位置指向产品代码里完全正常的一行 | 在 `src/test/setup.ts` 里补一个 `vi.fn()`。**不**在产品代码里加 `if (typeof x === 'function')` —— 真浏览器上这个方法一直有，为迁就 jsdom 改产品代码是本末倒置 |
| 测试文件与被测文件不同目录 | `vi.mock('../../api/qa')` 的路径**相对调用它的文件**解析，放进 `__tests__/` 会让每个 mock 多一层 `../`，抄错一层就得到"mock 没生效、测试悄悄打到真实模块" | 测试文件与源文件**同目录**（`SourceContext.tsx` + `SourceContext.test.tsx`） |

### AB.5 顺带修掉一个真实缺口：`stripMarkdown` 不认分隔线

写 `citationJump` 的测试时发现：`---`（Markdown 分隔线）没有被剥掉，
`stripMarkdown('---\n\n')` 返回 `'---'`。

它的实际后果有限（指纹只有 3 个字符，短于 4 字符的下限会被跳过，
最终退化成"只滚动不高亮"），但那是**碰巧**被长度下限挡住的 ——
换一个长一点的分隔线段落（比如 `----------`）就会搜不到而白白退化。

`stripMarkdown` 的注释里写着"只处理会影响文本连续性的标记"，
而分隔线渲染后是一个 `<hr>`、**不产生任何文本**，正是该被剥掉的。
已补上，并特意放在表格竖线替换**之前** —— 否则 `|---|---|`
会被压成 ` --- ` 而被误判成分隔线（它不是）。

这条正是"补测试时顺手发现产品缺陷"的典型：**之前没有任何测试覆盖它**。

### AB.6 依赖选择：为什么钉 `vitest@2` 而不是最新的 5

第一次装 `vitest` 直接失败：

    npm error Fix the upstream dependency conflict...
    peerOptional @vitest/browser-playwright@"5.0.0" from vitest@5.0.0

根因是 **Vitest 5 要求 Vite ≥ 6**，而本项目在 Vite 5.4。
两条路：升 Vite（会连带改动构建配置与分包策略），或者钉一个兼容的主版本。

选了后者：**这一轮的目的是"补上验证手段"，不是"升级构建工具链"**。
把两件事混在一起，一旦构建出问题就分不清是谁引起的。
Vite 升级是独立的一件事，应当有独立的验证。

### AB.7 验收

| 项 | 结果 |
|---|---|
| `npm test` | **50 passed / 4 files**，约 5 秒 |
| `npm run lint` | 通过 |
| `npx tsc --noEmit` | 通过（测试文件也在 `src` 下，同样受 `noUnusedLocals` 等约束） |
| `npm run build` | 成功（测试文件不进产物 —— 它们不在入口依赖图里） |
| `npm ls --depth=0` | 依赖树一致，`npm ci` 可复现 |
| CI | 新增阻断性 `Vitest` 步骤 |

### AB.8 仍未完成

- **Playwright 端到端**：本附录只到"组件 + 页面级"。
  "登录 → 导入 → 复习 → 引用回跳"这条全链路仍未自动化，
  而它恰恰是历史缺陷最集中的地方（§2.5 E-6/E-7）。
- **快捷键行为**：见 AB.2 表格第 2 行 —— `user-event` 可以模拟键盘，
  但需要先把焦点管理的行为想清楚，留待下一步。
- **覆盖率门槛**：现在没有覆盖率统计，也就没有"不得低于 X%"的门禁。
  在没有真实基线的阶段设一个数字，只会变成又一个被绕过的检查
  （与 §2.9 评测脚本不进 CI 是同一判断）。
- **`prettier --check` 仍是 77 个文件不合规**（附录 Z.6）：本附录新增的
  文件同样未按 prettier 格式化 —— 保持与既有代码一致，不单独格式化少数文件。

---

## 附录 AC · 阶段 4.2：LLM 调用记账（2026-09-11）

### AC.1 交付

| 部件 | 位置 | 说明 |
|---|---|---|
| 模型 | **新增** `models/llm_call.py` | `llm_calls` 表；新表由 `create_all` 创建，**无需 ALTER 迁移** |
| 记账服务 | **新增** `services/llm_accounting_service.py` | 上下文 / 计价 / 落库 / 聚合 |
| 出口接线 | `llm_service.chat_detailed` + `chat_stream` | 这两个是**唯一**真正发起 HTTP 的地方 |
| 上下文接线 | `understand_tasks`、`rag_service.answer_question`、`review_service`→`grade_short_answer_semantically` | 让「谁 + 哪篇笔记」有值 |
| 接口 | **新增** `api/llm.py`：`GET /api/llm/usage` | 按场景/笔记/模型/任务/天聚合，只返回当前用户 |
| 配置 | `llm_price_input_per_1m` / `llm_price_output_per_1m` / `llm_price_currency` | **默认全空**（见 AC.3） |
| 测试 | `tests/test_llm_accounting.py`（**28 用例**） | 上下文隔离 / 计价 / 落库 / 聚合 / HTTP 契约 |

后端测试 594 → **622 passed / 3 skipped**；ruff 全绿。
真库实测：`llm_calls` 已建（18 列 + 3 个复合索引），`integrity=ok`，其余表行数一行未变。

### AC.2 这一项补的是什么

改造前每次 LLM 调用只在**日志**里留一行：

```
LLM 响应 | scene=extract_knowledge_points | prompt_tokens=1000 | completion_tokens=200
```

日志能看，但**不能聚合**。于是"这个月花了多少""哪篇笔记最贵""上缓存之后省了多少"
一个都答不出来 —— 这正是症状 **A-12（成本只进日志）** 与原则
**P5（成本是一等公民）** 所指的问题。日志是给排障看的；成本要能聚合才叫一等公民。

### AC.3 🔴 没配价格时记 NULL，不记 0

这是本附录最要紧的一处判断。

价格随供应商、模型、时期变化，所以本项目**不内置价格表**（内置的那份数字迟早
变成错误的事实，而错误的价格比"不知道价格"更糟：它会被人当真）。默认两项都空，
此时 `cost` 记 **NULL**：

    记 0    → 报表上"花费 0 元"，与"完全免费"长得一模一样，没人会去查为什么
    记 NULL → 报表上"价格未知"，如实

配套地，聚合结果必须同时给出 `cost_known_calls` 与 `calls` 两个计数。
测试 `test_cost_known_calls_exposes_partial_pricing` 构造了"3 行里只有 1 行有价格"
的场景：单看 `total_cost = 1.5` 完全像个正常数字，只有并列计数才说得清。

HTTP 响应另外带 `price_configured`，前端据此**必须**提示而不是直接显示 0 元。

### AC.4 失败也要记账

`llm_max_retries = 5`，每次重试都可能已经把 prompt token 发出去并被计费。
只记成功，等于把**最该被看见的那部分成本**藏起来 —— 失败的重试风暴恰恰是最烧钱的形态。

所以 `chat_detailed` 在重试耗尽、抛异常之前先记一笔 `success=False`，
`error` 截断到 500 字符（完整堆栈已经在 logger 里，账本不需要抄一遍）。

### AC.5 记账写自己的会话，不蹭调用方的事务

`record_call` 用**独立的短生命周期会话**写入。这不是性能考虑，是语义：

- LLM 调用**已经发生**、钱**已经花掉**。若记账挂在调用方事务上，调用方一旦
  回滚（比如答题因为别的原因失败），这笔开销就凭空消失 —— 账本变成
  "只统计成功业务"的数字，而成本管理恰恰要知道**白花的那些**。
- 反向耦合同样要避免：记账失败**不得**让用户的请求失败。因此这里吞掉自身
  异常并打 WARNING。

⚠️ 这不是"静默降级"：记账是旁路，失败会以 WARNING 出现在日志里，且聚合接口
**永远不会替它编数字**（没记上就是没记上）。为了账本写不进去而让用户答不了题，
是明显更糟的取舍。

测试 `test_recording_survives_caller_rollback` 直接构造"记完账后调用方回滚"，
断言账目仍在。

### AC.6 上下文用 `contextvars` 而不是层层传参

`user_id` / `note_id` **不是 LLM 调用的语义参数**（模型不需要知道给谁用）。
把它们加到 `chat()` / `chat_detailed()` 及其十余个包装方法上，会让每次签名
变更波及一大片调用方，而且很容易漏 —— 漏一个就等于漏记一个维度。

改用 `llm_context(...)` 上下文管理器：

```python
with llm_context(user_id=uid, note_id=nid, task="understand_note"):
    await process_note_understanding(...)      # 内部所有 LLM 调用自动归属
```

三个刻意的性质：

1. **可嵌套且只覆盖声明了的字段** —— "整个任务属于谁"与"这一步属于哪篇笔记"
   可以分开声明，内层不必重抄三个字段；
2. **没声明上下文时照样记账**（`user_id` 为 NULL）—— 宁可少一个维度，
   也不能漏记一笔，因为偏小的总额不会被任何人发现；
3. **异常路径也恢复**（`try/finally`）—— 否则一次失败会让后续调用被算到
   上一个用户的头上。

### AC.7 接口只返回当前用户的数据

仓库里还没有管理员角色（`User` 表没有 `is_admin`），因此**不做**"查所有人"的开关
—— 那会变成一个任何登录用户都能看到别人消费额度的接口。
看全局成本属于阶段 6 的可观测性工作面（Prometheus 指标 + 内部看板），
不是靠把这个接口放宽权限来实现。

`group_by` 也不含 `user`：单用户接口按用户分组只有一个分组，放进去只会误导。

### AC.8 验收

| 项 | 结果 |
|---|---|
| `tests/test_llm_accounting.py` | **28 passed**：上下文 5 / 计价 4 / 落库 8 / 聚合 7 / HTTP 契约 4 |
| 全量后端测试 | 594 → **622 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |
| 真库 | `llm_calls` 已建（18 列、9 个索引），`integrity=ok`，其余表行数未变 |
| 备份 | `_backup/20260911-4-2-pre-llm-calls/engramnote.db` |

### AC.9 仍未完成

- **成本看板（前端）**：本附录只到 API。图表属于阶段 6。
  ⚠️ 但要说清楚：**现在没有任何界面能看这些数字** —— 与附录 Z/AA 反复强调的
  "做完了但用户用不上"是同一类风险，只是 4.2 的既定验收本就是后端聚合能力，
  且前端呈现被显式排进了阶段 6。这条不是遗漏，但也不能假装已经闭环。
- **4.1 LLMGateway**：本附录**没有**做那一步重构。记账是接在现有
  `chat_detailed` / `chat_stream` 两个出口上的；将来抽出网关时，记账调用应当
  跟着搬进网关（它就属于"统一入口"该做的事）。`llm_service.py` 现在 1136 行，
  距 4.1 的 <300 行目标仍很远。
- **4.3 配额与告警**：有了 `llm_calls` 之后，"拒绝超限请求"才第一次成为可实现的
  —— 现在能按用户算窗口内的用量了。这是 4.3 的直接前置。
  → ✅ **已完成**（附录 AD，2026-09-11）
- **清理策略未启用**：`llm_call_retention_days` 配置已就位，但还没有任何定时任务
  真的去删旧行。表是"每次调用一行"，长期运行会持续增长。
- **上下文接线只覆盖 3 个入口**：理解任务、RAG 问答、语义判分。
  题目生成、扩展知识点、卡片关系推断等仍在**没有上下文**的情况下记账
  （`user_id` 为 NULL）。它们不难接，但每个都要找到"这一步属于谁"的位置，
  留待逐个人工确认 —— 与其猜一个，不如留空。
  ⚠️ 这一条在附录 AD 里变成了**配额的已知缺口**：算不到人头上的调用
  既记不了账，也拦不住。

---

## 附录 AD · 阶段 4.3：LLM 配额与拒绝（2026-09-11）

### AD.1 交付

| 部件 | 位置 | 说明 |
|---|---|---|
| 配额判定 | `llm_accounting_service.check_quota` / `QuotaStatus` | 按用户、按**业务日**统计用量并与上限比较 |
| 拒绝异常 | `llm_accounting_service.LLMQuotaExceeded` | 带**稳定错误码** `LLM_QUOTA_EXCEEDED` 与 429 状态码 |
| 执行点 | `llm_service._enforce_quota`（`chat_detailed` / `chat_stream` 的**第一句**） | 唯一真正花钱的地方 |
| 错误契约 | `main.py` 的 `llm_quota_exceeded_handler` | 复用既有 `{detail, error_code, request_id}` 格式 |
| 状态暴露 | `GET /api/llm/usage` 新增 `quota` 段 | 用户能看到"今天用了多少 / 上限多少" |
| 配置 | `llm_daily_token_quota` / `llm_daily_cost_quota` | **默认都是 0 = 不限** |
| 测试 | `tests/test_llm_accounting.py` 扩到 **41 用例**（+13） | 判定边界 / 失效告警 / 出口拦截 |

后端测试 622 → **635 passed / 3 skipped**。

### AD.2 为什么默认**不**限额

两项默认都是 0（不限）。理由不是"懒得做"：

> 一个默认打开的额度上限，会在用户**毫无预期**时中断他正在做的事
> （导入一篇长文档、批量重跑理解），而"被自己的工具拦住"是最难排查的
> 一类故障 —— 用户会以为是模型坏了、笔记有问题，不会想到是额度。

顺序很重要：**先能算账，才谈得上有意地设限**。4.2 做完记账，4.3 才有意义。

### AD.3 边界包含：用满即拦

```python
if tokens_used >= token_limit:   # 不是 >
```

用 `>` 会让"上限 1000"实际允许第 1001 次调用通过 —— 上限就不再是上限。
`test_at_limit_is_exceeded` 专门盯这条。

### AD.4 🔴 配了金额上限但没配单价：**不假装它在生效**

`llm_daily_cost_quota` 依赖单价（`llm_price_*`）。没配单价时金额永远算不出来，
于是这个上限会变成**一个静默失效的保护**：

    用户以为设了上限 → 实际毫无作用 → 而且没有任何迹象表明这一点

这里的处理是：

1. 把 `cost_enforceable` 置为 **False**（不是悄悄忽略）；
2. 进程内**打一次** WARNING，把原因和两条出路写清楚；
3. `QuotaStatus.cost_limit` 归零 —— 免得调用方拿一个不生效的数字去展示；
4. 继续按 token 配额拦截（那一半是能算的）。

`test_cost_quota_requires_prices_and_says_so` 覆盖这条。
这与 4.2 的"没配价格记 NULL 不记 0"是同一条原则的两次应用：
**宁可说"不知道"，也不要给一个看起来正常的假数字。**

### AD.5 检查放在**发起请求之前**

`_enforce_quota` 是 `chat_detailed` 的第一句，在拿信号量之前；同时是
`chat_stream` 的第一句，在产出任何 token 之前。两处位置都是刻意的：

- 放在信号量之后 = 被拒绝的调用仍然占着并发额度；
- 放在请求之后 = **token 已经花掉了**，"拒绝"变成"事后记账"；
- 流式路径放在流的中间 = 用户先看到半截回答再断掉，比一开始就拒绝更糟。

`test_llm_call_is_rejected_before_any_network_request` 用一个"一旦被调用就
断言失败"的假客户端证明**一个请求都没发**；`test_stream_is_rejected_before_yielding_anything`
证明流式路径连第一个 token 都没产出。

### AD.6 为什么在 `llm_service` 里拦，而不是每个调用方自己查

这是**唯一**真正花钱的地方。拦在这里意味着无论从哪条路径进来（理解任务、
问答、语义判分、将来新增的场景），配额都自动生效。让每个调用方各自记得查一次，
必然会有漏的 —— 而漏掉的那个恰恰是没人想到的昂贵路径。

`user_id` 从 4.2 的 `llm_context` 里取，因此**不需要给任何调用方加参数**。

### AD.7 有类型的异常，而不是返回空答案

"配额用完了"必须与"模型答不出来"区分开：

| | 配额用完 | 模型失败 |
|---|---|---|
| 用户该做什么 | 明天再来 | 稍后重试 |
| 错误码 | `LLM_QUOTA_EXCEEDED` | 各自的错误 |

如果这里返回空字符串，用户看到的是"AI 什么都没说"，而真正的原因是额度用完了
—— 他会一直重试，每次都被拒，却永远得不到解释。这正是原则 P7
（失败必须响亮）要防的形态。

`error_code` 是**稳定**的（不随文案变化），与 `main.py` 既有的
`{detail, error_code, request_id}` 契约一致。处理器注册成全局的，
因为 LLM 调用散布在多条路径上（其中一部分在 Celery 任务里），逐个包 try 必然漏。

### AD.8 两个刻意的"不生效"，都写在明面上

| 情形 | 行为 | 理由 |
|---|---|---|
| 没有 `user_id` 上下文 | **放行** | 算不到任何人头上。宁可漏拦，也不能因为"不知道是谁"就把所有人的调用都拒掉。这是 4.2 遗留的缺口（AC.9），在配额这一层变成"不受保护的调用点" |
| 未配单价的金额上限 | **不执行 + 告警** | 见 AD.4 |

### AD.9 验收

| 项 | 结果 |
|---|---|
| `tests/test_llm_accounting.py` | **41 passed**（配额相关新增 13） |
| 覆盖的关键行为 | 默认不限额**不查库** / 边界包含 / 用户间不串号 / 单价缺失时不假装生效 / 超限时零网络请求 / 流式不产出 token / 未超限不误拦 |
| 全量后端测试 | 622 → **635 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |

### AD.10 仍未完成

- **per-user 覆盖**：现在两项配额是**全局默认值**（每个用户各自计算，
  但阈值来自同一份配置）。计划里说的"用户级配额"要真正做到按人设不同额度，
  需要 `users` 加列 + 一个管理入口（否则就是一个没人能写的死列）。
  这一轮刻意**没有**加那一列 —— 加了也没有界面能设置它，
  正是附录 Z/AA 反复说的"做完了但用不上"。
- **告警只到 WARNING 日志**：没有邮件/Webhook/Prometheus。
  真正的告警通道属于阶段 6（可观测性）。现在能做的只是"拒绝 + 记日志 +
  在 API 里暴露状态"，把这三件事说成"告警"是不诚实的。
- **没有前端**：`/api/llm/usage` 的 `quota` 段没有任何界面消费。
  与 4.2 一样，成本看板属于阶段 6。
- **4.1 LLMGateway 仍未做**：记账与配额都接在现有出口上；
  将来抽网关时，这两件事都应当跟着搬进网关（它们正是"统一入口"该做的）。
  `llm_service.py` 现在 **1187 行**（实测），距 4.1 的 <300 行目标更远了 ——
  **4.2 与 4.3 这两轮又给它加了约 90 行**，是必须记下来的一笔账：
  每一轮"顺手加个钩子"都在把 4.1 的重构成本推高。

---

## 附录 AE · 阶段 4.8 / 4.9：卡片入库门（2026-09-11）

### AE.1 交付

| 部件 | 位置 | 说明 |
|---|---|---|
| 指纹与质量门 | **新增** `services/card_intake_service.py` | `card_content_hash` / `reject_reason` / `save_cards_idempotent` |
| 模型 | `knowledge_cards.content_hash`（新列，索引） | 重跑理解的内容寻址键 |
| 迁移 | `database.py`：加列 + **回填 1183 行** | 回填**直接调用**运行时函数（理由见 AE.4） |
| 接线 | `understanding_service.save_knowledge_cards` → 返回 `SaveOutcome` | 由"无条件插入"改为"质量门 + 去重" |
| 可见性 | `process_note_understanding` 的返回值新增三个计数 | 复用 / 被拒 / 超限 |
| 配置 | `card_min_content_chars` / `card_min_title_chars` / `card_require_source_text` / `card_max_new_per_run` | 四个旋钮 |
| 测试 | `tests/test_card_intake.py`（**27 用例**） | 指纹 / 质量门 / 幂等 / 上限 |
| 迁移后真库自检 | — | 1183 行全部回填；抽样 200 张指纹与运行时函数**零不一致** |
| 顺带修掉 | `test_week5_6_understanding.py` 的**顺序依赖** | 见 AE.8（既存缺陷，与本次改动无关，已用 `git stash` 对比确认） |

后端测试 635 → **662 passed / 3 skipped**；ruff 全绿。

### AE.2 🔴 真库实测：这个缺陷**还没有发作过**

开工前先量了一次，结果对"该怎么说这件事"很关键：

| 事实 | 实测值 |
|---|---|
| 卡片总数 | 1183（分布在 17 篇笔记上） |
| **跨多天创建过卡片的笔记** | **0 篇** —— 重跑理解从未发生过 |
| 完全重复的 `(title, content)` | **0 行** |
| 同笔记内指纹重复（迁移后复测） | **0 行** |
| 现有 title 在同笔记内重复的 | 7 组（**但内容不同**，是两张正常的卡） |
| 会被质量门拦下的存量卡片 | 内容 <10 字 4 张（0.3%）、无 `source_text` 2 张 |

所以必须把话说明白：**这次没有清理掉任何重复数据**。
4.8 修的是一个**按一次按钮就会发作**的缺陷：

```python
# 改造前
for point in knowledge_points:
    db.add(KnowledgeCard(...))       # 无条件插入
await db.commit()
```

"重新理解这篇笔记"按一次，这篇笔记的卡片**翻一倍**。而重复卡片会进
复习队列（两道一模一样的题）与知识图谱（孪生节点），用户很难理解为什么。

**它的价值是预防性的**：谁都可以按那个按钮，而按下去不会坏。

### AE.3 三条设计决定

**① 内容寻址复用，只补差集，永不删除**

旧卡片上挂着 `review_states`（唯一约束含 `item_id`）。删卡片就等于把用户的
复习进度变成孤儿行 —— 那正是症状 D-3 的形态。宁可留下几张用户不再需要的卡，
也不能让学习记录凭空消失。

`test_existing_cards_are_never_deleted` 专门盯这条：重跑时少抽出来的卡**必须还在**。

**② 去重范围是"同一篇笔记"，不是全局**

跨笔记去重会让"同一概念在不同资料里的不同阐述"互相顶掉 —— 而它们的出处、
上下文都不同。`test_same_card_in_another_note_is_not_reused` 覆盖。

**③ 规范化只压缩空白，不删除空白**

`"甲\n乙"` 与 `"甲 乙"` 视为同一张卡（排版差异）；`"甲乙"` 与 `"甲 乙"`
**不是**（英文里空格是词边界，全删了 `machine learning` 会等于 `machinelearning`）。

判错方向是**合并两张不同的卡**，比漏判更糟 —— 所以这里不做模糊匹配，
模糊去重是 `detect_card_duplicates` 的职责。写测试时我一开始假设了
"删除空白"的行为，断言失败才发现两者必须分开，于是补了一条
`test_normalization_does_not_delete_whitespace` 把边界写死。

### AE.4 ⚠️ 回填必须调用**运行时那个函数**

迁移里回填 1183 行指纹时，第一版图省事在 SQL/Python 里另写了一套
"压缩空白 + 去首尾"的规则（因为 SQL 做不了 NFKC 归一）。这是个陷阱：

> 两边规则只要差一点（哪怕只是少一个 NFKC 归一），历史卡片的指纹就与
> "同样内容的新卡"不同 —— 重跑理解时每张历史卡都看起来"从没出现过"，
> 全被重新插入，**恰好造成这次要修的那种重复**。

改成在迁移里 `from .services.card_intake_service import card_content_hash`
直接调用同一份实现（函数内 import，运行时无循环依赖）。

真库自检里专门有一条：**抽样 200 张，指纹与运行时函数算出的零不一致**。
这条断言如果不去测，两边规则的漂移在功能上完全看不出来 ——
直到某天有人按了重跑。

### AE.5 质量门：拒收必须**可见**

`SaveOutcome` 有四个计数：`created` / `reused` / `rejected` / `truncated`。
只报 `created` 会有两种误导：

- **重跑时 `created=0`**，看起来像"什么都没做"，实际是复用了全部
  → 所以 `reused` 必须一起返回；
- **脏卡片被拦下时完全不出现**，"不入库"就变成了"静默少了几张"
  → 所以 `rejected` 带原因一起返回，并按原因聚合打 WARNING。

刻意**不为缺失的标题填占位**（旧代码填的是"未命名知识点"）：占位标题会让
一张本该丢弃的脏卡片看起来像正常卡片，而它入库后就再也没法自动清掉了。

单次上限只约束**新建**（复用不计入），否则"卡片数超过上限的笔记"
将永远无法通过重跑补差集 —— `test_cap_does_not_block_reruns` 覆盖。

### AE.6 验收

| 项 | 结果 |
|---|---|
| `tests/test_card_intake.py` | **27 passed**：指纹 5 / 质量门 5 / 幂等 9 / 上限与既有行为 8 |
| 全量后端测试 | 635 → **662 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |
| 真库迁移 | `content_hash` 已加并回填 1183 行；抽样零不一致；`integrity=ok`；其余表行数未变 |
| 备份 | `_backup/20260911-4-8-pre-card-hash/engramnote.db` |

### AE.7 仍未完成

- **存量脏卡片没有清理**：4 张正文 <10 字、2 张无 `source_text` 仍在库里。
  质量门只管入口，**不回删**（见 AE.3 ①）。要清理得单独做一次可预览、
  可回滚的运维动作 —— 而"删卡片"这件事本身需要先想清楚怎么处理
  `review_states`（那 6 张卡的复习状态）。
- **4.8 只覆盖"理解"这一条抽取路径**：扩展知识点
  （`generate_extension_knowledge`）与卡片关系推断（`infer_card_relations`）
  仍然是无条件插入。它们是另一条入口，同样的缺陷还在。
- **4.6 / 4.7 的基础已经就位但没有做**：Prompt 版本化与响应缓存都需要
  "识别同一份输入"的键。`content_hash` 解决的是"同一张卡"，
  而缓存需要的是"同一份 prompt + 输入"—— 形状不同，不能直接复用。
- **重跑理解没有 UI 入口**：后端的幂等已经好了，但"重新理解"这个动作
  目前只能通过理解接口触发，而前端没有暴露"重跑"按钮 ——
  也就是说这次修的路径，用户暂时还走不到（与附录 Z/AA 同类，但方向相反：
  这次是"修好了还没人用"）。

### AE.8 顺带发现：一个**依赖执行顺序**的测试（既存缺陷）

改完之后单独校验受影响文件时，`tests/test_week5_6_understanding.py` 报 3 个
失败。先用 `git stash` 对比确认**与本次改动无关**，再定位到根因：

```
LLMService._rate_limiter 是**类级单例**，在**首次**实例化时按当时的
settings.llm_max_rpm 建一次，此后整个进程复用。
```

而该文件的用例几乎都把 `llm_service.settings` 换成 `MagicMock` ——
`llm_max_rpm` 因此是自动生成的 MagicMock 属性。若它们恰好是进程里**第一个**
实例化 `LLMService` 的地方，MagicMock 就被固化进单例，同进程后续每次
`chat()` 都在 `RateLimiter.acquire` 里抛：

```
TypeError: '<=' not supported between instances of 'MagicMock' and 'int'
```

实测：

| 跑法 | 结果 |
|---|---|
| `pytest tests/test_week5_6_understanding.py` 单独跑 | **3 failed** |
| `pytest` 全量 | **全绿** |

全量能过只因为**前面的文件已经把单例建好了**，Mock 永远到不了限流器。

**这是一个"必须和别人一起跑才通过"的测试** —— 它不能用来判断改动有没有破坏
东西，而它恰恰会在最需要它的时候（只跑这一个文件排查问题）给出假警报。
修法是在该类上加一个 autouse fixture，在**每个用例之前**用真实的整数 rpm
把类级单例建好（而不是逐个用例补 `llm_max_rpm`），用例结束后再还原 ——
这样将来新增用例也不会重蹈覆辙。

⚠️ **产品侧的根因没有动**：类级单例在首次实例化时**冻结配置**，
与 conftest 里早已记录的"import/首次调用时冻结数据库地址"是同一类缺陷。
阶段 **4.5**（"限流器/信号量改为每 loop 惰性创建"）正是它的归属，
本附录只修测试侧，不越界做一半。

---

## 附录 AF · 阶段 4.7：LLM 响应缓存（2026-09-11）

### AF.1 交付

| 部件 | 位置 | 说明 |
|---|---|---|
| 模型 | **新增** `models/llm_cache.py` | `llm_cache` 表；**新表**由 `create_all` 建 |
| 服务 | **新增** `services/llm_cache_service.py` | `cache_key` / `lookup` / `store` / `purge_expired` / `stats` |
| 接线 | `llm_service.chat_detailed` | 查缓存在**配额检查之后、拿信号量之前**；写缓存在**成功之后** |
| 记账 | `llm_calls` += `cached` / `saved_tokens` | 命中**照样记账**，但记 0 用量 + 省下的量 |
| 迁移 | `database.py`：ALTER 加两列 | ⚠️ `llm_calls` 已存在，`create_all` **不会**加列（见 AF.6） |
| 接口 | `GET /api/llm/usage` += `cache` 段与 `cached_calls` / `saved_tokens` | 让节省**看得见** |
| 配置 | `llm_cache_enabled`（默认 **true**）/ `llm_cache_ttl_days`（30） | |
| 测试 | `tests/test_llm_cache.py`（**21 用例**） | 键 / 存取 / 记账口径 / 端到端少发一次请求 |

后端测试 662 → **683 passed / 3 skipped**；ruff 全绿。
真库：`llm_cache` 已建（14 列），`llm_calls` 两个新列已加，`integrity=ok`，
其余表行数一行未变。

### AF.2 为什么默认**开**（与 3.5 / 4.3 的默认关相反）

语义判分（3.5）与配额（4.3）都默认关，理由是它们**改变用户看到的行为**、
且出错时难以察觉。缓存不同：

> 它不改变"同一份输入得到什么" —— 它只是把**已经付过费的那份答案**再取出来一次。

而"重跑理解要再付一次全款"是实打实的浪费。默认关等于这个功能白做。

这个判断有个前提：**输入必须逐字节相同**才算命中（见 AF.3）。
理解管道满足这一点（提示词模板 + 资料原文，两次重跑逐字节相同）；
RAG 问答的上下文每次都不同，命中率天然接近零 —— 那不是缓存没生效，
而是那些请求本来就没有重复。

### AF.3 命中判定是逐字节相同，不是"相似"

```
key = sha256(provider + base_url + model + messages + temperature
             + max_tokens + response_format)
```

输入里任何一个字符不同，就是不同的键。这不是保守，而是**唯一能保证正确**的做法：
相似度匹配意味着"用 A 问题的答案回答 B 问题"，那比多花一次钱严重得多。

`test_any_input_difference_changes_the_key` 逐项验证（messages / temperature /
max_tokens / model / response_format / base_url）。

两个容易漏的字段：

- **`base_url`**：同一个模型名可以挂在不同的网关后面（`get_llm_config` 按 debug
  指向 GLM 或 DeepSeek），只按模型名做键会让两条链路互相串；
- **`sort_keys` + 固定分隔符**：字典迭代顺序跨进程不保证，不排序的话同一份输入
  在不同进程里可能算出不同的键 —— 症状是"缓存**偶尔**命中"（命中率忽高忽低），
  比完全不命中更难查。

### AF.4 🔴 命中时**必须**记 0 用量

这是本附录最容易埋雷的一处。`record_call` 现在这样处理命中：

```python
if cached:
    prompt_tokens = completion_tokens = total_tokens = 0
    cost, currency = None, None       # 不看调用方传进来的 usage
```

为什么不能照抄原始用量：**配额（4.3）是按 `total_tokens` 求和的**。
命中行若记下原始用量，就会把没花的钱算进配额 —— 症状是
**"开了缓存反而更快被限流"**，而这几乎不可能被联想到缓存。

省下的量记在单独一列 `saved_tokens`。测试 `test_cached_hit_records_no_cost`
故意传一份非零 usage，断言实现忽略它。

没有 `saved_tokens` 这一列，"开缓存省了多少钱"在任何报表上都看不见 ——
而**看不见的收益等于没有收益**，没人能据此决定要不要继续开。

### AF.5 位置与失败语义

| 动作 | 位置 | 理由 |
|---|---|---|
| 查缓存 | **配额检查之后**、拿信号量之前 | 超限时连缓存都不该查；而命中没有花钱，不该占用并发额度与限流令牌（放信号量里面会让一批重复请求白白排队） |
| 写缓存 | **成功响应之后** | 失败不写 —— 把失败缓存下来会把一次偶发故障固化成"这个输入永远失败" |
| 读失败 | 当作未命中 | 缓存是**旁路**，不是正确性的一部分。绝不因为缓存出问题而让调用失败 |
| 写失败 | 只打 WARNING | 同上 |
| 内容损坏 | **删掉那一行**再当未命中 | 留着它只会让之后每一次相同输入都白读一遍 |

### AF.6 ⚠️ 又一次踩到"新表会建、旧表不会加列"

`llm_cache` 是新表，`create_all` 直接建好；而 `llm_calls` 在 4.2 就已经存在，
`create_all` **不会**给它加 `cached` / `saved_tokens` 两列 ——
这正是 `database.py` 里反复写着的那句话：

> 新增一个模型字段时，必须同时在这里登记 —— 否则真库永远缺这一列，
> 而全新库却有（本轮实测踩到，表现为 `no such column: card_id`，
> 且只在真库复现）。

这次没有踩进去（动手前先查了真库有没有该表），但值得记一笔：
**同一个坑在本项目已经出现过至少两次**。

### AF.7 残余风险（写下来，因为无法消除）

缓存在 `chat_detailed` 这一层，也就是在**调用方校验之前**落库。
若模型某次返回了一份"JSON 能解析、内容不合格"的东西，它会被缓存，
之后相同输入一直拿到它，直到 TTL 到期。

三条缓解，都不是"消除"：

1. `llm_cache_ttl_days = 30` 到期即失效；
2. `purge_expired()` 可主动清理；
3. `llm_calls.cached=True` 的行在报表里可见，异常高的命中率能被注意到。

要做到"只缓存合格结果"，得把缓存挪到每个调用方（在它校验之后）——
那意味着每个场景各写一遍，且漏一个就少省一份钱。
目前的取舍是**统一在出口缓存 + 三条缓解**。

另一条已知边界：**key 里含模型名，但供应商可以在同一模型名后换权重**。
那时缓存会继续返回旧模型的输出，而日志上看不出异常 —— TTL 是唯一兜底。

**流式路径（`chat_stream`）不做缓存**：SSE 的 usage 在最后一个 chunk 才到，
缓存它需要在生成器里处理"部分消费即关闭"的语义。问答的上下文本来就每次不同，
收益很低，先不做 —— 这是取舍，不是遗漏。

### AF.8 顺带暴露的一处测试隔离缺陷

接入缓存后，`test_week5_6_understanding.py` 有 2 个用例失败：

```
test_chat_retry_on_failure      → 期望发 2 次请求，实际只发了 1 次
test_chat_all_retries_exhausted → 期望抛异常，实际成功返回
```

原因不是缓存错了，而是**那些用例没有声明 `test_db`**：它们落进 conftest 的
**会话级**临时库（所有用例共用），而三条 `chat()` 用例用着**完全相同的**
messages / 模型 / 参数 —— 于是前一条缓存的响应被后一条命中。

阶段 4.2 之后 `chat_detailed` 就会写记账表，4.7 之后还会写缓存：
**任何调用真实 `chat_detailed` 的用例现在都会碰数据库**，
因此必须声明 `test_db` 取得隔离。已给那三条用例补上。

这是"用例之间通过数据库互相影响"，根因是没做隔离，不是被测代码错了 ——
与 AE.8 那个"依赖执行顺序"的缺陷是同一家族。

### AF.9 验收

| 项 | 结果 |
|---|---|
| `tests/test_llm_cache.py` | **21 passed**：键 5 / 存取 8 / 记账口径 3 / 端到端 4 / 接口 1 |
| 核心用例 | **第二次相同输入不再发 HTTP 请求**（用请求次数直接证明，而不是断言"表里有行"——后者在"写了但读不到"时同样是绿的） |
| 全量后端测试 | 662 → **683 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |
| 真库 | `llm_cache` 已建、`llm_calls` 两列已加、`integrity=ok`、其余表行数未变 |
| 备份 | `_backup/20260911-4-7-pre-cache/engramnote.db` |

### AF.10 仍未完成

- **没有缓存的观测入口**：`GET /api/llm/usage` 的 `cache` 段有了，
  但没有界面消费（与 4.2/4.3 同一条：报表属阶段 6）。
- **没有清理定时任务**：`purge_expired()` 写好了，`llm_call_retention_days`
  也早就配好了，但**没有任何定时任务真的去调用它们**。长期运行下
  `llm_cache` 与 `llm_calls` 会持续增长。
- **4.6 Prompt 版本化仍未做**，但它与 4.7 **不构成依赖**：
  缓存键取完整输入（messages 里就有提示词），已经覆盖"提示词变了"。
  `prompt_version` 的真正用途是**溯源**（哪张卡是哪个版本的提示词产出的），
  是独立的一件事 —— 计划里把两者写成一对，这次核对后应当拆开看。
- **写得进、读不出的那种失败没有防线**：本轮实测踩到过一次
  （把 `get_session_factory` 这个**函数**当成了 sessionmaker 传进去，
  症状是写缓存静默失败、缓存"看起来开了"却永不命中，日志里只有一句
  `__aenter__`）。现在有端到端用例盯着"第二次不发请求"，
  但同类错误若发生在别处，仍只会表现为"命中率是 0"。


## 附录 AG · 阶段 4.1：抽出 LLMGateway（2026-09-11）

### AG.1 为什么这一项越晚越贵（做完之后的复盘）

4.2 / 4.3 / 4.7 这三轮各自都往 `chat_detailed` 里插了一段治理逻辑：
记账 22 行、配额 36 行、缓存 41 行。每一轮单看都很小，但三次之后
`llm_service.py` 从 831 行长到 **1273 行**，其中 **386 行是纯粹的调用策略**
（重试循环、限流、并发闸门、配额、缓存读写、记账）。

真正的问题不是行数，而是**三种不同性质的关注点挤在一个类里**：

| 关注点 | 内容 | 归属 |
|---|---|---|
| 业务 | 提示词、场景方法（摘要/提取/出题/问答/判分） | `LLMService` |
| 传输 | HTTP、SSE 流式、重试退避 | `LLMGateway` |
| 治理 | 限流、并发、配额、缓存、记账 | `LLMGateway` |

判断标准很简单：**只有"业务要问什么"变了才该改的代码**，就不该和
"这次调用要不要排队、要不要记账"放在一起。这次搬迁就是把后两类拿走。

### AG.2 搬走了什么

新文件 `backend/app/services/llm/gateway.py`（538 行，含说明性注释）：

| 成员 | 来源（搬迁前行号） | 职责 |
|---|---|---|
| `chat` / `chat_detailed` | `llm_service.py:188-391` | 非流式调用：限流 + 信号量 + 重试 + 记账 + 缓存 |
| `chat_stream` | `llm_service.py:392-504` | SSE 流式：限流 + 信号量 + 记账（**不做重试、不做缓存**） |
| `_enforce_quota` | `llm_service.py:152-187` | 调用**之前**的配额闸门 |
| `_lookup_cache` / `_store_cache` | `llm_service.py:89-151` | 响应缓存读写（4.7） |
| `_rate_limiter` / `_semaphore` | 类级单例 | 限流与并发闸门（语义**未改**，见 AG.4） |
| `from_settings()` | 新增 | 独立构造入口（Celery 任务、脚本用） |

`LLMService` 保留 `chat` / `chat_detailed` / `chat_stream` 三个**同名同签名**的
转发包装，并新增可选注入 `LLMService(gateway=...)`。

搬过去的是**逐行原文**（含 4.2/4.3/4.7 写下的注释与理由），不是重写 ——
这一点由 90 个既有用例证明：只有三处测试侧断言失败（见 AG.6），
治理行为本身一行都没有变。

### AG.3 为什么对外 API 一行都不改

全仓有 **15 个模块** `from ...llm_service import LLMService`
（`understanding_service` / `rag_service` / `assessment_service` /
`knowledge_link_service` / `graph_service` / `sm2_service` / Celery 任务 / 4 个 API 路由……）。

如果这次搬迁顺手改了调用方，改动量会从"移动 386 行"变成"改 20 个文件"，
而**收益完全相同**。更重要的是：不动调用方，4.2/4.3/4.7 的 **90 个用例
（写在搬迁之前，断言的是行为而不是位置）就能直接当回归证据用** ——
它们全绿，比任何新写的测试都更能说明"行为没变"。

代价是服务层多一次转发（一跳函数调用），以及读代码时要同时看两个类。
这个代价换到的是"搬迁可验证"，值。

**唯一的例外**是 `LLMService._provider/_model/_api_key/_base_url` 这四个属性：
`rag_service` 会读 `_provider` 回填给前端，测试也断言它们。它们现在是
**指向网关的只读快照**，不再是"真相本身"（真相在网关实例上）。

### AG.4 有意**没有**改的东西

| 没改 | 为什么 |
|---|---|
| 类级单例 `_rate_limiter` / `_semaphore` | "配置被冻结在首次实例化"是**已知缺陷**，归属 **4.5**（与"Celery 任务只用一个 `asyncio.run` 包裹"必须一起做）。只搬位置不改语义，才能让这次搬迁的风险最小 |
| `debug` 决定 provider | 4.10 的工作 |
| 提示词模板仍在 `llm_service.py` | 计划的 "<300 行" 还要再做一次纯机械的**提示词抽取**才算达成，见 AG.8 |
| 结构化输出校验（Pydantic） | 4.1 原文里有这一条，本轮**未做**。它与 4.6（Prompt 版本化 + 溯源）是同一类工作，单独做会与 4.6 打架 |
| 缓存开关的读取位置 | 由 `LLMService` 在构造时读自己的 `settings` 并**显式传给网关**，而不是让网关去读全局 `get_settings()`。既有测试靠替换 `llm_service.settings` 关缓存，网关若绕过它，那些用例会静默失效 |

### AG.5 顺带核出的一条结论：4.4 不一定需要 Redis

计划的 4.4 写的是"按用户 + 按供应商的令牌桶（**Redis 实现**）"。
但本项目**单写者已被强制**（`main.py::_enforce_single_writer()`），
也就是说部署形态是**单进程写入**。在单写者前提下，进程内按用户的桶
与 Redis 桶的差别只剩"重启后计数清零"这类可接受的行为差异。

因此 4.4 的现实做法是：**先做进程内的 per-user + per-provider 令牌桶**
（顺带把 4.5 的"每 loop 惰性创建"一起解决），Redis 留到真的要多实例时再引。
在磁盘空间紧张（曾因空间不足放弃 PG/Redis）的前提下，这一条尤其值得先按简单方案落地。

### AG.6 搬迁中真正出问题的只有三处（都在测试侧）

| 症状 | 根因 | 处理 |
|---|---|---|
| `test_llm_client_loop_rebuild` 报 `AttributeError` | 该用例从 `llm_service` 取 `get_llm_client`，搬迁后不再 re-export | 改为直接指向属主模块 `services/llm/client.py`（不为了迁就测试而继续转发） |
| 3 个配额用例报 `no attribute '_enforce_quota'` | 它们直接验证**内部方法** | 改为 `service.gateway._enforce_quota(...)`；`_patch_quota` 的注释同步更新（"函数内 import 所以打模块属性即生效"的机制没变） |
| 1 个配额用例 patch 错了模块 | `monkeypatch.setattr(llm_mod, "get_llm_client", ...)` 打的是服务层，而调用发生在网关层 | 改为打 `gateway_mod` |

⚠️ 第三处值得单独记住：**patch 目标必须是被测代码真正查表的地方**。
搬迁会移动这个"地方"，而 patch 到不存在的调用点**通常不会报错**，
只会静默失效（本例恰好因为断言"一个请求都不发"才暴露）。

另外，新增的 `tests/test_llm_gateway.py` 里那个"假网关"刻意**不继承**
`LLMGateway` —— 若转发依赖父类的实现细节，假网关就会因缺方法报错。

### AG.7 验收

| 项 | 结果 |
|---|---|
| `tests/test_llm_gateway.py`（新增） | **12 passed**：转发 3 / 接线 3 / 单一入口静态断言 3 / 调用顺序 3 |
| 核心顺序用例 | **缓存命中不占限流令牌**（第二次相同输入只取 1 次令牌、只发 1 次请求）；**配额超限时连缓存都不查**（`lookup` 调用数 = 0） |
| 全量后端测试 | 683 → **695 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |
| 行数 | `llm_service.py` **1273 → 985**；`gateway.py` 538 |
| 对外 API | 未变（15 个调用方零改动；4.2/4.3/4.7 的 90 个既有用例全绿） |
| 真库 | `integrity=ok`、`journal_mode=wal`、各表行数与搬迁前**逐项一致**（users 4 / notes 22 / cards 1183 / quiz 1058 / review_logs 194 / review_states 2241 / chunks 608） |
| 备份 | 本轮**无 schema 变更**，因此未新建备份（上一次为 `_backup/20260911-4-7-pre-cache`） |

### AG.8 仍未完成

- **<300 行的目标未达成**（现 985 行）。差的是一次提示词抽取 +
  4.11 的调试日志删除。这两件都是纯机械工作，但没有它们，
  "服务层只剩业务"这句话就还只是近似成立。
- **结构化输出校验未做**：现在仍是 `parse_json_tolerant` 的容错解析
  （它能修掉围栏与尾随逗号，但**不校验字段是否齐全**）。
  一张缺 `title` 的卡片仍然只能靠下游的 4.9 入库门拦下。
- **四个快照属性与网关之间没有断言约束**：它们在构造时同步一次，
  之后各是各的。好处是运行时无法把两者改得不一致，
  坏处是将来若有人给 `LLMService` 加"切换模型"的能力，
  就会出现两种真相。真要多模型时应当直接读 `service.gateway`，
  不要再复制快照。


如果暂时无法启动大重构，以下动作**当天可做**且有明确收益：

---

---

## 附录 B · 立即可以做的 10 件事（不改架构，1-2 天）


如果暂时无法启动大重构，以下动作**当天可做**且有明确收益：

1. `database.py:101-112` 加 `PRAGMA journal_mode=WAL`（实测当前为 `delete`），`busy_timeout` 改 30000
2. 删除 `database.py:537-544` 中针对 `review_logs` 的两条 DELETE
3. `embedding_tasks.py:246` 的 `sim = 1.0/(1.0+distance)` 改为 `1.0 - distance/2`（归一化向量下的正确余弦）
4. `sm2_service.py:169-189` 的填空题判分与 `:238-247` 的简答判分改为
   **精确匹配 + 用户自评**；至少**删除"字符集合重叠"这条**（`学器` 不该判对）
5. `config.py:230` `debug: bool = False`；`.env.example:113` 同步（停止把 bcrypt 哈希写进明文日志）
6. `nginx.conf` 的 `location /api/` 加 `client_max_body_size 500m;` 与 `proxy_buffering off;`
7. `frontend/.dockerignore` 加一行 `node_modules`（当前 Linux 镜像构建必然失败或臃肿）
8. `main.tsx` 外包一个 ErrorBoundary（当前任何渲染异常 = 整站白屏且刷新后复现）
9. `QuestionSets.tsx:65-94` 的分页 `while` 加 `&& page <= MAX_PAGES`（当前可无限请求后端）
10. `README.md` 与 `architecture.md` 中关于 **Vault 项目目录、端口、行数、alembic**
    的错误描述全部改掉或删除（见 §2.1 S-4）

## 附录 C · 与现有文档的关系

| 文档 | 处置 |
|---|---|
| `README.md` | 保留，重写"功能概览/技术栈/项目结构"三节；删除无法兑现的承诺（项目 Vault 路径、端口不一致等） |
| `docs/architecture.md` | **阶段 7 从零重写**。当前版本可作为"重构前快照"归档 |
| `docs/decisions.md` | 转为**只读历史归档**。新架构的决策记入新文件，不带 `F-xx` 编号 |
| `docs/code-review-report.md`（25KB） | 归档。其结论已被本方案覆盖 |
| `docs/archive/`（390KB） | 删除或移出仓库 |
| `docs/eslint-report*.txt` / `ruff-report*.txt`（约 470KB） | 删除（CI 里跑，不需要入库） |
| `参赛/` | 保留（参赛材料） |
| **本文件** | 重构期间的唯一执行依据。阶段完成后逐条勾销 |


## 附录 AH · 阶段 4.4 / 4.5：分桶限流与任务级事件循环（2026-09-11）

这两项计划里是分开的，实际必须一起做：4.4 要"按用户分桶"，
而桶（以及信号量）里都有 `asyncio.Lock`/`Semaphore`，**它们绑定事件循环** ——
4.5 不先解决"资源属于哪个 loop"，4.4 分出来的桶就会在 Celery 里跨 loop 复用。

### AH.1 4.5 的问题不是"循环多了几次"，而是三件具体的事

改造前 `understand_document_task` 一个任务体里有 5 次 `asyncio.run()`
（`clean_document_task` 有 7 次），每次新建一个 loop、跑完关掉：

1. **连接池全部作废**：httpx 客户端按 loop 判定失效并重建
   （`client.py` 的既有逻辑），于是"进程级共享客户端"在 Celery 里
   实际是"每次调用新建一个"——连接复用与 TLS 握手优化一次都没生效；
2. **数据库连接跨 loop 复用**：worker 侧的 `aiosqlite` 连接是模块级单例
   （`tasks/common.py`），却在 5 个不同的 loop 里被使用。目前"能跑"
   依赖 aiosqlite 内部按当前 loop 新建 Future 的实现细节，属于**偶然正确**；
3. **没有任何东西能"一次任务内共享"**：进度缓存、会话、临时句柄都无处安放。

### AH.2 做法：一个任务一个 loop（以及为什么偏离了计划的字面要求）

新增 `backend/app/tasks/loop.py`：

    with task_loop("understand_document"):
        run_async(_update_note_status(...))   # ┐
        run_async(_report_progress(...))      # │ 全部跑在同一个 loop 上
        run_async(_understand_document(...))  # │
        run_async(mark_succeeded(...))        # ┘

`run_async` 在 `task_loop` 之外调用时**退回 `asyncio.run`**，因此
`tasks/common.py` 里的公共助手不必关心自己是在任务里还是在脚本里被调用。

⚠️ **偏离之处**：计划写的是"`asyncio.run()` 一次包裹整个任务体"。
字面上那要求把 6 个任务模块的函数体改写成 `async def`，并把 Celery 的
`self.retry()`、日志、`finally` 清理全部搬进协程 —— 那些是**同步**的
Celery 语义，搬进去只会让重试路径更难读。本方案达到的是同一目的
（**一个任务内的所有异步代码共享一个 loop**），而任务体保持同步。
差别只在"task 本身是不是协程"，对 AH.1 那三件事毫无影响。

退出时 `task_loop` 会取消挂起协程、关闭异步生成器、再 `close()`，
并把 thread-local 置空 —— 留着一个已关闭的 loop 会让下一个任务报
`Event loop is closed`（旧实现踩过的正是这个）。

### AH.3 4.4：三层令牌桶，以及"总闸门必须最后取"

`KeyedRateLimiter` 按 key 分桶（每 key 一个 `RateLimiter`），网关把它们串成三层：

    1. 按用户     user_id（无上下文 → __anonymous__）   LLM_USER_MAX_RPM
    2. 按供应商   provider                              LLM_PROVIDER_MAX_RPM
    3. 总闸门     进程级                                LLM_MAX_RPM

**顺序是有意的**：前两层等待时**不占用**总闸门令牌。反过来（先取总闸门再等
个人限额）会让一个被个人限额拖住的用户把全局令牌一起扣住，反而更容易饿死别人。

两个刻意的设计：

- **无用户上下文 = 共用一个 `__anonymous__` 桶，不是免检**。否则任何
  "忘了接 `llm_context`"的路径都成了绕过限流的后门，而这类路径恰恰最可能是
  批量脚本。
- **`0` 表示不限，且不建桶**：默认关闭时零开销，也不会在内存里留下空桶。

### AH.4 为什么后两层**默认关闭**（一个需要确认的取舍）

计划里的验收是"单用户不饿死他人"。但把 `LLM_USER_MAX_RPM` 默认设成
`LLM_MAX_RPM` 的一半（例如 5），意味着**单用户场景的速率当场减半** ——
本项目实际使用人数只有 4 个，绝大多数时间只有一个人在跑，这正是
"为了一个假想的并发问题，惩罚真实的日常使用"。

因此默认值是 **0（不限）**，机制完整可用、测试全覆盖，打开只需一行配置
（`.env.example` 里已写明建议值）。这是**取舍而非遗漏**：
公平性只有在真的多人同时使用时才有价值。

⚠️ 这一条与计划原文（"单用户不饿死他人"作为验收）存在张力：
机制已实现并验证，但**默认不生效**。若要按要求默认生效，
把 `llm_user_max_rpm` 的默认值从 0 改成 5 即可 —— 这属于产品决策，留待确认。

### AH.5 顺带修掉的根因：配置不再被"冻结在首次实例化"

限流器/信号量原来是**类级单例、首次实例化时建一次**，于是：

- 配置被冻结在那一刻（与 conftest 记录的"import 时冻结数据库地址"同类）；
- 测试里的表现是**依赖执行顺序**（附录 AE.8：单独跑 3 failed、全量跑全绿）。

现在改为 `_resources()`：以**运行中的 loop 对象**为键
（`weakref.WeakKeyDictionary`，loop 被回收即自动清理），在第一次真正调用时
按**当时的配置**创建。rpm 也改为**网关自己从全局配置读**，不接受服务层传入 ——
于是"测试把 `llm_service.settings` 换成 MagicMock"再也到不了令牌桶。

**结果**：附录 AE.8 那个为兜底而加的 `_real_rate_limiter` fixture
连同它的长篇解释**被删除**。靠 fixture 兜住的缺陷，修好之后就该把 fixture
也删掉，否则它会掩盖同一次回归的下一次发生；替代它的是
`test_mocked_settings_cannot_poison_the_limiter`（正向用例，不再依赖执行顺序）。

### AH.6 4.4 的 Redis 问题（结论：暂不需要）

计划写的是"按用户 + 按供应商的令牌桶（**Redis 实现**）"。
本项目**单写者已被强制**（`main.py::_enforce_single_writer()`），
部署形态是单进程写入；进程内桶与 Redis 桶的差别只剩"重启后计数清零"
这类可接受的行为差异。在磁盘空间紧张（曾因空间不足放弃 PG/Redis）的前提下，
**先用进程内桶**是明确更优的选择；真要多实例时再引 Redis，
届时替换点只有 `_LoopResources` 一处。

### AH.7 验收

| 项 | 结果 |
|---|---|
| `tests/test_task_loop.py`（新增 8 例） | 同一 loop 共享 / 不同任务不同 loop / 退出关闭并清空 thread-local / 无任务时退回 `asyncio.run` / 异常语义与 `asyncio.run` 一致 / 挂起协程被取消 / **真实助手端到端：两次 `common.py` 调用跑在同一 loop 且真的落库** |
| `tests/test_rate_limit_buckets.py`（新增 13 例） | 单桶耗尽后等待 60s（虚拟时钟精确断言）/ 分桶隔离 / `0` 不建桶 / rpm 变更立即生效 / 匿名共桶 / 供应商桶独立 / 总闸门仍生效 / 每 loop 一份资源 |
| 全量后端测试 | 695 → **717 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |
| 真库 | 本轮**无 schema 变更**、无数据迁移，`integrity=ok`；表行数与上一轮逐项一致 |
| 删除的测试兜底 | `test_week5_6_understanding._real_rate_limiter`（根因已修） |

### AH.8 仍未完成

- **后两层默认关闭**（见 AH.4）：机制在、测试在、配置默认值让它在生产中不生效。
- **`asyncio.run` 仍在两处**：`tasks/celery_app.py:197`（worker 启动时 `init_db()`）
  与 `loop.run_async` 的回退分支。前者是进程启动的一次性动作、不在任务内，
  没有共享 loop 的需求；后者是刻意的回退语义。
- **桶的清理策略未做**：`KeyedRateLimiter` 的桶数上界是 key 基数
  （用户数 / 供应商数），当前规模下无需清理；若将来按 key 分桶的维度
  变成高基数（例如按 API key），需要补一个空闲桶回收。
- **进度缓存仍是模块级字典**：`_PROGRESS_CACHE` 在 worker 进程内共享。
  现在"一个任务一个 loop"了，它其实可以挂到任务上 —— 但它按 task_id 索引
  且有 `clear_progress_cache` 显式清理，没有实际风险，因此这轮没动。

---

## 附录 AI · 阶段 4.1 收尾 + 4.11：提示词与场景方法的最后两步（2026-09-11）

附录 AG 把**调用策略**搬走之后，`llm_service.py` 还剩 985 行。这一轮把剩下
两类内容也搬走，于是计划里"<300 行"这个验收**真的达成了**：

| 文件 | 行数 | 职责 |
|---|---|---|
| `services/llm_service.py` | **221** | 接线：构造网关、转发三个 chat 方法、re-export |
| `services/llm/gateway.py` | 625 | 传输 + 治理（重试/限流/并发/配额/缓存/记账） |
| `services/llm/scenes.py` | 556 | 14 个业务场景方法（组装请求 + 解析结果） |
| `services/llm/prompts.py` | 381 | 全部提示词文本 |

⚠️ **行数不是目的**：搬运不减少代码，它只是让每个文件只讲一件事。
真正的删除只有 4.11 那 3 条日志。把这一点写下来，是为了避免以后有人
"为了行数"继续拆 —— 下一个该拆的信号是"某个文件里出现了第二种关注点"，
不是"某个文件超过 N 行"。

### AI.1 为什么提示词要先搬（以及为什么必须先写护栏）

提示词搬移的风险**不在功能**，而在**缓存键**：4.7 的缓存键是完整输入的
sha256（messages 的每一个字符都在内）。少一个空格、多一个换行，
所有既有缓存条目就全部失效 —— 而症状只是"命中率变成 0"，**不报任何错**。

所以这一轮的做法是**先写护栏再动手**：
`tests/test_prompt_golden.py` 在搬移**之前**算出 11 个场景"真正送给模型的
那串 messages"的摘要并固化下来（当时提示词还在 `llm_service.py` 里），
搬移之后必须一字不差。**11 个摘要全部匹配**，因此"搬移没改一个字符"
这句话是有证据的，而不是"我检查过了"。

这个护栏同时成了以后改提示词的提醒：有意改就**连同摘要一起改**，
而不是顺手改。

### AI.2 提示词是业务逻辑，值得单独一个文件

搬移前，"出题时不要超纲"这一条藏在 90 行的 `generate_questions` 中间。
搬移后 `prompts.py` 里能一眼看完产品对模型说过的所有话 ——
对这个产品来说，这是最核心的业务规则，它以前**没有任何一个地方**
能被整体审视。

### AI.3 场景方法用 mixin 而不是模块级函数

`scenes.py` 里是 `class SceneMethods`，`LLMService` 通过
`class LLMService(SceneMethods)` 混入。理由与 4.1 搬迁时一致：
调用方写的是 `service.generate_questions(...)`，改成
`scenes.generate_questions(service, ...)` 要动 15 个模块，
而**收益完全相同**。

### AI.4 4.11：删掉脚手架，保留异常

`generate_questions_batch` 里有 3 条每次出题都打印的 `logger.info`：
原始响应类型、每个候选键的值类型与长度、首个元素的键。
它们是这段解析逻辑反复调不通时加的排查脚手架，**对生产运维没有任何意义**
（既不是错误，也不是可聚合的指标）。

保留的是 2 条 warning：「第一个元素不是字典」「未找到有效的题目列表」——
它们指向"模型返回的结构不符合约定"，那是需要有人知道的真实异常。
"删调试日志"不等于"把日志删干净"：区别在于**这条日志是否指向一个
需要有人采取行动的状态**。

### AI.5 顺带发现的两件事

1. **静态守卫会随代码搬移而"静默空转"**。
   `tests/test_fixes.py::test_max_tokens_no_4096` 原本只读 `llm_service.py`；
   场景方法搬走之后，那个文件里已经没有场景代码，断言于是**永远通过**。
   已改为扫描整个 `app/services` 树（跳过 `asr/`，理由见下），
   并加了一条"扫描范围里必须有 `llm/scenes.py`"的防呆断言。
   同类问题也存在于 `test_llm_gateway.py` 的"服务层不得出现传输符号"，
   已改为同时检查 `llm_service.py` 与 `llm/scenes.py`。
2. **静态断言必须查代码，不能查散文**。
   加上 `scenes.py` 之后那条守卫立刻失败 —— 原因是 `scenes.py` 的模块说明里
   写着"本模块不得出现 `httpx` / `get_llm_client` / `record_call` 等符号"，
   **解释禁令的文字本身**触发了禁令。已改为先用 `ast` 剥掉文档字符串与注释
   再检查。正确的反应是让检查对准代码，而不是把文档写得含糊。

### AI.6 一个**未修**的发现：ASR 的 `max_tokens=4096`

全树扫描顺带发现 `services/asr/asr_engine.py:220` 的标点恢复调用用的是
`max_tokens=4096`。**没有改**，理由：

- 它的输出是**纯文本**而不是被 `json.loads` 解析的结构化结果，
  "截断必然导致解析失败"这个前提不成立；
- 但截断确实是静默的（`finish_reason` 没有检查），长转写会被截掉尾巴
  而无人知晓 —— 这是**真实但独立**的问题，改动会影响 ASR 输出长度与耗时，
  应当单独评估（本轮不顺手改，因为顺手改正是这次重构要消除的习惯）。

### AI.7 验收

| 项 | 结果 |
|---|---|
| `tests/test_prompt_golden.py`（新增 11 例） | 搬移前后摘要**逐字节一致**（搬移前固化，见 AI.1） |
| 全量后端测试 | 717 → **728 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |
| 行数 | `llm_service.py` **985 → 221**（计划验收 <300 ✅） |
| 真库 | 本轮无 schema 变更；`integrity=ok`；表行数与上一轮逐项一致 |
| 调用方 | 零改动（`LLMService` 方法名与签名完全不变） |

### AI.8 仍未完成

- **结构化输出校验（Pydantic）**：4.1 原文里的一条，两轮都没做。
  现在仍是 `parse_json_tolerant` 的容错解析 + 各场景手写的
  "取哪个键、是不是 list"判断。一张缺 `title` 的卡片仍只能靠 4.9 的
  入库门拦下。它与 4.6（Prompt 版本化）是同一类工作，一起做更省。
- **场景方法仍在一个 556 行的文件里**：没有再按域拆。理由见开头那句 ——
  拆分的信号是"出现了第二种关注点"，而这 14 个方法确实只有一种关注点。
- **`prompts.py` 没有版本号**：4.6 会给每个提示词一个 `prompt_version`
  并写进 cards/quiz_items，用于**溯源**（哪张卡是哪个版本的提示词产出的）。
  目前只有摘要护栏，能回答"变没变"，回答不了"是哪一版"。

---

## 附录 AJ · 阶段 4.6：Prompt 版本化（2026-09-11）

### AJ.1 这一列要回答的问题

改了提示词之后，"新版是不是更好"**只能靠数据回答**。而数据要能按版本分组：
把卡片与题目按 `prompt_version` 切开，再看各组的复习表现（保持率、判分分布、
被标为盲点的比例）。没有这一列时，同一张表里混着不同提示词产出的行，
任何分组比较都做不了 —— 只剩"感觉新版好一些"。

### AJ.2 为什么是手工维护的字符串，而不是提示词文本的哈希

哈希看起来更省事（改文本自动变版本），但它**答不了业务问题**：
评审时要能说清"我们说的是第 3 版"，而 `a3f9c1e2` 既不能排序、也无法引用。

手工维护的代价是"可能忘记改"。这一条由测试兜住，而且是**两条测试配合**：

| 改动 | 结果 |
|---|---|
| 只改提示词文本 | `test_prompt_golden` 失败（摘要对不上） |
| 改了文本 + 更新摘要，但忘改版本 | `test_prompt_version` 失败（摘要表里记的版本对不上） |
| 改文本 + 更新摘要 + 升版本 | 全绿 —— 而这正是"有意识地换了一版" |

于是"忘记升版本"不再可能发生，手工维护版本号才成为可接受的方案。

### AJ.3 未知就是未知：三处刻意的"不兜底"

1. `prompt_version("没登记的名字")` 返回 **None**，不返回 `"1"`；
2. 迁移**不回填**历史行 —— 本列引入前的 1183 张卡片、1058 道题
   当时用的哪一版提示词已无从得知（文本改过多次且没有记录）。
   填 `"1"` 会让它们集体伪装成第一版，从而污染"第一版表现如何"这个统计；
3. 入库时调用方没给版本 → 写 NULL，不由写入层猜一个默认值。

这三条与 4.2 的"没配价格时 `cost` 记 NULL 而不是 0"是同一条原则：
**把"不知道"记成某个具体值，是最难被发现的数据污染** —— 它不会报错，
只会让所有按该维度的统计悄悄失真。

### AJ.4 写在哪两个地方

| 表 | 列 | 写入点 |
|---|---|---|
| `knowledge_cards` | `prompt_version` | 理解会话（`understanding_session`）、联合分析（`combined_analysis_session`）、拓展生成（`generate_extension_knowledge`） |
| `quiz_items` | `prompt_version` | 出题会话（`question_session`） |

**不加索引**：这一列的用途是按版本**分组聚合**，不是逐行查找；
千级行上全表扫描比维护索引更省。

### AJ.5 迁移的验证方式（这一轮刻意做的区分）

新库的列由 `create_all` 建出来 —— 用全新临时库测"列在不在"，
测的其实是模型定义，**测不出真库能不能升级**。因此测试里先造一个
"没有该列的旧库"（两张表 + 各一行历史数据），再跑真实的 `_migrate_sqlite`，
断言：列被 ALTER 补上、历史行为 NULL。真库上另外执行了一次并逐项核对：

    迁移前：1183 卡片 / 1058 题目 / 194 复习日志 / 2241 复习状态，两列均不存在
    迁移后：行数逐项一致，两列存在，两表 NULL 版本行数 = 0（历史行未被回填）

### AJ.6 顺带修掉一颗**定时炸弹**（与本项无关的既有缺陷）

`test_llm_accounting.py::test_time_window_is_half_open` 用**硬编码**的
`NOW = 2026-09-11 09:00Z` 构造查询窗口 `[NOW-1d, NOW+1d)`，而被查询的行
按**真实时钟**插入。于是只要机器时钟超过 `NOW+1d`，用例必然失败，
且失败信息（`assert 0 == 1`）与被测性质"左闭右开"毫无关系。

本轮实测：机器时钟跳到 2026-09-13 后它果然变红。已改为以**行自己的
`created_at`** 为基准构造窗口，并补了一条"`until` 恰好等于该行时刻时不含它"
的断言 —— 半开边界这个性质现在是真的在被测，而不是靠日历碰运气。

这一类缺陷值得单独记下：**用固定时间常量做时间窗测试，等于给测试设了保质期**，
而它失效的时机恰好是"几天后你正在改别的东西"。

### AJ.7 验收

| 项 | 结果 |
|---|---|
| `tests/test_prompt_version.py`（新增 9 例） | 版本格式 / 未登记返回 None / 登记表覆盖 / **版本与摘要绑定** / 卡片写入版本 / 缺省写 NULL / **旧库 ALTER 迁移** / 历史行 NULL |
| `tests/test_prompt_golden.py` | 摘要表升级为 `(版本, 摘要)`，11 个场景全绿 |
| 全量后端测试 | 728 → **737 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |
| 真库 | 两列已补、行数逐项一致、`integrity=ok`；历史行版本保持 NULL |
| 备份 | `_backup/20260911-4-6-pre-prompt-version/engramnote.db` |

### AJ.8 仍未完成

- **没有消费这一列的报表**：现在"能分组"，但没有任何接口/界面展示
  "哪一版提示词产出的卡片复习表现更好"（属阶段 6 的报表工作）。
  换句话说：**溯源管道通了，还没有人读它**。
- **`llm_calls` 没有 `prompt_version`**：成本账与提示词版本目前无法直接关联
  （`llm_calls.scene` 能区分场景，但不能区分同一场景的不同版本）。
  要加的话是多一列 + 网关从 `prompts` 读版本，本轮没做 —— 卡片/题目两处
  已经足够回答"版本 → 学习效果"，而那一列要回答的是"版本 → 成本"。
- **结构化输出校验（Pydantic）仍未做**：4.1 原文里的一条，三轮都没做，
  现在它比之前更值得做（有版本号之后，"这一版字段缺失率多少"是可度量的）。

---

## 附录 AK · 阶段 4.10：把 `debug` 拆成三个开关（2026-09-11）

### AK.1 一个布尔值管四件事

改造前 `debug` 同时决定：

1. SQLAlchemy 的 `echo`（SQL 明文进日志）；
2. FastAPI 的 `debug=`（异常 traceback 回吐给客户端）；
3. JWT 密钥策略（dev 可零配置自动生成，prod 必须显式配置）；
4. LLM 供应商（dev 走 GLM，prod 走 DeepSeek）。

后果不是"不够灵活"，而是**每一件想单独做的事都要付出另外三件的代价**：
要在生产看 SQL，就得同时把供应商换成 GLM 并把 traceback 暴露出去。
§2.5 E-5 那个"bcrypt 哈希与全部卡片正文进日志"的缺陷，正是这套耦合的产物
（当时 `debug` 默认 True，于是它默认发生）。

### AK.2 拆成什么

| 开关 | 默认 | 只回答一个问题 |
|---|---|---|
| `app_env` | `prod` | 是不是开发环境（JWT 零配置、traceback 是否回吐） |
| `log_sql` | `false` | 要不要把 SQL 打进日志 |
| `llm_provider` | `auto` | 用哪个供应商（`auto` = 与改造前一致：dev→GLM，否则→DeepSeek） |

`debug` **保留**为遗留别名：只折叠进 `app_env`，不再影响另外两件。
保留的理由是兼容既有 `.env`（升级后仍应拿到开发环境行为，
否则会出现"本地起不来"这种与本次改动无关的故障）。

### AK.3 一处**有意**的行为变化（需要知道）

`DEBUG=true` **不再**打开 SQL 日志。

- 为什么改：那些日志含 bcrypt 哈希与知识卡片/题目正文。它们以前跟着
  debug 一起来，而"只是想本地跑起来"的人不会预期这件事；
- 代价：习惯了 `DEBUG=true` 的开发者会发现 SQL 日志没了，
  需要显式加一行 `LOG_SQL=true`；
- 为什么可接受：这正是"让它成为一个有意识的动作"——
  本项要修的恰恰是"顺手就打开了"。

测试把这条变化**钉住**（`test_legacy_debug_no_longer_enables_sql_logging`），
所以它是一处被告知的变更，而不是一处被发现的变更。

### AK.4 验收

| 项 | 结果 |
|---|---|
| `tests/test_env_switches.py`（新增 15 例） | 默认生产安全 / **生产开 SQL 日志不影响供应商** / dev 可用 DeepSeek / prod 可用 GLM / 遗留 `DEBUG` 兼容 / 遗留 `DEBUG` **不**开 SQL 日志 / 非法取值拒绝 / JWT 防线未松 / **三处接线静态核对**（echo←log_sql、FastAPI debug←app_env、`get_llm_config` 不再读 debug） |
| 全量后端测试 | 737 → **752 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |
| 真实配置核对 | `app_env=prod`、`log_sql=false`、`llm_provider=auto` → DeepSeek、`app.debug=False`：**与改造前实际行为一致**（既有 `.env` 只有 `DEBUG=false`，因此对现网零变化） |

### AK.5 仍未完成

- **没有 `is_dev` 之外的"环境分层"**（如 test/staging）：当前只有两档。
  真要加第三档时，`log_sql` / `llm_provider` 都是独立开关，
  不需要再动它们 —— 这正是这次拆分的意义。
- **`.env.example` 与 `README` 的说明同步**属 §2.1 S-4（下一项）。

---

## 附录 AL · 收口：LLM 账本与缓存的清理定时任务（2026-09-11）

### AL.1 起点：清理逻辑早就写好了，缺的是"有人调用它"

附录 AF.10 记着这条：`purge_expired()` 写好了、`llm_call_retention_days`
也早就配好了（默认 365），但**没有任何定时任务真的去调用它们**。
于是 `llm_calls` 与 `llm_cache` 只增不减 —— 而这两张表是越用长得越快的那类：

- `llm_calls`：每次 LLM 调用一行（理解一篇长文档就是几十行）；
- `llm_cache`：每个不同输入一行。

在"磁盘曾经不够用、因此放弃了 PG/Redis"的前提下，
让两张日志表无界增长是**明确的**缺陷，不是"以后再说"。

### AL.2 两张表被删的东西性质完全不同

| 表 | 内容 | 删除口径 | 删错了会怎样 |
|---|---|---|---|
| `llm_cache` | **可再生的派生数据**（同一输入再问一次就有） | 过期即删（TTL 到期） | 多花一次钱 |
| `llm_calls` | **不可再生的账本**（花了多少钱的唯一记录） | 按保留期删，默认 365 天 | **永远算不出那段时间的花费** |

因此实现上有三条刻意的约束：

1. `purge_old_calls(retention_days <= 0)` → **直接返回 0，什么都不删**。
   这是最容易写反的一处：若把 0 实现成"cutoff = now"，就会把全部历史
   一次性删光 —— 而 0 的本意恰恰是"永久保留"（测试专门钉住了这一条）；
2. 清理任务把两类删除行数**分别**打日志：把"删掉了一年的账本"与
   "清了一堆过期缓存"混成一个数字，会让人无法判断该不该紧张；
3. Beat 排在**每日备份之后**（03:30 备份 → 04:30 清理）：
   先留下快照再删数据；顺序反了的话，"昨天备份里还有的账本"
   会在发现异常时已经无从对照。

### AL.3 为什么不是"永不删除"

保留期的默认值是 365 天，而不是"永久"：账本的价值随时间衰减
（"去年同期花了多少"是合理的回溯需求，"三年前某一天花了多少"不是），
而磁盘成本是持续的。需要永久保留就配 `LLM_CALL_RETENTION_DAYS=0` ——
但那是一个**要付代价的选择**，应当被显式做出。

### AL.4 验收

| 项 | 结果 |
|---|---|
| `tests/test_llm_ledger_cleanup.py`（新增 6 例） | 超期删除 / **保留期内一行不少** / **`0` = 永久保留（不是删全部）** / 不误删缓存 / 任务挂在 Beat 上 / 任务名已在 Celery 注册表 |
| 全量后端测试 | 752 → **758 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |
| 真库 | 本轮无 schema 变更（两张表都是既有表）；清理逻辑只删行、不改结构 |

### AL.5 仍未完成

- **清理没有"效果观测"**：日志里有删除行数，但没有指标/报表说明
  "账本现在多少行、增速多少"。真要看趋势得手工查库。
- **备份保留与账本保留是两套独立策略**（`backup_keep` 14 份 vs
  `llm_call_retention_days` 365 天），互相不感知：
  备份里可能含有已经被清理掉的账本行（这是**好事**，是恢复手段），
  但没有任何地方说明这一点 —— 需要恢复历史账本时得知道去翻备份。


---

## 附录 AM · §2.1 S-4：文档与对外描述的一致性（2026-09-11）

### AM.1 为什么这件事值得单独做

`README.md` 是**唯一会被外人读到的文档**。它此前有 5 处与代码不符的断言，
每一处都足以让照着做的人浪费半天：

| README 原文 | 实际 | 危害 |
|---|---|---|
| 后端端口 **8000**（多处，含手动启动命令） | `start.bat` 里是 `BACKEND_PORT=8001` | 照命令启动后前端代理指向空端口，"页面打不开" |
| 向量存储 **Chroma** | 向量存在 `chunks` 表（阶段 2.4 起），chromadb 已从依赖移除 | 以为需要装 Chroma / 去 `data/chroma/` 找数据 |
| 混合检索 **三路（含 n-gram）** | **两路**（n-gram 通道按阶段 2.6 删除） | 按三路理解代码，找不到第三路 |
| "数据库迁移 004 / Alembic 迁移脚本" | 运行时迁移是 `init_db()` + `_migrate_sqlite`，**不调用 alembic** | 以为改 schema 要写 alembic 迁移 |
| 容器化 "Docker + Compose + Nginx，一键部署" | 仓库里有这些文件，但本项目实际以本地进程运行；曾因磁盘不足明确放弃容器化 | 按文档去 docker compose up，撞上一堆已知问题 |

### AM.2 处置

| 文档 | 处置 |
|---|---|
| `README.md` | **逐条修正**上面 5 处；顶部加"文档校准"说明（列出改了哪几项）；架构级说明指向 overhaul-plan。"Docker 部署"一节加上"未经本项目验证 + 当前不是 Docker 部署 + 两条已知未修问题"的横幅（**保留命令**，因为文件还在仓库里，但明确它不是可用路径） |
| `docs/architecture.md` | 顶部加 **"重构前快照"** 横幅，列出已被改变的关键结构（向量存储/检索/调度/LLM 调用层/配置），并说明按计划在**阶段 7** 从零重写 |
| `docs/decisions.md` | 转为 **只读历史归档**：横幅写明"不再更新、不再是权威依据"，新决策记入 overhaul-plan 附录且**不再新增 F-xx 编号**；保留不删的理由也写进去了（F-20/F-30/F-33 这类踩坑知识在重构后依然成立） |

### AM.3 一个刻意的选择：**保留** architecture.md 而不是删掉

计划里它属于"阶段 7 从零重写"的对象，最省事的做法是现在删掉、等重写。
不这么做的理由：**重构的收益需要对照才能说清**。
"把 Chroma 换成 chunks 表""把 SM-2 换成 FSRS"这类结论，
只有和"改之前是什么样"放在一起才有说服力；
删掉旧文档，等于把已经付过学费的那部分知识一起丢掉。

这与 `decisions.md` 的处置是同一条原则：**归档 + 明确标注，而不是删除**。

### AM.4 验收

| 项 | 结果 |
|---|---|
| README 断言核对 | 5 处全部按代码实测修正（端口取自 `start.bat`、向量与检索取自 `chunk_search_service`/`rag_service`、迁移取自 `database.py`、容器化按实际部署方式说明） |
| 三份文档的定位 | README=对外说明、architecture.md=重构前快照、decisions.md=只读归档、overhaul-plan.md=**唯一执行依据**（四者互不冲突） |
| 代码/测试 | **本轮无代码改动**，仅文档；全量测试保持 **758 passed / 3 skipped** |

### AM.5 仍未完成

- **`docs/architecture.md` 的重写属阶段 7**（计划的安排），本轮只标注状态；
- **`docs/archive/`（390KB）与 `docs/*-report*.txt`（约 470KB）未清理**：
  计划里写的是"删除或移出仓库"，但那是**删除历史文件**，
  与"删除数据库里的数据"一样属于需要用户确认的动作，因此没有自行处理；
- **`参赛/` 目录未动**（计划：保留）。

---

## 附录 AN · 阶段 4.1 最后一条：结构化输出校验（Pydantic）（2026-09-11）

阶段 4 的验收表里，4.1 原文写着"统一入口、**结构化输出校验（Pydantic）**、
重试/退避/熔断"。前三轮（AG/AI/AJ）把统一入口、重试、退避、限流都做完了，
这一条一直挂着 —— 本轮补上，阶段 4 至此没有遗留条目。

### AN.1 改造前字段级违规的两种处理方式，都不可取

| 处理方式 | 例子 | 后果 |
|---|---|---|
| **静默强制转换** | `card_type` 不认识 → 当 `concept`；`question_type` 不认识 → 当 `choice` | "模型正在乱返回"永远不可见 |
| **靠下游兜底** | 缺 `title` 的卡片由 4.9 入库门拦下 | 只覆盖卡片：题目、拓展、联合分析、关系**当时都没有**对应的门 |

还有一处更隐蔽的兜底：拓展卡片缺标题时被写成 `"未命名拓展知识点"` ——
于是库里出现一堆同名卡片，而"模型没按格式返回"完全看不出来。

### AN.2 新增 `services/llm/structured.py`

四个模型 + 一个校验器：

| 模型 | 覆盖路径 | 认不出来时 |
|---|---|---|
| `CardPoint` | 理解管道（入库前）、联合分析（regular / blind_spot） | 枚举 → **修正并记录**；缺 title/content → 拒绝 |
| `QuizQuestion` | 出题管道 | 枚举 → 修正并记录；缺 question/answer → 拒绝；`options` 字符串 → 规范化成列表 |
| `ExtensionPoint` | 拓展生成 | 缺标题 → 拒绝（**不再**兜底成"未命名拓展知识点"） |
| `CardRelationPoint` | 关系推断 | `relation_type` 认不出来 → **拒绝**（造一条错误的边会被用户当成事实） |
| `GradeResult` | 简答判分 | `verdict` 认不出来 → **拒绝整条**（猜一个判分结论会改变复习间隔，且**无法事后纠正**） |

`validate_items(raw, model)` 返回 `valid`（模型实例）+ `issues`（每条处置），
**永不抛异常**。

### AN.3 三条设计原则

**1. 逐条校验，不整批拒绝。** 长批次里个别条目坏掉是常态；整批丢弃意味着
29 条好的陪 1 条坏的殉葬，而重试一次的代价是**再付一次全额费用** ——
成本治理刚做完，不该在校验层破功。

**2. "修正"与"拒绝"必须分开计数。** 被拒的是"这条不能用"，
被修正的是"这条能用，但模型写的枚举值不认识"。混成一个数字就看不出
**"模型开始在枚举上胡说"**这个前兆 —— 而它正是提示词需要调整的信号
（配合 4.6 的 `prompt_version`，可以按版本量化"这一版提示词的输出规范度"）。

**3. 判分与关系是"宁可丢弃"的例外。** 卡片类型猜错了只是分类不准，
判分猜错了会让"答错"的题被排到很久之后，关系猜错了会凭空造出一条边 ——
这两处的枚举**不允许修正**，认不出来就整条丢弃并记录。

### AN.4 分层：校验回答"形状"，质量门回答"内容"

卡片入库现在是两道关，顺序有意义：

    validate_items(...)  →  形状/枚举（缺 title、title 是数字、card_type 不认识）
    reject_reason(...)   →  内容够不够格（正文 <10 字、标题 <2 字、无 source_text）

于是"空正文"现在报 `缺少必填文本字段: content`，而不是以前的"正文过短"——
前者把排查方向指向"模型漏字段"，后者指向"内容太短"，而真相通常是前者。

### AN.5 验收

| 项 | 结果 |
|---|---|
| `tests/test_structured_output.py`（新增 25 例） | 通过/修正/拒绝三分；**一条坏的不殉葬整批**；两类处置分开计数；非 list / 非 dict 输入不崩；`options` 字符串规范化；多余字段忽略；`coercion_notes` 不写库；判分与关系的"宁可丢弃"；**五条接线静态核对**（含"旧的手写判定必须消失"） |
| `tests/test_card_intake.py` | 拒绝原因分层后更新断言：**空字段**由校验拒（`content`/`title` 缺失）、**过短字段**由质量门拒，两类都必须在结果里可见 |
| 全量后端测试 | 758 → **782 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |
| 提示词 | **未改动**（`test_prompt_golden` 11 个摘要保持不变，缓存键不受影响） |
| 真库 | 无 schema 变更；校验只影响新写入的数据 |

### AN.6 仍未完成

- **校验结果没有持久化**：`issues` 只进日志。要看"某版提示词的 coerced 比例"
  得去翻日志而不是查库 —— 与 4.6 的版本溯源是同一类"管道通了但没人读"。
- **`infer_card_relations` 的 `reason` 字段没有约束**（可空、无长度上限），
  只要求两个 ID 与关系类型合法。
- **ASR 输出仍不在校验范围内**（`services/asr/` 用另一套 OpenAI 客户端，
  且输出是纯文本）—— 见附录 AI.6。

---

## 附录 AO · 阶段 5.11：任务进度 UI（2026-09-11）

### AO.1 一个"后端早就准备好了、前端从没接过"的典型

阶段 1′ 就给 Celery 任务加了完整的可观测契约：

    task_runs 表        status / progress / stage / message / attempt / heartbeat_at
    GET  /api/tasks/note/{note_id}     列出某笔记近期任务（最新在前）
    POST /api/tasks/{task_id}/cancel   请求取消（幂等；如实返回 terminated）

任务侧也逐阶段上报可读阶段名（"正在抽取知识点"），注释里写明"可直接展示"。
但**前端一次都没消费过**：全仓 grep `/api/tasks` 命中 0 处，
用户在转换/清洗/理解期间看到的始终是一句静态文案或一个不确定进度的动画条。

于是一整套可观测性只活在日志和数据库里 —— 这不是"实现成本高"，
而是"最后一公里没接"。5.11 做的就是接上这一公里。

### AO.2 新增两个文件

| 文件 | 内容 |
|---|---|
| `frontend/src/api/tasks.ts` | `listNoteTasks` / `getTask` / `cancelTask` + `TaskRun` 类型 + `isTerminal`。**单独成文件**而不是继续往 `client.ts` 里堆 —— 那正是 5.1/5.3 要拆掉的东西，新增接口不该继续往里堆。⚠️ **2026-09-14 修正**：原文写"330 行、90 个函数的 `client.ts`"，两个数字都不成立 —— 手写 API 函数实测是 **111** 个（**从来不是 90**），而 `client.ts` 现在 **490 行、只剩 1 个端点函数**（其余 110 个早已按域拆走）。这句话的**理由仍然成立**（新增接口按域独立成文件是对的），但它描述的是一个**已经拆完**的状态，见附录 BH |
| `frontend/src/components/TaskProgress.tsx` | 真实进度条 + 阶段名 + 尝试次数 + 取消按钮；接口不可用时退回静态文案 |

### AO.3 三条刻意的行为（也是测试盯的地方）

1. **进度是装饰，不是正确性。** 接口失败时**不报错、不阻塞**，退回原来的静态
   文案。让"看不到进度"升级成"页面打不开"是本末倒置 —— 页面的主数据由别的
   接口提供，进度只是锦上添花。
2. **终态即停止轮询。** 任务成功/失败/取消/僵尸后不再请求，否则一个开着的
   页面会永远每 2 秒打一次接口（正是 5.2 要替换掉的那类手写轮询；在新方案
   落地之前，至少不要制造新的无限轮询）。
3. **取消成功 ≠ 已停止。** 文件系统 broker 无法强杀执行中的任务，后端如实
   返回 `terminated=false` 并附说明，界面也必须如实说"已请求取消：任务会在
   下一个阶段边界退出"。显示成"已取消"会让用户以为正在跑的那一步也停了 ——
   而它没有。

### AO.4 接在哪两处（以及为什么一处**不**给取消按钮）

| 位置 | 处理 |
|---|---|
| 笔记详情（`uploading` / `converting`） | 静态文案 → `TaskProgress`（含取消按钮）。此前这里没有任何停止入口 |
| 清洗面板（`cleaning`） | 不确定进度的动画条 + 静态文案 → `TaskProgress`，**`allowCancel={false}`** |

第二处的取舍值得写下来：清洗面板已经有"**停止清洗**"按钮，而它与
"取消 Celery 任务"**语义不同** —— 前者是把笔记标记为 `cleaning_failed`
（产品级动作，走 `POST /api/cleaning/{id}/stop`），后者是让任务自己退出。
两者都能"让它停下来"，但产生的状态不同。把两个外观一样、效果不同的按钮
并排放，用户无法判断该点哪个，因此这里只保留原有的产品级入口。

### AO.5 顺带核对：阶段 5 的真实进度（此前的表格是过时的）

本轮逐项查了代码，发现计划表里**没有状态列**、而其中几项其实早已完成：

| 项 | 核对结果 |
|---|---|
| 5.4 路由级懒加载 | ✅ 已做：`App.tsx` 18 个页面全部 `React.lazy`；构建产物 `graph-*.js` 186KB、`markdown-*.js` 393KB 是**独立 chunk**，入口仅 22KB |
| 5.7 真 404 | ✅ 已做：`NotFound` 组件 + `path="*"`（未登录时的 `*` 才回登录页） |
| 5.8 三态 + Toast | ✅ 已做：`EmptyState` / `ErrorDisplay` / `LoadingSpinner` / `Toast` / `ErrorBoundary`，全仓 147 处使用 |
| 5.10 响应式 | ⏸ 未做：`responsive.css` 实测 **51 行**（计划写 41 行，仍远不足以覆盖移动端） |
| 5.5 拆分巨型页面 | ✅ **`NoteDetail.tsx` 已完成**：1178 → **282 行**，拆出 24 个模块（最大 239 行），附录 AX。⚠️ `KnowledgeGraph.tsx`(980) 与 `Projects.tsx`(759) **仍未拆** —— 它们没有页面级测试，按 5.5 验证过的顺序须**先补安全网再拆** |

**这一节本身是这次核对最大的产出**：计划表把它写成"待办"的三项其实已经完成，
而"下一阶段该做什么"如果照抄表格，就会去做已经做完的事。

### AO.6 验收

| 项 | 结果 |
|---|---|
| `frontend/src/components/TaskProgress.test.tsx`（新增 11 例） | 真实进度与阶段名 / **接口失败退回静态文案** / 无任务记录退回文案 / **终态停止轮询** / 运行中持续轮询 / **`terminated=false` 时如实告知"下一个阶段边界退出"** / 终止成功显示已取消 / 取消失败提示可重试 / 终态无取消按钮 / 尝试次数 / `allowCancel=false` |
| 前端全量测试 | 50 → **61 passed**（5 个文件） |
| `tsc --noEmit` / `eslint src/` / `npm run build` | 全绿（构建 13.6s，产物 chunk 划分符合 5.4 的验收） |
| 后端 | **无改动**（本轮只消费既有接口）；后端测试保持 782 passed |
| 真库 | 无影响（只读 `task_runs`） |

### AO.7 仍未完成

- **没有行内端到端验证**：本轮只做到"tsc + eslint + vitest + build"，
  没有在真实浏览器里点一遍（Playwright 未装）。组件行为由 11 个单测覆盖，
  但"它出现在正确的位置、样式正确"这一层**未经运行时验证**。
- **理解任务（`learning` 状态）没有接**：笔记详情在该状态下没有明显的
  处理中区域（它的进度目前靠父组件的轮询刷新状态），要接得先决定
  放在哪里 —— 这属于 UI 布局决策，留给 5.5（拆分页面）时一起做。
- **上传（`uploading`）阶段通常没有 task_runs 记录**（上传发生在 API 进程里、
  不是 Celery 任务），因此该状态会一直显示兜底文案。这是**如实**的：
  真的没有任务可查，而不是组件没接上。

---

## 附录 AP · 阶段 5.5 的前置：给 `NoteDetail.tsx` 补上安全网（2026-09-11）

### AP.1 为什么这一轮没有直接开始拆

5.5 要求把 `NoteDetail.tsx`（实测 **1184 行**，计划里写的 1030 行已经过时）
拆到 300 行以下。而这个文件此前**一行测试都没有**：

    全仓页面级测试：CardReview.test.tsx / quiz 组件 / TaskProgress
    NoteDetail.tsx       → 无

直接拆等于盲改：少传一个 prop、把"已清洗笔记自动切到清洗版"的条件写反、
"删除先确认"被改成直接调接口 —— 这些都不会有任何东西告诉我，而它们的后果
分别是"用户以为清洗结果丢了""笔记被误删"。因此本轮先做前置条件。

### AP.2 新增 `NoteDetail.test.tsx`（10 例，行为级）

固定的都是**拆坏了后果最严重**的行为，且刻意只断言"点哪个 → 调哪个接口"，
不锁 DOM 结构（这样重构可以自由改结构而不必改测试）：

| 测试 | 拆坏了会怎样 |
|---|---|
| 已清洗笔记默认显示清洗版 | 用户以为清洗结果丢了 |
| 编辑保存走 `updateNoteContent`（且 content 正确） | 保存到错误版本、覆盖清洗副本 |
| 取消编辑不调保存接口 | 反悔也留下痕迹 |
| **删除必须先弹确认、不直接调接口** | 误点一次丢笔记 |
| **archived 重新学习：先探测影响（`confirm=false`）→ 用户确认后才 `confirm=true`** | 清空全部旧产物且不可撤销 |
| 二次确认点"取消"时不发第二次请求 | 同上，且"确认框"形同虚设 |
| 加载失败显示错误与重试入口 | 白屏 |
| 转换中显示进度组件（阶段 5.11 的成果） | 被拆回静态文案 |

### AP.3 写测试时顺带发现的真问题：一个字段就能白屏

第一版测试里，我把 `getNoteLinks` 的 mock 写成
`{ linked_materials: [], dangling_links: [] }`（少了一个 `linked_personal_notes`），
结果**整页崩掉**：

    TypeError: Cannot read properties of undefined (reading 'length')
    ❯ NoteDetail src/pages/NoteDetail.tsx:922
       {noteLinks && noteLinks.linked_personal_notes.length > 0 && (

渲染期抛异常 → 整棵树卸载 → 白屏。这正是阶段 5.8 想消灭的"白屏卡住"，
只不过它的来源不是"忘了写三态"，而是**一个字段的契约漂移**。

已修（4 处）：`linked_materials` / `linked_personal_notes` 的读取全部加
`?.` + `?? []`，并补一条回归测试 —— 让"后端少返回一个数组字段"的结果
退化成"这一块不显示"，而不是"什么都看不到"。

⚠️ 这一处的取舍值得写下来：类型上这两个字段是**必填**的，所以严格说
组件没写错。但"类型保证"只在本仓库的编译期成立，运行期面对的是真实响应
（版本不一致、代理缓存、旧部署）。**渲染期的 undefined 访问代价是整页白屏，
防御成本的代价是三个字符** —— 这个交换在任何时候都划算。

### AP.4 5.5 的下一步（已勘明，供后续执行）

已把拆分点勘明（按 JSX 区块注释实测）：

| 区块 | 行范围（约） | 提取目标 |
|---|---|---|
| 头部 + 元信息 + 错误 + 视图切换 | 741–878 | `NoteHeader`（纯展示，props 进 props 出） |
| 关联资料 / 悬挂链接 / 被引用笔记 | 883–938 | `NoteLinksPanel` |
| 内容区（原始 / 清洗 / 对比 / 编辑） | 948–1020 | `NoteContentView`（最大块） |
| 批注浮层 + AI 提问浮层 | 1039–1077 | `SelectionOverlays` |
| 链接管理弹窗 | 1077–1124 | `LinkManagerDialog` |
| 选区/批注状态与处理函数 | 99–119 + 485–671 | `useTextSelection` / `useAnnotations` |

⚠️ 还需要**把状态搬进 hook**（12 个 `useEffect`、30 个 state），只搬 JSX
不足以降到 300 行。因此 5.5 是**多轮工作**，本轮交付的是它的安全网。

### AP.5 验收

| 项 | 结果 |
|---|---|
| `tests/pages/NoteDetail.test.tsx`（新增 10 例） | 全部通过（见 AP.2 的行为清单） |
| 前端全量测试 | 61 → **71 passed**（6 个文件） |
| `tsc --noEmit` / `eslint src/` / `npm run build` | 全绿（构建 6.07s，`NoteDetail` chunk 47.35 kB） |
| 后端 | 无改动（782 passed 保持） |
| 修复 | 关联资料的字段访问防御（4 处）+ 回归测试 |

### AP.6 仍未完成

- **5.5 主体未做**：`NoteDetail.tsx` 仍 1184 行。本轮只交付前置条件与拆分方案。
- **`KnowledgeGraph.tsx`（980 行）/ `Projects.tsx`（759 行）没有测试**：
  按同样的道理，拆它们之前也要先补表征测试。`KnowledgeGraph` 依赖 force-graph
  （canvas/SVG 渲染），在 jsdom 里能测的行为比 `NoteDetail` 更少 ——
  它可能更适合放在有真实浏览器的场景里验证（即 5.13 的 Playwright）。

---

## 附录 AQ · 阶段 6.8：错误响应不泄露内部信息 + 启动安全姿态（2026-09-11）

### AQ.1 全局出口其实早就合格，漏的是**路由自己拼的 detail**

`ErrorHandlerMiddleware` 的设计是对的：未知异常一律 500 + "服务器内部错误"，
完整堆栈只进日志，响应体带 `request_id` 供对账。所以问题不在出口，
而在**路由把底层异常文本塞进 `detail`** —— 那种泄露绕过了整个出口设计。

全仓筛查 `detail=f"...{e}..."` 共 20 余处，其中**只有 2 处是真泄露**：

| 位置 | 原来的写法 | 为什么是泄露 |
|---|---|---|
| `upload.py`（PDF 页数） | `detail=f"PDF 解析失败: {e}"` | 解析库的 ValueError 形如 `cannot open D:\...\x.pdf: invalid header` |
| `upload.py`（PDF 裁剪） | `detail=f"裁剪失败: {e}"` | 同上 |

其余 18 处都是**用户可见的业务说明**（"当前状态为 X，只有 Y 才能触发清洗"、
"文件大小超过限制 (500MB)"、"不支持的文件格式: .xyz"），
它们是产品的一部分，不该改。

已改：用户看到"PDF 解析失败：文件可能已损坏或加密，请确认后重试"，
详情（含路径）进 `logger.warning`，客户端凭响应体里的 `request_id` 与服务端对账。

### AQ.2 "不泄露"必须用真实代码路径验证

写这两个测试时踩到一个**会让测试静默空转**的坑，值得记下来：

    最初想用 `monkeypatch.setattr(auth_mod, "get_current_user_dependency", boom)`
    制造一个未知异常 —— 但 FastAPI 在**注册路由时**就把依赖对象捕获了，
    patch 模块属性不会改变已注册的依赖。结果是：请求照常返回 401，
    而断言 `status_code in (500,401,403)` 依然通过 —— 测试永远是绿的，
    却什么也没验证。

改成用**真实的 `ErrorHandlerMiddleware` 包一个会抛异常的路由**，走完整 HTTP
往返；上传那处则真的调用 `prepare_upload`（真文件、真魔数校验），
只把底层 `get_pdf_page_count` 换成会抛异常的桩。

### AQ.3 "生产 `debug=False` 断言"改成了**告警**（一处对计划的偏离）

计划原文要"生产 `debug=False` 断言"。实现时发现它没法成立：
`APP_ENV` 是**用户自己填的**，没有一个可信的"我是生产"信号可以拿来断言 ——
在生产填 `APP_ENV=dev` 的机器上，断言只会看到"dev"，然后欣然放行。

因此改为**可观测 + 显式告警**，启动时打印一行生效姿态：

    安全姿态 | app_env=prod | LLM=deepseek | SQL 日志=关 | traceback 回吐=关 | JWT 密钥=已配置

外加两条告警：`is_dev` 为真时（"异常会回吐 traceback，JWT 密钥缺失时会自动生成"）、
`LOG_SQL=true` 时（"日志包含 bcrypt 哈希与卡片正文"）。

理由：这三种误配都是**静默降级**（不报错、只是安全性变差），
而"配错了"能在启动那一刻从日志里看见，比一个能被绕过的断言有用。
硬编码成拒绝启动则会让本地开发与演示环境跑不起来 —— 而本项目正是本地跑为主。

### AQ.4 验收

| 项 | 结果 |
|---|---|
| `tests/test_error_leakage.py`（新增 7 例） | 解析失败不泄露路径 / 真实中间件下 500 不含 traceback·SQL·路径 / 错误体带 request_id / 姿态日志字段齐全 / dev 告警 / `LOG_SQL` 告警 / 应用仍可导入 |
| 全量后端测试 | 782 → **790 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |
| 真库 | 无 schema 变更、无数据改动 |

### AQ.5 仍未完成（阶段 6 其余项）

6.1 密码策略、6.2 恒定时间登录与注册不泄露存在性、6.3 refresh token 轮换、
6.4 上传 magic bytes 与 zip 炸弹防护、6.5 全端点用户级限流、
6.6 备份恢复演练、6.7 依赖漏洞扫描。

⚠️ 其中 **6.1 / 6.2 会改变用户可见行为**（拒绝弱密码、注册失败不再说明原因），
**6.7 需要网络与新工具**（`pip-audit` / `npm audit` / Trivy）——
按既定约定，这两类**先确认再做**。6.4 / 6.5 不需要新依赖，可继续自主推进。

---

## 附录 AR · 阶段 6.4：上传安全护栏（2026-09-11）

### AR.1 先核对：计划里的四条，只有一条真的缺

| 计划条目 | 核对结果 |
|---|---|
| 上传按 magic bytes 验类型 | ✅ **早已存在**（阶段 0 的 `_MAGIC_RULES` + `_validate_magic`，含 `.md` 的脚本注入嗅探）|
| PDF 页数上限 | ❌ 缺失：`page_count` 只被返回给前端裁剪用，没有上限 |
| zip 炸弹防护 | ❌ 缺失：`.docx/.pptx/.xlsx` 只检查了前 4 字节是 `PK\x03\x04` |
| 单用户文件数上限 | ❌ 缺失：只有容量配额（MB）|

**又一条"照抄表格就会重复劳动"的例子**（与 AO.5 同类）：动手前先核对现状，
比按表格逐条实现省一半时间。

### AR.2 新增 `services/upload_safety.py`（纯函数）

三件事都做成"输入统计量、输出拒绝理由或 None"的纯函数 —— 不依赖 HTTP、
数据库与真实上传流程，于是边界可以逐条验证，接线留在 `api/upload.py`：

| 威胁 | 判据 | 默认阈值 | 为什么是这个数 |
|---|---|---|---|
| **zip 炸弹** | 解压后总体积 / 压缩比 / 条目数 | 500MB / 100:1 / 5000 | 正常 Office 文档解压后远小于 500MB、压缩比多在 10:1 内；`42KB → 4GB` 那种构造必然越过 100:1 |
| **超大 PDF** | 页数 | 2000 | 页数直接决定 MinerU 与 LLM 的工作量；教科书通常 <1000 页，而 3000 页的书会把管道拖成不可控的耗时与成本 |
| **无限上传** | 单用户笔记数 | 5000 | 容量配额挡不住"传一万个小文件"：每个都要建笔记、进队列、占 inode。当前真库 22 篇，5000 是护栏不是限制 |

三条都可以用 `0` 单独关闭（便于只关掉某一条而不动其它）。

### AR.3 检查自身不能成为攻击面

zip 炸弹检查**只读中央目录**（`ZipInfo.file_size`），**绝不解压** ——
否则"检查"就成了"帮攻击者解压"。为此专门写了一条测试：
monkeypatch `ZipFile.read` / `ZipFile.open`，断言检查过程中两者都没被调用。

另外，畸形 ZIP 会让 `zipfile` 自己抛 `BadZipFile`：这里**拒绝**而不是把异常
抛给调用方（"压缩包结构损坏"），因为一个连目录都读不出来的 Office 文档，
转换阶段同样会失败 —— 早拒比晚拒省一次完整解析。

### AR.4 默认值为什么给得宽（一处刻意的取舍）

三条阈值都远高于真实使用量级。理由：它们是**护栏**，不是产品限制。

把 PDF 页数卡到 200 页、笔记数卡到 100 篇，会立刻挡住合法用户，
而真正的攻击形态（炸弹、海量小文件）在 2000 页 / 5000 篇这个量级
一样被挡住 —— 阈值的作用是"让异常的数字停下来"，不是"规训正常使用"。

对照 4.4 的限流默认值（那里我选择了**默认关闭**）：区别在于
**误伤的代价不同**。限流误伤是"平时速度被砍半"（每个人每次都能感觉到），
而这里误伤需要用户真的有 2000 页 PDF 或 5000 篇笔记（几乎不可能），
所以这里可以把护栏默认打开。

### AR.5 验收

| 项 | 结果 |
|---|---|
| `tests/test_upload_safety.py`（新增 21 例） | 正常文档不误伤 / 高压缩比拒绝 / 体积超限拒绝 / 条目数超限拒绝 / **检查过程不解压**（monkeypatched 断言）/ 畸形 zip 拒绝而非抛异常 / `0` 关闭单条 / PDF 页数边界（超限·等于·未知·不限）/ 笔记数边界 / **三条接线静态核对** + 配置默认值存在 / **端到端：真实路由拒绝炸弹型 docx 且清掉临时文件** |
| 全量后端测试 | 790 → **811 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |
| 真库 | 无 schema 变更；新护栏只影响上传入口 |

### AR.6 仍未完成（阶段 6 剩余）

6.1 密码策略、6.2 恒定时间登录与注册不泄露存在性、6.3 refresh token 轮换、
6.5 全端点用户级限流、6.6 备份恢复演练、6.7 依赖漏洞扫描。

⚠️ 按既定约定：**6.1 / 6.2 改用户可见行为**（拒绝弱密码、注册失败不再说明原因）、
**6.7 需要网络与新工具** —— 这两类先确认。**6.5 与 6.6 不需要新依赖**，
可继续自主推进（6.6 的 `pg_dump` 要改成 SQLite 的 `VACUUM INTO`，
本项目已有 `backup_service`，剩下的是"恢复演练"这一步）。

---

## 附录 AS · 阶段 6.6：恢复演练（2026-09-11）

### AS.1 缺的不是备份，是"证明备份能用"

先核对现状：备份侧**早就完备**了 —— `VACUUM INTO` 原子快照、落盘即校验
（`integrity_check` + 关键表行数写进 `_verify.json`）、保留策略、
Beat 每日 03:30、以及一个考虑周全的 `scripts/restore_db.py`
（恢复前先校验快照、先给当前库存回退快照、先删 WAL 兄弟文件）。

缺的是**最后一步**：没有任何东西定期证明"最近这份快照真的能恢复"。
每天看到"备份完成 integrity=ok"很容易让人以为有备份，而那只证明
**刚写下的那一刻**它是好的。

计划里的 `pg_dump` 不适用（本项目是 SQLite），改用等价能力：
`inspect_snapshot` + `run_restore_drill` + 每周一 05:00 的 Beat 任务。

### AS.2 三条设计决定

**1. 演练 = 真的恢复一次（到临时副本），而不是只读检查。**
把快照复制到临时文件、在那里打开（可写）、查完删掉。这样同时满足两件事：
快照本身保持只读（它是唯一的救命备份），而 FTS5 的 `integrity-check`
这类**需要写权限**的检查也能做。副本上"复制 + 打开 + 查询"本身就是
恢复的最小可验证形态。

**2. 与线上库的行数差异是信息，不是失败。**
快照是过去某一刻的状态，之后的新数据不会在里面。把差异当失败，
演练会天天红 —— 那是演练最常见的死法。因此失败条件只有客观损坏
（文件不可读 / 结构性损坏 / 关键表缺失 / 实际查询失败）。

**3. 外键违规记为 warning，不是 failure。**
本项目**有意保留悬挂引用**（知识链接的"悬挂链接占位"策略；
`database.py::_rebuild_dangling_tables` 也是围绕它设计的）。
真库实测就有 1 处：`note_material_links` 指向一个已删除的用户。
把它当失败，演练会永远红着。

### AS.3 ⚠️ 一次**自己造出来的假警报**（本轮最有价值的教训）

第一版演练用了 `fts5vocab` 统计"索引里真正可检索的文档数"，与
`chunks.grams` 非空行数比对。在真实备份上它立刻报出：

    FTS 索引与 chunks.grams 不一致（可检索 171 行，应可检索 608 行）
    —— 检索会静默少结果

看起来是"437 个块检索不到"的重大生产缺陷。**但它是错的。** 本轮用四步把它推翻：

| 步骤 | 结果 |
|---|---|
| 在**一致副本**（VACUUM INTO）上新建一张同样的 FTS 表、灌入同样数据 | 也是 **171** —— 说明不是索引陈旧或损坏 |
| 用 `MATCH` 查 `chunk_rowid=600` 的词（`柴油`） | **命中 116 条** —— 它"不在索引里"却能查到 |
| `chunk_rowid=300/552/600` | `vocab` 说"无"，`MATCH` **全都能命中** |
| `chunk_rowid=608`（grams 全是 `##`） | 0 条命中 —— 这一条是真的没有可索引词元 |

结论：**`fts5vocab` 的 `doc` 不是一份完整普查**，据此外推必然得到假警报。
而假警报比没有检查更糟：它会让演练永远红着，然后被所有人忽略
（与 AS.2 第 3 条同一个道理）。

已改为只做**站得住**的那一条：FTS5 的 `integrity-check`（结构完整性）。
它发现不了"索引落后于内容表"，但**也不会误报** —— 而"索引落后"的真实判据
是**检索结果**（`scripts/eval_retrieval.py` 那类评估），不是元数据统计。

顺带记下另两条同样不可行的判据（都实测过）：
`SELECT COUNT(*) FROM chunks_fts`（外部内容表会直接读内容表，永远相等）、
`VALUES('integrity-check')` 对外部内容表**不比对内容表**（索引少行也不报错）。

### AS.4 验收

| 项 | 结果 |
|---|---|
| `tests/test_restore_drill.py`（新增 16 例） | 健康快照通过 / **行数差异不算失败** / 截断快照失败且给出原因 / 缺关键表失败 / 孤儿复习进度失败 / **索引落后不得误报** / 无 FTS 表时跳过 / **演练不改动快照与线上库**（SHA-256 前后一致）/ 自动选最新快照 / **外键违规只记 warning** / 挂在 Beat 上 / 任务已注册 / **真实备份流程端到端**（建快照 → 演练 → 行数差 0） |
| 真实备份演练 | **ok=True**，`problems=[]`，1 条 warning（已知悬挂外键），`deltas` 全 0 |
| 全量后端测试 | 811 → **827 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |
| 真库 | **未被修改**（演练只读线上库、只在临时副本上操作；已删临时文件） |

### AS.5 仍未完成（阶段 6 剩余）

6.1 密码策略、6.2 恒定时间登录与注册不泄露存在性、6.3 refresh token 轮换、
6.5 全端点用户级限流、6.7 依赖漏洞扫描。

⚠️ 按既定约定：6.1 / 6.2 改用户可见行为、6.7 需网络与新工具 —— **先确认**。
**6.5 不需要新依赖**，可继续自主推进。

另外：**演练只覆盖数据库**。对象存储（`data/storage/` 里的原始文件与
Markdown）**没有快照、也没有演练** —— 数据库里的路径指向它们，库恢复了而
文件没了同样等于丢数据。这属于计划里"对象存储快照"那一半，本轮未做。

---

## 附录 AT · 阶段 6.1 / 6.2：密码策略与登录时序（2026-09-11）

### AT.1 先核对：6.2 其实早就做完了，只是没有测试

`auth_service` 里两件事都已具备：

- `_DUMMY_HASH`：邮箱不存在时也跑一次 bcrypt，让两条路径耗时接近；
- 注册冲突统一文案（`§2.5 E-2` 时期修的），不区分"邮箱被占"还是"用户名被占"。

但**只有注释守护，没有任何测试**。一条只靠注释存在的安全属性，
下次重构就会被顺手删掉 —— 而且删掉之后所有测试仍然全绿。
本轮补了三条：未知邮箱必须跑一次 bcrypt、两种冲突的文案必须**完全相同**、
假哈希的 cost 必须与真实哈希一致（否则时序对齐失效）。

### AT.2 6.1：7 条判据，以及**刻意不做**的两件事

| 规则 | 理由 |
|---|---|
| 长度 ≥ 8 | 6 位是当代硬件可离线爆破的量级 |
| UTF-8 字节 ≤ 72 | bcrypt **静默截断**前 72 字节（中文 3 字节/字，第 25 字之后不参与校验）。与其让用户以为设了长密码，不如拒绝并说明 |
| 不能全是数字 / 不能是同一字符重复 | 撞库字典第一梯队 |
| 不在常见弱口令表里（40 条，经 NFKC 归一化后比对） | 挡住"最省事的那几个选择"，全角数字也拦得住 |
| 不能包含用户名，或完整邮箱地址 | 社工猜测的第一顺位 |
| ~~必须含大小写+数字+符号~~ | **不做**：强制组合把人推向 `Password1!` —— 而它出现在每一本字典里（NIST SP 800-63B 亦不再推荐） |
| ~~追溯既有短密码~~ | **不做**：追溯等于在用户毫无准备时锁死账号，而这些人往往正是最需要先登进来改密码的人 |

### AT.3 ⚠️ 一次自己的规则误伤了自己（实测记录）

第一版里"不能包含**邮箱 @ 之前的部分**"这条把
`contract@example.com` + `ContractPass123!` 判成了弱密码 —— 它连项目的
既有测试都过不了（`test_auth_contract` 就是这么被我弄红的）。

反思后收紧为"只匹配**完整邮箱地址**"：局部字符串重合并不等于可被社工猜中，
而误拒的代价是把用户赶去挑 `xxx123!` 这类**更弱、更好猜**的密码。
这条取舍连同实测经过写进了 `password_policy` 的注释 —— 免得以后有人
"顺手加强"回去。

### AT.4 bcrypt cost 改为显式配置

`bcrypt_rounds = 12`（≈200ms）。为什么不用库默认值：默认值会随版本变化，
而 cost 直接决定离线爆破成本，应当是**部署方的显式选择**。
`_DUMMY_HASH` 与 `hash_password` 读同一个值 —— 否则时序对齐失效。

提升 cost **不会让旧哈希失效**：bcrypt 把 cost 写在哈希串里（`$2b$12$…`），
校验按各自记录的值计算，存量用户在新设密码时自动升级。

### AT.5 验收

| 项 | 结果 |
|---|---|
| `tests/test_password_policy.py`（新增 17 例） | 强密码通过 / 短密码拒 / **超 72 字节拒**（含 72 字节边界）/ 纯数字拒 / 单字符重复拒 / 弱口令拒（含全角）/ 含用户名拒 / **与邮箱同词不误拒** / 空白拒 / 哈希 cost 来自配置 / 假哈希同 cost / **未知邮箱必跑 bcrypt** / 错密码跑 bcrypt / 接口层 422 带理由 / 强密码 201 / **存量短密码仍可登录** / **两种冲突文案一致** |
| 全量后端测试 | 827 → **846 passed / 3 skipped** |
| 既有测试的连带修改 | `test_week1_2_fixes`（6 处 fixture 短密码换成强密码）、`test_auth_contract`（1 处密码含用户名，被策略正确拒掉 → 换密码）。两者的被测性质都未改变 |
| ruff | `app tests scripts` 全绿 |
| 真库 | 无 schema 变更；**存量用户密码不受影响**（策略只在注册/改密时生效） |

### AT.6 仍未完成（阶段 6 剩余）

6.3 refresh token 轮换与 `jti` 黑名单（大改动）、6.5 全端点用户级限流、
6.7 依赖漏洞扫描（需网络与新工具）。

---

## 附录 AU · 阶段 6.5：限流覆盖（2026-09-11）

### AU.1 限流其实早就有，但**两条规则从未生效**

`RateLimitMiddleware` 已经具备正确的关键设计：优先按**已认证用户**计数、
未认证退回 IP（同 NAT 的不同用户不互相拖累）、滑动窗口、429 带 `Retry-After`
与统一错误信封。

问题在匹配方式：`path.endswith(suffix)`，而规则表里写的是
`/understanding/start` 与 `/cleaning/start`。真实路由是：

    POST /api/understanding/{note_id}/start
    POST /api/cleaning/{note_id}/start

—— **都不以 `/understanding/start` 结尾**。这两条规则从未命中过：
限流表看起来覆盖了"理解"与"清洗"，实际它们完全不限流。
这类缺陷不报错、不进日志，只有真去数请求次数才会发现。

### AU.2 三处修改

1. **后缀匹配 → 正则匹配**（`_RULES` 变成 `(正则, 上限, 规则名)`，编译一次复用）。
   带参数的路径终于能被明确表达。
2. **补上 4 条被漏掉的昂贵端点**：
   `/api/notes/{note_id}/ask/stream`（笔记级流式问答 —— **另一条** LLM 路径）、
   `/api/knowledge/cards/{card_id}/generate-questions`（卡片出题）、
   `/api/upload/{note_id}/retry`（重跑转换）、`/api/projects/{project_id}/scan`（扫描导入）。
   另外复习提交（`/api/review/*/submit`）可能触发**语义判分**（每题一次 LLM），
   也纳入 LLM 同级限量。
3. 删掉一条**死规则**：`/api/notes/{id}/retry` 在应用里不存在（真实路径在 `/api/upload/` 下）。

### AU.3 把"静默失效"变成红灯（本轮最有价值的部分）

新增 `tests/test_rate_limit_coverage.py`，核心两条**反向**断言：

- **每条规则都必须命中至少一个真实注册的路由** —— 防"死规则"（写错了却留在表里，
  给人"已经限流了"的错觉）；
- **一份手工维护的"昂贵端点清单"必须全部被覆盖** —— 防"漏网"，且清单里的路径
  若在应用里不存在会直接失败（清单过期也是缺陷，不是可以忽略的差异）。

⚠️ 取路由的方法本身也是坑：本项目把子路由挂在自定义的 `_IncludedRouter` 上，
`app.routes` 只有 6 项、**一条 POST 都没有**（实测）。因此改用
`app.openapi()["paths"]`（99 条路径 / 41 条 POST），并额外加了一条
`test_route_enumeration_works`：枚举不到 30 条 POST 就失败 ——
否则覆盖检查会**静默空转**，每条规则都"没有反例"，永远通过。

### AU.4 验收

| 项 | 结果 |
|---|---|
| `tests/test_rate_limit_coverage.py`（新增 15 例） | 带参数路径被限流 / 静态路径仍匹配 / GET 不限流 / 无关路径不限流 / **无死规则** / **昂贵端点全覆盖** / **枚举非空转** / 键优先用户、失败退回 IP / 窗口边界与滑动 / 键隔离 / **429 + Retry-After + 错误信封** / 未匹配路径直通 |
| 全量后端测试 | 846 → **861 passed / 3 skipped** |
| 连带修改 | `tests/test_rate_limit.py` 3 处解包 `_RULES` 的地方适配新形状（被测性质未变） |
| ruff | `app tests scripts` 全绿 |

### AU.5 仍未完成（阶段 6 剩余）

6.3 refresh token 轮换 + `jti` 黑名单（大改动：需要新表/新端点 + 前端改造）、
6.7 依赖漏洞扫描（需网络与新工具，**需先确认**）。

另外两条已知边界（有意，非遗漏）：
- **限流是进程内的**：`uvicorn --workers N` 已被 `_enforce_single_writer` 拒绝，
  所以当前部署形态下没有多进程计数问题；真要横向扩展时需换成共享存储
  （与 4.4 的限流同一条取舍）；
- **只限 POST**：读接口靠分页与单写者兜住，纳入限流会让正常翻页撞 429。

---

## 附录 AV · 阶段 6.6 收尾：存储审计接上调度（2026-09-11）

### AV.1 第三次遇到同一个模式："工具写好了，没人调用"

`audit_vault`（含缺失文件 / 孤儿文件 / 大小 / 哈希比对）**早就实现**了，
`tests/test_vault_audit.py` 也有 14 条测试 —— 但全仓只有
`scripts/verify_vault.py` 手工调用过，**没有任何调度**。
于是它在运行期等于不存在：真出问题时不会有人知道，除非碰巧有人记得跑脚本。

本轮已是第三次遇到这一模式：

| 位置 | 工具 | 缺的那一步 |
|---|---|---|
| AF.10 | `purge_expired` / `purge_old_calls` | 没有定时任务调用 → 两张表无界增长 |
| AU.1 | 限流规则表 | 规则写错了 → 两条规则从未命中 |
| AV.1 | `audit_vault` | 没有调度 → 只被手工脚本调用过 |

共同点：**不报错、不告警，只是静默地不生效。** 因此这类"接线"本身值得
当成一个条目来做，而不是假设"函数存在就等于能力存在"。

### AV.2 与恢复演练的分工（互补，不是重复）

    数据实际分布在两处：
      data/db/engramnote.db   元数据（笔记、卡片、题目、复习进度）
      data/storage/…          原始文件与 Markdown（库里的路径指向它们）

| 任务 | 回答的问题 | 周期 |
|---|---|---|
| `restore_drill` | **备份**可用吗（快照能否恢复、结构是否完好） | 每周一 05:00 |
| `vault_audit` | **线上**库与磁盘一致吗（有没有"界面能看到、但打不开"的内容） | 每周一 05:30 |

只恢复数据库、丢了文件，用户看到的是"笔记都在，但每一篇都打不开" ——
与丢了数据没有区别。因此两个任务都在调度里，而不是只留一个。

审计用 `deep=False`（不做逐文件哈希）：哈希深比要读全部文件，周频任务里是浪费，
而"文件被人手工删了"比"磁盘静默损坏"常见得多；需要深比时用
`scripts/verify_vault.py --deep` 手工跑。

### AV.3 ⚠️ 真库实测：审计**发现了 4 处悬空引用**（2 篇笔记）

    扫描 22 篇笔记 / 81 个 DB 对象 / 77 个磁盘对象
    问题分布: {'missing_file': 4}

明细（只读查证）：

| 笔记 | user | title | 路径 | 创建时间 |
|---|---|---|---|---|
| `n1` | `u1` | `"n1"` | `/u1/n1.pdf`、`/u1/n1.md` | 2026-08-15 12:17:33 |
| `n2` | `u2` | `"n2"` | `/u2/n2.pdf`、`/u2/n2.md` | 2026-08-15 12:17:33 |

这两篇是**测试夹具写进生产库**留下的**历史残渣**：`user_id='u1'`、
`title='n1'`、`file_size=1`、路径是测试里的字面量 —— 与 `tests/test_fixes.py`
的构造完全同形。它们各挂着 1 张知识卡片、0 道题，在界面上是两篇打不开的笔记。

**未清理**：删除它们属于删除既有数据，按既定约定**先确认**。
在清理之前，每周审计会**一直报 `ok: False`** —— 这是如实报告，
但它也意味着这条告警在清理前不具备"新问题"的信号价值（值得知道）。

### AV.4 验收

| 项 | 结果 |
|---|---|
| `tests/test_vault_audit_schedule.py`（新增 7 例） | 挂在 Beat 上 / 任务已注册 / **与恢复演练分工都在调度里** / 任务真的调用 `audit_vault` / 空库通过（**隔离真实存储目录**） / **不一致是 error 级** / 审计内部报错不抛异常 |
| 全量后端测试 | 861 → **868 passed / 3 skipped** |
| ruff | `app tests scripts` 全绿 |
| 真库 | 审计**只读**运行，未修改任何数据 |

### AV.5 仍未完成

- **那 2 篇残渣笔记待确认清理**（见 AV.3）；
- 阶段 6 剩余：**6.3 refresh token 轮换 + `jti` 黑名单**（大改动：新表 + 新端点 +
  前端改造）、**6.7 依赖漏洞扫描**（需网络与新工具，先确认）；
- **存储没有快照**（只有审计）：文件被误删时，数据库能恢复到昨天，
  而文件不能 —— 这是"备份策略"层面剩余的最大缺口，属计划里
  "对象存储快照"那一半。→ **已在本轮补上，见附录 AW**。

---

## 附录 AW · 阶段 6.6 第三项：对象存储快照（2026-09-12）

### AW.1 补的是"数据库能恢复、文件不能"

数据实际分布在两处，而**只有一处有备份**：

| | 数据库 | 上传文件与派生 Markdown |
|---|---|---|
| 位置 | `data/db/engramnote.db` | `data/storage/<user>/<note>/…` |
| 现状（本轮前） | 每日 `VACUUM INTO` 原子快照 + 校验 + 保留 14 份 + 每周恢复演练 | **一份备份都没有** |
| 事故后果 | 可回到昨天 | **永久丢失** —— 界面显示"笔记都在"，但每一篇都打不开 |

附录 AV 的存储审计只能**发现**不一致，它不提供任何恢复能力：
审计跑完告诉你"有 4 个文件没了"，然后呢？没有第二份可以拿回来。
所以 6.6 的"对象存储快照"这一半必须补，且必须**真的复制文件**。

### AW.2 非原子，所以必须诚实标注

文件树做不到 `VACUUM INTO` 那样的原子副本：复制 82 MB 的过程中如果有上传，
那份快照就是**模糊**的（某一刻之后的文件不在里面）。设计上没有假装它是原子的：

- 逐文件**边复制边算 sha256**，写完后**读回目标文件再算一次**，两者不符
  重试一次，仍不符则**不进清单** —— 宁可不恢复，也不恢复一个撕裂的文件；
- 这类文件计入 `unstable`，IO 失败计入 `failed`，两者都进清单**并让任务返回
  `ok=False`**（error 级日志），不静默降级；
- 因此结论要这样读："**这份快照里每个文件都是完整的**；
  制作过程中新增的文件可能不在里面" —— 对"恢复被误删的旧文件"这个主场景，
  完整性就是全部所需。

### AW.3 三份快照的分工（互补，缺一不可）

    04:45  storage_snapshot   留下文件的第二份（备）
    05:00  restore_drill      数据库快照真的能恢复吗（验备份）
    05:30  vault_audit        线上库 ↔ 磁盘一致吗（查现状）

顺序有理由：先备文件、再验备份、最后核对现状。
周期取**每周**而不是每天：保留 3 份的前提下周频给出**三周**历史，
而"文件被误删、过几天才发现"正是要覆盖的场景（每份约 82 MB，
三份把 `_backup/` 占用压在 ~250 MB；数据库快照 14 份与之并存互不干扰）。

### AW.4 两条不可退让的安全性质

1. **保留策略只删自己的目录。** 识别标志是目录里有 `_storage_manifest.json`
   —— 数据库快照目录与早期手工备份没有这个文件，因此**永远不会**被碰到；
   进入删除路径前还会再确认一次身份标志，然后才 `rmtree`。
   （`_backup/` 里同时躺着救命备份，误删一份就是不可逆的损失。）
2. **恢复默认只补缺失文件、绝不删除。** `overwrite=False` 是缺省：
   恢复场景里更常见的是"某几个文件被误删"，而现存文件通常比快照新；
   目标目录里多出来的内容（用户后来上传的）**一个都不删**。
   每个补回的文件都按清单哈希校验，不符就不写。

### AW.5 测试与真库实测

| 项 | 结果 |
|---|---|
| `tests/test_storage_snapshot.py`（新增 **29** 例） | 清单记录真实 sha256 / 撕裂副本不进清单且 `ok=False` / 同秒重跑不覆盖已有快照 / `keep=0` 不是"删光"开关 / **保留策略不碰数据库快照与手工备份** / 检查只读 / 大小相同但内容被改写靠哈希发现 / **恢复不删多余文件** / 拒收被篡改的快照文件 / 端到端"误删→补回→复查" |
| `tests/test_storage_snapshot_schedule.py`（新增 **9** 例） | 挂在 Beat 上 / 任务已注册 / **周一三件事的时分与互异性** / 任务真的调用 `create_storage_snapshot` 且不改写缺省参数 / 默认路径 == 真实 storage 与 `_backup` / 不完整是 error 级 / 空存储不刷 error / 内部报错不抛异常 |
| 真库真文件实测 | `ok=True`，**98 个文件 / 82.03 MB**，`unstable=0 failed=0`；`inspect` = `checked 98, missing 0, size_mismatch 0, hash_mismatch 0`；快照落在 `_backup/20260914-013419-storage` |
| 与数据库快照共存 | `list_snapshots()` 共 5 个目录，存储快照正确显示 `has_db=False`，**恢复演练仍选中最新的数据库快照并通过**（`ok=True`，1 条已知警告） |

### AW.6 顺带抓到的同类缺陷：#2 —— "注释承诺了行为，代码没有兑现"

给 `storage_snapshot_task` 写"失败不抛异常"这条测试时它**红了**，
原因是实现里真的没有 `try/except`：备份盘不可写时异常会直接穿透任务，
与它自己 docstring 写的"刻意不抛异常…不应该让 Beat 堆积失败状态"相反。

顺着这条线查，`backup_database_task`（阶段 1′ 第 5 项，早已存在）**有同一个毛病**：
注释承诺了同样的行为，实现同样没有兑现，而且没有任何测试盯着这句承诺。

两处都补上了 `try/except → logger.error + {"ok": False, "error": …}`，
并给后者补了回归测试（`test_backup_failure_does_not_raise`）。

> 这是本项目反复出现的一类问题的新变种：不是"工具写好了没人调用"（AV.1），
> 而是"**注释描述了实现里没有的行为**"。两者的共同点是**都不会报错**，
> 只能靠"为这句承诺写一条测试"来发现。

### AW.7 仍未完成

- 阶段 6 剩余：**6.3 refresh token 轮换 + `jti` 黑名单**（大改动）、
  **6.7 依赖漏洞扫描**（需网络与新工具，待确认）；
- 快照是**全量**复制：当前 82 MB 无所谓，若存储增长到 GB 级，
  应改为增量/硬链接（`--link-dest` 式）方案 —— 尚未做，此处留作已知边界；
- AV.3 的 2 篇残渣笔记（`n1`/`n2`）仍待确认清理。

---

## 附录 AX · 阶段 5.5：`NoteDetail.tsx` 拆分（2026-09-12）

### AX.1 先有安全网，再动手

5.5 一直没有做，不是因为难，而是因为**没有安全网**：1184 行的页面里混着
数据加载、轮询、DOM 标注包装、视频 blob、编辑态、AI 提问浮层，
改错一处就是白屏，而当时前端测试只有 0 条页面级用例。

所以顺序是：先补 `NoteDetail.test.tsx`（10 条，附录 AP），**再**拆。
本轮开工时这套测试已经在，且它守护的正是最危险的那条路径
（`noteLinks` 缺失时的 `?.` / `?? []` 兜底 —— 之前真的白屏过）。

### AX.2 结果

    1184 行  →  282 行（编排层）
    + 24 个模块，最大 239 行（useNoteDetailData.ts）
    = 25 个文件，合计 2166 行

| 层 | 文件 | 职责 |
|---|---|---|
| 编排 | `NoteDetail.tsx` 282 | 路由参数、ADHD 联动、刷新、组合 |
| 数据 | `useNoteDetailData` 239 / `useNoteAnnotations` 202 / `useNoteActions` 160 / `useNoteLinks` 108 | 加载、轮询、标注、动作 |
| 视图 | `NoteDetailHeader` 183 / `ContentArea` 118 / `MarkdownReader` 64 / `DiffPanel` 40 … | 纯展示 |
| 纯函数 | `viewMode` 64 / `selection` 51 / `annotations` 115 / `types` 27 | 可单测的判定逻辑 |

**纯提取，不改行为。** 这条纪律是硬要求：拆分与修 bug 混在一起做，
出问题时无法判断是哪一边引起的。子代理报告了 6 处**既存**缺陷
（陈旧闭包、`videoUrl` 未清空、不可达分支等），**一律未改**，只登记 ——
见 AX.4。

### AX.3 ★ 拆分撞出一条跨模块契约（这就是安全网的价值）

拆完全量后端测试**红了一条**：

    tests/test_week5_6_understanding.py::TestFrontendRoutesAndNav
        ::test_note_detail_has_start_learning

这条后端测试**直接 grep 前端单文件**，断言 `startUnderstanding` 与"开始学习"
出现在 `NoteDetail.tsx` 里。拆分后 `startUnderstanding` 搬去了
`notedetail/useNoteActions.ts` —— 功能完好，断言却失败了。

> 这是"结构测试绑定文件名"的典型脆弱性：它真正想验证的是
> **"这个页面提供开始学习的入口"**，而不是"这个词出现在某个文件里"。

修法不是把断言删掉（那就等于放弃验证），而是让断言跟着**页面**走：
新增 `_note_detail_sources()` 收集 `NoteDetail.tsx` + `pages/notedetail/`
下的全部源码，在合并后的文本上断言。同时补一条
`test_note_detail_source_collection_is_not_vacuous`：
**"收集到东西"本身也是断言** —— 目录被改名后收集为空会让用例 `skip`，
看起来是通过、实际什么都没查，这正是本项目反复出现的**静默失效**。

### AX.4 登记但未修的 6 处既存缺陷（拆分不夹带修复）

| # | 位置 | 现象 |
|---|---|---|
| 1 | `useNoteLinks` | 加载 effect 只依赖 `[noteId]` 却读 `noteRole`：首次加载时 `note_role` 仍为 `undefined`，"个人笔记预填资料"分支会被跳过一次 |
| 2 | `useNoteAnnotations` | 删除标注的监听器捕获的是 effect 时期的闭包，紧接着切换笔记可能删到上一篇的标注（原注释称有意为之） |
| 3 | `NoteDetailHeader` | 审阅按钮的 `status === 'archived'` 分支不可达（与 AI 预处理按钮的渲染条件重叠） |
| 4 | `NoteDetail` | 退出编辑态时 `showAnnotationMenu` 只被渲染条件隐藏、未重置 |
| 5 | `useNoteDetailData` | 切换笔记时 `videoUrl` 未清空：视频笔记之间跳转可能短暂显示上一条的视频 |
| 6 | 多处 | `useToast()` 从 1 处调用变成 5 处（纯提取的副产物，无行为差异） |

这些是**重构过程中发现、但不应在重构中顺手改掉**的东西：
改动需要各自的理由与验证。1/2/5 值得后续单独处理。

### AX.5 验收与仍未做

| 项 | 结果 |
|---|---|
| `npx.cmd tsc --noEmit` | exit 0 |
| `npm.cmd test` | **6 文件 / 71 通过**（基线 71，`NoteDetail.test.tsx` 10/10 未改动） |
| `npm run lint` | exit 0 |
| `npm run build` | 成功，`NoteDetail` chunk 52.45 kB |
| 最大文件 | **282 行**（全部 < 300） |
| 后端全量 | 915 passed / 3 skipped |

**仍未拆**：`KnowledgeGraph.tsx`（980 行）与 `Projects.tsx`（759 行）
—— 它们**没有页面级测试**。5.5 的教训已经写明顺序：先补安全网，再拆。
在没有测试的情况下动这两个文件，是拿白屏换行数。

---

## 附录 AY · `data/tmp_test/` 陈旧临时库清理（2026-09-12）

### AY.1 又是"看不见的增长"，这次在测试基础设施里

`test_db` fixture 会删掉自己的临时库（为此专门释放**两个**引擎的句柄，
见 conftest 末尾注释）。但被强杀/崩溃的会话留下的文件不会被再次使用
（文件名是随机 uuid），于是只能堆积。查 `backend/data` 占用时看到：

    storage 82 MB / chroma 54 MB / models 4543 MB（BGE-M3 权重，必需）
    tmp_test 34.8 MB（120 个文件）  ← 无人清理

与 AF.10（LLM 账本）、`_backup/` 保留策略同源：**只增不减，且没有生产者
会去调用清理函数**。测试基础设施里没有"任务调度"可挂，所以在
`pytest_configure` 会话启动时扫一次是最合适的落点。

### AY.2 ★ 两个由测试抓出来的真实缺陷

**(1) 只看 `.db` 结尾的清理逻辑对真正的堆积物完全无效。**
清点真实目录发现剩下的**全是孤儿侧车**（`test_*.db-wal` / `.db-shm`）：

    test_e7851572.db-shm / test_e7851572.db-wal
    test_fff50dde.db-shm / test_fff50dde.db-wal   ← 主库早已被 fixture 删除

所以清理范围必须包含"主库已不在的侧车"。

**(2) 侧车主库名的推导写错会删掉活库的 WAL —— 这是丢数据。**
第一版写的是 `name[:-len(".db-wal")]`，它**连 `.db` 一起去掉**：
`test_x.db-wal` → `test_x`（而不是 `test_x.db`），于是**每一个**侧车都被判成
孤儿。新写的 `test_removes_only_stale_test_dbs`（主库新鲜 + 侧车陈旧 →
侧车必须原样留着）当场抓到。

正确写法只去掉四个字符 `-wal` / `-shm`。这条判据的性质是
**"删错就是丢尚未 checkpoint 的数据"**，因此必须有一条专门的用例盯着它。

**(3) 顺带修掉顺序依赖。** 第一版依赖 `os.listdir` 顺序：先删主库、再看侧车时
它就成了"孤儿"。现在先把本轮要删的主库集合算出来，再据此判断侧车，
结果与遍历顺序无关。

### AY.3 设计

    只删 6 小时以上的 test_*.db 及其孤儿侧车（-wal / -shm）
      · 新鲜的主库不删        —— 可能是并发会话正在用的
      · 主库活下来的侧车不删  —— 里面可能有尚未 checkpoint 的数据
      · 主库本轮将被删的侧车一并删 —— 主库都没了，侧车没有意义
      · 不匹配 test_ 前缀的文件一律不碰

6 小时阈值的作用是**并发安全**：足够旧到不可能属于另一个正在跑的会话。
目录可用参数注入（`tmp_dir=`），因此判定逻辑可以在临时目录上验证，
不必碰真实的 `data/tmp_test/`。

### AY.4 验收

| 项 | 结果 |
|---|---|
| `tests/test_tmp_db_sweep.py`（新增 7 例） | 只删陈旧项 / **活库的侧车必须留着** / 主库与自己的侧车同去 / 孤儿侧车清理 / 无事发生时安静 / 目录不存在不报错 / 报告释放量 |
| 防"静默失效" | `test_default_directory_is_the_real_one`：缺省目录必须就是 `test_db` fixture 用的那个（写错 = 永远扫不到东西） |
| 后端全量 | 915 passed / 3 skipped；ruff 全绿 |

---

## 附录 AZ · 阶段 5.5 前置：`KnowledgeGraph` / `Projects` 页面级安全网（2026-09-12）

### AZ.1 为什么先补测试、而不是先拆

`NoteDetail.tsx` 的拆分（附录 AX）之所以能安全完成，是因为拆分**之前**已经有
10 条页面级测试。剩下两个巨型页面（`KnowledgeGraph.tsx` 980 行、
`Projects.tsx` 759 行）此前**一条测试都没有**，所以 5.5 对它们的正确第一步
不是"拆"，而是"先让它们可被验证"。

本轮**只加测试、不改任何生产代码**（`git status` 可证）。这条纪律的意义在于：
如果一边补测试一边顺手修 bug，那么"测试发现的到底是原有缺陷还是新引入的缺陷"
就永远说不清了。

### AZ.2 ★ 安全网当场兑现：9 条会白屏的契约漂移路径

喂入**残缺/漂移的接口数据**（后端少一个字段、返回包装对象、返回空体），
页面会直接崩到错误边界 —— 用户看到的是"这个页面出错了"，不是内容。
**全部 9 条都实测崩溃**（用 App 里真实的 `ErrorBoundary` 捕获，不是猜测）：

| # | 位置 | 触发条件 |
|---|---|---|
| 1 | `KnowledgeGraph.tsx:247` | `graphData.nodes` 缺失/null → `nodes.filter` 抛错（`useMemo` 在组件早退**之前**执行，所以早退救不了） |
| 2 | `KnowledgeGraph.tsx:249`（及 `:808`） | `graphData.edges` 缺失/null |
| 3 | `GraphSidebar.tsx:87` | `stats.relation_type_distribution` 缺失（只要统计面板渲染就崩） |
| 4 | `GraphSidebar.tsx:378` | `suggestions` 返回包装对象 `{items:[…]}` → 点开"建议"面板即崩 |
| 5 | `NodeInspector.tsx:53` | 节点缺 `note_id` 时点击 → `note_id.slice(0,8)` |
| 6 | `GraphSidebar.tsx:127` / `:140` | 子图缺 `center_node` / `neighbor_nodes` |
| 7 | `Projects.tsx:718` / `:733` | `/projects` 返回 `undefined`（204/空体）或非数组 → `length` / `.map is not a function` |
| 8 | `Projects.tsx:315` | 候选笔记 `title: null` **且**用户在搜索框输入 → `.toLowerCase()` |

另有 2 处**不崩但会误导**的瑕疵：KnowledgeGraph 顶部节点数用的是**未过滤**的
`graphData.nodes.length`（画布已滤掉回收站节点）→ "标题 3 节点、画布 2 个"；
Projects 在 `note_count` 缺失时把字面量 `undefined` 显示进文案。

> 这与 `NoteDetail` 的 `noteLinks` 白屏（附录 AP）是**同一类**问题：
> 前端把"后端一定给全字段"当成前提。区别是这次是在**补测试**时主动找出来的，
> 而不是等用户撞上。

### AZ.3 ★ 用变异测试证明安全网真的会咬

新增的 51 条用例如果只是"能跑绿"，它就没有价值 —— 常见的失败形态是用例
**恒真**（断言的东西无论如何都成立）。因此逐条做了变异验证：

| 变异（临时改生产文件） | 结果 |
|---|---|
| 删掉回收站过滤 | 只有"回收站节点不进画布"变红 ✔ |
| 删掉坐标守卫 | 只有"绘制回调对无坐标节点不抛错"变红 ✔（替身 canvas 对非有限数值抛错，所以守卫确实被压住） |
| 删掉删除项目的 `confirm` | 只有"删除必须先确认"变红 ✔ |
| 候选过滤改成恒真 | 只有"只列尚未归属本项目的笔记"变红 ✔ |

改完**逐字节还原**（`git checkout` + `git status`/`git diff` 核对无 M）。

### AZ.4 覆盖边界（写清楚"测不到什么"）

- 真实 `react-force-graph-2d` 的**布局与绘制**测不到：jsdom 没有 canvas
  （`getContext('2d')` 返回 null）。替身渲染节点/边的可点击元素并驱动页面回调，
  因此页面的数据装配与回调被覆盖，**库内部不被覆盖**；
- 因此未覆盖：悬停去重、拖拽、真实缩放/平移手势、`onZoom` → minimap 节流、
  画布上的 +/-/适应屏幕按钮；
- 网络层按既有约定整体 mock（`request()` 的错误映射 / 401 刷新 / SSE 不在本轮）。

### AZ.5 验收

| 项 | 结果 |
|---|---|
| 新增用例 | `KnowledgeGraph.test.tsx` **29** + `Projects.test.tsx` **22** = 51 |
| 全仓前端 | **10 文件 / 143 通过**（基线 71 + 本轮 51 + 并行任务 21） |
| `npx.cmd tsc --noEmit` / `npm run build` | exit 0 |
| 生产代码改动 | **零**（`KnowledgeGraph.tsx` / `Projects.tsx` 未出现在 `git status` 中） |

---

## 附录 BA · 阶段 6.3：刷新令牌、轮换与登出撤销（2026-09-14）

### BA.1 要修的是什么

`§2.5 E-1` 的那句话在本项之前一直成立：

> JWT 有效期 24 小时，且**无 token 撤销机制**（payload 只有 `sub` + `exp`，
> 无 `jti`、无黑名单、无 refresh token 轮换）—— **一旦泄露，24 小时内无法吊销**。

而且"登出"是假的：前端 `removeToken()` 删掉 localStorage 里那串字符，
服务端**一无所知**，被复制走的那枚令牌照样能用满 24 小时。
访问令牌是无状态的，这既是它的优点（每个请求不必查表），也是它无法被撤销的**原因**。

本轮引入的锚点是**有状态的刷新令牌**：

    登录        → 下发（access, refresh），refresh 落库（新开一条 family 链）
    刷新        → 校验 refresh → 旧行 revoked + replaced_by_jti → 下发新的一对
    重放        → 已撤销的 refresh 再次出现 ⇒ 判定泄露 ⇒ 撤销**整条链**，401
    登出        → 撤销该 refresh（可选 all_devices：撤销该用户全部 refresh）

### BA.2 设计要点

**「jti 黑名单」就是 `refresh_tokens` 表本身。** 没有另建一张黑名单表：
一行记录同时承担"这枚令牌存在过"与"它是否已作废"（`revoked_at IS NULL`），
两个状态放在一处就不会互相矛盾（分成两张表时，"黑名单说撤销了、签发记录说还在"
是必然会出现的形态）。`replaced_by_jti` 让轮换链可追溯 —— 排查盗用时能顺着链
看清"哪一枚换成了哪一枚"。

**链的粒度是"一次登录"（`family_id`），不是"一个用户"。** 每次登录新开一条链，
轮换沿用同一条链。于是：

- 一台设备登出/换令牌，**不影响**其他设备的会话（各自的链独立）；
- 重放检测只杀那一条链。按用户撤销会把"一次误判"放大成"所有设备被踢下线"。

**`typ` 声明必须双向严格，只在一个方向留兼容。** 判据收在
`auth_service.is_access_token_payload` 一处（`middleware/request_context.py`
解析日志用户时也复用同一判据，避免两处慢慢分叉）：

| 令牌 | `decode_access_token` | `decode_refresh_token` |
|---|---|---|
| `typ=access` | 接受 | **拒绝** |
| `typ=refresh` | **拒绝** | 接受（且要求 jti/sub/fam/exp 齐全） |
| **无 `typ`（存量）** | 接受（兼容） | **拒绝** |

无 `typ` 一律按访问令牌处理，是为了让**升级动作不打断正在使用的人**：
本次改造前签发的令牌在各自 `exp` 之前必须继续可用。反向不放宽 ——
刷新令牌有效期 30 天，若它能当访问令牌用，等于把会话有效期从 24 小时悄悄拉到 30 天。

**轮换用条件更新做成原子操作。** 旧行的撤销是
`UPDATE ... WHERE jti=? AND revoked_at IS NULL`，`rowcount != 1` 说明这枚令牌
在读取之后被别人用掉了 —— 与"重放已撤销令牌"是同一件事，同样撤销整链
（先插入的新行被 `rollback()` 撤掉，不会留下一枚无人知晓的令牌）。
顺序上先 `flush` 新行、再撤销旧行、最后一次 `commit`：中途失败不会留下
"已签发但无法追溯"的令牌。

### BA.3 ★ 三条刻意取舍（都要能被推翻，所以写清楚）

**(1) 访问令牌仍然无状态 ⇒ 登出后到 `exp` 之前它仍然可用。**
本项保证的是"会话**无法再被延长**"，不是"登出瞬间所有凭证失效"。
要让访问令牌即刻失效，就得在每个请求上多查一次黑名单，并且把
"清理过期行"与"会话是否有效"耦合起来 —— 清理掉证据就会误杀有效会话
（一条链的证据被删掉后，"这条链还活着吗"就无从判断）。
本轮不做，`jwt_expire_minutes` 也**保持 1440 不变**（改它会改变所有客户端的
续期频率，属于需要单独观察的变更）。这条边界有测试**显式盯着**
（`test_documented_limitation_access_token_survives_until_expiry`）：将来做
"登出即失效"时，那条测试就是需要被推翻的那一条。

**(2) 登出免认证，但只信"签名可验证的令牌"。**
登出必须**在任何情况下都能调用**：令牌已过期、已被轮换、已被撤销，恰恰是
用户来点登出的常见状态；要求先出示有效访问令牌，会让撤销能力在最需要它的
时刻失效。因此 `/api/auth/logout` 不带 `security` 声明，且对无效输入一律
返回 `200 {"revoked": 0}`（幂等）。
代价与防线：这是一个"未认证即可写库"的端点，所以(a)挂了限流规则，
(b)服务端只根据令牌里的 `jti`/`sub` 动手，**绝不接受客户端传 `user_id`** ——
否则它就是"任何人可以注销他人全部会话"的入口。测试里正反两向都钉住了：
无效令牌返回 0 **且**撤销不了任何东西（真实会话随后仍可用）。

**(3) 清理只删"已过期"的行，绝不删"已撤销但未过期"的行。**
后者是重放检测**唯一**的证据。删掉它，重放只会得到"查无此 jti"的普通 401，
"撤销整条链"这条处置就静默失效了 —— 而且不会有任何测试因此变红。
`refresh_tokens` 每次登录一行、每次刷新一行（30 天有效期），只增不减，
所以清理必须有调用方（本项目已经栽过三次"工具写好了没人调用"：
AF.10 的账本清理、AU.1 的限流规则、AV 的存储审计）：新增 Beat 任务
`cleanup-refresh-tokens-daily`（每日 04:15，与 04:30 的账本清理错开分钟，
避免两个写任务抢 SQLite 的写锁），单批 5000 行、单次最多 20 批 ——
**有界**，首次运行的巨删不会把写锁占住让整站 `database is locked`。

### BA.4 前端：401 → 刷新一次 → 重放一次（且必须"单飞"）

`api/client.ts` 里抽出 `authorizedFetch()`（原 `request` / `uploadRequest` /
`askQuestionStream` 三段各自的 fetch + 超时 + 401 处理合一）：

    doFetch → 401 且不是凭据接口/刷新接口 → refreshSession() → 成功则原样重放**一次**

两个必须写对的地方（都有测试钉住）：

- **重放只有一次**：刷新成功后的第二次响应（哪怕是 401）直接交给调用方，
  由它清凭据并派发 `token-expired` —— 不存在第二次刷新，也就不会成环。
- **并发的 401 共享同一次刷新**（`refreshInFlight` 单飞）。这不是优化而是**正确性
  要求**：服务端开了轮换，每次刷新都会把提交的那枚令牌标记为已撤销；两个请求
  各刷一次，后一次提交的就是刚被撤销的令牌，服务端判定为**重放**并撤销整条链，
  用户被莫名其妙地登出。一个仪表盘同时打 5 个接口时，令牌一过期就会命中这个形态。

`AuthContext.logout` 变成异步：先尽力撤销服务端会话，**无论成功失败**都清本地令牌
（把"撤销失败"变成"登不出去"会让用户卡在一个自己已经不想用的会话里）。
`api/notes.ts` 里手写 Authorization 头的那份流式请求也改走 `authorizedFetch` ——
两份实现必然会在"过期后怎么办"上分叉。

### BA.5 验收

| 项 | 结果 |
|---|---|
| `tests/test_refresh_tokens.py`（新增 44 例） | 类型混淆（双向 + 占位符缺失 + 未知 typ）/ 落库 / 轮换链 / 重放撤销整链 / 登出（按链、all_devices、幂等、**无请求体**、无效输入、**撤销不了别人的**）/ 过期 / **存量无 `typ` 令牌仍可用** / 配置非正数显式失败 / 日志无完整令牌 / 接线（端点真调用服务层）/ 新端点被限流覆盖 |
| `tests/test_refresh_token_maintenance.py`（新增 14 例） | Beat 挂载 + 任务注册 + 错开分钟 / 任务真调用 purge / **已撤销未过期的行必须留着** / 有效行不删 / 分批有界且能排干 / 批次上限生效 / 失败不抛异常 |
| `tests/test_refresh_token_migration.py`（新增 10 例） | `011` 真能执行出表与索引 / jti 唯一性由**数据库**保证 / 外键级联 / downgrade 干净 / **迁移 ↔ 模型**逐列逐索引一致 / 只在临时目录里跑 |
| `frontend/src/api/client.test.ts`（新增 17 例） | 刷新+重放（含新令牌生效）/ **只重放一次** / 刷新失败清凭据回登录 / **并发 401 只刷新一次** / 无刷新令牌不发请求 / 凭据接口不刷新 / 刷新接口不递归 / 非 401 不刷新 / 上传与流式同样重放 |
| `frontend/src/contexts/AuthContext.test.tsx`（新增 4 例） | 登录存两个令牌 / 登出把刷新令牌交给服务端 / **服务端撤销失败也必须清干净本地** / 过期事件切未登录 |
| 后端全量 | 983 passed / 3 skipped（基线 915 + 本项 68）；ruff 全绿 |
| 前端 | 本项涉及的 8 个文件 **92 passed**（既有 71 + 本项 21）；全量 145 passed / 10 files（含同一工作区里并行工作的两个页面测试文件）；`tsc --noEmit` / `npm run lint` / `npm run build` 全部通过 |
| 真实库 | **未触碰**：`backend/data/db/engramnote.db` 既没有 `refresh_tokens` 表，mtime 也停在改造前（迁移只在临时库里验证） |

### BA.6 迁移 `011_refresh_tokens` 的验证方式（以及一个既有问题）

计划里 `§2.6 M-1` 已经写明：**整条迁移链在空库上跑不通**（001 直接在
`ADD COLUMN` 上起步、没有建基础表的基线；`alembic.ini` 指向 PostgreSQL 而
`env.py` 用同步 API）。那是既有问题，不属于本项，本轮也没有顺手去"修"它
（修它等于重建整条链，风险与工作量都远超本项范围）。

但**新迁移不能因此不验证**。做法是在临时 SQLite 库上、用
`Operations.context(migration_context)` **只执行 011 这一个迁移**
（它只新建一张表、只依赖 `users` 存在），于是下面这些都被真正跑过：
DDL 可执行、`jti` 唯一索引真的建出来（重复插入必须报 IntegrityError）、
`ON DELETE CASCADE` 真的生效、`downgrade` 干净回退。

顺带补上一条**防漂移**断言：把迁移建出的表与 `Base.metadata` 描述的
`refresh_tokens` 逐列、逐可空性、逐索引比对。这两个来源一旦分叉，症状是
"新库正常、老库缺索引"，且**只在真库上复现**。

### BA.7 未做与遗留

- **访问令牌的即时吊销**（BA.3(1)）：需要"每个请求查一次黑名单"+ 明确
  "清理过期行"与"会话有效性"的关系，属于独立变更。
- **设备列表 / 远程踢设备 UI**：本轮没有存 `user_agent` / `ip`。没有消费方的
  数据收集只是隐私成本 —— 将来要做"会话管理"页面时再一并加列（纯加列迁移）。
- **`/api/auth/logout` 无法撤销"已过期"的刷新令牌**（`revoked=0`）：它本来就
  用不了，撤销它没有安全收益；若要支持"用过期令牌登出全部设备"，
  需要为登出单独放开 `exp` 校验并论证其副作用。

---

### AZ.6 处置：9 条崩溃全部修好，★ 用例翻成正向断言

安全网立起来之后才动生产代码。**最小化护栏 + 在边界归一化**（不是到处撒 `?.`）：

| # | 护栏 | 修好后的用户可见行为 |
|---|---|---|
| 1/2 | `normalizeGraphData`（`KnowledgeGraph.tsx:56`，`Array.isArray` → `[]`）+ memo 自身也空安全（`:290-291`） | `nodes` 缺失 → "暂无图谱数据" + 上传/理解提示，无画布；`edges` 缺失 → 画布只有节点、0 条边 |
| 3 | `GraphSidebar.tsx:64` `stats.relation_type_distribution ?? []` | 统计面板保留 节点/边/待确认/孤立节点，只少了分布条 |
| 4 | `normalizeSuggestions`（`:66` 应用于 `:229`） | `{items:[…]}` **拆包后正常渲染**（不是降级成"暂无"） |
| 5 | `NodeInspector.tsx:15/52/61` | 缺 `note_id` → 静态"未知来源"，不跳转，面板其余部分完好 |
| 6 | `normalizeSubgraph`（`:84` 应用于 `:276`） | 缺 `neighbor_nodes` → "→ 0 个关联节点"；连 `center_node` 都缺 → 该面板不渲染，图谱不受影响 |
| 7 | `Projects.tsx:98-99` 拆包/兜底 | `undefined`/204 → "还没有项目"；`{items:[…]}` → **拆包后正常渲染** |
| 8 | `Projects.tsx:321` `(n.title ?? '')` | null 标题渲染为空标签，永不匹配非空搜索，不崩 |
| 9 | `:857-859`（计数与画布口径一致）、`Projects.tsx:368/548` `?? 0` | 顶部节点数不再与画布矛盾；`note_count` 缺失显示"0 篇笔记"而不是字面量 `undefined` |

**一处刻意的设计选择**：`{items:[…]}` 这种**包装对象**选择"拆包后渲染"，而不是降级成空状态。
理由：数组存在且可恢复，显示"还没有项目"等于**告诉用户数据没了，而数据其实在** ——
这正是"优雅降级"与"静默丢失"的分界。真正缺失/非数组（`undefined`、`null`、`{}`）才走空状态。

**变异证据**（逐条回退护栏，看是否正好红对应用例）：9 条全部 ✔。
另加两条**反空转**验证 —— 把两个拆包分支强行改成 `[]` 后，"取内层数组渲染"那两条
立刻变红，说明它们断言的是**真的渲染出了数据**，而不只是"没崩"。
6 个文件改完**逐字节还原**（SHA256 前后一致）。

**顺带修掉一处原本不可达的瑕疵**：把 `maxCount` 提到守卫外之后，
空分布不再算出 `-Infinity`（原本会导致 `width: NaN%`）。

### AZ.7 本轮暴露的**契约漂移**（未修，属后续）

`api/graph.ts:77-80` 声明 `getSuggestions()` 返回普通数组，
`api/projects.ts:151-153` 声明 `/projects` 返回 `Project[]` —— 但两者**都观察到**
可能返回 `{items:[…]}`。护栏把它们**静默容忍**了，因此真正的后端契约破坏
从此不再在任何地方报错。这是"宽容解析"的固有代价，值得后续补一个
可见提示（toast / 行内提示），否则漂移会继续无声扩大。

> 这与 AZ.2 是同一个教训的两面：**不做防护**会让用户看到白屏，
> **只做防护不报警**会让契约漂移静默扩散。两件事都要做。

---

## 附录 BB · 阶段 5.5 收尾：`KnowledgeGraph` / `Projects` 拆分（2026-09-14）

### BB.1 前置条件先兑现，再动刀

这两个页面在附录 AZ 之前**没有任何测试**，所以 5.5 对它们的正确顺序是：
① 补页面级安全网并**用变异测试证明它会咬**（附录 AZ）→ ② 修掉安全网探出的
9 条白屏路径（AZ.6）→ ③ **才**做纯提取式拆分。本附录是第 ③ 步。

顺序不是形式主义：第 ② 步改动了生产代码，如果与拆分混在一起，
一旦出问题就无法区分"是拆错了"还是"护栏改错了"。

### BB.2 结果

| 文件 | 拆分前 | 拆分后 |
|---|---|---|
| `KnowledgeGraph.tsx` | 1055 | **248** |
| `Projects.tsx` | 765 | **113** |

- `pages/knowledgegraph/` **13 个模块**：`normalize` 54 / `buildForceGraphData` 56 /
  `renderMinimap` 103 / `drawNode` 182 / `drawLink` 107 / `useCanvasObjects` 61 /
  `useGraphData` 66 / `useGraphSearch` 55 / `useSubgraph` 38 / `useGraphInteraction` 157 /
  `useSuggestionSelection` 61 / `useGraphMutations` 213 / `GraphToolbar` 153
- `pages/projects/` **12 个模块**：`helpers` 42 / `useProjects` 212 / `useAddNotesPanel` 100 /
  `NewProjectForm` 105 / `ProjectRenameForm` 48 / `ScanResultPanel` 38 / `AddNotesPanel` 127 /
  `ProjectNotesList` 80 / `ProjectCard` 215 / `ProjectsHeader` 14 / `ProjectsErrorBanner` 34 /
  `ProjectsUsageNotes` 27
- **27 个文件 / 2725 行，最大 248 行，超过 300 行的文件数为 0**

沿用 `pages/notedetail/` 的既有约定；`components/graph/`（`GraphSidebar` /
`NodeInspector`）**未动**，不另建竞争结构。

### BB.3 行为保持的三重证据（"纯提取"是可验证的，不是自称的）

1. **安全网 53 条用例全绿**，其中包括 AZ.2 那 9 条契约漂移降级路径 —— 它们
   断言的正是"喂残缺数据时用户看到什么"，拆分若改了渲染分支立刻会红；
2. **两个测试文件逐字节未改**（`git diff --stat` 为空）：不需要任何 import 路径
   调整，说明模块边界划在了正确的位置；
3. **逐字核对**：旧文件的实质行（≥18 字符）都能在新模块中逐字找到，例外仅限
   必然的改名（import 路径、`graph.`/`interaction.` 前缀、handler 接线、
   `renderCard` → `<ProjectCard>`）；含中文字面量逐字节比对 —— 旧
   `KnowledgeGraph.tsx` 的 24 个中 23 个原样存在（唯一差异是模板串的插值前缀
   变了、**渲染结果不变**），旧 `Projects.tsx` **43/43 原样存在**。

### BB.4 唯一一处非"纯搬"编辑（如实登记）

`pages/knowledgegraph/useGraphSearch.ts`：拆分**之后** eslint（react-hooks v7 的
编译器诊断）报出 effect 内引用了声明在其后的 `handleSearch` —— 旧文件同样写法，
但 1055 行的文件被 v7 跳过、拆小后才开始分析。修法是把**函数声明的文本位置**
提到 effect 之前：函数声明会提升，运行时语义完全相同（搜索防抖用例仍绿）。
**没有加 `eslint-disable`、没有改逻辑。**

> 这条本身值得记：**拆分会让原本被"文件太大"掩盖的 lint 诊断浮出来**。
> 那不是拆分引入的缺陷，而是拆分暴露的旧账。

### BB.5 ★ 拆分暴露的一条真实白屏路径：归一化只做了一半

拆分把模块边界划清之后，一处**不对称**变得刺眼：

| 入口 | 是否归一化 |
|---|---|
| `useGraphData.fetchData`（加载路径） | ✅ 过 `normalizeSuggestions` |
| `useGraphMutations.handleGenerateSuggestions`（"生成相关建议"动作） | ❌ 直接 `setSuggestions(原始响应)` |

也就是说：后端若在**生成**这条路径上返回 `{items:[…]}`，建议面板会走 `else`
分支对非数组 `.map` → 渲染抛错 → **整页被错误边界接走**。而 AZ.2 的漂移用例
只覆盖了加载路径。

> 这是"护栏加在一个入口、漏了另一个"的典型形态 —— 与 AZ.7 记录的契约漂移
> 同源：**同一个数据的每一条入口都必须过同一道判据**，否则护栏只提供虚假的安心。
> 处置见 BB.6。

### BB.6 处置：不是一处，而是三处同类漏口（审计后修复）

顺着"同一个 API 的每一条入口是否过同一道判据"这条线做了一次**穷举审计**
（列出两个新目录里**所有**会写列表状态的赋值点，逐个判定），结果比报告的多：

| # | 位置 | 情况 |
|---|---|---|
| 1 | `useGraphMutations.ts:163` `handleGenerateSuggestions` | 报告的那一处：`setSuggestions(原始值)` → `normalizeSuggestions(...)` |
| 2 | `useGraphMutations.ts` **7 处** `setStats(原始值)`（`:91/109/127/146/167/185/202`） | **同一个 `getGraphStats()` 响应**：加载路径过了 `normalizeStats`，7 处"写操作后重拉"没过 → 分布字段是对象时渲染抛错、整页白屏 |
| 3 | `projects/ProjectCard.tsx:82` `detail?.notes ?? []` | `{notes:{items:[…]}}` 或 `{notes:{}}` 时白屏；`?? []` 只覆盖了"字段缺失"这一种 |

**修复原则：复用同一个归一化函数，不写第二个判据。** 判据只能有一处
（数组原样 / `{items:[…]}` 拆包渲染 / 真正非数组才退化成空），
否则就是在制造下一次判据漂移 —— 那正是本项目已经付过代价的错。
修好之后 `suggestions` 对**所有**写入方都恒为数组，这也让另外 4 处
`setSuggestions(prev => prev.filter(...))` 的函数式更新所依赖的不变量真正成立。

**穷举审计中确认"不属于此类"的（逐个核实，不是想当然）**：

- `useGraphSearch.ts:23` `result.items || []` 没有 `Array.isArray`，但
  `GraphToolbar.tsx:75-77` 用 `searchResults.length > 0 && … .map(…)` 短路，
  非数组会降级而非抛错；
- `useAddNotesPanel.ts:44` 在 `try` 内，漂移落成面板内提示（与 BB.8 那条一致）；
- `useProjects.ts:144` `setScanResults` —— 面板只渲染标量，三个列表字段**从不渲染**；
- `setGraphData` 的 8 处原始写入**安全**：消费者读的是
  `useMemo(() => normalizeGraphData(raw))`，判据在读取侧、不在写入侧（这是另一种
  正确做法：**判据放在唯一的读取收口处**）。

**每条都用变异验证**（单独回退修复 → 定向跑用例）：回退 `:163` → `2 failed`；
回退 7 处 `normalizeStats` → `1 failed`；回退 `ProjectCard` → `1 failed`。
三次失败的 DOM 里都出现了 `这个页面出错了` —— 说明用例咬住的是**真实的白屏**，
而不只是"没崩"。5 个文件改完 SHA256 前后一致（这些文件尚未提交，故不用 `git checkout`）。

新增 4 条用例（`KnowledgeGraph` +3 / `Projects` +1），前端 **145 → 149 通过**。

### BB.7 验收

| 项 | 结果 |
|---|---|
| `npm.cmd test` | 拆分前后均 **10 文件 / 145 通过**（计数不降）；BB.6 的修复再加 4 条 → **149 通过** |
| `npx.cmd tsc --noEmit` / `npm run lint` / `npm run build` | 全部 exit 0 |
| 最大文件 | **248 行**，超过 300 行的文件数为 0 |
| 后端测试是否 grep 这两个页面 | **没有**（已 grep `*.py` 确认，因此不存在 AX.3 那类跨模块契约坑） |
| 编码 | 27 个文件无 BOM、严格 UTF-8、LF |

### BB.8 只报告、未处理

- **4 段近乎相同的"重新拉取"块**（`handleConfirm`/`handleReject` 顺序 await，
  批量/删除/新建用 `Promise.all`）：合并会改变 await 顺序，而**调用次数是可观测的**，
  因此不合并；
- `useGraphMutations` 用裸 `confirm(...)`，`useProjects` 用 `window.confirm(...)`
  （浏览器里是同一对象，但写法不一致）；
- `useAddNotesPanel.openAddPanel` 对 `data.items` 没有 `Array.isArray` 兜底
  （其它列表接口都有）；好在整段在 `try/catch` 内，漂移会降级成面板内提示而非白屏；
- `formatSize(0)` → `'—'`（0 字节的笔记显示成"未知大小"）；
- Projects 的"加载失败"与"重命名校验"共用同一个 `error` 槽位，点一次 ✕ 会把两者
  一起清掉。

---

## 附录 BC · 阶段 5.10：移动端可用性（2026-09-14）

### BC.1 起点：`responsive.css` 只有 51 行

计划里 5.10 的验收标准是**"手机可用"**四个字，而改造前全站响应式规则只有 51 行 ——
等于没有移动端。本轮做到 **443 行**，但重点不是行数，而是**修的是什么**。

### BC.2 断点只用两个，其余交给内在尺寸

| 断点 | 依据 |
|---|---|
| `max-width: 768px` | **项目原本就在用的断点**；且桌面固定 240px 侧栏在 <768px 时正文只剩不到 530px |
| `max-width: 480px` | 窄屏手机（360–430px），验收目标区间 |

其余挤压一律用内在尺寸解决（`min()` / `auto-fit` / `flex-wrap`），**不再加断点** ——
断点越多越难维护，且每个断点都是一次"忘了改就错位"的机会。

### BC.3 ★ 根因：内联样式优先级高于媒体查询

有一批"响应式改不动"的地方，根因不是媒体查询写错了，而是**目标属性的值写在
内联 `style` 上**：媒体查询永远赢不了内联样式。因此本项目里"我在 CSS 里改了却没生效"
多半是这个原因（对应原审计的 F-16）。

处理方式不是加 `!important`（那会制造下一轮覆盖战争），而是**把必须能被响应式
改到的内联值搬进 CSS 类**：`Dashboard` 的两处 `1fr 1fr` → `.dashboard-two-col`、
图谱页的固定高度与侧栏 `width:320`、`EditSplitView` 的 textarea padding。
**页面逻辑一行未改**，只搬了样式归属。

### BC.4 修掉的几个真缺陷（不只是"看着挤"）

| 位置 | 改造前 | 改造后 |
|---|---|---|
| `NoteAskPanel`（★ 真 bug） | 用**常量 480** 夹取 `left`：375px 视口下右边界 127 < 左边界 248，再叠加 `translate(-50%)` → **浮层被推出屏幕外** | 按**实际宽度** `min(480, innerWidth-24)` 夹取；视口比面板还窄时居中。**宽屏下 panelWidth 恒为 480，桌面逐像素不变**（有测试钉住 `left=248 / width=480`） |
| 图谱页 | 375px 屏上侧栏占满，画布只剩**约 55px 竖缝** | ≤768px 侧栏变**贴底抽屉面板**，画布恢复整宽；缩略图隐藏、缩放键移到右上并放大到 40px |
| 笔记详情 | 长 URL/英文顶出**整页横向滚动**；宽表格与 KaTeX 同样 | 表格自身 `overflow-x:auto`（不是整页滚）、KaTeX 块可滚、正文 `overflow-wrap:anywhere`、字号 1rem / 行高 1.85 |
| 卡片复习（核心循环） | 自评按钮偏小 | `.self-rating-btn` min-height 56px（≤480px 60px），`.btn` 40/44px（**显式排除 checkbox/radio**） |

**一处容易漏的细节**：移动端抽屉宽度必须**同时覆盖 `.sidebar-collapsed`**。
只在桌面上收起过侧栏的用户，回到手机时会拿到 64px 的图标条 —— 因为收起状态是持久化的。
只写 `.sidebar` 的规则对他无效。

### BC.5 新增交互与测试（+3 文件 / +20 用例）

| 文件 | 用例 | 钉住的行为 |
|---|---|---|
| `components/Sidebar.test.tsx` | 13 | 14 个目的地全部可达；**三条关闭路径**（关闭键/遮罩/点导航项）；关闭键**只在打开时渲染**；打开加锁 → 关闭解锁 → **开着被卸载也必须解锁**；折叠态仍带 active 类 |
| `App.test.tsx` | 4 | 汉堡按钮 `aria-expanded`；遮罩出现；`body` 加锁；导航后自动收起并解锁 |
| `components/NoteAskPanel.test.tsx` | 3 | 375px / 360px 视口浮层左右边缘都在视口内；**1024px 下 `width=480`、`left=248`（桌面不回归）** |

> 那三条**关闭路径**值得单独说：移动端抽屉最常见的线上事故是
> "打开了关不掉"（遮罩点不到、关闭键被 z-index 压在下面、点了导航项不收起），
> 所以每一条都要求有用例，而不是只测"能打开"。

### BC.6 ⚠️ 只有真浏览器才能验证的部分（不声称已验证）

jsdom **不加载样式表、不做布局**。因此以下全部**只有规则声明、没有渲染结果**：
抽屉实际宽度/滑出动画/层级遮挡、44px 触控高度的真实高度、宽表格与 KaTeX 的真实滚动、
`env(safe-area-inset-*)`、`100dvh` 与地址栏收放、图谱贴底面板的观感、
横竖屏旋转边界。另有一条**已知未处理的边界**：抽屉开着时旋转到 >768px，
`body` 会保持锁定滚动（没有 `matchMedia` 监听），需点一次关闭键或导航项解除。

`index.html` 的 viewport **原本就是正确的**（且没有禁用缩放），本轮只补
`viewport-fit=cover` 与对应安全区 —— 先去核对而不是直接改，避免"修了不存在的问题"。

### BC.7 发现但受范围限制未修的三项（待补）

前两项是**真缺陷**，只是落在当时另一个代理正在编辑的目录里：

1. `pages/projects/ProjectCard.tsx` 底部操作行 `display:flex` 缺 `flex-wrap`：
   ~320px 视口上 5 个按钮会**横向溢出**。**已修**（4 处内联 flex 行都加 `flexWrap: 'wrap'`；
   这些行**没有类名**，CSS 侧无法干净命中，所以只能在 TSX 里修）。
2. `pages/knowledgegraph/useGraphInteraction.ts` 的 `sidebarOpen` 默认 `true`：
   手机上**首屏就展开贴底抽屉**，盖住约半屏画布。**已修**：初值改为
   `window.innerWidth > 768`（与 CSS 断点同值，注释写明两者必须一致）；桌面仍是默认展开。
3. **iOS 聚焦缩放**（输入框字号 <16px 会触发）：`AddNotesPanel` 的搜索框**已修** ——
   它本来是内联 `0.8rem` **压住了全局正确的 `1rem`**，删掉内联样式即恢复正确（不是绕过去）。
   ⚠️ **仍未修的两处，且必须改在正确的位置**：`.ask-ai-input`
   （`styles/markdown-extras.css:149`，0.9rem）与 `.graph-search-input`
   （`styles/graph.css:386`，0.8rem）。**不能**从 TSX 注入内联覆盖 ——
   内联样式会永久压过样式表并**掩盖正确的修法**；而且 `markdown-extras.css` 在
   `responsive.css` **之后**加载，所以写在 responsive.css 里的媒体查询也会输，
   必须改在各自样式表里。
4. `components/Sidebar.tsx` 里"上传资料"按钮**嵌在导航项按钮内部**
   （`<button>` 套 `<button>`，React 会告警 DOM 嵌套非法）—— 改造前既有，属 5.9 范围。

---

## 附录 BD · 契约漂移可见化 + 5 处小瑕疵（2026-09-14）

### BD.1 要解决的是"宽容"的代价

AZ.7 记录过一个两难：**不做防护**会让用户看到白屏；**只做防护不报警**会让
真正的后端契约破坏**不再在任何地方报错**。上一轮选了后者（先止血），
本附录把缺口补上：**容错照旧，但漂移必须看得见**。

### BD.2 判据与提示各只有一处

新增 `frontend/src/pages/contractDrift.ts`（**故意放在 `pages/` 下**，不放进任何一个
feature 目录 —— 它必须被两个目录共用，否则就是跨目录 import 或**判据重复**，
而判据重复正是 BB.6 记录的"三处漏口"的成因）：

| 原语 | 适用契约 |
|---|---|
| `coerceArrayPayload` | 顶层应是数组（`{items:[…]}` = 漂移 → 拆包**并**上报） |
| `unwrapPageItems` | 分页响应 `{items,…}` —— 这里 `items` **就是**契约，正常分页**不报** |
| `coerceArrayField` | 对象响应里的数组字段 |

三者全部汇入 `reportDrift()` —— "什么算异常形状"只有这一个判断点。

**提示复用既有的全局 Toast**（`main.tsx` 已挂载），不新增 UI、不新增 CSS、不新增依赖。

### BD.3 防刷屏是必需的，不是优化

`normalizeGraphData` 在 `useMemo` 里**每次渲染都跑**，而图谱页**每次写操作后都重拉统计** ——
不防就是几十条提示。做法：去重键 `source|field|kind`，**每个会话只报一次**，
再叠加 Toast 自身对同文案的合并。总量因此有界（端点 × 字段 × 2）。

### BD.4 5 处小瑕疵（每处都有用例）

| # | 问题 | 处置 |
|---|---|---|
| 1 | 4 段"写后重拉"块重复 | 抽 `refetchGraphAndStats(mode)`，**两种模式各自保留**（顺序 `await` vs `Promise.all`）—— 调用次数与顺序是**可观测**的，两种模式各有测试钉住 |
| 2 | 裸 `confirm` vs `window.confirm` | 统一为裸 `confirm`（全仓 8:4 占优）。风格在运行时不可见，故用 `?raw` 断言源码；行为仍由原有 confirm 测试钉住 |
| 3 | `openAddPanel` 缺 `Array.isArray` 兜底 | 复用统一原语 + 漂移提示；不再把"形状不对"伪装成"加载候选笔记失败" |
| 4 | `formatSize(0)` → `'—'`（0 字节笔记显示成"未知大小"） | 改为 `'0 B'`；`'—'` 只留给 `null/undefined/NaN` |
| 5 | 加载失败与重命名校验共用一个 `error` 槽位 | 拆成两个槽位，点一个 ✕ 不再清掉另一个；两个 ✕ 的 `aria-label` 可区分 |

### BD.5 ★ 变异验证 12 项（关键的一条是 (c)）

逐项回退 → 定向跑用例 → 逐字节还原（SHA256 一致）：

- 去掉上报广播 → 25 条红；
- **让判据对"正常形状"也上报 → 18 条红，其中包含两条"正常响应一条提示都不报"** ✔
- 去掉去重键 → 5 条红；去掉"包装上报" → 只有 3 条包装用例红（非数组那批仍绿，说明判据分得开）；
- 两种 mode 互换 → 各自只红对应的顺序用例；
- 回退 5 处修复 → 各自只红对应用例（含 `${'`clearError`'}` 与 `clearRenameError` 两个方向）。

> 加粗那条是本次最重要的一条：**一个"总是响"的告警比没有告警更糟** ——
> 用户会学会忽略它，于是它再也挡不住任何东西（本项目那份"一直为红"的存储审计
> 就是这个下场）。所以"不该响的时候不响"必须被测试钉住。

### BD.6 刻意保留的边界（不是遗漏）

- `/graph/node/{id}/subgraph` 返回整体 `{items:[…]}` 仍**静默降级成"无子图"**
  （该响应没有顶层数组契约，"缺 `center_node` ⇒ 无子图"是刻意的），有边界测试钉住；
- **字段级包装只上报、不拆包** —— 容错范围**不扩大**：数据仍被丢弃，只是现在看得见了；
- Toast 会合并同文案：同一端点的两个坏字段显示成一条（去重仍分别记账）；
- `useGraphSearch.ts` 的 `result.items || []` 仍未加护栏（有 `length` 短路，降级安全）；
- `loadProjects` 每次加载开始仍清 `error`（既存）；`formatSize(-5)` → `'-5 B'`（既存）。

---

## 附录 BE · 真实库垃圾清理 + 6.7 依赖扫描 + 5.13 端到端测试（2026-09-14）

### BE.1 真实库清理：删 6 行 + 改 1 个 JSON 数组，存储审计终于转绿

`audit_vault` 长期报 `ok=False {'missing_file': 4}`，指向 2 篇**测试夹具写进生产库**时代
留下的垃圾笔记（`n1`/`u1`、`n2`/`u2`，路径是测试字面量）。**后果不是 4 条报错，而是
存储审计这条红灯失去信号价值** —— 一直红着的告警等于没有告警。

流程（不可逆操作，顺序不允许临场发挥）：**先快照**（`create_snapshot`，
`_backup/20260914-122243-pre-junk-note-cleanup`，含 `_verify.json`）→ **先 dry-run**
（逐表列出将删行数，且**每条语句的 rowcount 必须先等于预测值才允许删**）→
单事务 `BEGIN IMMEDIATE` + 应用同款 pragma → 复查。

| | 前 | 后 |
|---|---|---|
| notes / knowledge_cards / review_states | 22 / 1183 / 2241 | **20 / 1181 / 2239** |
| **存储审计** | `ok=False`，`{'missing_file': 4}` | **`ok=True`，`counts={}`** |

**6 行分布在 3 张表**（notes 2 / knowledge_cards 2 / **review_states 2**），其余 22 张表
逐表枚举后零命中。快照事后复验仍含 n1/n2/c1/c2 → **可回滚**。那条既有的 FK 违规
（指向早已删除的用户，文档记为有意保留）**前后逐字节相同** —— 本次零新增违规。

它刻意**没有调用 `init_db()`**：`database.py::_migrate_sqlite()` 含三条**无条件写路径**
（`DELETE FROM note_projects WHERE orphan`、建唯一索引、`chunks.grams` 回填）——
"按应用的方式打开它"本身就会写生产库。

#### ⚠️ 一条被**实证推翻**的推断（先记教训）

我原先判断"清掉 `scope_notes` 里那个失效 id 会把目标范围**放大成全部笔记**"，
并据此收回了批准。逐**消费方**核实后，结论比我说的窄：

| 消费方 | 空 scope 的含义 |
|---|---|
| `get_goal_progress` 的 `total_count`/`reviewed_count` | 0（无兜底） |
| `get_goal_progress` 的 **`avg_mastery`** | ⚠️ **有 `else` 分支且不加笔记过滤 → 跨该用户全部卡片** |
| `generate_daily_plan` | **空计划**，不是"推荐全部" |
| `refresh_goal_progress`（Beat） | 无 `else`，保持 0 |
| 前端 `LearningGoals.tsx:378` | 渲染"0 notes" |

真实结果是"**范围为空**"这个诚实状态。**我错在只看 `if scope_notes:` 这个模式就下结论** ——
正是我一路要求代理们防的"前提没验到底"。隐患本身是真的，只是被**实证钉死为惰性**：
直接调用应用自己的 `get_goal_progress(…, "u1")` 跑真库，两个分支都得 `avg_mastery: 0.0`
（u1 名下 0 张卡片）。清理用的是**应用自己的**移除规则（`note_service.py::purge_note` 5.2），
没有自造语义。

**由此登记一个既有 bug（未修）**：`avg_mastery` 的 `else` 分支是真实的空范围语义漏洞 ——
目标范围清空后会退化成"该用户全部卡片"。今天惰性，**u1 再有卡片就会显形**。
→ ✅ **已修**（附录 BF.4，2026-09-14）：只删掉那个 `else` 分支（`if scope_notes:` 分支逐字节未动），
空范围现在报 `avg_mastery = 0.0`。它的触发条件与 u1 无关（任何用户删掉范围内最后一篇笔记都会走到），
所以没有因为 u1 已被删除而作废 —— 变异验证给出的症状是 66.67 / 100.00。

**过程教训**：这些依赖本该由**应用自己的删除路径**处理（`note_service` 里确有 5.1–5.3 三段
清理逻辑）。我为精确控制范围走了裸 SQL，代价是漏掉了那个 JSON 引用。**下次碰真实库，
优先走应用的删除路径。**

#### schema 陷阱（已写入 `backend/data/db/cleanup-notes-n1-n2.log` §9）

- `knowledge_cards.note_id` / `chunks.note_id` / `quiz_items.card_id` / `review_logs.card_id`
  都是 **ON DELETE NO ACTION** —— 删笔记**不会**带走卡片/分块/题目/日志，
  **那 4 条悬空引用就是这么活下来的**；而 `SET NULL` 的 `note_id` 列是**第二种陷阱**
  （悄悄把卡片"提升"为独立卡片）
- `review_states` **完全没有 FK**（多态 `item_type`+`item_id`）—— `foreign_key_check`
  看不见它，**6 行里有 2 行就藏在这里**（2241 = 1183 卡片 + 1058 题目，一卡一态）
- `chunks_fts` 是无触发器 external-content FTS5 表 —— 删 chunks 要补发 `'delete'`
  （需**删除前**捕获旧 `chunk_rowid` **与** `grams`）；且 `integrity-check`
  **只验结构、抓不到索引漂移**
- JSON 列必须 `json_each(<值>)`；写成 `json_each("t"."col")` **静默返回 0 行**
- id 不都是 UUID（`n1`/`c1`）—— 假设 UUID 的清理逻辑会**跳过并报成功**
- `PRAGMA foreign_keys` **默认 OFF 且按连接生效**；`init_db()` **不是只读的**
- 它**纠正了 `purge_note` 自己的注释**：注释称 `chunks.note_id` 会让 DELETE 失败，
  **实际不会**（显式删 chunks 仍对，但理由是 vault 一致性）

### BE.2 6.7 依赖扫描：34 条发现，但**头条不是 CVE 而是清单漂移**

装 `pip-audit` 2.10.1 / Trivy 0.74.0；审计对象是**项目声明的依赖**
（`requirements.txt` 与 CI 实际安装的 `requirements-test.txt`），**没有**退化成审计
整个 224 包的 conda 环境。

| 来源 | 原始输出 |
|---|---|
| `pip-audit -r requirements.txt` | 34 条 / 7 包（去重后 **19 条公告**） |
| `npm audit --json` | **1 critical / 4 high / 6 moderate = 11** |
| `trivy fs` | misconfig 4（high 2）、secret **0**；**vuln 未执行** |

**逐条定性**：可修未修（starlette ×7 需先升 fastapi；npm 11 条；Dockerfile 加固 4 条）、
**无修复**（ecdsa Minerva 上游明确不修、nltk、python-jose、transformers）、
**本仓打不到**（25 条逐条 grep 核实：全仓无 `jose.jwe`、HS256 钉死、无 `import transformers`/
`nltk`、只用 `vitest run` 从未 `--ui`、构建链发现不进 dist）、**误报 0**、
**待验证 2**（pydantic ReDoS 路径存在未构造利用；react-router 开放重定向是**唯一落在生产依赖上**的发现）。
→ 文档里**没有**出现"无高危 CVE"这类断言。

**★ 最重要的一条**：`requirements.txt` 用 `~=` 锁次版本，今天全新安装解析到
`starlette 0.46.2 / pydantic 2.0.3 / python-jose 3.3.0 / pytest 8.0.2 / fastapi 0.115.14`，
而**本机与 CI 实际跑的是** `1.6.0 / 2.13.5 / 3.5.0 / 9.1.1 / 0.141.1` —— **一条都不沾**。
也就是说这 19 条**几乎只在"真的按 requirements.txt 部署"时存在**。与
`requirements-test.txt` 文件头记录的漂移同类，只是方向相反（**声明比现实旧**）。**未修**
（版本策略是需要决策的取舍）。
→ ⚠️ **这段的结论要按附录 BF.3 修正**：`~=X.Y.Z` 展开是 `>=X.Y.Z, ==X.Y.*`
（**只有 patch 位浮动**），所以清单与现实不是"声明比现实旧"，而是**不相交** ——
19 条钉版里 **17 条的实际安装版本根本不在声明范围内**，另有 49 条传递依赖中的 48 条
其约束来自这些已越界的父包。已给出只读检查命令（`backend/scripts/check_dependency_drift.py`）；
**仍然不升级任何依赖**（BF.10）。

**Trivy 的诚实说明**：`vuln` 子扫描器**没能执行** —— trivy-db 是 OCI 制品，
`mirror.gcr.io` 与 `ghcr.io` 都**在连接层失败**（错误原文已记录）。脚本降级为
`misconfig,secret` 并**显式打印"vuln 扫描未执行"**，避免那 4 条被读成"依赖没问题"。
镜像扫描在本项目目前无对象（本机直接跑 uvicorn+vite）。

**可重跑**：`python backend/scripts/security_scan.py`（`--json-out` / `--fail-on`）。
退出码 **2 = 必需扫描器没跑起来** —— 专治本仓"配置了但从未执行"。
默认 `--fail-on none` 的理由写进文档：现在开 `high` 会长期常红 → 必然被 `|| true` 掉 →
**比没有门禁更糟**。脚本内固化三个环境坑（Windows+GBK 下 pip-audit 解码中文
requirements 会 `UnicodeDecodeError`；GBK 控制台打印会吞掉整份报告；
**registry.npmmirror.com 未实现 audit 端点返回 404** → 自动回退官方源并写进报告，
**绝不把 404 当 0 条发现**）。
⚠️ **本轮我独立复跑时退出码为 2**（pip-audit 未能运行）—— 这正是该退出码存在的意义：
它**没有**报成"0 条发现"。

### BE.3 5.13 端到端测试：Playwright 真的跑起来了

- 只装 **Chromium** rev 1243：下载 310.2 MiB / **磁盘净增 702.0 MB**
- ⚠️ `cdn.playwright.dev` **本机完全下不动**（10 分钟 0 字节）→ 改用 npmmirror CDN，
  103 秒完成。该环境变量**没有写进任何配置**（只在装浏览器时需要）
- 端口 **4319**（`--strictPort`，端口被占直接失败而不是悄悄换端口；**3080 全程未被触碰**）
- 4 个 spec / **8 通过 + 1 `fixme`**，`npm.cmd run e2e` exit 0（9.4s），
  开发服务器由 `webServer` 自启自杀，跑完无残留监听
- 用例里有一条**只有真浏览器才能验**的检查：**CSS 令牌层与模块层真的作用于元素**
  （用布局值判定，而不是拼哈希类名 —— jsdom 给不出布局）

**★ 它用 e2e 抓到一个真实产品缺陷（按硬规则未改产品代码）**：
`/` → 注册 → `/register` → 点登录 → `/login` → 输入**正确**凭据 → 登录成功、令牌落盘、
已登录外壳出现，**但 main 里是 404 页面**。原因：`App.tsx` 的已登录路由表**没有 `/login`**
（只在未登录分支注册），且登录成功后**没有任何 `navigate('/')`** → pathname 仍是 `/login`
→ 落到 `path="*"` 的 NotFound，用户必须手改地址。
保留为 **`test.fixme`（断言期望行为）**，修好后把 `fixme` 改回 `test` 即可 ——
在汇总里以 **skipped 出现，不是静默通过**。
→ ✅ **已按此修复**（附录 BF.5，2026-09-14）：两半都修（登录成功后 `navigate('/')`
+ 已登录分支给 `/login`、`/register` 加 `Navigate` 兜底），`fixme` 变回普通 `test`，
e2e 变成 **10 passed / 0 skipped**。

两个实现坑也一并记下：**不能拿 `/api/` 子串判断后端请求**（Vite 从源码路径提供模块，
`/src/api/client.ts` 也含 `/api/`，会把应用自己的 ES 模块用 JSON 桩顶掉，
现象是"登录框一直不出现"）；`**` 紧跟 `/` 是块注释结束符，通配 glob 写进 JSDoc
会提前终止注释。

**明确不覆盖**：真实后端契约、响应式/真机、可访问性、视觉回归、跨浏览器、性能、
SSE 流式渲染（`frontend/docs/e2e.md` 已写明）。

### BE.4 仍然没做的两件事（都需要决策）

1. **6.7 / 5.13 未接进 `ci.yml`**：接进去就必须同时定阈值与时长，而"**先看清家底再定阈值**"
   是本轮的顺序（文档已给出可直接粘贴的片段）。
   → ✅ **已接进**（附录 BF.6，2026-09-14）：e2e **阻断**（三步：缓存浏览器 + 只装 Chromium + `npm run e2e`）、
   安全扫描**顾问式**（`--fail-on` 故意不传，退出码 2 走"不阻断但四条路径强制可见"）。
   阈值门禁与镜像扫描仍未定（BF.10）。
2. **不升级任何依赖、不改版本策略、不加固 Dockerfile** —— 都是刻意的独立批次。
   → 附录 BF.10 维持同样三条（本轮把漂移**变可见**，但没有动任何一个版本）。

---

## 附录 BF · 真实库第二轮清理 + 依赖漂移可见化 + 两处真缺陷 + CI 接入（2026-09-14）

### BF.1 第二轮清理：测试夹具用户（5 行 / 3 表），真库只剩两个真实账号

两轮清理的现场都记在 `backend/data/db/cleanup-notes-n1-n2.log`：第一轮是 §1–§10
（附录 BE.1 已详述 schema 陷阱与"为什么长期为红的审计等于没有审计"，这里不重复），
第二轮是 §11。

| 轮次 | 删掉的东西 | 规模 |
|---|---|---|
| 第一轮（BE.1 / §1–§10） | 测试期垃圾笔记 `n1`/`n2` + 它们的卡片与复习状态，另把 1 处失效 id 从目标的 JSON 里摘掉 | 6 行 / 3 表（+1 个数组编辑） |
| **第二轮（本节 / §11）** | 夹具用户 `u1`（"a"）/ `u2`（"b"）+ 文件夹 `f1`/`f2` + 学习目标「目标1」 | **5 行 / 3 表** |
| 合计 | —— | **11 行 / 6 张表**（两轮互不重叠）+ 1 个 JSON 数组 |

第二轮删掉的 5 行：

| 表 | 行 | id |
|---|---|---|
| `learning_goals` | 1 | `35a1f6b5-dd20-4d9d-91bd-b73c74783f9f`（u1，`name=目标1`，`scope_notes=[]`） |
| `folders` | 2 | `f1`（u1）、`f2`（u2） |
| `users` | 2 | `u1`（username `a`）、`u2`（`b`）—— `hashed_password` 的字面量就是 `'x'` |

| | 前 | 后 |
|---|---|---|
| users / folders / learning_goals | 4 / 6 / 1 | **2 / 4 / 0** |
| `audit_vault` | `ok=True`，`counts={}` | `ok=True`，`counts={}`（**不变**） |
| `foreign_key_check` | `[('note_material_links', 3, 'users', 2)]` | **逐字节相同** |

真库现在只剩两个真实账号（`7775422b-…` Tian 与 `e5f1731e-…`）。其余二十余张表
逐表枚举后零命中；`chunks_fts` 与它的 shadow 表也不涉及（这次删的三张表不带 FTS 索引）；
`backend/data/storage/` 下本来就只有那两个真实账号的目录 —— 夹具用户**从没有过**
存储目录，所以这一轮**没有任何文件操作**。

那条**既有**的 FK 违规行（`note_material_links` rowid 3，指向早已删除的用户，
两端 note 均为 NULL）在**两轮清理前后逐字节相同**：它是刻意保留的悬挂引用
（附录 AS / `backup_service.inspect_snapshot`），本次**零新增违规**。
⚠️ `foreign_key_check` 不空≠这次没做干净，但它也确实永远不会因为这类"有意保留"
而变空 —— 所以判据只能是"与清理前逐字节相同"，不是"等于 0"。

**为什么第一轮不顺手把它们删掉。** 两轮之间的差别不是"多两行 SQL"，而是**爆炸半径**：
笔记是叶子，它的子行可以由我们显式枚举；`users` 是**所有者**，删它要同时回答
"它名下的文件夹、目标，以及将来任何带 `user_id` 的表怎么办"。第一轮结束时 u1
还挂着 1 个文件夹与 1 个目标（§8 的 probe），"顺手删"等于在一个**已经改过真库的**
事务里追加一类全新的引用。所以第一轮只**报告**（§8，明确标注"NOT APPROVED"），
第二轮单独批准、单独快照、单独 dry-run。

#### 第二轮把一次性 SQL 变成了可复跑的脚本

第一轮是手工 SQL（§5），第二轮落成 `backend/scripts/` 下的一组工具 —— 理由很直接：
**一次性意味着下一次还要重新想一遍顺序与谓词**，而 BF.2 恰好证明谓词是会写错的。

| 文件 | 作用 |
|---|---|
| `cleanup_fixture_users.py` | `--dry-run` / `--apply --snapshot <db> --expect <json>`；除 `u1`/`u2` 外不硬编码任何 id |
| `_dryrun_fixture_users.json` | 人工审过的**预测**：逐表、逐 id 的 rowcount |
| `_dump_rollback_sql.py` | 从**快照**重新导出那 5 行 INSERT（取快照的字节，不是手抄一遍） |
| `_verify_fixture_state.py` | 独立只读复核：25 张表的每一列，逐个 id 查"还在不在" |

探测方法是 `(表, 列)` 全枚举 + **JSON 列递归解析**（`daily_plans.recommended_tasks`
是嵌套对象，`LIKE` 扫不到），而不是猜"哪几张表可能引用 users"。任何落在预期集合之外的
命中、或任何"命中的行不是那 5 行之一"，一律**退出码 2 且不写任何东西**；
删完再跑一次 `--dry-run` 得 0 行 —— 这个动作是幂等的。

快照 `_backup/20260914-132932-pre-fixture-user-cleanup/`（含 `_verify.json`）；
`--apply` 会**先重读** `_verify.json`，`integrity != "ok"` 就直接终止 ——
"先快照"这条只有在快照真的可用时才算数。回滚走两条路：还原整库，或用
`_dump_rollback_sql.py` 从快照重新生成那 5 条 INSERT（顺序 users → folders → goals）。

### BF.2 ★ 那条 rowcount 断言真的拦下了一次半删除

第二轮的第一次 `--apply` **没有跑完**（§11.5 原文标了 ⚠）：

```
rowcount 不符：learning_goals 预期 1 实际 0     → 事务 ROLLED BACK
```

原因是把**行自己的 id** 当成了子表删除的谓词参数：`WHERE user_id IN (<目标的 uuid>)`
匹配 0 行。事后重读真库确认 `users 4 / folders 6 / learning_goals 1`，**完全未变** ——
一行都没有被删掉一半。改对谓词、重跑 dry-run、重审一遍预测，第二次 `--apply`
才按 1 / 2 / 2 逐条对上。

为什么这条规矩值得单独写一段：

- 它不是"防御性编程"，它**当场生效了一次**。脚本没有任何办法"看出"
  `IN (<一个 uuid>)` 与 `IN (<一个用户名>)` 的区别 —— 两者都是合法 SQL，都返回 0 行，
  都不报错；
- 这次拦下的是**第一条**语句，代价为零。真正危险的形态是同一类错误落在**中间某条**上：
  那时"少删了"会被读成"删完了"，事务提交、退出码 0，而库里停在**一半**；
  下一次运行再删剩下的，中间没有任何人知道该相信哪个计数；
- 所以预测必须**逐表逐 id**：总数 5 可以被"某表多删 3 行、另一表少删 3 行"满足。
  逐步比对 rowcount 是唯一能在**提交前**发现这件事的检查点。

> 这与 BE.1 是同一条：不可逆操作的每一步都要有一个"不同意就中止"的判据，
> 而不是一个"做完了再复查"的检查 —— 复查只能告诉你已经错了。

### BF.3 ★ `~=` 的语义被传递了两轮，而它是错的

`~=` 是 PEP 440 的"兼容版本"子句，只有两种形态：

| 写法 | 展开 | 浮动位 |
|---|---|---|
| `~=X.Y.Z` | `>=X.Y.Z, ==X.Y.*` | **只有 patch 位** |
| `~=X.Y` | `>=X.Y, ==X.*` | 次版本位 |

也就是说 `~=0.115.0` **不接受** 0.116，更不接受 0.141 —— 只有 patch 位能浮动。
此前两轮都沿用了一个**没有验到底的前提**：把清单与现实的差距读成"**声明比现实旧**"
（两条各自自洽的路径）。而按上面的语义算一遍就会发现，两边不是"旧一点"的关系，
而是**不相交**：19 条钉版里有 **17 条的实际安装版本根本不在声明范围内**。
这个数此前没人算过，因为它需要逐条做集合判定，而不是看几个版本号谁大谁小 ——
BE.2 给出了五个版本号的对比，方向是对的，但"声明比现实旧"这个措辞掩盖了量级。

实测（`python backend/scripts/check_dependency_drift.py`，本机解释器）：

| 项 | 数 |
|---|---|
| 声明条目 | 38（**钉版 19** / 未锁版本 19） |
| 在声明范围内 | **2**（`python-multipart` `~=0.0.9`→0.0.32、`sqlalchemy` `~=2.0.0`→2.0.52） |
| **超出声明范围** | **17 / 19** |
| 传递依赖 | 49 条，其中 **48** 条的约束来自**已经越界的父包** |

几行原文：`fastapi ~=0.115.0` → **0.141.1**；`uvicorn[standard] ~=0.30.0` → **0.52.4**；
`numpy ~=1.24.0` → **2.2.6**；`pydantic ~=2.0.0` → 2.13.5；`pytest ~=8.0.0` → 9.1.1。

**结论**：那份 `requirements.txt` 描述的是一个**从未存在过的部署形态**。
它既不是本机、也不是 CI，也不是"将来某次全新安装"—— 按它装出来的环境跑不跑得动
这套代码，没有任何人验证过；而 BE.2 的 19 条公告说的正是那个环境（本机与 CI 一条都不沾）。
"没验证过"与"验证过且没问题"在报告里长得一模一样，这就是它值得单独写一节的原因。

**处置：不升级任何依赖。** 升级会同时动 19 个直接依赖与 49 个传递依赖，
需要独立批次、独立验证与回滚方案；把它塞进"让漂移可见"这一轮，两件事都会做不干净。
本轮做的是**让它可见**（只读、不联网、不调 pip、不 import app）：

```
python backend/scripts/check_dependency_drift.py     # --json / --fail-on-drift 可选
```

退出码三态：**0** = 检查跑完了（**漂移存在与否都不影响它**）/ **1** = `--fail-on-drift`
且确有漂移 / **2** = **检查自身什么都没查到**（清单缺失、解析出 0 条、一条已安装版本
都查不到、`packaging` 不可用）。

> 退出码 2 是这里最要紧的一条：一个"永远没发现问题"的检查比没有检查更危险 ——
> 本仓库已经有过"规则写了但永不匹配""扫描器一次没跑过"的先例。
> **"没检查"必须与"没问题"在退出码上分开**，否则前者会被读成后者。

`tests/test_dependency_drift_check.py`（**9 用例**）守的全部是**反空转**，不是今天的数字：
`requirements.txt` 的**每一行声明**都必须被解析出来（用一套更笨的独立数法对账）/
CI 必装的 `requirements-test.txt` **每一条**都要查到已安装版本（否则"未安装"会被读成
"没漂移"）/ 传递依赖探测必须真的产出条目 / **比较器不能是常量**（`~=2.0.0` 收 2.0.52、
拒 2.13.5；`~=2.2` 收 2.13.5 —— 这里写错就会把漂移判反）/ 清单缺失必须退 2。
刻意**不**断言 `drift > 0`：有人同步了版本那天该庆祝，不该红。

### BF.4 一个既有语义 bug：空范围的目标会把掌握度算到范围外（已修）

BE.1 §7.3 把 `goal_service.get_goal_progress` 的 `avg_mastery` `else` 分支登记为
**既有 bug（未修）**，当时用真库证明它在 u1 名下是**惰性**的。本轮把它修掉 ——
触发条件与 u1 无关（任何用户删掉目标范围内最后一篇笔记都会走到），所以不能因为
"今天没显形"就留着。

三个聚合里 `total_count` / `reviewed_count` 都写在 `if scope_notes:` 之内，
只有 `avg_mastery` 有一个 `else`：scope 为空时按 `user_id` 聚合**该用户全部卡片**。
而"范围为空"不是假想状态 —— `note_service.py::purge_note` 步骤 5.2 就是这么干的：
把被删笔记从 `scope_notes` 里摘掉、**保留目标本身**。

变异验证（把那个 `else` 放回去，比对完逐字节还原）给出的真实症状：

| 用例 | 变异后 | 应为 |
|---|---|---|
| 空范围（用户名下有 40 / 60 / 100 三张卡，其中 100 在范围外） | `avg_mastery = 66.67` | **0.0** |
| 复现真实路径：范围内那篇笔记被删，只剩范围外的 100 分卡 | `avg_mastery = 100.00`、`progress_percentage = 100.0` | **0.0** |

第二条是这条 bug 最难看的样子：**删掉目标范围里最后一篇笔记之后，目标反过来报告
自己已经达成**。它不需要任何异常操作，只需要用户删一篇笔记。

修法是**只删掉那个 `else` 分支**：`if scope_notes:` 分支**逐字节未动**，
因此"非空范围的行为不变"是可证明的（用例同时钉住：范围指向 n1 时均值仍是 50.0、
进度 62.5%，而不是 66.67）。注释里补上"为什么空范围是空的"，并指向现场记录 §7.3。

`tests/test_goal_progress_empty_scope.py`（**3 用例**）里有一条**反空洞**断言：
先证明该用户名下**确实**有 3 张卡，否则"0.0"可能只是"库里没数据"。第三条逐字复制
`purge_note` 步骤 5.2 复现真实触发路径 —— ⚠️ 刻意**不调用 `purge_note` 本身**：
它还会删 vault 文件与版本历史，而 `test_db` fixture 只把 `DATABASE_URL` 指向临时库、
**不换 vault 目录**，在单元测试里调它等于去动真实存储。

### BF.5 `/login` 登录成功落 404：两半都修

BE.3 记录了 e2e 第一次真正跑起来时当场抓到的产品缺陷（从 `/login` 登录 → 令牌落盘 →
已登录外壳出现 → **main 里是 404**），当时按硬规则没有动产品代码，留成 `test.fixme`。
本轮修掉，**两半都修**：

| 位置 | 改动 | 修的是哪一半 |
|---|---|---|
| `pages/Login.tsx` | `await login(...)` 成功后 `navigate('/')` | "登录成功后没有任何跳转" |
| `App.tsx` | 已登录路由表给 `/login`、`/register` 各加 `<Navigate to="/" replace />` | "已登录路由表里没有这两条路径" |

**只修一半，同类 bug 会从另一个入口复发。** 只加跳转：带着已登录令牌直接访问 `/login`
（书签、历史记录、注册成功后回退一格）仍然落到 `path="*"` 的 NotFound。
只加路由兜底：地址栏与历史里会留下一条"能登录但不存在"的路径，而且登录成功后
想做的任何事都失去了挂载点。`navigate` 放在 `await` 之后的成功路径上 ——
401 仍然留在登录页显示统一文案（`catch` 分支不跳）。

**★ 反证**：把这两处修复 `stash` 掉再跑新用例，用例**真的失败** ——
`Received: "…/login"`（地址栏停在 `/login`），附件截图就是那个 404 页面。
这证明用例咬住的是**用户看到的症状**（地址栏 + 页面），而不是"源码里有 `navigate` 这句话"。

e2e：**8 passed + 1 skipped → 10 passed / 0 skipped**（4 个 spec）。两条新用例
**刻意不合并**成一句"登录后能看到仪表盘"：地址栏断言证明的是跳转那一半，
"已登录后直接访问 `/login`、`/register`"证明的是兜底那一半 —— 合并之后，
哪一半退化就再也看不出来了。BE.3 里那条 `fixme`（断言的是**期望行为**）
按原计划变回普通 `test`。

### BF.6 CI 接入：e2e 阻断，安全扫描顾问式 —— 两个决策，两种待遇

BE.4 把"6.7 / 5.13 接进 `ci.yml`"列为**需要决策**的事（阈值与时长都得先定）。
本轮接进去，两层给的待遇刻意不同：

| 层 | 位置 | 是否阻断 | 依据 |
|---|---|---|---|
| Playwright e2e | `frontend` job 末尾三步 | **阻断** | 确定性（`page.route` 把 `/api` 全桩掉，不要后端 / 数据库 / 网络）、本机实测约 10 秒、只装 Chromium（`--with-deps`）、浏览器目录按 `package-lock.json` 缓存（键随版本变，不会"缓存里是旧 revision"） |
| 依赖安全扫描 | **新增** `security-scan` job | **顾问式**（`--fail-on` 故意不传） | 见下 |

e2e 值得阻断的理由由 BF.5 自己给出：它是**唯一**跑在真浏览器里的一层，
而这轮的真缺陷在 tsc / eslint / build / Vitest 四道全绿的情况下存在了很久。
它之所以**可以**阻断：全部用例把 `/api` 桩掉、彼此不共享状态、dev server 由
`webServer` 自拉起自杀（端口 4319、`--strictPort`、`reuseExistingServer: false`），
CI 里不需要另起服务、也没有外部依赖 —— 这与"检索质量评测不放进 CI"（K.5 已记）是
同一条判据的两面：**没有真实资料/环境依赖的层才敢当门禁。**

安全扫描**不阻断**是一个决定，不是偷懒：

> 今天把 `high` 当门槛，CI 会**长期常红**（BE.2 的 34 条里，有"上游明确不修"的，
> 有只存在于 `requirements.txt` **声明路径**上的）。长期红的门禁只有一个结局 ——
> 被加一句 `|| true` 或干脆删掉，而它同时制造了"我们已经在管安全了"的假象。
> **那比没有门禁更糟。**

**退出码 2（必需扫描器没能运行）不阻断，但强制可见**，四条路径同时给：
`$GITHUB_STEP_SUMMARY` 里的一张表（退出码 / 未运行的扫描器 / 报告位置）/
`::warning::` 注解（PR 的 Checks 界面直接可见）/ JSON 报告作为构建产物归档 /
完整扫描日志。它不阻断的理由：code 2 的主因是网络与扫描器环境，**不是这次提交
引入了什么** —— 让一次 registry 抖动把 PR 变红，红灯指向的原因就不是代码。

**保留一条硬失败**：报告文件根本没生成 → `::error::` + `exit 1`。
那说明**这个 job 自己的骨架**坏了（脚本路径、工作目录，或脚本崩在写报告之前），
与扫描器能不能联网无关，必须红。`|| true` 的作用域被压到最小：只吃掉扫描脚本
自己的退出码，紧接着的那一步负责把它翻译成上面那些可见状态 —— 这一点**写在代码里**，
否则下一个人会以为可以照抄那句 `|| true`。

### BF.7 ⚠️ workflow 做过离线验证，但**不是**一次真实的 workflow 运行

没有 runner，能离线验的本轮都验了：

| 验了什么 | 怎么验的 |
|---|---|
| YAML 可解析、结构合理 | PyYAML；每个 job 有 `runs-on`、每个 step 有 `run` 或 `uses`（与 `tests/test_session_lifecycle.py::TestWorkflowYaml` 同一套判据） |
| shell 语法 | **20 个 `run:` 块逐个 `bash -n`**（Git bash 5.2.15），全部通过 —— 含带 heredoc 的那一条 |
| 摘要步骤的语义 | 用一份**真实报告**执行 `Report scanner status` 的 shell，四条路径的输出逐条核对 |

⚠️ **但它不是一次真实的 workflow 运行，本附录不声称 CI 已经跑过。** 没有 runner，
以下都还没被证明：`actions/cache` 的命中行为、`npx playwright install --with-deps chromium`
在 ubuntu 上的 apt 安装、`$GITHUB_OUTPUT` / `$GITHUB_STEP_SUMMARY` 的真实行为，
以及"第一次运行会不会因为别的原因红"。这与 BE.2 的"Trivy vuln 未执行"、
BE.2 的"镜像扫描无对象"是同一类标注：**能离线验的说清验了什么，没验的不要读成通过。**

### BF.8 两条会咬人的环境事实

**① PATH 上的默认 `python` 是 anaconda base 3.9.7，连测试都收集不了。**
`tests/conftest.py` 的 `tmp_dir: str | None = None` 是**运行时**注解（该文件没有
`from __future__ import annotations`），而 `X | None` 在 3.9 直接
`TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`（实测）。
裸解释器还有第二道门：GBK 代码页下连 `pytest.ini` 都读不干净（`UnicodeDecodeError`，实测）。
所以"必须用 3.10+"不是偏好而是硬要求。项目真正的运行时是 `mineru_env`
（**Python 3.10.21 / SQLite 3.53.4**），两轮清理用的也是它。

**② 只用 `aiforlearn` 环境会死在 `backports.asyncio.runner`。**
用户站点目录 `C:\Users\admin\AppData\Roaming\Python\Python310\site-packages` 里有一个
`pytest_asyncio` 1.4.0，它**遮蔽**了环境自带的那一份：

```
ModuleNotFoundError: No module named 'backports.asyncio.runner'
```

报错指向一个从没装过的包，真实原因却是"解释器路径上有第二个 `pytest_asyncio`"——
这是最难查的一类环境故障。加 `PYTHONNOUSERSITE=1` 后 import 走的是环境自己那份
（两份的 `__file__` 不同，实测）。⚠️ 顺带一条：`aiforlearn` 里
`test_dependency_drift_check.py` 的传递依赖用例会红（该环境缺若干传递依赖），
所以"已安装版本必须查到"那组断言要跑在装齐依赖的解释器上 —— 它红的时候说明的是环境，
不是代码。

这两条与 BE.2 的"两套版本集合"同源：**同一台机器上有多个解释器、多套依赖，
而没有任何一层在告诉你"这次跑的是哪一套"。** 依赖漂移（BF.3）是它在版本号上的形态，
这里是它在解释器上的形态。

### BF.9 验收

| 项 | 结果 |
|---|---|
| 真库第二轮 | **5 行 / 3 表**：`users 4→2`、`folders 6→4`、`learning_goals 1→0`，其余表一行未变 |
| 清理后自检 | `audit_vault` **仍 `ok=True`、`counts={}`**；`integrity_check` / `quick_check` = ok；`foreign_key_check` 与清理前**逐字节相同**（那 1 条既有违规未动），**零新增违规** |
| 残留 | 25 张表 × 每一列逐个 id 复核：夹具 id **0 命中**；`--dry-run` 复查 0 行（幂等） |
| 可回滚 | 快照 `_backup/20260914-132932-pre-fixture-user-cleanup/`（`integrity=ok`，含 `_verify.json`）+ 由快照重新导出的 5 条 INSERT |
| 依赖漂移 | `check_dependency_drift.py` 退出 **0**；**钉版 19 / 越界 17 / 传递依赖 49 中 48 条来自越界父包**；`test_dependency_drift_check.py` **9 passed** |
| 目标进度 | `test_goal_progress_empty_scope.py` **3 passed**；变异验证给出 66.67 / 100.00 两个真实症状 |
| e2e | 4 个 spec / **10 passed / 0 skipped**（`8 + 1 fixme` → 10 / 0）；反证有效（stash 掉修复即失败） |
| 后端全量 | **1037 passed / 3 skipped**（本轮新增 12 条 = 漂移 9 + 空范围 3；实测：`mineru_env` + `PYTHONNOUSERSITE=1`） |
| CI | 20 个 `run:` 块全部 `bash -n` 通过；YAML 结构与摘要 shell 离线验证；**未在 runner 上运行过**（BF.7） |
| 编码 | 新增的 `ci.yml` 段落与两个测试文件均为 UTF-8 无 BOM、LF |

### BF.10 本轮明确未做

- **不改任何依赖版本**：19 条越界条目原样保留；`requirements.txt` 只加了 7 行注释
  （说明"这份清单 ≠ 已经装好的那套"，并给出那条只读检查命令）。升级是独立批次。
- **不改版本策略**：`~=` 的写法维持原样。改成 `==`（可复现）或改成浮动区间
  （跟得上安全修复）是一次真正的取舍，需要与部署方式一起定，不能顺手改。
- **不加固 `Dockerfile`**：BE.2 的 4 条 misconfig 发现仍在（同一批"需要决策"的事）。
- **6.7 / 5.13 之外不再扩 CI**：覆盖率门槛、镜像扫描、检索质量评测都没有加 ——
  最后一项 BE 已给出不加的理由（CI 上没有真实资料库，加了 `|| true` 只是装饰）。
- **不追 6.7 的验收**："无高危 CVE"这句话本附录**没有**说，也不该说：
  今天的发现里一批是"上游明确不修"、另一批只存在于**声明路径**上（BE.2 / BF.3）。

---

## 附录 BG · 阶段 5.9：可访问性审计（axe-core + 真 Chromium）（2026-09-14）

### BG.1 这一轮只做了 5.9 的一半

计划 5.9 的验收只有四个字：**"axe 无 critical"**。而在本附录之前，全仓库
**没有任何一层**能回答这句话 —— Vitest + jsdom 没有布局引擎
（`getComputedStyle` 只回字符串），算不出颜色对比度；`npm run e2e`
那 4 个 spec 只覆盖外壳与认证入口。本轮把**能持续回答它**的工具与安全网
放到位，**不修代码**：`frontend/src/**` 正在被 5.6 的并行批次改，而审计对
**对比度与类名**恰好最敏感 —— 两边同时动手，两边的证据都会变脏。
修复是下一轮（就是随后正在进行的那个），本附录只对"审计一半"负责。

| 项 | 值 |
|---|---|
| 引擎 | `@axe-core/playwright` **4.13.0**（连带 `axe-core` 4.13.0，devDependency） |
| 规则集 | `wcag2a` / `wcag2aa` / `wcag21a` / `wcag21aa` / `best-practice` |
| 命令 | `npm run a11y`（= `playwright test --project=a11y`，**不含**功能层那 10 条） |
| 浏览器 | 仍是 e2e 用的那一个 Chromium，**额外下载 0 字节** |
| 用例 | **10 条** = 8 条扫描用例（覆盖 **10 个场景**）+ **2 条自检** |
| 并发 | `a11y` project 里固定 `workers: 4`（理由见 BG.9） |
| 实测耗时 | 约 **21~25 秒**（本机热缓存） |
| 逐条发现与完整计数 | `frontend/docs/a11y-audit.md`（本附录只记结论与判断） |

### BG.2 扫的是"有内容的真界面"

| 场景 | 界面 | 为什么是它 |
|---|---|---|
| `login` / `login-error` | 登录页初始态 / 401 失败态 | 每个用户的第一屏；失败态是**另一个渲染分支**（`role="alert"` 只在那里出现），只扫初始态等于没扫过失败态 |
| `dashboard` / `dashboard-mobile` | 已登录外壳 + 仪表盘 / 同页 375×667 | 登录后的落地页；侧边栏在**每个**已登录页面上都存在，一个场景能代表八个 |
| `notes-list` / `note-detail` | 笔记列表（2 条）/ 笔记详情（Markdown 正文 + 元信息栏） | 卡片是 `role="button"`、列表项里还有操作按钮；正文有标题层级、链接与角色下拉框 |
| `card-review-front` / `-back` | 卡片复习未翻面 / 已翻面 | 该页**没有输入控件**（靠容器接键盘），翻面前后是两个控件集合 |
| `knowledge-graph` / `projects` | 非空图谱（4 个节点）/ 项目页 | 工具栏、过滤器、侧栏统计；列表 + 新建表单 |

两件事让这些场景**不是摆拍**：

1. **夹具一律非空。** 通用桩（`support.ts` 的 `stubApi`）把所有列表接口回**空**，
   那在可访问性审计里是最坏的一种桩：页面只剩空状态，卡片、标签、图例、进度条
   这些**真正会被 axe 判定的 DOM 一个都不存在**，扫出来"0 违规" ——
   而这个 0 是"没东西可查"。所以审计另写了一份夹具，给每个被审页面喂**非空**的
   业务数据（笔记、卡片、图谱节点、项目），并把时间戳固定住，让结果不随"今天"漂移。
2. **未定义的 `/api` 一律回 501**（并记进 `A11yStubLog.unmatched`，每个场景断言它为空）。
   页面普遍用 `.catch(() => null)` 吞错误，静默兜底会把"忘了补桩"变成
   "某个区域**根本没被审计到**"却照样绿。现在它会以"页面渲染不出来"
   ＋"那条 URL 出现在输出里"的形式暴露 —— 这与 BE.2 的"0 条发现"、
   BF.3 的"未安装被读成没漂移"是同一条判据。

### BG.3 "不许空过"才是本层真正的工作量

**空白页面的 axe 结果恒为"0 违规"。** 那种绿灯比红灯更糟：它把"没扫到东西"
说成了"没问题"。所以每个场景都强制四道断言，外加两道自检：

| 防线 | 挡的是什么 |
|---|---|
| 先等该页**独有**的渲染标记出现 | 坏掉的选择器 / 失败的懒加载。等的是"标记 **+ 内容**"（列表要等到"共 2 条"**和**一条笔记标题都在），不是只等一个标题 —— **空态也有标题** |
| DOM 元素数 ≥ 场景下限（实测值留 ~30% 余量） | 骨架屏 / 半渲染的页面 |
| `passes > 0` 且评估规则数 > 20 | axe 根本没跑起来，或规则集被意外裁剪 |
| `unmatched === []`、`pageErrors === []` | 桩缺了某条接口（那块区域没被审计到）/ 页面抛了未捕获异常 |
| 自检 1：注入一个没有 `alt` 的 `<img>`，断言 axe 报出 `image-alt` | 扫描链路本身还活着。它守的是最坏的一种失败：桩、选择器或配置把页面搞空了，于是所有断言都因"没有违规"而通过 |
| 自检 2：登记表自检（场景名拼写、（场景，规则）唯一、每条都有归属） | 写错的场景名会让豁免**永远匹配不到**，而维护者会去补第二条豁免，越加越乱 |

### BG.4 判定尺度：登记表 + 上限（既不是"全绿"，也不是"全放行"）

审计的结论是"页面上确实存在违规"，于是只有三种做法，而只有一种同时保住
"能跑"与"有意义"：**关掉规则**（`disableRules`：该规则在**所有**页面上的未来
回归一起消失，用一行配置换掉一整类信号，最差）／**钉住违规总数**（修好一条
测试反而变红，维护者就会去改数字，数字一改就再也不是门禁）／
**逐条登记 + 上限**（本层采用）。

`REGISTRY` 把当前已知的每条违规连同"影响等级 + 节点数 + 归属"写下来，
运行时**五类失败都会红**：出现**未登记**的规则、已登记规则的**影响面扩大**
（命中节点变多）、**严重度上升**、登记表在（场景，规则）粒度上**不唯一**
（那种写法会让匹配悄悄失效）、以及**没有写归属**的条目
（没有归属的豁免就是"忘了修"）。

只卡上限的**代价**也要写清楚：它**不会**告诉你"某条已经被修好了" ——
那件事由 `a11y-audit.md` §3 的表格（人工过一遍）负责。这条取舍的理由是
这份文件是给**修 bug 的人**看的：修好立刻生效、不必同步改测试文件。

### BG.5 发现（一个数都没有修饰）

> ⚠️ **本节是历史基线，不是当前状态。** 下面这张表是**审计轮**（`6e8c677`）的数字，
> 此后这个数变过两次，两次都不是"把上面这些字擦掉重写"：
> **修复轮**（`a33de19`）**30 组 / 68 节点 → 4 组 / 4 节点**（见 BG.6 三处杠杆的落地；
> 剩下的 4 个节点全是待人工决定的 `heading-order`）；
> **覆盖轮**（`1ebb3a6`）**4 组 / 4 节点 → 12 组 / 15 节点** ——
> 那一次数字**变大**是因为**扫描场景从 10 个扩到 15 个**，不是回归，
> 逐条发现、实测色值、覆盖陷阱与未覆盖清单见 **BG.11**。
> 也就是说：**这一节回答"审计当场看到了什么"，BG.10 与 BG.11 回答"现在是什么状态"。**

axe 报的"违规"是**规则 × 节点**：一条规则命中 N 个元素 = 1 条规则 / N 个节点。

| 项 | 数 |
|---|---|
| 违规（规则 × 场景） | **30 组** |
| 违规节点 | **68** |
| `critical` / `serious` / `moderate` / `minor` | **2 / 39 / 21 / 6** |
| axe **明确不下结论**的节点（`incomplete`） | **149** |

> **应用不是无障碍合格的。** 计划给 5.9 写的验收是"axe 无 critical"，
> 而实测**当场就有 2 条 critical**（两个原生 `<select>` 没有可访问名）——
> 验收不成立，不是"还要再看看"。同时，**"没报出来"不等于"没问题"**：
> 那 149 个"需人工确认"**根本不是通过**，是 axe **拒绝判断**的节点
> （见 BG.7），比违规节点还多一倍。

两个计数上的细节，避免被读大或读小：

- **68 个节点不是 68 个不同的元素**：同一个元素会在多个场景里各出现一次
  （侧边栏在 **8 个**已登录场景里都存在；`login` 与 `login-error` 是同一页的
  两个状态）。按"规则 + 场景"去重后是 **30 组**，正好等于 `REGISTRY` 的条目数。
- 等级分布是**跨场景相加**的，所以同一节点在多个页面里各计一次 ——
  它是"影响面有多大"的度量，不是"有多少个坏元素"的度量。

### BG.6 最高杠杆的三处（修复轮的入口，本附录没动）

| 发现 | 一处改动覆盖多少 | 为什么排在最前 |
|---|---|---|
| **F-04 / F-05** `--color-text-tertiary`（`#9a9ab0`，白底 **2.75:1**，AA 正文需 4.5:1） | **9 个场景里 8 个命中**（窄屏收进抽屉的那个除外）—— 改一个令牌，八个页面受益 | 侧边栏三处分组标题是导航的**结构信息**，低视力用户看不出分组。⚠️ 该令牌共 16 处引用（含折叠态省略号、DiffView、Auth 页脚），改完**必须重跑本审计**确认没有连带回归 |
| **F-09 / F-17** `role="button"` 容器里嵌可聚焦子元素 | 仪表盘「今日待复习」卡 + 笔记列表的笔记卡片 | ★ **这与已经修掉的 `Sidebar.tsx` 的 `<button>` 嵌 `<button>` 是同一个洞的另一个入口**：上一轮修的是"真按钮嵌套"（`678ca90`，当时按 5.10 的移动端缺口顺手清掉），这次是"`role="button"` 容器嵌套"，症状同形（Tab 在同一个卡片里停两次、屏幕阅读器把内层按钮念成外层的一部分、外层只监听 `Enter` 不监听 Space）。**同一类洞修过一次，还会从另一个入口复发** —— 修法应统一成"控件之间是兄弟"：卡片不要 `role="button"`，标题是真链接、操作是独立按钮。F-03/F-16（`article[role="button"]` 不被 ARIA 允许）是同一处改动顺带消掉的 |
| **F-10 / F-11** 两个 `<select>` 没有可访问名（2 条 critical 的**全部**） | 各一行 `aria-label` | 图谱的类型过滤器只是筛选，而「笔记角色」下拉**会改数据**（`updateNoteRole`）—— 无名控件改动数据比只读筛选更糟。它内联样式里还有 `outline: none`，键盘焦点也看不见 |

另有一条**推断**（不是实测，已写进 `a11y-audit.md` §5）：F-19 的两个低对比度
自评档位色值来自 `utils/labels.ts`，那是**唯一数据源**，而同一个
`SelfRatingButtons` 也用在**本轮未扫**的答题复习页 —— 那里大概率同样不足。
修 `labels.ts` 两页一起受益，顺带应把 `Review` 加进扫描场景。
同理，`DailyMaterials` / `TodayLearn` / `Upload` 也有写法**逐字相同**的
`role="button"` 卡片，属于"用 grep 得到的高置信猜测"，不是审计结论。

### BG.7 axe **看不见**的（本层不产生那些结论）

下面每一条都是**当前没有任何一层在守**的（不是免责，是待办清单）：

| 看不见的 | 为什么判不了 |
|---|---|
| **Tab 顺序 / 焦点可见 / 焦点陷阱** | axe 只看 DOM 与计算样式，**不模拟 Tab 键**。已登录外壳里侧边栏在 DOM 中先于 `main`，所以每个页面都要先 Tab 过 15 个导航项才到内容 —— 这可能是问题也可能是有意，**本轮没有量过**；至少两处内联 `outline: none`（其中一处是真 `<select>`），键盘用户看不出焦点在哪；移动端抽屉与三个手写模态的焦点陷阱同样没测 |
| **屏幕阅读器语义** | 它只判"有没有名字"，不判"名字好不好"：图谱过滤器加了 `aria-label` 就算过，但那名字在朗读里清不清楚，只有戴耳机听一遍才知道。15 个导航项每个前面带 emoji（`☘ 今日学习`），会被念成什么，它不管 |
| **动态区域** | `aria-live` 的**存在性**它能判，"该不该有 / 该念什么"判不了 —— `Toast`、复习进度更新、"下次复习: 21 天后"都是异步出现的内容 |
| **渐变背景上的对比度** | `background-clip: text` + `-webkit-text-fill-color: transparent` 让前景色是**透明**的，axe 算不出背景就不猜，把节点整批丢进 `incomplete`（跨场景 **149 个**）。那不是"通过"，是"没有结论"：要人工按渐变两端分别算。**不要因为 axe 没报就当它没问题** |
| **触控目标尺寸** | ★ **axe-core 4.13 根本没有这条规则**（WCAG 2.5.8 Target Size 是 AA，但 axe 不实现几何判定）。`dashboard-mobile` 场景"0 条触控违规"是关于**工具**的事实，不是关于**界面**的事实 —— 用例把这一点写成断言（`expect(touchRules).toEqual([])`）钉住，并把导航项的真实 `boundingBox()` 存进 JSON 附件。`sidebar-collapse-btn` 28×28 这类疑点只能靠几何断言，**不能等 axe** |

（缩放/重排 200%、`prefers-reduced-motion`、跨浏览器与 NVDA/VoiceOver 实测
同样不在本层范围，完整清单见 `a11y-audit.md` §4.5。）

### BG.8 CI：顾问式（advisory），但**不是装饰**

`frontend` job 里在 `npm run e2e` **之后**加了一步（**只加了一步**，
没有改动任何现有 job 的结构）：

```yaml
- name: Playwright a11y audit (axe-core, advisory)
  continue-on-error: true
  timeout-minutes: 10
  run: npm run a11y
```

**它真的会失败。** 这是它与 `|| true` 那种装饰之间的分界线：BG.4 里那五类
判定都会让这一步**执行失败**（未登记的违规、影响面扩大、严重度上升、
登记表重复、条目无归属）。`continue-on-error` 只决定"是否让整个 job 变红"，
**不影响它跑、也不影响它报** —— CI 日志里能明确看到"这次提交有没有引入
新的无障碍回归"，也就是说它今天已经在**产生信号**，只是还没有**否决权**。

**为什么今天不阻断。** 审计当场就报出 30 组真实违规，修复在下一轮；
现在设成阻断，CI 会**长期常红**，而长期红的门禁只有一个结局：
被人加一句 `|| true` 或干脆删掉 —— 它同时制造"我们已经在管无障碍了"的假象。
**那比没有门禁更糟**（与 `security-scan` job 的取舍是同一条理由）。
第二个理由是并行性：`frontend/src/**` 正在做 CSS 迁移，审计对**对比度**与
**类名**最敏感，迁移期间的红指向的不是"这次提交引入了缺陷"。

**升级成阻断的前置条件已经写进 job 注释**（不是"以后再说"）：
① 5.9 修复轮把 `REGISTRY` 清空或降到可接受集合；② `frontend/src/**` 的
CSS 迁移结束。两条都满足后把 `continue-on-error` 改成 `false` 即可 ——
**门槛本身已经在测试里了**，不需要另写脚本。

### BG.9 三条会咬人的 harness 事实（不是产品问题，但会浪费一整个下午）

1. **审计并发压到 4，不是 8。** 8 个 worker 同时打 Vite dev server 时，
   **按需转换**（页面全是 `React.lazy`，每个路由第一次访问才现场编译）
   把首屏拖到 10 秒以上，于是"等欢迎标题出现"超时 ——
   **失败信息指向"页面没渲染"，真实原因是"开发服务器吞吐"**。
   定位它靠的是夹具自己的 `served` 日志：所有接口都回了 200，
   于是问题既不在接口、也不在前端代码。审计要的是**页面内容**，不是吞吐；
   把并发压到 4 比放宽超时更接近问题本身（实测稳定在 ~21 秒）。
2. **命令行传文件名 ∩ `testMatch` = `No tests found`。** `playwright test a11y.spec.ts`
   会与顶层 `testMatch`（已排除 a11y）**取交集**，得到的是"没有用例"，
   而不是"跑错了一批" —— 这个坑接线时真踩到了一次。用 project 表达
   "这一组用例有自己的匹配式与参数"才是配置该说的话（`npm run a11y`
   → `--project=a11y`）；同理 `npm run e2e` 加了 `--project=chromium`，
   不加时两个 project 会一起跑。
3. **固定端口 + 并发 = 第二种假阴性。** 端口固定为 4319 且 `--strictPort` 时，
   **第二个并发运行必然失败**，而输出只有一行
   `http://127.0.0.1:4319 is already used`、**连测试汇总行都没有**
   （Playwright 在起 `webServer` 那一步就退出了）—— 看起来像"这次跑挂了"、
   像产品回归，真实原因与代码无关。现在端口可用 `E2E_PORT` 覆盖
   （默认 4319 不变），并且 **3080 被写死在禁止列表里**：
   配到人类手上的 DSH Web GUI 端口会**直接抛错拒绝启动**，而不是去占它。
   约定与实测见 `frontend/docs/e2e.md` §1.1。

> 三条是同一类：**失败信息指向的地方不是原因所在。**
> 这与 BE.2 的"退出码 2 ≠ 0 条发现"、BF.3 的"没检查 ≠ 没问题"是同一条判据 ——
> 报告必须能把"没有信号"和"信号是坏的"分开。

### BG.10 验收

| 项 | 结果 |
|---|---|
| `npm run a11y` | **10 passed**（审计落盘时，约 21~24 秒），退出 0 |
| 场景覆盖 | 10 个场景（登录页正/反两面、卡片复习正/反两面各扫一次，仪表盘另有 375×667 一档） |
| 违规 | **30 组 / 68 节点**；critical 2、serious 39、moderate 21、minor 6；另 **149 个 `incomplete`** |
| 5.9 的原验收（"axe 无 critical"） | ❌ **不成立**：实测 2 条 critical |
| 修复 | **本附录不做**（修复轮随后进行，改动落在 `frontend/src/**`） |
| CI | 已接入 `frontend` job，**顾问式**（`continue-on-error: true`）；⚠️ 与 BF.7 同一标注：**没在 runner 上跑过** |
| 编码 | `e2e/a11y.spec.ts` / `a11y-fixtures.ts` / `docs/a11y-audit.md` 实测均为 UTF-8 无 BOM、LF |

⚠️ **重跑的前提**：修复轮**改到一半时不要重跑**。`frontend/src/**` 带着未提交
改动时，登记表会当场报出**未登记**的 `heading-order`（`notes-list` 的
`article > div > h3`、`card-review-front` 的 `CardFace.tsx` h3 —— 两处都是
"新加的 h1 直接跳到 h3"）。这正是登记表该有的行为：它报的是"出现了新违规"，
**不是"审计坏了"**；但那一刻的红不能当作 5.9 的验收结论，也不能反过来
把它读成"审计不可靠"。审计落盘时（`6e8c677`）的工作区是 **10 passed**。

### BG.11 覆盖轮（`1ebb3a6`）：10 个场景 → 15 个场景，数字从 4 / 4 涨到 12 / 15

BG.10 之后这个工具又跑了一轮，**只扩覆盖、不改应用代码**（`frontend/src/**` 仍被
并行的 CSS 迁移占着）。这一节是那一轮的实测与判断；BG.5 那张 30 / 68 的表是
**审计轮**的基线，两者不是同一个状态的两次描述（见 BG.5 顶部那条修正）。

**代号先对上**：BG.1–BG.10 里的"修复轮"是 5.9 的后一半（`a33de19`，68 → 4），
"覆盖轮"是**修复轮之后的那一轮**（`1ebb3a6`，4 → 12）—— 也就是本节。

#### BG.11.1 这一轮只加了"看得见"，没有加"修好了"

| 做 | 不做 |
|---|---|
| 把**从没被渲染过、因此没有任何门禁能判**的页面与状态加进扫描场景（10 → **15 个场景**） | **不改 `frontend/src/**`** —— 并行的 CSS 迁移正在改那些文件，这时改应用代码**无法被验证**（既可能修好也可能改坏而没有信号） |
| 每个新场景喂**非空**的业务数据，ready 标记必须是"只有真实内容渲染出来才成立"的东西 | **不改判定尺度**（BG.4 那五类判定一个字没动） |
| 新出现的违规**逐条登记 + 写明归属**，上限 = 实测值（不留余量） | **不放宽任何既有登记项**：F-07 / F-22 / F-08 / F-20 的上限仍然全是 **1** |

`npm run a11y`：**15 passed**（本机复核 **33.0 秒**，热缓存；用例 10 → 15 条 =
13 条扫描用例 + 2 条自检，扫描用例数少于场景数是因为登录页、卡片复习正反面
各在一个用例里扫两次 —— 与 BG.1 的记账方式一致）。

#### BG.11.2 新增的 5 个场景（以及"为什么它们不是摆拍"）

| 场景 | 页面 | 渲染什么真实内容（桩从哪来） | 实测 DOM / 下限 |
|---|---|---|---|
| `notes-status-processing` | `/notes` | **覆盖** `/api/notes`：2 条状态为 `converting` / `cleaning` 的笔记 —— 默认桩里**只有** `cleaned` / `converted`，所以这两个类名**从来没被渲染过** | 145 / 100 |
| `daily-materials` | `/daily` | `/api/folders` 从 `[]` 改成 1 个文件夹 + 新增 `/api/folders/folder-1`（3 条笔记，含两种处理中状态）；展开是**一次真实点击** | 157 / 110 |
| `today-learn` | `/today` | **覆盖** `/api/goals/daily-plan`（400 → 3 类任务）与 `/api/report/weak-points`（**契约形状**，见 BG.11.6） | 171 / 120 |
| `upload` | `/upload` | 无需新桩（`/api/projects` 已非空） | 128 / 90 |
| `review` | `/review` | **新增** `/api/review/due`（此前**没有桩** → 页面停在错误分支，扫的根本不是这一页），2 道题 | 126 / 85 |

三道**与 BG.2/BG.3 同源、但这一轮才用上新页面**的防线：

1. **每页独有的 ready 标记**，等的是"标记 **+ 内容**"（`upload` 等的是项目标签
   「蓄电池基础」与「云端解析」，不是那个每页都有的 h1）；
2. **DOM 元素数 ≥ 场景下限**（下限比实测值低约 20~30%：145/100、157/110、171/120、
   128/90、126/85）；
3. **`unmatched === []` 在新场景里照样成立** —— 页面请求的每个 `/api` 路径都有
   明确响应，**没有靠"静默空响应"混过去**。这一条是覆盖轮最容易被省掉的断言：
   少一个桩，场景照样能渲染出一半页面、照样绿，只是那一半**没被审计到**。

桩本身补了 3 处（都在 `e2e/a11y-fixtures.ts`）：`/api/review/due`、`/api/folders` +
`/api/folders/folder-1`、以及三个**只在某场景生效的覆盖导出**
（`PROCESSING_NOTES` / `DAILY_PLAN` / `WEAK_POINTS`，经 `installA11yStubs(page, overrides)`
传入）。**用覆盖而不是改默认值**：改默认值会移动已扫场景的基线
（默认 `/api/notes` 仍是 2 条，`notes-list` 的「共 2 条」断言因此原样成立）。

#### BG.11.3 数字：4 / 4 → **12 组 / 15 个节点**，而**旧场景一个都没变**

| 场景 | 违规规则 / 节点 | 与新发现的关系 |
|---|---|---|
| 10 个旧场景（BG.2 那张表） | **合计 4 / 4**（`dashboard` 1、`dashboard-mobile` 1、`note-detail` 1、`projects` 1） | **与修复轮逐字相同**；其中 6 个场景仍是 0 / 0 |
| `notes-status-processing`（新） | 1 / 2 | F-28 |
| `daily-materials`（新） | 3 / 4 | F-29、F-30、F-31 |
| `today-learn`（新） | 2 / 3 | F-34、F-35 |
| `upload`（新） | **0 / 0** | —— |
| `review`（新） | 2 / 2 | F-32、F-33 |
| **合计** | **12 组 / 15 个节点** | 11 个新节点全在 4 个新场景上 |

> ★ **这个数字变大，是这一轮的目的，不是它的代价。**
> 12 比 4 大，唯一的原因是**多了 4 个能报违规的场景**：同一批代码在 10 个旧场景里
> 仍然只报 4 个节点（零容忍照旧生效），`upload` 更是 0 / 0。
> **把它读成"无障碍变差了"就完全读反了** —— 变差的是**我们对自己代码的了解**。
> 判据很直接：**15 个违规节点里有 11 个落在扩进来的 4 个新场景上，
> 只有 4 个在旧场景，而旧场景的违规数一个都没动**；如果这是回归，旧场景不可能原地不动。
> 这也正是 BG.3 那条"空白页面的 axe 结果恒为 0 违规"的另一面：
> **没扫过的页面不是"没问题"，是"没有数"**。

`incomplete`（axe 明确不下结论的节点）从 **149 → 224**，同样是"多渲染了 4 个页面"
的算术结果，**不是通过**（语义见 BG.7；新增场景里 `upload` 那 15 个
"需人工确认"有一部分就是拖拽区的半透明背景）。

#### BG.11.4 8 条新发现，逐条都是**债**（登记是例外，删条目才回到零容忍）

| id | 规则 | 场景 | 上限 = 实测 | 实测数字 / 形状 | 下一轮怎么清 |
|---|---|---|---|---|---|
| **F-28** | `color-contrast` | `notes-status-processing` | 2 | `--color-warning` `#c4860a` 压白底 = **3.11:1**（需 4.5:1），12.8px/400 | **改一个令牌** —— 与 F-29 一起消 |
| **F-29** | `color-contrast` | `daily-materials` | 2 | 同一处缺陷的**第二条渲染路径**：`.status-converting` / `.status-cleaning` 在这里再报 2 个节点，色值/比值**逐字相同** | 随 F-28 一起消（两条分开登记的原因见 BG.4：粒度是（场景，规则），合并会让某个场景的上限失去约束） |
| **F-30** | `nested-interactive` | `daily-materials` | 1 | 文件夹头 `div[role="button"][tabindex="0"]` 里**嵌真按钮**（重命名/删除文件夹） | 照 F-09 / F-17 的形状：外层去 `role`/`tabIndex`，折叠交给真有名字的按钮 |
| **F-31** | `heading-order` | `daily-materials` | 1 | h1「今日资料」 → 文件夹名 h3，中间没有 h2 | ⚠️ **需要人工设计决定**（与 F-07 / F-20 同类），不是 5.9 单独能清 |
| **F-32** | `page-has-heading-one` | `review` | 1 | 整页 `<h1>`~`<h6>` **数量为 0**（`ReviewProgress` 在这里走"一行式"，连那个 h1 都没有） | 补 h1（**先决定标题叫什么**；注意 h1 → h3 跳级的坑） |
| **F-33** | `color-contrast` | `review` | 1 | 白字压 `#c9a959` = **2.25:1** —— **与已修的 F-19 是同一个色值** | 把 `utils/labels.ts` 里"白字压色块"的取值**一次过一遍**，见下 |
| **F-34** | `nested-interactive` | `today-learn` | 1 | 「待复习任务」卡 `div[role="button"][tabIndex=0]` 里嵌真 `<button>开始复习</button>` | 与 F-30 同一处修法 |
| **F-35** | `color-contrast` | `today-learn` | 2 | `#f44336` 压 12.5% 同色底（合成 = `#fee8e6`）= **3.13:1**，12px/400 | 改那个字面量 + **把默认桩的字段名改对**，否则仪表盘那份永远看不见（见 BG.11.6） |

> ★ **两条最该被记住的，都不是"又发现了一个 bug"，而是"上一次的修法漏在哪"。**
> **F-30 / F-34 与已修的 F-09 / F-17 逐字相同** —— 同一个洞的**第三个和第四个入口**
> （F-09 修的是仪表盘卡片、F-17 是笔记列表卡片，本轮在今日资料页与今日学习页
> 又各找到一处，连"外层只监听 `Enter`、不监听 `Space`"的毛病都一样）。
> ⚠️ BG.6 当时写的是"同一类洞修过一次，还会从另一个入口复发" —— 这一轮把
> "另一个"变成了"另两个"，而且是在**加场景的当天**就撞上的，不是过了很久。
> 修法已经跑通（控件之间是兄弟），缺的从来不是修法，是**覆盖**：
> **一次修复不配一次覆盖扩张，就等于把"这个洞修好了"误读成"这类洞没有了"。**
>
> **F-33 是同一个教训的颜色版，而且更难自欺**：F-19 当初只把 `selfRatingOptions`
> 里的金 `#c9a959` 压深成 `#8f7020`（4.66:1），**修法是对的、验证也是真的**，
> 但它只覆盖了 `utils/labels.ts` 里的**一张表**；另外 **5 张颜色表
> （`difficultyColors` / `cardTypeColors` / `questionTypeColors` /
> `cardCategoryColors` / `verdictLabels`）仍然写着这批旧值**，
> 本轮在答题复习页实测到的 `difficultyColors.medium` = `#c9a959` **就是 F-19 的原始值**。
> **一处局部修复屏蔽了一个系统性缺陷**：修完那一处，审计变绿、数字变好，
> 而"同一批色值还在别处"这件事**在任何报表上都不显示** ——
> 这次能看见它，唯一的原因是**多扫了一个页面**（`review`）。
> 其余 4 处**至今仍未实测**，要靠 `KnowledgeCards` / `QA` / `QuickReview` 那些页面。

#### BG.11.5 ★ axe **结构上判不了**：单字符文本的对比度（本节最可迁移的一条）

这一条比 BG.7 那张"axe 看不见什么"的清单更具体：**不是"工具不擅长"，是"工具按设计不判"**。

| 项 | 内容 |
|---|---|
| 位置 | axe-core 4.13.0 的 `colorContrastEvaluate`（`axe.js`：`var shortTextContent = visibleText.length === 1`） |
| 行为 | 单字符文本**且对比度不足**时，`missing = 'shortTextContent'` → 该节点**进 `incomplete`**、`evaluate` 返回 `undefined` → **永远不会变成 violation** |
| 实测例子 | `today-learn` 的优先级徽章 高/中/低 = **白字**压 `#f44336` / `#ff9800` / `#10b981` → **3.68:1 / 2.16:1 / 2.54:1**（11.2px/400，需 4.5:1）—— **三个全部不达标，三个全部不可达** |
| 后果 | `REGISTRY` 对它**无能为力**：没有 violation 就没有可登记的条目，`nodes: 1` 这种上限也写不出来。**这一条门禁守不住**，只有 `measureContrast` 的实测能给出信号 |

> **这就是"没报出来 ≠ 没问题"的具体机制**，比一句泛泛的免责声明有用得多：
> 那三个徽章不是"axe 觉得还行"，是 **axe 明确拒绝判断**；
> 而它们恰好是"中/低优先级"这类**会影响用户先做哪件事**的状态信息。
> ★ **由此定下一条下一轮的硬约束：F-30/F-33/F-35 那批颜色修复，必须与
> `measureContrast` 的断言一起交付** —— 否则修完"3.68 → 4.6"这件事**没有任何一层能验证**，
> 修好与没修在报告上长得一模一样。
> ⚠️ 同时注意 `measureContrast` 现在的写法**刻意不断言"合格/不合格"**
> （只断言"量到了"）：把"现在是 3.11:1"写成断言，等于给下一轮修好它的人埋一个必红的测试。
> 要断言的是**阈值**（≥ 4.5:1），不是**当前值**。

#### BG.11.6 一个覆盖陷阱：桩的字段名写错，页面不崩，只是**没被判过**

| 项 | 内容 |
|---|---|
| 陷阱 | 默认桩的 `/api/report/weak-points` 返回 `title` / `review_count`，而 `api/report.ts` 的 `WeakPoint` 契约要的是 `card_title` / `error_count` |
| 症状 | 页面**照常渲染**：文字是 `undefined`、徽章是**空的** —— **空文本没有对比度可判**，于是 axe 对那一块**无话可说** |
| 后果 | `dashboard` / `dashboard-mobile` 里的「薄弱点」卡**看起来被扫过了、实际没有被判过**；F-35 那处**字面相同**的红色徽章（`Dashboard.tsx`）因此完全不可见 |
| 处置 | 本轮**不改共享桩** —— 改默认值会同时移动 `dashboard` / `dashboard-mobile` 两个**已扫场景**的基线与计数；只在 `today-learn` 里用**契约形状**的覆盖，让同一张卡片**至少在一处**有真内容 |
| 归属 | **下一轮把默认桩一起改对**，否则仪表盘那条永远看不见（已进 F-35 的 `reason`） |

> **这个陷阱的可怕之处在于它不产生失败**：没有报错、没有白屏、没有断言变红，
> 只是**判定范围悄悄缩小了一块**。它与 BE.2 的"退出码 2 ≠ 0 条发现"、
> BF.3 的"没检查 ≠ 没问题"、BG.2 的"未定义 `/api` 回 501"是同一条判据 ——
> **必须能把"没有信号"和"信号是坏的"分开**。
> 而本轮选择**不动共享桩**同样是这条判据的一部分：**顺手修好它会让两个已扫场景的
> 历史基线无法对照** —— 先把信号拿到、把债务写明，比一次"顺手"更值。

#### BG.11.7 这一轮之后**仍然没有覆盖**的（照抄，别丢）

`QA`、`KnowledgeCards`、`CardDetail`（**需要先补契约形状的桩**，否则是假覆盖，
见 BG.11.6）、`LearningAssessment`、`LearningGoals`、`Trash`、`QuickReview`、
`Register`、404、`QuestionSets`。

⚠️ 其中一条是**推断，不是实测**：`QuickReview` 与 `QA` 共用 `QuizAnswerCard`，
所以 **F-33 那批旧色值大概率也在那里**（与 BG.6 末尾那条关于 `SelfRatingButtons`
的推断同一性质）。**推断不能写进登记表** —— 登记项的 `scene` 必须是真扫过的场景，
否则就是"凭猜测放行"。要把它变成事实，只有一条路：把那个页面加进场景。

#### BG.11.8 与 CI 的关系：升级条件里多了一条"覆盖"

BG.8 给阻断化写的两条前置条件是 ① `REGISTRY` 清空或降到可接受集合、② CSS 迁移结束。
本轮之后要把 ① 读得更细一点：**"降到可接受集合"必须发生在覆盖稳定之后** ——
否则会出现一种自欺的循环：**每扩一次覆盖就多几条债，然后靠"债还没还完"永远不阻断**。
所以顺序应该是：**先把要覆盖的页面覆盖完 → 再逐条清债 → 再阻断**。
在那之前，`continue-on-error: true` 的顾问式接入（BG.8）仍然是对的：
它今天已经在报告"这次提交有没有引入新的无障碍回归"（F-28…F-35 就是这一轮
由同一条命令报出来的），只是还没有否决权。

🎯 **一句话**：这一轮**没有修好任何东西，也没有弄坏任何东西**；
它做的是把 **4 个数字变成 15 个数字，并且让其中 11 个第一次有主** ——
**能涨的数字才有可能归零，看不见的缺陷永远不会被修。**

---

## 附录 BH · 阶段 5.1 第一步：从 OpenAPI 生成类型与客户端（**只生成与对比，未切换**）（2026-09-14）

### BH.1 这一轮只做了 5.1 的**前半**：把"契约漂移"从感觉变成数字

5.1 的验收是"前后端契约不可能漂移"。本轮先把**能持续回答这句话的工具**做出来，
**一行调用方都不改**：

| 产物 | 规模 | 为什么是它 |
|---|---|---|
| `backend/openapi.json` | 102 路径 / 118 操作 / 156 组件 schema / 356 080 字节 | `dump_openapi.py` 在**进程内**调 `app.openapi()` 落盘（不起服务、不连库、不占端口）。本项目的子路由挂在自定义 `_IncludedRouter` 上，`app.routes` 只有 **6 项、一条业务路由都没有** —— 路由枚举只有这一条路可用 |
| `frontend/src/api/generated/schema.ts` | 261 409 字节 / 10 000 行 | `openapi-typescript@7.13.0`（devDependency）生成，**纯类型**、构建时被完全擦除 |
| `frontend/src/api/generated/openapi-drift.mjs` | 一个 Node CLI | 用 **TypeScript 编译器 API**（不是正则）回答"端点在不在、类型对不对、错在哪"；不参与打包，但要过 lint |

**确定性也是验收的一部分**：`dump_openapi.py` 用 `sort_keys=True`、无时间戳、
`newline=""`（避免 Windows 把 `\n` 翻成 `\r\n`），连续三次生成 sha256 完全相同
（`e21c85952c30…`）；`npm run gen:api` 重复执行产物 hash 不变。

> 为什么先做这个而不是直接切：**生成类型只是"另一方的说法"**。没有机械对照，
> "切换"就只是把一份没人读过的报告换成另一份 —— 而本项目已经吃过一次亏（BH.10）。

### BH.2 ★ 修正：手写 API 函数是 **111 个**，不是计划里写的 ~90

计划的**四处**都写着"手写 90 个 API 函数"（§4.2 目标技术栈、阶段 5 的 5.1 行、
第八部分放弃清单第 10 项、附录 AO.2 的 `tasks.ts` 说明）。**这个数字是错的**，实测口径：

| 口径 | 数 | 怎么数的 |
|---|---|---|
| 本轮判定对象：**发起请求的导出函数** | **111** | AST 找出所有 `export function` 且函数体里调了 `request<T>(字面量路径)` / `uploadRequest` / `authorizedFetch` 的函数 |
| 其中不同的 `方法 + 路径` | 110 | `uploadFile` 与 `uploadFileToFolder` 都打 `POST /api/upload` |
| `src/api/*.ts` 的**全部**导出函数 | 121 | 121 − 111 = 10 个非端点导出：`client.ts` 的 9 个（`getToken`/`setTokens`/`clearTokens`/`notifyTokenExpired`/`refreshSession`/`authorizedFetch`/`request`/`uploadRequest`/`getRefreshToken`）+ `tasks.ts` 的 `isTerminal` |

四处已**就地改成 111 并标注日期**（§4.2 / 5.1 / 第八部分第 10 项 / AO.2）——
不是悄悄重写：本文件的规矩是"改了哪个数、什么时候改的、依据在哪"留在原处。

### BH.3 计划里"330 行、90 个函数的 `client.ts`"是**拆分之前**的描述

`client.ts` 现在是 **490 行**，并且**只剩 1 个端点函数**（`askQuestionStream`，SSE）：
其余 **110 个早已按域拆走**，文件尾是 11 行 `export *`（`auth` / `notes` / `upload` /
`cleaning` / `qa` / `review` / `report` / `assessment` / `graph` / `projects` / `goals`；
`knowledge.ts` / `tasks.ts` 由页面直接按域引入）。`client.ts` 自己只剩下：token 存取、
401 → 刷新一次 → 重放一次（含单飞）、超时、`Content-Type` 合并、204 / 空响应体处理、
`export *` 转发。

> 这条与 BH.2 是同一个毛病的两种形态：**"`client.ts` 很臃肿"曾经是事实，拆完之后它
> 变成了化石**（会让读者去做已经做完的事）；而**"90 个函数"从来就不是事实**
> （会让读者按错误的工作量估工）。附录 AO.5 记过一次同类教训 ——
> 照着过时的表格排期，就会去拆已经拆完的东西。

### BH.4 ★★ 真正的阻塞项在**后端**：22 个端点没有响应模型

**这是本轮唯一一条能决定"能不能切"的发现，而计划里从来没有提到过它。**

- schema 里 **23 个操作的 2xx 响应是空 schema `{}`**（本轮独立复核：
  `"application/json": {"schema": {}}`）—— 其中 `/health` 与
  `GET /api/notes/{id}/video` **没有任何前端调用方**，前端实际调的 111 个端点里命中 **21 个**；
- 另有 **1 个**端点的响应是 `additionalProperties: true` 的空壳
  （`knowledge.ts suggestSemanticRelations`）；
- 21 + 1 = **22 个**：它们的生成类型是 `unknown` / `{[k: string]: unknown}`，
  **切换等于拿 `unknown` 换掉手写类型，净亏**。

分布（22 个一个不漏）：

| 模块 | 数 | 端点 |
|---|---|---|
| `graph.ts` | **9** | `suggestRelations` / `confirmRelation` / `rejectRelation` / `createRelation` / `deleteRelation` / `getGraphStats` / `getNodeSubgraph` / `batchConfirmRelations` / `batchRejectRelations`（`getGraphData` / `getSuggestions` / `searchGraphNodes` 三个不在内） |
| `projects.ts` | 4 | `deleteFolder` / `deleteProject` / `addNotesToProject` / `removeNoteFromProject` |
| `notes.ts` | 4 | `deleteAnnotation` / `updateNoteLinks` / `getVersion` / `askNoteQuestionStream`（SSE） |
| `knowledge.ts` | 2 | `generateExtensionQuestions`（空 `{}`）+ `suggestSemanticRelations`（`additionalProperties` 空壳） |
| `upload.ts` | 1 | `prepareUpload` |
| `qa.ts` | 1 | `getCardDuplicates` |
| `client.ts` | 1 | `askQuestionStream`（SSE，本来就没有 JSON 模型） |

> ★ **这一条把 5.1 的工作量从"前端"挪到了"后端"。** 计划把 5.1 写成一项纯前端任务
> （"前端类型：手写 → 从 OpenAPI 生成"），而实测的阻塞是**后端每个路由要补
> `response_model=`** —— 前端既做不了这件事，也等不到它。**22 个端点补齐之前，
> 切换一步都动不了**（生成与对比不受影响，顺序见 BH.11）。
> ⚠️ 还要注意这 22 个里 **`graph.ts` 占了 9 个**：图谱是判定最差的一域，
> 而它恰好排在 S3 的中段（**必须等 P1**）。

### BH.5 4 处"外层有模型、内层是空壳"：**手写类型最值钱的地方，生成够不到**

编译器报 `Index signature for type 'string' is missing`（TS 的 `interface` **不会**
自动获得隐式索引签名，所以手写类型赋不给 `{[k: string]: unknown}`）：

| 位置 | schema 写了什么 | 前端按什么解析 |
|---|---|---|
| `AssessmentResponse.scores` | `{type: object, additionalProperties: true}` | `{covered_points, uncovered_points, coverage_score, completeness_score, depth_score, clarity_score}` |
| `AssessmentResponse.quiz_questions[]` | 同上 | `{index, question, key_points}` |
| `SubmitAnswerResponse.grading_detail` | 同上 `\| null` | `{verdict, missing_points, misconceptions, confidence, reason}` |
| `LinkListResponse.linked_materials[]` / `linked_personal_notes[]` | 同上 | `{id, title, source_type}` / `{id, title}` |

> 这 4 处（5 个字段）**恰好是 5.1 想消灭的那类风险所在**：字段全靠前端手写维护、
> 后端改字段前端不会知道 —— 但**生成解决不了它**，只有后端补模型才能解决。
> ★ **收益最大的地方，正是这一轮工具够不到的地方**，所以 P2 不是"顺手"，是收益项。

### BH.6 111 个函数的机械判定：只有 19 个两边一致

```
handWrittenFunctions     111     手写 API 函数
distinctEndpoints        110     去重后的 方法+路径
pathMatched              111     路径+方法在 schema 中命中
pathMissing                0     ← 0 个缺失
methodMissing              0     ← 0 个方法错
```

| 判定 | 数 | 含义 |
|---|---|---|
| `IDENTICAL` | 19 | 两边等价 |
| `HW_NARROWER` | 28 | 前端类型更窄（假设更强：后端可能不给它以为一定有的东西） |
| `HW_WIDER` | 4 | 前端类型更宽（唯一一类"前端接受后端保证不了的值"，见 BH.7a） |
| `CONFLICT` | 34 | **两边都不相容 —— 其中必有一方描述错了现实** |
| `SCHEMA_UNTYPED` | 21 | schema 没写响应模型（BH.4） |
| `SCHEMA_LOOSE` | 1 | schema 是 `additionalProperties` 空壳（BH.4） |
| `NO_JSON_RESPONSE` | 4 | 204，本来就没有响应体 |

判定全部来自编译器（每个函数生成一份探针 TS，让编译器回答**双向可赋值性**），
结构化 diff 只用于解释 —— 两者的关系是"编译器说不对"＋"差异在这里"，
**不是互相佐证**。逐函数对照表（111 行）、判定口径与逐条诊断见
**`frontend/docs/openapi-client.md` §6**（本附录不复制）。

### BH.7 三类具体漂移，每一类都说清"为什么"

**(a) 枚举：后端 10 个，前端只建模了 3 个。** 未建模的 7 个：`NoteStatus`
（**一个就影响 21 个字段处**）/ `SourceType` / `CardType` / `RelationType` /
`RelationStatus` / `DifficultyLevel` / `QuestionType`。**这 4 个函数因此被判 `HW_WIDER`**：
`cleaning.ts` 的 `startCleaning` / `stopCleaning`、`graph.ts` 的 `searchGraphNodes`、
`qa.ts` 的 `startUnderstanding`。
⚠️ 顺带查出**两处注释已经漏了**（不是类型问题，是文档问题）：`client.ts` 的状态流转
只写了 7 个值（`NoteStatus` 实际 **10** 个，另有 `cleaning_failed` / `learning_failed` /
`failed`），来源类型只写了 7 个（实际 **8** 个，多一个 `markdown`）——
**前端类型是 `string`，所以"注释漏了 3 个状态"这件事没有任何机制能发现**。
✅ **枚举值本身写错的 0 处**（已建模的 3 个与后端逐值一致）—— 全是"后端是枚举、
前端是 `string`"。

**(b) 可空性：9 处 schema 允许 `null`、前端不接受。** 两处会当场炸：

| 位置 | 后果 |
|---|---|
| `getSuggestions[].similarity_score` | schema `number \| null`，前端 `number` —— **排序/展示会拿到 `null`** |
| `compareAssessment` 的 `.quiz_questions` / `.quiz_answers` | schema 允许 `null`，前端 `?:` 只在"没有"时跳过，**收到 `null` 时 `.map()` 抛错** |
| `getNote.video_url` / `getProjectDetail.notes[].source_type` | 前者前端漏了 `null`，后者笔记卡片徽章会拿到 `null` |

**(c) ★ 一处真错：一个前端类型描述了两个后端模型。**

```
projects.ts getProjectDetail
  EXTRA_REQUIRED_FIELD @ getProjectDetail.notes[].file_size — 前端类型有、schema 没有
```

- `GET /api/folders/{id}` → `FolderDetailResponse.notes[]` 用 **`NoteInFolder`**（**有** `file_size`，且在 `required` 里）；
- `GET /api/projects/{id}` → `ProjectDetailResponse.notes[]` 用 **`NoteSummary`**（**没有** `file_size`）；
- 前端两处都声明成 `NoteInFolder`（`file_size: number` 必填）—— **后端自己就不一致**。

**佐证不是推理，是一条已经存在的用例**：`Projects.test.tsx:428` 名叫
*"安全：笔记行缺 file_size / source_type / status 时照常渲染"* ——
**写测试的人早就知道这个类型在撒谎**。目前页面没有真的读 `file_size`
（只有列表端点的 `Note` 被 `DailyMaterials` / `NotesList` 读），所以是**潜在错误而非
线上故障** —— 也正因如此它一直没被发现。

> 这一类错误只有"机械对照"捞得出来：它不违反任何测试、不产生任何告警，
> 缺字段时页面**照常渲染** —— BD 那一轮加的容错护栏正是为此，
> 而护栏同时也是"漂移不再报警"的原因。

### BH.8 可选性口径不一致（53 个函数，**最大的一类**）：机制不是"后端有 bug"

诊断里 `Type 'undefined' is not assignable` 命中 **53 个函数**，方向有两个，含义完全不同：

1. **schema 可选、前端当成必有**（危险方向）：`DueQuizResponse` 的
   `easiness_factor` / `interval` / `repetition` / `review_count`（pydantic 有默认值）
   在 schema 里**都是可选**，而前端 `DueQuiz` 把其中三个标成必有、
   **并且根本没有 `repetition`**；
2. **schema 必有、前端写成可选**（相对安全，但会掩盖"后端一定会给"）：
   如 `updateNote` 的 `note_role`。

> ⚠️ **机制必须写清楚，否则会得出"后端有 bug"的错误结论**：这不是 pydantic 的疏忽，
> 而是**两条规则叠加** ——
> ① pydantic v2 把**带默认值**的字段排除出 OpenAPI 的 `required`；
> ② **`openapi-typescript` v7 又把"带 `default` 的字段"重新标回必填**
> （生成的 `NoteResponse` 里是 `note_role: string`，不是 `note_role?: string`）。
> 对 pydantic 响应模型来说 ② **是对的**（有默认值的字段一定会被序列化出来），
> 所以**切换后前端会更准**，但前端那些 `?:` 会先编译失败；
> 反方向（`anyOf[T, null]` 且**没有**默认值的）生成的是 `?: T | null`，
> 于是所有 `x !== null` 的判据要统一改成 `x != null`，否则"字段名拼错 / 后端没给"
> 会静默通过。
> ★ **这是系统性的口径差，不是逐个 case 的偶发分歧** —— 换句话说，
> 它不能用"遇到一个改一个"的方式收掉。

### BH.9 ★ 一条诚实的否定结果：期待中的"数组 vs `{items,total}`"是 **0 例**

计划（附录 AZ.7 / BB.5）记过一类形状错误：**列表端点被包了一层信封，
前端按数组解析**。本轮把 schema 里 **19 个"列表被包了一层"的端点**逐个机械核对：

```
envelopeEndpoints    19      schema 里"列表被包了一层"的端点
envelopeMismatches    0      前端按数组解析的（即该类错误）
```

**前端 19 个全部声明为信封对象，一个都没写成数组**；`projects.ts` 里那段
`notes: {items:[…]}` 的防拆包逻辑（`Projects.test.tsx:413`）今天是
**冗余防御，不是活 bug**。其余"0 例"的核对一并列出（同样的道理：不写下来，
读者分不清"查过没有"和"没查"）：

| 项 | 数 |
|---|---|
| 前端传了 schema 未声明的 query 参数 | 0 |
| 漏传必填 query 参数 | 0 |
| 枚举**值集合**不一致（`ENUM_DRIFT`） | 0 |
| 数组 vs 信封 | 0 |
| 后端有可选 query 参数、前端没用 | 3（**能力未被消费，不是漂移**） |

> ★ **这是本轮唯一一条"预期有收益、实测为 0"的结果，必须专门记下来**：不写，
> **下一个人会把同一份期待再做一遍**。5.1 的真实收益不在这类形状错误上，
> 而在枚举收窄（BH.7a）与"**后端给、前端类型里没有**"的 22 个字段上
> （切换后它们会一次性变成可选字段，前端可以按需消费）。
> AZ.7 记录的是**页面级**症状，本轮回答的是"**契约级**还有没有同类" ——
> 两者不矛盾：页面确实被漂移打崩过，但漂移不来自列表信封。

### BH.10 ★ 检查器自己的空转 bug：**0 条诊断曾经被当成"通过"**

`openapi-drift.mjs` 的**第一版有两个 bug**（探针文件在 `createProgram` 之后才填内容；
TS 内部把路径统一成 `/` 而脚本用 `\` 比对，于是所有诊断被过滤掉），症状是
**"0 诊断 + 106 个 IDENTICAL"** —— 一份**看起来全绿**的报告。现在：两个坑都写进了
代码注释；并且**生成了探针却一条诊断都没有时，脚本直接抛错**，而不是输出结论
（"0 诊断 ⇒ throw"）。

> **一个只会输出"通过"的检查器，与没有检查器在结论上无法区分。**
> 这与 BE.2 的"退出码 2 ≠ 0 条发现"、BF.3 的"未安装被读成没漂移"、
> BG.3 的"空白页面的 axe 恒为 0 违规"是同一条判据 ——
> **必须能把"没有信号"和"信号是坏的"分开**。本轮把这条判据做进了工具本身，
> 而不是靠人记得。

### BH.11 切换计划（**本轮不执行**）：前置全在后端，步骤逐步可回滚

**前置 P1–P4 —— 全在后端：**

| # | 动作 | 为什么必须先做 |
|---|---|---|
| P1 | 给 BH.4 那 **22 个端点**补 `response_model=` | 否则生成类型是 `unknown`，切换 = 用 `unknown` 换掉手写类型，净亏 |
| P2 | 给 BH.5 那 **4 处内层空壳**补模型 | 同上；这 4 处是手写类型最"值钱"的地方 |
| P3 | 决定 `POST /api/auth/logout` 的 body 是否真的可选（schema 里是 `anyOf: [LogoutRequest, null]`） | 否则生成的客户端会把 body 变可选 —— **唯一一条要"拍板"而不是"补代码"的前置** |
| P4 | 把 `dump_openapi.py --check` 接进 CI | 没有它，前端会在"后端已改、schema 未更新"的窗口里继续绿 |

**步骤 S1–S5（每一步都可独立回滚）**：S1 冻结基线（提交 `openapi.json` 与漂移报告，
CI 挂 `--check`）→ **S2 只换类型、不换请求**（`export type X = components['schemas']['XResponse']`，
**调用方零改动**）→ S3 按域替换请求函数体（顺序按当前判定质量：`report` → `tasks` →
`auth` → `knowledge` → `graph`（**必须等 P1**）→ `notes` → `review` → `qa` → `projects`
→ `upload`）→ S4 收敛 `client.ts`（保留基础设施层）→ S5 才轮到 5.2 / 5.3。

三条**已经定下**的形态（写进计划，以免下一轮重新讨论）：

1. **保留同名导出函数、只换函数体** —— **13 处 `vi.mock('…/api/…')`**（分布 10 个测试文件：
   8 个用 `'../api/…'`、2 个用 `'../../api/…'`；`frontend/docs/openapi-client.md` §9.2 记作
   "8 个测试文件" —— 那 8 个是**只数了单层 `../`** 的那一半，按 mock 点算应以 **13 处**为准）
   因此不用改；一旦改成 `client.GET('/api/notes')` 那种调用形态，**13 处全部要重写**；
2. **401 刷新单飞 / 超时（普通 30 s、上传 600 s）/ `Content-Type` 合并 /
   204 与空响应体处理必须保持手写** —— 这些横切行为 schema 里没有、生成客户端表达不了；
   也**不引入** `openapi-fetch` 之类的运行时（本轮已定"不加运行时依赖"）；
3. **multipart 上传的 4 个函数保持手写**（`uploadFile` / `commitUpload` /
   `uploadFileToFolder` / `prepareUpload` 把 `project_ids`、`linked_material_ids`
   塞成 **JSON 字符串**放进 FormData，OpenAPI 只能表达"这是 string"）。

完整的分步顺序、每一步"先炸什么 / 怎么验证"与逐条风险清单（调用方面积 74 个源文件、
79 处 import；261 KB 类型文件对 `tsc` 的负担）见 **`frontend/docs/openapi-client.md` §9**
（本附录不复制那张表）。

### BH.12 顺带修掉的一处渲染缺陷：**状态标记此前根本没被渲染**（全文档扫描）

阶段 4 / 5 / 6 三张表的表头是 **3 列**（`| # | 动作 | 验收 |`），而带状态的行动项把
✅ / 🟡 写在**第 4 格** —— GFM 的规则是"行的单元格多于表头时，**多出来的被忽略**"，
也就是说：**这些状态标记只存在于源码里，渲染出来看不见**。本文件自己的惯例并非如此
（阶段 1 / 2 / 3 的表头写的是 `| # | 动作 | 验收 | 状态 |`）。

**这一处已经全部修掉，而且是"扫出来"的，不是"看出来的"** —— 判据与结果都写在下面，
便于复核与重跑：

| 步骤 | 判据 / 结果 |
|---|---|
| 怎么枚举表格 | 一行算**表头**：去掉缩进与 `>` 后以 `\|` 开头，且**下一行是分隔行**（只含 `\|` / `-` / `:` / 空白，且至少一个 `-`）；表体延续到不再以 `\|` 开头为止。**先剔除代码围栏（三连反引号）内的行**（ASCII 图里也有 `\|`，不剔就会把图当成表） |
| 怎么算宽度 | 先把转义 `\|` 去掉，再数**未转义** `\|`，按"首/尾各减一"换算成格数；**行宽 > 表头 = 会被丢弃的格子** |
| 结果：`docs/overhaul-plan.md` | 共 **224 张表**（2 列 87 / 3 列 98 / 4 列 29 / 5 列 8 / 6 列 2），**4 张有缺陷**（这 224 张**不含本表** —— 本表是修完之后才加进去的第 225 张；现在重跑同一判据应得 **225 张 / 0 张有缺陷** —— ⚠️ 该判据此后被证明**只数了一个方向**、数字也已再变（**2026-09-14：230 张 / 0 张**），见本表后的更正） |
| ① 阶段 4（`L3215`） | 表头 3 列，8 行带第 4 格状态 → 补 `\| 状态 \|` |
| ② 阶段 6（`L3250`） | 同上，8 行状态 |
| ③ §4.2 目标技术栈（`L2774`） | 两行被 `\|\|` 粘成一行（9 格 vs 表头 4 格）→ **"错误契约"那一整行从来没渲染过**；拆回两行 |
| ④ 附录 J.1（`L4077`） | 单元格内的 `\|D\|` **没转义**，把 BM25 公式劈成了三格 → 改成转义竖线（GFM 在**行内代码里也认** `\|`） |
| 另一条独立检查 | "表格行里反引号内出现未转义 `\|`"全文档只有 **1 处**（就是 ④）—— 这条防的是"行宽刚好对上、内容却错位"的漏网 |
| 结果：`frontend/docs/openapi-client.md` | 共 **14 张表，0 张有缺陷** |
| ⚠️ 阶段 7 为什么没改 | 它的表头是 **2 列**（`\| # \| 动作 \|`），而**表体每一行也正好 2 格**、压根没有状态列 —— **没有任何格子被丢弃**。给它补一列 `状态` 只会多出一列空白，那不是修复；等它真的有状态可写时再加 |

> **2026-09-14 更正（附录 BK.6）：上面的判据只数了一个方向，而后续扫出的 4 行缺陷
> 全都是"比表头窄"的。** 上面写的"**行宽 > 表头 = 会被丢弃的格子**"漏掉了"**行宽 < 表头**"这一类：
> GFM 会把缺的格子补成空格，于是内容**错位**（1.12 / 1.13 在 4 列的表头下只有 3 格
> ⇒ ✅ **渲染在"验收"列**、`状态` 列是空的）或整列**空着**（5.2 / 5.3）。
> **两个方向都要数**：宽 = 内容静默丢弃，窄 = 内容错位 / 缺格。
> 补上这 4 行之后为 **230 张表 / 0 张有缺陷**（225 → 230 = BI / BJ 两张附录新增的 5 张表；
> **2026-09-14 复跑同一判据实测 230 / 0**）—— 本附录（BK）自身再加 **7** 张表
> ⇒ **收口时全文档 237 张 / 0 张**（同一判据，两个方向都数）。

> ★ **这与本项目在代码里反复撞见的是同一个失败模式：一个"看起来在工作、实际什么都没做"的检查。**
> 表格少一个表头列的代价是静默的 —— **内容没丢，只是没人看得到**：
> 状态标记照常被维护、照常被 review，只是它们从来没出现在任何人的屏幕上。
> 与 BH.10 的"0 诊断被当成通过"、BE.2 的"退出码 2 ≠ 0 条发现"、
> BF.3 的"未安装被读成没漂移"、BG.3 的"空白页面的 axe 恒为 0 违规"是同一条判据 ——
> **必须先能区分"没有信号"和"信号是坏的"，才谈得上"这件事被检查过了"。**

### BH.13 验收

| 项 | 结果 |
|---|---|
| `python backend/scripts/dump_openapi.py` | 退出 0，356 080 字节，`paths=102 operations=118 schemas=156 sha256=e21c85952c30`；**连续 3 次 sha256 相同** |
| `python backend/scripts/dump_openapi.py --check` | 退出 0（CI 断言"落盘 schema 与当前代码一致"） |
| `npm run gen:api`（重复执行） | 退出 0，`schema.ts` hash 不变 |
| `npx tsc --noEmit` / `npm run build` / `npm run lint` / `prettier --check` | 全部退出 0（生成文件与漂移脚本都干净） |
| `npm test` | **21 files / 276 tests 全通过** |
| 端点覆盖 | **111/111** 命中，**0 缺失、0 方法错** |
| 未改动 | `frontend/src/api/` 下**除新增 `generated/` 外零改动**；未改 pages / components / 样式 / e2e；**未加运行时依赖**；未提交 |
| 本轮对计划的改动 | 111 的四处修正 + `client.ts` 现状 + 5.1 的状态行与"需先确认" + §2.9 S-6 的前置已兑现 + **阶段 4 / 5 / 6 表头补上 `状态` 列**、§4.2 那两行 `\|\|` 拆开、附录 J.1 的 `\|D\|` 转义（**全文档 224 张表扫过，4 张有缺陷、全部已修**，见 BH.12）（本附录，另见 §4.2 / 阶段 5 / 第八部分 / AO.2） |

> 🎯 **一句话**：这一轮**没有让契约不再漂移，它让漂移第一次可测量** ——
> 而测量的第一个产出是：**5.1 卡在后端，不在前端。**
> 计划原来把它写成一项纯前端任务，**这个前提是错的**；
> 同样值钱的是那条否定结果（BH.9）：**我们以为会抓到的那类形状错误，一个都不存在。**

---

## 附录 BI · 阶段 5.9 / 5.13 收尾，以及两条产品发现（2026-09-14）

本附录记三件事：**5.9 与 5.13 都已经收尾**（此前状态列标的是 🟡）、
收尾过程中暴露的一条**会污染生产库的危险**、以及**两条已上报、未修的产品发现**。
计划正文里对应的状态格已按本文档的规矩**就地更正并注明日期**（阶段 5 表里的
5.9 / 5.13 两行，与"下一阶段（阶段 5 前端）的真实状态"表里那一行），
**旧的句子没有被删掉** —— 那三处更正与本附录互为对照。

### BI.1 5.9 已完成（不再是 🟡）

收尾轮（Part A/B）的输入是 BG.11 末尾那份"axe 判不了、只在人工清单里"的列表，
与 `REGISTRY` 里剩下的 5 条 `heading-order`。收场数字：

| 项 | 值 |
|---|---|
| `npm run a11y` | **26 passed** —— **26 个场景**全部扫过（含本轮新增的第 26 个场景 `learning-goals-create`） |
| 违规 | **0 组 / 0 个节点**（零容忍） |
| `REGISTRY` | **5 条 → 0 条**，即**空表** —— "未登记的违规一条都不许有" |
| 键盘修复 | **18 处**（`a11y-audit.md` §11.1 那张表），**全部用真控件**（`<button>` / `<Link>`），**没有一处**用 `role` + `tabIndex` 去补 |
| `heading-order` | 最后 **5 条全部清掉**（F-07 / F-22 / F-20 / F-31 / F-08） |

**新增两道常驻门禁**（都是"每一轮都跑"，不是一次性脚本）：

1. **键盘可达性扫描，零容忍**：跑在 `auditScene()` 里，**每一个场景都跑**；
   判据是"用户的键盘会遇到什么"（手型光标但整个区域里没有可 Tab 的控件 /
   `tabindex >= 0` 挂在没有交互角色的非原生控件上 / `role="button"`、`role="link"`
   挂在非原生元素上）—— 这三类 axe **一条都不报**（机制见 §11.2）。
2. **Tab 走查断言**（`expectReachableByTab`）：**真的按 Tab**，断言焦点真的落到目标上。
   只断言"它有 `role=button`"是自证，证明不了"Tab 到得了"。

两道门禁都做过**变异测试**（改回 `div[role="button"]`、把 `maxTabs` 调小、
删掉 `aria-modal`、把 `Escape` 写成 `EscapeX`，五次都确实变红），见 §11.4。

★ **F-08 此前记的成因是错的**（这一轮实测推翻了上一轮的解释）：F-08 一直被记成
"标题层级来自**用户自己的 Markdown**"。**实测不是** —— 报出来的那一个节点是
**本页自己的 `<h4>清洁统计</h4>`**（`CleaningPanel` 的元信息区块），
与用户的 Markdown 内容无关；`H1 → H4` 是**跳级**（axe 的判据是
`currLevel - prevLevel <= 1`），而 **`H4 → H1` 是上移，上移对 axe 永远合法**。
因此：**一个字的用户内容都没有动，也没有登记任何豁免** —— 修的是我们自己的
标签层级，`heading-order` 整条消失。另外两条路线（渲染时归一化用户 Markdown
的标题级别、登记一条带理由的豁免）都被否掉，各自的代价见 §11.5。

逐条修法、命名口径与完整记录见 `frontend/docs/a11y-audit.md` **§11**。

### BI.2 5.13 已完成（不再是 🟡）

计划 5.13 的验收是"CI 里有 E2E"，字面链路是 **上传→理解→复习→问答**。
此前落地的两层（Vitest / Playwright）仍是"外壳 + 认证入口"级别的冒烟，
**字面链路没有做**；这一轮补上了：

| 项 | 值 |
|---|---|
| 跑的是什么 | **字面的上传 → 理解 → 复习 → 问答**，对着**真实后端 + 真实 LLM**（不是桩） |
| LLM 走哪 | **OpenCode 的网关**：`base_url=https://opencode.ai/zen/go/v1`，按 `backend/.env` 配置 |
| 结果 | **6 passed × 3 runs** |
| 怎么开 | **只在 `ENGRAMNOTE_E2E_FULL=1` 时注册这一层**，与阻断的 `npm run e2e`（**仍是 10 passed**）完全隔离 |
| 隔离靠什么 | 一次性数据库 / 存储 / broker（`backend/scripts/_e2e_full_*.py`），并**实测真库未被触碰**（真库 `mtime` 跑前跑后一致，且临时库确实存在） |

⚠️ **命名陷阱（读配置时不要被它骗）**：环境变量名里写的是 `DEEPSEEK_*`，
端点却是 **OpenCode** 的网关，**不是** `api.deepseek.com` ——
名字里的 DEEPSEEK 只是历史命名。**"名字叫 DEEPSEEK"与"请求发给谁"是两件事。**

#### BI.2.1 危险：只配 `DATABASE_URL` 的测试后端会把任务投到**生产 broker**

**这是本附录最重要的一段。**

`get_celery_broker_dir()` **硬编码返回 `DATA_DIR / "celery" / "broker"`**
（`DATA_DIR` 就是 `backend/data`），**没有任何环境变量入口**；而
`app/tasks/celery_app.py` 在**模块层**就把这个值写进了 Celery 配置。于是：

1. 一个"只设了 `DATABASE_URL`"的测试后端，任务仍然被投递到
   **生产那套文件系统 broker**；
2. 只要人类的 `start.bat` 起着 worker，它就会**抢走**这些任务
   （文件系统 broker 是"谁先 rename 谁拿到"，**没有任何所有权标记**）；
3. 而那个 worker 用的是**真实数据库**。

**后果：一次 E2E 运行就能污染生产知识库。**

**实际用的缓解方式**：隔离放在**进程内、在 import `celery_app` 之前**完成
（`_e2e_full_bootstrap.py` 在任何 `app.*` import 之前把 broker / 结果后端 /
暂存目录换到临时根；未设置开关时它是空操作）。⚠️ **刻意不给 `app/config.py`
加环境变量入口**：那会动到生产启动路径的配置面，而这一层只需要"测试时换目录"，
隔离逻辑留在测试侧、产品代码零改动 —— 但这也意味着
**"配置面缺一个入口"这件事仍然存在**，只是这一层绕开了它。

#### BI.2.2 这一层仍然不覆盖什么

- **PDF / Office / 音频的上传路径**（只跑了文本类笔记的链路）；
- **全部失败路径** —— 只断言"成功时产出正确"，不测"失败时表现如何"；
- **多 worker / 多用户**（这一层 `workers: 1` + `serial`，且是单用户串行）。

### BI.3 两条产品发现（**已上报，未修**）

#### BI.3.1 RAG 的向量通道在真实链路里是**退化的**

`/ask/stream` 的 `meta.retrieval_status` 实测**恒为 `hybrid`，从来没有出现过
`full_vector`**。机制：`rag_service._encode_via_celery` 只等 **10 秒**
（`task.get(text, 10)`），而 worker 里加载 `bge-m3` 要**几十秒**（首次约 55 秒），
编码这一路因此**每次都赶不上**，只能降级成"词法命中 + 编码成功但向量无命中"。

**净效果：向量通路从来没有被端到端地跑过一次。** 现在测出来的检索质量，
测的是 BM25 / FTS5 的能力，**不是"混合检索"的能力**。

#### BI.3.2 `LLM_PRICE_*` 未设置 ⇒ 成本聚合读作 **0**

`backend/.env` 里没有配单价（`LLM_PRICE_*` 为空），于是 `llm_calls.cost` 记的是
**NULL**，聚合出来的 `totals.cost=0`，**读起来像"免费"**；而同一个响应其实
返回了 `price_configured=false`。

**这是一个报表陷阱：`0` 不等于"免费"，它等于"没配单价、算不出来"。**
把 `cost=0` 当作"花了 0 元"来读，会得出一个完全没有依据的结论。

### BI.4 记录到的文档缺口（**报告，不要静默重构**）

- **阶段 0 与阶段 7 的表格根本没有状态列** —— 阶段 1 / 4 / 5 / 6 的表才有
  `状态` 列（其中阶段 4 / 5 / 6 的表头 `状态` 列还是最近才补上的，见 BH.12 / BH.13）。
  所以这两个阶段**从来就没有逐条的状态跟踪**：不是"跟丢了"，是**从来没有过**。
- 本轮**核对过这件事**（读的是两张表的表头行，不是凭印象）：
  阶段 0 的表头是 `| # | 动作 | 解决 | 验收 |`（**4 列，没有 `状态`**）；
  阶段 7 的表头是 `| # | 动作 |`（**2 列，没有 `状态`**）。
- **处理方式：只登记，不重构。** 本轮**不给**这两张表加状态列 ——
  加一列会把"这两个阶段本来就只写到动作层"这个事实盖掉，
  而缺的并不是一列，是**逐条的状态证据**。
  阶段 7 这半边此前已在 BH.12 记过一次（当时的结论同样是**不给它补一列空的 `状态`**），
  本附录补上阶段 0 这半边。缺口现在**在记录里**，而不是被一次结构改动"修好"。

🎯 **一句话**：这一轮把两个 🟡 收成 ✅，同时留下一条比它们更值钱的警告 ——
**"测试跑在生产 broker 上"这件事，差一次运行就能变成一次数据事故。**

---

## 附录 BJ · 状态缺口回填：阶段 0 全 17 条、阶段 1 的 1.1–1.5、阶段 3 的 3.10/3.11（2026-09-14）

本附录承接 **BI.4**。BI.4 记的是"阶段 0 与阶段 7 的表根本没有状态列，
本轮只登记、不重构"。**本轮把阶段 0 那半边补上了，方法不同**：

| | BI.4（2026-09-14 早些时候） | 本附录（2026-09-14） |
|---|---|---|
| 阶段 0 | 只登记缺口 | **给 17 条逐条查代码，回填 `状态` + 一行理由** |
| 阶段 7 | 只登记缺口 | **仍然只登记**（不加状态列），只补了两条**可核对**的事实，见 BJ.2 |
| 依据 | 读的是**表头行** | 读的是**代码与真库**（每条都给 `file:line` 或只读查询） |

**为什么这次敢补列、上次不敢**：BH.12/BI.4 拒绝补列的理由是
"缺的不是一列，是**逐条的状态证据**" —— 空列会把缺口盖掉。
本轮把**证据**补齐了，所以列里写的是结论 + 依据，而不是一个标记。
**阶段 7 之所以仍然不补**：它的五条是产品/运营动作（灰度、A/B、对外报表），
代码能给的最多是一句"没有对应物"，补一列只会多出五个空格 —— 那正是被否过一次的做法。

### BJ.1 阶段 0：完成 11 / 部分 3 / 未做 1 / 按路线不适用 2

| 判定 | 项 |
|---|---|
| ✅ 11 | 0.1 CI、0.3 启动零破坏性、0.4 WAL、0.5 相似度公式、0.6 停止删 `review_logs`、0.8 统一注册文案、0.9 `debug` 默认、0.14 ErrorBoundary、0.15 分页上限、0.16 轮询清理、0.17 状态样式 |
| 🟡 3 | **0.2**（守卫全落地，`backend/` 根目录那 15 个脚本**从未搬**，`scripts/dev/` 不存在）、**0.7**（限流+时序对齐落地，**登录失败计数与锁定零命中**）、**0.11**（`AppError`/错误信封落地，但 **`raise AppError` 2 处 vs `raise HTTPException` 152 处**，前端两处仍在匹配中文） |
| ⏸ 1 | **0.10**（**没有 `/ready`**，只有 `/health`；`FastAPI()` 未设 `docs_url`/`openapi_url`，因此 `/docs` 与 `/openapi.json` 在所有环境公开） |
| ⛔ 2 | **0.12 / 0.13**（容器化已放弃；`nginx.conf` 与 `frontend/.dockerignore` **文件是真的且内容正确**，CI 亦有守卫，但"Docker 部署下可用"这条验收在本项目的运行路径上不被检验） |

> **2026-09-14 更正（附录 BK）：本表是审计时点的结论，其中三行已在同一日收口。**
> 0.2（搬迁已执行）、0.10（`/ready` 已有、三条 schema 路由生产姿态不注册）、
> 0.11（`raise HTTPException` 152→3、`raise AppError` 2→151、80 个码、AST 守卫）
> ⇒ 阶段 0 的现结论是 **✅ 14 / 🟡 1（0.7）/ ⏸ 0 / ⛔ 2**。
> 下面两段里提到的 0.11 采用面（"`AppError` 2 处 vs `HTTPException` 152 处"、
> "这条验收**尚未成立**"）**已经过期**；**0.7 那半一个字都没变**（限流有、锁定无）。
> 旧行与旧句保留作对照，收口证据见**附录 BK**。

**两条最容易被计划自身措辞读错的**：

- **0.7**：计划把"限流"与"失败锁定"写在同一行、同一个验收里。实际只做了前一半。
  当前的爆破防护**只有** 60 秒滑动窗口（≈10 次/分钟/IP），窗口一过即可继续 ——
  这与计划想象的"锁定"是两种强度。
- **0.11**：`AppError` 存在、错误信封存在、前端也真的读 `error_code` ——
  逐条看每一样都"有"，但**采用面是 2/113**。这正是本项目反复出现的形态：
  **设施建好了，采用没跟上**；而"后端改文案不破坏前端"这条验收，
  只要还有一处 `message.includes('每日上限')` 就不成立。

**另有三条"做完后被别的工作取代"的，不要误读成待做**：
0.5 的公式对了，但它依赖的 Chroma 已按 2.4/2.4′ 删除（公式现在是**死代码**，
"重建 collection 的脚本"从未写，因为集合被**迁移到 `chunks` 表**）；
0.6 的后半（影子生成 + 原子替换）按 §2.6 M-5 / `docs/decisions.md#F-29`
**有意不做**；0.9 的开关改名成 `log_sql`（计划写的是 `db_echo`）。

### BJ.2 阶段 7：仍然只登记（5 条）

7.1 灰度双跑 / 7.2 真实用户 A/B / 7.3 拟合个人化 FSRS 参数 / 7.4 发布学习效果报告 /
7.5 从零重写 `architecture.md`。**本轮不给它们任何状态**，只补两条可核对的事实：
7.3 的参数拟合**没有**实现（`fsrs_service` 用的 `DEFAULT_W` 是 19 个公开默认值，
注释自称"预留的拟合插槽"）；7.1 与 7.2 在 `app/` 与 `src/` 里**零对应物**
（`grep` `ab_test` / `canary` / `灰度` 均 0 命中）。其余三条需要人来定，代码替不了。

### BJ.3 `llm_calls` 没有 `prompt_version`（4.6 的缺口）

只读实测（`file:…engramnote.db?mode=ro`）：

```
llm_calls 列数 = 20
user_id, note_id, task, scene, provider, model, prompt_tokens, completion_tokens,
total_tokens, cached_tokens, cost, currency, latency_ms, success, error,
id, created_at, updated_at, cached, saved_tokens
prompt_version ∈ 列? False      llm_calls 行数 = 0
```

`prompt_version` 只存在于 `knowledge_cards` 与 `quiz_items`。因此 4.6 的
**内容侧**溯源成立（`knowledge_cards` / `quiz_items` 都带版本号，四处写入点已接通，
读取侧 `GET /api/llm/prompt-versions` 也已落地），**花费侧**溯源不成立：
`llm_calls` 能回答"谁、哪篇笔记、哪个场景、花了多少"，
**不能**回答"这笔钱是哪一版提示词花的"。
**一个带 `prompt_version` 的台账才是让"这笔花费来自哪一版提示词"可回答的东西** ——
在那之前，按版本比较只能比"产出了多少内容、内容后来表现如何"，比不了成本。

⚠️ **它不是"加一列"就完**：`llm_calls` 的写入点在网关
（`services/llm/gateway.py`），而"此刻在跑哪个 prompt 版本"只有**场景方法**知道 ——
要么把版本号沿调用链传进网关，要么让网关按 `scene` 反查登记表。
这一层设计成本就是它至今没做的原因，**不是遗漏**。

### BJ.4 顺带核对到的三处过期（都不改正文数字，只在此登记）

1. **`README.md:273-274` 已过期**：它仍写着两条"已知问题（未修）"——
   `nginx.conf` 未设 `client_max_body_size`、`frontend/.dockerignore` 未排除
   `node_modules`。**两条都已经修了**（`nginx.conf:11`、`.dockerignore` 22 行，
   CI 还有守卫）。横幅里"当前部署方式不是 Docker"这半仍然正确，
   过期的只是"未修"那半 —— 属于 §2.1 S-4 那一类"文档成为负债"。
   → **2026-09-14 已修**：`README.md` 那两条已改为"**已修复**"（附
   `nginx.conf` / `.dockerignore` / CI 守卫三处证据），并且**保留**了
   "本节未经本项目验证、容器部署从未走通"的横幅结论 —— 修的是"未修"那半，
   不是"部署可用"那半。
2. **真库行数与正文快照不一致**：附录 A.1/E.5 记的是
   `knowledge_cards 1183 / quiz_items 1058 / review_logs 194`；
   2026-09-14 只读实测为 **1181 / 1058 / 195**（`notes 20`、`users 4`、
   `card_relations 260`、`chunks 608`、新增 26 张表含 `chunks_fts*` 与 `task_runs`）。
   差额是这几轮真库清理与真实使用造成的，**不是缺陷**；但拿附录 A 的绝对数字
   去断言"今天也一样"会得到错误结论。
3. **`backend/scripts/eval_retrieval.py:271-305` 的 `load_chunk_corpus()` 仍然
   `import chromadb`**（函数名与注释说的是"Chroma 里实际索引的内容"），
   而 `chromadb` 已按 2.4 从依赖里删除、当前环境也确实**没装**。
   后果是**静默**的：`except ImportError: return []` → 语料为空，
   评测按"没有这个语料"继续跑。它属于 2.4 收尾的残留（不是新缺陷），
   ⚠️ 但**读这份评测输出时要知道**：`--corpus both` 里的 chunk 那一路
   在当前环境下走的是**另一条**读取路径，别把它当成 Chroma 语料。
   → **2026-09-14 已修**：`load_chunk_corpus()` 改为只读 `chunks` 表
   （口径与线上 `chunk_service.get_user_chunks()` 一致：回收站笔记的 chunk
   不参与检索），删掉了 `--chroma` 参数与死常量 `DEFAULT_CHROMA`；
   "语料 0 条"不再是打印 `[跳过]` 后继续，而是**退出码 2 的错误**
   （与 `check_dependency_drift.py` 的"检查自身空转"同一口径）。

### BJ.5 验收方式（先证伪，再确认）

| 项 | 手段 |
|---|---|
| 真库事实（表、列、行数、FSRS 记录） | **只读**连接 `file:…engramnote.db?mode=ro` 的 SQL 查询；**全程未写库**（`mtime` 与行数由并发进程改动，与本轮无关） |
| 代码事实 | 逐条 `read`/`grep`，每条结论附 `file:line` |
| "从未运行过 Alembic" | 三条独立证据：真库无 `alembic_version` 表、`alembic.ini:6` 指向 PG、`import alembic` 在本机 `ModuleNotFoundError` |
| 未做的事 | **没有改任何代码**（本轮是审计 + 只改本文件）、**没有跑测试套件**、**没有提交** |

🎯 **一句话**：**"状态列缺失"和"状态是假的"是两种病 —— 这一轮治的是前一种，
并且刻意只治了有证据的那一半。**

---

## 附录 BK · 状态审计的收口：0.2 / 0.10 / 0.11 三条落地，外加一条可复现的 CORS 事实（2026-09-14）

本附录承接 **BJ**。BJ 逐条核出阶段 0 的"✅ 11 / 🟡 3 / ⏸ 1 / ⛔ 2"，
其中三条 —— **0.2 脚本搬迁、0.10 `/ready` 与生产姿态、0.11 错误契约的采用面** ——
在**同一天稍后**被做掉了。本附录记这轮做了什么、**哪一次测量推翻了设计前提**、
以及一件**测出来了但没修**的事实。

阶段 0 表里对应的三个状态格已按本文档的规矩**就地更正并注明日期**
（0.2 在搬迁那次提交里已改，本附录补上 0.10 / 0.11 两格与本节合计行），
**旧句要点保留在格内作对照** —— 那三格与**附录 BJ.1** 的旧结论互为参照。

⚠️ **时点**：本附录的全部事实以**写入时的 HEAD `c592645`** 为准
（当时工作区只有两个前端文件被改动）。代码在动，读这段时请先确认
`main.py` / `error_handler.py` / `review.py` / `.gitignore` 这几处**此后没有再被改过** ——
BK.4 与 BK.7 记的正是它们的当时状态。

### BK.1 0.2：15 个脚本搬出 `backend/` 根（顺带带出 59 条没人看见过的历史问题）

| 项 | 值 |
|---|---|
| 搬迁 | `backend/` 根 15 个一次性脚本 → `backend/scripts/dev/`；根目录现在 **0 个 `.py`**（实测） |
| 清单 | `backend/scripts/dev/README.md`（90 行）四列逐条列出：**文件 → 用途（文件头自述）→ 搬迁前的引用方 → 搬迁前的 git 状态** |
| 引用同步（否则当场坏） | `test_e2e.py` / `test_pdf_pipeline.py` / `test_convert_direct.py` 三处用 `__file__` 上溯推"项目根"，文件下移两级后改**上溯三级**；`test_clean_failed_api` 那三个脚本里硬编码的库路径同步（其余脚本按 cwd 取值，搬迁不影响） |
| 搬迁**带出来**的 59 条 | `ruff check scripts` 是**递归**的（`.github/workflows/ci.yml:63-68`），而这 15 个文件原来在 `backend/` 根 —— CI 只跑 `app` / `tests` / `scripts` 三段，**根目录不在任何一段里**；加之其中 14 个被 `.gitignore:64-69` 挡着**从未入库**。于是 59 条（**46×F541** f-string 无占位符 / 7×F401 未用导入 / 2×E401 / 2×E402 / 1×B007 / 1×F841）此前**没有任何人看见过**。已按最小改动修掉 |
| 两个疑似死文件（**只登记，不删**） | `test.py`（11 行 generator 教学片段，与本项目无关）与 `restore_note.py`（7 行、写死一个笔记 UUID）—— 除计划文档自己的清单外**零引用方**；删不删不是这次搬迁能决定的 |
| `pytest` 不受影响 | `pytest.ini` 的 `testpaths = tests` 决定了它们**过去和现在都不被收集**。⚠️ 但 `test_cleaning_failed.py` 定义了 `def test_cleaning_flow()`：被点名（`pytest tests/test_cleaning_failed.py`）时会**真的去打 `localhost:8000`** —— 将来要转正必须重写，不能搬回 `tests/` |

**"报告了但没改"的旧引用**（不在本轮可改动的文件范围内，完整清单在 README 的"遗留 3"）：
`backend/tests/test_full_e2e.py:753,762`、`backend/tests/测试账号信息.md:35,45`、
`docs/archive/新手教学.md:4175`、`docs/archive/开发时间表.md:77,514`、
`docs/code-review-report.md:44,150`、`docs/verification-report-20260830.md:24`、`.trae/documents/**`。
没有一处是可执行的构建 / CI 路径；正确的命令现在写作 `python scripts/dev/<file>.py`
（**cwd 仍是 `backend/`** —— 部分脚本按 cwd 找 `data/db/engramnote.db`）。

⚠️ **`.gitignore` 的连带后果（已定性，收口待做）**：`.gitignore:64-69` 的六条模式
（`backend/test.py` / `backend/test_api.py` / `backend/test_*.py` / `backend/verify_*.py` /
`backend/reset_*.py` / `backend/restore_*.py`）都**带路径前缀、锚在仓库根**，
文件一挪走就**不再命中**：实测旧路径仍匹配（`git check-ignore -v --no-index backend/test.py`
→ `.gitignore:64`），新路径不匹配（`backend/scripts/dev/test.py` → 无输出、退出 1）。
那 14 个文件随后**已随搬迁入库**（`git ls-files backend/scripts/dev/` = **16** 个文件），
"提交还是继续忽略"这个决定**已经做出**；剩下的是那六条模式现在**匹配不到任何文件** ——
删除或改写是**一行收口**，登记在 BK.7。

⚠️ **搬迁时写的 README"遗留 1 / 遗留 2"记的是提交前的样子**（"这 14 个文件现在会以未跟踪文件
的形式出现在 `git status` 里"、"`e2e_cleanup.py` 是唯一被 git 跟踪的文件"）——
**入库之后这两句已经过期**（只登记，不改：那个文件不在本文件的改动范围内）。

**0.10 的一个跨效应**（同上 README 遗留 4）：`test_e2e.py` 用 `GET /docs` 判"服务器起来了没有"，
而 0.10 之后生产姿态**根本不注册 `/docs`** ⇒ 探针拿 404、脚本空等 30 秒后打印"[FAIL] 服务器启动超时"
并退出 1 —— 而服务器其实早就起来了。两处探针改为 `GET /health`，并且**刻意不用 `/ready`**：
`/ready` 回答的是"现在能不能干活"，把依赖抖动翻译成"我的子进程没起来"会把排查引向错误方向。
**"拿调试端点当存活探针"本身才是缺陷** —— 任何门禁（关闭或认证）都会让它不再返回 200。

### BK.2 0.10：生产姿态**不注册**那三条 schema 路由 + 新增 `/ready`

| 决定 | 内容与理由 |
|---|---|
| 关法 | **不注册**，不是"加保护"：`docs_url` / `openapi_url` / `redoc_url` 三个都传 FastAPI 原生的 `None`（`main.py:239-245` 的 `_schema_endpoint_kwargs()`）⇒ 生产姿态下是 **404**，与"未知路径"**不可区分** |
| 判据 | **沿用既有的 `cfg.is_dev`**（`config.py:522-525`：`app_env == "dev" or debug` 的遗留折叠），**没有第二套"我是不是生产"** |
| 为什么不加认证 | 本项目**没有管理员角色**：任何已登录用户都能拿到同一份 schema ⇒ "加保护"实际是把"公开"变成"**对每个注册用户公开**"，收益接近零却多出一个要维护、要验证的中间件；而 `/openapi.json` 对攻击者是**侦察** —— 一次匿名请求即可拿到全部路由、参数名与认证方案 |
| 为什么 `None` 优于门禁 | `None` 是**声明式**的：没有"忘了加依赖"或"鉴权分支写反了"的形态 |
| dev 姿态 | **不变**（三条路由都在，`/docs` 能点、`/openapi.json` 能取） |
| 工厂化 | 模块级 `app = FastAPI(...)` → `create_app(config: Optional[Settings] = None)`（`main.py:486`）。**此前根本没有工厂**（不是"改了签名"），把姿态变成**显式入参**之后，**一个进程里就能同时断言两种姿态**（`tests/test_env_switches.py::TestSchemaEndpointGating`） |
| 不破坏工具链 | `dump_openapi.py` 走**进程内** `app.openapi()`（`:81-83`：不起服务、不连库、不占端口），`openapi_url=None` 对它没有任何影响；有配套用例钉住（`test_in_process_schema_survives_production_posture`）—— 否则"为了关文档而弄坏前端生成"会是一次静默的倒退 |

**`/ready`：200 / 503，两种状态共用同一响应体形状**
（`status` / `app` / `database{status, reason}` / `queue{depth, running, pending, source}`，`main.py:276-319`）。

| 分工 | 内容 |
|---|---|
| `/health` = **存活** | **零依赖**（不碰 DB / broker / 外部服务）。拿它决定"要不要重启进程"，因此**绝不能**因依赖抖动而失败：一次数据库抖动会变成成批重启，而重启既不修数据库，还会打断正在跑的任务 |
| `/ready` = **能不能干活** | 判据**只有数据库**：一次**只读聚合查询**（`select(TaskRun.status, func.count()).group_by(...)`），**不是 `SELECT 1`** —— "连得上但表不存在"是本项目真实踩过的形态（CI 临时库未建表 ⇒ 每个请求 500 `no such table: users`） |
| 队列深度 | **报告项，永不参与状态码**：忙 ≠ 坏。把深度做成就绪判据，会让高峰期所有实例一起被判未就绪、负载均衡器把流量发给空实例；接到存活探针上就是重启风暴 |
| 净效果 | DB 挂 ⇒ **`/health` 200 + `/ready` 503**（探针与看板可以只靠这一对区分"进程死了"和"依赖坏了"） |

★ **改了设计的那次测量：只数数据库得到的"队列深度"是假指标。**
`task_runs` 的行是 **worker 接手时**才建的（`tasks/common.py::begin_task_run` → `ensure_task_run`，
建出来就是 `running`）—— 消息在 broker 里排队的那段时间，数据库里**一行都没有**，
所以按 DB 数"pending"**几乎恒为 0**。
真实积压在 **broker**：kombu 的文件系统传输把每条消息写成一个 `{时刻}_{uuid}.{队列}.msg` 文件、
消费者取走时把文件**移出**该目录，因此"目录里剩下的 `.{队列}.msg` 文件数"就等于"还没被取走的消息数"
（与 kombu 自己的 `Channel._size(queue)` **同口径**）。⚠️ **只按后缀匹配队列名**：
目录里还住着控制消息（`…celery@主机.celery.pidbox.msg`，本机实测残留 **15** 个），
把它们算进业务队列会让深度凭空多出十几 —— 而"指标虚高"一旦被当成基线，
之后就再也没人看得出真正的积压。**测不到就报 `None`（不是 0）**：
redis 后端（`redis_broker_not_probed`）与目录不可读（`broker_dir_unreadable`）都给 `None` + 原因码；
只有"目录不存在 = 从没投递过一条消息"才是 0。

⚠️ **代码里两条注释把中间件顺序写错了**（本轮核对时发现，**未改**）：
`middleware/error_handler.py:20-22` 写的是"中间件挂在 CORS 内侧（CORS → ErrorHandler → RequestContext → 路由）"——
**两侧都与实际相反**；`main.py:532-536` 把 `RateLimit` 与 `RequestContext` 的相对位置写反
（它对"ErrorHandler 在 CORS 外侧"这一句是对的）。
实测顺序（外 → 内；按注册顺序 + Starlette `insert(0, …)` / `reversed()` 语义）：
**`RateLimit → RequestContext → ErrorHandler → CORS → ExceptionMiddleware → 路由`**。
先在这里点名，是因为这两条注释正是下一节那条事实的**引路牌**。

### BK.3 0.11：`raise HTTPException` **152 → 3**（3 处书面豁免），且**零状态/文案漂移**

| 项 | 值 |
|---|---|
| 采用面 | `raise AppError` **2 → 151**、`raise HTTPException` **152 → 3**（**AST** 实测：解析 `raise` 的调用名，不是正则）。按出口算采用面 **2/154 → 151/154** |
| 3 处存活 | 全部在 `app/api/auth.py`（`:103` / `:115` / `:227`） |
| 为什么必须豁免 | 它们**必须带 `WWW-Authenticate: Bearer`**：`ErrorHandlerMiddleware._error_response()` 构造 `JSONResponse` 时**不接受、也不转发 `headers=`**（`error_handler.py:52-63`），而路由层 `HTTPException` 走的是 FastAPI `ExceptionMiddleware` + `main.py:454` 的 `http_exception_handler`，那里**转发** `headers=getattr(exc, "headers", None)`。换成 `AppError` 就是**丢掉质询头** |
| 豁免语法 | 紧邻 raise 的注释 `# error-contract: exempt — <理由>`（守卫向上看 **3** 行；**理由为空不算豁免**） |
| 码表 | **80** 个，全部 `NAME == VALUE`、**0 重复、0 未使用**（`app/core/app_error.py:40-212`；⚠️ 不是 `error_codes.py`，**没有这个文件**）。命名约定写在表头：不把 HTTP 状态码写进名字、按"资源 + 条件"取义、**一个 code 只表达一件事** |
| 粒度规则 | 就写在码表里（`app_error.py:53-56`）：**判据是"调用方能否靠 code 区分处境"** —— 调用方知道自己调的是哪个端点，所以"清洗的状态闸门"与"重试的状态闸门"共用一个 `NOTE_STATUS_INVALID` 不会撞车；**给每个端点复制一份同义 code 只会让词表膨胀** |
| 守卫 | `backend/tests/test_error_contract_adoption.py`（516 行 / **16** 用例 / AST）。实测账目：`app/api` **24** 个文件 / **3** 处 raise / **0** 违规 / **3** 条豁免；`app/services` **66** 个文件 / 0 处 |

**反空转两道**（一个"零违规"的检查，必须先证明它抓得到东西）：

1. **合成源码**：植入一处违规 ⇒ `violations == ['planted.py:3']`；带理由的标记 ⇒ 豁免、
   且理由里确实含 `WWW-Authenticate`；**标记没有理由 ⇒ 仍是违规**；标记离得太远（>3 行）⇒ **不豁免**；
   非 `HTTPException` 的 raise 不计入。
2. **真源**：在**真实已迁移的** `tasks.py` 里临时植回一处 `raise HTTPException` ⇒
   `violations` 由 `[]` 变成**指向该 `文件:行` 的条目**（守卫报的是相对 `backend/` 的路径，
   形如 `app/api/tasks.py:NN`）。

另有 `MIN_SCANNED_FILES = 15` 这类**下限断言**：目录被整体挪走 / 改名时，扫描会退化成
"零个文件、零个违规"—— **而那看起来正是通过**。

⚠️ **一处说法要更正**：守卫**没有**打印汇总行 —— `print(` 出现 **0** 次，
`http_exception_raise_total` 这个标识符在文件里**不存在**。
**`24 / 3 / 0 / 3` 是它的扫描函数实测出来的值**（外加 `scanned_files >= 15` 与
`len(exemptions) == 3` 两条断言），不是一行输出。记这条是因为
"引用一个并不存在的输出"正是本文件反复登记的那种形态。

**"零漂移"是怎么证的**：一个 diff 脚本对 **HEAD 与工作区**逐一比对每个出口的
`(status, message)` **多重集** —— **149 个已迁移出口全同**，即**状态码与用户可见文案零漂移**
（152 − 3 = 149）。一个端点的前后对照留作样本：

```
迁移前  404 / "项目不存在" / HTTP_404
迁移后  404 / "项目不存在" / PROJECT_NOT_FOUND
```

**变的只有 `error_code`** —— 这也是为什么下面 BK.5 第 1 条那条"改文案就是 bug"的规则
在本轮被执行得如此死板。

### BK.4 ★ 测出来的一条事实：`AppError` 的响应**没有 CORS 头**（**没修**，也不是本轮引入）

**机制**：`AppError` 由 `ErrorHandlerMiddleware` 渲染，而这个中间件在 **`CORSMiddleware` 外侧**
（BK.2 末尾那条实测顺序）⇒ 它生成的响应**根本不经过 CORS**，因此**没有 `Access-Control-Allow-Origin`**。
而 `HTTPException` 的响应由**最内层** `ExceptionMiddleware` 渲染后**向外穿过** CORS ⇒ 有该头。

**实测**（同一个 app）：

| | 请求 | `access-control-allow-origin` |
|---|---|---|
| 迁移前 | `GET /api/projects/x`（HTTPException 路径） | **有** |
| 迁移后 | `GET /api/tasks/x`（AppError 路径） | **没有**（响应里只有 `x-request-id`） |

**今天的实际影响：零。** 前端与后端**同源**（走 Vite 代理），跨域调用不存在；
而且**既有的 `AppError` 出口与 429 一直就是这个样子** —— 所以这**不是本轮引入的回归**，
本轮只是把它变成了**可复现的事实**。

**给要修它的人三句话**：① 位置在 `middleware/error_handler.py` 或 `main.py`；
② 要害是 **CORS 与错误处理器的先后关系**，不是"给响应补一个头"（补头解决不了预检与
`credentials`，也帮不了别的中间件路径）；③ ⚠️ **别信那两条注释**
（`error_handler.py:20-22` 与 `main.py:532-536` 都把顺序写错了，见 BK.2 末尾）——
以**注册顺序 + Starlette 前插 / `reversed()` 语义**为准。登记在 BK.7 第 1 条。

### BK.5 本轮**刻意没改**的（每条都给理由，免得下一轮当成遗漏）

| # | 没改的 | 理由 |
|---|---|---|
| 1 | `assessment.py` 三处 500 的 `str(e)` | 规则是"**改文案就是 bug**"：本轮**只加 `error_code`**，用户可见内容一字不动。⚠️ S-8（错误响应回传内部细节）**仍然成立**，只是载体从 `HTTPException` 换成了 `AppError` —— 该节已就地注明 |
| 2 | 三处**仍由中文决定状态码**的地方 | `folders.py:246`（`if "不存在" in str(e)` ⇒ 404，否则 400）、`versions.py:207-209`、以及同类分支 —— **保留原判据与原状态码**；改判据＝改行为，是单独一件事 |
| 3 | `DELETE /api/projects/{id}` 对"不存在"返回 **400**（GET / PATCH 是 404） | 历史取值，不改；**码名照实取**（`PROJECT_NOT_FOUND`），以免"码说 404、状态其实是 400"这种**更坏的谎** |
| 4 | `quick_review` 的"题目不存在"返回 **400**，而 `review` 同一处境返回 **404** | 同上：两个端点各自的历史取值，本轮不统一 |
| 5 | `goal_service.py` 里的**英文**文案 | 5 处里 3 处英文（`:146` `Maximum N active goals`、`:249` `Goal not found`、`:420` `No active goals, …`）、2 处中文（`:84` / `:96`）—— 迁移前就有的不一致，照原样搬 |
| 6 | `versions.py` 那两处 `except ValueError` | 能落进去的**只有** `UnicodeDecodeError`（`ValueError` 的子类）⇒ 原写的"版本不存在 ⇒ 404"分支是**死代码**。码名照实取 `VERSION_CONTENT_UNAVAILABLE`（**不用码去撒谎**）；真正的版本缺失由 service 直接抛 `AppError(VERSION_NOT_FOUND)` |
| 7 | `review.py:369` 的 `except HTTPException: raise` | 它**目前是死代码**（服务层现在抛 `AppError`），**但不只是冗余**：一旦哪个依赖抛 `AppError`，会被下面那个 `except Exception` 包成 500 `REVIEW_REMINDERS_FAILED` 而不是透传 ⇒ 应改成 `except (HTTPException, AppError): raise`（登记在 BK.7） |
| 8 | docstring 里的 `Raises: HTTPException` 行 | **故意留着**：它们会**喂进 OpenAPI 描述**，而生成物（`backend/openapi.json` / `frontend/src/api/generated/schema.ts`）**刚刚重新生成过** —— 改它们会**立刻**让另一个 agent 的产物过期。要么连同生成物一起改，要么不改 |

### BK.6 顺带修掉的两件，其中一件**更正了计划自己的方法论**

1. **`eval_retrieval.py` 的静默空语料**（BJ.4 第 3 条已记，本轮已修，此处只补口径）：
   改为**只读** `chunks` 表；**0 条文档 = 退出码 2 + 大横幅**
   （与 `check_dependency_drift.py` 的"检查自身空转"**同一口径**）。
   "没东西可测"从此**不是一个绿色的结果**。
2. **四行缺格的表格**（阶段 1 的 1.12 / 1.13、阶段 5 的 5.2 / 5.3）：四列的表头下只有**三格** ——
   1.12 / 1.13 的 ✅ **渲染在"验收"列**、`状态` 列是空的；5.2 / 5.3 的状态列整列空着。已补回四格。
   补上之后（本附录之前）复跑同一判据实测 **230 张表 / 0 张有缺陷**
   （225 → 230 = BI / BJ 两张附录新增的 5 张表）；**本附录自己又加了 7 张表
   ⇒ 收口时全文档 237 张 / 0 张**。

★ **方法论更正（比上面两件都重要）**：**BH.12 记的判据只数了一个方向。**
它写的是"**行宽 > 表头 = 会被丢弃的格子**"，而这一轮扫出的 **4 行缺陷全都是"比表头窄"**——
**按那条旧判据，这 4 行一个都抓不到**。两者的表现不同、但都是缺陷：
**宽 = 内容被静默丢弃**（渲染出来看不见），**窄 = 内容错位或整列空着**（看得见，但是错的）。
**扫描必须两个方向都数。** 这与 BH.10 / BE.2 / BF.3 / BG.3 是同一条判据：
**"没有信号"与"信号是坏的"必须先能分开，才谈得上"这件事被检查过了"** ——
本轮补上的正是"**检查本身漏了一半**"这一种。

### BK.7 悬着的小事（不写下来就会丢）

下表的状态同样是**本附录写入时**（HEAD `c592645`）的状态 —— 每一条都给了位置，
便于下一轮逐条核对而不是重新找。

| # | 事项 | 位置 / 现状 |
|---|---|---|
| 1 | **CORS 与错误处理器的顺序**（BK.4） | `middleware/error_handler.py` 或 `main.py`；今天无实际影响，但是一条可复现的事实，且两处注释把顺序写错了 |
| 2 | `review.py` 的 `except HTTPException: raise` → `except (HTTPException, AppError): raise` | `review.py:369`；当前死代码，一旦有依赖抛 `AppError` 会被包成 500 |
| 3 | docstring 里的 `Raises: HTTPException` 行 | 必须与 `openapi.json` / `schema.ts` 的**再生同批**做，否则又是"改一句、过期一个产物" |
| 4 | `.gitignore:64-69` 六条**已经匹配不到任何文件**的模式 | 一行收口（删除或改写）；搬迁那 14 个文件已入库，所以这纯粹是**死规则** |
| 5 | 依赖漂移检查**容忍缺失的可选依赖** | `check_dependency_drift.py` 在**瘦环境**里会因为 `httptools` / `watchfiles` 缺席（`installed is None`）而失败 —— **那次失败是环境性的、不是回归**（脚本自己已有 `EXIT_VACUOUS = 2` 的口径，但"缺一个可选依赖"目前只记作"未安装"、**不进 `problems`**）。⚠️ 本机实测 `httptools 0.7.1` / `watchfiles 1.1.1` **是装着的**，该失败只在瘦环境复现 |
| 6 | 0.11 的**前端一半**：`TodayLearn.tsx` / `Review.tsx` 的分流切换 | HEAD 上它们**仍是** `message.includes('每日上限')`（`TodayLearn.tsx:146` / `Review.tsx:126`）。本轮已把结构化的码交给前端（`client.ts:314` 的 `ApiError.code`），并且 0.11 的提交里**已经带了要求它们改的用例**（`tests/test_error_contract_adoption.py::TestFrontendBranchesOnCode` 与 `pages/Review.test.tsx`）—— **两边配套，落地时必须一起** |
| 7 | `backend/scripts/dev/README.md` 的"遗留 1 / 2" | 记的是**提交前**的状态（"14 个文件会以未跟踪文件出现"、"`e2e_cleanup.py` 是唯一被跟踪的文件"）；**入库后已过期**（只登记，不改 —— 那个文件不在本文件的改动范围内） |
| 8 | `tests/test_error_contract_adoption.py:504` 引用的"`overhaul-plan` **第 10529 行**" | 该引用**本来就已经偏了**（现行 10529 行是 0.7 那段），而本附录会让行号**再次位移** ⇒ 这类引用应改成**附录锚点**（如"附录 BJ.1"），**不要引用行号** |

🎯 **一句话**：**这三条收口里最值钱的不是"152 → 3"，而是那次测量 —— `task_runs` 的行是 worker
接手时才建的，所以"队列深度"这个指标按原来的算法从一开始就只能读出 0；以及那条没修的事实：
`AppError` 的响应到今天仍然没有 CORS 头。**

---

## 附录 BL · 阶段 5.1 的 S3a / S4，以及 P4 终于接进 CI（2026-09-14）

> 本附录写的是**这一轮实际发生的事**，不是计划。逐函数记录、守卫设计与"没做的事"
> 在 `frontend/docs/openapi-client.md` §14；这里记的是**结论、证据、以及新欠下的账**。

### BL.0 一句话

**S3 的前半真的换掉了**：111 个手写 API 函数里的 **34 个 JSON 请求体**全部改为由契约
派生（新增按端点索引的 `BodyOf` / `QueryOf` / `ApiMethod` / `BodyWithDefaults`），
`bodyFindings` **25 → 0**、`hwDiscardsBody` **1 → 0**、`compilerDiagnostics` **2 → 0**，
而**分母一个没动**（判定对象 111、被比过的请求体 34）；**S4** 把三个请求入口里
逐字重复的错误解析收成一处；**P4 接进 CI** —— `.github/` 现在存在了，上一轮
"要改 `.github/**` 所以做不了"的前提已经消失。

### BL.1 漂移对照（`scripts/drift-delta.mjs`，两份 `--json` 快照逐项对照）

| 指标 | 前 | 后 | 说明 |
|---|---:|---:|---|
| `bodyFindings` | 25 | **0** | 请求体接入契约后，这类比对**结构性**无事可报 —— 所以下面三行才是重点 |
| `hwDiscardsBody` | 1 | **0** | `deleteAnnotation`：唯一一个"把响应体 await 掉"的函数（§13.5 那笔账） |
| `compilerDiagnostics` | 2 | **0** | 就是上一条产生的两条 `void` 双向不兼容 |
| `identical` | 104 | **105** | 只涨 1 —— S3 改的是**请求**，`identical` 判的是**响应**；`105 + 0 + 6 = 111` 本身即"判定对象一个没少" |
| `handWrittenFunctions` / `pathMatched` | 111 / 111 | 111 / 111 | 分母不变 |
| **`bodyChecked`（真的比过请求体的函数数）** | **34** | **34** | **归零不是"没比"的证据**（新加的守卫，见 BL.3） |
| `schemaPaths` / `schemaOperations` | 103 / 119 | 103 / 119 | 契约本身没动（本轮不改后端） |
| `unusedSchemaQuery`（信息项） | 3 | 3 | 那三条是"契约给了、前端从没传"的能力，见 BL.6 |

### BL.2 两条生成器事实（本轮实测撞出来的，不是读文档读来的）

1. **可选的 `requestBody` 会把最自然的条件类型骗成 `never`**：`openapi-typescript`
   在 `requestBody?: {…}` 上显式写着 `| undefined`，于是
   `Op extends { requestBody: { content: … } }` 永远不成立。第一版 `BodyOf` 因此让
   **34 个端点里的 3 个**（`logout` / `restoreVersion` / `startUnderstanding` ——
   恰好都是"body 可选"）静默退化成 `never`，而 `never` 与"这个端点确实没有请求体"
   **长得一模一样**。发现它的不是类型系统，是**把 34 个端点逐个用编译器 API 打印出来对照**。
2. **pydantic 的 `default` 让请求字段在生成类型里变必填**，比真实契约更严：
   对**响应**这是对的（S2 就是靠它），对**请求**则会让"我省略字段、让后端用默认值"
   变成编译错误 —— 而"照编译器要求把默认值抄一份发过去"更糟：后端改默认值时前端会
   **静默覆盖**。所以新增 `BodyWithDefaults<P, M, K>`：把列出来的键放回可选，
   `K` 受 `keyof` 约束（拼错编译失败），漏列则会立刻被编译器指出来。

### BL.3 一个被 S3 逼出来的守卫缺陷：**"没有信号"被当成了"信号是坏的"**

漂移脚本原来的空转守卫是"**有探针却 0 条诊断 ⇒ 探针没被真正检查**"。
它的来历是真的（第一版脚本就是"0 诊断 + 106 个 IDENTICAL"），但**判据本身是错的**：
0 条诊断恰恰是**目标状态**。S3 修好 `deleteAnnotation` 之后，守卫立刻误报并拒绝出报告：

```
[空转守卫] 生成了 105 个探针但编译器一条诊断都没有 —— 探针文件很可能没被真正检查
```

修法不是放宽守卫（那等于删掉它），而是让"探针真的被检查"**自带证据**：
往探针文件里塞一条**故意写错的金丝雀**（`const __canaryMustFail: never = <某个 schema 响应类型>`），
守卫改成"**金丝雀必须报错**"，且金丝雀那条诊断**不计入** `compilerDiagnostics`。
实测 `# 探针数 105 / 诊断数 1（其中金丝雀 1，真实 0）`。

> 这条与 BE.2 / BF.3 / BG.3 / BK.6 是同一条判据的第五次出现：
> **守卫必须用一个"已知为坏"的输入来证明检测链路活着**，否则"全绿"与"没检查"
> 在报告里长得完全一样。

### BL.4 P4：两个半条接进 CI（**CI runner 本身没跑过**，如实说）

`.github/workflows/ci.yml` 已经存在（上一轮的"接 CI"轮次建的），所以 P4 的阻塞消失了。
本轮加了两条**配对**的步骤 —— 只加一条会留下缺口：

| 位置 | 步骤 | 防的是什么 |
|---|---|---|
| backend job（Ruff 之后、pytest 之前） | `python scripts/dump_openapi.py --check` | **代码 → `openapi.json`**：改了后端（`response_model=` / 路由 / 字段）却忘了重新生成 schema |
| frontend job（ESLint 之后） | `npm run gen:api` + `git diff --exit-code -- src/api/generated/schema.ts` | **`openapi.json` → `schema.ts`**：生成了 schema 却忘了生成类型 |

**本机实测（两条命令的行为）**：`dump_openapi.py --check` 退出 0，打印
`paths=103 operations=119 schemas=184 sha256=9a633977af6e`；`gen:api` 后
`git diff --exit-code` 退出 0（幂等）。**没验证到的是 GitHub runner 上那一次真实执行**
（本轮不推送远程）。可依赖的间接证据：同一个 job 里的 pytest 本来就 `import app.main`，
而这个脚本只多做了 `app.openapi()`（纯内存，不连库、不起服务、不触发 lifespan）。
若它在 CI 上失败，先分清两类：写着"与当前代码不一致"= 真的忘了生成；
import/配置类报错 = 环境问题 —— **两者不许用 `|| true` 一起掩盖**。

### BL.5 用户决策（本轮口径）：**不影响单机用户体验的遗留项暂不修改**

按此口径，三条**挂起**（不是"做完了"，是"明确不做并记下理由"）：

| # | 事项 | 为什么可以挂起 |
|---|---|---|
| 1 | **限流按 IP 计数、而不是按用户**（`RateLimitMiddleware` 在最外层，`RequestContext` 还没跑，`get_user_id()` 那条分支永远拿不到值；它的 429 响应也没有 `request_id`） | 单机部署就是"一个人 + 一个 IP"，按 IP 与按用户是同一件事；多用户/反向代理部署下才会失真 |
| 2 | `requirements.txt` 的 `~=` 约束策略与依赖升级（**19 条里 17 条与已装版本不在范围内**） | 纯工程卫生，与用户可见行为无关；且改约束要连带重跑全套验证 |
| 3 | a11y / 安全扫描是否升级为**阻断**门禁 | 两者今天都是**建议性**且已在 CI 里真的会失败（`continue-on-error` + REGISTRY）；升级的前置条件 CI 注释里已写明 |

### BL.6 顺带发现（改的是请求侧，照出来的是别的东西）

1. ★ **"清空字段"这件事，前后端各有一道闸，合起来做不到**（本轮只登记、未修改）。
   前端：`pages/projects/useProjects.ts:102` 提交 `edit.description.trim() || undefined`
   → `JSON.stringify` 把 `undefined` 的键丢掉。后端（更根本）：
   `services/project_service.py:196-199`、`api/understanding.py:465-468`、
   `api/knowledge.py:180-183` 三处都是 `if x is not None:` ——
   **显式 `null` 与"不传"在服务层完全等价**。于是契约里那些 `| null` 字面上像
   "可以清空"，运行时却表示"不要改这个字段"。UI 上确实有编辑项目描述的输入框
   （`pages/projects/ProjectRenameForm.tsx:31-37`），所以这是"**用户做得到操作、
   系统做不到结果**"的缺陷。修法要动服务层（区分"显式给了 null"与"没给"），
   属行为变更，留给决策。
2. `updateGoal` 在前端**零调用方**（全仓 `grep` 只有定义），而契约里它有 6 个可更新字段
   （含前端从未暴露过的 `status`）。
3. `unusedSchemaQuery = 3` 的三条是"契约提供、前端从没传"的**能力**，不是缺陷：
   `GET /notes` 的 `project_id`、`POST /understanding/{note_id}/generate-questions` 的
   `target_categories` / `target_difficulty`。
4. **`src/api` 本来就不在 prettier 规范内**（实测 HEAD 内容上 `report.ts` /
   `cleaning.ts` / `upload.ts` / `client.test.ts` 都失败，`tasks.ts` 通过）——
   与 `format:check` 的既有失败列表一致。本轮触碰的文件统一跑了 `prettier --write`，
   所以 diff 里含**纯格式改动**；语义层面的可核对口径是"AST 判定对象与分母一个没动"
   加上"运行时改写只有'内联字面量提成 `const body`'"。

### BL.7 这一轮的账（全部真跑过）

| 命令 | 结果 |
|---|---|
| `npx tsc --noEmit` | 退出 0（实测 11.5 s） |
| `npm test` | **22 files / 280 tests 全通过**，退出 0（基线 21/278，+1 文件 +2 用例 = 新的 `types.test.ts`） |
| `npm run lint` | 退出 0 |
| `npm run build` | 退出 0（✓ built in 5.22 s） |
| `npm run e2e` | **10 passed**（真 Chromium，13.9 s） |
| `npm run e2e:full`（`ENGRAMNOTE_E2E_FULL=1`） | **6 passed**（真实后端 + 真实 LLM 全链路，1.6 min） |
| `npm run gen:api:drift` + `drift-delta` | 退出 0，数字见 BL.1 |
| `doc-table-scan.mjs`（本轮新入库的表格扫描器） | **本附录之前**：`docs/overhaul-plan.md` **237 张表 / 1156 行 / 0 处缺陷**（与 BK.6 的人工扫描一致）；**加上本附录之后**：**242 张 / 1184 行 / 0 处缺陷** |

### BL.8 悬着的小事（下一轮逐条核对，不重找）

| # | 事项 | 现状 / 下一步要什么 |
|---|---|---|
| 1 | ~~**S3b：query 参数接入契约**~~ | **已完成（2026-09-14，附录 BM）**：18 个调用点接入契约、仪器三处升级 + 两个新分母、两次变异验证。剩下的是那 3 条"契约有、UI 没传"的能力（属产品功能） |
| 2 | **S5（5.2 TanStack Query / 5.3 Zustand）** | **计划已出、未动手**（`frontend/docs/query-and-state-plan.md`，见附录 BM.6）：28 个文件在 `useEffect` 里取数、3 处轮询、2 个 context、4 个批次、5 条不变量；**四件事待你决定**（依赖批准 / `QueryClient` 默认值 / `AuthContext` 是否迁移 / "行数减半"的口径改写） |
| 3 | P3：`POST /api/auth/logout` 的 body 是否真的可选 | 契约写 `requestBody?`，前端总是发；本轮只做到"前端与契约一致" |
| 4 | `uploadRequest` / `askQuestionStream` 的 `ApiError.message` 是否统一（今天 `request()` 前置错误码、另两处不前置） | 行为变更（提示文案），要单独决策 |
| 5 | 清空字段的服务层语义（BL.6 第 1 条） | 要动 `x is not None` 那三处 |
| 6 | RAG 向量通道：`retrieval_status=hybrid` 的成因**未定论** | 领先假设是**索引时滞**（提问时新 chunk 还没有 embedding）；判定方法是"提问前先断言全部 chunk 都 `has_embedding=1`"。⚠️ 另有一条待测：`rag_service._encode_via_celery` 的 `task.get(10)` **10 秒硬超时**在冷启动下会走 BM25，但**超时不取消任务**，所以是窗口而不是永久降级 |
| 7 | 表格扫描已成为常驻工具 | `frontend/scripts/doc-table-scan.mjs`（默认扫 4 份文档；有缺陷退出码 1，判据与 BK.6 同：**两个方向都数**、转义竖线 `\|` 不算分隔、代码围栏里的 `\|` 不算表）。它是 BK.6 那条判据的**机器化复现**：本附录之前独立跑出 **237 张表 / 0 缺陷**（与人工扫描一致），加本附录后 **242 张 / 0 缺陷**。★ **它上线后第一次运行就抓到本人刚写的一行**：BL.8 这一格里原本写着一个裸 `\|`（正文里想引用"竖线"本身），GFM 会把它当分隔符 → 该行 4 格 vs 表头 3 格，多出的内容不会渲染 |
---

## 附录 BM · 阶段 5.1 的 S3b：查询串接入契约（外加 5.2 / 5.3 计划）（2026-09-14）

> 逐点记录、仪器改动与变异验证的完整版在 `frontend/docs/openapi-client.md` §15；
> 5.2 / 5.3 的迁移计划在 `frontend/docs/query-and-state-plan.md`。这里记结论与账。

### BM.0 一句话

**请求侧的另一半也接上了**：18 个函数的查询串从"手拼字符串"改成
"`QueryOf<P,M>` 类型标注 + 一个共享序列化器 `buildQuery`"，
参数名从此**在编译期被检查**（此前只有漂移脚本事后扫字符串）。
难点不在那 18 处（编译器与 e2e 都会拦），而在**仪器**：
换写法之后漂移脚本的"路径渲染"与"参数名扫描"**同时失明**，
而失明的表现是"报错项一直是 0"。

### BM.1 改了什么

| 项 | 内容 |
|---|---|
| 新增 | `frontend/src/api/query.ts`（运行时）+ `query.test.ts`（8 例锁语义） |
| 语义 7 条 | 键序=书写顺序；`undefined/null/''` 跳过；`false` **照发**（"不发 false"由调用点显式表达）；数组→重复键；编码交给 `URLSearchParams`；无参数返回 `''`；不支持的取值类型**抛错**（不静默发 `[object Object]`） |
| 转换 | **18 处 / 9 个模块**：report 1、tasks 1、goals 1、graph 1、knowledge 2、notes 6、projects 1、qa 2、review 3 |
| 签名 | 导出名、参数表、运行时路径字面量**一律未变**；`getBlindSpots` / `getMasteryOverview` 的对象入参改成契约类型（对外变宽，调用方零改动） |
| 类型检查在哪 | **调用点**（`QueryOf` 对象字面量写错键 → TS2561）；`buildQuery` 刻意宽松，且**不做运行时校验**（schema 只在类型层） |

### BM.2 ★ 仪器必须同批升级（三处 + 两个新分母）

| # | 位置 | 换写法后为什么失效 | 改法 |
|---|---|---|---|
| 1 | `renderPath()` | `${buildQuery(q)}` 是 **CallExpression**，旧的"标识符型查询后缀"判据不认 → 模板被渲染成 `/notes{}` | 查询 helper 调用与标识符后缀同样处理（`hadQuery = true` 并 `break`） |
| 2 | `isQuerySuffix()` | 同上（`const q = buildQuery(…)` 再插值） | 初始化来自 helper 调用也算 |
| 3 | `collectQueryNames()` | 参数名搬进 helper 的**实参**，且实参常常是**标识符** | 新增 `objectLiteralKeys()`：字面量直接收键，**标识符则回函数体解析初始化式**；三条老通道全保留（`upload.ts` 的 17 个 FormData 字段靠它） |
| 分母 | `queryNameDetections`（54）/ `functionsWithQueryNames`（22） | 只看 `unknownQueryParams` 时，"扫不到"与"没写错"长得一样 | 进 `drift-delta.mjs` 的 `MUST_NOT_CHANGE` |

### BM.3 两次变异验证（守卫真的会咬）

**M1 是真实踩到的，不是设计的**：仪器第一版只认"实参就是对象字面量"，
而迁移后的真实写法是"先存变量再传"。跑漂移立刻触发：

| 指标 | 正常 | M1（不认标识符实参） | M2（人为清空 helper 集合） |
|---|---:|---:|---:|
| `pathMatched` / `pathMissing` | 111 / 0 | 111 / 0 | **93 / 18** |
| `identical` | 105 | 105 | **88** |
| `verdictRegressions` | 0 | 0 | **18** |
| `queryNameDetections` / `functionsWithQueryNames` | 54 / 22 | **17 / 4** | **17 / 4** |
| `missingRequiredQuery` | 0 | **3** | —— |
| `unusedSchemaQuery`（信息项） | 3 | **40** | —— |
| `drift-delta` 退出码 | 0 | **1** | **1**（6 项被点名） |

M1 那行里最值得记的是：**报错项（`unknownQueryParams`）始终是 0**，
只有信息项变多 —— 没有分母的话，这一轮会被读成"参数没写错"。
还原后复跑：`pathMatched 111 / pathMissing 0 / identical 105 / 54 / 22`，退出码 0。

### BM.4 透传变量的代价（为什么两个函数把键名逐个写出来）

`getBlindSpots` / `getMasteryOverview` 的入参本身就是契约类型，
最自然是 `buildQuery(params)` 透传 —— 但扫描器只能看语法、拿不到类型：

| 指标 | 键名逐个写出（采用） | 透传变量 |
|---|---:|---:|
| `queryNameDetections` | 54 | **47** |
| `functionsWithQueryNames` | 22 | **20** |
| `unusedSchemaQuery` | 3 | **10** |

所以这两个函数在体内把 4 个 / 3 个键逐字写出来（仍带 `QueryOf` 标注）。
这条取舍写进了代码注释，避免后人"顺手简化成透传"。

### BM.5 这一轮的账（全部真跑）

| 命令 | 结果 |
|---|---|
| `npx tsc --noEmit` | 退出 0 |
| `npm test` | **23 files / 288 tests**（+8 例即 `query.test.ts`） |
| `npm run lint` | 退出 0（中途被 `no-constant-binary-expression` 拦了一次，已改成经函数参数传入） |
| `npm run build` | 退出 0（✓ built in 5.40 s） |
| `npm run e2e` | **10 passed** |
| `npm run e2e:full`（真实后端 + 真实 LLM） | **6 passed**（1.8 min）——改的是 URL，这一层是唯一真证据 |
| 漂移 + `drift-delta` | 退出 0；`111 / 105 / 54 / 22 / unused 3 / unknown 0 / missingReq 0 / bodyChecked 34` **与迁移前逐项相同** |
| 变异 M1 / M2 | 见 BM.3，跑完即还原并复跑确认回到基线 |

### BM.6 5.2 / 5.3 的计划已出（**未动代码、未装依赖**）

`frontend/docs/query-and-state-plan.md`：现状盘点（**28 个文件在 `useEffect` 里取数**、
3 处轮询、2 个 context、23 个测试文件、无共享 render 助手）、4 个批次与每批验收、
必须保持不变的 5 条不变量（401 单飞 / 错误码分流 / 请求顺序断言 / 文案与 `aria-*` / 卸载取消）、
行为变化清单，以及 5.3 的三块范围。
**四件事等你定**：① 批准两个运行时依赖吗；② `QueryClient` 默认值；
③ `AuthContext` 迁不迁；④ 计划原文的"页面行数减半"改成三条可测口径。

### BM.7 悬着的小事（下一轮逐条核对）

| # | 事项 | 现状 / 下一步 |
|---|---|---|
| 1 | **S5（5.2 / 5.3）** | 计划已出、**未动手**；等 BM.6 的四个决定 |
| 2 | P3：`POST /api/auth/logout` 的 body 是否真的可选 | 未动（前端与契约一致） |
| 3 | `uploadRequest` / `askQuestionStream` 的 `ApiError.message` 是否统一 | 未动（行为变更，需单独决策） |
| 4 | 清空字段的服务层语义 | 未动（`project_service.py:196-199` / `understanding.py:465-468` / `knowledge.py:180-183` 三处 `if x is not None`） |
| 5 | RAG 向量通道 `hybrid` 的成因 | 未定论；判定方法见 BL.8 第 6 条 |
| 6 | 限流按 IP 计数、`requirements.txt` 约束策略、扫描是否阻断 | 按你的口径挂起（BL.5） |
| 7 | 3 条"契约有、UI 没传"的查询参数 | 仍未接（`project_id` / `target_categories` / `target_difficulty`）——属产品功能 |

---

**文档版本**：v5.8（**2026-09-14 阶段 5.1 的 S3b**：18 个函数的查询串接入契约
（参数名改为**编译期**检查），`buildQuery` 语义 8 例锁住，漂移脚本的**三处仪器**
同批升级并新增两个分母（54 / 22）进 `MUST_NOT_CHANGE`，两次变异验证证明守卫会咬
（人手清空 helper 集合：`pathMatched 111 → 93`、`identical 105 → 88`、退出码 1）；
`e2e` 10 + `e2e:full` 6（真实后端 + 真实 LLM）通过；**5.2 / 5.3 的计划已出、代码未动**，
见**附录 BM** 与 `frontend/docs/query-and-state-plan.md`）；
v5.7 —— **2026-09-14 阶段 5.1 的 S3a / S4 落地，P4 接进 CI**：
34 个 JSON 请求体改为按端点从契约派生（`bodyFindings 25 → 0`、`hwDiscardsBody 1 → 0`、
`compilerDiagnostics 2 → 0`、`identical 104 → 105`，而**分母 111 / 34 一个没动**）；
`client.ts` 三处重复的错误解析收成一处；`dump_openapi.py --check` 与 `gen:api`
幂等性两条**配对**步骤进 CI；漂移脚本的空转守卫从"0 诊断"改成"金丝雀必须报错"；
表格缺陷扫描成为常驻脚本（独立复现 BK.6 的 237 张表 / 0 缺陷）；两条产品发现
（清空字段前后端各一道闸、`updateGoal` 零调用方）与三条挂起决策，见**附录 BL**）；
v5.6 —— **2026-09-14 状态审计收口**：阶段 0 的 **0.2 / 0.10 / 0.11 三条落地**并就地更正状态
（✅14 / 🟡1 / ⏸0 / ⛔2）、`AppError` 响应**缺 CORS 头**这条可复现事实的登记、
表格扫描判据补成**两个方向**、评测脚本"空语料 = 退出码 2"，见**附录 BK**；
**2026-09-14 状态缺口回填**：阶段 0 全 17 条补齐 `状态` 列并逐条给依据、
阶段 1 的 1.1–1.5 与阶段 3 的 3.10/3.11 补记现状、`llm_calls.prompt_version` 缺口登记、
阶段 7 五条只登记不加状态列，见**附录 BJ**；
阶段 2、3、阶段 4 全部，阶段 5 的 5.1（第一期）/ 5.5 / 5.10 / 5.11 / 5.13
与 5.9 的审计 / 修复 / 覆盖 / 收尾四轮（5.9 与 5.13 至此都已收尾），阶段 6 的 6.1 / 6.2 / 6.3 / 6.4 / 6.5 / 6.6 / 6.8
见附录 J–AV 与 BA；
FSRS 见 W，到期时刻策略见 X，掌握度曲线见 Y，前端接线见 Z，
卡片复习 UI 见 AA，前端测试框架见 AB，LLM 记账见 AC，配额见 AD，
卡片入库门见 AE，响应缓存见 AF，LLM 网关见 AG，分桶限流与任务循环见 AH，
提示词与场景拆分见 AI，提示词版本化见 AJ，环境开关解耦见 AK，
账本清理见 AL，文档一致性见 AM，结构化输出校验见 AN，任务进度 UI 见 AO，
NoteDetail 安全网见 AP，错误泄露与安全姿态见 AQ，上传安全护栏见 AR，
恢复演练见 AS，密码策略与登录时序见 AT，限流覆盖见 AU，存储审计调度见 AV，
对象存储快照见 AW，NoteDetail 拆分见 AX，临时库清理见 AY，
两页安全网见 AZ，刷新令牌与登出撤销见 BA，两页拆分见 BB，移动端见 BC，
契约漂移可见化见 BD，真实库清理与依赖扫描/端到端见 BE，
真库第二轮清理与依赖漂移可见化见 BF，可访问性审计与覆盖轮见 BG，
从 OpenAPI 生成客户端与 111 个手写函数的分歧见 BH，
5.9 / 5.13 收尾与两条产品发现见 BI，
状态缺口回填（阶段 0 全 17 条 / 1.1–1.5 / 3.10–3.11 / `llm_calls.prompt_version`）见 BJ，
状态审计的收口（阶段 0 的 0.2 / 0.10 / 0.11 三条、`AppError` 响应的 CORS 头缺失、
表格扫描判据的两个方向、评测脚本空语料 = 退出码 2）见 BK，
阶段 5.1 的 S3a / S4 落地与 P4 接进 CI（34 个请求体接入契约、`client.ts` 收敛、
空转守卫改成金丝雀、表格扫描成常驻脚本）见 BL，
阶段 5.1 的 S3b（18 个查询串接入契约、仪器三处升级与两次变异验证）与
5.2 / 5.3 计划的产出见 BM）














**撰写依据**：`backend/app`（约 20,000 行）与 `frontend/src`（约 16,000 行）逐行审计；
`backend/data/db/engramnote.db` 与 `backend/data/models/*` 现场实测；
22 次提交历史；`docs/architecture.md`、`docs/decisions.md`、`README.md`、`参赛/参赛贴文.md`
**核心判断**：保留产品命题，替换全部承重结构。
**争议最小的三步**：**阶段 0（止血）→ 阶段 1（换地基）→ 阶段 3（重写学习核心）**。
**附录 A 的 1058 行 `interval=1` 是全案最强的一条证据：
它证明这个产品的核心功能在当前实现下从未真正运作过。**
