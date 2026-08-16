# EngramNote 深度审查报告（Bug / 重设计 / 去重）

> 审查日期：2026-08 · 范围：`D:\engramnote` 全部源码（后端 50 文件 + 前端 38 文件 + 部署脚本）
> 方法：16 路并行子代理分区精读（后端 9 区 + 前端 5 区 + 交叉普查 + 部署脚本），关键发现经主代理逐条代码复核。
> 结论：**Request Changes**。核心学习闭环可用，但存在 6 个 critical（含 2 处静默数据丢失）、约 30 个 high，且 FIX_PLAN.md 中 7 项"已修复"声明经核实并未落地。

---

## 一、已确认 Bug（按严重度）

### Critical（数据丢失 / 安全 / 核心功能失效）

| # | 位置 | 问题 |
|---|------|------|
| C-1 | `backend/app/database.py:455-467` | **每次启动都会执行的破坏性去重 SQL**。`card_relations` 按 `(user_id, 小id, 大id)` 分组只保留 `MIN(id)`，未区分 `relation_type`（related/prerequisite/contrast）与方向。`prerequisite(A,B)` 与 `prerequisite(B,A)`（语义相反）或同一对卡的不同类型关系会被当成"重复"**静默永久删除**。FIX_PLAN.md:39 声称"保留不同方向/不同类型的关系"，代码未实现。**已亲自验证**。 |
| C-2 | `backend/app/api/understanding.py:69-134` | **archived 笔记"开始学习"无 confirm 即清空全部学习成果**。FIX_PLAN.md:24 声称"重新理解 archived 笔记必须 confirm=true"，但接口签名无 confirm 参数（仅 `note_id`），archived 状态直接放行并删除 ReviewLog/CardRelation/QuizItem/KnowledgeCard 后 commit。前端 `startUnderstanding`（client.ts:912）也未传 confirm、无二次确认 UI。用户误点即永久丢失全部卡片/题目/复习记录。**已亲自验证**。 |
| C-3 | `backend/app/tasks/celery_app.py:91` vs `reminder_tasks.py:86` | **Beat 定时任务名不匹配，00:30 目标进度刷新永不执行**。Beat 调度引用 `app.tasks.reminder_tasks.refresh_goal_progress_task`，实际注册名是 `app.tasks.reminder_tasks.refresh_goal_progress`（差 `_task` 后缀），Celery 报 Unknown task。`send_daily_review_email` 名称正确。**已亲自验证**。 |
| C-4 | `frontend/src/utils/markdown.ts:95-97` + `NoteDetail.tsx:776,803`、`LearningAssessment.tsx` 等 8 处 | **存储型 XSS**。`renderMarkdown` 直接 `marked.parse()` 输出，marked v14 无 sanitize，仓库无 DOMPurify；输出经 `dangerouslySetInnerHTML` 注入。笔记内容来自上传文档/AI 清洗/用户编辑，含 `<img onerror>`/`<script>` 即执行。**已亲自验证**。 |
| C-5 | `backend/app/services/llm_service.py:479,573` + `425-426` | **LLM 基建每次调用重建**：`async with httpx.AsyncClient(timeout=120.0)` 每次新建（连接池/ TLS 握手全浪费，旧报告 P-4 未修）；`RateLimiter(10rpm)` 与 `Semaphore(3)` 是实例属性，而 rag/knowledge_link/graph 到处 `LLMService()` 新建，**限流与并发闸门完全不生效**（同进程多实例各持令牌桶）。 |
| C-6 | `backend/app/services/rag_service.py:84,115` | **async 协程内同步 `task.get(timeout=10/15)` 阻塞整个事件循环**。FIX_PLAN.md:37 声称"改线程池等待"，实际未实现；问答期间任何并发请求全部卡死。同文件 `_get_session_factory`（:58-63）自建私有 engine 且从不 dispose，与 FIX_PLAN"复用主数据库会话"不符。**已亲自验证**。 |

### High（明显功能错误 / 数据不一致）

