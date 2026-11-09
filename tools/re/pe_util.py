"""Small static-analysis helpers for the Shogun 2 engine DLLs (read-only).

Usage as module: from pe_util import Img; img = Img(path)
"""
import sys, struct, functools
import numpy as np
import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

CE = r"F:\SteamLibrary\steamapps\common\Shogun 2 Complete Edition\RemasteredCE\CE.dll"
VANILLA = r"F:\SteamLibrary\steamapps\common\Shogun 2 Complete Edition\empire.retail.x64.dll"


class Img:
    def __init__(self, path):
        self.pe = pefile.PE(path, fast_load=True)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        # build a flat image (RVA-indexed)
        size = self.pe.OPTIONAL_HEADER.SizeOfImage
        mem = bytearray(size)
        mem[: self.pe.OPTIONAL_HEADER.SizeOfHeaders] = self.pe.__data__[: self.pe.OPTIONAL_HEADER.SizeOfHeaders]
        self.secs = []
        for s in self.pe.sections:
            name = s.Name.rstrip(b"\0").decode()
            raw = s.get_data()
            mem[s.VirtualAddress : s.VirtualAddress + len(raw)] = raw[: size - s.VirtualAddress]
            self.secs.append((name, s.VirtualAddress, max(s.Misc_VirtualSize, len(raw)), s.Characteristics))
        self.mem = bytes(mem)
        self.np = np.frombuffer(self.mem, dtype=np.uint8)
        self.cs = Cs(CS_ARCH_X86, CS_MODE_64)
        self.cs.detail = False
        t = [s for s in self.secs if s[0] == ".text"][0]
        self.text = (t[1], t[1] + t[2])
        self._pdata = None

    def find_all(self, needle, start=0, end=None):
        end = end or len(self.mem)
        out, i = [], self.mem.find(needle, start, end)
        while i != -1:
            out.append(i)
            i = self.mem.find(needle, i + 1, end)
        return out

    def find_cstr(self, s):
        """exact NUL-terminated (preceded by NUL) ascii string"""
        b = s.encode() + b"\0"
        return [i for i in self.find_all(b) if self.mem[i - 1] == 0]

    def find_wstr(self, s):
        b = s.encode("utf-16le") + b"\0\0"
        return self.find_all(b)

    @functools.lru_cache(maxsize=None)
    def _disp_arrays(self):
        a, b = self.text
        out = []
        for k in range(4):
            seg = self.mem[a + k : b]
            n = len(seg) // 4
            d = np.frombuffer(seg[: n * 4], dtype="<i4").astype(np.int64)
            pos = a + k + np.arange(n, dtype=np.int64) * 4  # RVA of disp field
            out.append(d + pos + 4)  # target if disp is last 4 bytes of insn
        return out

    def riprefs(self, target, slack=(0, 1, 4)):
        """RVAs of disp32 fields that reference target (insn end = disp+4+imm_size)."""
        a, _ = self.text
        res = []
        for k, tgt in enumerate(self._disp_arrays()):
            for imm in slack:
                idx = np.nonzero(tgt + imm == target)[0]
                for j in idx:
                    res.append((a + k + int(j) * 4, imm))
        return sorted(res)

    def calls_to(self, target):
        """E8 rel32 callers"""
        res = []
        for pos, imm in self.riprefs(target, slack=(0,)):
            if self.mem[pos - 1] == 0xE8:
                res.append(pos - 1)
            elif self.mem[pos - 1] == 0xE9:
                res.append(pos - 1)
        return res

    def pdata(self):
        if self._pdata is None:
            d = self.pe.OPTIONAL_HEADER.DATA_DIRECTORY[3]
            arr = np.frombuffer(self.mem[d.VirtualAddress : d.VirtualAddress + d.Size], dtype="<u4").reshape(-1, 3)
            self._pdata = arr
        return self._pdata

    def func_of(self, rva):
        p = self.pdata()
        i = np.searchsorted(p[:, 0], rva, side="right") - 1
        if i < 0:
            return None
        b, e, u = (int(x) for x in p[i])
        if b <= rva < e:
            # follow chained unwind info to the primary entry
            return self._primary(b, e, u)
        return None

    def _primary(self, b, e, u):
        # chained unwind: flags & UNW_FLAG_CHAININFO (4)
        seen = 0
        while seen < 32:
            ver_flags = self.mem[u]
            if (ver_flags >> 3) & 4:
                cnt = self.mem[u + 2]
                off = u + 4 + ((cnt + 1) & ~1) * 2
                b, e, u = struct.unpack_from("<III", self.mem, off)
                seen += 1
            else:
                break
        return b

    def func_range(self, start):
        p = self.pdata()
        i = np.searchsorted(p[:, 0], start, side="left")
        return int(p[i][0]), int(p[i][1])

    def dis(self, start, end=None, count=None):
        end = end or start + 0x200
        out = []
        for ins in self.cs.disasm(self.mem[start:end], start):
            out.append(ins)
            if count and len(out) >= count:
                break
        return out

    def cstr_at(self, rva, maxlen=200):
        if not (0 <= rva < len(self.mem)):
            return None
        e = self.mem.find(b"\0", rva, rva + maxlen)
        if e <= rva:
            return None
        s = self.mem[rva:e]
        if all(32 <= c < 127 or c in (9, 10, 13) for c in s) and len(s) >= 3:
            return s.decode()
        # utf16?
        e = rva
        while e + 1 < len(self.mem) and e - rva < maxlen * 2 and (self.mem[e] or self.mem[e + 1]):
            e += 2
        s = self.mem[rva:e]
        if len(s) >= 6 and all(s[i + 1] == 0 and 32 <= s[i] < 127 for i in range(0, len(s), 2)):
            return "L" + repr(s.decode("utf-16le"))
        return None

    def pretty(self, start, end, extra=None):
        lines = []
        for ins in self.cs.disasm(self.mem[start:end], start):
            ann = ""
            op = ins.op_str
            if "rip +" in op or "rip -" in op:
                import re
                m = re.search(r"rip ([+-]) (0x[0-9a-f]+)", op)
                d = int(m.group(2), 16) * (1 if m.group(1) == "+" else -1)
                t = ins.address + ins.size + d
                s = self.cstr_at(t)
                ann = f"  ; ->{t:#x}" + (f" {s!r}" if s else "")
            elif ins.mnemonic in ("call", "jmp") and op.startswith("0x"):
                t = int(op, 16)
                if extra and t in extra:
                    ann = f"  ; {extra[t]}"
            lines.append(f"{ins.address:08x}: {ins.bytes.hex():<24} {ins.mnemonic} {op}{ann}")
        return "\n".join(lines)
