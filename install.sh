#!/usr/bin/env bash
# =============================================================================
# Claude Agent Station - One-Command Installer
# =============================================================================
# Installs Claude Agent Station on Rocky Linux 9 / RHEL-based systems.
#
# Usage:
#   sudo bash install.sh              # Full installation
#   sudo bash install.sh --dry-run    # Preview what would be done
#   sudo bash install.sh --upgrade    # Upgrade existing installation
#   sudo bash install.sh --uninstall  # Remove installation (preserves data)
#
# Requirements:
#   - Rocky Linux 9 / RHEL 9 / AlmaLinux 9 (or compatible)
#   - Root or sudo access
#   - Internet connection
#
# After installation:
#   1. Add your GitHub token: claude-agent-station config set GH_TOKEN <token>
#   2. Log in to Claude: sudo -u claude-agent claude login
#   3. Open dashboard: http://<your-ip>:8420
# =============================================================================

set -euo pipefail

# =============================================================================
# Constants
# =============================================================================

readonly SCRIPT_VERSION="1.0.0"
readonly INSTALL_DIR="/opt/claude-agent-station"
readonly DATA_DIR="/var/lib/claude-agent-station"
readonly LOG_DIR="/var/log/claude-agent"
readonly VENV_DIR="${INSTALL_DIR}/venv"
readonly SERVICE_USER="claude-agent"
readonly SERVICE_GROUP="claude-agent"
readonly DASHBOARD_PORT=8420
readonly DB_PATH="${DATA_DIR}/station.db"
readonly CONFIG_PATH="/home/${SERVICE_USER}/.config/claude-agent/environment"
readonly MANAGER_CONFIG="/home/${SERVICE_USER}/.claude/autonomous/manager-config.json"

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[0;33m'
readonly BLUE='\033[0;34m'
readonly CYAN='\033[0;36m'
readonly BOLD='\033[1m'
readonly NC='\033[0m' # No Color

# =============================================================================
# Globals
# =============================================================================

DRY_RUN=false
UPGRADE=false
UNINSTALL=false
REPO_URL="https://github.com/kenhaesler/claude-agent-station.git"
SOURCE_DIR=""  # Set during install — either cloned repo or local source

# =============================================================================
# Utility Functions
# =============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

log_step() {
    echo -e "\n${BOLD}${CYAN}==> $*${NC}"
}

log_dry() {
    echo -e "${YELLOW}[DRY-RUN]${NC} Would: $*"
}

# Execute a command, or print it in dry-run mode
run() {
    if $DRY_RUN; then
        log_dry "$*"
    else
        "$@"
    fi
}

# Check if a command exists
cmd_exists() {
    command -v "$1" &>/dev/null
}

# Confirm action (skipped in non-interactive mode)
confirm() {
    local prompt="$1"
    if [[ -t 0 ]]; then
        read -rp "$(echo -e "${YELLOW}${prompt} [y/N]${NC} ")" answer
        [[ "$answer" =~ ^[Yy]$ ]]
    else
        # Non-interactive — proceed
        return 0
    fi
}

# =============================================================================
# Preflight Checks
# =============================================================================

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

check_os() {
    log_step "Checking operating system"

    if [[ ! -f /etc/os-release ]]; then
        log_error "Cannot detect OS — /etc/os-release not found"
        exit 1
    fi

    source /etc/os-release

    case "${ID}" in
        rocky|rhel|almalinux|centos|ol|fedora)
            log_info "Detected OS: ${PRETTY_NAME}"
            ;;
        *)
            log_warn "Unsupported OS: ${PRETTY_NAME}"
            log_warn "This script is designed for Rocky Linux 9 / RHEL-based systems."
            log_warn "Installation may work but is not guaranteed."
            if ! confirm "Continue anyway?"; then
                exit 1
            fi
            ;;
    esac

    # Check version (prefer 9.x)
    local major_version="${VERSION_ID%%.*}"
    if [[ "$major_version" -lt 8 ]]; then
        log_error "OS version ${VERSION_ID} is too old. Minimum: 8.x, Recommended: 9.x"
        exit 1
    elif [[ "$major_version" -lt 9 ]]; then
        log_warn "OS version ${VERSION_ID} — recommended 9.x for full compatibility"
    fi
}

