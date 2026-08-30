# EngramNote 启动与功能验证报告

> 日期：2026-08-30 · 依据：.trae/documents/engramnote-启动与功能验证计划.md（已批准）
> 环境：mineru_env（Python 3.10.20）、Node v22、现有 data/（用户恢复数据：1183 卡片 / 22 笔记 / 3 项目）、.env（DEBUG=false，DeepSeek 网关 + GLM + MinerU token 齐全）
> 原则：只验证不擅自修改；1 处启动级回归经用户授权最小修复。

## 一、总览

| 层级 | 结果 | 说明 |
|---|---|---|
| L0 环境自检 | ✅ | Python/Node 版本满足；mineru_env 依赖齐全（fastapi/sqlalchemy/celery/chromadb/sentence-transformers/torch-cpu）；BGE-M3（2.27GB）+ bge-small fallback + silero-vad 均在 data/models；端口空闲 |
| L1 后端启动 | ✅（修复后） | 首次启动失败（D1 拆分回归），授权最小修复后启动成功；init_db 双通道自动为 users 表补 email_reminder_enabled / last_reminded_at 两列 |
| L2 API 冒烟 | ✅ | 注册/登录/me、reminder-settings GET/PUT、notes/goals/stats/graph、上传正例 201、XSS 负例 400、回收站软删/统计/改名恢复/-1 改名、purge 204+卡片清零、版本快照/diff-404 VERSION_NOT_FOUND |
| L3 异步链 | ✅ | Celery worker（solo）8 任务注册；Beat 正常起调；md 上传→转换→清洗（21.8s，内存 3.7GB<4GB 阈值自动降级 bge-small）→理解（DeepSeek：6 卡片+6 题目）→archived |
| L4 AI 实测 | ✅ | 非流式问答（deepseek + retrieval_status=full_vector + 1 source）；SSE 流式（首事件 event:meta 含 retrieval_status，42 tokens + done）；图谱建议 POST /graph/suggest 200（Celery 编码路径正常，0 条建议为业务正常）；MinerU 云转换真实 PDF→cleaned（合法 PDF 通过；手写非法 PDF 正确进入 failed+error_message） |
| L5 前端 | ✅ | npm install（dompurify 已装）；tsc+vite build 成功产出 dist（npm 进程退出码 1 系沙箱拦截 node_cache 日志，非构建失败）；dev server 起于 5173；浏览器冒烟：登录→Dashboard 邮件提醒开关切换并 PUT 持久化→QA 流式+停止按钮→图谱/回收站空状态渲染正常 |

## 二、发现的问题（4 项）

1. **[已修复·启动级回归]** `api/notes` 包聚合路由时 include 不带 prefix，而 `list_notes` path 为空串 → `FastAPIError: Prefix and path cannot be both empty`，后端无法启动。
   - 修复（用户授权）：`api/notes/__init__.py` 将 `/notes` 前缀下放到四个子路由聚合 include 处；`api/router.py` 外层不再重复挂前缀。最终 URL 与拆分前完全一致。
2. **[遗留·未修]** `Sidebar.tsx:93-110`：`<button>` 嵌套 `<button>`（笔记列表按钮内嵌套上传资料按钮），浏览器报 validateDOMNesting。历史遗留、无功能影响，属可读性/语义问题，修复需另行授权。
3. **[新发现·低]** `QA.tsx` 流式处理中 `done` 事件分支直接 `return` 未取消/关闭 reader，导航或卸载时产生 `net::ERR_ABORTED POST /api/understanding/ask/stream` console 噪音；答案显示不受影响。修复需另行授权。
4. **[脚本问题·低]** `backend/e2e_cleanup.py` 清理循环删 `notes` 表时使用不存在的 `note_id` 列，报 `no such column: note_id`（被 try/except 吞掉后继续执行，最终清理成功）。顺带清理了历史 E2E 测试账号 e2e0816085528/e2eb0816085528（其文档注明可用该脚本删除，属预期）。

