/**
 * @file 今日资料页面
 * @description 按日期组织学习资料的文件夹视图，支持：
 * 1. 新建文件夹（默认以今天日期命名）
 * 2. 浏览最近 7 天的文件夹列表
 * 3. 展开文件夹查看内部文件
 * 4. 在文件夹内上传新文件
 * 5. 按状态筛选文件
 *
 * 批次 E8（visual-refactor-plan §6）的两处：
 *   ① **时间线**：文件夹里的资料行从 `.card` 改成单栏时间线（留白分隔 + 元数据 chip），
 *      见 `DailyMaterials.module.css`；
 *   ② **返回时恢复滚动位置**：§C3「每日材料」行的最后一条（Memos 为此单开过一个 PR）。
 *      实现只在**浏览器后退/前进**（react-router 的 `POP`）时生效，见下方 `pageMemory`。
 */
import { useEffect, useLayoutEffect, useState, useRef } from 'react';
import { Link, useNavigationType } from 'react-router-dom';
import {
  getFolders,
  getFolderDetail,
  createFolder,
  deleteFolder,
  updateFolder,
  uploadFileToFolder,
  getUploadStatus,
  type Folder,
  type FolderDetail,
  type NoteInFolder,
} from '../api/client';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import ErrorDisplay from '../components/ErrorDisplay';
import Icon from '../components/Icon';
import { sourceTypeLabels, statusLabels, statusClass } from '../utils/labels';
// 页面标题（visual-refactor-plan 批次 C1）：字号本就 1.5rem，观感不变；
// 页头那一行（标题 + 新建文件夹按钮）交给组件，窄屏换行随之进模块
import PageHeader from '../components/PageHeader';
import ConfirmDialog from '../components/ConfirmDialog';
import { useToast } from '../components/Toast';
// 本页私有样式（visual-refactor-plan 批次 E8）：时间线与元数据 chip
import styles from './DailyMaterials.module.css';

/** 允许上传的文件扩展名列表 */
const ALLOWED_EXTENSIONS = [
  '.pdf',
  '.png',
  '.jpg',
  '.jpeg',
  '.docx',
  '.pptx',
  '.xlsx',
  '.mp4',
  '.mp3',
  '.wav',
  '.m4a',
  '.md',
];

/** 状态筛选选项 */
const STATUS_FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'processing', label: '处理中' },
  { key: 'completed', label: '已完成' },
  { key: 'failed', label: '失败' },
] as const;

/**
 * 格式化文件大小
 * @param bytes - 文件大小（字节）
 * @returns 格式化后的文件大小字符串
 */
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * 格式化日期为中文格式
 * @param dateStr - ISO 日期字符串
 * @returns 格式化后的日期字符串
 */
function formatDate(dateStr: string): string {
  const date = new Date(dateStr);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);

  if (date.toDateString() === today.toDateString()) return '今天';
  if (date.toDateString() === yesterday.toDateString()) return '昨天';
  return date.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' });
}

/**
 * 判断笔记状态属于哪个筛选类别
 * @param status - 笔记状态
 * @returns 筛选类别 key
 */
function getStatusCategory(status: string): string {
  if (['uploading', 'converting', 'cleaning', 'learning'].includes(status)) return 'processing';
  if (['converted', 'cleaned', 'archived'].includes(status)) return 'completed';
  if (['failed', 'cleaning_failed', 'learning_failed'].includes(status)) return 'failed';
  return 'all';
}

/**
 * 会话内的"上次离开这一页时的样子"（visual-refactor-plan 批次 E8）。
 *
 * ## 为什么放在模块作用域，而不是 sessionStorage
 *
 * 页面组件是一个**懒加载 chunk 里的组件**：离开 `/daily` 时它被卸载，但模块本身
 * 留在内存里 —— 所以模块作用域的变量正好覆盖"这一趟会话里去过哪儿"，
 * 而整页刷新（F5）后它自然清零。写 sessionStorage 反而多两种要处理的情况：
 * 另一个标签页写脏、以及刷新后"恢复"到一个用户早就不记得的滚动位置。
 *
 * ## 只记两件事
 *
 * `folderId`：离开时**展开着**的文件夹（没有就是 `null`）。
 * `scrollY`：离开时的窗口滚动位置。这个页面由**窗口**滚动
 * （`App.module.css` 的 `.main` 只是居中 + 内边距，没有内层滚动容器）。
 */
