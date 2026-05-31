#ifndef YYDRONE_MISSION__MISSION_TAKEOFF_AND_FORWARD_HPP_
#define YYDRONE_MISSION__MISSION_TAKEOFF_AND_FORWARD_HPP_

#include "yydrone_mission/mission_interface.hpp"
#include "yydrone_mission/takeoff_handler.hpp"

#include <geometry_msgs/msg/pose_stamped.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <std_msgs/msg/string.hpp>
#include <tf2/LinearMath/Quaternion.h>
#include <tf2/LinearMath/Matrix3x3.h>
#include <string>
#include <cmath>

namespace yydrone_mission
{

/**
 * @brief 任务：起飞 1m → 前飞 2m
 *
 * 流程：
 *   1. 等待 FSM 完成起飞（进入 HOVER_WAIT）
 *   2. 向 ego-planner 发送前方 2m 目标点
 *   3. 监控直到到达目标
 *
 * 可配置参数：
 *   - takeoff_height:   起飞高度（米），默认 1.0
 *   - forward_distance:  前飞距离（米），默认 2.0
 *   - direction_deg:     飞行方向（度），0=正前 90=右 -90=左 180=后方，默认 0
 *   - reach_threshold:   到达阈值（米），默认 0.3
 *   - goal_timeout:      超时（秒），默认 30
 *
 * 坐标系：ENU (X=东 Y=北 Z=上)，前飞方向 = 机头 yaw + direction_deg
 */
class MissionTakeoffAndForward : public MissionInterface
{
public:
  MissionTakeoffAndForward();

  // ── MissionInterface 实现 ──────────────────────────────────
  std::string name() const override { return "takeoff_and_forward"; }
  bool initialize(rclcpp::Node * node) override;
  void start() override;
  void update() override;
  void abort() override;
  bool isComplete() const override { return complete_; }
  std::string statusString() const override;

private:
  // 内部状态
  enum class Phase {
    WAIT_READY,       // 等待就绪
    WAIT_TAKEOFF,     // 等待起飞完成
    SEND_GOAL,        // 发送目标点
    FLYING,           // 飞行中
    ARRIVED,          // 到达
    ABORTED           // 中止
  };

  rclcpp::Node * node_;
  Phase phase_;
  bool complete_;

  // 参数
  double takeoff_height_;
  double forward_distance_;
  double direction_deg_;
  double reach_threshold_;
  double goal_timeout_;

  // 状态
  double current_yaw_;
  double current_x_, current_y_, current_z_;
  double goal_x_, goal_y_, goal_z_;
  std::string fsm_state_;
  bool odom_received_;

  // 时间
  rclcpp::Time phase_start_;
  rclcpp::Time goal_sent_time_;

  // 工具
  TakeoffHandler takeoff_handler_;

  // 订阅器
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr odom_sub_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr fsm_state_sub_;

  // 发布器（ego-planner 目标点）
  rclcpp::Publisher<geometry_msgs::msg::PoseStamped>::SharedPtr goal_pub_;

  // 回调
  void odomCb(const nav_msgs::msg::Odometry::SharedPtr msg);
  void fsmCb(const std_msgs::msg::String::SharedPtr msg);

  // 工具
  void sendGoal(double x, double y, double z, double yaw);
  double distanceToGoal() const;
  static std::string phaseName(Phase p);
};

}  // namespace yydrone_mission

#endif  // YYDRONE_MISSION__MISSION_TAKEOFF_AND_FORWARD_HPP_