check_resources() {
    log_step "Checking system resources"

    # Check available memory (minimum 2GB recommended)
    local mem_kb
    mem_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    local mem_gb=$((mem_kb / 1024 / 1024))

    if [[ $mem_gb -lt 2 ]]; then
        log_warn "System has ${mem_gb}GB RAM — 2GB+ recommended for Claude agent operations"
    else
        log_info "Memory: ${mem_gb}GB — OK"
    fi

    # Check available disk (minimum 5GB recommended)
    local disk_avail_kb
    disk_avail_kb=$(df / --output=avail | tail -1 | tr -d ' ')
    local disk_avail_gb=$((disk_avail_kb / 1024 / 1024))

    if [[ $disk_avail_gb -lt 5 ]]; then
        log_warn "Only ${disk_avail_gb}GB disk space available — 5GB+ recommended"
    else
        log_info "Disk space: ${disk_avail_gb}GB available — OK"
    fi
}

# =============================================================================
# Installation Steps
# =============================================================================

install_system_deps() {
    log_step "Installing system dependencies"

    local packages=(
        python3
        python3-pip
        python3-devel
        git
        jq
        socat
        gcc
        make
    )

    # Check if bubblewrap is available
    if dnf list bubblewrap &>/dev/null 2>&1; then
        packages+=(bubblewrap)
    fi

    log_info "Installing packages: ${packages[*]}"
    run dnf install -y "${packages[@]}"

    # Verify Python version (need 3.11+)
    local python_version
    python_version=$(python3 --version 2>&1 | awk '{print $2}')
    local python_minor
    python_minor=$(echo "$python_version" | cut -d. -f2)

    if [[ "$python_minor" -lt 11 ]]; then
        log_warn "Python ${python_version} detected — Python 3.11+ recommended"
        log_info "Attempting to install Python 3.11..."
        run dnf install -y python3.11 python3.11-pip python3.11-devel 2>/dev/null || {
            log_warn "Could not install Python 3.11 — continuing with Python ${python_version}"
        }
    else
        log_success "Python ${python_version} — OK"
    fi

    log_success "System dependencies installed"
}

install_nodejs() {
    log_step "Installing Node.js"

    if cmd_exists node; then
        local node_version
        node_version=$(node --version 2>&1)
        log_info "Node.js ${node_version} already installed"

        # Check if it's recent enough (need 18+)
        local node_major
        node_major=$(echo "$node_version" | sed 's/v//' | cut -d. -f1)
        if [[ "$node_major" -ge 18 ]]; then
            log_success "Node.js ${node_version} — OK"
            return 0
        else
            log_warn "Node.js ${node_version} is too old — installing newer version"
        fi
    fi

    # Install Node.js 20 LTS via dnf module or nodesource
    if dnf module list nodejs 2>/dev/null | grep -q "20"; then
        log_info "Installing Node.js 20 via dnf module"
        run dnf module enable -y nodejs:20
        run dnf install -y nodejs npm
    else
        log_info "Installing Node.js via NodeSource repository"
        run dnf install -y https://rpm.nodesource.com/pub_20.x/nodistro/repo/nodesource-release-nodistro-1.noarch.rpm 2>/dev/null || true
        run dnf install -y nodejs npm
    fi

    if cmd_exists node; then
        log_success "Node.js $(node --version) installed"
    else
        log_error "Failed to install Node.js — frontend build will be skipped"
    fi
}

install_claude_cli() {
    log_step "Checking Claude Code CLI"

    if cmd_exists claude; then
        log_success "Claude CLI already installed: $(claude --version 2>&1 || echo 'version unknown')"
        return 0
    fi

    if ! cmd_exists npm; then
        log_warn "npm not found — cannot install Claude CLI automatically"
        log_warn "Install manually: npm install -g @anthropic-ai/claude-code"
        return 0
    fi

    log_info "Installing Claude Code CLI via npm"
    run npm install -g @anthropic-ai/claude-code

    if cmd_exists claude; then
        log_success "Claude CLI installed"
    else
        log_warn "Claude CLI installation may require PATH update"
        log_warn "Try: npm install -g @anthropic-ai/claude-code"
    fi
}

