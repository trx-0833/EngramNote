# 5.6 CSS 体系重建 —— 进度与剩余迁移计划

> 状态：**机制已建立 + 试点已落地 + 第一批（4 表）已落地 + 第二批（markdown-extras 拆分）已落地
> + 第三批（learning 按归属拆分 + dashboard 收尾/StatCard）已落地
> + 第四批（序 5 `assessment.css` 拆分 + 16 条冲突裁决）已落地
> + 第五~九批（序 8 / 9 / 10 / 12 / 13 全部落地，两张补丁层已清空）已落地**。
> 规范见 [`css-convention.md`](./css-convention.md)；实测证据见
> [`migration-evidence/`](./migration-evidence/)。
>
> 进度：序 **1 / 2 / 3 / 4 / 5 / 8 / 9 / 10 / 12 / 13 已完成**，序 **6 是"有记录的停"**
> （`.adhd-*` 留全局，见 §4.8 与证据 `5.6-07`），序 **7 / 11 按归属部分完成**
> （剩下的都是跨功能共用件或第三方 DOM）。
>
> **`responsive.css` 与 `refinements.css` 都已只剩注释**（`auth.css` / `cleaning.css` /
> `diff.css` 之后又两个）—— 文件**没有删**：`mobile-input-font-size.test.ts` 断言
> `src/styles/*.css` 每一个都被 `main.tsx` 引入，且导入顺序就是级联顺序。
> 现在**没有任何"补丁层"了**：窄屏规则与它们覆盖的基础规则住在同一个文件里，
> 顺序即行为，由 `CASCADE_PAIRS` / `ORDER_PAIRS` 与单测护栏钉住。
>
> 收尾状态（本轮末次实测）：`npm test` **21 files / 276 tests**、`npx tsc --noEmit`
> / `npm run lint` / `npm run build` 退出 0、`npm run e2e` / `npm run a11y`、三个证据脚本
> 全部退出 0；真 Chromium 探针 **3 024 条 computed 属性，未声明差异 0 条**
> （证据 `5.6-12`）。

## 1. 为什么不是一次性迁移

本项目有**两处依赖稳定全局类名**的地方，天真的全量迁移会同时踩坏它们：

1. **`responsive.css`** 用全局选择器覆盖各页面的类名
   （`.graph-search-input` / `.sidebar-item` / `.self-rating-btn` /
   `.note-detail-header` / `.dashboard-two-col` / `.page-header-row` / `.edit-split` …）。
   类名一旦哈希，这些选择器**选不中任何东西**：规则还在、永不生效。
2. **测试按类名查询**（`.sidebar-item` / `.graph-stats-bar-row` / `.fg-node-ids` /
   `.progress-bar-fill` / `.sidebar-mobile-open` …）。哈希后一律失效。

所以迁移只能按"依赖面从窄到宽"分批做，每批都要能机械地证明**没丢东西**。

## 2. 现有 14 个样式表的盘点与迁移顺序

**风险**列的判据是：它被多少切片外的文件使用、以及是否被 `responsive.css`
或测试命中。

| 序 | 样式表 | 主要类数 | 内容 | 风险 | 状态 / 迁移方式 |
|---|---|---|---|---|---|
| 1 | `auth.css` | 7 | 登录/注册卡片 | 低：只被 auth 页用 | ✅ **已完成** → `pages/Auth.module.css`（Login + Register 共用） |
| 2 | `cleaning.css` | 15 | 清洗面板、重复块对比 | 低：`CleaningPanel` 专用 | ✅ **已完成** → `components/CleaningPanel.module.css` |
| 3 | `diff.css` | 13 | 版本对比 | 低 | ✅ **已完成** → `components/DiffView.module.css` |
| 4 | `dashboard.css` | 13 | 仪表盘卡片、统计数字 | 中：`.progress-bar*` 被 **5 处**共用；`.stat-card*` 被 **2 个页面**共用 | ✅ **已完成**（第一批搬走仪表盘私有的 5 条；第三批抽出 `components/StatCard.tsx` 后把 `.stat-card*`(9) + `.stat-number` 的 480px 档搬进 `components/StatCard.module.css`；`.progress-bar*`(3) 按判据留全局，见 §4.3） |
| 5 | `assessment.css` | 22 | 学习评估页 | **高**：`.quiz-question-*` 与 `refinements.css` 打架（14 条属性冲突）；`.knowledge-points-grid` 被 `responsive.css` 命中 | ✅ **已完成**（第四批：**先用真实 Chromium 测出哪一套生效**，再按胜者拆；20 条进 `pages/LearningAssessment.module.css`，另有 `refinements.css` 的 14 条与 `responsive.css` 的 1 条随行；留全局的 3 组见 §4.9） |
| 6 | `markdown.css` | 5 | `.markdown-body` + ADHD 阅读器 | **高**：内容来自 `marked`，没有组件可挂类名 | ⛔ **有记录的停**（第三批核实）：5 条 `.adhd-*` 规则全是 `.markdown-body.adhd-reader-active …` 后代选择器，而 `.markdown-body` 被 3 个不相邻功能共用（判据 2、4）；4 个类名的**全部写入点**在 `src/hooks/useAdhdReader.ts`（classList 字面量），该目录不在本批可改范围。逐处表 + 判据对照见 §4.8 与证据 `5.6-07` |
| 7 | `learning.css` | **18**（迁移前 22） | 问答气泡/上传区/选项/反馈/筛选/分段/搜索/空态/加载 | 中：试点已搬走 4 条；第三批又搬走 6 条（三个单消费者组）；剩 12 条被多个不相邻功能共用 | ⚠️ **按归属部分完成**：`.qa-*`→`pages/QA.module.css`、`.upload-zone*`→`pages/Upload.module.css`、`.search-input-*`→`pages/NotesList.module.css`；`.filter-pill*` / `.segment-*` / `.collapse-arrow*` / `.state-*` / `.spinner` 留全局（§4.8 有逐组的文件数） |
| 8 | `components.css` | 52 | `.btn` `.card` `.badge` `.container` 等公共件 | **最高**：`.btn` 45 处、`.card` 35 处引用；`.container` 被测试查 | ✅ **已完成**（第五批：只搬明确单一归属的 `.note-detail-*` / `.note-list-*` / `.edit-split`，连 `responsive.css` 的 8 条窄屏规则一起；`.btn` / `.card` / `.container` / `.badge` / `.status-*` 等公共件按判据留全局，见 §4.10） |
| 9 | `layout.css` | 27 | 侧边栏 / 顶栏 / 布局骨架 | **最高**：`responsive.css` 命中 12 个类名、测试命中 5 个 | ✅ **已完成**（第六批：侧边栏一整节 + `App` 骨架 + `responsive.css` 同批 12 条 → `Sidebar.module.css` / `App.module.css`；`App.test.tsx` / `Sidebar.test.tsx` 的类名查询改成语义查询或模块导出） |
| 10 | `graph.css` | 47 | 知识图谱 | **最高**：`responsive.css` 命中 12 个类名，测试查 `.graph-search-input` | ✅ **已完成**（第七批：整份 → `components/graph/Graph.module.css` + `responsive.css` 16 条；`KnowledgeGraph.test.tsx` 的 5 处类名查询改用模块导出；`mobile-input-font-size.test.ts` 的 owner 跟着搬） |
| 11 | `markdown-extras.css` | 22 | KaTeX / AI 提问面板 / 引用高亮 | 中：`.katex*` 是第三方 DOM | ⚠️ **部分完成**：`.ask-ai-*`(17) + `.selection-menu`(3) 已搬；KaTeX(4) 与批注高亮(6，含 `citation-flash`) **留全局**（§4.6） |
| 12 | `refinements.css` | 25 | **补丁层**（后加载覆盖前面） | **最高**：它存在的意义就是覆盖别人 | ✅ **已完成**（第八批：`.card-hover` 的胜者值搬回 `components.css`；`.btn:disabled` / `.card` 窄屏 / `.filter-pill*` / `.segment-btn*` 各回归属；`.markdown-editor` / `.edit-toolbar` 进 `EditSplitView.module.css`；12 条属性选择器按实测删除；**文件只剩注释**） |
| 13 | `responsive.css` | 48 | 响应式补丁层 | **最高**：见 §1 | ✅ **已完成**（第九批：`.markdown-body` 一整组回 `markdown.css`；`.btn` / `.btn-ghost` / `.card` / `.page-header-row` / `.heading-serif` / 裸元素重置回 `components.css`；`.filter-pill` / `.segment-btn` 回 `learning.css`；**文件只剩注释**） |
| 14 | `base.css` | 0 类 | **令牌层 + 重置** | — | **保持全局，永不迁移** |

**顺序**：✅ 1–5 → ✅ 8 → ✅ 9 → ✅ 10 → ✅ 12 → ✅ 13（两个补丁层清空）。
剩下的是跨批次事项：死 CSS 清理、跨媒体查询覆盖战与简写/长写竞争的自动检查
（见 §7.0）。

## 3. 试点：`src/components/quiz/`

### 3.1 令牌层

`src/styles/base.css` 的 `:root` 增加：

```css
--radius-full: 9999px;   /* 胶囊/正圆：样式表里手写了 14 处 */
```

并在模块里把 `rgba(15, 52, 96, 0.08)` 换成语义令牌 `--color-primary-light`
（**值完全相同**；`--color-primary-rgb` 存在的唯一目的就是拼 alpha，见计划 F-18）。

**没有**大批新增令牌：字号与 z-index 确实是重复最多的两类
（`0.8rem` 13 处、`1rem` 11 处），但补齐它们要同时改动上百条声明，
diff 无法审阅 —— 那应当**单独一轮**做。

### 3.2 试点切片

