# 依赖与镜像扫描（overhaul-plan 阶段 6.7）

> 本轮扫描时间：**2026-09-14 12:57（+08:00）** ｜ 仓库 HEAD：`182ebbd`
> 执行入口：`python backend/scripts/security_scan.py`
> 本文件记录的是**一次真实运行的结果**，不是"以后应该怎么做"的设想。

## 0. 一页摘要

- **两个主要扫描器都真正跑起来了**：`pip-audit` 2.10.1、`npm audit`（项目配置的
  淘宝镜像**没有** audit 端点，已自动回退官方源）。第三个 `trivy` 0.74.0 也跑了，
  但它的 `vuln` 子扫描器**没能执行**（漏洞库拉不下来），只有 `misconfig` + `secret` 出了结果。
- **去重后的发现：Python 19 条公告（7 个包）、Node 11 条（11 个包）、Trivy 4 条配置问题
  —— 合计 34 条。** 严重度（Python 侧取自 GitHub Advisory API，见 §4 的开头说明）：
  **critical 2 / high 14 / moderate 13 / low 3 / 无等级 2**。
  ⚠️ 三边的计数粒度不同（Python 按公告条数、npm 按包、Trivy 按规则命中），**不要相加**。
- **`ecdsa` 与 `nltk` 两条上游没有修复版本**；其余都有，但**本轮一律没有升级依赖**
  （硬性约束：升级是独立的、需要单独验证的批次，见 §7）。
- **没有一条被认定为"生产环境真实可利用"**，但这句话的边界必须看清：
  它建立在**人工 grep 代码路径**上，不是"跑通了 exploit"；其中 2 条明确标了"待验证"（§5）。
- **最重要的一条发现不是某条 CVE，而是要求文件漂移**：`requirements.txt` 用 `~=` 锁次版本，
  今天全新安装会解析到 `starlette 0.46.2` / `pydantic 2.0.3` / `python-jose 3.3.0`，
  而**本机与 CI 实际跑的是 1.6.0 / 2.13.5 / 3.5.0**，一条都不沾。详见 §4.1。

---

## 1. 一条命令跑起来

```bash
# 在 backend/ 或仓库根目录都可以（脚本自己定位仓库）
python backend/scripts/security_scan.py

# 机器可读，供 CI 归档
python backend/scripts/security_scan.py --json-out scan.json

# 把"高危及以上"当阻断门禁
python backend/scripts/security_scan.py --fail-on high
```

退出码（**这是本脚本存在的核心理由**）：

| 码 | 含义 |
|---|---|
| `0` | 扫描完成，没有达到 `--fail-on` 阈值的发现 |
| `1` | 有发现达到 `--fail-on` 阈值 |
| `2` | **必需的扫描器没能运行**（未安装 / 网络失败）—— 不是"没发现问题" |

退出码 `2` 专门用来对付这个仓库反复踩到的形态：**能力"配置了"却从没真正执行过**。
对照 6.5 —— rate limit 规则写了 4 条昂贵的端点，但匹配方式写成
`path.endswith(suffix)`，与真实路由永不相等，于是**从未生效**且无人发现。
扫描器"没跑起来"必须是红灯，不能是报告里一句容易被忽略的"跳过"。

`pip-audit` 与 `npm audit` 是**必需**的（缺了就是退出码 2）；
`trivy` 是**可选**的：没装只算覆盖缺口（打印"未安装 —— 本项跳过（**不是**通过）"），
装了但 `vuln` 子扫描器因漏洞库拉不下来而失败时会**降级**为 config/secret 并显式声明
（见 §4.4），不会假装那 0 条等于"依赖没问题"。

`--fail-on` 默认是 `none`（只报告不阻断）。理由见 §6。

---

## 2. 本轮实际装了什么、跑了什么

| 工具 | 状态 | 版本 | 装在哪 |
|---|---|---|---|
| `pip-audit` | ✅ 已装并运行 | 2.10.1 | `mineru_env`（`python -m pip install pip-audit`） |
| `npm audit` | ✅ 已运行（**需换源**，见下） | npm 10.9.8 / node 22.22.3 | 随 npm |
| `trivy` | ⚠️ 已装，`misconfig` + `secret` 已运行；**`vuln` 因漏洞库拉不下来未执行** | 0.74.0 | `%LOCALAPPDATA%\Programs\trivy\trivy.exe`（仓库外，49.3 MB zip → 164.3 MB exe） |

