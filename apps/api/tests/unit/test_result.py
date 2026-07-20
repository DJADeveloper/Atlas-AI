"""Result type semantics."""

import pytest

from atlas.domain.shared.result import Err, Ok, Result, UnwrapError


def test_ok_unwraps_and_maps() -> None:
    result: Result[int, str] = Ok(2)
    assert result.is_ok()
    assert result.unwrap() == 2
    assert Ok(2).map(lambda v: v * 3).value == 6


def test_err_raises_on_unwrap_and_skips_map() -> None:
    result: Result[int, str] = Err("nope")
    assert not result.is_ok()
    with pytest.raises(UnwrapError):
        result.unwrap()
    assert Err("nope").map(lambda v: v).error == "nope"


def test_match_destructuring() -> None:
    def classify(result: Result[int, str]) -> str:
        match result:
            case Ok(value):
                return f"ok:{value}"
            case Err(error):
                return f"err:{error}"

    assert classify(Ok(1)) == "ok:1"
    assert classify(Err("x")) == "err:x"
