# MinerU / ASR 后端与版本决策

> **状态：活文档** ｜ 最后核对：2026-10-21 ｜ 权威性：本主题的现状依据
>
> 本文记录**一次实测**得出的后端取舍与版本策略，以及随之修掉的 6 个缺陷。
> 代码引用带 `文件:行号`；结论与代码冲突时以代码实测为准（`docs/README.md:22-26`）。

---

## 0. 结论先行

| 问题 | 结论 |
|---|---|
| **留 pipeline 还是 VLM？** | **留 VLM（云端 `vlm-http-client`）**，放弃本地 `pipeline` |
| **本地那 4.6 GB MinerU 模型** | 建议回收（`PDF-Extract-Kit-1.0` + 两个 `MinerU2.5` VLM）—— 需要你确认后我再删 |
| **mineru 版本** | **保持 3.4.5，暂不升 4.0.12**（4.0 是改版，升级要动集成代码） |
| **qwen-asr 版本** | **保持 0.0.6**（已是 PyPI 最新），只修一行写错的 pin |
| **本地 pipeline 现在能跑吗** | ❌ **跑不了** —— 依赖缺失，实测在 `torchvision` 处炸掉 |

---

## 1. 本机硬件事实（决定取舍的前提）

| 项 | 实测值 |
|---|---|
| torch | **2.14.0+cpu** —— `torch.cuda.is_available() == False` |
| GPU | NVIDIA RTX 3050 Ti Laptop（4 GB VRAM）—— **当前 torch 用不上** |
| 内存 | 总 15.7 GB，常用可用 **2.6 ~ 4 GB** |
| mineru | 3.4.5（PyPI 最新 4.0.12） |
| qwen-asr | 0.0.6（PyPI 最新 0.0.6） |
| modelscope / transformers | 1.40.0 / 4.57.6 |

> **关键**：这是一台**没有可用 CUDA 的机器**。本地 VLM（1.2B）在 CPU 上会非常慢，
> 而 `pipeline` 的小模型虽然能在 CPU 上跑，也要几个 GB 内存 —— 实测清洗阶段已经
> 因为「可用内存 1.8 GB < 4.0 GB」触发了嵌入模型降级（见
> `docs/ux-walkthrough-report-20261021.md` §2.8）。内存是本机的真实瓶颈。

---

## 2. 为什么留 VLM、放弃 pipeline

### 2.1 决定性证据：本地 pipeline **现在就是坏的**

用项目自己的调用方式（`app/services/mineru/converter.py:408-457`）实测跑一次
`backend='pipeline'`，2 页 PDF：

```
File ".../mineru/backend/pipeline/model_init.py", line 9, in <module>
    from ...model.layout.pp_doclayoutv2 import PPDocLayoutV2LayoutModel
File ".../mineru/model/layout/pp_doclayoutv2.py", line 15, in <module>
    import torchvision.transforms.v2.functional as tvF
ModuleNotFoundError: No module named 'torchvision'
```

缺的不止一个 —— `mineru[pipeline]` 共声明 9 个依赖，本机缺 4 个：

| 依赖 | 本机 | 说明 |
|---|---|---|
| `torchvision` | ❌ 缺 | **第一个炸点** |
| `ftfy` | ❌ 缺 | |
| `shapely` | ❌ 缺 | |
| `pyclipper` | ❌ 缺 | |
| `PyYAML` / `torch` / `transformers` / `safetensors` / `onnxruntime` | ✅ 有 | |

### 2.2 官方文档的口径

