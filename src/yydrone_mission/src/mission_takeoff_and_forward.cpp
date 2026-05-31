#include "yydrone_mission/mission_takeoff_and_forward.hpp"

using namespace std::chrono_literals;

namespace yydrone_mission
{

MissionTakeoffAndForward::MissionTakeoffAndForward()
  : node_(nullptr),
    phase_(Phase::WAIT_READY),
    complete_(false),
    takeoff_height_(1.0),
    forward_distance_(2.0),
    direction_deg_(0.0),
    reach_threshold_(0.3),
    goal_timeout_(30.0),
    current_yaw_(0.0),
    current_x_(0.0), current_y_(0.0), current_z_(0.0),
    goal_x_(0.0), goal_y_(0.0), goal_z_(0.0),
    odom_received_(false),
    takeoff_handler_(nullptr, TakeoffHandler::Mode::PASSIVE)
{
}

bool MissionTakeoffAndForward::initialize(rclcpp::Node * node)
{
  node_ = node;

  // ── 读取参数 ──────────────────────────────────────────────────
  node_->declare_parameter("takeoff_height", 1.0);
  node_->declare_parameter("forward_distance", 2.0);
  node_->declare_parameter("direction_deg", 0.0);
  node_->declare_parameter("reach_threshold", 0.3);
  node_->declare_parameter("goal_timeout", 30.0);
  node_->declare_parameter("drone_id", 0);

  takeoff_height_   = node_->get_parameter("takeoff_height").as_double();
  forward_distance_ = node_->get_parameter("forward_distance").as_double();
  direction_deg_    = node_->get_parameter("direction_deg").as_double();
  reach_threshold_  = node_->get_parameter("reach_threshold").as_double();
  goal_timeout_     = node_->get_parameter("goal_timeout").as_double();
  int drone_id      = node_->get_parameter("drone_id").as_int();

  // ── 起飞处理器（被动模式：等 FSM 完成起飞） ─────────────────
  takeoff_handler_ = TakeoffHandler(node_, TakeoffHandler::Mode::PASSIVE);
  takeoff_handler_.setTargetHeight(takeoff_height_);

  // ── 订阅器 ────────────────────────────────────────────────────
  rclcpp::QoS qos(10);
  qos.reliability(rclcpp::ReliabilityPolicy::BestEffort);

  odom_sub_ = node_->create_subscription<nav_msgs::msg::Odometry>(
    "/mavros/local_position/odom", qos,
    std::bind(&MissionTakeoffAndForward::odomCb, this, std::placeholders::_1));

  fsm_state_sub_ = node_->create_subscription<std_msgs::msg::String>(
    "/drone_state", qos,
    std::bind(&MissionTakeoffAndForward::fsmCb, this, std::placeholders::_1));

  // ── 发布器（ego-planner 目标点） ─────────────────────────────
  std::string goal_topic = "/drone_" + std::to_string(drone_id) + "_plan_vis/goal_point";
  goal_pub_ = node_->create_publisher<geometry_msgs::msg::PoseStamped>(goal_topic, 10);

  RCLCPP_INFO(node_->get_logger(),
    "[%s] 初始化完成 | 起飞%.1fm → 前飞%.1fm (方向%+.0f°) | 目标话题: %s",
    name().c_str(), takeoff_height_, forward_distance_, direction_deg_, goal_topic.c_str());

  phase_start_ = node_->now();
  return true;
}

void MissionTakeoffAndForward::start()
{
  RCLCPP_INFO(node_->get_logger(), "[%s] >>> 任务启动 <<<", name().c_str());
  phase_ = Phase::WAIT_TAKEOFF;
  phase_start_ = node_->now();
  complete_ = false;

  takeoff_handler_.start();
}

void MissionTakeoffAndForward::update()
{
  if (complete_) return;

  switch (phase_) {
    case Phase::WAIT_READY:
      break;  // 等待 start() 被调用

    case Phase::WAIT_TAKEOFF: {
      takeoff_handler_.updateFsmState(fsm_state_);
      auto s = takeoff_handler_.update();

      if (s == TakeoffHandler::State::COMPLETE) {
        RCLCPP_INFO(node_->get_logger(),
          "[%s] 起飞完成 | 高度 %.2fm | 机头朝向 %.0f°",
          name().c_str(), current_z_, current_yaw_ * 180.0 / M_PI);
        phase_ = Phase::SEND_GOAL;
        phase_start_ = node_->now();
      } else if (s == TakeoffHandler::State::TIMEOUT) {
        RCLCPP_ERROR(node_->get_logger(), "[%s] 起飞超时！", name().c_str());
        phase_ = Phase::ABORTED;
        complete_ = true;
      }
      break;
    }

    case Phase::SEND_GOAL: {
      // 计算机头前方目标点（ENU 坐标系）
      double rad = current_yaw_ + direction_deg_ * M_PI / 180.0;
      goal_x_ = current_x_ + forward_distance_ * cos(rad);
      goal_y_ = current_y_ + forward_distance_ * sin(rad);
      goal_z_ = takeoff_height_;

      sendGoal(goal_x_, goal_y_, goal_z_, 0.0);
      goal_sent_time_ = node_->now();

      RCLCPP_INFO(node_->get_logger(),
        "[%s] 目标已发送 | (%.2f, %.2f, %.2f) | 等待 ego-planner 规划...",
        name().c_str(), goal_x_, goal_y_, goal_z_);

      phase_ = Phase::FLYING;
      phase_start_ = node_->now();
      break;
    }

    case Phase::FLYING: {
      double dist = distanceToGoal();

      if (dist < reach_threshold_) {
        RCLCPP_INFO(node_->get_logger(),
          "[%s] 到达目标！距离 %.2fm < 阈值 %.2fm",
          name().c_str(), dist, reach_threshold_);
        phase_ = Phase::ARRIVED;
        complete_ = true;
        return;
      }

      double elapsed = (node_->now() - goal_sent_time_).seconds();
      if (elapsed > goal_timeout_) {
        RCLCPP_WARN(node_->get_logger(),
          "[%s] 飞行超时 (%.1fs) | 剩余距离 %.2fm",
          name().c_str(), elapsed, dist);
        phase_ = Phase::ARRIVED;  // 超时也算到，不停在原地
        complete_ = true;
        return;
      }

      // 每秒报一次进度
      RCLCPP_INFO_THROTTLE(node_->get_logger(), *node_->get_clock(), 2000,
        "[%s] 飞行中... | 剩余 %.2fm | FSM: %s",
        name().c_str(), dist, fsm_state_.c_str());
      break;
    }

    case Phase::ARRIVED:
    case Phase::ABORTED:
      complete_ = true;
      break;
  }
}

void MissionTakeoffAndForward::abort()
{
  RCLCPP_WARN(node_->get_logger(), "[%s] 任务中止！", name().c_str());
  phase_ = Phase::ABORTED;
  complete_ = true;
}

std::string MissionTakeoffAndForward::statusString() const
{
  char buf[128];
  snprintf(buf, sizeof(buf), "[%s] phase=%s pos=(%.1f,%.1f,%.1f) yaw=%.0f° complete=%d",
    name().c_str(), phaseName(phase_).c_str(),
    current_x_, current_y_, current_z_,
    current_yaw_ * 180.0 / M_PI, complete_);
  return buf;
}

// ═══════════════════════════════════════════════════════════════════
// 回调
// ═══════════════════════════════════════════════════════════════════

void MissionTakeoffAndForward::odomCb(const nav_msgs::msg::Odometry::SharedPtr msg)
{
  current_x_ = msg->pose.pose.position.x;
  current_y_ = msg->pose.pose.position.y;
  current_z_ = msg->pose.pose.position.z;

  // 提取 yaw
  tf2::Quaternion q(
    msg->pose.pose.orientation.x,
    msg->pose.pose.orientation.y,
    msg->pose.pose.orientation.z,
    msg->pose.pose.orientation.w);
  tf2::Matrix3x3 m(q);
  double roll, pitch;
  m.getRPY(roll, pitch, current_yaw_);

  takeoff_handler_.updateOdom(*msg);
  odom_received_ = true;
}

void MissionTakeoffAndForward::fsmCb(const std_msgs::msg::String::SharedPtr msg)
{
  fsm_state_ = msg->data;
  takeoff_handler_.updateFsmState(fsm_state_);
}

// ═══════════════════════════════════════════════════════════════════
// 工具
// ═══════════════════════════════════════════════════════════════════

void MissionTakeoffAndForward::sendGoal(double x, double y, double z, double yaw)
{
  auto goal = geometry_msgs::msg::PoseStamped();
  goal.header.stamp = node_->now();
  goal.header.frame_id = "odom";
  goal.pose.position.x = x;
  goal.pose.position.y = y;
  goal.pose.position.z = z;
  goal.pose.orientation.w = cos(yaw / 2.0);
  goal.pose.orientation.x = 0.0;
  goal.pose.orientation.y = 0.0;
  goal.pose.orientation.z = sin(yaw / 2.0);
  goal_pub_->publish(goal);
}

double MissionTakeoffAndForward::distanceToGoal() const
{
  double dx = current_x_ - goal_x_;
  double dy = current_y_ - goal_y_;
  double dz = current_z_ - goal_z_;
  return std::sqrt(dx*dx + dy*dy + dz*dz);
}

std::string MissionTakeoffAndForward::phaseName(Phase p)
{
  switch (p) {
    case Phase::WAIT_READY:   return "WAIT_READY";
    case Phase::WAIT_TAKEOFF: return "WAIT_TAKEOFF";
    case Phase::SEND_GOAL:    return "SEND_GOAL";
    case Phase::FLYING:       return "FLYING";
    case Phase::ARRIVED:      return "ARRIVED";
    case Phase::ABORTED:      return "ABORTED";
    default:                  return "?";
  }
}

}  // namespace yydrone_mission
