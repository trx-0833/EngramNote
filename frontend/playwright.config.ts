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
 * ## 为什么端口是 4319
 *
 * 人类手上的 DSH Web GUI 占着 **3080**，绝不能碰；Vite 默认的 5173
 * 也可能被别的会话占用。4319 是随手挑的一个当前未被监听的高位端口。
 * 配合 `--strictPort`：端口被占时**直接失败**，而不是悄悄换一个端口 ——
 * 悄悄换端口会让 `webServer.url` 的健康检查去探一个空地址，
 * 表现为"超时 120 秒后报一句看不懂的错"。
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
const E2E_PORT = 4319
const E2E_BASE_URL = `http://127.0.0.1:${E2E_PORT}`

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
      // 功能层：`npm run e2e` 跑这个 project（10 条用例）
      name: 'chromium',
      testMatch: FUNCTIONAL_SPEC,
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
  ],

  webServer: {
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

export { A11Y_SPEC, E2E_BASE_URL, E2E_PORT, FUNCTIONAL_SPEC }
