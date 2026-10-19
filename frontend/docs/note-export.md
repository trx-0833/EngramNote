# 笔记导出功能（`使用时的想法.txt` 第 4 条）

> **状态**：活文档 —— 导出功能的现状依据（契约、判据、边界、未做项）。
> **范围**：只讲**笔记正文导出**。批量导出、卡片/题目导出、JSON 备份都**未做**，
> 逐条登记在 §7。

## 1. 一句话

笔记详情页有一个「导出」按钮，点一下把**当前正在看的那一版正文**存成一个
带 YAML 元信息头的 `.md` 文件。

## 2. 契约

```
GET /api/notes/{note_id}/export?variant=clean|original
```

| 项                             | 值                                                                                             | 出处                                                     |
| ------------------------------ | ---------------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| 鉴权                           | `Depends(get_current_user_dependency)`                                                         | `backend/app/api/notes/list_detail.py` 的 `export_note`  |
| 归属校验                       | `get_note_detail(db, note_id, current_user.id)`（按 `user_id` 过滤）                           | `services/note_service.py:127-151`                       |
| 回收站                         | 默认排除（`trashed_at.is_(None)`）—— 对普通访问表现为不存在                                    | 同上                                                     |
| `variant`                      | 默认 `clean`；`Query(pattern="^(clean\|original)$")`，非法值 → **422**                         | 同上                                                     |
| 200 响应                       | `text/markdown; charset=utf-8`，body = front-matter + 正文                                     | `schemas/common.py` 的 `MarkdownDownloadResponse`        |
| 文件名                         | `Content-Disposition: attachment; filename="<ASCII 回退>"; filename*=UTF-8''<percent-encoded>` | `services/note_export.py` 的 `build_content_disposition` |
| 无正文                         | **400** + `NOTE_EXPORT_EMPTY`（**不静默回退到另一版**）                                        | `core/app_error.py`                                      |
| 笔记不存在 / 别人的 / 在回收站 | **404** + `NOTE_NOT_FOUND`（不区分 —— 不泄露"这个 id 存在"）                                   | 同上                                                     |

### 2.1 `Content-Disposition` **不在 OpenAPI 契约里**

这一点必须知道，否则会在 schema 里找不到文件名而以为漏了：

- HTTP 头的值是 **latin-1** 的（RFC 7230），中文标题直接写进去会让 Starlette
  在编码响应头时抛 `UnicodeEncodeError`。所以按 RFC 6266 / 5987 写**两份**：
  ASCII 回退（非 ASCII → `_`，**不是删除** —— 删了回退名会变空串）
  - `filename*=UTF-8''…`（现代浏览器优先用，中文原样还原）；
- **OpenAPI 表达不了这个头**，与 `video` 端点的 307 重定向同一性质。
  因此前端是**运行时从响应头解析**的：
  `frontend/src/api/client.ts` 的 `parseContentDispositionFilename`
  （优先 `filename*`，回退 `filename`，都读不到返回 `null` 由调用方兜底）。

**解析顺序写反的后果**：中文标题的笔记会下载成 `__________.md`，
而且**不报任何错** —— 用户只会觉得"文件名怎么坏了"。所以那条顺序有专门用例
（`frontend/src/api/client.test.ts` 的「★ 优先 filename*」）。

### 2.2 两条硬门禁（改这个功能时会撞到）

| 门禁                  | 位置                                                    | 为什么                                                                                                          |
| --------------------- | ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `NON_JSON_2XX` 白名单 | `backend/tests/test_openapi_response_contract.py:39`    | 该白名单是**穷举**的，新增"2xx 无 JSON 响应体"的端点必须显式加一行并写明理由；漏了直接红                        |
| 不许声明成 JSON       | `backend/tests/test_note_export.py::TestExportContract` | 声明成 `application/json` 会让客户端照着去 `response.json()` 并在真实响应上抛错。这是"类型说错了"而不是"缺类型" |

## 3. 文件内容

