import { defineConfig, devices } from '@playwright/test'

/**
 * Playwright E2E 配置（overhaul-plan 5.13）
 *
 * ## 这一层补的是什么
 *
 * Vitest + jsdom 那一层（`npm test`，273 个用例）跑在 Node 里：
 * 没有布局、没有真实的 HTML 约束校验、没有 CSS 级联、没有真实网络栈。
 * 它能断言"点了按钮之后 React 状态怎么变"，但断言不了
 * "按钮到底在不在视口里""原生 `type=email` 有没有拦住提交"
 * "全局样式表和 CSS Module 有没有真的进产物并生效"。
 *
 * ## 端口：默认 4319，可用 `E2E_PORT` 换一个
 *
 * 人类手上的 DSH Web GUI 占着 **3080**，绝不能碰；Vite 默认的 5173
 * 也可能被别的会话占用。4319 是随手挑的一个当前未被监听的高位端口。
 * 配合 `--strictPort`：端口被占时**直接失败**，而不是悄悄换一个端口 ——
 * 悄悄换端口会让 `webServer.url` 的健康检查去探一个空地址，
 * 表现为"超时 120 秒后报一句看不懂的错"。
 *
 * 但"固定端口 + `--strictPort`"还有第二种失败：**两个并发运行**。
 * 实测过一次 —— 两个 agent 同时跑 `npm run e2e`，第二个拿到
 * `http://127.0.0.1:4319 is already used`，而**汇总那行根本没打印出来**
 * （Playwright 起 `webServer` 就失败了），看起来像"这次跑挂了"，
 * 与真实原因（端口被另一个 Playwright 占着）毫无关系。
 *
 * 所以端口改成可配，默认值一字不变：
 *
 * ```
 * E2E_PORT=4320 npm run e2e        # bash / CI
 * $env:E2E_PORT = 4320; npm run e2e  # PowerShell
 * ```
 *
 * **3080 被显式禁止**：写错时直接抛错并拒绝启动，而不是去占人类正在用的
 * 那个界面。这一层的每一种失败都必须是响亮的 —— 包括"你把端口配错了"。
 *
 * ## 为什么 webServer 由 Playwright 自己拉起
 *
 * 手工 `npm run dev` 再跑测试是"配置了但没人执行"的温床：
 * 忘了开服务 → 测试全红 → 加 `|| true` → 这一层等于不存在。
 * `webServer` 让 `npm run e2e` **一条命令自洽**：自动启动、就绪后开跑、
 * 跑完自动关闭。
 *
 * ## 为什么 reuseExistingServer 是 false
 *
 * 复用已存在的服务会让"你测的到底是哪份代码"变得不可知 ——
 * 上一个会话留下的 dev server 可能跑的是改动前的模块图。
 * 这里宁可因为端口被占而明确失败。
 */
const DEFAULT_E2E_PORT = 4319

/** 全链路层（`npm run e2e:full`）的三个端口：前端 / 后端 API / 后端监督器控制口 */
const DEFAULT_E2E_FULL_PORT = 4381
const DEFAULT_E2E_FULL_API_PORT = 4382
const DEFAULT_E2E_FULL_CONTROL_PORT = 4383

/**
 * 这些端口不属于这一层：**3080 是人类手上的 DSH Web GUI**。
 * 单独列出来而不是"只在注释里说一句"，是因为注释拦不住一个写错的 E2E_PORT，
 * 而占错端口的后果（把正在用的界面顶掉）比这次测试失败严重得多。
 */
const FORBIDDEN_E2E_PORTS = [3080]

function resolvePort(envName: string, defaultPort: number): number {
  const raw = process.env[envName]
  if (raw === undefined || raw.trim() === '') return defaultPort
  const value = raw.trim()
  if (!/^\d+$/.test(value)) {
    throw new Error(`${envName}="${raw}" 不是合法端口：需要 1–65535 的整数。`)
  }
  const port = Number(value)
  if (port < 1 || port > 65535) {
    throw new Error(`${envName}=${port} 越界：需要 1–65535 的整数。`)
  }
  if (FORBIDDEN_E2E_PORTS.includes(port)) {
    throw new Error(
      `${envName}=${port} 是人类正在使用的 DSH Web GUI 端口，E2E 绝不能占用它。` +
        `换一个高位端口（默认 ${defaultPort}）。`,
    )
  }
  return port
}

