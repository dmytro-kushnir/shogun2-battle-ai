from pe_util import *; import struct
img=Img(CE)
for a in (0x2171fb0,0x2170fb8): print(hex(a), img.cstr_at(a), img.mem[a:a+24])
vt=0x2170aa0
for i in range(8):
    p=struct.unpack_from('<Q',img.mem,vt+i*8)[0]; print(i, hex(p-img.base))
