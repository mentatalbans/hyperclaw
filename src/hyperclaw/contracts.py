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

from pydantic import Field, JsonValue, field_validator, model_validator

CONTEXT_BYTES = 64 * 1024
RunStatus = Literal['queued', 'running', 'waiting_approval', 'succeeded', 'failed', 'cancelled', 'interrupted', 'uncertain']
TERMINAL = frozenset({'succeeded', 'failed', 'cancelled', 'interrupted', 'uncertain'})
Identifier = Annotated[str, Field(min_length=1, max_length=256)]
Generation = Annotated[int, Field(ge=0, strict=True)]


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


class Session(Value):
    id: Identifier
    generation: Generation


class Message(Value):
    role: Literal['user', 'assistant']
    content: str | list[dict[str, JsonValue]]


def message_bytes(messages: list[Message]) -> bytes:
    return canonical([m.model_dump() for m in messages]).encode('utf-8')


class RunRequest(Value):
    session_id: Identifier
    generation: Generation
    request_id: Identifier
    text: str = Field(min_length=1)
    retry_of: Identifier | None = None
    tools: tuple[str, ...] = ('workspace_read', 'workspace_list', 'workspace_search', 'workspace_write', 'command')
    images: tuple['ImageAttachment', ...] = ()
    context_bytes: int = Field(default=CONTEXT_BYTES, ge=CONTEXT_BYTES, le=8 * 1024 * 1024, strict=True)

    @model_validator(mode='after')
    def bounded_request(self):
        if len(self.tools) > 5 or len(set(self.tools)) != len(self.tools):
            raise ValueError('Invalid tool allowlist')
        allowed = {'workspace_read', 'workspace_list', 'workspace_search', 'workspace_write', 'command'}
        if set(self.tools) - allowed:
            raise ValueError('Unknown tool')
        if len(self.images) > 4 or len(message_bytes([self.current_message()])) > self.context_bytes:
            raise ValueError('Current request exceeds the selected input budget')
        return self

    def current_message(self):
        if not self.images:
            return Message(role='user', content=self.text)
        return Message(role='user', content=[{'type': 'text', 'text': self.text}] + [image.block() for image in self.images])

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
    verification: Literal['not_requested', 'passed', 'failed'] = 'not_requested'
    elapsed_s: float = 0
    artifacts: tuple['Artifact', ...] = ()


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
    kind: Literal['text', 'thinking', 'usage', 'tool_call', 'finish']
    data: dict[str, JsonValue]


class ImageAttachment(Value):
    media_type: Literal['image/png', 'image/jpeg', 'image/gif', 'image/webp']
    data: str = Field(min_length=1, max_length=8 * 1024 * 1024)

    @model_validator(mode='after')
    def valid_image(self):
        import base64
        import binascii
        try:
            raw = base64.b64decode(self.data, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('Invalid base64 image') from None
        matches = {'image/png': raw.startswith(b'\x89PNG\r\n\x1a\n'),
                   'image/jpeg': raw.startswith(b'\xff\xd8\xff'),
                   'image/gif': raw.startswith((b'GIF87a', b'GIF89a')),
                   'image/webp': raw.startswith(b'RIFF') and raw[8:12] == b'WEBP'}
        if not matches[self.media_type]:
            raise ValueError('Image bytes do not match media type')
        return self

    def block(self):
        return {'type': 'image', 'source': {'type': 'base64', 'media_type': self.media_type, 'data': self.data}}


class ToolCall(Value):
    id: Identifier
    name: Identifier
    arguments: dict[str, JsonValue]

    @field_validator('arguments')
    @classmethod
    def bounded_arguments(cls, value):
        if len(canonical(value).encode()) > 65536:
            raise ValueError('Tool arguments exceed 64 KiB')
        return value


class Artifact(Value):
    path: str
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    size_bytes: int = Field(ge=0)


class ToolReceipt(Value):
    invocation_id: Identifier
    status: Literal['succeeded', 'failed', 'cancelled', 'interrupted', 'uncertain']
    output: str = ''
    artifacts: tuple[Artifact, ...] = ()
    evidence: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator('output')
    @classmethod
    def bounded_output(cls, value):
        return value.encode('utf-8')[:65536].decode('utf-8', errors='ignore')


class Checkpoint(Value):
    messages: list[Message]
    pending_calls: list[ToolCall] = Field(default_factory=list)
    round_count: int = Field(default=0, ge=0, le=12)
    call_counts: dict[str, int] = Field(default_factory=dict)
    elapsed_s: float = Field(default=0, ge=0)
    history_length: int = Field(default=0, ge=0)
    workspace_id: str = ''


class Invocation(Value):
    id: str
    run_id: str
    call: ToolCall
    arguments_sha256: str
    policy_sha256: str
    workspace_id: str
    capability: str
    status: str
    approved: bool = False
    container_id: str | None = None
    receipt: ToolReceipt | None = None


class Approval(Value):
    id: str
    invocation_id: str
    run_id: str
    call: ToolCall
    arguments_sha256: str
    policy_sha256: str
    workspace_id: str
    expires_at: str
    status: str


class ApprovalRequired(RuntimeErrorBase):
    code = 'approval_required'
    message = 'This invocation requires an operator decision.'


RunRequest.model_rebuild()
Run.model_rebuild()
