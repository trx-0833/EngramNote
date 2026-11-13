# AI 问答历史持久化 + 可追问（QA History & Multi-turn）

> **状态：待批（方案稿，尚未动代码；2026-11-13 依"要能追问"改为 C 档多轮）** ｜ 起草：2026-11-13 ｜
> 剩两个待确认项见 §10 ｜ 权威性：**本主题（问答历史 / 会话与多轮上下文）的设计依据**；
> 现状描述仍以代码实测为准（`AGENTS.md` §1）。
> 与既有文档的关系：`docs/overhaul-plan.md` 仍是重构期总台账；本文件是**问答**这一件事的细化。
> 相关现状：`frontend/src/pages/QA.tsx`、`backend/app/api/understanding.py:645`（全库问答）、
> `backend/app/api/notes/ask.py:73`（笔记浮层问答）。

---

## 0. 结论先行

**现状：问答内容零持久化，且每一问都是孤立的一问一答。**

| 事实                   | 证据                                                                                                                                                    |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 前端"历史"只是组件内存 | `QA.tsx:134` `const [history, setHistory] = useState<QARecord[]>([])`，无 `localStorage`，挂载不拉任何历史                                              |
| 后端没有会话表         | `backend/app/models/` 22 个文件里无 `Conversation*` / `Chat*` / `Qa*`                                                                                   |
| 请求里没有上下文       | `askQuestionStream(question, signal)` → body 只有 `{question}`（`api/client.ts`）；服务端 `messages` 只有 `system + 当前一问`（`notes/ask.py:120-141`） |
| 回答也不缓存           | `llm_service.py` 的 `chat_stream` 自述"**不做响应缓存**"                                                                                                |
| 账本不存正文           | `models/llm_call.py:71` 只有 tokens / cost / latency / success / error                                                                                  |

**本计划做两件事**：① 把每一轮落库（可恢复、可删、可搜）；② **让会话支持追问** ——
第二轮起把最近若干轮作为 `messages` 送给模型，使"那它的寿命呢？"这类省略主语的追问能接上。

多轮会带来三类新问题（上下文预算、检索用什么查询词、以及"历史里的错误被继承"），
分别对应 §4.2 / §4.3 / §4.4 的处置。

---

## 1. 目标与非目标

### 1.1 目标

| #   | 目标                                         | 判据                                                                                                            |
| --- | -------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| 1   | 刷新 / 切路由 / 关标签页后仍能看到问过的问答 | 重新进入 `/qa`，历史列表里在                                                                                    |
| 2   | **同一会话里能追问**，省略主语的问句也能接上 | 首轮问"浮充和均充的区别"，追问"那它对寿命有影响吗"能基于上文作答                                                |
| 3   | 可按会话回看（含每轮引用来源）               | 点开会话能看到每一轮的问题、答案、来源                                                                          |
| 4   | 可删除 / 可搜索                              | 删除会话后（软删）列表与库里都不再出现；关键词能搜到问题与答案                                                  |
| 5   | 成本与上下文可追溯                           | 每轮记 `provider` / `model` / `latency` / `prompt_version` / `context_turns`；token 与费用仍以 `llm_calls` 为准 |

### 1.2 明确不做

| 不做                       | 理由                                                                                   |
| -------------------------- | -------------------------------------------------------------------------------------- |
| 把整段历史无限带下去       | token 成本线性增长、且越远的历史越容易污染当前回答。只带最近 N 轮（§4.2）              |
| 前端上传"历史消息数组"     | 可伪造、可放大。**上下文由服务端按 `session_id` 自己读**（§4.1）                       |
| 追问时复用上一轮的检索结果 | 第二个问题可能换了主题；每轮都重新检索，只有**查询词**参考上文（§4.3）                 |
| 历史会话的 ZIP 导出 / 导入 | 不是"笔记内容"，与 `note-export.md` 那套语义不同                                       |
| 问答缓存                   | 流式路径刻意不缓存；且 `llm_cache` 的 key 不含 `user_id`，接进去会造成跨用户泄漏（§7） |

