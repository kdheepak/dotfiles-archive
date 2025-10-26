#!/usr/bin/env -S uv run
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pyperclip",
#     "rich",
# ]
# ///

import getpass
import pyperclip
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()


def anonymize_username(content: str, username: str) -> str:
    return content.replace(username, "XXXXXX")


def main() -> None:
    username = getpass.getuser()
    content = pyperclip.paste() or ""

    if not content:
        console.print("[yellow]Clipboard is empty. Nothing to anonymize.[/yellow]")
        return

    anonymized = anonymize_username(content, username)
    pyperclip.copy(anonymized)

    # Confirmation panel
    console.print(
        Panel(
            Text(
                "Clipboard text has been anonymized and copied back to the clipboard.",
                style="bold",
            ),
            title=Text(" Anonymized & Copied ", style="bold white on green"),
            border_style="green",
            expand=False,
        )
    )

    # Print the full anonymized content (no truncation, no Rich markup)
    console.rule("[dim]Output[/dim]")
    console.print(
        Text(anonymized, no_wrap=False, overflow="fold"), markup=False, soft_wrap=True
    )


if __name__ == "__main__":
    main()
