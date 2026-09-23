/**
 * @file 项目页的数据层：列表加载、重命名、删除、展开详情、扫描导入
 * @description 自 `pages/Projects.tsx` 拆分（overhaul-plan 5.5），**只搬不改**：
 * 失败文案（如「加载项目列表失败，请稍后重试」「项目名称不能为空」）、
 * `console.error` 的日志点、确认文案、以及
 * "删除项目只移除标签、笔记与文件保留"的语义全部逐字保留。
 *
 * 破坏性操作（删除项目 / 移出笔记）的二次确认在各自的处理器里，
 * 取消时**不发请求**；扫描导入只在 `imported > 0` 时刷新列表。
 *
 * 三处 BB.8 收尾（语义不变，只改"错在哪、报给谁"）：
 * 1. `window.confirm` → 裸 `confirm`：全站 8 处裸调用 vs 4 处 `window.confirm`，
 *    统一到多数写法（浏览器里是同一个函数，测试里的 `window.confirm` 间谍照样命中）；
 * 2. 重命名校验（"项目名称不能为空"）从页面级 `error` 槽位里分出来 ——
 *    共用一个槽位时点掉"加载失败"会把"名称不能为空"一起抹掉，用户看不到自己为什么没保存成功；
 * 3. 挂上契约漂移提示的唯一出口（见 `pages/contractDrift.ts`）：`/projects` 与
 *    `/projects/{id}` 的形状漂移在归一化时上报，由这里接到全局 toast。
 */
import { useEffect, useState } from 'react';
import {
  deleteProject,
  getProjectDetail,
  getProjects,
  removeNoteFromProject,
  scanProject,
  updateProject,
  type NoteInFolder,
  type Project,
  type ProjectDetail,
  type ScanImportResponse,
} from '../../api/client';
import { useConfirm } from '../../components/ConfirmProvider';
import { useContractDriftNotice } from '../contractDrift';
import { unwrapProjects } from './helpers';