### 1.3 形态：C 档「可追问的会话」

`session` 从"分组容器"升级为**上下文边界**：一个会话内的轮次按时间顺序拼成 `messages`。
会话由首问隐式创建；"新建对话"= 前端清掉 `?s=`，下一问自动开新会话。

---

## 2. 数据模型：两张新表

新增 `backend/app/models/qa_history.py`（`BaseModel` + 显式 `mapped_column` + 长注释说明取舍）。

### 2.1 `qa_sessions`

| 列                          | 类型                     | 说明                                                         |
| --------------------------- | ------------------------ | ------------------------------------------------------------ |
| `id`                        | `String(36)` PK          | UUID                                                         |
| `user_id`                   | `String(36)`，索引，非空 | 归属                                                         |
| `surface`                   | `String(16)`，非空       | `global`（`/qa`）｜ `note`（笔记浮层）                       |
| `note_id`                   | `String(36)`，可空       | 仅 `surface=note`；**不设外键**（笔记被 purge 后历史仍可读） |
| `title`                     | `String(120)`，可空      | 首个问题截断 80 字；`NULL` → 前端"未命名对话"                |
| `turn_count`                | `Integer`，非空默认 0    | 冗余计数：列表页不必为每行数轮次                             |
| `last_question`             | `String(200)`，可空      | 列表副标题用（"最后一句问的是什么"）                         |
| `created_at` / `updated_at` | `DateTime(tz)`           | `updated_at` = 最后一轮时间，列表按它倒序                    |
| `deleted_at`                | `DateTime(tz)`，可空     | 软删                                                         |

索引：`(user_id, updated_at)`、`(deleted_at)`。

### 2.2 `qa_turns`

| 列                                      | 类型                                                    | 说明                                                                                            |
| --------------------------------------- | ------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `id`                                    | `String(36)` PK                                         | UUID                                                                                            |
| `session_id`                            | `String(36)` FK → `qa_sessions.id`，`ON DELETE CASCADE` |                                                                                                 |
| `user_id`                               | `String(36)`，索引，非空                                | 冗余一份，列表查询免 join                                                                       |
| `question`                              | `Text`，非空                                            | 用户输入原文                                                                                    |
| `answer`                                | `Text`，可空                                            | **最终答案**（不含 token 中间态）                                                               |
| `status`                                | `String(16)`，非空                                      | `ok` ｜ `failed` ｜ `aborted`                                                                   |
| `error_code`                            | `String(64)`，可空                                      |                                                                                                 |
| `provider` / `model`                    | `String(32/64)`，可空                                   |                                                                                                 |
| `prompt_version`                        | `String(32)`，可空                                      | 与 `prompt_version_comparison*.md` 同口径，便于以后做版本对照                                   |
| `retrieval_status`                      | `String(32)`，可空                                      | 仅 `surface=global`                                                                             |
| `retrieval_query`                       | `Text`，可空                                            | **实际用于检索的查询词**（追问时是拼接后的，见 §4.3）——没有它，"为什么这轮没检索到"永远查不出来 |
| `sources_json`                          | `Text`，可空                                            | 最终引用数组：`note_id` / `note_title` / `chunk_id` / `snippet`(≤200 字) / `score`              |
| `context_turns`                         | `Integer`，非空默认 0                                   | 本轮**实际带上了几轮历史**（预算裁剪后）                                                        |
| `source_span_start` / `source_span_end` | `Integer`，可空                                         | 笔记场景的选区偏移                                                                              |
| `source_hash`                           | `String(64)`，可空                                      | 选中文本的 sha256                                                                               |
| `latency_ms`                            | `Integer`，可空                                         |                                                                                                 |
| `created_at`                            | `DateTime(tz)`                                          |                                                                                                 |

索引：`(session_id, created_at)`、`(user_id, created_at)`。

