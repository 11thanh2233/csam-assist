# Checklist bản 0.9.7: triệt tiêu cudaErrorIllegalAddress, spec 3D v2 (hình học + tracking + Claude theo track), phiên theo lô, UI 3 bước

Các cột giống `docs/CHECKLIST_0.9.6.md`:

- **Đã thêm**
- **Đã test**
- **Lỗi tìm thấy**
- **Đã sửa**

Ký hiệu: ✅ xong · 🟡 làm một phần (lý do ở cuối) · ⬜ cần máy thật của bạn (GPU NVIDIA).

Bản này làm theo spec `c_t_k_thu_t_n_ng_c_p_cuboid_3d_v2.md` (Giai đoạn A → D) và nguyên tắc: tool chỉ gợi ý, người
dùng duyệt rồi mới ghi; job 3D không được đụng CUDA context của SAM3.

## A1. CUDA an toàn

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| A1.1 | `runtime_tools.release_for_3d()`: nhả model 2D giữ VRAM (pose, mặt, video, re-ID, depth, wholebody, SAM3) khi vào job 3D | ✅ | ✅ test_v097_phase_a | — | — |
| A1.2 | `_enter_3d()` gọi ở `/lidar/detect`, `/lidar/track` và `/lidar/v2/session/init`; danh sách model nhả ghi vào response | ✅ | ✅ test (endpoint gọi qua `_enter_3d`) | — | — |
| A1.3 | `lidar_assist.engine_ready()` + `check()`: KHÔNG nạp SAM3 giữa request 3D; engine chưa nạp thì trả note, không lỗi | ✅ | ✅ test_v097_phase_a (2 test) | — | — |
| A1.4 | Note "SAM3 chưa nạp" được đưa vào warnings của response để panel hiện | ✅ | ✅ (đường truyền note) | — | — |
| A1.5 | `pick_onnx_providers()`: TensorRT tuỳ chọn → CUDA (gpu_mem_limit = 60% VRAM trống, arena kSameAsRequested) → CPU dự phòng; Windows nạp DLL từ torch/lib + `preload_dlls()` | ✅ | ✅ test (fallback CPU luôn có; cfg CUDA khi có provider) | — | — |
| A1.6 | `check_onnx_conflict()`: cài cả onnxruntime CPU + GPU thì cảnh báo | ✅ | ✅ test (không sập trên máy không có onnxruntime) | — | — |
| A1.7 | Chuỗi 2D → 3D → 2D không phát sinh cudaErrorIllegalAddress trên GPU thật | ⬜ | ⬜ cần máy GPU của bạn trước khi tag | — | — |

## A2. Hình học + tracking 3D

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| A2.1 | `lidar.lshape_fit()`: L-shape theo cạnh bao lồi, chấm điểm = diện tích × phạt độ khít (trung vị khoảng cách điểm tới biên), ngưỡng khít 0.45 m; cụm ≥ 20 điểm mới dùng | ✅ | ✅ test_v097_phase_a (yaw 0/12/30/90/-70/145°, cụm nhỏ → None) | L3 | ✅ |
| A2.2 | `fit_box(…, lshape=True)` theo rule (`lshape: false` để tắt); cụm nhỏ quay về minAreaRect như cũ | ✅ | ✅ test (cụm 12 điểm vẫn ra cuboid) | — | — |
| A2.3 | `_merge_sparse` dùng cKDTree tìm cặp ứng viên (kết quả giống quét mọi cặp 0.9.6) | ✅ | ✅ test đối chiếu brute force 6 lần ngẫu nhiên | L4 | ✅ |
| A2.4 | `LocalGround` bản numba (`_ground_cells` njit) khi có numba và > 2000 điểm; fallback numpy giữ nguyên kết quả | ✅ | ✅ test so grid numba vs numpy (skip khi không cài numba) | — | — |
| A2.5 | `tracking_3d.py`: Kalman hằng vận tốc (x, y) + Hungarian `linear_sum_assignment`; thiếu scipy thì ghép tham lam như cũ | ✅ | ✅ test (ghép đúng cặp chéo; Kf2d predict/update) | L5 | ✅ |
| A2.6 | Khoá kích thước theo frame dày điểm nhất (hoà thì gần cảm biến nhất); lệch > 3×size_tol gắn cờ, không đổi size | ✅ | ✅ test (frame 500 điểm thắng frame 100 điểm) | — | — |
| A2.7 | occluded ≤ N keyframe → nội suy mượt bật occluded; quá N → cắt track mới; frame kết thúc là outside; RÀNG BUỘC CỨNG không bao giờ cả hai trên một keyframe | ✅ | ✅ test (6 frame che vẫn nối, 7 cắt; mọi keyframe kiểm cả hai) | L6 | ✅ |
| A2.8 | `lidar.parse_track_rules()`: đọc từ guideline số keyframe che tối đa, ±% kích thước, class khoá yaw theo bề ngang; `autoprofile` áp vào profile (track_hint của task thắng) | ✅ | ✅ test (guideline mẫu → 6 / 0.05 / barrier) | L7 | ✅ |
| A2.9 | `yaw_by_width`: barrier khoá yaw một hướng cho cả track, không xoay theo chuyển động | ✅ | ✅ test (yaw 0.1f vẫn đứng yên) | L7 | ✅ |
| A2.10 | `/lidar/track` dùng `tracking_3d.build` (output tương thích 100% shape cũ) | ✅ | ✅ test_v094 cũ pass (thuần logic) | — | — |

