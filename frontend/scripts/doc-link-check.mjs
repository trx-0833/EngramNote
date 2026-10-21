/**
 * 文档链接完整性守卫（C2）
 *
 * ## 它回答的问题
 *
 * 本仓库的文档**互相引用得很密**，但此前**没有任何一层在守**：
 *
 * - `backend/tests/*.py` 有 13 处按 `docs/decisions.md#F-xx` /
 *   `docs/overhaul-plan.md §x.y` 引用（那是代码注释，本脚本不管）；
 * - `docs/**` 与 `frontend/docs/**` 之间大量相对链接（**本脚本管这个**）；
 * - `docs/README.md:161-162` 自己记着一个已经发生过的坑：
 *   `docs/architecture.md` 加了一行横幅，就让 `overhaul-plan.md` 里
 *   `architecture.md:NN` 的引用**整体偏移约 21 行**。
 *
 * 失效模式是**安静**的：文件改名/移动、小节标题改了措辞、锚点拼错 ——
 * 渲染出来只是一个点不动的链接，没有任何地方会报错。
 *
 * ## 判据（三件都查）
 *
 * 1. **目标文件存在** —— `[x](./foo.md)` 里的 `./foo.md` 必须真的在磁盘上；
 * 2. **行号后缀合法** —— 本仓库的约定是 `路径:24` 或 `路径:24-30`
 *    （`docs/README.md:5` 的维护约定与 `AGENTS.md` §4 都要求"所有结论带 `文件:行号`"）。
 *    所以链接里带 `:NN` 时，要**剥掉行号再查文件**，并且行号必须是正整数或区间；
 * 3. **锚点存在** —— `[x](./foo.md#some-heading)` 里的 `some-heading`
 *    必须能匹配 `foo.md` 里某个标题的 GitHub 风格 slug。
 *
 * ## 刻意不做的三件事（写出来免得被当成漏做）
 *
 * 1. **不查外部 URL**（`http(s)://` / `mailto:`）—— 那要联网，
 *    而本脚本要在 CI 里**离线**跑（与 `backend/tests/conftest.py` 的网络守卫同一条原则）。
 * 2. **不查代码注释里的 `文件:行号`** —— 那是另一种引用形态（没有 `[]()` 语法），
 *    要查得先解析 Python/TS 注释，误报面很大。本轮先守住 markdown 内部。
 * 3. **不查 `notes/**` 与 `.trae/**`** —— 前者是用户的笔记正文（`notes/` 是资料，
 *    不是本仓库的文档），后者被 `.gitignore:197` 忽略（不是仓库的一部分）。
 *
 * ## 用法
 *
 *     node frontend/scripts/doc-link-check.mjs            # 扫默认清单
 *     node frontend/scripts/doc-link-check.mjs docs/x.md  # 只扫指定文件
 *     node frontend/scripts/doc-link-check.mjs --list     # 打印默认清单后退出
 *
 * 有死链 → 退出码 1（打印 文件:行号 + 链接原文 + 失败原因）。
 * **清单里任一路径不存在时同样退出 1** —— 与 `doc-table-scan.mjs` 同一条理由：
 * "少扫一份却仍然通过"的绿灯比红灯更危险。
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

/** 仓库根（本文件在 frontend/scripts/ 下） */
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

/**
 * 默认清单：目录递归 + 根目录那几个入口文件
 *
 * ⚠️ 目录写死、**不做全仓 glob**：全仓 `**\/*.md` 会把 `notes/`（用户资料）、
 * `.trae/`（gitignore）、`_agenttmp/`（临时区）一起扫进来，误报会淹没真问题。
 * 新增文档目录时**要来这里加一行** —— 这是刻意的（"扫哪些"必须是个决定，
 * 而不是一个 glob 的副作用）。
 */
const SCAN_DIRS = ['docs', 'frontend/docs', 'backend/docs'];
const SCAN_ROOT_FILES = [
  'README.md',
  'README.en.md',
  'CHANGELOG.md',
  'CONTRIBUTING.md',
  'SECURITY.md',
  'CODE_OF_CONDUCT.md',
  'UPGRADING.md',
  'AGENTS.md',
];

/** 跳过的目录名（出现在路径任意一段就跳过） */
const SKIP_SEGMENTS = new Set([
  'node_modules',
  '.git',
  'dist',
  'build',
  '_backup',
  '_ref',
  '_agenttmp',
  '__pycache__',
  '.pytest_cache',
  '.ruff_cache',
]);

