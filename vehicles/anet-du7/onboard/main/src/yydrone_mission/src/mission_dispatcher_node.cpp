#include "yydrone_mission/mission_dispatcher.hpp"
#include <rclcpp/rclcpp.hpp>

int main(int argc, char** argv)
{
  rclcpp::init(argc, argv);

  try {
    auto node = std::make_shared<yydrone_mission::MissionDispatcher>();
    rclcpp::spin(node);
  } catch (const std::exception& e) {
    std::cerr << "MissionDispatcher 异常: " << e.what() << std::endl;
    return 1;
  }

  rclcpp::shutdown();
  return 0;
}
