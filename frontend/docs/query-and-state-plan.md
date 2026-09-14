# 5.2 / 5.3 迁移计划：TanStack Query 与 Zustand（**待批，未动代码**）

> **这份文档是计划，不是记录。** 本仓库的规矩是"没做的事写成没做"，
> 所以先说清楚现状：`frontend/package.json` 里**没有** `@tanstack/react-query`、
> 也**没有** `zustand`；`frontend/src/**` 里两者的 import 都是零命中（实测）。
> 5.2 / 5.3 至今一格都没做。
>
> 为什么先写计划再动手：这两件事动的不是某一个页面，而是**每个页面拿数据的方式**
> （28 个文件）与**跨组件的状态归属**。直接开改的话，"改了哪一半"无法回滚、
> "行为哪里变了"说不清。所以拆成**批次**，每批一次提交、可单独回滚，
> 每批都给出验证命令与必须保持不变的东西。
>
> **四个需要你点头的地方在 §6。** 不替你决定。

---

## 1. 现状盘点（全部实测，不是估计）

| 项 | 数值 | 怎么数的 |
|---|---|---|
| 源文件（不含测试） | **129** | `src/**/*.{ts,tsx}` 排除 `*.test.*` |
| 在 `useEffect` 里取数的文件 | **28** | "含 `useEffect(` 且 import 了 `../api/…`" |
| 轮询点 | **3** | `setInterval` 只有三处：`components/ReminderBanner.tsx:70`、`pages/notedetail/useNoteDetailData.ts:177` 与 `:201` |
| Context 个数 | **2** | `contexts/AuthContext.tsx`、`components/Toast.tsx` |
| 测试文件 | **23** | 其中 ~10 个各自写了本地 `renderX()` 助手；**没有**共享 render 助手 |
| 依赖 | 未安装 | `@tanstack/react-query` 与 `zustand` 在 npmmirror 上**可解析**（实测 `npm view` 得到 5.102.8 / 5.0.15） |

**28 个取数文件**（5.2 的作业面）：

| 类别 | 文件 |
|---|---|
| 页面（14） | `CardDetail` / `DailyMaterials` / `Dashboard` / `KnowledgeCards` / `LearningAssessment` / `LearningGoals` / `NotesList` / `QA` / `QuestionSets` / `QuickReview` / `Review` / `TodayLearn` / `Trash` / `Upload` |
| 页内 hook（5） | `cardreview/useCardReviewSession`、`knowledgegraph/{useGraphData,useGraphSearch,useSuggestionSelection}`、`notedetail/useNoteAnnotations`、`notedetail/useNoteDetailData`（**7 个 useEffect**）、`notedetail/useNoteLinks`、`projects/useProjects` |
| 组件（5） | `DeleteNoteDialog` / `NoteAskPanel` / `ReminderBanner` / `TaskProgress` / `VersionHistory` |
| 上下文（1） | `contexts/AuthContext`（同时是 5.3 的候选） |

---

## 2. 5.2 TanStack Query

### 2.0 依赖

`@tanstack/react-query@5.102.8`（**运行时依赖**）。⚠️ 5.1 期间定过"不加运行时依赖"，
那是**为生成客户端**定的（类型层的事不该拖运行时进来）；5.2 的命题本身就是
"用成熟的取数库替换手写 fetch + 轮询"，所以这条要你重新批准（§6 第 1 问）。

### 2.1 Provider 与初始化

`src/main.tsx` 现在是 `ErrorBoundary → ToastProvider → BrowserRouter → App`。
插入点是**外层**（Query 不依赖路由）：

```
ErrorBoundary → QueryClientProvider → ToastProvider → BrowserRouter → App
```

⚠️ `main.tsx` 顶部那 14 行样式表导入**有语义**（CSS 产物顺序、
`styles/mobile-input-font-size.test.ts` 以它为断言前提，文件头写着"不要重排"）——
加 Provider 时**不许动那段注释与顺序**。

`QueryClient` 实例只建一次（模块级常量，不在组件里 `useState`），
否则每次渲染都会换缓存。

### 2.2 测试策略（本批最大的改动面）

页面测试直接渲染页面，所以 Provider 必须能被测试复用：

1. 新增 `src/test/render.tsx`，导出 `renderWithProviders(ui, { route })`：
   `QueryClientProvider`（`retry: false`、`gcTime: 0`）+ `MemoryRouter`（需要路由的页面）；