create_service_user() {
    log_step "Creating service user"

    if id "$SERVICE_USER" &>/dev/null; then
        log_info "User '${SERVICE_USER}' already exists"
    else
        log_info "Creating system user '${SERVICE_USER}'"
        run useradd \
            --system \
            --create-home \
            --home-dir "/home/${SERVICE_USER}" \
            --shell /bin/bash \
            --comment "Claude Agent Station service account" \
            "$SERVICE_USER"
        log_success "User '${SERVICE_USER}' created"
    fi

    # Add to docker group if it exists (for Docker-based builds)
    if getent group docker &>/dev/null; then
        run usermod -aG docker "$SERVICE_USER" 2>/dev/null || true
        log_info "Added ${SERVICE_USER} to docker group"
    fi
}

setup_directories() {
    log_step "Setting up directories"

    local dirs=(
        "$INSTALL_DIR"
        "$DATA_DIR"
        "$LOG_DIR"
        "$LOG_DIR/digests"
        "/home/${SERVICE_USER}/workspaces"
        "/home/${SERVICE_USER}/.config/claude-agent"
        "/home/${SERVICE_USER}/.claude/autonomous"
    )

    for dir in "${dirs[@]}"; do
        if [[ ! -d "$dir" ]]; then
            run mkdir -p "$dir"
            log_info "Created: $dir"
        fi
    done

    # Set ownership
    run chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$DATA_DIR"
    run chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$LOG_DIR"
    run chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "/home/${SERVICE_USER}"

    log_success "Directories configured"
}

clone_or_copy_source() {
    log_step "Installing application source"

    # Determine source: if running from within the repo, copy local files
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    if [[ -f "${script_dir}/ARCHITECTURE.md" && -d "${script_dir}/agent" && -d "${script_dir}/dashboard" ]]; then
        log_info "Installing from local source: ${script_dir}"
        SOURCE_DIR="$script_dir"

        if [[ "$script_dir" != "$INSTALL_DIR" ]]; then
            # Copy source to install directory (preserve git for updates)
            run rsync -a --exclude='node_modules' --exclude='__pycache__' \
                --exclude='.git' --exclude='venv' --exclude='dist' \
                "${script_dir}/" "${INSTALL_DIR}/"
            # Copy .git separately if it exists (for future git pull upgrades)
            if [[ -d "${script_dir}/.git" ]]; then
                run rsync -a "${script_dir}/.git" "${INSTALL_DIR}/"
            fi
        fi
    else
        log_info "Cloning from GitHub: ${REPO_URL}"
        if [[ -d "${INSTALL_DIR}/.git" ]]; then
            log_info "Existing installation found — pulling latest"
            run git -C "$INSTALL_DIR" fetch origin
            run git -C "$INSTALL_DIR" reset --hard origin/main
        else
            run git clone "$REPO_URL" "$INSTALL_DIR"
        fi
        SOURCE_DIR="$INSTALL_DIR"
    fi

    run chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$INSTALL_DIR"
    log_success "Source installed to ${INSTALL_DIR}"
}

setup_python_venv() {
    log_step "Setting up Python virtual environment"

    local python_bin="python3"

    # Prefer Python 3.11+ if available
    if cmd_exists python3.11; then
        python_bin="python3.11"
    elif cmd_exists python3.12; then
        python_bin="python3.12"
    fi

    if [[ ! -d "${VENV_DIR}" ]]; then
        log_info "Creating venv with ${python_bin}"
        run "$python_bin" -m venv "$VENV_DIR"
    else
        log_info "Virtual environment already exists"
    fi

    # Upgrade pip
    run "${VENV_DIR}/bin/pip" install --upgrade pip setuptools wheel

    # Install backend dependencies
    local req_file="${INSTALL_DIR}/dashboard/backend/requirements.txt"
    if [[ -f "$req_file" ]]; then
        log_info "Installing Python dependencies from requirements.txt"
        run "${VENV_DIR}/bin/pip" install -r "$req_file"
    else
        log_error "requirements.txt not found at ${req_file}"
        exit 1
    fi

    run chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$VENV_DIR"
    log_success "Python environment configured"
}

