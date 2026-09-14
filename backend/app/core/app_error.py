"""
应用层错误契约模块
================

定义统一的业务异常类型 AppError 与常用错误码常量，作为后端错误响应的契约设施。

AppError 由 middleware/error_handler.py 统一透传：中间件捕获后把
code -> error_code、message -> detail、http_status -> HTTP 状态码，
保证业务错误以统一 JSON 结构 {"detail", "error_code", "request_id"} 返回。

设计决策：
- AppError 直接继承 Exception（不继承 ValueError / HTTPException）：
  业务代码抛出后不被 API 层的 `except ValueError` 兜底吞掉，也不会被
  FastAPI ExceptionMiddleware 当作 HTTPException 处理，而是逐层上抛，
  最终由 ErrorHandlerMiddleware 捕获并转换为统一错误响应。
- data 为可选附带数据（默认 None），供错误响应需要携带结构化信息时使用，
  由中间件在响应体中以 data 字段透传（仅当非 None）。
- 新增错误码常量时在下方常量区追加，保持 code 全大写、语义化命名。
"""

from typing import Any, Optional


# ---- 常用错误码常量 ----
#
# 命名约定（沿用 VERSION_NOT_FOUND 的既有写法）：
# - 全大写 UPPER_SNAKE_CASE，值是字符串本身，前端按值比对（不是按变量名）；
# - 语义化，**不把 HTTP 状态码写进名字** —— 状态码由 http_status 单独表达，
#   同一个 code 将来调整状态码时不应改名；
# - 按"资源 + 条件"取义（NOTE_NOT_FOUND / FOLDER_NOT_EMPTY），
#   而不是按端点路径取义（那是把接口路径抄进错误码，重构即失效）；
# - 一个 code 只表达一件事：需要前端分流的分支必须能靠 code 区分，
#   合并两个不同含义的 code 等于把中文文案匹配换个地方做。
#
# 用法：`raise AppError(NOTE_NOT_FOUND, "笔记不存在", 404)`。
# 状态码与文案**原样保留**迁移前的取值 —— 本契约只新增 error_code，
# 不改 HTTP 状态、不改用户可见文案。

# 版本缺失（版本记录不存在或版本内容文件已丢失）
VERSION_NOT_FOUND = "VERSION_NOT_FOUND"
# 版本内容读不出来（记录在但内容解码失败，如非 UTF-8 的 Markdown）
VERSION_CONTENT_UNAVAILABLE = "VERSION_CONTENT_UNAVAILABLE"

# ---- 笔记（notes/**、upload.py、cleaning.py、understanding.py 共用）----
NOTE_NOT_FOUND = "NOTE_NOT_FOUND"
# note_role 取值非法（只允许 personal_note / material 等）
NOTE_ROLE_INVALID = "NOTE_ROLE_INVALID"
# 该接口只对视频笔记开放
NOTE_NOT_VIDEO = "NOTE_NOT_VIDEO"
# 视频笔记的源文件已丢失
NOTE_VIDEO_FILE_MISSING = "NOTE_VIDEO_FILE_MISSING"
# 当前状态不允许本次操作（编辑/归档/重试/清洗/理解各自的闸门）
#
# 为什么多个端点共用一个 code：判据是"调用方能否靠 code 区分处境"。
# 调用方知道自己调的是哪个端点，所以"清洗的状态闸门"和"重试的状态闸门"
# 不会在同一个端点里撞车；为每个端点复制一份同义 code 只会让词表膨胀。
NOTE_STATUS_INVALID = "NOTE_STATUS_INVALID"
# 原始版内容不可编辑（需先切到清洗版）
NOTE_ORIGINAL_NOT_EDITABLE = "NOTE_ORIGINAL_NOT_EDITABLE"
# 内容过大
NOTE_CONTENT_TOO_LARGE = "NOTE_CONTENT_TOO_LARGE"
# 笔记没有可写入的 Markdown 路径（资料尚未转换完成等）
NOTE_MARKDOWN_PATH_MISSING = "NOTE_MARKDOWN_PATH_MISSING"
# 笔记不在回收站中，无法恢复
NOTE_NOT_TRASHED = "NOTE_NOT_TRASHED"
# 恢复时 inbox 同名冲突且改名序号耗尽
NOTE_RESTORE_CONFLICT = "NOTE_RESTORE_CONFLICT"
# 恢复历史版本被拒（笔记不存在 / 无可写路径 / 当前内容读取失败）
NOTE_VERSION_RESTORE_REJECTED = "NOTE_VERSION_RESTORE_REJECTED"

# ---- 笔记-资料关联 / 批注 ----
# 被引用的资料不存在或无权访问
MATERIAL_NOT_FOUND = "MATERIAL_NOT_FOUND"
# 被引用的笔记不是学习资料（note_role != material）
MATERIAL_ROLE_INVALID = "MATERIAL_ROLE_INVALID"
# 该笔记不是个人笔记（只有个人笔记能设置关联资料）
NOTE_NOT_PERSONAL = "NOTE_NOT_PERSONAL"
ANNOTATION_NOT_FOUND = "ANNOTATION_NOT_FOUND"
ANNOTATION_TYPE_INVALID = "ANNOTATION_TYPE_INVALID"
ANNOTATION_VIEW_MODE_INVALID = "ANNOTATION_VIEW_MODE_INVALID"
ANNOTATION_TOO_LONG = "ANNOTATION_TOO_LONG"

