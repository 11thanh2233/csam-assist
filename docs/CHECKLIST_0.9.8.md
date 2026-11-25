# Checklist 0.9.8 — job 3D chạy đúng: ảnh camera, Claude, rổ trung thực, RT-DETR, CenterPoint

Nguồn: phản hồi sau 0.9.7 (ảnh 30, 31, job 2495): "Claude AI: bật" dù đã bỏ tick; bật Claude thì lỗi 500
`KeyError: 'center'`; mọi frame "ảnh camera lỗi: name 'lidar_assist' is not defined"; 109/109 track "Tin cậy cao";
nhiều box sai loại (thùng rác, nhà vệ sinh di động -> Trailer; tường -> Construction vehicle); gợi ý từng frame không
có Claude.

Cột: **Làm** = đã code · **Test** = có test tự động · **Lỗi** = lỗi tìm ra khi test · **Sửa**.

## A. Lỗi chặn luồng

| # | Việc | Làm | Test | Lỗi | Sửa |
|---|---|---|---|---|---|
| A1 | `lidar_v2_init` / `lidar_v2_frames` import `lidar_assist` (thiếu -> mất mọi ảnh camera) | ✅ | ✅ `test_session_api_keeps_camera_images` (API thật, zip CAM_FRONT/CAM_BACK) | — | — |
| A2 | Track gửi Claude / bộ đối chiếu có `id`, `center`, `yaw`, `points`, `speed` (`lidar_session.view`); `tracking_3d.build(keep_obs=True)` giữ vật thể từng frame ở `_obs` (không gửi về panel) | ✅ | ✅ `test_real_tracks_feed_claude_without_keyerror` — track THẬT, không track giả | L1 | ✅ |
| A3 | Lỗi một lô / một track không làm phiên trả 500 | ✅ | ✅ `test_claude_failure_never_500_and_explains` | — | — |

## B. Đối chiếu bằng ảnh camera

| # | Việc | Làm | Test | Lỗi | Sửa |
|---|---|---|---|---|---|
| B1 | RT-DETR r18vd (onnx-community, ghim commit `f2bee74…` + sha256 `11843b02…`) tải tự động; backend tìm `SAM3_ONNX2D_PATH` -> `SAM3_RTDETR_MODEL` -> `models/rtdetr_r18vd.onnx` | ✅ | ✅ `test_rtdetr_component_is_a_pinned_file` | — | — |
| B1a | Giải mã theo hình dạng output (bản này trả `[logits, pred_boxes]`), điểm **sigmoid** | ✅ | ✅ test + model thật trên ảnh COCO | L2 | ✅ |
| B1b | Toạ độ box chuẩn hoá nhân cỡ ảnh gốc | ✅ | ✅ test + model thật (mèo [10,56,317,474] trên ảnh 640x480) | L3 | ✅ |
| B1c | Phiên 3D: đối chiếu keyframe rõ nhất CÓ ảnh (thử 2 frame), lặp tới hết track; không ảnh -> `no_image` | ✅ | ✅ 3 test `test_vision_*` | — | — |
| B2 | "Gợi ý cuboid frame này" dùng RT-DETR nếu có (không đụng SAM3) | ✅ | ✅ qua `test_detect_frame_with_claude` | — | — |
| B3 | SAM3 chưa nạp: nạp khi VRAM trống ≥ `SAM3_3D_SAM3_MIN_FREE_MB` (6000), không thì báo lý do | ✅ | ⬜ cần GPU thật | — | — |

## C. Rổ và chất lượng nhãn

| # | Việc | Làm | Test | Lỗi | Sửa |
|---|---|---|---|---|---|
| C1 | Chưa ai kiểm -> "Cần xem" (0.9.7: "Tin cậy cao") | ✅ | ✅ `test_default_bucket_is_honest` + API | — | — |
| C2 | Trailer nhỏ giữ theo guideline, chỉ "Tin cậy cao" khi camera xác nhận hoặc xe kéo ĐÃ xác nhận đứng sát trước; construction vehicle cần camera | ✅ | ✅ `test_small_trailer_needs_confirmed_tow_vehicle` | — | — |
| C3 | Track vụn (≤ 2 frame của job ≥ 8 frame; luôn ít điểm) -> "Nghi rác"; < 15% frame -> "Cần xem" | ✅ | ✅ `test_debris_tracks_go_to_noise` | — | — |
| C4 | Panel nhắc: bỏ tick rồi Ghi thì tool nhớ cỡ, lần sau bỏ tick sẵn | ✅ | — | — | — |

## D. Claude

| # | Việc | Làm | Test | Lỗi | Sửa |
|---|---|---|---|---|---|
| D1 | Chip theo ô tick; panel gửi `use_llm`, backend tôn trọng | ✅ | ✅ `test_userscript_claude_chip_follows_checkbox` + API | — | — |
| D2 | Claude cho từng frame (`/lidar/detect` `use_llm`): bỏ tick / đổi class, chip "Claude ✓ / Claude: bỏ" | ✅ | ✅ `test_detect_frame_with_claude`, `..._off_when_no_key` | — | — |
| D3 | `explain_llm_error`: 401 / 429 / credit / 529 / timeout / JSON / mạng -> một câu dễ hiểu | ✅ | ✅ `test_explain_llm_error_messages` | — | — |

