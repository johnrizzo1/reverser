import json

from reverser.schemas.models import FindingModel, HypothesisModel
from reverser.schemas.validation import tool_input_schema


def test_schema_is_self_contained_no_refs():
    schema = tool_input_schema(FindingModel)
    blob = json.dumps(schema)
    assert "$ref" not in blob
    assert "$defs" not in schema and "definitions" not in schema


def test_schema_has_object_shape_and_required():
    schema = tool_input_schema(FindingModel)
    assert schema["type"] == "object"
    assert "properties" in schema
    for f in ("title", "severity", "description", "reproduction", "confidence", "reachability"):
        assert f in schema["required"]


def test_enum_is_inlined():
    schema = tool_input_schema(FindingModel)
    sev = schema["properties"]["severity"]
    assert "enum" in sev
    assert set(sev["enum"]) == {"info", "low", "medium", "high", "critical"}


def test_hypothesis_schema_self_contained():
    schema = tool_input_schema(HypothesisModel)
    assert "$ref" not in json.dumps(schema)
