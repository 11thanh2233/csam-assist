# Nhật ký thay đổi

Mỗi bản một mục `## <bản>`. App hiện các mục này trong khung "Có gì mới" sau khi cập nhật, và bước build lấy đúng
mục của bản đang phát hành làm ghi chú trên GitHub Releases.

## 0.8.2 (2026-11-20)

- **Sửa ô chọn ngôn ngữ của bộ cài:** bản 0.8.1 hiện "English" hai lần, giờ hiện "Tiếng Việt" và "English".
  Windows tiếng Việt tự chọn sẵn Tiếng Việt.

## 0.8.1 (2026-11-20)

Chi tiết từng mục, cách test và lỗi đã sửa ở `docs/CHECKLIST_0.8.1.md`.

- **Giao diện tiếng Anh đầy đủ.** Chọn English ở tab Cài đặt của panel, ở góc trên bảng điều khiển của app, hoặc ngay
  khi mở bộ cài. Một lựa chọn dùng chung cho cả panel, bảng điều khiển, khay hệ thống, thông báo và "Có gì mới".
  - Dịch toàn bộ: panel, bảng điều khiển, bộ cài, thông báo của backend, tên profile, trang duyệt guideline.
  - Bản nháp guideline và câu hỏi gửi LLM viết theo ngôn ngữ đã chọn.
- **Mã lỗi cố định.** Mỗi lỗi kèm một mã kỹ thuật (ví dụ `E_BACKEND_UNREACHABLE`, `E_HF_NOT_APPROVED`, `E_GPU_OOM`),
  giống nhau ở mọi ngôn ngữ, để tra cứu và báo lỗi dễ hơn.
- **Nhật ký luôn bằng tiếng Anh kỹ thuật**, kể cả khi giao diện là tiếng Việt; gói báo lỗi cũng vậy.
- **Bấm điểm dùng SAM 3 Tracker.** Model điểm riêng của SAM 3, nằm sẵn trong trọng số đã tải, không cần tải thêm.
  - Chỉ nạp khi bạn bấm điểm, tự nhả sau 5 phút không dùng.
  - Thiếu VRAM hoặc lỗi thì tự quay về cách cũ.
- **Chuẩn bị ký số bộ cài:** quy trình build hỗ trợ SignPath và chứng chỉ PFX, hướng dẫn ở `docs/SIGNING.md`.
- **Cho người phát triển:** chuỗi tiếng Việt mới trong mã mà chưa có bản dịch thì test báo lỗi
  (`scripts/i18n_extract.py`, `scripts/i18n_build.py`).

## 0.8.0 (2026-11-20)

37 cải tiến; chi tiết từng mục, cách test và lỗi đã sửa ở `docs/CHECKLIST_0.8.0.md`.

- **Gán nhãn nhanh hơn từng frame.**
  - Tool chạy sẵn gợi ý cho frame tiếp theo trong lúc bạn duyệt, sang frame là có ngay.
  - **Alt+Enter**: ghi rồi sang frame tiếp.
  - Duyệt bằng phím (Alt+J/K chọn, Alt+X tick, Alt+Delete bỏ), hoặc giữ Alt rồi bấm lên ảnh.
  - Thanh ngưỡng và nút ẩn class lọc tức thì.
  - Hoàn tác tới 20 lần ghi gần nhất (Alt+Z).
- **Nhãn đúng hơn ngay lần đầu.**
  - Tool kiểm luật QA trước khi ghi.
  - Tự điền truncated/occluded khi label có hai thuộc tính đó.
  - **Bấm điểm** để tách vật.
  - Giới hạn số đỉnh và làm mượt polygon theo profile.
  - Nhắc khi frame này ít vật hơn frame bên cạnh.
- **Việc dài.**
  - Gợi ý cả job tạm dừng / tiếp tục được, kể cả sau khi tắt trình duyệt, kèm thời gian còn lại.
  - Duyệt kết quả cả job dạng lưới ảnh nhỏ.
  - Thông báo khi việc dài xong.
- **App desktop.**
  - Nạp sẵn model và khởi động nóng; tự nhả VRAM khi rảnh (mặc định 15 phút).
  - Mở thẳng CVAT; có danh sách job gần đây.
  - Thông báo Windows.
  - Đồng hồ GPU (VRAM, nhiệt độ) trên panel.
- **Hỗ trợ và nhóm.**
  - Gửi báo lỗi một chạm (không kèm token, key, cookie hay ảnh).
  - Sao lưu / khôi phục; đồng bộ profile, guideline và bài học qua thư mục chung.
  - Một máy GPU phục vụ cả nhóm trong LAN (có key); máy không có GPU dùng backend của máy kia.
  - Chế độ reviewer tô nhãn do tool ghi mà chưa ai sửa.
  - Tự học tách theo từng người.
  - Báo cáo năng suất CSV.
- **Dữ liệu thông minh.**
  - Gợi ý frame nên làm trước (khác lạ, khó, chưa có nhãn).
  - Keyframe theo chuyển động, và gợi ý cả job chỉ trên keyframe.
  - Tìm frame bằng chữ (SAM3).
  - Đo độ đồng thuận giữa hai job.
- **Model riêng của dự án.** Một detector nhỏ học từ nhãn hộp bạn đã duyệt. Chỉ class nào đạt chuẩn mới được dùng, nhanh hơn SAM3 nhiều lần.
- **Nền tảng.**
  - Đầu nối Label Studio (ML backend).
  - Bước ký số bộ cài tự bật khi repo có chứng chỉ.
  - Giao diện tiếng Anh (một phần).
  - Chạy từ mã nguồn trên macOS/Linux.
  - Phím tắt tự đặt, cỡ chữ, tương phản cao.
  - Hướng dẫn lần đầu và thống kê phiên.

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