| # | 位置 | 问题 |
|---|------|------|
| H-1 | `cleaning_service.py:496-499` vs `528-534,551-555`；`cleaning.py:291-414` | **"恢复/删除重复块"功能 key 不一致，实为 no-op 且造成元数据-文件不一致**。注释生成用 `duplicate_of`（保留块 index），restore/delete 正则却匹配 `block_{block_index}`（重复块自身 index），两者几乎总不相等 → 正则永远失配，clean.md 未变，但 API 仍把 `duplicates_detail` 中该条目移除并 commit。前端 CleaningPanel 传 `dup.block_index`（:236,244），确认触发。**已亲自验证**。 |
| H-2 | `note_service.py:206-353` + `database.py:44-46` | **删除笔记遗漏 `note_projects` 清理，且 SQLite 外键未开启**。`delete_note` 级联清单无 NoteProject；connect_args 仅 `check_same_thread`，全库无 `PRAGMA foreign_keys=ON`，models 上的 `ondelete='CASCADE'` 全部不生效。删除笔记后 note_projects 孤儿残留，只靠下次启动的兜底清理。**已亲自验证**。 |
| H-3 | `alembic/versions/006_project_tags_refactor.py:51-53` + `alembic.ini:6` | **Alembic 006 在默认 SQLite 上无法执行**：`op.drop_constraint`/`op.drop_column` 未用 batch_alter_table（SQLite 不支持 DROP CONSTRAINT）；`alembic.ini` 硬编码 `postgresql+asyncpg://...`，而 `env.py:35` 用同步 `engine_from_config` 连 async URL（不兼容）。Alembic 轨道实际不可用，与 `_migrate_sqlite` 双轨漂移。**已亲自验证**。 |
| H-4 | `review_service.py:35` vs `Review.tsx:161`、`TodayLearn.tsx:153`、`goal_service.py:39` | **每日限额三处不一致：后端复习接口 10、前端硬编码 50、目标服务 50**。用户答到 10 题后端即 429，UI 仍显示"今日 X/50"进度条按 50 满格。FIX_PLAN"统一为 10"只改了 review_service 一处。**已亲自验证**。 |
| H-5 | `upload.py:310`（folder_id 无校验）+ `folder_service.py:149,212,257` | **文件夹归属校验缺失**（FIX_PLAN 声称已修）：上传的 `folder_id` 不查存在性/所有权；文件夹详情/计数/删除的笔记查询只按 `folder_id` 过滤、未加 `Note.user_id`。配合越权 folder_id 可造成跨用户笔记混入他人文件夹视图。**folder_service 部分已亲自验证**。 |
| H-6 | `api/goals.py:39` + `goal_service.py:109-119,208-211,232-235,515-518` | **学习目标 scope 归属校验缺失（IDOR）**：v1 `create_goal`/`update_goal` 不校验 scope_notes/scope_folders 归属，进度与每日计划查询缺 user_id 过滤。带校验的 `goal_service_v2._validate_goal_scopes` 从未被任何代码 import（v2 是死代码）。 |
| H-7 | `api/goals.py:109-112` | **目标列表 progress_percentage 恒为 0.0**：`_build_goal_response(g)` 不传 progress，仅详情接口实时算。FIX_PLAN"目标列表返回真实进度"未生效。**已亲自验证**。 |
| H-8 | `review_service.py:113-244` | **普通复习提交无"到期校验"**：`submit_answer` 仅查 quiz 归属与每日限额，不校验 `next_review_at<=now`，未到期/已答题目可反复提交改写 SM-2（FIX_PLAN 声称已修，未实现）。同题重复提交无幂等约束（review_logs 无唯一约束），可无限占用配额并任意拉长复习周期。 |
| H-9 | `Upload.tsx:285-291` | **上传轮询终态缺 `cleaning_failed`/`learning_failed`**（旧报告 L-3 仍在）：两状态落入空分支，轮询到 120 次超时误报"转换超时"；同文件 `setInterval`+async 无在途互斥（L-9 只在 DailyMaterials 修复）。DailyMaterials:333 已含终态，两处集合不一致。 |
| H-10 | `start.bat:167` / `start.sh:143` / `docker-compose.yml` | **本地与 Docker 均未启动 Celery Beat**（FIX_PLAN 声称"已增加 worker/beat"）：定时任务（00:30 目标刷新、09:00 提醒邮件）在默认部署下永不执行；compose 无 celery-worker 服务，Docker 部署下所有异步任务（转换/清洗/理解/嵌入）无人消费。**start.bat 已亲自验证**。 |
| H-11 | `rag_service.py`（delete_note 全程） | **删除笔记不清理 Chroma 向量**：`VectorStore.delete_note_chunks` 无任何调用点，`note_{id}` 集合与向量文件成为孤儿，长期累积占磁盘。 |
| H-12 | `embedding_service.py:201-205` | **`HF_HUB_OFFLINE=1` 全局环境变量无保护**：`finally: os.environ.pop(...)` 会误删调用方预设的离线标记，并发加载路径非线程安全。 |
| H-13 | `llm_service.py:493-501` | **重试吞掉所有 4xx**：400/401/403 也走指数退避重试（仅 429 特殊处理），放大失败耗时与额度消耗。另 `llm_service.py:465-468,614-621` DEBUG/INFO 记录完整 prompt 与响应（隐私）。 |
| H-14 | `upload.py:154-199` | **跨扩展名同名 base 导致 Markdown 输出互相覆盖**：`_resolve_unique_base` 的 `_exists` 按 `source_object(prefix, base, ext)`（含扩展名）查重，`a.pdf` 与 `a.md` 判定互不冲突，但两者转换输出同为 `output/markdown/a.md`，后上传者覆盖先上传者的转换结果，两条笔记指向同一文件。并发同名上传还存在 TOCTOU 竞态（os.replace 覆盖源文件）。**已亲自验证**。 |
| H-15 | `graph_service.py:276-278,585` + `llm_service.py`（N+1 等） | 有向关系被当无向去重（`tuple(sorted(...))` 使 `prerequisite(A,B)` 与 `prerequisite(B,A)` 视为同一对，方向相反的关系被静默跳过，L-7 部分未修）；`auto_suggest` 取卡 `limit(200)` 无 ORDER BY，卡片超限时结果游移。 |

