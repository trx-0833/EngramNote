/**
 * @file 学习评估 API
 * @description 笔记比对评估、开放性问题生成、作答评判与评估历史。
 */
import { request } from './client'

/** 评估结果 */
export interface AssessmentResult {
  /** 评估 ID */
  id: string;
  /** 评估模式：compare（笔记比对）或 quiz（开放性问题） */
  mode: 'compare' | 'quiz';
  /** 评分详情（compare: covered_points/uncovered_points；quiz: 各维度分数） */
  scores: AssessmentScores;
  /** 综合评分 */
  overall_score: number;
  /** 改进建议 */
  suggestions: string;
  /** 问题列表（quiz 模式） */
  quiz_questions?: Array<{ index: number; question: string; key_points: string[] }>;
  /** 答题结果列表（quiz 模式） */
  quiz_answers?: QuizAnswerItem[];
  /** 创建时间（ISO 8601 格式） */
  created_at: string;
}

/** 评估评分明细（与后端 assessment_service 产出结构一致） */
export interface AssessmentScores {
  covered_points?: string[];
  uncovered_points?: string[];
  coverage_score?: number;
  completeness_score?: number;
  depth_score?: number;
  clarity_score?: number;
}

/** 开放性问题作答与评判结果 */
export interface QuizAnswerItem {
  answer?: string;
  judgment?: {
    accuracy_score?: number;
    completeness_score?: number;
    depth_score?: number;
    feedback?: string;
  };
}

/** 评估历史条目 */
export interface AssessmentHistoryItem {
  /** 评估 ID */
  id: string;
  /** 评估模式 */
  mode: string;
  /** 综合评分 */
  overall_score: number;
  /** 创建时间（ISO 8601 格式） */
  created_at: string;
}

/**
 * 笔记比对评估
 * 比较学习资料与个人笔记的内容覆盖度、深度和清晰度。
 *
 * @param materialNoteIds - 学习资料笔记 ID 列表
 * @param personalNoteIds - 个人笔记 ID 列表
 * @returns 评估结果
 */
export async function compareAssessment(materialNoteIds: string[], personalNoteIds: string[]): Promise<AssessmentResult> {
  return request<AssessmentResult>('/assessment/compare', {
    method: 'POST',
    body: JSON.stringify({
      material_note_ids: materialNoteIds,
      personal_note_ids: personalNoteIds,
    }),
  });
}

/**
 * 生成开放性问题
 * 基于学习资料生成开放性问题，供用户作答。
 *
 * @param materialNoteIds - 学习资料笔记 ID 列表
 * @returns 评估结果（含问题列表）
 */
export async function generateQuiz(materialNoteIds: string[], personalNoteId?: string): Promise<AssessmentResult> {
  const body: { material_note_ids: string[]; personal_note_id?: string } = { material_note_ids: materialNoteIds };
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
export async function submitQuizAnswers(assessmentId: string, answers: Array<{ question_index: number; answer: string }>): Promise<AssessmentResult> {
  return request<AssessmentResult>('/assessment/submit-answer', {
    method: 'POST',
    body: JSON.stringify({
      assessment_id: assessmentId,
      answers,
    }),
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