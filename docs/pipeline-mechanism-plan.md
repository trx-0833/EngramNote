# 处理环节机制重设计（Pipeline Mechanism Redesign）

> **状态：已定案（本轮零代码改动）** ｜ 起草：2026-09-25 ｜ 六项决策见 §0.2 ｜
> 权威性：**本主题（处理环节 / 任务机制）的设计依据**；但**现状描述仍以代码实测为准**（`AGENTS.md` §1）。
> 执行顺序：先 S 期（§11 的五条缺陷修复），再 P 期（机制改造）。
> 与 `overhaul-plan.md` 的关系：本文件是**机制层**的细化，`overhaul-plan.md` 仍是重构期总台账。

---

## 0. 结论先行

**现状不是"没有机制"，而是"机制写好了但没接到主路径上"，外加"阶段不是真阶段"。**

三条具体表现（都有代码证据）：

1. **有追踪，没进度。** 后端有完整契约（`backend/app/services/task_run_service.py:162` 的
   `report_progress`、`:100` 的 `ensure_task_run`），Celery 任务里也确实调了上报
   （`backend/app/tasks/convert_tasks.py:113,137,281`；`clean_tasks.py:452,454`；
   `understand_tasks.py:458,460`）——但取值是**写死的占位**（`0.05 → 0.15 → 0.95`、`0.1 → 0.95`），
   中间过程（MinerU 解析、LLM 逐章抽取、逐批出题、向量嵌入）**一次都不报**。
   于是 UI 上"进度条"在长阶段里长期不动，用户无法区分"在跑"与"卡死"。
2. **阶段不是阶段，是"某个函数的内部步骤"。** 转换、清洗、理解各自是一个**巨型 Celery 任务**，
   阶段只存在于日志与注释里（`clean_tasks.py:140-146` 列了 10 步；`understand_tasks.py:55-62` 列了 7 步）。
   因此**无法单步重跑**：LLM 抽取失败 → 重试整篇理解；嵌入没跑 → 只能靠人工脚本
   （`backend/scripts/embed_chunks.py`，全仓唯一的向量写入入口）。
3. **失败语义四套并存，且互相矛盾。** 同一件事在四个地方各有一份真相：
   `notes.status`（10 个值的枚举）、`notes.error_message`（中文自由文本）、
   `task_runs.status / error`（6 个值 + 截断摘要）、`core/app_error.py:40-212`（80 个错误码）。
   例证：`convert_tasks.py:170-183` 在**内层**把笔记标 `failed` 后 **return**，
   外层 `:312-313` 于是照常 `mark_task_succeeded` —— 同一次转换，
   `notes` 说失败、`task_runs` 说成功。

**因此本方案的主张是：把"处理"从函数里的步骤，提升为数据库里的一等实体。**

### 0.1 先修的四条"安全 / 卡死 / 毁数据"（与机制设计无关，P0 立刻做）

盘点过程中还查出五条**跨链路的确定性缺陷**（证据见 §1 的 #31、#32、#33、#36、#37、#40），
它们不属于"设计取舍"，而是当前代码就会咬人的问题。建议在机制改造开始前**独立修掉**
（各自改动都很小，能单独提交、单独验证）：

| 编号 | 问题 | 修法 | 验证 |
|---|---|---|---|
| S1 | 🔴 `task_runs.user_id` 恒 NULL ⇒ 任何人可列出/取消**他人**任务（#31） | `common.py:185-190` 传 `user_id`（从 note 读），并对历史行回填 | 新增用例：用户 B 拿 A 的 note_id 列出任务必须为空 |
| S2 | 取消后笔记**永久卡死**（#32） | 撤销/收尾纳入同一台状态机；在 P0 里最小修法是"`cancelled` 也参与收尾扫描" | 用例：取消清洗/理解后，笔记必须回到可重启状态 |
| S3 | "已停止"与"100% 成功"同时成立（#33） | 任务收尾必须读回 `jobs/notes` 的真实结果，**不允许内层失败外层报成功** | 用例：内层 `return` 失败路径 ⇒ `task_runs.status != succeeded` |
| S4 | 重跑理解**不可逆毁数据**、且软删除不能中止链路（#36、#37、#40） | 重跑改为"影子生成 + 原子替换"（先把新产物写进新版本目录）；入口的删除判据改为看 `trashed_at` 而非中文文案 | 用例：重跑中途失败 ⇒ 旧卡片/题目/复习记录**一条不少**；回收站笔记的任务必须立刻退出 |

> 这四条不做完，机制改造的每一次上线都要面对"分不清是旧毛病还是新毛病"的成本。

---

## 0.2 已拍板（2026-09-25，用户回答；本节是后续执行的依据）

**本轮范围：零代码改动 —— 只交付本方案。**

| # | 问题 | 决定 | 对方案的影响 |
|---|---|---|---|
| D1 | §0.1 的五条缺陷（S1~S4）先修还是并入 | **先修，且作为下一轮的第一件事**；与机制改造解耦、各自独立提交 | 机制改造（P0 起）排在 S1~S4 之后 |
| D2 | 本轮落地到哪一期 | **只出方案，先不动代码** | 本轮不产生产代码改动、不跑门禁；P0→P5 是后续排期 |
| D3 | 阶段 `embed` 是否默认自动执行 | **自动跑**；`embed` worker 不在线时流水线停在 `waiting` 并**明示** | §9.2 的队列拓扑与 `waiting` 状态是必需项，不是可选 |
| D4 | 删除的竞态（`purge`） | **接受延迟释放**：先入回收站 + `purge_after`，由阶段 `purge` 真删 | §7 末条的删除语义按此定案 |
| D5 | LLM 成本预算闸门默认值 | **默认关闭**（0 = 不限），行为与今天一致 | §9.1 只做按阶段记账与报表；闸门机制保留但不默认生效 |
| D6 | `chunks.id` 是否切确定性 `chunk_key` | **不做**：保留 uuid4，只**加 `chunk_key` 列备用**（不切外键口径、不回填） | §4.3 中该行降级为"只加列"；幂等由阶段指纹承担 |

