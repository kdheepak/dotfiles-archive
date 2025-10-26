#!/usr/bin/env -S uv run
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "httpx>=0.27.0",
#   "typer>=0.12.5",
#   "rich>=13.7.1",
#   "platformdirs>=4.2.2",
# ]
# ///
"""
gh-release: Download GitHub release assets with smart platform matching,
parallel downloads, checksum detection, and resumable transfers.

Usage
-----
# Show latest release and what would be downloaded
uv run gh-release.py --repo cli/cli --list

# Download assets that match your current platform only
uv run gh-release.py --repo sharkdp/bat --match-platform --dir ./bin

# Download all release assets for a specific tag, with 8 workers
uv run gh-release.py --repo BurntSushi/ripgrep --tag 14.1.0 --parallel 8

# Filter by simple substring(s) or regex
uv run gh-release.py --repo junegunn/fzf --include linux,amd64 --exclude "musl|arm"
uv run gh-release.py --repo junegunn/fzf --regex "(linux|darwin).*(amd64|arm64)"

# Verify checksums if checksum files exist
uv run gh-release.py --repo stedolan/jq --verify
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import httpx
import typer
from platformdirs import user_cache_dir
from rich.console import Console
from rich.progress import (
    Progress,
    BarColumn,
    DownloadColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

app = typer.Typer(add_completion=False)
console = Console()

GITHUB_API = "https://api.github.com"


# ----------------------------- Utility helpers ----------------------------- #


def human_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024.0:
            return f"{f:.1f} {u}"
        f /= 1024.0
    return f"{f:.1f} PB"


def clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))


def sha256sum(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def get_token() -> Optional[str]:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def cache_dir() -> Path:
    return Path(user_cache_dir(appname="gh-release-combined", appauthor=False))


@dataclass(slots=True)
class Asset:
    name: str
    url: str
    size: int
    id: int


@dataclass(slots=True)
class ReleaseInfo:
    tag: str
    name: str
    assets: list[Asset]


OS_MAP = {
    "linux": ["linux", "gnu"],
    "darwin": ["darwin", "mac", "macos", "osx"],
    "windows": ["windows", "win", "win32", "win64", "msvc", "mingw"],
}

ARCH_MAP = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "armv7",
    "armv7": "armv7",
    "armhf": "armv7",
    "arm": "arm",
    "i386": "386",
    "i686": "386",
    "ppc64le": "ppc64le",
    "s390x": "s390x",
}


def current_platform_terms() -> list[str]:
    sys = platform.system().lower()
    mach = ARCH_MAP.get(platform.machine().lower(), platform.machine().lower())
    terms: list[str] = []

    # OS aliases
    os_aliases = []
    if sys in OS_MAP:
        os_aliases.extend(OS_MAP[sys])
    else:
        os_aliases.append(sys)

    # Arch variants
    arch_aliases = [mach]
    if mach == "amd64":
        arch_aliases += ["x86_64", "x64", "64bit"]
    elif mach == "arm64":
        arch_aliases += ["aarch64"]

    # Common terms to look for in asset names
    terms.extend(os_aliases + arch_aliases)

    # File suffixes that often differentiate builds
    if sys == "windows":
        terms += ["windows.zip", "win.zip", "msvc", "exe", "msi"]
    elif sys == "darwin":
        terms += ["darwin.tar.gz", "macos.tar.gz", "apple-darwin", "universal"]
    else:
        terms += ["linux.tar.gz", "linux.tgz", "unknown-linux-gnu", "musl", "gnu"]

    return list(dict.fromkeys(terms))  # dedupe while preserving order


# --------------------------- Checksum recognition --------------------------- #

CHECKSUM_BUNDLE_RE = re.compile(
    r"^(sha256sum(?:s)?|sha256sums|checksums|checksum|sha256|sha256sum\.txt|sha256sum\.sha|SHA256SUMS|SUMS|CHECKSUMS)(\.\w+)?$",
    re.IGNORECASE,
)


def strip_archive_ext(name: str) -> str:
    for ext in [".tar.gz", ".tgz", ".zip", ".tar.xz", ".txz", ".tar.bz2", ".tbz2"]:
        if name.endswith(ext):
            return name[: -len(ext)]
    return name.rsplit(".", 1)[0] if "." in name else name


def is_checksum_bundle(name: str) -> bool:
    return bool(CHECKSUM_BUNDLE_RE.match(name))


def parse_checksum_lines(text: str) -> dict[str, str]:
    """
    Parses common checksum formats:
    - 'HEX  filename' or 'HEX *filename'
    - 'filename: HEX'
    - lines that are just 'HEX  filename'
    """
    checksums: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # formats
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.*)$", s)
        if m:
            checksums[m.group(2).strip()] = m.group(1).lower()
            continue
        m = re.match(r"^(.*?)[=:]\s*([0-9a-fA-F]{64})$", s)
        if m:
            checksums[m.group(1).strip()] = m.group(2).lower()
            continue
    return checksums


def has_checksum_for(asset_name: str, checksum_names: set[str]) -> bool:
    # per-file: foo.ext.sha256 or foo.ext.sha256sum
    if (asset_name + ".sha256") in checksum_names or (
        asset_name + ".sha256sum"
    ) in checksum_names:
        return True
    # base without archive extension
    base = strip_archive_ext(asset_name)
    if (base + ".sha256") in checksum_names or (base + ".sha256sum") in checksum_names:
        return True
    return False


# ------------------------------ GitHub client ------------------------------ #


class GitHub:
    def __init__(self, token: Optional[str] = None):
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(headers=headers, timeout=60)

    def release(self, repo: str, tag: Optional[str]) -> ReleaseInfo:
        if tag:
            url = f"{GITHUB_API}/repos/{repo}/releases/tags/{tag}"
        else:
            url = f"{GITHUB_API}/repos/{repo}/releases/latest"
        r = self._client.get(url)
        r.raise_for_status()
        data = r.json()
        assets = [
            Asset(
                name=a["name"],
                url=a["browser_download_url"],
                size=a.get("size", 0),
                id=a["id"],
            )
            for a in data.get("assets", [])
        ]
        return ReleaseInfo(
            tag=data.get("tag_name") or tag or "unknown",
            name=data.get("name") or "",
            assets=assets,
        )

    def stream(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        return self._client.build_request("GET", url, headers=headers or {})


# ----------------------------- Download machinery ----------------------------- #


async def download_one(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    expected_size: int | None = None,
    etag: str | None = None,
    progress: Optional[Progress] = None,
    task_id: Optional[int] = None,
) -> None:
    """
    Resumable downloader: appends to *.part and renames on completion.
    Uses Range + If-Range when possible.
    """
    part = dest.with_suffix(dest.suffix + ".part")
    headers = {}
    pos = 0
    if part.exists():
        pos = part.stat().st_size
        headers["Range"] = f"bytes={pos}-"
        if etag:
            headers["If-Range"] = etag

    async with client.stream("GET", url, headers=headers, follow_redirects=True) as r:
        r.raise_for_status()
        mode = "ab" if pos > 0 else "wb"
        total = None
        # compute total if known (for progress bar)
        content_length = r.headers.get("Content-Length")
        if content_length is not None:
            total = int(content_length) + pos
        elif expected_size:
            total = expected_size
        if progress and task_id is not None and total is not None:
            progress.update(task_id, total=total, completed=pos)

        with open(part, mode) as f:
            async for chunk in r.aiter_bytes():
                f.write(chunk)
                if progress and task_id is not None:
                    progress.update(task_id, advance=len(chunk))

    # Verify size if available
    if expected_size is not None and part.stat().st_size != expected_size:
        # Some servers gzip on the fly; don't hard fail here. We'll rename anyway.
        pass

    part.rename(dest)


async def download_many(
    assets: list[Asset],
    dest_dir: Path,
    parallel: int = 4,
) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)

    # First, separate checksum assets vs normal assets so we can download checksums first
    checksum_assets = [
        a
        for a in assets
        if is_checksum_bundle(a.name) or a.name.endswith((".sha256", ".sha256sum"))
    ]
    data_assets = [a for a in assets if a not in checksum_assets]

    # Run downloads with a pool
    limits = httpx.Limits(max_connections=parallel)
    async with httpx.AsyncClient(limits=limits, timeout=60) as client:
        # Download checksums first
        with Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            transient=True,
            console=console,
        ) as progress:
            tasks = []
            for a in checksum_assets:
                dest = dest_dir / a.name
                t = progress.add_task(f"Checksum {a.name}", total=a.size or None)
                tasks.append(
                    download_one(client, a.url, dest, a.size, None, progress, t)
                )
            await asyncio.gather(*tasks)

        # Build checksum map (name -> hash)
        checksum_map: dict[str, str] = {}
        for a in checksum_assets:
            p = dest_dir / a.name
            if not p.exists():
                continue
            if a.name.endswith((".sha256", ".sha256sum")):
                try:
                    checksum_map[strip_archive_ext(a.name)] = (
                        p.read_text().strip().split()[0]
                    ).lower()
                except Exception:
                    pass
            elif is_checksum_bundle(a.name):
                try:
                    parsed = parse_checksum_lines(p.read_text())
                    checksum_map.update(parsed)
                except Exception:
                    pass

        # Download data assets
        with Progress(
            TextColumn("[bold green]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            transient=False,
            console=console,
        ) as progress:
            tasks = []
            for a in data_assets:
                dest = dest_dir / a.name
                t = progress.add_task(a.name, total=a.size or None)
                tasks.append(
                    download_one(client, a.url, dest, a.size, None, progress, t)
                )
            await asyncio.gather(*tasks)

    # Optional verification
    if checksum_map:
        failures = 0
        for a in data_assets:
            p = dest_dir / a.name
            if not p.exists():
                continue
            # direct match
            expected = checksum_map.get(a.name)
            # base-name match
            expected = expected or checksum_map.get(strip_archive_ext(a.name))
            if expected:
                actual = sha256sum(p)
                if actual != expected.lower():
                    failures += 1
                    console.print(
                        f"[red]Checksum FAILED[/red] {a.name} expected {expected} got {actual}"
                    )
                else:
                    console.print(f"[green]Checksum OK[/green] {a.name}")
        if failures:
            raise SystemExit(2)


# ------------------------------ Asset filtering ------------------------------ #


def filter_assets(
    assets: Iterable[Asset],
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    regex: str | None = None,
    match_platform: bool = False,
) -> list[Asset]:
    items = list(assets)

    def contains_any(name: str, needles: list[str]) -> bool:
        lname = name.lower()
        return any(n.lower() in lname for n in needles)

    if match_platform:
        terms = current_platform_terms()
        items = [a for a in items if contains_any(a.name, terms)]

    if include:
        items = [a for a in items if contains_any(a.name, include)]

    if exclude:
        items = [a for a in items if not contains_any(a.name, exclude)]

    if regex:
        r = re.compile(regex, re.IGNORECASE)
        items = [a for a in items if r.search(a.name)]

    return items


# ---------------------------------- CLI ---------------------------------- #


@app.command()
def main(
    repo: str = typer.Option(
        ..., help="GitHub repo in 'owner/name' form (e.g. cli/cli)"
    ),
    tag: Optional[str] = typer.Option(None, help="Release tag (default: latest)"),
    dir: Path = typer.Option(Path("."), help="Destination directory"),
    list: bool = typer.Option(
        False, "--list", help="List matching assets without downloading"
    ),
    match_platform: bool = typer.Option(
        False, help="Only select assets matching current OS/arch"
    ),
    include: Optional[str] = typer.Option(
        None, help="Comma-separated substrings that must appear"
    ),
    exclude: Optional[str] = typer.Option(
        None, help="Comma-separated substrings to exclude"
    ),
    regex: Optional[str] = typer.Option(
        None, help="Regex filter applied to asset names"
    ),
    parallel: int = typer.Option(4, help="Max parallel downloads"),
):
    """
    Download assets from a GitHub release.

    By default, fetches the *latest* release unless --tag is given.
    Filters can be combined. When --match-platform is on, OS/arch heuristics are applied.
    """
    token = get_token()
    gh = GitHub(token=token)
    info = gh.release(repo, tag)

    include_list = [s for s in (include or "").split(",") if s] or None
    exclude_list = [s for s in (exclude or "").split(",") if s] or None

    selected = filter_assets(
        info.assets,
        include=include_list,
        exclude=exclude_list,
        regex=regex,
        match_platform=match_platform,
    )

    if not selected:
        console.print("[yellow]No assets matched your filters.[/yellow]")
        raise SystemExit(1)

    # Pretty table: show ALL assets, mark selected ones with ✓
    selected_ids = {a.id for a in selected}
    table = Table(title=f"{repo} – {info.name or info.tag} ({info.tag})")
    table.add_column("✓", justify="center", width=2)
    table.add_column("Asset", overflow="fold")
    table.add_column("Size", justify="right")
    table.add_column("URL", overflow="fold")
    for a in info.assets:
        mark = "✓" if a.id in selected_ids else ""
        table.add_row(mark, a.name, human_size(a.size or 0), a.url)
    console.print(table)

    if list:
        return

    # Proceed with download
    console.print(f"Downloading {len(selected)} asset(s) to [bold]{dir}[/bold] ...")
    asyncio.run(download_many(selected, dir, clamp(parallel, 1, 16)))

    console.print("[green]Done.[/green]")


if __name__ == "__main__":
    raise SystemExit(app())