## B. Thị giác local GPU + calibration

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| B1 | `camera_rig.py`: tầng 1 CVAT/hiệu chỉnh (nhiều dạng metadata, suy fov từ intrinsics) → tầng 2 preset (nuScenes/KITTI/Waymo khớp tên camera) → đoán theo tên; thiếu góc nào bù từ tầng dưới | ✅ | ✅ test_v097_phase_b (10 test) | L8 | ✅ |
| B2 | `camera_rig.calibrate()`: căn từ box mẫu — dò offset (bước 5°) và hệ số góc nhìn, cần ≥ 2 vật khớp; `sam3_search()` dùng SAM3 đã nạp sẵn | ✅ | ✅ test (tìm đúng offset 15°; thiếu vật → note) | — | — |
| B3 | `lidar_assist.cameras_of` chuyển sang `camera_rig.build_rig` (output không đổi) | ✅ | ✅ test camera cũ 0.9.6 pass | — | — |
| B4 | `desktop/components.py`: khai báo `onnx2d`/`onnx3d` + bảng `ONNX_MODELS` kèm giấy phép (Apache-2.0), `use_onnx()` ghi nhận file người dùng chọn, `license_warning()` nhắc hỏi mentor khi giấy phép chưa rõ | ✅ | ✅ test_v097_phase_b | — | — |
| B5 | `lidar_vision.py`: `Vision2D` ONNX (DETR decode, resize, GPU provider có gpu_mem_limit), MockVision cho test | ✅ | ✅ test (decode đúng box/id; rác → rỗng) | L9 | ✅ |
| B6 | Frustum splitting: tách cụm dính theo chóp chiếu hộp 2D; cụm rộng > 3 m mới thử tách; phần dư ≥ 30 điểm thành cụm riêng | ✅ | ✅ test (0° vào hộp, 40° ra ngoài; cụm hẹp không đụng) | L10 | ✅ |
| B7 | 3D-to-2D verification: confirmed (+0.2 tin) / partial / unknown (gắn cờ) / none (bỏ tick ×0.4 + UNCERTAIN_CLASS) | ✅ | ✅ test (box khớp → confirmed; khác class → unknown; apply đổi tin cậy đúng) | L11 | ✅ |

## C. Claude Vision 3D + phiên theo lô

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| C1 | `judge_3d.py`: ảnh BEV đánh số + camera crop kèm #, prompt tiếng Anh sinh từ profile (định nghĩa class + size + range + luật occluded + exclude + lessons) | ✅ | ✅ test (prompt có #1 id=5, moving, range, lessons) | — | — |
| C2 | `ask_llm`: thử lại exponential backoff lỗi mạng/429/5xx, lỗi JSON hỏng báo ngay | ✅ | ✅ test (thử lại 1 lần rồi thành công; cạn retries → LLMError) | — | — |
| C3 | Pha 1 theo lô 6-10 track; class ngoài danh sách bị bỏ, không gán bừa; track Claude sót → needs_review; Claude lỗi → needs_review, không chặn luồng | ✅ | ✅ test (label lạ → None; lỗi → rổ needs_review) | — | — |
| C4 | Pha 2: mâu thuẫn class tool↔Claude gửi audit; Claude lỗi giữ kết quả Pha 1 | ✅ | ✅ test (Car↔Truck chốt thành Truck) | — | — |
| C5 | 3 rổ: suspected_noise > needs_review > high_confidence; `summarize()` đếm cho filter pills | ✅ | ✅ test bucket_of + summarize | — | — |
| C6 | `lidar_session.py`: phiên init/frames/geometry/vision/llm_batch/result/cancel/close; checkpoint từng lô, hủy an toàn GIỮ kết quả, frame trùng bỏ qua, tối đa 8 phiên (dọn cũ nhất) | ✅ | ✅ test_v097_phase_c (lô 2 frame x 2 lần, hủy giữa chừng, frame trùng, evict) | L12 | ✅ |
| C7 | Endpoint `/lidar/v2/session/*` trong app.py (8 route), session_id sai → 404 rõ ràng | ✅ | ✅ py_compile + AST kiểm đủ route | — | — |
| C8 | `run_vision` dùng bộ dò 2D ONNX chọn trong app desktop (env `SAM3_ONNX2D_PATH`); chưa chọn → skipped, không lỗi | ✅ | ✅ (đường skipped) | — | — |
| C9 | `run_llm` không có ANTHROPIC_API_KEY → skipped kèm lý do | ✅ | ✅ (đường skipped) | — | — |

