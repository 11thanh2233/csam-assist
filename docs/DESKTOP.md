# Bản desktop cho Windows (`SAM3-Label-Assist-Setup-<bản>.exe`)

Cài bằng một file, mở bằng một biểu tượng. Không cần Python, PowerShell hay Tampermonkey.

## Cài đặt

1. Tải `SAM3-Label-Assist-Setup-<bản>.exe` ở mục **Releases** của repo
   (bản thử mỗi lần cập nhật code: mục **Actions** → lần chạy mới nhất → **Artifacts**).
2. Chạy file. Windows có thể hiện **"Windows protected your PC"** vì file chưa ký số. Bấm **More info → Run anyway**.
   Mỗi bản phát hành có ghi mã SHA-256 của bộ cài trong Release, dùng để kiểm file tải về đúng là bản đó:
   `Get-FileHash .\SAM3-Label-Assist-Setup-0.6.0.exe` trong PowerShell.
3. Bộ cài chép app vào `AppData\Local\Programs\SAM3 Label Assist` (không cần quyền admin), rồi mở **trình tải
   thành phần**:
   1. **Kiểm tra máy:** card NVIDIA, VRAM, driver. Không có card NVIDIA thì cài PyTorch bản CPU (nhỏ hơn, nhưng rất chậm).
   2. **Giấy phép SAM:** đọc và đồng ý.
   3. **Token Hugging Face:** dán token loại *Read*. App kiểm tra ngay token có hợp lệ và đã được Meta duyệt vào
      `facebook/sam3` chưa. Meta chưa duyệt? Bấm **Cài trước, nhập token sau**: phần còn lại vẫn cài, SAM 3 tải sau
      trong app.
   4. **Chọn thành phần** và xem dung lượng: PyTorch (bắt buộc), SAM 3 (bắt buộc), skeleton người, skeleton mặt.
   5. **Tải và cài:** có thanh tiến trình. Rớt mạng thì bấm **Thử lại**, app tải tiếp từ chỗ dừng.
   6. **Sẵn sàng:** app chạy thử SAM 3 trên một ảnh mẫu (từ 0.9.1) và báo **Mọi thứ hoạt động** hoặc lỗi kèm cách sửa.

   Máy thiếu Microsoft Visual C++ (PyTorch cần) thì bộ cài tự cài trước, Windows hỏi quyền admin một lần (từ 0.9.1).

Dung lượng (máy có card NVIDIA): bộ cài khoảng 150 MB; tải thêm khoảng 6 GB; sau khi cài chiếm khoảng 9–10 GB,
lúc đang cài cần trống khoảng 12 GB.

## Dùng

Mở **SAM3 Label Assist** từ Desktop hoặc Start Menu. Bảng điều khiển (từ 0.9.0) có sáu trang ở thanh bên trái:

- **Tổng quan:** backend đang chạy hay không, trên card nào, VRAM, nhiệt độ GPU. Nút mở nhanh trang gán nhãn và
  job gần đây (**Mở tiếp**). Backend lỗi thì hiện nguyên nhân và các bước sửa, mỗi bước một nút bấm.
- **Trang gán nhãn:** danh sách địa chỉ CVAT (có sẵn `cvat.edufun.ai` và `localhost:8080`, thêm được địa chỉ khác).
  Bấm **Mở**: cửa sổ CVAT mở ra, panel của tool tự hiện như khi dùng Tampermonkey. Đăng nhập CVAT ngay trong cửa sổ
  đó; app nhớ đăng nhập. Hiện panel hỗ trợ CVAT; nền tảng khác sẽ thêm dần.
- **Thành phần:** mỗi thành phần một thẻ: đã cài gì, bản nào; tải thêm, cập nhật hoặc gỡ từng phần.
- **Dùng chung nhóm:** chia sẻ backend trong mạng nội bộ, hoặc dùng backend của máy khác.
- **Hỗ trợ:** tạo gói báo lỗi, xem nhật ký (lọc theo chữ, chỉ xem dòng lỗi, chép), sao lưu và đồng bộ.
- **Cài đặt:** ngôn ngữ, giao diện sáng/tối, tự chạy nền khi bật Windows, tự bật và tự bật lại backend, thư mục dữ liệu.

Backend tự bật khi mở app. Nếu đang chạy mà tắt bất ngờ, app tự bật lại (tối đa 3 lần trong 10 phút) và báo qua khay
hệ thống; tắt được trong Cài đặt.

