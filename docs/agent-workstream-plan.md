# EngramNote 长跑型改进方案集（Agent Workstreams）

> **状态：活文档（计划已拍板，未落地）** ｜ 最后核对：2026-10-21 ｜ 权威性：本主题的执行依据
>
> **这是计划，不是记录。** 本仓库的规矩是「没做的事写成没做」，所以先把话说清楚：
> **本文提出的 12 条工作流，一条都还没开工。** 文中的实测数字全部来自**真库只读探测**
> 与**已提交的代码**；引用其它文档时标注了该文档自己的状态（活文档 / 历史快照 / 过程记录），
> 按 `docs/README.md:22-26` 的裁决顺序：**代码实测 > 任何文档**。
>
> **§5 的 6 个待拍板项已于 2026-10-21 全部拍板**，逐条记录见 §5.1。现在这份文档可以当作
> 执行依据用了 —— 但 **§5.1 里的 🔴 项仍然只能在它自己那一轮里执行**（例如 D1 的删除动作）。
>
> **怎么用这份文档**：§2 是事实底账（先看它，别跳过 —— 里面有一条会改变你的判断），
> §3 是 12 条工作流，§4 是推荐执行顺序，§5 是**已拍板的 6 件事**，
> §6 是复现命令，§7 是明确不做的事。**要开工就直接看 §4 + §5.1。**

---

## 0. 这份文档解决什么问题

当前项目状态：**功能完整、CI 齐全、测试成体系**，但存在一批**已定案、已调研、却零执行**的改进项 ——
它们各自都有计划文档或实测报告撑着，缺的只是「有人连续跑一段时间把它做完」。

本文把这批工作收敛成 12 条工作流，每条都给出：**做什么 / 为什么是现在 / 具体落点（`文件:行号`）/
怎么验收 / 大概多久 / 风险**。目标是让它成为一轮批量执行的**唯一依据**。

**判断口径**（三条都满足才收进来）：

1. **能一次做完** —— 有明确终点，不是「持续优化」；
2. **有可执行的验收** —— 能写出命令或断言，不是「体验更好」；
3. **失败可回滚** —— 要么不写数据，要么有快照，要么在副本上跑。

---

## 1. 前置约束（动手前必须知道的三条）

| # | 约束 | 实测依据 | 对本方案的影响 |
|---|---|---|---|
| C-1 | **LLM 全站限流 10 RPM + 并发 3** | `backend/app/config.py:304`（`llm_max_rpm: int = 10`）；`backend/app/services/llm/gateway.py:90`（`MAX_CONCURRENCY = 3`） | 任何全库 LLM 重跑都是**分钟级**（实测一轮全量理解 ≈ 35 min）。**不能靠并发压时间** |
| C-2 | **SQLite 单写者** | `docs/sqlite-single-writer.md`（活文档）；`README.md:340-346`（「关于容器化」一节说明曾因资源约束放弃 PG/Redis） | **禁止两个 LLM 任务同时写同一个库**。需要并行时只能在**副本**上并行 |
| C-3 | **LLM 响应缓存默认开，TTL 30 天** | `backend/app/config.py:434-436`；缓存键 = `sha256(provider + base_url + model + messages + temperature + max_tokens + response_format)`，见 `backend/app/services/llm_cache_service.py:6-7,96-101` | **重复跑同样的输入 = 命中缓存 = 不花钱也不产生新结果。** 想拿到新产出，必须至少改变一个键成分：**换模型**、**改提示词**、或**关缓存** |

> **C-3 的推论（很重要）**：把 `prompts.py` 的题型/难度配比修好，本身就会让缓存键变化 ——
> 所以「修提示词」与「重跑」是**同一件事的两半**，修完不重跑等于没修，重跑但不修等于白花钱。

---

## 2. 事实底账（实测，2026-10-21）

> 探测方式：SQLite **只读**打开 `backend/data/db/engramnote.db`（`mode=ro`），不写任何数据。
> 复现脚本见 §6.1。

### 2.1 数据规模

| 项 | 实测值 | 备注 |
|---|---|---|
| 笔记 | **28** 篇 | 26 `archived` + 2 `cleaned`；`note_role` 含 material 与 personal_note |
| chunks | **818** 行 / 378,284 字符 | 其中 **210 行无向量**（`embedding_model IS NULL`） |
| 知识卡片 | **3469** 张 | concept 2884 / definition 325 / qa 214 / formula 46 |
| 卡片向量 | **0 张缺** | 3469 张全部有向量 |
| 卡片 `prompt_version` | `'1'` **2300** 张 / `NULL` **1169** 张 | NULL = 提示词未登记时期产出（`prompts.prompt_version()` 的约定） |
| 题目 | **3358** 道 | **全部 `choice`**；difficulty：easy 2567 / medium 773 / hard 18 |
| 图谱关系 | **604** 条 | **全部** `confirmed` + `related`；`suggested` 为 0 |
| LLM 历史账本 | **200** 行 / 2,958,262 token | `llm_calls`；集中在 2026-10-19（152 行）与 10-20（47 行） |
| LLM 响应缓存 | **197** 行 | `llm_cache` |
| 笔记版本 | 19 | `note_versions` |

### 2.2 ★ 一条会改变判断的事实：走查报告的 P0 已经修掉两条

`docs/ux-walkthrough-report-20261021.md`（**过程记录**，不是现状依据）里 §4 严重度总表列了 2 个 🔴。
**实测复核：两个都已在本轮修复，不能在本文里当成待办。**

| 走查报告里的 🔴 | 现状 | 证据 |
|---|---|---|
| 「日报日期永远是昨天；7 天趋势最后一根恒 0」 | ✅ **已修** | 提交 `e615715`「fix(report): 业务日日期串差一天 + copy_stats.total_blocks 恒为 0」；代码 `backend/app/services/report_service.py:64`（`today_str = business_date_str(now)`）、`:133`（`get_weekly_trend`） |
| 「答案重复 66 次，整段不可读」 | ✅ **已修** | `frontend/src/hooks/useStreamAnswer.ts:17-53`（`useThrottledStream` 节流累积 + 结束时强制 `flush`）、`:137`（流自然结束也 flush）、`:144`（异常路径也 flush） |

**仍然成立的（实测复核过，未修）**：

