from launch import LaunchDescription
from launch.actions import ExecuteProcess, TimerAction, IncludeLaunchDescription
from launch_ros.actions import Node
from launch.substitutions import Command, FindExecutable, PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare
import os
from ament_index_python.packages import get_package_share_directory
import xacro

def generate_launch_description():

    # 获取包路径
    pkg_path = get_package_share_directory('yydrone_description')
    
    # 世界文件路径
    world_file = '/home/lcx/drone_ws/src/yydrone_description/world/space_1.world'
    
    # URDF文件路径
    urdf_file = os.path.join(pkg_path, 'urdf', 'iris.urdf.xacro')
    
    doc = xacro.process_file(urdf_file)
    robot_desc = doc.toxml()

    ld = LaunchDescription([
        # 启动Ignition Gazebo
        ExecuteProcess(
            cmd=[  'gazebo',
                   '--verbose',
                   world_file, 
                   '-s','libgazebo_ros_init.so',
                   '-s','libgazebo_ros_factory.so'
                ],
            output='screen'
        ),
        ExecuteProcess(
            cmd=[ 'ros2','launch','mavros','px4.launch','fcu_url:=udp://:14540@127.0.0.1:14540'

            ],
            output='screen'
        )    
    ])
    
    # 添加RViz节点
    rviz = Node(
        package='rviz2',
        executable='rviz2',
        name='rviz2',
        parameters=[{'use_sim_time': True}],
        output='screen'
    )
    
    # 添加模型到Gazebo
    spawn_entity_node = Node(
        package='gazebo_ros',
        executable='spawn_entity.py',
        arguments=[
            '-entity', 'iris',
            '-file', '/home/lcx/drone_ws/src/yydrone_description/models/iris/model.sdf',
            '-x', '0', 
            '-y', '0', 
            '-z', '0',
            '-Y', '0'
        ],
        # output='screen'
    )
    
    # robot_state_publisher节点 - 使用处理好的URDF字符串
    robot_state_publisher_node = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        name='robot_state_publisher',
        output='screen',
        parameters=[{
            'robot_description': robot_desc,
            'use_sim_time': True
        }]
    )
    
    # joint_state_publisher节点 - 不需要传递URDF文件
    joint_state_publisher_node = Node(
        package='joint_state_publisher',
        executable='joint_state_publisher',
        name='joint_state_publisher',
        output='screen',
        parameters=[{'use_sim_time': True}]
    )
    
    # 添加所有节点到LaunchDescription
    # 使用TimerAction延迟启动一些节点，确保Gazebo已经启动
    ld.add_action(TimerAction(
        period=3.0,
        actions=[spawn_entity_node]
    ))
    
    # 这些可以立即启动
    ld.add_action(robot_state_publisher_node)
    ld.add_action(joint_state_publisher_node)
    ld.add_action(rviz)
    
    return ld