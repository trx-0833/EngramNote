# CSS 约定（overhaul-plan 5.6）

> 本文件是 CSS Modules 迁移的**规范**，不是进度报告。
> 迁移进度与逐文件风险见 `docs/migration-evidence/`，本轮（试点）的实测证据见该目录。

## 1. 三层结构

```
① 令牌层   src/styles/base.css        :root { --color-* / --space-* / --radius-* … }
② 全局层   src/styles/*.css           重置、跨功能共用件（.btn / .card / .container …）
③ 模块层   src/**/X.module.css        单个组件/页面自己的样式（默认去处）
```

**新增样式默认进第 ③ 层。** 只有满足第 4 节"必须留全局"的判据才进第 ② 层。

## 2. 令牌层

令牌**只住 `base.css` 的 `:root`**，不新建 `tokens.css`、不在组件里定义全局令牌。
理由：`mobile-input-font-size.test.ts` 断言 `src/styles` 下每个 `.css` 都被
`main.tsx` 引入，且把导入顺序当作级联顺序的真实来源；新增一个样式表就要同时
维护"导入位置"这个隐含契约，而令牌层的价值恰恰在于**没有顺序语义**。

现有命名族（沿用，不要另造同义词）：

| 族 | 例 | 说明 |
|---|---|---|
| `--color-*` | `--color-primary` `--color-border-light` `--color-success-light` | 语义色；`-light` 是低透明度底 |
| `--color-*-rgb` | `--color-primary-rgb: 15, 52, 96` | **专供拼 alpha**：`rgba(var(--color-primary-rgb), 0.16)` |
| `--gradient-*` | `--gradient-primary` | 渐变 |
| `--space-*` | `--space-xs`…`--space-2xl` | 间距 |
| `--radius-*` | `--radius-sm`…`--radius-xl` `--radius-full` | 圆角 |
| `--shadow-*` | `--shadow-sm`…`--shadow-xl` `--shadow-glow` | 阴影 |
| `--font-*` | `--font-sans` `--font-serif` `--font-mono` | 字族 |
| `--ease-*` | `--ease-out-expo` `--ease-in-out` | 缓动曲线 |

**加新令牌的判据**：同一个**语义**的值在项目里手写了 **≥3 处**。
只出现一两次的魔法数字不要进令牌层 —— 那只是把写死换个地方。

**已知缺口（下一轮补）**：字号与 z-index 目前仍是硬编码，且重复很多
（`0.8rem` 13 处、`1rem` 11 处、`0.875rem` 9 处；`z-index` 有 100/99/90/5/1
混用）。补的时候按上面的判据一次补齐、**单独一轮做**，不要和组件迁移混在一起
（那会同时改动上百条声明，diff 无法审阅）。

## 3. 模块层

### 命名与位置

| 事项 | 规则 |
|---|---|
| 文件名 | `组件名.module.css`，**与组件同目录** |
| 位置 | **绝不放 `src/styles/`** —— 见下方"雷区" |
| 导入 | `import styles from './X.module.css'`（放在其他 import 之后，附一行中文说明为什么从这里取类名） |
| 类名 | CSS 里 `camelCase`（`.quizOption`），TSX 里 `styles.quizOption` |
| 组合 | 模板字符串：`` className={`${styles.a}${on ? ` ${styles.b}` : ''}`} `` |
| 保留全局类 | 直接并列：`` className={`btn ${styles.selfRatingBtn}`} `` |

### 雷区 ⚠️ 这些是实测踩过的，不是理论风险

1. **`@keyframes` 动画名会被一起哈希。**
   模块里写 `animation: scaleIn 0.3s`（`scaleIn` 定义在 `base.css`）会被改写成
   `animation: _scaleIn_<hash> 0.3s`，而产物里**没有**这个 `@keyframes` ——
   动画静默消失，源码和规则清单都看不出来。
   实测三种写法：`animation: scaleIn`、`animation-name: scaleIn` 都会被哈希；
   `animation: :global(scaleIn)` **构建立刻失败**（PostCSS `Double colon`，
   声明值里不认 `:global()`）。
   **正确做法：把用到的 `@keyframes` 定义搬进模块自己**（动画体逐字复制），
   名字改成 `组件前缀+名字`（如 `feedbackScaleIn`）。**迁移那一轮**全局的原版不要删 ——
   它可能仍被 tsx 内联 `style={{ animation: 'shake …' }}` 按裸名引用
   （内联样式不过 CSS Modules），也是全局动画库的一部分。
   `scripts/verify-built-css.mjs` 会**逐产物文件**检查"引用的动画在本文件内
   有没有定义"。

   > 第一批之后 `scaleIn` 已经**没有样式表用户**了（最后一个用户 `auth.css`
   > 把它复制成 `authScaleIn` 搬进了模块），`cleaning-pulse` / `glowPulse` /
   > `graph-spin` 同样（它们的用户都被复制 + 改名搬进了模块）。
   > 迁移期间这 4 条**留着不删**：删它们属于"死代码清理"，与搬家混在一起
   > 会让"丢失 0"这条证据同时包含两种语义。
   >
   > **收尾轮已经删掉了它们**（连同另外 4 个从来没有过用户的
   > `slideDown` / `pulse` / `float` / `gradientShift`，以及模块内失去用户的
   > `cleaningPulse`）：证据 `docs/migration-evidence/5.6-13-dead-css-cleanup.md`。
   > 删除后多了一道**常设检查**：`verify-built-css.mjs` 要求产物里每个
   > `@keyframes` 都**有引用者**，唯一的合法例外是"只被行内样式引用"的
   > `shake`（点名登记在 `INLINE_ONLY_KEYFRAMES`）—— 死动画不可能再悄悄回来。

