# Nhật ký thay đổi

Mỗi bản một mục `## <bản>`. App hiện các mục này trong khung "Có gì mới" sau khi cập nhật, và bước build lấy đúng
mục của bản đang phát hành làm ghi chú trên GitHub Releases.

## 0.9.6 (2026-11-24)

Chi tiết và kết quả test ở `docs/CHECKLIST_0.9.6.md`. Bản này làm cho job cuboid 3D dùng được thật, backend chạy lâu
không chậm dần, theo dõi không còn đứt vì hết giờ, occluded / outside theo % thân bị che.

- **Mức cấu hình** (Cài đặt panel, như chỉnh đồ hoạ trong game): Tự động / Nhẹ / Cân bằng / Chất lượng / Tối đa.
  Tự động chọn theo VRAM của GPU. Mức quyết định số prompt mỗi label, độ dày khi theo dõi, model phụ nào được dùng.
- **Backend không chậm dần:** thiếu VRAM thì nhả model phụ lâu không dùng trước khi nạp model mới; sau lượt nặng thì
  trả bộ nhớ thừa; rảnh thì dọn theo mức cấu hình. Cài đặt panel hiện VRAM đang dùng và model đang nạp.
- **Theo dõi không đứt vì hết giờ:** kết quả từng frame được lưu; dừng hay lỗi thì lần sau chạy tiếp từ chỗ dở.
  Một lượt quá 5 phút thì backend được báo dừng lượt đó, panel thử lại từng frame.
- **occluded / outside theo % diện tích toàn thân bị che**, đổi theo từng frame:
  - ngưỡng lấy từ guideline (vd "che hơn 35% thì occluded, trên 60% thì outside"), không có thì mặc định 35% / 60%;
  - sửa ngay ở mục "Ngưỡng che khuất" của tab Cả job (nhớ theo task);
  - có trễ giữa các trạng thái, một frame lẻ không làm đổi trạng thái;
  - người hiện lại sau outside vẫn giữ ID cũ (trước đây outside kéo tới hết video);
  - frame model bỏ sót người: SAM3 tìm lại đúng chỗ dự đoán; thấy thì tính % bị che, không thấy mới outside.
- **Model phụ mới** (tuỳ chọn, cài ở mục Thành phần của app):
  - nhận lại người DINOv2-small: nối ID theo ngoại hình, ít nhầm giữa hai người mặc giống nhau;
  - độ sâu Depth Anything V2 Small: biết ai đứng trước, chỉ người đứng sau mới bị tính là bị che;
  - pose toàn thân RTMW (133 điểm, chạy CPU): skeleton có điểm tay / bàn chân; điền thuộc tính tay trái / phải cho
    hộp bàn tay.
- **Job cuboid 3D làm lại:**
  - chiều cao phải khớp bảng cỡ; dài / rộng được nhỏ hơn (vật bị che) nhưng không lớn hơn; cụm không khớp class nào
    thì bỏ, không gán bừa;
  - chỉ thấy đuôi / một phần vật: xoay đúng hướng, kéo cỡ theo bảng, dời tâm ra phía khuất;
  - phạm vi theo class (xe ≤ 50 m, người / xe hai bánh ≤ 40 m, rào chắn ≤ 30 m, hoặc theo guideline);
  - số điểm tối thiểu theo khoảng cách; mặt đất theo từng ô 8 m; bỏ cụm lơ lửng (biển, tán cây); tách dãy rào chắn;
  - đối chiếu ảnh camera đi kèm (CAM_FRONT, CAM_BACK…): tự dò hướng camera, SAM3 xác nhận vật, thấy thùng rác / nhà
    vệ sinh di động thì bỏ tick;
  - nhớ vật bạn bỏ khi ghi, lần sau bỏ tick sẵn vật cùng kiểu;
  - bản đồ nhỏ nhìn từ trên, đề xuất nhóm theo class, xếp theo độ tin, đề xuất yếu ẩn sẵn;
  - "Kiểm tra dữ liệu job 3D" và "Tự chỉnh theo cuboid bạn đã đánh";
  - tab Label của job 3D: sửa bảng cỡ, phạm vi, số điểm, prompt camera cho từng label.

