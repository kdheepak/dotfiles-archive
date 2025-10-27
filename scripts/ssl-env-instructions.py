#!/usr/bin/env -S uv run --script
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.9"
# dependencies = ["typer>=0.12", "rich>=13.7"]
# ///


from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Dict, Iterable, List, Union

import typer
from rich.console import Console, Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

app = typer.Typer(
    add_completion=False, help="Generate env var setup commands for SSL certificates."
)
console = Console()


def _build_env_vars(cert_path: Path) -> Dict[str, Union[str, int]]:
    cert_path = cert_path.expanduser()
    cert_dir = cert_path.parent
    cert_path_native = os.path.normpath(str(cert_path))
    cert_dir_native = os.path.normpath(str(cert_dir))

    return {
        "CURL_CA_BUNDLE": cert_path_native,
        "REQUESTS_CA_BUNDLE": cert_path_native,
        "SSL_CERT_FILE": cert_path_native,
        "SSL_CERT_DIR": cert_dir_native,
        "PYTHONHTTPSVERIFY": 1,
        "SSL_VERIFY": "true",
    }


def _print_current_env_vars(env_keys: Iterable[str]) -> None:
    table = Table(title="Current Environment Variables", expand=True)
    table.add_column("Variable", style="cyan", no_wrap=True)
    table.add_column("Current Value", style="white")

    for key in env_keys:
        value = os.environ.get(key)
        if value is None:
            table.add_row(key, Text("(not set)", style="red"))
        else:
            display = value if len(value) <= 45 else value[:42] + "..."
            table.add_row(key, display)

    console.print(table)
    console.print()


def _section(
    title: str, items: List[Union[str, Text, Syntax]], subtitle: str | None = None
) -> None:
    """
    Render a titled panel with a stack of Rich renderables (strings/Text/Syntax).
    """
    group = Group(*items)
    console.print(Panel.fit(group, title=title, subtitle=subtitle))


def _windows_commands(env_vars: Dict[str, Union[str, int]]) -> str:
    lines = []
    for var, val in env_vars.items():
        if isinstance(val, str) and not val.isdigit():
            lines.append(f'setx {var} "{val}"')
        else:
            lines.append(f"setx {var} {val}")
    return "\n".join(lines)


def _bash_commands(env_vars: Dict[str, Union[str, int]]) -> str:
    lines = []
    for var, val in env_vars.items():
        if isinstance(val, str) and not val.isdigit():
            lines.append(f'export {var}="{val}"')
        else:
            lines.append(f"export {var}={val}")
    return "\n".join(lines)


def _show_instructions(env_vars: Dict[str, Union[str, int]], show_all: bool) -> None:
    sysname = platform.system()

    console.print(
        Text(
            f"NOTE: Using certificate path: {env_vars['SSL_CERT_FILE']}", style="yellow"
        )
    )
    console.print()

    _print_current_env_vars(env_vars.keys())

    want_windows = show_all or sysname == "Windows"
    want_posix = show_all or sysname in {"Linux", "Darwin"}

    if want_windows:
        code = _windows_commands(env_vars)
        _section(
            "Windows Setup (Command Prompt)",
            [
                "[green]Run the following commands in Command Prompt:[/green]",
                "",
                Syntax(code, "batch", word_wrap=True),
            ],
            subtitle="Persistent via setx",
        )

    if want_posix:
        code = _bash_commands(env_vars)
        _section(
            "macOS/Linux Setup (bash/zsh)",
            [
                "[green]Add the following lines to your shell config file "
                "(`~/.bashrc`, `~/.zshrc`, etc.):[/green]",
                "",
                Syntax(code, "bash", word_wrap=True),
                "",
                "[cyan]Then restart your terminal to apply changes.[/cyan]",
            ],
        )

    if not show_all and not (want_windows or want_posix):
        console.print(
            Text(f"Unsupported operating system: {sysname}", style="bold red")
        )
        raise typer.Exit(code=1)


@app.command()
def main(
    cert_path: Path = typer.Argument(
        Path("~/.config/certs/cacert.pem"),
        help="Path to the certificate file.",
    ),
    show_all: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Show setup instructions for all supported operating systems.",
    ),
) -> None:
    """
    Generate environment variable setup commands for SSL certificates and show
    current values in a table.
    """
    env_vars = _build_env_vars(cert_path)
    _show_instructions(env_vars, show_all)


if __name__ == "__main__":
    app()
