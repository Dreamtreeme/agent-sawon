# Seed 3 cold retry 066 with exact request preservation

- Model: claude-haiku-5-5, low; education: bench/education/development/company-003.json.
- One cold run only, fresh method/cache files. No warm run. Business logic unchanged from run 064.
- Result: success, ERP 10/10; worker 250.465 s, harness 253.333 s; 27 paid calls, estimated USD 0.0517764, unknown cost calls 0.
- Final capture d96f5445-df85-4123-bd34-30a154731333 was visually inspected: saved record number 1, description Synthetic sample parts visible.
- Local launcher .tools/pilot_trace_run.py records request bodies at the Anthropic SDK boundary and response bodies before local conversion. No authentication headers/keys are recorded. Private artifacts stay under ignored cases/.
- Each paid call has prompt.txt (exact UTF-8 bytes), schema.json, copied images, provider-request.json (image base64, model/options/wire schema), provider-response.json, converted response.txt and metadata.json.
- Verified all 27 saved request fingerprints against run_log.jsonl; prompt content and image bytes also match the actual SDK request bodies. All 27 have original provider responses. model-requests/index.json maps log sequence to artifacts.
- Production source hashes match the pre-run protocol. Wrapper was checked with a fake SDK before execution; production code was not edited for logging.
- Evidence: ../agent-sawon-bench/evidence/seed-audit-20261017-066/status.json.
- Case: cases/seed-audit-066-003-cold/fb64a7995464c0aec4c7/case-f41d9181c1a64ad38a8508f6f2ef564b/case.

The successful retry does not prove the earlier blank-description failure is repaired. The exact request contexts are now available for future controlled comparison. The capture wrapper is local to this pilot, not enabled globally.
