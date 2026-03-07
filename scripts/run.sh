#!/bin/bash

# MaiCore & NapCat Adapter一键安装脚本 by Cookie_987
# 适用于macOS/Arch/Ubuntu 24.10/Debian 12/CentOS 9
# 请小心使用任何一键脚本！

INSTALLER_VERSION="0.0.5-refactor"
LANG=C.UTF-8

# 如无法访问GitHub请修改此处镜像地址
GITHUB_REPO="https://ghfast.top/https://github.com"

# 颜色输出
GREEN="\e[32m"
RED="\e[31m"
RESET="\e[0m"

# 需要的基本软件包（兼容 Bash 3，避免使用关联数组）
REQUIRED_PACKAGES_COMMON="git sudo python3 curl gnupg"
REQUIRED_PACKAGES_DEBIAN="python3-venv python3-pip build-essential"
REQUIRED_PACKAGES_UBUNTU="python3-venv python3-pip build-essential"
REQUIRED_PACKAGES_CENTOS="epel-release python3-pip python3-devel gcc gcc-c++ make"
REQUIRED_PACKAGES_ARCH="python-virtualenv python-pip base-devel"
REQUIRED_PACKAGES_MACOS="git gnupg python"

# 服务名称
SERVICE_NAME="maicore"
SERVICE_NAME_WEB="maicore-web"
SERVICE_NAME_NBADAPTER="maibot-napcat-adapter"

SERVICE_USER="${SUDO_USER:-$USER}"
SERVICE_HOME="$(eval echo "~${SERVICE_USER}" 2>/dev/null)"
if [[ -z "$SERVICE_HOME" || "$SERVICE_HOME" == "~${SERVICE_USER}" ]]; then
    SERVICE_HOME="$HOME"
fi

IS_MACOS=false
[[ "$(uname -s)" == "Darwin" ]] && IS_MACOS=true

INSTALL_CONF="/etc/maicore_install.conf"

# 默认项目目录
DEFAULT_INSTALL_DIR="/opt/maicore"
if [[ "$IS_MACOS" == true ]]; then
    DEFAULT_INSTALL_DIR="${SERVICE_HOME}/maicore"
    INSTALL_CONF="${SERVICE_HOME}/.config/maicore/maicore_install.conf"
fi

LAUNCHD_DOMAIN=""
LAUNCHD_AGENT_DIR=""
LAUNCHD_LABEL_MAIN="com.maicore.${SERVICE_NAME}"
LAUNCHD_LABEL_NBADAPTER="com.maicore.${SERVICE_NAME_NBADAPTER}"
LAUNCHD_PLIST_MAIN=""
LAUNCHD_PLIST_NBADAPTER=""

if [[ "$IS_MACOS" == true ]]; then
    SERVICE_UID="$(id -u "${SERVICE_USER}" 2>/dev/null || id -u)"
    LAUNCHD_DOMAIN="gui/${SERVICE_UID}"
    LAUNCHD_AGENT_DIR="${SERVICE_HOME}/Library/LaunchAgents"
    LAUNCHD_PLIST_MAIN="${LAUNCHD_AGENT_DIR}/${LAUNCHD_LABEL_MAIN}.plist"
    LAUNCHD_PLIST_NBADAPTER="${LAUNCHD_AGENT_DIR}/${LAUNCHD_LABEL_NBADAPTER}.plist"
fi

get_required_packages() {
    local distro="$1"
    case "$distro" in
    debian)
        echo "${REQUIRED_PACKAGES_COMMON} ${REQUIRED_PACKAGES_DEBIAN}"
        ;;
    ubuntu)
        echo "${REQUIRED_PACKAGES_COMMON} ${REQUIRED_PACKAGES_UBUNTU}"
        ;;
    centos)
        echo "${REQUIRED_PACKAGES_COMMON} ${REQUIRED_PACKAGES_CENTOS}"
        ;;
    arch)
        echo "${REQUIRED_PACKAGES_COMMON} ${REQUIRED_PACKAGES_ARCH}"
        ;;
    macos)
        echo "${REQUIRED_PACKAGES_MACOS}"
        ;;
    *)
        echo "${REQUIRED_PACKAGES_COMMON}"
        ;;
    esac
}

IS_INSTALL_NAPCAT=false
IS_INSTALL_DEPENDENCIES=false

resolve_brew_bin() {
    local brew_bin
    brew_bin="$(command -v brew)"
    [[ -z "$brew_bin" && -x /opt/homebrew/bin/brew ]] && brew_bin="/opt/homebrew/bin/brew"
    [[ -z "$brew_bin" && -x /usr/local/bin/brew ]] && brew_bin="/usr/local/bin/brew"
    [[ -n "$brew_bin" ]] && echo "$brew_bin"
}

