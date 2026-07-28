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

"""CLI client for interacting with a running ZeeRef session.

Uses :class:`QLocalSocket` so the same code works on Linux, macOS, and
Windows (named pipes).  Runs without a :class:`QCoreApplication`; the
synchronous ``waitFor*`` methods are sufficient for a one-shot CLI.

The CLI consumes the server's ``hello`` greeting on every connect and
validates that ``protocol_version`` matches the client's expectation.
Only ``add`` (and future ``add-text``) auto-spawn a session — other
subcommands error fast if the session is not running.  Use ``start``,
``new``, or ``open`` to launch a session explicitly.
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

from zeeref.session import PROTOCOL_VERSION, server_name


CONNECT_TIMEOUT_MS = 500
SPAWN_CONNECT_POLL_MS = 200
SPAWN_TIMEOUT_S = 10
WRITE_TIMEOUT_MS = 5000
HELLO_TIMEOUT_MS = 5000
REPLY_TIMEOUT_MS = 60000  # inserts/opens can take a while


# -- low-level socket helpers -----------------------------------------------


def _read_line(sock: QtNetwork.QLocalSocket, timeout_ms: int) -> bytes:
    """Read one \\n-terminated line from *sock*; raises on timeout."""
    buf = b""
    deadline = time.monotonic() + timeout_ms / 1000
    while b"\n" not in buf:
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        if not sock.waitForReadyRead(remaining_ms):
            break
        buf += sock.readAll().data()
    if b"\n" not in buf:
        raise TimeoutError("timed out waiting for server reply")
    line, _, _ = buf.partition(b"\n")
    return line


def _read_reply(
    sock: QtNetwork.QLocalSocket, timeout_ms: int = REPLY_TIMEOUT_MS
) -> dict:
    line = _read_line(sock, timeout_ms)
    return json.loads(line.decode())


def _send(sock: QtNetwork.QLocalSocket, message: dict) -> None:
    data = (json.dumps(message) + "\n").encode()
    sock.write(data)
    if not sock.waitForBytesWritten(WRITE_TIMEOUT_MS):
        sys.exit(f"Error: write timed out: {sock.errorString()}")


def _read_hello(sock: QtNetwork.QLocalSocket) -> dict:
    """Read the server's hello greeting and validate protocol version."""
    try:
        line = _read_line(sock, HELLO_TIMEOUT_MS)
    except TimeoutError:
        sys.exit("Error: timed out waiting for server hello")
    try:
        hello = json.loads(line.decode())
    except json.JSONDecodeError as e:
        sys.exit(f"Error: invalid hello from server: {e}")
    if hello.get("type") != "hello":
        sys.exit(f"Error: expected hello, got: {hello}")
    server_proto = hello.get("protocol_version")
    if server_proto != PROTOCOL_VERSION:
        sys.exit(
            f"Error: protocol mismatch (client={PROTOCOL_VERSION}, "
            f"server={server_proto})"
        )
    return hello


# -- connect / spawn --------------------------------------------------------


def _try_connect(session: str) -> QtNetwork.QLocalSocket | None:
    """Try to connect to *session*. Returns sock on success, None if down."""
    sock = QtNetwork.QLocalSocket()
    sock.connectToServer(server_name(session))
    if sock.waitForConnected(CONNECT_TIMEOUT_MS):
        return sock
    sock.abort()
    return None


def _spawn_zeeref(session: str, extra_args: list[str] | None = None) -> Path:
    """Spawn ``zeeref --session SESSION [extra_args...]``. Returns log path."""
    zeeref_bin = shutil.which("zeeref")
    if not zeeref_bin:
        sys.exit("Error: 'zeeref' not found in PATH")

    log = tempfile.NamedTemporaryFile(
        prefix=f"zeeref-{session}-spawn-",
        suffix=".log",
        delete=False,
    )
    argv = [zeeref_bin, "--session", session]
    if extra_args:
        argv.extend(extra_args)
    print(f"Starting session '{session}'...", file=sys.stderr)
    subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=log.fileno())
    log.close()
    return Path(log.name)


def _wait_for_connect(session: str) -> QtNetwork.QLocalSocket:
    """Poll-connect to *session* until SPAWN_TIMEOUT_S, then sys.exit."""
    name = server_name(session)
    deadline = time.monotonic() + SPAWN_TIMEOUT_S
    while time.monotonic() < deadline:
        sock = QtNetwork.QLocalSocket()
        sock.connectToServer(name)
        if sock.waitForConnected(SPAWN_CONNECT_POLL_MS):
            return sock
        sock.abort()
    sys.exit(f"Error: timed out connecting to session '{session}'")


