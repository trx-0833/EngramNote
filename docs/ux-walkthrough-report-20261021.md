# EngramNote 使用体验提升报告（按页面索引）

> **状态：过程记录** ｜ 记录时间：2026-10-21 ｜ 权威性：不是现状依据
>
> 本文是一次**真实用户视角的全功能实测**留档。结论以当次运行的实测数据与截图为准，
> 代码引用带 `文件:行号`。按 `docs/README.md:22-26` 的裁决顺序，代码实测优先于文档；
> 与本文冲突时，请重跑附录 A 的复现步骤。

**怎么读这份文档**：正文按**页面**组织（§3），每个页面给出「你在这页会遇到什么」。
跨页面的问题在 §2。如果你要**排优先级**，直接看 §4 的严重度总表。

**严重度标记**：

| 标记 | 含义 |
|---|---|
| 🔴 **P0** | 主流程产出不可用（做完了但结果没法用） |
| 🟠 **P1** | 明显损失功能、或让用户误解 |
| 🟡 **P2** | 摩擦、不一致、措辞问题 |
| ✅ | 实测通过，建议保持 |

---

## 0. 走查怎么做的

### 0.1 环境：真实数据 + 隔离副本，真实库零写入

用项目**自带的隔离机制**起了一套完整栈，与人类正在用的 8001/5173 完全并行：

| 项 | 值 |
|---|---|
| 隔离根 | `backend/data/_uxwalk/`（数据库 32.7 MB + vault 116.7 MB 的副本） |
| 后端 API | `http://127.0.0.1:8011`（`scripts/_e2e_full_runner.py`） |
| Celery worker | 独立 broker（`ENGRAMNOTE_E2E_BROKER_DIR`，见 `scripts/_e2e_full_bootstrap.py:50`） |
| 前端 | `http://localhost:5183`（Vite，`VITE_API_TARGET` 指向 8011） |
| 数据规模 | 28 篇笔记 / 3469 张卡片 / 3358 道题 / 604 条关系 / 820 个 chunk |
| LLM | 真实调用 GLM（`glm-4-flash`，`LLM_PROVIDER=glm`） |

> ⚠️ 为什么必须隔离：`app/config.py` 的 `DATA_DIR` 是**硬编码常量**，Celery broker 目录
> （`config.py:838`）没有环境变量入口。第二套后端若直接用 `uvicorn app.main:app`，
> 会与人类那个 worker 抢同一个 broker 目录 —— 文件系统 broker 没有所有权标记，
> **谁先 rename 谁拿到消息**（`_e2e_full_bootstrap.py:11-15` 记录了这个坑）。

### 0.2 走查方式

不桩任何接口（与 `frontend/e2e/` 的设计前提相反），用 Playwright 驱动真浏览器：

| 轮次 | 覆盖 |
|---|---|
| 第 1 轮 | 未登录面 + **22 条路由逐页**截图与观测（`walk1.mjs`） |
| 第 2 轮 | 搜索 / 三视图 / 位置记忆 / 导出 / 答题 / 问答 / 卡片筛选 / 图谱检索（`walk2.mjs`） |
| 第 3 轮 | 上传→清洗全链路 + 版本历史 + 删除恢复 + 建目标 + 题号跳转（`walk3.mjs`） |
| 第 4~5 轮 | 卡片截断 / 图谱建议 / 移动端 375×667 / 删除恢复补测（`walk4/5.mjs`） |
| 第 6 轮 | 选中提问浮层影响面（`walk6.mjs`） |
| 第 7~9 轮 | **PDF 两阶段上传 + 按页裁剪 + MinerU 云端解析** / 英文界面逐页 / 语言持久化复核（`walk7/8/9.mjs`） |
| 直连取证 | 原始 SSE 帧、日报/周报接口、SQLite 只读统计、理解/出题质量对照 ground truth |

**截图 80+ 张**在 `_agenttmp/uxwalk/shots1..shots9/`，结构化观测在同目录 `walk*.json`。

> 走查期间**修正过三次自己的误判**，都记在正文里：① 语言切换「没生效」实为我给每个页面
> 注入了强制 `zh-CN` 的 init script；② 清洗「全局空转」实为**只对 Markdown 输入空转**，
> 对 PDF 会去页眉页脚；③ **「本地 MinerU / ASR 模型不存在」是错的** ——
> 我只查了 `backend/data/models/`，漏了 ModelScope 缓存（附录 C）。
> **测量口径本身也会错**，所以三次都是重测后才写结论。

---

## 1. 先说三条最重要的（跨页面的 P0）

它们各自只出现在少数页面，但**影响整个产品的核心价值**，所以单独提出来：

| 问题 | 出现在 | 详见 |
|---|---|---|
| 智能问答答案重复 66 次，整段不可读 | `/qa`、笔记详情的浮层提问 | §3.12 |
| 「今日学习报告」日期永远是昨天；7 天趋势最后一根柱子恒为 0 | `/`、`/today` | §3.3 |
| 自动出题题型/难度完全单调（9 道题全是「简单选择题」） | `/questions`、`/review`、`/today` | §2.3 |

---

## 2. 全站级问题（不属于任何单一页面）

### 2.1 🟡 `error_code` 被前置进用户可见文案

`frontend/src/api/client.ts:518` 把错误拼成 `` `${code}: ${detail}` ``，
于是 toast 与页内错误块会原样显示机器码：

```
NOTE_EXPORT_EMPTY: 笔记没有可导出的正文
```

而 `api/client.ts:428-437` 自己写着「按 `error_code` 分流，不要匹配文案」——
`error_code` 的用途是**分流与日志**，不是给人看的。

### 2.2 🟡 面向开发者的话术出现在用户界面

| 文案 | 出处 | 问题 |
|---|---|---|
| 「暂无建议关系，可点击下方按钮**基于嵌入向量**挖掘新的潜在关联」 | `messages.zh-CN.ts:1151` | 暴露 embedding |
| 「已降级为关键词检索（**向量服务不可用**）」 | `messages.zh-CN.ts:1457` | 暴露内部服务 |
| 「**后端应改为直接返回数组**」 | `messages.zh-CN.ts:1034` | 给后端的建议展示给用户 |
| 「`GET /graph/suggestions` 的响应结构与约定不符」 | `messages.zh-CN.ts:1033` + `pages/contractDrift.ts:43` | 用户看到**接口路径** |
| 「扫描导入失败，请确认**后端服务**可用后重试」 | `messages.zh-CN.ts:1341` | 部署概念 |
| 「使用**本地模型**解析（需要本地环境支持）」 | `messages.zh-CN.ts:583` | 实现词 |

### 2.3 🟠 自动出题「题型与难度完全单调」

这一条**有 ground truth**：我上传的那份 Markdown 样本是**我自己写的**，
所以能逐条判断 AI 理解与出题的质量。原文含 9 个小节、1 个公式、1 处刻意重复段落。

**理解（卡片）产出 14 张，总体不错**：

| 判据 | 结果 |
|---|---|
| 公式是否被识别成 formula 卡片 | ✅ **是**，`[formula] φ的计算公式` 正文就是原始 LaTeX |
| 我刻意重复两次的段落产生几张卡片 | ✅ **1 张**（理想值）—— 卡片层有 `content_hash` 去重 |
| 元数据型卡片（提出机构 / 主要起草人） | ⚠️ **2 张**（全库该模式占 2.6%，89/3469） |
| 自指型空卡片 | ⚠️ 1 张：`[concept] 空压机运行超时` =「空压机运行超时是 8.4.1 现象章节列出的异常现象之一。」 |

**出题产出 9 道，问题明显**：

```
[choice/easy] × 9        ← 9 道题，全部是「选择题 + 简单」
```

