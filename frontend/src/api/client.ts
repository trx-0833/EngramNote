/**
 * @file API 请求客户端
 * @description 封装了与后端 API 交互的所有方法，包括认证、笔记管理和文件上传。
 * 采用原生 fetch 实现，不引入 axios 等第三方库，保持最小依赖。
 * 所有 API 请求均以 /api 为基础路径，通过 Bearer Token 进行身份认证。
 */

/** API 基础路径，所有请求都会在此路径前缀下发起 */
const API_BASE = '/api';

// --- 类型定义 ---

/** 用户信息 */
export interface User {
  /** 用户唯一标识 */
  id: string;
  /** 用户邮箱，同时作为登录账号 */
  email: string;
  /** 用户显示名称 */
  username: string;
  /** 账号是否激活 */
  is_active: boolean;
  /** 账号创建时间（ISO 8601 格式） */
  created_at: string;
}

/** 认证令牌响应，登录/注册成功后返回 */
export interface TokenResponse {
  /** JWT 访问令牌，后续请求需携带此令牌 */
  access_token: string;
  /** 令牌类型，固定为 "bearer" */
  token_type: string;
  /** 当前登录用户信息 */
  user: User;
}

/** 笔记概要信息，用于列表展示 */
export interface Note {
  /** 笔记唯一标识 */
  id: string;
  /** 所属用户 ID */
  user_id: string;
  /** 笔记标题，通常从文件名提取 */
  title: string;
  /** 来源类型，如 pdf、image、docx、pptx、xlsx、audio、video */
  source_type: string;
  /** 笔记角色：material（学习资料）或 personal_note（我的笔记） */
  note_role?: string;
  /**
   * 笔记处理状态，流转顺序：
   * uploading → converting → converted → cleaning → cleaned → learning → archived
   * 任何阶段都可能变为 failed
   */
  status: string;
  /** 原始文件大小（字节） */
  file_size: number;
  /** 文档页数，仅 PDF/Office 文档有值 */
  page_count: number | null;
  /** 错误信息，仅 status 为 failed 时有值 */
  error_message: string | null;
  /** 创建时间（ISO 8601 格式） */
  created_at: string;
  /** 最后更新时间（ISO 8601 格式） */
  updated_at: string;
}

/** 笔记详情，在 Note 基础上增加了 Markdown 内容和元数据 */
export interface NoteDetail extends Note {
  /** 原始 Markdown 内容，由后端从文件转换生成 */
  original_md_content: string | null;
  /** 清洗后的 Markdown 内容，由后端 AI 清洗流程生成 */
  clean_md_content: string | null;
  /** 文件元数据，如 PDF 的作者、标题等信息 */
  metadata_: Record<string, unknown> | null;
  /** 视频流地址，仅 source_type 为 video 时有值 */
  video_url?: string;
}

/** 笔记列表分页响应 */
export interface NoteListResponse {
  /** 当前页的笔记列表 */
  items: Note[];
  /** 笔记总数，用于计算分页 */
  total: number;
  /** 当前页码（从 1 开始） */
  page: number;
  /** 每页条数 */
  page_size: number;
}

// --- 清洗相关类型定义 ---

/** 清洗触发响应 */
export interface CleaningStartResponse {
  /** 笔记 ID */
  id: string;
  /** 当前状态 */
  status: string;
  /** 提示信息 */
  message: string;
}

/** 停止清洗响应 */
export interface CleaningStopResponse {
  /** 笔记 ID */
  id: string;
  /** 当前状态 */
  status: string;
  /** 提示信息 */
  message: string;
}

/** 清洗状态响应 */
export interface CleaningStatusResponse {
  /** 笔记 ID */
  id: string;
  /** 当前状态 */
  status: string;
  /** 清洗后 Markdown 路径 */
  clean_md_path: string | null;
  /** 错误信息 */
  error_message: string | null;
  /** 元数据（含清洗统计） */
  metadata_: Record<string, unknown> | null;
}

