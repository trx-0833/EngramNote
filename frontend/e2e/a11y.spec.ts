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
import { expect, test, type Page } from '@playwright/test'

import { installA11yStubs, loginAs, type A11yStubLog } from './a11y-fixtures'
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
 * 侧边栏在每个已登录页面上都渲染，但 axe **按规则聚合**返回 ——
 * 所以「侧边栏标题对比度」和「状态徽章对比度」在同一个页面上会**合并成
 * 一条 `color-contrast` 结果**。登记表必须与实测粒度一致：
 * 一个（场景，规则）一条，`reason` 里把该页命中的**每一种成因**都写清。
 * 下面这份常量只用来记录"各页侧边栏与徽章各有多少个节点"，供 reason 复用。
 */
const SHELL_SIDEBAR_NODES = 3
const SHELL_STATUS_NODES = 2

const REGISTRY: RegisteredRule[] = [
  // ── 登录页（未认证入口）──────────────────────────────────────────────
  {
    id: 'F-01',
    rule: 'landmark-one-main',
    scene: 'login',
    impact: 'moderate',
    nodes: 1,
    reason: '登录页没有 <main> 地标（最外层是 div，见 src/pages/Auth.module.css）。屏幕阅读器无法一跳直达主内容。归属：5.9 修复轮。',
  },
  {
    id: 'F-02',
    rule: 'region',
    scene: 'login',
    impact: 'moderate',
    nodes: 6,
    reason: '登录页所有内容（h1、两个 label、两个 input、页脚 p）都不在任何 landmark 里 —— 与 F-01 同源。归属：5.9 修复轮。',
  },
  {
    // 与 login 是**同一页面的另一个状态**（提交了错误凭据），问题同源，
    // 但必须单独登记：登记表按（场景，规则）匹配，不做任何继承。
    id: 'F-01b',
    rule: 'landmark-one-main',
    scene: 'login-error',
    impact: 'moderate',
    nodes: 1,
    reason: '同 F-01：登录失败态仍没有 <main> 地标。归属：5.9 修复轮。',
  },
  {
    id: 'F-02b',
    rule: 'region',
    scene: 'login-error',
    impact: 'moderate',
    nodes: 6,
    reason: '同 F-02：登录失败态的所有内容（含错误提示所在的表单区）依旧不在任何 landmark 里。归属：5.9 修复轮。',
  },

  // ── 仪表盘 ──────────────────────────────────────────────────────────
  {
    id: 'F-03',
    rule: 'aria-allowed-role',
    scene: 'dashboard',
    impact: 'minor',
    nodes: 2,
    reason: '<article role="button"> —— ARIA 不允许 article 使用 button 角色（「最近笔记」两张卡片）。归属：5.9 修复轮（Dashboard.tsx）。',
  },
  {
    id: 'F-07',
    rule: 'heading-order',
    scene: 'dashboard',
    impact: 'moderate',
    nodes: 1,
    reason: 'h1「欢迎使用 EngramNote」之后直接出现卡片里的 h3（中间没有 h2）：「今日学习目标」「每日推荐任务」「今日待复习」「薄弱点」。归属：5.9 修复轮（Dashboard.tsx 的卡片标题改用 h2）。',
  },
  {
    id: 'F-09',
    rule: 'nested-interactive',
    scene: 'dashboard',
    impact: 'serious',
    nodes: 1,
    reason: '「今日待复习」卡片是 div[role=button]，内部又有一个 <button>开始复习</button> —— 按钮里嵌按钮，屏幕阅读器与键盘都会异常。归属：5.9 修复轮（Dashboard.tsx:289-304）。',
  },
  {
    id: 'F-04',
    rule: 'color-contrast',
    scene: 'dashboard',
    impact: 'serious',
    nodes: 3 + SHELL_STATUS_NODES,
    reason: `两种成因合并成一条 axe 结果：侧边栏 ${SHELL_SIDEBAR_NODES} 个分组标题（--color-text-tertiary #9a9ab0，白底 2.75:1）+ ${SHELL_STATUS_NODES} 个状态徽章（--color-success #2d8a56，白底 4.3:1）。均为 11.2~12.8px 小字，要求 4.5:1。归属：5.6 设计令牌 / 5.9 修复轮。`,
  },

  // ── 移动端视口下的仪表盘（同一份 DOM，另加移动端汉堡按钮）─────────────
  {
    id: 'F-21',
    rule: 'aria-allowed-role',
    scene: 'dashboard-mobile',
    impact: 'minor',
    nodes: 2,
    reason: '移动端视口下与桌面是同一份 DOM（<article role="button">），即 F-03 在窄屏上的复现。归属：5.9 修复轮（Dashboard.tsx）。',
  },
  {
    id: 'F-22',
    rule: 'heading-order',
    scene: 'dashboard-mobile',
    impact: 'moderate',
    nodes: 1,
    reason: '同 F-07 在窄屏上的复现（h1 → h3 跳级）。归属：5.9 修复轮（Dashboard.tsx）。',
  },
  {
    id: 'F-23',
    rule: 'nested-interactive',
    scene: 'dashboard-mobile',
    impact: 'serious',
    nodes: 1,
    reason: '同 F-09 在窄屏上的复现（div[role=button] 里嵌 <button>）。归属：5.9 修复轮（Dashboard.tsx:289-304）。',
  },
  {
    id: 'F-24',
    rule: 'color-contrast',
    scene: 'dashboard-mobile',
    impact: 'serious',
    nodes: SHELL_STATUS_NODES,
    reason: `窄屏下侧边栏被收进抽屉（分组标题不在可见 DOM 里），可见的只剩 ${SHELL_STATUS_NODES} 个状态徽章（--color-success，白底 4.3:1）。归属：5.6 / 5.9。`,
  },

  // ── 笔记列表 ────────────────────────────────────────────────────────
  {
    id: 'F-16',
    rule: 'aria-allowed-role',
    scene: 'notes-list',
    impact: 'minor',
    nodes: 2,
    reason: '笔记卡片同样写作 <article role="button">（NotesList.tsx:212-219），与 F-03 同一类写法。归属：5.9 修复轮。',
  },
  {
    id: 'F-17',
    rule: 'nested-interactive',
    scene: 'notes-list',
    impact: 'serious',
    nodes: 2,
    reason: '笔记卡片本身是 role=button，卡片里又有可聚焦的按钮（删除/重试），共 2 张卡片各命中一次。归属：5.9 修复轮 —— 与 F-09 同源，修法应当统一（卡片不用 role=button，改为内部真链接 + 独立操作按钮）。',
  },
  {
    id: 'F-18',
    rule: 'page-has-heading-one',
    scene: 'notes-list',
    impact: 'moderate',
    nodes: 1,
    reason: '笔记列表页没有 h1（页面上最高级标题是卡片里的 h3）。归属：5.9 修复轮。',
  },
  {
    id: 'F-25',
    rule: 'color-contrast',
    scene: 'notes-list',
    impact: 'serious',
    nodes: SHELL_SIDEBAR_NODES + 3,
    reason: `三种成因合并成一条 axe 结果：侧边栏 ${SHELL_SIDEBAR_NODES} 个分组标题 + 3 个状态徽章（--color-success）+ 笔记卡片上的项目标签（内联样式 background: var(--color-primary-soft,#eef2ff) / color: var(--color-primary,#2563eb)，NotesList.tsx:229）。归属：5.6 / 5.9。`,
  },

  // ── 笔记详情 ────────────────────────────────────────────────────────
  {
    id: 'F-08',
    rule: 'heading-order',
    scene: 'note-detail',
    impact: 'moderate',
    nodes: 1,
    reason: 'Markdown 正文里的 h4 出现在页面 h1/h2 之后但没有 h3 —— 层级来自**用户内容本身**，属于"内容决定的结构"。仍记录：修法要先决定是否在渲染时归一化标题级别（见文档 §4 的取舍）。归属：5.9 修复轮评估。',
  },
  {
    id: 'F-11',
    rule: 'select-name',
    scene: 'note-detail',
    impact: 'critical',
    nodes: 1,
    reason: '笔记详情的「笔记角色」<select> 没有 label/aria-label（NoteDetailHeader.tsx:137）。归属：5.9 修复轮 —— 一行 aria-label 可修。',
  },
  {
    id: 'F-12',
    rule: 'color-contrast',
    scene: 'note-detail',
    impact: 'serious',
    nodes: SHELL_SIDEBAR_NODES + 1 + 1,
    reason: `三种成因合并成一条 axe 结果：侧边栏 ${SHELL_SIDEBAR_NODES} 个分组标题 + .status-cleaned（--color-success 在 --color-bg 上 4.08:1）+ 「笔记角色」下拉框（白字 #fff 配 #3b82f6 底 = 3.67:1，NoteDetailHeader.tsx:147-156 的内联样式）。归属：5.6 / 5.9。`,
  },
  {
    id: 'F-13',
    rule: 'link-in-text-block',
    scene: 'note-detail',
    impact: 'serious',
    nodes: 1,
    reason: 'Markdown 正文里的链接既没有下划线（base.css:120-124 的 a{text-decoration:none}），与周围文字的对比也只有 1.36:1（要求 3:1）—— 不靠颜色分辨不出这是链接。归属：5.9 修复轮（markdown 正文链接样式）。',
  },

  // ── 卡片复习 ────────────────────────────────────────────────────────
  {
    id: 'F-14',
    rule: 'page-has-heading-one',
    scene: 'card-review-front',
    impact: 'moderate',
    nodes: 1,
    reason: '卡片复习页没有任何 h1（ReviewProgress 用 div + 文本，"卡片复习"只是普通文本）。归属：5.9 修复轮。',
  },
  {
    // 正反两面都有：它来自「原文语境」组件的入口链接，未展开时也在
    id: 'F-26',
    rule: 'link-in-text-block',
    scene: 'card-review-front',
    impact: 'serious',
    nodes: 1,
    reason: '链接既无下划线、与周围文字对比也只有 1.89:1（SourceContext.tsx:120 的「在笔记中查看完整原文 →」）。归属：5.9 修复轮（链接样式）。',
  },
  {
    id: 'F-19b',
    rule: 'color-contrast',
    scene: 'card-review-front',
    impact: 'serious',
    nodes: SHELL_SIDEBAR_NODES,
    reason: `翻面前页面上只有侧边栏 ${SHELL_SIDEBAR_NODES} 个分组标题命中（自评按钮尚未渲染），成因同 F-04。归属：5.6 / 5.9。`,
  },
  {
    id: 'F-15',
    rule: 'page-has-heading-one',
    scene: 'card-review-back',
    impact: 'moderate',
    nodes: 1,
    reason: '同 F-14，翻面后状态同样没有 h1。归属：5.9 修复轮。',
  },
  {
    id: 'F-19',
    rule: 'color-contrast',
    scene: 'card-review-back',
    impact: 'serious',
    nodes: SHELL_SIDEBAR_NODES + 2,
    reason: `两种成因合并成一条 axe 结果：侧边栏 ${SHELL_SIDEBAR_NODES} 个分组标题（F-04）+ 自评四档的强调色（来自 utils/labels.ts 的 selfRatingOptions：「勉强想起」#c9a959 白底 2.25:1、「想起来了」#2d8a56 白底 4.3:1）。**同一组件也用在答题复习页（Review）**，那里本轮未扫（见文档 §5）。归属：5.9 修复轮（改 labels.ts 两个色值）。`,
  },
  {
    id: 'F-26b',
    rule: 'link-in-text-block',
    scene: 'card-review-back',
    impact: 'serious',
    nodes: 1,
    reason: '同 F-26（同一链接在翻面后的状态）。归属：5.9 修复轮。',
  },

  // ── 知识图谱 ────────────────────────────────────────────────────────
  {
    id: 'F-10',
    rule: 'select-name',
    scene: 'knowledge-graph',
    impact: 'critical',
    nodes: 1,
    reason: '图谱的卡片类型过滤器 <select> 没有 label/aria-label（GraphToolbar.tsx:100）。归属：5.9 修复轮 —— 一行 aria-label 可修。',
  },
  {
    id: 'F-05',
    rule: 'color-contrast',
    scene: 'knowledge-graph',
    impact: 'serious',
    nodes: SHELL_SIDEBAR_NODES + 1,
    reason: `两种成因合并成一条 axe 结果：侧边栏 ${SHELL_SIDEBAR_NODES} 个分组标题 + 图谱侧栏面板标题 .graph-panel-title（同一令牌 --color-text-tertiary，graph.css:186-195，白底 2.75:1）。归属：5.6 / 5.9。`,
  },

  // ── 项目页 ──────────────────────────────────────────────────────────
  {
    id: 'F-20',
    rule: 'heading-order',
    scene: 'projects',
    impact: 'moderate',
    nodes: 1,
    reason: '项目页 h1「项目」之后，卡片区里的 h3 之前没有 h2（与仪表盘同一类）。归属：5.9 修复轮。',
  },
  {
    id: 'F-27',
    rule: 'color-contrast',
    scene: 'projects',
    impact: 'serious',
    nodes: SHELL_SIDEBAR_NODES,
    reason: `仅侧边栏 ${SHELL_SIDEBAR_NODES} 个分组标题（--color-text-tertiary，白底 2.75:1）。归属：5.6 / 5.9。`,
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