| 走查报告里的 🟠 | 现状证据 |
|---|---|
| 卡片页声称 3469 张只渲染 999 张 | `frontend/src/pages/KnowledgeCards.tsx:109` —— `getKnowledgeCards(1, 999, …)` **硬编码 999，无分页** |
| `daily-plan` 无目标返回 400 + 前端靠匹配英文文案 | 后端 `backend/app/api/goals.py:123-148`（`GOAL_NONE_ACTIVE → 400` 仍然成立）；前端 `frontend/src/pages/Dashboard.tsx:104-111`（`msg.includes('active goals')`） |

> **教训（写进文档免得重犯）**：过程记录类文档的结论**有保质期**。本文所有「未修」判断
> 都以**当日代码实测**为准，引用走查报告的地方都重新核过一遍。

### 2.3 出题题型/难度单调 —— 根因已定位到行

走查报告 §2.3 的 🟠「9 道题全是简单选择题」，根因是**提示词把题型与难度写死**：

| 位置 | 内容 |
|---|---|
| `backend/app/services/llm/prompts.py:196-216` | `question_session` 提示词：`:199` 写「generate **multiple-choice** questions for each group」；`:202` 的 JSON 样例硬编码 `"question_type": "choice", "difficulty": "easy"`；`:209` 写「Generate 1 **multiple-choice** question per knowledge point」 |
| `backend/app/services/llm/prompts.py:175-194` | `generate_questions_batch` 同样的毛病（`:181`），**但它是死代码** —— `scenes.py:193` 的 `generate_questions_batch()` 在 `backend/app/` 下**零调用方**（实测 grep） |
| `backend/app/services/llm/prompts.py:524` / `:552` | **英文提示词**（`_EN_PROMPTS`）里同样硬编码，修的时候**两处都要改** |
| `backend/app/tasks/understand_tasks.py:324` | `response = await question_session.ask(cards_text)` —— **没传任何题型/难度参数** |

**已经具备但没被用上的能力**：`backend/app/api/understanding.py:729-730` 的
`target_categories` / `target_difficulty` 查询参数**存在且已接线**（`:750` 传给 Celery 任务），
但 UI 没有入口 —— 这正是 `docs/overhaul-plan.md:11210` 登记的「契约有、UI 没传」三条之一。

### 2.4 图谱关系：瓶颈是吞吐，不是质量

`docs/relation-method-research-20261021.md`（**过程记录**）与 `docs/graph-relation-methods.md`
（**活文档**）已经把病因定死。以下数字来自那两份文档，**引用而非重测**：

| 事实 | 数值 | 出处 |
|---|---|---|
| 真库孤点 | **3028 / 3469（87.3%）** | research §0 F1 |
| 其中最近邻**已过现行阈值** | **2683（88.6%）** | research §0 F1 |
| 全量跑一遍现行规则 | **新建 7020 条边 / 725 ms**（3469² 余弦矩阵 71 ms） | research §0 F2 |
| 真库 `suggested` 边 | **0**（从未触发） | research §3.1 |
| 限制点 | `backend/app/services/graph_service.py:108` `MAX_CARDS_FOR_SUGGEST = 200`，截断在 `:485` | 实测 grep 确认 |
| 标题重复 | **72 种 / 187 张卡（5.4%）** | research §1.4 |

> ⚠️ **口径差异（诚实标注）**：`docs/graph-relation-methods.md` 用的是 `_uxwalk` **副本**
> （3483 卡 / 2802 孤点 / 80.4%），research 用的是**真库**（3469 卡 / 3028 孤点 / 87.3%）。
> 两个数字都对，**别混用**。本文一律引真库口径。

### 2.5 「确实影响使用」只有两条（已登记）

`docs/overhaul-plan.md`（**活文档，重构期间唯一执行依据**）附录 BN 把未做事项按
「甲 不影响使用 / 乙 少一层 / **丙 确实影响使用**」分档，**丙档只有两条**（`:11224-11229`）：

| # | 症状 | 落点 |
|---|---|---|
| 丙-1 | 在项目里**清空描述**保存 → 看起来保存了，**重载后旧描述还在** | 前端 `frontend/src/pages/projects/useProjects.ts:102`（空值变 `undefined`，被 `JSON.stringify` 丢掉）；后端三处 `if x is not None` 让「显式 null」与「不传」等价：`backend/app/services/project_service.py:196-199`、`backend/app/api/understanding.py:465-468`、`backend/app/api/knowledge.py:180-183` |
| 丙-2 | 新上传笔记在人工补嵌入前**问答只能用 BM25**，界面上看不出来 | 成因已定论：清洗路径刻意不写向量，补嵌入**只有人工脚本**，**Beat 调度表里没有这个任务** |

**丙-2 的实测复核**：`backend/app/tasks/celery_app.py:128-211` 的 `beat_schedule` 共
**9 个任务**（`refresh_goal_progress` / `send_daily_review_email` / `reap_stale_tasks` /
`backup_database` / `cleanup_llm_ledger` / `cleanup_refresh_tokens` / `storage_snapshot` /
`restore_drill` / `vault_audit`）—— **没有任何补嵌入任务**，丙-2 成立。

### 2.6 前端取数层：作业面已数清

`frontend/docs/query-and-state-plan.md`（**活文档，计划未落地**）§1 的实测盘点：

| 项 | 数值 |
|---|---|
| `useEffect` 里取数的文件 | **28** |
| 轮询点 | **3**（`components/ReminderBanner.tsx:70`、`pages/notedetail/useNoteDetailData.ts:177` 与 `:201`） |
| Context | **2**（`contexts/AuthContext.tsx`、`components/Toast.tsx`） |
| 测试文件 | **23**，其中 ~10 个各自写了本地 `renderX()` 助手 |
| 依赖 | **未安装**（`@tanstack/react-query` 5.102.8 / `zustand` 5.0.15 在 npmmirror 上可解析） |

### 2.7 代码规模（`node_modules` 已排除）

| 区域 | 文件 | 行数 |
|---|---|---|
| `backend/app` | 161 | 37,437 |
| `backend/tests` | 84 | 21,703 |
| `backend/scripts` | 59 | 11,146 |
| `frontend/src` | 251 | 50,293 |
| `frontend/e2e` | 15 | 4,758 |
| **合计** | **570** | **125,337** |

### 2.8 门禁现状（改完必须过这些）

| 层 | 命令 | 出处 |
|---|---|---|
| 后端 lint | `python -m ruff check app` / `tests` / `scripts` | `.github/workflows/ci.yml` |
| 后端测试（离线） | `pytest`（`testpaths = tests`，`norecursedirs` 排除 `tests/integration`） | `backend/pytest.ini` |
| 配置一致性 | `python scripts/gen_env_example.py --check` | CI 步骤名「`.env.example` 与 `app/config.py` 一致」 |
| 契约漂移 | `python scripts/dump_openapi.py --check` | CI 步骤名「OpenAPI schema 与代码一致」 |
| 前端 lint / 测试 / 构建 | `npm run lint` / `npm run test` / `npm run build` | `frontend/package.json` scripts |
| 契约再生 | `npm run gen:api` + `npm run gen:api:drift` | 同上 |

