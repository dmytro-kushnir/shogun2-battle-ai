from pe_util import *; import sys
img=Img(CE)
for t in [int(x,16) for x in sys.argv[1:]]:
    for c in img.calls_to(t):
        print(hex(t),'caller',hex(c),'func',hex(img.func_of(c) or 0))
