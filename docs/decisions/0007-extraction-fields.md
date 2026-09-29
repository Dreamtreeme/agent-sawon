# D7: extraction field list

Status: **proposed**, 2026-09-29. No human decision has been recorded.

The default is the ten logical fields in the current public ShipmentRecord:
House/Master B/L, shipper, consignee, description, packages (count and unit),
gross weight in kg, container number, port of loading and port of discharge.

An alternative is to select a narrower operational field set after interviews,
or extend the record with invoice/item metadata. The current public contract
requires all ten fields, so a smaller `required_fields` configuration is rejected
explicitly. Changing the field set requires a versioned contract change rather
than filling missing values with invented defaults. Required document kinds and
cross-document required fields are configurable today.

Each extracted value preserves original document/page, the evidence boxes in
rendered-page pixels, raw value/unit, normalized value and distinct confidence
sources. The ERP-facing record is produced only when all ten values are usable;
blocking discrepancies keep it out of READY state.

Validation: deterministic pipeline tests cover complete and incomplete cases,
unit conversion, mismatched sources and traceable evidence. These are software
checks, not a claim about measured OCR or model accuracy. Actual measurements are
recorded separately in the M2 development report.
