/**
 * @file 笔记 API
 * @description 笔记的列表/详情/内容/归档/角色、回收站、批注、链接与版本历史相关接口。
 */
import {
  request,
  authorizedFetch,
  notifyTokenExpired,
  type Note,
  type NoteDetail,
  type NoteListResponse,
} from './client';
import type { BodyOf, BodyWithDefaults, Schema } from './generated/types';

/**
 * 获取笔记列表（分页）
 * 支持按关键词搜索，返回按创建时间倒序排列的笔记列表。
 *
 * @param page - 页码，默认第 1 页
 * @param pageSize - 每页条数，默认 20 条
 * @param keyword - 搜索关键词，可选，用于按标题模糊匹配
 * @returns 分页笔记列表响应
 */
export async function getNotes(
  page = 1,
  pageSize = 20,
  keyword?: string,
  noteRole?: string,
): Promise<NoteListResponse> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  // 仅在提供了关键词时才附加 keyword 参数
  if (keyword) params.set('keyword', keyword);
  // 仅在提供了笔记角色时才附加 note_role 参数
  if (noteRole) params.set('note_role', noteRole);
  return request<NoteListResponse>(`/notes?${params}`);
}

/**
 * 获取笔记详情
 * 返回包含 Markdown 内容和元数据的完整笔记信息。
 *
 * @param noteId - 笔记 ID
 * @returns 笔记详情
 */
export async function getNote(noteId: string): Promise<NoteDetail> {
  return request<NoteDetail>(`/notes/${noteId}`);
}

/**
 * 更新笔记请求体（契约派生：`PUT /api/notes/{note_id}`）
 *
 * 契约里 `title` 是可空的 `string | null`，手写时写的 `{ title?: string }`
 * 比契约窄 —— 换过来之后调用方可以传 `title: null`（后端接受）。
 */
export type UpdateNotePayload = BodyOf<'/notes/{note_id}', 'put'>;

/**
 * 更新笔记信息
 * 目前仅支持修改笔记标题。
 *
 * @param noteId - 笔记 ID
 * @param data - 更新数据（契约派生：`PUT /api/notes/{note_id}`，目前仅含可空的 title）
 * @returns 更新后的笔记信息
 */
