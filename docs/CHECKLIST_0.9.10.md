# Checklist 0.9.10 — phiên 3D tiếp tục được từ chỗ dừng

Nguồn: sau 0.9.9, job 3D 40 frame bị hết giờ ở "Pha 3/3: Claude nhận diện & kiểm duyệt…"; bấm "Tạm dừng / Hủy" thì
panel báo "kết quả đã chạy vẫn giữ" nhưng không có nút nào để chạy tiếp. Nguyên nhân:

- Pha 3 là MỘT request `llm_batch` cho mọi track (backend gọi Claude lần lượt từng lô 8 track trong cùng request),
  panel chờ tối đa 300 giây.
- Vòng gọi Claude ở backend không kiểm cờ dừng, nên "Tạm dừng" phải đợi hết request.
- Lỗi giữa chừng: phiên còn trên backend nhưng panel không có nút "Thử lại lô lỗi" (chỉ có chú thích trong code).

Cột: **Làm** = đã code · **Test** = có test tự động · **Lỗi** = lỗi tìm ra khi test · **Sửa**.

| # | Việc | Làm | Test | Lỗi | Sửa |
|---|---|---|---|---|---|
| 1 | `run_llm(max_batches, retry_failed)`: chạy từng lô Pha 1, tích luỹ trong phiên (`llm_p1`), Pha 2 một lần khi đủ; `max_batches=None` chạy hết như cũ | ✅ | ✅ `test_llm_one_batch_per_call_until_final`, `test_llm_without_max_batches_runs_everything_like_before` | — | — |
| 2 | Dừng giữa các lô Claude: phần đã chấm giữ lại, chạy tiếp chỉ gửi track còn thiếu (mỗi track đúng một lần) | ✅ | ✅ `test_cancel_between_batches_keeps_judged_and_resume_only_runs_rest` | — | — |
| 3 | Lô Claude lỗi: track đó vào Cần xem kèm lý do, các lô khác vẫn chạy; "Tiếp tục" chấm lại track lỗi | ✅ | ✅ `test_claude_error_in_one_batch_keeps_others_and_retry_rejudges` | — | — |
| 4 | Geometry không có frame mới: KHÔNG dựng lại track (trước đây dựng lại làm mất kết quả đối chiếu camera); có frame mới thì bỏ kết quả Claude cũ (số hiệu track đổi) | ✅ | ✅ `test_geometry_without_new_frames_keeps_camera_and_claude_results`, `test_new_frames_reset_stale_checks` | — | — |
| 5 | `GET /lidar/v2/session/{sid}/progress` (frame đã nhận, đã dựng, track đã đối chiếu, đã qua Claude); 404 khi phiên mất. `vision` / `llm_batch` xoá cờ dừng như `geometry` | ✅ | ✅ `test_progress_reports_each_step`, `test_api_llm_batch_step_by_step_then_resume_after_cancel`, `test_api_rejects_bad_max_batches` | — | — |
| 6 | Panel: Claude gửi `max_batches: 1` mỗi request, tiến độ "Pha 3/3: Claude … (16/40 track)"; "Tạm dừng" dừng sau lô đang chạy | ✅ | ✅ `userscript_3d_logic_check.cjs` (luồng với backend giả) | — | — |
| 7 | Panel: dừng / hết giờ / lỗi mạng → khung "Đã tạm dừng… Dừng ở: …" + nút **Tiếp tục từ chỗ dừng**, **Xem kết quả đã có**, **Bỏ phiên này**; nút chính đổi thành "Chạy lại từ đầu" | ✅ | ✅ logic check + `test_panel_has_resume_controls` | — | — |
| 8 | Tiếp tục chỉ chạy phần thiếu: không init lại, không gửi lại frame backend đã có, geometry / camera / Claude chỉ phần còn lại | ✅ | ✅ logic check "tiếp tục: không tạo phiên mới, không gửi lại frame" | — | — |
| 9 | Mã phiên nhớ theo job (GM `lidarSess`, 2 ngày): F5 vẫn thấy phiên tạm dừng; backend khởi động lại (404) → chạy lại từ đầu, báo rõ | ✅ | ✅ logic check "tải lại trang", "phiên mất trên backend" | — | — |
| 10 | i18n: 19 chuỗi mới (`scripts/i18n_0910.json`) | ✅ | ✅ `i18n_extract --missing` = `{}`, `i18n_build --check` | — | — |

## Cần máy thật

- [ ] Job 3D 40 frame, bật Claude: Pha 3 hiện "(8/N track)", "(16/N track)"… và không còn báo hết giờ.
- [ ] Bấm "Tạm dừng / Hủy" giữa Pha 3: trong khoảng một lô (vài chục giây) panel hiện khung tạm dừng; "Xem kết quả đã
      có" ra danh sách track; "Tiếp tục từ chỗ dừng" chạy nốt, không gửi lại point cloud.
- [ ] F5 giữa chừng: mở lại job thấy khung tạm dừng, bấm tiếp tục chạy nốt.
- [ ] Tắt / bật lại backend rồi bấm tiếp tục: panel báo phiên cũ không còn và chạy lại từ đầu.
