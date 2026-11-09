import sys; from pe_util import *
img=Img(CE if len(sys.argv)<4 else VANILLA)
a=int(sys.argv[1],16); b=int(sys.argv[2],16)
print(img.pretty(a,b))
