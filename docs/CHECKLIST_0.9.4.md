# Checklist bản 0.9.4: profile cho project, tag, cuboid 3D, tracking, tự học, kiểm tra máy

Các cột giống `docs/CHECKLIST_0.8.0.md`:

- **Đã thêm**
- **Đã test**
- **Lỗi tìm thấy**
- **Đã sửa**

Ký hiệu: ✅ xong · 🟡 làm một phần (lý do ở cuối).

Bản này gộp cả ba đợt trong kế hoạch, có đối chiếu thêm 8 hình người dùng gửi (mục cuối) và các guideline: road v1.0,
cabin OMS/DMS (`GUIDE.md`), Lab Ngày 4 Keypoint & Pose, Lab Ngày 5 Tracking, 3D Cuboid V1.

## A. Profile cho project

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| A1 | Profile cho project dựng từ label của task (không cần taxonomy, không cần label trước), lưu theo project hoặc task | ✅ | ✅ test_v094 (API preview / save / current), smoke v2 `projectProfileScenario` | — | — |
| A2 | Một job gộp box, polygon, polyline, mask, skeleton, tag (chế độ "mixed") | ✅ | ✅ test_v094 (đề xuất gộp), smoke v2 | — | — |
| A3 | Skeleton ghép từng điểm theo tên điểm (thân COCO 17, mặt 13 điểm theo MediaPipe), quy ước trái/phải theo guideline | ✅ | ✅ test_v094 (face13 của `GUIDE.md`, bộ 13 điểm Lab Ngày 4: 13/13, thân 17/17) | L6 | ✅ |
| A4 | Luật thuộc tính: chạm mép, bị che, rìa fisheye, màu đèn, để bạn chọn | ✅ | ✅ test_v094 (từng luật, khoá thuộc tính chuẩn hoá) | L4 | ✅ |
| A5 | Kích thước tối thiểu đọc từ guideline (bỏ object nhỏ hơn) | ✅ | ✅ test_v094 (chữ có `**`, backtick, "ít nhất") | — | — |
| A6 | LLM đọc guideline (chỉ văn bản, cần tick đồng ý) điền prompt, định nghĩa, luật thuộc tính, điểm skeleton | ✅ | ✅ test_v094 (LLM giả) · gọi LLM thật: chưa | — | — |
| A7 | Tab Label chỉ tới Profile cho project khi có label profile hiện tại không gợi ý được | ✅ | ✅ test_v094 | — | — |

## B. Tracking

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| B1 | Chọn người cần theo dõi (P1, P2…), theo dõi cả trước và sau frame chọn, gộp lại theo ngoại hình | ✅ | ✅ test_v094, smoke v2 (khoanh người) | L9 | ✅ |
| B2 | Vùng bỏ qua (gương, kính, quảng cáo) cho mọi job của task | ✅ | ✅ test_v094 (gợi ý từng frame và theo dõi) | — | — |
| B3 | Đánh cờ người đứng yên suốt job (poster, phản chiếu), bỏ chọn sẵn | ✅ | ✅ test_v094 | — | — |
| B4 | Nối khoảng model bỏ sót; khuất ≤ ngưỡng thì `occluded`, ra mép ảnh thì `outside` | ✅ | ✅ test_v094 | L8 | ✅ |
| B5 | SAM3 Video: 2 frame mỗi lần gọi, nút dừng, gỡ SAM3 ảnh trước khi nạp Video, dọn mask nhiễu | ✅ | ✅ test_v094 (huỷ, dọn mask) · GPU thật: chưa | — | — |
| B6 | GPU lỗi CUDA: trả `E_GPU_FAULT`, backend tự thoát để app bật lại (khi bật Tự khởi động lại) | ✅ | ✅ test_v094 | L10 | ✅ |
| B7 | Bỏ thông báo `kernels library is not installed` lặp lại | ✅ | ✅ test_v094 | — | — |
| B8 | Dữ liệu thông minh: job video có nút Theo dõi cả job (ra track) | ✅ | ✅ smoke v2 | — | — |
| B9 | Cờ "Kiểm tra ID": một cờ mỗi track, tối đa 3 ứng viên gần nhất | ✅ | ✅ test_v094 | H7 | ✅ |

