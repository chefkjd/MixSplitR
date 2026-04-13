import math
import os
import threading

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollBar,
    QVBoxLayout,
    QWidget,
)

from mixsplitr_devtools import annotate_widget

# Runtime-injected backend dependencies from main_ui.py.
AudioSegment = None
mixsplitr_editor = None
QMediaPlayer = None
QAudioOutput = None
QT_MEDIA_PLAYBACK_AVAILABLE = False


def configure_waveform_runtime(
    audio_segment_cls=None,
    editor_module=None,
    media_player_cls=None,
    audio_output_cls=None,
    media_playback_available=False,
):
    global AudioSegment
    global mixsplitr_editor
    global QMediaPlayer
    global QAudioOutput
    global QT_MEDIA_PLAYBACK_AVAILABLE
    AudioSegment = audio_segment_cls
    mixsplitr_editor = editor_module
    QMediaPlayer = media_player_cls
    QAudioOutput = audio_output_cls
    QT_MEDIA_PLAYBACK_AVAILABLE = bool(media_playback_available)


class NativeWaveformLoadThread(QThread):
    loaded = Signal(object, float, object)
    failed = Signal(str)
    progress = Signal(float, str)

    def __init__(
        self,
        audio_file,
        assisted_mode=False,
        split_silence_thresh_db=-37.0,
        split_seek_step_ms=20,
    ):
        super().__init__()
        self.audio_file = str(audio_file or "")
        self.assisted_mode = bool(assisted_mode)
        try:
            self.split_silence_thresh_db = float(split_silence_thresh_db)
        except Exception:
            self.split_silence_thresh_db = -37.0
        try:
            self.split_seek_step_ms = max(1, int(split_seek_step_ms))
        except Exception:
            self.split_seek_step_ms = 20
        self._cancel_requested = False
        self.result_loaded = False
        self.result_cancelled = False
        self.result_error = ""
        self.result_peaks = []
        self.result_duration_seconds = 0.0
        self.result_assisted_points = []

    def cancel(self):
        self._cancel_requested = True

    def _cancelled(self):
        return bool(self._cancel_requested)

    def _emit_progress(self, value, detail=""):
        try:
            percent = float(value)
        except Exception:
            percent = 0.0
        percent = max(0.0, min(100.0, percent))
        self.progress.emit(percent, str(detail or ""))

    def _record_failure(self, message):
        msg = str(message or "").strip()
        self.result_loaded = False
        self.result_cancelled = msg.lower() == "cancelled"
        self.result_error = msg if not self.result_cancelled else ""
        self.result_peaks = []
        self.result_duration_seconds = 0.0
        self.result_assisted_points = []

    def _compute_peaks(self, mono_audio, max_points=20000, progress_start=15.0, progress_end=82.0):
        try:
            samples = mono_audio.get_array_of_samples()
        except Exception:
            return []
        total_samples = len(samples)
        if total_samples <= 0:
            return []
        step = max(1, int(total_samples / float(max_points)))
        sample_width = max(1, int(getattr(mono_audio, "sample_width", 2) or 2))
        max_amp = float((1 << ((8 * sample_width) - 1)) - 1)
        if max_amp <= 0.0:
            max_amp = 32767.0
        peaks = []
        progress_span = max(0.0, float(progress_end) - float(progress_start))
        last_progress_int = -1
        for index in range(0, total_samples, step):
            if self._cancelled():
                return []
            chunk = samples[index:index + step]
            if not chunk:
                continue
            try:
                peak_val = max(abs(int(v)) for v in chunk)
            except Exception:
                peak_val = 0
            peaks.append(max(0.0, min(1.0, float(peak_val) / max_amp)))
            processed = min(total_samples, index + step)
            ratio = float(processed) / float(total_samples)
            loop_progress = float(progress_start) + (ratio * progress_span)
            progress_int = int(loop_progress)
            if progress_int > last_progress_int:
                last_progress_int = progress_int
                self._emit_progress(loop_progress, "Analyzing waveform peaks...")
        if not peaks:
            peaks = [0.0]
        return peaks

    def _assisted_points(
        self,
        audio,
        min_silence_len=2000,
        silence_thresh=None,
        seek_step=20,
        progress_start=86.0,
        progress_end=94.0,
    ):
        if audio is None:
            return []
        try:
            seg_len = int(len(audio))
            duration_seconds = float(seg_len) / 1000.0
        except Exception:
            return []
        if seg_len <= 0:
            return []
        if silence_thresh is None:
            silence_thresh = self.split_silence_thresh_db

        min_silence_len = max(250, int(min_silence_len))
        seek_step = max(1, int(seek_step))
        last_slice_start = seg_len - min_silence_len
        if last_slice_start < 0:
            self._emit_progress(progress_end, "No assisted anchors detected")
            return []

        try:
            max_amp = float(getattr(audio, "max_possible_amplitude", 0.0) or 0.0)
            if max_amp <= 0.0:
                max_amp = 32767.0
            silence_thresh_amp = max_amp * (10.0 ** (float(silence_thresh) / 20.0))
        except Exception:
            silence_thresh_amp = 0.0

        silent_starts = []
        progress_span = max(0.0, float(progress_end) - float(progress_start))
        base_steps = int(last_slice_start / seek_step) + 1
        total_steps = base_steps + (1 if (last_slice_start % seek_step) else 0)
        total_steps = max(1, total_steps)
        completed_steps = 0
        last_progress_int = -1

        def _scan_window(start_ms):
            try:
                return audio[start_ms:start_ms + min_silence_len].rms <= silence_thresh_amp
            except Exception:
                return False

        for start_ms in range(0, last_slice_start + 1, seek_step):
            if self._cancelled():
                return []
            if _scan_window(start_ms):
                silent_starts.append(start_ms)
            completed_steps += 1
            loop_progress = float(progress_start) + (float(completed_steps) / float(total_steps)) * progress_span
            progress_int = int(loop_progress)
            if progress_int > last_progress_int:
                last_progress_int = progress_int
                self._emit_progress(loop_progress, "Detecting assisted split anchors...")

        if (last_slice_start % seek_step) != 0:
            if self._cancelled():
                return []
            if _scan_window(last_slice_start):
                silent_starts.append(last_slice_start)
            self._emit_progress(progress_end, "Detecting assisted split anchors...")

        if not silent_starts:
            self._emit_progress(progress_end, "No assisted anchors detected")
            return []

        silent_ranges = []
        prev_i = int(silent_starts[0])
        current_range_start = prev_i
        for silence_start_i in [int(v) for v in silent_starts[1:]]:
            continuous = silence_start_i == (prev_i + seek_step)
            silence_has_gap = silence_start_i > (prev_i + min_silence_len)
            if (not continuous) and silence_has_gap:
                silent_ranges.append([current_range_start, prev_i + min_silence_len])
                current_range_start = silence_start_i
            prev_i = silence_start_i
        silent_ranges.append([current_range_start, prev_i + min_silence_len])

        points = []
        for start_ms, end_ms in silent_ranges:
            midpoint = float(start_ms + end_ms) / 2000.0
            if duration_seconds > 10.0:
                if midpoint <= 5.0 or midpoint >= (duration_seconds - 5.0):
                    continue
            elif midpoint <= 0.0:
                continue
            points.append(midpoint)
        points = sorted(set(points))
        self._emit_progress(progress_end, f"Detected {len(points)} assisted anchor(s)")
        return points

    def run(self):
        self.result_loaded = False
        self.result_cancelled = False
        self.result_error = ""
        self.result_peaks = []
        self.result_duration_seconds = 0.0
        self.result_assisted_points = []

        if AudioSegment is None:
            self._record_failure("Audio backend unavailable in this environment.")
            self.failed.emit("Audio backend unavailable in this environment.")
            return
        if not self.audio_file or not os.path.exists(self.audio_file):
            self._record_failure("Audio file not found.")
            self.failed.emit("Audio file not found.")
            return
        self._emit_progress(2.0, "Loading audio file...")
        try:
            audio = AudioSegment.from_file(self.audio_file)
        except Exception as exc:
            self._record_failure(f"Could not load audio: {exc}")
            self.failed.emit(f"Could not load audio: {exc}")
            return
        if self._cancelled():
            self._record_failure("Cancelled")
            self.failed.emit("Cancelled")
            return

        self._emit_progress(12.0, "Converting audio to waveform data...")
        mono = audio.set_channels(1)
        peaks = self._compute_peaks(mono, max_points=20000, progress_start=15.0, progress_end=82.0)
        if self._cancelled():
            self._record_failure("Cancelled")
            self.failed.emit("Cancelled")
            return

        assisted_points = []
        if self.assisted_mode:
            self._emit_progress(86.0, "Detecting assisted split anchors...")
            assisted_points = self._assisted_points(
                mono,
                silence_thresh=self.split_silence_thresh_db,
                seek_step=self.split_seek_step_ms,
            )
            if self._cancelled():
                self._record_failure("Cancelled")
                self.failed.emit("Cancelled")
                return
        self._emit_progress(95.0, "Finalizing waveform preview...")

        duration_seconds = float(len(audio)) / 1000.0
        self.result_loaded = True
        self.result_cancelled = False
        self.result_error = ""
        self.result_peaks = list(peaks or [])
        self.result_duration_seconds = float(duration_seconds or 0.0)
        self.result_assisted_points = [float(v) for v in list(assisted_points or [])]
        self._emit_progress(100.0, "Waveform ready")
        self.loaded.emit(self.result_peaks, self.result_duration_seconds, self.result_assisted_points)