def _spawn_and_connect(
    session: str, extra_args: list[str] | None = None
) -> tuple[QtNetwork.QLocalSocket, Path]:
    """Spawn zeeref with *extra_args* and poll until connected."""
    log_path = _spawn_zeeref(session, extra_args)
    try:
        sock = _wait_for_connect(session)
    except SystemExit:
        tail = log_path.read_text(errors="replace")[-2000:]
        if tail.strip():
            print(
                f"--- zeeref stderr ---\n{tail}",
                file=sys.stderr,
            )
        raise
    return sock, log_path


def _cleanup_spawn_log(log_path: Path | None) -> None:
    if log_path is None:
        return
    try:
        os.unlink(log_path)
    except OSError:
        pass


def _connect_or_die(session: str) -> tuple[QtNetwork.QLocalSocket, dict]:
    """Connect to *session* or sys.exit. Returns (sock, hello). No spawning."""
    sock = _try_connect(session)
    if sock is None:
        sys.exit(f"Error: session '{session}' is not running")
    assert sock is not None
    hello = _read_hello(sock)
    return sock, hello


def _connect_or_spawn(
    session: str, extra_args: list[str] | None = None
) -> tuple[QtNetwork.QLocalSocket, Path | None]:
    """Connect to *session*; spawn with *extra_args* if down. Reads hello."""
    sock = _try_connect(session)
    if sock is not None:
        _read_hello(sock)
        return sock, None
    sock, log_path = _spawn_and_connect(session, extra_args)
    _read_hello(sock)
    return sock, log_path


# -- request/reply ----------------------------------------------------------


def _request(sock: QtNetwork.QLocalSocket, message: dict) -> dict:
    """Send *message* and read one reply. Exits on transport error."""
    _send(sock, message)
    try:
        return _read_reply(sock)
    except (TimeoutError, json.JSONDecodeError) as e:
        sys.exit(f"Error reading reply: {e}")


def _emit(obj: dict) -> None:
    """Print a JSON object on stdout."""
    print(json.dumps(obj))


def _exit_on_error_reply(reply: dict) -> None:
    if reply.get("type") == "error":
        sys.exit(f"Error: {reply.get('message', 'unknown')}")


def _strip_type(d: dict) -> dict:
    """Drop the wire-envelope ``type`` field for cleaner user-facing output."""
    return {k: v for k, v in d.items() if k != "type"}


# -- payload helpers --------------------------------------------------------


_TRANSFORM_FIELDS: tuple[str, ...] = (
    "x",
    "y",
    "scale",
    "rotation",
    "z",
    "flip",
    "opacity",
)


def _shared_transform_overrides(args: argparse.Namespace) -> dict:
    """Collect non-None transform-flag values from *args*."""
    out: dict = {}
    for f in _TRANSFORM_FIELDS:
        v = getattr(args, f, None)
        if v is not None:
            out[f] = v
    return out


def _build_add_payload(args: argparse.Namespace) -> list[dict]:
    overrides = _shared_transform_overrides(args)
    if args.stdin:
        try:
            payload = json.loads(sys.stdin.read())
        except json.JSONDecodeError as e:
            sys.exit(f"Error: invalid JSON on stdin: {e}")
        if not isinstance(payload, list) or not payload:
            sys.exit("Error: expected non-empty JSON array on stdin")
        # CLI flags fill in any field not present in the per-image entry.
        if overrides:
            payload = [{**overrides, **entry} for entry in payload]
        return payload

    if not args.files:
        sys.exit("Error: no files provided")
    payload: list[dict] = []
    for f in args.files:
        p = Path(f).resolve()
        if not p.is_file():
            print(f"Warning: {f} does not exist, skipping", file=sys.stderr)
            continue
        entry: dict = {"path": str(p), **overrides}
        if args.title:
            entry["title"] = args.title
        if args.caption:
            entry["caption"] = args.caption
        payload.append(entry)
    return payload


# -- subcommand handlers ---------------------------------------------------


