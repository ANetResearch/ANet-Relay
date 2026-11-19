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
  target_height_(1.0),
  takeoff_speed_(0.5),
  max_horizontal_speed_(1.0),
  hover_position_(Vector3d::Zero()),
  takeoff_start_position_(Vector3d::Zero()),
  cmd_received_(false),
  is_armed_(false),
  offboard_engaged_(false),
  control_frequency_(50.0),
  cmd_timeout_(0.5),
  velocity_scale_(0.5),
  acceleration_scale_(0.3),
  pending_goal_valid_(false),
  odom_received_(false)
{
  initializeParameters();
  setupPublishers();
  setupSubscribers();
  setupServices();

  state_machine_timer_ = this->create_wall_timer(
    100ms, std::bind(&SimpleFSM::stateMachine, this));

  control_timer_ = this->create_wall_timer(
    std::chrono::milliseconds(static_cast<int>(1000.0 / control_frequency_)),
    std::bind(&SimpleFSM::controlLoop, this));

  state_start_time_ = this->now();

  RCLCPP_INFO(this->get_logger(), "EgoPlanner FSM 节点已启动");
  RCLCPP_INFO(this->get_logger(), "初始状态: %s", stateToString(current_state_).c_str());
}

SimpleFSM::~SimpleFSM()
{
  RCLCPP_INFO(this->get_logger(), "EgoPlanner FSM 节点关闭");
}

// 
// 参数
// 

void SimpleFSM::initializeParameters()
{
  this->declare_parameter<double>("target_height", 1.0);
  this->declare_parameter<double>("takeoff_speed", 0.5);
  this->declare_parameter<double>("max_horizontal_speed", 1.0);
  this->declare_parameter<double>("control_frequency", 50.0);
  this->declare_parameter<double>("cmd_timeout", 0.5);
  this->declare_parameter<bool>("use_attitude_control", false);
  this->declare_parameter<double>("velocity_scale", 0.5);
  this->declare_parameter<double>("acceleration_scale", 0.3);
  this->declare_parameter<double>("rotation_complete_threshold_deg", 15.0);
  this->declare_parameter<double>("rotation_settle_time", 2.0);
  this->declare_parameter<double>("rotation_timeout", 10.0);

  this->get_parameter("target_height", target_height_);
  this->get_parameter("takeoff_speed", takeoff_speed_);
  this->get_parameter("max_horizontal_speed", max_horizontal_speed_);
  this->get_parameter("control_frequency", control_frequency_);
  this->get_parameter("cmd_timeout", cmd_timeout_);
  this->get_parameter("velocity_scale", velocity_scale_);
  this->get_parameter("acceleration_scale", acceleration_scale_);
  this->get_parameter("rotation_complete_threshold_deg", rotation_complete_threshold_deg_);
  this->get_parameter("rotation_settle_time", rotation_settle_time_);
  this->get_parameter("rotation_timeout", rotation_timeout_);

  param_callback_handle_ = this->add_on_set_parameters_callback(
      std::bind(&SimpleFSM::onParameterChange, this, std::placeholders::_1));

  RCLCPP_INFO(this->get_logger(), "参数配置:");
  RCLCPP_INFO(this->get_logger(), "  目标高度: %.2f m", target_height_);
  RCLCPP_INFO(this->get_logger(), "  控制频率: %.1f Hz", control_frequency_);
  RCLCPP_INFO(this->get_logger(), "  速度前馈缩放: %.2f", velocity_scale_);
  RCLCPP_INFO(this->get_logger(), "  加速度前馈缩放: %.2f", acceleration_scale_);
}

rcl_interfaces::msg::SetParametersResult SimpleFSM::onParameterChange(
    const std::vector<rclcpp::Parameter> &params)
{
  for (const auto &p : params)
  {
    if (p.get_name() == "velocity_scale")
    {
      velocity_scale_ = std::clamp(p.as_double(), 0.0, 1.0);
      RCLCPP_INFO(this->get_logger(), "速度前馈缩放更新: %.2f", velocity_scale_);
    }
    else if (p.get_name() == "acceleration_scale")
    {
      acceleration_scale_ = std::clamp(p.as_double(), 0.0, 1.0);
      RCLCPP_INFO(this->get_logger(), "加速度前馈缩放更新: %.2f", acceleration_scale_);
    }
  }
  rcl_interfaces::msg::SetParametersResult result;
  result.successful = true;
  return result;
}

