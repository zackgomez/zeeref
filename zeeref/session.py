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
:class:`SessionServer` listens for local IPC connections via
:class:`QLocalServer`.  A lightweight client (``zeeref-cli``) connects
with :class:`QLocalSocket` and exchanges JSON messages.

Transport differs by platform (Qt handles the translation):
  * Linux/macOS: AF_UNIX socket at ``$XDG_RUNTIME_DIR/zeeref-<name>``
    (falls back to ``$TMPDIR``).
  * Windows: named pipe at ``\\\\.\\pipe\\zeeref-<name>``.

Wire protocol (one JSON object per line, ``\\n``-terminated).  Server
sends a ``hello`` greeting immediately on connect; clients should read
it before sending::

    Server (on connect): {"type": "hello", "protocol_version": 1,
                          "app_version": "..."}

    Client: {"type": "ping"}
    Server: {"type": "pong"}

    Client: {"type": "add", "payload": [{"path": "...", x?, y?, scale?,
             rotation?, z?, flip?, opacity?, title?, caption?}, ...]}
    Server: {"type": "ok"} | {"type": "error", "message": "..."}

    Client: {"type": "add_text", "payload": [{"text": "...", x?, y?, scale?,
             rotation?, z?, flip?, opacity?, wrap?}, ...]}
    Server: {"type": "ok"} | {"type": "error", "message": "..."}

    Client: {"type": "new", "force": false}
    Server: {"type": "ok"} | {"type": "error", "message": "..."}

    Client: {"type": "open", "path": "...", "force": false}
    Server: {"type": "ok"} | {"type": "error", "message": "..."}

    Client: {"type": "save", "path": "..." | null, "force": false}
    Server: {"type": "ok", "status": {...}} | {"type": "error", "message": "..."}

    Client: {"type": "status"}
    Server: {"type": "status_info", "loaded_file": "..." | null,
             "item_count": N, "dirty": bool}

    Client: {"type": "list"}
    Server: {"type": "items", "items": [{id, type, x, y, ...}, ...]}

    Client: {"type": "get", "id": "..."}
    Server: {"type": "item", "item": {id, type, ...} | null}

    Client: {"type": "view"}
    Server: {"type": "view_info", "center_x": ..., "center_y": ...,
             "zoom": ..., "window": {x, y, width, height}}

    Client: {"type": "edit", "payload": [{"id": "...", x?, y?, scale?,
             rotation?, z?, flip?, opacity?, title?, caption?, text?,
             wrap?}, ...]}
    Server: {"type": "ok"} | {"type": "error", "message": "..."}

    Client: {"type": "delete", "ids": ["...", ...]}
    Server: {"type": "ok"} | {"type": "error", "message": "..."}

    Client: {"type": "quit"}
    Server: {"type": "ok"} (then the app exits)

Bump ``PROTOCOL_VERSION`` on any wire-incompatible change.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import sys
import tempfile
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import cast

from PyQt6 import QtCore, QtNetwork

from zeeref import constants
from zeeref.fileio.io import ImageInsert, TextInsert

logger = logging.getLogger(__name__)


PROTOCOL_VERSION = 8


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
class AddTextMessage(ClientMessage):
    """Request to insert text items into the scene."""

    type: str = "add_text"
    texts: tuple[TextInsert, ...] = ()


@dataclasses.dataclass(frozen=True)
class PingMessage(ClientMessage):
    """Health check request."""

    type: str = "ping"


@dataclasses.dataclass(frozen=True)
class NewMessage(ClientMessage):
    """Reset scene to empty."""

    type: str = "new"
    force: bool = False


@dataclasses.dataclass(frozen=True)
class OpenMessage(ClientMessage):
    """Open a .zref file in the running session."""

    type: str = "open"
    path: str = ""
    force: bool = False