> D1 与 D2 的关系：D1 说的是**做完方案之后的执行顺序**（先 S1~S4），
> D2 说的是**本轮不动代码**。两者不冲突：本轮不写任何生产代码，S1~S4 是下一轮的入口。

---

## 1. 现状实测（只列与机制有关的事实）

| # | 事实 | 证据（`文件:行号`） |
|---|---|---|
| 1 | 任务追踪表 `task_runs` **只服务 3 个任务名**；真实库里总共 **4 行**、全部 `succeeded` | `backend/app/services/task_run_service.py:56-72`；真库实测 `select * from task_runs` = 4 行（2 转换 + 2 清洗） |
| 2 | 进度值是硬编码占位，不是度量 | `convert_tasks.py:113,137,281`；`clean_tasks.py:452,454`；`understand_tasks.py:458,460` |
| 3 | 出题任务**完全不入追踪**：装饰器无 `bind=True`，函数体却用 `self` | `understand_tasks.py:485` vs `:504` |
| 4 | 嵌入任务同样不入追踪（无 `begin_task_run`） | `backend/app/tasks/embedding_tasks.py:99-139` |
| 5 | 向量**没有任何任务在算**；重新分块会把整篇 `has_embedding` 清零 | `backend/app/services/chunk_service.py:144`；唯一入口 `backend/scripts/embed_chunks.py:145-186`；Beat 表无嵌入任务 `backend/app/tasks/celery_app.py:127-214` |
| 6 | 用户编辑笔记后**不重建** chunks / FTS5；偏移不变量只在写入那一刻校验 | `backend/app/services/note_service.py:1029-1104`；不变量校验 `chunk_service.py:119-125` |
| 7 | 四条写入链路统一是"先写文件、后写 DB 指针"，且 md 写入是**非原子** `write_bytes` | `convert_tasks.py:257-267`；`clean_tasks.py:329-379`；`version_service.py:111-134`；`backend/app/services/storage_service.py:473-477` |
| 8 | 原始文件相反：**DB 行先 commit，文件后写**（崩溃即"有笔记没文件"） | `backend/app/api/upload.py:469` vs `:504-516` |
| 9 | 两阶段上传的临时目录只在**进程启动时**清理；`.owner` 旁载**永不删除**（成功路径也留） | `backend/app/database.py:402-434,299-300`（且 `:423-425` 跳过非目录项）；`upload.py:701,722,932-934` |
| 10 | 自动串联（转换→清洗）是**裸 `except: pass`**：投递失败则流水线静默停在半路 | `convert_tasks.py:269-277` |
| 11 | 取消只在**入口**检查一次（转换），清洗有 6 个边界检查，理解无 | `backend/app/tasks/common.py:178-191`；`clean_tasks.py:217,230,256,278,321,371` |
| 12 | "停止清洗"把笔记标 `cleaning_failed`、任务标 `cancelled` —— 同一动作两个终态 | `backend/app/api/cleaning.py:196-207` vs `task_run_service.py:406-413` |
| 13 | 归档与处理完成**共用同一个枚举值** `archived`；取消归档靠猜（`clean_md_path` 是否为空） | `backend/app/models/note.py:65-95`；`backend/app/api/notes/list_detail.py:314-324` |
| 14 | 状态旁载 JSON 里进度**恒为 `null`**，注释自认"当前系统无真实进度数据" | `backend/app/services/vault_meta.py:58` |
| 15 | 失败文本会把完整 traceback 写进**可被 API 读取**的 `notes.error_message` | `convert_tasks.py:181,198,245,331` |
| 16 | Broker = 文件系统 ⇒ 无 visibility timeout，只能靠 DB 心跳自愈补偿 | `celery_app.py:74,83,92-96`；`task_run_service.py:285-360`；Beat 每 5 分钟 `celery_app.py:145-148` |
| 17 | 幂等是**碎片式**的，散在四处、口径不一 | 卡片 `card_intake_service.card_content_hash`（`services/card_intake_service.py:97`）；chunk 指纹 `chunk_service.py:45-57`；提示词版本 `services/llm/prompts.py:410`；模型一致性 `chunk_search_service.py:138-150`（**无生产调用方**） |
| 18 | SSOT 漂移：chunks 由**清洗**任务写，却服务于**检索**，且 `chunks_fts` 走全表 rebuild | `clean_tasks.py:390-406`；`services/fts_search_service.py:109-147` |
| 19 | MinerU 超时只是"放弃等待"：daemon 线程继续解析，而任务还会重试 —— 请求与临时目录互相叠加 | `services/mineru/converter.py:194-211`（线程 + `join(timeout)`）、`:262-263` 与 `:1118-1124`（清理与残留线程竞争）、`convert_tasks.py:284,314-333` |
| 20 | 两阶段上传的 temp_id **无并发占用声明**：检查后直接使用，同 id 并发 commit 可撞同一 Vault 路径 | `api/upload.py:854-857` + `services/storage_service.py:453`（同名 `os.replace` 覆盖）；`models/note.py` 无唯一约束 |
| 21 | `POST /api/upload/commit` 不传可选 `filename` 时 **500**（`TypeError`，非 `AppError`） | `api/upload.py:783`（声明可选）vs `:924`（`mimetypes.guess_type(None)`） |
| 22 | **未判分 ≠ 答错，但全站按答错统计**：LLM 判分失败 ⇒ `quality=1 / is_correct=False` 落库 | `services/review_service.py:363,366-371,419-437`；被日报/趋势/薄弱点/掌握度当成错：`services/report_service.py:60-69,117,157-168,216-229`、`services/mastery_service.py:236-247`（只有额度统计排除了 ungraded：`review_service.py:62`） |
| 23 | 复习的**调度依据与到期队列可永久分叉**：`ReviewState` 写失败只 warning，而日志与题目镜像已提交，且无自愈 | `services/review_service.py:457-471` vs `services/review_state_service.py:329-331` |
| 24 | 复习**幂等只有 SELECT，无唯一约束**：并发或重放会新增第二条 `ReviewLog` 并双推进调度 | `review_service.py:232-271,421`；`review_log.py:95-96` 与 `review_service.py:179-180` 的注释互相矛盾 |
| 25 | 卡片复习**偷耗答题日额度**（计数不区分 `item_type`），与"卡片不受限额约束"的声明相反 | `review_service.py:58-65,104-106` vs `api/review.py:267-271` |
| 26 | 学习目标**达成判定量纲错位**：`progress`（占目标百分比）与 `target_mastery`（0-100 掌握度）直接比较 | `services/goal_service.py:610` vs `:618`（另 `:737` vs `:744`）；前端同款判据 `frontend/src/pages/LearningGoals.tsx:472` |
| 27 | 7 天趋势**恒为 0**：聚合键与取数键用的是两个不同的日期 | `services/report_service.py:155` vs `:175` |
| 28 | 声明中的"校准闭环唯一来源"零消费：`predicted_retention`/`rating` 从未被查询 | `services/learning_metrics_service.py:388-395`、`:139-140`；承诺写在 `models/review_log.py:130-155` |
| 29 | 掌握度只在该卡被复习时刷新，**没有定时重算** ⇒ 未复习的卡不会随时间衰减 | `services/review_service.py:486-492`；`mastery_service.py:293` 注释与 `:298` 实现矛盾 |
| 30 | 定时任务把异常吞成返回字符串 ⇒ Celery 侧一律 SUCCESS，无重试、只有日志 | `tasks/reminder_tasks.py:82-88,105-112`（两个与本链路相关的 Beat 任务全是这个形状） |
| 31 | 🔴 **越权**：`begin_task_run` 不传 `user_id` ⇒ `task_runs.user_id` 恒 NULL ⇒ 归属校验恒真，**任何登录用户可列出/取消他人的清洗与理解任务** | `tasks/common.py:185-190`（只传 note_id）→ `api/tasks.py:107`（`user_id is not None` 恒假）、`:136`（过滤恒真） |
| 32 | **取消会让笔记永久卡死**：`/api/tasks/{id}/cancel` 只改 `task_runs`，僵尸扫描只扫 `pending/running` ⇒ `cancelled` 永不被收尾；理解链路没有 stop 端点，`start` 永远 409 | `task_run_service.py:406-409`；`models/task_run.py:60`；`api/understanding.py:133-139` |
| 33 | **"已停止"与"已成功"能同时成立**：stop 把笔记标 `cleaning_failed` 后，任务照常走完并写 100% 成功且无错误 | `api/cleaning.py:197-198` vs `clean_tasks.py:371-373,454-455`；`_finish` 只在 error 非空时写 `task_run_service.py:239-240` |
| 34 | **心跳超时会误杀正常长任务**：清洗/理解各只有 2 个上报点，中间含模型加载与整篇 encode；900s 超时后笔记被改判 `failed`，而 `failed` 不在两条链路的重启白名单里 | 上报点 `clean_tasks.py:452,454`、`understand_tasks.py:458,460`；超时 `task_run_service.py:50`；收尾 `:325-346`；白名单 `api/cleaning.py:134-139`、`api/understanding.py:141-146` |
| 35 | 同一个上报点还是**假阶段名**：`0.8「正在建立卡片关联」` 在全部入库**之后**才上报 | `understand_tasks.py:460`（`_understand_document` 内部已完成入库） |
| 36 | **重跑理解不可逆地毁数据**：在投递任务**之前**先物理删光卡片/题目/复习记录/图谱关系并 commit；投递无 try/except | `api/understanding.py:161-184`（删 + commit），`:196`（`delay` 裸调用） |
| 37 | 重跑理解留下**孤儿复习状态**：`ReviewState` 无 FK 级联，删除列表里也没有它 | `models/review_state.py:100-102,140` vs `api/understanding.py:161-184` |
| 38 | **出题链路三无**：无 `task_runs` 行、无进度、无笔记状态；重试耗尽只 `logger.error`；单批失败 `continue`（静默少题）；连点 N 次 = N 个任务，每个都先删旧题 | `understand_tasks.py:485-510`（无 `bind` 却用 `self`）、`:378-383`、`:266-282`；`api/understanding.py:723-749` |
| 39 | 并发保护**只靠 `notes.status` 一个字段**，有可复现竞态窗口：转换写 `converted` 与清洗任务写 `cleaning` 之间，用户点"清洗"能通过白名单 ⇒ 两个清洗任务并行 | `convert_tasks.py:264-274` vs `clean_tasks.py:448`；白名单 `api/cleaning.py:134-139`；`ensure_task_run` 从不检查"该笔记是否已有 running 任务" `task_run_service.py:100-159` |
| 40 | 软删除**不能中止链路**：回收站写 `"用户移入回收站"`，而任务入口只认 `"用户手动删除"` ⇒ 链路照跑，并可用 finished 状态覆盖删除标记 | `services/note_service.py:256` vs `clean_tasks.py:156-160`、`understand_tasks.py:71-76` |
| 41 | 全批次失败仍然归档：批次异常被吞掉后**无条件**写 `archived` | `services/understanding_service.py:497-508` + `understand_tasks.py:151-157` |

