# Checklist bản 0.9.6: mức cấu hình, VRAM, theo dõi không đứt, % bị che, model phụ mới, job cuboid 3D

Các cột giống `docs/CHECKLIST_0.8.0.md`:

- **Đã thêm**
- **Đã test**
- **Lỗi tìm thấy**
- **Đã sửa**

Ký hiệu: ✅ xong · 🟡 làm một phần (lý do ở cuối).

Bản này làm theo các trao đổi sau bản 0.9.5 (backend chậm dần, hết giờ làm hỏng job, occluded khi gần như thấy rõ,
outside kéo tới hết video, job cuboid 3D gán sai class, hình 21–24) và yêu cầu: làm trọn 0.9.6, chú ý dễ đọc, dễ thao tác.

## A. VRAM và mức cấu hình

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| A1 | Trước khi nạp model phụ: thiếu VRAM thì nhả model lâu không dùng nhất | ✅ | ✅ test_v096_tracking | — | — |
| A2 | Sau lượt nặng: PyTorch giữ dư > 1 GB thì trả lại | ✅ | ✅ test (gọi qua API) | — | — |
| A3 | Rảnh: nhả model phụ theo mức (Nhẹ 30 giây … Tối đa: giữ) | ✅ | ✅ test_v096_tracking | L1 | ✅ |
| A4 | Nạp lại SAM3 ảnh thì nhả SAM3 Video trước | ✅ | ✅ test cũ | — | — |
| A5 | Mức cấu hình Tự động / Nhẹ / Cân bằng / Chất lượng / Tối đa ở Cài đặt panel, có giải thích từng mức | ✅ | ✅ smoke v3, i18n | L2 | ✅ |
| A6 | Mức quyết định: số prompt mỗi label, ảnh phóng to gửi Claude, độ dày khi theo dõi, chạy sẵn frame sau, model phụ | ✅ | ✅ test_v096_components | — | — |
| A7 | Dòng VRAM đang dùng, model đang nạp; cảnh báo khi VRAM gần đầy | ✅ | ✅ smoke | — | — |

## B. Theo dõi không đứt vì hết giờ

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| B1 | Mỗi lượt có mã riêng; panel bỏ cuộc thì backend dừng đúng lượt đó | ✅ | ✅ test_v096_tracking | L3 | ✅ |
| B2 | Quá 5 phút: thử lại từng frame (tối đa 10 phút mỗi frame) | ✅ | ✅ smoke v2 (đường thường) | — | — |
| B3 | Lưu kết quả từng frame; lần sau chạy tiếp (cùng khoảng frame, label, vùng bỏ qua); nút "Bỏ tiến độ cũ" | ✅ | ✅ smoke v2 | L4 | ✅ |

## C. occluded / outside theo % thân bị che

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| C1 | % diện tích toàn thân bị che (hộp thấy được so với hộp toàn thân, cộng phần mask bị thủng) | ✅ | ✅ test_v096_tracking | L5 | ✅ |
| C2 | Ngưỡng theo profile: đọc từ guideline, Claude đọc guideline, mặc định 35% / 60% | ✅ | ✅ test_v096_tracking | L6 | ✅ |
| C3 | Ô "Ngưỡng che khuất" ở tab Cả job, hiện nguồn ngưỡng, nhớ theo task | ✅ | ✅ test API, smoke i18n | — | — |
| C4 | Trễ giữa các trạng thái, bỏ chập chờn 1 frame | ✅ | ✅ test_v096_tracking | — | — |
| C5 | Hiện lại sau outside: giữ ID, không outside tới hết video | ✅ | ✅ test_v096_tracking | — | — |
| C6 | Frame model bỏ sót: SAM3 tìm lại ở chỗ dự đoán; thấy thì tính % che, không thấy mới outside | ✅ | ✅ test API + mô phỏng | — | — |
| C7 | Nối lại ID khi người đã chọn bị tách: kiểm vị trí dự đoán + ngoại hình | ✅ | ✅ test cũ | — | — |

## D. Model phụ mới (tuỳ chọn)

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| D1 | Nhận lại người DINOv2-small (Apache-2.0): véc-tơ ngoại hình, mức Cân bằng trở lên | ✅ | ✅ engine giả · model thật: chưa | — | — |
| D2 | Độ sâu Depth Anything V2 Small (Apache-2.0): chỉ người đứng sau bị tính là bị che, mức Chất lượng trở lên | ✅ | ✅ test_v096_components | — | — |
| D3 | Pose toàn thân RTMW (Apache-2.0, ONNX, CPU): 133 điểm, tên điểm tay / chân nhiều kiểu viết | ✅ | ✅ test giải mã SimCC, cắt ảnh · model thật: chưa | L7 | ✅ |
| D4 | Skeleton có điểm tay / chân dùng pose toàn thân; chưa cài thì điểm đó outside, báo một dòng | ✅ | ✅ test_v096_components | L8 | ✅ |
| D5 | Hộp bàn tay có thuộc tính trái / phải: điền theo pose toàn thân, gắn cờ để kiểm | ✅ | ✅ test_v096_components | — | — |
| D6 | Thành phần mới trong app (tải, gỡ, giấy phép, manifest khi build) | ✅ | ✅ desktop smoke | L9 | ✅ |

