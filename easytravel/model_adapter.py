"""Replaceable raw-text adapter with one shared validation boundary."""
import asyncio
import json
from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from pydantic import ValidationError

from .contracts import Contract

T = TypeVar("T", bound=Contract)
Failure = Literal["invalid_json", "schema_error", "refusal", "timeout", "adapter_error"]


@dataclass(frozen=True)
class ModelReply:
    text: str = ""
    refused: bool = False


class ModelAdapter(Protocol):
    async def generate(self, *, repair: bool) -> ModelReply: ...


class MockAdapter:
    def __init__(self, *responses: ModelReply | Exception):
        self.responses = responses
        self.calls = 0

    async def generate(self, *, repair: bool) -> ModelReply:
        index = self.calls
        self.calls += 1
        if index >= len(self.responses):
            raise RuntimeError("mock response exhausted")
        response = self.responses[index]
        if isinstance(response, Exception):
            raise response
        return response


class OutputFailure(Exception):
    def __init__(self, code: Failure, attempts: int,
                 validation_issues: tuple[tuple[tuple[str | int, ...], str], ...] = ()):
        self.code, self.attempts = code, attempts
        self.validation_issues = validation_issues
        super().__init__(code)  # Never echo model text or adapter error details.


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_output(raw: str, schema: type[T]) -> T:
    if len(raw) > 200_000:
        raise ValueError("response too large")
    json.loads(raw, object_pairs_hook=_unique_object,
               parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    return schema.model_validate_json(raw)


async def generate_output(adapter: ModelAdapter, schema: type[T], *, timeout_seconds: float = 1) -> T:
    if timeout_seconds <= 0:
        raise ValueError("positive timeout required")
    for attempt in (1, 2):
        issues = ()
        try:
            reply = await asyncio.wait_for(adapter.generate(repair=attempt == 2), timeout_seconds)
        except TimeoutError:
            raise OutputFailure("timeout", attempt) from None
        except Exception:
            raise OutputFailure("adapter_error", attempt) from None
        if reply.refused:
            raise OutputFailure("refusal", attempt)
        try:
            return parse_output(reply.text, schema)
        except ValidationError as error:
            code = "invalid_json" if any(e["type"] == "json_invalid" for e in error.errors()) else "schema_error"
            # Retain field location/error kind, never raw values or free-text messages.
            issues = tuple((e["loc"], e["type"]) for e in error.errors())
        except (ValueError, RecursionError):
            code = "invalid_json"
        if attempt == 2:
            raise OutputFailure(code, attempt, issues)
    raise AssertionError("unreachable")
