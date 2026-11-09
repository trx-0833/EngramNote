# 治理改造：无害批次（第 0 批）

> 建立时间：2026-11-06 23:10（本地）
> 执行依据：`EngramNote治理计划.md` 阶段一
> 批次性质：**只新增文件，不修改任何既有文件**
> 当前状态：**配置文件全部未启用**（依赖工具均未安装）；`scripts/verify.sh` 已实测可运行（`EXIT=0`，约 66s），但**未接入任何自动流程**（无 git 钩子、CI 未引用）。

---

## 1. 为什么先做这一批

治理计划里有些任务会改变现有行为（改 CI、改 hooks、改 lint 规则、改依赖），一旦出问题会干扰正在进行的开发。

这一批刻意只做**"加了也不生效"**的部分：

| 判定条件 | 本批是否满足 |
|---|---|
| 是否修改既有文件 | 否，全部为新增 |
| 是否会改变 CI 结果 | 否，CI 未引用这些文件 |
| 是否会改变 agent 行为 | 否，`AGENTS.md` / `.trae/` / MCP 均未触碰 |
| 是否需要安装依赖 | 否，工具未安装即不生效 |
| 是否可一键撤销 | 是，删除本目录与新增文件即可 |

**核心保证**：把这一批全部删掉，仓库行为和现在完全一致。

---

## 2. 刻意避开的三处

| 避开对象 | 原因 |
|---|---|
| `docs/**.md` | `frontend/scripts/doc-link-check.mjs` 递归扫 `docs/`，且 `ci.yml:291` 会跑 `npm run docs:links`。新文档链接不合规会让 CI 变红 |
| `AGENTS.md` | 被 `.git/info/exclude:14` 排除；且其他 agent 会话可能正在读它，改写会让其上下文失效 |
| `.gitignore` | 当前处于未提交修改状态（`M .gitignore`），可能正在被另一个会话编辑 |

另外**不做 git add / commit**：只创建 untracked 文件。理由——提交会改变 index 与历史，若另一会话正在准备提交，可能交错。

---

## 3. 目录结构

```
_governance/                       ← 本批新增，不被任何脚本扫描
├── README.md                      ← 本文件
├── 01-启用清单.md                 ← 每个文件怎么启用、怎么回滚
├── 02-改动记录.md                 ← 逐项记录：文件、性质、影响面、验证
├── 03-验证结果.md                 ← 创建后跑的回归验证输出
└── skills-draft/
    └── code-review/SKILL.md       ← skill 草稿（不放到根 skills/，避免被自动加载）

scripts/                           ← 本批新建目录（根目录，不被 lint 扫）
├── verify.sh                      ← 本地验证入口（未接入任何流程）
├── backup.ps1                     ← 备份脚本（未注册计划任务）
└── risk.mjs                       ← 风险打分（未被调用）

lefthook.yml                       ← 新增，未启用（lefthook 未安装）
.semgrep.yml                       ← 新增，未启用（semgrep 未安装）
.gitleaks.toml                     ← 新增，未启用（gitleaks 未安装）
stryker.config.json                ← 新增，未启用（stryker 未安装）
```

---

## 4. 未启用的双重证据

**证据一：工具未安装**
```
lefthook       not installed
gitleaks       not installed
semgrep        not installed
stryker        not installed
syft           not installed
pre-commit     not installed
```

**证据二：`.git/hooks` 无自定义钩子**
```
（空——只有 *.sample）
```
即使往 `.git/hooks` 里放脚本也不会自动执行，因为 lefthook 需要通过 `lefthook install` 才会写入钩子。

---

## 5. 与治理计划的对应关系

| 本批文件 | 对应计划任务 | 计划里的启用时机 |
|---|---|---|
| `_governance/*` | 全程 | 立即（文档） |
| `scripts/verify.sh` | T1.4 | 阶段一（需先装 lefthook） |
| `lefthook.yml` | T1.4 | 阶段一（`lefthook install` 后生效） |
| `.gitleaks.toml` | T3.1 | 阶段三 |
| `.semgrep.yml` | T3.2 | 阶段三 |
| `stryker.config.json` | T2.2 | 阶段二 |
| `scripts/backup.ps1` | T4.4 | 阶段四 |
| `scripts/risk.mjs` | T5.1 | 阶段五 |
| `skills-draft/code-review` | T1.3 | 阶段一（确认无冲突后移入 `skills/`） |

**注意**：本批文件放好了，但**不改变计划的时间表**。启用仍按阶段推进——文件就位只是省了那时的创建时间。

---

## 6. 如何确认本批真的无害

```powershell
cd D:\engramnote

# 1) 只应看到新增的 untracked，没有 M（修改）
git status --porcelain

# 2) 文档检查不应扫到 _governance/
cd frontend; npm run docs:links; cd ..

# 3) 后端 lint 不应扫到根 scripts/
cd backend; python -m ruff check app tests scripts; cd ..

# 4) 前端 lint 不应扫到根 scripts/
cd frontend; npm run lint; cd ..
```

四条全通过 = 无害。实测输出见 `03-验证结果.md`。

---

## 7. 撤销方式

```powershell
cd D:\engramnote
Remove-Item _governance -Recurse -Force
Remove-Item scripts -Recurse -Force
Remove-Item lefthook.yml, .semgrep.yml, .gitleaks.toml, stryker.config.json -Force
git status --porcelain     # 应回到只有 " M .gitignore"
```

因为全部是 untracked 新增，删除即彻底复原，**不涉及 git 历史**。
