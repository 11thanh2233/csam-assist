<!-- Nguồn: HumanPose17_Annotation_Guideline_V1.docx (chuyển sang markdown, bỏ hình minh hoạ). Hình minh hoạ trong bản gốc cho thấy 18 cạnh của skeleton: 1-2, 1-3, 2-4, 3-5, 4-6, 5-7, 6-7, 6-8, 7-9, 8-10, 9-11, 6-12, 7-13, 12-13, 12-14, 13-15, 14-16, 15-17. -->

# Annotation Guideline: HumanPose-17 Body Landmarks

Student Annotation Guideline • CVAT

| **Dữ liệu**    | Ảnh người lái trong cabin xe                                 |
|----------------|--------------------------------------------------------------|
| **Công cụ**    | CVAT                                                         |
| **Phạm vi**    | Skeleton person, 17 keypoint (HumanPose-17)                  |
| **Nguyên tắc** | Làm theo guideline, các case không rõ phải đưa mentor review |

# 1. Phạm vi và dữ liệu sử dụng

- Ảnh 960 x 540 pixel, được chụp trong khoang calib, ảnh dạng rời rạc, mặc định chỉ gán nhãn người lái.

- Mỗi nhóm nhận 150 ảnh, gồm 50 ảnh có pre-label (gán nhãn tự động) và 100 ảnh gốc. Tất cả 150 ảnh đều phải được kiểm tra, tinh chỉnh hoặc vẽ mới để đảm bảo annotation chính xác.

- Chỉ dùng label person và 17 sublabel đã cấu hình; không tạo label/attribute mới, không đổi tên sublabel.

- Quy tắc trái/phải: Các điểm R (số chẵn 2–16) nằm phía phải ảnh, các điểm L (số lẻ 3–17) nằm phía trái ảnh, bất kể người lái quay mặt, quay lưng, vặn mình hay ảnh xoay 90°. Không suy luận theo tay/chân giải phẫu.

# 2. Taxonomy và loại shape bắt buộc

| **Nhóm**             | **Keypoint (R / L)** | **Khái niệm / nguyên tắc annotation**                                                                  |
|----------------------|----------------------|--------------------------------------------------------------------------------------------------------|
| **Vùng đầu**         | 1 – Nose             | Chóp mũi; mặt nghiêng vẫn là chóp mũi, không dời về giữa mặt.                                          |
|                      | 2 / 3 – Eye          | Tâm đồng tử (mắt nhắm: tâm khe mí).                                                                    |
|                      | 4 / 5 – Ear          | Ống tai, không phải chóp hay dái tai. Tóc phủ kín tai: ước lượng được thì Occluded, không thì Outside. |
| **Chi trên**         | 6 / 7 – Shoulder     | Tâm khớp vai, tại điểm xoay của cánh tay.                                                              |
|                      | 8 / 9 – Elbow        | Tâm khớp khuỷu.                                                                                        |
|                      | 10 / 11 – Wrist      | Tâm khớp cổ tay, ở nếp gấp cổ tay; tay trên vô-lăng vẫn không dời lên vành vô-lăng.                    |
| **Thân và chi dưới** | 12 / 13 – Hip        | Tâm khớp háng, không phải mép ngoài hông hay cạp quần.                                                 |
|                      | 14 / 15 – Knee       | Tâm khớp gối, giữa xương bánh chè.                                                                     |
|                      | 16 / 17 – Ankle      | Tâm khớp cổ chân, ngang mắt cá.                                                                        |

**Sai số cho phép:** ±5 pixel cho vị trí visible; ±20 pixel cho vị trí occluded.

# 3. Trạng thái điểm

Dùng property có sẵn của CVAT, không tạo attribute mới. **Không dùng Hidden thay Outside** vì Hidden chỉ ẩn hiển thị, không lưu vào dữ liệu. Xét từng khớp theo thứ tự trong bảng:

| **Trạng thái** | **Cấu hình CVAT**         | **Điều kiện**                                                                                                                                                                              | **Toạ độ**                                        |
|----------------|---------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------|
| **Visible**    | Outside = 0, Occluded = 0 | Nhìn thấy trực tiếp.                                                                                                                                                                       | Phải đúng                                         |
| **Occluded**   | Occluded = 1              | Bị che nhưng còn suy ra được vị trí: không thấy trực tiếp, nhưng khớp nằm trong khung hình và suy ra được vị trí từ các khớp liền kề, tư thế và tỉ lệ cơ thể. Đặt điểm ở vị trí ước lượng. | Phải đúng theo ước lượng, nội suy một cách hợp lý |
| **Outside**    | Outside = 1               | Không thấy trực tiếp và khớp nằm ngoài khung hình, hoặc không suy ra được vị trí.                                                                                                          | Không dùng đến                                    |

# 4. Tình huống đặc biệt

