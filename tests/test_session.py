import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6 import QtNetwork

from zeeref.session import (
    AddMessage,
    AddTextMessage,
    DeleteMessage,
    EditMessage,
    ErrorMessage,
    GetRequestMessage,
    ItemMessage,
    ItemsMessage,
    ListRequestMessage,
    NewMessage,
    OpenMessage,
    PingMessage,
    PROTOCOL_VERSION,
    QuitMessage,
    SaveMessage,
    SessionServer,
    StatusInfoMessage,
    StatusRequestMessage,
    ViewInfoMessage,
    ViewRequestMessage,
    parse_message,
    server_name,
)


@pytest.fixture
def session_name(tmp_path):
    """Use a unique session name based on tmp_path to avoid collisions."""
    return f"test-{tmp_path.name}"


def _mock_async_fn():
    """Mock that invokes its trailing callback with (errors=[], ids=[])."""
    fn = MagicMock()

    def side_effect(*args):
        on_done = args[-1]
        on_done([], [])

    fn.side_effect = side_effect
    return fn


@pytest.fixture
def mock_insert_fn():
    return _mock_async_fn()


@pytest.fixture
def mock_new_fn():
    return _mock_async_fn()


@pytest.fixture
def mock_open_fn():
    return _mock_async_fn()


@pytest.fixture
def mock_save_fn():
    return _mock_async_fn()


@pytest.fixture
def mock_status_fn():
    fn = MagicMock(
        return_value=StatusInfoMessage(loaded_file=None, item_count=0, dirty=False)
    )
    return fn


@pytest.fixture
def mock_list_fn():
    return MagicMock(return_value=ItemsMessage(items=()))


@pytest.fixture
def mock_get_fn():
    return MagicMock(return_value=ItemMessage(item=None))


@pytest.fixture
def mock_view_fn():
    return MagicMock(return_value=ViewInfoMessage())


@pytest.fixture
def mock_insert_text_fn():
    return _mock_async_fn()


@pytest.fixture
def mock_edit_fn():
    return _mock_async_fn()


@pytest.fixture
def mock_delete_fn():
    return _mock_async_fn()


@pytest.fixture
def mock_quit_fn():
    return MagicMock()


@pytest.fixture
def server(
    qtbot,
    session_name,
    mock_insert_fn,
    mock_new_fn,
    mock_open_fn,
    mock_save_fn,
    mock_status_fn,
    mock_list_fn,
    mock_get_fn,
    mock_view_fn,
    mock_insert_text_fn,
    mock_edit_fn,
    mock_delete_fn,
    mock_quit_fn,
):
    srv = SessionServer(
        session_name,
        mock_insert_fn,
        mock_new_fn,
        mock_open_fn,
        mock_save_fn,
        mock_status_fn,
        mock_list_fn,
        mock_get_fn,
        mock_view_fn,
        mock_insert_text_fn,
        mock_edit_fn,
        mock_delete_fn,
        mock_quit_fn,
    )
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
    """Sends a message to a session socket in a background thread.

    Consumes the server's ``hello`` greeting (stored as :attr:`hello`)
    before reading the actual reply to *message*.
    """

    def __init__(self, session_name: str, message: str):
        self.reply: dict | None = None
        self.hello: dict | None = None
        self._thread = threading.Thread(target=self._run, args=(session_name, message))
        self._thread.start()

    @staticmethod
    def _read_line(sock: QtNetwork.QLocalSocket) -> bytes:
        buf = b""
        while b"\n" not in buf:
            if not sock.waitForReadyRead(5000):
                break
            buf += sock.readAll().data()
        line, _, _ = buf.partition(b"\n")
        return line

    def _run(self, session_name: str, message: str):
        sock = QtNetwork.QLocalSocket()
        sock.connectToServer(server_name(session_name))
        if not sock.waitForConnected(5000):
            self.reply = {}
            return
        hello_line = self._read_line(sock)
        self.hello = json.loads(hello_line.decode()) if hello_line else {}
        sock.write(message.encode())
        sock.waitForBytesWritten(5000)
        reply_line = self._read_line(sock)
        sock.disconnectFromServer()
        self.reply = json.loads(reply_line.decode()) if reply_line else {}

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


def _can_connect(session: str, timeout_ms: int = 500) -> bool:
    sock = QtNetwork.QLocalSocket()
    sock.connectToServer(server_name(session))
    ok = sock.waitForConnected(timeout_ms)
    sock.abort()
    return ok


def test_server_accepts_connections(server, session_name):
    assert _can_connect(session_name)


def _make_server(session_name, insert_fn):
    """Construct a SessionServer with no-op stubs for everything but insert."""
    return SessionServer(
        session_name,
        insert_fn,
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(return_value=StatusInfoMessage()),
        MagicMock(return_value=ItemsMessage(items=())),
        MagicMock(return_value=ItemMessage(item=None)),
        MagicMock(return_value=ViewInfoMessage()),
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(),
    )


