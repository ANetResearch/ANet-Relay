cmake_minimum_required(VERSION 3.8)
project(yydrone_localization)

# 设置C++标准
if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 14)
endif()

# 查找依赖包
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(tf2_ros REQUIRED)
find_package(tf2_geometry_msgs REQUIRED)

# 查找Eigen3（如果需要）
find_package(Eigen3 REQUIRED)

# 包含目录
include_directories(
  include
  ${Eigen3_INCLUDE_DIRS}
)

# 添加你的C++节点
add_executable(fastlio_locate
  src/fastlio_locate.cpp
)

# 链接库
target_link_libraries(fastlio_locate
  ${rclcpp_LIBRARIES}
  ${geometry_msgs_LIBRARIES}
  ${nav_msgs_LIBRARIES}
  ${tf2_LIBRARIES}
  ${tf2_ros_LIBRARIES}
)

# 安装C++可执行文件
install(TARGETS fastlio_locate
  RUNTIME DESTINATION lib/${PROJECT_NAME}
)

# 安装Python脚本
install(PROGRAMS
  scripts/ground_filter.py
  scripts/relocalize.py
  DESTINATION lib/${PROJECT_NAME}
)

# 安装launch文件和config文件
install(DIRECTORY launch config
  DESTINATION share/${PROJECT_NAME}
)

ament_target_dependencies(fastlio_locate
  rclcpp
  geometry_msgs
  nav_msgs
  tf2_ros
  tf2_geometry_msgs
)
# 导出依赖
ament_export_dependencies(
  rclcpp
  geometry_msgs
  nav_msgs
  tf2
  tf2_ros
)

ament_package()