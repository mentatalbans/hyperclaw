import pytest
from hyperclaw.contracts import InvalidRequest, ToolCall
from hyperclaw.execution.policy import Policy


def test_policy_rejects_unknown_disallowed_and_malformed_calls():
    policy = Policy('workspace-a', set())
    for name, args, offered in [
        ('host_shell', {'command':'true'}, ['host_shell']),
        ('workspace_write', {'path':'answer','content':'x'}, ['workspace_read']),
        ('workspace_write', {'path':'answer','content':'x','grant':True}, ['workspace_write']),
        ('command', {'argv':['true'], 'timeout_s': 600}, ['command']),
    ]:
        with pytest.raises(InvalidRequest):
            policy.check(ToolCall(id='1', name=name, arguments=args), offered)


def test_execution_and_write_grants_are_separate_and_policy_hash_changes():
    call = ToolCall(id='1', name='command', arguments={'argv':['true']})
    none = Policy('w', set()).check(call, ['command'])
    write = Policy('w', {'write'}).check(call, ['command'])
    execute = Policy('w', {'execute'}).check(call, ['command'])
    assert none.requires_approval and not none.writable
    assert write.requires_approval and write.writable
    assert not execute.requires_approval and not execute.writable
    assert len({none.sha256, write.sha256, execute.sha256}) == 3


def test_command_rejects_nul_before_dispatch():
    with pytest.raises(InvalidRequest):
        Policy('w',{'execute'}).check(ToolCall(id='1',name='command',arguments={'argv':['echo','bad\0value']}),['command'])


def test_duplicate_artifact_checks_are_rejected_before_effects():
    with pytest.raises(InvalidRequest):
        Policy('w',{'execute'}).check(ToolCall(id='1',name='command',arguments={'argv':['true'],'checks':[{'path':'a'},{'path':'a'}]}),['command'])
