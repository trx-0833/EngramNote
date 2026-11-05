# 设置功能：设计与设置项调研

> **状态：活文档（P0/P1/P2 主体已落地，密码/删号/深色主题未做）** ｜ 最后核对：2026-11-05 ｜ 权威性：设置主题的现状与方案依据
>
> 2026-11-05 更新：六组设置页、留存率/最长间隔、AI 输出语言、卡片上限、清洗规则、
> LLM 用量与桌面通知权限已落地；密码/删号/深色主题仍不在本批范围。
> 实现与联动验收矩阵见 §9/§10。

> 本文回答两件事：①设置页长什么样、怎么存怎么读；②**具体该放哪些设置**，以及为什么另一些不该放。
> 全文的现状判断以代码实测为准，逐条带 `文件:行号`。

---

## 0. 结论（先看这一页）

1. **现在没有设置页**。`Sidebar.tsx:217` 的注释自己写着「全仓没有 Setting/Profile 页」。用户今天能改的东西只有三类：
   界面语言、邮件提醒开关、浏览器通知权限 —— 分别住在侧边栏底部、仪表盘卡片、浏览器设置里。
2. **其余 60+ 个可调参数全在 `.env`**（`backend/app/config.py`）。改一个要重启后端与 worker，普通用户既不知道、也碰不到。
3. **已做 `/settings` 页（只设计 PC）**：六组已全部落地：学习节奏 / 提醒 / 内容与生成 / 外观与语言 / 账号与安全 / 数据与关于。
   按阶段点亮：**没接线的组不渲染**，不做灰置占位。
4. **P0 只「搬家」，不新增行为**：语言、邮件开关、桌面通知、退出所有设备、关于信息。后端只加一个聚合读写门面，不建表。
5. **P1 才动行为**：每日复习上限、邮件发送时间、免打扰时段、字号、减少动效、侧栏折叠记忆、存储用量、导出入口。
6. **P2 中已落地**：目标留存率（改排期）、最长间隔、AI 输出语言（worker 读库）、清洗规则、卡片上限、LLM 用量。**仍未做**：深色主题、修改密码、删除账号（单独决策）。
7. **日界/时区不进 v1**。业务日界硬编码在 `timeutil.py:14-23`（Asia/Shanghai 00:00），而 `review_due_hour=4`
   只影响到期时刻（`scheduler_service.py:242`），两者不是一个口径。只放一个「一天从几点开始」会把口径差异变成用户可见的 bug。
8. **先修死旋钮，再上新旋钮**。`email_reminder_hour`、`reminder_quiet_hours_start/end` 在 `config.py:548-554`
   有定义但**全仓没有调用方**（grep 只有定义处三行）；Beat 把发信时间写死在 9:00（`celery_app.py:131`）。
   前端另有一份写死的静默时段 22–8（`utils/notifications.ts:54`）。把死配置直接搬上界面 = 给用户一个假开关。
9. **页面不是目的**：顺手把仪表盘的两条提醒横幅收成一条入口 —— 走查在移动端最明显
   （`ux-walkthrough-report-20261021.md:217`，结论表 `:253`），PC 上同样占掉首屏。邮件开关搬进设置页后，仪表盘只留一行链接。
10. **不做**：API key / 模型切换 / 嵌入模型 / 分块与 RAG 权重 / 上传限制 / SMTP / CORS / 日志级别。这些是**部署的事实**，不是用户的偏好（理由见 §4.4）。

---

## 1. 现状盘点

### 1.1 已经有「用户级」语义的六处（最后一类不算设置）

| 能力 | 现在住在哪 | 证据 | 问题 |
|---|---|---|---|
| 界面语言 | `localStorage['engramnote_locale']` + `users.locale` + `PUT /auth/locale` | `i18n/locales.ts:108,126`；`models/user.py:73`；`api/auth.py:369` | 入口在侧栏底部；语言**同时**决定 AI 输出语言（`tasks/understand_tasks.py:94-102`） |
| 邮件提醒 | `users.email_reminder_enabled` + `GET/PUT /auth/reminder-settings` | `models/user.py:56`；`api/auth.py:316,331`；`pages/Dashboard.tsx:244,783` | 开关嵌在仪表盘；全局总闸 `settings.email_reminder_enabled` 关掉后用户开关静默失效（`tasks/reminder_tasks.py:44`） |
| 桌面通知 | 浏览器 `Notification.permission`；前端每 10 分钟轮询 | `utils/notifications.ts:12`；`components/ReminderBanner.tsx:59-66` | 不是应用设置、不能跨设备；静默时段写死 22–8（`notifications.ts:54`） |
| ADHD Reader | 页面组件内 `useState(false)` | `hooks/useAdhdReader.ts:157` | 刷新即丢 |
| 侧栏折叠 | `useState(false)` | `App.tsx:177` | 刷新即丢 |
| 滚动位置 / 答题会话 | localStorage 自动记忆 | `utils/scrollMemory.ts`、`utils/quizSessionMemory.ts` | 自动行为，不算设置（不要放进页面） |

### 1.2 只在 env 里的参数（按主题分组）

| 主题 | 字段（`config.py` 行号） | 是否适合进设置页 |
|---|---|---|
| 学习调度 | `daily_review_limit:484`、`review_scheduler:492`、`fsrs_request_retention:500`、`fsrs_max_interval_days:507`、`review_fuzz_ratio:518`、`review_due_hour:533` | 部分：每日上限、留存率、最长间隔 |
| 提醒 | `reminder_quiet_hours_start/end:548,550`、`email_reminder_enabled:552`、`email_reminder_hour:554` | 邮件总闸保持部署级；时间/免打扰要做成用户级（且先接线） |
| 内容与生成 | `clean_*:279-287`、`chunk_size:289`、`card_min_content_chars:451`、`card_min_title_chars:453`、`card_require_source_text:456`、`card_max_new_per_run:459`、`similarity_threshold:252`、`vector_similarity_floor:262`、RAG 权重 `320-325` | 少数：清洗三条、单次生成上限 |
| 配额与上传 | `max_upload_size_mb:202`、`max_storage_per_user_mb:204`、`allowed_extensions:206`、`max_pdf_pages:225`、`max_notes_per_user:237` | 只读展示用量，不放开关 |
| 部署 | `database_url:104`、存储 `108-124`、Celery `128-132`、JWT `138-155`、LLM key `159-171`、MinerU `175-198`、嵌入模型 `242-250`、SMTP `390-400`、CORS `567`、日志 `557-560`、备份 `538-542` | 不展示（只读版本/通道状态） |
| LLM 成本 | 单价 `415-417`、日配额 `438-439`、缓存 `478-480` | 只读用量（`GET /llm/usage` 已有） |

### 1.3 三处「死配置」与一处注释/代码不一致

