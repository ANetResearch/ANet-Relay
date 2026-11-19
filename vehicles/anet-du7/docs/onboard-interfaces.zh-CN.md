# 无人机自主飞行系统接口文档

> **项目名称**：汤圆U7 接口文档
> **ROS 版本**：ROS2 Humble
> **文档版本**：v1.2（可读性优化版）
> **更新日期**：2026-09-02
> **审核人**：刘晨曦
> **适用范围**：板载实机自主飞行开发

---

## 📌 快速参考卡

### 核心控制链路

```
EGO-Planner → yydrone_fsm → MAVROS(setpoint_raw/local) → PX4
     位置+速度+加速度前馈 | 50Hz | NED惯性系
```

### 定位链路

```
Livox Mid-360 → fast_lio(200Hz) → fastlio_locate.py
     → /mavros/vision_pose/pose → PX4 EKF2 融合
```

### 关键话题速查

| 用途 | 话题名 | 消息类型 | 频率 |
|------|--------|----------|------|
| 飞控状态 | `/mavros/state` | `mavros_msgs/State` | 1Hz |
| 位置反馈 | `/mavros/local_position/odom` | `nav_msgs/Odometry` | 50Hz |
| 控制指令 | `/mavros/setpoint_raw/local` | `mavros_msgs/PositionTarget` | 50Hz |
| 规划指令 | `/drone_0_planning/pos_cmd` | `quadrotor_msgs/PositionCommand` | 50Hz |
| 高频里程计 | `/Odom_high_freq` | `nav_msgs/Odometry` | 200Hz |
| 视觉位姿输入 | `/mavros/vision_pose/pose` | `geometry_msgs/PoseStamped` | 100Hz |

### 关键服务速查

| 用途 | 服务名 | 服务类型 |
|------|--------|----------|
| 解锁/上锁 | `/mavros/cmd/arming` | `mavros_msgs/CommandBool` |
| 切换模式 | `/mavros/set_mode` | `mavros_msgs/SetMode` |
| 保存地图 | `/pgo/save_maps` | `interface/SaveMaps` |
| 重定位 | `/localizer/relocalize` | `interface/Relocalize` |
| 地图优化 | `/hba/refine_map` | `interface/RefineMap` |

---

## 目录

