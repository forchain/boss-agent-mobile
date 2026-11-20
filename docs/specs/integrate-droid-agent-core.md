# Spec: 将 `src/droid_agent_core` 按架构铁律集成进 `src/boss_agent`

> **状态**：Blueprint Ready（阶段一诊断与蓝图已完成；用户已在编排命令中授权后续实现步骤，下游可直接进入阶段二 TDD 分层实现，无需再暂停等待确认）  
> **最高准则**：`docs/architecture/iron-rules.md`（本 spec 与铁律存在任何冲突时，一律以铁律为准）  
> **相关 ADR**：ADR 0002（历史框架/领域拆分）、ADR 0005（拟人化防风控策略）、ADR 0016（仅沟通设备端口）、ADR 0019（无 shim 响亮退休先例）、ADR 0021（严格六边形归位）

---

## 1. Problem Statement（问题陈述）

当前仓库将 `src/droid_agent_core` 作为独立顶层包与 `src/boss_agent` 并列存在。在 ADR 0021 完成 `boss_agent` 内部六边形归位后，`src/droid_agent_core` 成为仓库中最后一个未遵循架构铁律的“混合层”：

1. **核心层被外部框架反向污染**：`boss_agent/core/` 的业务用例（`matching.py`、`rejection_classifier.py`）为了引用 `LLMDecisionClient` 抽象接口，被迫 `from droid_agent_core.llm import LLMDecisionClient`。而 `droid_agent_core/llm.py` 在顶层直接导入 `requests`、`yaml`、`langsmith`，导致任何导入核心用例的代码都会把 HTTP、YAML 与 LangSmith 观测栈拖入内存，`test_core_isolation.py` 也不得不保留 `ALLOWED_THIRD_PARTY = {"droid_agent_core"}` 的历史例外。
2. **Android / Appium / ADB 强耦合代码游离在 `adapters/` 之外**：`driver.py` 直接依赖 `appium` / `UiAutomator2Options` / `adb` 子进程，`gestures.py` 混入 `selenium` / `appium` W3C `ActionChains` 与 `driver.tap/swipe`，`interceptors.py` 硬编码 Android 权限控制器资源 ID 并直接操作 `driver.find_elements().click()`，`locators.py` 混入 `yaml` 磁盘读取与全局可变单例 `get_global_locator_registry()`。
3. **依赖倒置与组装根断裂**：业务规则（几何抖动、贝塞尔曲线、选择器解析、等待策略、拦截器编排）与易变外部技术（Appium、Selenium、ADB、YAML、HTTP、LangSmith）杂糅在同一文件中；隐式全局单例与无类型 `driver: Any` 导致替换底层设备驱动、配置源或 LLM 协议无法做到只改 `adapters/` 与 `main.py`。

---

## 2. Solution（解决方案）

遵循 `docs/architecture/iron-rules.md` 的三层架构与依赖倒置原则，将 `src/droid_agent_core` 的 5 个模块彻底解耦并按职责归位到 `src/boss_agent`：

- **纯业务/通用规则与领域异常**进入 `src/boss_agent/core/automation/domain/` 与 `src/boss_agent/core/shared/`，实现 100% 仅依赖 Python 标准库与自身 Domain 类型。
- **编排用例与抽象端口（Ports）**进入 `src/boss_agent/core/automation/{interfaces,use_cases}/` 与 `src/boss_agent/core/shared/llm.py`，彻底移除 `core/` 对第三方包的白名单例外。
- **全部 Android / Appium / Selenium / ADB / YAML / HTTP / LangSmith 技术实现**隔离进 `src/boss_agent/adapters/outbound/`，并补齐 `src/boss_agent/adapters/memory/` 内存测试替身。
- **全部具体适配器实例化与依赖注入**唯一收口于组装根 `src/boss_agent/main.py`，消除隐式全局单例（`get_global_locator_registry()`）与隐式环境变量副作用（`configure_langsmith()`）。
- 遵循 ADR 0019 / ADR 0021 的“无 shim 响亮退休”先例，完成迁移与验证后移除 `src/droid_agent_core/` 顶层包。

---

## 3. User Stories（用户故事）

1. As a **core domain developer**, I want `boss_agent.core` to import only Python stdlib and `boss_agent.core.*` (with zero `droid_agent_core` exception), so that importing any business rule or Use Case never pulls in `requests`, `yaml`, `langsmith`, `appium`, or `selenium`.
2. As an **automation engineer**, I want gesture geometry (jitter, probe coordinates, Bézier curve synthesis, double-tap interval clamping) separated from Appium/Selenium execution, so that I can verify humanized motion math purely in memory without a WebDriver.
3. As an **automation engineer**, I want locator parsing (`parse_selector`, `UISelector`, `LocatorStrategy`, `ViewDescriptor`) separated from YAML file loading and driver element queries, so that selector rules are deterministic and reusable across backends.
4. As a **maintainer**, I want Android-specific code (`UiAutomator2Options`, `AppiumSession`, `adb` subprocesses, W3C `ActionChains`, Android `permissioncontroller` IDs) isolated exclusively under `adapters/outbound/device/appium/`, so that swapping Appium for another UI driver never touches `core/`.
5. As a **maintainer**, I want `LLMDecisionClient` defined as an Application Port in `boss_agent/core/shared/` and `OpenAIChatClient` / `AnthropicChatClient` / `TracedLLMDecisionClient` implemented in `adapters/outbound/llm/`, so that adding or replacing an LLM protocol never changes core Use Cases.
6. As a **maintainer**, I want LangSmith tracing decorators separated from HTTP LLM clients into a dedicated tracing adapter, so that protocol transport and observability can evolve independently while preserving existing span names.
7. As a **maintainer**, I want `get_global_locator_registry()` eliminated in favor of constructor-injected `LocatorStorePort` wired in `main.py`, so that tests and concurrent workers never share hidden process-global mutable state.
8. As a **maintainer**, I want `main.py` to be the single Composition Root instantiating all concrete device, locator, interceptor, and LLM adapters, so that changing storage, device backend, or LLM provider touches only `adapters/` and `main.py`.
9. As a **developer writing unit tests**, I want in-memory fake adapters (`FakeUIDriver`, `FakeDeviceSession`, `FakeAdbClient`, `InMemoryLocatorStore`, `StubLLMDecisionClient`) under `adapters/memory/`, so that every Domain, Application, and Adapter test runs fast with zero real device, network, disk, or DB I/O.
10. As a **platform operator**, I want the `droid_agent_core.ui` logger channel name and existing LangSmith span names preserved across the refactor, so that existing dashboards, log greps, and telemetry pipelines do not break.
11. As a **CI/CD guardian**, I want static AST isolation tests (`test_core_isolation.py`, `test_composition_confinement.py`, `test_memory_adapter_imports.py`) tightened to reject any `droid_agent_core` import or leaked framework dependency, so that architectural regressions fail fast in CI.
12. As a **downstream implementation agent**, I want an explicit module-by-module mapping, four complete inventories (Domain models, Domain exceptions, Use Cases, Ports), and a strict TDD execution order (`Domain -> Application -> Adapters -> Composition Root`), so that I can implement the integration deterministically without ambiguity.

