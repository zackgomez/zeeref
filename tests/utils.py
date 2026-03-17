from pathlib import Path

from beeref.fileio.snapshot import IOResult, LoadResult, SaveResult


def queue2list(queue):
    qlist = []
    while not queue.empty():
        qlist.append(queue.get())
    return qlist


def assert_load_result(mock, filename: Path, has_errors: bool = False) -> LoadResult:
    """Assert a mock was called with a LoadResult and return it."""
    mock.assert_called_once()
    result = mock.call_args[0][0]
    assert isinstance(result, LoadResult), f"Expected LoadResult, got {type(result)}"
    assert result.filename == filename
    assert bool(result.errors) == has_errors
    return result


def assert_save_result(mock, filename: Path, has_errors: bool = False) -> SaveResult:
    """Assert a mock was called with a SaveResult and return it."""
    mock.assert_called_once()
    result = mock.call_args[0][0]
    assert isinstance(result, SaveResult), f"Expected SaveResult, got {type(result)}"
    assert result.filename == filename
    assert bool(result.errors) == has_errors
    return result


def assert_io_result(mock, filename: Path, has_errors: bool = False) -> IOResult:
    """Assert a mock was called with an IOResult and return it."""
    mock.assert_called_once()
    result = mock.call_args[0][0]
    assert isinstance(result, IOResult), f"Expected IOResult, got {type(result)}"
    assert result.filename == filename
    assert bool(result.errors) == has_errors
    return result


def assert_insert_images_result(
    mock,
    new_scene: bool,
    has_errors: bool = False,
    error_files: list[str] | None = None,
) -> IOResult:
    """Assert on_insert_images_finished was called with (bool, IOResult)."""
    mock.assert_called_once()
    args = mock.call_args[0]
    assert args[0] == new_scene
    result = args[1]
    assert isinstance(result, IOResult), f"Expected IOResult, got {type(result)}"
    assert bool(result.errors) == has_errors
    if error_files is not None:
        assert result.errors == error_files
    return result