```markdown
---
title: '拉哇水电站运行规程学习'
source_type: pdf
status: cleaned
variant: clean
page_count: 10
created_at: '2026-10-18T23:00:00+00:00'
updated_at: '2026-10-19T09:12:00+00:00'
exported_at: '2026-10-19T10:30:00+00:00'
---

<正文原样，一字不改>
```

规则（每条都有对应用例）：

1. **只有 `title` 需要引号** —— 它是唯一的用户可控字段。用双引号 + 转义
   `\` `"` `\n`，**永不裸写**。理由：`浮充与均充：区别` 裸写会被 YAML 当成键值分隔、
   `第 1 章 # 概述` 会被当成注释、`true` / `null` / `123` 会被当成别的类型。
   一律加引号则只有一条规则，**不可能出错**。
2. 其余字段是枚举 / 整数 / ISO 时间戳，都是安全标量，不加引号。
3. **`page_count` 为 `None` 时整行省略**，不写 `null` —— 对 Dataview / Logseq
   这类使用者来说，"没有这个字段"与"字段等于 null"是两种意思
   （后者像"页数未知、待补"）。
4. 时间戳**全部秒级**（`exported_at` 显式 `replace(microsecond=0)`）——
   同一块元信息里两种精度不好看。真浏览器实测发现的。
5. **正文逐字不改** —— 不规范化行尾、不补标题、不 trim。导出是"把应用里的
   东西拿出来"，任何加工都会让用户怀疑"我看到的和导出的到底是不是同一份"。
6. front-matter 与正文之间**恰好一个空行**。
7. **不导出** `user_id` / `folder_id` / 对象存储路径 —— 内部标识对用户无用，
   路径还会泄露存储布局。

### 3.1 为什么不用 `pyyaml`

`pyyaml` **不在主依赖里**：`backend/requirements.txt:30` 注明它只属 MinerU 本地
pipeline（`requirements-pdf-local.txt`）。为一个 8 行的元信息块把一个可选依赖
拉进主环境，不符合本项目"零非必要依赖"的口径。

代价是必须自己保证 YAML 正确性，所以只实现**最小引号规则**（§3 第 1 条），
不去实现通用序列化器。测试里自带一个最小解析器把导出结果读回来，
以此证明"不需要依赖也能读回"。

## 4. 文件名

`services/note_export.py` 的 `sanitize_filename`（**唯一**一份实现 ——
前端不重复一套，它从响应头读）：

| 输入                     | 输出            | 为什么                                                                                      |
| ------------------------ | --------------- | ------------------------------------------------------------------------------------------- |
| `拉哇水电站运行规程学习` | 原样            | 中文合法，且本项目 24 篇笔记标题全是中文                                                    |
| `a/b:c*d?`               | `a_b_c_d_`      | Windows 非法字符 `\ / : * ? " < > \|` → `_`（**不是删除**，保留"这里原来有个字符"）         |
| `浮充与均充：区别（上）` | **原样**        | ★ 全角标点在 Windows 上**合法**，且中文标题里很常见 —— 清理它们会平白改动用户看得见的文件名 |
| `a\nb`                   | `a b`           | 控制字符 → 空格（不是删除：`"a\nb"` 删掉换行会变成 `"ab"`，而本意是两个词）                 |
| `标题.`                  | `标题`          | ★ Windows 上**建不出**以点结尾的文件名                                                      |
| `标` × 200               | 前 80 字        | `MAX_FILENAME_CHARS = 80`（Windows 路径上限 260，下载目录还要占几十字符）                   |
| `""` / `"   "`           | `note`          | 空标题回退                                                                                  |
| `""` + id                | `note-11111111` | ★ 带 id 前 8 位 —— 否则两篇空标题笔记导出后互相覆盖                                         |

**超长截断后再去一次尾部空白与点**：截断点可能正好落在空格上（有用例守着）。

## 5. 前端链路