| 项 | 证据 | 后果 |
|---|---|---|
| `email_reminder_hour` | `config.py:554`；grep 只有定义处 | 改成 7 点也不生效，Beat 固定 `crontab(hour=9, minute=0)`（`celery_app.py:131`） |
| `reminder_quiet_hours_start/end` | `config.py:548,550`；grep 只有定义处 | 后端从不读它；前端另写死 22–8（`notifications.ts:54`, `ReminderBanner.tsx:85`） |
| `chunk_overlap` | `config.py:292-297` 自述「当前不生效」 | 分块器已换成 `markdown_segmenter`，没有字符级重叠 |
| `review_due_hour` 的注释 vs 代码 | 注释说它改变「今天」的判断边界（`config.py:520-533`），实际只用于到期时刻对齐（`scheduler_service.py:242`）；额度/报告的「今天」走 `timeutil.py:26-45` 的 00:00 | **以代码为准**：两个口径。设置页不要把它们混成一个旋钮 |

### 1.4 五条硬约束

1. **SQLite 单写者**（`docs/sqlite-single-writer.md`）：设置写入要短；`GET` 不能每次 upsert。
2. **worker 没有请求上下文**：影响 worker 的设置必须落库，再用 `user_id` 反查 —— 语言字段就是先例（`models/user.py:59-74`）。
3. **契约是生成出来的**：`backend/openapi.json` → `frontend/src/api/generated/schema.ts`；新增端点必须重跑
   `backend/scripts/dump_openapi.py` 与 `npm run gen:api`，否则 `--check` 与漂移检查会红
   （命令见 `frontend/docs/openapi-client.md:100-111`）。
4. **i18n 两侧键必须一一对应**：`i18n/i18n.test.tsx:41-47` 会把两份 messages 拍平对比。设置页文案量大，必须一次补齐中英。
5. **页面宽度**：`/settings` 走默认的 `standard` 档（1200px，`App.tsx:100-145` 的兜底），不登记 `reading`。
   页内自己收成「左栏 208 + 右栏 812」的两栏，靠卡片内侧留白控制行长。

---

## 2. 设计

### 2.1 五条原则

1. **只放用户的选择，不放部署的事实。** 判断句：换一台服务器部署，这个值该不该跟着变？该 → env；不该 → 用户设置。
2. **每个旋钮必须有可验证的生效点。** 没有调用方的字段不上界面（§1.3）。
3. **影响调度的设置必须写清代价。** 例如目标留存率：调高 → 复习更频繁。Anki 的文档也是这么写的（见 §4.2）。
4. **服务端为准，设备本地例外。** 跨设备要一致（语言、每日上限、邮件）→ 落库；只对这台机器有意义（字号、动效、免打扰、侧栏）→ localStorage。
5. **env 仍是默认值，用户设置是覆盖层。** 不建第二套配置系统；读取顺序固定为 `用户覆盖 → env 默认 → 代码默认`。

### 2.2 信息架构（六组 22 项，均已接线）

> 下表保留完整规划。未接线的行不渲染（§1.3 第 5 条）；本批已把内容组与
> 学习高级项接线，六组全部渲染。仍未做的是密码/删号/深色主题。

| 分组 | 设置项 | 控件 | 默认 | 存储 | 阶段 |
|---|---|---|---|---|---|
| **学习节奏** | 每日复习上限 | 数字 5–100 | 10（`config.py:484`） | 服务端 | P1 |
| | 目标留存率（高级，折叠） | 档位 80/85/90/95% | 90%（`config.py:500`） | 服务端 | 已落地 |
| | 最长间隔（高级，折叠） | 档位 1/5/10 年 | 3650 天（`config.py:507`） | 服务端 | 已落地 |
| **提醒** | 邮件提醒 | 开关 | 用户现值 | 服务端（列已存在） | P0 |
| | 邮件发送时间 | 0–23 时（北京时间） | 9（`config.py:554`） | 服务端 | P1 |
| | 免打扰时段 | 起/止两个下拉 | 22–8 | **本地** | P1 |
| | 桌面通知 | 本地开关 + 权限状态 + 请求按钮 + 测试通知 | 开（有权限时） | **本地（浏览器权限）** | P0 |
| **内容与生成** | AI 输出语言 | 跟随界面 / 中文 / English | 跟随界面 | 服务端（新字段） | 已落地 |
| | 清洗规则（三条） | 开关 | 见 `config.py:279-287` | 服务端（worker） | 已落地 |
| | 单次生成卡片上限 | 数字 50–1000 | 500（`config.py:459`） | 服务端 | 已落地 |
| **外观与语言** | 界面语言 | 中文 / English | 跟随浏览器/本地 | 本地 + 服务端回填 | P0 |
| | 字号 | 小 / 标准 / 大 | 标准 | 本地 | P1 |
| | 减少动效 | 开关 | 跟随系统 | 本地 | P1 |
| | ADHD Reader 默认开启 | 开关 | 关（`useAdhdReader.ts:157`） | 本地 | P1 |
| | 侧栏折叠记忆 | 开关（记住当前状态） | 展开（`App.tsx:177`） | 本地 | P1 |
| **账号与安全** | 退出所有设备 | 按钮 + 二次确认 | — | 服务端（已有） | P0 |
| | 邮箱 / 用户名 | 只读 | — | — | P1 |
| | 修改密码 | 表单 | — | 服务端（新端点） | P2 |
| **数据与关于** | 导出全部笔记 | 按钮（跳转/直接下载） | — | 已有（`notes.ts:545`） | P1 |
| | 存储用量 | 只读进度 | 配额来自 env | 服务端（新端点） | P1 |
| | LLM 用量 | 只读（今日配额 + 30 天） | — | 已有（`api/llm.py:141`） | 已落地 |
| | 版本 / 环境 | 只读 | `app/version.py` | 服务端 | P0 |

> 「阶段」= 交付批次，不是优先级排序的全部依据：P0 是「已有能力搬家」，P1 是「小改后端/纯前端」，P2 是「跨层或需产品决策」。

### 2.3 页面结构（PC 线框）

> **范围：只设计 PC（≥1180px）。** 2026-11-04 确认产品目前只在 PC 上使用；本页不做移动端适配，
> 窄于 1180px 由窗口横向滚动承受，不新增抽屉、横向锚点条或移动断点。

```
设置
管理学习节奏、提醒与界面偏好。改动即时保存。

┌ 设置分组 ─────┐   ┌ 01 学习节奏 ──────────────────────────────┐
│ 01 学习节奏   │   │ 每日复习上限                   [ 10 ] 道/天 │
│ 02 提醒       │   │ 每天最多结算多少道题……                     │
│ 01 学习       │   │ 每日复习上限                      [ 10 道 ] │
│ 02 提醒       │   └────────────────────────────────────────────┘
│ 03 外观       │   ┌ 02 提醒 ───────────────────────────────────┐
│ 04 账号       │   │ 邮件提醒                             [ 开 ] │
│ 05 数据       │   │ 发送时间                         [ 09:00 ▾ ] │
└───────────────┘   └────────────────────────────────────────────┘
                    ……（03~05 继续纵向排列）
```

页面规则：

