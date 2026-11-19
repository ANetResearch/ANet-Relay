#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from mavros_msgs.srv import CommandBool, SetMode
from mavros_msgs.msg import State, PositionTarget
from geometry_msgs.msg import TwistStamped, PoseStamped
from rclpy.qos import QoSProfile, QoSReliabilityPolicy, QoSHistoryPolicy,QoSDurabilityPolicy
import threading
import sys
import termios
import tty
import select
import math

class KeyboardDroneController(Node):
    def __init__(self):
        super().__init__('keyboard_drone_controller')
        
        # 保存原始终端设置
        self.old_settings = termios.tcgetattr(sys.stdin)
        
        # 配置QoS
        qos_profile = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=10
        )

        # 初始化服务客户端
        self.arming_client = self.create_client(CommandBool, '/mavros/cmd/arming')
        self.set_mode_client = self.create_client(SetMode, '/mavros/set_mode')

        # 初始化发布者
        self.velocity_pub = self.create_publisher(
            TwistStamped,
            '/mavros/setpoint_velocity/cmd_vel',
            qos_profile=qos_profile
        )
        
        # 位置控制发布者
        self.position_pub = self.create_publisher(
            PoseStamped,
            '/mavros/setpoint_position/local',
            qos_profile=qos_profile
        )
        
        # 位置目标发布者
        self.position_target_pub = self.create_publisher(
            PositionTarget,
            '/mavros/setpoint_raw/local',
            qos_profile=qos_profile
        )

        # 订阅飞控状态
        self.state_sub = self.create_subscription(
            State,
            '/mavros/state',
            self.state_callback,
            qos_profile
        )

        # 等待服务就绪
        while not self.arming_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info('等待 arming 服务...')
        while not self.set_mode_client.wait_for_service(timeout_sec=1.0):
            self.get_logger().info('等待 set_mode 服务...')

        self.current_state = State()
        self.is_armed = False
        self.is_offboard = False
        self.arm_request = None
        self.offboard_request = None
        
        # 控制参数初始化
        self.linear_speed = 1.0  # m/s
        self.angular_speed = 1.0  # rad/s
        self.vertical_speed = 1.0  # m/s
        self.current_velocity = TwistStamped()  # 当前速度指令
        
        # 位置控制参数初始化
        self.position_step = 0.5  # 米
        self.yaw_step = 0.2  # 弧度
        self.target_position = PoseStamped()
        self.target_position.header.frame_id = "map"
        self.target_position.pose.position.x = 0.0
        self.target_position.pose.position.y = 0.0
        self.target_position.pose.position.z = 1.0  # 默认高度1米
        self.target_position.pose.orientation.w = 1.0  # 默认无旋转
        
        # 控制模式 (0: 未选择, 1: 速度控制, 2: 位置控制)
        self.control_mode = 0
        
        # 输入状态 (0: 正常, 1: 等待X坐标, 2: 等待Y坐标, 3: 等待Z坐标, 4: 等待偏航角)
        self.input_state = 0
        self.input_buffer = ""
        
        # 导航状态 (False: 等待确认, True: 正在导航)
        self.navigation_active = False
        
        # 启动设定值发布线程
        self.setpoint_thread = threading.Thread(target=self.publish_setpoints)
        self.setpoint_thread.daemon = True
        self.setpoint_thread.start()
        
        # 启动键盘监听线程
        self.keyboard_thread = threading.Thread(target=self.read_keyboard)
        self.keyboard_thread.daemon = True
        self.keyboard_thread.start()

        self.get_logger().info('键盘控制无人机节点已启动')
        self.show_mode_selection()

    def restore_terminal(self):
        """恢复终端设置"""
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.old_settings)
        self.get_logger().info('终端设置已恢复')
        
    def show_mode_selection(self):
        """显示模式选择菜单"""
        instructions = [
            "\n===== 请选择控制模式 =====",
            "1: 速度控制模式",
            "2: 位置控制模式",
            "==========================\n"
        ]
        
        for line in instructions:
            self.get_logger().info(line)
    
    def show_velocity_instructions(self):
        """显示速度控制模式说明"""
        instructions = [
            "\n===== 速度控制模式 =====",
            "W/S: 前进/后退",
            "A/D: 左移/右移",
            "K/J: 上升/下降",
            "Q/E: 左转/右转",
            "空格键: 解锁/锁定无人机",
            "O: 切换OFFBOARD模式",
            "X: 急停(归零速度)",
            "1/2: 减小/增大速度",
            "M: 返回模式选择菜单",
            "ESC或Ctrl+C: 退出程序",
            "==========================\n"
        ]
        
        for line in instructions:
            self.get_logger().info(line)
    
    def show_position_instructions(self):
        """显示位置控制模式说明"""
        instructions = [
            "\n===== 位置控制模式 =====",
            "W/S: 增加/减少X坐标",
            "A/D: 增加/减少Y坐标",
            "K/J: 增加/减少Z坐标",
            "Q/E: 增加/减少偏航角",
            "P: 直接输入坐标和偏航角",
            "空格键: 解锁/锁定无人机",
            "O: 切换OFFBOARD模式",
            "R: 重置位置到原点",
            "N: 开始导航(确认目标位置)",
            "M: 返回模式选择菜单",
            "ESC或Ctrl+C: 退出程序",
            "==========================\n"
        ]
        
        for line in instructions:
            self.get_logger().info(line)
        self.log_current_position()

    def log_current_position(self):
        """记录当前位置信息"""
        # 计算偏航角（从四元数转换为欧拉角）
        w = self.target_position.pose.orientation.w
        x = self.target_position.pose.orientation.x
        y = self.target_position.pose.orientation.y
        z = self.target_position.pose.orientation.z
        
        # 计算偏航角（弧度）
        yaw = math.atan2(2.0*(w*z + x*y), 1.0 - 2.0*(y*y + z*z))
        yaw_deg = math.degrees(yaw)
        
        status = "导航中" if self.navigation_active else "等待确认"
        
        self.get_logger().info(f"当前目标位置: X={self.target_position.pose.position.x:.2f}m, "
                              f"Y={self.target_position.pose.position.y:.2f}m, "
                              f"Z={self.target_position.pose.position.z:.2f}m, "
                              f"偏航角={yaw_deg:.1f}度 [{status}]")

   
    def state_callback(self, msg):
        """更新飞控状态"""
        self.current_state = msg
        self.is_armed = msg.armed
        self.is_offboard = (msg.mode == "OFFBOARD")
        
    def publish_setpoints(self):
        """持续发布设定值"""
        rate = self.create_rate(50)  # 20 Hz
        
        # 先发送100帧零速度
        self.get_logger().info('发送初始设定值...')
        zero_velocity = TwistStamped()
        zero_velocity.header.frame_id = "map"
        
        for _ in range(100):
            zero_velocity.header.stamp = self.get_clock().now().to_msg()
            self.velocity_pub.publish(zero_velocity)
            rate.sleep()
            
        self.get_logger().info('准备就绪，等待键盘指令...')
        
        # 持续发布当前控制指令
        while rclpy.ok():
            if self.control_mode == 1:  # 速度控制模式
                self.current_velocity.header.stamp = self.get_clock().now().to_msg()
                self.current_velocity.header.frame_id = "map"
                self.velocity_pub.publish(self.current_velocity)
            elif self.control_mode == 2:  # 位置控制模式
                # if self.navigation_active:  # 只在导航激活时发布位置
                self.target_position.header.stamp = self.get_clock().now().to_msg()
                self.position_pub.publish(self.target_position)
            else:
                zero_vel = TwistStamped()
                self.velocity_pub.publish(zero_vel)
            rate.sleep()

    def read_keyboard(self):
        """监听键盘输入"""
        tty.setcbreak(sys.stdin.fileno())  
        
        try:
            while rclpy.ok():
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    key = sys.stdin.read(1)
                    self.process_keypress(key)
                    
                    # ESC或Ctrl+C退出
                    if ord(key) == 27 or ord(key) == 3:  
                        break
        finally:
            self.restore_terminal()
    
    def process_keypress(self, key):
        """处理按键指令 - 仅设置标志位"""
        # 模式选择阶段
        if self.control_mode == 0:
            if key == '1':
                self.control_mode = 1
                self.show_velocity_instructions()
            elif key == '2':
                self.control_mode = 2
                self.navigation_active = False  # 初始状态为等待确认
                self.show_position_instructions()
            return
        
        # 返回模式选择菜单
        if key == 'm':
            self.control_mode = 0
            self.navigation_active = False
            self.show_mode_selection()
            return
        
        # 如果处于输入状态，处理输入字符
        if self.input_state > 0:
            self.process_input(key)
            return
        
        # 通用控制命令
        if key == ' ':
            if self.is_armed:
                self.arm_request = False  # 请求锁定
            else:
                self.arm_request = True   # 请求解锁
                
        elif key == 'o':
            self.offboard_request = True  # 请求进入OFFBOARD模式
            
        # 模式特定的控制命令
        if self.control_mode == 1:  # 速度控制模式
            self.process_velocity_controls(key)
        elif self.control_mode == 2:  # 位置控制模式
            self.process_position_controls(key)
    
    def process_input(self, key):
        """处理坐标输入"""
        # 回车键确认输入
        if key == '\r' or key == '\n':
            try:
                value = float(self.input_buffer)
                
                if self.input_state == 1:  # X坐标
                    self.target_position.pose.position.x = value
                    self.get_logger().info(f"设置X坐标: {value:.2f}m")
                    self.input_state = 2
                    self.get_logger().info("请输入Y坐标: ")
                elif self.input_state == 2:  # Y坐标
                    self.target_position.pose.position.y = value
                    self.get_logger().info(f"设置Y坐标: {value:.2f}m")
                    self.input_state = 3
                    self.get_logger().info("请输入Z坐标: ")
                elif self.input_state == 3:  # Z坐标
                    self.target_position.pose.position.z = value
                    self.get_logger().info(f"设置Z坐标: {value:.2f}m")
                    self.input_state = 4
                    self.get_logger().info("请输入偏航角(度): ")
                elif self.input_state == 4:  # 偏航角
                    # 将角度转换为弧度
                    yaw_rad = math.radians(value)
                    
                    # 计算四元数
                    cy = math.cos(yaw_rad * 0.5)
                    sy = math.sin(yaw_rad * 0.5)
                    
                    self.target_position.pose.orientation.z = sy
                    self.target_position.pose.orientation.w = cy
                    self.get_logger().info(f"设置偏航角: {value:.1f}度")
                    
                    # 输入完成
                    self.input_state = 0
                    self.log_current_position()
                    self.show_position_instructions()
            except ValueError:
                self.get_logger().error("输入无效，请输入数字")
                self.input_state = 0
                self.show_position_instructions()
            
            self.input_buffer = ""
        # 退格键删除字符
        elif key == '\x7f' or key == '\x08':  # 退格键
            if self.input_buffer:
                self.input_buffer = self.input_buffer[:-1]
        # 其他字符添加到缓冲区
        else:
            self.input_buffer += key
    
    def process_velocity_controls(self, key):
        """处理速度控制模式的按键"""
        # 移动控制
        if key == 'w':  # 前进
            self.current_velocity.twist.linear.x = self.linear_speed
        elif key == 's':  # 后退
            self.current_velocity.twist.linear.x = -self.linear_speed
        elif key == 'a':  # 左移
            self.current_velocity.twist.linear.y = self.linear_speed
        elif key == 'd':  # 右移
            self.current_velocity.twist.linear.y = -self.linear_speed
        elif key == 'k':  # 上升
            self.current_velocity.twist.linear.z = self.vertical_speed
        elif key == 'j':  # 下降
            self.current_velocity.twist.linear.z = -self.vertical_speed
        elif key == 'q':  # 左转
            self.current_velocity.twist.angular.z = self.angular_speed
        elif key == 'e':  # 右转
            self.current_velocity.twist.angular.z = -self.angular_speed
            
        # 急停
        elif key == 'x':
            self.current_velocity.twist = TwistStamped().twist  # 归零
            
        # 速度调整
        elif key == '1':  # 降低速度
            self.linear_speed = max(0.1, self.linear_speed * 0.8)
            self.angular_speed = max(0.1, self.angular_speed * 0.8)
            self.vertical_speed = max(0.1, self.vertical_speed * 0.8)
            self.get_logger().info(f'速度降低到: 水平{self.linear_speed:.2f}m/s, 垂直{self.vertical_speed:.2f}m/s, 转向{self.angular_speed:.2f}rad/s')
            
        elif key == '2':  # 增加速度
            self.linear_speed = min(2.0, self.linear_speed * 1.2)
            self.angular_speed = min(2.0, self.angular_speed * 1.2)
            self.vertical_speed = min(2.0, self.vertical_speed * 1.2)
            self.get_logger().info(f'速度增加到: 水平{self.linear_speed:.2f}m/s, 垂直{self.vertical_speed:.2f}m/s, 转向{self.angular_speed:.2f}rad/s')
    
    def process_position_controls(self, key):
        """处理位置控制模式的按键"""
        # 位置调整
        if key == 'w':  # 增加X坐标
            self.target_position.pose.position.x += self.position_step
        elif key == 's':  # 减少X坐标
            self.target_position.pose.position.x -= self.position_step
        elif key == 'a':  # 增加Y坐标
            self.target_position.pose.position.y += self.position_step
        elif key == 'd':  # 减少Y坐标
            self.target_position.pose.position.y -= self.position_step
        elif key == 'k':  # 增加Z坐标
            self.target_position.pose.position.z += self.position_step
        elif key == 'j':  # 减少Z坐标
            self.target_position.pose.position.z -= self.position_step
            
        # 偏航角调整
        elif key == 'q':  # 增加偏航角（左转）
            # 获取当前偏航角
            w = self.target_position.pose.orientation.w
            z = self.target_position.pose.orientation.z
            current_yaw = math.atan2(2.0*(w*z), 1.0 - 2.0*(z*z))
            
            # 增加偏航角
            new_yaw = current_yaw + self.yaw_step
            
            # 更新四元数
            self.target_position.pose.orientation.z = math.sin(new_yaw/2)
            self.target_position.pose.orientation.w = math.cos(new_yaw/2)
            
        elif key == 'e':  # 减少偏航角（右转）
            w = self.target_position.pose.orientation.w
            z = self.target_position.pose.orientation.z
            current_yaw = math.atan2(2.0*(w*z), 1.0 - 2.0*(z*z))
            
            # 减少偏航角
            new_yaw = current_yaw - self.yaw_step
            
            # 更新四元数
            self.target_position.pose.orientation.z = math.sin(new_yaw/2)
            self.target_position.pose.orientation.w = math.cos(new_yaw/2)
            
        # 重置位置
        elif key == 'r':
            self.target_position.pose.position.x = 0.0
            self.target_position.pose.position.y = 0.0
            self.target_position.pose.position.z = 1.0
            self.target_position.pose.orientation.z = 0.0
            self.target_position.pose.orientation.w = 1.0
            self.get_logger().info('位置已重置到原点')
            
        # 直接输入坐标
        elif key == 'p':
            self.input_state = 1
            self.input_buffer = ""
            self.get_logger().info("请输入X坐标: ")
            
        # 开始/停止导航
        elif key == 'n':
            self.navigation_active = not self.navigation_active
            status = "开始" if self.navigation_active else "停止"
            self.get_logger().info(f"{status}导航到目标位置")
        
        # 记录位置变化
        if key in ['w', 's', 'a', 'd', 'k', 'j', 'q', 'e', 'r', 'p']:
            self.log_current_position()
    
    def execute_requests(self):
        """在主线程执行所有挂起的请求"""
        if self.offboard_request is not None:
            self.offboard_request = None
            self.activate_offboard()
            
        if self.arm_request is not None:
            arm = self.arm_request
            self.arm_request = None
            self.arm_drone(arm)

    def arm_drone(self, arm):
        """锁定/解锁无人机 - 只从主线程调用"""
        req = CommandBool.Request()
        req.value = arm
        
        future = self.arming_client.call_async(req)
        rclpy.spin_until_future_complete(self, future, timeout_sec=0.1)
        
        if future.result() and future.result().success:
            action = "解锁" if arm else "锁定"
            self.get_logger().info(f'无人机已{action}!')
        else:
            action = "解锁" if arm else "锁定"
            self.get_logger().error(f'无人机{action}失败!')
            if not self.is_offboard:
                self.get_logger().warning('注意:解锁前请先进入OFFBOARD模式!')

    def activate_offboard(self):
        """进入OFFBOARD模式 - 只从主线程调用"""
        req = SetMode.Request()
        req.custom_mode = "OFFBOARD"
        
        future = self.set_mode_client.call_async(req)
        rclpy.spin_until_future_complete(self, future, timeout_sec=0.1)
        
        if future.result() and future.result().mode_sent:
            self.get_logger().info('OFFBOARD模式已激活!')
        else:
            self.get_logger().error('激活OFFBOARD模式失败!')


def main(args=None):
    rclpy.init(args=args)
    controller = KeyboardDroneController()
    
    try:
        
        while rclpy.ok():
            rclpy.spin_once(controller, timeout_sec=0.1)
            controller.execute_requests()
            
    except KeyboardInterrupt:
        pass
    finally:
        controller.restore_terminal()
        controller.get_logger().info('关闭节点...')
        controller.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()