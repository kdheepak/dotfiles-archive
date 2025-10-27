#!/usr/bin/env -S uv --quiet run --script
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.13"
# dependencies = ["typer", "rich", "pygments", "pyyaml"]
# ///

import os
import shutil
import subprocess
import typer
import json
import yaml
import mimetypes
from pathlib import Path
from rich.console import Console
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.pretty import pprint

app = typer.Typer()
console = Console()


def command_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=False)


def mime_type(path: Path) -> str:
    # 1. Try to detect from extension
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        return mime

    # 2. Fallback to 'file' command
    try:
        result = subprocess.run(
            ["file", "--mime-type", "-b", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return "application/octet-stream"


def preview_git_file(filename: str):
    try:
        run(["git", "show", f":{filename}"])
        return
    except Exception:
        pass
    try:
        commit = subprocess.run(
            ["git", "log", "--format=%H", "--", filename],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()[0]
        run(["git", "show", f"{commit}:{filename}"])
    except Exception:
        console.print(
            f"[red]File not found in working tree or Git history: {filename}[/red]"
        )


@app.command()
def preview(filename: str):
    """Preview a file or directory with nice fallbacks."""
    path = Path(filename)
    fzf_cols = int(os.environ.get("FZF_PREVIEW_COLUMNS", "80"))

    # Fallback if file doesn’t exist
    if not path.exists():
        preview_git_file(filename)
        raise typer.Exit()

    mt = mime_type(path)

    # Directory preview
    if mt == "inode/directory":
        if command_exists("eza"):
            run(
                [
                    "eza",
                    "--git",
                    "-lha",
                    "--group-directories-first",
                    "--no-user",
                    "--color=always",
                    str(path),
                ]
            )
        else:
            run(["ls", "-lah", str(path)])
        raise typer.Exit()

    # Markdown
    if mt == "text/markdown":
        if command_exists("glow"):
            run(["glow", "-p", str(path)])
        elif command_exists("bat"):
            run(
                [
                    "bat",
                    "--color=always",
                    "--language=markdown",
                    "--terminal-width",
                    str(fzf_cols),
                    str(path),
                ]
            )
        else:
            console.print(Markdown(path.read_text()))
        raise typer.Exit()

    # JSON
    if mt == "application/json":
        if command_exists("jq"):
            run(["jq", ".", str(path)])
        else:
            try:
                data = json.loads(path.read_text())
                pprint(data, expand_all=True)
            except Exception:
                console.print(path.read_text())
        raise typer.Exit()

    # YAML
    if mt in ("text/yaml", "application/x-yaml"):
        if command_exists("yq"):
            run(["yq", ".", str(path)])
        else:
            try:
                data = yaml.safe_load(path.read_text())
                pprint(data, expand_all=True)
            except Exception:
                console.print(path.read_text())
        raise typer.Exit()

    # Images
    if mt.startswith("image/"):
        if command_exists("viu"):
            run(["viu", str(path)])
        elif command_exists("catimg"):
            run(["catimg", "-w", str(fzf_cols - 2), str(path)])
        else:
            console.print(f"[blue]Image file:[/blue] {filename}")
        raise typer.Exit()

    # Archives
    if mt in (
        "application/zip",
        "application/x-tar",
        "application/x-gzip",
        "application/x-bzip2",
        "application/x-xz",
        "application/x-7z-compressed",
    ):
        if command_exists("ouch"):
            run(["ouch", "list", str(path)])
        else:
            console.print(
                f"[yellow]Archive file:[/yellow] {filename} (no archive lister found)"
            )
        raise typer.Exit()

    # Default (source/text)
    if command_exists("bat"):
        run(["bat", "--color=always", "--terminal-width", str(fzf_cols), str(path)])
    else:
        try:
            syntax = Syntax(
                path.read_text(),
                path.suffix.lstrip("."),
                theme="monokai",
                line_numbers=True,
            )
            console.print(syntax)
        except Exception:
            console.print(path.read_text())


if __name__ == "__main__":
    app()
