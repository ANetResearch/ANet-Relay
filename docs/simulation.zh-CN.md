# 模拟

[English](simulation.md)

ANet Relay 自带多旋翼模拟器。本文说明它的定位、与业内常用无人机模拟软件的对比，以及如何改用 PX4 或 ArduPilot 软件在环（SITL）模拟并通过 Relay 飞行。

## 1. Relay 模拟器

| 特性 | 含义 |
|---|---|
| 桌面应用自带 | 机群栏 **Add sim**（或 `POST /sims`）即可在同一进程中启动模拟机；无需游戏引擎、GPU 或额外安装。0.6.0 起，除非选择小机预设（见下），新模拟机就是 ANetDU7 |
| 与真机一样使用 MAVLink | 模拟 ArduPilot 风格或 PX4 风格的飞控（飞行模式、解锁检查、失效保护、参数、着陆检测），经过与真机相同的驱动、杆量通道、技能与起飞前检查 |
| 摇杆级控制 | 智能体与机载程序以 50 Hz 的横滚、俯仰、偏航与油门飞行（06 §5）；可关闭悬停辅助，进行物理飞行 |
| 确定性 | 同一场景、种子与程序版本总是得到同一次飞行；机体缺陷（重心、水平误差、电机效率、桨叶损伤、噪声）由种子决定 |
| 无头运行、快于实时 | 评测器在没有套接字与窗口的情况下步进同一模拟器，单机约为实时的 13 倍、四机约 3.5–4 倍，用于题目、对抗及其回放 |
| 对抗场 | 虚拟子弹、碰撞、对局、狗斗与排行榜，可跨局域网终端；仅限模拟机 |

