#!/usr/bin/env bash
# ==============================================================================
# Boss Agent Mobile - System Doctor & Diagnostic CLI
# ==============================================================================
# Inspects end-to-end health across PocketBase, SvelteKit Web, Python Worker,
# Appium/Android device, and LLM configuration with actionable remediation steps.
#
# Usage:
#   ./doctor.sh
#   POCKETBASE_URL=http://192.168.1.100:8090 ./doctor.sh
# ==============================================================================

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

# Shared lifecycle primitives and the one config read. Readiness probing is delegated to
# each runner's `status` verb, so the configured ports, the LISTEN-only port checks, the
# cwd-alive checks and the bounded ADB probes are the runners' rules, not copies of them.
# What stays here is the cross-service narrative and the remediation guidance.
# shellcheck source=runner_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/runner_lib.sh"

mkdir -p ".boss_agent"

# ANSI Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

TOTAL_PASS=0
TOTAL_WARN=0
TOTAL_FAIL=0

log_pass() {
    echo -e "  ${GREEN}✓ [PASS]${NC} $1"
    TOTAL_PASS=$((TOTAL_PASS + 1))
}

log_warn() {
    echo -e "  ${YELLOW}⚠ [WARN]${NC} $1"
    if [[ -n "${2:-}" ]]; then
        echo -e "    ${BOLD}💡 建议修复:${NC} $2"
    fi
    TOTAL_WARN=$((TOTAL_WARN + 1))
}

log_fail() {
    echo -e "  ${RED}✗ [FAIL]${NC} $1"
    if [[ -n "${2:-}" ]]; then
        echo -e "    ${BOLD}💡 修复方案:${NC} $2"
    fi
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
}

echo -e "\n${BOLD}${CYAN}══════════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}${CYAN}   🩺  Boss Agent Mobile - 全链路健康体检 (System Doctor)          ${NC}"
echo -e "${BOLD}${CYAN}══════════════════════════════════════════════════════════════════${NC}\n"

# ------------------------------------------------------------------------------
# 1. PocketBase State Stream Broker Check
# ------------------------------------------------------------------------------
echo -e "${BOLD}${BLUE}[1/5] PocketBase State Stream 数据库与状态流${NC}"
POCKETBASE_URL="${POCKETBASE_URL:-$(runner_config_value pocketbase_url http://127.0.0.1:8090 pb_url)}"
# The runner's status probes the port it is configured for; hand it the configured URL so a
# non-default broker is what gets checked rather than the historical default.
export PB_HTTP="${PB_HTTP:-${POCKETBASE_URL#*://}}"

if ./pocketbase.sh status >/dev/null 2>&1; then
    log_pass "PocketBase 服务在线 (${POCKETBASE_URL}, 日志: .boss_agent/pocketbase.log)"

    # Check collections
    if curl -s -f "${POCKETBASE_URL%/}/api/collections/automation_tasks/records?perPage=1" >/dev/null 2>&1; then
        log_pass "PocketBase 集合 'automation_tasks' 已就绪"
    else
        log_warn "PocketBase 缺少 'automation_tasks' 集合或规则未开放" "./pocketbase.sh provision 或重启 ./pocketbase.sh"
    fi

    if curl -s -f "${POCKETBASE_URL%/}/api/collections/candidate_profiles/records?perPage=1" >/dev/null 2>&1; then
        log_pass "PocketBase 集合 'candidate_profiles' 已就绪"
    else
        log_warn "PocketBase 缺少 'candidate_profiles' 集合或规则未开放" "./pocketbase.sh provision 或重启 ./pocketbase.sh"
    fi
else
    log_fail "PocketBase 服务未启动或不可达 (${POCKETBASE_URL})" "运行 './pocketbase.sh' (前台) 或 './pocketbase.sh start --daemon' (后台启动)"
fi

echo ""

