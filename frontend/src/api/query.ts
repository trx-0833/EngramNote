/**
 * @file 查询串序列化（阶段 5.1 / S3b）
 *
 * ## 这个函数解决什么
 *
 * S3a 把**请求体**接上了契约，S3b 收的是另一半：**query 参数**。
 * 此前 18 个函数各自手拼查询串（`new URLSearchParams({…})` + 条件 `.set`、
 * 模板串 `?a=${x}&b=${y}`、三元拼串三种形态），参数名只有**事后**被
 * `openapi-drift.mjs` 扫字符串才发现写错 —— 而写错的运行时表现是
 * "参数被后端忽略"，页面照常渲染，没人会发现。
 *
 * 现在统一成：调用点写一个**带契约类型标注**的对象，交给这里序列化：
 *
 * ```ts
 * const query: QueryOf<'/notes', 'get'> = { page, page_size: pageSize, keyword, note_role: noteRole }
 * return request<NoteListResponse>(`/notes${buildQuery(query)}`)
 * ```
 *
 * ## 类型检查发生在**调用点**，不在这个函数里
 *
 * 本函数的参数类型是宽松的 `object` —— 它只负责序列化。参数名与取值域由调用点的
 * `QueryOf<P, M>` 标注保证（多写一个不存在的键会报 TS2561 "Did you mean…"）。
 * **这里刻意不做运行时校验**：schema 只存在于类型层，运行期没有它可比对；
 * 假装能校验只会给出虚假的安全感。
 *
 * ## 语义（每一条都有单测锁住，改动前先看 `query.test.ts`）
 *
 * 1. **键序 = 对象字面量的书写顺序** —— 迁移前的输出必须逐字一致，所以顺序是契约的一部分；
 * 2. `undefined` / `null` / `''` 的值**跳过**（迁移前普遍写作 `if (keyword) query.set(…)`，
 *    跳过空串正是那个行为）；
 * 3. `number` / `boolean` 一律 `String(v)` —— ⚠️ **`false` 照发**，不跳过：
 *    "false 不发"是调用点该显式表达的事（见 `purgeNote` 的 `x || undefined`），
 *    在这里跳过 `false` 会变成一条谁也想不起来的隐式规则；
 * 4. 数组 → **重复键**（`URLSearchParams.append`），元素按 1–3 过滤；
 * 5. 编码交给 `URLSearchParams`：空格编成 `+`、字面 `+` 编成 `%2B`、中文按 UTF-8 百分号编码
 *    —— 与后端 FastAPI 的 form 编码解码规则一致；
 * 6. 一个参数都没有时返回 **空串**（不是 `'?'`），于是调用点写成 `` `${path}${buildQuery(q)}` ``
 *    就与迁移前的 `x ? '?a=1' : ''` 形态完全一致；
 * 7. 遇到不支持的取值类型（对象、函数、symbol、bigint）**抛 `TypeError`**：
 *    静默 `String(v)` 会发出 `[object Object]` 这种东西，属于"看起来发了请求、
 *    其实发的是垃圾"，宁可当场炸。
 */

/** 把单个值追加进 `URLSearchParams`（数组递归，空值跳过，其它类型抛错） */
function appendValue(search: URLSearchParams, key: string, value: unknown): void {
  if (value === undefined || value === null || value === '') return;
  if (Array.isArray(value)) {
    for (const item of value) appendValue(search, key, item);
    return;
  }
  const kind = typeof value;
  if (kind === 'string' || kind === 'number' || kind === 'boolean') {
    search.append(key, String(value));
    return;
  }
  throw new TypeError(`buildQuery: 参数 ${key} 的取值类型不支持（${kind}）`);
}

/**
 * 把参数对象拼成查询串
 *
 * @param params - 参数对象；键名与取值域由调用点的 `QueryOf<P, M>` 标注保证
 * @returns `'?a=1&b=2'`，或一个参数都没有时的 `''`
 * @throws {TypeError} 取值类型不受支持（对象/函数/symbol/bigint）时
 */
export function buildQuery(params: object): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    appendValue(search, key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}
