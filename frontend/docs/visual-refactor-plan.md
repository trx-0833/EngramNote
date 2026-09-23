# 前端视觉整改计划（v1.0）

> **执行依据**：`frontend/docs/visual-design-spec.md`（设计规范，二审已通过）
> **问题依据**：`frontend/docs/visual-audit.md`（三路审计）
> **借鉴依据**：`docs/visual-symbol-research.md`（13 个同类产品 + 11 个设计语言）
>
> **口径**：每批 = 一个可独立验收、可独立提交的改动单元。
> 每批完成后**立即本地提交**（不推送），全批做完再集中汇报。
>
> **状态图例**：⏸ 未开始 ｜ 🟡 进行中 ｜ ✅ 已完成 ｜ ⛔ 已取消（附理由）

---

## 0. 计划总览

| 阶段 | 批次 | 内容 | 视觉变化 | 状态 |
|---|---|---|---|---|
| **0 准备** | 0.1 | 现状基线截图 | 无 | ✅ |
| | 0.2 | `/styleguide` 骨架 | 无 | ✅ |
| | A0 | **批次外**：修死代码自检的回溯窗口失效（9/21 → 21/21） | 无 | ✅ |
| | A0b | **批次外**：dev server 不再监听测试产物目录 + 探针独立成 project | 无 | ✅ |
| **A 地基** | A1 | 令牌层扩容 | 无 | ✅ |
| | A2 | 清理 8 个零引用令牌 + 2 个零用户类 | 无 | ✅ |
| | A3 | 去渐变 + 中文标题字距归零 | **有** | ✅ |
| | A4 | 颜色收敛（旧值 / 两套色阶 / 兜底色去重） | **有** | ✅ |
| **B 图标** | B1 | `Icon.tsx` 出口 + 导航 13 图标 | 无 | ✅ |
| | B2 | 替换侧边栏 Unicode（+ 退出/汉堡两个新图标） | **有** | ✅ |
| | B3 | 替换其余 Unicode / emoji / 内联 SVG（实际补齐到 **46** 个图标） | **有** | ✅ |
| **C 布局** | C1 | `<PageHeader>` + 标题统一 1.5rem | **有** | ✅ |
| | C2 | 页宽三档收敛 | **有** | ✅ |
| | C3 | 列表形态与筛选控件统一 | **有** | ✅ |
| **D 基座** | D1 | `<Dialog>` 基座 + `--z-*` 令牌 | 无 | ✅ |
| | D2 | 迁移 5 套 modal | **有** | ✅ |
| | D3 | 替换 14 处 `window.confirm`（**拆两半**：组件内 7 处 → hook 内 7 处） | **有** | ✅ |
| | D4 | `:focus-visible` 统一光环 | **有** | ✅ |
| **E 逐页** | E1–E8 | 8 个页组精修 | **有** | ✅ |
| **F 收口** | F1 | `/styleguide` 补全 | 无 | ✅ |
| | F2 | 设计冗余门禁（基线已于阶段 E 全部落地后设定） | 无 | ✅ |

**批次总数 26**（0 阶段 2 + A 阶段 4 + B 阶段 3 + C 阶段 3 + D 阶段 4 + E 阶段 8 + F 阶段 2）。**依赖顺序：0 → A → B/C/D（三者可并行）→ E → F。**

---

## 1. 阶段 0 · 准备

### 0.1 现状基线截图 ✅

| 项 | 内容 |
|---|---|
| 产物 | `frontend/shots/*.png`（22 张，1440×900 桌面 + 2 张 375×667 移动端） |
| 工具 | `frontend/e2e/shot-probe.spec.ts`（一次性探针，复用 `e2e/a11y-fixtures.ts` 的非空业务桩） |
| 复现 | `cmd /c "npx playwright test --project=chromium shot-probe.spec.ts --workers=4"` |
| 入库 | 截图**不入库**（`.gitignore:153` 已加 `frontend/shots/`），探针 spec 入库 |
| 定位 | 它是"改前基线"，每一批的截图对比都拿它当参照 |

> ⚠️ 探针**不属于门禁**，`npm run e2e` 不会跑它（它没有断言，只是拍照）。改后重拍建议输出到 `frontend/shots-after/`，避免覆盖基线。

### 0.2 `/styleguide` 骨架 ⏸

| 项 | 内容 |
|---|---|
| 目标 | 先建一个"能看见令牌"的页面，后续每批往里加内容 |
| 新增 | `frontend/src/pages/StyleGuide.tsx` + `StyleGuide.module.css` |
| 路由 | 在 `App.tsx` 注册，但**用 `import.meta.env.DEV` 守卫**——生产构建里不注册、不进产物 |
| 首屏内容 | 色彩令牌色块（含名字与色值）／字号令牌样例／间距与圆角示意／阴影四档／缓动与时长 |
| 导航 | **不进 Sidebar、不进任何导航**，只能手输 `/styleguide` |
| 视觉变化 | 无（纯新增） |
| 验收 | `npm run build`（确认生产产物里没有它）／`npm run lint`／手输地址可打开 |
| 风险 | 低 |

---

## 2. 阶段 A · 地基（令牌与颜色）

### A1 · 令牌层扩容 ⏸

| 项 | 内容 |
|---|---|
| 文件 | `frontend/src/styles/base.css`（唯一改动） |
| 新增 | **字号 10 个**（值＝现状值，见规范 §5.1）／**时长 3 个** `--duration-fast:150ms` `--duration-base:250ms` `--duration-slow:400ms`／`--space-2xs:2px`／`--shadow-focus`／**页宽 3 个** `--width-reading:760px` `--width-standard:1200px` `--width-full:100%`／**层级 4 个** `--z-base:1` `--z-dropdown:100` `--z-drawer:200` `--z-modal:1000` `--z-toast:1100` |
| 视觉变化 | **无**（只新增定义，本批无引用者） |
| 验收 | `npm run build` + `node scripts/verify-built-css.mjs` |
| 风险 | 低 |
| 备注 | 本批会短暂产生"零引用令牌"——它们在 A3/C/D 各批里被用起来。**F2 的门禁要求"令牌必须有引用者"，所以 F2 必须在 A–E 全部完成后才启用。** |

### A2 · 清理零引用令牌与零用户类 ⏸

| 项 | 内容 |
|---|---|
| 文件 | `base.css`、`components.css` |
| 删除 | `--radius-xl`／`--color-surface-elevated`／`--gradient-accent`／`--gradient-warm`／`--ease-out-quart`／`--ease-in-out`／`--color-primary-rgb`／`--color-accent-rgb`／`.gradient-text-gold`／`.glass-effect` |
| **前置动作** | **逐个 grep 重新确认零引用**——审计给的是 2026-09-23 快照；若发现引用，**该令牌不删**并记入本表 |
| 视觉变化 | 无（零引用） |
| 验收 | `npm run build` + `verify-built-css.mjs` + 全仓 grep 无残留引用 |
| 风险 | 低 |
| 备注 | `--graph-ink` / `--graph-ink-faint` / `--graph-paper-deep` **不删**——它们是零引用，但属于"该接通而没接通"，在 A4 接通 |

### A3 · 去渐变 + 中文标题字距归零 ⏸

