# AI 问答历史持久化 + 可追问（QA History & Multi-turn）

> **状态：已落地（2026-11-13，六个提交 `dc6e0eb`…`0c5e366`，全部门禁通过）** ｜ 起草：2026-11-13 ｜
> §10 两个决策已按建议执行（失败/中止轮次入历史但不进上下文；笔记浮层只落库）｜
> 权威性：**本主题（问答历史 / 会话与多轮上下文）的设计依据**；
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

### 5.4 面向用户的三句提示文案（**本次新增要求，必须有**）

多轮问答的三个"反直觉点"里，每一个都会在没有任何提示的情况下被当成 bug 报回来。
所以下面三句话不是装饰，而是**验收项**（§8.7 有对应用例盯它们不被删掉）。

| #   | 文案（中文原文）                                               | 渲染位置                                                                                                         | 挡住的是哪一种困惑                                                |
| --- | -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| 1   | **追问会带上最近 6 轮对话；换话题请点「新建对话」。**          | `/qa` 输入框下方的小字（`styles.qaHint`，`aria-describedby` 指向输入框）；`N`/`6` 由 `QA_CONTEXT_MAX_TURNS` 注入 | "AI 怎么不记得我刚说的？"——其实是超了预算、或换了话题还指望它接上 |
| 2   | **问答历史保留 90 天；删除后仍可能留在两周内的数据库备份里。** | 历史抽屉顶部一行（`styles.qaDrawerNote`）                                                                        | "我删了为什么备份里还有？"——`backup_keep = 14` 的必然结果（§6.3） |
| 3   | **此处提问不带入问答页的上下文。**                             | 笔记浮层 `NoteAskPanel` 的输入区下方（`styles.noteAskHint`）                                                     | "为什么这里问的和问答页说的不一样？"——浮层是单问单答（§5.3）      |

三个约束：

1. **数字从配置来，不写死在文案里**：第 1 句的 `6` 取 `QA_CONTEXT_MAX_TURNS`、第 2 句的 `90` 取
   `qa_history_retention_days`、`两周` 取 `backup_keep = 14`。写死就会在某天调参后变成谎言
   （这类"文案与配置漂移"在本仓库已有先例，见 `docs/README.md` 对过期陈述的处置口径）。
2. **不用 tooltip 藏起来**：三条都是常驻可见的小字（`color: var(--color-text-tertiary)`、
   `font-size: var(--text-xs)`），不依赖 hover —— 触屏与键盘用户看不到 hover。
3. **不阻断操作**：只是说明，不加确认框、不打断输入焦点。

### 5.5 i18n

新增以下键（中英各一份，`messages.zh-CN.ts` / `messages.en.ts`）：

| 键                                      | 中文                                                                    | 用途                          |
| --------------------------------------- | ----------------------------------------------------------------------- | ----------------------------- |
| `qa.newChat`                            | 新建对话                                                                | 页头动作                      |
| `qa.history`                            | 历史                                                                    | 页头动作 / 抽屉标题           |
| `qa.historyEmpty`                       | 还没有历史对话                                                          | 抽屉空态（与"从未问过"区分）  |
| `qa.historyUntitled`                    | 未命名对话                                                              | `title` 为 `NULL` 时的兜底    |
| `qa.deleteSession` / `qa.deleteConfirm` | 删除对话 / 删除后不可恢复，确定删除？                                   | 抽屉内删除                    |
| `qa.contextUsed`                        | 本轮参考了 {count} 轮上文                                               | `history_used > 0` 时的状态行 |
| `qa.sessionGone`                        | 会话已被删除，已开始新对话                                              | 追问到已删会话时的提示        |
| **`qa.multiturnHint`**                  | **追问会带上最近 {turns} 轮对话；换话题请点「新建对话」。**             | §5.4 第 1 句                  |
| **`qa.retentionHint`**                  | **问答历史保留 {days} 天；删除后仍可能留在{weeks}周内的数据库备份里。** | §5.4 第 2 句                  |
| **`qa.noteAskNoContext`**               | **此处提问不带入问答页的上下文。**                                      | §5.4 第 3 句                  |

⚠️ 第 2 句里的"周"用配置算（`backup_keep / 7` 取整），英文版写 `{weeks} weeks`；
占位符统一走现有 `t(key, {…})` 的插值约定，不要在模板里手拼字符串。

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

### 8.7 前端单测（`src/pages/QA.test.tsx` +8）

