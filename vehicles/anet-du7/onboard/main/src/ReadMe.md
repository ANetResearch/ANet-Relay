# drone_ws 工作空间说明

## 概述

本工作空间包含无人机自主飞行系统的完整 ROS2 Humble 代码，涵盖 **视觉定位**、**激光里程计**、**轨迹规划**、**飞控桥接**、**仿真** 和 **地面站 UI** 等模块。

## 目录结构

```
drone_ws/src/
├── yydrone_aruco/          # ArUco 标记检测与位姿估计
├── yydrone_localization/   # FAST-LIO 激光里程计 + MAVROS 桥接
├── yydrone_fsm/            # 自主飞行有限状态机
├── yydrone_control/        # 键盘控制 + Python 飞控脚本
├── yydrone_description/    # 无人机 URDF 模型 + Gazebo 仿真
├── yydrone_navigation/     # EGO 规划器启动集成
├── fast_lio/               # FAST-LIO2 激光惯性里程计
├── livox_ros_driver2/      # Livox 激光雷达 ROS2 驱动
├── aruco_ros/              # ArUco ROS2 封装库(aruco + aruco_msgs + aruco_ros)
├── ego-planner-swarm/      # EGO-Planner 集群规划 + UAV 仿真器
├── ui/                     # PyQt5 地面站 UI
├── fastlio_locate.py       # Fast-LIO -> MAVROS 桥接（Python 版）
└── ros2_keyboard_control.py # 键盘控制（Python 版）
```

## 编译

```bash
cd ~/drone_ws
colcon build --symlink-install
source install/setup.bash
```

首次编译或部分包编译：
```bash
# 只编译指定包
colcon build --symlink-install --packages-select yydrone_aruco yydrone_fsm

# 编译 ego-planner 相关包
colcon build --symlink-install --packages-up-to ego_planner
```

## 各模块使用说明

### 1. ArUco 视觉定位 (`yydrone_aruco`)

检测图像中的 ArUco 标记并估计 6-DOF 位姿。

```bash
ros2 launch yydrone_aruco aruco_detector.launch.py
```

关键参数：
- `image_topic` — 图像话题（默认 `/camera/image_raw`）
- `dictionary_id` — 标记字典（默认 10，即 DICT_6X6_250）
- `marker_size` — 标记物理尺寸，单位米（默认 0.15）
- `camera_fx/fy/cx/cy` — 相机内参，不设则跳过位姿估计

### 2. FAST-LIO 激光定位 (`yydrone_localization`)

启动 Livox 激光雷达驱动 + FAST-LIO2 里程计，并将里程计桥接到 PX4 视觉融合接口。

```bash
ros2 launch yydrone_localization fastlio_launch.py
```

关键参数见 `yydrone_localization/config/fastlio_livox_params.yaml`：
- IMU-LiDAR 外参
- 点云特征提取参数
- PCD 保存路径

### 3. 自主飞行状态机 (`yydrone_fsm`)

管理从起飞到轨迹跟踪的完整飞行流程。

```bash
ros2 launch yydrone_fsm fsm_launch.py
```

状态流转：INIT → PREFLIGHT → TAKEOFF → HOVER_WAIT → CMD_CONTROL → ERROR

参数见 `yydrone_fsm/config/fsm_params.yaml`：
- `target_height` — 目标飞行高度（m）
- `takeoff_speed` / `land_speed` — 起飞/降落速度（m/s）
- `max_horizontal_speed` — 最大水平速度（m/s）

### 4. 键盘手动控制 (`yydrone_control`)

```bash
# C++ 版本
ros2 run yydrone_control keyboard_control_node

# Python 版本
python3 src/ros2_keyboard_control.py
```

其他脚本：
- `offboard.py` — Offboard 模式悬停
- `takeoff.py` — 起飞指令
- `ego2mavros.py` — 将 EGO 规划器指令转为 MAVROS 位置设定值
- `integratemission.py` — 综合任务控制

### 5. Gazebo 仿真 (`yydrone_description`)

```bash
ros2 launch yydrone_description yydronemodel_launch.py
```

启动 Gazebo + PX4 SITL + 无人机模型（Iris / X500）+ RViz。

### 6. EGO 规划器 (`yydrone_navigation` + `ego-planner-swarm`)

```bash
# 启动规划器 + 随机地图
ros2 launch yydrone_navigation ego_planner_launch.py

# 单机仿真（含地图、感知、规划、仿真器）
ros2 launch ego_planner single_run_in_sim.launch.py

# 集群仿真
ros2 launch ego_planner swarm.launch.py
```

规划管道：mockamap（地图生成） → plan_env（ESDF 地图） → path_searching（路径搜索） → bspline_opt（B 样条优化） → traj_server（轨迹执行）

### 7. 地面站 UI (`ui`)

```bash
cd src/ui && python run.py
```

## 典型工作流

### 仿真测试

```bash
# 终端 1: 启动 Gazebo 仿真
ros2 launch yydrone_description yydronemodel_launch.py

# 终端 2: 启动规划器
ros2 launch yydrone_navigation ego_planner_launch.py

# 终端 3: 启动状态机
ros2 launch yydrone_fsm fsm_launch.py
```

### 真机飞行

```bash
# 终端 1: 激光定位
ros2 launch yydrone_localization fastlio_launch.py

# 终端 2: 状态机
ros2 launch yydrone_fsm fsm_launch.py

# 终端 3: 规划器
ros2 launch yydrone_navigation ego_planner_launch.py

# （可选）终端 4: ArUco 辅助定位
ros2 launch yydrone_aruco aruco_detector.launch.py
```

## 依赖

- **ROS2 Humble** — 基础框架
- **OpenCV 4.x** — 视觉检测 (`yydrone_aruco`)
- **PCL** — 点云处理 (`fast_lio`, `plan_env`, `local_sensing`)
- **Eigen3** — 矩阵运算 (`fast_lio`, EGO 规划器)
- **MAVROS** — PX4 飞控通信 (`yydrone_fsm`, `yydrone_control`)
- **Livox SDK2** — 激光雷达驱动 (`livox_ros_driver2`)
- **PyQt5 / PyQt-Fluent-Widgets / PyOpenGL** — 地面站 UI (`drone_ui`)
- **Armadillo** — 线性代数 (`pose_utils`, `odom_visualization`)

## 坐标系说明

```
map (世界坐标系，FAST-LIO 发布)
  └── odom
      └── base_link (无人机机体，FLU: 前-左-上)
          ├── camera_frame (相机)
          └── livox_frame (激光雷达)
```

- PX4 使用 **FRD** 坐标系（前-右-下），MAVROS 会自动转换
- `fastlio_locate` 将 FAST-LIO 里程计从 `map` 桥接到 `/mavros/vision_pose/pose` 供 EKF2 融合
