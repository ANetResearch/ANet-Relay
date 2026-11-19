#!/usr/bin/env python3
"""
点云 & 位姿中继节点
====================

订阅 FAST-LIO / FAST-LIO2 的原始大点云 → 降采样 → 发布紧凑 JSON，
通过 rosbridge 传给手机 APP。

发布的话题（rosbridge 可订阅）:
    /relay/cloud_json     std_msgs/String   降采样后的点云 JSON
    /relay/odom_json      std_msgs/String   位姿 JSON（几乎不占带宽）
    /relay/path_json      std_msgs/String   轨迹 JSON

参数（可调）:
    - fastlio_version: "v2" (默认, FASTLIO2_ROS2) 或 "v1" (旧版 fast_lio)
    - max_points:      降采样后最大点数（默认 400）
    - cloud_hz:        点云发布频率上限（默认 3 Hz）

用法:
    # 新版 FASTLIO2_ROS2 (namespace: /fastlio2)
    ros2 run pointcloud_relay pointcloud_relay --ros-args -p fastlio_version:=v2

    # 旧版 fast_lio (全局话题 /cloud_registered, /Odometry, /path)
    ros2 run pointcloud_relay pointcloud_relay --ros-args -p fastlio_version:=v1
"""

import json
import struct
import time
import numpy as np

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import PointCloud2
from nav_msgs.msg import Odometry, Path
from std_msgs.msg import String


class PointCloudRelay(Node):
    def __init__(self):
        super().__init__("pointcloud_relay")

        # ── 参数 ──────────────────────────────────
        self.declare_parameter("fastlio_version", "v2")
        self.declare_parameter("max_points", 400)
        self.declare_parameter("cloud_hz", 3.0)

        version = self.get_parameter("fastlio_version").value
        self.max_points = self.get_parameter("max_points").value
        self.cloud_hz = self.get_parameter("cloud_hz").value
        self.cloud_interval = 1.0 / self.cloud_hz
        self.last_cloud_time = 0.0

        # ── 根据版本选择话题 ─────────────────────
        if version == "v1":
            cloud_topic = "/cloud_registered"
            odom_topic = "/Odometry"
            path_topic = "/path"
        else:  # v2 (default)
            cloud_topic = "/fastlio2/world_cloud"
            odom_topic = "/fastlio2/lio_odom"
            path_topic = "/fastlio2/lio_path"

        # ── 订阅 FAST-LIO 话题 ───────────────────
        self.cloud_sub = self.create_subscription(
            PointCloud2, cloud_topic, self.on_cloud, 10
        )
        self.odom_sub = self.create_subscription(
            Odometry, odom_topic, self.on_odom, 10
        )
        self.path_sub = self.create_subscription(
            Path, path_topic, self.on_path, 10
        )

        # ── 发布紧凑 JSON ───────────────────────
        self.cloud_pub = self.create_publisher(String, "/relay/cloud_json", 10)
        self.odom_pub = self.create_publisher(String, "/relay/odom_json", 10)
        self.path_pub = self.create_publisher(String, "/relay/path_json", 10)

        self.get_logger().info(
            f"点云中继启动 (FAST-LIO {version}) — "
            f"降采样到 {self.max_points} 点, {self.cloud_hz} Hz\n"
            f"  订阅: {cloud_topic}, {odom_topic}, {path_topic}"
        )

    # ── 点云回调：降采样 + 压缩 JSON ────────────────

    def on_cloud(self, msg: PointCloud2):
        now = time.time()
        if now - self.last_cloud_time < self.cloud_interval:
            return  # 限流
        self.last_cloud_time = now

        try:
            points = self._parse_pointcloud2(msg)
            if len(points) == 0:
                return

            # 降采样
            if len(points) > self.max_points:
                idx = np.random.choice(len(points), self.max_points, replace=False)
                points = points[idx]

            # 紧凑 JSON: [[x,y,z], [x,y,z], ...]
            # 每个点只保留 3 位小数，大幅压缩
            compact = json.dumps(
                [[round(float(p[0]), 3), round(float(p[1]), 3), round(float(p[2]), 3)]
                 for p in points],
                separators=(",", ":"),
            )

            out = String()
            out.data = compact
            self.cloud_pub.publish(out)

        except Exception as e:
            self.get_logger().warn(f"点云处理失败: {e}")

    # ── 位姿回调 ──────────────────────────────────

    def on_odom(self, msg: Odometry):
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        data = {
            "x": round(p.x, 3),
            "y": round(p.y, 3),
            "z": round(p.z, 3),
            "qx": round(q.x, 4),
            "qy": round(q.y, 4),
            "qz": round(q.z, 4),
            "qw": round(q.w, 4),
        }
        out = String()
        out.data = json.dumps(data, separators=(",", ":"))
        self.odom_pub.publish(out)

    # ── 轨迹回调 ──────────────────────────────────

    def on_path(self, msg: Path):
        compact = json.dumps(
            [[round(p.pose.position.x, 3),
              round(p.pose.position.y, 3),
              round(p.pose.position.z, 3)]
             for p in msg.poses],
            separators=(",", ":"),
        )
        out = String()
        out.data = compact
        self.path_pub.publish(out)

    # ── 解析 PointCloud2 二进制格式 ────────────────

    def _parse_pointcloud2(self, msg: PointCloud2) -> np.ndarray:
        """从 PointCloud2 二进制数据中提取 x, y, z"""
        # 找到 x, y, z 字段的偏移和类型
        fields = {}
        for field in msg.fields:
            fields[field.name] = field

        if not all(k in fields for k in ("x", "y", "z")):
            return np.array([])

        offsets = [fields[k].offset for k in ("x", "y", "z")]
        point_step = msg.point_step
        num_points = msg.width * msg.height

        # PointCloud2 中 x,y,z 始终是 float32
        fmt = "<fff"
        xyz_size = 12
        points = np.zeros((num_points, 3), dtype=np.float32)

        data = bytearray(msg.data)
        for i in range(num_points):
            base = i * point_step
            # 如果字段是连续排列的 float32
            try:
                x, y, z = struct.unpack_from(fmt, data, base + offsets[0])
                points[i] = [x, y, z]
            except struct.error:
                continue

        # 过滤无效点（NaN, Inf, 全0）
        mask = np.isfinite(points).all(axis=1) & (np.abs(points).sum(axis=1) > 0)
        return points[mask]


def main():
    rclpy.init()
    node = PointCloudRelay()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
