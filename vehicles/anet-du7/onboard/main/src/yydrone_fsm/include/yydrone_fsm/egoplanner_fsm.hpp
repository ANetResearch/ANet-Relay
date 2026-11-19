#ifndef EGO_PLANNER_FSM_HPP_
#define EGO_PLANNER_FSM_HPP_

#include <rclcpp/rclcpp.hpp>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <mavros_msgs/msg/state.hpp>
#include <mavros_msgs/msg/attitude_target.hpp>
#include <mavros_msgs/msg/position_target.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <std_msgs/msg/string.hpp>
#include <quadrotor_msgs/msg/position_command.hpp>
#include <mavros_msgs/srv/command_bool.hpp>
#include <mavros_msgs/srv/set_mode.hpp>
#include <rcl_interfaces/msg/set_parameters_result.hpp>
#include <Eigen/Dense>
#include <memory>
#include <chrono>

namespace egoplanner_fsm
{

using Eigen::Vector3d;
using Eigen::Quaterniond;

enum class DroneState {
  INIT,
  PREFLIGHT,
  TAKEOFF,
  HOVER_WAIT,
  CMD_CONTROL,
  ERROR
};

class SimpleFSM : public rclcpp::Node
{
public:
  SimpleFSM();
  ~SimpleFSM();

  SimpleFSM(SimpleFSM&&) = delete;
  SimpleFSM& operator=(SimpleFSM&&) = delete;

private:
  // ROS2 接口
  rclcpp::Publisher<mavros_msgs::msg::AttitudeTarget>::SharedPtr attitude_pub_;
  rclcpp::Publisher<mavros_msgs::msg::PositionTarget>::SharedPtr position_pub_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr state_pub_;

  rclcpp::Subscription<mavros_msgs::msg::State>::SharedPtr state_sub_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr odom_sub_;
  rclcpp::Subscription<quadrotor_msgs::msg::PositionCommand>::SharedPtr cmd_sub_;

  // 服务客户端
  rclcpp::Client<mavros_msgs::srv::CommandBool>::SharedPtr arming_client_;
  rclcpp::Client<mavros_msgs::srv::SetMode>::SharedPtr set_mode_client_;

  // 定时器
  rclcpp::TimerBase::SharedPtr state_machine_timer_;
  rclcpp::TimerBase::SharedPtr control_timer_;

  // 参数回调
  rclcpp::node_interfaces::OnSetParametersCallbackHandle::SharedPtr param_callback_handle_;

  // 状态变量
  DroneState current_state_;
  mavros_msgs::msg::State current_fcu_state_;
  nav_msgs::msg::Odometry current_odom_;
  quadrotor_msgs::msg::PositionCommand last_cmd_;
  bool cmd_received_;
  bool is_armed_;
  bool offboard_engaged_;

  rclcpp::Time last_cmd_time_;
  rclcpp::Time state_start_time_;

  // 控制参数
  double target_height_;
  double takeoff_speed_;
  double max_horizontal_speed_;
  Vector3d hover_position_;
  Vector3d takeoff_start_position_;

  // 参数
  double control_frequency_;
  double cmd_timeout_;
  double velocity_scale_;
  double acceleration_scale_;

  // 方法
  void initializeParameters();
  rcl_interfaces::msg::SetParametersResult onParameterChange(
      const std::vector<rclcpp::Parameter> &params);
  void setupPublishers();
  void setupSubscribers();
  void setupServices();

  void stateCallback(const mavros_msgs::msg::State::SharedPtr msg);
  void odomCallback(const nav_msgs::msg::Odometry::SharedPtr msg);
  void commandCallback(const quadrotor_msgs::msg::PositionCommand::SharedPtr msg);

  void stateMachine();
  void publishState();
  void handleInitState();
  void handlePreflightState();
  void handleTakeoffState();
  void handleHoverWaitState();
  void handleCmdControlState();
  void handleErrorState();

  void controlLoop();
  void publishCurrentPosition();
  void publishTakeoffPosition();
  void publishHoverPosition();
  void publishCommandPosition();
  void publishAttitudeControl(double roll, double pitch, double yaw, double thrust);

  bool setOffboardMode();
  bool armDrone(bool arm);

  std::string stateToString(DroneState state);
  Vector3d toEigen(const geometry_msgs::msg::Point& point);

  Vector3d getPositionFromCommand(const quadrotor_msgs::msg::PositionCommand& cmd);
  Vector3d getVelocityFromCommand(const quadrotor_msgs::msg::PositionCommand& cmd);
  Vector3d getAccelerationFromCommand(const quadrotor_msgs::msg::PositionCommand& cmd);
  double getYawFromCommand(const quadrotor_msgs::msg::PositionCommand& cmd);
  double getYawRateFromCommand(const quadrotor_msgs::msg::PositionCommand& cmd);
};

}  // namespace egoplanner_fsm

#endif  // EGO_PLANNER_FSM_HPP_