2. **类名哈希后，`responsive.css` 里的选择器再也选不中它。**
   规则还在、永不生效 —— 与 `mobile-input-font-size.test.ts` 文件头记的
   那个级联 bug 是同一类事故。**响应式规则必须跟着组件一起搬进模块**，
   媒体查询断点值沿用全局的 `768px` / `480px`，不另造断点。

   > 序 13 之后这条有了收尾版：**`responsive.css` 已经空了**（只剩注释、文件没删）。
   > 补丁层里"类名留全局"的那些规则（`.btn` / `.card` / `.page-header-row` /
   > `.markdown-body …` / `.filter-pill`）也各自**回到了拥有该类名的样式表**
   > （`components.css` / `markdown.css` / `learning.css`）。
   > 现在窄屏规则与它覆盖的基础规则**住在同一个文件里**，顺序即行为。

3. **`src/styles/` 下不能放模块文件。**
   `mobile-input-font-size.test.ts` 会读该目录下所有 `.css` 并断言每一个都被
   `main.tsx` 导入；模块文件按需加载、不在那里，测试会直接失败。
   这就是"模块与组件同目录"的硬性理由。

   > 同理，**空掉的全局样式表不能删**（第一批之后 `auth.css` / `cleaning.css` /
   > `diff.css` 只剩注释；序 12/13 与收尾轮之后 `refinements.css` /
   > `responsive.css` / `layout.css` / `graph.css` 也是）：删文件就要连带删
   > `main.tsx` 里的 import。
   > 留一个只有注释的文件既满足断言，又把"这些类名去哪了"写在原处 ——
   > 收尾轮删掉的东西也在原处留了墓碑注释（写明依据与证据文件）。

4. **不要为了"顺手统一"改外观。**
   比如给 `.quiz-option` 加 `composes: btn from global` 很"合理"，但它的 DOM 上
   从来没有 `btn`，compose 会注入 `padding / border-radius / font-weight /
   transition / position / overflow` —— 一次纯搬家就变成了视觉改动，
   而本轮要证明的恰恰是"没变"。要统一按钮基线请单开一轮，带截图对比。

5. **⚠️ 模块层与全局层的先后（第一批实测 → 第二批已根治）。**
   Vite 按**模块图顺序**产出 CSS。第一批时 `main.tsx` 第 4 行就 `import App`、
   样式表第 7 行之后才引入，于是**静态引入的组件，其模块 CSS 排在全部全局
   样式表之前**。实测（重排前）产物 `index.css` 的字节位置：
   `Auth.module.css` = 1、`base.css` 的 `:root` = 2551、`.btn` = 6555。

   后果：`.auth-submit` 这种"在全局 `.btn` 之上覆盖几个属性"的规则，
   两者权重相同（都是单类），搬进模块后**先后关系反转**，`.btn` 反而盖住了
   它的 `padding / font-size / font-weight / transition`（按钮肉眼可见地
   变小变细）。文本差集看不出这种损失 —— 两条规则都还在、值也没改。

   **根治（已落地）**：`main.tsx` 里把全部 `./styles/*.css` 提到应用组件之前，
   产物顺序于是变成 **①令牌 → ②全局 → ③模块**，与 §1 的分层图一致。
   第一轮曾用 `:global(.btn).authSubmit` 提权顶住，重排后**特意还原成单类**：
   留着提权会把"模块排在全局之前"这个事实继续藏在代码里。
   （重排后实测：`:root` @0、`.btn` @4004、`_authBg` @48306 —— 模块层确实最后。）

   **两道护栏**（都不靠人记）：
   - `scripts/verify-built-css.mjs` 的 `CASCADE_PAIRS`：逐属性算"谁最终生效"，
     得主必须是模块。**新增一条与全局类打架的模块规则时必须往这里登记。**
     它同时支持两种取胜方式（权重更高 / 权重相同靠源序），同权重又跨文件时
     直接报"判不了"（那种情况必须提权消除歧义）。反向验证过：把 `main.tsx`
     的顺序改回去，这项立刻报 4 个属性得主是 global，并指出先查 `main.tsx`。
   - `src/styles/mobile-input-font-size.test.ts` 新增"模块层必须排在全局层之后"
     用例：单测层快速反馈，不需要构建。

   > 反面提醒：**懒加载**的页面/组件，其模块 CSS 会打成独立 chunk，
   > 本来就在 `index.css` 之后注入（`Dashboard.module.css`、
   > `NoteDetail-*.css` 就是这种）。所以不要无脑给模块规则加 `:global()`：
   > 只有当同一个元素上真有同权重竞争时才需要考虑。

