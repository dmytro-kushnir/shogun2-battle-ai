/* shogun2-battle-ai native module: makes battles WITHOUT a battle script (custom and campaign battles) load ours.
 *
 * Loaded from the main menu's Lua (frontend_scripted.lua) with package.loadlib; the engine's Lua calls our
 * exported functions as lua_CFunctions. We never touch the Lua API (it is statically linked, not exported):
 * the functions take the lua_State* and return 0 results.
 *
 * What it patches (docs/engine-notes.md; same RVAs in CE.dll and empire.retail.x64.dll, found by signature):
 *   battle env init:  cmp word [rsi+0x28128],0 ; jbe skip          <- empty script path -> no battle script
 *   P1: jbe skip (76 xx)                       -> 66 90 (nop): never skip
 *   P2: lea rdx,[rsi+0x28128] (7 bytes)        -> call stub ; 66 90
 *       stub: lea rdx,[rsi+disp] ; cmp word [rdx],0 ; jne done ; mov rdx,&g_fake ; done: ret
 *   so a battle with a script keeps it, and an empty path becomes ours. The path copy (0x199df0) only reads
 *   the wchar_t* at +0x18 of the engine string, so g_fake only needs that pointer.
 * Nothing is written unless both signatures match exactly once and every expected byte is in place. The patch
 * lives in memory only and lasts until the game exits; the engine files on disk are never touched.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

#define EXPORT __declspec(dllexport)

static wchar_t g_repo[MAX_PATH];
static const wchar_t g_script[] = L"data/s2ai/custom.battle_script"; /* shipped in s2ai_battle.pack */

/* engine wide string: {u64 len; u64 cap; Alloc* alloc; wchar_t* data} */
typedef struct { uint64_t len, cap; void *alloc; const wchar_t *data; } ENGINE_WSTR;
static ENGINE_WSTR g_fake;

/* ------------------------------------------------------------------ log */

static void logf_(const char *fmt, ...) {
    wchar_t path[MAX_PATH + 32];
    _snwprintf(path, MAX_PATH + 32, L"%ls\\runtime\\native.log", g_repo);
    FILE *f = _wfopen(path, L"a");
    if (!f) return;
    SYSTEMTIME t; GetLocalTime(&t);
    fprintf(f, "%04d-%02d-%02d %02d:%02d:%02d ", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
    va_list ap; va_start(ap, fmt); vfprintf(f, fmt, ap); va_end(ap);
    fputc('\n', f);
    fclose(f);
}

/* <repo>\native\s2ai_native.dll -> <repo> */
static void find_repo(void) {
    HMODULE self = NULL;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCWSTR)&find_repo, &self);
    GetModuleFileNameW(self, g_repo, MAX_PATH);
    for (int up = 0; up < 2; up++) {
        wchar_t *slash = wcsrchr(g_repo, L'\\');
        if (slash) *slash = 0;
    }
}

/* ------------------------------------------------------------------ engine module + signatures */

static uint8_t *g_base;
static size_t g_text_off, g_text_size;
static const char *g_engine_name;

static int find_engine(void) {
    static const wchar_t *names[] = { L"CE.dll", L"empire.retail.x64.dll" };
    static const char *anames[] = { "CE.dll", "empire.retail.x64.dll" };
    for (int i = 0; i < 2; i++) {
        HMODULE m = GetModuleHandleW(names[i]);
        if (m) { g_base = (uint8_t *)m; g_engine_name = anames[i]; break; }
    }
    if (!g_base) return 0;
    IMAGE_DOS_HEADER *dos = (IMAGE_DOS_HEADER *)g_base;
    IMAGE_NT_HEADERS64 *nt = (IMAGE_NT_HEADERS64 *)(g_base + dos->e_lfanew);
    IMAGE_SECTION_HEADER *sec = IMAGE_FIRST_SECTION(nt);
    for (int i = 0; i < nt->FileHeader.NumberOfSections; i++) {
        if (memcmp(sec[i].Name, ".text", 5) == 0) {
            g_text_off = sec[i].VirtualAddress;
            g_text_size = sec[i].Misc.VirtualSize;
            return 1;
        }
    }
    return 0;
}

/* pattern: hex bytes, "??" = any. Returns the number of matches; *first = first match. */
static int scan(const char *pattern, uint8_t **first) {
    uint8_t bytes[64]; uint8_t mask[64]; int n = 0;
    for (const char *p = pattern; *p && n < 64;) {
        while (*p == ' ') p++;
        if (!*p) break;
        if (p[0] == '?') { mask[n] = 0; bytes[n] = 0; p += 2; }
        else { mask[n] = 1; bytes[n] = (uint8_t)strtoul((char[]){p[0], p[1], 0}, NULL, 16); p += 2; }
        n++;
    }
    int hits = 0;
    uint8_t *start = g_base + g_text_off, *end = start + g_text_size - n;
    for (uint8_t *s = start; s <= end; s++) {
        int i = 0;
        while (i < n && (!mask[i] || s[i] == bytes[i])) i++;
        if (i == n) { if (!hits) *first = s; hits++; }
    }
    return hits;
}

static const char *SIG_P1 = "80 BE ?? ?? ?? ?? 00 75 ?? 66 83 BE ?? ?? ?? ?? 00 76 ?? B9 30 09 00 00 E8";
static const char *SIG_P2 = "48 8D 96 ?? ?? ?? ?? 48 8D 4D ?? E8 ?? ?? ?? ?? 48 8B 8E ?? ?? ?? ?? BB 01 00 00 00 E8";

