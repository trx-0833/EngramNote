# EngramNote 决策记录（decisions.md）

> ## 🔒 只读历史归档（2026-09-11 起，overhaul-plan §2.1 S-4）
> **状态：历史快照** ｜ 冻结于 2026-09-11 ｜ 权威性：仅供参考，现状以 `docs/overhaul-plan.md` 为准
>
> **本文不再更新，也不再是权威依据。**
>
> 它是**重构之前**的决策记录：F-01~F-35 每条记录的是"当时那个实现为什么这样写"。
> 大改造已经替换掉了其中相当一部分承重结构（向量存储、检索通道、调度算法、
> LLM 调用层、配置开关……），因此文中的方案描述**可能已经与代码不符** ——
> 代码注释里指向本文的 `见 docs/decisions.md#F-xx` 仍然有效，
> 但它们说明的是**历史原因**，不是当前设计。
>
> - 当前唯一执行依据：`docs/overhaul-plan.md`（新决策记入它的附录，沿用
>   J→AL 的编号；**不再新增 F-xx 编号**）
> - 需要知道"现在为什么这样"时，先读代码与 overhaul-plan 附录，
>   本文只用来回答"以前为什么那样"
>
> 保留而不删除的理由：很多**非显然的踩坑**（如 F-20 的可重试状态码判定、
> F-30 的 Celery retry 语义、F-33 的 JSON 截断）在重构中依然成立，
> 删掉它们等于把已经付过学费的知识丢掉。
>
> ---
>
> 本文承接代码注释中散落的 F-01~F-35「修复编号」，每条记录一个问题/方案/为什么；
> 代码注释只保留简短 WHY 并回链本文（`见 docs/decisions.md#F-xx`）。
> 文末追加本批次代码审查（2026-08-30）产生的新决策。

---

## F-01 card_relations 安全去重（唯一索引）

- **问题**：`_migrate_sqlite` 末尾每次启动都无条件执行破坏性去重——按 `(user_id, 小id, 大id)` 分组只留 `MIN(id)`，未区分关系方向与类型，导致 `prerequisite(A,B)` 与 `prerequisite(B,A)` 等语义相反的合法关系被静默删除。
- **方案**：删除启动期破坏性删除块；改用 Alembic `007_safe_card_relation_dedup.py` 做一次性安全去重（仅删 `(user_id, card_id_1, card_id_2, relation_type)` 完全同键行，保留 `MIN(id)`）+ `batch_alter_table` 建唯一索引 `uq_card_relations_pair_type`；`_migrate_sqlite` 改为幂等 `CREATE UNIQUE INDEX IF NOT EXISTS`。
- **为什么**：去重不能以破坏已有有效关系为代价；唯一索引从根上防止再次产生完全重复的行，且迁移幂等可安全重复执行。

## F-02 archived 笔记「重新学习」确认

- **问题**：archived 笔记触发「开始学习」会无条件清空全部旧产物（卡片/题目/复习记录/图谱关系），破坏性大且不可撤销，误点即永久丢数据。
- **方案**：请求增加 `confirm` 参数；archived 且 `confirm=false` 时只返回影响数量（`requires_confirm` + `impact`），不删除；用户确认后带 `confirm=true` 才清空。学习中态返回 409 防并发（联动 F-29）。
- **为什么**：破坏性操作必须显式二次确认，避免误触造成不可逆数据丢失。

## F-03 Beat 任务名修正

- **问题**：Beat 调度引用函数名 `refresh_goal_progress_task`，与实际注册名 `refresh_goal_progress` 差一个 `_task` 后缀，导致 00:30 目标进度刷新永不执行（Celery 报 Unknown task）。
- **方案**：将 Beat 的 `"task"` 改为正确的注册名。
- **为什么**：Celery Beat 必须按注册名调度，函数名≠任务名会让定时任务静默失效。

## F-04 Markdown 渲染 XSS（存储型）

