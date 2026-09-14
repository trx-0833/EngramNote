/**
 * @file 文件夹与项目 API
 * @description 文件夹（按日期组织资料）、项目标签与 source/ 目录扫描导入。
 */
import { request } from './client';
import type { BodyOf, Schema } from './generated/types';

// --- 文件夹/项目相关类型（阶段 5.1 / S2：来源已改为 OpenAPI 生成类型）---

/**
 * 文件夹内的笔记概要（生成自 `NoteInFolder`）
 *
 * ⚠️ `source_type` / `status` 现在是枚举；`file_size` **在这个模型里是真的**
 * （它在后端 `required` 里）。别拿它去描述项目详情的笔记条目 —— 见下面
 * `ProjectDetail` 的说明。
 */
export type NoteInFolder = Schema<'NoteInFolder'>;

/** 文件夹信息（生成自 `FolderResponse`） */
export type Folder = Schema<'FolderResponse'>;

/** 文件夹详情，包含笔记列表（生成自 `FolderDetailResponse`） */
export type FolderDetail = Schema<'FolderDetailResponse'>;

/** 项目信息（纯标签归属，不再作为 Vault 目录；生成自 `ProjectResponse`） */
export type Project = Schema<'ProjectResponse'>;

/**
 * 项目详情，包含项目下的笔记列表（生成自 `ProjectDetailResponse`）
 *
 * ★ **这里修掉了 §7.4 那处"一个前端类型描述两个后端模型"**：
 *
 * - `GET /api/folders/{id}` → `FolderDetailResponse.notes[]` 用 `NoteInFolder`（**有** `file_size`）；
 * - `GET /api/projects/{id}` → `ProjectDetailResponse.notes[]` 用 `NoteSummary`（**没有** `file_size`）。
 *
 * 而前端两处都声明成 `NoteInFolder`（`file_size: number` 必填）——
 * 也就是说"项目详情里每篇笔记一定有 file_size"是一条**后端从未保证过**的假设。
 * 切换后这一处由生成类型直接钉住，不需要改动任何调用方
 * （`Projects.test.tsx` 里已有一条"缺 file_size 时照常渲染"的用例，
 * 写测试的人早就知道那个类型在撒谎）。
 */
export type ProjectDetail = Schema<'ProjectDetailResponse'>;

/**
 * 创建文件夹
 *
 * @param name - 文件夹名称
 * @param description - 文件夹描述（可选）
 * @param folderDate - 文件夹日期，ISO 格式如 "2024-01-15"（可选，默认今天）
 * @returns 新创建的文件夹信息
 */