# ---- 知识图谱 ----
# 节点不存在或无权访问
GRAPH_NODE_NOT_FOUND = "GRAPH_NODE_NOT_FOUND"
# 关系不存在或无权访问
GRAPH_RELATION_NOT_FOUND = "GRAPH_RELATION_NOT_FOUND"
# 批量操作的 relation_ids 为空
GRAPH_RELATION_IDS_EMPTY = "GRAPH_RELATION_IDS_EMPTY"
# 关系操作未成功（确认/拒绝/创建；原因见文案）
GRAPH_RELATION_OPERATION_FAILED = "GRAPH_RELATION_OPERATION_FAILED"
# 自动关系建议生成失败（上游嵌入/LLM 不可用）
GRAPH_SUGGESTION_FAILED = "GRAPH_SUGGESTION_FAILED"

# ---- 知识卡片 / 联合分析 ----
# 卡片不存在或无权访问（复习与卡片接口共用）
CARD_NOT_EXTENSION = "CARD_NOT_EXTENSION"
CARD_CATEGORY_INVALID = "CARD_CATEGORY_INVALID"
# 笔记-资料关联不存在或无权访问
NOTE_MATERIAL_LINK_NOT_FOUND = "NOTE_MATERIAL_LINK_NOT_FOUND"
# 笔记-资料联合分析失败
COMBINED_EXTRACT_FAILED = "COMBINED_EXTRACT_FAILED"
# 拓展卡片生成失败
EXTENSION_GENERATE_FAILED = "EXTENSION_GENERATE_FAILED"

# ---- 评估（比对 / 出题 / 提交答案）----
ASSESSMENT_COMPARE_FAILED = "ASSESSMENT_COMPARE_FAILED"
ASSESSMENT_QUIZ_GENERATE_FAILED = "ASSESSMENT_QUIZ_GENERATE_FAILED"
# 提交答案被拒（评估记录不存在 / 不是开放性问题模式）
ASSESSMENT_ANSWER_REJECTED = "ASSESSMENT_ANSWER_REJECTED"
ASSESSMENT_ANSWER_FAILED = "ASSESSMENT_ANSWER_FAILED"

# ---- LLM 用量 / 提示词版本 ----
LLM_USAGE_GROUP_BY_INVALID = "LLM_USAGE_GROUP_BY_INVALID"
PROMPT_VERSION_INVALID = "PROMPT_VERSION_INVALID"

# ---- 清洗 ----
# 清洗任务正在进行中（禁止重复触发）
CLEANING_IN_PROGRESS = "CLEANING_IN_PROGRESS"
# 笔记尚未产出清洗副本（clean_md_path 为空）
CLEANING_NOT_DONE = "CLEANING_NOT_DONE"
# 清洗副本读不出来（内容为空）
CLEANING_CONTENT_UNREADABLE = "CLEANING_CONTENT_UNREADABLE"
# 清洗副本里找不到目标块的重复标记（可能已被处理）
CLEANING_MARKER_NOT_FOUND = "CLEANING_MARKER_NOT_FOUND"

# ---- 理解 / 出题 / 问答 ----
# 理解任务正在进行中（禁止重复触发）
UNDERSTANDING_IN_PROGRESS = "UNDERSTANDING_IN_PROGRESS"
# 笔记还没有知识卡片，需先跑理解管道
UNDERSTANDING_NO_CARDS = "UNDERSTANDING_NO_CARDS"
# 空问题（与 ask/understand SSE 事件里的 EMPTY_QUESTION 同义，沿用同一命名）
EMPTY_QUESTION = "EMPTY_QUESTION"

