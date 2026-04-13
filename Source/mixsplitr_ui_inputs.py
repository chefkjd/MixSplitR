"""Scroll-safe input widgets that ignore mouse wheel unless focused."""

from PySide6.QtWidgets import (
    QSpinBox, QDoubleSpinBox, QComboBox, QListWidget, QAbstractItemView, QScroller
)
from PySide6.QtCore import Qt, QEvent


# ==========================================
# Scroll-safe widgets: ignore mouse wheel unless the widget is focused.
# Prevents accidental value changes when scrolling the settings page.
# ==========================================

class _NoScrollSpinBox(QSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()

class _NoScrollDoubleSpinBox(QDoubleSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()

class _NoScrollComboBox(QComboBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class _ContainedScrollListWidget(QListWidget):
    """List widget that only captures wheel scroll after explicit list focus."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._wheel_scroll_armed = False
        self.setFocusPolicy(Qt.StrongFocus)
        try:
            QScroller.grabGesture(self.viewport(), QScroller.TouchGesture)
        except Exception:
            pass

    def _resolve_scroll_amount(self, event):
        if event is None:
            return 0
        pixel_delta = event.pixelDelta()
        if not pixel_delta.isNull():
            return -int(pixel_delta.y())
        angle_delta = event.angleDelta().y()
        if angle_delta == 0:
            return 0
        vbar = self.verticalScrollBar()
        step = max(24, vbar.singleStep() * 3) if vbar is not None else 24
        return int(-angle_delta / 120.0 * step)

    def _should_capture_wheel(self):
        return bool(self._wheel_scroll_armed and self.hasFocus())

    def _consume_wheel_scroll(self, event):
        if event is None:
            return False
        vbar = self.verticalScrollBar()
        if vbar is None or vbar.maximum() <= vbar.minimum():
            return False

        scroll_amount = self._resolve_scroll_amount(event)
        if scroll_amount == 0:
            return False

        vbar.setValue(max(vbar.minimum(), min(vbar.maximum(), vbar.value() + scroll_amount)))
        event.accept()
        return True

    def mousePressEvent(self, event):
        self._wheel_scroll_armed = True
        super().mousePressEvent(event)

    def focusOutEvent(self, event):
        self._wheel_scroll_armed = False
        super().focusOutEvent(event)

    def wheelEvent(self, event):
        if not self._should_capture_wheel():
            event.ignore()
            return
        if self._consume_wheel_scroll(event):
            return
        super().wheelEvent(event)
        event.accept()

    def viewportEvent(self, event):
        if event is not None and event.type() == QEvent.Wheel:
            if not self._should_capture_wheel():
                event.ignore()
                return False
            if self._consume_wheel_scroll(event):
                return True
        return super().viewportEvent(event)


class _ChevronComboBox(_NoScrollComboBox):
    """Combo box with a consistently visible chevron affordance."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QColor, QPen
        from PySide6.QtCore import QPointF

        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        arrow_color = QColor("#F0F6FF" if self.isEnabled() else "#808A97")
        pen = QPen(arrow_color)
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)

        indicator_width = 40
        center_x = self.rect().right() - (indicator_width // 2) - 4
        center_y = self.rect().center().y() + 1
        chevron_half_width = 5
        chevron_drop = 4

        painter.drawLine(
            QPointF(center_x - chevron_half_width, center_y - chevron_drop),
            QPointF(center_x, center_y),
        )
        painter.drawLine(
            QPointF(center_x, center_y),
            QPointF(center_x + chevron_half_width, center_y - chevron_drop),
        )