| 项 | 内容 |
|---|---|
| 文件 | `frontend/src/styles/components.css` |
| 改动 1 | `.gradient-text`（`:355-360`）：删掉 `background` / `-webkit-background-clip` / `-webkit-text-fill-color` / `background-clip`，改为 `color: var(--color-text)` |
| 改动 2 | `.btn-primary`（`:26-30`）：`background: var(--gradient-primary)` → `var(--color-primary)`；hover 由"抬升 + 加重阴影"改为"底色加深 + 轻微阴影" |
| 改动 3 | `h1, h2, h3`（`:402-407`）：`letter-spacing: -0.01em` → `0` |
| 视觉变化 | **有**：7 个页面标题从"看不见的渐变"变纯墨色；主按钮变实底；**18 个页面标题字距变松** |
| 验收 | ① 截图对比（重点看 Dashboard / Review / QA / 知识卡片）② `npm run a11y`——主按钮白字压在纯 `#0f3460` 上约 12.6:1，达标 ③ `npm run test` |
| 风险 | 中（用户可见）。回滚＝revert 单个 commit |
| 备注 | 这是"把墨韵做实"的核心一步，也是本计划里**第一个外观明显的批次** |

### A4 · 颜色收敛 ⏸

| 项 | 内容 |
|---|---|
| 文件 | `utils/labels.ts`／`pages/KnowledgeCards.tsx`／`components/graph/types.ts`／`graph/GraphCanvas.tsx`／`graph/GraphSidebar.tsx`／`graph/drawLink.ts`／`components/StatCard.module.css`／`cardreview/ScheduleFeedback.tsx` |
| 改动 1 | a11y 淘汰的旧值改回达标值：`#9a9ab0` → `#6f6f8a`（5 处）、`#2d8a56` → `#25714a`（4 处） |
| 改动 2 | **消灭"掌握度两套色阶"**：删除 `KnowledgeCards.tsx:39-43` 的 `getMasteryColor()`，改为 import `utils/labels.ts` |
| 改动 3 | `#6b7280`（13 处）：统一到 `--color-text-tertiary` 的对应值；若确需中性灰，**新增一个令牌**而不是散写字面量 |
| 改动 4 | **canvas 28 处接通令牌**：`drawNode.ts` / `drawLink.ts` / `renderMinimap.ts` / `GraphCanvas.tsx` 在组件挂载时用 `getComputedStyle(document.documentElement).getPropertyValue('--graph-ink')` 读一次并缓存，取代字面量；`--graph-ink` / `--graph-ink-faint` / `--graph-paper-deep` 由此从"零引用"变为"有引用" |
| 改动 5 | `utils/labels.ts:183-187` 那段"我们这里没有门禁守着"的自述注释**更新**——F2 会给它加上门禁 |
| 视觉变化 | **有**（图谱关系色、掌握度徽章、若干中性灰） |
| 验收 | ① 截图对比（**图谱页**、知识卡片页最明显）② `npm run a11y` ③ `npm test` |
| 风险 | 中。canvas 取色若读不到变量会画出透明/黑色——**必须有回退值**，并加一条测试 |

---

## 3. 阶段 B · 图标体系

### B1 · `Icon.tsx` 出口 + 导航 13 图标 ⏸

| 项 | 内容 |
|---|---|
| 新增 | `frontend/src/components/Icon.tsx`（唯一出口）／`frontend/src/components/icons/`（图形）／`Icon.test.tsx` |
| 硬约束 | `viewBox="0 0 24 24"`／内容区 20×20／`stroke-width: 1.5`／`stroke-linecap/linejoin: round`／`fill: none`／`stroke="currentColor"`／尺寸只允许 **16 / 20 / 24** |
| API | `<Icon name="review-due" size={20} />`；默认 `aria-hidden="true"`（纯装饰），需要语义时显式传 `title` |
| 图标 | 先做**导航 13 个**：`dashboard` `today` `review-cards` `daily` `projects` `assessment` `goals` `notes` `trash` `cards` `graph` `qa` `questions` |
| 视觉变化 | 无（尚无引用者） |
| 验收 | `Icon.test.tsx`：13 个名字全部可渲染／三档尺寸／未知名字报错／默认 aria-hidden |
| 风险 | 低 |

### B2 · 替换侧边栏 Unicode ⏸

| 项 | 内容 |
|---|---|
| 文件 | `components/Sidebar.tsx:22-54`、`Sidebar.module.css:250-258`、`App.tsx:114` |
| 改动 | 13 个 `icon: '\uXXXX'` → 语义名；渲染处改 `<Icon name={item.icon} size={20} />`；`.sidebarItemIcon` 从"字符容器"改为"图标容器" |
| 顺带修 | 折叠态 logo 现在是裸字母 `'E'`（`Sidebar.tsx:108`）／退出图标 `←`（语义是"返回"不是"退出"）／移动端汉堡 `☰`（`App.tsx:114`） |
| 视觉变化 | **有，且是全站最显眼的一处**（侧边栏每页都在） |
| 验收 | `Sidebar.test.tsx`（现有）／截图对比／**真机看垂直对齐**（20px 图标与 12.8px 文字的基线要齐） |
| 风险 | 中（侧边栏有测试，且是三处"字体图标"假设的集中地） |

### B3 · 替换其余 Unicode / emoji / 内联 SVG ⏸

| 项 | 内容 |
|---|---|
| 图标补齐 | 从 13 个扩到 **30 个**（动作 7 + 状态 6 + 内容 4） |
| 关键统一 | ① **"关闭"统一字形**（现在 `Toast.tsx:68` 是 `✕`、同文件 `:208` 是 `×`）② **"删除/添加"补图标**（现在全站纯文字）③ **搜索框补图标**（现在只有 `NotesList.tsx:171` 有）④ 折叠箭头统一挂共享类（`DailyMaterials.tsx:569` 现在没挂，所以没有旋转过渡） |
| 替换清单 | `Toast.tsx` `KnowledgeCards.tsx` `QuestionSets.tsx` `DailyMaterials.tsx` `GraphCanvas.tsx` `GraphSidebar.tsx` `NewProjectForm.tsx` `NoteAskPanel.tsx` `AddNotesPanel.tsx` `ProjectsErrorBanner.tsx` `LearningAssessment.tsx` `Projects.tsx`（📂）`MarkdownReader.tsx` `QA.tsx` `ProjectsUsageNotes.tsx` + `EmptyState` / `ErrorDisplay` / `Login` / `Register` / `NotesList` 的内联 SVG |
| 内联 SVG 统一 | 现有 6 个 24 网格图标（`Login.tsx` 等）迁入 `Icon.tsx`；**统一渲染尺寸**（现在同一骨架有 18×18 与 16×16 两档，线宽视觉差 1.5px vs 1.33px）；修 `ErrorDisplay.tsx:29,32` 同文件 `strokeWidth` 2 与 2.5 并存 |
| 视觉变化 | **有** |
| 验收 | 截图对比（逐页）／`npm test`／**检查所有图标在 16px 下仍清晰** |
| 风险 | 中（触及 20+ 文件，但每处改动都是"换一个渲染"） |

---

## 4. 阶段 C · 布局与标题

### C1 · `<PageHeader>` + 标题统一 ⏸