> **硬约束**：`frontend/src/main.tsx` 顶部那 14 行样式表导入**有语义**（CSS 产物顺序；
> `styles/mobile-input-font-size.test.ts` 以它为断言前提，文件头写着「不要重排」）——
> 加 Provider 时**不许动那段注释与顺序**（`frontend/docs/query-and-state-plan.md:57-59`）。

---

## 3. 十二条工作流

### 图例

- **量**：单人在本机连续执行的大致时长（含验证与提交），非纯编码时间。
- **额度**：是否消耗 OpenCode / LLM 额度。
- **风险**：🔴 破坏性（删数据/改语义，必须单独批） ｜ 🟠 需备份或在副本上做 ｜ 🟢 低

---

### A 线 · 数据与内容

#### A1. 知识图谱关系召回重建 ★ 最高杠杆

| 项 | 内容 |
|---|---|
| **做什么** | ① 放开候选上限；② **同批**上线「排序 + 分批」；③ 疑似重复卡可见化；④ 白化 + 多信号排序；⑤ LLM 关系定型（有类型有方向） |
| **为什么是现在** | 瓶颈已定死是**吞吐不是质量**（§2.4）：2683/3028 孤点的最近邻**已经过现行阈值**，全量跑一遍只要 **725 ms**，而 `MAX_CARDS_FOR_SUGGEST = 200` 逼着要跑 ~15 轮，真库 `suggested` 至今为 **0** |
| **落点** | `backend/app/services/graph_service.py:108`（常量）、`:485`（截断）、`:802-1000`（`suggest_semantic_relations`，`:829` 另有 `.limit(100)`、`:865` `BATCH_SIZE = 20`）<br>`docs/relation-method-research-20261021.md` §4 P0/P1/P2<br>`docs/graph-relation-methods.md` §4 |
| **验收** | 副本上跑生产函数：孤点从 3028 显著下降；`suggested` 不再为 0；审核队列首屏是**按价值排序的 200 条**，而不是 7020 条一次性倾倒 |
| **量** | P0 半天 ｜ P1（白化 + 排序 + 弱监督标定）1–2 天 ｜ P2-1（LLM 定型）半天 |
| **额度** | P0/P1 否（纯 CPU）｜ **P2-1 是**：约 351 次调用 / 56 万 token / ~35 min |
| **风险** | 🔴 **P0-1 单独上线 = 审核队列爆炸**（7020 条待审等于没有），**必须与 P0-2 捆绑**；<br>🔴 P2-3 真去重合并会改产品语义，**要单独批**；<br>🟠 P1-1 白化后**阈值必须重新标定**（白化后 P90 仅 0.0303），且白化矩阵随语料漂移需定期重算 |

> **为什么它排第一**：`relation-method-research` §5 明确写了 ——
> **P0 是 P1/P2 一切的前置条件**（P1-2 的 Adamic-Adar 在稀疏图上是恒 0 的，
> P2 的 LLM 定型需要候选对）。

---

#### A2. 全库内容重跑（理解 + 出题）

| 项 | 内容 |
|---|---|
| **做什么** | ① 修 `question_session` 的题型/难度配比（中英双语提示词）；② 在**库副本**上用更强模型把 28 篇全部重跑 |
| **为什么是现在** | §2.3 的根因已定位到行，且**不修就白跑**：题型与难度写死在提示词里，`understand_tasks.py:324` 也不传参数 → 现在重跑一万遍还是「简单四选一」（实测 3358 道题**全是 choice**、hard 只有 18 道） |
| **落点** | `backend/app/services/llm/prompts.py:196-216`（中文）、`:524`/`:552`（英文）、`:175-194`（死代码，顺手修或标注）<br>`backend/app/tasks/understand_tasks.py:216-401`（`_generate_questions`）<br>顺手接上已有能力：`backend/app/api/understanding.py:729-730` 的 `target_categories` / `target_difficulty` |
| **验收** | 小样 5 篇重跑后 `quiz_items` 出现 `fill_blank` / `short_answer` 与 `medium` / `hard`；`prompt_version` 分组报表能看到新旧两版对比（工具已现成：`backend/app/services/prompt_version_report_service.py`） |
| **量** | 改提示词 + 小样验证 半天；全量重跑 ~35 min/轮 |
| **额度** | **是**（换模型即可绕开缓存，见 C-3） |
| **风险** | 🔴 **破坏性**：`backend/app/api/understanding.py:159-184` 重跑会删掉该笔记**全部卡片 / 题目 / 复习记录 / 图谱关系**（`ReviewState` 按 `card_id` 保留）。**必须在副本上做** |

---

#### A3. 模型对照实验 + 选型结论

> ⚠️ **本条的形态已因拍板 2 改变**：不做跨模型横评，改为**同模型下的提示词版本对照**。
> 执行时以 **§5.1 第 1 条（A3′）** 为准，下表保留原始设计以便对照。

| 项 | 内容 |
|---|---|
| **做什么** | 同一份语料（挑 5–8 篇有代表性的），用 4–5 个模型各跑一遍完整理解 + 出题，出对照报告<br>**→ 改为**：同一份语料 + 同一个 `deepseek-v4-flash`，跑 v1/v2/v3 三版提示词 |
| **为什么是现在** | 这是**唯一能把额度花掉大半、又留下可引用结论**的路线。当前「哪个模型更适合这个项目」**没有任何实测依据**<br>**→ 改为**：当前「提示词的哪一处改动带来了改善」没有任何实测依据 |
| **落点** | 复用 `backend/scripts/_e2e_full_runner.py` 的隔离栈；报表用 `backend/app/services/prompt_version_report_service.py` |
| **验收** | 一份报告：卡片数 / 题型分布 / 难度分布 / `prompt_version` 分组效果 / 掌握度对照 + 明确推荐（含"不推荐哪个、为什么"）<br>**→ 改为**：v1/v2/v3 三版的题型与难度分布对照表 + 明确结论（哪一处改动带来了改善、改善有多大） |
| **量** | 脚本 + 跑 + 写报告：1–2 天 |
| **额度** | **是（主要消耗项）**<br>**→ 改为**：是，但只有 `deepseek-v4-flash` 一档费率；三版全量重跑合计约 9M token 量级 |
| **风险** | 🟠 全部在副本上跑；🟠 注意 C-1 的 10 RPM —— **三版是串行跑，约 1.8 小时纯等待** |

