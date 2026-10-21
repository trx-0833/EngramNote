"""
错误文案的双语表
================

## 这张表解决什么

`AppError(code, message, status)` 的 `message` 会作为响应体的 `detail` 返回给用户。
硬编码中文时，英文界面下的错误提示是中文 —— 这就是本表要修的东西。

## 迁移是**增量**的（这是本设计的要点）

`AppError` 的签名**没有改**，`message` 仍然是必填参数。查表发生在
`middleware/error_handler.py`：

```python
detail = resolve_message(exc.code, fallback=exc.message, locale=...)
```

于是：

| 情况 | 行为 |
|---|---|
| code 在表里，且表项的唯一文案与 `fallback` 相符 | 按当前语言返回 |
| code 在表里，但 `fallback` 是**另一条**文案（同码多文案） | 按 `(code, fallback)` 找到那一条 |
| code **不在表里**，或那条文案没登记 | 用 `raise` 时写的 `message`（= 现有行为，一字不变） |

所以 90 处 `raise AppError(...)` 可以**分批迁移**：加一条表项就等于翻译了一处，
而没加的照旧。不需要一次改完，也不会出现"改了一半、另一半报 KeyError"。

## ⚠️ 表里的中文必须与 `raise` 时那句**逐字相同**

否则迁移一处就等于**悄悄改了现有用户看到的中文提示**。
加表项时的做法是：从调用点把那个字符串复制过来，而不是重新写一遍。
`tests/test_i18n.py` 用 AST 扫全 `app/` 的调用点逐字比对 —— 那条守卫
**验证过不是空转**（故意改错一个字立刻红）。

## 同一错误码有多条文案时怎么办（`_ALT` 后缀）

实测有 6 个码在不同调用点用了**不同的文案**，例如：

- `CARD_NOT_FOUND`：`卡片不存在` / `知识卡片不存在` / `知识卡片不存在或无权访问`
- `FOLDER_NOT_FOUND`：`文件夹不存在` / `文件夹不存在或无权访问`

一个码只能有一条"主文案"，所以其余的登记成 `<CODE>_ALT` / `<CODE>_ALT2`。
查表顺序是**先按 `(code, fallback)` 精确匹配、再退回主项** ——
这样 `raise` 的代码一个字都不用改，翻译也不会串。

## 哪些**没有**进表（以及为什么）

`AppError` 的 `message` 有 40 处是**表达式**而不是字面量，分两种：

1. **含运行期值的入参校验**（约 31 处），如
   `f"无效的 note_role 值: {note_role}，有效值为: {', '.join(valid_roles)}"`。
   这类要进表就得给 `AppError` 加 `params` 并把 31 处 raise 改写成模板 ——
   而它们的**受众是开发者/调用方**（拼错枚举值的 API 调用者），
   正常使用中撞不到。本轮按"风险 > 收益"保留原样，在
   `UNTRANSLATED_CODES` 里登记，并有测试断言"它们确实不在表里"
   —— 这样"漏了"与"刻意没做"能被区分开。

2. **上游异常透传**（约 9 处），如 `str(e)`（LLM 失败、PDF 解析失败）。
   文案来自第三方库，翻译它等于给别人的报错编中文。

## 带运行期值的文案（`params` + `{占位符}`）

`AppError` 有一个可选的 `params`（`core/app_error.py`），用来填充文案里的
`{name}` 占位符。**表里存模板，运行时填值**：

```python
# 表里
"MATERIAL_NOT_FOUND": {
    "zh-CN": "资料 {material_id} 不存在",
    "en": "Material {material_id} does not exist",
},
# 调用点
raise AppError(MATERIAL_NOT_FOUND, f"资料 {material_id} 不存在", 404,
               params={"material_id": material_id})
```

三条约定：

1. **占位符名由表定义，不由调用点的表达式定义**。调用点写的是
   `f"... {note.status.value} ..."`，而表里用 `{status}` —— 因为
   `{note.status.value}` 既不是合法的 `format` 字段名，也不该让文案去耦合
   对象的属性路径。**两边不必同名**，只有 `params` 的键要与表里的占位符一致。
2. **`message` 仍然写完整的那句**（带值）。它是兜底，也是 `str(exc)` 的内容 ——
   表里没登记或 `params` 缺键时，行为与加 `params` 之前逐字一致。
3. **表里的 `zh-CN` 与调用点的 f-string 逐字对应**（把 `{表达式}` 换成
   `{占位符名}`）。`tests/test_i18n.py` 有一条守卫扫 f-string 调用点做比对，
   防止"表里写的是 A、代码里改成了 B"这种静默漂移。

## 新增语言时

`SUPPORTED_LOCALES`（`core/i18n.py`）加一种，本表每种 code 加一个键。
`resolve_message` 在缺该语言的翻译时**回退到默认语言**（不是回退到 `fallback`）——
因为表里登记了就说明"这句该被翻译"，只是还没翻。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from .i18n import DEFAULT_LOCALE

#: 结构：`{key: {locale: 文案}}`，`key` 是错误码或 `<错误码>_ALT[n]`
ERROR_MESSAGES: Dict[str, Dict[str, str]] = {
    # ── 笔记 ──
    "NOTE_NOT_FOUND": {"zh-CN": "笔记不存在", "en": "Note not found"},
    "NOTE_NOT_VIDEO": {"zh-CN": "该笔记不是视频类型", "en": "This note is not a video"},
    "NOTE_VIDEO_FILE_MISSING": {
        "zh-CN": "视频文件不存在",
        "en": "The video file does not exist",
    },
    "NOTE_ORIGINAL_NOT_EDITABLE": {
        "zh-CN": "原始版内容不可编辑，请切换到清洗版后编辑",
        "en": "The original version cannot be edited. Switch to the cleaned version first.",
    },
    "NOTE_CONTENT_TOO_LARGE": {
        "zh-CN": "内容过大（超过 5MB），请缩短后重试",
        "en": "Content is too large (over 5 MB). Shorten it and try again.",
    },
    "NOTE_MARKDOWN_PATH_MISSING": {
        "zh-CN": "目标 Markdown 路径为空，资料可能尚未转换完成",
        "en": "The target Markdown path is empty. The material may not be converted yet.",
    },
    "NOTE_EXPORT_EMPTY": {
        "zh-CN": "这篇笔记还没有清洗版，请先清洗或改导原始版",
        "en": "This note has no cleaned version yet. Clean it first, or export the original version.",
    },
    # `NOTE_EXPORT_EMPTY` 的文案来自一个变量（`list_detail.py:562/565` 的
    # `empty_message`），有两条取值，所以两条都要登记 —— 只登记一条的话，
    # 另一条会精确匹配失败、回退成中文。
    "NOTE_EXPORT_EMPTY_ALT": {
        "zh-CN": "这篇笔记的原始版内容读不出来",
        "en": "The original version of this note could not be read",
    },
    # B4 批量导出（独立 code：语义是"这一批全都没有该版本的正文"，
    # 与单篇的 `NOTE_EXPORT_EMPTY` 不是一回事）
    "NOTE_EXPORT_ZIP_EMPTY": {
        "zh-CN": "没有可导出的正文 —— 这些笔记都没有该版本的正文，请换一个版本或先清洗",
        "en": "Nothing to export — these notes have no content in the requested version.",
    },
    "NOTE_EXPORT_TOO_MANY": {
        "zh-CN": "一次最多导出 200 篇笔记，请分批导出",
        "en": "At most 200 notes can be exported at once — please export in batches.",
    },
    "NOTE_NOT_TRASHED": {"zh-CN": "该笔记不在回收站中", "en": "This note is not in the trash"},
    "NOTE_RESTORE_CONFLICT": {
        "zh-CN": "恢复失败：目标位置存在同名笔记，且自动改名已用尽",
        "en": "Restore failed: a note with the same name exists and auto-renaming was exhausted",
    },
    # ⚠️ `NOTE_VERSION_RESTORE_REJECTED` **刻意不进表**：它的 message 是
    # `str(e)`（`versions.py:215`），内容是 `笔记不存在: note_id=...` 这类
    # 带标识符的诊断串。翻译它没有意义（受众是排查问题的人），
    # 所以登记在 UNTRANSLATED_CODES 里。
    "NOTE_NOT_PERSONAL": {
        "zh-CN": "仅个人笔记可设置关联资料",
        "en": "Only personal notes can have linked materials",
    },
    "NOTE_MATERIAL_LINK_NOT_FOUND": {
        "zh-CN": "笔记-资料关联不存在或无权访问",
        "en": "The note–material link does not exist or you have no access to it",
    },
    # ── 笔记：带运行期值的校验（`params`）──
    # `NOTE_ROLE_INVALID` 有两种句式：列表接口给"有效值清单"，上传接口不给。
    # 主项是前者（更常见，3 处 vs 1 处），后者登记成 `_ALT`。
    # ⚠️ 占位符名与调用点表达式不同名是刻意的，见文件头第 1 条约定。
    "NOTE_ROLE_INVALID": {
        "zh-CN": "无效的 note_role 值: {role}，有效值为: {valid}",
        "en": "Invalid note_role: {role}. Valid values are: {valid}",
    },
    "NOTE_ROLE_INVALID_ALT": {
        "zh-CN": "无效的 note_role 值: {role}",
        "en": "Invalid note_role: {role}",
    },
    # `NOTE_STATUS_INVALID` 有 6 种句式（触发条件各不相同），全部登记。
    # 主项选"触发清洗"那条（它是最早写入的、也是最典型的）。
    "NOTE_STATUS_INVALID": {
        "zh-CN": "笔记当前状态为 {status}，只有 converted、cleaned 或 cleaning_failed 状态可以触发清洗",
        "en": "This note is in status {status}; only converted, cleaned or cleaning_failed "
        "notes can start cleaning",
    },
    "NOTE_STATUS_INVALID_ALT": {
        "zh-CN": "笔记当前状态为 {status}，只有 cleaning 状态可以停止清洗",
        "en": "This note is in status {status}; only cleaning notes can stop cleaning",
    },
    "NOTE_STATUS_INVALID_ALT2": {
        "zh-CN": "笔记正在处理中（{status}），暂不可编辑",
        "en": "This note is being processed ({status}) and cannot be edited yet",
    },
    "NOTE_STATUS_INVALID_ALT3": {
        "zh-CN": "当前状态 {status} 不允许归档/取消归档操作",
        "en": "Archiving or unarchiving is not allowed while the note is in status {status}",
    },
    "NOTE_STATUS_INVALID_ALT4": {
        "zh-CN": "笔记当前状态为 {status}，只有 cleaned、learning_failed 或 archived 状态可以触发理解",
        "en": "This note is in status {status}; only cleaned, learning_failed or archived "
        "notes can start understanding",
    },
    "NOTE_STATUS_INVALID_ALT5": {
        "zh-CN": "当前状态为 {status}，仅失败状态可重试",
        "en": "The note is in status {status}; only failed notes can be retried",
    },
    # `MATERIAL_*`：`material_id` 在调用点就是纯标量，两边同名
    "MATERIAL_NOT_FOUND": {
        "zh-CN": "资料 {material_id} 不存在",
        "en": "Material {material_id} does not exist",
    },
    "MATERIAL_ROLE_INVALID": {
        "zh-CN": "笔记 {material_id} 不是学习资料",
        "en": "Note {material_id} is not a study material",
    },

    # ── 批注 ──
    "ANNOTATION_NOT_FOUND": {"zh-CN": "批注不存在", "en": "Annotation not found"},
    "ANNOTATION_TOO_LONG": {"zh-CN": "批注内容过长", "en": "The annotation is too long"},
    "ANNOTATION_TYPE_INVALID": {
        "zh-CN": "type 必须为 highlight 或 underline",
        "en": "type must be highlight or underline",
    },
    "ANNOTATION_VIEW_MODE_INVALID": {
        "zh-CN": "view_mode 必须为 original 或 clean",
        "en": "view_mode must be original or clean",
    },

    # ── 知识图谱 ──
    "GRAPH_NODE_NOT_FOUND": {
        "zh-CN": "节点不存在或无权访问",
        "en": "The node does not exist or you have no access to it",
    },
    "GRAPH_RELATION_IDS_EMPTY": {
        "zh-CN": "关系 ID 列表不能为空",
        "en": "The relation ID list cannot be empty",
    },
    "GRAPH_RELATION_NOT_FOUND": {
        "zh-CN": "关系不存在",
        "en": "The relation does not exist",
    },
    # ⚠️ 这一条**只**对应 `delete_relation` 的返回值 —— 该函数只有这一种失败
    # （实测 `graph_service.py:792`）。确认/拒绝那两条路径的文案在
    # `GRAPH_RELATION_OPERATION_FAILED` 下，那是另一个码、且刻意未翻译
    # （它是"操作没成功"的笼统出口，文案由服务层给，见 UNTRANSLATED_CODES）。

    # ── 知识卡片 ──
    "CARD_NOT_FOUND": {"zh-CN": "卡片不存在", "en": "Card not found"},
    "CARD_NOT_FOUND_ALT": {"zh-CN": "知识卡片不存在", "en": "Knowledge card not found"},
    "CARD_NOT_FOUND_ALT2": {
        "zh-CN": "知识卡片不存在或无权访问",
        "en": "The knowledge card does not exist or you have no access to it",
    },
    "CARD_NOT_EXTENSION": {
        "zh-CN": "仅拓展卡片可使用此端点",
        "en": "Only extension cards can use this endpoint",
    },
    "CARD_REVIEW_STATE_UNAVAILABLE": {
        "zh-CN": "卡片复习状态不可用",
        "en": "Card review state is unavailable",
    },
    # 合法值清单**写死在文案里**（不放进占位符）：调用点的清单也是硬编码的
    # `regular/blind_spot/extension`，把清单塞进 `params` 只会让两边有第二个
    # 漂移点 —— 改枚举值时要改两处，而文案里那一处不会被编译器发现。
    "CARD_CATEGORY_INVALID": {
        "zh-CN": "无效的卡片类别: {category}，合法值为 regular/blind_spot/extension",
        "en": "Invalid card category: {category}. Valid values are "
        "regular/blind_spot/extension",
    },

    # ── 清洗 ──
    "CLEANING_IN_PROGRESS": {
        "zh-CN": "清洗任务正在进行中，请等待完成或先停止",
        "en": "A cleaning task is already running. Wait for it to finish or stop it first.",
    },
    "CLEANING_NOT_DONE": {
        "zh-CN": "笔记尚未完成清洗",
        "en": "This note has not been cleaned yet",
    },
    "CLEANING_CONTENT_UNREADABLE": {
        "zh-CN": "无法读取清洗副本内容",
        "en": "Could not read the cleaned copy",
    },
    "CLEANING_CONTENT_UNREADABLE_ALT": {
        "zh-CN": "无法读取 Markdown 内容",
        "en": "Could not read the Markdown content",
    },
    # 占位符 `{block_index}` 对应调用点的 `{block_index}`（两边同名，因为那是纯标量）
    "CLEANING_MARKER_NOT_FOUND": {
        "zh-CN": "未在清洗副本中找到块 {block_index} 的重复标记（可能已被处理）",
        "en": "No duplicate marker for block {block_index} was found in the cleaned copy "
        "(it may have been handled already)",
    },

    # ── 理解 / 出题 / 问答 ──
    "UNDERSTANDING_IN_PROGRESS": {
        "zh-CN": "理解任务正在进行中，请等待完成",
        "en": "An understanding task is already running. Wait for it to finish.",
    },
    "UNDERSTANDING_NO_CARDS": {
        "zh-CN": "该笔记暂无知识卡片，请先触发理解管道",
        "en": "This note has no knowledge cards yet. Run the understanding pipeline first.",
    },
    "EMPTY_QUESTION": {"zh-CN": "问题不能为空", "en": "The question cannot be empty"},

    # ── 上传 ──
    "UPLOAD_FILE_NAME_EMPTY": {
        "zh-CN": "文件名不能为空",
        "en": "The file name cannot be empty",
    },
    "UPLOAD_FILE_NAME_INVALID": {"zh-CN": "文件名不合法", "en": "Invalid file name"},
    "UPLOAD_FILE_NAME_INVALID_ALT": {
        "zh-CN": "文件名不能包含路径分隔符",
        "en": "The file name cannot contain path separators",
    },
    "UPLOAD_FILE_NAME_TOO_LONG": {
        "zh-CN": "文件名过长（最多 255 字符）",
        "en": "The file name is too long (255 characters max)",
    },
    "UPLOAD_FORMAT_UNSUPPORTED": {
        "zh-CN": "临时文件格式不受支持",
        "en": "The temporary file format is not supported",
    },
    "UPLOAD_FORMAT_UNSUPPORTED_ALT": {
        "zh-CN": "不支持的文件格式",
        "en": "Unsupported file format",
    },
    # 第三、四种文案：带"收到的扩展名 + 支持清单"（实测 `upload.py:616/703`）。
    # ⚠️ 支持清单**写死在文案里**还是放进占位符，两种做法都有代价：
    # 写死 → 改 `ALLOWED_EXTS` 时文案不会跟着变（但至少编译器不报错）；
    # 放进占位符 → 文案随配置变，但"哪些是支持的"就不再可翻译。
    # 这里选**放进占位符**：清单本身是机器可读的取值，翻译它没有意义，
    # 而写死会让文案与实际支持的类型**静默不一致**（用户看到"支持 pdf"却传不进 pdf）。
    "UPLOAD_FORMAT_UNSUPPORTED_ALT2": {
        "zh-CN": "不支持的文件格式: {ext}，支持: {allowed}",
        "en": "Unsupported file format: {ext}. Supported: {allowed}",
    },
    # ── 上传：带运行期值的校验（`params`）──
    # 占位符名与调用点表达式不同名是**刻意**的：调用点写的是
    # `{max_size // (1024 * 1024)}`（一个算式），表里用 `{max_mb}`。
    # 文案不该耦合调用点的算式（见文件头"带运行期值的文案"第 1 条）。
    "UPLOAD_FILE_TOO_LARGE": {
        "zh-CN": "文件大小超过限制 ({max_mb}MB)",
        "en": "The file exceeds the size limit ({max_mb} MB)",
    },
    "UPLOAD_STORAGE_QUOTA_EXCEEDED": {
        "zh-CN": "存储空间不足：已使用 {used_mb}MB，配额 {quota_mb}MB",
        "en": "Not enough storage: {used_mb} MB used of a {quota_mb} MB quota",
    },
    "UPLOAD_CONTENT_MISMATCH": {
        "zh-CN": "文件内容与格式不匹配，不是有效的 {ext} 文件",
        "en": "The file content does not match its format; it is not a valid {ext} file",
    },
    "UPLOAD_EXTENSION_MISMATCH": {
        "zh-CN": "文件名扩展名必须为 {ext}，请保留文件类型后缀",
        "en": "The file name must keep the {ext} extension; do not drop the file type suffix",
    },
    "UPLOAD_CROP_UNSUPPORTED_TYPE": {
        "zh-CN": "仅支持 PDF 裁剪，当前文件类型为 {source_type}",
        "en": "Only PDF cropping is supported; the current file type is {source_type}",
    },
    "UPLOAD_SCRIPT_CONTENT_REJECTED": {
        "zh-CN": "检测到疑似脚本注入内容（{violation}），已拒绝上传；"
        "请移除相关 HTML 标签或事件属性后重试",
        "en": "Possible script injection detected ({violation}); the upload was rejected. "
        "Remove the HTML tags or event attributes and try again.",
    },
    "UPLOAD_PDF_PARSE_FAILED": {
        "zh-CN": "PDF 解析失败：文件可能已损坏或加密，请确认后重试",
        "en": "PDF parsing failed: the file may be corrupted or encrypted. Check it and try again.",
    },
    "UPLOAD_PDF_CROP_FAILED": {
        "zh-CN": "PDF 裁剪失败：页码范围可能无效，或文件已损坏",
        "en": "PDF cropping failed: the page range may be invalid, or the file is corrupted.",
    },
    "UPLOAD_STORAGE_WRITE_FAILED": {
        "zh-CN": "文件上传失败，请稍后重试",
        "en": "Upload failed. Please try again later.",
    },
    "UPLOAD_TEMP_ID_INVALID": {
        "zh-CN": "无效的临时上传标识",
        "en": "Invalid temporary upload ID",
    },
    "UPLOAD_TEMP_EXPIRED": {
        "zh-CN": "临时上传已失效，请重新选择文件",
        "en": "The temporary upload has expired. Please select the file again.",
    },
    "UPLOAD_TEMP_DATA_INVALID": {
        "zh-CN": "临时上传数据异常，请重新选择文件",
        "en": "The temporary upload data is invalid. Please select the file again.",
    },

    # ── 文件夹 ──
    "FOLDER_NOT_FOUND": {"zh-CN": "文件夹不存在", "en": "Folder not found"},
    "FOLDER_NOT_FOUND_ALT": {
        "zh-CN": "文件夹不存在或无权访问",
        "en": "The folder does not exist or you have no access to it",
    },
    "FOLDER_DATE_INVALID": {
        "zh-CN": "无效的日期格式，请使用 ISO 格式如 2024-01-15",
        "en": "Invalid date format. Use an ISO date such as 2024-01-15.",
    },

    # ── 项目 ──
    "PROJECT_NOT_FOUND": {"zh-CN": "项目不存在", "en": "Project not found"},

    # ── 学习目标 ──
    #
    # ⚠️ 这两个码的中文**本来就是英文**（实测：`raise AppError(GOAL_NOT_FOUND,
    # "Goal not found", 404)`）。这不是本轮引入的 —— 迁表时**保持现状**，
    # 因为改中文界面的文案属于产品决策，不属于 i18n 基础设施。
    # 于是两种语言下值相同，这是**如实记录既有行为**，不是漏翻。
    "GOAL_NOT_FOUND": {"zh-CN": "Goal not found", "en": "Goal not found"},
    "GOAL_NONE_ACTIVE": {
        "zh-CN": "No active goals, please create a goal first",
        "en": "No active goals. Create a goal first.",
    },
    "GOAL_SCOPE_NOTE_INVALID": {
        "zh-CN": "目标范围包含不存在或无权访问的笔记",
        "en": "The goal scope includes a note that does not exist or is not accessible",
    },
    "GOAL_SCOPE_FOLDER_INVALID": {
        "zh-CN": "目标范围包含不存在或无权访问的文件夹",
        "en": "The goal scope includes a folder that does not exist or is not accessible",
    },

    # ── 版本 ──
    #
    # 两条文案都带运行期值（note_id / version_number），但它们的**可变部分
    # 只是标识符**，句子骨架是固定的 —— 所以拆成模板 + 参数是可行的
    # （`version_service.py:210/228`）。表里存 `{...}` 占位符，
    # 由 `resolve_message` 之外的调用方负责填充；这里先给出双语骨架。
    #
    # ⚠️ 现在**还不会生效**：`version_service` 传的是已格式化好的 f-string，
    # 与本表的 `zh-CN` 不逐字相同，于是精确匹配失败、回退到原中文。
    # 要让它们生效，需要把 f-string 改成传 `params`（属"给 AppError 加
    # params"那一批，见 UNTRANSLATED_CODES 的说明）。
    # 登记在这里是为了让翻译**先就位**，也让"哪些码已决定怎么翻"可查。
    "VERSION_NOT_FOUND": {
        "zh-CN": "版本不存在",
        "en": "Version not found",
    },

    # ── 复习 ──
    "REVIEW_QUIZ_NOT_FOUND": {
        "zh-CN": "题目不存在或不属于该笔记",
        "en": "The question does not exist or does not belong to this note",
    },
    # B5：编辑选择题时选项少于 2 个（少于 2 个无法区分对错）
    "QUIZ_OPTIONS_REQUIRED": {
        "zh-CN": "选择题至少需要 2 个选项",
        "en": "A multiple-choice question needs at least 2 options",
    },
    "REVIEW_REMINDERS_FAILED": {
        "zh-CN": "获取复习提醒数据失败",
        "en": "Failed to load review reminders",
    },

    # ── 任务 ──
    "TASK_NOT_FOUND": {"zh-CN": "任务不存在", "en": "Task not found"},

    # ── LLM 用量 / 提示词版本 ──
    # 占位符 `{choices}` / `{version}` 与调用点表达式不同名（那边是
    # `'/'.join(GROUP_BY_CHOICES)` 与 `{version!r}`），见文件头第 1 条约定。
    "LLM_USAGE_GROUP_BY_INVALID": {
        "zh-CN": "group_by 只能是 {choices} 之一",
        "en": "group_by must be one of: {choices}",
    },
    # ⚠️ 中文文案里的 `unknown` 与 `1 / 2` **刻意保留原样**：它们是**字面量取值**，
    # 不是可翻译的词（调用点写的也是反引号包起来的代码字面量）。
    "PROMPT_VERSION_INVALID": {
        "zh-CN": "version 只能是 `unknown`（版本未知那一桶）或纯数字版本号"
        "（如 1 / 2），收到 {version}",
        "en": "version must be either `unknown` (the bucket for unknown versions) or a "
        "numeric version (e.g. 1 / 2); got {version}",
    },

    # ── 学习目标：带运行期值的校验 ──
    # ⚠️ 调用点给的**原文案是英文**（`goal_service.py:150`
    # 的 `f"Maximum {MAX_ACTIVE_GOALS} active goals"`）—— 实测确认，不是笔误。
    # 所以本条的 `en` 与调用点逐字相同，`zh-CN` 是**新写的**。
    # 这是本轮唯一一条"中文是新增的"条目；其余条目的中文都来自调用点。
    "GOAL_ACTIVE_LIMIT_REACHED": {
        "zh-CN": "活跃目标数已达上限（最多 {max_goals} 个）",
        "en": "Maximum {max_goals} active goals",
    },

    # ── 认证 ──
    "AUTH_INVALID_CREDENTIALS": {
        "zh-CN": "邮箱或密码错误",
        "en": "Incorrect email or password",
    },
}

#: `HTTPException` 的文案（它们没有业务错误码，只有状态码）
#:
#: ⚠️ 这张表**不能**按状态码给通用文案：401 在这一处是"无效或过期的令牌"，
#: 在另一处可能是别的意思。所以键是**原文案本身**（中文），
#: 值是对应语言的翻译 —— 这样"同一个中文句子在不同端点出现"会自动共用翻译，
#: 而"同一状态码下不同句子"不会互相覆盖。
HTTP_STATUS_MESSAGES: Dict[str, Dict[str, str]] = {
    "无效或过期的令牌": {
        "zh-CN": "无效或过期的令牌",
        "en": "Invalid or expired token",
    },
    "用户不存在或已禁用": {
        "zh-CN": "用户不存在或已禁用",
        "en": "User does not exist or has been disabled",
    },
    "未提供认证令牌": {
        "zh-CN": "未提供认证令牌",
        "en": "Authentication token is required",
    },
    "刷新令牌无效或已失效，请重新登录": {
        "zh-CN": "刷新令牌无效或已失效，请重新登录",
        "en": "Refresh token is invalid or expired. Please sign in again.",
    },
}

#: 中间件里硬编码的那句 500 文案（不在任何 `AppError` 里，所以单独登记）
INTERNAL_ERROR_MESSAGES: Dict[str, str] = {
    "zh-CN": "服务器内部错误，请稍后重试",
    "en": "Internal server error. Please try again later.",
}

#: 参数校验失败（`ValidationError` → 422）的兜底文案。
#: ⚠️ 现有行为是把 Pydantic 的英文报错**原样**放进 detail（`str(exc)`），
#: 那是**英文**的 —— 所以本表只在"拿不到 str(exc)"时兜底，不参与正常路径。
VALIDATION_ERROR_MESSAGES: Dict[str, str] = {
    "zh-CN": "请求参数校验失败",
    "en": "Request validation failed",
}

#: 刻意**没有**进表的错误码
#:
#: 这份名单的作用是让"漏了"与"刻意没做"能被区分开：
#: `tests/test_i18n.py` 断言这些码确实不在 `ERROR_MESSAGES` 里。
#: 哪天把某一条迁进表，就从这里删掉它。
#:
#: ## 2026-10-21 的修订：从 32 个减到 17 个
#:
#: 原先这 32 个被统一描述成"受众是开发者/调用方的入参校验"。**那句话不准确** ——
#: 逐个核实调用点后发现，其中 15 个的文案是**用户可见的**，它们没进表只是因为
#: `AppError` 当时没有 `params`，带运行期值的文案无法进模板表。
#: 加了 `params` 之后那 15 个已迁走（见 `ERROR_MESSAGES` 里带 `{占位符}` 的条目）。
#:
#: 剩下这 17 个按**真正的原因**分三类：
#:
#: 1. **上游透传**（10 个）：`str(e)` 或 `result["error"]`，文案由服务层/第三方
#:    库生成。要翻译得改服务层的返回契约，属另一件事。
#:    - `ASSESSMENT_*`（4 个）：⚠️ 这条出口把**内部异常文本**回给客户端
#:      （`assessment.py:47-49` 的注释、计划里的 H5 项）。翻译它没有意义 ——
#:      文本是任意的。**该修的是那个泄漏本身，不是它的翻译。**
#:    - `COMBINED_EXTRACT_FAILED` / `EXTENSION_GENERATE_FAILED`：文案来自
#:      `knowledge_link_service` 的 `{"error": ...}`（十余条不同句子）。
#:    - `FOLDER_NOT_EMPTY` / `PROJECT_NOTE_LINK_NOT_FOUND` /
#:      `GRAPH_SUGGESTION_FAILED` / `VERSION_CONTENT_UNAVAILABLE` /
#:      `AUTH_REGISTRATION_REJECTED`：服务层 `ValueError` / 异常文本。
#: 2. **刻意不翻**（1 个）：`NOTE_VERSION_RESTORE_REJECTED` 的 message 是
#:    `str(e)`（`versions.py:215`），内容是 `笔记不存在: note_id=...` 这类
#:    带标识符的诊断串，受众是排查问题的人。
#: 3. **仍待处理**（6 个）：`DAILY_REVIEW_LIMIT_REACHED`、
#:    `GRAPH_RELATION_OPERATION_FAILED`、`UPLOAD_NOTE_COUNT_LIMIT_REACHED`、
#:    `UPLOAD_ARCHIVE_REJECTED`、`UPLOAD_PDF_TOO_MANY_PAGES`、
#:    `AUTH_REGISTRATION_REJECTED` —— 同样是上游给的 `reason` 串，
#:    但文案是**固定句式**（不含运行期值），理论上可以直接进表；
#:    没有本轮做是因为要先把服务层的 `reason` 改成**错误码**而不是句子，
#:    否则表里存中文、服务层给中文，两边靠字符串相等维系，一改就断。
UNTRANSLATED_CODES: frozenset = frozenset(
    {
        # ── 上游透传 ──
        "ASSESSMENT_COMPARE_FAILED",
        "ASSESSMENT_QUIZ_GENERATE_FAILED",
        "ASSESSMENT_ANSWER_REJECTED",
        "ASSESSMENT_ANSWER_FAILED",
        "COMBINED_EXTRACT_FAILED",
        "EXTENSION_GENERATE_FAILED",
        "FOLDER_NOT_EMPTY",
        "PROJECT_NOTE_LINK_NOT_FOUND",
        "GRAPH_SUGGESTION_FAILED",
        "VERSION_CONTENT_UNAVAILABLE",
        # ── 刻意不翻（诊断串）──
        "NOTE_VERSION_RESTORE_REJECTED",
        # ── 仍待处理（服务层给句子而非错误码）──
        "DAILY_REVIEW_LIMIT_REACHED",
        "GRAPH_RELATION_OPERATION_FAILED",
        "UPLOAD_NOTE_COUNT_LIMIT_REACHED",
        "UPLOAD_ARCHIVE_REJECTED",
        "UPLOAD_PDF_TOO_MANY_PAGES",
        "AUTH_REGISTRATION_REJECTED",
    }
)

#: `(code, fallback) -> key` 的索引，让"同码多文案"能精确命中
_ALT_INDEX: Dict[tuple, str] = {}


def _base_code(key: str) -> str:
    """`CARD_NOT_FOUND_ALT2` / `NOTE_STATUS_INVALID_ALT5` → 去掉 `_ALT[n]` 后缀

    ⚠️ 用正则匹配**任意**编号，不要写死 `("_ALT2", "_ALT")` 那样的元组 ——
    原先就是写死的，于是 `_ALT3` 及以上不被认作变体，
    `registered_codes()` 会把它们当成"表里登记了但代码里没人用"的独立码，
    测试直接报"这些 code 没有任何 AppError 使用"（实测踩到：
    加 `NOTE_STATUS_INVALID_ALT3/4/5` 后 `test_every_registered_code_exists_in_code` 红）。

    `_ALT` 后面**必须**跟数字或结束，否则 `CARD_NOT_FOUND_ALTITUDE` 这类
    会被误剥 —— 虽然现在没有这种码，但判据应当写对。
    """
    m = re.match(r"^(.*?)_ALT(\d*)$", key)
    return m.group(1) if m else key


def _build_alt_index() -> None:
    """把 `<CODE>_ALT[n]` 的表项按其中文文案反查回 `(code, 中文)`

    为什么按**中文**反查而不是按调用点行号：中文就是 `raise` 时传进来的
    `fallback`，这是运行期唯一能拿到的判据。代价是"两条不同文案恰好相同"
    时只命中一条 —— 那时两条的翻译也应当相同，所以无害。
    """
    for key, entry in ERROR_MESSAGES.items():
        base = _base_code(key)
        if base == key:
            continue
        zh = entry.get(DEFAULT_LOCALE)
        if zh:
            _ALT_INDEX[(base, zh)] = key


_build_alt_index()


class _SafeParams(dict):
    """`str.format_map` 用的参数字典：**缺键时原样保留占位符**，不抛异常

    ## 为什么要这个而不是直接 `template.format(**params)`

    `format` 在缺键时抛 `KeyError`。而这里是**错误处理路径** ——
    为了给用户报错反而自己炸掉、最后变成一个 500，是最糟的形态。

    保留 `{value}` 原样虽然难看，但：它是**局部**问题（只有一个占位符没填上），
    而不是整个请求失败；日志里也能一眼看出是哪个参数没传。

    `format_map` 只走 `__missing__`，所以继承 `dict` 就够了。
    """

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def fill_params(template: str, params: Optional[Dict[str, Any]]) -> str:
    """把 `{name}` 占位符换成 `params[name]`

    - `params` 为空 → 原样返回（不解析，避免把文案里本就存在的花括号当占位符）；
    - 缺某个键 → 保留该占位符（见 `_SafeParams`）；
    - 模板本身花括号不配对 → 原样返回（同上，不让错误路径抛异常）。
    """
    if not params:
        return template
    try:
        return template.format_map(_SafeParams(params))
    except (ValueError, IndexError):
        # 花括号不配对之类 —— 宁可给未填充的模板，也不要 500
        return template


def resolve_message(
    code: str,
    fallback: str,
    locale: str = DEFAULT_LOCALE,
    params: Optional[Dict[str, Any]] = None,
) -> str:
    """
    按错误码 + 语言取文案；表里没有就用 `fallback`

    查表顺序（"同码多文案"就靠这个顺序区分）：
    1. `(code, fallback)` 精确命中 `_ALT` 项 → 用那一条；
    2. `code` 的主项存在 → 用主项；
    3. 都没有 → `fallback`（= 现有行为）。

    Args:
        code: 业务错误码（`AppError.code`）
        fallback: `raise` 时写的那句 `message`（= 现有行为）
        locale: 目标语言
        params: 模板参数，用于填充文案里的 `{name}` 占位符
            （见 `AppError.params`；带运行期值的文案靠它才能进表）

    Returns:
        该语言的文案；该 code 未登记、或登记了但没有这个语言的翻译时，
        返回 `fallback`（**不是空串** —— 空串会让用户看到一个没有内容的错误框）
    """
    key = _ALT_INDEX.get((code, fallback)) or code
    entry = ERROR_MESSAGES.get(key)
    if not entry:
        return fallback
    template = entry.get(locale) or entry.get(DEFAULT_LOCALE) or fallback
    return fill_params(template, params)


def resolve_http_status_message(detail: str, locale: str = DEFAULT_LOCALE) -> str:
    """
    按**中文原文**取 `HTTPException` 的翻译

    为什么键是原文而不是状态码：见 `HTTP_STATUS_MESSAGES` 的说明。
    没有登记时返回原 `detail`（现有行为）。
    """
    entry = HTTP_STATUS_MESSAGES.get(detail)
    if not entry:
        return detail
    return entry.get(locale) or entry.get(DEFAULT_LOCALE) or detail


def internal_error_message(locale: str = DEFAULT_LOCALE) -> str:
    """500 兜底文案"""
    return INTERNAL_ERROR_MESSAGES.get(locale) or INTERNAL_ERROR_MESSAGES[DEFAULT_LOCALE]


def validation_error_message(locale: str = DEFAULT_LOCALE) -> str:
    """422 兜底文案（正常路径用 Pydantic 自己的英文报错，不走这里）"""
    return VALIDATION_ERROR_MESSAGES.get(locale) or VALIDATION_ERROR_MESSAGES[DEFAULT_LOCALE]


def registered_codes() -> list:
    """已登记的主错误码清单（不含 `_ALT`，供测试断言"表与调用点一致"）"""
    out = []
    for key in ERROR_MESSAGES:
        base = _base_code(key)
        if base not in out:
            out.append(base)
    return sorted(out)


def _lookup_for_test(code: str, locale: str) -> Optional[str]:
    """仅供测试：取某 code 某语言的登记值（未登记返回 None）"""
    return (ERROR_MESSAGES.get(code) or {}).get(locale)