| **Tình huống**                                            | **Xử lý**                                                                                                                                                                                 |
|-----------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Keypoint pre-label bị sai nhiều.**                      | Chủ động kéo các cụm keypoints về đúng chỗ, và điền đúng trạng thái.                                                                                                                      |
| **Hai keypoint chồng nhau**                               | Hai khớp Visible không thể trùng toạ độ: tách đúng vị trí, hoặc đánh lại trạng thái cho điểm không thực sự nhìn thấy.                                                                     |
| **Bị che bởi đồ vật (Ghế, cần số, dây an toàn,..)**       | Chủ động nội suy vị trí nếu vẫn còn liên kết kề bên thấy rõ và gán là Occluded; trong trường hợp không còn dấu hiệu để nội suy thì gán là Outside.                                        |
| **Bị che bởi chính cơ thể do góc nhìn chéo của Camera**   | Trong trường hợp bị che 1 phần hông, chân, mắt, tai. Nếu vẫn còn liên kết kề bên để nội suy thì vẽ và gán là Occluded; trong trường hợp không còn dấu hiệu để nội suy thì gán là Outside. |
| **Người lái vặn mình, quay lưng**                         | Không đảo quy ước R/L: trái ảnh vẫn là L, phải ảnh vẫn là R. Kiểm tra chéo: chuỗi vai–khuỷu–cổ tay mỗi phía phải liên tục.                                                                |
| **Không có người lái, hoặc có người thứ hai trong khung** | Bỏ qua nếu không có người lái, chỉ gán nhãn cho vị trí người lái.                                                                                                                         |
| **Ảnh thiếu sáng, mờ, nhòe**                              | Chủ động nội suy một cách hợp lý, bàn với team member để đưa ra quyết định gán nhãn phù hợp.                                                                                              |

# 5. Quy tắc khi class hoặc boundary không rõ

Khi gặp case chưa chắc chắn, ưu tiên quy trình review thay vì “đoán cho xong”. Dùng majority voting **“inter-annotator agreement”** giữa team member để quyết định trước, và format issue như sau, gửi tới mentor.

| **Issue type**     | **Ví dụ**                                                                                  |
|--------------------|--------------------------------------------------------------------------------------------|
| UNCERTAIN_CLASS    | không chắc điểm thuộc R hay L khi người lái vặn mình hoặc quay lưng                        |
| UNCERTAIN_BOUNDARY | không rõ vị trí khớp do bị vô-lăng, ghế, quần áo che                                       |
| UNCERTAIN_SCOPE    | không xác định được ai là người lái, hoặc có skeleton thứ hai                              |
| ATTRIBUTE_CHECK    | không chắc Visible / Occluded / Outside                                                    |
| LOW_VISIBILITY     | người khó quan sát do thiếu sáng, ngược sáng, nhoè hoặc không phân biệt được người với ghế |

# 6. Checklist chất lượng trước khi Submit

| **✓** | **Checklist**                                                                                         |
|-------|-------------------------------------------------------------------------------------------------------|
| ☐     | Đúng một skeleton person cho người lái; đủ 17 keypoint - không thừa, mỗi điểm có trạng thái rõ ràng.  |
| ☐     | R ở bên phải ảnh, L ở bên trái ảnh; tay và chân là chuỗi liền mạch, không bắt chéo sang bên kia thân. |
| ☐     | Độ chính xác của từng keypoint, và tổng thể thuộc phạm vi sai số cho phép.                            |
| ☐     | Các trường hợp không chắc chắn phải tạo Issue/comment để reviewer xử lý, và raise issue lên mentor.   |
| ☐     | Đã check 02 lần toàn dữ liệu trước khi save.                                                          |

# 7. Tiêu chí đánh giá của reviewer

| **Hạng mục**                | **Reviewer kiểm tra**                                                                                                                                                                       |
|-----------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Taxonomy & Completeness** | Đúng label person, 17 sublabel 1–17; không bỏ sót người lái, không skeleton thừa; mọi điểm có trạng thái.                                                                                   |
| **Keypoint Accuracy**       | Dựa vào sai số cho phép so với vị trí keypoint chuẩn bằng kinh nghiệm của mình.                                                                                                             |
| **Left/Right & Topology**   | R ở bên phải ảnh, L ở bên trái ảnh; vai–khuỷu–cổ tay và hông–gối–cổ chân nối đúng thứ tự, không bắt chéo sang bên kia.                                                                      |
| **Consistency**             | Các annotator áp dụng thống nhất guideline, taxonomy và quy tắc xử lý edge cases; cùng một tình huống phải được annotate nhất quán giữa các thành viên và xuyên suốt toàn bộ batch dữ liệu. |
| **Escalation**              | Case chưa có rule được đưa mentor/lead chốt, không tự invent rule.                                                                                                                          |