def test_server_shutdown_rejects_new_connections(qtbot, session_name, mock_insert_fn):
    srv = _make_server(session_name, mock_insert_fn)
    assert srv.start()
    assert _can_connect(session_name)
    srv.shutdown()
    assert not _can_connect(session_name)


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
        on_done(["bad_file.png"], [])

    fn = MagicMock(side_effect=insert_with_errors)
    srv = _make_server(session_name, fn)
    assert srv.start()

    c = AsyncClient(session_name, add_msg([{"path": str(imgfile)}]))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "error"
    srv.shutdown()


def test_blocking_queues_requests(qtbot, session_name, imgfile):
    """Second add blocks until the first insert completes."""
    finish_callbacks: list = []

    def slow_insert(inserts, on_done):
        finish_callbacks.append(on_done)

    fn = MagicMock(side_effect=slow_insert)
    srv = _make_server(session_name, fn)
    assert srv.start()

    msg = add_msg([{"path": str(imgfile)}])

    c1 = AsyncClient(session_name, msg)
    qtbot.waitUntil(lambda: len(finish_callbacks) == 1, timeout=3000)

    c2 = AsyncClient(session_name, msg)
    qtbot.waitUntil(lambda: len(srv._queue) >= 1, timeout=3000)
    assert fn.call_count == 1

    finish_callbacks[0]([], [])
    qtbot.waitUntil(lambda: fn.call_count == 2, timeout=3000)

    qtbot.waitUntil(lambda: c1.done, timeout=3000)
    assert c1.reply["type"] == "ok"

    finish_callbacks[1]([], [])
    qtbot.waitUntil(lambda: c2.done, timeout=3000)
    assert c2.reply["type"] == "ok"

    srv.shutdown()


# -- parse_message: new/open/status -----------------------------------------


def test_parse_status_request():
    result = parse_message('{"type": "status"}')
    assert isinstance(result, StatusRequestMessage)


def test_parse_new_default_force():
    result = parse_message('{"type": "new"}')
    assert isinstance(result, NewMessage)
    assert result.force is False


def test_parse_new_with_force():
    result = parse_message('{"type": "new", "force": true}')
    assert isinstance(result, NewMessage)
    assert result.force is True


def test_parse_new_rejects_non_bool_force():
    result = parse_message('{"type": "new", "force": "yes"}')
    assert isinstance(result, ErrorMessage)


def test_parse_open_requires_path():
    result = parse_message('{"type": "open"}')
    assert isinstance(result, ErrorMessage)


def test_parse_open_with_path():
    result = parse_message('{"type": "open", "path": "/tmp/a.zref"}')
    assert isinstance(result, OpenMessage)
    assert result.path == "/tmp/a.zref"
    assert result.force is False


def test_parse_open_with_force():
    result = parse_message('{"type": "open", "path": "/tmp/a.zref", "force": true}')
    assert isinstance(result, OpenMessage)
    assert result.force is True


def test_parse_save_path_less():
    result = parse_message('{"type": "save"}')
    assert isinstance(result, SaveMessage)
    assert result.path is None
    assert result.force is False


def test_parse_save_with_path():
    result = parse_message('{"type": "save", "path": "/tmp/a.zref"}')
    assert isinstance(result, SaveMessage)
    assert result.path == "/tmp/a.zref"


def test_parse_save_with_force():
    result = parse_message('{"type": "save", "path": "/tmp/a.zref", "force": true}')
    assert isinstance(result, SaveMessage)
    assert result.force is True


def test_parse_save_rejects_empty_path():
    result = parse_message('{"type": "save", "path": ""}')
    assert isinstance(result, ErrorMessage)


def test_parse_save_rejects_non_bool_force():
    result = parse_message('{"type": "save", "force": "yes"}')
    assert isinstance(result, ErrorMessage)


# -- Integration: hello, status, new, open ---------------------------------


def test_hello_sent_on_connect(qtbot, server, session_name):
    """Server pushes hello with protocol_version + app_version on connect."""
    c = AsyncClient(session_name, make_msg({"type": "ping"}))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.hello is not None
    assert c.hello["type"] == "hello"
    assert c.hello["protocol_version"] == PROTOCOL_VERSION
    assert isinstance(c.hello.get("app_version"), str)


def test_status_returns_status_info(qtbot, server, session_name, mock_status_fn):
    mock_status_fn.return_value = StatusInfoMessage(
        loaded_file="/tmp/x.zref", item_count=7, dirty=True
    )
    c = AsyncClient(session_name, make_msg({"type": "status"}))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "status_info"
    assert c.reply["loaded_file"] == "/tmp/x.zref"
    assert c.reply["item_count"] == 7
    assert c.reply["dirty"] is True
    mock_status_fn.assert_called_once()


