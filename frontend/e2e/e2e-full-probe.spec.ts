/**
 * 全链路层的**探针**自查（不需要后端、不花钱、秒级）
 *
 * ## 为什么单独有这么一个文件
 *
 * `e2e-full.spec.ts` 的第 ⑤ 条要从页面上把"AI 回答"读出来。那一读用的定位式
 * 一旦写错，症状极难读。本轮实测踩了两次，**每次花掉一整条链路**（含真实
 * LLM 调用、二十多分钟）：
 *
 * 1. **取错元素**：`getByText` 返回**最内层**匹配元素 = 问题气泡自己，
 *    读到的文本只有问题，被 `.replace(question, '')` 清成空串；
 *    报出来是"界面上没有渲染出答案（0 字）—— 后端流了但 UI 没显示"，
 *    而同一轮的证据 JSON 里 `tokenCount=107 / textLength=200 /
 *    providerText="由 DeepSeek 提供支持"` 都说明答案就在页面上；
 * 2. **挂满超时**：改用 `getByText(整句问题, {exact:true}).locator('..')` 后，
 *    `locator.innerText()` 用的是**用例超时**（25 分钟）而不是 `expect` 的 60 秒，
 *    于是整整挂了 25 分钟才报错 —— 这 25 分钟里后端早就把答案流完了
 *    （API 日志 `ask/stream status=200`、LLM 日志 `chars=196`）。
 *
 * 用半条链路的时间与额度换一条"选择器不匹配"是不可接受的。因此把
 * "这个选择器到底选到了什么"从那条昂贵的链路里**抽出来**：本文件用与
 * `src/pages/QA.tsx` 的 JSX **逐层对应**的静态 HTML 断言定位式，
 * 秒级完成、不碰后端、不花额度。
 *
 * 它**就是**这样抓到第三个错的：`filter({ has })` 会让**祖先**也满足条件，
 * 于是取到的是页面上那个"提问"输入卡片（证据：`Received string: "提问"`）。
 * 现在判定写在页面里（`readAnswerText`），"哪个 div 的直接文本等于问题"
 * 只有唯一答案。
 *
 * 用法：
 *
 *     npm run e2e:full:probe        # 需要 ENGRAMNOTE_E2E_FULL=1，但不启动后端/不调 LLM
 */
import { expect, test } from '@playwright/test'
import { cleanAnswerText, describeQaDom, expectAnswerCardText, readAnswerText } from './e2e-full-locators'

const QUESTION = '主变压器顶层油温的正常运行上限是多少？超过多少必须降低负荷？'
const ANSWER = '根据参考资料：顶层油温正常运行上限是 85 摄氏度，达到 95 摄氏度时必须降低负荷。'

/**
 * 与 `QA.tsx` 的问答历史渲染**结构一致**的最小 HTML
 *
 * 逐层对照（`QA.tsx` 的 `history.map` 分支）：
 *
 *     <div key=…>                       ← 记录外层
 *       <div flex-end><div qaUserBubble>问题</div></div>
 *       <div className={`card ${qaAiCard}`}>
 *         <div pre-wrap>答案</div>       ← 或 <div italic>AI 正在思考...</div>
 *         <div>引用来源 …</div>
 *         <p>由 DeepSeek 提供支持</p>
 *       </div>
 *     </div>
 */
function qaDom(question: string, answer: string, thinking: boolean, withHistory = true): string {
  const cardBody = thinking
    ? '<div style="font-style:italic">AI 正在思考...</div>'
    : `<div style="white-space:pre-wrap">${answer}</div>`
  const history = withHistory
    ? `
      <div>
        <div><div>${question}</div></div>
        <div class="card">
          ${cardBody}
          <div>
            <p>引用来源:</p>
            <div>[1] 📄 变压器运行技术标准</div>
          </div>
          <p>由 DeepSeek 提供支持</p>
        </div>
      </div>`
    : ''
  return `
    <div class="page-enter">
      <h1>智能问答</h1>
      <div class="card">
        <input class="input" value="" placeholder="输入你的问题..." />
        <button class="btn btn-primary">提问</button>
      </div>
      ${history}
    </div>
  `
}

