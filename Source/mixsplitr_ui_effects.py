"""Visual effects and overlay components for the UI."""

import sys
import math

from PySide6.QtWidgets import (
    QPushButton,
    QScrollBar,
    QGraphicsEffect,
    QScroller,
    QScrollArea,
    QStyle,
    QStyleOptionButton,
    QWidget,
)
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QLinearGradient, QPixmap
from PySide6.QtCore import Qt, QTimer, QVariantAnimation, QEasingCurve, QEvent, QPointF, QRect, QRectF


class RippleNavButton(QPushButton):
    """Sidebar nav button with reactive click and selection animations."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._text_tint = 0.0
        self._ripple_progress = 1.0
        self._ripple_center = QPointF(-9999.0, -9999.0)

        self._text_anim = QVariantAnimation(self)
        self._text_anim.setDuration(220)
        self._text_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._text_anim.valueChanged.connect(self._on_text_anim)

        self._ripple_anim = QVariantAnimation(self)
        self._ripple_anim.setDuration(360)
        self._ripple_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._ripple_anim.valueChanged.connect(self._on_ripple_anim)
        self._ripple_anim.finished.connect(self._on_ripple_finished)

    def _on_text_anim(self, value):
        try:
            self._text_tint = max(0.0, min(1.0, float(value)))
        except Exception:
            self._text_tint = 0.0
        self.update()

    def _on_ripple_anim(self, value):
        try:
            self._ripple_progress = max(0.0, min(1.0, float(value)))
        except Exception:
            self._ripple_progress = 1.0
        self.update()

    def _on_ripple_finished(self):
        self._ripple_progress = 1.0
        self.update()

    def set_selected_animated(self, selected, animated=True):
        target = 1.0 if bool(selected) else 0.0
        if not animated:
            self._text_tint = target
            self.update()
            return
        self._text_anim.stop()
        self._text_anim.setStartValue(float(self._text_tint))
        self._text_anim.setEndValue(float(target))
        self._text_anim.start()

    def mousePressEvent(self, event):
        try:
            pos = event.position() if hasattr(event, "position") else event.pos()
            self._ripple_center = QPointF(pos)
        except Exception:
            self._ripple_center = QPointF(float(self.width()) * 0.5, float(self.height()) * 0.5)
        self._ripple_anim.stop()
        self._ripple_anim.setStartValue(0.0)
        self._ripple_anim.setEndValue(1.0)
        self._ripple_anim.start()
        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        clip_rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip_path = QPainterPath()
        clip_path.addRoundedRect(clip_rect, 8.0, 8.0)
        painter.setClipPath(clip_path)

        selection_strength = float(max(0.0, min(1.0, self._text_tint)))
        if selection_strength > 0.01:
            selection_tint = QColor("#4D8DFF")
            selection_tint.setAlpha(int(round(20.0 * selection_strength)))
            painter.fillPath(clip_path, selection_tint)

            bar_height = max(14.0, (float(self.height()) - 14.0) * selection_strength)
            bar_rect = QRectF(6.0, (float(self.height()) - bar_height) * 0.5, 3.0, bar_height)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(77, 141, 255, int(round(220.0 * selection_strength))))
            painter.drawRoundedRect(bar_rect, 1.5, 1.5)

        if self._ripple_progress < 0.999:
            max_radius = math.hypot(float(self.width()), float(self.height())) * 0.95
            radius = max_radius * float(self._ripple_progress)
            alpha = int(round(88.0 * (1.0 - float(self._ripple_progress))))
            if alpha > 0:
                ripple_color = QColor("#D9EEDF" if self.isChecked() else "#F0ECE5")
                ripple_color.setAlpha(alpha)
                painter.setPen(Qt.NoPen)
                painter.setBrush(ripple_color)
                painter.drawEllipse(self._ripple_center, radius, radius)


class SlideFadeRevealEffect(QGraphicsEffect):
    """Draw-time slide/fade effect that avoids fighting layout geometry."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._opacity_value = 1.0
        self._offset_y = 0.0

    def set_opacity_value(self, value):
        try:
            opacity = float(value)
        except Exception:
            opacity = 1.0
        opacity = max(0.0, min(1.0, opacity))
        if abs(opacity - self._opacity_value) < 0.001:
            return
        self._opacity_value = opacity
        self.update()

    def set_offset_y(self, value):
        try:
            offset = float(value)
        except Exception:
            offset = 0.0
        if abs(offset - self._offset_y) < 0.01:
            return
        self._offset_y = offset
        self.updateBoundingRect()
        self.update()

    def boundingRectFor(self, rect):
        if rect.isNull():
            return rect
        extra_bottom = max(0.0, float(self._offset_y))
        extra_top = max(0.0, -float(self._offset_y))
        return rect.adjusted(0.0, -extra_top, 0.0, extra_bottom)

    def draw(self, painter):
        if painter is None:
            return
        source, offset = self.sourcePixmap(Qt.LogicalCoordinates)
        if source.isNull():
            return
        painter.save()
        painter.setOpacity(self._opacity_value)
        painter.translate(0.0, self._offset_y)
        painter.drawPixmap(offset, source)
        painter.restore()

