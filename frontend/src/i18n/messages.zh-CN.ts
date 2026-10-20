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
};

export default zhCN;
