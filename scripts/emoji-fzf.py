#!/usr/bin/env -S uv --quiet run --script
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "emoji",
#     "typer",
#     "rich",
# ]
# ///
"""
emoji-fzf: fuzzy-search all emojis with a Rich preview
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import unicodedata

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

app = typer.Typer(add_completion=False)
console = Console()

# Fitzpatrick skin-tone modifiers
SKIN_MODS = [
    "\U0001f3fb",  # light
    "\U0001f3fc",  # medium-light
    "\U0001f3fd",  # medium
    "\U0001f3fe",  # medium-dark
    "\U0001f3ff",  # dark
]


def _get_emoji_data() -> dict[str, dict]:
    # Prefer top-level EMOJI_DATA (emoji >=2.x), fall back if needed.
    try:
        from emoji import EMOJI_DATA  # type: ignore

        return EMOJI_DATA  # type: ignore[return-value]
    except Exception:
        try:
            from emoji.unicode_codes import EMOJI_DATA  # type: ignore

            return EMOJI_DATA  # type: ignore[return-value]
        except Exception as e:
            raise SystemExit(
                "Couldn't import emoji data. Check for a local 'emoji' module shadowing the package.\n"
                f"Original error: {e}"
            )


def _codepoints(s: str) -> str:
    return " ".join(f"U+{ord(c):04X}" for c in s)


def _safe_name(ch: str) -> str:
    try:
        return unicodedata.name(ch)
    except Exception:
        return ""


def _rows() -> list[tuple[str, str]]:
    """
    Build TAB-delimited rows: (glyph, display text).
    We show only the display text in fzf (via --with-nth=2..), but pass {1} (glyph) to preview.
    """
    EMOJI_DATA = _get_emoji_data()
    rows: list[tuple[str, str]] = []
    for ch, info in EMOJI_DATA.items():
        name = (info.get("en") or info.get("name") or _safe_name(ch) or "").title()
        group = str(info.get("group") or "")
        subgroup = str(info.get("subgroup") or info.get("sub_group") or "")
        aliases = info.get("aliases") or []
        tags = list(aliases or [])
        t = info.get("tags")
        if isinstance(t, (list, tuple)):
            tags.extend(t)
        right = " • ".join(filter(None, [name, group, subgroup]))
        trail = ("    " + " ".join(f"#{t}" for t in tags)) if tags else ""
        display = f"{ch}  {right}{trail}"
        rows.append((ch, display))
    rows.sort(key=lambda r: r[1].lower())
    return rows


def _ensure_fzf() -> str:
    fzf = shutil.which("fzf")
    if not fzf:
        raise SystemExit("fzf not found on PATH. Please install fzf.")
    return fzf


def _print_preview(glyph: str) -> None:
    EMOJI_DATA = _get_emoji_data()

    info = EMOJI_DATA.get(glyph, {})
    name = (info.get("en") or info.get("name") or _safe_name(glyph) or "").title()
    group = str(info.get("group") or "—")
    subgroup = str(info.get("subgroup") or info.get("sub_group") or "—")
    ver_val = info.get("E")
    ver = str(ver_val) if ver_val is not None else "—"
    status = str(info.get("status") or "—")
    aliases = [str(a) for a in (info.get("aliases") or [])]
    variations = [str(v) for v in (info.get("variations") or [])]
    tone_support = bool(info.get("skin_tone_support"))

    # Metadata table — ensure every cell is a string or Renderable
    tbl = Table(show_header=False, box=None, pad_edge=False)
    tbl.add_row("emoji", Text(f"{glyph}"))
    tbl.add_row("Name", Text(name or "—", style="bold"))
    tbl.add_row("Group", f"{group} / {subgroup}")
    tbl.add_row("Emoji ver.", ver)
    tbl.add_row("Status", status)
    tbl.add_row("Codepoints", _codepoints(glyph))
    if aliases:
        tbl.add_row("Aliases", ", ".join(aliases))
    console.print(tbl)

    # Variations
    if variations:
        console.print()
        console.print(Text("Variations", style="bold"))
        for v in variations[:8]:
            console.print(f"{v}   {_codepoints(v)}")
        if len(variations) > 8:
            console.print(f"… {len(variations) - 8} more")

    # Skin tones
    if tone_support:
        console.print()
        console.print(Text("Skin tones", style="bold"))
        tones = [glyph + m for m in SKIN_MODS]
        console.print("  " + "  ".join(tones))
        console.print("  " + "  ".join(_codepoints(t) for t in tones))
        console.print(
            Text(
                "Tip: After selecting the base emoji, append a skin tone modifier.",
                style="dim",
            )
        )


@app.command()
def preview(
    glyph: str = typer.Argument(..., help="(internal) emoji glyph passed from fzf"),
):
    """Internal command used by fzf: `emoji_fzf.py preview {1}`"""
    if not glyph:
        console.print("(no selection)")
        raise typer.Exit(0)
    _print_preview(glyph)


@app.command()
def pick(
    query: str = typer.Option("", "--query", "-q", help="Initial fzf query."),
    height: int = typer.Option(90, "--height", help="fzf height percentage (1-100)."),
    preview_width: int = typer.Option(
        60, "--preview-width", help="Preview width percentage (1-100)."
    ),
    multi: bool = typer.Option(True, "--multi/--no-multi", help="Enable multi-select."),
):
    """Open fzf to search all emojis with a Rich preview. Prints chosen emoji(s) to stdout."""
    fzf = _ensure_fzf()
    rows = _rows()

    delimiter = "\t"
    input_text = "\n".join(f"{g}{delimiter}{d}" for g, d in rows)

    this = os.path.abspath(sys.argv[0])
    py = shlex.quote(sys.executable)
    preview_cmd = f"{py} {shlex.quote(this)} preview {{1}}"

    opts: list[str] = [
        "--ansi",
        f"--height={height}%",
        "--reverse",
        "--prompt=emoji> ",
        "--delimiter",
        delimiter,
        "--with-nth=2..",
        "--preview",
        preview_cmd,
        f"--preview-window=right,{preview_width}%,wrap,border-rounded",
        "--bind",
        "?:toggle-preview",
        "--bind",
        "ctrl-a:select-all,ctrl-d:deselect-all",
    ]
    if multi:
        opts.append("--multi")
    if query:
        opts.extend(["--query", query])

    proc = subprocess.run(
        [fzf, *opts],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if proc.returncode != 0 or not proc.stdout.strip():
        raise typer.Exit(0)

    out: list[str] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        glyph = line.split(delimiter, 1)[0]
        if glyph:
            out.append(glyph)

    sys.stdout.write("\n".join(out))


if __name__ == "__main__":
    app()
