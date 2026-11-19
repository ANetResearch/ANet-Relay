#ifndef YYDRONE_MISSION__MODE_SWITCHER_HPP_
#define YYDRONE_MISSION__MODE_SWITCHER_HPP_

#include <rclcpp/rclcpp.hpp>
#include <mavros_msgs/msg/state.hpp>
#include <mavros_msgs/srv/command_bool.hpp>
#include <mavros_msgs/srv/set_mode.hpp>
#include <string>
#include <chrono>

namespace yydrone_mission
{

/**
 * @brief 飞行模式切换工具
 *
 * 封装 mavros 的 SetMode 和 CommandBool 服务调用，
 * 用于切换到 OFFBOARD 模式并解锁无人机。
 *
 * 用法：
 *   ModeSwitcher ms(node);
 *   ms.switchToOffboard();
 *   ms.arm();
 *   if (ms.isReady()) { ... }
 */
class ModeSwitcher
{
public:
  explicit ModeSwitcher(rclcpp::Node * node);

  /// 尝试切换到 OFFBOARD 模式，返回是否成功
  bool switchToOffboard();

  /// 尝试解锁 / 上锁，返回是否成功
  bool arm(bool arm = true);

  /// 检查是否已解锁且在 OFFBOARD 模式
  bool isReady() const;

  /// 更新飞控状态（由外部 odom/state callback 调用）
  void updateFcuState(const mavros_msgs::msg::State & state);

  /// 获取当前模式名
  std::string getMode() const { return current_mode_; }

  /// 是否已解锁
  bool isArmed() const { return armed_; }

  /// 飞控是否已连接
  bool isConnected() const { return connected_; }

private:
  rclcpp::Node * node_;
  rclcpp::Client<mavros_msgs::srv::SetMode>::SharedPtr set_mode_client_;
  rclcpp::Client<mavros_msgs::srv::CommandBool>::SharedPtr arming_client_;
  bool services_ready_;

  std::string current_mode_;
  bool armed_;
  bool connected_;
};

}  // namespace yydrone_mission

#endif  // YYDRONE_MISSION__MODE_SWITCHER_HPP_