class NativeWaveformCanvas(QWidget):
    points_changed = Signal(object)
    view_changed = Signal(float, float)
    scrub_point_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        annotate_widget(self, role="Waveform Canvas")
        self.setMinimumHeight(260)
        self.setMouseTracking(True)
        self.peaks = []
        self.duration = 0.0
        self.split_points = []
        self.zoom_pps = 100.0
        self.min_zoom_pps = 20.0
        self.max_zoom_pps = 1000.0
        self.offset_sec = 0.0
        self.playback_position_sec = None
        self.drag_marker_index = -1
        self.hover_marker_index = -1
        self.drag_scrub_marker = False
        self.hover_scrub_marker = False
        self._last_mouse_pos = None
        self.max_split_points = None

    def set_split_point_limit(self, limit=None):
        if limit in (None, "", 0):
            self.max_split_points = None
        else:
            try:
                parsed = int(limit)
            except Exception:
                parsed = 0
            self.max_split_points = parsed if parsed > 0 else None
        if self.max_split_points is not None and len(self.split_points) > self.max_split_points:
            self.split_points = sorted(self.split_points)[: self.max_split_points]
            self.update()
            self.points_changed.emit(list(self.split_points))

    def _fit_zoom_pps(self):
        width = max(1, int(self.width()))
        duration = max(0.001, float(self.duration))
        return max(0.001, float(width) / duration)

    def set_data(self, peaks, duration_seconds, split_points=None, fit_to_full_view=False):
        old_duration = float(self.duration)
        self.peaks = [float(v) for v in list(peaks or [])]
        self.duration = max(0.01, float(duration_seconds or 0.01))
        self.split_points = sorted(set([float(v) for v in list(split_points or []) if float(v) > 0.0]))
        if self.max_split_points is not None and len(self.split_points) > self.max_split_points:
            self.split_points = self.split_points[: self.max_split_points]
        self.min_zoom_pps = self._fit_zoom_pps()
        if fit_to_full_view or old_duration <= 0.01:
            self.zoom_pps = self.min_zoom_pps
            self.offset_sec = 0.0
        else:
            self.zoom_pps = max(self.min_zoom_pps, self.zoom_pps)
        self._clamp_offset()
        self.update()
        self.points_changed.emit(list(self.split_points))
        self._emit_view_changed()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.duration > 0.0:
            self.min_zoom_pps = self._fit_zoom_pps()
            self.zoom_pps = max(self.min_zoom_pps, self.zoom_pps)
            self._clamp_offset()
            self._emit_view_changed()

    def visible_duration(self):
        if self.zoom_pps <= 0.0:
            return self.duration
        return max(0.001, float(self.width()) / self.zoom_pps)

    def _clamp_offset(self):
        max_offset = max(0.0, self.duration - self.visible_duration())
        self.offset_sec = max(0.0, min(self.offset_sec, max_offset))

    def set_offset(self, offset_seconds):
        self.offset_sec = float(offset_seconds or 0.0)
        self._clamp_offset()
        self.update()
        self._emit_view_changed()

    def reset_zoom(self):
        if self.duration <= 0.0:
            return
        self.min_zoom_pps = self._fit_zoom_pps()
        self.zoom_pps = self.min_zoom_pps
        self.offset_sec = 0.0
        self.update()
        self._emit_view_changed()

    def zoom_by(self, factor):
        if self.duration <= 0.0:
            return
        try:
            factor = float(factor)
        except Exception:
            return
        if factor <= 0.0:
            return
        center_time = self.offset_sec + (self.visible_duration() / 2.0)
        self.zoom_pps = max(self.min_zoom_pps, min(self.max_zoom_pps, self.zoom_pps * factor))
        self.offset_sec = center_time - (self.visible_duration() / 2.0)
        self._clamp_offset()
        self.update()
        self._emit_view_changed()

    def set_playback_position(self, seconds):
        if seconds is None:
            self.playback_position_sec = None
        else:
            try:
                value = float(seconds)
            except Exception:
                value = 0.0
            self.playback_position_sec = max(0.0, min(self.duration, value))
        self.update()

    def _emit_view_changed(self):
        self.view_changed.emit(float(self.offset_sec), float(self.visible_duration()))

    def _layout_bounds(self):
        height = max(1, int(self.height()))
        timeline_height = 28
        plot_top = 8
        plot_bottom = max(plot_top + 24, height - timeline_height - 8)
        timeline_top = min(height - 6, plot_bottom + 4)
        timeline_bottom = max(timeline_top + 1, height - 6)
        axis_y = timeline_top + 3
        return plot_top, plot_bottom, timeline_top, timeline_bottom, axis_y

    def _is_plot_y(self, y_pos):
        plot_top, plot_bottom, _, _, _ = self._layout_bounds()
        value = float(y_pos)
        return plot_top <= value <= plot_bottom

    def _is_timeline_y(self, y_pos):
        _, _, timeline_top, timeline_bottom, _ = self._layout_bounds()
        value = float(y_pos)
        return timeline_top <= value <= timeline_bottom

    def _set_scrub_from_x(self, x_pos, emit_signal=True):
        seconds = self._time_for_x(x_pos)
        self.set_playback_position(seconds)
        if emit_signal:
            self.scrub_point_changed.emit(float(seconds))

    def _time_for_x(self, x_value):
        return max(0.0, min(self.duration, self.offset_sec + (float(x_value) / max(0.001, self.zoom_pps))))

    def _x_for_time(self, time_value):
        return int((float(time_value) - self.offset_sec) * self.zoom_pps)

    def _nearest_marker_index(self, x_pos):
        if not self.split_points:
            return -1
        target = self._time_for_x(x_pos)
        threshold = max(0.5, 14.0 / max(0.001, self.zoom_pps))
        best_index = -1
        best_delta = 1e9
        for idx, marker in enumerate(self.split_points):
            delta = abs(marker - target)
            if delta < best_delta and delta <= threshold:
                best_delta = delta
                best_index = idx
        return best_index

    def _timeline_tick_step(self, visible_seconds):
        pixels_per_second = max(0.001, float(self.zoom_pps))
        target_px = 90.0
        steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200]
        for step in steps:
            if (float(step) * pixels_per_second) >= target_px:
                return float(step)
        return max(1.0, float(visible_seconds) / 6.0)

    def _format_timeline_time(self, seconds):
        total = max(0, int(round(float(seconds))))
        minutes, secs = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _peak_position_for_time(self, seconds):
        peak_count = len(self.peaks)
        if peak_count <= 1 or self.duration <= 0.0:
            return 0.0
        ratio = max(0.0, min(1.0, float(seconds) / self.duration))
        return ratio * float(peak_count - 1)

    def _interpolated_peak(self, peak_position):
        peak_count = len(self.peaks)
        if peak_count <= 0:
            return 0.0
        if peak_count == 1:
            return float(self.peaks[0])
        clamped = max(0.0, min(float(peak_count - 1), float(peak_position)))
        left_index = int(math.floor(clamped))
        right_index = min(peak_count - 1, left_index + 1)
        if right_index == left_index:
            return float(self.peaks[left_index])
        blend = clamped - float(left_index)
        left_peak = float(self.peaks[left_index])
        right_peak = float(self.peaks[right_index])
        return (left_peak * (1.0 - blend)) + (right_peak * blend)

    def _wave_amplitude_for_time_range(self, start_seconds, end_seconds):
        peak_count = len(self.peaks)
        if peak_count <= 0:
            return 0.0
        left_peak = self._peak_position_for_time(start_seconds)
        right_peak = self._peak_position_for_time(end_seconds)
        if right_peak < left_peak:
            left_peak, right_peak = right_peak, left_peak
        if (right_peak - left_peak) <= 1.0:
            midpoint = (left_peak + right_peak) / 2.0
            return max(
                self._interpolated_peak(left_peak),
                self._interpolated_peak(midpoint),
                self._interpolated_peak(right_peak),
            )

        start_index = max(0, int(math.floor(left_peak)))
        end_index = min(peak_count - 1, int(math.ceil(right_peak)))
        amplitude = 0.0
        for idx in range(start_index, end_index + 1):
            amplitude = max(amplitude, float(self.peaks[idx]))
        return amplitude

    def _add_split_point(self, seconds):
        value = float(seconds or 0.0)
        if value <= 0.0 or value >= self.duration:
            return
        for marker in self.split_points:
            if abs(marker - value) < 0.25:
                return
        if self.max_split_points is not None and len(self.split_points) >= self.max_split_points:
            return
        self.split_points.append(value)
        self.split_points = sorted(set(self.split_points))
        self.update()
        self.points_changed.emit(list(self.split_points))

    def _remove_split_point(self, index):
        if index < 0 or index >= len(self.split_points):
            return
        del self.split_points[index]
        self.update()
        self.points_changed.emit(list(self.split_points))

    def _marker_draw_bounds(self, plot_top, plot_bottom):
        marker_top = min(plot_bottom, plot_top + 8)
        marker_bottom = max(marker_top + 1, plot_bottom - 8)
        return marker_top, marker_bottom

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect()
        painter.fillRect(rect, QColor("#0F1012"))
        if not self.peaks or self.duration <= 0.0:
            painter.setPen(QPen(QColor("#7B819B")))
            painter.drawText(rect, Qt.AlignCenter, "Waveform not loaded")
            return

        width = max(1, rect.width())
        plot_top, plot_bottom, timeline_top, timeline_bottom, axis_y = self._layout_bounds()
        mid_y = int((plot_top + plot_bottom) / 2.0)
        wave_half = int(max(10, ((plot_bottom - plot_top) / 2.0) - 8))

        start_t = self.offset_sec
        visible = self.visible_duration()
        end_t = min(self.duration, start_t + visible)

        played_until = None
        if self.playback_position_sec is not None:
            try:
                played_until = max(0.0, min(self.duration, float(self.playback_position_sec)))
            except Exception:
                played_until = None
        wave_played_pen = QPen(QColor("#6D88A8"))
        wave_unplayed_pen = QPen(QColor("#4A5568"))
        seconds_per_pixel = 1.0 / max(0.001, self.zoom_pps)
        for x in range(width):
            left_t = start_t + (float(x) * seconds_per_pixel)
            if left_t > end_t:
                break
            right_t = min(end_t, left_t + seconds_per_pixel)
            amp = self._wave_amplitude_for_time_range(left_t, right_t)
            y_delta = int(amp * wave_half)
            if played_until is not None and left_t <= played_until:
                painter.setPen(wave_played_pen)
            else:
                painter.setPen(wave_unplayed_pen)
            painter.drawLine(x, mid_y - y_delta, x, mid_y + y_delta)

        painter.setPen(QPen(QColor("#2A2D33")))
        painter.drawLine(0, mid_y, width, mid_y)

        marker_top, marker_bottom = self._marker_draw_bounds(plot_top, plot_bottom)
        marker_pen = QPen(QColor("#E45D5D"))
        marker_pen.setWidth(5)
        marker_pen.setCapStyle(Qt.RoundCap)
        highlight_pen = QPen(QColor("#FF8C8C"))
        highlight_pen.setWidth(7)
        highlight_pen.setCapStyle(Qt.RoundCap)
        for marker_idx, marker in enumerate(self.split_points):
            if marker < start_t or marker > end_t:
                continue
            x = self._x_for_time(marker)
            is_highlighted = marker_idx == self.drag_marker_index or marker_idx == self.hover_marker_index
            painter.setPen(highlight_pen if is_highlighted else marker_pen)
            painter.drawLine(x, marker_top, x, marker_bottom)

        # Timeline axis and timecode labels along the bottom of the waveform view.
        timeline_pen = QPen(QColor("#535965"))
        timeline_pen.setWidth(1)
        painter.setPen(timeline_pen)
        painter.drawLine(0, axis_y, width, axis_y)
        tick_step = self._timeline_tick_step(visible)
        first_tick = math.floor(start_t / tick_step) * tick_step
        if first_tick < start_t:
            first_tick += tick_step
        label_pen = QPen(QColor("#A6ACB8"))
        painter.setPen(label_pen)
        last_label_right = -1000
        tick = first_tick
        tick_guard = 0
        while tick <= (end_t + 0.0001) and tick_guard < 1000:
            tick_guard += 1
            x = self._x_for_time(tick)
            if 0 <= x <= width:
                painter.drawLine(x, axis_y, x, axis_y + 6)
                label = self._format_timeline_time(tick)
                label_width = 78
                left = int(x - (label_width / 2))
                right = left + label_width
                if left > (last_label_right + 8):
                    painter.drawText(
                        left,
                        axis_y + 7,
                        label_width,
                        max(1, timeline_bottom - (axis_y + 7)),
                        Qt.AlignHCenter | Qt.AlignTop,
                        label,
                    )
                    last_label_right = right
            tick += tick_step

        if self.playback_position_sec is not None and start_t <= float(self.playback_position_sec) <= end_t:
            scrub_x = self._x_for_time(float(self.playback_position_sec))
            scrub_pen = QPen(QColor("#69B97E"))
            scrub_pen.setWidth(2 if not (self.drag_scrub_marker or self.hover_scrub_marker) else 3)
            scrub_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(scrub_pen)
            marker_top = axis_y - 2
            marker_bottom = timeline_bottom - 1
            painter.drawLine(scrub_x, marker_top, scrub_x, marker_bottom)

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        x_pos = float(event.position().x())
        y_pos = float(event.position().y())
        self.drag_marker_index = -1
        self._last_mouse_pos = None
        if not self._is_plot_y(y_pos):
            event.accept()
            return
        index = self._nearest_marker_index(x_pos)
        if index >= 0:
            self._remove_split_point(index)
        else:
            self._add_split_point(self._time_for_x(x_pos))
        self.hover_marker_index = self._nearest_marker_index(x_pos)
        self.hover_scrub_marker = False
        if self.hover_marker_index >= 0:
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
        self.update()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        x_pos = float(event.position().x())
        y_pos = float(event.position().y())
        if self._is_timeline_y(y_pos):
            self.drag_scrub_marker = True
            self.hover_scrub_marker = True
            self._set_scrub_from_x(x_pos, emit_signal=True)
            self.setCursor(Qt.SizeHorCursor)
            self.update()
            event.accept()
            return

        is_plot_click = self._is_plot_y(y_pos)
        index = self._nearest_marker_index(x_pos) if is_plot_click else -1
        self.hover_marker_index = index
        self.hover_scrub_marker = False
        if is_plot_click and index < 0:
            # Single-click on waveform body seeks playback without changing split markers.
            self._set_scrub_from_x(x_pos, emit_signal=True)
            self.setCursor(Qt.ArrowCursor)
            self.update()
            event.accept()
            return
        self.drag_marker_index = index
        self._last_mouse_pos = x_pos
        self.setCursor(Qt.PointingHandCursor if index >= 0 else Qt.ArrowCursor)
        self.update()
        event.accept()

    def mouseMoveEvent(self, event):
        x_pos = event.position().x()
        y_pos = event.position().y()
        if self.drag_scrub_marker:
            self._set_scrub_from_x(x_pos, emit_signal=True)
            self.update()
            event.accept()
            return

        nearest_index = self._nearest_marker_index(x_pos) if self._is_plot_y(y_pos) else -1
        if self.drag_marker_index >= 0:
            marker_time = self._time_for_x(x_pos)
            marker_time = max(0.01, min(self.duration - 0.01, marker_time))
            self.split_points[self.drag_marker_index] = marker_time
            self.split_points = sorted(self.split_points)
            self.drag_marker_index = self._nearest_marker_index(x_pos)
            self.hover_marker_index = self.drag_marker_index
            self.update()
            self.points_changed.emit(list(self.split_points))
            event.accept()
            return

        timeline_hover = self._is_timeline_y(y_pos)
        if timeline_hover != self.hover_scrub_marker:
            self.hover_scrub_marker = timeline_hover
            self.update()

        if nearest_index != self.hover_marker_index:
            self.hover_marker_index = nearest_index
            self.update()
        if nearest_index >= 0:
            self.setCursor(Qt.PointingHandCursor)
        elif timeline_hover:
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
        event.accept()

    def mouseReleaseEvent(self, event):
        if self.drag_scrub_marker:
            self.drag_scrub_marker = False
            y_pos = float(event.position().y())
            self.hover_scrub_marker = self._is_timeline_y(y_pos)
            if self.hover_scrub_marker:
                self.setCursor(Qt.SizeHorCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            self.update()
            event.accept()
            return

        self.drag_marker_index = -1
        self._last_mouse_pos = None
        y_pos = float(event.position().y())
        if self._is_plot_y(y_pos):
            self.hover_marker_index = self._nearest_marker_index(event.position().x())
        else:
            self.hover_marker_index = -1
        self.hover_scrub_marker = self._is_timeline_y(y_pos)
        if self.hover_marker_index >= 0:
            self.setCursor(Qt.PointingHandCursor)
        elif self.hover_scrub_marker:
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
        self.update()
        event.accept()

    def leaveEvent(self, event):
        self.hover_marker_index = -1
        self.hover_scrub_marker = False
        self.drag_scrub_marker = False
        self.setCursor(Qt.ArrowCursor)
        self.update()
        event.accept()

    def wheelEvent(self, event):
        if self.duration <= 0.0:
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        cursor_x = float(event.position().x())
        anchor_time = self._time_for_x(cursor_x)
        factor = 1.15 if delta > 0 else (1.0 / 1.15)
        self.zoom_pps = max(self.min_zoom_pps, min(self.max_zoom_pps, self.zoom_pps * factor))
        self.offset_sec = anchor_time - (cursor_x / max(0.001, self.zoom_pps))
        self._clamp_offset()
        self.update()
        self._emit_view_changed()
        event.accept()


class NativeWaveformEditorDialog(QDialog):
    def __init__(self, parent, audio_file, peaks, duration_seconds, initial_points=None, editor_mode="split"):
        super().__init__(parent)
        annotate_widget(self, role="Waveform Editor Dialog")
        self.audio_file = str(audio_file or "")
        self.duration = max(0.01, float(duration_seconds or 0.01))
        self.editor_mode = str(editor_mode or "split").strip().lower()
        if self.editor_mode not in ("split", "trim"):
            self.editor_mode = "split"
        self.trim_mode = self.editor_mode == "trim"
        self.selected_points = sorted(set([float(v) for v in list(initial_points or []) if float(v) > 0.0]))
        if self.trim_mode and not self.selected_points:
            edge_seconds = min(0.05, max(0.001, self.duration / 10.0))
            if self.duration > (edge_seconds * 2.0):
                self.selected_points = [edge_seconds, max(edge_seconds, self.duration - edge_seconds)]
        if self.trim_mode and len(self.selected_points) > 2:
            self.selected_points = self.selected_points[:2]
        self.trim_start_seconds = 0.0
        self.trim_end_seconds = float(self.duration)
        self.media_player = None
        self.audio_output = None
        self.playback_duration_ms = max(1, int(round(self.duration * 1000.0)))

        if self.trim_mode:
            self.setWindowTitle(f"Waveform Trim Editor - {os.path.basename(self.audio_file)}")
        else:
            self.setWindowTitle(f"Waveform Split Editor - {os.path.basename(self.audio_file)}")
        self.resize(1200, 780)
        self._apply_dialog_theme()

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        if self.trim_mode:
            info = QLabel(
                "Double-click to add trim start/end markers. Double-click a marker to delete it. Drag markers to adjust."
            )
        else:
            info = QLabel(
                "Double-click to add a split point. Double-click a split point to delete it. Drag markers to adjust."
            )
        info.setStyleSheet("color: #C4C7C5;")
        root.addWidget(info)

        self.canvas = NativeWaveformCanvas(self)
        if self.trim_mode:
            self.canvas.set_split_point_limit(2)
        root.addWidget(self.canvas, 1)

        self.scrollbar = QScrollBar(Qt.Horizontal, self)
        self.scrollbar.setRange(0, 0)
        self.scrollbar.valueChanged.connect(self._on_scrollbar_changed)
        root.addWidget(self.scrollbar)

        playback_row = QHBoxLayout()
        self.play_btn = QPushButton("Play")
        self.play_btn.setProperty("class", "SecondaryButton")
        self.play_btn.clicked.connect(self._toggle_playback)
        playback_row.addWidget(self.play_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setProperty("class", "SecondaryButton")
        self.stop_btn.clicked.connect(self._stop_playback)
        playback_row.addWidget(self.stop_btn)
        playback_row.addStretch(1)

        self.playback_label = QLabel(f"{self._format_time(0)} / {self._format_time(self.duration)}")
        self.playback_label.setStyleSheet("color: #C4C7C5; font-family: monospace;")
        playback_row.addWidget(self.playback_label)
        root.addLayout(playback_row)

        row = QHBoxLayout()
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setStyleSheet("color: #C4C7C5; font-family: monospace;")
        row.addWidget(self.time_label)
        row.addStretch(1)

        self.zoom_out_btn = QPushButton("Zoom Out")
        self.zoom_out_btn.setProperty("class", "SecondaryButton")
        self.zoom_out_btn.clicked.connect(self._zoom_out)
        row.addWidget(self.zoom_out_btn)

        self.zoom_in_btn = QPushButton("Zoom In")
        self.zoom_in_btn.setProperty("class", "SecondaryButton")
        self.zoom_in_btn.clicked.connect(self._zoom_in)
        row.addWidget(self.zoom_in_btn)

        self.reset_zoom_btn = QPushButton("Reset Zoom")
        self.reset_zoom_btn.setProperty("class", "SecondaryButton")
        self.reset_zoom_btn.clicked.connect(self._reset_zoom)
        row.addWidget(self.reset_zoom_btn)

        self.import_btn = None
        if not self.trim_mode:
            self.import_btn = QPushButton("Import Tracklist")
            self.import_btn.setProperty("class", "SecondaryButton")
            self.import_btn.clicked.connect(self._import_tracklist)
            row.addWidget(self.import_btn)

        self.clear_btn = QPushButton("Clear Trim Points" if self.trim_mode else "Clear All")
        self.clear_btn.setProperty("class", "SecondaryButton")
        self.clear_btn.clicked.connect(self._clear_all_points)
        row.addWidget(self.clear_btn)
        root.addLayout(row)

        self.point_list = QListWidget(self)
        self.point_list.setMinimumHeight(140)
        root.addWidget(self.point_list)

        point_actions = QHBoxLayout()
        self.remove_btn = QPushButton("Remove Selected Marker" if self.trim_mode else "Remove Selected")
        self.remove_btn.setProperty("class", "SecondaryButton")
        self.remove_btn.clicked.connect(self._remove_selected_point)
        point_actions.addWidget(self.remove_btn)
        point_actions.addStretch(1)
        root.addLayout(point_actions)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setProperty("class", "SecondaryButton")
        self.cancel_btn.clicked.connect(self._cancel)
        buttons.addWidget(self.cancel_btn)
        self.done_btn = QPushButton("Use Trim" if self.trim_mode else "Done")
        self.done_btn.setProperty("class", "ActionButton")
        self.done_btn.clicked.connect(self._done)
        buttons.addWidget(self.done_btn)
        root.addLayout(buttons)

        self._updating_scroll = False
        self.canvas.points_changed.connect(self._on_points_changed)
        self.canvas.view_changed.connect(self._on_canvas_view_changed)
        self.canvas.scrub_point_changed.connect(self._on_canvas_scrub_point_changed)

        self.canvas.set_data(peaks, self.duration, self.selected_points, fit_to_full_view=True)
        self._refresh_point_list()
        self._on_canvas_view_changed(0.0, self.canvas.visible_duration())
        self._initialize_playback_backend()

    def _apply_dialog_theme(self):
        self.setStyleSheet(
            """
            QDialog {
                background-color: #181A1E;
            }
            QPushButton {
                border-radius: 12px;
                min-height: 34px;
                padding: 8px 16px;
                border: none;
                font-size: 14px;
            }
            QPushButton[class="SecondaryButton"] {
                background-color: rgba(46, 52, 62, 0.62);
                color: #E3E3E3;
            }
            QPushButton[class="SecondaryButton"]:hover {
                background-color: rgba(74, 96, 124, 0.52);
            }
            QPushButton[class="SecondaryButton"]:pressed {
                background-color: rgba(62, 80, 103, 0.62);
            }
            QPushButton[class="ActionButton"] {
                background-color: #69B97E;
                color: #131314;
            }
            QPushButton[class="ActionButton"]:hover {
                background-color: #7BCB90;
            }
            QPushButton:disabled {
                background-color: rgba(56, 62, 72, 0.88);
                color: #AEB8C7;
            }
            """
        )

    def _format_time(self, seconds):
        total = max(0, int(float(seconds)))
        mins, secs = divmod(total, 60)
        hours, mins = divmod(mins, 60)
        if hours > 0:
            return f"{hours:02d}:{mins:02d}:{secs:02d}"
        return f"{mins:02d}:{secs:02d}"

    def _initialize_playback_backend(self):
        if not QT_MEDIA_PLAYBACK_AVAILABLE or QMediaPlayer is None or QAudioOutput is None:
            self.play_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.playback_label.setText("Playback unavailable")
            return
        if not self.audio_file or not os.path.exists(self.audio_file):
            self.play_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.playback_label.setText("Playback file missing")
            return

        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.9)
        self.media_player = QMediaPlayer(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setSource(QUrl.fromLocalFile(self.audio_file))
        self.media_player.positionChanged.connect(self._on_playback_position_changed)
        self.media_player.durationChanged.connect(self._on_playback_duration_changed)
        self.media_player.playbackStateChanged.connect(self._on_playback_state_changed)
        if hasattr(self.media_player, "errorOccurred"):
            self.media_player.errorOccurred.connect(self._on_playback_error)

        self.play_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.canvas.set_playback_position(0.0)

    def _playback_is_playing(self):
        if self.media_player is None:
            return False
        state = self.media_player.playbackState()
        if hasattr(QMediaPlayer, "PlaybackState"):
            return state == QMediaPlayer.PlaybackState.PlayingState
        return str(state).lower().endswith("playingstate")

    def _toggle_playback(self):
        if self.media_player is None:
            return
        if self._playback_is_playing():
            self.media_player.pause()
        else:
            self.media_player.play()

    def _stop_playback(self):
        if self.media_player is None:
            return
        self.media_player.stop()
        self.canvas.set_playback_position(0.0)
        self.playback_label.setText(f"{self._format_time(0)} / {self._format_time(self.duration)}")

    def _on_playback_position_changed(self, position_ms):
        position_ms = max(0, int(position_ms or 0))
        position_sec = float(position_ms) / 1000.0
        self.canvas.set_playback_position(position_sec)
        self.playback_label.setText(
            f"{self._format_time(position_sec)} / {self._format_time(float(self.playback_duration_ms) / 1000.0)}"
        )

    def _on_canvas_scrub_point_changed(self, seconds):
        try:
            position_sec = max(0.0, min(self.duration, float(seconds)))
        except Exception:
            position_sec = 0.0
        self.canvas.set_playback_position(position_sec)
        self.playback_label.setText(
            f"{self._format_time(position_sec)} / {self._format_time(float(self.playback_duration_ms) / 1000.0)}"
        )
        if self.media_player is None:
            return
        try:
            self.media_player.setPosition(int(round(position_sec * 1000.0)))
        except Exception:
            pass

    def _on_playback_duration_changed(self, duration_ms):
        duration_ms = max(1, int(duration_ms or 0))
        if duration_ms > 1:
            self.playback_duration_ms = duration_ms
        else:
            self.playback_duration_ms = max(1, int(round(self.duration * 1000.0)))
        current_sec = 0.0
        if self.media_player is not None:
            try:
                current_sec = max(0.0, float(self.media_player.position()) / 1000.0)
            except Exception:
                current_sec = 0.0
        self.playback_label.setText(
            f"{self._format_time(current_sec)} / {self._format_time(float(self.playback_duration_ms) / 1000.0)}"
        )

    def _on_playback_state_changed(self, _state):
        self.play_btn.setText("Pause" if self._playback_is_playing() else "Play")

    def _on_playback_error(self, *_args):
        self.playback_label.setText("Playback error")

    def _cleanup_playback(self):
        if self.media_player is not None:
            try:
                self.media_player.stop()
            except Exception:
                pass
            try:
                self.media_player.setSource(QUrl())
            except Exception:
                pass

    def _cancel(self):
        self.reject()

    def _on_points_changed(self, points):
        self.selected_points = sorted(set([float(v) for v in list(points or []) if float(v) > 0.0]))
        self._refresh_point_list()

    def _refresh_point_list(self):
        self.point_list.clear()
        if self.trim_mode:
            if not self.selected_points:
                self.point_list.addItem("Add two markers to define the trim start and end.")
                return
            labels = ("Start", "End")
            for idx, point in enumerate(self.selected_points[:2]):
                item = QListWidgetItem(f"{labels[idx]}: {self._format_time(point)}")
                item.setData(Qt.UserRole, float(point))
                self.point_list.addItem(item)
            if len(self.selected_points) < 2:
                self.point_list.addItem("End: not set")
                return
            start_point = float(self.selected_points[0])
            end_point = float(self.selected_points[1])
            kept_duration = max(0.0, end_point - start_point)
            removed_duration = max(0.0, self.duration - kept_duration)
            summary_item = QListWidgetItem(
                f"Kept: {self._format_time(kept_duration)}   Removed: {self._format_time(removed_duration)}"
            )
            summary_item.setFlags(Qt.ItemIsEnabled)
            self.point_list.addItem(summary_item)
            return

        segments = [0.0] + self.selected_points + [self.duration]
        if not self.selected_points:
            self.point_list.addItem("No split points set. Automatic fallback will be used if you click Done.")
            return
        for idx, point in enumerate(self.selected_points):
            prev_start = segments[idx]
            segment_len = max(0.0, float(point) - float(prev_start))
            item = QListWidgetItem(
                f"{idx + 1:02d}. {self._format_time(point)}   (segment {self._format_time(segment_len)})"
            )
            item.setData(Qt.UserRole, float(point))
            self.point_list.addItem(item)

    def _on_canvas_view_changed(self, offset, visible):
        self._updating_scroll = True
        try:
            start_label = self._format_time(offset)
            end_label = self._format_time(min(self.duration, float(offset) + max(0.0, float(visible))))
            self.time_label.setText(f"{start_label} - {end_label} / {self._format_time(self.duration)}")
            max_offset = max(0.0, self.duration - max(0.001, visible))
            if max_offset <= 0.0:
                self.scrollbar.setRange(0, 0)
                self.scrollbar.setPageStep(1)
                self.scrollbar.setSingleStep(1)
                self.scrollbar.setValue(0)
            else:
                max_offset_ms = max(1, int(round(max_offset * 1000.0)))
                visible_ms = max(1, int(round(max(0.001, float(visible)) * 1000.0)))
                offset_ms = int(round(max(0.0, min(max_offset, float(offset))) * 1000.0))
                self.scrollbar.setRange(0, max_offset_ms)
                self.scrollbar.setPageStep(min(max_offset_ms + 1, visible_ms))
                self.scrollbar.setSingleStep(max(1, int(visible_ms / 20)))
                self.scrollbar.setValue(max(0, min(max_offset_ms, offset_ms)))
        finally:
            self._updating_scroll = False

    def _on_scrollbar_changed(self, value):
        if self._updating_scroll:
            return
        self.canvas.set_offset(float(value) / 1000.0)

    def _zoom_in(self):
        self.canvas.zoom_by(1.25)

    def _zoom_out(self):
        self.canvas.zoom_by(1.0 / 1.25)

    def _reset_zoom(self):
        self.canvas.reset_zoom()

    def _remove_selected_point(self):
        row = self.point_list.currentRow()
        if row < 0 or row >= len(self.selected_points):
            return
        del self.selected_points[row]
        self.selected_points = sorted(set(self.selected_points))
        self.canvas.set_data(self.canvas.peaks, self.duration, self.selected_points)

    def _clear_all_points(self):
        self.selected_points = []
        self.canvas.set_data(self.canvas.peaks, self.duration, self.selected_points)

    def _import_tracklist(self):
        if not mixsplitr_editor or not hasattr(mixsplitr_editor, "parse_tracklist"):
            QMessageBox.warning(self, "Tracklist Import", "Tracklist parser is unavailable.")
            return
        text, ok = QInputDialog.getMultiLineText(
            self,
            "Import Tracklist",
            "Paste tracklist or cue sheet:",
            "",
        )
        if not ok:
            return
        raw = str(text or "").strip()
        if not raw:
            return
        try:
            tracklist = mixsplitr_editor.parse_tracklist(raw)
        except Exception as exc:
            QMessageBox.warning(self, "Tracklist Import", f"Parse failed: {exc}")
            return
        if not tracklist:
            QMessageBox.information(self, "Tracklist Import", "No valid track entries found.")
            return
        imported = []
        for row in tracklist:
            try:
                ts = float(row.get("timestamp", 0.0))
            except Exception:
                continue
            if ts > 0.0 and ts < self.duration:
                imported.append(ts)
        self.selected_points = sorted(set(imported))
        self.canvas.set_data(self.canvas.peaks, self.duration, self.selected_points)

    def _done(self):
        self.selected_points = sorted(set([float(v) for v in self.canvas.split_points if float(v) > 0.0]))
        if self.trim_mode:
            if len(self.selected_points) != 2:
                QMessageBox.information(
                    self,
                    "Trim Range",
                    "Add both a trim start marker and a trim end marker before continuing.",
                )
                return
            trim_start = float(self.selected_points[0])
            trim_end = float(self.selected_points[1])
            boundary_snap = min(0.25, max(0.05, self.duration / 1000.0))
            if trim_start <= boundary_snap:
                trim_start = 0.0
            if trim_end >= (self.duration - boundary_snap):
                trim_end = float(self.duration)
            if trim_end <= trim_start:
                QMessageBox.warning(
                    self,
                    "Trim Range",
                    "Trim end must be later than trim start.",
                )
                return
            self.trim_start_seconds = trim_start
            self.trim_end_seconds = trim_end
        self.accept()

    def reject(self):
        self._cleanup_playback()
        super().reject()

    def accept(self):
        self._cleanup_playback()
        super().accept()


class AudioPreviewThread(QThread):
    status_update = Signal(str)
    finished_update = Signal(bool, str)

    def __init__(self, file_path, duration_seconds=30):
        super().__init__()
        self.file_path = str(file_path or "")
        self.duration_seconds = int(max(1, duration_seconds))
        self._stop_event = threading.Event()

    def stop_playback(self):
        self._stop_event.set()

    def run(self):
        if not self.file_path or not os.path.exists(self.file_path):
            self.finished_update.emit(False, "Preview audio file not found")
            return
        if not mixsplitr_editor or not hasattr(mixsplitr_editor, "play_audio_preview"):
            self.finished_update.emit(False, "Preview playback backend unavailable")
            return
        self.status_update.emit("Playing preview audio...")
        try:
            ok = bool(
                mixsplitr_editor.play_audio_preview(
                    self.file_path,
                    duration_seconds=self.duration_seconds,
                    show_status=False,
                    stop_event=self._stop_event,
                )
            )
        except Exception as exc:
            self.finished_update.emit(False, f"Preview failed: {exc}")
            return
        if self._stop_event.is_set():
            self.finished_update.emit(True, "Preview playback stopped")
            return
        if ok:
            self.finished_update.emit(True, "Preview playback finished")
        else:
            self.finished_update.emit(False, "Preview playback could not start")
