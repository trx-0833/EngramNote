/**
 * @file 生成类型入口的守卫（阶段 5.1 / S3）
 *
 * ## 这个文件守卫两件不同的事
 *
 * 1. **类型层面**：`BodyOf` / `QueryOf` / `ApiMethod` 真的取到了契约里的形状。
 *    下面的断言全是**编译期**的 —— 任何一项不成立，`tsc` 就直接失败
 *    （`npm run build` 的第一步就是 `tsc`），不需要开 vitest 的 `--typecheck`。
 *
 *    为什么非要有这些断言：三个 helper 全是"条件类型套索引"，
 *    写错一个 `infer` 位置就会**静默退化成 `never`**（或退化成 `any`）；
 *    而 `never` 在参数位置上的表现是"什么都不能传"，与"这个端点确实没有请求体"
 *    长得一模一样 —— 没有断言的话，S3 之后所有 body 标注会**看起来都对**。
 *
 *    这里不用 vitest 的 `expectTypeOf`（那个 API 在 2.x 里要传值，
 *    写成 `expectTypeOf<T>()` 会报"Expected 1 arguments"），
 *    改用最朴素的三条断言与一个 `AssertTrue` 约束 —— 约束不满足时报错信息里
 *    会直接写着是**哪一项**断言的类型不对。
 * 2. **运行期**：`generated/` 必须**一个运行期导出都没有**。
 *    类型导入漏写成值导入时，打包器会留下一个空模块 —— 体积上无所谓，
 *    但它意味着 `import type` 的纪律破了一个口子，而这道口子正是
 *    "生成物被当成运行期契约"的开端。
 */
import { describe, expect, it } from 'vitest';

import type { ApiMethod, ApiPath, BodyOf, BodyWithDefaults, QueryOf } from './types';

describe('生成类型入口', () => {
  it('types.ts 没有运行期导出（必须被构建完全擦除）', async () => {
    const mod = await import('./types');
    expect(Object.keys(mod)).toEqual([]);
  });

  it('schema.ts 也没有运行期导出', async () => {
    const mod = await import('./schema');
    expect(Object.keys(mod)).toEqual([]);
  });
});

// --- 以下全是编译期断言，运行期什么都不做 ---