---

#### A4. 卡片质量治理（打分 / 语义去重 / 重写）

| 项 | 内容 |
|---|---|
| **做什么** | 3469 张卡逐卡 LLM 打分（信息量 / 自足性 / 可出题性），低分卡标记或重写；语义级去重 |
| **为什么是现在** | 已知问题有 ground truth：元数据型卡片 89 张（2.6%）、自指型空卡片（走查报告 §2.3 有实例）、**72 种标题重复 / 187 张卡（5.4%）**。现有 `backend/scripts/merge_duplicate_cards.py:285` 只按**标题相似度**去重，**没有语义级** |
| **落点** | 新脚本；扩展 `backend/scripts/report_duplicate_cards.py` |
| **验收** | 产出一份「低分卡清单 + 理由 + 建议动作」；重复卡清单与 `report_duplicate_cards.py` 的现有输出对得上 |
| **量** | 1–2 天 |
| **额度** | **是**（3469 次调用，量不小） |
| **风险** | 🔴 **合并/删除卡会动产品语义与复习历史**（`review_states` 会变孤儿行）。**本轮只做「打分 + 报告 + 标记」，不删不合并** —— 真去重单独批 |

---

### B 线 · 功能

#### B1. 清洗策略升级 —— 兑现用户自己提的第 5 条

| 项 | 内容 |
|---|---|
| **做什么** | ① 公式简化（`$6.34m \times 61.95m$` → `6.34m*61.95m`）；② 标题层级严谨化；③ 数字/字母前后空格；④ **段落级**去重；⑤ 把「重复块」文案改成如实描述 |
| **为什么是现在** | `docs/ux-walkthrough-report-20261021.md` §5 第 5 条：**一条都没做**，且对 Markdown 输入**逐字节空转**（711 → 711，实测）。这是用户自己点名的需求里**唯一完全没兑现**的 |
| **落点** | `backend/app/services/cleaning_service.py:69`（`clean_rules`，对 .md 输入无变化）、`:64-66`（`_OCR_CORRECTIONS` 仍是占位）、`backend/app/config.py:245`（`chunk_size=500` —— 去重粒度不对是「看不到重复」的真因） |
| **验收** | 走查报告 §5 那张表的 4 个 ❌ 变 ✅；重复块文案与实际粒度一致 |
| **量** | 1 天（规则实现 + 测试 + 真样本回归） |
| **额度** | 否（规则清洗；若走 LLM 清洗另计） |
| **风险** | 🔴 **改清洗语义 = 改产品语义，要你批**。建议公式简化做成**可配置开关、默认关**（避免破坏公式可编辑性的既有承诺） |

---

#### B2. 自动补嵌入闭环（含 Beat 定时任务）

| 项 | 内容 |
|---|---|
| **做什么** | ① 给补嵌入加 **Beat 定时任务**；② 导入后自动排队；③ 无向量的笔记在详情页给**可见标记** |
| **为什么是现在** | §2.5 丙-2 —— overhaul-plan 附录 BN.3 **仅有的两条「确实影响使用」**之一。实测 **210/818 chunk 无向量**；`celery_app.py:128-211` 的 9 个 Beat 任务里**没有补嵌入**（实测复核） |
| **落点** | `backend/app/tasks/celery_app.py:128`（`beat_schedule`）、`backend/app/main.py:310`（自述 Beat 表里没有这个任务）、`backend/app/services/chunk_service.py:23-24`、`backend/scripts/embed_chunks.py` / `embed_cards.py`、`backend/app/main.py:354-362`（`/ready` 的 `pending_embeddings`） |
| **验收** | 上传一篇新笔记 → **不做任何手工操作** → 一段时间后 `/ready` 的 `pending_embeddings` 归零；界面不再出现「已降级为关键词检索」而无解释 |
| **量** | 半天 |
| **额度** | **否**（本地 bge-m3） |
| **风险** | 🟠 嵌入模型要加载 4.3 GB，**必须留在异步 worker 进程**（`AGENTS.md` §3 硬约束）；🟠 注意内存阈值会触发静默降级（见 D2） |

---

#### B3. 「清空字段」语义修正

| 项 | 内容 |
|---|---|
| **做什么** | 区分「显式给了 `null`」与「没传这个字段」，让清空描述真的能保存 |
| **为什么是现在** | §2.5 丙-1 —— BN.3 仅有的两条之一。实测：清空描述保存 → 看起来保存了，**重载后旧描述还在** |
| **落点** | 前端 `frontend/src/pages/projects/useProjects.ts:102`<br>后端 `backend/app/services/project_service.py:196-199`、`backend/app/api/understanding.py:465-468`、`backend/app/api/knowledge.py:180-183` |
| **验收** | 清空 → 保存 → 重载 → 仍是空的；**同时不传字段的旧行为不变**（不破坏既有契约）；`npm run gen:api:drift` 与 `dump_openapi.py --check` 都过 |
| **量** | 半天–1 天（含契约再生与前端类型更新） |
| **额度** | 否 |
| **风险** | 🔴 **改接口语义 = 改产品语义，要你批**。建议用哨兵值或显式 `fields` 清单，避免 `null` 与「不传」在全仓各处语义不一致 |

---

#### B4. 批量导出 / 导入互操作

| 项 | 内容 |
|---|---|
| **做什么** | ① 整库导出：`GET /api/notes/export?variant=…&ids=…` 返回 `application/zip`（内含 `index.md` 目录 + 每篇一个 `.md`）；② Markdown 目录导入 |
| **为什么是现在** | `docs/open-source-readiness.md:796-801`（**活文档**）明确写着「单篇导出已落地，但**批量导出（整库搬走）与导入都还没有**，而『整库可搬走』才是这条（防锁定）真正的价值」；目标形态已写好在 `frontend/docs/note-export.md:209-211` |
| **落点** | 后端新增导出端点（参照现有单篇导出）；`frontend/docs/note-export.md` 同步更新未做项 |
| **验收** | 导出的 ZIP 解压后能直接放进 Obsidian vault；导入一个 Markdown 目录能建出笔记（状态机走通） |
| **量** | 1–2 天 |
| **额度** | 否 |
| **风险** | 🟠 ZIP 生成要防 zip 炸弹 / 大库内存占用；🟠 导入要过既有的 magic bytes 与配额护栏（`backend/app/services/upload_safety.py`） |