---

## 4. 现状诊断（Diagnosis & Coupling Violations）

逐一审查 `src/droid_agent_core/` 的 5 个模块及 `src/boss_agent/` 现有调用点，列出所有强耦合违规并映射到 `docs/architecture/iron-rules.md` 对应条款：

| # | 现状文件与符号 | 耦合点与问题描述 | 违反的铁律条款 |
|---|---|---|---|
| **V1** | `droid_agent_core/driver.py`<br>`DriverConfig.to_options()`、`AppiumSession` | 直接 `from appium import webdriver`、`from appium.options.android import UiAutomator2Options`。配置数据结构 `DriverConfig` 内嵌 Appium Android 选项构造；`AppiumSession` 直接暴露 `webdriver.Remote` 且无抽象 Port。 | **Rule 1**（领域/配置与易变框架混杂）、**Rule 2 DIP**（缺少 Session 抽象端口）、**Rule 3**（Android 驱动未隔离进 `adapters/`） |
| **V2** | `droid_agent_core/driver.py`<br>`AdbClient` | 直接 `subprocess.run(["adb", ...])` 调用宿主机 Android Debug Bridge，无抽象端口，无类型化异常转换，测试无法无副作用替换。 | **Rule 1**（外部进程 I/O 未隔离）、**Rule 2 DIP**、**Rule 3** |
| **V3** | `droid_agent_core/gestures.py`<br>`HumanizedGestureExecutor`、`_resolve_w3c_action_chains()` | 同一文件混杂纯几何算法（`Point`、`calculate_bounding_box_jitter`、`calculate_probe_coordinate`、`BézierTouchSynthesizer`）与 Android/Appium/Selenium 执行逻辑（动态导入 `appium`/`selenium` `ActionChains`、直接调用 `driver.tap()`/`driver.swipe()`/`element.click()`/`element.send_keys()`、`time.sleep()`），且含模块级全局缓存 `_w3c_action_chains`。 | **Rule 1**（纯领域数学规则与框架/I/O 混杂）、**Rule 2 DIP/DI**（依赖无类型 `driver`/`element` 与硬编码 `time.sleep`） |
| **V4** | `droid_agent_core/locators.py`<br>`LocatorRegistry`、`get_global_locator_registry()` | 纯选择器解析规则（`By`、`UISelector`、`parse_selector`）与 `import yaml` 磁盘读取、`os.environ` 读取、`print()` 副作用混在同一文件；且暴露**进程级全局可变单例** `get_global_locator_registry()`，被 `BaseBossPage`、`SystemDialogInterceptor` 隐式默认调用。 | **Rule 1**（第三方 `yaml` 与文件 I/O 混入核心规则）、**Rule 2 DI & Composition Root**（隐藏全局单例破坏构造函数注入） |
| **V5** | `droid_agent_core/locators.py`<br>`ViewDescriptor`、`wait_until()` | `ViewDescriptor` 作为数据结构却内嵌 `is_present(driver)` / `wait_until_present(driver)`，直接调用 `driver.find_elements(...)`；`wait_until()` 直接依赖 `time.time()` / `time.sleep()` 且抛出内置 `TimeoutError` 而非领域异常。 | **Rule 1**（值对象直接耦合 Appium 驱动查询）、**Rule 2 DIP/DI**（时间/睡眠与驱动不可注入） |
| **V6** | `droid_agent_core/interceptors.py`<br>`BaseInterceptor`、`SystemDialogInterceptor`、`InterceptorRegistry` | 抽象接口 `BaseInterceptor` 接收无类型 `driver: Any`；编排器 `InterceptorRegistry` 与 Android 专属实现 `SystemDialogInterceptor`（硬编码 `com.android.permissioncontroller:id/permission_allow_button` 等资源 ID，直接调用 `driver.find_elements()` 与 `elements[0].click()`，且隐式回退 `get_global_locator_registry()`）混在同一文件。 | **Rule 1**（编排层与 Android 专属适配器混杂）、**Rule 2 DIP/DI**、**Rule 3** |
| **V7** | `droid_agent_core/llm.py`<br>`LLMDecisionClient`、`OpenAIChatClient`、`AnthropicChatClient`、`LLMConfig`、`configure_langsmith()` | **跨层污染核心源头**：抽象接口 `LLMDecisionClient` 与具体 HTTP 客户端（`import requests`）、YAML 读取（`import yaml`）、LangSmith 观测（`from langsmith import traceable, get_current_run_tree`）、全局环境变量修改（`os.environ[...] = ...`）全部堆在同一文件。导致 `boss_agent/core/` 只要导入 `LLMDecisionClient` 就被迫引入上述全部外部框架。此外 `FRAMEWORK_DEFAULTS` 中还残留 `"boss-agent-mobile"` 字符串。 | **Rule 1**（Application Port 拖拽 HTTP/YAML/LangSmith 进 `core/`）、**Rule 2 DIP & Composition Root**（端口与实现未分离、客户端构造时隐式改写 `os.environ`）、**Compliance Gate #1/#2** |
| **V8** | `boss_agent/main.py`、`boss_agent/adapters/inbound/worker/context.py`、`boss_agent/adapters/outbound/device/pages/base.py` | `main.py` 从外部顶层包 `droid_agent_core` 导入 `AppiumSession`/`DriverConfig`/`LLMConfig`/`LLMDecisionClient`；`WorkerContext` 使用 `Any` + duck-typing `_SESSION_CLOSERS` 持有驱动会话；`BaseBossPage` 内部自行 `HumanizedGestureExecutor(driver)` 并隐式回退 `get_global_locator_registry()`，绕过组装根注入。 | **Rule 2 DI & Composition Root**（非根组件自行实例化执行器或回退全局单例）、**Rule 3**（外部包游离在 `boss_agent/` 目录体系外） |