### 2.3 四个刻意的取舍

1. **不存组装后的完整 prompt**（系统提示词 + 历史 + 检索正文拼接）。
   体积大、含内部提示词；复现靠 `(question, 前 N 轮的 question/answer, chunk_id 列表, prompt_version)` 重放。
2. **不存 token 中间态 / SSE 原始帧**，只存最终答案；中止轮次存 `status=aborted` + 已生成部分。
3. **选中文本不存全文**（笔记浮层），存 `note_id + 起止偏移 + sha256`。
4. **`retrieval_query` 必须存**：多轮里"检索词被改写"是新的失败面，不落库就无法归因。

---

## 3. 接口契约

新增 `backend/app/schemas/qa.py` 与 `backend/app/api/qa.py`（新文件，便于整体回滚）。

### 3.1 新增三个端点

| 方法     | 路径                            | 参数                                                            | 响应                                           |
| -------- | ------------------------------- | --------------------------------------------------------------- | ---------------------------------------------- |
| `GET`    | `/api/qa/sessions`              | `page` / `page_size`（默认 20，上限 100）／`keyword`／`surface` | `{items, total, page, page_size}`              |
| `GET`    | `/api/qa/sessions/{session_id}` | —                                                               | `{session, turns}`（轮次按 `created_at` 升序） |
| `DELETE` | `/api/qa/sessions/{session_id}` | —                                                               | `204`；已删再删 → `404 QA_SESSION_NOT_FOUND`   |

### 3.2 改两处既有契约（两个 `ask/stream`）

**请求体**各加一个**可选** `session_id`（不带则新建会话，向后兼容）：

```
POST /api/understanding/ask/stream     { question: str, session_id?: str }
POST /api/notes/{note_id}/ask/stream   { question: str, selected_text: str, session_id?: str }
```

**SSE `meta` 帧**多返回两个字段：

| 字段           | 含义                                                               |
| -------------- | ------------------------------------------------------------------ |
| `session_id`   | 本轮所属会话（新建时即为新建的那个）——前端据此写进 `?s=`           |
| `history_used` | 本轮实际带上了几轮历史（0 = 首轮或预算裁到 0），用于前端提示与排障 |

影响面：`understanding.py:645`、`notes/ask.py:73` 的 `meta` 帧；前端 `utils/sse.ts` 的 `meta` 分支与其调用点。

### 3.3 错误码

新增 `QA_SESSION_NOT_FOUND`（404，别人的会话同样是 404，不泄露存在性）。
历史写失败**不返回给用户**，只 `logger.warning`（见 §4.5）。

---

## 4. 多轮上下文（本次改动的核心）

### 4.1 上下文由服务端组装，不信前端

客户端只发 `session_id`；服务端按 `(session_id, user_id)` 查最近 N 轮（`status='ok'` 才参与，
见 §4.4），自己拼 `messages`。这样：不可伪造、不放大请求体、且"删掉某一轮就不再影响后续回答"。

### 4.2 `messages` 的形状与预算

```
[system]  固定系统提示词（含"历史只用于理解指代，事实以本轮片段为准"，见 §4.4）
[user]    Q_{k-n}      … 最近 N 轮
[assistant] A_{k-n}
…
[user]    Q_{k-1}
[assistant] A_{k-1}
[user]    本轮：检索片段 + 当前问题（与现状同形）
```

**预算规则**（三条同时生效，按顺序裁）：

| 规则         | 值                                                                   | 理由                                                         |
| ------------ | -------------------------------------------------------------------- | ------------------------------------------------------------ |
| 最多带几轮   | `QA_CONTEXT_MAX_TURNS = 6`                                           | 6 轮 ≈ 12 条消息，足够消解指代，不至于把上下文撑爆           |
| 单条答案截断 | 每条历史答案取前 `QA_CONTEXT_ANSWER_CHARS = 400` 字，超长加"…（略）" | 长答案（尤其带表格的）会把预算吃光，而指代通常只需要开头几句 |
| 总字符上限   | `QA_CONTEXT_MAX_CHARS = 4000`，超出从**最旧**开始丢                  | 与检索片段共用预算时的兜底                                   |