Trang **Tổng quan** còn có số nhãn đã ghi hôm nay, thời gian tiết kiệm ước tính và biểu đồ 7 ngày (nút **Xuất CSV**).
Bấm số phiên bản ở thanh bên để mở trang **Giới thiệu**: giấy phép của SAM 3 và các phần mềm đi kèm.

Cập nhật an toàn (từ 0.9.1): app tự sao lưu dữ liệu trước khi cài bản mới; bản mới không mở được thì tự quay về bản cũ.
Chi tiết: [COMPAT.md](COMPAT.md).

Thao tác nhanh: **Ctrl+K** mở ô tìm thao tác (mở CVAT, bật backend, gửi báo lỗi…), **Alt+1…6** chuyển trang.
Mọi hộp thoại đóng được bằng **Esc**, bấm ra ngoài hoặc nút ✕. Xoá một trang gán nhãn có nút **Hoàn tác**.

Đóng bảng điều khiển thì app vẫn chạy ở khay hệ thống (góc phải thanh taskbar). Thoát hẳn: chuột phải biểu tượng → **Thoát**.

Userscript cho Tampermonkey vẫn dùng được song song cho ai thích trình duyệt quen thuộc.

## Đã có bản cũ? Không tải lại thứ đã có (từ 0.7.0)

Bước đầu của trình cài **quét máy** và liệt kê từng thành phần với một trong các trạng thái: *đã có, đúng bản*,
*cần cập nhật*, *dùng lại file có sẵn*, hoặc *chưa có*, kèm dung lượng phải tải. Những nơi được quét:

- bộ nhớ đệm Hugging Face (`.cache\huggingface\hub`, hoặc `HF_HOME` / `HF_HUB_CACHE`): bản cài tay 0.3.x – 0.5.x
  tải SAM 3 và ViTPose vào đây;
- thư mục tool cũ tên `sam3-cvat-assist` hoặc `csam-assist` trong Downloads, Desktop, Documents. Ở chỗ khác thì
  bấm **Thêm thư mục tool cũ để quét**. App lấy `models\face_landmarker.task`;
- model đã cài của chính app.

Cách dùng lại:

- **Kiểm trước khi dùng:** file chỉ được dùng lại khi đúng dung lượng và đúng mã băm Hugging Face công bố. File
  sai thì tải lại.
- **Không tốn thêm ổ đĩa:** dùng lại bằng liên kết cứng nếu cùng ổ đĩa, không được thì chép.
- **Token chỉ khi cần:** nếu trọng số SAM 3 đã có đủ trên máy thì không cần token Hugging Face.
- **PyTorch trong `.venv` cũ không được dùng lại.** Nó gắn với Python của máy đó và không kiểm mã băm lại được,
  nên an toàn hơn là tải bản đã ghim.

## Cập nhật

- **Bản app mới:** mỗi ngày một lần, app hỏi GitHub Releases của tool. Có bản mới thì nút **Có bản …** hiện trên
  thanh tiêu đề; mục **Cập nhật app** có ghi chú thay đổi và nút **Tải bản cập nhật**. Sau đó:
  1. App tải bộ cài từ github.com qua HTTPS và kiểm mã SHA-256 GitHub công bố. Sai mã thì xoá, không chạy.
  2. Bấm **Cài và mở lại app**: app đóng lại, bộ cài chạy ở chế độ cập nhật (không hỏi gì), xong tự mở lại app.
  3. PyTorch, trọng số, profile và dữ liệu tự học giữ nguyên. App không bao giờ hạ phiên bản.

  Tắt tự kiểm: bỏ tick **Tự kiểm bản mới mỗi ngày**. Vẫn bấm **Kiểm tra ngay** được.
- **Thành phần:**
  - **PyTorch được ghim bản** (`installer/torch-pin.txt`). App chỉ bắt tải lại PyTorch khi bản app mới cần bản
    khác; bảng điều khiển hiện dòng *"Bản app này cần cập nhật: PyTorch"*.
  - **Trọng số** có bản mới trên Hugging Face thì hiện *cần cập nhật (tuỳ chọn)*, và chỉ tải các file đã đổi.
