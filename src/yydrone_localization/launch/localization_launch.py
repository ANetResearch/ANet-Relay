#!/usr/bin/env python3
"""
FAST-LIO2 里程计 + 可选全局定位
───────────────────────────────
两种模式，通过 mode 参数切换:

  mode:=odometry     纯里程计模式 (默认)
    Livox → FAST-LIO2 → fastlio_locate(passthrough) → PX4
    使用静态 map→odom TF (identity)，无全局修正
    适用场景: 无先验地图、新环境飞行

  mode:=localization  全局定位模式
    Livox → FAST-LIO2 → localizer(ICP) → fastlio_locate(TF) → PX4
    需要先建好 PCD 地图，启动后调用 relocalize 服务对齐
    适用场景: 已建图的已知环境

用法:
  ros2 launch yydrone_localization localization_launch.py                          # 纯里程计
  ros2 launch yydrone_localization localization_launch.py mode:=localization       # 全局定位
"""

from launch import LaunchDescription, LaunchContext
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from launch.launch_description_sources import PythonLaunchDescriptionSource


def launch_setup(context, *args, **kwargs):
    pkg_name = "yydrone_localization"
    mode = LaunchConfiguration("mode").perform(context)

    is_localization = mode == "localization"

    nodes = []

    # ── Livox MID360 驱动 ────────────────────────────────────
    livox_driver = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            PathJoinSubstitution([
                FindPackageShare("livox_ros_driver2"),
                "launch",
                "msg_MID360_launch.py",
            ])
        ]),
        launch_arguments={
            "xfer_format": "1",
            "multi_topic": "0",
            "data_src": "0",
            "publish_freq": "10.0",
            "output_data_type": "0",
            "frame_id": "livox_frame",
        }.items(),
    )
    nodes.append(livox_driver)

    # ── FAST-LIO2 里程计 ────────────────────────────────────
    lio_config_path = PathJoinSubstitution([
        FindPackageShare(pkg_name), "config", "lio_mid360.yaml",
    ])

    fastlio2_node = Node(
        package="fastlio2",
        executable="lio_node",
        name="lio_node",
        output="screen",
        parameters=[
            {"config_path": lio_config_path},
        ],
        remappings=[
            ("lio_odom", "Odom_high_freq"),
            ("body_cloud", "cloud_registered"),
        ],
    )
    nodes.append(fastlio2_node)

    # ── 纯里程计模式: 静态 map→odom TF ─────────────────────
    if not is_localization:
        static_tf_map_odom = Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            arguments=["0", "0", "0", "0", "0", "0", "map", "odom"],
            output="screen",
        )
        nodes.append(static_tf_map_odom)

    # ── 全局定位模式: localizer ─────────────────────────────
    if is_localization:
        localizer_config_path = PathJoinSubstitution([
            FindPackageShare(pkg_name), "config", "localizer.yaml",
        ])

        localizer_node = Node(
            package="localizer",
            namespace="localizer",
            executable="localizer_node",
            name="localizer_node",
            output="screen",
            parameters=[
                {"config_path": localizer_config_path},
            ],
        )
        nodes.append(localizer_node)

    # ── 桥接节点: LIO odom → PX4 vision pose ────────────────
    fastlio_locate_node = Node(
        package="yydrone_localization",
        executable="fastlio_locate",
        name="fastlio_locate",
        output="screen",
        parameters=[
            {"enable_tf_lookup": is_localization},  # 定位模式开 TF，里程计模式直通
            {"world_frame": "odom"},
            {"map_frame": "map"},
            {"odom_topic": "/Odom_high_freq"},
            {"publish_freq": 100.0},
            {"verbose": False},
        ],
    )
    nodes.append(fastlio_locate_node)

    # ── RViz2 ───────────────────────────────────────────────
    rviz_node = Node(
        package="rviz2",
        executable="rviz2",
        name="rviz2",
        output="screen",
    )
    nodes.append(rviz_node)

    return nodes


def generate_launch_description():
    mode_arg = DeclareLaunchArgument(
        "mode",
        default_value="odometry",
        description="odometry (纯里程计) | localization (全局重定位)",
    )

    return LaunchDescription([
        mode_arg,
        OpaqueFunction(function=launch_setup),
    ])