// 
// Publisher / Subscriber / Service
// 

void SimpleFSM::setupPublishers()
{
  attitude_pub_ = this->create_publisher<mavros_msgs::msg::AttitudeTarget>(
    "/mavros/setpoint_raw/attitude", 10);

  // setpoint_raw/local 支持位置 + 速度 + 加速度 前馈
  position_pub_ = this->create_publisher<mavros_msgs::msg::PositionTarget>(
    "/mavros/setpoint_raw/local", 10);

  state_pub_ = this->create_publisher<std_msgs::msg::String>("drone_state", 10);

  goal_relay_pub_ = this->create_publisher<geometry_msgs::msg::PoseStamped>(
    "/move_base_simple/goal_relay", 10);
}

void SimpleFSM::setupSubscribers()
{
  rclcpp::QoS qos_profile(10);
  qos_profile.reliability(rclcpp::ReliabilityPolicy::BestEffort);
  qos_profile.durability(rclcpp::DurabilityPolicy::Volatile);
  qos_profile.history(rclcpp::HistoryPolicy::KeepLast);

  state_sub_ = this->create_subscription<mavros_msgs::msg::State>(
    "/mavros/state", qos_profile,
    std::bind(&SimpleFSM::stateCallback, this, std::placeholders::_1));

  odom_sub_ = this->create_subscription<nav_msgs::msg::Odometry>(
    "/mavros/local_position/odom", qos_profile,
    std::bind(&SimpleFSM::odomCallback, this, std::placeholders::_1));

  cmd_sub_ = this->create_subscription<quadrotor_msgs::msg::PositionCommand>(
    "/drone_0_planning/pos_cmd", 10,
    std::bind(&SimpleFSM::commandCallback, this, std::placeholders::_1));

  goal_intercept_sub_ = this->create_subscription<geometry_msgs::msg::PoseStamped>(
    "/move_base_simple/goal", 10,
    std::bind(&SimpleFSM::goalInterceptCallback, this, std::placeholders::_1));

  RCLCPP_INFO(this->get_logger(),
    "Goal interception enabled: rotation_threshold=%.1f°, settle_time=%.1fs",
    rotation_complete_threshold_deg_, rotation_settle_time_);
}

void SimpleFSM::setupServices()
{
  arming_client_ = this->create_client<mavros_msgs::srv::CommandBool>("/mavros/cmd/arming");
  set_mode_client_ = this->create_client<mavros_msgs::srv::SetMode>("/mavros/set_mode");

  RCLCPP_INFO(this->get_logger(), "等待 arming 服务...");
  auto start_time = this->now();

  while (!arming_client_->wait_for_service(1s) && rclcpp::ok()) {
    publishCurrentPosition();
    if ((this->now() - start_time).seconds() > 30.0) {
      RCLCPP_ERROR(this->get_logger(), "等待 arming 服务超时");
      current_state_ = DroneState::ERROR;
      state_start_time_ = this->now();
      break;
    }
  }

  RCLCPP_INFO(this->get_logger(), "等待 set_mode 服务...");
  start_time = this->now();

  while (!set_mode_client_->wait_for_service(1s) && rclcpp::ok()) {
    publishCurrentPosition();
    if ((this->now() - start_time).seconds() > 30.0) {
      RCLCPP_ERROR(this->get_logger(), "等待 set_mode 服务超时");
      current_state_ = DroneState::ERROR;
      state_start_time_ = this->now();
      break;
    }
  }

  if (rclcpp::ok()) {
    RCLCPP_INFO(this->get_logger(), "所有服务已就绪");
  }
}

// 
// 回调
// 