---

## 5. 重构蓝图：目标完整目录树（Target Directory Tree）

重构后移除顶层包 `src/droid_agent_core/`，全部代码收口于 `src/boss_agent/`。**仅看目录树即可直接回答三个问题**：
- **业务规则在哪**：`src/boss_agent/core/*/domain/` 与 `src/boss_agent/core/shared/`
- **有哪些 Use Cases**：`src/boss_agent/core/*/use_cases/`
- **换存储 / 换设备驱动 / 换 LLM / 换配置源改哪里**：`src/boss_agent/adapters/outbound/` 与唯一组装根 `src/boss_agent/main.py`

```text
src/boss_agent/
├── __init__.py                                   # 公共外观导出（重指新路径，保持外部符号兼容）
├── entities.py                                   # 领域实体聚合导出
├── main.py                                       # 【唯一组装根 Composition Root】统一实例化并注入全部适配器
│
├── core/                                         # 【内层：Domain + Application，仅允许 stdlib + boss_agent.core.*】
│   ├── __init__.py
│   ├── shared/                                   # 跨业务聚合共享内核
│   │   ├── __init__.py
│   │   ├── enums.py                              # 共享枚举（AuthStatus, ChatButtonState 等）
│   │   ├── errors.py                             # 共享领域/端口异常（BrokerError 家族 + LLMError 家族）
│   │   ├── identifier_helpers.py                 # 标识符纯规则
│   │   ├── keyword_constants.py                  # 关键词常量
│   │   ├── llm.py                                # [NEW] LLMDecisionClient 抽象 Port（纯 ABC/Protocol，零外部库）
│   │   └── seams.py                              # 共享可调用接缝（LogSink, CancelProbe, Pause, SyncSleep, MonotonicClock）
│   │
│   ├── automation/                               # [NEW] 移动端 UI 自动化聚合（原 droid_agent_core 纯核心归位）
│   │   ├── __init__.py
│   │   ├── domain/                               # 纯领域模型、几何/定位规则与领域异常（100% stdlib）
│   │   │   ├── __init__.py
│   │   │   ├── errors.py                         # AutomationError, DeviceSessionError, ElementWaitTimeoutError, AdbCommandError
│   │   │   ├── geometry.py                       # Point, BoundingBox, DOUBLE_TAP_MAX_INTERVAL, TAP_DURATION_MS,
│   │   │   │                                     # calculate_bounding_box_jitter, calculate_probe_coordinate,
│   │   │   │                                     # clamp_double_tap_interval, BézierTouchSynthesizer
│   │   │   └── locators.py                       # By(LocatorStrategy), UISelector, ViewDescriptor(纯数据), parse_selector, deep_merge_dict
│   │   ├── interfaces/                           # 应用层定义的抽象 Ports（Protocol / ABC）
│   │   │   ├── __init__.py
│   │   │   ├── device_session.py                 # DeviceSessionPort, AdbCommandPort
│   │   │   ├── interceptor.py                    # ScreenInterceptorPort（原 BaseInterceptor，强类型化）
│   │   │   ├── locator_store.py                  # LocatorStorePort
│   │   │   └── ui_driver.py                      # UIElementPort, UIDriverPort
│   │   └── use_cases/                            # 应用层编排用例（仅依赖 automation.domain + interfaces + core.shared）
│   │       ├── __init__.py
│   │       ├── gestures.py                       # HumanizedGestureExecutor（注入 UIDriverPort + 可选 sleep/rng）
│   │       ├── interceptors.py                   # InterceptorRegistry（纯编排 ScreenInterceptorPort 链）
│   │       ├── view_probe.py                     # ViewPresenceChecker（注入 UIDriverPort + wait_until）
│   │       └── wait.py                           # wait_until（可注入 clock/sleep，抛 ElementWaitTimeoutError）
│   │
│   ├── candidate/                                # 既有聚合：候选人简历与画像
│   │   ├── domain/
│   │   ├── interfaces/
│   │   └── use_cases/
│   ├── chat/                                     # 既有聚合：沟通/拒信分诊（改为依赖 core.shared.llm.LLMDecisionClient）
│   │   ├── domain/
│   │   ├── interfaces/
│   │   └── use_cases/
│   ├── job/                                      # 既有聚合：岗位筛选、匹配与列表流（改为依赖 core.shared.llm.LLMDecisionClient）
│   │   ├── domain/
│   │   ├── interfaces/
│   │   └── use_cases/
│   ├── search/                                   # 既有聚合：保存搜索与定时调度
│   │   ├── domain/
│   │   ├── interfaces/
│   │   └── use_cases/
│   └── task/                                     # 既有聚合：任务租约、取消与清理
│       ├── domain/
│       ├── interfaces/
│       └── use_cases/
│
└── adapters/                                     # 【外层：全部技术栈与 I/O 实现，依赖 Application + Domain】
    ├── __init__.py
    ├── shared/                                   # 适配器间共享纯工具（无网络/驱动依赖）
    │   ├── __init__.py
    │   ├── prompts.py
    │   └── records.py
    ├── memory/                                   # 内存/Fake 适配器（快速单元测试接缝，零外部 I/O）
    │   ├── __init__.py
    │   ├── automation.py                         # [NEW] FakeUIDriver, FakeUIElement, FakeDeviceSession, FakeAdbClient, InMemoryLocatorStore
    │   ├── candidate_memory_store.py
    │   ├── job_store.py
    │   ├── llm_client.py                         # [NEW] StubLLMDecisionClient（可编程内存 LLM 替身）
    │   ├── saved_search_store.py
    │   └── task_broker.py
    ├── infra/                                    # 基础设施守护脚本适配器
    │   ├── __init__.py
    │   ├── remote_adb_bridge.py
    │   └── teardown.py
    ├── inbound/                                  # 入站适配器（Worker Daemon、Handlers、Verification Runner）
    │   ├── __init__.py
    │   ├── verification/
    │   │   └── feed_verification.py
    │   └── worker/
    │       ├── config.py
    │       ├── context.py                        # 收敛为依赖 DeviceSessionPort / UIDriverPort
    │       ├── daemon.py
    │       ├── scheduler.py
    │       └── handlers/
    │           ├── auto_apply.py
    │           ├── base.py
    │           ├── check_chat.py
    │           ├── check_login.py
    │           └── scrape_jobs.py
    └── outbound/                                 # 出站适配器（外部技术与第三方服务实现）
        ├── __init__.py
        ├── config/                               # Configuration Realm 与本地 YAML 定位配置
        │   ├── __init__.py
        │   ├── config_realm.py
        │   ├── greeting_prompt.py
        │   ├── initial_searches.py
        │   ├── locator_registry.py               # [NEW] YamlLocatorRegistry（实现 LocatorStorePort，隔离 yaml 与路径解析）
        │   ├── screening_config.py
        │   ├── screening_prompt.py
        │   └── settings.py
        ├── device/                               # 移动端设备适配器
        │   ├── __init__.py
        │   ├── appium/                           # [NEW] Android / Appium / Selenium / ADB 专属隔离区
        │   │   ├── __init__.py
        │   │   ├── adb_client.py                 # SubprocessAdbClient（实现 AdbCommandPort，隔离 subprocess adb）
        │   │   ├── config.py                     # DriverConfig（含 to_options() -> UiAutomator2Options）
        │   │   ├── driver_adapter.py             # AppiumDriverAdapter（实现 UIDriverPort，封装 W3C ActionChains 双击探测）
        │   │   ├── interceptors.py               # AndroidSystemDialogInterceptor（实现 ScreenInterceptorPort，隔离 Android 权限 ID）
        │   │   └── session.py                    # AppiumSession（实现 DeviceSessionPort，封装 webdriver.Remote）
        │   ├── pages/                            # Boss 直聘 Page Objects（依赖 core.automation + 注入 LocatorStorePort/GestureExecutor）
        │   │   ├── __init__.py
        │   │   ├── base.py
        │   │   ├── chat_adapters.py
        │   │   ├── chat_bundle.py
        │   │   ├── communication.py
        │   │   ├── feed_bundle.py
        │   │   ├── job_detail.py
        │   │   ├── job_feed.py
        │   │   └── system.py
        │   └── takeover.py
        ├── llm/                                  # LLM 协议与观测适配器
        │   ├── __init__.py
        │   ├── anthropic_client.py               # [NEW] AnthropicChatClient（实现 LLMDecisionClient，纯 HTTP 协议）
        │   ├── config.py                         # [NEW] LLMConfig, FRAMEWORK_DEFAULTS, MASK_MARKERS, PLACEHOLDER_API_KEY, configure_langsmith
        │   ├── json_repair.py                    # [NEW] JSONRepairMixin / extract_json_block / robust_parse_json
        │   ├── llm_config.py                     # Configuration Realm -> LLMConfig 绑定与 create_llm_client 工厂
        │   ├── openai_client.py                  # [NEW] OpenAIChatClient（实现 LLMDecisionClient，纯 HTTP 协议 + MiniMax 适配）
        │   └── tracing.py                        # TracedLLMDecisionClient + 既有 TracedCandidateScreener / TracedJobMatchGreetingService
        ├── pocketbase/                           # 既有：PocketBase 持久化适配器
        └── resume/                               # 既有：简历解析与 LangGraph 适配器
```

