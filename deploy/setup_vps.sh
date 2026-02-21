#!/bin/bash
# VPS 初始化脚本 - 首次部署时在 VPS 上运行（支持 root 自动初始化）
# Usage: bash setup_vps.sh

set -e

echo "=========================================="
echo "  Polymarket Bot - VPS Setup Script"
echo "  Enhanced with Auto User Management"
echo "=========================================="

# ============================================
# 1. Root 用户自识别与初始化逻辑
# ============================================
DEPLOY_USER="jmm_deployer"

if [ "$EUID" -eq 0 ]; then
    echo "[ROOT] 检测到 root 用户，开始自动初始化..."
    
    # 1.1 创建部署用户（如果不存在）
    if ! id "$DEPLOY_USER" &>/dev/null; then
        echo "[ROOT] 创建系统用户: $DEPLOY_USER"
        useradd -m -s /bin/bash "$DEPLOY_USER"
        echo "[ROOT] ✓ 用户 $DEPLOY_USER 已创建"
    else
        echo "[ROOT] ✓ 用户 $DEPLOY_USER 已存在"
    fi
    
    # 1.2 配置 NOPASSWD sudo 权限（幂等操作）
    SUDOERS_FILE="/etc/sudoers.d/$DEPLOY_USER"
    if [ ! -f "$SUDOERS_FILE" ]; then
        echo "[ROOT] 配置 sudo NOPASSWD 权限..."
        echo "$DEPLOY_USER ALL=(ALL) NOPASSWD: /bin/systemctl, /usr/bin/journalctl" > "$SUDOERS_FILE"
        chmod 440 "$SUDOERS_FILE"
        echo "[ROOT] ✓ sudo 权限已配置"
    else
        echo "[ROOT] ✓ sudo 权限已存在"
    fi
    
    # 1.3 同步 SSH 密钥（从 root 到部署用户）
    if [ -f "/root/.ssh/authorized_keys" ]; then
        echo "[ROOT] 同步 SSH 密钥到 $DEPLOY_USER..."
        DEPLOY_HOME=$(eval echo ~$DEPLOY_USER)
        mkdir -p "$DEPLOY_HOME/.ssh"
        cp /root/.ssh/authorized_keys "$DEPLOY_HOME/.ssh/authorized_keys"
        chown -R "$DEPLOY_USER:$DEPLOY_USER" "$DEPLOY_HOME/.ssh"
        chmod 700 "$DEPLOY_HOME/.ssh"
        chmod 600 "$DEPLOY_HOME/.ssh/authorized_keys"
        echo "[ROOT] ✓ SSH 密钥已同步"
    else
        echo "[ROOT] ⚠ /root/.ssh/authorized_keys 不存在，跳过密钥同步"
    fi
    
    # 1.4 切换到部署用户并重新执行脚本
    echo "[ROOT] 切换到用户 $DEPLOY_USER 并继续执行..."
    echo "=========================================="
    
    # 检查脚本是否通过管道执行（curl | bash）
    if [ ! -f "$0" ] || [ "$0" = "bash" ] || [ "$0" = "/bin/bash" ] || [ "$0" = "/usr/bin/bash" ]; then
        # 通过管道执行，需要下载脚本到临时文件
        SCRIPT_URL="https://raw.githubusercontent.com/airhao3/jmm_trade/main/deploy/setup_vps.sh"
        TEMP_SCRIPT="/tmp/setup_vps_$$.sh"
        echo "[ROOT] 下载脚本到临时文件..."
        curl -sSL "$SCRIPT_URL" -o "$TEMP_SCRIPT"
        chmod +x "$TEMP_SCRIPT"
        chown "$DEPLOY_USER:$DEPLOY_USER" "$TEMP_SCRIPT"
        exec su - "$DEPLOY_USER" -c "bash $TEMP_SCRIPT && rm -f $TEMP_SCRIPT"
    else
        # 直接执行脚本文件
        exec su - "$DEPLOY_USER" -c "bash $0"
    fi
    exit 0
fi

# 从这里开始，脚本以普通用户身份运行
echo "[INFO] 当前用户: $(whoami)"

# ============================================
# 2. 颜色输出函数
# ============================================
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

