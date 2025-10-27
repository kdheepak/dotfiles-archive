#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "rich",
#     "typer",
#     "httpx",
#     "aiofiles",
# ]
# ///

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlsplit
import asyncio
import hashlib
import re
from typing import Optional, Iterable

import aiofiles
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
    m = re.search(r"filename\*\s*=\s*([^'\";]+)''([^;]+)", cd, re.IGNORECASE)
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


# ---------- core download (async) ----------


def _determine_filename(resp: httpx.Response, override_name: Optional[str]) -> str:
    if override_name:
        return _sanitize_filename(override_name)
    cd_name = _filename_from_content_disposition(
        resp.headers.get("content-disposition")
    )
    url_name = _filename_from_url(resp.request.url)  # final URL after redirects
    return _sanitize_filename(cd_name or url_name or "download.bin")


def _supports_ranges(headers: httpx.Headers) -> bool:
    return headers.get("accept-ranges", "").lower() == "bytes"


async def _hash_file_async(path: Path) -> str:
    return await asyncio.to_thread(_sha256_file, path)


class DownloadError(Exception):
    pass


async def _download_one_async(
    *,
    client: httpx.AsyncClient,
    url: str,
    dir: Path,
    name_override: Optional[str],
    resume: bool,
    overwrite: bool,
    sha256: Optional[str],
    chunk_size: int,
    progress: Progress,
    task_description: str,
) -> Path:
    headers = {"User-Agent": "httpx"}

    # Try HEAD to learn size/ranges (not all servers support it)
    total: Optional[int] = None
    range_ok: bool = False
    try:
        head = await client.head(url, headers=headers)
        head.raise_for_status()
        total = int(head.headers.get("content-length", "0") or 0) or None
        range_ok = _supports_ranges(head.headers)
    except Exception:
        total = None
        range_ok = False

    # Open streaming GET to learn final filename and maybe size
    async with client.stream("GET", url, headers=headers) as r0:
        r0.raise_for_status()
        filename = _determine_filename(r0, name_override)
        final_path = dir / filename
        temp_path = dir / (filename + ".part")

        if total is None:
            try:
                total = int(r0.headers.get("content-length", "0") or 0) or None
            except Exception:
                total = None

        # Create a task in the shared Progress
        task_id = progress.add_task(task_description, total=total or 0)

        async def stream_into_file(
            resp: httpx.Response, mode: str, start_completed: int = 0
        ):
            if total is None:
                # For unknown totals, set total to None in progress (Rich uses indeterminate)
                progress.update(task_id, total=None)
            else:
                progress.update(task_id, total=total)
                if start_completed:
                    progress.update(task_id, completed=start_completed)
            async with aiofiles.open(temp_path, mode) as f:
                async for chunk in resp.aiter_bytes(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    await f.write(chunk)
                    progress.advance(task_id, len(chunk))

        if resume and temp_path.exists():
            existing = temp_path.stat().st_size
            # if server supports ranges (via HEAD or first GET), try to resume
            if existing > 0 and (range_ok or _supports_ranges(r0.headers)):
                await r0.aclose()
                # Reopen with Range
                resp = await client.get(
                    url, headers={**headers, "Range": f"bytes={existing}-"}, stream=True
                )
                try:
                    if resp.status_code == 206:
                        part_len = int(resp.headers.get("content-length", "0") or 0)
                        total_effective = (existing + part_len) if part_len else None
                        total_saved = existing if total_effective else 0
                        # Adjust progress totals for resumed transfer
                        total = total_effective
                        await stream_into_file(resp, "ab", start_completed=total_saved)
                    else:
                        await resp.aclose()
                        if not overwrite and final_path.exists():
                            raise DownloadError(
                                f"File exists and server didn't support resume: {final_path}"
                            )
                        # Fresh full download
                        async with client.stream("GET", url, headers=headers) as r:
                            r.raise_for_status()
                            await stream_into_file(r, "wb")
                finally:
                    await resp.aclose()
            else:
                if not overwrite and final_path.exists():
                    raise DownloadError(
                        f"File exists and resume not possible: {final_path}"
                    )
                await stream_into_file(r0, "wb")
        else:
            if final_path.exists() and not overwrite:
                raise DownloadError(f"File already exists: {final_path}")
            await stream_into_file(r0, "wb")

    # Optional verify hash
    if sha256:
        digest = await _hash_file_async(temp_path)
        if digest.lower() != sha256.lower():
            try:
                temp_path.unlink(missing_ok=True)
            finally:
                pass
            raise DownloadError(
                f"SHA-256 mismatch for {final_path.name}: expected {sha256}, got {digest}"
            )

    # Atomic-ish move into place
    await asyncio.to_thread(temp_path.replace, final_path)
    progress.update(task_id, completed=total or temp_path.stat().st_size)
    console.print(f"[green]Saved:[/green] {final_path}")
    return final_path


# ---------- CLI ----------

app = typer.Typer(add_completion=False)


@app.command()
async def main(
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
    concurrency: int = typer.Option(
        4, "--concurrency", "-c", min=1, help="Max concurrent downloads."
    ),
    chunk_size: int = typer.Option(
        1 << 15, "--chunk-size", help="Chunk size in bytes."
    ),
) -> None:
    """
    Async generic downloader: follows redirects, smart filenames, resume, progress bars,
    configurable concurrency & retries, optional SHA-256 verification.
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

    limits = httpx.Limits(
        max_keepalive_connections=concurrency, max_connections=concurrency
    )

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers={"User-Agent": "httpx"},
        limits=limits,
    ) as client, Progress(
        TextColumn("[cyan]Downloading[/cyan]"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        sem = asyncio.Semaphore(concurrency)

        async def run_one(i: int, url: str):
            nonlocal succeeded, failed
            console.rule(f"[bold]({i}/{len(urls)}) {url}")
            last_err: Optional[str] = None
            for attempt in range(1, attempts + 1):
                try:
                    async with sem:
                        out = await _download_one_async(
                            client=client,
                            url=url,
                            dir=dir,
                            name_override=name if len(urls) == 1 else None,
                            resume=resume,
                            overwrite=overwrite,
                            sha256=sha256 if len(urls) == 1 else None,
                            chunk_size=chunk_size,
                            progress=progress,
                            task_description="download",
                        )
                    succeeded.append(out)
                    last_err = None
                    break
                except httpx.HTTPStatusException as e:  # pragma: no cover (type hint)
                    last_err = (
                        f"HTTP {e.response.status_code} {e.response.reason_phrase}"
                    )
                except httpx.HTTPStatusError as e:
                    last_err = (
                        f"HTTP {e.response.status_code} {e.response.reason_phrase}"
                    )
                except httpx.HTTPError as e:
                    last_err = f"Network error: {e}"
                except DownloadError as e:
                    last_err = str(e)
                except Exception as e:
                    last_err = f"{type(e).__name__}: {e}"

                console.print(f"[yellow]Attempt {attempt} failed:[/yellow] {last_err}")
                # brief backoff before retrying this URL
                await asyncio.sleep(min(2**attempt, 5))

            if last_err:
                failed.append((url, last_err))

        await asyncio.gather(*(run_one(i, u) for i, u in enumerate(urls, 1)))

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
    app()
