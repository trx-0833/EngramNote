/**
 * `QuizNavigator` 的测试（想法 8：刷题页要能前后翻、能跳题）
 *
 * ## 这个组件为什么值得单独测
 *
 * 它要同时把三件事说对，而这三件事**都不是视觉能一眼验证的**：
 *
 * 1. **四态判定**：未答 / 答对 / 答错 / 当前。判定错一位（比如把"答错"
 *    判成"未答"）在界面上只差一个颜色，肉眼很难发现，而用户会因此
 *    以为那道题没做过。
 * 2. **可访问名是完整句子**：读屏只念"7"是没用的，必须念
 *    "跳到第 7 题，已答对"。这条只有断言 `aria-label` 才能固定。
 * 3. **符号与语义双通道**：`✓`/`✗` 是给眼睛的，必须 `aria-hidden`，
 *    否则读屏会念出"对勾"这种噪音。
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import QuizNavigator, { type QuizNavItem } from './QuizNavigator';

/** 造一批导航项：前 n 项按给定状态，其余未答 */
function makeItems(spec: Array<'correct' | 'incorrect' | 'pending'>): QuizNavItem[] {
  return spec.map((s, i) => ({
    index: i,
    submitted: s !== 'pending',
    isCorrect: s === 'correct' ? true : s === 'incorrect' ? false : undefined,
  }));
}

describe('QuizNavigator', () => {
  it('渲染每个题号，且可访问名是完整的「跳到第 N 题 + 状态」', () => {
    render(
      <QuizNavigator
        items={makeItems(['correct', 'incorrect', 'pending'])}
        currentIndex={2}
        onJump={vi.fn()}
      />,
    );

    // 第 1 题答对
    expect(screen.getByRole('button', { name: '跳到第 1 题，已答对' })).toBeInTheDocument();
    // 第 2 题答错
    expect(screen.getByRole('button', { name: '跳到第 2 题，已答错' })).toBeInTheDocument();
    // 第 3 题未作答（且是当前题）
    expect(screen.getByRole('button', { name: '跳到第 3 题，未作答' })).toBeInTheDocument();
  });

  it('★ 四态各有对应的类名（判定错一位肉眼看不出来，只有断言能固定）', () => {
    const { container } = render(
      <QuizNavigator
        items={makeItems(['correct', 'incorrect', 'pending'])}
        currentIndex={2}
        onJump={vi.fn()}
      />,
    );

    const items = container.querySelectorAll('.quiz-nav-item');
    expect(items[0].className).toContain('quiz-nav-correct');
    expect(items[1].className).toContain('quiz-nav-incorrect');
    // 当前题 + 未作答：带 current，**不**带对错类
    expect(items[2].className).toContain('quiz-nav-current');
    expect(items[2].className).not.toContain('quiz-nav-correct');
    expect(items[2].className).not.toContain('quiz-nav-incorrect');
  });

  it('当前题用 aria-current 标注（这是"当前位置"的标准语义）', () => {
    render(
      <QuizNavigator
        items={makeItems(['pending', 'pending', 'pending'])}
        currentIndex={1}
        onJump={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /跳到第 2 题/ })).toHaveAttribute(
      'aria-current',
      'true',
    );
    expect(screen.getByRole('button', { name: /跳到第 1 题/ })).not.toHaveAttribute('aria-current');
  });

  it('点击题号回调对应下标（0 基）', async () => {
    const onJump = vi.fn();
    render(
      <QuizNavigator
        items={makeItems(['pending', 'pending', 'pending', 'pending'])}
        currentIndex={0}
        onJump={onJump}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: /跳到第 3 题/ }));
    expect(onJump).toHaveBeenCalledWith(2);
  });

  it('★ 符号是 aria-hidden —— 否则读屏会念出"对勾"这种噪音', () => {
    const { container } = render(
      <QuizNavigator
        items={makeItems(['correct', 'incorrect'])}
        currentIndex={0}
        onJump={vi.fn()}
      />,
    );
    const marks = container.querySelectorAll('.quiz-nav-mark');
    expect(marks.length).toBe(2);
    for (const m of marks) {
      expect(m).toHaveAttribute('aria-hidden', 'true');
    }
  });

  it('外层是带无障碍名的 group（读屏先报"题目导航"再进组）', () => {
    render(<QuizNavigator items={makeItems(['pending'])} currentIndex={0} onJump={vi.fn()} />);
    expect(screen.getByRole('group', { name: '题目导航' })).toBeInTheDocument();
  });

  it('未作答且不是当前题时不渲染符号（不假装它已作答）', () => {
    const { container } = render(
      <QuizNavigator
        items={makeItems(['pending', 'pending', 'pending'])}
        currentIndex={1}
        onJump={vi.fn()}
      />,
    );
    const items = container.querySelectorAll('.quiz-nav-item');
    // 第 1 题：未作答、非当前 → 无符号
    expect(items[0].querySelector('.quiz-nav-mark')).toBeNull();
    // 第 2 题：当前题 → 有 ● 占位
    expect(items[1].querySelector('.quiz-nav-mark')?.textContent).toBe('●');
  });

  it('50 道题时不崩、且题号连续', () => {
    const items = makeItems(Array(50).fill('pending'));
    render(<QuizNavigator items={items} currentIndex={49} onJump={vi.fn()} />);
    expect(screen.getAllByRole('button')).toHaveLength(50);
    expect(screen.getByRole('button', { name: /跳到第 50 题/ })).toBeInTheDocument();
  });
});
