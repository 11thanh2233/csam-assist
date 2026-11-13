# Viết profile cho một guideline

Một profile là một file YAML trong `profiles/`. Nó nói cho backend biết: label nào dùng prompt gì,
ra shape gì, và áp luật nào. File có tên bắt đầu bằng `_` bị bỏ qua. Sau khi sửa, bấm
**Nạp lại profile** ở tab Label của panel (không cần khởi động lại backend).

## Phần đầu

| Trường | Ý nghĩa |
| --- | --- |
| `id` | Mã profile (mặc định là tên file) |
| `title`, `description` | Hiển thị trên panel |
| `mode` | `instance`: từng đối tượng riêng (box, polygon, polyline, mask). `semantic`: mỗi pixel một class |
| `match_labels` | Vài tên label đặc trưng. Task có đủ các label này thì profile được ưu tiên khi "Tự chọn profile" |
| `defaults.threshold` | Ngưỡng tin cậy chung (0..1) |
| `defaults.min_area_px` | Mask nhỏ hơn mức này bị bỏ |

Cách tự chọn: profile khớp nhiều tên label của task nhất, và phải khớp ít nhất một nửa. Không có
profile nào đạt thì dùng `generic`.

## `labels`

```yaml
labels:
  - name: traffic sign          # tên label trong CVAT
    aliases: [sign]             # tên khác có thể gặp
    shape: rectangle            # rectangle | polygon | polyline | mask (bỏ qua ở mode semantic)
    prompts: ["traffic sign"]   # [] = label được suy ra bằng luật, không có prompt riêng
    threshold: 0.45             # tuỳ chọn
    min_area_px: 12             # tuỳ chọn
    group: sign                 # tuỳ chọn: các label cùng group loại trừ nhau khi trùng vị trí
```

Trường riêng cho polyline:

| Trường | Ý nghĩa |
| --- | --- |
| `polyline_mode` | `centerline` (tim của vạch) hoặc `long_edges` (hai mép dài của vùng) |
| `join_dashes` | `true`: nối các đoạn nét đứt thẳng hàng thành một polyline |
| `lane: {kind, color}` | `kind`: single/double; `color`: white/yellow/other. Dùng cho luật `lane_color` |

Trường `role` chỉ dùng cho drivable area: `drivable_direct` (prompt của label này là prompt tìm mặt
đường) và `drivable_alt` (suy ra, `prompts: []`).

### Viết prompt

- Cụm danh từ tiếng Anh, ngắn, cụ thể: `"traffic light"`, `"white lane line"`, `"curb"`.
- Khái niệm rộng thì tách thành vài prompt cụ thể: `vegetation` thành `["tree", "bush"]`.
- Hai class dễ lẫn (car/truck): cho cùng `group` để tool giữ class điểm cao hơn và gắn cờ khi sát điểm.
- Thử nhanh: sửa prompt ngay trên tab Label của panel, chạy lại gợi ý, ưng rồi mới chép vào YAML.

## `rules`

Mỗi luật có thể bỏ hẳn (không ghi) hoặc tắt bằng `enabled: false`.

| Luật | Tham số | Tác dụng |
| --- | --- | --- |
| `dedup` | `iou`, `class_margin`, `existing_iou` | Khử trùng giữa các đề xuất; gắn `UNCERTAIN_CLASS` khi hai class sát điểm; đánh dấu trùng với shape đã có |
| `rider_split` | `pedestrian_label`, `rider_label`, `vehicle_labels`, `min_overlap` | Người chồng lên xe hai bánh thành rider |
| `occluded` | `min_overlap` | Đặt occluded khi bị object gần camera hơn đè lên (kèm `ATTRIBUTE_CHECK`) |
| `truncated` | `attribute`, `border_px` | Đặt attribute checkbox khi box chạm biên ảnh |
| `low_visibility` | `min_side_px`, `score_margin` | Gắn `LOW_VISIBILITY` cho object quá bé hoặc điểm sát ngưỡng |
| `polygon` | `epsilon` | Sai số giản lược đỉnh (px) |
| `lane` | `sample_step_px`, `max_gap_px`, `join_gap_px`, `epsilon`, `min_length_px`, `exclusive_region_label` | Cách dựng polyline từ mask vạch kẻ |
| `lane_color` | `labels.single/double.{white,yellow,other}` | Đo màu vạch từ pixel, đổi class nếu lệch prompt |
| `drivable` | `ego_point`, `barrier_px`, `extend_near_px`, `extend_far_px`, `min_area_px`, `max_alternatives`, `ignore_lanes`, `opposite_separators` | Tách mặt đường thành direct / alternative |
| `semantic` | `output_shape`, `priority`, `min_region_px`, `fill_gaps_px`, `warn_unlabeled_pct`, `respect_existing`, `merge_instances`, `polygon_epsilon` | Ghép mask thành bản đồ class không chồng lấn |
| `qa` | `bounds_tolerance_px`, `min_box_side_px`, `min_polyline_px`, `duplicate_iou`, `max_overlap_px`, `min_coverage_pct` | Ngưỡng cho tab Kiểm tra |

`semantic.priority` liệt kê label từ ưu tiên cao xuống thấp: khi hai class cùng nhận một pixel,
class đứng trước thắng. Label không có trong danh sách được ưu tiên cao nhất.

## Từ một guideline mới tới profile: các bước

1. Chép bảng taxonomy thành mục `labels` (tên, shape).
2. Viết prompt cho từng label; label nào là "trường hợp riêng" của label khác thì để `prompts: []`
   và dùng luật để tách.
3. Với mỗi câu luật trong guideline, tìm luật tương ứng ở bảng trên và điền tham số. Câu nào không
   có luật tương ứng: ghi lại để người annotate tự làm, hoặc thêm hàm trong `backend/pipeline.py`.
4. Chép checklist thành ngưỡng trong `qa`.
5. Chạy trên 10-20 frame, chỉnh prompt và ngưỡng, rồi mới dùng cho cả batch.
