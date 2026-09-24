# 开源化差距报告（Open-Source Readiness）

> **状态：活文档** ｜ 最后核对：2026-09-24 ｜ 权威性：本主题的现状依据（§执行进度 随做随更新）
> ⚠️ **§2 / §3 是"核查当日（2026-09-23）"的判断快照，不是现状。**
> 哪些已完成、证据是什么，**一律以 [§执行进度](#执行进度本文发出后的实际进展) 为准**；
> 2026-09-24 的复核已就地更正其中若干条已过期的结论（每条都标了"已修"与实测依据）。
> **性质**：本文件回答一个问题 —— **EngramNote 离"一个别人愿意用、愿意贡献的开源项目"还差什么**。
> 它不是功能计划，是对着 `origin/main` 的真实快照与本地工作区做的逐条核查。
>
> - 核查日期：**2026-09-23**
> - 比对对象：`github.com/trx-0833/EngramNote` 的 `main` 快照（tarball，3,353,820 字节）
> - 本地 HEAD：`7ee732b`（`ci: 撤除临时诊断（CI 已全绿 run #10）+ 记账 BO.5.5`）
> - 远端 `main` HEAD：`7ee732bf`（GitHub API `pushed_at` = 2026-09-15T04:50:17Z）
> - 逐文件比对：**571 个已跟踪文件，0 个只存在于远端、0 个只存在于本地、内容差异 0 处**
>   （比对脚本与全量结果：`_ref/compare.py`、`_ref/compare-report.txt`；
>   快照本身核查后已清理，需要时 `python _ref/fetch_snapshot.py` 一键重取）
> - 未跟踪但存在于本机的工作产物：945 个文件（`.trae/` 52 个设计文档 10,807 行、
>   `_backup/` 8 个快照、`参赛/参赛贴文.md`、`backend/tests/results/` 等），**均未入库**
>
> **取数方式说明**：本机 `git` / `curl` / `Invoke-WebRequest` 的 Schannel TLS 栈当前
> 不可用（`SEC_E_NO_CREDENTIALS`），改用 Python（OpenSSL + `urllib`）走
> `api.github.com` 与 `codeload.github.com` 取快照；`github.com:443` 用 Python 实测可达。

---

## 执行进度（本文发出后的实际进展）

> 记账口径：**已完成**必须带可核对的证据（提交号 / 实测命令 / 机器结论）；
> 未做的写在这里，不写"计划中"。
>
> **取数时点：2026-09-24（第二轮）**｜ 远端 `main` HEAD = **`0a22405`**
> （`git rev-parse origin/main`）｜ 第一轮那 14 个提交**均已推送**。
> §2 / §3 里写的是**核查当日（2026-09-23）**的判断；哪些已经修完、证据是什么，
> **以本节为准**（复核时已就地给相关小节加上"已修"标注）。
>
> 📌 并行会话提示：前端视觉重构由**另一个会话**在推进，它已在远端留下
> `fbad410`、`6ab35f5`、`154060c`，并贡献了 `0a22405` 之前那 44 个提交中的大部分
> （`0a22405` 本身是本会话的依赖合并）。这些**不属于**本次开源化记账，
> 只是会把 HEAD 往前推 —— 引用 HEAD 时请现查。
> 另：该会话仍在改 `frontend/src/**`，工作区里可见它未提交的 `frontend/docs/e2e.md`
> 与一批 `frontend/_*.mjs` 探针脚本（**不要**把它们卷进本会话的提交）。
>
> ⚠️ 本文是活文档，仓库里有多处按 `docs/open-source-readiness.md:<行号>` 引用它。
> 2026-09-24 这次记账让全文从 **789 行涨到 844 行**，那些**行号引用会整体下移**；
> 本次能改的已改成"小节号 + 行号"，改不到的（不在写权限内）逐条登记在
> [`docs/journal/README.md`](journal/README.md) 的"行号引用漂移登记"一节。**引用本文请优先写小节号。**

### 已完成并推送（`a8a10be` → `0a22405`，第一轮 14 个 + 依赖合并 3 个）

| 批次 | 内容 | 证据 |
|---|---|---|
| — | README 按代码实测重写 + `参赛/` 撤出仓库（本地保留）+ `.gitignore` 防复发 | `a8a10be`；远端 README 与本地逐字节一致；`/contents/参赛` → 404 |
| 5.1 | **跑测试不再写真实 Vault**：会话级 `VAULT_DIR` 重定向 + 重绑模块级冻结引用 + 守卫测试 | `7af9dfe`；跑前跑后 `data/storage`(98 文件) 与 `db`(6 文件) **逐字节一致** |
| 0 | **JWT 密钥轮换 + 公开占位值黑名单** + 教程示范值改生成命令 | `07d0e9f`；实测旧占位密钥签发的令牌被 `JWTError` 拒绝；新增 4 条单测 |
| 1 | **PII 去标识化 30 处 / 18 文件** + 防复发守卫（码点构造禁止串，扫已跟踪文件） | `57e5058`；2026-09-24 复核：**除本文以外**的已跟踪文件里，姓名与"文档类型+姓名"组合均 **0 命中**（守卫 `backend/tests/test_no_personal_data.py:43-45` 的 `ALLOWLIST` **只放行本文**，并且反过来要求本文继续保留该姓名，见改正 3）；客户资料路径由新加的守卫当场发现 |
| 2 | **依赖分层**（运行/开发/CI/可选四份判据分离）+ 补齐 3 个模块级硬依赖 + 仓库根 `pyproject.toml` | `c14690b`；`pip install -e .` 在干净 venv 实测通过（27 依赖、包可导入） |
| 2.3 | **`.env.example` 双向守卫**（缺失/未知都报错）+ 补齐 41 个未登记字段（**60 → 103**） | `c14690b`；CI 新增同名步骤；2026-09-24 复跑 `python scripts/gen_env_example.py --check` → `Settings 字段 103 / 模板已登记 103 / 缺失 0 / 未知 0`，退出码 0 |
| 3 | **模板抄了就能跑**（`APP_ENV=dev` 生效、`GLM_MODEL` 生效）+ 缺 Key 启动即说明影响范围 | `c14690b`；实测复制模板可加载、密钥落在临时目录、未写真实 `data/` |
| 5.7 | **`check_env.py`**：删掉已废弃的 chromadb、`shell=True` → `shell=False`、补齐 20 项检测 | `c14690b`；新增守卫锁住三点（其中"不得再用 shell=True"用 AST 判定）；2026-09-24 复核：`REQUIRED_PACKAGES` 里已无 `chromadb`（`check_env.py:428` 有说明注释）、全文件 `shell=True` 0 命中 |
| 4 | **前端**：包元数据补齐、锁文件 371 条回归官方源（integrity 保留）、`VITE_API_BASE_URL` 可配置、lint/format 范围扩到 `e2e/ scripts/` | `3062582`；`VITE_API_BASE_URL` 注入实测命中产物；`vitest` 295 passed、`vite build` 通过 |
| 4 | **门禁升级**：prettier 与 a11y 从建议性改为**阻断** | `fe632f8`；YAML 解析通过，四个 job 全部步骤枚举确认；2026-09-24 复核：`ci.yml` 全文只剩**一处** `continue-on-error: true`（`:147` ruff format），a11y 步骤名已是 `Playwright a11y audit (axe-core, blocking)`（`:311`）、prettier 步骤名已是 `Prettier check (blocking)`（`:237`） |
| 5 剩余 | **JWT 算法白名单**（`config.py:672` 起 `jwt_algorithm` 的 `field_validator`）、**上传 `temp_id` 归属校验**（`upload.py:98-120` 归属旁载 + `:843-850` 读前校验）、**422 契约结构化**（`main.py:555-586` 返回数组 + `VALIDATION_ERROR`）、**安全响应头**（`middleware/error_handler.py:78-79` `nosniff` / `X-Frame-Options`）、**CORS 凭据可配置且默认关**（`config.py:536` `cors_allow_credentials = False`）、**反代真实 IP**（`config.py:553` `TRUSTED_PROXIES` + `rate_limit.py:115-144` 取 XFF 最右一跳）、**9 个零用例脚本搬出 `tests/`** | `3c631f9`；2026-09-24 实测：9 个脚本在 `backend/tests/` **0 个**、在 `backend/scripts/dev/` **9 个**（逐个 `os.path.exists` 核对） |
| 6 | **社区文件**：`CONTRIBUTING` / `SECURITY` / `CHANGELOG` / `UPGRADING` / `CODE_OF_CONDUCT` / `CODEOWNERS`（仓库根）/ `.editorconfig` / issue 与 PR 模板 / `dependabot.yml`；**22 份文档状态横幅**；`docs/README.md` 文档地图；`docs/journal/`；容器化补缺（`backend/.dockerignore` 等） | `3c631f9`；2026-09-24 实测：上述文件全部在 `git ls-files` 里、`backend/.dockerignore` 存在；带 `状态：` 横幅的文档 = **22 份**（`docs/` 17 + `frontend/docs/` 5）。⚠️ 例外：`sqlite-single-writer.md` 仍**无**横幅（本轮禁改），`docs/README.md` §4 有登记 |
| 7.2 | **依赖漂移当次结论入档** | `c741630`；`docs/security-scan.md:573-588`："**39 条声明里只有 2 条与真实环境相符**"（超出声明范围 18 条、传递依赖 49 条） |
| 7.3 | **版本号单一来源** | `c741630`；`backend/app/version.py:26` `__version__ = "0.1.0"`，`backend/app/main.py:43,635` 从它读取（不再硬编码），另有守卫测试；`frontend/package.json` 同版本 |
| — | **首个 tag** | `v0.1.0`：annotated（`git cat-file -t v0.1.0` → `tag`），指向 `154060c`，release note 即该 tag 的注解。⚠️ **GitHub Releases 页面上的 release 对象尚未创建**（tag ≠ release，见下方"尚未做"）|
| — | **合并 Dependabot 的 13 个绿 PR**（`git merge` 后本地提交，未走网页 Merge 按钮）| `b1526a6`（pip 声明对齐 6 条）+ `9fa6605`（Actions 版本 4 项）+ `0a22405`（npm 次/补丁 6 项）；证据：`0a22405` 的 **check-runs 20 条全部 `success`、非成功条目 0**（GitHub API 实测），开放 PR 数 **14 → 5** |

**关键实测结论（与本文原判断不同，已更正）**：
1. 基线**不是红的**：受限沙箱禁止写 `%TEMP%`，造成 100 error + 2 failed；
   放开后 **1139 passed / 3 skipped**。那些红是环境，不是缺陷。
2. `test_purge_file_consistency` 的"事后删目录"在正常路径下**确实自净**
   （真实存储逐字节未变）。真正的风险是它依赖"运行期间没人往存储目录写新目录"，
   而用户此时放进去的真实目录会被当垃圾删掉 —— 已改为结构上不可能写到真 Vault。
3. PII 实际是 **30 处 / 18 文件**（本文原写"12 文件"，**低估了**），
   并且还有一份**客户资料路径**（某电站运行技术标准 PDF）——
   由新加的守卫当场发现。
4. 前端 `format:check` 是建议性的，于是 `src/` 里 162 个文件的风格漂移
   从未被发现；现已成为阻断门禁。

### 第二轮（依赖收口 + 防复发）—— 本地 8 个提交，截至本文更新时**尚未推送**

> 触发原因：合并完 13 个绿 PR 之后，仓库里还剩 **5 个开放 PR**（`#10`、`#12`、`#13`、`#14`、`#15`），
> 其中 `#12`/`#13`/`#14`/`#15` 是 Dependabot "提议已在跑的版本、或跨大版本单独开 PR" 的产物。
> 这一轮把它们一次收口，并把"会导致复发的机制"钉住。

| 批次 | 内容 | 证据（提交号 + 实跑命令 / 机器结论）|
|---|---|---|
| 8.0 | **先建公式渲染护栏**（在升 `marked` 之前）| `a1651f9`；新 `frontend/src/utils/markdown.test.ts` **14 条**。起因：实测 `src/utils/` 下**没有任何用例**碰过 `$$...$$` / `$...$`，而那是 `marked` 大版本最容易**静默**碰坏的地方 |
| 8.1 | `typescript-eslint` 8.67.0 → **8.70.0**（PR `#15`）| `f60e398`；锁文件里**只有** `@typescript-eslint/*` 一族变化；`lint` / `format:check` / `vitest` **39 文件 438 条** / `build` 全过 |
| 8.2 | `marked` 14.1.4 → **18.0.13**（PR `#12`，跨 4 个大版本）| `5009c63`；**护栏 14/14 全过**（回退判据未触发）；`e2e` **10 passed**；锁文件只有 `marked` 一行变化 |
| 8.3 | `react-router-dom` 6.30.4 → **7.18.4**（PR `#13`，唯一进生产包的一个）| `ba9e11b`；先核实 35 处引用全为 v6 风格、`useHistory` / `<Switch` / `<Redirect` **0 命中**；`e2e` **10 passed** |
| 9 | **`vite` 5.4.21 → 8.3.0 + `@vitejs/plugin-react` → 6.1.1 + `vitest` → 4.1.11**（PR `#14`，三者必须联动）| `e3c68d1`；`esbuild` / `rollup` 已从依赖树消失、`rolldown 1.2.10` 就位；**三个手工分包仍在**（`react` 173.65 / `graph` 193.04 / `markdown` 400.00 kB）；`vitest` **39 文件 438 条**、`e2e` **10 passed**、**`a11y` 26 passed**（上一轮列为"本地未跑"，本轮补上）|
| 9 | 分包配置迁到 Rolldown 的 `codeSplitting` | 同上提交；`vite.config.ts` 由 `rollupOptions.output.manualChunks`（Rolldown 下**已弃用**）改为 `rolldownOptions.output.codeSplitting.groups`；**迁移等价性证据：产物文件名逐字节相同**（三个分包哈希未变）|
| 10.1 | **锁文件取源守卫**（防镜像污染复发）| `79c32d8`；`frontend/scripts/check-lockfile-registry.mjs`（实测 **347 条全部 `registry.npmjs.org`**）+ `backend/tests/test_lockfile_registry.py` **6 条**。**新建时先验证它会红**：缺 `frontend/.npmrc` 时为 5 过 1 红 |
| 10.2 | **项目级 `frontend/.npmrc` 把 registry 钉在官方源** | 同上；起因是本机全局 `.npmrc` 实际指向 `registry.npmmirror.com`，而 npm 缓存索引里镜像 **1014** 条 vs 官方 **77** 条 |
| 10.3 | **清 Dependabot 分支的 workflow**（默认 dry-run）| `302740b`；`.github/workflows/cleanup-dependabot-branches.yml` + `frontend/scripts/cleanup-dependabot-branches.mjs`（代码内两道锁：只删 `dependabot/` 前缀、只删**无开放 PR** 的）|
| 10.4 | **`dependabot.yml` 调参 + 纠正一处误判** | 同上；`open-pull-requests-limit` 5 → **10**（三份 ecosystem 各自计），对 `vite` / `@vitejs/plugin-react` / `vitest` / `jsdom` **忽略 major**（这四个必须联动升级）；`github-actions` 组扩到含 major |
| 11.1 | **修 `CHANGELOG.md` 里两句失实陈述** | `b55112b`；原写"本仓库没有 git tag、没有 release"，而 `v0.1.0` annotated tag 已推到远端 ⇒ 改为"有 tag、Releases 页面尚未创建" |

**第二轮的关键实测结论（含两处对既有判断的更正）**：

1. ⚠️ **更正**：`open-pull-requests-limit` **只管"同时开着的 PR 数"，不关分支**。
   此前把"15 个分支"归因为"限额太高"是错的 —— 真实机制是"PR 被合并/关闭后名额腾出，
   **分支却留在仓库里**，只有人或脚本显式删除才会消失"。
   而仓库设置里的 `delete_branch_on_merge` 对本项目**天然无效**：
   本项目是本地 merge 再 push，**从不点网页的 Merge 按钮**。
2. ⚠️ **更正**：`manualChunks` 在 Rolldown 下**不是"已移除"** ——
   官方迁移指南原文是"对象形式不再支持，**函数形式已弃用**"。
   实测：升到 vite 8 后**不改配置，三个分包一个没少**（先验证了这条，才决定迁移）。
3. **npm 自身缺陷**（与仓库无关，但会挡住升级）：npm **10.9.8** 解析 `vitest@4` 的
   **可选 peer 环**（`vitest` ↔ `@vitest/browser-*`）时崩溃
   （`Cannot read properties of null (reading 'edgesOut')`，`#loadPeerSet` 无限递归；
   `--package-lock-only` 同样复现）。绕法：换 **npm 11.20.0** 跑同一条命令。
   `lockfileVersion` 仍是 3，CI 用 `npm ci` 只读锁文件，故 runner 上不受影响。
4. **审计告警 11 → 2**（`npm audit --registry=https://registry.npmjs.org`）：
   消掉 `vite`、`vitest`（**critical**）、`react-router` / `react-router-dom`
   （open redirect，**唯一进生产包的那条**）、`esbuild`、`postcss`、`nanoid`。
   这是"版本对齐"的**附带结果**，不是针对性的漏洞修复；依赖扫描**仍然不阻断 CI**。
5. **浏览器下界**：Vite 8 默认 `build.target = baseline-widely-available`
   （源码实测展开为 `chrome111 / edge111 / firefox114 / safari16.4 / ios16.4`），
   而产物语法扫描显示实际只用到 Chrome 85 级别的语法；`tsconfig.target` 与
   `engines.node` **均未改动**。

### 尚未做（本轮仍未完成，均带 2026-09-24 实测依据）

| 优先级 | 事项 | 为什么还没做 / 实测依据 |
|---|---|---|
| 高 | **个人数据仍在 git 历史里（未重写）** | `57e5058` 只清了 HEAD 树：`git log -S"劳动合同书" -- backend` 仍命中 **6 个提交**（`b5f7d38` … `57e5058`）。重写历史会改变全部 commit hash 并需要 force push —— **不可逆，须你拍板**（§2.17） |
| 中 | **覆盖率阈值** | `ci.yml` 里 `coverage` / `--cov` 命中 **0**，`backend/pytest.ini` 与根 `pyproject.toml` 也没有覆盖率配置 —— 门禁只有"过/不过"，没有下限 |
| 中 | **Windows CI job** | YAML 解析实测：`ci.yml` 的 4 个 job **全部** `runs-on: ubuntu-latest`，全文只有一处 `windows`（一句注释）。而本仓库的主要使用环境恰恰是 Windows |
| 中 | **容器化仍未真机验证** | README §关于容器化自述"从未构建或运行过"；CI 里那个叫 `Docker build context sanity` 的 job（`ci.yml:482`）只有 **4 条 grep 断言**（nginx `client_max_body_size` / `proxy_buffering off` / `frontend/.dockerignore` / `Dockerfile` 用锁文件），**没有任何 `docker build` 步骤** |
| 中 | **锁文件（处置依赖漂移）** | 7.2 只把结论入档，**没有改变依赖形态**：后端仍无 lock 文件，`requirements.txt` 仍用 `~=`（`docs/security-scan.md:573-588`） |
| 中 | **直传补 `check_archive`**（§2.21 B-2） | 2026-09-24 复核：`check_archive` 只在 `/upload/prepare` 路径上（`upload.py:648` 起，调用点 `:729`）；直传 `POST /api/upload`（`:552-644`）做了魔术字节与 `.md` 嗅探，**仍未调用**压缩炸弹检查 |
| 中 | **分页统一**（§2.21 B-11） | 三套约定仍在：`understanding.py:321/362`（`999 / le=9999`）、`knowledge.py:198`（`20 / le=100`）、`notes/list_detail.py:67`（`20 / le=1000`）、`graph.py:93`（`limit` 无上界校验） |
| 中 | **GitHub 仓库设置**（topics / description / Discussions / 首个 release 的页面动作） | 只能在网页上点，本地改不了（§4.10）。2026-09-24 第二轮复核：**topics 仍为空**（首页 `topic-tag` 0 命中）；`/releases` 页面实测文案 **"There aren't any releases here"**，而 tag 已存在 ⇒ **tag ≠ release**，页面上的 release 对象确实还没建 |
| 低 | **剩下 5 个 `dependabot/*` 分支还没删** | 2026-09-24 第二轮实测：分支总数 6（`main` + 5 个 `dependabot/*`）。清理机制已就绪（`302740b` 的 workflow，默认 dry-run），但**首次必须先手工跑一次 dry-run 看名单**再授权删除 |
| 中 | **Dependabot 的 11 个开放 PR 需要网页上关闭** | 2026-09-24 实测：`git push` **不会**让 Dependabot 关闭它的 PR（目标版本已达成也不会）。其中 **9 个（`#17`–`#25`）已在 `0cabe4c` 里按"声明 = 实装"处理完毕**，只是 PR 还开着；剩余 `#10`（alembic，刻意不动）、`#16`（setup-node 4→7）、`#18`/`#19`/`#21` 的目标版本比实装新（`sqlalchemy 2.0.54` / `pypdfium2 5.13.0` / `openai 3.16.2`）需单独判断。**预期 Dependabot 下一个周一（09:00 Asia/Shanghai）重扫时会自行关闭已满足的那些**；要立刻清掉需要在网页操作或提供带 `repo` 权限的 PAT |
| 低 | **`ruff format` 全量重排**（**211 / 251** 个文件：app 132、tests 79、scripts 40） | **有意推迟**：理由写在 `ci.yml:131-147` —— 纯格式提交要单独开一轮，否则审阅者分不清"真修复"与"排版"；本轮只堵住新增漂移（prettier 侧已升级为阻断） |

> ⚠️ 推送时发现本机两个环境事实（与仓库无关，但会挡住你以后推送）：
> ① `git` 走 HTTPS 时**无法完成证书校验**（Schannel 无凭据 / OpenSSL 报未知 CA），
> 需要 `-c http.sslVerify=false` 才能推；
> ② Git Credential Manager **在受限沙箱下起不来**（`couldn't create signal pipe`），
> 必须放开该限制它才能取到已保存的凭据。
> 也就是说：**"证书校验 + 凭据助手"这两件事同时正常时，推送才不需要绕路。**

---

## 0. 一句话结论

**代码侧的工程质量远超它的"开源门面"。**
后端 43,851 行 / 前端 39,236 行 / 测试 28,246 行，CI 有 4 个 job（backend / frontend /
security-scan / docker-nginx-config，2026-09-24 用 YAML 解析复核）、契约漂移检查、
axe 可访问性、依赖扫描 —— 这些是很多千星级项目都没有的。
但仓库对外的部分基本是**空白**：GitHub 社区健康度 **42%**，
无 CONTRIBUTING / 模板 / CHANGELOG / release / topics，
README 首页有**可验证的错误陈述**，且**没有任何截图**。
用一句话概括差距：

> **它现在是一份"很硬的私人工程日志"，还不是一份"对外交付物"。**

⚠️ 而且核查中发现一条**必须先处理**的：本机实例正在用**仓库里公开的默认 JWT 密钥**签名
（§2.1）—— 这不是"门面问题"，是安全问题，处置顺序排在所有条目之前。

---

## 1. 现状核查（先对齐事实，再谈缺点）

### 1.1 与 GitHub 最新版的一致性（你要求的"copy 一份对比"）

| 项 | 结果 |
|---|---|
| 远端 `main` 的 HEAD | `7ee732bf84c8fd6653efae61d92b2923f7f417ab` |
| 本地 HEAD | `7ee732bf84c8fd6653efae61d92b2923f7f417ab`（**同一个提交**） |
| 本地工作区 vs HEAD | `git diff HEAD` **为空**（无未提交改动） |
| 快照 vs 本地（571 个跟踪文件） | 只有 CRLF/LF 差异（`.gitattributes` 的 `text` 规则 + Windows 检出），**内容差异 0** |
| 远端分支 / 标签 / release | `main` 一个分支；**tag 0 个、release 0 个** |
| 远端 issue / PR | **各 0 个**（仓库刚公开，尚无外部互动） |

**结论**：不存在"本地有、GitHub 没有"的代码漂移 —— 你关心的"本地 vs 最新版"这件事本身是干净的。
真正的差距全部集中在**仓库对外呈现层**。

### 1.2 GitHub 侧客观指标（API 实测）

| 指标 | 值 | 含义 |
|---|---|---|
| `community/profile.health_percentage` | **42%** | GitHub 自己的社区完备度评分 |
| 已具备的社区文件 | 只有 `README.md` + `LICENSE` | `contributing` / `issue_template` / `pull_request_template` / `code_of_conduct` **全为 null** |
| `topics` | **[] 空** | 搜索发现度 ≈ 0 |
| `description` | 有（中文一行） | ✅ |
| `homepage` | **null** | 无演示站 / 文档站 |
| stars / forks / watchers | 0 / 0 / 0 | 与 topics 空、无 release 相互印证 |
| `size` | 6,345 KB | 仓库体积健康（2GB 模型与真实数据确实没进库） |
| 语言构成 | Python 3.36 MB / TS 1.69 MB / JS 0.35 MB / CSS 0.19 MB | 前端占比合理 |
| CI 运行 | 44 次；最近一次 run #33（2026-09-20，`dynamic`）**success** | 最后一次 push 触发的 run #11 也是 success |
| `documentation` 字段 | `.../tree/**master**/docs` | ⚠️ **分支名是 main，这个链接指错了** |

### 1.3 本机未入库的工作产物（决定"哪些该进库、哪些不能进"）

| 路径 | 规模 | 现状 | 判断 |
|---|---|---|---|
| `.trae/documents/`（52 个 md） | 10,807 行 | 未入库（`.gitignore:155`） | **不该整批进库**，但里面有对贡献者有用的部分 |
| `_backup/`（8 个快照） | — | 未入库（`.gitignore:58`） | 保持不入库 ✅ |
| `参赛/EngramNote*.html`（2 个静态 mock）+ 3 张 PNG | **已入库** | 与 README 无任何互链 | ⚠️ 见 §2.1 |
| `参赛/参赛贴文.md` | 14,780 B | 未入库（`.gitignore:45`） | 与已入库的 HTML 同属一个目录，**规则自相矛盾** |
| `backend/.env`（含真实密钥） | — | 未入库（`.gitignore:26`） | ✅ 正确 |
| `notes/`（真实学习笔记）、`resource/`、`testfiles/` | — | 未入库 | ✅ 正确（隐私） |
| `AGENTS.md`（DSH 会话规矩） | 2,543 B | 未入库 | 判断见 §4.3 |

## 2. 必须修（公开仓库的硬伤 —— 每条都能被访客当场看到）

> 判定标准：**一个陌生访客在不克隆、只看首页/文档的 5 分钟内就会踩到**，
> 或者**会直接损害项目可信度**。§2.1 单独置顶，因为它不是"门面问题"而是安全问题。

### 2.1 🔴 最紧急：本机实例正在用**已公开**的 JWT 密钥签名

> 这条**必须先处理**，其余条目都可以排队。

| 事实 | 证据 |
|---|---|
| 本机 `backend/.env` 的 `JWT_SECRET_KEY` 值 = `engramnote-dev-secret-change-in-production`（42 字符，占位符） | `backend/.env:23`（实测逐字符相等） |
| **同一个字符串逐字符出现在已跟踪文件里**（两处） | `docs/archive/新手教学.md:238`（旧版 `config.py` 的硬编码默认值）、`docs/archive/新手教学.md:3454`（"照这样填 `.env`"的教学行） |
| 该字符串**在 git 历史里也有** | `git log -S` 命中 `b5f7d38`、`a037df5`、`19b957d` 三个提交 |
| 令牌参数放大了后果 | HS256（`backend/app/config.py:98`）+ 访问令牌 24 小时（`:106`）+ **无状态、登出不可撤销**（`:101-105` 自己写明"已泄露的访问令牌仍可用"） |
| 启动校验**只查"是否为空"，不查"是否为已知占位值"** | `backend/app/config.py:564-570` |
| 而 `security-scan.md` 记录的 trivy `secret` 扫描是 **0 命中** —— 它认不出"占位符当密钥用" | `docs/security-scan.md:370` |
| 附带：一枚**历史上真实签发**的 JWT（`sub=15a92671-7123-491b-9733-54e59063e6fd`）被提交入库（已过期，但泄露了 user id 形态） | `backend/tests/integration/test_final_verify.py:7`、`test_key_issues.py:8` |

**为什么这对"开源"是致命的**：仓库里那份 `新手教学.md` **就是在教人填这个值**。
换句话说 —— **任何照着教程部署的自托管实例，密钥都是公开的**，
而访问令牌 24 小时不可撤销。这不是"开发环境的坏习惯"，是**分发给陌生人的默认凭据**。

**处置顺序（建议）**：
1. **立刻轮换本机密钥**（作废全部会话）：`python -c "import secrets;print(secrets.token_hex(32))"` 写进 `backend/.env`；
2. 修掉 `docs/archive/新手教学.md:3454` 的示范值（改成 `你的随机密钥` + 生成命令），
   `:238` 那处标注为"历史默认值，已废弃"；
3. `config.py:564` 的校验**扩展为黑名单**：`jwt_secret_key in {"engramnote-dev-secret-change-in-production", "changeme", ...}` 时拒绝启动（prod）/自动重生成（dev）；
4. 删掉 `backend/tests/integration/*.py` 里那枚过期 JWT（换成从环境变量读）；
5. CI 的 secret 扫描补一条**已知占位值基线**（现在的 trivy 扫不出来）。

> ⚠️ 第 1 步是**改配置**、第 2/4 步是**改已跟踪文件（会进历史）** ——
> 按项目规矩，这些属于"改产品语义/删文件"级别，**请先确认再动手**。

---

### 2.2 README 首页有可验证的错误陈述（最该先修）

这是最伤的一条：**README 是唯一门面，而它说的功能与代码不符。**

| # | README 说的是 | 代码事实 | 证据 |
|---|---|---|---|
| R-1 | "**SM-2 算法**调度复习"（`README.md:49`） | 实际调度器是 **FSRS-5**，SM-2 只是可回退路径 | `backend/app/services/fsrs_service.py:16`、`scheduler_service` 被 `review_service.py:375` 调用（注释写明"阶段 3.6：默认 FSRS-5，`config.review_scheduler` 可回退 SM-2"） |
| R-2 | 项目结构里列 `sm2_service.py`（`README.md:335`） | **该文件不存在**（`app/services/` 下只有 `fsrs_service.py` + `scheduler_service.py`） | 目录实测 |
| R-3 | 核心流程图标"**三路**混合检索"（`README.md:379`） | 同一份 README 的 `:65-68` 已写明 **n-gram 通道已删除、现在是两路** —— **文档自己跟自己矛盾** | `README.md:48` vs `README.md:379` |
| R-4 | 文档入口推荐 `docs/architecture.md` 为"**首选入口**，随代码更新"（`README.md:587`） | 该文件开头自述"**本文不再是活的架构文档**…写作于 2025-06…当前唯一依据是 overhaul-plan" | `docs/architecture.md:3-20` |
| R-5 | 项目结构列出根目录 `.env.example`（`README.md:365`） | 根目录**没有**这个文件，只有 `backend/.env.example` | `git ls-files` 实测 |
| R-6 | `git clone https://github.com/**你的用户名**/EngramNote.git`（`README.md:156`） | 占位符没填，访客直接复制会失败 | `README.md:156` |

**建议改法**：把 README 拆成「门面 README（短、准、带图）」+「docs/ 深水区」两层；
把 R-1~R-6 逐条改成代码事实；R-4 的"首选入口"改为 `docs/overhaul-plan.md`
（或新建一份真正活的 `docs/architecture.md`，即计划里的阶段 7）。

### 2.3 首页没有任何视觉证据（0 张截图）

- README 里**一张图都没有**（只有 5 个 shields 徽章，`README.md:5-9`）。
- 而 `参赛/` 里**已经躺着 3 张可用截图**（`仪表盘.png` 118 KB、`对比页面.png` 220 KB、
  `知识图谱.png` 508 KB）和 2 个静态 HTML mock —— **已入库却没有任何文档引用它们**。
- 同一个仓库里还堆着 1.7 MB / 21,077 行的文档（`docs/`），却没有一段能让访客
  在 10 秒内明白"这东西长什么样、解决什么问题"。

**建议改法**：README 顶部放 3 张截图（或一张 GIF：上传 → 清洗对比 → 图谱 → 问答）；
`参赛/` 改名 `assets/` 或 `docs/media/` 并在 README 引用，同时把"静态 mock"标注清楚，
避免访客把 mock 当成真实产品截图。

### 2.4 仓库命名与内容里的"参赛"残留

- 已入库路径 `参赛/`（`参赛/EngramNote.html` 等 5 个文件）是**中文目录名 +
  赛事语境**；`EngramNote.html` 的 `<title>` 里写着
  "AI 学习笔记管理与知识库及答疑 | **TRAE AI 创造力大赛**"。
- 而 README **一次都没提**这个目录。
- `.gitignore:39-46` 对同一目录的规则是**自相矛盾**的：`EngramNote.html`、`EngramNote-demo.html`、
  3 张 PNG 已入库，`参赛贴文.md`/`EngranmNote--Demo.html` 被忽略。

**建议改法**：把有用素材搬到 `docs/media/`（英文路径）并引用；赛事残留要么进
`docs/archive/`，要么删除（**删除属破坏性操作，需你确认**）。

### 2.5 依赖可复现性：Python 侧等于没有锁（供应链硬伤）

| 侧 | 现状 | 证据 |
|---|---|---|
| 后端 | `requirements.txt` 用 `~=` 锁**主次版本**，**无 lock 文件** | `backend/requirements.txt:1-6` 自陈"描述的是**全新安装**会得到什么，不等于本机/CI 已装好的那套" |
| 后果（已被你自己量化） | 全新安装会解析到 `starlette 0.46.2 / pydantic 2.0.3 / python-jose 3.3.0`，而**本机与 CI 实际跑的是 `1.6.0 / 2.13.5 / 3.5.0`** | `docs/security-scan.md` §4.1 |
| 前端 | `package-lock.json` + `npm ci`，且 **CI 有守卫强制 `RUN npm ci`** | `.github/workflows/ci.yml:464-479` |
| 后端 Docker | `RUN pip install --no-cache-dir -r requirements.txt`（**无锁文件可依**） | `backend/Dockerfile` |

**也就是说：前端被要求可复现，后端不被要求。** 这不是"风格问题"——
它意味着**任何人 `pip install -r requirements.txt` 之后跑的都不是你测过的那棵树**，
而 `docs/security-scan.md` 的结论也是建立在一条"并不存在的部署"上的。

**建议改法**（三选一，按成本排序）：
1. `requirements.txt` 改成 `pip freeze` 的精确 `==`（最省事，但升级要手动）；
2. 引入 `uv.lock` / `pip-tools` 生成 `requirements.lock.txt`，CI 与 Docker 都只用锁文件；
3. `pyproject.toml` + `uv`/`hatch`，顺带解决 §2.5。
> ⚠️ 这属于"依赖或接口契约有改动"，按仓库规矩要跑**漂移检查**。

### 2.6 没有任何"可安装 / 可分发"形态

- **无** `pyproject.toml`、**无** `setup.py` → 后端**不能** `pip install`，
  也不能发 PyPI，更不能被别的项目当库引用。
- 版本号**只有两处、互不关联**：`frontend/package.json:4` 的 `"version": "0.1.0"`
  与 `backend/app/main.py:573` 硬编码的 `version="0.1.0"`；**没有**单一版本来源，
  **没有** git tag，**没有** CHANGELOG。
- 结果：访客无法回答"我装的是哪个版本、和上次比改了什么"。

**建议改法**：建立单一版本源（例如 `backend/app/version.py`，前端构建时注入），
打第一个 tag（`v0.1.0`）并生成 release note；`docs/overhaul-plan.md` 的
119 轮记录本身就是最好的 changelog 素材，只是**需要被摘要成对外可读的形态**。

### 2.7 许可证署名的法律细节

`LICENSE:3` 写的是 `Copyright (c) 2026 EngramNote Contributors`，
但 150 个提交**只有一位作者**（`trx-0833 <3162323563@qq.com>`），
且仓库里**没有** `AUTHORS` / `CONTRIBUTORS` 文件。

**影响**：MIT 的署名行需要指向真实的著作权人；"Contributors"在没有贡献者的情况下
是一个空指向，将来真有人提 PR 时权属也不清晰。
**建议改法**：改成真实署名（个人或组织名），并决定是保留"个人项目"还是走
"需要 CLA/DCO"的路子（见 §4）。

### 2.8 公开前的安全检查项（未跟踪文件与 `.gitignore` 的编码风险）

- `backend/.env` 存在于本机且**含真实配置**（1,572 字节），已被 `.gitignore:26` 忽略 ✅
- 我做的密钥扫描：**已跟踪文件里没有真实凭据**（`sk-` / `ghp_` / `AKIA` / 私钥块 **0 命中**；
  `JWT_SECRET_KEY` 的命中只是一个**占位符字符串**，但它引出了 §2.0 那条 P0）。
  唯一的"疑似"是 `backend/tests/测试账号信息.md`（**已公开**，内容是**本地**
  e2e 弱口令 `e2e…@example.com` / `Test@123456`，账号只存在于被忽略的 SQLite 里）——
  你在 `docs/overhaul-plan.md:11299` 已经自行判定为低风险，**我同意**：
  它是测试夹具，不是凭据；要撤只能改写历史，代价大于收益。
- ⚠️ 但要提醒一句：`*.env` / `*.log` / `backend/control/` 这些规则**只在 `.gitignore` 里**，
  而 `.gitignore` 的**中文注释在本机显示为乱码**（文件本身是合法 UTF-8，
  但写作时用了 GBK 语义的中文，编辑器读出来是"鏈湰椤圭洰"这种形态）——
  一旦有人用错误编码**重写**这个文件，规则本身可能被破坏。
  **建议**：给 `.gitignore` / `.gitattributes` 补 `.editorconfig`（`charset = utf-8`），
  并把中文注释改成英文或一次性修正编码。

### 2.9 前端包元数据缺三样，且锁文件钉在淘宝镜像

| # | 事实 | 证据 |
|---|---|---|
| F-1 | `frontend/package.json` **无 `license` / `repository` / `description` / `author`**（只有 name/private/version/type） | `frontend/package.json:1-5` |
| F-2 | **371 / 371 条 `resolved` 全部指向 `registry.npmmirror.com`**（integrity 齐全，lockfileVersion 3） | `frontend/package-lock.json:4`、`:49`、`:56`…（实测统计） |
| F-3 | `Dockerfile` **强制**写死该镜像：`RUN npm config set registry https://registry.npmmirror.com` | `frontend/Dockerfile:14` |
| F-4 | 仓库内**没有任何 `.npmrc`** | 实测 |

**影响**：`npm ci` 会**照锁文件从第三方镜像**拉全部依赖 —— 海外/企业网络下是**直接失败，不会回落到官方源**；
供应链审计工具会把"锁文件钉非官方 registry"当红旗。
而 `Dockerfile:19-25` 与 `ci.yml:167-172` 又宣称"锁文件保证可复现" ——
**这份可复现性把某个镜像站的可用性也算进去了**。

**建议改法**：用官方源重生成锁文件（`npm install --package-lock-only --registry=https://registry.npmjs.org`），
校验 `git diff` **只改 `resolved` 主机名**；镜像加速降级为可选项写进文档，别写进 `Dockerfile`。
同时补 `"license": "MIT"` 与 `"repository"`（否则 SBOM / dependabot 读不到前端包的许可证，
与根目录已入库的 MIT 自相矛盾）。

### 2.10 前端无法指向"非本机后端"，也没有 `frontend/.env.example`

- 客户端基址**硬编码**：`frontend/src/api/client.ts:11` `export const API_BASE = '/api'`；
  `import.meta.env` 在 `src/**` **0 命中**。
- 开发代理可被 `VITE_API_TARGET` 覆盖（`frontend/vite.config.ts:34`），
  但**全仓库只有 `frontend/docs/e2e.md:337` 提过一次**，README 无记载。
- 生产代理写死容器名：`frontend/nginx.conf:38` `proxy_pass http://backend:8000/api/`。
- `frontend/` 下**没有** `.env.example`（而根 `.gitignore:24` 已用 `!.env.example` 留好口子）。

**影响**：把 `dist/` 部署到静态托管、后端在另一台机器 —— 访客**只能改源码或改 nginx 重建**，
且没有任何文档告诉他该改哪里。**这是"能装上但用不起来"的典型形态。**

**建议改法**：`API_BASE` 改读 `import.meta.env.VITE_API_BASE_URL ?? '/api'`；
新增 `frontend/.env.example`（`VITE_API_TARGET` + `VITE_API_BASE_URL`）；README 加"后端不在本机"一节。

### 2.11 a11y 的**前置条件其实已经满足**，却还挂着"建议性"

- 步骤名自带 "(axe-core, **advisory**)"，并 `continue-on-error: true`：`ci.yml:263`、`:288`。
- 但门槛本身很硬，而且**已经清零**：`frontend/e2e/a11y.spec.ts:269` 的
  `const REGISTRY: RegisteredRule[] = []` 是**空表**；`:903-905` 断言"未登记违规 / 影响面扩大 / 严重度上升"；
  `:1882-1890` 有"注入必然违规元素"的自检（防假绿）；`:888` 还断言 DOM 节点数下限（防空页恒 0 违规）。
  文档自述 **26 passed、违规 0 组 / 0 个节点**（`frontend/docs/a11y-audit.md:6-7`）。
- 而 `ci.yml:279-283` 写明的升级前置是"清空 REGISTRY + CSS 迁移收尾"，
  后者已在 `frontend/docs/css-migration-plan.md:16` 标记"**5.6 至此完成**"。

**建议改法**：**直接把 `continue-on-error` 改成 `false`** —— 这是当前仓库里
"最便宜的一次门禁升级"（一行改动，换来一条真门禁）。
⚠️ 注意 `frontend/docs/a11y-audit.md:111-116` 那句"当前状态：违规 12 组 / 15 个节点"
是**过期指针**，与同文件 `:6-7` 矛盾，顺手修掉。

### 2.12 门禁覆盖面比它看起来窄（前端）

| 事实 | 证据 |
|---|---|
| `"lint": "eslint src/"` —— **e2e/ 与 scripts/ 不在 lint 范围**（含 103 KB 的 `e2e/a11y.spec.ts`、7 个 `scripts/*.mjs` 约 300 KB） | `frontend/package.json:10`、`eslint.config.js:10` |
| `prettier --check src/` 同样只管 `src/`，且 CI 里还是建议性 | `frontend/package.json:23`、`ci.yml:204-206` |
| 但 `tsconfig.json:20` 又把 `e2e` **纳入 tsc** —— "类型检查覆盖 e2e、lint 不覆盖"，两边口径不一致 | `frontend/tsconfig.json:20` |
| `npm run gen:api:drift`（契约漂移报告）**根本不在 CI 里** —— CI 只守"生成器幂等" | `frontend/package.json:25`、`ci.yml:188-202` |

**建议**：lint/format 范围扩到 `src e2e scripts`；把 `gen:api:drift` 接进 CI（哪怕是建议性），
让"契约漂移"这件事从人工工具变成机器可见。

### 2.13 生产依赖装不全：`requirements.txt` 里没有 PDF 路径的**模块级硬依赖**

| 事实 | 证据 |
|---|---|
| `requirements.txt` 的完整清单里**没有** `pypdfium2`、`Pillow`、`PyMuPDF` | `backend/requirements.txt`（19 个包，逐个核对） |
| 但 `pypdfium2` / `Pillow` 在 `intake.py` 里是**模块顶层无条件 import** | `backend/app/services/mineru/intake.py:22-23` |
| 它们只被登记在**测试**依赖里 —— 而该文件自己写明"缺任何一个都会让所有 `import mineru.converter` 的测试失败，所以必须显式列出" | `backend/requirements-test.txt:63-66`（注释原文） |
| `fitz`（PyMuPDF）被 6 处 `import` 使用（按页裁剪 PDF 功能） | `backend/app/services/pdf_crop.py:43`、`intake.py:221/236/335` 等 |
| `requirements.txt` 里还**混着测试依赖**（`pytest` / `pytest-asyncio`） | `backend/requirements.txt:20-21` |

**影响**：陌生访客按 README 执行 `pip install -r backend/requirements.txt` 后，
**PDF 上传路径会直接 `ModuleNotFoundError`**（而 PDF 是这个产品最主要的入口）。
`requirements-test.txt` 是"CI 能过"的清单，**不是"用户能跑"的清单** —— 两者被混为一谈。

**建议改法**：把 `requirements.txt` 拆成
`requirements.txt`（运行必需，含 `pypdfium2` / `Pillow` / `PyMuPDF`）+
`requirements-dev.txt`（`pytest` 等），并让 `check_env.py` 的检测清单与之一致。
**顺带三处"数不上"的声明**（都属"声明与事实不一致"，本仓库自己最在意的毛病）：
`README.md:173` 说"检测 17 个包"，`check_env.py:387` 的 `REQUIRED_PACKAGES` 实际 **16 项**；
`requirements.txt` 非注释行 **19** 行（含 2 个测试依赖、不含 3 个 PDF 依赖）。

### 2.14 `.env.example` 曾只登记不到六成的配置项 ✅ **已修**（见 §执行进度 批次 2.3）

> **2026-09-24 复核**：本条已在本轮修完 —— `.env.example` 与 `Settings` 现已 **103 / 103**
> 对齐（`python backend/scripts/gen_env_example.py --check` → 缺失 0 / 未知 0，退出码 0），
> 并有**双向守卫**（缺一个报错、多一个也报错）与 CI 步骤。
> **下面几行保留为"问题形态"的原始记录**（数字是核查当日的）。

- `Settings` 里共 **102 个字段**，`.env.example` 里出现过的变量名 **60 个**，
  **42 个字段完全没登记** —— 其中包含**会改变产品行为**的那些：
  `review_scheduler`（默认 `"fsrs"`，决定用 FSRS 还是回退 SM-2）、
  `daily_review_limit`、`fsrs_request_retention`、`rag_rrf_k` / `rag_rrf_bm25_weight`、
  `llm_daily_token_quota` / `llm_daily_cost_quota`、`max_storage_per_user_mb`、
  `bcrypt_rounds`、`backup_keep` …
- 对照证据：`backend/app/config.py`（102 个字段）vs `backend/.env.example`（60 个变量名，实测计数）。
- **影响**：访客**看不出**这个系统有配额、有调度器切换、有缓存与备份保留策略 ——
  而这些正是"能不能长期用"的关键旋钮。你在别处（如 §2.5 的 SM-2/FSRS）已经吃过
  "README 与代码不一致"的亏，这里是同一类问题的另一种形态。

### 2.15 Alembic 迁移链**在一个空库上第一步就会失败**（升级路径不存在）

- `alembic/versions/001_add_note_role_to_notes.py:16` 的 `down_revision = None`，
  而 `:23` 直接 `op.add_column('notes', ...)` —— **假设 `notes` 表已存在**。
- 也就是说：**"全新安装走 Alembic" 这条链从来不是可用路径**；真实建表靠
  `app/database.py` 的 `init_db()` + `_migrate_sqlite()`（README `:90-96` 已如实说明）。
- 对开源项目的含义：**没有"从 v0.1 升到 v0.2"的官方路径**，
  用户只能"换代码 + 让启动时的 `create_all` 补列"。这必须写进 `UPGRADING.md`，
  或者干脆把 `alembic/` 从公开仓库移除（留着会让人以为能用）。

---

### 2.16 反代部署下限流会**把所有用户算成一个人**

- 限流键取自直连对端：`backend/app/middleware/rate_limit.py:126`
  `client = request.client.host if request.client else "unknown"` —— **不读 `X-Forwarded-For`**。
- 容器化 / Nginx 反代（仓库自带的 `frontend/nginx.conf:38` 就是这么部署的）之后，
  **所有请求的 `client.host` 都是反代容器 IP** → 全站共用一个限流桶。
- 更糟的是 `main.py:629-634` 自己记着"RateLimit 置于最外层、`context.get_user_id()` 读不到，
  **按用户计数那条分支从未生效**"；而 `frontend/nginx.conf:41` 明明设置了 XFF，
  `Dockerfile` 与 `start.bat:161` 启动 uvicorn 却都没有 `--proxy-headers`。

**影响**：`rate_limit.py:61-62` 的登录 10/min、注册 5/min 在反代后变成**全站共享 10 次/分钟** ——
一个脚本就能让所有用户无法登录，爆破防护也失去区分度。
**建议**：显式可信代理配置（`TRUSTED_PROXIES` + 取 `X-Forwarded-For` 的**最右侧非可信跳**），
并把 RateLimit 移到能读到用户上下文的位置；补一条"两个 IP 各自计数"的测试。

### 2.17 🔴 已入库文件里有**真实身份信息与真实文件路径**

| 事实 | 证据 |
|---|---|
| 一位**真实姓名**出现在**已跟踪**文件里（10+ 处，含拟人化提问示例） | `backend/scripts/dev/test_clean_failed_quick.py:18`、`test_cleaning_pipeline.py:32`、`test_e2e.py:37`、`test_pdf_pipeline.py:27`；`backend/tests/integration/test_final_verify.py:32`（"田润鑫的工作地点在哪里？"）、`test_key_issues.py:52`（"田润鑫的工资是多少？"） |
| 一份**真实个人文档的文件名**（劳动合同）出现在 12 个已跟踪文件里 | `backend/tests/test_full_flow.py:27`、`test_week1_2_fixes.py:37`、`test_week5_6_integration.py:27`、`test_week8_review.py:27`、`test_week11_e2e.py:4`、`test_week12_e2e.py:4`、`backend/scripts/dev/test_e2e.py:37` …（`git grep -l "劳动合同书"` 共 12 个文件） |
| 维护者本机路径与用户名 | `docs/security-scan.md`、`docs/overhaul-plan.md` 里的 `C:\Users\admin\...`；`backend/tests/test_week11_e2e.py:22`、`test_week12_e2e.py:23` 里的 `C:\Users\admin\anaconda3\envs\mineru_env\python.exe` |
| 本机库的行数画像 | `backend/scripts/_dryrun_fixture_users.json:134-160` |

**为什么这条必须前置处理**：这不是"代码质量"问题 ——
**劳动合同是有法律效力的文件，而它的持有者姓名、文件名与提问内容被公开**，
且出现在**任何人可搜到**的文本里（GitHub 代码搜索 / Google 均可命中）。
更麻烦的是它**已经进了历史**：只删当前文件不解决问题。

**建议**（按代价升序）：
1. 先把 HEAD 里的这 12 个文件改成通用占位（`TEST_PDF_PATH` 必填、`张三`/`李四`）；
2. 历史清理：`git filter-repo --replace-text` 重写全历史（**改变所有 commit hash + 需要 force push，不可逆**）；
3. 若你判定"个人自用资料、可接受"，那**至少**在 README 明确声明，
   别让它以"没人注意到"的方式存在。

> ⚠️ 第 1 步能自查自改；**第 2 步属于不可逆操作，必须你拍板**。

### 2.18 🔴 跑一次 `pytest` 会**写真实的存储目录**（对贡献者是真风险）

- `backend/tests/test_purge_file_consistency.py:105-132` 是一个 **autouse fixture**：
  它真实调用 `storage_service` 写 `backend/data/storage/`（`:125` mkdir），
  跑完按"**运行期间新增的顶层目录**"逐个 `shutil.rmtree`（`:130-132`）。
- 它**自己的 docstring**（`:109-115`）就承认这个设计有多危险：
  "第一版跑一轮就留下 26 个随机 user_id 目录…**真实用户目录不可再生，误删就是数据损失**"。
- 仓库里**有正确写法**可参照：`backend/tests/test_vault_audit.py:100-102`
  把 `VAULT_DIR` 重定向到 tmp 并重绑 `storage_service.settings`。

**为什么对"开源"是硬伤**：CI 与 README 都让人跑 `pytest`。
一个**带着自己真实笔记**来试用/贡献的人，跑一次测试就可能删掉自己的数据 ——
这正是 `AGENTS.md` 里那句"历史上出现过清理副本却删掉真身"的同类风险。
**建议**：按 `test_vault_audit.py` 的写法改成 tmp 重定向 —— **这是本次核查里最该先修的测试**。

### 2.19 抄 `.env.example` 会让测试变红，而 README 让人抄它

- `backend/tests/test_week5_6_understanding.py:382-399`：当 `.env` **存在**但没有 `GLM_MODEL=` 时
  直接 `pytest.fail(...)`（`:399`，另 `:401` 有一行重复死代码）。
- 而 `.env.example` 里的 `GLM_MODEL` 恰好是**注释掉的**。
- 结果：新贡献者"复制模板 → 跑 pytest"**必然红**，且报错指向**环境**而不是代码 ——
  这正是本仓库在 CI 上花三轮才分离清楚的那类坑。

### 2.20 `tests/` 里曾有 9 个"零用例脚本"，会被 pytest 导入 ✅ **已搬出**（见 §执行进度 批次 5）

> **2026-09-24 复核**：这 9 个脚本已全部移入 `backend/scripts/dev/`（`3c631f9`），
> 现在 `backend/tests/` 下是 **0 个**（逐文件名核对），`backend/scripts/dev/` 下 **9 个**；
> `pytest.ini` 的 `testpaths = tests` 自然也不再收集它们。
> **下面几行是核查当日的形态**，保留作为"为什么必须有搬迁 + 守卫"的依据。

- 这 9 个文件 **0 个 `def test_`**，却在模块级创建 `httpx.Client` 指向 `localhost:8001`，
  其中 `test_week8_e2e.py:43/139` 还直接 `sqlite3.connect("data/db/engramnote.db")` 插数据：
  `test_full_e2e` / `test_full_flow` / `test_week5_6_integration` / `test_week8_e2e` /
  `test_week8_review` / `test_week8_review_existing` / `test_week9_10_e2e` /
  `test_week11_e2e` / `test_week12_e2e`。
- 彼时它们**会被 import**：收集期就会建目录（`test_full_e2e.py:44` 建 `backend/tests/results/`）。
- 另一个隐患**仍未处理**：`tests/conftest.py:269` 的 `_LOCAL_HOSTS` 把 `localhost/127.0.0.1` 白名单化，
  `:272` 依赖 httpx **私有属性** `_transport`，异常还被 `:298-299` 吞掉 ⇒
  **"离线守卫"可能静默失效**（守卫失效时测试仍显示通过）。

**建议（1/2 已做）**：这 9 个脚本移入 `backend/scripts/dev/`（✅ 已做，该目录现有 24 个同类脚本
与一份 README，搬迁时改了什么见 `backend/scripts/dev/README.md` 的"第二批"）；
**守卫改成"装不上就报失败"而不是吞掉 —— 这一半仍未做**。

### 2.21 其他后端硬伤（逐条带证据，可独立处置）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| B-1 | ~~**JWT 算法可由环境变量改写**，无白名单~~ ✅ **已修**（2026-09-24：`config.py:672` 起给 `jwt_algorithm` 加了白名单 `field_validator`） | `config.py:98` → `auth_service.py:163/209/280`、`request_context.py:76` 的 `algorithms=[settings.jwt_algorithm]` | 误配 `none` 即接受**无签名令牌**（`overhaul-plan.md:2359-2361` 自述"待实机验证"）→ 应为常量白名单 |
| B-2 | **直传绕过压缩炸弹检查** ❌ **仍未做**（2026-09-24 复核） | `POST /api/upload`（`api/upload.py:522-614`）不调用 `check_archive`；只有 `/upload/prepare`（`:695-705`）调用 | `config.py:165-172` 的三条护栏在直传路径上**失效** |
| B-3 | ~~**上传临时区不校验归属**~~ ✅ **已修**（2026-09-24：`upload.py:98-120` 的 owner 旁载文件 + `:843-850` 在**任何**枚举/读取之前校验归属） | `upload.py:806-808` 只验 UUID 正则，不验 temp_id 属于谁 | 越权取用他人暂存文件 |
| B-4 | **`alembic.ini` 带着弱口令与错的库** ❌ **仍未做**（2026-09-24 复核） | `backend/alembic.ini:6` = `postgresql+asyncpg://engram:engram123@localhost:5432/engramnote` | 公开仓库里的默认口令，且与真实 SQLite 路线不符 |
| B-5 | **MinIO 默认口令** ❌ **仍未做**（2026-09-24 复核） | `config.py:75-76` `minioadmin/minioadmin`，`.env.example` 照抄 | 自托管用户照抄即弱口令 |
| B-6 | ~~**`check_env.py` 仍在要求已删除的 chromadb**~~ ✅ **已修**（2026-09-24：`REQUIRED_PACKAGES` 里已无 `chromadb`，`check_env.py:428` 留有说明注释） | `check_env.py:399`（必需清单）、`:428-431`（`--fix` 会装回来）；而 `requirements.txt:23-24`、`config.py:241-250` 都声明 Chroma 已移除 | 首次自检"**假失败 + 假通过**并存"；计数三方不一致（它 16 个 vs README 说 17 vs `requirements.txt` 19 行） |
| B-7 | ~~**`check_env.py` 的 npm 版本检查在 Linux 上永远"看似通过"**~~ ✅ **已修**（2026-09-24：改用 `shell=False` 的参数数组，全文件 `shell=True` 0 命中） | `check_env.py:369-375` 用 `subprocess.run([...], shell=True)` 且未 `check=True` | POSIX 下 `/bin/sh -c` 把 `args[0]` 当命令串、`--version` 变成 `$0` ⇒ 打印 npm 用法并**记 pass** |
| B-8 | ~~**422 响应体与 OpenAPI 声明不一致**~~ ✅ **已修**（2026-09-24：`main.py:555-586` 的 `validation_exception_handler` 返回**数组** + `VALIDATION_ERROR` 错误码） | `main.py:516-525` 返回 `{"detail": "<str>"}`，而 `openapi.json` 按 FastAPI 规范声明 `detail: List[...]`（`openapi.json:2940`、`:7167`） | 按规范生成的**第三方客户端在参数错误时全部失败**；`detail` 还回显输入 |
| B-9 | **无安全响应头** ✅ **已修**（`middleware/error_handler.py:78-79`：`nosniff` / `X-Frame-Options: DENY`，且错误响应也补头）；**无改密/找回入口** ❌ **仍未做**（`api/auth.py` 只有 register / login / refresh / logout / reminder-settings） | `main.py:638-649` 只挂 CORS + 两个自定义中间件；`api/auth.py` 无改密端点 | 口令泄露后用户**无法自助轮换** |
| B-10 | `storage_service.py:136 remove_project_dir` 是**无调用方的死代码，内部是 `shutil.rmtree`** ❌ **仍未做**（2026-09-24 复核：`git grep` 仍只命中定义） | `git grep` 仅命中定义 | 危险能力留着；要么删、要么补用户校验 |
| B-11 | 分页约定三套并存 ❌ **仍未做**（2026-09-24 复核） | `understanding.py:321/362`（999/9999）、`knowledge.py:198`（20/100）、`notes/list_detail.py:67`（1000）、`graph.py:93`（`limit` 无边界校验） | 贡献者无从判断"该照哪个写" |

> **本表逐行复核（2026-09-24，均为代码实测）**：**已修** = B-1 / B-3 / B-6 / B-7 / B-8，
> 以及 B-9 的"安全响应头"那一半（提交 `3c631f9`，批次 5 剩余）；
> **仍是缺口** = B-2（直传无压缩炸弹检查）、B-4（`alembic.ini` 弱口令）、B-5（MinIO 默认口令）、
> B-9 的后一半（无改密/找回入口）、B-10（`remove_project_dir` 死代码）、B-11（分页三套）。
> 复核命令：`git grep -n check_archive` / `git grep -n remove_project_dir` /
> `python -c "import re,io;print('shell=True 命中', len(re.findall('shell=True', io.open('check_env.py',encoding='utf-8').read())))"`。
> 注意：本表的**行号是核查当日的**，`§2` 上游改动会让它们漂移 —— 找的时候按文件名与函数名找。

### 2.22 README 与代码不一致的**完整清单**（R-1~R-6 之外的补充）

| README 说 | 代码事实 | 证据 |
|---|---|---|
| RRF "k=60"（`README.md:69`） | `rag_rrf_k = 1`、`rag_rrf_bm25_weight = 0.65` | `backend/app/config.py:234-235` |
| 每日任务上限 `DAILY_REVIEW_LIMIT=50`（`README.md:83`） | `daily_review_limit = 10` | `backend/app/config.py:398` |
| "`DEBUG=true` → GLM / `DEBUG=false` → DeepSeek"（`README.md:419`、`:574-575`） | `DEBUG` 已降级为 `APP_ENV=dev` 的等价物，**不再决定供应商**（改用 `LLM_PROVIDER`） | `config.py:509-520`、`.env.example` 说明段 |
| JWT 密钥列在"**可选配置**"（`README.md:214-218`） | `app_env` 默认 `prod`，非 dev 且密钥为空时**启动即 raise** | `config.py:491`、`:564-568` |
| 全篇 **0 处**提 `APP_ENV` / `LOG_SQL` / `LLM_PROVIDER` | 这三个才是现行开关 | `config.py:482-520` |
| `docs/architecture.md` 是"随代码更新的首选入口"（`README.md:587`） | 三个文件对"哪份是活文档"互相矛盾 | `README.md:366/587` vs `architecture.md:3-5` vs `overhaul-plan.md:7237-7238` |
| — | ~~`docs/sqlite-single-writer.md:5` 仍写"D5=保留 Chroma"、`:51` 示例端口 8000（实际 8001）~~ ✅ **已修**：`:5` 那句仍在，但 `:6-8` 已就地标注"**D5 这一半已作废（2026-09-23 核对）**"；示例端口已于 `:57` 改为 **8001**（变更说明在 `:66`） | 2026-09-24 实测：`git grep -n 8000 docs/sqlite-single-writer.md` 只命中 `:66` 的那句说明 |
| — | `overhaul-plan.md:115` 引 `architecture.md:78`，内容实际在 `:99`（自 `e63793f` 加横幅起偏移 **+21** 行；核查当日为 `:98` / 偏移 +20，其后 `3c631f9` 又插了一行状态横幅） | 实测（`git show e63793f^:docs/architecture.md` 对照当前文件） |
| — | ~~`overhaul-plan.md:11389` 与 `:11406` 的锚点**重名**（都叫 `BO.5.2`）~~ ✅ **已修**：第二个已改名为 **`BO.5.2b`**（现位置 `:11402` 与 `:11419`），锚点冲突消失 | 实测（`git grep -n "BO.5.2" docs/overhaul-plan.md`） |

**建议**：README 的"质量门禁表"与"开关表"按 `config.py` 重新对一遍，
并加一条守卫：**README 里出现的配置项名必须在 `config.py` 里存在** ——
这类"作用域守卫"你在 `.gitignore` 的 `tests/test_gitignore_scope.py` 已经写过一次，思路可照搬。

### 2.23 另外两处"声明 ≠ 现实"

- `backend/scripts/cleanup_test_data.py` **被 `.gitignore:102` 忽略**，
  却在**已入库**的 `backend/tests/测试账号信息.md:35` 里被指引为清理命令 ——
  照文档做的人会发现**这个文件根本不存在**。
- `requirements-test.txt` 的 19 条依赖**全部没有版本约束**，而 `ci.yml:51` 直接
  `pip install -r requirements-test.txt` ⇒ **CI 装的那棵树既不是声明集也不是发布集**；
  加上 `python-jose~=3.3.0` 命中 CVE-2024-33663/33664（`overhaul-plan.md:2373-2375`）——
  这条与 §2.5 同根。

---

## 3. 应当修（高价值，不阻断公开）

### 3.1 文档体系：内部过程日志 ≫ 对外可读文档

| 文档 | 行数 | 面向谁 | 问题 |
|---|---|---|---|
| `docs/overhaul-plan.md` | **11,709 行 / 775 KB** | 作者自己 | 119 个附录（J→BO），是**过程台账**；访客打开会直接放弃 |
| `docs/architecture.md` | 254 行 | 访客 | 自述"重构前快照"，**待阶段 7 重写** |
| `docs/decisions.md` | 278 行 | 访客 | 自述"**只读历史归档**，可能已与代码不符" |
| `docs/security-scan.md` | 35 KB | 访客 | 记录的是 **`182ebbd` 时刻**的一次扫描，落后 20+ 个提交 |
| `docs/archive/` | **7 份 + 索引**（8 个 md）/ 约 380 KB | 考古 | 一 份"新手教学"171 KB、"项目架构"52 KB —— **面向访客的旧文档体量比活文档还大**（2026-09-24 实测：7 份均已在 `docs/archive/README.md` 索引里，且逐份带 `状态：历史快照` 横幅） |
| `frontend/docs/a11y-audit.md` | 131 KB | 访客 | 同上：过程记录，不是规范 |
| `frontend/docs/openapi-client.md` | 129 KB | 访客 | 同上 |

**建议改法**（这是我认为**收益最高**的一条）：
1. 新建 `docs/README.md` 做**文档地图**：哪些是活文档、哪些是历史、访客该按什么顺序读；
2. 把 `overhaul-plan.md` 的"当前状态"抽成一份 ≤300 行的 `docs/ARCHITECTURE.md`（阶段 7 的本职）；
3. 过程台账移入 `docs/journal/` 并**在文件名上标明性质**（如 `overhaul-plan-journal.md`）；
4. `docs/archive/新手教学.md` 这类 171 KB 的历史教学**不进仓库**（或只留索引）。

### 3.2 仓库是**中文单语**项目（可贡献面被砍掉一半）

- 前端：**154 / 155 个 ts/tsx 文件含中文，共 110,006 个汉字**；
  `package.json` 里**没有任何 i18n / intl 依赖**；`index.html:2` 固定 `lang="zh-CN"`。
- 后端：**148 个 py 文件含中文，共 168,326 个汉字**（含注释、错误文案、日志）。
- **无英文 README**、无英文文档、无 `i18n` 骨架。
- 好消息：错误契约**已经为 i18n 留好了路** —— 统一结构
  `{"detail", "error_code", "request_id"}`（`backend/app/core/app_error.py:8-9`），
  前端可以靠 `error_code` 做程序化分流而不是匹配中文文案（`:32-33` 写明了这条原则）。

**建议改法**：分三步，不必一次做完 ——
① `README.en.md` + 英文 quick start（**成本最低、收益最大**）；
② 前端抽 `src/i18n/`（先引 `react-i18next`，只用 `zh-CN` 一种资源，后续加 `en-US`）；
③ 后端错误文案按 `error_code` 查表，而不是散落在 `AppError(...)` 里。

### 3.3 质量门禁里有"看起来在守、实际不拦"的三处

| 关卡 | 现状 | 证据 |
|---|---|---|
| 依赖安全扫描 | job 名就叫 `Security scan (**advisory**)`，`--fail-on` 未启用、退出码 2 也不失败 | `.github/workflows/ci.yml:292-311`、`:351-365` |
| 可访问性 axe | `continue-on-error: true`（**建议性**） | `.github/workflows/ci.yml:288` |
| Ruff format | `continue-on-error: true` | `.github/workflows/ci.yml:112-114` |

我**认可**你在 `ci.yml:299-314` 写下的理由（"常红的门禁只会被 `|| true` 掉"），
这是成熟判断。但作为**公开项目**，它需要另一件东西来补位：
**把"我们已知有 N 条未处理的安全发现、为什么不阻断"写进仓库可见处**
（现在只写在 `docs/security-scan.md`，且过期）。
**建议**：README 加一行状态徽章 + `SECURITY.md` 说明处置口径（见 §4.1）。

### 3.4 前端侧的高价值项（按收益排序）

| # | 问题 | 证据 | 建议 |
|---|---|---|---|
| FE-1 | **"无后端也能看 UI"的能力其实已经写好了，只是没做成命令** —— Playwright 的桩（`stubApi` + 1105 行 fixtures，为 26 个场景喂**非空**数据，未匹配路径显式 501）等价于一个 demo 后端 | `frontend/e2e/support.ts:101`、`frontend/e2e/a11y-fixtures.ts:1-40` | 复用成 `npm run dev:demo`（Vite dev 中间件或 `VITE_DEMO=1`）——**这是 §4.5 Demo 模式成本最低的落地方式** |
| FE-2 | **无浏览器支持矩阵**：无 `browserslist`、无 `build.target`、文档未声明 | `frontend/package.json`、`vite.config.ts` | 声明支持范围（否则兼容性 issues 无法裁决） |
| FE-3 | **无深色模式**：`prefers-color-scheme` 全 `src/` 0 命中；唯一"深色"是浅色界面里嵌了一块 `github-dark` 代码主题 | `frontend/src/pages/NoteDetail.tsx:22` | 令牌层（`src/styles/base.css` 的 `--color-*`）已为 `[data-theme=dark]` 备好结构，只缺第二套值 |
| FE-4 | **首屏依赖 Google Fonts**（3 个 preconnect + Noto Serif SC 外链） | `frontend/index.html:9-11` | 自托管子集化字体或本地回退：墙内/离线/企业网下字体请求会挂起，且把访客 IP 交给第三方 |
| FE-5 | **三态组件不统一**：20 个页面里 13 个用共享组件，其余各自 `useState` 手绘 | `src/pages/Projects.tsx:18`、`QA.tsx:29-31`、`Upload.tsx:60-62`、`Review.tsx:44-45` | 收敛到统一 `Loading/Error/Empty` |
| FE-6 | **注释与文档漂移**：`Toast.tsx:16` 仍说"全站 49 处 `alert()`、10 处 `confirm()`"，实测 `alert()` 调用 **0**、`confirm(` **16** | `frontend/src/components/Toast.tsx:16` | 按仓库自己的规矩"以实测为准"顺手改 |
| FE-7 | **favicon 404**：`frontend/public/` 是空的且未入库，`index.html` 无 `<link rel="icon">` | 实测 | 加一个 favicon（低成本、观感明显） |
| FE-8 | **产物体积只有警告没有约束**：`chunkSizeWarningLimit: 800` 仅警告；实测 `markdown-*.js` 396 KB、`graph-*.js` 187 KB、`react-*.js` 164 KB（入口 26 KB、CSS 25 KB） | `frontend/vite.config.ts:70`、`frontend/dist/assets/` | 加 gzip/brotli 后体积断言进 CI（代码分割本身做得很好：18 个 `React.lazy` + 3 个 manualChunks） |

### 3.5 测试的"对外可跑性"

- 后端 75 个测试文件 / 28,246 行，默认**离线**（`tests/conftest.py:131` 的网络守卫）、
  `tests/integration` 默认不收集（`pytest.ini:11-13`）—— 设计正确 ✅
- 但**没有一份"贡献者怎么跑测试"的文档**：`requirements-test.txt` 装了
  `pytest` / `ruff` / `pyyaml` / `pypdfium2` / `Pillow`，命令却只散在 CI 的
  `run:` 与文件头注释里。
- 前端同理：24 个 vitest 测试文件、11 个 e2e 规格（其中 `e2e/a11y.spec.ts`
  103 KB、`a11y-fixtures.ts` 44 KB —— 体量已经像一整套产品），
  但**没有一个 `CONTRIBUTING.md` 把这些入口列清楚**。

**建议改法**：`CONTRIBUTING.md` + `Makefile`（或 `scripts/dev.sh` / `dev.ps1`）
把 5 条门禁命令固化成一条 `make check`（命令**必须从 CI 抄**，不要凭记忆写）。

### 3.6 Docker 路线：状态是"配置就绪但从未走通"，且**缺少前置步骤**

- README 已诚实标注"Docker 未验证"（`README.md:278-295`）✅ 这一点做得对。
- 但仓库里仍有两处会**当场失败**的陷阱：
  1. `docker-compose.yml:15` 把 `./backend/.env` 挂进容器 —— 而 `.env` 被 `.gitignore` 排除，
     **全新克隆的人没有这个文件**，Compose 会直接报错；
  2. ~~**根目录与 `backend/` 都没有 `.dockerignore`**（只有 `frontend/.dockerignore`），
     `context: ./backend` 会把 `backend/data/`（本机实测 **4,705 MB**）整个送进构建上下文。~~
     ✅ **已补**（2026-09-24 复核）`backend/.dockerignore` 已入库，逐条排除
     `data/`（4,705 MB / 1131 文件）、`tests/`（6 MB / 309 文件）、`.env`、`__pycache__`、`.venv/` 等；
     根目录仍没有 —— 也**不需要**，因为没有任何构建把 `.` 当上下文。
- ~~`backend/Dockerfile` 里还在 `mkdir /app/data/chroma`（Chroma 已废弃，`requirements.txt:13-14` 自陈移除）。~~
  ✅ **已删**（2026-09-24 复核）现在的 `mkdir` 只有
  `/app/data/db /app/data/storage /app/data/celery`。

> 本条**仍未收口的是"真机验证"本身**：README 已如实标注未验证，CI 里也只有配置守卫
> （见 §执行进度 → 尚未做），所以 §5.1 的处置口径仍然成立 —— **保持诚实标注，不要假装支持**。

**建议改法**：要么按 §3.1 的口径把它标成"实验性、未验证"并补 `.dockerignore`
与"先 `cp .env.example .env`"的前置说明；要么（更干净）在公开版**移出**容器化文件，
等你真的走通一次再放回来。

---

## 4. 需要新增的功能与附属产物（这才是"开源项目"缺的东西）

按**投入产出比**排序。前 6 项是"不做就不像开源项目"，后 6 项是"做了才有人用"。

### 4.1 社区基础文件（GitHub 健康度 42% → 90%+，成本：1 天）

| 文件 | 作用 | 备注 |
|---|---|---|
| `CONTRIBUTING.md` | 贡献流程、开发环境、门禁命令、提交规范 | 直接决定"别人敢不敢提 PR" |
| `CODE_OF_CONDUCT.md` | 行为准则（建议 Contributor Covenant 2.1） | GitHub 健康度直接计分 |
| `SECURITY.md` | 漏洞上报渠道 + **已知未处理发现的处置口径** | 与 §3.3 配套 |
| `.github/ISSUE_TEMPLATE/bug_report.yml` `feature_request.yml` `config.yml` | 结构化 issue | YAML 表单比 md 模板体验好得多 |
| `.github/PULL_REQUEST_TEMPLATE.md` | PR 自查清单（对应 §5 的五条门禁） | |
| `.github/CODEOWNERS` | 明确审核人 | 单人项目也要写，表示"谁负责" |
| `CHANGELOG.md` | 对外变更史 | 从 `overhaul-plan` 的阶段/附录摘要而来 |
| `docs/README.md` | 文档地图（见 §3.1） | |
| `.editorconfig` | 统一编码/换行（当前中文注释乱码与 CRLF 问题都源于缺它） | |
| `AUTHORS` / `NOTICE` | 署名（配合 §2.6） | |

### 4.2 发布与版本（让"用哪个版本"有答案）

- git **tag** + GitHub **Release**（自动生成 release note）
- 单一版本源；前端构建注入 `__APP_VERSION__`，后端 `/health` 回显版本
- `CHANGELOG.md`（Keep a Changelog 格式）+ 语义化版本政策
- **升级迁移说明**：`_migrate_sqlite` 只加列不删数据，跨版本升级对用户是"直接换代码"，
  这件事**必须写成 `UPGRADING.md`**，否则用户不敢升

### 4.3 AI 辅助开发约定要不要公开（你本地的 `AGENTS.md` / `.trae/`）

本机有 `AGENTS.md`（2,543 B，DSH 会话规矩）与 `.trae/` 下 52 个设计文档（10,807 行），
**都不在仓库里**。我的建议是**分开处理**：

- `AGENTS.md` / `CLAUDE.md` 这类"AI 助手约定"**值得进库**（这是 2026 年的项目惯例，
  也让 AI 生成的 PR 有统一口径），但**要删掉只对本机有意义的内容**
  （自检口令"蓝鲸七号"、conda 环境名、本地端口等 ← 这些属于私人备忘）；
- ⚠️ **当务之急是别让它"半入库"**：`AGENTS.md` 现在**未跟踪、也未忽略**
  （`git status --short` → `?? AGENTS.md`，`git check-ignore` 无输出）。
  一次 `git add -A` 就会把内部工作流（"本地提交不要推送"、端口约定、口令）
  连同 `.trae/` 的语境一起推上去。**先决定它的归宿：要么清洗后入库，要么进 `.gitignore`。**
- `.trae/` 的 52 个设计文档**不要整批进库**：它们是"每个 bug 的计划书"，
  对外价值低、体量大。挑 3–5 份**能被外部复用的**（如"存储结构与数据库设计"）
  整理进 `docs/design/`，其余留在本地。

### 4.4 分发与部署（现在只有"本地进程"一条路）

| 产物 | 为什么需要 | 备注 |
|---|---|---|
| **Docker 镜像（真跑通）** | 自托管用户的第一诉求 | 你已因磁盘/资源放弃容器化，但**公开项目里"未验证的 Dockerfile"比"没有 Dockerfile"更伤人** |
| **一键部署**（Railway / Fly.io / Zeabur 模板、或 `docker compose` 单命令） | 把"7 步手工安装"压成 1 步 | 与项目"零外部依赖"的定位天然契合 |
| **GitHub Pages 文档站**（VitePress / MkDocs） | `docs/` 已有 21,077 行素材，白放着 | 顺带解决 `documentation` 链接指错分支的问题 |
| **在线 Demo / 截图站** | 访客不装就能看 | 你已有 2 个静态 HTML mock，可整理成 Pages 上的"界面预览" |
| **发布物**：`pip install engramnote`（CLI）+ 可选的 Fly.io 一键模板 | 让"零配置启动"这句话成立 | 需要 §2.5 的 `pyproject.toml` 打底 |

### 4.5 首次上手体验（当前门槛 = 2.2 GB 模型 + 2 个 API Key）

这是**产品级**的开源缺口，比任何社区文件都影响采用率：

- **现状**：`check_env.py` 要下 **BGE-M3 约 2.2 GB**（`README.md:177`），
  AI 功能还要 DeepSeek/GLM Key 与 MinerU Token；不配 Key 时功能大面积不可用。
- **建议**：
  1. **Demo 模式**（`DEMO_MODE=true`）：预置夹具数据 + 桩化 LLM，
     让访客 `docker compose up` 或 `npm run dev` 就能看到完整界面 ——
     **这是把"0 stars"变成"有人试"的最短路径**；
  2. **"无嵌入模型"降级路径**：检索退化为纯 BM25（代码里已有降级逻辑，
     见 README 的降级策略），让 2.2 GB 变成"可选"而非"必需"；
  3. **`BYO-key` 向导**：前端首启引导填 Key，并在缺 Key 时**明确说明哪些功能不可用**
     （而不是让用户在一堆失败里猜）。
- 顺带：`check_env.py` 有 10 个步骤、5 个命令参数（`README.md:167-190`），
  对访客已经偏重；建议 README 只留"一条命令 + 一个可选命令"。

### 4.6 学习效果的可验证性（产品可信度）

项目核心命题是"长期记忆闭环"，但**仓库里没有任何学习效果的度量或基准**：

- 后端 `scripts/` 里有 `eval_retrieval.py`（检索质量评测，README 提到）
  但**没有** FSRS 调度的效果指标（如预测保持率 vs 实际正确率的校准曲线）——
  而 `review_log.py:145` 已经写明"FSRS 参数拟合（3.14）直接消费这一列"，
  说明**数据已经够了，缺的是把结论摆出来**。
- **建议**：`docs/BENCHMARKS.md`（检索 top-k 命中率 + 调度校准），
  放进 CI 作为**非阻断**报告 —— 这会成为这个项目最独特的卖点
  （同类笔记工具没有一个敢公开自己的调度效果）。

### 4.7 可观测性与运维（自托管用户的实际痛点）

| 缺口 | 证据 | 建议 |
|---|---|---|
| 无指标暴露（Prometheus / OpenTelemetry） | 无相关依赖 | 至少暴露 `/metrics`（队列深度、LLM 调用数/失败数、嵌入积压 —— `/ready` 已在报队列深度，扩展即可） |
| 日志无统一格式/级别约定 | `docs/journal/tooling-report.md` 之外无说明 | 结构化 JSON 日志 + `LOG_LEVEL` 文档化 |
| 无备份/恢复指引 | `docs/overhaul-plan.md` 附录 AS 做过恢复演练 | 把演练结论写成 `docs/BACKUP.md`（**用户最怕丢笔记**） |
| 无 SQLite 并发说明的对外版本 | `docs/sqlite-single-writer.md` 存在 ✅ | 在 README 显著位置提示"只能跑一个 worker"（否则用户会踩 `database is locked`） |

### 4.8 生态与扩展性（"附属产物"的长期项）

- **插件/扩展点**：清洗规则、卡片类型、题目模板现在都硬编码在服务里；
  给出一个 `plugins/` 约定能让社区贡献"某某学科的卡片模板"。
- **导入/导出与互操作**：Anki（`.apkg`）、Obsidian vault、Markdown 目录导入导出 ——
  **这是笔记类开源项目最重要的"防锁定"承诺**，也是最大的采用入口。
- **CLI**：`engramnote import ./notes/`、`engramnote export --format anki`
  （依赖 §2.5 的打包能力）。
- **主题/语言包**：UI 主题现在是内置的"学术优雅"一套；主题包机制成本低、传播性好。
- **学科模板包**：面向"法考/考研/医考"等场景的卡片与题目模板（对中文学习市场极对口）。

### 4.9 贡献者体验（把人留在项目里）

- **`good first issue` 清单**：从 `docs/overhaul-plan.md` 的"未做事项总表"
  （附录 BN，按"是否影响现在的使用"分三档）里挑出来 —— **这份表本身就是最好的
  issue backlog**，只要改写成对外的语言。
- **`ROADMAP.md`**：把阶段 0–7 的剩余部分翻译成"下一版会有什么"。
- **`GOVERNANCE.md`**：单人项目也应明确"谁拍板、决策记录写在哪"
  （你有 `docs/decisions.md` 的传统，只需对外说明）。
- **DCO / CLA 决策**：接受外部 PR 前必须定的法律问题（配合 §2.6 的署名）。
- **讨论区**：开启 GitHub Discussions 承接"用法问题"，把 issue 留给缺陷。

### 4.10 自动化（省你的时间，也让仓库显得"有人管"）

| 自动化 | 建议 |
|---|---|
| `.github/dependabot.yml`（或 Renovate） | pip + npm 双生态，**依赖漂移的根治手段**（配合 §2.4） |
| CI 状态徽章 | README 顶部（现在 5 个徽章全是静态 shields，**没有一个反映真实状态**） |
| CodeQL / `pip-audit` 阈值化 | 在 §3.3 的处置口径写清后逐步收紧 |
| 自动 release note | 打 tag 时从 PR/commit 生成，减少手工记账 |
| 新贡献者欢迎 workflow | `actions/first-interaction`，成本极低 |
| 仓库 topics | 补齐 `note-taking` `spaced-repetition` `rag` `fastapi` `react` `knowledge-base` `fsrs` `self-hosted` —— **0 → 有，等于把发现度从 0 拉起来** |

---

## 5. 可以不改（明确判定，避免把力气花错地方）

这些是我**认可**的现状，列出来是为了防止后续被"开源最佳实践"误导：

1. **容器化未走通**：README 已诚实标注（`README.md:278-295`），
   项目也因资源约束明确放弃 —— **保持诚实标注即可，不要为了"看起来专业"去假装支持**。
2. **不引入 PG / Redis / 消息队列**：`docs/sqlite-single-writer.md` 已把约束写清，
   这是项目的定位（零外部依赖），不是缺陷。
3. **`docs/decisions.md` 作为只读归档保留**：理由（保留踩坑知识）成立，做法正确。
4. **Alembic 目录存在但启动路径不调用**：README `:90-96` 已如实说明，
   属"历史对照"，可接受。
5. **测试账号弱口令入库**：本地 e2e 夹具，非真实凭据，撤除需改写历史 —— **不值得动**。
6. **CI 的 advisory 三件套**：理由充分（`ci.yml:299-314`），
   要补的是"对外可见的处置说明"，而不是把它们变成阻断门禁。
7. **中英混杂的提交信息**：单人项目、信息密度极高（150 个提交每条都说清了"为什么"），
   **不需要**改成 Conventional Commits —— 但**发布用的 release note 需要重新组织**。
8. **SQLite 单写者约束**：`main.py:59-120` 在多 worker 时**拒绝启动**并给出两条出路，
   `docs/sqlite-single-writer.md` 论证完整 —— 这是**有意的架构决策**，不是缺陷
   （只需把它在 README 显著位置写给自托管用户看）。
9. **SQL 注入面已达标**：用户输入全部走绑定参数（如 `fts_search_service.py:175-184` 的 `:expr/:uid`），
   f-string 拼接 SQL 仅用于内部表名（`backup_service.py:68/109`、`database.py:1219`）。
10. **SSRF 面很小**：只有运维配置的 `base_url` 会被请求（`mineru/converter.py:569-577`、
   `llm/client.py:102-110`），全后端仅 2 处 `import httpx` + 1 处 `requests`，
   **没有用户可控 URL 的抓取端点**。
11. **错误契约与日志**：中文文案 + **稳定 `error_code`** + `request_id` + 三路日志
   （`main.py:481-541`、`core/logging_config.py:13-18/53-67`）是仓库最扎实的一块 ——
   只需在 CONTRIBUTING 写明"`error_code` 是机器可读契约、文案不保证英文"。
12. **上传护栏与密码策略**（魔数与 `.md` 嗅探、页数/压缩比/条目数上限、配额、
   `password_policy.py:77-111` 含 bcrypt 72 字节硬上限）质量高于同规模项目。
   ⚠️ 但 `overhaul-plan.md:2380-2381` 仍把 bcrypt 截断列为"未修"，**实际已修**
   （`schemas/user.py:37`、`auth_service.py:351`、`password_policy.py:79-85`）—— 那条应改标"已解决"。
13. **生产关闭 `/docs`、`/openapi.json`、`/redoc`**（`main.py:200-245`）：理由充分。
14. **无遥测 / 无分析 SDK**：`sentry|analytics|gtag|posthog` 在 `src/` 0 命中 ——
    对"学习笔记"这类含私人内容的工具，这是**正确**的默认值。
15. **`dist/`、`test-results*/`、`.npm-cache/` 未入库**、前端无任何密钥：
    `git ls-files frontend` 共 241 个文件，对这些目录 0 命中。

---

## 6. 如果只做 5 件事（按顺序执行的最小路径）

| 顺序 | 事项 | 涉及文件 | 预估 |
|---|---|---|---|
| 0 | **轮换 JWT 密钥 + 修掉教程里的公开默认值 + 启动拒绝已知占位值**（见 §2.1） | `backend/.env`、`config.py:564`、`docs/archive/新手教学.md:238/3454` | 1 小时 |
| 1 | **修 README 的错误陈述（R-1~R-6）+ 加 3 张截图** | `README.md`（§2.2/§2.3） | 半天 |
| 2 | **补 `requirements.txt` 的 PDF 硬依赖 + 建社区基础文件** | `requirements*.txt`、`.github/`、根目录 | 1 天 |
| 3 | **依赖锁 + 版本单一来源 + 第一个 tag/release/CHANGELOG** | `requirements.txt`、`pyproject.toml`、tag | 1–2 天 |
| 4 | **把内部台账与对外文档分层**（`docs/README.md` 地图 + 英文 README + 精简架构文） | `docs/` | 2 天 |

做完 0–4，这个仓库的"开源门面"就与它的代码质量匹配了。
再往后（§4.5 的 Demo 模式、免 2.2 GB 模型的降级启动）决定它能不能**真的**被人用起来 ——
而这一步你已经有一半现成材料：Playwright 那套 1105 行 fixtures 就是现成的 demo 后端。

---

## 7. 附录：本次核查的可复现步骤

```bash
# 远端 HEAD（Python，绕开本机 Schannel 故障）
python -c "import json,urllib.request;print(json.load(urllib.request.urlopen(urllib.request.Request('https://api.github.com/repos/trx-0833/EngramNote/commits?per_page=1',headers={'User-Agent':'x'})))[0]['sha'])"

# 快照拉取与解包
python _ref/fetch_snapshot.py            # → _ref/snapshot/EngramNote-main

# 逐文件比对（已跟踪文件 vs 快照）
git -C D:/engramnote ls-files > _ref/local-tracked.txt
python _ref/compare.py                   # → _ref/compare-report.txt

# 本地 vs HEAD
git -C D:/engramnote diff HEAD --stat    # 空 = 一致
```

**未做/未取到的**：远端 issue/PR 数为 0（尚未开放讨论）；
本机无法用 `git clone`（Schannel TLS），快照经 `codeload` tarball 获取，
因此**没有远端 git 历史对象**（对比基于同一提交的文件树，结论不受影响）。
