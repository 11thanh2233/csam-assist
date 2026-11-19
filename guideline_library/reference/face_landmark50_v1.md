<!-- Nguồn: Face_Landmark50_Annotation_Guideline_V1.docx (chuyển sang markdown, bỏ hình minh hoạ). -->

# Annotation Guideline: Facial Landmark-50

Student Annotation Guideline • CVAT

| **Dữ liệu**    | Ảnh khuôn mặt người lái trong cabin xe                       |
|----------------|--------------------------------------------------------------|
| **Công cụ**    | CVAT                                                         |
| **Phạm vi**    | 50 face landmarks, chia thành 7 skeleton                     |
| **Nguyên tắc** | Làm theo guideline, các case không rõ phải đưa mentor review |

# 1. Phạm vi và dữ liệu sử dụng

- Ảnh 1280 x 720 pixel, được chụp trong khoang calib, ảnh dạng rời rạc, mặc định chỉ gán nhãn người lái.
- Mỗi nhóm nhận 150 ảnh, gồm 50 ảnh có pre-label (gán nhãn tự động) và 100 ảnh gốc. Tất cả 150 ảnh đều phải được kiểm tra, tinh chỉnh hoặc vẽ mới để đảm bảo annotation chính xác.
- Chỉ thực hiện annotation trên 7 skeleton được quy định trong tài liệu. Mỗi skeleton bao gồm nhiều keypoints, và mỗi keypoint cần được gán trạng thái (state) tương ứng theo hướng dẫn trong tài liệu này.
- Quy tắc trái phải: Trái/phải theo cách nhìn trên ảnh: longmaytrai và mattrai ở phía trái của khuôn mặt trên ảnh, longmayphai và matphai ở phía phải. Tham chiếu là khuôn mặt được nhìn thấy, không cần quan tâm mirror effect.

# 2. Taxonomy và loại shape bắt buộc

| **Nhóm nhãn** | **Skeleton (CVAT shape)** | **Point ID** | **Số điểm** | **Contour** |
|---------------|---------------------------|--------------|-------------|-------------|
| Lông mày      | longmaytrai               | 0 – 4        | 5           | hở          |
|               | longmayphai               | 5 – 9        | 5           | hở          |
| Sống mũi      | songmui                   | 10 – 13      | 4           | hở          |
| Mắt           | mattrai                   | 14 – 21      | 8           | kín         |
|               | matphai                   | 22 – 29      | 8           | kín         |
| Môi           | moingoai                  | 30 – 41      | 12          | kín         |
|               | moitrong                  | 42 – 49      | 8           | kín         |

- Tên label viết liền, không dấu, không viết hoa, không đổi.
- **Point ID chạy liên tục 0–49 qua cả 7 skeleton, không reset theo từng skeleton.**
- Vòng kín có tọa độ chạy theo cùng chiều kim đồng hồ, bắt đầu từ điểm trái nhất.

## 2.1. Định nghĩa và nguyên tắc annotation

**longmaytrai (0–4), longmayphai (5–9).** Đặt 5 điểm mỗi bên trên mép lông ở bờ trên (ranh giới giữa lông mày và da trán), chia đều thành 4 đoạn bằng nhau dọc theo chiều dài lông mày. Điểm phải nằm trên đường biên, không đặt giữa đám lông hoặc lên da trán.

- Lông mày trái: các điểm 0–4 được đánh index từ ngoài vào trong, với điểm 0 là đuôi ngoài và điểm 4 là đầu trong.
- Lông mày phải: các điểm 5–9 được đánh index đối xứng với bên trái, từ trong ra ngoài, với điểm 5 là đầu trong và điểm 9 là đuôi ngoài.

**songmui (10–13).** 4 điểm chia đều dọc sống mũi (ba đoạn gần bằng nhau). 10: đỉnh sống mũi, điểm lõm giữa hai mắt, ngang khoé mắt trong. 13: chân sống mũi, **không phải đỉnh mũi**: nằm phía trên lỗ mũi, chưa chạm chóp mũi.

**mattrai (14–21), matphai (22–29).** Contour kín 8 điểm dọc theo bờ mi (ranh giới giữa da mi và nhãn cầu, nơi lông mi mọc). Gồm 2 điểm khoé mắt, 3 điểm mí trên và 3 điểm mí dưới. Ba điểm mí trên và ba điểm mí dưới được chia đều khoảng cách giữa hai khoé tại các vị trí 1/4, 1/2 và 3/4 chiều rộng mắt. Đặt điểm chính xác trên bờ mi, không đặt lên lông mi hoặc vào lòng trắng.

- Mắt trái: điểm 14 là khoé ngoài, điểm 18 là khoé trong. Index bắt đầu từ điểm 14, chạy dọc mí trên từ trái sang phải (15–17), sau đó vòng về theo mí dưới (19–21).
- Mắt phải: điểm 22 là khoé trong, điểm 26 là khoé ngoài. Index bắt đầu từ điểm 22, chạy dọc mí trên từ trái sang phải (23–25), sau đó vòng về theo mí dưới (27–29).

