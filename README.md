# boss-agent-mobile

> 🤖 **Android 移动端 Boss 直聘智能求职与自动化交互 Agent**
>
> 专为 Android 移动端打造的智能求职交互框架：基于真实移动设备/模拟器环境、拟人化贝塞尔手势（Bézier Gestures）轨迹与多维筛选流程，实现职位智能检索、HR 沟通与全自动求职流。

<div align="center">

https://github.com/user-attachments/assets/ec8a2864-54fe-4e13-8f33-5ad4e7104f11

*Demo: Boss 直聘 Android 移动端自动化求职交互全流程*

</div>

---

## ✨ 核心特性 (Key Highlights)

- **📱 移动端原生自动化**：基于 Appium 与 Android 原生控件交互，相比 Web 爬虫具备更低风控限制与更完整的移动端专属功能。
- **🎯 拟人化手势与防检测**：内置三次贝塞尔曲线手势引擎（Bézier Gesture Synthesis）与高斯随机停顿，完美模拟真人滑动与点击轨迹。
- **🔍 多维多级筛选体系**：支持城市、职位关键词、薪资区间、学历要求以及多级行业分类（如互联网/金融/医疗）等精确筛选。
- **🛡️ 弹窗与打扰拦截器**：通用弹窗自动拦截体系（自动处理青少年模式、系统升级、定位授权等各类偶发弹窗），保障无人值守持续运行。
- **📦 零历史膨胀媒体管理**：演示视频通过 GitHub 附件（`user-attachments`）独立托管，主仓库体积保持极致轻量、秒级克隆与同步。

---

## 🏗️ 架构与项目结构 (Project Structure)

- `src/droid_agent_core/`: 通用、业务解耦的 Android 移动自动化框架，包含贝塞尔手势合成、统一定位器管理、弹窗拦截器与 LLM 决策接口。
- `src/boss_agent/`: Boss 直聘业务领域实现（Page Object 模型、多维检索工作流、会话持久化与职位解析器）。
- `config/locators.yaml`: UI 控件定位器配置（支持本地覆盖与多策略自动解析）。
- `scripts/bootstrap.py`: 幂等式自动化环境初始化工具（JDK、Android SDK、AVD 模拟器、Appium 服务及 APK 安装）。
- `scripts/init_worktree.py`: Git Worktree 与本地共享配置初始化工具（自动同步 main 分支、创建隔离工作区并软链接共享配置）。

---

## 🚀 快速上手 (Quick Start)

### 1. 环境准备
```bash
# 快速初始化独立 Git Worktree（自动同步最新 main 并软链接本地配置）
./init_worktree.sh <feature_name>

# 安装依赖
uv sync --extra dev

# 自动化环境检查与安装（JDK, Android SDK, AVD, Appium）
uv run python scripts/bootstrap.py
```

### 2. 运行测试
```bash
# 运行单元与集成测试套件
uv run --extra dev pytest

# 运行真机 / 模拟器冒烟测试
uv run python scripts/run_live_test.py
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
- [ACCEPTANCE.md](ACCEPTANCE.md): Phase 1 验收基线与 Multi-Agent 协作协议。
- [docs/adr/](docs/adr/): 核心架构决策记录（ADR 0001 - 0005）。

---

## 🤝 Multi-Agent Protocol

本项目遵循 **Agent Triad Model**（Dev Agent, Test Agent, Acceptance Agent），各 Agent 在独立隔离的上下文中分工协作，确保无确认偏差并保持高工程质量标准。