> **退休项**：完成全部迁移与验证后，删除 `src/droid_agent_core/` 整个目录，并将 `pyproject.toml` 的 `[tool.hatch.build.targets.wheel]` 缩减为 `packages = ["src/boss_agent"]`。

---

## 6. 四大清单（Inventories）

严格按照铁律 Step 1 要求，给出完整的四张清单：

### 6.1 领域模型（Domain Models：Entities & Value Objects）

| 名称 | 类型 | 归属路径 | 职责与不变式 |
|---|---|---|---|
| `Point` | Value Object (`@dataclass(frozen=True)`) | `core/automation/domain/geometry.py` | 二维屏幕坐标 `(x: float, y: float)`。 |
| `BoundingBox` | Value Object (`@dataclass(frozen=True)`) | `core/automation/domain/geometry.py` | 元素矩形 `(x, y, width, height)`，提供边界合法性校验与 `from_rect()` 转换。 |
| `By`（别名 `LocatorStrategy`） | Value Object (`StrEnum`) | `core/automation/domain/locators.py` | 定位策略枚举（`ID`, `XPATH`, `ACCESSIBILITY_ID`, `CLASS_NAME`, `ANDROID_UIAUTOMATOR`, `NAME`）。 |
| `UISelector` | Value Object (`@dataclass`) | `core/automation/domain/locators.py` | 声明式 UI 选择器 `(by, value, description, timeout)`，由纯函数 `parse_selector()` 解析构造。 |
| `ViewDescriptor` | Value Object (`@dataclass(frozen=True)`) | `core/automation/domain/locators.py` | 界面特征描述 `(name, required_selectors, optional_selectors)`；**纯数据，不再持有或调用 `driver`**。 |
| `BézierTouchSynthesizer` | Domain Service（纯函数/无状态） | `core/automation/domain/geometry.py` | 三次贝塞尔曲线采样器，以及同模块纯函数 `calculate_bounding_box_jitter()`、`calculate_probe_coordinate()`、`clamp_double_tap_interval()`。 |

### 6.2 领域异常（Domain Exceptions）

| 异常类 | 基类 | 归属路径 | 触发语义与适配器翻译规则 |
|---|---|---|---|
| `LLMError` | `Exception` | `core/shared/errors.py` | LLM 调用基础异常（连接失败、非 200 响应、JSON 无法修复解析）。 |
| `LLMAuthError` | `LLMError` | `core/shared/errors.py` | 鉴权失败（HTTP 401/403）。 |
| `LLMTimeoutError` | `LLMError` | `core/shared/errors.py` | LLM HTTP 请求超时。 |
| `AutomationError` | `RuntimeError` | `core/automation/domain/errors.py` | 移动端自动化领域基础异常。 |
| `DeviceSessionError` | `AutomationError` | `core/automation/domain/errors.py` | 设备会话启动、激活或探活失败（由 `AppiumSession` 捕获 WebDriver 异常后翻译抛出）。 |
| `ElementWaitTimeoutError` | `AutomationError, TimeoutError` | `core/automation/domain/errors.py` | `wait_until` / `ViewPresenceChecker` 等待超时（**多继承 `TimeoutError`**，保证既有 `pytest.raises(TimeoutError)` 与上游超时捕获无缝兼容）。 |
| `AdbCommandError` | `AutomationError` | `core/automation/domain/errors.py` | ADB 命令执行异常（由 `SubprocessAdbClient` 翻译）。 |

### 6.3 Use Cases（Application Entrypoints）