2. 把 ~10 个测试文件里各自的本地 `renderX()` 改为调用它（**逐个文件、不批量重写**）；
3. 纯展示组件的测试（`StatCard` / `SelfRatingButtons` / `ReviewProgress` / `SourceContext` 等）
   **不接 Provider** —— 它们不取数，接上反而掩盖"这个组件其实在偷偷取数"。

### 2.3 默认值（建议值，见 §6 第 2 问）

| 选项 | 建议 | 理由 |
|---|---|---|
| `staleTime` | 30_000 | 单机应用、数据变动来自本人操作；30 秒内切页面不重复打请求 |
| `retry`（query） | 1 | 网络抖动重试一次；再多会拖长"看起来卡住"的时间 |
| `retry`（mutation） | **0** | 写操作不能盲目重试（上传/提交/删笔记都不是幂等的） |
| `refetchOnWindowFocus` | **false** | 单机单窗口；开着会让"切回来就刷新"变成不可预期的行为变化 |
| 测试环境 | `retry: false`、`gcTime: 0` | 避免"等重试"导致的慢测试与假绿 |

### 2.4 批次（每批一次提交、可独立回滚）

| 批 | 范围 | 验收 | 回滚 |
|---|---|---|---|
| **B1 只读列表** | `Dashboard` / `NotesList` / `Trash` / `QuestionSets` / `CardDetail` | 每页的"加载中/失败/空态"三种界面与文案**逐字不变**；分页与关键字筛选仍会重新取数；`npm test` + `e2e` | 单文件回滚（5 个页面 + 各自的测试） |
| **B2 轮询三处** | `TaskProgress`、`useNoteDetailData`（2 处）、`ReminderBanner` | 轮询**停止条件**不变（`isTerminal` 终态即停、`clearInterval` 时机一致）；不再有"组件卸载后仍 setState"的路径 | 同上 |
| **B3 写后刷新** | `Upload` / `DailyMaterials` / `Projects` / `KnowledgeCards` / `LearningGoals` | 写操作后**该刷的列表一定刷**（用 `invalidateQueries` 表达，替代手写"再拉一遍"）；⚠️ **`KnowledgeGraph.test.tsx` 那条"批量确认后的重拉是 `Promise.all`"必须重新推导**，不许删 | 同上 |
| **B4 SSE（只登记，不改）** | `NoteAskPanel` / `QA` 的流式问答 | **保持手写**：SSE 不是 query，`authorizedFetch` + 手工解析事件仍是正确形态 | —— |

### 2.5 必须保持不变（不变量清单，逐批核对）

1. **401 刷新单飞**、请求超时（30 s / 上传 600 s）、`Content-Type` 合并、
   204/空响应处理：全部仍在 `src/api/client.ts`，**一行不动**；
2. `ApiError.code` 的分流（阶段 0.11：`DAILY_REVIEW_LIMIT_REACHED` 在
   `Review.tsx` / `TodayLearn.tsx` 里的分支）——迁到 query 的 error 通道后**行为一致**；
3. **请求顺序与次数**：现存的顺序类断言（如上面那条 `Promise.all`）必须重新推导并保留意图；
4. 页面文案、DOM 结构、`aria-*`、以及 `e2e/a11y` 的 26 个场景期望；
5. 卸载即取消：现有请求靠 `AbortSignal`；迁到 `queryFn({ signal })` 时**不能丢**。

### 2.6 行为变化清单（要你能预期，不藏在提交里）

| 变化 | 表现 | 证据方式 |
|---|---|---|
| 请求去重/缓存 | 30 秒内重复进入同一页面不再打请求 | 测试里断言 fetch 次数 |
| 后台重取 | 窗口重新聚焦默认**不**重取（我们把开关关掉） | 配置 + 测试 |
| 失败重试 | query 失败重试 1 次（写操作不重试） | 测试用 `retry: false` + 一条专门的用例 |
| 卸载取消 | 组件卸载即中止在途请求（此前部分路径没有） | 一条"卸载后不再有 setState"的用例 |

---

## 3. 5.3 Zustand

### 3.1 依赖

`zustand@5.0.15`（运行时依赖，同样需要批准）。

### 3.2 范围（三块，按风险从低到高）