---

## 2. 目标：三层分离

现在这三件事**混在一起**（都往 `notes.status` 上写）：

```
现在：  用户意图  ─┐
        处理进度  ─┼─→  notes.status（10 个值）＋ error_message（自由文本）＋ task_runs
        归档状态  ─┘
```

目标把它们拆开，**每个问题只有一个权威字段**：

```
目标：
  ① 处理进度   →  jobs / job_stages（新增两张表）；notes.status 降级为"当前所处阶段名"的派生视图
  ② 用户意图   →  notes.archived_at（新增列）；与"处理是否完成"彻底解耦
  ③ 执行实例   →  task_runs（保留，只回答"这个 Celery 任务跑到哪了"）
```

---

## 3. 核心机制 A：阶段协议（Stage Contract）

**任何一步处理 = 一条 `job_stages` 行 + 一个纯函数 + 一份产物哈希。**
不满足下列 7 项的代码，不允许出现在处理链路里（用测试锁死）。

| # | 必须声明 | 判据（可测） |
|---|---|---|
| 1 | **输入指纹** `fingerprint` | `sha256(上游产物哈希 + stage_version + prompt_version + 影响输出的配置项)`；指纹相同 ⇒ **本阶段直接复用，不执行** |
| 2 | **产物** | 产物对象名（Vault 路径）+ 内容 sha256；写进 `job_stages.output_hash` |
| 3 | **原子性** | 先写临时对象 → `os.replace`/对象存储 copy 到最终名 → **然后**在同一 DB 事务里写 stage 行与产物指针。禁止"先标状态、后写文件" |
| 4 | **断点** | 若阶段内有循环（逐章节 LLM、逐批嵌入、逐页解析），必须把已完成的批次单位写进 `job_stages.checkpoint`（JSON），**从断点继续而不是从头再来** |
| 5 | **取消点** | 声明 `interruptible` 与检查点位置（循环边界 / HTTP 轮询之间 / 分批之间）。见 §7 |
| 6 | **进度** | 声明 `weight`（在流水线里的权重）与 `unit_total`（可选：章节数、批次数、页数），进度按真实单位算 |
| 7 | **指标与失败面** | 至少一个可观测指标（耗时、token、成本、条目数）；失败必须给出 `error_code` + `retry_class`（见 §5） |