/** `A` 与 `B` 互相可赋值（即"同一个类型"） */
type Equal<A, B> =
  (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;

/** `A` 是 `B` 的子类型 */
type Extends<A, B> = [A] extends [B] ? true : false;

/** `K` 是 `T` 的键 */
type HasKey<T, K extends PropertyKey> = K extends keyof T ? true : false;

/**
 * `K` 在 `T` 里是否**可选**
 *
 * 刻意不写成 `{} extends Pick<T, K>`：那个"空对象类型"会被 eslint 的
 * `@typescript-eslint/no-empty-object-type` 拦下来。这里用"可选 vs 必填"的
 * 可赋值关系来判断，效果相同且不需要空对象类型字面量。
 */
type IsOptionalKey<T, K extends keyof T> = Pick<T, K> extends Required<Pick<T, K>> ? false : true;

/** 布尔取反（用在断言元组里） */
type Not<T extends boolean> = T extends true ? false : true;

/** 约束不满足时报错信息里会指出是哪一项 */
type AssertTrue<T extends true> = T;

/**
 * 全端点扫描：把"退化成 `unknown`"的端点名列出来（应当**一个都没有**）
 *
 * `never extends { … } ? infer B : …` 在 `B` **推不出候选**时给出的是 `unknown`，
 * 不是 `never` —— 于是"没有请求体"会变成"什么都能传"。这条扫描就是防它：
 * 只要有任何一个端点的 `BodyOf` / `QueryOf` 退化成 `unknown`，
 * 报错信息里会直接写着是哪个路径的哪个方法。
 *
 * （实测：这个坑真踩到了 —— `BodyOf<'/notes','get'>` 一度就是 `unknown`。）
 */
type UnknownBodyEndpoints = {
  [P in ApiPath]: {
    [M in ApiMethod<P>]: Equal<BodyOf<P, M>, unknown> extends true ? `${P} ${M}` : never;
  }[ApiMethod<P>];
}[ApiPath];

type UnknownQueryEndpoints = {
  [P in ApiPath]: {
    [M in ApiMethod<P>]: Equal<QueryOf<P, M>, unknown> extends true ? `${P} ${M}` : never;
  }[ApiMethod<P>];
}[ApiPath];
/** 该路径上真的有请求体的端点（用来证明下面的"没有退化成 unknown"不是空转） */
type EndpointsWithBody = {
  [P in ApiPath]: {
    [M in ApiMethod<P>]: [BodyOf<P, M>] extends [never] ? never : true;
  }[ApiMethod<P>];
}[ApiPath];

/** 该路径上真的有 query 参数的端点 */
type EndpointsWithQuery = {
  [P in ApiPath]: {
    [M in ApiMethod<P>]: Equal<QueryOf<P, M>, Record<string, never>> extends true ? never : true;
  }[ApiMethod<P>];
}[ApiPath];

/**
 * 逐项断言（顺序与注释一一对应）
 *
 * 这一整个元组就是"断言被执行"的证据：`noUnusedLocals` 下，
 * 断言不挂在导出的类型上就会变成死代码，而死掉的断言等于没有断言。
 */
export type TypeAssertions = [
  // 1. 路径键就是前端传给 request() 的形状（去掉了 /api 前缀）
  AssertTrue<Extends<'/notes', ApiPath>>,
  AssertTrue<Extends<'/notes/{note_id}', ApiPath>>,
  // 2. `ApiPath` 里不存在带前缀的键（否则"路径写错也不报错"）
  AssertTrue<Equal<Extract<ApiPath, `/api${string}`>, never>>,
  // 3. 只收 schema 声明过的方法：`/notes` 只有 GET（POST 在路径项里是 `post?: never`）
  AssertTrue<Equal<ApiMethod<'/notes'>, 'get'>>,
  // 4. 没有请求体的端点必须是 never（否则"给无 body 的端点标 body"不会报错）
  AssertTrue<Equal<BodyOf<'/notes', 'get'>, never>>,
  // 4b. …而且**全库**都不许退化成 unknown（"没有请求体"必须等于 never）
  AssertTrue<Equal<UnknownBodyEndpoints, never>>,
  AssertTrue<Equal<UnknownQueryEndpoints, never>>,
  // 4c. 非空转守卫：真有请求体、真有 query 的端点必须存在（否则上面两条是空扫描）
  AssertTrue<Equal<EndpointsWithBody, never> extends true ? false : true>,
  AssertTrue<Equal<EndpointsWithQuery, never> extends true ? false : true>,
  // 5. body 取的是**这个端点**的请求体，而不是碰巧同形状的别的端点
  AssertTrue<HasKey<BodyOf<'/goals', 'post'>, 'deadline'>>,
  AssertTrue<HasKey<BodyOf<'/notes/{note_id}', 'put'>, 'title'>>,
  AssertTrue<HasKey<BodyOf<'/review/submit', 'post'>, 'quiz_id'>>,
  // 5b. body 可选的端点也要取到（openapi-typescript 在那里写的是 `… | undefined`，
  //     这是 S3 第一版把 logout 解析成 never 的原因）
  AssertTrue<HasKey<BodyOf<'/auth/logout', 'post'>, 'all_devices'>>,
  AssertTrue<HasKey<BodyOf<'/understanding/{note_id}/start', 'post'>, 'confirm'>>,
  // 6. query 取的是这个端点的参数（`project_id` 是契约里有、前端此前没传过的那个）
  AssertTrue<HasKey<QueryOf<'/notes', 'get'>, 'project_id'>>,
  AssertTrue<HasKey<QueryOf<'/notes', 'get'>, 'page_size'>>,
  // 7. 没有 query 参数的端点是空对象，而不是 never
  AssertTrue<Equal<QueryOf<'/report/daily', 'get'>, Record<string, never>>>,
  // 8. 带 default 的字段按真实契约放宽成可选……
  AssertTrue<IsOptionalKey<BodyWithDefaults<'/goals', 'post', 'type'>, 'type'>>,
  // 8b. ……而放宽前它是必填（证明这一步真的放宽了，不是空转）
  AssertTrue<Not<IsOptionalKey<BodyOf<'/goals', 'post'>, 'type'>>>,
  // 8c. 放宽只影响列出的字段：`name` 仍然必填
  AssertTrue<Not<IsOptionalKey<BodyWithDefaults<'/goals', 'post', 'type'>, 'name'>>>,
];
