# 前端可访问性审计（axe-core + 真 Chromium）

> 对应 overhaul-plan **5.9（可访问性）** 的**审计一半**。
> 本文件记录的是**已经跑起来的工具**与**它当场报出的东西**，不是计划。
> 修复属于后续的 5.9 修复轮（本轮按项目自己的规矩来：
> **先把证据与安全网放到位，再改代码**）。

---

## 1. 怎么跑

```bash
cd frontend
npm run a11y            # = playwright test --project=a11y（该 project 固定 --workers=4）
npm run a11y:headed     # 带界面调试（单 worker）
npm run e2e             # 功能层（10 条），**不含**审计
npm run e2e:all         # 两个 project 一起跑（20 条，实测也全绿，约 36 秒）
```

一条命令自洽：dev server 由 `playwright.config.ts` 的 `webServer` 自动拉起与关闭
（端口 **4319**、`--strictPort`），与 `npm run e2e` 完全同一套机制 —— 不需要先开服务，
也不会碰人类手上 DSH Web GUI 的 3080。

### 两个 project，不是"一个 project 里的两个文件"

`playwright.config.ts` 里现在有**两个 project**：

| project | `testMatch` | 用例数 | worker | 谁在跑 |
|---|---|---|---|---|
| `chromium` | `**/!(*a11y).spec.ts` | 10 | 默认（按核数） | `npm run e2e` |
| `a11y` | `**/a11y.spec.ts` | 10 | **4**（写死在 project 里） | `npm run a11y` |

**为什么必须分开**（两个都是实测踩出来的）：

1. **并发**：8 个 worker 同时打 Vite dev server 时，**按需转换**（页面都是
   `React.lazy` 懒加载，每个路由第一次访问才现场编译）会把首屏拖到 10 秒以上，
   于是"等欢迎标题出现"超时 —— 失败信息指向"页面没渲染"，真实原因是
   "开发服务器被 8 个 axe 注入 + 懒加载请求同时敲"。审计要的是**页面内容**，
   不是吞吐。定到 4 之后稳定在 ~21 秒。
2. **`testMatch` 是交集**：`playwright test a11y.spec.ts` 这种"命令行传文件名"
   会与顶层 `testMatch`（已排除 a11y）取交集，直接得到 `No tests found`。
   用 project 表达"这一组用例有自己的匹配式与参数"才是配置该说的话 ——
   这个坑在接线时真踩到了一次，写在这里免得下次再踩。

| 项 | 值 |
|---|---|
| 引擎 | `@axe-core/playwright` **4.13.0**（devDependency，连带 `axe-core` 4.13.0） |
| 规则集 | `wcag2a`、`wcag2aa`、`wcag21a`、`wcag21aa`、`best-practice` |
| 浏览器 | 仍是那一个 Chromium（`npm run e2e` 用的同一个，没有额外下载） |
| 用例 | **10 条**：9 个扫描场景 + 2 条自检（登录页那条含 2 个状态） |
| 实测耗时 | 约 **21~24 秒**（本机，热缓存） |

### 扫了哪些界面

| 场景（`scene`） | 界面 | 为什么扫它 |
|---|---|---|
| `login` | 登录页（未认证入口，`/` 的兜底渲染） | 每个用户的第一屏 |
| `login-error` | 登录页 + 凭据错误的 401 状态 | 错误分支是另一个渲染分支，`role="alert"` 只在这里出现 |
| `dashboard` | 已登录外壳（侧边栏）+ 仪表盘 | 登录后的落地页；侧边栏在每个已登录页面上都存在 |
| `notes-list` | 笔记列表（非空列表，2 条笔记） | 卡片本身是 `role="button"`，列表项里还有操作按钮 |
| `note-detail` | 笔记详情（Markdown 正文 + 元信息栏） | 正文的标题层级、链接样式、角色下拉框 |
| `card-review-front` | 卡片复习：未翻面 | 该页**没有输入控件**（靠容器接键盘），结构特殊 |
| `card-review-back` | 卡片复习：已翻面（自评按钮 + 调度反馈） | 翻面后才出现的控件区 |
| `knowledge-graph` | 知识图谱（4 个节点的非空图谱） | 工具栏、过滤器、侧栏统计 |
| `projects` | 项目页 | 列表 + 新建表单 |
| `dashboard-mobile` | 仪表盘 @ 375×667 | 专门用来把"axe 判不了触控尺寸"落到记录里（见 §4） |