## 4. 什么时候类名**必须**留在全局

按顺序判断，命中任一条就留全局：

1. **重置 / 基础元素选择器** —— `*, *::before`、`html`、`body`、`a`、`input[type=checkbox]`。
   这类样式没有"拥有者组件"，且必须早于一切规则生效。
2. **跨功能共用件** —— 被**两个以上不相邻功能**使用的类。
   判据是 grep 出来的**文件数**，不是"感觉像公共组件"：
   `.btn`（45 处引用）、`.card`（35 处）、`.container`、`.progress-bar`
   （Dashboard / LearningGoals / TodayLearn / TaskProgress / ReviewProgress 五处在用）
   → 留全局。
3. **第三方 DOM 的类** —— KaTeX（`.katex` / `.katex-block` / `.katex-display`）、
   highlight.js（`.hljs*`）、`react-force-graph` 生成的 canvas。
   我们控制不了它们的类名，只能全局命中。
4. **`src/styles/markdown.css` 的 `.markdown-body` 后代选择器** ——
   内容来自 `marked` 渲染的 HTML 字符串，**没有组件可以挂类名**，
   属于事实上的第三方 DOM。
5. **补丁层 `responsive.css` / `refinements.css` 目前命中的类** ——
   在对应组件迁移之前必须保持全局（迁移时把这些规则一起搬走）。

**第一批的两个实例，可以当判据的样板：**

| 类名 | 判定 | 依据 |
|---|---|---|
| `.dashboard-two-col` `.dashboard-review-card` `.trend-bar*` | 进模块 | grep 只命中 `pages/Dashboard.tsx` |
| `.stat-card*` `.stat-number` `.stat-label` | **留全局** | `Dashboard.tsx` **与** `TodayLearn.tsx` 都在用；搬进 `Dashboard.module.css` 会逼 `TodayLearn` 跨页 import 一个页面模块 |
| `.progress-bar` `.progress-bar-fill` | **留全局** | 5 处在用（第 2 条），另有 2 个测试按类名查询 |

**留全局的代价要写在文件头**：`learning.css`、`dashboard.css` 在迁移后都加了
一段注释，点名列出"哪些类名仍被切片外页面使用、因此不能删"，
以及"它们要等到什么条件才可能搬走"。
没有这段注释，下一个读代码的人会以为整个文件都死了。

**边界不清时宁可停在边界上并写明**：把 `.stat-card` 留在全局是"部分迁移"，
但它是**按归属画的边界**（B 组 = 跨页共用），不是"搬了一半"。
半搬的定义是"同一个 SASS/样式表里一组互相依赖的规则只搬走一部分"。

## 5. 迁移一个文件的标准流程

0. **先确认这个样式表有没有"在打架"**：`node scripts/verify-built-css.mjs` 的
   "迁移前既有的冲突"一节会按**源文件**归因列出（第四批开始时是
   `assessment.css + refinements.css` 14 条 + `components.css + refinements.css` 2 条）。
   命中了就**先裁决再搬**：裁决必须有实测证据（真实 Chromium 读 computed style，
   配方见 `css-migration-plan.md` §5 雷区 3），照抄一套源码里的值不算裁决。
   裁决结果按 `BATCHES` 的 `resolvedConflicts` 登记（见 §7）。
1. `node scripts/css-rule-inventory.mjs <文件> --class <切片类名> --from-git [--rev HEAD~1]`
   先拿到**迁移前清单**（`--from-git` 是因为工作区里很快就没旧版本了；
   已经提交过的批次要 `--rev HEAD~1`，否则读到的是"规则已经搬走"的版本，
   清单为空而脚本会报错退出）。
2. grep 确认这些类名**只被切片内的文件使用**。命中切片外 → **停**，
   要么把那个文件也纳入本轮，要么把这些类名留在全局（见第 4 节第 2 条）。
3. 把规则搬进 `X.module.css`；`@keyframes` 按第 3 节雷区 1 处理；
   响应式规则一起搬。
4. 改 TSX：`className="a b"` → `` className={`${styles.a} ${styles.b}`} ``。
   **拼出来的类名**（`` `diff-line-${type}` ``）要改成显式查表 ——
   哈希后拼字符串必然失效，而查表还能让 tsc 帮你守住完整性。
