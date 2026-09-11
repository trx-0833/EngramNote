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
- `backend/` 根目录 15 个一次性脚本

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
- 这**直接阻断了 §阶段 5 的"从 OpenAPI 生成前端客户端"计划**

**修复方向**：改用 `fastapi.security.HTTPBearer`，为受保护路由统一声明
`responses={401: ..., 403: ...}`。**这是阶段 5 的前置条件。**

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
| **前端类型** | 手写 90 个 API 函数 + 手写类型 | **从后端的 OpenAPI 生成客户端与类型**（`openapi-typescript` / `orval`） | 消除契约漂移 |
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

| # | 动作 | 解决 | 验收 |
|---|---|---|---|
| 0.1 | **加 CI**：GitHub Actions 跑 `ruff` + `eslint` + `pytest -m "not integration"` | E-6 | 每个 PR 必过 |
| 0.2 | **测试隔离**：`conftest.py` 加守卫，禁止测试触网/写生产库；把 `backend/` 根目录 15 个脚本移入 `scripts/dev/`；`backend/tests/` 只留正式测试 | E-6 | `pytest` 离线可跑、零 API 调用 |
| 0.3 | **关掉启动时的破坏性迁移**：`init_db()` 中 `_rebuild_dangling_tables()` 与 `database.py:580-587` 的全局去重改为**显式脚本 + 先备份**，不再随进程启动执行 | D-2 | 启动不再 DROP 表、不再删数据 |
| 0.4 | **SQLite 开 WAL**：`_set_sqlite_pragma` 加 `PRAGMA journal_mode=WAL`（实测当前为 `delete`）；`busy_timeout` 从 5000 提到 30000 | D-1 | 并发读写不再报 locked |
| 0.5 | **修正向量相似度公式**：`embedding_tasks.py:246` 的 `1/(1+distance)` 在归一化向量上等价于错误的度量（无关内容得 0.333，见 A-1）。collection 建时指定 `hnsw:space=cosine` 并改 `1.0 - distance`；**写一个重建全部 collection 的迁移脚本**（当前 `backend/data/chroma/` 有 90+ 个） | A-1 | 相似度落在 [0,1] 且可区分 |
| 0.6 | **停止删除 `review_logs`**：删除孤儿清理中针对 `review_logs` 的两条 SQL；并把 `generate_questions` 的"先删后建"改为**影子生成 + 原子替换**（见 §2.6 M-5） | L-3 / D-3 / M-5 | 学习记录不再被删 |
| 0.7 | **限流兜底**：`/auth/login`、`/auth/register` 加 IP 级限流；加登录失败计数与锁定；补恒定时间比较（用户不存在时也跑一次 bcrypt） | E-1 / E-2 | 爆破与用户枚举被阻断 |
| 0.8 | **停止用户枚举**：注册对已占用邮箱/用户名返回统一文案 | E-2 | 无法枚举已注册用户 |
| 0.9 | **`debug` 默认改为 `False`**；`.env.example:113` 同步；`echo` 拆成独立开关 `db_echo`（当前 `debug=True` 把 bcrypt 哈希与全部学习内容写进明文日志，见 A-19） | E-5 / A-19 | 生产日志无用户内容 |
| 0.10 | **`/ready` 端点 + 队列深度**；`/docs`、`/openapi.json` 生产关闭或加保护 | E-7 | 可判断服务是否可用 |
| 0.11 | **统一错误契约**：引入 `AppError(code, http_status, message)`，前端改按 `error_code` 分支（不再匹配中文，见 F-19）；停止用 `str(e)` 回传内部异常（H5） | E-10 | 后端改文案不破坏前端 |
| 0.12 | **修 nginx 生产配置**：`client_max_body_size 500m`（当前默认 1MB → **所有 >1MB 上传必然 413**）、`proxy_buffering off`（当前 SSE 被缓冲 → 流式失效）、`gzip_vary on`、静态资源缓存头 + `index.html no-cache`、安全响应头 | F-1 / F-21 | Docker 部署下上传与流式可用 |
| 0.13 | **加 `frontend/.dockerignore`**（`node_modules`、`dist`）；`Dockerfile` 改 `npm ci` | F-4 | 镜像可在 Linux/CI 上构建 |
| 0.14 | **加 ErrorBoundary**（`main.tsx` 全局 + 路由级 `key={pathname}`）；`renderMarkdown` 调用包 `useMemo` 与 try/catch 兜底 | F-2 | 单条坏数据不再整站白屏 |
| 0.15 | **`QuestionSets` 分页循环加硬上限**：`page <= MAX_PAGES` + `if (data.items.length === 0) break` | F-3 | 不再无限请求后端 |
| 0.16 | **`DailyMaterials` 轮询加清理**（`pollTimerRef` + effect cleanup 的 `cancelled` 标志） | F-5 | 卸载后不再发请求 |
| 0.17 | **补 CSS `.status-learning` / `.status-learning-failed` / `.status-archived`**，统一 `statusClass()` 函数（当前四个页面渲染成无样式裸文本，见 F-13） | F-13 | 状态样式全站一致 |

