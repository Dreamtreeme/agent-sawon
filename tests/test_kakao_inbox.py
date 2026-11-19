from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sawon_interfaces import Observation

from pipeline.kakao_inbox import export_kakao_chat
from sawon_runtime.visual import OcrBlock


class FakeKakao:
    """Synthetic Kakao window and Save dialog; no actual desktop or Kakao account."""

    def __init__(self, room="시험거래처"):
        self.room = room
        self.title = "카카오톡"
        self.actions = []
        self.filename = None
        self.ocr_rows = [room]
        self.export_rows = ["대화 내보내기", "100%", "완료되었습니다.", "확인", "폴더열기"]
        self.closed = False

    def observe(self):
        titles = ["카카오톡", self.room, "다른 이름으로 저장", ""]
        windows = [
            dict(process="KakaoTalk.exe", title=t, handle=i + 1)
            for i, t in enumerate(titles)
            if t == "카카오톡" or t == self.title or (t == self.room and self.title == "")
        ]
        active = next(w for w in windows if w["title"] == self.title)
        return Observation.model_validate(
            dict(
                obs_id=uuid4(),
                ts="2026-11-19T00:00:00Z",
                active_window=active,
                windows=windows,
                elements=[],
                screen_hash="1" * 40,
                screenshot=dict(
                    path="screenshots/1.png",
                    width=640,
                    height=480,
                    geometry=dict(
                        capture_id=uuid4(),
                        captured_at="2026-11-19T00:00:00Z",
                        dpi=96,
                        origin=(0, 0),
                        physical_size=(640, 480),
                    ),
                ),
            )
        )

    def screenshot(self, obs, destination):
        destination.write_text(obs.active_window.title, encoding="utf-8")
        return destination

    def read(self, page):
        rows = {
            "카카오톡": self.ocr_rows,
            "다른 이름으로 저장": ["파일 이름(N):", "KakaoTalk_old.txt"],
            "": self.export_rows,
        }[self.title]
        return [
            OcrBlock(id=i, text=t, bbox=(15 + 170 * i, 150, 145, 30), confidence=1)
            for i, t in enumerate(rows)
        ]

    def close(self):
        self.closed = True

    def act(self, action):
        p = action.root
        self.actions.append((self.title, p.type, getattr(p, "keys", None)))
        if p.type == "double_click":
            self.title = self.room
        elif p.type == "key" and p.keys == "ctrl+s":
            assert self.title == self.room
            self.title = "다른 이름으로 저장"
        elif p.type == "set_text":
            assert self.title == "다른 이름으로 저장" and p.target.image_point == (257.5, 165.0)
            self.filename = Path(p.text)
        elif p.type == "key" and p.keys == "enter":
            assert self.title == "다른 이름으로 저장"
            self.filename.write_text(f"{self.room} 님과 카카오톡 대화\n내용", encoding="utf-8")
            self.title = ""
        elif p.type == "click":
            assert self.title == "" and p.target.image_point == (597.5, 165.0)
            self.title = self.room
        else:
            pytest.fail(f"Unexpected synthetic GUI action {p.type}")
        return SimpleNamespace(ok=True)


def test_opens_exact_room_and_exports_original_in_same_dialog(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline.kakao_inbox.time.sleep", lambda _: None)
    app = FakeKakao()
    target = tmp_path / "export.txt"
    assert export_kakao_chat(app, app.room, target, ocr=app) == target
    assert target.read_text(encoding="utf-8") == "시험거래처 님과 카카오톡 대화\n내용"
    assert app.closed
    assert app.actions == [
        ("카카오톡", "double_click", None),
        (app.room, "key", "ctrl+s"),
        ("다른 이름으로 저장", "set_text", None),
        ("다른 이름으로 저장", "key", "enter"),
        ("", "click", None),
    ]


def test_repeated_export_recovers_previous_completion_and_finishes_each_turn(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline.kakao_inbox.time.sleep", lambda _: None)
    app = FakeKakao()
    app.title = ""  # Previous process stopped with the export-complete modal still open.
    for stem in ("first", "second"):
        assert export_kakao_chat(app, app.room, tmp_path / f"{stem}.txt", ocr=app).is_file()
        assert app.title == app.room
    assert app.actions[0] == ("", "click", None)
    assert app.actions.count(("", "click", None)) == 3
    assert app.actions.count((app.room, "key", "ctrl+s")) == 2


def test_does_not_acknowledge_unrelated_untitled_modal(tmp_path):
    app = FakeKakao()
    app.title = ""
    app.export_rows = ["친구 차단", "완료되었습니다.", "확인"]
    with pytest.raises(RuntimeError, match="Cannot confirm"):
        export_kakao_chat(app, app.room, tmp_path / "wrong.txt", ocr=app)
    assert app.actions == []


def test_no_export_from_ambiguous_room(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline.kakao_inbox.time.sleep", lambda _: None)
    app = FakeKakao()
    app.ocr_rows *= 2
    with pytest.raises(RuntimeError, match="ambiguous"):
        export_kakao_chat(app, app.room, tmp_path / "one.txt", ocr=app)
    assert app.actions == []


def test_relay_rejection_preserved_if_ocr_cleanup_also_fails(tmp_path):
    app = FakeKakao()
    app.act = lambda action: SimpleNamespace(ok=False, error="point_outside_allowed_window")

    def fail_close():
        raise PermissionError

    app.close = fail_close
    with pytest.raises(
        RuntimeError, match="Kakao relay double_click: point_outside_allowed_window"
    ) as err:
        export_kakao_chat(app, app.room, tmp_path / "export.txt", ocr=app)
    assert any("PermissionError" in note for note in err.value.__notes__)
    assert not list(tmp_path.iterdir())
