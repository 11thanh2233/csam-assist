# Nhật ký thay đổi

Mỗi bản một mục `## <bản>`. App hiện các mục này trong khung "Có gì mới" sau khi cập nhật, và bước build lấy đúng
mục của bản đang phát hành làm ghi chú trên GitHub Releases.

## 0.7.0 (2026-11-19)

- **Không tải lại thứ đã có.**
  - Trước khi tải, app quét máy, gồm bộ nhớ đệm Hugging Face của bản cài tay cũ (0.3.x – 0.5.x), thư mục tool cũ
    trong Downloads, Desktop, Documents, và thư mục bạn tự thêm.
  - Trọng số SAM 3, ViTPose và model khuôn mặt đã có thì được dùng lại sau khi kiểm đúng mã băm.
  - Dùng lại bằng liên kết cứng nên không tốn thêm ổ đĩa.
- **Chỉ cập nhật phần cần.**
  - Bước đầu của trình cài liệt kê từng thành phần: đã có và đúng bản, cần cập nhật, hay dùng lại được, kèm dung lượng phải tải.
  - PyTorch được ghim bản: app mới chỉ bắt tải lại khi thật sự cần bản PyTorch khác.
  - Trọng số có bản mới trên Hugging Face thì chỉ tải các file đã đổi.
  - Token Hugging Face chỉ cần khi thật sự phải tải trọng số SAM 3.
- **Tự kiểm và tự cập nhật app.**
  - Mỗi ngày một lần, app hỏi GitHub Releases xem có bản mới không.
  - Có bản mới thì hiện ghi chú và nút Cập nhật: app tải bộ cài, kiểm mã SHA-256, tự cài ở chế độ cập nhật rồi mở lại.
  - PyTorch, trọng số, profile và dữ liệu tự học giữ nguyên.
  - Tắt được trong bảng điều khiển.
- **Khung "Có gì mới"** hiện một lần sau mỗi lần cập nhật, gộp các bản bạn đã bỏ qua.

## 0.6.0 (2026-11-19)

- **Bộ cài Windows một file** (khoảng 136 MB). Không cần Python, PowerShell hay Tampermonkey.
- **Trình tải thành phần ở bước cuối của bộ cài:**
  - kiểm tra máy;
  - giấy phép SAM;
  - kiểm token Hugging Face (đã được Meta duyệt chưa);
  - chỉ tải phần nặng (PyTorch, trọng số SAM 3, model skeleton nếu chọn), có thanh tiến trình và tải tiếp khi rớt mạng.
- **Cửa sổ CVAT riêng** có sẵn bảng của tool. Có danh sách địa chỉ CVAT để thêm hoặc xoá.
- **Bảng điều khiển:**
  - bật/tắt backend, xem nhật ký;
  - quản lý thành phần;
  - chạy nền ở khay hệ thống, tự chạy cùng Windows.
- **An toàn:**
  - mọi file tải về đều kiểm mã băm;
  - token chỉ gửi tới huggingface.co;
  - backend chỉ nghe trong máy, có API key ngẫu nhiên, chạy offline.

## 0.5.0

- **Tự học từ thao tác của người dùng:** giữ, sửa, đổi nhãn, xoá, bỏ tick, loại trừ, vật tự vẽ thêm. Tool tự chỉnh
  ngưỡng và cách vẽ hộp cho từng label.
- **Rà cả job** ở tab Kiểm tra.
- **Theo dõi video bằng SAM 3 Video** (thử nghiệm).
- **Loại trừ trong Tìm tương tự.**

## 0.4.4

- **Rà soát bảo mật backend:** chặn DNS rebinding, bỏ CORS mở.
- **Đọc frame thật của job.**
- **Chống đề xuất trùng.**

## 0.4.0

- **Chạy được trên CVAT local** và các trang CVAT khác.
- **Nạp guideline và `taxonomy.json`** cho task mới.
- **Gợi ý cả job** theo label chọn.
- **Quản lý profile.**
