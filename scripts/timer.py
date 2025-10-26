#!/usr/bin/env -S uv run --script
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "rich",
#     "typer",
#     "plyer",
# ]
# ///

from __future__ import annotations

from datetime import datetime, timedelta
from time import sleep, monotonic
from typing import Optional

import typer
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


def _format_mmss(total_seconds: int) -> str:
    minutes, seconds = divmod(max(0, total_seconds), 60)
    return f"{minutes:02d}:{seconds:02d}"


@app.command()
def timer(
    minutes: float = typer.Argument(
        ..., min=0.01, help="Duration in minutes (can be fractional)."
    ),
    message: Optional[str] = typer.Option(
        None, "--message", "-m", help="Custom message when the timer finishes."
    ),
    beep: bool = typer.Option(
        False, "--beep/--no-beep", help="Play a terminal bell when done."
    ),
    emoji: str = typer.Option("⏳", "--emoji", help="Emoji to display in the header."),
    refresh: float = typer.Option(
        0.1, "--refresh", help="UI refresh interval in seconds."
    ),
):
    """Start a timer with a live Rich UI

    Examples:
      uv run timer.py 5
      uv run timer.py 1.5 -m "Tea is ready" --beep
    """

    total_seconds = int(minutes * 60)
    if total_seconds <= 0:
        console.print("Please provide a positive number of minutes.", style="bold red")
        raise typer.Exit(code=1)

    end_time = datetime.now() + timedelta(seconds=total_seconds)

    header = Panel(
        Text.from_markup(
            f"[bold green]{emoji} Timer started[/bold green]\n"
            f"[dim]Duration:[/] {minutes:g} minute(s)  •  [dim]Ends at:[/] {end_time:%H:%M:%S}"
        ),
        title="Rich Timer",
        border_style="green",
        box=box.ROUNDED,
    )

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold][{task.description}]"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        expand=True,
        transient=False,
    )

    task = progress.add_task("Counting down", total=total_seconds)

    def _render_body(remaining: int) -> Panel:
        table = Table.grid(expand=True)
        table.add_column(justify="center")
        big_time = Align.center(
            Text(_format_mmss(remaining), style="bold magenta", justify="center"),
            vertical="middle",
        )
        table.add_row(big_time)
        table.add_row(
            Text.from_markup(
                f"[dim]ETA:[/] {end_time:%I:%M:%S %p}  •  [dim]Seconds left:[/] {remaining}"
            )
        )
        return Panel(
            table, border_style="magenta", title="Time Remaining", box=box.ROUNDED
        )

    console.print(Rule(style="green"))
    console.print(header)
    console.print(Rule(style="green"))

    try:
        # Use Live to update the same renderable instead of printing per tick.
        fps = max(1, int(1 / max(0.01, refresh)))
        start = monotonic()
        with Live(
            Group(_render_body(total_seconds), progress),
            refresh_per_second=fps,
            console=console,
            transient=False,
        ) as live:
            while True:
                elapsed = int(monotonic() - start)
                remaining = max(0, total_seconds - elapsed)

                progress.update(task, completed=elapsed)
                live.update(Group(_render_body(remaining), progress))

                if remaining <= 0:
                    break
                sleep(min(refresh, 0.25))

        done_text = message or "Your timer has finished!"
        finish_panel = Panel(
            Text.from_markup(f"🎉 [bold yellow]Time's up![/bold yellow]\n{done_text}"),
            title="⏰ Ding!",
            border_style="yellow",
            box=box.HEAVY,
        )
        console.print("\n")
        console.print(finish_panel)
        console.print(Rule(style="yellow"))

        if beep:
            # Terminal bell (may not work in all terminals)
            console.bell()

    except KeyboardInterrupt:
        console.print(
            Panel(
                Text.from_markup("🛑 [bold red]Timer cancelled by user[/bold red]"),
                title="Cancelled",
                border_style="red",
                box=box.ROUNDED,
            )
        )
        raise typer.Exit(code=130)


if __name__ == "__main__":
    app()
