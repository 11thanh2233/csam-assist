"""Thêm bản dịch tiếng Anh cho các chuỗi 0.9.7 (chạy một lần, giữ nguyên chuỗi regex).

Quy trình i18n của repo: chuỗi tiếng Việt trong mã -> backend/i18n/en.json -> scripts/i18n_build.py.
Chuỗi là biểu thức chính quy được giữ NGUYÊN VĂN (không dịch): công cụ dò chuỗi chỉ cần có mặt trong catalog.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "backend" / "i18n" / "en.json"

TRANSLATIONS = {
    # --- backend: runtime / phiên / vision / camera rig / judge_3d ---
    "Job 3D: đã nhả %s để dành VRAM.": "3D job: released %s to free VRAM.",
    "chưa chọn model 2D ONNX trong app desktop — bỏ qua đối chiếu camera":
        "no 2D ONNX model selected in the desktop app — skipping camera cross-check",
    "đổi model": "model changed",
    "Phiên 3D không còn trong bộ nhớ (backend đã khởi động lại, hoặc quá 8 phiên). Hãy tạo phiên mới.":
        "The 3D session is no longer in memory (backend restarted, or more than 8 sessions). Start a new session.",
    "session_id trong đường dẫn và thân request không khớp.": "session_id in the path and in the request body do not match.",
    "Phiên 3D không còn trong bộ nhớ. Hãy tạo phiên mới.": "The 3D session is no longer in memory. Start a new session.",
    "Không tìm thấy phiên.": "Session not found.",
    "frame {0} ảnh camera lỗi: {1}": "frame {0} camera image error: {1}",
    "theo dữ liệu CVAT": "from CVAT data",
    "theo hiệu chỉnh của bạn": "from your calibration",
    "theo bộ cảm biến chuẩn": "from the standard sensor preset",
    "đoán theo tên camera": "guessed from camera names",
    "đoán (một camera)": "guessed (single camera)",
    "căn từ box mẫu": "fitted from sample boxes",
    "Chỉ có một camera và không rõ tên: coi như nhìn thẳng trước (0°, 90°). Nếu khung chiếu lệch, dùng 'Căn chỉnh từ box mẫu'.":
        "Only one camera and its name is unknown: treated as looking straight ahead (0°, 90°). If the projected box "
        "is off, use 'Calibrate from sample boxes'.",
    "Cần ít nhất một camera và một cuboid mẫu để căn chỉnh.": "Calibration needs at least one camera and one sample cuboid.",
    "Chỉ khớp {0} vật: chưa đủ để căn chỉnh (cần ≥ {1}). Vẽ thêm 2-3 cuboid rõ nét ở frame đầu, hoặc chọn frame có nhiều xe.":
        "Only {0} objects matched: not enough to calibrate (need >= {1}). Hand-draw 2-3 clear cuboids on the first "
        "frame, or pick a frame with more vehicles.",
    "Nhận ra {0}/{1} camera theo tên. Camera còn lại bị bỏ qua khi chiếu.":
        "Recognised {0}/{1} cameras by name. The remaining cameras are skipped when projecting.",
    "Claude lỗi sau {0} lần thử: {1}": "Claude failed after {0} attempts: {1}",
    "Claude không chấm: cần người xem": "Claude did not score this one: needs a human look",
    "Claude lỗi: cần người xem": "Claude error: needs a human look",
    "Backend chưa có engine SAM3: bỏ qua bước đối chiếu ảnh camera.":
        "Backend has no SAM3 engine: skipping the camera cross-check.",
    "SAM3 đang không giữ trong VRAM (đã nhả cho job 3D). Đối chiếu ảnh camera cần SAM3 đã nạp sẵn: mở một job 2D dùng SAM3 rồi quay lại, hoặc đặt SAM3_IDLE_UNLOAD_MIN=0.":
        "SAM3 is not held in VRAM (released for the 3D job). The camera cross-check needs SAM3 already loaded: open a "
        "2D job that uses SAM3 and come back, or set SAM3_IDLE_UNLOAD_MIN=0.",
    "chưa dựng geometry": "geometry has not run yet",
    "chưa bật Claude (cần ANTHROPIC_API_KEY hoặc LLM local)":
        "Claude is off (needs ANTHROPIC_API_KEY or a local LLM)",
    "thiếu onnxruntime: {0}": "onnxruntime is missing: {0}",
    "không nạp được model ONNX: {0}": "could not load the ONNX model: {0}",
    "suy luận lỗi: {0}": "inference error: {0}",
    "Ảnh {0}: khu chiếu không có vật 2D nào. Có thể là rác (cột, thùng rác): tool bỏ tick.":
        "Image {0}: no 2D object where the cuboid projects. Possibly noise (pole, trash bin): the tool unticked it.",
    "Ảnh {0}: thấy vật khác class tại chỗ này (IoU {1}) — xem lại class.":
        "Image {0}: a different class is visible here (IoU {1}) — check the class.",
    "Cài cả onnxruntime và onnxruntime-gpu: gỡ bản CPU trước khi dùng GPU (pip uninstall onnxruntime). Provider CUDA có thể không nạp được.":
        "Both onnxruntime and onnxruntime-gpu are installed: remove the CPU build before using the GPU "
        "(pip uninstall onnxruntime). The CUDA provider may fail to load.",
    "chuyển sang job 3D": "switching to a 3D job",
    "Nhả model cho phiên 3D lỗi": "Failed to release models for the 3D session",
    "Nhả SAM3 cho phiên 3D lỗi": "Failed to release SAM3 for the 3D session",
    "onnxruntime không nạp được: {0}": "onnxruntime could not be imported: {0}",
    "Track tới {0} m, vượt phạm vi gán {1} m của class: xem lại các frame cuối.":
        "Track reaches {0} m, beyond the {1} m annotation range of the class: check the last frames.",
    "Dò vật 2D cho job 3D (ONNX)": "2D object detection for 3D jobs (ONNX)",
    "Dò vật 3D LiDAR (CenterPoint/PointPillars ONNX)": "3D LiDAR detection (CenterPoint/PointPillars ONNX)",
    "Model '{0}' chưa rõ giấy phép: hỏi mentor trước khi dùng cho dữ liệu thật.":
        "Model '{0}' has no clear license: ask your mentor before using it on real data.",
    "chưa rõ": "unknown",
    "Dò vật 2D (RT-DETR-L, COCO 80 class)": "2D detection (RT-DETR-L, COCO 80 classes)",
    "Nhanh, ổn định trên GPU; đủ cho xe/người/xe hai bánh.": "Fast and stable on GPU; enough for vehicles, people and two-wheelers.",
    "Dò vật 2D (D-FINE, COCO 80 class)": "2D detection (D-FINE, COCO 80 classes)",
    "Chính xác hơn ở vật nhỏ, chậm hơn RT-DETR một chút.": "More accurate on small objects, slightly slower than RT-DETR.",
    "Dò vật 3D LiDAR (PointPillars, KITTI 3 class)": "3D LiDAR detection (PointPillars, KITTI 3 classes)",
    "Nhẹ, chạy tốt trên GPU 4 GB; cần hiệu chỉnh voxel theo dataset.":
        "Light, runs well on a 4 GB GPU; voxel settings must be tuned per dataset.",
    "Dò vật 3D LiDAR (CenterPoint, nuScenes/KITTI)": "3D LiDAR detection (CenterPoint, nuScenes/KITTI)",
    "Chính xác hơn PointPillars, cần nhiều VRAM hơn.": "More accurate than PointPillars, uses more VRAM.",
    "loại model không hỗ trợ: {0}": "unsupported model kind: {0}",
    "Không thấy file ONNX: {0}": "ONNX file not found: {0}",
    # --- userscript: job 3D 3 bước ---
    "Ít điểm LiDAR — kích thước/hướng có thể sai": "Few LiDAR points — size/heading may be wrong",
    "Giống vật bạn đã bỏ trước đây": "Looks like an object you dropped before",
    "Ảnh {0}: khu chiếu không có vật — nghi rác": "Image {0}: nothing where the cuboid projects — suspected noise",
    "tự dò {0}°": "auto-detected {0}°",
    "đã lưu {0}°": "saved {0}°",
    "khớp hiệu chỉnh": "matches calibration",
    "không có ảnh camera": "no camera images",
    "Bước 1 · Thiết lập nhanh": "Step 1 · Quick setup",
    "Claude AI: bật": "Claude AI: on",
    "Claude AI: tắt": "Claude AI: off",
    "Dùng Claude AI hỗ trợ": "Use Claude AI assistance",
    "Tự nhận diện và kiểm duyệt lại toàn bộ nhãn theo guideline.":
        "Recognises objects and re-audits every label against the guideline.",
    "Tạm dừng / Hủy": "Pause / Cancel",
    "Bước 2 · Chạy dựng & Kiểm duyệt": "Step 2 · Build & audit",
    "Chạy theo lô có lưu tiến độ: dừng giữa chừng vẫn giữ phần đã chạy":
        "Runs in batches and saves progress: stopping midway keeps what has run",
    "Bắt đầu dựng Cuboid 3D ({0} frame)": "Start building 3D cuboids ({0} frames)",
    "Bước 3 · Duyệt & Ghi": "Step 3 · Review & write",
    "Cần xem": "Needs review",
    "Tin cậy cao": "High confidence",
    "Nghi rác": "Suspected noise",
    "Chọn hết Tin cậy cao ({0})": "Select all high confidence ({0})",
    "Bỏ chọn Nghi rác ({0})": "Deselect suspected noise ({0})",
    'Chưa có kết quả: bấm "Bắt đầu dựng Cuboid 3D" ở bước 2.':
        'No result yet: press "Start building 3D cuboids" in step 2.',
    "Pha 1/3: Quét LiDAR & dựng Track ({0}/{1} frame)": "Phase 1/3: LiDAR scan & track building ({0}/{1} frames)",
    "Pha 2/3: Đối chiếu camera ({0}/{1} track)": "Phase 2/3: camera cross-check ({0}/{1} tracks)",
    "Pha 3/3: Claude nhận diện & kiểm duyệt ({0}/{1} track)": "Phase 3/3: Claude recognition & audit ({0}/{1} tracks)",
    "Đang chạy…": "Running…",
    "Frame mỗi lô:": "Frames per batch:",
    "· Timeout mỗi lô (giây):": "· Timeout per batch (seconds):",
    "Tự chỉnh theo cuboid đã đánh": "Self-tune from your cuboids",
    "Rổ này trống.": "This bucket is empty.",
    "Tới frame rõ nhất": "Go to clearest frame",
    "Nhảy tới keyframe dày điểm nhất": "Jump to the keyframe with the most points",
    "Ghi {0} track đã chọn vào CVAT": "Write {0} selected tracks to CVAT",
    "Đang dựng cuboid 3D theo lô…": "Building 3D cuboids in batches…",
    "Claude xong: {0} cần xem, {1} nghi rác": "Claude finished: {0} to review, {1} suspected noise",
    "Job {0}: {1} cần xem, {2} nghi rác, {3} tin cậy cao.": "Job {0}: {1} to review, {2} suspected noise, {3} high confidence.",
    "{0} track: {1} cần xem · {2} nghi rác · {3} tin cậy cao. Duyệt ở bước 3 rồi ghi.":
        "{0} tracks: {1} to review · {2} suspected noise · {3} high confidence. Review in step 3, then write.",
    "Lỗi giữa chừng — kết quả đã chạy vẫn giữ. {0}":
        "Error midway — the batches already run are kept. {0}",
    "Đang dừng sau lô hiện tại… (kết quả đã chạy vẫn giữ)":
        "Stopping after the current batch… (batches already run are kept)",
    "Sang bước 2 (cả job)": "Go to step 2 (whole job)",
    "{0} track · {1} cần xem": "{0} tracks · {1} to review",
    "Khoảng frame {0}–{1}": "Frame range {0}–{1}",
    # --- app desktop: model ONNX của job 3D (chọn file, không tải qua bộ cài) ---
    "tuỳ chọn cho job 3D: chọn file .onnx có sẵn ở trang Thành phần":
        "optional for 3D jobs: choose an existing .onnx file on the Components page",
    "dùng file {0}": "using file {0}",
    "{0}: không tải qua bộ cài, chọn file .onnx ở trang Thành phần.":
        "{0}: not downloaded by the installer, choose an .onnx file on the Components page.",
    "Đã chọn {0}.{1}": "Selected {0}.{1}",
    "Loại model không hỗ trợ.": "Unsupported model type.",
    "Cần chọn file .onnx.": "Please choose an .onnx file.",
    "tuỳ chọn, chưa chọn file": "optional, no file chosen",
    "job 3D: đối chiếu ảnh camera": "3D jobs: camera image cross-check",
    "job 3D: dò vật trong point cloud": "3D jobs: detect objects in the point cloud",
    "Tuỳ chọn · job 3D: đối chiếu ảnh camera (RT-DETR / D-FINE, Apache-2.0). Chọn file .onnx có sẵn":
        "Optional · 3D jobs: camera image cross-check (RT-DETR / D-FINE, Apache-2.0). Choose an existing .onnx file",
    "Tuỳ chọn · job 3D: dò vật trong point cloud (PointPillars / CenterPoint, Apache-2.0). Chọn file .onnx có sẵn":
        "Optional · 3D jobs: detect objects in the point cloud (PointPillars / CenterPoint, Apache-2.0). Choose an existing .onnx file",
    "<span class=\"tag\">Chưa chọn</span>": "<span class=\"tag\">Not chosen</span>",
    "Đổi file": "Change file",
    "Chọn file .onnx": "Choose .onnx file",
    " <button class=\"quiet\" onclick=\"removeComp('{0}')\">Bỏ chọn</button>":
        " <button class=\"quiet\" onclick=\"removeComp('{0}')\">Unselect</button>",
    # --- biểu thức chính quy: giữ nguyên văn, chỉ để công cụ không báo thiếu ---
    "(?:occlu\\w*|bị che|che khuất)[^.\\n]{0,60}?(?:tối đa|max|không quá|≤|<=)[^\\d\\n]{0,12}(\\d{1,2})\\b":
        "(?:occlu\\w*|bị che|che khuất)[^.\\n]{0,60}?(?:tối đa|max|không quá|≤|<=)[^\\d\\n]{0,12}(\\d{1,2})\\b",
    "(?:±|\\+/-|sai lệch|tolerance)[^\\d\\n]{0,12}(\\d{1,2}(?:[.,]\\d+)?)\\s*%":
        "(?:±|\\+/-|sai lệch|tolerance)[^\\d\\n]{0,12}(\\d{1,2}(?:[.,]\\d+)?)\\s*%",
    "(?:occlu\\w*|bị che|che khuất)[^.\\n]{0,80}?(?:keyframe|frame)[^.\\n]{0,20}?(\\d{1,2})\\b":
        "(?:occlu\\w*|bị che|che khuất)[^.\\n]{0,80}?(?:keyframe|frame)[^.\\n]{0,20}?(\\d{1,2})\\b",
    "(?:keyframe|frame)[^.\\n]{0,30}?(?:tối đa|max|≤|<=)[^\\d\\n]{0,10}(\\d{1,2})\\b":
        "(?:keyframe|frame)[^.\\n]{0,30}?(?:tối đa|max|≤|<=)[^\\d\\n]{0,10}(\\d{1,2})\\b",
    "bề ngang|chiều ngang|width|ngang|bề rộng": "bề ngang|chiều ngang|width|ngang|bề rộng",
    "yaw|hướng|heading|orientation": "yaw|hướng|heading|orientation",
}


def main() -> None:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    strings = data.setdefault("strings", {})
    added = 0
    for vi, en in TRANSLATIONS.items():
        if strings.get(vi) != en:
            strings[vi] = en
            added += 1
    CATALOG.write_text(json.dumps(data, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    print(f"Đã thêm/cập nhật {added} chuỗi. Tổng: {len(strings)}.")


if __name__ == "__main__":
    main()
