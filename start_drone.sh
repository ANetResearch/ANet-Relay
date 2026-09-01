#!/bin/bash

# Drone startup script - launches MAVROS, Livox MID360, and Fast-LIO localization
# Managed by systemd: drone-stack.service

set -e

# Source ROS2 environment
source /opt/ros/humble/setup.bash
source /home/lcx/drone_ws/install/setup.bash

# Wait for FCU device if not present yet
MAX_WAIT=30
WAITED=0
while [ ! -e /dev/ttyACM0 ] && [ $WAITED -lt $MAX_WAIT ]; do
    echo "[startup] Waiting for /dev/ttyACM0... (${WAITED}s)"
    sleep 1
    WAITED=$((WAITED + 1))
done

if [ ! -e /dev/ttyACM0 ]; then
    echo "[startup] WARNING: /dev/ttyACM0 not found after ${MAX_WAIT}s, launching MAVROS anyway..."
fi

# 1. Launch MAVROS (PX4 communication)
echo "[startup] [1/3] Launching MAVROS..."
ros2 launch mavros px4.launch \
    fcu_url:=/dev/ttyACM0:921600 \
    gcs_url:=udp://:14550@192.168.43.111:14550 &
MAVROS_PID=$!

sleep 2

# 2. Launch Livox MID360 LiDAR driver
echo "[startup] [2/3] Launching Livox MID360..."
ros2 launch livox_ros_driver2 msg_MID360_launch.py &
MID_PID=$!

sleep 2

# 3. Launch Fast-LIO localization
echo "[startup] [3/4] Launching Fast-LIO localization..."
ros2 launch yydrone_localization fastlio_launch.py &
LOC_PID=$!

sleep 2

# 4. Launch Safety monitor layer
echo "[startup] [4/4] Launching safety monitor..."
ros2 launch yydrone_safety safety_launch.py &
SAFETY_PID=$!

echo "[startup] All processes started | MAVROS=$MAVROS_PID MID360=$MID_PID FastLIO=$LOC_PID Safety=$SAFETY_PID"

# Cleanup on stop
cleanup() {
    echo "[startup] Stopping all drone processes..."
    # Send SIGTERM to the launch process groups to let ROS2 clean up
    kill $MAVROS_PID $MID_PID $LOC_PID $SAFETY_PID 2>/dev/null
    sleep 2
    # Force kill anything still running
    kill -9 $MAVROS_PID $MID_PID $LOC_PID $SAFETY_PID 2>/dev/null
    wait $MAVROS_PID $MID_PID $LOC_PID $SAFETY_PID 2>/dev/null
    echo "[startup] All processes stopped."
    exit 0
}

trap cleanup SIGINT SIGTERM

# Wait for all background processes
wait
