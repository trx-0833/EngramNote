#!/usr/bin/env node
/* global console, process */
/**
 * @file OpenAPI 契约漂移检查（overhaul-plan 阶段 5.1 的证据生成器）
 *
 * ## 它回答什么问题
 *
 * 阶段 5.1 要把 `frontend/src/api/*.ts` 里手写的 API 函数换成由
 * `backend/openapi.json` 生成的类型。做这件事之前必须先知道**两边差在哪** ——
 * 否则迁移就成了一次"改完再跑 E2E 看哪炸了"的赌博。
 *
 * 本脚本用 **TypeScript 编译器 API**（不是正则、不是人眼）回答三个问题：
 *
 * 1. **端点在不在**：每个手写函数调的「路径 + 方法」，在 schema 里存在吗？
 * 2. **类型对不对**：把「手写函数的返回类型」与「schema 里同端点的响应类型」
 *    交给编译器做**双向可赋值性**判断。
 * 3. **错在哪**：对不一致的，再做一次**结构化 diff**（逐属性递归），
 *    指出是缺字段、多了字段、可空性不一致、schema 用了枚举而前端写成 `string`，
 *    还是「数组 vs `{items,total}` 信封」这类形状错误。
 *
 * 除此之外还机械地比对了：请求体字段、query 参数、以及 schema 里
 * **没有任何前端调用方**的端点。
 *
 * ## 为什么不用正则
 *
 * 手写客户端的路径既有字符串字面量（`'/auth/me'`）也有模板串
 * （`` `/notes/${noteId}/versions/${v}` ``），返回类型既有具名 interface
 * （`Promise<Note>`）也有内联字面量类型（`Promise<{ id: string; ... }>`）。
 * 正则只能覆盖前者，且会把注释里的路径也算进来。AST + 类型检查器
 * 覆盖全部形态，且**结论可复核**（`--emit-probes` 会把喂给编译器的
 * 探针文件原样吐出来）。
 *
 * ## 判定口径
 *
 * 记 `HW` = 手写函数的返回类型，`Schema` = schema 里同端点的响应类型：
 *
 * | 现象 | 判定 | 含义 |
 * |---|---|---|
 * | 两个方向都可赋值 | `IDENTICAL` | 两边等价 |
 * | 只有 `HW ⊆ Schema` | `HW_NARROWER` | 前端声明得比后端**少**（后端多给的字段前端不知道） |
 * | 只有 `Schema ⊆ HW` | `HW_WIDER` | 前端声明得比后端**宽**（典型：schema 是枚举，前端写 `string`） |
 * | 两个方向都不行 | `CONFLICT` | 其中必有一方描述错了现实 |
 *
 * ⚠️ 结构类型系统下，"更宽/更窄"只说明**类型层面**的关系，不代表运行时的值
 * 真的会越界。本脚本只报告关系，不下"线上一定炸"的结论。
 *
 * ## 几类不是漂移、但会被编译器报出来的情况（已单独分类，避免污染计数）
 *
 * - `SCHEMA_LOOSE`：schema 那边是 `additionalProperties: true` 的空壳对象。
 *   TS 的 interface **不会**自动获得隐式索引签名，于是
 *   `AssessmentScores` 这种 interface 无法赋给 `{[k: string]: unknown}` ——
 *   这是 schema 没写清楚，不是前端写错。
 * - `SCHEMA_UNTYPED`：schema 该端点根本没有响应模型（FastAPI 返回 `{}`，
 *   典型是 SSE 流式端点）。
 * - `HW_DISCARDS_BODY`：手写函数返回 `Promise<void>`（主动丢弃响应体）。
 * - `NO_JSON_RESPONSE`：schema 是 204，本来就没有响应体。
 *
 * ## 用法
 *
 *     npm run gen:api:drift                       # 打印 Markdown 报告
 *     npm run gen:api:drift -- --json=out.json    # 机器可读
 *     node src/api/generated/openapi-drift.mjs --debug --emit-probes=out.ts
 *
 * ## 局限（写在这里，避免读者高估结论）
 *
 * - **query 参数**是从函数体里扫 `URLSearchParams` / `.set()` / 字面量
 *   `?a=b` 得到的，不做数据流分析：参数名若只在运行时拼出来，扫不到。
 *   另外 multipart（`uploadRequest`）的 FormData 字段名不是 query 参数，已跳过。
 * - **请求体**只描述顶层字段（数组元素下探一层）。更深的嵌套要人看。
 * - **结构化 diff 只用于解释**，不参与判定；判定只来自编译器。
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const HERE = path.dirname(fileURLToPath(import.meta.url)); // frontend/src/api/generated
const API_DIR = path.resolve(HERE, '..'); // frontend/src/api
const FRONTEND_DIR = path.resolve(HERE, '../../..'); // frontend
const REPO_ROOT = path.resolve(HERE, '../../../..'); // repo root

const SCHEMA_JSON = path.join(REPO_ROOT, 'backend', 'openapi.json');
const SCHEMA_TS = path.join(HERE, 'schema.ts');
const VIRTUAL_FILE = path.join(HERE, '__openapi_drift__.ts');

const HTTP_METHODS = ['get', 'post', 'put', 'patch', 'delete', 'options', 'head', 'trace'];
const REQUEST_HELPERS = new Set(['request', 'uploadRequest', 'authorizedFetch']);

/* ================================================================== *
 * 一、schema 索引
 * ================================================================== */

const openapi = JSON.parse(fs.readFileSync(SCHEMA_JSON, 'utf8'));

/** 把 `/api/notes/{note_id}` 归一成 `/api/notes/{}`，用于与前端路径做结构比对 */
const normalizePath = (p) => p.replace(/\{[^}]*\}/g, '{}');

const schemaPaths = new Map();
for (const [p, item] of Object.entries(openapi.paths ?? {})) {
  schemaPaths.set(normalizePath(p), {
    raw: p,
    methods: new Set(HTTP_METHODS.filter((m) => item[m])),
    item,
  });
}