| 原位置 | 规则 | 去向 |
|---|---|---|
| `learning.css:54-75` | `.quiz-option` `.quiz-option:hover` `.quiz-option-selected` | `QuizAnswerCard.module.css` |
| `learning.css:79-93` | `.feedback-correct` `.feedback-incorrect` | `QuizAnswerCard.module.css` |
| `responsive.css:299` | `@media(768px) .self-rating-btn` | `SelfRatingButtons.module.css` |
| `responsive.css:401` | `@media(480px) .self-rating-btn` | `SelfRatingButtons.module.css` |

### 3.3 刻意**没有**搬的

| 类名 | 留在哪 | 为什么 |
|---|---|---|
| `.progress-bar` `.progress-bar-fill` | `dashboard.css` | 5 处使用；`ReviewProgress.test.tsx` / `Review.test.tsx` 按它查询 |
| `.btn` `.card` `.btn-primary` | `components.css` | 全局公共件（45 / 35 处引用） |
| `.self-rating-btn` 的**内联样式** | 未动 | 把 tsx 里约 30 行内联样式改成模块类属于**重构**，越出"证明没丢"的范围 |
| `.feedback-pending` | **删除** | 全项目没有任何样式表定义它，是死类名 |

## 4. 第一批：auth → cleaning → diff → dashboard

### 4.1 搬了什么（规则数逐条可查，见证据文件）

| 样式表 | 规则数 | 去向 | 备注 |
|---|---|---|---|
| `auth.css` | 16 | `src/pages/Auth.module.css` | Login / Register 共用一份；含 `@keyframes scaleIn` → `authScaleIn` |
| `cleaning.css` | 16 | `src/components/CleaningPanel.module.css` | 含 `@keyframes cleaning-pulse` → `cleaningPulse` |
| `diff.css` | 21 | `src/components/DiffView.module.css` | 无动画；`diff-line-${type}` 拼串改成查表 |
| `dashboard.css` | 5 / 17 | `src/pages/Dashboard.module.css` | 只搬仪表盘私有部分，见 §4.3 |
| `responsive.css` | 2 | `src/pages/Dashboard.module.css` | 768px 档的 `.dashboard-two-col` / `.dashboard-review-card` |
| **合计** | **60** | | 差集：**逐字保留 60 / 值有变化 0 / 丢失 0** |

三个样式表由此变成**只剩注释的空文件**（`auth.css` / `cleaning.css` / `diff.css`）。
**不能删文件**：`mobile-input-font-size.test.ts` 断言 `src/styles/*.css`
每一个都被 `main.tsx` 引入，而 `main.tsx` 不在本批允许改动的文件范围内。
三个文件的头部注释写明了"哪些类名搬去了哪、新增样式该写哪里"。

### 4.2 动画（计划 §5 雷区 1 的第三次、第四次实例）

`auth.css` 的 `.auth-card { animation: scaleIn }`、`cleaning.css` 的
`.cleaning-progress-bar { animation: cleaning-pulse }` 都是**裸名引用全局
`@keyframes`**。按实测规律，搬进模块后名字会被一起哈希而定义留在
`base.css` → 悬空 → 动画静默消失。

处理：两个 `@keyframes` 的**动画体逐字复制**进各自模块并改名
（`authScaleIn` / `cleaningPulse`）。`base.css` 的原版**不删**。

> 副作用（不是问题，但要记账）：`scaleIn` 与 `cleaning-pulse` 在
> 第一批之后**没有任何样式表用户**了。删它们属于"死代码清理"，
> 与搬家混在一起会让"丢失 0"这条证据同时包含两种语义，故留到单独一轮。

### 4.3 仪表盘为什么是"部分迁移"（对原计划的**更正**）

原计划写的是"拆：`.progress-bar*` 留全局，**其余进模块**"。
**这一条是错的**，实际按"谁在用"分了三组：

| 组 | 规则 | 去向 | 判据 |
|---|---|---|---|
| A 仪表盘私有 | `.dashboard-two-col` `.dashboard-review-card` `.trend-bar`(×3) | `pages/Dashboard.module.css` | grep 只命中 `pages/Dashboard.tsx` |
| B 跨页共用 | `.stat-card`(×9) `.stat-label` | 留 `dashboard.css` | `Dashboard.tsx` **与** `TodayLearn.tsx` 都在用 |
| C 跨功能共用 | `.progress-bar` `.progress-bar-fill`(×2) | 留 `dashboard.css` | 5 处在用 + 2 个测试按类名查询 |

B 组按原计划搬的话，`TodayLearn.tsx` 就得
`import styles from './Dashboard.module.css'` —— **页面模块被另一个页面引用**，
是把"归属"重新搞乱。规范 §4 第 2 条的判据是 grep 出来的**文件数**，
两个页面即命中。**正确的下一步**是先把统计卡片抽成一个真正的
`StatCard` 组件，再连样式一起搬 —— 那是重构、不是搬家，单开一轮。

`.stat-number` 那条 480px 窄屏规则**留在 `responsive.css` 不动**：
类名仍是全局的，规则能正常命中（与 `.dashboard-two-col` 的情况正好相反）。

### 4.4 顺手发现并处理的两处死代码 / 遗留

| 发现 | 处理 | 理由 |
|---|---|---|
| `.duplicate-block-text`（`CleaningPanel.tsx` 挂了它，**14 个样式表都没定义**） | **删除类名**，保留嵌套 div | 与试点轮删 `.feedback-pending` 同一处理；删 div 会改 DOM 层级，超出"纯搬家" |
| `.cleaning-progress` / `.cleaning-progress-bar`（**没有任何 TSX 引用**） | **逐字搬进模块，不删** | 本轮的契约是"证明什么都没丢"。删死规则是"死 CSS 清理"，混进来会让"丢失"同时包含两种语义。已登记在 §7 |
| `--color-bg-subtle` 从未在 `:root` 定义 | **照抄不改** | 三处用者的 fallback 各不相同（`#f7f8fa` / `#f7f7f8` / `#f5f6f8`），收成令牌要改掉其中两个颜色 = 顺手改外观 |

### 4.5 TSX 侧唯一的结构性改动

`DiffView.tsx` 原来用 `` `diff-line diff-line-${line.type}` `` **拼类名**。
哈希后拼字符串必然失效（构建期看不出、规则清单也看不出）。
改成显式查表：

```ts
const LINE_TYPE_CLASS: Record<DiffLine['type'], string> = {
  added: styles.diffLineAdded,
  removed: styles.diffLineRemoved,
  unchanged: styles.diffLineUnchanged,
}
```

查表比拼串强的地方：类型少一个键 `tsc` 直接报错。
（注意**不能**用 `Object.keys(styles)` 之类的枚举 —— 测试环境下
CSS Modules 返回的 Proxy 枚举出来是空的，见规范 §6。）

## 4.5 第二批：`markdown-extras.css` 按归属拆分（序 11）

原表 30 条规则，按"这些 DOM 是谁生成的"分成三组：

| 组 | 规则 | 去向 | 判据 |
|---|---|---|---|
| `.ask-ai-*`（17） | AI 提问浮层的全部外观 | → `components/NoteAskPanel.module.css` | grep 只命中 `NoteAskPanel.tsx`；`responsive.css` / `refinements.css` 0 命中 |
| `.selection-menu`（3） | 批注操作浮层 | → `pages/notedetail/SelectionMenu.module.css` | 同上，只命中 `SelectionMenu.tsx` |
| KaTeX（4）+ 批注高亮（6，含 `@keyframes citation-flash`） | 公式与引用/批注底色 | **留全局** | 作用在 `marked` / KaTeX / `citationJump.ts` 生成的 **HTML 字符串**上，没有组件可以挂类名（规范 §4 第 3、4 条） |

差集：**20 条逐字保留 / 0 值变化 / 0 丢失**。留下的 10 条不是懒，是这三条
判据都过不去 —— 给它们硬造模块只会让"谁生成这段 DOM"更难查。

### 4.6 本批的两个连带影响

1. **`mobile-input-font-size.test.ts` 必须跟着所有权走**（改了 2 处 + 加了 1 个用例，
   见 §6 测试改动表）。`ask-ai-input` 是 iOS 聚焦缩放护栏盯着的两个输入框之一，
   它的 `font-size` 搬进模块后，护栏若仍只扫 `src/styles/*.css`，就会
   **静默失明**：测试全绿而坑重新敞着。
2. **顺手删了两个死类名**：`.ask-ai-sources` / `.ask-ai-source-item`
   在 TSX 上挂着，但全项目没有任何样式表定义它们（与试点删
   `.feedback-pending`、第一批删 `.duplicate-block-text` 同一处理）。

### 4.7 未搬但值得记账

- `.markdown-body .katex { font-size: 1em }`（`responsive.css` 的 768px 档）
  **从未生效**：它与 `markdown-extras.css` 的顶层 `1.1em` 权重相同（0,2,0），
  媒体查询不增加权重，而后者在产物里更靠后（字节 36081 vs 37916）。
  迁移前就如此，与 5.6 无关；属于规范 §7 盲区 4 记的那个检查缺口。
  **不要**顺手改：那会改变窄屏外观，得单独一轮带截图。
- `markdown-extras.css` 的**导入位置仍然是语义的**（必须排在 `responsive.css`
  之后）：虽然 `.ask-ai-input` 搬走了，`responsive.css` 里还有针对
  `.markdown-body .katex-block` / `.katex-display` 的窄屏规则与它同层竞争。

## 4.8 第三批：`markdown.css`（停）+ `learning.css` 按归属拆分 + `dashboard.css` 收尾

### 4.8.1 一个"停"：`markdown.css` 一条没搬

计划 §7.1 事先把它标成"可能是个停"，核实后**确实是**。5 条 `.adhd-*` 规则
**全部**是 `.markdown-body.adhd-reader-active …` 的后代选择器，两半没法分开：