void SimpleFSM::stateCallback(const mavros_msgs::msg::State::SharedPtr msg)
{
  current_fcu_state_ = *msg;
  is_armed_ = current_fcu_state_.armed;

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

  // 更新位置和姿态用于旋转逻辑
  current_position_ = Eigen::Vector3d(
    msg->pose.pose.position.x,
    msg->pose.pose.position.y,
    msg->pose.pose.position.z
  );

  current_orientation_ = Eigen::Quaterniond(
    msg->pose.pose.orientation.w,
    msg->pose.pose.orientation.x,
    msg->pose.pose.orientation.y,
    msg->pose.pose.orientation.z
  );

  odom_received_ = true;

  // CMD_CONTROL 期间持续更新 hover_position_，切回悬停时 z 不会跳变
  if (cmd_received_) {
    hover_position_ = toEigen(current_odom_.pose.pose.position);
  }

  // INIT/PREFLIGHT 期间 cmd_received_ 恒为 false，hover_position_ 不会被上面
  // 那个分支更新，会一直停留在构造时的 (0,0,0)。这里用实时里程计的 x/y 覆盖，
  // 确保起飞目标是无人机实际所在位置，而不是原点（例如起飞前被搬动过）。
  if (current_state_ == DroneState::INIT || current_state_ == DroneState::PREFLIGHT) {
    hover_position_.x() = current_position_.x();
    hover_position_.y() = current_position_.y();
    takeoff_start_position_ = hover_position_;
  }
}

void SimpleFSM::commandCallback(const quadrotor_msgs::msg::PositionCommand::SharedPtr msg)
{
  last_cmd_ = *msg;
  last_cmd_time_ = this->now();
  cmd_received_ = true;

  RCLCPP_DEBUG(this->get_logger(), "收到指令: pos=(%.2f,%.2f,%.2f), vel=(%.2f,%.2f,%.2f)",
               last_cmd_.position.x, last_cmd_.position.y, last_cmd_.position.z,
               last_cmd_.velocity.x, last_cmd_.velocity.y, last_cmd_.velocity.z);
}

// 
// 状态机
// 

void SimpleFSM::stateMachine()
{
  publishState();

  switch (current_state_) {
    case DroneState::INIT:        handleInitState();        break;
    case DroneState::PREFLIGHT:   handlePreflightState();   break;
    case DroneState::TAKEOFF:     handleTakeoffState();     break;
    case DroneState::HOVER_WAIT:  handleHoverWaitState();   break;
    case DroneState::ROTATING:    handleRotatingState();    break;
    case DroneState::CMD_CONTROL: handleCmdControlState();  break;
    case DroneState::ERROR:       handleErrorState();       break;
  }
}

void SimpleFSM::publishState()
{
  auto msg = std_msgs::msg::String();
  msg.data = stateToString(current_state_);
  state_pub_->publish(msg);
}

void SimpleFSM::handleInitState()
{
  double elapsed = (this->now() - state_start_time_).seconds();
  if (elapsed < 1.0) return;

  if (!current_fcu_state_.connected) {
    RCLCPP_WARN(this->get_logger(), "等待飞控连接...");
    publishCurrentPosition();
    return;
  }

  current_state_ = DroneState::PREFLIGHT;
  state_start_time_ = this->now();
  RCLCPP_INFO(this->get_logger(), "状态转换: INIT -> PREFLIGHT");
}

void SimpleFSM::handlePreflightState()
{
  static int init_count = 0;

  if (init_count < 100) {
    if (init_count % 100 == 0) {
      RCLCPP_INFO(this->get_logger(), "发送 Offboard 准备命令");
    }
    publishCurrentPosition();
    init_count++;

    if (init_count >= 50) {
      if (!offboard_engaged_) {
        RCLCPP_INFO(this->get_logger(), "尝试切换到 OFFBOARD 模式...");
        if (setOffboardMode()) {
          offboard_engaged_ = true;
          RCLCPP_INFO(this->get_logger(), "成功切换到 OFFBOARD 模式");
        } else {
          RCLCPP_WARN(this->get_logger(), "切换 OFFBOARD 模式失败,继续尝试...");
        }
      }

      if (offboard_engaged_ && !is_armed_) {
        RCLCPP_INFO(this->get_logger(), "尝试解锁...");
        if (armDrone(true)) {
          RCLCPP_INFO(this->get_logger(), "已解锁");
        } else {
          RCLCPP_WARN(this->get_logger(), "解锁失败，继续尝试...");
        }
      }
    }
    return;
  }

  if (offboard_engaged_ && is_armed_) {
    current_state_ = DroneState::TAKEOFF;
    state_start_time_ = this->now();
    RCLCPP_INFO(this->get_logger(), "状态转换: PREFLIGHT -> TAKEOFF");
  } else {
    publishCurrentPosition();
    static int retry_count = 0;
    if (retry_count++ % 20 == 0) {
      if (!offboard_engaged_) setOffboardMode();
      if (offboard_engaged_ && !is_armed_) armDrone(true);
    }
  }
}

