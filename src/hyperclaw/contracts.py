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
