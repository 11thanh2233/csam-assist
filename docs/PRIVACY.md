# Quyền riêng tư / Privacy

*English below.*

SAM3 Label Assist không thu thập dữ liệu sử dụng, không có telemetry, không gửi gì về cho người phát triển.
Ảnh, nhãn và dữ liệu tự học ở trên máy bạn, trừ khi bạn tự bật một tính năng bên dưới.

## App kết nối ra ngoài khi nào

| Kết nối tới | Khi nào | Gửi gì | Tắt được |
|---|---|---|---|
| `api.github.com`, `github.com` (trang Releases của tool) | Kiểm tra bản mới, tối đa mỗi ngày một lần; tải bản cập nhật khi bạn bấm | Không gửi dữ liệu của bạn (yêu cầu thông thường của trình duyệt) | Có: **Cài đặt → Tự kiểm bản mới mỗi ngày** |
| `huggingface.co`, `download.pytorch.org`, `storage.googleapis.com` | Khi bạn bấm tải hoặc cập nhật thành phần (SAM 3, PyTorch, ViTPose, điểm mặt) | Token Hugging Face của bạn (chỉ gửi cho huggingface.co để tải SAM 3) | Có: chỉ chạy khi bạn bấm |
| Các trang CVAT trong danh sách **Trang gán nhãn** | Khi bạn mở CVAT | Như khi bạn dùng CVAT bằng trình duyệt | Có: bạn chọn trang nào |
| Máy khác trong mạng nội bộ | Chỉ khi bạn bật **Chia sẻ backend** hoặc dùng backend của máy khác | Ảnh frame đang gán, để máy kia chạy SAM 3 | Có: tắt mặc định |
| Dịch vụ AI tạo guideline (Anthropic hoặc dịch vụ bạn tự cấu hình) | Chỉ khi bạn dùng tính năng tạo hoặc nhập guideline bằng AI và đồng ý gửi | Ảnh mẫu và nội dung guideline bạn chọn, kèm key của chính bạn | Có: tắt mặc định, chỉ gửi khi bạn đồng ý |

## Lưu trên máy bạn

- Token Hugging Face: lưu trong Windows Credential Manager, không lưu dạng chữ thường trong file.
- Cài đặt, profile, guideline, dữ liệu tự học, nhật ký: thư mục `%LOCALAPPDATA%\SAM3CvatAssist`.
- Gỡ app có hỏi bạn muốn xoá các dữ liệu này hay giữ lại.

## Gói báo lỗi

Gói báo lỗi (trang **Hỗ trợ**) chỉ được tạo trên máy bạn; bạn tự quyết có gửi cho ai không. Gói gồm nhật ký, thông tin
máy và danh sách thành phần. Gói không có token, API key, cookie đăng nhập CVAT hay ảnh. Bạn mở file zip xem trước được.

## Phần mềm bên thứ ba

- Model SAM 3 của Meta chạy trên máy bạn; Meta không nhận dữ liệu từ việc sử dụng (SAM License, mục 2).
- Microsoft Edge WebView2 (hiển thị giao diện) theo chính sách quyền riêng tư của Microsoft.
- Danh sách đầy đủ phần mềm đi kèm và giấy phép: trong app, trang **Giới thiệu**.

---

## English

SAM3 Label Assist collects no usage data, has no telemetry and sends nothing to its developer. Your images, labels and
learning data stay on your computer unless you turn on one of the features below.

| Connects to | When | What is sent | Can be turned off |
|---|---|---|---|
| `api.github.com`, `github.com` (the tool's Releases page) | Checking for a new version, at most once a day; downloading an update when you click | None of your data (an ordinary web request) | Yes: **Settings → Check for a new version daily** |
| `huggingface.co`, `download.pytorch.org`, `storage.googleapis.com` | When you download or update components (SAM 3, PyTorch, ViTPose, face landmarks) | Your Hugging Face token, only to huggingface.co to download SAM 3 | Yes: only runs when you click |
| CVAT sites in your **Labeling sites** list | When you open CVAT | The same as using CVAT in a browser | Yes: you choose the sites |
| Another machine on your local network | Only if you turn on **Share backend** or use another machine's backend | The frame being labeled, so that machine can run SAM 3 | Yes: off by default |
| An AI service for drafting guidelines (Anthropic or one you configure) | Only when you use AI guideline drafting or import and agree to send | The sample images and guideline text you choose, with your own key | Yes: off by default, only sent with your consent |

**Stored on your computer:**

- The Hugging Face token is kept in Windows Credential Manager, not as plain text.
- Settings, profiles, guidelines, learning data and logs are in `%LOCALAPPDATA%\SAM3CvatAssist`.
- The uninstaller asks whether to delete them.

**Error reports:** created only on your computer; you decide whether to send them. A report contains logs, system
information and the component list. It never contains tokens, API keys, CVAT login cookies or images.

**Third-party components:**

- Meta's SAM 3 model runs locally; Meta receives no data from its use (SAM License, section 2).
- Microsoft Edge WebView2 follows Microsoft's privacy policy.
- The full list of bundled software and their licenses is on the app's **About** page.
