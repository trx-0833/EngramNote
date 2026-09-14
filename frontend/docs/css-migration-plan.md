# 5.6 CSS 体系重建 —— 进度与剩余迁移计划

> 状态：**机制已建立 + 试点已落地 + 第一批（4 表）已落地 + 第二批（markdown-extras 拆分）已落地**。
> 规范见 [`css-convention.md`](./css-convention.md)；实测证据见
> [`migration-evidence/`](./migration-evidence/)。
>
> 进度：序 **1 / 2 / 3 已完成**，序 **4 按归属部分完成**，
> 序 **11 按归属部分完成**（`.ask-ai-*` / `.selection-menu` 已搬；
> KaTeX 与批注规则按"第三方 DOM"留全局）。序 5–10 / 12 / 13 未动。
>
> **`main.tsx` 的导入顺序已修正**（全局样式表提到组件之前）——
> 第一批发现的"级联反转"雷区已**根治**，护栏见规范 §3 雷区 5。

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
| 4 | `dashboard.css` | 13 | 仪表盘卡片、统计数字 | 中：`.progress-bar*` 被 **5 处**共用；`.stat-card*` 被 **2 个页面**共用 | ⚠️ **部分完成**：仪表盘私有的 5 条 → `pages/Dashboard.module.css`；`.stat-card*`(9) 与 `.progress-bar*`(3) **留全局**（见 §4.3） |
| 5 | `assessment.css` | 22 | 学习评估页 | **高**：`.quiz-question-*` 与 `refinements.css` 打架（14 条属性冲突，§6 实测仍在）；`.knowledge-points-grid` 被 `responsive.css` 命中 | 先解决冲突再拆 |
| 6 | `markdown.css` | 5 | `.markdown-body` + ADHD 阅读器 | **高**：内容来自 `marked`，没有组件可挂类名 | 只搬 ADHD 部分；`.markdown-body` 后代选择器留全局 |
| 7 | `learning.css` | **18**（迁移前 22） | 问答气泡/上传区/选项/反馈/筛选/分段/搜索/空态/加载 | 中：试点已搬走 4 条；剩余 18 个类名被 8 个切片外页面使用 | 按"谁在用"逐组拆，不能整表搬 |
| 8 | `components.css` | 52 | `.btn` `.card` `.badge` `.container` 等公共件 | **最高**：`.btn` 45 处、`.card` 35 处引用；`.container` 被测试查 | 几乎全留全局；只把明确单一归属的（`.note-detail-*`、`.note-list-*`、`.edit-split`）拆出 |
| 9 | `layout.css` | 27 | 侧边栏 / 顶栏 / 布局骨架 | **最高**：`responsive.css` 命中 12 个类名、测试命中 5 个 | 与 `responsive.css` 一起整批处理，测试同步改 |
| 10 | `graph.css` | 47 | 知识图谱 | **最高**：`responsive.css` 命中 12 个类名，测试查 `.graph-search-input` | 与 `responsive.css` 一起整批处理 |
| 11 | `markdown-extras.css` | 22 | KaTeX / AI 提问面板 / 引用高亮 | 中：`.katex*` 是第三方 DOM | ⚠️ **部分完成**：`.ask-ai-*`(17) + `.selection-menu`(3) 已搬；KaTeX(4) 与批注高亮(6，含 `citation-flash`) **留全局**（§4.6） |
| 12 | `refinements.css` | 25 | **补丁层**（后加载覆盖前面） | **最高**：它存在的意义就是覆盖别人 | 拆分到各归属组件；拆完本文件应能删除 |
| 13 | `responsive.css` | 48 | 响应式补丁层 | **最高**：见 §1 | 规则跟着组件走，拆完本文件应能删除 |
| 14 | `base.css` | 0 类 | **令牌层 + 重置** | — | **保持全局，永不迁移** |

**顺序**：✅ 1–4 → 下一批 **11 + 6 + 7 + 3**（中等，需要"拆分"而不是整表搬）
→ 最后集中处理 5 + 8 + 9 + 10 + 12 + 13（与 `responsive.css`、测试互相咬合，必须**成批**处理）。

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

## 5. 雷区（按危险程度，★ = 第一批新发现）

1. **动画名会被一起哈希（试点实测，最阴）。**
   任何把含 `animation` 的规则搬进模块的地方都要处理：要么把 `@keyframes`
   定义搬进模块，要么别搬这条规则。受影响：
   `learning.css`（`slideUp` / `glowPulse` / `fadeIn` / `spin`）、
   `components.css`（`fadeIn` / `slideUp`）、`dashboard.css`（`shimmer`，
   连同 `.progress-bar*` 一起留全局所以暂时安全）、`layout.css`（`fadeIn`）、
   `graph.css`（`graph-spin`）、`markdown-extras.css`（`citation-flash`，
   但那条规则留在全局，所以引用与定义同层，不需要搬）。
   ~~`auth.css`~~ / ~~`cleaning.css`~~ 已在第一批处理完毕。
   第二批**没有**需要搬的动画：`.ask-ai-*` / `.selection-menu` 里一条 `animation` 都没有。
8. **★ 模块的 CSS 与全局样式表的先后** —— 详见 §5 下方说明，**已根治**。
2. **`responsive.css` 与 `layout.css` / `graph.css` 是同一批类名的两半。**
   拆任何一半，另一半立刻静默失效。必须整批一起动，并同步改
   `App.test.tsx` / `Sidebar.test.tsx` 里按类名查询的断言。