- **问题**：Markdown 渲染链无 HTML 消毒；NoteDetail 两处直调 `marked.parse` 绕过安全出口，渲染结果直接进 `dangerouslySetInnerHTML`，笔记内嵌 `<img onerror>`/`<script>` 即会执行，可窃取 JWT。
- **方案**：引入 DOMPurify 白名单消毒并收敛到 `renderMarkdown` 单一出口；允许 `style` 与 math 标签组以保证 KaTeX 公式不劣化；删除对 `marked` 的直调 import。
- **为什么**：消毒必须收敛为「渲染后、进 DOM 前」的唯一闸口，否则任何直调 marked 都会绕过防护。

## F-05 LLM 基建共享（连接池 + 类级限流）

- **问题**：每次调用 `httpx.AsyncClient` 新建连接（连接池/TLS 握手全浪费）；`RateLimiter`/`Semaphore` 是实例属性，各处 `LLMService()` 新建实例导致限流与并发闸门完全失效。
- **方案**：模块级共享 httpx 客户端（超时走 `llm_timeout_seconds` 配置）；限流器/信号量提升为类级共享；`close_llm_client()` 挂到应用 shutdown。
- **为什么**：连接复用降开销；限流/闸门必须跨实例共享才能保证全局 RPM 语义。

## F-06 RAG 阻塞 + 私有 engine

- **问题**：协程内同步 `task.get()` 阻塞整个事件循环（问答期间并发请求全卡死）；`_get_session_factory` 自建私有 engine 且从不 dispose。
- **方案**：`task.get()` 包进 `asyncio.to_thread`；会话工厂复用主应用 session。
- **为什么**：阻塞调用不能占事件循环；复用单一会话工厂避免多 engine/连接池泄漏。

## F-07 SQLite 外键开启 + busy_timeout

- **问题**：SQLite 默认 `PRAGMA foreign_keys=OFF`，模型上的 `ON DELETE CASCADE` 全部失效，删笔记/卡片遗留孤儿（如 note_projects）。
- **方案**：监听 `connect` 事件逐连接执行 `PRAGMA foreign_keys=ON` + `PRAGMA busy_timeout=5000`；`delete_note` 显式补删 `note_projects`。
- **为什么**：外键约束只能逐连接开启；busy_timeout 是 Worker/Beat 多进程并发写 SQLite 的前提，避免 "database is locked"。

## F-08 文件夹归属校验

- **问题**：上传的 `folder_id` 不校验存在性/所有权；文件夹详情/计数/删除的笔记查询只按 `folder_id` 过滤、未叠加 `user_id`，可跨用户混入/篡改他人文件夹视图。
- **方案**：上传时校验 `Folder.id==folder_id and Folder.user_id==user_id` 否则 400；folder_service 的相关查询全部叠加 `user_id`。
- **为什么**：防止跨用户 IDOR 与数据渗漏。

## F-09 学习目标 scope 归属校验（IDOR）

- **问题**：目标 `scope_notes`/`scope_folders` 不校验归属，可引用他人笔记/文件夹；进度与每日计划的 scope 查询缺 `user_id`，旧数据跨用户渗漏。
- **方案**：移植 `_validate_goal_scopes`，创建/更新时校验 scope 归属；scope 相关查询全部叠加 `user_id`。
- **为什么**：防止水平越权（IDOR）；统计只应基于当前用户自己的数据。

## F-10 Celery Beat / Worker 启动补齐

- **问题**：start 脚本与 docker-compose 只启动 API，Worker 与 Beat 均未启动，转换/清洗/理解/嵌入等异步任务与 00:30 目标刷新、09:00 邮件提醒在默认部署下全部失效。
- **方案**：start.bat/sh 补 Beat 启动（pidfile 防双启动）、cleanup 列表扩展；docker-compose 新增 `celery-worker`/`celery-beat`；顺带治理启动脚本 CRLF/LF。
- **为什么**：异步任务与定时任务依赖独立进程；进程缺失则核心链路失效。

## F-11 清洗重复块 key 统一

- **问题**：重复块注释标记误用「保留块」的 `duplicate_of` index，而前端/元数据用「重复块」自身 `block_index`，两者几乎总不相等，导致恢复/删除重复块正则永远失配（按钮点了没反应），且仍会 commit 元数据造成文件-元数据不一致。
- **方案**：注释标记改用重复块自身 `block_index`；持久化 `start_line`/`end_line`；restore/delete 按行区间操作，旧数据回退 `duplicate_of`；找不到目标块时报错而非静默。
- **为什么**：定位键两侧语义必须一致，才能精确恢复/删除目标行。

