"""Test HTTP API bằng engine giả lập (không cần GPU, không cần SAM3)."""
import base64

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import BDD_NAMES, cvat_labels


@pytest.fixture(scope="module")
def client():
    from backend.app import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def image_b64():
    image = np.full((720, 1280, 3), 90, np.uint8)
    ok, buf = cv2.imencode(".jpg", image)
    assert ok
    return base64.b64encode(buf.tobytes()).decode()


def test_health_and_install_page(client):
    body = client.get("/health").json()
    assert body["ok"] and body["engine"] == "mock" and body["is_mock"]
    assert "Tampermonkey" in client.get("/").text
    script = client.get("/sam3-cvat-assist.user.js")
    assert script.status_code == 200 and "==UserScript==" in script.text


def test_resolve_propose_interactive_qa(client, image_b64):
    labels = cvat_labels(BDD_NAMES)
    resolved = client.post("/resolve", json={"profile_id": "auto", "labels": labels}).json()
    assert resolved["profile_id"] == "bdd_bbox_polygon_polyline_v1"
    assert len(resolved["labels"]) == len(BDD_NAMES)

    body = {"profile_id": "auto", "labels": labels, "image_b64": image_b64}
    result = client.post("/propose", json=body).json()
    assert result["width"] == 1280 and result["height"] == 720
    kinds = {p["type"] for p in result["proposals"]}
    assert {"rectangle", "polygon", "polyline"} <= kinds
    assert [p["pid"] for p in result["proposals"]] == list(range(1, len(result["proposals"]) + 1))

    inter = client.post(
        "/interactive", json=dict(body, label_id=3, box=[100, 100, 300, 260], mode="single")
    ).json()
    assert len(inter["proposals"]) == 1 and inter["proposals"][0]["type"] == "rectangle"
    x1, y1, x2, y2 = inter["proposals"][0]["points"]
    assert 95 <= x1 <= 110 and 290 <= x2 <= 305

    shapes = [dict(p, id=i, attributes=[]) for i, p in enumerate(result["proposals"])]
    report = client.post("/qa", json={"profile_id": "auto", "labels": labels, "shapes": shapes, "width": 1280, "height": 720}).json()
    assert report["shape_count"] == len(shapes) and report["checklist"]


def test_bad_image_rejected(client):
    r = client.post("/propose", json={"labels": [], "image_b64": "not-an-image"})
    assert r.status_code == 400