function resolveE2ePort(): number {
  return resolvePort('E2E_PORT', DEFAULT_E2E_PORT)
}

const E2E_PORT = resolveE2ePort()
const E2E_BASE_URL = `http://127.0.0.1:${E2E_PORT}`

/**
 * 全链路层的端口。为什么要**三个**：
 *
 * - `E2E_FULL_PORT`：Vite 开发服务器（浏览器只访问它，`/api` 由它代理）；
 * - `E2E_FULL_API_PORT`：真实 uvicorn；
 * - `E2E_FULL_CONTROL_PORT`：`backend/scripts/_e2e_full_runner.py` 的就绪探针。
 *
 * 最后一个是必须的：`webServer` 只能探**一个** URL，而这条链路要等的不止
 * uvicorn（还有 Celery worker 连上 broker）。让探针 URL 指向监督器，
 * 由它在两者都就绪后才返回 200 —— 否则测试会在 worker 还没订阅时开始上传，
 * 表现为"转换超时"，与真实原因（worker 没起来）毫无关系。
 */
const E2E_FULL_PORT = resolvePort('E2E_FULL_PORT', DEFAULT_E2E_FULL_PORT)
const E2E_FULL_API_PORT = resolvePort('E2E_FULL_API_PORT', DEFAULT_E2E_FULL_API_PORT)
const E2E_FULL_CONTROL_PORT = resolvePort('E2E_FULL_CONTROL_PORT', DEFAULT_E2E_FULL_CONTROL_PORT)
const E2E_FULL_BASE_URL = `http://127.0.0.1:${E2E_FULL_PORT}`
const E2E_FULL_CONTROL_URL = `http://127.0.0.1:${E2E_FULL_CONTROL_PORT}`
const E2E_FULL_TMP =
  process.env.ENGRAMNOTE_E2E_TMP || `${process.env.TEMP ?? ''}\\engramnote-e2e-full`

/**
 * 全链路层是否**显式打开**。默认关闭的理由（钱、时间、CI 没密钥）见
 * `e2e/e2e-full.spec.ts` 的文件头。
 *
 * 这个开关同时决定两件事，缺一不可：
 *   1. project 在不在 `projects` 里 —— 不在就不会跑、也不会被收集；
 *   2. `webServer` 在不在配置里 —— 不在就不会启动真实后端。
 * 只做第 1 件的话，`npm run e2e:all` 之类仍会把 uvicorn/worker 拉起来
 * （Playwright 对 `webServer` 的启动**不**看项目），白等一次就绪超时。
 */
const E2E_FULL_ENABLED = ['1', 'true', 'True', 'yes'].includes(
  (process.env.ENGRAMNOTE_E2E_FULL ?? '').trim(),
)

/**
 * 只跑探针时（`npm run e2e:full:probe`）**不需要任何服务**：
 * 探针用 `page.setContent()` 注入静态 HTML，不访问后端。
 *
 * 判定方式是从进程参数里看目标 project —— `webServer` 是**按配置**启动的，
 * 不是按 project，因此"这一轮到底要不要真实后端"只能从命令行意图判断。
 * `--project=e2e-full-probe` 是探针脚本的唯一形态（见 package.json）。
 */
const PROBE_ONLY_RUN = process.argv.some((arg) => arg.includes('e2e-full-probe'))
const NEEDS_FULL_STACK = E2E_FULL_ENABLED && !PROBE_ONLY_RUN

/**
 * 可访问性审计的 spec（5.9）——**单独跑**，见文件末尾 `A11Y_SPEC` 的说明。
 *
 * 这里用 `testMatch` 而不是 `testIgnore`：项目的语义是
 * "`npm run e2e` 跑什么"，把要跑的东西**明确列出来**比"列一堆要忽略的"更难漂移
 * （新加的 spec 若忘了加进来会**不跑**，而"忘加忽略"会让它混进来 —— 前者更吵、
 * 更容易被发现）。
 */
