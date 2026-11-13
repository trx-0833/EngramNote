# AI 问答历史持久化（QA History Persistence）

> **状态：待批（方案稿，尚未动代码）** ｜ 起草：2026-11-13 ｜ 三个待确认项见 §10 ｜
> 权威性：**本主题（问答历史 / `/qa` 会话）的设计依据**；现状描述仍以代码实测为准（`AGENTS.md` §1）。
> 与既有文档的关系：`docs/overhaul-plan.md` 仍是重构期总台账；本文件是**问答历史**这一件事的细化。
> 相关现状：`frontend/src/pages/QA.tsx`（本页）、`backend/app/api/understanding.py:645` 与 `backend/app/api/notes/ask.py:73`（两个问答端点）。

---

## 0. 结论先行

**问答内容现在是零持久化**：前端 `history` 是组件内存（`QA.tsx:134`），后端没有会话表、没有历史端点，
流式问答也**不做响应缓存**（`llm_service.py` 的 `chat_stream` 自述"不做响应缓存"），
`llm_calls` 账本只记 token / cost / latency，**不记 prompt 与答案文本**。
所以"刷新就没了"不是读取缺陷，而是根本没有存储位置。

本计划要做的：**把每一轮变成可恢复、可删除、可搜索的记录**，同时**不改"单问单答"的语义**
（模型仍然看不到上一轮）。

---

## 1. 目标与非目标

### 1.1 目标

| #   | 目标                                         | 判据                                                                                                        |
| --- | -------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| 1   | 刷新 / 切路由 / 关标签页后仍能看到问过的问答 | 重新进入 `/qa`，历史列表里有那条会话                                                                        |
| 2   | 可按会话回看（含引用来源）                   | 点开会话能看到每一轮的问题、答案与引用片段                                                                  |
| 3   | 可删除                                       | 删除会话后列表与库里都不再出现（软删，30 天后硬删）                                                         |
| 4   | 可搜索                                       | 按关键词能搜到问题与答案文本                                                                                |
| 5   | 成本可追溯                                   | 每轮记录 `provider` / `model` / `latency` / `prompt_version`，token 与费用仍以 `llm_calls` 为准（不重复存） |

### 1.2 明确不做

| 不做                                             | 理由                                                                                            |
| ------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| **多轮上下文回灌**（把最近 N 轮塞进 `messages`） | 那是另一个产品决策（C 档）：涉及 token 预算、来源归并、以及"答案里混入历史错误"的风险。另开一轮 |
| 问答历史的 ZIP 导出 / 导入                       | 它不是"笔记内容"，导出语义与 `note-export.md` 那套不同；等真有需求再定形态                      |
| 把回答向量化进检索                               | 问答是"读者视角"，不是语料；混进去会污染 `chunks` 的语义                                        |
| 改 `llm_cache` 来缓存问答                        | 流式路径刻意不缓存；且缓存 key 不含 `user_id`（见 §7 的隐私条目）                               |
| 跨设备同步冲突处理                               | 单机单人形态下没有并发写同一会话的真实路径                                                      |

### 1.3 形态选择：B 档「Q&A 时间线」

`session` **只作分组容器**（同一屏连续追问归到一个会话），**不参与提示词构造**。
一轮一条记录，可回看、可搜索、可删除。

---

## 2. 数据模型：两张新表

新增 `backend/app/models/qa_history.py`，沿用仓库既有写法（`BaseModel` + 显式 `mapped_column` + 长注释说明取舍）。

### 2.1 `qa_sessions`

| 列                          | 类型                     | 说明                                                                  |
| --------------------------- | ------------------------ | --------------------------------------------------------------------- |
| `id`                        | `String(36)` PK          | UUID                                                                  |
| `user_id`                   | `String(36)`，索引，非空 | 归属                                                                  |
| `surface`                   | `String(16)`，非空       | `global`（`/qa`）｜ `note`（笔记浮层）                                |
| `note_id`                   | `String(36)`，可空       | 仅 `surface=note` 时有值；**不设外键**（笔记被 purge 后历史仍应可读） |
| `title`                     | `String(120)`，可空      | 首个问题截断 80 字；`NULL` 时前端显示"未命名对话"                     |
| `created_at` / `updated_at` | `DateTime(tz)`           | `updated_at` = 最后一轮时间，列表按它倒序                             |
| `deleted_at`                | `DateTime(tz)`，可空     | 软删                                                                  |