def _cmd_add(args: argparse.Namespace) -> None:
    payload = _build_add_payload(args)
    if not payload:
        sys.exit("Error: no valid files to send")

    sock, spawn_log = _connect_or_spawn(args.session)
    try:
        reply = _request(sock, {"type": "add", "payload": payload})
        _exit_on_error_reply(reply)
        _emit(
            {
                "ok": True,
                "session": args.session,
                "added": len(payload),
                "ids": list(reply.get("ids") or []),
            }
        )
    finally:
        sock.disconnectFromServer()
        _cleanup_spawn_log(spawn_log)


def _build_add_text_payload(args: argparse.Namespace) -> list[dict]:
    overrides = _shared_transform_overrides(args)
    if args.wrap is not None:
        overrides["wrap"] = args.wrap
    if args.stdin:
        try:
            payload = json.loads(sys.stdin.read())
        except json.JSONDecodeError as e:
            sys.exit(f"Error: invalid JSON on stdin: {e}")
        if not isinstance(payload, list) or not payload:
            sys.exit("Error: expected non-empty JSON array on stdin")
        if overrides:
            payload = [{**overrides, **entry} for entry in payload]
        return payload

    if not args.text:
        sys.exit("Error: no text provided")
    return [{"text": args.text, **overrides}]


def _cmd_add_text(args: argparse.Namespace) -> None:
    payload = _build_add_text_payload(args)
    sock, spawn_log = _connect_or_spawn(args.session)
    try:
        reply = _request(sock, {"type": "add_text", "payload": payload})
        _exit_on_error_reply(reply)
        _emit(
            {
                "ok": True,
                "session": args.session,
                "added": len(payload),
                "ids": list(reply.get("ids") or []),
            }
        )
    finally:
        sock.disconnectFromServer()
        _cleanup_spawn_log(spawn_log)


def _cmd_start(args: argparse.Namespace) -> None:
    sock, spawn_log = _connect_or_spawn(args.session)
    try:
        reply = _request(sock, {"type": "status"})
        _exit_on_error_reply(reply)
        _emit({"ok": True, "session": args.session, "status": reply})
    finally:
        sock.disconnectFromServer()
        _cleanup_spawn_log(spawn_log)


def _cmd_new(args: argparse.Namespace) -> None:
    # When session is down, "new" is satisfied by spawning a fresh empty
    # session — no IPC new needed.  When running, send the new message.
    existing = _try_connect(args.session)
    if existing is None:
        sock, spawn_log = _spawn_and_connect(args.session)
        try:
            _read_hello(sock)
            _emit({"ok": True, "session": args.session, "spawned": True})
        finally:
            sock.disconnectFromServer()
            _cleanup_spawn_log(spawn_log)
        return

    _read_hello(existing)
    try:
        reply = _request(existing, {"type": "new", "force": args.force})
        _exit_on_error_reply(reply)
        _emit({"ok": True, "session": args.session, "spawned": False})
    finally:
        existing.disconnectFromServer()


def _cmd_open(args: argparse.Namespace) -> None:
    path = Path(args.path).resolve()
    if not path.is_file():
        sys.exit(f"Error: file not found: {args.path}")

    existing = _try_connect(args.session)
    if existing is None:
        sock, spawn_log = _spawn_and_connect(args.session, [str(path)])
        try:
            _read_hello(sock)
            # After spawn-with-file, ask the fresh session for its status
            # so the reply matches the IPC-load branch.
            status_reply = _request(sock, {"type": "status"})
            _emit(
                {
                    "ok": True,
                    "session": args.session,
                    "path": str(path),
                    "spawned": True,
                    "status": _strip_type(status_reply),
                }
            )
        finally:
            sock.disconnectFromServer()
            _cleanup_spawn_log(spawn_log)
        return

    _read_hello(existing)
    try:
        reply = _request(
            existing,
            {"type": "open", "path": str(path), "force": args.force},
        )
        _exit_on_error_reply(reply)
        _emit(
            {
                "ok": True,
                "session": args.session,
                "path": str(path),
                "spawned": False,
                "status": reply.get("status"),
            }
        )
    finally:
        existing.disconnectFromServer()


def _cmd_save(args: argparse.Namespace) -> None:
    message: dict = {"type": "save", "force": args.force}
    if args.path is not None:
        target = Path(args.path).resolve()
        if not target.parent.is_dir():
            sys.exit(f"Error: directory not found: {target.parent}")
        message["path"] = str(target)

    sock, hello = _connect_or_die(args.session)
    try:
        reply = _request(sock, message)
        _exit_on_error_reply(reply)
        status = reply.get("status") or {}
        _emit(
            {
                "ok": True,
                "session": args.session,
                "path": status.get("loaded_file"),
                "status": status,
            }
        )
    finally:
        sock.disconnectFromServer()