| Use Case | 归属路径 | 注入的 Ports / 接缝 | 核心编排职责 |
|---|---|---|---|
| `wait_until()` | `core/automation/use_cases/wait.py` | `clock: MonotonicClock`, `sleep: SyncSleep`（默认 stdlib `time.monotonic`/`time.sleep`，测试可注入零延迟替身） | 有界预算轮询条件函数；预算耗尽后不追加尾随 sleep，抛出 `ElementWaitTimeoutError`。 |
| `ViewPresenceChecker` | `core/automation/use_cases/view_probe.py` | `driver: UIDriverPort`, `clock`, `sleep` | 提供 `is_present(view: ViewDescriptor) -> bool` 与 `wait_until_present(view: ViewDescriptor, timeout_sec: float) -> bool`，替代原 `ViewDescriptor` 上的驱动方法。 |
| `HumanizedGestureExecutor` | `core/automation/use_cases/gestures.py` | `driver: UIDriverPort`, `sleep: SyncSleep`, `rng: random.Random` | 编排拟人化交互：`random_sleep`、`human_click`、`human_click_at_point`、`human_double_click`、`human_double_click_at_point`、`human_type`、`human_swipe`。双击优先委托 `driver.perform_double_tap(x, y, interval)`（W3C 批量由适配器实现），不支持时优雅降级到两次 `tap` 或 `element.click()`。 |
| `InterceptorRegistry` | `core/automation/use_cases/interceptors.py` | `interceptors: list[ScreenInterceptorPort]`（支持构造函数注入或 `register()`） | 按优先级遍历拦截器链，`process_all(driver: UIDriverPort) -> bool` 命中首个 `can_handle` 后执行 `handle`。 |

> 既有业务 Use Cases（`JobMatchGreetingService`、`CandidateScreener`、`RejectionClassifier`、`JobFeedPipeline`、`ChatTriage`、`resume_lifecycle`）职责不变，仅将 `LLMDecisionClient` 的导入路径更新为 `boss_agent.core.shared.llm`。

### 6.4 抽象接口（Ports：应用层定义，适配层实现）

| Port 名称 | 定义位置（Application） | 核心方法契约 | 生产适配器实现（Outbound） | 内存测试替身（Memory） |
|---|---|---|---|---|
| `LLMDecisionClient` | `core/shared/llm.py` | `chat_completion(messages, temperature, max_tokens, response_format, extra_payload) -> str`<br>`chat_completion_json(...) -> dict[str, Any]` | `OpenAIChatClient`、`AnthropicChatClient`、`TracedLLMDecisionClient`（`adapters/outbound/llm/`） | `StubLLMDecisionClient`（`adapters/memory/llm_client.py`） |
| `DeviceSessionPort` | `core/automation/interfaces/device_session.py` | `start() -> UIDriverPort`、`activate_app(app_id) -> bool`、`stop() -> None`、`is_active() -> bool` | `AppiumSession`（`adapters/outbound/device/appium/session.py`） | `FakeDeviceSession`（`adapters/memory/automation.py`） |
| `AdbCommandPort` | `core/automation/interfaces/device_session.py` | `run(args: list[str]) -> AdbCommandResult`、`get_current_focus() -> str` | `SubprocessAdbClient`（`adapters/outbound/device/appium/adb_client.py`） | `FakeAdbClient`（`adapters/memory/automation.py`） |
| `UIElementPort` | `core/automation/interfaces/ui_driver.py` | `rect: dict[str, float \| int] \| None`、`class_name: str \| None`、`click()`、`clear()`、`send_keys(text)` | Appium/Selenium WebElement（结构化满足 Protocol，无须冗余包装） | `FakeUIElement`（`adapters/memory/automation.py`） |
| `UIDriverPort` | `core/automation/interfaces/ui_driver.py` | `find_elements(by, value) -> list[UIElementPort]`、`tap(positions, duration)`、`swipe(sx, sy, ex, ey, duration)`、`get_window_size()`、`activate_app(app_id)`、可选 `perform_double_tap(x, y, interval) -> bool` | `AppiumDriverAdapter`（封装原生 `webdriver.Remote` 并实现 W3C `ActionChains` 双击） | `FakeUIDriver`（`adapters/memory/automation.py`） |
| `LocatorStorePort` | `core/automation/interfaces/locator_store.py` | `get_raw(key, default) -> Any`、`get_selectors(key, format_args, default) -> list[UISelector]`、`reload() -> None` | `YamlLocatorRegistry`（`adapters/outbound/config/locator_registry.py`） | `InMemoryLocatorStore`（`adapters/memory/automation.py`） |
| `ScreenInterceptorPort` | `core/automation/interfaces/interceptor.py` | `can_handle(driver: UIDriverPort) -> bool`、`handle(driver: UIDriverPort) -> bool` | `AndroidSystemDialogInterceptor`（`adapters/outbound/device/appium/interceptors.py`） | 测试内匿名/内存实现 |

---

## 7. 模块归位与 Android 隔离方案（Module Mapping）

### 7.1 `droid_agent_core` 全量符号归位表

