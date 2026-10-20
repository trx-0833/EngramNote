/**
 * @file 中文消息表（**唯一权威**）
 *
 * ## 为什么这份是权威
 *
 * `messages.en.ts` 用 `satisfies typeof zhCN` 标注，所以**键的形状由本文件定义**：
 * 这里加一个键，英文那边不同步就编译失败。
 *
 * ## 组织方式
 *
 * 按**功能域**分顶层命名空间（`common` / `auth` / …），不用扁平的点号字符串。
 * 理由：嵌套对象让 `tsc` 能报出"少了一层"这种错误，而扁平字符串只能报
 * "这个字面量不在联合里" —— 后者在键有 1000+ 个时几乎读不出问题在哪。
 *
 * ## 阶段 0 的范围
 *
 * 本文件目前只有 `common` 与 `auth` 两个命名空间 —— 这是**阶段 0** 的范围
 * （基础设施 + 认证页跑通）。后续阶段按页面逐个补命名空间，
 * 每加一个域就同时补英文，`tsc` 会守住这一点。
 *
 * ## 为什么**不能**写 `as const`
 *
 * 第一版写了，`tsc` 立刻报 12 个错：`as const` 会把每个值收成**字面量类型**
 * （`loginSubmit` 的类型变成 `'登录'`），于是 `messages.en.ts` 里的
 * `'Sign in'` 赋不进去 —— 而英文表本来就该是**不同的字符串**。
 *
 * 所以这里用普通对象字面量：值的类型是 `string`，
 * 而 `messages.en.ts` 的 `satisfies typeof zhCN` 仍然保证**键的形状**一致。
 * 这是"同形"与"同值"的区别 —— 要的是前者。
 *
 * ## 写文案时的两条规矩
 *
 * 1. **值里不要写标点结尾的句号**（与现有中文文案一致：`登录`、`邮箱`，
 *    不是 `登录。`）—— 中英混排时句号会显得多余；
 * 2. **插值占位符用 `{name}`**，且**两种语言的占位符集合必须一致**
 *    （`messages.en.ts` 的注释里有说明；运行期有断言守着）。
 */

