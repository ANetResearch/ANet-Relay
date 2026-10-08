#!/usr/bin/env python3

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, IncludeLaunchDescription
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from launch.launch_description_sources import PythonLaunchDescriptionSource

def generate_launch_description():
    pkg_name = 'yydrone_localization'
    
    livox_driver = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            PathJoinSubstitution([
                FindPackageShare('livox_ros_driver2'),
                'launch_ROS2',
                'msg_MID360s_launch.py'
            ])
        ]),
        launch_arguments={
            'xfer_format': '1',  # PointCloud2 格式
            'multi_topic': '0',
            'data_src': '0',
            'publish_freq': '10.0',
            'output_data_type': '0',
            'frame_id': 'livox_frame',
            
        }.items()
    )
    static_tf_odom_to_map = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        arguments=['0', '0', '0', '0', '0', '0', 'map', 'odom'],
        output='screen'
    )
    static_tf_livox_to_base_link = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        arguments=['0', '0', '0', '0', '0', '0', 'livox_frame', 'base_link'],
        output='screen'
    )
    # FAST-LIO 节点
    fastlio_node = Node(
        package='fast_lio',
        executable='fastlio_mapping',
        name='fast_lio',
        output='screen',
        parameters=[PathJoinSubstitution([
            FindPackageShare(pkg_name),
            'config',
            'fastlio_livox_params.yaml'
        ])],
        remappings=[
            ('/imu', '/livox/imu'),
            ('/points', '/livox/lidar'),
        ],
        emulate_tty=True
    )
    fastlio_locate_node = Node(
        package='yydrone_localization',
        executable='fastlio_locate',
        name='fastlio_locate',
        output = 'screen'
    )
    # 地面点云滤波节点 (PassThrough Z-axis)
    # 滤除世界坐标系下 z < ground_height 的地面点
    # 输入: /cloud_registered (FAST-LIO 输出)
    # 输出: /cloud_registered_filter
    ground_filter_node = Node(
        package='yydrone_localization',
        executable='ground_filter.py',
        name='ground_filter',
        output='screen',
        parameters=[{
            'ground_height': 0.05,         # 地面高度阈值（米），低于此高度的点被滤除
            'filter_limit_max': 30.0,     # z 轴上限（米）
            'use_negative': False,        # True=保留阈值外的点
        }],
    )
    rviz_node = Node(
        package='rviz2',
        executable='rviz2',
    )

    return LaunchDescription([
        livox_driver,
        fastlio_node,
        fastlio_locate_node,
        ground_filter_node,
        static_tf_odom_to_map,
        static_tf_livox_to_base_link,
        # rviz_node,
    ])