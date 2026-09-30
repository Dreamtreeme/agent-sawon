"""Real subprocess lifetime tests with fake inference and no desktop/API access."""

import sys
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest
from PIL import Image

from brain.visual.detector_worker import DetectorSession
from pipeline.ocr_worker import OcrSession
from pipeline.persistent_worker import PersistentWorker, WorkerError


@pytest.fixture
def worker(tmp_path):
    server_path = Path(__file__).resolve().parents[1] / "pipeline"
    script = tmp_path / "fake_worker.py"
    script.write_text(
        "import sys,os,time,subprocess\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(server_path)!r})\n"
        "from worker_server import serve\n"
        "class Handler:\n"
        " def __init__(self): self.calls=0; self.metrics={}\n"
        " def __call__(self,r):\n"
        "  self.calls+=1\n"
        "  print('native library stdout noise',flush=True)\n"
        "  if r.get('child'):\n"
        "   p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],"
        "creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))\n"
        "   Path(r['child']).write_text(str(p.pid))\n"
        "  if r.get('fail'): raise ValueError('PRIVATE contents')\n"
        "  if r.get('hang'): time.sleep(30)\n"
        "  self.metrics={'model_initialized': self.calls==1}\n"
        "  return {'image':r['image'],'calls':self.calls}\n"
        "serve(Handler())\n",
        encoding="utf-8",
    )
    instance = PersistentWorker([sys.executable, str(script)], directory=tmp_path)
    yield instance
    instance.close()


def test_different_images_share_process_but_receive_new_results(worker, tmp_path):
    assert worker.request({"image": "first"}, timeout=10) == {"image": "first", "calls": 1}
    pid = worker.last_metrics["pid"]
    assert worker.last_metrics["model_initialized"]
    assert worker.request({"image": "second"}, timeout=10) == {"image": "second", "calls": 2}
    assert worker.last_metrics["pid"] == pid
    assert not worker.last_metrics["model_initialized"]
    assert not list(Path(worker._folder.name).glob("*.json"))
    worker.close()
    assert not psutil.pid_exists(pid)
    assert not list(tmp_path.glob(".worker-*"))


def test_failed_worker_is_not_reused_or_silently_retried(worker):
    worker.request({"image": "first"}, timeout=10)
    pid = worker.last_metrics["pid"]
    with pytest.raises(WorkerError, match="local_worker_request_failed") as error:
        worker.request({"image": "bad", "fail": True}, timeout=10)
    assert "PRIVATE" not in str(error.value)
    assert worker.process is None and not psutil.pid_exists(pid)
    assert worker.request({"image": "next"}, timeout=10)["calls"] == 1
    assert worker.last_metrics["model_initialized"]


@pytest.mark.parametrize("timeout", [False, True])
def test_timeout_or_normal_close_also_removes_owned_child(worker, tmp_path, timeout):
    import subprocess

    worker.request({"image": "warm"}, timeout=10)
    child = tmp_path / "child.pid"
    request = {"image": "second", "child": str(child), "hang": timeout}
    if timeout:
        with pytest.raises(subprocess.TimeoutExpired):
            worker.request(request, timeout=1)
    else:
        worker.request(request, timeout=10)
        worker.close()
    assert child.exists()
    pid = int(child.read_text())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    assert worker.process is None


def test_ocr_session_loads_once_until_configuration_changes(monkeypatch):
    loaded, recognized = [], []

    def create(*settings):
        model = object()
        loaded.append(model)
        return model

    def recognize(model, image):
        recognized.append((model, image))
        return []

    monkeypatch.setattr("pipeline.ocr_worker.create_engine", create)
    monkeypatch.setattr("pipeline.ocr_worker.recognize", recognize)
    session = OcrSession()
    request = dict(
        language="korean",
        device="cpu",
        enable_mkldnn=False,
        cpu_threads=4,
        ocr_version="PP-OCRv5",
        detection_model="det",
        recognition_model="rec",
        image="a.png",
    )
    session(request)
    session({**request, "image": "b.png"})
    assert len(loaded) == 1 and recognized[0][0] is recognized[1][0]
    assert [image for _, image in recognized] == ["a.png", "b.png"]
    assert not session.metrics["model_initialized"]
    session({**request, "language": "en"})
    assert len(loaded) == 2 and session.metrics["model_initialized"]


def test_detector_full_and_refinement_share_model(monkeypatch, tmp_path):
    loaded, inferred = [], []

    class Model:
        def __init__(self, weights):
            loaded.append(weights)
            self.predictor = SimpleNamespace(device="cuda:0")

        def __call__(self, image, **kwargs):
            assert kwargs["device"] == "cuda:0"
            inferred.append((image.size, image.getpixel((0, 0))))
            return []

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=Model))
    weights = tmp_path / "model.pt"
    weights.write_bytes(b"fake")
    first, second = tmp_path / "first.png", tmp_path / "second.png"
    Image.new("RGB", (40, 40), "white").save(first)
    Image.new("RGB", (40, 40), "black").save(second)
    request = dict(
        weights=str(weights),
        device="cuda:0",
        image=str(first),
        confidence=0.15,
        max_dimension=640,
        refinement_scale=2,
        regions=[],
    )
    session = DetectorSession()
    session(request)
    session({**request, "image": str(second)})
    session({**request, "image": str(second), "regions": [(0, 0, 20, 20)]})
    assert len(loaded) == 1 and len(inferred) == 3
    assert inferred[0][1] != inferred[1][1]
    assert not session.metrics["model_initialized"]


def test_gpu_ocr_registers_isolated_cuda_libraries_once(monkeypatch, tmp_path):
    from pipeline import ocr_worker

    cuda = tmp_path / "nvidia" / "cu13" / "bin" / "x86_64"
    cudnn = tmp_path / "nvidia" / "cudnn" / "bin"
    cuda.mkdir(parents=True)
    cudnn.mkdir(parents=True)
    registered = []
    monkeypatch.setattr(ocr_worker.sys, "platform", "win32")
    monkeypatch.setattr(ocr_worker.site, "getsitepackages", lambda: [str(tmp_path)])
    monkeypatch.setattr(ocr_worker, "_DLL_HANDLES", [])
    monkeypatch.delenv("PADDLE_CUDA_BIN_DIR", raising=False)
    monkeypatch.delenv("PADDLE_CUDNN_BIN_DIR", raising=False)
    monkeypatch.setenv("PATH", "original")
    monkeypatch.setattr(
        ocr_worker.os,
        "add_dll_directory",
        lambda path: registered.append(path) or object(),
        raising=False,
    )

    def factory(**kwargs):
        assert registered == [str(cuda), str(cudnn)]
        assert kwargs["device"] == "gpu:0"
        return object()

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=factory))
    ocr_worker.create_engine("en", "gpu:0")
    ocr_worker.create_engine("en", "gpu:0")
    assert len(registered) == 2


@pytest.mark.parametrize("interrupt", [False, True])
def test_brain_closes_local_workers_on_completion_and_interruption(monkeypatch, interrupt):
    from brain.graph.engine import Brain

    closed = []
    brain = object.__new__(Brain)
    brain.visual_perception = SimpleNamespace(close=lambda: closed.append(True))

    def run():
        if interrupt:
            raise KeyboardInterrupt
        return "completed"

    monkeypatch.setattr(brain, "_run", run)
    if interrupt:
        with pytest.raises(KeyboardInterrupt):
            brain.run()
    else:
        assert brain.run() == "completed"
    assert closed == [True]
