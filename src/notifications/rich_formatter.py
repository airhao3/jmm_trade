"""Rich notification formatter – multi-layer trade alerts.

Formats EnrichedTrade data into structured Telegram messages with:
  Layer 1: Basic trade info (market, action, price)
  Layer 2: Deep analysis (whale profile, orderbook, position)
  Layer 3: Cross-market reference (external prices, momentum)
"""

from __future__ import annotations

from src.core.enricher import EnrichedTrade


def format_rich_trade_alert(trade: EnrichedTrade) -> str:
    """Format a fully enriched trade into a rich Telegram notification."""
    sections = []

    # ── Header ─────────────────────────────────────────────
    signal_strength = _signal_strength(trade)
    sections.append(f"🚀 {signal_strength}")
    sections.append("")

    # ── Layer 1: Basic Info ────────────────────────────────
    whale = trade.whale
    labels_str = ""
    if whale.labels:
        labels_str = " | " + " ".join(whale.labels)

    profit_str = _format_usd(whale.all_time_profit)
    win_str = f"{whale.win_rate:.0f}%" if whale.win_rate else "N/A"
    rank_str = f"#{whale.rank}" if whale.rank else ""

    sections.append(
        f"👤 {whale.nickname} "
        f"(胜率: {win_str} | 累计盈利: {profit_str}{labels_str})"
        f" {rank_str}"
    )

    # Market
    title = trade.market_title
    if len(title) > 50:
        title = title[:47] + "..."
    sections.append(f"📊 市场: [{title}]")

    # Action details
    side_emoji = "🟢" if trade.side == "BUY" else "🔴"
    outcome_str = f" {trade.outcome}" if trade.outcome else ""
    sections.append(
        f"{side_emoji} 操作: {trade.side}{outcome_str}"
    )

    usd_str = _format_usd(trade.usd_value)
    sections.append(
        f"💰 规模: {usd_str} ({trade.size:.1f} Shares)"
    )

    sections.append(
        f"💲 成交价: ${trade.price:.4f} (概率: {trade.implied_probability:.1f}%)"
    )

    sections.append("")

    # ── Layer 2: Deep Analysis ─────────────────────────────
    sections.append("🔍 深度洞察:")

    # Position context
    pos = trade.position
    if pos.position_change == "NEW":
        pos_str = "🆕 新建仓位"
    elif pos.position_change == "ADD":
        pos_str = f"📈 加仓 (总持仓: {pos.total_shares:.1f} Shares ≈ {_format_usd(pos.total_value_usd)})"
    elif pos.position_change == "REDUCE":
        pos_str = f"📉 减仓 (剩余: {pos.total_shares:.1f} Shares)"
    elif pos.position_change == "EXIT":
        pos_str = "🚪 清仓退出"
    else:
        pos_str = "—"

    if pos.trade_count_recent > 1:
        pos_str += f" | 近10分钟第{pos.trade_count_recent}次操作"

    sections.append(f"  • 仓位: {pos_str}")

    # Orderbook
    ob = trade.orderbook
    if ob.spread_pct > 0:
        spread_quality = "流动性好" if ob.spread_pct < 1.0 else (
            "流动性一般" if ob.spread_pct < 3.0 else "流动性差"
        )
        sections.append(
            f"  • 价差: {ob.spread_pct:.1f}% ({spread_quality}) "
            f"| Bid: ${ob.best_bid:.4f} Ask: ${ob.best_ask:.4f}"
        )
        sections.append(
            f"  • 深度: 买盘 {_format_usd(ob.bid_depth_usd)} / "
            f"卖盘 {_format_usd(ob.ask_depth_usd)}"
        )

    # Market metadata
    mkt = trade.market
    if mkt.volume_24h > 0:
        sections.append(f"  • 24h成交量: {_format_usd(mkt.volume_24h)}")
    if mkt.liquidity > 0:
        sections.append(f"  • 流动性: {_format_usd(mkt.liquidity)}")

    sections.append("")

    # ── Layer 3: Cross-Market Reference ────────────────────
    if trade.external_price and trade.external_source:
        is_live = trade.external_source == "OKX"
        source_label = "⚡ OKX (实时)" if is_live else trade.external_source
        sections.append("📡 外部参考:")
        sections.append(
            f"  • {source_label}: "
            f"${trade.external_price:,.2f}"
        )
        # Show 1s momentum if available from raw_trade enrichment
        raw_ext = trade.raw_trade.get("_ext_momentum_1s")
        if raw_ext is not None:
            arrow = "📈" if raw_ext > 0 else "📉"
            sections.append(
                f"  • {arrow} 1秒动量: {raw_ext:+.3f}%"
            )
        if trade.premium_pct is not None:
            direction = "溢价" if trade.premium_pct > 0 else "折价"
            sections.append(
                f"  • 预测市场{direction}: {abs(trade.premium_pct):.2f}%"
            )
        sections.append("")

    # ── Risk Warning ───────────────────────────────────────
    risk_warnings = _assess_risk(trade)
    if risk_warnings:
        sections.append("🛡️ 风险提示:")
        for warn in risk_warnings:
            sections.append(f"  ⚠️ {warn}")
        sections.append("")

    # ── Footer ─────────────────────────────────────────────
    if trade.enrichment_latency_ms > 0:
        sections.append(
            f"⏱ 分析耗时: {trade.enrichment_latency_ms:.0f}ms"
        )

    return "\n".join(sections)


