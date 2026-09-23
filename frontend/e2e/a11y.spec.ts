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
import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Locator, type Page } from '@playwright/test';

import {
  installA11yStubs,
  loginAs,
  notesList,
  ASSESSMENT_NOTES,
  DAILY_PLAN,
  GOAL_STUBS,
  PROCESSING_NOTES,
  WEAK_POINTS,
  type A11yStubLog,
} from './a11y-fixtures';
import { isApiUrl } from './support';

const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'];

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
test.describe.configure({ mode: 'parallel', timeout: 90_000 });

/** axe 的影响等级，从轻到重（用于"不得比登记时更严重"的判定） */
const IMPACT_ORDER = ['minor', 'moderate', 'serious', 'critical'] as const;
type Impact = (typeof IMPACT_ORDER)[number];

function impactRank(impact: string): number {
  const index = IMPACT_ORDER.indexOf(impact as Impact);
  return index === -1 ? 0 : index;
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
  id: string;
  rule: string;
  /** 出现在哪个扫描场景（与用例里的 `scene` 一致） */
  scene: string;
  /** 登记时的影响等级：实测等级**高于**它 → 失败 */
  impact: Impact;
  /** 登记时该规则命中的节点数：实测**多于**它 → 失败 */
  nodes: number;
  /** 是什么问题、命中哪些元素、归属哪个工作流 */
  reason: string;
}

/**
 * ## ★ 当前状态（Part A/B 收尾轮）：**`REGISTRY` 是空的**
 *
 * 扫描场景 **25 → 26**（新增 `learning-goals-create`：新建目标弹窗），
 * 违规 **5 组 / 5 个节点 → 0**，登记表 **5 条 → 0 条**。
 * 逐条的修法与"名字从哪儿来"见下面第二段注释（`REGISTRY` 的定义处）。
 * **空表 = 零容忍**（不是"没有门禁"）：没登记的规则一律 `unregistered` → 失败。
 *
 * 本轮还补了**两类不依赖 axe 的门禁**（都在本文件里，见各自的注释）：
 *   1. **键盘可达性扫描**（`findFakeAffordances`，每次 `auditScene` 都跑）——
 *      "手型光标但键盘到不了" / "tabindex 挂在非控件上" / "role=button 挂在
 *      div 上" 三类，全部按零容忍断言。这一轮 Part A 修的 12 处，
 *      **axe 一处都没报过**（它不模拟 Tab，见 docs/a11y-audit.md §4.1）。
 *   2. **Tab 走查断言**（`expectReachableByTab`）—— 在受影响的场景里真的按 Tab，
 *      断言焦点会落到那个控件上（不是"它有 role=button"这种自证）。
 *
 * ⚠️ 下面这几段是**历史**（保留：它们是"改了多少"的凭据，删了就没法对照）。
 * 读它们时请以本节的状态为准。
 *
 * ────────────────────────────────────────────────────────────────────────
 * ## （历史）5.9 修复轮之后：登记表从 30 条（68 个节点）降到 4 条（4 个节点）
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
 * ## （历史）覆盖轮之后：4 条（4 个节点）→ **12 条（15 个节点）**
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
 *
 * ────────────────────────────────────────────────────────────────────────
 * ## （历史）清债 + 覆盖第二轮：**12 条（15 个节点）→ 5 条（5 个节点）**，
 * ## 扫描场景 **15 → 25**
 *
 * 上面那 8 条债里 **7 条已修、登记项已删除**（F-28/F-29/F-30/F-32/F-33/F-34/F-35），
 * 只剩 F-31 —— 它与 F-07/F-22/F-08/F-20 是同一笔债：
 * **待人工决定的标题层级**（见文件末尾的开放问题）。
 * 同时把最后 10 个页面接成场景（`qa` / `knowledge-cards` / `card-detail` /
 * `learning-assessment` / `learning-goals` / `trash` / `quick-review` /
 * `question-sets` / `register` / `not-found`），
 * **新场景一条登记项都没加**（10 个全是 0 违规 —— 带出来的 4 处问题当场改掉了：
 * 注册页缺 `<main>`、回收站/卡片详情/学习评估/知识卡片 的标题层级）。
 *
 * **新增的一类门禁**：`today-learn` 里那条基于 `measureContrast` 的
 * **阈值断言**（axe 对单字符文本按设计不下结论，F-36 只能这样守，见 §10.7）。
 */

/**
 * ────────────────────────────────────────────────────────────────────────
 * ## ★ 当前状态：**登记表是空的**（Part A/B 收尾轮）
 *
 * `REGISTRY` 从 **5 条（5 个节点）→ 0 条**。这是本文件第一次出现空表，
 * 含义必须说清楚：**空表不是"没有门禁"，而是门禁最紧的形态** ——
 * `reconcile()` 对**没登记的规则**一律判 `unregistered` → 失败，
 * 所以任何一条 axe 违规（哪怕只有 1 个节点）出现在任何一个场景里都会红。
 * 没有豁免、没有上限、没有"先登记下来以后再修"。
 *
 * 删掉的 5 条全是 `heading-order`，而且是**同一类**：区块标题的级别缺一级。
 * 它们"需要人拍板"的从来不是修法，而是**区块该叫什么名字**。
 * 这一轮的答案是：**一个新名字都不用起** —— 每个区块的可访问名都取自
 * 屏幕上**已经存在的字**（就是那些卡片/文件夹/资料自己的标题），
 * 做法是把标题**提到它本来就该在的级别**（与 F-18、F-14/F-15、trash、
 * card-detail、learning-assessment、knowledge-cards 的先例逐字相同：
 * 级别与视觉大小是两件事，字号一律显式钉住）：
 *
 * | 已删除 | 原来 | 改成 | 名字从哪来（屏幕上已有的字） |
 * |---|---|---|---|
 * | F-07 / F-22 | dashboard（桌面 + 移动）：h1 → 卡片 `h3` | 四张卡片标题 `h3` → **`h2`**（字号 `1.17em` 钉住 = UA 的 `h3` 字号，**计算值不变**） | 「今日学习目标」「每日推荐任务」「今日待复习: N 题」「薄弱点」 |
 * | F-20 | projects：h1 → 卡片 `h3` | `ProjectCard` 的标题与 `NewProjectForm` 的卡片标题 `h3` → **`h2`**（字号本来就显式写着：1.05rem / 1rem） | 项目名（「蓄电池基础」）与「创建新项目」 |
 * | F-31 | daily-materials：h1 → 文件夹名 `h3` → 资料名 `h4` | 文件夹名 `h3` → **`h2`**、资料名 `h4` → **`h3`**（字号不变：`1.17em` 钉住 / 0.9rem 本来就显式） | 文件夹名（「2026-01-05 学习资料」）与资料标题 |
 * | F-08 | note-detail：h1 → 「清洗统计」`h4` | `CleaningPanel` 的两个 `h4`（清洗统计 / 重复块）→ **`h2`**（字号 0.875rem 本来就显式） | 「清洗统计」「重复块（N 个）」 |
 *
 * ### ⚠️ F-08 的成因在这一轮被更正了（重要）
 *
 * 报出来的那**一个节点**是**页面自己的**「清洗统计」区块（`CleaningPanel.tsx`），
 * **不是**用户的 Markdown。实测的标题序列是
 * `H1 笔记标题 → H4 清洗统计 → H1 正文 → H2 正文`：其中只有 `H1 → H4`
 * 是跳级；`H4 → H1` 是**上行**，而 axe 的判据是
 * `currLevel - prevLevel <= 1`（`axe.js` 的 `headingOrderAfter`）—— 上行永远合法。
 * 所以**一个字的用户内容都不用动**：把页面自己的 `h4` 放回 `h2`，F-08 整条消失。
 * 上一轮"层级来自用户内容本身"的判断，把页面自己的元信息区块当成了用户正文
 * —— 这正是 docs/a11y-audit.md 反复强调的那件事：**不实测的成因解释与没有解释一样误导**。
 *
 * ### 为什么"给两组各加一个区块标题"没有被采纳（F-07/F-22）
 *
 * 仪表盘那两组（`.dashboardTwoCol`）只是**两栏排版**，不是产品概念：
 * 四张卡片各有各的标题、各有各的行为（进目标 / 展开任务 / 开始复习 / 进卡片）。
 * 给它们硬造一个上位名字（"今日进度""需要关注"之类）会得到一个
 * **只为了标题层级而存在的标题**，读屏的标题列表里会多出两个什么都不管的条目。
 * 真正缺的那一级是"这些卡片是这一页的顶层区块"—— 而它们**本来就是**，
 * 只是级别写小了一级。所以答案是提升**已经存在的**卡片标题，而不是发明新名字。
 * projects 的 F-20、daily-materials 的 F-31 是同一件事的另外两处。
 * ────────────────────────────────────────────────────────────────────────
 */
const REGISTRY: RegisteredRule[] = [];

/**
 * ────────────────────────────────────────────────────────────────────────
 * ## 历史登记项（**已全部清偿**；`reason` 逐字留在 docs/a11y-audit.md §3）
 *
 * 删掉登记项这件事本身**就是收紧**（`reconcile()` 对没登记的规则判
 * `unregistered` → 失败），所以"删除"从来不等于"放行"。
 * 5 条的理由与修法逐条记在 docs/a11y-audit.md 的「修复」列与 §11；
 * 这里只留一条最容易被重新提出的反对意见与它的答复：
 *
 * > **"把卡片标题从 h3 改成 h2 只是消掉报警，真正的结构问题原样留着。"**
 *
 * 答复：结构问题是"h1 与卡片标题之间缺一级"，改成 h2 **就是在补那一级**。
 * 反过来说，"给两栏各起一个名字"才是引入一个产品上不存在的概念 ——
 * 那不是修结构，那是**给排版加语义**。判据是：**这个标题下面管的是不是
 * 一个真正的区块**。四张卡片各自独立（标题、行为、空状态都不同），
 * 所以它们各自是一级；两栏只是它们恰好在屏幕上并排。
 * ────────────────────────────────────────────────────────────────────────
 */

const ACTIVE_REGISTRY = REGISTRY;

/**
 * 该登记项是否覆盖这个场景。
 *
 * 就是相等判断 —— 刻意**不**做任何"别名/继承"。原因见 `SHELL_SCENES` 的说明：
 * 别名会让一条规则在一个场景里命中两条登记项，匹配随即失效（静默放行）。
 */
