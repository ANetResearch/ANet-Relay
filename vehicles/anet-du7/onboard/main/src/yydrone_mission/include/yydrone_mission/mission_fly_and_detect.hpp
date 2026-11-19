#ifndef YYDRONE_MISSION__MISSION_FLY_AND_DETECT_HPP_
#define YYDRONE_MISSION__MISSION_FLY_AND_DETECT_HPP_

#include "yydrone_mission/mission_interface.hpp"
#include "yydrone_mission/takeoff_handler.hpp"

#include <geometry_msgs/msg/pose_stamped.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <std_msgs/msg/string.hpp>
#include <tf2/LinearMath/Quaternion.h>
#include <tf2/LinearMath/Matrix3x3.h>
#include <string>
#include <cmath>
#include <vector>

namespace yydrone_mission
{

/**
 * @brief 任务：起飞 → 前飞 2m → 悬停检测 AR 码
 *
 * 流程：
 *   1. 等待 FSM 完成起飞
 *   2. 向 ego-planner 发送前方目标点，飞行到达
 *   3. 飞行完成后进入检测阶段：订阅 aruco_pose 话题
 *   4. 检测到 AR 码后记录 ID + 位姿，超时后结束
 *
 * 确保飞行和检测严格顺序执行（先飞到，再检测）
 */
class MissionFlyAndDetect : public MissionInterface
{
public:
  MissionFlyAndDetect();

  // ── MissionInterface 实现 ──────────────────────────────────
  std::string name() const override { return "fly_and_detect"; }
  bool initialize(rclcpp::Node * node) override;
  void start() override;
  void update() override;
  void abort() override;
  bool isComplete() const override { return complete_; }
  std::string statusString() const override;

private:
  enum class Phase {
    WAIT_READY,
    WAIT_TAKEOFF,
    SEND_GOAL,
    FLYING,
    HOVER_DETECT,    // ★ 悬停 + AR 码检测
    COMPLETE_DONE,
    ABORTED
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
  double detect_duration_;     // 检测持续时间（秒）

  // 状态
  double current_yaw_;
  double current_x_, current_y_, current_z_;
  double goal_x_, goal_y_, goal_z_;
  std::string fsm_state_;
  bool odom_received_;

  // AR 检测
  struct DetectedMarker {
    int id;
    double x, y, z;
  };
  std::vector<DetectedMarker> detected_markers_;
  bool marker_found_;

  rclcpp::Time phase_start_;
  rclcpp::Time goal_sent_time_;
  rclcpp::Time detect_start_time_;

  // 工具
  TakeoffHandler takeoff_handler_;

  // 订阅器
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr odom_sub_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr fsm_state_sub_;
  rclcpp::Subscription<geometry_msgs::msg::PoseStamped>::SharedPtr aruco_pose_sub_;

  // 发布器
  rclcpp::Publisher<geometry_msgs::msg::PoseStamped>::SharedPtr goal_pub_;

  // 回调
  void odomCb(const nav_msgs::msg::Odometry::SharedPtr msg);
  void fsmCb(const std_msgs::msg::String::SharedPtr msg);
  void arucoPoseCb(const geometry_msgs::msg::PoseStamped::SharedPtr msg);

  // 工具
  void sendGoal(double x, double y, double z, double yaw);
  double distanceToGoal() const;
  static std::string phaseName(Phase p);
};

}  // namespace yydrone_mission

#endif  // YYDRONE_MISSION__MISSION_FLY_AND_DETECT_HPP_
