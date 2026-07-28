from unittest.mock import patch, MagicMock

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtCore import Qt

from zeeref.items import ZeeTextItem, item_registry
from zeeref.types.snapshot import ItemSnapshot


def test_in_items_registry():
    assert item_registry["text"] == ZeeTextItem


@patch("zeeref.selection.SelectableMixin.init_selectable")
def test_init(selectable_mock, qapp):
    item = ZeeTextItem("foo bar")
    assert item.width
    assert item.height
    assert item.scale() == 1
    assert item._markdown == "foo bar"
    assert item.edit_mode is False
    assert item.is_image is False
    selectable_mock.assert_called_once()


def test_sample_color_at(qapp, scene):
    item = ZeeTextItem("foo bar")
    scene.addItem(item)
    assert item.sample_color_at(QtCore.QPointF(2.0, 2.0)) is None


def test_set_pos_center(qapp):
    item = ZeeTextItem("foo bar")
    with patch.object(
        item, "bounding_rect_unselected", return_value=QtCore.QRectF(0, 0, 200, 100)
    ):
        item.set_pos_center(QtCore.QPointF(0, 0))
        assert item.pos().x() == -100
        assert item.pos().y() == -50


def test_set_pos_center_when_scaled(qapp):
    item = ZeeTextItem("foo bar")
    item.setScale(2)
    with patch.object(
        item, "bounding_rect_unselected", return_value=QtCore.QRectF(0, 0, 200, 100)
    ):
        item.set_pos_center(QtCore.QPointF(0, 0))
        assert item.pos().x() == -200
        assert item.pos().y() == -100


def test_set_pos_center_when_rotated(qapp):
    item = ZeeTextItem("foo bar")
    item.setRotation(90)
    with patch.object(
        item, "bounding_rect_unselected", return_value=QtCore.QRectF(0, 0, 200, 100)
    ):
        item.set_pos_center(QtCore.QPointF(0, 0))
        assert item.pos().x() == 50
        assert item.pos().y() == -100


def test_get_extra_save_data(qapp):
    item = ZeeTextItem("foo bar", wrap=80)
    assert item.get_extra_save_data() == {"text": "foo bar", "wrap": 80}


@patch("zeeref.items.ZeeTextItem.boundingRect")
def test_contains_when_inside_bounds(brect_mock, qapp):
    brect_mock.return_value = QtCore.QRectF(20, 30, 50, 50)
    item = ZeeTextItem("foo bar")
    item.contains(QtCore.QPointF(33, 45)) is True
    brect_mock.assert_called_once_with()


@patch("zeeref.items.ZeeTextItem.boundingRect")
def test_contains_when_outside_bounds(brect_mock, qapp):
    brect_mock.return_value = QtCore.QRectF(20, 30, 50, 50)
    item = ZeeTextItem("foo bar")
    item.contains(QtCore.QPointF(19, 29)) is False
    brect_mock.assert_called_once_with()


@patch("PyQt6.QtWidgets.QGraphicsTextItem.paint")
def test_paint(paint_mock, qapp):
    item = ZeeTextItem("foo bar")
    item.paint_selectable = MagicMock()
    painter = MagicMock()
    option = MagicMock()
    item.paint(painter, option, "widget")
    item.paint_selectable.assert_called_once()
    painter.drawRect.assert_called_once()
    assert option.state == QtWidgets.QStyle.StateFlag.State_Enabled
    paint_mock.assert_called_once_with(painter, option, "widget")


def test_has_selection_outline_when_not_selected(scene):
    item = ZeeTextItem("foo bar")
    scene.addItem(item)
    item.setSelected(False)
    item.has_selection_outline() is False


def test_has_selection_outline_when_selected(scene):
    item = ZeeTextItem("foo bar")
    scene.addItem(item)
    item.setSelected(True)
    item.has_selection_outline() is True


def test_has_selection_handles_when_not_selected(scene):
    item = ZeeTextItem("foo bar")
    scene.addItem(item)
    item.setSelected(False)
    item2 = ZeeTextItem("baz")
    scene.addItem(item2)
    item2.setSelected(False)
    item.has_selection_handles() is False


