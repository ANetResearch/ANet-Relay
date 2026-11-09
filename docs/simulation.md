# Simulation

[中文](simulation.zh-CN.md)

ANet Relay ships its own multirotor simulator. This page explains what it is for, how it compares with the
simulators commonly used for drones, and how to fly a PX4 or ArduPilot software-in-the-loop (SITL) simulation
through Relay instead.

## 1. The Relay simulator

| Property | What it means |
|---|---|
| Built into the desktop app | **Add sim** in the fleet bar (or `POST /sims`) starts a simulated aircraft in the same process; no game engine, GPU or extra installation |
| Speaks MAVLink like a real aircraft | A simulated ArduPilot-style or PX4-style autopilot (modes, arming checks, failsafes, parameters, landing detection) behind the same driver, stick path, skills and preflight as a real aircraft |
| Stick-level control | Agents and onboard programs fly with roll, pitch, yaw and throttle at 50 Hz (06 §5); hover assist can be switched off for physical flight |
| Deterministic | The same scenario, seed and program revision always give the same flight; airframe defects (centre of gravity, level error, motor efficiency, propeller damage, noise) are seeded |
| Headless and faster than real time | The judge's evaluator steps the same simulator without sockets or a window, about 13x real time with one aircraft and 3.5–4x with four, for challenges, duels and their replays |
| Arena | Virtual rounds, collisions, matches, dogfights and a leaderboard, also across terminals on the LAN; simulators only |