| 项 | 内容 |
|---|---|
| 新增 | `frontend/src/components/PageHeader.tsx` + `.module.css` |
| API | `<PageHeader title="…" subtitle? actions? />` |
| 替换 | 18 个页面的内联 `<h1 style={{fontSize}}>` |
| 统一为 | `--text-xl`(1.5rem) + `heading-serif` + `letter-spacing: 0` |
| 影响面 | `Dashboard.tsx:157` **2→1.5rem**／`LearningGoals.tsx:193` **2→1.5rem**／`NotesList.tsx:155` **1.25→1.5rem**／`LearningAssessment.tsx:349` **1.75→1.5rem**／`ProjectsHeader.tsx:8` **1.75→1.5rem**／其余 12 页字号不变 |
| 顺带修 | ① `.assessment-title` 的问题 ② 导航名与页内 h1 不一致（"仪表盘"→"欢迎使用 EngramNote"、"问答"→"智能问答"、"笔记列表"→"笔记"）——**本批只登记，不改文案**（改文案属于产品语义） |
| ⚠️ 口径修正（执行时发现） | 本条原写"两页**借用别家模块**的类名（各自归还本模块）"——**与代码不符**。实测：`.assessment-title` 是**全局** `styles/assessment.css:34` 的类，两个页面各用一次，即"两个不相邻功能共用一个全局类"（正是 `css-convention.md` §4 判据 2 要求留全局的情形）。执行时的处理：两页各自换成 `<PageHeader>`，`.assessment-header` / `.assessment-title` / `.assessment-subtitle` 三者同时归零 → 一起删除并在原处留墓碑。**顺带消掉最后一处真渐变标题**（`background-clip:text` + `-webkit-text-fill-color:transparent`；A3 只改了 `.gradient-text`，没改它） |
| ⚠️ 执行前必做 | **先读 `e2e/a11y.spec.ts:1080-1099`**——那里有 4 条 `expectFontSizeUnchanged` 断言把 Dashboard 四张卡片的标题字号钉死了。本批改的是 h1，理论上不碰它们，但**必须先确认没有对 h1 的字号断言**，否则会红 |
| 视觉变化 | **有** |
| 验收 | `npm run e2e`／`npm run a11y`（26 场景）／逐页截图 |
| 风险 | 中高（触及 18 个页面） |

### C2 · 页宽三档收敛 ⏸

| 项 | 内容 |
|---|---|
| 文件 | `App.module.css:54-57`（把 `max-width: none` 改回受控）、`App.tsx:119`（`<main>` 按路由给宽度类）、18 个页面（删内联 `maxWidth`） |
| 三档 | `--width-reading: 760px`／`--width-standard: 1200px`／`--width-full: 100%` |
| 路由映射 | **reading(760)**：`/review` `/review/cards` `/review/quick/*` `/upload` `/assessment` `/qa` `/login` `/register`<br>**full(100%)**：`/graph`、笔记详情编辑态<br>**standard(1200)**：其余全部（Dashboard / 笔记 / 回收站 / 卡片 / 题库 / 今日 / 每日 / 目标 / 项目 / 404） |
| 视觉变化 | **有，且是全局性的**：全宽页在 1920 屏上从约 1616px 收到 1200px；`Upload` 640→760、`Review` 700→760、`QA` 800→760、`Assessment` 960→760 |
| 验收 | `npm run e2e`／`npm run a11y`／**在 1440 与 1920 两种宽度下截图** |
| 风险 | 中高（页宽是"每页都能看出来"的变化）。回滚＝revert 本 commit |

### C3 · 列表形态与筛选控件统一 ⏸

| 项 | 内容 |
|---|---|
| 网格阈值 | `Projects.tsx:96` 的 `340px` → 与 `KnowledgeCards.tsx:334` 统一为 `minmax(min(320px, 100%), 1fr)` |
| 手写 pill | `KnowledgeCards.tsx:231-248` 用内联样式手写的 pill 改用全局 `.filter-pill` |
| 答题宽度 | `CardReview.tsx:142` 的 `760` 并入 `--width-reading`（C2 已覆盖，此处仅复核） |
| 视觉变化 | 有（小） |
| 验收 | 截图对比／`npm test` |
| 风险 | 低 |

---

## 5. 阶段 D · 交互基座

### D1 · `<Dialog>` 基座 ⏸

| 项 | 内容 |
|---|---|
| 新增 | `frontend/src/components/Dialog.tsx` + `.module.css` + `Dialog.test.tsx` |
| 能力 | **焦点陷阱（Tab/Shift+Tab 循环）**／Esc 关闭／点遮罩关闭（可关）／`role="dialog"` + `aria-modal="true"` + `aria-labelledby`／打开锁 body 滚动（复用 `Sidebar.tsx:78-82` 的既有写法）／**关闭后焦点归位到触发元素** |
| 令牌 | 遮罩 `rgba(0,0,0,.5)` 统一为一处常量／圆角 `--radius-lg`／阴影 `--shadow-lg`／层级 `--z-modal` |
| 视觉变化 | 无（新增） |
| 验收 | `Dialog.test.tsx` ≥10 条：Tab 循环、Shift+Tab 反向、Esc、初始焦点、焦点归位、aria 三件套、滚动锁**在卸载时必然解除** |
| 风险 | 低 |

### D2 · 迁移 5 套 modal ⏸

| 项 | 内容 |
|---|---|
| 迁移 | `DeleteNoteDialog.tsx:13-31`／`VersionHistory.tsx:173-197`／`LinkManagerModal.tsx:60-84`／`Trash.tsx:259-277`／`LearningGoals.tsx:305-323` |
| **不迁** | `SelectionMenu.tsx`（选区锚定 popover）／`NoteAskPanel.tsx`（坐标定位浮层）／`Toast.tsx`（层）——形态不同，强行统一会破坏定位逻辑 |
| 顺带统一 | 遮罩 `0.4` → `0.5`（`LearningGoals.tsx:309` 是唯一的 `0.4`）／`z-index:1000`×6 与 `99` 全部改令牌 |
| 视觉变化 | 有（遮罩深浅、圆角、阴影统一） |
| 验收 | `npm test`／`npm run a11y`（焦点陷阱是本批新增能力，**a11y 场景里含"新建目标弹窗"**，会让它更严） |
| 风险 | 中（5 个组件各有测试或依赖） |

### D3 · 替换 14 处 `window.confirm` ⏸

| 项 | 内容 |
|---|---|
| 文件 | `CardDetail.tsx:71`／`CleaningPanel.tsx:74,105`／`VersionHistory.tsx:153`／`KnowledgeCards.tsx:182`／`DailyMaterials.tsx:248`／`LearningGoals.tsx:145`／`useNoteActions.ts:71,147`／`useNoteAnnotations.ts:87`／`useProjects.ts:132,199`／`useGraphMutations.ts:159,195` |
| 新增 | `frontend/src/components/ConfirmDialog.tsx`（基于 D1） |
| ⚠️ 必做（**执行时已更正：本条过期**） | 原文写"同步修改 `Projects.test.tsx:822-824` —— 它把 `window.confirm(` 的存在写成了断言，不改必红"。**实测已过期**：BB.8 收尾时那两处已从 `window.confirm` 改成裸 `confirm`，该测试现在断言的是 hook 里的 `if (!confirm(`，与组件无关。D3 前半没动它，35 条测试全绿。**D3 后半（hook 那 7 处）仍要留意它** |
| 视觉变化 | 有（原生 confirm → 样式化确认框） |
| 验收 | `npm test`（14 处调用点的测试）／手动逐个核对：**确认逻辑、取消逻辑、异步等待、失败提示一字不变** |
| 风险 | **中高**——这 14 处全是破坏性操作（删除、覆盖、清空、撤销），改错会丢数据。逐个核对是本批的主要工作量 |
| 备注 | 建议本批**每改 2-3 处就提交一次**，而不是 14 处一起提交 |

### D4 · `:focus-visible` 统一 ⏸