# ==========================================
# 1. DRAG & DROP AREA COMPONENT
# ==========================================
class FloatingOverlayScrollBar(QScrollBar):
    """Plain scrollbar with a visible track and restrained hover state."""

    def __init__(self, orientation, parent=None, always_visible=False):
        super().__init__(orientation, parent)
        self._hovering_thumb = False
        self._visual_strength = 1.0
        self._always_visible = True
        self._dragging = False
        self.setAttribute(Qt.WA_Hover, True)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WA_NoSystemBackground, False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setMouseTracking(True)
        self.setStyleSheet(
            "QScrollBar { background: transparent; border: none; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,"
            "QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {"
            "background: transparent; border: none; width: 0px; height: 0px; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,"
            "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {"
            "background: transparent; }"
        )
        if orientation == Qt.Vertical:
            self.setFixedWidth(12)
        else:
            self.setFixedHeight(12)

        self._fade_timer = QTimer(self)
        self._fade_timer.setSingleShot(True)
        self._fade_timer.setInterval(600)
        self._fade_timer.timeout.connect(self._start_fade_out)

        self._fade_anim = QVariantAnimation(self)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_anim.setDuration(170)
        self._fade_anim.valueChanged.connect(self._on_fade_value_changed)

        self.sliderPressed.connect(self._on_slider_pressed)
        self.sliderReleased.connect(self._on_slider_released)
        self.valueChanged.connect(lambda _v: self.show_temporarily())
        self.sliderMoved.connect(lambda _v: self.show_temporarily())
        self.rangeChanged.connect(self._on_range_changed)

    def _on_slider_pressed(self):
        self._dragging = True
        self.show_temporarily()

    def _on_slider_released(self):
        self._dragging = False
        self.show_temporarily()

    def _on_fade_value_changed(self, value):
        try:
            self._visual_strength = max(0.0, min(1.0, float(value)))
        except Exception:
            self._visual_strength = 0.0
        self.update()

    def _animate_strength_to(self, target, duration_ms=170):
        target = max(0.0, min(1.0, float(target)))
        if abs(self._visual_strength - target) < 0.01:
            self._visual_strength = target
            self.update()
            return
        self._fade_anim.stop()
        self._fade_anim.setDuration(max(40, int(duration_ms)))
        self._fade_anim.setStartValue(float(self._visual_strength))
        self._fade_anim.setEndValue(target)
        self._fade_anim.start()

    def _on_range_changed(self, _minimum, _maximum):
        if int(self.maximum()) <= int(self.minimum()):
            self._fade_timer.stop()
            self._animate_strength_to(0.0, 100)
            return
        self.show_temporarily()

    def _start_fade_out(self):
        return

    def show_temporarily(self):
        self._visual_strength = 1.0
        self.update()

    def _thumb_rect(self):
        minimum = int(self.minimum())
        maximum = int(self.maximum())
        value = int(self.value())
        page = max(1, int(self.pageStep()))

        if self.orientation() == Qt.Vertical:
            track = self.rect().adjusted(3, 2, -3, -2)
            track_len = max(0, track.height())
        else:
            track = self.rect().adjusted(2, 3, -2, -3)
            track_len = max(0, track.width())

        if track_len <= 0:
            return QRect()

        if maximum <= minimum:
            thumb_len = track_len
            offset = 0
        else:
            data_span = (maximum - minimum) + page
            ratio = float(page) / float(max(1, data_span))
            thumb_len = int(round(track_len * ratio))
            thumb_len = max(28, min(track_len, thumb_len))
            travel = max(0, track_len - thumb_len)
            pos_ratio = float(value - minimum) / float(maximum - minimum)
            offset = int(round(travel * max(0.0, min(1.0, pos_ratio))))

        if self.orientation() == Qt.Vertical:
            return QRect(track.left(), track.top() + offset, track.width(), thumb_len)
        return QRect(track.left() + offset, track.top(), thumb_len, track.height())

    def _update_hover_state(self, pos):
        hovering = self._thumb_rect().contains(pos)
        if hovering != self._hovering_thumb:
            self._hovering_thumb = hovering
            if hovering:
                self.show_temporarily()
            self.update()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.show_temporarily()
        self._update_hover_state(self.mapFromGlobal(self.cursor().pos()))

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._hovering_thumb:
            self._hovering_thumb = False
        if not self._dragging:
            self._fade_timer.start()
        self.update()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self._update_hover_state(event.position().toPoint())

    def _surface_color_for_context(self):
        explicit = self.property("scrollbarSurfaceColor")
        if explicit is None and self.parentWidget() is not None:
            explicit = self.parentWidget().property("scrollbarSurfaceColor")
        if explicit is not None:
            explicit_color = QColor(str(explicit))
            if explicit_color.isValid():
                explicit_color.setAlpha(255)
                return explicit_color

        node = self.parentWidget()
        while node is not None:
            if str(node.objectName() or "") == "TrackEditorWorkspaceGroupCell":
                return QColor("#22262B")
            if bool(node.property("is_card")):
                return QColor("#272B30")
            try:
                role_color = node.palette().color(node.backgroundRole())
                if role_color.isValid() and role_color.alpha() > 0:
                    role_color = QColor(role_color)
                    role_color.setAlpha(255)
                    # Ignore very bright platform defaults; keep dark app fallback.
                    if role_color.lightness() < 210:
                        return role_color
            except Exception:
                pass
            node = node.parentWidget()

        return QColor("#1D2024")

    def paintEvent(self, event):
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setClipping(False)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        track_color = self._surface_color_for_context()
        painter.fillRect(self.rect(), track_color)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        if self._visual_strength <= 0.01:
            return

        slider = self._thumb_rect()
        if not slider.isValid() or slider.width() <= 0 or slider.height() <= 0:
            return

        thumb_rect = QRectF(slider.adjusted(0, 0, 0, 0))
        if thumb_rect.width() <= 0.0 or thumb_rect.height() <= 0.0:
            return

        base_alpha = 220 if (self._hovering_thumb or self._dragging) else 190
        alpha = int(max(0, min(255, round(base_alpha * self._visual_strength))))
        color = QColor(121, 131, 144, alpha)
        painter.setPen(QPen(QColor(37, 41, 47), 1))
        painter.setBrush(color)
        radius = 4.0
        painter.drawRoundedRect(thumb_rect, radius, radius)


