from zeeref.fileio.export import (
    exporter_registry,
    SceneToPixmapExporter,
)


def test_registry():
    assert exporter_registry["png"] == SceneToPixmapExporter
    assert exporter_registry["jpg"] == SceneToPixmapExporter