export async function createFolder(
  name: string,
  description?: string,
  folderDate?: string,
): Promise<Folder> {
  // 请求体由契约派生：`POST /api/folders`（`FolderCreate`，required 只有 `name`）
  const body: BodyOf<'/folders', 'post'> = { name, description, folder_date: folderDate };
  return request<Folder>('/folders', {
    method: 'POST',
    body: JSON.stringify(body),
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
  // 请求体由契约派生：`PATCH /api/folders/{folder_id}`（`FolderUpdate` 只有 `name` 一个字段）
  const body: BodyOf<'/folders/{folder_id}', 'patch'> = { name };
  return request<Folder>(`/folders/${folderId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

/**
 * 删除文件夹（仅允许删除空文件夹）
 *
 * @param folderId - 文件夹 ID
 * @returns 操作结果
 */
export async function deleteFolder(folderId: string): Promise<MessageResponse> {
  return request<MessageResponse>(`/folders/${folderId}`, {
    method: 'DELETE',
  });
}

/** 只带一句人类可读操作结果的响应（生成自 `MessageResponse`，folder/project 删除共用） */
export type MessageResponse = Schema<'MessageResponse'>;

/** 批量给笔记打项目标签的结果（生成自 `ProjectNotesAddedResponse`，比手写版多一个 `project_id`） */
export type ProjectNotesAddedResponse = Schema<'ProjectNotesAddedResponse'>;

/** 把笔记移出项目的结果（生成自 `ProjectNoteRemovedResponse`，比手写版多一个 `note_id`） */
export type ProjectNoteRemovedResponse = Schema<'ProjectNoteRemovedResponse'>;

/**
 * 创建项目
 *
 * @param name - 项目名称
 * @param description - 项目描述（可选）
 * @returns 新创建的项目信息
 */
export async function createProject(name: string, description?: string): Promise<Project> {
  // 请求体由契约派生：`POST /api/projects`（`ProjectCreate`，required 只有 `name`）
  const body: BodyOf<'/projects', 'post'> = { name, description };
  return request<Project>('/projects', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/**
 * 获取项目列表
 *
 * @returns 项目列表（含笔记数量）
 */
export async function getProjects(): Promise<Project[]> {
  return request<Project[]>('/projects');
}

/**
 * 获取项目详情（包含笔记列表）
 *
 * @param projectId - 项目 ID
 * @returns 项目详情
 */
export async function getProjectDetail(projectId: string): Promise<ProjectDetail> {
  return request<ProjectDetail>(`/projects/${projectId}`);
}

/**
 * 更新项目（标签化后名称/描述可改，不影响物理路径）
 *
 * @param projectId - 项目 ID
 * @param name - 新项目名称
 * @param description - 新项目描述
 * @returns 更新后的项目信息
 *
 * ⚠️ **"清空描述"当前做不到**（只报告，未改行为）：契约里 `description` 可空，
 * 但 `description: undefined` 会被 `JSON.stringify` 丢掉，而显式 `null` 与"不传"
 * 在后端是同一个值 —— `services/project_service.py` 的
 * `if description is not None` 把两者都当成"不变更"。
 * 详见调用侧 `pages/projects/useProjects.ts` 的 `edit.description.trim() || undefined`。
 */
export async function updateProject(
  projectId: string,
  name?: string,
  description?: string,
): Promise<Project> {
  // 请求体由契约派生：`PATCH /api/projects/{project_id}`（`ProjectUpdate`：`name` / `description` 均可空）
  const body: BodyOf<'/projects/{project_id}', 'patch'> = { name, description };
  return request<Project>(`/projects/${projectId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

/**
 * 删除项目（只删标签，笔记与文件保留）
 *
 * @param projectId - 项目 ID
 * @returns 操作结果
 */
export async function deleteProject(projectId: string): Promise<MessageResponse> {
  return request<MessageResponse>(`/projects/${projectId}`, {
    method: 'DELETE',
  });
}

// --- 项目扫描导入（阶段 5.1 / S2：来源已改为 OpenAPI 生成类型）---

/** 扫描导入的单条新笔记信息（生成自 `ScanImportDetail`） */
export type ScanImportDetail = Schema<'ScanImportDetail'>;

/** 被跳过的文件信息（生成自 `ScanSkipDetail`） */
export type ScanSkipDetail = Schema<'ScanSkipDetail'>;

/** 扫描 source/ 目录并导入新文件的响应（生成自 `ScanImportResponse`） */
export type ScanImportResponse = Schema<'ScanImportResponse'>;

/**
 * 扫描导入：将手动放入项目 source/ 目录的新文件识别为笔记
 *
 * @param projectId - 项目 ID
 * @returns 扫描结果统计
 */
export async function scanProject(projectId: string): Promise<ScanImportResponse> {
  return request<ScanImportResponse>(`/projects/${projectId}/scan`, {
    method: 'POST',
  });
}

/**
 * 将笔记批量添加到项目
 *
 * @param projectId - 目标项目 ID
 * @param noteIds - 要添加的笔记 ID 列表
 * @returns 添加结果统计（`project_id` + `added` + `not_found`）
 *
 * ⚠️ `not_found` 的语义是**差集**（请求 N 个、实际新增 M 个 → N-M），
 * 它同时包含"笔记不存在/在回收站"与"本来就已经打了这个标签"两种情况 ——
 * 读这个字段的人会自然以为是前者。
 */
export async function addNotesToProject(
  projectId: string,
  noteIds: string[],
): Promise<ProjectNotesAddedResponse> {
  // 请求体由契约派生：`POST /api/projects/{project_id}/notes`（`ProjectNotesAddRequest`：`note_ids` 可选）
  const body: BodyOf<'/projects/{project_id}/notes', 'post'> = { note_ids: noteIds };
  return request<ProjectNotesAddedResponse>(`/projects/${projectId}/notes`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/**
 * 将笔记从项目中移出
 *
 * @param projectId - 项目 ID
 * @param noteId - 要移出的笔记 ID
 * @returns 操作结果（`message` + `note_id`）
 */
export async function removeNoteFromProject(
  projectId: string,
  noteId: string,
): Promise<ProjectNoteRemovedResponse> {
  return request<ProjectNoteRemovedResponse>(`/projects/${projectId}/notes/${noteId}`, {
    method: 'DELETE',
  });
}