5. 从全局样式表删掉原规则，**在原地留一段注释**说明搬去哪了、
   以及本文件还有哪些类名不能删。
6. grep 全项目确认没有残留的字面类名（含测试里的 `querySelector('.old-name')`）。
7. `npm run build`，然后：
   - `node scripts/css-migration-diff.mjs` —— 差集必须 **丢失 0**；
   - `node scripts/verify-built-css.mjs` —— 无悬空动画、改动范围内无冲突、
     级联得主正确、退休类名已消失、**跨媒体查询覆盖战 0 条未登记**、
     **简写 vs 长写 0 条未登记**、**产物里没有死动画**（收尾轮新增的三项；
     任何一项的"候选对为 0"也是失败 —— 那是检查失明，不是没问题）；
   - `node scripts/gen-migration-evidence.mjs` —— 落证据文件。
8. `npm test` / `npx tsc --noEmit` / `npm run lint` / `npm run build` 四道全绿。

**往 `BATCHES` / `CASCADE_PAIRS` / `SLICE_MARKERS` / `RETIRED` 里登记本批**
（`css-migration-diff.mjs` 与 `verify-built-css.mjs` 各有一处）。
新类名不用手写：约定是 kebab → camelCase，脚本自己推导并回模块文件核对。

## 6. 测试里的类名查询

**优先改用语义查询**（`getByRole` / `getByText` / `getByLabelText`）。
类名是哈希的，写 `.quiz-option` 的测试在迁移后必然失效；
而"按角色+文本找按钮"既不受改名影响，也更接近用户看到的东西。

确实需要按类名查时（例如只关心某个纯展示元素），两种做法：

- **留在全局**：该类名本来就在全局层（如 `.progress-bar-fill` 被 5 处共用）。
- **用 `styles` 导出**：测试 `import styles from './X.module.css'` 后
  `container.querySelector('.' + styles.foo)`。Vite 在测试环境下对 CSS Modules
  返回真实的类名映射，这条路可行，但把测试和实现细节绑得更紧，非必要不用。

**实测定论**（第一批用一个临时探针跑了一遍，用完已删）：
Vitest 在 `test.css` 未开（本项目默认）时，`.module.css` 的默认导出是一个
**Proxy**，任意属性访问返回 `_<属性名>_<6位哈希>`（如 `styles.authBg`
→ `"_authBg_cce552"`），**且 `Object.keys(styles)` 是空数组**。
所以：

- `styles.foo` 用起来没问题（是字符串，不是 `undefined`）；
- `' .' + styles.foo` 能查得到元素；
- 但**别对 `styles` 做枚举**（`Object.entries(styles)` / `Object.keys(styles)`
  拿到的是空），那会静默得到"什么都没有"。需要遍历时只能显式列出键
  （`DiffView.module.css` 的 `LINE_TYPE_CLASS` 就是这么写的）。

## 7. 证据工具

| 脚本 | 作用 |
|---|---|
| `scripts/lib/css-parse.mjs` | 四个脚本共用的 CSS 解析器（**只此一份**，见文件头）+ `findRecentRev`（按内容定位"迁移前"） |
| `scripts/lib/css-cascade.mjs` | 收尾轮新增：级联求解（权重 / 源序 / 简写展开表 / 从 TSX 抽"并列类名"），**只服务 `verify-built-css.mjs` 的三项新检查**，不含第二个解析器 |
| `scripts/css-rule-inventory.mjs` | 按类名/前缀导出规则清单，`--from-git` 读 git 版本（修订**自动定位**，可 `--rev` 覆盖） |
| `scripts/css-migration-diff.mjs` | 迁移前(git) vs 迁移后(dist) 逐条差集 + 动画绑定/动画体校验；批次在文件头的 `BATCHES` 里声明。**四类差异**：逐字保留 / 值有变化 / 丢失 / **已声明删除**（`resolvedConflicts`，见下） |
| `scripts/verify-built-css.mjs` | 产物校验（**收尾轮之后这里就是那个"一条命令"**）：逐文件悬空动画、冲突归因到源文件、级联次序、切片标记、退休类名、产物新鲜度，加上**跨媒体查询覆盖战**、**简写 vs 长写**（同选择器 + 跨选择器）、**死代码清理的三向自检**、**产物里没有死动画** |
| `scripts/gen-migration-evidence.mjs` | 把上面三者的输出写成 `docs/migration-evidence/*.md` |

