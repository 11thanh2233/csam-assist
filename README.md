# SAM3 CVAT Assist

Công cụ gợi ý nhãn bằng SAM 3 (Segment Anything Model 3 của Meta) chạy ngay trên giao diện
`cvat.edufun.ai`. Tool phủ một panel lên trang CVAT, đề xuất bounding box, polygon, polyline
hoặc mask theo guideline; bạn duyệt từng đề xuất rồi mới ghi vào job. Không cần import/export file,
không cần quyền admin trên máy chủ CVAT.

Đã viết sẵn luật cho năm guideline:

| Guideline | Profile | Kết quả |
| --- | --- | --- |
| BBox_Polygon_Polyline_Annotation_Guideline_V1 | `bdd_bbox_polygon_polyline_v1` | 10 class object (rectangle), drivable area (polygon), lane marking (polyline) |
| Semantic_Segmentation_Annotation_Guideline_V1 | `semseg_cityscapes19_v1` | 19 class, mỗi pixel một class (mask hoặc polygon) |
| HumanPose17_Annotation_Guideline_V1 + Face_Landmark50_Annotation_Guideline_V1 | `driver_pose17_face50_v1` | Skeleton `person` 17 điểm của người lái (R/L theo phía của ảnh); 7 skeleton mặt, 50 điểm; trạng thái Visible / Occluded / Outside |
| Guideline tracking Lab 5 | `mot_person_track_v1` | Rectangle track qua cả video: ID, box toàn thân, occluded, outside, keyframe |

