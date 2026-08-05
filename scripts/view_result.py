#!/usr/bin/env python3
"""Step 6 - Visualize the reconstructed mesh / point cloud.

Usage:
    python scripts/view_result.py
    python scripts/view_result.py --path data/myscan/scene/integrated.ply

Failure patterns to look for (from the Sensors 2022 paper):
  * Staircase effect      -> loop closure failed; trajectory too complex, or
                             window reflections corrupted depth.
  * Misalignment on blank -> featureless white walls; add temporary markers next
    white walls               time so registration has something to latch onto.
  * Holes from occlusion  -> structural limit of this method; can't be fully removed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import open3d as o3d


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", default="data/myscan/scene/integrated.ply",
                    help="Mesh (.ply) or point cloud to display")
    args = ap.parse_args()

    p = Path(args.path)
    if not p.exists():
        sys.exit(f"Not found: {p}\nRun the pipeline first (scripts\\reconstruct.ps1).")

    mesh = o3d.io.read_triangle_mesh(str(p))
    if len(mesh.triangles) > 0:
        mesh.compute_vertex_normals()
        print(f"Mesh: {len(mesh.vertices)} verts, {len(mesh.triangles)} tris")
        o3d.visualization.draw_geometries([mesh])
    else:
        pcd = o3d.io.read_point_cloud(str(p))
        print(f"Point cloud: {len(pcd.points)} points")
        o3d.visualization.draw_geometries([pcd])


if __name__ == "__main__":
    main()
