#include <rclcpp/rclcpp.hpp>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <geometry_msgs/msg/twist_stamped.hpp>
#include <mavros_msgs/srv/command_bool.hpp>
#include <mavros_msgs/srv/set_mode.hpp>
#include <mavros_msgs/msg/state.hpp>
#include <termios.h>
#include <stdio.h>
#include <unistd.h>
#include <memory>

using namespace std::chrono_literals;

class KeyboardControl : public rclcpp::Node
{
private:
    // 发布器和订阅器
    rclcpp::Publisher<geometry_msgs::msg::PoseStamped>::SharedPtr local_pos_pub_;
    rclcpp::Publisher<geometry_msgs::msg::TwistStamped>::SharedPtr vel_pub_;
    rclcpp::Subscription<mavros_msgs::msg::State>::SharedPtr state_sub_;
    
    // 服务客户端
    rclcpp::Client<mavros_msgs::srv::CommandBool>::SharedPtr arming_client_;
    rclcpp::Client<mavros_msgs::srv::SetMode>::SharedPtr set_mode_client_;
    
    // 无人机状态
    mavros_msgs::msg::State current_state_;
    
    // 控制参数
    geometry_msgs::msg::PoseStamped pose_;
    geometry_msgs::msg::TwistStamped vel_;
    
    double linear_vel_;
    double angular_vel_;
    double altitude_vel_;
    
    // 键盘输入相关
    struct termios cooked_, raw_;
    
    // 定时器
    rclcpp::TimerBase::SharedPtr control_timer_;
    rclcpp::TimerBase::SharedPtr init_timer_;
    bool position_control_;
    int init_count_;
    
public:
    KeyboardControl() : Node("keyboard_control_node"), 
                       linear_vel_(0.5), angular_vel_(0.5), altitude_vel_(1.0),
                       position_control_(true), init_count_(0)
    {
        // 初始化发布器和订阅器
        local_pos_pub_ = this->create_publisher<geometry_msgs::msg::PoseStamped>(
            "/mavros/setpoint_position/local", 10);
        vel_pub_ = this->create_publisher<geometry_msgs::msg::TwistStamped>(
            "/mavros/setpoint_velocity/cmd_vel", 10);
        state_sub_ = this->create_subscription<mavros_msgs::msg::State>(
            "/mavros/state", 10, 
            std::bind(&KeyboardControl::state_cb, this, std::placeholders::_1));
        
        // 初始化服务客户端
        arming_client_ = this->create_client<mavros_msgs::srv::CommandBool>("/mavros/cmd/arming");
        set_mode_client_ = this->create_client<mavros_msgs::srv::SetMode>("/mavros/set_mode");
        
        // 初始化位置
        pose_.header.frame_id = "base_link";
        pose_.pose.position.x = 0.0;
        pose_.pose.position.y = 0.0;
        pose_.pose.position.z = 2.0;
        pose_.pose.orientation.w = 1.0;
        
        // 初始化速度
        vel_.header.frame_id = "base_link";
        
        while (!arming_client_->wait_for_service(1s)) {
            RCLCPP_INFO(this->get_logger(), "等待arming服务...");
        }
        while (!set_mode_client_->wait_for_service(1s)) {
            RCLCPP_INFO(this->get_logger(), "等待set_mode服务...");
        }    
        
        RCLCPP_INFO(this->get_logger(), "键盘控制节点已初始化");
        
        // 直接开始控制流程
        start_control();
    }
    
    ~KeyboardControl()
    {
        tcsetattr(STDIN_FILENO, TCSANOW, &cooked_);
    }
    //是否还会再执行
    void start_control()
    {
        // 使用成员变量定时器发送初始设置点
        init_timer_ = this->create_wall_timer(50ms, [this]() {
            if (init_count_ < 50) {
                pose_.header.stamp = this->now();
                local_pos_pub_->publish(pose_);
                init_count_++;
            } else {
                // 停止初始定时器并开始键盘控制
                init_timer_->cancel();
                RCLCPP_INFO(this->get_logger(), "初始设置点发送完成");
                print_controls();
                start_keyboard_control();
            }
        });
    }
    
    void print_controls()
    {
        RCLCPP_INFO(this->get_logger(), "=== 键盘控制指令 ===");
        RCLCPP_INFO(this->get_logger(), "w/s: 前进/后退");
        RCLCPP_INFO(this->get_logger(), "a/d: 左移/右移"); 
        RCLCPP_INFO(this->get_logger(), "r/f: 上升/下降");
        RCLCPP_INFO(this->get_logger(), "q/e: 左转/右转");
        RCLCPP_INFO(this->get_logger(), "t: OFFBOARD模式");
        RCLCPP_INFO(this->get_logger(), "y: 解锁无人机");
        RCLCPP_INFO(this->get_logger(), "u: 锁定无人机");
        RCLCPP_INFO(this->get_logger(), "i/o: 位置/速度模式");
        RCLCPP_INFO(this->get_logger(), "ESC: 退出");
    }
    //为什么不&msg
    void state_cb(const mavros_msgs::msg::State::SharedPtr msg)
    {
        current_state_ = *msg;
    }
    
    void arm_callback(rclcpp::Client<mavros_msgs::srv::SetMode>::SharedFuture future)
    {

    }
    void setMode(const std::string& mode)
    {
        if (set_mode_client_->wait_for_service(1s)) {
            auto request = std::make_shared<mavros_msgs::srv::SetMode::Request>();
            request->custom_mode = mode;
            auto future = set_mode_client_->async_send_request(request);
            RCLCPP_INFO(this->get_logger(), "设置模式: %s", mode.c_str());
        } else {
            RCLCPP_ERROR(this->get_logger(), "set_mode服务不可用");
        }
    }
    
