/**
 * E2E 可访问性审计（overhaul-plan **5.9**，axe-core + 真 Chromium）
 *
 * ────────────────────────────────────────────────────────────────────────
 * ## 为什么必须跑在真浏览器里
 *
 * `npm test`（Vitest + jsdom）那一层**做不了**这件事，不是"还没做"：
 *
 *   - jsdom **没有布局引擎** —— `getComputedStyle` 只回字符串，
 *     元素的真实尺寸、位置、是否被遮挡全部无从得知；
 *   - 因此**颜色对比度算不出来**：axe 的 `color-contrast` 需要元素的实际
 *     前景/背景色、字号与是否粗体（"大文本"豁免判据）；
 *   - 原生 HTML 约束校验、`aria-*` 与可见性的联动、tabindex 顺序，
 *     在 jsdom 里都是空壳。
 *
 * 所以这一层扫的是**真实渲染出来的应用**：真 CSS 级联、真盒模型、真对比度。
 *
 * ## 这一层能证明什么、不能证明什么
 *
 * 能：给定页面上，axe-core 的规则集（WCAG 2.0/2.1 A + AA + best-practice）
 * 没有报出**未登记**的违规；并且每个页面确实渲染出了足够多的 DOM
 * （见下面"不许空过"）。
 *
 * 不能：`docs/a11y-audit.md` §4 列了一整份"工具看不见"的清单
 * （焦点顺序、键盘陷阱、屏幕阅读器语义、动态状态对比度、触控目标尺寸……）。
 * **"axe 没报"不等于"无障碍没问题"** —— 本层不产生那种结论。
 *
 * ────────────────────────────────────────────────────────────────────────
 * ## 不许空过（vacuous pass 的防线）
 *
 * 一个坏掉的选择器、一次失败的懒加载、一个被桩吃掉的接口，都会让页面渲染成
 * 空白或骨架屏 —— 而**空白页面的 axe 结果恒为"0 违规"**。
 * 那样的绿灯比红灯更糟：它把"没扫到东西"说成了"没问题"。
 *
 * 因此每次扫描都强制：
 *   ① 先等该页**独有**的渲染标记（`ready`）出现；
 *   ② 断言 DOM 元素数 ≥ `minNodes`；
 *   ③ 断言 axe 真的执行了（`passes` 非空 —— 有内容的页面不可能一条都不过）；
 *   ④ 断言桩没有出现"未定义响应"的接口（那些接口的报错在页面里普遍被 `catch` 吞掉）。
 *
 * 另有一条**自检用例**（文件末尾）：注入一个必然违规的元素，断言 axe 抓得到。
 * 它守的是最坏的一种失败 —— 桩、选择器或配置把页面搞空了，于是所有断言
 * 都因"没有违规"而通过。
 *
 * ────────────────────────────────────────────────────────────────────────
 * ## 判定尺度：登记表（不是"全绿"也不是"全放行"）
 *
 * 本轮审计的结论是**页面上确实存在真实违规**（见 `docs/a11y-audit.md`）。
 * 于是有三种做法，只有一种同时保住"能跑"和"有意义"：
 *
 *   - 把规则关掉（`disableRules`）：该规则在**所有**页面上的未来回归一起消失
 *     —— 用一行配置换掉一整类信号，最差；
 *   - 钉住违规总数：修好一条测试反而变红（数量变了），维护者就会去改数字，
 *     数字一改就再也不是门禁了；
 *   - **逐条登记 + 上限（本文件采用）**：`REGISTRY` 把当前已知的每条违规
 *     连同"影响等级 + 节点数 + 归属"写下来，实测值**超出**登记值就红。
 *     新问题会立刻失败，"已知问题被修好"则不会失败（见下"为什么只卡上限"）。
 *
 * ## 为什么只卡上限、不要求精确相等
 *
 * 本文件是给**修 bug 的人**看的：他每修掉一条，节点数就往下掉。
 * 若要求相等，他必须同时改测试数字，于是很快就会出现"顺手把数字改大一点"，
 * 门禁随即失效。只卡上限意味着：**修好立刻生效，不需要改这个文件**；
 * 而问题变多一定失败。代价是它不会告诉你"某条已经被修好了" ——
 * 那件事由 `docs/a11y-audit.md` 的表格（人工过一遍）负责。
 */
import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Locator, type Page } from '@playwright/test'

import { installA11yStubs, loginAs, notesList, DAILY_PLAN, PROCESSING_NOTES, WEAK_POINTS, type A11yStubLog } from './a11y-fixtures'
import { isApiUrl } from './support'

const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']

/**
 * 并发模式与超时。
 *
 * ## 为什么并发要压到 4（`npm run a11y` 里带 `--workers=4`）
 *
 * 实测：8 个 worker 同时打 Vite dev server 时，**按需转换**（页面都是
 * `React.lazy` 懒加载的，每个路由第一次访问才现场编译）会把首屏拖到 10 秒以上，
 * 于是"等欢迎标题出现"超时 —— 失败信息指向"页面没渲染"，
 * 真实原因是"开发服务器在被 8 个 axe 注入 + 懒加载请求同时敲"。
 * 审计要的是**页面内容**，不是吞吐；把并发压下来比放宽超时更接近问题本身。
 * （并发数写在 `package.json` 的 `a11y` 脚本里，保持一处可见。）
 *
 * ## 为什么不用 `test.setTimeout` 逐个用例去调
 *
 * 那是散落的魔法数字。这里统一给整个文件：每个场景 = 真实等待 + axe 注入 +
 * 分析 + 证据收集，90 秒是"明显异常"与"确实需要这么久"之间的分界
 * （实测单场景 3~6 秒）。
 */
test.describe.configure({ mode: 'parallel', timeout: 90_000 })

/** axe 的影响等级，从轻到重（用于"不得比登记时更严重"的判定） */
const IMPACT_ORDER = ['minor', 'moderate', 'serious', 'critical'] as const
type Impact = (typeof IMPACT_ORDER)[number]

function impactRank(impact: string): number {
  const index = IMPACT_ORDER.indexOf(impact as Impact)
  return index === -1 ? 0 : index
}

/**
 * 已知违规登记表：**一个（场景，规则）组合一条**。
 *
 * 每条都要与 `docs/a11y-audit.md` §3 的发现表对得上：`id` 是那里的编号，
 * `nodes` 是登记时该规则命中的元素数，`reason` 写清"是什么问题 + 谁去修"。
 *
 * ## 为什么粒度是"（场景，规则）"而不是"每个元素一条"
 *
 * axe 是**按规则聚合**返回的：一条 `color-contrast` 会把该页所有对比度不足的
 * 元素一起报出来（侧边栏标题 3 个 + 状态徽章 2 个 = 5 个节点）。
 * 想按元素逐条登记，就得在同一个规则下写多条 —— 而匹配只能命中第一条，
 * 后面的**永远匹配不到**，变成"看起来登记了、实际在放行"。
 * 与其做一个会悄悄失效的匹配，不如把粒度定在实测的粒度上：
 * 一条规则、一个页面、一个上限，具体命中哪些元素写在 `reason` 里
 * （每次运行的完整选择器清单会打进测试输出并随 JSON 附件归档）。
 *
 * ## 为什么每条都写 `reason`
 *
 * 一张没有理由的豁免表就是一张"什么都放行"的白名单。
 * 理由必须包含**归属**：谁在哪个后续轮次修它。
 */
interface RegisteredRule {
  /** 与 docs/a11y-audit.md §3 的编号一致 */
  id: string
  rule: string
  /** 出现在哪个扫描场景（与用例里的 `scene` 一致） */
  scene: string
  /** 登记时的影响等级：实测等级**高于**它 → 失败 */
  impact: Impact
  /** 登记时该规则命中的节点数：实测**多于**它 → 失败 */
  nodes: number
  /** 是什么问题、命中哪些元素、归属哪个工作流 */
  reason: string
}

