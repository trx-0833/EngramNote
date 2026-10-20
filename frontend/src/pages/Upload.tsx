/**
 * @file 上传页面
 * @description 文件上传页面，支持拖拽和点击两种上传方式。
 * 上传后自动轮询后端转换状态，转换完成后自动跳转到笔记详情页。
 * 支持的文件格式：PDF、图片、Office 文档、音视频文件。
 */
import { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  prepareUpload,
  commitUpload,
  getUploadStatus,
  getNotes,
  getProjects,
  type Note,
  type Project,
  type PreparedUpload,
} from '../api/client';
// 本页私有样式（overhaul-plan 5.6 第三批）：`.upload-zone*` 从 `src/styles/learning.css`
// 拆出，连同被哈希的 `@keyframes glowPulse`（改名 `uploadGlowPulse`）—— 见文件头
import styles from './Upload.module.css';
// 页面标题（visual-refactor-plan 批次 C1）：字号本就 1.5rem，观感不变
import PageHeader from '../components/PageHeader';
import Icon from '../components/Icon';
import { useTranslation } from '../i18n';

/** 允许上传的文件扩展名列表，与后端支持的格式保持一致 */
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

/** 解析后端选项 */
const BACKEND_OPTIONS = [
  { value: '', labelKey: 'upload.parseAuto', descKey: 'upload.parseAutoHint' },
  { value: 'pipeline', labelKey: 'upload.parseLocal', descKey: 'upload.parseLocalHint' },
  { value: 'vlm-http-client', labelKey: 'upload.parseCloud', descKey: 'upload.parseCloudHint' },
] as const;

/** 笔记角色选项 */
const NOTE_ROLE_OPTIONS = [
  { value: 'material', labelKey: 'labels.noteRole.material' },
  { value: 'personal_note', labelKey: 'labels.noteRole.personal_note' },
] as const;

/**
 * 上传页面组件
 *
 * 数据流：
 * 1. 用户选择/拖拽文件 → 前端校验文件格式
 * 2. 调用 uploadFile() 上传文件到后端
 * 3. 上传成功后获取 noteId，开始轮询转换状态
 * 4. 转换完成后自动跳转到笔记详情页
 *
 * 状态管理：
 * - dragActive: 拖拽区域是否激活（文件悬停时高亮）
 * - uploading: 是否正在上传/转换中
 * - error: 错误提示信息
 * - noteId: 上传成功后返回的笔记 ID
 * - status: 当前处理状态文本，用于界面展示
 */