- **云端 API 文档**把 `model_version` 的三个取值标为
  `pipeline`（默认）/ **`vlm`（推荐）** / `MinerU-HTML`
  —— 官方自己推荐 vlm（[mineru.net/apiManage/docs](https://mineru.net/apiManage/docs)）。
- 本项目实测走云端 **4 页 PDF 25 秒**完成，产出带 `<!-- page=N -->` 锚点的干净 Markdown
  （worker 日志 `转换开始: input.pdf | 后端=vlm-http-client`）。

### 2.3 逐项对比

| 维度 | `pipeline`（本地） | `vlm-http-client`（云端，**选它**） |
|---|---|---|
| 本机现在能跑 | ❌ 缺 4 个依赖 | ✅ 已实测跑通 |
| 本机算力 | 无 CUDA，纯 CPU 推理 | 不占本机算力 |
| 内存压力 | 数 GB（本机已是瓶颈） | 几乎为零 |
| 磁盘 | 2.4 GB 模型 + 约 2 GB pip | 0 |
| 官方推荐 | 云端 API 的**非**推荐项 | ✅ 推荐 |
| 离线可用 | ✅ | ❌ 需联网 + 额度 |
| 隐私 | ✅ 文档不出本机 | ❌ 上传到 mineru.net |

**唯一支持本地的是「离线 / 隐私」**。如果你有这方面的硬需求，正确做法**不是**修 3.x 的
pipeline，而是看 §4 的 4.0 升级 —— 4.0 默认走 ONNX CPU 小模型 + llama.cpp Vulkan VLM，
在这台机器上才谈得上可用。

---

## 3. 本次修掉的 6 个缺陷

| # | 缺陷 | 证据 | 处置 |
|---|---|---|---|
| 1 | **`requirements-asr.txt:24` 的 `qwen-asr>=0.1.0` 无法满足** —— PyPI 只有 0.0.1~0.0.6，`pip install -r requirements-asr.txt` **直接失败** | `pip install --dry-run` 报 `No matching distribution found for qwen-asr>=0.1.0` | 改为 `qwen-asr>=0.0.6` |
| 2 | **`requirements-pdf-local.txt` 只列了 `pyyaml` + `requests`**，漏掉 `torchvision`/`ftfy`/`shapely`/`pyclipper` —— 文件里写的安装命令**装不出能用的 pipeline** | 上面的 traceback | 补全依赖并标注废弃 |
| 3 | **`model_loader.configure_modelscope_models()` 只看目录存在就返回 `True`** —— 实测返回 `True` 之后 `do_parse` 立刻失败，是**假就绪** | 探针输出 `返回: True` → `do_parse 失败` | 加真实能力探测 |
| 4 | **`model_loader` 强制要求 pipeline 与 VLM 两个模型都在**，而 `converter.py:104-105` 只在 `backend == "pipeline"` 时调它 —— 只用一种后端也要背两份模型 | `model_loader.py:85-95` | 改成只校验当前后端需要的那个 |
| 5 | **`.env` 没有 `MINERU_BACKEND`** → 静默取默认值走云端，而本地 4.6 GB 模型一直闲置，用户无从察觉 | `.env` 键列表；worker 日志 | 决策文档 + `.env.example` 说明 |
| 6 | **pipeline 失败以 mineru 内部的裸 `ModuleNotFoundError` 抛出**，不告诉用户「该装什么」 | traceback | 调用前做预检，给可执行的中文提示 |

---

## 4. 版本策略

### 4.1 mineru：保持 3.4.5，暂不升 4.0.12

- **4.0 是改版**：官方快速入门写的是 `mineru-kit parse document.pdf --tier standard`、
  四档解析（Flash / Basic / Standard / Advanced）、文档库与 Agent 阅读
  （[官方快速入门](https://opendatalab.github.io/MinerU/zh/)）。
- 本项目走的是 **3.x 的 Python API**：`from mineru.cli.common import do_parse`
  （`converter.py:408-413`）。入口形态变了，升级**必须改集成代码并重跑验证**。
- ⚠️ **未验证**：我没有确认 4.0.12 是否仍保留 `mineru.cli.common.do_parse`。
  升级前请先在一份副本里 `pip download mineru==4.0.12 --no-deps` 并检查 wheel 内的模块清单。
- 另外 4.0 要求 Python `>=3.10,<3.15`（本机 3.10.21 满足），
  且官方建议 Python 3.12。

**结论**：把它当成一次**独立的、有计划的重构**（升级 + 改集成 + 重跑 PDF 全链路回归），
不要顺手升。在那之前 3.4.5 是能用的 —— 只要走云端 VLM。

### 4.2 qwen-asr：保持 0.0.6

0.0.6 就是 PyPI 最新（`pip index versions qwen-asr` → `LATEST: 0.0.6`），**无需更新**。
本地模型 `Qwen/Qwen3-ASR-0___6B`（1.8 GB）与 `silero-vad` 都在，
依赖（`torch` / `qwen-asr` / `soundfile`）也齐 —— ASR 这条链路**没有版本问题**，
本次没测只是因为没上传音视频。

---

## 5. 模型处置建议（**待你确认，我没有删任何文件**）

磁盘现状：ModelScope 缓存 8.4 GB + `backend/data/models` 4.5 GB。

| 模型 | 大小 | 建议 |
|---|---|---|
| `PDF-Extract-Kit-1___0` | 2 379 MB | **可删** —— pipeline 专用，而 pipeline 已放弃 |
| `MinerU2___5-Pro-2604-1___2B` | 2 220 MB | **可删** —— 云端 VLM 不用本地权重 |
| `MinerU2___5-2509-1___2B` | 2 216 MB | **可删** —— 旧版本，`model_loader.py:19` 期望的是 Pro-2604 |
| `Qwen/Qwen3-ASR-0___6B` | 1 793 MB | **保留** —— ASR 链路依赖它 |
| `silero-vad` | 4 MB | 保留 |
| `Xorbits/bge-m3` | 4 356 MB | 保留（**另有 2.2 GB 可回收**：`.bin` 与 `.safetensors` 同一份权重存了两遍） |
| `BAAI/bge-small-zh-v1___5` | 183 MB | 保留（降级兜底模型） |

合计可回收约 **6.8 GB**（MinerU 三件 6.8 GB；bge-m3 的重复权重另计 2.2 GB）。
按 `AGENTS.md` §6，**删文件必须你点头**，所以我只列清单。

---

## 6. 复现

```powershell
# 1) 复现「pipeline 跑不了」
cd D:\engramnote\backend
$env:MINERU_BACKEND='pipeline'; $env:MINERU_MODEL_SOURCE='local'
& "$env:USERPROFILE\anaconda3\envs\mineru_env\python.exe" data\_uxwalk\_mineru_probe.py

# 2) 复现「ASR 依赖装不上」
& "$env:USERPROFILE\anaconda3\envs\mineru_env\python.exe" -m pip install --dry-run --no-deps -r requirements-asr.txt

# 3) 看当前实际走的后端（worker 日志里搜「后端=」）
Select-String -Path data\_uxwalk\logs\worker.log -Pattern "后端="
```