**阶段 0 的价值**：不改变架构，但把"随时可能丢数据/被爆破/静默失效"的风险按下去，
为后续重构赢得时间。

### 阶段 1 · 换地基（2 周）—— 手术刀 1

> ⚠️ **本表原为 PostgreSQL 路线设计，其中 1.1–1.5 依赖 Docker/PG，在本项目
> 已定路线（SQLite 单机，见 `docs/sqlite-single-writer.md`）下**不执行**。
> 与存储无关的 1.6–1.14 已通过 SQLite 路线落地，状态逐行标注如下。
> 将来若迁 PG，本表依然有效。

| # | 动作 | 验收 | 状态 |
|---|---|---|---|
| 1.1 | 引入 PostgreSQL：`docker-compose` 启用 postgres，`DATABASE_URL` 切 `postgresql+asyncpg://` | 全部测试在 PG 上通过 | ⛔ 不执行（无 Docker） |
| 1.2 | **删除手写迁移**：移除 `_migrate_sqlite()` / `_rebuild_dangling_tables()`（约 -400 行）；`init_db()` 只做连通性检查，**不再建表、不再改 schema** | 启动不再改 schema | ⛔ 不执行（手写迁移是 SQLite 路线的唯一通道）。但**启动零破坏性**已达成：重建函数已移出启动路径，见 1.2′ |
| 1.3 | ⚠️ **Alembic 从零重建**。现有迁移链（001→010）**从未生效过**（见 §2.6 M-1） | 空库 → `alembic upgrade head` → 全表 + `alembic_version` | ⛔ 不执行（无 PG）。**但 M-1 的结论仍成立**：迁移链确实从未生效，schema 由手写迁移维持 |
| 1.4 | **数据迁移脚本**：SQLite → PG（含 chroma → pgvector 的向量搬运） | 迁移后行数/内容哈希一致；项目归属不丢 | ⛔ 不执行 |
| 1.5 | 引入 Redis：Celery broker/backend 切 Redis | 多 worker 并发跑任务且不 OOM | ⛔ 不执行。**但"每 worker 加载一份 2.3GB 嵌入模型"的约束已写进 `sqlite-single-writer.md`**（`-c 1`） |
| 1.6 | **任务可靠性**：全局 `task_reject_on_worker_lost=True`、`task_time_limit`、`soft_time_limit` | 杀 worker → 任务自动重投 | ✅ 已落地（附录 F.2；文件 broker 无 visibility timeout，改用 DB 心跳自愈补偿） |
| 1.7 | 新增 `task_runs` 表 + 进度上报 + `GET /api/tasks/{id}` + 取消接口 | UI 显示真实百分比 | ✅ 已落地（附录 F.2，含 IDOR 校验与诚实的 `terminated=false`） |
| 1.8 | **僵尸任务自愈**：Beat 定时扫描 `heartbeat_at` 超时的 `task_runs` | worker 崩溃后笔记不再永久卡住 | ✅ 已落地（Beat 每 5 分钟） |
| 1.9 | **worker 启动时校验 schema** | worker 独立启动也能正常工作或明确报错 | ✅ 已落地（`_ensure_worker_schema()`） |
| 1.10 | **修正 Celery broker 目录推导** | broker 目录与 `config.py:397` 保持一致 | ✅ 已落地（收敛为 `get_celery_broker_dir()`） |
| 1.11 | 新增 `vault_files` 表 + 一致性校验任务（比对磁盘与 DB 的 sha256） | 可检测并修复发散 | ✅ **已落地**（附录 I）：`vault_audit_service` + `verify_vault.py`（14 用例）。**未建 `vault_files` 表** —— 改为每次扫盘，避免引入第三份需同步的真相源；且**只报告不修改**（自动"修复"会把误判变成不可逆删除） |
| 1.12 | ~~**消除双写**：统一"先写文件成功 → 再 commit DB → 失败则补偿回滚"的顺序~~ | ✅ **已排查并修复真实缺陷**（§2.6 M-10 修正框）：删除失败改为有界重试；持续失败仍删 DB 记录（不让用户卡死） |
| 1.13 | ~~**修复物理删除笔记的 FK 违约**（见 §2.6 M-4）~~ | ✅ **实测证伪：该缺陷不存在**（见 §2.6 M-4 修正框）。已补 `tests/test_purge_note_integrity.py`（5 用例）锁住正确行为，防止将来给 UPDATE 加 `note_id` 限定而真的引入它 |
| 1.14 | **修复 `card_relations` 唯一索引**（见 §2.6 M-3） | 索引确定存在 | ✅ 已落地（附录 D；去重口径统一为 5 列） |

