#!/usr/bin/env python3
"""
安全正方形航线脚本（PX4 + MAVROS / ROS2）
起飞方式完全照搬 egoplanner_fsm.cpp：

- 用 /mavros/setpoint_raw/local 的「位置控制」（不是速度控制）
- coordinate_frame = FRAME_LOCAL_NED
- type_mask 忽略速度/加速度/偏航角速率，只用 position + yaw
- yaw = 0.0
- 起飞：发位置 (x0, y0, 目标高度)，由飞控自己爬升

流程：连接 -> 预热设定点 -> 切 OFFBOARD -> 解锁 -> 起飞到 0.7m
      -> 前进3.5m -> 左3.5m -> 后3.5m -> 右3.5m（回原点）-> AUTO.LAND 降落

正方形用位置控制 + 逐步逼近目标点（0.5 m/s）。
NED 机体朝向约定（yaw=0，机头朝前）：前=+x，左=-y，后=-x，右=+y。
"""

import math
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy, DurabilityPolicy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry
from mavros_msgs.msg import State, PositionTarget
from mavros_msgs.srv import CommandBool, SetMode


class SquareMission(Node):
    def __init__(self):
        super().__init__('square_mission')

        # ---------------- 任务参数 ----------------
        self.target_altitude = 1.0      # 起飞目标高度（米）
        self.alt_tol = 0.15             # 判定“到达高度”的误差（米），与 fsm 一致
        self.side_length = 3.5          # 正方形边长（米）
        self.move_speed = 0.5           # 水平移动速度（米/秒）
        self.corner_hold_time = 1.5     # 每个角悬停稳定时间（秒）

        # ---------------- 安全参数 ----------------
        self.arm_timeout = 10.0
        self.takeoff_timeout = 20.0
        self.land_timeout = 25.0
        self.landed_alt = 0.1
        self.landed_vz = 0.2

        # ---------------- 方形路径（相对起飞点 x0,y0）----------------
        # NED：前=+x，左=-y，后=-x，右=+y
        self.path = [
            (0.0, 0.0),                          # 起点
            (self.side_length, 0.0),             # 1 前进
            (self.side_length, -self.side_length),  # 2 左
            (0.0, -self.side_length),            # 3 后
            (0.0, 0.0),                          # 4 右（回原点）
        ]
        self.seg_index = 0               # 当前段 0..3
        self.seg_start_time = None
        self.in_corner_hold = False
        self.corner_hold_start = None

        # ---------------- 状态 ----------------
        self.current_state = State()
        self.current_x = 0.0
        self.current_y = 0.0
        self.current_z = 0.0
        self.current_vz = 0.0
        self.start_x = None              # 起飞点水平位置
        self.start_y = None
        self.has_position = False
        self.last_position_time = None

        # 阶段机：INIT -> ARM_OFFBOARD -> TAKEOFF -> SQUARE -> LAND -> DONE
        self.phase = "INIT"
        self.phase_start_time = time.time()

        # ---------------- 发布器 ----------------
        self.setpoint_pub = self.create_publisher(
            PositionTarget, '/mavros/setpoint_raw/local', 10)

        # ---------------- 订阅器（BestEffort，与 fsm 一致）----------------
        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST, depth=10,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE)
        self.state_sub = self.create_subscription(
            State, '/mavros/state', self.state_cb, qos)
        self.odom_sub = self.create_subscription(
            Odometry, '/mavros/local_position/odom', self.odom_cb, qos)
        self.pose_sub = self.create_subscription(
            PoseStamped, '/mavros/local_position/pose', self.pose_cb, qos)

        # ---------------- 服务客户端 ----------------
        self.arming_client = self.create_client(CommandBool, '/mavros/cmd/arming')
        self.set_mode_client = self.create_client(SetMode, '/mavros/set_mode')

        for client, name in ((self.arming_client, 'arming'),
                             (self.set_mode_client, 'set_mode')):
            while not client.wait_for_service(timeout_sec=1.0):
                self.get_logger().info('等待 %s 服务...' % name)

        # ---------------- 控制循环 50Hz（与 fsm 一致）----------------
        self.create_timer(0.02, self.control_loop)

        self.get_logger().info(
            '正方形航线脚本已启动：起飞 %.1fm，边长 %.1fm，速度 %.1fm/s'
            % (self.target_altitude, self.side_length, self.move_speed))

    # ------------------------------------------------------------------ #
    #  回调
    # ------------------------------------------------------------------ #
    def state_cb(self, msg):
        self.current_state = msg

    def odom_cb(self, msg):
        self.current_x = msg.pose.pose.position.x
        self.current_y = msg.pose.pose.position.y
        self.current_z = msg.pose.pose.position.z
        self.current_vz = msg.twist.twist.linear.z
        self._touch_position()

    def pose_cb(self, msg):
        self.current_x = msg.pose.position.x
        self.current_y = msg.pose.position.y
        self.current_z = msg.pose.position.z
        self._touch_position()

    def _touch_position(self):
        if not (math.isfinite(self.current_x) and
                math.isfinite(self.current_y) and
                math.isfinite(self.current_z)):
            self.get_logger().warn(
                '位置数据无效（NaN/Inf），忽略。可能 EKF 未收敛 / 无定位源',
                throttle_duration_sec=3.0)
            return
        if not self.has_position:
            self.has_position = True
            self.start_x = self.current_x
            self.start_y = self.current_y
            self.get_logger().info(
                '已获取起飞点 x=%.2f y=%.2f z=%.2f'
                % (self.current_x, self.current_y, self.current_z))
        self.last_position_time = time.time()

    # ------------------------------------------------------------------ #
    #  辅助方法
    # ------------------------------------------------------------------ #
    def _set_phase(self, phase):
        if self.phase != phase:
            self.get_logger().info('进入阶段: %s' % phase)
            self.phase = phase
            self.phase_start_time = time.time()

    def publish_position(self, x, y, z):
        """位置控制设定点（FRAME_LOCAL_NED，与 egoplanner_fsm.cpp 一致）"""
        msg = PositionTarget()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "map"
        msg.coordinate_frame = PositionTarget.FRAME_LOCAL_NED
        # 忽略速度/加速度/偏航角速率，只用位置 + yaw
        msg.type_mask = (PositionTarget.IGNORE_VX |
                         PositionTarget.IGNORE_VY |
                         PositionTarget.IGNORE_VZ |
                         PositionTarget.IGNORE_AFX |
                         PositionTarget.IGNORE_AFY |
                         PositionTarget.IGNORE_AFZ |
                         PositionTarget.IGNORE_YAW_RATE)
        msg.position.x = x
        msg.position.y = y
        msg.position.z = z
        msg.yaw = 0.0
        self.setpoint_pub.publish(msg)

    def set_mode(self, mode):
        if self.current_state.mode != mode:
            req = SetMode.Request()
            req.custom_mode = mode
            self.set_mode_client.call_async(req)

    def arm(self, value):
        req = CommandBool.Request()
        req.value = value
        self.arming_client.call_async(req)

    def position_fresh(self):
        return (self.last_position_time is not None and
                time.time() - self.last_position_time < 1.0)

    def is_landed(self):
        return self.current_z < self.landed_alt and abs(self.current_vz) < self.landed_vz

    # ------------------------------------------------------------------ #
    #  主控制循环
    # ------------------------------------------------------------------ #
    def control_loop(self):
        now = time.time()
        phase_time = now - self.phase_start_time

        # 安全兜底：已在空中却失联或丢失位置 -> 紧急 AUTO.LAND
        if self.current_state.armed and self.phase in ("TAKEOFF", "SQUARE"):
            if not self.current_state.connected or not self.position_fresh():
                self.get_logger().warn('检测到失联或位置丢失，触发紧急降落！')
                self.set_mode("AUTO.LAND")
                self._set_phase("LAND")
                return

        if self.phase == "DONE":
            return

        # ---------- INIT：等连接 + 位置，预热设定点 ----------
        if self.phase == "INIT":
            # 发当前点（保持原地），同时预热 OFFBOARD 需要的 setpoint 流
            self.publish_position(self.current_x, self.current_y, self.current_z)
            if self.current_state.connected and self.has_position:
                self._set_phase("ARM_OFFBOARD")
            return

        # ---------- ARM_OFFBOARD：切 OFFBOARD + 解锁，确认成功后起飞 ----------
        if self.phase == "ARM_OFFBOARD":
            # 持续发当前点，保持原地（满足 PX4 对 OFFBOARD 的流要求）
            self.publish_position(self.current_x, self.current_y, self.current_z)
            if phase_time >= 2.0:
                if self.current_state.mode != "OFFBOARD":
                    self.set_mode("OFFBOARD")
                if not self.current_state.armed:
                    self.arm(True)

            if self.current_state.mode == "OFFBOARD" and self.current_state.armed:
                self.get_logger().info('OFFBOARD 已生效且已解锁，开始起飞')
                self._set_phase("TAKEOFF")
            elif phase_time > self.arm_timeout:
                self.get_logger().error('切 OFFBOARD/解锁超时，中止')
                if self.current_state.armed:
                    self.set_mode("AUTO.LAND")
                    self._set_phase("LAND")
                else:
                    self._set_phase("DONE")
            return

        # ---------- TAKEOFF：发 (x0, y0, 目标高度)，飞控自己爬升 ----------
        if self.phase == "TAKEOFF":
            self.publish_position(self.start_x, self.start_y, self.target_altitude)
            self.get_logger().info(
                '起飞中：z=%.2f 目标=%.2f | x=%.2f y=%.2f'
                % (self.current_z, self.target_altitude,
                   self.current_x, self.current_y),
                throttle_duration_sec=1.0)
            if self.current_z >= self.target_altitude - self.alt_tol:
                self.get_logger().info('已到达 %.2fm，开始正方形航线' % self.current_z)
                self.seg_index = 0
                self.in_corner_hold = True
                self.corner_hold_start = now
                self._set_phase("SQUARE")
            elif phase_time > self.takeoff_timeout:
                self.get_logger().warn('起飞超时（当前 %.2fm），转降落' % self.current_z)
                self.set_mode("AUTO.LAND")
                self._set_phase("LAND")
            return

        # ---------- SQUARE：位置控制 + 逐步逼近，走正方形 ----------
        if self.phase == "SQUARE":
            if self.in_corner_hold:
                # 角点悬停稳定
                px, py = self.path[self.seg_index + 1]
                self.publish_position(self.start_x + px, self.start_y + py,
                                      self.target_altitude)
                if now - self.corner_hold_start >= self.corner_hold_time:
                    self.in_corner_hold = False
                    self.seg_start_time = now
                    self.get_logger().info(
                        '第 %d 段开始（%.1f m/s）' % (self.seg_index + 1, self.move_speed))
            else:
                # 从 path[seg_index] 到 path[seg_index+1] 线性插值，速度 0.5 m/s
                fx, fy = self.path[self.seg_index]
                tx, ty = self.path[self.seg_index + 1]
                dur = self.side_length / self.move_speed
                ratio = min(1.0, (now - self.seg_start_time) / dur)
                dx = fx + (tx - fx) * ratio
                dy = fy + (ty - fy) * ratio
                self.publish_position(self.start_x + dx, self.start_y + dy,
                                      self.target_altitude)
                if now - self.seg_start_time >= dur:
                    self.seg_index += 1
                    if self.seg_index >= len(self.path) - 1:
                        self.get_logger().info('正方形航线完成，切换到 AUTO.LAND 降落')
                        self.set_mode("AUTO.LAND")
                        self._set_phase("LAND")
                    else:
                        self.in_corner_hold = True
                        self.corner_hold_start = now
            return

        # ---------- LAND：交给飞控 AUTO.LAND，等自动落地并上锁 ----------
        if self.phase == "LAND":
            if self.current_state.mode != "AUTO.LAND":
                self.set_mode("AUTO.LAND")

            if not self.current_state.armed:
                self.get_logger().info('飞控已自动上锁，降落完成，任务结束')
                self._set_phase("DONE")
            elif phase_time > self.land_timeout:
                self.get_logger().warn(
                    'AUTO.LAND 超时（高度 %.2fm），转手动慢降兜底' % self.current_z)
                self._set_phase("MANUAL_LAND")
            return

        # ---------- MANUAL_LAND：兜底受控慢降，落地才上锁 ----------
        if self.phase == "MANUAL_LAND":
            if self.current_state.mode != "OFFBOARD":
                self.set_mode("OFFBOARD")
            # 以 0.2 m/s 逐步降低目标高度
            target_z = max(0.0, self.target_altitude - 0.2 * phase_time)
            self.publish_position(self.current_x, self.current_y, target_z)

            if self.is_landed():
                self.get_logger().info('检测到已落地，执行上锁')
                self.arm(False)
                self._set_phase("DONE")
            elif phase_time > self.land_timeout + 10.0:
                self.get_logger().error(
                    '手动慢降超时！当前高度 %.2fm 速度 %.2fm/s，强制上锁'
                    % (self.current_z, self.current_vz))
                self.arm(False)
                self._set_phase("DONE")
            return


def main():
    rclpy.init()
    node = SquareMission()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info('程序被用户中断')
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
