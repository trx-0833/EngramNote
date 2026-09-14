# 5.6 CSS 体系重建 —— 试点落地与剩余迁移计划

> 状态：**机制已建立 + 试点已落地**（`src/components/quiz/`）。
> 规范见 [`css-convention.md`](./css-convention.md)；本轮实测证据见
> [`migration-evidence/`](./migration-evidence/)。

## 1. 为什么不是一次性迁移

本项目有**两处依赖稳定全局类名**的地方，天真的全量迁移会同时踩坏它们：

1. **`responsive.css`**（445 行）用全局选择器覆盖各页面的类名
   （`.graph-search-input` / `.sidebar-item` / `.self-rating-btn` /
   `.note-detail-header` / `.dashboard-two-col` / `.page-header-row` / `.edit-split` …）。
   类名一旦哈希，这些选择器**选不中任何东西**：规则还在、永不生效。
2. **测试按类名查询**（`.sidebar-item` / `.graph-stats-bar-row` / `.fg-node-ids` /
   `.progress-bar-fill` / `.sidebar-mobile-open` …）。哈希后一律失效。

所以本轮只做三件事：**建立机制、在一个自洽切片上验证、证明没丢东西**。

## 2. 现有 14 个样式表的盘点与迁移顺序

按"依赖面从窄到宽"排序。**风险**列的判据是：它被多少切片外的文件使用、
以及是否被 `responsive.css` 或测试命中。

| 序 | 样式表 | 主要类数 | 内容 | 风险 | 迁移方式 |
|---|---|---|---|---|---|
| 1 | `auth.css` | 7 | 登录/注册卡片 | 低：只被 auth 页用 | 整表 → `pages/Auth.module.css` |
| 2 | `cleaning.css` | 15 | 清洗面板、重复块对比 | 低：`Cleaning` 页专用 | 整表 → 该页模块 |
| 3 | `dashboard.css` | 13 | 仪表盘卡片、统计数字 | 中：`.progress-bar*` 被 **5 处**共用（见雷区） | 拆：`.progress-bar*` 留全局，其余进模块 |
| 4 | `diff.css` | 13 | 版本对比 | 低 | 整表 → `DiffView.module.css` |
| 5 | `assessment.css` | 22 | 学习评估页 | **高**：`.quiz-question-*` 与 `refinements.css` 正在打架（14 条属性冲突，见 §5）；`.knowledge-points-grid` 被 `responsive.css` 命中 | 先解决冲突再拆 |
| 6 | `markdown.css` | 5 | `.markdown-body` + ADHD 阅读器 | **高**：内容来自 `marked`，没有组件可挂类名 | 只搬 ADHD 部分；`.markdown-body` 后代选择器留全局 |
| 7 | `learning.css` | **18**（迁移前 22） | 问答气泡/上传区/选项/反馈/筛选/分段/搜索/空态/加载 | 中：本轮已搬走 4 条；剩余 18 个类名被 8 个切片外页面使用 | 按"谁在用"逐组拆，不能整表搬 |
| 8 | `components.css` | 52 | `.btn` `.card` `.badge` `.container` 等公共件 | **最高**：`.btn` 45 处、`.card` 35 处引用；`.container` 被测试查 | 几乎全留全局；只把明确单一归属的（`.note-detail-*`、`.note-list-*`、`.edit-split`）拆出 |
| 9 | `layout.css` | 27 | 侧边栏 / 顶栏 / 布局骨架 | **最高**：`responsive.css` 命中 12 个类名、测试命中 5 个 | 与 `responsive.css` 一起整批处理，测试同步改 |
| 10 | `graph.css` | 47 | 知识图谱 | **最高**：`responsive.css` 命中 12 个类名，测试查 `.graph-search-input` | 与 `responsive.css` 一起整批处理 |
| 11 | `markdown-extras.css` | 22 | KaTeX / AI 提问面板 / 引用高亮 | 中：`.katex*` 是第三方 DOM | `.ask-ai-*` 进模块；`.katex*` 留全局 |
| 12 | `refinements.css` | 25 | **补丁层**（后加载覆盖前面） | **最高**：它存在的意义就是覆盖别人 | 拆分到各归属组件；拆完本文件应能删除 |
| 13 | `responsive.css` | 48 | 响应式补丁层 | **最高**：见 §1 | 规则跟着组件走，拆完本文件应能删除 |
| 14 | `base.css` | 0 类 | **令牌层 + 重置** | — | **保持全局，永不迁移** |

