/**
 * @file 回车键约定的测试（5.12：两条复习流程共用的键盘行为）
 *
 * 这里钉两件在真机上"时灵时不灵"、在静态检查里完全看不见的事：
 *
 * 1. **按钮目标必须放行**。答题复习此前无差别 `preventDefault()`，
 *    于是焦点落在"查看原文语境"上按回车，原文没展开、人却被带到了下一题；
 *    焦点落在四档自评上按回车，自评根本没提交（只有 Space 能提交）。
 *    卡片复习在附录 AA.7 已加了守卫，这里把它变成两条流程共同的约定。
 * 2. **焦点收回容器是可选项**。卡片复习页没有任何输入控件，必须收回；
 *    答题复习页的焦点要留在输入框里，不能被抢走。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { useReviewKeyboard } from './useReviewKeyboard';

function Harness({ onEnter, refocusKey }: { onEnter: () => void; refocusKey?: string }) {
  const { containerRef, handleKeyDown } = useReviewKeyboard({ onEnter, refocusKey });
  return (
    <div ref={containerRef} tabIndex={-1} onKeyDown={handleKeyDown} data-testid="container">
      <input data-testid="input" />
      <button>下一题</button>
    </div>
  );
}

describe('useReviewKeyboard 的回车语义', () => {
  it('★ 焦点不在按钮上时，回车推进当前这一步', async () => {
    const onEnter = vi.fn();
    render(<Harness onEnter={onEnter} />);

    screen.getByTestId('input').focus();
    await userEvent.keyboard('{Enter}');

    expect(onEnter).toHaveBeenCalledTimes(1);
  });

  it('★ 焦点在按钮上时放行，交给按钮的原生回车激活（答题复习此前缺这条守卫）', async () => {
    const onEnter = vi.fn();
    render(<Harness onEnter={onEnter} />);

    screen.getByRole('button', { name: '下一题' }).focus();
    await userEvent.keyboard('{Enter}');

    expect(onEnter).not.toHaveBeenCalled();
  });

  it('Shift+Enter 不触发（简答题里要能换行）', async () => {
    const onEnter = vi.fn();
    render(<Harness onEnter={onEnter} />);

    screen.getByTestId('input').focus();
    await userEvent.keyboard('{Shift>}{Enter}{/Shift}');

    expect(onEnter).not.toHaveBeenCalled();
  });

  it('其他按键不触发', async () => {
    const onEnter = vi.fn();
    render(<Harness onEnter={onEnter} />);

    screen.getByTestId('input').focus();
    await userEvent.keyboard('a{Space}');

    expect(onEnter).not.toHaveBeenCalled();
  });
});

describe('useReviewKeyboard 的焦点策略', () => {
  it('★ 给了 refocusKey：换项/换阶段后焦点回到容器（卡片复习页没有输入控件可依靠）', async () => {
    const { rerender } = render(<Harness onEnter={vi.fn()} refocusKey="0:front" />);
    const input = screen.getByTestId('input');

    input.focus();
    expect(input).toHaveFocus();

    rerender(<Harness onEnter={vi.fn()} refocusKey="0:revealed" />);

    expect(screen.getByTestId('container')).toHaveFocus();
  });

  it('★ 不给 refocusKey：挂载后也不抢焦点，且换项时焦点留在用户的输入框里', () => {
    const { rerender } = render(<Harness onEnter={vi.fn()} />);
    // 挂载就不该抢：答题复习页的填空/简答输入框是靠 autoFocus 自己拿焦点的
    expect(screen.getByTestId('container')).not.toHaveFocus();

    const input = screen.getByTestId('input');
    input.focus();
    rerender(<Harness onEnter={vi.fn()} />);

    expect(input).toHaveFocus();
  });
});
