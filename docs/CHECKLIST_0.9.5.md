# Checklist bản 0.9.5: Claude xem ảnh, trí nhớ của project, occluded theo chính vật, dựng hình theo guideline, theo dõi nhanh

Các cột giống `docs/CHECKLIST_0.8.0.md`:

- **Đã thêm**
- **Đã test**
- **Lỗi tìm thấy**
- **Đã sửa**

Ký hiệu: ✅ xong · 🟡 làm một phần (lý do ở cuối).

Bản này làm theo 12 hình (9–20) người dùng gửi ngày 24/11 và yêu cầu: tool thích nghi theo guideline và cách người
dùng chốt nhãn của từng job, không viết luật riêng cho từng label; được dùng Claude cho mọi việc cần; tracking giữ độ
chính xác và dễ thao tác.

## A. Claude xem ảnh

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| A1 | Gửi frame có đánh số đề xuất + ảnh phóng to vật nhỏ, kèm label, thuộc tính, ghi chú guideline, bài học, các lần sửa | ✅ | ✅ test_v095 (nội dung gửi đi) | — | — |
| A2 | Áp kết quả: thuộc tính (chỉ nhận giá trị hợp lệ), occluded theo chính vật, từng nhóm điểm skeleton, bỏ đề xuất, tag | ✅ | ✅ test_v095 | L1 | ✅ |
| A3 | Vật bị sót: Claude chỉ khung, SAM3 vẽ lại, gắn cờ "Mới thêm" | ✅ | ✅ test_v095 (đổi toạ độ, bỏ label lạ) | — | — |
| A4 | Đếm lượt mỗi ngày, giới hạn (mặc định 500, đổi trong Cài đặt của app), nhớ kết quả cho cùng ảnh | ✅ | ✅ test_v095 | — | — |
| A5 | Ô "Claude xem ảnh" ở tab Gợi ý (bật sẵn khi có key), hiện số lượt hôm nay | ✅ | ✅ smoke | — | — |
| A6 | Không có Claude: báo rõ, tool vẫn trả kết quả đo trên ảnh | ✅ | ✅ test_v095 | — | — |
| A7 | Lời nhắn gửi Claude viết bằng tiếng Anh (Claude trả lý do bằng tiếng Việt) | ✅ | ✅ test i18n | L2 | ✅ |

## B. Trí nhớ của project

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| B1 | Ghi giá trị bạn chốt lúc bấm Ghi (thuộc tính, tag, occluded, đề xuất bị bỏ) so với giá trị tool gợi ý | ✅ | ✅ test_v095 | — | — |
| B2 | Học quy ước tên file (vd `_MVL` → `fisheye_left`), giữ tag theo chuỗi ảnh | ✅ | ✅ test_v095 với tên file thật của bạn | L3 | ✅ |
| B3 | Bài học: Claude tóm, bạn tự viết, xoá được; Claude đọc ở mọi lần xem ảnh sau | ✅ | ✅ test_v095 | — | — |
| B4 | Mục "Trí nhớ của project" ở tab Tự học | ✅ | ✅ smoke | L4 | ✅ |

## C. Thuộc tính, occluded, skeleton

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| C1 | Chọn thuộc tính ngay trên panel; nút occluded / visible | ✅ | ✅ smoke v3 | — | — |
| C2 | Chồng khung không còn tự đặt occluded, chỉ gắn cờ (profile cũ muốn giữ thì ghi `set: true`) | ✅ | ✅ test_v095, test_pipeline cập nhật | — | — |
| C3 | Skeleton mặt: kính râm che mắt không làm mất mũi, môi; luật Outside cả skeleton chỉ khi guideline ghi ngưỡng | ✅ | ✅ test_v095 | — | — |
| C4 | Vật che mặt mặc định thêm mũ, ria | ✅ | ✅ test | — | — |
| C5 | Skeleton người: bỏ tick sẵn người thấy quá ít điểm, người trong khung ảnh / poster / màn hình / gương | ✅ | ✅ test_v095 | — | — |

## D. Polygon / polyline theo guideline

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| D1 | Chọn cách dựng hình từ định nghĩa trong guideline (hoặc Claude chọn khi đọc guideline) | ✅ | ✅ test_v095 | — | — |
| D2 | Gộp vùng (crosswalk là một polygon bao các sọc) | ✅ | ✅ test_v095 + thử trên ảnh fisheye thật (mask giả bằng ngưỡng màu) | L5 | ✅ |
| D3 | Vạch trong vùng crosswalk không thành lane | ✅ | ✅ test_v095 | — | — |
| D4 | Vùng mặt đường liền với xe (drivable), thuộc tính làn đang đi / làn bên cạnh | ✅ | ✅ test_v095 | — | — |
| D5 | Mép vùng mặt đường làm road_curb, có cờ | ✅ | ✅ test_v095 | — | — |

## E. Tag

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| E1 | Fisheye khi chỉ hai góc tối; không biết hướng thì để bạn chọn | ✅ | ✅ test_v095 + 5 ảnh road thật của bạn (5/5 nhận đúng fisheye) | H14 | ✅ |
| E2 | Đoán luật tag theo tên và giá trị khi profile không ghi | ✅ | ✅ test | — | — |
| E3 | Giá trị học được (tên file, chuỗi ảnh) thắng câu trả lời của Claude | ✅ | ✅ test_v095 | — | — |

## F. Theo dõi

