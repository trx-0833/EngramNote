/**
 * @file 答题卡片的交互测试
 *
 * 覆盖的是附录 Z 那轮**真实踩到**的两个坑，以及 3.5 判分明细的展示契约：
 *
 * - 语义判分开关曾被放进组件内部，导致"勾了框再按回车"静默不生效
 *   → 所以这里断言的是"回调收到了正确的值"，而不是"复选框变了"
 * - `grading_detail` 为 null 时必须**什么都不显示**，不能渲染成
 *   "没有发现遗漏" —— 那是"判分过且无问题"，与"根本没判分"是两回事
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { SubmitAnswerResponse } from '../../api/client';
import QuizAnswerCard, { type QuizCardQuestion } from './QuizAnswerCard';
// 印章是 `aria-hidden` 的装饰，语义查询按定义取不到它 —— 按模块类名查
// （css-convention §6 允许的少数例外，理由写在用它的那条用例里）
import styles from './QuizAnswerCard.module.css';

// 卡片内会渲染 SourceContext（它自己按需拉卡片详情）；这里不展开，
// mock 掉是为了保证"绝不发生真实请求"，而不是为了改行为
vi.mock('../../api/qa', () => ({
  getKnowledgeCard: vi.fn(),
}));

function makeResult(over: Partial<SubmitAnswerResponse> = {}): SubmitAnswerResponse {
  return {
    quiz_id: 'q1',
    is_correct: true,
    quality: 4,
    correct_answer: '标准答案',
    explanation: null,
    options: null,
    question_type: 'short_answer',
    sm2: {
      interval: 6,
      repetition: 2,
      easiness_factor: 2.5,
      next_review_at: '2026-09-17T04:00:00+00:00',
      rating: 3,
      predicted_retention: 0.62,
    },
    self_rating: 4,
    grading_method: 'self_rating',
    needs_self_assessment: false,
    completing_placeholder: false,
    grading_reason: null,
    grading_detail: null,
    ...over,
  };
}

const BASE_QUIZ: QuizCardQuestion = {
  question_type: 'short_answer',
  question: '什么是浮充？',
  card_id: 'c1',
  note_id: 'n1',
};

function renderCard(props: Partial<Parameters<typeof QuizAnswerCard>[0]> = {}) {
  const handlers = {
    onSelectAnswer: vi.fn(),
    onSubmit: vi.fn(),
    onNext: vi.fn(),
  };
  render(
    <QuizAnswerCard
      quiz={BASE_QUIZ}
      userAnswer="浮充是一种运行方式"
      submitted={false}
      result={null}
      isLast={false}
      {...handlers}
      {...props}
    />,
  );
  return handlers;
}

describe('QuizAnswerCard 的作答区', () => {
  it('答案为空时提交按钮禁用（不允许提交空答案）', () => {
    renderCard({ userAnswer: '' });
    expect(screen.getByRole('button', { name: '提交答案' })).toBeDisabled();
  });

  it('提交中禁用按钮（防连点重复提交）', () => {
    renderCard({ submitting: true });
    expect(screen.getByRole('button', { name: '提交答案' })).toBeDisabled();
  });

  it('点提交只调用 onSubmit 一次', async () => {
    const { onSubmit } = renderCard();
    await userEvent.click(screen.getByRole('button', { name: '提交答案' }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });
});

describe('QuizAnswerCard 的语义判分开关（阶段 3.5）', () => {
  it('简答题且传入回调时显示开关', () => {
    renderCard({ onToggleSemanticGrading: vi.fn() });
    expect(screen.getByRole('checkbox')).toBeInTheDocument();
  });

  it('★ 状态由外部持有：勾选要把 true 报给页面（回车提交才不会漏掉它）', async () => {
    const onToggle = vi.fn();
    renderCard({ onToggleSemanticGrading: onToggle });
    await userEvent.click(screen.getByRole('checkbox'));
    expect(onToggle).toHaveBeenCalledWith(true);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it('选择题不显示开关（语义判分只对简答题有意义）', () => {
    renderCard({
      quiz: { ...BASE_QUIZ, question_type: 'choice' },
      onToggleSemanticGrading: vi.fn(),
    });
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('没传回调时不显示开关（调用方没接线的功能不该出现）', () => {
    renderCard();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });
});

describe('QuizAnswerCard 的判分明细（阶段 3.5 的落地处）', () => {
  const submitted = { submitted: true, userAnswer: '断路器' };

  it('★ 未判分（null）时整块不渲染，更不能说"没有发现问题"', () => {
    renderCard({ ...submitted, result: makeResult({ grading_detail: null }) });
    expect(screen.queryByText(/AI 判分/)).not.toBeInTheDocument();
    expect(screen.queryByText(/没有发现遗漏或误解/)).not.toBeInTheDocument();
  });

  it('部分正确时列出遗漏点', () => {
    renderCard({
      ...submitted,
      result: makeResult({
        is_correct: false,
        grading_detail: {
          verdict: 'partial',
          missing_points: ['接地刀闸', '隔离刀闸'],
          misconceptions: [],
          confidence: 0.9,
          reason: '漏了两种刀闸',
        },
      }),
    });
    expect(screen.getByText(/答对了部分/)).toBeInTheDocument();
    expect(screen.getByText('接地刀闸')).toBeInTheDocument();
    expect(screen.getByText('隔离刀闸')).toBeInTheDocument();
    expect(screen.getByText('漏了两种刀闸')).toBeInTheDocument();
  });

  it('判错时列出误解点', () => {
    renderCard({
      ...submitted,
      result: makeResult({
        is_correct: false,
        grading_detail: {
          verdict: 'incorrect',
          missing_points: [],
          misconceptions: ['把浮充说成了均充'],
          confidence: 0.95,
          reason: '概念混淆',
        },
      }),
    });
    expect(screen.getByText(/误解：/)).toBeInTheDocument();
    expect(screen.getByText('把浮充说成了均充')).toBeInTheDocument();
  });

  it('判对且无遗漏时才说"没有发现遗漏或误解"', () => {
    renderCard({
      ...submitted,
      result: makeResult({
        grading_detail: {
          verdict: 'correct',
          missing_points: [],
          misconceptions: [],
          confidence: 0.9,
          reason: '一致',
        },
      }),
    });
    expect(screen.getByText(/没有发现遗漏或误解/)).toBeInTheDocument();
  });
});

describe('QuizAnswerCard 的调度依据（阶段 3.6）', () => {
  const submitted = { submitted: true, userAnswer: 'x' };

  it('展示间隔、档位与复习前预测保持率', () => {
    renderCard({ ...submitted, result: makeResult(), showSm2Info: true });
    expect(screen.getByText(/下次复习: 6 天后/)).toBeInTheDocument();
    expect(screen.getByText(/档位: 想起来了/)).toBeInTheDocument();
    expect(screen.getByText(/复习前预测还能想起: 62%/)).toBeInTheDocument();
  });

  it('★ 预测保持率 = 1（首次复习）时不显示 —— "预测还能想起 100%" 没有信息量', () => {
    const result = makeResult();
    result.sm2.predicted_retention = 1;
    renderCard({ ...submitted, result, showSm2Info: true });
    expect(screen.queryByText(/复习前预测还能想起/)).not.toBeInTheDocument();
  });

  it('SM-2 回退路径（无 rating / 无预测）不显示这两项，也不报错', () => {
    const result = makeResult();
    result.sm2.rating = null;
    result.sm2.predicted_retention = null;
    renderCard({ ...submitted, result, showSm2Info: true });
    expect(screen.getByText(/下次复习: 6 天后/)).toBeInTheDocument();
    expect(screen.queryByText(/档位:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/复习前预测还能想起/)).not.toBeInTheDocument();
  });

  it('等待自评时不显示调度信息（此刻调度尚未推进，显示"下次复习"是假信息）', () => {
    const result = makeResult({ needs_self_assessment: true, self_rating: null });
    result.sm2.next_review_at = null;
    renderCard({ ...submitted, result, showSm2Info: true });
    expect(screen.queryByText(/下次复习:/)).not.toBeInTheDocument();
    expect(screen.getByText(/请对照答案/)).toBeInTheDocument();
  });
});

describe('QuizAnswerCard 自评阶段的行为', () => {
  const pending = {
    submitted: true,
    userAnswer: 'x',
    result: makeResult({
      needs_self_assessment: true,
      self_rating: null,
      is_correct: false,
    }),
  };

  it('★ 等待自评时不显示"回答错误"（占位判分恒为错误，照常渲染就是误导）', () => {
    renderCard(pending);
    expect(screen.queryByText('回答错误')).not.toBeInTheDocument();
    expect(screen.getByText('请对照答案，给自己的回忆程度打分')).toBeInTheDocument();
  });

  it('★ 等待自评时"下一题"禁用（此刻调度尚未推进，放行会让题永远结不了账）', () => {
    renderCard(pending);
    expect(screen.getByRole('button', { name: '下一题' })).toBeDisabled();
  });

  it('四档自评按 quality 回调', async () => {
    const onSelfRate = vi.fn();
    renderCard({ ...pending, onSelfRate });
    await userEvent.click(screen.getByText('想起来了'));
    expect(onSelfRate).toHaveBeenCalledWith(4);
  });

  it('完成自评后允许进入下一题', () => {
    renderCard({ ...pending, selfRated: true, result: makeResult({ self_rating: 4 }) });
    const next = screen.getByRole('button', { name: /下一题|完成复习/ });
    expect(next).not.toBeDisabled();
  });

  it('★ 自评生效后盖一枚印章，且旁边那句说明同时在场（批次 E6）', () => {
    renderCard({ ...pending, selfRated: true, result: makeResult({ self_rating: 4 }) });

    // 说明文字走语义查询
    expect(screen.getByText(/已按自评「想起来了」记录/)).toBeInTheDocument();

    // 印章本身是 `aria-hidden` 的装饰，语义查询看不见它 —— 只能按模块类名取。
    // 这是 css-convention §6 说的"少数必须按类名查"的地方之一：这里要验的正是
    // "那枚纯装饰的图形在不在"，而按定义它就没有任何可访问名。
    const seal = document.querySelector(`.${styles.selfRatingSeal}`);
    expect(seal).not.toBeNull();
    // 印章里装的确实是那枚自绘 seal 图标，不是个空 span
    expect(seal?.querySelector('svg')).not.toBeNull();
  });

  it('自评还没生效时不盖印章（印章是"完成"的记号，不能提前出现）', () => {
    renderCard({ ...pending });
    expect(document.querySelector(`.${styles.selfRatingSeal}`)).toBeNull();
  });
});
