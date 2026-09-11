"""Adversarial real SDK peer; launched only by test transports."""
import asyncio
from pathlib import Path
import sys

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
import mcp_types as types
from hyperclaw.mcp import catalog

mode = sys.argv[1]


async def listing(ctx, params):
    tools = [types.Tool(**item) for item in catalog()]
    if mode == 'catalog': tools.append(types.Tool(name='execute', inputSchema={'type': 'object'}))
    return types.ListToolsResult(tools=tools)


async def call(ctx, params):
    if mode == 'input_required':
        return types.InputRequiredResult(request_state='opaque-state-only')
    if mode == 'timeout': await asyncio.sleep(20)
    if mode == 'stderr':
        sys.stderr.write('x' * 1_000_000); sys.stderr.flush()
    if mode == 'disconnect': sys.exit(0)
    if mode == 'oversize_frame':
        sys.stdout.write('x' * 262145); sys.stdout.flush(); await asyncio.sleep(20)
    if mode == 'malformed':
        sys.stdout.write('{not-json}\n'); sys.stdout.flush(); await asyncio.sleep(20)
    if mode == 'inbound':
        sys.stdout.write('{"jsonrpc":"2.0","id":"peer-request","method":"roots/list"}\n'); sys.stdout.flush(); await asyncio.sleep(20)
    if mode == 'image':
        return types.CallToolResult(content=[types.ImageContent(type='image', data='AAAA', mimeType='image/png')])
    if mode == 'oversize_result':
        return types.CallToolResult(content=[types.TextContent(type='text', text='x' * 70000)])
    return types.CallToolResult(content=[types.TextContent(type='text', text='{"hits":[]}')], structuredContent={'hits': []})


async def main():
    server = Server('docs', version='1', on_list_tools=listing, on_call_tool=call)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

asyncio.run(main())
