/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // 单元测试（Vitest + Testing Library + jsdom）
  //
  // ## 为什么现在才加
  //
  // 在附录 Z / AA 那两轮里，前端改动一直只有 tsc + eslint + build 三道
  // **静态**保证 —— 它们能发现类型错误和语法问题，但发现不了
  // "正文在翻面前就露出来了""勾了语义判分但回车提交没带上"这类
  // **运行时**错误，而后者正是那两轮真实踩到的坑。
  //
  // ## 为什么测试文件与被测文件同目录
  //
  // `vi.mock('../../api/qa')` 这类路径是**相对调用它的文件**解析的。
  // 放进 `__tests__/` 子目录会让每个 mock 路径多一层 `../`，
  // 与被测文件里的写法不一致 —— 抄错一层就会得到"mock 没生效、
  // 测试悄悄打到未 mock 的模块"这种难查的失败。同目录最省事。
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    // 每个用例后复位 mock 调用记录（不清除实现），避免用例间互相污染
    clearMocks: true,
  },
  server: {
    port: 5173,
    /**
     * 让 dev server **不要监听测试与审计产物目录**（visual-refactor-plan 批次 0.1 实测）。
     *
     * ## 不加这一条会发生什么
     *
     * Playwright 把 trace / 截图 / 录像写进这些目录，而它们就在项目里 ——
     * Vite 默认 watch 整个项目根，于是**每写一个产物文件，就给正在被测的页面
     * 推一次 reload**。webServer 日志里能直接看到：
     *
     *     [vite] page reload test-results-shots/.playwright-artifacts-1/traces/resources/….html
     *
     * 后果不是"测试挂掉" —— `a11y` / `e2e` 有 `expect` 轮询兜着，所以一直"能过"；
     * 真正的问题是**被测页面在测量时正在重载**：那两层因此变慢、偶发不稳，
     * 而新增的截图探针（固定时长等待、没有断言）直接读到了路由 fallback ——
     * 22 个场景里 19 个的 `main` 只剩 6 个字符、`h1` 数量为 0。
     *
     * ## 为什么必须把 Vite 的默认值抄一遍
     *
     * `server.watch` 是**整体传给 chokidar** 的：一旦提供 `ignored`，
     * Vite 自己的默认值就不再生效。漏掉 `node_modules` 会让 dev server
     * 去监听整棵依赖树 —— 那是比原问题严重得多的性能事故。
     *
     * `.gitignore` 管的是"别提交"，与"别让 dev server 监听"是两件事：
     * 这几个目录本来就在 `.gitignore` 里，却照样触发了 reload。
     */
    watch: {
      ignored: [
        '**/.git/**',
        '**/node_modules/**',
        '**/test-results/**',
        '**/coverage/**',
        // 本项目自己新增的产物目录（Vite 的默认忽略列表里没有它们）
        '**/test-results-*/**',
        '**/shots*/**',
        '**/playwright-report/**',
        '**/blob-report/**',
      ],
    },
    proxy: {
      '/api': {
        // 后端地址可用环境变量覆盖，避免切换本地/远程后端时必须改源码
        target: process.env.VITE_API_TARGET || 'http://localhost:8001',
        changeOrigin: true,
      },
    },
  },
  build: {
    // 依赖分包（见 docs/overhaul-plan.md §2.8 F-7）：
    // 未配置 build 段时，所有第三方依赖会被打进单一 vendor chunk，
    // 任何一次业务代码改动都会让整包缓存失效。
    // 这里把三个体积大且更新频率低的依赖单独拆出，并让它们沿各自的
    // 动态 import 边界只在需要时加载：
    //   - graph    ：react-force-graph-2d + d3 生态（仅知识图谱页需要）
    //   - markdown ：KaTeX + highlight.js（仅含公式/代码的页面需要）
    //   - react    ：react/react-dom/router（长期不变，最易命中缓存）
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('react-force-graph') || id.includes('d3-') || id.includes('three')) {
            return 'graph'
          }
          if (id.includes('katex') || id.includes('highlight.js') || id.includes('marked')) {
            return 'markdown'
          }
          if (
            id.includes('/react/') ||
            id.includes('/react-dom/') ||
            id.includes('react-router')
          ) {
            return 'react'
          }
          return undefined
        },
      },
    },
    // 单 chunk 超过 800KB 时给出警告（默认 500KB 对本项目偏严）
    chunkSizeWarningLimit: 800,
  },
})
