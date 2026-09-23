# 前端视觉与信息架构现状审计

> **这份文档只回答"现在有什么问题"，不含方案。** 方案与视觉方向另起文档。
>
> | 项 | 内容 |
> |---|---|
> | 审计时间 | 2026-09-23 |
> | 范围 | `frontend/src`（33 个 `.css` / 90 个 `.tsx` / 65 个 `.ts`）+ `index.html` + `package.json` |
> | 方法 | 只读 grep / 聚合统计 + 一次性 Playwright 截图探针（22 张真实渲染截图，桩数据非空） |
> | 口径 | 统计**剔除注释**与 `*.test.*`；未跑构建与测试；未改动任何代码 |
> | 未核实项 | 各节末尾单独标注 |

## 0. 一句话诊断

**这个前端不是"丑"，是"三套逻辑各自生长"。**

设计令牌层（`styles/base.css`）在**间距与缓动**上真的收口了，但**颜色、图标、字号、几何**四层各有 40%~60% 的量绕过它；
绕过的方式还很一致：`.tsx` / `.ts` 里自建色表、canvas 里直接写 hex、图标用 Unicode 字符顶上。
结果是——**同一个语义在不同页面长得不一样，而 a11y 修复压深过的旧色值仍在字面量里活着**。

需要先讲清一件事：**底层工程是扎实的，这次重做不需要推倒架构。**

## 1. 已经具备的基础（本次重做要保住的东西）

| 项 | 实证 |
|---|---|
| 三层样式结构有规范、有产物级门禁 | `frontend/docs/css-convention.md`；`scripts/verify-built-css.mjs`（级联/死代码/悬空动画三向自检） |
| a11y 已达标并常驻 | `npm run a11y` 26 场景 / 0 违规 / 登记表 0 条（`frontend/docs/a11y-audit.md`） |
| 路由全懒加载、巨型页面已拆分 | `App.tsx:30-47` 18/18 lazy；三页拆分后最大 263 行 |
| 三态组件已被多数页面复用 | 13/18 页完整复用 `EmptyState` / `ErrorDisplay` / `LoadingSpinner` |
| 卡片类型色已单一数据源 | `utils/labels.ts` 的 `cardTypeColors` 被 5 处 import（F-28 真落地了） |
| 间距与缓动确实走令牌 | 间距 557/915 = 61%；缓动 45/48 = 94%（唯一真正收口的维度） |

## 2. 视觉符号层（本次重做的核心）

### 2.1 没有图标系统 —— 5 条渠道并存，同一个语义有不同字形

| 渠道 | 规模 | 代表 |
|---|---|---|
| emoji | 8 处 / 5 文件 | 📖 `pages/notedetail/MarkdownReader.tsx:74`、📂 `pages/Projects.tsx:83`、⭐ `pages/KnowledgeCards.tsx:383` |
| 内联 `<svg>` | 8 个 / 5 文件 | `components/EmptyState.tsx:17`、`pages/Login.tsx:63`、`pages/NotesList.tsx:171` |
| **Unicode 符号当图标** | **19 处** | ▶ 折叠 ×4、✕ 关闭 ×4、● ⚠ ⋯ ⤢ − ↔ ＋ |
| CSS 绘制 | 18 处 | `components.css:80,98,107`、`learning.css:107,121,130,143` |
| 图片文件 | **0 处** | `frontend/public/` 是空目录 |

