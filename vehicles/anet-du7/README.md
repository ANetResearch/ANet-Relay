# ANetDU7

ANetDU7（汤圆 U7）是 ANet Relay 的主力机型：室内自主飞行四旋翼，PX4 飞控，机载 ROS 2 定位、建图、避障，
可挂 EO/IR 云台吊舱。本目录是这架飞机的全部资料：机载代码、接口文档、真机测试脚本，以及它和 Relay 的接法。

| 项目 | 参数 |
|---|---|
| 构型 | 四旋翼 X 型，轴距 500 mm，含保护架 684 × 684 × 211 mm |
| 重量 | 2.50 kg（含电池、保护框） |
| 电池 / 续航 | 4S 5300 mAh 45C，约 10 min |
| 飞控 | CUAV X7+（STM32H743，ICM-42688-P + 2 × ICM-20689），PX4 稳定版 |
| 机载电脑 | Jetson Orin Nano Super（8 GB，67 TOPS），Ubuntu 22.04，ROS 2 Humble，MAVROS |
| 传感器 | Livox MID360S 激光雷达（标配），深度相机、光流测距（选配） |
| 遥控 | 2.4 GHz ELRS，12 通道 |
| 吊舱 | 上博智像 DYT 协议的 EO/IR 吊舱：可见光变焦 + 广角、红外、激光测距（选配） |

## 目录

| 路径 | 内容 |
|---|---|
| `onboard/new_u7_src/` | 机载 ROS 2 工作空间，当前版本（`auto-lcx/yix_drone_ws` 的 `new_u7_src` 分支，含提交历史）：FAST-LIO2、Livox 驱动、EGO-Planner、`yydrone_fsm` 状态机、`yydrone_localization`、`yydrone_description`（URDF、`yix.stl` 机体网格） |
| `onboard/main/` | 同一仓库的 `main` 分支（含提交历史）：早期工作空间，另有 `yydrone_safety`、`yydrone_mission`、`yydrone_aruco`、`yydrone_control`、Qt 地面端 `src/ui`、Orbbec 相机驱动、FASTLIO2_ROS2（PGO / 重定位 / HBA）、建好的地图 `src/maps`、开机自启 `drone-stack.service` + `start_drone.sh` |
| `tests/u7_mavros_simple.py` | 真机本地测试节点（MAVROS，机体 FLU 速度控制）：起飞 2 m，前后左右各 1 m，降落 |
| `tests/u7_mavros_square10.py` | 同一节点的室外版本，真机飞过：起飞 10 m，10 m 见方航线（前、左、后、右），降落；水平 2.5 m/s，垂直 0.5 m/s |
| `docs/onboard-interfaces.zh-CN.md` | 机载系统接口文档 v1.2：话题、服务、坐标系、MAVROS 注意事项 |
| `docs/gimbal-dyt.zh-CN.md` | 吊舱控制协议要点和 Relay 的实现对照 |
| `docs/vendor/` | 合作厂商原始资料：上博智像 DYT 吊舱控制协议与接口定义、汤圆 U7 整机接口文档与海报（版权归厂商） |

两个分支都是原样并入（`git subtree`，历史完整）。`main` 里也包含一些不属于源码的文件（Python 虚拟环境
`src/ui/scripts/drone_venv`、SDK 日志、预编译的 `.so`），为了不丢东西没有删。`onboard/main/Livox-SDK2` 是子模块，
指向官方仓库的同一提交。机载仓库里没有飞控固件：飞机出厂使用 PX4 官方稳定版，参数在飞控上。

许可证：机载代码中的第三方部分沿用各自的许可证（FAST-LIO 为 GPL-2.0，EGO-Planner 为 GPL-3.0，Livox 驱动为 MIT，
Sophus 为 MIT，Orbbec SDK 见其目录）；原仓库自己的包没有声明许可证。它们与 Relay 本体相互独立，不参与 Relay 的构建和发行包。

## 接到 Relay

Relay 直接说 MAVLink，不需要在机上装任何东西。

1. 机载 MAVROS 把 MAVLink 转发给 Relay 所在的电脑。`onboard/main/start_drone.sh` 里已经有这一行，把 IP 换成 Relay 主机的：

   ```bash
   ros2 launch mavros px4.launch \
       fcu_url:=/dev/ttyACM0:921600 \
       gcs_url:=udp://:14550@<Relay 主机 IP>:14550
   ```