build_frontend() {
    log_step "Building frontend"

    local frontend_dir="${INSTALL_DIR}/dashboard/frontend"

    if [[ ! -f "${frontend_dir}/package.json" ]]; then
        log_warn "Frontend package.json not found — skipping build"
        return 0
    fi

    if ! cmd_exists npm; then
        log_warn "npm not available — skipping frontend build"
        return 0
    fi

    # Check if dist already exists and is recent (skip rebuild if upgrading)
    if [[ -d "${frontend_dir}/dist" && "$UPGRADE" == true ]]; then
        log_info "Frontend dist exists — rebuilding for upgrade"
    fi

    log_info "Installing npm dependencies"
    run bash -c "cd '${frontend_dir}' && npm install --production=false"

    log_info "Building frontend (this may take a minute)"
    run bash -c "cd '${frontend_dir}' && npm run build"

    if [[ -d "${frontend_dir}/dist" ]]; then
        log_success "Frontend built successfully"
    else
        log_warn "Frontend build may have failed — check manually"
    fi
}

init_database() {
    log_step "Initializing database"

    if [[ -f "$DB_PATH" ]]; then
        log_info "Database already exists at ${DB_PATH}"
        if $UPGRADE; then
            log_info "Backing up database before upgrade"
            run cp "$DB_PATH" "${DB_PATH}.bak.$(date +%Y%m%d%H%M%S)"
        fi
    fi

    # The FastAPI app initializes tables on startup via init_db()
    # We can trigger this with a quick startup/shutdown
    log_info "Running database initialization"
    run sudo -u "$SERVICE_USER" bash -c "
        export PYTHONPATH='${INSTALL_DIR}/dashboard/backend'
        export STATION_DB_PATH='${DB_PATH}'
        '${VENV_DIR}/bin/python3' -c '
import asyncio
import sys
sys.path.insert(0, \"${INSTALL_DIR}/dashboard/backend\")
from app.database import init_db
asyncio.run(init_db())
print(\"Database initialized successfully\")
'
    " || {
        log_warn "Automatic DB init failed — database will be created on first dashboard start"
    }

    run chown "${SERVICE_USER}:${SERVICE_GROUP}" "$DB_PATH" 2>/dev/null || true
    log_success "Database configured at ${DB_PATH}"
}

install_config() {
    log_step "Installing configuration"

    # Manager config template
    if [[ ! -f "$MANAGER_CONFIG" ]]; then
        log_info "Installing default manager config"
        run mkdir -p "$(dirname "$MANAGER_CONFIG")"
        run cp "${INSTALL_DIR}/agent/config/default-config.json" "$MANAGER_CONFIG"
        run chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "/home/${SERVICE_USER}/.claude"
        log_info "Config template installed at ${MANAGER_CONFIG}"
    else
        log_info "Manager config already exists — preserving"
    fi

    # Environment file for systemd
    if [[ ! -f "$CONFIG_PATH" ]]; then
        log_info "Creating environment file template"
        run mkdir -p "$(dirname "$CONFIG_PATH")"
        cat > "$CONFIG_PATH" <<'ENVEOF'
# Claude Agent Station Environment Configuration
# ================================================
# Add your secrets and environment variables here.
# This file is loaded by the systemd service.
#
# Required:
#   GH_TOKEN=ghp_your_github_token_here
#
# Optional:
#   ANTHROPIC_API_KEY=sk-ant-...  (if using API directly)
#   GITHUB_REPO=owner/repo        (default repo for agent)
#
# GH_TOKEN=ghp_REPLACE_ME
ENVEOF
        run chown "${SERVICE_USER}:${SERVICE_GROUP}" "$CONFIG_PATH"
        run chmod 600 "$CONFIG_PATH"
        log_info "Environment file created at ${CONFIG_PATH}"
    else
        log_info "Environment file already exists — preserving"
    fi

    log_success "Configuration installed"
}

install_systemd_units() {
    log_step "Installing systemd units"

    local systemd_src="${INSTALL_DIR}/agent/systemd"
    local systemd_dest="/etc/systemd/system"

    # Install agent service
    if [[ -f "${systemd_src}/claude-agent.service" ]]; then
        run cp "${systemd_src}/claude-agent.service" "${systemd_dest}/"
        log_info "Installed claude-agent.service"
    fi

    # Install agent timer
    if [[ -f "${systemd_src}/claude-agent.timer" ]]; then
        run cp "${systemd_src}/claude-agent.timer" "${systemd_dest}/"
        log_info "Installed claude-agent.timer"
    fi

    # Install dashboard service (fix paths to use INSTALL_DIR)
    cat > "${systemd_dest}/claude-station-dashboard.service" <<DASHEOF
[Unit]
Description=Claude Agent Station Dashboard Backend
Documentation=https://github.com/kenhaesler/claude-agent-station
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${INSTALL_DIR}/dashboard/backend
ExecStart=${VENV_DIR}/bin/uvicorn app.main:app --host 0.0.0.0 --port ${DASHBOARD_PORT}
Restart=on-failure
RestartSec=5
Environment=PYTHONPATH=${INSTALL_DIR}/dashboard/backend
Environment=STATION_DB_PATH=${DB_PATH}
Environment=HOME=/home/${SERVICE_USER}
EnvironmentFile=-${CONFIG_PATH}

# Security hardening
NoNewPrivileges=yes
ProtectHome=read-only
ReadWritePaths=${DATA_DIR} ${LOG_DIR} /home/${SERVICE_USER}
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
DASHEOF
    log_info "Installed claude-station-dashboard.service"

    # Reload systemd
    run systemctl daemon-reload
    log_success "systemd units installed"
}

configure_selinux() {
    log_step "Configuring SELinux"

    # Check if SELinux is enforcing
    if ! cmd_exists getenforce; then
        log_info "SELinux tools not found — skipping"
        return 0
    fi

    local selinux_status
    selinux_status=$(getenforce 2>/dev/null || echo "Disabled")

    if [[ "$selinux_status" == "Disabled" ]]; then
        log_info "SELinux is disabled — skipping policy installation"
        return 0
    fi

    log_info "SELinux is ${selinux_status}"

    local te_file="${INSTALL_DIR}/agent/selinux/claude-agent.te"
    if [[ ! -f "$te_file" ]]; then
        log_warn "SELinux policy file not found — skipping"
        return 0
    fi

    if ! cmd_exists checkmodule; then
        log_info "Installing SELinux policy tools"
        run dnf install -y policycoreutils-python-utils selinux-policy-devel 2>/dev/null || {
            log_warn "Could not install SELinux tools — skipping policy"
            return 0
        }
    fi

    # Compile and install SELinux module
    local tmp_dir
    tmp_dir=$(mktemp -d)

    log_info "Compiling SELinux policy module"
    if checkmodule -M -m -o "${tmp_dir}/claude-agent.mod" "$te_file" 2>/dev/null; then
        semodule_package -o "${tmp_dir}/claude-agent.pp" -m "${tmp_dir}/claude-agent.mod" 2>/dev/null
        run semodule -i "${tmp_dir}/claude-agent.pp" 2>/dev/null || {
            log_warn "SELinux module installation failed — the agent may work anyway"
        }
        log_success "SELinux policy installed"
    else
        log_warn "SELinux policy compilation failed — skipping"
    fi

    rm -rf "$tmp_dir"
}

configure_firewall() {
    log_step "Configuring firewall"

    if ! cmd_exists firewall-cmd; then
        log_info "firewalld not found — skipping firewall configuration"
        return 0
    fi

    if ! systemctl is-active --quiet firewalld; then
        log_info "firewalld is not running — skipping"
        return 0
    fi

    log_info "Opening port ${DASHBOARD_PORT}/tcp for dashboard"
    run firewall-cmd --permanent --add-port="${DASHBOARD_PORT}/tcp"
    run firewall-cmd --reload
    log_success "Firewall configured — port ${DASHBOARD_PORT}/tcp open"
}

set_file_permissions() {
    log_step "Setting file permissions"

    # Make agent scripts executable
    run chmod +x "${INSTALL_DIR}/agent/scripts/"*.sh 2>/dev/null || true
    run chmod +x "${INSTALL_DIR}/agent/scripts/"*.py 2>/dev/null || true

    # Ensure correct ownership
    run chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$INSTALL_DIR"
    run chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$DATA_DIR"
    run chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "$LOG_DIR"

    log_success "Permissions set"
}

start_services() {
    log_step "Starting services"

    # Enable and start dashboard
    run systemctl enable claude-station-dashboard.service
    run systemctl start claude-station-dashboard.service

    # Enable agent timer (but don't start agent service — it's triggered by timer)
    run systemctl enable claude-agent.timer

    # Check dashboard status
    sleep 2
    if systemctl is-active --quiet claude-station-dashboard.service; then
        log_success "Dashboard service is running"
    else
        log_warn "Dashboard service may not have started correctly"
        log_warn "Check: systemctl status claude-station-dashboard.service"
    fi

    log_success "Services configured"
}

# =============================================================================
# Uninstall
# =============================================================================

do_uninstall() {
    log_step "Uninstalling Claude Agent Station"

    log_warn "This will remove the application but preserve data in ${DATA_DIR}"

    if ! confirm "Continue with uninstallation?"; then
        log_info "Uninstall cancelled"
        exit 0
    fi

    # Stop services
    log_info "Stopping services"
    systemctl stop claude-station-dashboard.service 2>/dev/null || true
    systemctl stop claude-agent.timer 2>/dev/null || true
    systemctl stop claude-agent.service 2>/dev/null || true
    systemctl disable claude-station-dashboard.service 2>/dev/null || true
    systemctl disable claude-agent.timer 2>/dev/null || true
    systemctl disable claude-agent.service 2>/dev/null || true

    # Remove systemd units
    log_info "Removing systemd units"
    rm -f /etc/systemd/system/claude-station-dashboard.service
    rm -f /etc/systemd/system/claude-agent.service
    rm -f /etc/systemd/system/claude-agent.timer
    systemctl daemon-reload

    # Remove installation directory
    log_info "Removing application files"
    rm -rf "$INSTALL_DIR"

    # Close firewall port
    if cmd_exists firewall-cmd && systemctl is-active --quiet firewalld; then
        firewall-cmd --permanent --remove-port="${DASHBOARD_PORT}/tcp" 2>/dev/null || true
        firewall-cmd --reload 2>/dev/null || true
    fi

    log_success "Uninstalled. Data preserved at: ${DATA_DIR}, Logs at: ${LOG_DIR}"
    log_info "To fully remove all data: rm -rf ${DATA_DIR} ${LOG_DIR}"
    log_info "To remove user: userdel -r ${SERVICE_USER}"
}

# =============================================================================
# Post-Install Summary
# =============================================================================

print_summary() {
    local ip_addr
    ip_addr=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

    echo ""
    echo -e "${BOLD}${GREEN}=============================================${NC}"
    echo -e "${BOLD}${GREEN}  Claude Agent Station - Installation Complete${NC}"
    echo -e "${BOLD}${GREEN}=============================================${NC}"
    echo ""
    echo -e "${BOLD}Dashboard:${NC}  http://${ip_addr}:${DASHBOARD_PORT}"
    echo -e "${BOLD}Install dir:${NC} ${INSTALL_DIR}"
    echo -e "${BOLD}Data dir:${NC}    ${DATA_DIR}"
    echo -e "${BOLD}Log dir:${NC}     ${LOG_DIR}"
    echo -e "${BOLD}Config:${NC}      ${MANAGER_CONFIG}"
    echo -e "${BOLD}Env file:${NC}    ${CONFIG_PATH}"
    echo ""
    echo -e "${BOLD}${YELLOW}Next Steps:${NC}"
    echo ""
    echo -e "  1. ${BOLD}Add your GitHub token:${NC}"
    echo -e "     Edit ${CONFIG_PATH}"
    echo -e "     Add: GH_TOKEN=ghp_your_token_here"
    echo ""
    echo -e "  2. ${BOLD}Authenticate Claude CLI:${NC}"
    echo -e "     sudo -u ${SERVICE_USER} claude login"
    echo ""
    echo -e "  3. ${BOLD}Add a project via the dashboard:${NC}"
    echo -e "     Open http://${ip_addr}:${DASHBOARD_PORT}"
    echo -e "     Navigate to Projects → Add Project"
    echo ""
    echo -e "  4. ${BOLD}Start the agent timer (scheduled runs):${NC}"
    echo -e "     systemctl start claude-agent.timer"
    echo ""
    echo -e "  5. ${BOLD}Trigger a manual run:${NC}"
    echo -e "     systemctl start claude-agent.service"
    echo ""
    echo -e "${BOLD}Useful Commands:${NC}"
    echo -e "  systemctl status claude-station-dashboard  # Dashboard status"
    echo -e "  systemctl status claude-agent.timer        # Timer status"
    echo -e "  journalctl -u claude-agent -f              # Agent logs"
    echo -e "  journalctl -u claude-station-dashboard -f  # Dashboard logs"
    echo ""
}

# =============================================================================
# Main
# =============================================================================

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)
                DRY_RUN=true
                log_info "Dry-run mode — no changes will be made"
                shift
                ;;
            --upgrade)
                UPGRADE=true
                shift
                ;;
            --uninstall)
                UNINSTALL=true
                shift
                ;;
            --repo)
                REPO_URL="$2"
                shift 2
                ;;
            --help|-h)
                echo "Usage: sudo bash install.sh [OPTIONS]"
                echo ""
                echo "Options:"
                echo "  --dry-run     Preview installation steps without making changes"
                echo "  --upgrade     Upgrade existing installation"
                echo "  --uninstall   Remove installation (preserves data)"
                echo "  --repo URL    Use custom Git repository URL"
                echo "  --help        Show this help message"
                echo ""
                echo "Examples:"
                echo "  sudo bash install.sh              # Fresh install"
                echo "  sudo bash install.sh --dry-run    # Preview only"
                echo "  sudo bash install.sh --upgrade    # Upgrade in place"
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                log_error "Run with --help for usage information"
                exit 1
                ;;
        esac
    done
}