/* executable page within +-2 GB of `near_addr` (a rel32 call has to reach it) */
static uint8_t *alloc_near(uint8_t *near_addr) {
    SYSTEM_INFO si; GetSystemInfo(&si);
    uintptr_t gran = si.dwAllocationGranularity;
    uintptr_t base = (uintptr_t)near_addr & ~(gran - 1);
    for (uintptr_t d = gran; d < 0x7FF00000; d += gran) {
        uintptr_t tries[2] = { base + d, base - d };
        for (int i = 0; i < 2; i++) {
            if (i == 1 && d > base) continue;
            void *p = VirtualAlloc((void *)tries[i], 4096, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
            if (p) return (uint8_t *)p;
        }
    }
    return NULL;
}

static int write_code(uint8_t *at, const uint8_t *bytes, size_t n) {
    DWORD old;
    if (!VirtualProtect(at, n, PAGE_EXECUTE_READWRITE, &old)) return 0;
    memcpy(at, bytes, n);
    VirtualProtect(at, n, old, &old);
    FlushInstructionCache(GetCurrentProcess(), at, n);
    return 1;
}

static int g_patched;

static void patch(void) {
    if (!find_engine()) { logf_("engine DLL not found in the process - nothing patched"); return; }
    uint8_t *m1 = NULL, *m2 = NULL;
    int h1 = scan(SIG_P1, &m1), h2 = scan(SIG_P2, &m2);
    uint8_t *p1 = m1 ? m1 + 0x11 : NULL, *p2 = m2;
    logf_("engine %s at %p; P1 sig hits=%d rva=%#llx, P2 sig hits=%d rva=%#llx", g_engine_name, (void *)g_base,
          h1, p1 ? (unsigned long long)(p1 - g_base) : 0ULL, h2, p2 ? (unsigned long long)(p2 - g_base) : 0ULL);

    /* already patched (the main menu runs its script again after every battle)? */
    if (h1 == 0 && h2 == 0 && g_patched) { logf_("already patched"); return; }
    if (h1 != 1 || h2 != 1) { logf_("signatures not unique - nothing patched"); return; }
    if (p1[0] != 0x76) { logf_("P1: expected jbe (76), found %02x - nothing patched", p1[0]); return; }
    if (p2 <= p1 || p2 - p1 > 0x40) { logf_("P2 not right after P1 (%lld bytes) - nothing patched", (long long)(p2 - p1)); return; }
    if (memcmp(p1 - 8, "\x66\x83\xBE", 3) != 0) { logf_("P1 not preceded by cmp word [rsi+..] - nothing patched"); return; }
    /* the cmp and the lea must address the same field */
    if (memcmp(p1 - 5, p2 + 3, 4) != 0) { logf_("cmp and lea use different offsets - nothing patched"); return; }

    uint8_t *stub = alloc_near(p2);
    if (!stub) { logf_("no executable page within 2 GB - nothing patched"); return; }
    int64_t rel = (int64_t)(stub - (p2 + 5));
    if (rel > INT32_MAX || rel < INT32_MIN) { logf_("stub out of rel32 range - nothing patched"); return; }

    g_fake.len = g_fake.cap = wcslen(g_script);
    g_fake.alloc = NULL;
    g_fake.data = g_script;

    /* stub: lea rdx,[rsi+disp] ; cmp word [rdx],0 ; jne +10 ; mov rdx, imm64 ; ret */
    uint8_t s[32]; int n = 0;
    memcpy(s + n, p2, 7); n += 7;                                  /* the original lea, displacement and all */
    memcpy(s + n, "\x66\x83\x3A\x00", 4); n += 4;                  /* cmp word [rdx], 0 */
    memcpy(s + n, "\x75\x0A", 2); n += 2;                          /* jne done */
    s[n++] = 0x48; s[n++] = 0xBA;                                  /* mov rdx, imm64 */
    uint64_t fake = (uint64_t)(uintptr_t)&g_fake; memcpy(s + n, &fake, 8); n += 8;
    s[n++] = 0xC3;                                                  /* done: ret */
    memcpy(stub, s, n);
    DWORD old;
    if (!VirtualProtect(stub, 4096, PAGE_EXECUTE_READ, &old)) {  /* no writable+executable page left behind */
        VirtualFree(stub, 0, MEM_RELEASE);
        logf_("could not make the stub read-only - nothing patched");
        return;
    }
    FlushInstructionCache(GetCurrentProcess(), stub, n);

    uint8_t call5[7] = { 0xE8, 0, 0, 0, 0, 0x66, 0x90 };
    int32_t rel32 = (int32_t)rel; memcpy(call5 + 1, &rel32, 4);
    static const uint8_t nop2[2] = { 0x66, 0x90 };
    /* P2 first: with P1 still in place an empty path is skipped before reaching P2 */
    if (!write_code(p2, call5, 7)) { logf_("P2 write failed - nothing patched"); return; }
    if (!write_code(p1, nop2, 2)) { logf_("P1 write failed (P2 is in place but harmless)"); return; }
    g_patched = 1;

    char script[260]; WideCharToMultiByte(CP_UTF8, 0, g_script, -1, script, sizeof script, NULL, NULL);
    logf_("PATCHED: battles without a script now load '%s' (stub %p)", script, (void *)stub);
}

/* ------------------------------------------------------------------ export (a lua_CFunction) */

EXPORT int luaopen_s2ai_native(void *L) {
    (void)L;
    if (!g_repo[0]) find_repo();
    patch();
    return 0;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r) { (void)h; (void)reason; (void)r; return TRUE; }
