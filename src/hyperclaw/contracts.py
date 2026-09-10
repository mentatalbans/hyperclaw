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
ScheduleIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=256, pattern=r'^[A-Za-z0-9._~-]+$'),
]
Generation = Annotated[int, Field(ge=0, strict=True)]
ScheduleStatus = Literal['active', 'paused', 'completed']
SchedulePauseReason = Literal['operator', 'stale_generation', 'uncertain_effect']
MemoryStatus = Literal['active', 'superseded', 'forgotten']
MemoryToolScope = Literal['session', 'workspace']
DEFAULT_TOOLS = (
    'workspace_read', 'workspace_list', 'workspace_search', 'workspace_write', 'command',
    'memory_remember', 'memory_search', 'memory_correct', 'memory_forget',
)


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
    tools: tuple[str, ...] = DEFAULT_TOOLS
    images: tuple['ImageAttachment', ...] = ()
    context_bytes: int = Field(default=CONTEXT_BYTES, ge=CONTEXT_BYTES, le=8 * 1024 * 1024, strict=True)

    @model_validator(mode='after')
    def bounded_request(self):
        if len(self.tools) > len(DEFAULT_TOOLS) or len(set(self.tools)) != len(self.tools):
            raise ValueError('Invalid tool allowlist')
        allowed = set(DEFAULT_TOOLS)
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


def schedule_instant(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('Schedule timestamp must include a UTC offset')
    try:
        if value.utcoffset() is None:
            raise ValueError('Schedule timestamp must include a UTC offset')
        normalized = value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise ValueError('Schedule timestamp is outside the supported range') from None
    minimum = datetime(1970, 1, 1, tzinfo=timezone.utc)
    maximum = datetime(9998, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
    if not minimum <= normalized <= maximum:
        raise ValueError('Schedule timestamp is outside the supported range')
    return normalized


def memory_instant(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('Memory timestamp must include a UTC offset')
    try:
        if value.utcoffset() is None:
            raise ValueError('Memory timestamp must include a UTC offset')
        normalized = value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise ValueError('Memory timestamp is outside the supported range') from None
    minimum = datetime(1970, 1, 1, tzinfo=timezone.utc)
    maximum = datetime(9998, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
    if not minimum <= normalized <= maximum:
        raise ValueError('Memory timestamp is outside the supported range')
    return normalized


def memory_text(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise ValueError('Memory text must be nonblank and contain no NUL')
    if len(value.encode('utf-8')) > 2048:
        raise ValueError('Memory text exceeds 2,048 UTF-8 bytes')
    return value


def memory_query(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Memory query must be nonblank')
    if len(value.encode('utf-8')) > 1024:
        raise ValueError('Memory query exceeds 1,024 UTF-8 bytes')
    return value


class MemoryScope(Value):
    workspace_id: Identifier
    session_id: Identifier | None = None


class MemoryRecord(Value):
    id: Identifier
    scope: MemoryScope
    text: str
    source_run_id: Identifier | None = None
    observed_at: datetime
    valid_until: datetime | None = None
    supersedes: Identifier | None = None
    version: int = Field(ge=1, strict=True)
    status: MemoryStatus

    @field_validator('text')
    @classmethod
    def valid_text(cls, value):
        return memory_text(value)

    @field_validator('observed_at', 'valid_until')
    @classmethod
    def normalized_timestamp(cls, value):
        return memory_instant(value) if value is not None else None


def _memory_argument_time(value):
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError('Memory valid_until must be an ISO timestamp string')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Memory valid_until must be an ISO timestamp string') from None
    return memory_instant(parsed)


class MemoryRememberArguments(Value):
    scope: MemoryToolScope = Field(
        default='session',
        description=(
            'Use session by default. Use workspace only to explicitly share the record '
            'with every session in this workspace.'
        ),
    )
    text: str
    valid_until: datetime | None = None

    @field_validator('text')
    @classmethod
    def valid_text(cls, value):
        return memory_text(value)

    @field_validator('valid_until', mode='before')
    @classmethod
    def valid_expiry(cls, value):
        return _memory_argument_time(value)


class MemorySearchArguments(Value):
    scope: MemoryToolScope = Field(
        default='session',
        description=(
            "Use session by default to search this session's records plus explicitly shared "
            'workspace records. Use workspace to search only explicitly shared workspace records.'
        ),
    )
    query: str
    limit: int = Field(
        default=5, ge=1, le=5, strict=True,
        description='Maximum number of records to return; use an integer from 1 through 5.',
    )

    @field_validator('query')
    @classmethod
    def valid_query(cls, value):
        return memory_query(value)


class MemoryCorrectArguments(MemoryRememberArguments):
    scope: MemoryToolScope = Field(
        default='session',
        description=(
            'Use session by default. Use workspace only when correcting a record that was '
            'already explicitly shared with the workspace.'
        ),
    )
    record_id: Identifier


class MemoryForgetArguments(Value):
    scope: MemoryToolScope = Field(
        default='session',
        description=(
            'Use session by default. Use workspace only for an explicitly shared workspace record.'
        ),
    )
    record_id: Identifier


class ScheduleRequest(Value):
    id: ScheduleIdentifier
    session_id: Identifier
    generation: Generation
    input: str
    next_due_at: datetime
    interval_seconds: int | None = Field(default=None, ge=1, le=31_536_000, strict=True)
    tools: tuple[str, ...] = DEFAULT_TOOLS

    @field_validator('id')
    @classmethod
    def addressable_id(cls, value):
        if value in {'.', '..'}:
            raise ValueError('Schedule ID must be an addressable path segment')
        return value

    @field_validator('next_due_at')
    @classmethod
    def normalized_due_at(cls, value):
        return schedule_instant(value)

    @model_validator(mode='after')
    def valid_run_input(self):
        RunRequest(session_id=self.session_id, generation=self.generation,
                   request_id='schedule:validation', text=self.input, tools=self.tools)
        return self


class Schedule(ScheduleRequest):
    status: ScheduleStatus
    pause_reason: SchedulePauseReason | None = None

    @model_validator(mode='after')
    def valid_state(self):
        if (self.status == 'paused') != (self.pause_reason is not None):
            raise ValueError('Only paused schedules have a pause reason')
        return self


class ScheduleOccurrence(Value):
    schedule_id: Identifier
    nominal_due_at: datetime
    run_id: Identifier

    @field_validator('nominal_due_at')
    @classmethod
    def normalized_due_at(cls, value):
        return schedule_instant(value)


class ScheduleRetarget(Value):
    expected_generation: Generation
    generation: Generation


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