| 原模块 | 原符号 | 目标层级 | 目标模块 | 改造说明 |
|---|---|---|---|---|
| `driver.py` | `DriverConfig` | **Outbound Adapter** | `adapters/outbound/device/appium/config.py` | 保留 Appium `UiAutomator2Options` 转换；纯 Appium 专属配置。 |
| `driver.py` | `AppiumSession` | **Outbound Adapter** | `adapters/outbound/device/appium/session.py` | 实现 `DeviceSessionPort`，返回包装后的 `AppiumDriverAdapter`（或保持兼容属性 `.raw_driver`）。 |
| `driver.py` | `AdbClient` | **Outbound Adapter** | `adapters/outbound/device/appium/adb_client.py` | 重命名/导出为 `SubprocessAdbClient`（可保留别名 `AdbClient`），实现 `AdbCommandPort`，隔离 `subprocess.run(["adb", ...])`。 |
| `gestures.py` | `Point`、`DOUBLE_TAP_MAX_INTERVAL`、`TAP_DURATION_MS`、`calculate_bounding_box_jitter`、`calculate_probe_coordinate`、`BézierTouchSynthesizer` | **Domain** | `core/automation/domain/geometry.py` | 100% 纯 stdlib；抽离 `_double_tap_interval` 为纯函数 `clamp_double_tap_interval`。 |
| `gestures.py` | `_load_w3c_action_chains`、`_resolve_w3c_action_chains`、`_emit_w3c_double_tap` | **Outbound Adapter** | `adapters/outbound/device/appium/driver_adapter.py` | 彻底移出核心层，成为 `AppiumDriverAdapter.perform_double_tap(x, y, interval)` 的实现细节。 |
| `gestures.py` | `HumanizedGestureExecutor` | **Application (Use Case)** | `core/automation/use_cases/gestures.py` | 注入 `UIDriverPort` + 可选 `sleep`/`rng`；保留 `logging.getLogger("droid_agent_core.ui")` 遥测通道名。 |
| `locators.py` | `By`、`UISelector`、`parse_selector`、`_deep_merge_dict`、`ViewDescriptor`（数据部分） | **Domain** | `core/automation/domain/locators.py` | 剥离 `ViewDescriptor.is_present`/`wait_until_present` 对 `driver` 的直接调用。 |
| `locators.py` | `wait_until` | **Application (Use Case)** | `core/automation/use_cases/wait.py` | 注入 `clock`/`sleep`，超时抛 `ElementWaitTimeoutError`（继承 `TimeoutError`）。 |
| `locators.py` | `ViewDescriptor.is_present` / `wait_until_present` | **Application (Use Case)** | `core/automation/use_cases/view_probe.py` | 提取为 `ViewPresenceChecker(driver: UIDriverPort)`。 |
| `locators.py` | `LocatorRegistry` | **Outbound Adapter** | `adapters/outbound/config/locator_registry.py` | 实现 `LocatorStorePort`；隔离 `yaml`、`Path`、`os.environ`；用 `logging` 替代 `print()`。 |
| `locators.py` | `get_global_locator_registry()` | **退休删除** | 由 `main.py` 实例化并注入 | 彻底消除进程级全局可变单例；`BaseBossPage` 与 `AndroidSystemDialogInterceptor` 改为构造函数接收 `LocatorStorePort`。 |
| `interceptors.py` | `BaseInterceptor` | **Application (Port)** | `core/automation/interfaces/interceptor.py` | 升级为 `ScreenInterceptorPort`（ABC/Protocol），参数类型化为 `UIDriverPort`。 |
| `interceptors.py` | `InterceptorRegistry` | **Application (Use Case)** | `core/automation/use_cases/interceptors.py` | 纯拦截器链编排，零 Android 依赖。 |
| `interceptors.py` | `SystemDialogInterceptor` | **Outbound Adapter** | `adapters/outbound/device/appium/interceptors.py` | 重命名为 `AndroidSystemDialogInterceptor`（可保留别名），隔离 `com.android.permissioncontroller:id/...`，强制注入 `LocatorStorePort`。 |
| `llm.py` | `LLMError`、`LLMAuthError`、`LLMTimeoutError` | **Domain (Shared Kernel)** | `core/shared/errors.py` | 与 `BrokerError` 家族并列，供所有 core Use Cases 捕获与传播。 |
| `llm.py` | `LLMDecisionClient` | **Application (Shared Port)** | `core/shared/llm.py` | 纯 ABC/Protocol，**不持有 `LLMConfig`**，零第三方库导入。 |
| `llm.py` | `LLMConfig`、`FRAMEWORK_DEFAULTS`、`MASK_MARKERS`、`PLACEHOLDER_API_KEY`、`configure_langsmith` | **Outbound Adapter** | `adapters/outbound/llm/config.py` | 配置数据对象与环境配置收口到 LLM 适配器，消除 `core/` 污染。 |
| `llm.py` | `_JSONRepairMixin` | **Outbound Adapter** | `adapters/outbound/llm/json_repair.py` | 共享于 `OpenAIChatClient` 与 `AnthropicChatClient` 的响应容错修复器。 |
| `llm.py` | `OpenAIChatClient` | **Outbound Adapter** | `adapters/outbound/llm/openai_client.py` | 纯 OpenAI 兼容 HTTP 协议实现（`requests.post` + MiniMax `thinking` 适配），剥离 `@traceable` 与 `__init__` 中的 `configure_langsmith` 副作用。 |
| `llm.py` | `AnthropicChatClient` | **Outbound Adapter** | `adapters/outbound/llm/anthropic_client.py` | 纯 Anthropic Messages HTTP 协议实现，剥离 `@traceable` 与 `configure_langsmith` 副作用。 |
| `llm.py` | `@traceable` + `get_current_run_tree` 埋点 | **Outbound Adapter** | `adapters/outbound/llm/tracing.py` | 新增 `TracedLLMDecisionClient(inner: LLMDecisionClient, ...)` 装饰器适配器，保持既有 span 名（`OpenAIChatClient.chat_completion` 等）与 `usage`/`model`/`base_url` 元数据契约。 |

### 7.2 Android 强耦合代码的隔离边界

重构后，仓库中**唯一允许导入** `appium`、`selenium`、调用 `adb` 子进程、或硬编码 `com.android.permissioncontroller` / `UiAutomator2` 的位置仅为：
- `src/boss_agent/adapters/outbound/device/appium/`（会话、驱动、W3C ActionChains、ADB、Android 系统弹窗拦截器）
- `src/boss_agent/adapters/outbound/device/pages/`（Boss 直聘 Android 页面定位键与页面对象，仅依赖 `core/automation` 与注入的 `LocatorStorePort` / `HumanizedGestureExecutor`）
- `src/boss_agent/adapters/infra/remote_adb_bridge.py`（既有远端 ADB 隧道守护进程）

任何其他目录（特别是 `src/boss_agent/core/` 和 `src/boss_agent/adapters/memory/`）严禁出现上述导入。

---

## 8. 组装根设计（Composition Root：`src/boss_agent/main.py`）

`src/boss_agent/main.py` 作为唯一的组装根，统一负责具体适配器的实例化、配置加载副作用（如 `configure_langsmith`）以及全链路依赖注入：

1. **定位配置注入（消除全局单例）**：
   - 新增 `build_locator_store(base_config_path=None, local_config_path=None) -> LocatorStorePort`，实例化 `YamlLocatorRegistry`。
   - `build_worker()`、`build_verification_pipeline()`、`build_takeover_handler()` 创建或接收 `locator_store`，并将其透传给 `build_feed_device_world(driver, locator_store=...)`、`build_triage_world(driver, locator_store=...)`、`LoginPage(driver, locator_registry=...)` 以及 `AndroidSystemDialogInterceptor(locator_store=...)`。