/**
 * 已知的**历史锚点腐化**白名单（刻意保留，不修）
 *
 * `docs/archive/LLM服务详解.md` 的目录锚点是用另一个工具生成的，
 * 它没有复现 GitHub 的 slug 规则 —— 典型形态是**把标点直接删掉而不留连字符**：
 *
 *     标题：`## 第一章：零基础理解"调用 LLM"这件事`
 *     目录写的：`#第一章零基础理解调用llm这件事`
 *     GitHub 实际：`#第一章-零基础理解调用-llm这件事`
 *
 * 实测这一类在该文件里有约 30 处（第二～九章、以及部分小节）。
 *
 * ## 为什么不"顺手全修"
 *
 * `docs/archive/` 是**历史快照**（`docs/README.md:19` 的三态约定：冻结、不再更新）。
 * 批量重写它的 30 个锚点属于"改历史文档"，而它们对访客的实际价值是"知道那份
 * 文档以前长什么样" —— 收益极低，且会让 archive 的内容与"归档时点"脱钩。
 *
 * ## 为什么不"直接跳过整个 archive/"
 *
 * 因为 archive 里**也有真的死链** —— 本轮就修出 1 处（`docs/overhaul-plan.md`
 * 的附录 B 锚点少了一段标题文字，那不在 archive 里）。整目录跳过等于放弃这一层
 * 保护。所以做成**逐条白名单**：新增腐化锚点会被报出来，除非有人显式加进来。
 */
const KNOWN_STALE_ANCHORS = new Map([
  [
    'docs/archive/LLM服务详解.md',
    new Set([
      '#第一章零基础理解调用llm这件事',
      '#第四章-llmservice-核心类逐段精讲',
      '#第七章-llm-在项目中的实际调用场景',
    ]),
  ],
]);

/** 该链接是否命中"已知历史腐化锚点"白名单 */
function isKnownStaleAnchor(fileAbs, raw) {
  const rel = path.relative(REPO_ROOT, fileAbs).replace(/\\/g, '/');
  const set = KNOWN_STALE_ANCHORS.get(rel);
  return Boolean(set && set.has(raw));
}

/** 递归收集目录下的 .md（按路径排序，输出稳定） */
function collectMarkdown(dirAbs, out) {
  if (!fs.existsSync(dirAbs)) return;
  const entries = fs.readdirSync(dirAbs, { withFileTypes: true });
  for (const entry of entries) {
    if (SKIP_SEGMENTS.has(entry.name)) continue;
    const abs = path.join(dirAbs, entry.name);
    if (entry.isDirectory()) {
      collectMarkdown(abs, out);
    } else if (entry.isFile() && entry.name.toLowerCase().endsWith('.md')) {
      out.push(abs);
    }
  }
}

/** 默认清单（绝对路径，已排序） */
function defaultTargets() {
  const out = [];
  for (const dir of SCAN_DIRS) collectMarkdown(path.join(REPO_ROOT, dir), out);
  for (const rel of SCAN_ROOT_FILES) {
    const abs = path.join(REPO_ROOT, rel);
    if (fs.existsSync(abs)) out.push(abs);
  }
  return [...new Set(out)].sort();
}

/**
 * GitHub 风格的标题 slug
 *
 * 规则（按 GitHub 实际行为，本仓库的中文标题也适用）：
 * 1. 去掉 markdown 行内标记（`**` / `` ` `` / 链接的 URL 部分保留可见文字）；
 * 2. 转小写；
 * 3. 去掉除 字母/数字/空格/连字符/下划线/**非 ASCII 字符**（中文、全角标点）之外的字符；
 * 4. **每个空格各自变成一个连字符**（不折叠）。
 *
 * ⚠️ 第 3 条里"保留非 ASCII"是关键：本仓库标题大量是中文
 * （`## 1. 前置约束（动手前必须知道的三条）`），若按 `[a-z0-9-]` 过滤会把
 * 中文全部删掉，于是**每一条中文锚点都变成假红**。
 *
 * ⚠️ 第 4 条里"不折叠"也是关键，且这一条是**实测出来的**：
 * 标题 `### 1.2 调用 LLM —— 发一个 HTTP 请求` 里的 `——` 被规则 3 删掉后
 * 留下**两个空格**，GitHub 给出的是 `12-调用-llm--发一个-http-请求`（双连字符）。
 * 第一版实现先 `\s+ → -` 折叠，于是把它算成单连字符 ——
 * `docs/archive/LLM服务详解.md` 的目录锚点因此整片报假红。
 *
 * ⚠️ 尾部空格**不 trim**：删掉一个行尾标点会留下一个尾随空格，
 * 它在 GitHub 上同样变成一个尾随连字符（`### 1.1 LLM 是什么？` → `11-llm-是什么-`）。
 */