- **没有图标组件，也没有图标库**：`package.json:40-50` 依赖里没有任何图标包；`components/` 23 个文件无一 Icon 组件；glob `**/*Icon*` 0 命中。
- **同一个"关闭"，同一个文件里两个字形**：`components/Toast.tsx:68` 用 `✕`，`Toast.tsx:208` 用 `×`。
- **加号两种**：`projects/NewProjectForm.tsx:77` 全角 `＋` vs `graph/GraphCanvas.tsx:136` ASCII `+`。
- **搜索图标只有一处**：`NotesList.tsx:171` 有放大镜 SVG，而 `KnowledgeCards.tsx:255`、`GraphToolbar.tsx:71`、`QuestionSets.tsx:182` 的搜索框**没有图标**。
- **"删除 / 添加"没有任何图标**，全靠纯文字标签（`NotesList.tsx:381`、`Trash.tsx:239`、`CardDetail.tsx:145`…）——所以这是"有/无图标"的不一致，比"符号不同"更难察觉。
- **侧边栏 13 个图标全是硬编码 Unicode 码点**（`components/Sidebar.tsx:22-54`）：`\u2302` ⌂ 仪表盘、`\u2618` ☘ **今日学习**、`\u21BB` ↻ 卡片复习、`\u25B7` ▷ 今日资料、`\u25A3` ▣ 项目、`\u2713` ✓ 学习评估、`\u25C9` ◉ 学习目标、`\u2630` ☰ 笔记列表、`\u2672` ♲ 回收站、`\u25C8` ◈ 知识卡片、`\u25CE` ◎ 知识图谱、`\u2753` ❓ 问答、`\u2611` ☑ 问题集。
  - 其中 **▷ ▣ ◉ ◈ ◎ 五个几何图形彼此几乎无法区分**，语义完全靠文字；☘ 三叶草表"今日学习"、❓ 是**彩色 emoji**（与其余单色符号不同源）；✓ 与 ☑ 都像勾选框却分指"学习评估"与"问题集"。
  - 渲染容器是 **20×20 盒里的 16px 字符**（`Sidebar.module.css:250-258`），跨平台字形差异极大。
- **自绘 SVG 骨架内部也不齐**：同一 24 viewBox 渲染成 **18×18**（`pages/Auth.module.css:157-158`）与 **16×16**（`pages/NotesList.module.css:48-49`）→ 视觉线宽 1.5px vs 1.33px；`ErrorDisplay.tsx:29,32` 同文件里 `strokeWidth` 2 与 2.5 并存。

### 2.2 颜色有"第二个数据源"，a11y 压深的旧值仍在字面量里活着

**硬编码色值 272 处 / 146 个不同值**（`.css` 150 / `.tsx` 74 / `.ts` 48）。

| 问题 | 证据 |
|---|---|
| **a11y 修复没同步到字面量** | `base.css:23-30` 把三级文字从 `#9a9ab0` 压深到 `#6f6f8a`，但旧值仍硬编码 **5 处**：`graph/GraphCanvas.tsx:103,113`、`graph/GraphSidebar.tsx:101`、`graph/types.ts:18`、`graph/drawLink.ts:50` |
| 同上（成功色） | `base.css:35-38` 把 `#2d8a56` 压深到 `#25714a`，旧值仍在 **4 处**：`graph/types.ts:20`、`StatCard.module.css:67`、`cardreview/ScheduleFeedback.tsx:42`、`KnowledgeCards.tsx:42` |
| **同一"掌握度"两套色阶** | `pages/KnowledgeCards.tsx:39-43` 用 `#c0392b / #c9a959 / #2d8a56`（**全是旧值**）vs `utils/labels.ts:106-109` 用 `#c0392b / #8f7020 / #25714a`（新值）——前者含 `#c9a959`，而 base.css 注释判定它白底只有 2.25:1 |
| ts 侧自建色表 ≥12 处，**无门禁** | `utils/labels.ts:72,87,106,120,200,252,286`、`graph/types.ts:17`、`KnowledgeCards.tsx:39`、`TodayLearn.tsx:571`、`Toast.tsx`、`utils/markdown.ts:114`。**该文件自己写明**（`labels.ts:183-187`）：「颜色是字面量，不读 CSS 变量…`--color-success` 必须**手工**保持一致…但它没有门禁守着」 |
| canvas 绘制 28 处全绕过令牌 | `knowledgegraph/drawNode.ts` 14、`renderMinimap.ts` 5、`graph/types.ts` 4、`GraphCanvas.tsx` 3、`drawLink.ts` 2；`drawNode.ts:71` 的 `rgba(201,169,89,0.15)` 与 `base.css:92` 的 `--shadow-glow` **同值不同源** |
| Tailwind 灰 `#6b7280` 13 处 | 不在令牌任何一档：`GraphSidebar.tsx:136,182`、`NodeInspector.tsx:30,47`、`drawNode.ts:47`、`renderMinimap.ts:91`、`labels.ts:121`… |
| 近似色一簇簇地长 | 浅蓝底 5 个几乎无法分辨的值：`#eef2ff` / `#e8f0fe` / `#e3f2fd` / `#e0f2fe` / `#dbeafe`；红 7 个值；金/琥珀/警告 ≥13 个值 |

### 2.3 "渐变"是这个项目的视觉签名，但两处最重要的渐变在色彩上不成立

