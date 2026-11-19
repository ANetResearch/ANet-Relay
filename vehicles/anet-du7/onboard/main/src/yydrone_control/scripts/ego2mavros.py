#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from quadrotor_msgs.msg import PositionCommand
from mavros_msgs.msg import PositionTarget
from geometry_msgs.msg import PoseStamped
import math
import tf_transformations

class EgoPlannerToMavros(Node):
    def __init__(self):
        super().__init__('ego_planner_to_mavros')
        
        # 参数配置
        self.declare_parameter('drone_id', 0)
        self.drone_id = self.get_parameter('drone_id').value
        
        # 订阅ego_planner的位置指令
        ego_topic = f'drone_{self.drone_id}_planning/pos_cmd'
        self.ego_sub = self.create_subscription(
            PositionCommand,
            ego_topic,
            self.ego_callback,
            10
        )
        
        # 发布到MAVROS的位置+速度控制
        self.position_target_pub = self.create_publisher(
            PositionTarget,
            '/mavros/setpoint_raw/local',
            10
        )
        
        # 发布到MAVROS的纯位置控制（备用）
        self.position_pub = self.create_publisher(
            PoseStamped,
            '/mavros/setpoint_position/local',
            10
        )
        
        # 定时器用于持续发布设定点（PX4要求）
        self.timer = self.create_timer(0.05, self.timer_callback)  # 20Hz
        
        # 存储最新的指令
        self.last_cmd = None
        self.cmd_received = False
        
        # 坐标系配置
        self.frame_id = 'world'
        
        self.get_logger().info(f'EgoPlanner to MAVROS 转换器已启动，监听话题: {ego_topic}')
        self.get_logger().info('发布到 /mavros/setpoint_raw/local (位置+速度控制)')
    
    def ego_callback(self, msg):
        """处理ego_planner的位置指令"""
        self.last_cmd = msg
        self.cmd_received = True
        
        # 记录接收到的指令信息
        self.get_logger().debug(
            f'收到指令 - 位置: [{msg.position.x:.2f}, {msg.position.y:.2f}, {msg.position.z:.2f}] '
            f'速度: [{msg.velocity.x:.2f}, {msg.velocity.y:.2f}, {msg.velocity.z:.2f}] '
            f'偏航: {msg.yaw:.2f} rad',
            throttle_duration_seconds=2.0  # 限制日志频率
        )
    
    def timer_callback(self):
        """定时发布控制指令到MAVROS"""
        if not self.cmd_received or self.last_cmd is None:
            return
        
        try:
            # 方案1：位置+速度控制（更平滑）
            self.publish_position_velocity_target()
            
            # 方案2：纯位置控制（备用）
            # self.publish_position_only()
            
        except Exception as e:
            self.get_logger().error(f'发布控制指令时出错: {str(e)}')
    
    def publish_position_velocity_target(self):
        """发布位置+速度控制指令"""
        target_msg = PositionTarget()
        
        # 设置时间戳和坐标系
        target_msg.header.stamp = self.get_clock().now().to_msg()
        target_msg.header.frame_id = self.frame_id
        
        # 设置坐标系为局部NED坐标系
        target_msg.coordinate_frame = PositionTarget.FRAME_LOCAL_NED
        
        # 设置控制掩码：忽略加速度和偏航率，使用位置+速度+偏航角控制
        target_msg.type_mask = (PositionTarget.IGNORE_AFX | 
                               PositionTarget.IGNORE_AFY | 
                               PositionTarget.IGNORE_AFZ |
                               PositionTarget.IGNORE_YAW_RATE)
        
        # 设置位置
        target_msg.position.x = self.last_cmd.position.x
        target_msg.position.y = self.last_cmd.position.y
        target_msg.position.z = self.last_cmd.position.z
        
        # 设置速度（前馈）
        target_msg.velocity.x = self.last_cmd.velocity.x
        target_msg.velocity.y = self.last_cmd.velocity.y
        target_msg.velocity.z = self.last_cmd.velocity.z
        
        # 设置偏航角（从ego_planner获取）
        target_msg.yaw = self.last_cmd.yaw
        
        # 发布消息
        self.position_target_pub.publish(target_msg)
    
    def publish_position_only(self):
        """发布纯位置控制指令（备用方案）"""
        pose_msg = PoseStamped()
        
        # 设置时间戳和坐标系
        pose_msg.header.stamp = self.get_clock().now().to_msg()
        pose_msg.header.frame_id = self.frame_id
        
        # 设置位置
        pose_msg.pose.position.x = self.last_cmd.position.x
        pose_msg.pose.position.y = self.last_cmd.position.y
        pose_msg.pose.position.z = self.last_cmd.position.z
        
        # 设置偏航角（转换为四元数）
        q = tf_transformations.quaternion_from_euler(0, 0, self.last_cmd.yaw)
        pose_msg.pose.orientation.x = q[0]
        pose_msg.pose.orientation.y = q[1]
        pose_msg.pose.orientation.z = q[2]
        pose_msg.pose.orientation.w = q[3]
        
        # 发布消息
        self.position_pub.publish(pose_msg)
    
    def destroy_node(self):
        """节点关闭时的清理工作"""
        self.get_logger().info('EgoPlanner to MAVROS 转换器关闭')
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    
    try:
        node = EgoPlannerToMavros()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f'节点运行出错: {e}')
    finally:
        if 'node' in locals():
            node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()