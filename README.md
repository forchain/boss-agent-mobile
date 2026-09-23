# boss-agent-mobile

> 🤖 **Android 移动端 Boss 直聘智能求职与自动化交互 Agent**
>
> 专为 Android 移动端打造的智能求职交互框架：基于真实移动设备/模拟器环境、拟人化贝塞尔手势（Bézier Gestures）轨迹与多维筛选流程，实现职位智能检索、HR 沟通与全自动求职流。

<div align="center">

https://github.com/user-attachments/assets/ec8a2864-54fe-4e13-8f33-5ad4e7104f11

*Demo: Boss 直聘 Android 移动端自动化求职交互全流程*

</div>

---

## 🎯 核心亮点：AI 个性化破冰打招呼系统

> **从「千篇一律群发」到「千人千面定制」—— 让每一句招呼都精准命中 JD 痛点。**

<div align="center">

<img src="https://github.com/user-attachments/assets/da1afdde-3d4d-440f-83b7-9219616c1d35" width="720" alt="AI 岗位契合度评估与破冰招呼语" />

*AI 岗位契合度评估 × 个性化破冰打招呼 × 人机协同微调*

</div>

### 🔥 这是什么？

Boss 直聘等求职平台的招呼语上限通常只有 **300 字**，却决定了 HR 是否打开你的简历。传统自动化工具只会群发千篇一律的模板——本项目通过大模型深度理解 JD 与候选人简历，**为每个岗位量身定制反套路、高命中率的破冰招呼语**。

### ✨ 核心能力

| 能力 | 说明 |
|------|------|
| **🧠 AI 岗位契合度评估** | 大模型逐项解析 JD 核心诉求，输出 0-100 量化匹配分与维度分析 |
| **✍️ 定制破冰打招呼语** | 结合 JD 痛点 + 候选人简历亮点，生成 80-150 字防套路招呼文案 |
| **💬 人机协同微调** | 输入自然语言批注（如「突出英语能力」），AI 立即重写并展示 Before/After 对比 |
| **🧩 快捷建议模板** | 一键触发常见优化方向：突出跨国协同、高并发实战、开源 Agent 落地等 |
| **🧬 单一活态 Greeting Prompt** | 结合批注与微调重写，LLM 全文重写打磨并提供 Before/After Diff 审核，确认后沉淀为唯一长期活态提示词，后续招呼生成自动生效 |

### 🔄 工作流

```
JD 输入 → AI 契合度评分 → 自动生成招呼语草稿
                                    ↓
                        候选人提出微调批注 ← 快捷建议
                                    ↓
                      AI 即时重写 + Before/After 对比
                                    ↓
            采纳 → 全文打磨并审核 Diff 对比 → 沉淀为单一活态 Greeting Prompt
```

---

## ✨ 更多核心特性 (More Key Highlights)

- **📱 移动端原生自动化**：基于 Appium 与 Android 原生控件交互，相比 Web 爬虫具备更低风控限制与更完整的移动端专属功能。
- **🎯 拟人化手势与防检测**：内置三次贝塞尔曲线手势引擎（Bézier Gesture Synthesis）与高斯随机停顿，完美模拟真人滑动、点击与触控微抖动轨迹。
- **⚡ 双锚点疾速检索与回退自愈**：仅依赖搜索框与搜索图标双状态识别，无冗余多 XPath 探测；遇到偶发弹窗或异常页面状态通过硬件 Back 键闭环快速恢复，检索耗时从 148s 大幅压缩至 7s。
- **🛑 已沟通识别、复投冷却与直招同企避嫌**：智能检测详情页“继续沟通”按钮状态，避免重复进入沟通；直招同企岗位自动避嫌（防止一企多投被 HR 系统判定不专业，猎头渠道豁免）；支持复投冷却窗口（`communication_cooldown_days`）与状态重置；今日打招呼配额精准与实际投递解耦。
- **📊 全栈 Web 控制台与实时遥测**：基于 SvelteKit + PocketBase Realtime (SSE)，提供任务状态流、职位流看板、候选人画像管理、SavedSearch 策略库与实时运行日志流。
- **🔍 多维多级筛选体系**：支持城市、职位关键词、薪资区间、学历要求以及多级行业分类（如互联网/金融/医疗）等精确筛选与白名单放宽机制。
- **🛡️ 弹窗与打扰拦截器**：通用弹窗自动拦截体系（自动处理青少年模式、系统升级、定位授权等各类偶发弹窗），保障无人值守持续运行。
- **🩺 全栈健康诊断 CLI (`./doctor.sh`)**：一键自动化检测 PocketBase、Web 控制台、Python Worker、Appium Server、Android 模拟器与 LLM 配置，提供针对性一键修复引导。
- **📦 零历史膨胀媒体管理**：演示视频通过 GitHub 附件（`user-attachments`）独立托管，主仓库体积保持极致轻量、秒级克隆与同步。