| 项 | 内容 |
|---|---|
| 改动 | 全局 `:focus-visible` 统一用 `--shadow-focus`；清理散落的焦点样式 |
| 现状 | a11y 已修过对比度与键盘可达性（26 场景 0 违规），本批是**统一表达**而非修缺陷 |
| 视觉变化 | 有（小） |
| 验收 | `npm run a11y`（**键盘走查断言会碰这里**，必须保持 0 违规） |
| 风险 | 中（a11y 有"真的按 Tab"的走查断言） |

---

## 6. 阶段 E · 逐页精修（8 批）

每批统一格式：**页面 → 具体改动（引自 `docs/visual-symbol-research.md` §C3）→ 截图对比 → 提交**。

| 批 | 页面 | 核心改动 | 视觉 |
|---|---|---|---|
| **E1** | Dashboard | 衬线大数字固化为一等规则；"今日到期 / 连续天数 / 掌握度"做成一横排卡片，每卡只讲一个数；去掉卡片渐变 | 有 |
| **E2** | 笔记列表 + 笔记详情 | 列表项**左侧 3px 类型色条**（Trilium 做法）、hover 才出操作按钮；详情页**视图切换 tab 提到顶部**（Markdown/PDF/视频/清洗对比）、元信息收进**窄竖轨**（Memos 的 property rail） | 有 |
| **E3** | 知识卡片 + 卡片详情 | 卡面白底细边框、背面转金色淡底；掌握度改**环形/圆点**（不用星星）；翻面动效 ≤200ms 且尊重 `prefers-reduced-motion` | 有 |
| **E4** | 知识图谱 | **关系类型改用线型区分**（实线=前提 / 虚线=相关 / 点线=对比）；接通 `--graph-*` 令牌；当前节点支持键盘上下切换；图例改为线型说明 | 有 |
| **E5** | 题库 / 问题集 | 只抄 Anki 三件：**可配置列 + 点列头排序**（默认 4–6 列）、行背景三态高亮、把 s/d/r 做成筛选维度；筛选用可点击 chip（**不抄搜索语法**） | 有 |
| **E6** | **复习页（核心）** | 四档**固定底部横排**，每档显示 **FSRS 实时算出的下次间隔**；**失败在左、通过靠右**；中文**不把 Hard 直译"困难"**，改"想了很久才想起来"；答题后**印章**动效标记"已内化" | 有 |
| **E7** | 问答 | 引用来源做**常驻右栏**（Khoj 的 Reference Panel），不做折叠角标；输入框带**检索范围**（全部/当前笔记/当前项目） | 有 |
| **E8** | 其余 9 页 | 每日材料（时间线、留白分隔、返回恢复滚动位置）／学习目标（进度条 + 右侧百分比）／项目（色条 + 计数 chip）／学习评估／回收站（表格化 + 描边危险按钮）／上传／登录注册（渐变**只留这里**）／404 | 有 |

> **E6 是唯一建议单独排期、单独验收的一批**——它是产品的核心页，也是"AI 预选一档待确认"这类交互新能力的落点。

---

## 7. 阶段 F · 门禁与收口

### F1 · `/styleguide` 补全 ⏸

| 项 | 内容 |
|---|---|
| 补 | 46 个图标排成表（按语义分组，**缺哪个一眼看出**）／组件状态矩阵（按钮 4 态 × 4 变体、输入框 3 态、徽章、卡片）／Dialog 演示／`prefers-reduced-motion` 下的动效预览 |
| 视觉变化 | 无 |
| 验收 | 手输地址可打开；生产构建不含它（`import.meta.env.DEV` 守卫） |

### F2 · 设计冗余门禁 ⏸ **（本计划最重要的收口）**

项目现在对**回归**零容忍（死动画、级联得主、退休类名都有检查），但对**设计冗余**完全没有度量——这正是 11 个零引用令牌、272 处硬编码色值能长期存活的原因。

| 新增检查 | 报什么 | 防呆 |
|---|---|---|
| **令牌必须有引用者** | `base.css` 里定义了但 `var()` 零引用的令牌 | 表为空 ⇒ 报错（防检查失明） |
| **硬编码色值不许增长** | `src/**` 里 hex/rgb/hsl 字面量的**总数**与**基线**比较 | 基线写在脚本里并注明统计日；超基线即红 |
| **裸 `font-size` 不许增长** | 同上，统计非令牌的字号字面量 | 同上 |
| **新样式必须走令牌** | 对新增文件/新增规则做增量检查 | 与"基线 + 增量"配套，避免一上来红一片 |

**启用的前提**：A–E 全部完成、存量已收敛到基线。**在 A–E 完成前启用它会红一片**，所以它排在最后。
**登记方式**：与既有 `CLEANUP_RETIREMENTS` / `MEDIA_WAR_RULES` 同风格——**空表 = 报错**，不允许退化成永远绿灯。

---

## 8. 全局约束（每批都必须遵守）

### 8.1 门禁（AGENTS.md §5）

```bash
# 在 frontend/ 下
npm run lint          # ESLint，阻断
npm run format:check  # prettier，阻断
npm test              # vitest，阻断
npm run build         # tsc + vite build，阻断
npm run e2e           # Playwright（桩掉 /api），阻断
npm run a11y          # axe-core 26 场景，阻断
node scripts/verify-built-css.mjs   # 产物级：悬空动画/级联得主/退休项
node scripts/design-drift.mjs       # 设计冗余：零引用令牌 / 硬编码色值 / 非令牌字号
```

> 最后一条是**批次 F2 启用**的（计划 §7 写的"启用的前提：A–E 全部完成、
> 存量已收敛到基线"）—— 在那之前跑它必然红一片，基线也失去意义。
> `BASELINE` 已于 2026-09-23（阶段 E 全部落地后）设为
> 硬编码色值 228 / 非令牌字号 408。**这两个数是水位线不是目标**：
> 门禁只拒绝"比它更高"，想压下来是单独一轮的事。

**每批完成即本地提交，不推送。**

### 8.2 CSS Modules 雷区（`frontend/docs/css-convention.md` §3，都是实测踩过的）

| 雷区 | 规矩 |
|---|---|
| `@keyframes` 名会被哈希 | 模块里要用动画，必须**把动画体复制进模块并改名**；全局层只放跨功能共用或行内引用的动画 |
| 类名哈希后 `responsive.css` 选不中 | 响应式规则**跟组件一起搬进模块**，断点沿用 768 / 480 |
| 新增与全局类打架的模块规则 | 必须登记进 `CASCADE_PAIRS`；同权重同文件靠先后竞争的登记进 `ORDER_PAIRS` |
| 不要"顺手统一"改外观 | 统一按钮基线这类事**单开一轮 + 带截图**（本计划 A3 / C1 / C2 就是那几轮） |

### 8.3 截图对比配方（避开 junction 清空 `node_modules` 的坑）

```powershell
# 改动前
Copy-Item -Recurse frontend\dist <快照目录>
# 之后两个产物各起一个预览
npx vite preview --outDir <快照目录>
```
改动后用同一探针拍 `frontend/shots-after/`，与 `frontend/shots/` 逐页人眼对比。

### 8.4 沙箱注意事项（本次会话实测）

`npm run e2e` / `npm run a11y` 走 Playwright 的 `webServer`，它用管道捕获子进程输出，在受限沙箱下会 `spawn EPERM` —— **每次跑这两条都需要放宽一次权限**。

---

