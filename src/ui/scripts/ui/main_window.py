"""主窗口 — 组装全部面板，连接后端信号。"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QLinearGradient
from PyQt5.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QMainWindow, QSizePolicy,
    QVBoxLayout, QWidget, QStackedWidget,
)

from qfluentwidgets import BodyLabel

from ui.constants import MODE_ALIAS_MAP
from ui.theme import ThemeManager
from ui.widgets import (
    AltitudeCard, FlightTimeCard, MetricCard, ScenicBackground,
)
from ui.panels import (
    ActionPanel, AlertPanel, FlightModePanel, HeroHeader,
    MotorPanel, NavigationSidebar, ReservePanel, WarehousePanel,
)
from ui.warehouse_manager import WarehouseManager


class MainWindow(QMainWindow):
    def __init__(self, backend, source_name: str):
        super().__init__()
        self.backend          = backend
        self.source_name      = source_name
        self.backend_ready    = False
        self.current_telemetry: Optional[object] = None
        self._last_armed_state: Optional[bool]   = None
        self._pending_mode:    Optional[str]      = None
        self._pending_telemetry: Optional[object] = None
        self._refresh_scheduled = False

        self.setWindowTitle("WS00 慧谷地面站")
        self.setMinimumSize(1440, 880)

        self.backend.telemetry_updated.connect(self._on_telemetry)
        self.backend.alert_received.connect(self.on_alert_received)
        self.backend.connection_timeout.connect(self.on_connection_timeout)
        self.backend.ros_initialized.connect(self.on_ros_initialized)
        self.backend.mode_change_result.connect(self.on_mode_change_result)
        if hasattr(self.backend, "action_result"):
            self.backend.action_result.connect(self.on_action_result)

        self._setup_ui()
        self._set_initial_status()

        # 刷新节流：30 Hz，保证电机显示流畅
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(33)
        self._refresh_timer.timeout.connect(self._do_refresh)
        self._refresh_timer.start()

    def _on_telemetry(self, telemetry):
        """缓存最新遥测数据，由定时器统一刷新界面。"""
        self.current_telemetry = telemetry
        self._pending_telemetry = telemetry
        self._refresh_scheduled = True

    def _do_refresh(self):
        if self._refresh_scheduled and self._pending_telemetry is not None:
            self._refresh_scheduled = False
            self.refresh_display()

    def _setup_ui(self):
        central = ScenicBackground(self)
        self.setCentralWidget(central)

        root_h = QHBoxLayout(central)
        root_h.setContentsMargins(18, 18, 18, 18)
        root_h.setSpacing(12)

        self.sidebar = NavigationSidebar(self)
        root_h.addWidget(self.sidebar)

        main_v = QVBoxLayout()
        main_v.setSpacing(12)

        self.hero = HeroHeader(self)
        self.hero.reboot_requested.connect(self._request_reboot_fc)
        main_v.addWidget(self.hero)

        content = QHBoxLayout()
        content.setSpacing(12)

        left_col = QVBoxLayout()
        left_col.setSpacing(12)

        # 2×3 metric card grid
        metrics_wrap   = QWidget(self)
        metrics_layout = QGridLayout(metrics_wrap)
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.setHorizontalSpacing(12)
        metrics_layout.setVerticalSpacing(12)

        self.connection_card = MetricCard(
            "连接状态", "显示飞控链路与 MAVROS 在线情况", self
        )
        self.mode_card = MetricCard(
            "飞行模式", "当前模式来自 /mavros/state", self
        )
        self.armed_card = MetricCard(
            "解锁状态", "仅解锁时闪烁提示", self
        )
        self.yaw_card = MetricCard(
            "偏航角(yaw)", "按 0 ~ 2π 弧度显示", self,
            gauge_max=2 * math.pi, gauge_color="#4D8CFF",
        )
        self.flow_card = MetricCard(
            "光流质量", "显示质量值与测距信息", self,
            gauge_max=255, gauge_color="#1FB96A",
        )
        self.battery_card = MetricCard(
            "电池电量", "14.5V → 0%，16.7V → 100%", self
        )

        for idx, card in enumerate([
            self.connection_card, self.mode_card, self.armed_card,
            self.yaw_card, self.flow_card, self.battery_card,
        ]):
            r, c = divmod(idx, 3)
            metrics_layout.addWidget(card, r, c)

        left_col.addWidget(metrics_wrap)

        extra_row = QHBoxLayout()
        extra_row.setSpacing(12)
        self.altitude_card    = AltitudeCard(self)
        self.flight_time_card = FlightTimeCard(self)
        extra_row.addWidget(self.altitude_card, 1)
        extra_row.addWidget(self.flight_time_card, 1)
        left_col.addLayout(extra_row)

        # Motor panel
        self.motor_panel = MotorPanel(self)
        left_col.addWidget(self.motor_panel)

        self.reserve_panel = ReservePanel(self)
        left_col.addWidget(self.reserve_panel, 1)

        # Right column
        right_col = QVBoxLayout()
        right_col.setSpacing(12)

        self.mode_panel = FlightModePanel(self)
        self.mode_panel.apply_button.clicked.connect(self._request_selected_mode)

        # Action panel — 解锁 / 上锁 / 急停 / 一键降落
        self.action_panel = ActionPanel(self)
        self.action_panel.arm_requested.connect(self._request_arm)
        self.action_panel.disarm_requested.connect(self._request_disarm)
        self.action_panel.emergency_stop_requested.connect(self._request_emergency_stop)
        self.action_panel.land_requested.connect(self._request_land)

        self.alert_panel = AlertPanel(self)

        right_col.addWidget(self.mode_panel)
        right_col.addWidget(self.action_panel)
        right_col.addWidget(self.alert_panel, 1)

        content.addLayout(left_col, 8)
        content.addLayout(right_col, 6)

        # ── 页面切换 (QStackedWidget) ──────────────────────────
        self._pages = QStackedWidget()
        # Page 0: 主仪表盘（上面的content layout）
        dash_wrap = QWidget()
        dash_wrap.setLayout(content)
        self._pages.addWidget(dash_wrap)

        # Page 1: 仓库管理
        self._warehouse_mgr = WarehouseManager(self)
        self._warehouse_panel = WarehousePanel(self._warehouse_mgr, self)
        self._pages.addWidget(self._warehouse_panel)

        main_v.addWidget(self._pages, 1)

        # Sidebar 页面切换
        self.sidebar.page_changed.connect(self._switch_page)

        # Footer
        footer = QFrame(self)
        footer.setObjectName("footerBar")
        foot_lay = QHBoxLayout(footer)
        foot_lay.setContentsMargins(18, 11, 18, 11)

        src_lbl = BodyLabel(f"▣  数据源：MAVROS  |  刷新率：10 Hz  |  {self.source_name}", self)
        src_lbl.setObjectName("dataSource")
        foot_lay.addWidget(src_lbl)
        foot_lay.addStretch(1)

        self.status_hint = BodyLabel("等待后端初始化...", self)
        self.status_hint.setObjectName("statusHint")
        self.status_hint.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        foot_lay.addWidget(self.status_hint)

        main_v.addWidget(footer)
        root_h.addLayout(main_v, 1)

        self._apply_root_style()

    def _apply_root_style(self):
        c = ThemeManager.instance().colors()
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background: transparent;
                font-family: 'Segoe UI', 'Microsoft YaHei UI', 'Microsoft YaHei';
            }}
            #footerBar {{
                background-color: {c["footer_bg"]};
                border: 1px solid {c["panel_border"]};
                border-radius: 18px;
            }}
            #statusHint  {{ color: {c["text_muted"]}; font-size: 13px; }}
            #dataSource  {{ color: {c["text_secondary"]}; font-size: 12px; }}
        """)

    # ── Initial state ──────────────────────────────────────────────────────

    def _set_initial_status(self):
        self.connection_card.set_state("未连接",   "", "danger")
        self.mode_card.set_state(      "未知",     "", "warning")
        self.armed_card.set_state(     "未解锁",   "", "good")
        self.yaw_card.set_state(       "0.00 rad", "", "normal")
        self.flow_card.set_state(      "0 / 255",  "", "normal")
        self.battery_card.set_state(   "0%",       "", "warning")

    # ── Action requests ────────────────────────────────────────────────────

    def _request_selected_mode(self):
        mode = self.mode_panel.selected_mode()
        if not mode:
            self.mode_panel.set_feedback("请先选择目标模式。", False)
            return
        self._pending_mode = mode
        self.mode_panel.set_feedback(f"正在发送模式：{mode}", True)
        if hasattr(self.backend, "request_mode_change"):
            self.backend.request_mode_change(mode)

    def _request_arm(self):
        self.action_panel.set_feedback("正在发送解锁指令...", True)
        if hasattr(self.backend, "request_arm"):
            self.backend.request_arm()

    def _request_disarm(self):
        self.action_panel.set_feedback("正在发送上锁指令...", True)
        if hasattr(self.backend, "request_disarm"):
            self.backend.request_disarm()

    def _request_emergency_stop(self):
        self.action_panel.set_feedback("急停触发中...", True)
        if hasattr(self.backend, "request_emergency_stop"):
            self.backend.request_emergency_stop()

    def _request_land(self):
        self.action_panel.set_feedback("正在发送 AUTO.LAND 模式...", True)
        if hasattr(self.backend, "request_mode_change"):
            self.backend.request_mode_change("AUTO.LAND")

    def _request_reboot_fc(self):
        from PyQt5.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "重启飞控",
            "确定要重启飞控吗？\n\n此操作将导致无人机短时间内失去响应。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.action_panel.set_feedback("正在发送重启飞控指令...", True)
        if hasattr(self.backend, "request_reboot_fc"):
            self.backend.request_reboot_fc()

    # ── Callbacks ──────────────────────────────────────────────────────────

    def on_action_result(self, action: str, success: bool, message: str):
        self.action_panel.set_feedback(message, success)

    def on_mode_change_result(self, success: bool, message: str):
        self.mode_panel.set_feedback(message, success)
        if not success:
            self._pending_mode = None

    def on_ros_initialized(self, success: bool):
        self.backend_ready = success
        if success:
            self.status_hint.setText("后端已启动，等待遥测数据进入界面。")
        else:
            self.status_hint.setText("后端初始化失败，请检查 MAVROS 与 ROS 环境。")

    def on_connection_timeout(self):
        self.status_hint.setText("连接超时，请检查 MAVROS 与飞控链路。")
        self.connection_card.set_state("离线", "", "danger")
        self.hero.set_link_state("LINK LOST", "#F06767")

    def on_alert_received(self, alert):
        timestamp = getattr(alert, "timestamp", datetime.now().strftime("%H:%M:%S"))
        severity  = getattr(alert, "severity", "INFO")
        text      = getattr(alert, "text", "")
        self.alert_panel.add_alert(timestamp, severity, text)

    # ── Display refresh ────────────────────────────────────────────────────

    def refresh_display(self):
        if self.current_telemetry is None:
            return

        data = self.current_telemetry

        connected         = bool(getattr(data, "connected", False))
        mode_text         = getattr(data, "mode", "未知") or "未知"
        mode_display      = MODE_ALIAS_MAP.get(mode_text, mode_text)
        armed             = bool(getattr(data, "armed", False))
        yaw_rad           = float(getattr(data, "yaw_rad", 0.0))
        flow_quality      = int(getattr(data, "flow_quality", 0))
        flow_distance_m   = float(getattr(data, "flow_distance_m", 0.0))
        battery_voltage_v = float(getattr(data, "battery_voltage_v", 0.0))
        battery_percent   = float(getattr(data, "battery_percent", 0.0))
        motor_rpms        = list(getattr(data, "motor_rpms", [0, 0, 0, 0]))
        altitude_m        = float(getattr(data, "altitude_m", 0.0))
        roll_deg          = float(getattr(data, "roll_deg", 0.0))
        pitch_deg         = float(getattr(data, "pitch_deg", 0.0))
        flight_time_s     = float(getattr(data, "flight_time_s", 0.0))

        # Connection
        if connected:
            self.connection_card.set_state("连接正常", "", "good")
            self.hero.set_link_state("LINK ACTIVE", "#1FB96A")
        else:
            self.connection_card.set_state("离线", "", "danger")
            self.hero.set_link_state("LINK LOST", "#F06767")

        # Mode
        self.mode_card.set_state(mode_display, "", "good" if connected else "warning")
        self.mode_panel.set_current_mode(mode_display)
        if self._pending_mode and mode_text == self._pending_mode:
            self.mode_panel.set_feedback(f"模式已切换：{mode_display}", True)
            self._pending_mode = None

        # Armed — 仅解锁时闪烁（上锁时不闪烁）
        armed_level = "warning" if armed else "good"
        armed_text  = "已解锁" if armed else "未解锁"
        self.armed_card.set_state(armed_text, "", armed_level)
        if self._last_armed_state is not None and not self._last_armed_state and armed:
            self.armed_card.blink_indicator("#1FB96A", "#D6FFE8", 5000, armed_level)
        self._last_armed_state = armed

        # Yaw
        yaw_rad = yaw_rad % (2 * math.pi)
        yaw_deg = math.degrees(yaw_rad)
        self.yaw_card.set_state(f"{yaw_rad:.2f} rad", "", "info")
        self.yaw_card.set_gauge_value(yaw_rad, f"{yaw_deg:.0f}°")

        # Flow
        if flow_quality >= 180:
            flow_level = "good"
        elif flow_quality >= 80:
            flow_level = "warning"
        else:
            flow_level = "danger" if connected else "normal"
        self.flow_card.set_state(f"{flow_quality} / 255", "", flow_level)
        self.flow_card.set_gauge_value(flow_quality, str(flow_quality))

        # Battery
        if battery_percent >= 60:
            batt_level = "good"
        elif battery_percent >= 25:
            batt_level = "warning"
        else:
            batt_level = "danger"
        self.battery_card.set_state(f"{battery_percent:.0f}%", "", batt_level)

        # Altitude + attitude
        self.altitude_card.set_state(altitude_m, roll_deg, pitch_deg)

        # Flight time
        self.flight_time_card.set_flight_time(flight_time_s)

        # Motor RPMs
        has_esc_data = bool(getattr(data, "has_esc_data", False))
        motor_outputs = list(getattr(data, "motor_outputs_smooth", [0, 0, 0, 0]))
        self.motor_panel.set_motor_rpms(motor_rpms, has_esc_data, motor_outputs)

        time_str = datetime.now().strftime("%H:%M:%S")
        self.status_hint.setText(
            f"系统状态：一切正常  |  最后更新：{time_str}"
        )

    def _switch_page(self, key: str):
        if key == "warehouse":
            self._pages.setCurrentIndex(1)
        else:
            self._pages.setCurrentIndex(0)

    def closeEvent(self, event):
        self._warehouse_mgr.stop_ros()
        if hasattr(self.backend, "shutdown"):
            self.backend.shutdown()
        event.accept()
