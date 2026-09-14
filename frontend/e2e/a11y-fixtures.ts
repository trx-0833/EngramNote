/**
 * 可访问性审计（overhaul-plan 5.9）用的 `/api` 桩
 *
 * ## 为什么不复用 `support.ts` 的 `stubApi`
 *
 * 那个桩是给"登录链路"用的，所有列表接口一律回**空**。空列表在 a11y 审计里
 * 是最坏的一种桩：页面只剩空状态，卡片、标签、图例、分页、进度条这些
 * **真正会被 axe 判定的 DOM** 一个都不存在 —— 扫出来"0 违规"，
 * 而这 0 是"没东西可查"，不是"页面没问题"。
 *
 * 所以这里另外给每个被审页面喂**非空**的业务数据（笔记、卡片、图谱节点、项目），
 * 让审计跑在"有内容"的渲染结果上。
 *
 * ## 为什么未匹配的请求要显式失败
 *
 * 审计的价值取决于"扫的到底是哪个页面"。如果未知接口被静默兜成空响应，
 * 页面可能停在骨架/错误态，而测试照样绿 —— 那正是本仓库反复踩到的
 * "配置了却从未真正执行"。这里的策略是：**未知路径一律 501 + 明确文案**，
 * 页面上会出现可见的错误提示，同时测试输出里能看到这条 URL。
 * 迁移接口后忘记补桩，会以"页面渲染不出来"的形式暴露，而不是静默降级。
 *
 * ## 为什么桩里写的是"前端期望的形状"
 *
 * 与 `support.ts` 一样：本层证明的是**前端在真实浏览器里的渲染结果**，
 * 不证明后端会这样响应。接口契约由 `backend/tests/` 负责。
 */
import type { Page } from '@playwright/test'

import { blockThirdParty, collectPageErrors, isApiUrl } from './support'

// ── 固定的时间戳：让渲染结果与"今天"无关，审计可重复 ──
const T0 = '2026-01-05T08:00:00Z'
const T1 = '2026-01-06T09:30:00Z'

/** 笔记列表里的一条（`GET /notes`） */
function note(over: Record<string, unknown> = {}) {
  return {
    id: 'note-1',
    user_id: 'e2e-user',
    title: '锂离子电池的浮充与均充',
    source_type: 'pdf',
    note_role: 'material',
    project_ids: ['proj-1'],
    project_names: ['蓄电池基础'],
    status: 'cleaned',
    file_size: 524_288,
    page_count: 12,
    error_message: null,
    trashed_at: null,
    created_at: T0,
    updated_at: T1,
    ...over,
  }
}

/** 笔记详情（`GET /notes/{id}`） */
function noteDetail() {
  const clean = [
    '# 浮充与均充',
    '',
    '浮充是蓄电池的一种**长期恒压**运行方式，用于补偿自放电。',
    '均充则是**短时提高电压**的补充充电，用于消除硫化。',
    '',
    '## 两者的区别',
    '',
    '- 电压：浮充低于均充',
    '- 时长：浮充长期，均充数小时',
    '',
    '详见 [相关说明](https://example.com/battery) 与 `float_voltage` 参数。',
    '',
  ].join('\n')
  return {
    ...note(),
    original_md_content: `# 原始内容\n\n${clean}`,
    clean_md_content: clean,
    metadata_: null,
    original_file_path: '/u/note-1.pdf',
    original_md_path: '/u/note-1.md',
    clean_md_path: '/u/note-1.clean.md',
  }
}

/** 到期卡片（`GET /review/cards/due`） */
const DUE_CARDS = [
  {
    card_id: 'card-1',
    title: '浮充的定义',
    content: '蓄电池的一种运行方式，端电压保持恒定。',
    summary: '浮充 = 恒压运行',
    card_type: 'concept',
    chapter_title: '第一章 蓄电池',
    note_id: 'note-1',
    mastery_level: 57.4,
    interval_days: 6,
    repetition: 2,
    easiness_factor: 2.5,
    next_review_at: null,
    review_count: 3,
    lapses: 1,
  },
  {
    card_id: 'card-2',
    title: '均充的适用场景',
    content: '长期浮充后单体电压偏差变大时，需要短时均充校正。',
    summary: '均充 = 校正电压偏差',
    card_type: 'qa',
    chapter_title: '第一章 蓄电池',
    note_id: 'note-1',
    mastery_level: 31.2,
    interval_days: 2,
    repetition: 1,
    easiness_factor: 2.36,
    next_review_at: null,
    review_count: 1,
    lapses: 0,
  },
]

