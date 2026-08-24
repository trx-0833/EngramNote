# 工具链首轮检查报告(ruff + eslint + prettier)

> 日期:2025-06 · 工具版本:ruff 0.16.4 / eslint 10 / prettier 3.9
> 原则:本次**只检查、未修改任何业务代码**(33/39 个前端文件曾因误操作被 prettier 格式化,已全部 git restore 还原)。
> 原始输出:`docs/ruff-report.txt`、`docs/eslint-report.txt`、`docs/prettier-report.txt`。

## 一句话结论

三个工具共发现 **266 个问题**:后端 146(ruff)、前端 81(eslint)、格式不统一 39 个文件(prettier)。其中排查出 **1 个潜在运行时 bug** 与 3 处"低质量但能跑"的代码——这正是工具的价值:把"AI 写的代码能不能用"从玄学变成清单。

---

## 一、后端 ruff(146 个问题)

### 按规则分布

| 规则 | 数量 | 含义 | 举例 |
|---|---|---|---|
| F401 | 68 | 导入了但从未使用 | `cleaning.py:24 typing.Dict/List`、`fastapi.status` 等一整批死导入 |
| B904 | 37 | except 里 raise 时丢了原异常上下文 | `assessment.py:40` `raise HTTPException(...)` 应写 `from e` |
| E712 | 11 | `== True` 比较写法 | 应直接 `if is_correct:` |
| F841 | 6 | 变量赋值后从未使用 | — |
| E741 | 5 | 模糊变量名(l / I / O) | — |
| E402 | 5 | import 不在文件顶部 | 如函数内部 import(历史遗留"延迟导入"习惯) |
| F821 | 3 | **引用了未定义的名称** | ⚠️ 见下方专项 |
| B023 | 3 | 循环变量被闭包捕获 | — |
| B007 | 3 | for 循环变量未使用 | — |
| F541 | 3 | 无占位符的 f-string | `config.py:352 f"filesystem://"`(之前审查已点名) |
| B905 / E721 | 2 | zip 缺少 strict / type() 比较 | — |

### ⚠️ 专项:F821 未定义名(3 处,逐一定性)

1. **`tasks/embedding_tasks.py:229` —— 潜在运行时 NameError【需修复】**
   嵌套函数 `_search_note_collection` 内引用 `EmbeddingService().loaded_model_name`,但该作用域只导入了 `VectorStore`;
   模块级也没有导入。当集合元数据与当前模型不一致、走到这条日志分支时,`EmbeddingService` 未定义 → NameError。
   (函数内 195 行是 `VectorStore` 的导入,`EmbeddingService` 只在别的函数里导入过。)
   **修复**:在该函数内补 `from ..services.embedding_service import EmbeddingService`,或改用已持有的共享实例。

2. **`services/note_service.py:857/877` —— 潜在,暂可不动**
   返回注解 `-> NoteAnnotation` 依赖 `from __future__ import annotations` 的字符串化才不报错;
   函数体内都有局部导入,运行正常。风险仅在将来有人调用 `typing.get_type_hints()` 时浮现。可选修复:顶部统一导入。

### 自动修复能力

- F401(68 个)、F841、E741、F541 等大多数可由 `ruff check . --fix` 安全自动修(删除死导入是无风险的);
- B904(37 个)需要逐个看(加 `from e`,AI 生成时普遍丢失);
- B023 需要人工理清闭包意图。

---

## 二、前端 eslint(81 个问题:35 error + 46 warning)

### 按规则分布

| 规则 | 数量 | 级别 | 含义 |
|---|---|---|---|
| `@typescript-eslint/no-explicit-any` | 37 | warning | 显式 any(后端 snake_case 字段的临时类型,已降级为警告) |
| `react-hooks/exhaustive-deps` | 9 | error/warn | useEffect 依赖数组缺项——**React 生命周期 bug 的温床** |
| `react-hooks/set-state-in-effect` | — | error | 在 effect 里同步 setState(新版 react-hooks 规则) |
| `@typescript-eslint/no-unused-vars` | 5 | error | `err` 定义未使用等 |
| `no-useless-escape` / `preserve-caught-error` / `no-useless-assignment` / `prefer-const` | 各 1-2 | error | 无用转义、捕获后覆盖错误、无用赋值、应改 const |