def test_has_selection_handles_when_selected_single(scene):
    item = ZeeTextItem("foo bar")
    scene.addItem(item)
    item.setSelected(True)
    item2 = ZeeTextItem("baz")
    scene.addItem(item2)
    item2.setSelected(False)
    item.has_selection_handles() is True


def test_has_selection_handles_when_selected_multi(scene):
    item = ZeeTextItem("foo bar")
    scene.addItem(item)
    item.setSelected(True)
    item2 = ZeeTextItem("baz")
    scene.addItem(item2)
    item2.setSelected(True)
    item.has_selection_handles() is False


def test_has_selection_handles_when_selected_single_and_edit_mode(scene):
    item = ZeeTextItem("foo bar")
    item.edit_mode = False
    scene.addItem(item)
    item.setSelected(True)
    item2 = ZeeTextItem("baz")
    scene.addItem(item2)
    item2.setSelected(False)
    item.has_selection_handles() is False


def test_selection_action_items(qapp):
    item = ZeeTextItem("foo bar")
    assert item.selection_action_items() == [item]


def test_update_from_data(qapp):
    item = ZeeTextItem("foo bar")
    item.update_from_data(save_id=3, x=11, y=22, z=1.2, scale=2.5, rotation=45, flip=-1)
    assert item.save_id == 3
    assert item.pos() == QtCore.QPointF(11, 22)
    assert item.zValue() == 1.2
    assert item.rotation() == 45
    assert item.flip() == -1


def test_update_from_data_keeps_flip(qapp):
    item = ZeeTextItem("foo bar")
    item.do_flip()
    item.update_from_data(flip=-1)
    assert item.flip() == -1


def test_update_from_data_keeps_unset_values(qapp):
    item = ZeeTextItem("foo bar")
    item.setScale(3)
    item.update_from_data(rotation=45)
    assert item.scale() == 3
    assert item.flip() == 1


def test_create_from_data(qapp):
    item = ZeeTextItem.create_from_data(data={"text": "hello world"})
    assert item._markdown == "hello world"


def test_render_pins_natural_width(qapp):
    # Each render pins the item to its natural (unwrapped) width so block
    # elements like <hr> have a width to render into. Re-rendering must reflect
    # the new content's width, not stay constrained to a previously pinned one.
    item = ZeeTextItem("short", wrap=0)
    narrow = item.textWidth()
    assert narrow > 0
    item.set_markdown("a much much much much much much longer single line of text")
    assert item.textWidth() > narrow * 2
    item.set_markdown("short")
    assert abs(item.textWidth() - narrow) < 1


def test_wrap_limits_wide_text(qapp):
    unwrapped = ZeeTextItem("word " * 200, wrap=0)
    wrapped = ZeeTextItem("word " * 200, wrap=80)
    limit = wrapped.wrap_width()
    assert limit is not None
    assert wrapped.textWidth() <= limit
    assert wrapped.textWidth() < unwrapped.textWidth() / 2
    # The box hugs the wrapped text rather than padding out to the full limit.
    assert wrapped.boundingRect().width() == wrapped.textWidth()


def test_wrap_leaves_narrow_text_alone(qapp):
    natural = ZeeTextItem("short", wrap=0).textWidth()
    assert ZeeTextItem("short", wrap=80).textWidth() == natural


def test_wrap_applies_on_rerender(qapp):
    item = ZeeTextItem("short", wrap=80)
    limit = item.wrap_width()
    assert limit is not None
    item.set_markdown("word " * 200)
    assert item.textWidth() <= limit
    item.set_markdown("short")
    assert item.textWidth() < limit


def test_set_wrap_cols_rewraps(qapp):
    item = ZeeTextItem("word " * 200, wrap=0)
    unwrapped = item.textWidth()
    item.set_wrap_cols(80)
    wrapped = item.textWidth()
    assert wrapped < unwrapped / 2
    item.set_wrap_cols(0)
    assert item.textWidth() == unwrapped
    # Negative widths are clamped to "no wrapping" rather than inverting.
    item.set_wrap_cols(-5)
    assert item.wrap_cols == 0


def test_next_wrap_cols_cycles(qapp):
    item = ZeeTextItem("foo", wrap=0)
    assert item.next_wrap_cols() == 100
    item.set_wrap_cols(100)
    assert item.next_wrap_cols() == 80
    item.set_wrap_cols(80)
    assert item.next_wrap_cols() == 0
    # A width outside the cycle (e.g. set from the CLI) unwraps first.
    item.set_wrap_cols(72)
    assert item.next_wrap_cols() == 0


