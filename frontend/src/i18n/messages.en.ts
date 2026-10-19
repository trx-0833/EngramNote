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
} satisfies typeof zhCN;

export default en;
