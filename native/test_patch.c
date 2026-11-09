/* Offline test of s2ai_native.dll: maps the real engine DLL WITHOUT running it (DONT_RESOLVE_DLL_REFERENCES:
 * no DllMain, no dependencies), loads our module, calls luaopen_s2ai_native, and dumps what changed.
 *   test_patch.exe <path to CE.dll or empire.retail.x64.dll>
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <wchar.h>

typedef int (*lua_cfn)(void *);
typedef struct { uint64_t len, cap; void *alloc; const wchar_t *data; } ENGINE_WSTR;

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
    printf("engine mapped at %p\n", (void *)base);
    hex("before P1 @0x345305:", base + 0x345305, 2);
    hex("before P2 @0x345319:", base + 0x345319, 7);

    HMODULE nat = LoadLibraryA("s2ai_native.dll");
    if (!nat) { printf("cannot load s2ai_native.dll (%lu)\n", GetLastError()); return 1; }
    lua_cfn open = (lua_cfn)GetProcAddress(nat, "luaopen_s2ai_native");
    open(NULL);
    open(NULL); /* second call must be a no-op */

    hex("after  P1 @0x345305:", base + 0x345305, 2);
    hex("after  P2 @0x345319:", base + 0x345319, 7);
    if (base[0x345319] != 0xE8) { printf("NOT PATCHED\n"); return 1; }
    int32_t rel; memcpy(&rel, base + 0x34531a, 4);
    uint8_t *stub = base + 0x34531e + rel;
    printf("stub at %p (distance %lld)\n", (void *)stub, (long long)(stub - base));
    hex("stub:", stub, 24);
    uint64_t fake_addr; memcpy(&fake_addr, stub + 15, 8);
    ENGINE_WSTR *fake = (ENGINE_WSTR *)(uintptr_t)fake_addr;
    printf("fake string: len=%llu data=%ls\n", (unsigned long long)fake->len, fake->data);
    printf("OK\n");
    return 0;
}
