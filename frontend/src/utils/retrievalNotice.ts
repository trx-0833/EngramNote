/**
 * @file 检索降级提示（把 SSE `meta` 里的 `retrieval_status` 翻成一句人话）
 *
 * ## 为什么需要它
 *
 * 后端的问答流首事件会下发 `retrieval_status`，契约里写明它是
 * "**供前端展示降级提示**"。它有三个取值（`app/services/rag_service.py`）：
 *
 * | 值 | 含义 | 用户该知道什么 |
 * |---|---|---|
 * | `full_vector` | 问题编码成功 + 向量检索有命中 | 不用提示 |
 * | `hybrid` | 问题编码成功，但**向量检索 0 条命中** —— 本次实际只用关键词 | 该提示：答案只靠关键词匹配 |
 * | `bm25_only` | **问题**编码失败（向量服务不可用），只剩关键词 | 该提示：向量服务坏了 |
 *
 * ## 这一轮修的是什么
 *
 * 修复前只有 `bm25_only` 会被提示，而**实际观测到的是 `hybrid`** ——
 * 2026-09-14 的全链路 e2e 里，上传后 48 秒提问得到的就是 `hybrid`：
 * 那条笔记的 chunk 还没有向量（清洗路径刻意不写向量，要靠人工跑
 * `scripts/embed_chunks.py` 补，见 `docs/overhaul-plan.md` 附录 BN.4）。
 * 也就是说：**最容易发生的那种降级，恰好是唯一不提示的那种**。
 *
 * ## 文案为什么这么写（不要随手改）
 *
 * `hybrid` 的成因**不止一个**：语料没向量（最常见）与"有向量但都没过阈值"
 * 都会得到 `hybrid`。所以文案说"向量检索没有命中"，把"刚导入的资料可能还没
 * 建索引"作为**可能原因**提一句，而不断言"就是没建索引" —— 界面上不写
 * 自己没验证过的因果。
 */

/** 后端 `retrieval_status` 的取值（未知值一律不提示） */
const NOTICES: Record<string, string> = {
  // 与改造前的文案**逐字一致**：这条不是本轮新增的，只是搬进了这里
  bm25_only: '已降级为关键词检索（向量服务不可用）',
  hybrid: '本次只用关键词检索（向量检索没有命中，刚导入的资料可能还没建索引）',
};

/**
 * 把检索状态翻成提示文案
 *
 * @param status - SSE `meta` 事件里的 `retrieval_status`（可能是 `undefined`）
 * @returns 需要提示时的文案；`full_vector` / 未知值 / 缺省时返回 `null`
 */
export function retrievalNotice(status: string | undefined): string | null {
  if (!status) return null;
  return NOTICES[status] ?? null;
}