def test_edit_mode_keeps_wrap_width(qapp, scene):
    item = ZeeTextItem("word " * 200, wrap=80)
    scene.addItem(item)
    limit = item.wrap_width()
    assert limit is not None
    item.enter_edit_mode()
    assert item.textWidth() == limit


def test_edit_mode_unbounded_without_wrap(qapp, scene):
    item = ZeeTextItem("word " * 200, wrap=0)
    scene.addItem(item)
    item.enter_edit_mode()
    assert item.textWidth() == -1


def _snapshot(data):
    return ItemSnapshot(
        save_id="abc",
        type="text",
        x=0,
        y=0,
        z=0,
        scale=1,
        rotation=0,
        flip=1,
        data=data,
        created_at=0,
    )


def test_from_snapshot_restores_wrap(qapp):
    item = ZeeTextItem.from_snapshot(_snapshot({"text": "foo", "wrap": 80}))
    assert item.wrap_cols == 80


def test_from_snapshot_without_wrap_stays_unwrapped(qapp):
    # Files written before wrapping existed must not re-flow when opened.
    item = ZeeTextItem.from_snapshot(_snapshot({"text": "word " * 200}))
    assert item.wrap_cols == 0
    assert item.wrap_width() is None


def test_create_copy(qapp):
    item = ZeeTextItem("foo bar", wrap=80)
    item.setPos(20, 30)
    item.setRotation(33)
    item.do_flip()
    item.setZValue(0.5)
    item.setScale(2.2)

    copy = item.create_copy()
    assert copy._markdown == "foo bar"
    assert copy.pos() == QtCore.QPointF(20, 30)
    assert copy.rotation() == 33
    assert copy.flip() == -1
    assert copy.zValue() == 0.5
    assert copy.scale() == 2.2
    assert copy.wrap_cols == 80


def test_enter_edit_mode(scene):
    item = ZeeTextItem("foo bar")
    scene.addItem(item)
    item.enter_edit_mode()
    assert item.edit_mode is True
    assert scene.edit_item == item
    flags = item.textInteractionFlags()
    assert flags == Qt.TextInteractionFlag.TextEditorInteraction


@patch("PyQt6.QtGui.QTextCursor")
@patch("zeeref.items.ZeeTextItem.setTextCursor")
def test_exit_edit_mode(setcursor_mock, cursor_mock, scene):
    item = ZeeTextItem("foo bar")
    item.edit_mode = True
    item.old_text = "old"
    scene.addItem(item)
    scene.edit_item = item
    item.exit_edit_mode()
    assert item.edit_mode is False
    assert scene.edit_item is None
    flags = item.textInteractionFlags()
    assert flags == Qt.TextInteractionFlag.NoTextInteraction
    cursor_mock.assert_called_once_with(item.document())
    setcursor_mock.assert_called_once_with(cursor_mock.return_value)
    assert scene.edit_item is None


def test_exit_edit_mode_when_text_empty(scene):
    item = ZeeTextItem(" \r\n\t")
    item.edit_mode = True
    item.old_text = "old"
    scene.addItem(item)
    scene.edit_item = item
    item.exit_edit_mode()
    assert item.edit_mode is False
    assert scene.edit_item is None
    flags = item.textInteractionFlags()
    assert flags == Qt.TextInteractionFlag.NoTextInteraction
    assert item.scene() is None
    assert scene.items() == []
    assert scene.edit_item is None


@patch("PyQt6.QtGui.QTextCursor")
@patch("zeeref.items.ZeeTextItem.setTextCursor")
def test_exit_edit_mode_when_commit_false(setcursor_mock, cursor_mock, scene):
    item = ZeeTextItem("foo bar")
    item.edit_mode = True
    item.old_text = "old"
    scene.addItem(item)
    scene.edit_item = item
    item.exit_edit_mode(commit=False)
    assert item.edit_mode is False
    assert scene.edit_item is None
    flags = item.textInteractionFlags()
    assert flags == Qt.TextInteractionFlag.NoTextInteraction
    cursor_mock.assert_called_once_with(item.document())
    setcursor_mock.assert_called_once_with(cursor_mock.return_value)
    assert scene.edit_item is None
    assert item._markdown == "old"


