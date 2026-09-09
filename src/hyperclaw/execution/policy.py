"""Closed tool catalog and operator-controlled, workspace-scoped authority."""
from dataclasses import dataclass
import hashlib
from typing import Annotated

from pydantic import ConfigDict, Field, ValidationError, model_validator
from hyperclaw.contracts import InvalidRequest, Value, canonical

PathArgument = Annotated[str, Field(min_length=1, max_length=1024)]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class Arguments(Value):
    model_config = ConfigDict(frozen=True, extra='forbid', strict=True, allow_inf_nan=False)


class Read(Arguments):
    path: PathArgument


class List(Arguments):
    path: PathArgument = '.'


class Search(List):
    query: str = Field(min_length=1, max_length=1024)


class Write(Read):
    content: str = Field(max_length=60000)
    expected_sha256: Digest | None = None


class FileCheck(Arguments):
    path: PathArgument
    expected_sha256: Digest | None = None


class Command(Arguments):
    argv: list[Annotated[str, Field(min_length=1, max_length=60000)]] = Field(min_length=1, max_length=128)
    timeout_s: float = Field(default=60, gt=0, le=60)
    checks: list[FileCheck] = Field(default_factory=list, max_length=16)

    @model_validator(mode='after')
    def unique_checks(self):
        if len({check.path for check in self.checks}) != len(self.checks):
            raise ValueError('Artifact check paths must be unique')
        return self


# Every definition declares authority, effect, output bound and deadline in one place.
CATALOG = {
    'workspace_read': (Read, 'read', 'read', 'Read a UTF-8 file within the selected workspace.'),
    'workspace_list': (List, 'read', 'read', 'List entries in a workspace directory.'),
    'workspace_search': (Search, 'read', 'read', 'Search workspace text files for a literal string.'),
    'workspace_write': (Write, 'write', 'write', 'Write a UTF-8 file; optionally check an expected SHA-256.'),
    'command': (Command, 'execute', 'command', 'Run argv in an isolated, network-disabled container at /workspace. Execution authority and writable mount authority are separate. Supply file checks to publish verified artifacts.'),
}
OUTPUT_LIMIT = 65536
DEADLINE_S = 60


@dataclass(frozen=True)
class Decision:
    arguments: dict
    capability: str
    effect: str
    sha256: str
    requires_approval: bool
    writable: bool
    deadline_s: float
    output_limit: int = OUTPUT_LIMIT


class Policy:
    def __init__(self, workspace_id, grants):
        self.workspace_id, self.grants = workspace_id, frozenset(grants)

    def definitions(self, offered):
        return [{'name': name, 'description': CATALOG[name][3], 'input_schema': CATALOG[name][0].model_json_schema()}
                for name in offered if name in CATALOG]

    def check(self, call, offered):
        if call.name not in CATALOG or call.name not in offered:
            raise InvalidRequest('tool_disallowed', 'This tool is not admitted for the run.')
        schema, capability, effect, _ = CATALOG[call.name]
        try:
            arguments = schema.model_validate(call.arguments).model_dump()
            if effect == 'command' and any('\x00' in arg for arg in arguments['argv']):
                raise ValueError('NUL argument')
        except (ValueError, ValidationError):
            raise InvalidRequest('invalid_tool_arguments', 'Tool arguments do not match the admitted schema.') from None
        deadline = min(DEADLINE_S, arguments.get('timeout_s', DEADLINE_S))
        from hyperclaw.execution.docker import TOOL_IMAGE
        value = {'sandbox_profile': 1, 'tool_image': TOOL_IMAGE, 'name': call.name, 'schema': schema.model_json_schema(), 'workspace_id': self.workspace_id,
                 'grants': sorted(self.grants), 'capability': capability, 'effect': effect,
                 'output_limit': OUTPUT_LIMIT, 'deadline_s': deadline, 'network': False}
        return Decision(arguments, capability, effect, hashlib.sha256(canonical(value).encode()).hexdigest(),
                        capability != 'read' and capability not in self.grants, 'write' in self.grants, deadline)
