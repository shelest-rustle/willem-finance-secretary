from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_BASE_PATH = Path(__file__).parent / "texts_base.yaml"


def _flatten(prefix: str, node: dict) -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in node.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(full_key, value))
        else:
            flat[full_key] = value
    return flat


@dataclass(frozen=True)
class Texts:
    _data: dict[str, str]

    def get(self, key: str, **kwargs) -> str:
        template = self._data[key]
        return template.format(**kwargs) if kwargs else template


def load_texts(profile_path: Path) -> Texts:
    base = yaml.safe_load(_BASE_PATH.read_text(encoding="utf-8")) or {}
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    merged = {**_flatten("", base), **_flatten("", profile.get("messages") or {})}
    return Texts(merged)
