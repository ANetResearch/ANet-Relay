cmake_minimum_required(VERSION 3.8)
project(yydrone_mission)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_components REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(std_msgs REQUIRED)
find_package(mavros_msgs REQUIRED)
find_package(tf2 REQUIRED)

# ── 编译成共享库 + 可执行文件 ──────────────────────────────────

add_library(yydrone_mission_component SHARED
  src/mode_switcher.cpp
  src/takeoff_handler.cpp
  src/mission_takeoff_and_forward.cpp
  src/mission_dispatcher.cpp
)

target_include_directories(yydrone_mission_component PRIVATE
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
)

ament_target_dependencies(yydrone_mission_component
  rclcpp
  rclcpp_components
  geometry_msgs
  nav_msgs
  std_msgs
  mavros_msgs
  tf2
)

rclcpp_components_register_node(yydrone_mission_component
  PLUGIN "yydrone_mission::MissionDispatcher"
  EXECUTABLE mission_dispatcher_node
)

# ── 安装 ──────────────────────────────────────────────────────

install(DIRECTORY include/ DESTINATION include)
install(DIRECTORY launch/ DESTINATION share/${PROJECT_NAME}/launch)
install(TARGETS yydrone_mission_component
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin
)

ament_package()
