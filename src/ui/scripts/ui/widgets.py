"""可复用控件：StatusDot · CircularGauge · AttitudeWidget · GlassCard 系列。"""

from __future__ import annotations

import math
import os
from typing import List, Optional

from PyQt5.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap, QRadialGradient,
)
from PyQt5.QtWidgets import (
    QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QProgressBar,
    QVBoxLayout, QWidget,
)

from qfluentwidgets import (
    BodyLabel, CaptionLabel, SimpleCardWidget, StrongBodyLabel,
    SubtitleLabel, TitleLabel,
)

from ui.theme import ThemeManager


# ─────────────────────────────────────────────────────────────────────────────
# StatusDot
# ─────────────────────────────────────────────────────────────────────────────

class StatusDot(QWidget):
    def __init__(self, color: str = "#7A8FAA", diameter: int = 12, parent=None):
        super().__init__(parent)
        self._color        = QColor(color)
        self._diameter     = diameter
        self._blink_colors = (QColor(color), QColor(color))
        self._blink_index  = 0
        self._settle_color = QColor(color)
        self._blinking     = False

        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._toggle_blink)
        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.timeout.connect(self._finish_blink)
        self.setFixedSize(diameter + 10, diameter + 10)

    def set_color(self, color: str, preserve_blink: bool = False):
        new_color = QColor(color)
        if preserve_blink and self._blinking:
            self._settle_color = new_color
            return
        self._blink_timer.stop()
        self._stop_timer.stop()
        self._blinking     = False
        self._color        = new_color
        self._settle_color = new_color
        self.update()

    def blink(self, color_a: str, color_b: str, duration_ms: int,
              settle_color: str, interval_ms: int = 100):
        self._blink_colors = (QColor(color_a), QColor(color_b))
        self._blink_index  = 0
        self._settle_color = QColor(settle_color)
        self._color        = self._blink_colors[0]
        self._blinking     = True
        self._blink_timer.start(interval_ms)
        self._stop_timer.start(duration_ms)
        self.update()

    def _toggle_blink(self):
        self._blink_index = 1 - self._blink_index
        self._color       = self._blink_colors[self._blink_index]
        self.update()

    def _finish_blink(self):
        self._blink_timer.stop()
        self._blinking = False
        self._color    = self._settle_color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        radius = self._diameter / 2
        halo   = QColor(self._color)
        halo.setAlpha(70)
        painter.setPen(Qt.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(center, radius + 4, radius + 4)
        painter.setBrush(self._color)
        painter.drawEllipse(center, radius, radius)


# ─────────────────────────────────────────────────────────────────────────────
# CircularGauge — 270° arc dial
# ─────────────────────────────────────────────────────────────────────────────

class CircularGauge(QWidget):
    def __init__(self, min_val: float = 0.0, max_val: float = 255.0,
                 size: int = 72, color: str = "#4D8CFF", parent=None):
        super().__init__(parent)
        self._min   = float(min_val)
        self._max   = float(max_val)
        self._value = float(min_val)
        self._color = QColor(color)
        self._text  = ""
        self.setFixedSize(size, size)

    def set_value(self, value: float, text: str = ""):
        self._value = max(self._min, min(self._max, float(value)))
        self._text  = text
        self.update()

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        w, h   = self.width(), self.height()
        margin = 8
        rect   = QRectF(margin, margin, w - 2 * margin, h - 2 * margin)

        tm = ThemeManager.instance()
        if tm.is_dark:
            track_color = QColor(80, 90, 120, 100)
        else:
            track_color = QColor(180, 200, 228, 90)

        track_pen = QPen(track_color, 5, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(track_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(rect, int(225 * 16), int(-270 * 16))

        ratio    = (self._value - self._min) / max(1e-9, self._max - self._min)
        val_span = int(-270 * ratio * 16)
        if abs(val_span) > 0:
            val_pen = QPen(self._color, 5, Qt.SolidLine, Qt.RoundCap)
            painter.setPen(val_pen)
            painter.drawArc(rect, int(225 * 16), val_span)

        if self._text:
            text_color = tm.color("text_primary")
            painter.setPen(QPen(QColor(text_color)))
            font = QFont("Segoe UI", 8, QFont.Medium)
            painter.setFont(font)
            painter.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, self._text)


# ─────────────────────────────────────────────────────────────────────────────
# AttitudeWidget — mini artificial horizon
# ─────────────────────────────────────────────────────────────────────────────

class AttitudeWidget(QWidget):
    def __init__(self, size: int = 72, parent=None):
        super().__init__(parent)
        self._roll  = 0.0
        self._pitch = 0.0
        self.setFixedSize(size, size)

    def set_attitude(self, roll_deg: float, pitch_deg: float):
        self._roll  = roll_deg
        self._pitch = pitch_deg
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing)
        w, h   = self.width(), self.height()
        cx, cy = w / 2, h / 2
        r      = min(w, h) / 2 - 3

        clip = QPainterPath()
        clip.addEllipse(QPointF(cx, cy), r, r)
        painter.setClipPath(clip)

        painter.save()
        painter.translate(cx, cy)
        painter.rotate(self._roll)
        pitch_px = self._pitch * (r / 30)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#A8C8E8"))
        painter.drawRect(int(-r), int(-r * 2 - pitch_px), int(r * 2), int(r * 2))
        painter.setBrush(QColor("#C8A882"))
        painter.drawRect(int(-r), int(-pitch_px), int(r * 2), int(r * 2))

        painter.setPen(QPen(QColor("#FFFFFF"), 1.5))
        painter.drawLine(int(-r), int(-pitch_px), int(r), int(-pitch_px))
        painter.restore()

        painter.setClipping(False)
        tm = ThemeManager.instance()
        if tm.is_dark:
            ring_color = QColor(90, 105, 140, 180)
        else:
            ring_color = QColor(160, 195, 228, 160)
        painter.setPen(QPen(ring_color, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), r, r)

        painter.setPen(QPen(QColor("#F5A623"), 1.8))
        painter.drawLine(int(cx - r * 0.55), int(cy), int(cx - r * 0.15), int(cy))
        painter.drawLine(int(cx + r * 0.15), int(cy), int(cx + r * 0.55), int(cy))
        painter.drawLine(int(cx), int(cy - r * 0.15), int(cx), int(cy + r * 0.15))


# ─────────────────────────────────────────────────────────────────────────────
# ScenicBackground
# ─────────────────────────────────────────────────────────────────────────────

class ScenicBackground(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._tm = ThemeManager.instance()
        self._tm.changed.connect(self._on_theme_changed)
        self._cache = None
        self._cache_size = None
        self._drone_img = None
        self._drone_scaled = None
        self._load_drone_image()

    def _on_theme_changed(self, _mode: str):
        self._cache = None
        self._drone_scaled = None
        self.update()

    def resizeEvent(self, event):
        self._cache = None
        self._drone_scaled = None
        super().resizeEvent(event)

    def paintEvent(self, event):
        sz = (self.width(), self.height())
        if self._cache is None or self._cache_size != sz:
            self._cache = self._render_background()
            self._cache_size = sz

        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._cache)

    def _render_background(self):
        w, h = self.width(), self.height()
        pix = QPixmap(w, h)
        pix.fill(Qt.transparent)
        painter = QPainter(pix)
        painter.setRenderHints(QPainter.Antialiasing)

        c = self._tm.colors()
        base = QLinearGradient(0, 0, w, h)
        base.setColorAt(0.0,  QColor(c["bg_start"]))
        base.setColorAt(0.36, QColor(c["bg_mid1"]))
        base.setColorAt(0.70, QColor(c["bg_mid2"]))
        base.setColorAt(1.0,  QColor(c["bg_end"]))
        painter.fillRect(0, 0, w, h, base)

        for (xf, yf, rf), (r, g, b, a) in zip(
            [(0.16, 0.18, 0.22), (0.84, 0.16, 0.18),
             (0.18, 0.86, 0.26), (0.84, 0.84, 0.24)],
            c["blob_colors"],
        ):
            self._blob(painter, w * xf, h * yf, w * rf, QColor(r, g, b, a))

        # 装饰无人机 + 探照灯
        self._draw_drone(painter, w, h)

        tm = ThemeManager.instance()
        if tm.is_dark:
            border_color = QColor(70, 76, 100, 100)
        else:
            border_color = QColor(255, 255, 255, 110)
        border_pen = QPen(border_color)
        border_pen.setWidth(2)
        painter.setPen(border_pen)
        painter.drawRoundedRect(QRectF(18, 18, w - 36, h - 36), 30, 30)

        painter.end()
        return pix

    def _load_drone_image(self):
        img_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "New Drone.png",
        )
        if os.path.exists(img_path):
            self._drone_img = QPixmap(img_path)
        else:
            self._drone_img = None

    def _get_drone_2d(self, target_w: int) -> QPixmap | None:
        """Return a scaled drone image (cached per target width)."""
        if self._drone_img is None:
            return None
        if self._drone_scaled is not None:
            cw, _ = self._drone_scaled
            if cw == target_w:
                return self._drone_scaled[1]
        scaled = self._drone_img.scaledToWidth(target_w, Qt.SmoothTransformation)
        self._drone_scaled = (target_w, scaled)
        return scaled

    def _draw_drone(self, painter, w, h):
        tm = ThemeManager.instance()
        drone_w = int(w * 0.55)
        drone_img = self._get_drone_2d(drone_w)

        if tm.is_dark:
            beam_bright = QColor(160, 210, 255, 90)
            beam_mid    = QColor(120, 180, 245, 45)
            beam_fade   = QColor(100, 160, 230, 0)
            glow_color  = QColor(120, 180, 245, 50)
            spot_color  = QColor(160, 210, 255, 40)
        else:
            beam_bright = QColor(180, 220, 255, 80)
            beam_mid    = QColor(150, 200, 245, 40)
            beam_fade   = QColor(130, 185, 235, 0)
            glow_color  = QColor(150, 200, 245, 42)
            spot_color  = QColor(180, 220, 255, 32)

        if drone_img is not None:
            dw = drone_img.width()
            dh = drone_img.height()
            drone_cx = w * 0.42
            drone_cy = h * 0.57
            drone_x = drone_cx - dw / 2
            drone_y = drone_cy - dh / 2

            beam_src_y = drone_y + dh * 0.65
            beam_src_x = drone_cx

            # Ground illumination spot (where searchlight hits)
            spot_cx = beam_src_x
            spot_cy = drone_y + dh + dh * 0.15
            spot = QRadialGradient(QPointF(spot_cx, spot_cy), dw * 0.9)
            spot.setColorAt(0.0, spot_color)
            spot.setColorAt(0.5, QColor(spot_color.red(), spot_color.green(),
                                        spot_color.blue(), int(spot_color.alpha() * 0.35)))
            spot.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(spot)
            painter.drawEllipse(QPointF(spot_cx, spot_cy), dw * 0.85, dw * 0.38)

            # Ambient glow around drone
            glow = QRadialGradient(QPointF(drone_cx, drone_cy), dw * 0.8)
            glow.setColorAt(0.0, glow_color)
            glow.setColorAt(0.55, QColor(glow_color.red(), glow_color.green(),
                                         glow_color.blue(), int(glow_color.alpha() * 0.25)))
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setBrush(glow)
            painter.drawEllipse(QPointF(drone_cx, drone_cy), dw * 0.78, dw * 0.78)

            # Searchlight beam cone from drone front downward
            beam = QPainterPath()
            beam_top_y = beam_src_y
            beam_bottom_y = spot_cy
            beam_half_w_top = dw * 0.08
            beam_half_w_bottom = dw * 0.45
            beam.moveTo(beam_src_x - beam_half_w_bottom, beam_bottom_y)
            beam.lineTo(beam_src_x - beam_half_w_top, beam_top_y)
            beam.lineTo(beam_src_x + beam_half_w_top, beam_top_y)
            beam.lineTo(beam_src_x + beam_half_w_bottom, beam_bottom_y)
            beam.closeSubpath()
            beam_grad = QLinearGradient(beam_src_x, beam_top_y, beam_src_x, beam_bottom_y)
            beam_grad.setColorAt(0.0, beam_bright)
            beam_grad.setColorAt(0.35, beam_mid)
            beam_grad.setColorAt(1.0, beam_fade)
            painter.setBrush(beam_grad)
            painter.drawPath(beam)

            # Inner bright core of the beam
            core = QPainterPath()
            core.moveTo(beam_src_x - beam_half_w_bottom * 0.52, beam_bottom_y)
            core.lineTo(beam_src_x - beam_half_w_top * 0.55, beam_top_y + dh * 0.02)
            core.lineTo(beam_src_x + beam_half_w_top * 0.55, beam_top_y + dh * 0.02)
            core.lineTo(beam_src_x + beam_half_w_bottom * 0.52, beam_bottom_y)
            core.closeSubpath()
            core_grad = QLinearGradient(beam_src_x, beam_top_y, beam_src_x, beam_bottom_y)
            bright_center = QColor(
                min(255, beam_bright.red() + 40),
                min(255, beam_bright.green() + 30),
                min(255, beam_bright.blue() + 20),
                int(beam_bright.alpha() * 0.7),
            )
            core_grad.setColorAt(0.0, bright_center)
            core_grad.setColorAt(1.0, beam_fade)
            painter.setBrush(core_grad)
            painter.drawPath(core)

            # Draw the 2D drone image
            painter.drawPixmap(int(drone_x), int(drone_y), drone_img)
        else:
            # Fallback: drawn silhouette
            self._draw_drone_fallback(painter, w, h)

    def _draw_drone_fallback(self, painter, w, h):
        """Fallback geometric drone silhouette when image is not available."""
        tm = ThemeManager.instance()
        if tm.is_dark:
            beam_center = QColor(120, 175, 240, 70)
            beam_edge   = QColor(120, 175, 240, 0)
            glow_color  = QColor(100, 160, 230, 55)
            body_color  = QColor(170, 205, 245, 75)
            arm_color   = QColor(150, 190, 240, 60)
            rotor_color = QColor(170, 200, 245, 68)
        else:
            beam_center = QColor(140, 190, 235, 55)
            beam_edge   = QColor(140, 190, 235, 0)
            glow_color  = QColor(130, 185, 230, 40)
            body_color  = QColor(160, 200, 235, 55)
            arm_color   = QColor(145, 190, 230, 42)
            rotor_color = QColor(160, 195, 235, 48)

        cx = w * 0.42
        cy = h * 0.58
        arm_len = min(w, h) * 0.10
        body_r  = arm_len * 0.24
        rotor_r = arm_len * 0.20

        glow = QRadialGradient(QPointF(cx, cy + body_r + arm_len * 0.4), arm_len * 1.6)
        glow.setColorAt(0.0, glow_color)
        glow.setColorAt(0.6, QColor(glow_color.red(), glow_color.green(),
                                     glow_color.blue(), int(glow_color.alpha() * 0.3)))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(cx, cy + body_r + arm_len * 0.6),
                            arm_len * 1.5, arm_len * 1.8)

        beam = QPainterPath()
        beam_top_y = cy + body_r
        beam.moveTo(cx - arm_len * 0.55, cy + body_r + arm_len * 1.6)
        beam.lineTo(cx - arm_len * 0.06, beam_top_y)
        beam.lineTo(cx + arm_len * 0.06, beam_top_y)
        beam.lineTo(cx + arm_len * 0.55, cy + body_r + arm_len * 1.6)
        beam.closeSubpath()
        beam_grad = QLinearGradient(cx, beam_top_y, cx, beam_top_y + arm_len * 1.6)
        beam_grad.setColorAt(0.0, beam_center)
        beam_grad.setColorAt(0.3, QColor(beam_center.red(), beam_center.green(),
                                          beam_center.blue(), int(beam_center.alpha() * 0.6)))
        beam_grad.setColorAt(1.0, beam_edge)
        painter.setBrush(beam_grad)
        painter.drawPath(beam)

        arm_angles = [42, 138, 222, 318]
        rotor_positions = []
        for angle_deg in arm_angles:
            rad_angle = math.radians(angle_deg)
            ex = cx + arm_len * math.cos(rad_angle)
            ey = cy + arm_len * math.sin(rad_angle)
            rotor_positions.append((ex, ey))
            pen = QPen(arm_color)
            pen.setWidthF(arm_len * 0.14)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawLine(QPointF(cx, cy), QPointF(ex, ey))

        painter.setPen(Qt.NoPen)
        painter.setBrush(body_color)
        painter.drawEllipse(QPointF(cx, cy), body_r, body_r)

        for (ex, ey) in rotor_positions:
            painter.setBrush(rotor_color)
            painter.drawEllipse(QPointF(ex, ey), rotor_r, rotor_r)

    def _blob(self, painter, x, y, radius, color):
        gradient = QRadialGradient(QPointF(x, y), radius)
        transparent = QColor(color)
        transparent.setAlpha(0)
        gradient.setColorAt(0.0, color)
        gradient.setColorAt(
            0.60, QColor(color.red(), color.green(), color.blue(),
                         int(color.alpha() * 0.25))
        )
        gradient.setColorAt(1.0, transparent)
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(QPointF(x, y), radius, radius)


