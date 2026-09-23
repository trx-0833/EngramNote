/**
 * @file 四档自评控件的测试（5.12：两条复习流程共用的评分控件）
 *
 * 这个控件是"答题复习"与"卡片复习"唯一**完全一致**的交互件，所以它的契约
 * 必须钉死：档位取值（SM-2 的 0/3/4/5）、提交中禁用、以及**只有答题复习
 * 才有**的跳过逃生口。任何一条漂移都会让两条流程在同一件事上给出不同结果 ——
 * 而自评会真的改变调度（间隔一经写入无法事后纠正）。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { selfRatingOptions } from '../../utils/labels';
import SelfRatingButtons, { selfRatingLabel } from './SelfRatingButtons';

describe('SelfRatingButtons 的四档', () => {
  it('四档的标签与说明全部来自 utils/labels（单一数据源，不另写字面量）', () => {
    render(<SelfRatingButtons onRate={vi.fn()} />);

    for (const opt of selfRatingOptions) {
      expect(screen.getByText(opt.label)).toBeInTheDocument();
      expect(screen.getByText(opt.hint)).toBeInTheDocument();
    }
    expect(screen.getAllByRole('button')).toHaveLength(selfRatingOptions.length);
  });

  it.each([0, 3, 4, 5])(
    '★ 点选 quality=%i 的档位要原样上报（SM-2 语义不能被改写）',
    async (quality) => {
      const onRate = vi.fn();
      render(<SelfRatingButtons onRate={onRate} />);
      const opt = selfRatingOptions.find((o) => o.quality === quality);
      if (!opt) throw new Error(`selfRatingOptions 缺少 quality=${quality}`);

      await userEvent.click(screen.getByText(opt.label));

      expect(onRate).toHaveBeenCalledWith(quality);
      expect(onRate).toHaveBeenCalledTimes(1);
    },
  );

  it('上方的提示语只有调用方给了才渲染（卡片复习有，答题复习没有）', () => {
    const { unmount } = render(<SelfRatingButtons onRate={vi.fn()} />);
    expect(screen.queryByText('刚才想得起来吗？')).not.toBeInTheDocument();
    unmount();

    render(<SelfRatingButtons onRate={vi.fn()} prompt="刚才想得起来吗？" />);
    expect(screen.getByText('刚才想得起来吗？')).toBeInTheDocument();
  });
});

describe('SelfRatingButtons 的提交中状态', () => {
  it('★ 提交中禁用全部按钮，并给出"正在记录自评"的反馈', () => {
    render(<SelfRatingButtons onRate={vi.fn()} submitting />);

    for (const opt of selfRatingOptions) {
      expect(screen.getByText(opt.label).closest('button')).toBeDisabled();
    }
    expect(screen.getByText('正在记录自评...')).toBeInTheDocument();
  });

  it('禁用期间点按钮不会上报（防连点重复推进调度）', async () => {
    const onRate = vi.fn();
    render(<SelfRatingButtons onRate={onRate} submitting />);

    await userEvent.click(screen.getByText('想起来了'));

    expect(onRate).not.toHaveBeenCalled();
  });
});

describe('SelfRatingButtons 的跳过逃生口', () => {
  it('★ 传了 onSkip 才出现（答题复习的两阶段提交有占位记录可放弃；卡片复习没有）', async () => {
    const onSkip = vi.fn();
    render(<SelfRatingButtons onRate={vi.fn()} onSkip={onSkip} />);

    await userEvent.click(screen.getByRole('button', { name: '跳过自评，先做下一题' }));

    expect(onSkip).toHaveBeenCalledTimes(1);
  });

  it('不传 onSkip 时页面上没有这个入口', () => {
    render(<SelfRatingButtons onRate={vi.fn()} />);
    expect(screen.queryByRole('button', { name: '跳过自评，先做下一题' })).not.toBeInTheDocument();
  });

  it('提交中不显示跳过入口（避免与正在进行的自评抢结果）', () => {
    render(<SelfRatingButtons onRate={vi.fn()} onSkip={vi.fn()} submitting />);
    expect(screen.queryByRole('button', { name: '跳过自评，先做下一题' })).not.toBeInTheDocument();
  });
});

describe('selfRatingLabel', () => {
  it('把 quality 翻译成用户看到的档位名', () => {
    expect(selfRatingLabel(4)).toBe('想起来了');
    expect(selfRatingLabel(0)).toBe('完全忘记');
  });

  it('未知档位回退成 quality=N，而不是 undefined', () => {
    expect(selfRatingLabel(2)).toBe('quality=2');
  });
});