## 0.9.5 (2026-11-24)

Chi tiết và kết quả test ở `docs/CHECKLIST_0.9.5.md`. Hướng chung của bản này: tool thích nghi theo guideline và
cách BẠN chốt nhãn của từng project, không viết luật riêng cho từng label.

- **Claude xem ảnh** (cần Anthropic API key trong Cài đặt của app; tắt được bằng ô "Claude xem ảnh" ở tab Gợi ý):
  - mỗi lần gợi ý, tool gửi frame đã đánh số từng đề xuất, cộng ảnh phóng to cho vật nhỏ, kèm guideline, label,
    thuộc tính và các lần bạn đã sửa;
  - Claude điền thuộc tính và tag theo đúng giá trị của task;
  - xét `occluded` / `outside` theo chính vật, và từng bộ phận của skeleton (kính râm, mũ, ria che miệng…);
  - đề nghị bỏ đề xuất guideline không gán (người trong ảnh chụp, thấy quá ít);
  - chỉ ra vật SAM3 bỏ sót để SAM3 vẽ lại;
  - có giới hạn số lần mỗi ngày (mặc định 500, đổi trong Cài đặt của app).
- **Trí nhớ của project** (tab Tự học):
  - tool nhớ giá trị bạn chốt mỗi lần bấm Ghi, so với giá trị nó gợi ý;
  - học quy ước tên file (vd `_MVL` là camera gương trái) và giữ tag theo chuỗi ảnh;
  - Claude tóm các lần sửa thành "bài học" cho project; bạn cũng tự viết được luật, xoá được luật sai.
- **Chọn thuộc tính ngay trên panel:** mỗi đề xuất có ô chọn cho từng thuộc tính (giá trị tool / Claude đã chọn
  sẵn, ô vàng là chỗ cần bạn chọn); bấm `occluded` / `visible` để đổi. Không phải sửa trong CVAT sau khi ghi.
- **occluded theo chính vật:** tool không còn tự đặt `occluded` chỉ vì hai khung chồng nhau, mà gắn cờ để xem
  (hoặc Claude quyết định).
- **Skeleton mặt:** kính râm che mắt không còn làm mất cả mũi và môi. Luật "thấy dưới ngưỡng thì Outside cả
  skeleton" chỉ áp khi guideline ghi ngưỡng cho chính skeleton đó. Tool tìm thêm mũ và ria làm vật che.
- **Skeleton người:** bỏ tick sẵn người thấy quá ít điểm và người nằm trong khung ảnh, poster, màn hình, gương.
- **Polygon / polyline theo guideline:** khi tạo Profile cho project, tool đọc định nghĩa để chọn cách dựng hình:
  - gộp các mảnh thành một vùng (crosswalk là một polygon bao các vạch);
  - vùng mặt đường liền với xe (drivable tự ra, có thuộc tính làn đang đi / làn bên cạnh);
  - mép vùng mặt đường làm road_curb;
  - vạch nằm trong vùng crosswalk không thành lane.
- **Tag:** nhận ra ảnh fisheye khi chỉ hai góc tối; không biết hướng thì để bạn chọn thay vì ghi `front`.
- **Theo dõi nhanh hơn, giữ độ chính xác:**
  - SAM3 chạy bf16 / fp16 trên GPU, VRAM giảm khoảng một nửa;
  - chế độ "Thông minh" chạy SAM3 trên keyframe, kiểm tra giữa mỗi khoảng, chỗ nội suy lệch thì chạy từng frame;
  - chọn "Mọi frame" để chạy như cũ.

## 0.9.4 (2026-11-23)

Chi tiết và kết quả test ở `docs/CHECKLIST_0.9.4.md`.

