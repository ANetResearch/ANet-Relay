#!/usr/bin/env python3
"""
2D 区域 → 3D 多边形投影节点

接收 hsv_region_checker 发布的 2D 图像区域多边形，
结合深度图投影为 3D 多边形，判定 3D 点是否在区域内。

订阅:
  /region_polygon     : 2D 图像区域多边形 (PolygonStamped, 来自 hsv_region_checker)
  /depth_image        : 对齐后的深度图 (sensor_msgs/Image)
  /check_point_3d     : 待检测的 3D 点 (PointStamped, 世界坐标)

发布:
  /region_3d_polygon  : 3D 多边形 (PolygonStamped, z 不为 0)
  /in_region_3d       : 3D 判定结果 (std_msgs/Bool)

参数:
  depth_scale     : 深度值缩放因子 (default: 1000.0, 即毫米转米)
  max_depth       : 最大有效深度(米), 超出忽略 (default: 5.0)
  margin          : 深度容差(米), 判定时 z 方向允许的浮动范围 (default: 0.15)
"""

import rclpy
import numpy as np
import cv2

from rclpy.node import Node
from sensor_msgs.msg import Image
from geometry_msgs.msg import Point, PointStamped, Point32, PolygonStamped
from std_msgs.msg import Bool
from cv_bridge import CvBridge


class DepthRegionProjector(Node):
    def __init__(self):
        super().__init__('depth_region_projector')

        self.declare_parameter('depth_scale', 1000.0)
        self.declare_parameter('max_depth', 5.0)
        self.declare_parameter('margin', 0.15)

        self.depth_scale = self.get_parameter('depth_scale').value
        self.max_depth   = self.get_parameter('max_depth').value
        self.margin      = self.get_parameter('margin').value

        self.bridge = CvBridge()

        # ── 内态 ──
        self.latest_region_2d = None   # PolygonStamped (像素坐标)
        self.latest_depth     = None   # 深度图 numpy (米)
        self.latest_check_3d  = None   # PointStamped (世界坐标)
        self.camera_k         = None   # 相机内参矩阵 [fx, fy, cx, cy]
        self.polygon_3d       = None   # 投影后的 3D 多边形顶点 list of (x,y,z)

        # ── 发布 ──
        self.poly3d_pub = self.create_publisher(PolygonStamped, '/region_3d_polygon', 10)
        self.bool3d_pub = self.create_publisher(Bool, '/in_region_3d', 10)

        # ── 订阅 ──
        self.create_subscription(PolygonStamped, '/region_polygon', self.region_cb, 10)
        self.create_subscription(Image, 'camera/depth/image_raw', self.depth_cb, 10)
        self.create_subscription(PointStamped, '/check_point_3d', self.point3d_cb, 10)

        # ── 相机参数（硬编码默认值，实际应从 /camera_info 获取） ──
        self.declare_parameter('fx', 387.23)
        self.declare_parameter('fy', 387.23)
        self.declare_parameter('cx', 321.05)
        self.declare_parameter('cy', 243.45)
        self.camera_k = np.array([self.get_parameter('fx').value,
                                   self.get_parameter('fy').value,
                                   self.get_parameter('cx').value,
                                   self.get_parameter('cy').value])

        self.add_on_set_parameters_callback(self._on_param_change)

        # ── 定时器 ──
        self.create_timer(0.1, self.timer_cb)

        self.get_logger().info('2D→3D 区域投影器已启动')

    def _on_param_change(self, params):
        for p in params:
            if p.name == 'depth_scale':
                self.depth_scale = p.value
            elif p.name == 'max_depth':
                self.max_depth = p.value
            elif p.name == 'margin':
                self.margin = p.value
        return rclpy.parameter.SetParametersResult(successful=True)

    def region_cb(self, msg: PolygonStamped):
        self.latest_region_2d = msg

    def depth_cb(self, msg: Image):
        """存储深度图并投影 3D 多边形"""
        try:
            depth_img = self.bridge.imgmsg_to_cv2(msg, '16UC1')
        except Exception:
            try:
                depth_img = self.bridge.imgmsg_to_cv2(msg, '32FC1')
            except Exception as e:
                self.get_logger().error(f'深度图格式不支持: {e}')
                return

        self.latest_depth = depth_img.astype(np.float32) / self.depth_scale  # 转米

        # 如果有 2D 区域，投影到 3D
        self._project_region()

    def point3d_cb(self, msg: PointStamped):
        self.latest_check_3d = msg

    def _project_region(self):
        """把 2D 多边形顶点投影到 3D"""
        if self.latest_region_2d is None or self.latest_depth is None:
            return

        poly_2d = self.latest_region_2d
        fx, fy, cx, cy = self.camera_k
        h, w = self.latest_depth.shape

        pts_3d = []
        for pt in poly_2d.polygon.points:
            u, v = int(pt.x), int(pt.y)
            u = np.clip(u, 0, w - 1)
            v = np.clip(v, 0, h - 1)
            z = self.latest_depth[v, u]

            if z <= 0 or z > self.max_depth:
                continue

            x = (u - cx) * z / fx
            y = (v - cy) * z / fy
            pts_3d.append((x, y, z))

        if len(pts_3d) < 3:
            self.polygon_3d = None
            return

        self.polygon_3d = pts_3d

        # 发布 3D 多边形
        out = PolygonStamped()
        out.header.stamp = self.get_clock().now().to_msg()
        out.header.frame_id = self.latest_region_2d.header.frame_id
        for p in pts_3d:
            out.polygon.points.append(Point32(x=float(p[0]), y=float(p[1]), z=float(p[2])))
        self.poly3d_pub.publish(out)

    def _point_in_3d_polygon(self):
        """判断 3D 点是否落在 3D 多边形内（投影到多边形平面 + 2D in-polygon 判定）"""
        if self.polygon_3d is None or self.latest_check_3d is None:
            return None

        pts = np.array(self.polygon_3d)  # (N, 3)

        # ── 用多边形顶点的平均 z 作为基准平面 ──
        z_mean = np.mean(pts[:, 2])

        # ── z 方向容差检查 ──
        check_z = self.latest_check_3d.point.z
        if abs(check_z - z_mean) > self.margin:
            return False

        # ── 将 3D 点投影到 XY 平面（假设桌面近似水平） ──
        #     直接比较 check_point 的 (x,y) 和 3D 多边形顶点的 (x,y)
        poly_2d = pts[:, :2].astype(np.float32)                  # (N, 2)
        pt_2d   = np.array([[self.latest_check_3d.point.x,
                              self.latest_check_3d.point.y]], dtype=np.float32)

        inside = cv2.pointPolygonTest(poly_2d, (pt_2d[0, 0], pt_2d[0, 1]), False)
        return inside >= 0

    def timer_cb(self):
        # 每次触发重新投影（保证深度图是最新的）
        if self.latest_region_2d is not None and self.latest_depth is not None:
            self._project_region()

        inside = self._point_in_3d_polygon()
        if inside is None:
            return

        result = Bool()
        result.data = inside
        self.bool3d_pub.publish(result)


def main():
    rclpy.init()
    node = DepthRegionProjector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