function resolveRef(node) {
  if (!node || typeof node !== 'object') return node;
  if (typeof node.$ref === 'string') {
    const m = /^#\/components\/schemas\/(.+)$/.exec(node.$ref);
    if (m) return openapi.components?.schemas?.[m[1]] ?? null;
  }
  return node;
}

/** 取某个操作的 2xx JSON 响应 schema */
function jsonResponseSchema(op) {
  const codes = Object.keys(op.responses ?? {})
    .filter((c) => /^2\d\d$/.test(c))
    .sort();
  for (const code of codes) {
    for (const [media, body] of Object.entries(op.responses[code]?.content ?? {})) {
      if (media === 'application/json' || media.endsWith('+json')) {
        const raw = body.schema ?? null;
        return {
          code,
          media,
          schema: resolveRef(raw),
          // openapi-typescript 对 inline 的 FastAPI 响应会自己起一个标题，
          // 有 $ref 的则直接用组件名 —— 两者都记下来供报告展示
          refName: raw?.$ref
            ? raw.$ref.replace('#/components/schemas/', '')
            : (raw?.title ?? null),
        };
      }
    }
  }
  return { code: codes[0] ?? null, media: null, schema: null, refName: null };
}

/* ================================================================== *
 * 二、AST：从手写客户端里挖出"真正发起请求"的导出函数
 * ================================================================== */

function calleeName(expr) {
  if (ts.isIdentifier(expr)) return expr.text;
  if (ts.isPropertyAccessExpression(expr)) return expr.name.text;
  return null;
}

/** `${query}` 这种插值是不是"查询串后缀"（`const query = status ? '?status=…' : ''`） */
function isQuerySuffix(expr, fnBody) {
  if (!ts.isIdentifier(expr)) return false;
  const name = expr.text;
  let found = false;
  const scanInit = (n) => {
    if (found) return;
    if (ts.isStringLiteralLike(n) && n.text.startsWith('?')) found = true;
    if (ts.isTemplateExpression(n) && n.head.text.startsWith('?')) found = true;
    ts.forEachChild(n, scanInit);
  };
  const scan = (node) => {
    if (found) return;
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.name.text === name &&
      node.initializer
    ) {
      scanInit(node.initializer);
    }
    ts.forEachChild(node, scan);
  };
  if (fnBody) scan(fnBody);
  return found;
}

/**
 * 路径表达式 → 模板串（插值统一记成 `{}`）
 *
 * 查询串会被截掉：既处理 `` `/x?a=${b}` ``（模板头里带 `?`），
 * 也处理 `` `/goals${query}` ``（查询串整个来自一个变量）。
 */
function renderPath(expr, fnBody) {
  if (ts.isStringLiteral(expr) || ts.isNoSubstitutionTemplateLiteral(expr)) {
    return { template: expr.text.split('?')[0], hadQuery: expr.text.includes('?') };
  }
  if (ts.isTemplateExpression(expr)) {
    let out = expr.head.text;
    let hadQuery = expr.head.text.includes('?');
    for (const span of expr.templateSpans) {
      if (!hadQuery && isQuerySuffix(span.expression, fnBody)) {
        hadQuery = true;
        break;
      }
      out += '{}' + span.literal.text;
      if (span.literal.text.includes('?')) hadQuery = true;
    }
    return { template: hadQuery ? out.split('?')[0] : out, hadQuery };
  }
  return null;
}

function readMethod(node) {
  if (!node || !ts.isObjectLiteralExpression(node)) return 'GET';
  for (const prop of node.properties) {
    if (
      ts.isPropertyAssignment(prop) &&
      ts.isIdentifier(prop.name) &&
      prop.name.text === 'method' &&
      ts.isStringLiteralLike(prop.initializer)
    ) {
      return prop.initializer.text.toUpperCase();
    }
  }
  return 'GET';
}

/** 扫函数体，收集 query 参数名（best-effort，见文件头"局限"） */
function collectQueryNames(body) {
  const names = new Set();
  const visit = (node) => {
    if (ts.isNewExpression(node) && calleeName(node.expression) === 'URLSearchParams') {
      const arg = node.arguments?.[0];
      if (arg && ts.isObjectLiteralExpression(arg)) {
        for (const prop of arg.properties) {
          if (ts.isPropertyAssignment(prop) && ts.isIdentifier(prop.name)) names.add(prop.name.text);
          if (ts.isShorthandPropertyAssignment(prop)) names.add(prop.name.text);
        }
      }
    }
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)) {
      const fn = node.expression.name.text;
      if ((fn === 'set' || fn === 'append') && ts.isStringLiteralLike(node.arguments[0])) {
        names.add(node.arguments[0].text);
      }
    }
    if (ts.isStringLiteralLike(node) || ts.isTemplateExpression(node)) {
      const text = ts.isTemplateExpression(node)
        ? node.head.text + node.templateSpans.map((s) => '{}' + s.literal.text).join('')
        : node.text;
      for (const m of text.matchAll(/[?&]([A-Za-z_][A-Za-z0-9_]*)=/g)) names.add(m[1]);
    }
    ts.forEachChild(node, visit);
  };
  if (body) visit(body);
  return names;
}

/** options 里的 `body:`，剥掉 `JSON.stringify(...)` 外壳 */
function readBodyExpr(node) {
  if (!node || !ts.isObjectLiteralExpression(node)) return null;
  for (const prop of node.properties) {
    if (ts.isPropertyAssignment(prop) && ts.isIdentifier(prop.name) && prop.name.text === 'body') {
      const init = prop.initializer;
      if (
        ts.isCallExpression(init) &&
        calleeName(init.expression) === 'stringify' &&
        init.arguments.length === 1
      ) {
        return init.arguments[0];
      }
      return init;
    }
  }
  return null;
}

const apiModules = fs
  .readdirSync(API_DIR)
  .filter((f) => f.endsWith('.ts') && !f.endsWith('.d.ts') && !f.includes('.test.'))
  .sort();
const ROOT_FILES = apiModules.map((f) => path.join(API_DIR, f));

