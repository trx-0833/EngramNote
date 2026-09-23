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

import QA from './QA';
// 需要按类名查"右栏是不是独立于主列"—— 这是布局事实，
// 语义查询看不见它（css-convention §6 允许的少数例外）
import styles from './QA.module.css';

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client');
  return { ...actual, askQuestionStream: vi.fn() };
});

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/qa']}>
      <QA />
    </MemoryRouter>,
  );
}

describe('QA 页的两栏骨架（批次 E7）', () => {
  it('右栏是可跳转的 complementary 区域，名字叫「引用来源」', () => {
    renderPage();

    const rail = screen.getByRole('complementary', { name: '引用来源' });
    expect(rail).toBeInTheDocument();
    expect(rail.tagName).toBe('ASIDE');
  });

  it('★ 右栏与对话流并列，而不是被塞进主列', () => {
    const { container } = renderPage();

    const rail = screen.getByRole('complementary', { name: '引用来源' });
    const main = container.querySelector(`.${styles.qaMain}`);

    expect(main).not.toBeNull();
    expect(main?.contains(rail)).toBe(false); // 并列：主列里不该有它
    // 两者的共同父节点才是那层两栏网格
    expect(main?.parentElement).toBe(rail.parentElement);
  });

  it('还没提问时右栏给出一句说明，而不是一片空白', () => {
    renderPage();

    // 空右栏看起来像加载失败 —— 这里要的是一句"它将会装什么"
    expect(screen.getByText(/会列在这里/)).toBeInTheDocument();
  });

  it('输入区与空状态都还在（本批没有动问答流程本身）', () => {
    renderPage();

    expect(screen.getByPlaceholderText(/输入你的问题/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '提问' })).toBeDisabled(); // 空问题不能提交
    expect(screen.getByText('输入问题开始问答')).toBeInTheDocument();
  });
});
