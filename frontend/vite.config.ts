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
