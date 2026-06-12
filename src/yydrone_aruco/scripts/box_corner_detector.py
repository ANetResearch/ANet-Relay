#!/usr/bin/env python3
"""
纸箱角点检测节点 — 自适应二值化 + findContours + 滑条调试

按 's' 键保存当前参数到 ROS 参数服务器。

订阅:
  /camera/color/image_raw  : 相机彩色图像 (Image)

发布:
  /box_corners_2d          : 纸箱 4 角点像素坐标 (PolygonStamped)
  /box_debug               : 调试图像 (Image)

滑条:
  BlockSize  : 自适应阈值邻域大小 (奇数, 3~51)
  C          : 阈值减常数 (-10~10)
  Dilate     : 膨胀迭代次数 (0~10)
  Erode      : 腐蚀迭代次数 (0~10)
  MinArea    : 最小轮廓面积 (0~50000)
"""

import rclpy
import numpy as np
import cv2

from rclpy.node import Node
from sensor_msgs.msg import Image
from geometry_msgs.msg import Point32, PolygonStamped
from cv_bridge import CvBridge

WIN_NAME = 'Box Detector (s=save params, q=quit)'


class BoxCornerDetector(Node):
    def __init__(self):
        super().__init__('box_corner_detector')

        # 声明参数 + 初始值
        self.declare_parameter('block_size', 11)
        self.declare_parameter('c_val', 2)
        self.declare_parameter('dilate_iter', 2)
        self.declare_parameter('erode_iter', 1)
        self.declare_parameter('min_area', 3000)

        self.block_size  = self.get_parameter('block_size').value
        self.c_val       = self.get_parameter('c_val').value
        self.dilate_iter = self.get_parameter('dilate_iter').value
        self.erode_iter  = self.get_parameter('erode_iter').value
        self.min_area    = self.get_parameter('min_area').value

        self.bridge = CvBridge()
        self.latest_frame = None

        self.corners_pub = self.create_publisher(PolygonStamped, '/box_corners_2d', 10)
        self.debug_pub   = self.create_publisher(Image, '/box_debug', 10)
        self.create_subscription(Image, '/camera/color/image_raw', self.image_cb, 10)

        # ── 滑条窗口 ──
        cv2.namedWindow(WIN_NAME, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(WIN_NAME, 640, 480)

        def nothing(x): pass
        cv2.createTrackbar('BlockSize', WIN_NAME, self.block_size, 51, nothing)
        cv2.createTrackbar('C',        WIN_NAME, self.c_val + 10, 20, nothing)  # offset +10
        cv2.createTrackbar('Dilate',   WIN_NAME, self.dilate_iter, 10, nothing)
        cv2.createTrackbar('Erode',    WIN_NAME, self.erode_iter, 10, nothing)
        cv2.createTrackbar('MinArea',  WIN_NAME, self.min_area, 50000, nothing)

        # 定时处理
        self.create_timer(0.05, self.process)
        self.get_logger().info('纸箱角点检测器已启动 (滑条调试模式)')

    def image_cb(self, msg: Image):
        try:
            self.latest_frame = self.bridge.imgmsg_to_cv2(msg, 'bgr8')
        except Exception as e:
            self.get_logger().error(f'图像转换失败: {e}')

    def _read_trackbars(self):
        bs = cv2.getTrackbarPos('BlockSize', WIN_NAME)
        self.block_size  = max(3, bs | 1)  # 奇数且 >= 3
        self.c_val       = cv2.getTrackbarPos('C', WIN_NAME) - 10
        self.dilate_iter = cv2.getTrackbarPos('Dilate', WIN_NAME)
        self.erode_iter  = cv2.getTrackbarPos('Erode', WIN_NAME)
        self.min_area    = cv2.getTrackbarPos('MinArea', WIN_NAME)

    def _update_params(self):
        self.set_parameters([
            rclpy.parameter.Parameter('block_size',  rclpy.parameter.Parameter.Type.INTEGER, self.block_size),
            rclpy.parameter.Parameter('c_val',       rclpy.parameter.Parameter.Type.INTEGER, self.c_val),
            rclpy.parameter.Parameter('dilate_iter', rclpy.parameter.Parameter.Type.INTEGER, self.dilate_iter),
            rclpy.parameter.Parameter('erode_iter',  rclpy.parameter.Parameter.Type.INTEGER, self.erode_iter),
            rclpy.parameter.Parameter('min_area',    rclpy.parameter.Parameter.Type.INTEGER, self.min_area),
        ])

    def process(self):
        if self.latest_frame is None:
            return

        self._read_trackbars()
        frame = self.latest_frame.copy()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # ── 自适应二值化 ──
        binary = cv2.adaptiveThreshold(gray, 255,
                                       cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY_INV,
                                       self.block_size, self.c_val)

        # ── 形态学 ──
        kernel = np.ones((3, 3), np.uint8)
        if self.erode_iter > 0:
            binary = cv2.erode(binary, kernel, iterations=self.erode_iter)
        if self.dilate_iter > 0:
            binary = cv2.dilate(binary, kernel, iterations=self.dilate_iter)

        # ── 找轮廓 ──
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        best_quad = None
        best_area = 0.0

        for c in contours:
            area = cv2.contourArea(c)
            if area < self.min_area:
                continue

            rect = cv2.minAreaRect(c)
            box = cv2.boxPoints(rect)
            box = np.int32(box).reshape(-1, 1, 2)
            rect_area = cv2.contourArea(box)

            if rect_area > best_area:
                best_quad = box
                best_area = rect_area

        # ── 发布 ──
        if best_quad is not None:
            pts = best_quad.reshape(4, 2).astype(np.float32)
            s = pts.sum(axis=1)
            d = np.diff(pts, axis=1)
            ordered = np.zeros((4, 2), dtype=np.float32)
            ordered[0] = pts[np.argmin(s)]
            ordered[2] = pts[np.argmax(s)]
            ordered[1] = pts[np.argmin(d)]
            ordered[3] = pts[np.argmax(d)]

            poly = PolygonStamped()
            poly.header.stamp = self.get_clock().now().to_msg()
            poly.header.frame_id = 'camera'
            for pt in ordered:
                poly.polygon.points.append(Point32(x=float(pt[0]), y=float(pt[1]), z=0.0))
            self.corners_pub.publish(poly)

        # ── 调试画面 ──
        debug = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
        info = f'BS={self.block_size} C={self.c_val} D={self.dilate_iter} E={self.erode_iter} A={self.min_area}'
        cv2.putText(debug, info, (5, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 0), 1)
        if best_quad is not None:
            cv2.drawContours(debug, [best_quad], -1, (0, 255, 0), 3)
            for pt in best_quad:
                cv2.circle(debug, (int(pt[0][0]), int(pt[0][1])), 6, (0, 0, 255), -1)
            cv2.putText(debug, f'{best_area:.0f}px', (5, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        else:
            cv2.putText(debug, 'No box', (5, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)

        # 同时发布 ROS 调试话题
        self.debug_pub.publish(self.bridge.cv2_to_imgmsg(debug, 'bgr8'))

        cv2.imshow(WIN_NAME, debug)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('s'):
            self._update_params()
            self.get_logger().info(
                f'参数已保存: bs={self.block_size} c={self.c_val} '
                f'dilate={self.dilate_iter} erode={self.erode_iter} minArea={self.min_area}')
        elif key == ord('q'):
            self.get_logger().info('用户退出')


def main():
    rclpy.init()
    node = BoxCornerDetector()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        cv2.destroyAllWindows()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