2. **设备会话与驱动适配注入**：
   - `build_device_session(device_udid, server_url) -> DeviceSessionPort` 实例化 `AppiumSession(DriverConfig(...))`。
   - `_driver_factory(config)` 通过 `AppiumSession(DriverConfig(...)).start()` 返回 `AppiumDriverAdapter`（满足 `UIDriverPort`），注入 `WorkerContext`。
   - `build_interceptor_registry(locator_store) -> InterceptorRegistry` 实例化 `InterceptorRegistry([AndroidSystemDialogInterceptor(locator_store)])`。
3. **LLM 客户端与观测装饰器注入**：
   - `build_llm_client(...) -> LLMDecisionClient`：在组装根显式调用 `configure_langsmith(llm_cfg)`（移出客户端 `__init__`），由 `create_llm_client(config=llm_cfg)` 按协议实例化 `OpenAIChatClient` 或 `AnthropicChatClient`，再由 `TracedLLMDecisionClient` 包装返回 `LLMDecisionClient` 抽象接口。
   - `build_matching_service`、`build_screener`、`RejectionClassifier`、`ResumeMemoryManager` 均仅接收 `LLMDecisionClient` 抽象，不再感知具体协议或 LangSmith。
4. **替换成本验证**：
   - 换设备驱动（如 Appium → Maestro/uiautomator2-python）：仅新增 `adapters/outbound/device/<new_driver>/` 并修改 `main.py` 中 `build_device_session` / `_driver_factory`。
   - 换定位配置源（如 YAML → PocketBase/远程配置中心）：仅新增 `LocatorStorePort` 实现并修改 `main.py` 中 `build_locator_store`。
   - 换 LLM 供应商或观测平台：仅修改 `adapters/outbound/llm/` 与 `main.py` 中 `build_llm_client`。

---

## 9. 两阶段工作流与授权说明（Two-Phase Refactor）

严格遵循 `docs/architecture/iron-rules.md` 的两阶段流程：

1. **阶段一：现状诊断与重构蓝图（Hard Gate）——已完成**
   - 本 spec 文档即阶段一交付物，涵盖全部耦合违规映射（§4）、完整目标目录树（§5）、四大清单（§6）、模块归位（§7）与组装根方案（§8）。
   - **授权声明**：用户已在本次编排指令中明确授权后续实现步骤，**下游实现 worker 无需再次暂停等待蓝图确认**，可直接按阶段二顺序启动 TDD 实施。
2. **阶段二：TDD 分层实现（严格按依赖由内向外顺序）**
   - **Step 2.1 — Domain 层**：先写红灯单元测试，再实现 `core/shared/errors.py`（补充 `LLMError` 家族）、`core/automation/domain/{errors,geometry,locators}.py` 直到全绿。
   - **Step 2.2 — Application 层**：定义 `core/shared/llm.py` 与 `core/automation/interfaces/{device_session,ui_driver,locator_store,interceptor}.py`；基于内存替身（`adapters/memory/{automation,llm_client}.py`）先写红灯单元测试，再实现 `core/automation/use_cases/{wait,view_probe,gestures,interceptors}.py`，并更新 `core/job`、`core/chat` 的 `LLMDecisionClient` 导入路径直到全绿。
   - **Step 2.3 — Adapters 层**：先写契约/单元测试（Mock HTTP/WebDriver/subprocess/tmp_path YAML，零真实网络/设备），再实现 `adapters/outbound/device/appium/*`、`adapters/outbound/config/locator_registry.py`、`adapters/outbound/llm/*`；同步更新 `adapters/outbound/device/pages/*`、`adapters/outbound/resume/memory.py`、`adapters/inbound/worker/context.py`。
   - **Step 2.4 — Composition Root & 退休旧包**：更新 `src/boss_agent/main.py` 统一装配；迁移 `scripts/` 与 `tests/` 导入路径；删除 `src/droid_agent_core/` 并更新 `pyproject.toml`；收紧 `test_core_isolation.py`（移除 `ALLOWED_THIRD_PARTY` 中 `droid_agent_core` 例外）并更新架构守护测试。

---

## 10. 测试策略与接缝设计（Testing Decisions）

### 10.1 测试接缝（Seams）
- **核心接缝**：优先复用并延续仓库既有的 **Port + `adapters/memory/` 内存双胞胎** 模式（与 `InMemoryTaskBroker`、`InMemoryJobRecordStore`、`FeedDeviceWorld`、`ChatListReader` 保持一致）。
- **时间与随机接缝**：`wait_until` 与 `HumanizedGestureExecutor` 通过构造函数参数接受 `clock`/`sleep`/`rng`，单元测试直接注入零耗时 stub，彻底摆脱 `monkeypatch.setattr(time, "sleep", ...)` 的脆弱全局补丁（同时保持现有 `tests/unit/conftest.py` 兼容，逐步收敛）。
- **外部边界接缝**：
  - LLM 适配器测试：仅在 `requests.post` 边界 mock HTTP 响应，验证协议载荷、状态码→领域异常映射、JSON 修复链与 `TracedLLMDecisionClient` span 元数据。
  - Appium/ADB 适配器测试：仅在 `webdriver.Remote`、`ActionChains`、`subprocess.run` 边界 mock，验证能力转换、W3C 双击降级与异常翻译。
  - YAML 定位器测试：使用 `pytest` `tmp_path` 夹具测试 base/local 深度合并与缺失回退，不触碰仓库真实本地配置。

### 10.2 分层 TDD 测试清单与既有先例（Prior Art）