- **两栏**：左栏 208px 的分组导航（`position: sticky; top: 32px`），右栏最宽 812px（1120 内容区减去 208 左栏与 36 间距），纵向排列六张 `.card`。
- 左栏点击滚动到对应卡片；滚动时高亮当前组；深链接 `/settings#study` 直接定位。
- 不再使用顶部横向锚点条 —— 那是移动端的交互残留，PC 上六组用左栏一眼可见。
- 高级项默认折叠（`<details>`），默认值不隐藏在折叠区里。
- 危险操作（退出所有设备）用现有 `ConfirmDialog`。
- 右栏每张卡片宽度固定、内容左对齐；不把行拉成 1000px 长的一行字。

每行的 DOM 骨架（`SettingRow` 的契约）：

```tsx
<section aria-labelledby="settings-study">
  <h2 id="settings-study">学习节奏</h2>
  <div className={styles.row}>
    <div className={styles.rowText}>
      <label htmlFor="daily-limit">每日复习上限</label>
      <p id="daily-limit-hint">每天最多结算多少道题。用完后仍可回看已答过的题。</p>
    </div>
    <input
      id="daily-limit"
      type="number"
      min={5}
      max={100}
      aria-describedby="daily-limit-hint"
      value={draft}
      onChange={...}
      onBlur={commit}
    />
    <span role="status" aria-live="polite">{saveState}</span>
  </div>
</section>
```

要点：`label` 必须 `htmlFor`（不靠 placeholder）；提示用 `aria-describedby`；保存态用 `role="status"`，读屏才会播报。

### 2.4 交互规则

| 场景 | 规则 | 理由 |
|---|---|---|
| 开关/下拉 | 立即 `PATCH`，乐观更新；失败回滚 + toast | 与 `Dashboard.tsx:810-818` 已有的「失败必须可见」教训一致 |
| 数字输入 | 失焦或 500ms 防抖后提交；越界就地提示，不发请求 | 避免每敲一个键写一次 SQLite |
| 保存反馈 | 组标题右侧 `aria-live="polite"` 显示「已保存 / 保存失败」 | 不做全局「保存」按钮，减少「改了没生效」的歧义 |
| 恢复默认 | v1 不做；P2 再加（删除覆盖行） | 先验证覆盖模型 |
| 危险操作 | 二次确认，确认文案写清后果 | `ConfirmProvider` 已有 |
| 加载/失败 | 骨架屏；整页失败给「重试」，不静默空表单 | 设置页读到的是默认值时，用户无法分辨「没设过」和「读失败」 |

### 2.5 组件与文件落点

前端（新增）：

| 文件 | 职责 |
|---|---|
| `frontend/src/pages/Settings.tsx` | 页面骨架、左栏分组导航、五个 section |
| `frontend/src/pages/settings/SettingsNav.tsx` | 左栏分组导航（sticky、当前组高亮、点击滚动） |
| `frontend/src/pages/settings/SettingRow.tsx` | 一行设置的通用外壳（label / hint / 控件 / 保存态） |
| `frontend/src/pages/settings/ToggleRow.tsx`、`NumberRow.tsx` | 两类高频控件的薄封装 |
| `frontend/src/pages/Settings.module.css` | 页面私有样式（模块层，不新增全局类） |
| `frontend/src/api/settings.ts` | `getSettings` / `patchSettings` / `getSettingsUsage` |
| `frontend/src/hooks/useDevicePrefs.ts` | 设备本地偏好（localStorage + `storage` 事件 + 根节点属性） |
| `frontend/src/components/icons/settings.tsx` | 齿轮图标（进 `icons/index.ts` 注册表，命名沿用现有小写文件） |

前端（改动）：

| 文件 | 改动 |
|---|---|
| `App.tsx` | 懒加载路由 `/settings`；**不进** `PAGE_WIDTH_ROUTES`（默认 `standard` 1200px） |
| `Sidebar.tsx` | 底部加「设置」入口；**删掉 `:217` 那句「没有设置页」的注释** |
| `ReminderBanner.tsx` | 静默时段与「本地开关」从 `useDevicePrefs()` 读，不再用默认参数；关闭时不弹但保留轮询/横幅 |
| `Dashboard.tsx` | 邮件开关卡片改为一行链接（或删除），文案指向 `/settings#reminders`（**实际未做，见 §9.4**） |
| `i18n/messages.zh-CN.ts`、`messages.en.ts` | 新增 `settings` 命名空间（约 60 键） |

后端（P0 无迁移；P1 起）：

| 文件 | 改动 | 阶段 |
|---|---|---|
| `app/api/settings.py` | `GET/PATCH /api/settings`，`GET /api/settings/usage` | P0/P1 |
| `app/api/router.py` | 挂载 `/settings` | P0 |
| `app/schemas/settings.py` | 聚合响应 + 分段 PATCH 请求（`extra="forbid"`） | P0 |
| `app/services/user_settings_service.py` | 读默认值、合并覆盖、部分更新 | P0 |
| `app/models/user_settings.py` + `alembic/versions/014_user_settings.py` | 新表 | P1 |
| `app/services/review_service.py`、`app/api/review.py` | 每日上限按用户取值 | P1 |
| `app/tasks/reminder_tasks.py`、`tasks/celery_app.py` | 每小时跑、按用户小时过滤 + 当日去重 | P1 |
| `app/services/scheduler_service.py` | 留存率/最长间隔可入参 | P2 |
| `app/tasks/understand_tasks.py`、`clean_tasks.py` | 读用户级内容偏好 | P2 |

### 2.6 取数与状态

- React Query 已在用（`main.tsx:47-77`、`ReminderBanner.tsx:59`）——`frontend/docs/query-and-state-plan.md` 开头「未安装」的现状段已过期，本设计按代码实测。
- Query key：`['settings']`、`['settings','usage']`；`staleTime` 用全局 30s（`api/queryClient.ts:39`）。
- Mutation：`retry: 0`（全局已定，`queryClient.ts:45`），`onMutate` 写乐观值，`onError` 回滚，`onSuccess` 用响应整体 `setQueryData`。
- 设备偏好：不经过 React Query；`useDevicePrefs` 读写 localStorage，并在 `document.documentElement` 上设
  `data-font-scale="sm|md|lg"`、`data-reduce-motion="true|false"`，CSS 只认这两个属性。
- 语言仍走 `useTranslation().setLocale`（`i18n/index.tsx:94-99`），设置页只是换了个入口，不新写一套同步逻辑。

---

## 3. 持久化与接口

### 3.1 三层存储

| 层 | 放什么 | 例子 | 跨设备 | 谁能改 |
|---|---|---|---|---|
| 设备本地 `localStorage` | 只对这台机器有意义的偏好 | 字号、动效、免打扰、侧栏、ADHD 默认 | 否 | 用户 |
| 用户级数据库 | 影响服务端行为或需要跨设备的偏好 | 每日上限、留存率、邮件、AI 语言 | 是 | 用户 |
| 部署 env | 密钥、模型、配额、基础设施 | API key、DB、SMTP、上传限制 | — | 部署者 |

