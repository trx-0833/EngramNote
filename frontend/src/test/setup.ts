/**
 * @file Vitest 全局启动（配置见 vite.config.ts 的 `test` 段）
 *
 * 只做两件事，其余保持显式：
 * 1. 接上 jest-dom 的断言（`toBeDisabled` / `toHaveTextContent` 等）；
 * 2. 每个用例后卸载已渲染的组件。
 *
 * ## 为什么不开 `globals: true`
 *
 * 开了之后 `describe` / `it` / `expect` 变成全局变量，测试文件里看不出
 * 它们从哪来；后端测试一律显式 `import pytest` 的那套写法在这里也适用。
 * 代价是每个文件多一行 import，换来的是"这个 API 属于谁"一眼可见。
 */
import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

/**
 * jsdom 不实现滚动相关的 DOM API
 *
 * `Element.prototype.scrollIntoView` 在 jsdom 里**根本不存在**（这是它
 * 明确的取舍：不做布局、不做滚动）。不补的话，任何走到"滚动到引用位置"
 * 那一步的代码都会抛 `TypeError: container.scrollIntoView is not a function`，
 * 而那是产品代码里完全正常的一行 —— 失败的形态会指向错误的地方。
 *
 * 补在**测试环境**而不是产品代码里：产品跑在真浏览器上，那里这个方法一直有；
 * 为了迁就 jsdom 去加 `if (typeof x === 'function')` 判断，等于让测试环境
 * 的缺陷污染产品代码。
 */
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = vi.fn();
}

afterEach(() => {
  cleanup();
});