---

## 🏗️ 架构与项目结构 (Project Structure)

- `src/droid_agent_core/`: 通用、业务解耦的 Android 移动自动化框架，包含贝塞尔手势合成、统一定位器管理、弹窗拦截器、UI 遥测与 LLM 决策接口。
- `src/boss_agent/`: Boss 直聘业务领域实现（Page Object 模型、双锚点检索工作流、候选人结构化画像、LangGraph 筛选流水线、PocketBase 状态流适配器与守护进程 Worker）。
- `web/`: 现代化 SvelteKit Web 管理控制台（职位流看板、实时任务管理、候选人结构化简历编辑、SavedSearch 策略库与系统配置面板）。
- `doctor.sh`: 系统全栈健康检查与自动诊断修复工具（[System Doctor](CONTEXT.md)）。
- `pocketbase.sh` (`pb.sh`): PocketBase 状态流持久化与实时 SSE Broker 服务管理脚本。
- `emulator.sh` (`emu.sh`): 专用 Android Virtual Device (AVD) 模拟器生命周期管理工具。
- `appium.sh` (`app.sh`): 独立 Appium 自动化服务启动与日志挂载工具。
- `web.sh`: Web 控制台一键启动管理工具。
- `run.sh`: 真实设备/模拟器冒烟与 Worker 任务执行入口。
- `config/`: 声明式系统配置（`greeting_prompt.local.md`, `settings.local.yaml`, `locators.yaml`）。
- `docs/adr/`: 核心架构决策记录（ADR 0001 - 0012，含决策总览索引 [docs/adr/README.md](docs/adr/README.md)）。
- `scripts/bootstrap.py`: 幂等式自动化环境初始化工具（JDK、Android SDK、AVD 模拟器、Appium 服务及 APK 安装）。
- `scripts/init_worktree.py`: Git Worktree 与本地共享配置初始化工具（自动同步 main 分支、创建隔离工作区并软链接共享配置）。

---

## 🚀 快速上手 (Quick Start)

### 1. 环境准备与依赖安装
```bash
# 快速初始化独立 Git Worktree（自动同步最新 main 并软链接本地配置）
./init_worktree.sh <feature_name>

# 安装 Python 依赖
uv sync --extra dev

# 自动化环境检查与安装（JDK, Android SDK, AVD, Appium）
uv run python scripts/bootstrap.py
```

### 2. 系统健康体检 (System Doctor)
```bash
# 一键诊断 PocketBase、Web 控制台、Python Worker、Appium、模拟器及 LLM 配置健康度
./doctor.sh
```

### 3. 启动基础设施与服务
```bash
# 启动 PocketBase 状态流 Broker 服务（后台守护模式）
./pocketbase.sh start --daemon

# 启动专用 Android 模拟器与 Appium 自动化服务（后台守护模式）
./emulator.sh start --daemon
./appium.sh start --daemon

# 启动 Web 管理控制台（访问 http://127.0.0.1:5173）
./web.sh
```

### 4. 运行自动化与测试
```bash
# 方式 A：通过 Web 控制台 (http://127.0.0.1:5173) 创建并派发自动化任务

# 方式 B：通过 CLI 运行 live 任务与冒烟测试
./run.sh --keyword "Agent 架构师"

# 运行完整自动化测试套件
uv run --extra dev pytest
```

---

## 🔍 LangSmith 可观测性与链路追踪 (Tracing & Observability)

