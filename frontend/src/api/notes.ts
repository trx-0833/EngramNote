/**
 * @file 笔记 API
 * @description 笔记的列表/详情/内容/归档/角色、回收站、批注、链接与版本历史相关接口。
 */
import { request, getToken, notifyTokenExpired, API_BASE, type Note, type NoteDetail, type NoteListResponse } from './client'

/**
 * 获取笔记列表（分页）
 * 支持按关键词搜索，返回按创建时间倒序排列的笔记列表。
 *
 * @param page - 页码，默认第 1 页
 * @param pageSize - 每页条数，默认 20 条
 * @param keyword - 搜索关键词，可选，用于按标题模糊匹配
 * @returns 分页笔记列表响应
 */
export async function getNotes(page = 1, pageSize = 20, keyword?: string, noteRole?: string): Promise<NoteListResponse> {
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
 * 更新笔记信息
 * 目前仅支持修改笔记标题。
 *
 * @param noteId - 笔记 ID
 * @param data - 更新数据，目前仅包含 title 字段
 * @returns 更新后的笔记信息
 */
export async function updateNote(noteId: string, data: { title?: string }): Promise<Note> {
  return request<Note>(`/notes/${noteId}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export type NoteContentTarget = 'clean' | 'original'

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
  target: NoteContentTarget = 'clean'
): Promise<Note> {
  return request<Note>(`/notes/${noteId}/content`, {
    method: 'PUT',
    body: JSON.stringify({ content, target }),
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
export async function getArchivedNotes(page = 1, pageSize = 20, noteRole?: string): Promise<NoteListResponse> {
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

// --- 回收站相关类型 ---

/** 回收站列表项：笔记 + 附属统计（"恢复可还原什么"的展示依据） */
export interface TrashNoteItem {
  /** 笔记信息 */
  note: Note;
  /** 知识卡片数 */
  card_count: number;
  /** 题目数 */
  quiz_count: number;
  /** 批注数 */
  annotation_count: number;
  /** 版本数 */
  version_count: number;
  /** 双向链接数 */
  link_count: number;
}

/** 回收站列表响应 */
export interface TrashListResponse {
  items: TrashNoteItem[];
  total: number;
}

/** 删除确认弹窗的关联统计 */
export interface TrashInfoResponse {
  /** 卡片总数 */
  card_count: number;
  /** 核心卡片数（is_key_point） */
  key_card_count: number;
  /** 双向链接数 */
  link_count: number;
}

/** 恢复结果：恢复后的笔记 + 同名冲突改名提示（无冲突为 null） */
export interface RestoreResponse {
  note: Note;
  renamed_to: string | null;
}

/** 清空回收站结果 */
export interface PurgeAllResponse {
  purged: number;
  failed: number;
}

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

// --- 批注相关类型 ---

/**
 * 批注信息
 */
export interface Annotation {
  id: string;
  note_id: string;
  view_mode: string;
  type: 'highlight' | 'underline';
  text_content: string;
  context_before: string;
  context_after: string;
  color: string | null;
  created_at: string;
}

/**
 * 批注列表响应
 */
export interface AnnotationListResponse {
  annotations: Annotation[];
}

// --- 批注 API ---

/**
 * 获取笔记批注列表
 *
 * @param noteId - 笔记 ID
 * @param viewMode - 视图模式：original 或 clean
 * @returns 批注列表响应
 */
export async function getAnnotations(noteId: string, viewMode: string): Promise<AnnotationListResponse> {
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
  }
): Promise<Annotation> {
  return request<Annotation>(`/notes/${noteId}/annotations`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/**
 * 删除批注
 *
 * @param noteId - 笔记 ID
 * @param annotationId - 批注 ID
 */
export async function deleteAnnotation(noteId: string, annotationId: string): Promise<void> {
  await request<{ success: boolean }>(`/notes/${noteId}/annotations/${annotationId}`, {
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
 * @param payload - 提问载荷：question（问题，可编辑）、selected_text（选中文本）、
 *                  context_before/context_after（选区前后上下文）、view_mode（original/clean）
 * @param signal - 可选 AbortSignal，用于中止流式请求
 */
export async function askNoteQuestionStream(
  noteId: string,
  payload: {
    question: string;
    selected_text: string;
    context_before?: string;
    context_after?: string;
    view_mode?: string;
  },
  signal?: AbortSignal
): Promise<ReadableStream<Uint8Array>> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'Accept': 'text/event-stream',
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const response = await fetch(`${API_BASE}/notes/${noteId}/ask/stream`, {
    method: 'POST',
    headers,
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
      ? error.detail.map((e: { msg?: string; message?: string }) => e.msg || e.message || String(e)).join('; ')
      : (error.detail || `请求失败: ${response.status}`);
    throw new Error(detail);
  }
  if (!response.body) {
    throw new Error('浏览器不支持流式响应');
  }
  return response.body;
}

// --- 笔记-资料链接相关类型 ---

/** 已关联的学习资料 */
export interface LinkedMaterial {
  id: string;
  title: string;
  source_type: string | null;
}

/** 引用该资料的个人笔记 */
export interface LinkedPersonalNote {
  id: string;
  title: string;
}

/** 笔记链接关系响应 */
export interface NoteLinksResponse {
  personal_note_id: string;
  linked_materials: LinkedMaterial[];
  linked_personal_notes: LinkedPersonalNote[];
  /** 悬挂链接数：资料被物理删除后置 NULL 的行数，用于显示"[已删除的笔记]"占位 */
  dangling_material_count?: number;
}

/** 更新链接关系响应 */
export interface UpdateLinksResponse {
  changed: boolean;
}

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
export async function updateNoteLinks(noteId: string, materialNoteIds: string[]): Promise<UpdateLinksResponse> {
  return request<UpdateLinksResponse>(`/notes/${noteId}/links`, {
    method: 'PUT',
    body: JSON.stringify({ material_note_ids: materialNoteIds }),
  });
}

// --- 版本历史类型定义 ---

/** 笔记版本快照信息 */
export interface NoteVersion {
  id: string;
  note_id: string;
  version_number: number;
  source: string;
  content_size: number;
  change_summary: string | null;
  created_at: string;
}

/** 版本列表响应 */
export interface NoteVersionListResponse {
  versions: NoteVersion[];
  total: number;
}

/** 单行 diff 数据 */
export interface NoteVersionDiffLine {
  type: 'added' | 'removed' | 'unchanged';
  content: string;
}

/** 版本对比 diff 响应 */
export interface NoteVersionDiffResponse {
  v1_number: number;
  v2_number: number;
  diff_lines: NoteVersionDiffLine[];
}

// --- 版本历史 API ---

/** 获取笔记的版本历史列表 */
export async function listVersions(noteId: string): Promise<NoteVersionListResponse> {
  return request<NoteVersionListResponse>(`/notes/${noteId}/versions`);
}

/** 预览指定版本的内容 */
export async function getVersion(noteId: string, versionNumber: number): Promise<{ content: string; version_number: number }> {
  return request(`/notes/${noteId}/versions/${versionNumber}`);
}

/** 对比两个版本的差异 */
export async function diffVersions(noteId: string, v1: number, v2: number): Promise<NoteVersionDiffResponse> {
  return request<NoteVersionDiffResponse>(`/notes/${noteId}/versions/diff?v1=${v1}&v2=${v2}`);
}

/** 恢复指定历史版本 */
export async function restoreVersion(noteId: string, versionNumber: number): Promise<NoteVersion> {
  return request<NoteVersion>(`/notes/${noteId}/versions/${versionNumber}/restore`, {
    method: 'POST',
    body: JSON.stringify({ confirm: true }),
  });
}