三个值都进 `config.py`（`.env.example` 一并再生），便于按语料调整。

### 4.3 追问时检索用什么查询词

**问题**：追问常常是"那它的寿命呢？"——直接拿这句话去检索，几乎必然检索不到。

**方案（选定，零额外 LLM 调用）**：检索查询词 = `上一轮问题 + 当前问题` 的拼接（中间用空格）：

```
retrieval_query = f"{previous_question} {current_question}".strip()
```

- 覆盖"省略主语/指代"的常见形态，且**确定性、可测试**（落库到 `retrieval_query`，见 §2.2）；
- 若拼接后仍检索不到（`sources` 为空且语料非空），再退回**只用当前问题**检索一次，
  两次都空就如实返回"没有找到相关片段"（不编造）；
- **不做**额外的"查询改写"LLM 调用：多一次调用就多一份延迟与成本，而拼接已能覆盖大部分追问；
  以后若需要，`retrieval_query` 这一列就是它的落点（升级路径已留）。

### 4.4 两类"不该进上下文"的轮次，以及继承错误的抑制

| 情况               | 处置                                                                                                                             | 理由                                                                                 |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `status='failed'`  | **不进**上下文                                                                                                                   | 没有答案，带上去只有问题、没有回答，会诱导模型重复失败                               |
| `status='aborted'` | **不进**上下文（但仍在历史里显示）                                                                                               | 半截答案被当成事实继承，比不给上下文更糟                                             |
| 历史里的事实错误   | 系统提示词加一句硬指令：**"历史消息只用于理解指代与上下文，任何事实性结论必须以本轮提供的笔记片段为准；两者冲突时以片段为准。"** | 多轮最大的风险是"上一轮说错了，这一轮继续错"；把优先级写进 system 是最便宜的抑制手段 |
| 会话被删           | 后续追问带旧 `session_id` → `404 QA_SESSION_NOT_FOUND`，前端清掉 `?s=` 并提示"会话已被删除，已开始新对话"                        | 不静默复用，避免出现"删了却还在写"的错觉                                             |

### 4.5 落库时机与三个分支

**服务端在流收尾时写**（不由前端二次上传：多一次往返、关页面就丢、且可伪造）。

| 分支                          | 落库内容                                                                                             |
| ----------------------------- | ---------------------------------------------------------------------------------------------------- |
| 正常结束                      | `status=ok` + 完整答案 + `sources` + `retrieval_query` + `context_turns`                             |
| LLM 报错                      | 先发 `error` 帧，再写 `status=failed` + `error_code`                                                 |
| 客户端断开 / `CancelledError` | `status=aborted` + 已累积文本。**必须放在 `finally` 里** —— 用户点「停止生成」的那一轮恰恰最需要回看 |

整段包 `try/except`，失败只 `logger.warning`（与 `pending_embedding_chunks` 那条"附加功能不该拖垮主流程"同一原则），
并使用**独立 DB session**、且在任何 token 产出**之后**才写，避免影响首字节时间。

---

## 5. 前端改动

### 5.1 API 层

`frontend/src/api/qa.ts` 新增 `getQaSessions` / `getQaSession` / `deleteQaSession`；
`askQuestionStream` 的请求体加可选 `session_id`，返回值/回调里带出 `meta` 的 `session_id` 与 `history_used`。
类型用 `QueryOf<>` / `BodyOf<>` 派生，随后 `npm run gen:api` 再生 `schema.ts`。

### 5.2 `QA.tsx`