function collectCalls(program) {
  const out = [];
  for (const file of ROOT_FILES) {
    const sf = program.getSourceFile(file);
    if (!sf) continue;
    const rel = path.relative(FRONTEND_DIR, file).replace(/\\/g, '/');

    for (const stmt of sf.statements) {
      if (!ts.isFunctionDeclaration(stmt) || !stmt.name) continue;
      if ((ts.getCombinedModifierFlags(stmt) & ts.ModifierFlags.Export) === 0) continue;

      let call = null;
      const walk = (node) => {
        if (call) return;
        if (ts.isCallExpression(node)) {
          const name = calleeName(node.expression);
          if (name && REQUEST_HELPERS.has(name) && node.arguments.length > 0) {
            const rendered = renderPath(node.arguments[0], stmt.body);
            if (rendered) {
              call = { name, node, rendered };
              return;
            }
          }
        }
        ts.forEachChild(node, walk);
      };
      if (stmt.body) ts.forEachChild(stmt.body, walk);
      if (!call) continue;

      const optionsNode = call.node.arguments[1] ?? null;
      out.push({
        modulePath: file,
        module: rel,
        fn: stmt.name.text,
        helper: call.name,
        path: rendered_path(call.rendered),
        hadQuery: call.rendered.hadQuery,
        method: call.name === 'uploadRequest' ? 'POST' : readMethod(optionsNode ?? undefined),
        queryNames: [...collectQueryNames(stmt.body)].sort(),
        bodyNode: readBodyExpr(optionsNode ?? undefined),
        declNode: stmt,
        sourceFile: sf,
      });
    }
  }
  return out;
}
const rendered_path = (r) => r.template;

/* ================================================================== *
 * 三、编译器程序（两段式）
 *
 * ⚠️ 这里有个必须记住的坑：探针文件的内容依赖"有哪些调用"，
 * 而"有哪些调用"又来自 program。若先 createProgram 再填探针内容，
 * program 里那份探针文件是**空的** —— 诊断数 0，于是每个函数都被判成
 * IDENTICAL。这正是本脚本第一版踩到的坑，下面的空转守卫就是防它回来。
 * ================================================================== */

const compilerOptions = {
  target: ts.ScriptTarget.ES2020,
  lib: ['lib.es2020.d.ts', 'lib.dom.d.ts', 'lib.dom.iterable.d.ts'],
  module: ts.ModuleKind.ESNext,
  moduleResolution: ts.ModuleResolutionKind.Bundler,
  jsx: ts.JsxEmit.ReactJSX,
  strict: true,
  skipLibCheck: true,
  noEmit: true,
  // 刻意关掉：探针文件里全是"只声明不使用"的类型与常量
  noUnusedLocals: false,
  noUnusedParameters: false,
  types: [],
  allowImportingTsExtensions: true,
  forceConsistentCasingInFileNames: true,
};

function makeHost(getVirtualText) {
  const host = ts.createCompilerHost(compilerOptions, true);
  const fileExists = host.fileExists.bind(host);
  const readFile = host.readFile.bind(host);
  const getSourceFile = host.getSourceFile.bind(host);
  host.fileExists = (f) => (path.resolve(f) === VIRTUAL_FILE ? true : fileExists(f));
  host.readFile = (f) => (path.resolve(f) === VIRTUAL_FILE ? getVirtualText() : readFile(f));
  host.getSourceFile = (f, lv, onError, shouldCreate) =>
    path.resolve(f) === VIRTUAL_FILE
      ? ts.createSourceFile(f, getVirtualText(), lv, true)
      : getSourceFile(f, lv, onError, shouldCreate);
  return host;
}

// --- 第一段：只为拿到"有哪些调用"，用来生成探针 ---
let virtualText = '';
const programA = ts.createProgram({
  rootNames: ROOT_FILES,
  options: compilerOptions,
  host: makeHost(() => ''),
});
const callsA = collectCalls(programA);

// --- 生成探针 ---
const lines = [];
const probes = new Map(); // 行号(1-based) → { id, fn, module, role }
const push = (line, meta) => {
  lines.push(line);
  if (meta) probes.set(lines.length, meta);
  return lines.length;
};

const moduleAlias = new Map();
for (const p of ROOT_FILES) {
  const rel = './' + path.relative(HERE, p).replace(/\\/g, '/').replace(/\.ts$/, '');
  moduleAlias.set(p, `__mod_${moduleAlias.size}`);
  push(`import * as ${moduleAlias.get(p)} from '${rel}';`, null);
}
push(`import type { paths } from './schema';`, null);
push('', null);