Task có bộ label khác vẫn dùng được ngay qua profile `generic` (prompt = tên label) và
`generic_track` cho theo dõi. Quy ước skeleton cũ của Lab 4 (person 17 điểm tên COCO, face 13 điểm, trái/phải theo người)
đã được cất vào `profiles/archive/` và `guideline_library/archive/`; xem [Khôi phục quy ước Lab 4](#khôi-phục-quy-ước-skeleton-của-lab-4).

> **Trạng thái kiểm thử.** Toàn bộ luồng đã chạy qua test tự động với model giả lập và một CVAT giả
> (122 test backend/desktop + 5 test đầu-cuối cho userscript và bản desktop, và trang duyệt guideline đã chạy trong trình duyệt thật). Phần ảnh đã được bạn chạy với SAM3 thật trên
> `cvat.edufun.ai`. Phần skeleton (ViTPose, MediaPipe), tracking và soạn guideline **chưa** chạy với
> model thật hay trên máy chủ thật; phần gọi LLM chỉ mới chạy với LLM giả trong test. Riêng skeleton theo hai
> guideline mới: vị trí 50 điểm mặt mới được kiểm trên mô hình mặt chuẩn của MediaPipe (mặt nhìn thẳng), chưa
> kiểm trên ảnh cabin thật; tên label người trong task thật là `person_vf17` (đã nhận được từ bản 0.3.3), còn tên
> sublabel tôi chưa thấy (tool giả định `1`–`17` và `0`–`49`, và nhận cả các cách đặt tên khác). Việc thêm label vào
> task (nhất là label skeleton) mới chạy với CVAT giả: thử trên một task thử trước. Hãy làm mục [Kiểm tra lần đầu](#6-kiểm-tra-lần-đầu) trên một job thử trước.

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
- **Trường `source` của nhãn:** mặc định tool không gửi trường này, nên CVAT tự ghi giá trị
  mặc định của nó là `manual`. Muốn đánh dấu nhãn do tool tạo là `semi-auto` (giống các công cụ AI
  có sẵn của CVAT), bật ô tương ứng ở tab Cài đặt.
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

### Cách nhanh trên Windows: bộ cài `.exe` (từ bản 0.6.0)

Tải `SAM3-CVAT-Assist-Setup-<bản>.exe` ở mục **Releases** của repo (từ 0.7.0 app tự báo và tự cập nhật bản mới), chạy, làm theo trình tải ở bước cuối
(giấy phép SAM, token Hugging Face, chọn thành phần). Xong là mở **SAM3 CVAT Assist** từ Desktop: bảng điều khiển
mở CVAT trong cửa sổ riêng, bảng của tool tự hiện. Không cần Python, PowerShell hay Tampermonkey. Chi tiết, dung
lượng, cách gỡ và các biện pháp an toàn: [docs/DESKTOP.md](docs/DESKTOP.md).

Bản 0.8.0 thêm phím tắt (Alt+Enter: ghi và sang frame tiếp), chạy sẵn frame tiếp theo, dùng chung GPU trong nhóm,
model riêng của dự án và nhiều tiện ích khác: xem [docs/CHANGELOG.md](docs/CHANGELOG.md) và
[docs/CHECKLIST_0.8.0.md](docs/CHECKLIST_0.8.0.md).

Bản 0.9.0 làm lại bảng điều khiển (sáu trang, báo lỗi backend kèm cách sửa, xem nhật ký trong app, Ctrl+K) và cho
backend tự bật lại khi tắt bất ngờ: xem [docs/CHECKLIST_0.9.0.md](docs/CHECKLIST_0.9.0.md).

Các bước dưới đây là cách cài thủ công (Linux, macOS, Colab, hoặc khi muốn dùng Tampermonkey).

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

Job skeleton cần thêm MediaPipe cho phần khuôn mặt (phần người dùng ViTPose qua `transformers`,
không phải cài thêm):

```powershell
.\scripts\setup.ps1 skeleton     # Windows
```
```bash
bash scripts/setup.sh skeleton    # Linux / macOS
```

Lần đầu mở job skeleton, backend tự tải hai model nhỏ: ViTPose từ Hugging Face (không cần xin quyền)
và model khuôn mặt của MediaPipe vào thư mục `models/`.

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

#### Chạy trên CVAT khác (CVAT local, CVAT của trường hay công ty)

Userscript đã bật sẵn cho:

| CVAT | Địa chỉ |
| --- | --- |
| CVAT của khoá học | `https://cvat.edufun.ai` |
| CVAT online chính thức | `https://app.cvat.ai` |
| CVAT chạy trên máy bạn, mọi cổng | `http://localhost:8080`, `http://127.0.0.1:8080`… |
| CVAT trong mạng nội bộ, mọi cổng | `http://192.168.x.x`, `http://10.x.x.x`, `http://172.16–31.x.x` |

Trên localhost và mạng nội bộ có thể có trang khác không phải CVAT: panel chỉ hiện khi trang đúng là CVAT
và đang mở một job.

**Thêm một CVAT khác** (ví dụ `https://cvat.truong-abc.edu.vn`), chọn một trong hai cách:

- **Không mất khi cài lại script (nên dùng):** Tampermonkey > bấm vào script **SAM3 CVAT Assist** > tab
  **Settings** > mục **Includes/Excludes** > ô **User matches** > thêm `https://cvat.truong-abc.edu.vn/*`
  > **Save**.
- **Sửa trong code:** mở script trong Tampermonkey. Ở đầu file có sẵn các dòng mẫu:
  ```
  // THEM @match        https://cvat.ten-mien-thu-nhat.com/*
  ```
  Xoá chữ `THEM ` để dòng bắt đầu bằng `// @match`, thay tên miền, giữ `/*` ở cuối, rồi Ctrl+S.
  Cách này mất khi cài bản userscript mới (phải sửa lại).

Sau đó tải lại trang job. Không cần đổi gì ở backend. Tool dùng REST API của CVAT 2.x; CVAT 1.x không chạy.

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
   tượng; SAM3 lấy đúng đối tượng đó. **Tìm tương tự** dùng hộp làm mẫu để tìm mọi đối tượng giống vậy
   (vật đã gán sẵn được bỏ tick). Vật nào bị nhận nhầm, bấm **Loại trừ** ở dòng của nó: vật đó thành mẫu
   *âm*, SAM3 tìm lại và bỏ mọi vật giống nó. Loại trừ nhiều lần được.
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

**Quản lý profile** (nút cuối tab Label): danh sách mọi profile, chia hai nhóm *đang dùng* và *đang ẩn*.
- **Ẩn:** chuyển file vào `profiles/archive/`. Profile biến khỏi ô chọn và khỏi "Tự chọn profile"; bấm
  **Hiện lại** để dùng lại.
- **Xoá:** xoá hẳn file, không hoàn tác được. Profile có sẵn của tool sẽ quay lại khi giải nén bản cập nhật.
  Profile nạp từ guideline: xoá profile không gỡ văn bản và định nghĩa; muốn gỡ cả guideline, dùng nút
  **Gỡ** ở tab Guideline.
- Hai profile dự phòng `generic` và `generic_track` luôn có, không ẩn hay xoá được.
- Danh sách đọc lại thư mục mỗi lần mở, nên file bạn tự chép vào hay xoá tay cũng hiện đúng.

### Tab Kiểm tra

Chạy trên annotation **đã lưu** của frame. Kiểm tra được bằng máy: sai loại shape so với taxonomy,
box vượt biên hoặc quá nhỏ, trùng lặp, polygon tự cắt, `truncated` không khớp với việc chạm biên
ảnh; riêng semantic segmentation: pixel bị hai class chiếm và pixel chưa gán. Các mục cần mắt người
(bỏ sót đối tượng, đã tạo Issue, rà 02 lần) hiện là "Tự rà".

**Rà cả job** (cuối tab Kiểm tra): chạy các kiểm tra trên cho mọi frame của job trong một lần, cộng hai
kiểm tra trên toàn job: frame chưa có nhãn nào, và label chưa dùng lần nào.
- Tick **Tìm cả đối tượng có thể bị sót** để SAM3 chạy trên từng frame và báo chỗ nó thấy vật (tin từ 60%)
  mà chưa có nhãn. Chậm như Gợi ý cho cả job; bấm **Dừng** để dừng giữa chừng.
- Mỗi frame có lỗi hiện một dòng: bấm **Frame N** để nhảy tới. **Tạo Issue cho mọi lỗi** tạo một Issue
  trên CVAT cho mỗi lỗi và mỗi chỗ có thể sót, đặt đúng frame và vị trí, nội dung theo mẫu `[MÃ] ...`.
- Lỗi trên nhãn do chính tool tạo được ghi vào tab **Tự học** dưới dạng *giả thuyết* (tool tự thấy),
  không tự đổi gì.

### Tab Tự học

Tool học từ cách **bạn** xử lý đề xuất của nó, rồi dùng điều đã học cho các lần gợi ý sau. Model SAM3
không bị huấn luyện lại; thứ được học là **ngưỡng tin cậy** và **cách đặt hộp** của từng label.

**Tool ghi lại gì** (bật/tắt bằng ô *Ghi lại cách tôi xử lý đề xuất*):

| Bạn làm | Tool hiểu là |
| --- | --- |
| Ghi đề xuất vào CVAT, không sửa gì | đề xuất đúng |
| Ghi rồi kéo lại hộp trong CVAT | đúng, nhưng hộp lệch: ghi lại bạn kéo cạnh nào, bao nhiêu |
| Đổi class (trên panel trước khi ghi, hoặc trong CVAT sau khi ghi) | sai class; ghi lại cặp hay nhầm |
| Xoá nhãn tool đã ghi | sai |
| Bỏ tick một đề xuất rồi ghi các đề xuất khác, hoặc bấm **Loại trừ** | sai |
| Tự vẽ thêm nhãn trên frame tool đã gợi ý | tool bỏ sót |

Nhãn có sẵn trên frame từ trước khi tool gợi ý không bị tính là "bỏ sót". Mỗi lần mở job, tool tự **đối
chiếu** các nhãn nó đã ghi với nhãn hiện tại (sau khi bạn sửa và lưu), nên chỉ cần làm việc như bình
thường. Nút **Đối chiếu job này ngay** làm việc đó lập tức.

**Tool học được gì**, cho từng label của từng profile:
- **Ngưỡng tin cậy:** khi đủ 12 đề xuất đã có kết quả, tool chọn ngưỡng cho F1 cao nhất, nằm giữa đề xuất
  sai cao điểm nhất và đề xuất đúng thấp điểm nhất. Tool chỉ tự **nâng** ngưỡng theo cách đó, vì đề xuất
  dưới ngưỡng cũ bạn chưa từng thấy. Khi tool bỏ sót nhiều (tìm được dưới 70%) mà gần như không sai, nó **hạ
  thử** 0,05 rồi xem kết quả ở lần đối chiếu sau; chỉ hạ tiếp khi có thêm dữ liệu mới, không bao giờ dưới 0,2.
- **Chỉnh hộp:** khi đủ 10 hộp đúng và bạn hay kéo một cạnh theo cùng hướng, tool tự chỉnh cạnh đó cho các
  đề xuất sau (ví dụ "cạnh dưới +4% chiều cao hộp"). Hộp đã chỉnh có dòng ghi chú trên panel; nếu bạn vẫn
  phải kéo thêm, lần học sau cộng dồn phần còn lệch.
- **Gợi ý chưa tự áp dụng:** label hay bị đổi sang label khác, label bị bỏ sót nhiều, và lỗi tool tự thấy
  khi rà cả job. Những thứ này cần bạn sửa prompt hay luật, tool không tự đoán.

**Kiểm soát:**
- Bỏ tick **Dùng điều tool đã học khi gợi ý** để quay về đúng cài đặt của profile.
- Mỗi label có nút **Tắt** (giữ dữ liệu, không áp) và **Quên** (xoá dữ liệu của label đó). **Quên tất
  cả** xoá hết.
- Ngưỡng bạn đặt tay ở tab Label luôn thắng ngưỡng tool học. Ở tab Label, label có điều đã học hiện chữ
  *tự học* dưới ô ngưỡng; rê chuột để xem lý do.
- Dữ liệu nằm trong `guideline_library/learning/` của backend (`events.jsonl`: các lần ghi; `outcomes/`:
  kết quả đối chiếu theo job; `model.json`: điều đã học). Chỉ có hình học và điểm số, không có ảnh.

Phần tự học này khác **bài học của phần soạn guideline** (tab Guideline): bài học guideline nhớ các quyết
định về *luật* (bạn chốt câu hỏi nào, sửa dòng luật nào); phần tự học ở đây nhớ cách bạn *gán nhãn*.

### Tab Cả job

Hai việc chạy trên cả một khoảng frame (mặc định là cả job; đổi ở ô "Từ frame … tới …"). Bấm
**Dừng** để dừng giữa chừng; phần đã chạy được giữ lại. Kết quả được nhớ theo job, nên tải lại trang
không mất.

**Gợi ý cho cả job** (job ảnh, kể cả job skeleton): chạy gợi ý lần lượt cho mọi frame.

1. Chọn **label cần gợi ý** (mặc định là mọi label đang bật). Nút **Chỉ label chưa có trong job** chọn
   nhanh các label chưa có nhãn nào, ví dụ label vừa thêm theo guideline mới.
2. Chọn cách xử lý **frame đã có nhãn**:
   - *Bỏ qua label đã có trên frame* (mặc định): mỗi frame chỉ gợi ý các label đã chọn mà frame đó còn
     thiếu; frame đã có đủ thì bỏ qua. Nhãn cũ không bị đè.
   - *Bỏ qua frame đã có bất kỳ nhãn nào*: cách cũ, hợp với job mới gán được một phần.
   - *Không bỏ qua*: gợi ý lại tất cả; đề xuất trùng nhãn đang có bị gắn cờ và bỏ tick.
3. Bấm **Gợi ý cho N frame**. Mỗi frame mất khoảng bằng một lần bấm Gợi ý; panel hiện thời gian còn lại.
4. **Ghi N nhãn vào CVAT** ghi mọi đề xuất đã tick và **không có cờ**. Mục có cờ được giữ lại; bật
   "Ghi cả mục có cờ" nếu muốn ghi luôn.
5. Danh sách **Frame cần duyệt** là các frame còn mục có cờ. Bấm số frame để nhảy tới; tab Gợi ý hiện
   ngay đề xuất của frame đó, duyệt và ghi như bình thường.

**Theo dõi đối tượng** (job video): mỗi đối tượng thành một track với một ID qua các frame.

1. Chọn **Cách theo dõi**, rồi bấm **Theo dõi N frame**.
   - *Cách thường*: SAM3 tìm đối tượng trên từng frame, tool nối thành track theo vị trí và màu sắc.
   - *SAM3 Video (thử nghiệm)*: model SAM3 Video tự giữ ID của từng vật qua các frame bằng bộ nhớ, nên ít
     đổi ID khi vật bị che hoặc đi cắt nhau. Lần đầu phải tải thêm model video (khoảng 3,4 GB) và tốn VRAM
     hơn; tool nạp nó ở float16 và để dữ liệu video trên RAM. Không dùng được (thiếu VRAM, `transformers` cũ)
     hoặc lỗi giữa chừng thì tool tự làm lại bằng cách thường và báo lý do.
2. Tua thanh player: box của từng track (#1, #2…) hiện trên ảnh đúng như CVAT sẽ nội suy; nét đứt là
   frame đặt `occluded`. Rê chuột vào một dòng để làm nổi track đó.
3. Bỏ tick track không cần (người ngoài phạm vi bài, phát hiện nhầm), đọc cờ của từng track, rồi bấm
   **Ghi N track vào CVAT**. Sau đó sửa tiếp bằng công cụ track của CVAT như bình thường.

**Hoàn tác lần ghi gần nhất** xoá đúng các nhãn hoặc track của lần ghi cuối, kể cả lần ghi cả job.

### Skeleton (job keypoint)

Job có label kiểu skeleton thì panel tự chuyển sang profile `driver_pose17_face50_v1`, viết theo hai
guideline HumanPose-17 và Facial Landmark-50. Task chỉ có `person`, chỉ có 7 skeleton mặt, hoặc có cả
hai đều dùng được. Gợi ý, Vẽ hộp, Cả job và Kiểm tra dùng như job ảnh:

- **Người lái (`person`, 17 điểm):** SAM3 tìm người, ViTPose đặt 17 khớp, rồi tool xếp lại R/L **theo
  phía của ảnh**: điểm R (số chẵn) ở bên phải ảnh, điểm L (số lẻ) ở bên trái ảnh, kể cả khi người quay
  lưng. Tool quyết định theo từng chuỗi (tay theo hai vai, chân theo hai hông, đầu theo hai mắt) để
  vai–khuỷu–cổ tay mỗi phía liền mạch. Mỗi ảnh chỉ đề xuất **một** người; thấy nhiều người thì chọn người
  chiếm diện tích lớn nhất và gắn cờ `UNCERTAIN_SCOPE`. Chọn sai thì **Vẽ hộp** quanh đúng người lái.
- **Khuôn mặt (7 skeleton, 50 điểm):** MediaPipe đặt 478 điểm trên mặt; mỗi landmark của guideline được
  tính là một vị trí trên đường nối các điểm đó (ví dụ 1/4 chiều rộng mắt, tỉ lệ 0,18–0,36–0,50–0,66–0,84
  trên bờ môi trên). Vẽ một hộp quanh mặt là nhận đủ cả 7 skeleton, dù đang chọn label nào trong 7.
  Nhiều mặt thì lấy mặt của người lái đã chọn, không có thì lấy mặt lớn nhất, kèm cờ.
- **Trạng thái điểm.** Người: suy từ độ tin cậy của model (từ 0,6 là Visible, 0,35–0,6 là Occluded, thấp
  hơn hoặc ngoài khung hình là Outside). Mặt: SAM3 tìm vật che (`sunglasses`, `hand`, `face mask`); điểm
  nằm dưới vật che là Occluded, và nếu số điểm còn thấy dưới ngưỡng của guideline (mắt 4/8, lông mày 3/5,
  môi ngoài 6/12, môi trong 4/8, sống mũi 2/4) thì tool đặt **Outside cả skeleton**. Mọi điểm không phải
  Visible đều kèm cờ `ATTRIBUTE_CHECK`: tool chỉ đoán, người annotate quyết định.
- **Trên ảnh:** chấm xanh = điểm R, chấm đỏ = điểm L, vàng = điểm giữa (với skeleton mặt, vàng là điểm
  đầu tiên để thấy chiều đánh số); vòng rỗng = Occluded; không vẽ = Outside. Trỏ vào một đề xuất để
  hiện số của từng điểm.
- **Ghép label với task:** tên label trong task không cần trùng guideline. Tool nhận `person_vf17`,
  `Mắt trái` hay `left_eye` theo tên gần giống; tên không gợi ý gì thì nhận theo dãy số của các điểm
  (14–21 là `mattrai`) hoặc theo số điểm (17 điểm là `person`). Tab Label ghi rõ tool coi label là
  skeleton nào và vì sao. Tool không nhận ra, hoặc nhận sai: chọn tay ở ô ngay dưới tên label.
- **Ghép điểm với task:** theo tên, rồi theo số trong tên, cuối cùng theo thứ tự. Sublabel đặt là `2`,
  `right_eye` hay `2 - Right Eye (R)` đều nhận được. Tab Label báo rõ khi tool phải ghép theo thứ tự.

Tool **không** làm được: đặt khớp đúng "tâm khớp" trong sai số ±5 px (bạn phải phóng to và chỉnh), phân
biệt kính trong với kính râm một cách chắc chắn, và biết ai là người lái. Đề xuất là điểm khởi đầu để
sửa, giống pre-label của bài.

#### Khôi phục quy ước skeleton của Lab 4

Hai quy ước không bật cùng lúc được: cả hai dùng label `person` nhưng trái/phải ngược nhau, nên tool sẽ
không biết task đang theo bên nào. Vì vậy khi cả hai file profile cùng nằm trong `profiles/`, tool chỉ
nạp profile mới và ghi một dòng cảnh báo trong log. Muốn quay lại Lab 4, **đổi chỗ** hai file:

1. Chuyển `profiles/driver_pose17_face50_v1.yaml` vào `profiles/archive/`.
2. Chuyển `profiles/archive/coco_pose_face_lab_v1.yaml` ra `profiles/`.
3. Nếu cần sinh label kiểu cũ ở tab Guideline: chép khối trong
   `guideline_library/archive/lab4_skeleton_templates.yaml` vào mục `skeletons` của
   `guideline_library/knowledge.yaml`.
4. Bấm **Nạp lại profile** ở tab Label (hoặc khởi động lại backend).

#### Cập nhật từ bản cũ (0.3.x, 0.4.x)

Giải nén bản mới đè lên thư mục cũ, cài lại userscript, khởi động lại backend. Không cần cài lại SAM3,
ViTPose hay MediaPipe. Các file cũ còn sót (`profiles/coco_pose_face_lab_v1.yaml`,
`guideline_library/reference/lab4_keypoint_pose.md`) được tool tự bỏ qua; xoá chúng đi cho gọn cũng được.
File `guideline_library/lessons.jsonl` (bài học của bạn) và thư mục `runs/` được giữ nguyên.

Từ 0.5.0: những gì tool tự học nằm ở `guideline_library/learning/` (giải nén bản mới đè lên không mất). Backend
mặc định chỉ nhận yêu cầu gửi tới `localhost`/`127.0.0.1`; nếu chạy backend trên máy khác hoặc Colab, đặt
`SAM3_ALLOWED_HOSTS=*` (notebook Colab mới đã đặt sẵn, nhớ tải lại notebook).

### Tab Guideline: nạp guideline có sẵn cho task khác

Khi có guideline mới (file Word của mentor, file markdown), đưa nó vào tool ở phần đầu tab **Guideline**:

1. Bấm chọn file `.docx`, `.md` hoặc `.txt` (`.pdf` cần cài thêm `pip install pypdf`; dễ hơn là mở PDF bằng
   Word rồi lưu thành `.docx`). Có file **taxonomy riêng** (`taxonomy.json`)? Chọn cả hai file cùng lúc (giữ
   Ctrl khi bấm): tool đọc guideline trước rồi ghép danh sách label của file JSON vào.
2. Tool đọc các bảng theo khung guideline của khoá học và hiện bản xem trước:
   - **Taxonomy:** bảng có cột tên class/label/skeleton và cột CVAT shape (ô chứa nhiều class cách nhau bằng
     dấu phẩy, hoặc mỗi class một dòng trong ô, đều được);
   - **Định nghĩa:** bảng có cột Sub-category/Class và cột Khái niệm/Định nghĩa (`direct` ghép với `area/direct`);
   - **Issue type**, **Checklist**, **Tiêu chí reviewer**;
   - **Loại bài toán** (detection, semantic, tracking, keypoint), đoán từ shape và từ khoá.
3. Kiểm tra và sửa trong bảng: tên label, shape, **prompt tiếng Anh cho SAM3**, cách vẽ vạch (polyline). Tool
   lấy prompt đã chỉnh sẵn nếu tên label trùng một profile có sẵn; tên không dấu thì dùng tạm làm prompt;
   tên tiếng Việt có dấu (`xe nâng`) thì để trống và báo bạn điền (`forklift`). Label không có prompt thì tool
   không tự tìm được.
4. Bấm **Lưu vào tool**. Tool ghi:
   - `guideline_library/reference/<tên>.md`: toàn văn, làm tài liệu nền khi soạn guideline (và cho LLM);
   - `guideline_library/imported/<tên>.yaml`: label, shape, định nghĩa, Issue type, checklist. Định nghĩa
     của guideline nạp vào thắng định nghĩa mẫu cùng tên;
   - `profiles/gl_<tên>.yaml`: profile để tab Gợi ý dùng ngay. Checklist của guideline thay checklist chung
     ở tab Kiểm tra.

Danh sách **Guideline đã nạp** có các nút:
- **Dùng cho job này:** chọn profile đó cho job đang mở. "Tự chọn profile" cũng tự nhận khi label của task
  khớp; nếu một profile có sẵn khớp ngang thì profile có sẵn được giữ, và bản xem trước sẽ báo trước.
- **Thêm label còn thiếu:** thêm vào task các label guideline có mà task chưa có (chỉ thêm, không xoá, không đổi).
- **Chép JSON label:** để dán vào tab Raw khi tạo task mới.
- **Gỡ:** chuyển các file trên vào thư mục `archive/`, không xoá hẳn.

Nếu backend có cấu hình LLM, có ô tick cho LLM đề xuất prompt tiếng Anh và định nghĩa còn thiếu. Khi tick, toàn
văn guideline được gửi tới LLM đó.

Giới hạn:
- Tool đọc **bảng**, không hiểu đoạn văn tự do. Luật viết bằng lời (vẽ sát tới đâu, che bao nhiêu thì bỏ)
  được lưu làm tài liệu nền, nhưng không tự thành luật hình học. Luật đặc thù (như vạch kép vẽ hai mép) chọn
  ở cột cách vẽ vạch, hoặc sửa trong profile.
- **Guideline skeleton:** tool nạp văn bản, định nghĩa, checklist và tạo được label (nếu có mẫu skeleton cùng
  tên trong thư viện), nhưng chưa tự sinh profile gợi ý, vì cần biết từng điểm lấy từ model nào. Người lái và
  khuôn mặt đã có profile `driver_pose17_face50_v1`; loại khác cần viết profile theo `docs/PROFILES.md`.
- Guideline không có bảng taxonomy: báo "Không tìm thấy bảng taxonomy". Thêm label bằng một trong ba cách:
  chọn thêm file `taxonomy.json`; bấm **Lấy label từ task đang mở** (khi task đã có đủ label); hoặc
  **Thêm label** từng dòng.

**File taxonomy (.json) tool đọc được:**

| Dạng | Ví dụ |
| --- | --- |
| JSON label của CVAT (tab *Raw* ở phần label của task/project) | `[{"name": "car", "type": "rectangle", "attributes": [...]}, ...]` |
| Kết quả `/api/labels` của CVAT | `{"results": [...]}` (điểm của skeleton có `parent_id`) |
| COCO | `{"categories": [{"name": "person", "keypoints": [...]}]}` |
| Nhóm theo shape | `{"groups": [{"shape": "bbox", "classes": ["car", {"name": "xe nâng", "prompt": "forklift"}]}]}` |
| Tên → shape | `{"car": "rectangle", "road": "mask"}` |
| Danh sách tên | `["car", "bus"]` (shape mặc định rectangle) |

Với JSON label của CVAT, tool giữ nguyên màu, attribute và các điểm skeleton, nên **Thêm label còn thiếu**
tạo label giống hệt file. Trường `prompt`/`prompts` và `definition`/`description` (nếu có) được dùng luôn.

### Tab Guideline: soạn guideline nháp từ ảnh của job

Dùng khi bạn có một bộ ảnh đã upload thành task trên CVAT và cần một bản guideline cho nó. Tool không
cần file upload riêng: nó đọc ảnh thẳng từ job đang mở.

1. Nhập **Mục đích của bộ dữ liệu** (model sẽ dùng nhãn để làm gì). Đây là thứ duy nhất không có trong
   ảnh, và nó quyết định nhiều luật.
2. Bấm **Quét N ảnh và soạn nháp**. Tool lấy mẫu rải đều cả job, cho SAM3 đo: có những loại vật gì,
   to nhỏ ra sao, bị che hoặc bị cắt nhiều không, hai class nào hay bị nhận lẫn nhau, ca khó nằm ở frame nào.
3. Trả lời **Câu hỏi cần chốt**. Mỗi câu kèm số liệu, lý do của lựa chọn gợi ý, và nút `frame N` để
   nhảy tới ảnh ví dụ (vùng liên quan được khoanh trên ảnh). Câu chưa chốt thì bản nháp tạm dùng gợi ý
   và ghi rõ *(đề xuất, chưa chốt)*.
4. Xem **Lỗ hổng tool tự phát hiện**: class chưa có định nghĩa, cặp dễ nhầm chưa có quy tắc, vật xuất
   hiện nhiều nhưng chưa nói trong hay ngoài phạm vi. Đây là giả thuyết để bạn xem, không phải kết luận.
5. **Mở bản nháp để sửa và duyệt**: một trang riêng có ô sửa Markdown, bản xem trước, ảnh các ca khó và
   danh sách bài học. Bấm **Duyệt và lưu** khi xong.
6. **Label CVAT theo guideline**: tool sinh bộ label ứng với taxonomy của bản nháp, cho cả ba loại task:

   | Loại task | Label sinh ra |
   | --- | --- |
   | Ảnh (detection, semantic) | Mỗi class một label, kiểu rectangle / polygon / polyline / mask theo hình dạng đo được và câu bạn chốt; màu theo color map của guideline mẫu; attribute `truncated` nếu bạn chọn dùng |
   | Keypoint / skeleton | Mỗi bộ skeleton một câu hỏi: `person` 17 điểm tên `1`–`17` với 18 cạnh (HumanPose-17); 7 skeleton mặt với Point ID chạy liên tục `0`–`49`, vòng kín cho mắt và môi (Facial Landmark-50). Mẫu nằm trong `guideline_library/knowledge.yaml`, thêm bộ khác được |
   | Tracking video | Mỗi loại đối tượng một label rectangle; `occluded` và `outside` là thuộc tính có sẵn của track nên không cần tạo |

   - **Thêm N label còn thiếu vào task** gọi API của CVAT để thêm label ngay. Tool chỉ thêm; nó không xoá,
     không đổi tên, không đổi kiểu label đang có, vì những việc đó làm mất annotation. Cần quyền sửa task;
     nếu tài khoản không có quyền, dùng nút dưới.
   - **Chép JSON label** chép cả bộ label để dán vào tab *Raw* ở phần label khi tạo task mới, hoặc gửi cho
     người tạo task.
   - Muốn tool đề xuất skeleton cho một task chưa có label skeleton: chốt câu hỏi "bài toán" là *Keypoint*.
7. **Dùng làm profile**: tool gợi ý nhãn dùng ngay các luật vừa chốt (loại shape, cách vẽ vạch, gộp class).
   Với job skeleton, hai câu "trái/phải tính theo đâu" và "gán những ai" quyết định profile: giống profile
   có sẵn thì dùng luôn, khác thì tool ghi một bản sao đã chỉnh (`auto_<mã lượt>.yaml`).
   Từ đây sang tab **Cả job** để gán nhãn cho mọi frame, hoặc theo dõi nếu là video. Mỗi job chạy riêng;
   task có nhiều job thì mở từng job.

**Tool học thế nào.** Tool không huấn luyện lại model nào (SAM3, ViTPose, MediaPipe giữ nguyên). Thứ
được "học" là các **quyết định của người**, ghi thành từng dòng văn bản đọc được, rồi dùng lại khi soạn
bản nháp kế tiếp:

| Tín hiệu từ người | Ví dụ | Tool ghi gì | Lần soạn sau |
| --- | --- | --- | --- |
| Câu hỏi bạn chốt (ghi lúc bấm **Duyệt và lưu**) | "Trái/phải tính theo: phía của ảnh" | Câu trả lời, gắn với khoá câu hỏi và loại bài toán | Câu hỏi cùng khoá được điền sẵn lựa chọn đó, ghi "lần trước bạn đã chốt" kèm nội dung bài học |
| Dòng luật bạn **sửa** trong bản nháp | Sửa luật box thành "sai lệch tối đa 2px" | Lời mới, gắn với mã của dòng luật đó | Luật đó xuất hiện bằng lời của bạn, ghi *(theo lần trước)* |
| Dòng luật bạn **xoá** | Bỏ luật "giữ hướng vẽ nhất quán" | Mã luật, đánh dấu "đã bỏ" | Luật không xuất hiện lại; phụ lục D ghi "đã bỏ theo lần trước" |
| Dòng bạn **thêm** | "Xe ba bánh chở hàng gán truck" | Nguyên văn, gắn với mục chứa nó | Được đưa vào đúng mục, ghi *(từ lần trước, xác nhận còn áp dụng)* |
| **Ghi nhận sai sót** sau khi dùng (reviewer trả bài, mentor sửa luật) | "Tay trên vô-lăng: không đặt cổ tay lên vành" | Nguyên văn, nguồn "phản hồi sau khi dùng" | Như dòng bạn thêm |

Cách tool nhận ra bạn sửa dòng nào: mỗi dòng luật trong bản nháp mang một mã ẩn (`<!--r:kp.lr-->`).
Lúc duyệt, tool so bản bạn lưu với bản nó soạn theo từng mã: mã còn mà lời khác là sửa, mã mất là xoá,
dòng không có mã là thêm.

Những gì **không** tự thành bài học, để tool không củng cố cái sai của chính nó:

- Lựa chọn tool gợi ý mà bạn không chốt. Câu chưa chốt chỉ là *(đề xuất, chưa chốt)* và không được ghi nhớ.
- "Lỗ hổng tool tự phát hiện". Chúng chỉ được lưu là **giả thuyết**, nhắc lại cho người xem, không tự sửa
  luật nào. Ý của LLM (nếu bật) chỉ hiện trong bản nháp với nhãn *(LLM đề xuất, chưa chốt)*.
- Việc tool gợi ý nhãn (tab Gợi ý, Cả job). Các lần gán nhãn không dạy lại gì cho tool; ngưỡng và prompt
  chỉ đổi khi bạn sửa ở tab Label hoặc trong profile.

Phạm vi và cách kiểm soát:

- Bài học chỉ áp cho **cùng loại bài toán** (detection, semantic, keypoint, tracking). Bài học keypoint
  không ảnh hưởng tới job bounding box.
- Bài học mới hơn thắng bài học cũ; bài học của bạn thắng bài học khởi đầu lấy từ guideline mẫu.
- Mọi bài học đã dùng được liệt kê ở **Phụ lục D** của bản nháp, kèm mã và nguồn. Ở trang duyệt, mỗi bài
  học có nút tắt; tắt rồi thì lần sau không áp nữa.
- Guideline mới bạn gửi (như hai guideline skeleton) vào tool qua `guideline_library/`: nội dung chuyển
  thành `reference/*.md`, còn định nghĩa, luật, mẫu skeleton và bài học khởi đầu nằm trong
  `knowledge.yaml`. Đây là việc làm tay một lần cho mỗi guideline; tool không tự đọc file `.docx`. Khi có
  cấu hình LLM, phần đầu của các file `reference/*.md` được gửi kèm làm tài liệu nền.
- Tất cả nằm trong `guideline_library/lessons.jsonl`, mỗi bài học một dòng văn bản, nên file rất nhẹ và
  mở bằng Notepad để đọc hoặc xoá dòng được. Xoá file là tool quên hết, chỉ còn bài học khởi đầu.

Giới hạn cần biết: tool chỉ học được điều bạn **nói ra** qua các tín hiệu trên. Nó không tự so nhãn bạn
đã sửa trên CVAT với nhãn nó đề xuất, và không tự biết một luật rõ ràng nhưng sai mục đích.

**Có và không có LLM.** Mặc định tool chạy không cần LLM: bản nháp lấy định nghĩa và quy tắc từ 5
guideline mẫu trong `guideline_library/reference/`, class mới sẽ ghi `⟨cần viết định nghĩa⟩`. Nếu cấu
hình LLM trong `.env` (Claude API, hoặc model chạy trên máy qua Ollama/LM Studio), tool làm thêm:

- viết định nghĩa cho class mới và quy tắc cho cặp dễ nhầm, theo văn phong guideline mẫu;
- nhìn vài ảnh và kể tên các loại vật SAM3 chưa được hỏi tới, rồi cho SAM3 đo chúng;
- chạy hai **annotator ảo** đọc bản nháp và gán các ca khó; chỗ hai bên gán khác nhau là chỗ guideline mơ hồ.

Ảnh chỉ được gửi cho LLM khi bạn tick "Cho phép gửi ảnh mẫu tới LLM"; mặc định chỉ gửi số liệu.

### Tab Cài đặt

Địa chỉ backend, API key, tự gợi ý khi chuyển frame, tải lại trang sau khi ghi, hỏi lại trước khi
ghi, độ đậm vùng tô. Ẩn/hiện panel: menu của Tampermonkey > *SAM3 Assist: hiện/ẩn panel*.

**Ngôn ngữ / Language (từ bản 0.8.1).** Chọn *English* thì mọi chữ đổi sang tiếng Anh:
- panel và thông báo của backend;
- tên profile, trang duyệt guideline;
- bản nháp guideline soạn từ lúc đó.

Trong app desktop, lựa chọn này dùng chung với bảng điều khiển, khay hệ thống và thông báo Windows.

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

Thêm cho job skeleton và job video (đây là chỗ tool chưa từng chạm vào máy chủ thật):

| # | Việc làm | Kết quả đúng | Nếu sai |
| --- | --- | --- | --- |
| 7 | Mở job skeleton, tab Label | Đầu panel ghi profile *Người lái: HumanPose-17 + Facial Landmark-50*. Mỗi skeleton một dòng (`skeleton 17 điểm` cho person; 5, 5, 4, 8, 8, 12, 8 điểm cho mặt), ô tick bật được. Dòng xám "Tool coi label này là 'person'…" là bình thường khi tên label khác guideline. Không có dòng cam báo "chưa nhận ra", "ghép theo thứ tự" hay "không có điểm" | "Chưa nhận ra": chọn skeleton ở ô ngay dưới tên label. Hai dòng cam còn lại: chụp tab Label và gửi tôi kèm JSON label của task (tab *Raw* trong phần label của task) |
| 8 | Gợi ý trên một frame có người lái | Lần đầu chờ tải model; chấm và đường nối nằm đúng trên người; **chấm xanh (R) ở bên phải ảnh, chấm đỏ (L) ở bên trái ảnh**; trỏ vào đề xuất thấy số điểm khớp với hình trong guideline | Lỗi tải model: chạy lại `setup.ps1 skeleton`, xem log backend. Không thấy người: thêm prompt ở tab Label (ví dụ `baby doll`, `mannequin`) |
| 8b | Gợi ý trên một frame thấy rõ mặt | 7 skeleton mặt; điểm 14 ở khoé ngoài mắt bên trái ảnh, 30 và 36 ở hai khoé miệng, 13 nằm trên chóp mũi một chút | Điểm lệch có hệ thống (ví dụ 10 quá cao): sửa `t` trong profile, xem `docs/PROFILES.md` |
| 9 | Tick **một** skeleton, ghi | Skeleton hiện trong CVAT, kéo từng điểm được, điểm Occluded/Outside có cờ đúng | Đọc dòng lỗi đỏ (thường là lỗi 400 kèm tên trường) và gửi tôi |
| 10 | Job video, tab Cả job, đặt khoảng 0–20, Theo dõi | Có track; tua thanh player thì box đi theo người | Gửi dòng lỗi đỏ |
| 11 | Tick **một** track, ghi | Track hiện trong CVAT ở chế độ Track, nội suy giữa keyframe, `outside` đúng chỗ | Gửi dòng lỗi đỏ |

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
| Polyline theo quy ước của lớp | Vạch đơn và curb: một đường theo tim vạch. Vạch đôi: hai đường theo hai mép ngoài của cả cặp vạch. Crosswalk: hai đường thẳng theo hai mép dài của cả vùng, kéo từ sọc đầu tới sọc cuối |
| Không nối tắt qua vùng không có vạch | Không nối qua khoảng trống trên 30px trong cùng một mask; các nét đứt thẳng hàng cách nhau tới 60px được nối (`join_dashes`) |
| Giữ hướng vẽ nhất quán | Vạch dọc vẽ từ gần xe tới xa; vạch ngang vẽ từ trái sang phải |
| single/double, white/yellow/other | Prompt riêng cho từng loại, rồi đo lại: màu từ pixel thật, đơn/đôi từ số nét song song trong mask; lệch thì đổi class và gắn `UNCERTAIN_CLASS` |

### HumanPose-17 (skeleton `person`, người lái)

| Guideline | Tool làm gì |
| --- | --- |
| Chỉ label `person` và 17 sublabel `1`–`17`; không đổi tên | Ghép điểm của profile với sublabel của task theo tên, số, rồi thứ tự; thiếu điểm nào thì báo ở tab Label. Tool không tạo hay đổi tên sublabel |
| R (số chẵn) ở phải ảnh, L (số lẻ) ở trái ảnh, bất kể quay mặt, quay lưng, vặn mình; không suy theo giải phẫu | ViTPose trả trái/phải theo người; tool xếp lại theo phía của ảnh cho từng chuỗi (tay theo vai, chân theo hông, đầu theo mắt) |
| Chuỗi vai–khuỷu–cổ tay mỗi phía liền mạch, không bắt chéo | Cả chuỗi đi theo gốc của nó. Vai và hông cho hai hướng khác nhau (vặn mình), hai vai gần thẳng hàng, hoặc hai cẳng chân cắt nhau: gắn cờ `UNCERTAIN_CLASS` |
| Visible / Occluded / Outside bằng property của CVAT | Từ độ tin cậy của model: ≥ 0,6 Visible; 0,35–0,6 Occluded kèm cờ `ATTRIBUTE_CHECK`; thấp hơn là Outside. Điểm model đặt ngoài khung hình luôn là Outside, không kéo về mép |
| Hai khớp Visible không thể trùng toạ độ | Hai điểm Visible cách nhau dưới 2 px: gắn cờ `ATTRIBUTE_CHECK` |
| Chỉ gán người lái; có người thứ hai thì không gán | Mỗi ảnh một đề xuất: người có diện tích lớn nhất, kèm cờ `UNCERTAIN_SCOPE` khi thấy nhiều người. Tool không biết ai là người lái |
| Sai số ±5 px (Visible), ±20 px (Occluded); khớp đặt ở tâm khớp | Tool **không** bảo đảm được: ViTPose đặt theo quy ước COCO (ví dụ tai, hông có thể lệch vài pixel so với "ống tai", "tâm khớp háng"). Phóng to và chỉnh từng điểm |
| Checklist: đúng một skeleton, đủ 17 điểm, R/L đúng phía | Tab Kiểm tra: thừa skeleton `person`, thiếu điểm, L nằm bên phải R, điểm sát mép ảnh, hai điểm Visible trùng nhau |

### Facial Landmark-50 (7 skeleton mặt)

| Guideline | Tool làm gì |
| --- | --- |
| 7 skeleton, Point ID liên tục 0–49; `trai`/`phai` theo khuôn mặt trên ảnh | Mỗi skeleton lấy điểm từ MediaPipe ở phía tương ứng của ảnh; ghép với sublabel theo số |
| Lông mày: 5 điểm trên bờ trên, chia đều; 0 = đuôi ngoài … 9 = đuôi ngoài | 5 điểm chia đều theo chiều dài hàng điểm bờ trên lông mày của MediaPipe |
| Sống mũi: 10 ngang khoé mắt trong, 13 trên lỗ mũi, chưa tới chóp mũi | 4 điểm chia đều trên đường giữa mũi, dừng trước chóp mũi. Đây là chỗ dễ lệch nhất; chỉnh `t` trong profile nếu lệch có hệ thống |
| Mắt: 2 khoé + mí trên và mí dưới ở 1/4, 1/2, 3/4 chiều rộng mắt; vòng kín theo chiều kim đồng hồ từ điểm trái nhất | Nội suy đúng các tỉ lệ đó trên bờ mi của MediaPipe; thứ tự: khoé trái, mí trên trái sang phải, khoé phải, mí dưới vòng về |
| Môi ngoài: bờ trên theo 0,18 · 0,36 · 0,50 · 0,66 · 0,84; bờ dưới chia đều | Nội suy đúng các tỉ lệ đó theo chiều rộng miệng |
| Môi trong luôn nằm trong môi ngoài; miệng đóng vẫn đủ 8 điểm | Lấy mép trong môi của MediaPipe; miệng đóng thì hai bờ gần trùng nhau, không bỏ skeleton |
| Ngưỡng bị che: mắt 4/8, lông mày 3/5, môi ngoài 6/12, môi trong 4/8, sống mũi 2/4; dưới ngưỡng thì Outside cả skeleton nếu không đủ bằng chứng | SAM3 tìm `sunglasses`, `hand`, `face mask`. Điểm dưới vật che: Occluded. Dưới ngưỡng: Outside cả skeleton, kèm cờ để bạn quyết định có đủ bằng chứng thị giác không |
| Kính trong vẫn Visible; kính râm đen không xác định được thì Outside | Chỉ tìm `sunglasses`, không tìm kính thường. SAM3 có thể nhầm hai loại: luôn kèm cờ `ATTRIBUTE_CHECK` |
| Mặt nghiêng mạnh: không dồn điểm phía xa về giữa mặt; không ước lượng được thì Outside | Hai mức, đo bằng độ lệch của chóp mũi so với hai khoé mắt: nghiêng rõ thì lông mày và mắt phía xa vẫn Visible nhưng kèm cờ `UNCERTAIN_BOUNDARY`; chóp mũi gần đè lên khoé mắt phía xa thì đặt Occluded kèm cờ `ATTRIBUTE_CHECK`. Tool không tự chuyển sang Outside |
| Một mặt: người lái | Mặt của người đã chọn làm người lái, hoặc mặt lớn nhất; kèm cờ `UNCERTAIN_SCOPE` khi có nhiều mặt |
| Checklist: đủ 7 skeleton, 50 điểm; mắt không bắt chéo; `moitrong` trong `moingoai` | Tab Kiểm tra: thiếu skeleton, thiếu điểm, vòng kín chạy ngược chiều hoặc tự cắt, `…trai` nằm bên phải `…phai`, điểm môi trong lọt ra ngoài môi ngoài, điểm Outside lẻ trong một skeleton |

### Tracking (Lab 5)

| Guideline | Tool làm gì |
| --- | --- |
| Rectangle, chế độ Track | Ghi thành track của CVAT, mỗi đối tượng một track |
| Box toàn thân, sát mép người | Box lấy từ mask của SAM3, làm mượt qua 5 frame để không rung |
| Bị che một phần: box vẫn ở vị trí toàn thân + `occluded` | Box tụt dưới 80% chiều cao gần đây (mà không chạm mép ảnh) thì giữ kích thước toàn thân và đặt `occluded`; bị người gần camera hơn che cũng đặt `occluded` |
| Bị che hoàn toàn, ra khỏi khung: `outside` từ frame mất hẳn | Đặt keyframe `outside` ở frame đầu tiên không còn thấy |
| Khuất ≤ 60 frame và chắc cùng người: cùng ID | Nối lại khi ngoại hình (màu quần áo) giống và chỗ hiện lại hợp với hướng đi; luôn gắn cờ `id_check` để bạn xác nhận |
| Khuất > 60 frame hoặc không chắc: track mới | Tạo track mới; nếu giống một track vừa mất thì gắn cờ gợi ý gộp |
| Keyframe ở đầu/cuối, chỗ đổi hướng, đổi tốc độ, trước và sau khi bị che | Chọn ít keyframe nhất sao cho nội suy luôn có IoU ≥ 0,8 với box thật (guideline lấy 0,7 làm ngưỡng trôi); thêm keyframe ở chỗ đổi `occluded` và không để cách nhau quá 20 frame |
| Trên 50 keyframe / 100 frame là quá dày | Gắn cờ `drift` |
| `merge`, `id_switch`: hai người đi sát | Hai track chồng nhau từ 3 frame thì gắn cờ `id_check` cho cả hai |

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

### Chạy backend bằng Docker (tuỳ chọn)

Repo có `Dockerfile` và `docker-compose.yml` cho máy có GPU NVIDIA (trên Windows: Docker Desktop với
WSL2). Tạo `.env` với `HF_TOKEN=<token đã được duyệt quyền facebook/sam3>`, rồi `docker compose up --build`.
Checkpoint, bài học guideline và các lượt soạn nằm trong volume `sam3-data` nên không mất khi cập nhật.
Hai file này **chưa được build thử** (môi trường viết tool không có Docker daemon); nếu build lỗi, gửi
tôi dòng lỗi.

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
- **Guideline mới cho task khác:** nạp file guideline ở tab **Guideline** (xem
  [Nạp guideline có sẵn](#tab-guideline-nạp-guideline-có-sẵn-cho-task-khác)). Muốn chỉnh sâu hơn: sửa file
  `profiles/gl_<tên>.yaml` tool vừa tạo, hoặc chép `profiles/_template.yaml` thành file mới, rồi bấm
  **Nạp lại profile** ở tab Label. Hướng dẫn từng trường: `docs/PROFILES.md`.
- **Luật mới chưa có sẵn** (ví dụ một attribute suy ra từ hình dạng): thêm hàm trong
  `backend/pipeline.py` và đọc tham số từ mục `rules` của profile.

---

## 10. Giới hạn đã biết

- **Prompt quyết định chất lượng.** SAM3 mạnh với danh từ cụ thể, yếu với khái niệm trừu tượng.
  Các prompt mặc định là điểm khởi đầu; hãy chỉnh ở tab Label sau khi thử trên dữ liệu thật.
- **area/direct và area/alternative** là suy luận hình học từ mặt đường và vạch kẻ. Ở ngã tư, đường
  cong gắt, hoặc khi không thấy vạch, kết quả cần sửa tay; tool gắn cờ các trường hợp đó.
- **Vạch đơn hay đôi**: tool chỉ nâng vạch đơn thành vạch đôi khi mask của SAM3 tách được hai nét song song. Nếu SAM3 gộp cả cặp thành một khối thì vẫn theo prompt.
- **occluded** chỉ là ước lượng. Quy tắc "che khuất quá 80%" trong guideline cần người đánh giá.
- **Phản chiếu, bóng đổ, hình trên billboard**: tool không tự nhận ra. Bỏ tick các đề xuất này.
- **Bản guideline nháp không thay được người phụ trách.** Số liệu do SAM3 đếm nên có thể sót hoặc nhầm;
  luật chưa chốt là gợi ý. Với bài trên lớp, guideline của mentor vẫn là thứ dùng để chấm.
- **SAM3 chỉ đo được thứ được gọi tên.** Bộ từ vựng khám phá mặc định dành cho ảnh đường phố; bộ ảnh
  lĩnh vực khác cần bạn thêm từ khoá (hoặc bật LLM để nó đề xuất).
- **Tool không phát hiện được luật rõ ràng nhưng sai mục đích.** Tự kiểm tra chỉ bắt được chỗ thiếu và
  chỗ mơ hồ; chỗ sai mục đích chỉ lộ ra khi người duyệt sửa hoặc reviewer trả bài, và khi đó hãy ghi nhận
  để tool nhớ.
- **Chưa hỗ trợ** cuboid, ellipse, tag, track kiểu polygon/mask, và track skeleton qua video
  (skeleton chỉ gợi ý theo từng frame).
- **Trạng thái Occluded/Outside của skeleton** là suy đoán: với người, từ độ tin cậy của model (ngưỡng
  0,6 / 0,35 là giá trị khởi đầu, chỉnh trong profile sau khi thử); với mặt, từ vật che do SAM3 tìm.
  MediaPipe luôn trả đủ điểm kể cả khi mặt bị che, nên vị trí điểm bị che là ước lượng của model.
- **Skeleton mặt cần MediaPipe tìm được mặt.** Mặt quá nghiêng, quá tối, bị che nhiều, hoặc mặt búp bê
  có thể không được tìm thấy; khi đó vẽ hộp quanh mặt, hoặc đặt tay.
- **Ảnh bị xoay 90°:** quy ước R/L theo ảnh vẫn đúng, nhưng ViTPose và MediaPipe kém hẳn trên người nằm
  ngang; kiểm tra kỹ hơn.
- **Theo dõi dựa vào phát hiện từng frame**, không dùng chế độ video của SAM3. Hai người mặc giống
  nhau đi cắt ngang nhau là ca dễ đổi ID nhất; tool gắn cờ nhưng không tự sửa được.
- **Theo dõi tải mọi frame** từ CVAT về trình duyệt rồi gửi sang backend. 150 frame mất vài phút trên
  RTX 2060 (ước lượng, chưa đo trên máy thật).
- **Ảnh bị xoay trong CVAT** làm lớp xem trước lệch. Trả ảnh về góc 0 trước khi dùng.
- **Quy ước polyline** (`polyline_mode` trong profile) theo cách lớp đang chấm: vạch đôi vẽ hai mép
  ngoài (`edges`), crosswalk vẽ hai mép dài (`long_edges`), vạch đơn và curb vẽ tim (`centerline`).
  Batch khác có quy ước khác thì đổi trong profile, không cần sửa code.

---

## 11. Xử lý sự cố

**Panel không hiện.** Kiểm tra theo thứ tự: biểu tượng Tampermonkey có hiện số 1 trên trang CVAT
không (script có chạy); đã bật *Allow User Scripts* / *Developer mode* chưa; địa chỉ trang có đúng
dạng `/tasks/<id>/jobs/<id>`; panel có bị ẩn không (menu Tampermonkey > hiện/ẩn panel).

**Chấm đỏ, "Không kết nối được backend".** Backend chưa chạy hoặc sai địa chỉ. Mở địa chỉ backend
trong tab mới để thử. Tampermonkey có thể đã chặn tên miền: mở trang cấu hình script > *Settings* >
*XHR Security*, thêm tên miền vào danh sách cho phép.

**Mã lỗi (từ bản 0.8.1).** Mỗi lỗi kèm một mã cố định ở cuối, ví dụ `(E_BACKEND_UNREACHABLE)`. Mã giống nhau dù
giao diện tiếng Việt hay tiếng Anh, nên khi báo lỗi chỉ cần gửi mã. Mã hay gặp:

| Mã | Nghĩa |
|---|---|
| `E_BACKEND_UNREACHABLE` | Không kết nối được backend |
| `E_BACKEND_TIMEOUT` | Backend không phản hồi |
| `E_AUTH` | Sai hoặc thiếu API key |
| `E_HOST_NOT_ALLOWED` | Mở backend bằng địa chỉ không được phép |
| `E_GPU_OOM` | Thiếu bộ nhớ GPU |
| `E_HF_NOT_APPROVED` / `E_HF_PENDING` | Tài khoản Hugging Face chưa được Meta duyệt |
| `E_COMPONENT_MISSING` | Thiếu thành phần, mở bảng điều khiển để tải |
| `E_CVAT_HTTP` | CVAT trả lỗi |

Nhật ký (`logs/*.log`) luôn viết bằng tiếng Anh kỹ thuật.

**"Backend trả lỗi 401" (`E_AUTH`).** API key trên panel khác `SAM3_API_KEY` của backend.

**"Backend trả lỗi 400: Host '…' không được phép".** Từ bản 0.4.4 backend chạy trên máy chỉ nhận request gửi
tới `127.0.0.1` hoặc `localhost`, để trang web lạ không gọi được nó. Ở tab Cài đặt, đặt địa chỉ backend là
`http://127.0.0.1:8765`. Nếu cố ý dùng backend từ máy khác (qua IP hoặc tên máy), thêm tên đó vào
`SAM3_ALLOWED_HOSTS` trong `.env` (ví dụ `SAM3_ALLOWED_HOSTS=127.0.0.1,localhost,192.168.1.20`) và đặt
`SAM3_API_KEY`. Colab: dùng notebook mới trong thư mục `colab/` (đã cấu hình sẵn).

**Job skeleton: "Backend trả lỗi 500. NameError: name 'inv' is not defined".** Thiếu gói `scipy` mà ViTPose cần.
Trong môi trường của backend chạy `pip install scipy` rồi khởi động lại backend. Từ bản 0.3.4 gói này nằm sẵn
trong `requirements.txt`.

**Job skeleton: tab Label báo "Skeleton này không có trong profile" hoặc "Tool chưa nhận ra skeleton này".**
Tên label trong task khác tên trong guideline (ví dụ `person_vf17`). Từ bản 0.3.3 tool tự nhận theo tên gần
giống, dãy số của các điểm hoặc số điểm; kiểm tra đầu panel đang ghi backend 0.3.3 trở lên. Nếu vẫn báo
"chưa nhận ra": chọn skeleton ở ô ngay dưới tên label trong tab Label.

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
node tests/userscript_smoke.cjs         # job ảnh: gợi ý, ghi, kiểm tra, hoàn tác
node tests/userscript_smoke_v2.cjs      # job skeleton, gợi ý cả job, theo dõi video, CVAT local, nạp guideline
node tests/userscript_smoke_v3.cjs      # các tính năng thao tác nhanh của bản 0.8.0
node tests/userscript_i18n_smoke.cjs    # panel tiếng Anh không còn chữ tiếng Việt
```

**Thêm chữ hiện cho người dùng:**
1. Viết tiếng Việt trong mã như bình thường.
2. Chạy `python scripts/i18n_extract.py --missing` để lấy chuỗi chưa dịch. Cần `npm install acorn acorn-walk`.
3. Thêm bản dịch vào `backend/i18n/en.json`. Thông báo lỗi thì thêm mã vào mục `codes`.
4. Chạy `python scripts/i18n_build.py` để nhúng bộ dịch vào userscript và bảng điều khiển.

Thiếu bản dịch thì test báo lỗi.

---

## 13. Cấu trúc repo

```
sam3-cvat-assist/
├── userscript/sam3-cvat-assist.user.js   Panel + lớp xem trước + gọi API CVAT (cài vào Tampermonkey)
├── desktop/                              Bản Windows: cửa sổ CVAT có sẵn panel, trình tải thành phần, quản lý backend
├── installer/                            Dựng bộ cài: build.py, Inno Setup, SAM3 Assist.exe (xem docs/DESKTOP.md)
├── backend/
│   ├── app.py                            HTTP API: /health /resolve /propose /interactive /qa /track/*
│   ├── pipeline.py                       Luật guideline: từ mask SAM3 tới đề xuất
│   ├── keypoints.py                      Skeleton người lái 17 điểm (R/L theo ảnh) và 7 skeleton mặt 50 điểm
│   ├── tracking.py                       Nối ID qua frame, box toàn thân, outside, chọn keyframe
│   ├── guideline/                        Soạn guideline nháp: survey.py (quét ảnh), draft.py (câu hỏi, văn bản,
│   │                                     profile, tự kiểm tra), library.py (bài học), llm.py, service.py, page.py
│   │                                     importer.py (nạp guideline .docx/.md có sẵn)
│   ├── geometry.py                       Mask -> bbox / polygon / polyline / mask RLE của CVAT
│   ├── qa.py                             Kiểm tra annotation theo checklist
│   ├── profiles.py                       Đọc profile, ghép với label của task
│   ├── config.py                         Cấu hình từ .env
│   └── engines/                          sam3_official.py, sam3_hf.py, mock.py, pose.py (ViTPose, MediaPipe)
├── profiles/                             Mỗi guideline một file YAML (+ generic, _template); archive/ = quy ước đã thôi dùng
├── guideline_library/                    reference/ (guideline mẫu), knowledge.yaml (định nghĩa, cặp dễ nhầm, mẫu skeleton,
│                                         bài học khởi đầu), archive/, lessons.jsonl và runs/ (tạo ra khi dùng)
├── Dockerfile, docker-compose.yml        Chạy backend bằng Docker
├── scripts/                              setup và run cho Windows / Linux
├── colab/sam3_backend_colab.ipynb        Chạy backend trên Colab
├── docs/PROFILES.md                      Cách viết profile
└── tests/                                pytest + test đầu-cuối userscript
```

SAM 3 do Meta phát hành với giấy phép riêng; đọc giấy phép tại repo của SAM3 trước khi dùng cho mục
đích ngoài học tập.

## 14. Chính sách ký số (Code signing policy)

Bộ cài Windows được build tự động từ mã nguồn trong repo này bằng GitHub Actions
(`.github/workflows/windows-installer.yml`) và được ký số khi có chứng chỉ (xem [docs/SIGNING.md](docs/SIGNING.md)).
Khi ký qua SignPath Foundation: *Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).*

- **Người duy trì, duyệt và phát hành:** [11thanh2233](https://github.com/11thanh2233).
- **Chỉ ký bản build từ GitHub Actions của repo này**, từ tag phát hành `v<bản>`. Không ký file build trên máy cá nhân.
- **Quyền riêng tư:**
  - Tool chỉ kết nối tới các địa chỉ CVAT bạn tự thêm, Hugging Face (tải trọng số, khi bạn đưa token) và GitHub
    Releases (kiểm bản mới; tắt được).
  - Ảnh chỉ gửi tới LLM bên ngoài khi bạn bật rõ ràng ở phần soạn guideline.
  - Tool không thu thập dữ liệu sử dụng.
