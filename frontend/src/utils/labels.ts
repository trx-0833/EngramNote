/**
 * @file 统一标签映射
 * @description 集中管理各页面共用的标签映射，颜色更和谐
 *
 * ## i18n（阶段 3）：标签文案搬进了消息表，本文件只留**结构**与**颜色**
 *
 * 改之前，这个文件里有 46 条中文字面量（状态、题型、难度、分类…），
 * 出现在 58 个消费点上。它们搬进了 `i18n/messages.*.ts` 的 `labels` 命名空间。
 *
 * ### 消费点怎么改（只有一种改法）
 *
 * ```diff
 * - import { statusLabels } from '../utils/labels';
 * + import { useLabels } from '../utils/labels';
 *
 *   function NoteRow({ note }) {
 * +   const { statusLabels } = useLabels();
 *     return <span>{statusLabels[note.status] || note.status}</span>;
 *   }
 * ```
 *
 * 即：**数据源从模块级常量换成 hook 的返回值**，取值表达式一个字不变。
 *
 * ### 为什么用 hook 返回整表，而不是给每个属性套 `t()`
 *
 * 这些标签几乎都是**动态取值**（`statusLabels[note.status]`、
 * `cardTypeLabels[card.type] || card.type`）。给每处套 `t()` 要先把
 * "值 → 键"的映射写在组件里 —— 那正是本文件要收敛掉的东西
 * （`statusClass` 的注释记着同一类教训：两套并行机制必然漂移）。
 * 让 hook 返回已翻译的整表，动态取值就能原样保留。
 *
 * ### 颜色表**没有**动
 *
 * `cardTypeColors` / `difficultyColors` / `verdictLabels[].color` 等是
 * **a11y 对比度**算出来的值（口径见下方长注释），与语言无关，逐字未改。
 */

import { useMemo } from 'react';

import { useTranslation, type MessageKey } from '../i18n';

/**
 * 把消息表里的一层子树取成 `Record<值, 已翻译标签>`
 *
 * ## 为什么返回类型是 `Record<string, string>` 而不是 `Record<K, string>`
 *
 * 第一版写成 `Record<K, string>`（K 是取值联合），`tsc` 立刻在消费点报错：
 * `statusLabels[note.status]` 里 `note.status` 的类型是 `string`
 * （契约里就是 `string`，不是联合），索引进联合键会报 TS7053。
 *
 * 收紧成联合需要**先**给契约加上枚举联合 —— 那是契约改动，不是本轮的事。
 * 所以这里放宽成 `Record<string, string>`，与改之前的模块级常量**类型完全一致**
 * （那本来就是 `Record<string, string>`）。
 *
 * 代价是"取值拼错"不会在编译期被发现；运行时靠消费点的
 * `表[值] || 值` 回退兜住（显示 `blind_spot` 好过显示空白）。
 * 键本身的拼写仍由 `t()` 的 `MessageKey` 联合守着 —— 那才是最容易错的地方。
 */
function useLabelTable<K extends string>(
  namespace: string,
  keys: readonly K[],
): Record<string, string> {
  const { t } = useTranslation();
  return useMemo(() => {
    const out: Record<string, string> = {};
    for (const key of keys) {
      out[key] = t(`${namespace}.${key}` as MessageKey);
    }
    return out;
    // `t` 会随语言变化（`useTranslation` 内部 useCallback 依赖 locale），
    // 所以语言一切换这里就重算 —— 这正是要的行为。
  }, [t, namespace, keys]);
}

