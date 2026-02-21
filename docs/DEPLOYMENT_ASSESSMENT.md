# Deployment Feasibility Assessment

> Based on live testing on 2026-02-21 (macOS, China → US East API)

---

## 1. Live Test Results Summary

| Metric | macOS (local) | VPS (US East, estimated) |
|--------|--------------|--------------------------|
| API latency (Data API) | 975–1591ms | ~50–100ms |
| API latency (CLOB /book) | 340–2177ms | ~30–80ms |
| API latency (Gamma /markets) | 935–2056ms | ~50–150ms |
| Poll cycle time | 241–593ms | ~50–100ms |
| Avg poll latency | 367–487ms | ~50ms |
| Polls per minute | ~17–20 | ~20 |
| Memory usage | ~50MB RSS | ~50MB RSS |
| New trade detection | ✓ (detected real PBot1 trade) | ✓ |

## 2. Core Finding: Slippage on 5-Minute Markets

**All 8 live-detected trades FAILED with ~105% average slippage.**

```
Target price:  0.52  (PBot1 bought BUY)
Sim price:     0.99  (orderbook 1-3s later)
Slippage:      90.38%
```

### Why This Happens

These are **ultra-short binary markets** (5-minute Bitcoin/Ethereum Up/Down). The lifecycle:

```
T+0s:   Market opens, price ≈ 0.50 (50/50 odds)
T+1-60s: Price moves toward resolution based on BTC movement
T+300s:  Market resolves at 1.0 (YES wins) or 0.0 (NO wins)
```

PBot1 trades at ~T+60-120s when the price is still 0.47-0.52. But by the time we detect (3s poll interval) and simulate (1-3s delay), the price has already moved to 0.99 because the market is resolving.

**Total detection-to-simulation latency:**

| Component | macOS | VPS (US East) | VPS + WebSocket |
|-----------|-------|---------------|-----------------|
| Trade detection (poll) | 0–3s | 0–3s | <0.1s |
| API call latency | 0.5s | 0.05s | 0.05s |
| Simulation delay | 1–3s | 1–3s | 1–3s |
| **Total** | **1.5–6.5s** | **1.05–6.05s** | **1.15–3.15s** |

### Key Insight

For 5-minute binary markets, **even 1 second of delay can mean 50%+ price movement**. The fundamental problem is not network latency — it's market speed. A VPS reduces API latency from ~500ms to ~50ms, but the poll interval (3s) and simulation delay (1-3s) dominate.

## 3. VPS Deployment: Feasibility

### 3a. Recommended VPS Specs

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 1 vCPU | 2 vCPU |
| RAM | 512MB | 1GB |
| Disk | 5GB SSD | 10GB SSD |
| Network | 1Gbps | 1Gbps |
| OS | Ubuntu 22.04 | Ubuntu 22.04 |
| Location | **US East (Virginia)** | US East |
| Cost | ~$5/mo (Vultr/DigitalOcean) | ~$6-12/mo |

### 3b. VPS Benefits

1. **10x lower API latency** (50ms vs 500ms)
2. **24/7 uptime** — no laptop sleep/disconnect
3. **Stable network** — no WiFi drops
4. **Docker-ready** — `docker-compose up -d` deployment
5. **Closer to Polymarket servers** (US East)

### 3c. VPS Limitations (same as local)

1. **Poll mode latency floor** — 3s poll interval is the bottleneck, not network
2. **5-minute market speed** — markets resolve too fast for copy-trading with delay
3. **No real orders** — simulation only (READ_ONLY_MODE)

## 4. Recommendations

### Short-term (current architecture)

| Action | Impact | Effort |
|--------|--------|--------|
| Deploy to US East VPS | Reduce API latency 10x | Low |
| Reduce poll_interval to 1s | Detect trades 3x faster | Config change |
| Add delay=0 (immediate) | Baseline comparison data | Config change |
| Increase max_slippage_pct to 200% | Record all trades as OPEN for analysis | Config change |

### Medium-term (architecture changes)

| Action | Impact | Effort |
|--------|--------|--------|
| **Implement WebSocket mode** | Near-instant trade detection (<100ms) | Medium |
| Track target PnL | Compare target's profit vs our simulated entry | Low |
| Add longer-duration markets (30min, 1hr) | Less price movement in delay window | Config |
| Parallel orderbook fetch | Reduce simulation time | Low |

### Long-term (strategy changes)

| Action | Impact | Effort |
|--------|--------|--------|
| Target slower markets (daily/weekly) | Much lower slippage | Strategy |
| Multiple target accounts | More trade signals | Config |
| Pre-fetch orderbooks for active markets | Instant price snapshot | Medium |
| ML-based slippage prediction | Skip known-bad trades | High |

## 5. Quick VPS Deploy

```bash
# On VPS (Ubuntu 22.04)
git clone git@github.com:airhao3/jmm_trade.git
cd jmm_trade

# Option A: Docker
cp .env.example .env
# Edit .env with your keys
docker-compose up -d

# Option B: Direct
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env
python main.py check-config
nohup python main.py run > /dev/null 2>&1 &

# Monitor
python main.py stats
tail -f logs/polymarket_*.log
```

## 6. Suggested Config for VPS

```yaml
monitoring:
  mode: poll
  poll_interval: 1          # reduced from 3
  max_concurrent: 5

simulation:
  delays: [0, 1, 3]         # added 0s for baseline
  investment_per_trade: 100.0
  fee_rate: 0.015
  enable_slippage_check: true
  max_slippage_pct: 200.0   # record everything, analyze later

market_filter:
  enabled: true
  assets: ["BTC", "ETH", "Bitcoin", "Ethereum"]
  min_duration_minutes: 5
  max_duration_minutes: 60   # include longer markets
  keywords: ["up", "down", "higher", "lower"]
  exclude_keywords: ["week", "month", "year"]
```

## 7. Conclusion

**VPS deployment is highly feasible and recommended.** The bot runs with minimal resources (~50MB RAM, negligible CPU). The main value of a VPS is 24/7 uptime and lower latency.

However, **the fundamental challenge is market speed, not infrastructure.** For 5-minute binary markets, even 1-second delay results in massive slippage. The most impactful improvements are:

1. **WebSocket mode** for near-instant trade detection
2. **Targeting slower markets** (30min+) where 1-3s delay is negligible
3. **Recording all trades** (max_slippage=200%) for data analysis before optimizing strategy