拉列表并渲染「历史」按钮；抽屉显示标题/时间/轮次；点一条 → 渲染该会话轮次并写 `?s=`；
「新建对话」→ 清空 `?s=` 与 `history`；删除会话 → 调 API 并从列表消失；
`history_used > 0` 时显示"本轮参考了 N 轮上文"。

**三条提示文案各一条（对应 §5.4）**：

| 用例             | 断言                                                                                                                                |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| 追问提示常驻可见 | 输入框下方能查到 `qa.multiturnHint` 渲染出的文本，且 `6` 来自配置（把 `QA_CONTEXT_MAX_TURNS` 换成 3，文案跟着变 3 —— 防"文案写死"） |
| 抽屉保留期提示   | 打开抽屉即可见 `qa.retentionHint`，且 `90` / `两周` 随配置变化                                                                      |
| 浮层无上下文提示 | `NoteAskPanel` 渲染后输入区下方有 `qa.noteAskNoContext`                                                                             |

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

| #   | 提交                                         | 内容                                                                               | 可独立回滚 |
| --- | -------------------------------------------- | ---------------------------------------------------------------------------------- | ---------- |
| 1   | `feat(qa): 问答历史与多轮的数据模型`         | 两张表 + `config` 三个上下文参数 + 保留期 + 清理任务 + 模型级测试                  | ✅         |
| 2   | `feat(qa): 多轮上下文组装与检索查询拼接`     | `qa_context.py`（组装 + 预算裁剪）+ `retrieval_query` 拼接 + §8.1/8.2 测试         | ✅         |
| 3   | `feat(qa): 问答历史的服务层与三个端点`       | `qa_history_service.py` + `api/qa.py` + schemas + 契约再生 + §8.3/8.4 测试         | ✅         |
| 4   | `feat(qa): 流式问答接入落库与多轮`           | 两个 `ask/stream` 接 `session_id` 与 `record_turn`，`meta` 加字段，§8.5 测试       | ✅         |
| 5   | `feat(qa): 问答页的会话抽屉、新建对话与 URL` | 前端 API 层 + `QA.tsx` + **§5.4 的三句提示文案** + i18n + §8.7/8.8 单测 + §8.9 e2e | ✅         |
| 6   | `docs(qa): 定稿与台账`                       | 本文件定稿 + `docs/README.md` 状态横幅 + `agent-workstream-progress.md` 一行       | ✅         |

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

| 环节                                                                    | 工时         |
| ----------------------------------------------------------------------- | ------------ |
| 数据层（模型 + 3 个配置 + 清理任务 + 测试）                             | 0.3 天       |
| 多轮组装 + 检索拼接 + 测试（本次新增的核心）                            | 0.5 天       |
| 服务层 + 三个端点 + 契约再生                                            | 0.4 天       |
| 流式接入（`session_id` / `record_turn` / `meta`）+ 测试                 | 0.4 天       |
| 前端（API 层 + 抽屉 + 新建对话 + URL + **三句提示文案** + i18n + 单测） | 0.6 天       |
| e2e / a11y / 文档 / 全量门禁                                            | 0.4 天       |
| **合计**                                                                | **≈ 2.6 天** |

---

## 13. 落地记录（2026-11-13，实现与方案的差异）

六个提交：`dc6e0eb`（表+配置+清理）→ `5f59249`（上下文组装）→ `c038d7a`（四个端点）
→ `cbaf758`（流式接入）→ `0c5e366`（前端）→ 本提交（文档）。

**实现时偏离方案的三处，都是实测逼出来的：**

| #   | 方案原写                            | 实际做法                                                    | 为什么                                                                                                                             |
| --- | ----------------------------------- | ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| 1   | "会话已删 → 在 SSE 里发 `error` 帧" | **流开始前就回真 HTTP 404**                                 | 流一开始状态码就发出去了，客户端要再解析一次帧才知道是"会话没了"；放在端点体外则前端 `ApiError.code` 直接可用                      |
| 2   | 落库复用请求作用域的 `db`           | **每次落库用独立 DB session**                               | 流式生成器可能比 `Depends(get_db)` 活得更久（慢读、断连收尾）；独立 session 同时也是 `CancelledError` 里能安全 `shield` 写库的前提 |
| 3   | `sources` 的片段键叫 `snippet`      | 实际是 **`relevant_text`**（只截 `snippet` 等于什么都没截） | 检索层字段名如右；`record_turn` 与 `strip_snippets_for_note` 两处都按两个键处理                                                    |

**另外补进来的两件事（原方案没有）：**