**没有任何 `fill_blank` / `short_answer`，也没有 `medium` / `hard`。**
这意味着复习永远是「四选一、一眼就会」，既不练回忆（填空/简答），
也没有难度爬升 —— 而 FSRS 的调度质量恰恰依赖评分分布。

顺带一条「垃圾进垃圾出」的实例：我原文里那句自指的
「空压机运行超时是 8.4.1 现象章节列出的异常现象之一」被出了道
「空压机运行超时是哪一章列出的异常现象？」，答案是「8.4.1现象章节」——
**考的是文档的目录结构，不是知识**。

**建议**：出题 prompt 里显式要求题型与难度配比（例如 choice:fill_blank:short_answer
≈ 5:3:2、easy:medium:hard ≈ 4:4:2），并在卡片生成侧过滤
「X 是本章列出的…之一」「本标准由…提出」这类**元数据句式**。

### 2.4 🟡 `ErrorBoundary` 把 React 组件栈渲染给用户

`frontend/src/components/ErrorBoundary.tsx:95-97` 直接渲染
`error.message` + `errorInfo.componentStack`；同文件 `:80-81` 的文案还明说「技术细节不翻译」。
生产环境下用户会看到一整段组件栈。

### 2.5 🟡 加载 / 空 / 错误态有 7 处「自成一派」

主流是共享组件（`LoadingSpinner` / `EmptyState` / `ErrorDisplay`），但以下 7 处绕开了它们：

| 位置 | 现状 | 证据 |
|---|---|---|
| `/review` 加载 | **一行纯文本**，无 spinner | `pages/Review.tsx:220-224` |
| `/review` 错误 | 内联 `<p>` + 按钮 | `pages/Review.tsx:280-289` |
| `/projects` 加载 | 手写 `.state-container` + `.spinner` | `pages/Projects.tsx:78-82` |
| `/projects` 空 | 手写副本 | `pages/Projects.tsx:83-94` |
| `/projects` 错误 | 全站唯一「可关闭的错误横幅」 | `pages/Projects.tsx:62`、`projects/ProjectsErrorBanner.tsx:27` |
| `/assessment` 错误 | 内联 `.card` + 拼接原始异常 | `pages/LearningAssessment.tsx:209,396-408` |
| `/cards/:id` 错误 | `ErrorDisplay` **没传 `onRetry`** | `pages/CardDetail.tsx:122-125` |

### 2.6 🟡 上传页不在侧边栏导航里

`components/Sidebar.tsx:54-87` 的 13 项导航**没有 `/upload`**。
入口只有：仪表盘主按钮（`Dashboard.tsx:168`）、「笔记列表」旁那个小 ↑ 图标（`Sidebar.tsx:194`）、
笔记列表空态（`NotesList.tsx:261`）。新用户很容易以为没有上传页。

### 2.7 🟠 新导入的内容不会自动补向量，界面上完全看不出来

DB 实测：`chunks` 832 行里 **224 行没有向量**（`embedding_model = None`），
其中**我这次上传的 PDF 那 12 行全部没有**；`knowledge_cards` 新加的 14 行同样没有。
`/ready` 会报 `pending_embeddings`，但那是给运维看的探针。

这不是 bug，是**已知的设计取舍**（代码注释写得很清楚）：

- `services/chunk_service.py:23-24`：「不能放在清洗流程里同步做）。
  `has_embedding=False` 的行需由 `scripts/embed_chunks.py` 补嵌入。」
- `main.py:310`：「…`scripts/embed_chunks.py`，而 **Beat 调度表里没有这个任务**」

**问题在用户侧**：刚导入的资料会**静默地不在向量通道里**，直到有人手工跑脚本，
而界面上没有任何地方提示这一点。建议给补嵌入加一条 Beat 任务（或导入后自动排队），
并在笔记详情页对「无向量的笔记」给一个可见标记。

### 2.8 🟠 嵌入模型会因可用内存**静默降级**，用户无从得知

`config.py:223,226,229` 三个默认值决定了这件事：

```python
embedding_model: str = "BAAI/bge-m3"
embedding_model_fallback: str = "BAAI/bge-small-zh-v1.5"
embedding_min_free_memory_gb: float = 4.0
```

可用内存低于 4.0 GB 时，`embedding_service.py:71-82` 直接换成降级模型，
**只在日志里留一行 WARNING**，界面上完全无感：

```
WARNING | 可用内存不足（1.8GB < 4.0GB），跳过主模型 BAAI/bge-m3，改用降级模型 BAAI/bge-small-zh-v1.5
WARNING | 嵌入模型已降级加载: BAAI/bge-small-zh-v1.5（配置主模型为 BAAI/bge-m3）
```

清洗照样显示「完成」，检索质量却已经不同了。两个模型的向量维度不同
（bge-m3 = 1024、bge-small-zh = 512）。

> **诚实说明**：本次可用内存只有 1.8 GB，部分是我自己造成的（同机还跑着
> uvicorn + worker + Vite + Playwright）。但这说明**阈值 4 GB 在开发机上很容易触发**，
> 而触发后没有任何用户可见提示。**「不同模型向量混写」我只标为潜在风险，未观测到** ——
> 实测新内容的 `embedding_model` 是 `None`（根本没写），所以没混。

### 2.9 🟡 掌握度几乎全是 0，导致「薄弱点」整列不可读

3469 张卡片里 **3330 张（96%）落在 0–20 档**，于是仪表盘与今日学习页的
「薄弱点」列表每一行都是「错 1 次 \| 0%」，看不出差别。

### 2.10 🟡 英文界面混用全角冒号

题库页在英文下显示 `Type：` / `Difficulty：`（应为 `Type:` / `Difficulty:`）。
`messages.en.ts` 是从中文目录结构照搬的，标点没跟着换。证据：`walk9.json`。

### 2.11 🟡 移动端首屏被提醒横幅占掉近一半

375×667 下两条提醒横幅（桌面通知 + 邮件提醒）占掉首屏约一半高度，
「欢迎使用 EngramNote」被推到折线以下。截图 `shots4/10-mobile-dashboard.png`。

---

## 3. 逐页问题

### 3.1 登录 `/login`

截图：`shots1/A1-login.png`、`shots1/A4-login-wrong-password.png`

| 级别 | 发现 |
|---|---|
| ✅ | 表单简单清晰，只有邮箱 + 密码 + 登录 + 去注册 |
| ✅ | 密码错误时用 `role="alert"` 内联提示（`pages/Login.tsx:100`），不是弹窗 |
| 🟡 | **未登录访问任何未知路径都渲染登录页**（`App.tsx:188`），而不是 404 —— 实测 `/definitely-not-a-page` 得到的是「登录 EngramNote」。这是**有意设计**，但意味着未登录用户永远看不到 404 页 |

### 3.2 注册 `/register`

截图：`shots1/A2-register.png`

| 级别 | 发现 |
|---|---|
| ✅ | 字段与校验完整（用户名限 `[a-zA-Z0-9]+`，`pages/Register.tsx:104`） |
| — | **本次未跑完整注册流程**（我复用了已有账号，避免往隔离副本外扩散数据）。注册链路的端到端验证见 `frontend/e2e/e2e-full.spec.ts:221` 的既有用例 |

### 3.3 仪表盘 `/` ⭐ 问题最集中的页面

截图：`shots1/B01-dashboard.png`、`shots3/15-dashboard-with-goal.png`