const FUNCTIONAL_SPEC = '**/!(*a11y).spec.ts'

/**
 * 可访问性审计自己的匹配式。单一来源：`playwright.config.ts` 里那个 `a11y`
 * project 用它，`docs/a11y-audit.md` 与 `npm run a11y` 也都指向同一处。
 *
 * ## 为什么审计与功能层分成两个 project
 *
 * 1. **并发不同**：功能层 `fullyParallel` 按 CPU 核数开 worker（本机 8）；
 *    审计跑 8 个 worker 时，Vite 的**按需转换**（页面都是 `React.lazy`）
 *    把首屏拖到 10 秒以上，8 条用例一起超时 —— 失败信息指向"页面没渲染"，
 *    真实原因是开发服务器被同时敲。审计只跑 4 个 worker，实测 ~20 秒。
 * 2. **判定尺度不同**：功能层断言"行为对不对"，审计断言"违规有没有超出登记表"。
 *    混在一起会让"这次红的到底是功能坏了还是多了一条对比度问题"变得含糊。
 * 3. **`testMatch` 是交集**：靠命令行传文件名来跑审计，会与顶层 `testMatch`
 *    取交集 → `No tests found`（第一次接线时真踩到了）。用 project 表达
 *    "这一组用例有自己的匹配式与参数"才是配置该说的话。
 */
const A11Y_SPEC = '**/a11y.spec.ts'

/**
 * 现状基线探针的匹配式（visual-refactor-plan 批次 0.1）。
 *
 * 与 `A11Y_SPEC` 同一个道理：它有自己的意图，该由 project 表达。
 * 它**只截图 + 打印 computed style，不做任何断言** —— 因此绝不能留在
 * `chromium` 那个阻断层里：顶层的 `FUNCTIONAL_SPEC`（"除 a11y 之外全部"
 * 的取反匹配）会把 `shot-probe.spec.ts` 收进去，`npm run e2e` 于是从
 * 10 条用例变成 32 条、多跑约 40 秒，而那一层的"通过"毫无意义
 * （探针没有断言，永远不会红）。
 */
const SHOT_PROBE_SPEC = '**/shot-probe.spec.ts'

/**
 * 全链路层的匹配式（5.13 后半段）。
 *
 * 命名刻意用 `e2e-full.spec.ts` 而不是 `full.spec.ts`：功能层的匹配式
 * （见 `FUNCTIONAL_SPEC`，一个"除 a11y 之外全部"的取反匹配）会把它排除掉，
 * 因此它**不可能**混进 `npm run e2e`。
 *
 * ⚠️ 上面这句刻意**没有**把那个匹配式原样写出来：它的字面量里含有
 * "星号紧跟斜杠"这两个字符，而那是块注释的结束符 —— 写进来会把注释提前终止，
 * 剩下的文字变成代码（`support.ts` 的文件头记着同一个坑，
 * 而且刚刚在 `playwright.config.ts` 上真的踩了一次：
 * 报错是 `Unexpected token (158:9)`，指向一句中文说明，看不出跟注释有关）。
 * 这不是巧合而是要求 —— 一条会花钱、要联网、跑几分钟的用例混进阻塞层，
 * 结果一定是那一层被加 `|| true` 或者没人再跑。
 */
const FULL_SPEC = '**/e2e-full.spec.ts'

/**
 * 全链路层的**探针**（`npm run e2e:full:probe`）。
 *
 * 它守的是第 ⑤ 条用例里那个"从 DOM 里读答案"的定位式：用与 `QA.tsx` 结构一致的
 * 静态 HTML 断言选择器，**不启动后端、不调 LLM、秒级完成**。
 *
 * 为什么要单独一个匹配式（而不是让它落进 `e2e-full` project）：`webServer` 是
 * **按配置**启动的，不是按 project —— 探针一旦挂在 `e2e-full` 下，跑它就会白等
 * 一次真实后端（uvicorn + Celery）的就绪探针。用一个独立 project 表达
 * "这一组用例不需要后端"才是配置该说的话。
 */