**顺序建议**：先做 1–4（低风险、能继续练手并暴露约定问题），
再做 11 + 6 + 7 + 3（中等，需要"拆分"而不是整表搬），
最后集中处理 5 + 8 + 9 + 10 + 12 + 13 —— 这几张表与 `responsive.css`、
测试互相咬合，必须**成批**处理，因为它们共享同一批类名。

## 3. 本轮落地内容（试点）

### 3.1 令牌层

`src/styles/base.css` 的 `:root` 增加：

```css
--radius-full: 9999px;   /* 胶囊/正圆：样式表里手写了 14 处 */
```

并在模块里把 `rgba(15, 52, 96, 0.08)` 换成语义令牌 `--color-primary-light`
（**值完全相同**；`--color-primary-rgb` 存在的唯一目的就是拼 alpha，见计划 F-18）。

**没有**大批新增令牌：字号与 z-index 确实是重复最多的两类
（`0.8rem` 13 处、`1rem` 11 处），但补齐它们要同时改动上百条声明，
diff 无法审阅 —— 那应当**单独一轮**做，见 `css-convention.md` §2 的缺口说明。

### 3.2 试点切片：`src/components/quiz/`

选它的理由：**自洽**（三个子组件只被复习类页面引用）、
**有真实收益**（搬走的类名全项目只有它在用）、
**风险可控**（只与 `responsive.css` 有 1 处交叉）。

| 原位置 | 规则 | 去向 |
|---|---|---|
| `learning.css:54-75` | `.quiz-option` `.quiz-option:hover` `.quiz-option-selected` | `QuizAnswerCard.module.css` |
| `learning.css:79-93` | `.feedback-correct` `.feedback-incorrect` | `QuizAnswerCard.module.css` |
| `responsive.css:299` | `@media(768px) .self-rating-btn` | `SelfRatingButtons.module.css` |
| `responsive.css:401` | `@media(480px) .self-rating-btn` | `SelfRatingButtons.module.css` |

### 3.3 刻意**没有**搬的（都在切片目录内）

| 类名 | 留在哪 | 为什么 |
|---|---|---|
| `.progress-bar` `.progress-bar-fill` | `dashboard.css` | 被 Dashboard / LearningGoals / TodayLearn / TaskProgress / ReviewProgress **5 处**使用；`ReviewProgress.test.tsx` 还按它查询。`ReviewProgress` 虽然是 quiz 目录下的组件，但它是**共享件**，不是切片私有件 |
| `.btn` `.card` `.btn-primary` | `components.css` | 全局公共件（45 / 35 处引用） |
| `.self-rating-btn` 的**内联样式** | 未动 | 本轮只搬 CSS 规则。把 `SelfRatingButtons.tsx` 里约 30 行内联样式改成模块类属于**重构**，会改变 DOM 的 style 属性，越出"证明没丢"的范围 |
| `.feedback-pending` | **删除** | 见 §5：全项目没有任何样式表定义它，是死类名 |

### 3.4 未迁移但有价值的发现

- **`.feedback-pending`**：`QuizAnswerCard.tsx:281` 挂着这个类名，
  而**全项目 14 个样式表都没有定义它**。它一直是个死类名，
  本轮顺手删除（行为无变化）。
- **`.self-rating-btn` 曾只在 `responsive.css` 里有定义**：
  桌面端（>768px）它没有任何样式表规则，基础外观全靠 tsx 内联样式。
  这类"没有任何样式表拥有它"的类名是最危险的一类 —— 本次把它连同
  两条窄屏规则一起收进模块，让它终于有了归属。
- **`.page-header-row` / `.list-toolbar`** 同样只在 `responsive.css` 里有定义，
  由 6 处 TSX 挂载。下一轮处理 `layout.css` / `components.css` 时要注意：
  这两个类名**没有基础规则**，搬走时别"顺手补一套基础样式"。

## 4. 证据（本轮）

| 证据 | 文件 | 结论 |
|---|---|---|
| 迁移前规则清单 | `migration-evidence/5.6-01-before-*.md` | `learning.css` 5 条 + `responsive.css` 2 条，取自 `git show HEAD` |
| 规则清单差集 | `migration-evidence/5.6-02-rule-diff.md` | **逐字保留 7 / 值有变化 0 / 丢失 0**；动画绑定全部自洽；动画体一致 |
| 产物 CSS 校验 | `migration-evidence/5.6-03-built-css.md` | 无悬空动画；**改动范围内 0 条冲突**；6 个退休类名在产物中全部消失 |

