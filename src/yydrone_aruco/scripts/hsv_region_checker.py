#!/usr/bin/env python3
"""
HSV 颜色区域检测 + 入区判定节点

功能:
1. 从相机图像中用 HSV 阈值检测目标区域（如桌子上的红色区域）
2. 将检测到的轮廓作为闭合多边形
3. 接收外界输入的点位（如 ArUco 检测到的物体位置），判断是否在多边形内
4. 发布判定结果和可视化 Marker

参数 (可运行时动态调):
  h_low / h_high : 色调范围 (0-180, OpenCV 格式)
  s_low / s_high : 饱和度范围 (0-255)
  v_low / v_high : 明度范围 (0-255)
  min_area       : 最小轮廓面积 (过滤噪点)
  max_area       : 最大轮廓面积 (0=不限制)

订阅:
  /image_raw            : 相机图像 (sensor_msgs/Image)
  /check_point          : 待检测的 2D 图像坐标 (geometry_msgs/Point)

发布:
  /region_polygon       : 检测到的区域多边形 (geometry_msgs/PolygonStamped)
  /in_region            : 判定结果 (std_msgs/Bool)
  /region_debug         : 调试用可视化图像 (sensor_msgs/Image)
"""

import rclpy
import numpy as np
import cv2

from rclpy.node import Node
from sensor_msgs.msg import Image
from geometry_msgs.msg import Point, Point32, PolygonStamped
from std_msgs.msg import Bool, Header
from cv_bridge import CvBridge


class HsvRegionChecker(Node):
    def __init__(self):
        super().__init__('hsv_region_checker')

        # ── HSV 参数 ──
        self.declare_parameter('h_low',   0)
        self.declare_parameter('h_high',  10)
        self.declare_parameter('s_low',   100)
        self.declare_parameter('s_high',  255)
        self.declare_parameter('v_low',   100)
        self.declare_parameter('v_high',  255)
        self.declare_parameter('min_area', 500.0)
        self.declare_parameter('max_area', 0.0)

        self._load_params()

        self.bridge = CvBridge()

        # ── 内态 ──
        self.latest_contour = None  # 当前检测到的最大轮廓 (N,1,2) numpy
        self.box_corners_2d = None  # 箱体 4 个角点 (list of (x,y))

        # ── 发布 ──
        self.poly_pub  = self.create_publisher(PolygonStamped, '/region_polygon', 10)
        self.bool_pub  = self.create_publisher(Bool, '/in_region', 10)
        self.debug_pub = self.create_publisher(Image, '/region_debug', 10)

        # ── 订阅 ──
        self.create_subscription(Image, '/camera/color/image_raw', self.image_cb, 10)
        # 订阅箱体 4 个角点 (来自 aruco_point_bridge)
        self.create_subscription(PolygonStamped, '/box_corners_2d', self.corners_cb, 10)

        # ── 参数回调 ──
        self.add_on_set_parameters_callback(self._on_param_change)

        # ── 定时判定（即使没有新图像也周期性判定） ──
        self.create_timer(0.1, self.timer_cb)

        self.get_logger().info('HSV 区域检测器已启动')

    def _load_params(self):
        self.h_low   = self.get_parameter('h_low').value
        self.h_high  = self.get_parameter('h_high').value
        self.s_low   = self.get_parameter('s_low').value
        self.s_high  = self.get_parameter('s_high').value
        self.v_low   = self.get_parameter('v_low').value
        self.v_high  = self.get_parameter('v_high').value
        self.min_area = self.get_parameter('min_area').value
        self.max_area = self.get_parameter('max_area').value

    def _on_param_change(self, params):
        self._load_params()
        return rclpy.parameter.SetParametersResult(successful=True)

    # ── 图像回调：HSV 提取区域 ──
    def image_cb(self, msg: Image):
        try:
            frame = self.bridge.imgmsg_to_cv2(msg, 'bgr8')
        except Exception as e:
            self.get_logger().error(f'图像转换失败: {e}')
            return

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        lower = np.array([self.h_low,  self.s_low,  self.v_low])
        upper = np.array([self.h_high, self.s_high, self.v_high])
        mask = cv2.inRange(hsv, lower, upper)

        # 形态学清理
        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        self.latest_contour = None
        for c in contours:
            area = cv2.contourArea(c)
            if area < self.min_area:
                continue
            if self.max_area > 0 and area > self.max_area:
                continue
            # 保留最大轮廓
            if self.latest_contour is None or area > cv2.contourArea(self.latest_contour):
                self.latest_contour = c

        # 发布 polygon
        if self.latest_contour is not None:
            poly_msg = PolygonStamped()
            poly_msg.header.stamp = self.get_clock().now().to_msg()
            poly_msg.header.frame_id = msg.header.frame_id if msg.header.frame_id else 'camera'
            for pt in self.latest_contour:
                poly_msg.polygon.points.append(Point32(x=float(pt[0][0]), y=float(pt[0][1]), z=0.0))
            self.poly_pub.publish(poly_msg)

        # 调试图像
        debug = frame.copy()
        if self.latest_contour is not None:
            cv2.drawContours(debug, [self.latest_contour], -1, (0, 255, 0), 2)
            # 画出多边形每个角点（红色圆点）
            for pt in self.latest_contour:
                cv2.circle(debug, (int(pt[0][0]), int(pt[0][1])), 4, (0, 0, 255), -1)
            # 画出箱体 4 个角点（绿色=在内，红色=在外）
            if self.box_corners_2d is not None:
                for (px, py) in self.box_corners_2d:
                    ix, iy = int(px), int(py)
                    inside = cv2.pointPolygonTest(self.latest_contour, (px, py), False)
                    color = (0, 255, 0) if inside >= 0 else (0, 0, 255)
                    cv2.circle(debug, (ix, iy), 8, color, -1)
        self.debug_pub.publish(self.bridge.cv2_to_imgmsg(debug, 'bgr8'))

    # ── 角点回调：接收箱体 4 角点 ──
    def corners_cb(self, msg: PolygonStamped):
        pts = []
        for p in msg.polygon.points:
            pts.append((p.x, p.y))
        self.box_corners_2d = pts

    # ── 定时判定（4 个角点全部在区域内才算 IN） ──
    def timer_cb(self):
        if self.latest_contour is None or self.box_corners_2d is None:
            return

        all_inside = True
        for (px, py) in self.box_corners_2d:
            if cv2.pointPolygonTest(self.latest_contour, (px, py), False) < 0:
                all_inside = False
                break

        result = Bool()
        result.data = all_inside
        self.bool_pub.publish(result)

        # 每 5 秒打印一次状态
        if self.get_clock().now().nanoseconds % 5_000_000_000 < 100_000_000:
            n = len(self.box_corners_2d)
            status = '全部在内' if all_inside else '有角点在外'
            self.get_logger().info(
                f'{n} 个角点检测: {status}', throttle_duration_sec=5.0
            )


def main():
    rclpy.init()
    node = HsvRegionChecker()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