@dataclasses.dataclass(frozen=True)
class SaveMessage(ClientMessage):
    """Save the running session's scene to a .zref file.

    ``path`` is the target file; ``None`` means save to the session's
    current backing file.  ``force`` permits overwriting a *different*
    pre-existing file (not required to overwrite the current file).
    """

    type: str = "save"
    path: str | None = None
    force: bool = False


@dataclasses.dataclass(frozen=True)
class StatusRequestMessage(ClientMessage):
    """Request session status info."""

    type: str = "status"


@dataclasses.dataclass(frozen=True)
class ListRequestMessage(ClientMessage):
    """Request the full list of scene items."""

    type: str = "list"


@dataclasses.dataclass(frozen=True)
class GetRequestMessage(ClientMessage):
    """Request a single scene item by id."""

    type: str = "get"
    id: str = ""


@dataclasses.dataclass(frozen=True)
class ViewRequestMessage(ClientMessage):
    """Request viewport state."""

    type: str = "view"


@dataclasses.dataclass(frozen=True)
class EditMessage(ClientMessage):
    """Partial-update one or more items by id.

    Each entry is a dict carrying an ``id`` plus the subset of fields
    to change.  Falsy strings (``""`` / ``None``) clear ``title``,
    ``caption``, and ``text``.
    """

    type: str = "edit"
    edits: tuple[dict, ...] = ()


@dataclasses.dataclass(frozen=True)
class DeleteMessage(ClientMessage):
    """Remove one or more items by id."""

    type: str = "delete"
    ids: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class QuitMessage(ClientMessage):
    """Ask the session's app to exit after acking."""

    type: str = "quit"


@dataclasses.dataclass(frozen=True)
class ServerMessage:
    """Base class for messages sent by the server."""

    type: str

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))


@dataclasses.dataclass(frozen=True)
class OkMessage(ServerMessage):
    """Generic success reply.

    Fields are populated for the operations where they're meaningful:
      * ``ids``: created snapshot ids (add / add_text)
      * ``items``: post-mutation snapshot dicts (edit)
      * ``status``: status_info dict (open)
    Empty for ops that don't have anything to return.
    """

    type: str = "ok"
    ids: tuple[str, ...] = ()
    items: tuple[dict, ...] = ()
    status: dict | None = None


@dataclasses.dataclass(frozen=True)
class ErrorMessage(ServerMessage):
    type: str = "error"
    message: str = ""


@dataclasses.dataclass(frozen=True)
class PongMessage(ServerMessage):
    type: str = "pong"


@dataclasses.dataclass(frozen=True)
class HelloMessage(ServerMessage):
    type: str = "hello"
    protocol_version: int = PROTOCOL_VERSION
    app_version: str = ""


@dataclasses.dataclass(frozen=True)
class StatusInfoMessage(ServerMessage):
    type: str = "status_info"
    loaded_file: str | None = None
    item_count: int = 0
    dirty: bool = False


@dataclasses.dataclass(frozen=True)
class ItemsMessage(ServerMessage):
    type: str = "items"
    items: tuple[dict, ...] = ()


@dataclasses.dataclass(frozen=True)
class ItemMessage(ServerMessage):
    """Reply to ``get``. ``item`` is ``None`` when the id is unknown."""

    type: str = "item"
    item: dict | None = None


@dataclasses.dataclass(frozen=True)
class ViewInfoMessage(ServerMessage):
    type: str = "view_info"
    center_x: float = 0.0
    center_y: float = 0.0
    zoom: float = 1.0
    window: dict | None = None


# -- Parsing ----------------------------------------------------------------


def server_name(session_name: str) -> str:
    """Return the server identifier to pass to :class:`QLocalServer.listen`
    and :class:`QLocalSocket.connectToServer`.

    On Windows this is a bare name (Qt maps to ``\\\\.\\pipe\\zeeref-<name>``).
    On Unix it is an absolute path so we can honour ``$XDG_RUNTIME_DIR``
    for per-user cleanup on logout.
    """
    if sys.platform == "win32":
        return f"zeeref-{session_name}"
    runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return os.path.join(runtime, f"zeeref-{session_name}")