/** 单行 diff 数据 */
export interface DiffLine {
  /** 行类型：added（新增）、removed（删除）、unchanged（未变） */
  type: 'added' | 'removed' | 'unchanged';
  /** 行内容 */
  content: string;
  /** 原始版行号 */
  line_number_original: number | null;
  /** 清洗版行号 */
  line_number_clean: number | null;
}

/** diff 块数据（连续的变更行） */
export interface DiffBlock {
  /** 块内的行列表 */
  lines: DiffLine[];
}

/** 清洗 diff 响应 */
export interface CleaningDiffResponse {
  /** 笔记 ID */
  note_id: string;
  /** 原始版行数 */
  original_lines: number;
  /** 清洗版行数 */
  clean_lines: number;
  /** diff 块列表 */
  blocks: DiffBlock[];
  /** 清洗统计信息 */
  stats: Record<string, number> | null;
}

/** 块操作响应（恢复/删除） */
export interface BlockOperationResponse {
  /** 笔记 ID */
  note_id: string;
  /** 块序号 */
  block_index: number;
  /** 操作类型 */
  operation: 'restored' | 'deleted';
  /** 提示信息 */
  message: string;
}

// --- Token 管理 ---

/** localStorage 中存储 JWT 令牌的键名 */
const TOKEN_KEY = 'engramnote_token';

/** Token 过期事件名称，用于通知 App 组件跳转到登录页 */
export const TOKEN_EXPIRED_EVENT = 'token-expired';

/**
 * 获取本地存储的 JWT 令牌
 * @returns 令牌字符串，若未登录则返回 null
 */
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

/**
 * 将 JWT 令牌保存到 localStorage
 * @param token - 登录/注册成功后获取的访问令牌
 */
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

/**
 * 移除本地存储的 JWT 令牌，用于退出登录
 */
export function removeToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

/**
 * 通知应用 Token 已过期
 * 清除本地 Token 并派发全局事件，App 组件监听后跳转到登录页
 */
export function notifyTokenExpired(): void {
  removeToken();
  window.dispatchEvent(new CustomEvent(TOKEN_EXPIRED_EVENT));
}

// --- 请求封装 ---

/**
 * 通用请求封装函数
 * 自动附加 Content-Type 和 Authorization 头，统一处理错误响应。
 * 已导出，供 api/ 目录下的模块化 API 文件复用。
 *
 * @typeParam T - 响应数据的类型
 * @param path - API 路径（不含基础路径前缀，如 /auth/login）
 * @param options - fetch 请求选项
 * @returns 解析后的 JSON 响应数据
 * @throws 当响应状态码非 2xx 时抛出 Error，包含后端返回的 detail 信息
 */
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getToken();
  // 默认设置 Content-Type 为 JSON，并合并调用方传入的 headers
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };

  // 若本地存在令牌，自动附加到 Authorization 头
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  // 响应状态码非 2xx 时，尝试解析后端错误信息
  if (!response.ok) {
    // Token 过期或无效时，通知应用跳转到登录页
    if (response.status === 401) {
      notifyTokenExpired();
      throw new Error('登录已过期，请重新登录');
    }
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    // FastAPI 422 验证错误的 detail 是数组，需提取可读信息
    const detail = Array.isArray(error.detail)
      ? error.detail.map((e: { msg?: string; message?: string }) => e.msg || e.message || String(e)).join('; ')
      : (error.detail || `请求失败: ${response.status}`);
    throw new Error(detail);
  }

  // 204 No Content 无响应体，返回 undefined
  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
}

// --- 认证 API ---

/**
 * 用户注册
 * 注册成功后自动返回 JWT 令牌，无需再次登录。
 *
 * @param email - 用户邮箱
 * @param username - 用户名（2-50个字符）
 * @param password - 密码（至少6位）
 * @returns 包含访问令牌和用户信息的响应
 */
export async function register(email: string, username: string, password: string): Promise<TokenResponse> {
  return request<TokenResponse>('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, username, password }),
  });
}

