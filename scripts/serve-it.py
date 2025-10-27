#!/usr/bin/env -S uv --quiet run --script
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "quart",
#     "htpy",
# ]
# ///

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from quart import Quart, Response, send_file
from quart.typing import ResponseReturnValue
from htpy import (
    html,
    head,
    body,
    meta,
    title as tag_title,
    script,
    link,
    div,
    h1,
    span,
    main as tag_main,
    header as tag_header,
    footer as tag_footer,
    svg,
    path as svg_path,
)

app = Quart(__name__)


# ---------------------------
# Models & helpers
# ---------------------------


@dataclass(slots=True)
class Entry:
    name: str
    is_dir: bool
    size: int
    mtime: float

    @property
    def kind(self) -> str:
        if self.is_dir:
            return "Folder"
        ext = Path(self.name).suffix.lower().lstrip(".")
        return f"{ext.upper()} File" if ext else "File"

    @property
    def display_size(self) -> str:
        if self.is_dir:
            return "—"
        size = self.size
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    @property
    def display_mtime(self) -> str:
        dt = datetime.fromtimestamp(self.mtime)
        return dt.strftime("%Y-%m-%d %H:%M")


def scan_cwd() -> list[Entry]:
    """Return entries for the current working directory (hidden files excluded)."""
    entries: list[Entry] = []
    for p in Path(".").iterdir():
        if p.name.startswith("."):
            continue
        stat = p.stat()
        entries.append(
            Entry(
                name=p.name, is_dir=p.is_dir(), size=stat.st_size, mtime=stat.st_mtime
            )
        )

    # Sort: folders first, then files, both alphabetically case-insensitive
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return entries


# --- Icon factories (inline SVG, Tailwind-colored) ---


def folder_icon() -> str:
    return str(
        svg(
            xmlns="http://www.w3.org/2000/svg",
            viewBox="0 0 24 24",
            fill="currentColor",
            class_="w-5 h-5 text-amber-500",
        )[
            svg_path(
                d="M2.25 6.75A2.25 2.25 0 0 1 4.5 4.5h4.318a2.25 2.25 0 0 1 1.59.66l1.232 1.232a2.25 2.25 0 0 0 1.59.658H19.5a2.25 2.25 0 0 1 2.25 2.25v7.5A2.25 2.25 0 0 1 19.5 19.5h-15A2.25 2.25 0 0 1 2.25 17.25v-10.5Z"
            ),
        ]
    )


def file_icon() -> str:
    return str(
        svg(
            xmlns="http://www.w3.org/2000/svg",
            viewBox="0 0 24 24",
            fill="currentColor",
            class_="w-5 h-5 text-slate-500",
        )[
            svg_path(
                d="M19.5 14.25v-2.379a2.25 2.25 0 0 0-.659-1.591l-4.121-4.121A2.25 2.25 0 0 0 13.128 5.5H8.25A2.25 2.25 0 0 0 6 7.75v8.5A2.25 2.25 0 0 0 8.25 18.5h9A2.25 2.25 0 0 0 19.5 16.25v-2Z"
            ),
        ]
    )


# ---------------------------
# Rendering
# ---------------------------


def render_explorer(entries: list[Entry]) -> str:
    rows = []
    for e in entries:
        icon_html = folder_icon() if e.is_dir else file_icon()
        rows.append(
            div(
                class_="group grid grid-cols-[1fr_160px_100px_180px] items-center gap-3 px-3 py-2 rounded-lg hover:bg-slate-100"
            )[
                div(class_="flex items-center gap-3 overflow-hidden")[
                    # icon
                    span(class_="shrink-0 inline-block")[icon_html],
                    # name
                    span(class_="truncate text-slate-800 font-medium")[e.name],
                ],
                span(class_="text-slate-500 text-sm")[e.kind],
                span(class_="text-slate-700 tabular-nums text-sm")[e.display_size],
                span(class_="text-slate-500 text-sm")[e.display_mtime],
            ]
        )

    if not rows:
        rows = [
            div(class_="px-3 py-10 text-center text-slate-500")["This folder is empty."]
        ]

    doc = html[
        head[
            meta(charset="utf-8"),
            meta(name="viewport", content="width=device-width, initial-scale=1"),
            tag_title["Directory"],
            script(src="https://cdn.tailwindcss.com"),
            link(rel="preconnect", href="https://fonts.googleapis.com"),
            link(rel="preconnect", href="https://fonts.gstatic.com", crossorigin=""),
            link(
                rel="stylesheet",
                href=(
                    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
                ),
            ),
        ],
        body(class_="min-h-screen bg-slate-50 text-slate-900 antialiased")[
            tag_main(
                class_=("max-w-5xl mx-auto p-6"),
                style=(
                    "font-family: Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', 'Apple Color Emoji', 'Segoe UI Emoji'"
                ),
            )[
                tag_header(class_="mb-6 flex items-center justify-between")[
                    h1(class_="text-xl font-semibold")["Files"],
                    span(class_="text-sm text-slate-500")[
                        f"{sum(1 for e in entries if e.is_dir)} folders • {sum(1 for e in entries if not e.is_dir)} files"
                    ],
                ],
                # column headers
                div(
                    class_="grid grid-cols-[1fr_160px_100px_180px] gap-3 px-3 py-2 text-xs uppercase tracking-wide text-slate-500"
                )[
                    span["Name"],
                    span["Kind"],
                    span["Size"],
                    span["Modified"],
                ],
                # rows
                div(class_="space-y-1")[*rows],
                tag_footer(class_="mt-8 text-xs text-slate-400")[
                    "Auto-generated because index.html was not found."
                ],
            ],
        ],
    ]

    return str(doc)


# ---------------------------
# Routes
# ---------------------------


@app.route("/")
async def serve_index() -> ResponseReturnValue:
    """Serve index.html if present; otherwise render a Tailwind-styled file explorer view (not clickable)."""
    if Path("index.html").exists():
        return await send_file("index.html")

    entries = scan_cwd()
    html_out = render_explorer(entries)
    return Response(html_out, mimetype="text/html; charset=utf-8")


@app.route("/<path:filename>")
async def serve_html(filename: str) -> ResponseReturnValue:
    """Serve any *.html in cwd; otherwise 404."""
    p = Path(filename)
    if p.suffix == ".html" and p.exists():
        return await send_file(str(p))
    return Response("404 Not Found", status=404, mimetype="text/plain")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
