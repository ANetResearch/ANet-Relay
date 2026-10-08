from launch import LaunchDescription
from launch_ros.actions import Node
from launch.substitutions import PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare

def generate_launch_description():
    # 找到功能包路径
    pkg_path = FindPackageShare('yydrone_fsm')
    
    # 创建节点
    fsm_node = Node(
        package='yydrone_fsm',
        executable='egoplanner_fsm_node',
        name='egoplanner_fsm',
        output='screen',
        parameters=[
            # 参数配置
            {'target_height': 1.0},
            {'takeoff_speed': 0.5},
            {'max_horizontal_speed': 1.0},
            {'control_frequency': 50.0},
            {'cmd_timeout': 0.5},
            {'kpx': 1.0},
            {'kpy': 1.0},
            {'kpz': 1.0},
            {'kdx': 0.0},
            {'kdy': 0.0},
            {'kdz': 0.0},
            {'max_angle': 0.5},
            {'thrust_coeff': 1.0},
            {'k_yaw_rate': 1.0},  
        ],        
        remappings=[
            ('/mavros/state', '/mavros/state'),
            ('/mavros/local_position/odom', '/mavros/local_position/odom'),
            ('/drone_0_planning/pos_cmd', '/drone_0_planning/pos_cmd'),
            ('/mavros/setpoint_position/local', '/mavros/setpoint_position/local'),
            ('/mavros/setpoint_raw/attitude', '/mavros/setpoint_raw/attitude'),
        ]
    )
    
    return LaunchDescription([
        fsm_node,
    ])