/**
 * 用户登录
 * 使用邮箱和密码进行身份认证，成功后返回 JWT 令牌。
 *
 * @param email - 用户邮箱
 * @param password - 密码
 * @returns 包含访问令牌和用户信息的响应
 */
export async function login(email: string, password: string): Promise<TokenResponse> {
  return request<TokenResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
}

/**
 * 获取当前登录用户信息
 * 需要有效的 JWT 令牌，用于验证令牌是否仍然有效。
 *
 * @returns 当前用户信息
 */
export async function getMe(): Promise<User> {
  return request<User>('/auth/me');
}

// --- 笔记 API ---

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
 * 删除笔记
 * 删除操作不可恢复，调用前应进行用户确认。
 *
 * @param noteId - 笔记 ID
 */
export async function deleteNote(noteId: string): Promise<void> {
  return request<void>(`/notes/${noteId}`, { method: 'DELETE' });
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

// --- 上传 API ---

/**
 * 上传文件
 * 使用 FormData 方式上传，不经过通用 request 函数（因为 Content-Type 需为 multipart/form-data）。
 * 上传成功后，后端会创建一条笔记记录并开始异步转换处理。
 *
 * @param file - 要上传的文件对象
 * @param backend - 解析后端选择（可选），如 "pipeline"（本地）或 "vlm-http-client"（云端），
 *                  不传则使用后端默认配置
 * @returns 新创建的笔记记录（状态为 uploading）
 */
export async function uploadFile(file: File, backend?: string, noteRole?: string, linkedMaterialIds?: string[]): Promise<Note> {
  const token = getToken();
  const formData = new FormData();
  formData.append('file', file);
  if (backend) formData.append('backend', backend);
  if (noteRole) formData.append('note_role', noteRole);
  if (linkedMaterialIds && linkedMaterialIds.length > 0) {
    formData.append('linked_material_ids', JSON.stringify(linkedMaterialIds));
  }

  // 文件上传不设置 Content-Type，让浏览器自动设置 multipart/form-data 边界
  const response = await fetch(`${API_BASE}/upload`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });

  if (!response.ok) {
    // Token 过期或无效时，通知应用跳转到登录页
    if (response.status === 401) {
      notifyTokenExpired();
      throw new Error('登录已过期，请重新登录');
    }
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `上传失败: ${response.status}`);
  }

  return response.json();
}

/**
 * 获取上传/转换状态
 * 用于轮询检查文件上传后的异步处理进度。
 *
 * @param noteId - 笔记 ID
 * @returns 包含当前状态和可能的错误信息
 */
export async function getUploadStatus(noteId: string): Promise<{ id: string; status: string; error_message: string | null }> {
  return request(`/upload/${noteId}/status`);
}

/**
 * 重试转换失败的笔记
 * 仅对 status 为 failed 的笔记有效，会重新提交 Celery 转换任务。
 *
 * @param noteId - 笔记 ID
 * @returns 重试后的笔记状态
 */
export async function retryConvert(noteId: string): Promise<{ id: string; status: string; error_message: string | null }> {
  return request(`/upload/${noteId}/retry`, { method: 'POST' });
}

// --- 清洗 API ---

/**
 * 手动触发笔记清洗
 * 仅对 converted、cleaned 或 cleaning_failed 状态的笔记有效。
 *
 * @param noteId - 笔记 ID
 * @returns 清洗触发响应
 */
export async function startCleaning(noteId: string): Promise<CleaningStartResponse> {
  return request<CleaningStartResponse>(`/cleaning/${noteId}/start`, {
    method: 'POST',
  });
}

/**
 * 停止正在进行的清洗任务
 * 仅对 cleaning 状态的笔记有效，将状态更新为 cleaning_failed。
 *
 * @param noteId - 笔记 ID
 * @returns 停止清洗响应
 */