/** 各枚举的取值清单（与后端 Enum 逐一对应，见 `messages.zh-CN.ts` 的 `labels` 注释） */
const SOURCE_TYPES = [
  'pdf',
  'image',
  'docx',
  'pptx',
  'xlsx',
  'audio',
  'video',
  'markdown',
] as const;
const NOTE_STATUSES = [
  'uploading',
  'converting',
  'converted',
  'cleaning',
  'cleaned',
  'cleaning_failed',
  'learning',
  'learning_failed',
  'archived',
  'failed',
] as const;
const CARD_TYPES = ['concept', 'formula', 'qa', 'definition'] as const;
const QUESTION_TYPES = ['choice', 'fill_blank', 'short_answer'] as const;
const DIFFICULTIES = ['easy', 'medium', 'hard'] as const;
const CARD_CATEGORIES = ['regular', 'blind_spot', 'extension'] as const;
const NOTE_ROLES = ['material', 'personal_note'] as const;
const VERSION_SOURCES = ['user_edit', 'auto_clean', 'system'] as const;
const RELATION_STATUSES = ['suggested', 'confirmed', 'rejected'] as const;
const GRADING_METHODS = ['choice', 'fill_blank', 'self_rating', 'ungraded', 'legacy'] as const;
/**
 * 四档自评的**语义名**（SM-2 的 Again/Hard/Good/Easy），不是 quality 数字
 *
 * ⚠️ 它是**类型来源**，不是运行期清单 —— 四档的实际取值写死在
 * `useLabels()` 的 `selfRatingOptions` 里（因为每档还要带 `quality` 与 `color`，
 * 光有语义名拼不出那一档）。所以这里没有对应的 `*_TIERS` 运行期数组：
 * 加一个 `as const` 数组只为了取类型，会被 eslint 判成"只用作类型"而未使用。
 */
export type SelfRatingTier = 'again' | 'hard' | 'good' | 'easy';

/**
 * 已按当前语言翻译好的标签表集合
 *
 * 用法：`const { statusLabels, cardTypeLabels } = useLabels();`
 * 只取用到的那几张 —— hook 内部按表缓存，多取几张没有额外代价，
 * 但**写清楚用了什么**对读代码的人有用。
 */
export function useLabels() {
  const { t } = useTranslation();
  const sourceTypeLabels = useLabelTable('labels.source', SOURCE_TYPES);
  const statusLabels = useLabelTable('labels.status', NOTE_STATUSES);
  const cardTypeLabels = useLabelTable('labels.cardType', CARD_TYPES);
  const questionTypeLabels = useLabelTable('labels.questionType', QUESTION_TYPES);
  const difficultyLabels = useLabelTable('labels.difficulty', DIFFICULTIES);
  const cardCategoryLabels = useLabelTable('labels.cardCategory', CARD_CATEGORIES);
  const noteRoleLabels = useLabelTable('labels.noteRole', NOTE_ROLES);
  const versionSourceLabels = useLabelTable('labels.versionSource', VERSION_SOURCES);
  const relationStatusLabels = useLabelTable('labels.relationStatus', RELATION_STATUSES);
  const gradingMethodLabels = useLabelTable('labels.gradingMethod', GRADING_METHODS);

  /**
   * 语义判分三档 —— 保留原来的 `{label, color}` 形状
   *
   * `color` 来自 a11y 口径（色字压浅底 ≥4.5:1，见下方长注释），
   * 所以这里只把 `label` 换成译文、颜色逐字保留。
   *
   * ⚠️ 返回类型带 `Record<string, …>` 索引签名：消费点是
   * `verdictLabels[detail.verdict]`，而 `verdict` 在契约里是 `string`。
   * 不加索引签名会报 TS7053（与 `useLabelTable` 同一个理由，见那里的说明）。
   */
  const verdictLabels = useMemo<Record<string, { label: string; color: string }>>(
    () => ({
      correct: { label: t('labels.verdict.correct'), color: '#25714a' },
      partial: { label: t('labels.verdict.partial'), color: '#7d6417' },
      incorrect: { label: t('labels.verdict.incorrect'), color: '#c0392b' },
    }),
    [t],
  );

  /**
   * SM-2 四档自评量表（单一数据源）
   *
   * `quality` 与 `color` 都与语言无关，只有 `label` / `hint` 走 `t()`。
   * `quality` 必须与后端 `sm2_service` 的语义一致：0=失败、3=勉强通过、
   * 4=通过、5=轻松，后端据此计算 EF 与下次间隔。
   */
  const selfRatingOptions = useMemo<SelfRatingOption[]>(
    () => [
      {
        quality: 0,
        tier: 'again',
        label: t('labels.selfRating.again.label'),
        hint: t('labels.selfRating.again.hint'),
        color: '#c0392b',
      },
      {
        quality: 3,
        tier: 'hard',
        label: t('labels.selfRating.hard.label'),
        hint: t('labels.selfRating.hard.hint'),
        color: '#8f7020',
      },
      {
        quality: 4,
        tier: 'good',
        label: t('labels.selfRating.good.label'),
        hint: t('labels.selfRating.good.hint'),
        color: '#25714a',
      },
      {
        quality: 5,
        tier: 'easy',
        label: t('labels.selfRating.easy.label'),
        hint: t('labels.selfRating.easy.hint'),
        color: '#0f3460',
      },
    ],
    [t],
  );

  /**
   * FSRS 评分档位（1-4）→ 标签
   *
   * 这四档**恰好**是自评四档按钮的取值（0/3/4/5 → Again/Hard/Good/Easy），
   * 所以文案与 `selfRatingOptions` 指向**同一批消息键** —— 用户看到的是
   * 同一套说法。机器判分不会有 Easy（"选对了"里没有"毫不费力"这层信息，
   * 见后端 `scheduler_service.rating_from_quality`）。
   */
  const ratingLabels = useMemo<Record<number, string>>(
    () => ({
      1: t('labels.selfRating.again.label'),
      2: t('labels.selfRating.hard.label'),
      3: t('labels.selfRating.good.label'),
      4: t('labels.selfRating.easy.label'),
    }),
    [t],
  );

  return {
    sourceTypeLabels,
    statusLabels,
    cardTypeLabels,
    questionTypeLabels,
    difficultyLabels,
    cardCategoryLabels,
    noteRoleLabels,
    versionSourceLabels,
    relationStatusLabels,
    gradingMethodLabels,
    verdictLabels,
    selfRatingOptions,
    ratingLabels,
  };
}

