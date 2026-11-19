#!/usr/bin/env python3
"""
PX4 无人机状态后端模块
负责 ROS2 节点初始化、订阅 MAVROS 话题，并通过 Qt 信号发送数据
"""

import math
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import List, Optional

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import BatteryState, Imu

try:
    from mavros_msgs.msg import ESCStatus, OpticalFlow, State, StatusText
except ImportError:
    from mavros_msgs.msg import ESCStatus, OpticalFlowRad as OpticalFlow, State, StatusText
try:
    from mavros_msgs.msg import RCOut
except ImportError:
    RCOut = None
from mavros_msgs.srv import CommandBool, CommandLong, SetMode


@dataclass
class VehicleTelemetry:
    connected: bool = False
    mode: str = "UNKNOWN"
    armed: bool = False
    yaw_rad: float = 0.0
    flow_quality: int = 0
    flow_distance_m: float = 0.0
    battery_voltage_v: float = 0.0
    battery_percent: float = 0.0
    motor_rpms: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    motor_outputs: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    motor_outputs_smooth: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    has_esc_data: bool = False
    altitude_m: float = 0.0
    roll_deg: float = 0.0
    pitch_deg: float = 0.0
    flight_time_s: float = 0.0


@dataclass
class GroundAlert:
    timestamp: str
    severity: str
    text: str


