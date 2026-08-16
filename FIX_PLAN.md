# EngramNote 修复计划

> 基于 2026-XX 静态审阅拟定。原则：先修数据安全/部署不可用/状态卡死，再修统计与前端一致性，最后做收敛重构。

## Phase 1（本次必须完成）

### 部署与迁移
- [x] Docker Compose 增加 Celery worker / beat，Nginx 关闭 SSE 缓冲
- [x] 新增 Alembic 006：移除 projects.slug / notes.project_id，创建 note_projects
- [x] `_migrate_sqlite` 兼容旧 projects.slug 表结构，创建唯一版本号索引

### 数据安全
- [x] `folder_id` 上传时校验归属；文件夹详情/计数/删除全部按 user_id 过滤笔记
- [x] 学习目标 scope_notes/scope_folders 校验归属；进度/每日计划查询补 user_id 过滤
- [x] Markdown 渲染禁用未过滤的原始 HTML（防 stored XSS）
- [x] 登录 401 不再被误判为 token 过期

> 实现说明：学习目标逻辑已整体迁移到 `backend/app/services/goal_service_v2.py`，
> `api/goals.py` 与 `tasks/reminder_tasks.py` 已切换到 V2。旧 `goal_service.py`
> 不再被运行时引用，后续可删除。


### 流程状态与破坏性操作
- [x] 重新理解 archived 笔记必须 `confirm=true`；前端显示影响数量并二次确认
- [x] cleaning/understanding 触发 Celery 失败时回滚状态并返回 503
- [x] `generate_questions` Celery 提交失败返回 503
- [x] Upload 轮询补充 cleaning_failed / learning_failed 终态

### 复习 / 目标 / 统计
- [x] 后端每日限额与前端统一为 10
- [x] 普通复习提交校验题目是否到期；快速复习保留免校验
- [x] 目标列表返回真实进度（progress_cache/实时计算）
- [x] 新增每日计划完成接口，使 completed_count 可更新

### 正确性与性能
- [x] 评估测验缓存按 material + personal_note 共同签名并过滤归属
- [x] RAG Celery task.get 改为线程池等待；复用主数据库会话，避免 engine 泄漏
- [x] 联合分析重跑级联删除题目/复习记录/图谱关系
- [x] 启动清理重复关系时保留不同方向/不同类型的关系
- [x] 版本号增加唯一约束，防并发重号

## Phase 2（后续迭代）
- 时区统一为配置化本地日界（默认 Asia/Shanghai）
- 三套答题 UI 抽 `QuizPlayer`
- 上传三入口抽 `ingestion_service`
- 全局向量索引 + 持久化 BM25
- Toast / ConfirmDialog / ErrorBoundary / 请求超时
- 软删除 + 回收站
- JWT 启动强校验、登录限流、CORS 环境变量化
