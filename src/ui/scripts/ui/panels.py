"""面板级控件：导航栏 · HeroHeader · 模式面板 · 电机面板 · 警报面板 · 动作面板。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QLinearGradient
from PyQt5.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QProgressBar, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from qfluentwidgets import (
    BodyLabel, CaptionLabel, PrimaryPushButton, StrongBodyLabel,
    SubtitleLabel, TitleLabel, TransparentPushButton,
)

from ui.constants import MODE_ALIAS_MAP, MODE_OPTIONS
from ui.theme import ThemeManager
from ui.widgets import GlassCard, PanelCard, StatusDot


# ─────────────────────────────────────────────────────────────────────────────
# NavigationSidebar
# ─────────────────────────────────────────────────────────────────────────────

class _NavItem(QWidget):
    clicked_signal = pyqtSignal(str)

    def __init__(self, icon: str, label: str, key: str, parent=None):
        super().__init__(parent)
        self.setObjectName("navItem")
        self._key      = key
        self._selected = False
        self._tm = ThemeManager.instance()
        self._tm.changed.connect(self._refresh)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 10, 4, 10)
        lay.setSpacing(4)
        lay.setAlignment(Qt.AlignCenter)

        self._icon_lbl = QLabel(icon, self)
        self._icon_lbl.setAlignment(Qt.AlignCenter)
        self._text_lbl = QLabel(label, self)
        self._text_lbl.setAlignment(Qt.AlignCenter)

        lay.addWidget(self._icon_lbl)
        lay.addWidget(self._text_lbl)
        self.setFixedHeight(68)
        self._refresh()

    def set_selected(self, selected: bool):
        self._selected = selected
        self._refresh()

    def _refresh(self, _mode: str = ""):
        c = self._tm.colors()
        if self._selected:
            bg, fg = "rgba(77,140,255,0.13)", "#3B7FF5"
        else:
            bg, fg = "transparent", (c["text_secondary"] if self._tm.is_dark else "#5B7499")
        self.setStyleSheet(
            f"QWidget#navItem {{ background:{bg}; border-radius:14px; }}"
        )
        self._icon_lbl.setStyleSheet(
            f"font-size:18px; background:transparent; color:{fg};"
        )
        weight = "600" if self._selected else "400"
        self._text_lbl.setStyleSheet(
            f"font-size:11px; color:{fg}; background:transparent; font-weight:{weight};"
        )

    def mousePressEvent(self, event):
        self.clicked_signal.emit(self._key)


class NavigationSidebar(QWidget):
    page_changed = pyqtSignal(str)

    _ITEMS = [
        ("⌂",  "概览",    "overview"),
        ("✈",  "飞行控制", "flight"),
        ("◎",  "航点规划", "waypoint"),
        ("↗",  "数据分析", "data"),
        ("⚙",  "系统设置", "settings"),
        ("☰",  "日志管理", "logs"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(88)
        self._items: List[_NavItem] = []
        self._tm = ThemeManager.instance()
        self._tm.changed.connect(self._on_theme_changed)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 14, 8, 14)
        root.setSpacing(4)

        logo = QLabel("WS", self)
        logo.setFixedSize(46, 46)
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("""
            background: qlineargradient(
                x1:0,y1:0,x2:1,y2:1,
                stop:0 #4D8CFF, stop:1 #1FB96A
            );
            color: white;
            font-size: 15px;
            font-weight: bold;
            border-radius: 13px;
        """)
        root.addWidget(logo, 0, Qt.AlignHCenter)
        root.addSpacing(10)

        for icon, label, key in self._ITEMS:
            item = _NavItem(icon, label, key, self)
            item.clicked_signal.connect(self._on_clicked)
            self._items.append(item)
            root.addWidget(item)

        root.addStretch(1)

        debug = _NavItem("◈", "调试", "debug", self)
        debug.clicked_signal.connect(self._on_clicked)
        self._items.append(debug)
        root.addWidget(debug)

        self._select("overview")
        self._apply_sidebar_style()

    def _apply_sidebar_style(self):
        c = self._tm.colors()
        self.setStyleSheet(f"""
            NavigationSidebar {{
                background-color: {c["sidebar_bg"]};
                border: 1px solid {c["panel_border"]};
                border-radius: 24px;
            }}
        """)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        if self._tm.is_dark:
            shadow.setColor(QColor(10, 12, 20, 110))
        else:
            shadow.setColor(QColor(90, 130, 185, 38))
        self.setGraphicsEffect(shadow)

    def _on_theme_changed(self, _mode: str):
        self._apply_sidebar_style()
        for item in self._items:
            item._refresh()

    def _on_clicked(self, key: str):
        self._select(key)
        self.page_changed.emit(key)

    def _select(self, key: str):
        for item in self._items:
            item.set_selected(item._key == key)


# ─────────────────────────────────────────────────────────────────────────────
# HeroHeader
# ─────────────────────────────────────────────────────────────────────────────

class HeroHeader(GlassCard):
    """顶部状态栏 — 链路状态 + 实时时钟 + 主题切换。"""

    reboot_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("heroHeader", parent)
        self.set_accent(self.COLORS["accent"])
        self.setFixedHeight(52)

        root = QHBoxLayout(self)
        root.setContentsMargins(18, 6, 18, 6)
        root.setSpacing(10)

        # 链路状态
        self.live_dot  = StatusDot(self.COLORS["normal"], 12, self)
        self.live_text = StrongBodyLabel("LINK OFFLINE", self)
        self.live_text.setObjectName("heroLive")
        root.addWidget(self.live_dot, 0, Qt.AlignVCenter)
        root.addWidget(self.live_text, 0, Qt.AlignVCenter)

        # 重启飞控按钮
        self.reboot_btn = TransparentPushButton("↻ 重启飞控", self)
        self.reboot_btn.setObjectName("rebootBtn")
        self.reboot_btn.clicked.connect(self._on_reboot_clicked)
        root.addWidget(self.reboot_btn, 0, Qt.AlignVCenter)

        root.addStretch(1)

        # 实时时钟
        self._time_lbl = QLabel("--:--:--", self)
        self._time_lbl.setObjectName("heroTime")
        self._date_lbl = QLabel("----/--/--", self)
        self._date_lbl.setObjectName("heroDate")
        root.addWidget(self._date_lbl, 0, Qt.AlignVCenter)
        root.addWidget(self._time_lbl, 0, Qt.AlignVCenter)

        # 主题切换
        self._theme_btn = TransparentPushButton("☀ 浅色", self)
        self._theme_btn.setObjectName("themeBtn")
        self._theme_btn.clicked.connect(self._toggle_theme)
        root.addWidget(self._theme_btn, 0, Qt.AlignVCenter)

        self._tm = ThemeManager.instance()
        self._tm.changed.connect(self._on_theme_changed_external)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start(1000)
        self._tick_clock()

        self._update_style()

    def _on_reboot_clicked(self):
        self.reboot_requested.emit()

    def _toggle_theme(self):
        self._tm.toggle()

    def _on_theme_changed_external(self, mode: str):
        label = "☀ 浅色" if mode == "dark" else "☾ 深色"
        self._theme_btn.setText(label)

    def _tick_clock(self):
        now = datetime.now()
        self._time_lbl.setText(now.strftime("%H:%M:%S"))
        self._date_lbl.setText(now.strftime("%Y/%m/%d"))

    def _update_style(self):
        accent = self._current_accent.name()
        c = self._tm.colors()
        self.setStyleSheet(f"""
            #heroHeader {{
                background-color: {c["sidebar_bg"]};
                border: 1px solid {c["panel_border"]};
                border-top: 3px solid {accent};
                border-radius: 18px;
            }}
            #heroLive    {{ color: {c["text_primary"]}; font-size: 14px; }}
            #heroTime    {{
                color: {c["text_primary"]};
                font-size: 18px;
                font-weight: 600;
                letter-spacing: 1px;
            }}
            #heroDate    {{ color: {c["text_secondary"]}; font-size: 12px; margin-right: 8px; }}
            #themeBtn    {{ color: {c["text_secondary"]}; font-size: 13px; }}
            #rebootBtn   {{ color: {c["text_secondary"]}; font-size: 12px; }}
        """)

    def set_link_state(self, text: str, color: str):
        self.live_text.setText(text)
        self.live_dot.set_color(color)
        self.set_accent(color)


# ─────────────────────────────────────────────────────────────────────────────
# FlightModePanel
# ─────────────────────────────────────────────────────────────────────────────

class _ModeButton(QPushButton):
    def __init__(self, label: str, mode: str, parent=None):
        super().__init__(label, parent)
        self._mode = mode
        self._tm = ThemeManager.instance()
        self._tm.changed.connect(lambda _: self.set_selected(self._selected))
        self.setFixedHeight(40)
        self.set_selected(False)

    @property
    def mode(self) -> str:
        return self._mode

    def set_selected(self, selected: bool):
        self._selected = selected
        c = self._tm.colors()
        if selected:
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: rgba(31,185,106,0.12);
                    border: 2px solid #1FB96A;
                    border-radius: 12px;
                    color: #0E8C52;
                    font-size: 13px;
                    font-weight: 600;
                    letter-spacing: 0.4px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {c["card_bg"]};
                    border: 1px solid {c["card_border"]};
                    border-radius: 12px;
                    color: {c["text_primary"]};
                    font-size: 13px;
                }}
                QPushButton:hover {{
                    background-color: {c["panel_bg"]};
                    border: 1px solid #92BDFB;
                    color: {c["text_primary"]};
                }}
                QPushButton:pressed {{
                    background-color: rgba(180,215,255,0.72);
                }}
            """)


