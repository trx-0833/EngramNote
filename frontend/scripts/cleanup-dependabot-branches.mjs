#!/usr/bin/env node
/**
 * @file 删除"已经没有开放 PR 的 Dependabot 分支"
 *
 * ## 为什么需要它（这不是洁癖，是一个被实测证明的坑）
 *
 * `dependabot.yml` 的 `open-pull-requests-limit` 只管**同时开着的 PR 数**，
 * **不关分支**。PR 被合并或关闭之后名额腾出来了，**分支却留在仓库里**，
 * 只有人或脚本显式删除才会消失。
 *
 * 实测：2026-09-24 复盘时仓库里有 **15 个分支**，其中 13 个是
 * "PR 早已关掉、分支还躺着"的。访客看到的就是"这个仓库有 15 个分支"。
 *
 * ⚠️ GitHub 仓库设置里的 **`delete_branch_on_merge` 对本项目无效** ——
 * 本项目的流程是"本地 merge 再 push"，**从不点 GitHub 的 Merge 按钮**，
 * 那个钩子根本不会被触发。所以要真删，只能自己动手。
 *
 * ## 安全约束（写进代码，不靠人记得）
 *
 * 1. **只删 `dependabot/` 前缀的分支** —— 其余一律跳过（默认分支更不可能被碰到）；
 * 2. **只删没有任何开放 PR 的分支** —— 还在被评审的分支不动；
 * 3. **默认 dry-run**：不加 `--delete` 只打印名单。
 *
 * ## 用法
 *
 *     node scripts/cleanup-dependabot-branches.mjs                # 只打印
 *     node scripts/cleanup-dependabot-branches.mjs --delete       # 真删
 *
 * 需要环境变量（workflow 里由 GitHub 自动注入）：
 *   `GITHUB_TOKEN`（需 `contents: write`）、`GITHUB_REPOSITORY`（`owner/repo`）
 *
 * 退出码：0 = 成功（含"没有可删的"）；1 = 有分支删除失败。
 */
import { setTimeout as sleep } from 'node:timers/promises';

const PREFIX = 'dependabot/';
const API = 'https://api.github.com';

const token = process.env.GITHUB_TOKEN;
const repo = process.env.GITHUB_REPOSITORY;
const doDelete = process.argv.includes('--delete');

if (!token || !repo) {
  console.error('缺少 GITHUB_TOKEN 或 GITHUB_REPOSITORY —— 这个脚本只能在 CI 里跑。');
  process.exit(1);
}

const headers = {
  Authorization: `Bearer ${token}`,
  Accept: 'application/vnd.github+json',
  'X-GitHub-Api-Version': '2022-11-28',
  'User-Agent': 'engramnote-cleanup-branches',
};

/** 带 403/429 重试的请求（GitHub 偶发限流） */
async function gh(path, init = {}, attempt = 1) {
  const res = await fetch(`${API}/repos/${repo}${path}`, { headers, ...init });
  if (res.status === 403 || res.status === 429) {
    if (attempt > 3)
      throw new Error(`${init.method ?? 'GET'} ${path} → ${res.status}（重试 3 次仍失败）`);
    const wait = Number(res.headers.get('retry-after') ?? 2) * 1000;
    await sleep(wait);
    return gh(path, init, attempt + 1);
  }
  return res;
}

async function listAll(path) {
  const out = [];
  for (let page = 1; ; page += 1) {
    const res = await gh(`${path}${path.includes('?') ? '&' : '?'}per_page=100&page=${page}`);
    if (!res.ok) throw new Error(`GET ${path} → ${res.status} ${await res.text()}`);
    const batch = await res.json();
    out.push(...batch);
    if (batch.length < 100) break;
  }
  return out;
}

const branches = await listAll('/branches');
const openPrs = await listAll('/pulls?state=open');

const dependabotBranches = branches.map((b) => b.name).filter((n) => n.startsWith(PREFIX));
const branchesWithOpenPr = new Set(openPrs.map((pr) => pr.head?.ref).filter(Boolean));

const deletable = dependabotBranches.filter((n) => !branchesWithOpenPr.has(n));
const kept = dependabotBranches.filter((n) => branchesWithOpenPr.has(n));

console.log(`仓库：${repo}`);
console.log(`分支总数：${branches.length}（其中 ${PREFIX}* 有 ${dependabotBranches.length} 个）`);
console.log(`开放 PR 数：${openPrs.length}`);

if (kept.length > 0) {
  console.log(`\n保留（仍有开放 PR，共 ${kept.length} 个）：`);
  for (const n of kept) console.log(`  keep  ${n}`);
}

if (deletable.length === 0) {
  console.log('\n没有可删的 Dependabot 分支 —— 无需操作。');
  process.exit(0);
}

console.log(`\n可删（无开放 PR，共 ${deletable.length} 个）：`);
for (const n of deletable) console.log(`  ${doDelete ? 'del ' : 'dry '} ${n}`);

if (!doDelete) {
  console.log('\n这是 dry-run。要真删请加 --delete（workflow_dispatch 输入 delete=true）。');
  process.exit(0);
}

let failed = 0;
for (const name of deletable) {
  // 分支名里可能有 `/`，作为路径段必须编码
  const res = await gh(`/git/refs/heads/${name.split('/').map(encodeURIComponent).join('/')}`, {
    method: 'DELETE',
  });
  if (res.status === 204) {
    console.log(`  已删除 ${name}`);
  } else {
    failed += 1;
    console.error(`  删除失败 ${name} → ${res.status} ${await res.text()}`);
  }
}

console.log(`\n完成：删除 ${deletable.length - failed} 个，失败 ${failed} 个。`);
process.exit(failed > 0 ? 1 : 0);