class TopFadeMaskEffect(QGraphicsEffect):
    """Apply a top transparency mask so scrolling content fades into the grab strip."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._clear_height = 0.0
        self._fade_height = 0.0

    def draw(self, painter):
        if painter is None:
            return
        source, offset = self.sourcePixmap(Qt.LogicalCoordinates)
        if source.isNull():
            return
        if self._clear_height <= 0.0 and self._fade_height <= 0.0:
            painter.drawPixmap(offset, source)
            return

        masked = QPixmap(source.size())
        masked.fill(Qt.transparent)

        mask_painter = QPainter(masked)
        mask_painter.setRenderHint(QPainter.Antialiasing, False)
        mask_painter.drawPixmap(0, 0, source)
        mask_painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)

        h = max(1.0, float(masked.height()))
        clear_h = max(0.0, min(h, float(self._clear_height)))
        fade_h = max(0.0, min(h - clear_h, float(self._fade_height)))
        y0 = 0.0
        y1 = clear_h / h
        y2 = (clear_h + fade_h) / h if fade_h > 0.0 else y1
        grad = QLinearGradient(0.0, 0.0, 0.0, h)
        grad.setColorAt(y0, QColor(255, 255, 255, 0))
        grad.setColorAt(y1, QColor(255, 255, 255, 0))
        grad.setColorAt(y2, QColor(255, 255, 255, 255))
        grad.setColorAt(1.0, QColor(255, 255, 255, 255))
        mask_painter.fillRect(masked.rect(), grad)
        mask_painter.end()

        painter.drawPixmap(offset, masked)


class SmoothScrollArea(QScrollArea):
    """Smooth scroll area with codex-like overlay scrollbars."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollBar(FloatingOverlayScrollBar(Qt.Vertical, self))
        self.setHorizontalScrollBar(FloatingOverlayScrollBar(Qt.Horizontal, self))
        try:
            QScroller.grabGesture(self.viewport(), QScroller.TouchGesture)
        except Exception:
            pass
        # QScrollArea does not expose setViewportUpdateMode (QGraphicsView API).
        # Keep viewport lightweight and transparent for smoother scrolling.
        self.viewport().setAttribute(Qt.WA_OpaquePaintEvent, False)

        self._scroll_idle_timer = QTimer(self)
        self._scroll_idle_timer.setSingleShot(True)
        self._scroll_idle_timer.setInterval(140)
        self._scroll_idle_timer.timeout.connect(self._on_scroll_idle)

        self.verticalScrollBar().valueChanged.connect(lambda _v: self._on_scroll_activity())
        self.horizontalScrollBar().valueChanged.connect(lambda _v: self._on_scroll_activity())
        self.verticalScrollBar().setSingleStep(40)

        # Smooth mouse-wheel scrolling: animate toward a target position at ~60 fps.
        self._scroll_target_v = 0
        self._smooth_timer = QTimer(self)
        self._smooth_timer.setInterval(16)
        self._smooth_timer.timeout.connect(self._smooth_scroll_step)

    def _resolve_main_window(self):
        node = self.parentWidget()
        while node is not None:
            if hasattr(node, "_raise_titlebar_overlays_deferred"):
                return node
            node = node.parentWidget()
        return None

    def _stabilize_macos_top_chrome_during_scroll(self):
        if sys.platform != "darwin":
            return
        # With viewport top-inset occlusion enabled, we no longer need forced
        # high-frequency top-band repaints; keep only overlay z-order sync.

        main_window = self._resolve_main_window()
        if main_window is not None:
            try:
                main_window._raise_titlebar_overlays_deferred()
            except Exception:
                pass
            try:
                fade = getattr(main_window, "titlebar_fade_overlay", None)
                if fade is not None and fade.isVisible():
                    fade.update()
            except Exception:
                pass
            try:
                strip = getattr(main_window, "titlebar_content_spacer", None)
                if strip is not None and strip.isVisible():
                    strip.update()
            except Exception:
                pass

    def _set_page_scroll_active(self, active):
        content = self.widget()
        if content is not None and hasattr(content, "set_scroll_active"):
            try:
                content.set_scroll_active(bool(active))
            except Exception:
                pass

    def _on_scroll_activity(self):
        for bar in (self.verticalScrollBar(), self.horizontalScrollBar()):
            if hasattr(bar, "show_temporarily"):
                try:
                    bar.show_temporarily()
                except Exception:
                    pass
        self._stabilize_macos_top_chrome_during_scroll()
        self._set_page_scroll_active(True)
        self._scroll_idle_timer.start()

    def _on_scroll_idle(self):
        self._set_page_scroll_active(False)

    def setWidget(self, widget):
        super().setWidget(widget)
        self._set_page_scroll_active(False)

    def wheelEvent(self, event):
        self._on_scroll_activity()
        # Trackpads and high-resolution scroll devices send pixel-level deltas and
        # already have OS-level momentum/smoothing — let Qt handle those natively.
        if not event.pixelDelta().isNull():
            super().wheelEvent(event)
            return
        # Standard mouse wheel: animate toward a target position instead of jumping.
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        vbar = self.verticalScrollBar()
        if not self._smooth_timer.isActive():
            self._scroll_target_v = vbar.value()
        pixels_per_notch = vbar.singleStep() * 3
        scroll_amount = int(-delta / 120.0 * pixels_per_notch)
        self._scroll_target_v = max(
            vbar.minimum(), min(vbar.maximum(), self._scroll_target_v + scroll_amount)
        )
        if not self._smooth_timer.isActive():
            self._smooth_timer.start()
        event.accept()

    def _smooth_scroll_step(self):
        vbar = self.verticalScrollBar()
        current = vbar.value()
        target = self._scroll_target_v
        diff = target - current
        if abs(diff) < 1:
            vbar.setValue(target)
            self._smooth_timer.stop()
            return
        vbar.setValue(int(round(current + diff * 0.18)))

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        if sys.platform != "darwin":
            return
        if int(dx) == 0 and int(dy) == 0:
            return
        self._stabilize_macos_top_chrome_during_scroll()