`.gradient-text`（`components.css:355-360`）用 `--gradient-primary` = `linear-gradient(135deg, #1a1a2e, #0f3460)`（`base.css:58`）。
**这两端的相对亮度比只有 1.37:1** —— 在 135° 渐变上肉眼几乎不可分辨。而它被用在：
- **几乎每个页面的 h1**（`className="heading-serif gradient-text"`，7 个页面：`QuestionSets.tsx:169`、`KnowledgeCards.tsx:212`、`DailyMaterials.tsx:454`、`TodayLearn.tsx:364`、`Review.tsx:321`、`QA.tsx:237`、`GraphToolbar.tsx:59`）
- **主按钮背景**（`.btn-primary`，`components.css:26-30`）

代价却是实打实的：`-webkit-text-fill-color: transparent` 让这些标题在**打印**与**强制高对比度模式**下存在不可见风险。

**另有 5 个零用户的设计资产**：`.gradient-text-gold`（`components.css:362`）、`.glass-effect`（`:369`）、`--gradient-accent`、`--gradient-warm`（`base.css:60-61`）——定义了从未被使用。

### 2.4 中文字体依赖大陆连不上的 CDN

`index.html:11` 从 `fonts.googleapis.com` 加载 `Noto Serif SC`（400/600/700）。
而 `--font-serif`（`base.css:74`）是**有真实用户**的：18 个页面标题 + 侧栏 Logo（`Sidebar.module.css:83`）+ 统计数字（`StatCard.module.css:86`）+ 认证页（`Auth.module.css:116`）+ 评估页（`LearningAssessment.module.css:107,257`）。
其回退链是 `'Source Han Serif SC', 'SimSun', serif` —— 在 Windows 上就是**宋体**，小字号渲染质量差。
> 本审计的 22 张截图正是"第三方域被 abort"状态下拍的，也就是**国内用户实际看到的观感**：路径 `frontend/shots/`。

### 2.5 没有暗色模式，却引入了一套深色代码主题

全仓 grep `prefers-color-scheme|data-theme|toggleTheme|isDark|darkMode` → **0 命中**；令牌层只有一套 `:root`（`base.css:6-120`），**无主题切换入口**。
唯一"暗"的地方是 `pages/NoteDetail.tsx:22` 引入的 `highlight.js/styles/github-dark.css` —— 在 `#faf9f7` 的宣纸底上硬套深色代码块，不是主题，是一处孤立冲突。

### 2.6 令牌层自身的完整性

**62 个令牌里 11 个零引用、13 个仅 1 处引用。**零引用的包括：

```
--color-primary-rgb  --color-accent-rgb   （css-convention 说"专供拼 alpha"，实际没人用）
--graph-ink  --graph-ink-faint  --graph-paper-deep   ← "水墨丹青"图谱的三个核心令牌
--gradient-accent  --gradient-warm
--radius-xl  --ease-out-quart  --ease-in-out  --color-surface-elevated
```

- `--graph-paper`（`#f5f0e4`）的值被**手写**成 `rgba(245, 240, 228, 0.92)`（`graph/Graph.module.css:293,339`），而不是拼 alpha——**图谱与主令牌层的连接是断的**。
- `--radius-full` 是为"`9999px` 手写了 14 处"而造的（`base.css:82-85`），**实测 `9999px` 仍手写 22 处、令牌只被引用 1 次**；注释已过期，这轮收口实际是倒退的。
- `box-shadow` 只有 39% 走令牌，23 处字面量里有 **20 个各不相同的定义**，同一"浮起"语义的 alpha 有 **0.06→0.4 十档**。

### 2.7 字号没有 scale

**`font-size` 34 个去重值 / 440 处，没有任何 `--font-size-*` 令牌。**
它不是模数级数，是"每次挪 0.05rem"的手调档：`0.6 / 0.65 / 0.7 / 0.75 / 0.78 / 0.8 / 0.8125 / 0.85 / 0.875 / 0.9 / 0.95 / 1.0 / 1.05 / 1.1 / 1.125 / 1.15 / 1.17 / 1.2 / 1.25 / 1.3 / 1.35 / 1.5 / 1.75 / 2`。
其中 `0.8rem` 出现 **104 次**、`0.75rem` 65 次、`0.875rem` 60 次 —— 而 `0.75 / 0.8 / 0.8125` 在同一屏里肉眼不可分。

## 3. 信息架构层

### 3.1 核心功能的导航入口缺失