- **「清空全部问答历史」**（`DELETE /api/qa/sessions` 返回 `{deleted}`）：§5 曾把它登记为未做项，按确认补上；
- **`tests/test_qa_history_copy_matches_config.py`**：直接读前端 `constants/qaHistory.ts`
  与 `Settings` 默认值比对 —— 三句提示里的 6/90/2 是**镜像常量**，没有这条用例就会在
  调参后变成"界面上的旧信息"。

**实测踩到并修掉的三个坑（写在这里给下一个人）：**

1. `useToast()` 在没有 Provider 时**每次渲染返回新对象**，把 `toast` 放进 effect 依赖会形成
   "取详情 → setState → 再取详情"的死循环（vitest 挂了 10 分钟才发现）；
2. QA 输入框只靠 `placeholder` + `aria-describedby` 时，axe 报
   `label-title-only [serious]`（a11y 门禁当场变红）—— 补 `aria-label` 后 0 违规；
3. `GET /api/qa/sessions` 让"问答页挂载时不发任何请求"这条 a11y 注释失效：不补桩的话
   场景会撞"未定义 501"，页面照常渲染而审计**静默漏掉**新加的页头与提示。

**已知未做（承接 §1.2）：** 不能删除单轮、不能导出问答、笔记浮层不支持追问（只有落库）、
跨会话的问答不支持语义搜索（`keyword` 只做 `LIKE`）。
---

## 14. 独立审计（2026-11-13）与整改记录

改完之后另开了一个**全新上下文**的子代理做只读审计：只告诉它仓库路径与审计口径，
不说本次改了什么、改在哪、怎么设计；范围由它自己用 `git log`/`git diff` 圈定。
报告落在 `_agenttmp/audit-report.md`（不入库），结论 **blocker 0 / major 5 / minor 8 / nit 4**，
并明确写了"未发现越权（四个端点全按 `user_id` 过滤）与崩溃级缺陷"。

### 14.1 五条 major 与整改

| #   | 问题（审计原文要点）                                                                                                                        | 整改                                                                                                                                                                                                                                                |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M1  | `surface` 维度做了没接上：前端列表不传 `surface`，笔记浮层会话混进 `/qa` 抽屉；续聊也不校验 surface，能在问答页把 global 轮次写进 note 会话 | 前端 `getQaSessions({ surface: 'global' })`；`get_or_create_session` 新增 `surface` 参数做**校验**（不匹配 → 404），两个端点各自显式传 `SURFACE_GLOBAL` / `SURFACE_NOTE`；落库路径也显式传（默认值会把 note 会话过滤掉 → 静默不落库，实测踩过一次） |
| M2  | 删除/清空全部**点了就执行**，`deleteConfirm` / `clearAllConfirm` 是零引用死键                                                               | 抽屉改用 `ConfirmDialog` 二次确认（两个确认框渲染在 `Dialog` 外，避免陷阱套陷阱），文案用原本那两个死键                                                                                                                                             |
| M3  | 追问到已删会话时不清 `?s=`，页面进入永久 404 错误态                                                                                         | `handleAsk` 的 catch 增加 `isSessionGoneError` 分支：清 `?s=`、清轮次、提示「对话已被删除」，与详情加载失败走同一条恢复路径                                                                                                                         |
| M4  | `status`/`error_code` 被前端丢掉 → 失败轮次永远显示「AI 正在思考...」                                                                       | `QARecord` 增加两个字段并在映射时保留；渲染改三态（答案 / `turnFailed({code})` / `turnAborted`），带独立样式                                                                                                                                        |
| M5  | §6.3「笔记 purge 后清片段」**没有接线**：`strip_snippets_for_note` 生产零调用，单测直接调函数造成"已实现"的假象                             | `purge_note` 第 5.4 步接线；新增 `test_qa_history_purge_integration.py` **从 `purge_note` 入口**验证（片段清空 / `chunk_id` 墓碑保留 / 问答正文不受影响 / 其他笔记不误伤）                                                                          |

### 14.2 一并修掉的 minor

