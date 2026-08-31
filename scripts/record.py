#!/usr/bin/env python3
"""Step 1 (interactive) - Record an Azure Kinect capture with a live RGB preview.

The Azure Kinect can only be opened by ONE process at a time, so we can't run
k4arecorder and a separate viewer together. This single process both shows the
live color feed (OpenCV window) AND writes the .mkv recording via pyk4a, so you
can see exactly what you're capturing while you move around.

Each capture goes into a new timestamped directory so earlier recordings are
never overwritten:  captures/yyyyMMdd_HHmmss/capture.mkv

Controls:
    q or ESC  - stop recording and save
    (or Ctrl+C in the terminal)

When the recording is saved it is handed straight to scripts/transfer.py, so the
workstation has the .mkv without a second manual step. That is on by default when
transfer.json sets "auto_send": true; --send / --no-send override it per run.

Usage:
    python scripts/record.py
    python scripts/record.py --seconds 60 --show-depth
    python scripts/record.py --seconds 60 --send
    python scripts/record.py --color-resolution 1080p --root D:\\scans
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

try:
    from pyk4a import PyK4A, Config, PyK4ARecord
    from pyk4a import ColorResolution, DepthMode, FPS, ImageFormat
except ImportError:
    sys.exit(
        "pyk4a is not installed (or libk4a is missing). "
        "Run: pip install -r requirements.txt  and install the Azure Kinect SDK."
    )

COLOR_RES = {
    "720p": ColorResolution.RES_720P,
    "1080p": ColorResolution.RES_1080P,
    "1440p": ColorResolution.RES_1440P,
    "1536p": ColorResolution.RES_1536P,
    "2160p": ColorResolution.RES_2160P,
    "3072p": ColorResolution.RES_3072P,
}
DEPTH_MODE = {
    "NFOV_UNBINNED": DepthMode.NFOV_UNBINNED,
    "NFOV_2X2BINNED": DepthMode.NFOV_2X2BINNED,
    "WFOV_UNBINNED": DepthMode.WFOV_UNBINNED,
    "WFOV_2X2BINNED": DepthMode.WFOV_2X2BINNED,
}
FPS_MAP = {5: FPS.FPS_5, 15: FPS.FPS_15, 30: FPS.FPS_30}


def decode_color(color) -> "np.ndarray | None":
    """MJPG-recorded color arrives as a 1-D buffer; decode to BGR for display."""
    if color is None:
        return None
    if color.ndim == 1:
        return cv2.imdecode(color, cv2.IMREAD_COLOR)
    if color.shape[-1] == 4:
        return cv2.cvtColor(color, cv2.COLOR_BGRA2BGR)
    return color


def draw_hud(img, elapsed: float, frames: int, blink: bool):
    """Overlay a recording indicator and stats onto the preview image."""
    h, w = img.shape[:2]
    cv2.rectangle(img, (0, 0), (w, 40), (0, 0, 0), -1)
    if blink:  # blinking red REC dot
        cv2.circle(img, (22, 20), 9, (0, 0, 255), -1)
    cv2.putText(img, f"REC  {elapsed:5.1f}s   {frames} frames   [q]=stop",
                (42, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return img


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="captures", help="Parent folder for captures")
    ap.add_argument("--color-resolution", default="720p", choices=list(COLOR_RES))
    ap.add_argument("--depth-mode", default="NFOV_UNBINNED", choices=list(DEPTH_MODE))
    ap.add_argument("--fps", type=int, default=30, choices=list(FPS_MAP))
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="Auto-stop after N seconds (0 = until 'q')")
    ap.add_argument("--show-depth", action="store_true",
                    help="Also open a colorized depth window")
    ap.add_argument("--preview-width", type=int, default=3080,
                    help="Preview window width in px (aspect kept)")
    ap.add_argument("--send", dest="send", action="store_true", default=None,
                    help="Upload the capture to the workstation when recording stops")
    ap.add_argument("--no-send", dest="send", action="store_false",
                    help="Keep the capture on this laptop (overrides auto_send)")
    ap.add_argument("--delete-local", action="store_true",
                    help="With --send: delete the local .mkv once the copy is verified")
    args = ap.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    capture_dir = Path(args.root) / stamp
    capture_dir.mkdir(parents=True, exist_ok=True)
    output = capture_dir / "capture.mkv"

    config = Config(
        color_resolution=COLOR_RES[args.color_resolution],
        color_format=ImageFormat.COLOR_MJPG,   # compact, matches k4arecorder
        depth_mode=DEPTH_MODE[args.depth_mode],
        camera_fps=FPS_MAP[args.fps],
        synchronized_images_only=True,
    )

    device = PyK4A(config=config, device_id=0)
    try:
        device.start()
    except Exception as e:  # device busy / not connected / no power
        shutil.rmtree(capture_dir, ignore_errors=True)
        sys.exit(f"Failed to open Azure Kinect: {e}\n"
                 "Close k4aviewer/other apps holding the device, check power & USB3.")

    record = PyK4ARecord(device=device, config=config, path=str(output))
    record.create()
    print(f"Recording -> {output}")
    print("  live preview open. Press 'q' or ESC in the window to stop.")

    win = "Azure Kinect - RGB (recording)"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    frames = 0
    start = time.monotonic()
    try:
        while True:
            capture = device.get_capture()
            record.write_capture(capture)   # write every capture for a valid mkv
            frames += 1
            elapsed = time.monotonic() - start

            bgr = decode_color(capture.color)
            if bgr is not None:
                h, w = bgr.shape[:2]
                if w > args.preview_width:
                    scale = args.preview_width / w
                    bgr = cv2.resize(bgr, (args.preview_width, int(h * scale)))
                draw_hud(bgr, elapsed, frames, blink=int(elapsed * 2) % 2 == 0)
                cv2.imshow(win, bgr)

            if args.show_depth and capture.transformed_depth is not None:
                d = capture.transformed_depth
                dvis = cv2.applyColorMap(
                    cv2.convertScaleAbs(d, alpha=255.0 / 3000.0), cv2.COLORMAP_JET)
                cv2.imshow("Azure Kinect - Depth", dvis)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord('q'), 27):        # q or ESC
                break
            if args.seconds > 0 and elapsed >= args.seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        record.flush()
        record.close()
        device.stop()
        cv2.destroyAllWindows()

    if frames == 0 or not output.exists():
        shutil.rmtree(capture_dir, ignore_errors=True)
        sys.exit("No frames recorded - removed empty capture directory.")

    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"\nSaved: {output}  ({size_mb:.1f} MB, {frames} frames, "
          f"{time.monotonic() - start:.1f}s)")

    if maybe_send(capture_dir, args):
        return
    print(f"Next:  python scripts/transfer.py send \"{capture_dir}\"   "
          f"(then extract on the workstation)")


def maybe_send(capture_dir: Path, args) -> bool:
    """Hand the finished capture to the workstation. Returns True if we tried.

    A failed upload must never look like a failed recording: the .mkv is already
    safe on disk, so we report the error and let the user rerun transfer.py, which
    resumes from whatever the workstation received.
    """
    try:
        import transfer  # scripts/ is on sys.path when running scripts/record.py
    except ImportError as e:
        if args.send:
            print(f"--send requested but scripts/transfer.py is unusable: {e}",
                  file=sys.stderr)
        return False

    cfg = transfer.Config.load()
    send = cfg.auto_send if args.send is None else args.send
    if not send:
        return False
    if not (cfg.host and cfg.user and cfg.remote_root):
        print("skipping upload: transfer.json has no host/user/remote_root "
              "(copy transfer.example.json).", file=sys.stderr)
        return False

    try:
        transfer.send_capture(cfg, capture_dir,
                              delete_local=args.delete_local or None)
        return True
    except transfer.TransferError as e:
        print(f"\nUpload failed: {e}\n"
              f"The recording is safe at {capture_dir}. Resume with:\n"
              f"  python scripts/transfer.py send \"{capture_dir}\"", file=sys.stderr)
    except KeyboardInterrupt:
        print(f"\nUpload interrupted. Resume with:\n"
              f"  python scripts/transfer.py send \"{capture_dir}\"", file=sys.stderr)
    return True


if __name__ == "__main__":
    main()
