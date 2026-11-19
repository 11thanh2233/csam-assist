# Profile đã ngừng dùng

Các profile ở đây không được tool nạp. Muốn dùng lại một profile: chuyển file của nó ra thư mục `profiles/`,
**và chuyển profile đã thay nó vào đây** (profile mới ghi `supersedes: [...]`, nên khi cả hai cùng nằm trong
`profiles/` thì tool chỉ nạp profile mới). Sau đó bấm "Nạp lại profile" hoặc khởi động lại backend.

- `coco_pose_face_lab_v1.yaml`: quy ước Lab 4 (người 17 điểm tên COCO với trái/phải theo người, mặt 13 điểm).
  Được thay bằng `driver_pose17_face50_v1.yaml` theo hai guideline HumanPose-17 và Facial Landmark-50.
  Hai profile dùng chung tên label `person` nhưng quy ước trái/phải ngược nhau, nên không để cùng lúc:
  khi cả hai cùng nằm trong `profiles/`, chế độ tự chọn profile không biết task đang theo bên nào.

  Khi dùng lại, profile này vẫn đặt đủ 17 + 13 điểm và giữ ngưỡng 60px. Hai thứ khác với trước:
  cờ đổi sang 5 mã chuẩn (`ATTRIBUTE_CHECK`, `UNCERTAIN_CLASS`...) thay cho `vis_flag` / `lr_swap`, và luật
  "mặt nghiêng mạnh thì mắt xa camera là occluded" không còn được áp tự động (bản mới áp luật đó theo từng
  skeleton mắt/lông mày, mà profile cũ gộp cả mặt trong một skeleton).
