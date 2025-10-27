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
push-to-assets.py
Push a file to an isolated 'assets' branch without affecting your working tree.
"""

import os
import subprocess
import tempfile
import shlex
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint

app = typer.Typer(
    help="Push files to an isolated 'assets' branch without touching your working tree.",
    add_completion=False,
)
console = Console()


def _run(
    cmd: str,
    check: bool = True,
    capture_output: bool = True,
    input_text: str | None = None,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    # Parse the command string into a list
    cmd_list = shlex.split(cmd)

    result = subprocess.run(
        cmd_list,
        capture_output=capture_output,
        text=True,
        check=check,
        input=input_text,
        env=env,
    )
    return result


def get_parent_commit(branch: str) -> str | None:
    """Get the current commit SHA of the remote branch."""
    result = _run(f"git ls-remote --heads origin {shlex.quote(branch)}", check=False)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.split()[0]
    return None


def get_repo_root() -> Path:
    """Get the repository root directory."""
    result = _run("git rev-parse --show-toplevel")
    return Path(result.stdout.strip())


@app.command()
def push(
    file_path: Path = typer.Argument(
        ...,
        help="Path to the file you want to push to the assets branch",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
    ),
    branch: str = typer.Option("assets", "--branch", "-b", help="Target branch name"),
    dest_dir: str = typer.Option(
        "assets", "--dest-dir", "-d", help="Destination directory in the branch"
    ),
):
    """Push a file to an isolated branch without affecting your working tree."""

    filename = file_path.name
    dest = f"{dest_dir}/{filename}"

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        # Get repository root
        task = progress.add_task("Finding repository root...", total=None)
        try:
            repo_root = get_repo_root()
            os.chdir(repo_root)
        except subprocess.CalledProcessError:
            console.print("[red]Error:[/red] Not in a git repository")
            raise typer.Exit(1)
        progress.update(task, completed=True)

        # Create temporary index file
        task = progress.add_task("Setting up temporary index...", total=None)
        with tempfile.NamedTemporaryFile(delete=False) as tmp_index:
            tmp_index_path = tmp_index.name
        progress.update(task, completed=True)

        try:
            # Set GIT_INDEX_FILE environment variable
            env = os.environ.copy()
            env["GIT_INDEX_FILE"] = tmp_index_path

            # Get the current commit of the assets branch
            task = progress.add_task(f"Checking branch '{branch}'...", total=None)
            parent_commit = get_parent_commit(branch)
            progress.update(task, completed=True)

            # Populate the temp index
            task = progress.add_task("Loading branch state...", total=None)
            if parent_commit:
                _run(f"git read-tree {shlex.quote(parent_commit)}", env=env)
            else:
                _run("git read-tree --empty", env=env)
                console.print(
                    f"[yellow]Note:[/yellow] Branch '{branch}' doesn't exist yet - will create it"
                )
            progress.update(task, completed=True)

            # Hash the file object
            task = progress.add_task("Hashing file content...", total=None)
            result = _run(f"git hash-object -w {shlex.quote(str(file_path))}")
            blob = result.stdout.strip()
            progress.update(task, completed=True)

            # Update the index
            task = progress.add_task(f"Adding {filename} to index...", total=None)
            _run(
                f"git update-index --add --cacheinfo 100644 {shlex.quote(blob)} {shlex.quote(dest)}",
                env=env,
            )
            progress.update(task, completed=True)

            # Write the new tree
            task = progress.add_task("Writing tree object...", total=None)
            result = _run("git write-tree", env=env)
            new_tree = result.stdout.strip()
            progress.update(task, completed=True)

            # Check if anything changed
            if parent_commit:
                result = _run(f"git show -s --format=%T {shlex.quote(parent_commit)}")
                parent_tree = result.stdout.strip()
                if new_tree == parent_tree:
                    console.print(
                        f"[yellow]Nothing to commit[/yellow] - '{dest}' already up-to-date"
                    )
                    raise typer.Exit(0)

            # Create a commit
            task = progress.add_task("Creating commit...", total=None)
            commit_message = f"Update {dest}" if parent_commit else f"Add {dest}"

            if parent_commit:
                result = _run(
                    f"git commit-tree {shlex.quote(new_tree)} -p {shlex.quote(parent_commit)}",
                    input_text=commit_message,
                    env=env,
                )
            else:
                result = _run(
                    f"git commit-tree {shlex.quote(new_tree)}",
                    input_text=commit_message,
                    env=env,
                )
            commit = result.stdout.strip()
            progress.update(task, completed=True)

            # Push the commit
            task = progress.add_task(f"Pushing to origin/{branch}...", total=None)
            _run(
                f"git push origin {shlex.quote(commit)}:refs/heads/{shlex.quote(branch)}"
            )
            progress.update(task, completed=True)

        except subprocess.CalledProcessError as e:
            console.print(f"[red]Error:[/red] Command failed: {e}")
            raise typer.Exit(1)

        finally:
            # Clean up temporary index file
            if os.path.exists(tmp_index_path):
                os.unlink(tmp_index_path)

    # Success message
    rprint(
        Panel.fit(
            f"[green]✅ Successfully pushed/updated[/green] '[cyan]{dest}[/cyan]' on branch '[yellow]{branch}[/yellow]'",
            title="Success",
            border_style="green",
        )
    )


@app.command()
def list_assets(
    branch: str = typer.Option(
        "assets", "--branch", "-b", help="Branch name to list files from"
    ),
):
    """List all files in the assets branch."""
    parent_commit = get_parent_commit(branch)

    if not parent_commit:
        console.print(f"[yellow]Branch '{branch}' doesn't exist yet[/yellow]")
        raise typer.Exit(0)

    try:
        result = _run(f"git ls-tree -r --name-only {shlex.quote(parent_commit)}")
        files = result.stdout.strip().split("\n") if result.stdout.strip() else []

        if files:
            console.print(
                Panel.fit(
                    "\n".join(f"[cyan]{f}[/cyan]" for f in sorted(files)),
                    title=f"Files in '{branch}' branch",
                    border_style="blue",
                )
            )
        else:
            console.print(f"[yellow]No files in '{branch}' branch[/yellow]")

    except subprocess.CalledProcessError as e:
        console.print(f"[red]Error:[/red] Failed to list files: {e}")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
