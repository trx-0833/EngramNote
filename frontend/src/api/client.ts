/**
 * @file API 请求客户端
 * @description 封装了与后端 API 交互的所有方法，包括认证、笔记管理和文件上传。
 * 采用原生 fetch 实现，不引入 axios 等第三方库，保持最小依赖。
 * 所有 API 请求均以 /api 为基础路径，通过 Bearer Token 进行身份认证。
 */

/** API 基础路径，所有请求都会在此路径前缀下发起 */
const API_BASE = '/api';

// --- 通用类型定义 ---

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
  /** 所属项目标签 ID 数组（多对多） */
  project_ids?: string[];
  /** 所属项目标签名称数组（多对多） */
  project_names?: string[];
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
  /** 移入回收站的时间（ISO 8601 格式），null 表示未删除 */
  trashed_at: string | null;
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
/** 请求超时（毫秒）。普通请求限时，避免网络挂起时永久 pending，见 docs/decisions.md#F-22 */
const REQUEST_TIMEOUT_MS = 30000;

/** 上传类请求专用超时（毫秒）。文件上传/两阶段上传耗时远超普通接口，独立于 REQUEST_TIMEOUT_MS */
const UPLOAD_TIMEOUT_MS = 600000;

/** 仅凭据提交接口的 401 不应触发全局登出（错误密码≠令牌过期）。
 *  注意：/auth/me 的 401 是令牌失效信号，必须触发登出，见 docs/decisions.md#F-22。 */
function isAuthCredentialPath(path: string): boolean {
  return path === '/auth/login' || path === '/auth/register';
}

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

  // AbortController 超时，避免网络挂起时请求永久 pending，见 docs/decisions.md#F-22
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
      signal: options.signal ?? controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      // ES2020 lib 下 Error 无 cause 属性，用自定义扩展类型附加
      const timeoutError = new Error('请求超时，请检查网络后重试');
      (timeoutError as Error & { cause?: unknown }).cause = err;
      throw timeoutError;
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }

  // 响应状态码非 2xx 时，尝试解析后端错误信息
  if (!response.ok) {
    // Token 过期或无效时，通知应用跳转到登录页（仅非凭据接口触发登出，见 docs/decisions.md#F-22）
    if (response.status === 401) {
      if (!isAuthCredentialPath(path)) {
        notifyTokenExpired();
      }
      throw new Error(response.status === 401 && isAuthCredentialPath(path)
        ? '邮箱或密码错误'
        : '登录已过期，请重新登录');
    }
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    // FastAPI 422 验证错误的 detail 是数组，需提取可读信息
    const detail = Array.isArray(error.detail)
      ? error.detail.map((e: { msg?: string; message?: string }) => e.msg || e.message || String(e)).join('; ')
      : (error.detail || `请求失败: ${response.status}`);
    // 错误响应统一为 {detail, error_code, request_id}：优先识别稳定的 error_code
    // （存在时前置，供上层按错误码分流/定位），缺失时回退中文 detail 文案兜底。
    const errorCode = typeof error.error_code === 'string' && error.error_code ? error.error_code : null;
    throw new Error(errorCode ? `${errorCode}: ${detail}` : detail);
  }

  // 204 No Content 无响应体，返回 undefined
  if (response.status === 204) {
    return undefined as T;
  }

  // 兼容 200 空响应体（部分 DELETE/操作接口返回 200 但无 body），见 docs/decisions.md#F-22
  const text = await response.text();
  if (!text) {
    return undefined as T;
  }
  try {
    return JSON.parse(text) as T;
  } catch {
    return text as unknown as T;
  }
}

/**
 * 通用 multipart/form-data 上传请求（合并多处重复的 FormData fetch 分支），
 * 401 处理与 request() 一致，见 docs/decisions.md#F-22。
 */
export async function uploadRequest<T>(path: string, formData: FormData): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPLOAD_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers,
      body: formData,
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      const timeoutError = new Error('上传超时，请检查网络后重试');
      (timeoutError as Error & { cause?: unknown }).cause = err;
      throw timeoutError;
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }

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

  const text = await response.text();
  if (!text) {
    return undefined as T;
  }
  try {
    return JSON.parse(text) as T;
  } catch {
    return text as unknown as T;
  }
}

// --- 流式问答 API ---

/**
 * 流式问答 SSE 流
 * 返回一个 ReadableStream，调用方需自行解析 SSE 事件：
 * - event: meta / data: {"retrieval_status":"...","provider":"..."}
 * - event: token / data: {"content":"..."}
 * - event: sources / data: {"sources":[...],"provider":"..."}
 * - event: done / data: {}
 * - event: error / data: {"message":"..."}
 */
export async function askQuestionStream(question: string, signal?: AbortSignal): Promise<ReadableStream<Uint8Array>> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    'Accept': 'text/event-stream',
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const response = await fetch(`${API_BASE}/understanding/ask/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ question }),
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

// --- 模块化 API re-export（按域拆分，各页面 import 路径保持不变） ---

export * from './auth'
export * from './notes'
export * from './upload'
export * from './cleaning'
export * from './qa'
export * from './review'
export * from './report'
export * from './assessment'
export * from './graph'
export * from './projects'
export * from './goals'