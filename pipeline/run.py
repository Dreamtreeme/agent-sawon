"""CLI: python -m pipeline.run --manifest input/manifest.json --case-dir cases/new."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from models.factory import create_model_client

from .extract import PipelineCallBudget
from .runner import load_inputs, prepare_case
from .schemas import DocumentInput, PipelineSettings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--case-id")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--hbl", type=Path)
    parser.add_argument("--invoice", type=Path)
    parser.add_argument("--packing-list", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config/pipeline.toml"))
    parser.add_argument("--model-config", type=Path, default=Path("config/models.lite.toml"))
    parser.add_argument("--env", type=Path)
    parser.add_argument("--provider", choices=("gemini", "replay"))
    args = parser.parse_args(argv)
    if args.manifest and any((args.hbl, args.invoice, args.packing_list)):
        parser.error("Use --manifest or explicit documents, not both")
    if args.manifest:
        manifest_case_id, documents = load_inputs(args.manifest)
        case_id = args.case_id or manifest_case_id
    else:
        documents = [
            DocumentInput(kind=kind, path=path)
            for kind, path in (
                ("hbl", args.hbl),
                ("invoice", args.invoice),
                ("packing_list", args.packing_list),
            )
            if path is not None
        ]
        case_id = args.case_id or f"documents-{uuid4().hex}"
    settings = PipelineSettings.load(args.config)
    result = prepare_case(
        args.case_dir,
        case_id=case_id,
        documents=documents,
        settings=settings,
        model_factory=lambda log: create_model_client(
            args.model_config, run_log=log, env_path=args.env, provider=args.provider
        ),
        reservation=PipelineCallBudget.from_config(args.model_config),
    )
    print(
        json.dumps(
            {
                "case_dir": str(result.case_dir),
                "case_id": result.case_id,
                "ready": result.ready,
                "known_cost_usd": result.known_cost_usd,
                "unknown_cost": result.unknown_cost,
                "error_type": result.error_type,
                "error_detail": result.error_detail,
                "blocking_codes": sorted(
                    {check.code for check in result.checks.checks if check.severity == "blocking"}
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
