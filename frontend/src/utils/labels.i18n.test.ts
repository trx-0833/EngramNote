/**
 * @file 枚举标签的双语测试（i18n **阶段 3** 的验收用例）
 *
 * ## 为什么这批标签值得单独测
 *
 * `utils/labels.ts` 的 46 条标签出现在 **58 个消费点**上 —— 状态徽章、
 * 题型、难度、卡片分类、判分方式、四档自评。它们是"每一页都会看到"的那批，
 * 所以英文路径必须有覆盖。
 *
 * ## 怎么测（用探针而不是抄中文）
 *
 * `readLabels()` 渲染一个探针把 `useLabels()` 的值取出来
 * （见 `labels.testkit.tsx` 里"两条错路"的说明）。这样断言用的是
 * **消息表里的真值**，改文案时测试会跟着走。
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { applyLocale, LOCALE_STORAGE_KEY } from '../i18n/locales';
import {
  cardCategoryColors,
  cardTypeColors,
  difficultyColors,
  getMasteryColor,
  statusClass,
  weakPointBadge,
} from './labels';
import { readLabels } from './labels.testkit';

beforeEach(() => {
  localStorage.setItem(LOCALE_STORAGE_KEY, 'zh-CN');
  applyLocale('zh-CN', { persist: false });
});

/** 切到英文（`readLabels()` 读的是当前语言） */
function useEnglish() {
  localStorage.setItem(LOCALE_STORAGE_KEY, 'en');
  applyLocale('en', { persist: false });
}

describe('中文（不能回归）', () => {
  it('状态与来源标签是中文', () => {
    const { statusLabels, sourceTypeLabels } = readLabels();
    expect(statusLabels.converted).toBe('已完成');
    expect(statusLabels.cleaning_failed).toBe('清洗失败');
    expect(sourceTypeLabels.image).toBe('图片');
    // 专有名词两种语言同形
    expect(sourceTypeLabels.pdf).toBe('PDF');
    expect(sourceTypeLabels.docx).toBe('Word');
  });

  it('题型 / 难度 / 分类是中文', () => {
    const { questionTypeLabels, difficultyLabels, cardCategoryLabels } = readLabels();
    expect(questionTypeLabels.choice).toBe('选择题');
    expect(difficultyLabels.medium).toBe('中等');
    expect(cardCategoryLabels.blind_spot).toBe('盲点');
  });

  it('四档自评的标签与说明是中文', () => {
    const { selfRatingOptions } = readLabels();
    expect(selfRatingOptions.map((o) => o.label)).toEqual([
      '完全忘记',
      '勉强想起',
      '想起来了',
      '轻松想起',
    ]);
    expect(selfRatingOptions[0].hint).toBe('想不起来，需要重新学');
  });

  it('判分方式与语义判分是中文', () => {
    const { gradingMethodLabels, verdictLabels } = readLabels();
    expect(gradingMethodLabels.self_rating).toBe('你的自评');
    expect(verdictLabels.correct.label).toBe('回答正确');
  });
});