**1.2′ 补充（SQLite 路线下的"启动零破坏性"）**：
`_rebuild_dangling_tables()` 已移出启动路径（破坏性变更需显式闸门），
但**它此后没有任何调用入口** —— 本轮实测因此导致 `review_logs.quiz_id`
的可空迁移从未执行。已新增 `scripts/reconcile_schema.py` 作为显式、
自动备份、逐项校验、幂等的入口（见附录 G.4）。

**阶段 1 之后**：D-1 / D-2 / D-7 / D-8 / D-11 / M-1 / M-4 / M-6 / M-12 消除。
（在 SQLite 路线下：D-1/D-2/D-7/D-11/M-6 已消除；M-1 已定性；
M-4 与 1.13 仍未做。）

### 阶段 2 · 重建检索层（2 周）—— 手术刀 3

> ⚠️ **本表原为 PostgreSQL 路线设计。** 在本项目已定路线
> （SQLite 单机，见 `docs/sqlite-single-writer.md`）下：
> **2.2（`chunks` 表 + pgvector）、2.5（`pg_bigm`）不执行** —— 它们依赖 PG 扩展，
> 在 SQLite 上没有对应物。SQLite 路线的等价物见下方「阶段 2′ 的 SQLite 等价项」。
> 其余各项与存储无关，已按 SQLite 路线落地，状态逐行标注。

| # | 动作 | 验收 | 状态 |
|---|---|---|---|
| 2.1 | **统一分块**：`markdown_segmenter` 成为唯一实现；删除 `cleaning_service.split_into_chunks`（-250 行） | 同一文档全链路同一套 chunk | ⬜ 待做（见下方风险说明） |
| 2.2 | 新增 `chunks` 表 + `pgvector` 列（HNSW, `vector_cosine_ops`） + `tsvector` 列 | 一次迁移建好 | ⛔ 不执行（无 PG） |
| 2.3 | 索引源改为**清洗后的原文 Markdown**（不再索引卡片） | 检索命中原文段落 | ⬜ 待做（先要 2.9 的基线） |
| 2.4 | 删除 Chroma 依赖与 90+ collection 目录；删除跨 collection 遍历逻辑（`embedding_tasks.py:196-262`） | 查询从 N 次降到 1 次 SQL | ⬜ 待做 |
| 2.5 | 中文全文检索用 `pg_bigm`（或 `zhparser`），替换纯 Python BM25 | 中文召回质量可测 | ⛔ 不执行（无 PG）。SQLite 等价物是 **FTS5**，见下方 |
| 2.6 | **删除 n-gram 通道**；RRF 改为加权融合（向量 / BM25 可配权重） | 少一路噪声 | ✅ **已落地**（附录 J）：通道已删；RRF 现只融合两路。加权可配留待 2.9 的基线给出权重依据 |
| 2.7 | **引用可回跳**：chunk 存 `char_start/char_end/heading_path`；前端点击引用 → 定位并高亮 | 引用能跳到段落 | ⬜ 待做 |
| 2.8 | **重写 RAG 提示词**：强制"仅依据给定资料"；无据则明确回答"资料中没有"；要求逐条标注引用编号 | 无据问题不再被编造 | ✅ 已落地（提交 `5da6d8a`） |
| 2.9 | **检索质量评测集**：造 50 条 (问题, 期望命中 chunk) 的离线评测，纳入 CI | Recall@5 / MRR 有基线 | ⬜ 待做（**必须先于 2.3/2.4**） |
| 2.10 | 删除 `_kb_cache` 模块级无界缓存 | 内存不随用户增长 | ✅ **已落地**（附录 J）：缓存与失效入口一并删除，语料改为按需查询 |