/** 图谱节点（`GET /graph`）—— 有节点才画得出 SVG/canvas，也才有工具栏与统计 */
const GRAPH_NODES = [
  { id: 'c-1', title: '浮充的定义', card_type: 'concept', note_id: 'note-1', relation_count: 2 },
  { id: 'c-2', title: '均充的定义', card_type: 'concept', note_id: 'note-1', relation_count: 1 },
  { id: 'c-3', title: '浮充电压公式', card_type: 'formula', note_id: 'note-1', relation_count: 1 },
  { id: 'c-4', title: '浮充与均充的区别', card_type: 'qa', note_id: 'note-1', relation_count: 0 },
]

const GRAPH_EDGES = [
  {
    id: 'e-1',
    source: 'c-1',
    target: 'c-2',
    relation_type: 'related',
    status: 'suggested',
    similarity_score: 0.82,
  },
  {
    id: 'e-2',
    source: 'c-1',
    target: 'c-3',
    relation_type: 'prerequisite',
    status: 'confirmed',
    similarity_score: 0.55,
  },
]

/** 项目（`GET /projects`）—— 后端这个接口回的是**裸数组**（见 api/projects.ts） */
const PROJECTS = [
  {
    id: 'proj-1',
    user_id: 'e2e-user',
    name: '蓄电池基础',
    description: '浮充、均充与寿命相关资料的合集',
    note_count: 3,
    created_at: T0,
    updated_at: T1,
  },
]

/**
 * 审计用到的 `/api` 桩：按**路径**匹配，忽略查询串
 * （判据与 `support.ts` 一致：`url.pathname` 以 `/api/` 开头，绝不用子串）。
 *
 * 忽略查询串是有意的：本层的目的是"让页面拿到足够的数据渲染出可审计的 DOM"，
 * 分页/筛选参数的具体取值不是审计对象（例如 `/api/notes?page=1&page_size=20`
 * 与 `/api/notes?keyword=x` 都该拿到同一份列表）。若哪天某个页面**只**在
 * 特定查询串下才渲染出关键 DOM，那属于该页面自己的测试要覆盖的事。
 */
