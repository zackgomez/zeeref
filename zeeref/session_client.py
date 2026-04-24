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

"""CLI client for sending images to a running ZeeRef session.

Uses :class:`QLocalSocket` so the same code works on Linux, macOS, and
Windows (named pipes).  Runs without a :class:`QCoreApplication`; the
synchronous ``waitFor*`` methods are sufficient for a one-shot CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PyQt6 import QtNetwork

from zeeref.session import server_name


CONNECT_TIMEOUT_MS = 500
SPAWN_CONNECT_POLL_MS = 200
SPAWN_TIMEOUT_S = 10
WRITE_TIMEOUT_MS = 5000
REPLY_TIMEOUT_MS = 60000  # image inserts can take a while


def _read_reply(sock: QtNetwork.QLocalSocket) -> dict:
    """Read one \\n-terminated JSON line from the socket."""
    buf = b""
    deadline = time.monotonic() + REPLY_TIMEOUT_MS / 1000
    while b"\n" not in buf:
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        if not sock.waitForReadyRead(remaining_ms):
            break
        buf += bytes(sock.readAll())
    if b"\n" not in buf:
        raise TimeoutError("timed out waiting for server reply")
    line, _, _ = buf.partition(b"\n")
    return json.loads(line.decode())


def _spawn_and_connect(
    session: str, sock: QtNetwork.QLocalSocket
) -> Path | None:
    """Spawn ``zeeref --session`` and poll until connected.

    Returns the path to a captured stderr log on success, or raises
    :class:`SystemExit` on failure.
    """
    zeeref_bin = shutil.which("zeeref")
    if not zeeref_bin:
        sys.exit("Error: 'zeeref' not found in PATH")

    log = tempfile.NamedTemporaryFile(
        prefix=f"zeeref-{session}-spawn-",
        suffix=".log",
        delete=False,
    )
    print(f"Starting session '{session}'...", file=sys.stderr)
    subprocess.Popen(
        [zeeref_bin, "--session", session],
        stdout=subprocess.DEVNULL,
        stderr=log,
    )
    log.close()

    name = server_name(session)
    deadline = time.monotonic() + SPAWN_TIMEOUT_S
    while time.monotonic() < deadline:
        sock.connectToServer(name)
        if sock.waitForConnected(SPAWN_CONNECT_POLL_MS):
            return Path(log.name)
        sock.abort()

    stderr_tail = Path(log.name).read_text(errors="replace")[-2000:]
    msg = f"Error: timed out waiting for session '{session}' to start"
    if stderr_tail.strip():
        msg += f"\n--- zeeref stderr ---\n{stderr_tail}"
    sys.exit(msg)


def _build_payload(args: argparse.Namespace) -> list[dict]:
    if args.stdin:
        try:
            payload = json.loads(sys.stdin.read())
        except json.JSONDecodeError as e:
            sys.exit(f"Error: invalid JSON on stdin: {e}")
        if not isinstance(payload, list) or not payload:
            sys.exit("Error: expected non-empty JSON array on stdin")
        return payload

    if not args.files:
        sys.exit("Error: no files provided")
    payload: list[dict] = []
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
    return payload


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

    payload = _build_payload(args)
    if not payload:
        sys.exit("Error: no valid files to send")

    sock = QtNetwork.QLocalSocket()
    sock.connectToServer(server_name(args.session))
    spawn_log: Path | None = None
    if not sock.waitForConnected(CONNECT_TIMEOUT_MS):
        sock.abort()
        spawn_log = _spawn_and_connect(args.session, sock)

    msg = (json.dumps({"type": "add", "payload": payload}) + "\n").encode()
    sock.write(msg)
    if not sock.waitForBytesWritten(WRITE_TIMEOUT_MS):
        sys.exit(f"Error: write timed out: {sock.errorString()}")

    try:
        reply = _read_reply(sock)
    except (TimeoutError, json.JSONDecodeError) as e:
        sys.exit(f"Error reading reply: {e}")
    finally:
        sock.disconnectFromServer()

    if reply.get("type") == "error":
        sys.exit(f"Error: {reply.get('message', 'unknown')}")

    print(f"Added {len(payload)} image(s) to session '{args.session}'")
    if spawn_log is not None:
        try:
            os.unlink(spawn_log)
        except OSError:
            pass


if __name__ == "__main__":
    main()
