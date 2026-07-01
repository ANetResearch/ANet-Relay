// fastlio_locate.cpp
// 将 FAST-LIO / FAST-LIO2 的里程计桥接到 PX4 的 /mavros/vision_pose/pose
//
// 支持两种模式：
//   - Passthrough 模式 (enable_tf_lookup=false):
//     直接转发里程计位姿，适用于旧版 fast_lio 或无需全局修正的场景
//   - TF 查询模式 (enable_tf_lookup=true, 默认):
//     通过 tf2 将里程计从 LIO 的局部坐标系(lidar)转到全局坐标系(map)
//     配合 PGO 或 Localizer 使用，获得回环修正后的全局位姿
//
// 数据流：
//   FAST-LIO2 /lio_odom (frame: lidar) ──→ tf2 查询 map→lidar ──→ map 系位姿
//                                                                   │
//                                                    /mavros/vision_pose/pose → PX4 EKF

#include <rclcpp/rclcpp.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <geometry_msgs/msg/transform_stamped.hpp>
#include <tf2_ros/transform_listener.h>
#include <tf2_ros/buffer.h>
#include <tf2/LinearMath/Quaternion.h>
#include <tf2/LinearMath/Matrix3x3.h>
#include <tf2_geometry_msgs/tf2_geometry_msgs.hpp>
#include <string>
#include <memory>
#include <mutex>

class FastLIOToMavros : public rclcpp::Node
{
public:
    FastLIOToMavros() : Node("fastlio_locate"), received_odom_(false)
    {
        // ── 参数声明 ────────────────────────────────────────────────
        this->declare_parameter<std::string>("odom_topic", "/Odom_high_freq");
        this->declare_parameter<std::string>("world_frame", "lidar");
        this->declare_parameter<std::string>("map_frame", "map");
        this->declare_parameter<double>("publish_freq", 100.0);
        this->declare_parameter<bool>("enable_tf_lookup", true);
        this->declare_parameter<bool>("verbose", false);

        this->get_parameter("odom_topic", odom_topic_);
        this->get_parameter("world_frame", world_frame_);
        this->get_parameter("map_frame", map_frame_);
        this->get_parameter("publish_freq", publish_freq_);
        this->get_parameter("enable_tf_lookup", enable_tf_lookup_);
        this->get_parameter("verbose", verbose_);

        // ── QoS ─────────────────────────────────────────────────────
        rclcpp::QoS high_freq_qos(rclcpp::KeepLast(200));
        high_freq_qos.reliability(rclcpp::ReliabilityPolicy::Reliable);
        high_freq_qos.durability(rclcpp::DurabilityPolicy::Volatile);

        rclcpp::QoS vision_qos(rclcpp::KeepLast(10));
        vision_qos.reliability(rclcpp::ReliabilityPolicy::Reliable);
        vision_qos.durability(rclcpp::DurabilityPolicy::Volatile);

        // ── TF 监听器 ──────────────────────────────────────────────
        if (enable_tf_lookup_) {
            tf_buffer_ = std::make_unique<tf2_ros::Buffer>(this->get_clock());
            tf_listener_ = std::make_unique<tf2_ros::TransformListener>(*tf_buffer_);
        }

        // ── Subscriber ──────────────────────────────────────────────
        odom_sub_ = this->create_subscription<nav_msgs::msg::Odometry>(
            odom_topic_,
            high_freq_qos,
            std::bind(&FastLIOToMavros::odomCallback, this, std::placeholders::_1)
        );

        // ── Publisher ───────────────────────────────────────────────
        vision_pub_ = this->create_publisher<geometry_msgs::msg::PoseStamped>(
            "/mavros/vision_pose/pose",
            vision_qos
        );

        // ── Timer ───────────────────────────────────────────────────
        int timer_ms = static_cast<int>(1000.0 / publish_freq_);
        timer_ = this->create_wall_timer(
            std::chrono::milliseconds(timer_ms),
            std::bind(&FastLIOToMavros::run, this)
        );

        // ── 日志 ────────────────────────────────────────────────────
        RCLCPP_INFO(this->get_logger(),
            "fastlio_locate 节点已启动");
        RCLCPP_INFO(this->get_logger(),
            "  里程计话题: %s", odom_topic_.c_str());
        RCLCPP_INFO(this->get_logger(),
            "  发布频率: %.1f Hz", publish_freq_);
        RCLCPP_INFO(this->get_logger(),
            "  TF 查询: %s (from '%s' to '%s')",
            enable_tf_lookup_ ? "启用" : "禁用",
            world_frame_.c_str(), map_frame_.c_str());
    }

private:
    // ── 里程计回调 ─────────────────────────────────────────────────
    void odomCallback(const nav_msgs::msg::Odometry::SharedPtr msg)
    {
        std::lock_guard<std::mutex> lock(data_mutex_);
        latest_odom_ = *msg;
        received_odom_ = true;
    }