索引：`(user_id, updated_at)`、`(deleted_at)`。

### 2.2 `qa_turns`

| 列                                      | 类型                                                    | 说明                                                                               |
| --------------------------------------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `id`                                    | `String(36)` PK                                         | UUID                                                                               |
| `session_id`                            | `String(36)` FK → `qa_sessions.id`，`ON DELETE CASCADE` |                                                                                    |
| `user_id`                               | `String(36)`，索引，非空                                | 冗余一份，列表查询免 join                                                          |
| `question`                              | `Text`，非空                                            | 用户输入原文                                                                       |
| `answer`                                | `Text`，可空                                            | **最终答案**（不含 token 中间态）                                                  |
| `status`                                | `String(16)`，非空                                      | `ok` ｜ `failed` ｜ `aborted`                                                      |
| `error_code`                            | `String(64)`，可空                                      | 失败时的机器可读码                                                                 |
| `provider` / `model`                    | `String(32/64)`，可空                                   | 回答由哪个供应商/模型产出                                                          |
| `prompt_version`                        | `String(32)`，可空                                      | 便于将来做"提示词版本对照"（与 `prompt_version_comparison*.md` 同口径）            |
| `retrieval_status`                      | `String(32)`，可空                                      | 仅 `surface=global`：`full_vector` / `hybrid` 等                                   |
| `sources_json`                          | `Text`，可空                                            | 最终引用数组：`note_id` / `note_title` / `chunk_id` / `snippet`(≤200 字) / `score` |
| `source_span_start` / `source_span_end` | `Integer`，可空                                         | 笔记场景的选区偏移                                                                 |
| `source_hash`                           | `String(64)`，可空                                      | 选中文本的 sha256                                                                  |
| `latency_ms`                            | `Integer`，可空                                         | 首字节或整体耗时（与 `llm_calls.latency_ms` 口径一致）                             |
| `created_at`                            | `DateTime(tz)`                                          |                                                                                    |

索引：`(session_id, created_at)`、`(user_id, created_at)`。

### 2.3 三个刻意的取舍

1. **不存组装后的 prompt**（系统提示词 + 检索正文拼接）。体积大、含内部提示词；复现靠
   `(question, chunk_id 列表, prompt_version)` 重放。
2. **不存 token 中间态 / SSE 原始帧**，只存最终答案。中止的轮次存 `status=aborted` + 已生成文本。
3. **选中文本不存全文**，存 `note_id + 起止偏移 + sha256`。正文本来就在库里、同一用户可回查；
   存快照会让"删了笔记，历史里还残留正文副本"。

---

## 3. 接口契约：加三个端点，改一处 SSE 事件

新增 `backend/app/schemas/qa.py`（Pydantic 响应模型），包 `backend/app/api/qa.py`（新文件，便于整体回滚）。

### 3.1 端点

| 方法     | 路径                            | 参数                                                                                     | 响应                                                 |
| -------- | ------------------------------- | ---------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| `GET`    | `/api/qa/sessions`              | `page`（默认 1）／`page_size`（默认 20，上限 100）／`keyword`（搜问题与答案）／`surface` | `{items, total, page, page_size}`                    |
| `GET`    | `/api/qa/sessions/{session_id}` | —                                                                                        | `{session, turns}`（轮次按 `created_at` 升序）       |
| `DELETE` | `/api/qa/sessions/{session_id}` | —                                                                                        | `204`（软删；已删再删 → `404 QA_SESSION_NOT_FOUND`） |

### 3.2 既有契约的一处改动

两个 `ask/stream` 端点的 **SSE `meta` 事件多返回 `session_id`**：

- 客户端没带 `session_id` → 服务端新建会话，在 `meta` 帧里回传；
- 客户端带 `session_id` → 追加到同一会话（不新建）。

影响面：

- `backend/app/api/understanding.py:645`（全库）与 `backend/app/api/notes/ask.py:73`（笔记）的 `meta` 帧；
- 前端 `parseSSEStream` 的 `meta` 分支要接住这个字段（`frontend/src/utils/sse.ts` 及其调用点）；
- 请求体各加一个**可选**字段 `session_id`（向后兼容：不带就新建）。