/**
 * ## 5.9 修复轮之后：登记表从 30 条（68 个节点）降到 4 条（4 个节点）
 *
 * 修掉的每一类都对应文档 §3 的一条发现，逐条证据见该文件；
 * 这里只记"还剩什么、为什么还留着"：
 *
 * | 剩余 id | 规则 | 场景 | 为什么还在 |
 * |---|---|---|---|
 * | F-07 / F-22 | `heading-order` | dashboard（桌面 + 移动） | 两栏区块没有区块级标题，**需要人决定它们叫什么** |
 * | F-08 | `heading-order` | note-detail | 标题层级来自**用户自己的 Markdown 内容** |
 * | F-20 | `heading-order` | projects | 同 F-07 的一类 |
 *
 * 也就是说：**剩下 4 个节点全是同一个待人工决定的问题**（P3 的 (b) 类），
 * 不是"还没修"。它们全部维持 1 个节点的上限 —— 那是最紧的写法：
 * 任何一个节点数增加都会当场失败。
 *
 * 曾经用来说明"侧边栏与状态徽章各有多少节点"的 `SHELL_SIDEBAR_NODES` /
 * `SHELL_STATUS_NODES` 两个常量随本轮一起删除：那一整类 `color-contrast`
 * （侧边栏分组标题 / 状态徽章 / 图谱面板标题 / 自评四档 / 笔记项目标签 /
 * 笔记角色下拉框）在 9 个场景里**一个节点都不剩**。历史计数见文档 §2。
 *
 * ────────────────────────────────────────────────────────────────────────
 * ## 覆盖轮（本轮）之后：4 条（4 个节点）→ **12 条（15 个节点）**
 *
 * ⚠️ **这个数字变大不是回归，是覆盖面变大**：新增的 8 条全部挂在**新场景**上
 * （`notes-status-processing` / `daily-materials` / `today-learn` / `review`），
 * 扫描场景从 10 个变成 15 个。旧场景的违规数**一个都没变**：
 * `login` 0、`login-error` 0、`notes-list` 0、`card-review-front/back` 0、
 * `knowledge-graph` 0、`dashboard` 1（F-07）、`dashboard-mobile` 1（F-22）、
 * `note-detail` 1（F-08）、`projects` 1（F-20）—— 也就是"旧场景仍是零容忍，
 * 新场景先把信号登记下来"。
 *
 * | 新增 id | 规则 | 场景 | 节点 | 一句话 |
 * |---|---|---|---|---|
 * | F-28 | `color-contrast` | notes-status-processing | 2 | `--color-warning` 白底 3.1:1（与已修的 F-06 同类） |
 * | F-29 | `color-contrast` | daily-materials | 2 | 同一个缺陷的第二条渲染路径 |
 * | F-30 | `nested-interactive` | daily-materials | 1 | 文件夹头 `role="button"` 里嵌真按钮（与已修的 F-09/F-17 同形） |
 * | F-31 | `heading-order` | daily-materials | 1 | h1 → h3（与 F-07/F-20 同类的 (b)） |
 * | F-32 | `page-has-heading-one` | review | 1 | 整页没有 h1（与已修的 F-14/F-15/F-18 同类） |
 * | F-33 | `color-contrast` | review | 1 | 白字压 `#c9a959` 2.25:1（F-19 只修了同一张表里的一处） |
 * | F-34 | `nested-interactive` | today-learn | 1 | 「待复习」卡片与 F-09 逐字相同 |
 * | F-35 | `color-contrast` | today-learn | 2 | `#f44336` 压 12.5% 同色底 3.13:1 |
 *
 * **每一条都是"下一轮要清掉的债"**（登记是例外，删条目、回到零容忍才是目标）：
 * 本轮不改应用代码的理由是并行的 CSS 迁移（见 docs/a11y-audit.md §9 开头）。
 * 归属与修法逐条写在下面每条的 `reason` 里。
 */

