# Viết profile cho một guideline

Một profile là một file YAML trong `profiles/`. Nó nói cho backend biết: label nào dùng prompt gì,
ra shape gì, và áp luật nào. File có tên bắt đầu bằng `_` bị bỏ qua. Sau khi sửa, bấm
**Nạp lại profile** ở tab Label của panel (không cần khởi động lại backend).

## Phần đầu

| Trường | Ý nghĩa |
| --- | --- |
| `id` | Mã profile (mặc định là tên file) |
| `title`, `description` | Hiển thị trên panel |
| `mode` | `instance`: từng đối tượng riêng (box, polygon, polyline, mask). `semantic`: mỗi pixel một class. `keypoints`: skeleton (xem mục cuối). `tracking`: rectangle track trên video |
| `auto_pick` | `false`: profile chỉ dùng khi được chọn đích danh ở đầu panel, không tham gia "Tự chọn profile" |
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
| `polyline_mode` | `centerline`: một đường theo tim vạch. `edges`: hai đường theo hai mép ngoài, đi theo đường cong (vạch đôi). `long_edges`: hai đường thẳng theo hai mép dài của cả vùng (crosswalk) |
| `merge_px` | Gom các mảnh mask cùng label cách nhau tới mức này thành một vạch. Mặc định 12 cho vạch làn, 40 cho `long_edges` |
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

## Profile keypoints (skeleton)

`mode: keypoints` dùng cho task có label kiểu skeleton. Ví dụ đầy đủ: `profiles/driver_pose17_face50_v1.yaml`.
Mỗi label là một skeleton; `kind` cho biết model nào đặt điểm.

```yaml
labels:
  - name: person
    shape: skeleton
    kind: body                      # ViTPose, 17 khớp COCO
    prompts: ["person"]             # SAM3 tìm người trước, rồi ViTPose đặt khớp trong hộp đó
    keypoints:
      - {id: 1, name: "1", source: nose}
      - {id: 2, name: "2", source: right_eye}     # source = tên khớp COCO của model
    edges: [[1, 2]]                 # đánh số từ 1 theo thứ tự keypoints

  - name: mattrai
    shape: skeleton
    kind: face                      # MediaPipe FaceLandmarker, 478 điểm
    contour: closed                 # open | closed: tool tự sinh cạnh nối các điểm liên tiếp
    face_side: left                 # nửa mặt bên trái / phải của ẢNH (dùng cho luật mặt nghiêng và kiểm tra trái/phải)
    min_visible: 4                  # thấy ít hơn số này: Outside cả skeleton
    inside_of: null                 # tên skeleton phải bao quanh skeleton này (moitrong nằm trong moingoai)
    keypoints:
      - {id: 15, path: [33, 246, 161, 160, 159, 158, 157, 173, 133], t: 0.25, by: chord}
```

### Ghép label của task với skeleton của profile

Tên label trong task thật thường khác tên trong guideline (ví dụ `person_vf17` thay cho `person`). Tool ghép
theo thứ tự tin cậy giảm dần, mỗi skeleton của profile chỉ ghép với một label:

| Cách | Điều kiện | Ví dụ |
| --- | --- | --- |
| Đúng tên | Trùng `name` hoặc một `aliases` (không phân hoa thường, gạch dưới = dấu cách) | `person`, `left_eye` |
| Tên gần giống | Tên label **chứa** `name` hoặc một alias dài từ 4 ký tự; bỏ dấu tiếng Việt, bỏ gạch và dấu cách; tên dài hơn được xét trước | `person_vf17`, `Mắt trái`, `longmaytrai_vf50` |
| Dãy số của các điểm | Tên các điểm là số và đúng bằng dãy `id` của skeleton. Chỉ dùng khi các skeleton của task không dùng chung số nào | điểm `14`…`21` → `mattrai` |
| Số điểm | Chỉ một skeleton chưa ghép có đúng số điểm đó, và chỉ một label chưa ghép có số điểm đó | 17 điểm → `person`; 12 điểm → `moingoai` |

Tab Label ghi tool đã coi label là skeleton nào và theo cách nào. Khi tool không nhận ra (ví dụ ba skeleton 8
điểm với tên không gợi ý gì), hoặc nhận sai, chọn skeleton ở ô ngay dưới tên label; lựa chọn được nhớ theo tên
label. Để tool tự nhận một cách đặt tên mới, thêm tên đó vào `aliases` của skeleton trong profile.