function slugify(heading) {
  let text = heading.trim();
  // 行内代码与强调标记：只留内容
  text = text.replace(/`([^`]*)`/g, '$1');
  text = text.replace(/\*\*([^*]*)\*\*/g, '$1');
  text = text.replace(/\*([^*]*)\*/g, '$1');
  // markdown 链接：只留可见文字
  text = text.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1');
  // HTML 标签（本仓库的横幅用 `>` 引用，不用 HTML，但稳妥起见）
  text = text.replace(/<[^>]+>/g, '');
  return (
    text
      .toLowerCase()
      .replace(/[^\p{L}\p{N}\s_-]/gu, '')
      // 每个空格各自成连字符（不折叠、不 trim）—— 见上面第 4 条
      .replace(/ /g, '-')
  );
}

/** 取一份 markdown 里所有标题的 slug 集合 */
function headingSlugs(absPath) {
  const content = fs.readFileSync(absPath, 'utf8');
  const slugs = new Set();
  let inFence = false;
  for (const rawLine of content.split(/\r?\n/)) {
    // 围栏代码块里的 `# xxx` 不是标题
    if (/^\s*(```|~~~)/.test(rawLine)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const m = /^(#{1,6})\s+(.*)$/.exec(rawLine);
    if (m) slugs.add(slugify(m[2]));
  }
  return slugs;
}

/**
 * 剥掉 `路径` 上的行号后缀
 *
 * 本仓库的约定形态：`文件:24`、`文件:24-30`（见 `docs/README.md:5` 与
 * `AGENTS.md` §4）。返回 `{ filePart, linePart }`；
 * 若 `:NN` 后面还有别的字符（例如 Windows 盘符 `C:`），**不当行号处理**。
 */
function stripLineSuffix(target) {
  const m = /^(.*?):(\d+(?:-\d+)?)$/.exec(target);
  if (!m) return { filePart: target, linePart: null };
  return { filePart: m[1], linePart: m[2] };
}

/**
 * 提取 markdown 链接（含图片）的目标
 *
 * 只认 `[文字](目标)` 与 `![文字](目标)` 两种；跳过围栏代码块内的内容
 * （文档里大量示例本身就是 markdown，扫进去必然是假红）。
 */
function extractLinks(absPath) {
  const content = fs.readFileSync(absPath, 'utf8');
  const lines = content.split(/\r?\n/);
  const found = [];
  let inFence = false;
  lines.forEach((line, idx) => {
    if (/^\s*(```|~~~)/.test(line)) {
      inFence = !inFence;
      return;
    }
    if (inFence) return;
    // 去掉行内代码里的内容（`` `[x](./y.md)` `` 是举例，不是链接）
    const withoutInlineCode = line.replace(/`[^`]*`/g, '');
    const re = /!?\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g;
    let m;
    while ((m = re.exec(withoutInlineCode)) !== null) {
      found.push({ line: idx + 1, raw: m[1], text: line.trim() });
    }
  });
  return found;
}

/** 判断是否外部链接 / 纯锚点 */
function classify(target) {
  if (/^[a-z][a-z0-9+.-]*:/i.test(target) && !/^[a-zA-Z]:[\\/]/.test(target)) {
    // 有 scheme（http:、https:、mailto:、tel:…）；Windows 盘符 `C:\` 排除在外
    return 'external';
  }
  if (target.startsWith('#')) return 'anchor-only';
  return 'relative';
}

function main(argv) {
  if (argv.includes('--list')) {
    for (const abs of defaultTargets()) {
      console.log(path.relative(REPO_ROOT, abs).replace(/\\/g, '/'));
    }
    return 0;
  }

  const explicit = argv.filter((a) => !a.startsWith('--'));
  const targets = explicit.length
    ? explicit.map((p) => path.resolve(REPO_ROOT, p))
    : defaultTargets();

  // 清单存在性：任一路径缺失直接失败（理由见文件头）
  const missingTargets = targets.filter((abs) => !fs.existsSync(abs));
  if (missingTargets.length) {
    console.error('✗ 待扫清单里有不存在的路径（文档被移动/改名了？）：');
    for (const abs of missingTargets) console.error(`    ${abs}`);
    return 1;
  }
  if (!targets.length) {
    console.error('✗ 待扫清单为空 —— 扫 0 份文件却返回通过，比红灯更危险');
    return 1;
  }

  const problems = [];
  let checkedFiles = 0;
  let checkedLinks = 0;
  let checkedAnchors = 0;
  let knownStaleSkipped = 0;

  for (const abs of targets) {
    checkedFiles += 1;
    const selfSlugs = headingSlugs(abs);
    for (const { line, raw } of extractLinks(abs)) {
      const kind = classify(raw);
      if (kind === 'external') continue;

      const hashAt = raw.indexOf('#');
      const beforeHash = hashAt === -1 ? raw : raw.slice(0, hashAt);
      const anchor = hashAt === -1 ? '' : raw.slice(hashAt + 1);

      if (kind === 'anchor-only') {
        checkedAnchors += 1;
        if (isKnownStaleAnchor(abs, raw)) {
          knownStaleSkipped += 1;
        } else if (anchor && !selfSlugs.has(slugify(anchor))) {
          problems.push({
            file: abs,
            line,
            raw,
            why: `本文件里没有匹配 #${anchor} 的标题`,
          });
        }
        continue;
      }

      // 相对路径：剥行号 → 解析 → 查存在
      const { filePart, linePart } = stripLineSuffix(beforeHash);
      checkedLinks += 1;

      const decoded = decodeURIComponent(filePart);
      // 两种解析顺序（**这是实测出来的第二类假红**）：
      // 先按"相对于本文件"解析（markdown 的标准语义），不存在时**再按仓库根**解析。
      // 为什么需要后者：`docs/archive/*.md` 里有 `backend/app/services/vault_path.py`
      // 这种写法 —— 它指的是**仓库根下的**那个路径，而不是 `docs/archive/backend/...`。
      // 只按前者判会整片报假红（实测 3 处，全在 archive）。
      const candidates = [
        path.resolve(path.dirname(abs), decoded),
        path.resolve(REPO_ROOT, decoded),
      ];
      const targetAbs = candidates.find((p) => fs.existsSync(p)) ?? candidates[0];
      const relToRepo = path.relative(REPO_ROOT, targetAbs);
      if (relToRepo.startsWith('..')) {
        problems.push({
          file: abs,
          line,
          raw,
          why: `目标在仓库之外（${targetAbs}）`,
        });
        continue;
      }
      if (!fs.existsSync(targetAbs)) {
        problems.push({
          file: abs,
          line,
          raw,
          why: `目标不存在（试过：相对本文件 ${path
            .relative(path.dirname(abs), candidates[0])
            .replace(/\\/g, '/')}；相对仓库根 ${decoded}）`,
        });
        continue;
      }
      if (linePart !== null && !/^\d+(-\d+)?$/.test(linePart)) {
        problems.push({ file: abs, line, raw, why: `行号后缀非法：${linePart}` });
        continue;
      }
      if (anchor && targetAbs.toLowerCase().endsWith('.md')) {
        checkedAnchors += 1;
        const targetSlugs = headingSlugs(targetAbs);
        if (!targetSlugs.has(slugify(anchor))) {
          problems.push({
            file: abs,
            line,
            raw,
            why: `${relToRepo.replace(/\\/g, '/')} 里没有匹配 #${anchor} 的标题`,
          });
        }
      }
    }
  }

  if (problems.length) {
    console.error(`✗ 文档链接检查失败：${problems.length} 处`);
    for (const p of problems) {
      console.error(`    ${path.relative(REPO_ROOT, p.file).replace(/\\/g, '/')}:${p.line}`);
      console.error(`      链接：${p.raw}`);
      console.error(`      原因：${p.why}`);
    }
    return 1;
  }

  console.log(
    `✓ 文档链接检查通过：${checkedFiles} 份文件 / ${checkedLinks} 条相对链接 / ` +
      `${checkedAnchors} 个锚点，0 死链` +
      (knownStaleSkipped > 0
        ? `（其中 ${knownStaleSkipped} 个是白名单里的历史腐化锚点，见脚本头部）`
        : ''),
  );
  return 0;
}

process.exit(main(process.argv.slice(2)));