## F-12 每日限额单一来源

- **问题**：复习每日上限后端 10、前端硬编码 50、目标服务 50，三处不一致，UI 进度与真实限额脱节。
- **方案**：`config.daily_review_limit=10` 作为单一来源，经 `/review/stats` 下发 `daily_limit`；前端硬编码改动态读取；目标的 50 改名为 `DAILY_PLAN_LIMIT` 区分语义。
- **为什么**：单一来源避免前后端漂移；「每日复习答题上限」与「每日计划任务数」是两个概念，命名须区分。

## F-13 目标列表进度缓存回退

- **问题**：目标列表进度只走实时计算，无数据时返回空，导致列表 `progress_percentage` 恒为 0。
- **方案**：列表接口无实时进度时回退读取 `goal.progress_cache`（Beat 每日刷新值）。
- **为什么**：让列表页能看到最近一次刷新的进度而非永远 0。

## F-14 复习到期校验 + 同日幂等

- **问题**：普通复习提交不校验题目是否到期（可提前刷完并反复改写 SM-2）；同日同题可重复提交，重复写日志、叠加评分。
- **方案**：`submit_answer(skip_due_check=False)`；普通复习校验 `next_review_at` 到期，未到期拒绝；快速复习 `skip_due_check=True` 免校验；同日同题幂等直接返回已有结果。
- **为什么**：到期校验保住 SM-2 间隔重复语义；幂等防止重复扣减与评分叠加。

## F-15 上传轮询终态补全

- **问题**：Upload 页轮询缺 `cleaning_failed`/`learning_failed` 失败终态，失败后空转到超时误报「转换超时」；`setInterval`+async 无在途互斥，且卸载后仍 setState。
- **方案**：补全失败终态；`setInterval` 改 `setTimeout` 递归 + in-flight 互斥 + 卸载清理定时器；统一终态常量。
- **为什么**：失败必须显式进入终态；轮询要防请求重叠与卸载后泄漏。

## F-16 跨扩展名同名 base 冲突

- **问题**：文件冲突检测按「含扩展名」路径查重，`a.pdf` 与 `a.md` 判定不冲突，但两者转换输出同为 `a.md`，后上传者覆盖先上传者的转换结果。
- **方案**：按 base（主干，去扩展名）检测冲突。
- **为什么**：转换产物按 base 命名，扩展名不同也可能落到同一目标，必须按主干查重。

## F-17 图谱有向关系去重

- **问题**：关系去重键 `tuple(sorted(...))` 抹掉方向，`prerequisite(A,B)` 与 `prerequisite(B,A)` 被当同一对，方向相反的合法关系被静默跳过。
- **方案**：去重键区分有向/无向——有向（prerequisite/subsequent）用有序元组保留方向，无向（related/contrast）用排序元组。
- **为什么**：有向关系的方向是语义的一部分，去重不能抹掉方向。

## F-18 删除笔记清理 Chroma 向量

- **问题**：`delete_note` 只删 DB/文件，不调 `VectorStore.delete_note_chunks`，向量集合与向量文件成为孤儿，长期累积占磁盘。
- **方案**：`delete_note` 调用 `delete_note_chunks(note_id)`，失败仅 warning。
- **为什么**：保持向量库与笔记数据一致，避免检索命中已删除内容。

## F-19 HF_HUB_OFFLINE 环境变量保护

- **问题**：`embedding_service` 设置 `HF_HUB_OFFLINE=1` 后 `finally` 里 `os.environ.pop(...)`，会误删调用方预设的离线标记。
- **方案**：保存原值、结束恢复原值，而非直接 pop。
- **为什么**：环境变量是进程级全局，修改须恢复原状以免影响其他逻辑。

## F-20 LLM 重试与日志收紧

