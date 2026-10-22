/**
 * 状态轮询的**卸载守卫**（C1 / 5.2 B2 第三处）
 *
 * ## 为什么这是一个"源码结构"守卫，而不是行为用例
 *
 * 要守的性质是：「`await getNote()` 回来时组件已经卸载 ⇒ **不要** setState」。
 * 而这件事在 jsdom 里**观察不到**：
 *
 * - 对已卸载组件 setState 不会抛错；
 * - React 18 **移除了**"Can't perform a React state update on an unmounted
 *   component"那条警告，所以 `console.error` 的 spy 恒为空 ——
 *   用那个做断言是**空转**（有没有守卫都绿）；
 * - 卸载之后没有任何可观察的输出面（DOM 没了、渲染计数也不再增长）。
 *
 * 所以退一步守**结构**：断言"每个轮询回调在 `await` **之后**确实检查了 `alive`"。
 * 这不能证明守卫语义正确（那靠人读），但能挡住"重构时顺手把守卫删掉" ——
 * 而那正是这个守卫唯一会失效的方式。
 *
 * ⚠️ 这类守卫的价值上限就在这儿：它证明"代码还在"，不证明"代码对"。
 * 计划 §2.4 的 B2 完整形态是把笔记本体也搬进 query（那样由 query 负责），
 * 那时这个文件应当**整体删掉**而不是继续维护。
 */
import { describe, expect, it, vi } from 'vitest';

/**
 * ⚠️ 本项目**没有** `@types/node`，所以自己声明用到的那两个 API，
 * 并用 `vi.importActual` 取真实的 `node:fs` / `node:path`。
 *
 * 第一版直接写 `readFileSync` + `__dirname`，`tsc` 报
 * `TS2304: Cannot find name '__dirname'` —— `__dirname` 是 CJS 全局，
 * 而测试文件在 ESM 语境下编译（`npm run build` 会连测试一起类型检查，
 * 所以 `vitest` 跑得绿不代表 `tsc` 过）。
 *
 * 形态照抄 `src/styles/mobile-input-font-size.test.ts:64-75` ——
 * 那是本仓库里既有的、跑得通的同一做法。
 */
interface NodeFs {
  readFileSync(path: string, encoding: 'utf8'): string;
}
const fs = await vi.importActual<NodeFs>('node:fs');

/**
 * 被测源码的绝对路径。
 *
 * ## 为什么用 `expect.getState().testPath` 而不是 `__dirname` / `import.meta.url`
 *
 * - `__dirname`：CJS 全局，本文件在 ESM 语境下编译 ⇒ `tsc` 报 TS2304；
 * - `import.meta.url`：实测在 Vitest 里拿到的是 **Vite 的模块 URL**
 *   （pathname 形如 `/src/pages/notedetail/…`），拼出来是
 *   `D:\src\pages\notedetail\useNoteDetailData.ts` ⇒ **ENOENT**；
 * - `expect.getState().testPath`：Vitest 给的是**原生绝对路径**，
 *   本仓库既有的 `src/styles/mobile-input-font-size.test.ts:117-122`
 *   就是这么定位的（注释写着"Vitest 给出的是原生绝对路径"）。
 *
 * 形态照抄既有做法，比自己发明一套路径推导可靠。
 */
function thisDir(): string {
  const testPath = expect.getState().testPath;
  if (!testPath) throw new Error('expect.getState().testPath 为空：定位不到被测源码');
  return testPath.replace(/[/\\][^/\\]*$/, '');
}

const SRC = fs.readFileSync(`${thisDir()}/useNoteDetailData.ts`, 'utf8');

/** 把两个状态轮询的 effect 各自切出来 */
function pollingBlocks(): string[] {
  const blocks: string[] = [];
  const re = /const interval = setInterval\(async \(\) => \{[\s\S]*?\}, 5000\);/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(SRC)) !== null) blocks.push(m[0]);
  return blocks;
}

describe('状态轮询的卸载守卫', () => {
  it('两个状态轮询（清洗中 / 学习中）都还在', () => {
    // 前提断言：下面的结构检查建立在"确实找到了两个轮询回调"之上。
    // 没有这条，把两个轮询整个删掉会让后面的断言对着空数组说话 —— 全绿。
    expect(pollingBlocks()).toHaveLength(2);
  });

  it('★ 每个轮询回调都在 `await getNote` **之后**检查 `alive`', () => {
    for (const [i, block] of pollingBlocks().entries()) {
      const awaitIdx = block.indexOf('await getNote');
      const guardIdx = block.indexOf('if (!alive) return;');
      expect(awaitIdx, `第 ${i + 1} 个轮询回调里找不到 await getNote`).toBeGreaterThan(-1);
      expect(
        guardIdx,
        `第 ${i + 1} 个轮询回调**没有**卸载守卫 —— ` +
          '`clearInterval` 只挡住"发起新请求"，挡不住已经在途的那一次回来 setState',
      ).toBeGreaterThan(-1);
      expect(
        guardIdx,
        `第 ${i + 1} 个轮询回调的守卫在 await **之前** —— 那样挡不住在途请求`,
      ).toBeGreaterThan(awaitIdx);
    }
  });

  it('★ 每个轮询的 cleanup 都把 `alive` 置 false 并清掉定时器', () => {
    // 只有"检查"没有"置位"的话，守卫恒为 true，等于没有。
    const cleanups = SRC.match(
      /return \(\) => \{\s*alive = false;\s*clearInterval\(interval\);\s*\};/g,
    );
    expect(cleanups, '两个轮询的 cleanup 都要置 alive=false 并 clearInterval').toHaveLength(2);
  });

  it('守卫变量在 effect 内部声明（不是跨 effect 共用的 ref）', () => {
    // 共用 ref 的话，一个 effect 的 cleanup 会误伤另一个 effect 的在途请求。
    expect(SRC.match(/let alive = true;/g)).toHaveLength(2);
    expect(SRC).not.toContain('aliveRef');
  });
});