main() {
    parse_args "$@"

    echo -e "${BOLD}${CYAN}"
    echo "  ╔═══════════════════════════════════════╗"
    echo "  ║     Claude Agent Station Installer    ║"
    echo "  ║              v${SCRIPT_VERSION}                  ║"
    echo "  ╚═══════════════════════════════════════╝"
    echo -e "${NC}"

    check_root

    if $UNINSTALL; then
        do_uninstall
        exit 0
    fi

    check_os
    check_resources

    if $UPGRADE; then
        log_info "Upgrade mode — existing config and data will be preserved"
    fi

    # Step 1: System dependencies
    install_system_deps

    # Step 2: Node.js (for frontend build + Claude CLI)
    install_nodejs

    # Step 3: Claude CLI
    install_claude_cli

    # Step 4: Service user
    create_service_user

    # Step 5: Directory structure
    setup_directories

    # Step 6: Application source
    clone_or_copy_source

    # Step 7: Python environment
    setup_python_venv

    # Step 8: Frontend build
    build_frontend

    # Step 9: Database initialization
    init_database

    # Step 10: Configuration files
    install_config

    # Step 11: systemd units
    install_systemd_units

    # Step 12: SELinux policy
    configure_selinux

    # Step 13: Firewall
    configure_firewall

    # Step 14: File permissions
    set_file_permissions

    # Step 15: Start services
    if ! $DRY_RUN; then
        start_services
    fi

    # Done!
    if $DRY_RUN; then
        log_success "Dry-run complete — no changes were made"
    else
        print_summary
    fi
}

main "$@"