### 2.1 Python 侧审计的是哪份依赖清单

仓库里没有 `pyproject.toml`、没有 `environment*.yml`，只有两份 requirements：

| 文件 | 性质 | 是否审计 | 为什么 |
|---|---|---|---|
| `backend/requirements.txt` | **声明的生产依赖集** | ✅ | 这是"真部署会装什么"，6.7 的验收对象就是它 |
| `backend/requirements-test.txt` | CI 与本机测试实际安装的**子集** | ✅ | CI 装的就是它（`.github/workflows/ci.yml` 第 51 行），漏了它等于漏了 CI |

**没有退化成"审计整个 conda 环境"**。`mineru_env` 里装了 224 个 distribution，
其中包含 mineru / torch / 各种与本项目无关的工具链 —— 对它们报警告只会淹没真正的信号。
两个清单已经覆盖了本项目的依赖面（`requirements.txt` 解析出 89 个包，
`requirements-test.txt` 解析出 62 个包）。

⚠️ **`pip-audit` 的版本语义必须说清楚**：`-r` 模式是在**临时虚拟环境里真解析一遍**
（`pip install --dry-run --report`）再查公告库。所以它报的是
**"今天全新安装会拿到哪些版本"**，不是"本机现在装着哪些版本"。
本机 conda 环境的版本与解析结果**并不一致**，这个差异本身就是本轮最重要的发现（见 §4.1）。

### 2.2 npm audit 的一个真实障碍：换源

本机 npm 源是 `registry.npmmirror.com`（淘宝镜像），
它**没有实现 audit 端点**：

```
npm warn audit 404 Not Found - POST https://registry.npmmirror.com/-/npm/v1/security/audits/quick
         - [NOT_IMPLEMENTED] /-/npm/v1/security/* not implemented yet
npm error audit endpoint returned an error
```

关键点是：**这不是"审计通过"，而是"审计根本没跑"**。
把 404 读成"0 条发现"是这个项目最典型的一类自欺。
`security_scan.py` 检测到 `NOT_IMPLEMENTED` 后自动回退到
`--registry=https://registry.npmjs.org` 重跑，并把"用了哪个源"写进报告。

---

## 3. 原始输出与计数（未加工）

```
$ python -m pip_audit -r backend/requirements.txt
Found 34 known vulnerabilities in 7 packages          # ← 原始记录数（含重复，见下）
解析出 89 个包

$ python -m pip_audit -r backend/requirements-test.txt
Found 2 known vulnerabilities in 1 package
解析出 62 个包

$ npm audit --json --registry=https://registry.npmjs.org
{"info":0,"low":0,"moderate":6,"high":4,"critical":1,"total":11}
依赖总数 345（prod 52 / dev 293）
```

### 3.1 为什么 pip-audit 报 34 条而实际只有 19 条公告

pip-audit 2.10.1 会把同一条 advisory 按**数据源**各输出一次
（PyPI advisory DB 与 OSV 都收录时就是 2 条）。逐包核对：

| 包 | 原始记录 | 去重后公告 | 重复的 id |
|---|---|---|---|
| starlette 0.46.2 | 14 | 7 | 全部 7 条各出现 2 次 |
| transformers 4.57.6 | 8 | 5 | 3 条重复 |
| python-jose 3.3.0 | 5 | 3 | 2 条重复 |
| pydantic 2.0.3 | 2 | 1 | 1 条重复 |
| pytest 8.0.2 | 2 | 1 | 1 条重复 |
| ecdsa 0.19.2 | 2 | 1 | 1 条重复 |
| nltk 3.10.3 | 1 | 1 | — |
| **合计** | **34** | **19** | |

所以后文一律用**去重后的 19 条**。这不是"少报"，是去掉工具自身的重复输出。

---

## 4. 严重度汇总

⚠️ 先说清楚两件事，否则下面的表会被误读：

1. **pip-audit 不提供严重度。** Python 的公告源（PyPI advisory DB / OSV）不带 CVSS，
   所以脚本把 Python 侧一律计入 `unknown` 而**不猜**。
   下表的 Python 严重度是**另取 GitHub Advisory API**（`GHSA-*` 的 `severity` / `cvss`）
   补齐的，属于**本文件的补充信息，不是扫描器的输出**；
   19 条中有 2 条连 GHSA 别名都没有，只能标 `unknown`。