---

#### B5. 题目审核 / 笔记评估反馈

| 项 | 内容 |
|---|---|
| **做什么** | ① 题目详情页的编辑 / 删除（后端 API + 前端）；② AI 对比原始资料，指出遗漏概念与理解偏差 |
| **为什么是现在** | `.trae/documents/未完成的内容.md:99-104`（#11、#12）列为中优先级且**完全未实现**。而且 **A2 重跑之后题库会整批换掉** —— 没有审核入口，用户无从对比新旧<br>⚠️ **注意该文件的地位**：`.trae/` 已被 `.gitignore:197` 忽略（实测 `git check-ignore` 命中），**它不是仓库里的权威文档**，只是本机的历史待办清单。本条的价值在于"这两件事确实没做"，**不依赖那份文件成立** —— 开工前应先用代码实测确认 |
| **落点** | 新增题目编辑/删除端点（`backend/app/api/understanding.py` 附近）+ 前端题目详情页 |
| **验收** | 能改一道题的题干/选项/答案/解析并落库；能删一道题且复习记录级联正确 |
| **量** | 题目编辑 1 天；笔记评估 1–2 天（LLM 侧） |
| **额度** | 题目编辑否；笔记评估**是** |
| **风险** | 🟠 删除题目要处理 `review_logs` / `review_states` 的级联（参考 `backend/app/models/review_state.py:13` 的说明） |

---

### C 线 · 架构与工程

#### C1. 前端取数层迁移：TanStack Query + Zustand ⏸ 待批

| 项 | 内容 |
|---|---|
| **做什么** | 把 28 个文件的 `fetch + useEffect` 换成 TanStack Query；2 个 context 收敛成 Zustand |
| **为什么是现在** | **计划已写好、零调研成本、纯执行**：`frontend/docs/query-and-state-plan.md`（173 行，含 4 批次 / 5 条不变量 / 行为变化清单 / 四个待决策项）；`docs/overhaul-plan.md:3334-3335` 登记为 ⏸ 未做 |
| **落点** | `frontend/docs/query-and-state-plan.md` §2（Provider 与初始化）、§2.2（测试策略）、§2.3（默认值）<br>新增 `frontend/src/test/render.tsx`（`renderWithProviders`） |
| **验收** | 计划 §5 的「必须保持不变的东西」逐条不破；`npm run lint` / `test` / `build` 全过；每批独立提交可单独回滚 |
| **量** | 4 个批次，2–3 天 |
| **额度** | 否 |
| **风险** | 🟠 **行为变更面最大的一条**（28 个文件）。硬约束见 §2.8：`main.tsx` 的样式表导入顺序**不许重排**；<br>⚠️ **开工前必须先回答计划 §6 的四个问题**（要不要加运行时依赖 / 默认值 / 批次边界 / 回滚口径） |

---

#### C2. 文档完整性治理

| 项 | 内容 |
|---|---|
| **做什么** | ① 写一个**文档链接完整性守卫**（扫全仓 `*.md` 的相对路径与 `#锚点`，验证目标真实存在）；② 把它接进 CI；③ 逐条修正过期陈述 |
| **为什么是现在** | 文档交叉引用**已经很多但没有任何一层在守**：实测 `backend/tests/*.py` 有 **13 处**引用 `docs/decisions.md#F-xx` / `docs/overhaul-plan.md §x.y`；`frontend/docs/openapi-client.md:1406` 自述某检查「**未跑**」。改文件名或插入行都会**静默留下死链** |
| **落点** | 新增 `backend/scripts/check_doc_links.py`（或 `frontend/scripts/doc-link-check.mjs`）+ `.github/workflows/ci.yml` 加一步 |
| **验收** | 脚本能抓出**人为注入的一条死链**（反空转证明），然后对全仓跑出 0 死链；CI 能拦住 |
| **量** | 1 天 |
| **额度** | 否 |
| **风险** | 🟢 低。⚠️ 注意 `docs/README.md:161-162` 的已知坑：**移动文件或插入行会让 `文件:行号` 引用整体偏移**（已发生过一次：`architecture.md` 加横幅后 overhaul-plan 里的引用偏移约 21 行） |

**顺带核到的两处文档不一致（写进这里，动手时一并处置）**：

| 现象 | 证据 |
|---|---|
| `docs/overhaul-plan.md:3239,3256` 说 `docs/decisions.md`「尚未转为只读归档」，但该文件**已经有**只读归档横幅（冻结于 2026-09-11） | `docs/decisions.md:2-3` 实测；`docs/README.md:87` 也登记为「历史快照（2026-09-11 冻结）」 |
| `docs/relation-method-research-20261021.md` **没有** §0 约定的三态横幅（只有调研性质说明行） | 该文件 `:3-5` 实测；`docs/README.md:28` 要求「新增文档**必须**带状态横幅」，`:159` 重申 |

---

#### C3. 走查报告缺陷的批量收口

| 项 | 内容 |
|---|---|
| **做什么** | 按 `docs/ux-walkthrough-report-20261021.md` §7 的建议顺序逐条修（**先扣掉 §2.2 已修的两条**） |
| **为什么是现在** | 报告证据齐全（80+ 张截图 + `walk*.json`），上下文最热 |
| **已核实仍成立的优先项** | ① 卡片页真分页（`frontend/src/pages/KnowledgeCards.tsx:109` 硬编码 999）<br>② `daily-plan` 无目标回 200（`backend/app/api/goals.py:123-148`）+ 前端改按 `error_code` 判断（`frontend/src/pages/Dashboard.tsx:104-111`）<br>③ 图谱默认只画连通分量 + 加筛选（`docs/ux-walkthrough-report-20261021.md` §3.10）<br>④ 去掉 `error_code` 前缀（`frontend/src/api/client.ts:518`）<br>⑤ 清理开发者话术（`frontend/src/i18n/messages.zh-CN.ts:1033-1035,1151,1457`）<br>⑥ 上传入口进侧边栏（`frontend/src/components/Sidebar.tsx:54-87`） |
| **验收** | 每条给出「修前 / 修后」实测证据；前端过 lint / test / build |
| **量** | 前 4 条 1 天；全表 2–3 天 |
| **额度** | 否 |
| **风险** | 🟢 低。每批独立提交 |

---

#### C4. a11y 深层验证（axe 看不见的那一层）