run_brew() {
    local brew_bin
    brew_bin="$(resolve_brew_bin)"

    [[ -z "$brew_bin" ]] && return 1
    if [[ "$(id -u)" -eq 0 && -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        sudo -u "${SUDO_USER}" "${brew_bin}" "$@"
    else
        "${brew_bin}" "$@"
    fi
}

run_launchctl() {
    if [[ "$(id -u)" -eq 0 && -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        sudo -u "${SUDO_USER}" launchctl "$@"
    else
        launchctl "$@"
    fi
}

ensure_writable_parent() {
    local path="$1"
    local parent
    parent="$(dirname "$path")"
    mkdir -p "$parent"
    if [[ "$IS_MACOS" == true && "$(id -u)" -eq 0 && -n "${SUDO_USER:-}" ]]; then
        chown "${SUDO_USER}" "$parent" 2>/dev/null || true
    fi
}

save_install_info() {
    ensure_writable_parent "$INSTALL_CONF"
    cat > "$INSTALL_CONF" <<EOF
INSTALLER_VERSION=${INSTALLER_VERSION}
INSTALL_DIR=${INSTALL_DIR}
BRANCH=${BRANCH}
EOF
}

compute_md5() {
    local file_path="$1"

    if command -v md5sum &>/dev/null; then
        md5sum "$file_path" | awk '{print $1}'
    elif command -v md5 &>/dev/null; then
        md5 -q "$file_path"
    else
        return 1
    fi
}

launchd_label_for_service() {
    local service="$1"
    case "$service" in
    ${SERVICE_NAME})
        echo "$LAUNCHD_LABEL_MAIN"
        ;;
    ${SERVICE_NAME_NBADAPTER})
        echo "$LAUNCHD_LABEL_NBADAPTER"
        ;;
    *)
        return 1
        ;;
    esac
}

launchd_plist_for_service() {
    local service="$1"
    case "$service" in
    ${SERVICE_NAME})
        echo "$LAUNCHD_PLIST_MAIN"
        ;;
    ${SERVICE_NAME_NBADAPTER})
        echo "$LAUNCHD_PLIST_NBADAPTER"
        ;;
    *)
        return 1
        ;;
    esac
}

is_launchd_service_loaded() {
    local service="$1"
    local label
    label="$(launchd_label_for_service "$service")" || return 1
    run_launchctl print "${LAUNCHD_DOMAIN}/${label}" &>/dev/null
}

start_service() {
    local service="$1"
    if [[ "$IS_MACOS" == true ]]; then
        local label
        local plist
        label="$(launchd_label_for_service "$service")" || return 1
        plist="$(launchd_plist_for_service "$service")" || return 1
        if [[ ! -f "$plist" && -d "${INSTALL_DIR}/MaiBot" ]]; then
            create_launchd_services
        fi
        if [[ ! -f "$plist" ]]; then
            echo -e "${RED}未找到服务配置文件：${plist}${RESET}"
            return 1
        fi

        if is_launchd_service_loaded "$service"; then
            run_launchctl kickstart -k "${LAUNCHD_DOMAIN}/${label}"
        else
            run_launchctl bootstrap "${LAUNCHD_DOMAIN}" "$plist"
        fi
    else
        systemctl start "$service"
    fi
}

stop_service() {
    local service="$1"
    if [[ "$IS_MACOS" == true ]]; then
        local label
        label="$(launchd_label_for_service "$service")" || return 1
        if is_launchd_service_loaded "$service"; then
            run_launchctl bootout "${LAUNCHD_DOMAIN}/${label}"
        fi
    else
        systemctl stop "$service"
    fi
}

restart_service() {
    local service="$1"
    if [[ "$IS_MACOS" == true ]]; then
        stop_service "$service"
        start_service "$service"
    else
        systemctl restart "$service"
    fi
}

