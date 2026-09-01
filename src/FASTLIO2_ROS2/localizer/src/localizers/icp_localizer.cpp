#include "icp_localizer.h"
#include <iostream>    // [DEBUG] for std::cout

ICPLocalizer::ICPLocalizer(const ICPConfig &config) : m_config(config)
{
    m_refine_inp.reset(new CloudType);
    m_refine_tgt.reset(new CloudType);
    m_rough_inp.reset(new CloudType);
    m_rough_tgt.reset(new CloudType);
}
bool ICPLocalizer::loadMap(const std::string &path)
{
    if (!std::filesystem::exists(path))
    {
        std::cerr << "Map file not found: " << path << std::endl;
        return false;
    }
    pcl::PCDReader reader;
    CloudType::Ptr cloud(new CloudType);
    reader.read(path, *cloud);
    if (m_config.refine_map_resolution > 0)
    {
        m_voxel_filter.setLeafSize(m_config.refine_map_resolution, m_config.refine_map_resolution, m_config.refine_map_resolution);
        m_voxel_filter.setInputCloud(cloud);
        m_voxel_filter.filter(*m_refine_tgt);
    }
    else
    {
        pcl::copyPointCloud(*cloud, *m_refine_tgt);
    }

    if (m_config.rough_map_resolution > 0)
    {
        m_voxel_filter.setLeafSize(m_config.rough_map_resolution, m_config.rough_map_resolution, m_config.rough_map_resolution);
        m_voxel_filter.setInputCloud(cloud);
        m_voxel_filter.filter(*m_rough_tgt);
    }
    else
    {
        pcl::copyPointCloud(*cloud, *m_rough_tgt);
    }
    return true;
}
void ICPLocalizer::setInput(const CloudType::Ptr &cloud)
{
    if (m_config.refine_scan_resolution > 0)
    {
        m_voxel_filter.setLeafSize(m_config.refine_scan_resolution, m_config.refine_scan_resolution, m_config.refine_scan_resolution);
        m_voxel_filter.setInputCloud(cloud);
        m_voxel_filter.filter(*m_refine_inp);
    }
    else
    {
        pcl::copyPointCloud(*cloud, *m_refine_inp);
    }

    if (m_config.rough_scan_resolution > 0)
    {
        m_voxel_filter.setLeafSize(m_config.rough_scan_resolution, m_config.rough_scan_resolution, m_config.rough_scan_resolution);
        m_voxel_filter.setInputCloud(cloud);
        m_voxel_filter.filter(*m_rough_inp);
    }
    else
    {
        pcl::copyPointCloud(*cloud, *m_rough_inp);
    }
}

bool ICPLocalizer::align(M4F &guess)
{
    // [DEBUG] 计数器,每10帧打印一次成功日志,失败时无条件打印
    static int align_count = 0;
    bool print_detail = (++align_count % 10 == 0);

    CloudType::Ptr aligned_cloud(new CloudType);
    if (m_refine_tgt->size() == 0 || m_rough_tgt->size() == 0)
        return false;

    // ── 粗配 ICP ──
    m_rough_icp.setMaximumIterations(m_config.rough_max_iteration);
    m_rough_icp.setInputSource(m_rough_inp);
    m_rough_icp.setInputTarget(m_rough_tgt);
    m_rough_icp.align(*aligned_cloud, guess);

    if (print_detail)
        std::cout << "[ICP] rough: converged=" << m_rough_icp.hasConverged()
                  << " score=" << m_rough_icp.getFitnessScore()
                  << " thresh=" << m_config.rough_score_thresh
                  << " iter=" << m_config.rough_max_iteration << std::endl;

    if (!m_rough_icp.hasConverged() || m_rough_icp.getFitnessScore() > m_config.rough_score_thresh)
    {
        std::cout << "[ICP] rough FAILED, score=" << m_rough_icp.getFitnessScore()
                  << " thresh=" << m_config.rough_score_thresh << std::endl;  // [DEBUG] 失败时无条件打印
        return false;
    }

    // ── 精配 ICP ──
    m_refine_icp.setMaximumIterations(m_config.refine_max_iteration);
    m_refine_icp.setInputSource(m_refine_inp);
    m_refine_icp.setInputTarget(m_refine_tgt);
    m_refine_icp.align(*aligned_cloud, m_rough_icp.getFinalTransformation());

    if (print_detail)
        std::cout << "[ICP] refine: converged=" << m_refine_icp.hasConverged()
                  << " score=" << m_refine_icp.getFitnessScore()
                  << " thresh=" << m_config.refine_score_thresh
                  << " iter=" << m_config.refine_max_iteration << std::endl;

    if (!m_refine_icp.hasConverged() || m_refine_icp.getFitnessScore() > m_config.refine_score_thresh)
    {
        std::cout << "[ICP] refine FAILED, score=" << m_refine_icp.getFitnessScore()
                  << " thresh=" << m_config.refine_score_thresh << std::endl;  // [DEBUG] 失败时无条件打印
        return false;
    }

    guess = m_refine_icp.getFinalTransformation();
    return true;
}