## 9. 风险登记表

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| 1 | 全站外观变化在某处"塌陷"（布局依赖了旧值） | 高 | 逐批提交 + 每批截图对比 + 可单批回滚 |
| 2 | a11y 26 场景回归 | 高 | 每批跑 `npm run a11y`；对比度收敛方向都是"往达标值走" |
| 3 | **C1 撞上 a11y 的字号断言** | 中 | 执行前先读 `e2e/a11y.spec.ts:1080-1099` |
| 4 | **D3 改坏破坏性操作的逻辑** | 高 | 每 2-3 处提交一次；逐个核对确认/取消/异步/失败四条路径 |
| 5 | canvas 取色读不到 CSS 变量 → 画成透明/黑 | 中 | 必须写回退值 + 加测试 |
| 6 | A2 删令牌时发现新引用 | 低 | 不删并记录（审计是快照，执行时重跑 grep） |
| 7 | F2 门禁一启用就红一片 | 中 | 排在最后，且用"基线 + 增量"而非"零容忍" |
| 8 | Playwright 沙箱 EPERM | 低 | 每次授权，或调宽会话策略 |
| 9 | B3 触及 20+ 文件，遗漏某处 Unicode | 低 | 结尾用 grep 扫一遍所有 Unicode 符号字符（`[\u2190-\u27BF]` 等区段） |

---

## 10. 进度记录（执行时逐批填写）

