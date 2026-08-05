# ak3d — Azure Kinect → Open3D room reconstruction

Lightweight indoor 3D reconstruction from an Azure Kinect DK recording, using the
standard **Open3D Reconstruction System**. Same structure as Figure 6 of the
Sensors 2022 paper (`preprocessing → Open3D pipeline`) — no CUDA custom kernels,
no ORB-SLAM2 bindings to build.

Roles are split: **laptop** records, **workstation** reconstructs.

> 🔰 처음이라면 → **[docs/GUIDE.md](docs/GUIDE.md)** : 기초 개념부터 스크립트·실행
> 절차까지 중학생도 이해할 수 있게 설명한 한국어 가이드.

## Pipeline

```
 laptop                         workstation
 ┌─────────────┐   output.mkv   ┌──────────────────────────────────────────────┐
 │ k4arecorder │ ────────────►  │ extract_mkv.py                                │
 │  (Step 1)   │                │   mkv → color/ depth/ intrinsic.json (Step 3) │
 └─────────────┘                │        │                                      │
                                │        ▼                                      │
                                │ run_system.py  (Steps 4–5)                    │
                                │   --make      fragments (RGBD odometry)       │
                                │   --register  global registration (RANSAC)    │
                                │   --refine    pose-graph refine + loop closure│
                                │   --integrate TSDF → scene/integrated.ply     │
                                │        │                                      │
                                │        ▼  view_result.py (Step 6)             │
                                └──────────────────────────────────────────────┘
```

The Open3D Reconstruction System has 4 stages: fragment creation (RGBD odometry) →
fragment registration (global) → pose-graph optimization → TSDF integration.

## Setup

**Native Azure Kinect SDK** is required on both machines (recording on the laptop,
`pyk4a` decoding on the workstation).

- Windows: install *Azure Kinect SDK 1.4.x* (gives `k4arecorder.exe`) and, for body/
  playback, the runtime. Reopen the shell so `k4arecorder.exe` is on `PATH`.
- Ubuntu 20.04:
  ```bash
  curl -sSL https://packages.microsoft.com/keys/microsoft.asc | sudo apt-key add -
  sudo apt-add-repository https://packages.microsoft.com/ubuntu/20.04/prod
  sudo apt update
  sudo apt install libk4a1.4 libk4a1.4-dev k4a-tools
  ```

**Python env** (workstation):
```bash
conda create -n o3d python=3.10 && conda activate o3d
pip install -r requirements.txt
```
The pip `open3d` wheel is enough — the reconstruction pipeline itself does not need
a from-source k4a build. `pyk4a` is only used to pull color/depth/intrinsics out of
the `.mkv`.

## Usage

### Step 1 — Record (laptop)
```powershell
.\scripts\record.ps1 -Seconds 60
```
Opens a **live RGB preview window** (pyk4a-based, [scripts/record.py](scripts/record.py))
so you see what you're capturing; press `q`/ESC to stop, or use `-Seconds`. Each
capture is written to a new timestamped directory —
`captures\yyyyMMdd_HHmmss\capture.mkv` — so earlier recordings are never
overwritten. The exact path is printed when recording finishes. Add `-ShowDepth`
for a depth window. (The preview needs the venv + pyk4a, not just the SDK.)
**Capture rules** (see [docs/GUIDE.md §9](docs/GUIDE.md) for the full version):
- Move **slowly**, **overlap** passes, and **return to the start** to close the loop
  (this fixed the staircase drift).
- Keep the camera **level, facing walls** — don't dwell on ceiling/floor. A single
  level loop leaves **holes in the walls** and a **domed/warped ceiling** (narrow
  NFOV vertical FOV + featureless ceiling). Do **multiple loops at different tilts**
  (level → tilt up → tilt down), or mix up/down tilt while walking.
- **NFOV vs WFOV**: NFOV has better depth range but a narrow vertical FOV; use
  `-DepthMode WFOV_UNBINNED` for tall rooms (wider vertical FOV).
- Keep 1–3 m from surfaces; **cover windows/mirrors** (reflections corrupt depth);
  add temporary texture to blank white walls.

Copy the capture directory to the workstation.

### Step 3 — Extract mkv → dataset (workstation)
```bash
python scripts/extract_mkv.py --input captures/<TIMESTAMP>/capture.mkv --output data/myscan
```
Produces `data/myscan/{color,depth,intrinsic.json}`. Depth is aligned to the color
camera, so `intrinsic.json` (the color intrinsics) is emitted automatically — no
manual intrinsic copying. Use `--every 2` to keep every 2nd frame for speed.

> Step 2 in the split is just "prepare the env" (see Setup). Numbering follows the
> original recipe.

### Steps 4–5 — Reconstruct
```powershell
.\scripts\reconstruct.ps1
```
Clones Open3D on first run, then executes `--make --register --refine --integrate`.
Run a subset while debugging, e.g. `-Stages make,register`. Intermediate output goes
to `data/myscan/fragments/` and `data/myscan/scene/`.

Ubuntu equivalent:
```bash
git clone --depth 1 https://github.com/isl-org/Open3D.git
python Open3D/examples/python/reconstruction_system/run_system.py \
    --config config.json --make --register --refine --integrate
```

### Step 6 — Inspect
```bash
python scripts/view_result.py            # opens data/myscan/scene/integrated.ply
```

## Tuning (`config.json`)

| key                    | meaning                          | notes |
|------------------------|----------------------------------|-------|
| `voxel_size`           | TSDF / downsample resolution     | 0.05 (5 cm) for room scale; 0.02–0.03 for BIM precision (slower) |
| `max_depth`            | max usable depth (m)             | ~3.0 given NFOV effective range (~3.86 m) |
| `icp_method`           | refinement ICP variant           | `color` uses photometric + geometric |
| `global_registration`  | fragment matching                | `ransac` (feature-based) |
| `tsdf_cubic_size`      | TSDF volume extent (m)           | bound the scene box |

## Failure checklist (from the paper)

- **Staircase effect** → loop closure failed; trajectory too complex, or window
  reflections. Re-shoot with a simpler path / covered windows.
- **Misalignment on blank white walls** → featureless regions; add temporary
  markers/stickers next time.
- **Holes from occlusion** → structural limit of this method; not fully removable.

## Notes

This path is CPU-heavy (ICP/RANSAC), low GPU dependence — a single RTX 3090 is
plenty. Keep the 4090 for later RTG-SLAM comparison experiments.

## Layout

```
config.json              reconstruction parameters
requirements.txt         workstation Python deps
scripts/
  record.ps1             Step 1  k4arecorder wrapper (laptop)
  extract_mkv.py         Step 3  mkv → Open3D dataset
  reconstruct.ps1        Steps 4–5  clone Open3D + run 4 stages
  view_result.py         Step 6  visualize the mesh
data/                    datasets & outputs (gitignored)
Open3D/                  cloned on demand (gitignored)
```
