"""Wildcard signature scan: python sig.py ce|vanilla "AA BB ?? CC" ..."""
import sys, re
from pe_util import *
img = Img(CE if sys.argv[1] == "ce" else VANILLA)
a, b = img.text
for pat in sys.argv[2:]:
    rx = b"".join(b"." if t == "??" else re.escape(bytes([int(t, 16)])) for t in pat.split())
    hits = [m.start() for m in re.finditer(rx, img.mem[a:b], re.S)]
    print(pat, "->", [hex(a + h) for h in hits][:10], "count", len(hits))