#### 阶段 2′ 的 SQLite 等价项（替代 2.2 / 2.5）

| # | 动作 | 目的 |
|---|---|---|
| 2.2′ | `chunks` 表（纯 SQLite，无扩展）承载 chunk 文本 + `char_start/char_end/heading_path` + 向量 BLOB | 支撑 2.7 回跳与 2.3 语料切换 |
| 2.5′ | **FTS5** 全文索引（SQLite 内置）替换纯 Python BM25 | 免维护索引；这是 A-5 在 SQLite 路线下的正解 |
| 2.4′ | 评估 `sqlite-vec` 扩展替代 Chroma 的多 collection | 消除"每篇笔记一个 collection、检索要遍历全部" |

#### 2.1 为什么不是"删掉一个函数"那么简单

原表把它写成一处删除（-250 行），实测发现**两个分块器的职责不同、不能直接砍掉任一个**：

| | `cleaning_service.split_into_chunks` | `markdown_segmenter` |
|---|---|---|
| 产出 | `{index, content, start_line, end_line, heading_context}` | 纯文本段列表 |
| 用途 | **检索 chunk**（`clean_tasks.py:224` → 嵌入 → Chroma） | **LLM 抽取的填充边界**（`understanding_service.py:166`） |
| 特性 | 段落级、带 overlap、带标题路径与行号 | 结构块原子性（不切破表格/代码块/列表） |

所以真正的目标是**一个同时具备两侧能力的 chunker**：结构块原子性
（`markdown_segmenter` 的强项）+ 字符偏移 / 标题路径 / 可选 overlap
（`split_into_chunks` 的强项，也是 2.7 回跳的前提）。
直接删掉 `split_into_chunks` 会连带丢掉 2.7 所需的定位信息，
而直接删掉 `markdown_segmenter` 会让抽取重新截断表格与代码块。

**顺序约束**：2.1 必须先做（2.1 → 2.7 → 2.3），而 2.9 的评测集要先于 2.3/2.4 ——
否则"卡片语料换成原文语料"这件事**无法判断是变好还是变差**。

**阶段 2 之后**：A-1 / A-2 / A-4 / A-5 / A-6 / A-7 / A-9 / A-14 / D-9 / D-10 全部消除。
**产品核心承诺（"基于你的资料回答，可追溯"）首次成立。**

### 阶段 3 · 重写学习核心（3 周）—— 手术刀 2 + 4

> **这是本项目最有价值、也最被低估的一段。** 建议单独排期，不要与其他阶段并行。

| # | 动作 | 验收 |
|---|---|---|
| 3.1 | 新增 `review_states` 表；把 `interval/repetition/EF/next_review_at` 从 `quiz_items` 迁出 | 数据无损迁移 |
| 3.2 | `review_logs` 重建为不可变事件流（加 `rating`、`predicted_retention`、`item_type`） | 历史日志迁移保留 |
| 3.3 | **删除字符 n-gram 判分**（`_score_short_answer` 整段移除） | 随机文本不再得高分 |
| 3.4 | **引入用户自评四档**（Again/Hard/Good/Easy）作为主评分来源 | UI 有四个按钮 |
| 3.5 | **简答题 LLM 语义判分**：输出 `verdict + missing_points + misconceptions`，不是 0-100 分 | 答错时给出具体缺失点 |
| 3.6 | **把 SM-2 换成 FSRS**（含 `stability`/`difficulty` 建模） | 同等保持率下复习量下降 |
| 3.7 | **修正 `next_review_at` 基准**：以计划到期日为基准（非 now）；到期时间对齐用户学习时段；加 fuzz | 间隔不被提前复习缩短 |
| 3.8 | **leech 检测**：`lapses >= 8` → 标记并要求重写卡片 | 顽固卡片被识别 |
| 3.9 | **修掉 `mastery` 公式**：改为基于 FSRS `retrievability` 的"当前可回忆概率"，随时间自然衰减；范围真实覆盖 [0,1] | 3 个月未复习的卡片掌握度下降 |
| 3.10 | **新卡与复习卡分开限额**（`new_per_day` / `review_per_day` 可配） | 到期队列收敛 |
| 3.11 | **薄弱点排序加时间衰减** + overdue 权重 | 老错误不再霸榜 |
| 3.12 | **卡片可直接复习**（`item_type='card'`），不再依赖是否出过题 | 死卡片消失 |
| 3.13 | **复习页显示原文上下文**：答错时一键展开 `source_quote` 与原文段落 | 答错能立刻回看原文 |
| 3.14 | **度量层**：保持率曲线、校准曲线、lapse 分布、FSRS 参数拟合报表 | 用户能看到"我 30 天保持率 87%" |