def _cmd_ping(args: argparse.Namespace) -> None:
    sock, hello = _connect_or_die(args.session)
    try:
        reply = _request(sock, {"type": "ping"})
        _exit_on_error_reply(reply)
        _emit(
            {
                "ok": True,
                "session": args.session,
                "protocol_version": hello.get("protocol_version"),
                "app_version": hello.get("app_version"),
            }
        )
    finally:
        sock.disconnectFromServer()


def _cmd_status(args: argparse.Namespace) -> None:
    sock, hello = _connect_or_die(args.session)
    try:
        reply = _request(sock, {"type": "status"})
        _exit_on_error_reply(reply)
        _emit({"ok": True, "session": args.session, "status": _strip_type(reply)})
    finally:
        sock.disconnectFromServer()


def _cmd_list(args: argparse.Namespace) -> None:
    sock, hello = _connect_or_die(args.session)
    try:
        reply = _request(sock, {"type": "list"})
        _exit_on_error_reply(reply)
        _emit(
            {
                "ok": True,
                "session": args.session,
                "items": reply.get("items", []),
            }
        )
    finally:
        sock.disconnectFromServer()


def _cmd_get(args: argparse.Namespace) -> None:
    sock, hello = _connect_or_die(args.session)
    try:
        reply = _request(sock, {"type": "get", "id": args.id})
        _exit_on_error_reply(reply)
        item = reply.get("item")
        if item is None:
            sys.exit(f"Error: no item with id '{args.id}'")
        _emit({"ok": True, "session": args.session, "item": item})
    finally:
        sock.disconnectFromServer()


def _cmd_view(args: argparse.Namespace) -> None:
    sock, hello = _connect_or_die(args.session)
    try:
        reply = _request(sock, {"type": "view"})
        _exit_on_error_reply(reply)
        _emit({"ok": True, "session": args.session, "view": _strip_type(reply)})
    finally:
        sock.disconnectFromServer()


_EDIT_METADATA_FIELDS: tuple[str, ...] = ("title", "caption", "text")

# Edit flags that aren't transforms and aren't string metadata.
_EDIT_EXTRA_FIELDS: tuple[str, ...] = ("wrap",)


def _build_edit_payload(args: argparse.Namespace) -> list[dict]:
    if args.stdin:
        try:
            payload = json.loads(sys.stdin.read())
        except json.JSONDecodeError as e:
            sys.exit(f"Error: invalid JSON on stdin: {e}")
        if not isinstance(payload, list) or not payload:
            sys.exit("Error: expected non-empty JSON array on stdin")
        return payload

    if not args.id:
        sys.exit("Error: edit requires an item id")
    entry: dict = {"id": args.id}
    for f in _TRANSFORM_FIELDS:
        v = getattr(args, f, None)
        if v is not None:
            entry[f] = v
    for f in _EDIT_METADATA_FIELDS + _EDIT_EXTRA_FIELDS:
        v = getattr(args, f, None)
        if v is not None:
            entry[f] = v
    if len(entry) == 1:
        sys.exit("Error: edit requires at least one field to change")
    return [entry]


def _cmd_edit(args: argparse.Namespace) -> None:
    payload = _build_edit_payload(args)
    sock, hello = _connect_or_die(args.session)
    try:
        reply = _request(sock, {"type": "edit", "payload": payload})
        _exit_on_error_reply(reply)
        _emit(
            {
                "ok": True,
                "session": args.session,
                "edited": len(payload),
                "items": list(reply.get("items") or []),
            }
        )
    finally:
        sock.disconnectFromServer()


def _cmd_delete(args: argparse.Namespace) -> None:
    if not args.ids:
        sys.exit("Error: delete requires at least one id")
    sock, hello = _connect_or_die(args.session)
    try:
        reply = _request(sock, {"type": "delete", "ids": args.ids})
        _exit_on_error_reply(reply)
        _emit({"ok": True, "session": args.session, "deleted": len(args.ids)})
    finally:
        sock.disconnectFromServer()


def _cmd_stop(args: argparse.Namespace) -> None:
    sock, hello = _connect_or_die(args.session)
    try:
        reply = _request(sock, {"type": "quit"})
        _exit_on_error_reply(reply)
        _emit({"ok": True, "session": args.session, "stopped": True})
    finally:
        sock.disconnectFromServer()