# ─────────────────────────────────────────────────────────────────────────────
# GlassCard base
# ─────────────────────────────────────────────────────────────────────────────

class GlassCard(SimpleCardWidget):
    COLORS = {
        "good":    "#1FB96A",
        "warning": "#FFB340",
        "danger":  "#F06767",
        "normal":  "#8BA1BF",
        "info":    "#53A8FF",
        "accent":  "#4D8CFF",
    }

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self._current_accent  = QColor(self.COLORS["normal"])
        self._accent_timer    = QTimer(self)
        self._accent_timer.timeout.connect(self._pulse_accent)
        self._accent_stop_timer = QTimer(self)
        self._accent_stop_timer.setSingleShot(True)
        self._accent_stop_timer.timeout.connect(self._finish_pulse)
        self._flash_colors  = (QColor(self.COLORS["normal"]), QColor(self.COLORS["normal"]))
        self._flash_index   = 0
        self._settle_accent = QColor(self.COLORS["normal"])

        self._tm = ThemeManager.instance()
        self._tm.changed.connect(self._on_theme_changed)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 10)
        if self._tm.is_dark:
            shadow.setColor(QColor(10, 12, 20, 120))
        else:
            shadow.setColor(QColor(90, 130, 185, 42))
        self.setGraphicsEffect(shadow)

    def _on_theme_changed(self, _mode: str):
        shadow = self.graphicsEffect()
        if isinstance(shadow, QGraphicsDropShadowEffect):
            if self._tm.is_dark:
                shadow.setColor(QColor(10, 12, 20, 120))
            else:
                shadow.setColor(QColor(90, 130, 185, 42))
        self._update_style()

    def set_accent(self, color: str, preserve_flash: bool = False):
        new_color = QColor(color)
        if preserve_flash and self._accent_timer.isActive():
            self._settle_accent = new_color
            return
        self._accent_timer.stop()
        self._accent_stop_timer.stop()
        if self._current_accent == new_color and self._settle_accent == new_color:
            return
        self._current_accent = new_color
        self._settle_accent  = new_color
        self._update_style()

    def flash_accent(self, color_a: str, color_b: str, duration_ms: int,
                     settle_color: str, interval_ms: int = 150):
        self._flash_colors  = (QColor(color_a), QColor(color_b))
        self._flash_index   = 0
        self._settle_accent = QColor(settle_color)
        self._current_accent = self._flash_colors[0]
        self._accent_timer.start(interval_ms)
        self._accent_stop_timer.start(duration_ms)
        self._update_style()

    def _pulse_accent(self):
        self._flash_index    = 1 - self._flash_index
        self._current_accent = self._flash_colors[self._flash_index]
        self._update_style()

    def _finish_pulse(self):
        self._accent_timer.stop()
        self._current_accent = self._settle_accent
        self._update_style()

    def _update_style(self):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# MetricCard