### 与之前人工审查的呼应

- `CardDetail.tsx:28` `exhaustive-deps: missing dependency: 'fetchCard'` —— 正是之前审查点名的问题模式;
- `KnowledgeGraph.tsx:220` `drawMinimap` 依赖缺失 —— 40KB 大组件里的典型;
- 多处 `err defined but never used` —— AI 写 catch(err) 却不用的通病。

---

## 三、前端 prettier(39/42 个文件格式不统一)

`prettier --check` 报告 39 个 src 文件不符合统一格式(几乎全部文件),包括:
- 分号/引号风格漂移(`client.ts` 与 `knowledge.ts` 风格不一致);
- `import` 语句夹在文件中部(如 QuestionSets.tsx 的 `import ErrorDisplay`);
- 长行、缩进不统一(`global.css` 2906 行全量重排)。

**性质说明**:纯格式问题,不影响运行;但会让 git diff 充满噪音、读代码费神。
**处置**:跑 `npm run format` 一次性统一即可,建议与"拆分大文件"等真正重构**分开提交**(先 format 一次,再重构,避免 diff 混淆)。

---

## 四、工具安装与使用方式(已配置完成)

### 安装位置(遵循最小依赖原则)

| 工具 | 环境 | 用途 | 新增内容 |
|---|---|---|---|
| ruff 0.16.4 | mineru_env(用户指定) | 后端检查+格式化 | `backend/ruff.toml`(仅 B008 豁免,其余默认核心集) |
| eslint 10 | frontend devDependencies | 前端问题检查 | `eslint.config.js`(基础+TS+react-hooks,+prettier 互斥关闭) |
| prettier 3.9 | frontend devDependencies | 前端格式化 | `.prettierrc.json`(semi/singleQuote/printWidth 100) |

注:前端新增 91 个传递依赖包(eslint 生态常见,业界标准做法);后端ruff 为单二进制,零 Python 依赖。因 npm 默认缓存目录无写权限,本次用项目内 `frontend/.npm-cache`(已加入 .gitignore),如需恢复全局缓存可删该目录后重装。

### 常用命令(已加入 package.json scripts / backend 对照)

```bash
# 后端(backend/ 目录)
python -m ruff check .           # 检查问题
python -m ruff check . --fix     # 自动修复(F401 等安全项)
python -m ruff format .          # 统一格式

# 前端(frontend/ 目录)
npm run lint                     # eslint 检查
npm run lint:fix                 # 自动修复安全项
npm run format:check             # prettier 检查格式
npm run format                   # prettier 统一格式
```

### 建议的落地节奏(避免 diff 噪音)

1. 本轮仅保留"安装 + 配置 + 报告",**不批量改代码**;
2. 下次做真正的逻辑重构时,顺手在独立提交里 `--fix` 安全项(F401 等),其余人工修;
3. 将 `ruff check` / `npm run lint` 纳入日常提交前的自查(< 2 秒)。

---

## 五、下一步建议

- **必须修(1 项)**:`embedding_tasks.py:229` 的 `EmbeddingService` 未导入(NameError 风险);
- **建议批量自动修**:后端 F401/F841/E741/F541 等 ~80 项(`ruff --fix`,零风险);
- **需要人工过一遍**:B904(37 项,补 `raise ... from e`)、前端 exhaustive-deps(9 项,涉及定时器清理类真实 bug 面);
- **格式统一**:`npm run format` + `ruff format` 各一次,单独提交。

是否现在执行"自动修 + 格式统一"?还是保持只检查,等你确认节奏?

---

## 六、修复执行结果(2025-06 完成)

> 本轮完成上述全部问题修复,当前状态:`ruff check app` **All checks passed**;`npx tsc --noEmit` 通过;`npm run lint` **0 errors, 0 warnings**。安全测试集 29 通过(6 个失败为**改动前已存在**的测试断言过时,经 git stash 基线验证与本次修复无关)。

