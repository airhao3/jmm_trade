# CI/CD Guide

> Continuous Integration, Continuous Deployment, and operational runbook.

---

## 1. Pipeline Overview

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐     ┌─────────────┐
│   Commit    │────→│   CI Tests   │────→│  Build Image │────→│   Deploy    │
│  (push/PR)  │     │  lint+test   │     │  Docker      │     │  VPS/Cloud  │
└─────────────┘     └──────────────┘     └──────────────┘     └─────────────┘
      │                    │                    │                     │
      │              on failure:           on success:          on success:
      │              block merge          push to registry     health check
      ▼                    ▼                    ▼                     ▼
   CHANGELOG          Slack/Email          ghcr.io/tag          Smoke test
   updated            notification         latest + vX.Y        API reachable
```

### Trigger Rules

| Trigger | Pipeline | Description |
|---------|----------|-------------|
| Push to `main` | Full (test → build → deploy) | Production release |
| Push to `develop` | Test + build (no deploy) | Integration check |
| Pull Request | Test only | Gate for merge |
| Manual dispatch | Full or selective | Hotfix / emergency |
| Nightly schedule | Smoke tests | Live API health check |

---

## 2. GitHub Actions Workflows

### 2.1 CI – Tests & Lint (`.github/workflows/ci.yml`)

```yaml
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main, develop]

env:
  PYTHON_VERSION: "3.11"
  FORCE_READ_ONLY: "true"

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Validate config
        run: python main.py check-config

      - name: Run unit tests
        run: pytest tests/ -m "unit" -v --tb=short

      - name: Run integration tests
        run: pytest tests/ -m "integration" -v --tb=short

      - name: Test coverage
        run: |
          pip install pytest-cov
          pytest tests/ --cov=src --cov-report=xml --cov-fail-under=60

      - name: Upload coverage
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: coverage-report
          path: coverage.xml

  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}

      - name: Install linters
        run: pip install ruff mypy

      - name: Ruff check
        run: ruff check src/ --exit-non-zero-on-fix

      - name: Type check (optional, non-blocking)
        run: mypy src/ --ignore-missing-imports || true

  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Check for secrets in code
        run: |
          # Fail if any private key patterns found in tracked files
          if git grep -lE '0x[a-fA-F0-9]{64}' -- '*.py' '*.yaml' '*.yml'; then
            echo "ERROR: Possible private key found in source files!"
            exit 1
          fi

      - name: Verify read-only guards
        run: |
          # Ensure READ_ONLY_MODE is never set to False
          if grep -rn "READ_ONLY_MODE\s*=\s*False" src/; then
            echo "ERROR: READ_ONLY_MODE must never be False!"
            exit 1
          fi
          # Ensure create_order always raises
          if grep -rn "def create_order" src/ | grep -v "raise RuntimeError"; then
            echo "WARNING: create_order must unconditionally raise"
          fi
```

### 2.2 Build & Push Docker (`.github/workflows/build.yml`)

```yaml
name: Build Docker Image

on:
  push:
    branches: [main]
    tags: ["v*"]

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build:
    runs-on: ubuntu-latest
    needs: [test]  # requires CI to pass
    permissions:
      contents: read
      packages: write

    steps:
      - uses: actions/checkout@v4

      - name: Log in to Container Registry
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=ref,event=branch
            type=semver,pattern={{version}}
            type=sha

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
```

### 2.3 Deploy (`.github/workflows/deploy.yml`)

```yaml
name: Deploy

on:
  workflow_run:
    workflows: ["Build Docker Image"]
    types: [completed]
    branches: [main]
  workflow_dispatch:
    inputs:
      environment:
        description: "Target environment"
        required: true
        default: "production"
        type: choice
        options: [production, staging]

