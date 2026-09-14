/**
 * @file API 请求客户端
 * @description 封装了与后端 API 交互的所有方法，包括认证、笔记管理和文件上传。
 * 采用原生 fetch 实现，不引入 axios 等第三方库，保持最小依赖。
 * 所有 API 请求均以 /api 为基础路径，通过 Bearer Token 进行身份认证。
 */

import type { Schema } from './generated/types';

/** API 基础路径，所有请求都会在此路径前缀下发起 */
export const API_BASE = '/api';

// --- 通用类型定义（阶段 5.1 / S2：来源已改为 OpenAPI 生成类型）---
//
// 以下类型全部是 `src/api/generated/types.ts` 里生成 schema 的**别名**。
// **导出名一个都没变**，所以 74 个调用方文件、79 处 import 与 10 个测试文件里的
// 13 处 `vi.mock('…/api/…')` 都不需要改 —— 这正是 S2 选"换类型不换函数"的理由。
//
// ⚠️ 生成类型整体比手写类型**更严**：pydantic v2 把带默认值的字段排除出
// OpenAPI 的 `required`，而 openapi-typescript v7 又按 `default` 把它们标回必填。
// 对响应模型来说这是**对的**（有默认值的字段一定会被序列化出来），代价是
// "少给字段"的构造点（测试里的 mock 工厂）会先编译失败 —— 那正是要暴露的东西。

/** 用户信息（生成自 `UserResponse`） */
export type User = Schema<'UserResponse'>;

/**
 * 认证令牌响应，登录/注册/刷新成功后返回（生成自 `TokenResponse`）
 *
 * 其中 `refresh_token` 是**必须**与访问令牌一起保存的：只存访问令牌等于
 * 丢掉会话的撤销能力（阶段 6.3）。
 */
export type TokenResponse = Schema<'TokenResponse'>;

/**
 * 笔记概要信息，用于列表展示（生成自 `NoteResponse`）
 *
 * ⚠️ 与手写版本相比的三处口径变化（都是**变准**，不是变宽）：
 *
 * - `status` / `source_type` 现在是枚举联合（`NoteStatus` 10 个值 /
 *   `SourceType` 8 个值），不再是 `string` —— 此前 `client.ts` 的注释只写了
 *   7 个状态、7 种来源，漏掉的 `cleaning_failed` / `learning_failed` / `failed`
 *   与 `markdown` 没有任何机制能发现，现在由类型兜住；
 * - `note_role` / `project_ids` / `project_names` 带默认值，生成类型里是**必填**
 *   （后端一定会把它们序列化出来）；
 * - `page_count` / `error_message` / `trashed_at` 这类 `anyOf[T, null]` 且无默认值的
 *   字段生成的是 `?: T | null`，读的时候要用 `x != null` 而不是 `x !== null`。
 */
export type Note = Schema<'NoteResponse'>;

/**
 * 笔记详情，在 Note 基础上增加了 Markdown 内容和元数据
 * （生成自 `NoteDetailResponse`）
 */
export type NoteDetail = Schema<'NoteDetailResponse'>;

/** 笔记列表分页响应（生成自 `NoteListResponse`） */
export type NoteListResponse = Schema<'NoteListResponse'>;

// --- Token 管理 ---

/** localStorage 中存储访问令牌的键名 */
const TOKEN_KEY = 'engramnote_token';

/**
 * localStorage 中存储**刷新令牌**的键名（阶段 6.3）
 *
 * 为什么必须单独持久化：访问令牌是无状态的、无法吊销，会话的"可撤销性"
 * 完全落在刷新令牌上（服务端有对应记录，可轮换、可撤销）。只存访问令牌
 * 等于把撤销能力丢掉 —— 刷新失败时也就无从"干净地结束会话"。
 */
const REFRESH_TOKEN_KEY = 'engramnote_refresh_token';