| #        | 问题                                                                                                         | 整改                                                                                                        |
| -------- | ------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------- |
| m1       | `context_turns` / `history_used` 记的是"想带几轮"                                                            | 改用 `context_turns_used()` 的真实值；新增用例构造"必然超预算"的历史，断言落库值 = meta 值 < `len(history)` |
| m2       | 检索回退后 `retrieval_query` 仍记拼接词                                                                      | 回退命中时改写为当前问（`nonlocal`）；补用例断言两次调用与落库值                                            |
| m3       | 空问题 / 未授权笔记会留下永久空会话                                                                          | 空问题校验与 `get_note_detail` 全部前移到建会话之前；补用例断言"空问题 → 422 且库里没有会话"                |
| m4       | 抽屉删除失败被吞，且非 2xx 会**重复发一次 DELETE**                                                           | `catch` → toast；`deleteQaSession` 改为只读一次响应（导出 `readErrorBody` 复用错误本地化）                  |
| m5       | 「清空全部」的 `{deleted}` 被丢弃                                                                            | `toast.info(qa.clearAllDone, { count })`                                                                    |
| m6       | 抽屉只拉 50 条且静默截断                                                                                     | 拉到后端上限 100；`total > shown` 时显示「共 N 个，这里显示最近 M 个」                                      |
| m7       | 流式端点在整个 SSE 期间持有请求作用域的 DB 会话                                                              | 读完上下文后立即 `await db.commit()`（只读事务提交无副作用）                                                |
| n1/n3/n4 | 函数体内 `import json`；`record_turn` 失败不 rollback（第二条 warning 淹掉真因）；`surface` 校验用硬编码正则 | 上提 import；catch 里 `rollback`；正则由 `SURFACES` 生成                                                    |

### 14.3 审计没提、但整改时暴露的两个真 bug

1. **`surface` 校验把落库路径也挡住了**：`_persist_completed_turn` 复核会话时用默认 `surface='global'`，
   笔记浮层那一轮因此**静默不落库**（`test_note_ask_creates_note_session_and_stores_turn` 当场变红）。
2. **"清完又冒出旧答案"**：`setSessionId('')` 生效前还会渲染一次旧 id，而载入 effect 的依赖里
   含 `setSessionId`（身份不保证稳定）→ 它把刚清掉的轮次重新拉了回来。修法是记一个
   `goneSessionsRef`（已判定删除的 id 集合），此后任何渲染周期都不再取它。

### 14.4 过程事故（诚实记录）

批量跑 `ruff format app tests` 时它重排了 **201 个无关文件**；我随后用一个按
`git status` 全量枚举的还原脚本回滚，脚本的 keep 列表只写了后端路径，
于是**把尚未提交的前端改动一起 checkout 掉了**。前端四个文件按审计结论重做了一遍
（这次用整文件写入，不再做宽路径还原）。教训：还原脚本必须用**白名单**枚举要还原的文件，
而不是"枚举全部再减掉要保留的"。

### 14.5 门禁（整改后）

后端 **1902 passed / 30 skipped**、ruff 0、`dump_openapi --check` 与 `gen_env_example --check` 一致；
前端 **819 passed（68 文件）**、tsc 0、eslint 0、build 0、e2e **30**、a11y **26（0 违规）**、
prettier / `docs:links` 通过。
---

## 15. 第二轮独立审计（2026-11-14）与整改

又开了一个**全新上下文**的子代理，这次只告诉它"审计范围 = 问答功能的那批文件"，
不说改动来历、不说上一轮审计。报告 `_agenttmp/audit-report-2.md`（669 行），
结论 **blocker 0 / major 1 / minor 15 / nit 5**，其中 5 条用自建探针实测过
（浮层 `session_id`、`LIKE` 通配符、`0` 值配置、连接归还、`turn_count` 并发写）。

### 15.1 major 与它连带的那条数据丢失

| 编号    | 问题                                                                                                                                           | 整改                                                                                                                                                               |
| ------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| MAJOR-1 | 删/清空**当前**会话后不清 `?s=`：页面留着幽灵对话；用户接着提问时 `handleAsk` 先把输入框清空，再带旧 `session_id` 撞 404，**刚敲的问题被吞掉** | 抽屉新增 `onSessionsRemoved(sessionId \| 'all')`，`QA.tsx` 命中当前会话就 `handleNewChat()`；404 分支把 `currentQuestion` 放回输入框（用户打过的字不能被静默丢弃） |

### 15.2 十五个 minor 的处置

