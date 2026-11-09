#!/usr/bin/env node
/**
 * 风险打分（规则版，不使用模型）
 *
 * 用途：为"分档评审"提供可复核的依据。风险高的改动要求更严的门禁与人工确认。
 * 当前状态：未接入任何门禁，仅手动执行。
 *
 * 用法：
 *   node scripts/risk.mjs --files backend/app/services/llm/router.py frontend/src/hooks/useNotes.ts
 *   node scripts/risk.mjs --staged          # 取 git 暂存区的改动
 *   node scripts/risk.mjs --range origin/main...HEAD
 *   node scripts/risk.mjs --files a.py --json
 *
 * 输出档位：
 *   <20  低  → lint + 单测即可
 *   20–50 中  → 加覆盖率 + SAST
 *   >50   高  → 加人工确认 + 变更说明必须写回滚方式
 *
 * 设计说明：
 *   - 只用"能从 diff/路径读到的信号"，不引入模型，保证可复现。
 *   - 权重按本项目的实际结构设定；调整权重应同时更新本注释与 _governance 文档。
 */

import { execSync } from 'node:child_process';

const RULES = [
  // 核心业务逻辑：LLM 编排、MinerU 解析、FSRS 等
  { re: /^backend\/app\/services\/(llm|mineru)\//, w: 30, tag: 'core-logic' },
  { re: /^backend\/app\/core\//, w: 30, tag: 'core' },
  // 数据库迁移：不可逆风险最高
  { re: /^backend\/alembic\//, w: 30, tag: 'schema' },
  // 接口契约：前后端耦合面
  { re: /^backend\/app\/api\//, w: 20, tag: 'api' },
  { re: /^backend\/app\/schemas\//, w: 20, tag: 'contract' },
  { re: /^backend\/openapi\.json$/, w: 20, tag: 'contract-artifact' },
  // 前端数据流：状态与请求层
  { re: /^frontend\/src\/api\//, w: 15, tag: 'data-flow' },
  { re: /^frontend\/src\/hooks\//, w: 15, tag: 'data-flow' },
  // 共享 UI 与设计系统
  { re: /^frontend\/src\/components\//, w: 10, tag: 'shared-ui' },
  // 安全与配置
  { re: /^backend\/app\/config\.py$/, w: 25, tag: 'config' },
  { re: /^backend\/app\/middleware\//, w: 20, tag: 'middleware' },
  // 测试与文档：降权（改它们不会直接改变生产行为）
  { re: /^(backend\/tests|frontend\/e2e)\//, w: -10, tag: 'tests' },
  { re: /^(docs|notes)\//, w: -10, tag: 'docs' },
];

const CHURN_THRESHOLD = 25;   // 改动文件数超过此值，额外加分
const CHURN_WEIGHT = 15;

function parseArgs(argv) {
  const out = { files: [], staged: false, range: null, json: false };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--files') { while (argv[i + 1] && !argv[i + 1].startsWith('--')) out.files.push(argv[++i]); }
    else if (a === '--staged') out.staged = true;
    else if (a === '--range') out.range = argv[++i];
    else if (a === '--json') out.json = true;
    else if (a === '--help' || a === '-h') { out.help = true; }
  }
  return out;
}

function gitFiles(args) {
  try {
    if (args.staged) {
      return execSync('git diff --cached --name-only --diff-filter=ACMR', { encoding: 'utf8' })
        .split('\n').filter(Boolean);
    }
    if (args.range) {
      return execSync(`git diff --name-only --diff-filter=ACMR ${args.range}`, { encoding: 'utf8' })
        .split('\n').filter(Boolean);
    }
  } catch (e) {
    console.error('[risk] git 读取失败：', e.message);
  }
  return [];
}

export function score(files) {
  let s = 0;
  const tags = new Set();
  const hits = [];
  for (const f of files) {
    for (const r of RULES) {
      if (r.re.test(f)) {
        s += r.w;
        tags.add(r.tag);
        hits.push({ file: f, tag: r.tag, w: r.w });
      }
    }
  }
  if (files.length > CHURN_THRESHOLD) {
    s += CHURN_WEIGHT;
    tags.add('large-diff');
  }
  const clamped = Math.max(0, Math.min(100, s));
  const tier = clamped < 20 ? 'low' : clamped <= 50 ? 'medium' : 'high';
  return { score: clamped, tier, tags: [...tags], hits, fileCount: files.length };
}

const TIER_ADVICE = {
  low: 'lint + 单测即可',
  medium: '加覆盖率门禁 + SAST（Semgrep）',
  high: '加人工确认 + 变更说明必须写"回滚方式"',
};

function main() {
  const args = parseArgs(process.argv);
  if (args.help) {
    console.log(`
风险打分（规则版）

用法：
  node scripts/risk.mjs --files <path...>
  node scripts/risk.mjs --staged
  node scripts/risk.mjs --range origin/main...HEAD
  node scripts/risk.mjs --files a.py --json

档位：<20 低 / 20–50 中 / >50 高
`);
    return;
  }

  const files = args.files.length ? args.files : gitFiles(args);
  if (!files.length) {
    console.error('[risk] 没有文件可评估。用 --files 指定，或 --staged / --range 从 git 取。');
    process.exitCode = 2;
    return;
  }

  const r = score(files);

  if (args.json) {
    console.log(JSON.stringify(r, null, 2));
    return;
  }

  console.log(`\n风险分：${r.score} / 100   档位：${r.tier.toUpperCase()}`);
  console.log(`改动文件数：${r.fileCount}`);
  console.log(`命中标签：${r.tags.length ? r.tags.join(', ') : '（无）'}`);
  console.log(`建议门禁：${TIER_ADVICE[r.tier]}\n`);

  if (r.hits.length) {
    console.log('命中明细：');
    for (const h of r.hits) {
      console.log(`  ${String(h.w).padStart(4)}  ${h.tag.padEnd(18)} ${h.file}`);
    }
    console.log('');
  }
}

// 直接执行时才跑 main（被 import 时不跑）
if (import.meta.url === `file://${process.argv[1].replace(/\\/g, '/')}` ||
    process.argv[1]?.endsWith('risk.mjs')) {
  main();
}
