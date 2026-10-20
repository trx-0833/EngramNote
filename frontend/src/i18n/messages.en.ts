/**
 * @file 英文消息表
 *
 * ## 形状由 `messages.zh-CN.ts` 定义
 *
 * `satisfies typeof zhCN` 让 `tsc` 保证**同形**：中文那边加一个键而这里没加，
 * 编译直接失败。这是手写 i18n 相对库的主要优势（库通常只在运行期发现漏键），
 * 所以这条约束**不要**为了图省事改成 `Partial<>` 或 `as any`。
 *
 * ## 占位符必须与中文一致
 *
 * 例如中文 `'欢迎 {name}'` 的英文必须是 `'Welcome {name}'`，
 * 不能写成 `'Welcome, dear user'` —— 插值参数是按名字取的，
 * 少一个占位符会让那次调用静默丢掉信息（不报错，只是少显示一段）。
 * `messages.test.ts` 有一条运行期断言逐键比对两边的占位符集合。
 *
 * ## 英文文案的三条口径
 *
 * 1. **句首大写、其余小写**（`Version history`，不是 `Version History`）——
 *    与按钮的既有英文习惯一致，也与本项目"克制"的调性一致；
 * 2. **不用句号结尾**（与中文表同一规矩）；
 * 3. **术语固定**：笔记 = note、卡片 = card、题目 = question、
 *    清洗 = clean、理解 = understand、复习 = review、归档 = archive。
 *    这三个词在后续阶段的 1000+ 条文案里会反复出现，
 *    一旦漂移（note/record/document 混用）英文界面会显得业余。
 *    ⚠️ 后续阶段新增文案时**先查这张表**再写。
 */

import type zhCN from './messages.zh-CN';