class FlightModePanel(PanelCard):
    def __init__(self, parent=None):
        super().__init__("模式切换", "", parent)
        self.set_panel_accent("accent")

        grid_wrap = QWidget(self)
        self._btn_grid = QGridLayout(grid_wrap)
        self._btn_grid.setContentsMargins(0, 0, 0, 0)
        self._btn_grid.setHorizontalSpacing(6)
        self._btn_grid.setVerticalSpacing(6)

        self._mode_buttons: List[_ModeButton] = []
        self._selected_mode_key: Optional[str] = None

        for idx, (label, mode) in enumerate(MODE_OPTIONS):
            btn = _ModeButton(label, mode, self)
            btn.clicked.connect(lambda _, b=btn: self._on_btn_clicked(b))
            r, c = divmod(idx, 3)
            self._btn_grid.addWidget(btn, r, c)
            self._mode_buttons.append(btn)

        self.apply_button = PrimaryPushButton("  ✈  发送模式", self)
        self.apply_button.setMinimumHeight(44)

        self.feedback_label = CaptionLabel("先选中目标模式，再发送。", self)
        self.feedback_label.setObjectName("modeFeedback")

        self.root.addWidget(grid_wrap)
        self.root.addWidget(self.apply_button)
        self.root.addWidget(self.feedback_label)
        self.root.addStretch(1)

        self.setMinimumHeight(240)
        self.setMinimumWidth(320)

        c = self._tm.colors()
        self.setStyleSheet(self.styleSheet() + f"""
            #modeFeedback {{ color: #607A9B; font-size: 12px; }}
        """)

    def _on_btn_clicked(self, btn: _ModeButton):
        self._selected_mode_key = btn.mode
        for b in self._mode_buttons:
            b.set_selected(b is btn)

    def set_current_mode(self, mode: str):
        display = MODE_ALIAS_MAP.get(mode, mode)
        if self._selected_mode_key is None:
            for btn in self._mode_buttons:
                if btn.mode in {mode, display}:
                    btn.set_selected(True)
                    self._selected_mode_key = btn.mode
                    break

    def selected_mode(self) -> Optional[str]:
        return self._selected_mode_key

    def set_feedback(self, text: str, success: bool):
        color = "#1FB96A" if success else "#F06767"
        self.feedback_label.setStyleSheet(f"color: {color};")
        self.feedback_label.setText(text)


