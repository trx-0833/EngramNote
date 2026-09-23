# 视觉符号与展现形式灵感库

> 调研对象：EngramNote（React 18 + TS + Vite + 纯手写 CSS Modules，零 UI 组件库依赖）
> 产出：A 同类产品（13 个项目）· B 通用设计语言（11 个库）· C 三张落地表
> **标注约定**：`【事实】`= 有链接可查；`【判断】`= 我的取舍意见。
> **怎么用这份文档**：先读 §0（现状核对）与 §D（只抄三件事），要落地某一页时再查 §C3 对应行，要定令牌时查 §C1/C2。A、B 两节是证据库，不必通读。
> 本文是**参考材料**，不是已生效的决策；采纳前请并入 `docs/overhaul-plan.md` 的决策附录。已知的相关未决项：`docs/open-source-readiness.md:553`（FE-4「首屏依赖 Google Fonts」）与本文 §C3「全局：中文排版」是同一件事的两个侧面。

## 0. 现状核对（先对齐，再建议）

| 项 | 实测 | 出处 |
|---|---|---|
| 依赖 | 无任何 UI/图标库；`react`/`react-dom`/`react-router-dom`/`marked`/`katex`/`dompurify`/`highlight.js`/`react-force-graph-2d` | `frontend/package.json:40-50` |
| 色彩令牌 | 墨蓝 `#0f3460`、金 `#c9a959`、米白 `#faf9f7`、中性色 3 档、success/warning/error 已按对比度压深 | `frontend/src/styles/base.css:8-62` |
| 衬线字体 | `--font-serif: 'Noto Serif SC'…` 已定义，**仅 5 处使用**（侧栏 Logo、StatCard 数字、Auth 标题、评估页标题、`components.css:410`） | `frontend/src/styles/base.css:74`；`Sidebar.module.css:83`、`StatCard.module.css:86`、`Auth.module.css:116`、`LearningAssessment.module.css:107,257`、`assessment.css:35`、`components.css:410` |
| 字体加载 | `index.html` 从 **Google Fonts CDN** 拉 Noto Serif SC 400/600/700（`display=swap`） | `frontend/index.html` |
| 图标 | **没有图标库**；侧栏导航用 Unicode 字符临时充当图标：`⌂ ◘ ↻ ▷ ▣ ✓ ◉ ☰ ♲ ◈ ◎ ❓ ☑` | `frontend/src/components/Sidebar.tsx:25-51` |
| 内联 SVG | 全项目仅 5 个 `.tsx` 文件含 `<svg>`（EmptyState / ErrorDisplay / Sidebar / Toast / Login 等） | `frontend/src/**` grep `<svg>` |
| 圆角/阴影 | `--radius-sm 6 / md 10 / lg 16 / xl 24 / full 9999`；阴影 4 档，色相统一 `rgba(26,26,46,…)` | `frontend/src/styles/base.css:77-92` |
| 图谱 | 独立"宣纸"令牌：`--graph-paper #f5f0e4`、`--graph-ink`、装裱画框线 | `frontend/src/styles/base.css:113-119` |

**现状一句话**【判断】：色彩与圆角尺度已经相当克制且有体系，真正"散"的是**图标层**——Unicode 字符在不同系统上会渲染成彩色 emoji（`♲`）或几何符号（`◈ ◎ ▣`），字重、基线、光学尺寸全都不一致，这是当前最便宜也最高收益的修复点。

---

## A. 同类产品（笔记 / 知识库 / 学习类开源项目）

> 字段说明：**栈** = 框架/UI 库/样式方案；**图标** = 具体图标库；**骨架** = 界面分区结构。

### A1. AppFlowy

