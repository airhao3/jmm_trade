#!/bin/bash
# VPS 初始化脚本 - 首次部署时在 VPS 上运行
# Usage: bash setup_vps.sh

set -e

echo "=========================================="
echo "  Polymarket Bot - VPS Setup Script"
echo "=========================================="

# 检查是否为 root
if [ "$EUID" -eq 0 ]; then 
    echo "请不要以 root 用户运行此脚本"
    echo "建议创建普通用户: adduser trader && usermod -aG sudo trader"
    exit 1
fi

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
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

# 1. 检查系统
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

info "✓ 系统环境检查完成"

# 2. 克隆或更新代码
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

# 3. 创建虚拟环境
if [ ! -d ".venv" ]; then
    info "创建 Python 虚拟环境..."
    python3.11 -m venv .venv
fi

info "激活虚拟环境并安装依赖..."
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. 配置环境变量
if [ ! -f ".env" ]; then
    info "创建 .env 配置文件..."
    cp .env.example .env
    warn "请编辑 .env 文件填入你的配置: nano .env"
else
    info "✓ .env 文件已存在"
fi

# 5. 创建必要目录
info "创建数据和日志目录..."
mkdir -p data data/exports logs

# 6. 验证配置
info "验证配置..."
if python main.py check-config; then
    info "✓ 配置验证通过"
else
    error "配置验证失败，请检查 config/config.yaml 和 .env"
    exit 1
fi

# 7. 创建 systemd 服务
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

info "✓ systemd 服务文件已创建: $SERVICE_FILE"

# 8. 重载 systemd
info "重载 systemd..."
sudo systemctl daemon-reload

# 9. 询问是否立即启动
echo ""
read -p "是否立即启动服务？(y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    info "启动服务..."
    sudo systemctl start polymarket-bot
    sudo systemctl enable polymarket-bot
    
    sleep 2
    
    if sudo systemctl is-active --quiet polymarket-bot; then
        info "✓ 服务已启动并设置为开机自启"
        echo ""
        info "查看服务状态: sudo systemctl status polymarket-bot"
        info "查看实时日志: journalctl -u polymarket-bot -f"
        info "查看文件日志: tail -f logs/bot.log"
    else
        error "服务启动失败，请检查日志"
        sudo journalctl -u polymarket-bot -n 20 --no-pager
        exit 1
    fi
else
    info "跳过启动，稍后可手动启动:"
    echo "  sudo systemctl start polymarket-bot"
    echo "  sudo systemctl enable polymarket-bot"
fi

echo ""
echo "=========================================="
echo "  ✓ VPS 设置完成！"
echo "=========================================="
echo ""
echo "常用命令:"
echo "  启动服务: sudo systemctl start polymarket-bot"
echo "  停止服务: sudo systemctl stop polymarket-bot"
echo "  重启服务: sudo systemctl restart polymarket-bot"
echo "  查看状态: sudo systemctl status polymarket-bot"
echo "  查看日志: journalctl -u polymarket-bot -f"
echo "  查看统计: cd $WORK_DIR && source .venv/bin/activate && python main.py stats"
echo ""
echo "下一步:"
echo "  1. 编辑 .env 文件（如需 Telegram 通知）: nano .env"
echo "  2. 重启服务使配置生效: sudo systemctl restart polymarket-bot"
echo "  3. 配置 GitHub Actions 自动部署（参考 docs/VPS_DEPLOYMENT.md）"
echo ""
