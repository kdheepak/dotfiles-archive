#!/usr/bin/env -S uv --quiet run --script
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "rich",
#     "typer",
# ]
# ///
"""
port-kill.py — find a TCP listener and terminate its owning process safely.

Features
- Cross‑platform discovery of listening TCP ports (macOS, Linux, Windows).
- Optional interactive picker via fzf if present; otherwise numeric menu.
- Filter by port or process name; kill by PID or by port.
- Safety prompts by default; --yes to skip. --dry-run to preview.
- Gentle SIGTERM/taskkill by default; configurable signal/force.

Examples
  # Fuzzy-pick a listener, then confirm kill
  port-kill.py

  # Kill whatever owns port 3000 (with confirmation)
  port-kill.py --port 3000

  # Use fzf explicitly (if installed)
  port-kill.py --fzf

  # Noninteractive: kill PID 12345 without prompt
  port-kill.py --pid 12345 --yes

  # Dry run
  port-kill.py --port 8000 --dry-run
"""

from __future__ import annotations

import argparse
import dataclasses as dc
import os
import platform
import re
import shlex
import signal
import subprocess
import sys
from typing import List, Optional, Tuple

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()

# --------------------------- Data model ---------------------------


@dc.dataclass
class Listener:
    pid: int
    port: int
    proto: str  # tcp
    process: str = "?"
    user: str = "?"
    cmd: str = "?"

    def display_row(self) -> str:
        return f"{self.port:>5}  {self.pid:>7}  {self.process:<20}  {self.cmd}"

    def fzf_line(self) -> str:
        # FZF will print this; keep it easy to parse back
        return f"port={self.port}\tpid={self.pid}\tproc={self.process}\tcmd={self.cmd}"


# --------------------------- Helpers ---------------------------


def which(cmd: str) -> Optional[str]:
    from shutil import which as _which

    return _which(cmd)


def run(cmd: List[str]) -> Tuple[int, str, str]:
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    return proc.returncode, proc.stdout, proc.stderr


# --------------------------- Discovery ---------------------------


def discover_listeners() -> List[Listener]:
    system = platform.system().lower()
    if system == "darwin":
        return discover_macos()
    elif system == "linux":
        return discover_linux()
    elif system == "windows":
        return discover_windows()
    else:
        print(f"Unsupported OS: {system}", file=sys.stderr)
        return []


def discover_macos() -> List[Listener]:
    # lsof output columns: COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME
    code, out, err = run(["/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN"])
    if code != 0:
        # fall back to plain lsof on PATH
        code, out, err = run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"])
        if code != 0:
            msg = err.strip() or out.strip() or "lsof failed"
            print(msg, file=sys.stderr)
            return []
    listeners: List[Listener] = []
    for line in out.splitlines()[1:]:  # skip header
        parts = re.split(r"\s+", line, maxsplit=8)
        if len(parts) < 9:
            continue
        cmd, pid, user, fd, _type, _device, _off, _node, name = parts
        # name looks like: *:3000 (LISTEN) or 127.0.0.1:8000 (LISTEN)
        m = re.search(r":(\d+) \(LISTEN\)$", name)
        if not m:
            continue
        port = int(m.group(1))
        listeners.append(
            Listener(
                pid=int(pid),
                port=port,
                proto="tcp",
                process=cmd,
                user=user,
                cmd=cmdline_of_pid(int(pid)),
            )
        )
    return dedupe(listeners)


def discover_linux() -> List[Listener]:
    # Prefer ss; it's standard on modern distros.
    # ss -H -ltnp  (Headerless, Listening, TCP, Numeric, show PIDs)
    if which("ss"):
        code, out, err = run(["ss", "-H", "-ltnp"])
        if code == 0:
            return parse_ss(out)
    # Fallback: netstat -ltnp
    if which("netstat"):
        code, out, err = run(["netstat", "-ltnp"])
        if code == 0:
            return parse_netstat_linux(out)
    print("Neither ss nor netstat found; cannot list listeners.", file=sys.stderr)
    return []


def parse_ss(out: str) -> List[Listener]:
    listeners: List[Listener] = []
    for line in out.splitlines():
        # Example: LISTEN 0 128 127.0.0.1:8000 *:* users:("python3",pid=1234,fd=7)
        cols = re.split(r"\s+", line)
        if len(cols) < 5:
            continue
        local = cols[3]
        mport = re.search(r":(\d+)$", local)
        if not mport:
            continue
        port = int(mport.group(1))
        mproc = re.search(r"users:\(([^\)]+)\)", line)
        proc_name = "?"
        pid = -1
        if mproc:
            # Could be multiple entries; pick first pid
            first = mproc.group(1)
            mpid = re.search(r"pid=(\d+)", first)
            mname = re.search(r"\"([^\"]+)\"", first)
            if mpid:
                pid = int(mpid.group(1))
            if mname:
                proc_name = mname.group(1)
        if pid == -1:
            continue
        listeners.append(
            Listener(
                pid=pid,
                port=port,
                proto="tcp",
                process=proc_name,
                cmd=cmdline_of_pid(pid),
            )
        )
    return dedupe(listeners)