## C. Soạn guideline nháp và nạp guideline

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| C1 | Soạn được khi task chưa có label thật; tự tính số ảnh mẫu, chọn ảnh theo cảnh khác nhau | ✅ | ✅ smoke v2 (Tự chọn số) | — | — |
| C2 | Câu hỏi: tiến độ, bỏ qua, sửa, xem trước câu trả lời đổi gì | ✅ | ✅ test_v094 (`effects`), smoke v2 | L2 | ✅ |
| C3 | Vật có trong mục đích / từ khoá thì mặc định Thêm class; hai tên cùng một vật hỏi một lần | ✅ | ✅ test_v094 | H2 | ✅ |
| C4 | Nạp guideline: cột "Dạng CVAT", tag, checklist ô tick, bỏ backtick | ✅ | ✅ test_v094, test_import | L3 | ✅ |
| C5 | Nạp guideline: skeleton theo mục + bảng tên điểm; dòng "Tag ảnh" thành từng tag; cột thuộc tính; câu "Nhãn: `person`" | ✅ | ✅ test_v094 trên cả 4 guideline gửi kèm | L12, L13 | ✅ |

## D. Tự học và model riêng

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| D1 | Học từ track (giữ, sửa, xoá) | ✅ | ✅ test_v094 | L7 | ✅ |
| D2 | Học điểm skeleton hay bị kéo lệch, thuộc tính tự điền hay bị đổi | ✅ | ✅ test_v094 | L7 | ✅ |
| D3 | Thành phần "Trọng số khởi đầu cho model riêng" (COCO, torchvision, BSD-3) trong app và bộ cài | ✅ | ✅ test_v094, control smoke (thẻ thứ 5) | L11 | ✅ |
| D4 | Huấn luyện đủ số bước dù ít ảnh, warmup, lật ảnh ngang; giải thích vì sao class chưa dùng được | ✅ | ✅ chạy huấn luyện thật bằng PyTorch CPU (dữ liệu giả) · với trọng số khởi đầu thật: chưa (môi trường test không tải được) | H3 | ✅ |
| D5 | Model riêng học cả polygon, mask | ✅ | ✅ test_v094 | — | — |

## E. Panel

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| E1 | Kéo mép đổi cỡ, nút Gọn / Vừa / Rộng | ✅ | ✅ ảnh chụp panel thật | — | — |
| E2 | Cờ thành chip ngắn, gom cùng loại kèm số đếm; lọc theo cờ | ✅ | ✅ smoke v2 | H7 | ✅ |
| E3 | Đoạn giải thích dài gập thành "Giải thích" | ✅ | ✅ ảnh chụp | — | — |
| E4 | Tag ảnh trong danh sách đề xuất, ghi và hoàn tác cùng nhãn | ✅ | ✅ smoke v2 | — | — |
| E5 | "Nên làm trước" gom frame cùng lý do; ảnh rời thì không liệt kê keyframe | ✅ | ✅ test_v094 | H1 | ✅ |
| E6 | Track không chọn ẩn bớt | ✅ | ✅ smoke | H7 | ✅ |
| E7 | Profile chọn tay nhớ theo task | ✅ | ✅ test_v094 | H8 | ✅ |
| E8 | Thanh trạng thái không bị cắt; bảng profile không tràn ngang | ✅ | ✅ test_v094 | H1, H5 | ✅ |

## F. App và bộ cài

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| F1 | Kiểm tra máy ở trang Hỗ trợ và bước 1: Windows, ổ đĩa, RAM, Visual C++, WebView2, WSL2, Docker | ✅ | ✅ test_v094 (máy giả đủ / thiếu), control smoke | — | — |
| F2 | Nút sửa: tải Visual C++, WebView2, Docker; cài WSL2 (Windows hỏi quyền admin) | ✅ | ✅ test_v094 · Windows thật: chưa | — | — |
| F3 | Bộ cài tự cài WebView2 khi thiếu (bộ cài chính thức của Microsoft, CI kiểm chữ ký); tuỳ chọn cài WSL2 | ✅ | ✅ test_v094 (script bộ cài, CI) · chạy bộ cài thật: chưa | — | — |
| F4 | Sửa nút chép nhật ký | ✅ | ✅ test_v094 (clip.exe giả), control smoke | L1 | ✅ |

