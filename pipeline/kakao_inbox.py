"""Export a named KakaoTalk Windows conversation through the existing visual relay."""

import time
from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sawon_interfaces import Action, Observation

from brain.relay import HttpRelay
from sawon_runtime.visual import OcrBlock, OcrEngine, RenderedPage


def _compact(text: str) -> str:
    return "".join(text.casefold().split())


def _windows(obs: Observation, title: str | None = None):
    return [
        w
        for w in obs.windows
        if w.process.casefold() == "kakaotalk.exe" and (title is None or w.title == title)
    ]


def _active(obs: Observation, title: str) -> bool:
    return obs.active_window is not None and obs.active_window in _windows(obs, title)


def _wait(relay: HttpRelay, condition: Callable[[Observation], bool], reason: str) -> Observation:
    deadline = time.monotonic() + 5
    while True:
        obs = relay.observe()
        if condition(obs):
            return obs
        if time.monotonic() >= deadline:
            raise RuntimeError(reason)
        time.sleep(0.15)


def _act(relay, obs, kind, *, point=None, **fields):
    if point is not None:
        fields["target"] = {
            "image_point": point,
            "capture_id": obs.screenshot.geometry.capture_id,
            "evidence": "current OCR of the selected app control",
        }
    result = relay.act(
        Action.model_validate(
            {
                "schema_version": "0.2",
                "action_id": uuid4(),
                "obs_id": obs.obs_id,
                "type": kind,
                "reason": "Read the original from the designated chat",
                **fields,
            }
        )
    )
    if not result.ok:
        raise RuntimeError(f"Kakao relay {kind}: {result.error}")


def _blocks(relay, obs, folder, ocr):
    shot = obs.screenshot
    if not shot or not shot.geometry:
        raise RuntimeError("Kakao capture unavailable")
    image = relay.screenshot(obs, folder / f"{obs.obs_id}.png")
    return ocr.read(
        RenderedPage(
            document="relay",
            kind="hbl",
            page=1,
            image_path=image,
            width=shot.width,
            height=shot.height,
        )
    )


def _center(block: OcrBlock):
    x, y, width, height = block.bbox
    return (x + width / 2, y + height / 2)


def _exact(blocks, label):
    matches = [b for b in blocks if _compact(b.text) == _compact(label)]
    if len(matches) > 1:
        raise RuntimeError("Kakao room/control label is ambiguous")
    return matches[0] if matches else None


def _focus(relay, obs, title):
    windows = _windows(obs, title)
    if len(windows) != 1:
        raise RuntimeError("Designated Kakao window missing or ambiguous")
    if _active(obs, title):
        return obs
    _act(relay, obs, "focus_window", handle=windows[0].handle)
    return _wait(relay, lambda o: _active(o, title), "Kakao window focus not confirmed")


def _dismiss_export_result(relay, obs, title, folder, ocr):
    """Confirm Kakao's export completion on its own popup and return to the room."""
    if not _windows(obs, title):
        raise RuntimeError("Designated room is unavailable behind Kakao export dialog")
    handle = obs.active_window.handle
    deadline = time.monotonic() + 10
    while _active(obs, "") and obs.active_window.handle == handle:
        blocks = _blocks(relay, obs, folder, ocr)
        words = [_compact(b.text).rstrip(".") for b in blocks]
        if "대화내보내기" not in words:
            break
        if "완료되었습니다" in words:
            confirm = _exact(blocks, "확인")
            if confirm is not None:
                _act(relay, obs, "click", point=_center(confirm))
                released = _wait(
                    relay,
                    lambda o: handle not in {w.handle for w in o.windows} and _windows(o, title),
                    "Kakao export dialog did not release the chat room",
                )
                return _focus(relay, released, title)
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)
        obs = relay.observe()
    raise RuntimeError("Cannot confirm the designated Kakao export completion")