3. **`assessment.css` × `refinements.css` 已经在打架**（迁移前既有，仍未动）：
   `.quiz-question-card` / `.quiz-question-number` / `.quiz-question-text`
   共 **14 条属性冲突**，`.card-hover:hover` 另 2 条（第一批的产物校验实测复现）。
   两个文件都后加载覆盖前者，所以"哪套生效"完全取决于导入顺序 ——
   这正是 5.6 要消灭的形态。迁移时**先决定保留哪一套**，再搬。
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

## 6. 证据（三轮 / 全部批次）

| 证据 | 文件 | 结论 |
|---|---|---|
| 试点：迁移前清单 | `migration-evidence/5.6-01-before-*.md` | `learning.css` 5 条 + `responsive.css` 2 条 |
| 第一批：迁移前清单 | `migration-evidence/5.6-04-before-batch1.md` | 5 个样式表共 60 条 |
| 第二批：迁移前清单 | `migration-evidence/5.6-05-before-batch2.md` | `markdown-extras.css` 20 条 |
| 规则清单差集 | `migration-evidence/5.6-02-rule-diff.md` | 试点 **7/0/0**；第一批 **60/0/0**；第二批 **20/0/0**（逐字保留 / 值有变化 / 丢失）；动画绑定全部自洽；动画体 4 条全部一致 |
| 产物 CSS 校验 | `migration-evidence/5.6-03-built-css.md` | 悬空动画 **0**；改动范围内冲突 **0**；级联得主正确（靠源序）；**59 个退休类名在产物中 0 次**；切片标记全部命中 |

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
```

### 各轮四道验证（全绿）

| 轮次 | 结果 |
|---|---|
| 试点 + 第一批 | `npm test` **20 files / 272 tests**（与迁移前一致）；`tsc` / `lint` / `build` 退出 0；两个证据脚本退出 0 |
| **`main.tsx` 重排** | `npm test` **20 files / 272 tests**；`tsc` / `lint` / `build` 退出 0；级联检查显示 `.authSubmit` **靠源序**取胜 |
| **第二批** | `npm test` **20 files / 273 tests**（+1：新增"模块层必须排在全局层之后"用例）；`tsc` / `lint` / `build` 退出 0；差集 20/0/0；产物校验退出 0 |

### 测试改动（三处，全部有据）

前两轮**没有修改任何测试**。第二批改了三处，逐条说明理由：

| 文件 | 改动 | 理由 |
|---|---|---|
| `components/NoteAskPanel.test.tsx` | `.ask-ai-panel` 字面量 → `styles.askAiPanel` | 类名进模块后被哈希，字面量查询必然拿到 `null` 并抛错。**没有**换成 `getByRole`：本文件断言的是内联几何（`style.left/width`），面板根节点在 a11y 树上没有稳定角色，为测试加 `role="dialog"` 属于产品/无障碍改动（还牵涉焦点管理），不该混进"纯搬家"。规范 §6 明确允许这条退路 |
| `styles/mobile-input-font-size.test.ts`（护栏） | 扫描范围加 `src/**/*.module.css`；`.ask-ai-input` 的 owner 改为 `components/NoteAskPanel.module.css`；`Target` 增加 `moduleClassName` | **收紧而非放宽**。不扩范围的话，输入框的 `font-size` 一进模块，护栏就再也看不见它 —— 全绿，但护栏已经空了（"静默失明比测试红更危险"） |
| `styles/mobile-input-font-size.test.ts`（新增用例） | "模块层必须排在全局层之后：`main.tsx` 里组件 import 在样式表 import 之后" | 把 `main.tsx` 的重排变成单测级护栏，不需要构建就能发现回退 |

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

### 7.1 下一批样式表：6 + 7 + 3（11 已完成）

| 序 | 样式表 | 拆分边界 | 前置条件 |
|---|---|---|---|
| 6 | `markdown.css` | ADHD 阅读器（`.adhd-*`）进模块；`.markdown-body` 后代选择器留全局（`marked` 产物，无组件） | ⚠️ **可能是个"停"**：`useAdhdReader.ts` 用 `classList.add/remove/contains` 直接操作 `.adhd-block` / `.adhd-current-block` / `.adhd-reader-active` / `.adhd-line-marker` 四个类名，`utils/markdown.ts` 也可能往 HTML 里插它们 —— 迁移前必须先把这几个类名的**全部写入点**列清楚，判断是"改成语义查询"还是"从模块导出常量"还是"留全局" |
| 7 | `learning.css` | 按"谁在用"逐组拆（`.qa-*` / `.upload-zone*` / `.segment-*` / `.state-*` / `.spinner` / `.search-input-*` / `.collapse-arrow` / `.filter-pill`） | 每组先 grep 出文件数，命中 2 个以上不相邻功能就留全局 |
| 3（余下） | `dashboard.css` | `.stat-card*` + `.stat-label` → 先抽 `StatCard` 组件（`Dashboard.tsx` + `TodayLearn.tsx` 共用），再连样式搬 | 抽组件是重构，需要一次带视觉核对的独立改动 |

### 7.2 最后一批（必须成批处理）

`assessment.css`（先解决与 `refinements.css` 的 14 条冲突）→ `components.css`
→ `layout.css` + `responsive.css` + 测试 → `graph.css` + `responsive.css`
→ `refinements.css`（拆完删文件）。

**每批的登记动作**（不登记 = 证据脚本对新批次**静默不覆盖**）：

- `css-migration-diff.mjs` 的 `BATCHES`（`beforeSheets` + `groups` + `keyframes`；
  `rev` 一般留空，脚本按内容自动定位；`hardened` 只在**确实提权**时才写）；
- `verify-built-css.mjs` 的 `TOUCHED_BY_THIS_MIGRATION` / `CASCADE_PAIRS`（有同权重竞争才加）
  / `SLICE_MARKERS` / `RETIRED`；
- `gen-migration-evidence.mjs` 里加一份本批的"迁移前清单"。
