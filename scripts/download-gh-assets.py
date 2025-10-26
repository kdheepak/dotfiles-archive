#!/usr/bin/env -S uv run
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "httpx>=0.27.0",
#   "typer>=0.12.5",
#   "rich>=13.7.1",
# ]
# ///
"""
Download all assets from a GitHub release into a folder

Examples
--------
# Latest release
uv run download-gh-assets.py download facebook/react -o ./react-latest

# Specific tag
uv run download-gh-assets.py download pytorch/pytorch --tag v2.5.0 -o ./torch-2.5.0

# Overwrite and bump parallelism
uv run download-gh-assets.py download owner/repo --workers 8 --force

# Filter / dry-run
uv run download-gh-assets.py download owner/repo --pattern '*.tar.gz' --dry-run

# Compute local checksums for each file
uv run download-gh-assets.py download owner/repo --checksum

# Verify using upstream checksum files shipped in the release
uv run download-gh-assets.py download owner/repo --verify

# List release metadata and assets (optionally filter)
uv run download-gh-assets.py list owner/repo --tag v2.5.0 --pattern '*.whl'
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import httpx
import typer
from rich.console import Console
from rich.table import Table
from rich import box
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    DownloadColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
)

console = Console()

app = typer.Typer(
    add_completion=False, help="Download or inspect assets from a GitHub release."
)
GITHUB_API = "https://api.github.com"

ARCHIVE_EXTS = [".tar.gz", ".tar.zst", ".tar.xz", ".tgz", ".zip", ".whl"]


@dataclass(slots=True, kw_only=True)
class Asset:
    name: str
    url: str
    size: int = 0


def clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


def sha256sum(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def human_size(n: int) -> str:
    """Return a human-friendly size string (e.g. '12.3 MB')."""
    if n <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    k = 1024.0
    i = 0
    while n >= k and i < len(units) - 1:
        n /= k
        i += 1
    return f"{n:.1f} {units[i]}"


def is_checksum_asset(name: str) -> bool:
    # Common checksum files used by many projects
    name_lower = name.lower()
    if name_lower in {"sha256sums", "checksums"}:
        return True
    return (
        name_lower.endswith(".sha256")
        or name_lower.endswith(".sha256sum")
        or name_lower.endswith("sha256sums.txt")
        or name_lower.endswith("sha256sum.txt")
        or name_lower == "sha256sum"
        or name_lower == "sha256sums"
    )


def parse_checksum_file(text: str) -> dict[str, str]:
    """
    Parse lines like:
      <hex>  filename
      <hex> *filename
    Returns {filename: hex}
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Fa-f0-9]{64})\s+\*?(.*)$", line)
        if not m:
            continue
        digest, fname = m.groups()
        fname = fname.strip()
        if fname:
            out[fname] = digest.lower()
    return out


def yn(flag: bool) -> str:
    return "[green]✓[/green]" if flag else "[red]✗[/red]"


def checksum_target_name(name: str) -> str | None:
    """Return the asset name this checksum file applies to, if detectable."""
    lower = name.lower()
    for ext in (".sha256", ".sha256sum"):
        if lower.endswith(ext):
            return name[: -len(ext)]
    # Non per-file checksum names (SHA256SUMS etc.) don’t map to a single file
    return None


def strip_archive_ext(name: str) -> str:
    for ext in ARCHIVE_EXTS:
        if name.endswith(ext):
            return name[: -len(ext)]
    # fall back to single suffix
    p = Path(name)
    return p.stem if p.suffix else name


def is_checksum_bundle(name: str) -> bool:
    """Files like SHA256SUMS / CHECKSUMS that cover multiple artifacts."""
    n = name.lower()
    return (
        n in {"sha256sums", "sha256sum", "checksums"}
        or n.endswith("sha256sums.txt")
        or n.endswith("sha256sum.txt")
    )