冲突规则：**用户覆盖 > env 默认**；设备本地不参与服务端判断。

### 3.2 数据库（P1）

新建表 `user_settings`，一行一个用户，**没有行 = 全用默认值**（`GET` 不写库）：

```text
user_settings
  id                   UUID PK                （沿用 BaseModel）
  user_id              VARCHAR UNIQUE FK users.id ON DELETE CASCADE
  daily_review_limit   INTEGER NOT NULL DEFAULT 10
  desired_retention    FLOAT   NOT NULL DEFAULT 0.9
  max_interval_days    INTEGER NOT NULL DEFAULT 3650
  email_hour           INTEGER NOT NULL DEFAULT 9
  output_locale        VARCHAR(10) NOT NULL DEFAULT 'follow' （follow/zh-CN/en）
  card_max_new_per_run INTEGER NOT NULL DEFAULT 500
  clean_simplify_formula BOOLEAN NOT NULL DEFAULT 0
  clean_normalize_spacing BOOLEAN NOT NULL DEFAULT 1
  clean_normalize_heading_levels BOOLEAN NOT NULL DEFAULT 1
  created_at / updated_at
```

约束：

- `CHECK daily_review_limit BETWEEN 5 AND 100`
- `CHECK desired_retention BETWEEN 0.70 AND 0.98`
- `CHECK max_interval_days BETWEEN 365 AND 3650`
- `CHECK output_locale IN ('follow','zh-CN','en')`
- `CHECK card_max_new_per_run BETWEEN 50 AND 1000`
- `CHECK email_hour BETWEEN 0 AND 23`

为什么是**显式列**而不是 `users.preferences` JSON：

1. 默认值能落在 DDL 上，不依赖应用层补默认；JSON 的「缺键」和「显式 null」会混。
2. SQLAlchemy 对 JSON 的原地修改不追踪，得靠 `MutableDict` 或整体重赋值 —— 是个已知坑。
3. 数值范围能用 `CHECK` 兜住；设置值直接参与调度计算，越界代价高。

为什么**不新建一套「用户偏好」表来放语言/邮件**：`users.locale` 已被 worker 读取（`understand_tasks.py:100`），
`email_reminder_enabled` 已被发信任务过滤（`reminder_tasks.py:60`），迁移它们只有风险没有收益。
`user_settings` 只装**新**字段；聚合接口负责把两处拼成一个响应。

### 3.3 接口

```http
GET /api/settings
200 {
  "locale": "zh-CN",
  "study":   { "daily_review_limit": 10, "desired_retention": 0.9, "max_interval_days": 3650 },
  "reminders": { "email_enabled": true, "email_hour": 9, "email_configured": true },
  "content": {
    "output_locale": "follow", "card_max_new_per_run": 500,
    "clean_simplify_formula": false, "clean_normalize_spacing": true,
    "clean_normalize_heading_levels": true
  },
  "account": { "email": "a@b.c", "username": "alice" },
  "about":   { "app_version": "0.1.0", "app_env": "prod" }
}

PATCH /api/settings
{ "study": { "daily_review_limit": 20 } }
200  → 与 GET 同形（返回合并后的完整值）
```

约定：

- `locale` 是服务端记录（给 worker 与跨设备回填）；`content.output_locale='follow'` 表示「AI 输出跟随界面语言」，非 follow 才覆盖。
- `PATCH` 每段可选、段内字段可选；`extra="forbid"`，未知字段 422，错误里带字段路径。
- 现有 `PUT /auth/locale`、`GET/PUT /auth/reminder-settings` **保留**，内部改调同一个 service（同一套校验）；
  设置页走聚合端点，侧栏语言仍可走旧端点，两条路写同一列。
- `GET /api/settings/usage`（P1）只返回存储：
  `{"storage": {"used_bytes": 0, "quota_bytes": 0}}`。LLM 用量走既有 `GET /api/llm/usage`（前端 `api/llm.ts` 只读展示）。
  存储用 `SUM(Note.file_size)`（与 `upload.py` 同一口径）。
- 能力位（capabilities）用于解释「为什么开关没用」：全局邮件未配置时 `email_configured=false`，
  页面显示「管理员未启用邮件通道」而不是一个点了没反应的开关。

### 3.4 生效链路（逐项）

| 设置 | 写入 → 读取 | 生效点 | 延迟 | 怎么验 |
|---|---|---|---|---|
| 每日复习上限 | DB → `api/review.py` 请求内解析 | `review_service.py:114`（剩余额度）、`:316`（提交校验）、`:625,701`（统计） | 下一次请求 | 上限改 5：`/review/due` 的 `remaining_quota=5`；第 6 次提交 429 |
| 目标留存率 | DB → `scheduler_service.advance` 入参 | `scheduler_service.py:355` | 下一张卡结算 | 固定 `rand` 的单测断言间隔方向 |
| 邮件发送时间 | DB → Beat 每小时任务过滤 | `celery_app.py:131` 改 `crontab(minute=0)`；`reminder_tasks.py` 比对小时 | ≤ 1 小时 | 冻结时间单测 + `last_reminded_at` 当日去重 |
| 免打扰时段 | localStorage → `ReminderBanner` | `ReminderBanner.tsx:85` | 立即 | 改起止小时后横幅不再弹 |
| 字号 | localStorage → 根节点属性 | `base.css:284`（`html{font-size:16px}`）+ 令牌层 | 立即 | 三档截图；确认布局不溢出 |
| 减少动效 | localStorage → 根节点属性 | 现有 `prefers-reduced-motion` 规则改为「媒体查询或属性」 | 立即 | 强制开关后动画停止 |
| ADHD 默认 | localStorage → `NoteDetail` 初始化 | `useAdhdReader.ts:157` 改成读初值 | 进入笔记时 | 刷新后仍开启 |
| AI 输出语言 | DB → worker 查库 | `understand_tasks.py` 读 `output_locale`，`follow` 回落 `locale` | 下一次理解任务 | 中英各生成一次，断言提示词语言 |
| 单次卡片上限 | DB → worker | `understand_tasks.py` 把上限传给 `save_cards_idempotent(config=...)` | 下一次理解任务 | 超过上限的一批被截断，`SaveOutcome.truncated` 为真 |
| 清洗规则 | DB → `clean_tasks` | `cleaning_service.clean_rules(..., settings_override=)` | 下一次清洗 | 三条各开/关一次，比对 diff |

### 3.5 兼容、迁移、回滚

- **无数据迁移**：新表无行 = 默认；老用户不受影响。`users.locale` / `email_reminder_enabled` 原地不动。
- **接口兼容**：旧端点保留；新端点只增不改。`GET /api/settings` 不写库，避免「打开设置页就产生一次写」。
- **回滚**：P0 回滚 = 删路由与页面；P1 回滚 = 删表迁移 + 恢复 `review_service` 常量路径。
  每个阶段单独提交，可独立回退（`AGENTS.md` §4：本地提交、不推送）。