- **问题**：LLM 请求对 4xx 也走退避重试（放大失败耗时与额度消耗）；DEBUG/INFO 日志记录完整 prompt 与响应体，泄露全文隐私。
- **方案**：仅对 429/5xx/超时/连接错误重试，4xx 直接抛；日志消息截断到 ≤200 字符，不记响应体。
- **为什么**：4xx 是请求错误重试无意义；日志脱敏防止敏感内容落盘。

## F-21a JWT 密钥非空校验

- **问题**：JWT 密钥有可预测默认值，生产漏配即以固定密钥签发，任何人可离线伪造任意 user_id 的 token。
- **方案**：默认改空串；生产模式（debug=False）为空时启动即报错。
- **为什么**：密钥可预测=鉴权失效；分区校验避免开发/生产误判（本批次进一步升级为 debug=True 自动生成并持久化，见文末新决策）。

## F-21b 邮箱归一化

- **问题**：注册/登录不归一化邮箱，大小写/空白差异可造成同名账户撞库或登录失败。
- **方案**：注册与登录统一 `strip + lower`。
- **为什么**：邮箱语义大小写不敏感，归一化保证唯一性与一致性。

## F-22 前端 request() 重构（超时 + 401 豁免）

- **问题**：前端请求无超时，网络挂起时永久 pending；401 对所有 `/auth/*` 触发全局登出，登录/注册的错误密码也被误登出；5 处 FormData 上传代码重复。
- **方案**：`request()` 内 AbortController 加 30s 超时；401 仅豁免 `/auth/login`、`/auth/register`；SSE 流不套超时；上传合并 `uploadRequest()`；`request<void>` 空体兼容。
- **为什么**：超时防挂起；401 豁免须精确到「凭据提交」接口避免误登出；收敛重复代码。

## F-23 答题提交竞态锁

- **问题**：Review/QuickReview/TodayLearn/LearningAssessment 提交无 in-flight 锁，连点/连按回车重复提交，重复扣次数、重复写 ReviewLog 并叠加 SM-2。
- **方案**：四页加 `submittingRef` + 按钮 disabled（学习评估用独立锁）。
- **为什么**：提交是写操作需幂等保护；前端锁是成本最低的防重手段。

## F-24 Dashboard 失败分级

- **问题**：Dashboard 6 个统计接口失败全部 `.catch(() => null)` 静默吞掉，用户无法区分「加载失败」与「真无数据」。
- **方案**：区分关键（笔记加载）与非关键（统计）失败，关键失败突出错误条，非关键轻提示/静默。
- **为什么**：关键数据失败必须可见，非关键失败不该整页报错制造噪音。

## F-25 复习提醒按值去重

- **问题**：`notified.length===0` 判断使去重被永久抑制（markNotified 后长度恒非 0），整个会话只弹一次提醒，`lastNotifiedDueRef` 按值去重被绕过。
- **方案**：移除 `notified.length===0` 判断，仅按 `due_count` 值变化去重；清理失去读者的死代码。
- **为什么**：去重应基于「值是否变化」而非「是否弹过」，否则后续提醒被吞。

## F-26 归档状态机 + 分页

- **问题**：converted 笔记取消归档被错误映射为 cleaned（从未清洗却谎称已清洗）；题库/卡片/相关笔记用 `pageSize=999` 一次拉取，前端内存压力大。
- **方案**：取消归档按原状态映射（converted→converted，其余→cleaned）；列表改「加载更多」分页。
- **为什么**：状态机要映射回原始状态；分页降低前端内存/渲染成本。

## F-27 任务公共模块收敛

- **问题**：4 个任务模块各自重复实现同步连接工厂与状态更新逻辑（understand_tasks 还用无白名单的 hasattr 版）；metadata 整包替换会丢失 `clean_task_id` 等任务字段。
- **方案**：新建 `tasks/common.py` 收敛 `get_sync_session`（含 PRAGMA）与 `update_note_status`（白名单 + `metadata_` 字段级合并）。
- **为什么**：单一实现避免三进程各自逻辑漂移。

## F-28 标签/颜色单一数据源

- **问题**：卡片类型/难度颜色在 labels.ts、KnowledgeGraph.tsx、NoteDetail.tsx、答题页三套硬编码，色值互相冲突。
- **方案**：统一到 `utils/labels.ts` 单一数据源，图谱/卡片页/答题页改 import。
- **为什么**：单一数据源避免视觉不一致与「改一处漏一处」。

