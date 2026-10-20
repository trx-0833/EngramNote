/**
 * @file 刷题题号导航条（想法 8）
 *
 * ## 它解决什么
 *
 * 三个刷题页（答题复习 / 快速复习 / 今日学习）此前**只能往后翻**：
 * `QuizAnswerCard` 只渲染一个「下一题」，没有题号、没有上一题。
 * 用户想回看刚做过的题只能靠记忆，实际上做不到。
 *
 * ## 为什么是"题号条"而不是只有"上一题"
 *
 * 只有上一题时，想回到第 3 题得连点十几次 —— 而"看刚才那几道错题"是
 * 复习里最常见的动作，代价不该是十几次点击。题号条把这件事变成一次点击，
 * 顺带把"哪几题做了、哪几题错了"变成一眼可见。
 *
 * ## 四态用「颜色 + 符号」双编码
 *
 * 只靠颜色区分状态对色盲用户等于没有区分（WCAG 1.4.1）。
 * 所以每一态都同时给：颜色、符号（`✓` / `✗` / `●`）、以及可访问名里的文字。
 * 三个通道任意一个可用就能读懂。
 *
 * ## 无障碍
 *
 * - 外层 `role="group"` + `aria-label`：读屏会先报"题目导航"再进组；
 * - 每个按钮的 `aria-label` 是完整的「跳到第 N 题，已答对」——
 *   而不是只读一个数字（读屏读"7"毫无信息量）；
 * - 当前题用 `aria-current="true"`（这是它的标准语义，不是自造属性）。
 */
import { useEffect, useRef } from 'react';

import { useTranslation } from '../../i18n';

/** 单题在导航条里要呈现的状态 */
export interface QuizNavItem {
  /** 题目在会话里的序号（0 基，用于回调） */
  index: number;
  /** 是否已提交 */
  submitted: boolean;
  /** 已提交且答对；未提交时为 undefined */
  isCorrect?: boolean;
}

interface QuizNavigatorProps {
  items: QuizNavItem[];
  /** 当前题的下标 */
  currentIndex: number;
  /** 跳到某一题 */
  onJump: (index: number) => void;
}

export default function QuizNavigator({ items, currentIndex, onJump }: QuizNavigatorProps) {
  const { t } = useTranslation();
  const currentRef = useRef<HTMLButtonElement | null>(null);

  /**
   * 当前题滚进视野
   *
   * ## 为什么必须有
   *
   * 一次会话可能装 50 道题，而导航条是**横向滚动**的：走到第 20 题时，
   * 题号条的可视区还停在 1~15，当前题在视野外 —— 于是"我在第几题"
   * 这个信息又丢了，导航条就白做了。
   *
   * `block: 'nearest'` 是关键：默认的 `'start'` 会连带**纵向**滚动，
   * 把整页往上拽（因为导航条在文档流里），用户会觉得页面自己乱跳。
   * `'nearest'` 只在需要时做最小滚动。
   *
   * `inline: 'center'` 让当前题居中，能看到前后各若干题 —— 比贴边更好用。
   */
  useEffect(() => {
    currentRef.current?.scrollIntoView({ block: 'nearest', inline: 'center' });
  }, [currentIndex]);

  /**
   * 单题的状态 → （CSS 修饰类、符号、可访问名里的文字）
   *
   * 用显式查表而不是拼字符串类名：与 `PageHeader` 的 `SPACING_CLASS` 同一理由 ——
   * 测试环境里 `*.module.css` 的默认导出是 Proxy，动态取值拿不到东西；
   * 而且写错一个档位 `tsc` 会当场报错。
   */
  function stateOf(item: QuizNavItem, isCurrent: boolean) {
    if (item.submitted) {
      return item.isCorrect
        ? { cls: 'quiz-nav-correct', mark: '✓', label: t('quizNav.stateCorrect') }
        : { cls: 'quiz-nav-incorrect', mark: '✗', label: t('quizNav.stateIncorrect') };
    }
    // 未作答：当前题也标成"当前"，但符号仍是占位点 —— 不假装它已作答
    return {
      cls: isCurrent ? 'quiz-nav-current' : '',
      mark: isCurrent ? '●' : '',
      label: t('quizNav.stateUnanswered'),
    };
  }

  return (
    <div
      className="quiz-nav"
      role="group"
      aria-label={t('quizNav.navigatorLabel')}
      data-testid="quiz-navigator"
    >
      {items.map((item) => {
        const isCurrent = item.index === currentIndex;
        const state = stateOf(item, isCurrent);
        return (
          <button
            key={item.index}
            ref={isCurrent ? currentRef : undefined}
            type="button"
            className={`quiz-nav-item ${state.cls}`.trim()}
            onClick={() => onJump(item.index)}
            // 当前题额外加 aria-current：这是"当前位置"的标准语义
            aria-current={isCurrent ? 'true' : undefined}
            // 完整句子而不是光一个数字：读屏读"7"没有信息量
            aria-label={`${t('quizNav.jumpTo', { number: item.index + 1 })}，${state.label}`}
          >
            <span className="quiz-nav-number">{item.index + 1}</span>
            {state.mark && (
              // aria-hidden：符号是给眼睛看的，语义已经在上面的 aria-label 里了，
              // 不隐藏会被读屏念成"对勾"这种无意义内容
              <span className="quiz-nav-mark" aria-hidden="true">
                {state.mark}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
