#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PoseStamped, Point
from mavros_msgs.srv import CommandBool, SetMode
from mavros_msgs.msg import State
from rclpy.qos import qos_profile_sensor_data
import time
import math

class DroneController(Node):
    def __init__(self):
        super().__init__('drone_controller')
        
        # 当前状态
        self.current_state = State()
        self.offboard_setpoint_counter = 0
        self.final_setpoint_counter = 0
        self.mission_complete = False
        
        # 目标高度与位置
        self.target_altitude = 1.0          # 目标高度 1米
        self.forward_distance = 2.0         # 前进距离 2米
        self.position_threshold = 0.15      # 到位判断阈值（米）

        # 当前实际位置（由里程计或视觉定位更新）
        self.current_x = 0.0
        self.current_y = 0.0
        self.current_z = 0.0

        # 任务阶段标志
        self.takeoff_complete = False
        self.forward_complete = False
        self.hover_start_time = None
        self.hover_duration = 5.0           # 前进到位后悬停5秒

        # 创建发布器 - 发布目标位置
        self.local_pos_pub = self.create_publisher(
            PoseStamped, '/mavros/setpoint_position/local', 10)

        # 创建订阅器 - 监听无人机状态
        self.state_sub = self.create_subscription(
            State, '/mavros/state', self.state_callback, 10)

        # 订阅本地位置，用于判断是否到达目标点
        self.local_pos_sub = self.create_subscription(
            PoseStamped, '/mavros/local_position/pose', self.local_pos_callback, qos_profile_sensor_data)

        # 创建服务客户端
        self.arming_client = self.create_client(CommandBool, '/mavros/cmd/arming')
        self.set_mode_client = self.create_client(SetMode, '/mavros/set_mode')
        
        # 等待服务可用
        while not self.arming_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info('等待arming服务...')
        while not self.set_mode_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info('等待set_mode服务...')
        
        # 创建定时器 - 控制循环 10Hz
        self.timer = self.create_timer(0.1, self.control_loop)
        
        self.get_logger().info('无人机控制器已启动，准备执行任务...')

    # ------------------------------------------------------------------ #
    #  回调函数
    # ------------------------------------------------------------------ #

    def state_callback(self, msg):
        """更新无人机飞控状态"""
        self.current_state = msg

    def local_pos_callback(self, msg):
        """更新当前本地位置（ENU坐标系）"""
        self.current_x = msg.pose.position.x
        self.current_y = msg.pose.position.y
        self.current_z = msg.pose.position.z

    def arm_callback(self, future):
        try:
            response = future.result()
            if response.success:
                self.get_logger().info('无人机解锁成功')
            else:
                self.get_logger().error('无人机解锁失败')
        except Exception as e:
            self.get_logger().error(f'解锁服务调用失败: {e}')

    def mode_callback(self, future):
        try:
            response = future.result()
            if response.mode_sent:
                self.get_logger().info('模式设置成功')
            else:
                self.get_logger().error('模式设置失败')
        except Exception as e:
            self.get_logger().error(f'模式设置服务调用失败: {e}')

    # ------------------------------------------------------------------ #
    #  辅助方法
    # ------------------------------------------------------------------ #

    def arm_drone(self):
        if not self.current_state.armed:
            request = CommandBool.Request()
            request.value = True
            future = self.arming_client.call_async(request)
            future.add_done_callback(self.arm_callback)

    def set_mode(self, mode):
        if self.current_state.mode != mode:
            request = SetMode.Request()
            request.custom_mode = mode
            future = self.set_mode_client.call_async(request)
            future.add_done_callback(self.mode_callback)

    def publish_setpoint(self, x, y, z, yaw_w=1.0):
        """发布目标位置（ENU坐标系，yaw保持朝前）"""
        pose = PoseStamped()
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.header.frame_id = "map"
        pose.pose.position.x = x
        pose.pose.position.y = y
        pose.pose.position.z = z
        pose.pose.orientation.w = yaw_w   # 保持初始朝向
        self.local_pos_pub.publish(pose)

    def reached(self, tx, ty, tz):
        """判断是否到达目标点（三维欧氏距离）"""
        dist = math.sqrt(
            (self.current_x - tx) ** 2 +
            (self.current_y - ty) ** 2 +
            (self.current_z - tz) ** 2
        )
        return dist < self.position_threshold

    # ------------------------------------------------------------------ #
    #  主控制循环
    # ------------------------------------------------------------------ #

    def control_loop(self):
        if self.mission_complete:
            return

        # ── 阶段1: 预发送设定点（OFFBOARD模式要求提前收到setpoint流）──
        if self.offboard_setpoint_counter < 100:
            self.publish_setpoint(0.0, 0.0, self.target_altitude)
            self.offboard_setpoint_counter += 1
            if self.offboard_setpoint_counter == 10:
                self.get_logger().info('预发送设定点中...')
            return

        # ── 阶段2: 切换OFFBOARD模式并解锁 ──────────────────────────────
        if self.offboard_setpoint_counter == 100:
            self.set_mode("OFFBOARD")
            self.arm_drone()
            self.offboard_setpoint_counter += 1
            self.get_logger().info('切换到OFFBOARD模式并解锁')
            return

        # ── 阶段3: 起飞到 1m 高度 ───────────────────────────────────────
        if not self.takeoff_complete:
            self.publish_setpoint(0.0, 0.0, self.target_altitude)

            if self.reached(0.0, 0.0, self.target_altitude):
                self.takeoff_complete = True
                self.get_logger().info(
                    f'已到达目标高度 {self.target_altitude}m，开始前进 {self.forward_distance}m')
            return

        # ── 阶段4: 前进 2m（沿机体初始朝向的 X 轴正方向）─────────────────
        if not self.forward_complete:
            target_x = self.forward_distance   # X 方向前进 2m
            self.publish_setpoint(target_x, 0.0, self.target_altitude)

            if self.reached(target_x, 0.0, self.target_altitude):
                self.forward_complete = True
                self.hover_start_time = time.time()
                self.get_logger().info(
                    f'已前进 {self.forward_distance}m，开始悬停 {self.hover_duration}s')
            return

        # ── 阶段5: 到位后悬停 ────────────────────────────────────────────
        if self.hover_start_time is not None:
            target_x = self.forward_distance
            self.publish_setpoint(target_x, 0.0, self.target_altitude)

            elapsed = time.time() - self.hover_start_time
            remaining = self.hover_duration - elapsed

            if remaining > 0:
                # 每5秒打印一次剩余时间
                if int(remaining) % 5 == 0 and int(remaining) != 0:
                    self.get_logger().info(f'悬停中... 剩余 {int(remaining)}s')
            else:
                self.get_logger().info('悬停完成，开始降落')
                self.hover_start_time = None
                self.final_setpoint_counter = 0  # 重置用于降落计数
            return

        # ── 阶段6: 原地降落 ──────────────────────────────────────────────
        target_x = self.forward_distance
        descent_rate = 0.05   # 每个控制周期下降 0.05m（约 0.5m/s @ 10Hz）
        current_altitude = max(
            0.0,
            self.target_altitude - descent_rate * self.final_setpoint_counter
        )
        self.publish_setpoint(target_x, 0.0, current_altitude)
        self.final_setpoint_counter += 1

        if current_altitude <= 0.1:
            self.get_logger().info('降落完成，任务结束，正在上锁...')
            self.mission_complete = True
            request = CommandBool.Request()
            request.value = False
            self.arming_client.call_async(request)


def main():
    rclpy.init()
    node = DroneController()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info('程序被用户中断')
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()