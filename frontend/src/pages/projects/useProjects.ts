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
import { useQuery, useQueryClient } from '@tanstack/react-query';
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
import { translate } from '../../i18n/translate';

/**
 * 项目列表查询的 key（迁移 5.2 / C1）
 *
 * 这个 key 没有参数维度（项目列表是按用户全量的），所以直接用常量。
 * 四处写操作后失效的都是它 —— 字面量写五遍迟早不一致，
 * 而那种不一致**不会报错**，只会让"改完某一处列表没刷新"偶尔出现。
 */
export const PROJECTS_QUERY_KEY = ['projects'] as const;

export function useProjects() {
  const confirm = useConfirm();
  /** 页面级失败（加载/删除/扫描/重命名请求失败…）：与重命名校验分开，各报各的 */
  const [error, setError] = useState('');
  /** 重命名表单的校验错误（"项目名称不能为空"），不能被页面级失败的关闭按钮顺手清掉 */
  const [renameError, setRenameError] = useState('');
  const queryClient = useQueryClient();

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

  /**
   * 项目列表（迁移 5.2 / C1，2026-10-22）
   *
   * ## `projects` / `loading` 两个本地状态去哪了
   *
   * 改动前是 `loadProjects()` + `useEffect` + 两个 `useState`。
   * 现在由 `data` / `isLoading` 承担 —— **本地那两个删掉**，不留两套
   * （计划 §4 风险栏）。`error` **保留为本地状态**：它是页面级失败出口，
   * 写操作（重命名/删除/扫描）失败时也会往里写，不只是加载失败。
   *
   * ## `unwrapProjects` 留在 queryFn 里
   *
   * 它是**契约漂移兜底**（`/projects` 可能回 204/空体或 `{items:[...]}` 包装）——
   * 属于"把服务端响应变成领域对象"，是 queryFn 该做的事。
   */
  const {
    data: projectsData,
    isLoading: loading,
    error: queryError,
    refetch,
  } = useQuery({
    queryKey: PROJECTS_QUERY_KEY,
    queryFn: async ({ signal }) => unwrapProjects(await getProjects({ signal })),
  });

  const projects = projectsData ?? [];

  // 加载失败也写进页面级 error（与改动前一致：那时它在 loadProjects 的 catch 里）
  useEffect(() => {
    if (queryError) {
      console.error('加载项目列表失败:', queryError);
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setError(translate('projectPage.loadFailed'));
    }
    // ⚠️ `translate` **不能**进依赖数组：它是模块级的 i18n 函数（不是本 hook 里的
    // `t`），改它不会触发重渲染 —— 放进依赖数组 lint 会报
    // "unnecessary dependency ... Outer scope values like 'translate' aren't
    // valid dependencies"。第一版就是那么写的。
  }, [queryError]);

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
      setRenameError(translate('projectPage.nameRequired'));
      return;
    }
    setRenameError('');
    try {
      // ── B3（2026-10-22）：清空描述要真的能保存 ──
      //
      // 改造前这里是 `edit.description.trim() || undefined`：用户清空描述时
      // `''` 变成 `undefined`，而 `undefined` 会被 `JSON.stringify` **丢掉**，
      // 于是请求体里没有 `description` 这个键，后端按"不改"处理 ——
      // 表现是"清空 → 保存 → 重新加载后旧描述还在"
      // （走查报告 §2.5 丙-1，BN.3 里仅有的两条「确实影响使用」之一）。
      //
      // 现在清空时发**显式 `null`**，后端用 `model_fields_set` 区分
      // "没传这个键"与"传了 null"（见 `api/projects.py` 的 `clear_description`）。
      //
      // ⚠️ `undefined` 仍然保留给"这次不动描述"的场景（当前没有这种调用，
      // 但类型上保留它，是为了不让将来的调用方被迫用 `null` 表达"不改"）。
      await updateProject(p.id, edit.name.trim(), edit.description.trim() || null);
      setRenaming((prev) => {
        const next = { ...prev };
        delete next[p.id];
        return next;
      });
      // B3：声明这份数据已不可信，由 query 决定何时重取并通知所有依赖它的组件
      await queryClient.invalidateQueries({ queryKey: PROJECTS_QUERY_KEY });
    } catch (err) {
      console.error('重命名项目失败:', err);
      setError(translate('projectPage.renameFailed'));
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
      title: translate('projectPage.deleteConfirm', { name: p.name }),
      message: translate('projectPage.deleteImpact'),
      confirmText: translate('common.delete'),
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteProject(p.id);
      // B3：声明这份数据已不可信，由 query 决定何时重取并通知所有依赖它的组件
      await queryClient.invalidateQueries({ queryKey: PROJECTS_QUERY_KEY });
    } catch (err) {
      console.error('删除项目失败:', err);
      setError(translate('projectPage.deleteFailed'));
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
      setError(translate('projectPage.loadNotesFailed'));
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
        // B3：声明这份数据已不可信，由 query 决定何时重取并通知所有依赖它的组件
        await queryClient.invalidateQueries({ queryKey: PROJECTS_QUERY_KEY });
      }
    } catch (err) {
      console.error('扫描导入失败:', err);
      setError(translate('projectPage.scanFailed'));
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
      title: translate('projectPage.removeConfirm', { note: n.title, project: p.name }),
      confirmText: translate('projectPage.remove'),
    });
    if (!ok) return;
    try {
      await removeNoteFromProject(p.id, n.id);
      // B3：声明这份数据已不可信，由 query 决定何时重取并通知所有依赖它的组件
      await queryClient.invalidateQueries({ queryKey: PROJECTS_QUERY_KEY });
      await refreshExpandedDetail(p);
    } catch (err) {
      console.error('移出笔记失败:', err);
      setError(translate('projectPage.removeNoteFailed'));
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
    /** 重新拉取项目列表（新建项目后由调用方触发）。
     *  迁移 5.2 / C1 前它叫 `loadProjects`，是手写的取数函数；
     *  现在就是 query 的 `refetch` —— 语义相同（都返回 Promise、都重新取一次），
     *  所以直接换名而不是包一层同名的壳（那会留下一个与 query 并行的手写入口）。 */
    refetch,
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