2. **三边的计数粒度不同。** Python 侧一行 = 一条公告；npm 侧一行 = 一个**包**
   （npm 的 `metadata.vulnerabilities` 按包聚合，取该包最严重公告的等级）；
   Trivy 侧一行 = 一条规则命中。所以**不要把它们相加**。

三边合起来（仅为看清全貌，**不是一个可比的单一数字**）：

| 严重度 | Python（按公告） | Node（按包） | Trivy（按命中） | 合计 |
|---|---|---|---|---|
| critical | 1 | 1 | 0 | 2 |
| high | 8 | 4 | 2 | 14 |
| moderate | 7 | 6 | 0 | 13 |
| low | 1 | 0 | 2 | 3 |
| unknown（工具与 GHSA 都不给等级） | 2 | 0 | 0 | 2 |
| **合计** | **19** | **11** | **4** | **34** |

### 4.1 Python：`backend/requirements.txt`（声明的生产依赖集）

89 个解析包 → **19 条公告 / 7 个包**。

| 严重度 | 条数 |
|---|---|
| critical | 1 |
| high | 8 |
| moderate | 7 |
| low | 1 |
| unknown（无 GHSA 别名，工具也不给） | 2 |
| **合计** | **19** |

按包展开（严重度取该包内最严重的一条）：

| 包（解析版本） | 公告数 | 最高严重度 | 最高 CVSS | 有修复版本？ |
|---|---|---|---|---|
| `starlette` 0.46.2 | 7 | **high** | 7.5 | 有（0.47.2 / 0.49.1 / 1.0.1 / 1.1.0 / 1.3.0 / 1.3.1，逐条不同） |
| `transformers` 4.57.6 | 5 | **high** | 8.0 | 3 条有（5.0.0rc3 / 5.3.0 / 5.10.0），1 条无，1 条无 GHSA |
| `python-jose` 3.3.0 | 3 | **critical**（GH 标签；CVSS 7.4） | 7.4 | 2 条有（3.4.0），1 条无 |
| `pydantic` 2.0.3 | 1 | moderate | 5.9 | 有（2.4.0） |
| `pytest` 8.0.2 | 1 | moderate | 6.8 | 有（9.0.3） |
| `ecdsa` 0.19.2 | 1 | **high** | 7.4 | **无**（上游明确不会修） |
| `nltk` 3.10.3 | 1 | **high** | 7.0 | **无** |

**⚠️ 本轮最重要的一条发现：`requirements.txt` 与"实际在跑的环境"严重漂移。**

`requirements.txt` 用 `~=` 锁定到**主次版本**（`pydantic~=2.0.0`、`python-jose~=3.3.0`、
`pytest~=8.0.0`…），于是全新安装解析到的是**该次版本号下最后一个补丁版**；
而本机 conda 环境和 CI 实际跑的是**新得多**的版本：

| 包 | `requirements.txt` 会装 | 本机实际装着 | 差异 |
|---|---|---|---|
| starlette | 0.46.2（7 条公告） | **1.6.0**（0 条 —— 高于全部修复版本） | 大版本差 |
| pydantic | 2.0.3（1 条） | **2.13.5**（0 条） | 次版本差 |
| python-jose | 3.3.0（3 条） | **3.5.0**（0 条） | 次版本差 |
| pytest | 8.0.2（1 条） | **9.1.1**（0 条） | 大版本差 |
| fastapi | 0.115.14 | **0.141.1** | 次版本差 |

也就是说：**这套 19 条公告里绝大多数，只有在"真的按 `requirements.txt` 部署"时才会出现；
本机与 CI 现在跑的版本一条都不沾。** 这是 `requirements-test.txt` 文件头已经记录过的
同一类缺陷（手抄/漂移的清单掩盖了真实状态），只不过这次漂移的方向是"声明比现实旧"。

### 4.2 Python：`backend/requirements-test.txt`（CI 实际安装集）

62 个解析包 → **2 条原始记录 / 1 条公告（去重）**，即同一个 `ecdsa` 问题。
这份清单**没有锁版本**，所以解析到的是最新版（fastapi 0.141.1、pytest 9.1.1、
python-jose 3.5.0…），除了上游明确不会修的 `ecdsa` 之外干干净净。

