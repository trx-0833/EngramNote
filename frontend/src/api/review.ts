/**
 * @file 复习调度 API
 * @description 到期题目、提交答案、复习统计与历史、快速复习、复习提醒。
 */
import { request } from './client'
import type { Schema } from './generated/types'

// --- 复习相关类型（阶段 5.1 / S2：来源已改为 OpenAPI 生成类型）---

/**
 * 到期题目（生成自 `DueQuizResponse`）
 *
 * ⚠️ 比手写版本多了**必填的 `repetition`**（后端有默认值，一定会序列化出来）：
 * 手写的 `DueQuiz` 把 `review_count` / `interval` / `easiness_factor` 标成必有、
 * 却**根本没有 `repetition`** —— 这正是"前端类型描述的现实与契约不同"的一处。
 */
export type DueQuiz = Schema<'DueQuizResponse'>;

/** 到期题目列表响应（生成自 `DueQuizListResponse`） */
export type DueQuizListResponse = Schema<'DueQuizListResponse'>;

/** 调度结果（生成自 `SM2Info`；字段名 `sm2` 是历史遗留，阶段 3.6 之后默认由 FSRS-5 产生） */
export type SM2Info = Schema<'SM2Info'>;

/**
 * 判分方式：choice/fill_blank 为可靠的自动判分，self_rating 为用户自评
 *
 * ⚠️ **契约里这只是 `string`**（`SubmitAnswerResponse.grading_method` 没写成枚举），
 * 所以这个联合**约束不到任何东西**：它既不能保证后端只给这 5 个值，
 * 也不能保证前端比较的那 5 个字面量是对的。保留它只是为了不改导出名 ——
 * 真正要修的是后端把它声明成枚举。
 */
export type GradingMethod = 'choice' | 'fill_blank' | 'self_rating' | 'ungraded' | 'legacy';

/**
 * LLM 语义判分明细（生成自 `GradingDetail`）
 *
 * 关键设计：**不是 0-100 分，而是"缺了哪一点、误解了哪一点"**。
 *
 * ⚠️ 与手写版本的两处口径差：
 * - `missing_points` / `misconceptions` / `confidence` / `reason` 带默认值，
 *   生成类型里是**必填**（`verdict` 之外不再有 `?`）；
 * - `verdict` 在契约里是 `string` 而不是 `'correct' | 'partial' | 'incorrect'` ——
 *   后端刻意如此（库里可能存着已下线 verdict 的旧数据，收紧会让读取变 500）。
 */
export type GradingDetail = Schema<'GradingDetail'>;

/** 提交答案响应（生成自 `SubmitAnswerResponse`） */
export type SubmitAnswerResponse = Schema<'SubmitAnswerResponse'>;

/** 复习统计（生成自 `ReviewStatsResponse`） */
export type ReviewStats = Schema<'ReviewStatsResponse'>;

/** 复习历史条目（生成自 `ReviewHistoryItem`） */
export type ReviewHistoryItem = Schema<'ReviewHistoryItem'>;

/** 复习历史响应（生成自 `ReviewHistoryResponse`） */
export type ReviewHistoryResponse = Schema<'ReviewHistoryResponse'>;

/** 快速复习题目（复用 DueQuiz 类型） */
export type QuickQuiz = DueQuiz

/**
 * 快速复习响应
 *
 * ⚠️ `GET /review/quick/{note_id}` 在契约里**就是** `DueQuizListResponse`
 * （后端 `response_model=DueQuizListResponse`），没有独立的模型 ——
 * 所以这里直接指向同一个生成类型，而不是再手写一份同形状的接口。
 */
export type QuickReviewResponse = Schema<'DueQuizListResponse'>;

/**
 * 获取今日到期复习题目
 */
export async function getDueQuizzes(limit = 50): Promise<DueQuizListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  return request<DueQuizListResponse>(`/review/due?${params}`);
}

/**
 * 提交答案
 *
 * 两阶段提交：简答题的自动判分不可信，第一次调用不传 selfRating 只会落一条
 * 占位记录、不推进调度（响应 needs_self_assessment=true）；用户四档自评后
 * 再次调用并传入 selfRating，才真正完成判分与调度。
 *
 * @param selfRating - 用户自评的 SM-2 质量分（0-5），不传表示本次不自评
 * @param useSemanticGrading - 是否请求 LLM 语义判分（阶段 3.5，仅简答题有意义）。
 *   **默认关闭**：判分在提交的同步路径上调用外部 LLM，会给每次提交叠加一次
 *   往返延迟，而两阶段流程本来就以用户自评为主评分来源（理由见后端
 *   `SubmitAnswerRequest.use_semantic_grading`）。只有首次提交需要它 ——
 *   带 `selfRating` 的那次自评优先，后端会直接跳过 LLM。
 */
export async function submitAnswer(
  quizId: string,
  userAnswer: string,
  timeSpentMs = 0,
  selfRating?: number,
  useSemanticGrading = false,
): Promise<SubmitAnswerResponse> {
  return request<SubmitAnswerResponse>('/review/submit', {
    method: 'POST',
    body: JSON.stringify({
      quiz_id: quizId,
      user_answer: userAnswer,
      time_spent_ms: timeSpentMs,
      ...(selfRating === undefined ? {} : { self_rating: selfRating }),
      ...(useSemanticGrading ? { use_semantic_grading: true } : {}),
    }),
  });
}