export function useProjects() {
  const confirm = useConfirm();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  /** 页面级失败（加载/删除/扫描/重命名请求失败…）：与重命名校验分开，各报各的 */
  const [error, setError] = useState('');
  /** 重命名表单的校验错误（"项目名称不能为空"），不能被页面级失败的关闭按钮顺手清掉 */
  const [renameError, setRenameError] = useState('');

  // 重命名（行内编辑）
  const [renaming, setRenaming] = useState<Record<string, { name: string; description: string }>>(
    {},
  );

  // 展开的笔记列表
  const [expanded, setExpanded] = useState<Record<string, ProjectDetail | null>>({});

  // 扫描导入
  const [scanning, setScanning] = useState<Record<string, boolean>>({});
  const [scanResults, setScanResults] = useState<Record<string, ScanImportResponse | null>>({});

  // 契约漂移的提示出口（本页面所有归一化的上报都从这里接到全局 toast）。
  // 放在既有 hook 之后：新增 hook 不改变上面那些 hook 的调用顺序。
  useContractDriftNotice();

  /** 加载项目列表 */
  async function loadProjects() {
    setLoading(true);
    setError('');
    try {
      const data = await getProjects();
      // 契约漂移兜底见 unwrapProjects：/projects 可能回 204/空体或 {items:[...]} 包装
      setProjects(unwrapProjects(data));
    } catch (err) {
      console.error('加载项目列表失败:', err);
      setError('加载项目列表失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  }

  // 挂载时加载数据（数据获取型 effect，同步 setState 豁免）
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadProjects();
  }, []);

  /** 开始重命名 */
  function startRename(p: Project) {
    // 上一次的校验错误属于上一次编辑，重开表单就不该再挂着
    setRenameError('');
    setRenaming((prev) => ({
      ...prev,
      [p.id]: { name: p.name, description: p.description ?? '' },
    }));
  }

  /** 更新行内编辑草稿（名称或描述） */
  function updateRenameField(p: Project, field: 'name' | 'description', value: string) {
    setRenaming((prev) => ({ ...prev, [p.id]: { ...prev[p.id], [field]: value } }));
  }

  /** 提交重命名 */
  async function handleRename(p: Project) {
    const edit = renaming[p.id];
    if (!edit) return;
    const name = edit.name.trim();
    if (!name) {
      setRenameError('项目名称不能为空');
      return;
    }
    setRenameError('');
    try {
      await updateProject(p.id, edit.name.trim(), edit.description.trim() || undefined);
      setRenaming((prev) => {
        const next = { ...prev };
        delete next[p.id];
        return next;
      });
      await loadProjects();
    } catch (err) {
      console.error('重命名项目失败:', err);
      setError('重命名项目失败，请稍后重试');
    }
  }

  /** 取消重命名 */
  function cancelRename(p: Project) {
    setRenameError(''); // 表单没了，表单上的校验错误也不该留在页面上
    setRenaming((prev) => {
      const next = { ...prev };
      delete next[p.id];
      return next;
    });
  }

  /** 删除项目（只删标签，笔记与文件保留） */
  async function handleDelete(p: Project) {
    const ok = await confirm({
      // 原文案一字未删，只是按 ConfirmDialog 的两段拆开
      title: `确定删除项目「${p.name}」？`,
      message: '删除仅移除该项目标签，关联笔记与文件都会保留。',
      confirmText: '删除',
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteProject(p.id);
      await loadProjects();
    } catch (err) {
      console.error('删除项目失败:', err);
      setError('删除项目失败，请稍后重试');
    }
  }

  /** 展开/收起笔记列表 */
  async function toggleExpand(p: Project) {
    if (expanded[p.id]) {
      setExpanded((prev) => {
        const next = { ...prev };
        delete next[p.id];
        return next;
      });
      return;
    }
    try {
      const detail = await getProjectDetail(p.id);
      setExpanded((prev) => ({ ...prev, [p.id]: detail }));
    } catch (err) {
      console.error('加载项目详情失败:', err);
      setError('加载项目笔记失败，请稍后重试');
    }
  }

  /** 扫描收件箱 source/ 目录并打上当前项目标签 */
  async function handleScan(p: Project) {
    setScanning((prev) => ({ ...prev, [p.id]: true }));
    setScanResults((prev) => ({ ...prev, [p.id]: null }));
    try {
      const result = await scanProject(p.id);
      setScanResults((prev) => ({ ...prev, [p.id]: result }));
      // 有新导入时刷新项目笔记数
      if (result.imported > 0) {
        await loadProjects();
      }
    } catch (err) {
      console.error('扫描导入失败:', err);
      setError('扫描导入失败，请确认后端服务可用后重试');
    } finally {
      setScanning((prev) => {
        const next = { ...prev };
        delete next[p.id];
        return next;
      });
    }
  }

  /** 若项目处于展开状态，重新拉取详情以同步笔记列表 */
  async function refreshExpandedDetail(p: Project) {
    if (!expanded[p.id]) return;
    try {
      const detail = await getProjectDetail(p.id);
      setExpanded((prev) => ({ ...prev, [p.id]: detail }));
    } catch (err) {
      console.error('刷新项目详情失败:', err);
    }
  }

  /** 将笔记移出项目（破坏性操作：先 confirm，取消则不发请求） */
  async function handleRemoveNote(p: Project, n: NoteInFolder) {
    // 「移出项目」刻意**不加** danger：笔记还能再加回来，红色留给不可恢复的操作
    // （批次 D2 收尾定的规矩 —— 红色一旦到处都是就不再承载语义）
    const ok = await confirm({
      title: `确定将笔记「${n.title}」移出项目「${p.name}」？`,
      confirmText: '移出',
    });
    if (!ok) return;
    try {
      await removeNoteFromProject(p.id, n.id);
      await loadProjects();
      await refreshExpandedDetail(p);
    } catch (err) {
      console.error('移出笔记失败:', err);
      setError('移出笔记失败，请稍后重试');
    }
  }

  /** 关闭页面级错误提示 */
  function clearError() {
    setError('');
  }

  /** 关闭重命名校验提示（只清自己那一条） */
  function clearRenameError() {
    setRenameError('');
  }

  return {
    projects,
    loading,
    error,
    clearError,
    renameError,
    clearRenameError,
    renaming,
    expanded,
    scanning,
    scanResults,
    loadProjects,
    startRename,
    updateRenameField,
    handleRename,
    cancelRename,
    handleDelete,
    toggleExpand,
    handleScan,
    refreshExpandedDetail,
    handleRemoveNote,
  };
}