- **Profile cho project** (tab Guideline, mục đầu tiên): tool dựng cách gợi ý riêng cho project từ chính label của task,
  không cần file taxonomy, không cần có label trước:
  - một job gộp được nhiều loại: box, polygon, polyline, mask, **skeleton** (thân 17 điểm, mặt 13 điểm…), **tag ảnh**;
  - skeleton ghép từng điểm theo tên điểm (vd `left_eye_outer`, `mouth_left`), theo quy ước trái/phải của guideline;
  - thuộc tính tự điền khi đo được từ ảnh (chạm mép, bị che, vùng rìa fisheye, màu đèn), còn lại để bạn chọn;
  - kích thước tối thiểu đọc từ guideline (vd người dưới 60 px không gợi ý);
  - **Thử trên frame này** trước khi **Lưu cho project**; mọi job của project tự dùng.
- **Tag ảnh** (camera, timeofday, weather, modality…): tool đoán giá trị đo được (ảnh hồng ngoại, độ sáng, fisheye,
  camera bị che), đánh dấu giá trị bạn phải chọn.
- **Cuboid 3D trên job CVAT 3D (LiDAR):** dựng cuboid từ point cloud (tách mặt đường, gom cụm, đoán class theo bảng
  kích thước của guideline), dựng track cả job với kích thước khoá, yaw theo hướng đi, nội suy khi bị che tối đa 6 frame.
  Không cần cài thêm gì.
- **Theo dõi (tracking) tốt hơn:**
  - **chọn người cần theo dõi** (P1, P2…) ở frame họ rõ nhất: tool chỉ theo dõi những người đó, cả trước và sau;
  - **vùng bỏ qua**: khoanh gương, kính, biển quảng cáo, màn hình;
  - người đứng yên suốt job (poster, phản chiếu) bị đánh cờ và bỏ chọn sẵn;
  - nối khoảng model bỏ sót, đặt `occluded` / `outside` theo khoảng khuất và mép ảnh;
  - SAM3 Video: hết `E_BACKEND_TIMEOUT` với job dài, có nút dừng, dọn mask nhiễu;
  - "Dữ liệu thông minh" trên job video có nút **Theo dõi cả job (ra track)**.
- **Soạn guideline nháp:**
  - không cần label trước, tool tự tính số ảnh mẫu;
  - câu hỏi có tiến độ, bỏ qua được, sửa lại được, xem trước câu trả lời sẽ đổi gì trong bản nháp;
  - vật có trong mục đích bạn ghi thì mặc định **Thêm class**; hai tên cùng một vật (`person` / `pedestrian`) hỏi một lần.
- **Nạp guideline** đọc được: skeleton theo mục kèm bảng tên điểm, dòng "Tag ảnh" thành từng tag, cột thuộc tính, cột
  "Dạng CVAT", checklist dạng ô tick.
- **Tự học:** học cả track, điểm skeleton hay bị kéo lệch, thuộc tính tự điền bạn hay đổi.
  **Model riêng** không còn ra 0%: thêm thành phần **Trọng số khởi đầu cho model riêng**, huấn luyện đủ số bước dù ít
  ảnh, giải thích lý do khi class chưa dùng được; học được cả polygon và mask.
- **Panel:**
  - kéo mép trái / mép dưới để đổi cỡ, hoặc chọn Gọn / Vừa / Rộng;
  - cờ thành chip ngắn, cờ cùng loại gom một chip có số đếm; đoạn giải thích dài gập lại;
  - danh sách "Nên làm trước" gom frame cùng lý do; track không chọn ẩn bớt;
  - profile chọn tay nhớ theo task; thanh trạng thái không còn bị cắt chữ.
- **App và bộ cài:**
  - trang **Hỗ trợ** và bước 1 của trình cài kiểm tra máy: Windows, ổ đĩa, RAM, Visual C++, WebView2, WSL2, Docker;
  - bộ cài tự cài WebView2 khi máy thiếu, có tuỳ chọn cài WSL2 (cho CVAT local);
  - GPU lỗi CUDA (`illegal memory access`): backend tự bật lại khi bật "Tự khởi động lại backend".
