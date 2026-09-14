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
   名字改成 `组件前缀+名字`（如 `feedbackScaleIn`）。全局的原版不要删 ——
   它仍可被 tsx 内联 `style={{ animation: 'shake …' }}` 按裸名引用
   （内联样式不过 CSS Modules），也是全局动画库的一部分。
   `scripts/verify-built-css.mjs` 会**逐产物文件**检查"引用的动画在本文件内
   有没有定义"。

   > 第一批之后 `scaleIn` 已经**没有样式表用户**了（最后一个用户 `auth.css`
   > 把它复制成 `authScaleIn` 搬进了模块），`cleaning-pulse` 同样
   > （它的唯一用户 `.cleaning-progress-bar` 是死规则，见 §4 末）。
   > 这两条全局 `@keyframes` 暂时**留着不删**：删它们属于"死代码清理"，
   > 与搬家混在一起会让"丢失 0"这条证据同时包含两种语义。

2. **类名哈希后，`responsive.css` 里的选择器再也选不中它。**
   规则还在、永不生效 —— 与 `mobile-input-font-size.test.ts` 文件头记的
   那个级联 bug 是同一类事故。**响应式规则必须跟着组件一起搬进模块**，
   媒体查询断点值沿用全局的 `768px` / `480px`，不另造断点。

3. **`src/styles/` 下不能放模块文件。**
   `mobile-input-font-size.test.ts` 会读该目录下所有 `.css` 并断言每一个都被
   `main.tsx` 导入；模块文件按需加载、不在那里，测试会直接失败。
   这就是"模块与组件同目录"的硬性理由。

   > 同理，**空掉的全局样式表不能删**（第一批之后 `auth.css` / `cleaning.css` /
   > `diff.css` 只剩注释）：删文件就要连带删 `main.tsx` 里的 import。
   > 留一个只有注释的文件既满足断言，又把"这些类名去哪了"写在原处。

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
     级联得主正确、退休类名已消失；
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
| `scripts/lib/css-parse.mjs` | 三个脚本共用的 CSS 解析器（**只此一份**，见文件头）+ `findRecentRev`（按内容定位"迁移前"） |
| `scripts/css-rule-inventory.mjs` | 按类名/前缀导出规则清单，`--from-git` 读 git 版本（修订**自动定位**，可 `--rev` 覆盖） |
| `scripts/css-migration-diff.mjs` | 迁移前(git) vs 迁移后(dist) 逐条差集 + 动画绑定/动画体校验；批次在文件头的 `BATCHES` 里声明。**四类差异**：逐字保留 / 值有变化 / 丢失 / **已裁决删除**（`resolvedConflicts`，见下） |
| `scripts/verify-built-css.mjs` | 产物校验：**逐文件**悬空动画、冲突归因到源文件、级联次序、切片标记、退休类名、产物新鲜度 |
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

**已知的检查盲区**（第二批发现、第三批又发现一个，都尚未补）：

4. **"窄屏规则被同权重的桌面规则压掉"这类事故，现有工具看不见。**
   实例：`responsive.css` 的 `@media (max-width:768px) { .markdown-body .katex
   { font-size: 1em } }` 与 `markdown-extras.css` 的顶层
   `.markdown-body .katex { font-size: 1.1em }` 权重相同（都是 0,2,0），
   媒体查询**不增加权重**，而后者在产物里更靠后（实测字节 36081 vs 37916）
   —— 于是那条窄屏字号**从未生效过**（迁移前就如此，与 5.6 无关）。
   差集脚本按 `(上下文 + 选择器 + 属性)` 建键，`@media` 里的规则与顶层规则
   落在不同键上，所以这种"跨上下文覆盖战"不会被报出来。
   要补的话应当是在产物上做一次**权重+顺序的真实求解**（像 `CASCADE_PAIRS`
   那样，但自动枚举同选择器对），代价是要引入 DOM 知识以判断元素是否真同时命中。
5. **跨属性竞争（简写 vs 长写）也看不见 —— 第三批发现。**
   实例：`` className={`card ${styles.qaAiCard}`} ``，全局 `.card` 写
   `border: 1px solid …`（简写会展开出 `border-left-*`），模块 `.qaAiCard` 写
   `border-left: 3px solid …`（长写）。两者权重同为 (0,1,0)，
   **谁赢只看产物里的先后**，而：
   - 差集按 (上下文 + 选择器 + 属性) 建键 —— 选择器不同，不配对；
   - 产物校验的冲突统计与 `CASCADE_PAIRS` 按**属性名**配对 —— `border` 与
     `border-left` 是两个名字，配不上。

   第三批用一次性探针实测过（配方见 `css-migration-plan.md` §5 雷区 12）：
   jsdom 对**字面值**的 `border` 简写会正确展开（对照实验能随顺序翻转胜负），
   但不解析 `var()`，所以要先把 `var(…)` 换成字面色、再读**真实产物**拼顺序。
   结论：模块类与全局类并列写在同一个元素上时，除了看"有没有同名属性竞争"，
   还要看"简写会不会展开出对方的长写" —— 这一条目前只能靠文档 + 探针守住。

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