| 级别 | 发现 | 证据 |
|---|---|---|
| 🔴 | **「今日学习报告」日期永远是昨天** —— 今天 2026-10-21，页面写 `(2026-10-20)`；而**数据是今天的**（`total_reviews: 1` 正是我当天做的那次）。对 UTC+8 用户 100% 复现 | `report_service.py:56-57` + `timeutil.py:53-55` |
| 🔴 | **7 天趋势最后一根柱子恒为 0** —— 我当天的复习被记到 `2026-10-20` 那根，`2026-10-21` 显示 0。整张图**左移一格** | `report_service.py:174-175` |
| 🟠 | **「没有学习目标」被实现成 HTTP 400**，每次进页面请求 **2 次**（React StrictMode），留下 **2 条控制台红字** | 实测 `walk1.json` B01 |
| 🟠 | 前端靠 `message.includes('active goals')` **匹配英文文案**判断这个状态，与「按 error_code 分流」的约定相反；后端一旦本地化这条消息就会误判成真错误 | `pages/Dashboard.tsx:107` |
| 🟡 | **两条提醒横幅占掉首屏**（桌面通知 + 邮件提醒），「欢迎使用」被推到 300px 以下 | 截图 |
| 🟡 | 「今日学习目标 / 还没有学习目标，**点击创建**」——文案说可点，但需要确认整卡是否真的可点（我只验证了 `/goals` 里的「新建目标」按钮可用） | `Dashboard.tsx:225` |
| 🟡 | 部分接口失败会**顶掉已取到的「最近笔记」**：`failedCount > 0` 就 `setError(...)`，最近笔记区把它当整块失败渲染 | `Dashboard.tsx:121-123` → `:619-620` |
| ✅ | **建了目标之后控制台错误数降到 0**，确认 400 完全由「无目标」触发 | `walk4.json` 末尾 |

### 3.4 笔记列表 `/notes`

截图：`shots4/09-notes-list.png`、`shots2/01-notes-search.png`

| 级别 | 发现 | 证据 |
|---|---|---|
| 🟡 | **搜索无防抖**：逐字输入 5 个字符，打出 **3 次**列表请求 | `walk2.json` 第 1 项；`NotesList.tsx:58,84,93` |
| 🟡 | 每行一个「删除」按钮（一屏 15 个），视觉噪音偏大 | 截图 |
| ✅ | 搜索框提示语诚实写明**「搜索笔记标题...」**——我实测搜「空压机」返回 0 条，而卡片里明明有大量空压机内容；因为确实是**只搜标题**，不是 bug | `walk2.json` 第 1 项 |
| ✅ | 角色 Tab（学习资料 / 我的笔记）与「全部 / 已审阅」筛选可用，总数正确（「共 24 条」= 23 篇 archived + 1 篇 cleaned 的资料笔记） | 实测 |

### 3.5 笔记详情 `/notes/:noteId` ⭐ 核心阅读页

截图：`shots1/B03-note-detail.png`、`shots2/04-note-scroll-memory.png`

| 级别 | 发现 | 证据 |
|---|---|---|
| 🟡 | **页面有两个 `<h1>`**：页面标题 + 正文首个标题 | 实测 `walk1.json` B03 |
| 🟡 | **标题把两个标题拼在一起**：「拉哇水电站运行技术标准**气系统运行技术标准**」（中间无分隔）。同库其它笔记是同一形态，说明是标题生成规则的问题，不是个例 | DB 实测 |
| 🟡 | 「开启 ADHD Reader」按钮独占一整行，位置在正文上方，与阅读动作的关系不明显 | 截图 |
| 🟡 | **选中提问浮层疑似共用 §3.12 的重复 bug** —— `NoteAskPanel.tsx:196` 与 `QA.tsx:250` 用同一个 hook、同一种写法。⚠️ **这是代码层推断，不是观测**：我两次尝试都没能定位到浮层的输入框（它是可拖拽浮窗） | `NoteAskPanel.tsx:80,196` |
| ✅ | **阅读位置记忆精确恢复**：滚到 3000px → 切到仪表盘 → 回来，仍在 **3000px**（`使用时的想法.txt` 第 1 条已修好） | `walk2.json` 2c |
| ✅ | **三视图可用**：原始版 / 清洗版 / 对比视图；对比视图渲染出 115 个 diff 元素 | `walk2.json` 2a/2b |
| ✅ | **导出完全达标**：文件名正确，内容是干净 Markdown + 规范 YAML front-matter（`title/source_type/status/variant/page_count/created_at/updated_at/exported_at`），496 行 | `walk2.json` 第 3 项 |
| ✅ | 版本历史、学习评估、AI 预处理、审阅、删除、返回等操作齐备 | 截图 |

### 3.6 上传 `/upload`

截图：`shots1/B04-upload.png`、`shots8/04-pdf-settings.png`

| 级别 | 发现 | 证据 |
|---|---|---|
| 🟡 | **不在侧边栏**（见 §2.6） | `Sidebar.tsx:54-87` |
| 🟡 | **界面上看不到体积上限**（后端限 500MB / 每用户 5000MB / PDF ≤2000 页），前端也没有预检 —— 用户要等上传失败才知道 | 前端全仓 `file.size` **0 命中** |
| 🟡 | 「本地解析（需要本地环境支持）」等描述含实现词（见 §2.2） | `messages.zh-CN.ts:583` |
| ✅ | **PDF 两阶段上传 + 按页裁剪实测通过**：22 页真实 PDF 裁成 `1-4`，笔记页数正确记为 **4 页**；非法页码（`99-1`）被**内联校验**拦住，不会打后端 | `walk8.json` B |
| ✅ | MinerU 解析产出带 `<!-- page=N -->` 页码锚点的干净 Markdown，标题层级（`# 主变压器…` / `## 1 范围`）与表格公式保留完好 | 同上 |
| 🟡 | **走的是云端 API，本地 MinerU 模型闲置**：`config.py:179` 默认 `mineru_backend = "vlm-http-client"`，`.env` 没覆盖它，于是磁盘上约 **4.6 GB** 的本地 MinerU 模型（`PDF-Extract-Kit-1.0` 2.4 GB + 两个 `MinerU2.5` VLM 各 2.2 GB）从未被调用，每次解析都在消耗云端额度。worker 日志为证：`转换开始: input.pdf \| 后端=vlm-http-client` | 日志第 47 行；`config.py:179` |
| 🟡 | **上传后新内容没有向量**（见 §2.7）：该 PDF 的 12 个 chunk 索引重建成功，但 `embedding_model = None` | 日志 73-74 行；DB 实测 |
| ✅ | 信息架构清晰：拖拽区 → 上传设置 → 解析方式 → 项目标签 → 笔记类型，无需说明书 | 截图 |

### 3.7 回收站 `/trash`

截图：`shots4/03-trash.png`、`shots5/01-after-delete.png`

| 级别 | 发现 | 证据 |
|---|---|---|
| 🟡 | **清空回收站成功时没有反馈**（只在 `failed > 0` 时 toast） | `pages/Trash.tsx:121-123` |
| 🟡 | **恢复成功时也没有反馈**（只在重名改名时给 `toast.info`） | `pages/Trash.tsx:89-91` |
| ✅ | **删除确认框做得好**：标题「移入回收站」+ 三块统计（0 知识卡片 / 0 核心卡片 / 0 双向链接）+ 一段后果说明（「卡片、题目、复习记录将作为整体保存」） | 截图 `shots4/01-delete-dialog.png` |
| ✅ | **删除 → 回收站 → 恢复全链路可用**：回收站显示「删除于 2026/10/21 GMT+8 5:25:57」与包含内容计数；恢复后接口确认 `trashed_at: null`、`status: cleaned`，回收站回到空态 | `walk5.json` 第 1 项 |

### 3.8 知识卡片 `/cards`

截图：`shots1/B06-cards.png`、`shots4/07-cards-truncation.png`

