"""Record visual demonstrations through an observation-only relay; never sends actions."""

from __future__ import annotations

import argparse
import json
import math
import os
from decimal import Decimal
from pathlib import Path

from brain.relay import HttpRelay
from brain.settings import BrainSettings
from brain.visual.detector import OmniParserDetector
from brain.visual.scanning import ScreenScanner, SemanticRequest
from brain.visual.scene import SemanticDetection
from models.accounting import read_model_accounting
from models.budget import ImageCallBudget
from models.factory import create_model_client
from pipeline.ocr import PaddleOcrEngine
from pipeline.schemas import PipelineSettings
from sawon_runtime.storage import RunLog, atomic_write_json


def record_scenes(source, perception, output: Path, *, samples: int) -> int:
    if type(samples) is not int or not 1 <= samples <= 120:
        raise ValueError("Samples must be 1..120")
    health = source.health()
    if (
        health.get("observation_only") is not True
        or health.get("observation_mode") != "visual_only"
    ):
        raise ValueError("Visual demonstration requires an observation-only visual relay")
    count = 0
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        for _ in range(samples):
            wire = source.observe()
            if wire.blocked_reason or wire.active_window is None:
                raise ValueError("blocked_foreground")
            scene = perception.scan(wire)
            stream.write(scene.model_dump_json() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            count += 1
    return count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--relay-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ocr-config", type=Path, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--brain-config", type=Path, required=True)
    parser.add_argument("--env-path", type=Path)
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--max-cost-usd", type=float, required=True)
    args = parser.parse_args(argv)
    if not math.isfinite(args.max_cost_usd) or not 0 < args.max_cost_usd <= 50:
        parser.error("A finite budget up to $50 is required")
    if not 1 <= args.samples <= 120:
        parser.error("Samples must be 1..120")
    source = HttpRelay(args.relay_url, os.environ.get("SAWON_RELAY_TOKEN", ""))
    health = source.health()
    if not health.get("observation_only"):
        parser.error("Relay must be observation-only")
    args.output.mkdir(parents=True, exist_ok=False)
    log = RunLog(args.output, case_id="visual-demonstration", agent_id="visual-recorder")
    model = create_model_client(args.model_config, run_log=log, env_path=args.env_path)
    budget = ImageCallBudget.from_config(args.model_config)

    def detect(request: SemanticRequest) -> SemanticDetection:
        prompt, page = request.prompt(), request.page
        accounting = read_model_accounting(log)
        if accounting.unknown:
            raise ValueError("unknown_model_call_cost")
        used = accounting.known_usd
        if used + budget(prompt, SemanticDetection, page) > Decimal(str(args.max_cost_usd)):
            raise ValueError("demonstration_cost_cap")
        result = model.complete_json(prompt, SemanticDetection, images=[page.image_path])
        if used + result.usd > Decimal(str(args.max_cost_usd)):
            raise ValueError("demonstration_cost_cap")
        return result.response

    settings = BrainSettings.load(args.brain_config)
    perception = ScreenScanner(
        source,
        PaddleOcrEngine(PipelineSettings.load(args.ocr_config)),
        args.output,
        log,
        detect,
        detector=OmniParserDetector(settings.visual_detector, log),
        merge_settings=settings.visual_merge,
    )
    try:
        count = record_scenes(
            source, perception, args.output / "observations.jsonl", samples=args.samples
        )
    except Exception as exc:
        atomic_write_json(
            args.output / "recording-result.json",
            {"status": "stopped", "error_type": type(exc).__name__},
        )
        return 1
    finally:
        perception.close()
    result = {
        "status": "recorded",
        "observations": count,
        "source": "ocr_visual",
        "cache_installed": False,
    }
    atomic_write_json(args.output / "recording-result.json", result)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