## F-29 并发触发防护（状态机）

- **问题**：理解/清洗/出题接口无状态锁，连点创建多个 Celery 任务；`generate_questions` 不清理旧题导致重复累积。
- **方案**：learning/cleaning 进行中返回 409；重新出题前先清理该笔记旧题（含复习记录），恢复「重新生成」语义。
- **为什么**：状态机是防重的根；幂等重生成要恢复语义而非累积。

## F-30 清洗正确性 + Celery 重试耗尽

- **问题**：清洗规则对全文正则替换，页码/数字/水印规则会误删代码块内纯数字行、公式行；Celery `retry()` 重试耗尽时重抛原始异常而非 `MaxRetriesExceededError`，旧代码捕获不到导致笔记永久停留在 converting/cleaning/learning。
- **方案**：逐行跟踪 ``` 代码块与 $$ 数学块状态，界内行不套规则；重试耗尽分支标记失败状态（重抛原始异常进失败分支）。
- **为什么**：结构化内容不应被内容清洗规则误伤；任务失败必须进入失败终态，否则状态机卡死。

## F-31 版本历史唯一性 + 空问题拒绝

- **问题**：版本号 `MAX+1` 取号非原子，并发创建重号并覆盖存储文件；restore_version 先建快照再读目标、读取失败仍以空串建快照污染历史；`/ask` 空/纯空白问题仍消耗一次 LLM 调用。
- **方案**：`(note_id, version_number)` 唯一索引 + `create_version` 捕获 IntegrityError 重试；恢复前先读目标验证可读、失败直接抛错不建空快照；空问题返回 422（`EMPTY_QUESTION`）。
- **为什么**：唯一约束保证版本号唯一；快照必须建立在可读内容之上；空问题浪费调用。

## F-32 搜索通配符转义 + 时区日界

- **问题**：`ilike(f"%{keyword}%")` 未转义 `%`/`_`，搜索 "100%" 会当通配符放大匹配；日界一律 UTC 零点，与 Celery 的 Asia/Shanghai 不一致，北京时间早间答题会被计入前一日。
- **方案**：ilike 转义（`%`/`_` → `\%`/`\_` + `escape`）；新建 `utils/timeutil.py` 统一 Asia/Shanghai 日界，替换各处的 UTC 零点。
- **为什么**：字面量搜索要精确匹配；业务「今日」应按本地时区计算。

## F-33 LLM JSON 健壮解析

- **问题**：推理模型输出被 `max_tokens` 精确截断、或包在 ```json 围栏里、或带尾缀杂文，裸 `json.loads` 直接失败，导致整批知识点/题目丢失；`llm_timeout_seconds=120` 小于实际生成时长，超时比截断更早触发。
- **方案**：`chat_detailed` 暴露 `finish_reason/truncated`；JSON 场景自动剥离围栏；`parse_json_tolerant` 逐级容错（直解→围栏剥离→raw_decode→字符串感知截断抢救）；超时/上限放大（json max_tokens 8192→16384、ceiling 32768、timeout 600）；解析失败重试并翻倍 max_tokens。
- **为什么**：分层防御，不依赖单一魔法数字；截断时抢救已生成部分而非整批丢弃。

## F-34 Markdown 结构感知分段

- **问题**：理解管道把长章节按固定字符/段落硬切，会从表格行、代码块、列表项、长句中间截断，送入 LLM 的片段上下文不连贯。
- **方案**：新增 `markdown_segmenter.py`：`split_markdown_blocks` 切结构性原子块 → `make_segments` 贪心打包（块放不下整块下移、单块超限在安全边界内拆、尾部续接）→ `truncate_to_complete_blocks` 按块边界防御性截断；纯标准库实现。
- **为什么**：送入 LLM 的片段必须结构完整、上下文连贯，硬切会破坏表格/代码/列表语义。

## F-35 测试体系补强（含 SM-2 阈值附带修复）