def test_new_calls_new_fn(qtbot, server, session_name, mock_new_fn):
    c = AsyncClient(session_name, make_msg({"type": "new", "force": True}))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "ok"
    mock_new_fn.assert_called_once()
    assert mock_new_fn.call_args[0][0] is True  # force


def test_new_reports_errors_from_callback(qtbot, session_name):
    """When new_fn reports errors (e.g., dirty), reply is error."""
    new_fn = MagicMock()
    new_fn.side_effect = lambda force, on_done: on_done(
        ["session has unsaved changes; pass force=true to discard"], []
    )
    srv = SessionServer(
        session_name,
        _mock_async_fn(),
        new_fn,
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(return_value=StatusInfoMessage()),
        MagicMock(return_value=ItemsMessage(items=())),
        MagicMock(return_value=ItemMessage(item=None)),
        MagicMock(return_value=ViewInfoMessage()),
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(),
    )
    assert srv.start()
    try:
        c = AsyncClient(session_name, make_msg({"type": "new"}))
        qtbot.waitUntil(lambda: c.done, timeout=3000)
        assert c.reply["type"] == "error"
        assert "unsaved" in c.reply["message"]
    finally:
        srv.shutdown()


def test_open_calls_open_fn(qtbot, server, session_name, mock_open_fn, tmp_path):
    target = tmp_path / "x.zref"
    target.write_bytes(b"stub")
    c = AsyncClient(
        session_name,
        make_msg({"type": "open", "path": str(target), "force": True}),
    )
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "ok"
    mock_open_fn.assert_called_once()
    called_path, called_force, _ = mock_open_fn.call_args[0]
    assert called_path == Path(str(target))
    assert called_force is True


def test_open_reports_errors_from_callback(qtbot, session_name):
    """When open_fn reports errors, reply is error."""
    open_fn = MagicMock()
    open_fn.side_effect = lambda path, force, on_done: on_done(
        ["file not found: /missing/x.zref"], []
    )
    srv = SessionServer(
        session_name,
        _mock_async_fn(),
        _mock_async_fn(),
        open_fn,
        _mock_async_fn(),
        MagicMock(return_value=StatusInfoMessage()),
        MagicMock(return_value=ItemsMessage(items=())),
        MagicMock(return_value=ItemMessage(item=None)),
        MagicMock(return_value=ViewInfoMessage()),
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(),
    )
    assert srv.start()
    try:
        c = AsyncClient(
            session_name,
            make_msg({"type": "open", "path": "/missing/x.zref"}),
        )
        qtbot.waitUntil(lambda: c.done, timeout=3000)
        assert c.reply["type"] == "error"
    finally:
        srv.shutdown()


def test_save_calls_save_fn(qtbot, server, session_name, mock_save_fn, mock_status_fn):
    mock_status_fn.return_value = StatusInfoMessage(
        loaded_file="/tmp/out.zref", item_count=3, dirty=False
    )
    c = AsyncClient(
        session_name,
        make_msg({"type": "save", "path": "/tmp/out.zref", "force": True}),
    )
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "ok"
    # ok reply is enriched with the post-save status_info.
    assert c.reply["status"]["loaded_file"] == "/tmp/out.zref"
    assert c.reply["status"]["dirty"] is False
    mock_save_fn.assert_called_once()
    called_path, called_force, _ = mock_save_fn.call_args[0]
    assert called_path == "/tmp/out.zref"
    assert called_force is True


def test_save_path_less_passes_none(qtbot, server, session_name, mock_save_fn):
    c = AsyncClient(session_name, make_msg({"type": "save"}))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "ok"
    mock_save_fn.assert_called_once()
    called_path, _, _ = mock_save_fn.call_args[0]
    assert called_path is None


def test_save_reports_errors_from_callback(qtbot, session_name):
    """When save_fn reports errors, reply is error."""
    save_fn = MagicMock()
    save_fn.side_effect = lambda path, force, on_done: on_done(
        ["session has no backing file; provide a path"], []
    )
    srv = SessionServer(
        session_name,
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        save_fn,
        MagicMock(return_value=StatusInfoMessage()),
        MagicMock(return_value=ItemsMessage(items=())),
        MagicMock(return_value=ItemMessage(item=None)),
        MagicMock(return_value=ViewInfoMessage()),
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(),
    )
    assert srv.start()
    try:
        c = AsyncClient(session_name, make_msg({"type": "save"}))
        qtbot.waitUntil(lambda: c.done, timeout=3000)
        assert c.reply["type"] == "error"
        assert "backing file" in c.reply["message"]
    finally:
        srv.shutdown()


