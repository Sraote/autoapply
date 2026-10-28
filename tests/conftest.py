from pathlib import Path

import pytest
import yaml

from autoapply.profile.models import UserProfile

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile() -> UserProfile:
    data = yaml.safe_load(
        (ROOT / "src" / "autoapply" / "templates" / "profile_data.example.yaml").read_text("utf-8")
    )
    return UserProfile.model_validate(data)
