import launch
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package='yydrone_mission',
            executable='mission_dispatcher_node',
            name='mission_dispatcher',
            output='screen',
            parameters=[{
                # ── 选择任务 ──────────────────────────────────
                # 可选: "takeoff_and_forward"
                'mission_name': 'takeoff_and_forward',

                # ── 坐标系: ENU (X=东 Y=北 Z=上) ────────────
                #  yaw=0 → 机头朝东 (+X)
                #  yaw=90°→ 机头朝北 (+Y)

                # ── 通用参数 ──────────────────────────────────
                'drone_id': 0,
                'auto_start': True,

                # ── 起飞+前飞 任务参数 ────────────────────────
                'takeoff_height': 1.0,        # 起飞高度（米）
                'forward_distance': 2.0,       # 前飞距离（米）
                'direction_deg': 0.0,          # 飞行方向（度），0=正前 90=右 -90=左 180=后方
                'reach_threshold': 0.3,        # 到达判定阈值（米）
                'goal_timeout': 30.0,          # 单航点超时（秒）
            }]
        ),
    ])
