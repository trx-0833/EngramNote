/**
 * E2E 公共工具（overhaul-plan 5.13）
 *
 * 只放两件"每个用例都必须做、忘了就会产生假绿"的事：
 *
 * 1. **拦掉第三方外链**（Google Fonts）。`index.html` 里有
 *    `<link href="https://fonts.googleapis.com/...">`，而浏览器在
 *    **样式表解析完之前不会渲染**。断网或外链慢的时候，页面会停在
 *    "HTML 到了、可见元素一直不出来"，表现为 `toBeVisible` 超时 ——
 *    失败指向的地方（登录框没出来）和真实原因（字体 CDN 不通）完全无关。
 *    本层测的是应用自己的壳，不是字体 CDN 的可用性，所以外链一律 abort。
 *
 * 2. **桩掉后端 `/api`**（见 `stubApi`）。
 *
 * 刻意**不**放的东西：任何对被测应用行为的封装（点登录、填表单）。
 * 那种"页面对象"会把"用例到底验证了什么"藏进辅助函数里，
 * 而这个套件小到不需要它。
 *
 * ────────────────────────────────────────────────────────────────────────
 * ⚠️ 本文件踩过、并且值得记住的坑：**不要用 `/api/` 子串匹配来判断"这是后端请求"**。
 *
 * 最初这里的路由写成 `**` + `/api/` + `**` 这样的通配 glob，而 Vite 开发服务器
 * 是从**源码路径**提供模块的 —— 模块地址长这样：
 *
 *     http://127.0.0.1:4319/src/api/client.ts
 *     http://127.0.0.1:4319/src/api/goals.ts
 *
 * 这些地址里**同样含有 `/api/`**，于是那条 glob 把它们全部截胡，
 * 用 JSON 桩响应顶掉了真正的 ES 模块。后果是：`page.goto` 成功（HTML 拿到了）、
 * 但 React 永远挂不上，`#email` 一直等不到 —— 失败信息指向"登录框没出现"，
 * 与真实原因（应用自己的模块被自己的桩吃掉了）看起来毫无关系。
 * 同一个坑还让"校验没过就不该发请求"那条断言把 12 条模块请求当成了 API 请求。
 *
 * 正确的判据是**路径以 `/api/` 开头**，不是"路径里含有 /api/"。
 * 下面一律用 URL 判定函数（Playwright 的 `page.route` 支持传谓词），
 * 而不是 glob 或子串。
 *
 * ⚠️ 附带一个更隐蔽的坑：**这段说明本身差点让文件编译不过**。
 * 通配 glob 的写法里含有 `*` 紧跟 `/` 的两个字符，而那正是块注释的结束符 ——
 * 直接写进 `/** ... *\/` 里会把注释**提前终止**，剩下的文字变成代码，
 * 报的错是 `Cannot find name 'api'`（指向一句中文说明，看不出跟注释有关）。
 * 所以上面把 glob 拆成三段写。这个坑在本仓库的 Chinese 注释里会反复遇到。
 */
import type { Page } from '@playwright/test'

/** 需要拦掉的第三方主机（精确匹配 hostname） */
const THIRD_PARTY_HOSTS = ['fonts.googleapis.com', 'fonts.gstatic.com']

/** 该 URL 是不是后端 API 请求（判据：路径以 `/api/` 开头） */
export function isApiUrl(url: URL): boolean {
  return url.pathname === '/api' || url.pathname.startsWith('/api/')
}

/**
 * 阻断第三方外链，让首屏渲染只取决于本应用的资源。
 *
 * 必须在 `page.goto` **之前**调用，否则首个请求已经发出去了。
 *
 * 用谓词而不是 `'**\/*'` + `route.fallback()`：谓词不匹配时请求**根本不会被拦截**，
 * 省掉了"每个请求都要在若干处理器之间 fallback 一遍"的复杂度，
 * 也顺带消掉了 `**\/*` 会吃掉 `/src/api/*.ts` 的可能。
 */