18 个登录后页面里 13 个有侧边栏一级入口，但：

| 页面 | 状况 | 证据 |
|---|---|---|
| **答题复习 `/review`** | **完全没有导航项，且进入后侧边栏零高亮** | `App.tsx:142`；`Sidebar.tsx:22-54`；高亮判定 `Sidebar.tsx:89-92` 用 `startsWith` 命中不了 `/review`；入口只有 3 处页内按钮 `Dashboard.tsx:403`、`ReminderBanner.tsx:110`、`CardReview.tsx:197` |
| **上传 `/upload`** | 无导航项，只有「笔记列表」行右侧一个 **`!collapsed` 才渲染**的「+」 | `Sidebar.tsx:152-159` |
| **快速复习 `/review/quick/:id`** | 唯一入口在笔记详情头部，且带状态条件 | `NoteDetailHeader.tsx:125-132` |

且 `/review` 的完成页**没有任何返回控件**（`Review.tsx:238-282`）——进去了既不知在哪，也没给出路。

### 3.2 术语漂移：同名异物 + 同物异名

| 类型 | 实例 | 证据 |
|---|---|---|
| **同名异物** | 「资料」= 笔记角色筛选（`NotesList.tsx:52,215`）**也是**文件夹里的文件（`DailyMaterials.tsx:3,455`） | 两套数据模型与操作都不同 |
| | 「卡片」= 知识卡片 / 答题卡片 / 统计卡片 / 核心卡片 | `KnowledgeCards.tsx:213`、`QuizAnswerCard.tsx:2`、`StatCard.tsx:2`、`DeleteNoteDialog.tsx:112` |
| | 「问题」= 题库题目 / 学习评估的开放性问题 | `QuestionSets.tsx:170` vs `LearningAssessment.tsx:5` |
| **同物异名** | 资料 ↔ 文件 ↔ 文档 ↔ 笔记 | `Upload.tsx:403`「上传学习资料」vs `Sidebar.tsx:156`「上传资料」vs `Dashboard.tsx:169`「上传新资料」vs `QuestionSets.tsx:4`「每份文档」 |
| | 题目 ↔ 问题 ↔ 问答题 ↔ 题（**同一页面内**） | h1「问题集」`QuestionSets.tsx:170` vs「搜索**题目**内容」`:182` vs「暂无**题目**」`:247` vs 文件头「所有**问答题**」`:3` |
| | 归档（archived）在界面上叫**审阅 / 已审阅 / 取消审阅** | `NotesList.tsx:53`、`NoteDetailHeader.tsx:158`、`api/notes.ts:140` |
| | 「每日推荐任务」与「今日学习目标」同物两名 | `TodayLearn.tsx:377` vs `Dashboard.tsx:222` |

### 3.3 视图状态不进 URL

3 处页面内 tab / 筛选全是本地 state：`KnowledgeCards.tsx:76`、`NotesList.tsx:48-57`、`ViewModeTabs.tsx:28-49`。
后果：刷新丢失、不能分享、不能回退。
另：**全站无面包屑**（`breadcrumb` 0 命中）。

### 3.4 入口重复与断头路

- **上传两套实现、能力不一致**：`/upload` 两阶段可重命名 + PDF 裁剪（`Upload.tsx:174,274`）vs `/daily` 页内直传（`DailyMaterials.tsx:342`）做不到。上传入口共 **4 处**。
- **复习入口 4 处且职责重叠**：`/review`、`/today`、`/review/cards` 互相进入。
- **问答两套**：`/qa` 整页 vs 笔记内浮层（`AnnotationAskPanel.tsx:9`）。
- 404 页返回按钮是 `<a href="/">`（`App.tsx:71`）——整页刷新而非 SPA 导航。

## 4. 展现形式层

### 4.1 页面宽度六档并存

根因：全局容器的 `max-width: 1200px`（`components.css:169-173`）在登录后被**显式改成 `none`**（`App.module.css:54-57`），宽度于是完全由各页内联 `maxWidth` 决定：

| 宽度 | 页面 |
|---|---|
| 600px | `CardReviewSummary.tsx:37` |
| **640px** | `Upload.tsx:398` |
| **700px** | `Review.tsx:306`、`TodayLearn.tsx:311`、`QuickReview.tsx:273` |
| **760px** | `CardReview.tsx:142` |
| 800px | `QA.tsx:235` |
| 960px | `LearningAssessment.tsx:346` |
| **全宽（无限制）** | Dashboard、NotesList、Trash、KnowledgeCards、QuestionSets、DailyMaterials、Projects、LearningGoals、KnowledgeGraph、CardDetail、NoteDetail |

