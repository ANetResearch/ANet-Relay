from setuptools import find_packages, setup

package_name = 'yydrone_localization'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/yydrone_localization/launch', ['launch/fastlio_launch.py']),
        ('share/yydrone_localization/config', ['config/fastlio_livox_params.yaml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='lcx',
    maintainer_email='lcx@todo.todo',
    description='TODO: Package description',
    license='TODO: License declaration',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
        ],
    },
)
