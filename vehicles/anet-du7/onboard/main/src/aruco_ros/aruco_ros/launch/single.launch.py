from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch.utilities import perform_substitutions
from launch_ros.actions import Node


""" Dictionary options (string names, NOT numeric IDs):
ALL_DICTS           - search all dictionaries (default, slowest)
ARUCO               - original ArUco dictionary
ARUCO_MIP_36h12     - 6x6 markers, *** recommended ***
ARUCO_MIP_25h7      - 5x5 markers
ARUCO_MIP_16h3      - 4x4 markers
ARTAG               - ARTag
ARTOOLKITPLUS       - ARToolkitPlus
ARTOOLKITPLUSBCH    - ARToolkitPlusBCH
TAG16h5             - AprilTag 16h5
TAG25h7             - AprilTag 25h7
TAG25h9             - AprilTag 25h9
TAG36h11            - AprilTag 36h11
TAG36h10            - AprilTag 36h10
CHILITAGS           - ChiliTags (NOT recommended, distance 0)

Detection mode options:
DM_NORMAL       - full resolution + adaptive thresholding (most robust, slower)
DM_FAST         - global thresholding + size filter (faster, less robust)
DM_VIDEO_FAST   - like DM_FAST, but adapts size based on previous frame
"""

def launch_setup(context, *args, **kwargs):

    camera_frame = LaunchConfiguration('camera_frame')

    aruco_single_params = {
        'image_is_rectified': LaunchConfiguration('image_is_rectified'),
        'marker_size': LaunchConfiguration('marker_size'),
        'marker_id': LaunchConfiguration('marker_id'),
        'reference_frame': LaunchConfiguration('reference_frame'),
        'camera_frame': camera_frame,
        'marker_frame': LaunchConfiguration('marker_frame'),
        'corner_refinement': LaunchConfiguration('corner_refinement'),
        'dictionary': LaunchConfiguration('dictionary'),
        'error_correction_rate': LaunchConfiguration('error_correction_rate'),
        'detection_mode': LaunchConfiguration('detection_mode'),
        'min_marker_size': LaunchConfiguration('min_marker_size'),
    }

    aruco_single = Node(
        package='aruco_ros',
        executable='single',
        parameters=[aruco_single_params],
        remappings=[('/camera_info', '/camera/color/camera_info'),
                    ('/image', '/camera/color/image_raw')],
    )

    return [aruco_single]


def generate_launch_description():

    marker_id_arg = DeclareLaunchArgument(
        'marker_id', default_value='1',
        description='Marker ID. '
    )

    marker_size_arg = DeclareLaunchArgument(
        'marker_size', default_value='0.34',
        description='Marker size in m. '
    )

    camera_frame_arg = DeclareLaunchArgument(
        'camera_frame', default_value='camera_color_optical_frame',
        description='Camera optical frame. '
    )

    marker_frame_arg = DeclareLaunchArgument(
        'marker_frame', default_value='aruco_marker_frame',
        description='Frame in which the marker pose will be refered. '
    )

    reference_frame = DeclareLaunchArgument(
        'reference_frame', default_value='camera_color_optical_frame',
        description='Reference frame. '
        'Leave it empty and the pose will be published wrt param parent_name. '
    )

    corner_refinement_arg = DeclareLaunchArgument(
        'corner_refinement', default_value='LINES',
        description='Corner Refinement. ',
        choices=['NONE', 'HARRIS', 'LINES', 'SUBPIX'],
    )

    image_is_rectified_arg = DeclareLaunchArgument(
        'image_is_rectified', default_value='True',
        description='Whether the image is already rectified. '
        'Set True if using stereo_gazebo or a calibrated rectified image. '
    )

    dictionary_arg = DeclareLaunchArgument(
        'dictionary', default_value='ARUCO_MIP_36h12',
        description='ArUco dictionary name. '
        'ARUCO_MIP_36h12 (6x6), ARUCO_MIP_25h7 (5x5), ARUCO_MIP_16h3 (4x4), etc.'
    )

    error_correction_rate_arg = DeclareLaunchArgument(
        'error_correction_rate', default_value='0.2',
        description='Error correction rate [0,1]. 0=no correction, 1=full. Recommend 0.2.'
    )

    detection_mode_arg = DeclareLaunchArgument(
        'detection_mode', default_value='DM_NORMAL',
        description='Detection mode: DM_NORMAL (robust), DM_FAST (fast), DM_VIDEO_FAST. '
    )

    min_marker_size_arg = DeclareLaunchArgument(
        'min_marker_size', default_value='0.01',
        description='Min marker size as fraction of image area [0,1]. 0=no limit. '
    )

    # Create the launch description and populate
    ld = LaunchDescription()

    ld.add_action(marker_id_arg)
    ld.add_action(marker_size_arg)
    ld.add_action(camera_frame_arg)
    ld.add_action(marker_frame_arg)
    ld.add_action(reference_frame)
    ld.add_action(corner_refinement_arg)
    ld.add_action(image_is_rectified_arg)
    ld.add_action(dictionary_arg)
    ld.add_action(error_correction_rate_arg)
    ld.add_action(detection_mode_arg)
    ld.add_action(min_marker_size_arg)

    ld.add_action(OpaqueFunction(function=launch_setup))

    return ld
