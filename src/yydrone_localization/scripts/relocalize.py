#!/usr/bin/env python3
"""
调用 FAST-LIO2 localizer 的重定位服务
────────────────────────────────────
用法:
  ros2 run yydrone_localization relocalize.py <map.pcd> x y z yaw [pitch] [roll]

示例:
  # 在地图原点重定位
  ros2 run yydrone_localization relocalize.py /home/lcx/drone_ws/src/maps/global_map.pcd 0 0 0 0

  # 带偏航角的重定位
  ros2 run yydrone_localization relocalize.py /home/lcx/drone_ws/src/maps/global_map.pcd 1.5 2.0 0.3 1.57
"""

import sys
import rclpy
from rclpy.node import Node
from interface.srv import Relocalize


class RelocalizeClient(Node):
    def __init__(self):
        super().__init__("relocalize_client")
        self.client = self.create_client(Relocalize, "/localizer/relocalize")

    def call(self, pcd_path, x, y, z, yaw, pitch=0.0, roll=0.0):
        if not self.client.wait_for_service(timeout_sec=5.0):
            self.get_logger().error("等待 /localizer/relocalize 服务超时，localizer 节点是否已启动？")
            return False

        req = Relocalize.Request()
        req.pcd_path = pcd_path
        req.x = x
        req.y = y
        req.z = z
        req.yaw = yaw
        req.pitch = pitch
        req.roll = roll

        self.get_logger().info(f"加载地图: {pcd_path}")
        self.get_logger().info(f"初始位姿估计: x={x:.3f}, y={y:.3f}, z={z:.3f}, "
                               f"yaw={yaw:.3f}, pitch={pitch:.3f}, roll={roll:.3f}")

        future = self.client.call_async(req)
        rclpy.spin_until_future_complete(self, future)

        if future.result() is not None:
            response = future.result()
            if response.success:
                self.get_logger().info(" 重定位成功！localizer 正在发布 map→lidar TF")
                return True
            else:
                self.get_logger().error(f" 重定位失败: {response.message}")
                return False
        else:
            self.get_logger().error(" 服务调用失败")
            return False


def main():
    rclpy.init(args=sys.argv)
    args = sys.argv[1:]

    if len(args) < 5:
        print("用法: relocalize.py <pcd_path> <x> <y> <z> <yaw> [pitch] [roll]")
        print("示例: relocalize.py /path/to/map.pcd 0 0 0 0")
        rclpy.shutdown()
        return

    pcd_path = args[0]
    x = float(args[1])
    y = float(args[2])
    z = float(args[3])
    yaw = float(args[4])
    pitch = float(args[5]) if len(args) > 5 else 0.0
    roll = float(args[6]) if len(args) > 6 else 0.0

    client = RelocalizeClient()
    client.call(pcd_path, x, y, z, yaw, pitch, roll)
    client.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