/** Token 过期事件名称，用于通知 App 组件跳转到登录页 */
export const TOKEN_EXPIRED_EVENT = 'token-expired';

/**
 * 获取本地存储的访问令牌
 * @returns 令牌字符串，若未登录则返回 null
 */
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

/**
 * 获取本地存储的刷新令牌（阶段 6.3）
 * @returns 刷新令牌字符串；未登录或旧版本会话（只存了访问令牌）时为 null
 */
export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY);
}

/**
 * 保存一对令牌（阶段 6.3）
 *
 * `refreshToken` 为空时**清掉**已存的刷新令牌，而不是保留旧的：
 * 保留会让"这一对"与"上一次会话的残留"混在一起，随后的刷新会用一枚
 * 属于旧会话（可能已被撤销）的令牌去换新令牌，直接触发服务端的重放检测。
 *
 * @param accessToken - 访问令牌
 * @param refreshToken - 刷新令牌；缺省/空表示本次响应没有下发
 */
export function setTokens(accessToken: string, refreshToken?: string | null): void {
  localStorage.setItem(TOKEN_KEY, accessToken);
  if (refreshToken) {
    localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
  } else {
    localStorage.removeItem(REFRESH_TOKEN_KEY);
  }
}

/**
 * 清除本地存储的全部令牌（登出、刷新失败时调用）
 *
 * 两个键必须一起清：只清访问令牌会留下一个"看起来还有会话"的刷新令牌，
 * 下一次 401 又拿它去刷新，用户会看到一个语义不明的中间态。
 */
export function clearTokens(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
}

/**
 * 通知应用 Token 已过期
 * 清除本地令牌（访问 + 刷新）并派发全局事件，App 组件监听后跳转到登录页
 */
export function notifyTokenExpired(): void {
  clearTokens();
  window.dispatchEvent(new CustomEvent(TOKEN_EXPIRED_EVENT));
}

// --- 请求封装 ---

/** 请求超时（毫秒）。普通请求限时，避免网络挂起时永久 pending，见 docs/decisions.md#F-22 */
const REQUEST_TIMEOUT_MS = 30000;

/** 上传类请求专用超时（毫秒）。文件上传/两阶段上传耗时远超普通接口，独立于 REQUEST_TIMEOUT_MS */
const UPLOAD_TIMEOUT_MS = 600000;

/** 刷新令牌换新令牌对的端点 */
const REFRESH_PATH = '/auth/refresh';

/** 仅凭据提交接口的 401 不应触发全局登出（错误密码≠令牌过期）。
 *  注意：/auth/me 的 401 是令牌失效信号，必须触发登出，见 docs/decisions.md#F-22。 */
function isAuthCredentialPath(path: string): boolean {
  return path === '/auth/login' || path === '/auth/register';
}

/**
 * 该路径在收到 401 时是否应"先刷新再重试"
 *
 * 排除两类：
 * - 登录/注册：它们的 401 是"邮箱或密码错误"，与令牌无关；
 * - `/auth/refresh` 自身：刷新失败就是失败，不能再触发一次刷新（递归）。
 *   即便 `refreshSession` 走的是裸 fetch，这条判据仍要留着 ——
 *   否则将来有人把刷新改回走 `request()` 时会出现嵌套刷新。
 */
function canAttemptRefresh(path: string): boolean {
  return !isAuthCredentialPath(path) && path !== REFRESH_PATH;
}

/**
 * 发起一次带认证头的 fetch（**不含**刷新逻辑，供重试复用）
 *
 * @param path - 相对 API_BASE 的路径
 * @param init - fetch 参数（headers 会与 Authorization 合并）
 * @param timeoutMs - 超时毫秒数；undefined 表示不设超时（流式响应由调用方的 signal 控制）
 * @param timeoutMessage - 超时文案（上传与普通请求的措辞不同）
 */
