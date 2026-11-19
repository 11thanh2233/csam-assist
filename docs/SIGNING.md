# Ký số bộ cài (code signing)

**Mục đích:** để Windows biết bộ cài đến từ ai và không bị sửa sau khi phát hành, nhờ đó hết cảnh báo
"Windows protected your PC".

**Đã chuẩn bị sẵn trong repo:**
- **Quy trình build** (`.github/workflows/windows-installer.yml`) có hai cách ký. Cách nào có khoá thì tự bật; chưa
  có khoá thì build vẫn chạy như cũ.
- **Mục "Chính sách ký số"** trong README, theo yêu cầu của SignPath Foundation.

**Còn cần bạn:** chọn giấy phép cho repo, đăng ký hoặc mua chứng chỉ, rồi thêm khoá vào GitHub.

## Cách 1: SignPath Foundation (miễn phí cho dự án mã nguồn mở)

1. **Chọn giấy phép mã nguồn mở cho repo.** SignPath chỉ nhận dự án có giấy phép được OSI công nhận, ví dụ MIT
   hoặc Apache-2.0. Repo hiện chưa có file `LICENSE`. Bạn quyết định giấy phép nào, mình thêm file.
2. **Kiểm tra điều kiện:**
   - repo công khai;
   - có mục "Chính sách ký số" trong README (đã có, bạn đọc lại và sửa nếu cần);
   - bộ cài được build từ mã nguồn trên GitHub Actions (đã đúng).
3. **Nộp đơn** tại <https://signpath.org> (mục Apply). Khai tên dự án `csam-assist`, link repo và người duy trì
   (`11thanh2233`). Họ duyệt thủ công, có thể mất vài ngày đến vài tuần.
4. **Khi được duyệt, lấy từ trang SignPath:**
   - API token (của một tài khoản "CI user");
   - Organization ID;
   - tên project (slug), ví dụ `csam-assist`;
   - tên chính sách ký (signing policy slug), ví dụ `release-signing`.
5. **Thêm vào GitHub:** repo → *Settings → Secrets and variables → Actions*:
   - mục **Secrets**: `SIGNPATH_API_TOKEN`;
   - mục **Variables**: `SIGNPATH_ORGANIZATION_ID`, và nếu khác mặc định thì `SIGNPATH_PROJECT_SLUG`,
     `SIGNPATH_POLICY_SLUG`.

Từ lần đẩy tag `v...` tiếp theo, bộ cài được gửi SignPath ký trước khi đăng Release. SignPath có thể yêu cầu bạn
bấm duyệt từng lần ký trên trang của họ.

## Cách 2: tự mua chứng chỉ code signing

1. Mua chứng chỉ code signing (OV hoặc EV) từ Sectigo, DigiCert, SSL.com... Giá khoảng vài trăm USD/năm.
   - Từ 2023, chứng chỉ thường được cấp trên USB token hoặc dịch vụ ký trên mây. Loại xuất được file `.pfx` mới
     dùng trực tiếp được với cách này. Loại token hoặc ký trên mây cần chỉnh bước ký theo hướng dẫn của hãng.
2. Đổi file `.pfx` sang base64: trong PowerShell chạy
   `[Convert]::ToBase64String([IO.File]::ReadAllBytes("cert.pfx")) | Set-Clipboard`.
3. Thêm hai secret vào GitHub: `WINDOWS_CERT_PFX` (chuỗi base64 vừa chép) và `WINDOWS_CERT_PASSWORD`.

Từ lần build tiếp theo, cả `SAM3 Assist.exe` lẫn bộ cài đều được ký và đóng dấu thời gian.

## Lưu ý

- **Với chứng chỉ OV**, SmartScreen vẫn có thể cảnh báo vài lần đầu, cho tới khi bộ cài có đủ lượt tải
  ("danh tiếng"). Chứng chỉ EV hết cảnh báo ngay.
- **Chưa ký thì tool vẫn chạy bình thường.** Người dùng chỉ cần bấm *More info → Run anyway*. Mã SHA-256 ghi
  trong mỗi Release giúp kiểm file tải về đúng là bản đó.