| # | Thay đổi | Đã thêm | Đã test | Lỗi tìm thấy | Đã sửa |
|---|---|---|---|---|---|
| F1 | SAM3 chạy bf16 / fp16 trên GPU, tự về fp32 nếu bản transformers không hỗ trợ | ✅ | ✅ test chọn dtype · GPU thật: chưa | — | — |
| F2 | Chế độ "Thông minh": keyframe + kiểm tra ở giữa và 1/4 khoảng, lệch thì chạy từng frame | ✅ | ✅ mô phỏng 150 frame: đoạn đều chạy ≤ 30/60 frame, đoạn giật chạy gần đủ, sai lệch nhỏ nhất vẫn ≥ 0,85 | L6 | ✅ |
| F3 | Ô chọn "Chạy SAM3 trên: Thông minh / Mọi frame" | ✅ | ✅ smoke v2 | — | — |

## Rà phần cũ

| Phần cũ | Thay đổi |
|---|---|
| Profile YAML có sẵn | Giữ nguyên. Luật occluded chồng khung đổi thành gắn cờ; muốn giữ cách cũ thì thêm `set: true` |
| Profile driver pose17 + face50 | Thêm mũ, ria vào vật che; luật Outside cả skeleton vẫn áp vì profile có ngưỡng riêng cho từng bộ phận |
| Profile cho project của 0.9.4 | Vẫn chạy; tạo lại để có cách dựng hình và ghi chú guideline cho Claude |
| Tự học cũ (ngưỡng, chỉnh hộp) | Giữ nguyên, chạy song song với trí nhớ mới |
| Tag của 0.9.4 | Giữ luật đo ảnh; thêm giá trị học được và Claude |
| Theo dõi SAM3 Video | Không đổi (chế độ Thông minh chỉ áp cho cách thường) |
| Cài đặt AI của app | Thêm ô giới hạn mỗi ngày; key, model như cũ |

## Lỗi tìm thấy khi làm và test

| # | Lỗi | Ở đâu | Đã sửa |
|---|---|---|---|
| L1 | Khoá thuộc tính của đề xuất là tên đã chuẩn hoá (`eye left`), test viết theo tên gốc | test | ✅ |
| L2 | Kết quả đo dạng `numpy.bool_` làm hỏng JSON gửi đi | backend | ✅ ép về `bool` |
| L3 | Quy ước tên file chỉ hiện khi một thuộc tính có từ 2 giá trị trở lên | trí nhớ | ✅ |
| L4 | Panel có thể gọi tải trí nhớ liên tục khi mạng lỗi | panel | ✅ |
| L5 | Sọc crosswalk cách xa nhau (fisheye) không nối được bằng phép đóng: chỉ lấy một sọc | dựng hình | ✅ dùng bao lồi của cả cụm |
| L6 | Chỉ kiểm ở giữa khoảng: chuyển động giật cục lọt qua (sai lệch 0,65) | theo dõi | ✅ kiểm thêm điểm 1/4, ngưỡng chặt hơn |
| L7 | Test cũ của 0.9.4 đếm đúng 12 frame được lấy khi theo dõi | smoke v2 | ✅ cập nhật theo cách mới |

## Đối chiếu 12 hình

| Hình | Thấy gì | Xử lý |
|---|---|---|
| 9, 10 | Thuộc tính (eye_left, glasses, gaze_zone, whose, side, on_wheel, seat, type) phải chỉnh tay trong CVAT | ✅ Claude điền theo guideline và giá trị của task; chọn ngay trên panel; tool học giá trị bạn chốt |
| 9 | occluded đặt vì bị khung khác chồng lên | ✅ bỏ luật đó; occluded xét theo chính vật |
| 11 | Tag hiện đúng kiểu ở tab Label | Đã đúng từ 0.9.4 |
| 12, 13 | drivable không tự ra; crosswalk tô từng sọc; lane lấy cả sọc crosswalk; sót lane, curb | ✅ cách dựng hình theo guideline + Claude chỉ vật sót. 🟡 cần thử với SAM3 thật |
| 14 | camera ghi `front` cho ảnh fisheye gương trái | ✅ nhận fisheye; học `_MVL`; Claude chọn hướng |
| 15, 16 | Mũ che mắt, ria che miệng mà vẫn visible | ✅ vật che mặc định thêm mũ, ria; Claude xét từng bộ phận |
| 17, 18 | Kính râm che mắt mà mất luôn mũi, môi | ✅ chỉ điểm bị che mới đổi trạng thái |
| 19 | Đánh cả người trong ảnh chụp, người thấy ít | ✅ bỏ tick sẵn + Claude đề nghị bỏ |
| 20 | Theo dõi 150 frame, 7 người: chậm, tốn VRAM | ✅ bf16 / fp16 + chế độ Thông minh. 🟡 cần đo trên GPU của bạn |

## Làm một phần

- 🟡 **Claude thật:** cách gửi và cách đọc câu trả lời test bằng Claude giả. Chưa gọi API thật từ môi trường test
  (không có key). Lần đầu dùng nên xem vài frame xem Claude điền có đúng ý không, rồi sửa: tool học từ đó.
- 🟡 **SAM3 thật trên GPU:** bf16 / fp16, cách dựng crosswalk / drivable / curb, vật che mũ / ria test bằng engine giả
  và mask giả trên ảnh thật của bạn. Cần thử trên máy có GPU.
- 🟡 **Tốc độ theo dõi:** số frame tiết kiệm đo bằng mô phỏng; thời gian thật phụ thuộc GPU.
- Ảnh bạn gửi chỉ dùng để thử trên máy làm việc, không đưa vào repo (có mặt người thật).
- Không làm trong bản này: học chung qua máy chủ của nhóm (vẫn chờ bàn với mentor).
