# SAM3 CVAT Assist

Công cụ gợi ý nhãn bằng SAM 3 (Segment Anything Model 3 của Meta) chạy ngay trên giao diện
`cvat.edufun.ai`. Tool phủ một panel lên trang CVAT, đề xuất bounding box, polygon, polyline
hoặc mask theo guideline; bạn duyệt từng đề xuất rồi mới ghi vào job. Không cần import/export file,
không cần quyền admin trên máy chủ CVAT.

Đã viết sẵn luật cho hai guideline:

| Guideline | Profile | Kết quả |
| --- | --- | --- |
| BBox_Polygon_Polyline_Annotation_Guideline_V1 | `bdd_bbox_polygon_polyline_v1` | 10 class object (rectangle), drivable area (polygon), lane marking (polyline) |
| Semantic_Segmentation_Annotation_Guideline_V1 | `semseg_cityscapes19_v1` | 19 class, mỗi pixel một class (mask hoặc polygon) |

Task có bộ label khác vẫn dùng được ngay qua profile `generic` (prompt = tên label).

> **Trạng thái kiểm thử.** Toàn bộ luồng đã chạy qua test tự động với engine giả lập và một CVAT
> giả (16 test backend + 1 test đầu-cuối cho userscript). Tool **chưa** được chạy với checkpoint SAM3
> thật và **chưa** được chạy trên `cvat.edufun.ai` thật, vì hai thứ đó cần GPU và tài khoản của bạn.
> Hãy làm mục [Kiểm tra lần đầu](#6-kiểm-tra-lần-đầu) trên một job thử trước khi dùng cho bài thật.

---

## 1. Tool hoạt động thế nào

```
 Trình duyệt của bạn                                   Máy có GPU (máy bạn hoặc Colab)
┌───────────────────────────────────────────┐         ┌──────────────────────────────┐
│ cvat.edufun.ai (giao diện CVAT gốc)       │         │ backend (Python, FastAPI)    │
│  ┌─────────────────────────────────────┐  │  ảnh +  │  SAM3 ── prompt theo label   │
│  │ Userscript (Tampermonkey)           │──┼─ label ─▶  luật guideline (profile)   │
│  │  panel + lớp xem trước trên canvas  │◀─┼─ đề xuất ─ hình học CVAT, cờ, QA      │
│  └──────────────┬──────────────────────┘  │         └──────────────────────────────┘
│                 │ REST API của CVAT        │
│                 ▼ (phiên đăng nhập sẵn có) │
│  đọc label, ảnh frame; ghi shape vào job   │
└───────────────────────────────────────────┘
```

1. Userscript đọc job và frame đang mở, lấy ảnh frame và danh sách label qua REST API của CVAT,
   dùng chính phiên đăng nhập của bạn.
2. Ảnh được gửi tới backend do bạn chạy. Backend chạy SAM3 với prompt của từng label, rồi áp luật
   của guideline (rider/pedestrian, truncated, occluded, tách làn, không chồng lấn...).
3. Đề xuất được vẽ đè lên canvas để bạn xem. Mỗi đề xuất có ô tick, ô đổi class, và cờ nếu tool
   không chắc (đúng theo bảng Issue type của guideline: `UNCERTAIN_CLASS`, `UNCERTAIN_BOUNDARY`,
   `UNCERTAIN_SCOPE`, `ATTRIBUTE_CHECK`, `LOW_VISIBILITY`).
4. Bấm **Ghi vào CVAT**: các đề xuất đã tick được tạo thành shape trong job bằng
   `PATCH /api/jobs/{id}/annotations?action=create`, rồi trang tự tải lại để hiện shape.
   Từ đó chúng là shape CVAT bình thường: kéo, sửa đỉnh, đổi label, xoá như shape vẽ tay.

Vài điều cần biết trước:

- **Tool chỉ gợi ý.** Chất lượng cuối cùng là trách nhiệm của người annotate. Guideline yêu cầu rà
  02 lần và đưa case không chắc cho mentor; tool giúp việc đó (tab Kiểm tra, nút Tạo Issue) chứ
  không thay được.
- **Nhãn do tool ghi mang `source = "semi-auto"`**, giống các công cụ AI có sẵn của CVAT. Trường
  này hiển thị trong CVAT và trong file export. Tool không có tuỳ chọn đổi nó thành `manual`.
- **Hỏi mentor trước khi dùng cho bài chấm điểm.** Hai guideline không cấm công cụ hỗ trợ, nhưng đây
  là bài của học viên; hãy xác nhận lớp có cho phép gợi ý bằng model hay không.
- Ảnh chỉ đi tới backend bạn cấu hình. Nếu chạy backend trên Colab, ảnh sẽ rời máy bạn.

### Vì sao chọn Tampermonkey

| Cách | Cài đặt | Ghi chú |
| --- | --- | --- |
| **Userscript (Tampermonkey)** - cách tool này dùng | Cài 1 extension, mở 1 link | Gọi được backend `http://127.0.0.1` từ trang `https` mà không vướng CORS; cập nhật = cài lại 1 file |
| Extension Chrome riêng | Phải bật Developer mode, nạp thư mục | Làm được như userscript nhưng khó phân phối hơn |
| Bookmarklet | Không cài gì | Bị chính sách bảo mật của trang chặn gọi backend ngoài |
| Nuclio / AI Tools của CVAT | Cần quyền admin máy chủ | Không áp dụng được với `cvat.edufun.ai` |

---

## 2. Yêu cầu

- Trình duyệt Chrome, Edge hoặc Firefox, và extension **Tampermonkey**.
- Tài khoản `cvat.edufun.ai` có quyền sửa annotation của job.
- Một máy chạy backend, chọn một trong hai:
  - Máy của bạn có GPU NVIDIA (nên từ 8 GB VRAM), Python 3.12 nếu dùng gói `sam3` chính thức,
    hoặc Python 3.10 trở lên nếu dùng qua `transformers`.
  - Google Colab có GPU (xem [mục 5](#5-không-có-gpu-chạy-backend-trên-google-colab)).
- Tài khoản Hugging Face đã được duyệt quyền tải `facebook/sam3` (model yêu cầu xin quyền).

Chưa có GPU vẫn thử được giao diện: backend có chế độ `mock` trả kết quả giả.

---

## 3. Cài đặt

### Bước 1 - Lấy repo và cài phần chung

Giải nén `sam3-cvat-assist.zip` (hoặc `git clone` repo của bạn), mở terminal trong thư mục đó.

**Windows (PowerShell):**

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1
```

**Linux / macOS / WSL:**

```bash
bash scripts/setup.sh
```

Script tạo môi trường ảo `.venv`, cài thư viện trong `requirements.txt` và tạo file `.env`.

Kiểm tra nhanh, chưa cần model:

```powershell
$env:SAM3_ENGINE="mock"; .\scripts\run.ps1        # Windows
```
```bash
SAM3_ENGINE=mock bash scripts/run.sh               # Linux / macOS
```

Mở <http://127.0.0.1:8765> thấy trang "Backend đang chạy" là được. Dừng bằng `Ctrl+C`.

### Bước 2 - Cài SAM3

Có hai cách nạp SAM3. Chọn một; backend tự nhận cách nào đã cài (`SAM3_ENGINE=auto`).

**Cách A - gói `sam3` chính thức của Meta** (Python 3.12, GPU NVIDIA):

```bash
bash scripts/setup.sh sam3          # Windows: .\scripts\setup.ps1 sam3
```

**Cách B - qua Hugging Face `transformers`** (dễ cài hơn, nên thử trước trên Windows):

```bash
bash scripts/setup.sh transformers  # Windows: .\scripts\setup.ps1 transformers
```

Cả hai cách cài PyTorch bản CUDA 12.6. Máy bạn dùng CUDA khác thì đặt biến `TORCH_INDEX` trước khi
chạy script, theo lệnh cài trong README của SAM3: <https://github.com/facebookresearch/sam3>.

Sau đó xin quyền và đăng nhập Hugging Face (làm một lần):

1. Mở <https://huggingface.co/facebook/sam3>, đăng nhập, đồng ý điều khoản, đợi được duyệt.
2. Tạo access token quyền *Read* tại <https://huggingface.co/settings/tokens>.
3. Trong terminal đã kích hoạt `.venv`: chạy `hf auth login` và dán token.

### Bước 3 - Chạy backend

```powershell
.\scripts\run.ps1                   # Windows
```
```bash
bash scripts/run.sh                  # Linux / macOS
```

Lần đầu backend tải checkpoint nên mất vài phút. Khi log in ra
`Sẵn sàng. Engine: sam3, thiết bị: cuda` là xong. Giữ cửa sổ này mở suốt lúc làm việc.

Tuỳ chọn trong file `.env`: cổng, thiết bị, API key... (xem chú thích trong `.env.example`).

### Bước 4 - Cài Tampermonkey và userscript

1. Cài Tampermonkey từ kho extension của trình duyệt: <https://www.tampermonkey.net>.
2. **Chrome / Edge:** mở `chrome://extensions`, bấm *Details* của Tampermonkey và bật
   **Allow User Scripts** (bản cũ hơn: bật **Developer mode** ở góc phải). Thiếu bước này script
   sẽ không chạy.
3. Khi backend đang chạy, mở <http://127.0.0.1:8765/sam3-cvat-assist.user.js>. Tampermonkey hiện
   trang cài đặt; bấm **Install**.
   Cách khác: mở Tampermonkey > *Create a new script*, dán toàn bộ nội dung file
   `userscript/sam3-cvat-assist.user.js`, lưu lại.
4. Lần đầu script gọi backend, Tampermonkey hỏi quyền truy cập địa chỉ đó. Chọn **Always allow**.

CVAT của bạn ở tên miền khác? Sửa dòng `// @match https://cvat.edufun.ai/*` ở đầu userscript.

### Bước 5 - Mở job và kết nối

1. Mở một job trên `cvat.edufun.ai` (địa chỉ có dạng `/tasks/<id>/jobs/<id>`). Panel
   **SAM3 Assist** hiện ở góc phải.
2. Chấm tròn cạnh tiêu đề: xanh = đã nối backend SAM3, cam = backend đang ở chế độ mock,
   đỏ = chưa nối được.
3. Nếu đỏ: vào tab **Cài đặt**, kiểm tra *Địa chỉ backend* (mặc định `http://127.0.0.1:8765`),
   nhập *API key* nếu bạn có đặt, bấm **Kiểm tra kết nối**.

---

## 4. Cách dùng

### Mỗi frame

1. Bấm **Gợi ý nhãn cho frame này** (hoặc `Alt+G`). Sau vài giây các đề xuất hiện trên ảnh và
   trong danh sách.
2. Duyệt danh sách. Rê chuột vào một dòng để làm nổi đề xuất đó trên ảnh.
   - Bỏ tick đề xuất sai.
   - Đổi class bằng ô chọn label trên dòng.
   - Dòng có cờ là chỗ tool không chắc: đọc lý do ngay dưới dòng. Bấm **Tạo Issue** để gửi case
     cho mentor qua Issue của CVAT, nội dung điền sẵn theo mẫu `[UNCERTAIN_CLASS] ...`.
   - Đề xuất trùng với annotation đã có trên frame được bỏ tick sẵn.
3. Sót đối tượng? Chọn label ở ô cạnh nút **Vẽ hộp**, bấm **Vẽ hộp** rồi kéo một hộp quanh đối
   tượng; SAM3 lấy đúng đối tượng đó. **Tìm tương tự** dùng hộp làm mẫu để tìm mọi đối tượng giống vậy.
4. Bấm **Ghi N nhãn vào CVAT** (hoặc `Alt+A`). Tool bấm Save của CVAT để lưu việc bạn đang làm
   dở, ghi các shape, rồi tải lại trang ở đúng frame.
5. Sửa tay những gì cần sửa bằng công cụ của CVAT, rồi `Ctrl+S`.
6. Vào tab **Kiểm tra**, bấm **Kiểm tra annotation của frame này** để đối chiếu với checklist.

Ghi nhầm? Nút **Hoàn tác lần ghi gần nhất** xoá đúng các shape của lần ghi cuối.

### Tab Label

Danh sách label lấy từ task CVAT. Mỗi label có: bật/tắt, loại shape, prompt, ngưỡng tin cậy.

- **Prompt** là cụm danh từ tiếng Anh ngắn và cụ thể ("traffic sign", "white lane line"). Nhiều
  prompt cách nhau bằng dấu phẩy. Ô trống với chữ mờ "suy ra bằng luật" nghĩa là label đó không có
  prompt riêng (ví dụ `rider` được tách ra từ `person`).
- **Ngưỡng**: ra thừa thì tăng, sót thì giảm.
- Thay đổi được nhớ theo tên label trong trình duyệt. **Về mặc định của profile** xoá mọi thay đổi.

### Tab Kiểm tra

Chạy trên annotation **đã lưu** của frame. Kiểm tra được bằng máy: sai loại shape so với taxonomy,
box vượt biên hoặc quá nhỏ, trùng lặp, polygon tự cắt, `truncated` không khớp với việc chạm biên
ảnh; riêng semantic segmentation: pixel bị hai class chiếm và pixel chưa gán. Các mục cần mắt người
(bỏ sót đối tượng, đã tạo Issue, rà 02 lần) hiện là "Tự rà".

### Tab Cài đặt

Địa chỉ backend, API key, tự gợi ý khi chuyển frame, tải lại trang sau khi ghi, hỏi lại trước khi
ghi, độ đậm vùng tô. Ẩn/hiện panel: menu của Tampermonkey > *SAM3 Assist: hiện/ẩn panel*.

---

## 5. Không có GPU: chạy backend trên Google Colab

1. Mở <https://colab.research.google.com>, *File > Upload notebook*, chọn
   `colab/sam3_backend_colab.ipynb`.
2. *Runtime > Change runtime type > GPU*.
3. Chạy lần lượt các ô: tải `sam3-cvat-assist.zip` lên, cài thư viện, đăng nhập Hugging Face,
   chạy backend. Ô cuối in ra một **địa chỉ https** (`...trycloudflare.com`) và một **API key**.
4. Trên CVAT, tab **Cài đặt** của panel: dán địa chỉ và API key, bấm **Kiểm tra kết nối**.

Địa chỉ đổi mỗi lần chạy lại notebook. API key là bắt buộc ở chế độ này vì địa chỉ là công khai.

---

## 6. Kiểm tra lần đầu

Làm trên một job thử, theo đúng thứ tự. Mỗi bước kiểm tra một giả định về CVAT 2.76.1 mà tôi không
thể tự xác nhận từ bên ngoài.

| # | Việc làm | Kết quả đúng | Nếu sai |
| --- | --- | --- | --- |
| 1 | Mở job | Panel hiện; dòng trên cùng ghi đúng `Job … · Frame …` | Xem "Panel không hiện" bên dưới |
| 2 | Chuyển frame bằng thanh player | Số frame trên panel đổi theo | Xem "Số frame không đổi" |
| 3 | Tab Label | Đủ label của task, đúng profile | Chọn profile bằng tay ở ô chọn profile |
| 4 | Gợi ý nhãn | Đề xuất vẽ **khớp vị trí** trên ảnh, kể cả khi zoom và kéo ảnh | Xem "Phần xem trước lệch hoặc không hiện" |
| 5 | Tick 1 đề xuất, Ghi vào CVAT | Trang tải lại, shape xuất hiện đúng chỗ, sửa được | Đọc dòng lỗi đỏ ở cuối panel; xem "Lỗi khi ghi" |
| 6 | Hoàn tác | Shape vừa ghi biến mất | Xoá tay trong CVAT |

---

## 7. Guideline được chuyển thành luật thế nào

### BBox / Polygon / Polyline

| Guideline | Tool làm gì |
| --- | --- |
| BBox bao sát theo 4 điểm cực | Box lấy từ điểm cực của mask SAM3, sau khi bỏ mảnh vụn; cắt vào trong biên ảnh |
| Mỗi object một BBox, box được phép chồng nhau | Mỗi đối tượng SAM3 tìm được là một đề xuất riêng |
| Không bỏ sót, không trùng lặp | Hai mask trùng nhau chỉ giữ một; trùng với shape đã có thì gắn cờ `Trùng lặp` và bỏ tick |
| rider = người ngồi trên xe hai bánh | Người có phần dưới chồng lên motorcycle/bicycle thành `rider`, còn lại là `pedestrian`; mức chồng lưng chừng thì gắn `UNCERTAIN_CLASS` |
| car / truck / bus theo loại xe | Mỗi class một prompt; cùng vị trí thì giữ class điểm cao hơn, hai điểm sát nhau thì gắn `UNCERTAIN_CLASS` |
| `truncated = true` khi bị cắt bởi biên ảnh | Box chạm biên (trong 2px) thì đặt attribute `truncated` |
| `occluded = true` khi bị object khác che | Ước lượng: bị một object ở gần camera hơn đè lên; luôn kèm cờ `ATTRIBUTE_CHECK` |
| Object quá bé, khó quan sát | Cạnh ngắn dưới 12px hoặc điểm sát ngưỡng thì gắn `LOW_VISIBILITY` |
| Polygon: bám biên, ít điểm ở đoạn thẳng, nhiều điểm ở đoạn cong, không tự cắt | Giản lược đường bao với sai số 1.5-2px; có kiểm tra tự cắt |
| area/direct, area/alternative | Lấy mặt đường, trừ xe và người, cắt theo vạch làn. Mảnh chứa vị trí xe là `direct`; các mảnh khác là `alternative` |
| Không vẽ đường ngược chiều là alternative | Mảnh nằm bên kia vạch vàng bị loại; không thấy vạch phân chiều thì gắn `UNCERTAIN_SCOPE` và bỏ tick |
| Polyline đi theo tim vạch, không nối tắt qua vùng không có vạch | Lấy điểm giữa của vạch theo từng lát cắt; không nối qua khoảng trống trên 30px trong cùng một mask |
| Giữ hướng vẽ nhất quán | Vạch dọc vẽ từ gần xe tới xa; vạch ngang vẽ từ trái sang phải |
| single/double, white/yellow/other | Prompt riêng cho từng loại, rồi đo màu pixel thật; lệch thì đổi class và gắn `UNCERTAIN_CLASS` |

### Semantic Segmentation

| Guideline | Tool làm gì |
| --- | --- |
| Hai class không cùng chiếm một pixel | Ghép mask theo thứ tự ưu tiên (vật ở gần đè lên nền); mỗi pixel đúng một class |
| Không để sót pixel giữa các vùng đã rõ | Lỗ nhỏ (tới 150px) được nhập vào class gần nhất |
| Không tự lấp vùng chưa rõ bằng class suy đoán | Vùng trống lớn hơn để nguyên; panel báo phần trăm pixel chưa gán |
| Person vs Rider | Cùng luật như trên |
| Cột đèn và đèn giao thông vẽ riêng | Prompt riêng cho `pole` và `traffic light`; đèn được ưu tiên hơn cột |
| Không tô phần bị che khuất | Chỉ dùng phần nhìn thấy trong mask của SAM3 |
| Frame đã có annotation | Chỉ đề xuất cho pixel chưa được gán |

Mọi con số trên nằm trong file YAML của profile và sửa được, xem `docs/PROFILES.md`.

---

## 8. Chuẩn bị task CVAT

Label do người tạo task định nghĩa; tool chỉ đọc. Để tool khớp được guideline:

- **Tên label** giống trong guideline. Không phân biệt hoa/thường; `_`, `-` và khoảng trắng coi như
  nhau (`traffic_light` = `traffic light`).
- **Kiểu label**: `any` là linh hoạt nhất. Nếu label bị khoá kiểu (`rectangle`, `polygon`,
  `polyline`, `mask`), tool ghi theo kiểu đó và báo nếu nó khác guideline.
- **Attribute `truncated`**: muốn tool gán được, các label object cần một attribute kiểu
  *checkbox* tên `truncated`. `occluded` là thuộc tính có sẵn của mọi shape trong CVAT, không cần tạo.
- **Semantic segmentation**: label kiểu `mask` hoặc `any` cho kết quả chính xác từng pixel. Label kiểu
  `polygon` cũng chạy, nhưng biên giữa hai vùng kề nhau có thể lệch khoảng 1px.

---

## 9. Thêm label hoặc guideline mới

- **Thêm vài label vào task:** không cần làm gì. Label mới hiện trong tab Label ở trạng thái tắt
  (nếu đang dùng profile của một guideline) với prompt là tên label; bật lên và sửa prompt.
- **Guideline mới:** chép `profiles/_template.yaml` thành file mới, điền label, prompt, shape và
  luật, rồi bấm **Nạp lại profile** ở tab Label. Hướng dẫn từng trường: `docs/PROFILES.md`.
- **Luật mới chưa có sẵn** (ví dụ một attribute suy ra từ hình dạng): thêm hàm trong
  `backend/pipeline.py` và đọc tham số từ mục `rules` của profile.

---

## 10. Giới hạn đã biết

- **Prompt quyết định chất lượng.** SAM3 mạnh với danh từ cụ thể, yếu với khái niệm trừu tượng.
  Các prompt mặc định là điểm khởi đầu; hãy chỉnh ở tab Label sau khi thử trên dữ liệu thật.
- **area/direct và area/alternative** là suy luận hình học từ mặt đường và vạch kẻ. Ở ngã tư, đường
  cong gắt, hoặc khi không thấy vạch, kết quả cần sửa tay; tool gắn cờ các trường hợp đó.
- **Vạch đơn hay đôi** dựa vào prompt của SAM3, chỉ có màu là được đo lại từ pixel.
- **occluded** chỉ là ước lượng. Quy tắc "che khuất quá 80%" trong guideline cần người đánh giá.
- **Phản chiếu, bóng đổ, hình trên billboard**: tool không tự nhận ra. Bỏ tick các đề xuất này.
- **Chỉ làm việc với shape trên từng frame.** Chưa hỗ trợ track, skeleton, cuboid, ellipse, tag.
- **Ảnh bị xoay trong CVAT** làm lớp xem trước lệch. Trả ảnh về góc 0 trước khi dùng.
- **Hai điểm cần hỏi mentor**, vì guideline không nói rõ:
  1. Vạch nét đứt: vẽ một polyline liền qua các nét, hay mỗi nét một polyline? Mặc định tool nối
     các nét thẳng hàng cách nhau tới 60px (`join_dashes: true`). Đổi trong profile.
  2. `lane/crosswalk`: vẽ một đường giữa hay hai mép? Mặc định là đường giữa
     (`polyline_mode: centerline`); đổi thành `long_edges` để vẽ hai mép dài.

---

## 11. Xử lý sự cố

**Panel không hiện.** Kiểm tra theo thứ tự: biểu tượng Tampermonkey có hiện số 1 trên trang CVAT
không (script có chạy); đã bật *Allow User Scripts* / *Developer mode* chưa; địa chỉ trang có đúng
dạng `/tasks/<id>/jobs/<id>`; panel có bị ẩn không (menu Tampermonkey > hiện/ẩn panel).

**Chấm đỏ, "Không kết nối được backend".** Backend chưa chạy hoặc sai địa chỉ. Mở địa chỉ backend
trong tab mới để thử. Tampermonkey có thể đã chặn tên miền: mở trang cấu hình script > *Settings* >
*XHR Security*, thêm tên miền vào danh sách cho phép.

**"Backend trả lỗi 401".** API key trên panel khác `SAM3_API_KEY` của backend.

**Backend báo "Không nạp được SAM3".** Dòng lỗi liệt kê lý do của từng cách nạp. Thường gặp: chưa
được duyệt quyền `facebook/sam3` hoặc chưa `hf auth login`; bản PyTorch không khớp CUDA; thiếu
bộ nhớ GPU. Nếu chữ ký hàm của gói `sam3` đã đổi so với lúc viết tool, chỗ cần sửa là
`backend/engines/sam3_official.py` (khoảng 100 dòng, bám theo README của SAM3).

**"CVAT trả lỗi 401/403".** Phiên đăng nhập hết hạn (đăng nhập lại), hoặc tài khoản không có quyền
sửa job này. Tạo Issue cũng có thể bị từ chối tuỳ vai trò của bạn trong job.

**"CVAT trả lỗi 400" khi ghi.** Thông báo đi kèm nói trường nào bị từ chối. Thường do loại shape
không hợp với kiểu label; đổi shape ở tab Label.

**Số frame không đổi khi chuyển frame.** Tool đọc số frame từ ô nhập trên thanh player
(`.cvat-player-frame-selector input`), rồi tới tham số `?frame=` trên địa chỉ. Nếu bản CVAT này đổi
tên class đó, sửa hàm `readCtx` trong userscript.

**Phần xem trước lệch hoặc không hiện.** Lớp xem trước bám theo phần tử ảnh nền của canvas CVAT
(`#cvat_canvas_background`). Nếu phần tử có tên khác: bấm F12, chọn phần tử chứa ảnh, rồi nhập CSS
selector của nó vào tab Cài đặt. Danh sách đề xuất và việc ghi vào CVAT không phụ thuộc lớp này.

**Ghi xong không thấy shape.** Tải lại trang (F5). Giao diện CVAT giữ annotation trong bộ nhớ nên
chỉ thấy shape mới sau khi tải lại; tuỳ chọn tải lại tự động nằm ở tab Cài đặt.

**Chậm.** Mỗi prompt là một lần chạy model. Tắt các label không cần ở tab Label; dùng GPU.

---

## 12. Chạy test

```bash
pip install -r requirements-dev.txt
python -m pytest -q                      # hình học, luật guideline, QA, HTTP API (engine giả lập)

npm install jsdom                        # test đầu-cuối cho userscript với CVAT giả
node tests/userscript_smoke.cjs
```

---

## 13. Cấu trúc repo

```
sam3-cvat-assist/
├── userscript/sam3-cvat-assist.user.js   Panel + lớp xem trước + gọi API CVAT (cài vào Tampermonkey)
├── backend/
│   ├── app.py                            HTTP API: /health /resolve /propose /interactive /qa
│   ├── pipeline.py                       Luật guideline: từ mask SAM3 tới đề xuất
│   ├── geometry.py                       Mask -> bbox / polygon / polyline / mask RLE của CVAT
│   ├── qa.py                             Kiểm tra annotation theo checklist
│   ├── profiles.py                       Đọc profile, ghép với label của task
│   ├── config.py                         Cấu hình từ .env
│   └── engines/                          sam3_official.py, sam3_hf.py, mock.py
├── profiles/                             Mỗi guideline một file YAML (+ generic, _template)
├── scripts/                              setup và run cho Windows / Linux
├── colab/sam3_backend_colab.ipynb        Chạy backend trên Colab
├── docs/PROFILES.md                      Cách viết profile
└── tests/                                pytest + test đầu-cuối userscript
```

SAM 3 do Meta phát hành với giấy phép riêng; đọc giấy phép tại repo của SAM3 trước khi dùng cho mục
đích ngoài học tập.
