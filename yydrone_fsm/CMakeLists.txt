cmake_minimum_required(VERSION 3.8)
project(yydrone_fsm)

# Default to C++14
if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 14)
endif()

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

# Find dependencies
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_components REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(mavros_msgs REQUIRED)
find_package(std_msgs REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(sensor_msgs REQUIRED)

# Find Eigen3
find_package(eigen3_cmake_module REQUIRED)
find_package(Eigen3 REQUIRED)


find_package(quadrotor_msgs REQUIRED)

# Include directories
include_directories(
  include
  ${Eigen3_INCLUDE_DIRS}
)

# Add executable
add_executable(egoplanner_fsm_node
  src/egoplanner_fsm_node.cpp
  src/egoplanner_fsm.cpp
)

# Link libraries
target_include_directories(egoplanner_fsm_node PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
)

ament_target_dependencies(egoplanner_fsm_node
  rclcpp
  rclcpp_components
  geometry_msgs
  mavros_msgs
  std_msgs
  nav_msgs
  sensor_msgs
  quadrotor_msgs   
)

# Install targets
install(TARGETS
  egoplanner_fsm_node
  DESTINATION lib/${PROJECT_NAME}
)

# Install launch files
install(DIRECTORY
  launch
  DESTINATION share/${PROJECT_NAME}
)

# Install config files
install(DIRECTORY
  config
  DESTINATION share/${PROJECT_NAME}
)

# Install include files
install(DIRECTORY include/
  DESTINATION include/
)

# Export dependencies
ament_export_include_directories(include)
ament_export_libraries(egoplanner_fsm_node)
ament_export_dependencies(
  rclcpp
  geometry_msgs
  mavros_msgs
  Eigen3
)

ament_package()