**阶段 3 之后**：L-1 … L-8 全部消除。
**这是产品从"看起来很酷的 demo"变成"真的能帮助记忆的工具"的分水岭。**

### 阶段 4 · LLM 网关与成本治理（1.5 周）

| # | 动作 | 验收 |
|---|---|---|
| 4.1 | 抽出独立 `LLMGateway`：统一入口、结构化输出校验（Pydantic）、重试/退避/熔断 | `llm_service.py` 从 831 行降到 <300 |
| 4.2 | 新增 `llm_calls` 表：每次调用记账（user/note/scene/model/tokens/cost/latency） | 成本可按用户/笔记/场景聚合 |
| 4.3 | **配额与告警**：用户级 token/金额配额；超限拒绝并给出明确错误码 | 无法无限烧钱 |
| 4.4 | **限流重构**：从"进程级 10 RPM"改为"按用户 + 按供应商"的令牌桶（Redis 实现） | 单用户不饿死他人 |
| 4.5 | **修复事件循环问题**：Celery 任务改为 `asyncio.run()` **一次**包裹整个任务体（而非 6 次）；限流器/信号量改为**每 loop 惰性创建** | 无线程/loop 冲突，无连接泄漏 |
| 4.6 | **Prompt 版本化**：每个 prompt 有 `prompt_version`，写入 cards/quiz_items | 可评估 prompt 改动的效果 |
| 4.7 | **LLM 响应缓存**：相同 (prompt_version, input_hash) 命中缓存，不重复付费 | 重跑理解成本大降 |
| 4.8 | **抽取幂等**：重跑理解时按 `content_hash` 复用已存在的卡片，只补差集 | 重跑不再产生重复卡片 |
| 4.9 | **卡片质量门**：空内容/过短/无 `source_quote` 的卡片不入库；单次抽取数量上限 | 脏卡片无法入库 |
| 4.10 | **修正 `debug` 开关耦合**：拆成 `app_env` / `log_sql` / `llm_provider` 三个独立配置 | 生产可用 DeepSeek 且不打印 SQL |
| 4.11 | 删除 `generate_questions` 中的调试日志（`llm_service.py:634-654`） | 生产日志干净 |

### 阶段 5 · 前端重建（2.5 周）

| # | 动作 | 验收 |
|---|---|---|
| 5.1 | **从 OpenAPI 生成类型与客户端**，删除手写 `client.ts` 的 90 个函数与重复类型 | 前后端契约不可能漂移 |
| 5.2 | 引入 **TanStack Query**：替换手写 fetch + `setInterval` 轮询 | 有缓存/重试/取消/去重 |
| 5.3 | 引入 **Zustand** 管理 UI 状态；消除 prop drilling | 页面组件行数减半 |
| 5.4 | **路由级懒加载**：18 个页面全部 `React.lazy` + 按需分包 | 首屏不含 force-graph/katex |
| 5.5 | 拆分巨型页面：`NoteDetail.tsx`(1030) / `KnowledgeGraph.tsx`(868→文档称 1547) / `Projects.tsx`(728) | 单文件 <300 行 |
| 5.6 | **CSS 体系重建**：14 个全局 CSS → CSS Modules 或 Tailwind + design token 层 | 样式可预测、无覆盖战争 |
| 5.7 | **修 404**：新增真实 404 页面，不再静默重定向到登录页 | 错链有明确提示 |
| 5.8 | **错误/空/加载三态组件化** + 全局 Toast | 无"白屏卡住" |
| 5.9 | **可访问性**：键盘导航、focus trap（模态）、ARIA、对比度 | axe 无 critical |
| 5.10 | **响应式**：`responsive.css` 只有 41 行 → 补齐移动端布局 | 手机可用 |
| 5.11 | **任务进度 UI**：真实进度条 + 阶段名 + 取消按钮 | 取代"转圈" |
| 5.12 | **复习 UI 重做**：四档自评 + 答错展开原文 + 引用回跳 | 核心流程体验 |
| 5.13 | **Vitest + Playwright**：核心流程 E2E（上传→理解→复习→问答） | CI 里有 E2E |