| 严重度 | 条数 |
|---|---|
| high | 1（`ecdsa` 0.19.2，无修复版本） |
| 其他 | 0 |

### 4.3 Node：`frontend/package-lock.json`

345 个依赖（prod 52 / dev 293）→ **11 条**（npm 按包聚合）。

| 严重度 | 条数 |
|---|---|
| critical | 1 |
| high | 4 |
| moderate | 6 |
| low | 0 |
| info | 0 |
| **合计** | **11** |

| 包（实际安装版本） | 严重度 | 归属 | 有修复？ |
|---|---|---|---|
| `vitest` 2.1.9 | **critical** | dev | 需跨大版本（→5.0.0） |
| `vite` 5.4.21 | **high** | dev | 需跨大版本（→8.3.0） |
| `browserslist` 4.28.2 | **high** | dev | 有（同大版本内） |
| `nanoid` 3.3.12 | **high** | dev | 有 |
| `postcss` 8.5.15 | **high** | dev | 有 |
| `react-router-dom` 6.30.4 | moderate | **prod** | 有（同大版本内） |
| `react-router` 6.30.4 | moderate | **prod** | 有 |
| `esbuild` 0.21.5 | moderate | dev | 需随 vite 升级 |
| `@vitest/mocker` 2.1.9 | moderate | dev | 需随 vitest 升级 |
| `vite-node` 2.1.9 | moderate | dev | 需随 vitest 升级 |
| `baseline-browser-mapping` 2.10.33 | moderate | dev | 有 |

**11 条里只有 2 条落在生产依赖上**（`react-router-dom` 及其传递依赖 `react-router`，
两者是同一个问题）。其余 9 条全部在构建/测试工具链里，不进 `frontend/dist`。

### 4.4 Trivy

`trivy fs` **跑起来了，但只跑了两个子扫描器**（原始计数）：

```
Tests: 27 (SUCCESSES: 25, FAILURES: 2)   ← backend/Dockerfile
Failures: 2 (UNKNOWN: 0, LOW: 1, MEDIUM: 0, HIGH: 1, CRITICAL: 0)
Tests: 27 (SUCCESSES: 25, FAILURES: 2)   ← frontend/Dockerfile
Failures: 2 (UNKNOWN: 0, LOW: 1, MEDIUM: 0, HIGH: 1, CRITICAL: 0)
Secrets: 0
```

| 严重度 | 条数 |
|---|---|
| high | 2 |
| low | 2 |
| **合计** | **4** |

| 规则 | 严重度 | 目标 | 含义 |
|---|---|---|---|
| `DS-0002` | **high** | `backend/Dockerfile`、`frontend/Dockerfile` | 没有 `USER` 指令，容器以 root 运行 |
| `DS-0026` | low | `backend/Dockerfile`、`frontend/Dockerfile` | 没有 `HEALTHCHECK` 指令 |

⚠️ **`vuln` 子扫描器没能执行**：`trivy-db`（漏洞库是个 OCI 制品）在本机网络上拉不下来。
两个可用源都试过，都是连接层失败，不是配置问题：

```
mirror.gcr.io  → dial tcp [2607:f8b0:400e:c00::52]:443: connectex: A connection attempt failed
ghcr.io        → stream error: stream ID 1; PROTOCOL_ERROR; received from peer
```

`security_scan.py` 因此**降级**为 `--scanners misconfig,secret --skip-db-update`
（这两个用的是内置策略，不需要 DB），并在报告里**显式打印"vuln 扫描未执行"** ——
否则那 4 条里没有依赖漏洞这件事会被读成"Trivy 说依赖没问题"。
依赖漏洞的结论以上面的 pip-audit / npm audit 为准。

---

## 5. 逐条定性：可修 / 不可修 / 打不到 / 误报

分类口径：

- **(a) 现在就能修** —— 有明确的非破坏性修复版本。（⚠️ **本任务不做依赖升级**，
  升级是独立且高风险的改动，见 §7。）
- **(b) 无修复可用** —— 上游没有发布修复版本。
- **(c) 在本应用中不可达 / 打不到** —— 代码路径不存在，或依赖的入口从未被调用。
- **(d) 误报** —— 公告与代码事实不符。
- **(?) 待验证** —— 路径存在，但没有实际构造过利用，**不假装已确认**。

