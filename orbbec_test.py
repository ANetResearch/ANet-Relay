#!/usr/bin/env python3
"""
相机 + 6×6 ArUco 码校准引导
- 检测 6×6 ArUco 码
- 计算旋转误差，输出"顺时针/逆时针旋转"指令
- 旋转校准后计算平移误差，输出"左/右/上/下平移"指令
- 误差小于阈值时提示"已校准"
- 按 Ctrl+C 退出

用法:
  python3 camera_calibrate.py              # 有显示器时使用
  python3 camera_calibrate.py --headless   # 无显示器(SSH/无人机)时使用，仅输出控制台文字
  python3 camera_calibrate.py --camera /dev/video2  # 指定摄像头设备
"""

import os
import sys
import cv2
import numpy as np

# ---------- 校准参数 ----------
ROTATION_THRESHOLD_DEG = 2.0   # 旋转误差阈值(度)
TRANSLATION_THRESHOLD_PX = 10  # 平移误差阈值(像素)

# ---------- ArUco 检测器 ----------
ARUCO_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_6X6_250)
ARUCO_PARAMS = cv2.aruco.DetectorParameters()
ARUCO_DETECTOR = cv2.aruco.ArucoDetector(ARUCO_DICT, ARUCO_PARAMS)


def detect_aruco(frame_bgr):
    """
    检测 6×6 ArUco 码，返回检测信息
    返回:
        detected: bool
        corners: (4,2) 四个角点，顺序 TL,TR,BR,BL
        center: (2,) 中心点坐标
        correction_angle_deg: 相机需要旋转的角度（正=逆时针，负=顺时针）
        translation_px: (dx, dy) 码中心相对于图像中心的偏移（像素）
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    corners, ids, _ = ARUCO_DETECTOR.detectMarkers(gray)

    if ids is None or len(ids) == 0:
        return False, None, None, None, None

    # 使用第一个检测到的码
    marker_corners = corners[0][0]  # (4,2)
    center = marker_corners.mean(axis=0)

    # 计算顶边方向（角点 0 -> 1）
    p0, p1 = marker_corners[0], marker_corners[1]
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    angle_rad = np.arctan2(dy, dx)          # 顶边与水平轴夹角
    angle_deg = np.degrees(angle_rad)       # 正表示逆时针倾斜
    correction_angle = -angle_deg           # 相机需要旋转的角度

    # 平移误差（图像坐标系：x 右，y 下）
    h, w = frame_bgr.shape[:2]
    img_center = np.array([w / 2.0, h / 2.0])
    translation = center - img_center       # dx > 0 码在右侧，dy > 0 码在下侧

    return True, marker_corners, center, correction_angle, translation


def generate_instructions(correction_angle, translation_px):
    """
    根据误差生成校准指令字符串
    返回: (rot_msg, trans_msg, is_aligned)
    """
    # 旋转指令
    if abs(correction_angle) > ROTATION_THRESHOLD_DEG:
        direction = "逆时针" if correction_angle > 0 else "顺时针"
        rot_msg = f"请{direction}旋转 {abs(correction_angle):.1f}°"
        rot_ok = False
    else:
        rot_msg = "旋转已校准"
        rot_ok = True

    dx, dy = translation_px

    # 平移指令（旋转未校准时暂不显示平移）
    if not rot_ok:
        trans_msg = "待旋转校准后进行平移"
        aligned = False
    else:
        lr_msg = ""
        if abs(dx) > TRANSLATION_THRESHOLD_PX:
            lr_msg = f"{'左' if dx > 0 else '右'}移 {abs(dx):.0f} px"
        else:
            lr_msg = "水平已居中"

        ud_msg = ""
        if abs(dy) > TRANSLATION_THRESHOLD_PX:
            ud_msg = f"{'上' if dy > 0 else '下'}移 {abs(dy):.0f} px"
        else:
            ud_msg = "垂直已居中"

        trans_msg = f"{lr_msg}, {ud_msg}"
        aligned = (abs(dx) <= TRANSLATION_THRESHOLD_PX and
                   abs(dy) <= TRANSLATION_THRESHOLD_PX)

    return rot_msg, trans_msg, rot_ok and aligned


def draw_overlay(frame_bgr, corners, center, rot_msg, trans_msg, aligned):
    """在图像上绘制检测结果和指令"""
    vis = frame_bgr.copy()
    h, w = vis.shape[:2]

    # 绘制图像中心十字
    cx, cy = w // 2, h // 2
    cv2.line(vis, (cx - 20, cy), (cx + 20, cy), (200, 200, 200), 1)
    cv2.line(vis, (cx, cy - 20), (cx, cy + 20), (200, 200, 200), 1)
    cv2.circle(vis, (cx, cy), 5, (200, 200, 200), 1)

    # 绘制 ArUco 边框和方向线
    if corners is not None:
        pts = corners.astype(int)
        for i in range(4):
            cv2.line(vis, tuple(pts[i]), tuple(pts[(i + 1) % 4]), (0, 255, 0), 2)

        # 中心点
        mc = center.astype(int)
        cv2.circle(vis, tuple(mc), 6, (0, 0, 255), -1)

        # 顶边方向线（放大显示）
        p0, p1 = pts[0], pts[1]
        direction = (p1 - p0).astype(float)
        norm = np.linalg.norm(direction)
        if norm > 1e-3:
            direction /= norm
            arrow_end = (mc + direction * 50).astype(int)
            cv2.arrowedLine(vis, tuple(mc), tuple(arrow_end),
                            (255, 100, 0), 2, tipLength=0.3)

    # 文字信息（左上角）
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.55
    y0 = 25
    dy = 25
    shadow = (0, 0, 0)

    def put_text(text, row, color=(255, 255, 255)):
        y = y0 + row * dy
        cv2.putText(vis, text, (9, y + 1), font, font_scale, shadow, 2)
        cv2.putText(vis, text, (8, y), font, font_scale, color, 1)

    put_text(f"旋转: {rot_msg}", 0, (0, 255, 255))
    put_text(f"平移: {trans_msg}", 1, (0, 255, 255))

    # 校准状态
    if aligned:
        put_text(">>> 已校准 <<<", 3, (0, 255, 0))
        cv2.rectangle(vis, (2, 2), (w - 2, h - 2), (0, 255, 0), 3)

    return vis


def _camera_backends():
    if sys.platform.startswith("win"):
        return [cv2.CAP_DSHOW, cv2.CAP_MSMF, None]
    if sys.platform.startswith("linux"):
        return [cv2.CAP_V4L2, None]
    return [None]


def _try_open(device):
    for backend in _camera_backends():
        cap = cv2.VideoCapture(device) if backend is None else cv2.VideoCapture(device, backend)
        if cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None:
                return cap
        cap.release()
    return None


def open_camera(device=None):
    """
    打开相机，按平台自动选择后端。
    Windows: CAP_DSHOW / CAP_MSMF
    Linux:   CAP_V4L2
    """
    if device is not None:
        cap = _try_open(device)
        if cap is not None:
            print(f"相机已打开: {device}")
            return cap
        print(f"无法打开指定设备: {device}")
        return None

    for idx in range(6):
        cap = _try_open(idx)
        if cap is not None:
            print(f"相机已打开: index {idx}")
            return cap
    return None


def main():
    # ---------- 解析命令行参数 ----------
    headless = "--headless" in sys.argv
    camera_arg = None
    for i, arg in enumerate(sys.argv):
        if arg == "--camera" and i + 1 < len(sys.argv):
            camera_arg = sys.argv[i + 1]
            try:
                camera_arg = int(camera_arg)
            except ValueError:
                pass  # 保持为字符串
            break

    # 检查是否有显示器（用于自动判断 headless）
    display = os.environ.get("DISPLAY", "")
    if not headless and not display:
        print("[INFO] 未检测到 DISPLAY 环境变量，自动启用 headless 模式")
        print("[INFO] 使用 --headless 可消除此提示")
        headless = True

    # ---------- 打开相机 ----------
    print("正在打开相机...")
    cap = open_camera(camera_arg)
    if cap is None:
        print("无法打开相机，请检查:")
        print("  1. 摄像头是否已连接")
        print("  2. 运行: ls /dev/video*  查看设备列表")
        print("  3. 使用 --camera /dev/videoX 指定设备")
        return

    mode_str = "headless (仅控制台)" if headless else "GUI 窗口"
    print(f"检测 6×6 ArUco 码... 按 Ctrl+C 退出  [{mode_str}]")

    try:
        while True:
            # 取帧
            ret, frame_bgr = cap.read()
            if not ret or frame_bgr is None:
                continue

            # 检测 ArUco
            detected, corners, center, corr_angle, trans_px = detect_aruco(frame_bgr)

            if detected:
                rot_msg, trans_msg, aligned = generate_instructions(corr_angle, trans_px)
                status = " *** 已校准 ***" if aligned else ""
                print(f"\r旋转:{rot_msg} | 平移:{trans_msg}{status}   ", end="")
            else:
                rot_msg = "未检测到 ArUco 码"
                trans_msg = ""
                aligned = False
                corners = None
                center = None
                print("\r未检测到 ArUco 码     ", end="")

            # 显示
            if headless:
                # headless 模式：跳过 GUI，仅控制台输出
                pass
            else:
                vis = draw_overlay(frame_bgr, corners, center, rot_msg, trans_msg, aligned)
                cv2.imshow("ArUco Calibration Guide", vis)
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord('q')):
                    break

    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if not headless:
            cv2.destroyAllWindows()
        print("\n已退出")


if __name__ == "__main__":
    main()