/**
 * 获取复习统计
 */
export async function getReviewStats(): Promise<ReviewStats> {
  return request<ReviewStats>('/review/stats');
}

/**
 * 获取复习历史
 */
export async function getReviewHistory(page = 1, pageSize = 20): Promise<ReviewHistoryResponse> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  return request<ReviewHistoryResponse>(`/review/history?${params}`);
}

/**
 * 获取指定笔记的快速复习题目
 * 用于笔记上传并完成理解后，立即复习该笔记关联的所有题目。
 *
 * @param noteId - 笔记 ID
 * @returns 快速复习题目列表
 */
export async function getQuickReview(noteId: string): Promise<QuickReviewResponse> {
  return request<QuickReviewResponse>(`/review/quick/${noteId}`);
}

/**
 * 提交快速复习答案
 * 与 submitAnswer 不同，此接口不受每日复习上限限制，
 * 用于"立即学习"场景。
 *
 * @param noteId - 笔记 ID
 * @param quizId - 题目 ID
 * @param userAnswer - 用户答案
 * @param timeSpentMs - 答题耗时（毫秒）
 * @param selfRating - 用户自评的 SM-2 质量分（0-5），见 submitAnswer 的两阶段说明
 * @param useSemanticGrading - 是否请求 LLM 语义判分，见 submitAnswer 的说明
 * @returns 提交答案响应
 */
export async function submitQuickReviewAnswer(
  noteId: string,
  quizId: string,
  userAnswer: string,
  timeSpentMs = 0,
  selfRating?: number,
  useSemanticGrading = false,
): Promise<SubmitAnswerResponse> {
  return request<SubmitAnswerResponse>(`/review/quick/${noteId}/submit`, {
    method: 'POST',
    body: JSON.stringify({
      quiz_id: quizId,
      user_answer: userAnswer,
      time_spent_ms: timeSpentMs,
      ...(selfRating === undefined ? {} : { self_rating: selfRating }),
      ...(useSemanticGrading ? { use_semantic_grading: true } : {}),
    }),
  });
}

/** 复习提醒响应（生成自 `ReminderResponse`） */
export type ReminderResponse = Schema<'ReminderResponse'>;

/** 获取复习提醒数据 */
export async function getReminders(): Promise<ReminderResponse> {
  return request<ReminderResponse>('/review/reminders');
}

// ---------------------------------------------------------------------------
// 卡片直接复习（阶段 3.12 的前端一半）
//
// ## 为什么要有这条独立路径
//
// 旧模型下调度参数只挂在 `quiz_items` 上，于是**没有生成过题目的卡片
// 永远进不了复习队列**（overhaul-plan 症状 L-5）。后端早已提供这两个接口，
// 但前端一直没有调用方 —— "没有题目的卡片可以直接复习"这件事
// 用户一次也没法用。这里补上。
// ---------------------------------------------------------------------------

/**
 * 到期可复习的卡片（生成自 `CardReviewItem`）
 *
 * ⚠️ `mastery_level` / `interval_days` / `repetition` / `lapses` / `review_count` /
 * `easiness_factor` 在后端都带默认值 → 生成类型里是**必填**；而 `summary` /
 * `chapter_title` / `next_review_at` 这类 `anyOf[T, null]` 无默认值的字段是
 * `?: T | null`（既可能缺省、也可能是 null）。
 */
export type DueCard = Schema<'CardReviewItem'>;

/** 到期卡片列表响应（生成自 `CardReviewListResponse`） */
export type CardReviewListResponse = Schema<'CardReviewListResponse'>;

/**
 * 卡片复习提交响应（生成自 `CardReviewSubmitResponse`）
 *
 * `stability` 是 FSRS 的记忆强度 S（天）：回忆概率降到 90% 所需的天数 ——
 * "下次复习 N 天后"的依据（`interval_days` 正是由 S 与目标保持率解出来的）；
 * `review_scheduler=sm2` 回退时为 null。`difficulty` 是 FSRS 的难度 D（1-10）。
 * `predicted_retention` 是**复习前**模型预测的可回忆概率（0-1）。
 */
export type CardReviewSubmitResponse = Schema<'CardReviewSubmitResponse'>;

/**
 * 获取当前到期的卡片
 *
 * 与"答题复习"是**并行**的两条路径：这里不涉及题目，用户直接对卡片回忆并自评。
 */
export async function getDueCards(limit = 20): Promise<CardReviewListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  return request<CardReviewListResponse>(`/review/cards/due?${params}`);
}

/**
 * 提交一次卡片级复习
 *
 * @param selfRating - 四档自评的 SM-2 质量分：0 完全忘记 / 3 勉强想起 / 4 想起 / 5 轻松
 *   （**必填**：卡片没有可自动判分的答案，自评是唯一的评分来源）
 * @param userAnswer - 回忆内容的备注（可选）
 */
export async function submitCardReview(
  cardId: string,
  selfRating: number,
  userAnswer = '',
  timeSpentMs = 0,
): Promise<CardReviewSubmitResponse> {
  return request<CardReviewSubmitResponse>(`/review/cards/${cardId}/submit`, {
    method: 'POST',
    body: JSON.stringify({
      self_rating: selfRating,
      user_answer: userAnswer,
      time_spent_ms: timeSpentMs,
    }),
  });
}