@patch("PyQt6.QtWidgets.QGraphicsTextItem.keyPressEvent")
@patch("zeeref.items.ZeeTextItem.exit_edit_mode")
def test_key_press_event_any_key(exit_mock, key_press_mock, scene):
    item = ZeeTextItem("foo bar")
    scene.addItem(item)
    scene.edit_item = item
    event = MagicMock()
    event.key.return_value = Qt.Key.Key_T
    event.modifiers.return_value = Qt.KeyboardModifier.NoModifier
    item.keyPressEvent(event)
    key_press_mock.assert_called_once_with(event)
    exit_mock.assert_not_called()
    assert scene.edit_item == item


@patch("PyQt6.QtWidgets.QGraphicsTextItem.keyPressEvent")
@patch("zeeref.items.ZeeTextItem.exit_edit_mode")
def test_key_press_event_shift_return(exit_mock, key_press_mock, scene):
    item = ZeeTextItem("foo bar")
    scene.addItem(item)
    scene.edit_item = item
    event = MagicMock()
    event.key.return_value = Qt.Key.Key_Return
    event.modifiers.return_value = Qt.KeyboardModifier.ShiftModifier
    item.keyPressEvent(event)
    key_press_mock.assert_not_called()
    exit_mock.assert_called_once_with()


@patch("PyQt6.QtWidgets.QGraphicsTextItem.keyPressEvent")
@patch("zeeref.items.ZeeTextItem.exit_edit_mode")
def test_key_press_event_shift_enter(exit_mock, key_press_mock, scene):
    item = ZeeTextItem("foo bar")
    scene.addItem(item)
    scene.edit_item = item
    event = MagicMock()
    event.key.return_value = Qt.Key.Key_Enter
    event.modifiers.return_value = Qt.KeyboardModifier.ShiftModifier
    item.keyPressEvent(event)
    key_press_mock.assert_not_called()
    exit_mock.assert_called_once_with()


@patch("PyQt6.QtWidgets.QGraphicsTextItem.keyPressEvent")
@patch("zeeref.items.ZeeTextItem.exit_edit_mode")
def test_key_press_event_return(exit_mock, key_press_mock, scene):
    item = ZeeTextItem("foo bar")
    scene.addItem(item)
    scene.edit_item = item
    event = MagicMock()
    event.key.return_value = Qt.Key.Key_Return
    event.modifiers.return_value = Qt.KeyboardModifier.NoModifier
    item.keyPressEvent(event)
    key_press_mock.assert_called_once_with(event)
    exit_mock.assert_not_called()


@patch("PyQt6.QtWidgets.QGraphicsTextItem.keyPressEvent")
@patch("zeeref.items.ZeeTextItem.exit_edit_mode")
def test_key_press_event_enter(exit_mock, key_press_mock, scene):
    item = ZeeTextItem("foo bar")
    scene.addItem(item)
    scene.edit_item = item
    event = MagicMock()
    event.key.return_value = Qt.Key.Key_Enter
    event.modifiers.return_value = Qt.KeyboardModifier.NoModifier
    item.keyPressEvent(event)
    key_press_mock.assert_called_once_with(event)
    exit_mock.assert_not_called()


@patch("PyQt6.QtWidgets.QGraphicsTextItem.keyPressEvent")
@patch("zeeref.items.ZeeTextItem.exit_edit_mode")
def test_key_press_event_escape(exit_mock, key_press_mock, scene):
    item = ZeeTextItem("foo bar")
    scene.addItem(item)
    scene.edit_item = item
    event = MagicMock()
    event.key.return_value = Qt.Key.Key_Escape
    event.modifiers.return_value = Qt.KeyboardModifier.NoModifier
    item.keyPressEvent(event)
    key_press_mock.assert_not_called()
    exit_mock.assert_called_once_with(commit=False)


def test_item_to_clipboard(qapp):
    clipboard = QtWidgets.QApplication.clipboard()
    item = ZeeTextItem("foo bar")
    item.copy_to_clipboard(clipboard)
    assert clipboard.text() == "foo bar"
