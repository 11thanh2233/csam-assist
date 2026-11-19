## SAM3 CVAT Assist 0.6.0: bản cài cho Windows

Lần đầu có bộ cài một file. Không cần Python, PowerShell hay Tampermonkey. Hướng dẫn đầy đủ: `docs/DESKTOP.md`.

- **Bộ cài nhẹ:** khoảng 150 MB, chứa Python và các thư viện nhỏ.
- **Trình tải thành phần ở bước cuối của bộ cài:**
  - kiểm tra máy;
  - hiện giấy phép SAM;
  - kiểm token Hugging Face (đã được Meta duyệt chưa);
  - chỉ tải phần nặng cần dùng (PyTorch, trọng số SAM 3, model skeleton nếu chọn), có thanh tiến trình và tải tiếp khi rớt mạng.
- **Cài trước, nhập token sau:** dùng khi Meta chưa duyệt; tải SAM 3 sau trong app.
- **Cửa sổ CVAT riêng:** bảng của tool tự hiện, không cần Tampermonkey. Có danh sách địa chỉ CVAT để thêm hoặc xoá.
- **Bảng điều khiển:** bật/tắt backend, xem nhật ký, quản lý thành phần, chạy nền ở khay hệ thống, tự chạy cùng Windows.
- **An toàn:**
  - mọi file tải về đều kiểm mã băm;
  - token chỉ gửi tới huggingface.co, không lưu nếu không chọn;
  - backend chỉ nghe trong máy, có API key ngẫu nhiên, chạy offline.

**Chưa thử trên máy thật có GPU:** lần chạy SAM 3 đầu tiên trong bản desktop. Máy build của GitHub không có GPU; đã
kiểm bản PyTorch có hỗ trợ RTX 20xx (`sm_75`) và đã chạy thử backend giả lập trong bản cài.
