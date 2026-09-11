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


@pytest.mark.parametrize(('name', 'arguments'), [
    ('memory_remember', {'text': 'Store this.', 'scope': 'workspace',
                         'valid_until': '2030-01-01T00:00:00+00:00'}),
    ('memory_search', {'query': 'Store', 'limit': 3}),
    ('memory_correct', {'record_id': 'record-1', 'text': 'Replace this.'}),
    ('memory_forget', {'record_id': 'record-1'}),
])
def test_memory_tools_are_scoped_by_admission_without_grants(name, arguments):
    policy = Policy('workspace-a', set())

    decision = policy.check(ToolCall(id='1', name=name, arguments=arguments), [name])

    assert decision.capability == 'memory'
    assert decision.effect == 'memory'
    assert not decision.requires_approval
    assert not decision.writable
    assert [definition['name'] for definition in policy.definitions([name])] == [name]


@pytest.mark.parametrize('field', ['workspace_id', 'session_id', 'source_run_id'])
def test_memory_policy_rejects_model_selected_identity_and_authority(field):
    call = ToolCall(id='1', name='memory_remember', arguments={
        'text': 'Store this.', field: 'forged',
    })

    with pytest.raises(InvalidRequest) as caught:
        Policy('workspace-a', {'write', 'execute'}).check(call, ['memory_remember'])

    assert caught.value.code == 'invalid_tool_arguments'


def test_memory_search_schema_advertises_scope_visibility_and_strict_limit():
    definition = Policy('workspace-a', set()).definitions(['memory_search'])[0]
    properties = definition['input_schema']['properties']

    assert properties['limit']['type'] == 'integer'
    assert properties['limit']['default'] == 5
    assert properties['limit']['minimum'] == 1
    assert properties['limit']['maximum'] == 5
    assert '1 through 5' in properties['limit'].get('description', '')
    assert 'session' in properties['scope'].get('description', '')
    assert 'explicitly shared workspace records' in properties['scope'].get('description', '')
    assert 'defaults to session visibility' in definition['description']


@pytest.mark.parametrize(('name', 'scope_guidance'), [
    ('memory_remember', 'explicitly share the record'),
    ('memory_correct', 'already explicitly shared'),
    ('memory_forget', 'explicitly shared workspace record'),
])
def test_memory_mutation_schemas_explain_exact_scope(name, scope_guidance):
    definition = Policy('workspace-a', set()).definitions([name])[0]
    description = definition['input_schema']['properties']['scope'].get('description', '')

    assert 'session' in description
    assert scope_guidance in description
