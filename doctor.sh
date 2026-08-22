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
POCKETBASE_URL="${POCKETBASE_URL:-http://127.0.0.1:8090}"
HEALTH_URL="${POCKETBASE_URL%/}/api/health"

if curl -s -f "${HEALTH_URL}" >/dev/null 2>&1; then
    PB_PID="$(cat .boss_agent/pocketbase.pid 2>/dev/null || lsof -ti :8090 2>/dev/null | head -n 1 || echo '')"
    if [[ -n "${PB_PID}" ]]; then
        log_pass "PocketBase 服务在线 (${POCKETBASE_URL}, PID: ${PB_PID}, 日志: .boss_agent/pocketbase.log)"
    else
        log_pass "PocketBase 服务在线 (${POCKETBASE_URL})"
    fi

    # Check collections
    if curl -s -f "${POCKETBASE_URL%/}/api/collections/automation_tasks/records?perPage=1" >/dev/null 2>&1; then
        log_pass "PocketBase 集合 'automation_tasks' 已就绪"
    else
        log_warn "PocketBase 缺少 'automation_tasks' 集合或规则未开放" "./pb.sh provision 或重启 ./pb.sh"
    fi

    if curl -s -f "${POCKETBASE_URL%/}/api/collections/candidate_profiles/records?perPage=1" >/dev/null 2>&1; then
        log_pass "PocketBase 集合 'candidate_profiles' 已就绪"
    else
        log_warn "PocketBase 缺少 'candidate_profiles' 集合或规则未开放" "./pb.sh provision 或重启 ./pb.sh"
    fi
else
    log_fail "PocketBase 服务未启动或不可达 (${POCKETBASE_URL})" "运行 './pb.sh' (前台) 或 './pb.sh start --daemon' (后台启动)"
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

if [[ -d "web/node_modules" ]]; then
    log_pass "Web 前端依赖 node_modules 已安装"
else
    log_fail "Web 前端依赖未安装" "运行: npm --prefix web install"
fi

if curl -s -f "http://127.0.0.1:5173" >/dev/null 2>&1; then
    WEB_PID="$(cat .boss_agent/web.pid 2>/dev/null || lsof -ti :5173 2>/dev/null | head -n 1 || echo '')"
    log_pass "SvelteKit Web 服务正在运行 (http://127.0.0.1:5173${WEB_PID:+, PID: ${WEB_PID}}, 日志: .boss_agent/web.log)"
else
    log_warn "SvelteKit Web 服务尚未启动" "运行: ./web.sh"
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

if pgrep -f "scripts/worker.py" >/dev/null 2>&1; then
    WORKER_PID="$(pgrep -f "scripts/worker.py" | head -n 1)"
    log_pass "Python Worker 守护进程正在运行 (PID: ${WORKER_PID}, 日志: .boss_agent/worker.log)"
else
    log_warn "Python Worker 守护进程尚未启动" "运行: ./run.sh (监听并消费 PocketBase 自动化任务)"
fi

echo ""

# ------------------------------------------------------------------------------
# 4. Appium & Dedicated Android AVD Environment Check
# ------------------------------------------------------------------------------
echo -e "${BOLD}${BLUE}[4/5] Appium 服务与专用 Android AVD 模拟器${NC}"

TARGET_AVD="${ANDROID_AVD:-${AVD_NAME:-}}"
if [[ -z "${TARGET_AVD}" && -f "config/settings.local.yaml" ]]; then
    TARGET_AVD="$(grep -E "^[[:space:]]*avd_name:" config/settings.local.yaml 2>/dev/null | awk '{print $2}' | tr -d '"' | tr -d "'" || true)"
fi
TARGET_AVD="${TARGET_AVD:-boss_avd_arm64}"

if command -v adb >/dev/null 2>&1; then
    RUNNING_AVD_SERIAL=""
    DEV_LIST="$(adb devices 2>/dev/null | grep -E "emulator-[0-9]+" | awk '{print $1}' || true)"
    for dev in ${DEV_LIST}; do
        AVD_FOUND="$(adb -s "${dev}" emu avd name 2>/dev/null | head -n 1 | tr -d '\r\n' || true)"
        if [[ "${AVD_FOUND}" == "${TARGET_AVD}" ]]; then
            RUNNING_AVD_SERIAL="${dev}"
            break
        fi
    done

    if [[ -n "${RUNNING_AVD_SERIAL}" ]]; then
        BOOT_STATUS="$(adb -s "${RUNNING_AVD_SERIAL}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r\n' || true)"
        if [[ "${BOOT_STATUS}" == "1" ]]; then
            log_pass "专用 Android AVD '${TARGET_AVD}' 已开机并就绪 (${RUNNING_AVD_SERIAL}, 日志: .boss_agent/emulator.log)"
        else
            log_warn "专用 Android AVD '${TARGET_AVD}' 正在启动中..." "请等待模拟器启动完毕: ./emulator.sh status"
        fi
    else
        log_warn "专用 Android AVD '${TARGET_AVD}' 尚未启动" "运行: ./emulator.sh 或 ./run.sh emu (启动专用模拟器)"
    fi
else
    log_warn "未在 PATH 中找到 'adb' 命令" "请安装 Android Platform Tools: brew install android-platform-tools"
fi

if curl -s -f "http://127.0.0.1:4723/status" >/dev/null 2>&1; then
    log_pass "Appium 服务正在运行 (http://127.0.0.1:4723)"
else
    log_warn "Appium 服务未启动" "如需执行实机自动化投递，请运行: appium"
fi

echo ""

# ------------------------------------------------------------------------------
# 5. LLM 大模型配置检测
# ------------------------------------------------------------------------------
echo -e "${BOLD}${BLUE}[5/5] LLM 大模型破冰与评分配置${NC}"

LLM_CONFIG_EXISTS=0
if [[ -f "config/llm.local.yaml" ]]; then
    LLM_CONFIG_EXISTS=1
    log_pass "检测到本地大模型配置文件 config/llm.local.yaml"
elif [[ -n "${OPENAI_API_KEY:-}" || -n "${MINIMAX_API_KEY:-}" || -n "${DEEPSEEK_API_KEY:-}" ]]; then
    LLM_CONFIG_EXISTS=1
    log_pass "检测到环境变量中的大模型 API Key"
else
    log_warn "未配置大模型 API Key (将使用规则兜底文案)" "运行: cp config/llm.example.yaml config/llm.local.yaml 并填入 API Key"
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
    echo -e "  - 启动 PocketBase : ${CYAN}./pb.sh start --daemon${NC}"
    echo -e "  - 启动 Web 控制台 : ${CYAN}./web.sh${NC}"
    echo -e "  - 启动 Worker 进程 : ${CYAN}./run.sh${NC}"
    echo -e "  - 执行全量自检     : ${CYAN}./doctor.sh${NC}\n"
    exit 0
else
    echo -e "\n${RED}${BOLD}⚠️ 存在 ${TOTAL_FAIL} 项关键依赖异常，请参考上述提示进行修复后重试。${NC}\n"
    exit 1
fi