- **Có gì mới:** lần đầu mở app sau khi cập nhật, khung **Có gì mới** liệt kê thay đổi của các bản từ bản bạn đang
  dùng trước đó, lấy từ `docs/CHANGELOG.md` đi kèm bộ cài, không cần mạng. Xem lại bất cứ lúc nào: **Nhật ký thay đổi**.
- **Lần lên 0.7.0 phải tải tay** `setup.exe` một lần, vì bản 0.6.0 chưa có nút cập nhật. Chạy bộ cài mới đè lên
  bản cũ là được. Trình cài thấy PyTorch và trọng số đã có thì bấm **Xong (không cần tải gì)**.

## Từ bản 0.8.0

### Gán nhãn nhanh

| Phím (giữ Alt) | Việc |
|---|---|
| Alt+G | Gợi ý nhãn cho frame này |
| Alt+Enter | Ghi rồi sang frame tiếp (frame sau đã được chạy sẵn) |
| Alt+J / Alt+K | Chọn đề xuất tiếp / trước |
| Alt+X | Tick / bỏ tick đề xuất đang chọn |
| Alt+Delete | Bỏ đề xuất đang chọn |
| Alt+Z | Hoàn tác (bấm tiếp để lùi thêm, tối đa 20 lần ghi) |
| Alt+P | Ẩn / hiện panel |
| Alt + bấm lên ảnh | Tick / bỏ tick đề xuất dưới con trỏ |

Đổi phím ở **Cài đặt panel (nút bánh răng) → Nâng cao → Phím tắt**. Tool không cho đặt hai việc trùng một phím, cũng không cho dùng phím
trùng trình duyệt/Windows.

### Dùng chung trong nhóm

- **Một máy GPU phục vụ cả nhóm.** Trên máy có GPU, mở bảng điều khiển → **Dùng chung nhóm** → bật
  **Chia sẻ backend của máy này**. App hiện địa chỉ (ví dụ `http://192.168.1.20:8765`) và một key.
  - Windows có thể hỏi về tường lửa: chọn *Private networks*.
  - Chỉ máy có key mới dùng được.
- **Máy không có GPU:** khi cài, ở bước *Quét máy* bấm **Dùng backend ở máy khác**, nhập địa chỉ và key. Máy này
  không cần tải PyTorch hay trọng số.
  - Địa chỉ ngoài mạng nội bộ bắt buộc dùng https.
- **Tự học tách theo người:** mỗi người (theo tên đăng nhập CVAT) có bộ tự học riêng, kể cả khi dùng chung backend.

### Hỗ trợ, sao lưu và đồng bộ

- **Gửi báo lỗi:** tạo một file zip gồm nhật ký và thông tin máy để gửi người hỗ trợ.
  - Token, key, cookie đăng nhập và ảnh được lọc ra trước khi đóng gói.
- **Sao lưu / Khôi phục:** profile, guideline, bài học, dữ liệu tự học và cài đặt của panel.
  - Trước khi khôi phục, app tự sao lưu dữ liệu đang có.
- **Thư mục chung** (OneDrive, Google Drive, ổ mạng): **Gửi lên** / **Lấy về** profile, guideline và bài học.
  - Dữ liệu tự học là của từng người nên không gửi.
  - Không bao giờ xoá file.
  - File hai bên cùng sửa khác nhau: giữ bản của bạn, bản của nhóm lưu cạnh với đuôi `.tu-thu-muc-chung`.

### Model riêng của dự án

Tab **Tự học** → **Model riêng của dự án**:
1. **Thu thập từ job này**: lấy ảnh và các nhãn hộp đã có trong job. Làm với nhiều job thì có nhiều dữ liệu hơn.
2. **Huấn luyện**: chạy nền khoảng vài phút đến vài chục phút, trong lúc đó vẫn gán nhãn được.
3. Xem bảng *Đúng / Tìm được* của từng class trên ảnh giữ lại để kiểm.
   - Class đạt chuẩn (≥ 30 ví dụ, đúng ≥ 70%, tìm được ≥ 60%) được đánh dấu ✓.
4. Tick **Dùng model riêng cho các class đạt chuẩn**: các class đó được gợi ý bằng model riêng (nhanh hơn SAM3
   nhiều lần), class còn lại vẫn do SAM3 làm.

Model dùng Faster R-CNN MobileNetV3 của torchvision (giấy phép BSD). Có trọng số khởi đầu COCO thì học tốt hơn
nhiều: đặt biến `SAM3_DETECTOR_BASE` trỏ tới file trọng số.