create_launchd_services() {
    mkdir -p "${LAUNCHD_AGENT_DIR}"
    mkdir -p "${INSTALL_DIR}/logs"

    cat > "${LAUNCHD_PLIST_MAIN}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LAUNCHD_LABEL_MAIN}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${INSTALL_DIR}/venv/bin/python3</string>
    <string>bot.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${INSTALL_DIR}/MaiBot</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${INSTALL_DIR}/logs/${SERVICE_NAME}.log</string>
  <key>StandardErrorPath</key>
  <string>${INSTALL_DIR}/logs/${SERVICE_NAME}.error.log</string>
</dict>
</plist>
EOF

    cat > "${LAUNCHD_PLIST_NBADAPTER}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LAUNCHD_LABEL_NBADAPTER}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${INSTALL_DIR}/venv/bin/python3</string>
    <string>main.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${INSTALL_DIR}/MaiBot-Napcat-Adapter</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${INSTALL_DIR}/logs/${SERVICE_NAME_NBADAPTER}.log</string>
  <key>StandardErrorPath</key>
  <string>${INSTALL_DIR}/logs/${SERVICE_NAME_NBADAPTER}.error.log</string>
</dict>
</plist>
EOF

    if [[ "$(id -u)" -eq 0 && -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
        chown "${SUDO_USER}" "${LAUNCHD_PLIST_MAIN}" "${LAUNCHD_PLIST_NBADAPTER}" "${LAUNCHD_AGENT_DIR}" 2>/dev/null || true
    fi
}

# 检查是否已安装
check_installed() {
    if [[ "$IS_MACOS" == true ]]; then
        [[ -f "$INSTALL_CONF" ]]
    else
        [[ -f /etc/systemd/system/${SERVICE_NAME}.service ]]
    fi
}

# 加载安装信息
load_install_info() {
    if [[ -f "$INSTALL_CONF" ]]; then
        source "$INSTALL_CONF"
    else
        INSTALL_DIR="$DEFAULT_INSTALL_DIR"
        BRANCH="refactor"
    fi
}

# 显示管理菜单
show_menu() {
    while true; do
        choice=$(whiptail --title "MaiCore管理菜单" --menu "请选择要执行的操作：" 15 60 7 \
            "1" "启动MaiCore" \
            "2" "停止MaiCore" \
            "3" "重启MaiCore" \
            "4" "启动NapCat Adapter" \
            "5" "停止NapCat Adapter" \
            "6" "重启NapCat Adapter" \
            "7" "拉取最新MaiCore仓库" \
            "8" "切换分支" \
            "9" "退出" 3>&1 1>&2 2>&3)

        [[ $? -ne 0 ]] && exit 0

        case "$choice" in
            1)
                start_service "${SERVICE_NAME}"
                whiptail --msgbox "✅MaiCore已启动" 10 60
                ;;
            2)
                stop_service "${SERVICE_NAME}"
                whiptail --msgbox "🛑MaiCore已停止" 10 60
                ;;
            3)
                restart_service "${SERVICE_NAME}"
                whiptail --msgbox "🔄MaiCore已重启" 10 60
                ;;
            4)
                start_service "${SERVICE_NAME_NBADAPTER}"
                whiptail --msgbox "✅NapCat Adapter已启动" 10 60
                ;;
            5)
                stop_service "${SERVICE_NAME_NBADAPTER}"
                whiptail --msgbox "🛑NapCat Adapter已停止" 10 60
                ;;
            6)
                restart_service "${SERVICE_NAME_NBADAPTER}"
                whiptail --msgbox "🔄NapCat Adapter已重启" 10 60
                ;;
            7)
                update_dependencies
                ;;
            8)
                switch_branch
                ;;
            9)
                exit 0
                ;;
            *)
                whiptail --msgbox "无效选项！" 10 60
                ;;
        esac
    done
}

# 更新依赖
update_dependencies() {
    whiptail --title "⚠" --msgbox "更新后请阅读教程" 10 60
    stop_service "${SERVICE_NAME}"
    cd "${INSTALL_DIR}/MaiBot" || {
        whiptail --msgbox "🚫 无法进入安装目录！" 10 60
        return 1
    }
    if ! git pull origin "${BRANCH}"; then
        whiptail --msgbox "🚫 代码更新失败！" 10 60
        return 1
    fi
    source "${INSTALL_DIR}/venv/bin/activate"
    if ! pip install -r requirements.txt; then
        whiptail --msgbox "🚫 依赖安装失败！" 10 60
        deactivate
        return 1
    fi
    deactivate
    whiptail --msgbox "✅ 已停止服务并拉取最新仓库提交" 10 60
}

# 切换分支
switch_branch() {
    new_branch=$(whiptail --inputbox "请输入要切换的分支名称：" 10 60 "${BRANCH}" 3>&1 1>&2 2>&3)
    [[ -z "$new_branch" ]] && {
        whiptail --msgbox "🚫 分支名称不能为空！" 10 60
        return 1
    }

    cd "${INSTALL_DIR}/MaiBot" || {
        whiptail --msgbox "🚫 无法进入安装目录！" 10 60
        return 1
    }

    if ! git ls-remote --exit-code --heads origin "${new_branch}" >/dev/null 2>&1; then
        whiptail --msgbox "🚫 分支 ${new_branch} 不存在！" 10 60
        return 1
    fi

    if ! git checkout "${new_branch}"; then
        whiptail --msgbox "🚫 分支切换失败！" 10 60
        return 1
    fi

    if ! git pull origin "${new_branch}"; then
        whiptail --msgbox "🚫 代码拉取失败！" 10 60
        return 1
    fi
    stop_service "${SERVICE_NAME}"
    source "${INSTALL_DIR}/venv/bin/activate"
    pip install -r requirements.txt
    deactivate

    BRANCH="${new_branch}"
    save_install_info
    check_eula
    whiptail --msgbox "✅ 已停止服务并切换到分支 ${new_branch} ！" 10 60
}

