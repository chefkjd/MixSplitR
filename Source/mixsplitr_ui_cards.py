"""Card frame and glass page widgets for the UI."""

import sys
import math

from PySide6.QtWidgets import QFrame, QWidget, QLabel, QCheckBox, QRadioButton, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QStyle, QStyleOptionButton
from PySide6.QtGui import QColor, QPainter, QPainterPath, QLinearGradient, QPixmap, QRegion, QIcon
from PySide6.QtCore import Qt, QTimer, QEvent, QPoint, QRect, QRectF

# Import objc-related globals if available (defined in main_ui.py)
try:
    from main_ui import (
        ENABLE_NATIVE_VIBRANCY,
        MACOS_VIBRANCY_AVAILABLE,
        objc,
        NSVisualEffectView,
        NSVisualEffectMaterialSidebar,
        NSVisualEffectBlendingModeBehindWindow,
        NSVisualEffectStateActive,
        NSViewWidthSizable,
        NSViewHeightSizable,
        NSWindowBelow,
    )
except ImportError:
    # Fallback if imports fail - these are macOS-specific
    ENABLE_NATIVE_VIBRANCY = False
    MACOS_VIBRANCY_AVAILABLE = False
    objc = None
    NSVisualEffectView = None
    NSVisualEffectMaterialSidebar = None
    NSVisualEffectBlendingModeBehindWindow = None
    NSVisualEffectStateActive = None
    NSViewWidthSizable = None
    NSViewHeightSizable = None
    NSWindowBelow = None

# Placeholder for DragDropArea - will be imported from main_ui
DragDropArea = None