| 事实 | 出处 |
|---|---|
| `.markdown-body` 被 **3 个不相邻功能**使用 | `pages/notedetail/MarkdownReader.tsx`、`pages/notedetail/EditSplitView.tsx`、`components/NoteAskPanel.tsx`（后者还 `document.querySelector('.markdown-body')`） |
| 4 个 `.adhd-*` 类名的**全部写入点**在 `src/hooks/useAdhdReader.ts` | `:36` `classList.contains('adhd-line-marker')`、`:165` `marker.className = 'adhd-line-marker'`、`:216/:217/:274` `adhd-current-block`、`:266` `adhd-block`、`:269/:272` `adhd-reader-active` |
| `utils/markdown.ts` **不**注入这些类名 | grep 0 命中（计划里那个"可能"排除了） |
| `responsive.css` 对 `.adhd-*` 0 命中 | 不存在"补丁层还在命中它" |

三条判据逐条对照 ⇒ 前两条都不成立（没有可替代的语义查询；写入点所在的
`src/hooks/**` 不在本批允许改动的文件范围内），第三条成立 ⇒ **留全局**。
把它硬塞进模块只能写成 `:global(.adhd-block)`，等于一个字符都没被作用域化，
却把"这是全项目约定"藏进页面模块里 —— 规范 §4 末尾定义的"半搬"。
逐处表写在 `markdown.css` 文件头，规则原文留证 `migration-evidence/5.6-07`。

### 4.8.2 搬了什么（21 条，逐条可查）

| 来源 | 条数 | 去向 | 判据（grep 出的文件数） |
|---|---|---|---|
| `learning.css` `.qa-user-bubble` `.qa-ai-card` | 2 | `pages/QA.module.css` | 只命中 `QA.tsx` |
| `learning.css` `.upload-zone` `:hover` `-active` `-active::after` | 4 | `pages/Upload.module.css` | 只命中 `Upload.tsx` |
| `learning.css` `.search-input-wrapper` `… input` `.search-input-icon` | 3 | `pages/NotesList.module.css` | 只命中 `NotesList.tsx` |
| `responsive.css` 480px `.list-toolbar`、`.list-toolbar .search-input-wrapper` | 2 | `pages/NotesList.module.css` | `.list-toolbar` 这个类名**只定义在补丁层**（全项目唯一一处），存在的唯一目的就是这两条窄屏规则 |
| `dashboard.css` `.stat-card`(含 `::before`/`:hover`/4 变体) `.stat-number` `.stat-label` | 9 | `components/StatCard.module.css` | 被 `Dashboard.tsx` + `TodayLearn.tsx` 共用 → **先抽组件**（§4.8.3） |
| `responsive.css` 480px `.stat-number` | 1 | `components/StatCard.module.css` | 类名进模块后，留在补丁层的选择器会永远选不中 |

差集：**21 条逐字保留 / 0 值变化 / 0 丢失**（证据 `5.6-02`）。
动画两条：`slideUp` → `qaSlideUp`、`glowPulse` → `uploadGlowPulse`
（动画体逐字复制，产物里"引用与定义同一文件"实测 0 悬空）。

**留下的 12 条**（`learning.css`）与 **3 条**（`dashboard.css` 的 `.progress-bar*`）
不是懒：`.filter-pill*`（4 个页面 + `refinements.css` 的 `::after` 指示线 +
`responsive.css` 的触控目标）、`.segment-*`（2 个页面 + 同样两层补丁）、
`.collapse-arrow*`（3 个页面）、`.state-*` / `.spinner`（3 个共享组件 + Projects 页，
是与 `.btn` / `.card` 同层的共享 UI 原语）、`.progress-bar*`（5 处在用 +
2 个测试按类名查询）——全部命中规范 §4 第 2 / 5 条。逐组判据写进了
`learning.css` / `dashboard.css` 的文件头（规范 §4 要求"留全局的代价要写在文件头"）。

### 4.8.3 `StatCard`：本批唯一一次重构，理由是判据要求的

原计划（§7.1 序 3）写的就是"先抽 `StatCard` 组件，再连样式搬"。做完之后：

- `components/StatCard.tsx`：`variant` / `value` / `label` 三个 props，
  变体→类名走**显式查表**（`VARIANT_CLASS`），不用 `styles[...]` 动态取值
  —— 测试环境下模块默认导出是 Proxy、`Object.keys(styles)` 是空数组（规范 §6）。
- `components/StatCard.module.css`：D 组 9 条 + 480px 那条，共 10 条。
- 两个调用点各 4 张卡片 → 4 个 `<StatCard />`，**DOM 层级、类名语义、
  每个声明值都逐字不变**（`div > div + div`）。
- 新增 `components/StatCard.test.tsx`（3 个用例）：把结构与"4 个变体
  必须渲染出 4 个不同类名"钉死 —— 计划要求这一步"带视觉核对"，
  无头环境里能做到的等价物就是它。

### 4.8.4 工具这次**抓到了一条真错**（记下来，别把它当成噪音）

第一次提交时我把 `responsive.css` 的 `.list-toolbar .search-input-wrapper`
在模块里"顺手"写成了单类 `.searchInputWrapper`。差集立刻报
**1 条丢失 + 1 条新增**（权重从 (0,2,0) 掉到 (0,1,0)）。
selector 是行为的一部分，不是排版 —— 已改回后代选择器。

同一轮里差集还报过 2 条**假**丢失，两条都是压缩器的等价改写，
已按"逐条写明出处"的规矩补进归一化（规范 §7 第 2 条那份清单）：

| 形态 | 出处（实测） |
|---|---|
| `border-radius: 16px 16px 4px 16px` → `border-radius:16px 16px 4px`（第 4 值省略时取第 2 值） | `.qaUserBubble`，产物 `QA-*.css` |
| `inset: 0` → `top:0;right:0;bottom:0;left:0`（构建器按目标浏览器把简写**降级**成长写） | `.uploadZoneActive::after`，产物 `Upload-*.css` |

### 4.8.5 顺手记账：`glowPulse` 也没有样式表用户了

与第一批之后的 `scaleIn` / `cleaning-pulse` 同样处理 —— **不删**。
它仍可被行内样式按裸名引用（`LearningAssessment.tsx` 就在用 `slideUp`、
`ErrorDisplay.tsx` 在用 `shake` + `fadeIn`），删它属于"死代码清理"，
单独一轮做（§7.0 第 2 条）。

## 4.9 第四批：序 5 `assessment.css` —— 先裁决冲突，再按归属拆

这一批的顺序与前三批相反：**先用真实浏览器把"哪一套生效"测出来**，
再动手搬。原因是这一批的目标文件不是"没人动过的老样式表"，
而是**已经在打架**的那一对（雷区 3）。

### 4.9.1 冲突裁决（16 条，全部实测）

`assessment.css` 与 `refinements.css` 对 `.quiz-question-card` / `-number` /
`-text` 写了 **14 条同名属性的不同值**，`.card-hover:hover` 另有 2 条
（`components.css` × `refinements.css`）。两边权重相同（都是单类），
**谁赢只看 `main.tsx` 的导入顺序** —— 迁移前没有任何人做过这个决定。

**测法**（配方见雷区 3）：仓库里已有 Playwright，就让它当尺子 ——
一次性探针走**真实渲染路径**（登录 → `/assessment` → 已链接对比「开始评估」
→ 切「开放性问题」→ 选资料 →「生成问题」→ hover → focus →「提交答案」），
读 31 个元素的**全部** computed 属性（16 802 条）。

**结论：16 条全部是后加载的补丁层赢**（`refinements.css` 在 `main.tsx`
第 39 行、`assessment.css` 在第 36 行）。于是：

| 组 | 处理 |
|---|---|
| 14 条（`.quiz-question-*`） | 胜者的值随规则进 `pages/LearningAssessment.module.css`；**输的 14 条声明从 `assessment.css` 删除**（删的是永远不生效的死声明） |
| 2 条（`.card-hover:hover` 的 `transform` / `box-shadow`） | 胜者在 `refinements.css`（8 个页面在用它，按规范 §4 第 2 条**留全局**，不进模块）；**输的 2 条从 `components.css` 删除**，原地留注释说明"这两条归补丁层所有，序 12 拆它时再搬回来" |
| 5 条（`.score-summary-number`，跨选择器覆盖） | 补丁层用 (0,2,0) 压掉单类的字号/配色/背景裁剪。**模块里故意保留两条规则**（合并会把权重从 (0,2,0) 降到 (0,1,0)，正是第三批被抓到的那种错），只删单类里被压掉的 5 条 |

差集工具因此新增了**第四类差异**：`resolvedConflicts`（前三是逐字保留 / 值有变化 / 丢失，
见 §7.2）。它把"按实测胜者删掉的输家声明"从"迁移前"一侧摘掉、单独成节列出（谁赢、凭什么），
并**自检每条都真的命中过** —— 写错类名或值会报错退出，
不会变成一条永远绿灯的空声明。逐条表见证据 `5.6-09`。

### 4.9.2 搬了什么、留了什么（判据 = grep 出的文件数）

| 组 | 规则数 | 去向 | 判据 |
|---|---|---|---|
| `.score-bar*` | 9 | `pages/LearningAssessment.module.css` | 只命中 `LearningAssessment.tsx` |
| `.quiz-question-*` | 4 + `refinements` 的 9 | 同上 | 同上 |
| `.score-summary-*` | 3 + `refinements` 的 4 | 同上 | 同上 |
| `.knowledge-points-*` | 4 + `responsive` 的 1（480px） | 同上 | 同上；窄屏规则必须跟着类名走（雷区 2） |
| `.assessment-header` `-title` `-subtitle` | 3 | **留 `assessment.css`** | 学习评估页 **与** `pages/projects/ProjectsHeader.tsx` |
| `.note-select-card` `-checked` `:hover` `input[type=checkbox]` | 4 | **留 `assessment.css`** | 学习评估页 **与** `pages/projects/ProjectNotesList.tsx`；`Projects.test.tsx` 还按类名查询 |
| `input[type="checkbox"]`（裸元素） | 1 | **留 `assessment.css`** | 规范 §4 第 1 条（没有"拥有者组件"的兜底重置） |