| 实施阶段 | 测试目标模块 | 对应测试文件（迁移/新增） | 验证重点（只测外部行为） |
|---|---|---|---|
| **1. Domain** | `core/automation/domain/*`、`core/shared/errors.py` | `tests/unit/test_automation_geometry.py`、`tests/unit/test_selector_parsing.py`（迁移自 `test_gestures_and_locators.py`、`test_inline_description_probes.py`） | 抖动边界夹紧、probe 坐标原点换算、贝塞尔起终点精度、双击间隔上限 `< 0.30s`、所有前缀/隐式选择器解析规则。 |
| **2. Application** | `core/automation/use_cases/*`、`core/shared/llm.py` | `tests/unit/test_wait_until_budget.py`、`tests/unit/test_ui_wait_polling.py`、`tests/unit/test_gesture_use_case.py`、`tests/unit/test_interceptor_registry.py`、`tests/unit/test_ui_debug_telemetry.py` | 使用 `FakeUIDriver`/`InMemoryLocatorStore`/零耗时 `sleep` 验证预算不超调、W3C→tap→click 三级降级、拦截器优先级短路、`droid_agent_core.ui` 日志契约。 |
| **3. Adapters** | `adapters/outbound/device/appium/*`、`adapters/outbound/config/locator_registry.py`、`adapters/outbound/llm/*` | `tests/unit/test_llm_client.py`、`tests/unit/test_langsmith_tracing.py`、`tests/unit/test_locator_registry_and_auto_strategy.py`、`tests/unit/test_appium_adapter.py`、`tests/unit/test_config_defaults_parity.py` | OpenAI/Anthropic 协议请求与错误翻译、JSON 六级修复链、LangSmith span 名称与 metadata、base+local YAML 合并、AppiumSession/AdbClient/AndroidSystemDialogInterceptor 契约。 |
| **4. Composition Root & Guards** | `main.py`、全仓依赖方向 | `tests/unit/test_core_isolation.py`、`tests/unit/test_composition_confinement.py`、`tests/unit/test_memory_adapter_imports.py`、`tests/unit/test_cli_llm_factory.py`、`tests/unit/test_package_exports.py` | 确认 `core/` 允许列表**零第三方包**、`adapters/memory/` 零传输依赖、全仓无 `droid_agent_core` 导入、所有 concrete adapter 仅在 `adapters/` 与 `main.py` 构造。 |

### 10.3 强制验证命令
- 快速单元测试全集：`uv run --extra dev pytest`
- 单元预算门禁：`uv run --extra dev python scripts/check_fast_tier_budget.py`
- 静态分析与格式化：`uv run --extra dev ruff check && uv run --extra dev ruff format --check`
- 类型同步守卫：`uv run python scripts/generate_dashboard_types.py --check`

---

## 11. Implementation Decisions & Out of Scope（关键决策与非目标）

### 关键实施决策
1. **ADR 0002 vs 架构铁律裁决**：ADR 0002 曾将 `droid_agent_core` 拆为独立包，导致 ADR 0021 不得不为其开放 `core/` 第三方导入白名单例外。本次集成以 `docs/architecture/iron-rules.md` 为最高准则，将自动化通用能力并入 `boss_agent/core/automation` 与 `boss_agent/adapters/outbound/`，彻底闭合架构例外。若未来需要独立发布，可直接从 `core/automation` + `adapters/outbound/device/appium` 按清晰端口边界切出。
2. **公有观测契约零破坏**：
   - Logger 名称 `"droid_agent_core.ui"` 保持不变（`scripts/worker.py`、日志检索与 `test_ui_debug_telemetry.py` 依赖该通道名）。
   - LangSmith span 名称（`OpenAIChatClient.chat_completion`、`OpenAIChatClient.chat_completion_json`、`AnthropicChatClient.chat_completion`、`AnthropicChatClient.chat_completion_json`）由 `TracedLLMDecisionClient` 保持不变。
3. **异常向后兼容**：`ElementWaitTimeoutError` 同时继承 `AutomationError` 与内置 `TimeoutError`，避免破坏既有 `except TimeoutError` 调用方。
4. **无兼容 Shim 退休**：遵循 ADR 0019 / ADR 0021 先例，不保留空的 `src/droid_agent_core/` 转发包；迁移完成后彻底删除该目录，任何遗漏导入以 `ModuleNotFoundError` 响亮暴露。
5. **E2E 测试同步**：`tests/e2e/test_live_device_smoke.py` 与 `tests/e2e/test_live_search_entry_timing.py` 中的 `AppiumSession`/`DriverConfig` 导入同步改指向 `boss_agent.main.build_device_session` 或 `boss_agent.adapters.outbound.device.appium`（不触发真机运行）。

### Out of Scope（非目标范围）
- 不修改 Boss 直聘业务筛选、打招呼、拒信分类、任务调度等业务规则语义。
- 不改动 Web Dashboard（SvelteKit）前端逻辑或 PocketBase Schema。
- 不引入新的设备自动化引擎（如替换 Appium 为其他框架），本次仅完成隔离与端口化。
- 不在单元阶段运行需要真机/模拟器的 `-m live` 测试。

---

## 12. 完成判据（Acceptance Checklist）

结合铁律 **Compliance Gate** 与本次集成交付目标，所有条目必须全部勾选通过：

- [ ] **Domain 纯净**：`src/boss_agent/core/*/domain/` 与 `core/shared/` 仅导入 Python stdlib 和自身 Domain 类型，零框架、DB、YAML、HTTP、Appium/Selenium 导入。
- [ ] **Application 纯净**：`src/boss_agent/core/` 全目录仅导入 stdlib 与 `boss_agent.core.*`；`tests/unit/test_core_isolation.py` 已移除 `droid_agent_core` 白名单例外并通过。
- [ ] **Adapter 职责单一**：全部 Android/Appium/Selenium/ADB 代码隔离在 `adapters/outbound/device/appium/`；YAML 定位器在 `adapters/outbound/config/locator_registry.py`；HTTP LLM 与 LangSmith 观测在 `adapters/outbound/llm/`；入站适配器翻译领域异常，出站适配器实现 Application Ports。
- [ ] **DI 与无全局单例**：所有跨层依赖均通过构造函数注入抽象 Port；`get_global_locator_registry()` 已彻底移除；`configure_langsmith()` 不再隐藏在 LLM 客户端 `__init__` 中。
- [ ] **单一组装根**：全部具体适配器实例化仅存在于 `adapters/` 与 `src/boss_agent/main.py`；`tests/unit/test_composition_confinement.py` 通过。
- [ ] **目录结构自解释**：`core/automation/{domain,interfaces,use_cases}` 与 `adapters/outbound/{device/appium,config,llm}`、`adapters/memory/` 严格落位，旧目录 `src/droid_agent_core/` 已删除，`pyproject.toml` 已更新，全仓无残留 `from droid_agent_core` / `import droid_agent_core` 导入。
- [ ] **内存单测与门禁全绿**：
  - [ ] `uv run --extra dev pytest` 全量通过，且零真实设备/网络/DB I/O。
  - [ ] `uv run --extra dev python scripts/check_fast_tier_budget.py` 通过。
  - [ ] `uv run --extra dev ruff check && uv run --extra dev ruff format --check` 通过。
  - [ ] `uv run python scripts/generate_dashboard_types.py --check` 通过。
- [ ] **观测契约保留**：`droid_agent_core.ui` 日志通道名与全部 LangSmith span 名称保持不变，相关测试全绿。