# ─────────────────────────────────────────────────────────────────────────────

class MetricCard(GlassCard):
    def __init__(self, title: str, helper: str, parent=None,
                 gauge_max: Optional[float] = None, gauge_color: str = "#4D8CFF"):
        super().__init__("metricCard", parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)
        self.dot         = StatusDot(self.COLORS["normal"], 10, parent=self)
        self.title_label = CaptionLabel(title, self)
        self.title_label.setObjectName("cardTitle")
        header.addWidget(self.dot, 0, Qt.AlignVCenter)
        header.addWidget(self.title_label, 0, Qt.AlignVCenter)
        header.addStretch(1)

        self._gauge: Optional[CircularGauge] = None
        if gauge_max is not None:
            self._gauge = CircularGauge(0.0, gauge_max, 52, gauge_color, self)
            header.addWidget(self._gauge, 0, Qt.AlignVCenter)

        self.value_label = TitleLabel("--", self)
        self.value_label.setObjectName("cardValue")

        layout.addLayout(header)
        layout.addWidget(self.value_label)
        self.setMinimumHeight(90)
        self._update_style()

    def _update_style(self):
        accent = self._current_accent.name()
        c = self._tm.colors()
        self.setStyleSheet(f"""
            #metricCard {{
                background-color: {c["card_bg"]};
                border: 1px solid {c["card_border"]};
                border-top: 3px solid {accent};
                border-radius: 16px;
            }}
            #cardTitle  {{ color: {c["text_secondary"]}; font-size: 11px; letter-spacing: 0.5px; }}
            #cardValue  {{ color: {c["text_primary"]}; font-size: 14px; }}
        """)

    def set_state(self, value: str, helper: str, level: str = "normal"):
        accent = self.COLORS.get(level, self.COLORS["normal"])
        self.set_accent(accent, preserve_flash=True)
        self.dot.set_color(accent, preserve_blink=True)
        self.value_label.setText(value)
        if self._gauge:
            self._gauge.set_color(accent)

    def set_gauge_value(self, value: float, text: str = ""):
        if self._gauge:
            self._gauge.set_value(value, text)

    def blink_indicator(self, flash_a: str, flash_b: str, duration_ms: int,
                        settle_level: str):
        settle_color = self.COLORS.get(settle_level, self.COLORS["normal"])
        self.dot.blink(flash_a, flash_b, duration_ms, settle_color, interval_ms=100)
        self.flash_accent(flash_a, flash_b, duration_ms, settle_color, interval_ms=100)