class PageRevealOverlay(QWidget):
    """Subtle page-transition fade overlay using opacity only."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._origin = QPointF(0.0, 0.0)
        self._progress = 1.0
        self._color = QColor("#1D2024")
        self._max_alpha = 42
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.hide()

        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_progress_changed)
        self._anim.finished.connect(self._finish)

    def _on_progress_changed(self, value):
        try:
            self._progress = max(0.0, min(1.0, float(value)))
        except Exception:
            self._progress = 1.0
        self.update()

    def _finish(self):
        self._progress = 1.0
        self.hide()

    def prime(self, origin, color=None):
        try:
            self._origin = QPointF(origin)
        except Exception:
            self._origin = QPointF(0.0, 0.0)
        if color is not None:
            new_color = QColor(color)
            if new_color.isValid():
                self._color = new_color
        self._anim.stop()
        self._progress = 0.0
        self.show()
        self.raise_()
        self.update()

    def play(self):
        self._anim.stop()
        self._anim.setDuration(145)
        self._anim.setStartValue(float(self._progress))
        self._anim.setEndValue(1.0)
        self._anim.start()

    def paintEvent(self, event):
        _ = event
        if self._progress >= 0.999:
            return
        alpha_ratio = max(0.0, min(1.0, 1.0 - float(self._progress)))
        alpha = int(round(float(self._max_alpha) * (alpha_ratio ** 1.1)))
        if alpha <= 0:
            return
        painter = QPainter(self)
        painter.setPen(Qt.NoPen)
        color = QColor(self._color)
        color.setAlpha(alpha)
        painter.fillRect(self.rect(), color)


class TitlebarOverlay(QWidget):
    """Top overlay bar that masks scrolling content and adds a bottom shadow."""

    def __init__(self, parent=None, base_color="#121417"):
        super().__init__(parent)
        self._base_color = QColor(base_color)
        self._top_corner_radius = 14.0
        self._bottom_shadow_alpha = 0
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        if sys.platform == "darwin":
            # Give the drag strip its own NSView/CALayer so it can be
            # explicitly ordered above native scrolling content.
            self.setAttribute(Qt.WA_NativeWindow, True)
            self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_base_color(self, color_value):
        color = QColor(color_value)
        if not color.isValid():
            return
        if color == self._base_color:
            return
        self._base_color = color
        self.update()

    def set_top_corner_radius(self, radius):
        try:
            value = float(radius)
        except Exception:
            return
        value = max(0.0, value)
        if abs(value - self._top_corner_radius) < 0.01:
            return
        self._top_corner_radius = value
        self.update()

    def paintEvent(self, event):
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect()
        if not rect.isValid():
            return
        if self._base_color.alpha() <= 0:
            # Transparent drag-strip mode must actively clear backing pixels;
            # SourceOver with alpha=0 can leave stale content while scrolling.
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.fillRect(rect, Qt.transparent)
            return

        radius = max(0.0, min(self._top_corner_radius, rect.width() * 0.5, rect.height() * 0.5))
        if radius <= 0.0:
            painter.fillRect(rect, self._base_color)
        else:
            # Keep only top corners rounded so the strip stays attached to
            # content below while matching the card-like surface.
            rounded_path = QPainterPath()
            # Use winding fill so the two added shapes are unioned, not XORed.
            rounded_path.setFillRule(Qt.WindingFill)
            rounded_path.addRoundedRect(QRectF(rect), radius, radius)
            rounded_path.addRect(QRectF(0.0, radius, float(rect.width()), max(0.0, float(rect.height()) - radius)))
            painter.fillPath(rounded_path, self._base_color)

        # Keep the strip edge clean and line-free.
        shadow_h = min(18, max(8, int(rect.height() * 0.5)))
        if shadow_h > 0 and self._bottom_shadow_alpha > 0:
            grad = QLinearGradient(0.0, float(rect.height() - shadow_h), 0.0, float(rect.height()))
            grad.setColorAt(0.0, QColor(0, 0, 0, int(self._bottom_shadow_alpha)))
            grad.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillRect(
                QRect(0, rect.height() - shadow_h, rect.width(), shadow_h),
                grad,
            )


class TitlebarFadeOverlay(QWidget):
    """Top content fade mask so scrolling cards dissolve into the dark background."""

    def __init__(self, parent=None, base_color="#121417"):
        super().__init__(parent)
        self._base_color = QColor(base_color)
        self._top_clear_fraction = 0.0
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        if sys.platform == "darwin":
            # WA_NativeWindow gives this widget its own NSView/CALayer so it
            # is a sibling of GlassPage's native layer rather than rendering
            # into the parent backing store that sits *below* GlassPage in
            # Core Animation.  After raise_() it is above GlassPage and can
            # actually composite over card content and per-card vibrancy views.
            # WA_TranslucentBackground sets isOpaque=NO on that CALayer so the
            # gradient alpha values produce genuine per-pixel transparency.
            self.setAttribute(Qt.WA_NativeWindow, True)
            self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_base_color(self, color_value):
        color = QColor(color_value)
        if not color.isValid():
            return
        if color == self._base_color:
            return
        self._base_color = color
        self.update()

    def set_top_clear_fraction(self, value):
        try:
            fraction = float(value)
        except Exception:
            return
        fraction = max(0.0, min(0.95, fraction))
        if abs(fraction - self._top_clear_fraction) < 0.001:
            return
        self._top_clear_fraction = fraction
        self.update()

    def paintEvent(self, event):
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setClipping(False)
        rect = self.rect()
        if not rect.isValid():
            return
        # Clear the full backing store every paint. Transparent gradient stops
        # plus SourceOver can otherwise leave stale pixels during rapid scroll.
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(rect, Qt.transparent)
        if self._base_color.alpha() <= 0:
            return
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        # Two-phase fade: hold fully opaque for the top 25% of the zone (solid
        # extension of the bar — no card can show through) then ease smoothly
        # to transparent over the remaining 75%.  This prevents card top-edges
        # from bleeding through early in the transition and creating a line.
        grad = QLinearGradient(0.0, 0.0, 0.0, float(rect.height() + 2))
        r = self._base_color.red()
        g = self._base_color.green()
        b = self._base_color.blue()
        max_alpha = max(0, min(255, int(self._base_color.alpha())))
        if self._top_clear_fraction > 0.0:
            # Solid black through the drag-strip zone, then ease to transparent.
            # The overlay spans strip + fade height; _top_clear_fraction marks
            # where the strip ends and the fade begins.
            solid_end = max(0.05, min(0.95, float(self._top_clear_fraction)))
            grad.setColorAt(0.0, QColor(r, g, b, max_alpha))
            grad.setColorAt(solid_end, QColor(r, g, b, max_alpha))
            # Ease out over the remaining space
            steps = 16
            for i in range(steps + 1):
                t = float(i) / float(steps)
                t_global = solid_end + t * (1.0 - solid_end)
                eased = (1.0 - t) ** 2.2
                alpha = int(round(float(max_alpha) * eased))
                grad.setColorAt(min(1.0, t_global), QColor(r, g, b, max(0, min(255, alpha))))
            painter.fillRect(rect, grad)
            return
        hold_fraction = 0.25 if max_alpha >= 255 else 0.0
        steps = 28
        for i in range(steps + 1):
            t = float(i) / float(steps)
            if t <= hold_fraction:
                alpha = max_alpha
            else:
                denom = max(1e-6, (1.0 - hold_fraction))
                t_adj = (t - hold_fraction) / denom
                eased = (1.0 - t_adj) ** 2.2
                alpha = int(round(float(max_alpha) * eased))
            grad.setColorAt(t, QColor(r, g, b, max(0, min(255, alpha))))
        painter.fillRect(rect, grad)
