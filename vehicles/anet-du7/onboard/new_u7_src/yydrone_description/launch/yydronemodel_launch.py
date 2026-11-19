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
    
    # URDF文件路径
    urdf_file = os.path.join(pkg_path, 'urdf', 'iris.urdf.xacro')
    
    doc = xacro.process_file(urdf_file)
    robot_desc = doc.toxml()
    ld = LaunchDescription()
   
    # 添加RViz节点
    rviz = Node(
        package='rviz2',
        executable='rviz2',
        name='rviz2',
        parameters=[{'use_sim_time': True}],
        output='screen'
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
    
    
    
    # 这些可以立即启动
    ld.add_action(robot_state_publisher_node)
    ld.add_action(joint_state_publisher_node)
    ld.add_action(rviz)
    
    return ld
