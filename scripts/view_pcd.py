#!/usr/bin/env python3
"""简易 PCD 地图查看器 — 3D 散点图，按高度/intensity 着色"""
import sys
import struct
import numpy as np
import matplotlib.pyplot as plt

def read_pcd_binary(path):
    """读取 PCD v0.7 binary 文件，返回字段名列表和 numpy 数组"""
    header = {}
    with open(path, 'rb') as f:
        # 读取头部
        for _ in range(20):
            line = f.readline().decode().strip()
            if line.startswith('DATA'):
                data_type = line.split()[1]
                break
            if line and not line.startswith('#'):
                parts = line.split()
                header[parts[0]] = parts[1:]

    fields = header['FIELDS']
    sizes = [int(s) for s in header['SIZE']]
    types = header['TYPE']
    counts = [int(c) for c in header['COUNT']]
    width = int(header['WIDTH'][0])
    height = int(header['HEIGHT'][0])
    n_points = width * height

    # 计算每行字节数
    row_bytes = sum(s * c for s, c in zip(sizes, counts))
    fmt_map = {'F': 'f', 'I': 'I', 'U': 'B'}
    dtype = np.dtype([(f, f'<{fmt_map[t]}{s*c}') for f, t, s, c in zip(fields, types, sizes, counts)])

    print(f"文件: {path}")
    print(f"点数: {n_points}, 字段: {fields}")
    print(f"范围计算中...")

    # 读取二进制数据
    with open(path, 'rb') as f:
        # 找到 DATA 行之后的位置
        buf = f.read()
    data_start = buf.find(b'DATA binary\n') + len(b'DATA binary\n')
    raw = buf[data_start:]

    data = np.frombuffer(raw, dtype=dtype, count=n_points)
    return data, fields

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else '/home/lcx/drone_ws/src/maps/global_map.pcd'
    data, fields = read_pcd_binary(path)

    x = data['x'].astype(np.float32)
    y = data['y'].astype(np.float32)
    z = data['z'].astype(np.float32)

    # 用高度着色
    colors = z
    color_label = 'Height (Z)'

    # 如果存在 intensity 且不为全0，用 intensity 着色
    if 'intensity' in data.dtype.names:
        intens = data['intensity'].astype(np.float32)
        if intens.max() - intens.min() > 0.01:
            colors = intens
            color_label = 'Intensity'

    # 降采样以提高渲染速度
    max_pts = 50000
    if len(x) > max_pts:
        idx = np.random.choice(len(x), max_pts, replace=False)
        x, y, z, colors = x[idx], y[idx], z[idx], colors[idx]
        print(f"降采样至 {max_pts} 点以便渲染")

    print(f"X: [{x.min():.2f}, {x.max():.2f}]")
    print(f"Y: [{y.min():.2f}, {y.max():.2f}]")
    print(f"Z: [{z.min():.2f}, {z.max():.2f}]")

    fig = plt.figure(figsize=(12, 5))

    # 俯视图 (XY)
    ax1 = fig.add_subplot(121)
    sc1 = ax1.scatter(x, y, c=colors, s=0.3, cmap='viridis', alpha=0.7)
    ax1.set_xlabel('X (m)'); ax1.set_ylabel('Y (m)')
    ax1.set_title('Top View (XY)')
    ax1.set_aspect('equal')
    plt.colorbar(sc1, ax=ax1, label=color_label)

    # 侧视图 (XZ)
    ax2 = fig.add_subplot(122)
    sc2 = ax2.scatter(x, z, c=colors, s=0.3, cmap='viridis', alpha=0.7)
    ax2.set_xlabel('X (m)'); ax2.set_ylabel('Z (m)')
    ax2.set_title('Side View (XZ)')
    ax2.set_aspect('equal')
    plt.colorbar(sc2, ax=ax2, label=color_label)

    plt.tight_layout()
    out_path = '/tmp/pcd_map_view.png'
    plt.savefig(out_path, dpi=150)
    print(f"图片已保存: {out_path}")
    plt.show()

if __name__ == '__main__':
    main()