# ─────────────────────────────────────────────────────────────────────────────
# ActionPanel — 解锁 / 上锁 / 急停
# ─────────────────────────────────────────────────────────────────────────────

class ActionPanel(PanelCard):
    """紧急动作面板：解锁、上锁、急停、一键降落。"""

    arm_requested   = pyqtSignal()
    disarm_requested = pyqtSignal()
    emergency_stop_requested = pyqtSignal()
    land_requested  = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("动作指令", "", parent)
        self.set_panel_accent("danger")

        btn_row1 = QHBoxLayout()
        btn_row1.setSpacing(6)

        self.arm_btn = PrimaryPushButton("⚡ 解锁", self)
        self.arm_btn.setObjectName("armBtn")
        self.arm_btn.setMinimumHeight(30)
        self.arm_btn.clicked.connect(self.arm_requested.emit)

        self.disarm_btn = PrimaryPushButton("🔒 上锁", self)
        self.disarm_btn.setObjectName("disarmBtn")
        self.disarm_btn.setMinimumHeight(30)
        self.disarm_btn.clicked.connect(self.disarm_requested.emit)

        btn_row1.addWidget(self.arm_btn, 1)
        btn_row1.addWidget(self.disarm_btn, 1)

        btn_row2 = QHBoxLayout()
        btn_row2.setSpacing(6)

        self.estop_btn = PrimaryPushButton("⛔ 急停", self)
        self.estop_btn.setObjectName("estopBtn")
        self.estop_btn.setMinimumHeight(30)
        self.estop_btn.clicked.connect(self.emergency_stop_requested.emit)

        self.land_btn = PrimaryPushButton("⬇ LAND", self)
        self.land_btn.setObjectName("landBtn")
        self.land_btn.setMinimumHeight(30)
        self.land_btn.clicked.connect(self.land_requested.emit)

        btn_row2.addWidget(self.estop_btn, 1)
        btn_row2.addWidget(self.land_btn, 1)

        self.action_feedback = CaptionLabel("就绪，等待指令。", self)
        self.action_feedback.setObjectName("actionFeedback")

        self.root.addLayout(btn_row1)
        self.root.addLayout(btn_row2)
        self.root.addWidget(self.action_feedback)
        self.root.addStretch(1)

        self.setMinimumHeight(120)
        self._apply_btn_styles()

    def _apply_btn_styles(self):
        self.setStyleSheet(self.styleSheet() + """
            #armBtn {
                background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #1FB96A, stop:1 #28D17C);
                color: white; font-size: 12px; font-weight: 600;
                border-radius: 10px;
            }
            #armBtn:hover {
                background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #22C572, stop:1 #32E088);
            }
            #disarmBtn {
                background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #FFB340, stop:1 #F0A030);
                color: white; font-size: 12px; font-weight: 600;
                border-radius: 10px;
            }
            #disarmBtn:hover {
                background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #FFC060, stop:1 #F5B040);
            }
            #estopBtn {
                background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #F06767, stop:1 #E05050);
                color: white; font-size: 12px; font-weight: 600;
                border-radius: 10px;
            }
            #estopBtn:hover {
                background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #F57A7A, stop:1 #E86060);
            }
            #landBtn {
                background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #53A8FF, stop:1 #3D8FED);
                color: white; font-size: 12px; font-weight: 600;
                border-radius: 10px;
            }
            #landBtn:hover {
                background-color: qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #6AB8FF, stop:1 #50A0F5);
            }
            #actionFeedback { color: #607A9B; font-size: 11px; }
        """)

    def set_feedback(self, text: str, success: bool):
        color = "#1FB96A" if success else "#F06767"
        self.action_feedback.setStyleSheet(f"color: {color};")
        self.action_feedback.setText(text)


