/**
 * 全链路层共用的**问答页定位式与现场描述**（被 `e2e-full.spec.ts` 与
 * `e2e-full-probe.spec.ts` 同时 import）
 *
 * ## 为什么单独一个文件（而不是放在 spec 里 export）
 *
 * 最初写在 `e2e-full.spec.ts` 里，探针 `import { locateAnswerCard } from './e2e-full'`
 * 直接编译不过：
 *
 *     error TS2307: Cannot find module './e2e-full' or its corresponding type declarations.
 *
 * —— 因为那个文件叫 `e2e-full.spec.ts`（**没有** `e2e-full.ts`）。
 * 让探针 import `.spec` 文件也是错的：那会把整条链路的用例定义也拉进探针进程
 * （Playwright 会在探针 project 里再收一遍那些用例）。
 *
 * 所以把"选择器"抽到这个非 spec 的模块里：**探针为试跑服务，试跑为链路服务**，
 * 三者共享同一份选择器定义。
 *
 * ## 这里记的两次踩坑（每次都用半条链路的钱换来）
 *
 * 1. **取错元素**：`getByText` 返回的是**最内层**匹配元素 —— 也就是问题气泡
 *    自己。第一版写成
 *
 *        page.locator('div').filter({ has: page.getByText(question) }).last()
 *
 *    取到的文本只有问题本身，被 `.replace(question, '')` 一清就成了空串，
 *    报出来是"界面上没有渲染出答案（0 字）—— 后端流了但 UI 没显示"，
 *    而真相是探针取错了元素；
 * 2. **挂满超时**：改成 `getByText(整句问题, { exact: true }).locator('..')` 后，
 *    `locator.innerText()` 用的是**用例超时**（25 分钟）而不是 `expect` 的 60 秒，
 *    于是整整挂了 25 分钟才报错。这 25 分钟里后端早就把答案流完了
 *    （API 日志 `POST /api/understanding/ask/stream status=200`，
 *    LLM 日志 `scene=rag_answer_stream … chars=196`）。
 *
 * 结论写进代码而不是只写在文档里：**读文本前必须先 `expect(card).toBeVisible()`
 * （有上限）再 `innerText()`**，定位式本身由免后端的探针守护。
 */
import { type Page } from '@playwright/test'

/**
 * 取"这一轮回答的卡片"的文本
 *
 * 判据链（与 `src/pages/QA.tsx` 的 JSX 逐层对应）：
 *
 *     div                       ← 记录外层（问题气泡与答案卡片的共同父级）
 *       div > div               ← 问题气泡：**直接文本**（不含子元素文本）等于问题
 *       div.card                ← 答案卡片：答案 / "AI 正在思考..." / 引用来源 / provider
 *
 * 为什么不用 `getByText(整句问题, { exact: true })`：那是最初两版的做法 ——
 * 一次取错元素（读到问题气泡，清空成"0 字"）、一次挂满 25 分钟用例超时。
 * 两次都花掉一整条链路（含真实 LLM 调用）。见文件头的记录。
 *
 * 为什么把判定放进 `page.evaluate` 而不是拼 Playwright 的嵌套
 * `filter({ has })`：后者会让**祖先**也满足条件，`.first()` 于是取到最外层容器 ——
 * 实测取到的是页面上那个"提问"输入卡片（探针证据里写着 `"提问"`）。
 * 逐层判定写在页面里，"哪个 div 的直接文本等于问题"这个问题只有唯一答案。
 *
 * @returns 卡片文本；**页面上还没有这轮记录**时返回 `null`（`timeoutMs` 内轮询）
 */
export async function readAnswerText(
  page: Page,
  question: string,
  timeoutMs = 60_000,
): Promise<string | null> {
  const read = async (): Promise<string | null> =>
    page
      .evaluate((q) => {
        const divs = Array.from(document.querySelectorAll('div'))
        const bubble = divs.find((el) => {
          let direct = ''
          for (const node of el.childNodes) {
            if (node.nodeType === Node.TEXT_NODE) direct += node.textContent ?? ''
          }
          return direct.trim() === q
        })
        if (!bubble) return null
        const record = bubble.parentElement?.parentElement
        const card = record?.querySelector('div.card')
        return card ? (card as HTMLElement).innerText : null
      }, question)
      .catch(() => null)

  const deadline = Date.now() + timeoutMs
  for (;;) {
    const text = await read()
    if (text !== null) return text
    if (Date.now() > deadline) return null
    await page.waitForTimeout(200)
  }
}

/** 断言这轮记录已经出现在页面上（失败信息带现场），返回卡片文本 */
export async function expectAnswerCardText(page: Page, question: string): Promise<string> {
  const text = await readAnswerText(page, question)
  if (text === null) {
    throw new Error(`问答记录没有出现在页面上。现场：${await describeQaDom(page, question)}`)
  }
  return text
}

/**
 * 定位失败时的"为什么"：把页面上的候选与原文带进失败信息
 *
 * 失败信息必须**自带现场**。上面那次"取错元素"花了一整条链路才被发现，
 * 就是因为报错只说"0 字"，没说"页面上其实有什么"。
 */
export async function describeQaDom(page: Page, question: string): Promise<string> {
  const cards = await page.locator('div.card').count()
  const exact = await page.getByText(question, { exact: true }).count()
  const body = (await page.locator('body').innerText().catch(() => '')).replace(/\s+/g, ' ')
  return [
    `url=${page.url()}`,
    `div.card=${cards}`,
    `问题精确命中=${exact}`,
    `正文前 300 字=${body.slice(0, 300)}`,
  ].join(' | ')
}

/**
 * 清理答案卡片的文本：去掉问题气泡、思考占位、provider 行与引用来源段
 *
 * 引用来源要整段切掉（`[\s\S]*$`）：它在卡片**内部**，只 replace 标题会留下
 * `[1] 📄 变压器运行技术标准` 这类行，让"界面答案 == 流文本"的比对失败。
 */
export function cleanAnswerText(raw: string, question: string): string {
  return raw
    .replace(question, '')
    .replace(/AI 正在思考\.\.\./g, '')
    .replace(/由 (DeepSeek|GLM) 提供支持/g, '')
    .replace(/引用来源:[\s\S]*$/, '')
    .trim()
}
