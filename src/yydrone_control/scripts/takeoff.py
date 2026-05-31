#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PoseStamped, Point
from mavros_msgs.srv import CommandBool, SetMode
from mavros_msgs.msg import State
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
        
        # 目标高度
        self.target_altitude = 1.0  # 3米高度
        self.takeoff_complete = False
        self.hover_start_time = None
        self.hover_duration = 10.0  # 悬停10秒
        
        # 创建发布器 - 发布目标位置
        self.local_pos_pub = self.create_publisher(PoseStamped, '/mavros/setpoint_position/local', 10)
        
        # 创建订阅器 - 监听无人机状态
        self.state_sub = self.create_subscription(State, '/mavros/state', self.state_callback, 10)
        
        # 创建服务客户端
        self.arming_client = self.create_client(CommandBool, '/mavros/cmd/arming')
        self.set_mode_client = self.create_client(SetMode, '/mavros/set_mode')
        
        # 等待服务可用
        while not self.arming_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info('等待arming服务...')
        while not self.set_mode_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info('等待set_mode服务...')
        
        # 创建定时器 - 控制循环
        self.timer = self.create_timer(0.1, self.control_loop)  # 10Hz
        
        self.get_logger().info('无人机控制器已启动，准备执行任务...')
        
    def state_callback(self, msg):
        """更新无人机状态"""
        self.current_state = msg
    def arm_drone(self):
        """解锁无人机"""
        if not self.current_state.armed:
            request = CommandBool.Request()
            request.value = True
            future = self.arming_client.call_async(request)
            future.add_done_callback(self.arm_callback)
        else:
            self.get_logger().info('无人机已解锁')
    
    def set_mode(self, mode):
        """设置飞行模式"""
        if self.current_state.mode != mode:
            request = SetMode.Request()
            request.custom_mode = mode
            future = self.set_mode_client.call_async(request)
            future.add_done_callback(self.mode_callback)
        else:
            self.get_logger().info(f'已经是{mode}模式')
    
    def arm_callback(self, future):
        """解锁回调函数"""
        try:
            response = future.result()
            if response.success:
                self.get_logger().info('无人机解锁成功')
            else:
                self.get_logger().error('无人机解锁失败')
        except Exception as e:
            self.get_logger().error(f'解锁服务调用失败: {e}')
    
    def mode_callback(self, future):
        """模式设置回调函数"""
        try:
            response = future.result()
            if response.mode_sent:
                self.get_logger().info('模式设置成功')
            else:
                self.get_logger().error('模式设置失败')
        except Exception as e:
            self.get_logger().error(f'模式设置服务调用失败: {e}')
    
    def publish_setpoint(self, x, y, z):
        """发布目标位置"""
        pose = PoseStamped()
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.header.frame_id = "base_link"
        pose.pose.position.x = x
        pose.pose.position.y = y
        pose.pose.position.z = z
        pose.pose.orientation.w = 1.0  # 无旋转
        self.local_pos_pub.publish(pose)
    
    def control_loop(self):
        """主控制循环"""
        # 如果任务已完成，直接返回
        if self.mission_complete:
            return
            
        # 阶段1: 发送设定点，准备切换到OFFBOARD模式
        if self.offboard_setpoint_counter < 100:
            self.publish_setpoint(0.0, 0.0, self.target_altitude)
            self.offboard_setpoint_counter += 1
            if self.offboard_setpoint_counter == 10:
                self.get_logger().info('发送设定点中...')
        
        # 阶段2: 切换到OFFBOARD模式并解锁
        elif self.offboard_setpoint_counter == 100:
            self.set_mode("OFFBOARD")
            self.arm_drone()
            self.offboard_setpoint_counter += 1
            self.get_logger().info('切换到OFFBOARD模式并解锁')
        
        # 阶段3: 起飞到目标高度
        elif not self.takeoff_complete:
            self.publish_setpoint(0.0, 0.0, self.target_altitude)
            
            # 检查是否到达目标高度（简单检查计数器）
            if self.offboard_setpoint_counter > 200:
                self.takeoff_complete = True
                self.hover_start_time = time.time()
                self.get_logger().info(f'已到达目标高度 {self.target_altitude}米，开始悬停')
            
            self.offboard_setpoint_counter += 1
            self.final_setpoint_counter = self.offboard_setpoint_counter
        
        # 阶段4: 悬停
        elif self.hover_start_time is not None:
            current_time = time.time()
            elapsed_time = current_time - self.hover_start_time
            
            # 持续发布当前位置以保持悬停
            self.publish_setpoint(0.0, 0.0, self.target_altitude)
            
            # 显示剩余悬停时间
            remaining_time = self.hover_duration - elapsed_time
            if remaining_time > 0:
                if int(remaining_time) % 5 == 0:  # 每5秒打印一次
                    self.get_logger().info(f'悬停中... 剩余时间: {int(remaining_time)}秒')
            else:
                self.get_logger().info('悬停完成，开始降落')
                self.hover_start_time = None
        
        # 阶段5: 降落
        else:
            # 逐步降低高度
            descent_rate = 0.05  # 米/循环
            current_altitude = max(0.0, self.target_altitude - descent_rate * (self.offboard_setpoint_counter - self.final_setpoint_counter))
            
            self.publish_setpoint(0.0, 0.0, current_altitude)
            
            # 检查是否已降落
            if current_altitude <= 0.1:
                self.get_logger().info('降落完成，任务结束')
                self.mission_complete = True
                
                # 可选: 切换回LAND模式或上锁
                # self.set_mode("AUTO.LAND")
                # 或者直接上锁
                request = CommandBool.Request()
                request.value = False
                self.arming_client.call_async(request)
            
            self.offboard_setpoint_counter += 1

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