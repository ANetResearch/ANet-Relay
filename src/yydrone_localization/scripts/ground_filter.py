#!/usr/bin/env python3
"""
地面点云滤波节点 (PassThrough Z-axis filter)

订阅 FAST-LIO 输出的世界坐标系点云 (/cloud_registered)，
滤除地面高度以下的点，发布滤波后的点云 (/cloud_registered_filter)。

原理：点云已经在世界坐标系下，地面 = z 小于某个高度。
对于 45° 倾斜安装的雷达，世界坐标系下的地面高度是固定的，
因此直接按 z 轴做 PassThrough 即可。

参数:
  ground_height : 地面高度阈值（米），低于此高度的点被滤除 (default: 0.2)
  filter_limit_max : z 轴上限（米），高于此值的点也滤除 (default: 30.0)
  use_negative   : True=保留阈值内的点, False=保留阈值外的点 (default: False)
"""

import rclpy
import numpy as np

from rclpy.node import Node
from sensor_msgs.msg import PointCloud2
from sensor_msgs_py import point_cloud2


class GroundFilterNode(Node):
    def __init__(self):
        super().__init__('ground_filter')

        # ── 参数 ──
        self.declare_parameter('ground_height', 0.2)
        self.declare_parameter('filter_limit_max', 30.0)
        self.declare_parameter('use_negative', False)

        self.ground_height = self.get_parameter('ground_height').value
        self.filter_limit_max = self.get_parameter('filter_limit_max').value
        self.use_negative = self.get_parameter('use_negative').value

        self.get_logger().info(
            f'Ground filter initialized: z in ({self.ground_height}, {self.filter_limit_max}), '
            f'use_negative={self.use_negative}'
        )

        # ── 订阅 / 发布 ──
        # 使用 RELIABLE QoS，和 FAST-LIO 发布端匹配
        qos = rclpy.qos.QoSProfile(
            depth=10,
            reliability=rclpy.qos.ReliabilityPolicy.RELIABLE,
            durability=rclpy.qos.DurabilityPolicy.VOLATILE,
        )

        self.sub_ = self.create_subscription(
            PointCloud2, '/cloud_registered',
            self.cloud_callback, qos
        )
        self.pub_ = self.create_publisher(
            PointCloud2, '/cloud_registered_filter', 10
        )

        # 参数变更回调（运行时动态调参）
        self.add_on_set_parameters_callback(self._on_param_change)

        # 统计
        self.total_received_ = 0

    def _on_param_change(self, params):
        for p in params:
            if p.name == 'ground_height':
                self.ground_height = p.value
                self.get_logger().info(f'ground_height updated to {self.ground_height}')
            elif p.name == 'filter_limit_max':
                self.filter_limit_max = p.value
                self.get_logger().info(f'filter_limit_max updated to {self.filter_limit_max}')
            elif p.name == 'use_negative':
                self.use_negative = p.value
                self.get_logger().info(f'use_negative updated to {self.use_negative}')
        return rclpy.parameter.SetParametersResult(successful=True)

    def cloud_callback(self, msg: PointCloud2):
        self.total_received_ += 1

        # 只读取 x, y, z 字段 → 返回 (N, 3) 非结构化 numpy 数组
        try:
            pts = point_cloud2.read_points_numpy(msg, field_names=['x', 'y', 'z'])
        except Exception as e:
            self.get_logger().error(f'Failed to read point cloud: {e}')
            return

        if len(pts) == 0:
            return

        # ── PassThrough 滤波 ──
        # pts 是 (N, 3) 数组: [x, y, z]
        z = pts[:, 2]

        if self.use_negative:
            # 保留阈值范围之外的（滤除范围内的）
            mask = (z < self.ground_height) | (z > self.filter_limit_max)
        else:
            # 保留阈值范围之内的（滤除范围外的）
            mask = (z >= self.ground_height) & (z <= self.filter_limit_max)

        filtered = pts[mask]

        if len(filtered) == 0:
            return

        # ── 发布 ──
        filtered_msg = point_cloud2.create_cloud_xyz32(msg.header, filtered)
        self.pub_.publish(filtered_msg)

        # 每 100 帧打印一次统计
        if self.total_received_ % 100 == 0:
            ratio = 100.0 * len(filtered) / len(pts) if len(pts) > 0 else 0.0
            self.get_logger().info(
                f'Frame {self.total_received_}: '
                f'{len(pts)} → {len(filtered)} points '
                f'({ratio:.1f}% retained)'
            )


def main():
    rclpy.init()
    node = GroundFilterNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