It is not a replacement for a high-fidelity simulator: there are no camera or lidar images, the aerodynamics are a
compact rigid-body model with motor dynamics, drag, ground effect and gusts, and the flight-control firmware is a
model of the autopilot, not the firmware itself. To test the real firmware, start a firmware twin (0.5.4): Relay
runs the official ArduCopter or PX4 SITL build that ships with it, with a real aircraft's version and parameters,
against its own physics, and the twin flies like any simulator ([Agent API, Firmware twins](agent-api.md#firmware-twins-054),
[runtime packs](autopilot-packs.md)). PX4 aircraft connected over USB can also run hardware in the loop. Section 3
connects a SITL you run yourself, for example with Gazebo; to study vision or aerodynamics, use one of the
simulators below.

## 2. Simulators in the field

| Simulator | Basis | Typical use | Notes |
|---|---|---|---|
| Gazebo (Harmonic, Ionic, Jetty) | Open Robotics; ODE / DART physics, Ogre rendering | PX4 and ArduPilot SITL, ROS 2 robotics, sensors (cameras, lidar, IMU) | The current Gazebo (formerly Ignition). Harmonic is the long-term release most autopilot guides use; Gazebo Classic (version 11) reached end of life in January 2025 |
| PX4 SITL | PX4 firmware built for the host | Firmware development and testing with Gazebo, jMAVSim or no rendering | `make px4_sitl gz_x500` starts PX4 with Gazebo; jMAVSim is the older lightweight Java simulator |
| ArduPilot SITL | ArduPilot firmware built for the host | Firmware and mission testing; built-in physics models, or Gazebo, JSBSim (fixed wing), X-Plane, FlightGear and RealFlight as the physics backend | `sim_vehicle.py` with MAVProxy |
| Microsoft AirSim | Unreal Engine plugin | Photorealistic perception research | Archived by Microsoft; continued as Project AirSim and by the community fork Colosseum |
| NVIDIA Isaac Sim + Pegasus Simulator | Omniverse, PhysX, RTX rendering | Multirotor simulation with PX4 in Isaac Sim; synthetic data, robotics | Needs an RTX GPU |
| Aerial Gym | NVIDIA Isaac Gym / GPU physics | Reinforcement learning for aerial robots, thousands of parallel environments | Research framework (NTNU) |
| Flightmare, Agilicious | Unity rendering, fast dynamics; open hardware and software stack | Agile flight and drone racing research | University of Zurich |
| RotorS | Gazebo Classic, ROS 1 | Multirotor research models | No longer maintained with Gazebo Classic at end of life |
| gym-pybullet-drones | PyBullet | Reinforcement learning and control of Crazyflie-class quadrotors | Lightweight, Python only |
| MuJoCo | Google DeepMind physics engine | Control and learning; quadrotor models in MuJoCo Menagerie (Skydio X2, Crazyflie 2) | Fast, accurate contacts |
| Genesis | GPU physics platform | Large-scale learning, drone examples | Recent (2024) |
| Webots | Cyberbotics, open source | Education and robotics, DJI Mavic 2 Pro model, ArduPilot SITL support | Cross-platform |
| CoppeliaSim | Coppelia Robotics (formerly V-REP) | Robotics education, a quadcopter model | Scriptable scenes |
| Liftoff, VelociDrone, DRL Simulator | Commercial FPV simulators | Pilot training with a real radio over USB | Not programmable through MAVLink |
| RealFlight | Commercial RC simulator (Horizon Hobby) | RC pilot training; physics backend for ArduPilot SITL (FlightAxis) | Windows |

Choosing: train stick skills in an FPV simulator; test firmware and missions in PX4 or ArduPilot SITL with Gazebo;
research perception in Isaac Sim or an AirSim descendant; train policies at scale in Aerial Gym,
gym-pybullet-drones, MuJoCo or Genesis; write, judge and duel stick-level programs and agents in Relay.

## 3. Connecting PX4 or ArduPilot SITL to Relay

Relay listens for MAVLink 2 on UDP 14550 (setting `listen_port`) and treats every source address that sends a
heartbeat as an aircraft. A SITL instance is therefore an ordinary MAVLink aircraft to Relay: no code changes and no
plug-in. Points to know first:

- Relay ignores the loopback address unless it starts with `RELAY_ALLOW_LOOPBACK=1`. For SITL on the same computer,
  start the desktop app or the backend with that variable (for example
  `RELAY_ALLOW_LOOPBACK=1 .venv/bin/python -m relay --dev`).
- Relay tells aircraft apart by source address: run one SITL instance per address (another computer, a virtual
  machine or a container with its own address) to fly several.
- A SITL aircraft is a **real aircraft** to Relay (`real-<address>`), not a Relay simulator: arming needs
  `confirm: "ARM"`, the preflight list applies, and it never joins the arena, matches or dogfights.
- The stick path uses MAVLink manual control and RC override as with a real aircraft; the preflight list shows any
  parameter that blocks it (ArduPilot `SYSID_MYGCS` / `MAV_GCS_SYSID` and `RC_OVERRIDE_TIME`, PX4
  `COM_RC_IN_MODE` 1 to 3).

### PX4 SITL with Gazebo

1. Install the PX4 toolchain and Gazebo Harmonic as the PX4 guide describes (Ubuntu 22.04 or 24.04), then build and
   start: `cd PX4-Autopilot && make px4_sitl gz_x500`.
2. PX4 sends its ground-station link to UDP 14550 on the same computer. With Relay on that computer and
   `RELAY_ALLOW_LOOPBACK=1`, the aircraft appears in the fleet within a few seconds.
3. With Relay on another computer, add a link to it in the PX4 shell (`pxh>`):
   `mavlink start -u 14560 -o 14550 -t <relay-ip> -r 400000`, or forward UDP 14550 with mavlink-router.
4. If the preflight list reports that MAVLink input is not allowed: `param set COM_RC_IN_MODE 1`.

### ArduPilot SITL

1. Set up the ArduPilot build environment (`Tools/environment_install/install-prereqs-ubuntu.sh`), then start a copter:
   `cd ardupilot && Tools/autotest/sim_vehicle.py -v ArduCopter --console --map`.
2. MAVProxy forwards the vehicle to UDP 127.0.0.1:14550; with Relay on the same computer and
   `RELAY_ALLOW_LOOPBACK=1` it appears in the fleet.
3. With Relay on another computer, add an output: `sim_vehicle.py -v ArduCopter --out=udp:<relay-ip>:14550`.
4. For Gazebo as the physics backend, install the `ardupilot_gazebo` plug-in, start `gz sim -r iris_runway.sdf`
   and then `sim_vehicle.py -v ArduCopter -f gazebo-iris --model JSON`; Relay connects the same way.

Then fly as with any aircraft: claim it, arm with confirmation, take off with a skill or the sticks, and land. For
programs, challenges and the arena, use Relay's own simulators.