`dist` 产物里可以看到类名确实被哈希，且退出码为 0：

```
._quizOption_1a0d4_52{…}                    ← 原 .quiz-option
._quizOption_1a0d4_52:hover{transform:translate(4px)}   ← 压缩器把 translateX(4px) 等价改写成 translate(4px)
._quizOptionSelected_1a0d4_69{box-shadow:0 0 0 3px var(--color-primary-light)}
._feedbackCorrect_1a0d4_79{animation:_feedbackScaleIn_1a0d4_1 .3s …}
@keyframes _feedbackScaleIn_1a0d4_1{…}
@media (max-width: 768px){._selfRatingBtn_1beya_22{min-height:56px}}
@media (max-width: 480px){._selfRatingBtn_1beya_22{min-height:60px}}
```

## 5. 下一轮的雷区（按危险程度）

1. **动画名会被哈希（已实测，最阴）。**
   任何把含 `animation` 的规则搬进模块的地方都要处理：要么把 `@keyframes`
   定义搬进模块，要么别搬这条规则。受影响：
   `learning.css`（`slideUp` / `glowPulse` / `fadeIn` / `spin`）、
   `auth.css`（`scaleIn`）、`components.css`（`fadeIn` / `slideUp`）、
   `dashboard.css`（`shimmer`）、`layout.css`（`fadeIn`）、
   `cleaning.css`（`cleaning-pulse`）、`graph.css`（`graph-spin`）。
2. **`responsive.css` 与 `layout.css` / `graph.css` 是同一批类名的两半。**
   拆任何一半，另一半立刻静默失效。必须整批一起动，并同步改
   `App.test.tsx` / `Sidebar.test.tsx` 里按类名查询的断言。
3. **`assessment.css` × `refinements.css` 已经在打架**（迁移前既有，本轮未动）：
   `.quiz-question-card` / `.quiz-question-number` / `.quiz-question-text`
   共 **14 条属性冲突**（`border-radius` / `padding` / `width` / `background` …），
   `.card-hover:hover` 另 2 条。两个文件都后加载覆盖前者，所以"哪套生效"
   完全取决于导入顺序 —— 这正是 5.6 要消灭的形态。
   迁移时**先决定保留哪一套**，再搬。
4. **`refinements.css` 用内联样式字符串当选择器**（计划 F-18）：
   `[style*="rgba(0,0,0,0.5)"]` 命中 4 个组件的确认弹窗，
   浏览器把 `rgba(0,0,0,0.5)` 重新序列化成 `rgba(0, 0, 0, 0.5)` 就整块失效。
   迁移时应当**消灭这种选择器**（给弹窗一个真正的类），不是搬走它。
5. **`components.css` 的 `.container`** 被 `useReviewKeyboard.test.tsx` 按类名查询；
   `.sidebar*` 被 `App.test.tsx` / `Sidebar.test.tsx` 查询 ——
   迁移时必须同步改测试（优先改成语义查询）。
6. **`mobile-input-font-size.test.ts` 会拒绝任何新增的 `src/styles/*.css`。**
   它断言该目录每个样式表都被 `main.tsx` 导入。所以模块文件只能放组件目录，
   令牌也只能加在 `base.css`。
7. **`markdown-extras.css` 在 `responsive.css` 之后加载** —— 这个顺序本身被
   `mobile-input-font-size.test.ts` 断言（防止 `.ask-ai-input` 的窄屏兜底
   被压掉）。迁移时若允许组件 CSS 按需插入 `<head>`，这条断言的前提
   （"级联顺序 = main.tsx 的导入顺序"）就需要重新审视。

## 6. 本轮四道验证（全绿）

| 命令 | 结果 |
|---|---|
| `npm.cmd test` | 退出 0，**20 files / 272 tests**（与迁移前完全一致，未新增/未减少） |
| `npx.cmd tsc --noEmit` | 退出 0 |
| `npm.cmd run lint` | 退出 0 |
| `npm.cmd run build` | 退出 0，`dist/assets/index-*.css` 55.11 kB |

**没有修改任何测试**：试点切片的测试本来就按 `getByRole` / `getByText` 查询
（`QuizAnswerCard.test.tsx`、`SelfRatingButtons.test.tsx`），
只有 `ReviewProgress.test.tsx` 按类名查 `.progress-bar-fill` ——
而那个类名按 §3.3 刻意留在了全局。
