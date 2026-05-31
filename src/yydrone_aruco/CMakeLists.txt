cmake_minimum_required(VERSION 3.8)
project(yydrone_aruco)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_components REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(tf2_ros REQUIRED)
find_package(tf2_geometry_msgs REQUIRED)
find_package(cv_bridge REQUIRED)
find_package(image_transport REQUIRED)
find_package(OpenCV REQUIRED)

add_library(yydrone_aruco_component SHARED
  src/aruco_detector.cpp
)
target_include_directories(yydrone_aruco_component PRIVATE
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
)
ament_target_dependencies(yydrone_aruco_component
  rclcpp
  rclcpp_components
  sensor_msgs
  geometry_msgs
  tf2_ros
  tf2_geometry_msgs
  cv_bridge
  image_transport
  OpenCV
)
rclcpp_components_register_node(yydrone_aruco_component
  PLUGIN "yydrone_aruco::ArucoDetector"
  EXECUTABLE yydrone_aruco_node
)

install(DIRECTORY include/ DESTINATION include)
install(DIRECTORY launch/ DESTINATION share/${PROJECT_NAME}/launch)
install(TARGETS yydrone_aruco_component
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin
)

ament_package()