差集：**35 条逐字保留 / 0 值有变化 / 0 丢失 / 19 条已裁决删除**（证据 `5.6-02`）。
本批**没有** `@keyframes` 要搬（这一批规则里一条 `animation` 都没有），
`CASCADE_PAIRS` 也**不需要**新增条目：元素上只挂模块类，
没有"全局类 + 模块类并列"的同权重竞争（同理见 §7.3 第三批那一行）。

### 4.9.3 TSX 侧唯一的结构性改动：评分条填充色查表

`renderScoreBar` 原来用 `` `score-bar-fill ${fillClass}` `` 拼类名
（`fillClass` 是 `'score-bar-fill-high'` 之类的字面量）。哈希后拼串必然失效，
改成显式查表 `SCORE_FILL_CLASS`（同 `DiffView.tsx` 的 `LINE_TYPE_CLASS`）。
其余本批元素的 `className` 也一并换成 `styles.*`（`.score-bar*` 4 处、
`.quiz-question-*` 2 处 ×2、`.score-summary-*` 3 处、
`.knowledge-points-*` 3 处）；`.note-select-card*` / `.assessment-*`
**保持字面量**（它们留在全局）。

### 4.9.4 渲染结果"没变"是怎么证的（不是靠眼睛）

同一个探针跑两次：**迁移前**用 `git worktree add --detach <临时目录> HEAD`
（只读 HEAD，不碰工作区 —— 本仓库有并行 agent，`git stash` 会搅进别人的改动）
+ `node_modules` junction + 独立 dev server；**迁移后**跑在当前工作区，
把前一次的 JSON 用 `route.fulfill({ path })` 喂回页面里逐属性比对。

**16 802 条 computed 属性 → 差异 0 条**（页面内比对 + node 侧独立比对，都是 0）。
class 属性按预期变了：**18 个元素拿到哈希类名**（模块生效），
**13 个一字不变**（留全局的 `.assessment-*` / `.note-select-card*`、
元素选择器命中的 `textarea` / `li`、以及全局裁决后的 `.card-hover`）。
探针还顺带在真浏览器里钉了窄屏那条：420px 下 `.knowledge-points-grid`
的 `grid-template-columns` 是**一条轨道**、1280px 下是两条（jsdom 不认 `@media`，
这条只有真浏览器能证）。

## 4.10 第五~九批：序 8 / 9 / 10 / 12 / 13 —— 补丁层的最后五批

五批的差集全部 **丢失 0**；两张补丁层清空后另有**逐条去向审计**（证据 `5.6-11`）
与**真 Chromium 的 computed 对账**（证据 `5.6-12`）。

### 4.10.1 逐批清单

| 批次 | 搬了多少条规则 | 去向 | 随行的响应式规则 | 差集 |
|---|---|---|---|---|
| **第五批（序 8）** | 5（`components.css` 的页面级挂点） | `.note-detail-*` → `pages/notedetail/NoteDetailHeader.module.css`；`.note-list-*` → `pages/NotesList.module.css`；`.edit-split` → `pages/notedetail/EditSplitView.module.css` | `responsive.css` 768px 档 7 条 + 480px 档 1 条 | **13/0/0** |
| **第六批（序 9）** | 23（`layout.css` 的侧边栏整节 + App 骨架） | `components/Sidebar.module.css`（20 个类名，含 `body.sidebarOpenLock`）、`App.module.css`（3 个） | 768px 档 11 条 + 480px 档 1 条 + `:root` 的 `--page-pad-y-*`（4 条声明） | **53/0/0** |
| **第七批（序 10）** | 60（`graph.css` 整份） | `components/graph/Graph.module.css`（47 个类名，整个功能一份模块） | 768px 档 14 条 + 480px 档 2 条 | **71/0/0** |
| **第八批（序 12）** | 7 条规则进模块 + **36 条声明搬家** | `EditSplitView.module.css`（`.markdown-editor` / `.edit-toolbar`）；`components.css`（`.card-hover*` / `.btn:disabled*` / `.card` 窄屏 / 5 个预留类）；`learning.css`（`.filter-pill::after` 等 6 条） | 随规则走 | **7/0/0 + 13 条"实测从未生效"的规则删除** |
| **第九批（序 13）** | **28 条声明搬家**（没有任何类名进模块） | `markdown.css`（`.markdown-body` 一整组 10 条规则）；`components.css`（`.btn` / `.btn-ghost` / `.card` / `.page-header-row` / `.heading-serif` / 裸元素重置）；`learning.css`（`.filter-pill` / `.segment-btn`） | — | **补丁层清空**（`responsive.css` 解析出 0 条规则） |

### 4.10.2 本轮的五个新发现（都进了雷区表与规范）

1. **压缩器会合并"相邻且声明逐字相同"的规则**（esbuild 实测）：`Graph.module.css` 里
   `.graphToolbar{flex-wrap;row-gap}` 与 `.graphToolbarLeft,.graphToolbarRight{同样两条}`
   在产物里合成一条三选择器规则 ⇒ 差集的"同 (上下文+选择器)"这个 key 两条都落空，
   报成 2 条丢失。修法是落空时找**超集规则**（成员集合 ⊇ 本条 + 声明逐字相同），
   并在命中时注明"被压缩器合并"。
2. **选择器组里逗号后的空格被压掉**：源码 `.sidebar,\n  .sidebar-collapsed` 与产物
   `._sidebar_h,._sidebarCollapsed_h` 的文本不同 ⇒ 三条"选择器组"规则被误报丢失。
   已在 `neutralSelector` 里归一（逗号两侧空格无语义）。
3. **`[style*="rgba(0,0,0,0.5)"]` 是死选择器（实测，从"猜想"变成"事实"）**：
   React 走 CSSOM 赋内联值，浏览器把颜色序列化成**带空格**的
   `rgba(0, 0, 0, 0.5)`，不带空格的子串**命中 0 个元素**。13 条规则迁移前就从未生效，
   删它是可证明的空操作（探针输出见证据 `5.6-12`）。计划原来设想的"先给弹窗一个真类名
   再搬"**会改变外观**（那 13 条突然生效），超出"证明什么都没丢"的契约，故不做。
4. **没有 tsx import 的模块不会进产物**：`.link-modal` 等 5 个零引用的预留类若放进
   `LinkManagerModal.module.css`，Vite 不会编译那个模块 ⇒ 规则从产物里消失
   （差集当场报 6 条丢失）。**零引用的类只能留全局**（或先给它们一个真正的挂载点）。
5. **模块之间"同权重、同文件、靠先后"的竞争也是行为**：`Sidebar.module.css` 里
   `.sidebarMobileOpen { transform: translateX(0) }` 一旦排到
   `.sidebar, .sidebarCollapsed { transform: translateX(-100%) }` 前面，
   抽屉就再也滑不出来 —— 而规则清单、冲突统计、动画检查**全都不会响**。
   为此给 `verify-built-css.mjs` 加了 `ORDER_PAIRS`（现有的 `CASCADE_PAIRS` 只覆盖
   "全局类 × 模块类"）。

### 4.10.3 测试改动（本轮两处 + 一处 owner 跟着搬）

| 文件 | 改动 | 理由 |
|---|---|---|
| `App.test.tsx`（4 条用例） | `'sidebar-mobile-open'` / `'.sidebar-overlay'` / `'sidebar-open-lock'` → **语义断言 + 模块导出**：抽屉开合一律先断言汉堡按钮的 `aria-expanded`（用户与读屏能感知的同一份状态），遮罩与 body 锁这两个"没有 ARIA 等价物"的改用 `import sidebarStyles` | 类名进模块后被哈希，字面量查询必然落空。**没有**为了测试把类名留在全局 —— 那会让该类的窄屏规则永远无法随组件搬走（雷区 2） |
| `Sidebar.test.tsx`（6 处） | `.sidebar-overlay` / `sidebar-open-lock` / `sidebar-item-active` / `sidebar-item-action` / `sidebar-item-row` → `styles.*`（结构性判据，无语义替代） | 同上；`getByRole` 本来就是这个文件的写法，能语义化的地方一处没动 |
| `KnowledgeGraph.test.tsx`（5 处） | `.graph-panel` / `.graph-suggestion-card` / `.graph-stats-bar-row` / `.graph-search-result-item` → `graphStyles.*` | 同上。搜索框本来就是 `getByPlaceholderText('搜索卡片...')`（语义查询） |
| `styles/mobile-input-font-size.test.ts` | 图谱搜索框的 `owner` 从 `graph.css` 改成 `components/graph/Graph.module.css` + 加 `moduleClassName` | **收紧而非放宽**：不跟着搬的话，输入框的 `font-size` 一进模块，护栏就再也看不见它（"静默护栏比测试红更危险"） |

### 4.10.4 渲染"没变"是怎么证的（本轮最强的一份证据）

同一个探针跑两次：**迁移前**用 `git worktree add --detach <临时目录> HEAD`（只读 HEAD）
+ `node_modules` junction + **独立 vite 缓存目录**起 dev server；**迁移后**跑在当前工作区，
把上一次的 JSON 用 `route.fulfill({ path })` 喂回页面逐属性比对。

**10 个场景 / 56 个样本 × 54 条 computed 属性 = 3 024 条值，未声明的差异 0 条**；
唯一一条差异是**已声明**的动画改名（`fadeIn` → `sidebarOverlayFadeIn`，动画体逐字复制、
产物内绑定自洽）。class 属性按预期变化（模块类名哈希），留全局的元素一字不变。

探针自身的两个坑（都踩过）：拼后代选择器必须用 `:is()` 包住"迁移前/迁移后"两种写法
（否则 `.a, .b .c` 会把容器自己匹配走，26 条差异全是假的）；
`page.waitForEvent('download')` 必须在触发之前注册。

## 5. 雷区（按危险程度，★ = 第一批新发现）