check_eula() {
    # 首先计算当前EULA的MD5值
    current_md5=$(compute_md5 "${INSTALL_DIR}/MaiBot/EULA.md")

    # 首先计算当前隐私条款文件的哈希值
    current_md5_privacy=$(compute_md5 "${INSTALL_DIR}/MaiBot/PRIVACY.md")

    # 如果当前的md5值为空，则直接返回
    if [[ -z $current_md5 || -z $current_md5_privacy ]]; then
        whiptail --msgbox "🚫 未找到使用协议\n 请检查PRIVACY.md和EULA.md是否存在" 10 60
    fi

    # 检查eula.confirmed文件是否存在
    if [[ -f ${INSTALL_DIR}/MaiBot/eula.confirmed ]]; then
        # 如果存在则检查其中包含的md5与current_md5是否一致
        confirmed_md5=$(cat "${INSTALL_DIR}/MaiBot/eula.confirmed")
    else
        confirmed_md5=""
    fi

    # 检查privacy.confirmed文件是否存在
    if [[ -f ${INSTALL_DIR}/MaiBot/privacy.confirmed ]]; then
        # 如果存在则检查其中包含的md5与current_md5是否一致
        confirmed_md5_privacy=$(cat "${INSTALL_DIR}/MaiBot/privacy.confirmed")
    else
        confirmed_md5_privacy=""
    fi

    # 如果EULA或隐私条款有更新，提示用户重新确认
    if [[ $current_md5 != $confirmed_md5 || $current_md5_privacy != $confirmed_md5_privacy ]]; then
        whiptail --title "📜 使用协议更新" --yesno "检测到MaiCore EULA或隐私条款已更新。\nhttps://github.com/MaiM-with-u/MaiBot/blob/refactor/EULA.md\nhttps://github.com/MaiM-with-u/MaiBot/blob/refactor/PRIVACY.md\n\n您是否同意上述协议？ \n\n " 12 70
        if [[ $? -eq 0 ]]; then
            echo -n "$current_md5" > "${INSTALL_DIR}/MaiBot/eula.confirmed"
            echo -n "$current_md5_privacy" > "${INSTALL_DIR}/MaiBot/privacy.confirmed"
        else
            exit 1
        fi
    fi

}