export async function stopCleaning(noteId: string): Promise<CleaningStopResponse> {
  return request<CleaningStopResponse>(`/cleaning/${noteId}/stop`, {
    method: 'POST',
  });
}

/**
 * 查询笔记清洗状态
 * 返回笔记的当前状态、清洗文件路径和元数据。
 *
 * @param noteId - 笔记 ID
 * @returns 清洗状态信息
 */
export async function getCleaningStatus(noteId: string): Promise<CleaningStatusResponse> {
  return request<CleaningStatusResponse>(`/cleaning/${noteId}/status`);
}

/**
 * 获取原始版与清洗版的 diff 数据
 * 返回结构化的行级差异数据，供前端渲染对比视图。
 *
 * @param noteId - 笔记 ID
 * @returns diff 数据
 */
export async function getCleaningDiff(noteId: string): Promise<CleaningDiffResponse> {
  return request<CleaningDiffResponse>(`/cleaning/${noteId}/diff`);
}

/**
 * 恢复被标记为重复的块
 * 移除指定块的 duplicate 注释标记，使其内容正常显示。
 *
 * @param noteId - 笔记 ID
 * @param blockIndex - 要恢复的块序号
 * @returns 操作结果
 */
export async function restoreBlock(noteId: string, blockIndex: number): Promise<BlockOperationResponse> {
  return request<BlockOperationResponse>(`/cleaning/${noteId}/restore/${blockIndex}`, {
    method: 'POST',
  });
}

/**
 * 彻底删除被标记为重复的块
 * 连同内容和注释标记一起删除，不可恢复。
 *
 * @param noteId - 笔记 ID
 * @param blockIndex - 要删除的块序号
 * @returns 操作结果
 */
export async function deleteBlock(noteId: string, blockIndex: number): Promise<BlockOperationResponse> {
  return request<BlockOperationResponse>(`/cleaning/${noteId}/block/${blockIndex}`, {
    method: 'DELETE',
  });
}

// --- 理解管道相关类型定义 ---

/** 知识卡片 */
export interface KnowledgeCard {
  id: string;
  user_id: string;
  note_id: string;
  note_title: string;
  card_type: string;
  title: string;
  content: string;
  summary: string | null;
  chapter_title: string | null;
  source_text: string | null;
  metadata_: Record<string, unknown> | null;
  card_category: 'regular' | 'blind_spot' | 'extension';
  is_key_point: boolean;
  is_difficulty: boolean;
  mastery_level: number;
  source_note_ids: string[] | null;
  parent_card_id: string | null;
  created_at: string;
  updated_at: string;
}

/** 知识卡片列表响应 */
export interface KnowledgeCardListResponse {
  items: KnowledgeCard[];
  total: number;
  page: number;
  page_size: number;
}

/** 题目 */
export interface QuizItem {
  id: string;
  user_id: string;
  card_id: string;
  note_id: string;
  note_title: string;
  question_type: string;
  difficulty: string;
  question: string;
  answer: string;
  options: string | null;
  explanation: string | null;
  metadata_: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

/** 题目列表响应 */
export interface QuizItemListResponse {
  items: QuizItem[];
  total: number;
  page: number;
  page_size: number;
}

/** 理解管道触发响应 */
export interface UnderstandingStartResponse {
  id: string;
  status: string;
  message: string;
}

/** 理解管道状态响应 */
export interface UnderstandingStatusResponse {
  id: string;
  status: string;
  error_message: string | null;
}

/** 章节摘要 */
export interface ChapterSummary {
  chapter_index: number;
  chapter_title: string;
  summary: string;
  card_count: number;
}

/** 问答请求 */
export interface QuestionRequest {
  question: string;
}

/** 问答引用来源 */
export interface AnswerSource {
  note_id: string;
  note_title: string;
  chapter_title: string | null;
  relevant_text: string;
}

/** 问答响应 */
export interface QuestionAnswerResponse {
  question: string;
  answer: string;
  sources: AnswerSource[];
  provider: string;
}

/** 题目生成响应 */
export interface GenerateQuestionsResponse {
  note_id: string;
  message: string;
  question_count: number;
}

// --- 理解管道 API ---

/**
 * 触发笔记理解管道
 */
export async function startUnderstanding(noteId: string): Promise<UnderstandingStartResponse> {
  return request<UnderstandingStartResponse>(`/understanding/${noteId}/start`, {
    method: 'POST',
  });
}

/**
 * 查询理解状态
 */
export async function getUnderstandingStatus(noteId: string): Promise<UnderstandingStatusResponse> {
  return request<UnderstandingStatusResponse>(`/understanding/${noteId}/status`);
}

/**
 * 获取章节摘要
 */
export async function getChapterSummaries(noteId: string): Promise<{ note_id: string; chapters: ChapterSummary[] }> {
  return request(`/understanding/${noteId}/chapters`);
}

/**
 * 获取知识卡片列表
 */
export async function getKnowledgeCards(page = 1, pageSize = 20, noteId?: string, keyword?: string): Promise<KnowledgeCardListResponse> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (noteId) params.set('note_id', noteId);
  if (keyword) params.set('keyword', keyword);
  return request<KnowledgeCardListResponse>(`/understanding/cards?${params}`);
}