def test_writes_serialize_through_queue(qtbot, session_name, imgfile):
    """add → new → open arriving back-to-back run one at a time."""
    add_callbacks: list = []
    new_callbacks: list = []
    open_callbacks: list = []

    def slow_add(inserts, on_done):
        add_callbacks.append(on_done)

    def slow_new(force, on_done):
        new_callbacks.append(on_done)

    def slow_open(path, force, on_done):
        open_callbacks.append(on_done)

    srv = SessionServer(
        session_name,
        MagicMock(side_effect=slow_add),
        MagicMock(side_effect=slow_new),
        MagicMock(side_effect=slow_open),
        _mock_async_fn(),
        MagicMock(return_value=StatusInfoMessage()),
        MagicMock(return_value=ItemsMessage(items=())),
        MagicMock(return_value=ItemMessage(item=None)),
        MagicMock(return_value=ViewInfoMessage()),
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(),
    )
    assert srv.start()
    try:
        c1 = AsyncClient(session_name, add_msg([{"path": str(imgfile)}]))
        qtbot.waitUntil(lambda: len(add_callbacks) == 1, timeout=3000)

        # While add is in flight, new and open should queue, not fire.
        c2 = AsyncClient(session_name, make_msg({"type": "new", "force": True}))
        c3 = AsyncClient(
            session_name,
            make_msg({"type": "open", "path": str(imgfile), "force": True}),
        )
        qtbot.waitUntil(lambda: len(srv._queue) >= 2, timeout=3000)
        assert len(new_callbacks) == 0
        assert len(open_callbacks) == 0

        # Drain in order.
        add_callbacks[0]([], [])
        qtbot.waitUntil(lambda: len(new_callbacks) == 1, timeout=3000)
        new_callbacks[0]([], [])
        qtbot.waitUntil(lambda: len(open_callbacks) == 1, timeout=3000)
        open_callbacks[0]([], [])

        qtbot.waitUntil(lambda: c1.done and c2.done and c3.done, timeout=3000)
        assert c1.reply["type"] == "ok"
        assert c2.reply["type"] == "ok"
        assert c3.reply["type"] == "ok"
    finally:
        srv.shutdown()


# -- parse_message: list/get/view -------------------------------------------


def test_parse_list_request():
    result = parse_message('{"type": "list"}')
    assert isinstance(result, ListRequestMessage)


def test_parse_get_request():
    result = parse_message('{"type": "get", "id": "abc123"}')
    assert isinstance(result, GetRequestMessage)
    assert result.id == "abc123"


def test_parse_get_missing_id():
    result = parse_message('{"type": "get"}')
    assert isinstance(result, ErrorMessage)


def test_parse_get_empty_id():
    result = parse_message('{"type": "get", "id": ""}')
    assert isinstance(result, ErrorMessage)


def test_parse_view_request():
    result = parse_message('{"type": "view"}')
    assert isinstance(result, ViewRequestMessage)


# -- Integration: list/get/view --------------------------------------------


def test_list_returns_items(qtbot, server, session_name, mock_list_fn):
    mock_list_fn.return_value = ItemsMessage(
        items=(
            {"id": "a", "type": "pixmap", "x": 0.0, "y": 0.0},
            {"id": "b", "type": "text", "x": 100.0, "y": 50.0},
        )
    )
    c = AsyncClient(session_name, make_msg({"type": "list"}))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "items"
    assert len(c.reply["items"]) == 2
    assert c.reply["items"][0]["id"] == "a"
    mock_list_fn.assert_called_once()


def test_get_returns_item(qtbot, server, session_name, mock_get_fn):
    mock_get_fn.return_value = ItemMessage(
        item={"id": "xyz", "type": "pixmap", "x": 12.0, "y": 34.0}
    )
    c = AsyncClient(session_name, make_msg({"type": "get", "id": "xyz"}))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "item"
    assert c.reply["item"]["id"] == "xyz"
    mock_get_fn.assert_called_once_with("xyz")


def test_get_unknown_id_returns_null(qtbot, server, session_name, mock_get_fn):
    mock_get_fn.return_value = ItemMessage(item=None)
    c = AsyncClient(session_name, make_msg({"type": "get", "id": "missing"}))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "item"
    assert c.reply["item"] is None


def test_view_returns_viewport_state(qtbot, server, session_name, mock_view_fn):
    mock_view_fn.return_value = ViewInfoMessage(
        center_x=10.5,
        center_y=-20.0,
        zoom=1.5,
        window={"x": 0, "y": 0, "width": 800, "height": 600},
    )
    c = AsyncClient(session_name, make_msg({"type": "view"}))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "view_info"
    assert c.reply["center_x"] == 10.5
    assert c.reply["zoom"] == 1.5
    assert c.reply["window"]["width"] == 800
    mock_view_fn.assert_called_once()