# 测速并选择PyPI源（仅当阿里云更快时使用阿里云）
measure_url_latency() {
    local url="$1"
    local latency

    latency=$(curl -sS -o /dev/null -w "%{time_total}" --connect-timeout 3 --max-time 8 "$url" 2>/dev/null)

    if [[ $? -eq 0 && "$latency" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
        echo "$latency"
        return 0
    else
        echo "999999"
        return 1
    fi
}

resolve_default_pypi_index_url() {
    local default_url=""

    if [[ -n "${PIP_INDEX_URL:-}" ]]; then
        default_url="$PIP_INDEX_URL"
    elif [[ -n "${UV_INDEX_URL:-}" ]]; then
        default_url="$UV_INDEX_URL"
    elif command -v pip &>/dev/null; then
        default_url=$(pip config get global.index-url 2>/dev/null | head -n 1)
        if [[ -z "$default_url" ]]; then
            default_url=$(pip config get install.index-url 2>/dev/null | head -n 1)
        fi
    fi

    if [[ -z "$default_url" ]]; then
        default_url="https://pypi.org/simple"
    fi

    echo "$default_url"
}

select_pypi_index_url() {
    local default_url
    local aliyun_url="https://mirrors.aliyun.com/pypi/simple"
    local default_latency
    local aliyun_latency
    local default_status
    local aliyun_status

    default_url=$(resolve_default_pypi_index_url)
    default_latency=$(measure_url_latency "$default_url")
    default_status=$?
    aliyun_latency=$(measure_url_latency "$aliyun_url")
    aliyun_status=$?

    if [[ $aliyun_status -eq 0 && $default_status -ne 0 ]]; then
        PYPI_INDEX_URL="$aliyun_url"
        PYPI_INDEX_NAME="阿里云镜像（默认源测速失败）"
        UV_PIP_INDEX_OPTION=(-i "$aliyun_url")
        echo -e "${RED}默认源测速失败，已选择${PYPI_INDEX_NAME}：${PYPI_INDEX_URL}${RESET}"
        return
    fi

    if [[ $aliyun_status -ne 0 && $default_status -eq 0 ]]; then
        PYPI_INDEX_URL="$default_url"
        PYPI_INDEX_NAME="默认源（阿里云测速失败）"
        UV_PIP_INDEX_OPTION=()
        echo -e "${RED}阿里云测速失败，已选择${PYPI_INDEX_NAME}：不显式指定 -i 参数${RESET}"
        return
    fi

    if [[ $aliyun_status -ne 0 && $default_status -ne 0 ]]; then
        PYPI_INDEX_URL="$default_url"
        PYPI_INDEX_NAME="默认源（双源测速失败）"
        UV_PIP_INDEX_OPTION=()
        echo -e "${RED}默认源和阿里云测速均失败，回退到${PYPI_INDEX_NAME}：不显式指定 -i 参数${RESET}"
        return
    fi

    if awk "BEGIN {exit !(${aliyun_latency} < ${default_latency})}"; then
        PYPI_INDEX_URL="$aliyun_url"
        PYPI_INDEX_NAME="阿里云镜像"
        UV_PIP_INDEX_OPTION=(-i "$aliyun_url")
    else
        PYPI_INDEX_URL="$default_url"
        PYPI_INDEX_NAME="默认源"
        UV_PIP_INDEX_OPTION=()
    fi

    if [[ ${#UV_PIP_INDEX_OPTION[@]} -gt 0 ]]; then
        echo -e "${GREEN}已选择${PYPI_INDEX_NAME}：${PYPI_INDEX_URL}${RESET}"
    else
        echo -e "${GREEN}已选择${PYPI_INDEX_NAME}：不显式指定 -i 参数${RESET}"
    fi
}

# ----------- 主安装流程 -----------
run_installation() {
    # 1/6: 检测是否安装 whiptail
    if ! command -v whiptail &>/dev/null; then
        echo -e "${RED}[1/6] whiptail 未安装，正在安装...${RESET}"

        if command -v apt-get &>/dev/null; then
            apt-get update && apt-get install -y whiptail
        elif command -v pacman &>/dev/null; then
            pacman -Syu --noconfirm whiptail
        elif command -v yum &>/dev/null; then
            yum install -y whiptail
        elif command -v brew &>/dev/null || [[ -x /opt/homebrew/bin/brew ]] || [[ -x /usr/local/bin/brew ]]; then
            run_brew install newt

            # 确保当前 shell 能找到 Homebrew 安装的 whiptail。
            [[ -x /opt/homebrew/bin/whiptail ]] && export PATH="/opt/homebrew/bin:${PATH}"
            [[ -x /usr/local/bin/whiptail ]] && export PATH="/usr/local/bin:${PATH}"
        else
            echo -e "${RED}[Error] 无受支持的包管理器，无法安装 whiptail!${RESET}"
            exit 1
        fi

        if ! command -v whiptail &>/dev/null; then
            echo -e "${RED}[Error] whiptail 安装失败或不可用，请手动安装后重试。${RESET}"
            exit 1
        fi
    fi

    whiptail --title "ℹ️ 提示" --msgbox "如果您没有特殊需求，请优先使用docker方式部署。" 10 60

    # 协议确认
    if ! (whiptail --title "ℹ️ [1/6] 使用协议" --yes-button "我同意" --no-button "我拒绝" --yesno "使用MaiCore及此脚本前请先阅读EULA协议及隐私协议\nhttps://github.com/MaiM-with-u/MaiBot/blob/refactor/EULA.md\nhttps://github.com/MaiM-with-u/MaiBot/blob/refactor/PRIVACY.md\n\n您是否同意上述协议？" 12 70); then
        exit 1
    fi

    # 欢迎信息
    whiptail --title "[2/6] 欢迎使用MaiCore一键安装脚本 by Cookie987" --msgbox "检测到您未安装MaiCore，将自动进入安装流程，安装完成后再次运行此脚本即可进入管理菜单。\n\n项目处于活跃开发阶段，代码可能随时更改\n文档未完善，有问题可以提交 Issue 或者 Discussion\nQQ机器人存在被限制风险，请自行了解，谨慎使用\n由于持续迭代，可能存在一些已知或未知的bug\n由于开发中，可能消耗较多token\n\n本脚本可能更新不及时，如遇到bug请优先尝试手动部署以确定是否为脚本问题" 17 60

    # 系统检查
    check_system() {
        if [[ "$IS_MACOS" == true ]]; then
            ID="macos"
            VERSION_ID="$(sw_vers -productVersion 2>/dev/null)"
            PRETTY_NAME="macOS ${VERSION_ID}"
            return
        fi

        if [[ "$(id -u)" -ne 0 ]]; then
            whiptail --title "🚫 权限不足" --msgbox "请使用 root 用户运行此脚本！\n执行方式: sudo bash $0" 10 60
            exit 1
        fi

        if [[ -f /etc/os-release ]]; then
            source /etc/os-release
            if [[ "$ID" == "debian" && "$VERSION_ID" == "12" ]]; then
                return
            elif [[ "$ID" == "ubuntu" && "$VERSION_ID" == "24.10" ]]; then
                return
            elif [[ "$ID" == "centos" && "$VERSION_ID" == "9" ]]; then
                return
            elif [[ "$ID" == "arch" ]]; then
                whiptail --title "⚠️ 兼容性警告" --msgbox "NapCat无可用的 Arch Linux 官方安装方法，将无法自动安装NapCat。\n\n您可尝试在AUR中搜索相关包。" 10 60
                return
            else
                whiptail --title "🚫 不支持的系统" --msgbox "此脚本仅支持 Arch/Debian 12 (Bookworm)/Ubuntu 24.10 (Oracular Oriole)/CentOS9！\n当前系统: $PRETTY_NAME\n安装已终止。" 10 60
                exit 1
            fi
        else
            whiptail --title "⚠️ 无法检测系统" --msgbox "无法识别系统版本，安装已终止。" 10 60
            exit 1
        fi
    }
    check_system

    # 设置包管理器
    case "$ID" in
        debian|ubuntu)
            PKG_MANAGER="apt"
            ;;
        centos)
            PKG_MANAGER="yum"
            ;;
        arch)  
            # 添加arch包管理器
            PKG_MANAGER="pacman"
            ;;
        macos)
            PKG_MANAGER="brew"
            ;;
    esac

    # 检查NapCat
    check_napcat() {
        if command -v napcat &>/dev/null; then
            NAPCAT_INSTALLED=true
        else
            NAPCAT_INSTALLED=false
        fi
    }
    check_napcat

    # 安装必要软件包
    install_packages() {
        missing_packages=()
        # 检查 common 及当前系统专属依赖
        for package in $(get_required_packages "$ID"); do
            case "$PKG_MANAGER" in
            apt)
                dpkg -s "$package" &>/dev/null || missing_packages+=("$package")
                ;;
            yum)
                rpm -q "$package" &>/dev/null || missing_packages+=("$package")
                ;;
            pacman)
                pacman -Qi "$package" &>/dev/null || missing_packages+=("$package")
                ;;
            brew)
                case "$package" in
                git)
                    command -v git &>/dev/null || missing_packages+=("$package")
                    ;;
                gnupg)
                    command -v gpg &>/dev/null || missing_packages+=("$package")
                    ;;
                python)
                    command -v python3 &>/dev/null || missing_packages+=("$package")
                    ;;
                *)
                    run_brew list --formula "$package" &>/dev/null || missing_packages+=("$package")
                    ;;
                esac
                ;;
            esac
        done

        if [[ ${#missing_packages[@]} -gt 0 ]]; then
            whiptail --title "📦 [3/6] 依赖检查" --yesno "以下软件包缺失:\n${missing_packages[*]}\n\n是否自动安装？" 10 60
            if [[ $? -eq 0 ]]; then
                IS_INSTALL_DEPENDENCIES=true
            else
                whiptail --title "⚠️ 注意" --yesno "未安装某些依赖，可能影响运行！\n是否继续？" 10 60 || exit 1
            fi
        fi
    }
    install_packages
       
    # 安装NapCat
    install_napcat() {
        [[ $NAPCAT_INSTALLED == true ]] && return
        whiptail --title "📦 [3/6] 软件包检查" --yesno "检测到未安装NapCat，是否安装？\n如果您想使用远程NapCat，请跳过此步。" 10 60 && {
            IS_INSTALL_NAPCAT=true
        }
    }

    # 仅在 Linux 非 Arch 系统上安装 NapCat，macOS 仅支持远程 NapCat。
    if [[ "$ID" == "macos" ]]; then
        whiptail --title "⚠️ NapCat 安装提示" --msgbox "当前为 macOS，暂不支持自动安装 NapCat。\n如需使用 NapCat，请配置远程实例后再连接。 " 10 60
    elif [[ "$ID" != "arch" ]]; then
        install_napcat
    fi

    # Python版本检查
    check_python() {
        PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        if ! python3 -c "import sys; exit(0) if sys.version_info >= (3,10) else exit(1)"; then
            whiptail --title "⚠️ [4/6] Python 版本过低" --msgbox "检测到 Python 版本为 $PYTHON_VERSION，需要 3.10 或以上！\n请升级 Python 后重新运行本脚本。" 10 60
            exit 1
        fi
    }

    # 如果没安装python则不检查python版本
    if command -v python3 &>/dev/null; then
        check_python
    fi
    

    # 选择分支
    choose_branch() {
    BRANCH=$(whiptail --title "🔀 选择分支" --radiolist "请选择要安装的分支：" 15 60 4 \
        "main" "稳定版本（推荐）" ON \
        "dev" "开发版（不知道什么意思就别选）" OFF \
        "classical" "经典版（0.6.0以前的版本）" OFF \
        "custom" "自定义分支" OFF 3>&1 1>&2 2>&3)
    RETVAL=$?
    if [ $RETVAL -ne 0 ]; then
        whiptail --msgbox "🚫 操作取消！" 10 60
        exit 1
    fi

    if [[ "$BRANCH" == "custom" ]]; then
        BRANCH=$(whiptail --title "🔀 自定义分支" --inputbox "请输入自定义分支名称：" 10 60 "refactor" 3>&1 1>&2 2>&3)
        RETVAL=$?
        if [ $RETVAL -ne 0 ]; then
            whiptail --msgbox "🚫 输入取消！" 10 60
            exit 1
        fi
        if [[ -z "$BRANCH" ]]; then
            whiptail --msgbox "🚫 分支名称不能为空！" 10 60
            exit 1
        fi
    fi
    }
    choose_branch

    # 选择安装路径
    choose_install_dir() {
        INSTALL_DIR=$(whiptail --title "📂 [6/6] 选择安装路径" --inputbox "请输入MaiCore的安装目录：" 10 60 "$DEFAULT_INSTALL_DIR" 3>&1 1>&2 2>&3)
        [[ -z "$INSTALL_DIR" ]] && {
            whiptail --title "⚠️ 取消输入" --yesno "未输入安装路径，是否退出安装？" 10 60 && exit 1
            INSTALL_DIR="$DEFAULT_INSTALL_DIR"
        }
    }
    choose_install_dir

    # 确认安装
    confirm_install() {
        local confirm_msg="请确认以下更改：\n\n"
        confirm_msg+="📂 安装MaiCore、NapCat Adapter到: $INSTALL_DIR\n"
        confirm_msg+="🔀 分支: $BRANCH\n"
        [[ $IS_INSTALL_DEPENDENCIES == true ]] && confirm_msg+="📦 安装依赖：${missing_packages[@]}\n"
        [[ $IS_INSTALL_NAPCAT == true ]] && confirm_msg+="📦 安装额外组件：\n"

        [[ $IS_INSTALL_NAPCAT == true ]] && confirm_msg+="  - NapCat\n"
        confirm_msg+="\n注意：本脚本默认使用ghfast.top为GitHub进行加速，如不想使用请手动修改脚本开头的GITHUB_REPO变量。"

        whiptail --title "🔧 安装确认" --yesno "$confirm_msg" 20 60 || exit 1
    }
    confirm_install

    # 开始安装
    echo -e "${GREEN}安装${missing_packages[@]}...${RESET}"
    
    if [[ $IS_INSTALL_DEPENDENCIES == true ]]; then
        case "$PKG_MANAGER" in
        apt)
            apt update && apt install -y "${missing_packages[@]}"
            ;;
        yum)
            yum install -y "${missing_packages[@]}" --nobest
            ;;
        pacman)
            pacman -S --noconfirm "${missing_packages[@]}"
            ;;
        brew)
            run_brew update && run_brew install "${missing_packages[@]}"
            ;;
        esac
    fi

    if [[ $IS_INSTALL_NAPCAT == true ]]; then
        echo -e "${GREEN}安装 NapCat...${RESET}"
        curl -o napcat.sh https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh && bash napcat.sh --cli y --docker n
    fi

    echo -e "${GREEN}创建安装目录...${RESET}"
    mkdir -p "$INSTALL_DIR"
    cd "$INSTALL_DIR" || exit 1

    echo -e "${GREEN}设置Python虚拟环境...${RESET}"
    python3 -m venv venv
    source venv/bin/activate

    echo -e "${GREEN}克隆MaiCore仓库...${RESET}"
    git clone -b "$BRANCH" "$GITHUB_REPO/MaiM-with-u/MaiBot" MaiBot || {
        echo -e "${RED}克隆MaiCore仓库失败！${RESET}"
        exit 1
    }

    echo -e "${GREEN}克隆 maim_message 包仓库...${RESET}"
    git clone $GITHUB_REPO/MaiM-with-u/maim_message.git || {
        echo -e "${RED}克隆 maim_message 包仓库失败！${RESET}"
        exit 1
    }

    echo -e "${GREEN}克隆 nonebot-plugin-maibot-adapters 仓库...${RESET}"
    git clone $GITHUB_REPO/MaiM-with-u/MaiBot-Napcat-Adapter.git || {
        echo -e "${RED}克隆 MaiBot-Napcat-Adapter.git 仓库失败！${RESET}"
        exit 1
    }


    echo -e "${GREEN}安装Python依赖...${RESET}"
    select_pypi_index_url
    pip install -r MaiBot/requirements.txt
    cd MaiBot
    pip install uv
    uv pip install "${UV_PIP_INDEX_OPTION[@]}" -r requirements.txt
    cd ..

    echo -e "${GREEN}安装maim_message依赖...${RESET}"
    cd maim_message
    uv pip install "${UV_PIP_INDEX_OPTION[@]}" -e .
    cd ..

    echo -e "${GREEN}部署MaiBot Napcat Adapter...${RESET}"
    cd MaiBot-Napcat-Adapter
    uv pip install "${UV_PIP_INDEX_OPTION[@]}" -r requirements.txt
    cd ..

    echo -e "${GREEN}同意协议...${RESET}"

    # 首先计算当前EULA的MD5值
    current_md5=$(compute_md5 "MaiBot/EULA.md")

    # 首先计算当前隐私条款文件的哈希值
    current_md5_privacy=$(compute_md5 "MaiBot/PRIVACY.md")

    echo -n "$current_md5" > MaiBot/eula.confirmed
    echo -n "$current_md5_privacy" > MaiBot/privacy.confirmed

    if [[ "$IS_MACOS" == true ]]; then
        echo -e "${GREEN}创建 launchctl 服务...${RESET}"
        create_launchd_services
        stop_service "${SERVICE_NAME}" >/dev/null 2>&1 || true
        stop_service "${SERVICE_NAME_NBADAPTER}" >/dev/null 2>&1 || true
    else
        echo -e "${GREEN}创建系统服务...${RESET}"
        cat > /etc/systemd/system/${SERVICE_NAME}.service <<EOF
