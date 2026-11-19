# Annotation Guideline — Semantic Segmentation (V1)

> Bản tóm lược của `Semantic_Segmentation_Annotation_Guideline_V1.docx`, giữ nguyên cấu trúc và luật.

| Dữ liệu | Ảnh đường phố giao thông, 1280 x 720, .jpg |
|---|---|
| Công cụ | CVAT |
| Phạm vi | Polygon / mask cho semantic segmentation |
| Nguyên tắc | Làm theo guideline, các case không rõ phải đưa mentor review |

## 1. Phạm vi và nguyên tắc chung

- Mỗi pixel được gán một class semantic, không phân biệt instance cùng class.
- Chỉ annotate 19 class quy định; không tự tạo, đổi tên hoặc gộp class.
- Màu chỉ để hiển thị; class xác định theo ngữ nghĩa.

## 2. Danh sách class

road, sidewalk, building, wall, fence, pole, traffic_light, traffic_sign, vegetation, terrain, sky, person, rider, car, truck, bus, train, motorcycle, bicycle.

## 3. Quy tắc vẽ mask và xử lý biên

- CLASS BOUNDARY: bám sát đường biên giữa các vùng; zoom ở vùng phức tạp (person, rider, xe hai bánh, pole, biển báo, đèn).
- KHÔNG CHỒNG LẤN: hai class không cùng chiếm một pixel.
- KHÔNG ĐỂ SÓT PIXEL giữa các vùng liền kề đã rõ; không tự lấp vùng chưa rõ bằng class suy đoán.
- GIỮ ĐÚNG HÌNH DẠNG object mảnh (pole, giá đỡ biển báo/đèn, bicycle, motorcycle); tránh mask phình rộng.
- Không gán reflection, bóng đổ, hình trên billboard/màn hình thành object thật.

## 4. Các cặp class dễ nhầm

| Cặp | Quy tắc |
|---|---|
| Road vs Sidewalk | road cho phương tiện; sidewalk là lối đi bộ tách khỏi mặt đường |
| Building vs Wall | building thuộc công trình; wall là tường độc lập hoặc tường ranh giới |
| Wall vs Fence | wall kín/đặc; fence dạng thanh, lưới, có khoảng hở |
| Vegetation vs Terrain | vegetation là cây, bụi, tán lá; terrain là đất, cỏ thấp |
| Person vs Rider | rider đang điều khiển hoặc ngồi trên phương tiện |
| Cột đèn và đèn giao thông | vẽ riêng phần đèn và phần cột/giá đỡ |
| Car vs truck vs bus | theo loại phương tiện thực tế; không đủ evidence thì tạo Issue |

## 5. Thao tác khuyến nghị trên CVAT

Kiểm tra đúng Job và label set; Mask/Brush cho vùng phức tạp, Polygon cho vùng lớn biên rõ; vẽ vùng lớn trước rồi tới object nhỏ; zoom chỉnh biên; không chắc thì tạo Issue; save thường xuyên.

## 6. Quy tắc khi class hoặc boundary không rõ

Giống guideline BBox: majority voting trong nhóm, rồi Issue theo 5 loại UNCERTAIN_CLASS, UNCERTAIN_BOUNDARY, UNCERTAIN_SCOPE, ATTRIBUTE_CHECK, LOW_VISIBILITY.

## 7. Quality checklist trước khi nộp

- [ ] Mask đúng class, không chồng lấn.
- [ ] Không bỏ sót pixel ở vùng đã rõ class.
- [ ] Boundary bám vùng quan sát được, không tô phần bị che hoặc ngoài ảnh.
- [ ] Object nhỏ, dễ nhầm được kiểm tra ở mức zoom phù hợp.
- [ ] Đúng 19 class, đúng label name và color map.
- [ ] Đã xử lý hoặc chuyển reviewer các Issue còn mở.
- [ ] Đã check 02 lần toàn dữ liệu trước khi save.

## 8. Tiêu chí đánh giá của reviewer

Taxonomy · Completeness · Polygon Accuracy · Instance Overlap · Consistency · Escalation.
