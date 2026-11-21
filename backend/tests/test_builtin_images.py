"""Built-in lab images: which ones the startup job builds (Docker stubbed)."""
from app.services import builtin_images as bi


def _setup(monkeypatch, tmp_path, labels: dict):
    for _img, d, _desc, _dep in bi.BUILTIN:
        (tmp_path / d).mkdir()
        (tmp_path / d / "Dockerfile").write_text(f"FROM scratch # {d}\n")
    monkeypatch.setattr(bi, "SOURCE_DIR", tmp_path)
    monkeypatch.setattr(bi, "_image_label", lambda image: labels.get(image))


def test_missing_and_outdated_are_built_and_dependants_follow(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, {})
    current = {img: bi.source_hash(tmp_path / d) for img, d, _x, _y in bi.BUILTIN}
    labels = {
        "plabs/host:latest": "old-hash",                    # source changed → rebuild
        "plabs/frr:latest": current["plabs/frr:latest"],    # up to date
        "plabs/traffic:latest": current["plabs/traffic:latest"],   # up to date, but FROM host
        # workstation missing → build
    }
    monkeypatch.setattr(bi, "_image_label", lambda image: labels.get(image))
    todo = [i for i, _d in bi._wanted(set())]
    assert todo == ["plabs/host:latest", "plabs/traffic:latest", "plabs/workstation:latest"]
    st = {s["image"]: s["state"] for s in bi.status()}
    assert st["plabs/host:latest"] == "outdated" and st["plabs/frr:latest"] == "present"
    assert st["plabs/workstation:latest"] == "missing"


def test_images_built_by_hand_are_left_alone(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, {img: "" for img, *_ in bi.BUILTIN})      # no label: scripts/build-images.sh
    assert bi._wanted(set()) == []
    assert [i for i, _ in bi._wanted({"plabs/frr:latest"})] == ["plabs/frr:latest"]
