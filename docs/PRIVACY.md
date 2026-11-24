# Quyền riêng tư / Privacy

*English below.*

SAM3 Label Assist không thu thập dữ liệu sử dụng, không có telemetry, không gửi gì về cho người phát triển.
Ảnh, nhãn và dữ liệu tự học ở trên máy bạn, trừ khi bạn tự bật một tính năng bên dưới.

## App kết nối ra ngoài khi nào

| Kết nối tới | Khi nào | Gửi gì | Tắt được |
|---|---|---|---|
| `api.github.com`, `github.com` (trang Releases của tool) | Kiểm tra bản mới, tối đa mỗi ngày một lần; tải bản cập nhật khi bạn bấm | Không gửi dữ liệu của bạn (yêu cầu thông thường của trình duyệt) | Có: **Cài đặt → Tự kiểm bản mới mỗi ngày** |
| `huggingface.co`, `download.pytorch.org`, `storage.googleapis.com`, `download.openmmlab.com` | Khi bạn bấm tải hoặc cập nhật thành phần (SAM 3, PyTorch, ViTPose, điểm mặt, trọng số khởi đầu cho model riêng; 0.9.6: nhận lại người DINOv2, độ sâu Depth Anything V2, pose toàn thân RTMW) | Token Hugging Face của bạn (chỉ gửi cho huggingface.co để tải SAM 3) | Có: chỉ chạy khi bạn bấm |
| Các trang CVAT trong danh sách **Trang gán nhãn** | Khi bạn mở CVAT | Như khi bạn dùng CVAT bằng trình duyệt | Có: bạn chọn trang nào |
| Máy khác trong mạng nội bộ | Chỉ khi bạn bật **Chia sẻ backend** hoặc dùng backend của máy khác | Ảnh frame đang gán, để máy kia chạy SAM 3 | Có: tắt mặc định |
| Dịch vụ AI tạo guideline (Anthropic hoặc dịch vụ bạn tự cấu hình) | Chỉ khi bạn dùng tính năng tạo hoặc nhập guideline bằng AI, hoặc tạo Profile cho project có tick "Cho AI đọc guideline", và đồng ý gửi | Ảnh mẫu và nội dung guideline bạn chọn (Profile cho project: chỉ văn bản guideline và tên label, không gửi ảnh), kèm key của chính bạn | Có: tắt mặc định, chỉ gửi khi bạn đồng ý |
| Claude xem ảnh (Anthropic, 0.9.5) | Mỗi lần gợi ý nhãn khi app có Anthropic API key và ô "Claude xem ảnh" ở tab Gợi ý đang bật (bật sẵn khi có key) | Ảnh frame thu nhỏ có đánh số đề xuất, vài ảnh cắt nhỏ quanh vật nhỏ (mặt, tay…), tên file của frame, tên label, thuộc tính, ghi chú guideline, bài học và các lần sửa của project (chữ, không có ảnh cũ), kèm key của chính bạn | Có: bỏ tick ô "Claude xem ảnh", hoặc xoá key trong Cài đặt của app. Có giới hạn số lần mỗi ngày |

## Lưu trên máy bạn

- Trí nhớ của project (0.9.5): giá trị bạn chốt, quy ước tên file, bài học. Chỉ là chữ, không lưu ảnh, nằm trong thư
  mục dữ liệu của app (`guideline_library/memory`).
- Job cuboid 3D (0.9.6): class và kích thước của cuboid bạn bỏ khi ghi (để lần sau bỏ tick sẵn), cùng chỗ trên.
  Ảnh camera của job 3D chỉ gửi tới backend của bạn để SAM3 đối chiếu, không lưu lại.

- Token Hugging Face và API key của AI soạn guideline: lưu trong Windows Credential Manager, không lưu dạng chữ thường
  trong file.
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
| `huggingface.co`, `download.pytorch.org`, `storage.googleapis.com`, `download.openmmlab.com` | When you download or update components (SAM 3, PyTorch, ViTPose, face landmarks, custom model starting weights; 0.9.6: DINOv2 re-identification, Depth Anything V2 depth, RTMW whole-body pose) | Your Hugging Face token, only to huggingface.co to download SAM 3 | Yes: only runs when you click |
| CVAT sites in your **Labeling sites** list | When you open CVAT | The same as using CVAT in a browser | Yes: you choose the sites |
| Another machine on your local network | Only if you turn on **Share backend** or use another machine's backend | The frame being labeled, so that machine can run SAM 3 | Yes: off by default |
| An AI service for drafting guidelines (Anthropic or one you configure) | Only when you use AI guideline drafting or import, or create a Project profile with "Let AI read the guideline" ticked, and agree to send | The sample images and guideline text you choose (Project profile: only the guideline text and label names, no images), with your own key | Yes: off by default, only sent with your consent |
| Claude image review (Anthropic, 0.9.5) | Each label suggestion while the app has an Anthropic API key and the "Claude reviews the image" box in the Suggest tab is on (on by default once a key is set) | A downscaled frame with numbered proposals, a few small crops around small objects (faces, hands…), the frame's file name, label names, attributes, guideline notes, and the project's lessons and past corrections (text, no earlier images), with your own key | Yes: untick "Claude reviews the image", or delete the key in the app's Settings. A daily call limit applies |

**Stored on your computer:**

- The Hugging Face token and the AI guideline drafting API key are kept in Windows Credential Manager, not as plain
  text.
- Settings, profiles, guidelines, learning data and logs are in `%LOCALAPPDATA%\SAM3CvatAssist`.
- Project memory (0.9.5): the values you settle on, file-name conventions and lessons. Text only, no images.
- Cuboid 3D jobs (0.9.6): class and size of cuboids you drop when saving (so they are unticked next time), same
  place. Camera images of 3D jobs only go to your own backend for SAM3 to check; they are not stored.
- The uninstaller asks whether to delete them.

**Error reports:** created only on your computer; you decide whether to send them. A report contains logs, system
information and the component list. It never contains tokens, API keys, CVAT login cookies or images.

**Third-party components:**

- Meta's SAM 3 model runs locally; Meta receives no data from its use (SAM License, section 2).
- Microsoft Edge WebView2 follows Microsoft's privacy policy.
- The full list of bundled software and their licenses is on the app's **About** page.