| 编号     | 处置                                                                                                                                                                                                            |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| MINOR-1  | 浮层端点对非法/别人的/跨 note 的 `session_id` 改为 **404**（不再静默新建）；`get_or_create_session` 增 `expect_note_id`，不匹配按取不到处理                                                                     |
| MINOR-2  | `qa_context_*` 不再用 `x or 默认值`（`0` 被吞成 6/400/4000）；三处 0 的语义写明：不带历史 / 不截断 / 不限总量（`assemble_messages` 对预算做 `>0` 判断）                                                         |
| MINOR-3  | `keyword` 转义 `\ % _` 并 `ilike(..., escape="\\")`；纯空白等价于不过滤；`max_length=100`                                                                                                                       |
| MINOR-4  | 新增 `QA_SESSION_SURFACE_MISMATCH`（仍 404）："属于另一个入口"不再被说成"已被删除"（后端用新增的 `get_session_by_id` 区分两种原因）                                                                             |
| MINOR-5  | `error_code` 改 `classify_exception()`（`LLM_TIMEOUT` / `DB_UNAVAILABLE` / `LLM_UNKNOWN` / `AppError.code`）；SSE `error` 帧只发稳定码 + 固定中文文案，**不再下发 `str(e)`**（原来会把 SQL 与绑定参数送到界面） |
| MINOR-6  | 详情接口加 `turns_limit`（默认 200，取最近 N 轮再升序）；`GET`/`DELETE` 支持 `surface` 校验                                                                                                                     |
| MINOR-7  | 两个 purge 改成一条 Core `DELETE`；「清空全部」改成一条 `UPDATE`（不再全量加载 + `IN (...)`，避免 SQL 变量上限整批失败）                                                                                        |
| MINOR-8  | `retrieve_context` 增 `corpus_empty`；空语料时追问不再白跑第二遍检索                                                                                                                                            |
| MINOR-9  | 列表响应下发 `limits: {context_turns, retention_days}`（生效值）；前端优先用接口、缺省回退常量；`gen:api` 已再生，删掉临时的 `QASessionListWithLimits` shim                                                     |
| MINOR-10 | 首问收到 `meta.session_id` 后把 `loadedSessionRef` 指向它，载入 effect 不再拉详情覆盖本地轮次（否则落库失败会让刚读到的答案消失）                                                                               |
| MINOR-11 | `finally` 里 `setLoading/setStreaming` 进"仍是当前请求"的守卫；`stopActiveStream()` 自己负责收尾（否则点停止后按钮不回来）                                                                                      |
| MINOR-12 | 每行删除按钮的可访问名带标题（`删除对话：<title>`），读屏能分辨删的是哪条                                                                                                                                       |
| MINOR-13 | 四个输入字段加 `max_length`（`question` 4000 / `selected_text` 8000 / `context_*` 4000），前端 `maxLength={4000}`；浮层 prompt 去掉重复拼接的 `selected_text`                                                   |
| MINOR-14 | `deleteQaSession` 的 204/404 用例都补 `toHaveBeenCalledTimes(1)`（守"只发一次请求"，这是上一轮 m4 的原始缺陷）                                                                                                  |
| MINOR-15 | e2e 桩的 `meta` 补 `session_id`/`history_used`、列表桩改 `page_size=100` + `limits`，并把"挂载不发请求"的过时注释改成事实                                                                                       |

### 15.3 nit

`_persist_completed_turn` 两处已分叉（`surface` 一处显式一处隐式）——**本轮未合并**，
登记为待办；`type="button"` 已补；轮次数加单位（`qa.turnsCount`）；`t`/`tRef` 用法统一；
`SURFACE_NOTE` 字面量已换常量、`context_turns_used` 改为复用 `assemble_messages` 计数
（结构上不可能与真正送进去的轮数漂移）。

### 15.4 审计之外、整改时被测试抓出来的一个真 bug

`created_at` 是 `server_default=func.now()`，**SQLite 只有秒级精度** —— 同一秒内的多轮
`created_at` 完全相同，于是 `order_by(created_at, id)` 退化成按 UUID 排（等于随机）。
`turns_limit` 的用例当场变红（取到的"最近两轮"是老的两条）。
修法：`record_turn` 显式写 `created_at=_now()`（微秒精度）。这一列是"轮次顺序"的唯一依据
（`recent_ok_turns` 取最近 N 轮、详情取最近 N 轮都靠它），审计把它列为"未验证疑点 2"，
现在有断言钉住了。

### 15.5 顺带修掉的一处真实可读性问题

a11y 桩补上 `history_used: 1` 之后，「本轮参考了 N 轮上文」那一行首次进入审计视野：
`--color-accent`（#c9a959）在白底上对比度 **2.25:1**，12px 正文远低于 WCAG AA 的 4.5:1，
axe 报 `color-contrast [serious]`。改用 `--color-text-secondary`。

### 15.6 门禁

后端 **1909 passed / 30 skipped**、ruff 0、`dump_openapi --check` 与 `gen_env_example --check` 一致；
前端 **822 passed（68 文件）**、tsc 0、eslint 0、build 0、e2e 30、a11y 26（0 违规）。
