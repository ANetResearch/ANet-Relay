#include <rclcpp/rclcpp.hpp>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <geometry_msgs/msg/twist_stamped.hpp>
#include <mavros_msgs/srv/command_bool.hpp>
#include <mavros_msgs/srv/set_mode.hpp>
#include <mavros_msgs/msg/state.hpp>
#include <termios.h>
#include <studio.h>
#include <unistd.h>
#include <memory>

using namespace std::chrono_literals;

class KeyboardControl : public rclcpp::Node
{
private:
    //创建订阅者和发布者
    rclcpp::Publisher<geometry_msgs::msg::PoseStamped>::SharedPtr local_pos_pub_ ;
    rclcpp::Publisher<geometry_msgs::msg::TwistStamped>::SharedPtr vel_pub_ ;
    rclcpp::Subscription<mavros_msgs::msg::State>::SharedPtr state_sub_ ;
    //创建客户端
    rclcpp::client<mavros_msgs::srv::CommandBool>::SharedPtr arm_client_ ;
    rclcpp::client<mavros_msgs::srv::SetMode>::SharedPtr set_mode_client_ ;
    //创建消息并定义消息类型
    mavros_msgs::msg::State current_state_;
    geometry_msgs::msg::TwistStamped vel_;
    geometry_msgs::msg::PoseStamped pose_;
    //创建线速度和角速度变量
    double linear_vel_;
    double angular_vel_;
    double altitude_value_;
    //键盘输入
    struct termios cooked_, raw_;
    
    //定时器循环
    rclcpp::TimerBase::SharedPtr control_timer_;
    rclcpp::TimerBase:: SharedPtr init_timer_;
    bool position_control_;
    int count_;

    public:
    KeyboardControl() : Node('keyboard_control'),linear_vel_(0.5),angular_vel_(0.5),altitude_value_(3),
    position_control_(false),count_(0)
    {
    local_pos_pub_ = this->create_publisher<geometry_msgs::msg::PoseStamped>("/mavros/setpoint_position/local",10);
    vel_pub_ = this->create_publisher<geometry_msgs::msg::TwistStamped>("/mavros/setpoint_velocity/cmd_vel",10);
    state_sub_ = this->create_subscription<mavros_msgs::msg::State>("/mavros/State",10,std::bind(&KeyboardControl::state_cb,this,std::placeholders::_1));

    arming_client_ = this->create_client<mavros_msgs::srv::CommandBool>("/mavros/cmd/arming");
    setmode_client_ = this->create_client<mavros_msgs::srv::SetMode>("/mavros/set_mode");

    //初始化设置
    pose_.header.frame_id ="map";
    pose_.pose.position.x = 0.0;
    pose_.pose.position.y = 0.0;
    pose_.pose.position.z = 2.0;
    pose_.pose.orientation.w =1.0;

    //初始化速度
    vel_pub_.frame_id = "map";

    //直接开始控制流程
    RCLCPP_INFO(this->get_logger(), "键盘控制节点已初始化");
    start_control();
    }

    ~KeyboardControl()
    {
        tcsetattr(STDIN_FILENO, TCSANOW, &cooked_);
    }

}
