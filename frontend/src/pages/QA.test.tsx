/**
 * @file 智能问答页的两栏骨架测试（visual-refactor-plan 批次 E7）
 *
 * ## 为什么只测骨架，不测问答流程
 *
 * 问答流程要走 SSE 流式响应（`parseSSEStream` + `askQuestionStream` 的 mock），
 * 那是另一件事，且这一批**没有改动**那条路径的任何一个字节（`handleAsk` 与
 * 所有 `setHistory` 的写法逐字保留）。这里要守的是本批**真正新加的东西**：
 * 引用来源从"卡片里的一段"变成"与对话流并列的常驻右栏"。
 *
 * 两条断言各自防一种走形：
 *
 * | 断言 | 防的是 |
 * |---|---|
 * | 右栏是 `complementary` 且可访问名是「引用来源」 | 右栏被换成匿名 `<div>`（读屏用户再也跳不到它） |
 * | 右栏**不在**对话流容器里 | "两栏"退化成"右栏被塞进主列"——那时它仍在页面上、语义也对，只有布局错了 |
 * | 没有提问时右栏给说明而不是空白 | 空右栏看起来像加载失败 |
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { QueryWrapper } from '../test/render';
import QA from './QA';
// 需要按类名查"右栏是不是独立于主列"—— 这是布局事实，
// 语义查询看不见它（css-convention §6 允许的少数例外）
import styles from './QA.module.css';

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client');
  return { ...actual, askQuestionStream: vi.fn() };
});

/**
 * 渲染被测页面
 *
 * ⚠️ 2026-11-13 起必须套 `QueryWrapper`：问答页用 `useQuery` 拉历史会话列表
 * （`GET /api/qa/sessions`），没有 Provider 时直接抛
 * `No QueryClient set, use QueryClientProvider`。`MemoryRouter` 仍要保留 ——
 * `useUrlState` 读写 `?s=`（当前会话）依赖路由上下文。
 */
function renderPage() {
  return render(
    <QueryWrapper>
      <MemoryRouter initialEntries={['/qa']}>
        <QA />
      </MemoryRouter>
    </QueryWrapper>,
  );
}

describe('QA 页的骨架（2026-11-14 改版：两栏 + 底部输入）', () => {
  it('引用不再有页面级 aside；来源属于每一轮回答自己的折叠', () => {
    const { container } = renderPage();

    // 常驻右栏已删除：页面里不该再有 complementary 区域（它曾把主区挤到 300px 宽）
    expect(screen.queryByRole('complementary')).toBeNull();
    // 主列存在，且消息流与输入框都在它里面（输入框不能再当 grid 的独立子项，
    // 那正是改版前"主区被压成竖排文字"的根因）
    const main = container.querySelector(`.${styles.qaMain}`);
    expect(main).not.toBeNull();
    expect(main?.querySelector(`.${styles.qaComposer}`)).not.toBeNull();
    expect(main?.querySelector(`.${styles.qaThread}`)).not.toBeNull();
  });

  it('输入区是底部粘性的 textarea（多行自适应，而不是单行 input）', () => {
    renderPage();
    const box = screen.getByPlaceholderText(/输入你的问题/);
    expect(box.tagName).toBe('TEXTAREA');
    expect(box).toHaveAttribute('rows', '1');
  });

  it('还没提问时给推荐问题，而不是一片空白', () => {
    renderPage();
    // 原来是"空右栏给一句说明"；现在空态由标题 + 推荐问题承担
    expect(screen.getByText('输入问题开始问答')).toBeInTheDocument();
    expect(screen.getByText('试试这样问')).toBeInTheDocument();
    expect(
      screen.getAllByRole('button', { name: /总结我最近的笔记|浮充和均充|帮我解释/ }),
    ).toHaveLength(3);
  });

  it('输入区与空状态都还在（本批没有动问答流程本身）', () => {
    renderPage();

    expect(screen.getByPlaceholderText(/输入你的问题/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '提问' })).toBeDisabled(); // 空问题不能提交
    expect(screen.getByText('输入问题开始问答')).toBeInTheDocument();
  });
});