def test_reads_bypass_mutation_queue(qtbot, session_name, imgfile):
    """list/get/view return immediately even while add is in flight."""
    add_callbacks: list = []

    def slow_add(inserts, on_done):
        add_callbacks.append(on_done)

    srv = SessionServer(
        session_name,
        MagicMock(side_effect=slow_add),
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(return_value=StatusInfoMessage()),
        MagicMock(return_value=ItemsMessage(items=())),
        MagicMock(return_value=ItemMessage(item=None)),
        MagicMock(return_value=ViewInfoMessage()),
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(),
    )
    assert srv.start()
    try:
        c_add = AsyncClient(session_name, add_msg([{"path": str(imgfile)}]))
        qtbot.waitUntil(lambda: len(add_callbacks) == 1, timeout=3000)

        # Reads should fire and reply while the add is still pending.
        c_list = AsyncClient(session_name, make_msg({"type": "list"}))
        qtbot.waitUntil(lambda: c_list.done, timeout=3000)
        assert c_list.reply["type"] == "items"

        # Drain the add.
        add_callbacks[0]([], [])
        qtbot.waitUntil(lambda: c_add.done, timeout=3000)
        assert c_add.reply["type"] == "ok"
    finally:
        srv.shutdown()


# -- parse_message: add transform fields ------------------------------------


def test_parse_add_accepts_transform_fields(imgfile):
    result = parse_message(
        json.dumps(
            {
                "type": "add",
                "payload": [
                    {
                        "path": str(imgfile),
                        "x": 10.5,
                        "y": -3.0,
                        "scale": 2.0,
                        "rotation": 45.0,
                        "z": 1.5,
                        "flip": -1,
                        "opacity": 0.5,
                    }
                ],
            }
        )
    )
    assert isinstance(result, AddMessage)
    img = result.images[0]
    assert img.x == 10.5
    assert img.y == -3.0
    assert img.scale == 2.0
    assert img.rotation == 45.0
    assert img.z == 1.5
    assert img.flip == -1
    assert img.opacity == 0.5


def test_parse_add_rejects_non_number_x(imgfile):
    result = parse_message(
        json.dumps({"type": "add", "payload": [{"path": str(imgfile), "x": "10"}]})
    )
    assert isinstance(result, ErrorMessage)


def test_parse_add_rejects_invalid_flip(imgfile):
    result = parse_message(
        json.dumps({"type": "add", "payload": [{"path": str(imgfile), "flip": 0}]})
    )
    assert isinstance(result, ErrorMessage)


def test_parse_add_rejects_out_of_range_opacity(imgfile):
    result = parse_message(
        json.dumps({"type": "add", "payload": [{"path": str(imgfile), "opacity": 1.5}]})
    )
    assert isinstance(result, ErrorMessage)


# -- parse_message: add_text ------------------------------------------------


def test_parse_add_text_basic():
    result = parse_message(
        json.dumps({"type": "add_text", "payload": [{"text": "hello"}]})
    )
    assert isinstance(result, AddTextMessage)
    assert len(result.texts) == 1
    assert result.texts[0].text == "hello"


def test_parse_add_text_with_transforms():
    result = parse_message(
        json.dumps(
            {
                "type": "add_text",
                "payload": [
                    {"text": "world", "x": 5, "y": 7, "scale": 0.5, "rotation": 90}
                ],
            }
        )
    )
    assert isinstance(result, AddTextMessage)
    t = result.texts[0]
    assert t.x == 5
    assert t.y == 7
    assert t.scale == 0.5
    assert t.rotation == 90


def test_parse_add_text_with_wrap():
    result = parse_message(
        json.dumps({"type": "add_text", "payload": [{"text": "hi", "wrap": 0}]})
    )
    assert isinstance(result, AddTextMessage)
    assert result.texts[0].wrap == 0


def test_parse_add_text_defaults_wrap_to_none():
    result = parse_message(
        json.dumps({"type": "add_text", "payload": [{"text": "hi"}]})
    )
    assert isinstance(result, AddTextMessage)
    assert result.texts[0].wrap is None


def test_parse_add_text_rejects_out_of_range_wrap():
    result = parse_message(
        json.dumps({"type": "add_text", "payload": [{"text": "hi", "wrap": -1}]})
    )
    assert isinstance(result, ErrorMessage)


def test_parse_add_text_requires_text():
    result = parse_message(json.dumps({"type": "add_text", "payload": [{}]}))
    assert isinstance(result, ErrorMessage)


def test_parse_add_text_requires_non_empty_payload():
    result = parse_message('{"type": "add_text", "payload": []}')
    assert isinstance(result, ErrorMessage)


