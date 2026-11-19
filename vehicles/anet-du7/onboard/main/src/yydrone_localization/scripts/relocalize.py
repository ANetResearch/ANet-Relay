#!/usr/bin/env python3
"""
FAST-LIO2 全局重定位工具
────────────────────────

用法1 — 命令行给初始位姿:
  ros2 run yydrone_localization relocalize.py <pcd> <x> <y> <z> <yaw> [pitch] [roll]

用法2 — RViz 2D Pose Estimate 给初始位姿:
  ros2 run yydrone_localization relocalize.py <pcd> --rviz
  然后在 RViz 里用 "2D Pose Estimate" 工具在地图上点一下当前位置
"""

import sys
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PoseWithCovarianceStamped
from interface.srv import Relocalize
import tf2_ros
import tf2_geometry_msgs  # noqa: F401 — 注册 transform 支持


class RelocalizeClient(Node):
    def __init__(self, pcd_path):
        super().__init__("relocalize_client")
        self.pcd_path = pcd_path
        self.client = self.create_client(Relocalize, "/localizer/relocalize")
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)

    def call_service(self, x, y, z, yaw, pitch=0.0, roll=0.0):
        if not self.client.wait_for_service(timeout_sec=3.0):
            self.get_logger().error(
                "等待 /localizer/relocalize 服务超时，localizer 节点是否已启动？"
            )
            return False

        req = Relocalize.Request()
        req.pcd_path = self.pcd_path
        req.x = x
        req.y = y
        req.z = z
        req.yaw = yaw
        req.pitch = pitch
        req.roll = roll

        self.get_logger().info(
            f"加载地图: {self.pcd_path}"
        )
        self.get_logger().info(
            f"初始位姿: x={x:.3f}, y={y:.3f}, z={z:.3f}, "
            f"yaw={yaw:.3f}, pitch={pitch:.3f}, roll={roll:.3f}"
        )

        future = self.client.call_async(req)
        rclpy.spin_until_future_complete(self, future)

        if future.result() is not None:
            response = future.result()
            if response.success:
                self.get_logger().info("✓ 重定位成功！localizer 正在发布 map→odom TF")
                return True
            else:
                self.get_logger().error(f"✗ 重定位失败: {response.message}")
                return False
        else:
            self.get_logger().error("✗ 服务调用失败")
            return False


class RVizRelocalizer(RelocalizeClient):
    """监听 RViz 的 /initialpose，收到后触发重定位"""

    def __init__(self, pcd_path):
        super().__init__(pcd_path)
        self.initialpose_sub = self.create_subscription(
            PoseWithCovarianceStamped,
            "/initialpose",
            self.initialpose_cb,
            10,
        )
        self.get_logger().info(
            "等待 RViz '2D Pose Estimate' ...\n"
            "  请在 RViz 中加载 PCD 地图，用 '2D Pose Estimate' 工具\n"
            "  在地图上点击无人机当前所在位置"
        )

    def initialpose_cb(self, msg: PoseWithCovarianceStamped):
        # RViz 给的位姿可能在 "map" 系，直接取 xy + yaw
        pose = msg.pose.pose
        x = pose.position.x
        y = pose.position.y
        z = pose.position.z

        # 从四元数提取 yaw
        q = pose.orientation
        siny = 2.0 * (q.w * q.z + q.x * q.y)
        cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        import math
        yaw = math.atan2(siny, cosy)

        self.get_logger().info(
            f"收到 RViz 初始位姿: x={x:.3f}, y={y:.3f}, z={z:.3f}, yaw={yaw:.3f}"
        )

        success = self.call_service(x, y, z, yaw)
        if success:
            self.get_logger().info("重定位完成，节点退出")
        raise SystemExit(0 if success else 1)


def main():
    rclpy.init(args=sys.argv)
    args = sys.argv[1:]

    if len(args) < 1:
        print("用法:")
        print("  CLI 模式 : relocalize.py <pcd> <x> <y> <z> <yaw> [pitch] [roll]")
        print("  RViz 模式: relocalize.py <pcd> --rviz")
        rclpy.shutdown()
        return

    pcd_path = args[0]
    use_rviz = "--rviz" in args

    if use_rviz:
        node = RVizRelocalizer(pcd_path)
        try:
            rclpy.spin(node)
        except SystemExit:
            pass
    else:
        if len(args) < 5:
            print("CLI 模式需要至少 5 个参数: <pcd> <x> <y> <z> <yaw>")
            rclpy.shutdown()
            return
        x = float(args[1])
        y = float(args[2])
        z = float(args[3])
        yaw = float(args[4])
        pitch = float(args[5]) if len(args) > 5 else 0.0
        roll = float(args[6]) if len(args) > 6 else 0.0

        client = RelocalizeClient(pcd_path)
        client.call_service(x, y, z, yaw, pitch, roll)

    node.destroy_node() if "node" in dir() else None
    rclpy.shutdown()


if __name__ == "__main__":
    main()
