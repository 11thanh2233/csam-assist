"""Xuất CenterPoint (pillar 0.2 m, nuScenes) ra một file ONNX cho SAM3 Label Assist (0.9.8; hướng dẫn sửa ở 0.9.11).

Chạy trên Google Colab (miễn phí, CPU là đủ), KHÔNG cần trên máy gán nhãn. Cách dễ nhất: mở notebook
scripts/export_centerpoint_onnx.ipynb trên Colab, tải file này lên /content, bấm Runtime -> Run all.

Chạy tay (mỗi khối một ô, KHÔNG để dấu cách đầu dòng khi dán vào Colab):

    # ô 1: Colab dùng Python 3.13, mmcv 2.1.0 chỉ có bản cài sẵn cho Python 3.8-3.11 -> tạo Python 3.10 riêng bằng uv
    !curl -LsSf https://astral.sh/uv/install.sh | sh
    !uv python install 3.10
    !uv venv -p 3.10 /content/py310

    # ô 2: thư viện (bản CPU: không phụ thuộc GPU của Colab)
    !uv pip install -p /content/py310/bin/python "torch==2.1.0" --index-url https://download.pytorch.org/whl/cpu
    !uv pip install -p /content/py310/bin/python https://download.openmmlab.com/mmcv/dist/cpu/torch2.1.0/mmcv-2.1.0-cp310-cp310-manylinux1_x86_64.whl
    !uv pip install -p /content/py310/bin/python "numpy<2" "opencv-python<4.11" "setuptools<70" "mmengine==0.10.4" "mmdet==3.3.0" "mmdet3d==1.4.0" "onnx==1.16.1" "onnxruntime==1.18.1"
    ![ -d mmdetection3d ] || git clone -q --depth 1 -b v1.4.0 https://github.com/open-mmlab/mmdetection3d.git

    # ô 3: kiểm tra, phải in "2.1.0+cpu 2.1.0 1.4.0"
    !/content/py310/bin/python -c "import torch, mmcv, mmdet3d; print(torch.__version__, mmcv.__version__, mmdet3d.__version__)"

    # ô 4: tải file này lên /content (biểu tượng thư mục ở cột trái) rồi xuất và tải về
    !/content/py310/bin/python export_centerpoint_onnx.py --out centerpoint_pillar02_nus.onnx
    from google.colab import files; files.download("centerpoint_pillar02_nus.onnx")

Luôn gọi /content/py310/bin/python (đường dẫn đầy đủ): "python" của Colab là Python 3.13, không có mmdet3d.
Cảnh báo "Don't suggest using CPU device" và "pip's dependency resolver ..." là bình thường.

Kết quả: một file .onnx khoảng 90 MB, script in "OK: ... [(1, 10, 128, 128), (1, 48, 128, 128), (1, 12, 128, 128)]".
Chép về máy, mở app -> Thành phần -> "Dò vật 3D LiDAR (CenterPoint ONNX)" -> Chọn file .onnx. Backend tự chia
pillar, giải mã và lọc box (backend/lidar_onnx.py).

onnxruntime báo "MatMulBnFusion_Gemm ... First input does not have rank 2" với tối ưu đồ thị mặc định (PillarFeatureNet
nhân ma trận 3 chiều ngay trước BatchNorm). File vẫn đúng: backend (0.9.11) và bước tự kiểm ở đây thử lại với tối ưu
đồ thị tắt (ORT_DISABLE_ALL).

Vì sao tự xuất thay vì dùng mmdeploy: bản xuất ở đây chỉ gồm phần mạng (PillarFeatureNet -> scatter -> SECOND ->
SECONDFPN -> CenterHead), đầu vào / đầu ra cố định đúng như lidar_onnx.py đọc:
    vào : voxels (N, 20, 5) float32, num_points (N,) int32, coors (N, 4) int32 [batch, z, y, x]
    ra  : heatmap (1, 10, 128, 128), box (1, 48, 128, 128) = 6 task x [reg 2, z 1, dim 3, vel 2],
          rot (1, 12, 128, 128) = 6 task x [sin, cos]          (chưa sigmoid / exp: backend làm)
Giấy phép: code MMDetection3D Apache-2.0; trọng số học trên nuScenes -> chỉ dùng phi thương mại / nghiên cứu.
"""
from __future__ import annotations

