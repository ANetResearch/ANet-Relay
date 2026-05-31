#ifndef YYDRONE_MISSION__MISSION_DISPATCHER_HPP_
#define YYDRONE_MISSION__MISSION_DISPATCHER_HPP_

#include "yydrone_mission/mission_interface.hpp"

#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/string.hpp>
#include <memory>
#include <vector>
#include <string>

namespace yydrone_mission
{

/**
 * @brief 任务调度器
 *
 * 管理所有注册的任务，选择并运行一个任务。
 *
 * 新任务注册方式：
 *   在 mission_dispatcher.cpp 的 registerMissions() 中
 *   添加一行: missions_.push_back(std::make_shared<YourNewMission>());
 *
 * 启动时通过参数选择任务：
 *   mission_name: 任务名（如 "takeoff_and_forward"），默认第一个注册的任务
 */
class MissionDispatcher : public rclcpp::Node
{
public:
  explicit MissionDispatcher(const rclcpp::NodeOptions & options = rclcpp::NodeOptions());

private:
  void registerMissions();
  void selectMission();
  void timerCallback();
  void heartbeatCallback();

  // 所有可用任务
  std::vector<std::shared_ptr<MissionInterface>> missions_;

  // 当前运行的任务
  std::shared_ptr<MissionInterface> active_mission_;
  bool mission_running_;

  // 定时器
  rclcpp::TimerBase::SharedPtr main_timer_;
  rclcpp::TimerBase::SharedPtr heartbeat_timer_;

  // 状态发布
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr state_pub_;
};

}  // namespace yydrone_mission

#endif  // YYDRONE_MISSION__MISSION_DISPATCHER_HPP_