# ============================================
# 3. 系统级性能优化 (Low-Latency Tuning)
# ============================================
info "应用系统级性能优化..."

# 3.1 提升文件描述符限制（幂等操作）
LIMITS_FILE="/etc/security/limits.conf"
CURRENT_USER=$(whoami)

if ! grep -q "$CURRENT_USER.*nofile" "$LIMITS_FILE" 2>/dev/null; then
    info "配置文件描述符限制 (nofile=65535)..."
    echo "$CURRENT_USER soft nofile 65535" | sudo tee -a "$LIMITS_FILE" > /dev/null
    echo "$CURRENT_USER hard nofile 65535" | sudo tee -a "$LIMITS_FILE" > /dev/null
    success "文件描述符限制已提升"
else
    success "文件描述符限制已配置"
fi

# 3.2 网络优化 - TCP 快速回收（可选，需要 root 权限）
info "应用网络优化（TCP 快速回收）..."
if [ -w /etc/sysctl.conf ]; then
    # 检查是否已配置（幂等）
    if ! grep -q "net.ipv4.tcp_tw_reuse" /etc/sysctl.conf 2>/dev/null; then
        echo "# Polymarket Bot - Network Optimization" | sudo tee -a /etc/sysctl.conf > /dev/null
        echo "net.ipv4.tcp_tw_reuse = 1" | sudo tee -a /etc/sysctl.conf > /dev/null
        echo "net.ipv4.tcp_fin_timeout = 30" | sudo tee -a /etc/sysctl.conf > /dev/null
        sudo sysctl -p > /dev/null 2>&1 || warn "sysctl 应用失败（需要重启生效）"
        success "网络优化已配置"
    else
        success "网络优化已存在"
    fi
else
    warn "无权限修改 sysctl.conf，跳过网络优化（非必需）"
fi

# ============================================
# 4. 检查系统依赖
# ============================================
info "检查系统环境..."
if ! command -v python3.11 &> /dev/null; then
    warn "Python 3.11 未安装，正在安装..."
    sudo apt update
    sudo apt install -y software-properties-common
    sudo add-apt-repository ppa:deadsnakes/ppa -y
    sudo apt update
    sudo apt install -y python3.11 python3.11-venv python3.11-dev
fi

if ! command -v git &> /dev/null; then
    warn "Git 未安装，正在安装..."
    sudo apt install -y git
fi

success "系统环境检查完成"

# ============================================
# 5. 克隆或更新代码
# ============================================
if [ -d "$HOME/jmm_trade" ]; then
    warn "项目目录已存在，跳过克隆"
    cd "$HOME/jmm_trade"
    git pull origin main || warn "无法拉取最新代码，请手动检查"
else
    info "克隆代码仓库..."
    cd "$HOME"
    git clone https://github.com/airhao3/jmm_trade.git
    cd jmm_trade
fi

# ============================================
# 6. 创建虚拟环境
# ============================================
if [ ! -d ".venv" ]; then
    info "创建 Python 虚拟环境..."
    python3.11 -m venv .venv
fi

info "激活虚拟环境并安装依赖..."
source .venv/bin/activate
pip install --upgrade pip > /dev/null
pip install -r requirements.txt

# ============================================
# 7. 配置环境变量（环境预热）
# ============================================
if [ ! -f ".env" ]; then
    info "创建 .env 配置文件..."
    cp .env.example .env
    warn "请编辑 .env 文件填入你的配置: nano .env"
else
    success ".env 文件已存在"
fi

# 环境变量检查
info "检查必要的环境变量..."
source .env

MISSING_VARS=()
[ -z "$POLYMARKET_API_KEY" ] && MISSING_VARS+=("POLYMARKET_API_KEY")
[ -z "POLYMARKET_SECRET" ] && MISSING_VARS+=("POLYMARKET_SECRET")
[ -z "POLYMARKET_PASSPHRASE" ] && MISSING_VARS+=("POLYMARKET_PASSPHRASE")

