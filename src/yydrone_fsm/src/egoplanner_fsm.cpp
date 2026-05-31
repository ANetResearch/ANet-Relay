#include "yydrone_fsm/egoplanner_fsm.hpp"
#include <chrono>
#include <string>
#include <future>

using namespace std::chrono_literals;

namespace egoplanner_fsm
{

SimpleFSM::SimpleFSM() 
: Node("egoplanner_fsm_node"),
  current_state_(DroneState::INIT),
  target_height_(0.7),
  takeoff_speed_(0.5),
  max_horizontal_speed_(1.0),
  hover_position_(Vector3d::Zero()),
  takeoff_start_position_(Vector3d::Zero()),
  cmd_received_(false),
  is_armed_(false),
  offboard_engaged_(false),
  control_frequency_(50.0),  // 50Hz
  cmd_timeout_(0.5)         // 0.5秒超时
{
  // 初始化参数
  initializeParameters();
  
  // 设置发布器、订阅器和服务
  setupPublishers();
  setupSubscribers();
  setupServices();
  
  // 启动定时器
  state_machine_timer_ = this->create_wall_timer(
    100ms, std::bind(&SimpleFSM::stateMachine, this));
    
  control_timer_ = this->create_wall_timer(
    std::chrono::milliseconds(static_cast<int>(1000.0/control_frequency_)),
    std::bind(&SimpleFSM::controlLoop, this));
    
  state_start_time_ = this->now();
  
  RCLCPP_INFO(this->get_logger(), "EgoPlanner FSM节点已启动");
  RCLCPP_INFO(this->get_logger(), "初始状态: %s", stateToString(current_state_).c_str());
}

SimpleFSM::~SimpleFSM()
{
  RCLCPP_INFO(this->get_logger(), "EgoPlanner FSM节点关闭");
}

void SimpleFSM::initializeParameters()
{
  // 从参数服务器获取参数，如果没有则使用默认值
  this->declare_parameter<double>("target_height", 1.0);
  this->declare_parameter<double>("takeoff_speed", 0.5);
  this->declare_parameter<double>("max_horizontal_speed", 1.0);
  this->declare_parameter<double>("control_frequency", 50.0);
  this->declare_parameter<double>("cmd_timeout", 0.5);
  this->declare_parameter<bool>("use_attitude_control", false);
  
  this->get_parameter("target_height", target_height_);
  this->get_parameter("takeoff_speed", takeoff_speed_);
  this->get_parameter("max_horizontal_speed", max_horizontal_speed_);
  this->get_parameter("control_frequency", control_frequency_);
  this->get_parameter("cmd_timeout", cmd_timeout_);
  
  RCLCPP_INFO(this->get_logger(), "参数配置:");
  RCLCPP_INFO(this->get_logger(), "  目标高度: %.2f m", target_height_);
  RCLCPP_INFO(this->get_logger(), "  起飞速度: %.2f m/s", takeoff_speed_);
  RCLCPP_INFO(this->get_logger(), "  控制频率: %.1f Hz", control_frequency_);
}

void SimpleFSM::setupPublishers()
{ 
  // 创建发布器
  attitude_pub_ = this->create_publisher<mavros_msgs::msg::AttitudeTarget>(
    "/mavros/setpoint_raw/attitude", 10);
    
  position_pub_ = this->create_publisher<geometry_msgs::msg::PoseStamped>(
    "/mavros/setpoint_position/local", 10);
    
  state_pub_ = this->create_publisher<std_msgs::msg::String>(
    "drone_state", 10);
}

void SimpleFSM::setupSubscribers()
{ 
  rclcpp::QoS qos_profile(10);
  qos_profile.reliability(rclcpp::ReliabilityPolicy::BestEffort);
  qos_profile.durability(rclcpp::DurabilityPolicy::Volatile);
  qos_profile.history(rclcpp::HistoryPolicy::KeepLast);

  // 创建订阅器
  state_sub_ = this->create_subscription<mavros_msgs::msg::State>(
    "/mavros/state", qos_profile,
    std::bind(&SimpleFSM::stateCallback, this, std::placeholders::_1));
  
  
  odom_sub_ = this->create_subscription<nav_msgs::msg::Odometry>(
    "/mavros/local_position/odom", qos_profile,
    std::bind(&SimpleFSM::odomCallback, this, std::placeholders::_1));
    
  // 使用quadrotor_msgs作为命令输入
  cmd_sub_ = this->create_subscription<quadrotor_msgs::msg::PositionCommand>(
    "/drone_0_planning/pos_cmd", 10,
    std::bind(&SimpleFSM::commandCallback, this, std::placeholders::_1));
}

void SimpleFSM::setupServices()
{
  // 创建服务客户端
  arming_client_ = this->create_client<mavros_msgs::srv::CommandBool>("/mavros/cmd/arming");
  set_mode_client_ = this->create_client<mavros_msgs::srv::SetMode>("/mavros/set_mode");
  
  // 等待服务可用
  RCLCPP_INFO(this->get_logger(), "等待arming服务...");
  auto start_time = this->now();
  
  while (!arming_client_->wait_for_service(1s) && rclcpp::ok()) {
    // 在等待服务时持续发布命令
    publishCurrentPosition();
    
    // 超时检查
    if ((this->now() - start_time).seconds() > 30.0) {
      RCLCPP_ERROR(this->get_logger(), "等待arming服务超时");
      current_state_ = DroneState::ERROR;
      state_start_time_ = this->now();
      break;
    }
  }
  
  RCLCPP_INFO(this->get_logger(), "等待set_mode服务...");
  start_time = this->now();
  
  while (!set_mode_client_->wait_for_service(1s) && rclcpp::ok()) {
    // 在等待服务时持续发布命令
    publishCurrentPosition();
    
    // 超时检查
    if ((this->now() - start_time).seconds() > 30.0) {
      RCLCPP_ERROR(this->get_logger(), "等待set_mode服务超时");
      current_state_ = DroneState::ERROR;
      state_start_time_ = this->now();
      break;
    }
  }
  
  if (rclcpp::ok()) {
    RCLCPP_INFO(this->get_logger(), "所有服务已就绪");
  }
}

// 回调函数实现
void SimpleFSM::stateCallback(const mavros_msgs::msg::State::SharedPtr msg)
{
  current_fcu_state_ = *msg;
  is_armed_ = current_fcu_state_.armed;
  
  // 定期输出状态信息（每秒一次）
  static rclcpp::Time last_log_time = this->now();
  if ((this->now() - last_log_time).seconds() > 1.0) {
    RCLCPP_INFO_THROTTLE(this->get_logger(), *this->get_clock(), 1000,
                        "FCU状态: 连接=%s, 已解锁=%s, 模式=%s",
                        current_fcu_state_.connected ? "是" : "否",
                        current_fcu_state_.armed ? "是" : "否",
                        current_fcu_state_.mode.c_str());
    last_log_time = this->now();
  }
}

void SimpleFSM::odomCallback(const nav_msgs::msg::Odometry::SharedPtr msg)
{
  current_odom_ = *msg;
  
  // 更新当前位置
  hover_position_ = toEigen(current_odom_.pose.pose.position);
  
  // 如果是起飞状态，记录起始位置
  if (current_state_ == DroneState::INIT || current_state_ == DroneState::PREFLIGHT) {
    takeoff_start_position_ = hover_position_;
  }
}

void SimpleFSM::commandCallback(const quadrotor_msgs::msg::PositionCommand::SharedPtr msg)
{
  // 直接存储quadrotor_msgs消息
  last_cmd_ = *msg;
  last_cmd_time_ = this->now();
  cmd_received_ = true;
  
  RCLCPP_DEBUG(this->get_logger(), "收到quadrotor_msgs命令: pos=(%.2f,%.2f,%.2f), vel=(%.2f,%.2f,%.2f)", 
               last_cmd_.position.x, last_cmd_.position.y, last_cmd_.position.z,
               last_cmd_.velocity.x, last_cmd_.velocity.y, last_cmd_.velocity.z);
}

// 状态机主循环
void SimpleFSM::stateMachine()
{
  // 发布当前状态
  publishState();
  
  // 根据当前状态执行相应的处理
  switch (current_state_) {
    case DroneState::INIT:
      handleInitState();
      break;
    case DroneState::PREFLIGHT:
      handlePreflightState();
      break;
    case DroneState::TAKEOFF:
      handleTakeoffState();
      break;
    case DroneState::HOVER_WAIT:
      handleHoverWaitState();
      break;
    case DroneState::CMD_CONTROL:
      handleCmdControlState();
      break;
    case DroneState::ERROR:
      handleErrorState();
      break;
  }
}

void SimpleFSM::publishState()
{
  auto msg = std_msgs::msg::String();
  msg.data = stateToString(current_state_);
  state_pub_->publish(msg);
}

// 状态处理函数实现
void SimpleFSM::handleInitState()
{
  double elapsed = (this->now() - state_start_time_).seconds();
  
  if (elapsed < 1.0) {
    // 等待1秒让系统初始化
    return;
  }
  
  // 检查飞控连接
  if (!current_fcu_state_.connected) {
    RCLCPP_WARN(this->get_logger(), "等待飞控连接...");
    // 在等待连接时也要发送命令
    publishCurrentPosition();
    return;
  }
  
  // 进入预起飞状态
  current_state_ = DroneState::PREFLIGHT;
  state_start_time_ = this->now();
  RCLCPP_INFO(this->get_logger(), "状态转换: INIT -> PREFLIGHT");
}

void SimpleFSM::handlePreflightState()
{
  static int init_count = 0;
  
  // 1. 持续发送命令
  if (init_count < 100) {  // 发布5秒的命令
    if (init_count % 100 == 0) {  // 只发一次
      RCLCPP_INFO(this->get_logger(), "发送Offboard准备命令");
    }
    publishCurrentPosition();
    init_count++;
    
    // 在最后几次尝试切换模式
    if (init_count >= 50) {
      // 2. 尝试切换到OFFBOARD模式
      if (!offboard_engaged_) {
        RCLCPP_INFO(this->get_logger(), "尝试切换到OFFBOARD模式...");
        if (setOffboardMode()) {
          offboard_engaged_ = true;
          RCLCPP_INFO(this->get_logger(), "成功切换到OFFBOARD模式");
        } else {
          RCLCPP_WARN(this->get_logger(), "切换OFFBOARD模式失败,继续尝试...");
        }
      }
      
      // 3. 尝试解锁无人机
      if (offboard_engaged_ && !is_armed_) {
        RCLCPP_INFO(this->get_logger(), "尝试解锁无人机...");
        if (armDrone(true)) {
          RCLCPP_INFO(this->get_logger(), "无人机已解锁");
        } else {
          RCLCPP_WARN(this->get_logger(), "解锁失败，继续尝试...");
        }
      }
    }
    
    return;
  }
  
  // 4. 所有条件满足，进入起飞状态
  if (offboard_engaged_ && is_armed_) {
    current_state_ = DroneState::TAKEOFF;
    state_start_time_ = this->now();
    RCLCPP_INFO(this->get_logger(), "状态转换: PREFLIGHT -> TAKEOFF");
    RCLCPP_INFO(this->get_logger(), "开始起飞到 %.2f 米高度", target_height_);
  } else {
    // 如果条件不满足，继续发送命令并重试
    publishCurrentPosition();
    
    // 定期重试切换模式和解锁
    static int retry_count = 0;
    if (retry_count++ % 20 == 0) {  // 每2秒重试一次
      RCLCPP_INFO(this->get_logger(), "重试切换模式和解锁...");
      
      if (!offboard_engaged_) {
        if (setOffboardMode()) {
          offboard_engaged_ = true;
        }
      }
      
      if (offboard_engaged_ && !is_armed_) {
        armDrone(true);
      }
    }
  }
}

void SimpleFSM::handleTakeoffState()
{
  // 获取当前高度
  double current_z = current_odom_.pose.pose.position.z;
  
  // 计算目标高度（起飞起始高度 + 目标相对高度）
  double target_z = takeoff_start_position_.z() + target_height_;
  
  // 设置目标高度给控制环使用
  hover_position_.z() = target_z;
  
  // 检查是否达到目标高度（允许0.05米的误差）
  if (current_z >= target_z - 0.05) {
    current_state_ = DroneState::HOVER_WAIT;
    state_start_time_ = this->now();
    RCLCPP_INFO(this->get_logger(), "状态转换: TAKEOFF -> HOVER_WAIT");
    RCLCPP_INFO(this->get_logger(), "已达到目标高度 %.2f 米，开始悬停等待指令", current_z);
  }
}
void SimpleFSM::handleHoverWaitState()
{
  // 保持当前悬停位置
  // hover_position_ 已经在odomCallback中更新
  
  // 检查是否有规划指令
  if (cmd_received_ && (this->now() - last_cmd_time_).seconds() < cmd_timeout_) {
    // 有有效的指令，切换到指令控制状态
    current_state_ = DroneState::CMD_CONTROL;
    state_start_time_ = this->now();
    RCLCPP_INFO(this->get_logger(), "状态转换: HOVER_WAIT -> CMD_CONTROL");
    RCLCPP_INFO(this->get_logger(), "收到规划指令，开始执行");
  }
}

void SimpleFSM::handleCmdControlState()
{
  // 检查指令是否超时
  if ((this->now() - last_cmd_time_).seconds() > cmd_timeout_) {
    cmd_received_ = false;
    current_state_ = DroneState::HOVER_WAIT;
    state_start_time_ = this->now();
    RCLCPP_INFO(this->get_logger(), "状态转换: CMD_CONTROL -> HOVER_WAIT");
    RCLCPP_INFO(this->get_logger(), "规划指令超时，返回悬停状态");
    return;
  }
  
  // 如果有新指令，使用指令位置
  // 位置已经在commandCallback中更新
}

void SimpleFSM::handleErrorState()
{
  // 简单的错误处理：尝试重新初始化
  RCLCPP_ERROR(this->get_logger(), "错误状态，尝试恢复...");
  
  // 在错误状态也发送命令，保持控制
  publishCurrentPosition();
  
  // 尝试重新连接服务
  static int retry_count = 0;
  if (retry_count++ % 50 == 0) {  // 每5秒重试一次
    RCLCPP_INFO(this->get_logger(), "尝试重新连接服务...");
    
    // 检查服务是否可用
    if (!arming_client_->service_is_ready()) {
      RCLCPP_WARN(this->get_logger(), "arming服务不可用,重新等待...");
    }
    
    if (!set_mode_client_->service_is_ready()) {
      RCLCPP_WARN(this->get_logger(), "set_mode服务不可用,重新等待...");
    }
  }
  
  // 简单实现：等待10秒后回到INIT状态
  double elapsed = (this->now() - state_start_time_).seconds();
  if (elapsed > 10.0) {
    current_state_ = DroneState::INIT;
    state_start_time_ = this->now();
    RCLCPP_INFO(this->get_logger(), "状态转换: ERROR -> INIT");
    offboard_engaged_ = false;
    is_armed_ = false;
  }
}

// 控制循环
void SimpleFSM::controlLoop()
{
  // 根据当前状态发布相应的控制命令
  switch (current_state_) {
    case DroneState::PREFLIGHT:
      // 在PREFLIGHT状态，状态机已经处理了命令发送
      break;
    case DroneState::TAKEOFF:
      publishTakeoffPosition();
      break;
    case DroneState::HOVER_WAIT:
      publishHoverPosition();
      break;
    case DroneState::CMD_CONTROL:
      if (cmd_received_) {
        publishCommandPosition();
      } else {
        publishHoverPosition();
      }
      break;
    case DroneState::INIT:
    case DroneState::ERROR:
      // 在INIT和ERROR状态发送当前位置
      publishCurrentPosition();
      break;
  }
}

// 发布当前位置命令
void SimpleFSM::publishCurrentPosition()
{
  auto pose_msg = geometry_msgs::msg::PoseStamped();
  pose_msg.header.stamp = this->now();
  pose_msg.header.frame_id = "odom";
  pose_msg.pose.position.x = hover_position_.x();
  pose_msg.pose.position.y = hover_position_.y();
  pose_msg.pose.position.z = hover_position_.z();
  pose_msg.pose.orientation.w = 1.0;
  
  position_pub_->publish(pose_msg);
}

// 发布悬停位置命令
void SimpleFSM::publishHoverPosition()
{
  auto pose_msg = geometry_msgs::msg::PoseStamped();
  pose_msg.header.stamp = this->now();
  pose_msg.header.frame_id = "odom";
  // pose_msg.pose.position = toPoint(hover_position_);
  pose_msg.pose.position.x = hover_position_.x();
  pose_msg.pose.position.y = hover_position_.y();
  
  pose_msg.pose.position.z = 1.0;
  pose_msg.pose.orientation.w = 1.0;
  
  position_pub_->publish(pose_msg);
}

// 发布起飞位置命令
void SimpleFSM::publishTakeoffPosition()
{
  auto pose_msg = geometry_msgs::msg::PoseStamped();
  pose_msg.header.stamp = this->now();
  pose_msg.header.frame_id = "odom";
  
  // 水平位置保持起飞起始位置
  pose_msg.pose.position.x = takeoff_start_position_.x();
  pose_msg.pose.position.y = takeoff_start_position_.y();
  
  // 目标高度 = 起飞起始高度 + 目标相对高度
  // pose_msg.pose.position.z = takeoff_start_position_.z() + target_height_;
  pose_msg.pose.position.z = 1.0;
  // 保持水平朝向
  pose_msg.pose.orientation.w = 1.0;
  pose_msg.pose.orientation.x = 0.0;
  pose_msg.pose.orientation.y = 0.0;
  pose_msg.pose.orientation.z = 0.0;
  
  position_pub_->publish(pose_msg);
}
// 发布规划指令位置命令
void SimpleFSM::publishCommandPosition()
{
  auto pose_msg = geometry_msgs::msg::PoseStamped();
  pose_msg.header.stamp = this->now();
  pose_msg.header.frame_id = "odom";
  
  // 使用quadrotor_msgs中的位置
  pose_msg.pose.position.x = last_cmd_.position.x;
  pose_msg.pose.position.y = last_cmd_.position.y;
  pose_msg.pose.position.z = last_cmd_.position.z;
  
  // 使用quadrotor_msgs中的偏航角
  double yaw = last_cmd_.yaw;
  pose_msg.pose.orientation.w = cos(yaw / 2.0);
  pose_msg.pose.orientation.x = 0.0;
  pose_msg.pose.orientation.y = 0.0;
  pose_msg.pose.orientation.z = sin(yaw / 2.0);
  
  position_pub_->publish(pose_msg);
}

// 发布姿态控制命令(未使用，后续开发)
void SimpleFSM::publishAttitudeControl(double roll, double pitch, double yaw, double thrust)
{
  // 添加消除未使用参数的代码
  (void)roll;  // 显式标记未使用
  (void)pitch; // 显式标记未使用
  
  auto att_msg = mavros_msgs::msg::AttitudeTarget();
  att_msg.header.stamp = this->now();
  att_msg.header.frame_id = "base_link";
  
  // 设置控制掩码
  att_msg.type_mask = 7; // 忽略角速度
  
  // 计算四元数（简化的欧拉角到四元数转换）
  // 注意：这里假设roll和pitch为0，只控制偏航
  att_msg.orientation.w = cos(yaw/2);
  att_msg.orientation.x = 0;
  att_msg.orientation.y = 0;
  att_msg.orientation.z = sin(yaw/2);
  
  att_msg.thrust = thrust;
  
  attitude_pub_->publish(att_msg);
}

// 服务调用方法
bool SimpleFSM::setOffboardMode()
{
  // 1. 首先检查是否已经处于OFFBOARD模式
  if (current_fcu_state_.mode == "OFFBOARD") {
    RCLCPP_INFO(this->get_logger(), "已经在OFFBOARD模式");
    offboard_engaged_ = true;
    return true;
  }
  for (int i = 0; i < 10; ++i) {
    publishCurrentPosition();
    rclcpp::sleep_for(50ms);
  }
  // 2. 发送服务请求
  auto request = std::make_shared<mavros_msgs::srv::SetMode::Request>();
  request->custom_mode = "OFFBOARD";
  
  auto future = set_mode_client_->async_send_request(request);
  offboard_engaged_ = true;
  return true;
}

bool SimpleFSM::armDrone(bool arm)
{
  // 确保在尝试解锁前已经发送了几个命令
  for (int i = 0; i < 10; ++i) {
    publishCurrentPosition();
    rclcpp::sleep_for(50ms);
  }
  
  auto request = std::make_shared<mavros_msgs::srv::CommandBool::Request>();
  request->value = arm;
  
  
  auto future = arming_client_->async_send_request(request);
  is_armed_ = true;
  return true;
}

// 工具方法
std::string SimpleFSM::stateToString(DroneState state)
{
  switch (state) {
    case DroneState::INIT: return "INIT";
    case DroneState::PREFLIGHT: return "PREFLIGHT";
    case DroneState::TAKEOFF: return "TAKEOFF";
    case DroneState::HOVER_WAIT: return "HOVER_WAIT";
    case DroneState::CMD_CONTROL: return "CMD_CONTROL";
    case DroneState::ERROR: return "ERROR";
    default: return "UNKNOWN";
  }
}

Vector3d SimpleFSM::toEigen(const geometry_msgs::msg::Point& point)
{
  return Vector3d(point.x, point.y, point.z);
}

geometry_msgs::msg::Point SimpleFSM::toPoint(const Vector3d& vec)
{
  geometry_msgs::msg::Point point;
  point.x = vec.x();
  point.y = vec.y();
  point.z = vec.z();
  return point;
}

// 从PositionCommand提取数据到Eigen
Vector3d SimpleFSM::getPositionFromCommand(const quadrotor_msgs::msg::PositionCommand& cmd)
{
  return Vector3d(cmd.position.x, cmd.position.y, cmd.position.z);
}

Vector3d SimpleFSM::getVelocityFromCommand(const quadrotor_msgs::msg::PositionCommand& cmd)
{
  return Vector3d(cmd.velocity.x, cmd.velocity.y, cmd.velocity.z);
}

Vector3d SimpleFSM::getAccelerationFromCommand(const quadrotor_msgs::msg::PositionCommand& cmd)
{
  return Vector3d(cmd.acceleration.x, cmd.acceleration.y, cmd.acceleration.z);
}

double SimpleFSM::getYawFromCommand(const quadrotor_msgs::msg::PositionCommand& cmd)
{
  return cmd.yaw;
}

double SimpleFSM::getYawRateFromCommand(const quadrotor_msgs::msg::PositionCommand& cmd)
{
  return cmd.yaw_dot;
}

}  // namespace egoplanner_fsm