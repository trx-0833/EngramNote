/**
 * @file 上传 API
 * @description 单次上传、两阶段上传、上传状态查询、转换重试与文件夹内上传。
 */
import { request, uploadRequest, type Note } from './client'

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
export async function uploadFile(file: File, backend?: string, noteRole?: string, linkedMaterialIds?: string[], projectIds?: string[]): Promise<Note> {
  const formData = new FormData();
  formData.append('file', file);
  if (backend) formData.append('backend', backend);
  if (noteRole) formData.append('note_role', noteRole);
  if (projectIds && projectIds.length > 0) formData.append('project_ids', JSON.stringify(projectIds));
  if (linkedMaterialIds && linkedMaterialIds.length > 0) {
    formData.append('linked_material_ids', JSON.stringify(linkedMaterialIds));
  }

  // 统一走 uploadRequest（multipart 不设 Content-Type，由浏览器自动生成边界），见 docs/decisions.md#F-22
  return uploadRequest<Note>('/upload', formData);
}

/** 两阶段上传阶段 1（prepare）的返回结果 */
export interface PreparedUpload {
  /** 临时上传标识，commit 时需回传 */
  temp_id: string;
  /** 原始文件名 */
  filename: string;
  /** 来源类型，如 pdf、docx 等 */
  source_type: string;
  /** PDF 页数，非 PDF 为 null */
  page_count: number | null;
}

/**
 * 两阶段上传阶段 1：接收文件并暂存到服务端临时目录
 *
 * 仅做基础校验（格式/大小/内容签名），返回 PDF 页数等信息供前端裁剪配置；
 * 配额校验与正式入库在 commitUpload 阶段完成。
 *
 * @param file - 要上传的文件对象
 * @returns 临时上传信息（temp_id、filename、source_type、page_count）
 */
export async function prepareUpload(file: File): Promise<PreparedUpload> {
  const formData = new FormData();
  formData.append('file', file);

  // 统一走 uploadRequest，见 docs/decisions.md#F-22
  return uploadRequest<PreparedUpload>('/upload/prepare', formData);
}

/** 两阶段上传阶段 2（commit）的可选参数 */
export interface CommitUploadOptions {
  /** 重命名后的文件名（可选），扩展名需与原文件一致 */
  filename?: string;
  /** 解析后端选择（可选） */
  backend?: string;
  /** 笔记角色，默认 material */
  note_role?: string;
  /** 所属项目标签 ID 数组（可选，支持多标签） */
  project_ids?: string[];
  /** 关联资料 ID 列表（可选，仅个人笔记） */
  linked_material_ids?: string[];
  /** 页码范围表达式（如 "1-20,25,30-32"），仅 PDF 支持 */
  crop_page_range?: string;
}

/**
 * 两阶段上传阶段 2：消费临时文件正式上传
 *
 * 可对 PDF 按页裁剪后再入库：传 crop_page_range 时后端先裁剪再转换；
 * 不传则与单次直传等价。返回 NoteResponse，后续可轮询转换状态。
 *
 * @param tempId - prepareUpload 返回的临时上传标识
 * @param opts - 可选上传参数
 * @returns 新创建的笔记记录（状态为 uploading）
 */
export async function commitUpload(tempId: string, opts: CommitUploadOptions = {}): Promise<Note> {
  const formData = new FormData();
  formData.append('temp_id', tempId);
  if (opts.filename && opts.filename.trim()) formData.append('filename', opts.filename.trim());
  if (opts.backend) formData.append('backend', opts.backend);
  if (opts.note_role) formData.append('note_role', opts.note_role);
  if (opts.project_ids && opts.project_ids.length > 0) formData.append('project_ids', JSON.stringify(opts.project_ids));
  if (opts.crop_page_range && opts.crop_page_range.trim()) {
    formData.append('crop_page_range', opts.crop_page_range.trim());
  }
  if (opts.linked_material_ids && opts.linked_material_ids.length > 0) {
    formData.append('linked_material_ids', JSON.stringify(opts.linked_material_ids));
  }

  // 统一走 uploadRequest，见 docs/decisions.md#F-22
  return uploadRequest<Note>('/upload/commit', formData);
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

/**
 * 上传文件到指定文件夹
 * 在原有 uploadFile 基础上增加 folder_id 参数。
 *
 * @param file - 要上传的文件对象
 * @param folderId - 目标文件夹 ID
 * @param backend - 解析后端选择（可选）
 * @returns 新创建的笔记记录
 */
export async function uploadFileToFolder(file: File, folderId: string, backend?: string, projectIds?: string[]): Promise<Note> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('folder_id', folderId);
  if (backend) formData.append('backend', backend);
  if (projectIds && projectIds.length > 0) formData.append('project_ids', JSON.stringify(projectIds));

  // 统一走 uploadRequest，见 docs/decisions.md#F-22
  return uploadRequest<Note>('/upload', formData);
}