# -- Integration: add transforms / add_text dispatch ------------------------


def test_add_transforms_round_trip(
    qtbot, server, session_name, mock_insert_fn, imgfile
):
    c = AsyncClient(
        session_name,
        add_msg(
            [
                {
                    "path": str(imgfile),
                    "x": 100.0,
                    "y": 200.0,
                    "scale": 1.5,
                    "rotation": 30.0,
                    "opacity": 0.8,
                }
            ]
        ),
    )
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "ok"
    inserts = mock_insert_fn.call_args[0][0]
    assert inserts[0].x == 100.0
    assert inserts[0].y == 200.0
    assert inserts[0].scale == 1.5
    assert inserts[0].rotation == 30.0
    assert inserts[0].opacity == 0.8


def test_add_text_dispatches_to_insert_text_fn(
    qtbot, server, session_name, mock_insert_text_fn
):
    c = AsyncClient(
        session_name,
        make_msg(
            {
                "type": "add_text",
                "payload": [{"text": "# hello", "x": 1.0, "y": 2.0}],
            }
        ),
    )
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "ok"
    mock_insert_text_fn.assert_called_once()
    texts = mock_insert_text_fn.call_args[0][0]
    assert texts[0].text == "# hello"
    assert texts[0].x == 1.0
    assert texts[0].y == 2.0


def test_add_text_serializes_with_other_writes(qtbot, session_name, imgfile):
    """add_text queues alongside add — both writes use the same slot."""
    add_callbacks: list = []
    text_callbacks: list = []

    def slow_add(inserts, on_done):
        add_callbacks.append(on_done)

    def slow_text(inserts, on_done):
        text_callbacks.append(on_done)

    srv = SessionServer(
        session_name,
        MagicMock(side_effect=slow_add),
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(return_value=StatusInfoMessage()),
        MagicMock(return_value=ItemsMessage(items=())),
        MagicMock(return_value=ItemMessage(item=None)),
        MagicMock(return_value=ViewInfoMessage()),
        MagicMock(side_effect=slow_text),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(),
    )
    assert srv.start()
    try:
        c1 = AsyncClient(session_name, add_msg([{"path": str(imgfile)}]))
        qtbot.waitUntil(lambda: len(add_callbacks) == 1, timeout=3000)

        # add_text must wait for add to drain.
        c2 = AsyncClient(
            session_name, make_msg({"type": "add_text", "payload": [{"text": "x"}]})
        )
        qtbot.waitUntil(lambda: len(srv._queue) >= 1, timeout=3000)
        assert len(text_callbacks) == 0

        add_callbacks[0]([], [])
        qtbot.waitUntil(lambda: len(text_callbacks) == 1, timeout=3000)
        text_callbacks[0]([], [])

        qtbot.waitUntil(lambda: c1.done and c2.done, timeout=3000)
        assert c1.reply["type"] == "ok"
        assert c2.reply["type"] == "ok"
    finally:
        srv.shutdown()


# -- parse_message: edit/delete --------------------------------------------


def test_parse_edit_basic():
    result = parse_message(
        json.dumps(
            {"type": "edit", "payload": [{"id": "abc", "x": 1.0, "title": "hi"}]}
        )
    )
    assert isinstance(result, EditMessage)
    edit = result.edits[0]
    assert edit["id"] == "abc"
    assert edit["x"] == 1.0
    assert edit["title"] == "hi"


def test_parse_edit_clears_title_on_empty_string():
    result = parse_message(
        json.dumps({"type": "edit", "payload": [{"id": "abc", "title": ""}]})
    )
    assert isinstance(result, EditMessage)
    assert result.edits[0]["title"] is None


def test_parse_edit_clears_caption_on_null():
    result = parse_message(
        json.dumps({"type": "edit", "payload": [{"id": "abc", "caption": None}]})
    )
    assert isinstance(result, EditMessage)
    assert result.edits[0]["caption"] is None


def test_parse_edit_clears_text_on_empty_string():
    result = parse_message(
        json.dumps({"type": "edit", "payload": [{"id": "abc", "text": ""}]})
    )
    assert isinstance(result, EditMessage)
    assert result.edits[0]["text"] is None


def test_parse_edit_sets_wrap():
    result = parse_message(
        json.dumps({"type": "edit", "payload": [{"id": "abc", "wrap": 0}]})
    )
    assert isinstance(result, EditMessage)
    assert result.edits[0]["wrap"] == 0


def test_parse_edit_rejects_null_wrap():
    result = parse_message(
        json.dumps({"type": "edit", "payload": [{"id": "abc", "wrap": None}]})
    )
    assert isinstance(result, ErrorMessage)


