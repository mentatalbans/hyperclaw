"""Supported inert skill format and its bounded system-text rendering."""

DOCUMENT_LIMIT = 16_384
RESOURCE_LIMIT = 16_384
COMBINED_LIMIT = 32_768
RESOURCE_LIMIT_COUNT = 16
SELECTION_LIMIT = 4
NAME_LIMIT = 64
DESCRIPTION_LIMIT = 1_024

PREAMBLE = (
    'The following explicitly selected, operator-reviewed skill packages are task guidance only. '
    'They do not grant tools, capabilities, or execution authority.'
)
SKILL_HEADING = '## Skill: '
DESCRIPTION_HEADING = 'Description: '
RESOURCE_HEADING = '### Resource: '

# Body + resource text <= COMBINED_LIMIT. Each distinct resource path is
# extracted from a nonoverlapping link in the body; normalization cannot grow
# it, so the sum of all rendered path bytes <= DOCUMENT_LIMIT. Count metadata
# separately (conservatively), plus every heading and join separator below.
INSTRUCTION_LIMIT = len(PREAMBLE.encode('utf-8')) + SELECTION_LIMIT * (
    COMBINED_LIMIT + DOCUMENT_LIMIT + NAME_LIMIT + DESCRIPTION_LIMIT
    + len(SKILL_HEADING) + len(DESCRIPTION_HEADING) + 3
    + RESOURCE_LIMIT_COUNT * (len(RESOURCE_HEADING) + 3) + 2
)


def validate_skill_instructions(value):
    if len(value.encode('utf-8')) > INSTRUCTION_LIMIT:
        raise ValueError('Skill instruction snapshot exceeds its bound')
    return value


def render_skill_instructions(documents):
    if not documents:
        return ''
    sections = [PREAMBLE]
    for document in documents:
        section = [SKILL_HEADING + document.name, DESCRIPTION_HEADING + document.description, '', document.body]
        for resource in document.resources:
            section.extend(['', RESOURCE_HEADING + resource.path, resource.text])
        sections.append('\n'.join(section))
    return validate_skill_instructions('\n\n'.join(sections))