const en = {
  common: {
    save: 'Save',
    cancel: 'Cancel',
    confirm: 'Confirm',
    delete: 'Delete',
    edit: 'Edit',
    close: 'Close',
    loading: 'Loading…',
    retry: 'Retry',
    back: 'Back',
    language: 'Language',
    // 语言名自指，两种语言表里**值相同** —— 这是刻意的，见中文表的说明
    languageZh: '中文',
    languageEn: 'English',
    dismiss: 'Dismiss notification',
    confirmAction: 'Confirm',
    cancelAction: 'Cancel',
  },

  auth: {
    loginTitle: 'Sign in to EngramNote',
    loginSubmit: 'Sign in',
    loginSubmitting: 'Signing in…',
    loginFailed: 'Sign-in failed',
    noAccount: "Don't have an account?",
    goRegister: 'Sign up',

    registerTitle: 'Create your EngramNote account',
    registerSubmit: 'Sign up',
    registerSubmitting: 'Creating account…',
    registerFailed: 'Sign-up failed',
    hasAccount: 'Already have an account?',
    goLogin: 'Sign in',

    email: 'Email',
    emailPlaceholder: 'your@email.com',
    password: 'Password',
    passwordPlaceholder: 'At least 6 characters',
    username: 'Username',
    usernamePlaceholder: 'Letters and digits only, 2–50 characters',
    usernameInvalid: 'Username may contain letters and digits only',
  },

  nav: {
    dashboard: 'Dashboard',
    sectionLearning: 'Learning',
    today: 'Today',
    reviewCards: 'Card review',
    daily: 'Today’s materials',
    projects: 'Projects',
    assessment: 'Assessment',
    goals: 'Goals',
    sectionNotes: 'Notes',
    notes: 'All notes',
    trash: 'Trash',
    sectionKnowledge: 'Knowledge',
    cards: 'Cards',
    graph: 'Graph',
    qa: 'Q&A',
    questions: 'Question sets',
    upload: 'Upload',
    logout: 'Sign out',
    mainNav: 'Main navigation',
    collapseSidebar: 'Collapse sidebar',
    expandSidebar: 'Expand sidebar',
    closeMenu: 'Close menu',
    openMenu: 'Open menu',
    uploadAction: 'Upload material',
  },

  notFound: {
    title: '404',
    message: 'This page does not exist. The link may have expired.',
    backHome: 'Back to home',
  },

  errorBoundary: {
    title: 'Something went wrong on this page',
    description:
      'A rendering error was caught by the error boundary. The rest of the app still works.',
    hint: 'This is usually caused by malformed Markdown or formula syntax in a note.',
    retry: 'Retry',
    reload: 'Reload page',
  },

  apiErrors: {
    timeout: 'The request timed out. Check your connection and try again.',
    uploadTimeout: 'The upload timed out. Check your connection and try again.',
    invalidCredentials: 'Incorrect email or password',
    sessionExpired: 'Your session has expired. Please sign in again.',
    unsupportedStream: 'Your browser does not support streaming responses',
  },

  labels: {
    // 专有名词与缩写两种语言同形 —— 刻意不译
    source: {
      pdf: 'PDF',
      image: 'Image',
      docx: 'Word',
      pptx: 'PPT',
      xlsx: 'Excel',
      audio: 'Audio',
      video: 'Video',
      markdown: 'Markdown',
    },
    status: {
      uploading: 'Uploading',
      converting: 'Converting',
      converted: 'Converted',
      cleaning: 'Cleaning',
      cleaned: 'Cleaned',
      cleaning_failed: 'Cleaning failed',
      learning: 'Learning',
      learning_failed: 'Learning failed',
      archived: 'Reviewed',
      failed: 'Failed',
    },
    cardType: {
      concept: 'Concept',
      formula: 'Formula',
      qa: 'Q&A',
      definition: 'Definition',
    },
    questionType: {
      choice: 'Multiple choice',
      fill_blank: 'Fill in the blank',
      short_answer: 'Short answer',
    },
    difficulty: {
      easy: 'Easy',
      medium: 'Medium',
      hard: 'Hard',
    },
    cardCategory: {
      regular: 'Regular',
      blind_spot: 'Blind spot',
      extension: 'Extension',
    },
    noteRole: {
      material: 'Material',
      personal_note: 'Personal note',
    },
    versionSource: {
      user_edit: 'Manual edit',
      auto_clean: 'Auto clean',
      system: 'System',
    },
    relationStatus: {
      suggested: 'Suggested',
      confirmed: 'Confirmed',
      rejected: 'Rejected',
    },
    gradingMethod: {
      choice: 'Graded automatically (multiple choice)',
      fill_blank: 'Graded automatically (fill in the blank)',
      self_rating: 'Your self-rating',
      ungraded: 'Awaiting your self-rating (not counted toward progress yet)',
      legacy: 'Historical record',
    },
    verdict: {
      correct: 'Correct',
      partial: 'Partially correct',
      incorrect: 'Incorrect',
    },
    selfRating: {
      again: { label: 'Forgot', hint: 'Could not recall it — needs relearning' },
      hard: { label: 'Hard', hint: 'Struggled; the answer was incomplete' },
      good: { label: 'Good', hint: 'Recalled it with a little effort' },
      easy: { label: 'Easy', hint: 'Instant recall, no effort' },
    },
  },
  noteDetail: {
    viewOriginal: 'Original',
    viewClean: 'Cleaned',
    viewDiff: 'Compare',
    notFound: 'Note not found',
    backToList: 'Back to list',
    noContent: 'No content yet',
    loadDiffFailed: 'Could not load the comparison',
    loadingDiff: 'Loading comparison…',
    uploading: 'Uploading…',
    converting: 'Converting, please wait…',
    retryConvert: 'Retry conversion',
    retryFailed: 'Retry failed',
    saving: 'Saving…',
    save: 'Save',
    cancel: 'Cancel',
    versionHistory: 'Version history',
    versionListFailed: 'Could not load the version list',
    versionContentFailed: 'Could not load the version content',
    diffFailed: 'Could not load the comparison',
    restoreVersionFailed: 'Could not restore the version',
    restoreVersionConfirm: 'Restore this version?',
    restoreVersionImpact: 'The current content will be saved as a new version.',
    restore: 'Restore',
    close: 'Close',
    closePreview: 'Close preview',
    loading: 'Loading…',
    noVersions: 'No version history yet',
    preview: 'Preview',
    restoreThisVersion: 'Restore this version',
    versionsIdentical: 'The two versions are identical — no differences.',
    selectTwoToCompare: 'Pick two versions to compare ({count}/2 selected)',
    versionCompare: 'Comparing v{v1} → v{v2}',
    versionPreview: 'Version content preview (v{version})',
    citedBy: 'Cited by these notes',
    relatedMaterials: 'Linked materials',
    removeLink: 'Remove this link',
    videoUnsupported: 'Your browser does not support video playback',
  },

  shared: {
    loadFailed: 'Failed to load',
    saveFailed: 'Failed to save',
    deleteFailed: 'Failed to delete',
    restoreFailed: 'Failed to restore',
    retryFailed: 'Retry failed',
    submitFailed: 'Failed to submit',
    operationFailed: 'Operation failed',
    loading: 'Loading…',
    loadingDots: 'Loading…',
    cancel: 'Cancel',
    confirm: 'Confirm',
    close: 'Close',
    retry: 'Retry',
    delete: 'Delete',
    taskProcessing: 'Processing, please wait…',
    taskCancelled: 'Cancelled.',
    taskCancelRequested:
      'Cancellation requested: the task will stop at the next stage boundary (the current step will finish).',
    taskCancelFailed: 'Cancellation failed. Please try again.',
    taskFinished: 'Finished',
    taskProcessingShort: 'Processing',
    taskCancelling: 'Cancelling…',
    taskCancel: 'Cancel task',
    diffIdentical: 'The two versions are identical — no differences.',
    diffOriginal: 'Original',
    diffClean: 'Cleaned',
    diffContent: 'Content',
    diffLineCount: '{label}: {count} lines',
    diffBlockCount: '{count} differences',
    moveToTrash: 'Move to trash',
    purgeForever: 'Delete permanently',
    confirmMoveToTrash: 'Move to trash',
    countingRelated: 'Counting related items…',
    knowledgeCards: 'Knowledge cards',
    coreCards: 'Core cards',
    bidirectionalLinks: 'Bidirectional links',
    moveToTrashConfirm: 'Move “{title}” to the trash?',
    moveToTrashLinked:
      'This note is linked bidirectionally with {cards} knowledge cards and {links} source materials. After moving it to the trash, those links become temporarily invisible, but references from other notes are unaffected. The note and all of its content (cards, questions, review history) are kept together and can be restored from the trash at any time.',
    moveToTrashImpact:
      'The note and all of its content (cards, questions, review history) will be moved to the trash as a whole, and can be restored at any time.',
    purgeConfirm: 'Permanently delete “{title}”? This action',
    irreversible: 'cannot be undone',
    purgeImpact:
      'The note, its cards, questions and review history will be permanently deleted. References from other notes will be kept as a “[deleted note]” placeholder, so their structure stays intact.',
    promoteCoreCards: 'Advanced: promote this note’s core cards to standalone nodes',
    promoteCoreCardsHint:
      'When checked, cards marked as core (is_key_point) stay alive in the knowledge graph instead of becoming orphaned when their parent note is deleted.',
    reviewReminder: 'Review reminder',
    dueForReview: 'You have {count} questions due for review',
    reminderEnabled: 'Review reminders are on',
    reminderHint: 'You will be reminded when questions are due',
    notificationDenied: 'Notification permission was denied. Enable it in your browser settings.',
    dismissReminder: 'Dismiss reminder',
    dueWithinOneHour: '{count} of them are due within 1 hour',
    weakPointsCount: 'Weak points: {count}',
    enableNotifications: 'Turn on desktop notifications to get review reminders',
    reminderAlgorithmHint: 'FSRS-based spaced repetition, so you never miss the best review time',
    enable: 'Enable',
    later: 'Later',
    goReview: 'Go to review',
  },
} satisfies typeof zhCN;

export default en;