### 3.2 两条必须成立的硬不变量

现状的并发保护**只有 `notes.status` 一个字段**（#39），因此存在可复现的并行窗口
（转换写 `converted` 与清洗任务写 `cleaning` 之间点"清洗" ⇒ 两个清洗任务并行）。
本方案把它换成两条**数据库级**不变量，用唯一索引表达，不靠约定：

1. **一条笔记同时只有一个活跃流水线**：`jobs` 上 `UNIQUE(note_id)`；
2. **同一流水线同一阶段同时只有一个活跃执行**：`job_stages` 上 `UNIQUE(job_id, stage)`
   + 派发前用条件更新（`UPDATE ... WHERE status != 'running'`）抢占，抢不到就放弃本次派发。

这两条同时消灭开篇 #0.3 的一类问题：内层失败外层报成功（#33）在"结果只有一个来源"之后不可能再发生。


层级**一律三层、名字**（`pipeline stage` / `phase`），且是**唯一的分层来源**：
阶段名与展示文案在代码里只写一份（`app/services/pipeline/stages.py`），前端**不再自己写中文**
（现状是后端写"正在抽取知识点"、前端另有一份兜底文案，见
`frontend/src/components/TaskProgress.tsx:125`、`frontend/src/pages/notedetail/ProcessingPanel.tsx:7`）。

| # | pipeline stage | 阶段名 | phase（子阶段） | 输入 → 产物 | 幂等键 | 可中断点 | 现状 |
|---|---|---|---|---|---|---|---|
| 1 | `ingest` | 接收 | 校验·魔术字节·配额·落盘 | 上传文件 → `source/{base}{ext}` | `file_sha256` | 无（毫秒级） | 已有（两阶段上传） |
| 2 | `convert` | 转换 | `parse_pages` · `assemble_md` | 源文件 → `output/markdown/{base}.md` | `sha256(源文件)+parser_version` | **逐页批**（MinerU 分批） | 已有，无子阶段上报 |
| 3 | `clean` | 清洗 | `rules` · `dedup` · `write_copy` | 原始 md → `.clean.md` | `sha256(md)+clean_rules_version` | 逐批去重 | 已有（10 步在一个任务里） |
| 4 | `chunk` | 分块 | — | clean md → `chunks` 行 | `chunk_hash(chunks, source_path)`（**已有**） | 无 | 已有（`chunk_service.py:45-57`） |
| 5 | `embed` | 向量 | 逐批 encode | `chunks` → `chunks.embedding` | `chunk.content_hash + embedding_model` | **逐批** | **缺失**（只有人工脚本） |
| 6 | `extract` | 拆解 | `summarize` · `cards` · `intake` | chunks → `knowledge_cards` | `chunk_hash + prompt_version` | 逐章节 | 已有（无子阶段上报） |
| 7 | `quiz` | 出题 | 逐批出题 | 卡片 → `quiz_items` | `card.content_hash + prompt_version` | 逐批 | 已有（**未入追踪**） |
| 8 | `link` | 关联 | 规则 + 语义 | 卡片 → `card_relations` | `note 卡片集哈希` | 逐批 | 已有（藏在 6 里） |
| 9 | `index` | 检索索引 | FTS5 重建 | chunks → `chunks_fts` | 同 `chunk` 指纹 | 无 | 已有（全表 rebuild，代价高） |
| 10 | `purge` | 清理 | 删 DB / 删文件 | 回收站笔记 → ∅ | `note_id` | 逐文件 | 已有（不在流水线模型里） |