### 5.1 Python（19 条）

| # | 包 · 公告 | 定性 | 依据 |
|---|---|---|---|
| 1 | `starlette` 7 条（含 3 条 high/CVSS 7.5） | **(a)** 但需连 `fastapi` 一起升 | 它就是 ASGI 框架本体，所有请求都经过它 —— 不存在"打不到"。修复版本分散在 0.47.2~1.3.1，而 `fastapi~=0.115.0` 把 starlette 钉在 `<0.47`，**要修必须先升 fastapi**。⚠️ 当前跑的是 starlette 1.6.0，不受影响 |
| 2 | `transformers` 5 条（最高 CVSS 8.0） | **(c)** | 全仓 `grep` 无 `import transformers`；它是 `sentence-transformers` 的传递依赖。相关公告都要求**加载攻击者控制的模型仓库/配置**，而本项目的模型来自运维配置 `settings.embedding_model = "BAAI/bge-m3"`（`app/config.py:181`），不由用户输入决定。攻击者要触发它，得先能改服务器的 `.env` —— 那时他已经在服务器上了 |
| 3 | `python-jose` 3 条 | **(c)** | ① 算法混淆（CVE-2024-33663）要求验证端接受多种密钥类型；本仓 `algorithms=[settings.jwt_algorithm]` 且 `jwt_algorithm = "HS256"`（`app/config.py:98`），是**单向钉死**的；② JWE "解压炸弹"（CVE-2024-33664 / CVE-2024-29370）需要走 `jose.jwe.decrypt`，而全仓只有 `from jose import JWTError, jwt` 两处 import，**从未引用 jwe**。⚠️ 当前跑的是 3.5.0 |
| 4 | `pydantic` 1 条（CVE-2024-3772，ReDoS） | **(?)** | 路径**确实存在**：`app/schemas/user.py` 的 `email: EmailStr` 用在注册/登录上，而注册接口未认证 —— 任何人都能提交邮箱字符串。修复版本 2.4.0 存在。**没有实际构造过 ReDoS 载荷**，所以标"待验证"而不是"可被利用"。⚠️ 当前跑的是 2.13.5，已修 |
| 5 | `pytest` 1 条（CVE-2025-71176） | **(c)** | 公告限定 **UNIX** 上的 `/tmp/pytest-of-{user}` 目录竞争；本机与 CI 的测试环境不构成"同机多用户"场景。且它是测试工具，不进生产 |
| 6 | `ecdsa` 0.19.2（Minerva 时序攻击，CVSS 7.4） | **(b) + (c)** | **(b)**：`fix_versions` 为空，上游 python-ecdsa 明确声明侧信道不在项目范围内、"no planned fix"。**(c)**：漏洞函数是 `ecdsa.SigningKey.sign_digest()`（**签名**路径；公告自己写了"签名校验不受影响"），而本应用只用 HS256（HMAC），**不做任何 ECDSA 签名** |
| 7 | `nltk` 3.10.3（CVSS 7.0） | **(b) + (c)** | **(b)**：无修复版本。**(c)**：全仓无 `import nltk`；它是 `sentence-transformers` 的传递依赖，只在后者某些评测工具里被用到，本项目不调用 |

Python 侧**没有发现误报 (d)**。

### 5.2 Node（11 条）