def _cmd_sessions(args: argparse.Namespace) -> None:
    sessions = _scan_sessions()
    _emit({"ok": True, "sessions": sessions})


def _scan_sessions() -> list[dict]:
    """Find candidate session sockets and ping each to confirm it's live.

    On Unix this scans ``$XDG_RUNTIME_DIR`` (or ``$TMPDIR``) for
    ``zeeref-*`` entries.  On Windows there's no equivalent enumeration,
    so we return an empty list and let callers fall back to ``ping``.
    """
    if sys.platform == "win32":
        return []
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    out: list[dict] = []
    try:
        entries = os.listdir(base)
    except OSError:
        return []
    for entry in entries:
        if not entry.startswith("zeeref-"):
            continue
        name = entry[len("zeeref-") :]
        sock = _try_connect(name)
        if sock is None:
            continue
        try:
            _read_hello(sock)
        except SystemExit:
            sock.disconnectFromServer()
            continue
        try:
            reply = _request(sock, {"type": "status"})
            if reply.get("type") == "status_info":
                out.append({"name": name, "status": _strip_type(reply)})
        finally:
            sock.disconnectFromServer()
    return out


# -- argparse wiring -------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="zeeref-cli",
        description="Interact with a running ZeeRef session.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # add
    add_p = sub.add_parser("add", help="Send images to a session (auto-spawns)")
    add_p.add_argument("session", help="Session name")
    add_p.add_argument("files", nargs="*", help="Image files to add")
    add_p.add_argument("--title", default=None, help="Title for the image(s)")
    add_p.add_argument("--caption", default=None, help="Caption for the image(s)")
    add_p.add_argument(
        "--x",
        type=float,
        default=None,
        help="Top-left x in scene coords (matches list output)",
    )
    add_p.add_argument(
        "--y",
        type=float,
        default=None,
        help="Top-left y in scene coords (matches list output)",
    )
    add_p.add_argument(
        "--scale", type=float, default=None, help="Scale factor (1.0 = native)"
    )
    add_p.add_argument(
        "--rotation",
        type=float,
        default=None,
        help="Rotation in degrees (pivots around image top-left)",
    )
    add_p.add_argument(
        "--z", type=float, default=None, help="Z stack order (higher = on top)"
    )
    add_p.add_argument(
        "--flip",
        type=int,
        default=None,
        choices=[-1, 1],
        help="-1 to flip horizontally",
    )
    add_p.add_argument(
        "--opacity", type=float, default=None, help="0.0 (invisible) to 1.0 (opaque)"
    )
    add_p.add_argument(
        "--stdin",
        action="store_true",
        help="Read JSON payload array from stdin "
        "(each entry: {path, title?, caption?, x?, y?, scale?, rotation?, z?, flip?, opacity?})",
    )
    add_p.set_defaults(func=_cmd_add)

    # add-text
    add_text_p = sub.add_parser(
        "add-text",
        help="Send a markdown text item to a session (auto-spawns)",
    )
    add_text_p.add_argument("session", help="Session name")
    add_text_p.add_argument("text", nargs="?", help="Markdown text")
    add_text_p.add_argument(
        "--x", type=float, default=None, help="Top-left x in scene coords"
    )
    add_text_p.add_argument(
        "--y", type=float, default=None, help="Top-left y in scene coords"
    )
    add_text_p.add_argument("--scale", type=float, default=None, help="Scale factor")
    add_text_p.add_argument(
        "--rotation", type=float, default=None, help="Rotation in degrees"
    )
    add_text_p.add_argument("--z", type=float, default=None, help="Z stack order")
    add_text_p.add_argument(
        "--flip", type=int, default=None, choices=[-1, 1], help="-1 to flip"
    )
    add_text_p.add_argument("--opacity", type=float, default=None, help="0.0..1.0")
    add_text_p.add_argument(
        "--wrap",
        type=int,
        default=None,
        metavar="COLS",
        help="Soft-wrap width in columns (0 for none; default from settings)",
    )
    add_text_p.add_argument(
        "--stdin",
        action="store_true",
        help="Read JSON payload array from stdin (each entry: {text, x?, y?, ...})",
    )
    add_text_p.set_defaults(func=_cmd_add_text)

    # start
    start_p = sub.add_parser("start", help="Ensure a session is running (idempotent)")
    start_p.add_argument("session", help="Session name")
    start_p.set_defaults(func=_cmd_start)

    # new
    new_p = sub.add_parser(
        "new", help="Fresh empty scene in a session (spawns if needed)"
    )
    new_p.add_argument("session", help="Session name")
    new_p.add_argument(
        "--force",
        action="store_true",
        help="Discard unsaved changes if session is dirty",
    )
    new_p.set_defaults(func=_cmd_new)

    # open
    open_p = sub.add_parser(
        "open", help="Open a .zref file in a session (spawns if needed)"
    )
    open_p.add_argument("session", help="Session name")
    open_p.add_argument("path", help="Path to .zref file")
    open_p.add_argument(
        "--force",
        action="store_true",
        help="Discard unsaved changes if session is dirty",
    )
    open_p.set_defaults(func=_cmd_open)

    # save
    save_p = sub.add_parser(
        "save", help="Save the running session's scene to a .zref (no spawn)"
    )
    save_p.add_argument("session", help="Session name")
    save_p.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Target .zref path (omit to save to the session's current file)",
    )
    save_p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite a different existing file "
        "(not needed to overwrite the session's current file)",
    )
    save_p.set_defaults(func=_cmd_save)

    # ping
    ping_p = sub.add_parser("ping", help="Liveness check (no spawn)")
    ping_p.add_argument("session", help="Session name")
    ping_p.set_defaults(func=_cmd_ping)

    # status
    status_p = sub.add_parser("status", help="Session status (no spawn)")
    status_p.add_argument("session", help="Session name")
    status_p.set_defaults(func=_cmd_status)

    # sessions
    sessions_p = sub.add_parser("sessions", help="List running sessions (no spawn)")
    sessions_p.set_defaults(func=_cmd_sessions)

    # list
    list_p = sub.add_parser("list", help="List all scene items (no spawn)")
    list_p.add_argument("session", help="Session name")
    list_p.set_defaults(func=_cmd_list)

    # get
    get_p = sub.add_parser("get", help="Get one item by id (no spawn)")
    get_p.add_argument("session", help="Session name")
    get_p.add_argument("id", help="Item id (save_id)")
    get_p.set_defaults(func=_cmd_get)

    # view
    view_p = sub.add_parser("view", help="Viewport state (no spawn)")
    view_p.add_argument("session", help="Session name")
    view_p.set_defaults(func=_cmd_view)

    # edit
    edit_p = sub.add_parser(
        "edit", help="Modify item fields by id (no spawn, additive)"
    )
    edit_p.add_argument("session", help="Session name")
    edit_p.add_argument("id", nargs="?", help="Item id (omit with --stdin)")
    edit_p.add_argument("--x", type=float, default=None, help="Top-left x")
    edit_p.add_argument("--y", type=float, default=None, help="Top-left y")
    edit_p.add_argument("--scale", type=float, default=None, help="Scale factor")
    edit_p.add_argument(
        "--rotation", type=float, default=None, help="Rotation in degrees"
    )
    edit_p.add_argument("--z", type=float, default=None, help="Z stack order")
    edit_p.add_argument("--flip", type=int, default=None, choices=[-1, 1])
    edit_p.add_argument("--opacity", type=float, default=None, help="0.0..1.0")
    edit_p.add_argument("--title", default=None, help="Image title ('' to clear)")
    edit_p.add_argument("--caption", default=None, help="Image caption ('' to clear)")
    edit_p.add_argument("--text", default=None, help="Text item markdown ('' to clear)")
    edit_p.add_argument(
        "--wrap",
        type=int,
        default=None,
        metavar="COLS",
        help="Text item soft-wrap width in columns (0 for none)",
    )
    edit_p.add_argument(
        "--stdin",
        action="store_true",
        help="Read JSON array from stdin (each entry: {id, ...fields})",
    )
    edit_p.set_defaults(func=_cmd_edit)

    # delete
    delete_p = sub.add_parser("delete", help="Remove items by id (no spawn)")
    delete_p.add_argument("session", help="Session name")
    delete_p.add_argument("ids", nargs="+", help="One or more item ids")
    delete_p.set_defaults(func=_cmd_delete)

    # stop
    stop_p = sub.add_parser("stop", help="Shut down a running session (no spawn)")
    stop_p.add_argument("session", help="Session name")
    stop_p.set_defaults(func=_cmd_stop)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