### 3.3 错误码

新增 `QA_SESSION_NOT_FOUND`（404）与 `QA_HISTORY_WRITE_FAILED`（内部只记日志，**不返回给用户**——
回答本身不该因为历史写失败而失败）。二者都要进 `backend/app/core/error_messages.py` 的中英文案。

---

## 4. 落库时机：服务端在流结束时写

### 4.1 为什么不选"前端二次上传"

| 方案                                  | 缺点                                                                                      |
| ------------------------------------- | ----------------------------------------------------------------------------------------- |
| 前端拿到 `done` 后再 `POST /qa/turns` | 多一次往返；用户关页面 / 断网 / 刷新就丢；可被伪造                                        |
| **服务端在流收尾时写（选定）**        | 无额外往返；要素齐全（`user_id`、`question`、累积答案、`sources`、`provider`、`latency`） |

### 4.2 三个分支都要落

| 分支                          | 落库内容                                                                                                       |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------- |
| 正常结束                      | `status=ok` + 完整答案 + sources                                                                               |
| LLM 报错                      | 先发 `error` 帧，再写 `status=failed` + `error_code`（答案留空或部分）                                         |
| 客户端断开 / `CancelledError` | `status=aborted` + 已累积文本。**这一步必须放在 `finally` 里** —— 用户点「停止生成」的那一轮恰恰是最需要回看的 |

### 4.3 不能拖垮主流程

整段包 `try/except`，失败只 `logger.warning`，与"附加字段不该让详情页 500"同一原则
（`backend/app/api/notes/list_detail.py` 里 `pending_embedding_chunks` 的注释有先例）。
写入使用**独立 session**（不复用请求的 db 会话），且在**流结束之后**，
以保证不改动首字节时间（`notes/ask.py` / `understanding.py` 现在流内不开事务，接线时要显式确认）。

---

## 5. 前端改动：一条读取链 + 一个历史入口

### 5.1 API 层

`frontend/src/api/qa.ts` 新增 `getQaSessions` / `getQaSession` / `deleteQaSession`：
参数类型用 `QueryOf<'/qa/sessions','get'>` 派生（与 `exportNotes` 同款），随后跑 `npm run gen:api` 再生 `schema.ts`。

### 5.2 `QA.tsx`

- 挂载时用 TanStack Query 拉 `['qa','sessions']`（沿用现有 `staleTime` 约定）；
- `PageHeader` 右侧加「历史」按钮 → 抽屉（复用 `Dialog` 基座）列会话：标题 + 最后时间 + 轮次数；
- 点一条会话 → 把其轮次装进现有 `history` 状态（复用现有气泡渲染，零重写）；
- 会话 id 挂 URL：`?s=<session_id>`（复用上一轮引入的 `hooks/useUrlState`），于是"发链接 / 返回本页"都能回到同一会话；
- 流结束后 `invalidateQueries(['qa','sessions'])`，新会话/新轮次立刻出现在列表；
- 空态区分两种：**从未问过**（现有 `qa.empty`）与**历史为空**（新 key）。

### 5.3 笔记浮层（`NoteAskPanel`）

本轮**只落库、不做历史 UI**（`surface=note`）。理由：浮层是"读这段时临时问一句"，
做成可回看列表会改变这个功能的语义；需要时再单独一轮。

### 5.4 i18n

新增 `qa.history` / `qa.historyEmpty` / `qa.historyUntitled` / `qa.deleteSession` /
`qa.deleteConfirm` 等键，中英各一份（`messages.zh-CN.ts` / `messages.en.ts`）。

---

## 6. 迁移、保留与清理

### 6.1 建表不需要破坏性迁移

新表由 `init_db()` 的 `Base.metadata.create_all` 自动创建（`backend/app/database.py:288`）。
`_migrate_sqlite` 的 `ALTER TABLE ... ADD COLUMN` 是给**既有表补列**用的，本次用不到，
因此**没有** `ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION` 门槛，也没有数据重建。

### 6.2 保留策略（照抄 `cleanup_llm_ledger` 先例）

- `backend/app/config.py` 加 `qa_history_retention_days: int = 90`；
- 新增 Beat 任务 `cleanup_qa_history`（`backend/app/tasks/celery_app.py` 的 `beat_schedule` 加一条，
  排在 `cleanup_llm_ledger` 之后；实现放 `maintenance_tasks.py`）；