/**
 * ── 颜色表的取值口径（a11y 修复轮：见 docs/a11y-audit.md 的 F-33）──
 *
 * 下面几张表**全部是"白字压色块"**（`background: <表里的值>; color: #fff`），
 * 所以判据是**该色与白色的对比度 ≥ 4.5:1**（徽章文字 11.2~12.8px 常规字重，
 * 不吃"大文本 3:1"的豁免）。唯一的例外是 `verdictLabels`：它是**色字压浅底**
 * （`QuizAnswerCard` 的语义判分块，底色 `--color-bg` #faf9f7），
 * 判据是"该色与 #faf9f7 的对比度 ≥ 4.5:1"。
 *
 * ⚠️ **上一轮的教训**：F-19 只压深了 `selfRatingOptions` 里的金与绿，
 * "修法对、验证也真"，但只覆盖了**一张表**；其余 5 张表仍写着同一批旧值，
 * 于是同一类缺陷从答题复习页（`difficultyColors.medium` = `#c9a959` = 2.25:1）
 * 又冒了出来。**一处的局部修复会遮蔽一个系统性缺陷** —— 所以这一轮把
 * 每一张表、每一个值都过了一遍，并把判据写在上面的那句话里，
 * 下次新增颜色表时照同一句话挑值即可（新增值必须 ≥4.5:1）。
 *
 * | 表 | 用途 | 判据 |
 * |---|---|---|
 * | `cardTypeColors` | 卡片类型徽章（KnowledgeCards / RelatedCardsSection / 图谱图例） | 白色 ≥4.5:1 |
 * | `questionTypeColors` | 题型徽章（QuestionSets） | 白色 ≥4.5:1 |
 * | `difficultyColors` | 难度徽章（QuizAnswerCard / QuestionSets） | 白色 ≥4.5:1 |
 * | `cardCategoryColors` | 分类徽章（KnowledgeCards） | 白色 ≥4.5:1 |
 * | `verdictLabels` | 语义判分的结论文字与左侧竖条 | #faf9f7 ≥4.5:1 |
 *
 * 这一轮只压深**不达标的那些档**（红 #c0392b 5.44:1、墨 #0f3460 12.50:1、
 * 紫 #6d28d9 7.10:1、灰 #6b7280 4.83:1 本来就够，**未改**）——
 * 改它们只会白白挪动视觉基准。金色在金/绿两档里各有两个值：
 * **色块底**用 `#8f7020`（白字 4.66:1），**压在 #faf9f7 上的文字**用 `#896c1f`
 * （对 #faf9f7 是 4.72:1；`#8f7020` 在该底色上只有 4.43:1，不够）。
 */

