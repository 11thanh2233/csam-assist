# Checklist 0.9.12 — Claude ở job 3D: hết lỗi "không đúng định dạng JSON", chọn model + effort

Nguồn: phản hồi sau 0.9.10 (2 ảnh chụp job 2495). Mọi track báo "Claude lỗi: Claude trả lời không đúng định dạng JSON",
kết quả sai và thiếu nhiều. Nhật ký trên máy: model đang cấu hình là `claude-fable-5-2`, các request `llm_batch` đều 200.

Chẩn đoán: tool gọi Claude với `max_tokens=4000`. Fable 5.2 luôn suy nghĩ (effort mặc định high) và phần suy nghĩ tính vào
`max_tokens` → câu trả lời JSON bị cắt (`stop_reason: max_tokens`). Lỗi JSON lại là lỗi cứng (không thử lại), nên mọi lô
đều hỏng và 3 rổ chỉ còn dựa vào hình học. Nhật ký cũ không ghi nội dung trả lời nên chưa xác nhận 100%; từ bản này thì có.

Yêu cầu của người dùng: mặc định Opus 5.5 effort medium; hiện tên model + effort; bỏ giới hạn token; đổi model / effort
bất cứ lúc nào trong Cài đặt; ghi log khi Claude trả lời hỏng.

Cột: **Làm** = đã code · **Test** = có test tự động · **Lỗi** = lỗi tìm ra khi test · **Sửa**.

| # | Việc | Làm | Test | Lỗi | Sửa |
|---|---|---|---|---|---|
| 1 | `AnthropicLLM.complete`: `max_tokens` = mức tối đa của model (128K), bỏ qua giới hạn của nơi gọi; gửi `output_config.effort` | ✅ | ✅ `test_anthropic_uses_max_output_and_effort_not_caller_limit` | — | — |
| 2 | Gọi kiểu streaming (SSE), chỉ giữ khối chữ, đọc `stop_reason` + `usage` (cả `thinking_tokens`); sự kiện lỗi giữa luồng → `LLMError` | ✅ | ✅ `test_read_sse_keeps_text_skips_thinking_and_reads_stop_reason`, `test_read_sse_error_event_raises` | — | — |
| 3 | Model cũ từ chối 128K → tự hạ 64K/32K/16K và nhớ; từ chối effort → gọi lại không effort; lỗi khác (401…) không thử lại | ✅ | ✅ `test_anthropic_lowers_max_tokens_for_older_models`, `test_anthropic_drops_effort_when_model_rejects_it`, `test_other_api_errors_are_not_retried` | — | — |
| 4 | Mặc định `claude-opus-5-5`, effort `medium` (backend và app); `GUIDELINE_LLM_EFFORT=""` = mặc định của model | ✅ | ✅ `test_from_env_defaults_to_opus_medium`, `test_v093` (cập nhật) | — | — |
| 5 | `/llm/config`: đổi model / effort khi backend đang chạy; kiểm tra tên model, mức effort; `/health` báo `guideline_llm.effort` | ✅ | ✅ `test_llm_config_endpoint_changes_live`, `test_configure_changes_model_and_effort_live` | — | — |
| 6 | App: ô Model có gợi ý (Opus / Sonnet / Haiku / Fable), ô Effort; chỉ đổi model / effort thì áp dụng ngay, đổi key / giới hạn / nhà cung cấp thì nhắc bật lại; backend không nhận được thì cũng nhắc bật lại | ✅ | ✅ `test_app_changes_model_and_effort_without_restart`, `test_ui_has_model_and_effort_controls` | — | — |
| 7 | Nút Kiểm tra gọi với effort low (rẻ, nhanh) | ✅ | ✅ `test_v093::test_llm_settings_survive_restart_and_test_button` | — | — |
| 8 | Claude trả lời hỏng: `log_bad_reply` ghi model, effort, `stop_reason`, token (in/out/thinking), 300 ký tự đầu; không ghi key. 3D: thử lại 1 lần, hỏng lần 2 báo kèm `stop_reason`. 2D (tab Gợi ý): ghi log | ✅ | ✅ `test_bad_json_is_logged_then_retried_once`, `test_bad_json_twice_reports_stop_reason`, `test_log_never_contains_api_key`, `test_judge_2d_logs_bad_reply` | — | — |
| 9 | Panel: chip "Claude AI: bật · claude-opus-5-5 · effort medium" (job 3D), dòng "Claude: …" khi bật Claude cho frame, ô "Claude xem ảnh" tab Gợi ý | ✅ | ✅ `test_ui_has_model_and_effort_controls` · smoke JS | — | — |
| 10 | i18n: `scripts/i18n_0912.json`; `i18n_extract --missing` = `{}`; `i18n_build --check` | ✅ | ✅ | — | — |

## Cần máy thật

- [ ] Gọi Claude thật với Opus 5.5 effort medium ở job 3D: không còn "không đúng định dạng JSON"; 3 rổ có lý do của Claude.
- [ ] Đổi model / effort ở Cài đặt khi backend đang chạy: toast "Đã áp dụng ngay"; chip trên panel đổi theo sau khi panel
      hỏi lại `/health` (bấm làm mới hoặc đợi vài giây).
- [ ] Máy đang để `claude-fable-5-2`: model vẫn giữ (Cài đặt chỉ đổi khi bạn chọn); nếu muốn dùng mặc định mới thì xoá ô
      Model rồi Lưu.
- [ ] Nếu vẫn lỗi: mở Hỗ trợ → Nhật ký, tìm dòng `LLM bad reply (3D)` và gửi lại (có `stop_reason`, số token suy nghĩ).
- [ ] Chi phí: Opus 5.5 tính theo token thật sự sinh ra; theo dõi "hôm nay N lượt" và trang Usage của Anthropic.
