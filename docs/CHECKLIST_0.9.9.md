# Checklist 0.9.9 — SAM3 ở job 3D không còn đòi cứng 6 GB VRAM

Nguồn: câu hỏi sau 0.9.8: "tool có cứng nhắc trong việc VRAM phải trống từ 6 GB trở lên không?". Có: ngưỡng 6000 MB
cố định, app desktop lọc bỏ mọi biến `SAM3_*` của Windows nên không đổi được, card 6–8 GB gần như không bao giờ đủ.
Chỉ ảnh hưởng khi chưa cài RT-DETR (có RT-DETR thì đối chiếu camera không đụng SAM3).

Cột: **Làm** = đã code · **Test** = có test tự động · **Lỗi** = lỗi tìm ra khi test · **Sửa**.

| # | Việc | Làm | Test | Lỗi | Sửa |
|---|---|---|---|---|---|
| 1 | Ngưỡng theo card: `min(3500, 55% tổng VRAM)`; `SAM3_3D_SAM3_MIN_FREE_MB` > 0 thì dùng số đó | ✅ | ✅ `test_need_follows_card_size`, `test_auto_loads_on_6gb_card_with_4gb_free` | — | — |
| 2 | Chế độ `SAM3_3D_SAM3_MODE` = auto / always / off; Cài đặt app → Backend và GPU → "SAM3 ở job 3D" (mặc định Tự động), lưu `settings.json`, truyền cho backend | ✅ | ✅ `test_always_*`, `test_off_*`, `test_desktop_*`, `desktop_control_smoke.cjs` | — | — |
| 3 | Thiếu VRAM khi nạp hoặc khi SAM3 xem ảnh: nhả SAM3, dọn VRAM, báo một dòng, vẫn trả cuboid; lỗi khác không bị nuốt | ✅ | ✅ `test_oom_while_loading_releases_and_reports`, `test_camera_check_frame_survives_oom_inside_sam3`, `test_camera_check_frame_other_errors_still_raise` | — | — |
| 4 | Panel ghi rõ lọc bằng gì: `/health` → `lidar3d.sam3_mode`, kết quả frame → `camera.engine` (rtdetr / sam3 / skip); dòng "Lọc rác bằng ảnh" ở tab Gợi ý | ✅ | ✅ `test_health_reports_sam3_mode`, `test_camera_check_frame_marks_skip_and_sam3`, `test_panel_line_names_the_filter` | — | — |
| 5 | i18n: 18 chuỗi mới (`scripts/i18n_099.json`, nạp bằng `scripts/i18n_add_098.py scripts/i18n_099.json`) | ✅ | ✅ `i18n_extract --missing` = `{}`, `i18n_build --check` | — | — |

## Cần máy thật

- [ ] Card 6 GB, chưa cài RT-DETR, chế độ Tự động: "Gợi ý cuboid frame này" có ảnh camera → SAM3 được nạp khi VRAM
      trống ≥ ~3.4 GB, dòng panel ghi "Lọc rác bằng ảnh: SAM3".
- [ ] Chế độ "Luôn nạp khi tôi tick" khi đang mở game / app nặng GPU: nếu thiếu VRAM, panel báo "Thiếu VRAM khi …",
      cuboid vẫn hiện, backend không tắt.
- [ ] Chế độ "Không dùng SAM3 ở job 3D": panel báo đã tắt, không có SAM3 trong VRAM (bảng điều khiển → Model).