1920px 屏上内容区有效宽约 **1616px**，而 Review 正文 **700px** —— 同层级内容页之间差 2.5 倍。

### 4.2 h1 五档字号，且"渐变标题"用不用不一致

1.25rem（`NotesList.tsx:155`）｜1.5rem（12 个页面）｜1.75rem（`.assessment-title`，`LearningAssessment.tsx:349` + `ProjectsHeader.tsx:8` 共用**一个全局类**）｜2rem（`Dashboard.tsx:157`、`LearningGoals.tsx:193`）｜认证页自有。

> ⚠️ **复核修正（批次 C1 执行时）**：本行原文写的是"借用别家**模块**的类名"，实测不成立 —— `.assessment-title` 定义在**全局** `styles/assessment.css:34`，两个页面各用一次。以代码为准。
**没有"页面标题"组件**，每页内联写 `<h1 style={{fontSize}}>`；带 `gradient-text` 的 7 处、不带的 3 处。

> ⚠️ **复核更新（2026-09-23，批次 C1 执行后）**：已新建 `components/PageHeader.tsx`，**18/18 个页面的 h1 全部改用它**，统一为 `--text-xl`(1.5rem) + 衬线 + 600 + 字距 0；探针实测 18 个业务页的 `main h1` 全部 `font-size=24px`。顺带删掉了 `.assessment-header` / `.assessment-title` / `.assessment-subtitle` 三个全局类（后者曾是页面上**最后一处真渐变标题**）。

### 4.3 8 套对话框各写各的，焦点陷阱零实现

| # | 位置 | 遮罩 | z-index | `role="dialog"` |
|---|---|---|---|---|
| 1 | `DeleteNoteDialog.tsx:13-31` | `rgba(0,0,0,.5)` | 1000 | ❌ |
| 2 | `VersionHistory.tsx:173-197` | `.5` | 1000 | ❌ |
| 3 | `LinkManagerModal.tsx:60-84` | `.5` | 1000 | ❌ |
| 4 | `Trash.tsx:259-277` | `.5` | 1000 | ❌ |
| 5 | `LearningGoals.tsx:305-323` | **`.4`** | 1000 | ✅ **全站唯一** |
| 6 | `SelectionMenu.tsx:27-31`（popover） | 无 | 1000 | ❌ |
| 7 | `NoteAskPanel.tsx:295`（popover） | 无 | 未核实 | ❌ |
| 8 | `Toast.tsx:140`（层） | 无 | 未核实 | ❌ |

- **焦点陷阱 grep `focusTrap|focus-trap|trapFocus` = 0 命中**；**Esc 关闭只有 2 处**（`LearningGoals.tsx:175` 是真对话框，`DailyMaterials.tsx:332` 是重命名输入）。
- `DeleteNoteDialog.tsx:12` 自述「与 NoteDetail 关联资料弹窗保持一致」——靠**人工复制常量**保持一致。
- **`window.confirm()` 残留 14 处**（破坏性二次确认）：`CardDetail.tsx:71`、`CleaningPanel.tsx:74,105`、`VersionHistory.tsx:153`、`KnowledgeCards.tsx:182`、`DailyMaterials.tsx:248`、`LearningGoals.tsx:145`、`useNoteActions.ts:71,147`、`useNoteAnnotations.ts:87`、`useProjects.ts:132,199`、`useGraphMutations.ts:159,195`。
  > ⚠️ `Projects.test.tsx:822-824` **把 `window.confirm(` 的存在写成了断言** —— 即当前状态是"有意保留"的，改造时必须同步改测试。
- `alert()` / `prompt()` 已清零（只剩 `Toast.tsx:16` 的历史注释）。

