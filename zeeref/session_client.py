#!/usr/bin/env python3

# This file is part of ZeeRef.
#
# ZeeRef is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# ZeeRef is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with ZeeRef.  If not, see <https://www.gnu.org/licenses/>.

"""Lightweight CLI client for sending images to a running ZeeRef session.

Uses only the Python standard library (no Qt, no Pillow) for fast startup.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


def socket_path(session_name: str) -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    return os.path.join(runtime, f"zeeref-{session_name}")


def recv_json(sock: socket.socket) -> dict:
    """Read a JSON line from a Unix socket."""
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return json.loads(buf.decode())


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="zeeref-add",
        description="Send images to a running ZeeRef session.",
    )
    parser.add_argument("session", help="Session name to connect to")
    parser.add_argument("files", nargs="*", help="Image files to add")
    parser.add_argument("--title", default=None, help="Title for the image(s)")
    parser.add_argument("--caption", default=None, help="Caption for the image(s)")
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read JSON payload array from stdin (each entry: {path, title?, caption?})",
    )
    args = parser.parse_args()

    if args.stdin:
        try:
            payload = json.loads(sys.stdin.read())
        except json.JSONDecodeError as e:
            print(f"Error: invalid JSON on stdin: {e}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(payload, list) or not payload:
            print("Error: expected non-empty JSON array on stdin", file=sys.stderr)
            sys.exit(1)
    else:
        if not args.files:
            print("Error: no files provided", file=sys.stderr)
            sys.exit(1)
        payload = []
        for f in args.files:
            p = Path(f).resolve()
            if not p.is_file():
                print(f"Warning: {f} does not exist, skipping", file=sys.stderr)
                continue
            entry: dict[str, str] = {"path": str(p)}
            if args.title:
                entry["title"] = args.title
            if args.caption:
                entry["caption"] = args.caption
            payload.append(entry)

    if not payload:
        print("Error: no valid files to send", file=sys.stderr)
        sys.exit(1)

    # Connect to session, starting one if needed
    sock_path = socket_path(args.session)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(sock_path)
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        # No session running — start one
        zeeref_bin = shutil.which("zeeref")
        if not zeeref_bin:
            print("Error: 'zeeref' not found in PATH", file=sys.stderr)
            sys.exit(1)
        print(f"Starting session '{args.session}'...", file=sys.stderr)
        subprocess.Popen(
            [zeeref_bin, "--session", args.session],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):  # 5 seconds
            time.sleep(0.1)
            if os.path.exists(sock_path):
                try:
                    sock.connect(sock_path)
                    break
                except OSError:
                    continue
        else:
            print(
                f"Error: timed out waiting for session '{args.session}' to start",
                file=sys.stderr,
            )
            sys.exit(1)

    msg = json.dumps({"type": "add", "payload": payload}) + "\n"
    sock.sendall(msg.encode())

    reply = recv_json(sock)
    sock.close()

    if reply.get("type") == "error":
        print(f"Error: {reply.get('message', 'unknown')}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"Added {len(payload)} image(s) to session '{args.session}'")


if __name__ == "__main__":
    main()