def _coerce_number(
    val: object, field: str, index: int, kind: type
) -> tuple[float | int | None, str | None]:
    """Coerce an optional JSON number to *kind*. Returns (value, error)."""
    if val is None:
        return None, None
    if isinstance(val, bool):  # bool is a subclass of int; reject
        return None, f"item {index}: '{field}' must be a number"
    if not isinstance(val, (int, float)):
        return None, f"item {index}: '{field}' must be a number"
    return kind(val), None


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

    def _f(name: str) -> tuple[float | None, str | None]:
        val, err = _coerce_number(d.get(name), name, index, float)
        return (val if val is None else float(val), err)

    def _i(name: str) -> tuple[int | None, str | None]:
        val, err = _coerce_number(d.get(name), name, index, int)
        return (val if val is None else int(val), err)

    x_v, err = _f("x")
    if err:
        return err
    y_v, err = _f("y")
    if err:
        return err
    scale_v, err = _f("scale")
    if err:
        return err
    rotation_v, err = _f("rotation")
    if err:
        return err
    z_v, err = _f("z")
    if err:
        return err
    flip_v, err = _i("flip")
    if err:
        return err
    opacity_v, err = _f("opacity")
    if err:
        return err

    if flip_v is not None and flip_v not in (-1, 1):
        return f"item {index}: 'flip' must be 1 or -1"
    if opacity_v is not None and not (0.0 <= opacity_v <= 1.0):
        return f"item {index}: 'opacity' must be in [0.0, 1.0]"

    resolved = Path(path).resolve()
    if not resolved.is_file():
        logger.warning("Session: skipping non-existent path: %s", path)
        return f"item {index}: file not found: {path}"
    return ImageInsert(
        path=str(resolved),
        title=title,
        caption=caption,
        x=x_v,
        y=y_v,
        scale=scale_v,
        rotation=rotation_v,
        z=z_v,
        flip=flip_v,
        opacity=opacity_v,
    )


def _parse_edit_entry(raw: object, index: int) -> dict | str:
    """Parse one edit dict, validating optional transform/metadata fields.

    Returns a dict carrying the (possibly empty) subset of fields the
    caller wants to change, plus the required ``id``.  Falsy strings
    for title/caption/text are normalized to ``None`` to clear.
    """
    if not isinstance(raw, dict):
        return f"item {index}: expected object, got {type(raw).__name__}"
    d = cast(dict[str, object], raw)
    item_id = d.get("id")
    if not isinstance(item_id, str) or not item_id:
        return f"item {index}: missing or invalid 'id'"

    changes: dict = {"id": item_id}

    for field in ("x", "y", "scale", "rotation", "z", "opacity"):
        if field in d:
            val, err = _coerce_number(d[field], field, index, float)
            if err:
                return err
            changes[field] = None if val is None else float(val)

    if "flip" in d:
        val, err = _coerce_number(d["flip"], "flip", index, int)
        if err:
            return err
        if val is not None and val not in (-1, 1):
            return f"item {index}: 'flip' must be 1 or -1"
        changes["flip"] = None if val is None else int(val)

    if "opacity" in changes:
        op = changes["opacity"]
        if op is not None and not (0.0 <= op <= 1.0):
            return f"item {index}: 'opacity' must be in [0.0, 1.0]"

    for field in ("title", "caption", "text"):
        if field in d:
            val = d[field]
            if val is None or isinstance(val, str):
                # Falsy strings clear; truthy strings set.
                changes[field] = val if val else None
            else:
                return f"item {index}: '{field}' must be a string or null"

    if "wrap" in d:
        val, err = _coerce_number(d["wrap"], "wrap", index, int)
        if err:
            return err
        if val is None or not (0 <= val <= 500):
            return f"item {index}: 'wrap' must be in [0, 500]"
        changes["wrap"] = int(val)

    return changes


