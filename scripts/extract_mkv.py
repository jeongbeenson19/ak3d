#!/usr/bin/env python3
"""Step 3 - Convert an Azure Kinect .mkv recording into an Open3D dataset.

Produces the exact layout the Open3D Reconstruction System expects::

    <output>/
        color/000000.jpg, 000001.jpg, ...
        depth/000000.png, 000001.png, ...   (16-bit, millimetres)
        intrinsic.json                       (Open3D PinholeCameraIntrinsic)

Depth is transformed into the *color* camera geometry, so color and depth share
one set of intrinsics (the color camera's). That is why intrinsic.json is emitted
automatically -- no manual copying of intrinsics into a YAML, unlike the ORB-SLAM
route.

Usage:
    python scripts/extract_mkv.py --input output.mkv --output data/myscan
    python scripts/extract_mkv.py --input output.mkv --output data/myscan --every 2

Requires: pyk4a, opencv-python, numpy, and the native Azure Kinect SDK (libk4a).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

try:
    from pyk4a import PyK4APlayback, CalibrationType, ImageFormat
except ImportError:
    sys.exit(
        "pyk4a is not installed (or libk4a is missing). "
        "Run: pip install -r requirements.txt  and install the Azure Kinect SDK.\n"
        "See README 'Setup'."
    )


def decode_color(capture) -> np.ndarray | None:
    """Return a BGR uint8 image regardless of the recorded color format."""
    color = capture.color
    if color is None:
        return None
    # MJPG recordings arrive as a 1-D buffer that must be JPEG-decoded.
    if color.ndim == 1:
        return cv2.imdecode(color, cv2.IMREAD_COLOR)
    # BGRA -> BGR
    if color.shape[-1] == 4:
        return cv2.cvtColor(color, cv2.COLOR_BGRA2BGR)
    return color


def write_intrinsic(playback: PyK4APlayback, width: int, height: int, path: Path) -> None:
    """Write the color-camera intrinsics in Open3D's PinholeCameraIntrinsic JSON."""
    k = playback.calibration.get_camera_matrix(CalibrationType.COLOR)
    fx, fy = float(k[0, 0]), float(k[1, 1])
    cx, cy = float(k[0, 2]), float(k[1, 2])
    intrinsic = {
        "width": int(width),
        "height": int(height),
        # Open3D stores the 3x3 matrix column-major (flattened).
        "intrinsic_matrix": [fx, 0.0, 0.0, 0.0, fy, 0.0, cx, cy, 1.0],
    }
    path.write_text(json.dumps(intrinsic, indent=4))
    print(f"  intrinsic.json  ({width}x{height}, fx={fx:.2f} fy={fy:.2f} "
          f"cx={cx:.2f} cy={cy:.2f})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="Path to the .mkv recording")
    ap.add_argument("--output", required=True, help="Output dataset directory")
    ap.add_argument("--every", type=int, default=1,
                    help="Keep every Nth capture (default 1 = all frames)")
    args = ap.parse_args()

    mkv = Path(args.input)
    if not mkv.exists():
        sys.exit(f"Input not found: {mkv}")

    out = Path(args.output)
    color_dir = out / "color"
    depth_dir = out / "depth"
    color_dir.mkdir(parents=True, exist_ok=True)
    depth_dir.mkdir(parents=True, exist_ok=True)

    playback = PyK4APlayback(str(mkv))
    playback.open()
    print(f"Reading {mkv}  ->  {out}")

    kept = 0
    seen = 0
    intrinsic_written = False
    try:
        while True:
            try:
                capture = playback.get_next_capture()
            except EOFError:
                break

            # Need both a color image and a depth frame aligned to it.
            if capture.color is None or capture.transformed_depth is None:
                continue

            if seen % args.every != 0:
                seen += 1
                continue
            seen += 1

            color = decode_color(capture)
            depth = capture.transformed_depth  # uint16, millimetres, color geometry
            if color is None or depth is None:
                continue

            if not intrinsic_written:
                h, w = color.shape[:2]
                write_intrinsic(playback, w, h, out / "intrinsic.json")
                intrinsic_written = True

            stem = f"{kept:06d}"
            cv2.imwrite(str(color_dir / f"{stem}.jpg"), color)
            cv2.imwrite(str(depth_dir / f"{stem}.png"), depth.astype(np.uint16))
            kept += 1
            if kept % 50 == 0:
                print(f"  ...{kept} frames")
    finally:
        playback.close()

    if kept == 0:
        sys.exit("No usable frames extracted. Check that the recording has "
                 "synchronized color + depth.")
    print(f"Done: {kept} frames -> {out}")


if __name__ == "__main__":
    main()
