/* Offline test of s2ai_native.dll: maps the real engine DLL WITHOUT running it (DONT_RESOLVE_DLL_REFERENCES:
 * no DllMain, no dependencies), finds the patch sites by the same signatures the module uses, loads the module,
 * calls luaopen_s2ai_native twice (the second call must change nothing) and checks the result.
 *   test_patch.exe <path to CE.dll or empire.retail.x64.dll>
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

typedef int (*lua_cfn)(void *);
typedef struct { uint64_t len, cap; void *alloc; const wchar_t *data; } ENGINE_WSTR;

static const char *SIG_P1 = "80 BE ?? ?? ?? ?? 00 75 ?? 66 83 BE ?? ?? ?? ?? 00 76 ?? B9 30 09 00 00 E8";
static const char *SIG_P2 = "48 8D 96 ?? ?? ?? ?? 48 8D 4D ?? E8 ?? ?? ?? ?? 48 8B 8E ?? ?? ?? ?? BB 01 00 00 00 E8";

static int scan(uint8_t *start, size_t size, const char *pattern, uint8_t **first) {
    uint8_t bytes[64], mask[64]; int n = 0;
    for (const char *p = pattern; *p && n < 64;) {
        while (*p == ' ') p++;
        if (!*p) break;
        if (p[0] == '?') { mask[n] = 0; bytes[n] = 0; }
        else { char h[3] = { p[0], p[1], 0 }; mask[n] = 1; bytes[n] = (uint8_t)strtoul(h, NULL, 16); }
        p += 2; n++;
    }
    int hits = 0;
    for (uint8_t *s = start; s + n <= start + size; s++) {
        int i = 0;
        while (i < n && (!mask[i] || s[i] == bytes[i])) i++;
        if (i == n) { if (!hits) *first = s; hits++; }
    }
    return hits;
}

static void hex(const char *label, const uint8_t *p, int n) {
    printf("%s", label);
    for (int i = 0; i < n; i++) printf(" %02x", p[i]);
    printf("\n");
}

int main(int argc, char **argv) {
    if (argc < 2) { printf("usage: test_patch <engine dll>\n"); return 2; }
    HMODULE eng = LoadLibraryExA(argv[1], NULL, DONT_RESOLVE_DLL_REFERENCES);
    if (!eng) { printf("cannot map %s (%lu)\n", argv[1], GetLastError()); return 1; }
    uint8_t *base = (uint8_t *)eng;
    IMAGE_NT_HEADERS64 *nt = (IMAGE_NT_HEADERS64 *)(base + ((IMAGE_DOS_HEADER *)base)->e_lfanew);
    IMAGE_SECTION_HEADER *sec = IMAGE_FIRST_SECTION(nt);
    uint8_t *text = NULL; size_t text_size = 0;
    for (int i = 0; i < nt->FileHeader.NumberOfSections; i++)
        if (memcmp(sec[i].Name, ".text", 5) == 0) { text = base + sec[i].VirtualAddress; text_size = sec[i].Misc.VirtualSize; }
    uint8_t *m1 = NULL, *m2 = NULL;
    int h1 = scan(text, text_size, SIG_P1, &m1), h2 = scan(text, text_size, SIG_P2, &m2);
    printf("engine mapped at %p; signature hits P1=%d P2=%d\n", (void *)base, h1, h2);
    if (h1 != 1 || h2 != 1) { printf("FAIL: signatures not unique in this engine build\n"); return 1; }
    uint8_t *p1 = m1 + 0x11, *p2 = m2;
    printf("P1 rva %#llx, P2 rva %#llx\n", (unsigned long long)(p1 - base), (unsigned long long)(p2 - base));
    hex("before P1:", p1, 2);
    hex("before P2:", p2, 7);
    uint8_t orig_lea[7]; memcpy(orig_lea, p2, 7);

    HMODULE nat = LoadLibraryA("s2ai_native.dll");
    if (!nat) { printf("cannot load s2ai_native.dll (%lu)\n", GetLastError()); return 1; }
    lua_cfn open = (lua_cfn)GetProcAddress(nat, "luaopen_s2ai_native");
    open(NULL);
    uint8_t after1[9]; memcpy(after1, p1, 2); memcpy(after1 + 2, p2, 7);
    open(NULL); /* second call must be a no-op */

    hex("after  P1:", p1, 2);
    hex("after  P2:", p2, 7);
    if (memcmp(after1, p1, 2) || memcmp(after1 + 2, p2, 7)) { printf("FAIL: second call changed the code\n"); return 1; }
    if (p1[0] != 0x66 || p1[1] != 0x90 || p2[0] != 0xE8 || p2[5] != 0x66 || p2[6] != 0x90) { printf("FAIL: not patched as expected\n"); return 1; }
    int32_t rel; memcpy(&rel, p2 + 1, 4);
    uint8_t *stub = p2 + 5 + rel;
    printf("stub at %p (%lld bytes from the engine)\n", (void *)stub, (long long)(stub - base));
    hex("stub:", stub, 24);
    if (memcmp(stub, orig_lea, 7) != 0) { printf("FAIL: stub does not start with the original lea\n"); return 1; }
    MEMORY_BASIC_INFORMATION mbi;
    VirtualQuery(stub, &mbi, sizeof mbi);
    if (mbi.Protect != PAGE_EXECUTE_READ) { printf("FAIL: stub page protection %#lx, expected execute-read\n", mbi.Protect); return 1; }
    uint64_t fake_addr; memcpy(&fake_addr, stub + 15, 8);
    ENGINE_WSTR *fake = (ENGINE_WSTR *)(uintptr_t)fake_addr;
    printf("fake string: len=%llu data=%ls\n", (unsigned long long)fake->len, fake->data);
    printf("OK\n");
    return 0;
}