1. **动画名会被一起哈希（试点实测，最阴）。**
   任何把含 `animation` 的规则搬进模块的地方都要处理：要么把 `@keyframes`
   定义搬进模块，要么别搬这条规则。受影响：
   `learning.css`（~~`slideUp`~~ / ~~`glowPulse`~~ 已在第三批随
   `.qa-*` / `.upload-zone*` 复制成 `qaSlideUp` / `uploadGlowPulse`；
   `fadeIn` / `spin` 仍安全 —— 它们所在的 `.state-*` / `.spinner`
   按判据留全局）、
   `components.css`（`fadeIn` / `slideUp`）、`dashboard.css`（`shimmer`，
   连同 `.progress-bar*` 一起留全局所以暂时安全）、`layout.css`（`fadeIn`）、
   `graph.css`（`graph-spin`）、`markdown-extras.css`（`citation-flash`，
   但那条规则留在全局，所以引用与定义同层，不需要搬）。
   ~~`auth.css`~~ / ~~`cleaning.css`~~ 已在第一批处理完毕，
   第三批的 `QA.module.css`（`qaSlideUp`）/ `Upload.module.css`
   （`uploadGlowPulse`）同理。
   第二批**没有**需要搬的动画：`.ask-ai-*` / `.selection-menu` 里一条 `animation` 都没有。
8. **★ 模块的 CSS 与全局样式表的先后** —— 详见 §5 下方说明，**已根治**。
2. **`responsive.css` 与 `layout.css` / `graph.css` 是同一批类名的两半。**
   拆任何一半，另一半立刻静默失效。必须整批一起动，并同步改
   `App.test.tsx` / `Sidebar.test.tsx` 里按类名查询的断言。
3. **★ `assessment.css` × `refinements.css` 的打架 —— 第四批已裁决（裁决方法本身要留下）**：
   `.quiz-question-card` / `.quiz-question-number` / `.quiz-question-text`
   共 **14 条属性冲突**，`.card-hover:hover` 另 2 条。两个文件都后加载覆盖前者、
   权重相同，所以"哪套生效"**只取决于 `main.tsx` 的导入顺序** ——
   迁移前没有任何人做过这个决定。迁移时**先决定保留哪一套**，再搬，
   而"决定"必须有证据，不能靠读源码推。**配方（20 行，值得照抄）**：

   1. 仓库里已经有真实 Chromium（`npm run e2e` 的 Playwright，`E2E_PORT` 可换端口）。
      写一个**一次性探针 spec**（用完删，别改 `e2e/**` 里别人的文件），
      走**真实渲染路径**而不是"往空页面塞一段 HTML" —— 后者证明不了祖先选择器
      与懒加载 chunk 的注入顺序。`e2e/a11y-fixtures.ts` 的 `installA11yStubs` /
      `loginAs` 是现成的样板（`/api` 按 **pathname** 桩，绝不用子串匹配）。
   2. 读 **computed style** 的全部属性（`getComputedStyle(el)` 逐项导出），
      不是只挑那几条 —— computed 是级联求解后的结果，简写 vs 长写
      （雷区 12 的盲区）会自动体现在解析出来的长写上。
   3. **必须等过渡结束再读**（每个状态变化后 ≥600ms）：第一次实测读到的是
      过渡中间值 —— 非 hover 态的 `box-shadow` 读成 `0 2.7px 9.4px rgba(…,.067)`
      （从 hover 值回落的 64% 处），`.card-hover:hover` 的 `transform` 读成
      `matrix(1,0,0,1,0,0)`（过渡起点），两者都会让人误判成"两条规则都没生效"。
      同理 `setViewportSize` 之后要等一次重排再读窄屏值。
   4. **两侧测法必须一致**才能说"渲染没变"：迁移前那份用
      `git worktree add --detach <临时目录> HEAD` + `node_modules` junction
      起一个独立 dev server（只读 HEAD，不碰工作区 —— 本仓库有并行 agent，
      `git stash` 会把别人的改动一起搅进来）；迁移后把前一次的 JSON 用
      `route.fulfill({ path })` 喂回页面里逐属性比对。
      （探针要落盘 JSON 时**不要**用 `node:fs`：本项目没有 `@types/node`，
      而 `e2e/` 在 `tsconfig.json` 的 `include` 里 ⇒ `tsc` 直接红；
      `playwright test` 的 `download.saveAs(path)` 由 Playwright 落盘，绕开这个坑。）

   **实测结论**：16 条全部是后加载的 `refinements.css` 赢；胜者的值随规则进模块，
   输的声明删除。`.card-hover` 因为被 8 个页面使用（规范 §4 第 2 条）**没有**进模块，
   胜者暂时留在补丁层，输的两条已从 `components.css` 删掉（序 12 拆它时再把值收口）。
   逐条表：证据 `5.6-09`；搬迁差集：证据 `5.6-02`。
4. **`refinements.css` 用内联样式字符串当选择器**（计划 F-18）：
   `[style*="rgba(0,0,0,0.5)"]` 命中 4 个组件的确认弹窗，
   浏览器把 `rgba(0,0,0,0.5)` 重新序列化成 `rgba(0, 0, 0, 0.5)` 就整块失效。
   迁移时应当**消灭这种选择器**（给弹窗一个真正的类），不是搬走它。
5. **`components.css` 的 `.container`** 被 `useReviewKeyboard.test.tsx` 按类名查询；
   `.sidebar*` 被 `App.test.tsx` / `Sidebar.test.tsx` 查询 ——
   迁移时必须同步改测试（优先改成语义查询）。
6. **`mobile-input-font-size.test.ts` 会拒绝任何新增 / 删除的 `src/styles/*.css`。**
   它断言该目录每个样式表都被 `main.tsx` 导入。所以
   ①模块文件只能放组件目录，②令牌只能加在 `base.css`，
   ③**搬空的样式表也不能删文件**（第一批的 `auth.css` / `cleaning.css` /
   `diff.css` 就是三个只剩注释的文件）。
7. **`markdown-extras.css` 在 `responsive.css` 之后加载** —— 这个顺序本身被
   `mobile-input-font-size.test.ts` 断言（防止 `.ask-ai-input` 的窄屏兜底
   被压掉）。迁移时若允许组件 CSS 按需插入 `<head>`，这条断言的前提
   （"级联顺序 = main.tsx 的导入顺序"）就需要重新审视。
8. **★ 模块的 CSS 与全局样式表的先后 —— 已根治（重排 `main.tsx`）。**
   第一批实测：Vite 按模块图顺序产出 CSS，而 `main.tsx` 原来第 4 行就
   `import App`、样式表第 7 行之后才引入 —— **静态引入的组件，其模块 CSS
   排在全部全局样式表之前**（实测字节位置：`Auth.module.css` = 1、
   `base.css` 的 `:root` = 2551、`.btn` = 6555）。

   后果实例：`.authSubmit` 的 `padding / font-size / font-weight / transition`
   被全局 `.btn` 反盖（按钮变小变细）。**文本差集完全看不出来**。

   **修法（已落地）**：把全部 `./styles/*.css` 提到应用组件之前，
   产物顺序 = ①令牌 → ②全局 → ③模块（重排后实测：`:root` @0、`.btn` @4004、
   `_authBg` @48306）。第一轮用的权宜之计 `:global(.btn).authSubmit` 已还原成单类。

   **两道护栏**：`verify-built-css.mjs` 的 `CASCADE_PAIRS`（逐属性算得主；
   反向验证过：改回顺序立刻报 4 个属性得主是 global）+
   `mobile-input-font-size.test.ts` 里"模块层必须排在全局层之后"的新用例。

   ⚠️ **懒加载**的页面/组件本来就不受影响（模块 CSS 自成 chunk、在
   `index.css` 之后注入），所以不要无脑加 `:global()`。
9. **★ 产物里按类名做正则匹配，必须先剥哈希。**
   哈希分隔符是 `_`，属于 `\w`，所以 `\._?authSubmit(?![\w-])` 对
   `._authSubmit_1hcab_41` **一条都匹配不上**。第一批的"级联次序"检查
   第一次跑起来就是这样**假通过**的。
10. **★ 压缩器的等价改写会伪装成"值有变化"。**
   `rgba(255,255,255,.92)` → `#ffffffeb`、`#fffc` ↔ `#ffffffcc`、
   `color: white` → `#fff`、`::after` → `:after`、`content: ""` → `content: ''`。
   差集脚本逐条归一化并写明出处（不归一化会有 6 条假警报把真变化淹掉）。
   另有 `-webkit-user-select` 这类**构建器注入**的声明：加法而非丢失，单独列出。
11. **★ 不要用 `HEAD~N` 记"迁移前"**（第二批踩到）。
    本项目多个 agent 并行改同一个仓库：第二批期间另一个 agent 提交了一个
    **后端**改动，HEAD 往前挪一位，写死的 `HEAD~1` / `HEAD~2` 集体指错，
    三个批次的"迁移前"全指向了迁移之后的版本。
    自检把它报成"解析出 0 条规则"（响亮失败，没有给出错误结论），
    但每来一个无关提交就要重算一遍。现在两个脚本都**按内容**定位：
    从 HEAD 往回找第一个"还含有这批老类名"的提交
    （`lib/css-parse.mjs` 的 `findRecentRev`），与提交顺序完全解耦。