def parse_netstat_linux(out: str) -> List[Listener]:
    listeners: List[Listener] = []
    for line in out.splitlines():
        if not line.startswith("tcp"):
            continue
        parts = re.split(r"\s+", line)
        if len(parts) < 7:
            continue
        local = parts[3]
        pid_prog = parts[-1]  # e.g., 1234/python3 or -
        m = re.search(r":(\d+)$", local)
        if not m:
            continue
        port = int(m.group(1))
        mpid = re.match(r"(\d+)/", pid_prog)
        if not mpid:
            continue
        pid = int(mpid.group(1))
        proc_name = pid_prog.split("/", 1)[1] if "/" in pid_prog else "?"
        listeners.append(
            Listener(
                pid=pid,
                port=port,
                proto="tcp",
                process=proc_name,
                cmd=cmdline_of_pid(pid),
            )
        )
    return dedupe(listeners)


def discover_windows() -> List[Listener]:
    code, out, err = run(["netstat", "-ano", "-p", "tcp"])
    if code != 0:
        print(err or out, file=sys.stderr)
        return []
    listeners: List[Listener] = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("TCP"):
            continue
        parts = re.split(r"\s+", line)
        if len(parts) < 5:
            continue
        local = parts[1]
        state = parts[3]
        pid_str = parts[4]
        if state.upper() != "LISTENING":
            continue
        m = re.search(r":(\d+)$", local)
        if not m:
            continue
        port = int(m.group(1))
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        proc_name = windows_process_name(pid)
        cmd = proc_name
        listeners.append(
            Listener(pid=pid, port=port, proto="tcp", process=proc_name, cmd=cmd)
        )
    return dedupe(listeners)


# --------------------------- Process info ---------------------------


def cmdline_of_pid(pid: int) -> str:
    # Best-effort: try /proc, then ps
    if os.path.exists(f"/proc/{pid}/cmdline"):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw = f.read().replace(b"\x00", b" ").decode(errors="ignore").strip()
                return raw or "?"
        except Exception:
            pass
    # ps -o command= -p PID
    if which("ps"):
        code, out, _ = run(["ps", "-o", "command=", "-p", str(pid)])
        if code == 0:
            return out.strip() or "?"
    return "?"


def windows_process_name(pid: int) -> str:
    # tasklist /FI "PID eq 1234" /FO CSV /NH
    code, out, err = run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"])
    if code != 0:
        return "?"
    line = out.strip().strip('"')
    if not line:
        return "?"
    # CSV columns: Image Name, PID, Session Name, Session#, Mem Usage
    parts = [p.strip('"') for p in line.split(",")]
    return parts[0] if parts else "?"


def dedupe(items: List[Listener]) -> List[Listener]:
    seen = set()
    out = []
    for it in items:
        key = (it.pid, it.port)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return sorted(out, key=lambda x: (x.port, x.pid))


# --------------------------- Selection ---------------------------


def pick_listener(listeners: List[Listener], use_fzf: bool) -> Optional[Listener]:
    if not listeners:
        print("No listening TCP ports found.")
        return None
    if use_fzf and which("fzf"):
        return pick_with_fzf(listeners)
    return pick_with_menu(listeners)


def pick_with_fzf(listeners: List[Listener]) -> Optional[Listener]:
    try:
        input_text = "\n".join(item.fzf_line() for item in listeners)
        proc = subprocess.run(
            ["fzf", "--ansi", "--no-sort", "--with-nth=1..", "--prompt=port> "],
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
        )
        sel = proc.stdout.strip()
        if not sel:
            return None
        m = re.search(r"pid=(\d+).*?port=(\d+)|port=(\d+).*?pid=(\d+)", sel)
        if m:
            pid = int(m.group(1) or m.group(4))
            port = int(m.group(2) or m.group(3))
            for item in listeners:
                if item.pid == pid and item.port == port:
                    return item
        # fallback: try to extract first number pairs
        nums = [int(n) for n in re.findall(r"\d+", sel)]
        if len(nums) >= 2:
            pid, port = nums[0], nums[1]
            for item in listeners:
                if item.pid == pid and item.port == port:
                    return item
    except Exception as e:
        print(f"fzf selection failed: {e}")
    return None


