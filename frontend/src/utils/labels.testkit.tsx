/**
 * @file 取**当前语言**下的枚举标签表（测试专用）
 *
 * ## 为什么测试需要它（i18n 阶段 3）
 *
 * 标签文案从 `utils/labels.ts` 的模块级常量搬进了消息表，取用要经过
 * `useLabels()`（一个 hook）。而测试里想断言"四档按钮显示的是那四个档位名"
 * 时，需要**知道**那四个名字是什么。
 *
 * 两条错路：
 * 1. **把中文抄进测试** —— 抄了就与消息表脱钩，改文案时测试不会跟着走，
 *    于是"断言通过但界面已经不是这样了"；
 * 2. **在测试里调 `useLabels()`** —— 测试函数不是组件，调不了 hook。
 *
 * 所以这里给一个**探针组件**：它渲染一次，把 `useLabels()` 的结果交回给测试。
 *
 * ## 用法
 *
 * ```tsx
 * const labels = await readLabels();
 * for (const opt of labels.selfRatingOptions) {
 *   expect(screen.getByText(opt.label)).toBeInTheDocument();
 * }
 * ```
 *
 * ⚠️ 它读到的是**当前语言**（测试环境钉在 `zh-CN`，见 `src/test/setup.ts`）。
 * 要测英文路径时先 `applyLocale('en')` 再调它。
 */

import { render } from '@testing-library/react';

import { LocaleProvider } from '../i18n';
import { useLabels } from './labels';

type Labels = ReturnType<typeof useLabels>;

/**
 * 渲染一个探针把 `useLabels()` 的值取出来
 *
 * 实现上借用 `render` 的同步语义：探针在渲染期就把值写进闭包变量，
 * `render` 返回时它已经就绪 —— 所以本函数可以**同步**返回（不必 await）。
 * 用完立刻 `unmount`，不在 document 上留残留节点。
 */
export function readLabels(): Labels {
  let captured: Labels | null = null;

  function Probe() {
    captured = useLabels();
    return null;
  }

  const { unmount } = render(
    <LocaleProvider>
      <Probe />
    </LocaleProvider>,
  );
  unmount();

  if (!captured) {
    throw new Error('readLabels 探针没有渲染 —— useLabels 的实现可能变了');
  }
  return captured;
}
