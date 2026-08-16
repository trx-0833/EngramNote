# 📋 EngramNote 修复方案（ADHD 友好版）

> 这是 `fix_improvement_plan.md` 的同款内容，改成了**短句 + 清单 + 勾选框**格式。
> 每条修复 = 1 行「改了什么」+ 几个关键点。想查细节再看原版。

---

## ✅ 总状态（先看这个）

| 阶段 | 状态 |
|------|------|
| P0 数据安全 | ✅ 全修完 |
| P1 契约与功能 | ✅ 全修完 |
| P2 前端体验 | ✅ 全修完 |
| P3 去重与卫生 | ✅ 全修完 |
| 测试 | ✅ 41 通过 / 0 新增失败 |
| 前端类型检查 | ✅ 通过 |

---

## 🔴 P0：数据安全与核心失效（最先修完）

### F-01 启动时偷偷删数据 → 改为安全去重
- 旧：每次启动把「方向相反/不同类型」的关系当重复删掉
- 新：只删完全相同的重复行，加唯一索引 `uq_card_relations_pair_type` 防再犯
- 新增迁移：`007_safe_card_relation_dedup.py`
- ✅ 已验证：反向/异型关系全部保留

### F-02 归档笔记"重新学习"一键清空 → 加确认
- 新：先返回「将删除 X 卡片 / Y 题 / Z 记录」→ 用户确认才删
- 前端弹确认框显示数量
- ✅ 附带：学习中禁止重复触发

### F-03 定时任务名写错（00:30 永不执行）→ 改一个名字
- `refresh_goal_progress_task` → `refresh_goal_progress`（1 行改动）

### F-04 笔记内容能注入脚本（XSS）→ 出口消毒
- 关键：NoteDetail 两处直调 `marked.parse` 绕过了消毒 → 改走 `renderMarkdown`
- 装 `dompurify`（不带 @types，v3 自带）
- 允许 style + math 标签（KaTeX 公式必需，防公式渲染劣化）
- ✅ 样张验证：`<img onerror>` 被剥离、`$$x^2$$` 公式正常

### F-05 LLM 每次新建连接 → 共享连接池
- httpx 客户端变模块级单例
- 限流器/信号量变类级（之前多实例各持一份 = 限流失效）
- 关闭钩子挂在应用 shutdown

### F-06 RAG 问答卡死服务器 → 阻塞移出事件循环
- `task.get()` 改 `asyncio.to_thread`
- 私有数据库引擎 → 复用主会话工厂

### F-07 数据库外键没开（删了父留孤儿）→ 开启 + 补删
- 每个连接执行 `PRAGMA foreign_keys=ON` + `busy_timeout=5000`
- `delete_note` 显式补删 `note_projects`
- ⚠️ 开启前先备份（已备份）再跑孤儿清理

### F-08 文件夹归属不校验（塞进别人的文件夹）→ 加校验
- 上传时校验 folder 属于当前用户，否则 400
- 文件夹详情/计数/删除全部按 user_id 过滤

### F-09 学习目标 scope 越权（IDOR）→ 移植校验
- 从 v2 移植 `_validate_goal_scopes` 到 v1
- 进度/每日计划查询全部补 user_id

### F-21a JWT 密钥默认值可预测 → 强制配置
- 默认改空；生产模式（debug=false）为空直接拒绝启动
- `.env.example` 改占位说明

---

## 🟡 P1：契约与功能

### F-10 Celery Beat 没启动（定时任务全废）→ 补齐
- start.bat / start.sh 加 beat 启动（带 pidfile 防双启动）
- docker-compose 加 `celery-worker` / `celery-beat` 服务
- ⚠️ 已实测脚本语法，容器环境待实测

### F-11 清洗"删除重复块"按钮没反应 → 修 key 不匹配
- 注释标记改用重复块自身 index（旧代码写的是保留块 index，永远匹配不上）
- metadata 记录行区间；旧数据回退兼容
- ✅ 已验证：restore/delete 真正生效

### F-12 每日限额对不上（后端 10 / 前端 50）→ 单一来源
- 配置 `daily_review_limit=10`；`/stats` 返回 `daily_limit`
- 前端 4 处硬编码 50 全部改动态读取
- goal 的 50 改名 `DAILY_PLAN_LIMIT`（语义区分）

### F-13 目标列表进度恒为 0 → 读缓存
- 列表接口回退 `progress_cache`

### F-14 复习不校验到期 + 可重复提交 → 加校验 + 幂等
- 普通复习：未到期拒绝；快速复习：免校验
- 同日同题重复提交：返回已有结果，SM-2 不叠加

### F-15 上传轮询缺失败终态 → 补全
- 补 `cleaning_failed` / `learning_failed`（旧代码会空转 10 分钟）
- setInterval 改 setTimeout 递归（防请求重叠）
- 卸载时清理定时器

