# Observation summaries (M1 stage 2)

This module documents the retained non-screen-matching/UIA summary path. The primary
`visual_only` worker does not send these summaries to the model; it captures the
allowlisted ERP window and uses PaddleOCR, OmniParser and the visual marker contract
described in [brain/visual/README.md](../visual/README.md). The deterministic
same-row completion rule below therefore applies to the retained non-screen path,
not to the configured screen model's visual evidence policy.

`flat_fields` lists visible interactive elements with refs, role, name, value,
label hint and parent ref. `label_grouped` additionally derives a nearby label
and an ordered region/ancestor chain; this is the configurable default (D3 proposed).

Both keep result cells, row relationships, tab selection values and text messages.
The shared `rows.py` helper recognizes explicit semantic rows and generic
`Custom`/`Group`/`Pane` containers directly under a grid when their direct data
cells establish an unambiguous row. Header-only, empty, mixed, and nested data
containers are excluded. Both summaries include identified row elements,
`is_row`, and current `row_ref` context; nested grids use their nearest row.
Completion validation uses this same helper and still requires every field in
the same row of the same observation. A cell within an identified row cannot
be substituted for the row itself.

Unrelated noninteractive chrome is dropped. Neither sends AutomationIds, coordinates,
process paths or screenshots to the model. The bounding boxes in the public
observation contract are used only to associate a nearby label in the grouped
strategy. They never become action targets or stored target descriptors.

The number of summarized elements is bounded. Truncation stops the agent instead
of interpreting a partial grid as an empty duplicate search. Password replacement
is applied before observations are saved or summarized. The relay additionally
suppresses protected control names and values.

`tests/test_brain.py` runs the same deterministic fake workflow through both
strategies. Actual model development-seed comparisons are recorded by the harness
and must not be inferred from these unit tests.

The measured development run `20260928T102626Z-4077ad53` completed final
verification after the model maximized the query window, exposing every
required column together. All 11 logical field components matched one observed
row, and all 10 database fields matched. The case ended `awaiting_approval`.
No evidence accumulation across observations was introduced. See the private
diagnosis in `docs/model-diagnosis-20260928.md` for measurements and historical
limitations; this single seed-0 run is not a full evaluation result.