| # | 包 | 定性 | 依据 |
|---|---|---|---|
| 1 | `vitest` 2.1.9（critical，CVSS 9.8） | **(c)** | 公告触发条件是 **Vitest UI 服务器正在监听**。本项目的脚本是 `vitest run` / `vitest`，**从未使用 `--ui`**；CI 里跑的也是 `npm test` = `vitest run`。没有监听端口就没有这个攻击面 |
| 2 | `vite` 5.4.21（high） | **(c) 生产 / (a) 开发侧需注意** | 最严重的一条是 `server.fs.deny` 在 Windows 备选路径上被绕过（CVSS 7.5）—— 它只影响**开发服务器**，而 dev server 只在开发机上跑、不进产物。⚠️ 但它确实对"开发时浏览器访问了恶意站点"这一场景成立；这不是生产风险 |
| 3 | `browserslist` 4.28.2（high×2） | **(c)** | 由 `@babel/core`（`@vitejs/plugin-react` 的依赖）拉起，只在**构建期**执行。触发条件之一是加载不可信的 `browserslist-stats.json` —— 本仓库没有该文件 |
| 4 | `nanoid` 3.3.12（high×2） | **(c)** | 传递依赖：`postcss` → `nanoid`，构建期使用。两条公告都要求调用方传**负数或 0 的 size**；`postcss` 不这样做，本仓也不直接调用 nanoid |
| 5 | `postcss` 8.5.15（high） | **(c)** | 构建期 CSS 处理。触发条件是把**攻击者控制的 CSS 源**交给 postcss 且未设 `from` —— 本仓的 CSS 全部来自自己的源码树 |
| 6 | `react-router` / `react-router-dom` 6.30.4（moderate×2 包） | **(?)** | **唯一落在生产依赖上的发现。** 一条是 `<Link>`/`useNavigate` 里的反斜杠导致的开放重定向（GHSA-wrjc-x8rr-h8h6），另一条是 **SSR hydration** 的 `deserializeErrors` 构造器注入（GHSA-337j-9hxr-rhxg）。后半条**不可达**：本应用是纯客户端 SPA（`main.tsx` 用 `ReactDOM.createRoot`，无 hydration），根本不走 SSR。前半条：全仓 `navigate()` 的目标都由服务端下发的 id 拼成固定前缀（`/notes/${id}`、`/cards/${id}`），没有找到"由攻击者控制完整跳转目标"的数据流，因此不构成协议相对 URL；但**没有实际构造过利用**，标"待验证"而非"不可达" |
| 7 | `esbuild` 0.21.5（moderate） | **(c)** | 公告是"任意网站可向**开发服务器**发请求并读响应"（CVSS 5.3）。dev-only，同上 |
| 8 | `@vitest/mocker` / `vite-node` 2.1.9（moderate） | **(c)** | 二者都不是独立入口，是 vitest 的内部包；它们"命中"只是因为继承了 `vite` 的等级。测试期代码，不进产物 |
| 9 | `baseline-browser-mapping` 2.10.33（moderate） | **(c)** | `browserslist` 的传递依赖，构建期；公告是"非法输入导致进程终止"（DoS），输入来自本仓自己的 browserslist 查询，不是外部数据 |

Node 侧**没有发现误报 (d)**。

### 5.2b Trivy（4 条）

| # | 规则 | 定性 | 依据 |
|---|---|---|---|
| 1 | `DS-0002`（high）× 2：Dockerfile 没有 `USER` | **(a)**，但只在 build 镜像时才有意义 | 两份 Dockerfile 都会让容器以 root 运行。**本项目当前不跑容器**（本机直接 `uvicorn` + `vite`），所以它不是当下的生产风险；一旦按 `docker-compose.yml` 部署就成立。修法是在 Dockerfile 里加非 root `USER`（不是依赖升级，属独立改动） |
| 2 | `DS-0026`（low）× 2：没有 `HEALTHCHECK` | **(a)**，同上 | `docker-compose.yml` 里 backend 服务**已经在 compose 层配了 healthcheck**（第 21-26 行），所以这条在真实部署路径上部分被覆盖；frontend 服务则没有 |

Trivy 的 `secret` 扫描器**没有命中任何东西**（0 条）。
注意它会读 `backend/.env` —— 该文件在 `.gitignore` 第 23 行，不在版本控制中，
因此即使命中也不是泄露；`security_scan.py` 只记录规则名与行号，
**绝不把匹配到的密钥内容写进报告**。

### 5.3 汇总：真正需要人做决定的只有 1 组

把 §5.1 / §5.2 的定性加起来：

| 定性 | Python | Node | Trivy | 说明 |
|---|---|---|---|---|
| (a) 可修（但本轮**不修**） | 7 条（`starlette`，须连带升 fastapi） | 11 条（几乎都可修，2 组需跨大版本） | 4 条（Dockerfile 加固，非依赖升级） | 升级是独立批次，见 §7 |
| (b) 无修复 | 2 条（`ecdsa`、`nltk`） | 0 | 0 | 上游不会修 / 已明确不修 |
| (c) 打不到 | 12 条 | 9 条 | 4 条（当前不跑容器） | 代码路径不存在或依赖入口从未被调用 |
| (?) 待验证 | 1 条（`pydantic` ReDoS 路径） | 1 组（`react-router` 开放重定向） | 0 | 路径存在，未构造利用 |
| (d) 误报 | 0 | 0 | 0 | |