[Unit]
Description=MaiCore
After=network.target ${SERVICE_NAME_NBADAPTER}.service

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}/MaiBot
ExecStart=$INSTALL_DIR/venv/bin/python3 bot.py
Restart=always
RestartSec=10s

[Install]
WantedBy=multi-user.target
EOF

#     cat > /etc/systemd/system/${SERVICE_NAME_WEB}.service <<EOF
# [Unit]
# Description=MaiCore WebUI
# After=network.target ${SERVICE_NAME}.service

# [Service]
# Type=simple
# WorkingDirectory=${INSTALL_DIR}/MaiBot
# ExecStart=$INSTALL_DIR/venv/bin/python3 webui.py
# Restart=always
# RestartSec=10s

# [Install]
# WantedBy=multi-user.target
# EOF

        cat > /etc/systemd/system/${SERVICE_NAME_NBADAPTER}.service <<EOF
[Unit]
Description=MaiBot Napcat Adapter
After=network.target mongod.service ${SERVICE_NAME}.service

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}/MaiBot-Napcat-Adapter
ExecStart=$INSTALL_DIR/venv/bin/python3 main.py
Restart=always
RestartSec=10s

[Install]
WantedBy=multi-user.target
EOF

        systemctl daemon-reload
    fi

    # 保存安装信息
    save_install_info

    if [[ "$IS_MACOS" == true ]]; then
        whiptail --title "🎉 安装完成" --msgbox "MaiCore安装完成！\n已创建 launchctl 服务：${LAUNCHD_LABEL_MAIN}、${LAUNCHD_LABEL_NBADAPTER}\n\n首次加载：launchctl bootstrap ${LAUNCHD_DOMAIN} ${LAUNCHD_PLIST_MAIN}\n重启服务：launchctl kickstart -k ${LAUNCHD_DOMAIN}/${LAUNCHD_LABEL_MAIN}\n查看状态：launchctl print ${LAUNCHD_DOMAIN}/${LAUNCHD_LABEL_MAIN}" 14 100
    else
        whiptail --title "🎉 安装完成" --msgbox "MaiCore安装完成！\n已创建系统服务：${SERVICE_NAME}、${SERVICE_NAME_WEB}、${SERVICE_NAME_NBADAPTER}\n\n使用以下命令管理服务：\n启动服务：systemctl start ${SERVICE_NAME}\n查看状态：systemctl status ${SERVICE_NAME}" 14 60
    fi
}