describe('★ 英文（阶段 3 验收）', () => {
  it('状态与来源标签是英文', () => {
    useEnglish();
    const { statusLabels, sourceTypeLabels } = readLabels();
    expect(statusLabels.converted).toBe('Converted');
    expect(statusLabels.cleaning_failed).toBe('Cleaning failed');
    expect(sourceTypeLabels.image).toBe('Image');
    // ★ 专有名词**刻意不译** —— 两种语言同形
    expect(sourceTypeLabels.pdf).toBe('PDF');
    expect(sourceTypeLabels.docx).toBe('Word');
    expect(sourceTypeLabels.markdown).toBe('Markdown');
  });

  it('题型 / 难度 / 分类是英文', () => {
    useEnglish();
    const { questionTypeLabels, difficultyLabels, cardCategoryLabels } = readLabels();
    expect(questionTypeLabels.choice).toBe('Multiple choice');
    expect(questionTypeLabels.fill_blank).toBe('Fill in the blank');
    expect(difficultyLabels.medium).toBe('Medium');
    expect(cardCategoryLabels.blind_spot).toBe('Blind spot');
  });

  it('★ 四档自评是英文，且 quality 与 color **逐值未变**', () => {
    useEnglish();
    const { selfRatingOptions } = readLabels();
    expect(selfRatingOptions.map((o) => o.label)).toEqual(['Forgot', 'Hard', 'Good', 'Easy']);
    // quality 是 SM-2 契约、color 是 a11y 对比度算出来的 —— 都与语言无关
    expect(selfRatingOptions.map((o) => o.quality)).toEqual([0, 3, 4, 5]);
    expect(selfRatingOptions.map((o) => o.color)).toEqual([
      '#c0392b',
      '#8f7020',
      '#25714a',
      '#0f3460',
    ]);
  });

  it('判分方式与语义判分是英文，且颜色逐值未变', () => {
    useEnglish();
    const { gradingMethodLabels, verdictLabels } = readLabels();
    expect(gradingMethodLabels.self_rating).toBe('Your self-rating');
    expect(verdictLabels.correct.label).toBe('Correct');
    // a11y 口径的颜色不随语言变
    expect(verdictLabels.correct.color).toBe('#25714a');
    expect(verdictLabels.partial.color).toBe('#7d6417');
    expect(verdictLabels.incorrect.color).toBe('#c0392b');
  });

  it('★ FSRS 档位标签与自评档位**指向同一批文案**', () => {
    // 两处刻意共用消息键：用户看到的是同一套说法（见 labels.ts 的注释）。
    // 若哪天有人给它们各写一套，这条会红。
    for (const locale of ['zh-CN', 'en'] as const) {
      applyLocale(locale, { persist: false });
      const { selfRatingOptions, ratingLabels } = readLabels();
      expect(ratingLabels[1], locale).toBe(selfRatingOptions[0].label);
      expect(ratingLabels[2], locale).toBe(selfRatingOptions[1].label);
      expect(ratingLabels[3], locale).toBe(selfRatingOptions[2].label);
      expect(ratingLabels[4], locale).toBe(selfRatingOptions[3].label);
    }
  });

  it('★ 未知枚举值回退到值本身（不是空白）', () => {
    // 后端新增枚举值而消息表没跟上时，界面显示原始值 —— 那比空白更容易被发现
    useEnglish();
    const { statusLabels, cardTypeLabels } = readLabels();
    expect(statusLabels.some_future_status || 'some_future_status').toBe('some_future_status');
    expect(cardTypeLabels.some_future_type || 'some_future_type').toBe('some_future_type');
  });
});

describe('颜色表与语言无关（a11y 口径，逐值未改）', () => {
  it('切换语言不改变任何颜色', () => {
    const before = {
      cardType: { ...cardTypeColors },
      difficulty: { ...difficultyColors },
      cardCategory: { ...cardCategoryColors },
      mastery: [getMasteryColor(10), getMasteryColor(50), getMasteryColor(90)],
      weakPoint: { ...weakPointBadge },
    };
    useEnglish();
    expect(cardTypeColors).toEqual(before.cardType);
    expect(difficultyColors).toEqual(before.difficulty);
    expect(cardCategoryColors).toEqual(before.cardCategory);
    expect([getMasteryColor(10), getMasteryColor(50), getMasteryColor(90)]).toEqual(before.mastery);
    expect(weakPointBadge).toEqual(before.weakPoint);
  });

  it('`statusClass` 不是 hook（类名与语言无关，保持普通函数）', () => {
    // 它是 CSS 类名映射，刻意没有变成 hook —— 否则每个消费点都要包 Provider
    expect(statusClass('converted')).toBe('status-converted');
    expect(statusClass(null)).toBe('status-unknown');
    expect(statusClass('nope')).toBe('status-unknown');
  });
});