### Ghép điểm của profile với sublabel của task

Theo thứ tự: **tên** (kể cả `right_eye`, `r_eye`, `RightEye`, `Right Eye (R)`), rồi **số** trong tên so với
`id` (`2`, `kp_2`, `2 - Right Eye`), cuối cùng theo **thứ tự** nếu số điểm bằng nhau mà không khớp được
cách nào. Ghép theo số chỉ được nhận khi mọi điểm đều khớp: task đánh số `0`–`16` trong khi guideline đánh
`1`–`17` sẽ rơi xuống ghép theo thứ tự, và tab Label báo để bạn kiểm tra. Sublabel không ghép được với
điểm nào luôn là Outside.

### Nguồn toạ độ của một điểm

| Trường | Dùng cho | Ý nghĩa |
| --- | --- | --- |
| `source` | `kind: body` | Tên khớp COCO: `nose`, `left_eye` … `right_ankle`. Với `lr_convention: image`, `right_*` là khớp nằm bên **phải ảnh** |
| `path` | `kind: face` | Dãy chỉ số điểm MediaPipe, theo thứ tự từ trái sang phải trên ảnh, nối thành một đường |
| `t` | `kind: face` | Vị trí trên đường đó, 0 = đầu, 1 = cuối |
| `by` | `kind: face` | `chord`: `t` tính theo chiều rộng giữa hai đầu mút (1/4 chiều rộng mắt). `length`: `t` tính theo chiều dài dọc đường (chia đều lông mày) |
| `index` | `kind: face` | Cách viết ngắn cho một điểm MediaPipe duy nhất (profile cũ) |

Chỉnh vị trí một landmark bị lệch có hệ thống: đổi `t` (trượt dọc đường) hoặc đổi `path` (đường khác).
Ví dụ điểm 10 của sống mũi luôn quá cao so với khoé mắt trong: tăng `t` từ 0.07 lên 0.12. Bản đồ chỉ số
điểm của MediaPipe: tìm "MediaPipe face mesh landmarks map".

### `rules` của profile keypoints

| Luật | Tham số | Tác dụng |
| --- | --- | --- |
| `pose.lr_convention` | `image` \| `anatomical` | `image`: xếp lại để R ở phải ảnh, L ở trái ảnh, theo từng chuỗi tay / chân / đầu. `anatomical`: giữ trái/phải theo người như model trả về |
| `pose.max_people`, `pose.pick` | số, `largest` | Giới hạn số người mỗi ảnh (0 = không giới hạn); vượt thì giữ người có hộp lớn nhất và gắn `UNCERTAIN_SCOPE` |
| `pose.min_person_height_px` | px | Người thấp hơn mức này không gán (0 = tắt) |
| `pose.visible_score`, `pose.occluded_score` | 0..1 | Độ tin cậy từ `visible_score`: Visible. Giữa hai mức: Occluded kèm cờ. Dưới `occluded_score`: Outside |
| `pose.coincident_px` | px | Hai điểm Visible gần nhau hơn mức này bị gắn cờ |
| `face.max_faces`, `face.min_face_px` | số, px | Giới hạn số mặt (0 = không giới hạn); bỏ mặt hẹp hơn `min_face_px` |
| `face.yaw` | `nose`, `left_corner`, `right_corner`, `ratio`, `occluded_ratio` | Tỉ lệ khoảng cách ngang từ chóp mũi tới hai khoé mắt ngoài. Từ `ratio`: skeleton có `face_side` ở phía xa được gắn cờ `UNCERTAIN_BOUNDARY`. Từ `occluded_ratio`: đặt Occluded kèm cờ `ATTRIBUTE_CHECK` |
| `face.occluders` | danh sách prompt | SAM3 tìm các vật này; điểm nằm dưới chúng là Occluded, và dưới `min_visible` thì Outside cả skeleton. Thêm `"steering wheel"`, `"seat belt"`, `"hair"` nếu bộ ảnh cần; bỏ `"sunglasses"` nếu SAM3 hay nhầm kính trong |
| `dedup.existing_iou` | 0..1 | Trùng với skeleton đã có trên frame (pre-label): gắn cờ và bỏ tick |
| `qa.one_per_label` | `true` \| `false` | Tab Kiểm tra báo lỗi khi một label có hơn một skeleton trên frame |

