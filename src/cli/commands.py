"""CLI commands powered by Click."""

from __future__ import annotations

import asyncio

import click
from loguru import logger


@click.group()
@click.option(
    "--config",
    "config_path",
    default="config/config.yaml",
    show_default=True,
    help="Path to config.yaml",
)
@click.pass_context
def cli(ctx: click.Context, config_path: str) -> None:
    """Polymarket Copy Trading Simulator."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


# ── run ──────────────────────────────────────────────────

@cli.command()
@click.option(
    "--mode",
    type=click.Choice(["poll", "ws"], case_sensitive=False),
    default=None,
    help="Override monitoring mode (poll or ws).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Run without sending notifications.",
)
@click.pass_context
def run(ctx: click.Context, mode: str | None, dry_run: bool) -> None:
    """Start the monitoring and simulation loop."""
    from src.config.loader import load_config
    from src.config.models import MonitorMode
    from src.utils.logger import setup_logger

    config = load_config(ctx.obj["config_path"])
    setup_logger(config.logging)

    if mode:
        config.monitoring.mode = MonitorMode(mode)
    if dry_run:
        config.notifications.enabled = False
        logger.info("Dry-run mode: notifications disabled")

    logger.info(
        f"Starting: mode={config.monitoring.mode.value}, "
        f"targets={len(config.get_active_targets())}, "
        f"investment=${config.simulation.investment_per_trade}"
    )

    from src.core.app import Application

    app = Application(config)
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user – exiting")


# ── export ───────────────────────────────────────────────

@cli.command()
@click.option(
    "--output",
    default=None,
    help="Output CSV filename (default: auto-generated in data/exports/).",
)
@click.pass_context
def export(ctx: click.Context, output: str | None) -> None:
    """Export simulated trades to CSV."""
    from src.config.loader import load_config
    from src.utils.logger import setup_logger

    config = load_config(ctx.obj["config_path"])
    setup_logger(config.logging)

    async def _export() -> None:
        from src.data.database import Database
        from src.data.export import export_trades_to_csv

        db = Database(config.database)
        await db.connect()
        try:
            all_trades = await db.get_all_trades()

            if not all_trades:
                click.echo("No trades to export.")
                return

            path = await export_trades_to_csv(
                all_trades, config.export, filename=output
            )
            click.echo(f"Exported {len(all_trades)} trades -> {path}")
        finally:
            await db.close()

    asyncio.run(_export())


# ── stats ────────────────────────────────────────────────

@cli.command()
@click.option(
    "--target",
    default=None,
    help="Filter by target address or nickname.",
)
@click.pass_context
def stats(ctx: click.Context, target: str | None) -> None:
    """Show trading statistics."""
    from src.config.loader import load_config
    from src.utils.logger import setup_logger

    config = load_config(ctx.obj["config_path"])
    setup_logger(config.logging)

    async def _stats() -> None:
        from src.data.database import Database

        db = Database(config.database)
        await db.connect()
        try:
            acct_stats = await db.get_statistics()
            summary = await db.get_pnl_summary(target)

            click.echo("")
            click.echo("=" * 50)
            click.echo("  POLYMARKET COPY TRADER – STATISTICS")
            click.echo("=" * 50)
            click.echo(f"  Total Trades:     {acct_stats.total_trades}")
            click.echo(f"  Open Positions:   {acct_stats.open_positions}")
            click.echo(f"  Settled:          {acct_stats.settled_trades}")
            click.echo(f"  Failed:           {acct_stats.failed_trades}")
            click.echo(f"  Total PnL:        ${acct_stats.total_pnl:+.2f}")
            click.echo(f"  Win Rate:         {acct_stats.win_rate:.1f}%")
            click.echo(f"  Avg Slippage:     {acct_stats.avg_slippage:.2f}%")
            click.echo(f"  Avg Fee:          ${acct_stats.avg_fee:.2f}")
            click.echo(f"  Total Invested:   ${acct_stats.total_investment:.2f}")
            click.echo(f"  Best Trade:       ${acct_stats.best_trade_pnl:+.2f}")
            click.echo(f"  Worst Trade:      ${acct_stats.worst_trade_pnl:+.2f}")

            if summary:
                click.echo("")
                click.echo("  --- PnL by Target & Delay ---")
                for row in summary:
                    click.echo(
                        f"  {row['target_nickname']} (delay={row['sim_delay']}s): "
                        f"{row['trade_count']} trades | "
                        f"PnL ${row['total_pnl']:+.2f} | "
                        f"Win {row['win_rate']:.0f}% | "
                        f"Slip {row['avg_slippage']:.2f}%"
                    )

            click.echo("=" * 50)
        finally:
            await db.close()

    asyncio.run(_stats())


# ── check-config ─────────────────────────────────────────

@cli.command("check-config")
@click.pass_context
def check_config(ctx: click.Context) -> None:
    """Validate configuration without starting the bot."""
    try:
        from src.config.loader import load_config

        config = load_config(ctx.obj["config_path"])
        click.echo("Config is valid!")
        click.echo(f"  Active targets: {len(config.get_active_targets())}")
        click.echo(f"  Mode:           {config.monitoring.mode.value}")
        click.echo(f"  Investment:     ${config.simulation.investment_per_trade}")
        click.echo(f"  Fee rate:       {config.simulation.fee_rate * 100:.1f}%")
        click.echo(f"  Delays:         {config.simulation.delays}")
        click.echo(f"  Read-only:      {config.system.read_only_mode}")

        for t in config.get_active_targets():
            click.echo(f"  Target: {t.nickname} ({t.address[:10]}...)")

    except Exception as exc:
        click.echo(f"Config validation FAILED: {exc}", err=True)
        raise SystemExit(1)