# ─────────────────────────────────────────────────────────────────────────────
# AltitudeCard
# ─────────────────────────────────────────────────────────────────────────────

class AltitudeCard(GlassCard):
    def __init__(self, parent=None):
        super().__init__("metricCard", parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)
        self.dot = StatusDot(self.COLORS["normal"], 10, parent=self)
        title    = CaptionLabel("高度 & 姿态", self)
        title.setObjectName("cardTitle")
        header.addWidget(self.dot, 0, Qt.AlignVCenter)
        header.addWidget(title, 0, Qt.AlignVCenter)
        header.addStretch(1)

        self.attitude_widget = AttitudeWidget(52, self)
        header.addWidget(self.attitude_widget, 0, Qt.AlignVCenter)

        self.value_label  = TitleLabel("0.00 m", self)
        self.value_label.setObjectName("cardValue")

        layout.addLayout(header)
        layout.addWidget(self.value_label)
        self.setMinimumHeight(90)
        self._update_style()

    def _update_style(self):
        accent = self._current_accent.name()
        c = self._tm.colors()
        self.setStyleSheet(f"""
            #metricCard {{
                background-color: {c["card_bg"]};
                border: 1px solid {c["card_border"]};
                border-top: 3px solid {accent};
                border-radius: 16px;
            }}
            #cardTitle  {{ color: {c["text_secondary"]}; font-size: 11px; letter-spacing: 0.5px; }}
            #cardValue  {{ color: {c["text_primary"]}; font-size: 14px; }}
        """)

    def set_state(self, alt_m: float, roll_deg: float, pitch_deg: float):
        self.attitude_widget.set_attitude(roll_deg, pitch_deg)
        self.value_label.setText(f"{alt_m:.2f} m")
        accent = self.COLORS["info"]
        self.set_accent(accent)
        self.dot.set_color(accent)