const REGISTRY: RegisteredRule[] = [
  // ── 仪表盘 ──────────────────────────────────────────────────────────
  {
    id: 'F-07',
    rule: 'heading-order',
    scene: 'dashboard',
    impact: 'moderate',
    nodes: 1,
    reason:
      'h1「欢迎使用 EngramNote」之后直接出现卡片里的 h3，axe 报**第一个**跳级节点（「今日学习目标」）。' +
      '同页的卡片标题还有「每日推荐任务」「今日待复习」「薄弱点」。' +
      '判定为 **(b) 需要人工决定**（docs/a11y-audit.md §3 的 P3）。' +
      '为什么本修轮没有顺手改成 h2：这一页的卡片是**两栏区块**（`Dashboard.module.css` 的 `.dashboardTwoCol`：' +
      '今日学习目标 + 每日推荐任务 / 今日待复习 + 薄弱点），而这两组区块**没有区块级标题**，' +
      '所以 h1 → 卡片标题之间本来就缺一级；把卡片标题降级成 h2 只能消掉报警，' +
      '真正的结构问题（四个卡片在这一页上没有归属哪个区块）原样留着。' +
      '要么给两组各加一个区块标题（"今日进度""需要关注"之类 —— 起名是产品/设计决定，' +
      '而且会让仪表盘多出两行标题），要么接受当前层级。' +
      '上限维持 1：多一个节点就失败。归属：需要设计决定（5.6/产品），不是 5.9 修复轮。',
  },

  // ── 移动端视口下的仪表盘（同一份 DOM，与 F-07 同源）───────────────
  {
    id: 'F-22',
    rule: 'heading-order',
    scene: 'dashboard-mobile',
    impact: 'moderate',
    nodes: 1,
    reason:
      '同 F-07 在窄屏（375×667）上的复现，同样是 1 个节点 —— 窄屏把两栏收成一列，' +
      '标题层级与桌面完全一致，所以修法也必须是同一个决定。' +
      '归属：需要设计决定（5.6/产品），不是 5.9 修复轮。',
  },

  // ── 笔记详情 ────────────────────────────────────────────────────────
  {
    id: 'F-08',
    rule: 'heading-order',
    scene: 'note-detail',
    impact: 'moderate',
    nodes: 1,
    reason:
      'Markdown 正文里的 h4（清洗统计区）出现在页面 h1/h2 之后但没有 h3 —— 层级来自**用户内容本身**，属于"内容决定的结构"。' +
      '判定为 **(b) 需要人工决定**（docs/a11y-audit.md §3 的 P3）：可选做法是渲染时归一化标题级别（h1→h2 整体下沉）或接受它；' +
      '**不要**为了消警告去改用户内容。归属：需要设计决定，不是 5.9 修复轮。',
  },

  // ── 项目页 ──────────────────────────────────────────────────────────
  {
    id: 'F-20',
    rule: 'heading-order',
    scene: 'projects',
    impact: 'moderate',
    nodes: 1,
    reason:
      '项目页 h1「项目」之后，卡片区里的 h3 之前没有 h2（与仪表盘的 F-07 同一类、同一个待决定的问题：' +
      '卡片标题的级别是视觉层次的一部分，而卡片区没有区块标题）。' +
      '归属：需要设计决定（5.6/产品），不是 5.9 修复轮。',
  },

  // ══════════════════════════════════════════════════════════════════════
  // 覆盖轮新增的 8 条登记项（场景见 docs/a11y-audit.md §9）
  //
  // ## 这些是**债**，不是"可以接受的现状"
  //
  // 项目口径（文件头 + docs §9）：**登记是例外，删掉登记项、回到零容忍才是目标**。
  // 本轮之所以只登记不修：`frontend/src/**` 正在被并行的 CSS 迁移改动，
  // 这一轮改应用代码**无法被验证**（改了可能修好也可能改坏，且与迁移冲突）。
  // 所以本轮的任务是**先把信号拿到**：让这些页面/状态第一次真的被渲染出来、
  // 被 axe 判过、被登记下来 —— 下一轮照 §9 的清单逐条修掉并**删除这些条目**
  // （删掉即回到零容忍：该规则只要出现 1 个节点就红）。
  //
  // ## 上限一律 = 实测值（最紧写法）
  //
  // 每条的 `nodes` 都是本轮实测的节点数，没有留余量：多一个节点就失败。
  // ## 没有放宽任何一条既有登记项
  //
  // F-07 / F-22 / F-08 / F-20 的上限仍然全是 1，一个字都没动；
  // 已归零的规则（`color-contrast` 在 9 个旧场景里）**没有**因为本轮新增
  // 场景而被重新登记 —— 新条目只挂在新场景上，旧场景仍是零容忍。
  // ══════════════════════════════════════════════════════════════════════

  // ── F-28 / F-29：`--color-warning` 的两个状态徽章（新场景）──────────
  {
    id: 'F-28',
    rule: 'color-contrast',
    scene: 'notes-status-processing',
    impact: 'serious',
    nodes: 2,
    reason:
      '笔记列表上的 `.status-converting` / `.status-cleaning` 两个徽章：' +
      '实测 foreground #c4860a（= `--color-warning`，base.css:40）压在 #ffffff 上 = **3.1:1**，' +
      '12.8px 常规字重要求 4.5:1。axe 原文即 "insufficient color contrast of 3.1"。' +
      '与已修的 F-06（`--color-success`）**同一类、同一个修法**：压深令牌本身。' +
      '本场景是这两个状态第一次被渲染出来（类名由 utils/labels.ts 的 statusClass() 产出，' +
      '而默认桩里只有 cleaned/converted）。实测色值与比值见 a11y-notes-status-contrast.json 附件。' +
      '**这是一笔债：下一轮把 `--color-warning` 压深一档（连同 daily-materials 的 F-29 一起消掉），' +
      '然后删掉本条目。**归属：5.9 下一轮（设计令牌取值属 5.6，但可见后果落在 5.9）。',
  },
  {
    id: 'F-29',
    rule: 'color-contrast',
    scene: 'daily-materials',
    impact: 'serious',
    nodes: 2,
    reason:
      '**同一个缺陷的第二条渲染路径**：今日资料页展开文件夹后，文件夹内的笔记行也用 ' +
      '`statusClass()` 渲染状态徽章，于是同样两个类名（`.status-converting` / `.status-cleaning`）' +
      '在这里再报 2 个节点，色值/比值与 F-28 逐字相同（#c4860a on #ffffff，3.1:1）。' +
      '两条分开登记而不是合并：登记表的粒度是（场景，规则），合并会让某个场景的上限失去约束。' +
      '修法是一处（令牌），消掉的是两条登记项。' +
      '**这是一笔债：随 F-28 一起修，然后删掉本条目。**归属：5.9 下一轮。',
  },

  // ── F-30 / F-31：今日资料页的结构问题 ────────────────────────────────
  {
    id: 'F-30',
    rule: 'nested-interactive',
    scene: 'daily-materials',
    impact: 'serious',
    nodes: 1,
    reason:
      '文件夹头是 `div[role="button"][tabindex="0"]`（DailyMaterials.tsx:467-480，`aria-expanded`），' +
      '里面**嵌了真按钮**「重命名文件夹」/「删除文件夹」→ axe: Element has focusable descendants。' +
      '**与已修的 F-09（仪表盘「今日待复习」卡片）/ F-17（笔记列表卡片）是同一个洞的不同入口**，' +
      '连写法都逐字相同（外层 `role="button"` + 手写 Enter 处理，内层是真 `<button>`）。' +
      'F-09/F-17 的修法已经跑通：**控件之间是兄弟** —— 外层去掉 role/tabIndex/onClick，' +
      '折叠/展开交给一个真有名字的按钮（或把 `aria-expanded` 挪到那个按钮上）。' +
      '**这是一笔债：照 F-09/F-17 的形状改，然后删掉本条目。**归属：5.9 下一轮（与 F-34 同一处修法）。',
  },
  {
    id: 'F-31',
    rule: 'heading-order',
    scene: 'daily-materials',
    impact: 'moderate',
    nodes: 1,
    reason:
      'h1「今日资料」之后，文件夹卡片里的文件夹名是 h3（DailyMaterials.tsx:517），中间没有 h2 —— ' +
      '与 F-07 / F-20 **同一类待人工决定的问题**（卡片标题的级别是视觉层次的一部分，' +
      '而这一页的区块没有区块级标题）。' +
      '不修的理由与 F-07 完全相同：**降级/升级只能消掉报警，真正的结构问题原样留着**，' +
      '要么给列表区加一个区块标题（起名是产品/设计决定），要么接受当前层级。' +
      '上限维持 1：多一个节点就失败。归属：需要设计决定（5.6/产品），不是 5.9 修复轮 —— ' +
      '**但仍登记在案，属于待人工决定那笔债，不由 5.9 单方面清掉。**',
  },

  // ── F-32 / F-33：答题复习页（新场景）────────────────────────────────
  {
    id: 'F-32',
    rule: 'page-has-heading-one',
    scene: 'review',
    impact: 'moderate',
    nodes: 1,
    reason:
      '答题复习页（`Review`）**整页没有任何 h1**（`<h1>`~`<h6>` 数量为 0）：' +
      '`ReviewProgress` 在它这里走"一行式"排版（不传 `title`），所以连那个 h1 都没有。' +
      '这与已修的 F-14/F-15（卡片复习缺 h1）、F-18（笔记列表缺 h1）**是同一类、同一个判据**，' +
      '而且上一轮已经知道这件事（§8.3 第 1 条是**人工探针**得出的结论）—— ' +
      '本轮把它变成场景之后，它才第一次是一条**门禁**。' +
      '修法明确（给这一页一个 h1；`ReviewProgress` 的 `title` 就是为此存在的），' +
      '但要先决定这一页的标题叫什么。' +
      '**这是一笔债：补 h1（同时注意 h1→h3 跳级，见 §8.4 的坑），然后删掉本条目。**归属：5.9 下一轮 + 文案确认。',
  },
  {
    id: 'F-33',
    rule: 'color-contrast',
    scene: 'review',
    impact: 'serious',
    nodes: 1,
    reason:
      '题目头部右侧的**难度徽章**：白字压在 `difficultyColors.medium` = `#c9a959` 上，' +
      '实测 axe "insufficient color contrast of 2.25 (foreground #ffffff, background #c9a959, ' +
      '12.8px normal)"。**与已修的 F-19 是同一类、甚至同一个色值**：' +
      '上一轮只把 `selfRatingOptions` 里的金 `#c9a959` 压深成 `#8f7020`（4.66:1），' +
      '而 `utils/labels.ts` 里**其它几张颜色表没动**（`difficultyColors` / `cardTypeColors` / ' +
      '`questionTypeColors` / `categoryColors` / `verdictLabels`）—— 本条目就是其中一张表的可见后果。' +
      '**这是一笔债：把 labels.ts 里所有"白字压色块"的取值一次过一遍（见 §9.3 的清单），' +
      '然后删掉本条目。**归属：5.9 下一轮（色值属 5.6 令牌，与 F-06/F-19 同例）。',
  },

  // ── F-34 / F-35：今日学习页（新场景）────────────────────────────────
  {
    id: 'F-34',
    rule: 'nested-interactive',
    scene: 'today-learn',
    impact: 'serious',
    nodes: 1,
    reason:
      '「待复习任务」卡片是 `div[role="button"][tabIndex=0]`（TodayLearn.tsx:329-335）' +
      '里面嵌了一个真 `<button>开始复习</button>`（第 344 行）→ Element has focusable descendants。' +
      '**与已修的 F-09 逐字相同**（仪表盘那张卡片），连"外层只监听 Enter、没监听 Space"的毛病都一样 —— ' +
      'F-09 的修法可以直接照搬：外层回到"盒子"，行为落在真按钮上。' +
      '**这是一笔债：照 F-09 的形状改，然后删掉本条目。**归属：5.9 下一轮（与 F-30 同一处修法）。',
  },
  {
    id: 'F-35',
    rule: 'color-contrast',
    scene: 'today-learn',
    impact: 'serious',
    nodes: 2,
    reason:
      '薄弱点列表里的**卡片类型徽章**：`#f44336` 压在 `#f4433620`（12.5% 透明度叠白底 = #fee8e6）上，' +
      '实测 axe "insufficient color contrast of 3.13"，12px 常规字重要求 4.5:1。' +
      '⚠️ **同一处代码在仪表盘上也有**（Dashboard.tsx:311-318，字面相同），' +
      '但那里**扫不出来**：默认桩的 `/api/report/weak-points` 字段名与 `WeakPoint` 契约不一致，' +
      '渲染出来是 `undefined` 文本、徽章是空的 —— 空文本没有对比度可判。' +
      '也就是说本条目背后是"两页同一个缺陷，其中一页被桩的形状藏住了"（见 §9.2）。' +
      '**这是一笔债：修那个字面量并顺手把默认桩的字段名改对，然后删掉本条目。**归属：5.9 下一轮。',
  },
]