function coversScene(entry: RegisteredRule, scene: string): boolean {
  return entry.scene === scene;
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
  const unregistered: string[] = [];
  const grew: string[] = [];
  const upgraded: string[] = [];
  const seen: string[] = [];

  // ── 登记表自身的一致性：同一（场景，规则）只能有一条 ──
  // 两条以上时，`find` 永远只命中第一条，其余形同虚设 ——
  // 那是"看起来登记了、实际在放行"，必须当场报出来（而不是等它某天漏掉一个回归）。
  const flaky: string[] = [];
  const byRule = new Map<string, RegisteredRule[]>();
  for (const entry of ACTIVE_REGISTRY.filter((r) => coversScene(r, scene))) {
    byRule.set(entry.rule, [...(byRule.get(entry.rule) ?? []), entry]);
  }
  for (const [rule, entries] of byRule) {
    if (entries.length > 1 && result.violations.some((v) => v.rule === rule)) {
      flaky.push(
        `${scene} / ${rule}：有 ${entries.length} 条登记项（${entries.map((e) => e.id).join('、')}），匹配会有歧义 —— 合并成一条`,
      );
    }
  }

  // ── 逐条对账 ──
  for (const violation of result.violations) {
    const entry = ACTIVE_REGISTRY.find((r) => r.rule === violation.rule && coversScene(r, scene));

    if (!entry) {
      const targets = violation.targets
        .map((t) => `${t.selector} :: ${t.summary}`)
        .join('\n        ');
      unregistered.push(
        `${violation.rule} [${violation.impact}] ×${violation.nodes}\n        ${targets}`,
      );
      continue;
    }

    seen.push(entry.id);
    if (violation.nodes > entry.nodes) {
      grew.push(
        `${entry.id} ${violation.rule}：登记上限 ${entry.nodes} 个节点，实测 ${violation.nodes} 个（${violation.targets.map((t) => t.selector).join('、')}）`,
      );
    }
    if (impactRank(violation.impact) > impactRank(entry.impact)) {
      upgraded.push(
        `${entry.id} ${violation.rule}：等级由 ${entry.impact} 升为 ${violation.impact}`,
      );
    }
  }

  return { unregistered, grew, upgraded, flaky, seen };
}

/** 一条违规的**证据**（进文档表格、进终端输出、进 JSON 附件） */
interface ViolationRecord {
  rule: string;
  impact: string;
  /** axe 的 helpUrl，便于复核规则定义 */
  helpUrl: string;
  /** 受影响节点数 */
  nodes: number;
  /** 每个节点的目标选择器 + 失败摘要 + HTML 片段 */
  targets: { selector: string; summary: string; snippet: string }[];
}

interface AuditResult {
  label: string;
  url: string;
  /** DOM 元素总数（防空过的证据之一） */
  nodeCount: number;
  engine: string;
  passes: number;
  inapplicable: number;
  violations: ViolationRecord[];
  /** axe 评估过的规则总数（pass + incomplete + inapplicable + violations） */
  rulesEvaluated: number;
  /**
   * axe **不敢下结论**的节点（`incomplete`）：绝大多数是"背景是渐变/图片，
   * 对比度算不出来"。它们不是违规，但也**不是通过** —— 是人工复核清单。
   */
  manualChecks: { rule: string; selector: string; summary: string }[];
}

type AxeResults = Awaited<ReturnType<AxeBuilder['analyze']>>;

/** axe 结果 → 精简记录（只留复核需要的字段） */
function summarize(
  label: string,
  url: string,
  nodeCount: number,
  results: AxeResults,
): AuditResult {
  const manualChecks = (results.incomplete ?? []).flatMap((rule) =>
    (rule.nodes ?? []).map((n) => ({
      rule: rule.id,
      selector: String(n.target[0]),
      summary:
        String(n.failureSummary ?? '')
          .split('\n')
          .filter(Boolean)[0] ?? '',
    })),
  );

  return {
    label,
    url,
    nodeCount,
    engine: `axe-core ${results.testEngine.version}`,
    passes: results.passes.length,
    inapplicable: results.inapplicable.length,
    rulesEvaluated:
      results.passes.length +
      results.incomplete.length +
      results.inapplicable.length +
      results.violations.length,
    violations: results.violations.map((v) => ({
      rule: v.id,
      impact: v.impact ?? 'unknown',
      helpUrl: v.helpUrl,
      nodes: v.nodes.length,
      targets: v.nodes.map((n) => ({
        // target 是选择器数组（frame 路径）；本项目没有 iframe，取第一段即可
        selector: String(n.target[0]),
        summary: String(n.failureSummary ?? '')
          .split('\n')
          .map((s) => s.trim())
          .filter(Boolean)
          .join(' '),
        snippet: String(n.html ?? '')
          .replace(/\s+/g, ' ')
          .slice(0, 200),
      })),
    })),
    manualChecks,
  };
}

/** 打印成人能读的一段（Playwright 的 list reporter 会把它带到终端） */
function printAudit(result: AuditResult): void {
  const violationNodes = result.violations.reduce((sum, v) => sum + v.nodes, 0);
  const lines: string[] = [
    '',
    `── a11y 扫描：${result.label} ─────────────────────────────`,
    `   URL        ${result.url}`,
    `   DOM 元素    ${result.nodeCount}   （${result.engine}，tags: ${AXE_TAGS.join(', ')}）`,
    `   规则        共 ${result.rulesEvaluated}：通过 ${result.passes} / 违规 ${result.violations.length}（${violationNodes} 节点）/ 需人工确认 ${result.manualChecks.length} / 不适用 ${result.inapplicable}`,
  ];
  if (result.violations.length === 0) {
    lines.push('   违规详情    无');
  } else {
    lines.push(`   违规详情    ${result.violations.length} 条规则 / ${violationNodes} 个节点`);
    for (const v of result.violations) {
      lines.push(`     • [${v.impact}] ${v.rule} ×${v.nodes}   ${v.helpUrl}`);
      for (const t of v.targets.slice(0, 4)) {
        lines.push(`         ${t.selector}`);
        lines.push(`           ${t.summary}`);
      }
      if (v.targets.length > 4) lines.push(`         …另有 ${v.targets.length - 4} 个节点`);
    }
  }
  if (result.manualChecks.length > 0) {
    // 这些**不是**通过：axe 判不了（典型是背景是渐变/图片，对比度算不出来）。
    // 打进终端是为了让"还有多少东西要人眼看"这件事在每次运行时都可见 ——
    // 否则 §4 的人工清单会慢慢被当成"已经覆盖了"。
    lines.push(`   需人工确认  ${result.manualChecks.length} 个节点（axe 判不了）：`);
    for (const check of result.manualChecks.slice(0, 6)) {
      lines.push(`     ? ${check.rule} @ ${check.selector}`);
    }
    if (result.manualChecks.length > 6) {
      lines.push(
        `     ? …另有 ${result.manualChecks.length - 6} 个（完整清单见本次运行的 JSON 附件）`,
      );
    }
  }
  console.log(lines.join('\n'));
}

/**
 * 一处"量出来的"对比度事实（真 Chromium 里的 computed style）。
 *
 * 这是**证据**，不是新的门禁 —— 门禁仍然只有 axe 的违规 + `REGISTRY` 上限。
 */
interface ContrastMeasurement {
  /** 命中的元素（tag + id + class） */
  target: string;
  /** 元素文本（用于人核对量的是哪一个徽章） */
  text: string;
  /** 计算后的前景色 */
  color: string;
  /** 逐层合成后的**有效**背景色 */
  background: string;
  /** 那个背景来自哪一层（谁真正决定了对比度） */
  backgroundFrom: string;
  fontSize: string;
  fontWeight: string;
  /** 该字号/字重下 WCAG AA 的门槛（大文本 3，正文 4.5） */
  required: number;
  /** 实测对比度 */
  ratio: number;
  passesAA: boolean;
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
 * ⚠️ **这里只负责"量"，断言在调用方**（函数本身不做通过/不通过判定）：
 * 多数调用方只断言"量到了"（元素数、有限数），因为把"现在是 3.11:1"写成断言，
 * 等于给下一轮修好它的人埋一个必红的测试（修好一条不该让测试变红 —— 见文件头）。
 * 唯一的例外是今日学习页的优先级徽章：axe **结构上**判不了它们
 * （见上一条），所以那里断言的是**阈值**（`ratio >= required`，即 ≥4.5:1），
 * 不是当前值 —— 修好即通过、退化即失败。
 */
async function measureContrast(locator: Locator): Promise<ContrastMeasurement[]> {
  return locator.evaluateAll((elements) => {
    interface Rgba {
      r: number;
      g: number;
      b: number;
      a: number;
    }

    const parse = (value: string): Rgba | null => {
      const match = value.match(/rgba?\(([^)]+)\)/);
      if (!match) return null;
      const parts = match[1].split(',').map((part) => Number.parseFloat(part.trim()));
      if (parts.length < 3 || parts.some((n) => Number.isNaN(n))) return null;
      return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
    };

    const channel = (value: number): number => {
      const c = value / 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    };
    const luminance = (c: Rgba): number =>
      0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
    const ratio = (a: Rgba, b: Rgba): number => {
      const la = luminance(a);
      const lb = luminance(b);
      return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
    };

    /** 元素的可读描述（够人找到它就行，不追求唯一） */
    const describe = (el: Element): string => {
      const id = el.id ? `#${el.id}` : '';
      const cls =
        typeof el.className === 'string' && el.className.trim()
          ? `.${el.className.trim().split(/\s+/).join('.')}`
          : '';
      return `${el.tagName.toLowerCase()}${id}${cls}`;
    };
    const format = (c: Rgba): string =>
      `rgb(${Math.round(c.r)}, ${Math.round(c.g)}, ${Math.round(c.b)})`;

    return elements.map((el) => {
      const style = getComputedStyle(el);
      const fg = parse(style.color) ?? { r: 0, g: 0, b: 0, a: 1 };

      // 从元素自己往上收集有颜色的背景层，遇到第一个不透明层为止
      const layers: { color: Rgba; from: string }[] = [];
      let node: Element | null = el;
      while (node) {
        const bg = parse(getComputedStyle(node).backgroundColor);
        if (bg && bg.a > 0) {
          layers.push({ color: bg, from: describe(node) });
          if (bg.a >= 1) break;
        }
        node = node.parentElement;
      }

      // 从最底层往上合成（半透明层压在祖先色上，不是压在白底上）
      let composed: Rgba = { r: 255, g: 255, b: 255, a: 1 };
      for (const layer of layers.reverse()) {
        const a = layer.color.a;
        composed = {
          r: layer.color.r * a + composed.r * (1 - a),
          g: layer.color.g * a + composed.g * (1 - a),
          b: layer.color.b * a + composed.b * (1 - a),
          a: 1,
        };
      }

      const size = Number.parseFloat(style.fontSize);
      const weight = Number.parseInt(style.fontWeight, 10) || 400;
      // WCAG 1.4.3 的"大文本"豁免：≥24px，或 ≥18.66px 且粗体
      const large = size >= 24 || (size >= 18.66 && weight >= 700);
      const required = large ? 3 : 4.5;
      const value = ratio(fg, composed);

      return {
        target: describe(el),
        text: (el.textContent ?? '').trim(),
        color: format(fg),
        background: format(composed),
        backgroundFrom:
          layers.length > 0 ? layers[layers.length - 1].from : '（没有找到任何背景层，按白底算）',
        fontSize: style.fontSize,
        fontWeight: style.fontWeight,
        required,
        ratio: Math.round(value * 100) / 100,
        passesAA: value >= required,
      };
    });
  });
}