const FULL_PROBE_SPEC = '**/e2e-full-probe.spec.ts'

export default defineConfig({
  testDir: './e2e',
  // 只认 *.spec.ts：Vitest 的用例是 src/**/*.test.tsx，两边目录和命名都不重叠。
  // 审计（a11y.spec.ts）被**刻意排除**在默认运行之外，见 A11Y_SPEC 的说明。
  testMatch: FUNCTIONAL_SPEC,

  // 全部用例都通过 page.route 桩掉了后端，彼此不共享状态，可以并行
  fullyParallel: true,

  // 本地调试时留下 trace/截图，CI 里失败可下载 —— 失败时没有证据的 E2E 等于没跑
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [['list']],

  timeout: 30_000,
  expect: { timeout: 10_000 },

  use: {
    baseURL: E2E_BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // 桌面尺寸：本层不测响应式（那是 5.10 的事），固定尺寸让布局断言可预期
    viewport: { width: 1280, height: 720 },
  },

  projects: [
    {
      /**
       * 功能层：`npm run e2e` 跑这个 project（10 条用例）
       *
       * ## ⚠️ `testIgnore` 不是可选的（这一条被真实数据证明过两次）
       *
       * 顶层的 `FUNCTIONAL_SPEC`（"除 a11y 之外全部"的取反匹配）**也会匹配
       * `e2e-full*.spec.ts`** —— `!(*a11y)` 里那个 `*` 匹配的是任意字符串，
       * 所以 `e2e-full` / `e2e-full-probe` 一样落进来。后果实测：
       *
       *     npm run e2e  → 1 failed / 5 did not run / 16 passed
       *
       * 全链路用例在**没有** opt-in 时被收集并执行：它们连不上 4381 上的前端、
       * 更连不上后端，于是失败 —— 而这一层是 **CI 里的阻断层**，
       * 于是每一次 PR 都会因为这个与产品无关的原因变红。
       *
       * 第一次修的时候只加了 project 注册门禁（`ENGRAMNOTE_E2E_FULL`），
       * 那只防住了"注册"，防不住"被别的 project 的匹配式收走"。
       * 现在两道都在：
       *   1. 两个全链路 project 只在 opt-in 时注册；
       *   2. 本 project 用 `testIgnore` 明确把它们排除。
       *
       * 反面做法是把那两个文件改名到 `*.spec.ts` 之外 —— 但 `testDir` 下的
       * 命名约定是这一层的"哪些文件是用例"的唯一线索，为匹配式的边界改名
       * 会让约定本身变模糊。匹配式的问题用匹配式解决。
       */
      name: 'chromium',
      testMatch: FUNCTIONAL_SPEC,
      testIgnore: ['**/e2e-full.spec.ts', '**/e2e-full-probe.spec.ts', SHOT_PROBE_SPEC],
      use: { ...devices['Desktop Chrome'] },
    },
    {
      /**
       * 现状基线探针（`npm run shots`）：截图 + computed style 快照，无断言。
       *
       * 用途只有一个 —— 给"改外观前后逐页对比"提供同一把尺子。
       * 截图落在 `frontend/shots-current/`（不入库），
       * computed style 快照打到 stdout（供无法看图的人逐批核对）。
       */
      name: 'shots',
      testMatch: SHOT_PROBE_SPEC,
      // 4 个 worker：22 个场景各自要等懒加载路由 + 桩请求 + 过渡结束，
      // 8 个并发会把 Vite 的按需转换拖到 10 秒以上（与 a11y 同一个理由）
      workers: 4,
      // 独立产物目录：避免与本层其它运行互相清空（同 e2e-full 的 outputDir 说明）
      outputDir: './test-results-shots',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      /**
       * 可访问性审计（5.9）：`npm run a11y` → `--project=a11y`。
       *
       * 单独建一个 project 而不是"让 a11y.spec.ts 落进上面那个 project、
       * 靠命令行传文件名"：命令行传文件名会与顶层 `testMatch` **取交集**，
       * 结果是 `No tests found`（这正是第一次接线时踩到的坑）。
       * 用 project 表达"这一组用例有自己的匹配式与并发参数"才是配置本身该说的话。
       */
      name: 'a11y',
      testMatch: A11Y_SPEC,
      // 审计的并发刻意压到 4：8 个 worker 同时打 Vite dev server 时，
      // 懒加载路由的**按需转换**会把首屏拖到 10 秒以上（详见 A11Y_SPEC 的说明）
      workers: 4,
      use: { ...devices['Desktop Chrome'] },
    },
    /**
     * 全链路层（5.13 后半段）：**真实**后端 + **真实** LLM。
     *
     * ## 只在显式打开时存在
     *
     * `ENGRAMNOTE_E2E_FULL=1` 之外一律不注册这个 project（见 E2E_FULL_ENABLED）。
     * 因此 `npm run e2e` / `npm run e2e:all` 即使在旧脚本里也不会顺手把它跑了。
     *
     * ## 为什么 `workers: 1`
     *
     * 这一组用例是**一条链**的四步（注册 → 上传+理解 → 复习 → 问答），
     * 共享同一份状态（令牌、笔记 ID）；并行既没有意义，也会让上传与理解
     * 互相抢 `MAX_CONCURRENCY=3` 的 LLM 并发闸门。
     *
     * ## 为什么 `testDir` 还是同一个
     *
     * 用例放在 `e2e/` 下与其它 spec 同目录：`support.ts`（拦第三方外链）等
     * 辅助件就在旁边，另开目录只会多一层 `../` 的相对路径。
     * 靠 `testMatch` 精确区分才是配置该说的话。
     */
    ...(E2E_FULL_ENABLED
      ? [
          {
            name: 'e2e-full',
            testMatch: FULL_SPEC,
            workers: 1,
            /**
             * ⚠️ **独立的产物目录**（不是 `test-results/`）。
             *
             * Playwright 在每次运行**开始时清空** `outputDir`。全链路层跑几分钟
             * （上传 → 清洗要加载嵌入模型 → 理解要调 LLM），这期间任何另一个
             * Playwright 运行（`npm run e2e` / `npm run a11y`，或另一个会话/agent
             * 正在跑的那一层）都会把 `test-results/` 整个删掉重建 —— 于是本层在
             * 收尾时会炸在一堆看起来毫不相干的错误上：
             *
             *     Error: browserContext.close: ENOENT: no such file or directory,
             *       open '...\test-results\.playwright-artifacts-0\traces\...-recording3.trace'
             *     Error: ENOENT: ... open '...\test-results\e2e-full\evidence.json'
             *
             * 实测就是并发跑 `a11y` 时发生的：**测试本身全绿**（后端日志、证据
             * JSON、页面都对），红的是产物写入。给这一层一个自己的目录，
             * 这个"看起来像测试失败、实际是目录被另一个运行删了"的坑就没了。
             */
            outputDir: './test-results-e2e-full',
            // 真实链路是分钟级：单条用例给 25 分钟，webServer 启动另算
            timeout: 1_500_000,
            expect: { timeout: 60_000 },
            use: {
              ...devices['Desktop Chrome'],
              baseURL: E2E_FULL_BASE_URL,
              trace: 'retain-on-failure' as const,
              screenshot: 'only-on-failure' as const,
            },
          },
          {
            /**
             * 探针层：只跑 `e2e-full-probe.spec.ts`（静态 HTML + 定位式断言）。
             *
             * ⚠️ 它**不启动任何服务** —— 见上面 `webServer` 那段的说明：
             * `webServer` 会跟着整个配置一起启动，所以探针必须与真实链路分开跑
             * （`npm run e2e:full` 跑链路，`npm run e2e:full:probe` 跑探针）。
             */
            name: 'e2e-full-probe',
            testMatch: FULL_PROBE_SPEC,
            workers: 1,
            outputDir: './test-results-e2e-full-probe',
            timeout: 60_000,
            use: { ...devices['Desktop Chrome'] },
          },
        ]
      : []),
  ],

  /**
   * `webServer` 用**数组**表达"这一层要哪些服务"。
   *
   * ⚠️ Playwright 启动 `webServer` **不看 project** —— 配置里写着的都会被拉起。
   * 因此这里按开关二选一：
   *
   * - 默认（桩掉后端的层）：只起 `E2E_PORT` 的 Vite；
   * - `ENGRAMNOTE_E2E_FULL=1`：只起全链路层自己的两个服务（Vite + 真实后端），
   *   全链路用例的 `baseURL` 指向 `E2E_FULL_PORT`，与 4319 那个无关；
   * - 只跑探针（`npm run e2e:full:probe`）：**一个服务都不起**
   *   （探针用 `page.setContent()` 注入静态 HTML，不碰后端）。
   *
   * 不做这个二选一的后果很具体：跑全链路时会白白多起一个 Vite，
   * 而跑桩层时会被拉去等一个真实 uvicorn + Celery worker 的就绪探针，
   * 跑探针时又会白等一次后端启动（实测：探针 6 条用例本身 14 秒，
   * 却因为 `webServer` 一起启动而多花十几秒）。
   */
  webServer: PROBE_ONLY_RUN
    ? []
    : NEEDS_FULL_STACK
    ? [
        {
          /**
           * 全链路层的前端：端口与 `VITE_API_TARGET` 都必须指向这一层的后端，
           * 否则代理会把请求打到 8001 上那个（人类正在用的）后端 ——
           * 那正是"测试碰到真实数据"的另一种形态。
           */
          command: `npm run dev -- --port ${E2E_FULL_PORT} --strictPort --host 127.0.0.1`,
          url: E2E_FULL_BASE_URL,
          reuseExistingServer: false,
          timeout: 180_000,
          stdout: 'pipe' as const,
          stderr: 'pipe' as const,
          env: {
            VITE_API_TARGET: `http://127.0.0.1:${E2E_FULL_API_PORT}`,
          },
        },
        {
          /**
           * 真实后端（uvicorn + Celery worker 由监督器一起拉起）。
           *
           * `url` 指向的是**监督器的控制口**，不是 uvicorn：见
           * E2E_FULL_CONTROL_PORT 的说明（要等 worker 真的连上 broker）。
           */
          command: 'python scripts/_e2e_full_runner.py',
          cwd: '../backend',
          url: `${E2E_FULL_CONTROL_URL}/health`,
          reuseExistingServer: false,
          // 首次启动要 import 整个应用 + worker 载入 Celery 配置
          timeout: 300_000,
          stdout: 'pipe' as const,
          stderr: 'pipe' as const,
          env: {
            ENGRAMNOTE_E2E_API_PORT: String(E2E_FULL_API_PORT),
            ENGRAMNOTE_E2E_CONTROL_PORT: String(E2E_FULL_CONTROL_PORT),
            E2E_FULL_PORT: String(E2E_FULL_PORT),
            ENGRAMNOTE_E2E_TMP: E2E_FULL_TMP,
            ...(process.env.ENGRAMNOTE_E2E_PYTHON
              ? { ENGRAMNOTE_E2E_PYTHON: process.env.ENGRAMNOTE_E2E_PYTHON }
              : {}),
            ...(process.env.ENGRAMNOTE_E2E_KEEP
              ? { ENGRAMNOTE_E2E_KEEP: process.env.ENGRAMNOTE_E2E_KEEP }
              : {}),
          },
        },
      ]
    : {
        // `npm run dev` = `vite`；用 `--` 把端口参数透传给 vite。
        // Windows 下 Playwright 经 cmd.exe 起进程，`npm` 会解析到 npm.cmd
        // （不是被策略拦住的 npm.ps1）。
        command: `npm run dev -- --port ${E2E_PORT} --strictPort --host 127.0.0.1`,
        url: E2E_BASE_URL,
        reuseExistingServer: false,
        timeout: 120_000,
        stdout: 'pipe',
        stderr: 'pipe',
      },
})

export {
  A11Y_SPEC,
  SHOT_PROBE_SPEC,
  E2E_BASE_URL,
  E2E_FULL_BASE_URL,
  E2E_FULL_ENABLED,
  E2E_FULL_PORT,
  E2E_PORT,
  FULL_SPEC,
  FUNCTIONAL_SPEC,
}
