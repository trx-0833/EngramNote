// ESLint 扁平配置(最小集:react-hooks 规则 + 基础问题检查)
// 风格统一交由 prettier 负责;格式/风格类规则不在此重复。
import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'
import eslintConfigPrettier from 'eslint-config-prettier'

export default tseslint.config(
  // 跳过构建产物
  { ignores: ['dist', 'node_modules', '.npm-cache'] },

  // 基础 JS 规则(未使用变量、引用未定义等)
  js.configs.recommended,

  // TypeScript 类型检查增强(no-explicit-any 降为 warn,不阻塞)
  ...tseslint.configs.recommended,

  // React Hooks 规则:依赖数组、定时器清理等 React 专用问题
  {
    files: ['**/*.{ts,tsx}'],
    plugins: {
      'react-hooks': reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // 后端字段是 snake_case,允许显式 any 便于渐进收敛
      '@typescript-eslint/no-explicit-any': 'warn',
    },
  },

  // 与 prettier 的格式规则互斥部分关闭(格式交给 prettier 管)
  eslintConfigPrettier,
)