## D. UI/QoL + i18n

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| D1 | Luồng 3 bước ở tab Cả job của job 3D: Bước 1 thiết lập nhanh (chip trạng thái + công tắc Claude + Nâng cao gập kín), Bước 2 một nút chính + tiến độ phân pha (không đếm ngược), Bước 3 filter pills | ✅ | ✅ userscript_3d_logic_check (phaseText, không ước lượng giây) | — | — |
| D2 | Filter pills: Cần xem (x) · Tin cậy cao (x) · Nghi rác (x) · Tất cả (x); mặc định mở tab Cần xem; nút "Chọn hết Tin cậy cao" / "Bỏ chọn Nghi rác" | ✅ | ✅ userscript_3d_logic_check (lidarBuckets) | L13 | ✅ |
| D3 | Thẻ track: ghi chú tiếng Việt thay nhãn cờ kỹ thuật, không lộ mã thô (LOW_VISIBILITY → "Ít điểm LiDAR…"), nút "Tới frame rõ nhất" (ref_frame) + "Tới frame đầu" | ✅ | ✅ userscript_3d_logic_check (trackNote) | — | — |
| D4 | Thanh ghi ghim đáy: "Ghi x track đã chọn vào CVAT" ở renderActions của job 3D | ✅ | ✅ load check + logic | — | — |
| D5 | Ẩn hoàn toàn mục 2D "Gợi ý cho cả job" trong job 3D; giữ ô chọn khoảng frame | ✅ | ✅ load check | — | — |
| D6 | Mini-bar: thu gọn panel ở job 3D vẫn hiện tiến độ pha / số track cần xem | ✅ | ✅ logic check | — | — |
| D7 | Phiên theo lô ở panel: `lidarTrackSession` gọi đúng thứ tự init → frames → geometry (vòng còn remaining) → vision → llm_batch → result → close; lỗi giữa chừng giữ phiên + kết quả đã chạy | ✅ | ✅ load check (đầy đủ cú pháp + tham chiếu) | — | — |
| D8 | Nút "Tạm dừng / Hủy" giữa chừng: đặt stopRequested + gọi cancel, không mất kết quả đã chạy | ✅ | ✅ load check | — | — |
| D9 | i18n: 99 chuỗi mới dịch đủ tiếng Anh; `i18n_extract` báo 0 thiếu; `i18n_build --check` pass | ✅ | ✅ 2450/2450; check OK | — | — |
| D10 | Cú pháp userscript (acorn) + nạp script không lỗi tham chiếu (tests/userscript_load_check.cjs — mới) | ✅ | ✅ PARSE OK + LOAD CHECK PASS | L14 | ✅ |
| D11 | Smoke jsdom đầy đủ (userscript_smoke*, desktop_control_smoke) trên luồng 3 bước | ⬜ | ⬜ sandbox không cài được jsdom (proxy 403) — chạy trên máy của bạn: `npm i jsdom@31 acorn acorn-walk && node tests/userscript_smoke_v2.cjs` | — | — |

## Kiểm thử bắt buộc trước khi commit (spec 3D v2 mục 6)