### Label Studio

Label Studio → *Settings → Model → Connect Model*:
- URL: `http://<máy chạy backend>:8765/ls`
- Nếu backend có `SAM3_API_KEY`: bật *Basic auth*, user bất kỳ, password là key.

Ảnh dạng `/data/upload/...` cần đặt `LABEL_STUDIO_URL` và `LABEL_STUDIO_API_KEY` cho backend. Nếu Label Studio
chạy ở máy khác thì cũng đặt `SAM3_ALLOWED_HOSTS`. Hỗ trợ `RectangleLabels` và `PolygonLabels`.

### macOS / Linux

Chưa có bộ cài. Chạy từ mã nguồn:

```
pip install -r requirements.txt pywebview pystray keyring
python -m desktop
```

PyTorch và trọng số SAM 3 cài như bản cài tay (README). App tự nhận ra đang chạy từ mã nguồn và dùng các thư viện có sẵn.

### Ký số bộ cài

Bước build tự ký `SAM3 Assist.exe` và bộ cài khi repo có hai secret `WINDOWS_CERT_PFX` (file .pfx mã hoá base64)
và `WINDOWS_CERT_PASSWORD`. Có chứng chỉ thì Windows không còn cảnh báo SmartScreen (với chứng chỉ OV, cảnh
báo giảm dần khi có người tải). Hai cách có chứng chỉ:
- đăng ký **SignPath Foundation** (miễn phí cho dự án mã nguồn mở, cần duyệt);
- mua chứng chỉ code signing.

## Dữ liệu nằm ở đâu

