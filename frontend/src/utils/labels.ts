/**
 * @file 统一标签映射
 * @description 集中管理各页面共用的标签映射，颜色更和谐
 */

/** 来源类型到中文标签的映射 */
export const sourceTypeLabels: Record<string, string> = {
  pdf: 'PDF',
  image: '图片',
  docx: 'Word',
  pptx: 'PPT',
  xlsx: 'Excel',
  audio: '音频',
  video: '视频',
  markdown: 'Markdown',
};

/** 笔记状态到中文标签的映射 */
export const statusLabels: Record<string, string> = {
  uploading: '上传中',
  converting: '转换中',
  converted: '已完成',
  cleaning: '清洗中',
  cleaned: '已清洗',
  cleaning_failed: '清洗失败',
  learning: '学习中',
  learning_failed: '学习失败',
  archived: '已审阅',
  failed: '失败',
};

/** 知识卡片类型到中文标签的映射 */
export const cardTypeLabels: Record<string, string> = {
  concept: '概念',
  formula: '公式',
  qa: '问答',
  definition: '定义',
};

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

/** 题目类型到中文标签的映射 */
export const questionTypeLabels: Record<string, string> = {
  choice: '选择题',
  fill_blank: '填空题',
  short_answer: '简答题',
};

/** 题目类型到颜色的映射（白字压色块，见上面的取值口径） */
export const questionTypeColors: Record<string, string> = {
  choice: '#0f3460',
  fill_blank: '#6d28d9',
  short_answer: '#25714a',
};

