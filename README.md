# boss-agent-mobile

> 🤖 **Android 移动端 Boss 直聘智能求职与自动化交互 Agent**
>
> 专为 Android 移动端打造的智能求职交互框架：基于真实移动设备/模拟器环境、拟人化贝塞尔手势（Bézier Gestures）轨迹与多维筛选流程，实现职位智能检索、HR 沟通与全自动求职流。

<p align="center">
  <a href="https://github.com/forchain/boss-agent-mobile/raw/assets/demo.mp4" target="_blank" rel="noopener noreferrer">
    <img src="https://raw.githubusercontent.com/forchain/boss-agent-mobile/assets/demo-preview.gif" alt="Boss Agent Mobile Demo Preview" width="360" style="border-radius: 8px; box-shadow: 0 4px 16px rgba(0,0,0,0.12);" />
  </a>
  <br>
  <sub>🎬 <b><a href="https://github.com/forchain/boss-agent-mobile/raw/assets/demo.mp4">▶️ 点击查看完整高清演示视频 (52s MP4)</a></b></sub>
</p>

---

## ✨ 核心特性 (Key Highlights)

- **📱 移动端原生自动化**：基于 Appium 与 Android 原生控件交互，相比 Web 爬虫具备更低风控限制与更完整的移动端专属功能。
- **🎯 拟人化手势与防检测**：内置三次贝塞尔曲线手势引擎（Bézier Gesture Synthesis）与高斯随机停顿，完美模拟真人滑动与点击轨迹。
- **🔍 多维多级筛选体系**：支持城市、职位关键词、薪资区间、学历要求以及多级行业分类（如互联网/金融/医疗）等精确筛选。
- **🛡️ 弹窗与打扰拦截器**：通用弹窗自动拦截体系（自动处理青少年模式、系统升级、定位授权等各类偶发弹窗），保障无人值守持续运行。
- **📦 零历史膨胀媒体管理**：演示视频与预览图独立托管于孤儿分支（`assets`），主仓库体积保持极致轻量、秒级克隆。

---

## 🏗️ 架构与项目结构 (Project Structure)

- `src/droid_agent_core/`: 通用、业务解耦的 Android 移动自动化框架，包含贝塞尔手势合成、统一定位器管理、弹窗拦截器与 LLM 决策接口。
- `src/boss_agent/`: Boss 直聘业务领域实现（Page Object 模型、多维检索工作流、会话持久化与职位解析器）。
- `config/locators.yaml`: UI 控件定位器配置（支持本地覆盖与多策略自动解析）。
- `scripts/bootstrap.py`: 幂等式自动化环境初始化工具（JDK、Android SDK、AVD 模拟器、Appium 服务及 APK 安装）。
- `scripts/update_demo_asset.py`: 演示视频资产一键同步工具（自动生成预览 GIF 并推送到独立的 `assets` 孤儿分支）。

---

## 🚀 快速上手 (Quick Start)

### 1. 环境准备
```bash
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

为避免大体积多媒体文件在 Git 历史中不断累积，本项目将视频文件隔离在独立的 `assets` 孤儿分支中：

```bash
# 未来更新演示视频时，只需执行单条脚本：
uv run python scripts/update_demo_asset.py /path/to/new_recording.mp4
```
该脚本会自动标准化视频文件、使用 `ffmpeg` 重新生成优化后的预览 GIF 与 Poster，并以覆盖形式推送到 `origin/assets` 分支，无需在主分支保留历史记录。

---

## 📚 关键文档 (Key Documents)

- [CONTEXT.md](CONTEXT.md): 领域术语表与统一语言定义（Ubiquitous Language）。
- [ACCEPTANCE.md](ACCEPTANCE.md): Phase 1 验收基线与 Multi-Agent 协作协议。
- [docs/adr/](docs/adr/): 核心架构决策记录（ADR 0001 - 0005）。

---

## 🤝 Multi-Agent Protocol

本项目遵循 **Agent Triad Model**（Dev Agent, Test Agent, Acceptance Agent），各 Agent 在独立隔离的上下文中分工协作，确保无确认偏差并保持高工程质量标准。