**第四批新增的第四类差异：`resolvedConflicts`（"已裁决删除"）。**
`assessment.css` × `refinements.css` 那 16 条冲突裁决之后，迁移时删掉的是
"永远不生效的死声明"——它既不是"丢失"（不是事故）也不是"值有变化"
（产物里根本没这条声明了）。不显式声明的话，差集会把它们报成 16 条丢失，
把真信号淹掉。所以 `BATCHES` 里逐条登记
`{ sheet, selector, prop, value, winner, evidence }`：脚本从"迁移前"一侧摘掉它们、
单独成节打印（谁赢、凭什么），并**自检每条都真的命中过** ——
写错类名或值会报错退出，不会变成一条永远绿灯的空声明。
登记的是**实测**结论（`evidence` 字段写 computed 值），不是源码里的先后。

**两个方向都反向验证过**（第四批实测）：把某条的 `value` 改成一个不存在的值 →
脚本报"这条声明一条都没命中"并 `exit 3`；把整条删掉（= 不登记这次删除）→
该属性被报成"丢失"并 `exit 1`。也就是说**漏登记不可能静默通过**。

这些脚本的判断都带**自检**：任何一侧解析出 0 条规则就报错退出，
而不是当成"没有差异"。解析器前后错过三次（伪类冒号被改写成 `: hover`、
`@media` 里的规则被静默丢弃、产物里 `._className` 多一个下划线导致匹配不到），
每次都险些得出"规则全丢了 / 全在"的相反结论。

**第一批又踩到两次，都记在这里：**

1. **类名后面紧跟哈希分隔符 `_`，属于 `\w`** ——
   `\._?authSubmit(?![\w-])` 对产物里的 `._authSubmit_1hcab_41` **一条都匹配不上**，
   于是"级联次序"这一项报的是"两侧没有同属性竞争"（**假通过**）。
   凡是要在产物里按类名匹配，都要**先剥哈希再判边界**。
2. **压缩器的等价改写会伪装成"值有变化"** ——
   `rgba(255,255,255,.92)` → `#ffffffeb`、`#fffc` ↔ `#ffffffcc`、
   `color: white` → `#fff`、`::after` → `:after`、`content: ""` → `content: ''`。
   差集脚本里每一条都归一化了，并且**逐条写明出处**：不归一化就会有 6 条假警报
   把真变化淹掉，归一化时若不写明依据，又会变成"把差异抹平"。
   另有 `-webkit-user-select` 这类**构建器注入**的声明：它是加法不是丢失，
   单独一节列出、不当作失败。

**第二批踩到一次"环境"问题，改变了工具设计：**

3. **不要用 `HEAD~N` 记"迁移前"。** 本项目是多个 agent 并行改同一个仓库：
   第二批期间另一个 agent 提交了一个**后端**改动，HEAD 往前挪一位，
   于是写死的 `HEAD~1` / `HEAD~2` 集体指错 —— 三个批次的"迁移前"全指向
   迁移**之后**的版本。自检把这件事报成了"解析出 0 条规则"（响亮地失败，
   没有静默给出错结论），但每来一个无关提交就要人工重算一遍，不可持续。
   现在两个脚本都从 HEAD 往回**按内容**找"第一个还含有这批老类名的提交"
   （`lib/css-parse.mjs` 的 `findRecentRev`），与提交顺序完全解耦。

**第三批：归一化清单又加两条，而且"假差异"与"真错误"是同一轮里一起出现的：**

4. **`border-radius` 四值 → 三值**：`border-radius: 16px 16px 4px 16px` 被压成
   `border-radius:16px 16px 4px`（第 4 个值省略时取第 2 个值，渲染完全相同）。
   出处：`.qaUserBubble`（产物 `QA-*.css`）。见差集脚本的 `canonRadius`。
5. **`inset` 简写被降级成长写**：源码 `inset: 0`，产物里是
   `top:0;right:0;bottom:0;left:0`（构建器按目标浏览器把简写降级）。
   出处：`.uploadZoneActive::after`（产物 `Upload-*.css`）。这是**声明级**改写，
   所以在 `canonDecls`（不是 `canonValue`）里把两侧都展开成四条长写。
6. **同一轮里差集还抓到一条真错**：把 `.list-toolbar .search-input-wrapper`
   在模块里写成单类 `.searchInputWrapper` —— 报"1 条丢失 + 1 条新增"
   （权重 (0,2,0) → (0,1,0)）。**selector 是行为的一部分，不是排版。**
   这条正好说明"归一化"与"抓真错"不冲突：把等价改写抹平之后，剩下的差异就是真的。

**第五~九批（序 8/9/10/12/13）又补了两条归一化与一条新机制：**

7. **选择器组里逗号后的空格被压掉**：源码 `.sidebar,\n  .sidebar-collapsed`
   解析成 `.sidebar, .sidebar-collapsed`，产物里是
   `._sidebar_h,._sidebarCollapsed_h`（逗号后无空格）。出处：`Sidebar.module.css`
   的 768px/480px 两条 + `App.module.css` 的 `.<appLayout>`。
   不归一化会把三条"选择器组"规则全报成丢失 —— 而它们逐字都在产物里。
   逗号两侧的空格在 CSS 里没有语义，归一化不抹平任何真实差异。