# ---- 上传（两道关卡：文件本身 / 两阶段暂存）----
# 文件超过单文件大小上限
UPLOAD_FILE_TOO_LARGE = "UPLOAD_FILE_TOO_LARGE"
# 超出每用户存储配额
UPLOAD_STORAGE_QUOTA_EXCEEDED = "UPLOAD_STORAGE_QUOTA_EXCEEDED"
# 超出每用户笔记数上限
UPLOAD_NOTE_COUNT_LIMIT_REACHED = "UPLOAD_NOTE_COUNT_LIMIT_REACHED"
# 文件名缺失 / 非法（含路径分隔符）/ 过长
UPLOAD_FILE_NAME_EMPTY = "UPLOAD_FILE_NAME_EMPTY"
UPLOAD_FILE_NAME_INVALID = "UPLOAD_FILE_NAME_INVALID"
UPLOAD_FILE_NAME_TOO_LONG = "UPLOAD_FILE_NAME_TOO_LONG"
# 扩展名不在允许列表内
UPLOAD_FORMAT_UNSUPPORTED = "UPLOAD_FORMAT_UNSUPPORTED"
# 重命名时扩展名与真实文件类型不一致
UPLOAD_EXTENSION_MISMATCH = "UPLOAD_EXTENSION_MISMATCH"
# 内容签名（魔术字节）与扩展名不符
UPLOAD_CONTENT_MISMATCH = "UPLOAD_CONTENT_MISMATCH"
# .md 嗅探到脚本注入内容
UPLOAD_SCRIPT_CONTENT_REJECTED = "UPLOAD_SCRIPT_CONTENT_REJECTED"
# Office 压缩包安全检查未通过（压缩比/体积/条目数）
UPLOAD_ARCHIVE_REJECTED = "UPLOAD_ARCHIVE_REJECTED"
# PDF 页数解析失败（详情只进日志）
UPLOAD_PDF_PARSE_FAILED = "UPLOAD_PDF_PARSE_FAILED"
# PDF 页数超过上限
UPLOAD_PDF_TOO_MANY_PAGES = "UPLOAD_PDF_TOO_MANY_PAGES"
# 非 PDF 却要求裁剪
UPLOAD_CROP_UNSUPPORTED_TYPE = "UPLOAD_CROP_UNSUPPORTED_TYPE"
# PDF 裁剪失败（页范围非法或文件损坏，详情只进日志）
UPLOAD_PDF_CROP_FAILED = "UPLOAD_PDF_CROP_FAILED"
# 两阶段上传：temp_id 不合法 / 已失效 / 暂存数据异常
UPLOAD_TEMP_ID_INVALID = "UPLOAD_TEMP_ID_INVALID"
UPLOAD_TEMP_EXPIRED = "UPLOAD_TEMP_EXPIRED"
UPLOAD_TEMP_DATA_INVALID = "UPLOAD_TEMP_DATA_INVALID"
# 写入对象存储失败
UPLOAD_STORAGE_WRITE_FAILED = "UPLOAD_STORAGE_WRITE_FAILED"

# ---- 任务 ----
TASK_NOT_FOUND = "TASK_NOT_FOUND"

# ---- 认证 ----
# 邮箱或密码错误（登录失败，刻意不区分"邮箱不存在"与"密码错"，防用户枚举）
AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
# 注册被拒：邮箱/用户名已被占用，或密码不满足策略（两者文案不同、状态码相同）
AUTH_REGISTRATION_REJECTED = "AUTH_REGISTRATION_REJECTED"

# ---- 文件夹 ----
FOLDER_NOT_FOUND = "FOLDER_NOT_FOUND"
# 文件夹非空，需先移出/删除其中笔记
FOLDER_NOT_EMPTY = "FOLDER_NOT_EMPTY"
# folder_date 不是合法 ISO 日期
FOLDER_DATE_INVALID = "FOLDER_DATE_INVALID"

# ---- 项目 ----
PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
# 笔记与项目的关联不存在（项目不存在/无权访问，或该笔记不在该项目中）
PROJECT_NOTE_LINK_NOT_FOUND = "PROJECT_NOTE_LINK_NOT_FOUND"

# ---- 学习目标 ----
GOAL_NOT_FOUND = "GOAL_NOT_FOUND"
# 活跃目标数已达上限
GOAL_ACTIVE_LIMIT_REACHED = "GOAL_ACTIVE_LIMIT_REACHED"
# 用户没有任何活跃目标，需先创建
GOAL_NONE_ACTIVE = "GOAL_NONE_ACTIVE"
# 目标范围引用了不存在或无权访问的笔记 / 文件夹（IDOR 防护）
GOAL_SCOPE_NOTE_INVALID = "GOAL_SCOPE_NOTE_INVALID"
GOAL_SCOPE_FOLDER_INVALID = "GOAL_SCOPE_FOLDER_INVALID"

# ---- 复习 ----
# 每日答题限额用尽（前端据此直接进入"今日完成"页，而不是报错）
DAILY_REVIEW_LIMIT_REACHED = "DAILY_REVIEW_LIMIT_REACHED"
# 题目不存在 / 不属于当前用户（或不属于指定笔记）
REVIEW_QUIZ_NOT_FOUND = "REVIEW_QUIZ_NOT_FOUND"
# 卡片不存在或无权访问
CARD_NOT_FOUND = "CARD_NOT_FOUND"
# 卡片尚未进入复习调度（review_states 无记录，无法自评）
CARD_REVIEW_STATE_UNAVAILABLE = "CARD_REVIEW_STATE_UNAVAILABLE"
# 复习提醒聚合失败（内部错误，详情只进日志）
REVIEW_REMINDERS_FAILED = "REVIEW_REMINDERS_FAILED"


class AppError(Exception):
    """
    业务异常：携带错误码、人读消息、HTTP 状态码与可选附带数据

    Attributes:
        code: 稳定错误码（如 VERSION_NOT_FOUND），前端据此做程序化判断
        message: 面向用户的可读错误详情（对应响应体 detail）
        http_status: 对应的 HTTP 状态码
        data: 可选附带数据，透传到响应体 data 字段（默认 None）
    """

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 500,
        data: Optional[Any] = None,
    ) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        self.data = data
        super().__init__(message)

    def __str__(self) -> str:
        return self.message