## E. App desktop

| # | Việc | Làm | Test | Lỗi | Sửa |
|---|---|---|---|---|---|
| E1 | Trang Thành phần: RT-DETR (tải), Bộ dò 2D khác (chọn file), CenterPoint (chọn file, dùng thật) | ✅ | ✅ test_v097_phase_b + test_v098 | — | — |
| E2 | Mô tả cần file gì (COCO 80 lớp 640x640; CenterPoint xuất bằng script) | ✅ | — | — | — |
| E3 | Các sửa L20 của 0.9.7 (ONNX không chặn setup, banner sai) đi cùng bản này | ✅ | ✅ | — | — |

## F. Model 3D CenterPoint (nuScenes, nghiên cứu)

| # | Việc | Làm | Test | Lỗi | Sửa |
|---|---|---|---|---|---|
| F1 | `backend/lidar_onnx.py`: pillar 0.2 m trong ±51.2 m, ≤ 20 điểm / pillar, ≤ 40000 pillar, 5 kênh (x, y, z, cường độ 0, sweep 0) -> voxels / num_points / coors[b, z, y, x] | ✅ | ✅ `test_voxelize_shapes_and_coords` | — | — |
| F1a | Giải mã theo `CenterPointBBoxCoder.decode` + `CenterHead.predict_by_feat` (mmdet3d): sigmoid heatmap, `(ô + reg)·4·0.2 − 51.2`, `exp(dim)`, `atan2(sin, cos)`, lọc tâm ±61.2, circle NMS theo `min_radius` từng task | ✅ | ✅ 3 test decode / NMS | — | — |
| F1b | Dời mặt đất về z = −1.84 (nuScenes) rồi dời box về; ánh xạ 10 lớp -> class của tool (traffic_cone bỏ) | ✅ | ✅ `test_centerpoint_detect_maps_class_and_restores_height` | — | — |
| F1c | Chạy qua onnxruntime thật với model tối giản đúng giao diện; file sai giao diện bị từ chối có lý do | ✅ | ✅ 2 test onnxruntime | — | — |
| F1d | Cắm vào `run_geometry(predict=…)` và `/lidar/detect`; frame lỗi -> quay về hình học | ✅ | ✅ `test_session_geometry_uses_model_when_given` | — | — |
| F1e | `scripts/export_centerpoint_onnx.py`: xuất phần mạng từ checkpoint chính thức `centerpoint_02pillar_…_20220811_031844-191a3822.pth` | ✅ | ⬜ cần Linux + mmdet3d (Colab) | — | — |
| F2 | So hình học vs CenterPoint bằng "Tự chỉnh theo cuboid bạn đã đánh" | ⬜ | ⬜ cần file model + dữ liệu thật | — | — |

## Lỗi tìm ra khi làm bản này

- **L1** Test 0.9.7 của `judge_3d` / `lidar_session` tự dựng track giả có sẵn `center` / `id`, nên không bắt được việc
  track thật của `tracking_3d.build` thiếu các khoá đó -> lỗi 500 chỉ lộ ra trên máy người dùng. Test 0.9.8 dựng track
  từ point cloud thật rồi mới gọi Claude / đối chiếu.
- **L2** `lidar_vision._decode` dùng softmax trên 80 lớp; RT-DETR / D-FINE học bằng focal loss (sigmoid từng lớp) -> điểm
  bị bóp nhỏ, gần như không box nào qua ngưỡng. Đổi sang sigmoid, kiểm bằng model thật.
- **L3** `Vision2D.detect` nhân toạ độ chuẩn hoá 0..1 với `w / 640` -> box cỡ 0–1 pixel, không bao giờ chồng khu chiếu.
  Model thật trước khi sửa: mọi box `[0, 0, 0, 1]`; sau khi sửa: đúng vị trí.

## Việc còn lại trên máy của bạn

1. Cập nhật app lên 0.9.8, trang Thành phần -> tải "Lọc rác job 3D bằng ảnh camera (RT-DETR)".
2. Chạy lại job 2495: kỳ vọng có ảnh 6 camera, pha 2/3 chạy hết track, thùng rác / nhà vệ sinh vào "Nghi rác", không
   còn 109/109 "Tin cậy cao".
3. Bật Claude: không còn lỗi 500; lỗi thì một dòng lý do.
4. (Tuỳ chọn) Xuất CenterPoint trên Colab bằng `scripts/export_centerpoint_onnx.py`, chọn file, chạy lại job, so với
   hình học (F2).
5. B3 và F1e cần GPU / Linux thật, chưa kiểm ở đây.