const q = (s) => `'${s.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;

/** 每个调用的 schema 侧信息（判定与报告都用它） */
for (const c of callsA) {
  const schemaEntry = schemaPaths.get(normalizePath('/api' + c.path));
  const op = schemaEntry?.item?.[c.method.toLowerCase()] ?? null;
  c.schemaPath = schemaEntry?.raw ?? null;
  c.schemaHasPath = Boolean(schemaEntry);
  c.schemaHasOperation = Boolean(op);
  c.op = op;
  c.id = callsA.indexOf(c);

  if (!op) continue;
  const resp = jsonResponseSchema(op);
  c.responseCode = resp.code;
  c.responseMedia = resp.media;
  if (!resp.schema) continue;

  const alias = moduleAlias.get(c.modulePath);
  const hwLine = push(`type __HW_${c.id} = Awaited<ReturnType<typeof ${alias}.${c.fn}>>;`, {
    id: c.id,
    fn: c.fn,
    module: c.module,
    role: 'hw-type',
  });
  const scLine = push(
    `type __SC_${c.id} = paths[${q(c.schemaPath)}][${q(c.method.toLowerCase())}]['responses'][${resp.code}]['content'][${q(resp.media)}];`,
    { id: c.id, fn: c.fn, module: c.module, role: 'sc-type' },
  );
  push(`declare const __hwv_${c.id}: __HW_${c.id};`, null);
  push(`declare const __scv_${c.id}: __SC_${c.id};`, null);
  push(`const __hwToSc_${c.id}: __SC_${c.id} = __hwv_${c.id};`, {
    id: c.id,
    fn: c.fn,
    module: c.module,
    role: 'hw-to-schema',
  });
  push(`const __scToHw_${c.id}: __HW_${c.id} = __scv_${c.id};`, {
    id: c.id,
    fn: c.fn,
    module: c.module,
    role: 'schema-to-hw',
  });
  push('', null);
  c.probe = { id: c.id, hwLine, scLine };
}

/* ------------------------------------------------------------------ *
 * 空转金丝雀（阶段 5.1 / S3 加）
 *
 * 原来的守卫是"有探针但**一条诊断都没有** ⇒ 探针没被真正检查"。
 * S3 把 `deleteAnnotation` 修好之后，`compilerDiagnostics` 正好归零 ——
 * 于是守卫开始**误报**：它把"目标状态"当成了"故障症状"。
 *
 * 正确的做法不是放宽守卫（那等于把空转守卫删掉），而是让"探针真的被检查"
 * 这件事**自带证据**：塞一个**故意写错**的探针（把某个 schema 响应类型赋给
 * `never`，这永远不成立），并要求它必须报错。
 * 它不参与任何指标（`role: 'canary'` 的诊断在 summary 里被排除），
 * 只回答一个问题：**这个虚拟文件到底有没有被编译器看**。
 * ------------------------------------------------------------------ */
const canaryCall = callsA.find((c) => c.probe);
let canaryLine = null;
if (canaryCall) {
  push(`declare const __canarySrc: __SC_${canaryCall.id};`, null);
  canaryLine = push(`const __canaryMustFail: never = __canarySrc;`, {
    id: -1,
    fn: '(canary)',
    module: '(canary)',
    role: 'canary',
  });
}

virtualText = lines.join('\n') + '\n';

// --- 第二段：真正的程序（含探针） ---
const program = ts.createProgram({
  rootNames: [...ROOT_FILES, SCHEMA_TS, VIRTUAL_FILE],
  options: compilerOptions,
  host: makeHost(() => virtualText),
});
const checker = program.getTypeChecker();
const calls = collectCalls(program);

// 两段必须得到同样的调用序列，否则 id 会对错行
if (calls.length !== callsA.length) {
  throw new Error(`两次 AST 遍历结果不一致：${calls.length} vs ${callsA.length}`);
}
for (let i = 0; i < calls.length; i++) {
  const a = callsA[i];
  const b = calls[i];
  Object.assign(b, {
    schemaPath: a.schemaPath,
    schemaHasPath: a.schemaHasPath,
    schemaHasOperation: a.schemaHasOperation,
    op: a.op,
    responseCode: a.responseCode,
    responseMedia: a.responseMedia,
    probe: a.probe,
    id: a.id,
  });
  if (a.fn !== b.fn || a.path !== b.path) {
    throw new Error(`两次 AST 遍历顺序不一致：${a.fn} vs ${b.fn}`);
  }
}

const virtualSource = program.getSourceFile(VIRTUAL_FILE);
// ⚠️ 路径比对必须归一化：TS 内部把路径统一成 `/` 分隔，
// 而 VIRTUAL_FILE 是 Windows 的 `\` —— 直接用 `===` 比会让所有诊断被过滤掉，
// 表现就是"0 诊断、全部 IDENTICAL"（第一版踩的第二个坑）。
const samePath = (a, b) => String(a).replace(/\\/g, '/').toLowerCase() === String(b).replace(/\\/g, '/').toLowerCase();

const diagnostics = ts
  .getPreEmitDiagnostics(program, virtualSource)
  .filter((d) => d.file && samePath(d.file.fileName, VIRTUAL_FILE));

/** 行号 → 诊断消息 */
const diagByLine = new Map();
for (const d of diagnostics) {
  const line = virtualSource.getLineAndCharacterOfPosition(d.start ?? 0).line + 1;
  if (!diagByLine.has(line)) diagByLine.set(line, []);
  diagByLine.get(line).push(ts.flattenDiagnosticMessageText(d.messageText, ' '));
}

const diagAtProbe = (id, role) => {
  const out = [];
  for (const [line, msgs] of diagByLine) {
    const probe = probes.get(line);
    if (probe && probe.id === id && probe.role === role) out.push(...msgs);
  }
  return out;
};

const probedCount = calls.filter((c) => c.probe).length;

/** 诊断按"属于谁"分开：金丝雀的报错不算指标，真实探针的报错才算 */
const diagLineOf = (d) => virtualSource.getLineAndCharacterOfPosition(d.start ?? 0).line + 1;
const canaryDiagnostics = diagnostics.filter((d) => probes.get(diagLineOf(d))?.role === 'canary');
/** 真实缺陷诊断（不含金丝雀）——`compilerDiagnostics` 用的是这个 */
const realDiagnostics = diagnostics.filter((d) => probes.get(diagLineOf(d))?.role !== 'canary');

/**
 * 空转守卫：探针文件若为空 / 没被真正检查，"全部一致"就是个**假结论**。
 *
 * ⚠️ **2026-09-14（S3）改判据**：原来判的是"有探针却 0 诊断"，而 S3 把最后两条
 * 真实诊断（`deleteAnnotation` 的 void 双向不兼容）修掉之后，0 诊断成了**目标状态**
 * —— 旧判据把目标状态误报成故障。现在判的是**金丝雀必须报错**：
 * 它是一条故意写错的探针（`… : never = <某个 schema 响应类型>`），
 * 只要编译器真的在看这个文件，它必然报错。
 */
const spinGuardTripped = probedCount > 0 && canaryDiagnostics.length === 0;

/* ================================================================== *
 * 四、结构化 diff（只用于解释，不参与判定）
 * ================================================================== */

/** JSON Schema → 归一化描述子 */
function descFromJsonSchema(node, depth = 0) {
  if (!node || depth > 6) return { kind: 'unknown' };
  const raw = node;
  node = resolveRef(node);
  if (!node) return { kind: 'unknown' };

  if (node.anyOf || node.oneOf) {
    const members = (node.anyOf ?? node.oneOf).map((m) => descFromJsonSchema(m, depth + 1));
    const nonNull = members.filter((m) => m.kind !== 'null');
    const nullable = nonNull.length !== members.length;
    if (nonNull.length === 1 && nonNull[0].kind !== 'unknown') {
      return { ...nonNull[0], nullable: nullable || Boolean(nonNull[0].nullable) };
    }
    return { kind: 'union', members: nonNull, nullable };
  }
  if (node.allOf) {
    const props = {};
    let nullable = false;
    for (const part of node.allOf) {
      const d = descFromJsonSchema(part, depth + 1);
      if (d.kind === 'object') Object.assign(props, d.props);
      if (d.nullable) nullable = true;
    }
    return { kind: 'object', props, nullable };
  }
  if (Array.isArray(node.enum)) {
    const values = node.enum.filter((v) => typeof v === 'string');
    if (values.length === node.enum.length) return { kind: 'literals', values };
  }
  if (node.type === 'null') return { kind: 'null' };
  if (node.type === 'array') {
    return {
      kind: 'array',
      element: descFromJsonSchema(node.items ?? {}, depth + 1),
      nullable: Boolean(node.nullable),
    };
  }
  if (node.type === 'object' || node.properties) {
    if (!node.properties) {
      // 只有 additionalProperties 的空壳对象（pydantic 的 dict[str, Any]）
      return { kind: 'loose', nullable: Boolean(node.nullable) };
    }
    const props = {};
    for (const [k, v] of Object.entries(node.properties)) {
      props[k] = descFromJsonSchema(v, depth + 1);
    }
    return { kind: 'object', props, required: new Set(node.required ?? []) };
  }
  if (nodeRefIsEmpty(raw)) return { kind: 'untyped' };
  if (['string', 'number', 'integer', 'boolean'].includes(node.type)) {
    return { kind: node.type === 'integer' ? 'number' : node.type };
  }
  return { kind: 'unknown' };
}
/** `{}`（无 type / 无 properties / 无 $ref）→ 后端根本没声明响应模型 */
const nodeRefIsEmpty = (n) =>
  n && typeof n === 'object' && !n.$ref && !n.type && !n.properties && !n.anyOf && !n.enum;

/** TS Type → 归一化描述子（与上面同构） */
function descFromTsType(type, depth = 0, seen = new Set()) {
  if (!type || depth > 6) return { kind: 'unknown' };
  if (type.flags & ts.TypeFlags.Any) return { kind: 'any' };
  if (type.flags & ts.TypeFlags.Unknown) return { kind: 'unknown' };
  if (type.flags & ts.TypeFlags.Null) return { kind: 'null' };
  if (type.flags & (ts.TypeFlags.Undefined | ts.TypeFlags.Void)) return { kind: 'undefined' };
  if (type.flags & ts.TypeFlags.StringLiteral) return { kind: 'literals', values: [type.value] };
  if (type.isUnion()) {
    const members = type.types.map((t) => descFromTsType(t, depth + 1, seen));
    const nonNull = members.filter((m) => m.kind !== 'null');
    const nullable = nonNull.length !== members.length;
    const optional = members.some((m) => m.kind === 'undefined');
    const meaningful = nonNull.filter((m) => m.kind !== 'undefined');
    if (meaningful.length > 1 && meaningful.every((m) => m.kind === 'literals')) {
      return { kind: 'literals', values: meaningful.flatMap((m) => m.values), nullable, optional };
    }
    if (meaningful.length === 1) return { ...meaningful[0], nullable, optional };
    if (meaningful.length === 0) return { kind: 'undefined' };
    return { kind: 'union', members: meaningful, nullable, optional };
  }
  if (type.flags & ts.TypeFlags.String) return { kind: 'string' };
  if (type.flags & ts.TypeFlags.Number) return { kind: 'number' };
  if (type.flags & ts.TypeFlags.Boolean) return { kind: 'boolean' };
  if (checker.isArrayType(type) || checker.isTupleType(type)) {
    const el = checker.getTypeArguments(type)[0];
    return { kind: 'array', element: el ? descFromTsType(el, depth + 1, seen) : { kind: 'unknown' } };
  }
  if (checker.isArrayLikeType(type)) {
    const el = checker.getIndexTypeOfType(type, ts.IndexKind.Number);
    return { kind: 'array', element: el ? descFromTsType(el, depth + 1, seen) : { kind: 'unknown' } };
  }
  const name = type.getSymbol()?.getName?.();
  if (type.id != null && seen.has(type.id)) return { kind: 'object', props: {}, cyclic: true };
  const nextSeen = new Set(seen);
  if (type.id != null) nextSeen.add(type.id);

  const props = {};
  const required = new Set();
  for (const p of checker.getPropertiesOfType(type)) {
    const decl = p.valueDeclaration ?? p.declarations?.[0];
    const anchor = decl ?? type.symbol?.valueDeclaration ?? virtualSource;
    const pt = checker.getTypeOfSymbolAtLocation(p, anchor);
    const d = descFromTsType(pt, depth + 1, nextSeen);
    props[p.getName()] = d;
    if (!(p.flags & ts.SymbolFlags.Optional)) required.add(p.getName());
  }
  if (Object.keys(props).length === 0) return { kind: 'unknown', typeName: name };
  return { kind: 'object', props, required, typeName: name };
}

const MAX_DEPTH = 4;

/** 递归 diff：hw 相对 schema 的问题 */
function diffDesc(hw, schema, where, out, depth = 0) {
  if (!hw || !schema || depth > MAX_DEPTH) return;
  if (schema.kind === 'untyped') {
    out.push({ kind: 'SCHEMA_UNTYPED', where, detail: 'schema 未声明此处的结构（空 schema）' });
    return;
  }
  if (schema.kind === 'loose') {
    if (hw.kind === 'object' && Object.keys(hw.props ?? {}).length > 0) {
      out.push({
        kind: 'SCHEMA_LOOSE_OBJECT',
        where,
        detail: `schema 只写了 additionalProperties（未声明字段），前端按 {${Object.keys(hw.props).join(', ')}} 解析`,
      });
    }
    return;
  }
  if (hw.kind === 'any' || hw.kind === 'unknown') return;

  if (schema.nullable && !hw.nullable) {
    out.push({ kind: 'SCHEMA_NULLABLE_HW_NOT', where, detail: 'schema 允许 null，前端类型不接受 null' });
  }
  if (schema.kind === 'literals' && (hw.kind === 'string' || hw.kind === 'number')) {
    out.push({
      kind: 'SCHEMA_ENUM_NOT_MODELLED',
      where,
      detail: `schema 是枚举 [${schema.values.join(', ')}]，前端写成 ${hw.kind}`,
    });
  }
  if (
    hw.kind === 'literals' &&
    schema.kind === 'literals' &&
    JSON.stringify([...hw.values].sort()) !== JSON.stringify([...schema.values].sort())
  ) {
    const onlyHw = hw.values.filter((v) => !schema.values.includes(v));
    const onlySchema = schema.values.filter((v) => !hw.values.includes(v));
    out.push({
      kind: 'ENUM_DRIFT',
      where,
      detail:
        `枚举值不同` +
        (onlyHw.length ? `；仅前端有 [${onlyHw}]` : '') +
        (onlySchema.length ? `；仅后端有 [${onlySchema}]` : ''),
    });
  }
  if (hw.kind === 'literals' && schema.kind === 'string') {
    out.push({
      kind: 'HW_ENUM_SCHEMA_STRING',
      where,
      detail: `前端写成枚举 [${hw.values.join(', ')}]，schema 只是 string（后端可给出枚举外的值）`,
    });
  }
  if (hw.kind === 'array' && schema.kind === 'object' && schema.props?.items) {
    out.push({
      kind: 'ARRAY_VS_ENVELOPE',
      where,
      detail: `前端是数组，后端是信封 {${Object.keys(schema.props).join(', ')}}`,
    });
    return;
  }
  if (hw.kind === 'object' && hw.props?.items && schema.kind === 'array') {
    out.push({ kind: 'ARRAY_VS_ENVELOPE', where, detail: '前端是信封，后端是数组' });
    return;
  }
  if (hw.kind === 'array' && schema.kind === 'array') {
    diffDesc(hw.element, schema.element, `${where}[]`, out, depth + 1);
    return;
  }
  if (hw.kind === 'object' && schema.kind === 'object') {
    for (const k of Object.keys(schema.props ?? {})) {
      if (!(k in (hw.props ?? {}))) {
        out.push({
          kind: schema.required?.has(k) ? 'MISSING_REQUIRED_FIELD' : 'MISSING_FIELD',
          where: `${where}.${k}`,
          detail: 'schema 有、前端类型没有',
        });
        continue;
      }
      diffDesc(hw.props[k], schema.props[k], `${where}.${k}`, out, depth + 1);
    }
    for (const k of Object.keys(hw.props ?? {})) {
      if (!(k in (schema.props ?? {}))) {
        out.push({
          kind: hw.required?.has(k) ? 'EXTRA_REQUIRED_FIELD' : 'EXTRA_FIELD',
          where: `${where}.${k}`,
          detail: '前端类型有、schema 没有',
        });
      }
    }
  }
}

/* ================================================================== *
 * 五、判定
 * ================================================================== */

const rows = [];

for (const c of calls) {
  const row = {
    module: c.module,
    fn: c.fn,
    helper: c.helper,
    method: c.method,
    path: c.path,
    schemaPath: c.schemaPath,
    pathExists: c.schemaHasOperation,
    queryNames: c.queryNames,
    verdict: 'UNKNOWN',
    findings: [],
    diagnostics: [],
  };

  if (!c.schemaHasPath) row.verdict = 'PATH_MISSING';
  else if (!c.schemaHasOperation) row.verdict = 'METHOD_MISSING';
  else if (c.probe) {
    const hwToSc = diagAtProbe(c.probe.id, 'hw-to-schema');
    const scToHw = diagAtProbe(c.probe.id, 'schema-to-hw');
    row.diagnostics = [
      ...hwToSc.map((m) => `HW⊆Schema 失败: ${m}`),
      ...scToHw.map((m) => `Schema⊆HW 失败: ${m}`),
    ];
    if (!hwToSc.length && !scToHw.length) row.verdict = 'IDENTICAL';
    else if (!hwToSc.length) row.verdict = 'HW_NARROWER';
    else if (!scToHw.length) row.verdict = 'HW_WIDER';
    else row.verdict = 'CONFLICT';
  } else {
    row.verdict = jsonResponseSchema(c.op).schema ? 'SCHEMA_UNTYPED' : 'NO_JSON_RESPONSE';
  }

  // ---- 返回类型的结构化 diff（解释用）----
  if (c.schemaHasOperation) {
    const resp = jsonResponseSchema(c.op);
    const schemaDesc = resp.schema ? descFromJsonSchema(resp.schema) : null;
    if (schemaDesc) {
      const sig = checker.getSignatureFromDeclaration(c.declNode);
      const ret = sig ? checker.getReturnTypeOfSignature(sig) : null;
      const awaited = ret ? (checker.getAwaitedType(ret) ?? ret) : null;
      if (awaited) {
        row.hwType = checker.typeToString(awaited);
        row.schemaType = resp.refName
          ? resp.refName
          : `(inline ${resp.code}, 无 $ref)`;
        // schema 侧根本没声明结构时，编译器的"不可赋值"不是前端的错 —— 单独归类
        if (schemaDesc.kind === 'untyped') row.verdict = 'SCHEMA_UNTYPED';
        else if (schemaDesc.kind === 'loose') row.verdict = 'SCHEMA_LOOSE';
        else if (awaited.flags & (ts.TypeFlags.Void | ts.TypeFlags.Undefined)) {
          if (row.verdict === 'CONFLICT' || row.verdict === 'HW_NARROWER') {
            row.verdict = 'HW_DISCARDS_BODY';
          }
        } else {
          diffDesc(descFromTsType(awaited), schemaDesc, c.fn, row.findings);
        }
      }
    }
  }

  // ---- 请求体（顶层 + 数组元素一层）----
  if (c.schemaHasOperation && c.bodyNode) {
    const rbRaw = c.op.requestBody;
    const rb = resolveRef(rbRaw)?.content?.['application/json']?.schema;
    const bodyType = checker.getTypeAtLocation(c.bodyNode);
    row.bodyType = checker.typeToString(bodyType);
    const findings = [];
    if (rb) {
      diffDesc(descFromTsType(bodyType), descFromJsonSchema(rb), `${c.fn}(body)`, findings, 3);
    } else {
      findings.push({
        kind: 'EXTRA_BODY',
        where: `${c.fn}(body)`,
        detail: '前端发了请求体，schema 没有 requestBody',
      });
    }
    // 请求体方向相反的发现没意义（后端能收的字段前端不传不是问题）
    row.bodyFindings = findings.filter(
      (f) => f.kind !== 'MISSING_FIELD' && f.kind !== 'SCHEMA_ENUM_NOT_MODELLED',
    );
  }

  // ---- query 参数 ----
  if (c.schemaHasOperation && c.helper !== 'uploadRequest') {
    const params = (c.op.parameters ?? []).filter((p) => p.in === 'query');
    const schemaQuery = new Set(params.map((p) => p.name));
    const requiredQuery = params.filter((p) => p.required).map((p) => p.name);
    row.schemaQuery = [...schemaQuery];
    row.unknownQuery = c.queryNames.filter((n) => !schemaQuery.has(n));
    row.missingRequiredQuery = requiredQuery.filter((n) => !c.queryNames.includes(n));
    row.unusedSchemaQuery = [...schemaQuery].filter((n) => !c.queryNames.includes(n));
  } else {
    row.unknownQuery = [];
    row.missingRequiredQuery = [];
    row.unusedSchemaQuery = [];
  }

  rows.push(row);
}

/* ================================================================== *
 * 六、反向覆盖：schema 里没有任何前端调用方的端点
 * ================================================================== */

const consumed = new Set(rows.filter((r) => r.pathExists).map((r) => `${r.method} ${r.schemaPath}`));
const unconsumed = [];
for (const entry of schemaPaths.values()) {
  for (const m of entry.methods) {
    if (!consumed.has(`${m.toUpperCase()} ${entry.raw}`)) {
      unconsumed.push(`${m.toUpperCase()} ${entry.raw}`);
    }
  }
}
unconsumed.sort();

/**
 * 「数组 vs `{items,total}` 信封」专项核对
 *
 * 这一条是**非空转证据**：overhaul-plan 附录 AZ.7 / BB.5 记的就是这个形状错误
 * （后端返回 `{items,total}`，前端按数组解析）。这里把 schema 里所有
 * `{items,total}` 信封端点列出来，逐个对照前端的返回类型 —— 这样"没有发现"
 * 才是可信的，而不是"没查"。
 */
const envelopeEndpoints = [];
for (const entry of schemaPaths.values()) {
  for (const m of entry.methods) {
    const op = entry.item[m];
    const resp = jsonResponseSchema(op);
    if (!resp.schema) continue;
    const desc = descFromJsonSchema(resp.schema);
    if (desc.kind !== 'object') continue;
    const propNames = Object.keys(desc.props ?? {});
    const arrayProps = propNames.filter((k) => desc.props[k]?.kind === 'array');
    // "列表被包了一层"的两种形态：{items,total,...} 或 {<单个数组字段>}
    const isEnvelope = arrayProps.length >= 1 && (propNames.includes('total') || propNames.length === 1);
    if (isEnvelope) {
      const row = rows.find((r) => r.schemaPath === entry.raw && r.method === m.toUpperCase());
      envelopeEndpoints.push({
        endpoint: `${m.toUpperCase()} ${entry.raw}`,
        keys: propNames,
        arrayKeys: arrayProps,
        fn: row?.fn ?? null,
        hwType: row?.hwType ?? null,
        verdict: row?.verdict ?? null,
        frontendIsArray: /\[\]$/.test(row?.hwType ?? ''),
      });
    }
  }
}
envelopeEndpoints.sort((a, b) => a.endpoint.localeCompare(b.endpoint));
const envelopeMismatches = envelopeEndpoints.filter((e) => e.fn && e.frontendIsArray).length;

/* ================================================================== *
 * 七、输出
 * ================================================================== */

const count = (v) => rows.filter((r) => r.verdict === v).length;

const summary = {
  handWrittenFunctions: rows.length,
  distinctEndpoints: new Set(rows.map((r) => `${r.method} ${r.path}`)).size,
  schemaPaths: Object.keys(openapi.paths ?? {}).length,
  schemaOperations: [...schemaPaths.values()].reduce((n, e) => n + e.methods.size, 0),
  pathMatched: rows.filter((r) => r.pathExists).length,
  pathMissing: count('PATH_MISSING'),
  methodMissing: count('METHOD_MISSING'),
  identical: count('IDENTICAL'),
  hwNarrower: count('HW_NARROWER'),
  hwWider: count('HW_WIDER'),
  conflict: count('CONFLICT'),
  schemaLoose: count('SCHEMA_LOOSE'),
  schemaUntyped: count('SCHEMA_UNTYPED'),
  hwDiscardsBody: count('HW_DISCARDS_BODY'),
  noJsonResponse: count('NO_JSON_RESPONSE'),
  compilerDiagnostics: realDiagnostics.length,
  bodyFindings: rows.reduce((n, r) => n + (r.bodyFindings?.length ?? 0), 0),
  arrayVsEnvelope: rows.reduce(
    (n, r) => n + (r.findings ?? []).filter((f) => f.kind === 'ARRAY_VS_ENVELOPE').length,
    0,
  ),
  envelopeEndpoints: envelopeEndpoints.length,
  envelopeMismatches,
  schemaEnumNotModelled: rows.reduce(
    (n, r) => n + (r.findings ?? []).filter((f) => f.kind === 'SCHEMA_ENUM_NOT_MODELLED').length,
    0,
  ),
  schemaNullableHwNot: rows.reduce(
    (n, r) => n + (r.findings ?? []).filter((f) => f.kind === 'SCHEMA_NULLABLE_HW_NOT').length,
    0,
  ),
  unknownQueryParams: rows.reduce((n, r) => n + (r.unknownQuery?.length ?? 0), 0),
  missingRequiredQuery: rows.reduce((n, r) => n + (r.missingRequiredQuery?.length ?? 0), 0),
  unusedSchemaQuery: rows.reduce((n, r) => n + (r.unusedSchemaQuery?.length ?? 0), 0),
  unconsumedOperations: unconsumed.length,
};

function markdown() {
  const out = [];
  out.push('# OpenAPI 契约漂移（机械判定）\n');
  out.push('```');
  for (const [k, v] of Object.entries(summary)) out.push(`${k.padEnd(24)} ${v}`);
  out.push('```\n');
  out.push('| 模块 | 函数 | 方法 | 路径 | schema 命中 | 返回类型判定 |');
  out.push('|---|---|---|---|---|---|');
  for (const r of rows) {
    out.push(
      `| ${r.module} | \`${r.fn}\` | ${r.method} | \`${r.path}\` | ${r.pathExists ? '是' : '**否**'} | ${r.verdict} |`,
    );
  }
  out.push('\n## 结构化发现（解释用，不参与判定）\n');
  for (const r of rows) {
    const all = [...r.findings, ...(r.bodyFindings ?? [])];
    if (!all.length) continue;
    out.push(`- \`${r.fn}\` — ${r.hwType ?? '?'} ←→ ${r.schemaType ?? '?'}`);
    for (const f of all) out.push(`  - \`${f.kind}\` @ \`${f.where}\` — ${f.detail}`);
  }
  out.push('\n## query 参数\n');
  for (const r of rows) {
    const bits = [];
    if (r.unknownQuery?.length) bits.push(`传了 schema 未声明的 [${r.unknownQuery.join(', ')}]`);
    if (r.missingRequiredQuery?.length)
      bits.push(`缺少必填 [${r.missingRequiredQuery.join(', ')}]`);
    if (r.unusedSchemaQuery?.length)
      bits.push(`schema 有可选参数但前端没传 [${r.unusedSchemaQuery.join(', ')}]`);
    if (bits.length) out.push(`- \`${r.fn}\`：${bits.join('；')}`);
  }
  out.push('\n## 「数组 vs {items,total} 信封」专项核对（非空转证据）\n');
  out.push('| 端点 | schema 信封字段 | 前端函数 | 前端类型 | 前端当成数组？ |');
  out.push('|---|---|---|---|---|');
  for (const e of envelopeEndpoints) {
    out.push(
      `| \`${e.endpoint}\` | ${e.keys.join(', ')} | ${e.fn ? `\`${e.fn}\`` : '（无调用方）'} | \`${e.hwType ?? '—'}\` | ${e.fn ? (e.frontendIsArray ? '**是（错）**' : '否') : '—'} |`,
    );
  }
  out.push(`\n结论：${envelopeEndpoints.length} 个信封端点，前端按数组解析的有 ${envelopeMismatches} 个。`);
  out.push('\n## schema 中无前端调用方的端点\n');
  for (const u of unconsumed) out.push(`- ${u}`);
  return out.join('\n') + '\n';
}

