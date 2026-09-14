/**
 * @file 学习评估 API
 * @description 笔记比对评估、开放性问题生成、作答评判与评估历史。
 */
import { request } from './client';
import type { BodyOf, Schema } from './generated/types';

// --- 评估相关类型（阶段 5.1 / S2：来源已改为 OpenAPI 生成类型）---

/**
 * 评估结果（生成自 `AssessmentResponse`）
 *
 * ⚠️ 与手写版本相比，**`scores` 从"必有"变成 `?: AssessmentScores | null`** ——
 * 契约里 `scores` 是 `anyOf[AssessmentScores, null]` 且没有默认值。
 * `generate-quiz` 未作答时后端返回的是 `{}`（不是 `null`），但类型上必须按
 * "可能没有"处理：这正是那类"前端以为一定有、后端不保证"的假设被收掉的地方。
 */
export type AssessmentResult = Schema<'AssessmentResponse'>;

/**
 * 评估评分明细（生成自 `AssessmentScores`）
 *
 * 同一个字段承载两种互斥形状，由 `mode` 决定：
 * compare 是 `covered_points` / `uncovered_points` / `coverage_score` /
 * `depth_score` / `clarity_score`；quiz 已作答是
 * `total_questions` / `average_score`；quiz 未作答是 `{}`。
 * 因此**每个字段都是 `?: T | null`** —— 没有任何一个字段是"任何模式下恒在"的。
 * `completeness_score` 是前端此前多声明的一个字段：后端 compare 分支**从不产出**它。
 */
export type AssessmentScores = Schema<'AssessmentScores'>;

/**
 * 一条作答及其评判（生成自 `QuizAnswer`）
 *
 * 比手写的 `QuizAnswerItem` 多一个**必填**的 `question_index`：
 * 它取自请求体，用来回答"这份答案对应哪道题"。
 */
export type QuizAnswerItem = Schema<'QuizAnswer'>;

/** 评估历史条目（生成自 `AssessmentHistoryItem`） */
export type AssessmentHistoryItem = Schema<'AssessmentHistoryItem'>;

/**
 * 笔记比对评估
 * 比较学习资料与个人笔记的内容覆盖度、深度和清晰度。
 *
 * @param materialNoteIds - 学习资料笔记 ID 列表
 * @param personalNoteIds - 个人笔记 ID 列表
 * @returns 评估结果
 */
export async function compareAssessment(
  materialNoteIds: string[],
  personalNoteIds: string[],
): Promise<AssessmentResult> {
  // 请求体由契约派生：`POST /api/assessment/compare`（`CompareRequest`：两个 ID 列表都必填）
  const body: BodyOf<'/assessment/compare', 'post'> = {
    material_note_ids: materialNoteIds,
    personal_note_ids: personalNoteIds,
  };
  return request<AssessmentResult>('/assessment/compare', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/**
 * 生成开放性问题
 * 基于学习资料生成开放性问题，供用户作答。
 *
 * @param materialNoteIds - 学习资料笔记 ID 列表
 * @returns 评估结果（含问题列表）
 */
export async function generateQuiz(
  materialNoteIds: string[],
  personalNoteId?: string,
): Promise<AssessmentResult> {
  // 请求体由契约派生：`POST /api/assessment/generate-quiz`（`QuizGenerateRequest`：`material_note_ids` 必填，`personal_note_id` 可空）
  const body: BodyOf<'/assessment/generate-quiz', 'post'> = { material_note_ids: materialNoteIds };
  if (personalNoteId) body.personal_note_id = personalNoteId;
  return request<AssessmentResult>('/assessment/generate-quiz', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/**
 * 提交开放性问题答案
 *
 * @param assessmentId - 评估 ID
 * @param answers - 答案列表
 * @returns 评估结果（含评判结果）
 */
export async function submitQuizAnswers(
  assessmentId: string,
  answers: Array<{ question_index: number; answer: string }>,
): Promise<AssessmentResult> {
  // 请求体对应 `POST /api/assessment/submit-answer`（`AnswerSubmitRequest`）：
  // 内层 `answers` 在契约里是 `{ [key: string]: unknown }[]`（后端把结构化答案塞进 JSON 列），
  // 前端**刻意保留更窄的手写类型** `{ question_index: number; answer: string }[]`（它更有用）。
  // `satisfies` 只做"与契约相容"的检查，不改变 `body` 的推断类型 —— 不是把类型放宽成契约的宽类型。
  const body = { assessment_id: assessmentId, answers } satisfies BodyOf<
    '/assessment/submit-answer',
    'post'
  >;
  return request<AssessmentResult>('/assessment/submit-answer', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/**
 * 获取笔记的评估历史
 *
 * @param noteId - 笔记 ID
 * @returns 评估历史列表
 */
export async function getAssessmentHistory(noteId: string): Promise<AssessmentHistoryItem[]> {
  return request<AssessmentHistoryItem[]>(`/assessment/history/${noteId}`);
}
