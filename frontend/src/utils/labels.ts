/**
 * @file 统一标签映射
 * @description 集中管理各页面共用的标签映射，颜色更和谐
 */

/** 来源类型到中文标签的映射 */
export const sourceTypeLabels: Record<string, string> = {
  pdf: 'PDF', image: '图片', docx: 'Word', pptx: 'PPT', xlsx: 'Excel', audio: '音频', video: '视频', markdown: 'Markdown',
}

/** 笔记状态到中文标签的映射 */
export const statusLabels: Record<string, string> = {
  uploading: '上传中', converting: '转换中', converted: '已完成',
  cleaning: '清洗中', cleaned: '已清洗', cleaning_failed: '清洗失败',
  learning: '学习中', learning_failed: '学习失败', archived: '已审阅', failed: '失败',
}

/** 知识卡片类型到中文标签的映射 */
export const cardTypeLabels: Record<string, string> = {
  concept: '概念', formula: '公式', qa: '问答', definition: '定义',
}

/** 知识卡片类型到颜色的映射 */
export const cardTypeColors: Record<string, string> = {
  concept: '#0f3460', formula: '#6d28d9', qa: '#2d8a56', definition: '#c9a959',
}

/** 题目类型到中文标签的映射 */
export const questionTypeLabels: Record<string, string> = {
  choice: '选择题', fill_blank: '填空题', short_answer: '简答题',
}

/** 题目类型到颜色的映射 */
export const questionTypeColors: Record<string, string> = {
  choice: '#0f3460', fill_blank: '#6d28d9', short_answer: '#2d8a56',
}

/** 难度到中文标签的映射 */
export const difficultyLabels: Record<string, string> = {
  easy: '简单', medium: '中等', hard: '困难',
}

/** 难度到颜色的映射 */
export const difficultyColors: Record<string, string> = {
  easy: '#2d8a56', medium: '#c9a959', hard: '#c0392b',
}

/** 知识卡片分类到中文标签的映射 */
export const cardCategoryLabels: Record<string, string> = {
  regular: '常规', blind_spot: '盲点', extension: '拓展',
}

/** 知识卡片分类到颜色的映射 */
export const cardCategoryColors: Record<string, string> = {
  regular: '#6b7280', blind_spot: '#c0392b', extension: '#2d8a56',
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
}

export function statusClass(status: string | null | undefined): string {
  if (!status) return 'status-unknown'
  return STATUS_CLASS_MAP[status] || 'status-unknown'
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
 */
export interface SelfRatingOption {
  /** SM-2 quality 分值（0-5） */
  quality: number
  /** 按钮主文案 */
  label: string
  /** 补充说明，帮助用户区分档位 */
  hint: string
  /** 按钮强调色 */
  color: string
}

export const selfRatingOptions: SelfRatingOption[] = [
  { quality: 0, label: '完全忘记', hint: '想不起来，需要重新学', color: '#c0392b' },
  { quality: 3, label: '勉强想起', hint: '很吃力，答得不完整', color: '#c9a959' },
  { quality: 4, label: '想起来了', hint: '稍作回忆就答对了', color: '#2d8a56' },
  { quality: 5, label: '轻松想起', hint: '脱口而出，毫不费力', color: '#0f3460' },
]

/** 判分方式到中文标签的映射（用于向用户解释这一次的分数是怎么来的） */
export const gradingMethodLabels: Record<string, string> = {
  choice: '选择题自动判分',
  fill_blank: '填空题自动判分',
  self_rating: '你的自评',
  ungraded: '待你自评（尚未计入复习进度）',
  legacy: '历史记录',
}

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
}

/** 语义判分的三档结论（用于缺失点/误解点区块的标题与配色） */
export const verdictLabels: Record<string, { label: string; color: string }> = {
  correct: { label: '回答正确', color: '#4caf50' },
  partial: { label: '答对了部分', color: '#c9a959' },
  incorrect: { label: '回答错误', color: '#f44336' },
}