| 块 | 内容 | 说明 |
|---|---|---|
| **Z1 Toast** | `components/Toast.tsx` 的 context → store | 最孤立：只有一个 Provider、调用方是 `useToast()`。改完 `createContext` 从 2 降到 1（可机械核对） |
| **Z2 页面级 UI 状态** | 图谱的选中/筛选（`knowledgegraph/*`、`components/graph/GraphSidebar.tsx` 679 行）、笔记详情的视图模式、列表筛选 | 这些是"同一个页面里几个隔层组件都要读写"的状态，也是 prop drilling 的主要来源 |
| **Z3 AuthContext** | **建议不动** | 它不只是状态：包含令牌持久化、401 副作用、`token-expired` 事件与已有测试。搬进 store 的收益小、风险大。**若你要一并迁，那是独立一轮**（§6 第 3 问） |

### 3.3 验收口径（计划原文的"页面组件行数减半"要改）

计划 5.3 的验收写的是"页面组件行数减半"。实测最大的几个页面：
`DailyMaterials.tsx` **682** 行、`GraphSidebar.tsx` **679**、`Dashboard.tsx` **676**、
`Upload.tsx` **640**、`LearningAssessment.tsx` **629**。
**"全部页面减半"在不重写 UI 的前提下做不到**（这些行里大头是 JSX 与文案，
不是状态）。建议换成三条可测口径（§6 第 4 问）：

1. **指定的 3 个最大页面**在"只搬状态与取数、不动 DOM 结构与文案"的前提下降到 **< 400 行**
   （`Dashboard` / `DailyMaterials` / `Upload`）——用 `git diff --stat` 与行数前后对照；
2. `createContext` 出现次数从 **2 → 1**（Toast 迁移完成，AuthContext 保留）；
3. **透传减少可数**：在 `KnowledgeGraph` 与 `NoteDetail` 两条组件树上，
   数"同一个 prop 名出现在 3 层以上组件签名里"的次数，迁移前后各记一次（人工表格 + 复核）。

### 3.4 顺序与提交

Z1 → Z2（一个页面一条提交）→ 每次提交后跑：`npm test`、`npm run e2e`、`npm run a11y`。

---

## 4. 风险与回滚

| 风险 | 表现 | 处置 |
|---|---|---|
| 测试被"顺手改成绿的" | 断言被删/放宽，行为变化无人察觉 | 每批的验收里写明"哪些断言必须重新推导而不是删"；删断言要在提交信息里逐条交代理由 |
| 缓存掩盖了刷新 | 写操作后列表不刷新（用户以为没生效） | B3 批次里每个写操作都要指出"它失效了哪些 key" |
| 请求次数变化撞上 e2e 桩 | `page.route` 桩按路径匹配，不受次数影响；但登录流程的时序敏感 | 每批跑 `e2e`；`e2e:full`（真实后端）在 B1/B3 之后各跑一次 |
| 双份缓存 | 页面里既留旧 state 又用 query | B1 起就删掉被 query 取代的 `loading/error` 本地状态，不留两套 |
| 依赖体积/供应链 | 新增两个运行时依赖 | 版本钉住（`^` 内），并在 `security_scan.py` 的报告里出现（它本来就扫 `npm audit`） |

## 5. 验收总表（整件事做完时应当成立）

1. 28 个取数文件里，**除 SSE 两处**外全部走 query/mutation；
2. `npm test` / `npm run e2e` / `npm run a11y` / `e2e:full` 全绿；
3. §2.5 的五条不变量逐条有测试兜住；
4. §3.3 的三条口径有前后数字；
5. `docs/overhaul-plan.md` 的 5.2 / 5.3 两行从 ⏸ 改成 ✅ 并附证据链接。

## 6. 待你决定的 4 件事

| # | 问题 | 备选 |
|---|---|---|
| 1 | 批准引入两个运行时依赖吗 | (a) 都批 (b) 只批 TanStack Query（5.2 先做，5.3 缓） (c) 都不批（那 5.2/5.3 就不做，改为"手写取数层收敛"，收益小得多） |
| 2 | `QueryClient` 默认值用 §2.3 的建议值吗 | (a) 照用 (b) `staleTime: 0`（每次进页面都拉，最接近现状） (c) `retry: 0`（完全照现状，不重试） |
| 3 | `AuthContext` 迁 Zustand 吗 | (a) 不迁（建议） (b) 迁（独立一轮） |
| 4 | "页面行数减半"改成 §3.3 的三条口径吗 | (a) 同意 (b) 你给另一个口径 |