/**
 * ★ **F-37：键盘可达性扫描**（本文件唯一一条**不依赖 axe** 的通用门禁）
 *
 * ## 为什么必须有它：axe 不模拟 Tab
 *
 * axe 只看 DOM 与计算样式。于是下面这一类问题在 axe 眼里**完全不存在**：
 *
 *   - `div[onClick]` 既没有 `role` 也没有 `tabIndex` → **键盘根本到不了**。
 *     `nested-interactive` 的前提是"外层有交互角色"，`aria-allowed-role`
 *     的前提是"有一个不允许的角色" —— 两条件都不成立，于是**一条规则都不报**；
 *   - `div[role="button"][tabIndex=0]` → axe 只在"里面还有可聚焦元素"时
 *     报 `nested-interactive`；里面没有的话它一句话都不说，而线上症状是
 *     **Tab 停在一个"不是按钮的按钮"上，而且只认 Enter、不认 Space**；
 *   - 内联 `outline: 'none'` → 不属于任何 ARIA 规则，永远不报（§4.1）。
 *
 * 这一轮（Part A）修的 12 处**全部属于这一类** —— 也就是说：
 * **这一整轮的输入不是 axe 给的，是"人工看代码 + grep"给的**。
 * 那就必须给它补一条门禁，否则下一轮同样只能靠人再看一遍。
 *
 * ## 判据（三条，逐条对应"用户的键盘会遇到什么"）
 *
 * 1. **手型光标但不可聚焦**：`cursor: pointer` 是"这里能点"的承诺，
 *    而元素自己不可聚焦、祖先里没有可聚焦控件、里面也没有 ——
 *    鼠标能点、键盘永远到不了。
 * 2. **`tabindex >= 0` 挂在非控件上**：Tab 会停在它上面，但它既不是链接
 *    也不是按钮（读屏念不出"按钮"），Space 通常也不生效。
 * 3. **`role="button"` / `role="link"` 挂在非原生元素上**：一个"不是按钮的按钮"。
 *    项目口径是**用真控件**（F-09/F-17/F-30/F-34 四处修法一致），
 *    所以这条按零容忍守着。真要出现合法用法，应该**先在这里写清理由再加白名单**，
 *    而不是把规则删掉 —— 与 `REGISTRY` 是同一条规矩。
 *
 * ## 为什么是"扫描"而不是"只看我改过的那几处"
 *
 * 规则来自 `frontend/src/**` 的写法，不来自某个页面。这一轮就是靠这条判据
 * 在**审计之外的页面**上又找出 4 处同类问题（`ProjectNotesList` 的笔记行、
 * `Dashboard` 的薄弱点行与推荐任务行、`TodayLearn` 的薄弱点行）。
 * 只盯着"改过的那几处"写断言，等于把"这类洞没有了"又一次误读成
 * "这个洞修好了"（BG.11 那条教训）。
 */
async function findFakeAffordances(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    /** 元素的可读描述（够人找到它就行） */
    const describe = (el: Element): string => {
      const cls =
        typeof el.className === 'string' && el.className.trim()
          ? `.${el.className.trim().split(/\s+/).join('.')}`
          : '';
      const text = (el.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 24);
      return `${el.tagName.toLowerCase()}${cls}${text ? `「${text}」` : ''}`;
    };
    /** 真的占了版面（`display:none` / 祖先隐藏的元素不参与判定） */
    const isVisible = (el: Element): boolean => el.getClientRects().length > 0;
    /**
     * 浏览器**能**把焦点交给它吗（≈ 会不会出现在 Tab 序列里）。
     * `tabindex="-1"` 刻意不算：它能被 `focus()` 主动聚焦，但 Tab 到不了 ——
     * 这一轮修的所有东西要的正是"Tab 到得了"。
     */
    const isTabbable = (el: Element): boolean => {
      if (!isVisible(el)) return false;
      if (el.hasAttribute('disabled')) return false;
      const tabindex = el.getAttribute('tabindex');
      if (tabindex !== null) return Number(tabindex) >= 0;
      const tag = el.tagName.toLowerCase();
      if (tag === 'a' || tag === 'area') return el.hasAttribute('href');
      return tag === 'button' || tag === 'input' || tag === 'select' || tag === 'textarea';
    };
    const NATIVE_INTERACTIVE = new Set(['a', 'button', 'input', 'select', 'textarea', 'summary']);

    /**
     * "手型区域"的最外层根。
     *
     * ⚠️ `cursor` 是**继承属性**：一个容器设了 `cursor: pointer`，它**所有**后代
     * 的计算值都是 `pointer`。所以不能逐个元素判"它是不是可点区域" ——
     * 那样会把容器里的每一个 `span` / `p` 都报一遍（第一版就是这么写的，
     * 一次跑出 14 条噪音，其中 13 条是卡片里的徽章）。
     * 正确的判据是：先找到这个手型区域的最外层根，再看**那个区域里**
     * 有没有可 Tab 的控件 —— 一个区域只报一次。
     */
    const pointerRegionRoot = (el: Element): Element => {
      let root = el;
      let node = el.parentElement;
      while (node && getComputedStyle(node).cursor === 'pointer') {
        root = node;
        node = node.parentElement;
      }
      return root;
    };

    const findings: string[] = [];
    const reportedRegions = new Set<Element>();
    for (const el of Array.from(document.querySelectorAll('*'))) {
      if (!isVisible(el)) continue;
      const tag = el.tagName.toLowerCase();
      const role = el.getAttribute('role');
      const tabindex = el.getAttribute('tabindex');
      const ownTabIndex = tabindex !== null && Number(tabindex) >= 0;
      const nativeInteractive = NATIVE_INTERACTIVE.has(tag);

      // ① 手型区域里没有任何可 Tab 的控件（自己 / 区域根 / 区域内部都没有）
      if (getComputedStyle(el).cursor === 'pointer') {
        const root = pointerRegionRoot(el);
        if (!reportedRegions.has(root) && !isTabbable(root)) {
          const regionHasTabbable = Array.from(root.querySelectorAll('*')).some(isTabbable);
          if (!regionHasTabbable) {
            reportedRegions.add(root);
            findings.push(
              `① 手型光标（cursor:pointer）但整个区域里没有可 Tab 的控件：${describe(root)}`,
            );
          }
        }
        // 注意：这里**不** `continue` —— 区域根自己可能还是 ②/③ 的命中对象
      }

      // ② tabindex 挂在非控件上（Tab 停在"不是控件的东西"上）
      if (ownTabIndex && !nativeInteractive && !role) {
        findings.push(`② tabindex 挂在非控件上（Tab 会停在它上面，但它不是控件）：${describe(el)}`);
        continue;
      }

      // ③ role=button / role=link 挂在非原生元素上（"不是按钮的按钮"）
      if ((role === 'button' || role === 'link') && !nativeInteractive) {
        findings.push(`③ 假控件角色（role="${role}" 挂在 <${tag}> 上）：${describe(el)}`);
        continue;
      }
    }
    return findings;
  });
}

/**
 * ★ **F-37 的第二半：真的按 Tab**，断言焦点会落到目标元素上。
 *
 * ## 为什么不能只断言"它有 role=button"
 *
 * 那是**自证**：`role="button"` + `tabIndex={0}` 也满足它，而那种写法的
 * 真实症状（键位不对、读屏语义不对）一个都测不出来。
 * 这一条走的是用户的路：**点一下页面标题把"顺序焦点导航起点"定住，
 * 然后一次次按 Tab**，直到焦点落到目标上（或超次数失败）。
 *
 * ## 起点为什么是"点 h1"
 *
 * `document.body.focus()` 不行（body 不可聚焦），而"从当前位置继续 Tab"
 * 会受上一步交互影响（`ready()` 里往往点过按钮）—— 那样这条断言就不确定了。
 * 浏览器有一条明确规则：**点击一个不可聚焦的元素，会把"顺序焦点导航起点"
 * 设到它上面**，下一次 Tab 从它之后开始。页面 h1 是不可聚焦的、且在所有
 * 场景里都先于被测控件出现，所以它是最稳的锚点。
 *
 * ⚠️ 弹窗场景（`learning-goals-create`）**不能**用这个锚点：遮罩盖住了 h1，
 * 点击会被拦截。那一条改用"弹窗自己承诺的东西"（`role`/`aria-modal`/Esc）来测。
 */
async function expectReachableByTab(
  page: Page,
  target: Locator,
  label: string,
  maxTabs = 80,
): Promise<void> {
  const handle = await target.first().elementHandle();
  expect(handle, `${label}：目标元素不在 DOM 里，键盘门禁不成立`).not.toBeNull();

  // 起点：页面标题（不可聚焦）—— 见上面的说明
  await page.locator('main h1').first().click();

  let tabs = 0;
  for (let i = 1; i <= maxTabs; i++) {
    await page.keyboard.press('Tab');
    tabs = i;
    if (await handle!.evaluate((el) => el === document.activeElement)) break;
  }

  const landedOnTarget = await handle!.evaluate((el) => el === document.activeElement);
  const landed = landedOnTarget
    ? ''
    : await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (!el) return '（焦点已经不在页面里）';
        const cls =
          typeof el.className === 'string' && el.className.trim()
            ? `.${el.className.trim().split(/\s+/).join('.')}`
            : '';
        return `${el.tagName.toLowerCase()}${cls}`;
      });

  expect(
    landedOnTarget,
    `${label}：从页面标题起按了 ${tabs} 次 Tab，焦点都没有落到它上面（最后停在 ${landed}）` +
      ' —— 也就是说**键盘到不了它**。修法是把它换成真控件（button / Link），' +
      '而不是给它加 role + tabIndex（本项目四处先例：F-09/F-17/F-30/F-34）。',
  ).toBe(true);

  console.log(`   [键盘] ${label}：第 ${tabs} 次 Tab 落到它上面`);
}

