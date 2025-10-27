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
emoji-fzf: fuzzy-search emojis or all Unicode characters with a Rich preview
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import unicodedata
from typing import Iterable, Literal

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

CATEGORY_NAMES = {
    "Lu": "Letter, Uppercase",
    "Ll": "Letter, Lowercase",
    "Lt": "Letter, Titlecase",
    "Lm": "Letter, Modifier",
    "Lo": "Letter, Other",
    "Mn": "Mark, Nonspacing",
    "Mc": "Mark, Spacing Combining",
    "Me": "Mark, Enclosing",
    "Nd": "Number, Decimal",
    "Nl": "Number, Letter",
    "No": "Number, Other",
    "Pc": "Punctuation, Connector",
    "Pd": "Punctuation, Dash",
    "Ps": "Punctuation, Open",
    "Pe": "Punctuation, Close",
    "Pi": "Punctuation, Initial Quote",
    "Pf": "Punctuation, Final Quote",
    "Po": "Punctuation, Other",
    "Sm": "Symbol, Math",
    "Sc": "Symbol, Currency",
    "Sk": "Symbol, Modifier",
    "So": "Symbol, Other",
    "Zs": "Separator, Space",
    "Zl": "Separator, Line",
    "Zp": "Separator, Paragraph",
    "Cc": "Other, Control",
    "Cf": "Other, Format",
    "Cs": "Other, Surrogate",
    "Co": "Other, Private Use",
    "Cn": "Other, Unassigned",
}


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


def _utf8_bytes(s: str) -> str:
    return " ".join(f"0x{b:02X}" for b in s.encode("utf-8"))


def _safe_name(ch: str) -> str:
    try:
        return unicodedata.name(ch)
    except Exception:
        return ""


def _plane_of(cp: int) -> str:
    # https://www.unicode.org/roadmaps/
    if cp <= 0xFFFF:
        return "BMP"
    if cp <= 0x1FFFF:
        return "SMP"
    if cp <= 0x2FFFF:
        return "SIP"
    if cp <= 0x3FFFF:
        return "TIP"
    if cp <= 0x4FFFF:
        return "SSP"
    return "Plane?"