### Medium / Low 摘要（代表性）

- **归档状态机不一致**（`notes.py:353-366`）：converted → 归档 → 取消归档直接变 cleaned，但从未清洗（clean_md_path 为空）；cleaning_failed 不允许归档，与 learning_failed 不对称。
- **版本号无唯一约束**（`note_version.py:70` + `version_service.py:88-94`）：`MAX+1` 取号非原子，并发创建版本重号并覆盖存储文件（FIX_PLAN:40"已加唯一约束"未实现）。恢复版本先建快照再读目标（`version_service.py:331-344`），目标缺失时产生多余快照并返回 404；读取失败以空串建快照污染版本历史（:318-329）。
- **SQL 通配符未转义**：`note_service.py:108`、`understanding.py:257-261,660` 的 `ilike(f"%{keyword}%")`，搜索 `%`/`_` 放大匹配（旧报告 S-2 未修）。
- **清洗管道**：`clean_rules` 的页码规则（`^\\s*\\d+\\s*$` 等）无代码块/公式保护（`cleaning_service.py:33-38,96-101`），会删掉代码块里的纯数字行；`stop_cleaning` 存在 late-stop 竞态（任务越过最终检查仍落 cleaned）；成功路径整包替换 `metadata_` 丢失 `clean_task_id` 等旧字段；`/diff` 端点原地修改 `note.metadata_["clean_stats"]` 且缺失时 `.update` 崩溃（`cleaning.py:277-280`）；`_OCR_CORRECTIONS` 定义了从未应用（死代码）。
- **Celery 异步引擎跨事件循环复用**：convert/clean/understand 三个任务模块全局 async engine + 任务内多次 `asyncio.run()`，存在 Event loop is closed / 连接复用隐患。
- **重试导致重复产物**：`understand_tasks.py:447` 任务失败重试时不清除已提交批次的卡片 → 重复卡片；`generate_questions_task` 重复触发累积重复题目（无去重/清旧）；`target_difficulty` 参数完全不生效（`understand_tasks.py:193-221` 从不引用）。
- **评估测验缓存签名缺失 personal_note**（`assessment_service.py:22-28`）：不同 personal_note 命中同一缓存；且生成 prompt 未注入 personal 笔记（FIX_PLAN:36 未实现）；`assessment.py:40,58,78` 仍 `detail=str(e)` 暴露内部异常。
- **卡片去重 N+1 未改**（`understanding.py:419-437`）：每张卡一次 `limit(500)` 全量查询，无 ORDER BY 取任意 500 张，阈值刻度不统一（threshold=5 vs similarity=score/100）。
- **前端 401 处理**（`client.ts:252-255`）：登录/注册失败 401 与令牌过期 401 不区分，误触发全局登出清 token；`request()` 无 AbortController 超时（全项目零 signal）；`request<void>` 对 200 空响应体 `response.json()` 抛错。
- **前端重复提交竞态**：Review/QuickReview/TodayLearn 三页 `handleSubmit` 无 in-flight 锁 → 重复 ReviewLog + SM-2 叠加；LearningAssessment 同。
- **QuestionSets.tsx:44-57** do/while 全量拉页 + 全量渲染（UX-P1 未修）；**KnowledgeCards.tsx:57** pageSize=999 未分页。
- **Dashboard.tsx:48-53** 6 个接口 `.catch(() => null)` 静默吞错（UX-P1 未修且扩大）。
- **ReminderBanner.tsx:52-59** 重复提醒被永久抑制：`markNotified` 后 `notified.length` 恒非 0，`lastNotifiedDueRef` 的按值去重被绕过，整个会话只弹一次通知。
- **视频 Range 解析无边界校验**（`notes.py:651-680`）：start>end 产生负 Content-Length；不支持 suffix range；未命中不返回 416。
- **上传**：commit 阶段 temp_id 无用户归属校验（`upload.py:606-620`，可消费他人暂存文件）；配额校验 TOCTOU；retry_convert 丢失 backend 参数（:766-771）；content_type 用原始 filename（:670）；mineru 分块 PDF 页码标签未按块偏移（`converter.py:182,971`）。
- **注册邮箱未归一化**（`auth_service.py:134-141`）：大小写可撞库；密码仅 min_length=6；JWT 默认密钥弱（`config.py:87`）；`embedding_tasks.py:47` `_hash_to_text_registry` 无界增长（内存泄漏）。
- **error_handler 死代码**（`error_handler.py:47,56`）：BaseHTTPMiddleware 位于 ExceptionMiddleware 外层，HTTPException/ValidationError 分支捕获不到，4xx 响应缺 error_code 字段。
- **时区**：日界一律 UTC（goal/review/notification/report 五处 `now.replace(hour=0)`），与 Celery 的 Asia/Shanghai 不一致，北京时间 08:00 前答题计入前一日。
- **删除项目/文件夹 400 vs 404 语义混用**；`projects.py:169` 删除不存在项目返回 400。