/**
 * ★ 「**改标题级别，不改外观**」的断言（Part B 的交付条件之一）。
 *
 * ## 为什么必须有它
 *
 * 本轮的 5 条 `heading-order` 全是"区块标题的级别缺一级"，修法是**提升级别**
 * （h3 → h2、h4 → h2/h3）。而这个项目的样式表里有两条事实：
 *
 *   1. `base.css` 的全局 reset 把 `margin` / `padding` 清零了，
 *      但**没有**清字号 —— 字号来自 UA 样式表：`h1 2em / h2 1.5em / h3 1.17em /
 *      h4 1em / h5 .83em / h6 .67em`；
 *   2. 因此"改级别"默认会**改字号**（h3 → h2 就是 1.17em → 1.5em，大了一圈）。
 *
 * 项目已有的做法是**把字号显式钉住**（F-18 的 `1.17rem`、F-14/F-15、
 * trash、card-detail、learning-assessment 都是这么做的）。这条断言把
 * "钉住了"从**口头承诺**变成**门禁**：它算出"改动前那一级在同一个父元素下
 * 会是多少 px"（UA 的 em 倍率 × 父元素字号），再要求实测值等于它。
 *
 * ⚠️ 它**不是**在断言某个像素值：期望值由父元素的实时字号推出，
 * 所以父元素字号变了、或者设计整体调了基准字号，这条断言照样成立 ——
 * 它守的是"**级别与大小是两件事**"，不是"18.72px 这个数"。
 *
 * @param previousLevel 这个标题**改动前**的级别（例如 h3 → h2 时传 `'h3'`）
 */
async function expectFontSizeUnchanged(
  target: Locator,
  label: string,
  previousLevel: 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6',
): Promise<void> {
  /** UA 样式表里的 `font-size`（相对父元素的 em） */
  const UA_EM: Record<string, number> = { h1: 2, h2: 1.5, h3: 1.17, h4: 1, h5: 0.83, h6: 0.67 };
  const measured = await target.evaluate((el, ratio) => {
    const parent = el.parentElement;
    const parentSize = parent ? Number.parseFloat(getComputedStyle(parent).fontSize) : 16;
    return {
      actual: Number.parseFloat(getComputedStyle(el).fontSize),
      expected: Math.round(parentSize * ratio * 100) / 100,
      parentSize,
      level: el.tagName.toLowerCase(),
    };
  }, UA_EM[previousLevel]);

  console.log(
    `   [字号] ${label}：<${measured.level}> 实测 ${measured.actual}px，` +
      `改动前的 <${previousLevel}> 在同一个父元素（${measured.parentSize}px）下是 ${measured.expected}px`,
  );
  expect(
    measured.actual,
    `${label}：改了标题级别之后字号跟着变了（实测 ${measured.actual}px，` +
      `改动前的 <${previousLevel}> 是 ${measured.expected}px）—— ` +
      '标题级别与视觉大小是两件事，改级别时必须把字号显式钉住' +
      '（本项目的先例：F-18 的 `fontSize: 1.17rem`、card-detail 的 `1rem`…）。',
  ).toBeCloseTo(measured.expected, 1);
}

/** 场景参数 */
interface SceneOptions {
  scene: string;
  /** 该页**独有**的渲染标记：它出现才算"页面真的渲染出来了" */
  ready: () => Promise<void>;
  /**
   * DOM 元素数下限。取值依据：实测值往下留约 30% 余量（写死实测值会让
   * 无关的样式调整把测试弄红；留太多则失去"页面没渲染出来"的保护）。
   */
  minNodes: number;
}

/**
 * 扫一个页面，并把违规与 `REGISTRY` 对账后断言。
 *
 * @returns 精简后的审计结果（调用方可以再断言别的）
 */
