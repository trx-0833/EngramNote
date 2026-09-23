/**
 * `e2e-full` 需要的**最小** Node 类型声明
 *
 * ## 为什么手写而不是装 `@types/node`
 *
 * 本仓库的 `frontend/tsconfig.json` 的 `include` 里有 `e2e`，但工程里
 * **没有** `@types/node`（实测 `node_modules/@types/` 下只有 react / katex /
 * aria-query 等）。于是 `e2e/e2e-full.spec.ts` 里
 *
 *     import { statSync } from 'node:fs'        // TS2307: 找不到模块
 *     process.env.TEMP                          // TS2580: 找不到 process
 *
 * 会让 `tsc`（也就是 `npm run build` 的第一步）失败 —— 而这一层**必须**
 * 读文件、读环境变量：它要写证据 JSON（报告引用它）、要算出真实数据库与
 * 临时库的路径来证明隔离生效。
 *
 * 三条路里选了第三条：
 *
 * 1. `npm i -D @types/node` —— 会改 `package.json` / lock，并给整个前端工程
 *    引入一整套 Node 全局（`Buffer`、`process`、`setImmediate`…），
 *    浏览器代码里误用它们将不再报错；
 * 2. 用 `// @ts-expect-error` 逐行压 —— 会把"类型不存在"变成静默，
 *    以后写错 API 名字也不会有人发现；
 * 3. **只声明这一层用到的几个符号**（本文件）—— 影响面被限制在
 *    `e2e/` 下，前端 `src/**` 一个符号都拿不到。
 *
 * 因此这里每个声明都对应 `e2e-full.spec.ts` 里的一处真实调用，
 * 多一个都不要加。
 */

declare module 'node:fs' {
  /** 文件信息（只声明用到的两个字段） */
  export interface Stats {
    size: number;
    mtimeMs: number;
  }
  /** 取文件信息；文件不存在时**抛异常**（调用方自己 try/catch） */
  export function statSync(path: string): Stats;
  /** 路径是否存在 */
  export function existsSync(path: string): boolean;
  /** 递归建目录 */
  export function mkdirSync(path: string, options?: { recursive?: boolean }): void;
  /** 写文本文件（用于落证据 JSON 与测试资料） */
  export function writeFileSync(path: string, data: string, encoding?: string): void;
  /** 读文本文件 */
  export function readFileSync(path: string, encoding?: string): string;
  /** 列目录条目名（用于"真实数据目录里有没有新增条目"这条判据） */
  export function readdirSync(path: string): string[];
}

declare module 'node:path' {
  export function join(...parts: string[]): string;
  export function resolve(...parts: string[]): string;
  export function dirname(path: string): string;
}

declare module 'node:url' {
  export function fileURLToPath(url: string): string;
}

/** Playwright 的 Node 运行环境里本来就有的全局 */
declare const process: {
  env: Record<string, string | undefined>;
  platform: string;
};