/** 知识卡片类型到颜色的映射（白字压色块，见上面的取值口径） */
export const cardTypeColors: Record<string, string> = {
  concept: '#0f3460',
  formula: '#6d28d9',
  qa: '#25714a',
  definition: '#8f7020',
};

/** 题目类型到颜色的映射（白字压色块，见上面的取值口径） */
export const questionTypeColors: Record<string, string> = {
  choice: '#0f3460',
  fill_blank: '#6d28d9',
  short_answer: '#25714a',
};

/**
 * 难度到颜色的映射（白字压色块，见上面的取值口径）
 *
 * F-33 就是这张表：`medium` 的 `#c9a959` 白字只有 2.25:1，
 * 上一轮修 `selfRatingOptions` 时漏掉了它，直到答题复习页被扫才报出来。
 */
export const difficultyColors: Record<string, string> = {
  easy: '#25714a',
  medium: '#8f7020',
  hard: '#c0392b',
};

/**
 * 未知分类值时的兜底色（Tailwind gray-500；白底 4.83:1，达标）。
 *
 * A4 收敛：这个字面量此前在 **12 处**各写了一遍，形态全是 `表[值] || '#6b7280'` ——
 * `KnowledgeCards` ×2、`QuestionSets` ×2、`RelatedCardsSection`、`drawNode`、
 * `renderMinimap`、`GraphToolbar`、`NodeInspector` ×2、`GraphSidebar` ×2。
 * 值本身没错（它是达标色），错的是"同一个兜底色有十一份副本"：
 * 想调整它，得先把它们找全。
 */
export const FALLBACK_CATEGORY_COLOR = '#6b7280';

/** 知识卡片分类到颜色的映射（白字压色块，见上面的取值口径） */
export const cardCategoryColors: Record<string, string> = {
  regular: FALLBACK_CATEGORY_COLOR,
  blind_spot: '#c0392b',
  extension: '#25714a',
};

/**
 * 掌握度（0–100）→ 进度条颜色。
 *
 * **A4 收敛**：这个函数原先私有在 `pages/KnowledgeCards.tsx` 里，而且用的是
 * a11y 压深**之前**的旧值 —— `<40` 红、`<70` **`#c9a959`（白底只有 2.25:1，不达标）**、
 * 否则 `#2d8a56`（4.30:1）。而本文件的 `difficultyColors` 早就改成
 * `#c0392b / #8f7020 / #25714a` 了 —— "难度"与"掌握度"表达的是同一种
 * "程度"语义，色阶却各走各的，于是同一张卡片在卡片页与题目页显示两种颜色。
 *
 * 现在与 `difficultyColors` **逐值对齐**：它们本来就该是同一套。
 */
export function getMasteryColor(level: number): string {
  if (level < 40) return '#c0392b';
  if (level < 70) return '#8f7020';
  return '#25714a';
}