test.describe('全链路探针：答案定位式（不需要后端）', () => {
  test('① 读到的文本是答案，而且**不含**输入卡片里的"提问"', async ({ page }) => {
    await page.setContent(qaDom(QUESTION, ANSWER, false))

    const text = await expectAnswerCardText(page, QUESTION)
    expect(text, '取到的是答案文本').toContain(ANSWER)
    // 反向自检 1：读到的不是问题气泡（第一版就是这样错的）
    expect(text, '答案卡片里不该只有问题本身').not.toBe(QUESTION)
    // 反向自检 2：读到的**不是页面上那个"提问"输入卡片**
    // （`filter({ has })` 的祖先匹配问题：实测取到过 `"提问"`）
    expect(text, '不该取到输入卡片').not.toBe('提问')
    expect(text.length).toBeGreaterThan(ANSWER.length)
  })

  test('② 首字之前（"AI 正在思考..."）也能读到卡片', async ({ page }) => {
    await page.setContent(qaDom(QUESTION, '', true))
    const text = await expectAnswerCardText(page, QUESTION)
    expect(text).toContain('AI 正在思考')
  })

  test('③ 有多轮问答时只认领**直接文本等于问题**的那一条记录', async ({ page }) => {
    // 另一轮的问题把本节问题作为**前缀**（`hasText` 之类的包容匹配会误认领），
    // 且它排在本节问题**前面**（`QA.tsx` 是 `[新记录, ...旧记录]`）。
    const other = `${QUESTION}（补充提问，请只答冷却方式）`
    await page.setContent(`
      <div>
        <div><div>${other}</div></div>
        <div class="card"><div>另一轮的答案：强迫油循环风冷。</div></div>
      </div>
      <div>
        <div><div>${QUESTION}</div></div>
        <div class="card"><div>${ANSWER}</div></div>
      </div>
    `)
    const text = await expectAnswerCardText(page, QUESTION)
    expect(text).toContain(ANSWER)
    expect(text, '不该读到上一轮的答案').not.toContain('强迫油循环风冷')
  })

  test('④ 还没有这轮记录时：`readAnswerText` 返回 null（**不挂住**）', async ({ page }) => {
    await page.setContent(qaDom(QUESTION, '', true, false))
    const started = Date.now()
    const text = await readAnswerText(page, QUESTION, 1_000)
    const elapsed = Date.now() - started
    expect(text).toBeNull()
    // 关键是"有上限"：上一版这里会等满 25 分钟的用例超时
    expect(elapsed, `应约 1 秒返回，实际 ${elapsed}ms`).toBeLessThan(10_000)
  })

  test('⑤ 定位失败时给出**带现场**的失败信息（不是只有"0 字"）', async ({ page }) => {
    await page.setContent(qaDom(QUESTION, '', true, false))
    const detail = await describeQaDom(page, QUESTION)
    // 现场里必须带上"页面上到底有什么"，否则失败信息会把排查引向产品代码
    expect(detail).toContain('div.card=')
    expect(detail).toContain('正文前 300 字=')
    expect(detail).toContain('智能问答')
  })

  test('⑥ `cleanAnswerText` 把卡片文本削成"只有答案"', async ({ page }) => {
    await page.setContent(qaDom(QUESTION, ANSWER, false))
    const cleaned = cleanAnswerText(await expectAnswerCardText(page, QUESTION), QUESTION)

    expect(cleaned).toContain(ANSWER.slice(0, 20))
    // 问题气泡、provider、引用来源整段都要削掉 —— 引用来源在卡片**内部**，
    // 只 replace 标题会留下 "[1] 📄 …" 这类行，让"界面答案 == 流文本"的比对失败
    expect(cleaned, '问题气泡要削掉').not.toContain(QUESTION)
    expect(cleaned, 'provider 行要削掉').not.toContain('提供支持')
    expect(cleaned, '引用来源整段要削掉').not.toContain('引用来源')
    expect(cleaned, '引用条目也要削掉').not.toContain('变压器运行技术标准')
    expect(cleaned).toBe(ANSWER)
  })
})
