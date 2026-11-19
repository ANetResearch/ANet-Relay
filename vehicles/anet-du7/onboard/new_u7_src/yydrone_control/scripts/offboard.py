#!/usr/bin/env python3
# offboard_hover.py

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PoseStamped, Pose, Point
from mavros_msgs.srv import CommandBool, SetMode
from mavros_msgs.msg import State
from nav_msgs.msg import Odometry
import time
import math

class OffboardHover(Node):
    def __init__(self):
        super().__init__('offboard_hover')
        
        # 当前状态
        self.current_state = State()
        self.current_pose = Pose()
        self.initial_position = Point()
        self.has_initial_position = False
        self.initial_position_set = False
        
        # 创建发布器 - 发送setpoint以维持Offboard模式
        self.setpoint_pub = self.create_publisher(PoseStamped, '/mavros/setpoint_position/local', 10)
        
        # 创建订阅器 - 监听状态和位置
        self.state_sub = self.create_subscription(State, '/mavros/state', self.state_callback, 10)
        self.local_pos_sub = self.create_subscription(Odometry, '/mavros/local_position/odom', self.local_position_callback, 10)
        
        # 创建服务客户端
        self.arming_client = self.create_client(CommandBool, '/mavros/cmd/arming')
        self.set_mode_client = self.create_client(SetMode, '/mavros/set_mode')
        
        # 等待服务
        while not self.arming_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info('等待arming服务...')
        while not self.set_mode_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info('等待set_mode服务...')
        
        # 目标高度（米）
        self.target_altitude = 1.0
        
        # 控制标志
        self.is_armed = False
        self.is_offboard = False
        self.setpoint_sent = False
        
        self.get_logger().info(f'Offboard悬停控制器已启动，目标高度: {self.target_altitude}米')
        
        # 启动定时器开始切换流程
        self.start_time = time.time()
        self.create_timer(0.1, self.control_loop)  # 10Hz控制循环
    
    def state_callback(self, msg):
        """更新无人机状态"""
        self.current_state = msg
        
        # 检查模式是否切换成功
        if msg.mode == "OFFBOARD" and not self.is_offboard:
            self.is_offboard = True
            self.get_logger().info('成功切换到OFFBOARD模式!')
        
        # 检查是否已解锁
        if msg.armed and not self.is_armed:
            self.is_armed = True
            self.get_logger().info('无人机已解锁!')
    
    def local_position_callback(self, msg):
        """更新本地位置信息"""
        self.current_pose = msg.pose.pose
        
        # 记录初始位置（仅第一次）
        if not self.has_initial_position:
            self.initial_position = self.current_pose.position
            self.has_initial_position = True
            
            # 等待一小段时间确保位置稳定
            if not self.initial_position_set:
                self.get_logger().info(f'获取到初始位置: x={self.initial_position.x:.2f}, y={self.initial_position.y:.2f}, z={self.initial_position.z:.2f}')
                self.initial_position_set = True
        
        # 定期报告当前位置
        current_time = time.time()
        if hasattr(self, 'last_position_report_time'):
            if current_time - self.last_position_report_time > 2.0:  # 每2秒报告一次
                alt = self.current_pose.position.z
                self.get_logger().info(f'当前高度: {alt:.2f}米，目标高度: {self.target_altitude}米')
                self.last_position_report_time = current_time
        else:
            self.last_position_report_time = current_time
    
    def send_hover_setpoint(self):
        """发送悬停setpoint"""
        pose = PoseStamped()
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.header.frame_id = "map"
        
        # 使用当前位置的x,y，但将z设置为目标高度
        if self.has_initial_position:
            pose.pose.position.x = self.initial_position.x
            pose.pose.position.y = self.initial_position.y
            pose.pose.position.z = self.target_altitude
        else:
            # 如果还没有获取到位置，使用默认值
            pose.pose.position.x = 0.0
            pose.pose.position.y = 0.0
            pose.pose.position.z = self.target_altitude
        
        # 保持水平方向（w=1表示无旋转）
        pose.pose.orientation.w = 1.0
        pose.pose.orientation.x = 0.0
        pose.pose.orientation.y = 0.0
        pose.pose.orientation.z = 0.0
        
        self.setpoint_pub.publish(pose)
        self.setpoint_sent = True
    
    def control_loop(self):
        """主控制循环"""
        current_time = time.time()
        elapsed_time = current_time - self.start_time
        
        # 阶段1: 发送初始setpoint（PX4要求）
        if elapsed_time < 5.0 and not self.setpoint_sent:
            self.get_logger().info('阶段1: 发送setpoint心跳...', throttle_duration_sec=2.0)
            self.send_hover_setpoint()
        
        # 阶段2: 切换到Offboard模式
        elif elapsed_time >= 5.0 and elapsed_time < 7.0:
            if not self.is_offboard and self.current_state.connected:
                self.get_logger().info('阶段2: 尝试切换到OFFBOARD模式...')
                self.set_mode("OFFBOARD")
            self.send_hover_setpoint()
        
        # 阶段3: 解锁
        elif elapsed_time >= 7.0 and elapsed_time < 9.0:
            if not self.is_armed and self.is_offboard:
                self.get_logger().info('阶段3: 尝试解锁无人机...')
                self.arm_drone()
            self.send_hover_setpoint()
        
        # 阶段4: 持续悬停
        elif elapsed_time >= 9.0:
            if self.is_armed and self.is_offboard:
                if elapsed_time < 11.0:
                    self.get_logger().info('阶段4: 保持悬停在1米高度...')
                
                # 持续发送悬停setpoint
                self.send_hover_setpoint()
                
                # 检查当前位置与目标高度的偏差
                if self.has_initial_position:
                    current_alt = self.current_pose.position.z
                    alt_error = abs(current_alt - self.target_altitude)
                    
                    if elapsed_time - getattr(self, 'last_alt_report', 0) > 5.0:
                        if alt_error < 0.1:
                            self.get_logger().info(f'✓ 悬停稳定! 当前高度: {current_alt:.2f}米，误差: {alt_error:.3f}米')
                        else:
                            self.get_logger().info(f'悬停中... 当前高度: {current_alt:.2f}米，目标: {self.target_altitude}米，误差: {alt_error:.3f}米')
                        self.last_alt_report = elapsed_time
            else:
                # 如果还没准备好，继续发送setpoint
                self.send_hover_setpoint()
    
    def set_mode(self, mode):
        """设置飞行模式"""
        req = SetMode.Request()
        req.custom_mode = mode
        future = self.set_mode_client.call_async(req)
        future.add_done_callback(self.mode_callback)
    
    def arm_drone(self):
        """解锁无人机"""
        req = CommandBool.Request()
        req.value = True
        future = self.arming_client.call_async(req)
        future.add_done_callback(self.arm_callback)
    
    def mode_callback(self, future):
        """模式设置回调"""
        try:
            response = future.result()
            if response.mode_sent:
                self.get_logger().info(f'模式切换请求已发送')
            else:
                self.get_logger().warn('模式切换失败，可能已经在该模式下')
        except Exception as e:
            self.get_logger().error(f'模式设置失败: {e}')
    
    def arm_callback(self, future):
        """解锁回调"""
        try:
            response = future.result()
            if response.success:
                self.get_logger().info('解锁请求已发送')
            else:
                self.get_logger().warn('解锁失败，可能已经解锁')
        except Exception as e:
            self.get_logger().error(f'解锁失败: {e}')

def main():
    rclpy.init()
    node = OffboardHover()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        node.get_logger().info('程序被用户中断')
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()