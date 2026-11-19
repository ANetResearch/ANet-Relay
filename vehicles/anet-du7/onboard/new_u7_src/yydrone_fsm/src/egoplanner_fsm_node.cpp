#include "yydrone_fsm/egoplanner_fsm.hpp"
#include <rclcpp/rclcpp.hpp>

int main(int argc, char** argv)
{
  rclcpp::init(argc, argv);
  
  try {
    auto node = std::make_shared<egoplanner_fsm::SimpleFSM>();
    rclcpp::spin(node);
  } catch (const std::exception& e) {
    std::cerr << "节点运行异常: " << e.what() << std::endl;
    return 1;
  }
  
  rclcpp::shutdown();
  return 0;
}