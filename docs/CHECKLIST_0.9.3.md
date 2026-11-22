# Checklist bản 0.9.3: AI soạn guideline trong app, CVAT local, sửa lỗi chép

Các cột giống `docs/CHECKLIST_0.8.0.md`:

- **Đã thêm**
- **Đã test**
- **Lỗi tìm thấy**
- **Đã sửa**

Ký hiệu: ✅ xong · 🟡 làm một phần (lý do ở cuối).

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| 1 | Cài đặt → AI soạn guideline: Claude hoặc máy chủ tương thích OpenAI, model, địa chỉ | ✅ | ✅ test_v093: kiểm tra đầu vào, backend đọc đúng cấu hình (`from_env`), mở lại app vẫn giữ · control smoke: chọn, lưu, tắt | L4 | ✅ |
| 2 | API key trong Windows Credential Manager, không ở `settings.json`, không gửi lại giao diện | ✅ | ✅ test_v093: không đụng token Hugging Face; ô để trống khi lưu thì xoá key | — | — |
| 3 | Nút Kiểm tra: gửi một câu ngắn, không có ảnh; báo lỗi rõ (ví dụ HTTP 401) | ✅ | ✅ test_v093 (LLM giả) · gọi Anthropic thật: chưa | — | — |
| 4 | Panel chỉ tới mục cài đặt khi AI chưa bật | ✅ | ✅ test_v093, smoke v2 | — | — |
| 5 | Sửa chép chữ báo `OverflowError: int too long to convert` | ✅ | ✅ test_v093: hàm Windows giả, handle 64 bit, giải phóng bộ nhớ khi Windows từ chối · Windows thật: chưa | L1 | ✅ |
| 6 | CVAT local: kiểm tra WSL2, Docker Desktop, Docker chạy, container CVAT, CVAT trả lời | ✅ | ✅ test_v093: từng trường hợp thiếu, wsl.exe in UTF-16, dự án compose tên khác, bỏ container không phải CVAT | — | — |
| 7 | CVAT local: Khởi động (mở Docker Desktop, `docker start` hoặc `docker compose up -d`, đợi CVAT), có nút Dừng | ✅ | ✅ test_v093: thứ tự lệnh, quá giờ, Dừng giữa chừng · control smoke: từ bấm Mở tới mở trang · Windows + Docker thật: chưa | L3 | ✅ |
| 8 | Chọn thư mục CVAT (có `docker-compose.yml`) | ✅ | ✅ test_v093 | — | — |
| 9 | Kiểm tra cả khi app mở thẳng CVAT lúc khởi động, Mở tiếp job, Mở CVAT ở khay | ✅ | ✅ test_v093 | L5 | ✅ |
| 10 | Chữ mới có bản tiếng Anh; hướng dẫn sử dụng, quyền riêng tư cập nhật | ✅ | ✅ test_i18n, control smoke (English không còn tiếng Việt) | — | — |

## Rà phần cũ

| Phần cũ | Thay đổi |
|---|---|
| Biến môi trường `GUIDELINE_LLM`, `ANTHROPIC_API_KEY`… đặt trong Windows | Vẫn dùng được: app chỉ đặt các biến này khi bạn cấu hình trong app |
| Chép từ panel trong cửa sổ CVAT (`set_clipboard` của app) | Dùng chung hàm chép đã sửa với nút chép nhật ký |
| Mutex chạy một bản, tín hiệu đóng màn hình chờ | Khai báo đủ kiểu tham số (cùng loại lỗi handle 64 bit), không dùng `ctypes.windll` chung |
| Trang CVAT trên mạng, trang của máy khác trong mạng nội bộ | Không kiểm Docker: chỉ `localhost` / `127.0.0.1` |
| `settings.json` của bản cũ | Thêm mục `llm`, `cvat_dir`; sai kiểu thì dùng mặc định |
| Gói báo lỗi | Không có API key (key không nằm trong file nào, backend không ghi biến môi trường ra nhật ký) |
| Dòng `TypeError: subprocess.Popen() argument after ** must be a mapping` trong nhật ký | Lỗi của bản 0.8.x (sáng 20/11), đã sửa từ 0.9.0; chỉ còn trong nhật ký cũ. Từ 0.9.2 nhật ký mặc định chỉ hiện lần chạy này |

## Lỗi tìm thấy khi làm và test

| # | Lỗi | Ở đâu | Đã sửa |
|---|---|---|---|
| L1 | Chép chữ báo `ctypes.ArgumentError: OverflowError`: `GlobalLock` / `GlobalUnlock` thiếu khai báo kiểu, handle 64 bit bị ép về 32 bit. Có ở cả nút chép nhật ký và chép từ panel | app desktop | ✅ |
| L2 | Khai báo kiểu trên `ctypes.windll` dùng chung có thể ảnh hưởng thư viện cửa sổ | app desktop | ✅ dùng `WinDLL` riêng |
| L3 | Câu "đang chạy docker compose…" ghép hai đoạn nên không dịch được đoạn sau | CVAT local | ✅ tách hai dòng |
| L4 | Bản đầu luôn đặt `GUIDELINE_LLM=none` khi chưa cấu hình, làm hỏng cách bật bằng biến môi trường của bản cũ | app desktop | ✅ chỉ đặt khi đã cấu hình |
| L5 | Bản đầu gọi hiện cửa sổ trước khi cửa sổ chạy | app desktop | ✅ kiểm tra trước khi tạo cửa sổ |

## Làm một phần

- 🟡 **Windows thật:** chép chữ, mở Docker Desktop và bật container CVAT cần thử trên máy thật có Docker Desktop +
  WSL2 (môi trường test không có Windows).
- 🟡 **Không tự cài WSL2, Docker hay CVAT:** cần quyền admin và khởi động lại máy, nên app chỉ mở hướng dẫn chính thức.