| 批次 | 提交 | 日期 | 验收结果 | 备注 |
|---|---|---|---|---|
| 0.1 | — | 2026-09-23 | 22 张基线截图（可靠版本） | 截图不入库；`npm run shots` 可复现 |
| 0.2 | `6ab35f5` | 2026-09-23 | lint / test / build / verify 全过 | DEV-only，不进生产产物 |
| A0 | `fbad410` | 2026-09-23 | verify 三向自检 **9/21 → 21/21** | 批次外必要修复：窗口 40 → 500 + 读取缓存 |
| A0b | `14567b9` | 2026-09-23 | 探针 22/22 `h1Total=1`（修前 19/22 为 0） | 批次外必要修复：Vite watch 忽略产物目录 |
| A1 | `154060c` | 2026-09-23 | 门禁全过 | 零视觉变化 |
| A2 | `cbccaf2` | 2026-09-23 | 门禁全过 | 复核纠正 2 个假设（`--gradient-primary`/`--gradient-gold` 有使用者，保留） |
| A3 | `e377498` | 2026-09-23 | 探针实测：标题字距 22/22 → `normal`；主按钮 `background-image` → `none` | 第一处外观明显变化 |
| A4 | `3523da2` | 2026-09-23 | 门禁全过；`#9a9ab0`/`#2d8a56` 活代码 0 处 | 17 文件；顺带补上前两批的格式债 |
| B1 | `753363a` | 2026-09-23 | 门禁全过；真实栅格化自查：13 个墨迹包围盒全落在 20×20 内容区、4 个对称图标镜像失配 0.0% | 16 个新文件 |
| B2 | `9818a01` | 2026-09-23 | 探针实测：`sidebar-icon` 从 `font-size=16px`（Unicode 字符）变为 svg 的 `20×20 / stroke-width=1.5px`，且 `color` 随激活态变 | 顺带修掉"同一码点两处语义"（☰ 既是笔记列表又是汉堡） |
| B3 | `8d99f5e` | 2026-09-23 | 七条门禁全绿（含我补跑的 e2e、a11y 26 passed）；结尾 grep：Unicode 图标 **0**、emoji **0**、内联 SVG 只剩 2 个插画 | 图标 15→46（补了 14 个"清单点名但无图形"的必需项）；⚠️ subagent 误判「e2e 要真后端」而漏跑，由我补跑 |
| C1 | `4fb3640` | 2026-09-23 | 6 条门禁 + a11y 全绿；探针实测 18 个业务页 `page-title` 全部 `font-size=24px / 衬线 / letter-spacing=normal` | 18/18 页改用 PageHeader；顺带删掉 3 个全局类、消掉最后一处真渐变标题；字重统一 700→600 |
| C2 | `2aea18c` | 2026-09-23 | 7 条门禁全绿；探针实测 `content-main` 从「22/22 全是 `none`」变为 760px×6 / 1200px×12 / 100%×2 | ⚠️ `/today` 归 standard 是最大变化（600 → 约 1136px）；全局 `.container` 现零引用（待死代码清理） |
| C3 | `55cd0f4` | 2026-09-23 | 门禁全过 | 网格阈值 340→320 + 手写 pill 归一为全局类 |
| D1 | `6e899c8` | 2026-09-23 | 门禁全过；23 条用例 + 变异验证（注释掉 `focus()` → 2 条归位用例变红） | 抓到 `:global()` 不哈希的静默陷阱，D2 要照 Sidebar 写 |
| D2 | `954534b` | 2026-09-23 | 7 条门禁全绿；a11y 那条「学习目标：新建目标弹窗（role/aria-modal/Esc/htmlFor）」在基座上原样通过 | 5 套 modal 迁移 —— 迁移前**0 套有焦点陷阱**、1 套有 role；遮罩 0.4→0.5、z-index 1000→`--z-modal`、面板几何统一 560px/85vh |
| D2 收尾 | （紧随 `954534b`） | 2026-09-23 | 7 条门禁全绿；测试 337 → **338** | 补回被 D2 削弱的危险语义：`Dialog` 加可选 `titleTone='danger'`，**只有 2 处**真正不可恢复的操作用它（彻底删除 / 清空回收站）；软删除的「移入回收站」刻意不加 —— 让红色保持稀缺。证据：7 个 `<Dialog>` 调用点的扫描表 + 组件行号区间 |
| D3 前半 | `604a266` | 2026-09-23 | 七条门禁全绿；live `confirm(` 只剩 4 个 hook 文件的 7 处 | 组件内 7 处已换；`Dialog` 加了可选 `footer`；⚠️ 新引入一条回退：触发元素 disabled ⇒ 关闭后焦点归位落到 body，留待后半一并修 |
| D4 | `8becd93` | 2026-09-23 | 七条门禁全绿 | 补全站唯一缺失的那一处键盘焦点光环：`styles/components.css` 加全局 `:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px }`；同时删掉零引用的 `--shadow-focus`（两个理由：`box-shadow` 会盖掉元素自身的阴影；`outline` 在 `forced-colors` 高对比模式下仍在，`box-shadow` 会没）。**边界**：没有把已有的 `:focus` 改成 `:focus-visible` —— 那是行为改动，见 §11 |
| D3 后半 | `d7440fe` | 2026-09-23 | 七条门禁全绿；测试 338 → **369**；grep 实测 hook 里的同步 `confirm('…')` 已清零 | 新增 `ConfirmProvider` + `useConfirm()`，7 处调用点写成 `if (!(await confirm({…}))) return`（形状与原来的同步 `if (!confirm(…)) return` 一致）。⚠️ **本批最险的一处**：`ConfirmDialog.handleConfirm` 是**先 `onCancel()` 关框、再 `onConfirm()`**，若把 Promise 的结账挂在 `onCancel` 上，点确认会先结出一个 `false` —— 调用方永远走取消分支、而框看起来是正常关闭的（**静默吃掉用户操作**）。为此给 `ConfirmDialog` 加了可选 `onConfirmClose`（不传时行为逐字不变）把"确认后的关闭"与"取消"解耦，并有一条专门用例钉住它。顺带修掉 D3 前半登记的焦点回退：`Dialog` 原来只判 `isConnected`，触发元素还在 DOM 但已 `disabled` 时 `focus()` 是静默 no-op ⇒ 焦点掉到 body；现在向上找第一个真正能收焦点的元素（非 disabled、不在 inert 子树、原生可聚焦或带 tabindex），整条链都不行时不强行 focus。`useNoteActions.handleCancelEdit` 由同步函数变为 async |
| E1 | `e28a2cb` | 2026-09-23 | 7 条门禁全绿（含 4 条钉住 Dashboard 卡片标题字号的 a11y 守卫） | 衬线数字固化为一等规则；数字从**渐变文字**改为焦墨实色（实色＝原渐变起点色，深浅一处未动）；四条色条去渐变。⚠️ StatCard 为两页共用，`/today` 受影响、待它所属页组核 |
| E2 | `07dd3e0` | 2026-09-23 | 七条门禁全绿，a11y **26 passed**；8 个文件 +588/-196 | 列表项左侧 3px 来源类型色条（Trilium 做法）+ 操作按钮 hover 才出；详情页元信息收进 220px 窄竖轨（新组件 `NotePropertyRail`，`<aside aria-label="笔记元信息">`，逐项搬迁、文案与可访问名一字未变）；视图切换 tab 提到页头；对比度修正：项目标签 4.62:1 → 5.49:1、角色下拉底 3.68:1 → 4.78:1；⚠️ 角色下拉的 `outline: none` 从**内联样式**移入样式表 —— 内联权重高于任何选择器，留着会让样式表的 `:focus-visible` 焦点环永远不生效；两栏用 `minmax(0, 1fr)`，正文里的长代码块 / 长 URL 不再顶开整页 |
| E3 | `4c90e17` | 2026-09-23 | 七条门禁全绿（`build` / `verify-built-css` **21/21** / `e2e` 10 / `a11y` 26 都是我复跑的，subagent 按分工禁跑构建类门禁）；测试 348 → **376**（本批 +21 条，另 +7 来自同期的 F1） | 卡面白底细边框 + 背面 `--color-accent-light` 金色淡底；掌握度改**环形**（新组件 `MasteryRing` —— 列表与详情共用同一个出口，学 `StatCard` 的抽法；环色只走 `currentColor`，所以 A4 那条"难度与掌握度色阶必须同源"没有被放宽）；翻面用 `--duration-fast`（150ms）+ `prefers-reduced-motion`（全站第 3 处，另两处是 `Dialog`/`NotesList`）。三处判断值得记：① `.cardFace`/`.cardFaceBack` 两面都用 `border`/`background` **简写** —— 一边简写一边长写会被 `verify-built-css.mjs` 的 `CROSS_CLASS_SHORTHAND_RULES` 记成未登记的跨类简写竞争；② 卡面嵌在全局 `.card` 面板里、没把模块类挂到带 `.card` 的同一个元素上（后者要动注册表），代价是内容内缩 24→41px，这是有意的；③ 金色用法与 `visual-design-spec.md:27` 的 R2「金色是印章、不是装饰」有张力，按**较新的计划文档**执行，且只用 12% 淡底、不拿金色当文字色（`--color-accent` 压 `#f9f5eb` 只有 2.07:1）。`CardDetail` 此前完全看不到掌握度，本批带上了同一枚环 |
| E6 | `0ffed1b` | 2026-09-23 | lint 绿；E6 相关测试全绿（`quiz/` + `Review` + `CardReview` = 7 files / **80 passed**；`SelfRatingButtons` 17 条含新增 4 条读磁盘布局护栏；`QuizAnswerCard` 21 条含新增 2 条印章用例）。⚠️ **全量 `test`/`build`/`verify-built-css` 本批没能复跑** —— 同期另一个 agent 正在改「项目」页，`ProjectCard.tsx` 已 import `./ProjectCard.module.css` 而该文件尚未创建，收集阶段直接失败；与本批无关（移除本批 6 个文件后同样失败），留待那一批落地后统一补跑；**随后**又修掉本批动过的那个文件里一处**既有**的非法 DOM（`QuizAnswerCard` 把 `<ul>` 嵌在 `<p>` 里 ⇒ DEV 下每次渲染都打 `validateDOMNesting`，且真被 HTML 解析器处理时 `<ul>` 会被顶出 `<p>`、结构就变了），独立提交 `9b83b62`，受控对照实测 1 条 → **0 条** | 计划四条逐条交代：① **四档固定底部横排** 做了 —— 原来布局全在内联样式里、用 `repeat(auto-fit, minmax(140px, 1fr))`，"几档一行"随可用宽度变（1250px 四档一行，窄一点掉成三档两档）；复习时每张卡都要自评一次，**档位位置跳动会直接变成误点**。现在恒为 `repeat(4, minmax(0, 1fr))` + `position: sticky; bottom: 0`（用 sticky 而不是 fixed：sticky 仍占文档流，滚到底停在原位不盖内容；fixed 要永久悬浮、得给每个调用方补等高下内边距）。② **每档显示 FSRS 实时算出的下次间隔** ❌ **没做，因为没有数据** —— 契约里只有提交后的 `interval_days`（`api/generated/schema.ts:3595`），没有"四档各自的预测间隔"；前端自算等于把调度算法实现两遍（两套必然漂移），**给后端加字段属于接口改动**，按 AGENTS.md §4 不擅自做，留用户决策。③ **失败在左、通过靠右** 已成立（`selfRatingOptions` 数据顺序就是 0/3/4/5），本批只把它写成明确的布局契约并加护栏。④ **不把 Hard 直译"困难"** 已成立（`utils/labels.ts:229-232` 用的是"完全忘记／勉强想起／想起来了／轻松想起"；`labels.ts:97` 的 `hard: '困难'` 是**题目难度**映射，与自评档位是两回事）。另加**自评印章**：复用已有的 `seal` 图标 + 一次盖章动效（1.6→1、-12°→0，`--duration-base`），`prefers-reduced-motion`（全站第 4 处）。用墨印而非绿勾是有意的 —— 绿勾在本项目表示"客观判对了"，自评是主观的、用户自己盖的章；金色印章正落在 design-spec 的 R2「金色是印章，不是装饰」上。`--color-accent` 压 `--color-bg` 不到 3:1 **刻意不修**：印章是 `aria-hidden` 纯装饰，旁边文字已把同一件事说全，信息不依赖这枚图形 |
| E7 | `89da9e4` | 2026-09-23 | lint 本批文件 0 problem；新增 `QA.test.tsx` **4 passed**（QA 页此前**没有测试**）。⚠️ 全量 `test`/`build` 仍被同期 E8 的中间状态挡住，留待那一批落地后统一补跑 | 计划两条：① **引用来源做常驻右栏**（Khoj 的 Reference Panel）做了 —— 页面改两栏，右栏 `position: sticky`；**刻意不做折叠角标**：这一页除提问之外唯一的操作就是"顺着引用跳原文"，把入口藏进一次点击等于降级成彩蛋。右栏**只跟最新一轮**，而卡片内那份**原样保留**（右栏是增强不是替代；窄屏 ≤768px 时右栏**自然堆叠**到对话流下方、不折叠，卡片内那份是唯一入口）。抽出 `SourceLinks` **两处共用** —— "带定位信息才能跳转"那三条判断各写一遍迟早有一边会忘记，后果是"跳到笔记开头"，用户会以为引用就是开头那段，比不给跳转更容易误导。② **输入框带检索范围** ❌ **没做，没有 API** —— `askQuestionStream(question, signal)` 只收两个参数（`api/client.ts:471`），契约里也没有对应字段（`schema.ts` 的 `scope_notes`/`scope_folders` 是**学习目标**的字段），加字段属接口改动，按 AGENTS.md §4 留用户决策。顺带**收敛 1 处渐变**：`QA.module.css` 的用户提问气泡由 `--gradient-primary` 换成实色 `--color-primary`（那个渐变两端只有 1.365:1，**当背景根本看不出渐变**），并把 `base.css` 里那份"还剩 9 处"的账目同步改成 **8 处** —— 那份账目明文写着"每收敛一处都要同步这个数字" |
| E4 | `6471f1d` | 2026-09-23 | 七条门禁全绿；`KnowledgeGraph.test.tsx` 既有 **44 条断言一条未失效、一条未改**，新增 9 条 + 新文件 `types.test.ts` 9 条 | **线型 = 关系类型**：实线=前提／虚线=相关／点线=对比／**长虚线=后续** —— 计划只点名 3 类，但接口真有第 4 类 `subsequent`（`backend/openapi.json` 的 `RelationType`），只给 3 类会让"后续"与"相关"共用虚线、类型之间又分不开，故按**代码**补第四种。**"待审=虚线"删除**，待审改用两条独立通道（更淡的 alpha + 不画箭头）—— 一处通道只能讲一件事。**接通 `--graph-*`**：grep 实测 `--graph-ink` / `--graph-ink-faint` / `--graph-paper-deep` 原本 **0 引用者**；CSS 侧写真 `var()`（墨晕 `::before` 用 `opacity:.08` 还原原手写 alpha），canvas 侧拿不到 `var()`、按设计规范 §6.4 用 `getComputedStyle` **读一次并缓存** + 同源回退值，落地了风险登记表 §9 第 5 条；实测 **76/76 令牌有引用者**。**键盘上下切换**：新 `useGraphKeyboardNav`（顺序取**数据顺序**而非坐标顺序 —— 力导向的 x/y 一直在变，按坐标排会让"下一个"连按时跳来跳去）+ 画布 `tabIndex=0` / `role="application"`（读屏浏览模式会截走方向键）/ `aria-live` 播报「当前节点：X（第 N / M 个）」。**图例改线型说明**：`repeating-linear-gradient` 与 canvas 的 `setLineDash` **同值**、两侧互指注释；工具栏那张是**节点**类型图例（讲卡片类型、不是线），刻意未动 |
| E5 | `02e851d` | 2026-09-23 | 七条门禁全绿；新增 `QuestionSets.test.tsx` **14 条**（这一页此前**没有测试**） | 计划说"只抄 Anki 三件"：① **可配置列 + 点列头排序** —— `<th scope="col" aria-sort>` 里包**真 `<button>`**（带 `onClick` 的 `<th>` 键盘到不了）；默认 5 列，落在"默认 4–6 列"；整页一个 `<table>` + **每组一个 `<tbody>`**，既满足排序又保住设计规范 §6.3 的"手风琴分组保留"，且列头只有一份 ⇒ Tab 停靠点不随笔记数放大。② **行三态高亮**取**题目难度**（Anki 的旗标/暂缓/已标记在 `quiz_items` 里根本不存在，照搬只会做出三个永不出现的状态）；为满足"不能只靠背景色传达"，**「难度」列定为不可隐藏**，那一格的中文徽章是文字载体。③ **s/d/r 三维数据层不存在，没有臆造**（逐层证据写在源码长注释里：题目响应只有 `question_type`/`difficulty`/时间戳；FSRS 的 s/d 在 `review_states` 表且**键是卡片**；r 全前端零字段）—— 把 chip 机制做成**数据驱动**的，后端一暴露按同一形状补三行即可。⚠️ **规避了一个极难归因的坑**：`e2e/a11y.spec.ts` 的题库场景点 `.first()` 再断言 **qi-1** 的答案，而桩**按数组原序返回**（真实后端才 `order_by(created_at desc)`）⇒ 默认排序若取 `created_at desc`，`.first()` 会指到 qi-2、断言以"找不到那句话"变红且失败信息指向答案文字而非排序。故默认排序取 `null`（行序 = 接口顺序，与改动前逐行一致），排序从第一次点列头开始。**另**：主 agent 补了一处类型修复 —— `compareQuestions` 形参含 `null` 而函数体直接取 `sort.key`，`tsc` 报两处 `TS18047`；**vitest 与 eslint 都看不见它**（esbuild 只剥类型、eslint 不做收窄分析），而本批被禁止跑 build，所以那处修复由我补并把因果写进了注释 |
| E8 | `e76f443` | 2026-09-23 | 七条门禁全绿；新增 3 个测试文件 6 条用例；既有断言**一条未改**（Projects 35 / App 4 / Icon 14 / PageHeader 5 / StyleGuide 7 全部原样通过） | 9 页**全部动过**（唯一刻意不动外观的是登录/注册的渐变）。**每日材料**：时间线（1px 竖轨 + 7px 圆点）、项间只留白不用边框、元数据收进胶囊 chip、行尾 Unicode `→` 换成自绘 `chevron`；**返回恢复滚动位置做成了**（模块作用域记忆、**仅 `useNavigationType()==='POP'` 才恢复**、`useLayoutEffect` 在绘制前滚回、文件夹已不在最近 7 天列表就只滚位置不发请求，2 条用例钉住 POP/PUSH）。**学习目标**：进度条右侧百分比（衬线 + tabular-nums），并把这条信息变成**语义**（`role="progressbar"` + `aria-valuemin/max/now`，缺名的 progressbar 会被 axe 判违规）；达成态金框用 `::after` 画 —— 用 `border-color` 会立刻变成未登记的跨类简写竞争。**项目**：色条顶→左，复用**已登记**的全局类 `.card-accent-left`（未新增注册项）。**学习评估**：评分条三条渐变→墨阶实色（原来 `#34d399`/`#f59e0b`/`#ef4444` 与底色只有 1.4:1，远低于 WCAG 1.4.11 的 3:1）；5 处逐字重复的内联小标签归一。**回收站**：真 `<table>` + 4 个 `<th scope="col">`；新增全局 `.btn-danger-outline`，**实底只留在确认框里那一下**（D2 收尾"红色稀缺性"的同一条纪律）。**上传**：拖拽区虚线由 `--color-border` 改 `--color-primary`，顺带修掉一个真问题（`#e8e6e1` 作为控件唯一边界与纸白底只有 1.1:1 → 12:1）。**登录/注册**：只把两页逐字重复的内联 form/error 搬进 `Auth.module.css`，**外观零变化**，渐变保留并加了守卫注释。**404**：从 `App.tsx` 的内联局部函数抽成计划点名的 `pages/NotFound.tsx` + 懒加载路由，文案/`href`/角色零改动。⚠️ 两处由主 agent 抽查后要求收口：① `css-convention.md` §7 的登记**被注释引用却并不存在**（这条约定**没有脚本强制**、只会静默漏掉）；② 新建的 `Trash.module.css` 与 `NotFound.module.css` 各有一个 `.title`，被 `verify-built-css` 的同选择器检查判成"同选择器、得主 unknown"（该检查把哈希名归一后**键不含文件**）⇒ **全量改名**而非登记：29 个新模块类加页面前缀，顺带消除了两处它没报但换个页面就会撞的同名（与 E5 的 `.table`、与 E2 的 `.noteTitle`） |
| F1 | `64e2a99` | 2026-09-23 | lint / build / verify-built-css(21/21) 全绿；新增 7 条用例（46 个名字逐一在表里、没有未归组段、不再登记 `--shadow-focus`、展示的是真实令牌值而非 label、4 变体各 2 态共 8 枚按钮） | 四条"待补"全部落地：**图标表**（46 个按语义分 5 组，名字取自 `ICONS` 注册表 —— 为此给 `Icon.tsx` 加了 `ICON_NAMES` 导出，因为 `icons/index.ts` 的文件头明确写了"页面不要直接 import 那里"；没归组的单独列一段并标红，新增图标忘了归组当场可见）、**组件状态矩阵**（4 变体 × 默认/禁用；hover 与 focus-visible 由真实伪类承担，静态页里停不住它们）、**对话框演示**（可点开的真实 `Dialog` + "该试哪三件事"）、**动效预览**（读 `matchMedia` 把本机偏好显示出来，也是全站第三处 `prefers-reduced-motion` 守卫，另两处是 `Dialog` / `NotesList`）。顺带修掉两处"清单在说谎"：`--shadow-focus` 已在 D4 删除却还登记着（会永远显示"未定义"，把已做出的决定伪装成待修的漂移）、`planned` 字段在 A1 落地后已成死字段。删掉零引用的 `.todoList` 并留墓碑 |
| F2 | `a7522ca` + `e598025` + **`25f42c3`** | 2026-09-23 | **已启用**（`25f42c3`）：完整门禁链一次跑通 **exit 0**（lint / format:check / test 38 files **424 passed** / build / verify-built-css **21/21** / e2e **10** / a11y **26**（0 违规）/ design-drift ✓）；脚本实测 **76/76** 令牌有引用者、硬编码色值 **228**、非令牌字号 **408** | 工具就位那一版：顺手接线 `--duration-slow`；提交时漏跑 lint，由 C1 的 subagent 发现并修复。**启用那一版**（阶段 E 全部落地后）：把 `BASELINE` 从 `null` 改成实测值，并写明**这两个数是水位线不是目标**（门禁只拒绝"比它更高"，想压下来是单独一轮）；清掉 **7 条过期**的 `PENDING_WIRING` 登记（`--z-dropdown` 由 E6 的 sticky 底栏接线，`--space-2xs`/`--text-xs`/`--text-sm`/`--text-sm-alt`/`--text-base`/`--text-2xl` 由阶段 E 的逐页精修兑现），原地留墓碑；把它**补进 §8.1 的门禁命令块** —— 一个没人跑的门禁不算门禁（同时补上一直在跑却没写进文档的 `npm run format:check`）。剩余 6 条登记是真实欠账：`--z-base`/`--z-drawer`/`--z-toast`（还没有需要显式层级的元素）与 `--text-2xs`/`--text-base-alt`/`--text-md`（规范预留） |

