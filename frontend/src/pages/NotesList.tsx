/**
 * @file 笔记列表页面
 * @description 展示用户所有笔记的列表页面，支持：
 * 1. 按标题关键词搜索
 * 2. 分页浏览（每页 20 条）
 * 3. 删除笔记（带确认提示）
 * 4. 点击笔记卡片跳转到详情页
 */
import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { getNotes, getArchivedNotes, deleteNote, retryConvert, type Note } from '../api/client';
import { DeleteNoteDialog } from '../components/DeleteNoteDialog';
import Icon from '../components/Icon';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import ErrorDisplay from '../components/ErrorDisplay';
import { sourceTypeLabels, statusLabels, statusClass } from '../utils/labels';
// 页面标题（visual-refactor-plan 批次 C1）：1.25rem → 1.5rem
import PageHeader from '../components/PageHeader';
import { useToast } from '../components/Toast';
// 本页私有样式（overhaul-plan 5.6 第三批 + 序 8）：`.search-input-*` 从 `src/styles/learning.css`
// 拆出，`.list-toolbar` 的两条 480px 规则从 `src/styles/responsive.css` 一起搬进来
// （类名哈希后写在全局补丁层里的选择器会永远选不中）；序 8 再把
// `.note-list-item` / `.note-list-actions`（含 768px 档 3 条）从
// `components.css` + `responsive.css` 搬进来 —— 见 NotesList.module.css 文件头
import styles from './NotesList.module.css';

/**
 * 笔记列表页面组件
 *
 * 数据流：
 * 1. 组件挂载或 page/keyword 变化时，调用 getNotes() 获取笔记列表
 * 2. 展示笔记卡片列表，支持搜索和分页
 * 3. 删除操作：确认后调用 deleteNote()，成功后从本地状态中移除该笔记
 *
 * 状态管理：
 * - notes: 当前页的笔记列表
 * - total: 笔记总数，用于计算分页
 * - page: 当前页码
 * - keyword: 搜索关键词，输入时自动重置到第 1 页
 * - loading: 数据加载状态
 */