**没有扫**（本轮范围外，不要当成"已覆盖"）：答题复习 `Review`、问答 `QA`、
上传 `Upload`、今日学习 `TodayLearn`、知识卡片 `KnowledgeCards`、卡片详情 `CardDetail`、
学习评估 `LearningAssessment`、学习目标 `LearningGoals`、回收站 `Trash`、
每日资料 `DailyMaterials`、快速复习 `QuickReview`、注册页 `Register`、404 页。
其中 `Review` 与 `CardReview` **共用** `SelfRatingButtons` 组件，
所以 F-19 的自评按钮对比度问题**大概率也在那里**，但那一条是推断，不是本次实测。

---

## 2. 原始计数（本次运行的实测值，一个数都没有修饰）

axe 报的"违规"是**规则 × 节点**：一条规则命中 N 个元素 = 1 条规则 / N 个节点。

| 场景 | DOM 元素 | 评估规则 | 通过 | 违规规则 | 违规节点 | 需人工确认 | 不适用 |
|---|---|---|---|---|---|---|---|
| `login` | 48 | 90 | 26 | 2 | 7 | 8 | 61 |
| `login-error` | 49 | 90 | 30 | 2 | 7 | 9 | 57 |
| `dashboard` | 208 | 94 | 33 | 4 | 9 | 23 | 56 |
| `notes-list` | 143 | 93 | 32 | 4 | 10 | 17 | 56 |
| `note-detail` | 158 | 92 | 34 | 4 | 8 | 14 | 53 |
| `card-review-front` | 123 | 91 | 29 | 3 | 5 | 14 | 58 |
| `card-review-back` | 140 | 91 | 29 | 3 | 7 | 13 | 58 |
| `knowledge-graph` | 183 | 91 | 30 | 2 | 5 | 25 | 58 |
| `projects` | 139 | 92 | 29 | 2 | 4 | 15 | 60 |
| `dashboard-mobile` | 208 | 94 | 34 | 4 | 6 | 11 | 55 |
| **合计（按场景相加）** | — | — | **306** | **30** | **68** | **149** | **572** |

**这 68 个节点不是 68 个不同的元素**：同一个元素会在多个场景里各出现一次
（侧边栏在 8 个已登录场景里都存在；`login` 与 `login-error` 是同一页面的两个状态）。
按"规则 + 场景"去重后是 **30 组**（= 表格里每个场景的"违规规则"之和），
按 axe 的等级分布（同一节点跨场景重复计数）：

| 等级 | 节点数（跨场景相加） |
|---|---|
| `critical` | **2** |
| `serious` | **39** |
| `moderate` | **21** |
| `minor` | **6** |

"需人工确认"合计 **149 个节点** —— 那些**不是通过**，是 axe 判不了（见 §4.3）。

> 逐场景的完整证据（每个违规节点的**选择器 + 失败原因 + HTML 片段**）会作为
> JSON 附件随测试归档（`a11y-<scene>.json`），跑 `--reporter=json` 或失败时可直接取用。

### 一句话结论（不夸大）

**应用不是"无障碍合格"的**：axe 的 WCAG 2.0/2.1 A+AA 规则集在 9 个真实界面上
报出了 30 组"规则 × 场景"违规，其中 2 个节点是 `critical`（表单控件没有可访问名）、
39 个是 `serious`（对比度不足、按钮里嵌按钮、链接无法与正文区分）。
同时，**"这次没报出来的" 不等于 "没问题"** —— axe 看不见的东西列在 §4，
另有 149 个节点是它**明确不下结论**的（渐变背景上的对比度）。

---

## 3. 发现（按优先级排序，含逐条判定）

**判定口径：**
- **(a) 真实问题** —— 会影响真实用户的某种输入方式（键盘 / 屏幕阅读器 / 低视力），
  且修法明确；
- **(b) 需要人工确认 / 可能是设计取舍** —— 规则的判据与产品意图有张力，
  或依赖用户内容，需要人做决定；
- **(c) 真实但在本轮范围外** —— 确实是问题，但归属别的工作流（如设计令牌由 5.6 管）。

`id` 与 `frontend/e2e/a11y.spec.ts` 里的 `REGISTRY` **一一对应** ——
测试里每一条豁免都能在这里查到为什么。