## G. Cuboid 3D

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| G1 | Đọc point cloud: PCD ascii / binary / binary_compressed, KITTI `.bin`, zip | ✅ | ✅ test_v094 (từng định dạng) | — | — |
| G2 | Cuboid từng frame: tách mặt đường, gom cụm, hộp xoay, class theo bảng kích thước | ✅ | ✅ test_v094 (cảnh giả có xe, người, tường) | L14 | ✅ |
| G3 | Track cả job: khoá kích thước ±5%, yaw theo hướng đi, nội suy ≤ 6 frame bị che, keyframe | ✅ | ✅ test_v094 | — | — |
| G4 | Panel nhận job 3D, ghi cuboid vào CVAT | ✅ | ✅ smoke v2 `lidarScenario` (CVAT giả) · CVAT 3D thật: chưa | — | — |

## Rà phần cũ

| Phần cũ | Thay đổi |
|---|---|
| Profile YAML có sẵn (generic, tracking, driver pose, BDD…) | Giữ nguyên; profile cho project chỉ được chọn khi job thuộc project đó, chế độ tự chọn bỏ qua nó ở job khác |
| Ô chọn profile | Lựa chọn tay nay nhớ theo task; lần đầu chạy 0.9.4 giữ lựa chọn cũ cho task đang mở |
| Gợi ý từng frame (box, polygon, polyline, mask) | Thêm lọc kích thước tối thiểu và vùng bỏ qua; không có cấu hình thì như cũ |
| Keypoints thân / mặt | Dùng chung bộ ghép điểm; profile keypoints cũ chạy như trước |
| Theo dõi bằng SAM3 ảnh | Thêm chọn người, vùng bỏ qua, nối khoảng; không chọn ai thì theo dõi mọi người như cũ |
| Tự học (ngưỡng, chỉnh hộp) | Thêm loại sự kiện mới; dữ liệu cũ vẫn đọc được |
| Soạn guideline nháp | Câu hỏi cũ giữ khoá; bài học đã lưu vẫn dùng |
| Nạp guideline (.docx Face Landmark 50, HumanPose 17) | Kết quả như cũ (test_import), chỉ thêm label khi guideline có mục skeleton kèm bảng tên điểm |
| Bảng điều khiển: thẻ Thành phần | Thêm thẻ thứ 5; manifest cũ không có mục này thì báo "bản cài này chưa có", không lỗi |
| Bộ cài: Visual C++ | Như cũ; WebView2 chuyển từ "chỉ báo" sang "tự cài", vẫn báo nếu cài không được |
| Cập nhật từ trong app (`/SILENT /update=1`) | Không hỏi WebView2, WSL2 |

## Lỗi tìm thấy khi làm và test

