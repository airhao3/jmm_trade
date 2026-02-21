# VPS 部署指南

完整的 VPS 部署教程，包括手动部署和 CI/CD 自动部署两种方式。

---

## 目录

1. [VPS 推荐配置](#1-vps-推荐配置)
2. [手动部署（首次）](#2-手动部署首次)
3. [CI/CD 自动部署](#3-cicd-自动部署)
4. [配置管理](#4-配置管理)
5. [监控和维护](#5-监控和维护)
6. [故障排查](#6-故障排查)

---

## 1. VPS 推荐配置

### 最低配置
- **CPU**: 1 vCPU
- **内存**: 512MB RAM
- **存储**: 5GB SSD
- **网络**: 1Gbps
- **位置**: **US East (Virginia)** — 最接近 Polymarket 服务器
- **系统**: Ubuntu 22.04 LTS

### 推荐配置
- **CPU**: 2 vCPU
- **内存**: 1GB RAM
- **存储**: 10GB SSD
- **费用**: $5-12/月 (Vultr, DigitalOcean, Linode)

### 推荐供应商
| 供应商 | 位置 | 价格 | 链接 |
|--------|------|------|------|
| Vultr | New Jersey | $6/mo | https://www.vultr.com |
| DigitalOcean | New York | $6/mo | https://www.digitalocean.com |
| Linode | Newark | $5/mo | https://www.linode.com |
| AWS Lightsail | Virginia | $5/mo | https://aws.amazon.com/lightsail |

---

## 2. 手动部署（首次）

### 2.1 初始连接和用户配置

#### 首次以 root 登录

```bash
# SSH 连接到 VPS（首次使用 root）
ssh root@YOUR_VPS_IP

# 如果 VPS 提供商给了密码，首次登录后建议修改
passwd
```

#### 创建专用用户（强烈推荐）

为安全起见，不要直接使用 root 用户运行应用。创建专用用户：

```bash
# 创建新用户 trader（可自定义用户名）
adduser trader

# 提示输入密码和用户信息，按提示操作：
#   New password: ******
#   Retype new password: ******
#   Full Name []: Polymarket Trader
#   其他信息可直接回车跳过

# 将用户添加到 sudo 组（获得管理员权限）
usermod -aG sudo trader

# 验证用户已添加到 sudo 组
groups trader
# 应该看到: trader : trader sudo
```

#### 配置 SSH 密钥登录（推荐）

**在本地机器上**:

```bash
# 生成 SSH 密钥对（如果还没有）
ssh-keygen -t ed25519 -C "your_email@example.com"

# 查看公钥
cat ~/.ssh/id_ed25519.pub
# 复制输出内容
```

**在 VPS 上（以 trader 用户）**:

```bash
# 切换到新创建的用户
su - trader

# 创建 .ssh 目录
mkdir -p ~/.ssh
chmod 700 ~/.ssh

# 添加公钥
nano ~/.ssh/authorized_keys
# 粘贴刚才复制的公钥内容，保存（Ctrl+O, Enter, Ctrl+X）

# 设置正确权限
chmod 600 ~/.ssh/authorized_keys

# 退出到 root
exit
```

**测试 SSH 密钥登录**:

```bash
# 在本地机器测试（新开一个终端窗口，不要关闭当前连接）
ssh trader@YOUR_VPS_IP

# 应该能无密码直接登录
# 如果成功，可以禁用 root SSH 登录（可选，更安全）
```

#### 禁用 root SSH 登录（可选，推荐）

确认 trader 用户可以正常登录后：

```bash
# 以 trader 用户登录 VPS
ssh trader@YOUR_VPS_IP

# 编辑 SSH 配置
sudo nano /etc/ssh/sshd_config

# 找到并修改以下行:
#   PermitRootLogin yes
# 改为:
#   PermitRootLogin no

# 保存并重启 SSH 服务
sudo systemctl restart sshd

# 注意：确保 trader 用户能正常登录后再执行此步骤！
```

#### 切换到工作用户

```bash
# 如果当前是 root，切换到 trader
su - trader

# 或直接以 trader 登录
ssh trader@YOUR_VPS_IP

# 验证当前用户
whoami
# 应该显示: trader
```

### 2.2 安装依赖

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装 Python 3.11
sudo apt install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev

# 安装 Git
sudo apt install -y git

# 安装 Docker（可选，用于容器化部署）
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
```

### 2.3 克隆代码

```bash
# 克隆仓库
git clone https://github.com/airhao3/jmm_trade.git
cd jmm_trade

# 或使用 SSH（需配置 GitHub SSH key）
git clone git@github.com:airhao3/jmm_trade.git
cd jmm_trade
```

### 2.4 配置环境

```bash
# 创建虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install --upgrade pip
pip install -r requirements.txt

# 创建 .env 文件
cp .env.example .env
nano .env  # 编辑配置
```

**`.env` 配置示例**:
```bash
# 如果需要 Telegram 通知
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# 如果需要其他 API keys
# POLYMARKET_API_KEY=...  # 当前不需要

# 强制只读模式（必须）
FORCE_READ_ONLY=true
```

### 2.5 验证配置

```bash
# 检查配置
python main.py check-config

# 应该看到:
# Config is valid!
#   Active targets: 1
#   Mode:           poll
#   Investment:     $100.0
#   ...
```

### 2.6 创建 systemd 服务（后台运行）

```bash
# 创建服务文件
sudo nano /etc/systemd/system/polymarket-bot.service
```

**服务文件内容**:
```ini
[Unit]
Description=Polymarket Copy Trading Bot
After=network.target

[Service]
Type=simple
User=trader
WorkingDirectory=/home/trader/jmm_trade
Environment="PATH=/home/trader/jmm_trade/.venv/bin"
Environment="FORCE_READ_ONLY=true"
ExecStart=/home/trader/jmm_trade/.venv/bin/python main.py run
Restart=always
RestartSec=10

# 日志
StandardOutput=append:/home/trader/jmm_trade/logs/bot.log
StandardError=append:/home/trader/jmm_trade/logs/bot.error.log

[Install]
WantedBy=multi-user.target
```

**启动服务**:
```bash
# 创建日志目录
mkdir -p logs

# 重载 systemd（让系统识别新服务）
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start polymarket-bot

# 设置开机自启（重启 VPS 后自动运行）
sudo systemctl enable polymarket-bot

# 查看服务状态
sudo systemctl status polymarket-bot

# 应该看到:
# ● polymarket-bot.service - Polymarket Copy Trading Bot
#    Loaded: loaded (/etc/systemd/system/polymarket-bot.service; enabled)
#    Active: active (running) since ...
#    Main PID: 12345 (python)
#    ...
```

### 2.7 验证服务运行

#### 检查服务状态

```bash
# 查看服务是否运行
sudo systemctl is-active polymarket-bot
# 应该输出: active

# 查看服务是否开机自启
sudo systemctl is-enabled polymarket-bot
# 应该输出: enabled

# 查看详细状态
sudo systemctl status polymarket-bot
```

#### 查看日志

```bash
# 实时查看 systemd 日志
journalctl -u polymarket-bot -f

# 查看最近 50 行日志
journalctl -u polymarket-bot -n 50 --no-pager

# 查看文件日志
tail -f ~/jmm_trade/logs/bot.log

# 查看错误日志
tail -f ~/jmm_trade/logs/bot.error.log

# 应该看到类似输出:
# [INFO] Application starting in READ_ONLY mode
# [INFO] Monitoring 1 target accounts
# [INFO] Poll mode enabled (interval: 1s)
# [INFO] Starting poll loop...
# [INFO] Poll #1: 0 new trades discovered (latency: 52ms)
```

#### 验证进程

```bash
# 查看 Python 进程
ps aux | grep "main.py"

# 查看资源使用
top -p $(pgrep -f "main.py")

# 应该看到进程在运行，CPU < 5%, 内存 ~50MB
```

#### 测试 API 连接

```bash
cd ~/jmm_trade
source .venv/bin/activate

# 运行配置检查
python main.py check-config

# 运行本地 E2E 测试（验证 API 连接）
python tests/test_e2e_local.py

# 应该看到 API 延迟约 50-70ms（US East VPS）
```

### 2.8 常用服务管理命令

```bash
# 启动服务
sudo systemctl start polymarket-bot

# 停止服务
sudo systemctl stop polymarket-bot

# 重启服务
sudo systemctl restart polymarket-bot

# 重新加载配置（修改 .env 后）
sudo systemctl restart polymarket-bot

# 查看服务状态
sudo systemctl status polymarket-bot

# 查看实时日志
journalctl -u polymarket-bot -f

# 禁用开机自启
sudo systemctl disable polymarket-bot

# 启用开机自启
sudo systemctl enable polymarket-bot
```

---

## 3. CI/CD 自动部署

### 3.1 配置 GitHub Secrets

在 GitHub 仓库设置中添加以下 Secrets（Settings → Secrets and variables → Actions）:

| Secret 名称 | 说明 | 示例 |
|------------|------|------|
| `VPS_HOST` | VPS IP 地址 | `123.45.67.89` |
| `VPS_USER` | SSH 用户名 | `trader` |
| `VPS_SSH_KEY` | SSH 私钥 | `-----BEGIN OPENSSH PRIVATE KEY-----...` |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token（可选） | `123456:ABC-DEF...` |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID（可选） | `123456789` |

### 3.2 生成 SSH 密钥对

**在本地机器上**:
```bash
# 生成新的 SSH 密钥对（专用于部署）
ssh-keygen -t ed25519 -C "deploy@jmm_trade" -f ~/.ssh/jmm_deploy

# 查看私钥（复制到 GitHub Secret VPS_SSH_KEY）
cat ~/.ssh/jmm_deploy

# 查看公钥（需要添加到 VPS）
cat ~/.ssh/jmm_deploy.pub
```

**在 VPS 上**:
```bash
# 添加公钥到 authorized_keys
mkdir -p ~/.ssh
chmod 700 ~/.ssh
nano ~/.ssh/authorized_keys
# 粘贴公钥内容，保存

chmod 600 ~/.ssh/authorized_keys
```

**测试连接**:
```bash
# 在本地测试 SSH 连接
ssh -i ~/.ssh/jmm_deploy trader@YOUR_VPS_IP
```

### 3.3 自动部署流程

当你推送代码到 `main` 分支时，GitHub Actions 会自动：

1. ✅ 运行所有测试（lint + unit + integration）
2. 🔒 安全检查通过
3. 🚀 SSH 连接到 VPS
4. 📥 拉取最新代码
5. 📦 安装/更新依赖
6. 🔄 重启服务
7. ✅ 健康检查

**手动触发部署**:
```bash
# 在 GitHub Actions 页面点击 "Deploy to VPS" workflow
# 或使用 gh CLI
gh workflow run deploy.yml
```

### 3.4 回滚到上一版本

```bash
# SSH 到 VPS
ssh trader@YOUR_VPS_IP

cd jmm_trade

# 查看最近的 commits
git log --oneline -5

# 回滚到指定 commit
git checkout <commit-hash>

# 重启服务
sudo systemctl restart polymarket-bot

# 查看状态
sudo systemctl status polymarket-bot
```

---

## 4. 配置管理

### 4.1 VPS 专用配置

VPS 上建议使用优化后的配置（已在 `config/config.yaml` 中）:

```yaml
monitoring:
  poll_interval: 1        # 1秒轮询（本地是3秒）

simulation:
  delays: [0, 1, 3]       # 0s=即时快照
  max_slippage_pct: 50.0  # 记录更多交易用于分析

market_filter:
  max_duration_minutes: 60  # 包含更长市场
```

### 4.2 环境变量优先级

配置优先级（从高到低）:
1. 环境变量（`.env` 或 systemd `Environment`）
2. `config/config.yaml`
3. 代码默认值

### 4.3 日志管理

```bash
# 查看实时日志
tail -f logs/polymarket_*.log

# 查看最近 100 行
tail -100 logs/polymarket_*.log

# 搜索错误
grep -i error logs/polymarket_*.log

# 日志轮转（自动，由 loguru 管理）
# 默认: 100MB 轮转，保留 30 天
```

---

## 5. 监控和维护

### 5.1 查看运行状态

```bash
# 服务状态
sudo systemctl status polymarket-bot

# 进程状态
ps aux | grep "main.py"

# 资源使用
top -p $(pgrep -f "main.py")

# 内存使用
free -h
```

### 5.2 查看统计数据

```bash
cd jmm_trade
source .venv/bin/activate

# 查看统计
python main.py stats

# 导出 CSV
python main.py export
```

### 5.3 数据库备份

```bash
# 手动备份
cp data/trades.db data/trades_backup_$(date +%Y%m%d).db

# 自动备份脚本（添加到 crontab）
crontab -e

# 每天凌晨 2 点备份
0 2 * * * cd /home/trader/jmm_trade && cp data/trades.db data/trades_backup_$(date +\%Y\%m\%d).db

# 清理 30 天前的备份
0 3 * * * find /home/trader/jmm_trade/data -name "trades_backup_*.db" -mtime +30 -delete
```

### 5.4 更新代码

```bash
# 手动更新
cd jmm_trade
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart polymarket-bot

# 或使用 CI/CD 自动部署（推荐）
```

---

## 6. 故障排查

### 6.1 服务无法启动

```bash
# 查看详细错误
journalctl -u polymarket-bot -n 50 --no-pager

# 检查配置
python main.py check-config

# 检查权限
ls -la /home/trader/jmm_trade
ls -la /home/trader/jmm_trade/data

# 手动运行（调试模式）
source .venv/bin/activate
python main.py run --dry-run
```

### 6.2 API 延迟过高

```bash
# 检查网络延迟到 Polymarket
ping -c 5 clob.polymarket.com
curl -w "@-" -o /dev/null -s https://clob.polymarket.com/book <<'EOF'
    time_namelookup:  %{time_namelookup}\n
       time_connect:  %{time_connect}\n
    time_appconnect:  %{time_appconnect}\n
      time_redirect:  %{time_redirect}\n
   time_pretransfer:  %{time_pretransfer}\n
 time_starttransfer:  %{time_starttransfer}\n
                    ----------\n
         time_total:  %{time_total}\n
EOF

# 应该看到 time_total < 100ms（US East VPS）
```

### 6.3 数据库锁定

```bash
# 检查数据库
sqlite3 data/trades.db "PRAGMA integrity_check;"

# 如果损坏，从备份恢复
cp data/trades_backup_YYYYMMDD.db data/trades.db
sudo systemctl restart polymarket-bot
```

### 6.4 磁盘空间不足

```bash
# 检查磁盘使用
df -h

# 清理日志
find logs/ -name "*.log" -mtime +7 -delete

# 清理旧备份
find data/ -name "trades_backup_*.db" -mtime +30 -delete
```

### 6.5 内存不足

```bash
# 检查内存
free -h

# 如果 OOM，添加 swap
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 快速命令参考

```bash
# 启动/停止/重启
sudo systemctl start polymarket-bot
sudo systemctl stop polymarket-bot
sudo systemctl restart polymarket-bot

# 查看状态和日志
sudo systemctl status polymarket-bot
journalctl -u polymarket-bot -f
tail -f logs/polymarket_*.log

# 更新代码
cd jmm_trade && git pull && pip install -r requirements.txt
sudo systemctl restart polymarket-bot

# 查看统计
cd jmm_trade && source .venv/bin/activate && python main.py stats

# 备份数据库
cp data/trades.db data/trades_backup_$(date +%Y%m%d).db
```

---

## 安全建议

1. ✅ **永远保持 `FORCE_READ_ONLY=true`** — 绝不允许真实交易
2. 🔒 **使用 SSH 密钥** — 禁用密码登录
3. 🛡️ **配置防火墙** — 只开放必要端口（SSH 22）
4. 🔄 **定期更新系统** — `sudo apt update && sudo apt upgrade`
5. 📊 **监控资源** — 设置告警（内存、磁盘、CPU）
6. 💾 **定期备份** — 数据库和配置文件

---

## 性能优化建议

### VPS 位置选择
- **US East (Virginia/New York)** — 最佳，延迟 ~50ms
- **US West (California)** — 可接受，延迟 ~80ms
- **Europe (London/Frankfurt)** — 延迟 ~100-150ms
- **Asia (Singapore/Tokyo)** — 不推荐，延迟 >200ms

### 配置调优
```yaml
# 高频交易配置（US East VPS）
monitoring:
  poll_interval: 1
simulation:
  delays: [0, 1]
  max_slippage_pct: 20.0

# 稳定性优先配置
monitoring:
  poll_interval: 3
simulation:
  delays: [1, 3, 5]
  max_slippage_pct: 50.0
```

---

**下一步**: 参考 [DEPLOYMENT_ASSESSMENT.md](./DEPLOYMENT_ASSESSMENT.md) 了解 VPS 部署的可行性分析和性能预期。