void SimpleFSM::handleTakeoffState()
{
  double current_z = current_odom_.pose.pose.position.z;
  double target_z = target_height_;
  hover_position_.z() = target_z;

  RCLCPP_INFO(this->get_logger(), "当前高度: %.2f m, 目标高度: %.2f m", current_z, target_z);
  if (current_z >= target_z - 0.15) {
    current_state_ = DroneState::HOVER_WAIT;
    state_start_time_ = this->now();
    RCLCPP_INFO(this->get_logger(), "状态转换: TAKEOFF -> HOVER_WAIT");
  }
}

void SimpleFSM::handleHoverWaitState()
{
  if (cmd_received_ && (this->now() - last_cmd_time_).seconds() < cmd_timeout_) {
    current_state_ = DroneState::CMD_CONTROL;
    state_start_time_ = this->now();
    RCLCPP_INFO(this->get_logger(), "状态转换: HOVER_WAIT -> CMD_CONTROL");
  }
}

void SimpleFSM::handleCmdControlState()
{
  // cmd_received_ 为 false 时 last_cmd_time_ 从未被赋值（默认构造，时钟源与
  // this->now() 不同），直接相减会抛出 "different time sources" 异常导致节点崩溃。
  // ROTATING 转完后是立即切入 CMD_CONTROL 的，此时 ego_planner 可能还没发出第一帧
  // pos_cmd，必须先判空再做时间比较。
  if (cmd_received_ && (this->now() - last_cmd_time_).seconds() > cmd_timeout_) {
    cmd_received_ = false;
    current_state_ = DroneState::HOVER_WAIT;
    state_start_time_ = this->now();
    RCLCPP_INFO(this->get_logger(), "状态转换: CMD_CONTROL -> HOVER_WAIT (指令超时)");
    return;
  }
}

void SimpleFSM::handleErrorState()
{
  RCLCPP_ERROR(this->get_logger(), "错误状态，尝试恢复...");
  publishCurrentPosition();

  double elapsed = (this->now() - state_start_time_).seconds();
  if (elapsed > 10.0) {
    current_state_ = DroneState::INIT;
    state_start_time_ = this->now();
    RCLCPP_INFO(this->get_logger(), "状态转换: ERROR -> INIT");
    offboard_engaged_ = false;
    is_armed_ = false;
  }
}

// 
// 控制循环
// 

void SimpleFSM::controlLoop()
{
  switch (current_state_) {
    case DroneState::PREFLIGHT:   break;
    case DroneState::TAKEOFF:     publishTakeoffPosition();  break;
    case DroneState::HOVER_WAIT:  publishHoverPosition();    break;
    case DroneState::ROTATING:    publishRotatingPosition(); break;
    case DroneState::CMD_CONTROL:
      if (cmd_received_) publishCommandPosition();
      else                publishHoverPosition();
      break;
    case DroneState::INIT:
    case DroneState::ERROR:
      publishCurrentPosition();
      break;
  }
}

// 
// PositionTarget 发布（MAVROS setpoint_raw/local）
// 

void SimpleFSM::publishCurrentPosition()
{
  auto target = mavros_msgs::msg::PositionTarget();
  target.header.stamp = this->now();
  target.header.frame_id = "map";
  target.coordinate_frame = mavros_msgs::msg::PositionTarget::FRAME_LOCAL_NED;

  target.type_mask =
      mavros_msgs::msg::PositionTarget::IGNORE_VX |
      mavros_msgs::msg::PositionTarget::IGNORE_VY |
      mavros_msgs::msg::PositionTarget::IGNORE_VZ |
      mavros_msgs::msg::PositionTarget::IGNORE_AFX |
      mavros_msgs::msg::PositionTarget::IGNORE_AFY |
      mavros_msgs::msg::PositionTarget::IGNORE_AFZ |
      mavros_msgs::msg::PositionTarget::IGNORE_YAW_RATE;

  target.position.x = hover_position_.x();
  target.position.y = hover_position_.y();
  target.position.z = hover_position_.z();
  target.yaw = odom_received_ ? getCurrentYaw() : 0.0;

  position_pub_->publish(target);
}