> ### ⚠️ 复核更新（2026-09-23，批次 D2 / D3 前半执行后）
>
> 上面这一节是本审计**当时**的记录，其中多条已被整改批次改掉。**以代码为准**：
>
> | 项 | 审计时 | 现在 |
> |---|---|---|
> | 表格第 1–5 套 modal | 5 套各写各的（遮罩 `.5`/`.4`、z-index 手写 `1000`） | **全部迁到 `<Dialog>` 基座**（批次 D2）：遮罩统一 `.5`、层级 `--z-modal`、面板几何统一 560px/85vh，并获得焦点陷阱 / Esc / 遮罩关闭 / aria 三件套 / 滚动锁 / 焦点归位 |
> | 第 6–8 套（`SelectionMenu` / `NoteAskPanel` / `Toast`） | popover 与提示层 | **刻意不迁**（形态不同，强行统一会破坏定位逻辑）；迁移后 `src/**` 里唯一还活着的 `zIndex: 1000` 就是 `SelectionMenu.tsx:31` |
> | 焦点陷阱 | `grep` 0 命中 | 基座内置（Tab / Shift+Tab 循环），24 条 `Dialog.test.tsx` 用例覆盖 |
> | `role="dialog"` | 全站 **1 处** | 基座统一提供 |
> | Esc 关闭 | 2/8 处 | 基座统一提供；`LearningGoals` 自己那份 document 级监听已删除（原位留墓碑） |
> | `window.confirm()` | **14 处** | **7 处**，只剩 4 个 hook 文件（`useNoteActions` ×2 / `useGraphMutations` ×2 / `useProjects` ×2 / `useNoteAnnotations` ×1）——组件内 7 处已在批次 D3 前半换成 `ConfirmDialog` |
> | `Projects.test.tsx:822-824` 那条警告 | 说它把 `window.confirm(` 的存在写成了断言 | **已过期**：BB.8 收尾时那两处已改成裸 `confirm`，该测试断言的是 hook 而非组件（D3 前半实测，35 条测试全绿） |
> | 额外补回的一处语义 | — | D2 迁移时基座的 `title` 只吃 string，丢了「彻底删除」「清空回收站」的红字标题；已由 D2 收尾批加可选 `titleTone='danger'` 补回，**且只给这 2 处用**（软删除的「移入回收站」刻意不加） |

### 4.4 分页几乎不存在，但全量数据进了内存

- **分页 UI 只有 1 个页面有**：`NotesList.tsx:396-430`。
- **全量进内存 7 处**：`KnowledgeCards.tsx:86`（999）、`useNoteDetailData.ts:101`（999）、`useAddNotesPanel.ts:41`（999）、`QuestionSets.tsx:81-100`（`MAX_PAGES=100` × 100 = **理论 10000 条**，全进内存后再前端过滤）、`Trash.tsx:45`（接口**不接受分页参数**，无上限）、`Upload.tsx:135`/`useNoteLinks.ts:65`/`LearningAssessment.tsx:100,138,154`（各 100）、`CardDetail.tsx:41-42`（**硬编码 20 后前端 filter，超出静默丢失**）。
- `IntersectionObserver` / `加载更多` / `hasMore` / 虚拟滚动 **全部 0 命中**；长 Markdown 一次性全量解析（`NoteDetail.tsx:172`）。
- **`<table>` 全站 0 使用**。

### 4.5 同类数据三种形态

| 数据 | 形态 | 证据 |
|---|---|---|
| 知识卡片 | 320px 自适应网格 / 纵向列表 / 手风琴分组 | `KnowledgeCards.tsx:332-334` vs `CardDetail.tsx:290` vs `QuestionSets.tsx:250` |
| 「集合页」 | 单列卡片 vs 自适应网格（阈值 **340 vs 320**，且都写在 tsx 内联样式里） | `Projects.tsx:96` vs `KnowledgeCards.tsx:334` |
| 答题流程 | 700px vs **760px** | `Review.tsx:306` vs `CardReview.tsx:142` |

### 4.6 搜索 / 筛选 / 排序

- **防抖 3/4**：`NotesList.tsx:187-190` 无防抖（effect `:87-90` 依赖 keyword ⇒ **每击键一次请求**），另 3 处有 300ms。
- **同一枚举三种控件**：「笔记角色」在 `NotesList.tsx:208-225` 是 pill 按钮组、在 `NoteDetailHeader.tsx:204-224` 是 `<select>`、在 `Upload.tsx:676` 又是 pill；`KnowledgeCards.tsx:231-248` 还**用内联样式手写了一遍同款 pill**。
- **排序入口 0 个**（四处硬编码 `.sort()`）。
- **5 个列表既无搜索也无筛选**：Trash、Projects、LearningGoals、CardDetail、Dashboard 最近笔记。

### 4.7 三态有 5 个页面在手写

