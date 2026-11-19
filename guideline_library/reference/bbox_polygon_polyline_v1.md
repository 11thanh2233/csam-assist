# Annotation Guideline — Bounding Box, Polygon & Polyline (V1)

> Bản tóm lược của `BBox_Polygon_Polyline_Annotation_Guideline_V1.docx`, giữ nguyên cấu trúc và luật.

| Dữ liệu | Ảnh đường phố giao thông, 1280 x 720, .jpg |
|---|---|
| Công cụ | CVAT |
| Phạm vi | Bounding box, Polygon, Polyline |
| Nguyên tắc | Làm theo guideline, các case không rõ phải đưa mentor review |

## 1. Phạm vi và dữ liệu sử dụng

- Học viên không sử dụng annotation/ground truth có sẵn.
- Chỉ annotate các class được liệt kê, không tự tạo class mới, không đổi tên class, không gộp class theo cảm tính.

## 2. Taxonomy và loại shape bắt buộc

| Nhóm nhãn | CVAT shape | Class |
|---|---|---|
| Object instance | Rectangle | pedestrian, rider, car, truck, bus, train, motorcycle, bicycle, traffic light, traffic sign |
| Drivable area | Polygon | area/direct, area/alternative |
| Lane marking | Polyline | lane/crosswalk, lane/double white, lane/double yellow, lane/double other, lane/road curb, lane/single white, lane/single yellow, lane/single other |

Định nghĩa:

- pedestrian: người đi bộ (đứng, đi, chạy, ngồi…). Không bao gồm túi xách, ba lô, ô.
- rider: người điều khiển hoặc ngồi trên phương tiện hai bánh.
- car: ô tô con và xe van chở người. truck: xe tải và xe van chở hàng. bus: xe buýt. train: tàu hoả.
- area/direct: vùng mặt đường xe đang đi hoặc có thể đi tiếp trực tiếp, không cần chuyển làn.
- area/alternative: vùng xe có thể chuyển sang (làn cùng chiều); không vẽ đường ngược chiều.
- lane/single|double white|yellow|other: vạch đơn/đôi theo màu, dùng cho cả nét đứt và nét liền.
- lane/road curb: đường biên bó vỉa. lane/crosswalk: vạch cho người đi bộ sang đường.

## 3. Quy tắc Bounding Box

- Box bao sát đối tượng theo 4 extreme point, không quá rộng hoặc quá chặt.
- Không annotate đối tượng không tồn tại hoặc ngoài danh sách class (FP); không bỏ sót đối tượng hợp lệ (FN).
- Mỗi object một box riêng; box được phép chồng lấn.
- Đối tượng bị che hoặc bị cắt bởi biên ảnh không quá 80% vẫn phải annotate; trên 80% khuyến khích annotate nếu đủ bằng chứng.
- Không gán reflection, bóng đổ, hình trên billboard/màn hình thành object thật.

Attribute: `occluded = true` khi bị object khác che một phần; `truncated = true` khi bị cắt bởi biên ảnh; có thể cả hai.

## 4. Quy tắc Polygon và Polyline

- Polygon (drivable area): điểm bám sát ranh giới, không tự giao cắt; ít điểm ở đoạn thẳng, nhiều điểm ở đoạn cong.
- Polyline (lane marking): đi theo tim lane marking theo quy ước của bài, không nối tắt qua vùng không có vạch; giữ hướng vẽ nhất quán; kết thúc tại điểm không còn đủ bằng chứng thị giác.

## 5. Quy tắc khi class hoặc boundary không rõ

Ưu tiên review thay vì đoán. Dùng majority voting (inter-annotator agreement) trong nhóm, rồi tạo Issue gửi mentor:

| Issue type | Ví dụ |
|---|---|
| UNCERTAIN_CLASS | car/van vs truck |
| UNCERTAIN_BOUNDARY | không rõ extent do occlusion |
| UNCERTAIN_SCOPE | không chắc object có thuộc phạm vi bài |
| ATTRIBUTE_CHECK | không chắc occluded / truncated |
| LOW_VISIBILITY | thiếu sáng, ngược sáng, mưa, sương mù, quá bé, motion blur |

## 6. Checklist trước khi Submit

- [ ] Đúng class và shape theo taxonomy.
- [ ] Không bỏ sót object thuộc scope, không annotation trùng lặp.
- [ ] Box sát object, không vượt biên ảnh; occluded/truncated đúng.
- [ ] Polygon/polyline bám đúng biên, không tự cắt, không nối qua vùng thiếu evidence.
- [ ] Case không chắc đã tạo Issue.
- [ ] Đã check 02 lần toàn dữ liệu trước khi save.

## 7. Tiêu chí đánh giá của reviewer

Taxonomy · Completeness · Geometry Accuracy · Attributes · Consistency · Escalation.