---

> ## 收口：26 个批次全部完成
>
> | 阶段 | 批次数 | 状态 |
> |---|---|---|
> | 0 准备 | 2 | ✅ |
> | A 地基 | 4 | ✅ |
> | B 图标 | 3 | ✅ |
> | C 布局 | 3 | ✅ |
> | D 基座 | 4 | ✅ |
> | E 逐页 | 8 | ✅ |
> | F 收口 | 2 | ✅ |
> | **合计** | **26** | **✅** |
>
> 另有 3 项**批次外**工作，已就地登记在上表：`A0`（修死代码自检的回溯窗口失效，9/21 → 21/21）、
> `A0b`（dev server 不再监听测试产物目录 + 探针独立成 project）、`F2` 的工具部分。
> 期间还捎带修掉两处**既有**缺陷（都不在原计划里，各自独立提交）：
> `QuizAnswerCard` 的 `<ul>` 嵌在 `<p>` 里（DEV 下每次渲染都打 `validateDOMNesting`，
> 而真被 HTML 解析器处理时结构会变）、以及 E6 引入的一处对比度回归
> （四档自评的 sticky 底栏取了页面米色而非卡片纸白，使「勉强想起」掉到 4.42:1）。
>
> **最后一次完整门禁链**（§8.1 的 7 条 + `design-drift`）在 `25f42c3` 上跑通，**exit 0**。
>
> **仍未做、且需要平台侧决策**的两条登记在 **§11.1**（E6 的 FSRS 逐档预测间隔、E7 的问答检索范围）——
> 它们不是"决定不做"，而是缺后端字段，属接口改动。

