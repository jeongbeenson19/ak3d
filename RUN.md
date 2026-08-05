# 실행 명령어 (촬영 → 재건 → 포인트클라우드)

Windows / PowerShell 기준. 최종 출력은 **point cloud** (`data\myscan\scene\integrated_pcd.ply`).

> 사전 준비(최초 1회): `conda`/venv 환경에 `pip install -r requirements.txt`,
> Azure Kinect SDK 설치, MSVC C++ Build Tools 설치. 상세는 [README](README.md) 참고.

---

## 0. 환경 활성화 (매 세션)

```powershell
cd C:\ak3d
.\.venv\Scripts\Activate.ps1
$env:PATH += ";C:\Program Files\Azure Kinect SDK v1.4.2\tools"
$env:PATH += ";C:\Program Files\Azure Kinect SDK v1.4.2\sdk\windows-desktop\amd64\release\bin"
```

- `tools` → `k4arecorder.exe` (녹화)
- `...\release\bin` → `k4a.dll` (pyk4a 런타임)

---

## 1. 촬영 (노트북, Kinect 연결)

```powershell
.\scripts\record.ps1 -Seconds 60
```

- **라이브 RGB 프리뷰 창**을 보면서 촬영 (REC 표시·경과시간·프레임 수 오버레이).
  창에서 **`q` 또는 ESC**로 정지, 또는 `-Seconds`로 자동 정지.
- 촬영마다 **새 타임스탬프 디렉토리**에 저장 → 기존 촬영물 보존:
  `captures\yyyyMMdd_HHmmss\capture.mkv` (경로는 실행 후 출력됨)
- 기본 720p / NFOV / 30fps. 더 세밀한 색이 필요하면 `-ColorResolution 1080p`.
- depth도 같이 보려면 `-ShowDepth`. 저장 위치는 `-Root D:\scans`.

> 프리뷰+녹화는 pyk4a 기반([scripts/record.py](scripts/record.py))이라 venv가 필요합니다
> (`record.ps1`이 자동으로 venv 파이썬과 `k4a.dll` 경로를 잡아줍니다).

**촬영 규칙 (이게 품질의 90%):**

*A. 궤적 — 드리프트/계단 현상 방지*
- **천천히, 부드럽게** 이동 (급회전 금지)
- **시작 지점으로 반드시 되돌아오기** ← loop closure의 핵심 (검증됨)
- 같은 벽/코너를 **겹치게** 여러 번 지나가기 (overlap)

*B. 카메라 방향 — 벽·구멍 문제 방지 (한 바퀴 결과에서 배운 것)*
- **카메라를 수평(정면)으로 유지하고 벽을 향하게.** 천장·바닥만 오래 비추지 말 것.
  벽면은 특징이 풍부해 정합에 유리하고, 천장은 밋밋해 **돔처럼 휘는 왜곡**을 유발.
- **한 높이로 한 바퀴는 벽 전체를 못 담는다** (NFOV 수직 화각이 좁음). 다음 중 하나:
  - **높이를 나눠 여러 바퀴**: ① 눈높이 수평 → ② 위로 살짝 틸트 → ③ 아래로 살짝 틸트,
    각 바퀴마다 시작점 복귀. 바닥→벽→천장을 겹치게 커버.
  - 또는 걸으면서 **위아래로 천천히 틸트**를 섞어 한 번에 상하 커버.
- 천장이 목표가 아니면 천장 비추는 시간을 줄이고 **벽 위주로**.
- 벽이 아주 높은 공간이면 **WFOV 고려**(`-DepthMode WFOV_UNBINNED`, 수직 화각 넓음).

*C. 환경*
- 대상과 **1~3m** 거리 유지 (NFOV 유효 범위)
- 창문·거울은 가리기 (반사 → depth 오류)
- 흰 벽/밋밋한 면엔 임시 특징물(포스터·물건) 배치

> ⚠️ k4aviewer 등 다른 앱이 장치를 잡고 있으면 녹화 실패("device unavailable").
> 먼저 종료: `Stop-Process -Name k4aviewer -Force`

---

## 2. 추출 (mkv → color/depth/intrinsic)

```powershell
python scripts\extract_mkv.py --input captures\<TIMESTAMP>\capture.mkv --output data\myscan --every 1
```

- `--input`은 1번에서 출력된 실제 캡처 경로 (예: `captures\20260722_150405\capture.mkv`).
- `--every 1` = 모든 프레임 (촘촘 = 정합 유리). 빠르게 볼 땐 `--every 2~3`.
- 결과: `data\myscan\{color, depth, intrinsic.json}`

---

## 3. 재건 (Open3D Reconstruction System)

`-Dataset`으로 촬영시각 폴더를 지정 → 중간물·결과·로그가 **모두 그 폴더 안**에 저장됨:

```powershell
.\scripts\reconstruct.ps1 -Dataset data\myscan\<TIMESTAMP>
```

- 최초 실행 시 Open3D를 자동 clone 후 `--make --register --refine --integrate` 순차 실행
- 중간 산출물: `data\myscan\<TIMESTAMP>\fragments\`, `...\scene\`
- 최종 메시: `data\myscan\<TIMESTAMP>\scene\integrated.ply`
- **실행 로그: `data\myscan\<TIMESTAMP>\reconstruct.log`** (실시간 표시 + 파일 저장)
- 자동생성 설정: `data\myscan\<TIMESTAMP>\config.json`
- 일부 단계만: `... -Stages make,register`

**소요 시간 주의:** 수백 프레임이면 수십 분+ (CPU). **전원 어댑터 연결 필수.**
빠르게 하려면 추출을 `--every 2~3`로 줄이거나 [config.json](config.json)의 `voxel_size`를 키움.

---

## 4. 포인트클라우드 추출 (★ 최종 산출물)

표준 `--integrate`는 메시를 내므로, 정점을 포인트클라우드로 변환:

```powershell
python scripts\export_pointcloud.py --input data\myscan\<TIMESTAMP>\scene\integrated.ply --output data\myscan\<TIMESTAMP>\scene\integrated_pcd.ply
```

- 결과: `data\myscan\<TIMESTAMP>\scene\integrated_pcd.ply`
- 용량 줄이려면 다운샘플: 위 명령에 `--voxel 0.01` (1cm) 추가

---

## 5. 확인

```powershell
python scripts\view_result.py --path data\myscan\<TIMESTAMP>\scene\integrated_pcd.ply
```

VS Code에서 보려면 확장 `kleinicke.ply-visualizer` (대용량은 `view_result.py`가 안정적).

**품질 체크:**
- 계단 현상 → loop closure 실패 (2번 촬영 규칙대로 재촬영)
- 흰 벽 어긋남 → 특징 부족 (임시 마커)
- 구멍 → occlusion (구조적 한계)

---

## 파라미터 튜닝 ([config.json](config.json))

| key | 값 | 효과 |
|-----|-----|------|
| `n_frames_per_fragment` | 40 | fragment 작게 → fragment 간 정합 기회↑ (loop edge↑). 현재값 |
| `voxel_size` | 0.05 | 작을수록 정밀·느림 (BIM은 0.02~0.03) |
| `max_depth` | 3.0 | 유효 depth 상한(m). 바닥 짤리면 조정 |

`n_frames_per_fragment` 변경 시 `--make`부터 다시 (fragment 재생성).