    void arm(bool arm_command)
    {
        if (arming_client_->wait_for_service(1s)) {
            auto request = std::make_shared<mavros_msgs::srv::CommandBool::Request>();
            request->value = arm_command;
            auto future = arming_client_->async_send_request(request);
            // future.then(std::bind(&KeyboardControl::arm_callback, this, std::placeholders::_1));
            RCLCPP_INFO(this->get_logger(), "%s电机", arm_command ? "解锁" : "锁定");
        } else {
            RCLCPP_ERROR(this->get_logger(), "arming服务不可用");
        }
    }
    
    char getKey()
    {
        char c = 0;
        
        // 获取终端设置
        if (tcgetattr(STDIN_FILENO, &cooked_) != 0) {
            return 0;
        }
        
        raw_ = cooked_;
        raw_.c_lflag &= ~(ICANON | ECHO);
        
        if (tcsetattr(STDIN_FILENO, TCSANOW, &raw_) != 0) {
            return 0;
        }

        tcflush(STDIN_FILENO, TCIFLUSH);
        
        if (read(STDIN_FILENO, &c, 1) < 0) {
            RCLCPP_INFO(get_logger(),"获取失败");
        }
        
        // 恢复终端设置
        tcsetattr(STDIN_FILENO, TCSANOW, &cooked_);
        
        return c;
    }
    
    void start_keyboard_control()
    {
        control_timer_ = this->create_wall_timer(100ms, [this]() {
            char key = getKey();
            if (key != 0) {
                handle_key_input(key);
            }
            
            // 发布控制命令
            if (position_control_) {
                pose_.header.stamp = this->now();
                local_pos_pub_->publish(pose_);
            } else {
                vel_.header.stamp = this->now();
                vel_pub_->publish(vel_);
            }
        });
    }
    
    void handle_key_input(char key)
    {
        switch(key) {
            case 'w':  // 前进
                if (position_control_) {
                    pose_.pose.position.x += linear_vel_;
                    RCLCPP_INFO(this->get_logger(), "前进: x=%.2f", pose_.pose.position.x);
                } else {
                    vel_.twist.linear.x = linear_vel_;
                }
                break;
                
            case 's':  // 后退
                if (position_control_) {
                    pose_.pose.position.x -= linear_vel_;
                    RCLCPP_INFO(this->get_logger(), "后退: x=%.2f", pose_.pose.position.x);
                } else {
                    vel_.twist.linear.x = -linear_vel_;
                }
                break;
                
            case 'a':  // 左移
                if (position_control_) {
                    pose_.pose.position.y += linear_vel_;
                    RCLCPP_INFO(this->get_logger(), "左移: y=%.2f", pose_.pose.position.y);
                } else {
                    vel_.twist.linear.y = linear_vel_;
                }
                break;
                
            case 'd':  // 右移
                if (position_control_) {
                    pose_.pose.position.y -= linear_vel_;
                    RCLCPP_INFO(this->get_logger(), "右移: y=%.2f", pose_.pose.position.y);
                } else {
                    vel_.twist.linear.y = -linear_vel_;
                }
                break;
                
            case 'r':  // 上升
                if (position_control_) {
                    pose_.pose.position.z += altitude_vel_;
                    RCLCPP_INFO(this->get_logger(), "上升: z=%.2f", pose_.pose.position.z);
                } else {
                    vel_.twist.linear.z = altitude_vel_;
                }
                break;
                
            case 'f':  // 下降
                if (position_control_) {
                    pose_.pose.position.z -= altitude_vel_;
                    RCLCPP_INFO(this->get_logger(), "下降: z=%.2f", pose_.pose.position.z);
                } else {
                    vel_.twist.linear.z = -altitude_vel_;
                }
                break;
                
            case 'q':  // 左转
                if (!position_control_) {
                    vel_.twist.angular.z = angular_vel_;
                }
                break;
                
            case 'e':  // 右转
                if (!position_control_) {
                    vel_.twist.angular.z = -angular_vel_;
                }
                break;
                
            case 't':  // OFFBOARD模式
                setMode("OFFBOARD");
                break;
                
            case 'y':  // 解锁
                arm(true);
                break;
                
            case 'u':  // 锁定
                arm(false);
                break;
                 
            case 'i':  // 位置控制模式
                position_control_ = true;
                RCLCPP_INFO(this->get_logger(), "切换到位置控制模式");
                break;
                
            case 'o':  // 速度控制模式
                position_control_ = false;
                // 重置速度
                vel_.twist.linear.x = 0;
                vel_.twist.linear.y = 0;
                vel_.twist.linear.z = 0;
                vel_.twist.angular.z = 0;
                RCLCPP_INFO(this->get_logger(), "切换到速度控制模式");
                break;
                
            case 27:   // ESC键
                RCLCPP_INFO(this->get_logger(), "退出程序");
                rclcpp::shutdown();
                break;
                
            // default:
            //     // 在速度控制模式下，松开按键时停止
            //     if (!position_control_) {
            //         vel_.twist.linear.x = 0;
            //         vel_.twist.linear.y = 0;
            //         vel_.twist.linear.z = 0;
            //         vel_.twist.angular.z = 0;
            //     }
                // break;
        }
    }
};

int main(int argc, char** argv)
{
    rclcpp::init(argc, argv);
    auto node = std::make_shared<KeyboardControl>();
    rclcpp::spin(node);
    rclcpp::shutdown();
    return 0;
}