### P0 —— 现在就修，成本极低、覆盖全部用户

| id | 规则 | 影响 | 位置 | 证据 | 判定 |
|---|---|---|---|---|---|
| **F-10** | `select-name` | **critical** | 知识图谱 → 卡片类型过滤器<br>`src/pages/knowledgegraph/GraphToolbar.tsx:100` | `<select className="graph-filter-select">` 没有 `<label>`、没有 `aria-label`、没有 `title`。axe：*Element does not have an implicit (wrapped) `<label>` … aria-label attribute does not exist or is empty* | **(a) 真实问题**。屏幕阅读器只念"组合框"，用户不知道自己在筛什么。<br>**一行可修**：加 `aria-label="按卡片类型筛选"`。 |
| **F-11** | `select-name` | **critical** | 笔记详情 → 「笔记角色」下拉框<br>`src/pages/notedetail/NoteDetailHeader.tsx:137` | 同上（内联样式里还有 `outline: none`，键盘焦点也看不出来） | **(a) 真实问题**。这个控件会**改数据**（`updateNoteRole`），无名控件比只读筛选更糟。<br>**一行可修**：加 `aria-label="笔记角色"`。 |

> 两条 `critical` 是同一类：**原生表单控件缺可访问名**。它们是本次审计里
> 唯一被 axe 标为 `critical` 的东西，也是最便宜的修复。

### P1 —— 覆盖面最广的单点修复

