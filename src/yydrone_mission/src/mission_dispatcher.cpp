#include "yydrone_mission/mission_dispatcher.hpp"
#include "yydrone_mission/mission_takeoff_and_forward.hpp"

using namespace std::chrono_literals;

namespace yydrone_mission
{

MissionDispatcher::MissionDispatcher(const rclcpp::NodeOptions & options)
: Node("mission_dispatcher", options),
  mission_running_(false)
{
  this->declare_parameter("mission_name", "takeoff_and_forward");
  this->declare_parameter("auto_start", true);

  // 注册所有任务
  registerMissions();

  // 发布器
  state_pub_ = this->create_publisher<std_msgs::msg::String>("~/state", 10);

  // 定时器
  main_timer_ = this->create_wall_timer(
    200ms, std::bind(&MissionDispatcher::timerCallback, this));
  heartbeat_timer_ = this->create_wall_timer(
    2000ms, std::bind(&MissionDispatcher::heartbeatCallback, this));

  // 选择并启动任务
  selectMission();

  RCLCPP_INFO(this->get_logger(),
    "╔══════════════════════════════════════════════╗");
  RCLCPP_INFO(this->get_logger(),
    "║   yydrone_mission 任务调度器                    ║");
  RCLCPP_INFO(this->get_logger(),
    "║   已注册 %zu 个任务                              ║", missions_.size());
  for (size_t i = 0; i < missions_.size(); ++i) {
    RCLCPP_INFO(this->get_logger(),
      "║     [%zu] %s", i, missions_[i]->name().c_str());
  }
  RCLCPP_INFO(this->get_logger(),
    "║   当前任务: %s", active_mission_ ? active_mission_->name().c_str() : "无");
  RCLCPP_INFO(this->get_logger(),
    "╚══════════════════════════════════════════════╝");
}

// ═══════════════════════════════════════════════════════════════════
// ★ 注册新任务在这里加一行 ★
// ═══════════════════════════════════════════════════════════════════

void MissionDispatcher::registerMissions()
{
  // 任务1：起飞 1m → 前飞 2m
  missions_.push_back(std::make_shared<MissionTakeoffAndForward>());

  // ── 在此添加新任务 ──────────────────────────────────────────
  // 示例：
  // missions_.push_back(std::make_shared<MissionPatrol>());
  // missions_.push_back(std::make_shared<MissionLanding>());
}

// ═══════════════════════════════════════════════════════════════════

void MissionDispatcher::selectMission()
{
  std::string target_name = this->get_parameter("mission_name").as_string();
  bool auto_start = this->get_parameter("auto_start").as_bool();

  for (auto & m : missions_) {
    if (m->name() == target_name) {
      active_mission_ = m;
      break;
    }
  }

  if (!active_mission_ && !missions_.empty()) {
    RCLCPP_WARN(this->get_logger(),
      "未找到任务 '%s'，使用第一个注册任务 '%s'",
      target_name.c_str(), missions_[0]->name().c_str());
    active_mission_ = missions_[0];
  }

  if (!active_mission_) {
    RCLCPP_ERROR(this->get_logger(), "没有可用的任务！");
    return;
  }

  // 初始化任务
  if (!active_mission_->initialize(this)) {
    RCLCPP_ERROR(this->get_logger(), "任务 '%s' 初始化失败！", active_mission_->name().c_str());
    active_mission_.reset();
    return;
  }

  // 自动启动
  if (auto_start) {
    RCLCPP_INFO(this->get_logger(), "自动启动任务 '%s'", active_mission_->name().c_str());
    active_mission_->start();
    mission_running_ = true;
  }
}

void MissionDispatcher::timerCallback()
{
  if (!active_mission_ || !mission_running_) return;

  active_mission_->update();

  if (active_mission_->isComplete()) {
    RCLCPP_INFO(this->get_logger(),
      "══════════════════════════════════════════");
    RCLCPP_INFO(this->get_logger(),
      "  任务 '%s' 完成！", active_mission_->name().c_str());
    RCLCPP_INFO(this->get_logger(),
      "══════════════════════════════════════════");
    mission_running_ = false;
  }
}

void MissionDispatcher::heartbeatCallback()
{
  auto msg = std_msgs::msg::String();
  if (active_mission_ && mission_running_) {
    msg.data = active_mission_->statusString();
  } else if (active_mission_ && active_mission_->isComplete()) {
    msg.data = active_mission_->name() + ": COMPLETE";
  } else {
    msg.data = "NO_ACTIVE_MISSION";
  }
  state_pub_->publish(msg);
}

}  // namespace yydrone_mission

#include <rclcpp_components/register_node_macro.hpp>
RCLCPP_COMPONENTS_REGISTER_NODE(yydrone_mission::MissionDispatcher)