| # | Lỗi | Ở đâu | Đã sửa |
|---|---|---|---|
| L1 | 0.9.3 vẫn không chép được trên máy thật: mở clipboard không có cửa sổ chủ | app desktop | ✅ chép trong trình duyệt của app, dự phòng `clip.exe` |
| L2 | Câu hỏi có ký hiệu đứng trước làm sai phép so chữ của smoke cũ | panel | ✅ |
| L3 | Nạp guideline không đọc cột "Dạng CVAT", tên label còn dấu backtick | nạp guideline | ✅ |
| L4 | Khoá thuộc tính của tag không chuẩn hoá ("camera blocked") | tag | ✅ |
| L5 | Test nạp guideline để lại guideline 'cabin' ảnh hưởng test khác | test | ✅ dọn sau test |
| L6 | "Khoé ngoài" bị hiểu là "góc" (corner) thay vì khoé mắt | ghép điểm | ✅ vị trí cụ thể ưu tiên |
| L7 | Bộ lọc dữ liệu tự học theo profile bỏ mất các loại bài học mới | tự học | ✅ |
| L8 | Người ra mép ảnh 1 frame bị đặt `outside` nhầm | tracking | ✅ chỉ khi khuất > 1 frame |
| L9 | Danh sách người chọn bị nhớ đệm, không thấy người vừa thêm | panel | ✅ |
| L10 | Test lỗi GPU trả 400 vì kiểm tra host | test | ✅ |
| L11 | Thẻ Thành phần và bước quét đếm 4 thẻ sau khi thêm bộ phát hiện | control smoke | ✅ |
| L12 | Dòng "Tag ảnh" thành một tag tên "ảnh" (guideline road) | nạp guideline | ✅ tách thành camera, timeofday, weather, source |
| L13 | Mở rộng câu đoán label bắt nhầm "Label học…" thành label "ho" | nạp guideline | ✅ |
| L14 | Tường giả trong test dính vào cụm xe | test cuboid | ✅ |

## Đối chiếu 8 hình người dùng gửi

| Hình | Thấy gì | Xử lý |
|---|---|---|
| H1 (Cả job) | "Nên làm trước" lặp cùng một câu cho từng frame; keyframe 50/50 với job ảnh rời; thanh trạng thái bị cắt | ✅ gom frame theo lý do; ảnh rời thì báo không lọc được thay cho lưới 50 nút; thanh trạng thái cuộn được |
| H2 (câu hỏi guideline) | Câu "class X không có trong task" mặc định trái với mục đích bạn ghi; `person` và `pedestrian` hỏi hai lần | ✅ mục đích / từ khoá có nhắc thì mặc định Thêm class; hai tên trùng trên 80% hỏi một lần |
| H3 (model riêng 0%) | 50 ảnh, 2184 hộp nhưng mọi class 0% | ✅ trọng số khởi đầu (thành phần mới), tối thiểu 600 bước, warmup, lật ảnh; giải thích lý do |
| H4 (nạp guideline Lab Ngày 4) | 0 label, phải thêm tay | ✅ đọc được 2 skeleton (person 17 điểm, face 13 điểm) |
| H5 (tab Label, job mặt + người) | Skeleton face 13 điểm "chưa nhận ra"; bảng profile tràn ngang | ✅ Profile cho project ghép 13/13 điểm; thông báo chỉ tới đó; bảng không tràn |
| H6 (soạn nháp) | Đặt 50 ảnh mà nút ghi "Quét 13 ảnh" | ✅ lỗi giữ khoảng frame của job trước khi chuyển job |
| H7 (danh sách track) | 7 chip "Kiểm tra ID" giống nhau, đoạn chữ dài lặp | ✅ một cờ, tối đa 3 ứng viên; chip gom theo loại có số đếm; track không chọn ẩn bớt |
| H8 (tab Label, job đường phố) | Tag "chưa được hỗ trợ" và hiện ô shape rectangle; job ảnh lại dùng profile Tracking | ✅ hiện đúng kiểu tag, chỉ tới Profile cho project; profile chọn tay nhớ theo task |

## Làm một phần

- 🟡 **Windows thật:** chép nhật ký, kiểm tra máy, cài WSL2, bộ cài tự cài WebView2 cần thử trên máy thật (môi trường
  test không có Windows).
- 🟡 **CVAT 3D thật:** định dạng dữ liệu point cloud CVAT trả về và cách ghi cuboid đã làm theo tài liệu CVAT và test bằng
  CVAT giả; cần thử với một job 3D thật.
- 🟡 **SAM3 / SAM3 Video trên GPU thật:** chọn người, nối khoảng, dọn mask test bằng engine giả.
- 🟡 **Model riêng với trọng số khởi đầu thật:** vòng huấn luyện chạy thật trên CPU; chưa đo độ chính xác với trọng số
  COCO vì môi trường test không tải được file trọng số.
- Không làm trong bản này: học chung qua máy chủ của nhóm (chờ bàn với mentor về bảo mật và pháp lý).
