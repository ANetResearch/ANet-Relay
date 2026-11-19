#include "yydrone_mission/mission_fly_and_detect.hpp"

using namespace std::chrono_literals;

namespace yydrone_mission
{

MissionFlyAndDetect::MissionFlyAndDetect()
  : node_(nullptr),
    phase_(Phase::WAIT_READY),
    complete_(false),
    takeoff_height_(1.0),
    forward_distance_(2.0),
    direction_deg_(0.0),
    reach_threshold_(0.3),
    goal_timeout_(30.0),
    detect_duration_(10.0),
    current_yaw_(0.0),
    current_x_(0.0), current_y_(0.0), current_z_(0.0),
    goal_x_(0.0), goal_y_(0.0), goal_z_(0.0),
    odom_received_(false),
    marker_found_(false),
    takeoff_handler_(nullptr, TakeoffHandler::Mode::PASSIVE)
{
}

bool MissionFlyAndDetect::initialize(rclcpp::Node * node)
{
  node_ = node;

  // ── 参数 ──────────────────────────────────────────────────────
  node_->declare_parameter("takeoff_height", 1.0);
  node_->declare_parameter("forward_distance", 2.0);
  node_->declare_parameter("direction_deg", 0.0);
  node_->declare_parameter("reach_threshold", 0.3);
  node_->declare_parameter("goal_timeout", 30.0);
  node_->declare_parameter("detect_duration", 15.0);
  node_->declare_parameter("drone_id", 0);

  takeoff_height_   = node_->get_parameter("takeoff_height").as_double();
  forward_distance_ = node_->get_parameter("forward_distance").as_double();
  direction_deg_    = node_->get_parameter("direction_deg").as_double();
  reach_threshold_  = node_->get_parameter("reach_threshold").as_double();
  goal_timeout_     = node_->get_parameter("goal_timeout").as_double();
  detect_duration_  = node_->get_parameter("detect_duration").as_double();
  int drone_id      = node_->get_parameter("drone_id").as_int();

  // ── 起飞处理器 ────────────────────────────────────────────────
  takeoff_handler_ = TakeoffHandler(node_, TakeoffHandler::Mode::PASSIVE);
  takeoff_handler_.setTargetHeight(takeoff_height_);

  // ── 订阅器 ────────────────────────────────────────────────────
  rclcpp::QoS qos(10);
  qos.reliability(rclcpp::ReliabilityPolicy::BestEffort);

  odom_sub_ = node_->create_subscription<nav_msgs::msg::Odometry>(
    "/mavros/local_position/odom", qos,
    std::bind(&MissionFlyAndDetect::odomCb, this, std::placeholders::_1));

  fsm_state_sub_ = node_->create_subscription<std_msgs::msg::String>(
    "/drone_state", qos,
    std::bind(&MissionFlyAndDetect::fsmCb, this, std::placeholders::_1));

  // AR 检测结果订阅
  aruco_pose_sub_ = node_->create_subscription<geometry_msgs::msg::PoseStamped>(
    "/aruco_detector/aruco_pose", 10,
    std::bind(&MissionFlyAndDetect::arucoPoseCb, this, std::placeholders::_1));

  // ── 发布器 ────────────────────────────────────────────────────
  std::string goal_topic = "/move_base_simple/goal";
  goal_pub_ = node_->create_publisher<geometry_msgs::msg::PoseStamped>(goal_topic, 10);

  RCLCPP_INFO(node_->get_logger(),
    "[%s] 初始化完成 | 起飞%.1fm → 前飞%.1fm (方向%+.0f°) → 检测AR码(%zds)",
    name().c_str(), takeoff_height_, forward_distance_, direction_deg_, (size_t)detect_duration_);

  phase_start_ = node_->now();
  return true;
}

void MissionFlyAndDetect::start()
{
  RCLCPP_INFO(node_->get_logger(), "[%s] >>> 任务启动 <<<", name().c_str());
  RCLCPP_INFO(node_->get_logger(), "[%s] 阶段: 起飞 → 前飞%.1fm → 检测AR码", name().c_str(), forward_distance_);
  phase_ = Phase::WAIT_TAKEOFF;
  phase_start_ = node_->now();
  complete_ = false;
  detected_markers_.clear();
  marker_found_ = false;

  takeoff_handler_.start();
}

void MissionFlyAndDetect::update()
{
  if (complete_) return;

  switch (phase_) {

    case Phase::WAIT_READY:
      break;

    // ── 起飞 ──────────────────────────────────────────────────
    case Phase::WAIT_TAKEOFF: {
      takeoff_handler_.updateFsmState(fsm_state_);
      auto s = takeoff_handler_.update();
      if (s == TakeoffHandler::State::COMPLETE) {
        RCLCPP_INFO(node_->get_logger(), "[%s] 起飞完成", name().c_str());
        phase_ = Phase::SEND_GOAL;
        phase_start_ = node_->now();
      }
      break;
    }

    // ── 发送目标点 ────────────────────────────────────────────
    case Phase::SEND_GOAL: {
      double rad = current_yaw_ + direction_deg_ * M_PI / 180.0;
      goal_x_ = current_x_ + forward_distance_ * cos(rad);
      goal_y_ = current_y_ + forward_distance_ * sin(rad);
      goal_z_ = takeoff_height_;

      sendGoal(goal_x_, goal_y_, goal_z_, 0.0);
      goal_sent_time_ = node_->now();

      RCLCPP_INFO(node_->get_logger(),
        "[%s] 前飞 %.1fm → ENU(%.2f, %.2f, %.2f)",
        name().c_str(), forward_distance_, goal_x_, goal_y_, goal_z_);

      phase_ = Phase::FLYING;
      phase_start_ = node_->now();
      break;
    }

    // ── 飞行中 ────────────────────────────────────────────────
    case Phase::FLYING: {
      double dist = distanceToGoal();
      if (dist < reach_threshold_) {
        RCLCPP_INFO(node_->get_logger(),
          "[%s] 到达目标！进入 AR 码检测阶段", name().c_str());
        phase_ = Phase::HOVER_DETECT;
        phase_start_ = node_->now();
        detect_start_time_ = node_->now();
        detected_markers_.clear();
        marker_found_ = false;
        return;
      }

      double elapsed = (node_->now() - goal_sent_time_).seconds();
      if (elapsed > goal_timeout_) {
        RCLCPP_WARN(node_->get_logger(), "[%s] 飞行超时，仍进入检测阶段", name().c_str());
        phase_ = Phase::HOVER_DETECT;
        phase_start_ = node_->now();
        detect_start_time_ = node_->now();
        return;
      }

      RCLCPP_INFO_THROTTLE(node_->get_logger(), *node_->get_clock(), 2000,
        "[%s] 飞行中... 剩余 %.2fm", name().c_str(), dist);
      break;
    }

    // ── ★ 悬停 + AR 码检测 ────────────────────────────────
    case Phase::HOVER_DETECT: {
      double elapsed = (node_->now() - detect_start_time_).seconds();

      if (marker_found_ && !detected_markers_.empty()) {
        // 只需要检测到一个就结束
      }

      if (elapsed > detect_duration_) {
        // 检测超时
        if (detected_markers_.empty()) {
          RCLCPP_WARN(node_->get_logger(),
            "[%s] 检测超时 (%.1fs)，未发现 AR 码", name().c_str(), elapsed);
        } else {
          RCLCPP_INFO(node_->get_logger(),
            "[%s] 检测完成！共发现 %zu 个 AR 码:", name().c_str(), detected_markers_.size());
          for (auto & m : detected_markers_) {
            RCLCPP_INFO(node_->get_logger(),
              "[%s]   └─ id=%d  pos=(%.2f, %.2f, %.2f)",
              name().c_str(), m.id, m.x, m.y, m.z);
          }
        }
        phase_ = Phase::COMPLETE_DONE;
        complete_ = true;
        return;
      }

      RCLCPP_INFO_THROTTLE(node_->get_logger(), *node_->get_clock(), 2000,
        "[%s] 检测中... %.1f/%.1fs | 已发现: %zu 个",
        name().c_str(), elapsed, detect_duration_, detected_markers_.size());
      break;
    }

    case Phase::COMPLETE_DONE:
    case Phase::ABORTED:
      complete_ = true;
      break;
  }
}

void MissionFlyAndDetect::abort()
{
  RCLCPP_WARN(node_->get_logger(), "[%s] 任务中止！", name().c_str());
  phase_ = Phase::ABORTED;
  complete_ = true;
}

std::string MissionFlyAndDetect::statusString() const
{
  char buf[200];
  snprintf(buf, sizeof(buf),
    "[%s] phase=%s pos=(%.1f,%.1f,%.1f) yaw=%.0f° markers=%zu complete=%d",
    name().c_str(), phaseName(phase_).c_str(),
    current_x_, current_y_, current_z_,
    current_yaw_ * 180.0 / M_PI,
    detected_markers_.size(), complete_);
  return buf;
}

// ═══════════════════════════════════════════════════════════════════
// 回调
// ═══════════════════════════════════════════════════════════════════

void MissionFlyAndDetect::odomCb(const nav_msgs::msg::Odometry::SharedPtr msg)
{
  current_x_ = msg->pose.pose.position.x;
  current_y_ = msg->pose.pose.position.y;
  current_z_ = msg->pose.pose.position.z;

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

void MissionFlyAndDetect::fsmCb(const std_msgs::msg::String::SharedPtr msg)
{
  fsm_state_ = msg->data;
  takeoff_handler_.updateFsmState(fsm_state_);
}

void MissionFlyAndDetect::arucoPoseCb(const geometry_msgs::msg::PoseStamped::SharedPtr msg)
{
  // 只在检测阶段处理
  if (phase_ != Phase::HOVER_DETECT) return;

  // 从 frame_id 中提取 marker ID（格式: "aruco_marker_N"）
  int marker_id = -1;
  const std::string & cframe = msg->header.frame_id;
  auto pos = cframe.find("aruco_marker_");
  if (pos != std::string::npos) {
    try {
      marker_id = std::stoi(cframe.substr(pos + 13));
    } catch (...) { marker_id = -1; }
  }

  DetectedMarker dm;
  dm.id = marker_id;
  dm.x = msg->pose.position.x;
  dm.y = msg->pose.position.y;
  dm.z = msg->pose.position.z;

  // 去重
  bool already = false;
  for (auto & m : detected_markers_) {
    if (m.id == dm.id) { already = true; break; }
  }
  if (!already) {
    detected_markers_.push_back(dm);
    marker_found_ = true;
    RCLCPP_INFO(node_->get_logger(),
      "[%s] ★ 检测到 AR 码: id=%d  pos=(%.2f, %.2f, %.2f)",
      name().c_str(), dm.id, dm.x, dm.y, dm.z);
  }
}

// ═══════════════════════════════════════════════════════════════════
// 工具
// ═══════════════════════════════════════════════════════════════════

void MissionFlyAndDetect::sendGoal(double x, double y, double z, double yaw)
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

double MissionFlyAndDetect::distanceToGoal() const
{
  double dx = current_x_ - goal_x_;
  double dy = current_y_ - goal_y_;
  double dz = current_z_ - goal_z_;
  return std::sqrt(dx*dx + dy*dy + dz*dz);
}

std::string MissionFlyAndDetect::phaseName(Phase p)
{
  switch (p) {
    case Phase::WAIT_READY:   return "WAIT_READY";
    case Phase::WAIT_TAKEOFF: return "WAIT_TAKEOFF";
    case Phase::SEND_GOAL:    return "SEND_GOAL";
    case Phase::FLYING:       return "FLYING";
    case Phase::HOVER_DETECT: return "HOVER_DETECT";
    case Phase::COMPLETE_DONE:return "COMPLETE";
    case Phase::ABORTED:      return "ABORTED";
    default:                  return "?";
  }
}

}  // namespace yydrone_mission
