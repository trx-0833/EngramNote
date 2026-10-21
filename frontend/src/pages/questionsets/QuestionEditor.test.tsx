/**
 * 题目行内编辑 / 删除（B5，2026-10-22）
 *
 * ## 这一组盯的是什么
 *
 * 编辑器的逻辑不复杂，但它有三条**错了不会报错**的分支：
 *
 * 1. **`options` 的解析与回填** —— 库里存的是 JSON 字符串，界面要的是多行文本。
 *    解析失败时若"吞掉"再保存，用户看不到的那部分内容就被静默删了。
 * 2. **清空解析** —— PUT 语义下 `explanation: ''` 必须变成 `null`（明确清空），
 *    而不是漏传一个字段。
 * 3. **删除是不可撤销的** —— 必须先确认，且确认框要说清"复习进度也会清掉"。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api/client', () => ({
  updateQuestion: vi.fn(),
  deleteQuestion: vi.fn(),
}));

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}));
vi.mock('../../components/Toast', () => ({ useToast: () => toast }));

const confirmMock = vi.hoisted(() => vi.fn());
vi.mock('../../components/ConfirmProvider', () => ({ useConfirm: () => confirmMock }));

import { deleteQuestion, updateQuestion, type QuizItem } from '../../api/client';
import { QuestionEditor, draftFromQuestion, optionsToText, textToOptions } from './QuestionEditor';

const mockedUpdate = vi.mocked(updateQuestion);
const mockedDelete = vi.mocked(deleteQuestion);

function makeQuestion(over: Partial<Record<string, unknown>> = {}): QuizItem {
  return {
    id: 'q1',
    user_id: 'u1',
    card_id: 'c1',
    note_id: 'n1',
    note_title: '笔记',
    question_type: 'choice',
    difficulty: 'medium',
    question: '浮充是什么？',
    answer: '长期恒压补偿自放电',
    options: JSON.stringify(['A.甲', 'B.乙']),
    explanation: '原解析',
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-02T00:00:00Z',
    ...over,
  } as unknown as QuizItem;
}

function renderEditor(q = makeQuestion(), onSaved = vi.fn()) {
  render(<QuestionEditor question={q} onSaved={onSaved} onCancel={vi.fn()} />);
  return { onSaved };
}

beforeEach(() => {
  vi.clearAllMocks();
  confirmMock.mockResolvedValue(true);
});

describe('options 的解析与回填', () => {
  it('JSON 数组 → 一行一个', () => {
    expect(optionsToText(JSON.stringify(['A.甲', 'B.乙']))).toBe('A.甲\nB.乙');
  });

  it('null / undefined / 空串 → 空文本', () => {
    expect(optionsToText(null)).toBe('');
    expect(optionsToText(undefined)).toBe('');
    expect(optionsToText('')).toBe('');
  });

  it('★ 脏数据（不是 JSON）原样保留，不吞掉', () => {
    // 库里可能存着历史脏数据。若解析失败就返回空串，用户一保存
    // 就把那部分**他看不到的内容**删了 —— 而界面上不会报任何错。
    expect(optionsToText('A.甲,B.乙')).toBe('A.甲,B.乙');
  });

  it('文本 → 数组时丢掉空行', () => {
    expect(textToOptions('A.甲\n\n  \nB.乙\n')).toEqual(['A.甲', 'B.乙']);
  });

  it('draftFromQuestion 把四列都填上', () => {
    const draft = draftFromQuestion(makeQuestion());
    expect(draft.question).toBe('浮充是什么？');
    expect(draft.answer).toBe('长期恒压补偿自放电');
    expect(draft.optionsText).toBe('A.甲\nB.乙');
    expect(draft.explanation).toBe('原解析');
  });
});

describe('保存', () => {
  it('★ 提交四个字段（PUT 全量替换）', async () => {
    mockedUpdate.mockResolvedValue(makeQuestion({ question: '改过' }) as never);
    const { onSaved } = renderEditor();

    const textareas = screen.getAllByRole('textbox');
    await userEvent.clear(textareas[0]);
    await userEvent.type(textareas[0], '改过的题干');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(mockedUpdate).toHaveBeenCalled());
    const [id, body] = mockedUpdate.mock.calls[0];
    expect(id).toBe('q1');
    expect(body).toMatchObject({
      question: '改过的题干',
      answer: '长期恒压补偿自放电',
      options: ['A.甲', 'B.乙'],
    });
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it('★ 清空解析 ⇒ 发 null（PUT 语义下"清空"是明确动作）', async () => {
    mockedUpdate.mockResolvedValue(makeQuestion() as never);
    renderEditor();

    // 解析是最后一个 textarea
    const textareas = screen.getAllByRole('textbox');
    await userEvent.clear(textareas[textareas.length - 1]);
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(mockedUpdate).toHaveBeenCalled());
    expect(mockedUpdate.mock.calls[0][1].explanation).toBeNull();
  });

  it('非选择题不传选项（后端存 null，存了也永远不读）', async () => {
    mockedUpdate.mockResolvedValue(makeQuestion() as never);
    renderEditor(makeQuestion({ question_type: 'fill_blank', options: null }));

    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(mockedUpdate).toHaveBeenCalled());
    expect(mockedUpdate.mock.calls[0][1].options).toBeNull();
  });

  it('题干为空时不发请求，并提示', async () => {
    renderEditor();

    const textareas = screen.getAllByRole('textbox');
    await userEvent.clear(textareas[0]);
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    expect(mockedUpdate).not.toHaveBeenCalled();
    expect(await screen.findByRole('alert')).toHaveTextContent('题干与答案都不能为空');
  });

  it('★ 选择题选项少于 2 个时不发请求，并提示', async () => {
    renderEditor();

    const textareas = screen.getAllByRole('textbox');
    // 第 2 个是选项
    await userEvent.clear(textareas[1]);
    await userEvent.type(textareas[1], 'A.只剩一个');
    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    expect(mockedUpdate).not.toHaveBeenCalled();
    expect(await screen.findByRole('alert')).toHaveTextContent('至少需要 2 个选项');
  });

  it('保存失败时把原因显示出来（而不是静默什么都不发生）', async () => {
    mockedUpdate.mockRejectedValue(new Error('服务器开小差了'));
    renderEditor();

    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('服务器开小差了');
  });

  it('保存成功时弹一条成功提示', async () => {
    mockedUpdate.mockResolvedValue(makeQuestion() as never);
    renderEditor();

    await userEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('已保存'));
  });
});

describe('删除', () => {
  it('★ 先确认，且确认框说明"复习进度也会清掉"', async () => {
    mockedDelete.mockResolvedValue(undefined as never);
    renderEditor();

    await userEvent.click(screen.getByRole('button', { name: '删除这道题' }));

    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    const opts = confirmMock.mock.calls[0][0];
    expect(opts.danger).toBe(true);
    // 删除是**不可撤销**且会连带清进度 —— 这句话不能省，否则用户以为只是藏起来
    expect(opts.message).toContain('复习');
  });

  it('★ 确认框点"取消" ⇒ 不发删除请求', async () => {
    confirmMock.mockResolvedValue(false);
    renderEditor();

    await userEvent.click(screen.getByRole('button', { name: '删除这道题' }));

    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    expect(mockedDelete).not.toHaveBeenCalled();
  });

  it('确认后真的删（onSaved 收到"没有更新后的行"的信号）', async () => {
    mockedDelete.mockResolvedValue(undefined as never);
    const onSaved = vi.fn();
    render(<QuestionEditor question={makeQuestion()} onSaved={onSaved} onCancel={vi.fn()} />);

    await userEvent.click(screen.getByRole('button', { name: '删除这道题' }));

    await waitFor(() => expect(mockedDelete).toHaveBeenCalledWith('q1'));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(null));
  });

  it('删除失败时把原因显示出来', async () => {
    mockedDelete.mockRejectedValue(new Error('删不掉'));
    renderEditor();

    await userEvent.click(screen.getByRole('button', { name: '删除这道题' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('删不掉');
  });
});