| 级别 | 发现 | 证据 |
|---|---|---|
| 🟠 | **静默截断**：页面标题写「共 **3469** 张卡片」，实际只渲染 **999** 张，**没有分页器、没有「加载更多」、没有任何提示**，页尾直接结束。后 2470 张在任何界面都看不到、也搜不到 | `pages/KnowledgeCards.tsx:109`（`pageSize=999`）；`api/understanding.py:321,362`（默认 999 / 上限 9999） |
| 🟡 | **「盲点 / 拓展」两个筛选恒为空**：该用户 3469 张卡片 `card_category` **全部是 `regular`** | DB 实测 |
| 🟡 | 一页 **1033 个按钮**（`walk1.json` B06），DOM 偏重 | 实测 |
| ✅ | 按笔记分组的折叠手风琴、分类 pill、搜索都在；卡片色与徽章一致性好 | 截图 |

### 3.9 卡片详情 `/cards/:cardId`

截图：`shots1/B07-card-detail.png`

| 级别 | 发现 | 证据 |
|---|---|---|
| 🟡 | **错误态没有重试按钮**（`ErrorDisplay` 未传 `onRetry`） | `pages/CardDetail.tsx:122-125` |
| 🟡 | 页面内容较薄（实测 `main` 仅 226 字），掌握度环 + 关联题目的信息密度偏低 | `walk1.json` B07 |

### 3.10 知识图谱 `/graph` ⭐ 在真实规模下不可用

截图：`shots1/B08-graph.png`、`shots5/04-graph-generate.png`

| 级别 | 发现 | 证据 |
|---|---|---|
| 🟠 | **87.3% 的节点是孤立点**：3469 节点 / 604 已确认边 / **3028 孤立**。关系条数分布：3028 张卡片 0 条，180 张 1 条，74 张 2 条…… | DB 实测 |
| 🟠 | **画布不可读**：3469 个节点被力导向铺成一个**实心圆盘**，节点文字重叠成灰色噪点，缩略图里只有一个点 | 截图 |
| 🟡 | **同屏两个「边」数不一致**：标题「3469 节点 · **879** 边 · 275 待审」，统计卡「**604** 边」。前者含待审建议，后者只含已确认，但都叫「边」 | 截图 |
| 🟡 | 空态文案暴露实现（「基于**嵌入向量**挖掘」，见 §2.2） | `messages.zh-CN.ts:1151` |
| 🟡 | **搜索结果项是 `div[onClick]`**，无 `role` / `tabIndex`，键盘到不了（同页节点选择已有键盘通道，这里没有） | `knowledgegraph/GraphToolbar.tsx:91-95` |
| ✅ | **「生成相关建议」功能本身是好的**：实测产出 **275 条建议**（边 604 → 879），带相似度分数（0.81–0.97）与逐条「确认 / 拒绝」+ 全选 + 批量操作 | `walk5.json` 第 2 项 |
| ✅ | 图例清楚（相关/前置/后续/对比/待审建议），键盘导航 + `aria-live` 播报齐备 | `pages/KnowledgeGraph.tsx:179-183,286` |

> **建议**：① 默认只画有关系的连通分量，孤立节点折叠成「N 个未关联卡片」入口；
> ② 加按笔记 / 项目 / 章节的筛选（现在只有「全部类型」）；③ 节点超阈值时先聚类或分页。

### 3.11 问题集 `/questions`

截图：`shots1/B09-questions.png`

| 级别 | 发现 | 证据 |
|---|---|---|
| 🟠 | **一页渲染 161 766 字 / 3441 个按钮** —— 与 §3.8 同源，列表没有真正的分页或虚拟滚动 | `walk1.json` B09 |
| 🟡 | 英文界面下全角冒号 `Type：` / `Difficulty：`（见 §2.9） | `walk9.json` |
| 🟡 | 英文界面下首屏 3.2 秒仍显示「0 questions / Loading…」，而同页在中文走查时能渲染出 3441 个按钮 —— 首屏反馈不足（**低置信度**，未排除只是慢） | `walk9.json` |
| ✅ | 真 `<table>`、列显示开关、列头排序、筛选 chip 用 `<button aria-pressed>`，语义正确 | 截图 + 前端盘点 |

### 3.12 智能问答 `/qa` ⭐ 最严重的一页

截图：`shots3/18-qa-duplication.png`

| 级别 | 发现 | 证据 |
|---|---|---|
| 🔴 | **答案重复 66 次，整段不可读** | 见下 |
| 🟡 | 输入框 placeholder 被截断（「输入你的问题，AI 将基于你的笔」），宽度不够 | 截图 |
| ✅ | **引用来源侧栏是问答页最亮的部分**：5 条来源带「笔记 › 章节路径」，可点击跳转 | 截图 |

**用户看到什么**：

```
储储气储气罐储气罐压力储气罐压力过高时的处理方法储气罐压力过高时的处理方法如下：
1.储气罐压力过高时的处理方法如下：
1.**储气罐压力过高时的处理方法如下：
1.**现场储气罐压力过高时的处理方法如下：
1.**现场核实储气罐压力过高时的处理方法如下：
```

**实测数据**：

| 指标 | 值 |
|---|---|
| 页面上首句出现次数 | **66 次** |
| DOM 文本长度 | 14 237 字 |
| 后端原始 SSE 字节数 | 10 208 字节 |
| 后端 273 个 `token` 帧拼起来 | **469 字**（干净、完整、可读） |

**根因（已定位到行）**：后端发的是**正确的增量**（每帧 1~2 个字符，已用直连 SSE 逐帧验证）。
前端 `useThrottledStream` 把增量累积进 `bufferRef`，提交时**提交了整个缓冲区却不清空**：

```ts
// frontend/src/hooks/useStreamAnswer.ts:22-32
const push = useCallback((chunk, commit) => {
  bufferRef.current += chunk;
  if (timerRef.current !== null) return;
  timerRef.current = setTimeout(() => {
    timerRef.current = null;
    if (bufferRef.current) commit(bufferRef.current);   // ← 提交后没有 bufferRef.current = ''
  }, flushIntervalMs);
}, [flushIntervalMs]);

// frontend/src/hooks/useStreamAnswer.ts:35-41
const flush = useCallback((commit) => {
  ...
  if (bufferRef.current) commit(bufferRef.current);       // ← 同样没有清空
}, []);
```

而调用方把回调参数当作**本批新增文本**（契约见 `useStreamAnswer.ts:62` 的注释
「每批新增文本（已节流）」）直接**追加**到答案上：

```ts
// frontend/src/pages/QA.tsx:250-251
pushDelta(String(payload.content || ''), (delta) => {
  setHistory((prev) => { ... 把 delta 追加到当前回答 ... });
});
```

于是第 k 次提交追加的是前 k 次的总和 —— 典型的前缀阶梯。
`reset()`（`:44-50`）**确实**会清空缓冲，但它只在「开始新一轮回答」时调用，
所以清空永远赶不上提交。

**影响面（同一 hook、同一写法）**：`pages/QA.tsx:142,250`（整页）
与 `components/NoteAskPanel.tsx:80,196`（选中提问浮层）。

**修复**：两处 commit 之后各补一行清空，即
`commit(bufferRef.current); bufferRef.current = '';`。

**为什么没被测试拦住**：`frontend/src/hooks/useStreamAnswer.ts` **没有对应的测试文件**
（同目录 `useThrottledStream` 是唯一一个没有 `.test` 的 hook）。
文件头详细论证了「为什么要节流」（O(n²) 渲染），但没有任何用例断言
「连续 push 两次、commit 收到的是两段增量而不是累加」。
建议补：push('a') → 等一拍 → push('b') → flush，断言两次 commit 分别为 `'a'` 与 `'b'`。

### 3.13 答题复习 `/review`

截图：`shots1/B11-review.png`、`shots2/07-review-graded.png`、`shots3/17-review-jump-back-1.png`

