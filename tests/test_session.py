import json
import socket
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from zeeref.session import (
    AddMessage,
    ErrorMessage,
    PingMessage,
    SessionServer,
    parse_message,
    server_name,
)


@pytest.fixture
def session_name(tmp_path):
    """Use a unique session name based on tmp_path to avoid collisions."""
    return f"test-{tmp_path.name}"


@pytest.fixture
def mock_insert_fn():
    """A mock insert function that calls the callback immediately."""
    fn = MagicMock()

    def side_effect(inserts, on_done):
        on_done([])

    fn.side_effect = side_effect
    return fn


@pytest.fixture
def server(qtbot, session_name, mock_insert_fn):
    srv = SessionServer(session_name, mock_insert_fn)
    assert srv.start()
    yield srv
    srv.shutdown()


@pytest.fixture
def imgfile(tmp_path):
    """Create a small test image file."""
    p = tmp_path / "test.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    return p


def make_msg(msg: dict) -> str:
    """Serialize a message dict to a protocol line."""
    return json.dumps(msg) + "\n"


def add_msg(items: list[dict]) -> str:
    """Build an add message line."""
    return make_msg({"type": "add", "payload": items})


class AsyncClient:
    """Sends a message to a session socket in a background thread."""

    def __init__(self, session_name: str, message: str):
        self.reply: dict | None = None
        self._thread = threading.Thread(target=self._run, args=(session_name, message))
        self._thread.start()

    def _run(self, session_name: str, message: str):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(server_name(session_name))
        sock.sendall(message.encode())
        buf = b""
        try:
            while not buf.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
        except TimeoutError:
            pass
        sock.close()
        self.reply = json.loads(buf.decode()) if buf else {}

    @property
    def done(self) -> bool:
        return self.reply is not None

    def join(self):
        self._thread.join(timeout=2)


# -- parse_message unit tests -----------------------------------------------


def test_parse_ping():
    result = parse_message('{"type": "ping"}')
    assert isinstance(result, PingMessage)


def test_parse_add(imgfile):
    result = parse_message(
        json.dumps(
            {
                "type": "add",
                "payload": [{"path": str(imgfile), "title": "t", "caption": "c"}],
            }
        )
    )
    assert isinstance(result, AddMessage)
    assert len(result.images) == 1
    assert result.images[0].title == "t"
    assert result.images[0].caption == "c"


def test_parse_invalid_json():
    result = parse_message("not json")
    assert isinstance(result, ErrorMessage)


def test_parse_missing_type():
    result = parse_message('{"payload": []}')
    assert isinstance(result, ErrorMessage)


def test_parse_unknown_type():
    result = parse_message('{"type": "explode"}')
    assert isinstance(result, ErrorMessage)


def test_parse_add_missing_path():
    result = parse_message(json.dumps({"type": "add", "payload": [{"title": "x"}]}))
    assert isinstance(result, ErrorMessage)


def test_parse_add_invalid_title_type(imgfile):
    result = parse_message(
        json.dumps({"type": "add", "payload": [{"path": str(imgfile), "title": 123}]})
    )
    assert isinstance(result, ErrorMessage)


# -- Server integration tests ------------------------------------------------


def test_server_starts_and_creates_socket(server, session_name):
    # On Unix server_name() returns the socket file path.
    path = Path(server_name(session_name))
    assert path.exists()


def test_server_shutdown_removes_socket(qtbot, session_name, mock_insert_fn):
    srv = SessionServer(session_name, mock_insert_fn)
    assert srv.start()
    path = Path(server_name(session_name))
    assert path.exists()
    srv.shutdown()
    assert not path.exists()


def test_ping(qtbot, server, session_name):
    c = AsyncClient(session_name, make_msg({"type": "ping"}))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "pong"


def test_add_single_image(qtbot, server, session_name, mock_insert_fn, imgfile):
    c = AsyncClient(session_name, add_msg([{"path": str(imgfile)}]))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "ok"
    mock_insert_fn.assert_called_once()
    inserts = mock_insert_fn.call_args[0][0]
    assert len(inserts) == 1
    assert inserts[0].path == str(imgfile)


def test_add_with_title_and_caption(
    qtbot, server, session_name, mock_insert_fn, imgfile
):
    c = AsyncClient(
        session_name,
        add_msg([{"path": str(imgfile), "title": "10x", "caption": "Chip 2"}]),
    )
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "ok"
    inserts = mock_insert_fn.call_args[0][0]
    assert inserts[0].title == "10x"
    assert inserts[0].caption == "Chip 2"


def test_add_multiple_images(qtbot, server, session_name, mock_insert_fn, tmp_path):
    files = []
    for i in range(3):
        p = tmp_path / f"img{i}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        files.append(p)

    c = AsyncClient(session_name, add_msg([{"path": str(f)} for f in files]))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "ok"
    inserts = mock_insert_fn.call_args[0][0]
    assert [ins.path for ins in inserts] == [str(f) for f in files]


def test_add_nonexistent_file(qtbot, server, session_name, mock_insert_fn):
    c = AsyncClient(session_name, add_msg([{"path": "/nonexistent/file.png"}]))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "error"
    mock_insert_fn.assert_not_called()


def test_add_skips_nonexistent_keeps_valid(
    qtbot, server, session_name, mock_insert_fn, imgfile
):
    c = AsyncClient(
        session_name,
        add_msg([{"path": "/nonexistent/file.png"}, {"path": str(imgfile)}]),
    )
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "ok"
    inserts = mock_insert_fn.call_args[0][0]
    assert [ins.path for ins in inserts] == [str(imgfile)]


def test_unknown_command(qtbot, server, session_name):
    c = AsyncClient(session_name, make_msg({"type": "explode"}))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "error"


def test_add_reports_insert_errors(qtbot, session_name, imgfile):
    """When the insert callback reports errors, the reply is error."""

    def insert_with_errors(inserts, on_done):
        on_done(["bad_file.png"])

    fn = MagicMock(side_effect=insert_with_errors)
    srv = SessionServer(session_name, fn)
    assert srv.start()

    c = AsyncClient(session_name, add_msg([{"path": str(imgfile)}]))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "error"
    srv.shutdown()


def test_blocking_queues_requests(qtbot, session_name, imgfile):
    """Second add blocks until the first insert completes."""
    finish_callbacks: list[callable] = []

    def slow_insert(inserts, on_done):
        finish_callbacks.append(on_done)

    fn = MagicMock(side_effect=slow_insert)
    srv = SessionServer(session_name, fn)
    assert srv.start()

    msg = add_msg([{"path": str(imgfile)}])

    c1 = AsyncClient(session_name, msg)
    qtbot.waitUntil(lambda: len(finish_callbacks) == 1, timeout=3000)

    c2 = AsyncClient(session_name, msg)
    qtbot.waitUntil(lambda: len(srv._queue) >= 1, timeout=3000)
    assert fn.call_count == 1

    finish_callbacks[0]([])
    qtbot.waitUntil(lambda: fn.call_count == 2, timeout=3000)

    qtbot.waitUntil(lambda: c1.done, timeout=3000)
    assert c1.reply["type"] == "ok"

    finish_callbacks[1]([])
    qtbot.waitUntil(lambda: c2.done, timeout=3000)
    assert c2.reply["type"] == "ok"

    srv.shutdown()