def format_sim_result(
    trade: EnrichedTrade,
    sim_delay: int,
    sim_price: float | None,
    slippage_pct: float | None,
    sim_success: bool,
    failure_reason: str | None = None,
) -> str:
    """Format simulation result as a follow-up notification."""
    if not sim_success:
        return (
            f"❌ 模拟失败 [{trade.whale.nickname}]\n"
            f"  市场: {trade.market_title[:40]}\n"
            f"  延迟: {sim_delay}s | 原因: {failure_reason or 'Unknown'}"
        )

    slip_emoji = "✅" if abs(slippage_pct or 0) < 1.0 else (
        "⚠️" if abs(slippage_pct or 0) < 3.0 else "🔴"
    )

    return (
        f"📋 模拟执行 [{trade.whale.nickname}] +{sim_delay}s\n"
        f"  市场: {trade.market_title[:40]}\n"
        f"  目标价: ${trade.price:.4f} → 模拟价: ${sim_price:.4f}\n"
        f"  {slip_emoji} 滑点: {slippage_pct:.2f}%"
    )


def format_batch_summary(trades: list[EnrichedTrade]) -> str:
    """Format a batch summary for multiple trades in one notification."""
    if not trades:
        return "📊 无新交易"

    lines = [f"📊 批量交易汇总 ({len(trades)} 笔)", ""]

    for t in trades[:8]:
        side_emoji = "🟢" if t.side == "BUY" else "🔴"
        usd = _format_usd(t.usd_value)
        prob = f"{t.implied_probability:.0f}%"
        title = t.market_title[:35]
        if len(t.market_title) > 35:
            title += "..."
        lines.append(
            f"{side_emoji} [{t.whale.nickname}] {t.side} {title} "
            f"@ {prob} ({usd})"
        )

    if len(trades) > 8:
        lines.append(f"\n... 还有 {len(trades) - 8} 笔交易")

    return "\n".join(lines)


# ── Helper functions ────────────────────────────────────────


def _format_usd(value: float) -> str:
    """Format USD value with appropriate suffix."""
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.2f}"


def _signal_strength(trade: EnrichedTrade) -> str:
    """Determine signal strength from trade context."""
    score = 0

    # Large trade
    if trade.usd_value > 1000:
        score += 1
    if trade.usd_value > 5000:
        score += 1

    # Adding to position
    if trade.position.is_adding:
        score += 1
    if trade.position.trade_count_recent > 2:
        score += 1

    # Whale quality
    if trade.whale.all_time_profit > 50_000:
        score += 1
    if trade.whale.win_rate > 60:
        score += 1

    # Low spread = conviction
    if 0 < trade.orderbook.spread_pct < 1.0:
        score += 1

    if score >= 5:
        return "🔥 顶级大户重仓信号 (Whale Alert)"
    if score >= 3:
        return "⚡ 大户交易信号 (Smart Money)"
    if score >= 1:
        return "📌 交易监测 (Trade Detected)"
    return "📌 交易监测 (Trade Detected)"


def _assess_risk(trade: EnrichedTrade) -> list[str]:
    """Generate risk warnings."""
    warnings = []

    # Time to close
    if trade.market.minutes_to_close is not None:
        if trade.market.minutes_to_close <= 0:
            warnings.append("该市场已结算")
        elif trade.market.minutes_to_close < 15:
            warnings.append(
                f"距离结算仅剩 {trade.market.minutes_to_close:.0f} 分钟，波动剧烈"
            )
        elif trade.market.minutes_to_close < 60:
            warnings.append(
                f"距离结算 {trade.market.minutes_to_close:.0f} 分钟"
            )

    # Low liquidity
    if trade.orderbook.spread_pct > 3.0:
        warnings.append("价差过大，跟单可能产生较大滑点")

    # Large trade vs liquidity
    if trade.orderbook.ask_depth_usd > 0:
        impact = trade.usd_value / trade.orderbook.ask_depth_usd
        if impact > 0.3:
            warnings.append(
                f"交易规模占订单簿深度 {impact * 100:.0f}%，可能影响价格"
            )

    # Extreme probability
    if trade.implied_probability > 90:
        warnings.append("隐含概率 >90%，赔率极低")
    elif trade.implied_probability < 10:
        warnings.append("隐含概率 <10%，高风险长尾事件")

    return warnings