# ----------- 主执行流程 -----------
# Linux 仍需 root，macOS 使用用户级 launchctl（无需 root）。
if [[ "$IS_MACOS" == true && $(id -u) -eq 0 ]]; then
    echo -e "${RED}macOS 请勿使用 root/sudo 运行此脚本，请直接以当前登录用户执行。${RESET}"
    exit 1
fi

if [[ "$IS_MACOS" != true && $(id -u) -ne 0 ]]; then
    echo -e "${RED}请使用root用户运行此脚本！${RESET}"
    exit 1
fi

# 如果已安装显示菜单，并检查协议是否更新
if check_installed; then
    load_install_info
    check_eula
    show_menu
else
    run_installation
    # 安装完成后询问是否启动
    if whiptail --title "安装完成" --yesno "是否立即启动MaiCore服务？" 10 60; then
        start_service "${SERVICE_NAME}"
        if [[ "$IS_MACOS" == true ]]; then
            whiptail --msgbox "✅ 服务已启动！\n使用 launchctl print ${LAUNCHD_DOMAIN}/${LAUNCHD_LABEL_MAIN} 查看状态" 10 80
        else
            whiptail --msgbox "✅ 服务已启动！\n使用 systemctl status ${SERVICE_NAME} 查看状态" 10 60
        fi
    fi
fi