**moingoai (30–41).** Contour kín 12 điểm dọc ranh giới môi đỏ và da quanh miệng. Điểm 30 và 36 là hai khoé miệng, 33 và 39 là hai mốc chính giữa.

- Bờ trên: các điểm 31–35 nằm trên bờ môi trên (từ trái sang phải), bố trí theo tỉ lệ 0,18 · 0,36 · 0,50 · 0,66 · 0,84 chiều rộng môi (từ khoé trái sang khoé phải). Điểm 33 và 39 nằm chính giữa môi, làm mốc chia khoảng.
- Bờ dưới: các điểm 37–41 nằm trên bờ môi dưới, chia đều khoảng cách giữa hai khoé miệng theo chiều ngược lại (từ phải sang trái). Contour bám sát ranh giới giữa môi đỏ và da quanh miệng.

**moitrong (42–49).** Contour kín 8 điểm dọc mép trong môi (ranh giới giữa môi và khoang miệng), luôn nằm hoàn toàn bên trong contour môi ngoài (moingoai). Điểm 42 và 46 là hai khoé miệng. Các điểm trên mép môi trên và dưới được chia đều tại các vị trí 1/4, 1/2 và 3/4 chiều rộng miệng.

- Bờ trên: các điểm 43–45 nằm trên mép trong môi trên (từ trái sang phải), điểm 44 là mốc chính giữa.
- Bờ dưới: các điểm 47–49 nằm trên mép trong môi dưới (từ phải sang trái), điểm 48 là mốc chính giữa.

# 3. Trạng thái điểm

Dùng property có sẵn của CVAT, không tạo attribute mới. **Không dùng Hidden thay Outside** vì Hidden chỉ ẩn hiển thị, không lưu vào dữ liệu.

| **Trạng thái** | **Cấu hình CVAT**         | **Điều kiện** | **Toạ độ** |
|----------------|---------------------------|---------------|------------|
| Visible        | Outside = 0, Occluded = 0 | Nhìn thấy trực tiếp. | Phải đúng |
| Occluded       | Occluded = 1              | Bị che nhưng còn nội suy ra được vị trí: không thấy trực tiếp, nhưng điểm nằm trong khung hình và suy ra được vị trí từ các điểm liền kề, tư thế và tỉ lệ landmark, khuôn mặt. | Phải đúng theo ước lượng, nội suy một cách hợp lý |
| Outside        | Outside = 1               | Ngoài khung hình, hoặc bị che đến mức không còn căn cứ ước lượng. | Không dùng đến |

## 3.1. Ngưỡng quyết định khi cả một bộ phận bị che

| **Skeleton**             | **Còn nhìn thấy**    | **Xử lý** |
|--------------------------|----------------------|-----------|
| mattrai, matphai         | Từ 4/8 điểm trở lên  | Đặt đủ contour, điểm không thấy đánh Occluded |
|                          | Dưới 4/8 điểm        | Đặt đủ nếu có đủ bằng chứng thị giác; còn không thì gán Outside cả skeleton |
| longmaytrai, longmayphai | Từ 3/5 điểm trở lên  | Đặt đủ, điểm không thấy đánh Occluded |
|                          | Dưới 3/5 điểm        | Đặt đủ nếu có đủ bằng chứng thị giác; còn không thì gán Outside cả skeleton |
| moingoai                 | Từ 6/12 điểm trở lên | Đặt đủ, điểm không thấy đánh Occluded |
|                          | Dưới 6/12 điểm       | Đặt đủ nếu có đủ bằng chứng thị giác; còn không thì gán Outside cả skeleton |
| moitrong                 | Từ 4/8 điểm trở lên  | Đặt đủ, điểm không thấy đánh Occluded |
|                          | Dưới 4/8 điểm        | Đặt đủ nếu có đủ bằng chứng thị giác; còn không thì gán Outside cả skeleton |
| songmui                  | Từ 2/4 điểm trở lên  | Đặt đủ, điểm không thấy đánh Occluded |
|                          | Dưới 2/4 điểm        | Đặt đủ nếu có đủ bằng chứng thị giác; còn không thì gán Outside cả skeleton |

**Sai số cho phép:** ±5 pixel cho vị trí visible; ±20 pixel cho vị trí occluded.

# 4. Tình huống đặc biệt