12. **★ 跨属性竞争（简写 vs 长写）现有脚本全都看不见（第三批发现）。**
    实例：`QA.tsx` 的 AI 卡片是 `` className={`card ${styles.qaAiCard}`} ``，
    全局 `.card` 写 `border: 1px solid …`（**简写**，会展开出
    `border-left-*` 三条长写），模块 `.qaAiCard` 写
    `border-left: 3px solid var(--color-accent)`（**长写**）。两条权重同为
    (0,1,0)，谁赢完全取决于**产物里的先后**。

    为什么三道检查都漏掉它：
    - `css-migration-diff.mjs` 按 (上下文 + 选择器 + 属性) 建键 ——
      选择器不同（`.card` vs `._qaAiCard_hash`），根本不配对；
    - `verify-built-css.mjs` 的冲突统计与 `CASCADE_PAIRS` 都按**属性名**
      配对 —— `border` 与 `border-left` 是两个名字，配不上；
    - 两个脚本对"同权重 + 跨文件"本来也只能报"判不了"。

    **实测（临时探针，用完已删）**：jsdom 对**字面值**的 `border` 简写会正确
    展开（对照实验：长写在后 → `3px solid blue`；简写在后 → `1px solid red`），
    但它不解析 `var()`。所以配方是"把 `var(…)` 替换成字面色，读**真实产物**
    的两个 CSS 文件，按 chunk 注入顺序拼起来算 `getComputedStyle`"：

    ```
    index-*.css 在前、QA-*.css 在后（= 真实顺序） → border-left = 3px  （模块赢）
    两个文件对调（反向对照）                     → border-left = 1px  （.card 赢）
    ```

    当前顺序是对的：懒加载页面的模块 CSS 由 `__vitePreload` 在 `index.css`
    **之后**注入。**结论**：模块类与全局类并列写在同一个元素上时，除了看
    "有没有同名属性竞争"，还要看"简写会不会展开出对方的长写"。
    这一条目前只能靠**文档 + 一次探针**守住，配方就记在这里。

    > 第四批补了一句：**这条竞争的存在性本身可以被自动发现**。
    > 第四批裁决 `.quiz-question-card` 时，`.quizQuestionCard` 里
    > `border: 1px solid` + `border-left: 4px solid` 写在**同一条规则内**
    > （顺序固定，不依赖产物先后），所以没有引入新的盲区；
    > 而 `.card-hover:hover` 那种"跨文件同权重"由 `verify-built-css.mjs`
    > 的冲突统计直接报出来（`TOUCHED_BY_THIS_MIGRATION` 一旦包含该文件，
    > 它就必须归零）。真正剩下的盲区只有"**跨文件 + 简写 vs 长写 + 不同选择器**"
    > 这一种组合 —— 目前只有 `.qaAiCard` × `.card` 这一例，已登记在案。
13. **★ 压缩器的两种新等价改写（第三批实测，已进差集归一化）。**
    不补的话它们会伪装成"丢失"，把真变化淹掉 —— 第三批同一轮里正好
    真假各一条（§4.8.4）：
    - `border-radius: 16px 16px 4px 16px` → `border-radius:16px 16px 4px`
      （第 4 个值省略时取第 2 个值，渲染完全相同）；
    - `inset: 0` → `top:0;right:0;bottom:0;left:0`
      （构建器按目标浏览器把简写**降级**成长写；这是**声明级**改写，
      所以在 `canonDecls` 里展开，不是 `canonValue`）。

14. **★ 压缩器还会"合并"与"去空格"，两者都会伪装成丢失（第五~九批实测）。**
    - **合并相邻的同声明规则**：`Graph.module.css` 的
      `.graphToolbar{flex-wrap:wrap;row-gap:var(--space-xs)}` 与
      `.graphToolbarLeft,.graphToolbarRight{同样两条}` 在产物里合成
      `._graphToolbar_h,._graphToolbarLeft_h,._graphToolbarRight_h{…}` ⇒ 差集按
      "同 (上下文+选择器)" 建的两个 key 同时落空，报成 2 条丢失。
      修法：落空时找**超集规则**（同上下文 + 成员集合 ⊇ 本条 + 声明逐字相同），
      命中时打印"压缩器合并了选择器组"。
    - **选择器组里逗号后的空格被压掉**：`neutralSelector` 现在把 `\s*,\s*` 归一到 `,`
      （逗号两侧空格在 CSS 里无语义）。

15. **★ 属性选择器命中的是"序列化后的内联样式"，不是源码里的写法。**
    `[style*="rgba(0,0,0,0.5)"]` **命中 0 个元素**：React 走 CSSOM 赋内联值，
    浏览器把颜色重新序列化成 `rgba(0, 0, 0, 0.5)`（逗号后带空格）。
    `refinements.css` 的 12 条 + `responsive.css` 的 1 条因此**从未生效**（迁移前就如此）。
    真 Chromium 实测输出见证据 `5.6-12`。**教训**：任何靠 `style` 属性做匹配的选择器
    都必须先在真浏览器里量一次"到底命中了几个元素"，而不是读源码推断。

16. **★ 没有任何 tsx import 的模块文件不会进产物（第七批实测）。**
    `.link-modal` / `.material-list-item*` / `.type-badge*` 这 5 个类**零引用**；
    把它们（连同一个新建的 `LinkManagerModal.module.css`）当"按归属搬家"处理时，
    Vite 根本不会编译那个模块 ⇒ 规则从产物里消失，差集当场报 **6 条丢失**。
    结论：零引用的类**只能留全局**（或先给它们一个真正的挂载点）；
    "搬到归属模块"这个动作的前提是**那个模块被 import**。

17. **★ 模块之间"同权重、同文件、靠先后"的竞争，三道检查全都看不见（第六批）。**
    实例：`Sidebar.module.css` 的 768px 块里 `.sidebarMobileOpen { transform: translateX(0) }`
    必须排在 `.sidebar, .sidebarCollapsed { transform: translateX(-100%) }` **之后**；
    顺序反了抽屉再也滑不出来，而规则清单、冲突统计、动画检查都不会响。
    为此新增 `verify-built-css.mjs` 的 **`ORDER_PAIRS`**（`CASCADE_PAIRS` 只覆盖
    "全局类 × 模块类"）。已登记两条：抽屉开合、图谱按钮的 active+hover 态。

18. **★ 探针（真浏览器对账）自身的两个坑（第五~九批实测）。**
    - 选择器要同时命中"迁移前 kebab"与"迁移后 camelCase 哈希"，**拼后代选择器时必须
      用 `:is()` 包住整个备选列表**：`[class~="a"],[class*="b_"] .btn` 是**两个**选择器，
      第一个会把容器 div 自己匹配走（实测 26 条属性差异全是假的）。
    - `page.waitForEvent('download')` **必须在触发之前注册**（下载事件可能在
      `page.evaluate` 期间就发出去）；用 Blob URL 比 `data:` URL 稳。
    - 给 worktree 起 dev server 时**必须给独立的 vite `cacheDir`**：与工作区共用
      `node_modules/.vite` 会互相覆盖预打包产物，症状是图谱页崩到错误边界
      （`Cannot read properties of null (reading 'useRef')`）。

## 6. 证据（四轮 / 全部批次）

| 证据 | 文件 | 结论 |
|---|---|---|
| 试点：迁移前清单 | `migration-evidence/5.6-01-before-*.md` | `learning.css` 5 条 + `responsive.css` 2 条 |
| 第一批：迁移前清单 | `migration-evidence/5.6-04-before-batch1.md` | 5 个样式表共 60 条 |
| 第二批：迁移前清单 | `migration-evidence/5.6-05-before-batch2.md` | `markdown-extras.css` 20 条 |
| 第三批：迁移前清单 | `migration-evidence/5.6-06-before-batch3.md` | `learning.css` 9 条 + `responsive.css` 3 条 + `dashboard.css` 7 条 |
| 第三批的"停" | `migration-evidence/5.6-07-markdown-adhd-stayed-global.md` | `markdown.css` 的 5 条 `.adhd-*` 规则原文 + 判据对照（**本批没搬它**） |
| 第四批：迁移前清单 | `migration-evidence/5.6-08-before-batch4.md` | `assessment.css` 20 条 + `refinements.css` 14 条（= 冲突胜者）+ `responsive.css` 1 条 |
| 第四批：冲突裁决 | `migration-evidence/5.6-09-conflict-resolution.md` | 16 条属性的**实测胜者**逐条表 + "渲染没变"的 16 802 条属性对账 |
| 规则清单差集 | `migration-evidence/5.6-02-rule-diff.md` | 试点 **7/0/0**；第一批 **60/0/0**；第二批 **20/0/0**；第三批 **21/0/0**；第四批 **35/0/0 + 19 条已裁决删除**；第五批 **13/0/0**；第六批 **53/0/0**；第七批 **71/0/0**；第八批 **7/0/0 + 13 条实测从未生效的规则删除**；第九批 **补丁层清空**（逐条搬家 28 条声明）。全部批次**丢失 0**；动画绑定全部自洽；动画体 6 条全部一致 |
| 产物 CSS 校验 | `migration-evidence/5.6-03-built-css.md` | 悬空动画 **0**；改动范围内冲突 **0**（迁移前既有的 16 条也已在第四批清成 0）；级联得主正确（`CASCADE_PAIRS` + 新增的 `ORDER_PAIRS`）；**167 个退休类名在产物中 0 次**；切片标记全部命中 |
| 序 8/9/10 的迁移前清单 | `migration-evidence/5.6-10-before-batches-8-9-10.md` | components 挂点 / 侧边栏+App 骨架 / 图谱，每一组都同时列出"全局样式表那半"与"`responsive.css` 那半" |
| 序 12/13 + 补丁层清空审计 | `migration-evidence/5.6-11-before-batches-12-13-and-empty-patch-layers.md` | `refinements.css` 38 条 = 25 条逐字在工作区找到 + 13 条实测删除 + **0 条静默丢失**；`responsive.css` 59 条 = 56 + 3（`:root` 两条随元素走 / 1 条属性选择器删除）+ **0 条静默丢失** |
| 序 8~13 的渲染对账 | `migration-evidence/5.6-12-rendered-unchanged-probe.md` | 真 Chromium，10 个场景 × 56 个样本 × 54 条 computed 属性 = **3 024 条值，未声明差异 0 条**；唯一差异是已声明的动画改名；顺带实测 `[style*="rgba(0,0,0,0.5)"]` 命中 **0** 个元素 |

修订不再手写：两个脚本都从 HEAD 往回**按内容**定位"迁移前"（见规范 §7 第 3 条）。

产物抽样（可核对哈希确实生效）：