def has_checksum_for(asset_name: str, checksum_names: set[str]) -> bool:
    """Heuristics to decide if a checksum file exists for this asset."""
    if any(is_checksum_bundle(n) for n in checksum_names):
        return True  # assume bundle covers all

    # direct per-file: foo.ext.sha256 / foo.ext.sha256sum
    if (asset_name + ".sha256") in checksum_names or (
        asset_name + ".sha256sum"
    ) in checksum_names:
        return True

    # extensionless per-file: foo + .sha256 where foo matches asset without archive ext
    base = strip_archive_ext(asset_name)
    if (base + ".sha256") in checksum_names or (base + ".sha256sum") in checksum_names:
        return True

    # some projects publish both foo.tar.gz and foo.tgz; try both bases
    alt_bases = {
        strip_archive_ext(base),
        strip_archive_ext(asset_name.replace(".tgz", ".tar.gz")),
    }
    for b in alt_bases:
        if (b + ".sha256") in checksum_names or (b + ".sha256sum") in checksum_names:
            return True

    return False


class GithubReleaseDownloader:
    """Small wrapper around httpx.Client for GitHub Releases."""

    def __init__(self, *, token: str | None = None) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "gh-release-downloader/1.4",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(headers=headers, follow_redirects=True)
        self.token = token

    # allow use as a context manager
    def __enter__(self) -> GithubReleaseDownloader:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        # return False so exceptions are not suppressed
        return False

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass

    def _handle_rate_limit(self, resp: httpx.Response) -> None:
        # Helpful message only (no retries/backoff).
        if resp.status_code not in (403, 429):
            return
        remaining = resp.headers.get("X-RateLimit-Remaining")
        reset = resp.headers.get("X-RateLimit-Reset")
        if remaining == "0" and reset:
            reset_epoch = int(reset)
            wait = max(0, reset_epoch - int(time.time()))
            raise RuntimeError(
                "GitHub API rate limit exceeded. "
                f"Try again in ~{wait} seconds (resets at epoch {reset_epoch}). "
                "Provide GITHUB_TOKEN to increase limits."
            )

    def get_release(self, owner: str, repo: str, tag: str | None) -> dict[str, Any]:
        url = (
            f"{GITHUB_API}/repos/{owner}/{repo}/releases/tags/{tag}"
            if tag and tag.lower() != "latest"
            else f"{GITHUB_API}/repos/{owner}/{repo}/releases/latest"
        )
        resp = self.client.get(url)
        if resp.status_code == 404:
            raise SystemExit(
                f"Release not found for {owner}/{repo} with tag '{tag or 'latest'}'."
            )
        self._handle_rate_limit(resp)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def parse_assets(release_json: dict[str, Any]) -> list[Asset]:
        assets: list[Asset] = []
        for a in release_json.get("assets", []) or []:
            name = a.get("name")
            url = a.get("browser_download_url")
            size = int(a.get("size") or 0)
            if name and url:
                assets.append(Asset(name=name, url=url, size=size))
        return assets

    def download_asset(
        self,
        asset: Asset,
        outdir: Path,
        *,
        force: bool = False,
        progress: Progress | None = None,
        task_id: int | None = None,
    ) -> tuple[str, str]:
        """
        Returns (asset_name, status) where status is one of:
        'downloaded', 'exists', 'error: ...'
        """
        dest = outdir / asset.name

        if dest.exists() and not force:
            # Mark complete immediately if it already exists
            if progress and task_id is not None:
                try:
                    # If we know the total, set completed; otherwise let it finish indeterminate
                    total = asset.size if asset.size > 0 else None
                    progress.update(task_id, completed=total)
                except Exception:
                    pass
            return (asset.name, "exists")

        try:
            with self.client.stream("GET", asset.url) as r:
                if r.status_code == 404:
                    if progress and task_id is not None:
                        try:
                            progress.update(
                                task_id, description=f"[red]{asset.name}[/red]"
                            )
                        except Exception:
                            pass
                    return (asset.name, "error: 404 not found")
                self._handle_rate_limit(r)
                r.raise_for_status()

                dest_tmp = dest.with_suffix(dest.suffix + ".part")
                with open(dest_tmp, "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        if progress and task_id is not None:
                            try:
                                progress.update(task_id, advance=len(chunk))
                            except Exception:
                                pass
                dest_tmp.replace(dest)

                # Optional integrity check against known size if provided by API
                if asset.size and dest.stat().st_size != asset.size:
                    return (
                        asset.name,
                        f"error: size mismatch (expected {asset.size}, got {dest.stat().st_size})",
                    )

        except Exception as e:
            return (asset.name, f"error: {e}")
        return (asset.name, "downloaded")


def parse_repo(repo: str) -> tuple[str, str]:
    if "/" not in repo:
        raise typer.BadParameter("Use the form 'owner/repo'.")
    owner, name = repo.split("/", 1)
    if not owner or not name:
        raise typer.BadParameter("Use the form 'owner/repo'.")
    return owner, name


def filter_assets(assets: Iterable[Asset], pattern: str | None) -> list[Asset]:
    if not pattern:
        return list(assets)
    if pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 2:
        rx = re.compile(pattern[1:-1])
        return [a for a in assets if rx.search(a.name)]
    from fnmatch import fnmatch

    return [a for a in assets if fnmatch(a.name, pattern)]


def build_expected_digests(
    outdir: Path, checksum_asset_names: list[str]
) -> dict[str, str]:
    expected: dict[str, str] = {}
    for name in checksum_asset_names:
        p = outdir / name
        if not p.exists():
            continue
        try:
            text = p.read_text(errors="ignore")
        except UnicodeDecodeError:
            # Try binary decode fallback
            text = p.read_bytes().decode("utf-8", errors="ignore")
        parsed = parse_checksum_file(text)
        expected.update(parsed)
    return expected


def verify_checksums(outdir: Path, expected: dict[str, str]) -> list[tuple[str, str]]:
    """
    Returns list of (asset_name, status) where status is 'ok', 'mismatch: ...' or 'missing'
    """
    results: list[tuple[str, str]] = []
    for relname, digest in expected.items():
        path = outdir / relname
        if not path.exists():
            results.append((relname, "missing"))
            continue
        actual = sha256sum(path)
        if actual.lower() == digest.lower():
            results.append((relname, "ok"))
        else:
            results.append((relname, f"mismatch: expected {digest}, got {actual}"))
    return results


@app.command(help="Download all assets from a GitHub release into a folder.")
def download(
    repo: str = typer.Argument(
        ..., help="Repository as 'owner/repo' (e.g., 'numpy/numpy')."
    ),
    tag: str = typer.Option(
        "latest", "--tag", "-t", help="Release tag (default: latest)."
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory (default: ./downloads/{repo}-{tag}).",
    ),
    workers: int = typer.Option(
        clamp((os.cpu_count() or 4) * 2, 1, 32),
        "--workers",
        "-w",
        help="Parallel download workers.",
        show_default=True,
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files."),
    token: str | None = typer.Option(
        os.environ.get("GITHUB_TOKEN"),
        "--token",
        help="GitHub token for authentication.",
    ),
    pattern: str | None = typer.Option(
        None,
        "--pattern",
        "-p",
        help="Filter assets by glob (e.g. '*.tar.gz') or /regex/.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be downloaded, without writing files."
    ),
    checksum: bool = typer.Option(
        False,
        "--checksum",
        help="Write a <file>.sha256 next to each asset after download (or if it already exists).",
    ),
    verify: bool = typer.Option(
        False,
        "--verify",
        help="Verify downloaded files using checksum assets from the release (e.g., SHA256SUMS, *.sha256).",
    ),
) -> int:
    owner, repo_name = parse_repo(repo)

    with GithubReleaseDownloader(token=token) as downloader:
        try:
            release = downloader.get_release(owner, repo_name, tag)
        except Exception as e:
            typer.secho(
                f"Error fetching release metadata: {e}", fg=typer.colors.RED, err=True
            )
            return 2

        release_tag = release.get("tag_name") or tag or "latest"
        all_assets = GithubReleaseDownloader.parse_assets(release)

        checksum_assets = [a for a in all_assets if is_checksum_asset(a.name)]
        data_assets = [a for a in all_assets if not is_checksum_asset(a.name)]

        data_assets = filter_assets(data_assets, pattern)

        assets_to_download = list(data_assets) + (checksum_assets if verify else [])

        if not assets_to_download:
            typer.secho(
                f"No matching assets in release '{release_tag}' of {owner}/{repo_name}.",
                fg=typer.colors.YELLOW,
                err=True,
            )
            return 1

        outdir = output or Path(
            "downloads"
        ) / f"{owner}-{repo_name}-{release_tag}".replace("/", "-")

        if dry_run:
            typer.secho(
                f"[dry-run] Would download {len(assets_to_download)} asset(s) from {owner}/{repo_name} "
                f"release '{release_tag}' to '{outdir}':",
                fg=typer.colors.CYAN,
            )
            for a in assets_to_download:
                mark = " (checksum)" if is_checksum_asset(a.name) else ""
                typer.echo(
                    f" - {a.name}{mark} ({a.size or 'unknown'} bytes) <- {a.url}"
                )
            if token:
                typer.echo(
                    "[dry-run] Would use authentication from GITHUB_TOKEN.", err=True
                )
            return 0

        outdir.mkdir(parents=True, exist_ok=True)

        typer.secho(
            f"Downloading {len(assets_to_download)} asset(s) from {owner}/{repo_name} "
            f"release '{release_tag}' to '{outdir}'.",
            fg=typer.colors.CYAN,
        )
        if token:
            typer.echo("Using authentication from GITHUB_TOKEN.", err=True)

        results: list[tuple[str, str]] = []

        # --- Rich progress setup (one task per asset) ---
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.fields[repo]}[/]"),
            BarColumn(bar_width=None),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            TextColumn(" • {task.description}"),
            transient=False,  # keep bars on screen after completion
            console=console,
        )

        name_to_task: dict[str, int] = {}
        for a in assets_to_download:
            # description holds the filename; 'repo' field shows owner/repo
            total = a.size if a.size > 0 else None
            task_id = progress.add_task(
                description=a.name,
                total=total,
                repo=f"{owner}/{repo_name}",
            )
            name_to_task[a.name] = task_id

        with progress:
            with cf.ThreadPoolExecutor(max_workers=clamp(workers, 1, 32)) as ex:
                fut_map = {
                    ex.submit(
                        downloader.download_asset,
                        asset,
                        outdir,
                        force=force,
                        progress=progress,
                        task_id=name_to_task[asset.name],
                    ): asset.name
                    for asset in assets_to_download
                }
                for fut in cf.as_completed(fut_map):
                    name = fut_map[fut]
                    try:
                        asset_name, status = fut.result()
                    except Exception as e:
                        asset_name, status = name, f"error: {e}"
                    results.append((asset_name, status))
                    # Colorized status after the bars (using progress console)
                    color = (
                        "green"
                        if status == "downloaded"
                        else ("yellow" if status == "exists" else "red")
                    )
                    progress.console.print(
                        f"[{color}][{status}][/ {color}] {asset_name}"
                    )

        # --- Checksums (unchanged) ---
        if checksum:
            typer.secho("\nComputing SHA-256 checksums...", fg=typer.colors.CYAN)
            for asset_name, status in results:
                if status.startswith("error") or is_checksum_asset(asset_name):
                    continue
                dest = outdir / asset_name
                try:
                    digest = sha256sum(dest)
                    (dest.with_suffix(dest.suffix + ".sha256")).write_text(
                        f"{digest}  {asset_name}\n"
                    )
                    typer.secho(
                        f"[checksum] {asset_name} {digest}", fg=typer.colors.GREEN
                    )
                except Exception as e:
                    typer.secho(
                        f"[checksum error] {asset_name}: {e}",
                        fg=typer.colors.RED,
                        err=True,
                    )

        # --- Verification (unchanged) ---
        verify_failed = False
        if verify:
            typer.secho(
                "\nVerifying checksums from release files...", fg=typer.colors.CYAN
            )
            checksum_names = [a.name for a in checksum_assets]
            expected = build_expected_digests(outdir, checksum_names)
            if not expected:
                typer.secho(
                    "No checksum data found in release assets; cannot verify.",
                    fg=typer.colors.YELLOW,
                    err=True,
                )
            else:
                ver_results = verify_checksums(outdir, expected)
                mismatches = [r for r in ver_results if r[1] != "ok"]
                for name, status in ver_results:
                    color = typer.colors.GREEN if status == "ok" else typer.colors.RED
                    typer.secho(f"[verify] {name}: {status}", fg=color)
                if mismatches:
                    typer.secho(
                        f"\nVerification failed for {len(mismatches)} file(s).",
                        fg=typer.colors.RED,
                        err=True,
                    )
                    verify_failed = True

        errors = [r for r in results if r[1].startswith("error")]
        if errors:
            typer.secho(
                f"\nCompleted with {len(errors)} download error(s). Failed assets:",
                fg=typer.colors.RED,
                err=True,
            )
            for n, s in errors:
                typer.secho(f" - {n}: {s}", fg=typer.colors.RED, err=True)

        if errors:
            return 3
        if verify_failed:
            return 4

        typer.secho("\nAll assets processed.", fg=typer.colors.GREEN)
        return 0


