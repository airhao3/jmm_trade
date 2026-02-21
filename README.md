# Polymarket Copy Trading Simulator

A Python-based simulation bot that monitors top traders on Polymarket and performs shadow (simulated) copy-trading. **No real orders are ever placed.**

## Features

- **Multi-account monitoring** – track multiple target wallets simultaneously
- **Dual-delay simulation** – sample orderbook at 1s and 3s after target trade
- **Slippage & fee tracking** – realistic cost modeling (1.5% fee default)
- **Market filtering** – focus on BTC/ETH 5–15 min Up/Down markets
- **SQLite persistence** – full trade history with PnL analytics
- **Notification system** – Telegram and iMessage with batched delivery
- **Structured logging** – JSON logs + periodic metrics snapshots
- **CLI interface** – `run`, `export`, `stats`, `check-config` commands
- **Docker support** – one-command deployment

## Quick Start

```bash
# 1. Clone and enter the project
cd /path/to/JMM_trade

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure
cp .env.example .env
# Edit .env with your API keys

# 5. Validate config
python main.py check-config

# 6. Run
python main.py run
```

## CLI Usage

```bash
# Poll mode (default)
python main.py run

# WebSocket mode
python main.py run --mode ws

# Dry run (no notifications)
python main.py run --dry-run

# Export trades to CSV
python main.py export

# Show statistics
python main.py stats

# Validate configuration
python main.py check-config

# Custom config path
python main.py --config path/to/config.yaml run
```

## Configuration

All configuration lives in `config/config.yaml`. Key sections:

| Section | Purpose |
|---------|---------|
| `system` | Read-only safety flags |
| `monitoring` | Poll interval, mode, concurrency |
| `simulation` | Delays, investment amount, fee rate |
| `market_filter` | Asset types, duration, keywords |
| `targets` | Wallet addresses to monitor |
| `api` | Polymarket API endpoints and rate limits |
| `notifications` | Telegram/iMessage settings |
| `database` | SQLite path, cache TTL |
| `export` | CSV export settings |
| `logging` | Log level, format, metrics interval |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `POLYMARKET_API_KEY` | No* | API key (read-only scope) |
| `POLYMARKET_SECRET` | No* | API secret |
| `POLYMARKET_PASSPHRASE` | No* | API passphrase |
| `TELEGRAM_BOT_TOKEN` | No | Telegram bot token |
| `TELEGRAM_CHAT_ID` | No | Telegram chat ID |
| `IMESSAGE_PHONE` | No | iMessage phone number |
| `FORCE_READ_ONLY` | No | Safety override (default: `true`) |
| `LOG_LEVEL` | No | Override log level |

\* Most public data endpoints work without authentication.

## Project Structure

```
├── config/config.yaml          # Unified configuration
├── src/
│   ├── config/                 # Pydantic models + loader
│   ├── api/                    # Async API client, WebSocket, rate limiter
│   ├── core/                   # Monitor, Simulator, Portfolio, Settlement
│   ├── data/                   # SQLite database, data models, CSV export
│   ├── notifications/          # Telegram, iMessage, aggregation manager
│   ├── utils/                  # Structured logger, metrics collector
│   └── cli/                    # Click CLI commands
├── main.py                     # Entry point
├── Dockerfile                  # Container image
└── docker-compose.yml          # One-command deploy
```

## VPS Deployment

**推荐用于 24/7 运行和最低延迟（~50ms API latency）**

### 快速部署

```bash
# 1. SSH 到 VPS (Ubuntu 22.04, US East 推荐)
ssh your_user@YOUR_VPS_IP

# 2. 运行一键设置脚本
curl -fsSL https://raw.githubusercontent.com/airhao3/jmm_trade/main/deploy/setup_vps.sh -o setup_vps.sh
bash setup_vps.sh

# 3. 编辑配置（可选）
nano .env

# 4. 启动服务
sudo systemctl start polymarket-bot
sudo systemctl enable polymarket-bot

# 5. 查看状态
sudo systemctl status polymarket-bot
journalctl -u polymarket-bot -f
```

### CI/CD 自动部署

配置 GitHub Actions 后，每次推送到 `main` 分支自动部署到 VPS。

**配置步骤**:
1. 在 GitHub 仓库添加 Secrets: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`
2. 推送代码，自动触发部署工作流
3. 查看 Actions 页面确认部署成功

**详细文档**: [`docs/VPS_DEPLOYMENT.md`](docs/VPS_DEPLOYMENT.md)

## Docker

```bash
# Build and run
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

## Documentation

| Document | Description |
|----------|-------------|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Full system architecture, module reference, DB schema, config reference, API endpoints, safety design |
| [`docs/VPS_DEPLOYMENT.md`](docs/VPS_DEPLOYMENT.md) | **VPS deployment guide**, manual setup, CI/CD auto-deploy, configuration, monitoring, troubleshooting |
| [`docs/DEPLOYMENT_ASSESSMENT.md`](docs/DEPLOYMENT_ASSESSMENT.md) | VPS feasibility analysis, latency benchmarks, performance expectations, optimization roadmap |
| [`docs/TESTING.md`](docs/TESTING.md) | Test strategy, test case catalog (60+ cases), running tests, coverage goals, writing new tests |
| [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) | Development workflow, code conventions, **AI coding assistant guide**, debugging scenarios, security checklist |
| [`docs/CICD.md`](docs/CICD.md) | CI/CD pipeline design, GitHub Actions workflows, deployment procedures, monitoring & alerting |
| [`CHANGELOG.md`](CHANGELOG.md) | Version history, commit conventions, release template |

## Testing

```bash
# Run all tests
pytest tests/ -v

# Unit tests only
pytest tests/ -m unit -v

# Integration tests only
pytest tests/ -m integration -v

# With coverage
pytest tests/ --cov=src --cov-report=term-missing
```

## Safety

- `READ_ONLY_MODE = True` is enforced at multiple levels
- `FORCE_READ_ONLY` environment variable provides an additional safety layer
- `create_order()` and `cancel_order()` methods raise `RuntimeError` unconditionally
- No private keys are used for signing transactions

## License

Private – Internal use only.
# Test deploy - Sun Feb 22 00:18:48 CST 2026
# Deploy test 1771692390
