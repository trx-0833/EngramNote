<!--
  提交 PR 前请把下面**适用**的勾选项打上勾。
  勾选项与 CONTRIBUTING.md 第 4 节的门禁清单一一对应，命令出处都写在括号里。
  不适用的项请写"不适用 + 原因"，不要留空。
-->

## 这个 PR 做了什么

<!-- 一到三句话。说清"为什么改"，而不只是"改了哪些行"。 -->

## 关联 issue

<!-- 例如 Closes #123；没有就写"无" -->

## 改动面（勾选后按对应清单自查）

- [ ] 后端代码 / 测试 / 脚本
- [ ] 前端代码
- [ ] 后端接口契约（`openapi.json`）
- [ ] 数据库 schema 或迁移
- [ ] 依赖清单（`requirements*.txt` / `pyproject.toml` / `package.json`）
- [ ] 文档（含 README / docs / 本 PR 模板提到的那些文件）
- [ ] CI 或部署配置

## 门禁自查

### 后端（在 `backend/` 下，改动涉及后端时必跑）

- [ ] `python -m ruff check app tests scripts` （`ci.yml:56/61/68`，阻断）
- [ ] `python scripts/gen_env_example.py --check` （`ci.yml:87`，阻断）
- [ ] `python scripts/dump_openapi.py --check` （`ci.yml:129`，阻断；干净环境需带 `APP_ENV=dev` 与假 `JWT_SECRET_KEY`，见 `ci.yml:126-128`）
- [ ] `python -m pytest -q` （`ci.yml:154`，阻断；**离线**，网络被 `backend/tests/conftest.py` 挡住）
- [ ] `python -m ruff format --check app tests` （`ci.yml:146`，**建议性**，`continue-on-error: true`）

### 前端（在 `frontend/` 下，改动涉及前端时必跑）

- [ ] `npm ci` （`ci.yml:205`）
- [ ] `node -e "require('jsdom')"` （`ci.yml:216`，阻断；Node 版本不对时让失败指名道姓）
- [ ] `npm run lint` （`ci.yml:219`，阻断）
- [ ] `npm run gen:api` 且 `git diff --exit-code -- src/api/generated/schema.ts` 为零 diff （`ci.yml:233-235`，阻断）
- [ ] `npm run format:check` （`ci.yml:254`，阻断）
- [ ] `npm test` （`ci.yml:266`，阻断）
- [ ] `npm run build` （`ci.yml:269`，阻断）
- [ ] `npm run e2e` （`ci.yml:309`，阻断）
- [ ] `npm run a11y` （`ci.yml:338`，**自 2026-09-23 起阻断**；注意 `README.md:263` 仍写作"建议性"，以 CI 为准）

> 完整命令与逐条出处见 `CONTRIBUTING.md` 第 4 节。

## 契约与迁移

- [ ] **改了后端接口** → 已重跑 `python scripts/dump_openapi.py` 与 `npm run gen:api`，
      并把 `backend/openapi.json`、`frontend/src/api/generated/schema.ts` **一起**提交
      （否则前端会按过期契约编译，见 `ci.yml:89-110`）
- [ ] **改了 schema** → 走的是 `init_db()` + `_migrate_sqlite()` 的**前滚**通道
      （只加列、不删数据）；没有把破坏性操作放回启动路径
      （`backend/app/database.py:260-276`、`:390-399`）；需要用户做什么已写进 `UPGRADING.md`
- [ ] **改了依赖** → 同步了 `backend/requirements*.txt` 与 `pyproject.toml`
      （一致性由 `backend/tests/test_packaging_metadata.py` 断言，见 `pyproject.toml:30-31`）
- [ ] **用户可见的行为变化**（配置默认值、接口语义、错误码）→ 已写进 `CHANGELOG.md`，
      必要时写进 `UPGRADING.md` / `SECURITY.md`

## 请确认

- [ ] 结论都带 `文件:行号`（本项目不接受"建议加强"这类空话，`AGENTS.md:34`）
- [ ] 没有引入 `AGENTS.md:13-18` 里**已否决**的方向（外部中间件 / 第二套迁移工具 / 恢复旧存储 / 容器化承诺）
- [ ] 没有提交 `backend/.env`、`backend/data/`、真实笔记或真实语料
      （真实 PDF 语料走 `TEST_PDF_PATH`，见 `CONTRIBUTING.md` 第 6.1 节）
- [ ] 纯格式重排与逻辑修复**没有**混在同一个提交里（`ci.yml:131-145`）
- [ ] 如果这个 PR 让某处文档与现实不符，**文档也一起改了**（或在本 PR 描述里明确写出未改的原因）