- 挂载拉 `['qa','sessions']`（TanStack Query，沿用现有 `staleTime`）；
- `PageHeader` 右侧加两个动作：**「新建对话」**（清空 `?s=` 与当前 `history`）与**「历史」**（抽屉列会话）；
- 抽屉点一条 → 载入该会话的轮次进 `history`，并把 `?s=` 设成它 ⇒ **可以直接接着追问**；
- 会话 id 进 URL（`hooks/useUrlState` 的 `s` 参数），发链接/返回本页都回到同一会话；
- 流结束 `invalidateQueries(['qa','sessions'])`；
- 顶栏显示一行轻量状态：`本轮参考了 N 轮上文`（`history_used > 0` 时才显示），让"追问是否生效"可见；
- 空态区分：从未问过（现有 `qa.empty`）与历史为空（新 key）。

### 5.3 笔记浮层（`NoteAskPanel`）

本轮**只落库、不接多轮、不做历史 UI**（`surface=note`，`session_id` 恒不传）。
理由：浮层是"读这段时临时问一句"，把它变成会话编辑会改变这个功能的语义；要支持时再单独一轮
（数据结构已能容纳）。

### 5.4 i18n

新增 `qa.history` / `qa.historyEmpty` / `qa.historyUntitled` / `qa.newChat` /
`qa.deleteSession` / `qa.deleteConfirm` / `qa.contextUsed` / `qa.sessionGone` 等键，中英各一份。

---

## 6. 迁移、保留与清理

### 6.1 建表不需要破坏性迁移

新表由 `init_db()` 的 `Base.metadata.create_all` 自动创建（`database.py:288`）；
`_migrate_sqlite` 的 `ALTER TABLE ... ADD COLUMN` 是给既有表补列用的，本次用不到 ⇒ 无 `ENGRAMNOTE_ALLOW_DESTRUCTIVE_MIGRATION` 门槛。

### 6.2 保留策略（照抄 `cleanup_llm_ledger` 先例）

- `config.py` 加 `qa_history_retention_days: int = 90`；
- 新增 Beat 任务 `cleanup_qa_history`（`celery_app.py` 的 `beat_schedule`，排在 `cleanup_llm_ledger` 之后）；
- 软删 30 天以上硬删（级联删轮次）；超 90 天的会话按最老优先删到上限内。

### 6.3 与笔记删除的联动

| 事件         | 对历史的影响                                                               |
| ------------ | -------------------------------------------------------------------------- |
| 笔记进回收站 | **不动**（用户可能还要看）                                                 |
| 笔记被 purge | 只清 `sources_json` 里的 `snippet`（保留 `chunk_id` 作墓碑）；问答正文保留 |
| 删除会话     | 软删 → 30 天后硬删                                                         |

---

## 7. 隐私与安全口径

1. **不做跨用户共享缓存**：`llm_cache` 的 key 只按 prompt 算、不含 `user_id`。哪天有人把问答接进缓存，
   就会出现"A 的答案给了 B"——这条写进服务层注释，作为硬约束。
2. **上下文只来自服务端**：客户端无法注入伪造的历史消息（§4.1）。
3. **日志默认不落正文**：提示词只在 `logger.debug` 且被截断（`services/llm/gateway.py:350-354`），默认 `INFO`。
4. **鉴权与归属**：列表/详情/删除都按 `user_id` 过滤；别人的会话 → `404`。
5. **可删除**：会话级软删 + 硬删；MVP 不做"删除单轮"。

---

## 8. 测试方法（分层，先写断言再写实现）

### 8.1 多轮组装（`backend/tests/test_qa_context.py`，约 8 条）—— 本次新增的重点

`messages` 顺序为 `system → (Q,A)×N → 当前问`；`N` 不超过 `QA_CONTEXT_MAX_TURNS`；
超预算时从**最旧**开始丢；单条答案按 `QA_CONTEXT_ANSWER_CHARS` 截断且带"…（略）"；
`failed` / `aborted` 轮次**不出现**在 `messages` 里；`status='ok'` 才参与；
`context_turns` 与实际带上的一致；系统提示词含"以本轮片段为准"那条硬指令（防回归删掉）；
空会话（首轮）等价于现状（只有 `system + 当前问`）。

