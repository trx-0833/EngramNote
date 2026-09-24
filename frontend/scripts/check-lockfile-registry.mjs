#!/usr/bin/env node
/**
 * @file 锁文件取源检查（防止 `package-lock.json` 被镜像源污染）
 *
 * ## 为什么需要它
 *
 * 本项目的 `package-lock.json` 里 **378 个条目全部来自 `registry.npmjs.org`**，
 * 这是刻意维持的状态。但有两件事会反复破坏它：
 *
 * 1. **开发机上的全局 `.npmrc`**：只要 `npm config get registry` 指向镜像
 *    （例如 `registry.npmmirror.com`），一次不带 `--registry` 的 `npm install`
 *    就会把**镜像 URL 批量写回锁文件**。这不是假想 —— 本机实际就是这种配置。
 * 2. **Dependabot 每次重写锁文件**：都是一次重犯机会。
 *
 * 失效形态是**静默**的：锁文件照样能 `npm ci` 成功，没人会注意到包里多了一批
 * 第三方主机名，直到有人审阅供应链。
 *
 * ## 用法
 *
 *     node scripts/check-lockfile-registry.mjs            # 检查（CI/本地都可用）
 *     node scripts/check-lockfile-registry.mjs --quiet    # 只失败时才输出
 *
 * 退出码：0 = 全部来自允许的主机；1 = 存在不允许的主机（会逐条列出）。
 *
 * ## 与 pytest 守卫的关系
 *
 * `backend/tests/test_lockfile_registry.py` 是同一判据的 **pytest 版**（CI 里必然跑到）。
 * 有意保留两份而不是"一份调另一份"：Node 侧给人手动跑（改依赖后立刻自查），
 * Python 侧给 CI 与本地测试套件 —— 两边都**直接读锁文件**，不互相依赖，
 * 任何一边失效都不会让判据静默消失。
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

/** 唯一允许的取源主机。改它之前先读文件头——这个值同时写在 pytest 守卫里。 */
const ALLOWED_HOST = 'registry.npmjs.org';

const quiet = process.argv.includes('--quiet');
const here = dirname(fileURLToPath(import.meta.url));
const lockPath = join(here, '..', 'package-lock.json');

const lock = JSON.parse(readFileSync(lockPath, 'utf8'));
const packages = lock.packages ?? {};

/** @type {Map<string, string[]>} 主机 → 该主机下的条目名 */
const byHost = new Map();
/** @type {string[]} 有 resolved 但没有 integrity 的条目 */
const missingIntegrity = [];

for (const [name, meta] of Object.entries(packages)) {
  if (!name) continue; // 根条目没有 resolved
  const resolved = meta.resolved;
  if (typeof resolved !== 'string') continue;
  let host;
  try {
    host = new URL(resolved).host;
  } catch {
    host = `(无法解析: ${resolved.slice(0, 60)})`;
  }
  if (!byHost.has(host)) byHost.set(host, []);
  byHost.get(host).push(name.replace(/^node_modules\//, ''));
  if (typeof meta.integrity !== 'string' || meta.integrity === '') {
    missingIntegrity.push(name);
  }
}

const total = [...byHost.values()].reduce((n, list) => n + list.length, 0);
const bad = [...byHost.entries()].filter(([host]) => host !== ALLOWED_HOST);

if (!quiet) {
  console.log(`锁文件：${lockPath}`);
  console.log(`lockfileVersion: ${lock.lockfileVersion}`);
  console.log(`带 resolved 的条目: ${total}`);
  for (const [host, list] of [...byHost.entries()].sort((a, b) => b[1].length - a[1].length)) {
    const mark = host === ALLOWED_HOST ? 'OK  ' : 'BAD ';
    console.log(`  ${mark}${host.padEnd(30)} ${String(list.length).padStart(4)} 条`);
  }
}

let failed = false;

if (bad.length > 0) {
  failed = true;
  console.error(`\n✗ 发现 ${bad.length} 个不允许的取源主机：`);
  for (const [host, list] of bad) {
    console.error(`  ${host} —— ${list.length} 条`);
    for (const name of list.slice(0, 10)) console.error(`      ${name}`);
    if (list.length > 10) console.error(`      …另有 ${list.length - 10} 条`);
  }
  console.error(
    '\n修法：删掉 package-lock.json 后用官方源重装 ——\n' +
      '  npm install --registry=https://registry.npmjs.org\n' +
      '（本机全局 npm 源可能是镜像，必须显式带 --registry，否则会再次污染）',
  );
}

if (missingIntegrity.length > 0) {
  failed = true;
  console.error(`\n✗ ${missingIntegrity.length} 个条目有 resolved 却缺 integrity：`);
  for (const name of missingIntegrity.slice(0, 10)) console.error(`    ${name}`);
}

if (!failed) {
  if (!quiet) console.log(`\n✓ 全部 ${total} 个条目都来自 ${ALLOWED_HOST}，且都带 integrity`);
  process.exit(0);
}
process.exit(1);