**没有任何一条被认定为"生产环境真实可利用"。** 但请注意这句话的边界：
它建立在 §5.1/§5.2/§5.2b 的**代码路径核查**上，不是建立在"跑通了 exploit"上。
其中 2 条明确标了"待验证"。

### 5.4 Trivy 在这个项目里值多少

Trivy 0.74.0 已装（`%LOCALAPPDATA%\Programs\trivy\trivy.exe`，
49.3 MB zip 解压出 164.3 MB 的 exe，装在仓库外）。
本项目的实际形态决定了它的价值边界：

- **镜像扫描目前无对象。** 仓库里确实有 `docker-compose.yml` 与两份 `Dockerfile`，
  但本项目当前是**本机直接跑**（FastAPI + Vite），不跑容器；
  `docker-compose.yml` 只在部署时才 build。所以"镜像 CVE"这一块
  **只有在你真的 build 镜像时才有东西可扫**。
- **实际出结果的是 `misconfig`**：4 条 Dockerfile 加固问题（§4.4 / §5.2b），
  这是 pip-audit 与 npm audit **完全看不到**的一类。
- **`secret` 跑了，0 条命中。**
- **`vuln` 跑不了**（trivy-db 拉不下来，见 §4.4）—— 这一块本来也只是
  把同样的依赖换个公告源复核一遍，pip-audit / npm audit 已经覆盖。

`security_scan.py` 检测到 `trivy` 时会自动跑
`trivy fs --scanners vuln,misconfig,secret`（跳过 `frontend/node_modules`、
`backend/data`、`.git`、`dist`）；未安装时**明确打印"未安装 —— 本项跳过（不是通过）"**；
DB 拉不下来时**降级为 config/secret 并显式声明 vuln 未执行**，而不是整项失败、
也不是静默报 0 条。

---

## 6. 为什么不把 `--fail-on high` 设成默认

现在这台机器上跑 `--fail-on high` 会**红灯**（Node 侧 4 条 high + 1 条 critical，
Python 侧 8 条 high + 1 条 critical）。默认就红会带来一个确定的后果：
CI 长期常红 → 有人加 `|| true` → 门禁等于不存在。

这个仓库已经为此付出过代价。`.github/workflows/ci.yml` 里对 `ruff format --check`
和 `prettier --check` 都写了 `continue-on-error: true`，注释里也写明了
"检索质量评测**刻意不放在 CI 里**，加了 `|| true` 又等于永不失败 —— 那种步骤只是装饰"。

所以本脚本的选择是：**默认如实报告 + 退出码 0，把阻断阈值留给使用方显式选择**，
但把"扫描器根本没跑起来"（退出码 2）**无条件**做成红灯 —— 因为那不是"发现了问题"，
而是"我们没有在做这件事"。

要在 CI 里真正落门禁，建议分两步：先修掉 Node 侧那 11 条（多数是同大版本内可修），
再把 `--fail-on high` 打开。**在那之前，`--fail-on high` 是装饰。**

---

## 7. 本轮**没有**做的事（刻意的）

- **没有升级任何依赖。** 修 `starlette` 要先升 `fastapi`，修 `vite`/`vitest`
  要跨大版本（vite 5→8、vitest 2→5）—— 这些都会改动应用行为与构建产物，
  与 5.6 CSS 级联那类"文本差集看不见的损失"是同一类风险。
  它们属于独立的、需要单独验证的批次。
- **没有改 `requirements.txt` 的版本策略。** `~=` 锁次版本导致陈旧，
  但把 `~=` 改成不锁或改锁大版本同样会影响可复现性，是需要决策的取舍。
  §4.1 记下了事实，决策留给维护者。
- **没有修 `npm audit` 的换源问题**（只在脚本里做了回退）。
  更彻底的做法是在 CI 里显式指定 registry，或在 `.npmrc` 里为 audit 单独设源 ——
  那会改动 CI 的联网行为，超出本任务范围。
- **没有把扫描接进 `.github/workflows/ci.yml`。**
  接了就必须同时决定阈值（见 §6），而"先看清家底再定阈值"是本轮的顺序。