class GlossyCardFrame(QFrame):
    """Flat card renderer retained for compatibility."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        if sys.platform == "darwin":
            # Ensure each card has a native NSView so per-card vibrancy can be inserted behind it.
            self.setAttribute(Qt.WA_NativeWindow, True)
        self.setStyleSheet("background: transparent; border: none;")
        self._mac_blur_enabled = False
        self._ns_vibrancy_view = None
        self._ns_native_view = None
        self._card_matte_cache = None
        self._card_matte_cache_w = 0
        self._card_matte_cache_h = 0
        self._card_matte_cache_dpr = 0.0
        self._card_matte_cache_dirty = True
        self._pending_mac_blur_sync = False
        self._hovered = False
        self.setAttribute(Qt.WA_Hover, True)
        self.setMouseTracking(True)
        # Keep per-card native blur disabled by default; rounded Qt-painted card
        # transparency remains active via GlassPage punch-through.
        self.setProperty("disableMacBlur", True)

    def _is_in_active_scroll_context(self):
        parent = self.parentWidget()
        while parent is not None:
            if bool(getattr(parent, "_scroll_active", False)):
                return True
            parent = parent.parentWidget()
        return False

    def _schedule_mac_blur_sync_after_scroll(self):
        if self._pending_mac_blur_sync:
            return
        self._pending_mac_blur_sync = True

        def _run():
            self._pending_mac_blur_sync = False
            if self._is_in_active_scroll_context():
                self._schedule_mac_blur_sync_after_scroll()
                return
            self._sync_mac_blur_geometry()

        # Coalesce bursts of move events while kinetic scroll is active.
        QTimer.singleShot(50, _run)

    def _teardown_mac_blur(self):
        if self._ns_vibrancy_view is not None:
            try:
                self._ns_vibrancy_view.removeFromSuperview()
            except Exception:
                pass
        self._mac_blur_enabled = False
        self._ns_vibrancy_view = None
        self._ns_native_view = None

    def _setup_mac_blur_if_available(self):
        if not ENABLE_NATIVE_VIBRANCY:
            self._teardown_mac_blur()
            return False
        if sys.platform != "darwin":
            return False
        if bool(self.property("disableMacBlur")):
            self._teardown_mac_blur()
            return False
        if not MACOS_VIBRANCY_AVAILABLE or objc is None or NSVisualEffectView is None:
            return False
        if self._ns_vibrancy_view is not None:
            self._sync_mac_blur_geometry()
            return True
        try:
            from ctypes import c_void_p
            _ = int(self.winId())
            ns_view = objc.objc_object(c_void_p=int(self.winId()))
            ns_parent = ns_view.superview()
            if ns_parent is None:
                # Retry after ensuring native handle materialized.
                _ = int(self.winId())
                ns_view = objc.objc_object(c_void_p=int(self.winId()))
                ns_parent = ns_view.superview()
            if ns_parent is None:
                return False

            effect = NSVisualEffectView.alloc().initWithFrame_(ns_view.frame())
            effect.setAutoresizingMask_(int(NSViewWidthSizable | NSViewHeightSizable))
            effect.setMaterial_(NSVisualEffectMaterialSidebar)
            effect.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
            effect.setState_(NSVisualEffectStateActive)
            ns_parent.addSubview_positioned_relativeTo_(effect, int(NSWindowBelow), ns_view)
            self._ns_vibrancy_view = effect
            self._ns_native_view = ns_view
            self._mac_blur_enabled = True
            self._sync_mac_blur_geometry()
            return True
        except Exception:
            self._mac_blur_enabled = False
            self._ns_vibrancy_view = None
            self._ns_native_view = None
            return False

    def _sync_mac_blur_geometry(self):
        if not self._mac_blur_enabled:
            return
        if self._ns_vibrancy_view is None:
            return
        try:
            from ctypes import c_void_p
            ns_view = self._ns_native_view
            if ns_view is None:
                ns_view = objc.objc_object(c_void_p=int(self.winId()))
                self._ns_native_view = ns_view
            self._ns_vibrancy_view.setFrame_(ns_view.frame())
            self._ns_vibrancy_view.setHidden_(not self.isVisible())
        except Exception:
            self._mac_blur_enabled = False
            self._ns_vibrancy_view = None
            self._ns_native_view = None

    def showEvent(self, event):
        super().showEvent(event)
        if bool(self.property("disableMacBlur")):
            return
        # Defer to the next event-loop tick so Qt's layout engine and the native
        # NSView hierarchy have both settled before we read frame geometry.
        # Without this, the vibrancy view is sized/positioned to the card's
        # pre-layout frame, producing "bars" of bleed-through transparency that
        # disappear only after the first repaint triggered by a tab switch.
        QTimer.singleShot(0, self._setup_mac_blur_if_available)
        QTimer.singleShot(0, self._sync_mac_blur_geometry)
        # On the very first show, deeply nested cards (e.g. inside
        # TrackEditorPanel) may not have a fully-parented NSView at
        # tick 0 — _setup_mac_blur_if_available returns False because
        # superview() is still None.  Retry with progressively longer
        # delays to catch the native hierarchy once it settles.  If
        # the vibrancy view was already created above, the retries
        # will early-return via the _ns_vibrancy_view is not None guard.
        if sys.platform == "darwin":
            for delay in (80, 250):
                QTimer.singleShot(delay, self._setup_mac_blur_if_available)
                QTimer.singleShot(delay, self._sync_mac_blur_geometry)

    def moveEvent(self, event):
        super().moveEvent(event)
        if sys.platform == "darwin" and self._is_in_active_scroll_context():
            self._schedule_mac_blur_sync_after_scroll()
            return
        self._sync_mac_blur_geometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._invalidate_card_matte_cache()
        self._sync_mac_blur_geometry()

    def event(self, e):
        t = e.type()
        if t in (
            QEvent.LayoutRequest,
            QEvent.ChildAdded,
            QEvent.ChildRemoved,
            QEvent.FontChange,
            QEvent.StyleChange,
            QEvent.PaletteChange,
            QEvent.EnabledChange,
        ):
            self._invalidate_card_matte_cache()
        return super().event(e)

    def hideEvent(self, event):
        if self._ns_vibrancy_view is not None:
            try:
                self._ns_vibrancy_view.setHidden_(True)
            except Exception:
                pass
        super().hideEvent(event)

    def _set_hovered(self, hovered):
        hovered = bool(hovered)
        if hovered == self._hovered:
            return
        self._hovered = hovered
        self._invalidate_card_matte_cache()
        self.update()

    def enterEvent(self, event):
        self._set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._set_hovered(False)
        super().leaveEvent(event)

    def _invalidate_card_matte_cache(self):
        self._card_matte_cache_dirty = True
        self._card_matte_cache = None

    def _paint_row_surface_capsules(self, painter):
        # Import DragDropArea locally to avoid circular imports
        from main_ui import DragDropArea as _DragDropArea

        row_widgets = self.findChildren(QWidget)
        for child in row_widgets:
            if child is self or not child.isVisibleTo(self):
                continue
            row_rect = None
            if isinstance(child, QLabel):
                if bool(child.property("is_card_title")):
                    continue
                if isinstance(child.parentWidget(), _DragDropArea):
                    continue
                text = child.text().strip()
                if not text:
                    continue
                content_rect = child.contentsRect()
                flags = int(child.alignment())
                if child.wordWrap():
                    flags |= int(Qt.TextWordWrap)
                text_rect_local = child.style().itemTextRect(
                    child.fontMetrics(),
                    content_rect,
                    flags,
                    False,
                    text,
                )
                if text_rect_local.width() <= 0 or text_rect_local.height() <= 0:
                    continue
                top_left = child.mapTo(self, text_rect_local.topLeft())
                colon_extra_right = 5.0 if text.endswith(":") else 0.0
                row_rect = QRectF(
                    float(top_left.x()) - 7.0,
                    float(top_left.y()) - 6.0,
                    float(text_rect_local.width()) + 20.0 + colon_extra_right,
                    float(text_rect_local.height()) + 12.0,
                )
            elif isinstance(child, (QCheckBox, QRadioButton)):
                text = child.text().strip()
                if not text:
                    continue
                option = QStyleOptionButton()
                option.initFrom(child)
                option.text = text
                if isinstance(child, QCheckBox):
                    indicator_rect = child.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, child)
                else:
                    indicator_rect = child.style().subElementRect(QStyle.SE_RadioButtonIndicator, option, child)
                if indicator_rect.width() <= 0 or indicator_rect.height() <= 0:
                    indicator_rect = QRect(0, 0, 16, max(16, child.fontMetrics().height()))
                text_w = child.fontMetrics().horizontalAdvance(text)
                text_h = child.fontMetrics().height()
                left_pad = 8.0
                right_pad = 14.0
                text_gap = 10.0
                top_pad = 6.0
                bottom_pad = 6.0
                cap_h = max(float(indicator_rect.height()), float(text_h)) + top_pad + bottom_pad
                cap_w = float(indicator_rect.width()) + text_gap + float(text_w) + left_pad + right_pad
                cap_x = float(indicator_rect.x()) - left_pad
                cap_y = (float(child.height()) - cap_h) / 2.0
                top_left = child.mapTo(self, QPoint(0, 0))
                row_rect = QRectF(
                    float(top_left.x()) + cap_x,
                    float(top_left.y()) + cap_y,
                    cap_w,
                    cap_h,
                )
            elif isinstance(child, (QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox)):
                control_rect = child.rect()
                if control_rect.width() <= 0 or control_rect.height() <= 0:
                    continue
                top_left = child.mapTo(self, control_rect.topLeft())
                row_rect = QRectF(
                    float(top_left.x()) - 3.0,
                    float(top_left.y()) - 3.0,
                    float(control_rect.width()) + 6.0,
                    float(control_rect.height()) + 6.0,
                )
            else:
                continue
            if row_rect.width() <= 0.0 or row_rect.height() <= 0.0:
                continue
            if child.isEnabled():
                if self._hovered:
                    fill_color = QColor(33, 40, 51, 132)
                    border_color = QColor(214, 228, 247, 62)
                else:
                    fill_color = QColor(29, 35, 44, 118)
                    border_color = QColor(204, 220, 242, 50)
            else:
                fill_color = QColor(22, 26, 33, 92)
                border_color = QColor(180, 194, 214, 36)
            painter.setBrush(fill_color)
            capsule_pen = QPen(border_color)
            capsule_pen.setWidthF(1.0)
            painter.setPen(capsule_pen)
            radius = min(14.0, row_rect.height() * 0.5)
            painter.drawRoundedRect(row_rect, radius, radius)

    def _rebuild_card_matte_cache(self):
        w = self.width()
        h = self.height()
        if w <= 1 or h <= 1:
            self._card_matte_cache = None
            self._card_matte_cache_w = 0
            self._card_matte_cache_h = 0
            self._card_matte_cache_dpr = 0.0
            self._card_matte_cache_dirty = False
            return

        dpr = float(max(1.0, self.devicePixelRatioF()))
        px_w = max(1, int(math.ceil(float(w) * dpr)))
        px_h = max(1, int(math.ceil(float(h) * dpr)))
        cache = QPixmap(px_w, px_h)
        cache.setDevicePixelRatio(dpr)
        cache.fill(Qt.transparent)

        painter = QPainter(cache)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = QRectF(0.0, 0.0, float(w), float(h))
        rect.adjust(0.5, 0.5, -0.5, -0.5)
        radius = 10.0
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        painter.setClipPath(path)

        fill_color = QColor("#2B3036" if self._hovered else "#272B30")
        painter.fillPath(path, fill_color)

        self._paint_row_surface_capsules(painter)

        border_pen = QPen(QColor(0, 0, 0, 0))
        border_pen.setWidthF(1.0)
        painter.setPen(border_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.end()

        self._card_matte_cache = cache
        self._card_matte_cache_w = w
        self._card_matte_cache_h = h
        self._card_matte_cache_dpr = dpr
        self._card_matte_cache_dirty = False

    def _ensure_card_matte_cache(self):
        w = self.width()
        h = self.height()
        dpr = float(max(1.0, self.devicePixelRatioF()))
        if (
            self._card_matte_cache_dirty
            or self._card_matte_cache is None
            or self._card_matte_cache_w != w
            or self._card_matte_cache_h != h
            or abs(self._card_matte_cache_dpr - dpr) > 0.001
        ):
            self._rebuild_card_matte_cache()

    def paintEvent(self, event):
        _ = event
        self._ensure_card_matte_cache()
        if self._card_matte_cache is None:
            return
        painter = QPainter(self)
        # During high-velocity scroll, partial event clips can leave stale
        # edge fragments. Always redraw the full card backing deterministically.
        painter.setClipping(False)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        painter.drawPixmap(0, 0, self._card_matte_cache)


class GlassPage(QWidget):
    """Page widget that paints a plain solid background behind content."""

    _bg_grad_top = QColor("#1D2024")
    _bg_grad_mid = QColor("#1D2024")
    _bg_grad_bot = QColor("#1D2024")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        # Keep card cutouts opt-in. Parent masks can clip child painting on
        # some compositor/driver combinations, making cards appear missing.
        self._enable_card_cutout_mask = False
        # Keep page fully opaque behind cards; no clear-cutout punch-through.
        self._enable_card_punchthrough = False
        # On macOS, CompositionMode_Clear while the viewport is rapidly moving
        # can produce shimmer/jitter artifacts. Optionally suspend punch-through
        # during active scrolling and restore it as soon as scrolling goes idle.
        self._suspend_punchthrough_during_scroll = False
        self._scroll_active = False
        if sys.platform == "darwin":
            # Own NSView so GlossyCardFrame NSViews become children of ours.
            # The whole subtree moves as a unit during scroll — no per-card
            # moveEvent / _sync_mac_blur_geometry() call on the hot path.
            self.setAttribute(Qt.WA_NativeWindow, True)
        self._card_cache = None   # GlossyCardFrame children list, rebuilt on layout change
        self._observed_cards = []
        self._grad_pixmap = None  # gradient pixmap (full widget size), rebuilt on resize
        self._shell_path = QPainterPath()
        self._punchthrough_path = QPainterPath()
        self._punchthrough_path_dirty = True
        self._mask_pending = False
        self._top_fade_clear_h = 0.0
        self._top_fade_h = 0.0
        self._top_fade_strength = 1.0
        self._top_fade_page_offset_y = 0.0

    def set_top_fade_zone(self, clear_h, fade_h, page_offset_y=0.0, strength=1.0):
        try:
            clear_v = max(0.0, float(clear_h))
            fade_v = max(0.0, float(fade_h))
            offset_v = float(page_offset_y)
            strength_v = max(1.0, float(strength))
        except Exception:
            return
        changed = (
            abs(clear_v - self._top_fade_clear_h) > 0.01
            or abs(fade_v - self._top_fade_h) > 0.01
            or abs(offset_v - self._top_fade_page_offset_y) > 0.01
            or abs(strength_v - self._top_fade_strength) > 0.01
        )
        if not changed:
            return
        self._top_fade_clear_h = clear_v
        self._top_fade_h = fade_v
        self._top_fade_page_offset_y = offset_v
        self._top_fade_strength = strength_v
        self.update()

    # -----------------------------------------------------------------------

    def _rebuild_grad_pixmap(self):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            self._grad_pixmap = None
            self._shell_path = QPainterPath()
            return
        px = QPixmap(w, h)
        px.fill(QColor("#1D2024"))
        pp = QPainter(px)
        pp.setRenderHint(QPainter.Antialiasing, True)

        # Flat page surface: no rounded "dark card" shell behind content.
        full_rect = QRectF(0.0, 0.0, float(w), float(h))
        shell_path = QPainterPath()
        shell_path.addRect(full_rect)
        self._shell_path = shell_path

        pp.fillRect(QRectF(full_rect), self._bg_grad_top)
        pp.end()
        self._grad_pixmap = px

    def _invalidate_card_geometry_cache(self):
        self._card_cache = None
        self._punchthrough_path_dirty = True

    def _rebuild_card_cache(self):
        my_window = self.window()
        cards = [
            c for c in self.findChildren(GlossyCardFrame)
            if c.window() is my_window
        ]
        old_cards = set(self._observed_cards)
        new_cards = set(cards)
        for card in old_cards - new_cards:
            try:
                card.removeEventFilter(self)
            except Exception:
                pass
        for card in new_cards - old_cards:
            try:
                card.installEventFilter(self)
            except Exception:
                pass
        self._observed_cards = cards
        self._card_cache = cards
        self._punchthrough_path_dirty = True

    def _rebuild_punchthrough_path(self):
        if self._card_cache is None:
            self._rebuild_card_cache()
        path = QPainterPath()
        for card in self._card_cache:
            if not card.isVisibleTo(self):
                continue
            pos = card.mapTo(self, QPoint(0, 0))
            card_rect = QRectF(
                float(pos.x()),
                float(pos.y()),
                float(card.width()),
                float(card.height()),
            )
            clear_rect = card_rect.adjusted(0.5, 0.5, -0.5, -0.5)
            path.addRoundedRect(clear_rect, 10.0, 10.0)
        self._punchthrough_path = path
        self._punchthrough_path_dirty = False

    def _apply_mask(self):
        """Compute QRegion = widget rect minus card rects, apply as mask.

        Called once after layout settles (deferred via QTimer.singleShot so
        that mapTo() reads final positions).  Never called on the scroll hot
        path — card positions relative to GlassPage don't change during scroll.
        """
        self._mask_pending = False
        if not self._enable_card_cutout_mask:
            self.clearMask()
            # Re-dirty the punchthrough path so the next paint rebuilds it
            # with up-to-date card visibility.  The first paint after
            # showEvent may have built an empty path because cards were not
            # yet visible (especially on macOS where NSViews settle late).
            self._punchthrough_path_dirty = True
            self.update()
            return
        if self._card_cache is None:
            self._rebuild_card_cache()
        region = QRegion(self.rect())
        for card in self._card_cache:
            if not card.isVisibleTo(self):
                continue
            pos = card.mapTo(self, QPoint(0, 0))
            region -= QRegion(QRect(pos.x(), pos.y(), card.width(), card.height()))
        self.setMask(region)
        self.update()

    def _schedule_mask(self):
        if not self._mask_pending:
            self._mask_pending = True
            QTimer.singleShot(0, self._apply_mask)

    # -----------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        self._invalidate_card_geometry_cache()
        self._schedule_mask()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._grad_pixmap = None   # rebuild on next paint
        self._punchthrough_path_dirty = True
        self._schedule_mask()

    def event(self, e):
        t = e.type()
        if t == QEvent.LayoutRequest:
            self._invalidate_card_geometry_cache()
            self._schedule_mask()
        elif t in (QEvent.ChildAdded, QEvent.ChildRemoved):
            self._invalidate_card_geometry_cache()
            self._schedule_mask()
        return super().event(e)

    def eventFilter(self, watched, e):
        if watched in self._observed_cards:
            t = e.type()
            if t in (QEvent.Move, QEvent.Resize, QEvent.Show, QEvent.Hide, QEvent.Destroy):
                if t == QEvent.Move and self._scroll_active:
                    return super().eventFilter(watched, e)
                self._punchthrough_path_dirty = True
                # Show/Hide changes which cards are visible in the
                # punchthrough path.  Without scheduling a repaint here the
                # GlassPage can stay with a stale (empty) path — rendering
                # as a solid dark overlay — until something else (scroll,
                # resize) forces a paint.  This is the primary cause of the
                # "blank page on macOS tab switch" issue because native
                # NSViews settle asynchronously after Qt's showEvent.
                if t in (QEvent.Show, QEvent.Hide, QEvent.Resize):
                    self.update()
        return super().eventFilter(watched, e)

    def set_scroll_active(self, active):
        active = bool(active)
        if active == self._scroll_active:
            return
        self._scroll_active = active
        if self._enable_card_punchthrough and self._suspend_punchthrough_during_scroll:
            self.update()

    def paintEvent(self, event):
        # Blit gradient pixmap with default SourceOver — no exotic composition
        # modes.  Qt's scroll-area can therefore use its bitblt optimisation
        # (shift existing content, repaint only the newly-exposed strip).
        if self._grad_pixmap is None or self._grad_pixmap.size() != self.size():
            self._rebuild_grad_pixmap()
        if self._grad_pixmap is None:
            return
        painter = QPainter(self)
        # Top-fade masking depends on whole-surface compositing. During scroll,
        # partial repaint clips can leave stale shell pixels that miss the fade.
        # Redraw full surface each paint to keep the outer shell fade coherent.
        _ = event
        painter.setClipping(False)
        painter.drawPixmap(0, 0, self._grad_pixmap)
        punchthrough_enabled = self._enable_card_punchthrough and not (
            self._suspend_punchthrough_during_scroll and self._scroll_active
        )
        if punchthrough_enabled:
            if self._card_cache is None or self._punchthrough_path_dirty:
                self._rebuild_punchthrough_path()
            if not self._punchthrough_path.isEmpty():
                painter.save()
                painter.setRenderHint(QPainter.Antialiasing, True)
                if not self._shell_path.isEmpty():
                    painter.setClipPath(self._shell_path, Qt.IntersectClip)
                painter.setCompositionMode(QPainter.CompositionMode_Clear)
                painter.fillPath(self._punchthrough_path, Qt.transparent)
                painter.restore()
        # On macOS the top-fade is handled by a fixed black TitlebarFadeOverlay
        # sitting above the scroll area — no scroll-dependent rendering needed.
        # On other platforms, apply the DestinationIn fade as before.
        if self._top_fade_h > 0.01 and sys.platform != "darwin":
            h = float(max(1, self.height()))
            clear_local = self._top_fade_clear_h - self._top_fade_page_offset_y
            fade_h = max(1.0, float(self._top_fade_h))
            strength = max(1.0, float(self._top_fade_strength))
            grad = QLinearGradient(0.0, 0.0, 0.0, h)
            t_clear = max(0.0, min(1.0, clear_local / h))
            t_end = max(0.0, min(1.0, (clear_local + fade_h) / h))
            if t_end <= t_clear + 1e-4:
                alpha = 255 if t_clear <= 0.0 else 0
                grad.setColorAt(0.0, QColor(255, 255, 255, alpha))
                grad.setColorAt(1.0, QColor(255, 255, 255, alpha))
            else:
                t_mid = t_clear + ((t_end - t_clear) * 0.58)
                mid_alpha = int(round(255.0 * (0.58 ** strength)))
                grad.setColorAt(0.0, QColor(255, 255, 255, 0))
                grad.setColorAt(t_clear, QColor(255, 255, 255, 0))
                grad.setColorAt(t_mid, QColor(255, 255, 255, max(0, min(255, mid_alpha))))
                grad.setColorAt(t_end, QColor(255, 255, 255, 255))
                grad.setColorAt(1.0, QColor(255, 255, 255, 255))
            painter.save()
            painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            painter.fillRect(self.rect(), grad)
            painter.restore()
        painter.end()