const ACTIVE_REGISTRY = REGISTRY

/**
 * 该登记项是否覆盖这个场景。
 *
 * 就是相等判断 —— 刻意**不**做任何"别名/继承"。原因见 `SHELL_SCENES` 的说明：
 * 别名会让一条规则在一个场景里命中两条登记项，匹配随即失效（静默放行）。
 */
function coversScene(entry: RegisteredRule, scene: string): boolean {
  return entry.scene === scene
}

/**
 * 检查一个场景的扫描结果与登记表是否一致。
 *
 * 返回四类问题（调用方断言全为空）：
 *   - `unregistered`：完全没登记过的规则（新回归），或登记表自身写得不清楚；
 *   - `grew`：已登记规则的节点数超出登记上限（影响面扩大）；
 *   - `upgraded`：已登记规则的影响等级变高（变严重）；
 *   - `flaky`：登记表里有**同一场景 + 同一规则**的多条登记项（匹配会失效）。
 */
function reconcile(
  result: AuditResult,
  scene: string,
): { unregistered: string[]; grew: string[]; upgraded: string[]; flaky: string[]; seen: string[] } {
  const unregistered: string[] = []
  const grew: string[] = []
  const upgraded: string[] = []
  const seen: string[] = []

  // ── 登记表自身的一致性：同一（场景，规则）只能有一条 ──
  // 两条以上时，`find` 永远只命中第一条，其余形同虚设 ——
  // 那是"看起来登记了、实际在放行"，必须当场报出来（而不是等它某天漏掉一个回归）。
  const flaky: string[] = []
  const byRule = new Map<string, RegisteredRule[]>()
  for (const entry of ACTIVE_REGISTRY.filter((r) => coversScene(r, scene))) {
    byRule.set(entry.rule, [...(byRule.get(entry.rule) ?? []), entry])
  }
  for (const [rule, entries] of byRule) {
    if (entries.length > 1 && result.violations.some((v) => v.rule === rule)) {
      flaky.push(`${scene} / ${rule}：有 ${entries.length} 条登记项（${entries.map((e) => e.id).join('、')}），匹配会有歧义 —— 合并成一条`)
    }
  }

  // ── 逐条对账 ──
  for (const violation of result.violations) {
    const entry = ACTIVE_REGISTRY.find((r) => r.rule === violation.rule && coversScene(r, scene))

    if (!entry) {
      const targets = violation.targets.map((t) => `${t.selector} :: ${t.summary}`).join('\n        ')
      unregistered.push(`${violation.rule} [${violation.impact}] ×${violation.nodes}\n        ${targets}`)
      continue
    }

    seen.push(entry.id)
    if (violation.nodes > entry.nodes) {
      grew.push(
        `${entry.id} ${violation.rule}：登记上限 ${entry.nodes} 个节点，实测 ${violation.nodes} 个（${violation.targets.map((t) => t.selector).join('、')}）`,
      )
    }
    if (impactRank(violation.impact) > impactRank(entry.impact)) {
      upgraded.push(`${entry.id} ${violation.rule}：等级由 ${entry.impact} 升为 ${violation.impact}`)
    }
  }

  return { unregistered, grew, upgraded, flaky, seen }
}

/** 一条违规的**证据**（进文档表格、进终端输出、进 JSON 附件） */
interface ViolationRecord {
  rule: string
  impact: string
  /** axe 的 helpUrl，便于复核规则定义 */
  helpUrl: string
  /** 受影响节点数 */
  nodes: number
  /** 每个节点的目标选择器 + 失败摘要 + HTML 片段 */
  targets: { selector: string; summary: string; snippet: string }[]
}

interface AuditResult {
  label: string
  url: string
  /** DOM 元素总数（防空过的证据之一） */
  nodeCount: number
  engine: string
  passes: number
  inapplicable: number
  violations: ViolationRecord[]
  /** axe 评估过的规则总数（pass + incomplete + inapplicable + violations） */
  rulesEvaluated: number
  /**
   * axe **不敢下结论**的节点（`incomplete`）：绝大多数是"背景是渐变/图片，
   * 对比度算不出来"。它们不是违规，但也**不是通过** —— 是人工复核清单。
   */
  manualChecks: { rule: string; selector: string; summary: string }[]
}

type AxeResults = Awaited<ReturnType<AxeBuilder['analyze']>>

/** axe 结果 → 精简记录（只留复核需要的字段） */
function summarize(label: string, url: string, nodeCount: number, results: AxeResults): AuditResult {
  const manualChecks = (results.incomplete ?? []).flatMap((rule) =>
    (rule.nodes ?? []).map((n) => ({
      rule: rule.id,
      selector: String(n.target[0]),
      summary: String(n.failureSummary ?? '').split('\n').filter(Boolean)[0] ?? '',
    })),
  )

  return {
    label,
    url,
    nodeCount,
    engine: `axe-core ${results.testEngine.version}`,
    passes: results.passes.length,
    inapplicable: results.inapplicable.length,
    rulesEvaluated:
      results.passes.length + results.incomplete.length + results.inapplicable.length + results.violations.length,
    violations: results.violations.map((v) => ({
      rule: v.id,
      impact: v.impact ?? 'unknown',
      helpUrl: v.helpUrl,
      nodes: v.nodes.length,
      targets: v.nodes.map((n) => ({
        // target 是选择器数组（frame 路径）；本项目没有 iframe，取第一段即可
        selector: String(n.target[0]),
        summary: String(n.failureSummary ?? '').split('\n').map((s) => s.trim()).filter(Boolean).join(' '),
        snippet: String(n.html ?? '').replace(/\s+/g, ' ').slice(0, 200),
      })),
    })),
    manualChecks,
  }
}

/** 打印成人能读的一段（Playwright 的 list reporter 会把它带到终端） */
function printAudit(result: AuditResult): void {
  const violationNodes = result.violations.reduce((sum, v) => sum + v.nodes, 0)
  const lines: string[] = [
    '',
    `── a11y 扫描：${result.label} ─────────────────────────────`,
    `   URL        ${result.url}`,
    `   DOM 元素    ${result.nodeCount}   （${result.engine}，tags: ${AXE_TAGS.join(', ')}）`,
    `   规则        共 ${result.rulesEvaluated}：通过 ${result.passes} / 违规 ${result.violations.length}（${violationNodes} 节点）/ 需人工确认 ${result.manualChecks.length} / 不适用 ${result.inapplicable}`,
  ]
  if (result.violations.length === 0) {
    lines.push('   违规详情    无')
  } else {
    lines.push(`   违规详情    ${result.violations.length} 条规则 / ${violationNodes} 个节点`)
    for (const v of result.violations) {
      lines.push(`     • [${v.impact}] ${v.rule} ×${v.nodes}   ${v.helpUrl}`)
      for (const t of v.targets.slice(0, 4)) {
        lines.push(`         ${t.selector}`)
        lines.push(`           ${t.summary}`)
      }
      if (v.targets.length > 4) lines.push(`         …另有 ${v.targets.length - 4} 个节点`)
    }
  }
  if (result.manualChecks.length > 0) {
    // 这些**不是**通过：axe 判不了（典型是背景是渐变/图片，对比度算不出来）。
    // 打进终端是为了让"还有多少东西要人眼看"这件事在每次运行时都可见 ——
    // 否则 §4 的人工清单会慢慢被当成"已经覆盖了"。
    lines.push(`   需人工确认  ${result.manualChecks.length} 个节点（axe 判不了）：`)
    for (const check of result.manualChecks.slice(0, 6)) {
      lines.push(`     ? ${check.rule} @ ${check.selector}`)
    }
    if (result.manualChecks.length > 6) {
      lines.push(`     ? …另有 ${result.manualChecks.length - 6} 个（完整清单见本次运行的 JSON 附件）`)
    }
  }
  console.log(lines.join('\n'))
}

