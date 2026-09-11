"""Closed public documentation peer, served by the maintained MCP SDK."""
import asyncio
import hashlib
from pathlib import Path
import sys

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
import mcp_types as types
from hyperclaw.contracts import canonical
from hyperclaw.mcp import ARGUMENTS, catalog, collect


def excerpt(text, size):
    return text.encode('utf-8')[:size].decode('utf-8', errors='ignore')


def build_server(directory):
    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=[types.Tool(**tool) for tool in catalog()])

    async def call_tool(ctx, params):
        if params.name not in ARGUMENTS:
            raise ValueError('Unknown documentation tool')
        args = ARGUMENTS[params.name].model_validate(params.arguments or {}).model_dump()
        files = collect(directory)
        if params.name == 'read':
            data = files[args['path']]
            lines = data.decode('utf-8').splitlines()
            start = args['start_line']
            selected = lines[start - 1:start - 1 + args['max_lines']]
            text = excerpt('\n'.join(selected), 8192)
            value = {'path': args['path'], 'start_line': start,
                     'end_line': start + len(text.splitlines()) - 1,
                     'sha256': hashlib.sha256(data).hexdigest(), 'text': text}
        else:
            hits = []
            for path, data in files.items():
                for line, text in enumerate(data.decode('utf-8').splitlines(), 1):
                    if args['query'].casefold() in text.casefold():
                        hits.append({'path': path, 'line': line, 'sha256': hashlib.sha256(data).hexdigest(),
                                     'text': excerpt(text, 512)})
                        if len(hits) == args['limit']: break
                if len(hits) == args['limit']: break
            value = {'hits': hits}
        return types.CallToolResult(content=[types.TextContent(type='text', text=canonical(value))], structuredContent=value)

    return Server('docs', version='1', on_list_tools=list_tools, on_call_tool=call_tool)


async def main():
    directory = Path(sys.argv[1]) if len(sys.argv) == 2 else Path('/docs')
    server = build_server(directory)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == '__main__':
    asyncio.run(main())