export default function NotesList() {
  const toast = useToast();
  const navigate = useNavigate();
  /** 当前页的笔记列表 */
  const [notes, setNotes] = useState<Note[]>([]);
  /** 笔记总数，用于计算总页数 */
  const [total, setTotal] = useState(0);
  /** 当前页码（从 1 开始） */
  const [page, setPage] = useState(1);
  /** 搜索关键词 */
  const [keyword, setKeyword] = useState('');
  /** 当前笔记角色筛选：material=学习资料，personal_note=我的笔记 */
  const [noteRole, setNoteRole] = useState<'material' | 'personal_note'>('material');
  /** 各角色下是否只显示已审阅笔记，保持两个 Tab 下已审阅筛选独立 */
  const [archivedByRole, setArchivedByRole] = useState<Record<string, boolean>>({
    material: false,
    personal_note: false,
  });
  /** 数据加载状态 */
  const [loading, setLoading] = useState(true);
  /** 错误信息 */
  const [error, setError] = useState('');
  /** 待移入回收站的笔记（打开删除确认弹窗） */
  const [noteToDelete, setNoteToDelete] = useState<Note | null>(null);

  /** 每页显示条数，固定为 20 */
  const pageSize = 20;

  // 当页码或关键词或筛选条件变化时重新获取笔记列表
  const showArchived = archivedByRole[noteRole];
  const fetchNotes = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = showArchived
        ? await getArchivedNotes(page, pageSize, noteRole)
        : await getNotes(page, pageSize, keyword || undefined, noteRole);
      setNotes(res.items);
      setTotal(res.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [page, keyword, noteRole, showArchived]);

  // 挂载/参数变化时加载数据（数据获取型 effect，同步 setState 豁免）
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchNotes();
  }, [page, keyword, noteRole, showArchived, fetchNotes]);

  /**
   * 处理删除笔记（移入回收站）
   * 打开确认弹窗（含关联统计），确认后调用 API 软删除，成功后从本地状态中移除。
   *
   * 这里**不再需要** `e.stopPropagation()`：卡片本身已经不是控件了
   * （见下方笔记卡片处的说明），按钮的点击不会再冒泡到"整卡导航"上。
   * 依赖 stopPropagation 的日子一长，谁也不敢挪动这些按钮 —— 现在没有这层耦合。
   *
   * @param note - 要移入回收站的笔记
   */
  function handleDelete(note: Note) {
    setNoteToDelete(note);
  }

  /** 确认移入回收站：调用软删除 API，乐观更新本地列表 */
  async function confirmDelete() {
    if (!noteToDelete) return;
    try {
      await deleteNote(noteToDelete.id);
      // 乐观更新：从本地状态中移除已删除的笔记，无需重新请求列表
      setNotes((prev) => prev.filter((n) => n.id !== noteToDelete.id));
      setTotal((prev) => prev - 1);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '移入回收站失败');
    } finally {
      setNoteToDelete(null);
    }
  }

  /**
   * 处理重试转换失败的笔记
   * 调用 retryConvert API，成功后更新本地状态
   *
   * 与 `handleDelete` 同理：不再需要阻止冒泡（卡片已不是控件）。
   */
  async function handleRetry(noteId: string) {
    try {
      const result = await retryConvert(noteId);
      setNotes((prev) =>
        prev.map((n) =>
          n.id === noteId
            ? { ...n, status: result.status, error_message: result.error_message }
            : n,
        ),
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '重试失败');
    }
  }

  /** 计算总页数，用于分页控件 */
  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="page-enter">
      {/* 页面一级标题。
          为什么此前没有：这一页只有卡片标题（h3），整页没有 h1 ——
          axe 判 `page-has-heading-one`（F-18），屏幕阅读器也就没有
          "这是什么页"的答案。占位与搜索框同一行：这一页两行之间的空间
          本来就不宽裕，新起一行会把列表往下推，而标题与"共 N 条"是同一件事
          （页面身份 + 结果计数），视觉上本来就在一起。
          批次 C1：1.25rem → 1.5rem（**变大**，有意为之），量尺交给
          `<PageHeader>`；`spacing="sm"` 保持与下方工具条原有的 8px。
          导航名「笔记列表」与这里「笔记」的不一致属于产品语义，本批只登记不改。 */}
      <PageHeader title="笔记" spacing="sm" />

      {/* 搜索栏：输入关键词即时搜索，同时重置到第 1 页 */}
      <div
        className={styles.listToolbar}
        style={{
          display: 'flex',
          gap: 'var(--space-md)',
          marginBottom: 'var(--space-lg)',
          alignItems: 'center',
        }}
      >
        <div className={styles.searchInputWrapper}>
          {/* 批次 B3：这一处原本是全站**唯一**的放大镜（内联 `<svg>`，24 网格、
              线宽 2）；迁进唯一出口后线宽收到 1.5，`.searchInputIcon` 的
              定位规则与类名原样保留（`verify-built-css.mjs` 的切片标记里有它）。 */}
          <Icon name="search" size={16} className={styles.searchInputIcon} />
          <input
            type="search"
            placeholder="搜索笔记标题..."
            value={keyword}
            onChange={(e) => {
              setKeyword(e.target.value);
              setPage(1);
            }}
            aria-label="搜索笔记"
          />
        </div>
        <span style={{ color: 'var(--color-text-secondary)', fontSize: '0.875rem' }}>
          共 {total} 条
        </span>
      </div>

      {/* 笔记角色 Tab 切换：学习资料 / 我的笔记 */}
      <div
        style={{
          display: 'flex',
          gap: 'var(--space-sm)',
          marginBottom: 'var(--space-md)',
          flexWrap: 'wrap',
        }}
      >
        <button
          className={`filter-pill ${noteRole === 'material' ? 'filter-pill-active' : ''}`}
          onClick={() => {
            setNoteRole('material');
            setPage(1);
          }}
        >
          学习资料
        </button>
        <button
          className={`filter-pill ${noteRole === 'personal_note' ? 'filter-pill-active' : ''}`}
          onClick={() => {
            setNoteRole('personal_note');
            setPage(1);
          }}
        >
          我的笔记
        </button>
      </div>

      {/* 筛选标签：全部 / 已审阅 */}
      <div style={{ display: 'flex', gap: 'var(--space-sm)', marginBottom: 'var(--space-md)' }}>
        <button
          className={`filter-pill ${!archivedByRole[noteRole] ? 'filter-pill-active' : ''}`}
          onClick={() => {
            setArchivedByRole((prev) => ({ ...prev, [noteRole]: false }));
            setPage(1);
          }}
        >
          全部
        </button>
        <button
          className={`filter-pill ${archivedByRole[noteRole] ? 'filter-pill-active' : ''}`}
          onClick={() => {
            setArchivedByRole((prev) => ({ ...prev, [noteRole]: true }));
            setPage(1);
          }}
        >
          已审阅
        </button>
      </div>

      {/* 笔记列表 */}
      {loading ? (
        <LoadingSpinner />
      ) : error ? (
        <ErrorDisplay message={error} onRetry={fetchNotes} />
      ) : notes.length === 0 ? (
        /* 空状态：根据是否有搜索关键词显示不同提示 */
        <EmptyState
          message={keyword ? '没有找到匹配的笔记' : '还没有笔记'}
          description={keyword ? undefined : '上传你的第一份学习资料'}
          action={
            !keyword ? (
              <button className="btn btn-primary" onClick={() => navigate('/upload')}>
                {/* 批次 B3：空状态里的上传入口补 `upload` 图标 */}
                <Icon name="upload" size={16} />
                上传资料
              </button>
            ) : undefined
          }
        />
      ) : (
        /* 笔记卡片列表 */
        <div style={{ display: 'grid', gap: 'var(--space-md)' }}>
          {notes.map((note) => (
            /* 卡片本身**不是**控件：原来写的是 `article[role="button"][tabindex="0"]`
               ＋ 手写的 Enter 处理，而卡片里还有「重试」「删除」两个真按钮
               —— axe 判 nested-interactive（F-17），ARIA 也不允许 article 用
               button 角色（F-16）。改法与已修掉的 Sidebar 同形：**控件之间是兄弟**。
               标题现在就是真链接（可右键、可新标签页、Tab 一次即达），
               删除/重试是各自独立的按钮，靠 flex + `.note-list-actions` 仍排在右侧。 */
            <article key={note.id} className={`card card-hover ${styles.noteListItem}`}>
              <div style={{ flex: 1, minWidth: 0 }}>
                {/* `h2` 而不是 `h3`：这一页的顶层标题是上面的 h1「笔记」，
                    中间没有任何层级 —— h1 → h3 是跳级，axe 判 `heading-order`
                    （这正是补上 h1 之后必须一起做的事：F-18 修好、不能反手
                    多出一条层级违规）。字号本来就是显式写的，
                    改级别**不改外观**。 */}
                <h2
                  style={{ fontSize: '1.17rem', fontWeight: 500, marginBottom: 'var(--space-xs)' }}
                >
                  {/* 下划线显式关掉：`base.css` 现在给所有 <a> 默认下划线
                      （正文链接必须与正文可区分，F-13/F-26），而这里链接的
                      文本就是整张卡片的标题 —— 标题带下划线不是这个页面的观感，
                      而且它并不"嵌在正文里"，不落在那条规则的适用场景内。 */}
                  <Link
                    to={`/notes/${note.id}`}
                    style={{ color: 'inherit', textDecoration: 'none' }}
                  >
                    {note.title}
                  </Link>
                </h2>
                <div
                  style={{
                    display: 'flex',
                    gap: 'var(--space-sm)',
                    alignItems: 'center',
                    flexWrap: 'wrap',
                  }}
                >
                  {/* 来源类型标签 */}
                  <span className={`badge badge-${note.source_type}`}>
                    {sourceTypeLabels[note.source_type] || note.source_type}
                  </span>
                  {/* 所属项目标签（支持多标签）。
                      色值说明：底色是 --color-primary-soft 的兜底 #eef2ff，
                      而 --color-primary 在本项目**未定义**（base.css 里是 #0f3460），
                      于是这里落到兜底 #2563eb —— 与 #eef2ff 只有 4.62:1，
                      12px 小字压在 4.5 的门槛线上（a11y-audit F-25 的第三种成因）。
                      改用同色系的 #1b4fbf（5.49:1），色相不变、余量足够。 */}
                  {note.project_names?.map((name) => (
                    <span
                      key={name}
                      className="badge"
                      style={{
                        backgroundColor: 'var(--color-primary-soft, #eef2ff)',
                        color: '#1b4fbf',
                      }}
                    >
                      {name}
                    </span>
                  ))}
                  {/* 处理状态标签 */}
                  <span className={statusClass(note.status)} style={{ fontSize: '0.8rem' }}>
                    {statusLabels[note.status] || note.status}
                  </span>
                  {/* 页数信息，仅 PDF/Office 文档有值 */}
                  {note.page_count && (
                    <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                      {note.page_count} 页
                    </span>
                  )}
                  {/* 文件大小，从字节转换为 KB 显示 */}
                  <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                    {(note.file_size / 1024).toFixed(0)} KB
                  </span>
                  {/* 创建日期 */}
                  <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                    {new Date(note.created_at).toLocaleDateString('zh-CN')}
                  </span>
                </div>
                {/* 错误信息，仅 status 为 failed 时显示 */}
                {note.error_message && (
                  <p
                    style={{
                      color: 'var(--color-error)',
                      fontSize: '0.8rem',
                      marginTop: 'var(--space-xs)',
                    }}
                  >
                    {note.error_message}
                  </p>
                )}
              </div>
              {/* 操作按钮区域 */}
              <div className={styles.noteListActions}>
                {/* 重试按钮，仅 failed 状态显示 */}
                {note.status === 'failed' && (
                  <button
                    className="btn btn-primary"
                    style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                    onClick={() => handleRetry(note.id)}
                    aria-label={`重试 ${note.title}`}
                  >
                    重试
                  </button>
                )}
                {/* 删除按钮（移入回收站），需要阻止事件冒泡 */}
                <button
                  className="btn btn-danger"
                  style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                  onClick={() => handleDelete(note)}
                  aria-label={`删除 ${note.title}`}
                >
                  {/* 批次 B3：「删除」此前是全站纯文字按钮 —— 补 `delete` 图标 */}
                  <Icon name="delete" size={16} />
                  删除
                </button>
                <span
                  style={{ color: 'var(--color-text-secondary)', alignSelf: 'center' }}
                  aria-hidden="true"
                >
                  →
                </span>
              </div>
            </article>
          ))}
        </div>
      )}

      {/* 分页控件：仅在总页数大于 1 时显示 */}
      {totalPages > 1 && (
        <nav
          style={{
            display: 'flex',
            justifyContent: 'center',
            gap: 'var(--space-sm)',
            marginTop: 'var(--space-lg)',
          }}
          aria-label="分页"
        >
          <button
            className="btn btn-secondary"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            上一页
          </button>
          <span
            style={{
              alignSelf: 'center',
              fontSize: '0.875rem',
              color: 'var(--color-text-secondary)',
            }}
          >
            {page} / {totalPages}
          </span>
          <button
            className="btn btn-secondary"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </button>
        </nav>
      )}

      {/* 移入回收站确认弹窗 */}
      {noteToDelete && (
        <DeleteNoteDialog
          note={noteToDelete}
          onClose={() => setNoteToDelete(null)}
          onConfirm={confirmDelete}
        />
      )}
    </div>
  );
}