# ─────────────────────────────────────────────────────────────────────────────
# FlightTimeCard
# ─────────────────────────────────────────────────────────────────────────────

class FlightTimeCard(GlassCard):
    def __init__(self, parent=None):
        super().__init__("metricCard", parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)
        self.dot = StatusDot(self.COLORS["info"], 10, parent=self)
        title    = CaptionLabel("飞行时间", self)
        title.setObjectName("cardTitle")
        header.addWidget(self.dot, 0, Qt.AlignVCenter)
        header.addWidget(title, 0, Qt.AlignVCenter)
        header.addStretch(1)

        self.value_label  = TitleLabel("00:00:00", self)
        self.value_label.setObjectName("cardValue")

        layout.addLayout(header)
        layout.addWidget(self.value_label)
        self.setMinimumHeight(90)
        self._update_style()

    def _update_style(self):
        accent = self._current_accent.name()
        c = self._tm.colors()
        self.setStyleSheet(f"""
            #metricCard {{
                background-color: {c["card_bg"]};
                border: 1px solid {c["card_border"]};
                border-top: 3px solid {accent};
                border-radius: 16px;
            }}
            #cardTitle  {{ color: {c["text_secondary"]}; font-size: 11px; letter-spacing: 0.5px; }}
            #cardValue  {{ color: {c["text_primary"]}; font-size: 14px; }}
        """)

    def set_flight_time(self, seconds: float):
        s = max(0.0, seconds)
        h = int(s) // 3600
        m = (int(s) % 3600) // 60
        sec = int(s) % 60
        self.value_label.setText(f"{h:02d}:{m:02d}:{sec:02d}")
        accent = self.COLORS["info"] if s > 0 else self.COLORS["normal"]
        self.set_accent(accent)
        self.dot.set_color(accent)