def test_parse_edit_requires_id():
    result = parse_message(json.dumps({"type": "edit", "payload": [{"x": 1.0}]}))
    assert isinstance(result, ErrorMessage)


def test_parse_edit_rejects_invalid_field():
    result = parse_message(
        json.dumps({"type": "edit", "payload": [{"id": "abc", "title": 123}]})
    )
    assert isinstance(result, ErrorMessage)


def test_parse_edit_rejects_invalid_flip():
    result = parse_message(
        json.dumps({"type": "edit", "payload": [{"id": "abc", "flip": 2}]})
    )
    assert isinstance(result, ErrorMessage)


def test_parse_delete_basic():
    result = parse_message('{"type": "delete", "ids": ["a", "b"]}')
    assert isinstance(result, DeleteMessage)
    assert result.ids == ("a", "b")


def test_parse_delete_requires_non_empty():
    result = parse_message('{"type": "delete", "ids": []}')
    assert isinstance(result, ErrorMessage)


def test_parse_delete_rejects_non_string_id():
    result = parse_message('{"type": "delete", "ids": [123]}')
    assert isinstance(result, ErrorMessage)


def test_parse_quit():
    result = parse_message('{"type": "quit"}')
    assert isinstance(result, QuitMessage)


def test_quit_acks_and_calls_quit_fn(qtbot, server, session_name, mock_quit_fn):
    c = AsyncClient(session_name, make_msg({"type": "quit"}))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "ok"
    qtbot.waitUntil(lambda: mock_quit_fn.called, timeout=3000)


# -- Integration: edit/delete dispatch -------------------------------------


def test_edit_dispatches_to_edit_fn(qtbot, server, session_name, mock_edit_fn):
    c = AsyncClient(
        session_name,
        make_msg(
            {
                "type": "edit",
                "payload": [{"id": "abc", "x": 5.0, "title": "renamed"}],
            }
        ),
    )
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "ok"
    mock_edit_fn.assert_called_once()
    edits = mock_edit_fn.call_args[0][0]
    assert edits[0]["id"] == "abc"
    assert edits[0]["x"] == 5.0
    assert edits[0]["title"] == "renamed"


def test_edit_reports_unknown_id_error(qtbot, session_name):
    def edit_with_missing(edits, on_done):
        on_done(["unknown id: missing"], [])

    fn = MagicMock(side_effect=edit_with_missing)
    srv = SessionServer(
        session_name,
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(return_value=StatusInfoMessage()),
        MagicMock(return_value=ItemsMessage(items=())),
        MagicMock(return_value=ItemMessage(item=None)),
        MagicMock(return_value=ViewInfoMessage()),
        _mock_async_fn(),
        fn,
        _mock_async_fn(),
        MagicMock(),
    )
    assert srv.start()
    try:
        c = AsyncClient(
            session_name,
            make_msg({"type": "edit", "payload": [{"id": "missing", "x": 0.0}]}),
        )
        qtbot.waitUntil(lambda: c.done, timeout=3000)
        assert c.reply["type"] == "error"
        assert "unknown id" in c.reply["message"]
    finally:
        srv.shutdown()


def test_delete_dispatches_to_delete_fn(qtbot, server, session_name, mock_delete_fn):
    c = AsyncClient(session_name, make_msg({"type": "delete", "ids": ["a", "b"]}))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "ok"
    mock_delete_fn.assert_called_once()
    ids = mock_delete_fn.call_args[0][0]
    assert ids == ["a", "b"]


def test_delete_reports_unknown_id_error(qtbot, session_name):
    def delete_with_missing(ids, on_done):
        on_done(["unknown id: nope"], [])

    fn = MagicMock(side_effect=delete_with_missing)
    srv = SessionServer(
        session_name,
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(return_value=StatusInfoMessage()),
        MagicMock(return_value=ItemsMessage(items=())),
        MagicMock(return_value=ItemMessage(item=None)),
        MagicMock(return_value=ViewInfoMessage()),
        _mock_async_fn(),
        _mock_async_fn(),
        fn,
        MagicMock(),
    )
    assert srv.start()
    try:
        c = AsyncClient(session_name, make_msg({"type": "delete", "ids": ["nope"]}))
        qtbot.waitUntil(lambda: c.done, timeout=3000)
        assert c.reply["type"] == "error"
        assert "unknown id" in c.reply["message"]
    finally:
        srv.shutdown()


# -- Integration: ok reply carries created ids -----------------------------


