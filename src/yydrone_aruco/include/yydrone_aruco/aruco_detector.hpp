#ifndef YYDRONE_ARUCO__ARUCO_DETECTOR_HPP_
#define YYDRONE_ARUCO__ARUCO_DETECTOR_HPP_

#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <sensor_msgs/msg/camera_info.hpp>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <geometry_msgs/msg/transform_stamped.hpp>
#include <tf2_ros/transform_broadcaster.h>
#include <image_transport/image_transport.hpp>
#include <opencv2/opencv.hpp>
#include <opencv2/aruco.hpp>

#include <string>
#include <vector>

namespace yydrone_aruco
{

class ArucoDetector : public rclcpp::Node
{
public:
  explicit ArucoDetector(const rclcpp::NodeOptions & options = rclcpp::NodeOptions());

private:
  void imageCallback(const sensor_msgs::msg::Image::ConstSharedPtr & msg);
  void cameraInfoCallback(const sensor_msgs::msg::CameraInfo::SharedPtr msg);
  void publishPose(int marker_id, const cv::Vec3d & tvec, const cv::Vec3d & rvec);

  // Subscribers
  image_transport::Subscriber image_sub_;
  rclcpp::Subscription<sensor_msgs::msg::CameraInfo>::SharedPtr camera_info_sub_;

  // Publishers
  rclcpp::Publisher<geometry_msgs::msg::PoseStamped>::SharedPtr pose_pub_;
  rclcpp::Publisher<geometry_msgs::msg::TransformStamped>::SharedPtr transform_pub_;
  rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr debug_image_pub_;

  // TF broadcaster
  std::unique_ptr<tf2_ros::TransformBroadcaster> tf_broadcaster_;

  // ArUco parameters
  cv::Ptr<cv::aruco::Dictionary> dictionary_;
  cv::Ptr<cv::aruco::DetectorParameters> detector_params_;
  bool use_all_dicts_;
  std::vector<cv::aruco::PREDEFINED_DICTIONARY_NAME> all_dict_ids_;

  // Camera parameters
  cv::Mat camera_matrix_;
  cv::Mat dist_coeffs_;
  bool camera_calibrated_;
  double marker_size_;

  // Configuration
  std::string image_topic_;
  std::string camera_info_topic_;
  std::string camera_frame_;
  int dictionary_id_;
  bool publish_debug_image_;
  bool publish_all_markers_;
  int target_marker_id_;
};

}  // namespace yydrone_aruco

#endif  // YYDRONE_ARUCO__ARUCO_DETECTOR_HPP_