def _emoji_rows() -> list[tuple[str, str]]:
    """
    Build TAB-delimited rows: (glyph, display text) for emoji.
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


def _iter_unicode_chars(include_plane: Literal["bmp", "all"]) -> Iterable[str]:
    start = 0x0000
    end = 0x10FFFF if include_plane == "all" else 0xFFFF
    for cp in range(start, end + 1):
        # Skip surrogates; these are not real scalar values
        if 0xD800 <= cp <= 0xDFFF:
            continue
        ch = chr(cp)
        # Only include assigned codepoints (those with a name)
        try:
            _ = unicodedata.name(ch)
        except ValueError:
            continue
        yield ch


def _unicode_rows(include_plane: Literal["bmp", "all"]) -> list[tuple[str, str]]:
    """
    Build rows for all assigned Unicode scalar values (by chosen plane range).
    Display: "<glyph>  <Name> • <Category> • <Plane> [#tags]"
    """
    rows: list[tuple[str, str]] = []
    for ch in _iter_unicode_chars(include_plane):
        name = _safe_name(ch)
        cat = unicodedata.category(ch)
        cat_hr = CATEGORY_NAMES.get(cat, cat)
        cp = ord(ch)
        plane = _plane_of(cp)
        tags = []
        if unicodedata.combining(ch):
            tags.append("combining")
        if cat.startswith("C"):
            tags.append("control")
        if unicodedata.mirrored(ch):
            tags.append("mirrored")
        right = " • ".join([name or "—", cat_hr, plane])
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


def _print_emoji_preview(glyph: str) -> bool:
    """
    Try printing an emoji-focused preview.
    Returns True if glyph looked up via emoji data, else False to fall back.
    """
    EMOJI_DATA = _get_emoji_data()
    info = EMOJI_DATA.get(glyph)
    if info is None:
        return False

    name = (info.get("en") or info.get("name") or _safe_name(glyph) or "").title()
    group = str(info.get("group") or "—")
    subgroup = str(info.get("subgroup") or info.get("sub_group") or "—")
    ver_val = info.get("E")
    ver = str(ver_val) if ver_val is not None else "—"
    status = str(info.get("status") or "—")
    # aliases = [str(a) for a in (info.get("aliases") or [])]
    variations = [str(v) for v in (info.get("variations") or [])]
    tone_support = bool(info.get("skin_tone_support"))

    tbl = Table(show_header=False, box=None, pad_edge=False)
    tbl.add_row("Glyph", Text(f"{glyph}"))
    tbl.add_row("Name", Text(name or "—", style="bold"))
    tbl.add_row("Group", f"{group} / {subgroup}")
    tbl.add_row("Emoji ver.", ver)
    tbl.add_row("Status", status)
    tbl.add_row("Codepoints", _codepoints(glyph))
    tbl.add_row("UTF-8", _utf8_bytes(glyph))
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

    return True


def _print_unicode_preview(glyph: str) -> None:
    name = _safe_name(glyph) or "—"
    cat = unicodedata.category(glyph)
    cat_hr = CATEGORY_NAMES.get(cat, cat)
    dec = None
    try:
        dec = unicodedata.decimal(glyph)
    except Exception:
        pass
    dig = None
    try:
        dig = unicodedata.digit(glyph)
    except Exception:
        pass
    num = None
    try:
        num = unicodedata.numeric(glyph)
    except Exception:
        pass

    bidi = unicodedata.bidirectional(glyph) or "—"
    eaw = unicodedata.east_asian_width(glyph) or "—"
    comb = unicodedata.combining(glyph)
    mirrored = "Yes" if unicodedata.mirrored(glyph) else "No"
    decomp = unicodedata.decomposition(glyph) or "—"

    tbl = Table(show_header=False, box=None, pad_edge=False)
    tbl.add_row("Glyph", Text(f"{glyph}"))
    tbl.add_row("Name", Text(name, style="bold"))
    tbl.add_row("Category", f"{cat} ({cat_hr})")
    tbl.add_row("Codepoints", _codepoints(glyph))
    tbl.add_row("UTF-8", _utf8_bytes(glyph))
    tbl.add_row("Combining", str(comb))
    tbl.add_row("Bidi", bidi)
    tbl.add_row("East Asian Width", eaw)
    tbl.add_row("Mirrored", mirrored)
    tbl.add_row("Decomposition", decomp)
    if dec is not None:
        tbl.add_row("Decimal", str(dec))
    if dig is not None:
        tbl.add_row("Digit", str(dig))
    if num is not None:
        tbl.add_row("Numeric", str(num))
    console.print(tbl)


def _print_preview(glyph: str) -> None:
    # Prefer rich emoji preview; fall back to generic Unicode preview.
    if not _print_emoji_preview(glyph):
        _print_unicode_preview(glyph)


def _build_rows(
    scope: Literal["emoji", "unicode", "both"],
    plane: Literal["bmp", "all"],
) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if scope in ("emoji", "both"):
        rows.extend(_emoji_rows())
    if scope in ("unicode", "both"):
        rows.extend(_unicode_rows(plane))
    # Sort once globally so "both" merges nicely
    rows.sort(key=lambda r: r[1].lower())
    return rows


@app.command()
def preview(
    glyph: str = typer.Argument(..., help="(internal) glyph passed from fzf"),
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
    scope: Literal["emoji", "unicode", "both"] = typer.Option(
        "emoji",
        "--scope",
        help="Search set: 'emoji' (fast), 'unicode' (all assigned codepoints), or 'both'.",
    ),
    plane: Literal["bmp", "all"] = typer.Option(
        "bmp",
        "--plane",
        help="For scope 'unicode' or 'both': limit to BMP (U+0000–U+FFFF) or include ALL planes.",
    ),
):
    """
    Open fzf to search emojis and/or all Unicode characters with a Rich preview.
    Prints chosen glyph(s) to stdout.
    """
    fzf = _ensure_fzf()
    rows = _build_rows(scope, plane)

    delimiter = "\t"
    input_text = "\n".join(f"{g}{delimiter}{d}" for g, d in rows)

    this = os.path.abspath(sys.argv[0])
    py = shlex.quote(sys.executable)
    preview_cmd = f"{py} {shlex.quote(this)} preview {{1}}"

    prompt = {
        "emoji": "emoji> ",
        "unicode": "unicode> ",
        "both": "glyph> ",
    }[scope]

    opts: list[str] = [
        "--ansi",
        f"--height={height}%",
        "--reverse",
        f"--prompt={prompt}",
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
    sys.stdout.write("\n")


if __name__ == "__main__":
    app()