| 维度 | 内容 |
|---|---|
| **栈**【事实】 | Flutter（`flutter >=3.27.4`）+ Rust 后端（FFI）；**自研设计系统包 `appflowy_ui`**（v1.0.0，仅依赖 flutter / flutter_animate / cached_network_image）与 `flowy_infra_ui`；BLoC + SQLite + Yrs CRDT | [官方架构文档](https://appflowy-io-appflowy.mintlify.app/developer/architecture)、[pubspec.yaml](https://cdn.jsdelivr.net/gh/AppFlowy-IO/AppFlowy@main/frontend/appflowy_flutter/pubspec.yaml)、[appflowy_ui/pubspec.yaml](https://cdn.jsdelivr.net/gh/AppFlowy-IO/AppFlowy@main/frontend/appflowy_flutter/packages/appflowy_ui/pubspec.yaml) |
| **图标**【事实】 | **自绘 SVG 图标集 + 代码生成**：业务里 `import '.../generated/flowy_svgs.g.dart'`，资产按 **16x/20x/24x/32x/40x 分档目录**；另有专门按 `path+size` 缓存 `SvgPicture` 的性能 PR。（**更正**：社区那个 `phosphoricons_flutter` 只是第三方 Flutter 包，AppFlowy 并未使用） | [table_option_action.dart](https://github.com/AppFlowy-IO/AppFlowy/blob/41ca1dd8/frontend/appflowy_flutter/lib/plugins/document/presentation/editor_plugins/table/table_option_action.dart)、[PR #8731](https://github.com/AppFlowy-IO/AppFlowy/pull/8731) |
| **骨架**【事实】 | **三栏：左侧栏 + 中间文档区 + 右侧面板**；左侧栏是"命令中心"，**workspace 切换器放在侧栏左上角**（而不是设置里），导航靠 Quick Search 与面包屑 | [官方文档 Workspace Basics](https://appflowy-io-appflowy.mintlify.app/getting-started/workspace-basics)、[sidebar.dart](https://github.com/AppFlowy-IO/AppFlowy/blob/41ca1dd8/frontend/appflowy_flutter/lib/workspace/presentation/home/menu/sidebar/sidebar.dart) |
| **字体**【事实】 | Poppins（9 个字重本地打包）+ Roboto Mono，并预留 `WHITE_LABEL_FONT` 注入位；**AppFlowy-Web 是另一个仓库、用 MUI + Tailwind**（两端视觉不同源） | [pubspec fonts 段](https://cdn.jsdelivr.net/gh/AppFlowy-IO/AppFlowy@main/frontend/appflowy_flutter/pubspec.yaml)、[AppFlowy-Web tailwind/colors.cjs](https://github.com/AppFlowy-IO/AppFlowy-Web/blob/9aef5fec/tailwind/colors.cjs) |
| **可抄**【判断】 | ①**"自研 UI 包 + 业务包"严格分层**（`appflowy_ui` 几乎零依赖）——对应本项目"零 UI 库"策略：基础组件收进一个内部层，业务层禁止写原子样式；②**图标按尺寸分档 + 生成常量**，调用处没有魔法字符串，改图标不会漏改；③侧栏顶部放**空间切换器**，把"换空间"当一级动作 |
| **别抄**【判断】 | Flutter 渲染模型与 CSS 盒模型完全不同，其 design token 是 Dart 常量类；双端 Flutter+Rust 体量无法复用 |
| **结论**【判断】 | **"自研 UI 层 + 图标常量生成"两条理念可抄**，代码零可复用 |

### A2. AFFiNE

| 维度 | 内容 |
|---|---|
| **栈**【事实】 | **React 19.2** + react-router 7 + Vite 7 + Electron 39；组件层 `@affine/component` 用 **Radix UI 全家桶 + Emotion**，样式主体是 **vanilla-extract**（`*.css.ts`）并**正在迁往 Emotion**；token 在独立包 `@toeverything/theme`（1.1.23，解包 8.8 MB）；编辑器是 BlockSuite（Lit + React 包装） | [component/package.json](https://cdn.jsdelivr.net/gh/toeverything/AFFiNE@canary/packages/frontend/component/package.json)、[根 package.json](https://cdn.jsdelivr.net/gh/toeverything/AFFiNE@canary/package.json)、[theme dist/style.css](https://cdn.jsdelivr.net/npm/@toeverything/theme@1.1.23/dist/style.css)、[PR #12195 迁 Emotion](https://github.com/toeverything/AFFiNE/pull/12195) |
| **图标**【事实】 | **自绘** `@blocksuite/icons`：图标由 **Figma 导出脚本生成**（仓库里可见 `figma_file_id`/`figma_node_id`），同时产出 React 与 Lit 两套绑定 | [@blocksuite/icons package.json](https://cdn.jsdelivr.net/npm/@blocksuite/icons@2.2.17/package.json) |
| **骨架**【事实】 | `WorkspaceLayout > WorkbenchRoot`；左侧**常驻应用侧栏**（workspace 管理 + 文档导航）+ 多 view 并存（split view）+ 右侧检视栏（状态化，可右键常驻）；**命令面板优先**（依赖 `cmdk`，曾专项重做 CMD-K）；文档标题在顶部 header **内联可编辑** | [workspace/index.tsx](https://cdn.jsdelivr.net/gh/toeverything/AFFiNE@canary/packages/frontend/core/src/desktop/pages/workspace/index.tsx)、[detail-page-header.tsx](https://cdn.jsdelivr.net/gh/toeverything/AFFiNE@canary/packages/frontend/core/src/desktop/pages/workspace/detail-page/detail-page-header.tsx)、[PR #4408 CMD-K](https://github.com/toeverything/AFFiNE/pull/4408) |
| **令牌与排版**【事实】 | `--affine-*` 前缀，light/dark 两份；`--affine-brand-color: #1E96EB`、**`--affine-editor-width: 944px`**、`--affine-editor-side-padding: 96px`、popover 圆角 12px；字体含 **Source Serif 4 + Noto Serif** 且**单独留了 `--affine-font-serif-family`**；字号 title 36 / h1 28 / base 15 / xs 12；**标签色板 `--affine-tag-{gray,red,orange,yellow,green,teal,blue,purple,pink,magenta}`**，v2 再分 `chip/tag/*`（浅底）与 `chip/label/*`（深底） | [theme dist/style.css](https://cdn.jsdelivr.net/npm/@toeverything/theme@1.1.23/dist/style.css)、[detail-page-header.css.ts](https://cdn.jsdelivr.net/gh/toeverything/AFFiNE@canary/packages/frontend/core/src/desktop/pages/workspace/detail-page/detail-page-header.css.ts) |
| **可抄**【判断】 | ①**容器查询驱动的 header 降级**：`container-type: inline-size`，宽度 <500 隐藏分享、<400 隐藏演示与模板徽章、<300 隐藏收藏——**纯 CSS、零依赖、对"桌面+移动都要能用"最划算**；②**hover 才出现的拖拽把手**（header 左侧 -16px、宽 16、`opacity:0`，hover 或拖拽中才显示）；③**token 用业务语义命名空间**（layer/background、text/{primary,secondary,tertiary}、icon/*、chip/{tag,label}）而非 `gray-100`——最抗主题改动；④**正文 944px + 左右 96px** 作为固定阅读常量；⑤**衬线字体单独留 token**（正好对应本项目"衬线已定义但几乎没用"） |
| **别抄**【判断】 | ①BlockSuite + yjs 块级协同（数千文件量级）；②Radix + Emotion + vanilla-extract **三套样式方案并存**是迁移期包袱，不是榜样；③`@toeverything/theme` 单包 8.8 MB——本项目零依赖，引入即毁体积预算 |
| **结论**【判断】 | **最值得学的邻居**：容器查询降级 + 语义 token 命名 + 衬线 token，三条都能直接落到 CSS Modules |

### A3. SiYuan 思源笔记

| 维度 | 内容 |
|---|---|
| **栈**【事实】 | Electron 44 + Web 前端 **TypeScript 4.9 / webpack 5 / sass**，**无前端框架**（没有 React/Vue/Svelte）；**运行期依赖只有 2 个**（`@electron/remote`、`zxcvbn`）；编辑器是自研 "Protyle"（自绘 contenteditable） | [app/package.json](https://cdn.jsdelivr.net/gh/siyuan-note/siyuan@master/app/package.json) |
| **图标**【事实】 | **自研 SVG sprite**（`<symbol>` + `<use xlink:href="#iconXxx">`，约 250+ 枚，含 `iconPanelLeft/Right/Bottom(Dashed)`、`iconDock`、`iconLayout*` 等布局类图标），且**图标包本身可插件化替换**（`app/appearance/icons/` 目录 + icon.json 包格式）。（**更正**：此前推测的 Iconify 只出现在思源*插件*生态里） | [appearance/icons/index.html](https://cdn.jsdelivr.net/gh/siyuan-note/siyuan@master/app/appearance/icons/index.html)、[siyuan-note/icon-sample](https://github.com/siyuan-note/icon-sample) |
| **骨架**【事实】 | **左/右/下三条"停靠栏"只放面板入口按钮 + 页签 + 面板**；三者状态合成"布局"，可在左上角工作空间菜单保存/切换；`--sidebar-width: 200px` 是主题层变量 | [术语表](https://siyuannote.com/article/1749332107)、[快速上手](https://siyuannote.com/article/1724525755)、[daylight/theme.css](https://cdn.jsdelivr.net/gh/siyuan-note/siyuan@master/app/appearance/themes/daylight/theme.css) |
| **令牌体系**【事实】 | 主色 `--b3-theme-primary: #3575f0`、辅色橙 `#ff9200`、成功 `#65b84d`、错误 `#d23f31`，body 底 `#EBECF0`；**文字四级**（on-background/on-surface/on-surface-light/on-surface-lighter）；**圆角三档 3/6/12px**、间距 `--b3-layout-space: 4px`；语义色**前景+浅底成对**（如 info `#005599` on `#d6eaf9`）；**中文回退是按语言覆盖的**：`:root:lang(zh-CN)` 单独一套字族栈，注释里记着"把 arial 放在微软字体之前"以修斜体遮挡 | [themes/daylight](https://cdn.jsdelivr.net/gh/siyuan-note/siyuan@master/app/appearance/themes/daylight/theme.css)、[issue #11841](https://github.com/siyuan-note/siyuan/issues/11841) |
| **可抄**【判断】 | ①**"零依赖但有体系"的活样本**：它证明自研 CSS 变量 + sprite 图标 + 停靠栏足以支撑成熟产品；②**"角色-强度"命名**（`--b3-theme-on-surface-light`）比 `--gray-400` 更抗主题改动；③**圆角/间距只留 3–5 档固定刻度**；④**中文回退做成语言维度的显式覆盖**——这一条本项目现在就用得上（`base.css:73` 的栈里 `Noto Sans SC` 排在系统字体之后，中文实际常落到 PingFang/微软雅黑）；⑤**语义色前景+浅底成对**，对比度天然一致 |
| **别抄**【判断】 | TypeScript 4.9 + webpack + sass 的构建栈已落后 Vite 时代；无框架手写 DOM 的 Protyle 编辑器不可复用；与 Go 内核强耦合的 API/资源协议 |
| **结论**【判断】 | **令牌命名 + 中文回退 + 固定刻度**三条直接抄，架构不抄 |

### A4. Logseq

| 维度 | 内容 |
|---|---|
| **栈**【事实】 | **ClojureScript 主体**（shadow-cljs），React 19.2 只作渲染层；组件基座是 **`@base-ui/react@^1.5`**（不是 MUI/Radix）；样式 = **Tailwind 4.3 + PostCSS** 与自研 `--ls-*` 变量并存；`--ls-*` 调色板源自 Blueprint.js | [package.json](https://cdn.jsdelivr.net/gh/logseq/logseq@master/package.json)、[社区自定义示例](https://github.com/UNICKCHENG/logseq-developer-theme/blob/main/custom-color.md) |
| **图标**【事实】 | **Tabler Icons**（`@tabler/icons-react@3.44.0`）+ `emoji-mart` 做 emoji 选择 | [package.json](https://cdn.jsdelivr.net/gh/logseq/logseq@master/package.json) |
| **骨架**【事实】 | **两栏模型**：左 = 主编辑区，**右 = 次级编辑/引用区（可 Shift+点击开多个，且可编辑）**；左栏组件 `left_sidebar.cljs` | [官方论坛说明](https://discuss.logseq.com/t/is-logseq-developing-a-multi-windowed-mode/243)、[left_sidebar.cljs](https://github.com/logseq/logseq/blob/19a92947/src/main/frontend/components/left_sidebar.cljs) |
| **配色**【事实】 | 双层背景变量：`--ls-primary-background-color`（页面底）/ `--ls-secondary-background-color`（容器面）；字体依赖含 `inter-ui`（判断：主字体 Inter） | [论坛示例](https://discuss.logseq.com/t/css-code-that-controls-the-background-color/20649) |
| **可抄**【判断】 | ①**"右侧就是第二个可编辑工作区"**——不是只读检视器。对"一边读笔记、一边让 AI 生成卡片"的场景极契合；②**双层背景变量（页面底 / 容器面）足以表达层次**，不必给每张卡片加阴影——本项目 `base.css` 里 `--color-bg` 与 `--color-surface` 已经是这两个角色，可直接固化成规则；③反链以"块"为单位内联在正文下方，减少视线跳转 |
| **别抄**【判断】 | ClojureScript 完全不可移植（逻辑/组件/状态都在 `.cljs` 里）；代码块仍用 CodeMirror 5；一次性引入 pixi / sqlite-wasm / pdfjs 等重依赖 |
| **结论**【判断】 | **"右栏可编辑" + "双层背景"** 两条可直接抄 |

### A5. Trilium / TriliumNext

| 维度 | 内容 |
|---|---|
| **栈**【事实】 | pnpm monorepo，v0.105.0，**AGPL-3.0-only**；客户端自我描述是 **"JQuery-based client"**（`jquery 4.0.0` 仍在运行依赖），**同时**引入 Preact 10.29 + signals 与 react-i18next/react-window，构建用 **Vite 8**；编辑器是自研 `@triliumnext/ckeditor5` 与 `@triliumnext/codemirror` 分支 | [根 package.json](https://cdn.jsdelivr.net/gh/TriliumNext/Trilium@main/package.json)、[apps/client/package.json](https://cdn.jsdelivr.net/gh/TriliumNext/Trilium@main/apps/client/package.json)、[Custom Widgets 文档](https://docs.triliumnotes.org/user-guide/scripts/frontend-basics/custom-widget) |
| **图标**【事实】 | **混用且 README 明确列出**：系统托盘 = Tabler；导入对话框 = Material Design Icons / Font Awesome / SVGicons.com（各对应 OneNote、Notion、Obsidian、Anytype）；LLM 供应商 = Lobe Icons；UI 里另有 boxicons 字体 | [README Shoutouts](https://cdn.jsdelivr.net/gh/TriliumNext/Trilium@main/docs/README.md) |
| **骨架**【事实】 | FancyTree 笔记树 + 标签页 + 垂直/水平分屏；**右侧栏固定 4 个 tab：Outline / Attributes（可视化属性编辑器）/ AI chat / Connections**；新版把侧栏改为**常驻**，官方写明理由就是"此前'有内容才出现'导致切换笔记时内容跳动"，并新增面包屑 + 状态栏 | [Right Sidebar](https://docs.triliumnotes.org/user-guide/concepts/ui/right-sidebar)、[New Layout](https://docs.triliumnotes.org/user-guide/concepts/ui/new-layout) |
| **可抄**【判断】 | ①**"笔记可指定图标 + 颜色"**（颜色成为分类信号，作用于树/列表）——比只靠 emoji 强，且正好匹配本项目"按 sourceType 着色"的想法；②**右侧栏做成固定 tab 的常驻栏**，并记住官方的踩坑结论：**不要用"有内容才出现"的侧栏，否则切换笔记时整个布局跳动**（本项目"笔记详情"从 Markdown 切到清洗对比时最容易犯这个错）；③属性用可视化编辑器而非 YAML 文本框；④主题能力用具名 CSS 变量暴露，把"能调什么"变成契约清单 |
| **别抄**【判断】 | jQuery + FancyTree + Bootstrap 的 DOM 范式（jQuery 4 + Preact 双栈是迁移期产物）；CKEditor 5 自研分支 + Univer 表格体量巨大；**AGPL-3.0-only 有传染性——代码不可抄，只能借设计** |
| **结论**【判断】 | **类型色条 + 常驻右栏（防布局跳动）** 两条借它 |

### A6. Joplin

| 维度 | 内容 |
|---|---|
| **栈**【事实】 | Electron 42 + React **19.1.5** + redux；样式方案是 **styled-components 5.3 + styled-system**（运行时 CSS-in-JS），主题对象由 `themeStyle(themeId)` 提供；编辑器三选一（CodeMirror 5 / TinyMCE 6 / 纯文本）；桌面端已内置本地向量与推理依赖（`@huggingface/transformers`、`onnxruntime-node`、`sqlite-vec`） | [app-desktop/package.json](https://cdn.jsdelivr.net/gh/laurent22/joplin@dev/packages/app-desktop/package.json)、[TinyMCE.tsx](https://cdn.jsdelivr.net/gh/laurent22/joplin@dev/packages/app-desktop/gui/NoteEditor/NoteBody/TinyMCE/TinyMCE.tsx) |
| **图标**【事实】 | **Font Awesome 5 Free**（devDeps 里有 `@fortawesome/fontawesome-free@5.15.4`，源码里还有针对 TinyMCE 覆盖 FA 字体族的显式补丁）——即**字体字形方案**（此前论坛提到的 fork-awesome 已被 FA5 取代） | [TinyMCE.tsx](https://cdn.jsdelivr.net/gh/laurent22/joplin@dev/packages/app-desktop/gui/NoteEditor/NoteBody/TinyMCE/TinyMCE.tsx) |
| **骨架**【事实】 | 默认三段：笔记本列表 \| 笔记列表 \| 编辑器（编辑器常为"编辑区+预览区"两段）；**布局可由用户在 `Display > Change application layout` 里自由重排**（实现命令 `toggleLayoutMoveMode`）；全局跳转是 `Ctrl+P` 的 Goto-Anything 式搜索栏，**不是命令面板优先** | [第三方教程](https://klemet.github.io/Workshop-Organization-EN/02-joplin.html)、[toggleLayoutMoveMode.ts](https://fossies.org/linux/joplin/packages/app-desktop/gui/WindowCommandsAndDialogs/commands/toggleLayoutMoveMode.ts) |
| **可抄**【判断】 | ①**主题即一个扁平 JS 对象**（十几个语义键 + `toolbarHeight` 这类尺寸键）——零依赖等价做法：`themes/light.ts` 导出一张 CSS 变量表，运行时写进 `:root`；②**`whiteBackgroundNoteRendering`**：正文渲染底色与 UI 主题解耦（暗色下也能白底阅读/导出），对打印、截图、分享很实用——本项目的 PDF 预览与"清洗对比"都用得上；③**两级钻取**（笔记本 → 笔记列表）比"全局列表 + 筛选器"更省认知 |
| **别抄**【判断】 | ①styled-components（运行时 CSS-in-JS）+ redux 老范式，与 CSS Modules 静态方案冲突；②TinyMCE 6 / CodeMirror 5 均为旧世代，其主题覆盖代码满是 `!important`；③**webfont 图标方案**：字体字形无法按需 tree-shake、无法调线宽、与中文字体基线冲突——这正是本项目要避开的坑（对比 C1 的 SVG 方案） |
| **结论**【判断】 | **反向对照组**：它示范了"图标库选错"与"运行时 CSS-in-JS"的长期代价；可抄的只有"主题即变量表"与"正文底色解耦" |

### A7. Memos

| 维度 | 内容 |
|---|---|
| **栈**【事实】 | Go 后端 + React 前端（新版 Web 目录已重构，含 `web/src/pages`、`web/src/contexts`）；自托管、单条 memo 时间线 | [usememos/memos](https://github.com/usememos/memos)、[仓库源码树](https://github.com/usememos/memos/tree/main/web/src)、[官网](https://usememos.com/) |
| **图标**【事实】 | **Lucide**：源码直接 `import { ArchiveIcon, CheckIcon, GlobeIcon, LogOutIcon, PaletteIcon, SettingsIcon, SquareUserIcon, User2Icon } from "lucide-react"` | [web/src/components/UserMenu.tsx](https://github.com/usememos/memos/blob/30c0611a/web/src/components/UserMenu.tsx) |
| **骨架**【判断】 | 极简单栏信息流 + 左/顶轻导航；**没有三栏**，卡片之间只用间距分隔 |
| **可抄**【判断】 | ①**极简时间线卡片**：头像/时间同行、正文独占、操作按钮 hover 才出现——本项目的"今日资料""复习记录"可以照这个删减；②**卡片之间用留白分隔而非边框**，视觉噪音立降 |
| **别抄**【判断】 | 无层级、无标签体系、无结构化元数据，"轻"到无法承载"资料 → 卡片 → 复习"的状态机 |
| **结论**【判断】 | **借它的"减法"**：状态元数据收进 chip，正文独占 |

### A8. Anytype

| 维度 | 内容 |
|---|---|
| **栈**【事实】 | 本地优先 / 对象模型（Object + Type + Relation）；客户端有多套实现（含 TS 客户端） | [anytypeio](https://github.com/anytypeio) |
| **图标**【事实/判断】 | 未核实到其图标库；界面以"对象类型图标可自定义 + 色彩"为主 | — 见文末证据缺口 |
| **骨架**【判断】 | 左侧空间/类型导航 + 主区对象视图 + 右侧属性面板（Relation 列表）；**对象即一切**（笔记、任务、人都是对象） |
| **可抄**【判断】 | ①**右侧属性面板的"键值行"排版**（灰标签 + 值 + 悬浮编辑）——本项目"卡片详情""笔记元信息"可直接用；②**类型图标 + 类型色**成对出现（同一语义用同一对配色） |
| **别抄**【判断】 | 对象/关系模型对用户是重概念负担，中文用户在"类型"概念上更容易迷失；其 P2P 同步与本项目中心化后端冲突 |
| **结论**【判断】 | **属性面板排版**可借；概念模型不借 |

### A9. Obsidian（闭源，可参考）

| 维度 | 内容 |
|---|---|
| **栈**【判断】 | 闭源 Electron；主题 = CSS 变量 + 社区 CSS 片段，插件可注入 UI |
| **图标**【事实】 | **官方全站使用 Lucide**，并强制 Lucide 规范（24×24 画布、≥1px 内边距、**2px 描边**、round join/cap）；中文帮助里也直接引用 `lucide-cog.svg` 这类 id | [Obsidian 开发者文档：Icons](https://docs.obsidian.md/Reference/CSS+variables/Foundations/Icons)、[中文帮助：功能区](https://obsidian.md/zh/help/ribbon) |
| **骨架**【事实】 | 桌面端三大区：**左侧栏（内含竖排 ribbon，折叠后 ribbon 仍在）+ 编辑区（多 tab group，可左右/上下分屏）+ 右侧栏（可独立折叠）**；命令面板 `Ctrl/Cmd+P` 条目右侧内联显示快捷键；状态栏在右下角且是可注册槽位；移动端侧栏默认折叠，底部导航条替代 ribbon | [官方帮助 Workspace（镜像直读）](https://obsidianmd-obsidian-help.mintlify.app/ui/workspace.md)、[Obsidian 中文帮助：侧边栏](https://obsidian.md/zh/help/sidebar) |
| **令牌体系**【事实】 | 400+ CSS 变量挂在 `body`，按 `.theme-light/.theme-dark` 分支；分 Foundations（Borders/Colors/Icons/Layers/**Radiuses**/Spacing/Typography）+ Components；**4px 网格**（`--size-4-1`=4px、`--size-4-2`=8px…）；`--radius-s` 默认 4px；强调色是 **HSL 三元组** `--accent-h/s/l` 可用 `calc()` 派生；**光标语义**：`pointer` 只给链接 | [主题迁移指南](https://obsidian.md/blog/1-0-theme-migration-guide/) |
| **可抄**【判断】 | ①**Lucide + 规范线宽**：Obsidian 证明了 Lucide 能撑起"长时间阅读型"界面（见 C1）；②**"逻辑图标名 → 实现"的间接层**（界面写 `lucide-cog`，主题可替换）；③侧栏**标签页容器**思路可退化为"笔记详情页顶部视图切换 tab"；④**HSL 三元组强调色**——做"品牌色默认值 + 用户可覆盖"最省事的结构；⑤状态栏当可注册槽位（挂向量索引状态、今日待复习数） |
| **别抄**【判断】 | ①竖排 ribbon + 双可折叠 sidebar 是**桌面宽屏假设**，Web 端 + 中文正文会被持续挤压；②Reading/Live Preview 双渲染引擎（官方长期背"两模式不一致"的债）；③追 400+ 变量规模——文档必然与实现漂移；④插件各自写样式导致观感不一，本项目应保持视觉唯一真源 |
| **结论**【判断】 | **图标规范 + HSL 强调色 + 状态栏槽位**三条直接借 |

### A10. Anki（复习类）

| 维度 | 内容 |
|---|---|
| **栈**【事实】 | Python 应用层 + Rust 核心 + **Qt(PyQt)** 桌面壳；新版审查界面用 **Svelte 5 + SvelteKit + TypeScript** 重写，样式为 **Sass/SCSS**；卡片与底栏历史上是两个 webview lockstep 同步（官方正在合并为单页） | [PR #4289 Svelte port for reviewer](https://github.com/ankitects/anki/pull/4289)、[issue #3871](https://github.com/ankitects/anki/issues/3871)、[ts/reviewer/reviewer.scss](https://github.com/ankitects/anki/blob/main/ts/reviewer/reviewer.scss) |
| **图标**【事实】 | Web 侧用 **Material Design Icons（`@mdi/js`）**，在 `ts/lib/components/icons.ts` 里包成 `<svg>` 组件（因为用 `@mdi/svg`+`<img>` 时 `fill` 无法着色而改的） | [issue #3127](https://github.com/ankitects/anki/issues/3127) |
| **复习界面**【事实】 | 底栏 2–4 个评分按钮，**每个按钮上直接印下次间隔**（`10m` / `5d`）。DOM 结构是 `<button data-ease="N">{档位名}<span class=stattxt>{间隔}</span></button>`，**默认推荐档位额外挂 `id="defease"`**，`title` 里塞快捷键与说明；"显示答案"按钮自身也带 `stattxt` 槽位 | [qt/aqt/reviewer.py](https://github.com/ankitects/anki/blob/main/qt/aqt/reviewer.py)、[官方手册 Studying](https://docs.ankiweb.net/studying.html) |
| **评分语义**【事实】 | Again=`1` / Hard=`2` / Good=`3`(Space/Enter) / Easy=`4`；官方建议使用率：Again 5–20%、**Good 80–95%**；间隔预览会加随机 fuzz，学习卡还会附加最多 5 分钟延迟**但按钮不反映它**（即预览是近似值） | [官方手册 Studying](https://docs.ankiweb.net/studying.html) |
| **着色**【事实】 | **桌面端默认四按钮同色，不上色**。维护者原话：不反对上色（"移动端已经在用了"），但担心桌面端用户偏好不同而"又要多一个选项"；官方在讨论的另一条路是 **AnkiMobile 的做法——按 Again 闪红叉、其余闪黄/绿/蓝对勾**（反馈动效代替按钮配色）。另有 PR「给按钮加彩色边框」专门治"Hard 被误当失败" | [Anki 论坛 #40879](https://forums.ankiweb.net/t/add-visual-cues-to-make-it-clear-that-hard-is-not-a-failing-grade/40879)、[PR #4370](https://github.com/ankitects/anki/pull/4370) |
| **浏览器（表格）**【事实】 | **三区**：左侧栏（搜索/选择两种工具，`Alt+1/2` 切换）+ 右上卡片/笔记表格 + 右下编辑区，分隔条可拖拽；表格列可配置、可拖拽排序、点列头排序；**行背景色三态（取第一个匹配）**：有旗标→旗标色、被 suspend→黄、笔记被 marked→紫 | [官方手册 Browsing](https://docs.ankiweb.net/browsing.html) |
| **FSRS 相关**【事实】 | 搜索语法把记忆参数做成一等公民：`prop:s>21`（stability）、`prop:d>0.3`（difficulty）、`prop:r<0.9`（retrievability）；另有 `rated:1:2`（今天评过 Hard）等 | [官方手册 Searching](https://docs.ankiweb.net/searching.html) |
| **可抄**【判断】 | ①**"档位名 + 下次间隔"共用一个按钮**，间隔放在可被 CSS 单独控制的子元素里（`.answerButton__stat`），默认档位用 class 标记；②**固定 4 档位置**（Anki 会按卡片历史显示 2–4 个按钮，位置会跳变破坏肌肉记忆）；③**"失败在左、通过靠右"的空间语义 + 按钮上印间隔**，配合 AnkiDroid 的图标反馈；④题库页只抄三件：可配置列 + 行高亮三态 + **把 FSRS 的 s/d/r 做成筛选维度** | [AnkiDroid 新复习屏公告](https://forums.ankiweb.net/t/new-study-screen-official-thread/67394) |
| **别抄**【判断】 | ① Qt 原生控件观感与其信息密度（Card Browser 14 列 × 两种模式 × 20+ 搜索语法）；②双 webview lockstep 架构（Anki 自己正在废）；③把"配色决定权"推给用户做成开关（维护者自己承认会"多一个选项"）——直接定一套过 WCAG AA 的四档色；④**中文界面不要直译 Hard 为"困难"**，应写"想了很久才想起来" | 同上 |
| **结论**【判断】 | **"颜色 × 间隔 × 左右空间语义"三件套是本次调研最值得抄的单点交互**；浏览器表格只抄筛选维度 |

### A11. Reor（AI 笔记）

| 维度 | 内容 |
|---|---|
| **栈**【事实】 | Electron + React + TypeScript + **Vite**；**同时引入五套 UI 方案**（Tamagui、MUI、Mantine、Radix UI、Material Tailwind，并因 Tamagui 拖进 `react-native`）——第三方源码审计原话直接点名；编辑器后来换 BlockNote；向量库是 **LanceDB**，嵌入走 `@xenova/transformers`（WASM，主进程内） | [vite.config.mts](https://github.com/reorproject/reor/blob/main/vite.config.mts)、[codeline.co 源码审计](https://www.codeline.co/thoughts/repo-review/2024/reor-local-ai-knowledge-management-with-rag)、[PR #512 换 BlockNote](https://github.com/reorproject/reor/pull/512) |
| **图标**【事实/未核实】 | 依赖清单里未见可靠证据 | 见证据缺口 |
| **骨架**【事实】 | markdown vault 为数据源；**编辑器侧栏实时显示"相关笔记"**（打字时刷新，同一条向量检索管线）+ **Q&A 面板**（对整个 vault 提问、回答标注引用来源）；chat 与编辑器同屏 | [codeline.co 源码审计](https://www.codeline.co/thoughts/repo-review/2024/reor-local-ai-knowledge-management-with-rag)、[README](https://github.com/reorproject/reor/blob/main/README.md) |
| **检索细节**【事实】 | 两阶段检索 = ANN + `bge-reranker-base` cross-encoder 重排；按 markdown 标题优先分块；上下文累加到模型窗口 **90%** 时截断并把 `contextCutoffAt` 回传 UI；**表名编码嵌入模型与 vault 路径**（`ragnote_table_<model>_<vault>`），换模型 = 新表；靠文件 mtime 增量重嵌入 | [codeline.co 源码审计](https://www.codeline.co/thoughts/repo-review/2024/reor-local-ai-knowledge-management-with-rag) |
| **可抄**【判断】 | ①**"AI 与人类共用同一条检索管线"**（README 原话：*a RAG app with two generators — the LLM and the human*）——"AI 引用了哪些笔记"和"我写这条笔记时该看哪些笔记"应该是同一个出口，别做两套；②**把上下文截断点回传给 UI**（渲染成"因长度限制，以下内容未纳入本次回答"的可展开提示）——这是**信任度**功能；③**表名编码嵌入模型**：直接解决"换嵌入模型要重建索引"的迁移难题，本项目向量与原文同库同表，这条尤其值钱；④对索引状态做**实时可见的反馈**（侧栏随打字刷新），比一个"索引完成" toast 更有说服力 |
| **别抄**【判断】 | ①**五套 UI 库并存**是与本项目"零 UI 库 + 纯手写 CSS Modules"最直接对立的反面教材（第一名）；②为一个切分函数引入 langchain（40 MB+）；③把主题切换器塞在编辑器左下角（Web 中文场景会被输入法候选框遮挡） |
| **结论**【判断】 | **共用检索管线 + 截断点回传 + 表名编码模型**三条抄进架构；UI 库部分当反面教材 |

### A12. Khoj（AI 第二大脑 / RAG）

| 维度 | 内容 |
|---|---|
| **栈**【事实】 | Python（FastAPI 应用层 + Django ORM/admin）+ **Next.js + Tailwind + shadcn/ui + Phosphor Icons** 的新 Web 前端；前端产物直接打进 PyPI 包分发 | [Khoj release 1.20.0](https://github.com/khoj-ai/khoj/releases/tag/1.20.0)、[官方博客 new-ux-fresh](https://blog.khoj.dev/posts/new-ux-fresh/) |
| **图标**【事实】 | **Phosphor**：源码直接 `import { ArrowCircleDown, ArrowRight, Code, Note, Clipboard, Check } from "@phosphor-icons/react"` | [referencePanel.tsx](https://github.com/khoj-ai/khoj/blob/9258f57d/src/interface/web/app/components/referencePanel/referencePanel.tsx) |
| **骨架**【事实】 | **左栏（导航 + 会话历史 + 会话级文件过滤器）\| 中栏对话 \| 右栏 Reference Panel（列出生成回答用到的笔记）**；官方文档明说"You can use conversation file filters … use the **left panel** in the web UI" | [docs.khoj.dev/features/chat](https://docs.khoj.dev/features/chat/) |
| **字体**【事实】 | Next.js `layout.tsx` 里用 `noto_sans` + `noto_sans_arabic`（Noto Sans 家族） | [layout.tsx](https://github.com/khoj-ai/khoj/blob/9258f57d/src/interface/web/app/layout.tsx) |
| **可抄**【判断】 | ①**引用来源做常驻右栏**而不是折叠角标——这直接决定用户是否信任 AI 生成内容；②**左栏做"会话级文件过滤器"**（一个对话内限定"只查这几本笔记"），比全局检索设置更贴合"我正在学这一章"；③**斜杠命令分模式**（`/notes` 只用我的笔记、`/general` 只用通用知识、`/online`…）——中文可做 `/笔记` `/联网`，把"知识来源边界"显式化，成本极低；④**字体选 Noto 家族**（CJK 覆盖）直接对应 `Noto Sans SC` |
| **别抄**【判断】 | ①Next.js + SSG（官方选它是为了从 Jinja 平滑迁移，不是绿地最优解）；②FastAPI + Django 双框架（官方自承性能痛点）；③Agent/Automation/Code Execution/Voice 的功能面——会把"学习笔记"摊薄；④把 Chat 当首页主体（本项目是 note + review 优先） |
| **结论**【判断】 | **来源常驻右栏 + 会话级过滤器 + 斜杠命令**三条必抄 |

### A13. Heptabase（白板式，补充）

| 维度 | 内容 |
|---|---|
| **栈**【事实/判断】 | 闭源商业产品（非开源）；以"卡片 + 白板"为核心隐喻 | [heptabase.com](https://heptabase.com/)、[Getting started](https://wiki.heptabase.com/getting-started-with-heptabase) |
| **可抄**【判断】 | ①**笔记卡片有明确的"正面"视觉**（白底、细边框、投影仅 1 档），卡片之间靠画布表达关系而非层级；②**卡片高亮/批注**（2026-03 更新）——本项目"清洗对比""卡片正反面"可借它的"高亮块 + 边注"排版 | [Heptabase 更新日志](https://wiki.heptabase.com/newsletters/2026-03-24) |
| **别抄**【判断】 | 无边界白板对"学习目标 / 每日任务"这类有截止日期的流程没有帮助，容易变成"看起来很美但不用"的页面 |
| **结论**【判断】 | 借**卡片正面样式**与**高亮批注排版**，不引入白板 |

---

## B. 通用设计语言与组件库

> 评估基准【判断口径】：①中文优先；②"学术/书卷气"（低饱和、衬线标题、克制阴影）；③当前零依赖、CSS Modules 手写；④桌面 + 移动都要用。
> 体积为 bundlephobia **整包**数字（未 tree-shake 的最坏情况），版本为核实当日 npm latest。

| 库 | 样式方案 / React 18【事实】 | 视觉语言核心【事实+判断】 | 适配度【判断】 | 引入成本【判断】 |
|---|---|---|---|---|
| **shadcn/ui** | 复制源码，**硬依赖 Tailwind**；2026-07 起底座默认换成 Base UI | 冷灰阶、`--radius: 10px`、几乎无阴影；语义 token 命名（`background/foreground` 成对 + `ring` + `sidebar-*`）值得抄 | **不适合（作库）** | 需装 Tailwind v4 + `cn` + `cva` 等 ≥5 个包并改构建链。**非 Tailwind 项目不能直接用**；可当"源码级规格书"读结构，把类名逐条改写成 CSS Modules |
| **Radix Primitives** | 零样式、不发 CSS；统一包 `radix-ui@1.6.7`，`sideEffects:false`，peer React 16.8–19 | 无视觉主张，只给 a11y 与键盘/焦点管理 | **适合** | 低-中：约 53 个子包，摇树后常见 Dialog+Menu+Tooltip 数十 KB gzip。**绝不引入 `@radix-ui/themes/styles.css`**（会全局污染） |
| **Radix Themes** | 自带 vanilla CSS，官方声明"组件相对封闭、token 变更视为 breaking" | 中圆角、6 档阴影分层克制；字号带**负字距**（拉丁调优，套中文会挤字） | **不适合** | 1 包 + 63 KB gzip 全量 CSS；定制要对抗官方设计，回归成本高 |
| **Vercel Geist** | `geist` 是**字体包**（入口面向 `next/font`）；**组件未开源**；`@geist-ui/react` 是第三方同名项目、2022 年停更 | 无衬线、尺寸命名式排版（`text-copy-16`）；气质偏 Linear/Vercel | **部分适合（只取字体）** | 低：Vite 用 [`@fontsource/geist-sans`](https://registry.npmjs.org/@fontsource/geist-sans/latest)（OFL，可自托管）；只挂 `--font-latin/--font-mono`，不动 `--font-sans` |
| **Linear**（闭源） | 无 npm 包 | 4px 网格、圆角 4/6/12、**1px 边框代替阴影**、单一强调色只用于交互、`scale(0.97)` + 0.1–0.15s 微动效 | **参考（理念）** | 零依赖。风险是"只抄外形不抄纪律"；其 16px/1.5 对中文偏紧，中文正文应 1.75–1.9 |
| **Notion**（闭源） | 无 npm 包 | **暖中性**（`#f6f5f4` / `#31302e`）、正文近黑非纯黑、全站 `1px solid rgba(0,0,0,.1)`、阴影累计不透明度 ≤0.05、单一强调色 | **参考（正面样板）** | 零依赖。是本批里**最接近"书卷气"**的一支（差异只在它用无衬线） |
| **Tailwind UI / Headless UI** | Plus 已改为**一次性买断**（商业授权）；Headless UI 免费 MIT，peer `react ^18||^19` | Headless UI 无视觉；Plus/Catalyst 是通用 SaaS 风 | **不适合（Plus）/ 部分适合（Headless UI）** | Headless UI 1 包 + 4 传递依赖 + 63 KB gzip；**它本身不依赖 Tailwind**，但组件面窄（缺 Table/Tree/DatePicker/Toast） |
| **Ark UI / Park UI** | Ark `@ark-ui/react@5.39.2` 无样式（**69 个依赖**，`exports` 直发 TS 源码）；Park UI 基于 Panda CSS | Ark 无视觉；Park UI 是 Chakra 系柔和现代风 | **部分适合（Ark）/ 不适合（Park UI）** | Ark 整包 290 KB gzip；Park UI 版本停在 2024-11 且绑 Panda CSS，与本项目路线冲突 |
| **Mantine** | **v7 起原生 CSS Modules**（去 Emotion），`className` + `classNames` + `--mantine-*` 变量，可 headless | 默认 `radius sm=4px`、5 档多层阴影（偏重）、`fontFamily` 与 `headings.fontFamily` 可分开设（**标题可上衬线**）；默认 `respectReducedMotion: false` | **部分适合（工程最同构，视觉要重做）** | 【事实】latest 已 **9.6.2，peer 要求 React 19.2+**；React 18 必须锁 [`@mantine/core@8.3.18`](https://unpkg.com/@mantine/core@8.3.18/package.json)（peer `^18.x || ^19.x`，6 个依赖，整包 128 KB gzip） |
| **Ant Design 5** | 运行时 CSS-in-JS（`@ant-design/cssinjs`），`cssVar` 开关默认 **false**；React 18 原生匹配（19 才需补丁包） | 企业后台语言：14px 基准 / 32px 控件 / 圆角 6px / 主色 `#1677ff`。**关键事实：seed `fontFamily` 里没有任何中文字族**，"中文优先"靠的是 locale 与组件行为，不是排版 | **不适合** | 5.x 停在 5.29.3，整包 **452 KB gzip / 49 个直接依赖**；cssinjs 运行时摇不掉。改造成书卷气是持久战 |
| **Base UI** | 官方原话"unstyled, don't bundle CSS, and are compatible with Tailwind, **CSS Modules**, CSS-in-JS"，并给出完整 CSS Modules 示例；单包 tree-shakable，**peer `react ^17‖^18‖^19`**，只 5 个依赖 | 无视觉主张（0 样式/字体/圆角），只提供 `data-*` 状态钩子与组件级 CSS 变量（如 `--available-height`） | **适合（首选）** | 【事实】v1.0.0 于 **2025-12-11 Stable**（35 个无样式组件），2026-09-04 已 **1.8.0**、按月发版（[Releases](https://base-ui.com/react/overview/releases)）。整包 147 KB gzip（全 35 组件上限），按需后数十 KB。旧包名 `@base-ui-components/react` 已废弃 |

**B 段结论**【判断】：

1. **不要引入任何"视觉层"**（shadcn 的类名、Radix Themes 的 CSS、antd 的观感都不行）——它们会把墨蓝/金/米白挤成第二套 token。
2. **唯一建议的依赖是 `@base-ui/react`**（不是 Radix）：它是唯一把 **CSS Modules 写进官方一等示例**、不发一行 CSS、React 18 原生兼容、且 1.0 已 stable 的原语层；用它换掉手写的 Dialog / Dropdown / Tooltip / Tabs 的焦点与键盘逻辑（本项目 `components/` 下的弹窗、`Toast`、`UserMenu` 类组件是主要受益者）。
3. **设计纪律可以直接抄 Linear / Notion 的"零成本"部分**：4px 间距网格、1px 细线代替阴影、单一强调色只用于交互、暖中性 + 近黑文字。本项目 `base.css:88-92` 的 4 档阴影已经比 Notion 重，可考虑把 `--shadow-md/lg` 实际使用面收窄，只留给浮层。
4. 任何组件库都**不替你解决中文排版**：正文 16-17px / 行高 1.75-1.9、标题字距归零（别沿用 Radix/Linear 的负字距）、衬线只给标题与数字。

---

## C. 三张落地表

### C1. 图标方案选型表

数量与体积均为实测：图标数取自 Iconify 集合元数据（`api.iconify.design/collection?prefix=…`），体积取自 bundlephobia 的**整包**体积（未 tree-shake 的最坏情况，实际按需引入后单图标约 0.5-1.5 KB）。

| 候选 | 图标数【事实】 | 线宽可调【事实】 | 树摇友好度 | 整包体积（min / gzip）【事实】 | 与中文衬线搭配观感【判断】 | 结论 |
|---|---|---|---|---|---|---|
| **Lucide** | **1853**（[Iconify lucide](https://api.iconify.design/collection?prefix=lucide)） | ✅ 每图标/全局都能调；官方支持 `stroke-width` CSS + `LucideProvider`，还能 `vector-effect: non-scaling-stroke` 防缩放变粗 | 高（`sideEffects:false`，按名导入） | **576 KB / 143 KB**（[bundlephobia lucide-react](https://bundlephobia.com/package/lucide-react)） | 24px 网格、圆头圆角、**默认 2px 线宽偏"现代 UI"**；调到 **1.5** 后与思源宋体的细横竖笔画气质一致 | **首选** |
| **Phosphor** | **9072**（含 6 字重，[Iconify ph](https://api.iconify.design/collection?prefix=ph)） | ✅ 但靠"6 个字重文件"实现，不是数值线宽 | 中：官方讨论确认**导入任一图标会带上 6 个权重** | **5.05 MB / 1.06 MB**（[bundlephobia @phosphor-icons/react](https://bundlephobia.com/package/@phosphor-icons/react)） | `duotone` 很漂亮，适合做"AI 生成中"这类需要情绪的表达；但 6 字重体系与本项目"低预算装饰"的取向相悖 | 备选（只用 duotone 做点缀不划算） |
| **Tabler** | **6202**（[Iconify tabler](https://api.iconify.design/collection?prefix=tabler)） | ✅ 但设计基准固定在 **24×24 / 2px**，改线宽会破坏光学一致性 | 高 | **3.44 MB / 614 KB**（[bundlephobia @tabler/icons-react](https://bundlephobia.com/package/@tabler/icons-react)） | 2px 直线条更"工程感"，与"墨韵书香"偏冷；数量多但风格不如 Lucide 克制 | 备选（Logseq 用它，见 A4） |
| **Radix Icons** | 15×15 网格的小型集（[官方](https://www.radix-ui.com/icons)、[npm](https://www.npmjs.com/package/@radix-ui/react-icons)） | ❌ 单一线宽、单一大小的"crisp"风格 | 高 | **403 KB / 100 KB** | 15px 网格在中文界面里**偏小**，且集内缺"知识管理"语义图标（图谱、复习、卡片） | 不选 |
| **Iconify / unplugin-icons** | ~150 集合 / **20 万+** 图标（[unplugin-icons](https://github.com/unplugin/unplugin-icons)） | 取决于被引入的集合 | **最高**：构建期按需生成组件，产物只含用到的图标 | 取决于集合；运行时包极小 | 可以"Lucide 为主 + 少量别家补齐"，但**混库会带来风格不统一**的风险 | 作为补图渠道，不作主库（见下） |
| **自绘 SVG** | 想画多少画多少 | 完全自由 | 最高 | ≈ 每个 1-3 KB | 唯一能做出"水墨/毛笔"质感的方案 | **仅用于 5-8 个产品独有语义**（见 C2 的"产品自绘"列） |

**明确推荐**【判断】：

1. **主库选 Lucide，全局线宽锁 1.5px**。理由：①1853 个图标里"知识管理"语义齐全（`notebook-pen`、`layers`、`sparkles`、`calendar-clock`、`waypoints`、`quote`、`circle-check`…）；②线宽是**数值**而非字重文件，`--icon-stroke: 1.5` 一个 CSS 变量就能全站统一（[官方 Global styling](https://lucide.dev/guide/react/advanced/global-styling)），这正是当前 Unicode 字符做不到的；③整包 gzip 143 KB，按需引入后通常 < 15 KB；④**Obsidian 全站用 Lucide 并写进了开发者文档规范**（24×24 画布、≥1px 内边距、round join/cap，[Icons](https://docs.obsidian.md/Reference/CSS+variables/Foundations/Icons)），证明它扛得住"长时间阅读型"界面；⑤同类产品中 **Memos**（[UserMenu.tsx](https://github.com/usememos/memos/blob/30c0611a/web/src/components/UserMenu.tsx)）也在用，说明笔记类产品普遍选它。**注**：Lucide 官方默认线宽是 2px，Obsidian 规范也是 2px；本项目改 1.5px 是【判断】——中文界面文字笔画较细，配 2px 图标会显得图标抢戏。
2. **允许的例外只有一个**：知识图谱里的"节点/关系/年代"等 5-8 个语义，用**自绘 SVG**（水墨笔触），学 AFFiNE 的做法——产品独有语义自绘，通用语义用现成库（[`@blocksuite/icons` 由 Figma 脚本生成](https://cdn.jsdelivr.net/npm/@blocksuite/icons@2.2.17/package.json)）。若坚持完全零图标依赖，则可照 **Anytype** 的路子自建 "SVG registry + TSX 组件"。
3. **落地方式**【判断】：新建 `frontend/src/components/Icon.tsx` 作为**唯一图标出口**（学 AppFlowy 的"图标常量代码生成"与 Logseq 的单一映射表思路，[AppFlowy PR #8731](https://github.com/AppFlowy-IO/AppFlowy/pull/8731)），对外只暴露语义名（`<Icon name="review-due" />`），内部映射到 Lucide 组件或自绘 SVG。这样**将来换库只改一个文件**，且可顺手把 `Sidebar.tsx:25-51` 的 Unicode 全部替换掉。

### C2. "视觉符号"映射建议表

> "形态"= 除图标外的表达手段（色条、圆点、环形、底纹）。"借鉴自"标注具体作品的具体做法。

| # | 语义 | 图标【判断】 | 色彩语义 | 形态 | 借鉴自（含出处） |
|---|---|---|---|---|---|
| 1 | **笔记** | `notebook-pen` / `file-text` | 墨蓝 `--color-primary`（中性态） | 列表项左侧 **3px 类型色条** | Trilium 的 Note Icons & Colors（[文档](https://docs.triliumnotes.org/)）：用颜色承载类型，图标留给动作 |
| 2 | **文件夹 / 项目** | `folder` / `folder-open`（展开态换图标） | 中性 `--color-text-secondary` | 树节点**三态**：默认 / hover 淡墨底 / 选中墨蓝淡底 | **AppFlowy**：侧栏承担"命令中心"，workspace 切换器放侧栏顶部而非设置里（[Workspace Basics](https://appflowy-io-appflowy.mintlify.app/getting-started/workspace-basics)）；**Obsidian** 侧栏是"可停靠标签页容器"（[Workspace](https://obsidianmd-obsidian-help.mintlify.app/ui/workspace.md)） |
| 3 | **知识卡片** | `layers` / `square-stack` | 金色 `--color-accent`（背书面） | **卡片正面**：白底 + 1px 边框 + 仅 1 档阴影 | Heptabase 卡片正面样式（[Getting started](https://wiki.heptabase.com/getting-started-with-heptabase)） |
| 4 | **题目** | `circle-question-mark` / `list-checks` | 墨蓝 + 题型用金色小徽章 | 题号用**等宽数字**（表格右对齐） | Anki 浏览器表格可排序列（[Anki Manual: Browsing](https://docs.ankiweb.net/browsing.html)） |
| 5 | **复习到期** | `calendar-clock` / `bell-ring` | 警告色 `--color-warning`（已压深，白底 5.16:1） | **数字徽章**（不是红点）：直接显示"12 张到期"；进入复习前先给"新增 / 学习中 / 待复习"三个数 | Anki 的 Study Overview 中间页与**三色计数**（蓝=新卡、红=学习中、绿=待复习，[AnkiDroid 文档](https://docs.ankidroid.org/)）；原则同 Anki 把**具体间隔**印在按钮上（[studying.html](https://docs.ankiweb.net/studying.html)）：**数字 > 颜色** |
| 6 | **掌握度** | 无图标（用图形） | 绿→金→灰三档 | **环形进度**或 1-5 段小圆点（`--radius-full`），**不用星星**；详情里可展开 stability / difficulty / retrievability 三个数 | 本项目已有评分圆标（`base.css:82-85` 注释）；数值维度抄 Anki 浏览器把 FSRS 的 `prop:s` / `prop:d` / `prop:r` 做成一等筛选维度（[searching.html](https://docs.ankiweb.net/searching.html)） |
| 7 | **AI 生成** | `sparkles` | 金色 `--color-accent` + `--color-accent-light` 底 | 生成中：`loader-circle` 旋转 + **斜向流光条**（金色渐变） | Reor 的 Chat 与编辑器同屏（[仓库](https://github.com/reorproject/reor)）；Phosphor `duotone` 的情绪化表达思路 |
| 8 | **处理中 / 清洗中** | `loader-circle`（不要 `hourglass`） | 中性灰 + 进度条用墨蓝 | **阶段条**：上传 → 解析 → 清洗 → 生成，四段各自独立状态 | 本项目图谱外的"清洗对比"页天然适配；Anki 的"间隔预览"= 让用户看到**下一步会发生什么** |
| 9 | **图谱关系** | `waypoints` / `git-fork`（列表回退用） | 墨色 `--graph-ink` / 淡墨 `--graph-ink-faint` | 画布用**宣纸底 + 装裱框**；关系类型用**线型**（实线=前提、虚线=相关）；侧栏可放"局部图谱"降级版 | 本项目已定的水墨令牌（`base.css:113-119`）；Obsidian 把图谱做成"全局 + 局部"两级（[Workspace](https://obsidianmd-obsidian-help.mintlify.app/ui/workspace.md)）；Logseq 的右栏是**可编辑的次级工作区**（不是只读检视器，[论坛](https://discuss.logseq.com/t/is-logseq-developing-a-multi-windowed-mode/243)） |
| 10 | **标签 / 徽章** | 无图标 | 中性灰底 + 墨蓝文字；若需区分来源，用**浅底族**而非换色 | **chip**：`--radius-full`、8px 内边距、无边框只有底色；徽章用 `radius 4px / padding 2px 8px / fontSize 12` | **AFFiNE 的 `--affine-tag-*` 10 色浅底族 + `chip/tag`（浅底）/`chip/label`（深底）两类**（[theme](https://cdn.jsdelivr.net/npm/@toeverything/theme@1.1.23/dist/style.css)）；Anytype 的对象类型图标 + 类型色成对出现 |
| 11 | **目标** | `target` / `flag` | 墨蓝主色 + 达成后转金 | **横向进度条 + 右侧百分比**；达成态加金色描边 | 本项目 Dashboard 已有 StatCard 衬线数字（`StatCard.module.css:86`），把"衬线 = 数字/成就"固化为规则 |
| 12 | **引用来源** | `quote` / `link-2` | 淡墨底 `--color-bg` | **常驻右栏的 Reference Panel**（列出来源笔记），而非折叠角标；条目可点击跳原文 | **Khoj**：官方文档明确右栏列出"生成回答用到的笔记"（[docs.khoj.dev](https://docs.khoj.dev/features/chat/)、[referencePanel.tsx](https://github.com/khoj-ai/khoj/blob/9258f57d/src/interface/web/app/components/referencePanel/referencePanel.tsx)） |
| 13 | **上传** | `upload` / `file-up` | 墨蓝描边虚线区 | **虚线拖拽区**（`border-style: dashed`，本项目 `CleaningPanel.module.css:107` 已有虚线先例） | 通用范式；CleaningPanel 的"清洗前后"分栏可作上传后的状态承载 |
| 14 | **危险操作** | `trash` / `triangle-alert` | `--color-error`（已压深到 #c0392b） | 二次确认弹窗 + **危险按钮用描边而非实底**，实底只留给最终确认 | Anki 的"Hard 被误用"教训（[论坛 #40879](https://forums.ankiweb.net/t/add-visual-cues-to-make-it-clear-that-hard-is-not-a-failing-grade/40879)）：**颜色语义一旦含糊，用户一定误用**；本项目 `DeleteNoteDialog` 已有二次确认 |
| 15 | **会话 / 问答** | `message-square-text` / `bot` | 用户侧墨蓝淡底，AI 侧米白 | 会话**可命名可置顶**，不是一次性对话框；输入框支持斜杠命令分模式 | Khoj 的会话列表 + 斜杠命令（`/notes` `/general` `/online`，[docs.khoj.dev](https://docs.khoj.dev/features/chat/)） |

### C3. 展现形式借鉴表（逐页）

| 页面 | 参考谁的做法 | 具体抄什么 | 不要抄什么 |
|---|---|---|---|
| **Dashboard** | 本项目 StatCard + Linear（[redesign 说明](https://linear.app/now/how-we-redesigned-the-linear-ui)） | 衬线大数字 + 灰字标签（`StatCard.module.css:86` 已有）；把"今日到期 / 连续天数 / 掌握度"做成**一横排卡片**，每卡只讲一个数 | Linear 的深色优先；卡片加渐变（会与米白底打架，`base.css:58-62` 已定义 5 条渐变，建议**只在 Auth/Hero 用**） |
| **笔记列表** | Joplin（两级钻取）+ Trilium（类型色条） | 左色条表类型、标题 + 一行元信息（来源类型 · 状态 · 更新时间）、hover 才出操作按钮 | Anki Browser 那样的多列可排序大表——移动端放不下，且本项目列表项信息量不足 |
| **笔记详情** | Obsidian（侧栏标签页）+ **Memos 的 property rail** | 顶部 **视图切换 tab**：Markdown / PDF / 视频 / 清洗对比；元信息收进一条**窄竖轨**（快速动作、可见性、metadata、附件、关联），主内容区保持纯净（Memos v0.30.0 自称 "Linear-style rail: a quiet icon action"，[changelog](https://usememos.com/changelog/0-30-0)）；表格类属性用 Anytype 的"键值行"排版 | ①引入可拖拽 Dock 系统（SiYuan 的能力，成本 ≫ 收益）；②Obsidian 的 Reading / Live Preview 双渲染引擎（官方长期背"两模式不一致"的债） |
| **清洗对比** | Heptabase 高亮批注（[更新日志](https://wiki.heptabase.com/newsletters/2026-03-24)） | 左右分栏 + **改动处以金色淡底高亮**，左侧原文、右侧清洗后；顶部一行"删除 N / 修正 M"统计 chip | 纯红绿 diff（本项目 `DiffView` 已用 error/success 语义，保持不变即可） |
| **知识卡片** | Heptabase（卡片正面） | 正面白底细边框、背面转金色淡底；掌握度用环形/圆点；翻面动效 **≤ 200ms** 且尊重 `prefers-reduced-motion` | 3D 翻转（在中文字体渲染下容易糊） |
| **知识图谱** | 本项目水墨令牌 + Obsidian 的"全局/局部两级"（[Workspace](https://obsidianmd-obsidian-help.mintlify.app/ui/workspace.md)） | 保留宣纸/装裱框；**图例（legend）用线型区分关系类型**；把"当前节点"做成可用键盘上下切换 | Obsidian 全局图谱的"毛球"效果（节点一多就不可读）；不要在画布上叠加过多文字标签 |
| **题库 / 问题集** | Anki Browser（[Browsing](https://docs.ankiweb.net/browsing.html)） | 只抄三件：**可配置列 + 点列头排序**（默认 4–6 列）、**行背景三态高亮**（旗标色 / 黄=暂缓 / 紫=已标记）、**把 FSRS 的 s/d/r 做成筛选维度**；筛选用可点击 chip | ①Anki 的搜索语法（`deck:X (is:due or tag:Y)`）——中文用户学习成本过高；②14 列全量信息密度；③"Cards / Notes 双模式"（本项目卡片天然挂在笔记下） |
| **复习（核心页）** | **Anki 四档自评**（[studying.html](https://docs.ankiweb.net/studying.html)）+ **AnkiDroid 新复习屏**（[公告](https://forums.ankiweb.net/t/new-study-screen-official-thread/67394)） | ①四档按钮**固定底部横排**：档位名 + 由 FSRS 实时算出的**下次间隔**（`<1分钟` / `3天` / `8天` / `21天`）；结构照 Anki 的"名称 + 间隔子元素 + 默认档位标记"（[reviewer.py](https://github.com/ankitects/anki/blob/main/qt/aqt/reviewer.py)），默认档位（对应 Good）可高亮；②**"失败在左、通过靠右"的空间语义**，配按档位的对勾/叉图标反馈，缓解"Hard 到底算不算过"；③**AI 先预选一档、用户确认或改**（RemNote 的 Type-in-Answer 做法，[文档](https://help.remnote.com/en/articles/7752298-typing-in-answers)）——把"4 选 1"降级为"确认"；④顶部显示剩余数；键盘 `1/2/3/4`；答题后自动前进；⑤可选"先试学、再决定是否加入复习队列"两段式闸门 | ①不要出现"Again = 失败"式暗示；②**中文不要把 Hard 直译成"困难"**（Anki 官方文档专门警告这会让间隔失真），写"想了很久才想起来"；③不要把 FSRS 参数放复习页（放卡片详情/设置）；④不要 Qt 原生控件观感；⑤按钮数量别随卡片历史浮动（位置会跳变） |
| **每日材料** | Memos（[changelog v0.30.0](https://usememos.com/changelog/0-30-0)） | 极简单栏时间线；卡片之间**用留白分隔而非边框**；元数据收进 chip；列表**返回时恢复滚动位置**（Memos 为此单开过一个 PR） | ①无层级的纯时间流（本页需要"今日目标 vs 已完成"的结构）；②可变列瀑布流当默认视图，会削弱"一条笔记 = 一个知识单元"的边界 |
| **学习目标** | 本项目 Dashboard 风格 + Anytype 属性行 | 目标卡 = 标题 + 横向进度条 + 右侧百分比；子任务用 **checkbox 列表**（Lucide `square-check`） | 甘特图（超出范围） |
| **项目** | Trilium 类型色条 | 项目卡左侧色条 + 成员/资料计数 chip | 看板拖拽（桌面可用、移动端是负担） |
| **问答（RAG）** | Reor + Khoj（[Reor](https://github.com/reorproject/reor)、[Khoj Reference Panel](https://github.com/khoj-ai/khoj/blob/9258f57d/src/interface/web/app/components/referencePanel/referencePanel.tsx)） | ①输入框带**检索范围**（全部 / 当前笔记 / 当前项目，可做成斜杠命令 `/笔记`）；②**来源常驻右栏或回答下方 chip 行**，点击跳回原文；③会话可命名可删除；④上下文被截断时显示"以下内容未纳入本次回答" | Agent 工具调用面板、多客户端插件架构；不要做两套检索（AI 问答与"相关笔记"必须共用一条管线） |
| **回收站** | 本项目既有 | 表格化、行内"恢复 / 彻底删除"；**彻底删除按钮用描边**、需二次确认 | 无（本页是纯功能页，保持最简） |
| **登录 / 注册** | 本项目 Auth 已有衬线标题（`Auth.module.css:116`） | 把 `--gradient-hero`（`base.css:62`）与衬线标题集中用在这里，作为"书卷气"的第一印象 | 不要把这些渐变扩散到内页（`base.css:58-62` 已定义 5 条渐变，建议只留 Auth/Hero 用途） |
| **全局：响应式降级** | AFFiNE 的容器查询（[use-header-responsive.ts](https://cdn.jsdelivr.net/gh/toeverything/AFFiNE@canary/packages/frontend/core/src/desktop/pages/workspace/detail-page/use-header-responsive.ts)） | 用**原生容器查询**做"组件随容器变窄逐级隐藏"：`container-type: inline-size` + `@container (width <= 500px)` 隐藏次要按钮、`<=400px` 隐藏徽章、`<=300px` 隐藏收藏。零依赖，比堆 `@media` 更准（同一个 header 在窄栏里也能正确降级） | 不要只按视口断点写媒体查询——三栏布局里"主区宽度"与"视口宽度"不是一回事 |
| **全局：中文排版** | SiYuan 的按语言覆盖（[issue #11841](https://github.com/siyuan-note/siyuan/issues/11841)） | 中文字族做**语言维度**的显式覆盖（`:root:lang(zh-CN)`），并注明回退顺序的原因；正文行高 1.75-1.9、标题字距归零（不要沿用 Radix/Linear 的负字距——那是为拉丁字形调的） | 不要继续用"系统字体在前、中文字族在后"的栈（`base.css:73` 现状），中文实际会落到 PingFang/微软雅黑，衬线标题与正文无衬线会各自为政 |

---

## D. 如果只能抄三件事

1. **把侧栏那串 Unicode 字符（`frontend/src/components/Sidebar.tsx:25-51`）换成 Lucide，线宽全站锁 1.5px，并收敛到单一 `Icon.tsx` 出口** —— 出处：Lucide 全局线宽用 CSS 一个变量即可统一（[Global styling](https://lucide.dev/guide/react/advanced/global-styling)）；Obsidian 全站用 Lucide 并把它写进开发者规范（[Icons](https://docs.obsidian.md/Reference/CSS+variables/Foundations/Icons)）；Memos 用 `lucide-react`（[UserMenu.tsx](https://github.com/usememos/memos/blob/30c0611a/web/src/components/UserMenu.tsx)）。
2. **复习页四档自评 = 档位名 + 由 FSRS 实时算出的下次间隔，固定底部一排，失败在左、通过靠右，并让 AI 预选一档待确认** —— 出处：Anki 每个评分按钮自带 `stattxt` 间隔槽位（[reviewer.py](https://github.com/ankitects/anki/blob/main/qt/aqt/reviewer.py)）+ AnkiDroid 为澄清"Hard 也是答对"改用左右空间语义与图标反馈（[新复习屏公告](https://forums.ankiweb.net/t/new-study-screen-official-thread/67394)）+ RemNote 输入判定后自动预选一档（[Typing in Answers](https://help.remnote.com/en/articles/7752298-typing-in-answers)）。
3. **AI 回答的引用来源做成常驻右栏（并可限定检索范围），而不是折叠角标** —— 出处：Khoj 的 Reference Panel 与左栏"会话级文件过滤器"（[docs.khoj.dev](https://docs.khoj.dev/features/chat/)、[referencePanel.tsx](https://github.com/khoj-ai/khoj/blob/9258f57d/src/interface/web/app/components/referencePanel/referencePanel.tsx)）；Reor 让"AI 问答引用了什么"与"我该看哪些相关笔记"共用同一条检索管线（[reorproject/reor](https://github.com/reorproject/reor)）。

---

## 证据缺口（不要据此下结论）

> 环境限制：本机 `github.com` / `raw.githubusercontent.com` / `api.github.com` **DNS 不可达**（解析到非公网 IP），`bing`/`ddg` 搜索后端 302。所有 GitHub 源码级结论均来自搜索后端返回的**页面正文片段**（可点击但本机打不开），建议在有网环境 `git clone` 复核；能直连的官方文档（Anki Manual、Obsidian 镜像、Memos/Khoj/Base UI 官网）为直读，证据等级最高。

| 项 | 状态 |
|---|---|
| SiYuan 图标 sprite 与 Lucide 的授权/衍生关系 | 命名与造型高度吻合 Lucide 系（CirclePlay / TriangleAlert / ListFilterPlus…），但**未取到授权声明文件，不能断言** |
| AppFlowy 的具体色值与语义色 token 名 | `default_colorscheme.dart` 文件存在，但 Dart 源码被 CDN 以 content-type 拒绝，正文未读 |
| Anytype / Reor / Khoj 前端依赖清单 | 未取得完整 `package.json`；Khoj 的图标/字体来自其源码片段（已给链接），Anytype 只有自有 SVG registry 的 commit 证据 |
| Reor 的复习/闪卡界面 | **零界面证据**，只有 issue 在讨论"是否用 FSRS"；A11 因此主要覆盖其检索与问答骨架 |
| Anki 桌面端四档配色的最终方案 | 官方文档**不描述颜色**；已确认桌面端默认不上色、AnkiDroid 用图标 + 左右空间语义、PR #4370「彩色边框」存在，但**是否合并且色值未知** |
| Anki 新版 Svelte 复习屏的 DOM 与配色 | 只知目标是把双 webview 合并为单页、Svelte 5.53.x；具体按钮色值未知 |
| Logseq 的命令面板 / Trilium 与 Joplin 的圆角阴影 token | 未核实到（`.cljs` 被 CDN 拒绝；官方文档未列） |
| 各 npm 包"按需引入后"的真实体积 | bundlephobia 返回的是**整包**体积；本项目接入后需实测 |
| Tabler / Phosphor / Lucide 官方**图标总数**（非 Iconify 聚合值） | 用了 Iconify 集合计数（Tabler 6202 / Phosphor 9072 / Lucide 1853），与官方口径可能略有出入：Lucide 官网/README 只写 "**1600+** vector files"（[lucide.dev/guide](https://lucide.dev/guide/)），差异来自 Iconify 侧包含别名与 hidden 项 |