async function doFetch(
  path: string,
  init: RequestInit,
  timeoutMs?: number,
  timeoutMessage = '请求超时，请检查网络后重试',
): Promise<Response> {
  const headers: Record<string, string> = {
    ...((init.headers as Record<string, string> | undefined) ?? {}),
  };
  const token = getToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  // 调用方自带 signal 时不再叠加超时计时器：请求生命周期由调用方决定
  // （原实现在这种情况下也会建一个 AbortController，但它的 signal 从未被使用，
  //  等于留了一个到点就空转的定时器）
  const controller = timeoutMs !== undefined && !init.signal ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    return await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      signal: init.signal ?? controller?.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      // ES2020 lib 下 Error 无 cause 属性，用自定义扩展类型附加
      const timeoutError = new Error(timeoutMessage);
      (timeoutError as Error & { cause?: unknown }).cause = err;
      throw timeoutError;
    }
    throw err;
  } finally {
    if (timer !== null) {
      clearTimeout(timer);
    }
  }
}

/** 正在进行中的刷新请求（单飞，见 refreshSession 的说明） */
let refreshInFlight: Promise<boolean> | null = null;

/**
 * 用本地刷新令牌换一对新令牌（阶段 6.3）
 *
 * ## 两个必须遵守的约束
 *
 * 1. **永不抛出**：返回 true/false。刷新失败的处理是"清本地状态回登录页"
 *    （由调用方 `notifyTokenExpired` 完成），而不是把一个新的异常类型
 *    抛给每个调用点去分辨"这是网络错误还是令牌失效"。
 * 2. **单飞（同一时刻只有一个刷新在飞）**：这是**轮换**带来的硬约束 ——
 *    服务端每次刷新都会把提交的那枚令牌标记为已撤销，若两个并发请求各刷一次，
 *    后一次提交的就是刚被撤销的令牌，服务端会判定为**重放（令牌被盗）**
 *    并撤销整条链，用户直接被登出。因此并发 401 必须共享同一次刷新请求。
 *
 * 刻意不走 `request()`：`request()` 在 401 时会尝试刷新，形成递归。
 */
export function refreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = performRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