8. **压缩器会合并"相邻且声明逐字相同"的规则**（esbuild 实测）：
   ```
   .graphToolbar { flex-wrap: wrap; row-gap: var(--space-xs) }
   .graphToolbarLeft, .graphToolbarRight { flex-wrap: wrap; row-gap: var(--space-xs) }
   ```
   在产物里合成 `._graphToolbar_h,._graphToolbarLeft_h,._graphToolbarRight_h{…}` ⇒
   差集按"同 (上下文 + 选择器)"建的两个 key **同时落空**，报成 2 条丢失。
   修法是落空时找**超集规则**：同上下文 + 成员集合 ⊇ 本条 + 声明逐字相同
   （三条缺一不可，任何真丢声明仍会走到"丢失"分支），命中时打印
   "压缩器合并了选择器组"。出处：`Graph.module.css` 的 768px 档。
9. **第五类差异：`relocations`（已登记的搬家）。** 序 9/12/13 要做一件工具原本看不见的事：
   **把声明从一个全局样式表挪到另一个全局样式表**，或**把一条多选择器组拆开**
   （重的部分随组件进模块、留全局的那部分进它自己的样式表）。
   两种情况下"迁移前有、迁移后那一处没有"都会长得像"丢失"。所以 `BATCHES` 里新增
   `relocations: [{ from: {sheet, context, selector, prop, value}, to: {file, context, selector}, why }]`：
   脚本把这条声明从"迁移前"一侧摘掉、单独成节列出，并**双向自检** ——
   ① `from` 在迁移前的源文本里必须找得到（写错类名会报"一条都没命中"）；
   ② `to` 在目标文件里必须落得下（搬丢了会报"目标里没有这条"）。
   `from.prop: '*'` 表示"整条规则一起搬"（零引用的预留类用它）。
10. **第九批的"空批次"通道**：序 13 没有任何类名进模块（规则只是换了归属），
   于是 `groups: []`，修订改为**按声明**定位（从 HEAD 往回找第一个"登记的每条
   `from` 声明都还在"的修订）。这一批的证据不是"逐条保留"，而是
   "工作区里那张样式表**真的只剩注释**（解析出 0 条规则）"，
   再加一份**逐条去向审计**：迁移前那张表的每一条规则，都能在工作区的某个 CSS 里
   找到"上下文 + 选择器（kebab→camel 归一）+ 声明逐字相同"的那一条，
   否则必须落在显式例外表里（`gen-migration-evidence.mjs` 里的
   `EMPTY_SHEET_EXCEPTIONS`）。实测：`refinements.css` 38 条 = 25 + 13 例外 + **0 丢失**；
   `responsive.css` 59 条 = 56 + 3 例外 + **0 丢失**（证据 `5.6-11`）。

**第五~九批（序 8~13）补在雷区表里的五条实测（详见计划 §5 雷区 14~18）：**

6. **压缩器会合并相邻的同声明规则**、**会压掉选择器组里逗号后的空格** ——
   两者都会伪装成"丢失"（已在差集里归一化，见 §7 第 7、8 条）。
7. **属性选择器匹配的是"序列化后的内联样式"**：`[style*="rgba(0,0,0,0.5)"]`
   命中 **0** 个元素（浏览器把它序列化成 `rgba(0, 0, 0, 0.5)`，逗号后带空格）。
   任何靠 `style` 属性做匹配的选择器都要**先在真浏览器里量"命中了几个元素"**，
   而不是读源码推断。实测输出见证据 `5.6-12`。
8. **没有任何 tsx import 的模块文件不会进产物** —— 零引用的类"按归属搬进模块"
   的结果是规则从产物里消失（差集当场报 6 条丢失）。这类类**只能留全局**。
9. **模块之间"同权重、同文件、靠先后"的竞争也是行为**（例：抽屉的
   `transform: translateX(0)` 必须排在 `translateX(-100%)` 之后）——
   规则清单、冲突统计、动画检查**全都不响**。`verify-built-css.mjs` 因此新增
   **`ORDER_PAIRS`**（`CASCADE_PAIRS` 只覆盖"全局类 × 模块类"）。
10. **探针（真浏览器对账）自身的三个坑**：拼"迁移前/迁移后"两种写法的后代选择器
   必须用 `:is()` 包住整个备选列表（否则容器自己会被匹配走，差异全是假的）；
   `page.waitForEvent('download')` 必须在触发之前注册；
   给 worktree 起 dev server 要给**独立的 vite `cacheDir`**（共用
   `node_modules/.vite` 会让图谱页崩到错误边界）。

**已知的检查盲区**（第二批发现、第三批又发现一个；**收尾轮把第 4、5 条做成了
常设检查**，剩下的写在第 6 条里）：