---

## 11. 本计划明确不做的事

| 不做 | 理由 |
|---|---|
| 暗色模式 | 你的决定；令牌层只维护一套值 |
| 改字体加载方式 / 删 Google Fonts | 你的决定；FE-4 继续留档在 `docs/open-source-readiness.md:553` |
| 字号 scale 收敛（动值） | 你的决定；本计划只建命名，不动位移 |
| 引入 UI 组件库 / TanStack Query / Zustand | 零依赖调性；状态层是独立议题（见 `overhaul-plan` 5.2 / 5.3） |
| 改路由结构、术语文案、接口语义 | 属产品语义，需单独决策（审计里已登记，如「资料」同名异物） |
| 重构 5.2/5.3（数据层） | 与视觉无关，不混进本计划 |
| 修 5 处 `return null` / 静默失败 | 属行为缺陷，另开一轮 |
| **把已有的 `:focus` 改成 `:focus-visible`** | 批次 D4 的边界：那是**行为改动**（会改变鼠标用户的观感），需要单独一轮 + 逐处核对。D4 只补了"没有自定义焦点样式"的元素。实测现状：全站只有 1 处 `:focus-visible`，其余焦点样式都是 `:focus` |
| **删零引用的 `.gradient-text` / 全局 `.container`** | 死代码清理要进 `CLEANUP_RETIREMENTS` 三向自检 + 落证据文件，是单独一轮（5.6 收尾轮的先例）。两处都已零引用：前者因批次 C1 让 18 个页面改用 `PageHeader`，后者因批次 C2 让 `<main>` 改用全模块类 |
| **让 `ConfirmDialog` 的 `danger` 联动 `titleTone`** | 会一次改 7 个调用点的外观（D2 收尾批发现的），属于"统一危险表达"的单独一轮 |

### 11.1 计划要求、但**被 API 挡住**的两条（性质与上表不同）

上表是"决定不做"，下面两条是**想做但做不了**：当前契约拿不到所需的数据 / 参数，
而"给后端加字段"属于接口改动（AGENTS.md §4 的四类停止条件之一），
所以停在原地等决策 —— 记在这里是为了它们**不会随着批次推进而被忘掉**。

| 条目 | 卡在哪 |
|---|---|
| **E6「每档显示 FSRS 实时算出的下次间隔」** | 契约里只有**提交后**才有的 `interval_days`（`api/generated/schema.ts:3595`），没有"四档各自的预测间隔"这种**提交前**就能拿到的字段。前端自己按 FSRS 公式估算等于把调度算法实现两遍（本项目最忌讳的两套实现漂移，参见 `statusClass` 的历史），所以没做。后端若提供一个形如 `interval_previews: { again, hard, good, easy }` 的字段，E6 那一处即可补上 |
| **E7「输入框带检索范围（全部 / 当前笔记 / 当前项目）」** | `askQuestionStream(question, signal)` 只收这两个参数（`api/client.ts:471`），契约里没有对应字段 —— `schema.ts` 里的 `scope_notes` / `scope_folders` 是**学习目标**的字段，与问答无关。需要一个能让问答请求带上检索范围的新参数 |
