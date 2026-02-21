# 部署文件说明

此目录包含 VPS 部署所需的配置文件和脚本。

## 文件列表

| 文件 | 说明 |
|------|------|
| `setup_vps.sh` | VPS 初始化脚本（首次部署） |
| `polymarket-bot.service` | systemd 服务模板 |

## 使用方法

### 首次部署

1. **SSH 连接到 VPS**:
   ```bash
   ssh your_user@YOUR_VPS_IP
   ```

2. **下载并运行设置脚本**:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/airhao3/jmm_trade/main/deploy/setup_vps.sh -o setup_vps.sh
   bash setup_vps.sh
   ```

   或手动克隆后运行：
   ```bash
   git clone https://github.com/airhao3/jmm_trade.git
   cd jmm_trade
   bash deploy/setup_vps.sh
   ```

3. **编辑配置**（如需要）:
   ```bash
   nano .env
   ```

4. **启动服务**:
   ```bash
   sudo systemctl start polymarket-bot
   sudo systemctl enable polymarket-bot
   ```

### CI/CD 自动部署

配置 GitHub Actions 后，每次推送到 `main` 分支会自动部署到 VPS。

**配置步骤**:

1. 在 VPS 上生成 SSH 密钥对（用于 GitHub Actions）
2. 在 GitHub 仓库添加 Secrets（VPS_HOST, VPS_USER, VPS_SSH_KEY）
3. 推送代码到 main 分支，自动触发部署

详细说明见 [docs/VPS_DEPLOYMENT.md](../docs/VPS_DEPLOYMENT.md)

## systemd 服务管理

```bash
# 启动
sudo systemctl start polymarket-bot

# 停止
sudo systemctl stop polymarket-bot

# 重启
sudo systemctl restart polymarket-bot

# 查看状态
sudo systemctl status polymarket-bot

# 查看日志
journalctl -u polymarket-bot -f

# 开机自启
sudo systemctl enable polymarket-bot

# 禁用开机自启
sudo systemctl disable polymarket-bot
```

## 手动部署更新

```bash
cd ~/jmm_trade
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart polymarket-bot
```

## 故障排查

如果服务无法启动：

```bash
# 查看详细日志
journalctl -u polymarket-bot -n 50 --no-pager

# 检查配置
cd ~/jmm_trade
source .venv/bin/activate
python main.py check-config

# 手动运行（调试）
python main.py run --dry-run
```
