#!/usr/bin/env -S uv --quiet run --script
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "rich",
# ]
# ///


import os
import re
import tty
import termios
import select
import time
import shutil
from rich.console import Console, Group
from rich.columns import Columns
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

console = Console()
WIDTH = shutil.get_terminal_size((100, 24)).columns

# ---------- OSC helpers ----------
BEL = b"\x07"
ST = b"\x1b\\"
RE_OSC4 = re.compile(rb"\x1b\]4;(\d+);([^\x07\x1b]+)(?:\x07|\x1b\\)")
RE_OSC1X = re.compile(rb"\x1b\](1[0-9]);([^\x07\x1b]+)(?:\x07|\x1b\\)")


def _read_reply(fd, timeout=0.25):
    end = time.time() + timeout
    buf = bytearray()
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], max(0, end - time.time()))
        if not r:
            break
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        buf.extend(chunk)
        if BEL in buf or ST in buf:
            break
    return bytes(buf)


def _to_hex(s: str) -> str:
    s = s.strip().lower()
    if s.startswith("rgb:"):
        parts = s[4:].split("/")

        def pick2(h):
            return (h.strip() + "0" * 2)[:2]

        r, g, b = (pick2(p) for p in parts[:3])
        return f"#{r}{g}{b}"
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            return "#" + "".join(c * 2 for c in h)
        if len(h) == 6:
            return "#" + h
        if len(h) == 12:
            return "#" + h[0:2] + h[4:6] + h[8:10]
    return s  # unknown; just show it


def query_osc_palette(indices):
    out = {}
    try:
        with open("/dev/tty", "rb+", buffering=0) as t:
            fd = t.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                for n in indices:
                    os.write(fd, f"\x1b]4;{n};?\x07".encode())
                    rep = _read_reply(fd)
                    for m in RE_OSC4.finditer(rep):
                        if int(m.group(1)) == n:
                            out[n] = _to_hex(m.group(2).decode(errors="replace"))
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        pass
    return out


def query_dynamic(codes=(10, 11, 12, 13, 14, 17, 19)):
    out = {}
    try:
        with open("/dev/tty", "rb+", buffering=0) as t:
            fd = t.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                for code in codes:
                    os.write(fd, f"\x1b]{code};?\x07".encode())
                    rep = _read_reply(fd)
                    for m in RE_OSC1X.finditer(rep):
                        if int(m.group(1)) == code:
                            out[code] = _to_hex(m.group(2).decode(errors="replace"))
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        pass
    return out


# ---------- Small rendering helpers ----------
def swatch_256_bg(idx: int, width: int = 1) -> Text:
    t = Text(" " * width)
    t.stylize(f"on color({idx})")
    return t


def swatch_true_bg(hexval: str, width: int = 2) -> Text:
    t = Text(" " * width)
    if isinstance(hexval, str) and hexval.startswith("#") and len(hexval) == 7:
        t.stylize(f"on {hexval}")
    return t


def colored_hex_text(hexval: str) -> Text:
    if isinstance(hexval, str) and hexval.startswith("#") and len(hexval) == 7:
        return Text(hexval, style=hexval)
    return Text(hexval or "—", style="italic dim")


# ---------- Sections ----------
def _contrast(hex_or_none: str, idx: int) -> str:
    """
    Return 'black' or 'white' for readable text on the given background.
    Prefers hex (if OSC 4 returned it). Falls back to a heuristic per ANSI index.
    """
    if (
        isinstance(hex_or_none, str)
        and hex_or_none.startswith("#")
        and len(hex_or_none) == 7
    ):
        r = int(hex_or_none[1:3], 16)
        g = int(hex_or_none[3:5], 16)
        b = int(hex_or_none[5:7], 16)
        luma = 0.299 * r + 0.587 * g + 0.114 * b
        return "black" if luma >= 140 else "white"
    # sensible fallback for typical palettes
    dark_bg = {0, 1, 4, 5, 6, 8, 9, 12, 13, 14}
    return "white" if idx in dark_bg else "black"