export async function updateNote(noteId: string, data: UpdateNotePayload): Promise<Note> {
  return request<Note>(`/notes/${noteId}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

/** 内容写入目标：取自契约 `PUT /api/notes/{note_id}/content` 请求体的 `target`（clean / original） */
export type NoteContentTarget = BodyOf<'/notes/{note_id}/content', 'put'>['target'];

/**
 * 更新笔记的 Markdown 内容
 *
 * @param noteId - 笔记 ID
 * @param content - Markdown 内容字符串
 * @param target - 更新目标：clean（清洗版）或 original（原始版），默认 clean
 * @returns 更新后的笔记信息
 */
export async function updateNoteContent(
  noteId: string,
  content: string,
  target: NoteContentTarget = 'clean',
): Promise<Note> {
  // 请求体按契约派生：`PUT /api/notes/{note_id}/content`（字段与取值同切换前）
  const body: BodyOf<'/notes/{note_id}/content', 'put'> = { content, target };
  return request<Note>(`/notes/${noteId}/content`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

/**
 * 归档/取消归档笔记
 */
export async function archiveNote(noteId: string): Promise<Note> {
  return request<Note>(`/notes/${noteId}/archive`, {
    method: 'POST',
  });
}

/**
 * 更新笔记角色
 * 在学习资料（material）和我的笔记（personal_note）之间切换。
 *
 * @param noteId - 笔记 ID
 * @param noteRole - 新的笔记角色值：material 或 personal_note
 * @returns 更新后的笔记信息
 */
export async function updateNoteRole(noteId: string, noteRole: string): Promise<Note> {
  return request<Note>(`/notes/${noteId}/role?note_role=${encodeURIComponent(noteRole)}`, {
    method: 'PATCH',
  });
}

/**
 * 获取已归档笔记列表
 */
export async function getArchivedNotes(
  page = 1,
  pageSize = 20,
  noteRole?: string,
): Promise<NoteListResponse> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  // 仅在提供了笔记角色时才附加 note_role 参数
  if (noteRole) params.set('note_role', noteRole);
  return request<NoteListResponse>(`/notes/archive?${params}`);
}

/**
 * 删除笔记（移入回收站，软删除）
 * 笔记及其全部子内容作为原子包整体进回收站，可随时恢复。
 *
 * @param noteId - 笔记 ID
 */
export async function deleteNote(noteId: string): Promise<void> {
  return request<void>(`/notes/${noteId}`, { method: 'DELETE' });
}

// --- 回收站相关类型（阶段 5.1 / S2：来源已改为 OpenAPI 生成类型）---

/** 回收站列表项：笔记 + 附属统计（"恢复可还原什么"的展示依据；生成自 `TrashNoteItem`） */
export type TrashNoteItem = Schema<'TrashNoteItem'>;

/** 回收站列表响应（生成自 `TrashListResponse`） */
export type TrashListResponse = Schema<'TrashListResponse'>;

/** 删除确认弹窗的关联统计（生成自 `TrashInfoResponse`） */
export type TrashInfoResponse = Schema<'TrashInfoResponse'>;

/** 恢复结果：恢复后的笔记 + 同名冲突改名提示（无冲突为 null；生成自 `RestoreResponse`） */
export type RestoreResponse = Schema<'RestoreResponse'>;

/** 清空回收站结果（生成自 `PurgeAllResponse`） */
export type PurgeAllResponse = Schema<'PurgeAllResponse'>;

// --- 回收站 API ---

/**
 * 获取回收站笔记列表（含附属统计）
 *
 * @returns 回收站列表响应
 */
export async function getTrashedNotes(): Promise<TrashListResponse> {
  return request<TrashListResponse>(`/notes/trash`);
}

/**
 * 获取笔记的关联统计（删除确认弹窗文案依据）
 *
 * @param noteId - 笔记 ID
 */
export async function getNoteTrashInfo(noteId: string): Promise<TrashInfoResponse> {
  return request<TrashInfoResponse>(`/notes/${noteId}/trash-info`);
}

/**
 * 从回收站恢复笔记（原子包整体还原）
 *
 * @param noteId - 笔记 ID
 * @returns 恢复后的笔记及同名冲突改名提示
 */
export async function restoreNote(noteId: string): Promise<RestoreResponse> {
  return request<RestoreResponse>(`/notes/${noteId}/restore`, { method: 'POST' });
}

/**
 * 彻底删除笔记（物理删除，悬挂引用策略）
 *
 * @param noteId - 笔记 ID
 * @param promoteKeyCards - 是否将核心卡片提升为独立节点（图谱中保留）
 */
export async function purgeNote(noteId: string, promoteKeyCards = false): Promise<void> {
  const query = promoteKeyCards ? '?promote_key_cards=true' : '';
  return request<void>(`/notes/${noteId}/purge${query}`, { method: 'DELETE' });
}

/**
 * 清空回收站（物理删除所有回收站笔记）
 */
export async function purgeAllTrash(): Promise<PurgeAllResponse> {
  return request<PurgeAllResponse>(`/notes/trash/purge-all`, { method: 'DELETE' });
}

// --- 批注相关类型（阶段 5.1 / S2：来源已改为 OpenAPI 生成类型）---

/**
 * 批注信息（生成自 `AnnotationResponse`）
 *
 * ⚠️ `type` 在契约里是 `string`，手写的 `'highlight' | 'underline'`
 * 因此被放宽 —— 与 `DiffLine.type` 同一类：后端没把它声明成枚举。
 */
export type Annotation = Schema<'AnnotationResponse'>;

/** 批注列表响应（生成自 `AnnotationListResponse`） */
export type AnnotationListResponse = Schema<'AnnotationListResponse'>;

/** 删除批注的结果（生成自 `AnnotationDeleteResponse`：`{ success: boolean }`，后端恒为 true） */
export type AnnotationDeleteResponse = Schema<'AnnotationDeleteResponse'>;

// --- 批注 API ---

/**
 * 获取笔记批注列表
 *
 * @param noteId - 笔记 ID
 * @param viewMode - 视图模式：original 或 clean
 * @returns 批注列表响应
 */
export async function getAnnotations(
  noteId: string,
  viewMode: string,
): Promise<AnnotationListResponse> {
  return request<AnnotationListResponse>(`/notes/${noteId}/annotations?view_mode=${viewMode}`);
}

/**
 * 创建批注
 *
 * @param noteId - 笔记 ID
 * @param data - 批注数据
 * @returns 创建后的批注信息
 */
export async function createAnnotation(
  noteId: string,
  data: {
    view_mode: string;
    type: 'highlight' | 'underline';
    text_content: string;
    context_before: string;
    context_after: string;
    color?: string;
  },
): Promise<Annotation> {
  // 请求体按契约派生：`POST /api/notes/{note_id}/annotations`。
  // 对外签名有意保持比契约窄：契约里 `type` 只是 `string`，这里仍只收
  // 前端的 `'highlight' | 'underline'`；`color` 仍不收 null。
  // 发出去的 body 用契约类型约束 —— 字段名或取值拼错都会在这里编译失败。
  const body: BodyOf<'/notes/{note_id}/annotations', 'post'> = { ...data };
  return request<Annotation>(`/notes/${noteId}/annotations`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/**
 * 删除批注
 *
 * @param noteId - 笔记 ID
 * @param annotationId - 批注 ID
 * @returns 删除结果（契约 `DELETE /api/notes/{note_id}/annotations/{annotation_id}`：
 *          后端返回 `{ success: true }`，此前这里把它 await 掉、声明成 `Promise<void>`）
 */
export async function deleteAnnotation(
  noteId: string,
  annotationId: string,
): Promise<AnnotationDeleteResponse> {
  return request<AnnotationDeleteResponse>(`/notes/${noteId}/annotations/${annotationId}`, {
    method: 'DELETE',
  });
}

// --- 选中文本 AI 提问 API ---

/**
 * 基于当前笔记选区局部上下文，对选中文本流式提问（SSE 流）
 * 返回一个 ReadableStream，调用方需自行解析 SSE 事件：
 * - event: meta / data: {"provider":"..."}
 * - event: token / data: {"content":"..."}
 * - event: done / data: {}
 * - event: error / data: {"message":"..."}
 *
 * @param noteId - 笔记 ID
 * @param payload - 提问载荷（请求体契约派生：`POST /api/notes/{note_id}/ask/stream`）：
 *                  question（问题，可编辑）、selected_text（选中文本）必填；
 *                  context_before / context_after / view_mode 可缺省 —— 后端
 *                  `NoteAskRequest` 里这三个字段带默认值，分别是 "" / "" / "original"
 * @param signal - 可选 AbortSignal，用于中止流式请求
 */
export async function askNoteQuestionStream(
  noteId: string,
  payload: BodyWithDefaults<
    '/notes/{note_id}/ask/stream',
    'post',
    'context_before' | 'context_after' | 'view_mode'
  >,
  signal?: AbortSignal,
): Promise<ReadableStream<Uint8Array>> {
  // 走统一的认证 fetch（阶段 6.3）：访问令牌过期时先刷新一次再重放一次，
  // 而不是直接把用户登出。手写 Authorization 头的那份代码已删除 ——
  // 两份实现必然会在"过期后怎么办"上分叉。
  const response = await authorizedFetch(`/notes/${noteId}/ask/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    // 请求体按契约派生：`POST /api/notes/{note_id}/ask/stream`（SSE 响应解析仍手写）
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) {
    if (response.status === 401) {
      notifyTokenExpired();
      throw new Error('登录已过期，请重新登录');
    }
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    const detail = Array.isArray(error.detail)
      ? error.detail
          .map((e: { msg?: string; message?: string }) => e.msg || e.message || String(e))
          .join('; ')
      : error.detail || `请求失败: ${response.status}`;
    throw new Error(detail);
  }
  if (!response.body) {
    throw new Error('浏览器不支持流式响应');
  }
  return response.body;
}

