#!/usr/bin/env python3
"""Export a point cloud from the reconstructed mesh.

The standard Open3D `run_system.py --integrate` path always writes a *triangle
mesh* (TSDF extraction); `save_output_as: pointcloud` only affects the optional
`--slac_integrate` step. This helper turns the mesh's vertices (which already
carry color + normals) into a point cloud, with an optional voxel downsample to
shrink the file.

Usage:
    python scripts/export_pointcloud.py
    python scripts/export_pointcloud.py --voxel 0.01          # 1 cm downsample
    python scripts/export_pointcloud.py --input data/myscan/scene/integrated.ply \
                                        --output data/myscan/scene/integrated_pcd.ply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import open3d as o3d


def load_point_cloud(path: Path) -> o3d.geometry.PointCloud:
    """Read a point cloud, keeping colors correct.

    SLAC writes colors as `property float red/green/blue` in [0, 1]. Open3D's
    legacy PLY reader assumes float colors are 0-255 and divides by 255, so such
    clouds load almost black (and many external viewers do the same). Reading via
    the tensor API and re-saving through the legacy writer emits standard
    `uchar` RGB, which every viewer reads correctly.
    """
    try:
        pcd = o3d.t.io.read_point_cloud(str(path)).to_legacy()
    except Exception:
        pcd = o3d.io.read_point_cloud(str(path))

    if pcd.has_colors():
        c = np.asarray(pcd.colors)
        if c.size and c.max() <= 1.0 / 255 * 2:
            print("  (colors looked 255x too dark - rescaling)")
            pcd.colors = o3d.utility.Vector3dVector(np.clip(c * 255.0, 0.0, 1.0))
    return pcd


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="data/myscan/scene/integrated.ply",
                    help="Input mesh (or point cloud) .ply")
    ap.add_argument("--output", default="data/myscan/scene/integrated_pcd.ply",
                    help="Output point cloud .ply")
    ap.add_argument("--voxel", type=float, default=0.0,
                    help="Voxel size (m) for downsampling; 0 = keep all points")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        sys.exit(f"Not found: {src}\nRun the reconstruction first (scripts\\reconstruct.ps1).")

    mesh = o3d.io.read_triangle_mesh(str(src))
    if len(mesh.vertices) > 0 and len(mesh.triangles) > 0:
        pcd = o3d.geometry.PointCloud()
        pcd.points = mesh.vertices
        if mesh.has_vertex_colors():
            pcd.colors = mesh.vertex_colors
        if mesh.has_vertex_normals():
            pcd.normals = mesh.vertex_normals
        else:
            pcd.estimate_normals()
        print(f"Mesh -> point cloud: {len(pcd.points)} points from mesh vertices")
    else:
        # Already a point cloud on disk. Use the tensor reader so SLAC output
        # (float colors in [0,1]) is not mis-scaled; see load_point_cloud below.
        pcd = load_point_cloud(src)
        print(f"Loaded point cloud: {len(pcd.points)} points")
        print("  re-saving with standard uchar RGB (portable to other viewers)")

    if args.voxel > 0:
        before = len(pcd.points)
        pcd = pcd.voxel_down_sample(args.voxel)
        print(f"Voxel downsample @ {args.voxel} m: {before} -> {len(pcd.points)} points")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    o3d.io.write_point_cloud(str(out), pcd)
    size_mb = out.stat().st_size / (1024 * 1024)
    print(f"Saved: {out}  ({size_mb:.1f} MB, {len(pcd.points)} points)")


if __name__ == "__main__":
    main()