# ------------------------------------------------------------------------------
# 2. Web Dashboard (SvelteKit) Check
# ------------------------------------------------------------------------------
echo -e "${BOLD}${BLUE}[2/5] SvelteKit Web 控制台${NC}"

if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    NODE_VER="$(node -v)"
    NPM_VER="$(npm -v)"
    log_pass "Node.js (${NODE_VER}) 与 npm (${NPM_VER}) 环境正常"
else
    log_fail "未找到 Node.js 或 npm 环境" "请安装 Node.js (推荐 v20+): brew install node"
fi

if [[ -d "web/node_modules" ]] && npm --prefix web ls --depth=0 >/dev/null 2>&1; then
    log_pass "Web 前端依赖 node_modules 已安装且满足 package.json"
elif [[ -d "web/node_modules" ]]; then
    log_warn "Web 前端依赖存在缺失或未满足项 (UNMET DEPENDENCIES)" "运行: npm --prefix web install"
else
    log_fail "Web 前端依赖未安装" "运行: npm --prefix web install"
fi

if WEB_STATUS="$(./dashboard.sh status 2>&1)"; then
    log_pass "SvelteKit Web 服务正在运行 (http://127.0.0.1:${WEB_PORT:-5173}, 日志: .boss_agent/web.log)"
elif [[ "${WEB_STATUS}" == *STALE* ]]; then
    log_fail "SvelteKit Web 服务运行于已删除的旧目录，无法加载页面模块！" "运行: ./dashboard.sh restart"
else
    log_warn "SvelteKit Web 服务尚未启动" "运行: ./dashboard.sh"
fi

echo ""

# ------------------------------------------------------------------------------
# 3. Python & Worker Daemon Check
# ------------------------------------------------------------------------------
echo -e "${BOLD}${BLUE}[3/5] Python Worker 守护进程与运行环境${NC}"

if command -v uv >/dev/null 2>&1; then
    UV_VER="$(uv --version 2>/dev/null || echo 'uv')"
    log_pass "Python 包管理器已就绪 (${UV_VER})"
elif command -v python3 >/dev/null 2>&1; then
    PY_VER="$(python3 --version 2>/dev/null || echo 'python3')"
    log_pass "Python 运行环境已就绪 (${PY_VER})"
else
    log_fail "未找到 Python 3 或 uv" "请安装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

if ./worker.sh status >/dev/null 2>&1; then
    log_pass "Python Worker 守护进程正在运行 (日志: .boss_agent/worker.log)"
else
    log_warn "Python Worker 守护进程尚未启动" "运行: ./run.sh (监听并消费 PocketBase 自动化任务)"
fi

echo ""

# ------------------------------------------------------------------------------
# 4. Appium & Dedicated Android AVD Environment Check
# ------------------------------------------------------------------------------
echo -e "${BOLD}${BLUE}[4/5] Appium 服务与专用 Android AVD 模拟器${NC}"

# `emulator.sh status` is the one owner of the AVD/bridge verdicts: it resolves the
# dedicated AVD, bounds every ADB query, and distinguishes ONLINE, BOOTING and absent.
EMU_STATUS=""
if EMU_STATUS="$(./emulator.sh status 2>&1)"; then
    log_pass "专用 Android AVD 已开机并就绪 (日志: .boss_agent/emulator.log)"

    # A booted AVD whose bridge has died is the outage that looks healthy from the console
    # and only shows up as "cannot connect" for a remote client, so it is called out
    # separately -- with the command that repairs it, because the obvious reflex (restart
    # the emulator) throws away the running session for nothing. The runner already told
    # us which of the two failure shapes it saw.
    if [[ "${EMU_STATUS}" == *"Remote ADB Bridge is NOT RUNNING"* ]]; then
        log_warn "Remote ADB Bridge 未运行，远程客户端将无法连接" "运行: ./emulator.sh reconnect（无需重启模拟器即可恢复局域网 ADB 访问）"
    elif [[ "${EMU_STATUS}" == *"LAN ADB Connection is DISCONNECTED"* ]]; then
        log_warn "Remote ADB Bridge 在运行，但局域网 ADB 连接已断开" "运行: ./emulator.sh reconnect（无需重启模拟器即可恢复局域网 ADB 访问）"
    fi