def section_ansi_0_15(pal_hex: dict) -> Panel:
    base_names = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]

    def cell(idx: int) -> Text:
        hx = pal_hex.get(idx)
        t = Text()
        sw = Text(f" {hx or '—'} ")
        if isinstance(hx, str) and hx.startswith("#") and len(hx) == 7:
            sw.stylize(f"{_contrast(hx, idx)} on {hx}")  # truecolor bg
        else:
            sw.stylize(f"{_contrast(hx, idx)} on color({idx})")  # 256-color fallback
        t.append_text(sw)
        return t

    # Grid: 1 row-label column + 8 color columns
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(justify="left", no_wrap=True)  # row label (normal/bright)
    for _ in range(8):
        tbl.add_column(justify="center", no_wrap=True)

    # Header row: empty cell at [0,0], then color names across
    header_cells = [Text("", style="bold")] + [
        Text(n, style="bold") for n in base_names
    ]
    tbl.add_row(*header_cells)

    # Body rows
    for label, offset in (("normal", 0), ("bright", 8)):
        row = [Text(label)]
        for base_idx in range(8):
            row.append(cell(base_idx + offset))
        tbl.add_row(*row)

    return Panel(tbl, title="ANSI 0–15 (OSC 4)")


def section_dynamic(dyn_hex: dict) -> Panel:
    labels = {
        10: "foreground",
        11: "background",
        12: "cursor",
        13: "pointer_fg",
        14: "pointer_bg",
        17: "highlight_bg",
        19: "highlight_fg",
    }
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(justify="right", no_wrap=True)
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=True)
    tbl.add_column(no_wrap=True)
    for code in (10, 11, 12, 13, 14, 17, 19):
        hx = dyn_hex.get(code)
        tbl.add_row(
            Text(str(code), style="dim"),
            Text(labels[code]),
            colored_hex_text(hx),
            swatch_true_bg(hx, 3),
        )
    return Panel(tbl, title="Dynamic UI (OSC 10–19)")


def section_cube() -> Panel:
    # Color cube with Taskwarrior-style labels:
    #   header row:      0            1            2            3            4            5
    #   header sub-row:  0 1 2 3 4 5  0 1 2 3 4 5  ...
    #   row labels:      0..5 down the left side (G axis)
    grid = Table.grid(padding=(0, 1))
    grid.add_column(justify="right", no_wrap=True)  # row labels (G)
    for _ in range(6):
        grid.add_column(no_wrap=True)  # columns for R=0..5

    # Header line: "0 1 2 3 4 5" centered over each R column
    header_top = [Text(" ", style="dim")] + [
        Text(f"{r}", style="dim") for r in range(6)
    ]
    grid.add_row(*header_top)

    # Header sub-line: "0 1 2 3 4 5" within each column (B axis legend)
    header_seq = [Text(" ", style="dim")] + [
        Text("0 1 2 3 4 5", style="dim") for _ in range(6)
    ]
    grid.add_row(*header_seq)

    # Body: rows for G=0..5, each column shows a strip for B=0..5 at that R
    for g in range(6):
        row_cells = [Text(f"{g}", style="dim")]
        for r in range(6):
            strip = Text()
            for b in range(6):
                idx = 16 + 36 * r + 6 * g + b
                strip.append_text(swatch_256_bg(idx, 2))
            row_cells.append(strip)
        grid.add_row(*row_cells)

    title = "Color cube rgb000 – rgb555 (also color16 – color231)"
    return Panel(grid, title=title)


def section_gray() -> Panel:
    # One-line gray ramp (232..255) with Taskwarrior-style labels on top.
    indices = [232 + i for i in range(24)]
    swatch_width = 2  # width of each gray swatch block

    # Build the swatch row
    swatches = Text()
    for i in indices:
        swatches.append_text(swatch_256_bg(i, swatch_width))

    # Build the label row: "0 1 2 . . .           . . . 23"
    left_label = "0 1 2 . . . "
    right_label = ". . . 23"
    total_width = len(indices) * (swatch_width)
    middle_spaces = max(1, total_width - len(left_label) - len(right_label))
    label = Text(left_label + (" " * middle_spaces) + right_label, style="dim")

    # Compose panel: label on top, swatches below
    grid = Table.grid(padding=0)
    grid.add_column(no_wrap=True)
    grid.add_row(label)
    grid.add_row(swatches)

    return Panel(grid, title="Gray ramp gray0 – gray23 (also color232 – color255)")


# ---------- Main ----------
def main():
    pal_hex = query_osc_palette(range(16))
    dyn_hex = query_dynamic()

    s1 = section_ansi_0_15(pal_hex)
    s2 = section_dynamic(dyn_hex)
    s3 = section_cube()  # always full cube
    s4 = section_gray()

    if WIDTH >= 120:
        top = Columns([s1, s2], equal=True, expand=True)
        bottom = Columns([s3, s4], equal=True, expand=True)
        console.print(Group(top, bottom))
    else:
        console.print(Group(s1, s2, s3, s4))


if __name__ == "__main__":
    main()
