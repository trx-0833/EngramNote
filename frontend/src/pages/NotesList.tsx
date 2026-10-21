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
import {
  getNotes,
  getArchivedNotes,
  deleteNote,
  retryConvert,
  exportNotesZip,
  type Note,
} from '../api/client';
import { DeleteNoteDialog } from '../components/DeleteNoteDialog';
import Icon from '../components/Icon';
import LoadingSpinner from '../components/LoadingSpinner';
import EmptyState from '../components/EmptyState';
import ErrorDisplay from '../components/ErrorDisplay';
import { statusClass, useLabels } from '../utils/labels';
// 日期**格式**的唯一出口（i18n 阶段 0）：本页此前自己写死 `'zh-CN'`
import { formatDate } from '../utils/datetime';
// 页面标题（visual-refactor-plan 批次 C1）：1.25rem → 1.5rem
import PageHeader from '../components/PageHeader';
import { useToast } from '../components/Toast';
// 本页私有样式（overhaul-plan 5.6 第三批 + 序 8）：`.search-input-*` 从 `src/styles/learning.css`
// 拆出，`.list-toolbar` 的两条 480px 规则从 `src/styles/responsive.css` 一起搬进来
// （类名哈希后写在全局补丁层里的选择器会永远选不中）；序 8 再把
// `.note-list-item` / `.note-list-actions`（含 768px 档 3 条）从
// `components.css` + `responsive.css` 搬进来 —— 见 NotesList.module.css 文件头
import styles from './NotesList.module.css';
import { useTranslation } from '../i18n';

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
  const { t } = useTranslation();
  const { sourceTypeLabels, statusLabels } = useLabels();
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
  /** 「导出全部」进行中（B4）—— 期间禁用按钮，防止连点生成多份 ZIP */
  const [exportingAll, setExportingAll] = useState(false);
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
      setError(err instanceof Error ? err.message : t('shared.loadFailed'));
    } finally {
      setLoading(false);
    }
    // `t` 进依赖：语言切换后重新加载时，错误文案要是新语言的
  }, [page, keyword, noteRole, showArchived, t]);

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
      toast.error(err instanceof Error ? err.message : t('notesList.moveToTrashFailed'));
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
      toast.error(err instanceof Error ? err.message : t('shared.retryFailed'));
    }
  }

  /**
   * 「导出全部为 ZIP」（B4，2026-10-22）
   *
   * ## 为什么这条才是"防锁定"的真正价值
   *
   * `docs/open-source-readiness.md` 写着：单篇导出已落地，但**批量导出
   * （整库搬走）与导入都还没有**，而「整库可搬走」才是这条真正的价值。
   * 导出的 ZIP 解压后可直接放进 Obsidian vault。
   *
   * ## 与单篇导出同一套下载写法
   *
   * `downloadRequest` 已经处理了"后端把错误写在 body 里但状态码不是 2xx"
   * 那种情况（详见 `api/client.ts` 的 `downloadRequest` 注释）。这里唯一
   * 不同的是文件名兜底：后端给的是 `engramnote-export.zip`，但它也可能缺失，
   * 所以保留一个兜底名。
   *
   * ## 失败时**透传后端文案**
   *
   * `NOTE_EXPORT_ZIP_EMPTY`（一篇都没有该版本正文）这种情况下，后端那句
   * "没有可导出的正文 —— 这些笔记都没有该版本的正文"比前端任何自造文案都准确
   * —— 它说清了**为什么**（版本不对）与**怎么办**（换版本或先清洗）。
   */
  async function handleExportAll() {
    setExportingAll(true);
    try {
      const { blob, filename } = await exportNotesZip('clean');
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename ?? 'engramnote-export.zip';
        document.body.appendChild(anchor);
        anchor.click();
        // 点击后立刻移除：`<a>` 只在这次点击里有用
        document.body.removeChild(anchor);
      } finally {
        // 不释放会漏一份文件内容在内存里（与单篇导出的处理一致）
        URL.revokeObjectURL(url);
      }
      toast.success(t('notesList.exportAllDone'));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('notesList.exportAllFailed'));
    } finally {
      setExportingAll(false);
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
      <PageHeader title={t('notesList.title')} spacing="sm" />

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
            placeholder={t('notesList.searchPlaceholder')}
            value={keyword}
            onChange={(e) => {
              setKeyword(e.target.value);
              setPage(1);
            }}
            aria-label={t('notesList.searchLabel')}
          />
        </div>
        <span style={{ color: 'var(--color-text-secondary)', fontSize: '0.875rem' }}>
          {t('notesList.totalCount', { count: total })}
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
          {t('notesList.filterMaterial')}
        </button>
        <button
          className={`filter-pill ${noteRole === 'personal_note' ? 'filter-pill-active' : ''}`}
          onClick={() => {
            setNoteRole('personal_note');
            setPage(1);
          }}
        >
          {t('notesList.filterPersonal')}
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
          {t('notesList.filterAll')}
        </button>
        <button
          className={`filter-pill ${archivedByRole[noteRole] ? 'filter-pill-active' : ''}`}
          onClick={() => {
            setArchivedByRole((prev) => ({ ...prev, [noteRole]: true }));
            setPage(1);
          }}
        >
          {t('notesList.filterArchived')}
        </button>
      </div>

      {/* ── 导出全部（B4，2026-10-22）──
          放在列表之上、筛选之下的位置：它是"对整个库"的动作，
          与上面那排"筛选"不是一类，与下面的"每行操作"也不是一类。

          只在**没有搜索关键词**时显示：搜索状态下"全部"到底指什么会变得
          歧义（全部库？还是搜索结果？），而这次导出的是**整个库**。
          用户搜索时看到这个按钮，会以为导的是筛出来的那些。

          ⚠️ 计划里明确把"批量选择 UI（勾选框）"列为配套、不在本轮，
          所以这里只有"导出全部"一个动作，没有"导出选中的 N 篇"。 */}
      {!keyword && (
        <div style={{ marginBottom: 'var(--space-md)' }}>
          <button className="btn btn-secondary" onClick={handleExportAll} disabled={exportingAll}>
            {/* 图标集里**没有**下载图标（`components/icons/` 只有 upload 等 40 个）。
                这里不新造一个：按钮文案「导出全部（ZIP）」已经把动作说清了，
                为一次导出加一个图标文件不值当 —— 而"新造图标"会让图标集
                多一个来源不明的资产。 */}
            {exportingAll ? t('notesList.exportingAll') : t('notesList.exportAll')}
          </button>
        </div>
      )}

      {/* 笔记列表 */}
      {loading ? (
        <LoadingSpinner />
      ) : error ? (
        <ErrorDisplay message={error} onRetry={fetchNotes} />
      ) : notes.length === 0 ? (
        /* 空状态：根据是否有搜索关键词显示不同提示 */
        <EmptyState
          message={keyword ? t('notesList.noMatch') : t('notesList.empty')}
          description={keyword ? undefined : t('notesList.emptyHint')}
          action={
            !keyword ? (
              <button className="btn btn-primary" onClick={() => navigate('/upload')}>
                {/* 批次 B3：空状态里的上传入口补 `upload` 图标 */}
                <Icon name="upload" size={16} />
                {t('notesList.upload')}
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
              {/* 批次 E2：左侧 3px 类型色条（借鉴表 §C3「笔记列表」行 · Trilium）。
                  色值**不在这里写**：它挂 `badge badge-<source_type>`，取的是
                  `components.css` 既有徽章色表里这一类型的前景色
                  （`background: currentColor`，见 NotesList.module.css）。
                  纯装饰、无文本，`aria-hidden` 把它排除在无障碍树之外 ——
                  "这是什么类型"仍由同一行的来源类型徽章对读屏用户交代。 */}
              <span
                className={`badge badge-${note.source_type} ${styles.noteTypeBar}`}
                aria-hidden="true"
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                {/* `h2` 而不是 `h3`：这一页的顶层标题是上面的 h1「笔记」，
                    中间没有任何层级 —— h1 → h3 是跳级，axe 判 `heading-order`
                    （这正是补上 h1 之后必须一起做的事：F-18 修好、不能反手
                    多出一条层级违规）。字号本来就是显式写的，
                    改级别**不改外观**。
                    批次 E2：`fontWeight` 500 → 600、并挂上 `.noteTitle`
                    （衬线）—— visual-design-spec §3.3 的卡片标题是"衬线 600"；
                    `fontSize: 1.17rem` 一字不动（它钉的是"级别 ≠ 大小"）。 */}
                <h2
                  className={styles.noteTitle}
                  style={{ fontSize: '1.17rem', fontWeight: 600, marginBottom: 'var(--space-xs)' }}
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
                {/* 批次 E2：这一行是"来源类型 · 状态 · 时间"的**一行元信息**。
                    项与项之间的 `·` 由 `.noteMeta > * + *::before` 生成
                    （CSS `content`，不往无障碍树里插文本），顺序与文案一项未动。
                    项目标签仍是胶囊，`.` 不会落进胶囊里。 */}
                <div
                  className={styles.noteMeta}
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
                      {t('notesList.pageCount', { count: note.page_count })}
                    </span>
                  )}
                  {/* 文件大小，从字节转换为 KB 显示 */}
                  <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                    {(note.file_size / 1024).toFixed(0)} KB
                  </span>
                  {/* 批次 E2：这一项从 `created_at` 改为 `updated_at`
                      （借鉴表 §C3「笔记列表」行点名的是"更新时间"）——
                      展示的字段换了、格式与位置一字未改，也没有新增文案
                      （这一项本来就没有标签）。字段缺失时回落到创建时间，
                      避免契约漂移时渲染出 "Invalid Date"。 */}
                  <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>
                    {formatDate(note.updated_at || note.created_at)}
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
                    aria-label={t('notesList.retryNamed', { title: note.title })}
                  >
                    {t('notesList.retry')}
                  </button>
                )}
                {/* 删除按钮（移入回收站），需要阻止事件冒泡 */}
                <button
                  className="btn btn-danger"
                  style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                  onClick={() => handleDelete(note)}
                  aria-label={t('notesList.deleteNamed', { title: note.title })}
                >
                  {/* 批次 B3：「删除」此前是全站纯文字按钮 —— 补 `delete` 图标 */}
                  <Icon name="delete" size={16} />
                  {t('notesList.delete')}
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
          aria-label={t('notesList.pagination')}
        >
          <button
            className="btn btn-secondary"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            {t('notesList.prevPage')}
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
            {t('notesList.nextPage')}
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
