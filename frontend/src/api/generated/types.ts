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
 * 本文件做两件事：
 *
 * 1. （S2）把"按组件名取类型"变成一个**可读、可被编译器检查**的入口 ——
 *    `Schema<'NoteResponse'>`。于是手写模块里的
 *    `export interface Note { … }` 可以换成
 *    `export type Note = Schema<'NoteResponse'>`：
 *    **导出名不变、调用方不变，类型的来源从"手抄"变成"生成"**。
 * 2. （S3）把**请求侧**也按**端点**索引起来 —— `BodyOf<'/notes/{note_id}', 'put'>`
 *    与 `QueryOf<'/notes', 'get'>`，见下面第二节。响应类型按组件名取就够了，
 *    请求体不行：请求体必须能回答"它属于哪个端点的哪个方法"。
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
import type { components, paths } from './schema';

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

/* ================================================================== *
 * 二、端点索引（阶段 5.1 / S3）
 *
 * S2 只把**响应类型**接上了契约（`Schema<'XResponse'>`）—— 那是"事后比对"：
 * 手写一个类型、再由 `openapi-drift.mjs` 拿编译器去比。S3 把**请求侧**
 * （query 参数与请求体）也接上契约，于是"比对"变成"构造即契约"：
 * 写错字段名/漏字段/抄错端点，都在**调用点**编译失败，而不是等漂移报告。
 *
 * 为什么按"前端路径"索引而不是按组件名：
 *
 * - 各域模块传给 `request()` 的就是 `/notes/${id}` 这样的字面量。把类型写成
 *   `BodyOf<'/notes/{note_id}', 'put'>`，**类型键与请求路径长得一样**，
 *   抄错端点用眼睛就能看出来；
 * - 按组件名索引（`Schema<'NoteUpdateRequest'>`）看不出"这个 body 属于哪个端点"，
 *   端点换了模型而这里没换，编译器也不会响 —— 这正是 S3 要消掉的那类静默。
 * ================================================================== */

/** 去掉 schema 路径的 `/api` 前缀（schema 是 `/api/notes`，前端 `request()` 收 `/notes`） */
type StripApiPrefix<K> = K extends `/api${infer Rest}` ? Rest : never;

/** 前端路径 → schema 路径项 的映射（键即 `request()` 里的路径字面量） */
type ApiPathMap = { [K in keyof paths as StripApiPrefix<K>]: paths[K] };

/** 前端 `request()` 接受的路径字面量（= schema 的全部路径去掉 `/api`） */
export type ApiPath = keyof ApiPathMap;

/**
 * 某个路径上 schema **真正声明了**的方法
 *
 * 路径项里没声明的方法写成 `get?: never` / `post?: never`，这里用
 * `{ responses: unknown }` 把它们滤掉，于是 `BodyOf<'/notes', 'post'>` 这类
 * "端点写错了"的用法会在**类型参数本身**报错。
 *
 * `-?` 不能省：映射类型会保留可选修饰符，索引出来的联合就变成 `'get' | undefined`。
 */
export type ApiMethod<P extends ApiPath> = {
  [M in Extract<keyof ApiPathMap[P], string>]-?: ApiPathMap[P][M] extends { responses: unknown }
    ? M
    : never;
}[Extract<keyof ApiPathMap[P], string>];

/** 端点（路径 + 方法）对应的 operation 类型 */
export type OperationOf<P extends ApiPath, M extends ApiMethod<P>> = Extract<
  ApiPathMap[P][M],
  { responses: unknown }
>;

/**
 * 剥掉 openapi-typescript 给可选属性显式加上的 `| undefined`
 *
 * 它在 `requestBody?: { … }` 这类位置写的是 `requestBody?: { … } | undefined`，
 * 于是 `Op extends { requestBody: { content: … } }` 这种最自然的写法**永远不成立**
 * ——请求体一律退化成 `never`，而 `never` 与"这个端点确实没有请求体"长得一样。
 * 这个坑是 S3 第一版实测撞到的（logout / restoreVersion / startUnderstanding 三个
 * "body 可选"的端点全被解析成 never，另 31 个正常）。
 */