/** 难度到中文标签的映射 */
export const difficultyLabels: Record<string, string> = {
  easy: '简单',
  medium: '中等',
  hard: '困难',
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

/** 知识卡片分类到中文标签的映射 */
export const cardCategoryLabels: Record<string, string> = {
  regular: '常规',
  blind_spot: '盲点',
  extension: '拓展',
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
 * SM-2 四档自评分量表（单一数据源）
 *
 * 为什么是四档而不是 0-5 六档：用户实际能可靠区分的是「完全没想起来 /
 * 勉强想起来 / 想起来 / 轻松想起来」四种主观体验，六档会让相邻档位
 * 无法区分、自评失去信息量。四档映射到 SM-2 的 quality 0/3/4/5
 * （整数分，SM-2 只用它做 >=3 的成功判定与 EF 增量）。
 *
 * `quality` 必须与后端 sm2_service 的语义一致：0=失败、3=勉强通过、
 * 4=通过、5=轻松，后端据此计算 EF 与下次间隔。
 *
 * ── 关于 `color`（本轮 a11y 修复，见 docs/a11y-audit.md 的 F-19）──
 *
 * 这四个色**本身就是区分手段**（按钮的文字与左侧 4px 竖条都用它），
 * 所以它们必须对低视力与色觉障碍用户也成立 —— 也就是小字要 ≥ 4.5:1。
 * 审计实测：金 #c9a959 白底只有 2.26:1、绿 #2d8a56 是 4.30:1，
 * 于是「勉强想起」与「想起来了」两档几乎看不出区别（红 #c0392b 5.44:1、
 * 墨 #0f3460 12.50:1 本来就够，未改）。修法是**只压深这两档**，
 * 色相与四档之间的相对关系（红→金→绿→墨）都不变：
 *
 * | 档位 | 原值 | 白底 | 新值 | 白底 |
 * |---|---|---|---|---|
 * | 完全忘记 | `#c0392b` | 5.44:1 | 未改 | — |
 * | 勉强想起 | `#c9a959` | 2.26:1 | `#8f7020` | 4.66:1 |
 * | 想起来了 | `#2d8a56` | 4.30:1 | `#25714a` | 5.93:1 |
 * | 轻松想起 | `#0f3460` | 12.50:1 | 未改 | — |
 *
 * ⚠️ 本文件里的颜色是**字面量**，不读 CSS 变量（`--color-success` 等）——
 * 这些值要作为内联样式与 canvas 参数使用，拿不到 `var()` 的解析结果。
 * 后果是"同一语义两处取值"：本文件是**唯一数据源**，
 * `base.css` 的 `--color-success` 必须**手工**与这里的绿保持一致。
 * 这次修复就是这么做的（两处都改成了 `#25714a`），但它没有门禁守着。
 */
export interface SelfRatingOption {
  /** SM-2 quality 分值（0-5） */
  quality: number;
  /** 按钮主文案 */
  label: string;
  /** 补充说明，帮助用户区分档位 */
  hint: string;
  /** 按钮强调色 */
  color: string;
}

export const selfRatingOptions: SelfRatingOption[] = [
  { quality: 0, label: '完全忘记', hint: '想不起来，需要重新学', color: '#c0392b' },
  { quality: 3, label: '勉强想起', hint: '很吃力，答得不完整', color: '#8f7020' },
  { quality: 4, label: '想起来了', hint: '稍作回忆就答对了', color: '#25714a' },
  { quality: 5, label: '轻松想起', hint: '脱口而出，毫不费力', color: '#0f3460' },
];

/** 判分方式到中文标签的映射（用于向用户解释这一次的分数是怎么来的） */
export const gradingMethodLabels: Record<string, string> = {
  choice: '选择题自动判分',
  fill_blank: '填空题自动判分',
  self_rating: '你的自评',
  ungraded: '待你自评（尚未计入复习进度）',
  legacy: '历史记录',
};

/**
 * FSRS 评分档位（1-4）到中文标签的映射
 *
 * 这四档**恰好**是自评四档按钮的取值（0/3/4/5 → Again/Hard/Good/Easy），
 * 所以文案与 `selfRatingOptions` 保持一致 —— 用户看到的是同一套说法。
 * 机器判分不会有 Easy（"选对了"里没有"毫不费力"这层信息，见后端
 * `scheduler_service.rating_from_quality`）。
 */
export const ratingLabels: Record<number, string> = {
  1: '完全忘记',
  2: '勉强想起',
  3: '想起来了',
  4: '轻松想起',
};

/**
 * 语义判分的三档结论（用于缺失点/误解点区块的标题与配色）
 *
 * ⚠️ **这一张表与上面四张方向相反**：它是**色字压浅底**
 * （`QuizAnswerCard` 的语义判分块，容器 `background: var(--color-bg)` = #faf9f7），
 * 所以判据是"该色对 #faf9f7 ≥ 4.5:1"，不是"对白色"。
 * 三档原值（`#4caf50` 2.64:1 / `#c9a959` 2.15:1 / `#f44336` 3.50:1，
 * 均对 #faf9f7）**三档全部不达标**，本轮一起压深：
 *
 * | 档位 | 原值 | 对 #faf9f7 | 新值 | 对 #faf9f7 |
 * |---|---|---|---|---|
 * | 回答正确 | `#4caf50` | 2.64:1 | `#25714a` | 5.64:1 |
 * | 答对了部分 | `#c9a959` | 2.15:1 | `#7d6417` | 5.38:1 |
 * | 回答错误 | `#f44336` | 3.50:1 | `#c0392b` | 5.17:1 |
 *
 * （`partial` 用 `#7d6417` 而不是色块底那支 `#8f7020`：后者对 #faf9f7 只有
 * 4.43:1，压在浅底上不够 —— 同一个语义在两种背景下各有一个值，见上面的口径表。
 * 同一个"压在浅底上的金"也用在 `CardDetail` 的「独立卡片」徽章上，
 * 那里底色是 `--color-accent-light`（rgba(201,169,89,.12) 叠白 = #f9f5eb），
 * `#c9a959` 只有 2.07:1，`#7d6417` 是 5.19:1。）
 */
export const verdictLabels: Record<string, { label: string; color: string }> = {
  correct: { label: '回答正确', color: '#25714a' },
  partial: { label: '答对了部分', color: '#7d6417' },
  incorrect: { label: '回答错误', color: '#c0392b' },
};

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
