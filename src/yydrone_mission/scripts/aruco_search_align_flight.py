#!/usr/bin/env python3
"""
AR码搜索与对准节点 - 实机飞行版本 (velocity 控制)
====================================================
状态机: INIT → OFFBOARD → TAKEOFF → SEARCH → APPROACH → ALIGN → HOVER

控制方式:
  话题:   /mavros/setpoint_raw/local (mavros_msgs/PositionTarget)
  坐标:   机体 FLU (X=前 Y=左 Z=上)
  模式:   velocity + yaw_rate

坐标系对照:
  相机光学: X=右  Y=下  Z=前
  机体 FLU: X=前  Y=左  Z=上
  → cam_right → body_left  = -Y
  → cam_down  → body_up    = -Z
  → 对准速度: vy = -cam_x * gain,  vz = -cam_y * gain

安全:
  按 q 键 → AUTO.LAND
  每阶段有超时保护
  位置限幅
"""

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from sensor_msgs.msg import Image
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from mavros_msgs.srv import SetMode, CommandBool
from mavros_msgs.msg import State, PositionTarget
import cv2
from cv_bridge import CvBridge
import math
from enum import Enum


class Phase(Enum):
    INIT = 1
    OFFBOARD = 2
    TAKEOFF = 3
    SEARCH = 4
    APPROACH = 5       # 前进靠近 AR 码
    ALIGN = 6
    HOVER = 7


