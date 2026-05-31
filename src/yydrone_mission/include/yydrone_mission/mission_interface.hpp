#ifndef YYDRONE_MISSION__MISSION_INTERFACE_HPP_
#define YYDRONE_MISSION__MISSION_INTERFACE_HPP_

#include <rclcpp/rclcpp.hpp>
#include <string>

namespace yydrone_mission
{

/**
 * @brief 任务基类接口
 *
 * 每个具体任务（如"起飞1m+前飞2m"）继承此接口，
 * 由 mission_dispatcher 统一调度。
 *
 * 生命周期：
 *   initialize() → start() → update() 循环 → isComplete() → 结束
 *  任何时候可调用 abort()
 */
class MissionInterface
{
public:
  virtual ~MissionInterface() = default;

  /// 任务名（用于日志和调度器识别）
  virtual std::string name() const = 0;

  /// 初始化（订阅 topic、读取参数等）
  /// @return true if OK
  virtual bool initialize(rclcpp::Node * node) = 0;

  /// 启动任务
  virtual void start() = 0;

  /// 每周期调用（由 dispatcher 的 timer 驱动）
  virtual void update() = 0;

  /// 中止任务
  virtual void abort() = 0;

  /// 任务是否已完成
  virtual bool isComplete() const = 0;

  /// 获取当前状态描述（用于日志）
  virtual std::string statusString() const = 0;
};

}  // namespace yydrone_mission

#endif  // YYDRONE_MISSION__MISSION_INTERFACE_HPP_