### F-16 同名不同扩展名互相覆盖 → 按主干查重
- `a.pdf` 传完再传 `a.md` → 后者自动加随机后缀

### F-17 图谱有向关系被当重复 → 区分方向
- prerequisite/subsequent 保方向；related/contrast 排序去重

### F-18 删笔记不清理向量库 → 补删
- `delete_note` 调 `delete_note_chunks`（失败仅告警）

### F-19 HF 离线环境变量误删 → 保存/恢复原值

### F-20 LLM 重试吞 4xx + 日志泄全文 → 收紧
- 只重试 429/5xx/超时/断连；4xx 直接抛
- 日志截断 200 字符，不记响应体

### F-21b 邮箱大小写撞库 → 归一化
- 注册/登录都 strip + lowercase

---

## 🟢 P2：前端体验与后端正确性

### F-22 前端请求无超时/401 误登出 → 重构 request()
- 加 30 秒超时（AbortController）
- 401 只豁免 `/auth/login`、`/auth/register`（别误豁免 /auth/me）
- SSE 流式不套超时
- 4 处重复的上传代码合并为 `uploadRequest()`

### F-23 双击提交重复扣次数 → 加锁
- 四页（Review/QuickReview/TodayLearn/LearningAssessment）加 `submittingRef`

### F-24 仪表盘 6 个接口静默吞错 → 区分关键/非关键
- 笔记加载失败 → 突出错误条；统计失败 → 轻提示

### F-25 复习提醒只弹一次 → 按值去重
- 移除 `notified.length===0` 死判断；顺带清理死代码

### F-26 归档状态机 + 分页
- converted 归档后取消 → 回 converted（不再谎称 cleaned）
- 题库/卡片列表改"加载更多"（原来一次拉 999 条）

### F-27 4 份数据库连接代码 → 合并成 `tasks/common.py`
- 会话工厂 + 状态更新 + 白名单 + metadata 合并，一处维护

### F-28 卡片颜色三套打架 → 统一一套
- 统一用 `labels.ts`（图谱/卡片页/答题页全部改 import）
- ⚠️ 图谱颜色会变（刻意统一）

### F-29 连点触发多个任务 → 加状态拒绝
- 理解中/清洗中 → 409；重新出题先清旧题

### F-30 清洗误删代码块里的数字 → 逐行保护
- 跟踪 ``` 代码块与 $$ 公式状态，内部行不套页码/水印规则
- ✅ 已验证：代码块/公式内数字保留，正文页码正常删

### F-31 版本号并发重号 → 唯一约束 + 重试
- 新增迁移 `008_note_version_unique.py`
- 恢复版本先验证目标可读再建快照（不再留空快照垃圾）

### F-32 搜索通配符 + 时区
- 搜索 `%`/`_` 转义（搜"100%"不再匹配一切）
- 日界统一北京时间（新建 `utils/timeutil.py`，替换 8 处 UTC 零点）

---

## ⚪ P3：去重与卫生

### F-33 三页答题 UI 重复 480 行 → 抽共享组件
- 新建 `QuizAnswerCard.tsx`，三页改造完成
- 行为差异参数化：提交 API / SM-2 展示 / autoFocus / 完成文案
- ⚠️ 继承 F-23 的竞态锁（已带上）

### F-34 死代码清理
- `backend/control/`（celery 残留，git 跟踪）→ 移除
- `_q.py`、`goal_service_v2.py`（从未被引用）→ 删除
- `.gitignore` 补 pytest 残留目录

### F-35 测试体系
- requirements 补 `pytest` / `pytest-asyncio`
- 新建 `tests/conftest.py`（独立临时库，不碰真实数据）
- 新建 `tests/test_fixes.py`（6 项回归，全过）
- 顺手修了 SM-2 存量缺陷（填空部分匹配 2/4 被阈值挡在门外）

---

## ⚠️ 风险与回滚（短版）

| 事项 | 说明 |
|------|------|
| 数据库备份 | `engramnote.db.bak-20260815-200246`（F-07 前已备份） |
| 回滚方式 | 改动未提交 → `git checkout -- <文件>` 即回修复前 |
| 视觉变化 | F-28 颜色统一是刻意的（图谱/答题页色值会变） |
| 分阶段提交 | 建议 `fix/P0-*`、`fix/P1-*` 独立提交，好回滚 |

---

## 🔜 剩 3 件事（都不是代码问题）

- [ ] `pip install pytest-asyncio`（网络恢复后，4 个老测试就能过）
- [ ] 换 JWT 密钥（`backend/.env` 还是旧默认值）
- [ ] Docker 容器实测（worker/beat 服务）

---

> 细节控？看原版 `fix_improvement_plan.md`（206 行完整版）。
> 只想看结果？看 `修复总结_ADHD友好版.md`（30 秒版）。
