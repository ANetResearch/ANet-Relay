#!/usr/bin/env python3
"""
ArUco → 箱体角点桥接节点

从 ArUco 标记的 6DOF 位姿 + 标记尺寸，推算标记 4 个角点在图像
和相机坐标系下的坐标，发布供区域判定使用。

订阅:
  /aruco_detector/aruco_pose   : 标记位姿 (PoseStamped)
  /camera/color/camera_info    : 相机内参 (CameraInfo)

发布:
  /box_corners_2d    : 标记 4 个角点的 2D 图像坐标 (PolygonStamped, 4 点)
  /box_corners_3d    : 标记 4 个角点的 3D 相机坐标 (PolygonStamped, 4 点)
  /check_point       : 箱子中心 2D 坐标 (Point) — 兼容旧版
  /check_point_3d    : 箱子中心 3D 坐标 (PointStamped) — 兼容旧版

参数:
  marker_size        : ArUco 标记的实际边长(米), default: 0.15
"""

import rclpy
import numpy as np

from rclpy.node import Node
from geometry_msgs.msg import Point, PointStamped, Point32, PolygonStamped, PoseStamped
from sensor_msgs.msg import CameraInfo


def quaternion_to_rotation(qx, qy, qz, qw):
    """四元数 → 旋转矩阵 (3x3)"""
    R = np.zeros((3, 3))
    R[0, 0] = 1 - 2*qy*qy - 2*qz*qz
    R[0, 1] = 2*qx*qy - 2*qz*qw
    R[0, 2] = 2*qx*qz + 2*qy*qw
    R[1, 0] = 2*qx*qy + 2*qz*qw
    R[1, 1] = 1 - 2*qx*qx - 2*qz*qz
    R[1, 2] = 2*qy*qz - 2*qx*qw
    R[2, 0] = 2*qx*qz - 2*qy*qw
    R[2, 1] = 2*qy*qz + 2*qx*qw
    R[2, 2] = 1 - 2*qx*qx - 2*qy*qy
    return R


class ArucoPointBridge(Node):
    def __init__(self):
        super().__init__('aruco_point_bridge')

        self.declare_parameter('marker_size', 0.15)
        self.marker_size = self.get_parameter('marker_size').value
        self.half = self.marker_size / 2.0

        # 相机内参
        self.fx = self.fy = self.cx = self.cy = 0.0
        self.has_camera_info = False

        # ── 发布 ──
        self.corners2d_pub = self.create_publisher(PolygonStamped, '/box_corners_2d', 10)
        self.corners3d_pub = self.create_publisher(PolygonStamped, '/box_corners_3d', 10)
        self.center2d_pub  = self.create_publisher(Point, '/check_point', 10)
        self.center3d_pub  = self.create_publisher(PointStamped, '/check_point_3d', 10)

        # ── 订阅 ──
        self.create_subscription(PoseStamped, '/aruco_detector/aruco_pose', self.pose_cb, 10)
        self.create_subscription(CameraInfo, '/camera/color/camera_info', self.camera_info_cb, 10)

        self.add_on_set_parameters_callback(self._on_param_change)
        self.get_logger().info(f'ArUco 角点桥接已启动, marker_size={self.marker_size}m')

    def _on_param_change(self, params):
        for p in params:
            if p.name == 'marker_size':
                self.marker_size = p.value
                self.half = self.marker_size / 2.0
                self.get_logger().info(f'marker_size 更新: {self.marker_size}')
        return rclpy.parameter.SetParametersResult(successful=True)

    def camera_info_cb(self, msg: CameraInfo):
        self.fx = msg.k[0]
        self.fy = msg.k[4]
        self.cx = msg.k[2]
        self.cy = msg.k[5]
        self.has_camera_info = True

    def pose_cb(self, msg: PoseStamped):
        if not self.has_camera_info:
            self.get_logger().warn('尚未收到 camera_info，跳过', throttle_duration_sec=5.0)
            return

        # 标记中心在相机坐标系
        tx, ty, tz = msg.pose.position.x, msg.pose.position.y, msg.pose.position.z
        if tz <= 0:
            return

        qx, qy, qz, qw = (msg.pose.orientation.x, msg.pose.orientation.y,
                           msg.pose.orientation.z, msg.pose.orientation.w)
        R = quaternion_to_rotation(qx, qy, qz, qw)

        # 标记 4 个角在标记局部坐标系（标记平面 XY，中心为原点）
        corners_local = np.array([
            [-self.half, -self.half, 0.0],
            [ self.half, -self.half, 0.0],
            [ self.half,  self.half, 0.0],
            [-self.half,  self.half, 0.0],
        ])

        # 变换到相机坐标系
        corners_cam = (R @ corners_local.T).T + np.array([tx, ty, tz])  # (4,3)

        pts2d, pts3d = [], []
        ts = self.get_clock().now().to_msg()
        fid = msg.header.frame_id

        for i in range(4):
            X, Y, Z = corners_cam[i]
            if Z <= 0:
                continue
            u = self.fx * X / Z + self.cx
            v = self.fy * Y / Z + self.cy
            pts2d.append(Point32(x=float(u), y=float(v), z=0.0))
            pts3d.append(Point32(x=float(X), y=float(Y), z=float(Z)))

        if len(pts2d) < 4:
            return

        # 发布 2D 角点
        poly2d = PolygonStamped()
        poly2d.header.stamp = ts
        poly2d.header.frame_id = fid
        poly2d.polygon.points = pts2d
        self.corners2d_pub.publish(poly2d)

        # 发布 3D 角点
        poly3d = PolygonStamped()
        poly3d.header.stamp = ts
        poly3d.header.frame_id = fid
        poly3d.polygon.points = pts3d
        self.corners3d_pub.publish(poly3d)

        # 发布中心点（兼容旧版）
        u0 = self.fx * tx / tz + self.cx
        v0 = self.fy * ty / tz + self.cy
        pt2d = Point(x=u0, y=v0, z=0.0)
        self.center2d_pub.publish(pt2d)

        pt3d = PointStamped()
        pt3d.header.stamp = ts
        pt3d.header.frame_id = fid
        pt3d.point.x, pt3d.point.y, pt3d.point.z = tx, ty, tz
        self.center3d_pub.publish(pt3d)


def main():
    rclpy.init()
    node = ArucoPointBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