## E. Job cuboid 3D

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| E1 | Chiều cao phải khớp bảng; dài / rộng nhỏ hơn được, lớn hơn không; không khớp class nào thì bỏ | ✅ | ✅ test_v096_cuboid | L10 | ✅ |
| E2 | Chỉ thấy một mặt: thử cả hai cách ghép cạnh, xoay đúng hướng, kéo cỡ theo bảng, dời tâm ra phía khuất | ✅ | ✅ test_v096_cuboid | L11 | ✅ |
| E3 | Phạm vi theo class (50 / 40 / 30 m), đọc từ guideline nếu có | ✅ | ✅ test_v096_cuboid | — | — |
| E4 | Số điểm tối thiểu theo khoảng cách; nối cụm thưa ở xa | ✅ | ✅ test_v096_cuboid | L12 | ✅ |
| E5 | Mặt đất theo từng ô 8 m; bỏ cụm lơ lửng (đáy cách đất > 0,5 m) | ✅ | ✅ test_v096_cuboid (dốc 4%) | — | — |
| E6 | Tách dãy rào chắn liền nhau thành từng tấm | ✅ | ✅ test_v096_cuboid | — | — |
| E7 | Hai class cùng khớp: class có khoảng cỡ hẹp hơn thắng, gắn cờ để xem ảnh camera | ✅ | ✅ test_v096_cuboid | L10 | ✅ |
| E8 | Ảnh camera: đọc zip context image, đoán hướng theo tên CAM_*, tự dò trục, SAM3 xác nhận / thấy vật hay bị nhầm | ✅ | ✅ test_v096_cuboid, smoke v2 | — | — |
| E9 | Nhớ vật bạn bỏ khi ghi; giữ vật cùng cỡ thì quên dần; nút "Quên hết" | ✅ | ✅ test_v096_cuboid | — | — |
| E10 | Kiểm tra dữ liệu job 3D | ✅ | ✅ smoke v2 | — | — |
| E11 | Tự chỉnh theo cuboid bạn đã đánh (khớp theo đáy cuboid, đề nghị, bấm Áp dụng) | ✅ | ✅ test_v096_cuboid | L13 | ✅ |
| E12 | Bản đồ nhỏ nhìn từ trên, bấm hộp để chọn | ✅ | ✅ smoke v2 (có canvas) | — | — |
| E13 | Đề xuất nhóm theo class, xếp theo độ tin, đề xuất yếu ẩn sẵn, tick cả nhóm | ✅ | ✅ smoke v2 | — | — |
| E14 | Tab Label của job 3D: bảng cỡ, phạm vi, số điểm, prompt camera; không còn chữ "chưa hỗ trợ" | ✅ | ✅ smoke v2 | — | — |
| E15 | Track cuboid: bỏ tick nếu phần lớn frame bị bỏ tick; có độ tin | ✅ | ✅ test | — | — |

## Rà phần cũ

| Phần cũ | Thay đổi |
|---|---|
| Profile YAML có sẵn | Giữ nguyên; profile không ghi ngưỡng che thì dùng 35% / 60% |
| Profile cho project đã tạo ở 0.9.5 | Vẫn chạy; tạo lại để tool đọc ngưỡng che và phạm vi 3D từ guideline |
| Luật "hộp chồng hộp thì occluded" | Đã bỏ ở 0.9.5; 0.9.6 thay bằng độ sâu khi có model |
| Theo dõi Thông minh (0.9.5) | Bước keyframe theo mức cấu hình (16 / 8 / 4 / mọi frame) |
| SAM3 Video | Không đổi; huỷ phiên của bản giả lập chỉ xoá đúng phiên đó |
| Skeleton ViTPose 17 điểm | Không đổi; chỉ skeleton có điểm tay / chân mới dùng pose toàn thân |
| Cuboid 3D của 0.9.4 | Giữ đọc file, track, keyframe; thay cách chọn class, mặt đất, cụm |
| Panel đã cài | Cài đặt cũ giữ nguyên; mức cấu hình mặc định Tự động |

## Lỗi tìm thấy khi làm và test

