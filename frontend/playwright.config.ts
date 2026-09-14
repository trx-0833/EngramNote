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

export default defineConfig({
  testDir: './e2e',
  // 只认 *.spec.ts：Vitest 的用例是 src/**/*.test.tsx，两边目录和命名都不重叠
  testMatch: '**/*.spec.ts',

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
      name: 'chromium',
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

export { E2E_BASE_URL, E2E_PORT }