def _parse_text_entry(raw: object, index: int) -> TextInsert | str:
    """Parse a single JSON object into a TextInsert."""
    if not isinstance(raw, dict):
        return f"item {index}: expected object, got {type(raw).__name__}"
    d = cast(dict[str, object], raw)
    text = d.get("text")
    if not isinstance(text, str):
        return f"item {index}: missing or invalid 'text'"

    def _f(name: str) -> tuple[float | None, str | None]:
        val, err = _coerce_number(d.get(name), name, index, float)
        return (val if val is None else float(val), err)

    def _i(name: str) -> tuple[int | None, str | None]:
        val, err = _coerce_number(d.get(name), name, index, int)
        return (val if val is None else int(val), err)

    x_v, err = _f("x")
    if err:
        return err
    y_v, err = _f("y")
    if err:
        return err
    scale_v, err = _f("scale")
    if err:
        return err
    rotation_v, err = _f("rotation")
    if err:
        return err
    z_v, err = _f("z")
    if err:
        return err
    flip_v, err = _i("flip")
    if err:
        return err
    opacity_v, err = _f("opacity")
    if err:
        return err
    wrap_v, err = _i("wrap")
    if err:
        return err

    if flip_v is not None and flip_v not in (-1, 1):
        return f"item {index}: 'flip' must be 1 or -1"
    if opacity_v is not None and not (0.0 <= opacity_v <= 1.0):
        return f"item {index}: 'opacity' must be in [0.0, 1.0]"
    if wrap_v is not None and not (0 <= wrap_v <= 500):
        return f"item {index}: 'wrap' must be in [0, 500]"

    return TextInsert(
        text=text,
        wrap=wrap_v,
        x=x_v,
        y=y_v,
        scale=scale_v,
        rotation=rotation_v,
        z=z_v,
        flip=flip_v,
        opacity=opacity_v,
    )


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

    if msg_type == "status":
        return StatusRequestMessage()

    if msg_type == "list":
        return ListRequestMessage()

    if msg_type == "view":
        return ViewRequestMessage()

    if msg_type == "get":
        item_id = msg.get("id")
        if not isinstance(item_id, str) or not item_id:
            return ErrorMessage(message="'get' requires 'id' string")
        return GetRequestMessage(id=item_id)

    if msg_type == "new":
        force = msg.get("force", False)
        if not isinstance(force, bool):
            return ErrorMessage(message="'force' must be a boolean")
        return NewMessage(force=force)

    if msg_type == "open":
        path = msg.get("path")
        if not isinstance(path, str) or not path:
            return ErrorMessage(message="'open' requires 'path' string")
        force = msg.get("force", False)
        if not isinstance(force, bool):
            return ErrorMessage(message="'force' must be a boolean")
        return OpenMessage(path=path, force=force)

    if msg_type == "save":
        path = msg.get("path")
        if path is not None and (not isinstance(path, str) or not path):
            return ErrorMessage(
                message="'save' path must be a non-empty string or omitted"
            )
        force = msg.get("force", False)
        if not isinstance(force, bool):
            return ErrorMessage(message="'force' must be a boolean")
        return SaveMessage(path=path, force=force)

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

    if msg_type == "add_text":
        payload = msg.get("payload")
        if not isinstance(payload, list) or not payload:
            return ErrorMessage(message="'add_text' requires non-empty 'payload' array")
        texts: list[TextInsert] = []
        for i, entry in enumerate(payload):
            parsed = _parse_text_entry(entry, i)
            if isinstance(parsed, str):
                return ErrorMessage(message=parsed)
            texts.append(parsed)
        return AddTextMessage(texts=tuple(texts))

    if msg_type == "edit":
        payload = msg.get("payload")
        if not isinstance(payload, list) or not payload:
            return ErrorMessage(message="'edit' requires non-empty 'payload' array")
        edits: list[dict] = []
        for i, entry in enumerate(payload):
            parsed = _parse_edit_entry(entry, i)
            if isinstance(parsed, str):
                return ErrorMessage(message=parsed)
            edits.append(parsed)
        return EditMessage(edits=tuple(edits))

    if msg_type == "delete":
        ids = msg.get("ids")
        if not isinstance(ids, list) or not ids:
            return ErrorMessage(message="'delete' requires non-empty 'ids' array")
        out_ids: list[str] = []
        for i, v in enumerate(ids):
            if not isinstance(v, str) or not v:
                return ErrorMessage(message=f"ids[{i}]: must be non-empty string")
            out_ids.append(v)
        return DeleteMessage(ids=tuple(out_ids))

    if msg_type == "quit":
        return QuitMessage()

    return ErrorMessage(message=f"unknown type: {msg_type}")