> **两处刻意的设计选择**：
> ① `index` 独立成阶段（现状它是 `chunk` 的副作用）：全表 FTS rebuild 是大文档的耗时项，
> 独立后可以"只重建这一篇"、可以单独失败重试、可以让用户看见"检索还没可用"。
> ② `purge` 纳入模型：删除是最需要"进度与自愈"的操作，现状它是同步 API（`note_service.purge_note`）。

---

## 4. 核心机制 B：数据模型（只增列、只加表）

**约束**（`AGENTS.md` §2 与既定路线）：不换数据库引擎、不引中间件、不引第二套迁移工具。
因此下面全部走 `database.py::_migrate_sqlite` 的**非破坏性加列/建表**通道。

### 4.1 新增 `jobs`（一条笔记一条流水线，`UNIQUE(note_id)`）

| 列 | 说明 |
|---|---|
| `status` | `queued` / `running` / `waiting` / `paused` / `succeeded` / `failed` / `cancelled` |
| `current_stage` | 当前阶段（= 前端要显示的那个名字） |
| `plan_version` | 阶段清单自身的版本（新增阶段时用它做迁移） |
| `stop_before` | 停在哪一阶段之前（取代 `cleaning.py` / `understanding.py` 里各自的状态闸门） |
| `source_hash` | 源对象 sha256（**陈旧检测**的依据） |
| `artifact_hash` | 产物指针快照（md / clean / cards…），用于判"是否落后于源" |
| `error_code` / `error_stage` / `retry_class` | 失败三件套（见 §5） |
| `cost_usd` / `token_in` / `token_out` | 从 `llm_calls` 按阶段聚合（现状 `scene` 有、阶段归因没有） |
| `budget_usd` | 本条流水线的成本上限（0 = 不限，默认与现状一致） |
| `heartbeat_at` / `started_at` / `finished_at` | 与 `task_runs` 同语义，**被它取代** |

### 4.2 新增 `job_stages`（`UNIQUE(job_id, stage)`）

| 列 | 说明 |
|---|---|
| `status` | `pending` / `running` / `succeeded` / `failed` / `skipped`（指纹未变）/ `cancelled` |
| `attempt` / `max_attempts` | 与现状 `task_runs` 同语义，但**按阶段**计数（现在按整篇任务计） |
| `fingerprint` | §3 第 1 项 |
| `output_hash` / `artifact_path` | §3 第 2 项 |
| `checkpoint` | §3 第 4 项（JSON：`{"done_batches":[0,1,2],"next_batch":3}`） |
| `units_done` / `units_total` | 真实单位（章节数 / 批数 / 页数） |
| `weight` | 进度加权（§7） |
| `metrics` | JSON：耗时、token、成本、条目数 |
| `error_code` / `retry_class` / `error_digest` | 结构化失败（人类可读细节**只进日志**，见 §5.3） |
| `started_at` / `finished_at` / `heartbeat_at` | |

### 4.3 与既有表的关系

| 既有对象 | 处置 | 理由 |
|---|---|---|
| `notes.status` | **降级为派生视图**（= `jobs.current_stage` 的映射），写入点从 71 处收敛到 1 处 | 现状 `NoteStatus` 71 处写入，语义已被撑坏（#13） |
| `notes.archived_at` | **新增列**（用户意图） | 把"归档"从 `NoteStatus` 里拆出去 |
| `task_runs` | **保留不动**，新增 `parent_job_id` 关联（可为空） | 兼容：现有 API、前端、20 条测试不破 |
| `chunks.id` | **只新增 `chunk_key = sha256(note_id + index + content_hash)[:32]` 列备用**（按 D6：**不切主键、不回填、不改外键口径**） | 现状每次重建 id 全变（#18），任何外部引用失效；本方案把幂等交给**阶段指纹**承担，主键迁移留作将来可选项 |
| `llm_calls` | 新增 `job_id` / `stage` 两列（现状只有 `scene`） | 成本要能按阶段归因 |
| `vault_meta` 旁载 | `progress` 不再写 `null`，改写 `{stage, units_done/units_total, percent}` | 它自认"无真实进度数据"（#14），现在有 |

---

## 5. 核心机制 C：失败分类与重试归属

### 5.1 五类失败（取代"中文文本 + 自由重试"）

| retry_class | 含义 | 自动重试 | 退避 | 谁收尾 |
|---|---|---|---|---|
| `retryable_auto` | 瞬时（网络、5xx、锁冲突、崩溃残留） | ✅ 最多 `max_attempts` | 指数 + 抖动（30s → 2m → 8m） | 系统 |
| `provider_backoff` | 限流 / 配额 / 供应商过载 | ✅ | 尊重 `Retry-After`，否则 5m 起 | 系统 |
| `retryable_manual` | 需要人（文件损坏、token 失效、模型未就绪） | ❌ | — | 用户点"重试这一阶段" |
| `budget_blocked` | 超出成本上限 | ❌ | — | 用户提额或确认继续 |
| `non_retryable` | 数据本身不合法（空文档、页数超限） | ❌ | — | 用户改资料 |

**判定依据只有一处**：`error_code → retry_class` 的映射表，放在 `app/services/pipeline/errors.py`，
错误码沿用既有词表（`app/core/app_error.py` 的 80 个码，**不新造平行体系**）。

### 5.2 重试归属：只有一层

现状是**两层叠加**：Celery 的 `self.retry(max_retries=2)`（`convert_tasks.py:284`）
与僵尸自愈、任务内手工重试混在一起，语义互相打架（重试会重跑整篇）。
目标：

- **Celery 层不再负责业务重试**（去掉流水线任务的 `self.retry`，保留框架级 `acks_late` / `reject_on_worker_lost`）；
- **重试由 `advance()` 决定**：读 `job_stages` 的失败阶段，从该阶段重新入队，`attempt += 1`；
- **自愈并入同一条通路**：`reap_stale_tasks`（现状 `task_run_service.py:285`）改为"把心跳超时的阶段标 `failed(worker_lost)` 并交 `advance()`"。