```
点「导出」
  → pages/notedetail/useNoteActions.ts 的 handleExport
  → api/notes.ts 的 exportNote(noteId, variant)        ← variant 由 viewMode 决定
  → api/client.ts 的 downloadRequest(path)             ← authorizedFetch（401 刷新一次 + 重放一次）
  → URL.createObjectURL(blob) → <a download> 点击 → revokeObjectURL
```

三个要点：

1. **不能直接用 `<a href="/api/notes/x/export">`** —— 端点需要 `Authorization`
   头，而浏览器对 `<a href>` 的导航请求**不带自定义头**。必须先 `fetch` 成 blob。
   这与视频加载同源（`pages/notedetail/useNoteDetailData.ts:237-261`），
   `downloadRequest` 把那套逻辑收敛成一处供导出复用。
2. **`revokeObjectURL` 必须在 `finally` 里** —— blob URL 会把整个文件内容
   钉在内存里直到被释放。不释放的话每次导出都漏一份，而页面上完全看不出来。
3. **文件名优先用后端给的**，解析不出来才回退 `${note.title}.md` ——
   那个名字可能含非法字符，但**浏览器的 `download` 属性会自己做一次安全化**，
   比前端再实现一遍规则可靠。

### 5.1 导出哪一版 = 用户当前在看哪一版

页头已有「原始版 / 清洗版」切换，导出跟着它走 —— 零新增 UI，
也不会让用户猜"我导的到底是哪一版"。

按钮的禁用条件（`pages/notedetail/NoteDetailHeader.tsx`）：

| 情况                                                   | 行为       | 为什么                                                                                                           |
| ------------------------------------------------------ | ---------- | ---------------------------------------------------------------------------------------------------------------- |
| 处理中（uploading / converting / cleaning / learning） | **禁用**   | 正文还没生成或正在改写，导出来是半成品                                                                           |
| 对比视图                                               | **禁用**   | "对比"是两版并排看，不是某一个版本                                                                               |
| 尚未清洗                                               | **不禁用** | 此时 `viewMode` 必然是 `original`（`ViewModeTabs` 的 `canShowClean` 已挡住切过去），用户能导的正是手上有的那一版 |

⚠️ **页头 CSS 一个字都没动**：新增的是第 9 个按钮，复用现成的
`.noteDetailActions` 换行规则。那 5 条窄屏规则（768px 上下排列 + 按钮撑满、
480px 一行两个）是 5.6 序 8 专门搬进模块、并被 `verify-built-css.mjs` 的
切片标记盯着的既有结论。

## 6. 验证

### 6.1 自动化（离线）

| 层                                    | 文件                                     | 条数              |
| ------------------------------------- | ---------------------------------------- | ----------------- |
| 后端纯函数 + 端点 + 契约              | `backend/tests/test_note_export.py`      | 72                |
| 前端头解析 + 下载请求                 | `frontend/src/api/client.test.ts`        | 12（该文件共 29） |
| 前端页面行为（按钮、禁用、blob 释放） | `frontend/src/pages/NoteDetail.test.tsx` | 7（该文件共 17）  |

jsdom **证明不了浏览器真的开始下载** —— 那是浏览器行为不是 DOM 行为。
所以 §6.2 那一层不可省。

### 6.2 真浏览器 + 真后端实测（2026-10-19）

用 `e2e-full` 那一套（独立端口 + 一次性数据目录，见 `e2e.md` §8）跑了一次
临时探针，**4 passed / 56 秒**，实测输出：

```
filename=变压器运行技术标准.md          ← 中文没乱码、没变成下划线
bytes=463
head="---\ntitle: \"变压器运行技术标准\"\nsource_type: markdown\nstatus: cleaned\n
      variant: original\ncreated_at: \"2026-10-19T04:35:54+00:00\"\n
      updated_at: \"2026-10-19T04:36:22+00:00\"\nexported_at: \"2026-10-19T04:36:29+00:00\"\n
      ---\n\n# 变压器运行技术标准\n\n## 顶层油温\n\n自然循环冷却方式下，顶层油温不得超过 95℃…"
clean_variant=clean
diff_disabled=OK
```