import argparse
import os
import urllib.request

CONFIG = "mmdetection3d/configs/centerpoint/centerpoint_pillar02_second_secfpn_head-circlenms_8xb4-cyclic-20e_nus-3d.py"
WEIGHTS = ("https://download.openmmlab.com/mmdetection3d/v1.0.0_models/centerpoint/"
           "centerpoint_02pillar_second_secfpn_circlenms_4x8_cyclic_20e_nus/"
           "centerpoint_02pillar_second_secfpn_circlenms_4x8_cyclic_20e_nus_20220811_031844-191a3822.pth")


def main() -> None:
    import torch
    from mmdet3d.apis import init_model

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="centerpoint_pillar02_nus.onnx")
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--checkpoint", default="centerpoint_pillar02_nus.pth")
    args = ap.parse_args()
    if not os.path.exists(args.checkpoint):
        print("Tải trọng số:", WEIGHTS)
        urllib.request.urlretrieve(WEIGHTS, args.checkpoint)
    model = init_model(args.config, args.checkpoint, device="cuda:0" if torch.cuda.is_available() else "cpu")
    model.eval()

    class Net(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, voxels, num_points, coors):
            feats = self.m.pts_voxel_encoder(voxels, num_points, coors)
            x = self.m.pts_middle_encoder(feats, coors, 1)
            x = self.m.pts_backbone(x)
            x = self.m.pts_neck(x)
            outs = self.m.pts_bbox_head(x)
            heat, box, rot = [], [], []
            for task in outs:
                r = task[0]
                heat.append(r["heatmap"])
                box += [r["reg"], r["height"], r["dim"], r["vel"]]
                rot.append(r["rot"])
            return torch.cat(heat, 1), torch.cat(box, 1), torch.cat(rot, 1)

    dev = next(model.parameters()).device
    n = 12000
    voxels = torch.zeros(n, 20, 5, device=dev)
    voxels[:, 0, :3] = torch.rand(n, 3, device=dev) * 20 - 10
    num_points = torch.ones(n, dtype=torch.int32, device=dev)
    coors = torch.zeros(n, 4, dtype=torch.int32, device=dev)
    coors[:, 2] = torch.randint(0, 512, (n,), device=dev)
    coors[:, 3] = torch.randint(0, 512, (n,), device=dev)
    net = Net(model).eval()
    with torch.no_grad():
        h, b, r = net(voxels, num_points, coors)
    assert h.shape[1] == 10 and b.shape[1] == 48 and r.shape[1] == 12, (h.shape, b.shape, r.shape)
    torch.onnx.export(net, (voxels, num_points, coors), args.out, opset_version=13,
                      input_names=["voxels", "num_points", "coors"], output_names=["heatmap", "box", "rot"],
                      dynamic_axes={"voxels": {0: "n"}, "num_points": {0: "n"}, "coors": {0: "n"}})
    import onnxruntime as ort

    # nạp giống backend/lidar_onnx.py: tối ưu mặc định trước, lỗi thì tắt tối ưu đồ thị (lỗi MatMulBnFusion_Gemm)
    try:
        s = ort.InferenceSession(args.out, providers=["CPUExecutionProvider"])
        mode = "tối ưu mặc định"
    except Exception as exc:  # noqa: BLE001
        print("Nạp với tối ưu mặc định lỗi (bình thường với file này), thử tắt tối ưu:", str(exc)[:160])
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        s = ort.InferenceSession(args.out, opts, providers=["CPUExecutionProvider"])
        mode = "tối ưu tắt"
    out = s.run(None, {"voxels": voxels.cpu().numpy(), "num_points": num_points.cpu().numpy(), "coors": coors.cpu().numpy()})
    shapes = [tuple(o.shape) for o in out]
    assert shapes == [(1, 10, 128, 128), (1, 48, 128, 128), (1, 12, 128, 128)], shapes
    print("OK:", args.out, shapes, f"{os.path.getsize(args.out) / 1e6:.1f} MB", f"({mode})")


if __name__ == "__main__":
    main()