### 5.3 失败文本的可见面

- 进 API 的：`error_code`（结构化）+ 后端登记的一句 `user_message`（可本地化、不含路径/堆栈）；
- 进日志的：完整堆栈（现状它同时进了库，且可被 API 读走，见 #15）；
- 进 UI 的：`error_code → 文案 + 可做的动作（重试 / 换后端 / 重新上传 / 提额）`。

---

## 6. 核心机制 D：幂等、断点与陈旧

```
advance(note_id):
    job = load_or_create_job(note_id)
    for stage in PLAN[job.plan_version]:
        if job.stop_before == stage.id and user_intent == "only_until_here":
            job.status = paused; return
        fp = fingerprint(stage)                       # 上游产物哈希 + 版本
        row = job_stages[stage]
        if row.status == succeeded and row.fingerprint == fp:
            continue                                  # ← 复用，不执行（现状没有这一步）
        if row.status == failed and row.retry_class in (manual, non_retryable):
            job.status = failed; return               # ← 等人，不自动重跑
        dispatch(stage, from_checkpoint=row.checkpoint)
        return                                        # ← 一次只推进一个阶段
```

- **陈旧（stale）**：源变了 ⇒ 只把 `fingerprint` 会变的下游阶段标 `pending`，
  **指纹未变的阶段保持 `succeeded` 与产物指针**。
  这直接解决两件事：重新清洗不再必然丢向量（只有 `clean` 之后的内容真的变了才重算 `chunk`/`embed`）；
  用户编辑笔记后索引会**自动**重建（现状 #6 完全不重建）。
- **编辑风暴**：编辑接口只**置脏**（`jobs.dirty_at`），重建由 Beat 合并后触发（去抖），
  避免"边打字边重建 FTS"。

---

## 7. 核心机制 E：进度与取消

### 7.1 进度只有一个来源

```
job.progress = Σ(weight[已完成阶段]) + weight[当前阶段] × (units_done / units_total)
```

- 每个阶段必须报真实单位（§3 第 6 项）；没有可切分单位的阶段报"进入/离开"两个点即可；
- 前端只渲染 `job.progress` 与 `stage.label`，**不再有硬编码兜底文案**；
- 旁载 JSON 与 DB 用同一个计算函数（消除 #14）。

### 7.2 取消是阶段协议的一部分

| 阶段 | 可中断粒度 | 中断后的动作 |
|---|---|---|
| `convert` | 逐页批之间 / HTTP 轮询之间 | 已完成批次留在 checkpoint，可续跑 |
| `clean` | 逐批去重之间 | 丢弃未提交的副本，保留原 md |
| `embed` | 逐批 encode 之间 | 已写批次保留（按 `has_embedding` 续跑，**已有能力**） |
| `extract` / `quiz` | 逐章节 / 逐批之间 | 已入库卡片/题目保留（内容寻址天然幂等） |
| `index` / `chunk` | 不可中断（单事务） | 事务回滚，状态回到上一阶段 |

- **取消语义统一**：`jobs.status = cancelled` + `job_stages[current].status = cancelled`；
  `notes.status` 由 `jobs` 派生 ⇒ 不再出现"笔记说失败、任务说取消"（现状 #12）；
  现状的**三套互不兼容**的停止语义（`/api/tasks/{id}/cancel` 只改 `task_runs`、
  `/api/cleaning/{note_id}/stop` 只改 `notes`、回收站走第三条路且文案与任务判据不匹配）
  在这一节里收敛为**一条**：所有"停"都是"把当前阶段标 cancelled，然后由 `advance()` 决定下一步"。
- **诚实性以"多久会停"表达**：文件 broker 无法强杀（`api/tasks.py:165` 继续返回 `terminated=false`），
  但每个阶段声明检查点位置 ⇒ 可以算出**上界**（如"≤1 个批次，约 40 秒"），而不是现状那句无法验证的
  "会在下一个阶段边界退出"。不可中断的阶段（单事务的 `chunk`/`index`）必须**如实显示"本阶段不可中断"**。
- **同步重活的可见化**：`EmbeddingService.encode` 是同步 `def`（`services/embedding_service.py:283`），
  在清洗任务里被直接调用（`clean_tasks.py:268`）⇒ 事件循环占满，期间**心跳与取消都不会发生**。
  迁到 `embed` 队列后由批次边界上报，这条堵点自然消失。
- **删除与取消的竞态**：删除中的笔记不再"立刻删文件"，而是先 `trashed_at` + `purge_after`（阶段 `purge` 才真删）；
  入口的中止判据改为**读 `trashed_at`**，不再依赖中文文案（现状 #40：软删除写的
  `"用户移入回收站"` 与任务入口只认的 `"用户手动删除"` 不匹配 ⇒ 链路照跑，还能把已删除笔记写回 `cleaned/archived`）。

---

## 8. 核心机制 F：唯一入口 `advance()`

**现状**：触发分散在 6 处，各自维护状态闸门与串联逻辑
（`api/upload.py:530` 转换、`api/cleaning.py:142-156` 清洗、`api/understanding.py:187-200` 理解、
`convert_tasks.py:269-277` 自动串清洗、`understand_tasks.py:167` 自动串出题、
`scripts/index_chunks.py`+`scripts/embed_chunks.py` 两个人工脚本）。

**目标**：所有触发都变成"对同一台状态机的一次推进"。