void SimpleFSM::publishHoverPosition()
{
  auto target = mavros_msgs::msg::PositionTarget();
  target.header.stamp = this->now();
  target.header.frame_id = "map";
  target.coordinate_frame = mavros_msgs::msg::PositionTarget::FRAME_LOCAL_NED;

  target.type_mask =
      mavros_msgs::msg::PositionTarget::IGNORE_VX |
      mavros_msgs::msg::PositionTarget::IGNORE_VY |
      mavros_msgs::msg::PositionTarget::IGNORE_VZ |
      mavros_msgs::msg::PositionTarget::IGNORE_AFX |
      mavros_msgs::msg::PositionTarget::IGNORE_AFY |
      mavros_msgs::msg::PositionTarget::IGNORE_AFZ |
      mavros_msgs::msg::PositionTarget::IGNORE_YAW_RATE;

  target.position.x = hover_position_.x();
  target.position.y = hover_position_.y();
  target.position.z = hover_position_.z();   // 使用当前高度，不再硬编码
  // 保持当前真实 yaw，避免 ROTATING 转完后、ego_planner 第一帧 pos_cmd
  // 到达前的空档把 yaw 拉回 0 造成瞬间跳变
  target.yaw = odom_received_ ? getCurrentYaw() : 0.0;

  position_pub_->publish(target);
}

void SimpleFSM::publishTakeoffPosition()
{
  auto target = mavros_msgs::msg::PositionTarget();
  target.header.stamp = this->now();
  target.header.frame_id = "map";
  target.coordinate_frame = mavros_msgs::msg::PositionTarget::FRAME_LOCAL_NED;

  target.type_mask =
      mavros_msgs::msg::PositionTarget::IGNORE_VX |
      mavros_msgs::msg::PositionTarget::IGNORE_VY |
      mavros_msgs::msg::PositionTarget::IGNORE_VZ |
      mavros_msgs::msg::PositionTarget::IGNORE_AFX |
      mavros_msgs::msg::PositionTarget::IGNORE_AFY |
      mavros_msgs::msg::PositionTarget::IGNORE_AFZ |
      mavros_msgs::msg::PositionTarget::IGNORE_YAW_RATE;

  target.position.x = takeoff_start_position_.x();
  target.position.y = takeoff_start_position_.y();
  target.position.z = hover_position_.z();   // 使用参数化高度
  target.yaw = 0.0;

  position_pub_->publish(target);
}

void SimpleFSM::publishCommandPosition()
{
  auto target = mavros_msgs::msg::PositionTarget();
  target.header.stamp = this->now();
  target.header.frame_id = "map";
  target.coordinate_frame = mavros_msgs::msg::PositionTarget::FRAME_LOCAL_NED;

  // 位置 + 速度 + 加速度 + 偏航，只忽略偏航角速率
  // 如加速度前馈导致抖动，可加回 IGNORE_AFX | IGNORE_AFY | IGNORE_AFZ
  target.type_mask =
      mavros_msgs::msg::PositionTarget::IGNORE_YAW_RATE;

  target.position.x = last_cmd_.position.x;
  target.position.y = last_cmd_.position.y;
  target.position.z = last_cmd_.position.z;

  target.velocity.x = last_cmd_.velocity.x * velocity_scale_;
  target.velocity.y = last_cmd_.velocity.y * velocity_scale_;
  target.velocity.z = last_cmd_.velocity.z * velocity_scale_;

  target.acceleration_or_force.x = last_cmd_.acceleration.x * acceleration_scale_;
  target.acceleration_or_force.y = last_cmd_.acceleration.y * acceleration_scale_;
  target.acceleration_or_force.z = last_cmd_.acceleration.z * acceleration_scale_;

  double yaw = getYawFromCommand(last_cmd_);
  target.yaw = yaw;

  position_pub_->publish(target);
}