function apiFixtures(): Record<string, unknown> {
  return {
    // ── 认证 ──
    '/api/auth/login': {
      access_token: 'e2e-access-token',
      refresh_token: 'e2e-refresh-token',
      token_type: 'bearer',
      user: {
        id: 'e2e-user',
        email: 'e2e@example.com',
        username: 'e2e',
        is_active: true,
        created_at: T0,
      },
    },
    '/api/auth/reminder-settings': { email_reminder_enabled: false },

    // ── 仪表盘 ──
    '/api/goals': { goals: [], total: 0 },
    // 无活跃目标时后端回 400，前端当作正常业务状态（见 Dashboard.tsx）
    '/api/goals/daily-plan': { __status: 400, detail: 'No active goals' },
    '/api/review/stats': { due_count: 12, today_done: 7, today_accuracy: 0.86, daily_limit: 50 },
    '/api/report/daily': {
      date: '2026-01-06',
      new_mastered: 4,
      total_review_time_ms: 512_000,
      total_reviews: 18,
      today_accuracy: 0.86,
      weak_point_count: 2,
      question_type_accuracy: [{ question_type: 'choice', accuracy: 0.9, total: 10 }],
    },
    '/api/report/weekly-trend': {
      items: [
        { date: '2026-01-02', review_count: 12, accuracy: 0.8 },
        { date: '2026-01-03', review_count: 18, accuracy: 0.86 },
        { date: '2026-01-04', review_count: 9, accuracy: 0.72 },
      ],
      total_reviews: 39,
      avg_accuracy: 0.79,
    },
    '/api/report/weak-points': {
      items: [
        { card_id: 'c-2', title: '均充的适用场景', accuracy: 0.4, review_count: 5 },
        { card_id: 'c-3', title: '浮充电压公式', accuracy: 0.5, review_count: 4 },
      ],
      total: 2,
    },

    // ── 笔记 ──
    '/api/notes': {
      items: [
        note(),
        note({
          id: 'note-2',
          title: '铅酸电池的硫化机理',
          source_type: 'docx',
          status: 'converted',
          page_count: null,
          project_names: [],
        }),
      ],
      total: 2,
      page: 1,
      page_size: 20,
    },
    '/api/notes/note-1': noteDetail(),
    '/api/notes/note-1/annotations': [],
    '/api/notes/note-1/links': {
      linked_materials: [],
      linked_personal_notes: [],
      dangling_links: [],
    },
    '/api/notes/note-1/versions': { items: [], total: 0 },
    // 空列表形状要给对：给错形状会让页面渲染成**错误态**，
    // 而审计要的是正常态（错误态是另一件事，见 docs/a11y-audit.md §5）。
    '/api/cards': { items: [], total: 0, page: 1, page_size: 20 },
    '/api/questions': { items: [], total: 0, page: 1, page_size: 20 },
    '/api/tasks': { items: [], total: 0 },

    // ── 卡片复习 ──
    '/api/review/cards/due': { items: DUE_CARDS, total: DUE_CARDS.length },
    '/api/review/cards/card-1/submit': {
      card_id: 'card-1',
      quality: 4,
      is_correct: true,
      interval_days: 21,
      repetition: 3,
      easiness_factor: 2.46,
      next_review_at: '2026-01-27T04:00:00+00:00',
      mastery_level: 63.1,
      stability: 21.4,
      difficulty: 5.27,
      predicted_retention: 0.62,
    },

    // ── 知识图谱 ──
    '/api/graph': { nodes: GRAPH_NODES, edges: GRAPH_EDGES },
    '/api/graph/suggestions': [],
    '/api/graph/stats': {
      total_nodes: 4,
      total_edges: 2,
      confirmed_edges: 1,
      suggested_edges: 1,
      relation_type_distribution: [
        { relation_type: 'related', count: 1 },
        { relation_type: 'prerequisite', count: 1 },
      ],
      isolated_nodes: 1,
    },

    // ── 项目 ──
    '/api/projects': PROJECTS,
    '/api/projects/proj-1': { ...PROJECTS[0], notes: [] },
    '/api/folders': [],
  }
}

/** 审计期间观察到的网络事实（按引用读取，随页面活动增长） */
export interface A11yStubLog {
  /** 页面未捕获异常（`pageerror`） */
  pageErrors: string[]
  /**
   * 桩没有定义、因而被回 501 的 `/api` 路径。
   *
   * 为什么必须收集：这些请求在页面里**大概率被 `catch` 吞掉**
   * （前端对统计类接口普遍写 `.catch(() => null)`），页面会带着"部分数据为空"
   * 继续渲染 —— 审计照样绿，但扫的不是完整页面。把它断言为空，
   * 才能保证"审计覆盖了这些区域"这句话是真的。
   */
  unmatched: string[]
  /** 桩处理过的每个 `/api` 请求（路径 + 回的状态码），用于排查"页面卡在加载中" */
  served: string[]
  /** 浏览器控制台的 error/warning（排查渲染卡住时的第一手线索） */
  consoleErrors: string[]
}

/**
 * 排查开关：默认关闭，需要时把下面的 `false` 改成 `true` 再跑一次
 * （`npm run a11y`），桩处理的每个请求会打到测试输出。
 *
 * 只在**排查"页面卡在加载中"**时用：它直接回答"请求有没有到桩、回的哪个接口"，
 * 比从页面症状反推快得多（本项目真的在 8 worker 并发下卡过一次）。
 *
 * 为什么不做成环境变量：`tsconfig.json` 的 `include` 里有 `e2e`，而
 * `lib` 只有 ES2020 + DOM —— 没有 `@types/node`，写 `process` 会让
 * `npm run build`（= `tsc`）报 TS2580。为一行排查代码去加一个类型依赖不划算。
 */
