# Checklist bản 0.8.1

Bản này làm tiếp ba mục còn 🟡 ở bản 0.8.0: 19 (bấm điểm), 28 (ký số), 29 (tiếng Anh).

Các cột giống `docs/CHECKLIST_0.8.0.md`:

- **Đã thêm**
- **Đã test**: test tự động nào kiểm nó
- **Lỗi tìm thấy**
- **Đã sửa**

Ký hiệu: ✅ xong · 🟡 làm một phần (lý do ở cuối).

## Tính năng

| # | Tính năng | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| 19 | Bấm điểm dùng SAM 3 Tracker: nạp khi cần, tự nhả sau 5 phút, thiếu VRAM hoặc lỗi thì quay về cách cũ | ✅ | ✅ test_v080 · GPU thật: chưa | — | — |
| 28 | Chuẩn bị ký số: quy trình build hỗ trợ SignPath và PFX, `docs/SIGNING.md`, mục chính sách ký số trong README | 🟡 | Bước ký chỉ chạy khi repo có khoá | — | — |
| 29a | Panel tiếng Anh: mọi tab, trạng thái, hộp thoại xác nhận, thông báo | ✅ | ✅ i18n smoke: duyệt mọi tab, không còn chữ tiếng Việt | L1, L2 | ✅ |
| 29b | Bảng điều khiển tiếng Anh, đổi qua lại không phải mở lại app | ✅ | ✅ control smoke | L3, L4, L5 | ✅ |
| 29c | Backend trả thông báo theo ngôn ngữ của panel (header `X-Lang`) | ✅ | ✅ test_i18n + i18n smoke | — | — |
| 29d | Mã lỗi cố định (`E_...`), giống nhau ở mọi ngôn ngữ | ✅ | ✅ test_i18n | — | — |
| 29e | Nhật ký luôn bằng tiếng Anh: app.log, backend.log, gói báo lỗi | ✅ | ✅ test_i18n | — | — |
| 29f | Bộ cài hai ngôn ngữ; ngôn ngữ chọn ở bộ cài được app dùng luôn | ✅ | ✅ test_i18n (cấu hình) · CI Windows: biên dịch và cài thử | — | — |
| 29g | "Có gì mới" và ghi chú cập nhật theo ngôn ngữ (`CHANGELOG.en.md`, Release có hai phần) | ✅ | ✅ test_i18n | — | — |
| 29h | Bản nháp guideline, trang duyệt guideline, câu gửi LLM, tên profile theo ngôn ngữ | ✅ | ✅ test_i18n: bản nháp tiếng Anh, duyệt không sinh bài học giả | L6, L7 | ✅ |
| 29i | Ngăn quên dịch: thêm chữ tiếng Việt mà thiếu bản dịch thì test báo lỗi | ✅ | ✅ test_i18n | — | — |

## Rà tính năng cũ cho khớp với đa ngôn ngữ

Theo yêu cầu "mọi tính năng update đều phải xem lại phần cũ", đã rà và sửa:

| Phần cũ | Thay đổi |
|---|---|
| Bộ dịch EN cũ của panel (khoảng 90 câu) | Thay bằng bộ dịch đầy đủ 556 câu sinh tự động từ mã |
| Lỗi backend trên panel | Thêm mã lỗi; panel gửi ngôn ngữ cho backend |
| Cầu nối của app (bridge) | Thông báo lỗi theo ngôn ngữ; đổi ngôn ngữ trong panel thì app đổi theo |
| Khay hệ thống, thông báo Windows, hộp thoại lỗi lúc khởi động | Theo ngôn ngữ đã chọn |
| Trang duyệt guideline | Mở theo ngôn ngữ của panel |
| Duyệt bản nháp guideline | Bản nháp lưu theo ngôn ngữ người soạn, nên so chỗ sửa vẫn đúng |
| Annotator ảo | Nhận cả "không quyết được" lẫn "cannot decide" |
| Ghi chú cập nhật trên GitHub Releases | Có hai phần; app hiện phần đúng ngôn ngữ |
| Gói báo lỗi | README tiếng Anh; nhật ký vốn đã tiếng Anh |
| Test cũ | Hai test cập nhật theo tính năng mới (cấu hình panel có thêm ngôn ngữ; link trang duyệt có `&lang=`) |
| README, DESKTOP.md | Thêm mục ngôn ngữ, bảng mã lỗi, cách thêm chữ mới |

## Lỗi tìm thấy khi test

| # | Lỗi | Ở đâu | Đã sửa |
|---|---|---|---|
| L1 | Tên và mô tả profile (từ file YAML) còn tiếng Việt | panel | ✅ quét cả file YAML vào bộ dịch |
| L2 | Chuỗi nối bằng dấu `+` bị tách thành mảnh, dịch không khớp | panel, app | ✅ bộ quét gộp chuỗi nối thành một mẫu |
| L3 | Đoạn văn xuống dòng trong HTML không khớp bản dịch | bảng điều khiển | ✅ so khớp bỏ qua khoảng trắng |
| L4 | Sau khi đổi ngôn ngữ, dữ liệu đã tải trước đó vẫn là tiếng Việt | bảng điều khiển | ✅ tải lại dữ liệu khi đổi ngôn ngữ |
| L5 | Chữ đã tách theo thẻ HTML (từng nút chữ) không dịch được | bảng điều khiển | ✅ bộ dịch thêm từng đoạn giữa các thẻ |
| L6 | Luật của guideline mẫu nằm giữa dòng (kèm đuôi chú thích) không dịch | bản nháp guideline | ✅ tách đuôi chú thích, ghép đoạn dài nhất có trong bộ dịch |
| L7 | Dịch bản nháp dài mất 60 giây | backend | ✅ lọc nhanh mẫu theo chữ cố định, nhớ kết quả: còn dưới 1 giây |

## Các mục 🟡 và lý do

- **19. SAM 3 Tracker:** đã test bằng model giả lập. Lần chạy đầu trên card RTX 2060 của bạn là lần kiểm cuối.
- **28. Ký số:** cần bạn làm phần còn lại:
  - chọn giấy phép cho repo;
  - đăng ký SignPath, hoặc mua chứng chỉ;
  - thêm khoá vào GitHub (theo `docs/SIGNING.md`).

**Phạm vi tiếng Anh:**
- Mọi chữ do tool viết ra đều có bản tiếng Anh.
- Chữ không dịch vì là dữ liệu: tên label, tên task, tiêu đề bạn tự đặt, và câu trả lời của LLM nếu LLM viết
  tiếng Việt.
- Bài học đã lưu từ trước (do người duyệt viết) giữ nguyên ngôn ngữ gốc.

**CI GitHub (Windows + Ubuntu + macOS): xanh toàn bộ** ở commit `8baab9d`, gồm biên dịch bộ cài hai ngôn ngữ và cài thử im lặng.