void SimpleFSM::publishAttitudeControl(double roll, double pitch, double yaw, double thrust)
{
  (void)roll;
  (void)pitch;

  auto att_msg = mavros_msgs::msg::AttitudeTarget();
  att_msg.header.stamp = this->now();
  att_msg.header.frame_id = "base_link";
  att_msg.type_mask = 7;

  att_msg.orientation.w = cos(yaw / 2);
  att_msg.orientation.x = 0;
  att_msg.orientation.y = 0;
  att_msg.orientation.z = sin(yaw / 2);
  att_msg.thrust = thrust;

  attitude_pub_->publish(att_msg);
}

// 
// MAVROS 服务调用
// 

bool SimpleFSM::setOffboardMode()
{
  if (current_fcu_state_.mode == "OFFBOARD") {
    offboard_engaged_ = true;
    return true;
  }
  for (int i = 0; i < 10; ++i) {
    publishCurrentPosition();
    rclcpp::sleep_for(50ms);
  }
  auto request = std::make_shared<mavros_msgs::srv::SetMode::Request>();
  request->custom_mode = "OFFBOARD";
  set_mode_client_->async_send_request(request);
  offboard_engaged_ = true;
  return true;
}

bool SimpleFSM::armDrone(bool arm)
{
  for (int i = 0; i < 10; ++i) {
    publishCurrentPosition();
    rclcpp::sleep_for(50ms);
  }
  auto request = std::make_shared<mavros_msgs::srv::CommandBool::Request>();
  request->value = arm;
  arming_client_->async_send_request(request);
  is_armed_ = true;
  return true;
}

// 
// 工具
// 

std::string SimpleFSM::stateToString(DroneState state)
{
  switch (state) {
    case DroneState::INIT:        return "INIT";
    case DroneState::PREFLIGHT:   return "PREFLIGHT";
    case DroneState::TAKEOFF:     return "TAKEOFF";
    case DroneState::HOVER_WAIT:  return "HOVER_WAIT";
    case DroneState::ROTATING:    return "ROTATING";
    case DroneState::CMD_CONTROL: return "CMD_CONTROL";
    case DroneState::ERROR:       return "ERROR";
    default:                      return "UNKNOWN";
  }
}

Vector3d SimpleFSM::toEigen(const geometry_msgs::msg::Point& point)
{
  return Vector3d(point.x, point.y, point.z);
}

double SimpleFSM::getCurrentYaw() const
{
  return atan2(
      2.0 * (current_orientation_.w() * current_orientation_.z() +
             current_orientation_.x() * current_orientation_.y()),
      1.0 - 2.0 * (current_orientation_.y() * current_orientation_.y() +
                   current_orientation_.z() * current_orientation_.z())
  );
}

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

// 
// Goal Interception and Rotation
// 

void SimpleFSM::goalInterceptCallback(const geometry_msgs::msg::PoseStamped::SharedPtr msg)
{
  if (!odom_received_) {
    RCLCPP_WARN(this->get_logger(), "No odometry, ignore goal");
    return;
  }

  Eigen::Vector3d goal_pos(msg->pose.position.x, msg->pose.position.y, msg->pose.position.z);
  Eigen::Vector3d dir_to_goal = goal_pos - current_position_;
  dir_to_goal(2) = 0.0;

  double distance = (goal_pos - current_position_).norm();

  // 计算目标方向（世界坐标系）
  double goal_direction_world = atan2(dir_to_goal(1), dir_to_goal(0));

  // 提取当前 yaw
  double current_yaw = atan2(
      2.0 * (current_orientation_.w() * current_orientation_.z() +
             current_orientation_.x() * current_orientation_.y()),
      1.0 - 2.0 * (current_orientation_.y() * current_orientation_.y() +
                   current_orientation_.z() * current_orientation_.z())
  );

  // 计算相对角度
  double relative_angle = goal_direction_world - current_yaw;

  // 归一化到 [-π, π]
  while (relative_angle > M_PI) relative_angle -= 2 * M_PI;
  while (relative_angle < -M_PI) relative_angle += 2 * M_PI;

  double relative_angle_deg = relative_angle * 180.0 / M_PI;

  // 判断目标在后方：|relative_angle| > 90°
  bool goal_behind = (fabs(relative_angle) > M_PI / 2);

  RCLCPP_INFO(this->get_logger(),
      "Goal intercept: dist=%.2fm, relative_angle=%.1f° → %s",
      distance, relative_angle_deg,
      goal_behind ? "REAR (rotate first)" : "FRONT (direct)");

  if (goal_behind) {
    // 进入 ROTATING 状态
    pending_goal_ = goal_pos;
    pending_goal_yaw_ = goal_direction_world;
    pending_goal_valid_ = true;
    rotation_start_time_ = this->now();
    current_state_ = DroneState::ROTATING;
    state_start_time_ = this->now();

    RCLCPP_INFO(this->get_logger(),
        "Target in rear hemisphere, rotating to yaw=%.1f°",
        pending_goal_yaw_ * 180.0 / M_PI);
  } else {
    // 前方目标，直接转发给 ego_planner
    goal_relay_pub_->publish(*msg);
  }
}

