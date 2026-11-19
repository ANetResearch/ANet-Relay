#!/usr/bin/env python3
"""
drone_health_monitor.py — 无人机定位健康监控节点

监测三个独立信号，任一异常即触发告警：

  信号1 — 点云 PCA 退化率（预测性）
    对 /livox/lidar 的每一帧做 PCA 分解，
    最小特征值 / 最大特征值 < 阈值 → 场景结构贫瘠，LIO 即将/已经退化

  信号2 — LIO 速度异常检测（检测性）
    从 /Odom_high_freq 计算瞬时速度，
    速度幅值突跳超过物理极限 → LIO 已经漂了

  信号3 — PX4 EKF2 协方差膨胀（确认性）
    订阅 /mavros/local_position/pose_cov，
    x/y 方差持续增大 → 飞控自己对定位也不确定了

发布:
  /drone_safety_status  (std_msgs/String)
  格式: "LEVEL:<N>|msg:<诊断信息>|pca:<ratio>|vel:<m/s>|ekf2_x:<var>|ekf2_y:<var>"
  其中 LEVEL: 0=正常, 1=退化警告, 2=确认漂移, 3=紧急

  同时发布:
  /drone_safety_level  (std_msgs/Int8)  仅等级数字，方便 FSM 直接订阅
"""

import rclpy
import numpy as np

from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from sensor_msgs.msg import PointCloud2, Imu
from sensor_msgs_py import point_cloud2
from nav_msgs.msg import Odometry
from geometry_msgs.msg import PoseWithCovarianceStamped
from std_msgs.msg import String, Int8


# ── 参数默认值 ──────────────────────────────────────────────
PCA_DEGENERACY_THRESHOLD   = 0.02    # PCA ratio 低于此值 → 退化
PCA_FRAME_INTERVAL         = 5       # 每 N 帧做一次 PCA（降低计算负载）
PCA_VOXEL_SIZE             = 0.5     # 降采样体素大小 (m)
PCA_MIN_POINTS             = 100     # 最少点数
LIO_VELOCITY_JUMP_THRESHOLD = 5.0    # LIO 速度突跳阈值 (m/s)
LIO_VELOCITY_JUMP_WINDOW   = 3       # 连续 N 帧超阈值才触发
LIO_POSITION_LIMIT         = 500.0   # LIO 位置绝对值超过此范围 (m) → 直接 EMERGENCY
EKF2_VARIANCE_HIGH          = 10.0   # EKF2 pos_x/y 方差高 → DEGRADED
EKF2_VARIANCE_CRITICAL      = 50.0   # EKF2 pos_x/y 方差极高 → EMERGENCY
HYSTERESIS_UP              = 5       # 连续 N 帧异常才升级
HYSTERESIS_DOWN            = 10      # 连续 N 帧正常才降级
PUBLISH_RATE               = 10.0    # 状态发布频率 (Hz)


