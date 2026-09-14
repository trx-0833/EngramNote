/**
 * 漂移前后对照器（阶段 5.1 / S3 起的"改动有据"工具）
 *
 * ## 它回答的问题
 *
 * `openapi-drift.mjs` 给的是**某一时刻**的分布。判断"这一轮改动到底有没有
 * 让契约贴合变好"要的是**两份快照的逐项对照**，而且必须机械判定：
 *
 *   node src/api/generated/openapi-drift.mjs --json=before.json   # 改动前
 *   node src/api/generated/openapi-drift.mjs --json=after.json    # 改动后
 *   node scripts/drift-delta.mjs before.json after.json
 *
 * 输出三部分：
 *
 * 1. **指标表**：每一项的方向由 `DIRECTION` 声明（越小越好 / 越大越好 /
 *    必须不变 / 只是信息），任何"反向"都会被标成 ❌ 并让脚本以退出码 1 结束；
 * 2. **逐函数判定迁移**：`CONFLICT → IDENTICAL` 这类迁移各有多少，
 *    **反向迁移逐个点名**（只给总数的话，"37 个变好、3 个变坏"会被平均值盖住）；
 * 3. **非空转守卫**：几个"分母"必须不变 —— `handWrittenFunctions`（判定对象
 *    有没有少）、`pathMatched`、`bodyChecked`（有多少个函数真的被比过请求体）、
 *    `schemaPaths` / `schemaOperations`。这一条是防"指标变好是因为**没比**"：
 *    S3 给请求体加类型标注之后，`bodyFindings` 归零是**结构性**的，
 *    所以更要有东西证明"34 个请求体仍然在被比"。
 *
 * 退出码：有反向指标或分母变动 → 1；`--allow-regression` 可以只报告不失败
 * （用于"我知道有一项会变坏，先看看差多少"的场合，报告里必须写明为什么）。
 */
/* global console, process */
import fs from 'node:fs';

/* ------------------------------------------------------------------ *
 * 一、方向表
 * ------------------------------------------------------------------ */

/** 越小越好 */
const LOWER_IS_BETTER = [
  'pathMissing',
  'methodMissing',
  'hwNarrower',
  'hwWider',
  'conflict',
  'schemaLoose',
  'schemaUntyped',
  'hwDiscardsBody',
  'compilerDiagnostics',
  'bodyFindings',
  'arrayVsEnvelope',
  'envelopeMismatches',
  'schemaEnumNotModelled',
  'schemaNullableHwNot',
  'unknownQueryParams',
  'missingRequiredQuery',
];

/** 越大越好 */
const HIGHER_IS_BETTER = ['identical', 'pathMatched'];

/** 必须不变（变了就是"判定对象/契约本身动了"，本轮不该发生） */
const MUST_NOT_CHANGE = [
  'handWrittenFunctions',
  'distinctEndpoints',
  'schemaPaths',
  'schemaOperations',
  'bodyChecked',
];

/** 只是信息（涨跌都正常，不给对错） */
const INFORMATIONAL = [
  'noJsonResponse',
  'unusedSchemaQuery',
  'envelopeEndpoints',
  'unconsumedOperations',
];

const DIRECTION = new Map([
  ...LOWER_IS_BETTER.map((k) => [k, 'lower']),
  ...HIGHER_IS_BETTER.map((k) => [k, 'higher']),
  ...MUST_NOT_CHANGE.map((k) => [k, 'fixed']),
  ...INFORMATIONAL.map((k) => [k, 'info']),
]);

/* ------------------------------------------------------------------ *
 * 二、读两份快照
 * ------------------------------------------------------------------ */

const [beforePath, afterPath] = process.argv.slice(2).filter((a) => !a.startsWith('--'));
if (!beforePath || !afterPath) {
  console.error('用法：node scripts/drift-delta.mjs <before.json> <after.json> [--allow-regression]');
  process.exit(2);
}
const allowRegression = process.argv.includes('--allow-regression');

const load = (p) => {
  const j = JSON.parse(fs.readFileSync(p, 'utf8'));
  if (!j.summary || !Array.isArray(j.rows)) {
    console.error(`[失败] ${p} 不是 openapi-drift.mjs --json 的产物（缺 summary/rows）`);
    process.exit(2);
  }
  if (j.rows.length === 0) {
    console.error(`[失败] ${p} 的 rows 是空的 —— 这份快照什么都没比，对照没有意义`);
    process.exit(2);
  }
  return j;
};

const before = load(beforePath);
const after = load(afterPath);

/** 请求体被真正比过的函数数（`bodyType` 只在扫到 `body:` 时才有） */
const bodyChecked = (j) => j.rows.filter((r) => r.bodyType !== undefined).length;

const metricOf = (j, key) =>
  key === 'bodyChecked' ? bodyChecked(j) : (j.summary[key] ?? 0);

/* ------------------------------------------------------------------ *
 * 三、指标表
 * ------------------------------------------------------------------ */

const keys = [
  ...LOWER_IS_BETTER,
  ...HIGHER_IS_BETTER,
  ...MUST_NOT_CHANGE,
  ...INFORMATIONAL,
].filter((k) => k in before.summary || k === 'bodyChecked');

const problems = [];
const rows = [];

