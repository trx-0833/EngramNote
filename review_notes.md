# 审核意见收集（进行中）

> 3 路审核（后端/前端/部署测试）意见汇总。待全部完成后统一修订 fix_improvement_plan.md。

## 部署与测试审核（已完成）

### 修正意见
1. **F-10 需修改**：
   - start.sh 的 cleanup() 必须扩展 kill/wait 列表包含 BEAT_PID；
   - beat 加 `--pidfile` 防双启动双触发（重复邮件/重复刷新）；
   - docker-compose 的 worker/beat 用 `depends_on: {backend: {condition: service_healthy}}`；
   - worker/beat 显式挂载 `./backend/.env`（或 env_file）与共享 volume、`restart: unless-stopped`；
   - 注明随 F-07 的 busy_timeout 同批合入（三进程并发写 SQLite）。
2. **F-34 修正（经主代理复核）**：`backend/control/` 3 个文件**确被 git 跟踪**（git ls-files 实证），`git rm -r --cached backend/control/` + 磁盘删除正确；`.trae/`、`参赛/`、`_q.py` 未跟踪，直接磁盘清理即可（.gitignore 已有规则）。游离测试脚本不强行 pytest 化，保留冒烟脚本，其余删除。
3. **F-35 需修改（基础设施缺口）**：
   - `backend/tests/` 现有 20 个文件多为脚本式（模块级 httpx 调 localhost:8000、硬编码 token），非 pytest 用例；pytest 收集即失败（test_rag.py KeyError access_token）；
   - 真实环境 mineru_env 有 pytest 9.0.3，但 requirements.txt 未声明 pytest/pytest-asyncio；
   - 无 conftest.py、无测试库隔离（现有测试直连 data/db/engramnote.db）；
   - **基线测试现状（已实测）**：
     - test_week8_sm2.py：11 passed / **1 failed**（test_quality_fill_blank：填空题部分匹配 q2>=3 实际返回 1——存量失败，需单独评估是否为真实 bug）；
     - test_conversation_session / test_batch_format：通过；
     - test_batch_raw / test_week7_features 等：13 failed（多数因 async 用例无 pytest-asyncio 插件、或依赖活服务器）；
     - test_rag.py 等 e2e：collection 即失败（依赖 localhost:8000）。
   - 修订：新增 conftest.py（aiosqlite 内存库 fixture + 测试用户/Token 工厂），新修复测试写成真 pytest 单测挂 fixture；requirements.txt 增补 pytest/pytest-asyncio（dev 分组或注释说明）。
4. **F-21 需修改**：JWT 默认值改空 + model_validator 后，check_env.py 与 Docker 启动依赖 .env.example 的示例值——需同步处理 check_env 行为，避免存量升级无法启动。
5. **F-03/F-07/F-01 可行**：F-03 方向正确（beat 引用函数名 → 注册名）；F-07 PRAGMA 逐连接设置正确；F-01 与 F-07 顺序安全。
6. **Alembic 006 未提交**：`006_project_tags_refactor.py` 是 untracked，新增 007/008 前应先提交 006，保证迁移序列完整。

## 主代理复核补充（待后端/前端审核完成后并入）

7. **F-34 复核修正**：`git ls-files` 实证 `backend/control/` 3 个文件**已被跟踪**（部署审核代理误判为未跟踪），`git rm -r --cached backend/control/` 方案正确保留。
8. **基线测试 SM-2 缺陷（新发现，属存量 bug）**：`test_week8_sm2.py::test_quality_fill_blank` 失败——`quality_from_answer("fill_blank", "机器", "机器学习")` 返回 1 而非 >=3。根因：`sm2_service.py:186` 字符集重叠阈值 `overlap/total > 0.5` 是严格大于，2/4=0.5 被挡；且前缀包含场景（长度 <3）未进部分匹配分支。修复：`> 0.5` 改为 `>= 0.5`（q3"深度学习"vs"机器学习"=0.5 会得 3 分，测试断言 <=3 仍通过）。归入 F-35 附带修复。
9. **pytest 残留目录**：工作区根出现 `pytest-cache-files-*` 临时目录（沙箱权限删除失败），无害，后续清理或加入 .gitignore。

## 前端审核（已完成）——需修订项

10. **F-04（阻断）**：
    - NoteDetail.tsx:554/557 **直接 `marked.parse`**（htmlContent / editPreviewHtml），完全绕过 `renderMarkdown`——仅改 markdown.ts 等于没修。必须把这两处改为 `renderMarkdown`（删除对 marked 的导入）；实际调用点是 **9 处**（LearningAssessment 7 + NoteDetail 2），非 8 处。
    - DOMPurify 默认 allowlist 不含 `<math>/<semantics>/<annotation>`（MathML），KaTeX 兜底可能被剥离；hljs `hljs-*` span、katex `katex-*` span（含 style）需在消毒后保留。验证须加"含 $$...$$ 公式、多语言代码块的样张渲染后公式/高亮不劣化"。
    - `@types/dompurify` 已弃用（DOMPurify v3 自带类型），只装 `dompurify`。
11. **F-22（阻断）**：
    - 401 豁免 `startsWith('/auth/')` 过宽：会误豁免 `/auth/me`（getMe 401 本应登出）。改为只豁免 `path === '/auth/login' || path === '/auth/register'`。
    - 显式声明 `askQuestionStream`（SSE）**不得**套 request() 的 30s 超时/AbortController。
    - FormData 内联分支实测 **4 处**（uploadFile:566/prepareUpload:618/commitUpload:667/uploadFileToFolder:1829），非 5 处。
12. **F-12（高）**：前端 `ReviewStats` TS 接口（client.ts:1048-1057）须加 `daily_limit: number`；硬编码 50 还有 Review.tsx:178/234、TodayLearn.tsx:342/346 共 4 处需一并替换；`getDueQuizzes(50)` 是拉取上限语义，不改。
13. **F-15**：DailyMaterials 轮询成功态含 `learning` 而其 getStatusCategory 又把 learning 归 processing（自身矛盾），统一常量时须明确 learning 归属；失败原因文案保留。
14. **F-28（中）**：先敲定唯一基准色值（卡面三套 concept/formula/qa/definition、难度三套），合并会导致图谱/答题页视觉整体变化（属刻意变更，需声明）；KnowledgeGraph 还有 CARD_TYPE_SHAPES/RELATION_TYPE_COLORS/LABELS 本地表，收敛范围需写明（本次只收颜色/文案）。
15. **F-33（中）**：行为差异清单必须明确：①提交 API（submitAnswer vs submitQuickReviewAnswer 免配额）；②每日限额/完成页差异（Review 有、QuickReview 无、TodayLearn 入口显示）；③难度色来源；④每题后是否刷新 stats；⑤"再来一次"重拉（QuickReview）；⑥fill_blank autofocus/回车细节。抽取必须**继承 F-23 的 submittingRef 锁**。
16. **F-24**：区分关键失败（notes）与非关键失败（stats），不能任一失败都弹红色大错误条。
17. **F-26**：getKnowledgeCards 后端 cap=9999、getNotes cap=1000 均接受 999，属纯前端内存优化；Projects 候选面板与 NoteDetail 相关卡片需保留过滤/分页语义。
18. **F-23/F-25/F-02**：可行。F-25 移除 notified.length 判断后，`getNotifiedQuizIds/markNotified`（notifications.ts:70-91）失去读者，一并清理。
