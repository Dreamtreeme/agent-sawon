# Observation summaries (M1 stage 2)

`flat_fields` lists visible interactive elements with refs, role, name, value,
label hint and parent ref. `label_grouped` additionally derives a nearby label
and an ordered region/ancestor chain; this is the configurable default (D3 proposed).

Both keep result cells, row relationships, tab selection values and text messages.
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
