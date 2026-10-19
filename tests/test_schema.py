import pytest

from vnpipe.schema import SchemaError, validate


def test_valid_instance_passes():
    validate(
        {
            "work": "x",
            "source_sha256": "a" * 64,
            "scenes": [{"id": "s", "heading": "H", "lines": []}],
        },
        "parsed",
    )


def test_reports_all_problems():
    with pytest.raises(SchemaError) as exc:
        validate({"work": 1, "source_sha256": "bad", "scenes": []}, "parsed")
    message = str(exc.value)
    assert "3 problem(s)" in message
    assert "/work" in message and "/source_sha256" in message and "/scenes" in message


def test_unknown_schema():
    with pytest.raises(FileNotFoundError):
        validate({}, "nope")