---

## 二、值得重新设计的地方

### 后端

1. **单一数据库引擎/会话工厂 + SQLite FK 治理**（B1）——engine 散落 database.py、4 个任务模块、rag_service、goal_service_v2 共 7 处各自 `create_async_engine`。收敛为唯一工厂，统一 `PRAGMA foreign_keys=ON` + `busy_timeout`，让 ON DELETE CASCADE 真正生效，删掉三份手写级联删除。
2. **迁移轨道合一**（B1）——`_migrate_sqlite` 手写 DDL 与 Alembic 004/006 双轨漂移（已在 card_relations 上造成实际数据丢失）。`init_db` 应检测 `alembic_version` 执行 `upgrade head`，`_migrate_sqlite` 仅作旧库一次性升级；修复 alembic.ini/env.py 的 SQLite 与 async 连接问题。**移除启动期破坏性 DELETE SQL**（card_relations 去重、孤儿清理改为一次性迁移 + 唯一约束）。
3. **全局 LLM 基建**（B8）——LLMService 收敛为受控单例（app.state 注入），httpx.AsyncClient、RateLimiter、Semaphore 提升为进程级共享，timeout 配置化；修复 C-5/C-6（RAG 的 `task.get` 用 `asyncio.to_thread`，`_get_session_factory` 复用主 `async_session`）。
4. **版本号分配改为 DB 唯一约束 + 重试**（B2）——`(note_id, version_number)` UniqueConstraint，storage_path 嵌入 uuid 段；restore/删除的"读当前→建快照→覆盖"流程抽共享 helper 并先校验目标可读。
5. **清洗去重块操作按行区间定位**（B4）——停止正则匹配注释文本的 hack；metadata 记录重复块精确行区间（start_line/end_line），restore/delete 直接按行操作；`metadata_` 更新改 merge 而非整体替换；`clean_rules` 复用 split 的代码块/公式上下文状态机。
6. **统一卡片/题目 API 归属**（B2）——knowledge.py 与 understanding.py 对 KnowledgeCard/QuizItem 大量重叠（list/delete/generate-questions 各一份），按"管理"与"AI 生成"垂直拆分。
7. **关系方向第一公民**（B8）——CardRelation 增加"无向/有向"schema，auto_suggest/suggest_semantic/create_relation 共享同一构建与去重规则，消除 sorted-元组去重的方向歧义。
8. **免打扰后端化**（B6）——`reminder_quiet_hours_*` 配置目前零读取，邮件提醒无静默期；后端按 22:00-08:00 跨天判断，前端 isInQuietHours 仅作补充。
9. **删除/清理统一抽象**（B2/B8）——按外键顺序的多段级联删除三处重复；向量生命周期挂入笔记删除钩子。
10. **每日限额单点配置**（B6/B7）——`DAILY_REVIEW_LIMIT` 进 settings，经 `/review/stats` 下发给前端，删除前端 50 硬编码；区分"复习接口 10"与"目标计划 50"两套语义。
11. **错误处理**（B7/B9）——错误用结构化 code 替代字符串匹配（`"上限" in error`、`msg.includes('每日上限')`）；error_handler 改 `@app.exception_handler` 三件套；API 层停止 `detail=str(e)`。
12. **统一进程编排**（OPS）——抽 `scripts/run_dev.py`（或 Makefile）替代 start.bat/sh 双份：端口预检、按序启动 backend/worker/beat/frontend、日志落文件、PID 清理；compose 补 celery-worker/beat 服务；nginx `/api/` 加 `proxy_buffering off` 支持 SSE。
13. **check_env 原子化下载**（OPS）——模型先落临时目录再 rename，中断可重入；bge-m3 与 torch 一并列为必需；修复"`--check` 全绿但启动崩溃"。