| 触发方 | 现状 | 目标（同形签名） |
|---|---|---|
| 上传完成 | `convert_document_task.delay()` | `advance(note_id, reason="upload", stop_before="extract")` |
| 点"清洗" | `clean_document_task.delay()` | `advance(note_id, reason="user", from_stage="clean", stop_before="extract")` |
| 点"理解" | `understand_document_task.delay()` | `advance(note_id, reason="user", from_stage="extract")` |
| 重跑清洗 | 直接 `.delay()`（旧的 chunk/embed 一键清空） | `invalidate(note_id, "clean")` + `advance()` |
| 编辑笔记 | **什么都不做** | `mark_dirty(note_id)` → Beat 合并 → `advance(from_stage="chunk")` |
| 补嵌入 | 人工脚本 | 阶段 `embed`（`advance()` 自动串，独立队列） |
| 编辑后索引 | 无 | 阶段 `chunk` + `index` 自动重算 |

配套两个持久保障（把"投递成功"从关键路径上摘掉）：

1. **Beat `advance_stalled_jobs`（每 2 分钟）**：`queued/waiting/dirty` 的流水线重新入队。
   于是"投递失败 ⇒ 流水线永久停在半路"（现状 #10）不可能再发生；
2. **Beat `reap_stale_jobs`（每 5 分钟）**：取代现状的 `reap_stale_tasks`（`celery_app.py:145-148`）。

---

## 9. 核心机制 G：成本与队列

### 9.1 LLM 成本进入阶段账本

- LLM 调用审计现状只有 `scene`（`services/llm/scenes.py` 的 30 余处 `scene=`）与账本
  （`services/llm_accounting_service.py:234-255`）；目标：在 `llm_context` 里再带 `job_id/stage`，
  于是"这篇笔记的拆解花了多少钱、哪一步最贵"第一次可回答；
- **预算闸门**放在阶段边界与批次循环边界：超出 `jobs.budget_usd` ⇒
  `paused(budget_blocked)`，**不丢半成品**（已入库的卡片/题目保留），用户可提额继续；
- 重试不重复记账：`llm_cache_service` 命中时记 0 用量（既有能力），阶段级缓存键含 `stage_version`。

### 9.2 队列拓扑（沿用既定约束：嵌入必须隔离在异步 worker）

| 队列 | 消费者 | 任务 | 现状 |
|---|---|---|---|
| `default` | 主 worker（`-c 1`） | convert / clean / extract / quiz / index | 已有 |
| `embed` | **独立** worker（加载 2.2GB 模型） | embed | 新增（现状是人工脚本） |
| `beat` | Beat | advance_stalled_jobs / reap_stale_jobs / 既有维护任务 | 已有 |

`waiting` 状态是这套拓扑的可见化：`embed` worker 不在线时，流水线**明确显示"等待向量 worker"**，
而不是像现在这样静默降级为 BM25-only（#5）。

---

## 10. 对外契约（API / OpenAPI / 前端）

### 10.1 新增（需要 `dump_openapi.py` + `gen:api` 两道门禁）

| 端点 | 形状 | 用途 |
|---|---|---|
| `GET /api/notes/{id}/pipeline` | `{job, stages[], eta_seconds?}` | 被 demo 的收件箱（五段工序时间轴）直接用 |
| `POST /api/notes/{id}/pipeline/retry` | body `{stage}` | 只重跑某一段（现状做不到） |
| `POST /api/notes/{id}/pipeline/cancel` | body `{stage?}` | 语义统一的取消 |
| `GET /api/pipelines?status=active` | `{items[], total}` | **收件箱聚合视图**（demo 契约里已登记为唯一新增接口） |

### 10.2 兼容

- `GET /api/tasks/*` 与 `task_runs` **保留**，响应体加 `job_id`（可空）；
- `notes.status` 继续返回，值由 `jobs.current_stage` 映射（老前端不改也能用）；
- 前端 `TaskProgress.tsx` 改为消费 `/pipeline`，`ProcessingPanel.tsx` 的兜底文案改为读后端 `label`。

---

## 11. 落地路线（每期独立可验证、可回滚）

**执行顺序按 D1：先 S 期（缺陷修复），再 P 期（机制改造）。**

### S 期 · 先修五条会咬人的（下一轮的入口，各自独立提交）

| 期 | 内容 | 完成判据（门禁） |
|---|---|---|
| **S1** 🔴 | **越权**：`begin_task_run` 传 `user_id`（`tasks/common.py:185-190` → 从 note 读出），并对既有行回填；`api/tasks.py` 的两处归属判定改为**拒绝 NULL**（不把"无主"当成"人人可见"） | 新用例：用户 B 用 A 的 `note_id` 列任务 ⇒ 空；点 cancel ⇒ 404。真库现存 4 行全部有主 |
| **S2** | **取消后卡死**：`request_cancel` 同步把 `notes` 从进行中状态改为**可重启**状态；僵尸/收尾扫描把 `cancelled` 也纳入（`models/task_run.py:60`、`task_run_service.py:312-315`） | 用例：取消清洗 / 取消理解后，两条链路的 `start` 都**不再 409** |
| **S3** | **假成功**：内层失败路径改为**抛出**而不是 `return`（`clean_tasks.py:192-214,306-315`；`understand_tasks.py:96-117`；`convert_tasks.py:166-183`），使外层 `except` 能接管；任务收尾读回真实结果 | 用例：构造缺 `original_md_path` 的笔记 ⇒ `task_runs.status != succeeded` |
| **S4** | **重跑毁数据 + 软删除不生效**：重跑理解改为"影子生成 + 原子替换"（先写新产物再交换指针，中途失败旧数据一条不少）；链路入口判据改为**看 `trashed_at`**（`clean_tasks.py:156-160`、`understand_tasks.py:71-76`），并补 `ReviewState` 的孤儿处理口径 | 用例：重跑中途失败 ⇒ 旧卡片/题目/复习记录**一条不少**；回收站笔记的任务入口**立刻退出**，且不会把笔记写回 `cleaned/archived` |

