/**
 * @file AI 问答历史的接口层（docs/qa-history-plan.md §5.1）
 *
 * ## 为什么单独一个模块，而不是塞进 `client.ts`
 *
 * `client.ts` 已经 1000+ 行（鉴权 / 上传 / SSE / 错误本地化），而问答历史的三个
 * 只读 + 删除接口自成一组。本项目既有先例：`api/notes.ts` / `api/knowledge.ts`
 * 都按领域拆分，`client.ts` 只保留跨领域基础设施（`request` / `authorizedFetch`）。
 *
 * ⚠️ 本文件与 `api/qa.ts`（笔记理解 / 题库相关）**不是同一件事**：
 * 那个模块名里的 "qa" 指的是"理解与出题"，本模块是"问答历史"。为避免混淆，
 * 本文件里所有函数名都带 `Qa` 前缀并以 `Sessions` / `Session` 结尾。
 *
 * ## 类型从契约派生，不手抄
 *
 * `QueryOf<...>` / `Schema<...>` 来自 `generated/types`（`npm run gen:api` 生成）。
 * 手抄类型在"后端改了字段"时不会报警，而这类漂移在运行时只表现为
 * "某块信息永远不显示"—— 最难发现的一种。
 */
import { ApiError, authorizedFetch, notifyTokenExpired, readErrorBody, request } from './client';
import { translate } from '../i18n/translate';
import type { QueryOf, Schema } from './generated/types';
import { buildQuery } from './query';

/** 会话摘要（列表项） */
export type QASessionSummary = Schema<'QASessionResponse'>;
/** 一轮问答 */
export type QATurnItem = Schema<'QATurnResponse'>;
/** 会话详情：会话 + 其轮次 */
export type QASessionDetail = Schema<'QASessionDetailResponse'>;
/** 会话列表响应 */
export type QASessionList = Schema<'QASessionListResponse'>;
/** 「清空全部」的结果 */
export type QAClearAllResult = Schema<'QAClearAllResponse'>;

/**
 * 列出当前用户的问答会话（按最近聊过排序）
 *
 * `keyword` 由后端同时匹配标题、最后问题**与该会话内任意轮次的问答正文** ——
 * 用户记住的往往是答案里的那句话。
 */
export async function getQaSessions(
  params: { page?: number; page_size?: number; keyword?: string; surface?: string } = {},
  options?: { signal?: AbortSignal },
): Promise<QASessionList> {
  // `keyword` 先 trim：`buildQuery` 只跳过 `undefined` / 空串，纯空白会变成
  // `keyword=+++`，后端把"搜三个空格"当成过滤条件 → 列表永远是空的。
  const keyword = params.keyword?.trim() || undefined;
  const query: QueryOf<'/qa/sessions', 'get'> = {
    page: params.page ?? 1,
    page_size: params.page_size ?? 20,
    keyword,
    surface: params.surface?.trim() || undefined,
  };
  return request<QASessionList>(`/qa/sessions${buildQuery(query)}`, { signal: options?.signal });
}

/**
 * 取单个会话 + 其轮次
 *
 * 会话不存在（或属于别人、或已被删）时后端回 `404 QA_SESSION_NOT_FOUND`，
 * 调用方据此清掉 URL 上的 `?s=` 并提示"已开始新对话"。
 *
 * @throws {ApiError} `code` 为 `QA_SESSION_NOT_FOUND` 时表示会话已消失
 */
export async function getQaSession(
  sessionId: string,
  options?: { signal?: AbortSignal },
): Promise<QASessionDetail> {
  return request<QASessionDetail>(`/qa/sessions/${encodeURIComponent(sessionId)}`, {
    signal: options?.signal,
  });
}

/**
 * 删除一个会话（软删；30 天后由后台任务硬删）
 *
 * 后端回 `204`（无响应体），所以不能走会解析 JSON 的 `request()`；
 * 失败分支也**直接读这一次响应**（`readErrorBody`）—— 审计 m4 抓到原先的写法
 * 为了复用错误本地化又发了一次 `DELETE`：破坏性请求被发两次，第一次其实已成功、
 * 第二次拿到 404 时用户会看到"对话不存在"而列表仍是旧的。
 *
 * @throws {ApiError} 再删一次会抛 `QA_SESSION_NOT_FOUND`
 */
export async function deleteQaSession(
  sessionId: string,
  options?: { signal?: AbortSignal },
): Promise<void> {
  const path = `/qa/sessions/${encodeURIComponent(sessionId)}`;
  const response = await authorizedFetch(path, { method: 'DELETE', signal: options?.signal });
  if (response.ok) return;
  if (response.status === 401) {
    notifyTokenExpired();
    throw new ApiError(translate('apiErrors.sessionExpired'));
  }
  const { detail, code } = await readErrorBody(response);
  throw new ApiError(detail, code);
}

/**
 * 清空全部问答历史（软删），返回删掉的会话数
 *
 * 返回条数而不是 void：清空后列表变空，"本来就空"与"清掉了 12 个"在界面上一样，
 * 条数是那次操作唯一的反馈。
 */
export async function clearQaSessions(options?: {
  signal?: AbortSignal;
}): Promise<QAClearAllResult> {
  return request<QAClearAllResult>('/qa/sessions', {
    method: 'DELETE',
    signal: options?.signal,
  });
}

/** 判定"会话已消失"（前端据此清掉 `?s=` 并提示已开始新对话） */
export function isSessionGoneError(error: unknown): boolean {
  return (error as ApiError | undefined)?.code === 'QA_SESSION_NOT_FOUND';
}
