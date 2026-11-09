from pe_util import *; import sys
img=Img(CE)
for t in [int(x,16) for x in sys.argv[1:]]:
    for r,imm in img.riprefs(t):
        f=img.func_of(r); print(hex(t), hex(r), imm, 'func', hex(f or 0))