| 级别 | 发现 | 证据 |
|---|---|---|
| 🟠 | **队列封顶 50 却不告知**：DB 里已到期 **206** 条，`/api/review/due` 只回 **50** 条，界面显示「1 / 50」。用户会以为一共 50 道 | `review_service.py:71`（`limit: int = 50`） |
| 🟠 | **积压追不上**：最早到期时间 **2026-06-24**（约 4 个月前），而每日上限只有 **10** 道 | DB 实测 + `config.py:440` |
| 🟡 | **加载态是一行裸文本**，无 spinner、无 `.state-container` | `pages/Review.tsx:220-224` |
| 🟡 | 题目里的公式以 `sqrt(1 + (A1/A2)²) - A1/A2` 这种**纯文本**呈现，没有走 KaTeX 排版（同页 Markdown 渲染能力是有的） | 截图 `shots1/B11-review.png` |
| ✅ | **题号条可自由前后跳题**（`使用时的想法.txt` 第 8 条已修好）：实测 `1 / 50` → 点「跳到第 3 题」→ `3 / 50` → 点「跳到第 1 题」→ `1 / 50`；答过的题带 ✗ 标记 | `walk3.json` 第 7 项 |
| ✅ | 「今日还可结算 10 道（上限 10）」额度条清楚；提交按钮在未选择时置灰 | 截图 |
| ✅ | 「上一题」不受自评约束是刻意设计，细节考虑到位 | `QuizAnswerCard.tsx:626-628` |

### 3.14 卡片复习 `/review/cards`

截图：`shots1/B12-review-cards.png`

| 级别 | 发现 |
|---|---|
| 🟡 | 该用户无到期卡片，页面只有空态（`main` 144 字）。**空态本身正常**，但「卡片复习」与「答题复习」两条并行路径的区别在界面上没有一句解释 |
| ✅ | 空态给了「去知识卡片」的出口 |

### 3.15 快速复习 `/review/quick/:noteId`

截图：`shots1/B13-review-quick.png`

| 级别 | 发现 |
|---|---|
| 🟡 | 一页 **173 个按钮**（题号条 + 选项），DOM 偏重 |
| ✅ | 复用 `QuizAnswerCard` + `QuizNavigator`，与答题复习体验一致；「再来一轮」「返回笔记」出口清楚 |

### 3.16 今日学习 `/today`

截图：`shots2/15-today.png`

| 级别 | 发现 | 证据 |
|---|---|---|
| 🔴 | **「今日报告 (2026-10-20)」日期错位**（与 §3.3 同一个根因） | `report_service.py:56-57` |
| 🟠 | **`/api/goals/daily-plan` 400 ×2 + 2 条控制台红字**（与 §3.3 同源） | 实测 `walk1.json` B14 |
| 🟡 | **优先级徽章在未知优先级下渲染为空**：兜底对象给的是 `label`，读取却用 `prio.labelKey`，于是徽章只剩底色没有文字（`task.priority ∉ {1,2,3}` 必现）。⚠️ 这条来自**静态审查**，我未构造出该数据形态直接观测 | `pages/TodayLearn.tsx:664-667` vs `:697`；`i18n/translate.ts:87` |
| 🟡 | 「薄弱点」整列「错 N 次 \| 0%」（见 §2.8） | 截图 |
| ✅ | 「今日待复习 / 今日已完成 / 每日目标 10 题」三处口径一致，都取自同一个 `daily_limit` | `TodayLearn.tsx:244,468,480` |

### 3.17 今日资料 `/daily`

截图：`shots1/B15-daily.png`

| 级别 | 发现 | 证据 |
|---|---|---|
| 🟡 | **对 28 篇笔记的用户是一个纯空壳**：整页只有「还没有文件夹 / 创建一个文件夹来组织今天的学习资料」+ 一个按钮（`main` 仅 37 字） | 实测 `walk1.json` B15 |
| 🟡 | 「文件夹」这个概念与侧边栏那个 ↑ 上传入口**没有互相指引**，用户不知道文件夹里的资料从哪来 | 截图 |
| ✅ | 空态文案与按钮明确，不是死胡同 | 截图 |

### 3.18 项目 `/projects`

截图：`shots1/B16-projects.png`

| 级别 | 发现 | 证据 |
|---|---|---|
| 🟡 | 加载/空态是手写副本，错误用全站唯一的可关闭横幅（见 §2.5） | `pages/Projects.tsx:62,78-94` |
| ✅ | 「项目就是笔记上的标签，一篇笔记可归属多个项目」这句说明写得好，概念一次讲清 | 截图 |
| ✅ | 新建 / 重命名 / 删除 / 扫描导入 / 添加笔记 / 移出笔记 都齐备 | 前端盘点 |

### 3.19 学习评估 `/assessment`

截图：`shots1/B17-assessment.png`

| 级别 | 发现 | 证据 |
|---|---|---|
| 🟡 | 错误提示**拼接原始异常**：`` `${t('assessment.assessFailed')}: ${e.message}` `` | `pages/LearningAssessment.tsx:209,399` |
| 🟡 | 页面内容较薄（`main` 158 字），模式切换（对比 / 出题）的差异缺少一句说明 | `walk1.json` B17 |

### 3.20 学习目标 `/goals`

截图：`shots1/B18-goals.png`、`shots3/13-goal-filled.png`

| 级别 | 发现 | 证据 |
|---|---|---|
| 🟡 | **空态无引导**：对已有 28 篇笔记的用户只显示「还没有学习目标」+ 按钮（`main` 39 字），没提「建了目标之后仪表盘会出现每日推荐任务」这个实际收益 | 实测 `walk1.json` B18 |
| ✅ | **创建流程可用**：弹窗字段清晰（目标名称 / 每日或每周 / 目标掌握度 % / 截止日期可选），创建后立刻出现在「进行中的目标」并显示进度条 | `walk3.json` 第 6 项 |
| ✅ | 创建目标**顺带消除了全站的 400 与控制台红字**（见 §3.3） | `walk4.json` |

### 3.21 404 与其他

| 页面 | 级别 | 发现 | 证据 |
|---|---|---|---|
| 404 `/no-such-page` | ✅ | 真 404（`<h1>404</h1>` + 返回首页链接），不是静默重定向 | `shots1/B19-notfound.png`、`pages/NotFound.tsx:30-47` |
| `/styleguide` | ✅ | 仅 DEV 注册（`App.tsx:64` 的 `import.meta.env.DEV` 守卫），生产被 tree-shake；走查时可用 | `shots1/B20-styleguide.png` |
| 英文界面（全站） | ✅ | 侧边栏切 English 后 `localStorage` 写入 `en` 并**跨整页刷新保持**；逐页实测 12 个页面**界面文案全部英文**，残留中文只有笔记标题与卡片正文这类用户数据。`messages.en.ts` 1098 键与中文目录一致，`i18n.test.tsx:368` 有键形状断言 | `walk9.json` |
| 移动端 375×667 | ✅ | 仪表盘正常渲染、汉堡菜单可点、图谱页可用；唯一问题是首屏横幅占位（§2.10） | `shots4/10-mobile-dashboard.png` |

---

## 4. 严重度总表（排优先级用）