2. Relay 监听 UDP 14550，飞机出现在机队里，机型显示为 ANetDU7（设置 `real_model_default`，可在检视器里逐机修改）。
3. 多机：每架飞机的 PX4 参数 `MAV_SYS_ID` 必须不同；各机的 MAVROS 都指向同一台 Relay。
4. 吊舱：吊舱网口接入机载网络后，在检视器“云台”里填它的 IP（TCP 2000）挂接；或用串口（115200 8N1）。
5. 机上的定位（FAST-LIO → `/mavros/vision_pose/pose` → EKF2）照常运行。Relay 需要飞控有本地位置估计才能飞
   `takeoff` / `move` / `goto`。

机上同时运行 `yydrone_fsm` 或 `u7_mavros_simple.py` 时，它们也会发 OFFBOARD 设定点；同一时间只让一方控制。

## 坐标体系

Relay 的控制接口默认 FLU，与机载代码一致：

| | 机体系 x / y / z | 本地系 x / y / z | 航向 | 偏航角速度正方向 |
|---|---|---|---|---|
| `flu`（默认） | 前 / 左 / 上 | 东 / 北 / 上（即 MAVROS `local_position`） | 0° 沿 +x，逆时针为正 | 逆时针 |
| `frd` | 前 / 右 / 下 | 北 / 东 / 下（PX4 内部） | 0° 朝北，顺时针为正 | 顺时针 |

设置里切换 `control_frame`，或在单个请求里带 `frame`。`vehicle.nav` 按当前体系给出位置、速度、机体速度和航向。

## Relay 怎样飞这架飞机

`u7_mavros_simple.py` 的飞法是 Relay 飞 ANetDU7 的默认方式（技能 `takeoff` / `move` / `goto`）：

| 测试节点 | Relay |
|---|---|
| POSCTL 下预发 2 s 零速度设定点 → 解锁 → 切 OFFBOARD | 相同顺序 |
| `setpoint_raw/local`，`FRAME_BODY_NED`，`type_mask` 只用速度和偏航角速度 | `SET_POSITION_TARGET_LOCAL_NED`，`MAV_FRAME_BODY_NED`，`type_mask` 0x05C7（MAVROS 发出的同一帧） |
| 水平速度 = min(0.8 × 距离, 限速, √(2 × 0.5 × 距离))，后一项是刹车曲线；垂直速度 = 0.8 × 高度误差，限 0.5 m/s；加速度限 0.5 m/s²，20 Hz，偏航角速度 0 | 相同（水平限速即 `speed`，缺省 0.5 m/s） |
| 水平、高度误差首次都进入 0.20 m 后计时 2 s 算到位；一直进不去则到“到达时限”后同样计时 2 s 并进入下一步 | 相同（`settle_s` 可改）；到达时限 = 按限速飞完这一段的时间 + 8 s，这样结束的步骤 `detail` 里写明离目标多远 |
| 倾角 > 20°、位置跳变 > 0.25 m + 1.5 m/s·dt、遥测过期 → 请求 AUTO.LAND 一次 | 相同；遥测过期阈值 1.0 s（Relay 经无线链路看到的位置比机上晚） |
| 模式被遥控器改走 → 放手，不再发指令 | 相同 |
| 航线：起飞 2 m，前 1 m 回，左 1 m 回，降落 | 内置计划 `du7_box` |
| 室外航线（`u7_mavros_square10.py`）：起飞 10 m，前、左、后、右各 10 m，降落；水平 2.5 m/s | 内置计划 `du7_square10` |
| 室外版的包线：离起飞点水平 30 m、高度 12 m；水平速度 > 3.5 m/s 或垂直速度 > 1.0 m/s 即停 | 安全区（设置 `safety_volume`，飞 `du7_square10` 前设为 30 × 30 × 12 m；时间轴里打开这个计划会提示并可一键调大，或用“不限安全区执行”）；速度超过限速 0.5 m/s 持续 1 s 请求降落 |

与测试节点不同的地方：Relay 在地面电脑上，经 Wi-Fi 控制；链路中断时由 PX4 的 OFFBOARD 丢失保护
（`COM_OBL_RC_ACT`、`COM_OF_LOSS_T`）接管，首飞前先确认这两个参数和遥控器接管开关。

```bash
# 模拟机上先飞一遍方框
curl -X POST localhost:8765/sims                                        # 默认就是 ANetDU7
curl -X POST localhost:8765/vehicles/sim-1/plan -H 'content-type: application/json' -d '{"builtin":"du7_box"}'
# 真机：同一个请求，加 "confirm":"ARM"
```