4. ~~**"窄屏规则被同权重的桌面规则压掉"这类事故，现有工具看不见。**~~
   **→ 收尾轮已补上常设检查**：`scripts/verify-built-css.mjs` 的
   "跨媒体查询覆盖战"一节（求解器在 `scripts/lib/css-cascade.mjs`）。
   实例仍在案：`markdown.css` 的 `@media (max-width:768px) { .markdown-body .katex
   { font-size: 1em } }` 与 `markdown-extras.css` 的顶层
   `.markdown-body .katex { font-size: 1.1em }` 权重同为 (0,2,0)，
   媒体查询**不增加权重**，而后者在产物里更靠后（实测字节 36081 vs 37916）
   ⇒ 那条窄屏字号**从未生效**（迁移前就如此）。现在它被登记成
   `MEDIA_WAR_RULES` 的第一条"已知、故意留着"：让它生效是**改外观**，
   得单开一轮带截图（计划 §4.7）。细节与它自己的盲区见下面那一节。
5. ~~**跨属性竞争（简写 vs 长写）也看不见 —— 第三批发现。**~~
   **→ 收尾轮已补上常设检查**（同一份 `lib/css-cascade.mjs`，
   `verify-built-css.mjs` 的"简写 vs 长写"两节）。实例：`` className={`card
   ${styles.qaAiCard}`} ``，全局 `.card` 写 `border: 1px solid …`（简写会展开出
   `border-left-*`），模块 `.qaAiCard` 写 `border-left: 3px solid …`（长写）。
   两条权重同为 (0,1,0)，**谁赢只看产物里的先后**，而差集按
   (上下文 + 选择器 + 属性) 建键（选择器不同 ⇒ 不配对）、冲突统计与
   `CASCADE_PAIRS` 按属性名配对（`border` 与 `border-left` 是两个名字 ⇒ 配不上）。
   现在这条**有名字、有得主、有人盯着**（`CROSS_CLASS_SHORTHAND_RULES`）。
6. **仍然看不见的**（写在这里，免得下一个人以为已经全覆盖）：
   - **"两个类会不会命中同一个元素"只有 TSX 字面量级的证据**：跨选择器那一半靠
     `className` 里并列的类名（含模板串与三元分支里的字面量）。**拼不出来的写法**：
     `classList.add(…)` 运行时加的类（`useAdhdReader.ts` 的 6 处）、`composes`、
     第三方 DOM（KaTeX / highlight.js），以及"两个类分别挂在父子元素上、
     但简写与长写作用在同一个盒子上"的情形。
   - **值级求解**：只判"两条声明争同一批长写属性"，**不展开简写的具体值**
     （`border: 1px solid var(--x)` 不会被拆成三条长写的值）。所以"两边其实写了
     同一个值"这种无害情况也会被列出来（注册表按模式登记即可）。
   - **简写表的覆盖面**是 border / padding / margin / background / font / inset
     （任务点名的六族）+ border-radius / gap / flex / overflow / transition /
     text-decoration / list-style / outline。**没有**做 CSS 全量简写表 ——
     一份又长又没人维护的表只会变成噪音源；`grid-*` / `place-*` / `columns`
     之类目前**不在**扫描范围内。
   - **`:hover` / `:focus` 等状态不参与跨选择器配对**（只比基态单类规则）：
     `.card:hover` 上的简写与另一个类的长写之间的竞争不会被报出来。
   - **两个懒加载 chunk 之间的先后静态判不了**：那种组合报 `unknown`
     （得主必须靠人看，不能猜）。`index.css` ↔ chunk 这一种是确定的
     （chunk 由 `__vitePreload` 在 `index.css` 之后注入 —— 计划 §5 雷区 8 的补充）。

### 收尾轮（死代码清理 + 三项新检查）新增的登记点

全部走**同一个入口**：`node scripts/verify-built-css.mjs`（退出码非 0 = 有问题）。
求解器只有一份，在 `scripts/lib/css-cascade.mjs`（与解析器 `lib/css-parse.mjs` 一样
"只此一份"）—— **没有**第二个 CSS 解析器。