| 级别 | 页面 | 问题 | 证据 |
|---|---|---|---|
| 🔴 | `/qa` | 答案重复 66 次，整段不可读 | `useStreamAnswer.ts:28,40` |
| 🔴 | `/`、`/today` | 日报日期永远是昨天；7 天趋势最后一根恒 0 | `report_service.py:57,175` |
| 🟠 | `/questions`、`/review`、`/today` | 出题题型/难度完全单调（9/9 都是简单选择题） | §2.3 |
| 🟠 | `/cards` | 声称 3469 张，只渲染 999 张，无分页无提示 | `KnowledgeCards.tsx:109` |
| 🟠 | `/graph` | 87.3% 孤立节点，画布成毛球不可读 | 截图 + DB |
| 🟠 | `/upload`、`/notes/:id` | 清洗对 Markdown 输入逐字节空转；用户点名的三种写法一条没做 | `cleaning_service.py:69`、`config.py:245` |
| 🟠 | `/`、`/today` | 「无学习目标」= HTTP 400 ×2 + 控制台红字；靠匹配英文文案判断 | `Dashboard.tsx:107` |
| 🟠 | `/review` | 到期 206 条但队列封顶 50 且不告知；每日上限 10 追不上 | `review_service.py:71` |
| 🟠 | `/questions` | 一页 161 766 字 / 3441 按钮 | `walk1.json` B09 |
| 🟡 | 全站 | 开发者话术出现在界面（嵌入向量 / 向量服务 / 后端应改为…） | `messages.zh-CN.ts:1033-1035,1151,1457` |
| 🟡 | 全站 | `error_code` 前置进用户文案 | `client.ts:518` |
| 🟡 | 全站 | 上传页不在侧边栏 | `Sidebar.tsx:54-87` |
| 🟡 | 全站 | `ErrorBoundary` 渲染 React 组件栈 | `ErrorBoundary.tsx:95-97` |
| 🟡 | 全站 | 加载/空/错误态 7 处自成一派 | §2.5 |
| 🟠 | 全站 | 新导入内容不自动补向量，界面无提示（224/832 chunk 无向量） | `chunk_service.py:23-24`、`main.py:310` |
| 🟠 | 全站 | 嵌入模型因可用内存静默降级（bge-m3 → bge-small-zh），仅日志 WARNING | `config.py:229`、`embedding_service.py:71-82` |
| 🟡 | `/upload` | 约 4.6 GB 本地 MinerU 模型闲置，PDF 实际走云端 | `config.py:179`；worker 日志 |
| 🟡 | 全站 | 掌握度 96% 落在 0–20 档，「薄弱点」不可读 | DB |
| 🟡 | `/notes/:id` | 两个 `<h1>`；标题把两个标题拼在一起 | `walk1.json` B03 |
| 🟡 | `/graph` | 两个「边」数不一致（879 vs 604）；搜索项键盘不可达 | 截图；`GraphToolbar.tsx:91-95` |
| 🟡 | `/notes` | 搜索无防抖（5 字符 3 请求） | `NotesList.tsx:58,84,93` |
| 🟡 | `/review` | 加载态裸文本；公式未走 KaTeX | `Review.tsx:220-224` |
| 🟡 | `/trash` | 清空/恢复成功无反馈 | `Trash.tsx:89-91,121-123` |
| 🟡 | `/daily`、`/goals` | 空态无引导，概念与入口不互指 | 截图 |
| 🟡 | `/assessment` | 错误拼接原始异常 | `LearningAssessment.tsx:209,399` |
| 🟡 | `/cards/:id` | 错误态无重试 | `CardDetail.tsx:122-125` |
| 🟡 | `/questions` | 英文界面全角冒号 | `walk9.json` |
| 🟡 | 移动端 | 首屏被提醒横幅占近一半 | 截图 |

---

## 5. 用户自己那 8 条想法的逐条验收

`使用时的想法.txt`（2026-10-20）是本次走查的起点。逐条实测结论：

| # | 用户原话 | 实测结论 | 证据 |
|---|---|---|---|
| 1 | 笔记页面刷新后不知道看到哪了 | ✅ **已修好**。滚到 3000px → 切走 → 回来，仍在 **3000px** | `walk2.json` 2c；`hooks/useScrollMemory.ts` |
| 2 | 想重新设计前端风格 | ✅ 已完成（`visual-refactor-plan` 26 批全 ✅）。本次走查视觉一致性良好 | 各页截图 |
| 3 | 替换 opencode 密钥 | ⚠️ 运维动作，非体验问题。**但本次发现相关配置陷阱**：`backend/.env` 里**没有** `APP_ENV` 也**没有** `DEEPSEEK_API_KEY`，于是 `llm_provider` 默认 `auto` + `app_env` 默认 `prod` → **解析成 deepseek 且密钥为空**（`config.py:756-772`），而真正配好的是 GLM。建议显式写 `LLM_PROVIDER=glm` | `config.py:574,756-772` |
| 4 | 导出功能 | ✅ **完全达标**。见 §3.5 | `walk2.json` 第 3 项 |
| 5 | 清洗策略（公式简化 / 标题层级 / 空格） | ❌ **一条都没做**，且对 Markdown 输入逐字节无变化 —— 见 §3.6 与下方说明 | `walk3.json` 第 2 项 |
| 6 | `GRAPH_SUGGESTION_FAILED` 提示看不懂 | ⚠️ **功能本身是好的，但首次使用会给开发者指令**。实测点「生成相关建议」成功产出 **275 条建议**；失败时的文案是「请先运行 `python backend/scripts/embed_cards.py` 补向量后重试」（`graph_service.py:391-392`）—— 这是给开发者的话 | `walk5.json` 第 2 项 |
| 7 | 日志里「chunk 索引重建失败…NULL identity key」 | ⚠️ 仍开放。日志说「**不影响清洗结果**」，但 chunk 索引失败意味着该笔记在 FTS/向量通道**没有语料** —— 后果被低估。实测确有 **212/820 个 chunk 无向量** | `clean_tasks.py:405-406`；DB 实测 |
| 8 | 刷题页面不能往前往后翻题 | ✅ **已修好**。见 §3.13 | `walk3.json` 第 7 项 |

**关于第 5 条的补充说明（清洗到底做了什么）**：
我造了一份刻意包含用户点名三种写法的 Markdown 样本，又用一份**真实 PDF**（22 页裁为 4 页）做对照：

| 检查项 | Markdown 样本 | 真实 PDF |
|---|---|---|
| 公式简化（`$6.34m \times 61.95m$` → `6.34m*61.95m`） | ❌ 原样保留 | ❌ 原样保留 |
| 标题层级严谨化（`## 1.1 …` → `###`） | ❌ 原样 | ❌ 原样（MinerU 给的层级本就合理） |
| 数字/字母前后空格 | ❌ 未见处理 | ❌ 未见处理 |
| 重复段落去重 | ❌ 原文 2 次 → **仍 2 次** | 无重复可测 |
| 去页眉页脚 | 0 块（样本没有页眉） | ✅ **去了 4 块** |
| 产物长度 | **711 → 711（逐字节等同）** | 5094 → 5031（**-63 字**） |

**结论要分两句话说**：① **清洗规则本身是有效的**（PDF 上确实剥掉了 4 处页眉页脚），
但对**已经是干净 Markdown 的输入**它逐字节不改 —— 而用户上传的 Markdown / 自己写的笔记
恰好都属于这一类，界面还显示「总分块: 2 / 重复块: 0」，看起来像「已经清洗过」。
② **用户点名的三件事确实一条都没做**，这与代码一致：清洗目前是**纯规则**
（`cleaning_service.py:69` 的 `clean_rules`），全文件 **LLM 相关 0 命中**；
全角标点映射在 `cleaning_service.py:49,157` 明确写着「暂不启用：可能破坏公式」。

**为什么段落级重复抓不到**：去重是**块级**的，块大小 `chunk_size = 500`（`config.py:245`），
切块用 `segment_with_offsets(text, 500)`（`clean_tasks.py:226`）。
711 字的文档只切出 **2 块**，所以「同一段落出现两次」在块的粒度上根本不可见。
建议要么把去重降到段落粒度，要么把文案改成「本次未检测到重复**块**（按 500 字分块）」。

---