for (const key of keys) {
  const a = metricOf(before, key);
  const b = metricOf(after, key);
  const dir = DIRECTION.get(key) ?? 'info';
  const delta = b - a;
  let mark = '·';
  if (delta !== 0) {
    if (dir === 'lower') mark = delta < 0 ? '✅' : '❌';
    else if (dir === 'higher') mark = delta > 0 ? '✅' : '❌';
    else if (dir === 'fixed') mark = '❌';
  }
  if (mark === '❌') problems.push({ key, a, b, dir });
  rows.push({ key, a, b, delta, dir, mark });
}

const w = Math.max(...rows.map((r) => r.key.length));
console.log(`# 漂移对照\n`);
console.log(`before: ${beforePath}`);
console.log(`after : ${afterPath}\n`);
console.log('| 指标 | 前 | 后 | 差 | 方向 | |');
console.log('|---|---:|---:|---:|---|---|');
for (const r of rows) {
  const label = { lower: '越小越好', higher: '越大越好', fixed: '必须不变', info: '信息' }[r.dir];
  console.log(`| \`${r.key}\` | ${r.a} | ${r.b} | ${r.delta > 0 ? '+' : ''}${r.delta} | ${label} | ${r.mark} |`);
}

/* ------------------------------------------------------------------ *
 * 四、逐函数判定迁移（反向迁移点名）
 * ------------------------------------------------------------------ */

const keyOf = (r) => `${r.module}::${r.fn}`;
const beforeByFn = new Map(before.rows.map((r) => [keyOf(r), r]));
const afterByFn = new Map(after.rows.map((r) => [keyOf(r), r]));

const missing = [...beforeByFn.keys()].filter((k) => !afterByFn.has(k));
const added = [...afterByFn.keys()].filter((k) => !beforeByFn.has(k));
if (missing.length || added.length) {
  console.log(`\n## 判定对象发生变化（这是"分母动了"，必须解释）\n`);
  if (missing.length) console.log(`- 前有后无（${missing.length}）：${missing.join('、')}`);
  if (added.length) console.log(`- 前无后有（${added.length}）：${added.join('、')}`);
  problems.push({ key: 'judgedFunctions', a: beforeByFn.size, b: afterByFn.size, dir: 'fixed' });
}

const transitions = new Map();
const backward = [];
const VERDICT_RANK = {
  CONFLICT: 0,
  HW_NARROWER: 1,
  HW_WIDER: 1,
  HW_DISCARDS_BODY: 1,
  SCHEMA_UNTYPED: 1,
  SCHEMA_LOOSE: 1,
  METHOD_MISSING: 0,
  PATH_MISSING: 0,
  NO_JSON_RESPONSE: 2,
  IDENTICAL: 3,
};
for (const [k, r0] of beforeByFn) {
  const r1 = afterByFn.get(k);
  if (!r1 || r0.verdict === r1.verdict) continue;
  const t = `${r0.verdict} → ${r1.verdict}`;
  transitions.set(t, (transitions.get(t) ?? 0) + 1);
  const rank0 = VERDICT_RANK[r0.verdict];
  const rank1 = VERDICT_RANK[r1.verdict];
  if (rank0 !== undefined && rank1 !== undefined && rank1 < rank0) backward.push(`${k}: ${t}`);
}
console.log(`\n## 判定迁移\n`);
if (!transitions.size) console.log('- 没有任何函数换判定');
for (const [t, n] of [...transitions.entries()].sort()) console.log(`- ${t}：**${n}**`);
if (backward.length) {
  console.log(`\n### 反向迁移（逐个点名）\n`);
  for (const b of backward) console.log(`- ${b}`);
  problems.push({ key: 'verdictRegressions', a: 0, b: backward.length, dir: 'lower' });
}

/* ------------------------------------------------------------------ *
 * 五、请求体逐函数对照（S3 的核心指标）
 * ------------------------------------------------------------------ */

const bodyRows = [];
for (const [k, r0] of beforeByFn) {
  const r1 = afterByFn.get(k);
  if (!r1) continue;
  const n0 = (r0.bodyFindings ?? []).length;
  const n1 = (r1.bodyFindings ?? []).length;
  const had0 = r0.bodyType !== undefined;
  const had1 = r1.bodyType !== undefined;
  if (n0 !== n1 || had0 !== had1) bodyRows.push({ k, n0, n1, had0, had1 });
}
console.log(`\n## 请求体逐函数对照（被比过的函数：${bodyChecked(before)} → ${bodyChecked(after)}）\n`);
if (!bodyRows.length) {
  console.log('- 没有任何函数的请求体发现数发生变化');
} else {
  for (const { k, n0, n1, had0, had1 } of bodyRows) {
    console.log(`- ${k}: 发现 ${n0} → ${n1}${had0 !== had1 ? `；body 是否可比 ${had0} → ${had1}` : ''}`);
  }
}

/* ------------------------------------------------------------------ *
 * 六、结论与退出码
 * ------------------------------------------------------------------ */

console.log(`\n## 结论\n`);
if (!problems.length) {
  console.log('- ✅ 所有有方向的指标都朝好的一边走，分母未变');
  process.exit(0);
}
for (const p of problems) {
  const label = { lower: '本应下降', higher: '本应上升', fixed: '本应不变' }[p.dir] ?? '';
  console.log(`- ❌ \`${p.key}\`：${p.a} → ${p.b}（${label}，却 ${p.b > p.a ? '上升' : '下降'}了）`);
}
if (allowRegression) {
  console.log('\n（`--allow-regression`：只报告不失败。报告里必须写明为什么允许）');
  process.exit(0);
}
process.exit(1);