| 项 | 内容 |
|---|---|
| **做什么** | ① Tab 顺序是否**合理**（完整 `document.activeElement` 序列比对）；② 模态/抽屉的**焦点陷阱**验证；③ 焦点环外观断言 |
| **为什么是现在** | `frontend/docs/a11y-audit.md:326-369` 自述这是「**当前没有任何一层在守**」的待办清单。已做的一半是 `expectReachableByTab`（16 处）与 `findFakeAffordances`（零容忍扫描）；**没做**的正是上面三条 |
| **落点** | `frontend/e2e/`（Playwright 真浏览器）；参考 `frontend/docs/a11y-audit.md` §4.1 |
| **验收** | 新增断言：Tab 序列可复现且被记录；打开 `LinkManagerModal` / `DeleteNoteDialog` / 侧边栏抽屉后，焦点**不跑到背后** |
| **量** | 1–2 天 |
| **额度** | 否 |
| **风险** | 🟢 低 |

---

### D 线 · 决策与收口

#### D1. 「能不能删」的决策报告（只出清单，不执行）

| 项 | 内容 |
|---|---|
| **做什么** | **只读**盘点 `backend/data/` 下每一样「谁在引用 / 删了会怎样 / 怎么恢复」，出可执行清单（含回滚命令） |
| **盘点对象** | `chroma/` **114 个子目录**（向量已搬进 `chunks` 表，不参与检索）｜约 **4.6 GB** 闲置本地 MinerU 模型（PDF 实际走云端，`backend/app/config.py:179` 默认 `vlm-http-client`）｜`_uxwalk/` 副本｜`tmp_test` / `_understand_test_tmp`｜两个 db 备份 |
| **为什么值得** | `docs/overhaul-plan.md:11204` 只写了「想清理磁盘时（纯家务）」，**从来没有人系统盘过**。而 `AGENTS.md` §6 明确警告「历史上出现过清理副本却删掉真身」 |
| **落点** | 新增一份 `docs/data-inventory-<date>.md`（过程记录）；**不执行任何删除** |
| **验收** | 每一项都有「引用它的代码/文档位置 + 删除影响 + 回滚方式」；有明确「不确定 → 不动」的清单 |
| **量** | 半天 |
| **额度** | 否 |
| **风险** | 🔴 **本文只出报告。任何删除动作都要单独批、单独备份** |

---

#### D2. 「隐性降级」可见性收口

| 项 | 内容 |
|---|---|
| **做什么** | 把三处**静默降级**变成用户可见 / 可查 |
| **三处降级** | ① 嵌入模型因内存不足换小模型 —— **只有日志 WARNING**，界面完全无感（`backend/app/services/embedding_service.py:71-82`、`backend/app/config.py:229`）<br>② 新笔记无向量 → RAG 只能 BM25（与 B2 同源）<br>③ chunk 索引重建失败 → 该笔记没有语料，而日志说「**不影响清洗结果**」（`backend/app/tasks/clean_tasks.py:405-406`） |
| **为什么值得** | `docs/ux-walkthrough-report-20261021.md` 附录 C.1 的结论是「**降级本身是确凿的，而且用户无从得知**」—— 三个都是「看起来成功了，其实少了东西」 |
| **验收** | 三个降级各自有可见出口（界面标记 / 可查接口），且**降级与正常两种状态都能被测试构造出来** |
| **量** | 1 天 |
| **额度** | 否 |
| **风险** | 🟠 ② 与 B2 有重叠，**建议与 B2 同批做**，避免两套机制 |

---

## 4. 推荐执行顺序

> 排期原则：**先做不花钱、风险低的**（把上下文热的部分收掉），**再动数据线**（副本上跑，可长跑），
> **最后是架构线**（面最大，需要你先批）。并行只允许发生在**互不写同一个库**的两条之间（见 C-2）。

```
第 1 天 · 零额度线（可并行，互不冲突）
  ├─ C3  走查缺陷前 4 条        ← 上下文最热，证据齐全
  ├─ C2  文档链接守卫 + CI 接线  ← 纯工程，一次到位
  ├─ B2  自动补嵌入闭环         ← 修掉 BN.3 丙-2（不花额度）
  └─ D2  隐性降级可见性         ← 与 B2 同批，共用一套出口

第 2 天 · 数据线（全部在副本上，可长跑）
  ├─ A1  图谱 P0（P0-1 + P0-2 必须捆绑）
  ├─ A2  提示词修复 → 小样 5 篇验证 → 全量重跑
  └─ B1  清洗升级（要你批；公式简化默认关）

第 3 天 · 深度线
  ├─ A1  P1（白化 + 多信号排序 + 弱监督标定）
  ├─ A3  模型对照实验（要你选模型）
  └─ C1  前端取数迁移（要你先答 4 个问题）

第 4 天 · 收口
  ├─ A1  P2-1 LLM 关系定型
  ├─ A4  卡片打分 + 报告（只标记不删）
  ├─ C4  a11y 深层验证
  └─ D1  数据清理决策报告

按需（不阻塞上面任何一条）
  └─ B3 清空字段语义 · B4 批量导出 · B5 题目审核
```

**关键路径**：`A1(P0)` → `A1(P1)` → `A1(P2-1)`。
其余各条之间**没有依赖**，可任意重排。

---

## 5. 已拍板的 6 件事

> 按 `AGENTS.md` §6，以下四类**必须停下来问**：删数据/文件 · 装占磁盘的东西 ·
> 改产品语义或接口 · 推翻既定决策。下面 6 条落在这些类别里，**已于 2026-10-21 逐条拍板**。

