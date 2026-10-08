# Private prompts

The active prompts live beside their schemas and runtime boundaries rather than in
standalone files in this directory. Screen matching is assembled in
`brain/graph/matching.py`; task/notebook context is assembled in
`brain/graph/context.py`; document extraction prompts live in the pipeline modules.
All provider calls still go through the measured `ModelClient`.

Do not add ERP-specific labels, identifiers, coordinates, menu paths or tab order to
shared prompts. User-taught UI details belong in a `TaskInstruction` supplied with
the run, and runtime coordinates must come from the current observation.