def _open_room(relay, title, folder, ocr, self_chat):
    obs = relay.observe()
    if not _windows(obs, title) and not _windows(obs, "카카오톡"):
        _act(relay, obs, "open_app", app_id="kakaotalk")
        obs = _wait(relay, _windows, "KakaoTalk window unavailable")
    if _active(obs, ""):
        return _dismiss_export_result(relay, obs, title, folder, ocr)
    if _windows(obs, title):
        return _focus(relay, obs, title)
    obs = _focus(relay, obs, "카카오톡")
    blocks = _blocks(relay, obs, folder, ocr)
    label = _exact(blocks, title)
    if label is None:
        # Ctrl+F in the main window opens its current list's search. Only type into
        # the observed empty search field; set_text would send the app-wide Ctrl+A.
        def search_field(lines):
            return next(
                (
                    b
                    for b in lines
                    if _compact(b.text).startswith(
                        ("채팅방,참여자검색", "친구검색", "친구이름검색", "채팅방검색")
                    )
                ),
                None,
            )

        field = search_field(blocks)
        if field is None:
            _act(relay, obs, "key", keys="ctrl+f")
            time.sleep(0.3)
            obs = _wait(relay, lambda o: _active(o, "카카오톡"), "Kakao list search missing")
            field = search_field(_blocks(relay, obs, folder, ocr))
        if field is None:
            raise RuntimeError("Open the designated room or expose the Kakao room search")
        _act(relay, obs, "type_text", point=_center(field), text=title)
        time.sleep(0.4)
        obs = _wait(relay, lambda o: _active(o, "카카오톡"), "Kakao room search unavailable")
        # The search field itself can now contain the room name. Exclude its row.
        row_y = _center(field)[1] + field.bbox[3]
        label = _exact(
            [b for b in _blocks(relay, obs, folder, ocr) if _center(b)[1] > row_y], title
        )
    if label is None:
        raise RuntimeError("Exact Kakao room label not found")
    _act(relay, obs, "double_click", point=_center(label))
    time.sleep(0.5)
    obs = relay.observe()
    if self_chat and not _windows(obs, title) and obs.active_window in _windows(obs):
        button = _exact(_blocks(relay, obs, folder, ocr), "나와의 채팅")
        if button is not None:
            _act(relay, obs, "click", point=_center(button))
    obs = _wait(relay, lambda o: bool(_windows(o, title)), "Designated Kakao room did not open")
    return _focus(relay, obs, title)


def _filename_point(blocks, width):
    labels = [b for b in blocks if _compact(b.text).startswith(("파일이름", "filename"))]
    if len(labels) != 1:
        raise RuntimeError("Cannot identify the Save dialog filename row")
    label = labels[0]
    x, y = _center(label)
    row = [b for b in blocks if abs(_center(b)[1] - y) <= label.bbox[3] and _center(b)[0] > x]
    names = [b for b in row if ".txt" in b.text.casefold() or "kakaotalk" in b.text.casefold()]
    if len(names) == 1:
        return _center(names[0])
    if ".txt" in label.text.casefold():
        return (label.bbox[0] + label.bbox[2] * 0.85, y)
    # Standard Save dialog: the edit spans the row to the right of its label.
    return ((label.bbox[0] + label.bbox[2] + width) / 2, y)


@contextmanager
def _private_captures(directory, ocr):
    with ExitStack() as resources:
        folder = Path(resources.enter_context(TemporaryDirectory(dir=directory)))
        # The Paddle worker can keep its log open inside this directory on Windows.
        if close := getattr(ocr, "close", None):
            resources.callback(close)
        try:
            yield folder
        except Exception as failure:
            try:
                resources.close()
            except Exception as cleanup:
                failure.add_note(f"Private capture cleanup also failed: {type(cleanup).__name__}")
            raise


def export_kakao_chat(
    relay: HttpRelay, room_title: str, destination: Path, *, ocr: OcrEngine, self_chat: bool = False
) -> Path:
    """Export into the caller's private temporary directory. Never overwrite an existing file."""
    destination = destination.resolve()
    if not room_title.strip() or room_title == "카카오톡":
        raise ValueError("Supply the chat window's exact title")
    if (
        destination.suffix.lower() != ".txt"
        or destination.exists()
        or not destination.parent.is_dir()
    ):
        raise ValueError("Supply a new .txt path in a private existing directory")
    with _private_captures(destination.parent, ocr) as folder:
        obs = _open_room(relay, room_title, folder, ocr, self_chat)
        previous = {w.handle for w in obs.windows}
        _act(relay, obs, "key", keys="ctrl+s")
        dialog_titles = {
            "다른이름으로저장",
            "저장",
            "대화내용저장",
            "대화내용내보내기",
            "saveas",
            "save",
        }
        dialog = _wait(
            relay,
            lambda o: (
                o.active_window in _windows(o)
                and o.active_window.handle not in previous
                and _compact(o.active_window.title) in dialog_titles
            ),
            "Kakao Save dialog did not open",
        )
        handle = dialog.active_window.handle
        point = _filename_point(_blocks(relay, dialog, folder, ocr), dialog.screenshot.width)
        _act(relay, dialog, "set_text", point=point, text=str(destination))
        ready = _wait(
            relay,
            lambda o: o.active_window in _windows(o) and o.active_window.handle == handle,
            "Kakao Save dialog changed",
        )
        _act(relay, ready, "key", keys="enter")
        # Kakao creates the file before its separate export-result popup releases the chat.
        completed = _wait(
            relay,
            lambda o: destination.is_file() and _active(o, "") and o.active_window.handle != handle,
            "Kakao export result did not open for the requested file",
        )
        _dismiss_export_result(relay, completed, room_title, folder, ocr)
    return destination
