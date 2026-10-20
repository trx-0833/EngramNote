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
};

export default zhCN;