```
:root{…}                                     ← ①令牌层，现在真的在最前（字节 0）
.btn{…}                                      ← ②全局层 @4004
._authSubmit_1hcab_41{…}                     ← ③模块层 @49953（单类，靠源序压过 .btn）
@keyframes _authScaleIn_1hcab_1{0%{opacity:0;transform:scale(.95)}to{…}}
._cleaningProgressBar_76z94_64{…;animation:_cleaningPulse_76z94_1 1.5s ease-in-out infinite}
@keyframes _cleaningPulse_76z94_1{0%{transform:translate(-100%)}100%{transform:translate(100%)}}
@media (max-width: 768px){._dashboardTwoCol_1l4yv_38{grid-template-columns:1fr;gap:var(--space-md)}}
._askAiInput_hash{…;font-size:1rem;…}         ← iOS 聚焦缩放护栏的目标，现在归模块所有
._qaUserBubble_1lr0k_43{…;border-radius:16px 16px 4px;animation:_qaSlideUp_1lr0k_1 .3s …}
@keyframes _qaSlideUp_1lr0k_1{0%{opacity:0;transform:translateY(16px)}to{…}}
._uploadZoneActive_1j5js_13:after{…;top:0;right:0;bottom:0;left:0;…animation:_uploadGlowPulse_1j5js_1 1.5s …}
@media (max-width: 480px){._listToolbar_1fqhn_54 ._searchInputWrapper_1fqhn_28{flex:1 1 100%;max-width:none}}
._statCard_be3qp_1{…}._statCardBlue_be3qp_29:before{background:var(--gradient-primary)}
._scoreBarFill_1uspn_120{height:100%;border-radius:9999px;transition:width .6s …}
._quizQuestionCard_1uspn_68{…;border:1px solid var(--color-border-light);border-left:4px solid var(--color-border);border-radius:var(--radius-lg);padding:24px;margin-bottom:16px;box-shadow:0 2px 8px #0f34600f;transition:border-color .25s …}
._scoreSummaryCard_1uspn_71 ._scoreSummaryNumber_1uspn_252{font-size:2rem;color:var(--color-accent);…;background:none;…}
@media (max-width: 480px){._knowledgePointsGrid_1uspn_300{grid-template-columns:1fr}}
```

第四批那三行可以直接对照裁决结论看：`.quizQuestionCard` 是**胜者**那一套
（`--radius-lg` 16px / `24px` / `16px` / `0 2px 8px #0f34600f`），
`border-left` 长写排在 `border` 简写之后；`.scoreSummaryNumber` 仍然是
**两条规则、权重 (0,2,0)**（没有为了好看合并成一条）；480px 那条窄屏规则
跟着类名一起进了模块 chunk。

### 各轮四道验证（全绿）

| 轮次 | 结果 |
|---|---|
| 试点 + 第一批 | `npm test` **20 files / 272 tests**（与迁移前一致）；`tsc` / `lint` / `build` 退出 0；两个证据脚本退出 0 |
| **`main.tsx` 重排** | `npm test` **20 files / 272 tests**；`tsc` / `lint` / `build` 退出 0；级联检查显示 `.authSubmit` **靠源序**取胜 |
| **第二批** | `npm test` **20 files / 273 tests**（+1：新增"模块层必须排在全局层之后"用例）；`tsc` / `lint` / `build` 退出 0；差集 20/0/0；产物校验退出 0 |
| **第三批** | `npm test` **21 files / 276 tests**（+1 文件 / +3：新增 `components/StatCard.test.tsx`）；`npx tsc --noEmit` / `lint` / `npm run build` 退出 0；差集 **21/0/0** 与产物校验退出 0；`e2e` 里**原有的 10 个用例 10 passed**，但 `npm run e2e` 整体退出 1 —— 并行 agent 正在写的 `e2e/a11y.spec.ts`（未跟踪文件）有 8 个用例失败，原因全是"页面停在加载/登录态 ⇒ 找不到标题"，与本批无关（详见 §8） |
| **第四批（序 5）** | `npm test` **21 files / 276 tests**（与迁移前一致，**没改任何既有测试**）；`npx tsc --noEmit` / `lint` / `npm run build` 退出 0；差集 **35/0/0 + 19 条已裁决删除**、产物校验退出 0（迁移前既有的 16 条冲突 → **0**）；`npm run e2e` **10 passed**、`npm run a11y` **10 passed**（第三批时挡路的 a11y spec 已被对方修好并提交）；临时探针实测 **16 802 条 computed 属性差异 0** |
| **第五~九批（序 8/9/10/12/13）** | `npm test` **21 files / 276 tests**（与迁移前一致）；`npx tsc --noEmit` / `npm run lint` / `npm run build` 退出 0；差集：五批合计 **丢失 0**（13 / 53 / 71 / 7 / 补丁层清空）、产物校验退出 0（冲突 0 / 悬空动画 0 / 167 个退休类名 0 次 / `ORDER_PAIRS` 两条得主正确）；`npm run e2e` **10 passed**、`npm run a11y` **25 passed**（并行 agent 把 a11y 场景从 15 扩到 25，无下降）；临时探针实测 **3 024 条 computed 属性未声明差异 0**；两张补丁层清空后另有**逐条去向审计**（0 条静默丢失） |

> 本轮的环境插曲（不是本批引入的）：执行期间并行 agent 正在改
> `pages/Dashboard.tsx` / `TodayLearn.tsx` / `utils/labels.ts` / `base.css`，
> 中途 `tsc` 报过 4 条 `Cannot find name 'weakPointBadge'`（全在他们的文件里），
> `npm run build` 因此停过一段；**收尾时对方已修好，`npx tsc --noEmit` 与
> `npm run build` 都已退出 0**。期间产物证据是用 `npx vite build` 产出的
> （与第三批同一处理），最终所有证据都用 `npm run build` 的产物重跑过。

### 测试改动（三处 + 一处新增 + 第四批零改动）

前两轮**没有修改任何测试**。第二批改了三处，第三批**没有改任何既有测试**
（三个页面都没有测试文件，且迁移只换类名），只新增了一个。
第四批同样**没有改任何测试文件**：`LearningAssessment.tsx` 没有测试，
而唯一按类名查询这批元素的测试不存在（`Projects.test.tsx` 查的 `.note-select-card`
**留在了全局**，所以那三处断言一字未动）。

| 文件 | 改动 | 理由 |
|---|---|---|
| `components/NoteAskPanel.test.tsx` | `.ask-ai-panel` 字面量 → `styles.askAiPanel` | 类名进模块后被哈希，字面量查询必然拿到 `null` 并抛错。**没有**换成 `getByRole`：本文件断言的是内联几何（`style.left/width`），面板根节点在 a11y 树上没有稳定角色，为测试加 `role="dialog"` 属于产品/无障碍改动（还牵涉焦点管理），不该混进"纯搬家"。规范 §6 明确允许这条退路 |
| `styles/mobile-input-font-size.test.ts`（护栏） | 扫描范围加 `src/**/*.module.css`；`.ask-ai-input` 的 owner 改为 `components/NoteAskPanel.module.css`；`Target` 增加 `moduleClassName` | **收紧而非放宽**。不扩范围的话，输入框的 `font-size` 一进模块，护栏就再也看不见它 —— 全绿，但护栏已经空了（"静默失明比测试红更危险"） |
| `styles/mobile-input-font-size.test.ts`（新增用例） | "模块层必须排在全局层之后：`main.tsx` 里组件 import 在样式表 import 之后" | 把 `main.tsx` 的重排变成单测级护栏，不需要构建就能发现回退 |
| `components/StatCard.test.tsx`（第三批**新增**） | 3 个用例：结构与迁移前逐字一致（`div > div + div`、类名映射）；4 个变体渲染出 4 个互不相同的类名组合；`value` 支持字符串 | 计划给"抽 `StatCard`"加的前置条件是"一次带视觉核对的独立改动"。无头环境里能做到的等价物就是把这个组件被两个页面依赖的**结构契约**钉死：漏一个变体、把 `value`/`label` 挂错节点、把外层换成 `<span>`，全都当场红 |

## 7. 下一批计划（按风险从窄到宽）

### 7.0 已完成 / 待做的跨批次事项

1. ✅ **`main.tsx` 导入顺序已修正**：全局样式表提到应用组件之前，
   产物顺序 = ①令牌 → ②全局 → ③模块，雷区 8 根治。
   选择器也从 `:global(.btn).authSubmit` 还原成单类。
2. ⏳ **死 CSS 清理单独一轮**：`.cleaning-progress` / `.cleaning-progress-bar`
   （无 TSX 引用）、`base.css` 里已失去全部用户的 `@keyframes scaleIn` /
   `cleaning-pulse`、以及登记过的其它死类名（`.feedback-pending` /
   `.duplicate-block-text` / `.ask-ai-sources` / `.ask-ai-source-item` 已在前几批删除）。
   带"确认无引用"的证据再删。
3. ⏳ **补一个检查盲区**：跨媒体查询的覆盖战目前看不见
   （实例：`responsive.css` 768px 的 `.markdown-body .katex { font-size: 1em }`
   被 `markdown-extras.css` 的顶层 `1.1em` 压掉 —— 权重相同、后者更靠后，
   这条窄屏规则**从未生效**；迁移前就如此）。详见规范 §7 盲区 4。
4. ⏳ **跨属性竞争（简写 vs 长写）也看不见** —— 第三批新登记的雷区 12。
   目前只有文档 + 一次性探针（配方在雷区 12 里，20 行）。要做成常设检查，
   得在产物上做"权重 + 顺序 + **简写展开**"的真实求解，与第 3 条是同一类工作，
   建议合并成一轮。
5. ⏳ **第三批留下的 `glowPulse`**：`.upload-zone-active::after` 搬走后，
   它和 `scaleIn` / `cleaning-pulse` 一样没有样式表用户了（行内样式仍可用）。
   与前两条一起进"死 CSS 清理"轮。

