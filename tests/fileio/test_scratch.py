import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from beeref.fileio.schema import APPLICATION_ID, USER_VERSION
from beeref.fileio.scratch import (
    create_scratch_file,
    delete_scratch_file,
    derive_swp_path,
    derive_untitled_swp_path,
    list_recovery_files,
)


def test_derive_swp_path_deterministic(settings):
    path1 = derive_swp_path(Path("/some/path/file.bee"))
    path2 = derive_swp_path(Path("/some/path/file.bee"))
    assert path1 == path2


def test_derive_swp_path_different_for_different_files(settings):
    path1 = derive_swp_path(Path("/some/path/file.bee"))
    path2 = derive_swp_path(Path("/other/path/file.bee"))
    assert path1 != path2


def test_derive_swp_path_in_recovery_dir(settings):
    path = derive_swp_path(Path("/some/path/file.bee"))
    assert "recovery" in str(path)
    assert path.name.endswith(".bee.swp")
    assert "file_" in path.name


def test_derive_untitled_swp_path(settings):
    path = derive_untitled_swp_path()
    assert "recovery" in str(path)
    assert "untitled_" in path.name
    assert path.name.endswith(".bee.swp")


def test_create_scratch_file_copies_existing(settings):
    with tempfile.NamedTemporaryFile(suffix=".bee", delete=False) as f:
        f.write(b"test content 12345")
        original = Path(f.name)
    try:
        swp = create_scratch_file(original)
        assert swp.exists()
        assert swp.read_bytes() == b"test content 12345"
        swp.unlink()
    finally:
        original.unlink()


def test_create_scratch_file_reports_progress(settings):
    with tempfile.NamedTemporaryFile(suffix=".bee", delete=False) as f:
        f.write(b"x" * 1024)
        original = Path(f.name)
    try:
        worker = MagicMock()
        swp = create_scratch_file(original, worker=worker)
        worker.begin_processing.emit.assert_called_once_with(100)
        worker.progress.emit.assert_called()
        swp.unlink()
    finally:
        original.unlink()


def test_create_scratch_file_none_creates_empty_db(settings):
    swp = create_scratch_file(None)
    assert swp.exists()
    # Verify it's a valid sqlite db with the schema
    conn = sqlite3.connect(swp)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = [t[0] for t in tables]
    assert "items" in table_names
    assert "sqlar" in table_names
    conn.close()
    swp.unlink()


def test_create_scratch_file_none_sets_pragmas(settings):
    """Empty scratch files must set version pragmas so SQLiteIO skips migration."""
    swp = create_scratch_file(None)
    conn = sqlite3.connect(swp)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    app_id = conn.execute("PRAGMA application_id").fetchone()[0]
    conn.close()
    swp.unlink()
    assert version == USER_VERSION
    assert app_id == APPLICATION_ID


def test_delete_scratch_file(settings):
    with tempfile.NamedTemporaryFile(suffix=".bee.swp", delete=False) as f:
        path = Path(f.name)
    assert path.exists()
    delete_scratch_file(path)
    assert not path.exists()


def test_delete_scratch_file_nonexistent(settings):
    # Should not raise
    delete_scratch_file(Path("/nonexistent/path.bee.swp"))


def test_list_recovery_files(settings):
    recovery_dir = Path(settings.get_recovery_dir())
    swp1 = recovery_dir / "test1.bee.swp"
    swp2 = recovery_dir / "test2.bee.swp"
    other = recovery_dir / "notaswp.txt"
    for path in (swp1, swp2, other):
        path.touch()

    files = list_recovery_files()
    assert len(files) == 2
    basenames = [f.name for f in files]
    assert "test1.bee.swp" in basenames
    assert "test2.bee.swp" in basenames


def test_list_recovery_files_empty(settings):
    files = list_recovery_files()
    assert files == []


def test_close_event_deletes_scratch_file(main_window, settings):
    """Closing the main window should delete the scratch file."""
    swp = create_scratch_file(None)
    main_window.view.scene._scratch_file = swp
    assert swp.exists()
    main_window.close()
    assert not swp.exists()


def test_clear_scene_deletes_scratch_file(view, settings):
    """Clearing the scene should delete the scratch file without creating a new one."""
    swp = create_scratch_file(None)
    view.scene._scratch_file = swp
    assert swp.exists()
    view.clear_scene()
    assert not swp.exists()
    assert view.scene._scratch_file is None