它验证了 jsdom 测不到的四件事：

1. 点一下**真的触发浏览器下载**（`page.waitForEvent('download')` 拿到了事件）；
2. `download.suggestedFilename()` = `变压器运行技术标准.md` —— 这是
   **Chromium 解析 `Content-Disposition` 之后**的结果，所以它同时验证了
   后端的 RFC 5987 编码**与**前端的头解析；
3. 正文与上传的原文逐字一致（含 `℃` 等非 ASCII）；
4. 清洗版可用时导出 `variant: clean`；对比视图下按钮确实禁用。

**探针是临时的**（一次性 project + 一次性 spec，跑完即删，配置已还原到无差异）。
没有把它留成常设用例，理由：它会拉起真后端 + Celery + 加载 bge-m3（首次约 55 秒），
而它的判据已经被 §6.1 的三层离线用例覆盖了大半 ——
唯一不可替代的是第 1、2 条（浏览器下载行为与 `Content-Disposition` 的真实解析），
那两条**改这个功能时需要手动重跑**，脚本骨架见本节的实测输出。

### 6.3 这一层没有花 LLM 额度

上传 Markdown 这条路径**不碰任何外部 API**：转换是读文件
（`tasks/convert_tasks.py:185-189`），清洗是**纯规则**
（`tasks/clean_tasks.py:222` 调 `cleaning_service.clean_rules`，无 LLM）。
所以实测没有用掉 OpenCode 的免费额度。

## 7. 明确不做（登记，避免下次重复讨论）

| 不做                       | 理由 / 下一步形态                                                                                                                                                                                                                                 |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **列表页批量导出（ZIP）**  | 与"单个 `.md`"冲突（一个文件装不下一篇）。下一步形态：`GET /api/notes/export?variant=…&ids=…` 返回 `application/zip`，内含 `index.md` 目录 + 每篇一个 `.md`。**这是"防锁定"真正的价值所在**（整库可搬走），见 `docs/open-source-readiness.md:790` |
| 导出知识卡片 / 题目        | 卡片导出要定 Anki 字段映射（另一件事）                                                                                                                                                                                                            |
| 导出原始文件（PDF / 视频） | 那是"下载原文件"不是"导出内容"，且要处理大文件与 `Range`                                                                                                                                                                                          |
| JSON 备份 / 可再导入的格式 | 需要版本化 schema 与导入端，是一个完整的双向功能                                                                                                                                                                                                  |
| 批量选择 UI（勾选框）      | 属于批量导出的配套                                                                                                                                                                                                                                |
| 定时自动备份               | 属 `overhaul-plan.md:1398` 的 E-11（数据库备份），不是用户导出                                                                                                                                                                                    |
| 导出复习进度 / 统计        | 属"学习数据导出"，与"内容导出"是两类需求                                                                                                                                                                                                          |

## 8. 回滚

```
git revert <导出功能提交>
```

涉及文件（共 13 个）：

- 后端新增：`app/services/note_export.py`、`tests/test_note_export.py`
- 后端修改：`app/api/notes/list_detail.py`、`app/core/app_error.py`、
  `app/schemas/common.py`、`tests/test_openapi_response_contract.py`、`openapi.json`
- 前端新增函数：`api/client.ts`（`downloadRequest` / `parseContentDispositionFilename`）、
  `api/notes.ts`（`exportNote`）、`pages/notedetail/useNoteActions.ts`（`handleExport`）
- 前端修改：`pages/notedetail/NoteDetailHeader.tsx`、`pages/NoteDetail.tsx`、
  `api/generated/schema.ts`
- 测试：`api/client.test.ts`、`pages/NoteDetail.test.tsx`

**只加不删**：没有改任何现有端点、没有动页头 CSS、没有动数据模型。
所以回滚是纯删除，不会影响既有功能。
