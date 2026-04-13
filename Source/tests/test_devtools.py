import inspect
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QWidget

from mixsplitr_devtools import (
    DeveloperInspectorController,
    annotate_widget_owner,
    build_inspector_payload,
    format_inspector_location,
    get_widget_effective_source,
    get_widget_source,
    resolve_inspector_widget,
)


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def controller(app):
    dev_controller = DeveloperInspectorController()
    yield dev_controller
    try:
        app.removeEventFilter(dev_controller)
    except Exception:
        pass
    dev_controller.deleteLater()
    app.processEvents()


def _build_owner_annotated_widget(parent):
    card = QWidget(parent)
    helper_label = QLabel("helper-owned", card)
    annotate_widget_owner(card, role="Helper Card", stacklevel=1)
    annotate_widget_owner(helper_label, role="Helper Label", stacklevel=1)
    return card, helper_label


def test_child_added_capture_uses_widget_creation_line(app, controller):
    parent = QWidget()
    expected_line = inspect.currentframe().f_lineno + 1
    label = QLabel("direct child", parent)
    app.processEvents()

    source = get_widget_source(label)

    assert source["file"].endswith("test_devtools.py")
    assert source["module"] == __name__
    assert source["line"] == expected_line


def test_owner_source_overrides_helper_internal_callsite(app, controller):
    parent = QWidget()
    expected_line = inspect.currentframe().f_lineno + 1
    _card, helper_label = _build_owner_annotated_widget(parent)
    app.processEvents()

    source = get_widget_effective_source(helper_label)

    assert source["file"].endswith("test_devtools.py")
    assert source["line"] == expected_line


def test_generated_viewport_resolves_to_annotated_parent(app, controller):
    parent = QWidget()
    expected_line = inspect.currentframe().f_lineno + 1
    list_widget = QListWidget(parent)
    app.processEvents()

    viewport = list_widget.viewport()
    resolved, fallback = resolve_inspector_widget(viewport)
    payload = build_inspector_payload(resolved, raw_widget=viewport)

    assert resolved is list_widget
    assert fallback is True
    assert payload["fallback_target"] is True
    assert payload["line_number"] == str(expected_line)
    assert "[ancestor fallback]" in payload["element_label"]


def test_formatting_includes_location_and_fallback_details(app, controller):
    parent = QWidget()
    list_widget = QListWidget(parent)
    app.processEvents()

    viewport = list_widget.viewport()
    resolved, _fallback = resolve_inspector_widget(viewport)
    payload = build_inspector_payload(resolved, raw_widget=viewport, locked=True)
    text = format_inspector_location(payload)

    assert "Status: Locked" in text
    assert "Location:" in text
    assert "Resolution: Ancestor fallback" in text
