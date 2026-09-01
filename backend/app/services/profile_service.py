import copy


def merge(base: dict, patch: dict) -> dict:
    result = copy.deepcopy(base or {})
    _merge_into(result, patch or {})
    return result


def _merge_into(base: dict, patch: dict) -> None:
    for key, value in patch.items():
        if value in (None, [], {}, ""):
            continue
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_into(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
