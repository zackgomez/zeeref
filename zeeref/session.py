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

"""Named session IPC server.

When ZeeRef is started with ``--session <name>``, a
:class:`SessionServer` listens on a Unix domain socket at
``$XDG_RUNTIME_DIR/zeeref-<name>``.  A lightweight client
(``zeeref-add``) can connect and send JSON messages to insert
images into the running scene.

Wire protocol (one JSON object per line, ``\\n``-terminated)::

    Client:  {"type": "add", "payload": [{"path": "...", ...}]}
    Server:  {"type": "ok"} | {"type": "error", "message": "..."}

    Client:  {"type": "ping"}
    Server:  {"type": "pong"}
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import cast

from PyQt6 import QtCore, QtNetwork

from zeeref.fileio.io import ImageInsert

logger = logging.getLogger(__name__)


# -- Messages --------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ClientMessage:
    """Base class for messages sent by the client."""

    type: str


@dataclasses.dataclass(frozen=True)
class AddMessage(ClientMessage):
    """Request to insert images into the scene."""

    type: str = "add"
    images: tuple[ImageInsert, ...] = ()


@dataclasses.dataclass(frozen=True)
class PingMessage(ClientMessage):
    """Health check request."""

    type: str = "ping"


@dataclasses.dataclass(frozen=True)
class ServerMessage:
    """Base class for messages sent by the server."""

    type: str

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))


@dataclasses.dataclass(frozen=True)
class OkMessage(ServerMessage):
    type: str = "ok"


@dataclasses.dataclass(frozen=True)
class ErrorMessage(ServerMessage):
    type: str = "error"
    message: str = ""


@dataclasses.dataclass(frozen=True)
class PongMessage(ServerMessage):
    type: str = "pong"


# -- Parsing ----------------------------------------------------------------


def socket_path(session_name: str) -> Path:
    """Return the Unix domain socket path for a session name."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    return Path(runtime) / f"zeeref-{session_name}"


def _parse_insert_entry(raw: object, index: int) -> ImageInsert | str:
    """Parse a single JSON object into an ImageInsert.

    Returns an ImageInsert on success, or an error string on failure.
    """
    if not isinstance(raw, dict):
        return f"item {index}: expected object, got {type(raw).__name__}"
    # JSON objects always have string keys
    d = cast(dict[str, object], raw)
    path = d.get("path")
    if not isinstance(path, str) or not path:
        return f"item {index}: missing or invalid 'path'"
    title = d.get("title")
    if title is not None and not isinstance(title, str):
        return f"item {index}: 'title' must be a string"
    caption = d.get("caption")
    if caption is not None and not isinstance(caption, str):
        return f"item {index}: 'caption' must be a string"
    resolved = Path(path).resolve()
    if not resolved.is_file():
        logger.warning("Session: skipping non-existent path: %s", path)
        return f"item {index}: file not found: {path}"
    return ImageInsert(path=str(resolved), title=title, caption=caption)


def parse_message(line: str) -> ClientMessage | ErrorMessage:
    """Parse a JSON line into a typed ClientMessage.

    Returns an ErrorMessage if parsing or validation fails.
    """
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as e:
        return ErrorMessage(message=f"invalid JSON: {e}")

    if not isinstance(raw, dict):
        return ErrorMessage(message="expected JSON object")

    msg = cast(dict[str, object], raw)
    msg_type = msg.get("type")
    if not isinstance(msg_type, str):
        return ErrorMessage(message="missing or invalid 'type'")

    if msg_type == "ping":
        return PingMessage()

    if msg_type == "add":
        payload = msg.get("payload")
        if not isinstance(payload, list) or not payload:
            return ErrorMessage(message="'add' requires non-empty 'payload' array")

        images: list[ImageInsert] = []
        for i, entry in enumerate(payload):
            parsed = _parse_insert_entry(entry, i)
            if isinstance(parsed, str):
                if "file not found" in parsed:
                    continue
                return ErrorMessage(message=parsed)
            images.append(parsed)

        if not images:
            return ErrorMessage(message="no valid files")
        return AddMessage(images=tuple(images))

    return ErrorMessage(message=f"unknown type: {msg_type}")


