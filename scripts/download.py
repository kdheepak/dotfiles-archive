#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "rich",
#     "typer",
#     "httpx",
# ]
# ///

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlsplit
import hashlib
import re
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.progress import (
    Progress,
    BarColumn,
    DownloadColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

console = Console()

# ---------- filename helpers ----------


def _filename_from_content_disposition(cd: str | None) -> Optional[str]:
    if not cd:
        return None
    # RFC 5987: filename*=utf-8''encoded-name.ext
    m = re.search(r'filename\*\s*=\s*([^\'";]+)\'\'([^;]+)', cd, re.IGNORECASE)
    if m:
        charset, enc_name = m.groups()
        try:
            return unquote(enc_name, encoding=charset, errors="replace")
        except LookupError:
            return unquote(enc_name)
    # filename="name.ext" or filename=name.ext
    m = re.search(r'filename\s*=\s*"?(?P<fn>[^";]+)"?', cd, re.IGNORECASE)
    if m:
        return m.group("fn")
    return None


def _filename_from_url(url: httpx.URL | str) -> Optional[str]:
    s = str(url)
    path = urlsplit(s).path
    name = Path(unquote(path)).name
    return name or None


def _sanitize_filename(name: str) -> str:
    name = name.replace("\\", "_").replace("/", "_").strip()
    name = re.sub(r"[\x00-\x1f]", "_", name)
    return name or "download.bin"


# ---------- hashing ----------


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ---------- core download ----------


def _determine_filename(resp: httpx.Response, override_name: Optional[str]) -> str:
    if override_name:
        return _sanitize_filename(override_name)
    cd_name = _filename_from_content_disposition(
        resp.headers.get("content-disposition")
    )
    url_name = _filename_from_url(resp.request.url)  # final URL after redirects
    return _sanitize_filename(cd_name or url_name or "download.bin")


def _start_progress(total: Optional[int]):
    return Progress(
        TextColumn("[cyan]Downloading[/cyan]"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    ), {"total": total if total and total > 0 else None}


def _supports_ranges(headers: httpx.Headers) -> bool:
    return headers.get("accept-ranges", "").lower() == "bytes"


def _download_one(
    client: httpx.Client,
    url: str,
    dir: Path,
    name_override: Optional[str],
    resume: bool,
    overwrite: bool,
    sha256: Optional[str],
    chunk_size: int = 1 << 15,  # 32 KiB
) -> Path:
    headers = {"User-Agent": "httpx"}
    temp_path: Optional[Path] = None

    # Try HEAD to learn size/ranges (not all servers support it)
    try:
        head = client.head(url, headers=headers)
        head.raise_for_status()
        total = int(head.headers.get("content-length", "0")) or None
        range_ok = _supports_ranges(head.headers)
    except Exception:
        total = None
        range_ok = False

    # Open streaming GET to learn final filename and maybe size
    with client.stream("GET", url, headers=headers) as r0:
        r0.raise_for_status()
        filename = _determine_filename(r0, name_override)
        final_path = dir / filename
        temp_path = dir / (filename + ".part")

        if total is None:
            try:
                total = int(r0.headers.get("content-length", "0")) or None
            except Exception:
                total = None

        if resume and temp_path.exists():
            existing = temp_path.stat().st_size
            if existing > 0 and (range_ok or _supports_ranges(r0.headers)):
                r0.close()
                # Reopen with Range
                resp = client.get(
                    url, headers=headers | {"Range": f"bytes={existing}-"}, stream=True
                )
                if resp.status_code == 206:
                    part_len = int(resp.headers.get("content-length", "0") or 0)
                    total_effective = (existing + part_len) if part_len else None
                    progress, opts = _start_progress(total_effective)
                    with progress:
                        task = progress.add_task(
                            "download",
                            total=opts["total"],
                            completed=existing if opts["total"] else 0,
                        )
                        with temp_path.open("ab") as f:
                            for chunk in resp.iter_bytes(chunk_size=chunk_size):
                                if not chunk:
                                    continue
                                f.write(chunk)
                                progress.update(task, advance=len(chunk))
                    resp.close()
                else:
                    resp.close()
                    if not overwrite and final_path.exists():
                        raise FileExistsError(
                            f"File exists and server didn't support resume: {final_path}"
                        )
                    progress, opts = _start_progress(total)
                    with progress:
                        task = progress.add_task("download", total=opts["total"])
                        with temp_path.open("wb") as f:
                            with client.stream("GET", url, headers=headers) as r:
                                r.raise_for_status()
                                for chunk in r.iter_bytes(chunk_size=chunk_size):
                                    if not chunk:
                                        continue
                                    f.write(chunk)
                                    progress.update(task, advance=len(chunk))
            else:
                if not overwrite and final_path.exists():
                    raise FileExistsError(
                        f"File exists and resume not possible: {final_path}"
                    )
                progress, opts = _start_progress(total)
                with progress:
                    task = progress.add_task("download", total=opts["total"])
                    with temp_path.open("wb") as f:
                        for chunk in r0.iter_bytes(chunk_size=chunk_size):
                            if not chunk:
                                continue
                            f.write(chunk)
                            progress.update(task, advance=len(chunk))
        else:
            if final_path.exists() and not overwrite:
                raise FileExistsError(f"File already exists: {final_path}")
            progress, opts = _start_progress(total)
            with progress:
                task = progress.add_task("download", total=opts["total"])
                with temp_path.open("wb") as f:
                    for chunk in r0.iter_bytes(chunk_size=chunk_size):
                        if not chunk:
                            continue
                        f.write(chunk)
                        progress.update(task, advance=len(chunk))

    # Optional verify hash
    if sha256:
        digest = _sha256_file(temp_path)
        if digest.lower() != sha256.lower():
            temp_path.unlink(missing_ok=True)
            raise ValueError(
                f"SHA-256 mismatch for {final_path.name}: expected {sha256}, got {digest}"
            )

    temp_path.replace(final_path)
    return final_path


# ---------- Single, root command (no subcommands) ----------


def main(
    urls: list[str] = typer.Argument(..., help="One or more URLs to download."),
    dir: Path = typer.Option(Path("."), "--dir", "-o", help="Output directory."),
    name: Optional[str] = typer.Option(
        None, "--name", "-n", help="Override output filename (only used if one URL)."
    ),
    resume: bool = typer.Option(
        True, "--resume/--no-resume", help="Resume partial downloads if supported."
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Overwrite existing files if present."
    ),
    timeout: float = typer.Option(60.0, help="HTTP timeout (seconds)."),
    sha256: Optional[str] = typer.Option(
        None, help="Expected SHA-256 for the downloaded file (only if one URL)."
    ),
) -> None:
    """
    Generic downloader: follows redirects, smart filenames, resume, progress bar, retries, optional SHA-256.
    """
    if len(urls) > 1 and name:
        typer.echo("Ignoring --name because multiple URLs were provided.", err=True)
        name = None
    if len(urls) > 1 and sha256:
        typer.echo("Ignoring --sha256 because multiple URLs were provided.", err=True)
        sha256 = None

    dir.mkdir(parents=True, exist_ok=True)

    succeeded: list[Path] = []
    failed: list[tuple[str, str]] = []
    attempts = 3

    with httpx.Client(
        follow_redirects=True, timeout=timeout, headers={"User-Agent": "httpx"}
    ) as client:
        for i, url in enumerate(urls, 1):
            console.rule(f"[bold]({i}/{len(urls)}) {url}")
            last_err = None
            for attempt in range(1, attempts + 1):
                try:
                    out = _download_one(
                        client=client,
                        url=url,
                        dir=dir,
                        name_override=name,
                        resume=resume,
                        overwrite=overwrite,
                        sha256=sha256 if len(urls) == 1 else None,
                    )
                    console.print(f"[green]Saved:[/green] {out}")
                    succeeded.append(out)
                    last_err = None
                    break
                except httpx.HTTPStatusError as e:
                    last_err = (
                        f"HTTP {e.response.status_code} {e.response.reason_phrase}"
                    )
                except FileExistsError as e:
                    last_err = str(e)
                    break
                except httpx.HTTPError as e:
                    last_err = f"Network error: {e}"
                except Exception as e:
                    last_err = f"{type(e).__name__}: {e}"

                console.print(f"[yellow]Attempt {attempt} failed:[/yellow] {last_err}")
            if last_err:
                failed.append((url, last_err))

    if succeeded:
        console.print(
            f"\n[bold green]Downloaded {len(succeeded)} file(s).[/bold green]"
        )
    if failed:
        console.print(f"\n[bold red]Failed {len(failed)} file(s):[/bold red]")
        for u, err in failed:
            console.print(f"• {u}\n  └─ {err}")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    typer.run(main)
