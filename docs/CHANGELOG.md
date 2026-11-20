# Nhật ký thay đổi

Mỗi bản một mục `## <bản>`. App hiện các mục này trong khung "Có gì mới" sau khi cập nhật, và bước build lấy đúng
mục của bản đang phát hành làm ghi chú trên GitHub Releases.

## 0.9.1 (2026-11-20)

Chi tiết và kết quả test ở `docs/CHECKLIST_0.9.1.md`.

- **Bộ cài tự cài Microsoft Visual C++** khi máy thiếu (Windows hỏi quyền admin một lần), thay vì chỉ báo lỗi.
- **Kiểm tra nhanh sau khi cài:** bước "Sẵn sàng" tự chạy SAM 3 trên một ảnh mẫu và báo "Mọi thứ hoạt động" hoặc lỗi
  kèm cách sửa. Chạy lại được ở trang Hỗ trợ.
- **Cập nhật an toàn:**
  - trước khi cài bản mới, app tự sao lưu profile, guideline, dữ liệu tự học và cài đặt (giữ 3 bản);
  - bản mới không mở được thì tự quay về bản cũ và báo cho bạn;
  - cài đặt của mọi bản cũ đều đọc được; file cài đặt hỏng được giữ lại, không bị ghi đè.
- **Trang Giới thiệu:** phiên bản, giấy phép của SAM 3 (tóm tắt và toàn văn) và mọi phần mềm, model đi kèm. Mở bằng
  cách bấm số phiên bản ở thanh bên.
- **Thống kê công việc ở Tổng quan:** số nhãn đã ghi hôm nay, số frame, thời gian tiết kiệm ước tính, biểu đồ 7 ngày,
  nút xuất CSV để báo cáo với nhóm.
- **Báo rõ khi panel và backend lệch bản:** nói bên nào cũ hơn và cách cập nhật (Tampermonkey, app, hay máy chia sẻ).
- **Hướng dẫn sử dụng cho người gán nhãn** có ảnh minh hoạ: `docs/HUONG_DAN.md` (mở từ app: Giới thiệu → Hướng dẫn
  sử dụng). Thêm trang quyền riêng tư `docs/PRIVACY.md`.
- Sửa: chữ "null" hiện dưới dòng Job/Frame trong panel.
- Sửa: thống kê trong panel tính ngày theo giờ UTC (ở Việt Nam, việc làm lúc 0h-7h sáng bị tính sang hôm trước).
- **Đổi tên thành SAM3 Label Assist** (trước là SAM3 CVAT Assist), vì tool sẽ hỗ trợ cả các nền tảng gán nhãn khác.
  - Cập nhật từ bản cũ giữ nguyên dữ liệu, token Hugging Face đã lưu, đăng nhập CVAT và chế độ tự chạy cùng Windows.
  - Lối tắt ở Desktop và Start Menu đổi sang tên mới, không để lại lối tắt tên cũ.
  - Bộ cài tên mới là `SAM3-Label-Assist-Setup-<bản>.exe`. Release vẫn kèm bản tên cũ để app 0.9.0 trở về trước tự cập
    nhật được.
  - Người dùng Tampermonkey: script vẫn giữ tên cũ trong Tampermonkey để không bị chạy hai panel cùng lúc.
- Sửa: chữ "vừa xong" ở job gần đây chưa dịch khi dùng tiếng Anh.

## 0.9.0 (2026-11-20)

Chi tiết và kết quả test ở `docs/CHECKLIST_0.9.0.md`.

- **Sửa lỗi bấm Bật backend / Mở CVAT không có phản ứng** (có từ 0.8.0). Lỗi nằm trong app, không phải do máy thiếu gì.
  Bản cài không cần `Set-ExecutionPolicy` hay `run.ps1`.
- **Bảng điều khiển làm lại theo bản mẫu:** sáu trang ở thanh bên (Tổng quan, Trang gán nhãn, Thành phần,
  Dùng chung nhóm, Hỗ trợ, Cài đặt) và bộ icon riêng.
  - Tổng quan: trạng thái backend, card, VRAM, nhiệt độ GPU; mở nhanh trang gán nhãn và job gần đây.
  - Backend lỗi thì nói rõ nguyên nhân (ví dụ thiếu Microsoft Visual C++) và các bước sửa, mỗi bước một nút bấm.
  - Hỗ trợ: tạo gói báo lỗi kèm mô tả, xem nhật ký ngay trong app (lọc chữ, chỉ xem dòng lỗi, chép).
  - Cài đặt: công tắc và nút chọn; thêm giao diện sáng/tối/theo Windows.
- **Backend tự bật lại khi tắt bất ngờ** (tối đa 3 lần trong 10 phút), báo qua khay hệ thống. Tắt được trong Cài đặt.
- **Thao tác nhanh:** Ctrl+K tìm và chạy mọi thao tác, Alt+1…6 chuyển trang. App nhớ trang đang mở.
- **Hộp thoại dễ đóng hơn ở mọi nơi:**
  - bảng điều khiển: Esc, bấm ra ngoài hoặc ✕;
  - panel trong CVAT: hộp thoại riêng thay hộp thoại của trình duyệt (Esc, bấm ra ngoài, Enter để đồng ý).
- **Nút nào bấm cũng có phản hồi:** vòng xoay khi đang chạy, thông báo khi xong. Xoá trang gán nhãn có nút Hoàn tác.
- Trang gán nhãn có ghi nền tảng (hiện là CVAT), chuẩn bị cho các nền tảng gán nhãn khác.

## 0.8.3 (2026-11-20)

Chi tiết và kết quả test ở `docs/CHECKLIST_0.8.3.md`.

- **Logo mới:** chữ S vẽ bằng nét polyline có đỉnh.
  - Dùng ở file exe, bộ cài, lối tắt, khay hệ thống, cửa sổ app, panel trong CVAT, tab trình duyệt của backend.
  - Cỡ nhỏ (16–24 px) dùng nét dày riêng cho dễ nhìn.
- **Taskbar hiện đúng icon và tên SAM3 CVAT Assist**, không còn biểu tượng của Python.
- **Màn hình chờ có logo** hiện ngay khi mở app, tự đóng khi cửa sổ app hiện.
- **Cửa sổ cài đặt làm lại theo bản mẫu:**
  - cột trái xanh đậm có logo và 6 bước; bước xong có dấu ✓, bước đang làm tô đậm;
  - thanh nút Huỷ / Quay lại / Tiếp cố định ở dưới;
  - trang tải có thanh tiến độ tổng, tốc độ, thời gian còn lại, trạng thái từng thành phần, nhật ký thu gọn;
  - trang "Sẵn sàng" ở cuối.
- **Bảng điều khiển** cùng kiểu: cột trái có mục lục nhảy tới từng phần; sáng/tối theo Windows.
- **Bộ cài** có dải màu thương hiệu và logo; lối tắt gom chung một biểu tượng trên taskbar.

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
