#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from geometry_msgs.msg import PoseStamped
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSDurabilityPolicy
import numpy as np
class FastLIOToMavros(Node):
    def __init__(self):
        super().__init__('fastlio_to_mavros')
        # 保存最新的 Fast-LIO 位姿
        self.p_lidar_body = np.zeros(3)
        self.q_mav = [0.0, 0.0, 0.0, 1.0]
        self.received_odom = False
        # QoS 配置
        high_freq_qos = QoSProfile(
            depth=200,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.VOLATILE
        )
        qos_profile_vision = QoSProfile(
            depth=10,
            reliability=QoSReliabilityPolicy.RELIABLE,
            durability=QoSDurabilityPolicy.VOLATILE
        )
        # 订阅高频里程计
        self.create_subscription(Odometry, '/Odom_high_freq', self.vins_callback, high_freq_qos)
        # 发布视觉位姿
        self.vision_pub = self.create_publisher(PoseStamped, '/mavros/vision_pose/pose', qos_profile_vision)
        # 定时发布（100 Hz）
        self.timer = self.create_timer(0.01, self.run)
    def vins_callback(self, msg):
        self.p_lidar_body = np.array([
            msg.pose.pose.position.x,
            msg.pose.pose.position.y,
            msg.pose.pose.position.z
        ])
        self.q_mav = [
            msg.pose.pose.orientation.x,
            msg.pose.pose.orientation.y,
            msg.pose.pose.orientation.z,
            msg.pose.pose.orientation.w
        ]
        self.received_odom = True
    def run(self):
        if not self.received_odom:
            return
        vision = PoseStamped()
        vision.header.stamp = self.get_clock().now().to_msg()
        vision.header.frame_id = "map"
        vision.pose.position.x = self.p_lidar_body[0]
        vision.pose.position.y = self.p_lidar_body[1]
        vision.pose.position.z = self.p_lidar_body[2]
        vision.pose.orientation.x = self.q_mav[0]
        vision.pose.orientation.y = self.q_mav[1]
        vision.pose.orientation.z = self.q_mav[2]
        vision.pose.orientation.w = self.q_mav[3]
        self.vision_pub.publish(vision)
        self.get_logger().info(
            "Pos: x={:.3f} y={:.3f} z={:.3f} | Ori: x={:.3f} y={:.3f} z={:.3f} w={:.3f}".format(
                self.p_lidar_body[0], self.p_lidar_body[1], self.p_lidar_body[2],
                self.q_mav[0], self.q_mav[1], self.q_mav[2], self.q_mav[3]
            )
        )
def main(args=None):
    rclpy.init(args=args)
    node = FastLIOToMavros()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
if __name__ == '__main__':
    main()
