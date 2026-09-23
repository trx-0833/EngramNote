/**
 * SSE（Server-Sent Events）流解析工具
 *
 * 背景（见 docs/overhaul-plan.md §2.8 F-10）：
 * 同一段约 60 行的 SSE 解析代码此前被复制到 `pages/QA.tsx` 与
 * `components/NoteAskPanel.tsx` 两份，而且两份都带同样的缺陷：
 *
 * 1. **只保留最后一行 `data:`** —— SSE 规范允许多行 data，需按 `\n` 拼接
 * 2. **不识别 `\r\n`** —— 上游或反代（nginx）可能输出 CRLF，导致事件切不开
 * 3. **`JSON.parse` 没有 try/catch** —— 一个被截断的分片就会抛错并
 *    **终结整个流**，用户看到 "Unexpected token" 且回答中断
 * 4. 两份实现已经漂移（QA.tsx 处理 `sources` 事件，NoteAskPanel 不处理）
 *
 * 这里收敛为唯一实现，并按规范补齐上述行为。
 */

export interface SSEHandlers {
  /** 收到一个事件（event 名 + 已解析的 JSON 数据） */
  onEvent: (eventType: string, data: unknown) => void;
  /** 遇到无法解析的事件时回调（默认仅告警，不中断流） */
  onParseError?: (raw: string, error: unknown) => void;
}

/**
 * 解析一个 SSE 响应体，逐事件回调
 *
 * ## 参数为什么接受"流或读取器"两种
 *
 * 调用方通常需要**持有读取器**才能主动停流（`reader.cancel()`，
 * 见 QA.tsx / NoteAskPanel.tsx 的 `stopActiveStream`），于是它们会先
 * `stream.getReader()` 再把流交给本函数。而 `getReader()` 是**独占**的：
 * 同一个流第二次调用会抛
 * `ReadableStreamDefaultReader constructor can only accept readable streams
 * that are not yet locked to a reader` —— 这正是本函数第二个 `getReader()`
 * 撞上的错误：`QA` 页的问答**每次都会失败**，报的却是这句底层报错
 * （2026 覆盖轮把 `qa` 加进审计场景时实测到的，见 docs/a11y-audit.md §10）。
 *
 * 所以这里显式接受两种入参：给**流**就自己取读取器（调用方不需要停流时，
 * 例如 `useStreamAnswer`），给**读取器**就直接用（调用方需要停流时）。
 * 这不是"两种写法都行"的宽容，而是把"谁拥有读取器"这件事写成契约：
 * 谁取的谁负责 `cancel`/`releaseLock`，本函数只做读取。
 *
 * @param body - fetch 返回的可读流，**或**调用方已经取好的读取器
 * @param handlers - 事件回调
 * @param signal - 可选的中止信号（abort 时抛 AbortError，由调用方处理）
 */
export async function parseSSEStream(
  body: ReadableStream<Uint8Array> | ReadableStreamDefaultReader<Uint8Array>,
  handlers: SSEHandlers,
  signal?: AbortSignal,
): Promise<void> {
  // `read` 只在读取器上有：据此区分"传进来的是流"还是"已经取好的读取器"，
  // 后者**不能**再取一次（会抛上面那个 locked 错误）。
  const ownsReader = !('read' in body);
  const reader = ownsReader
    ? (body as ReadableStream<Uint8Array>).getReader()
    : (body as ReadableStreamDefaultReader<Uint8Array>);
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      if (signal?.aborted) {
        const err = new Error('Aborted');
        err.name = 'AbortError';
        throw err;
      }

      const { done, value } = await reader.read();
      if (done) break;

      // stream: true 表示可能还有后续 chunk，避免多字节字符被截断
      buffer += decoder.decode(value, { stream: true });

      // 事件之间以空行分隔。先统一换行符，兼容 \r\n 与 \r
      buffer = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

      const blocks = buffer.split('\n\n');
      // 最后一个可能不完整，保留到下一次循环
      buffer = blocks.pop() || '';

      for (const block of blocks) {
        dispatchEventBlock(block, handlers);
      }
    }

    // 流结束时若缓冲区还有内容（某些实现不发送结尾空行），补发一次
    if (buffer.trim()) {
      dispatchEventBlock(buffer, handlers);
    }
  } finally {
    // 只释放**自己取的**那把锁：调用方传进来的读取器由调用方负责
    // （它在 `stopActiveStream` 里还要用 `reader.cancel()` 停流，
    //  而 `releaseLock()` 会把读取器与流解绑，之后的 cancel 只会抛 TypeError）。
    if (ownsReader) {
      try {
        reader.releaseLock();
      } catch {
        /* 忽略：锁可能已被释放 */
      }
    }
  }
}

/** 解析单个事件块并回调 */
function dispatchEventBlock(block: string, handlers: SSEHandlers): void {
  let eventType = '';
  const dataLines: string[] = [];

  for (const rawLine of block.split('\n')) {
    const line = rawLine;
    if (line.startsWith(':')) continue; // 注释/心跳行
    if (line.startsWith('event:')) {
      eventType = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      // 规范：去掉冒号后可选的一个空格，其余原样保留
      dataLines.push(line.slice(5).replace(/^ /, ''));
    }
    // id: / retry: 当前不需要，显式忽略
  }

  // 规范：无 event 字段时默认事件名为 "message"
  if (!eventType) eventType = 'message';
  // 多行 data 按 \n 拼接（这是原实现漏掉的关键行为）
  const dataStr = dataLines.join('\n');

  if (!dataStr) return;

  let parsed: unknown;
  try {
    parsed = JSON.parse(dataStr);
  } catch (error) {
    // 关键：坏事件**跳过而不是抛错**，否则一个截断分片会终结整个回答
    handlers.onParseError?.(dataStr, error);
    return;
  }

  handlers.onEvent(eventType, parsed);
}