// --- 笔记-资料链接相关类型（阶段 5.1 / S2：来源已改为 OpenAPI 生成类型）---
//
// ⚠️ 此前 `linked_materials[]` / `linked_personal_notes[]` 在 schema 里是裸
// `dict`（§7.1 的内层空壳），前端只能靠手写维持正确；P2 补齐模型后它们
// 现在指向真实 `$ref`（`LinkedMaterialItem` / `LinkedPersonalNoteItem`），
// 切换后**这两处字段再也不靠手写维护** —— 这是本轮收益最大的地方之一。

/** 已关联的学习资料（生成自 `LinkedMaterialItem`；`source_type` 可缺省且可空） */
export type LinkedMaterial = Schema<'LinkedMaterialItem'>;

/** 引用该资料的个人笔记（生成自 `LinkedPersonalNoteItem`） */
export type LinkedPersonalNote = Schema<'LinkedPersonalNoteItem'>;

/**
 * 笔记链接关系响应（生成自 `LinkListResponse`）
 *
 * `dangling_material_count` 带默认值 → 生成类型里是**必填**：
 * 它是资料被物理删除后置 NULL 的行数，用于显示"[已删除的笔记]"占位。
 */
export type NoteLinksResponse = Schema<'LinkListResponse'>;

/** 更新链接关系响应（生成自 `LinkUpdateResponse`） */
export type UpdateLinksResponse = Schema<'LinkUpdateResponse'>;

