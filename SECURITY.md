# 安全政策（Security Policy）

## 支持范围

EngramNote 是**自托管**应用，本项目只维护 `main` 分支上的最新代码。
当前版本号是 `0.1.0`（`pyproject.toml:41`、`frontend/package.json:4`），
**没有** git tag / release 分支，因此"支持哪个版本"的答案是：只有 `main`。

> ⚠️ 本项目**没有任何官方托管实例**。你部署在哪里、谁能访问、数据放在哪，
> 都由你自己负责（见下面的「部署者注意事项」）。

---

## 如何上报漏洞

**首选：GitHub Security Advisories（私密渠道）**

<https://github.com/trx-0833/EngramNote/security/advisories/new>

**次选：通过 issue 私密联系维护者**

本仓库**没有公开邮箱**，也不要在公开 issue 里贴漏洞细节。如果你走不通
Security Advisories，可以开一个**不含任何技术细节**的 issue
（<https://github.com/trx-0833/EngramNote/issues>），只说明"有一个安全问题需要私下沟通"，
由维护者把渠道转成私密方式后再请你补充。

**请不要公开细节的内容包括但不限于**：可利用的 PoC、真实密钥/令牌、
真实笔记或上传文件的内容、数据库文件、`backend/.env`、日志原文。

### 上报时请尽量附上

- 受影响版本 / 提交号，以及部署方式（本地进程 or 容器——容器化这条路**未经验证**，见下）；
- `APP_ENV` 取值（`dev` / `prod`）与相关开关（如 `LOG_SQL`）；
- 复现步骤与最小复现输入；
- 响应体里的 `request_id`（错误信封是 `{detail, error_code, request_id}`，
  见 `backend/app/core/app_error.py:9`；服务端可按 `rid=` 检索日志，
  见 `backend/app/middleware/request_context.py:5-11`）。

### 我们的处理方式（诚实版）

- 这是一个**单人维护**的项目，没有安全响应团队，也**不承诺**任何 SLA 时限；
- 我们会确认收到、复现、在 `CHANGELOG.md` 里如实记录修复；
- 若你希望署名，请在报告里说明。

---

## 已知未处理的安全发现（处置口径）

这一节的存在本身就是结论：**下面这些今天没有被解决**。
把它们写成"已加固"是本仓库最忌讳的毛病（声明比现实宽松）。

### 1. 依赖扫描在 CI 里是**建议性**的，不是门禁

- `security-scan` job 的 `continue-on-error` 语义与 `--fail-on` 阈值：
  扫描**必须跑**，但**不阻断合并**（`.github/workflows/ci.yml:340-362`，
  其中 `ci.yml:347-362` 写明了这条判定的理由）。
- 扫描的原始结果、逐条定性（可修 / 不可修 / 打不到 / 误报）与**升级成门禁的前置条件**
  在 `docs/security-scan.md`：见该文件 §6（`:382-398`）、§9.3（`:490-500`）、
  §9.4 的三条 promotion conditions（`:520-532`）。
- **为什么不设阈值**：今天若拿 `--fail-on high` 当门禁，CI 会**长期常红**——
  Node 侧 4 条 high + 1 条 critical、Python 侧 8 条 high + 1 条 critical（`docs/security-scan.md:492-493`），
  其中一批是上游明确不修，另一批只存在于 `requirements.txt` 的**声明路径**上
  （`ci.yml:351-354`）。常红的门禁只有一个结局：被加 `|| true` 或删掉，
  那比没有门禁**更糟**，因为它制造"我们已经在管安全了"的假象
  （`docs/security-scan.md:494-495`）。
- 唯一**无条件红灯**的是"扫描器根本没跑起来"（退出码 2）——
  那不是"发现了问题"，而是"我们没有在做这件事"（`ci.yml:400-419`、`:458-459`；
  `docs/security-scan.md:498-500`）。

### 2. 已知的具体依赖风险

- `python-jose` 锁在 `~=3.3.0`（`backend/requirements.txt:46`），命中
  **CVE-2024-33663 / CVE-2024-33664**；文件内已写明"升级到 `>=3.4` 或迁 PyJWT
  是独立批次，需要单独验证"（`backend/requirements.txt:44-46`）。**今天没有修。**
- `backend/requirements.txt` 用 `~=` 锁主次版本，允许补丁浮动；
  声明集与本机实际安装集的差异**可能存在**——
  用只读命令自查：`python backend/scripts/check_dependency_drift.py`
  （`backend/requirements.txt:19-24`、`docs/security-scan.md:528`）。

### 3. 容器镜像**没有**加固，而且这条路**从未验证**

- 两份 Dockerfile 都**没有**非 root `USER`、也**没有** `HEALTHCHECK`
  （`docs/security-scan.md:415-417` 明确把这两条列为"本轮没有做的事"）。
- 容器化在本项目里是**历史遗留、从未构建或运行过**（`README.md:324-336`）。
  配置守卫只保证"文件没被改坏"，**不代表这条路走得通**，更不代表安全。