# -- Server -----------------------------------------------------------------


class SessionServer(QtCore.QObject):
    """QLocalServer that accepts JSON messages over a named Unix socket."""

    def __init__(
        self,
        session_name: str,
        insert_fn: Callable[[list[ImageInsert], Callable], None],
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_name = session_name
        self._insert_fn = insert_fn
        self._server = QtNetwork.QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._connections: list[_SessionConnection] = []
        self._queue: deque[tuple[AddMessage, _SessionConnection]] = deque()
        self._busy = False

    def start(self) -> bool:
        """Begin listening.  Returns False if the name is taken."""
        path = str(socket_path(self._session_name))
        QtNetwork.QLocalServer.removeServer(path)
        if not self._server.listen(path):
            logger.error(
                "SessionServer: failed to listen on %s: %s",
                path,
                self._server.errorString(),
            )
            return False
        logger.info("Session '%s' listening on %s", self._session_name, path)
        return True

    def shutdown(self) -> None:
        """Stop listening and clean up."""
        self._server.close()
        for conn in self._connections:
            conn.close()
        self._connections.clear()
        logger.info("Session '%s' shut down", self._session_name)

    # -- internal ----------------------------------------------------------

    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            conn = _SessionConnection(socket, self._on_add, self)
            self._connections.append(conn)

    def _on_add(self, msg: AddMessage, conn: _SessionConnection) -> None:
        """Called by a connection when it parses a valid add message."""
        self._queue.append((msg, conn))
        self._process_queue()

    def _process_queue(self) -> None:
        if self._busy or not self._queue:
            return
        self._busy = True
        msg, conn = self._queue[0]
        logger.info("Session: inserting %d image(s)", len(msg.images))
        self._insert_fn(list(msg.images), self._on_insert_finished)

    def _on_insert_finished(self, errors: list[str]) -> None:
        self._busy = False
        msg, conn = self._queue.popleft()
        if errors:
            conn.reply(
                ErrorMessage(
                    message=f"{len(errors)} file(s) failed: {', '.join(errors)}"
                )
            )
        else:
            conn.reply(OkMessage())
        self._process_queue()

    def _remove_connection(self, conn: _SessionConnection) -> None:
        if conn in self._connections:
            self._connections.remove(conn)


class _SessionConnection(QtCore.QObject):
    """Handles one client connection, accumulating bytes and parsing lines."""

    def __init__(
        self,
        socket: QtNetwork.QLocalSocket,
        on_add: Callable[[AddMessage, _SessionConnection], None],
        server: SessionServer,
    ) -> None:
        super().__init__(server)
        self._socket = socket
        self._on_add = on_add
        self._server = server
        self._buf = b""
        socket.readyRead.connect(self._on_ready_read)
        socket.disconnected.connect(self._on_disconnected)

    def reply(self, msg: ServerMessage) -> None:
        if (
            self._socket.state()
            == QtNetwork.QLocalSocket.LocalSocketState.ConnectedState
        ):
            self._socket.write((msg.to_json() + "\n").encode())
            self._socket.flush()

    def close(self) -> None:
        self._socket.disconnectFromServer()

    def _on_ready_read(self) -> None:
        raw = self._socket.readAll()
        if raw:
            self._buf += raw.data()
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            self._process_line(line.decode())

    def _process_line(self, line: str) -> None:
        result = parse_message(line)
        if isinstance(result, ErrorMessage):
            self.reply(result)
        elif isinstance(result, PingMessage):
            self.reply(PongMessage())
        elif isinstance(result, AddMessage):
            self._on_add(result, self)

    def _on_disconnected(self) -> None:
        self._server._remove_connection(self)
