/**
 * 文档表格缺陷扫描（overhaul-plan 的"表格也必须自证"工具）
 *
 * ## 它回答的问题
 *
 * `docs/overhaul-plan.md` 是**表格密集**的：每一行都是一条任务的状态与依据。
 * Markdown 表格有一个安静的失效模式 —— **某一行的格子数与表头不一致**：
 *
 *   - **行比表头宽**：多出来的格子**不渲染**，内容被静默丢弃（查表的人看不见
 *     那句依据，而文档看上去完全正常）；
 *   - **行比表头窄**：后面的列**错位**或整列为空（看得见，但读到的是错的）。
 *
 * 两个方向都是缺陷，而且**都不是语法错误** —— 没有这个脚本，就只能靠人在
 * 700 KB 的文档里逐张表数竖线。附录 BK.6 记的正是这条判据（当时是手工扫的）。
 *
 * ## 判据
 *
 * 1. 表头行 = 一个以 `|` 开头、且**下一行是分隔行**（`|---|---|`）的行；
 * 2. 表格 = 表头 + 分隔行 + 之后连续的 `|` 行；
 * 3. **代码块里的 `|` 不算表格**（``` 围栏按成对出现跳过）；
 * 4. 单元格计数必须忽略**转义竖线** `\|` —— 本项目的表格里到处是
 *    `string \| null` 这种写法，按裸 `|` 切会把一格数成两格（假缺陷）。
 *
 * ## 用法
 *
 *     node frontend/scripts/doc-table-scan.mjs [文件…]
 *
 * 默认扫 `docs/overhaul-plan.md` 与 `frontend/docs/*.md`。
 * 有缺陷 → 退出码 1（打印文件、行号、表头列数、该行列数、原始行）。
 */
/* global console, process */
import fs from 'node:fs';
import path from 'node:path';

const REPO_ROOT = path.resolve(import.meta.dirname, '..', '..');

const DEFAULT_FILES = [
  'docs/overhaul-plan.md',
  'frontend/docs/openapi-client.md',
  'frontend/docs/css-migration-plan.md',
  'frontend/docs/css-convention.md',
  'frontend/docs/query-and-state-plan.md',
];

const files = process.argv.slice(2).length
  ? process.argv.slice(2)
  : DEFAULT_FILES.map((f) => path.join(REPO_ROOT, f));

/** 数一格里的竖线：忽略 `\|`（转义） */
function countCells(line) {
  const body = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  let cells = 1;
  for (let i = 0; i < body.length; i++) {
    if (body[i] !== '|') continue;
    if (i > 0 && body[i - 1] === '\\') continue; // 转义竖线，属上一格内容
    cells++;
  }
  return cells;
}

const isSeparator = (line) =>
  /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(line) && !/[^\s:|-]/.test(line);
const isRow = (line) => /^\s*\|/.test(line);

let totalTables = 0;
let totalRows = 0;
const defects = [];

for (const file of files) {
  if (!fs.existsSync(file)) {
    console.log(`· 跳过（不存在）：${path.relative(REPO_ROOT, file)}`);
    continue;
  }
  const lines = fs.readFileSync(file, 'utf8').split('\n');
  let inFence = false;
  let fenceMarker = null;
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s*(```+|~~~+)/);
    if (fence) {
      if (!inFence) {
        inFence = true;
        fenceMarker = fence[1][0];
      } else if (fence[1][0] === fenceMarker) {
        inFence = false;
        fenceMarker = null;
      }
      i++;
      continue;
    }
    if (inFence) {
      i++;
      continue;
    }
    // 表头：本行是行、下一行是分隔行
    // ⚠️ 不能写成 `!isRow(下一行)`：分隔行**本身**也以 `|` 开头，
    //    第一版就是这么写的，结果一张表都认不出来（0 张表 = 假"没问题"）。
    if (isRow(line) && i + 1 < lines.length && isSeparator(lines[i + 1])) {
      const headerCells = countCells(line);
      const startLine = i + 1;
      const inTable = [];
      let j = i + 2;
      while (j < lines.length && isRow(lines[j])) {
        inTable.push({ line: lines[j], no: j + 1, cells: countCells(lines[j]) });
        j++;
      }
      totalTables++;
      totalRows += inTable.length;
      for (const row of inTable) {
        if (row.cells !== headerCells) {
          defects.push({
            file: path.relative(REPO_ROOT, file),
            tableAt: startLine,
            rowAt: row.no,
            headerCells,
            cells: row.cells,
            how: row.cells > headerCells ? '过宽（多出的格子不渲染）' : '过窄（后续列错位）',
            raw: row.line.trim().slice(0, 160),
          });
        }
      }
      i = j;
      continue;
    }
    i++;
  }
  console.log(`· ${path.relative(REPO_ROOT, file)}`);
}

console.log(`\n扫描完成：${totalTables} 张表 / ${totalRows} 行数据 / **${defects.length} 处缺陷**`);
for (const d of defects) {
  console.log(
    `\n[缺陷] ${d.file}:${d.rowAt}（表头在第 ${d.tableAt} 行，表头 ${d.headerCells} 列，本行 ${d.cells} 列 —— ${d.how}）`,
  );
  console.log(`        ${d.raw}`);
}
process.exit(defects.length ? 1 : 0);