function argValue(flag) {
  const withEq = process.argv.find((a) => a.startsWith(`${flag}=`));
  if (withEq) return withEq.slice(flag.length + 1);
  return process.argv.includes(flag) ? '-' : null;
}

const jsonTarget = argValue('--json');
const mdTarget = argValue('--md');
const probesTarget = argValue('--emit-probes');

if (probesTarget) {
  if (probesTarget === '-') process.stdout.write(virtualText);
  else {
    fs.writeFileSync(probesTarget, virtualText, 'utf8');
    console.log(`wrote ${probesTarget}`);
  }
}

if (spinGuardTripped) {
  console.error(
    `[空转守卫] 生成了 ${probedCount} 个探针，但**故意写错的金丝雀探针一条错都没报**` +
      `（金丝雀行号 ${canaryLine ?? '未生成'}）—— 探针文件没被编译器真正检查；` +
      `此时"全部一致"是假结论。`,
  );
  if (!process.argv.includes('--debug') && !probesTarget) {
    throw new Error('空转守卫触发：拒绝输出可能全为 IDENTICAL 的结论');
  }
}

if (process.argv.includes('--debug')) {
  console.log(`# 探针文件 ${VIRTUAL_FILE}`);
  console.log(
    `# 探针行数 ${lines.length} / 探针数 ${probedCount} / 诊断数 ${diagnostics.length}` +
      `（其中金丝雀 ${canaryDiagnostics.length}，真实 ${realDiagnostics.length}）`,
  );
  console.log(
    `# virtualText=${virtualText.length} 字节 | program 里的该文件语句数=${
      virtualSource?.statements.length ?? -1
    } | semantic=${program.getSemanticDiagnostics(virtualSource).length} | syntactic=${
      program.getSyntacticDiagnostics(virtualSource).length
    }`,
  );
  for (const d of diagnostics) {
    const line = virtualSource.getLineAndCharacterOfPosition(d.start ?? 0).line + 1;
    const owner = probes.get(line);
    console.log(
      `  L${line} ${owner ? `[${owner.fn} ${owner.role}]` : '[—]'} ` +
        ts.flattenDiagnosticMessageText(d.messageText, ' ').slice(0, 200),
    );
  }
}

if (jsonTarget === null && mdTarget === null) {
  process.stdout.write(markdown());
} else {
  if (jsonTarget) {
    const payload = JSON.stringify({ summary, rows, unconsumed }, null, 2) + '\n';
    if (jsonTarget === '-') process.stdout.write(payload);
    else {
      fs.writeFileSync(jsonTarget, payload, 'utf8');
      console.log(`wrote ${jsonTarget}`);
    }
  }
  if (mdTarget) {
    const payload = markdown();
    if (mdTarget === '-') process.stdout.write(payload);
    else {
      fs.writeFileSync(mdTarget, payload, 'utf8');
      console.log(`wrote ${mdTarget}`);
    }
  }
}