def test_add_reply_includes_created_ids(qtbot, session_name, imgfile):
    """insert_fn passes ids via its callback; server echoes them in the ok reply."""

    def insert_with_ids(inserts, on_done):
        on_done([], ["id-aaaa", "id-bbbb"])

    fn = MagicMock(side_effect=insert_with_ids)
    srv = SessionServer(
        session_name,
        fn,
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(return_value=StatusInfoMessage()),
        MagicMock(return_value=ItemsMessage(items=())),
        MagicMock(return_value=ItemMessage(item=None)),
        MagicMock(return_value=ViewInfoMessage()),
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(),
    )
    assert srv.start()
    try:
        c = AsyncClient(session_name, add_msg([{"path": str(imgfile)}]))
        qtbot.waitUntil(lambda: c.done, timeout=3000)
        assert c.reply["type"] == "ok"
        assert c.reply["ids"] == ["id-aaaa", "id-bbbb"]
    finally:
        srv.shutdown()


def test_add_text_reply_includes_created_ids(qtbot, session_name):
    def text_with_ids(inserts, on_done):
        on_done([], ["text-id-1"])

    fn = MagicMock(side_effect=text_with_ids)
    srv = SessionServer(
        session_name,
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(return_value=StatusInfoMessage()),
        MagicMock(return_value=ItemsMessage(items=())),
        MagicMock(return_value=ItemMessage(item=None)),
        MagicMock(return_value=ViewInfoMessage()),
        fn,
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(),
    )
    assert srv.start()
    try:
        c = AsyncClient(
            session_name,
            make_msg({"type": "add_text", "payload": [{"text": "hi"}]}),
        )
        qtbot.waitUntil(lambda: c.done, timeout=3000)
        assert c.reply["type"] == "ok"
        assert c.reply["ids"] == ["text-id-1"]
    finally:
        srv.shutdown()


def test_edit_ok_reply_has_empty_ids(qtbot, server, session_name):
    """Non-creating ops still emit ids in the ok reply, but as an empty list."""
    c = AsyncClient(
        session_name,
        make_msg({"type": "edit", "payload": [{"id": "abc", "x": 1.0}]}),
    )
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "ok"
    assert c.reply["ids"] == []


# -- Server-side ok-reply enrichment ---------------------------------------


def test_edit_reply_includes_post_edit_items(qtbot, session_name):
    """Server fetches the edited item snapshots and includes them in ok reply."""
    fake_item = {
        "id": "abc",
        "type": "pixmap",
        "x": 5.0,
        "y": 6.0,
        "data": {"title": "new"},
    }
    edit_fn = _mock_async_fn()
    get_fn = MagicMock(return_value=ItemMessage(item=fake_item))
    srv = SessionServer(
        session_name,
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(return_value=StatusInfoMessage()),
        MagicMock(return_value=ItemsMessage(items=())),
        get_fn,
        MagicMock(return_value=ViewInfoMessage()),
        _mock_async_fn(),
        edit_fn,
        _mock_async_fn(),
        MagicMock(),
    )
    assert srv.start()
    try:
        c = AsyncClient(
            session_name,
            make_msg({"type": "edit", "payload": [{"id": "abc", "x": 5.0}]}),
        )
        qtbot.waitUntil(lambda: c.done, timeout=3000)
        assert c.reply["type"] == "ok"
        assert c.reply["items"] == [fake_item]
        get_fn.assert_called_once_with("abc")
    finally:
        srv.shutdown()


def test_open_reply_includes_status(qtbot, session_name, tmp_path):
    """Server calls status_fn after open and includes the snapshot in the reply."""
    target = tmp_path / "x.zref"
    target.write_bytes(b"stub")
    status_fn = MagicMock(
        return_value=StatusInfoMessage(
            loaded_file=str(target), item_count=42, dirty=False
        )
    )
    srv = SessionServer(
        session_name,
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        status_fn,
        MagicMock(return_value=ItemsMessage(items=())),
        MagicMock(return_value=ItemMessage(item=None)),
        MagicMock(return_value=ViewInfoMessage()),
        _mock_async_fn(),
        _mock_async_fn(),
        _mock_async_fn(),
        MagicMock(),
    )
    assert srv.start()
    try:
        c = AsyncClient(
            session_name,
            make_msg({"type": "open", "path": str(target), "force": True}),
        )
        qtbot.waitUntil(lambda: c.done, timeout=3000)
        assert c.reply["type"] == "ok"
        assert c.reply["status"]["loaded_file"] == str(target)
        assert c.reply["status"]["item_count"] == 42
        # Wire envelope type is stripped before going into the open reply.
        assert "type" not in c.reply["status"]
        status_fn.assert_called()
    finally:
        srv.shutdown()


def test_new_reply_has_no_items_or_status(qtbot, server, session_name):
    """new doesn't enrich the reply — items/status stay at their defaults."""
    c = AsyncClient(session_name, make_msg({"type": "new", "force": True}))
    qtbot.waitUntil(lambda: c.done, timeout=3000)
    assert c.reply["type"] == "ok"
    assert c.reply["items"] == []
    assert c.reply["status"] is None