/**
 * 一处"量出来的"对比度事实（真 Chromium 里的 computed style）。
 *
 * 这是**证据**，不是新的门禁 —— 门禁仍然只有 axe 的违规 + `REGISTRY` 上限。
 */
interface ContrastMeasurement {
  /** 命中的元素（tag + id + class） */
  target: string
  /** 元素文本（用于人核对量的是哪一个徽章） */
  text: string
  /** 计算后的前景色 */
  color: string
  /** 逐层合成后的**有效**背景色 */
  background: string
  /** 那个背景来自哪一层（谁真正决定了对比度） */
  backgroundFrom: string
  fontSize: string
  fontWeight: string
  /** 该字号/字重下 WCAG AA 的门槛（大文本 3，正文 4.5） */
  required: number
  /** 实测对比度 */
  ratio: number
  passesAA: boolean
}

/**
 * 量出选择器命中元素的**实际**前景色、有效背景色与对比度比值。
 *
 * ## 为什么 axe 之外还要量一遍
 *
 * axe 的 `color-contrast` 已经把不达标的元素判成违规 —— 那条进登记表、是门禁。
 * 这里量的是**给下一轮修的人看的数字**：哪个色值、压在哪层背景上、差多少。
 * （`docs/a11y-audit.md` §3 里 F-06 / F-19 的"修前 / 修后"就是靠这种一次
 * `getComputedStyle` 得出的结论，而不是靠猜色值算的。）
 *
 * 背景是**逐层往上合成**的：徽章自己通常是 `transparent`，真正决定对比度的是
 * 祖先卡片的底色。只读元素自己的 `backgroundColor` 会得到 `rgba(0, 0, 0, 0)`,
 * 那正是 axe 把节点丢进 `incomplete`（"算不出背景"）的原因 ——
 * 而这件事花一次 `getComputedStyle` 就有答案（同 §4.3 的结论）。
 *
 * ## 还有一种 axe **结构上**判不了的情况：单个字符的文本
 *
 * axe-core 的 `colorContrastEvaluate` 里有一条：
 * `shortTextContent = visibleText.length === 1`，此时只要对比度不足，
 * 它**不下结论**（节点进 `incomplete`，messageKey `shortTextContent`），
 * 于是"高 / 中 / 低"这种一个字的状态徽章**永远不会变成违规**。
 * 那一类只有这里的实测能给出信号 —— 这就是 `today-learn` 场景要量它的原因。
 *
 * ⚠️ 这里**只断言"量到了"**（元素数、有限数），不断言"量到的值合格/不合格"：
 * 把"现在是 3.11:1"写成断言，等于给下一轮修好它的人埋一个必红的测试
 * （修好一条不该让测试变红 —— 见文件头的判定尺度）。
 */
async function measureContrast(locator: Locator): Promise<ContrastMeasurement[]> {
  return locator.evaluateAll((elements) => {
    interface Rgba {
      r: number
      g: number
      b: number
      a: number
    }

    const parse = (value: string): Rgba | null => {
      const match = value.match(/rgba?\(([^)]+)\)/)
      if (!match) return null
      const parts = match[1].split(',').map((part) => Number.parseFloat(part.trim()))
      if (parts.length < 3 || parts.some((n) => Number.isNaN(n))) return null
      return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 }
    }

    const channel = (value: number): number => {
      const c = value / 255
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
    }
    const luminance = (c: Rgba): number =>
      0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b)
    const ratio = (a: Rgba, b: Rgba): number => {
      const la = luminance(a)
      const lb = luminance(b)
      return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
    }

    /** 元素的可读描述（够人找到它就行，不追求唯一） */
    const describe = (el: Element): string => {
      const id = el.id ? `#${el.id}` : ''
      const cls =
        typeof el.className === 'string' && el.className.trim()
          ? `.${el.className.trim().split(/\s+/).join('.')}`
          : ''
      return `${el.tagName.toLowerCase()}${id}${cls}`
    }
    const format = (c: Rgba): string => `rgb(${Math.round(c.r)}, ${Math.round(c.g)}, ${Math.round(c.b)})`

    return elements.map((el) => {
      const style = getComputedStyle(el)
      const fg = parse(style.color) ?? { r: 0, g: 0, b: 0, a: 1 }

      // 从元素自己往上收集有颜色的背景层，遇到第一个不透明层为止
      const layers: { color: Rgba; from: string }[] = []
      let node: Element | null = el
      while (node) {
        const bg = parse(getComputedStyle(node).backgroundColor)
        if (bg && bg.a > 0) {
          layers.push({ color: bg, from: describe(node) })
          if (bg.a >= 1) break
        }
        node = node.parentElement
      }

      // 从最底层往上合成（半透明层压在祖先色上，不是压在白底上）
      let composed: Rgba = { r: 255, g: 255, b: 255, a: 1 }
      for (const layer of layers.reverse()) {
        const a = layer.color.a
        composed = {
          r: layer.color.r * a + composed.r * (1 - a),
          g: layer.color.g * a + composed.g * (1 - a),
          b: layer.color.b * a + composed.b * (1 - a),
          a: 1,
        }
      }

      const size = Number.parseFloat(style.fontSize)
      const weight = Number.parseInt(style.fontWeight, 10) || 400
      // WCAG 1.4.3 的"大文本"豁免：≥24px，或 ≥18.66px 且粗体
      const large = size >= 24 || (size >= 18.66 && weight >= 700)
      const required = large ? 3 : 4.5
      const value = ratio(fg, composed)

      return {
        target: describe(el),
        text: (el.textContent ?? '').trim(),
        color: format(fg),
        background: format(composed),
        backgroundFrom: layers.length > 0 ? layers[layers.length - 1].from : '（没有找到任何背景层，按白底算）',
        fontSize: style.fontSize,
        fontWeight: style.fontWeight,
        required,
        ratio: Math.round(value * 100) / 100,
        passesAA: value >= required,
      }
    })
  })
}

/** 场景参数 */
interface SceneOptions {
  scene: string
  /** 该页**独有**的渲染标记：它出现才算"页面真的渲染出来了" */
  ready: () => Promise<void>
  /**
   * DOM 元素数下限。取值依据：实测值往下留约 30% 余量（写死实测值会让
   * 无关的样式调整把测试弄红；留太多则失去"页面没渲染出来"的保护）。
   */
  minNodes: number
}

/**
 * 扫一个页面，并把违规与 `REGISTRY` 对账后断言。
 *
 * @returns 精简后的审计结果（调用方可以再断言别的）
 */