## 6. 做得好的地方（建议保持，不要在这些地方动刀）

1. **笔记导出** —— 契约、文件名 sanitize、YAML front-matter 都规范，一次成功。
2. **回收站闭环** —— 删除确认框信息量足，删除 → 回收站 → 恢复全链路可用，
   恢复后接口确认 `trashed_at: null`、状态完好。
3. **阅读位置记忆** —— 精确到像素级恢复，且实现有 TTL 与条数上限。
4. **答题三页共用的 `QuizAnswerCard` + `QuizNavigator`** —— 题号条四态、答对/答错标记、
   「上一题不受自评约束」，细节考虑到位。
5. **RAG 的引用来源侧栏** —— 5 条来源带「笔记 › 章节路径」，可点击跳转
   （被 §3.12 的渲染 bug 拖累了，修完立刻可用）。
6. **上传页的信息架构** —— 四段清晰，无需说明书；PDF 裁剪有内联校验。
7. **PDF 全链路** —— 22 页裁成 4 页，页数记录正确；MinerU 产出带页码锚点的干净 Markdown。
8. **英文界面** —— 12 个页面全部翻译，键与中文目录 1:1（1098 键）且有断言守着。
9. **可访问性底子** —— `role="group"` + `aria-label`（题号条）、`aria-current`、
   `aria-live` 播报、Dialog 的焦点陷阱与归位，本次走查**没碰到一个键盘到不了的必需操作**。
10. **错误契约与日志** —— `{detail, error_code, request_id}` 三件套、`request_id` 全链路，
    排查时非常好用（本次定位 §3.3 就是靠接口实测 + 代码对照）。
11. **隔离机制本身** —— `_e2e_full_bootstrap` / `_e2e_full_runner` 让「跑一套真栈而不污染真库」
    变成一条命令，本次走查直接受益。

---

## 7. 建议的修复顺序

| 序 | 事项 | 改动面 | 收益 |
|---|---|---|---|
| 1 | **§3.12 SSE 缓冲清空**（2 行）+ 补 `useStreamAnswer` 单测 | `useStreamAnswer.ts:28,40` | 问答与浮层提问同时恢复可用 |
| 2 | **§3.3 日期标签按业务时区格式化** | `report_service.py:57,175`（+ 全仓同类 grep） | 日报/周报数字与标签对齐 |
| 3 | **§3.3 `daily-plan` 无目标回 200**，前端改按 `error_code` 判断 | `goals.py` + `Dashboard.tsx:107` | 消除每页 2 条控制台红字，去掉文案耦合 |
| 4 | **§3.8 卡片页真分页**（或虚拟滚动）+ 页尾说清「已显示 N / 共 M」 | `KnowledgeCards.tsx:109`、`QuestionSets.tsx` | 止住静默丢数据 |
| 5 | **§2.3 出题加题型/难度配比**，并在卡片侧过滤元数据句式 | 出题 prompt（`services/llm/`） | 复习不再永远是「简单四选一」 |
| 6 | **§2.1 去掉 `error_code` 前缀** | `client.ts:518` | 用户看到的都是人话 |
| 7 | **§2.2 清理开发者话术** | `messages.zh-CN.ts:1033-1035,1151,1457-1458` | 界面不再像开发者工具 |
| 8 | **§3.10 图谱默认只画连通分量 + 加筛选** | `KnowledgeGraph.tsx` / `GraphCanvas.tsx` | 让「知识图谱」重新有信息量 |
| 9 | **§5 第 5 条 清洗**：先做「段落级去重 + 文案如实描述」；公式简化与标题层级属产品决策，建议单独立项 | `cleaning_service.py`、`config.py:245` | 兑现 README 对清洗的承诺 |
| 10 | **§2.6 把「上传」放进侧边栏** | `Sidebar.tsx:54-87` | 新用户找得到入口 |
| 11 | 其余 🟡 按页面顺手收 | — | 一致性 |

---

## 8. 附录

### A. 复现命令

```powershell
# 1) 隔离栈（真实库零写入）
cd D:\engramnote\backend
$env:ENGRAMNOTE_E2E_TMP='D:\engramnote\backend\data\_uxwalk'; $env:ENGRAMNOTE_E2E_KEEP='1'
$env:ENGRAMNOTE_E2E_API_PORT='8011'; $env:ENGRAMNOTE_E2E_CONTROL_PORT='8012'
$env:E2E_FULL_PORT='5183'; $env:LLM_PROVIDER='glm'
& "$env:USERPROFILE\anaconda3\envs\mineru_env\python.exe" scripts\_e2e_full_runner.py

# 2) 前端
cd D:\engramnote\frontend
$env:VITE_API_TARGET='http://localhost:8011'; npm.cmd run dev -- --port 5183 --strictPort

# 3) 走查（九个轮次）
cd D:\engramnote\_agenttmp\uxwalk
node walk1.mjs; node walk2.mjs; node walk3.mjs; node walk4.mjs; node walk5.mjs
node walk6.mjs; node walk7.mjs; node walk8.mjs; node walk9.mjs

# 4) 直连取证
cd D:\engramnote\backend
& "$env:USERPROFILE\anaconda3\envs\mineru_env\python.exe" data\_uxwalk\_sse.py
& "$env:USERPROFILE\anaconda3\envs\mineru_env\python.exe" data\_uxwalk\_analyze.py
& "$env:USERPROFILE\anaconda3\envs\mineru_env\python.exe" data\_uxwalk\_understand_quality.py
```

### B. 证据文件

| 路径 | 内容 |
|---|---|
| `_agenttmp/uxwalk/walk1.json` … `walk9.json` | 每步的结构化观测（标题、正文长度、按钮、失败请求、控制台错误） |
| `_agenttmp/uxwalk/shots1/` … `shots9/` | 80+ 张真实渲染截图 |
| `_agenttmp/uxwalk/downloads/` | 导出的 Markdown（验证 front-matter） |
| `_agenttmp/uxwalk/sample-气系统运行技术标准.md` | 为验证清洗/理解/出题质量自造的样本（有 ground truth） |
| `backend/data/_uxwalk/_sse.py` `_analyze.py` `_understand_quality.py` `_setpw.py` | SSE 逐帧取证 / DB 只读统计 / 理解质量评估 / 副本口令 |

### C. 模型运行情况（本地跑通了哪些）

