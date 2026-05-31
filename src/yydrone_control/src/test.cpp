#include <rclcpp/rclcpp.hpp>
#include <mavros_msgs/srv/set_mode.hpp>
#include <mavros_msgs/srv/command_bool.hpp>
#include <mavros_msgs/msg/state.hpp>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <geometry_msgs/msg/twist_stamped.hpp>
#include <termios.h>
#include <stduio.h>
using namespace std::chrono_literals;

class KeyboardControl : public rclcpp::Node
{
private:
    //定义订阅者和发布者
    rclcpp::Publisher<geometry_msgs::msg::PoseStamped>::SharedPtr setpoint_pub_;
    rclcpp::Publisher<geometry_msgs::msg::TwistStamped>::SharedPtr vel_pub_;
    rclcpp::Subscription<mavros_msgs::msg::State>::SharedPtr current_state_sub_;

    //定义客户端
    rclcpp::Client::mavros_msgs::srv::CommandBool::SharedPtr arming_client_;
    rclcpp::client::mavros_msgs::srv::SetMode::SharedPtr set_mode_client_;

    //定义其它初始值
    rclcpp::TimerBase time1_;
    rclcpp::TimerBase time2_;
    int count_;
    double altitude_value_;
    bool position_control_;
    geometry_msgs::msg::TwistStamped vel_;
    geometry_msgs::msg::PoseStamped pose_;
    struct termios cooked_,raw_;
    double linear_vel_;
    double angular_vel;

    KeyboardControl():rclcpp::Node("keyboard_control_node"),count_(0),altitude_value_(2),
    linear_vel_(0.5),angular_vel(0.5),position_control_(true)
    {
        setpoint_pub_ = this->create_publisher<geometry_msgs::msg::PoseStamped>("/mavros/setpoint_position/local",10);
        vel_pub_ = this->create_publisher<geometry_msgs::msg::TwistStamped>("/mavros/setpoint_velocity/cmd_vel",10);
        current_state_sub_ = this->create_subscription<mavros_msgs::msg::State>("/mavros/State",10,std::bind(&KeyboardControl::state_cb,this,std::placeholders::_1));
        arming_client_ = this->create_client<mavros_msgs::srv::CommandBool>("/mavros/cmd/arming");
        set_mode_client_ = this->create_client<mavros_msgs::srv::SetMode>("/mavros/set_mode");
        pose_.header.frame_id = "map";
        pose_.pose.position.x = 0.0;
        pose_.pose.position.y = 0.0;
        pose_.pose.position.z = 2.0;
        vel_.header.frame_id = "map";
        vel_.twist.linear.x = 0;
        while(!arming_client_.wait_for_service())
        {
            RCLCPP_INFO(get_logger(),"等待解锁服务...");
        }
        while(!set_mode_client_.wait_for_service())
        {
            RCLCPP_INFO(get_logger(),"等待模式设置服务...");
        }
        RCLCPP_INFO(get_logger(),"键盘节点已初始化完成");
        start_control();
    }
    ~KeyboardControl()
    {
        tcsetattr(STDIN_FILENO,TCSANOW,&cooked_);
    }

    start_control()
    {   
        time1_ = this->create_wall_timer(50ms,[this]{
        if(count_<50){
            pose_.header.stamp = this->now();
            setpoint_pub_->publish(pose_);
            count_++;
            RCLCPP_INFO(get_logger(),"正在发送目标点...");
        }
        else{
            RCLCPP_INFO(get_logger(),"50个目标点已经发送完成");
            print_controls();

        }
    })
        
    }

    void print_controls()
    {
        RCLCPP_INFO(get_logger(),"====键盘控制节点已经启动====");
        RCLCPP_INFO(get_logger(),"k 切换至OFFOBARD模式");
        RCLCPP_INFO(get_logger(),"w/s 前进/后退");
        RCLCPP_INFO(get_logger(),"a/d 左移/右移");
        RCLCPP_INFO(get_logger(),"q/e 左旋/右旋");
        RCLCPP_INFO(get_logger(),"r/f 上升/下降");
        RCLCPP_INFO(get_logger(),"i/o 位置模式/速度模式");
        RCLCPP_INFO(get_logger(),"x 一键回到初始点");
        RCLCPP_INFO(get_logger(),"q 退出键盘控制");
        RCLCPP_INFO(get_logger(),"t/u 解锁/上锁");        
    }

    void state_cb(const mavros_msgs::msg::State& msg)
    {
        current_state_ = msg;
    }

    void arm(bool arm_command)
    {
        auto request = std::make_shared<mavros_msgs::srv::CommandBool::Request>();
        request->value = arm_command;
        arming_client_->async_send_request(request);
        RCLCPP_INFO(get_logger(),"无人机%s", arm_command ? "解锁":"锁定");

    }
    void set_mode(const std::string& mode){
        auto request = set_mode_client_<mavros_msgs::srv:SetMode::Request>();
        request.custom_mode = mode;
        set_mode_client_.async_send_request(request);
        RCLCPP_INFO(get_logger(),"模式设置为%s",mode.c_str());
    }

    char getkey()
    {
        char c = 0 ;
        if (tcgetattr(STDIN_FILENO,&cooked_) != 0)
        {
            return 0 ;
        }
        
        raw_ = &cooked_;
        raw_.c_lflag = ~(|);
        if (tcsetattr(STDIN_FILENO,TCSANOW,raw_) != 0)
        {
            return 0 ;
        }

        if (read(STDIN_FILENO, &c,1)<0)
        {
            RCLCPP_INFO("获取失败");
        }
        
        tcsetattr(STDIN_FILENO, TCSANOW, &cooked_);

    }

    void start_keyboard_control()
    {
        control_timer_ = this->create_wall_timer(100ms,[this]{
            char key = getKey();
            if (key != 0){
                handle_key_input();
            }
            if (position_control_ !=0 ){
                pose_.header.stamp = now();
                setpoint_pub_->publish(pose_);
            }
            else
            {
                linear_vel_.header.stamp = now();
                vel_pub_(linear_vel_);
            }
            
        })
    }

    void handle_key_input()
    {
        if (key == "w"){
            vel_.twist.linear_vel_.x += 0.5;                
        }
        if (key == "s"){
            vel_.twist.linear_vel_.x -= 0.5;                
        }
        if (key == "d"){
            vel_.twist.linear_vel_.y += 0.5;                
        }        
        if (key == "a"){
            vel_.twist.linear_vel_.y -= 0.5;                
        }        
        if (key == "r"){
            vel_.twist.linear_vel_.z += 0.5;                
        }
        if (key == "f"){
            vel_.twist.linear_vel_.z -= 0.5;                
        }
        if (key == "t"){
            arm(true);
        }
        if (key == "u"){
            arm(false);
        }
        if (key == "q"){
            vel_.twist.angular_vel_.z += 0.5
        }

    }
}   
