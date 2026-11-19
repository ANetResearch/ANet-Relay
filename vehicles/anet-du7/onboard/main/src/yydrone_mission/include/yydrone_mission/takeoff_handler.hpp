#ifndef YYDRONE_MISSION__TAKEOFF_HANDLER_HPP_
#define YYDRONE_MISSION__TAKEOFF_HANDLER_HPP_

#include <rclcpp/rclcpp.hpp>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <string>

namespace yydrone_mission
{

/**
 * @brief 起飞处理工具
 *
 * 支持两种模式：
 *   1. 被动模式：只监控 FSM 状态，等待 TAKEOFF → HOVER_WAIT
 *   2. 主动模式：直接向 mavros 发送起飞 setpoint（绕开 FSM）
 *
 * 默认使用被动模式（配合 yydrone_fsm）
 */
class TakeoffHandler
{
public:
  enum class Mode { PASSIVE, ACTIVE };
  enum class State { IDLE, TAKING_OFF, COMPLETE, TIMEOUT };

  TakeoffHandler(rclcpp::Node * node, Mode mode = Mode::PASSIVE);

  /// 设置目标起飞高度（米）
  void setTargetHeight(double h) { target_height_ = h; }

  /// 设置起飞速度（m/s）
  void setTakeoffSpeed(double v) { takeoff_speed_ = v; }

  /// 更新里程计
  void updateOdom(const nav_msgs::msg::Odometry & odom);

  /// 更新 FSM 状态（被动模式需要）
  void updateFsmState(const std::string & state);

  /// 开始起飞流程
  void start();

  /// 每周期调用，返回当前状态
  State update();

  /// 中止
  void abort();

  /// 当前高度
  double currentHeight() const { return current_z_; }

  /// 是否完成
  bool isComplete() const { return state_ == State::COMPLETE; }

  /// 获取起飞起始位置
  double startX() const { return start_x_; }
  double startY() const { return start_y_; }
  double startZ() const { return start_z_; }

  /// 主动模式：发布起飞 setpoint 到 mavros
  void publishTakeoffSetpoint();

private:
  rclcpp::Node * node_;
  Mode mode_;
  State state_;

  double target_height_;
  double takeoff_speed_;
  double current_z_;
  double start_x_, start_y_, start_z_;
  std::string fsm_state_;

  rclcpp::Time state_start_time_;
  rclcpp::Publisher<geometry_msgs::msg::PoseStamped>::SharedPtr position_pub_;
  bool odom_received_;
};

}  // namespace yydrone_mission

#endif  // YYDRONE_MISSION__TAKEOFF_HANDLER_HPP_
