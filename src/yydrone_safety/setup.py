from setuptools import find_packages, setup

package_name = 'yydrone_safety'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/safety_launch.py']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='lcx',
    maintainer_email='lcx@todo.todo',
    description='Drone safety monitoring nodes',
    license='TODO: License declaration',
    entry_points={
        'console_scripts': [
            'drone_health_monitor = yydrone_safety.drone_health_monitor:main',
        ],
    },
)