/**
 * 获取知识卡片详情
 */
export async function getKnowledgeCard(cardId: string): Promise<KnowledgeCard> {
  return request<KnowledgeCard>(`/understanding/cards/${cardId}`);
}

/**
 * RAG 问答
 */
export async function askQuestion(question: string): Promise<QuestionAnswerResponse> {
  return request<QuestionAnswerResponse>('/understanding/ask', {
    method: 'POST',
    body: JSON.stringify({ question }),
  });
}

/**
 * 触发题目生成
 */
export async function generateQuestions(noteId: string): Promise<GenerateQuestionsResponse> {
  return request<GenerateQuestionsResponse>(`/understanding/${noteId}/generate-questions`, {
    method: 'POST',
  });
}

/**
 * 获取题目列表
 */
export async function getQuestions(page = 1, pageSize = 20, noteId?: string, keyword?: string): Promise<QuizItemListResponse> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (noteId) params.set('note_id', noteId);
  if (keyword) params.set('keyword', keyword);
  return request<QuizItemListResponse>(`/understanding/questions?${params}`);
}

/**
 * 获取笔记的卡片去重建议
 */
export async function getCardDuplicates(noteId: string): Promise<{ duplicates: Array<{ card_id: string; card_title: string; existing_card_id: string; existing_title: string; similarity: number }> }> {
  return request(`/understanding/${noteId}/duplicates`);
}

/**
 * 更新知识卡片
 */
