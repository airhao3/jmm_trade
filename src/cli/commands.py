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

            path = await export_trades_to_csv(all_trades, config.export, filename=output)
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

            s = acct_stats
            success_rate = (
                (s.total_trades - s.failed_trades) / s.total_trades * 100
                if s.total_trades > 0
                else 0
            )

            click.echo("")
            click.echo("=" * 55)
            click.echo("  POLYMARKET COPY TRADER – STATISTICS")
            click.echo("=" * 55)
            click.echo(f"  Total Trades:     {s.total_trades}")
            click.echo(f"  Open Positions:   {s.open_positions}")
            click.echo(f"  Settled:          {s.settled_trades}")
            click.echo(f"  Failed:           {s.failed_trades}")
            click.echo(f"  Success Rate:     {success_rate:.1f}%")
            click.echo(f"  Avg Slippage:     {s.avg_slippage:.2f}%")
            click.echo(f"  Avg Fee:          ${s.avg_fee:.2f}")
            click.echo("")
            click.echo("  --- PnL (settled trades only) ---")
            click.echo(f"  Total PnL:        ${s.total_pnl:+.2f}")
            click.echo(f"  Win Rate:         {s.win_rate:.1f}%")
            click.echo(f"  Invested (open):  ${s.total_investment:.2f}")
            click.echo(f"  Simulated (all):  ${s.total_simulated:.2f}")
            click.echo(f"  Best Trade:       ${s.best_trade_pnl:+.2f}")
            click.echo(f"  Worst Trade:      ${s.worst_trade_pnl:+.2f}")

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
        raise SystemExit(1) from exc


# ── Target Management ────────────────────────────────────────


@cli.command("add-target")
@click.argument("address")
@click.argument("nickname")
@click.option("--notes", default="", help="Optional notes about this target")
@click.option("--disabled", is_flag=True, help="Add target in disabled state")
def add_target(address: str, nickname: str, notes: str, disabled: bool) -> None:
    """Add a new target address to track.

    ADDRESS: Ethereum address (0x...)
    NICKNAME: Human-readable name for this target
    """
    from src.utils.target_manager import TargetManager

    manager = TargetManager()

    try:
        success = manager.add_target(
            address=address,
            nickname=nickname,
            enabled=not disabled,
            notes=notes,
        )

        if success:
            click.echo(f"✓ Added target: {nickname} ({address})")
            if disabled:
                click.echo("  Status: DISABLED (use 'enable-target' to activate)")
        else:
            click.echo(f"✗ Target already exists: {address}", err=True)
            raise SystemExit(1)

    except ValueError as e:
        click.echo(f"✗ Invalid address: {e}", err=True)
        raise SystemExit(1) from e
    except Exception as e:
        click.echo(f"✗ Failed to add target: {e}", err=True)
        raise SystemExit(1) from e


@cli.command("remove-target")
@click.argument("identifier")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation prompt")
def remove_target(identifier: str, yes: bool) -> None:
    """Remove a target address.

    IDENTIFIER: Address (0x...) or nickname
    """
    from src.utils.target_manager import TargetManager

    manager = TargetManager()

    # Get target info first
    target = manager.get_target(identifier)
    if not target:
        click.echo(f"✗ Target not found: {identifier}", err=True)
        raise SystemExit(1)

    # Confirm removal
    if not yes:
        click.echo(f"Remove target: {target['nickname']} ({target['address']})?")
        if not click.confirm("Are you sure?"):
            click.echo("Cancelled")
            return

    success = manager.remove_target(identifier)
    if success:
        click.echo(f"✓ Removed target: {target['nickname']}")
    else:
        click.echo("✗ Failed to remove target", err=True)
        raise SystemExit(1)


@cli.command("list-targets")
@click.option("--all", "show_all", is_flag=True, help="Show all targets including disabled")
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
def list_targets(show_all: bool, output_json: bool) -> None:
    """List all tracked targets."""
    import json

    from src.utils.target_manager import TargetManager

    manager = TargetManager()
    targets = manager.list_targets(enabled_only=not show_all)

    if not targets:
        click.echo("No targets configured")
        return

    if output_json:
        click.echo(json.dumps(targets, indent=2))
        return

    click.echo(f"\n{'='*80}")
    click.echo(f"  TRACKED TARGETS ({len(targets)} total)")
    click.echo(f"{'='*80}\n")

    for target in targets:
        status = "✓ ENABLED" if target.get("enabled", True) else "✗ DISABLED"
        click.echo(f"{status}  {target['nickname']}")
        click.echo(f"  Address: {target['address']}")
        if target.get("notes"):
            click.echo(f"  Notes:   {target['notes']}")
        click.echo(f"  Added:   {target.get('added_at', 'N/A')}")
        if target.get("updated_at"):
            click.echo(f"  Updated: {target['updated_at']}")
        click.echo()


@cli.command("enable-target")
@click.argument("identifier")
def enable_target(identifier: str) -> None:
    """Enable a target address.

    IDENTIFIER: Address (0x...) or nickname
    """
    from src.utils.target_manager import TargetManager

    manager = TargetManager()
    success = manager.enable_target(identifier)

    if success:
        click.echo(f"✓ Enabled target: {identifier}")
    else:
        click.echo(f"✗ Target not found: {identifier}", err=True)
        raise SystemExit(1)


@cli.command("disable-target")
@click.argument("identifier")
def disable_target(identifier: str) -> None:
    """Disable a target address.

    IDENTIFIER: Address (0x...) or nickname
    """
    from src.utils.target_manager import TargetManager

    manager = TargetManager()
    success = manager.disable_target(identifier)

    if success:
        click.echo(f"✓ Disabled target: {identifier}")
    else:
        click.echo(f"✗ Target not found: {identifier}", err=True)
        raise SystemExit(1)


@cli.command("update-target")
@click.argument("identifier")
@click.option("--nickname", help="New nickname")
@click.option("--notes", help="New notes")
def update_target(identifier: str, nickname: str | None, notes: str | None) -> None:
    """Update target information.

    IDENTIFIER: Address (0x...) or current nickname
    """
    from src.utils.target_manager import TargetManager

    if not nickname and not notes:
        click.echo("✗ Must specify --nickname or --notes", err=True)
        raise SystemExit(1)

    manager = TargetManager()
    success = manager.update_target(identifier, nickname=nickname, notes=notes)

    if success:
        click.echo(f"✓ Updated target: {identifier}")
    else:
        click.echo(f"✗ Target not found: {identifier}", err=True)
        raise SystemExit(1)