| 检查 | 登记表 | 报什么 | 响亮失败（防空检查） |
|---|---|---|---|
| 跨媒体查询覆盖战 | `MEDIA_WAR_RULES` | 同选择器 + 同属性，`@media` 里那条**在源序上被顶层压掉**的每一对 | ① 候选对为 0 ⇒ 报错（今天实测 68 对候选）；② 注册项一条都没命中 ⇒ 报错；③ 有发现没登记 ⇒ 报错 |
| 简写 vs 长写（同选择器） | `SHORTHAND_CLASH_RULES`（按**属性模式**登记） | 同一（选择器 + 上下文）下简写与长写落在同一批长写属性上的每一对 + 得主 | 候选对为 0 ⇒ 报错（今天 24 对）；注册项空转 ⇒ 报错 |
| 简写 vs 长写（跨选择器） | `CROSS_CLASS_SHORTHAND_RULES`（按**类名对**登记，比较时忽略顺序） | TSX 里并列在同一个 `className` 上的两个**单类**规则，展开后落在同一批长写属性上 + 得主 | ① TSX 里抽不到任何"并列 ≥2 个类名"的 `className` ⇒ 报错（抽取器失明）；② 产物里没有单类规则 ⇒ 报错；③ 候选对为 0 ⇒ 报错；④ 注册项空转 / 有未登记的类名对 ⇒ 报错 |
| 死代码清理不许回来 | `CLEANUP_RETIREMENTS` | 收尾轮删掉的类名 / `@keyframes` / 令牌**三个方向**自检：迁移前那个文件里确实有它（从 HEAD 往回**按内容**定位修订，写死 `HEAD` 会在删除提交之后失效）→ 源码里没了（**剥注释后**比较，否则墓碑注释会把自检弄红）→ 产物里没了 | 表为空 ⇒ 报错；任一条对不上 ⇒ 报错 |
| 产物里没有死动画 | `INLINE_ONLY_KEYFRAMES` | 每个 `@keyframes` 定义都要**有引用者**；唯一合法例外是"只被 tsx 行内样式引用"（`shake`，点名登记） | 产物里 0 个定义 ⇒ 报错；登记的"仅行内"动画其实有样式表引用 ⇒ 报错（登记过期）；有定义没人引用 ⇒ 报错 |

**反向验证过（收尾轮实测，改完即还原）**：把 `cascadeDecls` 换空 ⇒ 两项检查都报
"候选对 0 条"并退出 1；把 TSX 抽取结果换空 ⇒ 报"抽取器失明"；把一条注册项的判据
改错 ⇒ 同时报"空注册"与"未登记 1 条"；把 `INLINE_ONLY_KEYFRAMES` 换成 `fadeIn`
⇒ 报"它现在有样式表引用"并把 `shake` 报成死动画；把 `CLEANUP_RETIREMENTS` 里的名字
改一个字母 ⇒ 报"从 HEAD 往回 40 个提交里找不到还含有它的修订"。
也就是说这几项**不可能退化成永远绿灯**。

**同选择器的注册表按"属性模式"登记、跨选择器的按"类名对"登记** —— 这个区别是有意的：
前者表达"这种写法（`background` + `background-clip`）是已知且正确的"，
所以**新的同模式声明不会报红**；后者表达"这两个类名并列在同一个元素上"，
所以**新的类名对一定会报红**。想收紧前者，就把 `SHORTHAND_CLASH_RULES` 的 `match`
写成对具体选择器的断言。


**第四批（序 5）的补充：裁决一场"谁也没决定过"的冲突，要用真浏览器量。**

`assessment.css` × `refinements.css` 的 16 条冲突是**迁移前既有**的：
两个文件权重相同、都在 `main.tsx` 里静态引入，"哪套生效"只取决于导入顺序。
这类债不能靠读源码"选一套自己喜欢的"，也不能靠人肉比产物的字节位置。
可复用的做法（完整配方在计划 §5 雷区 3，结论在证据 `5.6-09`）：

1. 用仓库里现成的 Playwright 写一个**一次性探针 spec**（用完删；别改 `e2e/**`
   里别人的文件），走真实渲染路径（登录 → 目标页 → 触发目标状态），
   `getComputedStyle` 导出**全部**属性 —— computed 是级联求解后的结果，
   简写 vs 长写（上面第 5 条那个盲区）会自动体现在解析出来的长写上；
2. **等过渡结束**再读（每个状态变化后 ≥600ms），否则读到的是过渡中间值：
   第四批实测非 hover 态的 `box-shadow` 读成 `0 2.7px 9.4px rgba(…,.067)`
   （回落到静止值的 64% 处）、`.card-hover:hover` 的 `transform` 读成
   `matrix(1,0,0,1,0,0)`（过渡起点），两条都会让人得出"两边都没生效"的错误结论；
3. "渲染结果没变"要用**同一把尺子**量两次：迁移前用
   `git worktree add --detach <临时目录> HEAD`（只读 HEAD，不碰工作区 ——
   仓库里常有并行 agent，`git stash` 会把别人的改动一起搅进来）+
   `node_modules` junction 起独立 dev server；迁移后把前一次的 JSON 用
   `route.fulfill({ path })` 喂回页面逐属性比对。第四批：**16 802 条 computed
   属性差异 0**（class 属性按预期 18 个变哈希 / 13 个一字不变）；
4. 探针要落盘 JSON 时**别用 `node:fs`**：本项目没有 `@types/node`，而 `e2e/`
   在 `tsconfig.json` 的 `include` 里 ⇒ `tsc`（= `npm run build` 的第一步）直接红；
   `download.saveAs(path)` 由 Playwright 落盘，绕开这个坑。
