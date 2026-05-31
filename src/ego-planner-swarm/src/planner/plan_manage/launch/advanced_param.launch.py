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

    max_vel = LaunchConfiguration('max_vel', default=2.0)
    max_acc = LaunchConfiguration('max_acc', default=3.0)
    planning_horizon = LaunchConfiguration('planning_horizon', default=7.5)

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

    # ╔══════════════════════════════════════════════════════════════════════════╗
    # ║                        Ego Planner Node                                 ║
    # ╚══════════════════════════════════════════════════════════════════════════╝
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

            # ──────────────────────────────────────────────────────────────
            # 1. 飞行状态机 (fsm) — 控制飞行模式、重规划和紧急行为
            # ──────────────────────────────────────────────────────────────
            {'fsm/flight_type': flight_type},                          # 飞行模式：1 = Rviz/topic给目标点；2 = 多航点顺序飞行
            {'fsm/thresh_replan_time': 1.0},                           # 重规划时间阈值（秒）：每隔多久强制重规划一次
            {'fsm/thresh_no_replan_meter': 1.0},                       # 重规划距离阈值（米）：偏离当前轨迹超过此距离触发重规划，越小越敏感
            {'fsm/planning_horizon': planning_horizon},                # 规划视野长度（秒）：规划器往前看多远，典型值 5~10
            {'fsm/planning_horizen_time': 3.0},                        # 规划时间视野（秒）：与 planning_horizon 配合使用
            {'fsm/emergency_time': 1.0},                               # 紧急刹车时间（秒）：检测到碰撞风险后多少秒内紧急停止
            {'fsm/realworld_experiment': False},                       # ★ 实机飞行开关：实机必须设为 True！会启用安全保护逻辑
            {'fsm/fail_safe': True},                                   # 故障安全：失联/异常时自动悬停或降落

            # 航点（仅在 flight_type=2 时生效）
            {'fsm/waypoint_num': point_num},                           # 航点数量：有多少个航点需要飞
            {'fsm/waypoint0_x': point0_x},                             # 航点0 X坐标（米）
            {'fsm/waypoint0_y': point0_y},                             # 航点0 Y坐标（米）
            {'fsm/waypoint0_z': point0_z},                             # 航点0 Z坐标（米）
            {'fsm/waypoint1_x': point1_x},                             # 航点1 X坐标（米）
            {'fsm/waypoint1_y': point1_y},                             # 航点1 Y坐标（米）
            {'fsm/waypoint1_z': point1_z},                             # 航点1 Z坐标（米）
            {'fsm/waypoint2_x': point2_x},                             # 航点2 X坐标（米）
            {'fsm/waypoint2_y': point2_y},                             # 航点2 Y坐标（米）
            {'fsm/waypoint2_z': point2_z},                             # 航点2 Z坐标（米）
            {'fsm/waypoint3_x': point3_x},                             # 航点3 X坐标（米）
            {'fsm/waypoint3_y': point3_y},                             # 航点3 Y坐标（米）
            {'fsm/waypoint3_z': point3_z},                             # 航点3 Z坐标（米）
            {'fsm/waypoint4_x': point4_x},                             # 航点4 X坐标（米）
            {'fsm/waypoint4_y': point4_y},                             # 航点4 Y坐标（米）
            {'fsm/waypoint4_z': point4_z},                             # 航点4 Z坐标（米）

            # ──────────────────────────────────────────────────────────────
            # 2. 栅格地图 (grid_map) — 控制环境建图和深度相机数据处理
            # ──────────────────────────────────────────────────────────────

            # —— 地图尺寸 ——
            {'grid_map/resolution': 0.1},                              # 地图分辨率（米/格）：越小越精细但计算量越大
            {'grid_map/map_size_x': map_size_x},                       # 地图 X 方向总长度（米）
            {'grid_map/map_size_y': map_size_y},                       # 地图 Y 方向总长度（米）
            {'grid_map/map_size_z': map_size_z},                       # 地图 Z 方向总高度（米）：无人机不能飞超过此高度
            {'grid_map/local_update_range_x': 5.5},                    # 局部地图 X 方向更新范围（米）：以无人机为中心
            {'grid_map/local_update_range_y': 5.5},                    # 局部地图 Y 方向更新范围（米）
            {'grid_map/local_update_range_z': 4.5},                    # 局部地图 Z 方向更新范围（米）
            {'grid_map/obstacles_inflation': 0.099},                   # 障碍物膨胀层厚度（米）：把障碍物"变胖"，增加安全余量
            {'grid_map/local_map_margin': 10},                         # 局部地图边界扩展（体素）
            {'grid_map/ground_height': -0.01},                         # 地面高度（米）：低于此高度视为地面

            # —— 相机内参（深度相机） ——
            {'grid_map/cx': cx},                                       # 相机光心 X 像素坐标
            {'grid_map/cy': cy},                                       # 相机光心 Y 像素坐标
            {'grid_map/fx': fx},                                       # 相机焦距 fx（像素）
            {'grid_map/fy': fy},                                       # 相机焦距 fy（像素）

            # —— 深度滤波 ——
            {'grid_map/use_depth_filter': False},                      # 是否启用深度滤波：True = 过滤噪声和异常深度值
            {'grid_map/depth_filter_tolerance': 0.15},                 # 深度一致性容忍度（米）：相邻像素深度差 > 此值视为异常并丢弃
            {'grid_map/depth_filter_maxdist': 5.0},                    # 最大有效深度（米）：超过此距离的深度值忽略（远距离不可靠）
            {'grid_map/depth_filter_mindist': 1.1},                    # 最小有效深度（米）：小于此距离忽略（避免拍到机身），应 > 无人机半径
            {'grid_map/depth_filter_margin': 2},                       # 图像边缘忽略像素数：边缘畸变大，直接跳过
            {'grid_map/k_depth_scaling_factor': 1000.0},               # 深度缩放因子：原始值 ÷ 此值 = 米（如 1500 ÷ 1000 = 1.5m）
            {'grid_map/skip_pixel': 2},                                # 降采样间隔：每隔 N 个像素采一个点，值越大越快但越稀疏

            # —— 概率融合（local fusion） ——
            {'grid_map/p_hit': 0.65},                                  # 命中概率增量：传感器射线击中障碍物时，该栅格被占据概率增加
            {'grid_map/p_miss': 0.35},                                 # 穿透概率减量：射线穿过栅格时，占据概率减少
            {'grid_map/p_min': 0.12},                                  # 占据概率下限：防止概率变为 0（数值稳定性）
            {'grid_map/p_max': 0.90},                                  # 占据概率上限：防止概率变为 1（预留传感器噪声余量）
            {'grid_map/p_occ': 0.80},                                  # 占据判定阈值：p ≥ 此值认为该栅格是障碍物（碰撞检测用）

            # —— 射线参数 ——
            {'grid_map/min_ray_length': 0.1},                          # 射线最小长度（米）：忽略传感器太近的数据，避免拍到机身
            {'grid_map/max_ray_length': 4.5},                          # 射线最大长度（米）：只看此距离内的传感器数据，超出的忽略

            # —— 高度过滤 ——
            {'grid_map/virtual_ceil_height': 2.9},                     # 虚拟天花板高度（米）：超过此高度的障碍物忽略（如吊灯、天花板）
            {'grid_map/visualization_truncate_height': 1.8},           # 可视化截断高度（米）：RViz 只显示低于此高度的地图，减少视觉混乱

            # —— 其他 ——
            {'grid_map/show_occ_time': False},                         # 是否显示栅格被占据的时间戳
            {'grid_map/pose_type': 2},                                 # 里程计类型：2 = FAST-LIO 输出的里程计
            {'grid_map/frame_id': "odom"},                             # 全局坐标系名称："odom" 或 "world"

            # ──────────────────────────────────────────────────────────────
            # 3. 规划管理器 (manager) — 控制轨迹规划和动力学约束
            # ──────────────────────────────────────────────────────────────
            {'manager/max_vel': max_vel},                              # ★ 最大飞行速度（m/s）：调大飞得快，调小飞得稳
            {'manager/max_acc': max_acc},                              # ★ 最大加速度（m/s²）：调大加速猛，调小加速柔和
            {'manager/max_jerk': 4.0},                                 # 最大 jerk（m/s³）：加速度的变化率，影响轨迹平滑度
            {'manager/control_points_distance': 0.2},                  # B 样条控制点间距（米）：小 → 轨迹灵活计算多；大 → 轨迹简单计算少
            {'manager/feasibility_tolerance': 0.05},                   # 动力学约束允许误差：轨迹稍微超速的容忍度
            {'manager/planning_horizon': planning_horizon},            # ★ 规划视野（秒）：规划器向前看多远，视线越远越能提前规避
            {'manager/use_distinctive_trajs': use_distinctive_trajs},  # 多机差异化轨迹：True = 多机协同各自走不同路线避碰
            {'manager/drone_id': drone_id},                            # 无人机 ID 编号

            # ──────────────────────────────────────────────────────────────
            # 4. 轨迹优化 (optimization) — 控制优化的目标权重
            # ──────────────────────────────────────────────────────────────
            {'optimization/lambda_smooth': 1.0},                       # ★ 平滑性权重：越大轨迹越平滑（弯少），但可能更贴近障碍物
            {'optimization/lambda_collision': 1.0},                    # ★ 避障权重：越大离障碍物越远，但轨迹可能更曲折
            {'optimization/lambda_feasibility': 0.1},                  # 可行性权重：确保速度/加速度不超限的程度
            {'optimization/lambda_fitness': 1.0},                      # 贴合度权重：控制轨迹与全局参考路径的重合度
            {'optimization/dist0': 1.0},                               # ★ 安全距离阈值（米）：优化器确保轨迹离障碍物至少这么远
            {'optimization/swarm_clearance': 0.5},                     # 多机安全距离（米）：与其他无人机的避碰距离
            {'optimization/max_vel': max_vel},                         # 优化中的最大速度约束（m/s）
            {'optimization/max_acc': max_acc},                         # 优化中的最大加速度约束（m/s²）

            # ──────────────────────────────────────────────────────────────
            # 5. B 样条 (bspline) — B 样条轨迹的限制参数
            # ──────────────────────────────────────────────────────────────
            {'bspline/limit_vel': max_vel},                            # B 样条速度上限（m/s）
            {'bspline/limit_acc': max_acc},                            # B 样条加速度上限（m/s²）
            {'bspline/limit_ratio': 1.1},                              # 限制比例：1.1 表示允许超 10%，给予一定缓冲

            # ──────────────────────────────────────────────────────────────
            # 6. 动态障碍物预测 (prediction) — 预测其他物体的运动
            # ──────────────────────────────────────────────────────────────
            {'prediction/obj_num': obj_num_set},                       # 同时跟踪的动态障碍物最大数量
            {'prediction/lambda': 1.0},                                # 预测权重：越大预测越激进（假设物体更可能继续运动）
            {'prediction/predict_rate': 1.0},                          # 预测更新频率（Hz）

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