void SimpleFSM::handleRotatingState()
{
  if (!odom_received_ || !pending_goal_valid_) {
    RCLCPP_ERROR(this->get_logger(), "ROTATING: invalid state");
    current_state_ = DroneState::ERROR;
    state_start_time_ = this->now();
    return;
  }

  // 计算当前 yaw 与目标 yaw 的差值
  double current_yaw = atan2(
      2.0 * (current_orientation_.w() * current_orientation_.z() +
             current_orientation_.x() * current_orientation_.y()),
      1.0 - 2.0 * (current_orientation_.y() * current_orientation_.y() +
                   current_orientation_.z() * current_orientation_.z())
  );

  double yaw_diff = pending_goal_yaw_ - current_yaw;
  while (yaw_diff > M_PI) yaw_diff -= 2 * M_PI;
  while (yaw_diff < -M_PI) yaw_diff += 2 * M_PI;

  double yaw_diff_deg = fabs(yaw_diff) * 180.0 / M_PI;

  // 检查旋转是否完成
  if (yaw_diff_deg < rotation_complete_threshold_deg_) {
    double elapsed = (this->now() - rotation_start_time_).seconds();

    if (elapsed > rotation_settle_time_) {
      // 旋转完成，转发 goal
      auto goal_msg = geometry_msgs::msg::PoseStamped();
      goal_msg.header.stamp = this->now();
      goal_msg.header.frame_id = "map";
      goal_msg.pose.position.x = pending_goal_(0);
      goal_msg.pose.position.y = pending_goal_(1);
      goal_msg.pose.position.z = pending_goal_(2);
      goal_msg.pose.orientation.w = 1.0;
      RCLCPP_INFO(this->get_logger(),
          "Rotation complete, forwarding goal to ego_planner");
      
      goal_relay_pub_->publish(goal_msg);

      

      pending_goal_valid_ = false;
      current_state_ = DroneState::CMD_CONTROL;
      state_start_time_ = this->now();
    }
  } else {
    // 继续旋转
    double elapsed = (this->now() - rotation_start_time_).seconds();

    if (elapsed > rotation_timeout_) {
      RCLCPP_ERROR(this->get_logger(),
          "Rotation timeout (%.1fs), yaw_diff=%.1f°",
          elapsed, yaw_diff_deg);
      pending_goal_valid_ = false;
      current_state_ = DroneState::HOVER_WAIT;
      state_start_time_ = this->now();
    }
  }
}

void SimpleFSM::publishRotatingPosition()
{
  auto target = mavros_msgs::msg::PositionTarget();
  target.header.stamp = this->now();
  target.header.frame_id = "map";
  target.coordinate_frame = mavros_msgs::msg::PositionTarget::FRAME_LOCAL_NED;

  target.type_mask =
      mavros_msgs::msg::PositionTarget::IGNORE_VX |
      mavros_msgs::msg::PositionTarget::IGNORE_VY |
      mavros_msgs::msg::PositionTarget::IGNORE_VZ |
      mavros_msgs::msg::PositionTarget::IGNORE_AFX |
      mavros_msgs::msg::PositionTarget::IGNORE_AFY |
      mavros_msgs::msg::PositionTarget::IGNORE_AFZ |
      mavros_msgs::msg::PositionTarget::IGNORE_YAW_RATE;

  // 保持当前位置
  target.position.x = current_position_(0);
  target.position.y = current_position_(1);
  target.position.z = current_position_(2);

  // 设置目标 yaw
  target.yaw = pending_goal_yaw_;

  position_pub_->publish(target);
}

}  // namespace egoplanner_fsm