const TRACE = false

/**
 * 装好审计需要的全部桩：第三方外链 abort + `/api` 覆写。
 *
 * 必须在 `page.goto` **之前**调用（外链的首个请求已经发出去就拦不住了，
 * 理由见 `support.ts` 文件头）。
 *
 * @param overrides - 按路径覆盖个别响应。审计**页面状态**（例如"登录失败态"）
 *   时需要它：那些状态由某个接口的特定响应触发，而默认桩为了能进到应用里，
 *   给的都是成功响应。覆盖只作用于本次调用，不会污染其他场景。
 */
export async function installA11yStubs(
  page: Page,
  overrides: Record<string, unknown> = {},
): Promise<A11yStubLog> {
  const log: A11yStubLog = {
    pageErrors: collectPageErrors(page),
    unmatched: [],
    served: [],
    consoleErrors: [],
  }
  const fixtures = { ...apiFixtures(), ...overrides }

  page.on('console', (message) => {
    if (message.type() === 'error' || message.type() === 'warning') {
      log.consoleErrors.push(`[${message.type()}] ${message.text()}`)
    }
  })

  // 外链一律 abort：`index.html` 的 Google Fonts 会阻塞首屏渲染，
  // 断网时表现为"元素一直不出来"，与真实原因无关（support.ts 文件头）
  await blockThirdParty(page)

  await page.route(
    (url) => isApiUrl(url),
    async (route) => {
      const { pathname } = new URL(route.request().url())
      const body = fixtures[pathname]

      if (body === undefined) {
        // 见文件头：未知路径显式失败，不静默兜空
        log.unmatched.push(pathname)
        log.served.push(`501 ${pathname}`)
        if (TRACE) console.log(`   [stub] 501 ${pathname}（未定义）`)
        await route.fulfill({
          status: 501,
          contentType: 'application/json',
          body: JSON.stringify({
            detail: `e2e a11y 桩没有为 ${pathname} 定义响应（请补进 e2e/a11y-fixtures.ts）`,
          }),
        })
        return
      }

      const status = typeof body === 'object' && body !== null && '__status' in body
        ? Number((body as { __status: number }).__status)
        : 200

      log.served.push(`${status} ${pathname}`)
      if (TRACE) console.log(`   [stub] ${status} ${pathname}`)

      await route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(body),
      })
    },
  )

  return log
}

/**
 * 登录并落在已登录外壳里，然后进入 `path`。
 *
 * ## 为什么先登录、**再**走客户端路由
 *
 * 未登录时任何路径都由 `App.tsx` 的 `path="*"` 渲染成**登录页**（这是产品行为），
 * 而登录成功后 `Login.tsx` 一律 `navigate('/')` —— 也就是说
 * **"打开 /graph → 登录 → 停在该地址"这条路在真实产品里不存在**：
 * 用户点完登录会落到仪表盘。所以桩里若直接 `goto('/graph')` 再登录，
 * 断言"图谱工具栏出现"必然超时，而这不是缺陷、只是走错了路。
 *
 * 这里走的是用户的真实次序：登录 → 落到仪表盘 → 从外壳里点进目标页。
 * 用 `history.pushState` + `popstate` 触发同一次客户端路由，
 * 避免整页刷新（刷新会重新走一遍 AuthContext 初始化，虽然也能到达，
 * 但那不是"用户点进来"的路径）。
 */
export async function loginAs(page: Page, path = '/'): Promise<void> {
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await page.locator('#email').fill('e2e@example.com')
  await page.locator('#password').fill('secret123')
  await page.getByRole('button', { name: '登录' }).click()
  // 侧边栏只在已登录分支渲染：它是"登录真的成功了"的判据
  await page.getByRole('navigation', { name: '主导航' }).waitFor({ state: 'visible' })

  if (path !== '/') {
    await page.evaluate((to) => {
      window.history.pushState({}, '', to)
      window.dispatchEvent(new PopStateEvent('popstate'))
    }, path)
    await page.waitForURL((url) => url.pathname === path)
  }
}