export async function updateKnowledgeCard(cardId: string, data: { title?: string; content?: string }): Promise<KnowledgeCard> {
  return request<KnowledgeCard>(`/understanding/cards/${cardId}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

/**
 * 删除知识卡片
 */
export async function deleteKnowledgeCard(cardId: string): Promise<void> {
  return request<void>(`/understanding/cards/${cardId}`, {
    method: 'DELETE',
  });
}

// --- 复习调度相关类型定义 ---

/** 到期题目 */
export interface DueQuiz {
  id: string;
  card_id: string;
  note_id: string;
  question_type: string;
  difficulty: string;
  question: string;
  options: string | null;
  next_review_at: string | null;
  review_count: number;
  interval: number;
  easiness_factor: number;
}

/** 到期题目列表响应 */
export interface DueQuizListResponse {
  items: DueQuiz[];
  total: number;
}

/** SM-2 更新信息 */
export interface SM2Info {
  interval: number;
  repetition: number;
  easiness_factor: number;
  next_review_at: string;
}

/** 提交答案响应 */
export interface SubmitAnswerResponse {
  quiz_id: string;
  is_correct: boolean;
  quality: number;
  correct_answer: string;
  explanation: string | null;
  options: string[] | null;
  question_type: string;
  sm2: SM2Info;
}

/** 复习统计 */
export interface ReviewStats {
  due_count: number;
  today_done: number;
  today_correct: number;
  today_accuracy: number;
  total_reviews: number;
  total_correct: number;
  total_accuracy: number;
  total_quizzes: number;
}

/** 复习历史条目 */
export interface ReviewHistoryItem {
  id: string;
  quiz_id: string;
  note_id: string;
  user_answer: string;
  is_correct: boolean;
  quality: number;
  time_spent_ms: number;
  review_at: string | null;
}

/** 复习历史响应 */
export interface ReviewHistoryResponse {
  items: ReviewHistoryItem[];
  total: number;
  page: number;
  page_size: number;
}

// --- 学习报告相关类型定义 ---

/** 各题型正确率 */
export interface QuestionTypeAccuracy {
  question_type: string;
  total: number;
  correct: number;
  accuracy: number;
}

/** 今日学习报告 */
export interface DailyReport {
  date: string;
  new_mastered: number;
  total_review_time_ms: number;
  total_reviews: number;
  today_accuracy: number;
  weak_point_count: number;
  question_type_accuracy: QuestionTypeAccuracy[];
}

/** 单日趋势数据 */
export interface WeeklyTrendItem {
  date: string;
  review_count: number;
  correct_count: number;
  accuracy: number;
}

/** 7天趋势响应 */
export interface WeeklyTrendResponse {
  items: WeeklyTrendItem[];
  total_reviews: number;
  avg_accuracy: number;
}

/** 薄弱点条目 */
export interface WeakPoint {
  card_id: string;
  card_title: string;
  card_type: string;
  note_id: string;
  note_title: string;
  error_count: number;
  total_reviews: number;
  accuracy: number;
}

/** 薄弱点列表响应 */
export interface WeakPointsResponse {
  items: WeakPoint[];
  total: number;
}

// --- 快速复习相关类型定义 ---

/** 快速复习题目（复用 DueQuiz 类型） */
export type QuickQuiz = DueQuiz

/** 快速复习响应 */
export interface QuickReviewResponse {
  items: QuickQuiz[];
  total: number;
}

// --- 复习调度 API ---

/**
 * 获取今日到期复习题目
 */
export async function getDueQuizzes(limit = 50): Promise<DueQuizListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  return request<DueQuizListResponse>(`/review/due?${params}`);
}

/**
 * 提交答案
 */
export async function submitAnswer(quizId: string, userAnswer: string, timeSpentMs = 0): Promise<SubmitAnswerResponse> {
  return request<SubmitAnswerResponse>('/review/submit', {
    method: 'POST',
    body: JSON.stringify({ quiz_id: quizId, user_answer: userAnswer, time_spent_ms: timeSpentMs }),
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
 * @returns 提交答案响应
 */
export async function submitQuickReviewAnswer(noteId: string, quizId: string, userAnswer: string, timeSpentMs = 0): Promise<SubmitAnswerResponse> {
  return request<SubmitAnswerResponse>(`/review/quick/${noteId}/submit`, {
    method: 'POST',
    body: JSON.stringify({ quiz_id: quizId, user_answer: userAnswer, time_spent_ms: timeSpentMs }),
  });
}

// --- 学习报告 API ---

/**
 * 获取今日学习报告
 */
export async function getDailyReport(): Promise<DailyReport> {
  return request<DailyReport>('/report/daily');
}

/**
 * 获取7天复习趋势
 */
export async function getWeeklyTrend(): Promise<WeeklyTrendResponse> {
  return request<WeeklyTrendResponse>('/report/weekly-trend');
}

/**
 * 获取薄弱点列表
 */
export async function getWeakPoints(limit = 5): Promise<WeakPointsResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  return request<WeakPointsResponse>(`/report/weak-points?${params}`);
}

// --- 评估相关类型定义 ---

/** 评估结果 */
export interface AssessmentResult {
  /** 评估 ID */
  id: string;
  /** 评估模式：compare（笔记比对）或 quiz（开放性问题） */
  mode: 'compare' | 'quiz';
  /** 评分详情 */
  scores: Record<string, any>;
  /** 综合评分 */
  overall_score: number;
  /** 改进建议 */
  suggestions: string;
  /** 问题列表（quiz 模式） */
  quiz_questions?: Array<{ index: number; question: string; key_points: string[] }>;
  /** 答题结果列表（quiz 模式） */
  quiz_answers?: Array<Record<string, any>>;
  /** 创建时间（ISO 8601 格式） */
  created_at: string;
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

// --- 评估 API ---

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
  const body: Record<string, any> = { material_note_ids: materialNoteIds };
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

// --- 知识图谱相关类型定义 ---

/** 图谱节点，对应一张知识卡片 */
export interface GraphNode {
  /** 节点唯一标识（卡片 ID） */
  id: string;
  /** 卡片标题 */
  title: string;
  /** 卡片类型：concept / formula / qa / definition */
  card_type: string;
  /** 所属笔记 ID */
  note_id: string;
  /** 关联边数量 */
  relation_count: number;
}

/** 图谱边，对应卡片间的关系 */
export interface GraphEdge {
  /** 边唯一标识 */
  id: string;
  /** 起点节点 ID */
  source: string;
  /** 终点节点 ID */
  target: string;
  /** 关系类型：related / prerequisite / subsequent / contrast */
  relation_type: string;
  /** 边状态：suggested / confirmed */
  status: string;
  /** 相似度分数 */
  similarity_score: number | null;
}

/** 图谱数据，包含节点和边 */
export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/** 建议关系 */
export interface SuggestedRelation {
  /** 建议关系 ID */
  id: string;
  /** 卡片 1 ID */
  card_id_1: string;
  /** 卡片 2 ID */
  card_id_2: string;
  /** 卡片 1 标题 */
  card_1_title: string;
  /** 卡片 2 标题 */
  card_2_title: string;
  /** 相似度分数 */
  similarity_score: number;
}

// --- 知识图谱 API ---

/**
 * 获取知识图谱数据
 * 返回所有节点和边，用于力导向图可视化。
 *
 * @returns 图谱数据
 */
export async function getGraphData(): Promise<GraphData> {
  return request<GraphData>('/graph');
}

/**
 * 获取建议关系列表
 * 返回系统自动检测到的潜在关联，供用户确认或拒绝。
 *
 * @returns 建议关系列表
 */
export async function getSuggestions(): Promise<{ items: SuggestedRelation[] }> {
  return request<{ items: SuggestedRelation[] }>('/graph/suggestions');
}

/**
 * 确认建议关系
 * 将 suggested 状态的边转为 confirmed。
 *
 * @param relationId - 建议关系 ID
 */
export async function confirmRelation(relationId: string): Promise<any> {
  return request('/graph/confirm', {
    method: 'POST',
    body: JSON.stringify({ relation_id: relationId }),
  });
}

/**
 * 拒绝建议关系
 * 删除 suggested 状态的边。
 *
 * @param relationId - 建议关系 ID
 */
export async function rejectRelation(relationId: string): Promise<any> {
  return request('/graph/reject', {
    method: 'POST',
    body: JSON.stringify({ relation_id: relationId }),
  });
}

/**
 * 创建卡片间关系
 * 手动在两张卡片之间建立指定类型的关联。
 *
 * @param cardId1 - 卡片 1 ID
 * @param cardId2 - 卡片 2 ID
 * @param relationType - 关系类型
 */
export async function createRelation(cardId1: string, cardId2: string, relationType: string): Promise<any> {
  return request('/graph/relation', {
    method: 'POST',
    body: JSON.stringify({ card_id_1: cardId1, card_id_2: cardId2, relation_type: relationType }),
  });
}

/**
 * 删除关系
 * 删除已确认的关系边。
 *
 * @param relationId - 关系 ID
 */
export async function deleteRelation(relationId: string): Promise<any> {
  return request(`/graph/relation/${relationId}`, {
    method: 'DELETE',
  });
}

// --- 文件夹相关类型定义 ---

/** 文件夹内的笔记概要 */
export interface NoteInFolder {
  /** 笔记 ID */
  id: string;
  /** 笔记标题 */
  title: string;
  /** 来源类型 */
  source_type: string;
  /** 处理状态 */
  status: string;
  /** 文件大小（字节） */
  file_size: number;
  /** 创建时间（ISO 8601 格式） */
  created_at: string;
}

/** 文件夹信息 */
export interface Folder {
  /** 文件夹 ID */
  id: string;
  /** 所属用户 ID */
  user_id: string;
  /** 文件夹名称 */
  name: string;
  /** 文件夹描述 */
  description: string | null;
  /** 文件夹日期（ISO 8601 格式） */
  folder_date: string;
  /** 创建时间（ISO 8601 格式） */
  created_at: string;
  /** 文件夹内笔记数量 */
  note_count: number;
}

/** 文件夹详情，包含笔记列表 */
export interface FolderDetail extends Folder {
  /** 文件夹内的笔记列表 */
  notes: NoteInFolder[];
}

// --- 文件夹 API ---

/**
 * 创建文件夹
 *
 * @param name - 文件夹名称
 * @param description - 文件夹描述（可选）
 * @param folderDate - 文件夹日期，ISO 格式如 "2024-01-15"（可选，默认今天）
 * @returns 新创建的文件夹信息
 */
export async function createFolder(name: string, description?: string, folderDate?: string): Promise<Folder> {
  return request<Folder>('/folders', {
    method: 'POST',
    body: JSON.stringify({ name, description, folder_date: folderDate }),
  });
}

/**
 * 获取文件夹列表
 *
 * @param days - 查询最近多少天的文件夹，默认 7 天
 * @returns 文件夹列表
 */
export async function getFolders(days = 7): Promise<Folder[]> {
  const params = new URLSearchParams({ days: String(days) });
  return request<Folder[]>(`/folders?${params}`);
}

/**
 * 获取文件夹详情（包含笔记列表）
 *
 * @param folderId - 文件夹 ID
 * @returns 文件夹详情
 */
export async function getFolderDetail(folderId: string): Promise<FolderDetail> {
  return request<FolderDetail>(`/folders/${folderId}`);
}

/**
 * 更新文件夹信息（当前用于重命名）
 *
 * @param folderId - 文件夹 ID
 * @param name - 新文件夹名称
 * @returns 更新后的文件夹信息
 */
export async function updateFolder(folderId: string, name: string): Promise<Folder> {
  return request<Folder>(`/folders/${folderId}`, {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  });
}

/**
 * 删除文件夹（仅允许删除空文件夹）
 *
 * @param folderId - 文件夹 ID
 * @returns 操作结果
 */
export async function deleteFolder(folderId: string): Promise<{ message: string }> {
  return request<{ message: string }>(`/folders/${folderId}`, {
    method: 'DELETE',
  });
}

/**
 * 上传文件到指定文件夹
 * 在原有 uploadFile 基础上增加 folder_id 参数。
 *
 * @param file - 要上传的文件对象
 * @param folderId - 目标文件夹 ID
 * @param backend - 解析后端选择（可选）
 * @returns 新创建的笔记记录
 */
export async function uploadFileToFolder(file: File, folderId: string, backend?: string): Promise<Note> {
  const token = getToken();
  const formData = new FormData();
  formData.append('file', file);
  formData.append('folder_id', folderId);
  if (backend) formData.append('backend', backend);

  const response = await fetch(`${API_BASE}/upload`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: formData,
  });

  if (!response.ok) {
    if (response.status === 401) {
      notifyTokenExpired();
      throw new Error('登录已过期，请重新登录');
    }
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || `上传失败: ${response.status}`);
  }

  return response.json();
}
