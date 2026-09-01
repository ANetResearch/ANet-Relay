#!/usr/bin/env python3
"""
AR码搜索与对准节点（手动测试版本）
=============================================
- 显示 /aruco_detector/debug_image 调试画面
- 未检测到AR码 → 竖直面(Y-Z)圆周搜索
- 检测到AR码 → 根据AR位置计算机体移动方向并打印

坐标系说明:
  相机光学坐标系: X=右 Y=下 Z=前
  机体坐标系(FRD): X=前 Y=右 Z=下
  转换: body_move = (cam_z, cam_x, cam_y)

使用方式:
  python3 aruco_search_align.py
  按 q 键退出
"""

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from geometry_msgs.msg import PoseStamped
import cv2
from cv_bridge import CvBridge
import math


class ArucoSearchAlign(Node):
    def __init__(self):
        super().__init__('aruco_search_align')

        # ── 参数 ──────────────────────────────────────────────────
        self.declare_parameter('circle_radius', 0.5)     # 搜索圆半径 (m)
        self.declare_parameter('circle_period', 5.0)     # 搜索一圈时间 (s)
        self.declare_parameter('detect_timeout', 0.5)    # AR丢失超时 (s)
        self.declare_parameter('align_deadzone', 0.05)   # 对准死区 (m)
        self.declare_parameter('yaw_deadzone_deg', 5.0) # yaw对准死区 (度)

        self.circle_radius = self.get_parameter('circle_radius').value
        self.circle_period = self.get_parameter('circle_period').value
        self.detect_timeout = self.get_parameter('detect_timeout').value
        self.align_deadzone = self.get_parameter('align_deadzone').value
        self.yaw_deadzone_deg = self.get_parameter('yaw_deadzone_deg').value

        # ── 状态变量 ──────────────────────────────────────────────
        self.bridge = CvBridge()
        self.aruco_detected = False
        self.last_pose_time = self.get_clock().now()
        self.start_time = self.get_clock().now()
        self.search_start_time = self.get_clock().now()

        # 节流日志用
        self._last_log = {}  # key -> last_log_time

        # AR码在相机系下的位置和姿态
        self.ar_x = 0.0   # 相机右
        self.ar_y = 0.0   # 相机下
        self.ar_z = 0.0   # 相机前
        self.ar_qx = 0.0
        self.ar_qy = 0.0
        self.ar_qz = 0.0
        self.ar_qw = 1.0

        # ── 订阅 ──────────────────────────────────────────────────
        self.debug_sub = self.create_subscription(
            Image,
            '/aruco_detector/debug_image',
            self.debug_cb,
            10)

        self.pose_sub = self.create_subscription(
            PoseStamped,
            '/aruco_detector/aruco_pose',
            self.pose_cb,
            10)

        # ── 定时器: 10Hz 控制循环 ─────────────────────────────────
        self.timer = self.create_timer(0.1, self.control_loop)

        # ── 启动日志 ──────────────────────────────────────────────
        self.get_logger().info('╔══════════════════════════════════════════╗')
        self.get_logger().info('║  AR码搜索对准节点 (手动测试模式)        ║')
        self.get_logger().info('╠══════════════════════════════════════════╣')
        self.get_logger().info('║  调试画面: /aruco_detector/debug_image  ║')
        self.get_logger().info('║  AR位姿:   /aruco_detector/aruco_pose   ║')
        self.get_logger().info(f'║  搜索半径: {self.circle_radius:.1f} m                       ║')
        self.get_logger().info('║  显示窗口: AR Debug View (按q退出)      ║')
        self.get_logger().info('╚══════════════════════════════════════════╝')

    # ═══════════════════════════════════════════════════════════════
    # 回调
    # ═══════════════════════════════════════════════════════════════

    def debug_cb(self, msg: Image):
        """显示AR检测调试画面"""
        try:
            frame = self.bridge.imgmsg_to_cv2(msg, 'bgr8')
        except Exception as e:
            self.get_logger().error(f'图像转换失败: {e}')
            return

        h, w = frame.shape[:2]
        cx_img, cy_img = w // 2, h // 2

        # ── 画图像中心十字线 ──────────────────────────────────
        cv2.line(frame, (cx_img - 30, cy_img), (cx_img + 30, cy_img), (0, 255, 255), 1)
        cv2.line(frame, (cx_img, cy_img - 30), (cx_img, cy_img + 30), (0, 255, 255), 1)
        cv2.circle(frame, (cx_img, cy_img), 8, (0, 255, 255), 1)

        # ── 左上角状态栏 ──────────────────────────────────────
        if self.aruco_detected:
            cv2.putText(frame,
                        f'AR DETECTED | cam_pos=({self.ar_x:.2f}, {self.ar_y:.2f}, {self.ar_z:.2f})m',
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2)
        else:
            cv2.putText(frame, 'SEARCHING (circular scan)...',
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)

        # ── 显示 ──────────────────────────────────────────────
        cv2.imshow('AR Debug View', frame)
        key = cv2.waitKey(1)
        if key == ord('q') or key == 27:  # q 或 Esc 退出
            self.get_logger().info('用户按键退出')
            raise KeyboardInterrupt

    def pose_cb(self, msg: PoseStamped):
        """AR码位姿回调（相机坐标系）"""
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
    # 节流日志
    # ═══════════════════════════════════════════════════════════════

    def _log_throttled(self, key: str, interval: float, msg: str):
        """按 key 节流打印日志，同一 key 每隔 interval 秒才打印一次"""
        now = self.get_clock().now()
        last = self._last_log.get(key, None)
        if last is None:
            dt = float('inf')
        else:
            dt = (now - last).nanoseconds / 1e9
        if dt >= interval:
            self._last_log[key] = now
            self.get_logger().info(msg)

    # ═══════════════════════════════════════════════════════════════
    # 控制循环
    # ═══════════════════════════════════════════════════════════════

    def control_loop(self):
        """10Hz 主控制循环"""
        now = self.get_clock().now()

        # 检查AR码是否丢失
        dt_pose = (now - self.last_pose_time).nanoseconds / 1e9
        if dt_pose > self.detect_timeout:
            if self.aruco_detected:
                self.get_logger().info('[!] AR码丢失，切换到搜索模式')
                self.search_start_time = now
            self.aruco_detected = False

        # 模式切换
        if self.aruco_detected:
            self.align_mode()
        else:
            self.search_mode(now)

    # ═══════════════════════════════════════════════════════════════
    # 搜索模式
    # ═══════════════════════════════════════════════════════════════

    def search_mode(self, now):
        """
        竖直面(Y-Z)圆周搜索运动

        机体坐标系(FRD):
          X = 前
          Y = 右
          Z = 下

        竖直面 = Y-Z 平面 (左右+上下扫描, 保持前向距离不变)
          圆心:  初始悬停位置
          半径:  self.circle_radius
          周期:  self.circle_period
        """
        elapsed = (now - self.search_start_time).nanoseconds / 1e9
        omega = 2.0 * math.pi / self.circle_period
        R = self.circle_radius

        # 当前位置 (相对起始点)
        pos_y = R * math.cos(omega * elapsed)   # 左右
        pos_z = R * math.sin(omega * elapsed)   # 上下

        # 切线速度 (下一时刻的运动方向)
        vel_y = -R * omega * math.sin(omega * elapsed)  # 右+
        vel_z =  R * omega * math.cos(omega * elapsed)  # 下+

        # 方向文字
        dir_y = '→右' if vel_y > 0.01 else ('←左' if vel_y < -0.01 else '·')
        dir_z = '↓下' if vel_z > 0.01 else ('↑上' if vel_z < -0.01 else '·')

        self._log_throttled('search', 0.5,
            f'[搜索] 圆周角={math.degrees(omega * elapsed)%360:5.1f}° | '
            f'偏移(相对起点): Y={pos_y:+6.2f}m Z={pos_z:+6.2f}m | '
            f'机身移动方向: {dir_y} {dir_z} | '
            f'速度: Y={vel_y:+5.2f} Z={vel_z:+5.2f} m/s')

    def _get_yaw_angle_deg(self):
        """
        从AR码姿态四元数提取yaw角（度），用于机体旋转对准

        相机光学帧: X=右 Y=下 Z=前
        机体FRD帧:  X=前 Y=右 Z=下
        body_Z(下) = cam_Y(下) → drone yaw = 绕相机Y轴的旋转

        四元数 → pitch(绕Y): sin(pitch) = 2*(qw*qy - qz*qx)
        pitch > 0 → 顺时针旋转（从上往下看）
        pitch < 0 → 逆时针旋转（从上往下看）
        """
        qx, qy, qz, qw = self.ar_qx, self.ar_qy, self.ar_qz, self.ar_qw
        sin_pitch = 2.0 * (qw * qy - qz * qx)
        sin_pitch = max(-1.0, min(1.0, sin_pitch))
        pitch_rad = math.asin(sin_pitch)
        return math.degrees(pitch_rad)

    # ═══════════════════════════════════════════════════════════════
    # 对准模式
    # ═══════════════════════════════════════════════════════════════

    def align_mode(self):
        """
        根据AR码在相机系中的位置，计算机体移动方向

        相机光学帧 → 机体帧(FRD):
          body_move_x (前)  = cam_z (前)
          body_move_y (右)  = cam_x (右)
          body_move_z (下)  = cam_y (下)

        AR码在画面中心 = AR码在相机系 X≈0, Y≈0
        不在中心 → 朝对应方向移动使AR码归中
        """
        # 相机系下的偏差
        dx_cam = self.ar_x   # AR在相机右侧 → 正值
        dy_cam = self.ar_y   # AR在相机下方 → 正值
        dz_cam = self.ar_z   # AR距离

        # 转为机体移动量
        body_fwd  = dz_cam              # 前向距离 (仅参考)
        body_y    = dx_cam              # 右移量
        body_z    = dy_cam              # 下移量

        # yaw角
        yaw_deg = self._get_yaw_angle_deg()

        # 判断对准状态
        y_aligned = abs(body_y) < self.align_deadzone
        z_aligned = abs(body_z) < self.align_deadzone
        yaw_aligned = abs(yaw_deg) < self.yaw_deadzone_deg
        aligned = y_aligned and z_aligned and yaw_aligned

        if aligned:
            self._log_throttled('align_ok', 0.5,
                f'[✓已对准] AR距离={body_fwd:.2f}m | '
                f'偏差 Y={body_y:+.3f}m Z={body_z:+.3f}m yaw={yaw_deg:+.1f}° (死区内)')
            return

        # 移动方向
        dir_y = '→右' if body_y > self.align_deadzone else \
                ('←左' if body_y < -self.align_deadzone else '·')
        dir_z = '↓下' if body_z > self.align_deadzone else \
                ('↑上' if body_z < -self.align_deadzone else '·')
        dir_x = '↑前' if body_fwd > 2.0 else \
                ('↓后' if body_fwd < 0.3 else '·')
        dir_yaw = '↻顺' if yaw_deg > self.yaw_deadzone_deg else \
                  ('↺逆' if yaw_deg < -self.yaw_deadzone_deg else '·')

        self._log_throttled('align', 0.25,
            f'[对准] AR(相机系): ({dx_cam:+.2f}, {dy_cam:+.2f}, {dz_cam:+.2f})m | '
            f'→ 机体移动: {dir_y} {dir_z} {dir_x} | 旋转: {dir_yaw} '
            f'({yaw_deg:+.1f}°) | '
            f'移动量: Y={body_y:+.2f}m Z={body_z:+.2f}m '
            f'{"(已接近)" if body_fwd < 1.0 else "(距离较远)"}')


def main():
    rclpy.init()
    node = ArucoSearchAlign()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        print('\n用户中断')
    except Exception as e:
        print(f'异常: {e}')
    finally:
        cv2.destroyAllWindows()
        rclpy.shutdown()
        print('节点已退出')


if __name__ == '__main__':
    main()
