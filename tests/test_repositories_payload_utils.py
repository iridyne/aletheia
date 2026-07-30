from pydantic import BaseModel
import pytest

import aletheia.storage.repositories as repositories


class _DummyPayload(BaseModel):
    intent: str
    risk_level: str


def test_to_payload_dict_supports_basemodel_and_mapping_inputs() -> None:
    model_payload = _DummyPayload(intent="status", risk_level="read")
    assert repositories._to_payload_dict(model_payload) == {
        "intent": "status",
        "risk_level": "read",
    }
    assert repositories._to_payload_dict({"intent": "status", "risk_level": "read"}) == {
        "intent": "status",
        "risk_level": "read",
    }


def test_to_payload_dict_rejects_non_supported_payload_type() -> None:
    with pytest.raises(TypeError) as exc:
        repositories._to_payload_dict(1)
    assert "Unsupported payload type" in str(exc.value)
