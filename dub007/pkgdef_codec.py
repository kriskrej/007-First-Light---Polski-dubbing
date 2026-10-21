# Adapted from derx05/gaming-007FirstLight-toolbox, MIT, Copyright (c) 2026 derx05.
# Pinned upstream bba21551493da9e1877c93782be437e46a29c323; license retained in licenses/toolbox-LICENSE.txt.
"""packagedefinition.txt XTEA codec for 007 First Light (keys from RPKG-Tool first-light branch, src/crypto.cpp).
File = 16-byte header (kept verbatim from the game's own file) + u32 CRC32 of plaintext + XTEA blocks (zero-padded)."""
import struct, zlib, sys
K=[0x71482CF0,0x5FDC4B9F,0x86CE569D,0x0509FC1E]; D=0x61C88647; M=0xFFFFFFFF
def _enc(v0,v1):
    s=0
    for _ in range(32):
        v0=(v0+(((((v1<<4)&M)^(v1>>5))+v1)&M ^ ((s+K[s&3])&M)))&M; s=(s-D)&M
        v1=(v1+(((((v0<<4)&M)^(v0>>5))+v0)&M ^ ((s+K[(s>>11)&3])&M)))&M
    return v0,v1
def _dec(v0,v1):
    s=0xC6EF3720
    for _ in range(32):
        v1=(v1-(((((v0<<4)&M)^(v0>>5))+v0)&M ^ ((s+K[(s>>11)&3])&M)))&M; s=(s+D)&M
        v0=(v0-(((((v1<<4)&M)^(v1>>5))+v1)&M ^ ((s+K[s&3])&M)))&M
    return v0,v1
def decrypt(data):
    hdr=data[:16]; crc=struct.unpack_from('<I',data,16)[0]; body=data[20:]; out=bytearray()
    for i in range(0,len(body)-len(body)%8,8):
        out+=struct.pack('<II',*_dec(*struct.unpack_from('<II',body,i)))
    while out and out[-1]==0: out.pop()
    return hdr,crc,bytes(out)
def encrypt(hdr,plain):
    pad=plain+b'\0'*((-len(plain))%8); out=bytearray()
    for i in range(0,len(pad),8): out+=struct.pack('<II',*_enc(*struct.unpack_from('<II',pad,i)))
    return hdr+struct.pack('<I',zlib.crc32(plain))+bytes(out)