| # | 问题 | 为什么必须你决定 | **拍板结果** |
|---|---|---|---|
| **1** | **A2 / A3 在副本还是真库上重跑？** | 真库重跑会**删掉该笔记全部卡片/题目/复习记录/图谱关系**（`backend/app/api/understanding.py:159-184`）—— 现有 3358 道题 + 604 条关系会被清空重来 | ✅ **在副本上跑**。`backend/data/_uxwalk/` 是现成隔离栈，`backend/scripts/_e2e_full_runner.py` 一条命令起（见 §6.2）。真库零写入 |
| **2** | **A3 用哪几个模型？** | 决定额度消耗量与结论价值 | ✅ **只用 OpenCode 的 API 跑 DeepSeek V4 Flash**（不做跨模型横评）→ **A3 的性质随之改变**：改为**同模型下的提示词版本对照**（见 §5.2） |
| **3** | **A2 的提示词跑几版对照？** | 每改一版提示词缓存键就变（真实调用、真实产出），这正是对照实验的机制 | ✅ **三版递进**：v1 = 现状（写死 choice/easy）→ v2 = 只修硬编码 → v3 = v2 + 明确要求四类题型与三档难度按比例分布 |
| **4** | **C1 要不要加运行时依赖？** | 5.1 定过「不加运行时依赖」（`docs/overhaul-plan.md:10392`），但那是为生成客户端定的（类型层） | ✅ **两个都批**：`@tanstack/react-query@5.102.8` + `zustand@5.0.15`。计划 §6 的另三问：**默认值照 §2.3 建议值**（`staleTime: 30_000` / query `retry: 1` / mutation `retry: 0` / `refetchOnWindowFocus: false`）；**AuthContext 不迁**；**验收改用 §3.3 的三条可测口径** |
| **5** | **B3「清空字段」改接口语义，批不批？改多大？** | 它是 BN.3 **仅有的两条「确实影响使用」**之一；改法涉及「显式 null vs 不传」的全局语义 | ✅ **批，但先只改「项目描述」一个字段**（`frontend/src/pages/projects/useProjects.ts:102` + `backend/app/services/project_service.py:196-199`）。跑通语义、确认「不传字段的旧客户端行为不变」后再推广到另两处 |
| **6** | **D1 清理 / A4 重复卡的删除边界？** | 涉及**删文件**与**删数据 + 改复习历史**；`AGENTS.md` §6 警告过「清理副本却删掉真身」 | ✅ **都只出报告，不执行删除**。D1 只产出清单（含回滚命令）；A4 只打分标记。**任何删除动作都要另开一轮单独批** |

### 5.1 拍板带来的三处修正

拍板不是"盖章"，它改变了下面三条的形态。**执行时以本节为准**：

1. **A3 不再是"模型对照实验"**（拍板 2）。改为 **A3′：同模型提示词版本对照** ——
   同一份语料、同一个 `deepseek-v4-flash`，跑 v1/v2/v3 三版提示词，
   用 `backend/app/services/prompt_version_report_service.py` 按版本对比题型/难度分布。
   **额度消耗随之下降**（不再有"贵模型"这一项）。
2. **A2 与 A3′ 合并成一件事**（拍板 3）：三版提示词 × 全量重跑，本身就是对照实验。
   §4 执行顺序里 `A2` 与 `A3` 两格按此合并执行。
3. **B1 的承诺冲突要一并处置**（拍板 4 全选）：公式简化与标题层级化属**改写原文**，
   与清洗一直承诺的「仅做减法，不改变原意」相冲突。执行时需**同步更新口径**：
   `docs/archive/项目架构.md`（历史快照，只加标注不改正文）与 `参赛/参赛贴文.md` 的表述，
   并**为公式简化提供可配置开关、默认关**。

### 5.2 开工前必须先做的一次冒烟验证

拍板 2 选的模型是 **DeepSeek V4 Flash**，也就是 `backend/.env:39` 里现有的
**`deepseek-v4-flash`** —— **不需要改配置**。

> **一处需要留意的名字差异**：OpenCode Go 档文档里列的模型 ID 写作 `deepseek-v4.1-flash`，
> 与 `.env` 里的 `deepseek-v4-flash` **字面不一致**。
> **历史证据支持后者是可用的**：`llm_calls` 里 200 行**全部**是
> `provider=deepseek, model=deepseek-v4-flash`，且 `success` 全为 1（实测只读查询）。
> 但**是别名还是巧合，无法从本仓库断定** —— 所以不靠推断，靠一次实调。

**开工第一步是一次最小调用**（不是改名字，是冒烟）：用当前 `.env` 配置发一个最短请求，确认
(a) 网关接受这个名字、(b) 返回 200、(c) `llm_calls` 里落下一行成功记录。
**这一步失败就先查网关与额度，不要开始全量重跑** —— 否则整批跑在错名字上，白等 35 分钟。

### 5.3 批量模式授权

按 `AGENTS.md` §4，你说一句「批量执行 / 一次做完 / 不要中途问我」即视为
**对整批的预授权** —— 中途不逐项确认，**每项完成后立即本地提交**，全部做完再集中汇报。

⚠️ **但 §5 里这几项即使在批量模式下也要单独点头**（拍板 6 已把它们挡在报告阶段）：

- D1 清单里任何**实际删除**；
- A4 的**真去重合并**；
- B3 从「项目描述」**推广到另两处字段**；
- B1 公式简化的**开关默认值改成开**。

---

## 6. 复现方式

### 6.1 事实底账的复现（全部只读，不写库、不联网、不加载模型）

```powershell
# 项目声明的环境（见 start.bat:20 CONDA_ENV=mineru_env）
cd D:\engramnote
$env:PYTHONIOENCODING='utf-8'
python _agenttmp\quota\usage_probe.py      # llm_calls 表结构与时间范围
python _agenttmp\quota\usage_probe2.py     # 按 task/scene/日 聚合 + 内容计数
python _agenttmp\quota\corpus_probe.py     # 笔记/chunk/卡片/题目/关系规模
```

> 三个脚本都以 `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)` 打开真库，**只读**。
> 它们目前放在 `_agenttmp/quota/`（临时区）；若这份文档要长期维护，
> 应把它们收进 `backend/scripts/` 并加 `--check` 语义（见 C2 的同类做法）。

### 6.2 隔离栈（真库零写入）—— A 线全部在此跑

```powershell
# 后端 + worker + 前端（取自 docs/ux-walkthrough-report-20261021.md 附录 A）
cd D:\engramnote\backend
$env:ENGRAMNOTE_E2E_TMP='D:\engramnote\backend\data\_uxwalk'; $env:ENGRAMNOTE_E2E_KEEP='1'
$env:ENGRAMNOTE_E2E_API_PORT='8011'; $env:ENGRAMNOTE_E2E_CONTROL_PORT='8012'
$env:E2E_FULL_PORT='5183'; $env:LLM_PROVIDER='glm'
& "$env:USERPROFILE\anaconda3\envs\mineru_env\python.exe" scripts\_e2e_full_runner.py
```

> ⚠️ 为什么必须隔离：`backend/app/config.py` 的 `DATA_DIR` 是**硬编码常量**，Celery broker 目录
> **没有环境变量入口** —— 第二套后端若直接 `uvicorn app.main:app`，会与正在跑的那个 worker
> **抢同一个 broker 目录**（文件系统 broker 没有所有权标记，**谁先 rename 谁拿到消息**）。
> 细节见 `backend/scripts/_e2e_full_bootstrap.py:11-15`。

### 6.3 门禁（每条工作流改完都要过，命令见 §2.8）