| # | Lỗi | Ở đâu | Đã sửa |
|---|---|---|---|
| L1 | Test nhả model khi rảnh viết sai: backend chỉ dọn khi cả backend rảnh, không dọn giữa lúc đang chạy | test | ✅ |
| L2 | Engine giả (demo, test) bị coi là máy không GPU: mức Nhẹ tắt luôn chạy sẵn frame sau | panel | ✅ engine giả tính là Cân bằng |
| L3 | Huỷ phiên của SAM3 Video giả lập xoá mọi phiên, không chỉ phiên bị huỷ | backend | ✅ |
| L4 | Tên biến tiến độ trùng biến cũ trong hàm theo dõi (panel không chạy) | panel | ✅ |
| L5 | Hộp ra ngoài mép ảnh bị tính là bị che | theo dõi | ✅ cắt theo khung, ra mép là outside |
| L6 | Thêm dòng "theo dõi" vào bảng profile làm hỏng profile không phải video | profile | ✅ bỏ dòng đó |
| L7 | Tên điểm 'right_pinky_finger3', 'LThumb4' không nhận ra | profile | ✅ tách số, tách chữ hoa |
| L8 | Test cũ dựng skeleton từ danh sách điểm (nay 133 điểm) | test | ✅ |
| L9 | Desktop smoke đếm 5 thành phần | test | ✅ 8 thành phần |
| L10 | Xe con 4,5 m bị gán trailer (bảng trailer nhỏ 2–5 m cũng khớp) | 3D | ✅ class cụ thể hơn thắng + cờ |
| L11 | Chỉ thấy đuôi xe: cạnh dài đo được là bề rộng xe, không khớp car | 3D | ✅ thử cả hai cách ghép cạnh |
| L12 | Vật ở xa điểm thưa vỡ thành nhiều cụm nhỏ | 3D | ✅ nối cụm nhỏ sát nhau |
| L13 | Tự chỉnh so theo tâm: cụm chỉ thấy một mặt không khớp cuboid đủ cỡ của bạn | 3D | ✅ so theo đáy cuboid |
| L14 | Tiếng Anh: tên mức cấu hình chèn trong câu không được dịch | panel | ✅ |
| L15 | Ảnh chụp kiểm tra: xe thấy trọn cũng bị cờ "Class?" (khớp cả trailer nhỏ) | 3D | ✅ chỉ gắn cờ khi một bên chỉ khớp một phần |
| L16 | Ảnh chụp kiểm tra: bảng của tab Label 3D quá rộng, cột Số điểm / Prompt camera bị che | panel | ✅ mỗi label một thẻ, đọc từ trên xuống |

## Đối chiếu hình 21–24 (job cuboid 3D)

| Thấy gì | Xử lý |
|---|---|
| Cuboid gán Trailer / Truck cho thùng rác, nhà vệ sinh di động | ✅ chiều cao phải khớp; ảnh camera; nhớ vật bạn bỏ |
| Cuboid ở rất xa, ít điểm | ✅ phạm vi theo class, số điểm theo khoảng cách |
| Đáy cuboid lơ lửng / chìm ở chỗ dốc | ✅ mặt đất cục bộ; bỏ cụm lơ lửng |
| Rào chắn dài thành một cuboid | ✅ tách từng tấm |
| Tab Label ghi "chưa hỗ trợ" | ✅ tab Label riêng cho job 3D |
| Danh sách khó đọc | ✅ nhóm theo class, xếp theo độ tin, ẩn đề xuất yếu, bản đồ nhìn từ trên |
| Có ảnh CAM_BACK, CAM_BACK_LEFT, CAM_BACK_RIGHT | ✅ đoán hướng theo tên, tự dò trục |

## Làm một phần

- 🟡 **Model thật:** DINOv2, Depth Anything V2, RTMW và SAM3 trên ảnh camera test bằng engine giả và dữ liệu mô
  phỏng (môi trường test không tải được model). Thang điểm của RTMW (`score_gain`) cần chỉnh khi thử trên máy bạn.
- 🟡 **Hướng camera:** không có thông số hiệu chỉnh nên đoán theo tên kiểu nuScenes (trước 0°, trước-trái 55°,
  sau 180°…). Bộ camera khác thì dò sai; xem dòng "Ảnh camera" ở Kiểm tra dữ liệu.
- 🟡 **Point cloud thật:** test bằng point cloud tự dựng (mặt đường, dốc, xe, người, rào chắn). Chạy "Kiểm tra dữ liệu
  job 3D" và "Tự chỉnh theo cuboid bạn đã đánh" trên job của bạn trước khi dựng track cả job.
- 🟡 **Giấy phép RTMW:** mã và trọng số Apache-2.0, nhưng model học trên nhiều bộ dữ liệu; vài bộ hạn chế dùng
  thương mại. Hỏi mentor trước khi dùng cho dự án thương mại.
- Không đưa ảnh của bạn vào repo.