- **问题**：缺 pytest 运行环境与回归测试护栏；存量的 `sm2_service` 字符集重叠判断 `> 0.5` 挡住「机器」vs「机器学习」这类前缀部分匹配，导致正确评分被拦。
- **方案**：requirements 补 `pytest`/`pytest-asyncio`（dev 注释段）；新增 `conftest.py` 独立临时库 fixture + 测试用户/token 工厂 + `test_fixes.py` 6 项回归；附带把 SM-2 阈值改为 `>= 0.5`。
- **为什么**：修复必须有回归护栏；阈值边界用 `>=` 才是「部分匹配返回 3 分」的正确语义。

## F-36 选中文本 AI 提问（仅当前笔记 + 选区局部上下文）

- **问题**：阅读笔记时选中文本无法即时向 AI 提问；全库 RAG 回答范围过大、上下文不聚焦，且 `retrieve_context` 不支持按 note 过滤。
- **方案**：批注浮层新增「AI 提问」按钮，前端截取选区局部上下文（选中文本 + 前后各 1500 字符）提交至新端点 `POST /api/notes/{note_id}/ask/stream`；后端仅做笔记归属校验（防 IDOR）并引用笔记标题，不起 RAG、不读全文，复用 `LLMService.chat_stream` SSE 流式回答（scene=note_ask_stream），事件格式与 `/understanding/ask/stream` 对齐；前端 `NoteAskPanel` 浮层支持提问编辑、停止生成、重新提问。不落库、不改批注表。
- **为什么**：局部上下文聚焦、token 消耗小、响应快；归属校验防越权；复用既有 LLM 基建与 SSE 协议，零新依赖、不改 Schema。

---

## 本次新决策（2026-08-30 代码审查批次）

### D-01 JWT 密钥策略

- **问题**：生产部署漏配 `jwt_secret_key` 且未设 `DEBUG=false` 时，以空字符串为 HS256 密钥签发 token，任何人可离线伪造任意 user_id 的 token。
- **方案**：`debug=False` 且未配置时启动即报错（保留既有校验）；`debug=True` 且未配置时自动生成 `secrets.token_hex(32)` 并持久化到 `data/.jwt-secret`（后续启动复用），记 WARNING；不再允许空密钥签发。
- **为什么**：密钥可预测=鉴权失效；开发环境自动生成并持久化，既满足不空密钥又保证重启不失效与开发体验。

### D-02 Markdown 消毒

- **问题**：marked 默认保留原始 HTML，渲染结果直接进入 DOM，恶意 PDF/MD 内嵌 `<script>`、`onerror` 事件、`<iframe>` 即可执行。
- **方案**：管道收敛为 `marked.parse` → DOMPurify 白名单消毒 → KaTeX 二次渲染；允许标签最小集，`a` 强制 `rel="noopener noreferrer"`，`img` 仅 http(s) 源；同时 `.md` 上传做内容嗅探（纵深防御第二层）。
- **为什么**：白名单消毒是「渲染后、进 DOM 前」的唯一闸口；配合上传侧嗅探形成两层防线。

### D-03 图谱嵌入 Celery 化

- **问题**：图谱建议在 API 进程内加载 BGE-M3（约 2.2GB），首次点击建议时 API 进程内存峰值 ~2×，低内存机器重演段错误/OOM，与「嵌入模型隔离到 Worker」的架构决策冲突。
- **方案**：`suggest_relations` 通过 `celery_app.send_task("app.tasks.embedding_tasks.encode_text")` 批量编码（`task.get(timeout=180)`，线程池包装），API 进程不再实例化 EmbeddingService；失败抛 `GraphSuggestionError` 由 api 层转 503。
- **为什么**：大模型加载必须留在 Worker，FastAPI 主进程保持轻量稳定；失败显式可见（不再「点了没反应」）。

### D-04 上传超时分离

- **问题**：上传类请求复用全局 30s 超时，与 `max_upload_size_mb=500` 上限矛盾，慢网/大视频/大 PDF 超过 30s 即被 AbortController 强制中断。
- **方案**：上传类请求使用独立 `UPLOAD_TIMEOUT_MS=600000`（10 分钟），其余请求仍用 30s。
- **为什么**：上传时长与上传体积强相关，须与普通请求的超时语义分离。