from __future__ import annotations

import pytest

from willem.config import profile_yaml_path
from willem.texts import Texts, load_texts


@pytest.fixture
def texts() -> Texts:
    return load_texts(profile_yaml_path("willem"))