# ─────────────────────────────────────────────────────────────────────────────
# MotorPanel
# ─────────────────────────────────────────────────────────────────────────────

class MotorPanel(PanelCard):
    _PWM_MIN = 1000
    _PWM_MAX = 2000

    _ZONE_COLORS = {
        "green":  ("#1FB96A", "#28D17C"),
        "yellow": ("#F0A030", "#FFB340"),
        "red":    ("#E05050", "#F06767"),
    }

    def __init__(self, parent=None):
        super().__init__("电机转速", "四路 RPM 实时监控", parent)
        self.set_panel_accent("info")
        self.setObjectName("motorPanelCard")

        self._tm = ThemeManager.instance()
        self._bar_bg = ""
        self._bar_color = ""
        self._panel_bg = ""

        self.bars = []
        self.labels = []
        for index in range(4):
            row = QHBoxLayout()
            row.setSpacing(10)
            label = CaptionLabel(f"M{index + 1}", self)
            label.setFixedWidth(30)
            bar = QProgressBar(self)
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFormat("0 rpm")
            bar.setTextVisible(True)
            bar.setToolTip("等待 ESC 遥测或 PWM 数据...")
            row.addWidget(label)
            row.addWidget(bar, 1)
            self.root.addLayout(row)
            self.bars.append(bar)
            self.labels.append(label)

        self._last_zone = ["", "", "", ""]
        self._apply_base_style()

    def _apply_base_style(self):
        if self._tm.is_dark:
            self._panel_bg = "rgba(26, 30, 50, 0.55)"
            self._bar_bg = "rgba(40, 48, 72, 0.70)"
            self._bar_color = "#D0D8E8"
        else:
            self._panel_bg = "rgba(255, 255, 255, 0.48)"
            self._bar_bg = "rgba(243, 248, 255, 0.78)"
            self._bar_color = "#1A2B45"
        self.setStyleSheet(self.styleSheet() + f"""
            #motorPanelCard {{
                background-color: {self._panel_bg};
            }}
            QProgressBar {{
                min-height: 22px;
                background-color: {self._bar_bg};
                border: 1px solid rgba(163,190,227,0.58);
                border-radius: 10px;
                text-align: center;
                color: {self._bar_color};
                font-size: 11px;
                font-weight: 600;
            }}
        """)

    def _on_theme_changed(self, _mode: str):
        super()._on_theme_changed(_mode)
        self._apply_base_style()
        self._last_zone = ["", "", "", ""]

    @staticmethod
    def _pwm_zone(pwm: float):
        if pwm <= 1.0:
            return None
        if pwm < 1300:
            return "green"
        if pwm < 1550:
            return "yellow"
        return "red"

    @staticmethod
    def _rpm_zone(rpm: int):
        if rpm <= 0:
            return None
        if rpm < 3000:
            return "green"
        if rpm < 5500:
            return "yellow"
        return "red"

    def _bar_chunk_style(self, zone: str):
        c1, c2 = self._ZONE_COLORS[zone]
        return f"""
            QProgressBar::chunk {{
                background-color: qlineargradient(
                    x1:0,y1:0,x2:1,y2:0,
                    stop:0 {c1}, stop:1 {c2}
                );
                border-radius: 9px;
            }}
        """

    def _bar_full_style(self, zone: str):
        return f"""
            QProgressBar {{
                min-height: 22px;
                background-color: {self._bar_bg};
                border: 1px solid rgba(163,190,227,0.58);
                border-radius: 10px;
                text-align: center;
                color: {self._bar_color};
                font-size: 11px;
                font-weight: 600;
            }}
            {self._bar_chunk_style(zone)}
        """

    def set_motor_rpms(self, rpms: List[int], has_esc: bool = False,
                       outputs: Optional[List[float]] = None):
        if outputs is None:
            outputs = [0.0, 0.0, 0.0, 0.0]
        values = list(rpms[:4]) + [0] * max(0, 4 - len(rpms))
        pwms = list(outputs[:4]) + [0.0] * max(0, 4 - len(outputs))

        for i, (bar, rpm, pwm) in enumerate(zip(self.bars, values[:4], pwms[:4])):
            if has_esc and rpm > 0:
                pct = max(0, min(100, int(rpm / 80)))
                bar.setValue(pct)
                bar.setFormat(f"{rpm} rpm")
                zone = self._rpm_zone(rpm)
            elif pwm > 1.0:
                pwm_int = int(pwm)
                pct = max(0, min(100, int((pwm - self._PWM_MIN) / (self._PWM_MAX - self._PWM_MIN) * 100)))
                bar.setValue(pct)
                bar.setFormat(f"{pwm_int} µs")
                zone = self._pwm_zone(pwm)
            else:
                bar.setValue(0)
                bar.setFormat("--")
                zone = None

            if zone and zone != self._last_zone[i]:
                self._last_zone[i] = zone
                bar.setStyleSheet(self._bar_full_style(zone))


