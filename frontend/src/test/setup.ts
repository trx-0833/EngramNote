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
import { afterEach, beforeEach, vi } from 'vitest';

import { applyLocale } from '../i18n/locales';

/**
 * ★ 把测试环境的语言**钉死**在 `zh-CN`（i18n 阶段 0）
 *
 * ## 为什么这是整轮 i18n 最关键的一行
 *
 * 全仓 **502 处测试断言在按中文文案查询**（`KnowledgeGraph.test.tsx` 146 处、
 * `Projects.test.tsx` 96 处、`QuestionSets.test.tsx` 48 处…）。
 * 如果不钉住语言，`resolveInitialLocale()` 会去读 `navigator.language` ——
 * 那在 jsdom 里默认是 `en-US`，于是**所有中文断言一起变红**。
 *
 * 钉住之后：那 502 处**一条都不用改**，而英文路径由专门的用例
 * （显式切到 `en`）覆盖。这条选择写在 i18n 方案的 §3.2 ——
 * 不钉的话，这轮就会从"做 i18n"变成"重写 502 处断言"，
 * 而那件事既容易引入假绿，也不是用户要的。
 *
 * ## 为什么用 `beforeEach` 而不是只写一次
 *
 * 有用例会显式 `setLocale('en')` 或往 localStorage 里写值来测英文路径。
 * 每个用例前复位，才不会让上一个用例的语言泄漏到下一个 ——
 * 那种泄漏的表现是"单独跑绿、一起跑红"，极难查。
 */
beforeEach(() => {
  localStorage.setItem('engramnote_locale', 'zh-CN');
  // 同步模块级变量（`applyLocale` 写的就是它，见 `i18n/locales.ts`）——
  // 只写 localStorage 不够：`api/client.ts` 读的是那个变量。
  applyLocale('zh-CN', { persist: false });
});

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