### 4. 其他**按设计**保留的风险（不是待修的缺陷）

- **访问令牌是无状态的**，默认有效期 24 小时（`backend/app/config.py:141-148`）：
  登出/撤销之后，已泄露的那枚访问令牌在过期前**仍然可用**。
  刷新令牌与撤销机制见 `docs/overhaul-plan.md` 阶段 6.3（`:3305`）。
- **限流是进程内的**，重启清零；全端点限流已补齐，但**没有**登录失败计数与锁定
  （`docs/overhaul-plan.md:2943` 的 0.7 条：限流有、锁定无）。
- **错误响应在 `dev` 下会回吐 traceback**：这是 `APP_ENV=dev` 的语义，
  生产请用 `prod`（`backend/app/main.py:125-141`）。

### 5. 扫描覆盖不到的部分（"跑过了"≠"安全"）

依赖扫描只把版本与公开公告库比对，**看不见**业务逻辑漏洞、越权（IDOR）、SSRF、
认证绕过与 SQL 注入；也**不做可达性分析**（`docs/security-scan.md:421-426`）。
这一层的防线是 `backend/tests/` 的用例（含 `tests/test_error_contract_adoption.py`
这类 AST 级守卫）与人肉核查。

---

## 部署者注意事项（你能自己降低的风险）

### JWT 密钥策略（启动即拦截）

`APP_ENV=prod`（**这是默认值**，`backend/app/config.py:563`）下，
`JWT_SECRET_KEY` **为空**或为**已知公开占位值**时，应用**拒绝启动**：

```text
Value error, 生产环境必须配置 JWT_SECRET_KEY，当前配置的是**已公开的占位值**（历史文档里的示范密钥，任何人都知道）。
生成方法：python -c "import secrets; print(secrets.token_hex(32))"
```

- 判定实现：`backend/app/config.py:627-670` 的 `_validate_production_secrets`；
- "已知公开占位值"清单：`backend/app/config.py:59-67` 的 `KNOWN_INSECURE_JWT_SECRETS`
  （含历史教程里示范过的 `engramnote-dev-secret-change-in-production` 等 7 个值）；
- **为什么必须拦**：HS256 + 24h 无状态访问令牌（登出不可撤销）意味着
  任何拿到这份仓库的人都能为任意 `user_id` 伪造有效令牌（`config.py:54-56`、`:642-643`）；
- `APP_ENV=dev` 下允许空密钥零配置启动，但**空密钥不用于签发**：
  会自动生成随机密钥并持久化到 `data/.jwt-secret`，重启复用
  （`config.py:633-634`、`:691-712`）。⚠️ 多实例部署下 `dev` 是**错的**姿态：
  每个容器会生成不同的密钥，换容器即全员失效。

生成一个真正的密钥：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### `LOG_SQL=true` 会把业务数据明文写进日志

`LOG_SQL` 默认 `false`，且**不再跟随 `APP_ENV`**（`config.py:566-572`）。
打开后 SQLAlchemy 的 SQL 语句会进入 `data/logs`（日志目录见 `README.md:214`），
其中包含 **bcrypt 密码哈希**与**知识卡片/题目的正文**
（`backend/app/config.py:569-570`、`backend/app/database.py:41`、
`backend/app/main.py:161-165`）。

**生产环境不要开。** 需要排障时请临时开、排完关，并注意 `data/logs` 的轮转文件
里同样有这些内容（`log_max_bytes` / `log_backup_count`：`config.py:515-516`）。

### 启动时的"安全姿态"日志行

启动日志里有一行明确的生效姿态（`app_env` / LLM / SQL 日志 / traceback 回吐 / JWT 密钥来源），
并在 `dev` 与 `LOG_SQL=true` 两种危险组合下额外告警
（`backend/app/main.py:125-165`）。**它只记录、不阻止启动**——
把它当成部署后的第一条自查输出，而不是安全保证（`main.py:139-141`）。

### 其他

- 生产姿态下 `/docs`、`/redoc`、`/openapi.json` **不注册**（`docs/overhaul-plan.md:2946`）；
  只有 `APP_ENV=dev` 才开放，别把文档端点当成"反正没人知道路径"。
- `backend/.env` 与 `backend/data/` **绝不入库**（`.gitignore:22-40`），
  也**不要**打进任何镜像或压缩包。
- 备份与恢复请走项目自带脚本（`backend/scripts/backup_db.py`、
  `backend/scripts/restore_db.py`），细节见 `UPGRADING.md`。
- 发现**仓库里**混入了真实凭据或真实笔记：按上面的渠道私密上报，
  不要在公开 issue 里贴出内容本身。

---

## 本仓库之外的边界

- 第三方服务（DeepSeek / GLM / MinerU 云端 API / SMTP）的安全问题请向**它们**上报；
  本项目只负责"如何配置与调用它们"。
- 你自己的部署环境（反向代理、TLS、防火墙、宿主安全）不在本项目范围内。