const zhCN = {
  /**
   * 通用词。**只放"在任何页面都成立"的短词** ——
   * 放多了它会变成一个杂物抽屉，而真正的问题是"这个词到底属于谁"。
   */
  common: {
    save: '保存',
    cancel: '取消',
    confirm: '确定',
    delete: '删除',
    edit: '编辑',
    close: '关闭',
    loading: '加载中…',
    retry: '重试',
    back: '返回',
    /** 语言切换器的可访问名（读屏用） */
    language: '语言',
    /**
     * 语言名**用该语言自己写**（`中文` / `English`），不翻译。
     * 理由：一个只懂英文的用户在中文界面里找 "English" 时，
     * 把 "English" 翻译成"英语"反而更难找 —— 语言名是**自指**的。
     */
    languageZh: '中文',
    languageEn: 'English',
    /** Toast 关闭按钮的 aria-label（阶段 1） */
    dismiss: '关闭提示',
    /** `ConfirmDialog` 的默认按钮文案，调用方不传时用（阶段 1） */
    confirmAction: '确认',
    cancelAction: '取消',
  },

  /** 认证（登录 / 注册）—— 阶段 0 的验收页面 */
  auth: {
    // ---- 登录 ----
    loginTitle: '登录 EngramNote',
    loginSubmit: '登录',
    loginSubmitting: '登录中…',
    loginFailed: '登录失败',
    noAccount: '没有账号？',
    goRegister: '注册',

    // ---- 注册 ----
    registerTitle: '注册 EngramNote',
    registerSubmit: '注册',
    registerSubmitting: '注册中…',
    registerFailed: '注册失败',
    hasAccount: '已有账号？',
    goLogin: '登录',

    // ---- 表单字段（标签 + 占位符）----
    email: '邮箱',
    emailPlaceholder: 'your@email.com',
    password: '密码',
    /** 登录页与注册页共用（现有中文文案就是同一句） */
    passwordPlaceholder: '至少6位',
    username: '用户名',
    usernamePlaceholder: '仅限英文和数字，2-50个字符',
    /** 注册页的本地校验（与后端 `pattern=r"^[a-zA-Z0-9]+$"` 同一判据） */
    usernameInvalid: '用户名只能包含英文字母和数字',
  },

  /**
   * 侧边栏导航（i18n 阶段 1）
   *
   * 值与 `Sidebar.tsx` 的 `NAV_SECTIONS` 逐字对应 —— 那份定义现在存的是
   * **键名**（`nav.dashboard`）而不是文案，所以改文案只改这里。
   */
  nav: {
    dashboard: '仪表盘',
    /** 分组标题 */
    sectionLearning: '学习',
    today: '今日学习',
    reviewCards: '卡片复习',
    daily: '今日资料',
    projects: '项目',
    assessment: '学习评估',
    goals: '学习目标',
    sectionNotes: '笔记',
    notes: '笔记列表',
    trash: '回收站',
    sectionKnowledge: '知识',
    cards: '知识卡片',
    graph: '知识图谱',
    qa: '问答',
    questions: '问题集',
    upload: '上传资料',
    logout: '退出',
    // ---- 可访问名（读屏用；视觉上不可见，但同样是"用户会看到的内容"）----
    /** `<nav>` 的 aria-label */
    mainNav: '主导航',
    collapseSidebar: '收起侧边栏',
    expandSidebar: '展开侧边栏',
    closeMenu: '关闭菜单',
    openMenu: '打开菜单',
    /** 笔记分组里那颗上传快捷按钮 */
    uploadAction: '上传资料',
  },

  /** 404 页（i18n 阶段 1） */
  notFound: {
    /**
     * `404` 是**数字**，两种语言相同 —— 但仍然登记成键，
     * 理由是 `a11y.spec.ts` 的 `not-found` 场景按它定位
     * （`getByRole('heading', { name: '404' })`），
     * 放在表里能让"这一页有哪些文案"一目了然。
     */
    title: '404',
    message: '页面不存在，可能是链接已失效。',
    backHome: '返回首页',
  },

  /** 渲染异常兜底页（`ErrorBoundary`，i18n 阶段 1） */
  errorBoundary: {
    title: '这个页面出错了',
    description: '页面渲染时发生异常，已被错误边界拦截（应用其余部分仍可正常使用）。',
    hint: '常见原因是笔记内容里的 Markdown/公式语法异常。',
    retry: '重试',
    reload: '刷新页面',
  },

  /**
   * **前端本地生成**的 API 错误文案（i18n 阶段 2）
   *
   * ## 为什么只有这几条，而不是把后端 55 个错误码也抄一份
   *
   * 后端的 `AppError` 文案**已经**按 `Accept-Language` 翻译了
   * （见 `backend/app/core/error_messages.py`）。前端再维护一份码→英文的表，
   * 就是**同一个事实的两份副本** —— 后端改一句措辞，前端那份不会跟着变，
   * 而症状是"英文界面下偶尔冒出一句旧文案"，极难发现。
   *
   * 所以前端只翻译**到不了后端**的那些：网络层失败、超时、令牌失效、
   * 浏览器能力不足。这些文案是前端自己造的，请求根本没发出去或没拿到响应，
   * 后端无从翻译。
   */
  apiErrors: {
    /** `request()` 的默认超时文案（`client.ts` 的 `doFetch` 参数默认值） */
    timeout: '请求超时，请检查网络后重试',
    /** 上传专用超时（`UPLOAD_TIMEOUT_MS`，10 分钟） */
    uploadTimeout: '上传超时，请检查网络后重试',
    /** 401 且是登录/注册接口 —— 就是密码错，不是令牌过期 */
    invalidCredentials: '邮箱或密码错误',
    /** 401 且是普通接口 —— 令牌失效，`notifyTokenExpired()` 已触发跳登录页 */
    sessionExpired: '登录已过期，请重新登录',
    /** 流式响应不可用（`ReadableStream` 缺失，老浏览器） */
    unsupportedStream: '浏览器不支持流式响应',
  },

  /**
   * 枚举值 → 标签（i18n 阶段 3）
   *
   * ## 为什么这些必须进消息表（而不是留在 `utils/labels.ts`）
   *
   * 它们是**后端枚举值**到人读标签的映射，出现在几乎每一页的徽章与筛选器上
   * （58 个消费点）。留在 `labels.ts` 里当字符串字面量，就等于 46 条文案
   * 永远只有中文 —— 而它们恰恰是最显眼的那批（状态徽章、题型、难度）。
   *
   * ## 值必须与后端枚举**逐一对应**
   *
   * 清单来自后端 `app/models/**` 的 Enum 类（实测导出）：
   * `NoteStatus` 10 个、`CardType` 4 个、`QuestionType` 3 个、`DifficultyLevel` 3 个、
   * `CardCategory` 3 个、`SourceType` 5 个 + 前端的 `audio`/`video`/`markdown`、
   * `NoteRole` 2 个、`VersionSource` 3 个、`RelationStatus` 3 个。
   *
   * ⚠️ 后端新增一个枚举值时，这里要跟着加 —— 否则界面上会**直接显示原始值**
   * （`labels.ts` 的 `useLabels()` 在键缺失时回退到值本身，那是刻意的：
   * 显示 `blind_spot` 好过显示空白）。
   */
  labels: {
    /** 来源类型。`PDF` / `Word` / `PPT` / `Excel` / `Markdown` 是**专有名词**，两种语言同形 */
    source: {
      pdf: 'PDF',
      image: '图片',
      docx: 'Word',
      pptx: 'PPT',
      xlsx: 'Excel',
      audio: '音频',
      video: '视频',
      markdown: 'Markdown',
    },
    /** 笔记处理状态（`NoteStatus`，10 个） */
    status: {
      uploading: '上传中',
      converting: '转换中',
      converted: '已完成',
      cleaning: '清洗中',
      cleaned: '已清洗',
      cleaning_failed: '清洗失败',
      learning: '学习中',
      learning_failed: '学习失败',
      archived: '已审阅',
      failed: '失败',
    },
    /** 知识卡片类型（`CardType`，4 个） */
    cardType: {
      concept: '概念',
      formula: '公式',
      qa: '问答',
      definition: '定义',
    },
    /** 题目类型（`QuestionType`，3 个） */
    questionType: {
      choice: '选择题',
      fill_blank: '填空题',
      short_answer: '简答题',
    },
    /** 难度（`DifficultyLevel`，3 个） */
    difficulty: {
      easy: '简单',
      medium: '中等',
      hard: '困难',
    },
    /** 卡片分类（`CardCategory`，3 个） */
    cardCategory: {
      regular: '常规',
      blind_spot: '盲点',
      extension: '拓展',
    },
    /** 笔记角色（`NoteRole`，2 个） */
    noteRole: {
      material: '学习资料',
      personal_note: '个人笔记',
    },
    /** 版本来源（`VersionSource`，3 个） */
    versionSource: {
      user_edit: '手动编辑',
      auto_clean: '自动清洗',
      system: '系统',
    },
    /** 关系状态（`RelationStatus`，3 个） */
    relationStatus: {
      suggested: '建议',
      confirmed: '已确认',
      rejected: '已拒绝',
    },
    /** 判分方式（用于向用户解释这一次的分数是怎么来的） */
    gradingMethod: {
      choice: '选择题自动判分',
      fill_blank: '填空题自动判分',
      self_rating: '你的自评',
      ungraded: '待你自评（尚未计入复习进度）',
      legacy: '历史记录',
    },
    /** 语义判分的三档结论 */
    verdict: {
      correct: '回答正确',
      partial: '答对了部分',
      incorrect: '回答错误',
    },
    /**
     * SM-2 / FSRS 四档自评
     *
     * `label` 是按钮主文案，`hint` 是帮助用户区分档位的补充说明。
     * 四档**同时**用于自评按钮（`quality` 0/3/4/5）与历史记录的档位标签
     * （`rating` 1/2/3/4）—— 两处文案刻意相同，用户看到的是同一套说法。
     */
    selfRating: {
      again: { label: '完全忘记', hint: '想不起来，需要重新学' },
      hard: { label: '勉强想起', hint: '很吃力，答得不完整' },
      good: { label: '想起来了', hint: '稍作回忆就答对了' },
      easy: { label: '轻松想起', hint: '脱口而出，毫不费力' },
    },
  },
  /**
   * 笔记详情页及其子组件（i18n 阶段 3）
   *
   * 这一页是全站最复杂的一页（分屏编辑、版本、批注、ADHD 阅读、关联资料…），
   * 所以它单独一个命名空间，而不是把几十条塞进 `common`。
   */
  noteDetail: {
    // ---- 视图切换 ----
    viewOriginal: '原始版',
    viewClean: '清洗版',
    viewDiff: '对比视图',
    // ---- 加载与错误 ----
    notFound: '笔记不存在',
    backToList: '返回列表',
    noContent: '暂无内容',
    loadDiffFailed: '无法加载对比数据',
    loadingDiff: '加载对比数据...',
    // ---- 处理中 ----
    uploading: '正在上传...',
    converting: '正在转换中，请稍候...',
    retryConvert: '重试转换',
    retryFailed: '重试失败',
    // ---- 编辑 ----
    saving: '保存中...',
    save: '保存',
    cancel: '取消',
    // ---- 版本历史 ----
    versionHistory: '版本历史',
    versionListFailed: '加载版本列表失败',
    versionContentFailed: '加载版本内容失败',
    diffFailed: '加载对比失败',
    restoreVersionFailed: '恢复版本失败',
    restoreVersionConfirm: '确定恢复到此版本吗？',
    restoreVersionImpact: '当前内容将被保存为新版本。',
    restore: '恢复',
    close: '关闭',
    closePreview: '关闭预览',
    loading: '加载中...',
    noVersions: '暂无版本历史',
    preview: '预览',
    restoreThisVersion: '恢复此版本',
    versionsIdentical: '两个版本完全相同，没有差异。',
    /** 版本历史里的"选择两个版本进行对比"提示 */
    selectTwoToCompare: '选择两个版本进行对比（已选 {count}/2）',
    versionCompare: '版本对比: v{v1} → v{v2}',
    versionPreview: '版本内容预览 (v{version})',
    // ---- 内容区与关联 ----
    citedBy: '被以下笔记引用',
    relatedMaterials: '关联的学习资料',
    removeLink: '清理此链接',
    videoUnsupported: '您的浏览器不支持视频播放',
    // ---- 页头工具栏 ----
    edit: '编辑',
    editHint: '编辑笔记内容',
    editBlockedOriginal: '原始版不可编辑，请在清洗版中编辑',
    editBlockedProcessing: '处理中，暂不可编辑',
    viewVersionHistory: '查看版本历史',
    export: '导出',
    exporting: '导出中…',
    exportOriginalHint: '导出原始版 Markdown 文件',
    exportCleanHint: '导出清洗版 Markdown 文件',
    exportBlockedProcessing: '处理中，暂不可导出',
    exportBlockedDiff: '对比视图无法导出，请先切换到某一版',
    assessment: '学习评估',
    manageLinks: '管理关联资料',
    aiPreprocess: 'AI预处理',
    archive: '审阅',
    unarchive: '取消审阅',
    delete: '删除',
    back: '返回',
    reviewNow: '立即复习',
    errorPrefix: '错误',
  },

  /**
   * 共享组件的通用文案（i18n 阶段 3）
   *
   * 这些词在**多个页面**上重复出现（"加载失败"在 20+ 处），
   * 所以放在一处 —— 各页面自己写一份的话，同一句话会有多种译法。
   */
  shared: {
    loadFailed: '加载失败',
    saveFailed: '保存失败',
    deleteFailed: '删除失败',
    restoreFailed: '恢复失败',
    retryFailed: '重试失败',
    submitFailed: '提交失败',
    operationFailed: '操作失败',
    loading: '加载中...',
    loadingDots: '加载中…',
    cancel: '取消',
    confirm: '确认',
    close: '关闭',
    retry: '重试',
    delete: '删除',
    // ---- 任务进度 ----
    taskProcessing: '处理中，请稍候...',
    taskCancelled: '已取消。',
    taskCancelRequested: '已请求取消：任务会在下一个阶段边界退出（正在进行的这一步不会中断）。',
    taskCancelFailed: '取消失败，请重试。',
    taskFinished: '已结束',
    taskProcessingShort: '处理中',
    taskCancelling: '正在取消...',
    taskCancel: '取消任务',
    // ---- 差异视图 ----
    diffIdentical: '两个版本完全相同，没有差异。',
    diffOriginal: '原始版',
    diffClean: '清洗版',
    diffContent: '内容',
    /** `{label}` 传的是「原始版」或「清洗版」的**已翻译**文案 */
    diffLineCount: '{label} {count} 行',
    diffBlockCount: '{count} 处差异',
    // ---- 删除笔记对话框 ----
    moveToTrash: '移入回收站',
    purgeForever: '彻底删除',
    confirmMoveToTrash: '确认移入',
    countingRelated: '正在统计关联内容…',
    knowledgeCards: '知识卡片',
    coreCards: '核心卡片',
    bidirectionalLinks: '双向链接',
    moveToTrashConfirm: '确定将「{title}」移入回收站吗？',
    moveToTrashLinked:
      '该笔记与 {cards} 张知识卡片、{links} 个原始资料存在双向链接。移入回收站后，这些关联将暂不可见，但不影响其他笔记的引用。笔记及其全部内容（卡片、题目、复习记录）将作为整体保存，可随时在回收站中恢复。',
    moveToTrashImpact: '笔记及其全部内容（卡片、题目、复习记录）将作为整体移入回收站，可随时恢复。',
    purgeConfirm: '确定彻底删除「{title}」吗？此操作',
    irreversible: '不可恢复',
    purgeImpact:
      '笔记及其卡片、题目、复习记录将被永久删除。其他笔记对该笔记的引用将以「[已删除的笔记]」占位符保留，不会破坏其他笔记的内容结构。',
    promoteCoreCards: '高级选项：将本笔记中的核心卡片自动提升为独立节点',
    promoteCoreCardsHint:
      '勾选后，标记为核心（is_key_point）的卡片在知识图谱中依然存活，不会因父级笔记删除而变成信息孤岛。',
    // ---- 复习提醒 ----
    reviewReminder: '复习提醒',
    dueForReview: '你有 {count} 个题目待复习',
    reminderEnabled: '复习提醒已开启',
    reminderHint: '将在有待复习题目时提醒你',
    notificationDenied: '通知权限已被拒绝，请在浏览器设置中开启',
    dismissReminder: '关闭提醒',
    dueWithinOneHour: '其中 {count} 个将在 1 小时内到期',
    weakPointsCount: '薄弱知识点：{count} 个',
    enableNotifications: '开启桌面通知，及时获取复习提醒',
    reminderAlgorithmHint: '基于 FSRS 算法的间隔重复，不错过任何最佳复习时机',
    enable: '开启',
    later: '稍后',
    goReview: '去复习',
  },

  /**
   * 清洗面板（`CleaningPanel`，i18n 阶段 3）
   *
   * ⚠️ `cleaning.*` 与后端错误码 `CLEANING_*` **不是一回事**：
   * 前者是面板自己的界面文案，后者是 API 返回的错误码文案
   * （在 `backend/app/core/error_messages.py` 里，按 `Accept-Language` 翻译）。
   * 两者都叫"清洗"，但住在不同的层，改一处不会带动另一处。
   */
  cleaning: {
    triggerFailed: '触发清洗失败',
    stopFailed: '停止清洗失败',
    restoreFailed: '恢复失败',
    deleteFailed: '删除失败',
    readyHint: '笔记已转换完成，可以开始 AI 清洗',
    triggering: '正在触发...',
    start: '开始清洗',
    inProgress: '正在进行 AI 清洗，请稍候...',
    stopping: '正在停止...',
    stop: '停止清洗',
    failed: '清洗失败',
    reclean: '重新清洗',
    stats: '清洗统计',
    totalChunks: '总分块',
    duplicateBlocks: '重复块',
    emptyLinesRemoved: '去空行',
    headersFootersRemoved: '去页眉页脚',
    watermarksRemoved: '去水印',
    duplicateBlocksCount: '重复块（{count} 个）',
    blockIndex: '块 {index}',
    similarToBlock: '与块 {index} 相似度 {percent}%',
    collapseContent: '收起内容',
    viewContent: '查看内容',
    restore: '恢复',
    delete: '删除',
    originalBlock: '保留的块 {index}（首次出现）',
    duplicateBlock: '重复的块 {index}',
    contentMissing: '（内容缺失）',
    legacyNoBlockContent: '该笔记清洗时未保存块内容（旧版本清洗），点击"重新清洗"后即可查看',
    noDuplicates: '未检测到重复内容',
    stopConfirm: '确定停止清洗？',
    stopImpact: '当前进度将丢失。',
    deleteBlockConfirm: '确定删除块 {index}？',
    deleteBlockImpact: '此操作不可恢复。',
  },

  /** 笔记列表页（`NotesList`，i18n 阶段 3） */
  notesList: {
    title: '笔记',
    searchPlaceholder: '搜索笔记标题...',
    searchLabel: '搜索笔记',
    totalCount: '共 {count} 条',
    /** 角色筛选 */
    filterMaterial: '学习资料',
    filterPersonal: '我的笔记',
    /** 状态筛选 */
    filterAll: '全部',
    filterArchived: '已审阅',
    noMatch: '没有找到匹配的笔记',
    empty: '还没有笔记',
    emptyHint: '上传你的第一份学习资料',
    upload: '上传资料',
    pageCount: '{count} 页',
    retryNamed: '重试 {title}',
    deleteNamed: '删除 {title}',
    retry: '重试',
    delete: '删除',
    pagination: '分页',
    prevPage: '上一页',
    nextPage: '下一页',
    moveToTrashFailed: '移入回收站失败',
  },

  /** 回收站页（`Trash`，i18n 阶段 3） */
  trash: {
    title: '回收站',
    subtitle: '笔记及其全部内容（卡片、题目、复习记录、关系）作为整体保存，可随时整体恢复',
    emptyAll: '清空回收站',
    empty: '回收站是空的',
    emptyHint: '被删除的笔记会在这里保留，随时可以恢复',
    // ---- 表格列头 ----
    colNote: '笔记',
    colSourceAndDeletedAt: '来源与删除时间',
    colContains: '包含内容',
    colActions: '操作',
    deletedAt: '删除于 {time}',
    countCards: '{count} 张卡片',
    countQuizzes: '{count} 道题目',
    countAnnotations: '{count} 条批注',
    countVersions: '{count} 个版本',
    countLinks: '{count} 个双向链接',
    restore: '恢复',
    purge: '彻底删除',
    restoredRenamed: '原位置已存在同名文件，恢复后已自动重命名为「{name}」',
    restoreFailed: '恢复失败',
    purgeFailed: '彻底删除失败',
    purgeResult: '已彻底删除 {purged} 条，{failed} 条删除失败，请重试',
    emptyAllFailed: '清空回收站失败',
    // ---- 清空确认框 ----
    emptyAllConfirm: '确定彻底删除回收站中的全部 {count} 条笔记吗？此操作',
    emptyAllImpact:
      '其他笔记对这些笔记的引用将以「[已删除的笔记]」占位符保留，不会影响其他笔记的内容。',
    cancel: '取消',
    emptyAction: '清空',
  },

  /** 上传页（`Upload`，i18n 阶段 3） */
  upload: {
    title: '上传学习资料',
    dropZoneLabel: '点击或拖拽文件上传',
    dropZoneIdle: '点击或拖拽文件到此处',
    dropZoneBusy: '处理中...',
    supportedFormats: '支持 PDF、图片、Office 文档、音视频、Markdown 文件',
    // ---- 上传设置 ----
    settings: '上传设置',
    settingsPdf: '上传设置（共 {count} 页）',
    fileName: '文件名',
    fileNameRequired: '文件名不能为空',
    fileNameNoSeparator: '文件名不能包含路径分隔符',
    // ---- PDF 裁剪 ----
    cropHint: '按页裁剪（可选）：只保留需要的页码范围，其余页面在上传处理前剔除。',
    cropPlaceholder: '如 1-20,25,30-32',
    cropRangeLabel: '页码范围',
    cropSkip: '不裁剪，直接上传',
    cropApply: '裁剪后上传',
    cropRangeEmpty: '页码范围不能为空',
    cropRangeInvalid: '无效的页码范围: {item}',
    cropRangeStartFromOne: '页码必须从 1 开始: {item}',
    cropRangeOutOfBounds: '页码超出范围（文档共 {count} 页）: {item}',
    cropRangeOrder: '区间起始页不能大于结束页: {item}',
    confirmUpload: '确认上传',
    // ---- 解析方式 ----
    parseMode: '解析方式',
    parseAuto: '自动',
    parseAutoHint: '使用后端默认配置',
    parseLocal: '本地解析',
    parseLocalHint: '使用本地模型解析（需要本地环境支持）',
    parseCloud: '云端解析',
    parseCloudHint: '使用云端API解析（需要API Token）',
    // ---- 所属项目 ----
    projects: '所属项目（标签，可多选）',
    noProjects: '暂无项目，可先不选择或稍后创建。',
    projectsHint: '一篇笔记可归属多个项目（标签）；不选择则稍后可在「项目」页面添加。',
    // ---- 笔记类型与关联 ----
    noteType: '笔记类型',
    noteTypeMaterial: '学习资料',
    noteTypePersonal: '我的笔记',
    relatedMaterials: '关联学习资料（可选）',
    // ---- 状态与错误 ----
    uploading: '上传中...',
    uploadFailed: '上传失败',
    uploadedConverting: '文件已上传，正在转换...',
    convertingStatus: '状态: {status}',
    convertDone: '转换完成！',
    processFailed: '处理失败（{status}）',
    processTimeout: '处理超时，请稍后在笔记列表中查看',
    unsupportedFormat: '不支持的文件格式: {ext}',
    viewNote: '查看笔记',
  },

  /** 仪表盘 / 首页（`Dashboard`，i18n 阶段 3） */
  dashboard: {
    welcome: '欢迎使用 EngramNote',
    tagline: 'AI 驱动的学习笔记管理与知识库工具',
    uploadNew: '上传新资料',
    todayLearn: '今日学习',
    // ---- 空态引导 ----
    quickStart: '快速开始',
    quickStartUpload: '点击「上传新资料」上传 PDF、Word 或图片文件',
    quickStartClean: '系统自动转换并清洗内容，生成知识卡片',
    quickStartQuiz: 'AI 自动生成复习题目，点击「今日学习」开始答题',
    quickStartReview: '间隔重复算法帮你高效记忆，薄弱点一目了然',
    // ---- 目标 ----
    goals: '今日学习目标',
    noGoals: '还没有学习目标，点击创建',
    activeGoals: '进行中目标 {count} 个',
    averageProgress: '平均进度',
    // ---- 每日计划 ----
    dailyPlan: '每日推荐任务',
    noDailyPlan: '今日暂无推荐任务',
    dailyPlanProgress: '{date} · 已完成 {done} / {total}',
    // ---- 日报 ----
    dailyReport: '今日学习报告 ({date})',
    statNewMastered: '新掌握',
    statTotalReviews: '复习次数',
    statAccuracy: '正确率',
    statReviewTime: '复习时长',
    // ---- 复习 ----
    dueToday: '今日待复习: {count} 题',
    todayDone: '今日已完成 {count} 题 | 正确率 {accuracy}%',
    startReview: '开始复习',
    weakPoints: '薄弱点',
    /** 每日任务类型（与 dashboard.taskReview 同一批说法） */
    taskReview: '复习',
    taskNewMaterial: '新资料',
    errorCount: '错{count}次 | {accuracy}%',
    weeklyTrend: '本周复习趋势',
    accuracyAbove: '正确率 ≥ 60%',
    accuracyBelow: '正确率 &lt; 60%',
    // ---- 最近笔记 ----
    recentNotes: '最近笔记',
    viewAll: '查看全部',
    noNotes: '还没有笔记',
    noNotesHint: '上传你的第一份学习资料',
    upload: '上传资料',
    // ---- 邮件提醒 ----
    emailReminder: '邮件复习提醒',
    emailReminderHint: '每日通过邮件发送到期复习提醒',
    emailReminderEnableFailed: '开启邮件提醒失败',
    emailReminderDisableFailed: '关闭邮件提醒失败',
    enabled: '已开启',
    disabled: '已关闭',
    // ---- 时长格式 ----
    durationSeconds: '{count}秒',
    durationMinutes: '{count}分钟',
    durationHours: '{count}小时',
    // ---- 错误 ----
    partialLoadFailed: '部分数据加载失败（{count} 项），其余内容正常显示，可刷新重试',
    loadFailedRetry: '加载失败，请重试',
    // ---- 每日任务分类 ----
    taskWeakPoint: '薄弱点',
    itemCount: '{count} 个',
  },

  /** 学习目标页（`LearningGoals`，i18n 阶段 3） */
  goals: {
    title: '学习目标',
    newGoal: '新建目标',
    empty: '还没有学习目标',
    emptyHint: '创建你的第一个学习目标，开始追踪学习进度',
    activeSection: '进行中的目标',
    archivedToggle: '{action}已归档目标 ({count})',
    collapse: '收起',
    expand: '展开',
    // ---- 目标卡片 ----
    typeDaily: '每日',
    typeWeekly: '每周',
    /** `{type}` 传的是「每日」/「每周」的**已翻译**文案 */
    goalSummary: '{type} · 目标 {mastery}%',
    typeOnly: '{type}',
    masteryOnly: '目标 {mastery}%',
    linkedNotes: '关联笔记 {count} 篇',
    expired: '已过期',
    dueToday: '今日截止',
    daysRemaining: '剩余 {count} 天',
    progressLabel: '学习进度',
    archiveNamed: '归档 {name}',
    deleteNamed: '删除 {name}',
    archive: '归档',
    delete: '删除',
    // ---- 新建表单 ----
    createTitle: '新建学习目标',
    nameLabel: '目标名称',
    namePlaceholder: '例如：掌握第一章核心概念',
    nameRequired: '请输入目标名称',
    nameTooLong: '目标名称不能超过 200 个字符',
    masteryRange: '目标掌握度需在 0-100 之间',
    typeLabel: '目标类型',
    typeDailyOption: '每日目标',
    typeWeeklyOption: '每周目标',
    masteryLabel: '目标掌握度 (%)',
    dueDateLabel: '截止日期（可选）',
    cancel: '取消',
    create: '创建',
    creating: '创建中...',
    createFailed: '创建失败',
    archiveFailed: '归档失败',
    // ---- 确认框 ----
    deleteConfirm: '确定删除此学习目标？',
    deleteImpact: '此操作不可恢复。',
  },

  /** 今日资料页（`DailyMaterials`，i18n 阶段 3） */
  dailyMaterials: {
    title: '今日资料',
    newFolder: '新建文件夹',
    creating: '创建中...',
    // ---- 状态筛选 ----
    filterAll: '全部',
    filterProcessing: '处理中',
    filterCompleted: '已完成',
    filterFailed: '失败',
    // ---- 相对日期 ----
    today: '今天',
    yesterday: '昨天',
    // ---- 文件夹 ----
    emptyFolders: '还没有文件夹',
    emptyFoldersHint: '创建一个文件夹来组织今天的学习资料',
    /** 新建文件夹时的默认名，如「2024-01-15 学习资料」 */
    defaultFolderName: '{date} 学习资料',
    folderNameLabel: '文件夹名称',
    folderNameEmpty: '文件夹名称不能为空',
    saveNameLabel: '保存名称',
    cancelRenameLabel: '取消重命名',
    renameFolderLabel: '重命名文件夹',
    deleteFolderLabel: '删除文件夹',
    fileCount: '{count} 个文件',
    saving: '保存中...',
    save: '保存',
    cancel: '取消',
    rename: '重命名',
    delete: '删除',
    deleteFolderConfirm: '确定删除此文件夹？',
    // ---- 文件 ----
    loading: '加载中...',
    uploadFile: '上传文件',
    uploading: '上传中...',
    noMatchingFiles: '没有符合筛选条件的文件',
    emptyFolder: '文件夹为空',
    emptyFolderHint: '点击上方按钮上传学习资料',
    // ---- 错误 ----
    loadFailed: '加载失败',
    loadDetailFailed: '加载详情失败',
    createFailed: '创建失败',
    renameFailed: '重命名失败',
    uploadFailed: '上传失败',
    unsupportedFormat: '不支持的文件格式: {ext}',
    uploadedConverting: '文件已上传，正在转换...',
  },

  /** 学习评估页（`LearningAssessment`，i18n 阶段 3） */
  assessment: {
    title: '学习评估',
    subtitle: '通过笔记比对或开放性问题，评估你对学习资料的掌握程度',
    // ---- 两种模式 ----
    modeCompare: '笔记比对',
    modeQuiz: '开放性问题',
    // ---- 笔记选择 ----
    selectNote: '选择笔记',
    linkedCompare: '已链接对比',
    manualSelect: '手动选择',
    loadingLinkedNotes: '加载已链接笔记...',
    noLinkedNotes: '暂无已链接的笔记',
    noLinkedNotesHint: '请先在笔记详情页关联资料后再使用此功能',
    goToNotes: '前往笔记列表',
    linkedMaterialsCount: '关联资料: {count} 篇',
    willCompareWith: '将比对以下资料与该笔记：',
    willGenerateFrom: '将基于以下资料生成开放性问题：',
    useLinkedNotes: '使用已链接的笔记',
    noSelectableNotes: '暂无可选笔记',
    // ---- 资料选择 ----
    materials: '学习资料',
    noMaterials: '暂无学习资料',
    myNotes: '我的笔记',
    noPersonalNotes: '暂无个人笔记（请先上传并标记为"我的笔记"）',
    // ---- 操作 ----
    startAssessment: '开始评估',
    assessing: '评估中...',
    generateQuestions: '生成问题',
    generating: '生成中...',
    submitAnswer: '提交答案',
    judging: '评判中...',
    answerPlaceholder: '请输入你的答案...',
    retry: '重试',
    // ---- 结果 ----
    result: '评估结果',
    scoreCoverage: '内容覆盖度',
    scoreDepth: '思考深度',
    scoreClarity: '结构清晰度',
    scoreOverall: '综合评分',
    coveredPoints: '已覆盖知识点',
    missedPoints: '未覆盖知识点',
    suggestions: '改进建议：',
    yourAnswer: '你的回答：',
    judgment: '评判结果',
    scoreAccuracy: '准确性',
    scoreCompleteness: '完整性',
    // ---- 错误与提示 ----
    loadNotesFailed: '加载笔记失败',
    selectNoteFirst: '请选择一个笔记',
    selectMaterialAndNote: '请选择学习资料和笔记',
    selectMaterial: '请选择学习资料',
    answerAllQuestions: '请回答所有问题',
    assessFailed: '评估失败',
    generateQuestionsFailed: '生成问题失败',
    submitAnswerFailed: '提交答案失败',
  },

  /** 问题集页（`QuestionSets`，i18n 阶段 3） */
  questionSets: {
    title: '问题集',
    untitledNote: '未命名笔记',
    totalCount: '共 {count} 道题',
    totalFiltered: '，筛选后 {count} 道',
    searchPlaceholder: '搜索题目内容...',
    filterBy: '按{label}筛选',
    filterAll: '全部',
    // ---- 可配置列 ----
    colQuestion: '题目',
    colQuestionType: '题型',
    colDifficulty: '难度',
    colCreatedAt: '创建时间',
    colUpdatedAt: '更新时间',
    colAnswer: '答案',
    columnsCount: '列（{shown}/{total}）',
    columnsHint: '点列头可按该列排序',
    columnsPanel: '显示列',
    columnsFixedNote:
      '「题目」「难度」固定显示：难度那一格里的中文是行底色的文字说明，隐藏它，色觉障碍用户就读不出行状态了。',
    tableLabel: '题目列表（按所属笔记分组）',
    groupCount: '({count} 道题)',
    viewNote: '查看笔记',
    // ---- 排序可访问名 ----
    sortIdle: '{label}，点击按此列排序',
    sortAsc: '{label}，当前升序，点击改为降序',
    sortDesc: '{label}，当前降序，点击改为升序',
    // ---- 筛选维度 ----
    dimensionType: '题型',
    dimensionDifficulty: '难度',
    /** 题型筛选项的短名（与表格里的 `labels.questionType` 全称不同） */
    typeChoice: '选择',
    typeFillBlank: '填空',
    typeShortAnswer: '简答',
    difficultyEasy: '简单',
    difficultyMedium: '中等',
    difficultyHard: '困难',
    // ---- 答案 ----
    showAnswer: '显示答案',
    hideAnswer: '隐藏答案',
    answer: '答案：',
    explanation: '解析：',
    // ---- 空态与错误 ----
    empty: '暂无题目',
    emptyHint: '请先上传笔记并触发理解管道生成题目',
    noMatch: '没有符合筛选条件的题目',
    noMatchHint: '试试放宽题型或难度筛选',
  },

  /** 复习提醒横幅（`ReminderBanner`，i18n 阶段 3） */
  reminder: {
    title: '复习提醒',
    dueCount: '你有 {count} 个题目待复习',
    enabledTitle: '复习提醒已开启',
    enabledBody: '将在有待复习题目时提醒你',
    permissionDenied: '通知权限已被拒绝，请在浏览器设置中开启',
    desktopHint: '开启桌面通知，及时获取复习提醒',
    algorithmHint: '基于 FSRS 算法的间隔重复，不错过任何最佳复习时机',
    enable: '开启',
    dismissLabel: '关闭提醒',
    later: '稍后',
    dueInOneHour: '其中 {count} 个将在 1 小时内到期',
    weakPoints: '薄弱知识点: {count} 个',
    goReview: '去复习',
  },

  /** 答题复习页（`Review`，i18n 阶段 3） */
  review: {
    title: '答题复习',
    loading: '加载复习题目中...',
    done: '复习完成',
    sessionStats: '本次复习统计',
    answerCount: '答题数: {count}',
    correctCount: '正确数: {count}',
    accuracy: '正确率: {percent}%',
    overallStats: '总体统计',
    todayProgress: '今日已完成: {done} / {limit}',
    todayAccuracy: '今日正确率: {percent}%',
    dueCount: '待复习题目: {count}',
    totalReviews: '累计复习次数: {count}',
    totalAccuracy: '累计正确率: {percent}%',
    totalQuizzes: '总题目数: {count}',
    dailyLimitReached: '今日已完成 {limit} 道题，休息一下吧！',
    comeBackTomorrow: '明天再来继续复习',
    continueReview: '继续复习',
    headerProgress: '{correct}/{total} 正确 | 今日 {done}/',
    selfRatingRecorded: '自评已记录，复习进度已更新',
    loadFailed: '加载失败',
    submitFailed: '提交失败',
    retry: '重试',
  },

  /** 快速复习页（`QuickReview`，i18n 阶段 3） */
  quickReview: {
    title: '快速复习',
    loading: '加载复习题目...',
    empty: '暂无复习题目',
    emptyHint: '该笔记还没有生成题目，请先完成理解流程',
    backToNote: '返回笔记',
    done: '复习完成',
    sessionStats: '本次统计',
    answerCount: '答题数: {count}',
    correctCount: '正确数: {count}',
    accuracy: '正确率: {percent}%',
    headerProgress: '{correct}/{total} 正确',
    again: '再来一次',
    loadFailed: '加载题目失败',
    submitFailed: '提交失败',
    selfRatingRecorded: '自评已记录，复习进度已更新',
  },

  /** 卡片复习页（`CardReview`，i18n 阶段 3） */
  cardReview: {
    title: '卡片复习',
    loading: '加载到期卡片...',
    loadFailed: '加载到期卡片失败',
    empty: '没有到期的卡片',
    emptyHint:
      '卡片复习只包含「已进入复习计划」的卡片。刚导入的卡片会在你第一次复习它们时加入计划；若刚重建过数据，可能需要先运行一次复习状态迁移。',
    goToCards: '去看知识卡片',
    progress: '第 {current} / {total} 张',
    dueTotal: '到期共 {count} 张',
    selfRatingPrompt: '刚才想得起来吗？',
    needSelfRating: '请先完成自评',
    finishRound: '完成本轮',
    nextCard: '下一张',
    lightweightNote: '卡片复习是轻量回顾，不占用每日答题限额。 想复习带题目的内容请到',
    quizReviewLink: '答题复习',
  },

  /** 知识卡片详情页（`CardDetail`，i18n 阶段 3） */
  cardDetail: {
    loadFailed: '加载失败',
    saveFailed: '保存失败',
    deleteFailed: '删除失败',
    notFound: '卡片不存在',
    backToList: '返回列表',
    save: '保存',
    cancel: '取消',
    edit: '编辑',
    delete: '删除',
    back: '返回',
    sourceNote: '来源笔记：',
    deletedNote: '已删除的笔记',
    viewNote: '查看笔记',
    source: '来源：',
    standaloneCard: '独立卡片',
    chapter: '章节: {title}',
    labelTitle: '标题',
    labelContent: '内容',
    chapterSummary: '章节摘要',
    originalSource: '原始出处',
    relatedQuestions: '关联题目 ({count})',
    showAnswer: '显示答案',
    hideAnswer: '隐藏答案',
    answer: '答案: {text}',
    explanation: '解析: {text}',
    deleteConfirm: '确定删除此知识卡片？',
    deleteImpact: '将同时删除关联的练习题目、复习记录和知识图谱关系。此操作不可恢复。',
  },

  /** 智能问答页（`QA`，i18n 阶段 3） */
  qa: {
    title: '智能问答',
    inputPlaceholder: '输入你的问题，AI 将基于你的笔记内容回答...',
    stop: '停止生成',
    ask: '提问',
    empty: '输入问题开始问答',
    emptyHint: 'AI 将基于你所有笔记的内容进行回答',
    thinking: 'AI 正在思考...',
    sources: '引用来源',
    sourcesCount: '引用来源（{count}）',
    sourcesRailHint: '提问之后，这一轮回答引用到的笔记会列在这里 —— 不用滚动就能点进原文。',
    jumpToSource: '点击跳到原文该段落',
    noLocation: '该引用缺少定位信息，只能打开笔记',
    noLocationShort: '（无定位）',
    poweredBy: '由 {provider} 提供支持',
    failed: '问答失败',
  },

  /** 知识图谱页（`KnowledgeGraph`，i18n 阶段 3） */
  graph: {
    loading: '加载知识图谱...',
    empty: '暂无图谱数据',
    emptyHint: '请先上传笔记并触发理解管道，生成知识卡片后即可查看图谱',
    canvasLabel: '知识图谱画布',
    zoomIn: '放大',
    zoomOut: '缩小',
    fitScreen: '适应屏幕',
    keyboardHint: '按上下方向键切换当前节点，当前节点的详情会显示在侧边栏面板中。',
    currentOrdinal: '当前节点：{title}（第 {current} / {total} 个）',
    createFirstNode: '请点击第一个节点',
    createSecondNode: '已选择: {title}，请点击第二个节点',
  },

  /** 项目页（`Projects` 及其子组件，i18n 阶段 3） */
  projects: {
    loading: '正在加载项目…',
    empty: '还没有项目',
    emptyHint: '点击上方「新建项目」创建第一个项目（纯标签，不生成文件夹）。',
    dismissRenameHint: '关闭重命名提示',
  },

  /** 契约漂移提示（`contractDrift`，i18n 阶段 3） */
  contractDrift: {
    responseBody: '响应体',
    fieldSuffix: '{field} 字段',
    shapeMismatch: '{source} 的响应结构与约定不符',
    itemsWrapped: '期望纯数组，实际是 items 包装；已拆包正常渲染，后端应改为直接返回数组',
    notArray: '{where}不是数组，已按空列表处理（页面不崩，但数据可能不完整）',
  },

  /** 今日学习页（`TodayLearn`，i18n 阶段 3） */
  todayLearn: {
    title: '今日学习',
    loading: '加载今日学习数据...',
    // ---- 完成态 ----
    done: '今日复习完成',
    sessionStats: '本次统计',
    answerCount: '答题数: {count}',
    correctCount: '正确数: {count}',
    accuracy: '正确率: {percent}%',
    todayOverall: '今日总体',
    todayProgress: '已完成: {done} / {limit}',
    todayAccuracy: '正确率: {percent}%',
    dueCount: '待复习: {count}',
    continueReview: '继续复习',
    dailyLimitReached: '今日已完成 {limit} 道题，休息一下吧！',
    headerProgress: '{correct}/{total} 正确',
    // ---- 计划与报告 ----
    dailyPlan: '每日推荐任务',
    dailyPlanProgress: '{date} · 已完成 {done} / {total}',
    todayReport: '今日报告 ({date})',
    statNewMastered: '新掌握',
    statTotalReviews: '复习次数',
    statAccuracy: '正确率',
    statReviewTime: '复习时长',
    // ---- 待复习 ----
    dueTasks: '待复习任务',
    dueToday: '今日待复习: {count} 题',
    todayDone: '今日已完成 {count} 题 | 正确率 {accuracy}%',
    startReview: '开始复习',
    dailyGoal: '每日目标 {limit} 题，已完成 {done} 题',
    noDue: '今日没有待复习的题目',
    noDueHint: '所有题目都已复习完毕，明天再来',
    weakPoints: '薄弱点',
    /** 每日任务类型（与 dashboard.taskReview 同一批说法） */
    taskReview: '复习',
    taskNewMaterial: '新资料',
    errorCount: '错{count}次 | {accuracy}%',
    itemCount: '共 {count} 项',
    // ---- 难度档位 ----
    difficultyHigh: '高',
    difficultyMedium: '中',
    difficultyLow: '低',
    // ---- 错误 ----
    loadFailed: '加载失败',
    loadQuestionsFailed: '加载题目失败',
    submitFailed: '提交失败',
  },

  /** 知识卡片页（`KnowledgeCards`，i18n 阶段 3） */
  knowledgeCards: {
    title: '知识卡片',
    untitledNote: '未命名笔记',
    totalCount: '共 {count} 张卡片',
    graphView: '图谱视图',
    searchPlaceholder: '搜索卡片标题或内容...',
    empty: '暂无知识卡片',
    emptyHint: '请先上传笔记并触发理解管道',
    groupCount: '({count} 张卡片)',
    // ---- 筛选 ----
    filterAll: '全部',
    filterRegular: '常规',
    filterBlindSpot: '盲点',
    filterExtension: '拓展',
    // ---- 卡片操作 ----
    keyPointTitle: '重点',
    difficultyTitle: '难点',
    chapter: '章节: {title}',
    suggestExtension: '建议生成拓展知识点',
    moreActions: '更多操作',
    markKeyPoint: '标记重点',
    unmarkKeyPoint: '取消重点',
    markDifficulty: '标记难点',
    unmarkDifficulty: '取消难点',
    masteryTooLow: '掌握度需达到 80',
    generateExtension: '生成拓展知识点',
    extensionConfirm: '拓展知识点已生成！是否立即为拓展知识点出题？',
    confirm: '确定',
    // ---- 错误 ----
    loadFailed: '加载失败',
    actionFailed: '操作失败',
    generateExtensionFailed: '生成拓展知识点失败',
    quizFailed: '出题失败',
    quizFailedWithReason: '出题失败：{reason}',
  },

  /** 图谱侧边栏（`GraphSidebar`，i18n 阶段 3） */
  graphSidebar: {
    stats: '图谱统计',
    statNodes: '节点',
    statEdges: '边',
    statPending: '待确认',
    statIsolated: '孤立节点',
    // ---- 关联节点 ----
    relatedNodes: '关联节点',
    close: '关闭',
    relatedCount: '→ {count} 个关联节点',
    noRelated: '该节点暂无关联节点',
    // ---- 关系详情 ----
    relationDetail: '关系详情',
    labelType: '类型：',
    labelStatus: '状态：',
    statusSuggested: '建议',
    statusConfirmed: '已确认',
    labelSimilarity: '相似度：',
    confirm: '确认',
    reject: '拒绝',
    deleteRelation: '删除关系',
    // ---- 建议关系 ----
    suggestions: '建议关系 ({count})',
    selectAll: '全选',
    confirmSelected: '批量确认 ({count})',
    rejectSelected: '批量拒绝 ({count})',
    noSuggestions: '暂无建议关系，可点击下方按钮基于嵌入向量挖掘新的潜在关联',
    generating: '生成中，卡片较多时可能需要数十秒...',
    generateSuggestions: '生成相关建议',
    similarity: '相似度: {score}',
    // ---- 创建关系 ----
    createRelation: '创建关系',
    node1: '节点 1：',
    node2: '节点 2：',
    clickToSelect: '请在图谱中点击选择',
    relationType: '关系类型',
    creating: '创建中...',
    confirmCreate: '确认创建',
    cancel: '取消',
    // ---- 图例 ----
    relationTypesLegend: '关系类型（点击高亮）',
    pendingSuggestionLegend: '待审建议（淡色 · 无箭头）',
  },

  /** 答题卡组件（`quiz/QuizAnswerCard`，i18n 阶段 3） */
  quizCard: {
    /** 判分结果未知时的兜底标签 */
    graded: '已判分',
    reviewStats: '已复习 {count} 次 | 间隔 {interval} 天',
    answerPlaceholder: '请输入答案...',
    shortAnswerPlaceholder: '请输入你的回答...',
    aiGradeLabel: '让 AI 先判一次，指出遗漏与误解',
    aiGradeHint: '需要联网调用模型，提交会慢几秒；不勾选则直接由你自评。',
    submit: '提交答案',
    selfRatingPrompt: '请对照答案，给自己的回忆程度打分',
    shortAnswerNote: '简答题无法自动判分，需要你自评后才会计入复习进度。',
    correct: '回答正确!',
    incorrect: '回答错误',
    correctAnswer: '正确答案:',
    referenceAnswer: '参考答案:',
    explanation: '解析:',
    aiVerdict: 'AI 判分：{verdict}',
    omissions: '遗漏：',
    misunderstandings: '误解：',
    noIssues: '没有发现遗漏或误解。',
    selfRatingRecorded: '已按自评「{rating}」记录，复习进度已更新。',
    nextReview: '下次复习: {interval} 天后 | 评分: {quality}',
    ratingTier: ' | 档位: {label}',
    predictedRetention: '复习前预测还能想起: {percent}%',
    gradingMethod: ' | 判分方式: {method}',
    needSelfRating: '请先完成自评',
    finishReview: '完成复习',
    nextQuestion: '下一题',
  },

  /** 原文语境面板（`quiz/SourceContext`，i18n 阶段 3） */
  sourceContext: {
    loadFailed: '原文加载失败',
    expand: '查看原文语境',
    collapse: '收起原文语境',
    loading: '正在读取原文...',
    retry: '重试',
    noSource: '这张卡片的生成过程没有记下原文段落。',
    viewFullText: '在笔记中查看完整原文 →',
  },

  /** 自评按钮组（`quiz/SelfRatingButtons`，i18n 阶段 3） */
  selfRating: {
    pending: '正在记录自评...',
    skip: '跳过自评，先做下一题',
  },

  /** 卡片复习完成汇总（`cardreview/CardReviewSummary`，i18n 阶段 3） */
  cardReviewSummary: {
    done: '本轮卡片复习完成',
    stats: '本次统计',
    reviewed: '复习卡片: {count} 张',
    recalled: '想起来了: {count} 张',
    loadedAllDue: '本次加载了 {loaded} 张，全部到期共 {due} 张 —— 可以再来一轮。',
    queueCleared: '到期队列已经清空。',
    again: '再复习一轮',
    backToToday: '回到今日学习',
  },

  /** 卡片复习排程反馈（`cardreview/ScheduleFeedback`，i18n 阶段 3） */
  scheduleFeedback: {
    /** 短日期，如「1月15日」 */
    monthDay: '{month}月{day}日',
    recorded: '已按自评「{rating}」记录',
    nextReview: '下次复习: ',
    daysLater: '{count} 天后',
    predictedRetention: '复习前模型认为你还能想起: ',
    retentionNote: '—— 越接近遗忘，这次答对后间隔涨得越大',
    stability: '记忆强度 {value} 天',
    difficulty: '难度 {value}',
    mastery: '掌握度 {from} → {to}',
    empty: '—',
  },

  /** 笔记内 AI 提问浮窗（`NoteAskPanel`，i18n 阶段 3） */
  noteAsk: {
    title: 'AI 提问',
    dragHint: '按住拖动窗口',
    placeholder: '输入你的问题...',
    selectionPrefix: '基于当前笔记选中文本：「{text}」',
    ask: '提问',
    askAgain: '重新提问',
    jumpFailed: '未能定位到引用段落（正文可能尚未加载或排版有差异）',
  },

  /** 卡片正面（`cardreview/CardFace`，i18n 阶段 3） */
  cardFace: {
    reviewCount: '已复习 {count} 次',
    intervalDays: ' · 间隔 {count} 天',
    lapses: ' · 遗忘 {count} 次',
    selfTestHint:
      '先在心里把这张卡的内容讲一遍，再看答案 —— 直接翻面等于看答案， 这次自评就不准了。',
    showAnswer: '显示答案',
    summary: '摘要:',
  },

  /** 笔记详情页的批注 / 关联 / 属性栏（i18n 阶段 3） */
  noteAnnotations: {
    railLabel: '笔记元信息',
    roleLabel: '笔记角色',
    roleUpdateFailed: '更新角色失败',
    pageCount: '{count} 页',
    createdAt: '创建于 {date}',
    highlight: '高亮',
    underline: '下划线',
    askAiTitle: '选中文本调用 AI 提问',
    deleteConfirm: '确定删除此批注？',
    deleteFailed: '删除批注失败',
    selectionInvalid: '选中文本过长或为空',
    saveFailed: '保存批注失败',
  },

  /** 关联资料（i18n 阶段 3） */
  noteLinks: {
    loadFailed: '加载资料列表失败',
    updated: '关联资料已更新',
    unchanged: '关联资料未变化',
    saveFailed: '保存关联失败',
    cleanupFailed: '清理链接失败',
    manageTitle: '管理关联资料',
    empty: '暂无可关联的资料',
    relatedCards: '关联知识卡片 ({count})',
    viewAllCards: '查看全部 {count} 张卡片',
  },

  /** 笔记详情页的操作（i18n 阶段 3） */
  noteActions: {
    impactCards: '{count} 张知识卡片',
    impactQuizzes: '{count} 道题目',
    impactReviewLogs: '{count} 条复习记录',
    impactRelations: '{count} 条图谱关系',
    /** 影响清单的连接符（中文用顿号） */
    impactSeparator: '、',
    relearnConfirm: '此笔记已归档，重新学习将清空其现有学习成果。此操作不可恢复，确定继续？',
    relearnImpact: '将删除：{items}',
    continueAction: '继续',
    startLearningFailed: '启动学习失败',
    moveToTrashFailed: '移入回收站失败',
    operationFailed: '操作失败',
    exportFailed: '导出失败',
    originalNotEditable: '原始版不可编辑，请切换到清洗版后编辑',
    saveFailed: '保存失败',
    discardConfirm: '放弃当前编辑的修改？',
    discard: '放弃',
    loadFailed: '加载失败',
    citationJumpFailed: '已打开笔记，但未能定位到引用段落（可能因排版差异）',
  },

  /** ADHD 专注阅读模式（`MarkdownReader`，i18n 阶段 3） */
  adhdReader: {
    disableHint: '关闭 ADHD 专注阅读模式',
    enableHint: '开启 ADHD 专注阅读模式（鼠标跟随：高亮所在行、模糊其他内容）',
    disable: '关闭 ADHD Reader',
    enable: '开启 ADHD Reader',
    followHint: '鼠标跟随 · 移动鼠标高亮所在行',
    reading: '正在阅读',
  },

  /** 项目页（`pages/projects/*`，i18n 阶段 3） */
  projectPage: {
    title: '项目',
    subtitle: '项目作为标签归属笔记，一篇笔记可属于多个项目；所有文件统一存放在收件箱（inbox）。',
    // ---- 错误 ----
    loadFailed: '加载项目列表失败，请稍后重试',
    /** 重命名表单：名称清空时的校验 */
    nameRequired: '项目名称不能为空',
    /** 新建表单：名称清空时的校验（与重命名表单**文案不同**，不要合并） */
    nameRequiredCreate: '请输入项目名称',
    renameFailed: '重命名项目失败，请稍后重试',
    deleteFailed: '删除项目失败，请稍后重试',
    loadNotesFailed: '加载项目笔记失败，请稍后重试',
    scanFailed: '扫描导入失败，请确认后端服务可用后重试',
    removeNoteFailed: '移出笔记失败，请稍后重试',
    createFailed: '创建项目失败，请稍后重试',
    loadCandidatesFailed: '加载候选笔记失败，请稍后重试',
    selectNotesFirst: '请先勾选要添加的笔记',
    addNotesFailed: '添加笔记失败，请稍后重试',
    // ---- 新建项目 ----
    createTitle: '创建新项目',
    createHint: '项目为纯标签，创建后不生成物理目录；用标签给笔记打归属',
    newProject: '新建项目',
    newProjectTitle: '新建项目',
    nameLabel: '项目名称',
    namePlaceholder: '如：Transformer 论文精读',
    descriptionLabel: '项目描述（可选）',
    descriptionPlaceholder: '这个项目是做什么的？',
    create: '创建项目',
    creating: '创建中…',
    cancel: '取消',
    save: '保存',
    // ---- 项目卡片 ----
    namePlaceholderShort: '项目名称',
    noteCount: '{count} 篇笔记',
    addNotesHint: '从已有笔记中选择并添加到本项目',
    addNotes: '添加笔记',
    scanHint: '扫描导入 source/ 目录中的新文件',
    scan: '扫描导入',
    scanning: '扫描中…',
    collapseNotes: '收起笔记',
    expandNotes: '查看笔记（{count}）',
    rename: '重命名',
    delete: '删除',
    removeNoteHint: '将笔记移出该项目',
    remove: '移出',
    noNotes: '项目暂无笔记。可把文件放入收件箱',
    noNotesTail: '后点击「扫描导入」。',
    // ---- 删除 / 移出确认 ----
    deleteConfirm: '确定删除项目「{name}」？',
    deleteImpact: '删除仅移除该项目标签，关联笔记与文件都会保留。',
    removeConfirm: '确定将笔记「{note}」移出项目「{project}」？',
    // ---- 扫描结果 ----
    scanResult: '扫描结果',
    scanImported: '新增 {count}',
    scanSkipped: '跳过 {count}',
    scanUnsupported: '不支持 {count}',
    scanEmptyPrefix: '未在收件箱',
    scanEmptyMiddle: '目录发现新文件。可把文件拷贝到',
    scanEmptySuffix: '后再扫描。',
    // ---- 使用说明 ----
    usageTitle: '使用说明',
    usageTagOnly: '项目是纯标签：一篇笔记可打上多个项目标签，创建项目不会生成物理文件夹。',
    usageAllFiles: '所有文件统一存放在收件箱（inbox）的',
    usageCopyHint: '把文件直接拷贝到收件箱',
    usageScanHint: '后，点击「扫描导入」即可识别为笔记并打上当前项目标签。',
    usageUploadHint: '也可以在上传页选择多个项目标签，通过网页直接上传文件。', // ---- 添加笔记面板 ----
    addPanelTitle: '添加笔记',
    addPanelClose: '关闭添加笔记面板',
    searchPlaceholder: '按标题搜索候选笔记…',
    noCandidates: '暂无可添加的笔记',
    adding: '添加中…',
    addWithCount: '添加（{count}）',
    // ---- 错误横幅 ----
    dismissBanner: '关闭',
  },

  /** 知识图谱的节点/关系类型（`components/graph/types.ts`，i18n 阶段 3） */
  graphTypes: {
    // ---- 关系类型 ----
    relationRelated: '相关',
    relationPrerequisite: '前置',
    relationSubsequent: '后续',
    relationContrast: '对比',
    // ---- 节点类型（图例里的单字缩写） ----
    nodeConcept: '概',
    nodeFormula: '式',
    nodeQa: '问',
    nodeDefinition: '定',
  },

  /** 知识图谱页的操作（`knowledgegraph/*`，i18n 阶段 3） */
  graphPage: {
    title: '知识图谱',
    /** 节点/边计数，如「12 节点 · 8 边 · 3 待审」 */
    stats: '{nodes} 节点 · {edges} 边',
    statsSuggested: ' · {count} 待审',
    searchPlaceholder: '搜索卡片...',
    filterByType: '按卡片类型筛选',
    allTypes: '全部类型',
    cancel: '取消',
    createRelation: '创建关系',
    suggestions: '建议',
    collapse: '收起',
    expand: '展开',
    // ---- 节点详情 ----
    nodeDetail: '节点详情',
    labelTitle: '标题',
    labelType: '类型',
    labelRelationCount: '关联数',
    labelSourceNote: '来源笔记',
    unknownSource: '未知来源',
    viewPointDetail: '查看知识点详情',
    loading: '加载中...',
    viewRelatedNodes: '查看关联节点',
    // ---- 错误与确认 ----
    operationFailed: '操作失败',
    batchFailed: '批量操作失败',
    suggestFailed: '生成建议失败',
    deleteFailed: '删除失败',
    createFailed: '创建失败',
    loadFailed: '加载图谱失败',
    rejectBatchConfirm: '确定要拒绝 {count} 条建议关系吗？',
    reject: '拒绝',
    deleteRelationConfirm: '确定要删除此关系吗？',
    delete: '删除',
  },
  /** 检索降级提示（`utils/retrievalNotice.ts`，i18n 阶段 3） */
  retrievalNotice: {
    bm25Only: '已降级为关键词检索（向量服务不可用）',
    hybrid: '本次只用关键词检索（向量检索没有命中，刚导入的资料可能还没建索引）',
  },

  /** 最后的零散文案（i18n 阶段 3 收尾） */
  misc: {
    requestFailed: '请求失败: {status}',
    taskAttempt: '第 {attempt}/{max} 次尝试',
    taskCancelFailedWithReason: '取消失败：{reason}',
    selfRatingSubmitFailed: '自评提交失败',
    mastery: '掌握度 {percent}%',
  },
};

export default zhCN;
