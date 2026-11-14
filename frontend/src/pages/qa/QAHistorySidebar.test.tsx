import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { QASessionSummary } from '../../api/qaHistory';
import QAHistorySidebar from './QAHistorySidebar';

function session(id: string, updatedAt: string, extra: Partial<QASessionSummary> = {}) {
  return {
    id,
    title: `对话 ${id}`,
    last_question: null,
    surface: 'global',
    turn_count: 2,
    created_at: updatedAt,
    updated_at: updatedAt,
    ...extra,
  } as QASessionSummary;
}

const NOW = new Date();
const TODAY = new Date(NOW.getFullYear(), NOW.getMonth(), NOW.getDate(), 9, 0).toISOString();
const LONG_AGO = new Date(NOW.getFullYear() - 1, 0, 1, 9, 0).toISOString();

function renderSidebar(overrides: Partial<React.ComponentProps<typeof QAHistorySidebar>> = {}) {
  const props = {
    sessions: [session('a', TODAY), session('b', LONG_AGO)],
    loading: false,
    keyword: '',
    onKeywordChange: vi.fn(),
    onOpenSession: vi.fn(),
    onNewChat: vi.fn(),
    onOpenManage: vi.fn(),
    collapsed: false,
    onToggleCollapsed: vi.fn(),
    total: 2,
    ...overrides,
  };
  const utils = render(<QAHistorySidebar {...props} />);
  return { ...utils, props };
}

describe('QAHistorySidebar：宽屏常驻会话栏', () => {
  it('按时间分组渲染，当前会话标 aria-current', () => {
    renderSidebar({ activeSessionId: 'a' });

    // 分组标题来自 `updated_at` 的前端计算（今天 / 更早）
    expect(screen.getByRole('heading', { name: '今天' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '更早' })).toBeInTheDocument();

    const active = screen.getByRole('button', { name: /对话 a/ });
    expect(active).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: /对话 b/ })).not.toHaveAttribute('aria-current');
  });

  it('点会话项与「新建」都会回调（侧栏只是入口，状态在页面里）', async () => {
    const user = userEvent.setup();
    const { props } = renderSidebar();

    await user.click(screen.getByRole('button', { name: /对话 a/ }));
    expect(props.onOpenSession).toHaveBeenCalledWith('a');

    // 「＋ 新建对话」与页头同名按钮的可访问名不同（前缀 ＋），这里按侧栏那一个点
    await user.click(screen.getByRole('button', { name: /＋\s*新建对话/ }));
    expect(props.onNewChat).toHaveBeenCalledTimes(1);
  });

  it('搜索框是受控的：输入触发 onKeywordChange，且有显式 label', async () => {
    const user = userEvent.setup();
    const { props } = renderSidebar();

    const input = screen.getByLabelText('搜索对话');
    await user.type(input, '浮充');
    expect(props.onKeywordChange).toHaveBeenCalled();
  });

  it('收起时只留按钮，列表与搜索框都不在 DOM 里（不是只靠 CSS 隐藏）', () => {
    renderSidebar({ collapsed: true });
    expect(screen.queryByLabelText('搜索对话')).toBeNull();
    expect(screen.queryByRole('button', { name: /对话 a/ })).toBeNull();
    // 折叠按钮的 aria-expanded 要如实反映状态
    expect(screen.getByRole('button', { name: '展开对话历史' })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  it('服务端条数多于本页时说明只显示了最近几条', () => {
    renderSidebar({ total: 250 });
    expect(screen.getByText(/仅显示最近 2 条（共 250 条）/)).toBeInTheDocument();
  });
});