# ─────────────────────────────────────────────────────────────────────────────
# AlertPanel
# ─────────────────────────────────────────────────────────────────────────────

class AlertPanel(PanelCard):
    def __init__(self, parent=None):
        super().__init__("地面站警报", "新警报到达时会闪烁提示", parent)
        self.set_panel_accent("warning")

        self.list_widget = QListWidget(self)
        self.list_widget.setAlternatingRowColors(True)
        self.root.addWidget(self.list_widget, 1)

        self.setStyleSheet(self.styleSheet() + """
            QListWidget {
                color: #21324A;
                background-color: rgba(247,250,255,0.86);
                border: 1px solid rgba(167,191,224,0.58);
                border-radius: 16px; padding: 6px;
            }
            QListWidget::item {
                padding: 9px 10px;
                border-bottom: 1px solid rgba(156,182,214,0.22);
            }
            QListWidget::item:selected {
                background-color: rgba(215,232,255,0.80);
            }
        """)

    def add_alert(self, timestamp: str, severity: str, text: str):
        item = QListWidgetItem(f"[{timestamp}]  {text}")
        color_map = {
            "ERROR": QColor("#E15959"),
            "WARN":  QColor("#C97D12"),
            "INFO":  QColor("#3777E8"),
        }
        flash_map = {
            "ERROR": ("#F06767", "#FFE3E3", "danger"),
            "WARN":  ("#FFB340", "#FFF1D5", "warning"),
            "INFO":  ("#53A8FF", "#E2F1FF", "info"),
        }
        item.setForeground(color_map.get(severity, QColor("#2B3F5C")))
        self.list_widget.insertItem(0, item)
        fa, fb, settle = flash_map.get(severity, ("#53A8FF", "#E2F1FF", "info"))
        self.flash_panel(fa, fb, 3200, settle)
        while self.list_widget.count() > 50:
            self.list_widget.takeItem(self.list_widget.count() - 1)