### 8.2 检索查询拼接（`test_qa_retrieval_query.py`，约 4 条）

首轮 `retrieval_query == question`；追问时等于 `上一问 + 当前问`；
拼接后检索为空时退回只用当前问再检索一次（用桩断言调用次数）；
两次都空时 `sources=[]` 且答案里如实说明"没有找到相关片段"。

### 8.3 服务层（`test_qa_history_service.py`，约 12 条）

创建会话 → 追加轮次 → `updated_at` / `turn_count` / `last_question` 同步；
列表按 `updated_at` 倒序；`keyword` 能搜问题与答案；`surface` / `note_id` 过滤；
软删后不在列表但行还在；硬删级联；`retry` 同一 `session_id` 不新建会话；
消失的会话再追问 → `404`；`sources_json` 往返一致；笔记 purge 后片段清空；写入异常被吞。

### 8.4 端点（`test_qa_history_api.py`，约 8 条）

401；别人的会话 404；分页边界（`page=0` / 超大 `page_size`）；`DELETE` 两次第二次 404；
空列表 `{items: [], total: 0}`；`surface` 非法 → 422。

### 8.5 SSE 落库与多轮（`test_ask_stream_persists_turn.py`，约 6 条）

用假 LLM 生成器驱动端点：流结束后出现一轮 `ok`；LLM 抛错 → `failed` + `error_code`；
客户端取消 → `aborted` + 部分文本；`meta` 帧带回 `session_id` 与 `history_used`；
**带同一 `session_id` 的第二轮 `messages` 里出现第一轮**；
第二轮请求带一个**已软删**的 `session_id` → `404`。

### 8.6 契约

`python scripts/dump_openapi.py --check` 一致；新端点都有 `response_model`；
`DELETE .../sessions/{id}` 进 `NON_JSON_2XX` 白名单（该表**穷举**，必须写理由）；
两个 `ask/stream` 请求体新增的是**可选**字段（`test_openapi_response_contract.py` 会看 JSON schema）。

### 8.7 前端单测（`src/pages/QA.test.tsx` +6）

拉列表并渲染「历史」按钮；抽屉显示标题/时间/轮次；点一条 → 渲染该会话轮次并写 `?s=`；
「新建对话」→ 清空 `?s=` 与 `history`；删除会话 → 调 API 并从列表消失；
`history_used > 0` 时显示"本轮参考了 N 轮上文"。

### 8.8 前端 API 层（`src/api/qa.test.ts` +3）

分页/`keyword` 编码；`deleteQaSession` 走 `DELETE`、204 返回 `void`；错误码透传。

### 8.9 e2e / a11y（`frontend/e2e/qa-history.spec.ts`，2 条）

用 `e2e/a11y-fixtures.ts` 打桩 `GET /api/qa/sessions` 与详情：真浏览器点「历史」→ 抽屉开、
`Esc` 关、焦点回触发按钮；axe 扫抽屉打开态无新增违规；会话项可 Tab 到（`expectReachableByTab`）。

### 8.10 回归门禁（当前基线）

后端：`ruff` 0、`pytest -q`（1847 passed）、`dump_openapi --check`、`gen_env_example --check`。
前端：`tsc` 0、`eslint` 0、`vitest` 796、`build` 0、`e2e` 28、`a11y` 26、
`prettier --check --end-of-line auto`、`docs:links` 0 死链。

---

## 9. 提交切分与顺序