// --- 链接管理 API ---

/**
 * 获取笔记的链接关系
 * 返回该笔记关联的学习资料列表，以及引用该资料的个人笔记列表。
 *
 * @param noteId - 笔记 ID
 * @returns 链接关系响应
 */
export async function getNoteLinks(noteId: string): Promise<NoteLinksResponse> {
  return request<NoteLinksResponse>(`/notes/${noteId}/links`);
}

/**
 * 更新笔记关联的学习资料
 *
 * @param noteId - 笔记 ID
 * @param materialNoteIds - 学习资料笔记 ID 列表
 * @returns 更新结果，包含是否发生变化
 */
export async function updateNoteLinks(
  noteId: string,
  materialNoteIds: string[],
): Promise<UpdateLinksResponse> {
  // 请求体按契约派生：`PUT /api/notes/{note_id}/links`（字段与取值同切换前）
  const body: BodyOf<'/notes/{note_id}/links', 'put'> = { material_note_ids: materialNoteIds };
  return request<UpdateLinksResponse>(`/notes/${noteId}/links`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

// --- 版本历史类型定义（阶段 5.1 / S2：来源已改为 OpenAPI 生成类型）---

/** 笔记版本快照信息（生成自 `NoteVersionResponse`） */
export type NoteVersion = Schema<'NoteVersionResponse'>;

/** 版本列表响应（生成自 `NoteVersionListResponse`） */
export type NoteVersionListResponse = Schema<'NoteVersionListResponse'>;

/** 单行 diff 数据（生成自 `NoteVersionDiffLine`） */
export type NoteVersionDiffLine = Schema<'NoteVersionDiffLine'>;

/** 版本对比 diff 响应（生成自 `NoteVersionDiffResponse`） */
export type NoteVersionDiffResponse = Schema<'NoteVersionDiffResponse'>;

/** 单个版本快照的 Markdown 内容（生成自 `NoteVersionContentResponse`） */
export type NoteVersionContentResponse = Schema<'NoteVersionContentResponse'>;

// --- 版本历史 API ---

/** 获取笔记的版本历史列表 */
export async function listVersions(noteId: string): Promise<NoteVersionListResponse> {
  return request<NoteVersionListResponse>(`/notes/${noteId}/versions`);
}

/** 预览指定版本的内容 */
export async function getVersion(
  noteId: string,
  versionNumber: number,
): Promise<NoteVersionContentResponse> {
  return request(`/notes/${noteId}/versions/${versionNumber}`);
}

/** 对比两个版本的差异 */
export async function diffVersions(
  noteId: string,
  v1: number,
  v2: number,
): Promise<NoteVersionDiffResponse> {
  return request<NoteVersionDiffResponse>(`/notes/${noteId}/versions/diff?v1=${v1}&v2=${v2}`);
}

/** 恢复指定历史版本 */
export async function restoreVersion(noteId: string, versionNumber: number): Promise<NoteVersion> {
  // 请求体按契约派生：`POST /api/notes/{note_id}/versions/{version_number}/restore`
  // （该端点的 requestBody 在 schema 里本身可选，生成类型是 `confirm?: boolean | null`）
  const body: BodyOf<'/notes/{note_id}/versions/{version_number}/restore', 'post'> = {
    confirm: true,
  };
  return request<NoteVersion>(`/notes/${noteId}/versions/${versionNumber}/restore`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}
