import launch
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='yydrone_aruco',
            executable='yydrone_aruco_node',
            name='aruco_detector',
            output='screen',
            parameters=[{
                # ── Orbbec camera topics ──────────────────────────
                'image_topic': '/camera/color/image_raw',
                'camera_info_topic': '/camera/color/camera_info',
                'camera_frame': 'camera_color_optical_frame',

                # ── Dictionary ────────────────────────────────────
                # DICT_4X4_50   DICT_4X4_100   DICT_4X4_250   DICT_4X4_1000
                # DICT_5X5_50   DICT_5X5_100   DICT_5X5_250   DICT_5X5_1000
                # DICT_6X6_50   DICT_6X6_100   DICT_6X6_250   DICT_6X6_1000
                # DICT_7X7_50   DICT_7X7_100   DICT_7X7_250   DICT_7X7_1000
                # DICT_ARUCO_ORIGINAL
                'dictionary': 'DICT_6X6_250',

                # ── Marker physical size (meters) ─────────────────
                'marker_size': 0.10,

                # ── Publish all markers, or only target_marker_id? ─
                'publish_all_markers': True,
                'target_marker_id': 0,

                # ── Debug image (draw detection overlay) ──────────
                'publish_debug_image': True,
            }]
        ),
    ])