type Defined<T> = Exclude<T, undefined>;

/** 从 operation 里取 JSON 请求体的 schema（没有 requestBody 时是 `never`） */
type JsonRequestBody<Op> = Op extends { requestBody?: infer RB }
  ? [Defined<RB>] extends [never]
    ? never
    : Defined<RB> extends { content: { 'application/json': infer B } }
      ? B
      : never
  : never;

/** 从 operation 里取 query 参数对象（没有 query 参数时是 `never`） */
type QueryParams<Op> = Op extends { parameters?: infer Params }
  ? Params extends { query?: infer Q }
    ? [Defined<Q>] extends [never]
      ? never
      : Defined<Q>
    : never
  : never;

/**
 * 端点的 JSON 请求体类型
 *
 * schema 没声明 `requestBody` 的端点是 `never` —— 给它标一个 body 会立刻
 * 编译失败（而不是悄悄发一个后端不认的字段）。
 *
 * @example
 * export type UpdateNotePayload = BodyOf<'/notes/{note_id}', 'put'>;
 */
export type BodyOf<P extends ApiPath, M extends ApiMethod<P>> = JsonRequestBody<ApiPathMap[P][M]>;

/**
 * 端点的 query 参数对象类型
 *
 * schema 没声明 query 参数（`query?: never`）时给 `Record<string, never>`，
 * 而不是 `never` —— 后者会让"这个端点没有 query"和"你写错了"长得一样。
 */
export type QueryOf<P extends ApiPath, M extends ApiMethod<P>> = [
  QueryParams<ApiPathMap[P][M]>,
] extends [never]
  ? Record<string, never>
  : QueryParams<ApiPathMap[P][M]>;

/**
 * 把契约里**带 `default`** 的请求体字段放宽成可选（阶段 5.1 / S3）
 *
 * ## 为什么需要它（这是生成类型唯一一处系统性偏离真实契约的地方）
 *
 * pydantic 把带默认值的字段**排除出** `required`（后端接受缺省），
 * 但 `openapi-typescript` 会按 `default` 把它们标回**必填**。
 * 对**响应**这是对的（一定会被序列化出来），对**请求**则更严：
 * 前端按真实契约完全合法的"我省略这个字段，让后端用它自己的默认值"
 * 会变成编译错误 —— 而"照编译器的要求把默认值抄一份发过去"更糟：
 * 后端哪天改了默认值，前端会**静默地覆盖**它。
 *
 * ## 为什么 `K` 必须手写
 *
 * "哪些字段带 default"只存在于 `backend/openapi.json` 里，生成的 TS 类型把它丢了。
 * 所以这里要求把字段名一个个列出来，并在调用处注明后端默认值。代价是"手写一份清单"，
 * 换来的是：`K` 受 `keyof BodyOf<P, M>` 约束，**拼错字段名会编译失败**，
 * 而"漏列一个"的后果只是那个字段仍然必填（编译器会立刻指出来）—— 两个方向都不会静默。
 *
 * @example
 * // 后端 `GoalCreateRequest`：`type: str = "weekly"`、`target_mastery: int = 80`
 * export type CreateGoalPayload = BodyWithDefaults<'/goals', 'post', 'type' | 'target_mastery'>;
 */
export type BodyWithDefaults<
  P extends ApiPath,
  M extends ApiMethod<P>,
  K extends keyof BodyOf<P, M>,
> = Omit<BodyOf<P, M>, K> & Partial<Pick<BodyOf<P, M>, K>>;

/**
 * 把契约里**带 `default`** 的 query 参数放宽成可选（与 `BodyWithDefaults` 同理）
 */
export type QueryWithDefaults<
  P extends ApiPath,
  M extends ApiMethod<P>,
  K extends keyof QueryOf<P, M>,
> = Omit<QueryOf<P, M>, K> & Partial<Pick<QueryOf<P, M>, K>>;