async function auditScene(page: Page, log: A11yStubLog, opts: SceneOptions): Promise<AuditResult> {
  await opts.ready()

  // 数据回来后常还有一次重排（列表、统计、力导向图）。
  // 刻意**不**用 `waitForLoadState('networkidle')`：Vite 的 HMR 常驻连接会让它永不成立。
  await page.waitForTimeout(500)

  const results = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze()
  const nodeCount = await page.evaluate(() => document.querySelectorAll('*').length)
  const result = summarize(opts.scene, page.url(), nodeCount, results)

  await test.info().attach(`a11y-${opts.scene}.json`, {
    body: JSON.stringify(result, null, 2),
    contentType: 'application/json',
  })
  printAudit(result)

  // ── 防空过 ──
  expect(
    nodeCount,
    `${opts.scene}: DOM 元素太少（${nodeCount} < ${opts.minNodes}），页面可能没渲染出来 —— 此时"0 违规"没有意义`,
  ).toBeGreaterThanOrEqual(opts.minNodes)
  expect(result.passes, `${opts.scene}: axe 一条规则都没通过 —— 扫描对象可能是空的，结果不可信`).toBeGreaterThan(0)
  expect(result.rulesEvaluated, `${opts.scene}: axe 评估的规则数异常偏少`).toBeGreaterThan(20)
  expect(log.unmatched, `${opts.scene}: 有接口没桩（见 e2e/a11y-fixtures.ts），该区域没有被审计到`).toEqual([])
  expect(log.pageErrors, `${opts.scene}: 页面出现未捕获异常，渲染结果不完整`).toEqual([])

  // ── 与登记表对账 ──
  const { unregistered, grew, upgraded, flaky, seen } = reconcile(result, opts.scene)

  if (seen.length > 0) {
    console.log(`   [已登记] ${[...new Set(seen)].sort().join(', ')} —— 逐条理由见 docs/a11y-audit.md §3`)
  }

  expect(flaky, `${opts.scene}: 登记表在（场景，规则）粒度上不唯一，匹配会失效`).toEqual([])
  expect(unregistered, `${opts.scene}: 出现**未登记**的违规（新回归，或需要补进 REGISTRY 并写明理由）`).toEqual([])
  expect(grew, `${opts.scene}: 已登记违规的**影响面扩大**了（同一规则命中更多元素）`).toEqual([])
  expect(upgraded, `${opts.scene}: 已登记违规的**严重度上升**了`).toEqual([])

  return result
}

// ────────────────────────────────────────────────────────────────────────────
// 场景
// ────────────────────────────────────────────────────────────────────────────

