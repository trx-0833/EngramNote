/**
 * A6：通用知识开关的用例。
 *
 * 守三件事：
 * - 默认**关**（产品承诺是"基于你的资料回答"）；
 * - 勾上后请求里 `allow_general_knowledge` 为 `true`（不是静默丢弃）；
 * - 警示文案只在打开时出现（默认不打扰）。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import QA from './QA';

const { askQuestionStream, getQaSuggestions } = vi.hoisted(() => ({
  askQuestionStream: vi.fn(),
  getQaSuggestions: vi.fn(),
}));

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client');
  return { ...actual, askQuestionStream, getQaSuggestions };
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/qa']}>
        <QA />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** 一个只发 meta + done 的流，让 handleAsk 能正常收尾 */
function emptyStream() {
  const enc = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(
        enc.encode('event: meta\ndata: {"session_id":"s-1","history_used":0}\n\n'),
      );
      controller.enqueue(enc.encode('event: done\ndata: {"turn_id":"t-1"}\n\n'));
      controller.close();
    },
  });
}

describe('A6 通用知识开关', () => {
  beforeEach(() => {
    askQuestionStream.mockReset();
    getQaSuggestions.mockReset();
    getQaSuggestions.mockResolvedValue([]);
    askQuestionStream.mockResolvedValue(emptyStream());
  });

  it('默认是关的，且不显示警示文案', async () => {
    renderPage();
    const box = await screen.findByRole('checkbox', { name: /也允许用模型自身知识回答/ });
    expect(box).not.toBeChecked();
    expect(screen.queryByText(/答案可能包含不来自你资料的内容/)).toBeNull();
  });

  it('勾上后显示警示文案', async () => {
    const user = userEvent.setup();
    renderPage();
    const box = await screen.findByRole('checkbox', { name: /也允许用模型自身知识回答/ });
    await user.click(box);
    expect(box).toBeChecked();
    expect(await screen.findByText(/答案可能包含不来自你资料的内容/)).toBeInTheDocument();
  });

  it('勾上后提问，请求里 allowGeneral 必须为 true', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('checkbox', { name: /也允许用模型自身知识回答/ }));

    const input = screen.getByPlaceholderText(/输入你的问题/);
    await user.type(input, '量子色动力学');
    await user.keyboard('{Enter}');

    await waitFor(() => expect(askQuestionStream).toHaveBeenCalled());
    // 第 5 个参数就是 allowGeneral
    expect(askQuestionStream.mock.calls[0][4]).toBe(true);
  });

  it('不勾时请求里是 false（不能是 undefined —— 开关状态要显式传）', async () => {
    const user = userEvent.setup();
    renderPage();

    const input = screen.getByPlaceholderText(/输入你的问题/);
    await user.type(input, '浮充是什么');
    await user.keyboard('{Enter}');

    await waitFor(() => expect(askQuestionStream).toHaveBeenCalled());
    expect(askQuestionStream.mock.calls[0][4]).toBe(false);
  });
});
