"""Cache experiments use synthetic snapshots and fake agents, never relay or models."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sawon_interfaces import ShipmentRecord

from brain import run
from brain.cache import SemanticCache, SemanticEntry, TargetDescriptor
from brain.cache.experiment import CacheExperiment, CacheProvenance
from brain.cache.semantic import CacheDocument
from brain.graph import Result
from brain.runtime.storage import atomic_write_json
from brain.settings import BrainSettings, CacheSettings


def settings(path: Path, *, enabled: bool = True) -> BrainSettings:
    return BrainSettings(cache=CacheSettings(enabled=enabled, path=path))


def snapshot_document() -> CacheDocument:
    return CacheDocument(
        entries=(
            SemanticEntry(
                field="hbl_no",
                target_descriptor=TargetDescriptor(role="edit", label_semantics="shipment number"),
                last_verified=datetime(2026, 1, 1, tzinfo=UTC),
                success_count=3,
            ),
        )
    )


def provenance(case: Path) -> CacheProvenance:
    return CacheProvenance.model_validate_json(
        (case / "cache-experiment/provenance.json").read_bytes()
    )


def test_cold_cache_ignores_global_bytes_and_preserves_initial_evidence(tmp_path):
    global_cache = tmp_path / "global.json"
    global_cache.write_bytes(b"not a cache; must not be read")
    case = tmp_path / "case"
    case.mkdir()
    original_settings = settings(global_cache)
    experiment = CacheExperiment(original_settings, case_dir=case, mode="cold")
    isolated = experiment.start()
    assert original_settings.cache.path == global_cache
    assert isolated.cache.path == case / "cache-experiment/working.json"
    initial = (case / "cache-experiment/initial.json").read_bytes()
    assert CacheDocument.model_validate_json(initial).entries == ()
    assert isolated.cache.path.read_bytes() == initial
    assert provenance(case).outcome == "running" and not provenance(case).complete
    atomic_write_json(isolated.cache.path, snapshot_document())
    assert experiment.finish(outcome="success")
    result = provenance(case)
    assert result.mode == "cold" and result.snapshot_sha256 is None
    assert result.initial_working_sha256 == hashlib.sha256(initial).hexdigest()
    assert result.final_working_sha256 != result.initial_working_sha256
    assert result.outcome == "success" and result.final_cache_valid
    assert (case / "cache-experiment/initial.json").read_bytes() == initial
    assert global_cache.read_bytes() == b"not a cache; must not be read"
    assert not global_cache.with_suffix(".json.lock").exists()
    with pytest.raises(ValueError, match="new execution"):
        experiment.start()


def test_snapshot_copy_validates_and_records_failed_run_mutations_without_changing_source(tmp_path):
    snapshot = tmp_path / "frozen.json"
    snapshot.write_text(snapshot_document().model_dump_json(), encoding="utf-8")
    source_bytes = snapshot.read_bytes()
    case = tmp_path / "case"
    case.mkdir()
    experiment = CacheExperiment(
        settings(tmp_path / "unused-global.json"), case_dir=case, mode="snapshot", snapshot=snapshot
    )
    isolated = experiment.start()
    initial = (case / "cache-experiment/initial.json").read_bytes()
    copied = CacheDocument.model_validate_json(isolated.cache.path.read_bytes())
    assert copied == snapshot_document()
    cache = SemanticCache(isolated.cache.path)
    cache.record_failure(copied.entries[0].entry_id)
    assert experiment.finish(outcome="failed", error_type="RuntimeError")
    result = provenance(case)
    assert result.snapshot_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert result.snapshot_path == str(snapshot.resolve())
    assert result.outcome == "failed" and result.error_type == "RuntimeError"
    assert result.initial_working_sha256 != result.final_working_sha256
    assert cache.entries()[0].failure_count == 1
    assert snapshot.read_bytes() == source_bytes
    assert (case / "cache-experiment/initial.json").read_bytes() == initial
    assert not snapshot.with_suffix(".json.lock").exists()


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b'{"schema_version":"0.1","proposed_entries":[]}',
        b'{"schema_version":"0.1","entries":[],"approved":true}',
        b'{"schema_version":"0.1","entries":[{"field":"hbl_no"}]}',
    ],
)
def test_invalid_snapshot_or_proposals_are_preserved_with_failure_provenance(tmp_path, raw):
    snapshot = tmp_path / "source.json"
    snapshot.write_bytes(raw)
    case = tmp_path / "case"
    case.mkdir()
    experiment = CacheExperiment(
        settings(tmp_path / "global.json"), case_dir=case, mode="snapshot", snapshot=snapshot
    )
    with pytest.raises(ValueError):
        experiment.start()
    result = provenance(case)
    assert result.outcome == "failed" and not result.complete
    assert result.snapshot_sha256 == hashlib.sha256(raw).hexdigest()
    assert result.initial_working_sha256 is None and result.final_working_sha256 is None
    assert snapshot.read_bytes() == raw and not experiment.working_path.exists()
    assert not snapshot.with_suffix(".json.lock").exists()


@pytest.mark.parametrize("existing", ["run_log.jsonl", "status.json", "result.json"])
def test_isolation_refuses_existing_execution_without_modifying_it(tmp_path, existing):
    case = tmp_path / "case"
    case.mkdir()
    evidence = case / existing
    evidence.write_bytes(b"existing evidence")
    experiment = CacheExperiment(settings(tmp_path / "global.json"), case_dir=case, mode="cold")
    with pytest.raises(ValueError, match="cannot reset"):
        experiment.start()
    assert evidence.read_bytes() == b"existing evidence"
    assert list(case.iterdir()) == [evidence]


def test_exclusive_experiment_directory_preserves_previous_artifacts(tmp_path):
    case = tmp_path / "case"
    case.mkdir()
    first = CacheExperiment(settings(tmp_path / "global.json"), case_dir=case, mode="cold")
    first.start()
    before = {path.name: path.read_bytes() for path in first.directory.iterdir()}
    second = CacheExperiment(settings(tmp_path / "global.json"), case_dir=case, mode="cold")
    with pytest.raises(FileExistsError):
        second.start()
    assert not second.started
    assert {path.name: path.read_bytes() for path in first.directory.iterdir()} == before


def test_disabled_cache_rejects_isolation_before_snapshot_io_or_directory_creation(tmp_path):
    with pytest.raises(ValueError, match="enabled=true"):
        CacheExperiment(
            settings(tmp_path / "global.json", enabled=False),
            case_dir=tmp_path / "case",
            mode="snapshot",
            snapshot=tmp_path / "missing-snapshot.json",
        )
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def cli(tmp_path, monkeypatch):
    values = {
        "hbl_no": "SYNTHETIC-001",
        "mbl_no": "SYNTHETIC-MASTER",
        "shipper": "Synthetic Supplier",
        "consignee": "Synthetic Customer",
        "description": "Synthetic widgets",
        "packages": {"count": 2, "unit": "CT"},
        "gross_weight_kg": 10.5,
        "container_no": "SYNTHETIC-C01",
        "pol": "AAAAA",
        "pod": "BBBBB",
    }
    record = ShipmentRecord.model_validate(
        {
            "case_id": "cache-experiment-test",
            "origin": "manual",
            "fields": {name: {"value": value} for name, value in values.items()},
        }
    )
    record_path = tmp_path / "record.json"
    record_path.write_text(record.model_dump_json(), encoding="utf-8")
    state = SimpleNamespace(
        case=tmp_path / "case",
        settings=settings(tmp_path / "global.json"),
        calls=[],
        captured=None,
        action=None,
        model_error=False,
        result_status="success",
    )

    def factory(*_args, **_kwargs):
        state.calls.append("model_factory")
        if state.model_error:
            raise RuntimeError("private provider detail MUST_NOT_LEAK")
        return object()

    class FakeBrain:
        def __init__(self, **kwargs):
            state.captured = kwargs

        def run(self):
            data = state.captured
            data["run_log"].append("plan", payload={"fake": True})
            if state.action:
                state.action(data)
            result = Result(status=state.result_status, reason="fake_result", steps=1, elapsed_ms=1)
            atomic_write_json(state.case / "result.json", result)
            return result

    monkeypatch.setattr(run.BrainSettings, "load", lambda _path: state.settings)
    monkeypatch.setattr(run, "HttpRelay", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(run, "create_model_client", factory)
    monkeypatch.setattr(run.CallBudget, "from_config", lambda _path: None)
    monkeypatch.setattr(run, "Brain", FakeBrain)
    state.args = [
        "--record",
        str(record_path),
        "--case-dir",
        str(state.case),
        "--relay-url",
        "http://fake.invalid",
        "--goal",
        "synthetic goal",
        "--config",
        str(tmp_path / "unused.toml"),
        "--model-config",
        str(tmp_path / "unused-model.toml"),
    ]
    return state


@pytest.mark.parametrize("status", ["success", "stopped", "failed"])
@pytest.mark.parametrize("mode", ["cold", "snapshot"])
def test_cli_finalizes_isolated_cache_for_every_terminal_business_result(
    cli, tmp_path, status, mode
):
    cli.result_status = status
    args = cli.args + ["--cache-isolation", mode]
    if mode == "snapshot":
        snapshot = tmp_path / "snapshot.json"
        snapshot.write_text(snapshot_document().model_dump_json(), encoding="utf-8")
        args += ["--cache-snapshot", str(snapshot)]
    assert run.main(args) == (0 if status == "success" else 1)
    result = provenance(cli.case)
    assert result.outcome == status and result.complete
    assert result.initial_working_sha256 == result.final_working_sha256
    assert cli.captured["settings"].cache.path == cli.case / result.working_path
    assert not cli.settings.cache.path.exists()


def test_cli_setup_failure_still_finalizes_and_sanitizes_provenance(cli, capsys):
    cli.model_error = True
    assert run.main(cli.args + ["--cache-isolation", "cold"]) == 2
    result = provenance(cli.case)
    assert result.outcome == "failed" and result.error_type == "RuntimeError" and result.complete
    assert "MUST_NOT_LEAK" not in capsys.readouterr().out
    assert "MUST_NOT_LEAK" not in (cli.case / "cache-experiment/provenance.json").read_text()


def test_cli_missing_snapshot_records_failed_provenance_before_model_creation(cli, tmp_path):
    assert (
        run.main(
            cli.args
            + ["--cache-isolation", "snapshot", "--cache-snapshot", str(tmp_path / "missing.json")]
        )
        == 2
    )
    result = provenance(cli.case)
    assert result.outcome == "failed" and result.error_type == "FileNotFoundError"
    assert not result.complete and result.snapshot_sha256 is None
    assert cli.calls == [] and not (cli.case / result.working_path).exists()


def test_cli_interrupt_keeps_partial_append_only_log_and_finalizes_cache(cli):
    def interrupt(_data):
        raise KeyboardInterrupt

    cli.action = interrupt
    with pytest.raises(KeyboardInterrupt):
        run.main(cli.args + ["--cache-isolation", "cold"])
    result = provenance(cli.case)
    assert result.outcome == "interrupted" and result.complete
    assert result.error_type == "KeyboardInterrupt"
    assert len((cli.case / "run_log.jsonl").read_text().splitlines()) == 1
    assert not (cli.case / "result.json").exists()


def test_cli_corrupt_final_cache_marks_provenance_incomplete_without_rewriting_business_result(cli):
    cli.action = lambda data: data["settings"].cache.path.write_bytes(b"broken cache evidence")
    assert run.main(cli.args + ["--cache-isolation", "cold"]) == 2
    result = provenance(cli.case)
    assert result.outcome == "success" and not result.complete and not result.final_cache_valid
    assert result.final_working_sha256 == hashlib.sha256(b"broken cache evidence").hexdigest()
    assert result.finalization_error_type == "ValidationError"
    assert json.loads((cli.case / "result.json").read_text())["status"] == "success"
    assert (cli.case / result.working_path).read_bytes() == b"broken cache evidence"


@pytest.mark.parametrize("missing", [False, True])
def test_cli_damaged_initial_evidence_cannot_claim_complete_provenance(cli, missing):
    def damage_initial(data):
        initial = data["settings"].cache.path.parent / "initial.json"
        if missing:
            initial.unlink()
        else:
            atomic_write_json(initial, snapshot_document())

    cli.action = damage_initial
    assert run.main(cli.args + ["--cache-isolation", "cold"]) == 2
    result = provenance(cli.case)
    assert result.final_cache_valid and not result.initial_cache_valid and not result.complete
    assert result.finalization_error_type == ("FileNotFoundError" if missing else "ValueError")
    assert json.loads((cli.case / "result.json").read_text())["status"] == "success"


@pytest.mark.parametrize(
    "options",
    [
        ["--cache-isolation", "snapshot"],
        ["--cache-snapshot", "missing.json"],
        ["--cache-isolation", "cold", "--cache-snapshot", "missing.json"],
    ],
)
def test_cli_rejects_invalid_isolation_flags_before_any_case_or_client(cli, options):
    with pytest.raises(SystemExit) as error:
        run.main(cli.args + options)
    assert error.value.code == 2 and not cli.case.exists() and cli.calls == []


def test_cli_rejects_invalid_prepared_isolation_without_touching_existing_evidence(cli):
    cli.case.mkdir()
    names = ["record.json", "status.json", "checks.json", "run_log.jsonl", "result.json"]
    for name in names:
        (cli.case / name).write_bytes(b"existing private evidence")
    before = {name: (cli.case / name).read_bytes() for name in names}
    assert run.main(cli.args + ["--cache-isolation", "cold", "--prepared-case"]) == 2
    assert {name: (cli.case / name).read_bytes() for name in names} == before
    assert cli.calls == [] and not (cli.case / "cache-experiment").exists()


def test_cli_without_isolation_preserves_legacy_cache_path(cli):
    assert run.main(cli.args) == 0
    assert cli.captured["settings"].cache.path == cli.settings.cache.path
    assert not (cli.case / "cache-experiment").exists()


def test_cli_disabled_cache_has_no_cache_reads_or_writes(cli):
    cli.settings = settings(cli.settings.cache.path, enabled=False)
    cli.settings.cache.path.write_bytes(b"corrupt global cache must remain ignored")

    def check_disabled(data):
        cache = data["settings"].cache
        assert SemanticCache(cache.path, enabled=cache.enabled).entries() == ()

    cli.action = check_disabled
    assert run.main(cli.args) == 0
    assert cli.settings.cache.path.read_bytes() == b"corrupt global cache must remain ignored"
    assert not cli.settings.cache.path.with_suffix(".json.lock").exists()
    assert not (cli.case / "cache-experiment").exists()


def test_cli_disabled_cache_with_isolation_stops_before_case_or_model(cli):
    cli.settings = settings(cli.settings.cache.path, enabled=False)
    assert run.main(cli.args + ["--cache-isolation", "cold"]) == 2
    assert not cli.case.exists() and cli.calls == []


def test_example_changes_only_cache_treatment_and_keeps_harness_options_out_of_agent_cli():
    root = Path(__file__).resolve().parents[1]
    example = json.loads((root / "config/harness_agents.cache-isolated.json").read_text("utf-8"))
    normalized_commands = []
    for name, entry in example["agents"].items():
        command = list(entry["command"])
        assert "--max-consecutive-environment-failures" not in command
        assert entry["max_case_usd"] == 0.5
        assert all((root / path).is_file() for path in entry["metadata_files"])
        mode = (
            command[command.index("--cache-isolation") + 1]
            if "--cache-isolation" in command
            else None
        )
        assert (
            mode
            == {
                "core_m2_cache_cold": "cold",
                "core_m2_cache_snapshot": "snapshot",
                "core_m2_no_cache": None,
            }[name]
        )
        for flag in ["--cache-isolation", "--cache-snapshot", "--config"]:
            if flag in command:
                index = command.index(flag)
                del command[index : index + 2]
        normalized_commands.append(command)
    assert normalized_commands[0] == normalized_commands[1] == normalized_commands[2]