/**
 * 笔记状态到 CSS 类名的映射（单一数据源）
 *
 * 背景（见 docs/overhaul-plan.md §2.8 F-13）：
 * 四个页面此前各自用 `` `status-${note.status}` `` 拼接类名，而 CSS 只定义了
 * 7 个 `.status-*`；`learning` / `learning_failed` / `archived` **完全没有样式**，
 * 于是同一状态在「项目页」显示红色失败、在其余四个页面显示无样式裸文本。
 * `Projects.tsx` 当时是靠自己手写一张映射表绕过的 —— 两套并行机制必然漂移。
 *
 * 这里收敛为唯一出口：所有页面调用本函数，未知状态显式落到 `status-unknown`
 * （有兜底样式），而不是拼出一个不存在的类名静默失效。
 *
 * ⚠️ 类名与语言无关，所以它**不是** hook —— 保持普通函数。
 */
const STATUS_CLASS_MAP: Record<string, string> = {
  uploading: 'status-uploading',
  converting: 'status-converting',
  converted: 'status-converted',
  cleaning: 'status-cleaning',
  cleaned: 'status-cleaned',
  cleaning_failed: 'status-cleaning-failed',
  learning: 'status-learning',
  learning_failed: 'status-learning-failed',
  archived: 'status-archived',
  failed: 'status-failed',
};

export function statusClass(status: string | null | undefined): string {
  if (!status) return 'status-unknown';
  return STATUS_CLASS_MAP[status] || 'status-unknown';
}

/**
 * 一档自评的全部外观参数
 *
 * `quality` 与 `color` 与语言无关；`label` / `hint` 已翻译。
 * `tier` 是**语义名**（`again`/`hard`/`good`/`easy`），供需要按档位分支的
 * 调用方使用 —— 用语义名而不是 `quality` 数字，因为数字是 SM-2 的实现细节。
 */
export interface SelfRatingOption {
  /** SM-2 quality 分值（0-5） */
  quality: number;
  /** 语义档位名（与 FSRS rating 1-4 一一对应） */
  tier: SelfRatingTier;
  /** 按钮主文案（已翻译） */
  label: string;
  /** 补充说明，帮助用户区分档位（已翻译） */
  hint: string;
  /** 按钮强调色（a11y 口径算出来的，与语言无关） */
  color: string;
}

/**
 * 「薄弱点」列表里那块**卡片类型徽章**的配色（今日学习页与仪表盘共用一份）
 *
 * ## 为什么抽成常量而不是继续写两遍字面量
 *
 * 同一段徽章 JSX 在 `TodayLearn.tsx` 与 `Dashboard.tsx` 里**一字不差**地写了两遍
 * （同一处代码的两个渲染路径），而 a11y 审计的 **F-35** 正是在这两处之一
 * （今日学习页）报出来的：`#f44336` 压在 `#f4433620`（12.5% 叠白底 = `#fee8e6`）
 * 上只有 **3.13:1**，要求 4.5:1。
 *
 * ⚠️ 更值得记住的是**另一处为什么没报**：仪表盘那份在那个场景里渲染的是
 * `undefined`（默认桩的字段名与 `WeakPoint` 契约不一致，见 e2e/a11y-fixtures.ts），
 * **空文本没有对比度可判**，于是 axe 对整块无话可说。
 * 一份字面量写两遍，改了其中一处根本不会被发现 —— 所以这里收敛成**唯一出口**，
 * 两页 import 同一个对象。
 *
 * ## 取值（都是实测/算出来的，不是挑好看的）
 *
 * | 项 | 原值 | 对比度 | 新值 | 对比度 |
 * |---|---|---|---|---|
 * | 文字 | `#f44336` | 3.13:1（压 `#fee8e6`） | `#c0392b` | **4.68:1** |
 * | 底 | `#f4433620` | —— | `#c0392b1a` | —— |
 *
 * 底与字现在取自同一个基色（红 `#c0392b` = `--color-error`，白底 5.17:1），
 * 原来的写法是"底用 `#f44336` 的 12.5%、字也是 `#f44336`"——
 * 字改了底没改会留下两种红。透明度由 12.5% 降到 10%：淡底更浅，
 * 与深一档的文字拉开差距（12.5% 时是 4.51:1，贴着门槛；10% 是 4.68:1）。
 */
export const weakPointBadge = {
  background: '#c0392b1a',
  color: '#c0392b',
} as const;