### 前端

14. **`request()` 统一重构**（F1）——AbortController 15-30s 超时、401 按路径区分登录/鉴权、错误中文映射、`request<void>` 空体兼容；5 个内联 FormData 上传块合并。
15. **渲染安全层**（F1/F2）——DOMPurify 集成进 `renderMarkdown`（module 级单点），8 处 `dangerouslySetInnerHTML` 全部改走安全出口。
16. **答题三页抽 `useQuizSession` + `<QuizAnswerCard>`**（F4）——消除 ~480 行重复；统一提交竞态锁、到期校验、SM-2 幂等。
17. **统一上传/轮询 Hook**（F3）——`useUploadStatus` 复用 Upload/DailyMaterials，统一终态集合（含 cleaning_failed/learning_failed）；Upload 支持多文件队列与 XHR 进度条。
18. **KnowledgeGraph 60KB 单文件拆分**（F5）——常量/Canvas/侧栏/批量建议独立组件；无界 `pageSize=999`（3 处）改分页/虚拟滚动；键盘可达性补全。
19. **全局反馈与兜底**（F1）——Toast 系统 + ErrorBoundary + 路由懒加载；Sidebar button 嵌套 button 修正；logout 二次确认。
20. **统一标签/颜色单一数据源**（CROSS/F5）——卡片类型颜色三套冲突（#0f3460 vs #2d8a56 色值错位）、难度颜色两套，全部收敛到 `utils/labels.ts`。

---

## 三、需要去重的功能清单

### 代码重复（量化）