### 后端(146 → 0)

| 规则 | 处理 |
|---|---|
| F401(68) | `ruff --fix` 自动删除死导入;5 处"依赖可用性探测"导入(`asr/__init__.py`、`intake.py`)加 `# noqa: F401` 注释保留 |
| B904(37) | 全部补 `raise ... from e`(HTTPException 响应体不含 __cause__,安全) |
| E712(11) | 均位于 SQLAlchemy 查询表达式,改用 `.is_(True/False)`(平等语义,方言安全) |
| F821(3) | **①`embedding_tasks.py:229` 真实 NameError 已修**(补 EmbeddingService 导入);②`note_service.py` 注解引用补顶部导入 |
| E741(5) | `l` → `line` 重命名 |
| E402(5) | 模块级 import 移至顶部(验证无循环导入) |
| B023(3) | **行为修正**:converter 线程闭包默认参数绑定,修复超时残留线程污染下一迭代的晚绑定 bug |
| B905(1) | `zip(strict=True)`,调用方均为同源等长向量 |
| E721(1) | `cast_type is int` |
| F841/F541/B007 | 删除死赋值/无意义 f-string/未用循环变量 |

### 前端(81 → 0)

| 规则 | 处理 |
|---|---|
| no-unused-vars(5) | `catch (err)` → `catch {` |
| no-useless-escape(2) | 正则字符类中 `\$` → `$` |
| preserve-caught-error(2) | throw 附加 `cause`(ES2020 兼容写法) |
| set-state-in-effect(18) | 2 处真修(初始 state 已覆盖,删冗余 setState);16 处数据获取/订阅类 effect 加 `eslint-disable-next-line` + 理由注释(遵循"不确定安全不重构"守则) |
| refs/purity/impure(4) | latest-ref 模式与剩余天数时钟读取,加豁免注释 |
| cannot-access-variable-before-declared(3) | 纯函数 `groupByNote` 提为模块级(两份);`loadNotes` 用 `useCallback` 前置 |
| exhaustive-deps(9) | 4 处 `useCallback` 包裹修复;**顺带修复 NoteDetail blob URL 泄漏真 bug**(cleanup 闭包捕获旧 state 永不 revoke → 局部变量持有);4 处加载类/闭包依赖加豁免注释 |
| no-explicit-any(37) | 定义 `AssessmentScores`/`QuizAnswerItem`/`GraphOperationResult` 接口 + 图 API 类型化;`catch (e: any)` → `unknown` 窄化;KnowledgeGraph 的 21 处多余 `as any` 直接删除(ForceGraphLink 接口本已声明字段),`graphRef` 用库的 `ForceGraphMethods` 类型 |

### 豁免清单(记录在案,非隐藏债务)

1. `useAdhdReader.ts` 2 处渲染期 ref 同步(latest-ref 模式,推迟到 effect 会引入时序 bug);
2. `QuickReview.tsx` 渲染期读 `submittingRef`(提交锁,由 submit setState 兜底);
3. KnowledgeGraph/NoteDetail 等 4 处加载类 effect 的空依赖(effect 由业务参数驱动,补依赖会重复触发);
4. `LearningGoals.tsx` `Date.now()` 剩余天数(展示性时钟读取)。
5. 后端 5 处依赖探测导入的 F401 noqa。

### 说明

- 配置:`backend/ruff.toml`(规则 E4/E7/E9/F/B,豁免 B008、5 处 noqa);`frontend/eslint.config.js`(基础+TS+react-hooks+prettier 互斥)。
- 前端 `npm run lint` / `format:check` 已写入 package.json scripts。
- **未执行格式统一**(ruff format / prettier --write):按计划独立提交,避免污染逻辑修复的 diff——如需执行告知即可。
- 沙箱环境限制说明:`npm run build` 的 esbuild 阶段 spawn 被沙箱拦截(EPERM),tsc 编译阶段已通过,非代码问题。