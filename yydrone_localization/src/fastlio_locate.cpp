// fastlio_to_mavros.cpp
#include <rclcpp/rclcpp.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <array>

class FastLIOToMavros : public rclcpp::Node
{
public:
    FastLIOToMavros() : Node("fastlio_to_mavros"), received_odom_(false)
    {
        // ---------- 初始化状态 ----------
        p_lidar_body_ = {0.0, 0.0, 0.0};
        q_mav_        = {0.0, 0.0, 0.0, 1.0};  // x y z w

        // ---------- QoS: 高频里程计 ----------
        rclcpp::QoS high_freq_qos(rclcpp::KeepLast(200));
        high_freq_qos.reliability(rclcpp::ReliabilityPolicy::Reliable);
        high_freq_qos.durability(rclcpp::DurabilityPolicy::Volatile);

        // ---------- QoS: 发送给vision_pose ----------
        rclcpp::QoS vision_qos(rclcpp::KeepLast(10));
        vision_qos.reliability(rclcpp::ReliabilityPolicy::Reliable);
        vision_qos.durability(rclcpp::DurabilityPolicy::Volatile);

        // ---------- 订阅者 ----------
        odom_sub_ = this->create_subscription<nav_msgs::msg::Odometry>(
            "/Odom_high_freq",
            high_freq_qos,
            std::bind(&FastLIOToMavros::vins_callback, this, std::placeholders::_1)
        );

        // ---------- 发布者 ----------
        vision_pub_ = this->create_publisher<geometry_msgs::msg::PoseStamped>(
            "/mavros/vision_pose/pose",
            vision_qos
        );

        // ---------- Timer: 100 Hz高频发布 -----------
        timer_ = this->create_wall_timer(
            std::chrono::milliseconds(10),
            std::bind(&FastLIOToMavros::run, this)
        );

        RCLCPP_INFO(this->get_logger(), "fastlio_to_mavros node started.");
    }

private:
    // ---------- Callback: 从Fast-LIO获取里程计数据 ----------
    void vins_callback(const nav_msgs::msg::Odometry::SharedPtr msg)
    {
        p_lidar_body_[0] = msg->pose.pose.position.x;
        p_lidar_body_[1] = msg->pose.pose.position.y;
        p_lidar_body_[2] = msg->pose.pose.position.z;

        q_mav_[0] = msg->pose.pose.orientation.x;
        q_mav_[1] = msg->pose.pose.orientation.y;
        q_mav_[2] = msg->pose.pose.orientation.z;
        q_mav_[3] = msg->pose.pose.orientation.w;

        received_odom_ = true;
    }

    // ---------- Timer callback: 以100 Hz发布 ----------
    void run()
    {
        if (!received_odom_) return;

        geometry_msgs::msg::PoseStamped vision;
        vision.header.stamp    = this->get_clock()->now();
        vision.header.frame_id = "map";

        vision.pose.position.x = p_lidar_body_[0];
        vision.pose.position.y = p_lidar_body_[1];
        vision.pose.position.z = p_lidar_body_[2];

        vision.pose.orientation.x = q_mav_[0];
        vision.pose.orientation.y = q_mav_[1];
        vision.pose.orientation.z = q_mav_[2];
        vision.pose.orientation.w = q_mav_[3];

        vision_pub_->publish(vision);

        RCLCPP_INFO(this->get_logger(),
            "Pos: x=%.3f y=%.3f z=%.3f | Ori: x=%.3f y=%.3f z=%.3f w=%.3f",
            p_lidar_body_[0], p_lidar_body_[1], p_lidar_body_[2],
            q_mav_[0], q_mav_[1], q_mav_[2], q_mav_[3]
        );
    }

    // ---------- Members ----------
    rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr  odom_sub_;
    rclcpp::Publisher<geometry_msgs::msg::PoseStamped>::SharedPtr vision_pub_;
    rclcpp::TimerBase::SharedPtr timer_;

    std::array<double, 3> p_lidar_body_;   // x y z
    std::array<double, 4> q_mav_;          // x y z w
    bool received_odom_;
};

// ---------- main ----------
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