- **Sửa:**
  - nút chép nhật ký vẫn báo "Không chép được": chép thẳng trong trình duyệt của app, dự phòng bằng `clip.exe`;
  - chuyển job trong CVAT có thể giữ khoảng frame của job trước (nút "Quét 13 ảnh" dù đặt 50);
  - bỏ dòng thông báo `kernels library is not installed` lặp lại trong nhật ký.

## 0.9.3 (2026-11-22)

Chi tiết và kết quả test ở `docs/CHECKLIST_0.9.3.md`.

- **AI soạn guideline bật được ngay trong app** (trước chỉ bật được bằng biến môi trường, nên panel luôn báo "Backend
  chưa cấu hình LLM"):
  - **Cài đặt → AI soạn guideline**: chọn Claude (Anthropic) hoặc máy chủ tương thích OpenAI (Ollama, LM Studio…);
  - API key lưu trong Windows Credential Manager, không ghi ra file và không có trong gói báo lỗi;
  - nút **Kiểm tra** gửi một câu ngắn (không có ảnh) để chắc key và địa chỉ đúng;
  - panel chỉ đường tới mục này khi AI chưa bật. Ảnh mẫu vẫn chỉ gửi đi khi bạn tick đồng ý.
- **CVAT trên máy này (localhost):** bấm **Mở** mà CVAT chưa chạy thì app không còn mở trang lỗi kết nối, mà:
  - kiểm tra từng bước: WSL2, Docker Desktop đã cài, Docker đang chạy, container của CVAT, CVAT trả lời;
  - nút **Khởi động CVAT**: mở Docker Desktop, bật các container CVAT (hoặc `docker compose up -d` trong thư mục CVAT
    bạn chọn), đợi CVAT sẵn sàng rồi mở trang;
  - thiếu WSL2, Docker hay CVAT thì có nút mở hướng dẫn cài chính thức;
  - áp dụng cả khi app mở thẳng CVAT lúc khởi động, nút **Mở tiếp** job và mục **Mở CVAT** ở khay hệ thống.
- **Sửa:** chép chữ (nút chép nhật ký, chép từ panel) báo lỗi `ctypes.ArgumentError: OverflowError: int too long to
  convert` trên Windows 64-bit.

## 0.9.2 (2026-11-20)

Chi tiết và kết quả test ở `docs/CHECKLIST_0.9.2.md`.

- **Panel trong CVAT làm lại cho thoáng, dễ nhìn** (theo bản mẫu đã duyệt):
  - 6 tab có icon; Cài đặt chuyển thành nút bánh răng ở góc panel;
  - dòng Job / Frame và ô profile gọn trên một hàng;
  - **thanh Ghi luôn nằm dưới cùng**: nút lớn "Ghi N, sang frame tiếp", hàng dưới có Chỉ ghi, Hoàn tác, Bỏ đề xuất;
  - các tab dài (Cả job, Kiểm tra, Guideline, Tự học) chia thành mục bấm để mở hoặc gập, panel nhớ mục bạn mở;
  - Cài đặt chia nhóm (Kết nối, Khi duyệt, Khi ghi vào CVAT, Hiển thị) với công tắc; tuỳ chọn ít dùng ở mục Nâng cao.
- **Chép và bôi đen chữ được trong app:**
  - nút chép nhật ký hoạt động (trước báo "Không chép được");
  - bôi đen được nhật ký, key, giấy phép, và cả chữ trong cửa sổ CVAT.
- **Nhật ký mặc định chỉ hiện lần chạy này**, lỗi của các lần trước không lẫn vào. Bấm "Lần chạy này" để xem mọi lần.
- Release chỉ còn một file exe (`SAM3-Label-Assist-Setup-<bản>.exe`). App 0.9.0 trở về trước tải bản mới bằng nút
  "Mở trang Releases" một lần.

## 0.9.1 (2026-11-20)

Chi tiết và kết quả test ở `docs/CHECKLIST_0.9.1.md`.

- **Bộ cài tự cài Microsoft Visual C++** khi máy thiếu (Windows hỏi quyền admin một lần), thay vì chỉ báo lỗi.
- **Kiểm tra nhanh sau khi cài:** bước "Sẵn sàng" tự chạy SAM 3 trên một ảnh mẫu và báo "Mọi thứ hoạt động" hoặc lỗi
  kèm cách sửa. Chạy lại được ở trang Hỗ trợ.
- **Cập nhật an toàn:**
  - trước khi cài bản mới, app tự sao lưu profile, guideline, dữ liệu tự học và cài đặt (giữ 3 bản);
  - bản mới không mở được thì tự quay về bản cũ và báo cho bạn;
  - cài đặt của mọi bản cũ đều đọc được; file cài đặt hỏng được giữ lại, không bị ghi đè.
- **Trang Giới thiệu:** phiên bản, giấy phép của SAM 3 (tóm tắt và toàn văn) và mọi phần mềm, model đi kèm. Mở bằng
  cách bấm số phiên bản ở thanh bên.
- **Thống kê công việc ở Tổng quan:** số nhãn đã ghi hôm nay, số frame, thời gian tiết kiệm ước tính, biểu đồ 7 ngày,
  nút xuất CSV để báo cáo với nhóm.
- **Báo rõ khi panel và backend lệch bản:** nói bên nào cũ hơn và cách cập nhật (Tampermonkey, app, hay máy chia sẻ).
- **Hướng dẫn sử dụng cho người gán nhãn** có ảnh minh hoạ: `docs/HUONG_DAN.md` (mở từ app: Giới thiệu → Hướng dẫn
  sử dụng). Thêm trang quyền riêng tư `docs/PRIVACY.md`.
- Sửa: chữ "null" hiện dưới dòng Job/Frame trong panel.
- Sửa: thống kê trong panel tính ngày theo giờ UTC (ở Việt Nam, việc làm lúc 0h-7h sáng bị tính sang hôm trước).
- **Đổi tên thành SAM3 Label Assist** (trước là SAM3 CVAT Assist), vì tool sẽ hỗ trợ cả các nền tảng gán nhãn khác.
  - Cập nhật từ bản cũ giữ nguyên dữ liệu, token Hugging Face đã lưu, đăng nhập CVAT và chế độ tự chạy cùng Windows.
  - Lối tắt ở Desktop và Start Menu đổi sang tên mới, không để lại lối tắt tên cũ.
  - Bộ cài tên mới là `SAM3-Label-Assist-Setup-<bản>.exe`. Release vẫn kèm bản tên cũ để app 0.9.0 trở về trước tự cập
    nhật được.
  - Người dùng Tampermonkey: script vẫn giữ tên cũ trong Tampermonkey để không bị chạy hai panel cùng lúc.
- Sửa: chữ "vừa xong" ở job gần đây chưa dịch khi dùng tiếng Anh.

## 0.9.0 (2026-11-20)

Chi tiết và kết quả test ở `docs/CHECKLIST_0.9.0.md`.

- **Sửa lỗi bấm Bật backend / Mở CVAT không có phản ứng** (có từ 0.8.0). Lỗi nằm trong app, không phải do máy thiếu gì.
  Bản cài không cần `Set-ExecutionPolicy` hay `run.ps1`.
- **Bảng điều khiển làm lại theo bản mẫu:** sáu trang ở thanh bên (Tổng quan, Trang gán nhãn, Thành phần,
  Dùng chung nhóm, Hỗ trợ, Cài đặt) và bộ icon riêng.
  - Tổng quan: trạng thái backend, card, VRAM, nhiệt độ GPU; mở nhanh trang gán nhãn và job gần đây.
  - Backend lỗi thì nói rõ nguyên nhân (ví dụ thiếu Microsoft Visual C++) và các bước sửa, mỗi bước một nút bấm.
  - Hỗ trợ: tạo gói báo lỗi kèm mô tả, xem nhật ký ngay trong app (lọc chữ, chỉ xem dòng lỗi, chép).
  - Cài đặt: công tắc và nút chọn; thêm giao diện sáng/tối/theo Windows.
- **Backend tự bật lại khi tắt bất ngờ** (tối đa 3 lần trong 10 phút), báo qua khay hệ thống. Tắt được trong Cài đặt.
- **Thao tác nhanh:** Ctrl+K tìm và chạy mọi thao tác, Alt+1…6 chuyển trang. App nhớ trang đang mở.
- **Hộp thoại dễ đóng hơn ở mọi nơi:**
  - bảng điều khiển: Esc, bấm ra ngoài hoặc ✕;
  - panel trong CVAT: hộp thoại riêng thay hộp thoại của trình duyệt (Esc, bấm ra ngoài, Enter để đồng ý).
- **Nút nào bấm cũng có phản hồi:** vòng xoay khi đang chạy, thông báo khi xong. Xoá trang gán nhãn có nút Hoàn tác.
- Trang gán nhãn có ghi nền tảng (hiện là CVAT), chuẩn bị cho các nền tảng gán nhãn khác.

## 0.8.3 (2026-11-20)

Chi tiết và kết quả test ở `docs/CHECKLIST_0.8.3.md`.

- **Logo mới:** chữ S vẽ bằng nét polyline có đỉnh.
  - Dùng ở file exe, bộ cài, lối tắt, khay hệ thống, cửa sổ app, panel trong CVAT, tab trình duyệt của backend.
  - Cỡ nhỏ (16–24 px) dùng nét dày riêng cho dễ nhìn.
- **Taskbar hiện đúng icon và tên SAM3 CVAT Assist**, không còn biểu tượng của Python.
- **Màn hình chờ có logo** hiện ngay khi mở app, tự đóng khi cửa sổ app hiện.
- **Cửa sổ cài đặt làm lại theo bản mẫu:**
  - cột trái xanh đậm có logo và 6 bước; bước xong có dấu ✓, bước đang làm tô đậm;
  - thanh nút Huỷ / Quay lại / Tiếp cố định ở dưới;
  - trang tải có thanh tiến độ tổng, tốc độ, thời gian còn lại, trạng thái từng thành phần, nhật ký thu gọn;
  - trang "Sẵn sàng" ở cuối.
- **Bảng điều khiển** cùng kiểu: cột trái có mục lục nhảy tới từng phần; sáng/tối theo Windows.
- **Bộ cài** có dải màu thương hiệu và logo; lối tắt gom chung một biểu tượng trên taskbar.

## 0.8.2 (2026-11-20)

- **Sửa ô chọn ngôn ngữ của bộ cài:** bản 0.8.1 hiện "English" hai lần, giờ hiện "Tiếng Việt" và "English".
  Windows tiếng Việt tự chọn sẵn Tiếng Việt.

## 0.8.1 (2026-11-20)

Chi tiết từng mục, cách test và lỗi đã sửa ở `docs/CHECKLIST_0.8.1.md`.

- **Giao diện tiếng Anh đầy đủ.** Chọn English ở tab Cài đặt của panel, ở góc trên bảng điều khiển của app, hoặc ngay
  khi mở bộ cài. Một lựa chọn dùng chung cho cả panel, bảng điều khiển, khay hệ thống, thông báo và "Có gì mới".
  - Dịch toàn bộ: panel, bảng điều khiển, bộ cài, thông báo của backend, tên profile, trang duyệt guideline.
  - Bản nháp guideline và câu hỏi gửi LLM viết theo ngôn ngữ đã chọn.
- **Mã lỗi cố định.** Mỗi lỗi kèm một mã kỹ thuật (ví dụ `E_BACKEND_UNREACHABLE`, `E_HF_NOT_APPROVED`, `E_GPU_OOM`),
  giống nhau ở mọi ngôn ngữ, để tra cứu và báo lỗi dễ hơn.
- **Nhật ký luôn bằng tiếng Anh kỹ thuật**, kể cả khi giao diện là tiếng Việt; gói báo lỗi cũng vậy.
- **Bấm điểm dùng SAM 3 Tracker.** Model điểm riêng của SAM 3, nằm sẵn trong trọng số đã tải, không cần tải thêm.
  - Chỉ nạp khi bạn bấm điểm, tự nhả sau 5 phút không dùng.
  - Thiếu VRAM hoặc lỗi thì tự quay về cách cũ.
- **Chuẩn bị ký số bộ cài:** quy trình build hỗ trợ SignPath và chứng chỉ PFX, hướng dẫn ở `docs/SIGNING.md`.
- **Cho người phát triển:** chuỗi tiếng Việt mới trong mã mà chưa có bản dịch thì test báo lỗi
  (`scripts/i18n_extract.py`, `scripts/i18n_build.py`).

## 0.8.0 (2026-11-20)

37 cải tiến; chi tiết từng mục, cách test và lỗi đã sửa ở `docs/CHECKLIST_0.8.0.md`.

- **Gán nhãn nhanh hơn từng frame.**
  - Tool chạy sẵn gợi ý cho frame tiếp theo trong lúc bạn duyệt, sang frame là có ngay.
  - **Alt+Enter**: ghi rồi sang frame tiếp.
  - Duyệt bằng phím (Alt+J/K chọn, Alt+X tick, Alt+Delete bỏ), hoặc giữ Alt rồi bấm lên ảnh.
  - Thanh ngưỡng và nút ẩn class lọc tức thì.
  - Hoàn tác tới 20 lần ghi gần nhất (Alt+Z).
- **Nhãn đúng hơn ngay lần đầu.**
  - Tool kiểm luật QA trước khi ghi.
  - Tự điền truncated/occluded khi label có hai thuộc tính đó.
  - **Bấm điểm** để tách vật.
  - Giới hạn số đỉnh và làm mượt polygon theo profile.
  - Nhắc khi frame này ít vật hơn frame bên cạnh.
- **Việc dài.**
  - Gợi ý cả job tạm dừng / tiếp tục được, kể cả sau khi tắt trình duyệt, kèm thời gian còn lại.
  - Duyệt kết quả cả job dạng lưới ảnh nhỏ.
  - Thông báo khi việc dài xong.
- **App desktop.**
  - Nạp sẵn model và khởi động nóng; tự nhả VRAM khi rảnh (mặc định 15 phút).
  - Mở thẳng CVAT; có danh sách job gần đây.
  - Thông báo Windows.
  - Đồng hồ GPU (VRAM, nhiệt độ) trên panel.
- **Hỗ trợ và nhóm.**
  - Gửi báo lỗi một chạm (không kèm token, key, cookie hay ảnh).
  - Sao lưu / khôi phục; đồng bộ profile, guideline và bài học qua thư mục chung.
  - Một máy GPU phục vụ cả nhóm trong LAN (có key); máy không có GPU dùng backend của máy kia.
  - Chế độ reviewer tô nhãn do tool ghi mà chưa ai sửa.
  - Tự học tách theo từng người.
  - Báo cáo năng suất CSV.
- **Dữ liệu thông minh.**
  - Gợi ý frame nên làm trước (khác lạ, khó, chưa có nhãn).
  - Keyframe theo chuyển động, và gợi ý cả job chỉ trên keyframe.
  - Tìm frame bằng chữ (SAM3).
  - Đo độ đồng thuận giữa hai job.
- **Model riêng của dự án.** Một detector nhỏ học từ nhãn hộp bạn đã duyệt. Chỉ class nào đạt chuẩn mới được dùng, nhanh hơn SAM3 nhiều lần.
- **Nền tảng.**
  - Đầu nối Label Studio (ML backend).
  - Bước ký số bộ cài tự bật khi repo có chứng chỉ.
  - Giao diện tiếng Anh (một phần).
  - Chạy từ mã nguồn trên macOS/Linux.
  - Phím tắt tự đặt, cỡ chữ, tương phản cao.
  - Hướng dẫn lần đầu và thống kê phiên.

## 0.7.0 (2026-11-19)

- **Không tải lại thứ đã có.**
  - Trước khi tải, app quét máy, gồm bộ nhớ đệm Hugging Face của bản cài tay cũ (0.3.x – 0.5.x), thư mục tool cũ
    trong Downloads, Desktop, Documents, và thư mục bạn tự thêm.
  - Trọng số SAM 3, ViTPose và model khuôn mặt đã có thì được dùng lại sau khi kiểm đúng mã băm.
  - Dùng lại bằng liên kết cứng nên không tốn thêm ổ đĩa.
- **Chỉ cập nhật phần cần.**
  - Bước đầu của trình cài liệt kê từng thành phần: đã có và đúng bản, cần cập nhật, hay dùng lại được, kèm dung lượng phải tải.
  - PyTorch được ghim bản: app mới chỉ bắt tải lại khi thật sự cần bản PyTorch khác.
  - Trọng số có bản mới trên Hugging Face thì chỉ tải các file đã đổi.
  - Token Hugging Face chỉ cần khi thật sự phải tải trọng số SAM 3.
- **Tự kiểm và tự cập nhật app.**
  - Mỗi ngày một lần, app hỏi GitHub Releases xem có bản mới không.
  - Có bản mới thì hiện ghi chú và nút Cập nhật: app tải bộ cài, kiểm mã SHA-256, tự cài ở chế độ cập nhật rồi mở lại.
  - PyTorch, trọng số, profile và dữ liệu tự học giữ nguyên.
  - Tắt được trong bảng điều khiển.
- **Khung "Có gì mới"** hiện một lần sau mỗi lần cập nhật, gộp các bản bạn đã bỏ qua.

## 0.6.0 (2026-11-19)

- **Bộ cài Windows một file** (khoảng 136 MB). Không cần Python, PowerShell hay Tampermonkey.
- **Trình tải thành phần ở bước cuối của bộ cài:**
  - kiểm tra máy;
  - giấy phép SAM;
  - kiểm token Hugging Face (đã được Meta duyệt chưa);
  - chỉ tải phần nặng (PyTorch, trọng số SAM 3, model skeleton nếu chọn), có thanh tiến trình và tải tiếp khi rớt mạng.
- **Cửa sổ CVAT riêng** có sẵn bảng của tool. Có danh sách địa chỉ CVAT để thêm hoặc xoá.
- **Bảng điều khiển:**
  - bật/tắt backend, xem nhật ký;
  - quản lý thành phần;
  - chạy nền ở khay hệ thống, tự chạy cùng Windows.
- **An toàn:**
  - mọi file tải về đều kiểm mã băm;
  - token chỉ gửi tới huggingface.co;
  - backend chỉ nghe trong máy, có API key ngẫu nhiên, chạy offline.

## 0.5.0

- **Tự học từ thao tác của người dùng:** giữ, sửa, đổi nhãn, xoá, bỏ tick, loại trừ, vật tự vẽ thêm. Tool tự chỉnh
  ngưỡng và cách vẽ hộp cho từng label.
- **Rà cả job** ở tab Kiểm tra.
- **Theo dõi video bằng SAM 3 Video** (thử nghiệm).
- **Loại trừ trong Tìm tương tự.**

## 0.4.4

- **Rà soát bảo mật backend:** chặn DNS rebinding, bỏ CORS mở.
- **Đọc frame thật của job.**
- **Chống đề xuất trùng.**

## 0.4.0

- **Chạy được trên CVAT local** và các trang CVAT khác.
- **Nạp guideline và `taxonomy.json`** cho task mới.
- **Gợi ý cả job** theo label chọn.
- **Quản lý profile.**