let pageMemory: { folderId: string | null; scrollY: number } | null = null;

/**
 * 今日资料页面组件
 *
 * 数据流：
 * 1. 组件挂载时调用 getFolders() 获取最近 7 天的文件夹列表
 * 2. 点击文件夹展开详情，调用 getFolderDetail() 获取笔记列表
 * 3. 新建文件夹调用 createFolder()，默认以今天日期命名
 * 4. 在文件夹内上传文件调用 uploadFileToFolder()
 *
 * 状态管理：
 * - folders: 文件夹列表
 * - expandedFolderId: 当前展开的文件夹 ID
 * - folderDetail: 当前展开文件夹的详情（含笔记列表）
 * - statusFilter: 笔记状态筛选
 * - uploading: 是否正在上传
 */
export default function DailyMaterials() {
  const toast = useToast();
  /**
   * 这次渲染是"怎么来的"。
   *
   * `POP` = 浏览器后退/前进（也包括首屏进入）；`PUSH` = 点了站内链接过来。
   * 恢复滚动位置**只对 POP 做**：从侧栏点「今日资料」进来时用户期待的是页面顶部，
   * 而"上一次停在半截"是另一回事（那是 Memos 那个 PR 要解决的具体场景：
   * 点开一条资料看详情，再退回来，列表还在原处）。
   */
  const navigationType = useNavigationType();
  /** 本次挂载要不要恢复（POP 且有记忆才恢复）—— 只在首次渲染取值，之后不再看它 */
  const restoreRef = useRef(navigationType === 'POP' ? pageMemory : null);
  /** 恢复只做一次（`loading` / `folders` 变化会重复触发下面那条 effect） */
  const restoredRef = useRef(false);
  /** 展开态的最新值：卸载时的清理函数读它，不能闭包捕获第一次渲染的 `null` */
  const expandedRef = useRef<string | null>(null);
  /** 待应用的滚动位置（详情渲染完才滚，见下面那条 `useLayoutEffect`） */
  const pendingScrollRef = useRef<number | null>(null);
  /** 文件夹列表 */
  const [folders, setFolders] = useState<Folder[]>([]);
  /** 当前展开的文件夹 ID */
  const [expandedFolderId, setExpandedFolderId] = useState<string | null>(null);
  /** 当前展开文件夹的详情 */
  const [folderDetail, setFolderDetail] = useState<FolderDetail | null>(null);
  /** 笔记状态筛选 */
  const [statusFilter, setStatusFilter] = useState<string>('all');
  /** 数据加载状态 */
  const [loading, setLoading] = useState(true);
  /** 详情加载状态 */
  const [detailLoading, setDetailLoading] = useState(false);
  /** 错误信息 */
  const [error, setError] = useState('');
  /** 详情错误信息 */
  const [detailError, setDetailError] = useState('');
  /** 是否正在创建文件夹 */
  const [creating, setCreating] = useState(false);
  /** 是否正在上传文件 */
  const [uploading, setUploading] = useState(false);
  /** 上传状态文本 */
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  /** 隐藏的文件输入框引用 */
  const fileInputRef = useRef<HTMLInputElement>(null);
  /** 当前正在重命名的文件夹 ID */
  const [editingFolderId, setEditingFolderId] = useState<string | null>(null);
  /** 重命名输入框的当前值 */
  const [editingName, setEditingName] = useState('');
  /** 是否正在保存重命名 */
  const [renaming, setRenaming] = useState(false);
  /** 重命名输入框引用 */
  const renameInputRef = useRef<HTMLInputElement>(null);
  /**
   * 待删除的文件夹 ID（`null` = 删除确认框关着）。
   *
   * 批次 D3：原来是同步的 `confirm('确定删除此文件夹？')`，改成对话框后
   * "要删哪一个"必须先存起来 —— 用户点确认时那次点击上下文早就不在了。
   */
  const [pendingDeleteFolderId, setPendingDeleteFolderId] = useState<string | null>(null);

  /**
   * 上传状态轮询的定时器句柄
   *
   * 必须存 ref 并在卸载时清理（见 docs/overhaul-plan.md §2.8 F-5）：
   * 原实现用裸 `setTimeout(check, 5000)` 递归且**从不清理**，
   * 后果是用户上传后切走页面，轮询仍会继续跑满 120 次 × 5 秒 = **10 分钟**，
   * 期间不断对已卸载组件 setState，并刷新与当前页面无关的文件夹详情。
   * 连续上传两个文件还会产生两条互不感知的轮询链。
   * （同项目 Upload.tx 已有正确实现：pollTimerRef + 卸载清理，此处属遗漏。）
   */
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 卸载标志：阻止已在飞行中的请求回来后继续 setState / 续链 */
  const unmountedRef = useRef(false);

  /** 停止轮询并清空句柄 */
  function stopPolling() {
    if (pollTimerRef.current !== null) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }

  useEffect(() => {
    unmountedRef.current = false;
    return () => {
      unmountedRef.current = true;
      stopPolling();
    };
  }, []);

  /**
   * 记住展开态（批次 E8）。
   *
   * 卸载时的清理函数要写 `pageMemory.folderId`，而它**不能闭包捕获**
   * `expandedFolderId` —— 那个闭包是首次渲染的，永远是 `null`（一个典型的
   * "记忆永远是空的"事故：症状是"恢复位置偶尔不生效"，很难查）。
   */
  useEffect(() => {
    expandedRef.current = expandedFolderId;
  }, [expandedFolderId]);

  /**
   * 卸载时把"展开的是哪个文件夹 + 此刻滚到哪儿"存进会话记忆（批次 E8）。
   *
   * 为什么在**卸载**时才读 `window.scrollY`：路由切换时浏览器不会自动把窗口
   * 滚回顶部（本项目没有装 `ScrollRestoration`），所以这一刻读到的就是用户
   * 离开时的位置。
   */
  useEffect(
    () => () => {
      pageMemory = { folderId: expandedRef.current, scrollY: window.scrollY };
    },
    [],
  );

  /**
   * 获取文件夹列表
   */
  async function fetchFolders() {
    setLoading(true);
    setError('');
    try {
      const data = await getFolders(7);
      setFolders(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }

  // 挂载时加载数据（数据获取型 effect，同步 setState 豁免）
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchFolders();
  }, []);

  /**
   * 从浏览器后退/前进回到本页时，把上次的展开态接回去（批次 E8）。
   *
   * 依赖 `loading` 与 `folders`：展开的是**列表里的一行**，列表没到位就无从展开；
   * `restoredRef` 保证只做一次（这两个依赖会变好几次）。
   *
   * 只恢复"还在最近 7 天列表里的那个文件夹"：不在列表里（隔天/被删）就只滚位置，
   * 免得对一个已经不存在的 id 发请求、再把"加载详情失败"糊到用户脸上。
   */
  useEffect(() => {
    if (loading || restoredRef.current) return;
    restoredRef.current = true;
    const memory = restoreRef.current;
    if (!memory) return;
    const folder = memory.folderId ? folders.find((f) => f.id === memory.folderId) : undefined;
    if (folder) {
      // 与上面那条 `fetchFolders` 的区别：这里的 setState 发生在 async 函数内部
      // （`await` 之前），编译器规则不把它算作"effect 里的同步 setState"，
      // 所以不需要 `react-hooks/set-state-in-effect` 的豁免 —— 实测多写一条
      // 豁免注释会被 eslint 判为"未使用的 disable 指令"（0 error / 1 warning）。
      void loadFolderDetail(folder.id, memory.scrollY);
    } else {
      window.scrollTo({ top: memory.scrollY });
    }
  }, [loading, folders]);

  /**
   * 详情渲染完再把窗口滚回原位（批次 E8）。
   *
   * 用 `useLayoutEffect` 而不是 `useEffect`：前者在**浏览器绘制之前**跑，
   * 用户不会先看到顶部闪一下再跳下去。
   * 判据是"详情已经不在加载中"——那时 `<div className={styles.materialsTimeline}>`
   * 已经在 DOM 里，滚到目标位置不会被截断（内容不够高时浏览器自己会夹住）。
   */
  useLayoutEffect(() => {
    const target = pendingScrollRef.current;
    if (target === null || detailLoading) return;
    pendingScrollRef.current = null;
    window.scrollTo({ top: target });
  }, [detailLoading, folderDetail]);

  /**
   * 加载某个文件夹的详情并展开它。
   *
   * @param folderId - 要展开的文件夹
   * @param restoreScrollY - 传了就是"恢复路径"：详情渲染完后滚到这个位置
   *   （展开路径不传，保持原来的行为——点开文件夹不滚动）
   */
  async function loadFolderDetail(folderId: string, restoreScrollY?: number) {
    setExpandedFolderId(folderId);
    setDetailLoading(true);
    setDetailError('');
    setStatusFilter('all');

    try {
      const detail = await getFolderDetail(folderId);
      setFolderDetail(detail);
      if (restoreScrollY !== undefined) pendingScrollRef.current = restoreScrollY;
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : '加载详情失败');
    } finally {
      setDetailLoading(false);
    }
  }

  /**
   * 展开/折叠文件夹
   * 点击已展开的文件夹则折叠，点击新的文件夹则加载其详情。
   *
   * @param folderId - 文件夹 ID
   */
  async function toggleFolder(folderId: string) {
    if (expandedFolderId === folderId) {
      setExpandedFolderId(null);
      setFolderDetail(null);
      return;
    }

    await loadFolderDetail(folderId);
  }

  /**
   * 创建新文件夹
   * 默认以今天日期命名，如 "2024-01-15 学习资料"
   */
  async function handleCreateFolder() {
    setCreating(true);
    try {
      const today = new Date().toISOString().split('T')[0];
      const folder = await createFolder(`${today} 学习资料`);
      setFolders((prev) => [folder, ...prev]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '创建失败');
    } finally {
      setCreating(false);
    }
  }

  /**
   * 删除空文件夹
   * @param folderId - 文件夹 ID
   * @param e - 鼠标事件，阻止冒泡
   */
  async function handleDeleteFolder(folderId: string, e: React.MouseEvent) {
    e.stopPropagation();
    setPendingDeleteFolderId(folderId);
  }

  /**
   * 真正执行"删除空文件夹"（批次 D3：原来这段紧跟在同步的 `confirm()` 之后，
   * 现在由确认框的 `onConfirm` 调用 —— 逐字保留，含删除后收起详情的联动
   * 与失败走 `toast.error`）
   */
  async function performDeleteFolder(folderId: string) {
    try {
      await deleteFolder(folderId);
      setFolders((prev) => prev.filter((f) => f.id !== folderId));
      if (expandedFolderId === folderId) {
        setExpandedFolderId(null);
        setFolderDetail(null);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '删除失败');
    }
  }

  /**
   * 进入文件夹重命名模式
   * @param folder - 要重命名的文件夹
   * @param e - 鼠标事件，阻止冒泡触发折叠/展开
   */
  function startRenameFolder(folder: Folder, e: React.MouseEvent) {
    e.stopPropagation();
    setEditingFolderId(folder.id);
    setEditingName(folder.name);
    // 输入框渲染后自动聚焦并选中文本
    setTimeout(() => {
      const input = renameInputRef.current;
      if (input) {
        input.focus();
        input.select();
      }
    }, 0);
  }

  /**
   * 取消重命名
   * @param e - 事件，阻止冒泡
   */
  function cancelRenameFolder(e?: React.SyntheticEvent) {
    e?.stopPropagation();
    setEditingFolderId(null);
    setEditingName('');
    setRenaming(false);
  }

  /**
   * 保存重命名
   * @param folderId - 文件夹 ID
   * @param e - 事件，阻止冒泡
   */
  async function saveRenameFolder(folderId: string, e?: React.SyntheticEvent) {
    e?.stopPropagation();
    const trimmed = editingName.trim();
    if (!trimmed) {
      toast.warning('文件夹名称不能为空');
      return;
    }

    setRenaming(true);
    try {
      const updated = await updateFolder(folderId, trimmed);
      setFolders((prev) => prev.map((f) => (f.id === folderId ? { ...f, name: updated.name } : f)));
      // 若该文件夹已展开，同步更新详情中的文件夹名
      setFolderDetail((prev) =>
        prev && prev.id === folderId ? { ...prev, name: updated.name } : prev,
      );
      setEditingFolderId(null);
      setEditingName('');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '重命名失败');
    } finally {
      setRenaming(false);
    }
  }

  /**
   * 重命名输入框按键处理：Enter 保存，Esc 取消
   * @param folderId - 文件夹 ID
   * @param e - 键盘事件
   */
  function handleRenameKeyDown(folderId: string, e: React.KeyboardEvent<HTMLInputElement>) {
    e.stopPropagation();
    if (e.key === 'Enter') {
      e.preventDefault();
      saveRenameFolder(folderId, e);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cancelRenameFolder(e);
    }
  }

  /**
   * 处理文件上传到文件夹
   * @param file - 上传的文件
   */
  async function handleUpload(file: File) {
    if (!expandedFolderId) return;

    const ext = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      toast.error(`不支持的文件格式: ${ext}`);
      return;
    }

    setUploading(true);
    setUploadStatus('上传中...');

    try {
      const note = await uploadFileToFolder(file, expandedFolderId);
      setUploadStatus('文件已上传，正在转换...');

      // 轮询转换状态
      pollUploadStatus(note.id);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '上传失败');
      setUploading(false);
      setUploadStatus(null);
    }
  }

  /**
   * 轮询上传/转换状态
   * 使用 setTimeout 递归代替 setInterval，避免 async 回调请求重叠。
   * @param noteId - 笔记 ID
   */
  async function pollUploadStatus(noteId: string) {
    const maxAttempts = 120;
    let attempts = 0;

    // 重新开始轮询前先终止上一条链，避免并发轮询
    stopPolling();

    /** 所有终态：成功或失败 */
    const successStatuses = ['converted', 'cleaned', 'archived', 'learning'];
    const failedStatuses = ['failed', 'cleaning_failed', 'learning_failed'];

    async function check() {
      // 组件已卸载则直接终止，不再发请求、不再续链
      if (unmountedRef.current) return;

      if (attempts >= maxAttempts) {
        setUploading(false);
        setUploadStatus(null);
        return;
      }
      attempts++;

      try {
        const res = await getUploadStatus(noteId);
        if (unmountedRef.current) return;
        setUploadStatus(`状态: ${statusLabels[res.status] || res.status}`);

        if (successStatuses.includes(res.status) || failedStatuses.includes(res.status)) {
          setUploading(false);
          setUploadStatus(null);
          // 刷新文件夹详情
          if (expandedFolderId) {
            const detail = await getFolderDetail(expandedFolderId);
            if (!unmountedRef.current) setFolderDetail(detail);
          }
          return; // 终态，停止轮询
        }
      } catch {
        // 出错继续轮询
      }

      if (unmountedRef.current) return;
      // 非终态，5 秒后再检查（句柄入 ref，供卸载/重启时清理）
      pollTimerRef.current = setTimeout(check, 5000);
    }

    check();
  }

  /**
   * 处理文件选择
   */
  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (files && files.length > 0) {
      handleUpload(files[0]);
    }
    // 重置 input 以便再次选择同一文件
    e.target.value = '';
  }

  /**
   * 根据状态筛选笔记
   */
  const filteredNotes: NoteInFolder[] = folderDetail
    ? folderDetail.notes.filter((note) =>
        statusFilter === 'all' ? true : getStatusCategory(note.status) === statusFilter,
      )
    : [];

  return (
    <div className="page-enter">
      {/* 页面标题和操作按钮（批次 C1：统一进 <PageHeader>） */}
      <PageHeader
        title="今日资料"
        actions={
          <button className="btn btn-primary" onClick={handleCreateFolder} disabled={creating}>
            {creating ? '创建中...' : '新建文件夹'}
          </button>
        }
      />

      {/* 文件夹列表 */}
      {loading ? (
        <LoadingSpinner />
      ) : error ? (
        <ErrorDisplay message={error} onRetry={fetchFolders} />
      ) : folders.length === 0 ? (
        <EmptyState
          message="还没有文件夹"
          description="创建一个文件夹来组织今天的学习资料"
          action={
            <button className="btn btn-primary" onClick={handleCreateFolder}>
              新建文件夹
            </button>
          }
        />
      ) : (
        <div style={{ display: 'grid', gap: 'var(--space-md)' }}>
          {folders.map((folder) => (
            <div key={folder.id} className="card" style={{ overflow: 'hidden' }}>
              {/* 文件夹头部：折叠/展开是一**个控件**，操作按钮是它的**兄弟**。
                  ⚠️ 这里原来是 `div[role="button"][tabIndex=0]` 包着「重命名」「删除」
                  两个真按钮 —— axe 判 nested-interactive（F-30），与已修的 F-09
                  （仪表盘卡片）/ F-17（笔记列表卡片）是同一个洞的第三个入口。
                  改法就是 F-09 跑通的那个形状（**控件之间是兄弟**）：
                  外层回到"盒子"，折叠行为落在真有名字的按钮上（`<h3>` 里的
                  `<button aria-expanded>`，这是 WAI-ARIA 手风琴的标准写法：
                  标题里放按钮，屏幕阅读器念"三级标题 + <文件夹名> + 按钮 + 已折叠"）。
                  副作用是"点整行"变成"点文件夹名"：与 F-09 的取舍一致 ——
                  外壳只负责外观，行为在有名字的控件上；键盘 Tab 也只停一次。 */}
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: 'var(--space-md)',
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 'var(--space-md)',
                    flex: 1,
                    minWidth: 0,
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    {editingFolderId === folder.id ? (
                      <input
                        ref={renameInputRef}
                        type="text"
                        value={editingName}
                        onChange={(e) => setEditingName(e.target.value)}
                        onKeyDown={(e) => handleRenameKeyDown(folder.id, e)}
                        style={{
                          width: '100%',
                          maxWidth: '360px',
                          fontSize: '1rem',
                          fontWeight: 500,
                          padding: '4px 8px',
                          marginBottom: 'var(--space-xs)',
                          border: '1px solid var(--color-primary)',
                          borderRadius: '4px',
                        }}
                        disabled={renaming}
                        aria-label="文件夹名称"
                      />
                    ) : (
                      <h2
                        style={{
                          fontSize: '1.17em',
                          fontWeight: 500,
                          marginBottom: 'var(--space-xs)',
                        }}
                      >
                        <button
                          type="button"
                          onClick={() => toggleFolder(folder.id)}
                          aria-expanded={expandedFolderId === folder.id}
                          style={{
                            // 复位按钮的 UA 外观，只留下"可点"：字号/字重/颜色全部继承标题，
                            // 所以文件夹名的视觉与改动前一致（这次修的是结构，不是外观）
                            display: 'flex',
                            alignItems: 'center',
                            gap: 'var(--space-md)',
                            width: '100%',
                            background: 'none',
                            border: 'none',
                            padding: 0,
                            margin: 0,
                            font: 'inherit',
                            color: 'inherit',
                            textAlign: 'left',
                            cursor: 'pointer',
                          }}
                        >
                          {/* 展开/折叠箭头（装饰：状态由 aria-expanded 表达）。
                              批次 B3：`▶` 换 `<Icon name="chevron" />`，并且**挂上
                              `.collapse-arrow` 共享类** —— 这一处此前是一份内联的
                              死箭头（`transition: transform 0.2s` + 手写 rotate），
                              与另外三处（KnowledgeCards / QuestionSets / ProjectCard）
                              各写一遍，旋转时长还不一样（0.2s vs 0.3s / 同一条曲线）。
                              现在四处共用 `learning.css` 的一条过渡。 */}
                          <span
                            className={`collapse-arrow ${
                              expandedFolderId === folder.id ? 'collapse-arrow-open' : ''
                            }`}
                            aria-hidden="true"
                            style={{ color: 'var(--color-text-secondary)' }}
                          >
                            <Icon name="chevron" size={16} />
                          </span>
                          <span>{folder.name}</span>
                        </button>
                      </h2>
                    )}
                    <div style={{ display: 'flex', gap: 'var(--space-sm)', alignItems: 'center' }}>
                      <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                        {formatDate(folder.folder_date)}
                      </span>
                      <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                        {folder.note_count} 个文件
                      </span>
                    </div>
                  </div>
                </div>
                {/* 文件夹操作按钮：编辑态显示保存/取消，否则显示重命名/删除。
                    这些按钮是折叠控件（上面 h3 里那个）的**兄弟** —— 不是它的后代。
                    `stopPropagation` 已经不是必需的（外层那个 role="button" 没有了），
                    但各 handler 里仍留着：它们同时对"点空白处"这类调用有意义，
                    删掉属于顺手重构，与这次可访问性修复无关，所以刻意没动。 */}
                <div style={{ display: 'flex', gap: 'var(--space-xs)', alignItems: 'center' }}>
                  {editingFolderId === folder.id ? (
                    <>
                      <button
                        className="btn btn-primary"
                        style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                        onClick={(e) => saveRenameFolder(folder.id, e)}
                        disabled={renaming}
                        aria-label="保存名称"
                      >
                        {renaming ? '保存中...' : '保存'}
                      </button>
                      <button
                        className="btn btn-secondary"
                        style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                        onClick={(e) => cancelRenameFolder(e)}
                        disabled={renaming}
                        aria-label="取消重命名"
                      >
                        取消
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        className="btn btn-secondary"
                        style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                        onClick={(e) => startRenameFolder(folder, e)}
                        aria-label="重命名文件夹"
                      >
                        重命名
                      </button>
                      {/* 仅空文件夹可删除 */}
                      {folder.note_count === 0 && (
                        <button
                          className="btn btn-danger"
                          style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                          onClick={(e) => handleDeleteFolder(folder.id, e)}
                          aria-label="删除文件夹"
                        >
                          {/* 批次 B3：「删除」此前是全站纯文字按钮 —— 补 `delete` 图标 */}
                          <Icon name="delete" size={16} />
                          删除
                        </button>
                      )}
                    </>
                  )}
                </div>
              </div>

              {/* 文件夹详情：展开时显示 */}
              {expandedFolderId === folder.id && (
                <div
                  style={{ borderTop: '1px solid var(--color-border)', padding: 'var(--space-md)' }}
                >
                  {detailLoading ? (
                    <LoadingSpinner text="加载中..." />
                  ) : detailError ? (
                    <ErrorDisplay message={detailError} onRetry={() => toggleFolder(folder.id)} />
                  ) : (
                    <>
                      {/* 上传按钮和状态筛选 */}
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          marginBottom: 'var(--space-md)',
                          flexWrap: 'wrap',
                          gap: 'var(--space-sm)',
                        }}
                      >
                        <div
                          style={{ display: 'flex', gap: 'var(--space-sm)', alignItems: 'center' }}
                        >
                          <button
                            className="btn btn-primary"
                            style={{ fontSize: '0.875rem' }}
                            onClick={() => fileInputRef.current?.click()}
                            disabled={uploading}
                          >
                            {/* 批次 B3：上传按钮补 `upload` 图标（托盘 + 向上箭头） */}
                            <Icon name="upload" size={16} />
                            {uploading ? '上传中...' : '上传文件'}
                          </button>
                          <input
                            ref={fileInputRef}
                            type="file"
                            accept={ALLOWED_EXTENSIONS.join(',')}
                            onChange={handleFileChange}
                            style={{ display: 'none' }}
                            aria-hidden="true"
                          />
                          {uploadStatus && (
                            <span style={{ fontSize: '0.8rem', color: 'var(--color-primary)' }}>
                              {uploadStatus}
                            </span>
                          )}
                        </div>
                        {/* 状态筛选标签 */}
                        <div style={{ display: 'flex', gap: 'var(--space-xs)' }}>
                          {STATUS_FILTERS.map((filter) => (
                            <button
                              key={filter.key}
                              className={`filter-pill${statusFilter === filter.key ? ' filter-pill-active' : ''}`}
                              onClick={() => setStatusFilter(filter.key)}
                            >
                              {filter.label}
                            </button>
                          ))}
                        </div>
                      </div>

                      {/* 笔记列表（批次 E8：时间线）。
                          原来每条是 `div.card.card-hover`（白底 + 边框 + 阴影），
                          一屏七八条时边框噪音很大；§C3「每日材料」行要的是
                          **单栏时间线 + 留白分隔 + 元数据收进 chip**（Memos 的减法）。
                          结构与几何见 `DailyMaterials.module.css`。 */}
                      {filteredNotes.length === 0 ? (
                        <EmptyState
                          message={statusFilter !== 'all' ? '没有符合筛选条件的文件' : '文件夹为空'}
                          description={
                            statusFilter !== 'all' ? undefined : '点击上方按钮上传学习资料'
                          }
                        />
                      ) : (
                        <div className={styles.materialsTimeline}>
                          {filteredNotes.map((note) => (
                            /* ⚠️ 这一行原来是 `div.card.card-hover[role="button"][tabIndex=0]`
                               + 一个只认 `Enter` 的 `onKeyDown` —— axe **报不出来**
                               （它只看"可聚焦元素里嵌可聚焦元素"，这一行里没有可聚焦后代），
                               但 Tab 会停在一个"不是按钮的按钮"上，而且 Space 不生效。
                               改法与同一页的文件夹头（F-30）逐字同形：外层回到"盒子"，
                               行为落在**真链接**上（标题）—— 进笔记是导航，链接比按钮更准
                               （可右键、可新标签页、Tab 一次即达）。
                               批次 E8 只换了"盒子"的样式（卡片 → 时间线项），
                               控件与键盘行为一个字没动。 */
                            <div key={note.id} className={styles.materialsTimelineItem}>
                              <div className={styles.materialsTimelineBody}>
                                {/* `h3` 而不是 `h4`：这一页的大纲是 h1「今日资料」→
                                    h2（文件夹名，见上）→ h3（文件夹里的资料），
                                    `h4` 会让 h2 与 h4 之间缺一级。字号 0.9rem/字重 500
                                    本来就显式钉着（现在钉在模块里），所以**一个像素都没动**。 */}
                                <h3 className={styles.materialsNoteTitle}>
                                  <Link
                                    to={`/notes/${note.id}`}
                                    className={styles.materialsNoteLink}
                                  >
                                    {note.title}
                                  </Link>
                                </h3>
                                <div className={styles.materialsMetaRow}>
                                  {/* 来源类型标签 */}
                                  <span className={`badge badge-${note.source_type}`}>
                                    {sourceTypeLabels[note.source_type] || note.source_type}
                                  </span>
                                  {/* 处理状态标签 */}
                                  <span className={statusClass(note.status)}>
                                    {statusLabels[note.status] || note.status}
                                  </span>
                                  {/* 文件大小 / 上传时间（批次 E8：收进 chip） */}
                                  <span className={styles.materialsMetaChip}>
                                    {formatFileSize(note.file_size)}
                                  </span>
                                  <span className={styles.materialsMetaChip}>
                                    {new Date(note.created_at).toLocaleTimeString('zh-CN', {
                                      hour: '2-digit',
                                      minute: '2-digit',
                                    })}
                                  </span>
                                </div>
                              </div>
                              {/* 批次 E8：行尾的 `→`（Unicode U+2192）换成自绘 `chevron`
                                  —— 那个字符在中文字体里宽度随字号变、基线还偏低；
                                  图标是 aria-hidden 的纯装饰（真链接在标题上）。 */}
                              <span className={styles.materialsGoIcon} aria-hidden="true">
                                <Icon name="chevron" size={16} />
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 删除文件夹的确认框（批次 D3）：文案逐字保留原来的 `confirm()` 参数。
          `onConfirm` 先关框再执行（见 `ConfirmDialog` 文件头），取消什么都不做。 */}
      <ConfirmDialog
        open={pendingDeleteFolderId !== null}
        title="确定删除此文件夹？"
        confirmText="删除"
        danger
        onConfirm={() => {
          const folderId = pendingDeleteFolderId;
          setPendingDeleteFolderId(null);
          if (folderId === null) return;
          void performDeleteFolder(folderId);
        }}
        onCancel={() => setPendingDeleteFolderId(null)}
      />
    </div>
  );
}