| **Tình huống** | **Xử lý** |
|----------------|-----------|
| Mặt nghiêng mạnh | Lông mày và mắt phía xa bị che, pre-label thường dồn cụm điểm về giữa mặt. Chủ động chỉnh sửa từng cụm sao cho kết quả skeleton hợp lý và chính xác. Nếu không thể xác định vị trí ngay cả khi ước lượng, gán **Outside** cho skeleton tương ứng. Không dồn các keypoints về giữa mặt. |
| Nheo hoặc nhắm mắt | Vẫn phải đặt đủ 8 keypoints, tạo thành vòng kín quanh mắt. Khi nhắm mắt, mí trên và mí dưới nằm sát hoặc trùng nhau theo khe mí (các cặp 15–21, 16–20, 17–19 có thể trùng nhau). Hai khoé mắt vẫn phải đặt đúng vị trí. Gán **Visible** nếu vẫn xác định được khe mí; chỉ gán **Occluded** khi mắt bị vật khác che khuất. |
| Miệng mở / miệng đóng | **Mở:** moitrong tách khỏi moingoai, ôm khoang miệng thật; thấy răng hoặc lưỡi thì đặt lên mép môi. Làm điểm 44 và 48 trước rồi chia các điểm còn lại. **Đóng:** moitrong vẫn đủ 8 điểm dọc đường khép môi; các cặp 43–49, 44–48, 45–47 được trùng hoặc gần trùng. Không xoá moitrong, không Outside chỉ vì miệng đóng. |
| Bị che bởi vật thể khác (kính, tay, vô-lăng, dây an toàn che mặt lúc đeo...) | Nếu vẫn xác định được vị trí keypoint dù có đeo kính, gán **Visible**. Nếu keypoint bị chi tiết khác che khuất nhưng vẫn có thể ước lượng vị trí, gán **Occluded**. Nếu không thể xác định hoặc suy luận vị trí keypoint, ví dụ người lái đeo kính râm màu đen không xác định được đủ điểm, gán **Outside**. |
| Ảnh thiếu sáng, mờ, nhòe | Chủ động nội suy một cách hợp lý, bàn với team member để đưa ra quyết định gán nhãn phù hợp. |
| Không có mặt / nhiều hơn một mặt | Không có mặt thì không cần label frame đó; có nhiều hơn một mặt thì chọn mặt người lái xe. |
| Pre-label đã đúng | Giữ nguyên vị trí nhưng vẫn phải đặt trạng thái cho cả 50 điểm. |

# 5. Quy tắc khi class hoặc boundary không rõ

Khi gặp case chưa chắc chắn, ưu tiên quy trình review thay vì “đoán cho xong”. Dùng majority voting **“inter-annotator agreement”** giữa team member để quyết định trước, và format issue như sau, gửi tới mentor.

| **Issue type**     | **Ví dụ** |
|--------------------|-----------|
| UNCERTAIN_CLASS    | không chắc nhóm điểm thuộc mattrai hay matphai khi mặt nghiêng mạnh |
| UNCERTAIN_BOUNDARY | không rõ vị trí điểm do bị gọng kính, tay, vật thể khác che |
| UNCERTAIN_SCOPE    | ảnh không có mặt, hoặc có mặt thứ hai (hành khách, phản chiếu) |
| ATTRIBUTE_CHECK    | không chắc Visible / Occluded / Outside |
| LOW_VISIBILITY     | landmark khó quan sát do thiếu sáng, ngược sáng, loá kính, nhoè hoặc mặt quá nhỏ |

# 6. Checklist chất lượng trước khi Submit

| **✓** | **Checklist** |
|-------|---------------|
| ☐     | Đủ 7 skeleton và 50 điểm - không thừa, có trạng thái từng điểm, không nhầm tên class giữa skeleton kín, hở |
| ☐     | Không đặt keypoint lên vật thể che khuất (kính, tóc, tay, khẩu trang...) hoặc ra ngoài vùng khuôn mặt. Nếu bị che, ước lượng vị trí và gán trạng thái Occluded hoặc Outside theo quy định. |
| ☐     | Độ chính xác của từng keypoint, và tổng thể thuộc phạm vi sai số cho phép. |
| ☐     | Các trường hợp không chắc chắn phải tạo Issue/comment để reviewer xử lý, và raise issue lên mentor. |
| ☐     | Đã check 02 lần toàn dữ liệu trước khi save. |

# 7. Tiêu chí đánh giá của reviewer

| **Hạng mục**            | **Reviewer kiểm tra** |
|-------------------------|-----------------------|
| Taxonomy & Completeness | Đúng 7 skeleton, 50 điểm, mọi điểm có trạng thái. |
| Landmark Accuracy       | Dựa vào sai số cho phép so với vị trí landmark chuẩn bằng kinh nghiệm của mình. |
| Left/Right & Topology   | Trái/phải theo khuôn mặt trên ảnh; thứ tự điểm đúng, mắt không bắt chéo, moitrong phải ở trong moingoai và không được bắt chéo. |
| Consistency             | Các annotator áp dụng thống nhất guideline, taxonomy và quy tắc xử lý edge cases; cùng một tình huống phải được annotate nhất quán giữa các thành viên và xuyên suốt toàn bộ batch dữ liệu. |
| Escalation              | Case chưa có rule được đưa mentor/lead chốt, không tự invent rule. |