async function auditScene(page: Page, log: A11yStubLog, opts: SceneOptions): Promise<AuditResult> {
  await opts.ready();

  // 数据回来后常还有一次重排（列表、统计、力导向图）。
  // 刻意**不**用 `waitForLoadState('networkidle')`：Vite 的 HMR 常驻连接会让它永不成立。
  await page.waitForTimeout(500);

  const results = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
  const nodeCount = await page.evaluate(() => document.querySelectorAll('*').length);
  const result = summarize(opts.scene, page.url(), nodeCount, results);

  await test.info().attach(`a11y-${opts.scene}.json`, {
    body: JSON.stringify(result, null, 2),
    contentType: 'application/json',
  });
  printAudit(result);

  // ── 防空过 ──
  expect(
    nodeCount,
    `${opts.scene}: DOM 元素太少（${nodeCount} < ${opts.minNodes}），页面可能没渲染出来 —— 此时"0 违规"没有意义`,
  ).toBeGreaterThanOrEqual(opts.minNodes);
  expect(
    result.passes,
    `${opts.scene}: axe 一条规则都没通过 —— 扫描对象可能是空的，结果不可信`,
  ).toBeGreaterThan(0);
  expect(result.rulesEvaluated, `${opts.scene}: axe 评估的规则数异常偏少`).toBeGreaterThan(20);
  expect(
    log.unmatched,
    `${opts.scene}: 有接口没桩（见 e2e/a11y-fixtures.ts），该区域没有被审计到`,
  ).toEqual([]);
  expect(log.pageErrors, `${opts.scene}: 页面出现未捕获异常，渲染结果不完整`).toEqual([]);

  // ── 与登记表对账 ──
  const { unregistered, grew, upgraded, flaky, seen } = reconcile(result, opts.scene);

  if (seen.length > 0) {
    console.log(
      `   [已登记] ${[...new Set(seen)].sort().join(', ')} —— 逐条理由见 docs/a11y-audit.md §3`,
    );
  }

  expect(flaky, `${opts.scene}: 登记表在（场景，规则）粒度上不唯一，匹配会失效`).toEqual([]);
  expect(
    unregistered,
    `${opts.scene}: 出现**未登记**的违规（新回归，或需要补进 REGISTRY 并写明理由）`,
  ).toEqual([]);
  expect(grew, `${opts.scene}: 已登记违规的**影响面扩大**了（同一规则命中更多元素）`).toEqual([]);
  expect(upgraded, `${opts.scene}: 已登记违规的**严重度上升**了`).toEqual([]);

  // ── F-37：键盘可达性扫描（**每个场景都跑**，与 axe 无关）──
  //
  // 放在每个场景里而不是"只在我改过的那几页"：判据来自 `frontend/src/**`
  // 的写法，不来自某个页面。这一轮就是靠它在本轮之前**没有任何场景覆盖**的
  // 页面上又找出 4 处同类问题（见 findFakeAffordances 的说明）。
  const fakeAffordances = await findFakeAffordances(page);
  if (fakeAffordances.length > 0) {
    console.log(
      `   [键盘] ${opts.scene}: 命中 ${fakeAffordances.length} 处"看起来能点、键盘到不了"`,
    );
    for (const item of fakeAffordances) console.log(`        ${item}`);
  }
  expect(
    fakeAffordances,
    `${opts.scene}: 出现"看起来能点、键盘到不了"的元素（F-37，见 docs/a11y-audit.md §4.1）。` +
      'axe 结构上报不出这一类（它不模拟 Tab），所以只有这条扫描守着。' +
      '修法是换成真控件（button / Link）或把行为移到真控件上，' +
      '**不要**给 div 加 role + tabIndex。',
  ).toEqual([]);

  return result;
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
    const log = await installA11yStubs(page);

    await auditScene(page, log, {
      scene: 'login',
      ready: async () => {
        await page.goto('/', { waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('heading', { name: '登录 EngramNote' })).toBeVisible();
      },
      // 实测 48（表单很小，这是真实值，不是渲染失败）
      minNodes: 35,
    });

    // 失败态需要**真的**拿到 401：页面上那个 `role="alert"` 只在凭据被拒时渲染。
    // 桩必须在这里换成 401（默认桩为了能进到应用里，给的是成功响应）——
    // 否则这条用例会停在"alert 一直不出现"，而失败信息不会告诉你原因。
    await page.unroute((url) => isApiUrl(url));
    const errorLog = await installA11yStubs(page, {
      '/api/auth/login': { __status: 401, detail: 'Incorrect email or password' },
    });

    await auditScene(page, errorLog, {
      scene: 'login-error',
      ready: async () => {
        // 真实交互走到失败态：空表单会被浏览器原生约束拦下（见 login-form.spec.ts），
        // 所以必须填非法凭据才会真的发出请求、拿到 401
        await page.locator('#email').fill('e2e@example.com');
        await page.locator('#password').fill('wrong-password');
        await page.getByRole('button', { name: '登录' }).click();
        await expect(page.getByRole('alert')).toBeVisible();
      },
      minNodes: 35,
    });
  });

  /**
   * 已登录外壳 + 仪表盘：侧边栏（导航）、欢迎区、统计卡、最近笔记、趋势图。
   *
   * ## 本轮在这一页上加了两处
   *
   * 1. **`/api/goals/daily-plan` 从默认的 400 换成 `DAILY_PLAN`**。
   *    默认桩给的是 400（"无活跃目标"），于是「每日推荐任务」整块**从来不渲染**，
   *    里面那几行可点任务**一次都没被扫过**（F-37 的键盘扫描也一样看不见）。
   *    用**场景级覆盖**而不是改默认桩：`dashboard-mobile` 与其它场景的基线
   *    一个元素都不动（这是 fixtures 文件里那条规矩）。
   * 2. **键盘走查**：卡片标题是链接、薄弱点每一行是链接 —— 都真的按 Tab 验过。
   *    这两处此前分别是 `div[role="button"][tabIndex=0]`（只认 Enter）
   *    与 `div[onClick]`（根本没有 role/tabIndex）—— **axe 两处都没报过**。
   */
  test('已登录外壳与仪表盘', async ({ page }) => {
    const log = await installA11yStubs(page, { '/api/goals/daily-plan': DAILY_PLAN });
    await auditScene(page, log, {
      scene: 'dashboard',
      ready: async () => {
        await loginAs(page, '/');
        await expect(page.getByRole('heading', { name: '欢迎使用 EngramNote' })).toBeVisible();
        await expect(page.getByRole('heading', { name: '最近笔记' })).toBeVisible();
        // 推荐任务整块：只在 `total_count > 0` 时渲染（本轮才第一次出现）
        await expect(page.getByRole('heading', { name: '每日推荐任务' })).toBeVisible();
        await expect(page.getByText('复习「均充的适用场景」')).toBeVisible();
      },
      // 实测 208（旧基线）→ 231（加上推荐任务整块之后）
      minNodes: 140,
    });

    // ── 键盘走查（F-37 的第二半）──
    await expectReachableByTab(
      page,
      page.getByRole('link', { name: '今日学习目标' }),
      '仪表盘：卡片标题「今日学习目标」',
    );
    await expectReachableByTab(
      page,
      page.getByRole('link', { name: '均充的适用场景' }),
      '仪表盘：薄弱点第一行',
    );
    await expectReachableByTab(
      page,
      page.getByRole('button', { name: '复习「均充的适用场景」' }),
      '仪表盘：每日推荐任务里可点的那一行',
    );

    // ── 「改级别不改外观」：这四张卡片的标题由 h3 提升为 h2（F-07）──
    // 字号在 tsx 里显式钉成 `1.17em`（= UA 的 `h3` 字号），这条断言守着它。
    await expectFontSizeUnchanged(
      page.getByRole('heading', { name: '今日学习目标' }),
      '仪表盘「今日学习目标」',
      'h3',
    );
    await expectFontSizeUnchanged(
      page.getByRole('heading', { name: '每日推荐任务' }),
      '仪表盘「每日推荐任务」',
      'h3',
    );
    await expectFontSizeUnchanged(
      page.getByRole('heading', { name: /^今日待复习/ }),
      '仪表盘「今日待复习: N 题」',
      'h3',
    );
    await expectFontSizeUnchanged(
      page.getByRole('heading', { name: '薄弱点', exact: true }),
      '仪表盘「薄弱点」',
      'h3',
    );
  });

  /** 笔记列表：搜索框、角色 Tab、筛选标签、笔记卡片（含 `role="button"` 的卡片本身） */
  test('笔记列表', async ({ page }) => {
    const log = await installA11yStubs(page);
    await auditScene(page, log, {
      scene: 'notes-list',
      ready: async () => {
        await loginAs(page, '/notes');
        // 空态的页面结构完全不同：必须等到"共 N 条"与非空列表一起出现，
        // 否则扫的是空状态，卡片上的问题一个都扫不到
        await expect(page.getByText('共 2 条')).toBeVisible();
        await expect(page.getByText('锂离子电池的浮充与均充')).toBeVisible();
      },
      // 实测 143
      minNodes: 90,
    });
  });

  /** 笔记详情：Markdown 渲染结果（标题层级、链接、代码）、元信息栏、关联区 */
  test('笔记详情', async ({ page }) => {
    const log = await installA11yStubs(page);
    await auditScene(page, log, {
      scene: 'note-detail',
      ready: async () => {
        await loginAs(page, '/notes/note-1');
        await expect(page.getByRole('heading', { name: /浮充与均充/ }).first()).toBeVisible();
      },
      // 实测 158
      minNodes: 110,
    });
  });

  /**
   * 卡片复习：正面（未翻面）与背面（已翻面 + 自评按钮 + 调度反馈）。
   *
   * 两个状态都要扫：翻面后多出的自评按钮区、"下次复习"文案在初始态根本不存在
   * —— 而那正是这一页最需要键盘与屏幕阅读器可用的一段。
   */
  test('卡片复习：正面与背面', async ({ page }) => {
    const log = await installA11yStubs(page);

    await auditScene(page, log, {
      scene: 'card-review-front',
      ready: async () => {
        await loginAs(page, '/review/cards');
        await expect(page.getByRole('button', { name: '显示答案' })).toBeVisible();
      },
      // 实测 123
      minNodes: 85,
    });

    await auditScene(page, log, {
      scene: 'card-review-back',
      ready: async () => {
        await page.getByRole('button', { name: '显示答案' }).click();
        await expect(page.getByText('刚才想得起来吗？')).toBeVisible();
      },
      // 实测 140
      minNodes: 85,
    });
  });

  /** 知识图谱：工具栏、搜索框、过滤器、侧栏统计（力导向图本体是 canvas，见文档 §4） */
  test('知识图谱', async ({ page }) => {
    const log = await installA11yStubs(page);
    await auditScene(page, log, {
      scene: 'knowledge-graph',
      ready: async () => {
        await loginAs(page, '/graph');
        await expect(page.getByRole('heading', { name: '知识图谱' })).toBeVisible();
        // 桩给了 4 个节点 → 工具栏的计数文案必须体现出来，
        // 否则说明图谱数据没进页面（canvas 空转，审计等于扫了个空壳）
        await expect(page.getByText(/4 节点/)).toBeVisible();
      },
      // 实测 183
      minNodes: 130,
    });

    // ── 键盘走查（F-37 的第二半）：「关系类型（点击高亮）」图例 ──
    // 原来是 `span[onClick]`（没有 role/tabIndex）—— **键盘到不了**。
    // 这一处是本轮**新找到**的：它不在人工清单里，是 F-37 的键盘扫描
    // 在 `knowledge-graph` 场景上报出来的（"手型区域里没有可 Tab 的控件"）。
    await expectReachableByTab(
      page,
      page.getByRole('button', { name: '相关' }),
      '知识图谱：关系类型图例（点击高亮）',
    );
  });

  /** 项目页：标题、新建表单、项目卡片、说明区 */
  test('项目页', async ({ page }) => {
    const log = await installA11yStubs(page);
    await auditScene(page, log, {
      scene: 'projects',
      ready: async () => {
        await loginAs(page, '/projects');
        await expect(page.getByRole('heading', { name: '项目', exact: true })).toBeVisible();
        await expect(page.getByText('蓄电池基础')).toBeVisible();
      },
      // 实测 139
      minNodes: 100,
    });
  });

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
    const log = await installA11yStubs(page);
    await auditScene(page, log, {
      scene: 'daily-materials',
      ready: async () => {
        await loginAs(page, '/daily');
        await expect(page.getByRole('heading', { name: '今日资料' })).toBeVisible();
        // 文件夹头是 `div[role="button"]`（它里面还有真按钮 —— 见登记表）
        await page.getByRole('button', { name: /2026-01-05 学习资料/ }).click();
        // 展开后才会请求 /api/folders/folder-1：这三个标记都来自那一份数据
        await expect(page.getByText('浮充与均充的讲义.pdf')).toBeVisible();
        await expect(page.getByText('待转换：蓄电池巡检记录.docx')).toBeVisible();
        await expect(page.getByRole('button', { name: '上传文件' })).toBeVisible();
      },
      // 实测 157
      minNodes: 110,
    });

    // ── 键盘走查（F-37 的第二半）──
    // 资料行原来是 `div.card[role="button"][tabIndex=0]` + 只认 `Enter` 的
    // `onKeyDown` —— axe **报不出来**（里面没有可聚焦后代），但 Tab 会停在一个
    // "不是按钮的按钮"上，Space 也不生效。现在它是真 `<Link>`（在 `<h3>` 里）。
    await expectReachableByTab(
      page,
      page.getByRole('link', { name: '浮充与均充的讲义.pdf' }),
      '今日资料：展开后的资料行',
    );
    // 文件夹头（F-30 那一次修的）也一起守着 —— 它是这一页的第一个真控件
    await expectReachableByTab(
      page,
      page.getByRole('button', { name: /2026-01-05 学习资料/ }),
      '今日资料：文件夹头（折叠/展开）',
    );

    // ── 「改级别不改外观」：文件夹名由 h3 提升为 h2（F-31）──
    // 字号同样显式钉成 `1.17em`；文件夹里的资料名由 h4 提升为 h3（0.9rem 本来就显式写着）
    await expectFontSizeUnchanged(
      page.getByRole('heading', { name: '2026-01-05 学习资料' }),
      '今日资料「文件夹名」',
      'h3',
    );
  });

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
    });
    await auditScene(page, log, {
      scene: 'today-learn',
      ready: async () => {
        await loginAs(page, '/today');
        await expect(page.getByRole('heading', { name: '今日学习' })).toBeVisible();
        await expect(page.getByRole('heading', { name: '每日推荐任务' })).toBeVisible();
        await expect(page.getByText('复习「均充的适用场景」')).toBeVisible();
        await expect(page.getByRole('heading', { name: '今日报告 (2026-01-06)' })).toBeVisible();
        // 待复习卡片的计数来自 /api/review/stats（12）：0 的话整块换成空状态
        await expect(page.getByText('今日待复习: 12 题')).toBeVisible();
        // `exact: true`：推荐任务的类别卡片里也有一个叫「薄弱点」的 h3
        // （"薄弱点共 1 项"），不写 exact 会被 Playwright 判成 strict 冲突
        await expect(page.getByRole('heading', { name: '薄弱点', exact: true })).toBeVisible();
        await expect(page.getByText('错3次 | 0.4%')).toBeVisible();
      },
      // 实测 171
      minNodes: 120,
    });

    // ── 键盘走查（F-37 的第二半）：这一页此前有两处键盘到不了的地方 ──
    // 薄弱点每一行是 `div[onClick]`；推荐任务的每一行是
    // `div[role="button"][tabIndex=0]` + **只认 Enter 的** onKeyDown。
    await expectReachableByTab(
      page,
      page.getByRole('button', { name: '复习「均充的适用场景」' }),
      '今日学习：推荐任务里可点的那一行',
    );
    await expectReachableByTab(
      page,
      page.getByRole('link', { name: '均充的适用场景' }),
      '今日学习：薄弱点第一行',
    );

    // ── 量出三个优先级徽章（高/中/低）的真实色值与比值，并**把阈值钉成门禁** ──
    //
    // 为什么这三条**必须**单独量：axe 的 `color-contrast` 对
    // "只有一个字符"的文本不下结论（`shortTextContent`，见 measureContrast 的说明），
    // 于是它们永远进 `incomplete`、永远不会变成违规 —— `REGISTRY` 对它无能为力
    // （没有 violation 就没有可登记的条目，连"上限"都写不出来）。
    // 这一条**只有实测守得住**，所以修色值与加断言必须同时交付：
    // 否则"3.68 → 5.44"这件事没有任何一层能验证，修好与没修在报告上长得一样。
    const priorityBadges = await measureContrast(page.locator('.card').getByText(/^[高中低]$/));
    expect(
      priorityBadges.map((m) => m.text),
      '三个优先级徽章没量全，本场景的证据不成立',
    ).toEqual(['高', '中', '低']);
    for (const m of priorityBadges) {
      expect(Number.isFinite(m.ratio), `${m.target}: 对比度没算出来`).toBe(true);
      // ★ 断言的是**阈值**（`measureContrast` 按字号/字重算出的 WCAG AA 门槛，
      //   这里是 4.5:1），不是"当前值等于多少"。
      //   写成"现在是 3.68"会给下一轮修好它的人埋一个必红的测试；
      //   写成"≥ 阈值"则修好即通过、退化即失败 —— 与 REGISTRY 只卡上限同一个道理。
      expect(
        m.ratio,
        `${m.text}（优先级徽章）：实测 ${m.ratio}:1 < 要求 ${m.required}:1。` +
          `色值来自 TodayLearn.tsx 的 priorityMeta（白字压色块），` +
          `axe **结构上**判不了单字符文本，所以只有这条断言守着它。`,
      ).toBeGreaterThanOrEqual(m.required);
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
    );

    await test.info().attach('a11y-today-learn-badges.json', {
      body: JSON.stringify(
        {
          note:
            '量出来的事实 + 一条阈值门禁：axe 对单字符文本的对比度不下结论（shortTextContent），' +
            '所以这三条只有本文件的断言守着（ratio >= required，不是"等于某个值"）',
          gateLevel: 'ratio >= required（WCAG AA 4.5:1）',
          priorityBadges,
        },
        null,
        2,
      ),
      contentType: 'application/json',
    });
  });

  /** 上传页（`Upload`）：拖拽区（`role="button"` + `aria-label`）、解析方式、项目标签、笔记类型 */
  test('上传页：拖拽上传区与三个设置卡片', async ({ page }) => {
    const log = await installA11yStubs(page);
    await auditScene(page, log, {
      scene: 'upload',
      ready: async () => {
        await loginAs(page, '/upload');
        await expect(page.getByRole('heading', { name: '上传学习资料' })).toBeVisible();
        // 这一页的主控件就是那个拖拽区（本轮从 `div[role="button"]`
        // 换成**真 `<button>`**：Enter 与 Space 都生效，见下面键盘走查）
        await expect(page.getByRole('button', { name: '点击或拖拽文件上传' })).toBeVisible();
        // 项目标签来自 /api/projects：桩回空数组时这里只剩一句"暂无项目"
        await expect(page.getByRole('button', { name: '蓄电池基础' })).toBeVisible();
        await expect(page.getByRole('button', { name: '云端解析' })).toBeVisible();
      },
      // 实测 128
      minNodes: 90,
    });

    // ── 键盘走查（F-37 的第二半）：拖拽区原来是 `div[role="button"][tabIndex=0]`
    // + 只认 `Enter` 的 `onKeyDown`（axe 报不出来：里面没有可聚焦后代）。
    // 现在是真 `<button>`，所以 Enter **与 Space** 都能打开文件选择框。
    await expectReachableByTab(
      page,
      page.getByRole('button', { name: '点击或拖拽文件上传' }),
      '上传页：拖拽上传区',
    );
  });

  /**
   * 答题复习（`Review`）：选择题 + 难度徽章。
   *
   * 这一页此前**从未被扫过**（`/api/review/due` 没有桩，打开就是错误分支）：
   * 一次人工探针发现它整页没有 h1（docs/a11y-audit.md §8.3 第 1 条）。
   * 本轮把它变成场景 —— 探针结论与门禁结论的区别就在这里。
   */
  test('答题复习：选择题（含难度徽章）', async ({ page }) => {
    const log = await installA11yStubs(page);
    await auditScene(page, log, {
      scene: 'review',
      ready: async () => {
        await loginAs(page, '/review');
        await expect(page.getByText('浮充与均充的主要区别是什么？')).toBeVisible();
        await expect(
          page.getByRole('button', { name: '浮充长期恒压补偿自放电，均充短时升压校正' }),
        ).toBeVisible();
        // 复习元信息（已复习次数 / 间隔）来自 /api/review/due 的字段
        await expect(page.getByText('已复习 3 次 | 间隔 6 天')).toBeVisible();
      },
      // 实测 126
      minNodes: 85,
    });
  });

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
    const log = await installA11yStubs(page, { '/api/notes': notesList(PROCESSING_NOTES) });
    await auditScene(page, log, {
      scene: 'notes-status-processing',
      ready: async () => {
        await loginAs(page, '/notes');
        await expect(page.getByText('共 2 条')).toBeVisible();
        await expect(page.getByText('待转换：蓄电池巡检记录.docx')).toBeVisible();
        await expect(page.getByText('待清洗：浮充与均充对照表.pdf')).toBeVisible();
        // 这两个类名是**本场景存在的全部理由**：等到它们真的挂上 DOM
        await expect(page.locator('.status-converting')).toHaveCount(1);
        await expect(page.locator('.status-cleaning')).toHaveCount(1);
      },
      // 实测 145
      minNodes: 100,
    });

    // ── 把色值与比值量出来（真 Chromium 的 computed style）──
    // 断言只覆盖"量到了"：两个徽章都在、比值是有限数；值本身进附件与终端输出。
    const measurements = await measureContrast(
      page.locator('.status-converting, .status-cleaning'),
    );
    expect(
      measurements.map((m) => m.text),
      '两个状态徽章都没量到，本场景的证据不成立',
    ).toEqual(['转换中', '清洗中']);
    for (const m of measurements) {
      expect(Number.isFinite(m.ratio), `${m.target}: 对比度没算出来`).toBe(true);
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
    );

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
    });
  });

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
    const log = await installA11yStubs(page);
    await page.setViewportSize({ width: 375, height: 667 });

    const width = await page.evaluate(() => window.innerWidth);
    expect(width, '视口没被设成移动端宽度').toBe(375);

    const result = await auditScene(page, log, {
      scene: 'dashboard-mobile',
      ready: async () => {
        await loginAs(page, '/');
        await expect(page.getByRole('heading', { name: '欢迎使用 EngramNote' })).toBeVisible();
        // 移动端专属控件：桌面由 CSS 隐藏（见 App.tsx / layout.css）
        await expect(page.getByRole('button', { name: '打开菜单' })).toBeVisible();
      },
      minNodes: 140,
    });

    // 明确记录：本场景 0 条"触控尺寸"类违规 —— 不是因为尺寸没问题，
    // 而是因为 axe 没有这条规则。想守住它必须另外写几何断言（见文档 §4）。
    const touchRules = result.violations.filter((v) => /target|touch/i.test(v.rule));
    expect(touchRules, 'axe 里没有触控尺寸规则（本条断言就是在钉住这个事实）').toEqual([]);

    // 顺带量一个**真实**的触控尺寸事实，供文档 §4 的清单引用：
    // 侧边栏抽屉里的导航项高度（px）。它不进断言的上限，只进附件。
    const navBox = await page
      .getByRole('button', { name: /仪表盘/ })
      .first()
      .boundingBox();
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
    });
  });

  // ────────────────────────────────────────────────────────────────────────
  // 覆盖轮（第二轮）：把最后 10 个页面接进来
  //
  // 这一批与上一批的**不同点**：上一批是"把已经从没渲染过的状态渲染出来"，
  // 这一批是"把从没有过桩的页面接上" —— 每个页面都要先有**契约形状**的桩，
  // 否则扫到的是加载失败页（那正是 `CardDetail` 一直没被接进来的原因，
  // 而真实原因不是文档当时猜的"桩是列表形状"，是**路径写错了**：
  // 应用请求 `/api/understanding/cards/{id}`，桩里写的却是 `/api/cards`）。
  //
  // 每个场景都遵守同一套防线（见文件头与 auditScene）：
  //   ① ready 等的是"该页**独有**的标记 **+ 真实内容**"（不是通用标题）；
  //   ② DOM 元素数 ≥ minNodes（取实测值留约 30% 余量，实测值写在注释里）；
  //   ③ `passes > 0` 且评估规则数 > 20（证明 axe 真的跑了）；
  //   ④ `log.unmatched === []`（没有靠"未定义的接口静默 501"混过去）；
  //   ⑤ `log.pageErrors === []`（页面没有未捕获异常）。
  //
  // ⚠️ 凡是"主内容要靠交互才出现"的页面，都**必须真的走那一步交互**：
  // `KnowledgeCards` 的分组默认折叠、`QA` 不问就是空状态、
  // `QuestionSets` 的答案默认折叠、`LearningAssessment` 的关联资料要选中笔记
  // —— 只扫初始态等于扫了个空壳（空白页面的 axe 结果恒为 0 违规）。
  // ────────────────────────────────────────────────────────────────────────

  /**
   * 智能问答（`QA`）：一次**真实的流式提问**之后的回答 + 引用来源。
   *
   * 为什么必须问一次：这一页挂载时**一个请求都不发**（只有一个输入框 +
   * 空状态 `输入问题开始问答`），主内容只有提问之后才存在。
   * 桩要按 `text/event-stream` 回（见 `rawBody` / `QA_ANSWER_SSE`），
   * 因为按 JSON 回的话页面会永远停在 `AI 正在思考...` —— 而那一句
   * **既是加载态、也是"答案为空"的终态**（QA.tsx:248/267），拿它当
   * "已经渲染出来了"的判据是假的。
   */
  test('智能问答：一次真实提问后的回答与引用来源', async ({ page }) => {
    const log = await installA11yStubs(page);
    await auditScene(page, log, {
      scene: 'qa',
      ready: async () => {
        await loginAs(page, '/qa');
        await expect(page.getByRole('heading', { name: '智能问答' })).toBeVisible();
        await page
          .getByPlaceholder('输入你的问题，AI 将基于你的笔记内容回答...')
          .fill('浮充和均充有什么区别？');
        await page.getByRole('button', { name: '提问' }).click();
        // **流结束**的标记是引用来源与页脚，不是"AI 正在思考..."（见上）
        await expect(page.getByText('引用来源:')).toBeVisible();
        await expect(
          page.getByText('浮充是长期恒压运行，用于补偿自放电；均充是短时升压校正。'),
        ).toBeVisible();
        await expect(page.getByText('由 DeepSeek 提供支持')).toBeVisible();
      },
      // 实测 121
      minNodes: 90,
    });

    // ── 键盘走查（F-37 的第二半）：引用来源那一行原来是 `div[onClick]` ──
    // （没有 role/tabIndex，键盘到不了），现在是真 `<Link>`。
    // 它同时是 `link-in-text-block` 的判据现场：下划线**刻意保留**（全局默认）。
    await expectReachableByTab(
      page,
      page.getByRole('link', { name: /引用|锂离子电池的浮充与均充/ }).first(),
      '智能问答：引用来源那一行',
    );
  });

  /**
   * 知识卡片（`KnowledgeCards`）：按来源笔记分组 + 卡片单元。
   *
   * ⚠️ 分组**加载完成后默认是展开的**（`fetchCards` 里
   * `setExpandedNotes(new Set(grouped.map(...)))` —— 全部展开），
   * 所以这里刻意**不点**那个分组头：点一下反而会把它收起来。
   * （分组头此前是 `div[onClick]`、没有 role/tabIndex，**键盘到不了** ——
   * 那一类 axe 判不了，本轮已换成 `<h2>` 里的真 `<button aria-expanded>`，
   * 并由下面的 `expectReachableByTab` 真的按 Tab 守着。）
   */
  test('知识卡片：按笔记分组与卡片单元', async ({ page }) => {
    const log = await installA11yStubs(page);
    await auditScene(page, log, {
      scene: 'knowledge-cards',
      ready: async () => {
        await loginAs(page, '/cards');
        await expect(page.getByRole('heading', { name: '知识卡片' })).toBeVisible();
        await expect(page.getByText('(4 张卡片)')).toBeVisible();
        // 四张卡片真实渲染：标题、掌握度、以及 ≥80 分那张多出来的金色提示
        await expect(page.getByRole('heading', { name: '浮充的定义' })).toBeVisible();
        await expect(page.getByRole('heading', { name: '硫化的定义' })).toBeVisible();
        await expect(page.getByText('掌握度').first()).toBeVisible();
        // 两张卡片都写了同一个章节名 → 用 first()（严格模式会报 2 个元素）
        await expect(page.getByText('章节: 第一章 蓄电池').first()).toBeVisible();
        await expect(page.getByText('✨ 建议生成拓展知识点')).toBeVisible();
      },
      // 实测 191
      minNodes: 135,
    });
    // ── 键盘走查（F-37 的第二半）：这一页此前有**三处**键盘到不了的地方 ──
    // 分组头是 `div[onClick]`、卡片本体是 `div[onClick]`、
    // 「✨ 建议生成拓展知识点」是 `div[onClick]`；axe 三处全绿。
    await expectReachableByTab(
      page,
      page.getByRole('button', { name: '锂离子电池的浮充与均充' }),
      '知识卡片：分组头（折叠/展开）',
    );
    await expectReachableByTab(
      page,
      page.getByRole('link', { name: '浮充的定义' }),
      '知识卡片：卡片标题链接',
    );
    await expectReachableByTab(
      page,
      page.getByRole('button', { name: '✨ 建议生成拓展知识点' }),
      '知识卡片：「建议生成拓展知识点」',
    );
  });

  /**
   * 卡片详情（`CardDetail`）：标题 + 来源笔记 + 章节摘要 + 原始出处 + 关联题目。
   *
   * 这一页此前**被明确判定为"不能扫"**（审计文档 §5 第 3 条），理由是
   * "桩里 `/api/cards/{id}` 返回的是列表形状，加进去只会得到加载失败页"。
   * 本轮查清了真实原因：**应用请求的路径是 `/api/understanding/cards/{id}`**
   * （api/qa.ts:186），桩里那条 `/api/cards` 是一条**永远匹配不到的死键**，
   * 请求落到 501 上才渲染成加载失败页。补上正确路径的桩之后这一页就能扫了。
   *
   * 「显示答案」也要真的点开：关联题目的答案与解析默认不渲染，
   * 而答案那行的颜色（`var(--color-success)`）是**只有点开才存在**的 DOM。
   */
  test('卡片详情：章节摘要 / 原始出处 / 关联题目', async ({ page }) => {
    const log = await installA11yStubs(page);
    await auditScene(page, log, {
      scene: 'card-detail',
      ready: async () => {
        await loginAs(page, '/cards/card-1');
        await expect(page.getByRole('heading', { name: '浮充的定义' })).toBeVisible();
        await expect(page.getByText('来源笔记：')).toBeVisible();
        await expect(page.getByRole('heading', { name: '章节摘要' })).toBeVisible();
        await expect(page.getByRole('heading', { name: '原始出处' })).toBeVisible();
        await expect(page.getByRole('heading', { name: '关联题目 (1)' })).toBeVisible();
        await page.getByRole('button', { name: '显示答案' }).click();
        await expect(
          page.getByText('答案: 浮充长期恒压补偿自放电，均充短时升压校正'),
        ).toBeVisible();
      },
      // 实测 139
      minNodes: 100,
    });

    // ── 键盘走查（F-37 的第二半）：「来源笔记」原来是 `span[onClick]` ──
    // （没有 role/tabIndex，键盘到不了），现在是真 `<Link>`；它就在一行文字里
    // （「来源笔记：<标题>」），所以下划线**不能**关掉 —— `link-in-text-block`
    // 正是判它（这一页的场景因此同时守着那条规则）。
    await expectReachableByTab(
      page,
      page.getByRole('link', { name: '锂离子电池的浮充与均充' }),
      '卡片详情：「来源笔记」链接',
    );
  });

  /**
   * 学习评估（`LearningAssessment`）：默认的"已链接对比"模式。
   *
   * 两处必须喂对：
   *   - `/api/notes` 要**同时**含 material 与 personal_note（页面在客户端
   *     按 `note_role` 再分一次），且 `status` 必须在可评估状态里；
   *   - `/api/notes/note-personal/links` 的 `linked_materials` 必须非空 ——
   *     它是**运行时必需**字段，缺了这条笔记会被静默丢掉、页面显示
   *     "暂无已链接的笔记"（空状态，不是被审过的页面）。
   *
   * 选中一张笔记之后才会渲染"将比对以下资料与该笔记"，所以那一步要真的点。
   */
  test('学习评估：已链接对比模式（选中笔记与关联资料）', async ({ page }) => {
    const log = await installA11yStubs(page, { '/api/notes': ASSESSMENT_NOTES });
    await auditScene(page, log, {
      scene: 'learning-assessment',
      ready: async () => {
        await loginAs(page, '/assessment');
        await expect(page.getByRole('heading', { name: '学习评估' })).toBeVisible();
        await expect(page.getByRole('heading', { name: '选择笔记' })).toBeVisible();
        // 「关联资料: — 篇」是**有数据才有**的标记（空状态时整块不渲染）
        await expect(page.getByText('关联资料: — 篇')).toBeVisible();
        // 选中的是**真控件**（本轮之前是 `div[onClick]`，键盘到不了）：
        // 点击的位置从"整张卡片"变成"标题按钮"
        await page.getByRole('button', { name: '复盘：浮充的三个月' }).click();
        await expect(page.getByText('将比对以下资料与该笔记：')).toBeVisible();
        await expect(page.getByText('• 锂离子电池的浮充与均充')).toBeVisible();
        await expect(page.getByRole('button', { name: '开始评估' })).toBeVisible();
      },
      // 实测 126
      minNodes: 90,
    });

    // ── 键盘走查（F-37 的第二半）：笔记选择卡片原来是 `div[onClick]` ──
    await expectReachableByTab(
      page,
      page.getByRole('button', { name: '复盘：浮充的三个月' }),
      '学习评估：笔记选择卡片',
    );
  });

  /**
   * 学习目标（`LearningGoals`）：进行中的目标 + 展开后的已归档目标。
   *
   * ⚠️ 这里的桩是**带查询串**的两份（`GOAL_STUBS`）：页面用同一个路径
   * `/api/goals?status=active` 与 `?status=archived` 取两份数据。
   * 只按路径名匹配时"已归档目标 (1)"里显示的其实是**进行中**的那个目标 ——
   * 页面照常渲染、断言照常绿，而那一块从未被真正判过（见 fixtures 的说明）。
   */
  test('学习目标：进行中的目标与展开后的已归档目标', async ({ page }) => {
    const log = await installA11yStubs(page, GOAL_STUBS);
    await auditScene(page, log, {
      scene: 'learning-goals',
      ready: async () => {
        await loginAs(page, '/goals');
        await expect(page.getByRole('heading', { name: '学习目标' })).toBeVisible();
        await expect(page.getByRole('heading', { name: '进行中的目标' })).toBeVisible();
        await expect(page.getByRole('heading', { name: '掌握蓄电池基础概念' })).toBeVisible();
        await expect(page.getByText('目标 80%')).toBeVisible();
        await page.getByRole('button', { name: '展开已归档目标 (1)' }).click();
        await expect(page.getByText('读完《蓄电池维护手册》')).toBeVisible();
        await expect(page.getByText('每周 · 目标 60%')).toBeVisible();
      },
      // 实测 138
      minNodes: 100,
    });
  });

  /**
   * 学习目标：**新建目标弹窗**（`LearningGoals` 的第二个渲染分支）。
   *
   * ## 为什么必须单独一条场景（而不是"顺手修一下"）
   *
   * 弹窗只在 `showCreateForm === true` 时渲染 —— **不点「新建目标」就等于没扫过它**
   * （没有渲染出来的 DOM，axe 的结果恒为 0 违规）。这一处此前**没有任何一层看得见**：
   * `role="dialog"` / `aria-modal` / Esc / 四个 `<label>` 的 `htmlFor` 全都没有，
   * 而 axe 的 `label` 规则**只有在弹窗被渲染出来时才会报**。
   * 本轮先补这条场景、再改代码，就是为了让"改好了"这件事有证据
   * （口径与 BG.11.1 一致：**先把信号拿到，再动代码**）。
   *
   * ## 这条用例断言的三件事（axe 能判两件，第三件只能靠实测）
   *
   * 1. **axe 扫弹窗渲染态** —— `label`（四个控件都要有可访问名）与
   *    `aria-dialog-name`（对话框要有名字）都会在这里被判；
   * 2. **结构** —— `role="dialog"` + `aria-modal="true"` + 可访问名来自标题
   *    （`aria-labelledby`）。`aria-modal` **没有任何 axe 规则会检查**：
   *    "该不该是模态"是产品语义，不是 DOM 合法性；
   * 3. **Esc 退出** —— 此前完全没有键盘退路（只有"点遮罩"和"点取消"）。
   */
  test('学习目标：新建目标弹窗（role/aria-modal/Esc/htmlFor）', async ({ page }) => {
    const log = await installA11yStubs(page, GOAL_STUBS);
    await auditScene(page, log, {
      scene: 'learning-goals-create',
      ready: async () => {
        await loginAs(page, '/goals');
        await page.getByRole('button', { name: '新建目标' }).click();
        await expect(page.getByRole('dialog')).toBeVisible();
        // 四个控件都真的拿到了名字 —— `getByLabel` 走的就是"标签与控件的关联"
        // （aria-label / aria-labelledby / label[for] / 包裹式 label），
        // 所以这四行**就是** `htmlFor`/`id` 生效的证据。
        await expect(page.getByLabel('目标名称')).toBeVisible();
        await expect(page.getByLabel('目标类型')).toBeVisible();
        await expect(page.getByLabel('目标掌握度 (%)')).toBeVisible();
        await expect(page.getByLabel('截止日期（可选）')).toBeVisible();
      },
      // 实测 151（列表页 138 + 弹窗的十来个元素）
      minNodes: 105,
    });

    // ── 结构：role / aria-modal / 可访问名（axe 只判得到最后一项）──
    const dialog = page.getByRole('dialog');
    await expect(dialog, '弹窗必须是 aria-modal：底下的内容此刻不参与交互').toHaveAttribute(
      'aria-modal',
      'true',
    );
    await expect(dialog, '对话框必须有可访问名，且名字来自它自己的标题').toHaveAccessibleName(
      '新建学习目标',
    );

    // ── Esc 关掉弹窗（此前没有键盘退路）──
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog'), 'Esc 没有关掉弹窗').toHaveCount(0);
    // 关掉之后回到列表态（「新建目标」按钮又在了），而不是把整页也带走
    await expect(page.getByRole('button', { name: '新建目标' })).toBeVisible();
  });

  /** 回收站（`Trash`）：非空的已删除笔记（标题 + 删除时间 + 五项附属统计 + 恢复/彻底删除） */
  test('回收站：一条已删除的笔记', async ({ page }) => {
    const log = await installA11yStubs(page);
    await auditScene(page, log, {
      scene: 'trash',
      ready: async () => {
        await loginAs(page, '/trash');
        await expect(page.getByRole('heading', { name: '回收站' })).toBeVisible();
        await expect(
          page.getByRole('heading', { name: '已删除：蓄电池寿命与温度的关系' }),
        ).toBeVisible();
        await expect(page.getByText('删除于')).toBeVisible();
        await expect(page.getByText('4 张卡片')).toBeVisible();
        // 「清空回收站」只在 items 非空时渲染 —— 它同时是"列表真的有内容"的标记
        await expect(page.getByRole('button', { name: '清空回收站' })).toBeVisible();
        await expect(page.getByRole('button', { name: '恢复' })).toBeVisible();
        await expect(page.getByRole('button', { name: '彻底删除' })).toBeVisible();
      },
      // 实测 126
      minNodes: 90,
    });
  });

  /**
   * 快速复习（`QuickReview`）：`/review/quick/:noteId` 的答题态。
   *
   * ★ 这一页是审计文档里那条**推断**的验证：「`QuickReview` 与 `QA` 共用
   * `QuizAnswerCard`，所以 F-33（难度徽章 `#c9a959` 2.25:1）大概率也在那里」。
   * 实测结论是**一半对**：`QuickReview` 确实共用 `QuizAnswerCard`
   * （难度徽章就在这里，本场景直接判它）；而 `QA` 是 SSE 聊天页，
   * 根本不渲染那个组件。桩里的 `difficulty` 刻意用 `medium`
   * —— 就是 F-33 报出来的那一个取值。
   */
  test('快速复习：共用答题卡片的答题态', async ({ page }) => {
    const log = await installA11yStubs(page);
    await auditScene(page, log, {
      scene: 'quick-review',
      ready: async () => {
        await loginAs(page, '/review/quick/note-1');
        await expect(page.getByRole('heading', { name: '快速复习' })).toBeVisible();
        await expect(page.getByText('浮充与均充的主要区别是什么？')).toBeVisible();
        // 难度徽章（白字压 difficultyColors.medium）与题型徽章
        await expect(page.getByText('中等')).toBeVisible();
        await expect(page.getByText('选择题')).toBeVisible();
        await expect(
          page.getByRole('button', { name: '浮充长期恒压补偿自放电，均充短时升压校正' }),
        ).toBeVisible();
        await expect(page.getByRole('button', { name: '返回笔记' })).toBeVisible();
      },
      // 实测 129
      minNodes: 95,
    });
  });

  /**
   * 考试/问题集（`QuestionSets`）：按笔记分组的题目 + 展开后的答案。
   *
   * ⚠️ 桩的 `total` 必须等于 `items.length`：这一页是**翻页循环**
   * （`QuestionSets.tsx:82-94`，直到 `items.length === 0` 或收满 `total`），
   * `total` 写大了它会一直请求到 100 页的硬上限，审计会慢得莫名其妙。
   *
   * 「显示答案」要真的点开：答案那一行的绿色文字（原值 `#10b981`，白底 2.54:1）
   * **只有点开才渲染** —— 不点开就等于没判过它。
   */
  test('问题集：按笔记分组的题目与展开后的答案', async ({ page }) => {
    const log = await installA11yStubs(page);
    await auditScene(page, log, {
      scene: 'question-sets',
      ready: async () => {
        await loginAs(page, '/questions');
        await expect(page.getByRole('heading', { name: '问题集' })).toBeVisible();
        await expect(page.getByText('(2 道题)')).toBeVisible();
        await expect(page.getByText('浮充与均充的主要区别是什么？')).toBeVisible();
        await expect(page.getByRole('button', { name: '查看笔记' })).toBeVisible();
        await page.getByRole('button', { name: '显示答案' }).first().click();
        await expect(
          page.getByText('答案：浮充长期恒压补偿自放电，均充短时升压校正'),
        ).toBeVisible();
      },
      // 实测 158
      minNodes: 115,
    });

    // ── 键盘走查（F-37 的第二半）：分组头原来是 `div[onClick]` ──
    // （没有 role/tabIndex），里面还嵌着「查看笔记」真按钮；
    // 现在是真 `<button aria-expanded>`，与「查看笔记」互为**兄弟**。
    await expectReachableByTab(
      page,
      page.getByRole('button', { name: '锂离子电池的浮充与均充' }),
      '问题集：分组头（折叠/展开）',
    );
  });

  /**
   * 注册页（`Register`）：**未登录**分支的第二个入口（登录页之外的唯一未认证页面）。
   *
   * 这一页**不能**先登录：`App.tsx` 在已登录分支把 `/register` 重定向到 `/`，
   * 于是"注册页场景"会静默变成"仪表盘场景"—— 那种绿是假绿。
   * 所以这里不走 `loginAs`，直接 `goto`（页面挂载时也不发任何请求）。
   */
  test('注册页：未登录入口', async ({ page }) => {
    const log = await installA11yStubs(page);
    await auditScene(page, log, {
      scene: 'register',
      ready: async () => {
        await page.goto('/register', { waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('heading', { name: '注册 EngramNote' })).toBeVisible();
        await expect(page.getByLabel('邮箱')).toBeVisible();
        await expect(page.getByLabel('用户名')).toBeVisible();
        await expect(page.getByLabel('密码')).toBeVisible();
        await expect(page.getByRole('button', { name: '注册' })).toBeVisible();
        // 反向自检：**没有**登录（侧边栏不在），否则扫的就不是这一页
        await expect(page.getByRole('navigation', { name: '主导航' })).toHaveCount(0);
      },
      // 实测 56（未登录，没有侧边栏 —— 这一页本来就小）
      minNodes: 40,
    });
  });

  /**
   * 404 页（`App.tsx` 的 `path="*"`）。
   *
   * ⚠️ 它**只在已登录时**才渲染：未登录时 `path="*"` 落到 `<Login />`
   * （那是产品行为，不是缺陷）。所以这一条必须先登录，再走一个不存在的路径；
   * 否则扫到的是登录页，而"0 违规"会显得像 404 页没问题。
   */
  test('404 页：已登录时的未知路径', async ({ page }) => {
    const log = await installA11yStubs(page);
    await auditScene(page, log, {
      scene: 'not-found',
      ready: async () => {
        await loginAs(page, '/no-such-page');
        await expect(page.getByRole('heading', { name: '404' })).toBeVisible();
        await expect(page.getByText('页面不存在，可能是链接已失效。')).toBeVisible();
        await expect(page.getByRole('link', { name: '返回首页' })).toBeVisible();
      },
      // 实测 109
      minNodes: 85,
    });
  });
});

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
    const log = await installA11yStubs(page);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: '登录 EngramNote' })).toBeVisible();

    const injected = await page.evaluate(() => {
      const img = document.createElement('img');
      // 没有 alt 的 img：`image-alt` 是 axe 里最稳定的一条违规。
      // 用 1x1 的 data URI 而不是站点内的图片：本自检验的是 axe 的判定链路，
      // 不该依赖任何网络请求成不成功（请求失败也照样是"没有 alt 的 img"，
      // 但那样就多了一个与被测性质无关的变量）。
      img.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
      img.id = 'a11y-self-check';
      document.body.appendChild(img);
      return document.querySelectorAll('#a11y-self-check').length;
    });
    expect(injected, '自检元素没能注入 DOM').toBe(1);

    const results = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
    const ids = results.violations.map((v) => v.id);
    expect(ids, 'axe 没有报出注入的 image-alt —— 扫描链路本身失效了').toContain('image-alt');

    await page.evaluate(() => document.querySelector('#a11y-self-check')?.remove());
    expect(log.pageErrors).toEqual([]);
  });

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
      // ── 覆盖轮（第二轮）加进来的 10 个场景 ──
      // 场景名写错的后果见上：豁免永远匹配不到，而违规照旧出现。
      'qa',
      'knowledge-cards',
      'card-detail',
      'learning-assessment',
      'learning-goals',
      // ── Part A/B 收尾轮加进来的第 26 个场景 ──
      // `LearningGoals` 的**弹窗**是另一个渲染分支（只在点开时存在），
      // 此前没有任何一层看得见它：role/aria-modal/Esc/htmlFor 四件事都没做。
      'learning-goals-create',
      'trash',
      'quick-review',
      'question-sets',
      'register',
      'not-found',
    ]);

    const unknown = ACTIVE_REGISTRY.filter((entry) => !knownScenes.has(entry.scene));
    expect(
      unknown.map((entry) => `${entry.id}:${entry.scene}`),
      '登记表里有未知场景名',
    ).toEqual([]);

    const pairs = ACTIVE_REGISTRY.map((entry) => `${entry.scene}/${entry.rule}`);
    const duplicates = pairs.filter((pair, index) => pairs.indexOf(pair) !== index);
    expect(duplicates, '登记表里有重复的（场景，规则）组合').toEqual([]);

    // 每条豁免都必须写明归属，否则它就不是"已接受的风险"而是"忘了修"
    const noOwner = ACTIVE_REGISTRY.filter((entry) => !entry.reason.includes('归属：'));
    expect(
      noOwner.map((entry) => entry.id),
      '登记表里有没写归属的条目',
    ).toEqual([]);
  });
});