test.describe('可访问性审计（axe-core，真 Chromium）', () => {
  /**
   * 登录页（未认证入口）+ 登录失败态。
   *
   * 两个状态分开扫：错误态的 `role="alert"` 文案与它在深色背景上的对比度
   * 属于另一个渲染分支 —— 只扫初始态等于没扫过失败态。
   */
  test('登录页：初始态与登录失败态', async ({ page }) => {
    const log = await installA11yStubs(page)

    await auditScene(page, log, {
      scene: 'login',
      ready: async () => {
        await page.goto('/', { waitUntil: 'domcontentloaded' })
        await expect(page.getByRole('heading', { name: '登录 EngramNote' })).toBeVisible()
      },
      // 实测 48（表单很小，这是真实值，不是渲染失败）
      minNodes: 35,
    })

    // 失败态需要**真的**拿到 401：页面上那个 `role="alert"` 只在凭据被拒时渲染。
    // 桩必须在这里换成 401（默认桩为了能进到应用里，给的是成功响应）——
    // 否则这条用例会停在"alert 一直不出现"，而失败信息不会告诉你原因。
    await page.unroute((url) => isApiUrl(url))
    const errorLog = await installA11yStubs(page, {
      '/api/auth/login': { __status: 401, detail: 'Incorrect email or password' },
    })

    await auditScene(page, errorLog, {
      scene: 'login-error',
      ready: async () => {
        // 真实交互走到失败态：空表单会被浏览器原生约束拦下（见 login-form.spec.ts），
        // 所以必须填非法凭据才会真的发出请求、拿到 401
        await page.locator('#email').fill('e2e@example.com')
        await page.locator('#password').fill('wrong-password')
        await page.getByRole('button', { name: '登录' }).click()
        await expect(page.getByRole('alert')).toBeVisible()
      },
      minNodes: 35,
    })
  })

  /** 已登录外壳 + 仪表盘：侧边栏（导航）、欢迎区、统计卡、最近笔记、趋势图 */
  test('已登录外壳与仪表盘', async ({ page }) => {
    const log = await installA11yStubs(page)
    await auditScene(page, log, {
      scene: 'dashboard',
      ready: async () => {
        await loginAs(page, '/')
        await expect(page.getByRole('heading', { name: '欢迎使用 EngramNote' })).toBeVisible()
        await expect(page.getByRole('heading', { name: '最近笔记' })).toBeVisible()
      },
      // 实测 208
      minNodes: 140,
    })
  })

  /** 笔记列表：搜索框、角色 Tab、筛选标签、笔记卡片（含 `role="button"` 的卡片本身） */
  test('笔记列表', async ({ page }) => {
    const log = await installA11yStubs(page)
    await auditScene(page, log, {
      scene: 'notes-list',
      ready: async () => {
        await loginAs(page, '/notes')
        // 空态的页面结构完全不同：必须等到"共 N 条"与非空列表一起出现，
        // 否则扫的是空状态，卡片上的问题一个都扫不到
        await expect(page.getByText('共 2 条')).toBeVisible()
        await expect(page.getByText('锂离子电池的浮充与均充')).toBeVisible()
      },
      // 实测 143
      minNodes: 90,
    })
  })

  /** 笔记详情：Markdown 渲染结果（标题层级、链接、代码）、元信息栏、关联区 */
  test('笔记详情', async ({ page }) => {
    const log = await installA11yStubs(page)
    await auditScene(page, log, {
      scene: 'note-detail',
      ready: async () => {
        await loginAs(page, '/notes/note-1')
        await expect(page.getByRole('heading', { name: /浮充与均充/ }).first()).toBeVisible()
      },
      // 实测 158
      minNodes: 110,
    })
  })

  /**
   * 卡片复习：正面（未翻面）与背面（已翻面 + 自评按钮 + 调度反馈）。
   *
   * 两个状态都要扫：翻面后多出的自评按钮区、"下次复习"文案在初始态根本不存在
   * —— 而那正是这一页最需要键盘与屏幕阅读器可用的一段。
   */
  test('卡片复习：正面与背面', async ({ page }) => {
    const log = await installA11yStubs(page)

    await auditScene(page, log, {
      scene: 'card-review-front',
      ready: async () => {
        await loginAs(page, '/review/cards')
        await expect(page.getByRole('button', { name: '显示答案' })).toBeVisible()
      },
      // 实测 123
      minNodes: 85,
    })

    await auditScene(page, log, {
      scene: 'card-review-back',
      ready: async () => {
        await page.getByRole('button', { name: '显示答案' }).click()
        await expect(page.getByText('刚才想得起来吗？')).toBeVisible()
      },
      // 实测 140
      minNodes: 85,
    })
  })

  /** 知识图谱：工具栏、搜索框、过滤器、侧栏统计（力导向图本体是 canvas，见文档 §4） */
  test('知识图谱', async ({ page }) => {
    const log = await installA11yStubs(page)
    await auditScene(page, log, {
      scene: 'knowledge-graph',
      ready: async () => {
        await loginAs(page, '/graph')
        await expect(page.getByRole('heading', { name: '知识图谱' })).toBeVisible()
        // 桩给了 4 个节点 → 工具栏的计数文案必须体现出来，
        // 否则说明图谱数据没进页面（canvas 空转，审计等于扫了个空壳）
        await expect(page.getByText(/4 节点/)).toBeVisible()
      },
      // 实测 183
      minNodes: 130,
    })
  })

  /** 项目页：标题、新建表单、项目卡片、说明区 */
  test('项目页', async ({ page }) => {
    const log = await installA11yStubs(page)
    await auditScene(page, log, {
      scene: 'projects',
      ready: async () => {
        await loginAs(page, '/projects')
        await expect(page.getByRole('heading', { name: '项目', exact: true })).toBeVisible()
        await expect(page.getByText('蓄电池基础')).toBeVisible()
      },
      // 实测 139
      minNodes: 100,
    })
  })

  // ────────────────────────────────────────────────────────────────────────
  // 覆盖轮（见 docs/a11y-audit.md §9）：把"从没被渲染过"的状态与页面补进来
  //
  // 这些场景不是"再扫一遍已知页面"，而是**让之前没有信号的东西出现**：
  //   - `converting` / `cleaning` 两个状态用的 `--color-warning`（#c4860a）
  //     在默认桩里一次都不会被渲染（§8.3 第 2 条）；
  //   - `DailyMaterials` / `TodayLearn` / `Upload` 上有与 F-09/F-17 逐字相同的
  //     `role="button"` 容器写法，但三页都不在审计范围里（§5 第 2 条 / §8.3 第 4 条）；
  //   - `Review` 有一次人工探针发现"整页没有 h1"（§8.3 第 1 条），但那是探针结论、
  //     不是门禁。
  // 上一条与下面每一条的 `minNodes` 都取自**实测值**（留 ~30% 余量），
  // 实测数字写在同行的注释里。
  // ────────────────────────────────────────────────────────────────────────

  /**
   * 今日资料（`DailyMaterials`）：文件夹列表 + **展开后**的资料列表。
   *
   * 为什么必须点开文件夹：这一页的笔记行、上传按钮、状态筛选标签全部在
   * `expandedFolderId === folder.id` 分支里 —— 只扫折叠态等于只扫了文件夹头。
   */
  test('今日资料：文件夹列表与展开后的资料', async ({ page }) => {
    const log = await installA11yStubs(page)
    await auditScene(page, log, {
      scene: 'daily-materials',
      ready: async () => {
        await loginAs(page, '/daily')
        await expect(page.getByRole('heading', { name: '今日资料' })).toBeVisible()
        // 文件夹头是 `div[role="button"]`（它里面还有真按钮 —— 见登记表）
        await page.getByRole('button', { name: /2026-01-05 学习资料/ }).click()
        // 展开后才会请求 /api/folders/folder-1：这三个标记都来自那一份数据
        await expect(page.getByText('浮充与均充的讲义.pdf')).toBeVisible()
        await expect(page.getByText('待转换：蓄电池巡检记录.docx')).toBeVisible()
        await expect(page.getByRole('button', { name: '上传文件' })).toBeVisible()
      },
      // 实测 157
      minNodes: 110,
    })
  })

  /**
   * 今日学习（`TodayLearn`）入口页。
   *
   * 两个覆盖（都写在 fixtures 里、只作用于本场景）：
   *   - `/api/goals/daily-plan` 从 400 换成有内容的计划 —— 「每日推荐任务」
   *     整块只在 `total_count > 0` 时渲染；
   *   - `/api/report/weak-points` 换成**契约形状**的那份 —— 默认桩的字段名
   *     与 `WeakPoint` 不一致，渲染出来全是 `undefined`（fixtures 里有说明）。
   */
  test('今日学习：推荐任务 / 今日报告 / 待复习 / 薄弱点', async ({ page }) => {
    const log = await installA11yStubs(page, {
      '/api/goals/daily-plan': DAILY_PLAN,
      '/api/report/weak-points': WEAK_POINTS,
    })
    await auditScene(page, log, {
      scene: 'today-learn',
      ready: async () => {
        await loginAs(page, '/today')
        await expect(page.getByRole('heading', { name: '今日学习' })).toBeVisible()
        await expect(page.getByRole('heading', { name: '每日推荐任务' })).toBeVisible()
        await expect(page.getByText('复习「均充的适用场景」')).toBeVisible()
        await expect(page.getByRole('heading', { name: '今日报告 (2026-01-06)' })).toBeVisible()
        // 待复习卡片的计数来自 /api/review/stats（12）：0 的话整块换成空状态
        await expect(page.getByText('今日待复习: 12 题')).toBeVisible()
        // `exact: true`：推荐任务的类别卡片里也有一个叫「薄弱点」的 h3
        // （"薄弱点共 1 项"），不写 exact 会被 Playwright 判成 strict 冲突
        await expect(page.getByRole('heading', { name: '薄弱点', exact: true })).toBeVisible()
        await expect(page.getByText('错3次 | 0.4%')).toBeVisible()
      },
      // 实测 171
      minNodes: 120,
    })

    // ── 量出三个优先级徽章（高/中/低）的真实色值与比值 ──
    //
    // 为什么这三条**必须**单独量：axe 的 `color-contrast` 对
    // "只有一个字符"的文本不下结论（`shortTextContent`，见 measureContrast 的说明），
    // 于是它们永远进 `incomplete`、永远不会变成违规 —— 这一条门禁守不住，
    // 只有实测能给出信号。
    const priorityBadges = await measureContrast(
      page.locator('.card').getByText(/^[高中低]$/),
    )
    expect(
      priorityBadges.map((m) => m.text),
      '三个优先级徽章没量全，本场景的证据不成立',
    ).toEqual(['高', '中', '低'])
    for (const m of priorityBadges) {
      expect(Number.isFinite(m.ratio), `${m.target}: 对比度没算出来`).toBe(true)
    }

    console.log(
      [
        '',
        '── 实测对比度（今日学习页的徽章，axe 判不了"单字符"那一类）──────────',
        ...priorityBadges.map(
          (m) =>
            `   ${m.text}（优先级徽章）  ${m.color} on ${m.background}（背景来自 ${m.backgroundFrom}）` +
            ` = ${m.ratio}:1，${m.fontSize}/${m.fontWeight} 要求 ${m.required}:1 → ${m.passesAA ? '达标' : '不足'}`,
        ),
        '   来源：TodayLearn.tsx 的 priorityMeta（#f44336 / #ff9800 / #10b981）+ 白字',
      ].join('\n'),
    )

    await test.info().attach('a11y-today-learn-badges.json', {
      body: JSON.stringify(
        {
          note: '量出来的事实，不是断言：axe 对单字符文本的对比度不下结论（shortTextContent）',
          priorityBadges,
        },
        null,
        2,
      ),
      contentType: 'application/json',
    })
  })

  /** 上传页（`Upload`）：拖拽区（`role="button"` + `aria-label`）、解析方式、项目标签、笔记类型 */
  test('上传页：拖拽上传区与三个设置卡片', async ({ page }) => {
    const log = await installA11yStubs(page)
    await auditScene(page, log, {
      scene: 'upload',
      ready: async () => {
        await loginAs(page, '/upload')
        await expect(page.getByRole('heading', { name: '上传学习资料' })).toBeVisible()
        // 这一页的主控件就是那个 role="button" 的拖拽区
        await expect(page.getByRole('button', { name: '点击或拖拽文件上传' })).toBeVisible()
        // 项目标签来自 /api/projects：桩回空数组时这里只剩一句"暂无项目"
        await expect(page.getByRole('button', { name: '蓄电池基础' })).toBeVisible()
        await expect(page.getByRole('button', { name: '云端解析' })).toBeVisible()
      },
      // 实测 128
      minNodes: 90,
    })
  })

  /**
   * 答题复习（`Review`）：选择题 + 难度徽章。
   *
   * 这一页此前**从未被扫过**（`/api/review/due` 没有桩，打开就是错误分支）：
   * 一次人工探针发现它整页没有 h1（docs/a11y-audit.md §8.3 第 1 条）。
   * 本轮把它变成场景 —— 探针结论与门禁结论的区别就在这里。
   */
  test('答题复习：选择题（含难度徽章）', async ({ page }) => {
    const log = await installA11yStubs(page)
    await auditScene(page, log, {
      scene: 'review',
      ready: async () => {
        await loginAs(page, '/review')
        await expect(page.getByText('浮充与均充的主要区别是什么？')).toBeVisible()
        await expect(page.getByRole('button', { name: '浮充长期恒压补偿自放电，均充短时升压校正' })).toBeVisible()
        // 复习元信息（已复习次数 / 间隔）来自 /api/review/due 的字段
        await expect(page.getByText('已复习 3 次 | 间隔 6 天')).toBeVisible()
      },
      // 实测 126
      minNodes: 85,
    })
  })

  /**
   * 笔记状态：`converting` / `cleaning`（= `.status-converting` / `.status-cleaning`）。
   *
   * 这两个类名都取 `--color-warning`（#c4860a，白底 3.11:1），而默认桩里
   * 只有 `cleaned` / `converted` —— 也就是说这条颜色**从来没被渲染过**。
   * 本场景用覆盖把带这两个状态的笔记喂进列表页，并且当场把
   * **实际的前景色 / 有效背景色 / 对比度比值**量出来（`measureContrast`），
   * 让下一轮修它的人有一个可以对照的数字，而不是一句"大概不够"。
   */
  test('笔记状态：转换中 / 清洗中（.status-converting / .status-cleaning）', async ({ page }) => {
    const log = await installA11yStubs(page, { '/api/notes': notesList(PROCESSING_NOTES) })
    await auditScene(page, log, {
      scene: 'notes-status-processing',
      ready: async () => {
        await loginAs(page, '/notes')
        await expect(page.getByText('共 2 条')).toBeVisible()
        await expect(page.getByText('待转换：蓄电池巡检记录.docx')).toBeVisible()
        await expect(page.getByText('待清洗：浮充与均充对照表.pdf')).toBeVisible()
        // 这两个类名是**本场景存在的全部理由**：等到它们真的挂上 DOM
        await expect(page.locator('.status-converting')).toHaveCount(1)
        await expect(page.locator('.status-cleaning')).toHaveCount(1)
      },
      // 实测 145
      minNodes: 100,
    })

    // ── 把色值与比值量出来（真 Chromium 的 computed style）──
    // 断言只覆盖"量到了"：两个徽章都在、比值是有限数；值本身进附件与终端输出。
    const measurements = await measureContrast(page.locator('.status-converting, .status-cleaning'))
    expect(measurements.map((m) => m.text), '两个状态徽章都没量到，本场景的证据不成立').toEqual([
      '转换中',
      '清洗中',
    ])
    for (const m of measurements) {
      expect(Number.isFinite(m.ratio), `${m.target}: 对比度没算出来`).toBe(true)
    }

    console.log(
      [
        '',
        '── 实测对比度（.status-converting / .status-cleaning）──────────',
        ...measurements.map(
          (m) =>
            `   ${m.text}  ${m.color} on ${m.background}（背景来自 ${m.backgroundFrom}）` +
            ` = ${m.ratio}:1，${m.fontSize}/${m.fontWeight} 要求 ${m.required}:1 → ${m.passesAA ? '达标' : '不足'}`,
        ),
      ].join('\n'),
    )

    await test.info().attach('a11y-notes-status-contrast.json', {
      body: JSON.stringify(
        {
          note: '这是量出来的事实，不是断言：门禁仍是 axe 的 color-contrast + REGISTRY 上限',
          cssVariable: '--color-warning: #c4860a（src/styles/base.css:40）',
          usedBy: ['.status-converting（converting）', '.status-cleaning（cleaning）'],
          measurements,
        },
        null,
        2,
      ),
      contentType: 'application/json',
    })
  })

  /**
   * 移动端视口（375×667）下的仪表盘 —— **不是**为了再扫一遍违规，
   * 而是为了把"axe 看不见触控目标尺寸"这件事变成一条可复核的记录：
   * axe-core 4.13 的规则集里**没有**触摸目标尺寸规则
   * （WCAG 2.2 的 2.5.8 Target Size 是 AA，axe 不实现；它只做 ARIA/结构/对比度）。
   *
   * 这条用例把移动端视口下的渲染结果固定下来并纳入审计（顺带证明
   * "换视口不会炸"），而**触控尺寸的判定留给人工清单**（`docs/a11y-audit.md` §4）
   * —— 那是本层明确做不到的事，不假装做到了。
   */
  test('移动端视口（375×667）：记录 axe 在移动端也判不出触控尺寸', async ({ page }) => {
    const log = await installA11yStubs(page)
    await page.setViewportSize({ width: 375, height: 667 })

    const width = await page.evaluate(() => window.innerWidth)
    expect(width, '视口没被设成移动端宽度').toBe(375)

    const result = await auditScene(page, log, {
      scene: 'dashboard-mobile',
      ready: async () => {
        await loginAs(page, '/')
        await expect(page.getByRole('heading', { name: '欢迎使用 EngramNote' })).toBeVisible()
        // 移动端专属控件：桌面由 CSS 隐藏（见 App.tsx / layout.css）
        await expect(page.getByRole('button', { name: '打开菜单' })).toBeVisible()
      },
      minNodes: 140,
    })

    // 明确记录：本场景 0 条"触控尺寸"类违规 —— 不是因为尺寸没问题，
    // 而是因为 axe 没有这条规则。想守住它必须另外写几何断言（见文档 §4）。
    const touchRules = result.violations.filter((v) => /target|touch/i.test(v.rule))
    expect(touchRules, 'axe 里没有触控尺寸规则（本条断言就是在钉住这个事实）').toEqual([])

    // 顺带量一个**真实**的触控尺寸事实，供文档 §4 的清单引用：
    // 侧边栏抽屉里的导航项高度（px）。它不进断言的上限，只进附件。
    const navBox = await page
      .getByRole('button', { name: /仪表盘/ })
      .first()
      .boundingBox()
    await test.info().attach('a11y-dashboard-mobile-touch.json', {
      body: JSON.stringify(
        {
          note: 'axe 不判触控目标尺寸；这里是量出来的真实值，供 docs/a11y-audit.md §4 的人工清单使用',
          primaryNavItemBox: navBox,
          viewport: { width: 375, height: 667 },
        },
        null,
        2,
      ),
      contentType: 'application/json',
    })
  })
})

