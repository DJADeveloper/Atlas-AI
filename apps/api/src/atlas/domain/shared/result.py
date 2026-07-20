"""Result type: explicit success/failure without exception control flow.

Use cases return ``Result`` where failure is an expected outcome the caller
must handle (policy denial, not-found on a user-supplied id); exceptions
remain for bugs and infrastructure faults. ``match`` statements destructure
it naturally.
"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Ok[T]:
    value: T

    def is_ok(self) -> bool:
        return True

    def unwrap(self) -> T:
        return self.value

    def map[U](self, fn: Callable[[T], U]) -> "Ok[U]":
        return Ok(fn(self.value))


@dataclass(frozen=True, slots=True)
class Err[E]:
    error: E

    def is_ok(self) -> bool:
        return False

    def unwrap(self) -> object:
        raise UnwrapError(self.error)

    def map(self, fn: Callable[[object], object]) -> "Err[E]":
        return self


class UnwrapError(Exception):
    def __init__(self, error: object) -> None:
        super().__init__(f"called unwrap on Err({error!r})")
        self.error = error


type Result[T, E] = Ok[T] | Err[E]
