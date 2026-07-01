import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    # LaunchConfigurations
    # 规划搜索范围
    map_size_x = LaunchConfiguration('map_size_x_', default=32.0)
    map_size_y = LaunchConfiguration('map_size_y_', default=30.0)
    map_size_z = LaunchConfiguration('map_size_z_', default=5.0)
    
    odometry_topic = LaunchConfiguration('odometry_topic', default='Odometry')  #odom
    camera_pose_topic = LaunchConfiguration('camera_pose_topic', default='camera_pose')
    depth_topic = LaunchConfiguration('depth_topic', default='depth_image')
    cloud_topic = LaunchConfiguration('cloud_topic', default='livox/lidar/pointcloud')  #cloud
    
    cx = LaunchConfiguration('cx', default=321.04638671875)
    cy = LaunchConfiguration('cy', default=243.44969177246094)
    fx = LaunchConfiguration('fx', default=387.229248046875)
    fy = LaunchConfiguration('fy', default=387.229248046875)
    # ※
    max_vel = LaunchConfiguration('max_vel', default=1.0)
    # ※
    max_acc = LaunchConfiguration('max_acc', default=1.0)
    planning_horizon = LaunchConfiguration('planning_horizon', default=4.5) #7.5
     
    point_num = LaunchConfiguration('point_num', default=1)
    point0_x = LaunchConfiguration('point0_x', default=0.0)
    point0_y = LaunchConfiguration('point0_y', default=0.0)
    point0_z = LaunchConfiguration('point0_z', default=0.0)
    point1_x = LaunchConfiguration('point1_x', default=10.0)
    point1_y = LaunchConfiguration('point1_y', default=10.0)
    point1_z = LaunchConfiguration('point1_z', default=0.0)
    point2_x = LaunchConfiguration('point2_x', default=20.0)
    point2_y = LaunchConfiguration('point2_y', default=20.0)
    point2_z = LaunchConfiguration('point2_z', default=1.0)
    point3_x = LaunchConfiguration('point3_x', default=-10.0)
    point3_y = LaunchConfiguration('point3_y', default=-10.0)
    point3_z = LaunchConfiguration('point3_z', default=1.0)
    point4_x = LaunchConfiguration('point4_x', default=30.0)
    point4_y = LaunchConfiguration('point4_y', default=30.0)
    point4_z = LaunchConfiguration('point4_z', default=1.0)
    #飞行模式，1为rviz或者topic给点，2为多航点飞行模式
    flight_type = LaunchConfiguration('flight_type', default=1)
    #是否使用独特轨迹，用于多机协同避障
    use_distinctive_trajs = LaunchConfiguration('use_distinctive_trajs', default=True)
    #设置动态障碍物的数量
    obj_num_set = LaunchConfiguration('obj_num_set', default=10)
    
    drone_id = LaunchConfiguration('drone_id', default=0)

    # DeclareLaunchArguments
    map_size_x_arg = DeclareLaunchArgument('map_size_x_', default_value=map_size_x, description='Map size along X')
    map_size_y_arg = DeclareLaunchArgument('map_size_y_', default_value=map_size_y, description='Map size along Y')
    map_size_z_arg = DeclareLaunchArgument('map_size_z_', default_value=map_size_z, description='Map size along Z')
    odometry_topic_arg = DeclareLaunchArgument('odometry_topic', default_value=odometry_topic, description='Odometry topic')
    camera_pose_topic_arg = DeclareLaunchArgument('camera_pose_topic', default_value=camera_pose_topic, description='Camera pose topic')
    depth_topic_arg = DeclareLaunchArgument('depth_topic', default_value=depth_topic, description='Depth topic')
    cloud_topic_arg = DeclareLaunchArgument('cloud_topic', default_value=cloud_topic, description='Point cloud topic')
    cx_arg = DeclareLaunchArgument('cx', default_value=cx, description='Camera intrinsic cx')
    cy_arg = DeclareLaunchArgument('cy', default_value=cy, description='Camera intrinsic cy')
    fx_arg = DeclareLaunchArgument('fx', default_value=fx, description='Camera intrinsic fx')
    fy_arg = DeclareLaunchArgument('fy', default_value=fy, description='Camera intrinsic fy')
    max_vel_arg = DeclareLaunchArgument('max_vel', default_value=max_vel, description='Maximum velocity')
    max_acc_arg = DeclareLaunchArgument('max_acc', default_value=max_acc, description='Maximum acceleration')
    planning_horizon_arg = DeclareLaunchArgument('planning_horizon', default_value=planning_horizon, description='Planning horizon')
    
    point_num_arg = DeclareLaunchArgument('point_num', default_value=point_num, description='Number of waypoints')
    point0_x_arg = DeclareLaunchArgument('point0_x', default_value=point0_x, description='Waypoint 0 X coordinate')
    point0_y_arg = DeclareLaunchArgument('point0_y', default_value=point0_y, description='Waypoint 0 Y coordinate')
    point0_z_arg = DeclareLaunchArgument('point0_z', default_value=point0_z, description='Waypoint 0 Z coordinate')
    point1_x_arg = DeclareLaunchArgument('point1_x', default_value=point1_x, description='Waypoint 1 X coordinate')
    point1_y_arg = DeclareLaunchArgument('point1_y', default_value=point1_y, description='Waypoint 1 Y coordinate')
    point1_z_arg = DeclareLaunchArgument('point1_z', default_value=point1_z, description='Waypoint 1 Z coordinate')
    point2_x_arg = DeclareLaunchArgument('point2_x', default_value=point2_x, description='Waypoint 2 X coordinate')
    point2_y_arg = DeclareLaunchArgument('point2_y', default_value=point2_y, description='Waypoint 2 Y coordinate')
    point2_z_arg = DeclareLaunchArgument('point2_z', default_value=point2_z, description='Waypoint 2 Z coordinate')
    point3_x_arg = DeclareLaunchArgument('point3_x', default_value=point3_x, description='Waypoint 3 X coordinate')
    point3_y_arg = DeclareLaunchArgument('point3_y', default_value=point3_y, description='Waypoint 3 Y coordinate')
    point3_z_arg = DeclareLaunchArgument('point3_z', default_value=point3_z, description='Waypoint 3 Z coordinate')
    point4_x_arg = DeclareLaunchArgument('point4_x', default_value=point4_x, description='Waypoint 4 X coordinate')
    point4_y_arg = DeclareLaunchArgument('point4_y', default_value=point4_y, description='Waypoint 4 Y coordinate')
    point4_z_arg = DeclareLaunchArgument('point4_z', default_value=point4_z, description='Waypoint 4 Z coordinate')
    
    flight_type_arg = DeclareLaunchArgument('flight_type', default_value=flight_type, description='flight_type')
    use_distinctive_trajs_arg = DeclareLaunchArgument('use_distinctive_trajs', default_value=use_distinctive_trajs, description='Use distinctive trajectories')
    obj_num_set_arg = DeclareLaunchArgument('obj_num_set', default_value=obj_num_set, description='Number of objects')
    drone_id_arg = DeclareLaunchArgument('drone_id', default_value=drone_id, description='Drone ID')

    # Ego Planner Node
    ego_planner_node = Node(
        package='ego_planner',
        executable='ego_planner_node',
        name=['drone_', drone_id, '_ego_planner_node'],
        output='screen',
        remappings=[
            ('odom_world', ['drone_', drone_id, '_', odometry_topic]),
            ('planning/bspline', ['drone_', drone_id, '_planning/bspline']),
            ('planning/data_display', ['drone_', drone_id, '_planning/data_display']),
            ('planning/broadcast_bspline_from_planner', '/broadcast_bspline'),
            ('planning/broadcast_bspline_to_planner', '/broadcast_bspline'),
            
            ('goal_point', ['drone_', drone_id, '_plan_vis/goal_point']),
            ('global_list', ['drone_', drone_id, '_plan_vis/global_list']),
            ('init_list', ['drone_', drone_id, '_plan_vis/init_list']),
            ('optimal_list', ['drone_', drone_id, '_plan_vis/optimal_list']),
            ('a_star_list', ['drone_', drone_id, '_plan_vis/a_star_list']),
            
            ('grid_map/odom', ['drone_', drone_id, '_', odometry_topic]),
            ('grid_map/cloud', ['drone_', drone_id, '_', cloud_topic]),
            ('grid_map/pose', ['drone_', drone_id, '_', camera_pose_topic]),
            ('grid_map/depth', ['drone_', drone_id, '_', depth_topic]),
            ('grid_map/occupancy_inflate', ['drone_', drone_id, '_grid/grid_map/occupancy_inflate'])
        ],
        parameters=[
            {'fsm/flight_type': flight_type},
            {'fsm/thresh_replan_time': 1.0},
            {'fsm/thresh_no_replan_meter': 1.0},
            {'fsm/planning_horizon': planning_horizon},
            {'fsm/planning_horizen_time': 3.0},
            {'fsm/emergency_time': 1.0},
            {'fsm/realworld_experiment': False},
            {'fsm/fail_safe': True},
            
            {'fsm/waypoint_num': point_num},
            {'fsm/waypoint0_x': point0_x},
            {'fsm/waypoint0_y': point0_y},
            {'fsm/waypoint0_z': point0_z},
            {'fsm/waypoint1_x': point1_x},
            {'fsm/waypoint1_y': point1_y},
            {'fsm/waypoint1_z': point1_z},
            {'fsm/waypoint2_x': point2_x},
            {'fsm/waypoint2_y': point2_y},
            {'fsm/waypoint2_z': point2_z},
            {'fsm/waypoint3_x': point3_x},
            {'fsm/waypoint3_y': point3_y},
            {'fsm/waypoint3_z': point3_z},
            {'fsm/waypoint4_x': point4_x},
            {'fsm/waypoint4_y': point4_y},
            {'fsm/waypoint4_z': point4_z},
            
            {'grid_map/resolution': 0.1},
            {'grid_map/map_size_x': map_size_x},
            {'grid_map/map_size_y': map_size_y},
            {'grid_map/map_size_z': map_size_z},
            #地图更新范围
            {'grid_map/local_update_range_x': 10.5}, #5.5
            {'grid_map/local_update_range_y': 10.5}, #5.5
            {'grid_map/local_update_range_z': 7.5}, #4.5
            #膨胀层
            {'grid_map/obstacles_inflation': 0.3}, #0.099
            #局部地图的体素扩展
            {'grid_map/local_map_margin': 10},
            {'grid_map/ground_height': -0.01},
            # 地面点云在grid_map中的最低高度过滤（米）
            # 低于此高度的世界坐标点云会被直接丢弃，避免假地面导致Z轴晃动
            {'grid_map/ground_filter_min_z': 0.3},
            #相机参数
            {'grid_map/cx': cx},
            {'grid_map/cy': cy},
            {'grid_map/fx': fx},
            {'grid_map/fy': fy},
            # 深度相机滤波
            # 是否启用深度滤波
            # 作用：过滤深度图像中的噪声和异常值True
            {'grid_map/use_depth_filter': True},
            # 深度一致性容忍度（米）
            # 作用：相邻像素深度差超过此值时，视为异常并过滤
            # 示例：
            # 像素A深度=1.5m，相邻像素B深度=1.8m
            # 深度差=0.3m > 0.15m → 过滤掉
            {'grid_map/depth_filter_tolerance': 0.15},
            # 最大有效深度（米）
            # 作用：超过此距离的深度值视为无效
            # 原因：远距离深度数据通常不准确
            # 示例：深度相机测距范围通常为0.5-10米，但5米后精度下降
            {'grid_map/depth_filter_maxdist': 5.0},
            # 最小有效深度（米）
            # 作用：小于此距离的深度值视为无效
            # 原因：避免将无人机自身或支架误判为障碍物
            # 注意：这个值应大于无人机的半径
            {'grid_map/depth_filter_mindist': 1.1},
            # 图像边界忽略的像素数
            # 作用：忽略深度图像边缘的像素
            # 原因：图像边缘的深度值通常有较大畸变
            # 示例：忽略图像四周2个像素宽的区域
            {'grid_map/depth_filter_margin': 2},
            # 深度缩放因子
            # 作用：将深度图像的原始值（毫米）转换为米
            # 解释：
            # 大多数深度相机输出的是uint16类型，单位是毫米
            # 例如：深度值1500表示1.5米
            # 转换公式：深度(米) = 原始值 / 1000.0
            {'grid_map/k_depth_scaling_factor': 1000.0},
            # 像素跳过间隔
            # 作用：每隔N个像素处理一个点，降低计算量
            # 示例：skip_pixel=2，则每行每列每隔2个像素取一个点
            # 计算量减少：1/(2×2)=1/4，只处理25%的像素
            {'grid_map/skip_pixel': 2},
            # local fusion
            {'grid_map/p_hit': 0.65},
            {'grid_map/p_miss': 0.35},
            # 栅格被占据概率的下限
            # 防止概率变为0（数值稳定性）
            # 作用：始终保持一定的"怀疑"，避免完全信任传感器
            {'grid_map/p_min': 0.12},
            # 栅格被占据概率的上限
            # 防止概率变为1（考虑到传感器误差）
            # 作用：避免过度自信，留有余地应对传感器噪声
            {'grid_map/p_max': 0.90},
            # 判断栅格是否被占据的阈值
            # 当 p >= 0.80 时，认为该栅格是障碍物
            # 作用：二值化决策，用于碰撞检测
            {'grid_map/p_occ': 0.80},
            # 传感器射线的最小长度（米）
            # 作用：忽略距离传感器太近的区域
            # 原因：避免将无人机自身或支架误判为障碍物
            {'grid_map/min_ray_length': 0.1},
            # 传感器射线的最大长度（米）
            # 作用：只处理此距离内的传感器数据,感知距离需要足够大
            # 原因：
            # 1. 远距离传感器数据不可靠
            # 2. 减少计算量
            # 3. 实时性要求
            {'grid_map/max_ray_length': 7.5},
            # 虚拟天花板高度（米）
            # 作用：高于此高度的障碍物被忽略
            # 用途：
            # 1. 室内：忽略天花板、吊灯等
            # 2. 室外：忽略树木高处、电线等
            # 3. 确保无人机在安全高度飞行            
            {'grid_map/virtual_ceil_height': 2.9},
            # 可视化时截断的高度（米）
            # 作用：在RViz中只显示低于此高度的地图
            # 原因：
            # 1. 减少可视化数据量
            # 2. 聚焦于无人机飞行高度附近
            # 3. 避免视觉混乱
            {'grid_map/visualization_truncate_height': 1.8},
            {'grid_map/show_occ_time': False},
            {'grid_map/pose_type': 2}, #使用fastliou输出的里程计
            {'grid_map/frame_id': "map"},  #world
            # planner manager
            #最大速度
            {'manager/max_vel': max_vel},
            #最大加速度
            {'manager/max_acc': max_acc},
            #最大急动度  ※
            {'manager/max_jerk': 1.0},
            # B样条控制点之间的期望距离（米）
            # 作用：决定轨迹的"分辨率"和"平滑度"
            # 影响：
            # 小值（0.2-0.3）：控制点密集 → 轨迹灵活但计算量大
            # 中值（0.4-0.5）：平衡灵活性和效率
            # 大值（0.6-0.8）：控制点稀疏 → 轨迹简单但可能不够平滑
            {'manager/control_points_distance': 0.4}, #0.4
            # 动力学约束违反的允许误差（米/秒或米/秒²）
            # 作用：在优化中允许的约束违反程度
            {'manager/feasibility_tolerance': 0.05},
            # 规划轨迹的时间长度（秒）
            # 作用：决定规划器向前看多远

            # 典型值：3.0-10.0秒
            # 影响：
            # 短视野（3-5秒）：反应快，但可能短视
            # 中视野（5-8秒）：平衡反应和预见性
            # 长视野（8-10秒）：预见性强，但计算量大
            {'manager/planning_horizon': planning_horizon},
            {'manager/use_distinctive_trajs': use_distinctive_trajs},
            {'manager/drone_id': drone_id},
            # Trajectory optimization parameters
            # 平滑性权重         ※
            {'optimization/lambda_smooth': 2.0},
            # 避障权重0.5   ※1.3-0.8-1.0
            {'optimization/lambda_collision': 1.0},
            # 可行性权重，确保速度，加速度在限制范围内 ※
            {'optimization/lambda_feasibility': 0.5},
            # 贴合度权重，控制轨迹和全局轨迹的重合度
            {'optimization/lambda_fitness': 1.0},
            # 安全距离阈值 0.5 **************！！！！！！！！！
            {'optimization/dist0': 0.6}, #1.0->0.6
            # 多机安全距离
            {'optimization/swarm_clearance': 0.5},
            # 无人机允许的最大速度
            {'optimization/max_vel': max_vel},
            # 无人机允许的最小速度
            {'optimization/max_acc': max_acc},

            # B-Spline parameters
            {'bspline/limit_vel': max_vel},
            {'bspline/limit_acc': max_acc},
            # ※
            {'bspline/limit_ratio': 1.0},

            # Object prediction parameters
            {'prediction/obj_num': obj_num_set},
            {'prediction/lambda': 1.0},
            {'prediction/predict_rate': 1.0}
        ]
    )

    # Create LaunchDescription
    ld = LaunchDescription()

    # Add LaunchArguments
    ld.add_action(map_size_x_arg)
    ld.add_action(map_size_y_arg)
    ld.add_action(map_size_z_arg)
    ld.add_action(odometry_topic_arg)
    ld.add_action(camera_pose_topic_arg)
    ld.add_action(depth_topic_arg)
    ld.add_action(cloud_topic_arg)
    ld.add_action(cx_arg)
    ld.add_action(cy_arg)
    ld.add_action(fx_arg)
    ld.add_action(fy_arg)
    ld.add_action(max_vel_arg)
    ld.add_action(max_acc_arg)
    ld.add_action(planning_horizon_arg)
    
    ld.add_action(point_num_arg)
    ld.add_action(point0_x_arg)
    ld.add_action(point0_y_arg)
    ld.add_action(point0_z_arg)
    ld.add_action(point1_x_arg)
    ld.add_action(point1_y_arg)
    ld.add_action(point1_z_arg)
    ld.add_action(point2_x_arg)
    ld.add_action(point2_y_arg)
    ld.add_action(point2_z_arg)
    ld.add_action(point3_x_arg)
    ld.add_action(point3_y_arg)
    ld.add_action(point3_z_arg)
    ld.add_action(point4_x_arg)
    ld.add_action(point4_y_arg)
    ld.add_action(point4_z_arg)
    
    ld.add_action(flight_type_arg)
    ld.add_action(use_distinctive_trajs_arg)
    ld.add_action(obj_num_set_arg)
    ld.add_action(drone_id_arg)


    # Add Node
    ld.add_action(ego_planner_node)

    return ld
