#include "yydrone_aruco/aruco_detector.hpp"
#include <cv_bridge/cv_bridge.h>
#include <tf2/LinearMath/Quaternion.h>
#include <tf2/LinearMath/Matrix3x3.h>
#include <tf2_geometry_msgs/tf2_geometry_msgs.hpp>


namespace yydrone_aruco
{

ArucoDetector::ArucoDetector(const rclcpp::NodeOptions & options)
: Node("aruco_detector", options)
{
  // ── Declare parameters ──────────────────────────────────────────
  this->declare_parameter("image_topic", "/camera/color/image_raw");
  this->declare_parameter("camera_info_topic", "/camera/color/camera_info");
  this->declare_parameter("camera_frame", "camera_color_optical_frame");
  this->declare_parameter("dictionary", "DICT_6X6_250");
  this->declare_parameter("marker_size", 0.15);
  this->declare_parameter("publish_debug_image", true);
  this->declare_parameter("publish_all_markers", true);
  this->declare_parameter("target_marker_id", 0);

  // ── Read parameters ─────────────────────────────────────────────
  image_topic_         = this->get_parameter("image_topic").as_string();
  camera_info_topic_   = this->get_parameter("camera_info_topic").as_string();
  camera_frame_        = this->get_parameter("camera_frame").as_string();
  marker_size_         = this->get_parameter("marker_size").as_double();
  publish_debug_image_ = this->get_parameter("publish_debug_image").as_bool();
  publish_all_markers_ = this->get_parameter("publish_all_markers").as_bool();
  target_marker_id_    = this->get_parameter("target_marker_id").as_int();
  std::string dict_name = this->get_parameter("dictionary").as_string();

  // ── Camera intrinsics: start unknown, will be filled from camera_info ──
  camera_calibrated_ = false;
  camera_matrix_ = cv::Mat::eye(3, 3, CV_64F);
  dist_coeffs_   = cv::Mat::zeros(1, 5, CV_64F);

  // ── Set up ArUco dictionary ─────────────────────────────────────
  // "ALL" = iterate all predefined dictionaries (slower but universal)
  use_all_dicts_ = (dict_name == "ALL");

  if (use_all_dicts_) {
    // 只遍历每个尺寸的最大字典（覆盖所有该尺寸的可能码）
    // 4种尺寸 × 每个最大容量 = 4次检测，比全遍历17次快4倍
    all_dict_ids_ = {
      cv::aruco::DICT_4X4_1000,   // 覆盖所有4x4
      cv::aruco::DICT_5X5_1000,   // 覆盖所有5x5
      cv::aruco::DICT_6X6_1000,   // 覆盖所有6x6
      cv::aruco::DICT_7X7_1000,   // 覆盖所有7x7
      cv::aruco::DICT_ARUCO_ORIGINAL,  // 原始ArUco
    };
    // Use first dict as default (detection will loop over all)
    dictionary_ = cv::aruco::getPredefinedDictionary(all_dict_ids_[0]);
    RCLCPP_INFO(this->get_logger(), "AR marker detection: ALL dictionaries (%zu types)", all_dict_ids_.size());
  } else {
    cv::aruco::PREDEFINED_DICTIONARY_NAME dict_id;
    try {
      dict_id = cv::aruco::PREDEFINED_DICTIONARY_NAME(std::stoi(dict_name));
    } catch (...) {
      if (dict_name == "DICT_4X4_50")      dict_id = cv::aruco::DICT_4X4_50;
      else if (dict_name == "DICT_4X4_100")     dict_id = cv::aruco::DICT_4X4_100;
      else if (dict_name == "DICT_4X4_250")     dict_id = cv::aruco::DICT_4X4_250;
      else if (dict_name == "DICT_4X4_1000")    dict_id = cv::aruco::DICT_4X4_1000;
      else if (dict_name == "DICT_5X5_50")      dict_id = cv::aruco::DICT_5X5_50;
      else if (dict_name == "DICT_5X5_100")     dict_id = cv::aruco::DICT_5X5_100;
      else if (dict_name == "DICT_5X5_250")     dict_id = cv::aruco::DICT_5X5_250;
      else if (dict_name == "DICT_5X5_1000")    dict_id = cv::aruco::DICT_5X5_1000;
      else if (dict_name == "DICT_6X6_50")      dict_id = cv::aruco::DICT_6X6_50;
      else if (dict_name == "DICT_6X6_100")     dict_id = cv::aruco::DICT_6X6_100;
      else if (dict_name == "DICT_6X6_250")     dict_id = cv::aruco::DICT_6X6_250;
      else if (dict_name == "DICT_6X6_1000")    dict_id = cv::aruco::DICT_6X6_1000;
      else if (dict_name == "DICT_7X7_50")      dict_id = cv::aruco::DICT_7X7_50;
      else if (dict_name == "DICT_7X7_100")     dict_id = cv::aruco::DICT_7X7_100;
      else if (dict_name == "DICT_7X7_250")     dict_id = cv::aruco::DICT_7X7_250;
      else if (dict_name == "DICT_7X7_1000")    dict_id = cv::aruco::DICT_7X7_1000;
      else if (dict_name == "DICT_ARUCO_ORIGINAL") dict_id = cv::aruco::DICT_ARUCO_ORIGINAL;
      else {
        RCLCPP_WARN(this->get_logger(), "Unknown dictionary '%s', fallback to DICT_6X6_250", dict_name.c_str());
        dict_id = cv::aruco::DICT_6X6_250;
      }
    }
    dictionary_ = cv::aruco::getPredefinedDictionary(dict_id);
  }

  // ── Detector parameters ─────────────────────────────────────────
  detector_params_ = cv::aruco::DetectorParameters::create();
  // Use adaptive thresholding for better robustness to lighting changes
  detector_params_->adaptiveThreshWinSizeMin  = 3;
  detector_params_->adaptiveThreshWinSizeMax  = 23;
  detector_params_->adaptiveThreshWinSizeStep = 10;
  detector_params_->minMarkerPerimeterRate    = 0.03;   // marker at least 3% of image
  detector_params_->maxMarkerPerimeterRate    = 4.0;
  detector_params_->polygonalApproxAccuracyRate = 0.03;
  detector_params_->cornerRefinementMethod    = cv::aruco::CORNER_REFINE_SUBPIX;
  detector_params_->cornerRefinementWinSize   = 5;
  detector_params_->cornerRefinementMaxIterations = 30;
  detector_params_->cornerRefinementMinAccuracy   = 0.1;
  // Enable error correction
  detector_params_->errorCorrectionRate        = 0.6;

  // ── Subscribers ─────────────────────────────────────────────────
  image_sub_ = image_transport::create_subscription(
    this, image_topic_,
    std::bind(&ArucoDetector::imageCallback, this, std::placeholders::_1),
    "raw");

  camera_info_sub_ = this->create_subscription<sensor_msgs::msg::CameraInfo>(
    camera_info_topic_, rclcpp::SensorDataQoS(),
    std::bind(&ArucoDetector::cameraInfoCallback, this, std::placeholders::_1));

  // ── Publishers ──────────────────────────────────────────────────
  pose_pub_ = this->create_publisher<geometry_msgs::msg::PoseStamped>(
    "~/aruco_pose", 10);
  transform_pub_ = this->create_publisher<geometry_msgs::msg::TransformStamped>(
    "~/aruco_transform", 10);

  if (publish_debug_image_) {
    debug_image_pub_ = this->create_publisher<sensor_msgs::msg::Image>("~/debug_image", 10);
  }

  tf_broadcaster_ = std::make_unique<tf2_ros::TransformBroadcaster>(this);

  // ── Log ─────────────────────────────────────────────────────────
  RCLCPP_INFO(this->get_logger(),
    "ArUco detector ready | dict=%s | marker_size=%.3f m | image=%s | camera_info=%s | frame=%s",
    dict_name.c_str(), marker_size_, image_topic_.c_str(),
    camera_info_topic_.c_str(), camera_frame_.c_str());
  RCLCPP_INFO(this->get_logger(),
    "Publishing all markers: %s | target marker id: %d",
    publish_all_markers_ ? "yes" : "no", target_marker_id_);
}


void ArucoDetector::cameraInfoCallback(const sensor_msgs::msg::CameraInfo::SharedPtr msg)
{
  if (camera_calibrated_) {
    camera_info_sub_.reset();  // only need it once
    return;
  }

  // Build camera matrix from CameraInfo
  camera_matrix_ = (cv::Mat_<double>(3, 3) <<
    msg->k[0], msg->k[1], msg->k[2],
    msg->k[3], msg->k[4], msg->k[5],
    msg->k[6], msg->k[7], msg->k[8]);

  // Distortion coefficients
  dist_coeffs_ = cv::Mat::zeros(1, 5, CV_64F);
  for (size_t i = 0; i < msg->d.size() && i < 5; i++) {
    dist_coeffs_.at<double>(0, i) = msg->d[i];
  }

  // Update camera frame from camera_info if not explicitly set
  if (camera_frame_.empty() || camera_frame_ == "camera_color_optical_frame") {
    // already correct, keep as-is
  }

  camera_calibrated_ = true;
  RCLCPP_INFO(this->get_logger(),
    "Camera intrinsics loaded | fx=%.1f fy=%.1f cx=%.1f cy=%.1f | dist=[%.4f %.4f %.4f %.4f %.4f]",
    msg->k[0], msg->k[4], msg->k[2], msg->k[5],
    msg->d.size() > 0 ? msg->d[0] : 0.0,
    msg->d.size() > 1 ? msg->d[1] : 0.0,
    msg->d.size() > 2 ? msg->d[2] : 0.0,
    msg->d.size() > 3 ? msg->d[3] : 0.0,
    msg->d.size() > 4 ? msg->d[4] : 0.0);

  // Shut down subscription after first camera_info
  camera_info_sub_.reset();
}


void ArucoDetector::imageCallback(const sensor_msgs::msg::Image::ConstSharedPtr & msg)
{
  // Convert ROS image → OpenCV
  cv_bridge::CvImageConstPtr cv_ptr;
  try {
    cv_ptr = cv_bridge::toCvShare(msg, sensor_msgs::image_encodings::BGR8);
  } catch (cv_bridge::Exception & e) {
    RCLCPP_ERROR(this->get_logger(), "cv_bridge error: %s", e.what());
    return;
  }

  cv::Mat image = cv_ptr->image;
  cv::Mat debug_image;
  if (publish_debug_image_) {
    debug_image = image.clone();
  }

  // ── Detect markers ──────────────────────────────────────────────
  std::vector<int> ids;
  std::vector<std::vector<cv::Point2f>> corners;

  if (use_all_dicts_) {
    // Iterate ALL dictionaries, merge results
    for (auto dict_id : all_dict_ids_) {
      auto dict = cv::aruco::getPredefinedDictionary(dict_id);
      std::vector<int> tmp_ids;
      std::vector<std::vector<cv::Point2f>> tmp_corners;
      cv::aruco::detectMarkers(image, dict, tmp_corners, tmp_ids, detector_params_);
      if (!tmp_ids.empty()) {
        // Filter out duplicates (IDs already found in previous dicts)
        for (size_t i = 0; i < tmp_ids.size(); i++) {
          if (std::find(ids.begin(), ids.end(), tmp_ids[i]) == ids.end()) {
            ids.push_back(tmp_ids[i]);
            corners.push_back(tmp_corners[i]);
          }
        }
      }
    }
  } else {
    cv::aruco::detectMarkers(image, dictionary_, corners, ids, detector_params_);
  }

  if (ids.empty()) {
    if (publish_debug_image_) {
      auto debug_msg = cv_bridge::CvImage(msg->header, "bgr8", debug_image).toImageMsg();
      debug_image_pub_->publish(*debug_msg);
    }
    return;
  }

  // ── Draw detected markers on debug image ────────────────────────
  if (publish_debug_image_) {
    cv::aruco::drawDetectedMarkers(debug_image, corners, ids);
  }

  // ── Pose estimation (if camera is calibrated) ───────────────────
  if (camera_calibrated_) {
    std::vector<cv::Vec3d> rvecs, tvecs;
    cv::aruco::estimatePoseSingleMarkers(corners, marker_size_, camera_matrix_, dist_coeffs_, rvecs, tvecs);

    for (size_t i = 0; i < ids.size(); i++) {
      int m_id = ids[i];

      // Skip if filtering to a single marker and this is not the target
      if (!publish_all_markers_ && m_id != target_marker_id_) {
        continue;
      }

      publishPose(m_id, tvecs[i], rvecs[i]);

      if (publish_debug_image_) {
        cv::aruco::drawAxis(debug_image, camera_matrix_, dist_coeffs_,
                           rvecs[i], tvecs[i], marker_size_ * 0.5);
      }
    }
  } else {
    // Camera not yet calibrated — still draw markers, rate-limited warning
    RCLCPP_WARN_THROTTLE(this->get_logger(), *this->get_clock(), 5000,
      "Camera not calibrated yet — waiting for camera_info on %s", camera_info_topic_.c_str());
  }

  // ── Publish debug image ─────────────────────────────────────────
  if (publish_debug_image_) {
    auto debug_msg = cv_bridge::CvImage(msg->header, "bgr8", debug_image).toImageMsg();
    debug_image_pub_->publish(*debug_msg);
  }
}


void ArucoDetector::publishPose(int marker_id, const cv::Vec3d & tvec, const cv::Vec3d & rvec)
{
  // Rodrigues → rotation matrix → quaternion
  cv::Mat rot_mat;
  cv::Rodrigues(rvec, rot_mat);
  tf2::Matrix3x3 tf_rot(
    rot_mat.at<double>(0, 0), rot_mat.at<double>(0, 1), rot_mat.at<double>(0, 2),
    rot_mat.at<double>(1, 0), rot_mat.at<double>(1, 1), rot_mat.at<double>(1, 2),
    rot_mat.at<double>(2, 0), rot_mat.at<double>(2, 1), rot_mat.at<double>(2, 2));
  tf2::Quaternion tf_quat;
  tf_rot.getRotation(tf_quat);

  rclcpp::Time now = this->now();

  // ── PoseStamped ─────────────────────────────────────────────────
  geometry_msgs::msg::PoseStamped pose_msg;
  pose_msg.header.stamp = now;
  pose_msg.header.frame_id = camera_frame_;
  pose_msg.pose.position.x = tvec[0];
  pose_msg.pose.position.y = tvec[1];
  pose_msg.pose.position.z = tvec[2];
  pose_msg.pose.orientation = tf2::toMsg(tf_quat);
  pose_pub_->publish(pose_msg);

  // ── TF broadcast ────────────────────────────────────────────────
  std::string child_frame = "aruco_marker_" + std::to_string(marker_id);
  geometry_msgs::msg::TransformStamped tf_msg;
  tf_msg.header.stamp = now;
  tf_msg.header.frame_id = camera_frame_;
  tf_msg.child_frame_id = child_frame;
  tf_msg.transform.translation.x = tvec[0];
  tf_msg.transform.translation.y = tvec[1];
  tf_msg.transform.translation.z = tvec[2];
  tf_msg.transform.rotation = tf2::toMsg(tf_quat);
  tf_broadcaster_->sendTransform(tf_msg);

  // ── TransformStamped topic ──────────────────────────────────────
  transform_pub_->publish(tf_msg);
}

}  // namespace yydrone_aruco

#include <rclcpp_components/register_node_macro.hpp>
RCLCPP_COMPONENTS_REGISTER_NODE(yydrone_aruco::ArucoDetector)