if [ ${#MISSING_VARS[@]} -gt 0 ]; then
    warn "以下环境变量未设置: ${MISSING_VARS[*]}"
    warn "请编辑 .env 文件: nano .env"
    warn "按 Ctrl+X 保存后重新运行此脚本"
else
    success "所有必要环境变量已配置"
fi

# ============================================
# 8. 创建必要目录
# ============================================
info "创建数据和日志目录..."
mkdir -p data data/exports logs

# ============================================
# 9. 验证配置
# ============================================
info "验证配置..."
if python main.py check-config; then
    success "配置验证通过"
else
    error "配置验证失败，请检查 config/config.yaml 和 .env"
    exit 1
fi

# ============================================
# 10. 创建 systemd 服务
# ============================================
info "创建 systemd 服务..."
SERVICE_FILE="/etc/systemd/system/polymarket-bot.service"

# 获取当前用户和工作目录
CURRENT_USER=$(whoami)
WORK_DIR=$(pwd)
VENV_PYTHON="$WORK_DIR/.venv/bin/python"

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Polymarket Copy Trading Bot
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$WORK_DIR
Environment="PATH=$WORK_DIR/.venv/bin"
Environment="FORCE_READ_ONLY=true"
ExecStart=$VENV_PYTHON main.py run
Restart=always
RestartSec=10

# 日志
StandardOutput=append:$WORK_DIR/logs/bot.log
StandardError=append:$WORK_DIR/logs/bot.error.log

[Install]
WantedBy=multi-user.target
EOF

success "systemd 服务文件已创建: $SERVICE_FILE"

# ============================================
# 11. 一键启动流程（自动化）
# ============================================
info "重载 systemd 配置..."
sudo systemctl daemon-reload

info "启动并启用服务（一键启动）..."
sudo systemctl enable polymarket-bot
sudo systemctl restart polymarket-bot

sleep 3

if sudo systemctl is-active --quiet polymarket-bot; then
    success "服务已启动并设置为开机自启"
    
    # 显示服务状态
    echo ""
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}  服务状态${NC}"
    echo -e "${BLUE}========================================${NC}"
    sudo systemctl status polymarket-bot --no-pager -l | head -15
else
    error "服务启动失败，查看错误日志："
    sudo journalctl -u polymarket-bot -n 30 --no-pager
    exit 1
fi

# ============================================
# 12. 完成提示和日志引导
# ============================================
echo ""
echo -e "${GREEN}==========================================${NC}"
echo -e "${GREEN}  ✓ VPS 设置完成！机器人已启动${NC}"
echo -e "${GREEN}==========================================${NC}"
echo ""
echo -e "${BLUE}📊 实时监控命令：${NC}"
echo -e "  ${GREEN}查看实时日志:${NC} journalctl -u polymarket-bot -f"
echo -e "  ${GREEN}查看文件日志:${NC} tail -f $WORK_DIR/logs/bot.log"
echo -e "  ${GREEN}查看错误日志:${NC} tail -f $WORK_DIR/logs/bot.error.log"
echo ""
echo -e "${BLUE}🔧 常用管理命令：${NC}"
echo -e "  ${GREEN}查看状态:${NC} sudo systemctl status polymarket-bot"
echo -e "  ${GREEN}重启服务:${NC} sudo systemctl restart polymarket-bot"
echo -e "  ${GREEN}停止服务:${NC} sudo systemctl stop polymarket-bot"
echo -e "  ${GREEN}查看统计:${NC} cd $WORK_DIR && source .venv/bin/activate && python main.py stats"
echo ""
echo -e "${BLUE}📈 性能优化已应用：${NC}"
echo -e "  ✓ 文件描述符限制: 65535"
echo -e "  ✓ TCP 快速回收已启用"
echo -e "  ✓ WebSocket 实时监控模式"
echo ""
echo -e "${BLUE}🚀 下一步（可选）：${NC}"
echo -e "  1. 配置 Telegram 通知: nano $WORK_DIR/.env"
echo -e "  2. 查看网络延迟评估（已在启动日志中）"
echo -e "  3. 配置 GitHub Actions 自动部署: 参考 docs/VPS_DEPLOYMENT.md"
echo ""
echo -e "${YELLOW}💡 提示: 运行以下命令查看实时日志${NC}"
echo -e "${GREEN}journalctl -u polymarket-bot -f${NC}"
echo ""