完整的 13/18；`Review.tsx:228,285`、`QA.tsx:266-276`（无重试）、`Projects.tsx:76-89`（**手写 spinner + 📂 emoji 代替插画，复制了 `LoadingSpinner` 的 DOM**）、`LearningAssessment.tsx:379,410,520`、`Upload.tsx:506` 各写各的。
6 个**复用组件内部**也有裸三态（`VersionHistory.tsx:216,229,239`、`DeleteNoteDialog.tsx:98`、`LinkManagerModal.tsx:88`、`ContentArea.tsx:117`、`CleaningPanel.tsx:130`）。
3 处静默 `return null`（`CardReview.tsx:132`、`Review.tsx:297`、`QuestionSets.tsx:252`）；2 处只有 `console.error`（`LearningAssessment.tsx:141,169`）。

## 5. 改这一轮时要绕开的已知地雷（项目自己记录的）

| 地雷 | 出处 |
|---|---|
| **改外观必须"单开一轮 + 带截图对比"**，不能混在搬家/统一里 | `frontend/docs/css-convention.md` §4 雷区 4 |
| `@keyframes` 动画名会被 CSS Modules 一起哈希 → 模块内必须复制动画体并改名 | 同上 §3 雷区 1 |
| 类名哈希后 `responsive.css` 里的选择器永远选不中 → 响应式规则必须跟组件走 | 同上 §3 雷区 2 |
| `verify-built-css.mjs` 的 `CASCADE_PAIRS` / `ORDER_PAIRS` / 三张登记表：**新增与全局类打架的模块规则时必须登记** | 同上 §7 |
| 有一条窄屏 KaTeX 字号规则**从未生效**，登记为"已知、故意留着"；让它生效属于改外观 | 同上 §7 已知盲区第 4 条 |
| `git worktree` + `node_modules` junction 会**清空真实 node_modules**；改基线推荐用 `dist` 快照 + `vite preview` | 同上 §5 雷区 21 |
| Playwright `webServer` 用 `stdout/stderr: 'pipe'`，受限沙箱下会 `spawn EPERM`（本次审计实测） | 本次审计 |

**门禁（AGENTS.md §5 要求）**：`npm run lint` / `npm test` / `npm run build` / `npm run e2e` / `npm run a11y`，另有产物级 `node scripts/verify-built-css.mjs`。

## 6. 问题优先级汇总

| 层 | 高 | 中 | 低 |
|---|---|---|---|
| 视觉符号 | 无图标系统（5 渠道）；颜色第二数据源 + 旧值存活 9 处；渐变签名不成立；字体走 Google Fonts | 令牌 11 个零引用；字号无 scale；box-shadow/圆角 60% 不走令牌；无暗色模式却有 dark 代码块 | 零用户资产 5 个；折叠箭头 1 处没挂共享类；全角/半角加号 |
| 信息架构 | `/review` 无入口且零高亮；`/upload` 折叠即消失；「资料」同名异物 | 术语漂移 6 组；tab 不进 URL；上传两套；h1/导航名不一致 | 404 用 `<a href>`；分组归类可疑 |
| 展现形式 | 宽度 6 档；8 套对话框 + 焦点陷阱 0；分页 1/12 + 7 处全量进内存；三态 5 页手写 | 同类数据 3 形态；筛选控件 3 种；排序 0；`window.confirm` 14 处 | `<table>` 0 使用 |

## 附录 A · 审计方法与可复现证据

- **三路只读子审计**：视觉符号（图标/颜色/字体/几何/动效/暗色）、信息架构（路由/导航/术语/入口）、展现形式（形态/三态/对话框/分页/表单/搜索/密度），全部带 `文件:行号`。
- **令牌引用统计**（本次实测）：从 `base.css` 提取 62 个 `--*` 定义，对 `src/**`（排除 `generated/`）统计 `var(--x)` 出现次数 → 11 个 0 次、13 个 1 次。
- **截图探针**：`frontend/e2e/shot-probe.spec.ts`（**临时文件**，复用 `e2e/a11y-fixtures.ts` 的非空业务桩），22 张截图落在 `frontend/shots/`，`22 passed (41.5s)`。
  - ⚠️ 该探针与截图目录**均为本次审计的临时产物，不属于门禁**；不需要时删除 `frontend/e2e/shot-probe.spec.ts` 与 `frontend/shots/` 即可。
- **对比度计算**：`#1a1a2e` 相对亮度 0.01156、`#0f3460` 为 0.03401 → 对比度 **1.365:1**。
