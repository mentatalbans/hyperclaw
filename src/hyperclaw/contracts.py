"""Public immutable values and sanitized errors shared by the runtime adapters."""
from pydantic import BaseModel, ConfigDict


class Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')


class RuntimeErrorBase(Exception):
    code = 'runtime_error'
    message = 'Runtime operation failed.'

    def __init__(self, code: str | None = None, message: str | None = None):
        self.code = code or type(self).code
        self.message = message or type(self).message
        super().__init__(self.message)


class InvalidRequest(RuntimeErrorBase):
    code = 'invalid_request'
    message = 'Invalid request or configuration.'


class NotFound(RuntimeErrorBase):
    code = 'not_found'
    message = 'The requested resource does not exist.'


class Conflict(RuntimeErrorBase):
    code = 'conflict'
    message = 'The request conflicts with current state.'


class RootInUse(RuntimeErrorBase):
    code = 'root_in_use'
    message = 'Another daemon owns this runtime root.'


class UnsupportedSchema(RuntimeErrorBase):
    code = 'unsupported_schema'
    message = 'This database schema is not supported.'


class ProviderFailure(RuntimeErrorBase):
    code = 'provider_failure'
    message = 'The selected model request failed.'


class StorageFailure(RuntimeErrorBase):
    code = 'storage_failure'
    message = 'Runtime storage is unavailable.'

from datetime import datetime, timezone
import json
from typing import Annotated, Literal

from pydantic import Field, JsonValue, field_validator

CONTEXT_BYTES = 64 * 1024
RunStatus = Literal['queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted']
TERMINAL = frozenset({'succeeded', 'failed', 'cancelled', 'interrupted'})
Identifier = Annotated[str, Field(min_length=1, max_length=256)]
Generation = Annotated[int, Field(ge=0, strict=True)]


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


class Session(Value):
    id: Identifier
    generation: Generation


class Message(Value):
    role: Literal['user', 'assistant']
    content: str


def message_bytes(messages: list[Message]) -> bytes:
    return canonical([m.model_dump() for m in messages]).encode('utf-8')


class RunRequest(Value):
    session_id: Identifier
    generation: Generation
    request_id: Identifier
    text: str = Field(min_length=1)
    retry_of: Identifier | None = None

    @field_validator('text')
    @classmethod
    def bounded_text(cls, value):
        if not value.strip() or len(message_bytes([Message(role='user', content=value)])) > CONTEXT_BYTES:
            raise ValueError('Current request exceeds the input budget or is empty')
        return value


class Failure(Value):
    code: str
    message: str


class Run(Value):
    id: Identifier
    request: RunRequest
    status: RunStatus
    output: str | None = None
    error: Failure | None = None
    verification: Literal['not_requested'] = 'not_requested'


class RunEvent(Value):
    run_id: Identifier
    seq: int = Field(gt=0, strict=True)
    kind: str
    at: datetime
    data: dict[str, JsonValue]

    @field_validator('at')
    @classmethod
    def utc_timestamp(cls, value):
        if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
            raise ValueError('Event timestamp must be UTC')
        return value


class ModelEvent(Value):
    kind: Literal['text', 'thinking', 'usage', 'finish']
    data: dict[str, JsonValue]