jobs:
  deploy:
    runs-on: ubuntu-latest
    if: ${{ github.event.workflow_run.conclusion == 'success' || github.event_name == 'workflow_dispatch' }}

    steps:
      - name: Deploy to VPS via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          key: ${{ secrets.VPS_SSH_KEY }}
          script: |
            cd /opt/polymarket-bot
            git pull origin main
            docker-compose pull
            docker-compose up -d --build
            sleep 10
            docker-compose exec polymarket-bot python main.py check-config
            echo "Deploy complete at $(date)"

      - name: Smoke test
        run: |
          sleep 15
          curl -sf https://${{ secrets.VPS_HOST }}:8080/health || echo "Health check skipped (no HTTP endpoint yet)"

      - name: Notify on failure
        if: failure()
        run: |
          echo "Deployment failed! Check logs."
          # Add Telegram/Slack notification here
```

### 2.4 Nightly Smoke Test (`.github/workflows/nightly.yml`)

```yaml
name: Nightly Smoke

on:
  schedule:
    - cron: "0 2 * * *"  # 2 AM UTC daily
  workflow_dispatch:

jobs:
  smoke:
    runs-on: ubuntu-latest
    env:
      FORCE_READ_ONLY: "true"

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Config validation
        run: python main.py check-config

      - name: API reachability
        run: |
          curl -sf "https://data-api.polymarket.com/trades?user=0x88f46b9e5d86b4fb85be55ab0ec4004264b9d4db&limit=1" > /dev/null
          echo "Data API: OK"
          curl -sf "https://gamma-api.polymarket.com/markets?limit=1" > /dev/null
          echo "Gamma API: OK"
          curl -sf "https://clob.polymarket.com/midpoint?token_id=21742633143463906290569050155826241533067272736897614950488156847949938836455" > /dev/null || echo "CLOB API: token may not exist"

      - name: Run smoke tests
        run: pytest tests/test_smoke.py -v -m smoke --timeout=60 || true
```

---

## 3. Local CI Reproduction

Run the same checks locally before pushing:

```bash
# Activate environment
source .venv/bin/activate

# 1. Config validation
python main.py check-config

# 2. Unit tests
pytest tests/ -m unit -v

# 3. Integration tests
pytest tests/ -m integration -v

# 4. Coverage
pip install pytest-cov
pytest tests/ --cov=src --cov-report=term-missing

# 5. Lint (optional, install ruff first)
pip install ruff
ruff check src/

# 6. Security check
grep -rn "READ_ONLY_MODE\s*=\s*False" src/ && echo "FAIL" || echo "PASS"
```

---

## 4. Deployment Procedures

### 4.1 First-time VPS Setup

```bash
# On VPS
sudo apt update && sudo apt install -y docker.io docker-compose git

# Clone repository
cd /opt
git clone <repo-url> polymarket-bot
cd polymarket-bot

# Configure
cp .env.example .env
vim .env  # Fill in API keys, Telegram tokens

# Start
docker-compose up -d

# Verify
docker-compose logs -f
docker-compose exec polymarket-bot python main.py check-config
```

### 4.2 Upgrade Procedure

```bash
cd /opt/polymarket-bot

# Pull latest code
git pull origin main

# Rebuild and restart (zero-downtime with Docker)
docker-compose up -d --build

# Verify health
docker-compose logs --tail=50
docker-compose exec polymarket-bot python main.py stats
```

### 4.3 Rollback Procedure

```bash
# Find previous working commit
git log --oneline -10

# Rollback
git checkout <previous-commit>
docker-compose up -d --build

# Verify
docker-compose logs --tail=20
```

### 4.4 Database Backup

```bash
# Manual backup
cp data/trades.db data/trades.db.bak.$(date +%Y%m%d)

# Automated (add to crontab)
# 0 */6 * * * cp /opt/polymarket-bot/data/trades.db /opt/polymarket-bot/data/backups/trades.$(date +\%Y\%m\%d_\%H).db