它不能替代高保真模拟器：没有相机或激光雷达的传感器图像（云台吊舱的画中画是对舞台场景的渲染，不是相机模型）；空气动力学是包含电机动态、阻力、地面效应与阵风的紧凑刚体模型；飞控是对飞控行为的建模，而不是固件本身。要测试真实固件，请启动固件孪生（0.5.4）：Relay 运行随安装包分发的官方 ArduCopter 或 PX4 SITL 固件，加载真机的版本与参数，在自己的物理模型中飞行，孪生与其他模拟机的用法相同（[智能体接口：固件孪生](agent-api.zh-CN.md#固件孪生054)、[飞控运行包（英文）](autopilot-packs.md)）。经 USB 连接的 PX4 飞机还可以进行硬件在环。第 3 节说明如何接入自行运行的 SITL（例如配合 Gazebo）；研究视觉或空气动力学，请使用下表中的模拟器。

### 机型：ANetDU7 与小机预设（0.6.0）

模拟机的物理由机型预设决定（`sim.frame`：质量、轴距、桨径、电池、推重比，以及由它们得到的电机与阻力模型）。新建模拟机取设置 `sim_frame_default`，缺省 `anet-du7`；请求里的 `frame.preset` 可指定别的预设（**Add sim** 对话框提供同样的选择），预设中的任何数值都可以单独覆盖。

| 预设 | 机型 | 质量 | 轴距 | 桨径 | 电池 | 推重比 | 缺省飞控风格 |
|---|---|---|---|---|---|---|---|
| `anet-du7`（缺省） | ANetDU7：CUAV X7+ 四旋翼，带保护架，684 × 684 × 211 mm | 2.5 kg | 500 mm | 10 寸 | 4S 5300 mAh | 2.0 | PX4，带模拟云台吊舱 |
| `micro-120` | 0.5 的小机（舞台上是 GoFilm 20 模型） | 0.12 kg | 250 mm | 5.1 寸 | 3S 850 mAh | 2.4 | ArduPilot |
| `small-250` | 250 mm 级，5 寸桨 | 0.42 kg | 250 mm | 5 寸 | 3S 850 mAh | 3.5 | ArduPilot |
| `large-450` | 450 mm 级，10 寸桨 | 1.5 kg | 450 mm | 10 寸 | 4S 5000 mAh | 2.2 | ArduPilot |

`anet-du7` 预设的质量、轴距与电池取自机型技术参数，保护架使机体阻力增大。转动惯量与推力曲线取同级别机型的经验值，并非对某架真机的辨识；用真机参数启动的固件孪生会把悬停油门对齐。飞机的 `model` 在该预设下为 `anet-du7`，其他预设为 `micro`，它决定舞台按真实尺寸绘制的模型以及飞行缺省行为（ANetDU7 的起飞、移动与 goto 以 0.5 m/s 限速的机体系速度飞行，见[智能体接口](agent-api.zh-CN.md#anetdu7-与限速飞行060)）。狗斗、评测题目与内置程序始终使用 `micro-120`；固件孪生使用档案中的机型，没有则为 `micro-120`。

**云台吊舱。** `anet-du7` 模拟机自带模拟吊舱（`vehicle.gimbal`，`kind: "sim"`），动作与真机 DYT 吊舱相同（[智能体接口：云台吊舱](agent-api.zh-CN.md#云台吊舱060)）。它与物理同一时钟，因而结果可复现：每个轴以一阶滞后响应角速度指令（时间常数 0.08 s，最大 100°/s），俯仰限制在 −120°…+90°，方位在 ±180° 内连续；`follow` 使方位随机头，`lock` 保持地理指向；变焦以每秒 2 倍的速度趋近目标倍数，每个镜头各记一个倍数；激光测距把视线与地面（z = 0）以及世界里的环和障碍求交，最远 1200 m；`track` 锁定画面某个像素背后的地面点，之后保持指向它；拍照与录像连同飞机位姿一起记录。舞台绘制 ANetDU7 模型，并让吊舱随 `vehicle.gimbal` 转动；画中画由舞台场景按当前镜头的视场角渲染（仅模拟吊舱；0.6.0 不解码真吊舱的视频流）。`micro` 模拟机没有吊舱，吊舱动作返回 `gimbal_missing`。

## 2. 业内模拟软件

| 模拟器 | 基础 | 典型用途 | 说明 |
|---|---|---|---|
| Gazebo（Harmonic、Ionic、Jetty） | Open Robotics；ODE / DART 物理，Ogre 渲染 | PX4 与 ArduPilot SITL、ROS 2 机器人、传感器（相机、激光雷达、IMU） | 现行 Gazebo（原 Ignition）。Harmonic 是多数飞控教程采用的长期支持版本；Gazebo Classic（版本 11）已于 2025 年 1 月停止维护 |
| PX4 SITL | 为主机编译的 PX4 固件 | 配合 Gazebo、jMAVSim 或无渲染方式进行固件开发与测试 | `make px4_sitl gz_x500` 启动 PX4 与 Gazebo；jMAVSim 是较早的轻量 Java 模拟器 |
| ArduPilot SITL | 为主机编译的 ArduPilot 固件 | 固件与任务测试；内置物理模型，或以 Gazebo、JSBSim（固定翼）、X-Plane、FlightGear、RealFlight 作为物理后端 | 使用 `sim_vehicle.py` 与 MAVProxy |
| Microsoft AirSim | Unreal Engine 插件 | 照片级感知研究 | 微软已归档；由 Project AirSim 与社区分支 Colosseum 延续 |
| NVIDIA Isaac Sim + Pegasus Simulator | Omniverse、PhysX、RTX 渲染 | 在 Isaac Sim 中配合 PX4 进行多旋翼模拟；合成数据、机器人 | 需要 RTX 显卡 |
| Aerial Gym | NVIDIA Isaac Gym / GPU 物理 | 空中机器人强化学习，数千个并行环境 | 研究框架（NTNU） |
| Flightmare、Agilicious | Unity 渲染、快速动力学；开源软硬件栈 | 敏捷飞行与无人机竞速研究 | 苏黎世大学 |
| RotorS | Gazebo Classic、ROS 1 | 多旋翼研究模型 | 随 Gazebo Classic 停止维护而不再更新 |
| gym-pybullet-drones | PyBullet | Crazyflie 级四旋翼的强化学习与控制 | 轻量，纯 Python |
| MuJoCo | Google DeepMind 物理引擎 | 控制与学习；MuJoCo Menagerie 中有四旋翼模型（Skydio X2、Crazyflie 2） | 速度快、接触精确 |
| Genesis | GPU 物理平台 | 大规模学习，含无人机示例 | 较新（2024 年） |
| Webots | Cyberbotics，开源 | 教学与机器人，DJI Mavic 2 Pro 模型，支持 ArduPilot SITL | 跨平台 |
| CoppeliaSim | Coppelia Robotics（原 V-REP） | 机器人教学，含四旋翼模型 | 场景可脚本化 |
| Liftoff、VelociDrone、DRL Simulator | 商业 FPV 模拟器 | 通过 USB 连接真实遥控器进行飞手训练 | 不能通过 MAVLink 编程 |
| RealFlight | 商业遥控模拟器（Horizon Hobby） | 遥控飞手训练；ArduPilot SITL 的物理后端（FlightAxis） | Windows |

选择建议：飞手手感训练用 FPV 模拟器；固件与任务测试用 PX4 或 ArduPilot SITL 配合 Gazebo；感知研究用 Isaac Sim 或 AirSim 的后续项目；大规模策略训练用 Aerial Gym、gym-pybullet-drones、MuJoCo 或 Genesis；编写、评测与对抗摇杆级程序和智能体用 Relay。

## 3. 将 PX4 或 ArduPilot SITL 接入 Relay

Relay 在 UDP 14550（设置 `listen_port`）上接收 MAVLink 2，把每个发送心跳的源地址视为一架飞机。因此 SITL 实例对 Relay 而言就是一架普通的 MAVLink 飞机：无需修改代码，也无需插件。先了解以下几点：

- 除非以 `RELAY_ALLOW_LOOPBACK=1` 启动，Relay 会忽略回环地址。SITL 与 Relay 在同一台计算机上时，请带上该变量启动桌面应用或后端（例如 `RELAY_ALLOW_LOOPBACK=1 .venv/bin/python -m relay --dev`）。
- Relay 按源地址区分飞机：要飞多架，每个地址运行一个 SITL 实例（另一台计算机、虚拟机或拥有独立地址的容器）。
- SITL 飞机对 Relay 而言是**真机**（`real-<地址>`），不是 Relay 模拟机：解锁需要 `confirm: "ARM"`，适用起飞前检查，且从不加入对抗场、对局或狗斗。
- 杆量通道与真机一样使用 MAVLink 手动控制与 RC 覆盖；起飞前检查会列出阻止它的参数（ArduPilot `SYSID_MYGCS` / `MAV_GCS_SYSID` 与 `RC_OVERRIDE_TIME`，PX4 `COM_RC_IN_MODE` 为 1 至 3）。

### PX4 SITL 配合 Gazebo

1. 按 PX4 指南安装工具链与 Gazebo Harmonic（Ubuntu 22.04 或 24.04），然后编译并启动：`cd PX4-Autopilot && make px4_sitl gz_x500`。
2. PX4 把地面站链路发往本机 UDP 14550。Relay 在同一台计算机上并设置了 `RELAY_ALLOW_LOOPBACK=1` 时，飞机会在几秒内出现在机群中。
3. Relay 在另一台计算机上时，在 PX4 命令行（`pxh>`）中增加一条链路：`mavlink start -u 14560 -o 14550 -t <relay-ip> -r 400000`，或用 mavlink-router 转发 UDP 14550。
4. 若起飞前检查提示不接受 MAVLink 输入：`param set COM_RC_IN_MODE 1`。

### ArduPilot SITL

1. 配置 ArduPilot 编译环境（`Tools/environment_install/install-prereqs-ubuntu.sh`），然后启动多旋翼：`cd ardupilot && Tools/autotest/sim_vehicle.py -v ArduCopter --console --map`。
2. MAVProxy 把飞机转发到 UDP 127.0.0.1:14550；Relay 在同一台计算机上并设置了 `RELAY_ALLOW_LOOPBACK=1` 时，飞机会出现在机群中。
3. Relay 在另一台计算机上时，增加一个输出：`sim_vehicle.py -v ArduCopter --out=udp:<relay-ip>:14550`。
4. 以 Gazebo 作为物理后端时，安装 `ardupilot_gazebo` 插件，启动 `gz sim -r iris_runway.sdf`，再运行 `sim_vehicle.py -v ArduCopter -f gazebo-iris --model JSON`；Relay 的接入方式相同。

之后与任何飞机一样飞行：认领、确认后解锁、用技能或摇杆起飞并降落。机载程序、题目与对抗场请使用 Relay 自带的模拟机。
