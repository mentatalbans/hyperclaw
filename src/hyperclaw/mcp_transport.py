"""Bounded byte framing for an already owned Docker attachment.

SDK types and Client own all protocol semantics. No stdio/HTTP server selection,
negotiation, IDs, retries, or request dispatcher is implemented here.
"""
import asyncio
from contextlib import asynccontextmanager, suppress

from hyperclaw.mcp import WIRE_LIMIT


@asynccontextmanager
async def byte_streams(stdout, stdin, stderr=None, *, evidence=None):
    import anyio
    from mcp.shared.message import SessionMessage
    from mcp_types import JSONRPCRequest, jsonrpc_message_adapter

    incoming, read = anyio.create_memory_object_stream(0)
    write, outgoing = anyio.create_memory_object_stream(0)
    evidence = evidence if evidence is not None else {}
    evidence['stderr_bytes'] = 0
    async def reader_guard():
        # Keep the send endpoint open until errors have reached the SDK.
        buffer = bytearray()
        try:
            while chunk := await stdout.read(8192):
                buffer.extend(chunk)
                while (end := buffer.find(b'\n')) >= 0:
                    if end > WIRE_LIMIT:
                        raise ValueError('MCP frame exceeds 262144 bytes')
                    line = bytes(buffer[:end])
                    del buffer[:end + 1]
                    message = jsonrpc_message_adapter.validate_json(line, by_name=False)
                    if isinstance(message, JSONRPCRequest):
                        raise ValueError('MCP inbound requests are unsupported')
                    await incoming.send(SessionMessage(message))
                if len(buffer) > WIRE_LIMIT:
                    raise ValueError('MCP frame exceeds 262144 bytes')
            if buffer:
                raise ValueError('MCP disconnected inside a frame')
        except (ValueError, OSError) as exc:
            with suppress(anyio.ClosedResourceError, anyio.BrokenResourceError):
                await incoming.send(exc)
        finally:
            await incoming.aclose()
    async def writer():
        try:
            async with outgoing:
                async for item in outgoing:
                    data = item.message.model_dump_json(by_alias=True, exclude_unset=True).encode('utf-8')
                    if len(data) > WIRE_LIMIT:
                        raise ValueError('MCP outgoing frame exceeds limit')
                    stdin.write(data + b'\n')
                    await stdin.drain()
        except (OSError, ValueError):
            await incoming.aclose()
    async def drain_stderr():
        if stderr:
            while chunk := await stderr.read(8192):
                evidence['stderr_bytes'] = min(65536, evidence['stderr_bytes'] + len(chunk))
    tasks = [asyncio.create_task(reader_guard()), asyncio.create_task(writer()), asyncio.create_task(drain_stderr())]
    try:
        yield read, write
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for stream in (read, write, incoming, outgoing):
            await stream.aclose()


@asynccontextmanager
async def owned_attachment(backend, container_id, *, evidence):
    """Attach/start only the bound container; closing this is not termination proof."""
    process = await backend.attach_start(container_id)
    try:
        async with byte_streams(process.stdout, process.stdin, process.stderr, evidence=evidence) as streams:
            yield streams
    finally:
        async def close_attachment():
            process.stdin.close()
            with suppress(TimeoutError, OSError):
                await asyncio.wait_for(process.wait(), 1)
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                with suppress(TimeoutError):
                    await asyncio.wait_for(process.wait(), 2)
        from hyperclaw.execution import settle
        await settle(asyncio.create_task(close_attachment()))