| 重复项 | 位置 | 规模 |
|--------|------|------|
| `_update_note_status`（+字段白名单） | convert_tasks.py:73 / clean_tasks.py:100 / understand_tasks.py:60 | 3 份，核心逻辑逐字相同（understand_tasks 仍用 `hasattr` 无白名单） |
| Celery 同步引擎工厂 | convert_tasks.py:54 / embedding_tasks.py:54 / clean_tasks.py:47 / understand_tasks.py:43 | 4 份（前两者**同名** `_get_sync_session`） |
| 答题 UI（题目卡片/提交/判分/下一题） | Review.tsx / QuickReview.tsx / TodayLearn.tsx | ~480 行重复 + `QuizState` 接口 ×3 |
| 卡片类型颜色/标签 | utils/labels.ts:19-26 vs KnowledgeGraph.tsx:34-47 vs NoteDetail.tsx:902-905 | 3 套，色值互相冲突 |
| 难度颜色 | labels.ts:44 vs Review.tsx:136 vs QuickReview/TodayLearn 内联 | 2-3 套（easy 色各不相同） |
| 上传入口/轮询 | Upload.tsx（prepare/commit+裁剪） vs DailyMaterials.tsx（uploadFileToFolder+轮询） + client.ts 两个底层包装 | 4 个入口、2 份轮询（终态集合不一致） |
| client.ts 薄包装 | 96 个 `export async function`，5 处 FormData 上传块 | 模板化重复 |
| 级联删除（ReviewLog→CardRelation→QuizItem→KnowledgeCard） | note_service.py:250-281 / understanding.py:92-116 / understanding.py:377-404 | 3 处，枚举不完全一致 |
| 两两相似度去重算法 | cleaning_service.find_duplicates_lightweight:597-659 vs embedding_service.VectorStore.find_duplicates:449-532 | 逻辑几乎相同，仅相似度来源不同 |
| 日界 UTC 滚动（`now.replace(hour=0)`） | goal_service / goal_service_v2 / review_service / notification_service / report_service | 5 处 |
| 薄弱点（<60）查询 | notification_service / goal_service.generate_daily_plan / report_service | 3 份近同逻辑 + 阈值 60 魔法数 |
| 版本快照"读当前→建 USER_EDIT→覆盖" | note_service.py:551-590 / version_service.py:320-352 | 2 处 |
| 迁移 DDL | database.py:171-351 vs alembic 004/006 | 双轨重复且漂移 |
| 卡片/题目 CRUD + JOIN notes | knowledge.py vs understanding.py | 跨模块重复 |
| 状态字符串硬编码 | Upload.tsx:285 / DailyMaterials.tsx:77,332 / CleaningPanel / NoteDetail / Projects | 前端多处，labels.ts 的 statusLabels 未统一使用 |
| 删除确认文案与级联影响提示 | NoteDetail:320-327 / NotesList:89 / CardDetail:59 | 3 处手工重复 |
| diff 行渲染 | VersionHistory.tsx:272-293 vs DiffView.tsx:26-46 | 2 份 |
| `compute_link_signature` | knowledge_link_service.py:54 vs assessment_service.py:22 | SHA-256+排序+16 位完全一致 |
| `_strip_unused_resources` / PDF 页数获取 | pdf_crop.py vs mineru/intake.py | 各 2-3 份 |
| pageSize=999 无界拉取 | KnowledgeCards:57 / Projects:250 / NoteDetail:110 | 3 处 |
| formatSize / formatDate | Projects.tsx:57 / VersionHistory.tsx:49-56 / NotesList:238 | 各 2-3 份 |
| API CRUD 样板（service→except ValueError→HTTPException） | folders.py / projects.py | 逐端点复制 |
| 代码块 fence 跟踪 | cleaning_service.py:205-211 / 256-260（clean_rules 缺失） | 2 份且不统一 |

### 死代码 / 仓库卫生