class DroneHealthMonitor(Node):
    """无人机定位健康监控节点"""

    def __init__(self):
        super().__init__('drone_health_monitor')

        self.declare_params()

        # QoS profiles
        self._best_effort = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        self._reliable = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )

        # ── 订阅 ──────────────────────────────────────────
        self._lidar_sub = self.create_subscription(
            PointCloud2, '/livox/lidar',
            self._lidar_callback, self._best_effort
        )
        self._odom_sub = self.create_subscription(
            Odometry, '/Odom_high_freq',
            self._odom_callback, self._reliable
        )
        self._ekf2_cov_sub = self.create_subscription(
            PoseWithCovarianceStamped, '/mavros/local_position/pose_cov',
            self._ekf2_callback, self._best_effort
        )

        # ── 发布 ──────────────────────────────────────────
        self._status_pub = self.create_publisher(String, '/drone_safety_status', 10)
        self._level_pub = self.create_publisher(Int8, '/drone_safety_level', 10)

        self._publish_timer = self.create_timer(
            1.0 / PUBLISH_RATE, self._publish_callback
        )

        # ── 状态变量 ──────────────────────────────────────
        self._last_odom_stamp = None
        self._last_odom_pos = np.zeros(3)
        self._odom_init_count = 0     # 跳过前 N 帧，等时间戳稳定

        self._lidar_frame_count = 0

        self._ekf2_cov_x = 0.0
        self._ekf2_cov_y = 0.0

        # ── 信号当前值 ────────────────────────────────────
        self._pca_ratio = 1.0
        self._lio_vel_mag = 0.0
        self._lio_pos_mag = 0.0        # LIO 位置距离原点的距离

        # ── 退化计数器（滞回） ────────────────────────────
        self._pca_bad_counter = 0
        self._velocity_bad_counter = 0
        self._ekf2_warn_counter = 0
        self._ekf2_crit_counter = 0

        # ── 安全等级滞回 ──────────────────────────────────
        self._current_level = 0   # 0=HEALTHY, 1=DEGRADED, 2=DRIFTING, 3=EMERGENCY
        self._upgrade_count = 0
        self._downgrade_count = 0

        self._level_names = ['HEALTHY', 'DEGRADED', 'DRIFTING', 'EMERGENCY']

        self.get_logger().info(
            f'drone_health_monitor 已启动 | '
            f'PCA阈值={PCA_DEGENERACY_THRESHOLD:.3f}, '
            f'速度突跳阈值={LIO_VELOCITY_JUMP_THRESHOLD:.1f}m/s'
        )

    # ── 参数声明 ───────────────────────────────────────────
    def declare_params(self):
        self.declare_parameter('pca_degeneracy_threshold', PCA_DEGENERACY_THRESHOLD)
        self.declare_parameter('pca_frame_interval', PCA_FRAME_INTERVAL)
        self.declare_parameter('pca_voxel_size', PCA_VOXEL_SIZE)
        self.declare_parameter('pca_min_points', PCA_MIN_POINTS)
        self.declare_parameter('lio_velocity_jump_threshold', LIO_VELOCITY_JUMP_THRESHOLD)
        self.declare_parameter('lio_velocity_jump_window', LIO_VELOCITY_JUMP_WINDOW)
        self.declare_parameter('ekf2_variance_high', EKF2_VARIANCE_HIGH)
        self.declare_parameter('ekf2_variance_critical', EKF2_VARIANCE_CRITICAL)
        self.declare_parameter('hysteresis_up', HYSTERESIS_UP)
        self.declare_parameter('hysteresis_down', HYSTERESIS_DOWN)

    @property
    def _pca_threshold(self):
        return self.get_parameter('pca_degeneracy_threshold').value

    @property
    def _pca_interval(self):
        return self.get_parameter('pca_frame_interval').value

    @property
    def _pca_voxel(self):
        return self.get_parameter('pca_voxel_size').value

    @property
    def _pca_min_pts(self):
        return self.get_parameter('pca_min_points').value

    @property
    def _vel_jump_threshold(self):
        return self.get_parameter('lio_velocity_jump_threshold').value

    @property
    def _vel_jump_window(self):
        return self.get_parameter('lio_velocity_jump_window').value

    @property
    def _ekf2_high(self):
        return self.get_parameter('ekf2_variance_high').value

    @property
    def _ekf2_crit(self):
        return self.get_parameter('ekf2_variance_critical').value

    @property
    def _hyst_up(self):
        return self.get_parameter('hysteresis_up').value

    @property
    def _hyst_down(self):
        return self.get_parameter('hysteresis_down').value

    # ── 点云 PCA 退化检测 ──────────────────────────────────
    def _lidar_callback(self, msg: PointCloud2):
        self._lidar_frame_count += 1
        if self._lidar_frame_count % self._pca_interval != 0:
            return

        try:
            pts = point_cloud2.read_points_numpy(msg, field_names=['x', 'y', 'z'])
        except Exception:
            return

        if len(pts) < self._pca_min_pts:
            return

        # 体素降采样
        pts_down = _voxel_downsample(pts, self._pca_voxel)
        if len(pts_down) < self._pca_min_pts:
            return

        # PCA
        pts_centered = pts_down - pts_down.mean(axis=0)
        cov = pts_centered.T @ pts_centered / (len(pts_centered) - 1)
        try:
            eigvals, _ = np.linalg.eigh(cov)
        except np.linalg.LinAlgError:
            return

        max_eig = eigvals.max()
        min_eig = eigvals.min()
        if max_eig < 1e-10:
            return

        self._pca_ratio = float(min_eig / max_eig)

        if self._pca_ratio < self._pca_threshold:
            self._pca_bad_counter += 1
        else:
            self._pca_bad_counter = max(0, self._pca_bad_counter - 1)

    # ── LIO 速度突跳检测 ───────────────────────────────────
    def _odom_callback(self, msg: Odometry):
        # 跳过前 5 帧让时间戳序列稳定
        self._odom_init_count += 1
        if self._odom_init_count <= 5:
            self._last_odom_pos = np.array([
                msg.pose.pose.position.x,
                msg.pose.pose.position.y,
                msg.pose.pose.position.z,
            ])
            self._last_odom_stamp = rclpy.time.Time.from_msg(msg.header.stamp)
            return

        pos = np.array([
            msg.pose.pose.position.x,
            msg.pose.pose.position.y,
            msg.pose.pose.position.z,
        ])
        stamp = rclpy.time.Time.from_msg(msg.header.stamp)

        if self._last_odom_stamp is not None:
            dt = (stamp - self._last_odom_stamp).nanoseconds * 1e-9
            if 1e-6 < dt < 1.0:   # 合理范围: 1us ~ 1s
                vel = (pos - self._last_odom_pos) / dt
                self._lio_vel_mag = float(np.linalg.norm(vel))

                if self._lio_vel_mag > self._vel_jump_threshold:
                    self._velocity_bad_counter += 1
                else:
                    self._velocity_bad_counter = max(0, self._velocity_bad_counter - 1)

        self._last_odom_stamp = stamp
        self._last_odom_pos = pos
        self._lio_pos_mag = float(np.linalg.norm(pos))

    # ── EKF2 协方差膨胀检测 ────────────────────────────────
    def _ekf2_callback(self, msg: PoseWithCovarianceStamped):
        cov = msg.pose.covariance
        self._ekf2_cov_x = cov[0]
        self._ekf2_cov_y = cov[7]

        if self._ekf2_cov_x > self._ekf2_crit or self._ekf2_cov_y > self._ekf2_crit:
            self._ekf2_crit_counter += 1
            self._ekf2_warn_counter += 1
        elif self._ekf2_cov_x > self._ekf2_high or self._ekf2_cov_y > self._ekf2_high:
            self._ekf2_crit_counter = max(0, self._ekf2_crit_counter - 1)
            self._ekf2_warn_counter += 1
        else:
            self._ekf2_crit_counter = max(0, self._ekf2_crit_counter - 1)
            self._ekf2_warn_counter = max(0, self._ekf2_warn_counter - 1)

    # ── 综合评估 ──────────────────────────────────────────
    def _evaluate(self):
        """
        三信号投票:
          - EMERGENCY: 速度突跳持续 或 EKF2协方差爆炸
          - DRIFTING:  PCA退化 + EKF2协方差高
          - DEGRADED:  仅 PCA退化 或 仅 EKF2警告
          - HEALTHY:   全正常
        """
        pca_drift = self._pca_bad_counter >= self._hyst_up
        vel_drift = self._velocity_bad_counter >= self._vel_jump_window
        pos_insane = self._lio_pos_mag > LIO_POSITION_LIMIT
        ekf2_crit = self._ekf2_crit_counter >= self._hyst_up
        ekf2_warn = self._ekf2_warn_counter >= self._hyst_up

        msgs = []

        if pos_insane:
            msgs.append(f'位置离谱:{self._lio_pos_mag:.0f}m>{LIO_POSITION_LIMIT}m')

        if vel_drift:
            msgs.append(f'速度突跳:{self._lio_vel_mag:.1f}m/s')

        if ekf2_crit:
            msgs.append(f'EKF2协方差爆炸:x={self._ekf2_cov_x:.1f},y={self._ekf2_cov_y:.1f}')

        if pca_drift:
            msgs.append(f'PCA退化:{self._pca_ratio:.4f}')

        if ekf2_warn and not ekf2_crit:
            msgs.append(f'EKF2不确定:x={self._ekf2_cov_x:.1f},y={self._ekf2_cov_y:.1f}')

        # 投票逻辑
        if pos_insane or vel_drift or ekf2_crit:
            level = 3  # EMERGENCY
        elif pca_drift and ekf2_warn:
            level = 2  # DRIFTING
        elif pca_drift or ekf2_warn:
            level = 1  # DEGRADED
        else:
            level = 0  # HEALTHY

        message = '定位漂移！！！' if level >= 2 else ('定位退化' if level == 1 else '正常')
        detail = ' | '.join(msgs) if msgs else 'OK'

        return level, message, detail

    # ── 定时发布 ──────────────────────────────────────────
    def _publish_callback(self):
        target_level, message, detail = self._evaluate()

        # 滞回逻辑
        if target_level > self._current_level:
            self._upgrade_count += 1
            self._downgrade_count = 0
            if self._upgrade_count >= self._hyst_up:
                if self._current_level != target_level:
                    self.get_logger().warn(
                        f'⚠ 定位状态升级: {self._level_names[self._current_level]} → '
                        f'{self._level_names[target_level]} | {detail}'
                    )
                self._current_level = target_level
                self._upgrade_count = 0
        elif target_level < self._current_level:
            self._downgrade_count += 1
            self._upgrade_count = 0
            if self._downgrade_count >= self._hyst_down:
                if self._current_level != target_level:
                    self.get_logger().info(
                        f'✓ 定位状态恢复: {self._level_names[self._current_level]} → '
                        f'{self._level_names[target_level]} | {detail}'
                    )
                self._current_level = target_level
                self._downgrade_count = 0
        else:
            self._upgrade_count = max(0, self._upgrade_count - 1)
            self._downgrade_count = max(0, self._downgrade_count - 1)

        # 发布 String（人类可读）
        status_str = (
            f'LEVEL:{self._current_level}|'
            f'msg:{message}|'
            f'detail:{detail}|'
            f'pca:{self._pca_ratio:.4f}|'
            f'vel:{self._lio_vel_mag:.1f}|'
            f'pos:{self._lio_pos_mag:.0f}|'
            f'ekf2_x:{self._ekf2_cov_x:.2f}|'
            f'ekf2_y:{self._ekf2_cov_y:.2f}'
        )
        self._status_pub.publish(String(data=status_str))

        # 发布 Int8（FSM 可直接 switch）
        lvl_msg = Int8()
        lvl_msg.data = self._current_level
        self._level_pub.publish(lvl_msg)

        # 定期日志
        if self._lidar_frame_count % int(PUBLISH_RATE * 5) == 0:
            self.get_logger().info(
                f'[{self._level_names[self._current_level]}] '
                f'PCAratio={self._pca_ratio:.4f} '
                f'LIOvel={self._lio_vel_mag:.2f}m/s '
                f'EKF2cov=(x={self._ekf2_cov_x:.2f}, y={self._ekf2_cov_y:.2f})'
            )


# ── 工具函数 ───────────────────────────────────────────────
def _voxel_downsample(points: np.ndarray, voxel_size: float) -> np.ndarray:
    """简单体素降采样，返回每个体素内点的均值"""
    if voxel_size <= 0 or len(points) == 0:
        return points
    voxel_indices = np.floor(points / voxel_size).astype(np.int64)
    voxel_dict = {}
    for i, idx in enumerate(voxel_indices):
        key = (int(idx[0]), int(idx[1]), int(idx[2]))
        if key not in voxel_dict:
            voxel_dict[key] = []
        voxel_dict[key].append(points[i])
    return np.array([np.mean(v, axis=0) for v in voxel_dict.values()])


def main():
    rclpy.init()
    node = DroneHealthMonitor()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