    // ── 定时发布 ───────────────────────────────────────────────────
    void run()
    {
        if (!received_odom_) return;

        nav_msgs::msg::Odometry odom_copy;
        {
            std::lock_guard<std::mutex> lock(data_mutex_);
            odom_copy = latest_odom_;
        }

        geometry_msgs::msg::PoseStamped vision;
        vision.header.stamp = this->get_clock()->now();
        vision.header.frame_id = map_frame_;

        if (enable_tf_lookup_ && tf_buffer_) {
            // ── TF 模式: 将 lidar 系下位姿转到 map 系 ─────────────
            geometry_msgs::msg::PoseStamped pose_in_world;
            pose_in_world.header.stamp = odom_copy.header.stamp;
            pose_in_world.header.frame_id = odom_copy.header.frame_id;
            pose_in_world.pose = odom_copy.pose.pose;

            try {
                // 等待 TF 可用 (最多 50ms)
                std::string tf_err;
                if (tf_buffer_->canTransform(
                        map_frame_, world_frame_,
                        odom_copy.header.stamp,
                        tf2::durationFromSec(0.05),
                        &tf_err))
                {
                    auto pose_in_map = tf_buffer_->transform(
                        pose_in_world, map_frame_,
                        tf2::durationFromSec(0.05));

                    vision.pose = pose_in_map.pose;
                } else {
                    // TF 尚不可用，回退到直通模式
                    vision.pose = odom_copy.pose.pose;
                    if (verbose_) {
                        RCLCPP_WARN_THROTTLE(this->get_logger(), *this->get_clock(), 2000,
                            "TF [%s→%s] 不可用 (%s), 直通输出",
                            world_frame_.c_str(), map_frame_.c_str(), tf_err.c_str());
                    }
                }
            } catch (const tf2::TransformException & ex) {
                vision.pose = odom_copy.pose.pose;
                if (verbose_) {
                    RCLCPP_WARN_THROTTLE(this->get_logger(), *this->get_clock(), 2000,
                        "TF 变换异常: %s, 直通输出", ex.what());
                }
            }
        } else {
            // ── 直通模式: 直接转发 ──────────────────────────────────
            vision.pose = odom_copy.pose.pose;
        }

        vision_pub_->publish(vision);

        if (verbose_) {
            RCLCPP_INFO(this->get_logger(),
                "Vision pose → PX4: pos=(%.3f, %.3f, %.3f) frame=%s",
                vision.pose.position.x,
                vision.pose.position.y,
                vision.pose.position.z,
                vision.header.frame_id.c_str());
        }
    }

    // ── 成员变量 ───────────────────────────────────────────────────
    rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr  odom_sub_;
    rclcpp::Publisher<geometry_msgs::msg::PoseStamped>::SharedPtr vision_pub_;
    rclcpp::TimerBase::SharedPtr timer_;

    std::unique_ptr<tf2_ros::Buffer> tf_buffer_;
    std::unique_ptr<tf2_ros::TransformListener> tf_listener_;

    std::string odom_topic_;
    std::string world_frame_;       // LIO 里程计的坐标系 (默认 "lidar")
    std::string map_frame_;         // 全局坐标系 (默认 "map")
    double publish_freq_;
    bool enable_tf_lookup_;
    bool verbose_;

    nav_msgs::msg::Odometry latest_odom_;
    bool received_odom_;
    std::mutex data_mutex_;
};

// ── main ──────────────────────────────────────────────────────────────
int main(int argc, char * argv[])
{
    rclcpp::init(argc, argv);
    auto node = std::make_shared<FastLIOToMavros>();
    try {
        rclcpp::spin(node);
    } catch (const std::exception & e) {
        RCLCPP_ERROR(rclcpp::get_logger("main"), "Exception: %s", e.what());
    }
    rclcpp::shutdown();
    return 0;
}
