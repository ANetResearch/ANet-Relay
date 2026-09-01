conda config --set auto_activate_base false
# 1. 启动 AR 检测（支持任何类型的 AR 码）
ros2 launch yydrone_aruco aruco_detector.launch.py dictionary:=ALL

# 2. 启动任务
ros2 launch yydrone_mission mission_launch.py mission_name:=fly_and_detect

# 自定义参数
ros2 launch yydrone_mission mission_launch.py \
    mission_name:=fly_and_detect \
    forward_distance:=3.0 \
    detect_duration:=20.0

 colcon build --symlink-install --cmake-args -DCMAKE_POLICY_VERSION_MINIMUM=3.5 2>&1 | grep -E "(Starting|Finished|Failed|Summary|Aborted)" | tail -30   

 # 禁用开机自启
 sudo systemctl disable drone-stack

 # 启用开机自启
 sudo systemctl enable drone-stack.service

 # 停止服务
 sudo systemctl stop drone-stack

 # 启动服务
 sudo systemctl start drone-stack.service