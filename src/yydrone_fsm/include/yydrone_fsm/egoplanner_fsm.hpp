#ifndef EGO_PLANNER_FSM_HPP_
#define EGO_PLANNER_FSM_HPP_

#include <rclcpp/rclcpp.hpp>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <geometry_msgs/msg/twist_stamped.hpp>
#include <mavros_msgs/msg/state.hpp>
#include <mavros_msgs/msg/attitude_target.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <std_msgs/msg/string.hpp>
#include <quadrotor_msgs/msg/position_command.hpp>
#include <mavros_msgs/srv/command_bool.hpp>
#include <mavros_msgs/srv/set_mode.hpp>
#include <Eigen/Dense>
#include <memory>
#include <chrono>

namespace egoplanner_fsm
{

// 使用Eigen简化矩阵操作
using Eigen::Vector3d;
using Eigen::Quaterniond;

// 状态枚举
enum class DroneState {
  INIT,           // 初始化状态
  PREFLIGHT,      // 预起飞准备
  TAKEOFF,        // 起飞到指定高度
  HOVER_WAIT,     // 悬停等待指令
  CMD_CONTROL,    // 执行规划指令
  ERROR           // 错误状态
};
class SimpleFSM : public rclcpp::Node
{
public:
  // 使用智能指针创建
  SimpleFSM();
  ~SimpleFSM();
  
  // 如果需要，可以添加移动构造和移动赋值
  SimpleFSM(SimpleFSM&&) = delete;
  SimpleFSM& operator=(SimpleFSM&&) = delete;

private:
  // ROS2接口
  rclcpp::Publisher<mavros_msgs::msg::AttitudeTarget>::SharedPtr attitude_pub_;
  rclcpp::Publisher<geometry_msgs::msg::PoseStamped>::SharedPtr position_pub_;
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
  
  // 状态变量
  DroneState current_state_;
  std::string state_str_;
  mavros_msgs::msg::State current_fcu_state_;
  nav_msgs::msg::Odometry current_odom_;
  quadrotor_msgs::msg::PositionCommand last_cmd_;
  bool cmd_received_;
  bool is_armed_;             // 是否已解锁
  bool offboard_engaged_;     // OFFBOARD模式是否已启动
  
  rclcpp::Time last_cmd_time_;
  rclcpp::Time state_start_time_;
  
  // 控制参数
  double target_height_;      // 目标高度
  double takeoff_speed_;      // 起飞速度
  double max_horizontal_speed_; // 最大水平速度
  Vector3d hover_position_;   // 悬停位置
  Vector3d takeoff_start_position_;  // 起飞起始位置
  
  // 参数
  double control_frequency_;
  double cmd_timeout_;
  
  // 私有方法
  void initializeParameters();
  void setupPublishers();
  void setupSubscribers();
  void setupServices();
  
  // 回调函数
  void stateCallback(const mavros_msgs::msg::State::SharedPtr msg);
  void odomCallback(const nav_msgs::msg::Odometry::SharedPtr msg);
  void commandCallback(const quadrotor_msgs::msg::PositionCommand::SharedPtr msg);
  
  // 状态机方法
  void stateMachine();
  void publishState();
  
  // 状态处理函数
  void handleInitState();
  void handlePreflightState();
  void handleTakeoffState();
  void handleHoverWaitState();
  void handleCmdControlState();
  void handleErrorState();
  
  // 控制方法
  void controlLoop();
  void publishCurrentPosition();
  void publishTakeoffPosition();
  void publishHoverPosition();
  void publishCommandPosition();
  void publishAttitudeControl(double roll, double pitch, double yaw, double thrust);
  
  // 服务调用方法
  bool setOffboardMode();
  bool armDrone(bool arm);
  
  // 工具方法
  std::string stateToString(DroneState state);
  Vector3d toEigen(const geometry_msgs::msg::Point& point);
  geometry_msgs::msg::Point toPoint(const Vector3d& vec);
  
  // 从PositionCommand提取数据到Eigen
  Vector3d getPositionFromCommand(const quadrotor_msgs::msg::PositionCommand& cmd);
  Vector3d getVelocityFromCommand(const quadrotor_msgs::msg::PositionCommand& cmd);
  Vector3d getAccelerationFromCommand(const quadrotor_msgs::msg::PositionCommand& cmd);
  Vector3d getJerkFromCommand(const quadrotor_msgs::msg::PositionCommand& cmd);
  double getYawFromCommand(const quadrotor_msgs::msg::PositionCommand& cmd);
  double getYawRateFromCommand(const quadrotor_msgs::msg::PositionCommand& cmd);
};

}  // namespace egoplanner_fsm

#endif  // EGO_PLANNER_FSM_HPP_