### 7.1 下一批：序 8 / 9 / 10 / 12 / 13（**已全部完成**，见 §4.10）

6 / 7 / 3 三行的结果见 §4.8：**6 是有记录的停**（`.adhd-*` 留全局，
除非把 `src/hooks/useAdhdReader.ts` 纳入某一批的改动范围）；
**7 按归属拆完了能拆的部分**（剩下 5 组是跨功能共用件）；
**3 已通过抽 `StatCard` 组件收尾**；
**5 已在第四批完成**（先裁决 16 条冲突再拆，见 §4.9）。

剩下五个样式表互相咬合，**已按下面的顺序成批做完**（第五~九批）：

| 序 | 样式表 | 结果 |
|---|---|---|
| 8 | `components.css` | ✅ 第五批：只把明确单一归属的拆出；`.btn`(45) / `.card`(35) / `.container` 留全局 |
| 9 | `layout.css` + `responsive.css` | ✅ 第六批：侧边栏 / App 骨架 → 两个模块；`App.test.tsx` / `Sidebar.test.tsx` 同步改成语义查询 |
| 10 | `graph.css` + `responsive.css` | ✅ 第七批：整份 → `components/graph/Graph.module.css`；`KnowledgeGraph.test.tsx` 改用模块导出 |
| 12 | `refinements.css` | ✅ 第八批：先消灭 `[style*=…]` 那 12 条（实测从未生效 ⇒ 删除），再把 `.card-hover:hover` 的胜者值搬回 `components.css`，其余按归属拆 |
| 13 | `responsive.css` | ✅ 第九批：最后 28 条声明各回归属；文件只剩注释（**没删文件**） |

**⚠️ 仍未做的两件事**（都不属于"迁移"，属于"清理"或"补检查"）：
`components.css` 里还留着 5 个零引用的预留类（`.link-modal` / `.material-list-item*` /
`.type-badge*`）、`layout.css` 里还留着 5 条 `.navbar*`、`base.css` 里还留着 4 个
没有样式表用户的 `@keyframes` —— 都登记在 §7.0 第 2 条的死代码清理轮。

### 7.2 每批的登记动作（不登记 = 证据脚本对新批次**静默不覆盖**）

- `css-migration-diff.mjs` 的 `BATCHES`（`beforeSheets` + `groups` + `keyframes`；
  `rev` 一般留空，脚本按内容自动定位；`hardened` 只在**确实提权**时才写；
  **第四批新增 `resolvedConflicts`**：按实测胜者删掉的"输家声明"要逐条登记
  `{ sheet, selector, prop, value, winner, evidence }`，脚本会从"迁移前"一侧
  摘掉它们、单独成节列出，并**自检每条都真的命中过** ——
  写错类名/值会报错退出，不会变成一条永远绿灯的空声明）；
- `verify-built-css.mjs` 的 `TOUCHED_BY_THIS_MIGRATION` / `CASCADE_PAIRS`（有同权重竞争才加）
  / `SLICE_MARKERS` / `RETIRED`；
- `gen-migration-evidence.mjs` 里加一份本批的"迁移前清单"（第三批多加了一份
  `5.6-07`：把"决定不搬"的规则也留成证据；第四批多加了一份
  `5.6-09`：把**冲突裁决的实测值**固化成可核对的证据 ——
  探针是临时的，结论不能只活在某个人的对话里）。

### 7.3 第三批 / 第四批的登记结果（照着抄）

| 登记点 | 第三批 | 第四批（序 5） |
|---|---|---|
| `BATCHES` | 第三个批次项：`beforeSheets` 三个样式表、4 个 `groups`（QA / Upload / NotesList / StatCard）、2 个 `keyframes`（`slideUp`→`qaSlideUp`、`glowPulse`→`uploadGlowPulse`） | 第四个批次项：`beforeSheets` 三个样式表（assessment / refinements / responsive）、1 个 `group`（19 个类名 → `pages/LearningAssessment.module.css`）、`keyframes: []`、**`resolvedConflicts` 19 条**（14 条 quiz 冲突 + 5 条 `.score-summary-number` 跨选择器覆盖） |
| `TOUCHED_BY_THIS_MIGRATION` | `pages/QA.module.css` / `pages/Upload.module.css` / `pages/NotesList.module.css` / `components/StatCard.module.css` | `styles/assessment.css` / `styles/refinements.css` / `pages/LearningAssessment.module.css` / `styles/components.css`（只删了 2 条被压掉的声明，登记进来 = 它再冒冲突就是红灯） |
| `CASCADE_PAIRS` | **没加**：本批没有"同名属性、同权重、跨文件"的竞争（`.qaAiCard` × `.card` 是简写 vs 长写，按属性名配不上，登记在雷区 12） | **没加**，理由同上：元素上只挂模块类，没有"全局类 + 模块类并列"；`.scoreSummaryCard .scoreSummaryNumber` 那条是**故意保留两条规则**（合并会降权重），不是新竞争 |
| `SLICE_MARKERS` | 16 个新类名/动画名（`qaUserBubble` … `statLabel`） | 19 个新类名（`scoreBar` … `knowledgePointsSection`） |
| `RETIRED` | 14 个退休类名（含**只定义在补丁层**的 `list-toolbar`） | 19 个退休类名（`score-bar*` / `quiz-question-*` / `score-summary-*` / `score-value` / `knowledge-points-*`）。留全局的 `.assessment-*` 与 `.note-select-card*` **不列**，它们必须继续以 kebab 形态出现在产物里 |
| 第四批的"新增一类差异" | — | 差集脚本从"逐字保留 / 值有变化 / 丢失"扩成四类：**已裁决删除**。理由是这一批删掉的 19 条声明既不是丢失也不是值变化，而是"按实测胜者删掉的死声明" —— 不单独声明的话，差集会把它们报成 19 条丢失（把真信号淹掉） |

## 8. 本轮的环境限制：并行的无障碍 agent

第三批执行期间，另一个 agent 正在写无障碍工具，产生了两个**未跟踪**文件
`frontend/e2e/a11y.spec.ts` / `a11y-fixtures.ts`。因为 `tsconfig.json` 的
`include` 里有 `e2e`，它们会进入本项目的类型检查与 e2e 运行。两个影响：

**① `tsc` / `npm run build` 一度被挡住（已恢复）。** 中途实测：

```
e2e/a11y-fixtures.ts(300,15): error TS2580: Cannot find name 'process'.
e2e/a11y.spec.ts(280,9): error TS6133: 'manualChecks' is declared but its value is never read.
e2e/a11y.spec.ts(288,3): error TS2741: Property 'manualChecks' is missing in type ...
```

错误**全部**在 `e2e/`，`src/` 下 0 条（用 `tsc --noEmit | Select-String '^src/'`
核对过）。`npm run build` = `tsc && vite build`，所以构建也停在 `tsc` 段。
本批不允许改 `frontend/e2e/**`（那个 agent 正在改），当时的处理是：
先用 `npx vite build` 产出 `dist` 让证据脚本照常跑，并用"错误按目录分类"
证明本批没有引入任何类型错误。**收尾时对方已修好**：`npx tsc --noEmit`
与 `npm run build` 都已退出 0，产物与证据都是用 `npm run build` 的产物重跑的。

**② `npm run e2e` 整体退出 1，但失败全在对方那份新 spec 里。**
分别实测：

| 命令 | 结果 |
|---|---|
| `npx playwright test e2e/app-shell.spec.ts e2e/auth-routing.spec.ts e2e/login-api.spec.ts e2e/login-form.spec.ts`（**迁移前就在的 4 个 spec**） | **10 passed**，退出 0 —— 与基线 10 passed / 0 skipped 一致 |
| `npm run e2e`（含对方新加的 `a11y.spec.ts`） | 11 passed / **8 failed**，退出 1 |

8 条失败的形态**完全一样**：`getByRole('heading', …).toBeVisible()` →
`element(s) not found`，快照里页面停在"加载中…"或登录页 —— 那是"后端没起/夹具没播种"
造成的，不是样式问题（没有一条提到 contrast / target size / 类名）。
要判断"某条 e2e 失败是不是 5.6 引起的"，**跑上面那条只含 4 个老 spec 的命令**即可。

### 8.1 第四批（序 5）时的环境：对方已经收口，`e2e` / `a11y` 都全绿

第四批执行期间那个 agent 又提交了两笔（`1ebb3a6` 把 a11y 覆盖扩到 15 场景、
`97f964b` 补文档），**都没有动 `src/`**（`git show --stat` 核对过），
所以本批的"迁移前"基线是干净的。实测：

| 命令 | 结果 |
|---|---|
| `npm test` | **21 files / 276 tests passed**，退出 0（与迁移前一致，本批没改测试） |
| `npx tsc --noEmit` / `npm run lint` / `npm run build` | 都退出 0 |
| `npm run e2e` | **10 passed**，退出 0（与基线一致） |
| `npm run a11y` | **15 passed**，退出 0（第三批时是 10 条且 8 条失败，现在对方已修好并扩到 15 条） |
| `node scripts/css-migration-diff.mjs` / `verify-built-css.mjs` | 都退出 0 |

两个仍然要记的环境细节（都不是本批引入的）：

1. **`HEAD` 会在你干活的时候往前挪** —— 第四批一开始 HEAD 是 `a33de19`，
   收尾时已经变成 `97f964b`。这正是两个证据脚本用 `findRecentRev` **按内容**
   定位"迁移前"的理由：本批的差集照样报 `迁移前（git HEAD 源码）`，一字没错。
2. **别在 `e2e/` 里留东西**：本批的冲突探针写成 `e2e/zz-cssconf-probe.spec.ts`
   （一次性，用完删）。它会进 `tsc` 的类型检查 —— 所以探针里**不能** `import fs
   from 'node:fs'`（本项目没有 `@types/node`，直接红）。删掉之后 `npm run e2e`
   才回到 10 passed 的基线口径。
