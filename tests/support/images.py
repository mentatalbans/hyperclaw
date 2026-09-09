"""Deterministic synthetic image; no downloaded or personal image data."""
import struct
import zlib


def png(color=(235, 20, 20), width=96, height=96):
    def chunk(name,data):
        return struct.pack('>I',len(data))+name+data+struct.pack('>I',zlib.crc32(name+data))
    raw = b''.join(b'\0'+bytes(color)*width for _ in range(height))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')