项目全面集成了 [LangSmith](https://smith.langchain.com/) 追踪能力，支持对 LangGraph 复合 Agent 决策图、JD 语义精筛、打招呼草稿生成、以及底层 LLM 请求全链路的可观测性监控与性能开销审计。

### 1. 启用追踪

通过环境变量或 `config/settings.local.yaml` 配置文件（或在 Web 控制台 `/settings` 界面）启用：

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY="lsv2_pt_your_api_key_here"
export LANGSMITH_PROJECT="boss-agent-mobile" # 默认为 boss-agent-mobile
```

或在 `config/settings.local.yaml` 中配置：
```yaml
langsmith_tracing: true
langsmith_api_key: "lsv2_pt_your_api_key_here"
langsmith_project: "boss-agent-mobile"
```

### 2. 使用 LangSmith CLI 查询与排错

已通过 `langsmith-skills` 安装并配置 `langsmith` CLI 工具：

```bash
# 查看最新执行 Traces
langsmith trace list --project boss-agent-mobile --limit 10

# 包含耗时、Token 用量与成本元数据
langsmith trace list --project boss-agent-mobile --include-metadata

# 查看单条 Trace 完整树状层级
langsmith trace get <trace-id>

# 导出最近 Traces 为 JSONL 便于评估分析
langsmith trace export ./traces --project boss-agent-mobile --full
```

---

## 🎬 演示视频更新说明 (Updating Demo Assets)

为避免大体积多媒体文件在 Git 历史中不断累积，本项目将演示视频以 GitHub 附件形式托管：

```bash
# 未来更新演示视频时，上传至 GitHub 附件并替换 README.md 中的 URL：
gh attach /path/to/new_recording.mp4 -R forchain/boss-agent-mobile
```
将命令输出的 `https://github.com/user-attachments/assets/<uuid>` 填入 `README.md` 即可，零 Git 仓库体积膨胀。

---

## 📚 关键文档 (Key Documents)

- [CONTEXT.md](CONTEXT.md): 领域术语表与统一语言定义（Ubiquitous Language）。
- [ACCEPTANCE.md](ACCEPTANCE.md): 验收基线、可执行验收矩阵与 Multi-Agent 协作协议。
- [docs/adr/README.md](docs/adr/README.md): 核心架构决策记录总览索引（ADR 0001 - 0012）：
  - [ADR 0001](docs/adr/0001-avd-and-appium-bootstrap.md): AVD 与 Appium 环境自动化基线
  - [ADR 0002](docs/adr/0002-framework-domain-split.md): 核心自动化框架与业务领域深度解耦
  - [ADR 0003](docs/adr/0003-phase-1-scope.md): Phase 1 交付范围基线
  - [ADR 0004](docs/adr/0004-dynamic-acceptance-and-agent-triad.md): 动态验收基准与 Agent Triad 协作协议
  - [ADR 0005](docs/adr/0005-anti-detection-and-humanized-interaction.md): 拟人化手势防检测与人工接管拦截器
  - [ADR 0006](docs/adr/0006-backend-api-worker-and-state-stream.md): 模块化后端、PocketBase 状态流 Broker 与独立 Worker
  - [ADR 0007](docs/adr/0007-saved-searches-database-and-scheduled-triggers.md): 数据库持久化 SavedSearches、Worker 零启动运行与 Cron 调度器
  - [ADR 0008](docs/adr/0008-langgraph-resume-lifecycle-and-normalization.md): LangGraph 简历全生命周期编排与结构化 Markdown 画像
  - [ADR 0009](docs/adr/0009-inline-description-probing-expansion.md): 职位详情页右下角热区探测展开与提前终止校验
  - [ADR 0010](docs/adr/0010-single-living-greeting-prompt.md): 单一活态 Greeting Prompt 提示词与人机协同 Diff 演进机制
  - [ADR 0011](docs/adr/0011-two-anchor-search-entry-back-recovery-and-ui-telemetry.md): 双锚点检索入场、Back 键快速恢复与 DEBUG UI 遥测
  - [ADR 0012](docs/adr/0012-communicated-job-detection-cooldown-and-enterprise-exclusion.md): 详情页已沟通检测、复投冷却时效与直招同企避嫌
- [docs/agents/](docs/agents/): Agent 协作准则（包含 [Issue Tracker](docs/agents/issue-tracker.md), [Triage Labels](docs/agents/triage-labels.md), [Domain Docs](docs/agents/domain.md), [Demo Assets](docs/agents/demo-assets.md)）。

---

## 🤝 Multi-Agent Protocol

本项目遵循 **Agent Triad Model**（Dev Agent, Test Agent, Acceptance Agent），各 Agent 在独立隔离的上下文中分工协作，确保无确认偏差并保持高工程质量标准。
