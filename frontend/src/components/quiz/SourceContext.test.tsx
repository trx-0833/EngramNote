/**
 * @file 原文语境组件的交互测试（阶段 3.13）
 *
 * 这个组件的失败模式都是**静默**的，所以每一条都要有测试盯着：
 * - 永远转圈（加载卡住）→ 用户以为原文不存在
 * - 把"没有原文"与"加载失败"显示成同一句话 → 用户不知道该重试还是该死心
 * - 折叠状态下就把正文渲染出来 → 白请求一次，且用户提前看到答案
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { getKnowledgeCard } from '../../api/qa';
import SourceContext from './SourceContext';

vi.mock('../../api/qa', () => ({
  getKnowledgeCard: vi.fn(),
}));

const mockedGet = vi.mocked(getKnowledgeCard);

function renderContext(props: { cardId?: string | null; noteId?: string | null }) {
  return render(
    <MemoryRouter>
      <SourceContext {...props} />
    </MemoryRouter>,
  );
}

describe('SourceContext', () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it('没有 card_id 时什么都不渲染（不发无效请求）', () => {
    const { container } = renderContext({ cardId: null });
    expect(container).toBeEmptyDOMElement();
    expect(mockedGet).not.toHaveBeenCalled();
  });

  it('★ 折叠时**不**请求原文（一次复习几十道题，不能每题都拉一遍）', () => {
    renderContext({ cardId: 'c1' });
    expect(screen.getByRole('button', { name: '查看原文语境' })).toBeInTheDocument();
    expect(mockedGet).not.toHaveBeenCalled();
  });

  it('展开后拉取并显示原文；再点收起', async () => {
    mockedGet.mockResolvedValue({ source_text: '原始段落内容' } as never);
    renderContext({ cardId: 'c1' });

    await userEvent.click(screen.getByRole('button', { name: '查看原文语境' }));

    expect(await screen.findByText('原始段落内容')).toBeInTheDocument();
    expect(mockedGet).toHaveBeenCalledWith('c1');

    await userEvent.click(screen.getByRole('button', { name: '收起原文语境' }));
    expect(screen.queryByText('原始段落内容')).not.toBeInTheDocument();
  });

  it('★ 只请求一次：收起再展开不重复拉取', async () => {
    mockedGet.mockResolvedValue({ source_text: '内容' } as never);
    renderContext({ cardId: 'c1' });

    await userEvent.click(screen.getByRole('button', { name: '查看原文语境' }));
    await screen.findByText('内容');
    await userEvent.click(screen.getByRole('button', { name: '收起原文语境' }));
    await userEvent.click(screen.getByRole('button', { name: '查看原文语境' }));

    expect(mockedGet).toHaveBeenCalledTimes(1);
  });

  it('★ "没有原文"与"加载失败"必须是两句不同的话', async () => {
    mockedGet.mockResolvedValue({ source_text: '' } as never);
    renderContext({ cardId: 'c1' });
    await userEvent.click(screen.getByRole('button', { name: '查看原文语境' }));
    expect(await screen.findByText(/没有记下原文段落/)).toBeInTheDocument();
  });

  it('★ 加载失败要显示错误与重试，而不是永远转圈', async () => {
    mockedGet.mockRejectedValueOnce(new Error('网络不通'));
    renderContext({ cardId: 'c1' });
    await userEvent.click(screen.getByRole('button', { name: '查看原文语境' }));

    expect(await screen.findByText('网络不通')).toBeInTheDocument();
    const retry = screen.getByRole('button', { name: '重试' });

    // 重试要真的再请求一次，并且成功后把错误清掉
    mockedGet.mockResolvedValueOnce({ source_text: '终于拿到了' } as never);
    await userEvent.click(retry);
    expect(await screen.findByText('终于拿到了')).toBeInTheDocument();
    expect(screen.queryByText('网络不通')).not.toBeInTheDocument();
  });

  it('有 note_id 时提供跳回笔记原文的链接', async () => {
    mockedGet.mockResolvedValue({ source_text: '内容' } as never);
    renderContext({ cardId: 'c1', noteId: 'n1' });
    await userEvent.click(screen.getByRole('button', { name: '查看原文语境' }));

    const link = await screen.findByRole('link', { name: /在笔记中查看完整原文/ });
    expect(link).toHaveAttribute('href', '/notes/n1?view=clean');
  });

  it('加载中显示提示而不是空白', async () => {
    let resolve!: (v: unknown) => void;
    mockedGet.mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }) as never,
    );
    renderContext({ cardId: 'c1' });
    await userEvent.click(screen.getByRole('button', { name: '查看原文语境' }));

    expect(screen.getByText('正在读取原文...')).toBeInTheDocument();
    resolve({ source_text: '内容' });
    await waitFor(() => expect(screen.getByText('内容')).toBeInTheDocument());
  });
});