> ⚠️ **本节经过一次修正**。首版我写「本地 MinerU / Qwen3-ASR 模型不存在，需下 ~8 GB」——
> **这是错的**。我只查了 `backend/data/models/`，而 MinerU 与 ASR 的模型实际在
> **ModelScope 缓存**（`C:\Users\admin\.cache\modelscope\hub\models\`，路径来源见
> `check_env.py:58`），且名字里没有 `mineru` 字样（叫 `PDF-Extract-Kit-1___0`），
> 所以我的目录名过滤全部漏掉了。**模型一个都不缺。**

**磁盘上实际有什么**（实测，合计约 **12.9 GB**）：

| 模型 | 位置 | 大小 |
|---|---|---|
| `Xorbits/bge-m3` | `backend/data/models/` | 4 356 MB |
| `BAAI/bge-small-zh-v1___5` | `backend/data/models/` | 183 MB |
| `silero-vad` | `backend/data/models/` | 4 MB |
| `OpenDataLab/PDF-Extract-Kit-1___0`（MinerU Pipeline） | **ModelScope 缓存** | 2 379 MB |
| `OpenDataLab/MinerU2___5-Pro-2604-1___2B`（MinerU VLM） | **ModelScope 缓存** | 2 220 MB |
| `OpenDataLab/MinerU2___5-2509-1___2B`（旧版 VLM） | **ModelScope 缓存** | 2 216 MB |
| `Qwen/Qwen3-ASR-0___6B` | **ModelScope 缓存** | 1 793 MB |

`python check_env.py` 的步骤 6/7/8/9 **全部 ✓**，且步骤 3 显示 `qwen-asr` 与 `torch`
**都已安装**。

**但「磁盘上有」≠「本次跑到了」**。逐条核对（worker 日志 `data/_uxwalk/logs/worker.log` 为证）：

| 模型 | 本次是否被调用 | 依据 |
|---|---|---|
| `bge-m3` | ❌ **本次没被调用** | 日志第 68 行：`可用内存不足（1.8GB < 4.0GB），跳过主模型 BAAI/bge-m3，改用降级模型` |
| `bge-small-zh-v1.5` | ✅ **本次真被加载并调用** | 日志 70-72 行：`Load pretrained SentenceTransformer: …\bge-small-zh-v1___5` |
| MinerU（Pipeline / VLM） | ❌ **未调用** | 日志第 47 行：`转换开始: input.pdf \| 后端=vlm-http-client` —— 走的是**云端 API** |
| `silero-vad` / `Qwen3-ASR` | ❌ 未触发 | 本次没有音视频上传 |

> 所以首版那句「bge-m3 确实在本地跑了，而且是被真实链路用上的」**也是错的**：
> 库里 3469 张卡的 bge-m3 向量（1024 维）是**你此前自己跑出来的**，随数据库副本继承过来；
> 我点「生成相关建议」得到的 275 条建议，用的是**这些已有向量**，不是本次新算的。

### C.1 🟠 新增发现：嵌入模型会因可用内存**静默降级**

`config.py:223,226,229` 三个默认值决定了这件事：

```python
embedding_model: str = "BAAI/bge-m3"
embedding_model_fallback: str = "BAAI/bge-small-zh-v1.5"
embedding_min_free_memory_gb: float = 4.0
```

可用内存低于 4.0 GB 时，`embedding_service.py:71-82` 直接换成降级模型，
**只在日志里留一行 WARNING**，界面上完全无感：

```
WARNING | app.services.embedding_service | 可用内存不足（1.8GB < 4.0GB），
        跳过主模型 BAAI/bge-m3，改用降级模型 BAAI/bge-small-zh-v1.5
WARNING | app.services.embedding_service | 嵌入模型已降级加载: BAAI/bge-small-zh-v1.5（配置主模型为 BAAI/bge-m3）
```

两个模型的向量维度不同（bge-m3 = 1024，bge-small-zh = 512）。
本次实测**没有**发生维度混写（新内容的 `embedding_model` 是 `None`，即根本没写向量），
所以「混维度」只是**潜在风险、我未验证**，不作为结论。
但**降级本身是确凿的**，而且**用户无从得知**：清洗照样显示「完成」，
检索质量却已经不同了。

> 诚实说明：本次可用内存只有 1.8 GB，部分是我自己造成的
> （同一台机器上还跑着 uvicorn + worker + Vite + Playwright 浏览器）。
> 但这说明**阈值 4 GB 在开发机上很容易触发**，而触发后没有任何用户可见的提示。

### C.2 🟡 新增发现：约 4.6 GB 本地 MinerU 模型**下载了却没被用上**

`.env` 里有 `MINERU_API_TOKEN`，但**没有** `MINERU_BACKEND`，
于是取 `config.py:179` 的默认值：

```python
mineru_backend: str = "vlm-http-client"   # "pipeline" 或 "vlm-http-client" 或 "hybrid-http-client"
```

实测日志确认 PDF 走的是**云端**（上传到 `mineru.net/api/v4/file-urls/batch`，24.9 秒完成 4 页）。
也就是说 `PDF-Extract-Kit-1.0`（2.4 GB）+ 两个 `MinerU2.5` VLM（各 2.2 GB）**一共约 4.6 GB
在磁盘上闲置**，而每次解析都在消耗云端额度。

**这未必是错的**（云端更快、不占内存），但值得你确认一句：
**当初下这些模型是想走本地解析吗？** 如果是，设 `MINERU_BACKEND=pipeline` 即可切换；
如果不是，这 4.6 GB（外加旧版 `MinerU2___5-2509` 那 2.2 GB）可以考虑回收。

### C.3 🟡 新增发现：新导入的内容**不会自动补向量**，且没有兜底任务

worker 日志第 73-74 行显示，PDF 的 12 个 chunk **索引重建成功**：

```
chunk 索引已重建: note_id=5e7f9729-…, chunks=12
```

但这 12 行 `embedding_model = None` —— **没有向量**。DB 实测：
`chunks` 832 行中 **224 行无向量**，`knowledge_cards` 3469+14 行中**新加的 14 行无向量**。

这不是 bug，而是**已知的设计取舍**，代码注释里写得很清楚：

- `services/chunk_service.py:23-24`：「不能放在清洗流程里同步做）。
  `has_embedding=False` 的行需由 `scripts/embed_chunks.py` 补嵌入。」
- `main.py:310`：「…`scripts/embed_chunks.py`，而 **Beat 调度表里没有这个任务** ——
  于是"新导入的…"」

**问题在于用户侧**：`/ready` 会报 `pending_embeddings: 210`，但那是**给运维看的探针**；
界面上没有任何地方告诉用户「你刚导入的这篇资料目前检索不到」。
新资料会静默地**不在向量通道里**，直到有人手工跑脚本。

**建议**：给补嵌入加一条 Beat 定时任务（或导入后自动排队），
并在笔记详情页对「无向量的笔记」给一个可见标记。

### D. 本次走查**没有**覆盖的部分（诚实声明）

> ⚠️ **首版这里写错了一条**：「本地 MinerU / Qwen3-ASR 模型不存在，需先取得下载许可」——
> 模型**全都在磁盘上**（见附录 C）。真实情况是**它们存在但没被调用**，
> 原因见下面每一条。

- **本地 MinerU `pipeline` 模式**：模型在（`PDF-Extract-Kit-1.0` 2.4 GB + `MinerU2.5` VLM 2.2 GB），
  但 `mineru_backend` 默认 `vlm-http-client`（`config.py:179`），所以本次走的是云端。
  **本地 pipeline 路径未被验证**。想验的话设 `MINERU_BACKEND=pipeline` 再传一次即可。
- **音视频 ASR（Qwen3-ASR + silero-vad）**：模型与依赖都在
  （`Qwen3-ASR-0___6B` 1.8 GB，`qwen-asr` / `torch` 已安装），
  但我**没有上传任何音视频文件**，所以这条链路未被触发。
- **bge-m3 主模型**：本次因内存不足被降级跳过（见 §2.8），
  所以「bge-m3 在真实链路下的表现」本次**没测到**，测到的是降级模型。
- **邮件提醒实发、Celery Beat 定时任务**（每日 09:00 邮件、每周备份/一致性校验）：
  未等到触发时刻；`email_reminder_enabled` 只验证了开关读写。
- **SM-2 回退路径**（`REVIEW_SCHEDULER=sm2`）：本次只跑默认 FSRS。
- **多用户隔离与并发**：单账号串行走查，未验证两个账号同时操作。
- **完整注册流程**：复用已有账号，未走注册→首登的空账号体验。
- **深色模式**：项目当前没有该功能（`prefers-color-scheme` / `data-theme` 在 `src/**` 0 命中），
  不是「没测」而是「没有」。
- **选中文本提问浮层的答案重复**：只做到**代码层**证据（见 §3.5），
  **这一条是推断，不是观测**。修 §3.12 时请顺手在浮层上回归一次。
- **「不同嵌入模型向量混写」**：只标为潜在风险（见 §2.8），未观测到实例。

---

*本报告的所有结论都可按附录 A 重跑复核；与代码冲突时以代码实测为准。*