`AppData\Local\SAM3CvatAssist\` (giữ nguyên khi cập nhật app):

| Thư mục | Nội dung |
|---|---|
| `packages\` | PyTorch đã tải |
| `models\` | trọng số SAM 3 (kèm `SAM_LICENSE.txt`), ViTPose, model khuôn mặt |
| `profiles\`, `guideline_library\` | profile, guideline, bài học, dữ liệu tự học (`learning\`) |
| `store\` | cài đặt của bảng trong CVAT |
| `webview\` | đăng nhập CVAT của cửa sổ app |
| `logs\` | nhật ký app và backend |

Khi cập nhật app: profile và guideline có sẵn được cập nhật, trừ file bạn đã sửa. PyTorch và trọng số không phải tải lại.

Gỡ app: **Settings → Apps → SAM3 Label Assist → Uninstall**. Bộ gỡ hỏi riêng có xoá model đã tải (khoảng 9 GB) và
có xoá dữ liệu của bạn không. Mặc định là giữ.

## An toàn

- **Chỉ tải thứ đã kiểm được.** Mọi file tải khi cài đều có mã băm biết trước. File sai mã băm bị xoá, không bao giờ được dùng:
  - PyTorch: đúng file wheel, sha256 và dung lượng được ghim lúc build (`desktop/manifest.lock.json`).
  - Trọng số trên Hugging Face: ghim theo commit, mỗi file kiểm theo mã băm Hugging Face công bố.
  - Chỉ tải qua HTTPS.
- **Chỉ tải file cần dùng.** Từ `facebook/sam3` chỉ lấy cấu hình, tokenizer và `.safetensors`; bỏ checkpoint `.pt`
  trùng lặp và ảnh minh hoạ. File cài PyTorch bị xoá ngay sau khi cài xong.
- **Bộ cài không chứa gì chưa kiểm.**
  - Python lấy bản nhúng của python.org, kiểm chữ ký Sigstore lúc build.
  - Mọi thư viện cài với `--require-hashes`.
  - Máy build tải thật bản PyTorch CUDA để kiểm mã băm và kiểm có hỗ trợ GPU RTX 20xx (`sm_75`).
- **Token Hugging Face:**
  - Token chỉ được gửi tới `huggingface.co`. Khi Hugging Face chuyển sang máy chủ tải (CDN), token bị bỏ.
  - Mặc định token không được lưu lại. Nếu chọn "Nhớ token", nó được cất trong Windows Credential Manager, không ghi ra file.
- **Backend khoá trong máy:**
  - Chỉ nghe trên `127.0.0.1`, chạy hoàn toàn offline (không tự tải gì lúc chạy).
  - Mỗi lần chạy có một API key ngẫu nhiên. Key chỉ nằm trong app: trang web và chương trình khác không gọi được backend.
- **Cửa sổ CVAT:**
  - Bảng của tool chỉ được tiêm vào các địa chỉ trong danh sách của bạn.
  - Trang khác mở trong cửa sổ đó (link ngoài) không gọi được app.
  - App cảnh báo khi bạn thêm địa chỉ `http` không mã hoá trên mạng ngoài.
- **Không cần quyền admin:** app cài và chạy với quyền người dùng thường.

## Giao diện (từ bản 0.8.3)

- **Logo và taskbar:** app có logo riêng (chữ S nét polyline). Taskbar hiện đúng icon và tên SAM3 Label Assist.
- **Màn hình chờ:** hiện có logo trong vài giây lúc Python khởi động.
- **Cửa sổ cài đặt và bảng điều khiển:** cùng một kiểu.
  - Cột trái là các bước (lúc cài) hoặc các trang (lúc dùng); thanh nút ở dưới.
  - Sáng, tối hoặc theo Windows (chọn ở Cài đặt, từ 0.9.0).
- **Đổi logo:** sửa `desktop/ui/logo.svg` và `logo-small.svg` (bản cỡ nhỏ), rồi chạy `python scripts/make_icons.py`.

## Ngôn ngữ

Chọn ngôn ngữ ở bộ cài (lần đầu), ở trang Cài đặt của bảng điều khiển, hoặc ở Cài đặt của panel (nút bánh răng). Một lựa chọn chung cho
panel, bảng điều khiển, khay hệ thống, thông báo và "Có gì mới". Nhật ký luôn bằng tiếng Anh.

## Khi có lỗi

- Mỗi lỗi kèm một mã cố định, ví dụ `(E_GPU_OOM)`. Danh sách mã hay gặp ở README, mục "Xử lý sự cố".

- **"WebView2" khi mở app:** máy thiếu Microsoft Edge WebView2 Runtime. Windows 11 có sẵn. Windows 10 tải bản
  *Evergreen Bootstrapper* tại <https://developer.microsoft.com/microsoft-edge/webview2/>.
- **Backend lỗi:** trang **Tổng quan** hiện nguyên nhân và cách sửa (ví dụ thiếu Microsoft Visual C++ thì có nút tải).
  Xem nhật ký ở trang **Hỗ trợ**. Lỗi thiếu VRAM thì đóng bớt ứng dụng dùng GPU.
- **Đổi tên ở 0.9.1 (SAM3 CVAT Assist → SAM3 Label Assist):** cập nhật từ bản cũ giữ nguyên dữ liệu, token đã lưu,
  đăng nhập CVAT và chế độ tự chạy cùng Windows. Lối tắt tên cũ được thay bằng tên mới. App vẫn nằm ở thư mục cài cũ.
- **Không cần `Set-ExecutionPolicy` hay `run.ps1`:** bản cài bật Python đi kèm trực tiếp, không qua PowerShell.
- **Token "chưa được duyệt":** đợi email duyệt của Meta, rồi vào **Thành phần → Tải**.
- Nhật ký đầy đủ: `AppData\Local\SAM3CvatAssist\logs\`.

## Cách bộ cài được dựng (cho người phát triển)

`.github/workflows/windows-installer.yml` chạy trên máy Windows của GitHub:

1. Chạy toàn bộ test (Linux) và test desktop (Windows).
2. `installer/build.py`:
   - Python bản nhúng (kiểm Sigstore) và thư viện nhỏ (`--require-hashes`);
   - chọn bản PyTorch CUDA mới nhất có `sm_75`;
   - ghi `manifest.lock.json`.
3. Biên dịch `SAM3 Assist.exe` (`installer/Launcher.cs`), đóng gói bằng Inno Setup (`installer/sam3-assist.iss`).
4. Cài im lặng, chạy thử và gỡ trên máy build.
5. Push tag `v<bản>`: đăng bộ cài, manifest và mã SHA-256 lên Releases. Ghi chú lấy từ mục `## <bản>` trong
   `docs/CHANGELOG.md`; thiếu mục đó thì bước đăng báo lỗi.

Phát hành bản mới: sửa `__version__` trong `backend/__init__.py` (và `VERSION` trong userscript), ghi
`docs/CHANGELOG.md` (mục `## <bản>`), rồi `git tag v<bản> && git push origin v<bản>`.
