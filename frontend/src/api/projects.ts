/**
 * @file 文件夹与项目 API
 * @description 文件夹（按日期组织资料）、项目标签与 source/ 目录扫描导入。
 */
import { request } from './client'

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

/** 项目信息（纯标签归属，不再作为 Vault 目录） */
export interface Project {
  /** 项目 ID */
  id: string;
  /** 所属用户 ID */
  user_id: string;
  /** 项目显示名称，如 "Transformer 论文" */
  name: string;
  /** 项目描述 */
  description: string | null;
  /** 项目内笔记数量 */
  note_count: number;
  /** 创建时间（ISO 8601 格式） */
  created_at: string;
  /** 更新时间（ISO 8601 格式） */
  updated_at: string;
}

/** 项目详情，包含项目下的笔记列表 */
export interface ProjectDetail extends Project {
  notes: NoteInFolder[];
}

/**
 * 创建项目
 *
 * @param name - 项目名称
 * @param description - 项目描述（可选）
 * @returns 新创建的项目信息
 */
export async function createProject(name: string, description?: string): Promise<Project> {
  return request<Project>('/projects', {
    method: 'POST',
    body: JSON.stringify({ name, description }),
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
 */
export async function updateProject(
  projectId: string,
  name?: string,
  description?: string,
): Promise<Project> {
  return request<Project>(`/projects/${projectId}`, {
    method: 'PATCH',
    body: JSON.stringify({ name, description }),
  });
}

/**
 * 删除项目（只删标签，笔记与文件保留）
 *
 * @param projectId - 项目 ID
 * @returns 操作结果
 */
export async function deleteProject(projectId: string): Promise<{ message: string }> {
  return request<{ message: string }>(`/projects/${projectId}`, {
    method: 'DELETE',
  });
}

// --- 项目扫描导入 ---

/** 扫描导入的单条新笔记信息 */
export interface ScanImportDetail {
  id: string;
  title: string;
  status: string;
  source_type: string | null;
  path: string;
}

/** 被跳过的文件信息 */
export interface ScanSkipDetail {
  path: string;
  reason: string;
}

/** 扫描 source/ 目录并导入新文件的响应 */
export interface ScanImportResponse {
  project_id: string;
  project_name: string;
  scanned: number;
  imported: number;
  skipped: number;
  unsupported: number;
  imported_notes: ScanImportDetail[];
  skipped_details: ScanSkipDetail[];
  unsupported_details: ScanSkipDetail[];
}

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
 * @returns 添加结果统计（added / not_found）
 */
export async function addNotesToProject(projectId: string, noteIds: string[]): Promise<{ added: number; not_found: number }> {
  return request<{ added: number; not_found: number }>(`/projects/${projectId}/notes`, {
    method: 'POST',
    body: JSON.stringify({ note_ids: noteIds }),
  });
}

/**
 * 将笔记从项目中移出
 *
 * @param projectId - 项目 ID
 * @param noteId - 要移出的笔记 ID
 * @returns 操作结果
 */
export async function removeNoteFromProject(projectId: string, noteId: string): Promise<{ message: string }> {
  return request<{ message: string }>(`/projects/${projectId}/notes/${noteId}`, {
    method: 'DELETE',
  });
}