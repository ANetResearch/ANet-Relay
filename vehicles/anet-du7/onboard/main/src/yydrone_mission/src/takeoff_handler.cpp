#include "yydrone_mission/takeoff_handler.hpp"

using namespace std::chrono_literals;

namespace yydrone_mission
{

TakeoffHandler::TakeoffHandler(rclcpp::Node * node, Mode mode)
  : node_(node),
    mode_(mode),
    state_(State::IDLE),
    target_height_(1.0),
    takeoff_speed_(0.5),
    current_z_(0.0),
    start_x_(0.0), start_y_(0.0), start_z_(0.0),
    fsm_state_(""),
    odom_received_(false)
{
  // 主动模式需要的 publisher
  if (mode_ == Mode::ACTIVE && node_) {
    position_pub_ = node_->create_publisher<geometry_msgs::msg::PoseStamped>(
      "/mavros/setpoint_position/local", 10);
  }
  if (node_) {
    state_start_time_ = node_->now();
  }
}

void TakeoffHandler::updateOdom(const nav_msgs::msg::Odometry & odom)
{
  current_z_ = odom.pose.pose.position.z;
  if (!odom_received_) {
    start_x_ = odom.pose.pose.position.x;
    start_y_ = odom.pose.pose.position.y;
    start_z_ = odom.pose.pose.position.z;
    odom_received_ = true;
  }
}

void TakeoffHandler::updateFsmState(const std::string & state)
{
  fsm_state_ = state;
}

void TakeoffHandler::start()
{
  RCLCPP_INFO(node_->get_logger(),
    "[TakeoffHandler] 起飞开始 | 目标高度 %.1f m | 模式 %s",
    target_height_, mode_ == Mode::PASSIVE ? "被动(等FSM)" : "主动");
  state_ = State::TAKING_OFF;
  state_start_time_ = node_->now();
}

TakeoffHandler::State TakeoffHandler::update()
{
  if (state_ != State::TAKING_OFF || !node_) return state_;

  if (mode_ == Mode::PASSIVE) {
    // 被动：等 FSM 从 TAKEOFF 变成 HOVER_WAIT
    if (fsm_state_ == "HOVER_WAIT" &&
        std::abs(current_z_ - target_height_) < 0.3)
    {
      RCLCPP_INFO(node_->get_logger(),
        "[TakeoffHandler] 起飞完成 | 高度 %.2f m", current_z_);
      state_ = State::COMPLETE;
    }
  } else {
    // 主动：自己发 setpoint
    publishTakeoffSetpoint();
    if (std::abs(current_z_ - target_height_) < 0.1) {
      RCLCPP_INFO(node_->get_logger(),
        "[TakeoffHandler] 主动起飞完成 | 高度 %.2f m", current_z_);
      state_ = State::COMPLETE;
    }
  }

  // 超时
  double elapsed = (node_->now() - state_start_time_).seconds();
  if (elapsed > 30.0 && state_ == State::TAKING_OFF) {
    RCLCPP_ERROR(node_->get_logger(), "[TakeoffHandler] 起飞超时！当前高度 %.2f", current_z_);
    state_ = State::TIMEOUT;
  }

  return state_;
}

void TakeoffHandler::abort()
{
  state_ = State::IDLE;
}

void TakeoffHandler::publishTakeoffSetpoint()
{
  auto pose = geometry_msgs::msg::PoseStamped();
  pose.header.stamp = node_->now();
  pose.header.frame_id = "odom";
  pose.pose.position.x = start_x_;
  pose.pose.position.y = start_y_;
  pose.pose.position.z = target_height_;
  pose.pose.orientation.w = 1.0;
  position_pub_->publish(pose);
}

}  // namespace yydrone_mission
