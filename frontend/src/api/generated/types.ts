/**
 * @file OpenAPI 生成类型的唯一入口（阶段 5.1 / S2）
 *
 * ## 这个文件解决什么
 *
 * `schema.ts` 是 `npm run gen:api` 的产物（`openapi-typescript` 从
 * `backend/openapi.json` 生成：103 条路径 / 119 个操作 / 184 个组件 schema），
 * 它是**契约的唯一来源**。但它的顶层导出是三个大接口（`paths` / `components` /
 * `operations`），直接用要写成 `components['schemas']['NoteResponse']` 这种两跳索引。
 *
 * 本文件只做一件事：把"按组件名取类型"变成一个**可读、可被编译器检查**的入口 ——
 * `Schema<'NoteResponse'>`。于是手写模块里的
 * `export interface Note { … }` 可以换成
 * `export type Note = Schema<'NoteResponse'>`：
 * **导出名不变、调用方不变，类型的来源从"手抄"变成"生成"**。
 *
 * ## 为什么不直接 `import type { components } from './schema'`
 *
 * 两个理由，都不是审美：
 *
 * 1. `components['schemas'][K]` 里的 `K` 拼错时，报错落在很深的索引表达式里；
 *    而 `Schema<'NoteRespones'>` 的 `K extends keyof Schemas` 会在**别名本身**报错，
 *    错误信息里直接写着 `Did you mean 'NoteResponse'?`；
 * 2. 生成文件随时会被重新生成。把手写代码对生成结构的**全部依赖**收在一个文件里，
 *    schema 换形状时的改动面才是可见的。
 *
 * ## 这里**不放**什么
 *
 * 不放任何运行时值：本文件与 `schema.ts` 一样必须在构建时被完全擦除。
 * `import type` 是硬要求 —— 写成值导入会让打包器留下一个空模块。
 *
 * ## 生成类型覆盖不到的（**必须保持手写**，见 docs/openapi-client.md §9.2）
 *
 * 401 刷新单飞、请求超时（30 s / 上传 600 s）、`Content-Type` 合并、
 * 204 / 空响应体处理、以及 4 个把数组塞成 JSON 字符串的 multipart 上传
 * （`uploadFile` / `commitUpload` / `uploadFileToFolder` / `prepareUpload`）——
 * 这些横切行为 OpenAPI 表达不了，仍然在 `client.ts` / 各域模块里手写。
 */
import type { components } from './schema';

export type { components, paths, operations } from './schema';

/** `components['schemas']`：全部组件 schema 的索引（184 个） */
export type Schemas = components['schemas'];

/**
 * 按组件名取一个生成的 schema 类型
 *
 * @example
 * export type Note = Schema<'NoteResponse'>;
 */
export type Schema<K extends keyof Schemas> = Schemas[K];