class Backend(QObject):
    """
    后端数据提供者

    信号：
        telemetry_updated(object)     - 遥测聚合信息
        alert_received(object)        - 地面站告警文本
        connection_timeout()          - 连接超时
        ros_initialized(bool)         - ROS 初始化完成
        mode_change_result(bool, str) - 模式切换结果
        action_result(str, bool, str) - 动作执行结果 (action_name, success, message)
    """

    telemetry_updated = pyqtSignal(object)
    alert_received = pyqtSignal(object)
    connection_timeout = pyqtSignal()
    ros_initialized = pyqtSignal(bool)
    mode_change_result = pyqtSignal(bool, str)
    action_result = pyqtSignal(str, bool, str)

    def __init__(self):
        super().__init__()
        self._node: Optional[Node] = None
        self._executor: Optional[MultiThreadedExecutor] = None
        self._mode_client = None
        self._arm_client = None
        self._running = False
        self._last_state_time: Optional[float] = None
        self._telemetry = VehicleTelemetry()
        self._lock = threading.Lock()
        self._flight_start: Optional[float] = None

        self._ros_thread = threading.Thread(target=self._ros_spin, daemon=True)
        self._ros_thread.start()

        self._timeout_timer = QTimer()
        self._timeout_timer.timeout.connect(self._check_timeout)
        self._timeout_timer.start(3000)

    def _ros_spin(self):
        """ROS2 节点运行线程。"""
        try:
            if not rclpy.ok():
                rclpy.init(args=[])

            self._node = self._create_node()
            self._executor = MultiThreadedExecutor()
            self._executor.add_node(self._node)

            self.ros_initialized.emit(True)
            self._running = True
            self._executor.spin()
        except Exception as exc:
            print(f"[Backend] ROS executor error: {exc}")
            self.ros_initialized.emit(False)
        finally:
            self._running = False
            if self._executor is not None:
                try:
                    self._executor.shutdown()
                except Exception:
                    pass
            if self._node is not None:
                try:
                    self._node.destroy_node()
                except Exception:
                    pass
            if rclpy.ok():
                try:
                    rclpy.shutdown()
                except Exception:
                    pass

    def _create_node(self) -> Node:
        """创建 ROS2 节点并订阅话题。"""
        node = Node("px4_state_backend")

        state_qos = self._best_effort_qos(10)
        sensor_qos = self._best_effort_qos(20)
        alert_qos = self._reliable_qos(50)

        node.create_subscription(State, "/mavros/state", self._state_callback, state_qos)
        node.create_subscription(Imu, "/mavros/imu/data", self._imu_callback, sensor_qos)
        node.create_subscription(
            OpticalFlow,
            "/mavros/optical_flow/raw/optical_flow",
            self._optical_flow_callback,
            sensor_qos,
        )
        node.create_subscription(BatteryState, "/mavros/battery", self._battery_callback, sensor_qos)
        node.create_subscription(ESCStatus, "/mavros/esc_status", self._esc_callback, sensor_qos)
        if RCOut is not None:
            node.create_subscription(RCOut, "/mavros/rc/out", self._rc_out_callback, sensor_qos)
        node.create_subscription(StatusText, "/mavros/statustext/recv", self._status_text_callback, alert_qos)

        self._mode_client = node.create_client(SetMode, "/mavros/set_mode")
        self._arm_client = node.create_client(CommandBool, "/mavros/cmd/arming")
        self._cmd_client = node.create_client(CommandLong, "/mavros/cmd/command")

        node.get_logger().info("Backend: 等待 MAVROS 数据...")
        return node

    def _best_effort_qos(self, depth: int) -> QoSProfile:
        return QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=depth,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )

    def _reliable_qos(self, depth: int) -> QoSProfile:
        return QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=depth,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )

    def _snapshot(self) -> VehicleTelemetry:
        with self._lock:
            return replace(self._telemetry)

    def _emit_telemetry(self):
        self.telemetry_updated.emit(self._snapshot())

    # ── ROS2 callbacks ──────────────────────────────────────────────────────

    def _state_callback(self, msg: State):
        self._last_state_time = time.time()
        with self._lock:
            was_armed = self._telemetry.armed
            self._telemetry.connected = bool(msg.connected)
            self._telemetry.mode = msg.mode or "UNKNOWN"
            self._telemetry.armed = bool(msg.armed)
            if not was_armed and self._telemetry.armed:
                self._flight_start = time.time()
            elif was_armed and not self._telemetry.armed:
                if self._flight_start is not None:
                    self._telemetry.flight_time_s += time.time() - self._flight_start
                self._flight_start = None
        self._emit_telemetry()

    def _imu_callback(self, msg: Imu):
        q = msg.orientation
        # yaw (Z)
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        yaw = math.atan2(siny_cosp, cosy_cosp) % (2 * math.pi)
        # roll (X) — 向左转为正
        sinr_cosp = 2.0 * (q.w * q.x + q.y * q.z)
        cosr_cosp = 1.0 - 2.0 * (q.x * q.x + q.y * q.y)
        roll = math.degrees(math.atan2(sinr_cosp, cosr_cosp))
        # pitch (Y)
        sinp = 2.0 * (q.w * q.y - q.z * q.x)
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, sinp))))

        with self._lock:
            self._telemetry.yaw_rad = yaw
            self._telemetry.roll_deg = roll
            self._telemetry.pitch_deg = pitch
        self._emit_telemetry()

    def _optical_flow_callback(self, msg):
        quality = int(getattr(msg, "quality", 0))
        distance = float(getattr(msg, "ground_distance", getattr(msg, "distance", 0.0)))
        with self._lock:
            self._telemetry.flow_quality = quality
            self._telemetry.flow_distance_m = distance
        self._emit_telemetry()

    def _battery_callback(self, msg: BatteryState):
        voltage = float(msg.voltage)
        percent = self._convert_battery_to_percent(voltage, msg.percentage)

        with self._lock:
            self._telemetry.battery_voltage_v = voltage
            self._telemetry.battery_percent = percent
        self._emit_telemetry()

    def _esc_callback(self, msg: ESCStatus):
        rpms = [0, 0, 0, 0]
        try:
            for index, esc in enumerate(msg.esc_status[:4]):
                rpms[index] = int(getattr(esc, "rpm", 0))
        except Exception:
            pass

        with self._lock:
            self._telemetry.motor_rpms = rpms
            self._telemetry.has_esc_data = True
        self._emit_telemetry()

    def _rc_out_callback(self, msg):
        outputs = [0, 0, 0, 0]
        try:
            channels = getattr(msg, "channels", [])
            for i in range(min(4, len(channels))):
                outputs[i] = int(channels[i])
        except Exception:
            pass

        with self._lock:
            self._telemetry.motor_outputs = outputs
            prev = self._telemetry.motor_outputs_smooth
            alpha = 0.50
            smoothed = [alpha * outputs[i] + (1 - alpha) * prev[i] for i in range(4)]
            self._telemetry.motor_outputs_smooth = smoothed
        self._emit_telemetry()

    def _status_text_callback(self, msg: StatusText):
        text = msg.text.strip()
        if not text:
            return

        alert = GroundAlert(
            timestamp=datetime.now().strftime("%Y/%m/%d %H:%M:%S"),
            severity=self._severity_to_text(int(msg.severity)),
            text=text,
        )
        self.alert_received.emit(alert)

    # ── Flight time tracking ────────────────────────────────────────────────

    def _get_flight_time_s(self) -> float:
        with self._lock:
            total = self._telemetry.flight_time_s
            if self._flight_start is not None:
                total += time.time() - self._flight_start
            return total

    # ── Mode change ────────────────────────────────────────────────────────

    def request_mode_change(self, mode: str):
        threading.Thread(target=self._mode_change_worker, args=(mode,), daemon=True).start()

    def _mode_change_worker(self, mode: str):
        if self._node is None or self._mode_client is None:
            self.mode_change_result.emit(False, "后端未就绪，无法发送模式切换。")
            return

        if not self._mode_client.wait_for_service(timeout_sec=1.5):
            self.mode_change_result.emit(False, "未找到 /mavros/set_mode 服务。")
            return

        request = SetMode.Request()
        request.base_mode = 0
        request.custom_mode = mode

        future = self._mode_client.call_async(request)
        deadline = time.time() + 3.0
        while not future.done() and time.time() < deadline:
            time.sleep(0.05)

        if not future.done():
            self.mode_change_result.emit(False, f"模式切换超时：{mode}")
            return

        try:
            response = future.result()
        except Exception as exc:
            self.mode_change_result.emit(False, f"模式切换失败：{exc}")
            return

        if getattr(response, "mode_sent", False):
            self.mode_change_result.emit(True, f"模式切换已发送：{mode}")
        else:
            self.mode_change_result.emit(False, f"飞控拒绝切换模式：{mode}")

    # ── Arm / Disarm / Emergency Stop ──────────────────────────────────────

    def request_arm(self):
        """解锁（上锁 → 解锁）"""
        threading.Thread(target=self._arm_worker, args=(True,), daemon=True).start()

    def request_disarm(self):
        """上锁（解锁 → 上锁）"""
        threading.Thread(target=self._arm_worker, args=(False,), daemon=True).start()

    def request_emergency_stop(self):
        """急停：先切 LAND 模式再上锁。"""
        threading.Thread(target=self._emergency_stop_worker, daemon=True).start()

    def request_reboot_fc(self):
        """重启飞控。"""
        threading.Thread(target=self._reboot_fc_worker, daemon=True).start()

    def _reboot_fc_worker(self):
        if self._node is None or self._cmd_client is None:
            self.action_result.emit("重启飞控", False, "后端未就绪，无法发送重启指令。")
            return

        if not self._cmd_client.wait_for_service(timeout_sec=1.5):
            self.action_result.emit("重启飞控", False, "未找到 /mavros/cmd/command 服务。")
            return

        request = CommandLong.Request()
        request.broadcast = False
        request.command = 246  # MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN
        request.param1 = 1.0   # 1 = reboot, 2 = shutdown
        request.param2 = 0.0
        request.param3 = 0.0
        request.param4 = 0.0
        request.param5 = 0.0
        request.param6 = 0.0
        request.param7 = 0.0

        future = self._cmd_client.call_async(request)
        deadline = time.time() + 5.0
        while not future.done() and time.time() < deadline:
            time.sleep(0.05)

        if not future.done():
            self.action_result.emit("重启飞控", False, "重启飞控超时")
            return

        try:
            response = future.result()
        except Exception as exc:
            self.action_result.emit("重启飞控", False, f"重启飞控失败：{exc}")
            return

        if getattr(response, "success", False):
            self.action_result.emit("重启飞控", True, "飞控重启指令已发送，等待飞控重启...")
        else:
            self.action_result.emit("重启飞控", False, "飞控拒绝重启指令")

    def _arm_worker(self, arm: bool):
        action_name = "解锁" if arm else "上锁"
        if self._node is None or self._arm_client is None:
            self.action_result.emit(action_name, False, "后端未就绪，无法执行动作。")
            return

        if not self._arm_client.wait_for_service(timeout_sec=1.5):
            self.action_result.emit(action_name, False, "未找到 /mavros/cmd/arming 服务。")
            return

        request = CommandBool.Request()
        request.value = arm

        future = self._arm_client.call_async(request)
        deadline = time.time() + 3.0
        while not future.done() and time.time() < deadline:
            time.sleep(0.05)

        if not future.done():
            self.action_result.emit(action_name, False, f"{action_name}超时")
            return

        try:
            response = future.result()
        except Exception as exc:
            self.action_result.emit(action_name, False, f"{action_name}失败：{exc}")
            return

        if getattr(response, "success", False):
            self.action_result.emit(action_name, True, f"{action_name}指令已发送")
        else:
            self.action_result.emit(action_name, False, f"飞控拒绝{action_name}")

    def _emergency_stop_worker(self):
        if self._node is None or self._cmd_client is None:
            self.action_result.emit("急停", False, "后端未就绪。")
            return

        if not self._cmd_client.wait_for_service(timeout_sec=1.0):
            self.action_result.emit("急停", False, "未找到 /mavros/cmd/command 服务。")
            return

        # Force disarm via MAV_CMD_COMPONENT_ARM_DISARM (cmd=400)
        # param1=0 (disarm), param2=21196 (force disarm magic number)
        request = CommandLong.Request()
        request.broadcast = False
        request.command = 400
        request.param1 = 0.0
        request.param2 = 21196.0
        request.param3 = 0.0
        request.param4 = 0.0
        request.param5 = 0.0
        request.param6 = 0.0
        request.param7 = 0.0

        future = self._cmd_client.call_async(request)
        deadline = time.time() + 3.0
        while not future.done() and time.time() < deadline:
            time.sleep(0.05)

        if not future.done():
            self.action_result.emit("急停", False, "急停超时")
            return

        try:
            response = future.result()
        except Exception as exc:
            self.action_result.emit("急停", False, f"急停失败：{exc}")
            return

        if getattr(response, "success", False):
            self.action_result.emit("急停", True, "急停已触发 → 电机立即停转")
        else:
            self.action_result.emit("急停", False, "飞控拒绝急停指令，尝试切换 LAND 模式...")
            self._land_then_disarm()

    def _land_then_disarm(self):
        """Fallback: switch to LAND mode then disarm when force-disarm is rejected."""
        if self._mode_client is None:
            return
        if not self._mode_client.wait_for_service(timeout_sec=1.0):
            return

        request = SetMode.Request()
        request.base_mode = 0
        request.custom_mode = "AUTO.LAND"

        future = self._mode_client.call_async(request)
        deadline = time.time() + 3.0
        while not future.done() and time.time() < deadline:
            time.sleep(0.05)

        if not future.done():
            return

        try:
            response = future.result()
        except Exception:
            return

        if getattr(response, "mode_sent", False):
            self.action_result.emit("急停", True, "急停已触发 → LAND 模式")
            time.sleep(0.3)
            self._arm_worker(False)
        else:
            self.action_result.emit("急停", False, "飞控拒绝所有急停方式")

    # ── Timeout ─────────────────────────────────────────────────────────────

    def _check_timeout(self):
        if self._last_state_time is not None and time.time() - self._last_state_time > 5:
            self.connection_timeout.emit()

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _convert_battery_to_percent(self, voltage: float, reported_percentage: float) -> float:
        full_v = 16.7
        empty_v = 14.5
        if voltage <= empty_v:
            return 0.0
        if voltage >= full_v:
            return 100.0
        return (voltage - empty_v) / (full_v - empty_v) * 100.0

    def _severity_to_text(self, severity: int) -> str:
        if severity <= 3:
            return "ERROR"
        if severity <= 5:
            return "WARN"
        return "INFO"

    def shutdown(self):
        self._timeout_timer.stop()
        self._running = False
        if self._executor is not None:
            try:
                self._executor.shutdown()
            except Exception:
                pass