class ArucoSearchAlignFlight(Node):
    def __init__(self):
        super().__init__('aruco_search_align_flight')

        # ═══════════════════════════════════════════════════════════
        # 参数
        # ═══════════════════════════════════════════════════════════
        self.declare_parameter('takeoff_height',    1.0)
        self.declare_parameter('takeoff_speed',     0.5)     # 起飞上升速度 (m/s)
        self.declare_parameter('circle_radius',     0.25)
        self.declare_parameter('circle_period',     5.0)
        self.declare_parameter('detect_timeout',    0.5)
        self.declare_parameter('align_gain_xy',     0.15)    # YZ 对准增益
        self.declare_parameter('align_gain_yaw',    0.1)     # yaw 对准增益
        self.declare_parameter('align_deadzone',    0.1)     # 位置死区 (m)
        self.declare_parameter('yaw_deadzone_deg', 10.0)     # yaw 死区 (度)
        self.declare_parameter('takeoff_timeout',  30.0)
        self.declare_parameter('phase_timeout',   120.0)
        self.declare_parameter('max_vel_xy',        0.15)    # 水平速度限幅
        self.declare_parameter('max_vel_z',         0.15)    # 垂直速度限幅
        self.declare_parameter('max_yaw_rate',     45.0)     # yaw 角速度限幅 (deg/s)
        self.declare_parameter('approach_distance', 2.5)     # 靠近目标距离 (m)，ar_z 小于此值开始对准
        self.declare_parameter('approach_speed',    0.2)     # 前进靠近速度 (m/s)

        self.takeoff_height  = self.get_parameter('takeoff_height').value
        self.takeoff_speed   = self.get_parameter('takeoff_speed').value
        self.circle_radius   = self.get_parameter('circle_radius').value
        self.circle_period   = self.get_parameter('circle_period').value
        self.detect_timeout  = self.get_parameter('detect_timeout').value
        self.align_gain_xy   = self.get_parameter('align_gain_xy').value
        self.align_gain_yaw  = self.get_parameter('align_gain_yaw').value
        self.align_deadzone  = self.get_parameter('align_deadzone').value
        self.yaw_deadzone_deg = self.get_parameter('yaw_deadzone_deg').value
        self.takeoff_timeout = self.get_parameter('takeoff_timeout').value
        self.phase_timeout   = self.get_parameter('phase_timeout').value
        self.max_vel_xy      = self.get_parameter('max_vel_xy').value
        self.max_vel_z       = self.get_parameter('max_vel_z').value
        self.max_yaw_rate    = math.radians(self.get_parameter('max_yaw_rate').value)
        self.approach_distance = self.get_parameter('approach_distance').value
        self.approach_speed   = self.get_parameter('approach_speed').value

        # ═══════════════════════════════════════════════════════════
        # 状态
        # ═══════════════════════════════════════════════════════════
        self.bridge = CvBridge()
        self.phase = Phase.INIT
        self.phase_start_time = self.get_clock().now()

        # ── 飞控状态 ──────────────────────────────────────────
        self.connected = False
        self.armed = False
        self.mode = ''
        self.pos_x = 0.0
        self.pos_y = 0.0
        self.pos_z = 0.0
        self.yaw = 0.0
        self.odom_received = False
        self.origin_x = 0.0
        self.origin_y = 0.0
        self.origin_z = 0.0

        # ── 速度指令 ──────────────────────────────────────────
        self.cmd_vx = 0.0      # 前
        self.cmd_vy = 0.0      # 左
        self.cmd_vz = 0.0      # 上
        self.cmd_yaw_rate = 0.0

        # ── AR 检测 ──────────────────────────────────────────
        self.aruco_detected = False
        self.last_pose_time = self.get_clock().now()
        self.ar_x = 0.0
        self.ar_y = 0.0
        self.ar_z = 0.0
        self.ar_qx = 0.0
        self.ar_qy = 0.0
        self.ar_qz = 0.0
        self.ar_qw = 1.0

        self.search_start_time = None
        self._last_log = {}

        # type_mask: velocity + yaw_rate, 忽略 position/accel/yaw
        self._type_mask_vel = (PositionTarget.IGNORE_PX |
                               PositionTarget.IGNORE_PY |
                               PositionTarget.IGNORE_PZ |
                               PositionTarget.IGNORE_AFX |
                               PositionTarget.IGNORE_AFY |
                               PositionTarget.IGNORE_AFZ |
                               PositionTarget.IGNORE_YAW)

        # ── QoS for mavros BEST_EFFORT publishers ─
        self._qos_sensor = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE)

        # ═══════════════════════════════════════════════════════════
        # 订阅
        # ═══════════════════════════════════════════════════════════
        self.state_sub = self.create_subscription(
            State, '/mavros/state', self.state_cb, self._qos_sensor)
        self.odom_sub = self.create_subscription(
            Odometry, '/mavros/local_position/odom', self.odom_cb, self._qos_sensor)
        self.debug_sub = self.create_subscription(
            Image, '/aruco_detector/debug_image', self.debug_cb, 10)
        self.pose_sub = self.create_subscription(
            PoseStamped, '/aruco_detector/aruco_pose', self.pose_cb, 10)

        # ═══════════════════════════════════════════════════════════
        # 发布: /mavros/setpoint_raw/local
        # ═══════════════════════════════════════════════════════════
        self.setpoint_pub = self.create_publisher(
            PositionTarget, '/mavros/setpoint_raw/local', 10)

        # ═══════════════════════════════════════════════════════════
        # 服务
        # ═══════════════════════════════════════════════════════════
        self.set_mode_client = self.create_client(SetMode, '/mavros/set_mode')
        self.arm_client = self.create_client(CommandBool, '/mavros/cmd/arming')

        # ═══════════════════════════════════════════════════════════
        # 定时器: 20Hz (offboard 需要 ≥2Hz)
        # ═══════════════════════════════════════════════════════════
        self.timer = self.create_timer(0.05, self.control_loop)

        self.get_logger().info('╔══════════════════════════════════════════╗')
        self.get_logger().info('║  AR搜索对准飞行节点 v2.0 (velocity)     ║')
        self.get_logger().info('╠══════════════════════════════════════════╣')
        self.get_logger().info(f'║  起飞高度: {self.takeoff_height:.1f}m | 速度: {self.takeoff_speed:.1f}m/s   ║')
        self.get_logger().info(f'║  搜索半径: {self.circle_radius:.1f}m | 周期: {self.circle_period:.1f}s     ║')
        self.get_logger().info('║  控制: 机体FLU(X=前 Y=左 Z=上)        ║')
        self.get_logger().info('║  按 q 紧急降落                         ║')
        self.get_logger().info('╚══════════════════════════════════════════╝')

    # ═══════════════════════════════════════════════════════════════
    # 回调
    # ═══════════════════════════════════════════════════════════════

    def state_cb(self, msg: State):
        self.connected = msg.connected
        self.armed = msg.armed
        self.mode = msg.mode

    def odom_cb(self, msg: Odometry):
        self.pos_x = msg.pose.pose.position.x
        self.pos_y = msg.pose.pose.position.y
        self.pos_z = msg.pose.pose.position.z
        qx = msg.pose.pose.orientation.x
        qy = msg.pose.pose.orientation.y
        qz = msg.pose.pose.orientation.z
        qw = msg.pose.pose.orientation.w
        siny = 2.0 * (qw * qz + qx * qy)
        cosy = 1.0 - 2.0 * (qy * qy + qz * qz)
        self.yaw = math.atan2(siny, cosy)
        if not self.odom_received:
            self.origin_x = self.pos_x
            self.origin_y = self.pos_y
            self.origin_z = self.pos_z
            self.odom_received = True

    def debug_cb(self, msg: Image):
        try:
            frame = self.bridge.imgmsg_to_cv2(msg, 'bgr8')
        except Exception as e:
            self.get_logger().error(f'图像转换失败: {e}')
            return
        h, w = frame.shape[:2]
        cx_img, cy_img = w // 2, h // 2
        cv2.line(frame, (cx_img - 30, cy_img), (cx_img + 30, cy_img), (0, 255, 255), 1)
        cv2.line(frame, (cx_img, cy_img - 30), (cx_img, cy_img + 30), (0, 255, 255), 1)
        cv2.circle(frame, (cx_img, cy_img), 8, (0, 255, 255), 1)
        cv2.putText(frame,
                    f'{self.phase.name} | OFFBOARD={self.mode=="OFFBOARD"} ARMED={self.armed}',
                    (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.putText(frame,
                    f'v=({self.cmd_vx:+.2f},{self.cmd_vy:+.2f},{self.cmd_vz:+.2f}) yaw_r={math.degrees(self.cmd_yaw_rate):+.1f}',
                    (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)
        if self.aruco_detected:
            cv2.putText(frame,
                        f'AR: ({self.ar_x:.2f},{self.ar_y:.2f},{self.ar_z:.2f})m',
                        (10, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
        cv2.imshow('AR Debug View', frame)
        key = cv2.waitKey(1)
        if key == ord('q') or key == 27:
            self.get_logger().warn('[!] 用户紧急退出！')
            self._emergency_land()
            raise KeyboardInterrupt

    def pose_cb(self, msg: PoseStamped):
        self.aruco_detected = True
        self.last_pose_time = self.get_clock().now()
        self.ar_x = msg.pose.position.x
        self.ar_y = msg.pose.position.y
        self.ar_z = msg.pose.position.z
        self.ar_qx = msg.pose.orientation.x
        self.ar_qy = msg.pose.orientation.y
        self.ar_qz = msg.pose.orientation.z
        self.ar_qw = msg.pose.orientation.w

    # ═══════════════════════════════════════════════════════════════
    # 主循环 20Hz
    # ═══════════════════════════════════════════════════════════════

    def control_loop(self):
        now = self.get_clock().now()

        # AR 超时
        dt_pose = (now - self.last_pose_time).nanoseconds / 1e9
        if dt_pose > self.detect_timeout:
            self.aruco_detected = False

        # 状态机
        if self.phase == Phase.INIT:
            self._run_init(now)
        elif self.phase == Phase.OFFBOARD:
            self._run_offboard(now)
        elif self.phase == Phase.TAKEOFF:
            self._run_takeoff(now)
        elif self.phase == Phase.SEARCH:
            self._run_search(now)
        elif self.phase == Phase.APPROACH:
            self._run_approach(now)
        elif self.phase == Phase.ALIGN:
            self._run_align(now)
        elif self.phase == Phase.HOVER:
            self._run_hover(now)

        # 始终发布
        self._publish_velocity()

    # ═══════════════════════════════════════════════════════════════
    # 阶段实现
    # ═══════════════════════════════════════════════════════════════

    def _run_init(self, now):
        if self.connected and self.odom_received:
            self.get_logger().info('[INIT] FCU已连接，里程计已收到')
            self.phase = Phase.OFFBOARD
            self.phase_start_time = now

    def _run_offboard(self, now):
        """切 offboard + 解锁"""
        # 保持零速度悬停
        self.cmd_vx = 0.0
        self.cmd_vy = 0.0
        self.cmd_vz = 0.0
        self.cmd_yaw_rate = 0.0

        if self.mode != 'OFFBOARD':
            self._log_throttled('offboard', 1.0,
                f'[OFFBOARD] 请求切换 (当前: {self.mode})')
            self._request_offboard()
            return
        if not self.armed:
            self._log_throttled('offboard', 1.0, '[OFFBOARD] 请求解锁...')
            self._request_arm(True)
            return

        self.get_logger().info('[OFFBOARD] 已解锁，开始起飞')
        self.phase = Phase.TAKEOFF
        self.phase_start_time = now

    def _run_takeoff(self, now):
        """起飞: vz > 0 (上), vx=vy=0"""
        elapsed = (now - self.phase_start_time).nanoseconds / 1e9
        target_z = self.origin_z + self.takeoff_height
        height_error = target_z - self.pos_z

        self.cmd_vx = 0.0
        self.cmd_vy = 0.0
        self.cmd_vz = min(self.takeoff_speed, max(0.2, height_error * 0.8))
        self.cmd_yaw_rate = 0.0

        self._log_throttled('takeoff', 0.5,
            f'[TAKEOFF] 目标={self.takeoff_height:.1f}m '
            f'当前={self.pos_z - self.origin_z:.2f}m 误差={height_error:.2f}m')

        if abs(height_error) < 0.15:
            self.get_logger().info(
                f'[TAKEOFF] 起飞完成！高度={self.pos_z - self.origin_z:.2f}m')
            self.phase = Phase.SEARCH
            self.phase_start_time = now
            self.search_start_time = now

        if elapsed > self.takeoff_timeout:
            self.get_logger().warn('[TAKEOFF] 超时，强制进入搜索')
            self.phase = Phase.SEARCH
            self.phase_start_time = now
            self.search_start_time = now

    def _run_search(self, now):
        """竖直面(Y-Z)圆周搜索, vx=0 保持前向位置"""
        elapsed = (now - self.search_start_time).nanoseconds / 1e9

        if self.aruco_detected:
            self.get_logger().info(f'[SEARCH] ★ 检测到AR码 → 前进靠近 (目标距离<{self.approach_distance:.1f}m)')
            self.phase = Phase.APPROACH
            self.phase_start_time = now
            return

        phase_elapsed = (now - self.phase_start_time).nanoseconds / 1e9
        if phase_elapsed > self.phase_timeout:
            self.get_logger().warn('[SEARCH] 超时 → 悬停')
            self.phase = Phase.HOVER
            self.phase_start_time = now
            return

        # ── 竖直面圆周: Y-Z 平面 (左-上) ─────────────────────
        omega = 2.0 * math.pi / self.circle_period
        R = self.circle_radius

        # 圆周在竖直面: Y=R*cos(ωt), Z=R*sin(ωt)
        # 速度(切线): vy = -R*ω*sin(ωt), vz = R*ω*cos(ωt)
        vy_circle = -R * omega * math.sin(omega * elapsed)
        vz_circle =  R * omega * math.cos(omega * elapsed)

        self.cmd_vx = 0.0
        self.cmd_vy = vy_circle
        self.cmd_vz = vz_circle
        self.cmd_yaw_rate = 0.0

        self._log_throttled('search', 0.5,
            f'[搜索] 角={math.degrees(omega * elapsed) % 360:5.1f}° | '
            f'vy={vy_circle:+.2f} vz={vz_circle:+.2f} m/s | '
            f'pos=({self.pos_x:.2f},{self.pos_y:.2f},{self.pos_z:.2f})')

    def _run_approach(self, now):
        """前进靠近 AR 码，保持粗略居中，距离够了再校准"""
        if not self.aruco_detected:
            self.get_logger().info('[APPROACH] AR丢失 → 回搜索')
            self.phase = Phase.SEARCH
            self.phase_start_time = now
            self.search_start_time = now
            return

        # ── 已足够近 → 校准 ──────────────────────────────────
        if self.ar_z < self.approach_distance:
            self.get_logger().info(
                f'[APPROACH] 距离已足够 (ar_z={self.ar_z:.2f}m < {self.approach_distance:.1f}m) → 校准')
            self.phase = Phase.ALIGN
            self.phase_start_time = now
            return

        # ── 前进 + 粗略居中 ──────────────────────────────────
        # vx: 前进 (ar_z 越远越快，但不超过 approach_speed)
        self.cmd_vx = min(self.approach_speed, self.ar_z * 0.3)

        # vy, vz: 粗略保持 AR 在画面中间
        self.cmd_vy = max(-self.max_vel_xy, min(self.max_vel_xy,
                        -self.ar_x * self.align_gain_xy))
        self.cmd_vz = max(-self.max_vel_z, min(self.max_vel_z,
                        -self.ar_y * self.align_gain_xy))
        # 接近过程中不做 yaw 校准
        self.cmd_yaw_rate = 0.0

        self._log_throttled('approach', 0.3,
            f'[靠近] AR距={self.ar_z:.2f}m (目标<{self.approach_distance:.1f}m) | '
            f'vx={self.cmd_vx:+.2f} vy={self.cmd_vy:+.2f} vz={self.cmd_vz:+.2f} | '
            f'AR({self.ar_x:+.2f},{self.ar_y:+.2f})')

    def _run_align(self, now):
        """对准: vy = -cam_x * gain, vz = -cam_y * gain (机体FLU)"""
        if not self.aruco_detected:
            self.get_logger().info('[ALIGN] AR丢失 → 回搜索')
            self.phase = Phase.SEARCH
            self.phase_start_time = now
            self.search_start_time = now
            return

        # ── 偏差 ────────────────────────────────────────────
        # 相机光学 → 机体 FLU:
        #   cam右(X+) → body左(Y-) → vy = -cam_x
        #   cam下(Y+) → body上(Z-) → vz = -cam_y
        err_y = -self.ar_x    # 右正 → 需要左移(vy负)
        err_z = -self.ar_y    # 下正 → 需要上移(vz负)
        yaw_deg = self._get_yaw_angle_deg()
        err_yaw_rad = math.radians(yaw_deg)

        # ── 判断对准 ────────────────────────────────────────
        y_ok = abs(self.ar_x) < self.align_deadzone
        z_ok = abs(self.ar_y) < self.align_deadzone
        yaw_ok = abs(yaw_deg) < self.yaw_deadzone_deg

        if y_ok and z_ok and yaw_ok:
            self._log_throttled('align_ok', 1.0,
                f'[✓已对准] AR距={self.ar_z:.2f}m '
                f'| cam({self.ar_x:+.3f},{self.ar_y:+.3f}) yaw={yaw_deg:+.1f}°')
            self.phase = Phase.HOVER
            self.phase_start_time = now
            # 归零速度
            self.cmd_vy = 0.0
            self.cmd_vz = 0.0
            self.cmd_yaw_rate = 0.0
            return

        # ── 速度指令 (比例 + 限幅) ──────────────────────────
        self.cmd_vx = 0.0
        self.cmd_vy = max(-self.max_vel_xy, min(self.max_vel_xy,
                        err_y * self.align_gain_xy))
        self.cmd_vz = max(-self.max_vel_z, min(self.max_vel_z,
                        err_z * self.align_gain_xy))
        if yaw_ok:
            self.cmd_yaw_rate = 0.0
        else:
            self.cmd_yaw_rate = max(-self.max_yaw_rate, min(self.max_yaw_rate,
                                  -err_yaw_rad * self.align_gain_yaw))

        # ── 方向文字 ────────────────────────────────────────
        dir_y = '→右' if self.ar_x > self.align_deadzone else \
                ('←左' if self.ar_x < -self.align_deadzone else '·')
        dir_z = '↓下' if self.ar_y > self.align_deadzone else \
                ('↑上' if self.ar_y < -self.align_deadzone else '·')
        dir_yaw = '↺逆' if yaw_deg > self.yaw_deadzone_deg else \
                  ('↻顺' if yaw_deg < -self.yaw_deadzone_deg else '·')

        self._log_throttled('align', 0.3,
            f'[对准] AR({self.ar_x:+.2f},{self.ar_y:+.2f},{self.ar_z:.2f})m | '
            f'机体: {dir_y}{dir_z} 旋转:{dir_yaw}({yaw_deg:+.1f}°) | '
            f'cmd: vy={self.cmd_vy:+.2f} vz={self.cmd_vz:+.2f} '
            f'yaw_r={math.degrees(self.cmd_yaw_rate):+.1f}°/s')

    def _run_hover(self, now):
        """悬停"""
        self.cmd_vx = 0.0
        self.cmd_vy = 0.0
        self.cmd_vz = 0.0
        self.cmd_yaw_rate = 0.0

        # AR 偏移 → 重新对准
        if self.aruco_detected:
            if (abs(self.ar_x) > self.align_deadzone * 2 or
                abs(self.ar_y) > self.align_deadzone * 2 or
                abs(self._get_yaw_angle_deg()) > self.yaw_deadzone_deg * 2):
                self.get_logger().info('[HOVER] AR偏移，重新对准')
                self.phase = Phase.ALIGN
                self.phase_start_time = now
                return

        self._log_throttled('hover', 2.0,
            f'[悬停] pos=({self.pos_x:.2f},{self.pos_y:.2f},{self.pos_z:.2f}) '
            f'AR={"✓" if self.aruco_detected else "✗"}')

    # ═══════════════════════════════════════════════════════════════
    # 发布
    # ═══════════════════════════════════════════════════════════════

    def _publish_velocity(self):
        """发布 velocity + yaw_rate setpoint (机体 FLU 坐标系)"""
        msg = PositionTarget()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'map'
        msg.coordinate_frame = PositionTarget.FRAME_BODY_NED  # 机体坐标系
        msg.type_mask = self._type_mask_vel
        msg.velocity.x = float(self.cmd_vx)       # X=前
        msg.velocity.y = float(self.cmd_vy)       # Y=左
        msg.velocity.z = float(self.cmd_vz)       # Z=上
        msg.yaw_rate   = float(self.cmd_yaw_rate)

        self.setpoint_pub.publish(msg)

    # ═══════════════════════════════════════════════════════════════
    # 服务请求
    # ═══════════════════════════════════════════════════════════════

    def _request_offboard(self):
        if not self.set_mode_client.wait_for_service(timeout_sec=0.0):
            return
        req = SetMode.Request()
        req.custom_mode = 'OFFBOARD'
        self.set_mode_client.call_async(req)

    def _request_arm(self, arm: bool):
        if not self.arm_client.wait_for_service(timeout_sec=0.0):
            return
        req = CommandBool.Request()
        req.value = arm
        self.arm_client.call_async(req)

    def _emergency_land(self):
        self.get_logger().error('[!] 紧急降落！→ AUTO.LAND')
        if self.set_mode_client.wait_for_service(timeout_sec=1.0):
            req = SetMode.Request()
            req.custom_mode = 'AUTO.LAND'
            self.set_mode_client.call_async(req)
        self._request_arm(False)

    # ═══════════════════════════════════════════════════════════════
    # 工具
    # ═══════════════════════════════════════════════════════════════

    def _get_yaw_angle_deg(self):
        """从AR姿态四元数提取yaw偏差角（绕相机Y轴=机体Z轴）"""
        qx, qy, qz, qw = self.ar_qx, self.ar_qy, self.ar_qz, self.ar_qw
        sin_pitch = 2.0 * (qw * qy - qz * qx)
        sin_pitch = max(-1.0, min(1.0, sin_pitch))
        return math.degrees(math.asin(sin_pitch))

    def _log_throttled(self, key: str, interval: float, msg: str):
        now = self.get_clock().now()
        last = self._last_log.get(key)
        if last is not None:
            dt = (now - last).nanoseconds / 1e9
        else:
            dt = float('inf')
        if dt >= interval:
            self._last_log[key] = now
            self.get_logger().info(msg)


def main():
    rclpy.init()
    node = ArucoSearchAlignFlight()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        print('\n[!] 用户中断')
    except Exception as e:
        print(f'[!] 异常: {e}')
        import traceback
        traceback.print_exc()
    finally:
        cv2.destroyAllWindows()
        rclpy.shutdown()
        print('节点已退出')


if __name__ == '__main__':
    main()
