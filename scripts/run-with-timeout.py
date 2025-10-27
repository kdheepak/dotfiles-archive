#!/usr/bin/env -S uv --quiet run --script
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "rich",
#     "typer",
# ]
# ///
"""
Runs a command with a timeout and redacts sensitive paths/usernames from output.
Usage:
./run-with-timeout.py 10 -- <your command>
"""

import subprocess
import os
import re
import time
import threading
import signal
from typing import List, Optional

import typer
from rich.console import Console
from rich.live import Live
from rich.text import Text
from rich.console import Group
from rich.progress import (
    Progress,
    SpinnerColumn,
    BarColumn,
    TimeRemainingColumn,
)

console = Console()
app = typer.Typer(
    help="Run commands with timeout and output redaction",
    add_completion=False,
    no_args_is_help=True,
)


def sanitize(text: str) -> str:
    """Redact likely sensitive values from text output."""
    username = (
        os.getenv("USER")
        or os.getenv("USERNAME")
        or os.path.basename(os.path.expanduser("~"))
    )
    patterns = [
        username,
        os.path.expanduser("~"),
        re.escape(os.path.expanduser("~")),
        r"/Users/[^/]+",  # Mac home directory path
        r"/home/[^/]+",  # Linux home directory path
        r"C:\\Users\\[^\\]+",  # Windows home directory path
    ]
    for pattern in patterns:
        text = re.sub(pattern, "[REDACTED]", text, flags=re.IGNORECASE)
    return text


class TimeoutError(Exception):
    """Raised when command times out."""

    pass


def run_and_capture(command: list[str], timeout_seconds: Optional[float] = None):
    """Run a command with optional timeout, streaming output in real-time."""

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True,
        preexec_fn=os.setsid if os.name != "nt" else None,
    )

    output_lines = []

    # Create the progress bar (same as your original)
    progress = Progress(
        SpinnerColumn(),
        BarColumn(bar_width=None),
        TimeRemainingColumn(),
        transient=True,
        expand=True,
    )

    def create_progress_display(elapsed_time: float):
        """Create multi-line progress display."""
        lines = []

        # Command line
        cmd_display = f"[bold green]Running:[/] [code]`{' '.join(command)}`[/code]"
        lines.append(Text.from_markup(cmd_display))

        # Additional info line
        if timeout_seconds:
            info_line = f"[bold yellow]Timeout:[/] {timeout_seconds}s"
            lines.append(Text.from_markup(info_line))

        # Add the actual Rich progress bar
        lines.append(progress)

        return Group(*lines)

    def read_output():
        """Read output in a separate thread."""
        try:
            for line in process.stdout:
                sanitized_line = sanitize(line.rstrip())
                # Print output below the live display
                console.print(sanitized_line, highlight=False, markup=False)
                output_lines.append(line)
        except ValueError:
            pass

    # Start reading thread
    reader_thread = threading.Thread(target=read_output, daemon=True)
    reader_thread.start()

    start_time = time.time()

    # Set up the progress task
    task_desc = f"[code]`{' '.join(command)}`[/]"
    if timeout_seconds:
        task_desc += f" ([bold yellow]timeout[/]: {timeout_seconds} seconds)"

    task = progress.add_task(task_desc, total=timeout_seconds or 1)

    try:
        with Live(
            create_progress_display(0),
            console=console,
            refresh_per_second=4,
            transient=True,  # Remove when done, like Progress
        ) as live:
            # Update loop
            while process.poll() is None:
                elapsed = time.time() - start_time

                # Update the progress bar
                if timeout_seconds:
                    progress.update(task, completed=elapsed)
                else:
                    # For no timeout, just update to show elapsed time
                    progress.update(task, completed=elapsed)

                # Update the live display
                live.update(create_progress_display(elapsed))

                if timeout_seconds and elapsed >= timeout_seconds:
                    raise subprocess.TimeoutExpired(command, timeout_seconds)

                time.sleep(0.25)  # Update every 250ms

            # Final update
            elapsed = time.time() - start_time
            progress.update(task, completed=elapsed)
            live.update(create_progress_display(elapsed))

        return_code = process.returncode

        # Wait for reader thread to finish
        reader_thread.join(timeout=1.0)

        if return_code != 0:
            raise RuntimeError(f"Command failed with exit code {return_code}")

    except subprocess.TimeoutExpired:
        # Terminate process (same logic as before)
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            else:
                process.terminate()

            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
        except (ProcessLookupError, OSError):
            pass

        raise TimeoutError(f"Command timed out after {timeout_seconds} seconds")

    finally:
        # Stop the progress bar
        progress.stop()

        if process.stdout and not process.stdout.closed:
            process.stdout.close()


@app.command()
def main(
    timeout: float = typer.Argument(..., help="Timeout in seconds"),
    command: List[str] = typer.Argument(..., help="Command to run (after --)"),
):
    """
    Run a command with a timeout and redact sensitive information from output.

    Example:
        ./run-with-timeout.py 10 -- ls -la
        ./run-with-timeout.py 30 -- python my_script.py
    """
    if not command:
        console.print("[bold red]Error:[/] No command provided")
        raise typer.Exit(1)

    try:
        run_and_capture(command, timeout)

        console.print("")
        console.print("[bold green]Command completed successfully![/]")

    except TimeoutError as e:
        console.print(f"\n[bold red]Error:[/] {e}")
        raise typer.Exit(124)  # Timeout exit code

    except RuntimeError as e:
        console.print(f"\n[bold red]Error:[/] {e}")
        raise typer.Exit(1)

    except KeyboardInterrupt:
        console.print("\n[bold yellow]Interrupted by user[/]")
        raise typer.Exit(130)

    except Exception as e:
        console.print(f"\n[bold red]Unexpected error:[/] {e}")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