- **`backend/app/services/goal_service_v2.py`（18KB，git 未跟踪）**：全仓库零 import（仅 FIX_PLAN.md 提到），v1 才是运行时唯一引用。**二选一：接线 v2 或删除 v2**，同时修正 FIX_PLAN 的虚假声明。
- **`backend/control/`**：3 个 celery 运行时残留 exchange 文件**被 git 跟踪**（celery.exchange / celery.pidbox.exchange / reply.celery.pidbox.exchange）。
- **`backend/data/`**：653 个运行时文件（engramnote.db + celery-task-meta-* 结果残留 + 模型/chroma）虽未入库，但占工作区；celery 结果 1h 过期未清理。
- **根目录 `_q.py`**（硬编码路径的调试脚本）、**`参赛/`**（同名拼写变体 HTML 三份）、**`.trae/`**（IDE 私有目录，19 个历史计划文档）、**多层 `__pycache__`**。
- **`backend/` 根目录 11 个游离测试脚本**（test_api.py / test_e2e.py / test_clean_failed_*.py / reset_cleaning.py / restore_note.py 等，未跟踪，与 `backend/tests/` 重复；test_api.py 无任何 assert，"All tests passed!" 恒真）。
- **`cleaning_service.py:62-64` `_OCR_CORRECTIONS`**：定义了从未应用。
- **`config.py` `reminder_quiet_hours_*` / `reminder_poll_interval_seconds`**：零读取的死配置。

---

## 四、FIX_PLAN.md 声称 vs 代码现实

| FIX_PLAN 声称（[x] 已完成） | 现实 |
|------|------|
| 学习目标已切 V2（api/goals、reminder_tasks） | **未实现**：仍 import v1；v2 零引用（B6/CROSS 双重确认） |
| 旧 goal_service.py 不再被引用 | **相反**：v1 是唯一被引用的实现 |
| 版本号增加唯一约束防并发重号 | **未实现**：note_version.py:70 无唯一约束，MAX+1 并发重号 |
| 重新理解 archived 必须 confirm=true | **未实现**：接口无 confirm 参数，archived 直接清空（C-2） |
| 评估测验缓存按 material + personal_note 共同签名 | **未实现**：签名仅 material ids（B5/CROSS 确认） |
| 后端每日限额与前端统一为 10 | **半实现**：review_service=10，但前端 50、goal_service 50（H-4） |
| 上传 folder_id 校验归属；文件夹按 user 过滤 | **未实现**：upload.py:310 无校验；folder_service 查询无 user 过滤（H-5） |
| 普通复习提交校验题目是否到期 | **未实现**：submit_answer 无 due 校验（H-8） |
| 目标列表返回真实进度 | **未实现**：列表 progress_percentage 恒 0（H-7） |
| RAG task.get 改线程池 + 复用主会话 | **未实现**：同步 task.get 阻塞事件循环（C-6） |
| 启动清理重复关系时保留不同方向/类型 | **未实现**：仍是破坏性 MIN(id) 去重（C-1） |
| Docker Compose 增加 Celery worker/beat | **未实现**：compose 无 worker/beat 服务（H-10） |

> 注：`_update_note_status` 白名单（convert/clean）已按 C-3 修复，但 understand_tasks.py:60 仍是 `hasattr` 版本；`stop_cleaning` revoke 主逻辑已实现（残留 late-stop 竞态）；`markdown 渲染禁用原始 HTML` 声明与 `marked` 现状矛盾（C-4）；`create_relation` 排除 rejected（L-1）与 QuickReview 再来一次（L-6）**确已修复**。

---

## 五、修复优先级建议

1. **P0（数据安全/核心失效）**：C-1 破坏性去重 SQL（改为一次性迁移+唯一约束）、C-2 archived 重新理解 confirm、C-3 Beat 任务名、H-2 删除笔记清理 note_projects + 开启 SQLite FK、H-1 清洗块 key 修复、H-3 Alembic 轨道修复。
2. **P0（安全）**：C-4 markdown XSS（DOMPurify）、H-5 folder 归属校验、H-6 goal scope IDOR、S-1 JWT 默认密钥启动校验。
3. **P1**：C-5/C-6 LLM/RAG 基建、H-4 限额统一、H-7/H-8/H-9 契约修复、H-10 Beat/worker 编排、H-11 向量清理。
4. **P2**：去重清单（答题 UI、引擎工厂、_update_note_status、标签颜色）、前端 request()/轮询/加载重构、死代码清理（v2 接线或删除、control/、游离测试脚本）。
5. **P3**：时区统一、免打扰后端化、错误码结构化、测试体系（backend/tests/ 目前仅 2 个测试文件，覆盖严重不足）。