# ─────────────────────────────────────────────────────────────────────────────
# ReservePanel (RViz placeholder)
# ─────────────────────────────────────────────────────────────────────────────

class ReservePanel(PanelCard):
    def __init__(self, parent=None):
        super().__init__("3D 视图", "", parent)
        self.set_panel_accent("info")
        self.setObjectName("reservePanelCard")

        self.placeholder = QLabel(self)
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setObjectName("reserveLabel")
        self.placeholder.setMinimumHeight(200)
        self.root.addWidget(self.placeholder, 1)

        self._apply_reserve_style()

    def _apply_reserve_style(self):
        tm = ThemeManager.instance()
        if tm.is_dark:
            panel_bg = "rgba(26, 30, 50, 0.42)"
            label_bg = "rgba(30, 36, 58, 0.38)"
            border_c = "rgba(80, 100, 150, 0.30)"
        else:
            panel_bg = "rgba(255, 255, 255, 0.35)"
            label_bg = "transparent"
            border_c = "rgba(129, 165, 210, 0.38)"
        self.setStyleSheet(self.styleSheet() + f"""
            #reservePanelCard {{
                background-color: {panel_bg};
            }}
            #reserveLabel {{
                border: 1px dashed {border_c};
                border-radius: 20px;
                background-color: {label_bg};
            }}
        """)

    def _on_theme_changed(self, _mode: str):
        super()._on_theme_changed(_mode)
        self._apply_reserve_style()