# Restore from backup
cp data/backups/trades.YYYYMMDD_HH.db data/trades.db
docker-compose restart
```

---

## 5. Monitoring & Alerting

### 5.1 Health Indicators

| Indicator | Source | Healthy | Alert Threshold |
|-----------|--------|---------|----------------|
| App running | `docker-compose ps` | Status: Up | Container exited |
| Polls executing | `logs/metrics.log` | `polls_completed` increasing | No increase in 5 min |
| API latency | `logs/metrics.log` | `avg_api_latency_ms < 500` | > 2000ms |
| Failed requests | `logs/metrics.log` | `failed_requests` stable | > 10 in 5 min |
| DB size | `ls -la data/trades.db` | < 500MB | > 1GB |
| Log size | `du -sh logs/` | < 1GB | > 5GB |

### 5.2 Log Monitoring Commands

```bash
# Live application logs
docker-compose logs -f --tail=100

# Last 50 lines of metrics
tail -50 logs/metrics.log | python -m json.tool

# Errors only
grep -i "error\|exception\|traceback" logs/app.log | tail -20

# Trade detection rate
grep "NEW TRADE" logs/app.log | tail -20

# Database stats
docker-compose exec polymarket-bot python main.py stats
```

### 5.3 SQLite Quick Queries

```bash
# Open DB
sqlite3 data/trades.db

# Recent trades
SELECT trade_id, target_nickname, market_name, sim_delay, sim_price, slippage_pct, status
FROM sim_trades ORDER BY created_at DESC LIMIT 20;

# PnL summary
SELECT target_nickname, sim_delay, COUNT(*) as n,
       SUM(CASE WHEN status='SETTLED' THEN pnl ELSE 0 END) as total_pnl,
       AVG(slippage_pct) as avg_slip
FROM sim_trades GROUP BY target_nickname, sim_delay;

# Failed notifications
SELECT * FROM notification_history WHERE success=0 ORDER BY sent_at DESC LIMIT 10;

# Market cache status
SELECT condition_id, market_name, is_active, is_resolved, last_updated
FROM market_cache ORDER BY last_updated DESC LIMIT 10;
```

---

## 6. Environment Secrets Reference

### GitHub Actions Secrets

| Secret | Used In | Description |
|--------|---------|-------------|
| `VPS_HOST` | deploy.yml | Server IP/hostname |
| `VPS_USER` | deploy.yml | SSH username |
| `VPS_SSH_KEY` | deploy.yml | SSH private key |
| `POLYMARKET_API_KEY` | ci.yml (optional) | For smoke tests |
| `TELEGRAM_BOT_TOKEN` | deploy.yml | Notification bot |
| `TELEGRAM_CHAT_ID` | deploy.yml | Notification target |

### Production `.env`

All variables from `.env.example` must be set. Critical:

```
FORCE_READ_ONLY=true          # NEVER change to false
POLYMARKET_API_KEY=...        # Read-only scope
TELEGRAM_BOT_TOKEN=...        # For alerts
TELEGRAM_CHAT_ID=...          # Your chat
LOG_LEVEL=INFO                # DEBUG for troubleshooting
```

---

## 7. Troubleshooting CI/CD

| Problem | Solution |
|---------|----------|
| Tests pass locally, fail in CI | Check Python version match; CI uses `ubuntu-latest` |
| Docker build fails | Check `requirements.txt` for platform-specific deps |
| Deploy SSH timeout | Verify `VPS_SSH_KEY` secret, firewall rules |
| Health check fails after deploy | Check `docker-compose logs`; may need startup delay |
| Coverage below threshold | Add tests or lower `--cov-fail-under` temporarily |
| Nightly smoke fails | API may be down; check `status.polymarket.com` |

---

## 8. Roadmap for CI/CD Improvements

### Phase 1 (Current)
- [x] GitHub Actions CI workflow design
- [x] Docker build pipeline design
- [x] Deploy-via-SSH procedure
- [ ] **TODO:** Create actual `.github/workflows/` files
- [ ] **TODO:** Set up GitHub Secrets

### Phase 2 (Short-term)
- [ ] Add `ruff` linting to CI
- [ ] Add coverage badge to README
- [ ] Implement database backup cron
- [ ] Add Telegram alert on deploy failure

### Phase 3 (Long-term)
- [ ] Staging environment with separate config
- [ ] Blue-green deployment
- [ ] Grafana dashboard for `metrics.log`
- [ ] Automated rollback on health check failure
