#!/usr/bin/env -S uv run --script
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "httpx",
#     "rich",
#     "typer",
# ]
# ///
"""
Nextword Data Downloader
Downloads and extracts nextword-data from GitHub with progress tracking.
"""

import shutil
import tarfile
import tempfile
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

app = typer.Typer(
    name=__name__, no_args_is_help=True, help=__doc__, add_completion=False
)

console = Console()

# Configuration
DATA_URL = "https://github.com/high-moctane/nextword-data/archive/large.tar.gz"
DEFAULT_DIR = Path.home() / "local" / "dictionary"
EXPECTED_SUBDIR = "nextword-data-large"


def format_size(bytes: float) -> str:
    """Format bytes as human readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes < 1024:
            return f"{bytes:.1f} {unit}"
        bytes /= 1024
    return f"{bytes:.1f} TB"


def check_existing_data(dict_dir: Path) -> bool:
    """Check if nextword data already exists and is not empty."""
    expected_path = dict_dir / EXPECTED_SUBDIR
    return expected_path.exists() and any(expected_path.iterdir())


def extract_tar_data(tar_data: bytes, extract_dir: Path) -> None:
    """Extract tar.gz data to directory."""
    with tempfile.NamedTemporaryFile() as tmp_file:
        tmp_file.write(tar_data)
        tmp_file.flush()

        with tarfile.open(tmp_file.name, "r:gz") as tar:
            # Use data_filter for security (Python 3.12+) or fallback to no filter
            try:
                tar.extractall(extract_dir, filter="data")
            except TypeError:
                # Fallback for older Python versions that don't support filter parameter
                tar.extractall(extract_dir)


@app.command()
def download(
    directory: Path | None = typer.Option(
        None, "--dir", "-d", help="Directory to download data to", metavar="PATH"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force download even if data exists"
    ),
) -> None:
    """Download nextword data for dictionary applications."""

    dict_dir = directory or DEFAULT_DIR
    expected_path = dict_dir / EXPECTED_SUBDIR

    # Check if data already exists
    if not force and check_existing_data(dict_dir):
        console.print(
            f"✅ [green]Nextword data already exists at[/green] [blue]{expected_path}[/blue]"
        )
        console.print(
            "💡 [yellow]Use[/yellow] [code]--force[/code] [yellow]to re-download[/yellow]"
        )
        return

    console.print("📥 [blue]Downloading nextword data...[/blue]")
    console.print(f"📍 [dim]Target directory: {dict_dir}[/dim]")

    try:
        # Create directory
        dict_dir.mkdir(parents=True, exist_ok=True)

        # Clean existing data if force mode
        if force and expected_path.exists():
            console.print("🗑️  [yellow]Removing existing data...[/yellow]")
            shutil.rmtree(expected_path)

        # Download with progress bar - we'll update columns after getting response
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task("Connecting...", total=None)

            try:
                # First, check if we can get the total size
                with httpx.stream("GET", DATA_URL, follow_redirects=True) as response:
                    response.raise_for_status()
                    total_size = int(response.headers.get("content-length", 0)) or None

                    # Now configure progress bar based on whether we have total size
                    progress.stop()

                    if total_size:
                        # We have size - show full progress bar
                        progress_columns = [
                            TextColumn("[progress.description]{task.description}"),
                            BarColumn(),
                            "[progress.percentage]{task.percentage:>3.1f}%",
                            "•",
                            DownloadColumn(),
                            "•",
                            TransferSpeedColumn(),
                            "•",
                            TimeRemainingColumn(),
                        ]
                    else:
                        # No size - show simpler progress without percentage/time
                        progress_columns = [
                            TextColumn("[progress.description]{task.description}"),
                            "•",
                            DownloadColumn(),
                            "•",
                            TransferSpeedColumn(),
                        ]

                    # Restart progress with appropriate columns
                    with Progress(
                        *progress_columns, console=console, transient=True
                    ) as sized_progress:
                        download_task = sized_progress.add_task(
                            "Downloading..."
                            if total_size
                            else "Downloading (size unknown)...",
                            total=total_size,
                        )

                        data = bytearray()
                        for chunk in response.iter_bytes(chunk_size=8192):
                            data.extend(chunk)
                            sized_progress.update(download_task, advance=len(chunk))

                tar_data = bytes(data)
            except httpx.RequestError as e:
                console.print(f"❌ [red]Download failed:[/red] {e}")
                raise typer.Exit(1)

        # Extract data
        console.print("📦 [blue]Extracting data...[/blue]")
        try:
            extract_tar_data(tar_data, dict_dir)
        except (tarfile.TarError, OSError) as e:
            console.print(f"❌ [red]Extraction failed:[/red] {e}")
            # Cleanup on failure
            if expected_path.exists():
                shutil.rmtree(expected_path)
            raise typer.Exit(1)

        # Verify extraction
        if not check_existing_data(dict_dir):
            console.print(
                "❌ [red]Extraction completed but data directory is missing or empty[/red]"
            )
            console.print(f"   [dim]Expected: {expected_path}[/dim]")
            if expected_path.exists():
                shutil.rmtree(expected_path)
            raise typer.Exit(1)

        # Success!
        data_size = sum(
            f.stat().st_size for f in expected_path.rglob("*") if f.is_file()
        )
        console.print(
            "✅ [green]Successfully downloaded and extracted nextword data![/green]"
        )
        console.print(f"📂 [blue]Location:[/blue] {expected_path}")
        console.print(f"📊 [blue]Size:[/blue] {format_size(data_size)}")

    except KeyboardInterrupt:
        console.print("\n⚠️  [yellow]Download interrupted by user[/yellow]")
        # Cleanup partial download
        if expected_path.exists():
            console.print("🧹 [dim]Cleaning up partial download...[/dim]")
            shutil.rmtree(expected_path)
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"❌ [red]Unexpected error:[/red] {e}")
        # Cleanup on any error
        if expected_path.exists():
            shutil.rmtree(expected_path)
        raise typer.Exit(1)


@app.command()
def status(
    directory: Path | None = typer.Option(
        None, "--dir", "-d", help="Directory to check for data", metavar="PATH"
    ),
) -> None:
    """Check status of nextword data installation."""

    dict_dir = directory or DEFAULT_DIR
    expected_path = dict_dir / EXPECTED_SUBDIR

    if check_existing_data(dict_dir):
        data_size = sum(
            f.stat().st_size for f in expected_path.rglob("*") if f.is_file()
        )
        file_count = len(list(expected_path.rglob("*")))

        console.print("✅ [green]Nextword data is installed[/green]")
        console.print(f"📂 [blue]Location:[/blue] {expected_path}")
        console.print(f"📊 [blue]Size:[/blue] {format_size(data_size)}")
        console.print(f"📄 [blue]Files:[/blue] {file_count}")
    else:
        console.print("❌ [red]Nextword data not found[/red]")
        console.print(f"📍 [dim]Expected location: {expected_path}[/dim]")
        console.print(
            f"💡 [yellow]Run[/yellow] [code]python {Path(__file__).name} download[/code] [yellow]to install[/yellow]"
        )


@app.command()
def clean(
    directory: Path | None = typer.Option(
        None, "--dir", "-d", help="Directory to clean data from", metavar="PATH"
    ),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Remove nextword data."""

    dict_dir = directory or DEFAULT_DIR
    expected_path = dict_dir / EXPECTED_SUBDIR

    if not check_existing_data(dict_dir):
        console.print("ℹ️  [blue]No nextword data found to clean[/blue]")
        return

    if not confirm:
        data_size = sum(
            f.stat().st_size for f in expected_path.rglob("*") if f.is_file()
        )
        console.print(f"📂 [yellow]About to remove:[/yellow] {expected_path}")
        console.print(f"📊 [yellow]Size:[/yellow] {format_size(data_size)}")

        if not typer.confirm("Are you sure you want to delete this data?"):
            console.print("❌ [red]Cancelled[/red]")
            return

    shutil.rmtree(expected_path)
    console.print("✅ [green]Successfully removed nextword data[/green]")


if __name__ == "__main__":
    app()