- **契约**：改完后 `python backend/scripts/dump_openapi.py` → `npm run gen:api` → `dump_openapi.py --check`，
  保证 `openapi.json` 与生成类型同步。

---

## 4. 设置项调研

### 4.1 判定框架：四个问题，有一个「否」就不进页面

1. **它是用户的选择，还是部署的事实？**（换台服务器该不该变）
2. **改了以后用户能不能看出来？**（看不出 → 不进页面；要么进只读诊断）
3. **误设的代价可逆吗？**（不可逆 → 加范围限制/二次确认，或干脆不做）
4. **生效链路现在通吗？**（不通 → 先接线，再上界面；见 §1.3）

补充一条产品判断：**设置页不是「功能清单」**。每加一项，都在增加「我该调哪个」的认知成本。
只放那些「用户已经遇到问题、且改完能自己确认变好了」的项。

拿仓库里的 `使用时的想法.txt` 逐条对照：8 条里直接与设置相关的只有第 4 条（导出）与第 5 条（清洗策略）；
第 1 条「记住看到哪」是自动行为，第 8 条「题目不能前后翻」是功能缺口 —— 都不该塞进设置页。

### 4.2 竞品对照：Anki 把设置拆成两层

Anki 的官方手册把可调项分成两处（[Preferences](https://docs.ankiweb.net/manual/preferences)、
[Deck Options](https://docs.ankiweb.net/manual/deck-options)）：

| 层 | 放什么 | 对本项目的启示 |
|---|---|---|
| Preferences | 语言、主题、界面尺寸、**减少动效**、**下一天从几点开始**、学习提前量、时间盒、音频、编辑、同步、备份 | 「外观 + 应用行为」是一层；**纯界面偏好和设备相关项**放这里 |
| Deck Options | 每日新卡/复习上限、学习步、**FSRS 目标留存率**、最长间隔、Easy Days、显示顺序 | 「影响排期的参数」单独一层，并且和「它作用于谁」（preset）绑定 |

三条可直接借用的结论：

1. **影响排期的参数不混进通用偏好**。EngramNote 没有牌组层级，作用域只能是「当前用户」——所以学习节奏单独一组，
   且文案必须写「只影响之后的排期」。
2. **目标留存率必须带代价说明**。Anki 文档写明：默认 90%，高于 90% 工作量快速上升，高于 97% 会压垮；
   改参数后「是否重排」默认**不重排**。本项目的 `config.py:496-499` 也写了同一来源（R(S,S)=0.9）。
3. **「下一天从几点开始」是日界，不是到期时刻**。Anki 的 4AM 同时决定「今天」；本项目的 `review_due_hour=4`
   只改 `next_review_at`，额度与报告仍按 00:00。**名字像、语义不同，不能照搬。**

反面对照：Anki 的 Deck Options 里有 `FSRS parameters`（一长串浮点）与 `Historical retention` 这类**只给高级用户**的项。
本项目不暴露 FSRS 参数与抖动比例 —— 它们改的是「算法内部」，用户无法判断对错，出错也难以归因。

### 4.3 推荐清单（按阶段）

> 「改动」是粗估，用于排期，不是承诺。所有行都以当前代码为准。

#### P0：已有能力搬家（不新增行为，无迁移）

| 项 | 用户问题 | 现状证据 | 落点 | 改动 |
|---|---|---|---|---|
| 界面语言 | 我看不懂中文/英文 | `i18n/index.tsx:94-99` | 外观与语言组；侧栏保留快捷入口 | 前端 |
| 邮件提醒开关 | 我不想每天收邮件 | `api/auth.py:331` | 提醒组 | 前端 + 聚合门面 |
| 桌面通知 | 我想在浏览器里被提醒 | `notifications.ts:12` | 提醒组（本地开关只管「弹不弹」，权限本身只能由浏览器授予） | 前端 |
| 退出所有设备 | 我怀疑账号在别处登录 | `auth.ts:77`（`all_devices`） | 账号组 | 前端 + 确认框 |
| 版本/环境 | 我装的是哪个版本 | `app/version.py`（单一来源） | 关于组（只读） | 后端加只读字段 |

#### P1：小改后端或纯前端（风险可控）

| 项 | 用户问题 | 现状证据 | 生效改动 | 粗估 |
|---|---|---|---|---|
| 每日复习上限 | 10 道太少/太多 | `config.py:484`；`review_service.py:43,316,625,701`；`api/review.py:99,108,159` | 新字段 + 4 处取值 + 3 个测试 | 0.5–1 天 |
| 邮件发送时间 | 9 点我已经在上班路上 | `config.py:554` 死配置；`celery_app.py:131` 固定 9:00 | Beat 改每小时 + 按用户小时过滤 + 当日去重 | 0.5 天 |
| 免打扰时段 | 晚上别弹通知 | `notifications.ts:54` 写死 22–8 | 本地偏好 + `ReminderBanner` 读取 | 0.25 天 |
| 字号 | 字太小 | `base.css:284` 根字号 + rem 令牌 | 根节点属性 + 三档 CSS | 0.5 天 |
| 减少动效 | 动画让我分心 | 现有 9 处 `prefers-reduced-motion` | 增加手动态（属性选择器与媒体查询并列） | 0.5 天 |
| ADHD 默认开启 | 每篇都要点一次 | `useAdhdReader.ts:157` | 读本地初值 | 0.1 天 |
| 侧栏折叠记忆 | 每次刷新都展开 | `App.tsx:177` | 读本地初值 | 0.1 天 |
| 存储用量 | 我还有多少空间 | `upload.py:369-374` 只在上传时算 | 新只读端点 + 进度条 | 0.5 天 |
| 导出全部 | 我想把东西搬走 | `notes.ts:545`；`NotesList.tsx:229` | 设置页放入口（复用现有动作） | 0.1 天 |
| 邮箱/用户名 | 我现在的账号是什么 | `GET /auth/me` | 只读展示 | 0.1 天 |

#### P2：跨层改造或需要产品决策

| 项 | 用户问题 | 为什么是 P2 | 关键改动 |
|---|---|---|---|
| 目标留存率 | 我想记得更牢/复习少点 | 改的是排期，误设代价高；需要「不重排」语义 | `scheduler_service.advance` 入参 + 说明文案 + 单测 |
| 最长间隔 | 不想 10 年后再见 | 同上，但影响更小 | 同上 |
| AI 输出语言 | 界面中文，但想用英文生成卡片 | 现在语言**同时**决定 UI 与提示词（`understand_tasks.py:94-102`） | `output_locale` 字段 + worker 回退链 |
| 每日新增卡片上限 | 一天别塞太多新卡 | 现在只有「单次最多 500 张」（`config.py:459`），没有「新卡/天」概念 | 当日新卡计数 + 生成/展示延后策略（研究项） |
| 清洗规则 | 公式/空格/标题按我的习惯处理 | worker 里读配置，需改成读用户覆盖 | `clean_tasks` + `cleaning_service` |
| 单次生成上限 | 一次别灌太多卡 | 是安全阀，不是偏好；放开会灌库 | 校验 + 上限 |
| 深色主题 | 晚上刺眼 | 没有深色调色板；要重做对比度审计 | 令牌层 + 全站截图回归（3–5 天） |
| 修改密码 | 我想换密码 | 无端点；要同时撤销会话 | 新端点 + bcrypt + `refresh_tokens` |
| LLM 用量面板 | 我用掉多少额度 | 有端点无前端；需要新 API 模块 | `api/llm.ts` + 只读卡片 |
| 删除账号 | 我想彻底删除 | 删数据不可逆，必须产品决策与导出引导 | 见 `AGENTS.md` §6 |

> **日界/时区不是 P2 交付项**，是研究项：它同时影响额度、报告、目标与到期对齐，必须先统一口径（见 §4.5），不进 v1。

### 4.4 明确不放进设置页

| 项 | 为什么不做 | 替代 |
|---|---|---|
| LLM 供应商 / 模型 / API key | 密钥泄露面、成本与提示词随模型变；一改全站行为 | 保留 env；设置页只显示「当前通道已配置」 |
| 嵌入模型 / 分块大小 / RAG 权重 / 相似度阈值 | 改了要重建索引；用户无法判断效果 | env；只读诊断里显示模型名 |
| 上传大小 / 扩展名 / 笔记数上限 | 部署策略与磁盘容量，不是个人偏好 | 上传页显示剩余配额 |
| SMTP 主机/账号 | 基础设施与凭据 | 只读显示「邮件通道是否可用」 |
| 调度算法 FSRS/SM-2、抖动比例 | 算法内部；改错无法归因 | env；文档说明 |
| CORS / 日志级别 / 备份保留 / 数据库路径 | 运维 | env |
| 滚动位置、答题会话记忆 | 自动行为，用户没有「选择」 | 保持现状 |
| 复习排序（到期优先 / 薄弱优先） | 现行排序有明确理由（`review_service.py:81-84`），且改动影响每次复习体验 | 保持现状；要改先做实验 |
| 回收站保留期 | 涉及自动删除，语义比设置页更重 | 先明确删除策略，再谈开关 |
| 「清空所有数据」 | 与删除账号同类，不可逆 | 先做导出，再谈删除 |

### 4.5 日界/时区：为什么单独拎出来

- 现状：`timeutil.py:14-23` 固定 `Asia/Shanghai`；`today_start_utc`（`:26-38`）被额度（`review_service.py:111`）、
  报告（`report_service.py:62-63`）、目标（`goal_service.py:289-290`）、LLM 账本（`llm_accounting_service.py:544,552`）共用。
- `review_due_hour=4` 只影响 `next_review_at`（`scheduler_service.py:242`），**不改变「今天」**。
- 因此「一天从几点开始」如果只改 `review_due_hour`，会出现：卡片 4 点到期、额度 0 点重置、日报 0 点切日。
  三个口径同时存在，用户看到的就是「明明到期了却提交不了」。
- 结论：**v1 不暴露**。要做就做成一个「日界」设置，同时改 `timeutil`、额度、报告、目标与到期对齐，
  并补跨日测试；这是独立的一轮工作，不应塞进设置页首批。

---

## 5. 路线图

> **2026-11-04 实际执行**：阶段 1（P0）与阶段 2（P1）合并完成；`user_settings` 表在阶段 1
> 一并创建（原计划推迟到阶段 2）。与下面步骤的差异、以及 Dashboard 旧开关的去留见 §9。

### 阶段 0：确认（本文）

产出：本文件 + 需要拍板的 6 个问题（§8）。不动代码。

### 阶段 1：P0（页面骨架 + 搬家）

1. 后端：`schemas/settings.py`、`services/user_settings_service.py`、`api/settings.py`、`router.py`；
   旧端点改调同一 service。**不建表**，默认值从 `Settings` 读。
2. 前端：`pages/Settings.tsx` + `settings/*` + `Settings.module.css`；`api/settings.ts`；
   `useDevicePrefs`；路由与侧栏入口；`Settings` 图标。**P0 只挂已接线的组**（语言、提醒、账号、数据与关于）。
3. 搬家：语言、邮件开关、桌面通知、退出所有设备、关于。
4. Dashboard：把 `EmailReminderToggle` 的 UI 移进设置页，删掉仪表盘那张卡片；仪表盘只留一行「提醒设置」链接。
5. 测试：`Settings.test.tsx`（六组、开关/数字/下拉调用、失败回滚、设备偏好、权限状态）、后端 `test_user_settings.py`（默认值/部分更新/越界/每日上限/邮件时间/调度参数/语言/清洗）与 `test_settings_api.py`（注册→PATCH→review stats 的端到端联动）。
6. 门禁：§6 全部命令。

**回滚**：删路由/页面/端点，Dashboard 恢复卡片。

### 阶段 2：P1（行为设置）

1. 迁移 `014_user_settings` + 模型 + 服务读写合并。
2. 每日上限：`api/review.py` 解析偏好并传入 `get_due_quizzes` / `submit_answer` / `get_review_stats`；
   `review_service` 去掉三处模块常量直读。
3. 邮件时间：Beat 改每小时；任务按 `email_hour` 过滤；用 `last_reminded_at` 做当日去重（列已存在，`models/user.py:58`）。
4. 前端：字号、动效、免打扰、侧栏、存储用量、导出入口、只读账号信息。
5. 测试：上限生效（5 道）、邮件小时过滤（冻结时间）、设备偏好持久化、字号三档快照。
6. 门禁 + 契约同步。

**回滚**：迁移 downgrade；`review_service` 回常量；Beat 回 9:00。

### 阶段 3：P2（主体已落地；密码/删号/深色主题另立计划）

2026-11-05 已完成：目标留存率、最长间隔、AI 输出语言、清洗规则、卡片上限、LLM 用量。
仍未做：深色主题、修改密码、删除账号 —— 它们不是设置页联动问题，而是视觉重构/认证/数据生命周期决策。

每项独立提交、独立门禁；不打包成一次大改。

---

## 6. 验收

### 6.1 后端断言

- 无 `user_settings` 行时，`GET /api/settings` 返回 env 默认值，且**数据库行数不变**（不写库）。
- `PATCH {"study":{"daily_review_limit":20}}` 只改该字段，其余保持默认/原值。
- 越界（每日上限 4 / 101、留存率 0.5 / 1.0）→ 422，错误里含字段路径；未知字段 → 422。
- 两个用户互不影响（A 改 5，B 仍 10）。
- 上限=5：`/review/due.remaining_quota=5`；第 6 次提交 429 `DAILY_REVIEW_LIMIT_REACHED`；`/review/stats.daily_limit=5`。
- 留存率=0.8 与 0.9：固定 `rand` 的单测里，间隔方向符合「低留存 → 更长间隔」。
- 邮件任务：`email_hour=7` 的用户在 7 点被选中、9 点不再收到；`last_reminded_at` 当天已有值则跳过。

### 6.2 前端断言

- 六组都已接线项都渲染；每组的标题是 `<h2>`，控件有可访问名（`getByLabelText` 能取到）。
- 开关失败：回滚到旧值 + toast（不是静默弹回，参考 `Dashboard.tsx:810-818`）。
- 数字越界：就地提示，不发 `PATCH`。
- 语言切换后，设置页文案立即变；刷新后保持。
- 本地偏好刷新后保持；两个标签页通过 `storage` 事件同步。
- 键盘：Tab 可达全部控件；`details` 可展开；确认框焦点被困住（现有 `Dialog` 已实现）。
- 左栏分组：Tab 可达，↑/↓ 切换分组并滚动到对应卡片；当前组带 `aria-current="page"`。

### 6.3 门禁命令

```powershell
# 后端（项目 conda 环境：mineru_env，见 start.bat:20）
python -m ruff check app tests scripts
python -m pytest -q

# 契约（新增/改端点后）
python backend/scripts/dump_openapi.py
npm --prefix frontend run gen:api
python backend/scripts/dump_openapi.py --check
node frontend/src/api/generated/openapi-drift.mjs --self-test  # 或 npm --prefix frontend run gen:api:drift

# 前端
npm --prefix frontend run lint
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run e2e
npm --prefix frontend run docs:links
```

### 6.4 手工检查

- **1280×800**：两栏不重叠，右栏卡片完整可用；左栏滚动时 sticky 正常。
- **1440×900 / 1920×1080**：内容不被拉到过长；右栏最宽 812px，不随窗口无限变宽。
- **1180×720（最小支持宽度）**：无横向溢出；窄于此宽度出现横向滚动是**预期行为**，不降级成单列。
- **125% / 150% 缩放**：行标签与控件不互相压叠；下拉与数字输入不截断。
- **读屏**：左栏五个已接线分组可朗读，当前组带 `aria-current="page"`；保存状态有 `aria-live`；危险操作对话框有可读标题与说明。

---

## 7. 风险

| 风险 | 表现 | 缓解 |
|---|---|---|
| 设置成为第二套配置源 | 「env 改了没生效」/「页面改了重启又变」 | 读取顺序固定并写进文档；设置页脚注「服务端默认由部署决定」 |
| 死旋钮上界面 | 用户改完没反应 | §1.3 清单；先接线后上界面；能力位解释不可用原因 |
| SQLite 写放大 | 每次切开关写一次 | 数值防抖、只 PATCH 改动字段、GET 不写 |
| 契约漂移 | 生成类型与后端不一致 | `dump_openapi --check` + `gen:api` 进 CI |
| i18n 缺键 | 英文界面回落中文 | 两侧 messages 同批提交；`i18n.test.tsx` 会红 |
| 留存率误设 | 复习量暴涨/遗忘变多 | 档位限制 0.70–0.98 + 代价文案 + 默认不重排 |
| 页面膨胀 | 变成「什么都有」 | §4.4 不做清单；新项先过 §4.1 四问 |

---

## 8. 已拍板 / 仍未决的 6 件事

> 2026-11-04：第 1、2、4、5、6 项已按建议落地；第 3 项（留存率）仍是 P2，未进首批。
> 下面保留原始问题与建议，作为决策记录。

按 `AGENTS.md` §6，以下都属于「改产品语义或接口」，需要确认后再动手：

1. **设置页与侧栏语言开关并存，还是只留一个？**
   建议：并存。侧栏是高频快捷入口，设置页是权威入口，两者调同一个 `setLocale`。
2. **每日复习上限的范围与边界值？**
   建议：5–100，不允许 0（0 会被读成「不限」，与现有 `remaining_quota` 语义冲突）。
3. **目标留存率是否进首批？改了要不要重排已有卡片？**
   建议：进 P2；只影响未来排期，不重排（与 Anki 默认一致），页面写明。
4. **日界/时区是否进 v1？**
   建议：不进。先把 §4.5 的口径统一单独做一轮。
5. **存储方案：新建 `user_settings` 表，还是 `users.preferences` JSON？**
   建议：新表（理由见 §3.2）。
6. **账号区首批只做「退出所有设备」，改密码/删账号后置？**
   建议：是。删账号必须单独做数据与导出策略，不能在设置页顺手加。

---

## 9. 落地记录（2026-11-05）

### 9.1 后端

| 落点 | 实现 | 关键约束 |
|---|---|---|
| 存储 | `backend/app/models/user_settings.py` + `alembic/versions/014_user_settings.py`、`015_settings_expansion.py` | `daily_review_limit` 5–100、`email_hour` 0–23、留存率 0.70–0.98、最长间隔 365–3650、卡片上限 50–1000 的 CHECK |
| 读写 | `backend/app/services/user_settings_service.py` | 无行 = 环境默认；`GET` 不写库；PATCH 只改显式字段；布尔值用 `is not None` 区分“显式关闭” |
| 接口 | `backend/app/api/settings.py` 的 `GET/PATCH /api/settings`、`GET /api/settings/usage` | PATCH 返回合并后的完整快照；未知字段 422；`/usage` 与上传配额同口径 |
| 每日上限 | `api/review.py` 的 `_effective_daily_limit()`；`review_service.py` | due / submit / stats 三处都读用户设置 |
| 留存率 / 最长间隔 | `review_service.py` 与 `api/review.py` 的卡片复习都传 `scheduling_settings(...)` 给 `scheduler_service.advance` | 只覆盖本次调度的两个字段，不改全局配置；不重排已有卡片 |
| 邮件时间 | `tasks/reminder_tasks.py` 的 `should_send_now()` / `select_due_recipients()`；`tasks/celery_app.py` | Beat 每小时；按北京时间匹配；`last_reminded_at` 当日去重 |
| 内容语言 | `tasks/understand_tasks.py` | `output_locale='follow'` 回落 `users.locale`；无请求上下文也能工作 |
| 卡片上限 / 清洗 | `understanding_service.py`、`card_intake_service.py`、`cleaning_service.clean_rules(..., settings_override=)`、`clean_tasks.py` | 只影响下一次生成/清洗；历史数据不自动重跑 |
| 兼容 | `PUT /auth/locale`、`PUT /auth/reminder-settings` 保留 | 与聚合端点写同一批列 |

### 9.2 前端

| 落点 | 实现 |
|---|---|
| 路由/入口 | `App.tsx` 的 `/settings` 懒加载；`Sidebar.tsx` 底部「设置」按钮 |
| 页面 | `pages/Settings.tsx` + `Settings.module.css`：六组；学习高级折叠、内容组、权限状态、LLM 用量、今日额度 |
| 服务端联动 | `api/settings.ts`、`api/llm.ts`；保存每日上限/留存率后刷新 `reviewStats` |
| 设备偏好 | `utils/devicePrefs.ts`：字号 / 减少动效 / ADHD 默认 / 侧栏折叠 / 桌面通知 / 免打扰 |
| 通知权限 | `utils/notifications.ts`：权限状态、请求权限、测试通知；不支持/被拒时按钮降级 |
| 账号 | `AuthContext.logoutAllDevices()` → 撤销全部刷新令牌 |
| 数据 | 导出、存储用量、版本、LLM 用量只读 |
| 去重 | Dashboard 的旧邮件开关已删除，改为 `/settings#reminders` 链接 |

### 9.3 验证

| 门禁 | 结果 |
|---|---|
| 后端全量 | `pytest -q`：**1722 passed, 30 skipped** |
| 后端设置/联动 | `tests/test_user_settings.py` 16 条、`tests/test_settings_api.py` 2 条通过 |
| 前端全量 | `vitest run`：**731 passed / 62 files**（设置页 12 条、ADHD hook 1 条、NoteDetail 22 条、通知 5 条） |
| 前端 lint / tsc / build | 全绿 |
| Playwright | `e2e/settings.spec.ts` 2 条通过（六组、PATCH、字号、深链接） |
| 契约漂移 | `dump_openapi.py --check` 与 `gen:api:drift` 通过 |
| 文档链接 | `docs:links` 通过 |
| 真实冒烟 | 1440×1000 临时库：每日上限 10→25，刷新后 UI/`/api/review/stats` 一致；控制台 0 错误 |

### 9.4 与路线的差异 / 未做

- **未做**：深色主题、修改密码、删除账号 —— 分别需要视觉审计、认证/会话策略、不可逆数据策略。
- **ADHD Reader 默认开启的“只亮按钮”缺陷已修**：设置默认 true 时，首次 effect 常遇到 `markdownRef.current=null`（笔记数据未到），旧代码直接 return 后不再重跑；现在 root 不存在时观察 `document.body`，容器真正挂载后再接上遮罩/块类名/鼠标监听。`useAdhdReader.test.tsx` 与 `NoteDetail.test.tsx` 覆盖“ref 晚于 hook 挂载”和真实正文容器。
- **免打扰时段存设备本地**：换设备不跟随（刻意选择）。
- **日界/时区仍固定 Asia/Shanghai**：没有进本批。
- **Alembic 015 不在测试库执行**：测试库用 `create_all`；生产走 Alembic，开发库走 `_migrate_sqlite()` 补列。
- **LLM 用量价格未配置时显示“未配置单价”**，不把 0 元当真实花费。

### 9.5 复跑命令

```bash
# 后端
cd backend
python -m pytest -q
python -m pytest tests/test_user_settings.py tests/test_settings_api.py -q

# 前端
cd frontend
npm run lint
npx tsc --noEmit
npm test
npm run build
npx playwright test e2e/settings.spec.ts --project=chromium
npm run gen:api:drift

# 文档
cd ..
npm --prefix frontend run docs:links
```

---

## 10. 联动验收矩阵

| 设置改动 | 可观察结果 | 证据 |
|---|---|---|
| 每日上限 10→25 | `/review/stats.daily_limit=25`；due 剩余额度变化；第 26 道 429 | `test_settings_api.py`、`test_user_settings.py` |
| 留存率 0.80→0.95 | 固定 `rand` 下下一次间隔变短 | `test_higher_retention_shortens_next_interval` |
| 最长间隔 3650→365 | 间隔被夹紧到 ≤365 | `test_max_interval_is_enforced_from_user_view` |
| 邮件开关/时间 | 任务按 `email_hour` 选人；当日去重 | `test_reminder_task_selects_users_by_their_own_hour` |
| 输出语言 follow/zh/en | worker 解析出 `users.locale` 或显式语言 | `test_resolve_output_locale_follows_user_and_falls_back` |
| 卡片上限 500→50 | 一次理解最多新增 50 张 | `GateConfig` + `save_cards_idempotent` 既有用例；`understand_tasks` 传参 |
| 清洗三条开关 | `clean_rules()` 输出随开关变化 | `test_clean_rules_respects_user_override` |
| 字号/动效/侧栏/ADHD | 根节点、`data-*`、localStorage 变化；ADHD 默认开启时正文容器挂上 `adhdReaderActive` 与 `adhdBlock` | `devicePrefs.test.ts`、`Settings.test.tsx`、`useAdhdReader.test.tsx`、`NoteDetail.test.tsx` |
| 桌面通知权限/测试 | 权限状态正确；测试通知调用 `Notification` | `notifications.test.ts`、`Settings.test.tsx` |
| 设置页 PATCH 形状 | 六组控件发对应段，失败回滚 | `Settings.test.tsx`、`e2e/settings.spec.ts` |

---

## 附录 A：`config.py` 字段归属（全量分类）

| 归属 | 字段 |
|---|---|
| **进页面（P0/P1/P2）** | `daily_review_limit`、`fsrs_request_retention`、`fsrs_max_interval_days`、`card_max_new_per_run` |
| **待接线后才可进页面** | `email_reminder_hour`（Beat 先改每小时）、`clean_*`（worker 先读用户覆盖）、`reminder_quiet_hours_*`（先决定存服务端还是设备本地） |
| **只读展示（用量/状态）** | `app_name`、`app_env`、`max_storage_per_user_mb`、`max_notes_per_user`、`max_upload_size_mb`、`allowed_extensions`、`llm_daily_token_quota`、`llm_daily_cost_quota`、`llm_price_*`、`embedding_model`、`review_scheduler` |
| **仅部署（不进页面）** | `database_url`、`storage_*`、`minio_*`、`celery_*`、`jwt_*`、`*_api_key`、`*_base_url`、`mineru_*`、`asr_*`、`smtp_*`、`cors_*`、`trusted_proxies`、`log_*`、`backup_keep`、`storage_backup_keep`、`chunk_size`、`chunk_overlap`、`rag_*`、`similarity_threshold`、`vector_similarity_floor`、`review_fuzz_ratio`、`review_due_hour`、`email_reminder_enabled`、`debug`、`log_sql` |

> `review_due_hour` 与 `reminder_quiet_hours_*` 被列进「仅部署」不是永久结论：前者要先统一日界口径（§4.5），
> 后者要先决定「存服务端还是设备本地」（本文建议设备本地）。

## 附录 B：调研来源

- 项目内：`backend/app/config.py`、`api/auth.py`、`api/review.py`、`services/review_service.py`、
  `services/scheduler_service.py`、`tasks/reminder_tasks.py`、`tasks/celery_app.py`、`utils/timeutil.py`、
  `frontend/src/{App.tsx,Sidebar.tsx,pages/Dashboard.tsx,components/ReminderBanner.tsx,utils/notifications.ts}`。
- 走查：`docs/ux-walkthrough-report-20261021.md`（仪表盘首屏两条横幅、语言持久化、静默时段相关观察；移动端部分只作为问题严重度的旁证）。
- 竞品：[Anki Preferences](https://docs.ankiweb.net/manual/preferences)、
  [Anki Deck Options](https://docs.ankiweb.net/manual/deck-options)（设置分层、FSRS 留存率与「不重排」语义）。
- 项目约定：`AGENTS.md` §4/§5/§6、`docs/sqlite-single-writer.md`、`frontend/docs/openapi-client.md`。