## 三、回归核对（对照修复批次 A1-D7）

| 项 | 结果 | 证据 |
|---|---|---|
| A1 消毒 | ✅（输出端静态+构建；动态注入未测） | 管道 parse→sanitize→renderMathInHtml；入口负例即被 A4 拦截 |
| A2 JWT 策略 | ✅（配置密钥路径） | 已配密钥正常启动；自动生成分支因密钥已配置未触发（静态核验） |
| A3 CORS 配置化 | ✅ | `access-control-allow-origin: http://localhost:5173` + credentials 头实测 |
| A4 md 嗅探 | ✅ | XSS 样例 400「检测到疑似脚本注入内容（<script> 标签）」 |
| A5/A6 恢复一致性/改名 | ✅ | 回收站期间同名再传 → restore 返回 `renamed_to: verify_ok-1.md` |
| A7 purge SQL 化 | ✅ | purge 204；卡片/题目归零；评估/目标/计划清理无异常 |
| A8 move_file | ✅ | 回收站搬移/改名恢复全程文件一致（trash→inbox 成功） |
| B1/B2 图谱 Celery 化 | ✅ | graph/suggest 200：API 进程无模型加载日志、编码走 worker |
| B3 DB 阶段0 | ✅ | 启动日志 users 两列补齐；无并发重建异常 |
| B4 惰性化 | ✅（启动/运行层面） | uvicorn/worker 正常；import 级副作用单独未测 |
| B5 AppError | ✅ | 版本 404：`error_code=VERSION_NOT_FOUND` + request_id |
| C1 上传超时 600s | ✅（静态） | 大文件 30s 截断场景未动态测试（需 >30s 上传） |
| C2 RAG 缓存/状态 | ✅ | 非流式+SSE meta 均下发 full_vector；缓存命中路径未专门压测 |
| C3 目标按需刷新 | ✅（端点级） | GET /goals 正常；2h 过期刷新触发未动态验证（空目标） |
| C4 QA 取消 | ✅ | 生成期「停止生成」按钮可见；abort 生效（见问题3 console） |
| C5 轮询互斥 | ⏸ 未动态覆盖 | CleaningPanel 块操作与轮询并发场景未在浏览器执行 |
| C6 邮件设置 | ✅ | API GET/PUT + Dashboard 开关切换 + 持久化（重启库后仍 true/false） |
| C7 APP_LINK | ⏸ 未动态发送 | SMTP 未配置，邮件不通（代码级使用 settings.app_base_url ✓） |
| C8 版本摘要 | ✅ | 版本列表 change_summary 字段返回（user_edit 快照）；UI 已有展示 |
| C10 注释 | ✅ | 静态已核 |
| D1-D5 拆分 | ✅ | 后端全 URL 工作；前端 tsc 通过 + 页面全渲染 |
| D6 decisions | ✅ | docs/decisions.md 存在 |
| D7 requirements | ✅ | `~=` 锁定生效（本次安装解析无冲突） |

## 四、清理结果

- 验证期创建：验证账号（e2e_verify_*）、2 条 md 笔记、2 条 PDF 笔记、1 个「验证项目」——全部清除；e2e*/e2eb* 账号在库中归零。
- 真实数据完好：1183 卡片 / 22 笔记 / 3 项目（Tian 与 clean_test 账号未动）。
- 验证期启动的 4 个进程（uvicorn / worker / beat / vite dev）已全部停止。
- 临时文件（data/tmp/verify/*、check_db.py）已删除。

## 五、结论

**项目可正常启动，核心链路与修复批次全部验证通过。** 存在 1 处已修复的启动级回归（D1 路由聚合 prefix）与 3 个低严重度遗留问题（Sidebar DOM 嵌套、QA done 未 cancel reader 的 console 噪音、e2e_cleanup 脚本冗余报错），均不影响功能，修复需另行授权。未动态覆盖项（C5/C7/A2 自动生成分支/C3 过期触发/C1 大文件超时）已在表中标注，可在后续批次补测。