#include "yydrone_mission/mode_switcher.hpp"

using namespace std::chrono_literals;

namespace yydrone_mission
{

ModeSwitcher::ModeSwitcher(rclcpp::Node * node)
  : node_(node),
    services_ready_(false),
    current_mode_(""),
    armed_(false),
    connected_(false)
{
  set_mode_client_ = node_->create_client<mavros_msgs::srv::SetMode>("/mavros/set_mode");
  arming_client_   = node_->create_client<mavros_msgs::srv::CommandBool>("/mavros/cmd/arming");

  // 等待服务就绪
  RCLCPP_INFO(node_->get_logger(), "[ModeSwitcher] 等待 mavros 服务...");
  bool ok = set_mode_client_->wait_for_service(5s);
  ok = ok && arming_client_->wait_for_service(5s);
  if (ok) {
    services_ready_ = true;
    RCLCPP_INFO(node_->get_logger(), "[ModeSwitcher] mavros 服务就绪");
  } else {
    RCLCPP_ERROR(node_->get_logger(), "[ModeSwitcher] mavros 服务超时！");
  }
}

bool ModeSwitcher::switchToOffboard()
{
  if (!services_ready_) return false;
  if (current_mode_ == "OFFBOARD") return true;

  auto req = std::make_shared<mavros_msgs::srv::SetMode::Request>();
  req->custom_mode = "OFFBOARD";

  auto future = set_mode_client_->async_send_request(req);
  RCLCPP_INFO(node_->get_logger(), "[ModeSwitcher] 切换 OFFBOARD...");
  return true;  // fire-and-forget，实际结果由 state callback 确认
}

bool ModeSwitcher::arm(bool do_arm)
{
  if (!services_ready_) return false;
  if (armed_ == do_arm) return true;

  auto req = std::make_shared<mavros_msgs::srv::CommandBool::Request>();
  req->value = do_arm;

  auto future = arming_client_->async_send_request(req);
  RCLCPP_INFO(node_->get_logger(), "[ModeSwitcher] %s", do_arm ? "解锁..." : "上锁...");
  return true;
}

bool ModeSwitcher::isReady() const
{
  return connected_ && armed_ && (current_mode_ == "OFFBOARD");
}

void ModeSwitcher::updateFcuState(const mavros_msgs::msg::State & state)
{
  current_mode_ = state.mode;
  armed_ = state.armed;
  connected_ = state.connected;
}

}  // namespace yydrone_mission
