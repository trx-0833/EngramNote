// ESLint 扁平配置(最小集:react-hooks 规则 + 基础问题检查)
// 风格统一交由 prettier 负责;格式/风格类规则不在此重复。
import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'
import eslintConfigPrettier from 'eslint-config-prettier'

// ---------------------------------------------------------------------------
// 运行环境的全局变量：**手写最小集**，不引入 `globals` 包
//
// ## 为什么不装 `globals`
//
// 官方 `globals` 包确实更完整，但为了一条 lint 规则引入新依赖，与本项目
// "最小依赖"的一贯取舍不符（`src/api/client.ts` 开头就写着"不引入 axios
// 等第三方库，保持最小依赖"）。这里只需要浏览器与 Node 各自的常见名字，
// 手写十来行即可，且**看得见**：将来真要放宽，改的是这份清单。
//
// 若某个文件确实需要清单外的名字（如 `structuredClone`），
// 优先在文件内显式 `import`/`const` 声明，而不是往这里堆。
// ---------------------------------------------------------------------------
const BROWSER_GLOBALS = {
  window: 'readonly', document: 'readonly', navigator: 'readonly',
  location: 'readonly', history: 'readonly', localStorage: 'readonly',
  sessionStorage: 'readonly', console: 'readonly', fetch: 'readonly',
  URL: 'readonly', URLSearchParams: 'readonly', FormData: 'readonly',
  File: 'readonly', FileReader: 'readonly', Blob: 'readonly',
  AbortController: 'readonly', AbortSignal: 'readonly', Headers: 'readonly',
  Request: 'readonly', Response: 'readonly', Event: 'readonly',
  CustomEvent: 'readonly', EventTarget: 'readonly', MutationObserver: 'readonly',
  ResizeObserver: 'readonly', IntersectionObserver: 'readonly',
  requestAnimationFrame: 'readonly', cancelAnimationFrame: 'readonly',
  setTimeout: 'readonly', clearTimeout: 'readonly',
  setInterval: 'readonly', clearInterval: 'readonly',
  Notification: 'readonly', HTMLElement: 'readonly', HTMLInputElement: 'readonly',
  HTMLTextAreaElement: 'readonly', HTMLSelectElement: 'readonly',
  HTMLButtonElement: 'readonly', HTMLDivElement: 'readonly',
  SVGSVGElement: 'readonly', Element: 'readonly', Node: 'readonly',
  KeyboardEvent: 'readonly', MouseEvent: 'readonly', DragEvent: 'readonly',
  DOMParser: 'readonly', XMLSerializer: 'readonly', Image: 'readonly',
  performance: 'readonly', crypto: 'readonly', getComputedStyle: 'readonly',
  matchMedia: 'readonly', scrollTo: 'readonly', alert: 'readonly',
  confirm: 'readonly', prompt: 'readonly', structuredClone: 'readonly',
}

const NODE_GLOBALS = {
  process: 'readonly', console: 'readonly', Buffer: 'readonly',
  __dirname: 'readonly', __filename: 'readonly', require: 'readonly',
  module: 'writable', exports: 'writable', global: 'readonly',
  setTimeout: 'readonly', clearTimeout: 'readonly',
  setInterval: 'readonly', clearInterval: 'readonly',
  setImmediate: 'readonly', URL: 'readonly', URLSearchParams: 'readonly',
  TextEncoder: 'readonly', TextDecoder: 'readonly', fetch: 'readonly',
}

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

  // ---------------------------------------------------------------------------
  // 运行环境:浏览器(src/) vs Node(e2e/ 与 scripts/)
  //
  // ## 为什么需要这一节(2026-09-23)
  //
  // `lint` 此前只扫 `src/`(浏览器环境),于是 103KB 的 `e2e/a11y.spec.ts`、
  // 其余 e2e 规格与 7 个 `scripts/*.mjs`(约 300KB)全在门禁之外 ——
  // 而 `tsconfig.json` 反而把 `e2e` 纳入了 tsc,"类型检查覆盖、lint 不覆盖"
  // 两边口径不一致。
  //
  // 把范围扩到 `e2e/ scripts/` 之后,第一轮报了 187 条错 —— 全部是
  // `'console'/'process' is not defined` 这类**环境未声明**,不是代码缺陷。
  // 正确修法是显式声明运行环境,而不是把范围缩回去。
  // ---------------------------------------------------------------------------
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      globals: BROWSER_GLOBALS,
    },
  },
  {
    files: ['e2e/**/*.{ts,tsx}', 'scripts/**/*.{js,mjs}'],
    languageOptions: {
      globals: NODE_GLOBALS,
    },
  },

  // 与 prettier 的格式规则互斥部分关闭(格式交给 prettier 管)
  eslintConfigPrettier,
)