/**
 * 自检：审计真的在扫东西吗？
 *
 * 这条用例故意**注入**一个必然违规的元素，断言 axe 抓得到。
 * 它守的是最坏的一种失败：桩、选择器或配置把页面搞空了，
 * 于是所有断言都因"没有违规"而通过 —— 那种绿灯是假绿。
 * 有了这条，本文件其余用例的通过才有意义。
 */
test.describe('审计自检', () => {
  test('注入一个必然违规的元素时，axe 必须报出来', async ({ page }) => {
    const log = await installA11yStubs(page)
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: '登录 EngramNote' })).toBeVisible()

    const injected = await page.evaluate(() => {
      const img = document.createElement('img')
      // 没有 alt 的 img：`image-alt` 是 axe 里最稳定的一条违规。
      // 用 1x1 的 data URI 而不是站点内的图片：本自检验的是 axe 的判定链路，
      // 不该依赖任何网络请求成不成功（请求失败也照样是"没有 alt 的 img"，
      // 但那样就多了一个与被测性质无关的变量）。
      img.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
      img.id = 'a11y-self-check'
      document.body.appendChild(img)
      return document.querySelectorAll('#a11y-self-check').length
    })
    expect(injected, '自检元素没能注入 DOM').toBe(1)

    const results = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze()
    const ids = results.violations.map((v) => v.id)
    expect(ids, 'axe 没有报出注入的 image-alt —— 扫描链路本身失效了').toContain('image-alt')

    await page.evaluate(() => document.querySelector('#a11y-self-check')?.remove())
    expect(log.pageErrors).toEqual([])
  })

  test('登记表里每个（场景，规则）组合都唯一，且场景名拼写正确', () => {
    // 场景名写错（如 'dashbord'）的后果是：那条豁免永远匹配不到，
    // 而页面上的违规照旧出现 → 测试失败信息却指向"未登记"，
    // 维护者会去加第二条豁免，越加越乱。这里一次性把拼写钉住。
    const knownScenes = new Set([
      'login',
      'login-error',
      'dashboard',
      'dashboard-mobile',
      'notes-list',
      'note-detail',
      'card-review-front',
      'card-review-back',
      'knowledge-graph',
      'projects',
      // ── 覆盖轮加进来的 5 个场景（见 docs/a11y-audit.md §9）──
      'notes-status-processing',
      'daily-materials',
      'today-learn',
      'upload',
      'review',
    ])

    const unknown = ACTIVE_REGISTRY.filter((entry) => !knownScenes.has(entry.scene))
    expect(unknown.map((entry) => `${entry.id}:${entry.scene}`), '登记表里有未知场景名').toEqual([])

    const pairs = ACTIVE_REGISTRY.map((entry) => `${entry.scene}/${entry.rule}`)
    const duplicates = pairs.filter((pair, index) => pairs.indexOf(pair) !== index)
    expect(duplicates, '登记表里有重复的（场景，规则）组合').toEqual([])

    // 每条豁免都必须写明归属，否则它就不是"已接受的风险"而是"忘了修"
    const noOwner = ACTIVE_REGISTRY.filter((entry) => !entry.reason.includes('归属：'))
    expect(noOwner.map((entry) => entry.id), '登记表里有没写归属的条目').toEqual([])
  })
})