### P 期 · 机制改造

| 期 | 内容 | 完成判据（门禁） |
|---|---|---|
| **P0** | 阶段协议 + `jobs`/`job_stages` 两表 + `advance()` 骨架；**旧链路照跑**，新表影子写入 | 新表行数与旧链路一致；不改任何现有行为 |
| **P1** | `index` 独立成阶段；`embed` 进流水线（独立队列，按 D3 自动跑）；Beat `advance_stalled_jobs` | 新建笔记不再出现 `has_embedding=False`；编辑后索引自动重建（测：改一个字 → FTS 命中新词）；无 `embed` worker 时状态为 `waiting` 且有明示 |
| **P2** | 状态真相迁移：`notes.status` 变派生、`archived_at` 拆出、`error_code` 单一来源 | 71 处 `NoteStatus` 写入收敛到 1 处；`test_task_runs.py` 等既有用例改写后仍绿 |
| **P3** | 触发面收敛：`advance()` 接管 6 处触发 | 删掉 `convert_tasks.py:269-277` 的裸 `except: pass` 后链路仍全程自走 |
| **P4** | 前端接 `/pipeline`：工序时间轴 + 逐段重试 + 诚实取消 | E2E：打断 `extract` → 从断点重试 → 只重跑该段（不重跑 convert/clean） |
| **P5** | 成本账本按阶段归因（闸门机制保留但**默认关闭**，按 D5） | 真库上能答"这篇笔记拆解花了多少钱" |

每期收尾必须过：后端 `ruff` + 离线 `pytest` + `dump_openapi.py --check`；
前端（P4/P5）`lint` + `test` + `build` + `gen:api:drift`。

---

## 12. 与既定决策的关系（不推翻，只细化）

| 既定决策 | 本方案的关系 |
|---|---|
| 保留 SQLite、单写者、不引中间件（`docs/sqlite-single-writer.md`） | **完全遵守**。只加两张表 + 若干列，无新依赖 |
| 嵌入模型隔离在异步 worker | **强化**：从"人工脚本"变成"独立队列的正常阶段" |
| 向量与原文同库同表 | **遵守**（继续用 `chunks.embedding`） |
| 不引第二套迁移工具 | **遵守**：复用 `_migrate_sqlite` 的非破坏性加列通道 |
| overhaul-plan P6（长任务有进度、可取消、可重试、可自愈） | **本方案是 P6 的可落地实现**；现状它只落了一半 |
| overhaul-plan P7（失败必须响亮） | **强化**：`except: pass`（#10）与"内层失败外层报成功"（#0.3）都在这条口径下消除 |

---

## 13. 不做什么（划清边界）

- 不换数据库、不引 Redis / 消息队列 / 第二套迁移工具（`AGENTS.md` §2）；
- 不把处理改成"事件溯源"或"工作流引擎"：阶段是**有限、线性**的，够用即可；
- 不在本方案里改产品语义（出题数量、判分口径、掌握度公式等）；
  已知的语义缺陷（如 `goal_service` 的达成判定、`generate_questions` 的"重新生成即删旧题与复习记录"）
  **只登记，另行拍板**；
- 不做前端视觉改造（样式与信息架构以既有的评审 demo 口径为准）。

---

## 14. 决策登记（原"待拍板"清单，2026-09-25 已全部回答）

| # | 问题 | 结论 | 落在本文哪一节 |
|---|---|---|---|
| 1 | S1~S4 先修还是并入 | **先修**（S 期），本轮仍不动代码 | §0.1、§0.2、§11 |
| 2 | 本轮落地范围 | **只出方案** | §0.2（D2） |
| 3 | `embed` 是否默认自动跑 | **自动跑**，无 worker 时 `waiting` 并明示 | §9.2、§11 P1 |
| 4 | `purge` 竞态 | **接受延迟释放**（回收站 + `purge_after`） | §7、§11 |
| 5 | 成本闸门默认值 | **默认关闭** | §9.1、§11 P5 |
| 6 | `chunks.id` 切 `chunk_key` | **不切**，只加备用列 | §4.3 |

**仍然悬置、需要单独拍板的产品语义问题**（本方案刻意不夹带，逐条有证据）：

1. **未判分 ≠ 答错**（#22）：LLM 判分失败时 `quality=1 / is_correct=False` 落库，被日报、趋势、薄弱点、
   掌握度当成答错统计 —— 要不要引入 `ungraded` 口径并让所有报表排除它？
2. **学习目标达成判定量纲错位**（#26）：`progress >= target` 应为 `avg_mastery >= target`；
   已判完成的历史目标要不要重算（我倾向不重算，只登记"存量已知"）？
3. **重跑的语义**：出题"重新生成"是否仍应删除旧题与其复习记录，还是改为版本化替换（与 S4 同源问题）？
4. **卡片复习是否占用答题日额度**（#25）：现在是占用，与"卡片不受限额约束"的注释相反 —— 以哪个为准？
5. **7 天趋势恒为 0**（#27）与 **`predicted_retention` 零消费**（#28）：前者是确定性 bug，后者是
   "校准闭环"这个产品承诺的落空 —— 要不要在本轮之外单开一期修？

---

## 附：本方案的证据来源

- 代码盘点（三条链路，均带 `文件:行号`）：上传/转换/索引、清洗/理解、复习/度量；
- 真库只读普查（26 张表、`task_runs` 仅 4 行、`chunks` 608 行全部 `has_embedding=1`、
  `notes` 20 条全部 `archived/cleaned`）；
- 既有台账：`docs/overhaul-plan.md`（阶段 1′ 1.7/1.8、阶段 2.2′、阶段 4.2/4.6/4.7）、
  `docs/sqlite-single-writer.md`、`AGENTS.md`。
