<div align="center">

<img src="docs/media/readme/banner.gif" alt="ANet Relay：面向智能体集群的地面站。昏暗体素大厅中的一队开放式机架四旋翼" width="100%" />

<h1>ANet Relay</h1>

**面向 Agent 编队的地面站。**<br/>
人、大模型 Agent 与 50 Hz 机载程序一起驾驶小型无人机编队，一个按键即可在它们之间交接控制权。

[![Release](https://img.shields.io/github/v/release/ANetResearch/ANet-Relay?label=release&color=1f1f1f)](https://github.com/ANetResearch/ANet-Relay/releases/latest)
[![CI](https://github.com/ANetResearch/ANet-Relay/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ANetResearch/ANet-Relay/actions/workflows/ci.yml)
[![Platforms](https://img.shields.io/badge/macOS%20·%20Linux%20·%20Windows-1f1f1f)](https://github.com/ANetResearch/ANet-Relay/releases/latest)
[![Agent API](https://img.shields.io/badge/agents-REST%20·%20WebSocket%20·%20MCP-e0322d)](docs/agent-api.zh-CN.md)
[![License](https://img.shields.io/badge/license-modified%20Apache--2.0-1f1f1f)](LICENSE)

**[下载](https://github.com/ANetResearch/ANet-Relay/releases/latest)** · **[Wiki](https://github.com/ANetResearch/ANet-Relay/wiki/Home-zh-CN)** · [Agent API](docs/agent-api.zh-CN.md) · [机载程序](docs/programs.zh-CN.md) · [更新日志](CHANGELOG.md) · [English](README.md)

</div>

<br/>

<img src="docs/media/readme/fly-dogfight.gif" alt="六机狗斗中的飞行舞台：红色体素曳光弹、命中与体素爆炸，以及排行榜" width="100%" />

<sub>六机狗斗中的飞行舞台：四架开火手、两架撞击手，全部只用摇杆级控制。由应用渲染器播放内置演示场景（<code>?fixture=dogfight</code>）逐帧录制。</sub>

## 为什么是 Relay

大模型以秒为单位思考，四旋翼却需要每秒修正摇杆五十次。Relay 像一个团队那样分工：

- **人**随时用手柄或键盘接管任意一架飞机，并且始终优先。
- **Agent**（你自己的代码、MCP 客户端或内置 Copilot）认领飞机，决定做什么。
- **机载程序**是由这些 Agent 编写和调参的小型 Python 状态机，负责 50 Hz 的飞行回路。

人松手后，控制权交回原来持有它的 Agent。对 API 而言，真实的 PX4、ArduPilot 飞机与内置仿真机完全一致。

## 亮点

<table>
<tr>
<td width="50%"><img src="docs/media/readme/relay.gif" alt="体素光流从手柄连向多架无人机，红色光流从一架移到另一架" width="100%" /></td>
<td width="50%"><h3>接管与交还</h3>按 <code>P</code> 从 Agent 手中接管任意飞机；松手后 Agent 收到 <code>relay_handback</code>。时间线记录每架飞机由谁驾驶。最多四个手柄或遥控器同时驾驶四架飞机。</td>
</tr>
<tr>
<td><h3>一句话开狗斗</h3>对 Copilot 说<i>“开一场 6 机混战，4 架开火、2 架撞击”</i>。Relay 生成仿真机、部署程序并开战：红色体素曳光弹、体素爆炸，高速撞击双方同时坠毁。</td>
<td><img src="docs/media/readme/dogfight.gif" alt="体素竞技场中四旋翼互射红色曳光弹，其中一架炸成体素" width="100%" /></td>
</tr>
<tr>
<td><img src="docs/media/readme/physics.gif" alt="碰撞球内的四旋翼，带旋翼推力箭头与反扭矩弧线" width="100%" /></td>
<td><h3>参照 Gazebo 的旋翼物理</h3>六自由度刚体，四个旋翼参照 Gazebo <code>MulticopterMotorModel</code> 建模，串级飞控，250 Hz 下的碰撞体积与实体圆环。结果可复现，评测时比实时快 13 倍。</td>
</tr>
<tr>
<td><h3>Agent 编写大脑</h3>在默认的<b>仅摇杆</b>级别下，Agent 不能说“向前飞 1 米”。它要编写程序、做单元测试、在隐藏机体上通过评测挑战、在 Elo 天梯上与其他程序对决，并在飞行中调参。</td>
<td><img src="docs/media/readme/programs.jpg" alt="悬停在体素状态机上方的四旋翼" width="100%" /></td>
</tr>
<tr>
<td><img src="docs/media/readme/lan.jpg" alt="三台笔记本共享同一个全息体素竞技场" width="100%" /></td>
<td><h3>局域网竞技场与排行榜</h3>同一网络中的每台 Relay 都能看到其他终端的仿真机。Agent 跨机器追逐与狗斗；共享排行榜按击落、存活、命中率与撞击排名。碰撞由双方核验，真实飞机从不参与。</td>
</tr>
</table>

## 快速开始

1. [下载](https://github.com/ANetResearch/ANet-Relay/releases/latest)并打开应用（macOS 版本已签名并公证）。
2. 点击 **添加模拟机**，按 **P** 接管，在 **控制** 页解锁，用 `W A S D` / `R F` / `Q E` 或手柄飞行。
3. 打开 **Copilot**（`Ctrl+J` / `⌘J`），填入任意兼容 OpenAI 或 Anthropic 的接口，然后让它开一场狗斗。

### 接入你自己的 Agent

```python
from anet_relay_client import RelayClient           # sdk/python，仅依赖标准库

with RelayClient() as relay:                          # http://127.0.0.1:8765
    relay.open_session("my-agent")
    pid = relay.program_create("My hover", copy_of="hover_trim")["program"]["id"]
    print(relay.challenge_submit("hover", pid)["result"])        # 在隐藏种子上无头评测
    relay.dogfight_start(aircraft=6, roles={"gunner": 4, "rammer": 2})
    print(relay.leaderboard("downs")["rows"][:3])
```

```sh
claude mcp add anet-relay-app -- python3 /path/to/ANet-Relay/mcp/anet_relay_app_mcp.py --name claude-code-app   # 或任意 MCP 客户端
```

三个 MCP 服务器分别覆盖飞行（20 个工具）、直到单个电机的设备层（36 个）和整个应用（65 个）。详见 [MCP Servers](https://github.com/ANetResearch/ANet-Relay/wiki/MCP-Servers)。

## 包含什么

| | |
|---|---|
| **机队链路** | UDP 14550 上的 MAVLink 2，子网扫描，在线 / 降级 / 丢失状态，完整遥测、参数与原始消息检查器 |
| **控制** | 接管、认领与会话，50 Hz 摇杆，技能与序列（Full 级别），可覆盖一切的急停与全部急停 |
| **仿真** | 最多 8 架，ArduPilot 或 PX4 风格自驾仪，悬停辅助，带种子的机体缺陷（重心偏移、电机不均、桨叶损伤） |
| **程序与 Lab** | 50 Hz 受限 Python、单元测试、10 个评测挑战、Elo 对决，以及包含每发子弹与爆炸的轨迹回放 |
| **竞技场** | 虚拟子弹、碰撞、比赛、狗斗与跨局域网排行榜 |
| **真实飞机** | ESP8266 桥接后的 PX4 或 ArduPilot，带确认词的飞前检查、台架流程、飞行健康诊断、FPV 画中画 |
| **记录** | 每次飞行存入本地 SQLite：曲线、事件与控制轨道、三维回放、CSV 与 JSON |
| **SITL** | PX4 + Gazebo 或 ArduPilot SITL 作为普通 MAVLink 飞机接入（[说明](docs/simulation.zh-CN.md)） |

## 下载

| 平台 | 文件 |
|---|---|
| macOS，Apple 芯片 / Intel | `ANet-Relay-<version>-mac-arm64.dmg` / `-mac-x64.dmg`（已签名、已公证） |
| Linux x64 | `ANet-Relay-<version>-linux-x64.AppImage` 或 `.tar.gz` |
| Windows x64 | `ANet-Relay-<version>-win-x64.exe`（暂未代码签名） |

应用自带后端。请用 `SHA256SUMS.txt` 校验下载。

## 文档

**[Wiki](https://github.com/ANetResearch/ANet-Relay/wiki/Home-zh-CN)** 提供使用指南：安装、操控、真机、物理、碰撞、竞技场、狗斗、程序、Lab、Agent、MCP、Copilot、SITL、排障与常见问题。随代码版本化的参考文档：[Agent API](docs/agent-api.zh-CN.md) · [机载程序](docs/programs.zh-CN.md) · [仿真](docs/simulation.zh-CN.md) · [硬件](docs/hardware.zh-CN.md) · [飞行调参](docs/flight-tuning.zh-CN.md) · [设计文档](docs/design/)。

## 安全

Relay 驾驶真实飞机。请在防护网内、装好桨叶保护罩飞行，窗口前始终有操作员，并在拆下桨叶时核对摇杆方向。接管、**急停** 与 **全部急停** 可覆盖任何 Agent 与程序。API 只监听回环地址，局域网收到的任何内容都不会进入控制路径。

## 从源码构建

```sh
git clone https://github.com/ANetResearch/ANet-Relay.git && cd ANet-Relay
python3.12 -m venv .venv && .venv/bin/pip install -e "./backend[dev]"
npm ci && npm ci --prefix web
npm run dev        # 后端、Vite 与 Electron
npm test           # 合并门槛
```

欢迎用中文或英文提交 Issue 与 Pull Request。涉及控制模型、Agent API 或停止语义的改动，请先开 Issue。

## 许可

**ANet 开源许可证**，基于 Apache License 2.0 修改；须保留 ANet 标志与版权声明（[LICENSE](LICENSE)）。三维飞机模型为 Caddx GoFilm20 机架，仅用于可视化。基于 [Electron](https://www.electronjs.org)、[pymavlink](https://github.com/ArduPilot/pymavlink)、[FastAPI](https://fastapi.tiangolo.com)、[three.js](https://threejs.org)、[React Three Fiber](https://github.com/pmndrs/react-three-fiber)、[pi](https://github.com/earendil-works/pi)、[ArduPilot](https://ardupilot.org)、[PX4](https://px4.io) 与 [mavesp8266](https://github.com/dogmaphobic/mavesp8266)。与 [ANet](https://github.com/ANetResearch/ANet)、[ANet4D](https://github.com/ANetResearch/ANet4D) 同属 ANet 生态。

<div align="center">
<br/>
<img src="docs/media/anet-avatar.png" alt="Agent Network Research" width="48" />
<br/>
<sub>Agent Network Research</sub>
</div>
