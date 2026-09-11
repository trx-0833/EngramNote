import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
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
