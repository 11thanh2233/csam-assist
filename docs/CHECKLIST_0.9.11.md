# Checklist 0.9.11 — shape từng frame ở job 3D, Claude đọc guideline, nạp được CenterPoint từ Colab

Nguồn: sau 0.9.10:
- "Tab Gợi ý chỉ đánh shape cho từng frame, tab Cả job chỉ ra track, không có shape cho cả job."
- "Muốn Claude đọc cả guideline để bước hỗ trợ chính xác hơn; job chưa có guideline thì sao?"
- File CenterPoint xuất trên Colab báo `MatMulBnFusion_Gemm ... First input does not have rank 2` khi nạp; Colab dùng
  Python 3.13 nên hướng dẫn `mim install` cũ không chạy.

Cột: **Làm** = đã code · **Test** = có test tự động · **Lỗi** = lỗi tìm ra khi test · **Sửa**.

## A. Model CenterPoint

| # | Việc | Làm | Test | Lỗi | Sửa |
|---|---|---|---|---|---|
| A1 | `CenterPointONNX.load`: tối ưu mặc định lỗi thì nạp lại với `ORT_DISABLE_ALL`; cả hai lỗi thì báo lỗi lần đầu, dùng hình học | ✅ | ✅ `test_model3d_retries_without_graph_optimization`, `test_model3d_normal_file_keeps_default_optimization`, `test_model3d_broken_file_reports_first_error` | — | — |
| A2 | `/health` → `lidar3d.model3d_error`; panel ghi "model 3D lỗi — đang dùng hình học" kèm lý do | ✅ | ✅ `test_health_reports_model3d_error`, `test_health_reports_3d_engines` | — | — |
| A3 | Hướng dẫn xuất: Python 3.10 bằng uv, gọi `/content/py310/bin/python`, chạy CPU; bước tự kiểm nạp như backend; notebook `export_centerpoint_onnx.ipynb` | ✅ | ✅ `test_export_script_and_notebook` | — | — |

## B. Ghi "Shape từng frame"

| # | Việc | Làm | Test | Lỗi | Sửa |
|---|---|---|---|---|---|
| B1 | `tracking_3d.build(keep_obs)` giữ `_frames` (mọi frame, `seen` = thấy thật / nội suy), kích thước khoá | ✅ | ✅ `test_build_keeps_every_frame_with_seen_flag` | — | — |
| B2 | `result(per_frame)` trả `frames` mỗi track + `singles` (vật 1 frame); label Claude sửa vẫn giữ; `/result` nhận `per_frame` | ✅ | ✅ `test_result_per_frame_and_singles`, `test_result_per_frame_keeps_claude_label`, `test_api_result_per_frame` | — | — |
| B3 | Panel: "Ghi dạng: Track / Shape từng frame" (mặc định theo loại job), "Ghi cả frame nội suy", nút ghi đếm cuboid / frame | ✅ | ✅ `test_panel_has_write_mode_and_guideline_line` | — | — |
| B4 | `tracksToShapes`: bỏ frame nội suy (trừ khi chọn), bỏ track không tick, bỏ cuboid trùng (cùng label, < 1 m), 16 số / cuboid; `applyLidarShapes` ghi theo lô 200, hoàn tác cả lần ghi | ✅ | ✅ `userscript_3d_logic_check.cjs` (6 kiểm) | — | — |
| B5 | Vật 1 frame: rổ Nghi rác, không tick; ghi dạng track bỏ qua chúng | ✅ | ✅ logic check "vật 1 frame" | — | — |

## C. Claude đọc guideline

| # | Việc | Làm | Test | Lỗi | Sửa |
|---|---|---|---|---|---|
| C1 | `guideline_context`: định nghĩa label của task + đoạn liên quan (~6.000 ký tự) / cả guideline (~20.000) / không gửi; so chữ đã bỏ dấu, khớp nguyên từ | ✅ | ✅ `test_guideline_related_has_definitions_and_relevant_sections`, `test_guideline_full_and_off` | L1 | ✅ |
| C2 | Pha 1, Pha 2, gợi ý từng frame gửi guideline ở system, giống hệt nhau mọi lô; không có guideline thì prompt như cũ | ✅ | ✅ `test_phases_send_guideline_as_context_same_for_every_batch`, `test_no_guideline_prompt_unchanged` | — | — |
| C3 | Anthropic: guideline là khối system riêng có `cache_control: ephemeral`; OpenAI-compatible: ghép vào system | ✅ | ✅ `test_anthropic_caches_guideline_block` | — | — |
| C4 | Tìm guideline của job từ profile: `guideline_id` (profile của project), `gl_<id>`, tên file; không đọc ngoài thư mục guideline | ✅ | ✅ `test_guideline_found_from_profile` (3 kiểu), `test_no_guideline_for_job` | — | — |
| C5 | Panel: dòng "Claude đọc guideline: …" + Đoạn liên quan / Cả guideline / Không gửi (cả tab Gợi ý và Cả job); chưa có guideline thì nhắc + nút mở tab Guideline | ✅ | ✅ `test_panel_has_write_mode_and_guideline_line` | — | — |
| C6 | `docs/PRIVACY.md`: dòng Claude ở job 3D (gửi gì, cách tắt) | ✅ | — | — | — |
| C7 | i18n: chuỗi mới (`scripts/i18n_0911.json`) | ✅ | ✅ `i18n_extract --missing` = `{}`, `i18n_build --check` | — | — |

**L1** Bản đầu chấm điểm đoạn theo số lần nhắc từ khoá: đoạn "Quy định chung" dài 400 dòng chiếm hết chỗ, đoạn "Car"
ngắn bị bỏ; từ khoá "rác" khớp nhầm giữa từ. Sửa: điểm theo mật độ (số lần nhắc / độ dài) + điểm cao khi tên label nằm ở
tiêu đề, mỗi đoạn tối đa 1.800 ký tự, khớp nguyên từ trên chữ đã bỏ dấu.

## Cần máy thật

- [ ] Chọn file `centerpoint_pillar02_nus.onnx` (xuất trên Colab) ở trang Thành phần: dòng panel ghi "Dựng cuboid: model
      3D (CenterPoint)", log backend có "nạp với tối ưu đồ thị tắt".
- [ ] Job 3D ảnh rời: chạy cả job, "Ghi dạng: Shape từng frame", ghi; mở vài frame trong CVAT thấy cuboid; "Hoàn tác lần
      ghi gần nhất" xoá hết.
- [ ] Job có guideline đã nạp: dòng "Claude đọc guideline: <tên>" hiện; lý do Claude ghi ở track có nhắc luật guideline.
- [ ] Job chưa có guideline: dòng nhắc nạp ở tab Guideline, Claude vẫn chạy.