export async function blockThirdParty(page: Page): Promise<void> {
  await page.route(
    (url) => THIRD_PARTY_HOSTS.includes(url.hostname),
    (route) => route.abort(),
  )
}

/**
 * 开始收集页面里的未捕获异常，返回的数组会随异常增长（按引用读取即可）。
 *
 * 注意：只收 `pageerror`（未捕获异常），**不**收 `console.error` ——
 * 后者会把 React 的正常告警也算进去，断言 `[]` 就成了维护负担而不是信号。
 */
export function collectPageErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(String(error)))
  return errors
}

/** 登录接口的两种桩行为 */
export type LoginStub = 'ok' | 'unauthorized'

/**
 * 桩掉后端 `/api`：登录返回令牌或 401，仪表盘用到的一堆统计接口返回空数据。
 *
 * ## 为什么桩"整个 /api"而不是只桩登录
 *
 * 登录成功后应用会渲染 `/`（Dashboard），它并发请求 8 个接口。
 * 只桩登录的话，其余请求会打到 Vite 的 `/api` 代理 —— 而本机后端不一定在跑，
 * 代理拿到 ECONNREFUSED 会返回 500，Dashboard 于是走错误分支。
 * 那样测出来的"登录成功"只到一半：令牌落盘了，但界面是错的。
 *
 * ## 这个桩证明不了什么
 *
 * 它证明的是**前端链路**（表单 → api client → 令牌持久化 → 路由切换 → 外壳渲染）
 * 是通的；它**不**证明后端真的会这样响应。接口契约由后端的
 * `backend/tests/` 那一层负责。
 */
export async function stubApi(page: Page, login: LoginStub): Promise<void> {
  await page.route(
    (url) => isApiUrl(url),
    async (route) => {
      // 用 URL 判据而非 glob：见文件头关于 `/src/api/*.ts` 的说明
      const path = new URL(route.request().url()).pathname
      const json = (status: number, body: unknown) =>
        route.fulfill({
          status,
          contentType: 'application/json',
          body: JSON.stringify(body),
        })

      if (path === '/api/auth/login') {
        if (login === 'unauthorized') {
          // 与后端一致：401 + detail。前端对凭据类接口的 401 不触发全局登出，
          // 而是抛出「邮箱或密码错误」（见 api/client.ts 的 isAuthCredentialPath）。
          await json(401, { detail: 'Incorrect email or password' })
        } else {
          await json(200, {
            access_token: 'e2e-access-token',
            refresh_token: 'e2e-refresh-token',
            token_type: 'bearer',
            user: {
              id: 'e2e-user',
              email: 'e2e@example.com',
              username: 'e2e',
              is_active: true,
              created_at: '2026-01-01T00:00:00Z',
            },
          })
        }
        return
      }

      // ── 已登录外壳（Dashboard）用到的接口 ──
      // 顺序要紧：/api/goals/daily-plan 必须先于 /api/goals 判断
      if (path === '/api/goals/daily-plan') {
        // 无活跃目标时后端返回 400，前端把它当作正常业务状态而非加载失败
        await json(400, { detail: 'No active goals' })
      } else if (path === '/api/goals') {
        await json(200, { goals: [], total: 0 })
      } else if (path === '/api/notes') {
        await json(200, { items: [], total: 0, page: 1, page_size: 5 })
      } else if (path === '/api/review/stats') {
        await json(200, { due_count: 0, today_done: 0, today_accuracy: 0, daily_limit: 50 })
      } else if (path === '/api/report/daily') {
        await json(200, {
          date: '2026-01-01',
          new_mastered: 0,
          total_review_time_ms: 0,
          total_reviews: 0,
          today_accuracy: 0,
          weak_point_count: 0,
          question_type_accuracy: [],
        })
      } else if (path === '/api/report/weekly-trend') {
        await json(200, { items: [], total_reviews: 0, avg_accuracy: 0 })
      } else if (path === '/api/report/weak-points') {
        await json(200, { items: [], total: 0 })
      } else if (path === '/api/auth/reminder-settings') {
        await json(200, { email_reminder_enabled: false })
      } else {
        await json(200, {})
      }
    },
  )
}