export default function Upload() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  /** 隐藏的文件输入框引用，用于点击上传区域时触发文件选择 */
  const fileInputRef = useRef<HTMLInputElement>(null);
  /** 拖拽区域是否激活，用于高亮显示拖拽反馈 */
  const [dragActive, setDragActive] = useState(false);
  /** 是否正在上传/转换中，控制按钮禁用和界面状态 */
  const [uploading, setUploading] = useState(false);
  /** 错误提示信息 */
  const [error, setError] = useState('');
  /** 上传成功后返回的笔记 ID，用于轮询状态和跳转 */
  const [noteId, setNoteId] = useState<string | null>(null);
  /** 当前处理状态文本，展示给用户 */
  const [status, setStatus] = useState<string | null>(null);
  /** 解析后端选择，空字符串表示使用后端默认配置 */
  const [parseBackend, setParseBackend] = useState('');
  /** 笔记角色选择，默认为学习资料 */
  const [noteRole, setNoteRole] = useState('material');
  /** 项目列表 */
  const [projects, setProjects] = useState<Project[]>([]);
  /** 选中的项目标签 ID 数组（空表示不归属任何项目） */
  const [selectedProjectIds, setSelectedProjectIds] = useState<string[]>([]);
  /** 可关联的学习资料列表（仅 personal_note 时加载） */
  const [availableMaterials, setAvailableMaterials] = useState<Note[]>([]);
  /** 已选中的关联资料 ID 列表 */
  const [selectedMaterialIds, setSelectedMaterialIds] = useState<string[]>([]);
  /** 两阶段上传阶段 1 的暂存结果（prepare 成功后的临时文件信息） */
  const [prepared, setPrepared] = useState<PreparedUpload | null>(null);
  /** 重命名后的文件名输入 */
  const [renameName, setRenameName] = useState('');
  /** 重命名输入的内联校验提示 */
  const [renameError, setRenameError] = useState('');
  /** 裁剪页码范围输入 */
  const [cropPageRange, setCropPageRange] = useState('');
  /** 裁剪输入的内联校验提示 */
  const [cropError, setCropError] = useState('');
  /** 轮询定时器引用，组件卸载时清理，见 docs/decisions.md#F-15 */
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 卸载时清理轮询定时器，避免卸载后 setState/导航，见 docs/decisions.md#F-15
  useEffect(() => {
    return () => {
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current);
      }
    };
  }, []);

  // 加载项目列表，默认不选择项目（留空则不归属任何项目）
  useEffect(() => {
    const loadProjects = async () => {
      try {
        const data = await getProjects();
        setProjects(data);
        setSelectedProjectIds([]);
      } catch (err) {
        console.error('加载项目列表失败:', err);
      }
    };
    loadProjects();
  }, []);

  // 当选择"我的笔记"角色时加载可关联的学习资料（派生状态重置豁免）
  useEffect(() => {
    if (noteRole === 'personal_note') {
      const loadMaterials = async () => {
        try {
          const data = await getNotes(1, 100, undefined, 'material');
          setAvailableMaterials(data.items || []);
        } catch (err) {
          console.error('加载资料列表失败:', err);
        }
      };
      loadMaterials();
    } else {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSelectedMaterialIds([]);
    }
  }, [noteRole]);

  /**
   * 处理文件选择（两阶段上传阶段 1：prepare）
   * 校验文件格式后调用 prepareUpload 暂存文件；PDF 返回页数并展示裁剪配置，
   * 非 PDF 直接进入 commit 提交。
   *
   * @param file - 用户选择的文件对象
   */
  async function handleUpload(file: File) {
    // 提取文件扩展名并校验格式
    const ext = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      setError(t('upload.unsupportedFormat', { ext }));
      return;
    }

    setError('');
    setRenameError('');
    setCropError('');
    setRenameName('');
    setCropPageRange('');
    setPrepared(null);
    setUploading(true);
    setStatus(t('upload.uploading'));

    try {
      // 阶段 1：暂存文件，获取页数等信息
      const result = await prepareUpload(file);
      // 统一进入上传设置步骤（可重命名文件；PDF 额外支持按页裁剪）
      setRenameName(result.filename);
      setPrepared(result);
      setUploading(false);
      setStatus(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('upload.uploadFailed'));
      setUploading(false);
    }
  }

  /**
   * 校验页码范围表达式格式（1-based，支持 a、a-b、逗号组合，不越界）
   *
   * @param spec - 页码范围表达式，如 "1-20,25,30-32"
   * @param pageCount - PDF 总页数
   * @returns 空字符串表示合法，否则返回错误提示
   */
  function validatePageSpec(spec: string, pageCount: number): string {
    const trimmed = spec.trim();
    if (!trimmed) return t('upload.cropRangeEmpty');
    const items = trimmed.split(',');
    const pageRangeRe = /^(\d+)(?:-(\d+))?$/;
    const pages: number[] = [];
    for (const item of items) {
      const match = pageRangeRe.exec(item.trim());
      if (!match) return t('upload.cropRangeInvalid', { item: item.trim() });
      const start = parseInt(match[1], 10);
      const end = match[2] !== undefined ? parseInt(match[2], 10) : start;
      if (start < 1 || end < 1) return t('upload.cropRangeStartFromOne', { item: item.trim() });
      if (start > pageCount || end > pageCount)
        return t('upload.cropRangeOutOfBounds', { count: pageCount, item: item.trim() });
      if (start > end) return t('upload.cropRangeOrder', { item: item.trim() });
      for (let p = start; p <= end; p++) pages.push(p);
    }
    if (pages.length === 0) return t('upload.cropRangeEmpty');
    return '';
  }

  /**
   * 校验并确定最终文件名
   * 规则：非空、不含路径分隔符；若缺少原文件扩展名则自动补全（扩展名必须保留，
   * 与后端校验一致）。
   *
   * @param name - 用户输入的文件名
   * @param originalFilename - 原始文件名（用于提取扩展名）
   * @returns 空字符串表示合法并返回最终文件名；否则返回错误提示
   */
  function validateRename(name: string, originalFilename: string): { name: string; error: string } {
    const trimmed = name.trim();
    if (!trimmed) return { name: '', error: t('upload.fileNameRequired') };
    if (trimmed.includes('/') || trimmed.includes('\\')) {
      return { name: '', error: t('upload.fileNameNoSeparator') };
    }
    const dotIndex = originalFilename.lastIndexOf('.');
    const ext = dotIndex >= 0 ? originalFilename.slice(dotIndex) : '';
    const finalName =
      ext && !trimmed.toLowerCase().endsWith(ext.toLowerCase()) ? `${trimmed}${ext}` : trimmed;
    return { name: finalName, error: '' };
  }

  /**
   * 校验上传设置并提交
   * 校验文件名（及 PDF 页码范围）后调用 commitPrepared 正式上传。
   *
   * @param cropRange - 页码范围表达式（可选，仅 PDF；undefined 表示不裁剪）
   */
  function confirmUpload(cropRange: string | undefined) {
    if (!prepared) return;
    const { name, error: renameErr } = validateRename(renameName, prepared.filename);
    if (renameErr) {
      setRenameError(renameErr);
      return;
    }
    if (prepared.source_type === 'pdf' && cropRange !== undefined) {
      const pageCount = prepared.page_count ?? 0;
      const cropErr = validatePageSpec(cropRange, pageCount);
      if (cropErr) {
        setCropError(cropErr);
        return;
      }
    }
    commitPrepared(prepared.temp_id, cropRange, name);
  }

  /**
   * 两阶段上传阶段 2：commit
   * 携带可选重命名与裁剪范围调用 commitUpload 正式上传，成功后开始轮询转换状态。
   *
   * @param tempId - prepare 返回的临时上传标识
   * @param cropRange - 页码范围表达式（可选，仅 PDF）
   * @param finalName - 最终文件名（重命名后的，含扩展名）
   */
  async function commitPrepared(tempId: string, cropRange: string | undefined, finalName: string) {
    setError('');
    setUploading(true);
    setStatus(t('upload.uploading'));

    try {
      const note = await commitUpload(tempId, {
        filename: finalName,
        backend: parseBackend || undefined,
        note_role: noteRole,
        project_ids: selectedProjectIds.length > 0 ? selectedProjectIds : undefined,
        linked_material_ids: selectedMaterialIds,
        crop_page_range: cropRange,
      });
      setNoteId(note.id);
      setPrepared(null);
      setStatus(t('upload.uploadedConverting'));

      // 开始轮询转换状态
      pollStatus(note.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('upload.uploadFailed'));
      setUploading(false);
    }
  }

  /**
   * 轮询笔记转换状态
   * 每 5 秒查询一次后端状态，直到转换完成、失败或超时。
   *
   * - setTimeout 递归替代 setInterval，避免慢网络下请求重叠
   * - 终态集合补全 cleaning_failed / learning_failed
   * - timer 存 ref，组件卸载时清理，见 docs/decisions.md#F-15
   *
   * @param id - 笔记 ID
   */
  async function pollStatus(id: string) {
    const maxAttempts = 120; // 最多轮询 120 次（约 10 分钟）
    let attempts = 0;
    let checking = false; // 在途请求互斥

    const check = async () => {
      if (checking) return;
      checking = true;
      attempts++;
      let isTerminal = false;
      try {
        const res = await getUploadStatus(id);
        setStatus(t('upload.convertingStatus', { status: res.status }));

        // 成功终态
        if (res.status === 'converted' || res.status === 'cleaned' || res.status === 'archived') {
          isTerminal = true;
          setUploading(false);
          setStatus(t('upload.convertDone'));
          // 延迟 1 秒后自动跳转到笔记详情页
          setTimeout(() => navigate(`/notes/${id}`), 1000);
        } else if (
          res.status === 'failed' ||
          res.status === 'cleaning_failed' ||
          res.status === 'learning_failed'
        ) {
          // 补全失败终态（旧实现漏掉 cleaning_failed/learning_failed，会空转到超时），见 docs/decisions.md#F-15
          isTerminal = true;
          setUploading(false);
          setError(res.error_message || t('upload.processFailed', { status: res.status }));
        }
      } catch {
        // 轮询过程中的网络错误，继续尝试
      } finally {
        checking = false;
      }

      if (isTerminal) return;
      if (attempts >= maxAttempts) {
        // 超时处理
        setUploading(false);
        setError(t('upload.processTimeout'));
        return;
      }
      // 递归调度下一次（等本次完成后再计时，避免重叠）
      pollTimerRef.current = setTimeout(check, 5000);
    };

    check();
  }

  /**
   * 处理文件拖放
   * 获取拖放的第一个文件并触发上传。
   */
  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragActive(false);

    const files = e.dataTransfer.files;
    if (files.length > 0) {
      handleUpload(files[0]);
    }
  }

  /**
   * 处理拖拽悬停
   * 阻止默认行为并激活拖拽高亮。
   */
  function handleDragOver(e: React.DragEvent) {
    e.preventDefault();
    setDragActive(true);
  }

  /**
   * 处理拖拽离开
   * 取消拖拽高亮状态。
   */
  function handleDragLeave() {
    setDragActive(false);
  }

  /**
   * 处理文件选择框变化
   * 获取选择的第一个文件并触发上传。
   */
  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files;
    if (files && files.length > 0) {
      handleUpload(files[0]);
    }
  }

  return (
    /* 批次 E8：去掉了内联的 `margin: '0 auto'` —— 页宽三档（批次 C2）之后
       居中由 `App.module.css` 的 `.main` 负责，这一条是 C2 之前自己居中的遗留。 */
    <div className="page-enter">
      {/* 页面标题（批次 C1：统一进 <PageHeader>，字号本就 1.5rem） */}
      <PageHeader title={t('upload.title')} />

      {/* 拖拽上传区：支持点击和拖拽两种方式。
          ⚠️ 原来这里整个容器是 `div[role="button"][tabIndex=0]` + 一个只认 `Enter`
          的 `onKeyDown` —— 与 F-09 / F-30 / F-34 是同一个洞的又一处入口
          （axe 报不出来：它里面没有可聚焦后代，所以没有 `nested-interactive`）。
          Tab 会停在一个"不是按钮的按钮"上，而且 **Space 不生效**。
          现在它是**真 `<button>`**：Enter 与 Space 都生效，读屏也会念"按钮"。
          拖放事件仍挂在同一个元素上（拖放落在哪儿与改动前逐字相同）。
          外观不用复位：`.uploadZone` 已经把 border/background/padding/text-align
          全写好了（作者的类选择器胜过按钮的 UA 默认值），只需要把
          font / line-height / color 显式继承过来 —— 否则按钮会换回系统字体。 */}
      <button
        type="button"
        className={`${styles.uploadZone}${dragActive ? ` ${styles.uploadZoneActive}` : ''}`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => fileInputRef.current?.click()}
        aria-label={t('upload.dropZoneLabel')}
        style={{
          display: 'block',
          width: '100%',
          fontFamily: 'inherit',
          lineHeight: 'inherit',
          color: 'inherit',
          cursor: uploading ? 'wait' : 'pointer',
        }}
      >
        <span
          style={{
            display: 'block',
            marginBottom: 'var(--space-sm)',
            color: uploading ? 'var(--color-text-secondary)' : 'var(--color-primary)',
          }}
        >
          {/* 批次 B3：拖拽区补图标 —— 平时是 `upload`（托盘 + 向上箭头），
              上传中换成 `processing`（静止的 300° 进度环，**不是动画**：
              这一页的"在动"由文案与 `cursor: wait` 表达，再来一个转圈会打架）。 */}
          <Icon name={uploading ? 'processing' : 'upload'} size={24} />
        </span>
        <span
          style={{
            display: 'block',
            fontSize: '1.125rem',
            fontWeight: 500,
            marginBottom: 'var(--space-sm)',
          }}
        >
          {uploading ? t('upload.dropZoneBusy') : t('upload.dropZoneIdle')}
        </span>
        <span
          style={{ display: 'block', fontSize: '0.875rem', color: 'var(--color-text-secondary)' }}
        >
          {t('upload.supportedFormats')}
        </span>
        {/* 显示所有支持的文件扩展名 */}
        <span
          style={{
            display: 'block',
            fontSize: '0.75rem',
            color: 'var(--color-text-secondary)',
            marginTop: 'var(--space-xs)',
          }}
        >
          {ALLOWED_EXTENSIONS.join(' ')}
        </span>
      </button>

      {/* 隐藏的文件输入框，通过 ref 触发。放在按钮**外面**：
          按钮的内容模型只允许 phrasing content，`<input>` 嵌在里面是非法结构 */}
      <input
        ref={fileInputRef}
        type="file"
        accept={ALLOWED_EXTENSIONS.join(',')}
        onChange={handleFileChange}
        style={{ display: 'none' }}
        aria-hidden="true"
      />

      {/* 上传设置（prepare 完成后显示，可重命名文件；PDF 额外支持按页裁剪） */}
      {prepared && (
        <div className={`card ${styles.uploadPanel}`}>
          <p className={styles.uploadPanelTitle}>
            {prepared.source_type === 'pdf'
              ? t('upload.settingsPdf', { count: prepared.page_count ?? '?' })
              : t('upload.settings')}
          </p>

          {/* 文件名（可修改） */}
          <p
            style={{
              fontSize: '0.75rem',
              color: 'var(--color-text-secondary)',
              marginBottom: 'var(--space-xs)',
            }}
          >
            {t('upload.fileName')}
          </p>
          <input
            type="text"
            value={renameName}
            onChange={(e) => {
              setRenameName(e.target.value);
              setRenameError('');
            }}
            disabled={uploading}
            style={{
              width: '100%',
              padding: 'var(--space-sm)',
              borderRadius: 'var(--radius-sm)',
              border: `1px solid ${renameError ? 'var(--color-error)' : 'var(--color-border)'}`,
              marginBottom: 'var(--space-xs)',
            }}
            aria-label={t('upload.fileName')}
          />
          {renameError && (
            <p
              style={{
                fontSize: '0.75rem',
                color: 'var(--color-error)',
                marginBottom: 'var(--space-xs)',
              }}
              role="alert"
            >
              {renameError}
            </p>
          )}

          {/* PDF 按页裁剪配置（仅 PDF 显示） */}
          {prepared.source_type === 'pdf' && (
            <>
              <p
                style={{
                  fontSize: '0.75rem',
                  color: 'var(--color-text-secondary)',
                  marginBottom: 'var(--space-xs)',
                  marginTop: 'var(--space-sm)',
                }}
              >
                {t('upload.cropHint')}
              </p>
              <input
                type="text"
                value={cropPageRange}
                onChange={(e) => {
                  setCropPageRange(e.target.value);
                  setCropError('');
                }}
                placeholder={t('upload.cropPlaceholder')}
                disabled={uploading}
                style={{
                  width: '100%',
                  padding: 'var(--space-sm)',
                  borderRadius: 'var(--radius-sm)',
                  border: `1px solid ${cropError ? 'var(--color-error)' : 'var(--color-border)'}`,
                }}
                aria-label={t('upload.cropRangeLabel')}
              />
              {cropError && (
                <p
                  style={{
                    fontSize: '0.75rem',
                    color: 'var(--color-error)',
                    marginBottom: 'var(--space-xs)',
                  }}
                  role="alert"
                >
                  {cropError}
                </p>
              )}
            </>
          )}

          <div
            style={{
              display: 'flex',
              gap: 'var(--space-sm)',
              flexWrap: 'wrap',
              marginTop: 'var(--space-md)',
            }}
          >
            {prepared.source_type === 'pdf' ? (
              <>
                <button
                  className="btn"
                  disabled={uploading}
                  onClick={() => confirmUpload(undefined)}
                >
                  {t('upload.cropSkip')}
                </button>
                <button
                  className="btn btn-primary"
                  disabled={uploading}
                  onClick={() => confirmUpload(cropPageRange)}
                >
                  {t('upload.cropApply')}
                </button>
              </>
            ) : (
              <button
                className="btn btn-primary"
                disabled={uploading}
                onClick={() => confirmUpload(undefined)}
              >
                {t('upload.confirmUpload')}
              </button>
            )}
          </div>
        </div>
      )}

      {/* 解析方式选择 */}
      <div className={`card ${styles.uploadPanel}`}>
        <p className={styles.uploadPanelTitle}>{t('upload.parseMode')}</p>
        <div style={{ display: 'flex', gap: 'var(--space-sm)', flexWrap: 'wrap' }}>
          {BACKEND_OPTIONS.map((option) => (
            <button
              key={option.value}
              className={`filter-pill${parseBackend === option.value ? ' filter-pill-active' : ''}`}
              onClick={() => setParseBackend(option.value)}
              disabled={uploading}
              title={t(option.descKey)}
            >
              {t(option.labelKey)}
            </button>
          ))}
        </div>
        <p
          style={{
            fontSize: '0.75rem',
            color: 'var(--color-text-secondary)',
            marginTop: 'var(--space-xs)',
          }}
        >
          {(() => {
            const opt = BACKEND_OPTIONS.find((o) => o.value === parseBackend);
            return opt ? t(opt.descKey) : null;
          })()}
        </p>
      </div>

      {/* 项目标签选择（支持多选） */}
      <div className={`card ${styles.uploadPanel}`}>
        <p className={styles.uploadPanelTitle}>{t('upload.projects')}</p>
        {projects.length > 0 ? (
          <div style={{ display: 'flex', gap: 'var(--space-sm)', flexWrap: 'wrap' }}>
            {projects.map((project) => {
              const checked = selectedProjectIds.includes(project.id);
              return (
                <button
                  key={project.id}
                  type="button"
                  className={`filter-pill${checked ? ' filter-pill-active' : ''}`}
                  onClick={() => {
                    setSelectedProjectIds((prev) =>
                      checked ? prev.filter((id) => id !== project.id) : [...prev, project.id],
                    );
                  }}
                  disabled={uploading}
                >
                  {project.name}
                </button>
              );
            })}
          </div>
        ) : (
          <p style={{ fontSize: '0.75rem', color: 'var(--color-text-secondary)' }}>
            {t('upload.noProjects')}
          </p>
        )}
        <p
          style={{
            fontSize: '0.75rem',
            color: 'var(--color-text-secondary)',
            marginTop: 'var(--space-xs)',
          }}
        >
          {t('upload.projectsHint')}
        </p>
      </div>

      {/* 笔记角色选择 */}
      <div className={`card ${styles.uploadPanel}`}>
        <p className={styles.uploadPanelTitle}>{t('upload.noteType')}</p>
        <div style={{ display: 'flex', gap: 'var(--space-sm)', flexWrap: 'wrap' }}>
          {NOTE_ROLE_OPTIONS.map((option) => (
            <button
              key={option.value}
              className={`filter-pill${noteRole === option.value ? ' filter-pill-active' : ''}`}
              onClick={() => setNoteRole(option.value)}
              disabled={uploading}
            >
              {t(option.labelKey)}
            </button>
          ))}
        </div>
      </div>

      {/* 关联学习资料（仅 personal_note 时显示） */}
      {noteRole === 'personal_note' && availableMaterials.length > 0 && (
        <div className={`card ${styles.uploadPanel}`}>
          <p className={styles.uploadPanelTitle}>{t('upload.relatedMaterials')}</p>
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 'var(--space-xs)',
              maxHeight: '200px',
              overflowY: 'auto',
            }}
          >
            {availableMaterials.map((m) => (
              <label
                key={m.id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 'var(--space-xs)',
                  cursor: 'pointer',
                }}
              >
                <input
                  type="checkbox"
                  checked={selectedMaterialIds.includes(m.id)}
                  onChange={(e) => {
                    setSelectedMaterialIds((prev) =>
                      e.target.checked ? [...prev, m.id] : prev.filter((id) => id !== m.id),
                    );
                  }}
                  disabled={uploading}
                  // 覆盖全局 input{width:100%}，否则 checkbox 撑满整行、标题被挤成 0 宽
                  style={{ width: 'auto', margin: 0, padding: 0, flexShrink: 0 }}
                />
                <span style={{ flex: 1, minWidth: 0 }}>{m.title}</span>
              </label>
            ))}
          </div>
        </div>
      )}

      {/* 处理状态反馈 */}
      {status && (
        <div className="card" style={{ marginTop: 'var(--space-md)', textAlign: 'center' }}>
          <p style={{ color: 'var(--color-primary)' }}>{status}</p>
        </div>
      )}

      {/* 错误提示 */}
      {error && (
        <div className="card" style={{ marginTop: 'var(--space-md)', textAlign: 'center' }}>
          <p role="alert" style={{ color: 'var(--color-error)' }}>
            {error}
          </p>
        </div>
      )}

      {/* 上传完成后的跳转按钮（当自动跳转未生效时的备用入口） */}
      {noteId && !uploading && !error && (
        <div style={{ marginTop: 'var(--space-md)', textAlign: 'center' }}>
          <button className="btn btn-primary" onClick={() => navigate(`/notes/${noteId}`)}>
            {t('upload.viewNote')}
          </button>
        </div>
      )}
    </div>
  );
}
