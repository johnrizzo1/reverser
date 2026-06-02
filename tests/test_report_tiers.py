import pytest
from types import SimpleNamespace

from reverser.tools.kb import _is_confirmed


@pytest.mark.parametrize("reach,validated,expected", [
    ("demonstrated", True, True),
    ("demonstrated", False, False),
    ("likely", True, False),
    ("theoretical", True, False),
    ("unknown", True, False),
    (None, True, False),
    ("demonstrated", None, False),
])
def test_is_confirmed(reach, validated, expected):
    f = SimpleNamespace(reachability=reach, validated=validated)
    assert _is_confirmed(f) is expected