### 阶段 6 · 安全与合规收口（1 周）

| # | 动作 | 验收 |
|---|---|---|
| 6.1 | 密码策略（长度/复杂度）+ bcrypt cost 提升 | 弱密码被拒 |
| 6.2 | 恒定时间登录（邮箱不存在也跑一次 bcrypt）；注册不再泄露存在性 | 时间侧信道关闭 |
| 6.3 | **Refresh token + 轮换 + `jti` 黑名单 + 登出撤销** | token 可吊销 |
| 6.4 | 上传按 magic bytes 验类型；PDF 页数上限；zip 炸弹防护；单用户文件数上限 | 恶意文件被拒 |
| 6.5 | 全端点补齐用户级 rate limit（尤其 LLM 端点） | 无法脚本刷接口 |
| 6.6 | 备份与恢复：定时 `pg_dump` + 对象存储快照 + 一键恢复演练 | 演练成功 |
| 6.7 | 依赖与镜像扫描（`pip-audit` / `npm audit` / Trivy）纳入 CI | 无高危 CVE |
| 6.8 | 错误响应不再泄露内部路径/SQL；生产 `debug=False` 断言 | 无信息泄露 |

### 阶段 7 · 效果验证与产品化（1 周）

| # | 动作 |
|---|---|
| 7.1 | 灰度：老库只读 + 新库并行，双跑 2 周验证一致性 |
| 7.2 | 真实用户 A/B：FSRS vs SM-2、LLM 判分 vs 自评，用保持率作为指标 |
| 7.3 | 用真实数据拟合 FSRS 个人化参数，发布"你的记忆模型"页面 |
| 7.4 | 发布"学习效果报告"：保持率、校准、薄弱领域、卡片质量榜 |
| 7.5 | 文档重写：`architecture.md` 从零重写，删除全部 `F-xx` 引用与归档文档 |

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
| 7 | **SM-2 自实现** | `sm2_service.py` 的调度部分 | 换 FSRS；判分部分保留但重写 |
| 8 | **`debug` 单一开关** | `config.debug` 同时控 SQL 日志 / FastAPI debug / LLM 供应商 | 拆成三个 |
| 9 | **`_kb_cache` 模块级无界缓存** | `rag_service.py:47` | 有 DB 索引后不需要；无界字典是内存泄漏 |
| 10 | **`client.ts` 手写 90 个 API 函数 + 手写类型** | 前端 | 从 OpenAPI 生成 |
| 11 | **14 个全局 CSS 文件** | 约 2700 行 | 无作用域、互相覆盖、补丁式追加 |
| 12 | **`backend/` 根目录 15 个一次性脚本** | `test*.py` / `verify_clean.py` / `e2e_cleanup.py` / `reset_cleaning.py` / `restore_note.py` 等 | 移入 `scripts/dev/` 并加环境守卫，或直接删 |
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
| 打分 | `IDF × tf×(k1+1) / (tf + k1×(1-b+b×|D|/avgdl))` | `Σ 命中子串长度` |
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
已在正文阶段 2 补上顺序约束：**2.1 → 2.7 → 2.3，且 2.9 必须先于 2.3/2.4** ——
否则"卡片语料换成原文语料"无法判断是变好还是变差。

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

---

**文档版本**：v1.4（M-4/M-10 复核见附录 H）
**撰写依据**：`backend/app`（约 20,000 行）与 `frontend/src`（约 16,000 行）逐行审计；
`backend/data/db/engramnote.db` 与 `backend/data/models/*` 现场实测；
22 次提交历史；`docs/architecture.md`、`docs/decisions.md`、`README.md`、`参赛/参赛贴文.md`
**核心判断**：保留产品命题，替换全部承重结构。
**争议最小的三步**：**阶段 0（止血）→ 阶段 1（换地基）→ 阶段 3（重写学习核心）**。
**附录 A 的 1058 行 `interval=1` 是全案最强的一条证据：
它证明这个产品的核心功能在当前实现下从未真正运作过。**