```powershell
# 后端
cd D:\engramnote\backend
python -m ruff check app; python -m ruff check tests; python -m ruff check scripts
python -m pytest
python scripts/gen_env_example.py --check
python scripts/dump_openapi.py --check

# 前端
cd D:\engramnote\frontend
npm.cmd run lint; npm.cmd run test; npm.cmd run build
npm.cmd run gen:api:drift
```

### 6.4 全链路 E2E（**会消耗真实额度**）

```powershell
cd D:\engramnote\frontend
$env:ENGRAMNOTE_E2E_FULL = '1'; npm.cmd run e2e:full
```

> `frontend/docs/e2e.md:250` 说明它**刻意没接进 CI**：要联网、要花钱、分钟级。
> **跑之前先确认额度还剩多少。**

---

## 7. 明确不做的事（不要再提）

以下方向**已否决**，本文一条都没有包含（`AGENTS.md` §2 + `docs/overhaul-plan.md` 的 ⛔ 条目）：

| 方向 | 否决理由 | 依据 |
|---|---|---|
| 更换数据库引擎（PostgreSQL / pgvector） | 资源约束，SQLite 单写者路线自洽 | `docs/sqlite-single-writer.md`；`overhaul-plan.md:3015` |
| 引入 Redis / 消息队列 / 缓存服务 | 同上 | `overhaul-plan.md:3019` |
| 引入第二套数据库迁移工具（Alembic 重建） | 手写迁移是 SQLite 路线的**唯一通道** | `overhaul-plan.md:3017` |
| 容器化部署（含 nginx / Docker 验收） | 历史遗留，**未经验证** | `overhaul-plan.md:2983-2984,10615` |
| 恢复已被替换掉的旧存储或索引方案（Chroma 检索、独立向量库） | 向量与原文**同库同表**是既定设计 | `AGENTS.md` §3；`overhaul-plan.md:11204` |

**另外两条「本方案刻意不做」**：

- **A4 的真去重合并**、**D1 的实际删除** —— 都只出报告，不执行（见 §5 第 6 条）。
- **把 `docs/architecture.md` 改写成活文档** —— 它已冻结为历史快照，现状依据是
  `docs/overhaul-plan.md` 与代码（`docs/README.md:40-42,86`）。重写它等于造第二份"现状"。

---

## 8. 诚实边界（本文哪些地方不确定）

按本仓库「把不确定写出来」的惯例，以下三条**是本文的已知弱点**：

1. **§2.4 的图谱数字是引用，不是重测。** 3028 孤点 / 7020 边 / 725 ms 来自
   `docs/relation-method-research-20261021.md`（过程记录），我用真库只读统计**只验证了**
   `suggested = 0`、`confirmed/related = 604`、`MAX_CARDS_FOR_SUGGEST = 200`（`:108`）
   与截断点（`:485`）。**孤点数与边数没有重算** —— 要最新数字请按该文档 §7 重跑。
2. **§3 的「量」是估算，不是实测。** 每条工作流都没开工，时长是按改动面与
   §2.7 的代码规模推的。**A1 的 P1 与 C1 的 4 批次尤其可能超时。**
3. **§2.2 的「已修」判断只覆盖我逐条核过的两条。** 走查报告 §4 严重度总表共 **27 条**，
   我**只复核了其中 4 条**（卡片页 999、`daily-plan` 400、日报日期、SSE 重复）。
   其余 **23 条**的现状**未逐条核**，C3 开工前需要先跑一遍状态核对。

---

## 9. 与其它文档的关系

| 文档 | 状态 | 与本文的关系 |
|---|---|---|
| `docs/README.md` | 活文档 | **本文已登记进 §2.1 的清单**；本文遵守它的三态横幅约定与 §5 维护约定 |
| `docs/overhaul-plan.md` | 活文档（重构期间唯一执行依据） | 本文是它的**下游执行计划**，不推翻它的任何决策；冲突时**以它为准** |
| `docs/pipeline-mechanism-plan.md` | 活文档（已定案，本轮零代码改动） | 处理环节/任务机制主题；A2 与 B2 若触及任务机制，**以它为准** |
| `docs/relation-method-research-20261021.md` | 过程记录 | A1 的**方案来源**；数字需按它的 §7 重跑才能更新 |
| `docs/graph-relation-methods.md` | 活文档 | A1 的**现状依据**（图谱关系判定主题） |
| `docs/ux-walkthrough-report-20261021.md` | 过程记录 | C3 的**缺陷清单来源**；§2.2 已标出其中两条已修 |
| `docs/mineru-backend-decision.md` | 活文档 | D1 的**依据**（本地 MinerU 已定案留云端，模型可回收） |
| `frontend/docs/query-and-state-plan.md` | 活文档（计划未落地） | C1 的**执行依据**；四个待决策项见其 §6 |
| `frontend/docs/a11y-audit.md` | 过程记录 | C4 的**待办来源**（§4「axe 看不见的东西」） |
| `frontend/docs/note-export.md` | 活文档 | B4 的**目标形态与未做项**（§209-211） |

> **不在仓库里的来源（诚实标注）**：`.trae/documents/*.md` 与 `_agenttmp/` 都已被 `.gitignore` 忽略
> （`.gitignore:197` 实测命中），**它们不是本仓库的权威文档**，只在本机存在。
> 本文引用 `.trae/documents/未完成的内容.md` 仅用于说明「这两件事确实没做」——
> 结论本身由代码实测支撑（见 B5 的备注）。**换台机器克隆本仓库后，那份文件不存在。**

---

## 10. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-21 | 首版。12 条工作流 + 事实底账（真库只读实测）+ 6 个待决项。**零开工。** |
| 2026-10-21 | **6 个待决项全部拍板**（§5 表格改为「拍板结果」），新增 §5.1 拍板带来的三处修正（A3→A3′、A2+A3′ 合并、B1 承诺冲突的处置）与 §5.2 开工前的冒烟验证（模型名 `deepseek-v4-flash` vs 文档 ID `deepseek-v4.1-flash` 的名字差异）。状态横幅由「计划，未落地」改为「计划已拍板，未落地」。**仍未开工。** |
| 2026-10-21 | **更正**：拍板 2 的模型名上一版误记为 `DeepSeek V4.1 Flash`，实际是 **`DeepSeek V4 Flash`**（即 `.env:39` 现有的 `deepseek-v4-flash`）。§5 表格、§5.1 第 1 条、§5.2、§3 A3 四处一并改正；§5.2 由「名字不一致待定」改为「名字一致，但仍做一次冒烟实调」。**仍未开工。** |