| #   | 提交                                         | 内容                                                                         | 可独立回滚 |
| --- | -------------------------------------------- | ---------------------------------------------------------------------------- | ---------- |
| 1   | `feat(qa): 问答历史与多轮的数据模型`         | 两张表 + `config` 三个上下文参数 + 保留期 + 清理任务 + 模型级测试            | ✅         |
| 2   | `feat(qa): 多轮上下文组装与检索查询拼接`     | `qa_context.py`（组装 + 预算裁剪）+ `retrieval_query` 拼接 + §8.1/8.2 测试   | ✅         |
| 3   | `feat(qa): 问答历史的服务层与三个端点`       | `qa_history_service.py` + `api/qa.py` + schemas + 契约再生 + §8.3/8.4 测试   | ✅         |
| 4   | `feat(qa): 流式问答接入落库与多轮`           | 两个 `ask/stream` 接 `session_id` 与 `record_turn`，`meta` 加字段，§8.5 测试 | ✅         |
| 5   | `feat(qa): 问答页的会话抽屉、新建对话与 URL` | 前端 API 层 + `QA.tsx` + i18n + §8.7/8.8 单测 + §8.9 e2e                     | ✅         |
| 6   | `docs(qa): 定稿与台账`                       | 本文件定稿 + `docs/README.md` 状态横幅 + `agent-workstream-progress.md` 一行 | ✅         |

依赖：1 → 2 → 3 → 4 顺序固定；5 依赖 3（可与 4 并行）；6 最后。

---

## 10. 待确认的两个决策

| #   | 问题                            | 我的建议                                          | 影响                                                      |
| --- | ------------------------------- | ------------------------------------------------- | --------------------------------------------------------- |
| 1   | 失败 / 中止的轮次**入历史**吗？ | **入**（`status` 标记），但**不进上下文**（§4.4） | 决定 §8.3 与 §8.5 的分支用例                              |
| 2   | 笔记浮层要不要也支持追问？      | 本轮**不做**（只落库）                            | 若要做：`NoteAskPanel` 加会话状态 + 一处 UI；工时 +0.3 天 |

（原"形态"一项已按你的意见定为 C 档可追问，§1.3。）

---

## 11. 风险与回滚

| 风险                           | 处置                                                                                                        |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| 多轮把 token 成本抬上去        | 三条预算（6 轮 / 400 字 / 4000 字）都在 `config.py`，可按实测调；`context_turns` 落库后可统计"真实带了几轮" |
| 追问时检索词拼接后**反而更差** | 已定"拼接失败就用当前问再检一次"；`retrieval_query` 落库，出问题能直接归因                                  |
| 历史里的错误被继承             | 系统提示词硬指令 + 失败/中止轮次不进上下文（§4.4）                                                          |
| SSE 生成器里写库拖慢首字节     | 写在流结束之后、独立 DB session；§8.5 有断言                                                                |
| 落库失败影响回答               | 全包 `try/except` + `warning`，异常绝不穿出生成器                                                           |
| 历史表膨胀                     | 保留期 90 天 + Beat 清理（第 1 个提交就带上）                                                               |
| 隐私                           | 上下文只来自服务端；会话可删；笔记 purge 清片段；不做跨用户缓存                                             |
| 前端抽屉改变既有布局           | 抽屉默认**关闭**；未点「历史」时 `/qa` 与现状逐像素一致                                                     |

**开关**：`config.py` 加 `qa_history_enabled: bool = True` 与 `qa_multiturn_enabled: bool = True`。
前者 `False` ⇒ 不写库、不显示历史；后者 `False` ⇒ 退回单问单答（历史照存）。两个开关都能单独回滚。

---

## 12. 工时估算

| 环节                                                    | 工时         |
| ------------------------------------------------------- | ------------ |
| 数据层（模型 + 3 个配置 + 清理任务 + 测试）             | 0.3 天       |
| 多轮组装 + 检索拼接 + 测试（本次新增的核心）            | 0.5 天       |
| 服务层 + 三个端点 + 契约再生                            | 0.4 天       |
| 流式接入（`session_id` / `record_turn` / `meta`）+ 测试 | 0.4 天       |
| 前端（API 层 + 抽屉 + 新建对话 + URL + i18n + 单测）    | 0.5 天       |
| e2e / a11y / 文档 / 全量门禁                            | 0.4 天       |
| **合计**                                                | **≈ 2.5 天** |