| id | 规则 | 影响 | 位置 | 证据 | 判定 |
|---|---|---|---|---|---|
| **F-04** | `color-contrast` | serious | **每个已登录页面**的侧边栏分组标题<br>`src/styles/layout.css:180-189`（`color: var(--sidebar-section-title)` → `--color-text-tertiary`） | `#9a9ab0` 白底 **2.75:1**，11.2px 小字要求 4.5:1。9 个场景里 8 个命中（窄屏收进抽屉除外） | **(a) 真实问题**。三处分组标题（学习 / 笔记 / 知识）是导航的**结构信息**，低视力用户看不出分组。<br>**一处修，九页受益**：改 `--color-text-tertiary`（`src/styles/base.css:23`）或让 `.sidebar-section-title` 用更深的令牌。注意该令牌共 16 处引用（含折叠态省略号、DiffView、Auth 页脚），改动需要跑一遍本审计确认没有连带回归。 |
| **F-05** | `color-contrast` | serious | 知识图谱侧栏面板标题 `.graph-panel-title`<br>`src/styles/graph.css:186-195` | 同一令牌 `--color-text-tertiary`，白底 2.75:1 | **(a) 真实问题**，与 F-04 同源。 |
| **F-06** | `color-contrast` | serious | 状态徽章 `.status-cleaned` / `.status-converted`<br>`src/styles/components.css:213-215`（`--color-success: #2d8a56`，`base.css:28`） | 白底 **4.3:1**、`--color-bg` (#faf9f7) 上 **4.08:1**，12.8~14px 小字要求 4.5:1 | **(a) 真实问题，但边际**（差 0.2~0.4）。它标的是"这条笔记处理到哪一步了"，低视力用户会把它和相邻徽章混淆。<br>修法：把 `--color-success` 压深一档（例如 `#25714a` 一类）—— **归属 5.6 设计令牌**，属于 (c) 的一部分，但影响用户，建议在 5.9 一起做。 |

### P2 —— 交互结构问题（比对比度更影响键盘与屏幕阅读器）

| id | 规则 | 影响 | 位置 | 证据 | 判定 |
|---|---|---|---|---|---|
| **F-09** | `nested-interactive` | serious | 仪表盘「今日待复习」卡片<br>`src/pages/Dashboard.tsx:289-304` | 外层 `div[role="button"][tabindex="0"]` 里嵌了一个真 `<button>开始复习</button>`。axe：*Element has focusable descendants* | **(a) 真实问题**。可聚焦元素里嵌可聚焦元素：Tab 会在同一个卡片里停两次、屏幕阅读器会把内层按钮念成外层按钮的一部分，而且外层只监听 `Enter`（**没监听 Space**），与 `role="button"` 的约定不符。 |
| **F-17** | `nested-interactive` | serious | 笔记列表的笔记卡片（2 张各一次）<br>`src/pages/NotesList.tsx:212-219` | 卡片是 `article[role="button"][tabindex="0"]`，内部有可聚焦的删除/重试按钮 | **(a) 真实问题**，与 F-09 同一类。**与已修掉的 `<button>` 嵌 `<button>`（Sidebar）是同一个洞的不同入口** —— 那次修的是真按钮嵌套，这次是 `role="button"` 容器嵌套。修法应统一：卡片不要 `role="button"`，改为"标题是真链接 + 操作是独立按钮"。 |
| **F-03 / F-16** | `aria-allowed-role` | minor | 仪表盘「最近笔记」2 张卡 + 笔记列表 2 张卡<br>`Dashboard.tsx` / `NotesList.tsx:212` | `<article role="button">` —— ARIA 不允许 `article` 使用 `button` 角色 | **(a) 真实问题，但等级轻**。`role=button` 会**覆盖** `article` 的语义，屏幕阅读器不再把它当一篇文章；可访问名来自内容，所以还能用。<br>修法与 F-09/F-17 是同一处改动，建议一起做。 |
| **F-19** | `color-contrast` | serious | 卡片复习的自评四档<br>`src/utils/labels.ts:110-115`（`selfRatingOptions[].color`） | 「勉强想起」`#c9a959` 白底 **2.25:1**、「想起来了」`#2d8a56` 白底 **4.3:1** | **(a) 真实问题**。四个档位的**色差本身就是区分手段**（红/金/绿/蓝），而金色只有 2.25:1 —— 对色觉障碍与低视力用户，这两档几乎一样。<br>**同一组件 `SelfRatingButtons` 也用在答题复习页（`Review`）**，那里本轮未扫 → 见 §5。 |

### P3 —— 语义与导航结构

| id | 规则 | 影响 | 位置 | 证据 | 判定 |
|---|---|---|---|---|---|
| **F-01 / F-02** | `landmark-one-main`、`region` | moderate | 登录页（初始态与失败态）<br>`src/pages/Login.tsx` + `Auth.module.css` | 全页没有 `<main>`；h1、两个 label、两个 input、页脚 p 共 6 个节点"不在任何 landmark 里" | **(a) 真实问题**，但影响的是**屏幕阅读器的跳转效率**（"跳到主内容"不可用），不是"用不了"。修法：把表单区包进 `<main>`（顺带消掉 `region` 的 6 个节点）。 |
| **F-07 / F-22** | `heading-order` | moderate | 仪表盘（桌面 + 移动端）<br>`Dashboard.tsx:156/215/296/310` 是 h3，`:236/349/399` 是 h2 | h1「欢迎使用 EngramNote」之后直接出现卡片里的 h3 | **(b) 需要确认**。`h3` 是有意为之还是顺手写的？视觉上卡片标题确实比区块标题小。修法有两种（把卡片标题改成 h2，或把区块标题改成 h2 后卡片用 h3），**取决于设计意图**，所以标 (b)。 |
| **F-18** | `page-has-heading-one` | moderate | 笔记列表 | 整页没有 h1（最高级标题是卡片里的 h3） | **(a) 真实问题**。页面没有"这是什么页"的一级标题。修法：给列表加 `<h1>笔记</h1>` 或复用一个现有标题。 |
| **F-14 / F-15** | `page-has-heading-one` | moderate | 卡片复习（正反两面）<br>`src/components/quiz/ReviewProgress.tsx:68` | 该组件渲染的是 `<h2>{title}</h2>` —— 页面上根本没有 h1 | **(a) 真实问题，且修法明确**：`ReviewProgress` 的标题就是这一页的顶层标题，改成 `<h1>` 即可（同时消掉 `heading-order` 的隐患）。 |
| **F-20** | `heading-order` | moderate | 项目页<br>`src/pages/projects/ProjectsHeader.tsx:8` 是 h1，卡片里是 h3 | h1 → h3 跳级 | **(b) 需要确认**，同 F-07。 |
| **F-08** | `heading-order` | moderate | 笔记详情：Markdown 正文的 h4 | 页面 h1/h2 之后直接出现正文里的 h4（原文本身没写 h3） | **(b) 需要人工决定**。层级来自**用户自己的内容**，不是布局缺陷。可选：渲染时归一化标题级别（`h1→h2` 整体下沉）、或接受它。**不要**为了消警告去改用户内容。 |

### P4 —— 正文链接的可辨识度

| id | 规则 | 影响 | 位置 | 证据 | 判定 |
|---|---|---|---|---|---|
| **F-13 / F-26** | `link-in-text-block` | serious | 笔记详情正文；卡片复习的「原文语境」区<br>`src/styles/base.css:120-124`（`a { text-decoration: none }`）+ `SourceContext.tsx:120` | 链接与周围文字的对比 **1.36:1**（正文）与 **1.89:1**（灰字旁），要求 3:1；且**没有下划线** | **(a) 真实问题**。正文里 `#0f3460` 的链接和 `#1a1a2e` 的正文几乎同色 —— 不靠颜色分辨不出哪里可以点。WCAG 1.4.1（不只靠颜色传达信息）的典型场景。<br>修法：给 `.markdown-body a` 与 SourceContext 的链接加下划线（或把链接色改成与正文对比 ≥3:1 的明显色）。 |

**汇总判定**（口径：`(a)` 会影响真实用户的某种输入方式；`(b)` 判据与产品意图有张力，
需要人做决定；`(c)` 属实但归属别的工作流）：

- **(a) 真实问题：13 组** —— F-01/F-02（登录页无 landmark，2 条同源）、
  F-03/F-16（`article[role=button]`）、F-04/F-05（侧边栏与图谱面板标题对比度，
  同源）、F-09/F-17（卡片里嵌可聚焦元素）、F-10、F-11（两个无名 `<select>`）、
  F-13/F-26（正文链接无法与正文区分）、F-14/F-15（卡片复习缺 h1）、
  F-18（笔记列表缺 h1）、F-19（自评档位对比度）。
- **(b) 需要人工决定：3 组** —— F-07/F-22、F-20（标题级别是否该改）、
  F-08（Markdown 正文的标题层级要不要归一化）。它们都是**同一件事**：
  "标题级别是设计意图还是顺手写的"，只有人能回答。
- **(c) 归属其他工作流：1 组** —— F-06（`--color-success` 令牌本身的取值属
  5.6 设计令牌；但它的可见后果落在 5.9，所以两边都要知道）。

按 axe 的等级分布（**跨场景相加**，所以同一元素会在多个页面里各计一次）：
`critical` 2 个节点、`serious` 39 个、`moderate` 21 个、`minor` 6 个。

### 一句话结论（不夸大）

**应用不是"无障碍合格"的**：axe 的 WCAG 2.0/2.1 A+AA 规则集在 9 个真实界面上
报出了 30 组"规则 × 场景"违规，其中 2 个节点是 `critical`（表单控件没有可访问名）、
39 个是 `serious`（对比度不足、按钮里嵌按钮、链接无法与正文区分）。
同时，**"这次没报出来的"不等于"没问题"** —— axe 看不见的东西列在 §4，
另有 149 个节点是它**明确不下结论**的（渐变背景上的对比度）。

**没有发现**的问题（同样是结论，写下来免得下次重复劳动）：
- 没有 `image-alt` 违规 —— 应用里的图标要么 `aria-hidden`、要么有可访问名；
- 没有 `button-name` / `link-name` 违规 —— 现有按钮与链接都有名字
  （`Sidebar.tsx:98/104/138` 的 `aria-label` 起了作用）；
- 没有 `duplicate-id` / `aria-hidden-focus` / `label`（表单标签）违规 —— 登录表单的
  `<label for>` 与 `#email`/`#password` 的关联是**真的**，这解释了为什么
  `login-form.spec.ts` 那条"原生约束会拦下提交"能成立；
- 没有颜色之外的对比度问题（`color-contrast-enhanced` 未启用，AAA 不在本层范围内）。

---

## 4. axe **看不见**的东西（这一层明确做不到的事）

下面每一条都是**当前没有任何一层在守**的。写在这里不是免责，是待办清单。

### 4.1 焦点顺序、焦点可见性、键盘陷阱

axe 只看 DOM 与计算样式，**不模拟 Tab 键**。所以：

- **Tab 顺序是否合理**没有验证。已登录外壳里侧边栏在 DOM 中先于 `main`
  （`App.tsx:91` 的 `<Sidebar/>` 在 `<main>` 之前），所以每个页面都要先 Tab 过
  15 个导航项才能到内容 —— 这可能是问题，也可能是有意，**本轮没有量过**。
- **焦点陷阱**（尤其在移动端抽屉与模态框里）没有验证：`LinkManagerModal`、
  `DeleteNoteDialog`、侧边栏抽屉打开时焦点会不会跑到背后？没有测。
- **`outline: none`** 出现在至少两处内联样式里（`NoteDetailHeader.tsx:155`
  的 `<select>`、`CardReview.tsx:129` 的容器）。前者是**真控件**，去掉焦点环
  意味着键盘用户看不出焦点在哪。axe 不检查这个（它不属于任何 ARIA 规则）。
- **`CardReview` 的键盘约定**：该页没有输入控件，靠容器 `tabIndex={-1}` +
  `useReviewKeyboard` 接 Enter（`CardReview.tsx:71-76` 有说明）。它在 Vitest 里
  有单测，但"真浏览器里焦点落到 body 后回车还灵不灵"**没有被本层验证**。

**后续**：写几何 + `page.keyboard.press('Tab')` 的断言（记录 `document.activeElement`
序列），或引入 `@axe-core/playwright` 之外的手工脚本。这是一件**独立**的活，
不要指望 axe 顺手覆盖。

### 4.2 屏幕阅读器语义

- **可访问名算不算"好名字"**：axe 只判"有没有名字"。例如图谱过滤器加了
  `aria-label="按卡片类型筛选"` 就算过，但这个名字在实际朗读里是否清楚，
  只有人戴耳机听一遍才知道。
- **朗读顺序 / 冗余**：侧边栏 15 个按钮每个前面带 emoji（`☘ 今日学习`、
  `◎ 知识图谱`）。emoji 会不会被念成"四叶草"？axe 不管。
- **动态区域**：`Toast`、`ReviewProgress` 的进度更新、`CardReview` 的
  "下次复习: 21 天后" —— 这些**异步出现**的内容有没有 `aria-live`，
  应该念还是不该念，axe 判不了（`aria-live` 存在性它可以判，**该不该有**不行）。

### 4.3 动态状态与渐变背景上的对比度（**149 个"需人工确认"节点**）

axe 的 `color-contrast` 在**算不出背景色**时不会猜，而是把节点丢进 `incomplete`。
本项目里这是最大的一类（跨场景相加 149 个节点，比违规节点还多一倍），成因有两类：

1. **渐变文字**：`background-clip: text` + `-webkit-text-fill-color: transparent`
   （`layout.css:26/90-93` 的 `.sidebar-logo`、`components.css:270-281` 的
   `.gradient-text` / `.gradient-text-gold`）。**文字的前景色是透明的**，
   axe 拿不到；要人工按渐变两端色分别算对比度。
   命中的有：侧边栏 logo「EngramNote」、仪表盘 h1、知识图谱 h1 等。
2. **半透明/图标型控件**：`.sidebar-collapse-btn`（28×28，`layout.css:110-123`，
   `color: var(--color-text-tertiary)`）、`.sidebar-item-icon` 等。
   它们多半带有 `opacity` 或叠加在非纯色上。

**最值得先看的一条**：`.sidebar-collapse-btn` 用的是 `--color-text-tertiary`
（#9a9ab0）—— 与 F-04 同色。若其背景确实是白色，它就是 **2.75:1**，
即又一个真实违规，只是 axe 没能确定背景而没报出来。

**后续**：人工对照 `docs/design` 或直接在浏览器里取色算比值；
或引入能处理渐变的对比度工具。**不要**因为 axe 没报就当它没问题。

### 4.4 移动端触控目标尺寸

`dashboard-mobile` 场景（375×667）**0 条触控尺寸类违规** —— 不是因为尺寸没问题，
而是因为 **axe-core 4.13 根本没有这条规则**（WCAG 2.5.8 Target Size 是 AA，
但 axe 不实现几何判定）。这条用例里已把这一点写成断言
（`expect(touchRules).toEqual([])` —— 钉住的是"工具没有这个能力"这个事实），
并把导航项的实际 `boundingBox()` 存进 JSON 附件。

已知的具体疑点（手工看代码就能确认的）：
- `.sidebar-collapse-btn` **28×28 px**（`layout.css:114-115`）—— 低于常见的
  44×44 建议值，也低于 WCAG 2.5.8 的 24×24 下限之外更严的门槛；
- `SelfRatingButtons` 的四个档位在窄屏下是否够大（`SelfRatingButtons.module.css`
  里带 768/480 两条窄屏规则，本层没有针对它的几何断言）；
- 侧边栏导航项高度、笔记卡片上的删除按钮（`icon-btn` 一类）。

**后续**：写几何断言（`boundingBox()` 的宽高 ≥ 阈值），而不是等 axe。
这是本层**结构上**做不到的事。

### 4.5 其它明确不在本层范围

- **缩放 / 重排**（WCAG 1.4.4 / 1.4.10）：200% 缩放、320px 宽下的重排没有测；
- **动效**（`prefers-reduced-motion`）：项目里有 `fadeIn`、`slideIn` 等关键帧
  （`base.css:130+`）与 `transition`，有没有尊重用户的减动效设置没有测；
- **跨浏览器 / 跨屏幕阅读器**：只有 Chromium；没有 NVDA / VoiceOver 实测；
- **真实后端的错误文案**：本层 `/api` 全部被桩掉，所以"错误提示本身是否可理解"
  不在范围内（那属于后端契约与文案评审）。

---

## 5. 还没被任何一层验证的相邻风险（本轮审计顺带发现）

1. **`Review`（答题复习）未扫，但它与 `CardReview` 共用 `SelfRatingButtons`** ——
   F-19 的两个低对比度色值来自 `utils/labels.ts`，那里是**唯一数据源**，
   所以 `Review` 上的自评按钮几乎必然同样不足。这是**推断**，不是实测。
   修 `labels.ts` 时两页一起受益；顺带应该把 `Review` 加进扫描场景。
2. **`DailyMaterials` / `TodayLearn` / `Upload` 也有 `role="button"` 的卡片**
   （`DailyMaterials.tsx:476/655`、`TodayLearn.tsx:342`、`Upload.tsx:391`）。
   本次没扫这些页面，但写法与 F-03/F-16/F-17 **逐字相同**，
   所以同类问题**大概率存在**。这是"用 grep 得到的高置信猜测"，不是审计结论。
3. **卡片详情 `/cards/:cardId` 本轮未扫**：桩里 `/api/cards/{id}` 返回的是列表形状，
   直接加进场景会得到"加载失败"页 —— 那扫的是错误态而不是卡片页，
   属于**假覆盖**，所以刻意没加。要覆盖它需要先补一个符合卡片详情契约的桩。

---

## 6. 测试怎么判定"通过"（以及为什么不许空过）

### 判定尺度：登记表 + 上限

`e2e/a11y.spec.ts` 里的 `REGISTRY` 把当前**已知的每条违规**逐条写下来
（`id` + 规则 + 场景 + 影响等级 + 节点数 + 归属）。运行时：

1. 出现**未登记**的规则 → **失败**（这是真正在守的部分：新回归立刻红）；
2. 已登记规则的**节点数超过**登记上限 → **失败**（影响面扩大）；
3. 已登记规则的**影响等级高于**登记值 → **失败**（变严重）；
4. 同一（场景，规则）出现两条登记项 → **失败**（那种写法会让匹配悄悄失效）；
5. 登记表里有**没写归属**的条目 → **失败**（没有归属的豁免就是"忘了修"）。

**为什么只卡上限、不要求精确相等**：本文件是给修 bug 的人看的。若要求相等，
他每修掉一条就得同时改测试里的数字，很快就会出现"顺手把数字改大一点"，
门禁随即失效。只卡上限意味着**修好立刻生效、不需要改测试文件**；
而问题变多一定失败。代价是它**不会告诉你"某条已被修好"** ——
那件事由上面 §3 的表格（人工过一遍）负责。

### 防线：不许空过

一个坏掉的选择器、一次失败的懒加载、一个被桩吃掉的接口，都会让页面渲染成空白
—— 而**空白页面的 axe 结果恒为"0 违规"**。那种绿灯比红灯更糟。因此每次扫描都断言：

- 该页**独有**的渲染标记先出现（例如笔记列表要等到"共 2 条"**和**一条笔记标题都在，
  而不是只等一个标题 —— 空态也有标题）；
- DOM 元素数 ≥ 场景下限（实测值留 ~30% 余量）；
- axe 真的执行了（`passes > 0`、评估规则数 > 20）；
- 桩没有出现"未定义响应"的接口（`/api` 桩对未知路径回 **501** 并记录，
  因为页面普遍用 `.catch(() => null)` 把它吞掉）；
- 页面无未捕获异常（`pageerror`）。

另外两条**自检用例**：

1. **注入一个没有 `alt` 的 `<img>`，断言 axe 报出 `image-alt`** ——
   守的是"扫描链路本身还活着"；
2. **登记表自检**：场景名拼写正确（写错的后果是豁免永远匹配不到，
   而维护者会去加第二条豁免，越加越乱）、（场景，规则）唯一、每条都写了归属。

### 已知的稳定性注意点

- 并发由 project 固定为 4（见 §1）。8 worker 时 Vite 的按需转换会让首屏超 10 秒，
  表现为"页面没渲染出来"，与真实原因无关；
- 端口 4319 若被上一次异常退出的 dev server 占着，`--strictPort` 会**直接失败**
  （这是有意的：悄悄换端口会让 `webServer.url` 的健康检查探一个空地址）。
  遇到时先确认没有遗留进程，而不是改成 `reuseExistingServer: true`；
- **对比度节点数会随内容漂移**：`color-contrast` 的命中数取决于"页面上有几条
  状态徽章"，桩数据一变就可能多一个节点。登记表刻意留了一点余量（上限），
  但**如果它开始因无关的数据改动而红，正确的做法是把桩数据固定住，
  而不是把上限调大**。

### 排查用的一手线索

`e2e/a11y-fixtures.ts` 里的桩会把**每个 `/api` 请求**记进 `A11yStubLog.served`，
并把浏览器控制台的 error/warning 记进 `consoleErrors`。要逐条打到测试输出，
把该文件里的 `const TRACE = false` 改成 `true` 再跑一次即可
（不做成环境变量：`tsconfig.json` 的 `include` 含 `e2e` 而 `lib` 里没有
`@types/node`，写 `process.env` 会让 `npm run build` 报 TS2580）。

这套线索真的用上过一次：8 worker 并发时"页面卡在加载中"，
`served` 立刻显示**所有接口都回了 200**，于是问题被定位到开发服务器吞吐，
而不是接口或前端代码。

---

## 7. 与 CI 的关系

`.github/workflows/ci.yml` 的 `frontend` job 里，在 `npm run e2e` **之后**加了一步
（**只加了一步，没有改动任何现有 job 的结构**）：

```yaml
- name: Playwright a11y audit (axe-core, advisory)
  continue-on-error: true
  timeout-minutes: 10
  run: npm run a11y
```

> 同一份改动还让 `npm run e2e` 变成 `playwright test --project=chromium`
> （= 功能层 10 条，**不含**审计）—— 这是"审计单独一个 project"的必要配套：
> 不加 `--project` 时 Playwright 会把两个 project 都跑（共 20 条，实测也能过，
> 只是想跑功能层的人不该被塞进 10 条审计）。想两者一起跑用 `npm run e2e:all`。

**为什么是建议性（advisory）而不是阻断性：**

1. 审计当场发现了一批真实违规，修复属于后续的 5.9 修复轮。现在设成阻断，
   CI 会**长期常红**，而常红的门禁只有一个结局：被人加 `|| true` 或删掉 ——
   那比没有门禁更糟，因为它制造"我们已经在管无障碍了"的假象
   （这与本仓库 `security-scan` job 的取舍是同一个理由）。
2. `frontend/src/**` 正在被并行的 CSS 迁移改动。审计对**对比度**与**类名**敏感，
   迁移期间更容易因无关改动变红，而那种红的指向不是"这次提交引入了缺陷"。

**它仍然是有信号的一步**（不是 `|| true` 那种装饰）：审计本身会把未登记的违规
判为失败，所以这一步在 CI 日志里会明确显示"当前是否有**新**的无障碍回归"。
`continue-on-error` 只影响"是否让整个 job 变红"，不影响它跑、也不影响它报。

**升级成阻断的前置条件**（写在这里避免"以后再说"）：

1. 5.9 修复轮把 `REGISTRY` 清空或降到可接受集合；
2. `frontend/src/**` 的 CSS 迁移结束。

两条都满足后，把那一行 `continue-on-error: true` 删掉即可 —— 门槛本身已经在
测试里了，不需要另写脚本。
