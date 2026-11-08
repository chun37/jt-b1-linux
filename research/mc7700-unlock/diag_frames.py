"""Pure Qualcomm DIAG framing helpers; no device I/O."""
import struct

def crc16(data):
    c=0xffff
    for byte in data:
        c^=byte
        for _ in range(8):c=(c>>1)^0x8408 if c&1 else c>>1
    return (~c)&0xffff

def frame(data):
    data+=struct.pack('<H',crc16(data))
    return b'\x7e'+data.replace(b'\x7d',b'\x7d\x5d').replace(b'\x7e',b'\x7d\x5e')+b'\x7e'

def unframe(data):
    out=bytearray();escape=False
    for x in data:
        if escape:out.append(x^0x20);escape=False
        elif x==0x7d:escape=True
        else:out.append(x)
    if escape or len(out)<3 or crc16(out[:-2])!=struct.unpack('<H',out[-2:])[0]:return None
    return bytes(out[:-2])