/** 真正执行一次刷新（调用方应通过 refreshSession 保证单飞） */
async function performRefresh(): Promise<boolean> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    // 没有刷新令牌（未登录，或改造前留下的旧会话）：不发起无意义的请求
    return false;
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(`${API_BASE}${REFRESH_PATH}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
      signal: controller.signal,
    });
    if (!response.ok) {
      return false;
    }
    const data = (await response.json()) as Partial<TokenResponse>;
    if (!data?.access_token || !data?.refresh_token) {
      // 响应缺字段时按失败处理：半个令牌对比"没刷新"更危险
      // （访问令牌换了、刷新令牌没换 → 下一次刷新必然用已撤销的令牌）
      return false;
    }
    setTokens(data.access_token, data.refresh_token);
    return true;
  } catch {
    // 网络错误/超时同样按刷新失败处理
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 带认证的 fetch：401 时**刷新一次并重放原请求一次**（阶段 6.3）
 *
 * 重试严格只有一次：刷新成功后的第二次响应无论是什么（包括再次 401）都
 * 直接交给调用方 —— 那条路径会走 `notifyTokenExpired()`，绝不会形成循环。
 */
export async function authorizedFetch(
  path: string,
  init: RequestInit,
  timeoutMs?: number,
  timeoutMessage?: string,
): Promise<Response> {
  let response = await doFetch(path, init, timeoutMs, timeoutMessage);
  if (response.status === 401 && canAttemptRefresh(path)) {
    const refreshed = await refreshSession();
    if (refreshed) {
      response = await doFetch(path, init, timeoutMs, timeoutMessage);
    }
  }
  return response;
}

/**
 * 带错误码的 API 错误（阶段 0.11：统一错误契约）
 *
 * 后端错误响应统一为 `{detail, error_code, request_id}`（见后端
 * `middleware/error_handler.py`）。其中 `error_code` 是**稳定**的机器可读标识，
 * `detail` 是面向用户的中文文案 —— 文案会随措辞调整，错误码不会。
 *
 * ## 为什么要有这个类
 *
 * 改造前 `error_code` 只被拼进 message 字符串（形如 `"HTTP_404: 笔记不存在"`），
 * 调用方拿不到结构化的码，想按错误类型分流就只能对**中文文案**做匹配
 * （`message.includes('每日上限')`）—— 后端把文案改一个字，前端分支就静默
 * 失效，而失效的样子是"页面不跳转"而不是报错（overhaul-plan 的 F-19 记的
 * 正是这个形态）。`code` 字段让分流依据与文案解耦。
 *
 * ## 为什么 message 的构造一个字都没改
 *
 * `message` 仍是 `${error_code}: ${detail}`（没有 error_code 时就是 detail）：
 * 展示层看到的内容不变，本次改造**只新增**结构化的 `code`。
 */
export class ApiError extends Error {
  /** 后端 error_code；响应里没有该字段时为 null（如客户端本地生成的 401 文案） */
  readonly code: string | null

  constructor(message: string, code: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.code = code
  }
}

/**
 * 通用请求封装函数
 * 自动附加 Content-Type 和 Authorization 头，统一处理错误响应。
 * 已导出，供 api/ 目录下的模块化 API 文件复用。
 *
 * 访问令牌过期（401）时先尝试刷新一次并重放原请求一次；刷新失败则清除
 * 全部本地令牌并派发 token 过期事件（App 会回到登录页）。
 *
 * @typeParam T - 响应数据的类型
 * @param path - API 路径（不含基础路径前缀，如 /auth/login）
 * @param options - fetch 请求选项
 * @returns 解析后的 JSON 响应数据
 * @throws {ApiError} 当响应状态码非 2xx 时抛出，message 含后端 detail，
 *   `code` 为后端 error_code（调用方应按 code 分流，不要匹配文案）
 */
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  // 默认设置 Content-Type 为 JSON，并合并调用方传入的 headers
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };
  const response = await authorizedFetch(
    path,
    { ...options, headers },
    REQUEST_TIMEOUT_MS,
  );

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
    throw new ApiError(errorCode ? `${errorCode}: ${detail}` : detail, errorCode);
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
 * 401 处理与 request() 一致（含"刷新一次 + 重放一次"），见 docs/decisions.md#F-22。
 */
export async function uploadRequest<T>(path: string, formData: FormData): Promise<T> {
  const response = await authorizedFetch(
    path,
    { method: 'POST', body: formData },
    UPLOAD_TIMEOUT_MS,
    '上传超时，请检查网络后重试',
  );

  if (!response.ok) {
    if (response.status === 401) {
      notifyTokenExpired();
      throw new ApiError('登录已过期，请重新登录');
    }
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    const detail = Array.isArray(error.detail)
      ? error.detail.map((e: { msg?: string; message?: string }) => e.msg || e.message || String(e)).join('; ')
      : (error.detail || `请求失败: ${response.status}`);
    // message 仍是 detail（与改造前逐字一致），只是额外带上结构化的 code
    throw new ApiError(detail, typeof error.error_code === 'string' ? error.error_code : null);
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
  // 不设超时：流式响应可能持续很久，生命周期由调用方的 signal 控制
  const response = await authorizedFetch('/understanding/ask/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'text/event-stream',
    },
    body: JSON.stringify({ question }),
    signal,
  });
  if (!response.ok) {
    if (response.status === 401) {
      notifyTokenExpired();
      throw new ApiError('登录已过期，请重新登录');
    }
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    const detail = Array.isArray(error.detail)
      ? error.detail.map((e: { msg?: string; message?: string }) => e.msg || e.message || String(e)).join('; ')
      : (error.detail || `请求失败: ${response.status}`);
    throw new ApiError(detail, typeof error.error_code === 'string' ? error.error_code : null);
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