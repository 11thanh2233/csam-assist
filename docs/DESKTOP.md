# Bản desktop cho Windows (`SAM3-CVAT-Assist-Setup-<bản>.exe`)

Cài bằng một file, mở bằng một biểu tượng. Không cần Python, PowerShell hay Tampermonkey.

## Cài đặt

1. Tải `SAM3-CVAT-Assist-Setup-<bản>.exe` ở mục **Releases** của repo
   (bản thử mỗi lần cập nhật code: mục **Actions** → lần chạy mới nhất → **Artifacts**).
2. Chạy file. Windows có thể hiện **"Windows protected your PC"** vì file chưa ký số. Bấm **More info → Run anyway**.
   Mỗi bản phát hành có ghi mã SHA-256 của bộ cài trong Release, dùng để kiểm file tải về đúng là bản đó:
   `Get-FileHash .\SAM3-CVAT-Assist-Setup-0.6.0.exe` trong PowerShell.
3. Bộ cài chép app vào `AppData\Local\Programs\SAM3 CVAT Assist` (không cần quyền admin), rồi mở **trình tải
   thành phần**:
   1. **Kiểm tra máy:** card NVIDIA, VRAM, driver. Không có card NVIDIA thì cài PyTorch bản CPU (nhỏ hơn, nhưng rất chậm).
   2. **Giấy phép SAM:** đọc và đồng ý.
   3. **Token Hugging Face:** dán token loại *Read*. App kiểm tra ngay token có hợp lệ và đã được Meta duyệt vào
      `facebook/sam3` chưa. Meta chưa duyệt? Bấm **Cài trước, nhập token sau**: phần còn lại vẫn cài, SAM 3 tải sau
      trong app.
   4. **Chọn thành phần** và xem dung lượng: PyTorch (bắt buộc), SAM 3 (bắt buộc), skeleton người, skeleton mặt.
   5. **Tải và cài:** có thanh tiến trình. Rớt mạng thì bấm **Thử lại**, app tải tiếp từ chỗ dừng.

Dung lượng (máy có card NVIDIA): bộ cài khoảng 150 MB; tải thêm khoảng 6 GB; sau khi cài chiếm khoảng 9–10 GB,
lúc đang cài cần trống khoảng 12 GB.

## Dùng

Mở **SAM3 CVAT Assist** từ Desktop hoặc Start Menu. Bảng điều khiển có:

- **Mở CVAT:** danh sách địa chỉ CVAT (có sẵn `cvat.edufun.ai` và `localhost:8080`, thêm được địa chỉ khác).
  Bấm **Mở**: cửa sổ CVAT mở ra, bảng của tool tự hiện như khi dùng Tampermonkey. Đăng nhập CVAT ngay trong cửa sổ đó;
  app nhớ đăng nhập.
- **Backend:** tự bật khi mở app. Có nút Bật/Tắt/Khởi động lại và Nhật ký.
- **Thành phần:** đã cài gì, bản nào; tải thêm hoặc gỡ từng phần.
- **Cài đặt và dữ liệu:** tự chạy nền khi bật Windows, quên token, mở các thư mục dữ liệu.

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

Gỡ app: **Settings → Apps → SAM3 CVAT Assist → Uninstall**. Bộ gỡ hỏi riêng có xoá model đã tải (khoảng 9 GB) và
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

## Khi có lỗi

- **"WebView2" khi mở app:** máy thiếu Microsoft Edge WebView2 Runtime. Windows 11 có sẵn. Windows 10 tải bản
  *Evergreen Bootstrapper* tại <https://developer.microsoft.com/microsoft-edge/webview2/>.
- **Backend lỗi:** bảng điều khiển → **Backend → Nhật ký**. Lỗi thiếu VRAM thì đóng bớt ứng dụng dùng GPU.
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
