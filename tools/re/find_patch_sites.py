"""Locate the battle-script patch sites in CE.dll / empire.retail.x64.dll by signature
and print RVAs + original bytes. Read-only. See docs/engine-notes.md."""
import re, sys
from pe_util import Img, CE, VANILLA

SIGS = {
    # cmp byte [rsi+0x283c8],0 ; jne ; cmp word [rsi+0x28128],0 ; jbe skip ; mov ecx,0x930 ; call alloc
    "empty_check": ("80 BE ?? ?? ?? ?? 00 75 ?? 66 83 BE ?? ?? ?? ?? 00 76 ?? B9 30 09 00 00 E8", 0x11),  # -> the jbe
    # lea rdx,[rsi+0x28128] ; lea rcx,[rbp-0x78] ; call wstr_copy ; mov rcx,[rsi+0x284a0] ; mov ebx,1 ; call
    "path_lea": ("48 8D 96 ?? ?? ?? ?? 48 8D 4D ?? E8 ?? ?? ?? ?? 48 8B 8E ?? ?? ?? ?? BB 01 00 00 00 E8", 0),
    # end of BattleScriptInterface ctor: mov [rip+singleton],rdi ; epilogue
    "singleton_store": ("48 89 3D ?? ?? ?? ?? 48 83 C4 20 41 5E 5F 5E C3", 0),
    # BattleScriptInterface ctor: lea r8,[rip+register_battle_classes] ; mov rdx,rax ; mov rcx,rdi ; call LuaEnv::LuaEnv
    "register_cb": ("4C 8D 05 ?? ?? ?? ?? 48 8B D0 48 8B CF E8 ?? ?? ?? ?? 4C 89 77 50", 0),
}

def scan(path):
    img = Img(path)
    a, b = img.text
    print(path)
    for name, (pat, off) in SIGS.items():
        rx = b"".join(b"." if t == "??" else re.escape(bytes([int(t, 16)])) for t in pat.split())
        hits = [a + m.start() for m in re.finditer(rx, img.mem[a:b], re.S)]
        for h in hits:
            site = h + off
            ins = next(img.cs.disasm(img.mem[site:site + 15], site, 1))
            extra = ""
            if name in ("singleton_store", "register_cb"):
                d = int.from_bytes(img.mem[site + 3:site + 7], "little", signed=True)
                extra = f"  target RVA {site + 7 + d:#x}"
            print(f"  {name:16} hits={len(hits)} RVA {site:#x}  {ins.bytes.hex(' ')}  {ins.mnemonic} {ins.op_str}{extra}")

for p in (sys.argv[1:] or [x for x in (CE, VANILLA) if x]):
    scan(p)