| # | Mục | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| T1 | Test unit L-shape, khoá ±5%, không bao giờ occluded+outside, cắt track > 6 keyframe | ✅ | ✅ test_v097_phase_a (25 test) | — | — |
| T2 | Test phân lô `/lidar/v2/session/*`: lỗi giữa chừng → giữ tiến độ, thử lại đúng lô | ✅ | ✅ test_v097_phase_c (tầng logic; tầng HTTP cần máy có fastapi) | — | — |
| T3 | Smoke GPU: 2D → 3D → 2D không cudaErrorIllegalAddress, không tràn VRAM | ⬜ | ⬜ máy GPU của bạn (A1.7) | — | — |
| T4 | Job 40 frame tắt LLM: nhanh, không timeout | ⬜ | ⬜ máy thật | — | — |
| T5 | Job 40 frame bật LLM: đủ 3 rổ, phân biệt Car/Truck/Construction_vehicle | ⬜ | ⬜ máy thật + API key | — | — |
| T6 | Chạy `pytest tests/` đầy đủ (~262 test cũ + 70 test mới) trên môi trường có pip | ⬜ | ⬜ sandbox không có mạng để cài pytest/fastapi/scipy — đã chạy 66 test thuần logic mới + 20 test hồi quy cũ qua runner tự chế (shim scipy): pass | — | — |

## Lỗi tìm thấy trong bản này (đã sửa hết)

- **L3** `lshape_fit` chọn khung theo diện tích trần + chấm khít trên đỉnh bao lồi → yaw lệch ~177°. Sửa: chấm khít trên TẤT CẢ điểm cụm, điểm số = diện tích × (1 + 2×độ khít), loại khung span < 0.4 m.
- **L4** `_merge_sparse` bản cKDTree: chỉ mục `own[a]` dùng nhầm làm chỉ số cụm (IndexError khi nhiều cụm). Sửa: bảng `pos` riêng và chuẩn hoá cặp (min, max).
- **L5** `linear_sum_assignment` trên ma trận toàn inf (không có cặp khả thi) báo lỗi. Sửa: lọc hàng/cột có ít nhất một cặp finite trước, inf đổi thành số phạt lớn rồi lọc lại.
- **L6** Kỳ vọng test "đúng 6 frame che thì cắt" sai với spec (≤ N vẫn nối). Sửa test: 6 nối, 7 cắt; đồng thời đoạn track sau phải đủ min_track_frames.
- **L7** Regex `parse_track_rules` không bắt "bị che tối đa 6 keyframe" (thiếu mẫu "bị che … tối đa"); yaw barrier bị xoay theo chuyển động. Sửa regex 3 mẫu và khóa yaw = yaw frame chuẩn cho cả track.
- **L8** `camera_rig.from_meta` bỏ qua camera chỉ có intrinsics; `build_rig` không bù góc thiếu từ preset. Sửa: giữ camera có ít nhất một giá trị, bù yaw/fov từ tầng dưới.
- **L9** `verify` chỉ chạy khi `vision.ready` → MockVision (test) và bộ dò lỗi bị nuốt thành "không thấy". Sửa: gọi `detect` khi có hàm, bọc try/except riêng từng lần dò.
- **L10** `frustum_split` dùng wrap 180° làm dải góc âm dương lệch. Sửa: so trực tiếp min/max của az_lo/az_hi (dải ≤ fov < 180° luôn liên tục).
- **L11** Frustum test dùng góc 5° ngoài dải hộp ±4.4°. Sửa test: góc 0° trong hộp, 40° ngoài.
- **L12** `add_frames` cho gửi lại cùng frame trong MỘT lô (kiểm `have` trước vòng lặp, không cập nhật khi thêm). Sửa: cập nhật `have` sau mỗi frame.
- **L13** `lidarBuckets` bản đầu map sai tên bucket tự suy ('noise'/'review'/'high' không khớp 'suspected_noise'…). Sửa: chuẩn hoá tên bucket trước khi chia rổ.
- **L14** Userscript bị thiếu `GM_registerMenuCommand` trong stub nạp (không phải lỗi script). Sửa shim kiểm nạp `tests/userscript_load_check.cjs`.

## Việc còn lại trên máy thật của bạn (trước khi tag v0.9.7)

1. `pip install -r requirements.txt -r requirements-dev.txt` rồi `python -m pytest -q tests/` — kỳ vọng ~330 test pass (262 cũ + 70 mới; numba không cài được thì test numba tự skip).
2. `npm i jsdom@31 acorn acorn-walk` rồi chạy đủ 6 smoke test node (`userscript_smoke*`, `desktop_*`).
3. Smoke GPU thật: mở job 2D (SAM3) → chuyển job 3D → quay lại job 2D, xác nhận không `cudaErrorIllegalAddress`, không tràn VRAM (mục A1.7/T3).
4. Chạy một job 40 frame tắt LLM và một job bật LLM để xác nhận không timeout và đủ 3 rổ (T4/T5).
5. Quyết của mentor về checkpoint ONNX 3D (PointPillars / CenterPoint) và model 2D (RT-DETR / D-FINE) — bảng `desktop/components.py:ONNX_MODELS` sẵn sàng đổi URL khi chốt.