# -- Server -----------------------------------------------------------------


class SessionServer(QtCore.QObject):
    """QLocalServer that accepts JSON messages over a named Unix socket.

    Mutating operations (add/new/open) are serialized through a single
    queue so they don't race against each other on the scene.  Reads
    (status/ping) bypass the queue.
    """

    def __init__(
        self,
        session_name: str,
        insert_fn: Callable[
            [list[ImageInsert], Callable[[list[str], list[str]], None]], None
        ],
        new_fn: Callable[[bool, Callable[[list[str], list[str]], None]], None],
        open_fn: Callable[[Path, bool, Callable[[list[str], list[str]], None]], None],
        save_fn: Callable[
            [str | None, bool, Callable[[list[str], list[str]], None]], None
        ],
        status_fn: Callable[[], StatusInfoMessage],
        list_fn: Callable[[], ItemsMessage],
        get_fn: Callable[[str], ItemMessage],
        view_fn: Callable[[], ViewInfoMessage],
        insert_text_fn: Callable[
            [list[TextInsert], Callable[[list[str], list[str]], None]], None
        ],
        edit_fn: Callable[[list[dict], Callable[[list[str], list[str]], None]], None],
        delete_fn: Callable[[list[str], Callable[[list[str], list[str]], None]], None],
        quit_fn: Callable[[], None],
        parent: QtCore.QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._session_name = session_name
        self._insert_fn = insert_fn
        self._new_fn = new_fn
        self._open_fn = open_fn
        self._save_fn = save_fn
        self._status_fn = status_fn
        self._list_fn = list_fn
        self._get_fn = get_fn
        self._view_fn = view_fn
        self._insert_text_fn = insert_text_fn
        self._edit_fn = edit_fn
        self._delete_fn = delete_fn
        self._quit_fn = quit_fn
        self._server = QtNetwork.QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._connections: list[_SessionConnection] = []
        self._queue: deque[tuple[ClientMessage, _SessionConnection]] = deque()
        self._busy = False

    def start(self) -> bool:
        """Begin listening.  Returns False if the name is taken."""
        name = server_name(self._session_name)
        QtNetwork.QLocalServer.removeServer(name)
        if not self._server.listen(name):
            logger.error(
                "SessionServer: failed to listen on %s: %s",
                name,
                self._server.errorString(),
            )
            return False
        logger.info(
            "Session '%s' listening on %s",
            self._session_name,
            self._server.fullServerName(),
        )
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
            conn = _SessionConnection(socket, self._on_client_message, self)
            self._connections.append(conn)
            conn.reply(HelloMessage(app_version=constants.VERSION))

    def _on_client_message(self, msg: ClientMessage, conn: _SessionConnection) -> None:
        """Dispatch a parsed client message.

        Reads reply inline; mutations get queued for serial processing.
        """
        if isinstance(msg, StatusRequestMessage):
            conn.reply(self._status_fn())
            return
        if isinstance(msg, ListRequestMessage):
            conn.reply(self._list_fn())
            return
        if isinstance(msg, GetRequestMessage):
            conn.reply(self._get_fn(msg.id))
            return
        if isinstance(msg, ViewRequestMessage):
            conn.reply(self._view_fn())
            return
        if isinstance(msg, QuitMessage):
            conn.reply(OkMessage())
            # Defer to next event-loop tick so the reply finishes flushing
            # before the app starts tearing down.
            QtCore.QTimer.singleShot(0, self._quit_fn)
            return
        if isinstance(
            msg,
            (
                AddMessage,
                AddTextMessage,
                NewMessage,
                OpenMessage,
                SaveMessage,
                EditMessage,
                DeleteMessage,
            ),
        ):
            self._queue.append((msg, conn))
            self._process_queue()
            return
        # Unknown — shouldn't reach here since parse_message guards.
        conn.reply(ErrorMessage(message=f"unhandled message type: {msg.type}"))

    def _process_queue(self) -> None:
        if self._busy or not self._queue:
            return
        self._busy = True
        msg, conn = self._queue[0]
        if isinstance(msg, AddMessage):
            logger.info("Session: inserting %d image(s)", len(msg.images))
            self._insert_fn(list(msg.images), self._on_op_finished)
        elif isinstance(msg, AddTextMessage):
            logger.info("Session: inserting %d text item(s)", len(msg.texts))
            self._insert_text_fn(list(msg.texts), self._on_op_finished)
        elif isinstance(msg, NewMessage):
            logger.info("Session: new scene (force=%s)", msg.force)
            self._new_fn(msg.force, self._on_op_finished)
        elif isinstance(msg, OpenMessage):
            logger.info("Session: opening %s (force=%s)", msg.path, msg.force)
            self._open_fn(Path(msg.path), msg.force, self._on_op_finished)
        elif isinstance(msg, SaveMessage):
            logger.info("Session: saving (path=%s, force=%s)", msg.path, msg.force)
            self._save_fn(msg.path, msg.force, self._on_op_finished)
        elif isinstance(msg, EditMessage):
            logger.info("Session: editing %d item(s)", len(msg.edits))
            self._edit_fn(list(msg.edits), self._on_op_finished)
        elif isinstance(msg, DeleteMessage):
            logger.info("Session: deleting %d item(s)", len(msg.ids))
            self._delete_fn(list(msg.ids), self._on_op_finished)
        else:
            # Defensive — should be unreachable.
            self._busy = False
            self._queue.popleft()
            conn.reply(ErrorMessage(message=f"unqueueable message: {msg.type}"))
            self._process_queue()

    def _on_op_finished(self, errors: list[str], ids: list[str]) -> None:
        self._busy = False
        msg, conn = self._queue.popleft()
        if errors:
            conn.reply(ErrorMessage(message="; ".join(errors)))
            self._process_queue()
            return

        # Enrich the ok reply for ops with meaningful return data.
        items: tuple[dict, ...] = ()
        status: dict | None = None
        if isinstance(msg, EditMessage):
            fetched = [self._get_fn(entry["id"]).item for entry in msg.edits]
            items = tuple(it for it in fetched if it is not None)
        elif isinstance(msg, (OpenMessage, SaveMessage)):
            status_msg = self._status_fn()
            status = {
                k: v for k, v in dataclasses.asdict(status_msg).items() if k != "type"
            }
        conn.reply(OkMessage(ids=tuple(ids), items=items, status=status))
        self._process_queue()

    def _remove_connection(self, conn: _SessionConnection) -> None:
        if conn in self._connections:
            self._connections.remove(conn)


class _SessionConnection(QtCore.QObject):
    """Handles one client connection, accumulating bytes and parsing lines."""

    def __init__(
        self,
        socket: QtNetwork.QLocalSocket,
        on_message: Callable[[ClientMessage, _SessionConnection], None],
        server: SessionServer,
    ) -> None:
        super().__init__(server)
        self._socket = socket
        self._on_message = on_message
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
        else:
            self._on_message(result, self)

    def _on_disconnected(self) -> None:
        self._server._remove_connection(self)
