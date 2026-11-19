"""Project one selected work memo into a screen-model request."""

from copy import deepcopy

from brain.input_binding import MODEL_INPUTS_NAME

_ITEM_LISTS = {
    "available_credentials",
    "confirmed_fields",
    "final_required_fields",
    "input_scope",
    "matched_inputs",
    "missing_fields",
    "pending_verification_fields",
    "pending_verification_inputs",
    "remaining_fields",
    "remaining_inputs",
    "required_sources",
    "verified_fields",
    "verified_inputs",
}


def memo_prompt_context(context: dict, labels: dict[str, str], title: str) -> dict:
    """Leave values, observations and cache state intact; only present bindings as item names."""

    result_keys = set(context.get("task", {}).get("result_keys", ()))

    def labelled_keys(mapping: dict) -> dict:
        return {labels.get(key, key): value for key, value in mapping.items()}

    def screen_tree(branch: dict) -> None:
        if "fields" in branch:
            branch["fields"] = {
                key if key in result_keys else labels.get(key, key): field
                for key, field in branch["fields"].items()
            }
        for child in branch.get("children", {}).values():
            screen_tree(child)

    def present_links(node) -> None:
        if isinstance(node, (list, tuple)):
            for item in node:
                present_links(item)
            return
        if not isinstance(node, dict):
            return
        for key, value in list(node.items()):
            if key == "input_key" and isinstance(value, str):
                node["memo_item"] = labels.get(value, value)
                del node[key]
            elif key in {"source", "source_key", "effect_source", "identity_field", "field"}:
                if isinstance(value, str):
                    node[key] = labels.get(value, value)
            elif key in _ITEM_LISTS and isinstance(value, (list, tuple)):
                node[key] = [labels.get(item, item) for item in value]
            elif key == "screen_tree":
                screen_tree(value)
            elif key in {"fields", "evidence"} and isinstance(value, dict):
                # Observation fields use visible screen labels; saved evidence uses input keys.
                if key == "evidence":
                    node[key] = labelled_keys(value)
                for item in value.values():
                    if (
                        key == "fields"
                        and isinstance(item, dict)
                        and item.get("source") in result_keys
                    ):
                        continue
                    present_links(item)
            elif key == "slots" and isinstance(value, list):
                for slot in value:
                    for prop in ("source_key", "name", "description"):
                        if prop in slot:
                            slot[prop] = labels.get(slot[prop], slot[prop])
            elif key == "slot_bindings":
                for method, bindings in value.items():
                    value[method] = labelled_keys(
                        {slot: labels.get(source, source) for slot, source in bindings.items()}
                    )
            elif key not in {"collected_result", "work_memo", "input_formats"}:
                present_links(value)

    view = deepcopy(context)
    notebook = view["notebook"]
    record = (
        notebook.pop(MODEL_INPUTS_NAME)
        if MODEL_INPUTS_NAME in notebook
        else notebook.pop("record", {})
    )
    notebook["work_memo"] = {"name": title, "items": labelled_keys(record)}
    task = view.get("task", {})
    identity = task.pop("identity_input_key", None)
    if identity is not None:
        task["identity_memo_item"] = labels.get(identity, identity)
    if "input_formats" in task:
        task["input_formats"] = labelled_keys(task["input_formats"])
    for turn in notebook.get("recent_turns", []):
        if turn["action_type"] in {"set_text", "type_text"}:
            action, _, target = turn["target_summary"].partition(":")
            if target in labels:
                turn["target_summary"] = f"{action}:{labels[target]}"
    present_links(view)
    return view