- 规则：软删 30 天以上的行**硬删**（级联删轮次）；超过 90 天的会话按"最老优先"删到上限内。

### 6.3 与笔记删除的联动

| 事件         | 对历史的影响                                                                 |
| ------------ | ---------------------------------------------------------------------------- |
| 笔记进回收站 | **不动**（用户可能还要看那几轮问答）                                         |
| 笔记被 purge | 只清 `sources_json` 里的 `snippet`（保留 `chunk_id` 作墓碑），问题与答案保留 |
| 用户删除会话 | 软删 → 30 天后硬删                                                           |

---

## 7. 隐私与安全口径

1. **不做跨用户共享缓存**：`llm_cache` 的 key 只按 prompt 算、**不含 `user_id`**。
   哪天有人把问答接进缓存，就会出现"A 的答案给了 B" —— 这条要写进服务层注释，作为硬约束。
2. **默认日志不落正文**：提示词只在 `logger.debug` 里出现且被截断（`services/llm/gateway.py:350-354`），
   启动日志是 `INFO` ⇒ 默认不写。落库后正文只在 `qa_turns.answer`，由用户显式删除。
3. **鉴权与归属**：列表与会话详情都按 `user_id` 过滤；别人的会话 → `404`（不泄露"这个 id 存在"，
   与 `NOTE_NOT_FOUND` 同一口径）。
4. **可删除**：会话级软删 + 硬删；不做"删除单轮"（MVP），避免多一个端点与一套级联规则。

---

## 8. 测试方法（分层，先写断言再写实现）

### 8.1 后端服务层（`backend/tests/test_qa_history_service.py`，约 12 条）

创建会话 → 追加轮次 → `updated_at` 跟着变；列表按 `updated_at` 倒序；`keyword` 能搜到问题与答案；
`surface` / `note_id` 过滤；软删后不在列表但行还在；硬删级联删轮次；`failed`/`aborted` 轮次也进历史；
答案超长时的截断策略；`sources_json` 序列化往返一致；笔记 purge 后片段被清空而问题保留；
写入异常被吞掉且不影响调用方。

### 8.2 后端端点（`backend/tests/test_qa_history_api.py`，约 8 条）

需鉴权（401）；别人家的会话（404）；分页边界（`page=0` / 超大 `page_size`）；
`DELETE` 两次第二次 404；空列表返回 `{items: [], total: 0}`；`surface` 非法值 → 422。

### 8.3 SSE 落库（`backend/tests/test_ask_stream_persists_turn.py`，约 4 条）—— 不可省

用假 LLM 生成器驱动端点：流结束后库里出现一轮 `ok`；LLM 抛错出现 `failed` + `error_code`；
客户端取消出现 `aborted` 且已有部分文本；`meta` 帧带回 `session_id`，第二轮带同一 id 不新建会话。

### 8.4 契约（既有守卫自动覆盖）

`python scripts/dump_openapi.py --check` 一致；新端点都有 `response_model`
（`tests/test_openapi_response_contract.py` 会拦）；`DELETE .../sessions/{id}` 加进
`NON_JSON_2XX` 白名单并写明理由（该白名单是**穷举**的）。

### 8.5 前端单测（`src/pages/QA.test.tsx` +5）

挂载时请求会话列表并渲染「历史」按钮；抽屉里显示标题与时间；点一条 → 渲染该会话的轮次（含 sources）；
删除会话 → 调 API 且从列表消失；空历史显示新空态（与"从未问过"区分）。

### 8.6 前端 API 层（`src/api/qa.test.ts` +3）

URL 与查询串拼装（分页、`keyword` 编码）；`deleteQaSession` 走 `DELETE` 且 204 返回 `void`；错误码透传。

### 8.7 e2e / a11y（`frontend/e2e/qa-history.spec.ts`，2 条）

用 `e2e/a11y-fixtures.ts` 的桩造出 `GET /api/qa/sessions` 与详情：
真浏览器里点「历史」→ 抽屉可开、`Esc` 可关、焦点回到触发按钮；
axe 扫描抽屉打开态无新增违规；抽屉里的会话项可 Tab 到（`expectReachableByTab`）。

### 8.8 回归门禁（改完必须全绿）

