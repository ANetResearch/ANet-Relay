#!/usr/bin/env python3
"""
safety_launch.py — 启动无人机安全监控层

节点:
  drone_health_monitor  — 定位健康监控（PCA退化 + LIO速度异常 + EKF2协方差膨胀）

发布 topic:
  /drone_safety_status  (std_msgs/String)  完整诊断信息
  /drone_safety_level   (std_msgs/Int8)    安全等级 0=正常 1=退化 2=漂移 3=紧急
"""

from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():

    health_monitor = Node(
        package='yydrone_safety',
        executable='drone_health_monitor',
        name='drone_health_monitor',
        output='screen',
        parameters=[{
            'pca_degeneracy_threshold': 0.02,
            'pca_frame_interval': 5,
            'pca_voxel_size': 0.5,
            'pca_min_points': 100,
            'lio_velocity_jump_threshold': 5.0,
            'lio_velocity_jump_window': 3,
            'ekf2_variance_high': 10.0,
            'ekf2_variance_critical': 50.0,
            'hysteresis_up': 5,
            'hysteresis_down': 10,
        }],
    )

    return LaunchDescription([health_monitor])
