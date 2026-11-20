# Tương thích giữa các bản

Từ bản 1.0, mỗi bản mới phải đọc được dữ liệu và cài đặt của mọi bản cũ hơn, và cập nhật không được làm mất gì.

## Dữ liệu được giữ khi cập nhật

Nằm ở thư mục dữ liệu `%LOCALAPPDATA%\SAM3CvatAssist` (tên cũ, giữ nguyên để không phải chuyển dữ liệu):

- `settings.json`: cài đặt của app;
- `profiles/`, `guideline_library/`: profile và guideline;
- `store/`: cài đặt của panel, đăng nhập CVAT;
- dữ liệu tự học;
- `packages/`, `models/`: PyTorch và trọng số SAM 3.

Bộ cài chỉ thay thư mục chương trình (`app`, `runtime`), không đụng tới thư mục dữ liệu.

## Quy tắc khi đổi định dạng

1. **Chỉ thêm, không đổi nghĩa.**
   - Mục mới phải có giá trị mặc định.
   - Mục cũ giữ nguyên tên và nghĩa.
   - Cần đổi nghĩa thì tạo mục mới và chuyển dữ liệu một lần, kèm test.
2. **Giữ mục lạ.** File do bản mới hơn ghi (ví dụ sau khi quay về bản cũ) vẫn đọc được; các mục bản cũ không biết được giữ nguyên.
3. **Không ghi đè file hỏng.**
   - `settings.json` hỏng thì được giữ lại thành `settings.broken-<thời điểm>.json` rồi app chạy với cài đặt mặc định.
   - Một mục sai kiểu thì chỉ mục đó về mặc định.
4. **Đánh số định dạng.**
   - `settings.json` có `schema` (hằng `SETTINGS_SCHEMA` trong `desktop/control.py`).
   - Đổi định dạng thì tăng số này và thêm bước chuyển.
5. **Có test cho mọi bản cũ.**
   - `tests/test_v091.py` (`OLD_SETTINGS`) có mẫu cài đặt của từng bản.
   - Phát hành bản mới thì thêm mẫu của bản đó vào.

## Cập nhật an toàn (từ 0.9.1)

- **Sao lưu trước khi cài bản mới.** App tự sao lưu profile, guideline, dữ liệu tự học và cài đặt vào
  `backups\truoc-cap-nhat-<bản cũ>-len-<bản mới>-<thời điểm>.zip` (giữ 3 bản gần nhất). Sao lưu lỗi thì không cập nhật.
- **Giữ bản cũ để quay về.** Script cập nhật đổi tên `app` và `runtime` thành `app.prev` và `runtime.prev`, rồi chạy bộ cài.
  - Bộ cài lỗi, hoặc 120 giây sau bản mới vẫn chưa mở được: trả bản cũ về chỗ, mở lại app và báo
    "Đã quay về bản cũ". App không mời cập nhật lên bản đó nữa, nhưng bạn vẫn tự cập nhật được.
  - Bản mới mở được: xoá bản cũ đã giữ.
- **Nhật ký.** Mỗi lần cập nhật ghi vào `logs\update.log`.

Code: `desktop/safe_update.py`. CI thử thật trên Windows trường hợp bộ cài lỗi phải quay về bản cũ.