- **没有加固两份 Dockerfile**（`DS-0002` 加非 root `USER`、`DS-0026` 加 `HEALTHCHECK`）。
  这不是依赖升级，但会改变容器镜像与启动行为；而本项目当前不跑容器，
  改了也无法在本轮被验证 —— 属于独立批次。

---

## 8. 覆盖不到的部分（"跑过了"≠"安全"）

1. **无 CVE 数据 ≠ 无漏洞。** 三个工具都只是把依赖版本与**公开公告库**比对。
   业务逻辑漏洞、越权（IDOR）、SSRF、认证绕过、SQL 注入 —— 它们一条都看不见。
   这一层靠 `backend/tests/`（1025 个用例）与 §5 里的人肉可达性核查。
2. **不做可达性分析。** 工具说"这个版本的这个函数有洞"，它**不判断你有没有调用那个函数**。
   §5 的全部 (c) 判定都是人工 grep 得出的，不来自工具。
3. **不打补丁、不验证利用。** 本脚本不发起任何攻击性请求。
4. **只覆盖清单里的依赖。** 手工 `pip install` 进环境、但没写进 requirements 的包不会被审计到；
   反过来，写进 requirements 但从未安装的包会被审计（这正是 §4.1 那 19 条的来源）。
5. **Trivy 的 `secret` 扫描会读 `backend/.env`。**
   该文件在 `.gitignore` 里（第 23 行 `.env`），不在版本控制中，因此即使命中
   **也不是泄露**。`security_scan.py` 刻意只记录规则名与行号，
   **绝不把匹配到的密钥内容写进报告**。
6. **没有基线/白名单机制。** 今天的 34 条（Python 19 + Node 11 + Trivy 4）全部如实报出；
   如果将来要长期跑，需要一份"已评审、暂不修"的抑制清单，
   否则每周都会重新读一遍同样的那 11 条。
7. **Trivy 的 `vuln` 扫描器在本机跑不了**（trivy-db 是 OCI 制品，两个源都不通，见 §4.4）。
   这不是脚本的问题，是网络的问题；降级后 config/secret 仍然可用。

---

## 附：本次运行的环境事实

| 项 | 值 |
|---|---|
| 仓库 HEAD | `182ebbd`（工作区当时干净） |
| 操作系统 | Windows |
| Python | 3.10.21（`C:\Users\admin\anaconda3\envs\mineru_env\python.exe`） |
| pip-audit | 2.10.1 |
| node / npm | v22.22.3 / 10.9.8 |
| npm registry（项目配置） | `https://registry.npmmirror.com/`（无 audit 端点） |
| 审计用 registry | `https://registry.npmjs.org/`（自动回退） |
| Trivy | 0.74.0（`%LOCALAPPDATA%\Programs\trivy\trivy.exe`） |
| Trivy 漏洞库 | **拉不下来**：`mirror.gcr.io` 连接被拒、`ghcr.io` 流中断（PROTOCOL_ERROR） |

### 三个必须留在脚本里的环境坑

1. **Windows + 中文 locale 下 `pip-audit` 直接崩。**
   `pip-requirements-parser` 按 `locale.getpreferredencoding()`（本机 GBK）
   解码 requirements 文件，而两份 requirements 是 UTF-8 且带中文注释：
   ```
   UnicodeDecodeError: 'gbk' codec can't decode byte 0x8e in position 10
   ```
   修法不是去删注释，而是给子进程加 `PYTHONUTF8=1`（见 `_scanner_env()`）。
   本文件记录此事，是因为"把中文注释删了"这种修法会让下一个人再把中文加回来。
2. **控制台编码会把整个报告吞掉。** 扫描跑完之后 `print("✗")` 在 GBK 控制台上
   抛 `UnicodeEncodeError` —— 使用者看到 traceback 而不是结果。
   `_configure_console()` 的处理是：stdout 不是终端（管道/CI 日志）时显式用 UTF-8，
   是终端时保留终端编码、只把错误策略降级为 `replace`。
3. **`npm audit` 在镜像源上返回 404 而不是"没问题"。**
   脚本靠识别 `NOT_IMPLEMENTED` 自动回退官方源。**不要**把这段回退删掉换成
   `|| true` —— 那会把"审计没跑"变成"审计通过"，是本仓库最忌讳的一类改法。