@app.command(help="List release metadata and assets (optionally filter).")
def assets(
    repo: str = typer.Argument(
        ..., help="Repository as 'owner/repo' (e.g., 'numpy/numpy')."
    ),
    tag: str = typer.Option(
        "latest", "--tag", "-t", help="Release tag (default: latest)."
    ),
    token: str | None = typer.Option(
        os.environ.get("GITHUB_TOKEN"),
        "--token",
        help="GitHub token for authentication.",
    ),
    pattern: str | None = typer.Option(
        None,
        "--pattern",
        "-p",
        help="Filter assets by glob (e.g. '*.tar.gz') or /regex/.",
    ),
) -> int:
    owner, repo_name = parse_repo(repo)
    with GithubReleaseDownloader(token=token) as downloader:
        try:
            release = downloader.get_release(owner, repo_name, tag)
        except Exception as e:
            typer.secho(
                f"Error fetching release metadata: {e}", fg=typer.colors.RED, err=True
            )
            return 2

        release_tag = release.get("tag_name") or tag or "latest"
        published = release.get("published_at") or "unknown"
        draft = bool(release.get("draft"))
        prerelease = bool(release.get("prerelease"))
        typer.secho(f"{owner}/{repo_name}", fg=typer.colors.CYAN)
        # --- Release metadata table ---
        meta = Table(
            box=box.SIMPLE_HEAVY,
            show_lines=False,
            header_style="cyan",
            title=f"{owner}/{repo_name}",
        )
        meta.add_column("Field", style="bold", no_wrap=True)
        meta.add_column("Value")

        meta.add_row("Tag", release_tag)
        meta.add_row("Published", published or "unknown")
        meta.add_row("Draft", "Yes" if draft else "No")
        meta.add_row("Prerelease", "Yes" if prerelease else "No")

        console.print(meta)
        console.print()  # spacer

        # --- Assets table (one row per non-checksum asset, with ✓/✗) ---
        all_assets = GithubReleaseDownloader.parse_assets(release)

        data_assets = [a for a in all_assets if not is_checksum_asset(a.name)]
        checksum_assets = [a for a in all_assets if is_checksum_asset(a.name)]

        # apply filter to data assets only
        data_assets = filter_assets(data_assets, pattern)
        if not data_assets:
            console.print("[yellow]No matching assets.[/yellow]")
            return 0

        # Build a fast lookup of checksum file names
        checksum_name_set: set[str] = {a.name for a in checksum_assets}

        # sort by name
        data_assets.sort(key=lambda a: a.name.lower())

        table = Table(
            title=f"{release_tag} assets",
            box=box.SIMPLE_HEAVY,
            show_lines=False,
            header_style="cyan",
            pad_edge=False,
        )
        table.add_column(
            "Name", style="bold", no_wrap=False, overflow="fold", max_width=60
        )
        table.add_column("Size", justify="right", no_wrap=True)
        table.add_column("Checksum", justify="center", no_wrap=True)
        table.add_column("Link", justify="center", no_wrap=True)

        for a in data_assets:
            size_str = human_size(a.size) if a.size else "unknown"
            has_cs = has_checksum_for(a.name, checksum_name_set)
            link = f"[underline][link={a.url}]Download[/link][/underline]"
            table.add_row(a.name, size_str, yn(has_cs), link)

        console.print(table)
        return 0


if __name__ == "__main__":
    raise SystemExit(app())