elif [[ "${EMU_STATUS}" == *"BOOTING"* ]]; then
    log_warn "专用 Android AVD 正在启动中..." "请等待模拟器启动完毕: ./emulator.sh status"
else
    log_warn "专用 Android AVD 尚未启动" "运行: ./emulator.sh 或 ./run.sh emu (启动专用模拟器)"
fi

APPIUM_URL="${APPIUM_URL:-$(runner_config_value server_url http://127.0.0.1:4723 appium_url)}"
if [[ -z "${APPIUM_PORT:-}" ]]; then
    APPIUM_PORT="${APPIUM_URL##*:}"
    APPIUM_PORT="${APPIUM_PORT%%/*}"
fi
export APPIUM_PORT

if ./appium.sh status >/dev/null 2>&1; then
    log_pass "Appium 服务正在运行 (${APPIUM_URL}, 日志: .boss_agent/appium.log)"
else
    log_warn "Appium 服务尚未启动" "运行: ./appium.sh 或 ./run.sh appium (或 ./appium.sh start --daemon)"
fi

echo ""

# ------------------------------------------------------------------------------
# 5. LLM 大模型配置检测
# ------------------------------------------------------------------------------
echo -e "${BOLD}${BLUE}[5/5] LLM 大模型破冰与评分配置${NC}"

LLM_CONFIG_EXISTS=0
if [[ -n "$(runner_config_value api_key "")" ]]; then
    # The realm resolves the chain *and* rejects a masked or template value, so this
    # is the same question the grep chain asked, asked once and through the guard that
    # stops a `••••` display value being mistaken for a key.
    LLM_CONFIG_EXISTS=1
    log_pass "检测到可用的本地大模型 API Key"
elif [[ -f "config/llm.local.yaml" ]]; then
    LLM_CONFIG_EXISTS=1
    log_pass "检测到本地大模型配置文件 config/llm.local.yaml"
elif [[ -n "${OPENAI_API_KEY:-}" || -n "${MINIMAX_API_KEY:-}" || -n "${DEEPSEEK_API_KEY:-}" || -n "${LLM_API_KEY:-}" ]]; then
    LLM_CONFIG_EXISTS=1
    log_pass "检测到环境变量中的大模型 API Key"
else
    log_warn "未配置大模型 API Key (将使用规则兜底文案)" "运行: cp config/settings.example.yaml config/settings.local.yaml 并填入 API Key，或在网页端 /settings 配置"
fi

echo ""

# ------------------------------------------------------------------------------
# Summary Table
# ------------------------------------------------------------------------------
echo -e "${BOLD}${CYAN}══════════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}诊断结果汇总:${NC} ${GREEN}${TOTAL_PASS} 项通过${NC} | ${YELLOW}${TOTAL_WARN} 项待关注${NC} | ${RED}${TOTAL_FAIL} 项失败${NC}"

if [[ ${TOTAL_FAIL} -eq 0 ]]; then
    echo -e "\n${GREEN}${BOLD}🎉 核心组件全部正常！系统已处于就绪状态。${NC}"
    echo -e "常用指令推荐:"
    echo -e "  - 启动 PocketBase : ${CYAN}./pocketbase.sh start --daemon${NC}"
    echo -e "  - 启动 Web 控制台 : ${CYAN}./dashboard.sh${NC}"
    echo -e "  - 启动 Worker 进程 : ${CYAN}./run.sh${NC}"
    echo -e "  - 执行全量自检     : ${CYAN}./doctor.sh${NC}\n"
    exit 0
else
    echo -e "\n${RED}${BOLD}⚠️ 存在 ${TOTAL_FAIL} 项关键依赖异常，请参考上述提示进行修复后重试。${NC}\n"
    exit 1
fi