def pick_with_menu(listeners: List[Listener]) -> Optional[Listener]:
    print("Listening TCP ports:\n  PORT    PID      PROCESS               COMMAND")
    for idx, item in enumerate(listeners, 1):
        print(f"{idx:>3}) {item.display_row()}")
    try:
        raw = input("Select # to kill (empty to cancel): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return None
    try:
        i = int(raw)
        if 1 <= i <= len(listeners):
            return listeners[i - 1]
    except ValueError:
        pass
    print("Invalid selection.")
    return None


# --------------------------- Killing ---------------------------


def kill_pid(
    pid: int, *, force: bool, sig: Optional[int], dry_run: bool
) -> Tuple[bool, str]:
    system = platform.system().lower()
    if system == "windows":
        cmd = ["taskkill", "/PID", str(pid)] + (["/F"] if force else [])
        if dry_run:
            return True, "DRY: " + shlex.join(cmd)
        code, out, err = run(cmd)
        ok = code == 0
        return ok, (out or err or "")
    # POSIX
    sig_to_send = signal.SIGKILL if force else (sig or signal.SIGTERM)
    if dry_run:
        return True, f"DRY: os.kill({pid}, {sig_to_send})"
    try:
        os.kill(pid, sig_to_send)
        return True, f"Sent signal {sig_to_send} to PID {pid}"
    except ProcessLookupError:
        return False, f"PID {pid} not found"
    except PermissionError:
        return False, f"Permission denied to signal PID {pid} (try sudo?)"
    except Exception as e:
        return False, f"Failed to signal PID {pid}: {e}"


# --------------------------- CLI ---------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Find a listening TCP port and kill its owning process."
    )
    gsel = p.add_argument_group("Selection")
    gsel.add_argument("--port", type=int, help="Select by TCP port number")
    gsel.add_argument("--pid", type=int, help="Select by PID directly")
    gsel.add_argument("--filter", type=str, help="Substring filter on process or cmd")
    gsel.add_argument(
        "--fzf",
        action="store_true",
        help="Use fzf for interactive selection if available",
    )

    gact = p.add_argument_group("Action")
    gact.add_argument(
        "--yes", "-y", action="store_true", help="Proceed without confirmation"
    )
    gact.add_argument(
        "--force", "-f", action="store_true", help="Force kill (SIGKILL or taskkill /F)"
    )
    gact.add_argument(
        "--signal",
        type=int,
        help="POSIX signal number to send (default TERM; ignored on Windows)",
    )
    gact.add_argument(
        "--dry-run", action="store_true", help="Print what would be done, do not kill"
    )

    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    if args.pid:
        # kill directly
        selected_listener = Listener(
            pid=args.pid,
            port=-1,
            proto="tcp",
            process=cmdline_of_pid(args.pid) or "?",
            cmd=cmdline_of_pid(args.pid) or "?",
        )
    else:
        listeners = discover_listeners()
        if args.filter:
            kw = args.filter.lower()
            listeners = [
                item
                for item in listeners
                if kw in (item.process or "").lower() or kw in (item.cmd or "").lower()
            ]
        if args.port is not None:
            candidates = [item for item in listeners if item.port == args.port]
            if not candidates:
                print(f"No listener found on port {args.port}")
                return 1
            selected_listener = candidates[0]
        else:
            selected_listener = pick_listener(listeners, use_fzf=args.fzf)
            if selected_listener is None:
                return 1

    # Confirm
    if not args.yes and not args.dry_run:
        try:
            resp = (
                input(
                    f"Kill PID {selected_listener.pid} (proc='{selected_listener.process}', port={selected_listener.port if selected_listener.port != -1 else '?'} )? [y/N]: "
                )
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        if resp not in {"y", "yes"}:
            print("Aborted.")
            return 1

    ok, msg = kill_pid(
        selected_listener.pid, force=args.force, sig=args.signal, dry_run=args.dry_run
    )
    print(msg)
    return 0 if ok else 1


app = typer.Typer(
    add_completion=False, help="Find a listening TCP port and kill its owning process."
)


@app.command(help="Pick a listening TCP port and kill its owning process.")
def cli(
    port: Optional[int] = typer.Option(None, help="Select by TCP port number"),
    pid: Optional[int] = typer.Option(None, help="Select by PID directly"),
    filter: Optional[str] = typer.Option(
        None, "--filter", help="Substring filter on process or cmd"
    ),
    fzf: bool = typer.Option(
        False, help="Use fzf for interactive selection if available"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Proceed without confirmation"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force kill (SIGKILL or taskkill /F)"
    ),
    signal: Optional[int] = typer.Option(
        None, "--signal", help="POSIX signal number to send (ignored on Windows)"
    ),
    dry_run: bool = typer.Option(False, help="Print what would be done, do not kill"),
):
    # Build argv to reuse the existing logic
    argv = []
    if port is not None:
        argv += ["--port", str(port)]
    if pid is not None:
        argv += ["--pid", str(pid)]
    if filter:
        argv += ["--filter", filter]
    if fzf:
        argv += ["--fzf"]
    if yes:
        argv += ["--yes"]
    if force:
        argv += ["--force"]
    if signal is not None:
        argv += ["--signal", str(signal)]
    if dry_run:
        argv += ["--dry-run"]

    console.print(
        Panel.fit(
            "Running port-kill...",
            title="port-kill",
        )
    )
    rc = main(argv)
    raise typer.Exit(code=rc)


if __name__ == "__main__":
    app()
