"""Stage-0 model/logging smoke demo; not an ERP workflow or benchmark run."""

from argparse import ArgumentParser
from pathlib import Path

from pydantic import BaseModel

from brain.runtime.storage import RunLog
from models import create_model_client

DEMO_PROMPT = "Return the synthetic stage-0 smoke test greeting."


class DemoResponse(BaseModel):
    message: str


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, default=Path("cases/replay_demo"))
    args = parser.parse_args()
    log = RunLog(args.case_dir, case_id="replay_demo", agent_id="offline_demo")
    config = Path(__file__).resolve().parents[1] / "config" / "models.toml"
    client = create_model_client(config, run_log=log, provider="replay")
    result = client.complete_json(DEMO_PROMPT, DemoResponse)
    print(result.response.message)
    print(f"Actual model cost: ${result.usd}; log: {args.case_dir / 'run_log.jsonl'}")


if __name__ == "__main__":
    main()