# ─────────────────────────────────────────────────────────────────────────────
# PanelCard base
# ─────────────────────────────────────────────────────────────────────────────

class PanelCard(GlassCard):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__("panelCard", parent)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(20, 18, 20, 18)
        self.root.setSpacing(12)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.title_dot   = StatusDot(self.COLORS["normal"], 10, self)
        self.title_label = StrongBodyLabel(title, self)
        self.title_label.setObjectName("panelTitle")
        title_row.addWidget(self.title_dot, 0, Qt.AlignVCenter)
        title_row.addWidget(self.title_label, 0, Qt.AlignVCenter)
        title_row.addStretch(1)

        self.subtitle_label = CaptionLabel(subtitle, self)
        self.subtitle_label.setObjectName("panelSubtitle")
        self.subtitle_label.setVisible(bool(subtitle))

        self.root.addLayout(title_row)
        if subtitle:
            self.root.addWidget(self.subtitle_label)

        self._update_style()

    def _update_style(self):
        accent = self._current_accent.name()
        c = self._tm.colors()
        self.setStyleSheet(f"""
            #panelCard {{
                background-color: {c["panel_bg"]};
                border: 1px solid {c["panel_border"]};
                border-top: 3px solid {accent};
                border-radius: 24px;
            }}
            #panelTitle    {{ color: {c["text_primary"]}; font-size: 16px; }}
            #panelSubtitle {{ color: #7087A7; font-size: 12px; }}
        """)

    def set_panel_accent(self, level: str):
        accent = self.COLORS.get(level, self.COLORS["normal"])
        self.set_accent(accent)
        self.title_dot.set_color(accent)

    def flash_panel(self, color_a: str, color_b: str, duration_ms: int,
                    settle_level: str):
        settle_color = self.COLORS.get(settle_level, self.COLORS["normal"])
        self.title_dot.blink(color_a, color_b, duration_ms, settle_color, interval_ms=120)
        self.flash_accent(color_a, color_b, duration_ms, settle_color, interval_ms=120)