后端：`ruff check app tests scripts`、`pytest -q`（当前基线 1847 passed）、`dump_openapi.py --check`、
`gen_env_example.py --check`。
前端：`tsc --noEmit`、`npm run lint`、`vitest run`（当前基线 796）、`npm run build`、
`npm run e2e`（28）、`npm run a11y`（26）、`prettier --check --end-of-line auto`、`npm run docs:links`。

---

## 9. 提交切分与顺序

| #   | 提交                                     | 内容                                                                       | 可独立回滚 |
| --- | ---------------------------------------- | -------------------------------------------------------------------------- | ---------- |
| 1   | `feat(qa): 问答历史的数据模型与保留策略` | 模型 + 配置 + 清理任务 + 模型级测试（尚未接线）                            | ✅         |
| 2   | `feat(qa): 问答历史的服务层与三个端点`   | `qa_history_service.py` + `api/qa.py` + schemas + 契约再生 + 端点测试      | ✅         |
| 3   | `feat(qa): 流式问答在结束时落库`         | 两个 `ask/stream` 接上 `record_turn`，`meta` 加 `session_id`，SSE 落库测试 | ✅         |
| 4   | `feat(qa): 问答页的历史抽屉与 URL 会话`  | 前端 API 层 + `QA.tsx` + i18n + 单测 + e2e/a11y                            | ✅         |
| 5   | `docs(qa): 问答历史的设计与保留口径`     | 本文件定稿 + `agent-workstream-progress.md` 记一行                         | ✅         |

依赖关系：1 → 2 → 3 顺序固定；4 依赖 2（可与 3 并行）；5 最后。

---

## 10. 待确认的三个决策

| #   | 问题                                                       | 我的建议                            | 影响                                  |
| --- | ---------------------------------------------------------- | ----------------------------------- | ------------------------------------- |
| 1   | 形态：B 档"时间线"（session 只分组），多轮上下文另开一轮？ | **是**                              | 决定表结构与是否要改提示词            |
| 2   | 失败 / 中止的轮次入历史吗？                                | **入，只留状态 + 已生成的部分文本** | 决定 `status` 分支与测试（8.3）       |
| 3   | 笔记浮层本轮只落库、不做历史 UI？                          | **是**                              | 决定 §5.3 与是否需要改 `NoteAskPanel` |

三点确认后即可开工；若第 1 点改成"要支持多轮上下文"，本计划的 §2（加 `messages_json` 或按轮查）、
§3.2（请求体加 `history_turns` 或服务端回灌）、§8.3（上下文裁剪测试）都要扩，工时 +1 天左右。

---

## 11. 风险与回滚

| 风险                                 | 处置                                                                   |
| ------------------------------------ | ---------------------------------------------------------------------- |
| SSE 生成器里做 DB 写，可能拖慢首字节 | 写入在**流结束之后**，且用独立 session；§8.3 有对应断言                |
| 落库失败影响回答                     | 全包 `try/except` + `warning`，异常绝不穿出生成器                      |
| 历史表无限增长                       | 保留期 + Beat 清理（第 1 个提交就带上）                                |
| 隐私：答案可能含笔记原文             | 会话级删除；笔记 purge 时清片段；不做跨用户缓存（§7）                  |
| 前端抽屉改变 `/qa` 既有布局          | 抽屉默认**关闭**，未点「历史」时页面与现状逐像素一致；e2e 快照不受影响 |

**开关与回滚**：`config.py` 加 `qa_history_enabled: bool = True`；
置 `False` ⇒ 不写库、不显示历史入口（端点仍可读已有历史）。代码按提交粒度 `git revert`；
两张表可保留（`create_all` 只增不删）。

---

## 12. 工时估算

| 环节                                      | 工时                         |
| ----------------------------------------- | ---------------------------- |
| 数据层（模型 + 配置 + 清理任务 + 测试）   | 0.3 天                       |
| 服务层 + 三个端点 + 契约再生              | 0.4 天                       |
| 流式落库接线 + SSE 测试                   | 0.4 天                       |
| 前端（API 层 + 抽屉 + URL + i18n + 单测） | 0.5 天                       |
| e2e / a11y / 文档 / 全量门禁              | 0.4 天                       |
| **合计**                                  | **≈ 2 天**（不含多轮上下文） |