- [1. 系统概述](#1-系统概述)
  - [1.1 系统简介](#11-系统简介)
  - [1.2 功能模块清单](#12-功能模块清单)
- [2. 系统架构](#2-系统架构)
  - [2.1 模块依赖关系](#21-模块依赖关系)
  - [2.2 数据流架构](#22-数据流架构)
  - [2.3 定位系统对比](#23-定位系统对比)
- [3. 坐标系与控制模式](#3-坐标系与控制模式)
  - [3.1 坐标系定义](#31-坐标系定义)
  - [3.2 控制模式详解](#32-控制模式详解)
  - [3.3 坐标系转换关系](#33-坐标系转换关系)
  - [3.4 外参配置](#34-外参配置)
- [4. 核心模块接口](#4-核心模块接口)
  - [4.1 yydrone_fsm（飞行状态机）](#41-yydrone_fsm飞行状态机)
  - [4.2 fast_lio（高频激光里程计）](#42-fast_lio高频激光里程计)
  - [4.3 FASTLIO2_ROS2（全局定位系统）](#43-fastlio2_ros2全局定位系统)
  - [4.4 yydrone_aruco（ArUco视觉定位）](#44-yydrone_arucoaruco视觉定位)
  - [4.5 ros2_keyboard_control.py（键盘控制）](#45-ros2_keyboard_controlpy键盘控制)
- [5. 话题接口规范](#5-话题接口规范)
- [6. 服务接口规范](#6-服务接口规范)
- [7. 消息类型定义](#7-消息类型定义)
- [8. 参数配置接口](#8-参数配置接口)
- [9. Python 脚本接口](#9-python-脚本接口)
- [10. MAVROS 完整话题列表](#10-mavros-完整话题列表)

---

# 1. 系统概述

## 1.1 系统简介

本系统是基于 **ROS2 Humble** 的无人机自主飞行系统，整合了激光/视觉定位、轨迹规划、飞控通信与状态管理等全套功能模块，支持板载实机自主飞行。

### 核心技术栈

| 类别 | 组件 | 说明 |
|------|------|------|
| 定位系统 | Fast-LIO（200Hz特调版） | 高频实时激光惯性里程计，板载飞行主里程计 |
| 全局定位 | FASTLIO2_ROS2 | PGO回环检测 + Localizer重定位 + HBA地图优化 |
| 视觉定位 | ArUco标记检测 | 选配深度相机，实现标记6-DOF位姿解算 |
| 轨迹规划 | EGO-Planner | B样条轨迹优化，输出位置/速度/加速度前馈 |
| 飞控通信 | MAVROS | 对接PX4飞控，完成ROS2 ↔ MAVLink协议转换 |
| 状态管理 | yydrone_fsm 有限状态机 | 起飞、悬停、轨迹跟踪、故障恢复全流程管理 |

## 1.2 功能模块清单

| 模块包名 | 功能说明 |
|----------|----------|
| `yydrone_aruco` | ArUco标记检测与位姿估计、TF广播 |
| `yydrone_localization` | 激光定位桥接封装，对接MAVROS视觉位姿输入 |
| `yydrone_fsm` | 自主飞行有限状态机，执行起飞、悬停、轨迹跟踪、故障恢复 |
| `yydrone_control` | 键盘控制、飞行交互脚本 |
| `yydrone_description` | 无人机URDF仿真模型 |
| `yydrone_navigation` | EGO-Planner规划器集成封装 |
| `yydrone_mission` | 任务航点管理模块 |
| `fast_lio` | 特调版FAST-LIO，200Hz高频激光里程计 |
| `FASTLIO2_ROS2` | 全局定位系统（建图、回环、重定位、地图优化） |
| `livox_ros_driver2` | Livox激光雷达驱动 |
| `ego-planner-swarm` | EGO轨迹规划算法库 |
| `aruco_ros` | ArUco检测底层依赖库 |
| `OrbbecSDK_ROS2` | 奥比中深度相机驱动（选配） |

---

# 2. 系统架构

## 2.1 模块依赖关系

| 上层模块 | 依赖模块 | 接口类型 | 数据流向 | 说明 |
|----------|----------|----------|----------|------|
| yydrone_fsm | MAVROS | 话题+服务 | FSM → MAVROS | 下发控制指令、模式切换、解锁上锁 |
| yydrone_fsm | EGO-Planner | 话题 | EGO → FSM | 接收规划输出的轨迹指令 |
| yydrone_fsm | MAVROS | 话题 | MAVROS → FSM | 获取飞控状态、机体位姿反馈 |
| MAVROS | PX4 | MAVLink | 双向 | 飞控底层通信协议 |
| fastlio_locate.py | fast_lio | 话题 | fast_lio → bridge | 接收高频里程计输出 |
| fastlio_locate.py | MAVROS | 话题 | bridge → MAVROS | 将LIO位姿送入PX4 EKF融合 |
| fast_lio | livox_ros_driver2 | 话题 | 驱动 → LIO | 激光点云+IMU原始数据输入 |
| FASTLIO2/pgo | FASTLIO2/fastlio2 | 话题 | LIO → PGO | 里程计、局部地图输入用于回环优化 |
| FASTLIO2/localizer | 预建地图 | 服务 | 外部 → Localizer | 触发重定位请求 |
| FASTLIO2/hba | 地图碎片 | 服务 | 外部 → HBA | 触发大场景地图一致性优化 |
| EGO-Planner | 地图服务 | 话题 | 感知 → 规划 | ESDF障碍物地图输入 |
| EGO-Planner | yydrone_fsm | 话题 | 规划 → FSM | 输出完整轨迹指令 |
| yydrone_aruco | OrbbecSDK_ROS2 | 话题 | 相机 → ArUco | RGB图像、相机内参输入 |
| yydrone_aruco | MAVROS/TF | 话题+TF | ArUco → 系统 | 输出标记位姿、发布TF变换树 |

## 2.2 数据流架构

| 功能链路 | 数据流程 | 输出频率 | 备注 |
|----------|----------|----------|------|
| 高频实时定位 | Livox → fast_lio → fastlio_locate → MAVROS → PX4 | 200Hz | 实机飞行主里程计源 |
| 全局建图定位 | Livox → FASTLIO2/fastlio2 → PGO回环 → 优化全局地图 | 50Hz | 离线建图、丢失后重定位使用 |
| 视觉辅助定位 | 深度相机 → ArUco检测 → TF树发布 | 30Hz | 可选辅助定位模块 |
| 轨迹规划控制 | EGO-Planner → yydrone_fsm → MAVROS → PX4 | 50Hz | 输出位置+速度+加速度前馈 |
| 飞控状态监听 | PX4 → MAVROS → yydrone_fsm | 10Hz | 状态机决策依据 |

## 2.3 定位系统对比

| 对比项 | fast_lio（200Hz特调版） | FASTLIO2_ROS2 |
|--------|--------------------------|----------------|
| 核心用途 | 实时高频里程计，用于板载飞行 | 全局建图、回环校正、重定位、地图优化 |
| 输出频率 | 200Hz（特调优化） | 50Hz |
| 能力范围 | 仅里程计输出 | 里程计+回环检测+ICP重定位+地图优化 |
| 内部组成 | 单节点（laserMapping） | fastlio2 + pgo + localizer + hba 多节点 |
| 回环检测 | ❌ 无 | ✅ PGO位姿图优化（GTSAM） |
| 重定位能力 | ❌ 无 | ✅ 两阶段ICP（粗定位+精定位） |
| 地图优化 | ❌ 无 | ✅ BLAM小场景 / HBA大场景优化 |
| 适用场景 | 无人机板载实时自主飞行 | 离线建图、飞行丢失后重定位 |
| 输出话题 | `/Odom_high_freq` | `/mapping/odom` + 优化后全局地图 |

> 💡 **提示**：实机飞行时使用 fast_lio（200Hz）作为主里程计；FASTLIO2 主要用于离线建图和丢失后的重定位，不参与实时飞行控制。

---

# 3. 坐标系与控制模式

> ⚠️ **重要约定**：
> - ROS2 / 本系统对外接口统一使用 **FLU（前-左-上）**
> - PX4飞控内部使用 **FRD（前-右-下）**，MAVROS自动完成 FLU ↔ FRD 转换
> - 部分MAVROS控制接口需要传入 **NED 坐标系**，代码内必须做坐标转换！

## 3.1 坐标系定义

### 3.1.1 TF坐标系层级

惯性系（Inertial Frame）是固定在地面上的全局坐标系，用于描述无人机的绝对位置和姿态。

```
map（全局惯性系，fast_lio / FASTLIO2 发布）
 └─ odom（局部惯性系，MAVROS 发布）
     └─ base_link（机体系，FLU 约定）
         ├─ camera_color_optical_frame（相机光心坐标系）
         │    └─ aruco_marker_{id}（ArUco标记坐标系）
         └─ livox_frame（激光雷达本体坐标系）
```

### 3.1.2 坐标系轴定义

| 坐标系 | 全称 | X轴 | Y轴 | Z轴 | 使用场景 |
|--------|------|-----|-----|-----|----------|
| **FLU** | Forward-Left-Up | 前 | 左 | 上 | ROS2、本系统全部外部接口 |
| **FRD** | Forward-Right-Down | 前 | 右 | 下 | PX4飞控内部航空惯例 |
| **NED** | North-East-Down | 北 | 东 | 下 | GPS导航、MAVROS部分Raw控制接口 |
| **ENU** | East-North-Up | 东 | 北 | 上 | 地理GIS相关模块 |

> ✅ **说明**：
> 1. `fastlio_locate.py` 输出 `frame_id="map"`，作为PX4 EKF外部视觉定位输入；
> 2. EGO-Planner全部算法逻辑基于FLU坐标系。

### 3.1.3 机体坐标系（base_link / FLU）

- **X轴（Forward）**：指向无人机前方
- **Y轴（Left）**：指向无人机左侧
- **Z轴（Up）**：指向无人机上方
- **原点**：通常在无人机质心或IMU位置

**机体系与惯性系位置转换公式：**

```
P_inertial = R_body_to_inertial × P_body + T_body_to_inertial

  P_inertial  — 惯性系中的位置
  P_body      — 机体系中的位置
  R_body_to_inertial — 旋转矩阵（由姿态四元数计算）
  T_body_to_inertial — 平移向量（无人机在惯性系中的位置）
```

## 3.2 控制模式详解

### 3.2.1 位置控制（Position Control）

**控制变量**：目标位置 (x, y, z) + 偏航角 (yaw)

- 在惯性系中指定目标位置
- PX4内部闭环控制到达目标
- 适合定点飞行、航点导航

**MAVROS接口：**
- 话题：`/mavros/setpoint_position/local`
- 消息类型：`geometry_msgs/msg/PoseStamped`
- 坐标系：`frame_id = "map"`（惯性系）

```python
# Python 示例
pose = PoseStamped()
pose.header.frame_id = "map"
pose.pose.position.x = 5.0   # 惯性系X坐标
pose.pose.position.y = 3.0   # 惯性系Y坐标
pose.pose.position.z = 2.0   # 惯性系Z坐标
pose.pose.orientation.w = 1.0  # 偏航角0度
```

### 3.2.2 速度控制（Velocity Control）

**控制变量**：目标速度 (vx, vy, vz) + 偏航角速率 (yaw_rate)

- 在惯性系或机体系中指定速度
- 实时响应，灵活性高
- 适合遥控飞行、动态避障

**MAVROS接口：**
- 话题：`/mavros/setpoint_velocity/cmd_vel`
- 消息类型：`geometry_msgs/msg/TwistStamped`
- 坐标系：`frame_id = "map"`（惯性系）或 `"base_link"`（机体系）

**惯性系速度控制示例：**

```python
twist = TwistStamped()
twist.header.frame_id = "map"
twist.twist.linear.x = 1.0    # 惯性系X方向速度
twist.twist.linear.y = 0.5    # 惯性系Y方向速度
twist.twist.linear.z = 0.2    # 惯性系Z方向速度
twist.twist.angular.z = 0.1   # 偏航角速率
```

**机体系速度控制示例：**

```python
twist = TwistStamped()
twist.header.frame_id = "base_link"
twist.twist.linear.x = 1.0    # 机体前向速度
twist.twist.linear.y = 0.0    # 机体左向速度
twist.twist.linear.z = 0.0    # 机体上向速度
twist.twist.angular.z = 0.1   # 偏航角速率
```

### 3.2.3 位置+速度+加速度前馈控制

**控制变量**：位置 + 速度 + 加速度 + 偏航

- 提供速度和加速度前馈，显著提高轨迹跟踪精度
- EGO-Planner默认使用此模式
- 适合高速飞行、精确轨迹跟踪

**MAVROS接口：**
- 话题：`/mavros/setpoint_raw/local`
- 消息类型：`mavros_msgs/msg/PositionTarget`
- 坐标系：`FRAME_LOCAL_NED`（NED惯性系）

> ⚠️ **注意**：此接口使用 **NED 坐标系**！从EGO-Planner（FLU）收到指令后，必须先做 FLU → NED 坐标转换再发布。yydrone_fsm 内部已完成此转换。

```cpp
// C++ 示例（yydrone_fsm 使用）
PositionTarget target;
target.coordinate_frame = PositionTarget::FRAME_LOCAL_NED;
target.type_mask = PositionTarget::IGNORE_YAW_RATE;

// 位置（NED惯性系）
target.position.x = cmd.position.x;
target.position.y = cmd.position.y;
target.position.z = cmd.position.z;

// 速度前馈（NED惯性系）
target.velocity.x = cmd.velocity.x * velocity_scale_;
target.velocity.y = cmd.velocity.y * velocity_scale_;
target.velocity.z = cmd.velocity.z * velocity_scale_;

// 加速度前馈（NED惯性系）
target.acceleration_or_force.x = cmd.acceleration.x * acceleration_scale_;
target.acceleration_or_force.y = cmd.acceleration.y * acceleration_scale_;
target.acceleration_or_force.z = cmd.acceleration.z * acceleration_scale_;

// 偏航角（NED惯性系）
target.yaw = cmd.yaw;
```

### 3.2.4 姿态控制（Attitude Control）

**控制变量**：目标姿态（roll, pitch, yaw）+ 油门

- 直接控制无人机姿态，响应最快
- 需要手动控制油门，飞行难度高
- 适合特技飞行、姿态测试（本系统默认不使用）

**MAVROS接口：**
- 话题：`/mavros/setpoint_raw/attitude`
- 消息类型：`mavros_msgs/msg/AttitudeTarget`

```cpp
AttitudeTarget att;
att.type_mask = 7;  // 忽略body rate

// 姿态四元数（仅偏航）
att.orientation.w = cos(yaw / 2);
att.orientation.x = 0;
att.orientation.y = 0;
att.orientation.z = sin(yaw / 2);

// 油门（0-1）
att.thrust = 0.6;
```

## 3.3 坐标系转换关系

### 3.3.1 惯性系之间转换（FLU ↔ NED）

**FLU → NED：**

```
x_ned = x_flu
y_ned = -y_flu
z_ned = -z_flu
```

**NED → FLU：**

```
x_flu = x_ned
y_flu = -y_ned
z_flu = -z_ned
```

### 3.3.2 机体系与惯性系转换

**机体系速度 → 惯性系速度（Python示例）：**

```python
import tf_transformations
import numpy as np

# 获取当前姿态（四元数）
q = [odom.pose.pose.orientation.x,
     odom.pose.pose.orientation.y,
     odom.pose.pose.orientation.z,
     odom.pose.pose.orientation.w]

# 四元数转旋转矩阵
R = tf_transformations.quaternion_matrix(q)[:3, :3]

# 机体系速度
v_body = np.array([vx_body, vy_body, vz_body])

# 转换为惯性系速度
v_inertial = R @ v_body
```

## 3.4 外参配置

### 3.4.1 激光雷达到IMU（fast_lio / FASTLIO2）

**配置文件位置：**
- `fast_lio/config/livox_mid360.yaml`
- `FASTLIO2_ROS2/fastlio2/config/xxx.yaml`

```yaml
mapping:
  extrinsic_T: [0.04, 0.0, 0.05]   # LiDAR到IMU平移向量 [x,y,z] (m)
  extrinsic_R: [1, 0, 0,             # LiDAR到IMU旋转矩阵 3x3
                0, 1, 0,
                0, 0, 1]
```

**测量方法：**
1. 使用CAD模型或实测距离获取平移向量
2. 激光雷达原点通常在外壳中心
3. IMU原点在其几何中心
4. 旋转矩阵需考虑两传感器的安装方向差异

### 3.4.2 相机到机体（ArUco）

**配置方式：通过TF树静态发布**

```bash
# 命令行方式
ros2 run tf2_ros static_transform_publisher \
  --x 0.1 --y 0.0 --z -0.05 \
  --roll 0 --pitch 0 --yaw 0 \
  --frame-id base_link \
  --child-frame-id camera_color_optical_frame
```

**或在 launch 文件中配置：**

```python
from launch_ros.actions import Node

Node(
    package="tf2_ros",
    executable="static_transform_publisher",
    arguments=["0.1", "0", "-0.05", "0", "0", "0",
               "base_link", "camera_color_optical_frame"]
)
```

---

# 4. 核心模块接口

## 4.1 yydrone_fsm（飞行状态机）

### 4.1.1 节点信息

| 项目 | 内容 |
|------|------|
| 节点名称 | `egoplanner_fsm_node` |
| 功能描述 | 管理无人机飞行状态，实现自动起飞、悬停、轨迹跟踪、故障恢复 |
| 控制模式 | 位置+速度+加速度前馈控制（NED惯性系） |
| 控制频率 | 50Hz |

### 状态定义

| 状态 | 名称 | 说明 |
|------|------|------|
| INIT | 初始化 | 等待飞控连接、参数加载 |
| PREFLIGHT | 飞前准备 | 发送100帧初始设定点 → 切换OFFBOARD → 解锁 |
| TAKEOFF | 起飞阶段 | 上升到目标高度（target_height） |
| HOVER_WAIT | 悬停等待 | 悬停并等待规划器指令 |
| CMD_CONTROL | 指令跟踪 | 跟踪EGO规划器指令，超时返回HOVER_WAIT |
| ERROR | 错误恢复 | 故障状态，10秒后自动重置到INIT |

### 4.1.2 订阅话题

| 话题名称 | 消息类型 | 坐标系 | 说明 |
|----------|----------|--------|------|
| `/mavros/state` | `mavros_msgs/msg/State` | N/A | 飞控状态（连接、解锁、模式） |
| `/mavros/local_position/odom` | `nav_msgs/msg/Odometry` | 惯性系(map) | 本地位置里程计反馈 |
| `/drone_0_planning/pos_cmd` | `quadrotor_msgs/msg/PositionCommand` | 惯性系 | EGO规划器位置指令 |

### 4.1.3 发布话题

| 话题名称 | 消息类型 | 频率 | 坐标系 | 说明 |
|----------|----------|------|--------|------|
| `/mavros/setpoint_raw/local` | `mavros_msgs/msg/PositionTarget` | 50Hz | NED惯性系 | 位置+速度+加速度前馈控制 |
| `/mavros/setpoint_raw/attitude` | `mavros_msgs/msg/AttitudeTarget` | 50Hz | 机体系 | 姿态控制（备用，use_attitude_control=true时） |
| `~/drone_state` | `std_msgs/msg/String` | 10Hz | N/A | 当前状态机状态字符串 |

### 4.1.4 服务客户端

| 服务名称 | 服务类型 | 说明 |
|----------|----------|------|
| `/mavros/cmd/arming` | `mavros_msgs/srv/CommandBool` | 解锁/上锁无人机 |
| `/mavros/set_mode` | `mavros_msgs/srv/SetMode` | 切换飞行模式（OFFBOARD） |

### 4.1.5 参数配置

| 参数名称 | 类型 | 默认值 | 说明 |
|----------|------|--------|------|
| `target_height` | double | 1.0 | 起飞目标高度（米，惯性系Z） |
| `takeoff_speed` | double | 0.5 | 起飞速度（米/秒） |
| `max_horizontal_speed` | double | 1.0 | 最大水平速度（米/秒） |
| `control_frequency` | double | 50.0 | 控制循环频率（Hz） |
| `cmd_timeout` | double | 0.5 | 规划指令超时时间（秒），超时后悬停 |
| `velocity_scale` | double | 0.5 | 速度前馈缩放系数（0-1） |
| `acceleration_scale` | double | 0.3 | 加速度前馈缩放系数（0-1） |
| `use_attitude_control` | bool | false | 是否使用姿态控制模式 |

### 4.1.6 核心方法

**状态转换逻辑：**

```cpp
void SimpleFSM::stateMachine()
{
  switch (current_state_) {
    case DroneState::INIT:        // 等待飞控连接
    case DroneState::PREFLIGHT:   // 发送100帧初始设定点 → 切OFFBOARD → 解锁
    case DroneState::TAKEOFF:     // 上升到目标高度
    case DroneState::HOVER_WAIT:  // 悬停并等待规划指令
    case DroneState::CMD_CONTROL: // 跟踪规划器指令（超时返回HOVER_WAIT）
    case DroneState::ERROR:       // 错误恢复（10秒后重置到INIT）
  }
}
```

**控制指令发布（NED惯性系）：**

```cpp
void SimpleFSM::publishCommandPosition()
{
  target.coordinate_frame = FRAME_LOCAL_NED;  // NED惯性系
  target.type_mask = IGNORE_YAW_RATE;  // 位置+速度+加速度+偏航

  // 所有变量均已转换到NED惯性系
  target.position = last_cmd_.position;
  target.velocity = last_cmd_.velocity * velocity_scale_;
  target.acceleration = last_cmd_.acceleration * acceleration_scale_;
  target.yaw = last_cmd_.yaw;
}
```

## 4.2 fast_lio（高频激光里程计）

### 4.2.1 节点信息

| 项目 | 内容 |
|------|------|
| 节点名称 | `laserMapping` |
| 功能描述 | 200Hz特调版FAST-LIO激光惯性里程计，提供高频位姿估计 |
| 输出坐标系 | FLU惯性系（map frame） |
| 输出频率 | 200Hz（特调优化） |
| 特点 | 纯里程计功能，无回环检测，适合板载实时飞行 |

### 4.2.2 订阅话题

| 话题名称 | 消息类型 | 说明 |
|----------|----------|------|
| `/livox/lidar` | `sensor_msgs/msg/PointCloud2` | Livox激光雷达点云 |
| `/livox/imu` | `sensor_msgs/msg/Imu` | IMU数据（机体系） |

### 4.2.3 发布话题

| 话题名称 | 消息类型 | 频率 | 坐标系 | 说明 |
|----------|----------|------|--------|------|
| `/Odom_high_freq` | `nav_msgs/msg/Odometry` | 200Hz | FLU惯性系 | 高频里程计输出（主输出） |
| `/cloud_registered` | `sensor_msgs/msg/PointCloud2` | 10Hz | FLU惯性系 | 配准后点云 |
| `/path` | `nav_msgs/msg/Path` | 1Hz | FLU惯性系 | 轨迹路径 |

### 4.2.4 参数配置

| 参数名称 | 类型 | 说明 |
|----------|------|------|
| `lid_topic` | string | 激光雷达话题名 |
| `imu_topic` | string | IMU话题名 |
| `extrinsic_T` | array | LiDAR到IMU平移向量 [x, y, z]（机体系，米） |
| `extrinsic_R` | array | LiDAR到IMU旋转矩阵（3x3行优先） |

## 4.3 FASTLIO2_ROS2（全局定位系统）

### 4.3.1 系统架构

FASTLIO2_ROS2 由4个功能模块组成：

| 模块 | 功能 | 节点名称 | 输入 | 输出 |
|------|------|----------|------|------|
| fastlio2 | 激光惯性里程计 | `lio_node` | 点云+IMU | 里程计+局部地图 |
| pgo | 位姿图优化（回环） | `pgo_node` | 里程计+地图 | 优化后轨迹+全局地图 |
| localizer | 重定位 | `localizer_node` | 当前扫描+全局地图 | 重定位位姿 |
| hba | 地图一致性优化 | `hba_node` | 地图碎片 | 优化后一致性地图 |

### 4.3.2 fastlio2 模块

**订阅话题：**

| 话题名称 | 消息类型 | 说明 |
|----------|----------|------|
| `/livox/lidar` | `sensor_msgs/msg/PointCloud2` | 激光雷达点云 |
| `/livox/imu` | `sensor_msgs/msg/Imu` | IMU数据 |

**发布话题：**

| 话题名称 | 消息类型 | 频率 | 坐标系 | 说明 |
|----------|----------|------|--------|------|
| `/mapping/odom` | `nav_msgs/msg/Odometry` | 50Hz | FLU惯性系 | 里程计输出 |
| `/mapping/cloud` | `sensor_msgs/msg/PointCloud2` | 10Hz | FLU惯性系 | 局部地图 |
| `/mapping/path` | `nav_msgs/msg/Path` | 1Hz | FLU惯性系 | 轨迹路径 |

### 4.3.3 pgo 模块（回环检测）

**功能**：基于位置先验+ICP的回环检测，使用GTSAM进行位姿图优化。

**订阅话题：**

| 话题名称 | 消息类型 | 说明 |
|----------|----------|------|
| `/mapping/odom` | `nav_msgs/msg/Odometry` | fastlio2里程计 |
| `/mapping/cloud` | `sensor_msgs/msg/PointCloud2` | 局部地图 |

**发布话题：**

| 话题名称 | 消息类型 | 说明 |
|----------|----------|------|
| `/pgo/odom` | `nav_msgs/msg/Odometry` | 优化后里程计 |
| `/pgo/path` | `nav_msgs/msg/Path` | 优化后轨迹 |
| `/pgo/global_map` | `sensor_msgs/msg/PointCloud2` | 全局地图 |

**服务接口：**

| 服务名称 | 服务类型 | 说明 |
|----------|----------|------|
| `/pgo/save_maps` | `interface/srv/SaveMaps` | 保存优化后的地图 |

**服务请求参数：**

```
string file_path      # 保存路径
bool save_patches     # 是否保存地图碎片（HBA优化需要，必须设true）
```

### 4.3.4 localizer 模块（重定位）

**功能**：基于两阶段ICP（粗定位+精定位）的重定位。

**服务接口：**

| 服务名称 | 服务类型 | 说明 |
|----------|----------|------|
| `/localizer/relocalize` | `interface/srv/Relocalize` | 执行重定位 |
| `/localizer/relocalize_check` | `interface/srv/IsValid` | 检查重定位结果是否有效 |

**重定位请求参数：**

```
string pcd_path    # 全局地图路径
float64 x          # 初始位置X（米，惯性系）
float64 y          # 初始位置Y（米，惯性系）
float64 z          # 初始位置Z（米，惯性系）
float64 yaw        # 初始偏航角（弧度）
float64 pitch      # 初始俯仰角（弧度）
float64 roll       # 初始横滚角（弧度）
```

### 4.3.5 hba 模块（地图优化）

**功能**：地图一致性优化。

- **BLAM算法** — 小场景地图优化
- **HBA算法** — 大场景地图优化

**服务接口：**

| 服务名称 | 服务类型 | 说明 |
|----------|----------|------|
| `/hba/refine_map` | `interface/srv/RefineMap` | 执行地图一致性优化 |

**优化请求参数：**

```
string maps_path    # 地图碎片目录（需PGO保存时设置 save_patches=true）
```

## 4.4 yydrone_aruco（ArUco视觉定位）

> 💡 **提示**：本模块为选配模块，需要深度相机（Orbbec Astra系列或其他兼容ROS2的RGB-D相机）。

### 4.4.1 节点信息

| 项目 | 内容 |
|------|------|
| 节点名称 | `aruco_detector` |
| 功能描述 | 检测ArUco标记并估计6-DOF位姿 |
| 输出坐标系 | 相机光轴坐标系（camera_color_optical_frame） |

### 4.4.2 订阅话题

| 话题名称 | 消息类型 | 说明 |
|----------|----------|------|
| `/camera/color/image_raw` | `sensor_msgs/msg/Image` | RGB图像流 |
| `/camera/color/camera_info` | `sensor_msgs/msg/CameraInfo` | 相机内参（仅订阅一次） |

### 4.4.3 发布话题

| 话题名称 | 消息类型 | 坐标系 | 说明 |
|----------|----------|--------|------|
| `~/aruco_pose` | `geometry_msgs/msg/PoseStamped` | 相机系 | 检测到的标记位姿 |
| `~/aruco_transform` | `geometry_msgs/msg/TransformStamped` | 相机系 | TF变换消息 |
| `~/debug_image` | `sensor_msgs/msg/Image` | N/A | 标注了检测结果的调试图像 |

### 4.4.4 TF广播

- **父坐标系**：`camera_color_optical_frame`
- **子坐标系**：`aruco_marker_{id}`（例如 `aruco_marker_0`）

### 4.4.5 参数配置

| 参数名称 | 类型 | 默认值 | 说明 |
|----------|------|--------|------|
| `image_topic` | string | `/camera/color/image_raw` | 图像话题 |
| `camera_info_topic` | string | `/camera/color/camera_info` | 相机信息话题 |
| `camera_frame` | string | `camera_color_optical_frame` | 相机坐标系名称 |
| `dictionary` | string | `DICT_6X6_250` | ArUco字典类型 |
| `marker_size` | double | 0.15 | 标记物理尺寸（米） |
| `publish_debug_image` | bool | true | 是否发布调试图像 |
| `publish_all_markers` | bool | true | 是否发布所有检测到的标记 |
| `target_marker_id` | int | 0 | 目标标记ID |

### 4.4.6 支持的ArUco字典

- `DICT_4X4_50 / 100 / 250 / 1000`
- `DICT_5X5_50 / 100 / 250 / 1000`
- `DICT_6X6_50 / 100 / 250 / 1000`
- `DICT_7X7_50 / 100 / 250 / 1000`
- `DICT_ARUCO_ORIGINAL`
- `ALL`（遍历所有字典，自动识别）

## 4.5 ros2_keyboard_control.py（键盘控制）

### 4.5.1 节点信息

| 项目 | 内容 |
|------|------|
| 节点名称 | `keyboard_drone_controller` |
| 功能描述 | 通过键盘实现无人机的速度控制和位置控制 |
| 控制坐标系 | 惯性系（map） |

### 4.5.2 控制模式

**模式1：速度控制（惯性系）**

| 按键 | 功能 | 说明 |
|------|------|------|
| W / S | 前进 / 后退 | 惯性系X方向 |
| A / D | 左移 / 右移 | 惯性系Y方向 |
| K / J | 上升 / 下降 | 惯性系Z方向 |
| Q / E | 左转 / 右转 | 偏航角速率 |
| X | 急停 | 立即停止所有运动 |
| 1 / 2 | 降低 / 增大速度 | 调整飞行速度 |

**模式2：位置控制（惯性系）**

| 按键 | 功能 | 说明 |
|------|------|------|
| W / S | 增加 / 减少X坐标 | 惯性系X方向 |
| A / D | 增加 / 减少Y坐标 | 惯性系Y方向 |
| K / J | 增加 / 减少Z坐标 | 惯性系Z方向 |
| Q / E | 增加 / 减少偏航角 | 调整朝向 |
| P | 直接输入目标坐标 | 手动输入(x,y,z,yaw) |
| N | 开始 / 停止导航 | 切换导航状态 |
| R | 重置到原点 | 回到起飞点 |

**通用指令：**

| 按键 | 功能 |
|------|------|
| 空格 | 解锁 / 上锁 |
| O | 切换OFFBOARD模式 |
| M | 返回模式选择 |
| ESC | 退出程序 |

### 4.5.3 发布话题

| 话题名称 | 消息类型 | 控制模式 | 频率 | 坐标系 |
|----------|----------|----------|------|--------|
| `/mavros/setpoint_velocity/cmd_vel` | `geometry_msgs/msg/TwistStamped` | 速度控制 | 50Hz | 惯性系 |
| `/mavros/setpoint_position/local` | `geometry_msgs/msg/PoseStamped` | 位置控制 | 50Hz | 惯性系 |

### 4.5.4 核心参数

```python
self.linear_speed = 1.0        # 水平速度 (m/s)
self.angular_speed = 1.0       # 转向速度 (rad/s)
self.vertical_speed = 1.0      # 垂直速度 (m/s)
self.position_step = 0.5       # 位置增量 (m)
self.yaw_step = 0.2            # 偏航增量 (rad)
```

---

# 5. 话题接口规范

## 5.1 quadrotor_msgs/msg/PositionCommand

| 项目 | 内容 |
|------|------|
| 来源 | EGO-Planner规划器 |
| 用途 | 包含位置、速度、加速度、偏航的完整轨迹指令 |
| 坐标系 | FLU惯性系 |
| 消费方 | yydrone_fsm（订阅后转发到MAVROS） |

```
# 位置指令 (m, FLU惯性系)
geometry_msgs/Point position
  float64 x
  float64 y
  float64 z

# 速度前馈 (m/s, FLU惯性系)
geometry_msgs/Vector3 velocity
  float64 x
  float64 y
  float64 z

# 加速度前馈 (m/s², FLU惯性系)
geometry_msgs/Vector3 acceleration
  float64 x
  float64 y
  float64 z

# 偏航角 (rad, FLU惯性系)
float64 yaw

# 偏航角速率 (rad/s)
float64 yaw_dot
```

**使用示例（FSM订阅回调）：**

```cpp
// C++
void SimpleFSM::commandCallback(const PositionCommand::SharedPtr msg)
{
  last_cmd_ = *msg;
  last_cmd_time_ = this->now();
  cmd_received_ = true;
}
```

## 5.2 mavros_msgs/msg/PositionTarget

| 项目 | 内容 |
|------|------|
| 来源 | yydrone_fsm、ego2mavros |
| 目标话题 | `/mavros/setpoint_raw/local` |
| 用途 | PX4位置+速度+加速度前馈控制 |
| 坐标系 | NED惯性系（注意：与EGO-Planner的FLU不同！） |

```
Header header
uint8 coordinate_frame  # FRAME_LOCAL_NED = 1
uint16 type_mask        # 控制掩码（见下文）

geometry_msgs/Point position      # 目标位置 (m, NED惯性系)
geometry_msgs/Vector3 velocity    # 速度前馈 (m/s, NED惯性系)
geometry_msgs/Vector3 acceleration_or_force  # 加速度前馈 (m/s², NED惯性系)
float32 yaw            # 偏航角 (rad, 惯性系)
float32 yaw_rate       # 偏航角速率 (rad/s)
```

### type_mask 位定义

| 掩码常量 | 值 | 二进制 | 说明 |
|----------|----|--------|------|
| IGNORE_PX | 1 | 0b000000001 | 忽略位置X |
| IGNORE_PY | 2 | 0b000000010 | 忽略位置Y |
| IGNORE_PZ | 4 | 0b000000100 | 忽略位置Z |
| IGNORE_VX | 8 | 0b000001000 | 忽略速度X |
| IGNORE_VY | 16 | 0b000010000 | 忽略速度Y |
| IGNORE_VZ | 32 | 0b000100000 | 忽略速度Z |
| IGNORE_AFX | 64 | 0b001000000 | 忽略加速度X |
| IGNORE_AFY | 128 | 0b010000000 | 忽略加速度Y |
| IGNORE_AFZ | 256 | 0b100000000 | 忽略加速度Z |
| IGNORE_YAW | 1024 | - | 忽略偏航角 |
| IGNORE_YAW_RATE | 2048 | - | 忽略偏航角速率 |

### 常用组合

```cpp
// 纯位置控制（忽略速度和加速度）
type_mask = IGNORE_VX | IGNORE_VY | IGNORE_VZ |
            IGNORE_AFX | IGNORE_AFY | IGNORE_AFZ |
            IGNORE_YAW_RATE;

// 位置+速度+加速度前馈（FSM默认，仅忽略yaw_rate）
type_mask = IGNORE_YAW_RATE;
```

## 5.3 mavros_msgs/msg/State

| 项目 | 内容 |
|------|------|
| 来源话题 | `/mavros/state` |
| 频率 | ~1Hz |
| 用途 | 飞控状态监听（连接、解锁、飞行模式） |

```
Header header
bool connected      # MAVROS与飞控的连接状态
bool armed          # 解锁状态（true=已解锁）
bool guided         # 引导模式（本系统未使用）
bool manual_input   # 手动输入（本系统未使用）
string mode         # 当前飞行模式（"OFFBOARD", "MANUAL"等）
uint8 system_status # 系统状态（MAV_STATE枚举）
```

**使用示例：**

```cpp
// C++
void SimpleFSM::stateCallback(const State::SharedPtr msg)
{
  current_fcu_state_ = *msg;
  is_armed_ = current_fcu_state_.armed;

  if (msg->mode == "OFFBOARD" && !offboard_engaged_) {
    offboard_engaged_ = true;
    RCLCPP_INFO(get_logger(), "成功切换到OFFBOARD模式");
  }
}
```

## 5.4 nav_msgs/msg/Odometry

| 项目 | 内容 |
|------|------|
| 来源话题 | `/mavros/local_position/odom`、`/Odom_high_freq`（fast_lio）、`/mapping/odom`（FASTLIO2） |
| 频率 | 50~200Hz（视来源而定） |
| 用途 | 位置反馈、速度反馈 |
| 坐标系 | FLU惯性系（pose）；twist可能是惯性系或机体系，需看child_frame_id |

```
Header header
string child_frame_id  # "base_link"（机体系）

geometry_msgs/PoseWithCovariance pose
  Pose pose
    Point position     # 位置 (m, 惯性系)
    Quaternion orientation  # 姿态四元数（惯性系到机体系）
  float64[36] covariance

geometry_msgs/TwistWithCovariance twist
  Twist twist
    Vector3 linear     # 线速度 (m/s, 惯性系或机体系)
    Vector3 angular    # 角速度 (rad/s, 机体系)
  float64[36] covariance
```

---

# 6. 服务接口规范

## 6.1 mavros_msgs/srv/CommandBool

| 项目 | 内容 |
|------|------|
| 服务名称 | `/mavros/cmd/arming` |
| 功能 | 解锁/上锁无人机 |

**请求：**

```
bool value  # true=解锁, false=上锁
```

**响应：**

```
bool success  # 是否成功
uint8 result  # MAV_RESULT枚举
```

**使用示例：**

```python
# Python
req = CommandBool.Request()
req.value = True
future = self.arming_client.call_async(req)
```

```cpp
// C++
auto request = std::make_shared<CommandBool::Request>();
request->value = true;
arming_client_->async_send_request(request);
```

## 6.2 mavros_msgs/srv/SetMode

| 项目 | 内容 |
|------|------|
| 服务名称 | `/mavros/set_mode` |
| 功能 | 切换飞行模式 |

**请求：**

```
uint8 base_mode      # 基础模式（通常不使用）
string custom_mode   # 自定义模式字符串
```

**PX4常用模式：**

| 模式字符串 | 说明 |
|------------|------|
| `MANUAL` | 手动模式 |
| `STABILIZED` | 自稳模式 |
| `ALTCTL` | 定高模式 |
| `POSCTL` | 定点模式 |
| `OFFBOARD` | 板外控制模式（本系统核心模式） |
| `AUTO.TAKEOFF` | 自动起飞 |
| `AUTO.LAND` | 自动降落 |

**响应：**

```
bool mode_sent  # 模式命令是否已发送
```

**使用示例：**

```cpp
// C++
auto request = std::make_shared<SetMode::Request>();
request->custom_mode = "OFFBOARD";
set_mode_client_->async_send_request(request);
```

## 6.3 interface/srv/SaveMaps

| 项目 | 内容 |
|------|------|
| 服务名称 | `/pgo/save_maps` |
| 功能 | 保存PGO优化后的地图 |

**请求：**

```
string file_path      # 保存路径
bool save_patches     # 是否保存地图碎片（HBA优化需要，建议设true）
```

**响应：**

```
bool success  # 是否成功
string message  # 结果信息
```

## 6.4 interface/srv/Relocalize

| 项目 | 内容 |
|------|------|
| 服务名称 | `/localizer/relocalize` |
| 功能 | 执行重定位（两阶段ICP） |

**请求：**

```
string pcd_path    # 全局地图路径
float64 x          # 初始位置X（米，惯性系）
float64 y          # 初始位置Y（米，惯性系）
float64 z          # 初始位置Z（米，惯性系）
float64 yaw        # 初始偏航角（弧度）
float64 pitch      # 初始俯仰角（弧度）
float64 roll       # 初始横滚角（弧度）
```

**响应：**

```
bool success  # 是否成功
```

## 6.5 interface/srv/RefineMap

| 项目 | 内容 |
|------|------|
| 服务名称 | `/hba/refine_map` |
| 功能 | 执行地图一致性优化 |

**请求：**

```
string maps_path    # 地图碎片目录（PGO保存时save_patches=true生成）
```

**响应：**

```
bool success  # 是否成功
string refined_map_path  # 优化后地图路径
```

---

# 7. 消息类型定义

## 7.1 geometry_msgs/msg/PoseStamped

坐标系：由 `header.frame_id` 指定（`"map"`=惯性系，`"base_link"`=机体系）

```
Header header
  builtin_interfaces/Time stamp
  string frame_id  # 坐标系名称

Pose pose
  Point position  # 位置
    float64 x
    float64 y
    float64 z
  Quaternion orientation  # 姿态四元数
    float64 x
    float64 y
    float64 z
    float64 w
```

## 7.2 geometry_msgs/msg/TwistStamped

坐标系：由 `header.frame_id` 指定（`"map"`=惯性系，`"base_link"`=机体系）

```
Header header
Twist twist
  Vector3 linear    # 线速度 (m/s)
    float64 x
    float64 y
    float64 z
  Vector3 angular   # 角速度 (rad/s, 通常在机体系)
    float64 x
    float64 y
    float64 z
```

## 7.3 sensor_msgs/msg/Image

```
Header header
uint32 height           # 图像高度（像素）
uint32 width            # 图像宽度（像素）
string encoding         # 编码格式（"bgr8", "rgb8", "mono8"等）
uint8 is_bigendian
uint32 step             # 每行字节数
uint8[] data            # 图像数据
```

## 7.4 sensor_msgs/msg/CameraInfo

```
Header header
uint32 height
uint32 width

string distortion_model  # "plumb_bob", "rational_polynomial"
float64[] d              # 畸变系数 [k1, k2, t1, t2, k3]
float64[9] k             # 相机内参矩阵（3x3，行优先）
float64[9] r             # 校正矩阵
float64[12] p            # 投影矩阵

uint32 binning_x
uint32 binning_y
RegionOfInterest roi
```

**K矩阵布局：**

```
K = [fx  0  cx]
    [ 0 fy  cy]
    [ 0  0   1]
```

---

# 8. 参数配置接口

## 8.1 yydrone_fsm 参数

```yaml
egoplanner_fsm_node:
  ros__parameters:
    target_height: 1.0              # 起飞目标高度（米，惯性系Z）
    takeoff_speed: 0.5              # 起飞速度（米/秒）
    max_horizontal_speed: 1.0       # 最大水平速度（米/秒）
    control_frequency: 50.0         # 控制频率（Hz）
    cmd_timeout: 0.5                # 指令超时（秒），超时后悬停
    velocity_scale: 0.5             # 速度前馈缩放（0-1）
    acceleration_scale: 0.3         # 加速度前馈缩放（0-1）
    use_attitude_control: false     # 是否使用姿态控制模式
```

**运行时动态修改：**

```bash
ros2 param set /egoplanner_fsm_node velocity_scale 0.7
ros2 param set /egoplanner_fsm_node acceleration_scale 0.4
```

> ⚠️ **注意**：`velocity_scale` 和 `acceleration_scale` 是飞行安全的关键参数。首次试飞建议设为 0.3 / 0.2，确认飞行稳定后再逐步增大。

## 8.2 yydrone_aruco 参数

配置文件：`yydrone_aruco/config/aruco_params.yaml`

```yaml
arucu_detector:
  ros__parameters:
    image_topic: "/camera/color/image_raw"
    camera_info_topic: "/camera/color/camera_info"
    camera_frame: "camera_color_optical_frame"
    dictionary: "DICT_6X6_250"      # ArUco字典类型
    marker_size: 0.15               # 标记物理尺寸（米）
    publish_debug_image: true       # 发布调试图像
    publish_all_markers: true       # 发布所有检测到的标记
    target_marker_id: 0             # 目标标记ID
```

## 8.3 fast_lio 参数

配置文件：`fast_lio/config/livox_mid360.yaml`

```yaml
common:
  lid_topic: "/livox/lidar"         # 激光雷达话题
  imu_topic: "/livox/imu"           # IMU话题
  time_sync_en: false               # 时间同步（Livox自带时间戳时关闭）

preprocess:
  lidar_type: 1                     # 1=Livox
  scan_line: 4                      # Mid-360为4线
  timestamp_unit: 3                 # 0=s, 1=ms, 2=us, 3=ns

mapping:
  extrinsic_T: [0.0, 0.0, 0.0]     # LiDAR到IMU平移（米，机体系）
  extrinsic_R: [1, 0, 0,            # LiDAR到IMU旋转矩阵（3x3行优先）
                0, 1, 0,
                0, 0, 1]

publish:
  path_en: true                     # 发布轨迹
  scan_publish_en: true             # 发布扫描点云
  dense_publish_en: true            # 发布稠密点云
  scan_bodyframe_pub_en: false      # 发布机体系点云
```

## 8.4 FASTLIO2 参数

配置文件：`FASTLIO2_ROS2/fastlio2/config/xxx.yaml`

```yaml
common:
  lid_topic: "/livox/lidar"
  imu_topic: "/livox/imu"

mapping:
  extrinsic_T: [0.0, 0.0, 0.0]
  extrinsic_R: [1, 0, 0,
                0, 1, 0,
                0, 0, 1]

pgo:
  loop_closure_distance: 10.0       # 回环检测距离阈值（米）
  loop_closure_score: 0.5           # ICP匹配得分阈值（越高越严格）

localizer:
  coarse_icp_voxel_size: 0.5        # 粗定位体素大小（米）
  fine_icp_voxel_size: 0.1          # 精定位体素大小（米）

hba:
  optimization_method: "HBA"        # "BLAM"（小场景） or "HBA"（大场景）
```

---

# 9. Python 脚本接口

## 9.1 fastlio_locate.py

| 项目 | 内容 |
|------|------|
| 功能 | fast_lio → MAVROS 桥接，将高频里程计转为视觉位姿输入PX4 EKF2 |
| 输入话题 | `/Odom_high_freq`（fast_lio，200Hz，FLU惯性系） |
| 输出话题 | `/mavros/vision_pose/pose`（100Hz降采样，FLU惯性系） |
| 运行方式 | `python3 src/fastlio_locate.py` 或 `ros2 run yydrone_localization fastlio_locate.py` |

```bash
# 直接运行
python3 src/fastlio_locate.py

# 或作为ROS2节点运行
ros2 run yydrone_localization fastlio_locate.py
```

**必需前置条件：**
1. fast_lio 节点正在运行，`/Odom_high_freq` 正常发布
2. MAVROS 节点正在运行，与PX4飞控连接正常

> ⚠️ **注意**：PX4端必须配置 `EKF2_AID_MASK=24`（启用视觉位置+偏航融合），`EKF2_EV_DELAY=0`（视觉延迟补偿，毫秒），否则视觉位姿输入不会被EKF融合。

## 9.2 ros2_keyboard_control.py

| 项目 | 内容 |
|------|------|
| 功能 | 键盘遥控无人机，支持速度控制和位置控制两种模式 |
| 运行方式 | `python3 src/ros2_keyboard_control.py` |
| 控制坐标系 | 惯性系（map） |

```bash
python3 src/ros2_keyboard_control.py
```

**使用流程：**
1. 启动后选择控制模式（1=速度控制，2=位置控制）
2. 按 `O` 切换到 OFFBOARD 模式
3. 按 `空格` 解锁无人机
4. 使用 `W/A/S/D`、`K/J`、`Q/E` 进行控制
5. 按 `ESC` 退出程序

---

# 10. MAVROS 完整话题列表

## 10.1 状态与诊断话题

| 话题名称 | 消息类型 | 方向 | 频率 | 说明 |
|----------|----------|------|------|------|
| `/mavros/state` | `mavros_msgs/msg/State` | 订阅 | 1Hz | 飞控状态（连接、解锁、模式） |
| `/mavros/extended_state` | `mavros_msgs/msg/ExtendedState` | 订阅 | 1Hz | 扩展状态（降落检测、飞行状态） |
| `/mavros/battery` | `sensor_msgs/msg/BatteryState` | 订阅 | 1Hz | 电池状态 |
| `/mavros/estimator_status` | `mavros_msgs/msg/EstimatorStatus` | 订阅 | 1Hz | 估计器状态（EKF健康度） |
| `/mavros/sys_status` | `mavros_msgs/msg/SysStatus` | 订阅 | 1Hz | 系统状态 |
| `/mavros/mission/reached` | `mavros_msgs/msg/WaypointReached` | 订阅 | 事件 | 到达航点事件 |
| `/mavros/statustext/recv` | `mavros_msgs/msg/StatusText` | 订阅 | 事件 | 飞控状态文本消息（重要报错信息） |

## 10.2 位置与姿态话题

| 话题名称 | 消息类型 | 方向 | 频率 | 坐标系 | 说明 |
|----------|----------|------|------|--------|------|
| `/mavros/local_position/pose` | `geometry_msgs/msg/PoseStamped` | 订阅 | 50Hz | FLU惯性系 | 本地位置与姿态 |
| `/mavros/local_position/odom` | `nav_msgs/msg/Odometry` | 订阅 | 50Hz | FLU惯性系 | 本地里程计（含速度） |
| `/mavros/local_position/velocity_local` | `geometry_msgs/msg/TwistStamped` | 订阅 | 50Hz | FLU惯性系 | 本地速度（惯性系） |
| `/mavros/local_position/velocity_body` | `geometry_msgs/msg/TwistStamped` | 订阅 | 50Hz | FRD机体系 | 机体系速度（注意是FRD！） |
| `/mavros/local_position/accel` | `geometry_msgs/msg/AccelWithCovarianceStamped` | 订阅 | 50Hz | FRD机体系 | 加速度（FRD机体系） |
| `/mavros/global_position/global` | `sensor_msgs/msg/NavSatFix` | 订阅 | 5Hz | WGS84 | GPS全局位置 |
| `/mavros/global_position/local` | `nav_msgs/msg/Odometry` | 订阅 | 50Hz | ENU惯性系 | GPS转局部坐标（ENU！） |
| `/mavros/global_position/rel_alt` | `std_msgs/msg/Float64` | 订阅 | 5Hz | - | 相对高度 |
| `/mavros/global_position/compass_hdg` | `std_msgs/msg/Float64` | 订阅 | 5Hz | - | 罗盘航向角 |

> ⚠️ **注意**：`/mavros/local_position/velocity_body` 和 `/mavros/local_position/accel` 输出的是 **FRD（前-右-下）机体系**，不是FLU！使用时必须做坐标系转换。

## 10.3 控制指令话题（发布到MAVROS）

| 话题名称 | 消息类型 | 方向 | 最低频率 | 坐标系 | 说明 |
|----------|----------|------|----------|--------|------|
| `/mavros/setpoint_position/local` | `geometry_msgs/msg/PoseStamped` | 发布 | >2Hz | FLU惯性系 | 位置控制 |
| `/mavros/setpoint_velocity/cmd_vel` | `geometry_msgs/msg/TwistStamped` | 发布 | >2Hz | 惯性系或机体系 | 速度控制 |
| `/mavros/setpoint_velocity/cmd_vel_unstamped` | `geometry_msgs/msg/Twist` | 发布 | >2Hz | 惯性系或机体系 | 速度控制（无时间戳） |
| `/mavros/setpoint_raw/local` | `mavros_msgs/msg/PositionTarget` | 发布 | >2Hz | NED惯性系 | 位置+速度+加速度前馈（本系统主用） |
| `/mavros/setpoint_raw/attitude` | `mavros_msgs/msg/AttitudeTarget` | 发布 | >2Hz | 机体系 | 姿态+油门控制 |
| `/mavros/setpoint_raw/global` | `mavros_msgs/msg/GlobalPositionTarget` | 发布 | >2Hz | WGS84+ENU | GPS航点控制 |
| `/mavros/setpoint_accel/accel` | `geometry_msgs/msg/Vector3Stamped` | 发布 | >2Hz | FRD机体系 | 加速度控制 |

> 🚫 **警告**：OFFBOARD模式下，控制指令发布频率必须 **>2Hz**，否则PX4会自动退出OFFBOARD模式！建议发布频率 20~50Hz。

## 10.4 传感器数据话题

| 话题名称 | 消息类型 | 方向 | 频率 | 说明 |
|----------|----------|------|------|------|
| `/mavros/imu/data` | `sensor_msgs/msg/Imu` | 订阅 | 100Hz | IMU融合数据（FRD机体系） |
| `/mavros/imu/data_raw` | `sensor_msgs/msg/Imu` | 订阅 | 100Hz | IMU未校准原始数据 |
| `/mavros/imu/mag` | `sensor_msgs/msg/MagneticField` | 订阅 | 10Hz | 磁力计数据 |
| `/mavros/imu/static_pressure` | `sensor_msgs/msg/FluidPressure` | 订阅 | 10Hz | 气压计数据 |
| `/mavros/imu/temperature_imu` | `sensor_msgs/msg/Temperature` | 订阅 | 1Hz | IMU温度 |
| `/mavros/altitude` | `mavros_msgs/msg/Altitude` | 订阅 | 10Hz | 多源高度融合 |
| `/mavros/distance_sensor/hrlv_ez4_pub` | `sensor_msgs/msg/Range` | 订阅 | 10Hz | 测距传感器（如定高雷达） |
| `/mavros/rc/in` | `mavros_msgs/msg/RCIn` | 订阅 | 50Hz | 遥控器输入通道 |
| `/mavros/rc/out` | `mavros_msgs/msg/RCOut` | 订阅 | 50Hz | PWM输出到电机/舵机 |

## 10.5 视觉与外部定位话题

| 话题名称 | 消息类型 | 方向 | 最低频率 | 坐标系 | 说明 |
|----------|----------|------|----------|--------|------|
| `/mavros/vision_pose/pose` | `geometry_msgs/msg/PoseStamped` | 发布 | >30Hz | FLU惯性系 | 视觉位姿输入（EKF2融合，本系统主用） |
| `/mavros/vision_pose/pose_cov` | `geometry_msgs/msg/PoseWithCovarianceStamped` | 发布 | >30Hz | FLU惯性系 | 视觉位姿+协方差 |
| `/mavros/odometry/out` | `nav_msgs/msg/Odometry` | 发布 | >30Hz | FLU惯性系 | 外部里程计输入 |
| `/mavros/mocap/pose` | `geometry_msgs/msg/PoseStamped` | 发布 | >100Hz | FLU惯性系 | 动捕系统位姿输入 |

## 10.6 航点与任务话题

| 话题名称 | 消息类型 | 方向 | 频率 | 说明 |
|----------|----------|------|------|------|
| `/mavros/mission/waypoints` | `mavros_msgs/msg/WaypointList` | 订阅 | 事件 | 当前任务航点列表 |
| `/mavros/mission/reached` | `mavros_msgs/msg/WaypointReached` | 订阅 | 事件 | 航点到达通知 |
| `/mavros/home_position/home` | `mavros_msgs/msg/HomePosition` | 订阅 | 事件 | Home点位置 |

## 10.7 参数与时间同步话题

| 话题名称 | 消息类型 | 方向 | 频率 | 说明 |
|----------|----------|------|------|------|
| `/mavros/time_reference` | `sensor_msgs/msg/TimeReference` | 订阅 | 10Hz | 飞控时间参考 |
| `/mavros/timesync_status` | `mavros_msgs/msg/TimesyncStatus` | 订阅 | 1Hz | MAVROS与飞控时间同步状态 |

## 10.8 调试与日志话题

| 话题名称 | 消息类型 | 方向 | 频率 | 说明 |
|----------|----------|------|------|------|
| `/mavros/debug_value/debug` | `mavros_msgs/msg/DebugValue` | 订阅 | 可变 | 调试数值 |
| `/mavros/debug_value/debug_vector` | `mavros_msgs/msg/DebugValue` | 订阅 | 可变 | 调试向量 |
| `/mavros/debug_value/named_value_float` | `mavros_msgs/msg/DebugValue` | 订阅 | 可变 | 命名浮点数 |
| `/mavros/debug_value/named_value_int` | `mavros_msgs/msg/DebugValue` | 订阅 | 可变 | 命名整数 |

## 10.9 MAVROS 服务列表

| 服务名称 | 服务类型 | 说明 |
|----------|----------|------|
| `/mavros/cmd/arming` | `mavros_msgs/srv/CommandBool` | 解锁/上锁 |
| `/mavros/cmd/land` | `mavros_msgs/srv/CommandTOL` | 降落指令 |
| `/mavros/cmd/takeoff` | `mavros_msgs/srv/CommandTOL` | 起飞指令 |
| `/mavros/set_mode` | `mavros_msgs/srv/SetMode` | 切换飞行模式 |
| `/mavros/mission/push` | `mavros_msgs/srv/WaypointPush` | 上传航点任务 |
| `/mavros/mission/pull` | `mavros_msgs/srv/WaypointPull` | 下载航点任务 |
| `/mavros/mission/clear` | `mavros_msgs/srv/WaypointClear` | 清除航点任务 |
| `/mavros/mission/set_current` | `mavros_msgs/srv/WaypointSetCurrent` | 设置当前航点 |
| `/mavros/param/get` | `mavros_msgs/srv/ParamGet` | 获取单个飞控参数 |
| `/mavros/param/set` | `mavros_msgs/srv/ParamSet` | 设置单个飞控参数 |
| `/mavros/param/pull` | `mavros_msgs/srv/ParamPull` | 拉取所有飞控参数 |
| `/mavros/param/push` | `mavros_msgs/srv/ParamPush` | 推送参数到飞控 |

## 10.10 MAVROS 使用注意事项

### 10.10.1 坐标系约定

**MAVROS自动转换规则：**
- 外部ROS2接口使用 **FLU（Forward-Left-Up）**
- PX4内部使用 **FRD（Forward-Right-Down）**
- MAVROS在中间自动转换 FLU ↔ FRD

**需要特别注意的话题（不自动转换或使用其他坐标系）：**

| 话题名称 | 坐标系 | 说明 |
|----------|--------|------|
| `/mavros/setpoint_raw/local` | NED惯性系 | PositionTarget接口，必须手动传NED坐标 |
| `/mavros/local_position/velocity_body` | FRD机体系 | 机体系速度，Y轴向右，Z轴向下 |
| `/mavros/local_position/accel` | FRD机体系 | 机体系加速度 |
| `/mavros/imu/data` | FRD机体系 | IMU数据，角速度和加速度均为FRD |
| `/mavros/global_position/local` | ENU惯性系 | GPS转局部坐标，使用ENU而非FLU |

### 10.10.2 控制频率要求

**OFFBOARD模式硬性要求：**
- 切换到OFFBOARD模式前，必须已经持续发送设定点（至少2Hz）
- 切换后必须持续发送设定点（至少2Hz），否则PX4会自动退出OFFBOARD模式（可能触发悬停或降落）
- 建议发送频率：20~50Hz（本系统yydrone_fsm使用50Hz）

**推荐做法（yydrone_fsm已实现）：**

```cpp
// 发送100帧初始设定点（@50Hz，约2秒）
for (int i = 0; i < 100; i++) {
  publishCurrentPosition();
  rclcpp::sleep_for(20ms);
}
// 然后再切换OFFBOARD并解锁
setOffboardMode();
armDrone(true);
```

### 10.10.3 视觉位姿输入配置

**fast_lio → MAVROS 桥接配置：**

| 项目 | 值 | 说明 |
|------|----|------|
| 输入话题 | `/Odom_high_freq` | fast_lio输出，200Hz，FLU惯性系 |
| 输出话题 | `/mavros/vision_pose/pose` | 降采样到100Hz，FLU惯性系 |
| frame_id | `"map"` | 必须与MAVROS局部坐标系一致 |
| PX4 EKF2_AID_MASK | `24` | 启用视觉位置(bit3) + 视觉偏航(bit4) 融合 |
| PX4 EKF2_EV_DELAY | `0` | 视觉延迟补偿（毫秒，根据实际延迟调整） |

> ⚠️ **注意**：如果视觉位姿输入后飞机出现漂移或抖动，优先检查：①坐标系是否一致（FLU vs NED）；②外参是否正确；③EKF2_EV_DELAY是否需要调整；④视觉位姿频率是否稳定>30Hz。

---

<div align="center">

**— 文档结束 —**

*如需进一步技术支持，请联系项目维护团队。*

</div>
