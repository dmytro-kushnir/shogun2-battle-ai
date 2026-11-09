# Engine notes: battle-script creation (static analysis)

Target: `RemasteredCE\CE.dll` (x64, ImageBase 0x180000000). All addresses are **RVAs**.
`CE.dll` is the vanilla `empire.retail.x64.dll` with about 150 small patches (jumps into its own `.cepath` section), so **.text RVAs are the same in both**.
Every site below has the same bytes in both DLLs, and the signatures match once in each. Tools: `tools/re/pe_util.py`, `tools/re/find_patch_sites.py` (re-finds the sites by signature), `tools/re/sig.py`.

## How a battle script gets created

1. **Setup parse.** `BATTLE_SETUP_INFO` ctor-from-XML at `0x1d7840` (`this`=rcx, r9 = setup XML path).
   - Script path = `setup+0x98`. This is an engine wide string, 0x20 bytes: `{u64 len; u64 cap; Alloc* alloc; wchar_t* data}`. Default ctor `0x199580` points `data` at the empty literal `0x2ea4dd4`.
   - At `0x1d7d60` the ctor reads the `<battle_script>` node.
   - If the name has no `.`, it appends `".battle_script"` (`0x1d7e83`).
   - If the name has no `\` or `/`, it prepends the directory of the setup XML (`0x1d7f59..0x1d80b7`).
   - It also reads `prepare_for_fade_in` into `setup+0xb8`.
   - Custom and campaign battles leave `len == 0`.
2. **Battle env init** is the big function containing `0x3452fd`; the chunk is listed at `0x343220`, `rsi` = env. The env holds a copy of `BATTLE_SETUP_INFO` at `env+0x28090`, so the script path is at `env+0x28128`.
   - `0x343520`: `env+0x284a0 = new LuaEnv("data/battle_scripted.lua")` (ctor `0xe89820`). This happens for every battle.
   - **Skip decision** (`0x3452f4`):
     ```
     003452f4 80 BE C8 83 02 00 00    cmp  byte ptr [rsi+0x283c8], 0   ; unknown flag (replay/MP?) -> skip
     003452fb 75 73                   jne  0x345370
     003452fd 66 83 BE 28 81 02 00 00 cmp  word ptr [rsi+0x28128], 0   ; script path empty?
     00345305 76 69                   jbe  0x345370                     ; <-- empty -> no battle script
     00345307 B9 30 09 00 00          mov  ecx, 0x930
     0034530c E8 9F 58 E4 FF          call 0x18abb0                     ; engine operator new
     00345311 48 8B F8                mov  rdi, rax
     00345314 48 85 C0                test rax, rax
     00345317 74 39                   je   0x345352
     00345319 48 8D 96 28 81 02 00    lea  rdx, [rsi+0x28128]           ; <-- source path
     00345320 48 8D 4D 88             lea  rcx, [rbp-0x78]
     00345324 E8 C7 4A E5 FF          call 0x199df0                     ; tmp = copy(path) (reads only src+0x18 = wchar_t*)
     00345329 48 8B 8E A0 84 02 00    mov  rcx, [rsi+0x284a0]           ; battle_scripted.lua LuaEnv
     00345330 BB 01 00 00 00          mov  ebx, 1
     00345335 E8 F6 51 52 01          call 0x186a530                    ; get/create its Lua wrapper
     0034533a 4C 8D 4D 88             lea  r9, [rbp-0x78]
     0034533e 89 5C 24 20             mov  [rsp+0x20], ebx              ; 1
     00345342 4C 8B C0                mov  r8, rax
     00345345 48 8B D6                mov  rdx, rsi                     ; env
     00345348 48 8B CF                mov  rcx, rdi
     0034534b E8 90 2B F7 FF          call 0x2b7ee0                     ; BattleScriptInterface::ctor
     0034535b 48 89 86 A8 84 02 00    mov  [rsi+0x284a8], rax           ; env->battle_script
     ```
3. **`BattleScriptInterface`** (size 0x930, ctor `0x2b7ee0(this, env, parentLuaWrapper, const wstr* path, int 1)`):
   - Its base is the `LuaEnv` ctor `0x1858b40`, called with `register_cb = 0x2bfec0` and parent = the battle_scripted.lua wrapper.
   - It stores `env` at `+0x748` and the path at `+0x760`. At the end it sets the global singleton **`[0x2ebe330] = this`** (`0x2b8a05`).
4. **Per-frame:** the env update `0x420cb0` calls `0x31be60(env->[0x284a8])` when that pointer is non-null.
   - On the first tick it calls vfunc `+0x10` = `0x2c4da0`. That function opens the file at `+0x760` through the filesystem (`0x1912020`, vtable `+0x100`).
   - Then it gets the `lua_State` (`0x186b1c0` → `0x186a530`), calls `luaL_loadbuffer` (`0x12cb1b0`), runs the chunk (`0x1870db0`), and on failure logs "LUA compile error:".
5. **Lua state model:**
   - All engine scripts are threads of one master `lua_State` (global `0x3381350`, created in `0x186a260`).
   - For each `LuaEnv`, `0x186a260` makes `lua_newthread` with a fresh globals table. Its `__index`/`lookup` goes to the parent env, so **the battle script can read battle_scripted.lua's globals** (this is inferred from the code, not tested).
   - It then calls `register_cb(L)`.
   - `0x2bfec0(lua_State*)` registers all battle classes: battle_vector `0x2c4700`, battle_location, battle.alliances/alliance/armies/army, battle.unit_controller `0x2c43b0`, battle.ai_unit_controller, ship_*, units/unit, buildings, camera, sound, commandevent, weather, subtitles, ui_component, **empire_battle `0x2c1f40`**, battle_marker `0x2c25e0`.
6. Debug tweak `reload_battle_script` (bool at `0x2f15868`, registered in `0x2fde0`): `0x421a30..0x421aeb` in `0x420cb0` repeats the same empty check and creation. That is the only other creation path.

## Proposed intervention (a): substitute a path when empty (recommended)

Two patches in .text (VirtualProtect → write → restore → FlushInstructionCache), plus a 24-byte stub that must be within ±2 GB of the module. To place the stub, VirtualAlloc a page near ImageBase (try `base - k*64KB` downward), the way MinHook does. The largest int3 runs in .text are only 21 bytes, so there is no handy in-module cave.

| # | RVA | original | patched | meaning |
|---|-----|----------|---------|---------|
| P1 | `0x345305` | `76 69` | `66 90` | never skip on empty path (the replay/MP flag check at `0x3452f4` stays) |
| P2 | `0x345319` | `48 8D 96 28 81 02 00` | `E8 <rel32 stub> 66 90` | rel32 = stub − (base+0x34531E) |

Stub (it clobbers only rdx and the flags, which are dead here; it does not touch the stack):
```
48 8D 96 28 81 02 00     lea  rdx, [rsi+0x28128]   ; original instruction
66 83 3A 00              cmp  word ptr [rdx], 0
75 0A                    jne  done
48 BA <imm64>            mov  rdx, &g_fake_wstr
C3               done:   ret
```
`g_fake_wstr` is 0x20 bytes in our DLL: `{len, len, base+0x2e41a00, L"data/…/our_script.lua"}`. `0x199df0` only reads `+0x18` (a NUL-terminated `wchar_t*`), so only the data pointer must be valid. Battles that already have a script (historical) are unchanged.

Signatures (each matches exactly once in both DLLs):
- P1: `80 BE ?? ?? ?? ?? 00 75 ?? 66 83 BE ?? ?? ?? ?? 00 76 ?? B9 30 09 00 00 E8` (+0x11 = the jbe)
- P2: `48 8D 96 ?? ?? ?? ?? 48 8D 4D ?? E8 ?? ?? ?? ?? 48 8B 8E ?? ?? ?? ?? BB 01 00 00 00 E8` (+0)

Confidence: **high** that this is the only gate for normal battle start and that the patch makes the engine construct and tick a BattleScriptInterface with our file. **Medium** on the path format; see unknowns.

Variant: instead of P2, assign our path into the env string, using `0x1f9c80` = `wstr::assign(wstr* dst, const wstr* src)`. Then the `reload_battle_script` path would also work. The downside is that it changes the env's copy of the setup, which other code may read (`0x382a00`, `0x3f22e0`, `0x421ed0` and others take `env+0x28090`).

## (b) Calling the constructor ourselves: possible, but needs `env`
Reproduce the sequence above:
```
obj = 0x18abb0(0x930)
0x199df0(&tmp, &fake)
w = 0x186a530(env->[0x284a0])
0x2b7ee0(obj, env, w, &tmp, 1)
env->[0x284a8] = obj
```
The ticking then happens automatically. However, no global battle-env pointer was found. `env` is only reachable through `[0x2ebe330]+0x748`, which needs an existing script object. You would have to hook something anyway, so (a) is simpler.

## (c) Registering the classes into battle_scripted.lua: not viable alone
`0x2bfec0(L)` would register the classes. However, the bindings dereference the singleton with no null check, for example `0x297840`: `mov rax,[0x2ebe330]; mov rcx,[rax+0x748]`. Without a BattleScriptInterface they crash.

## Unknowns / to verify at runtime
- **Path format** given to the filesystem. Engine literals use `data/...` (`data/battle_scripted.lua`, `data/TestData/world/DemoLandBattle.xml`), so try `L"data/<dir>/<file>.lua"` first. If it fails, try it without `data/`. You can check what a historical battle produces by reading `obj+0x760` (or `env+0x28128`) at runtime.
- Meaning of `env+0x283c8` (copied in `0x345840` from a battle-start parameter struct). It is probably replay or multiplayer, but this is unconfirmed. If it is set for campaign battles, P1 alone will not help; check at runtime.
- Whether custom and campaign battles reach this path at all: they should, because `battle_scripted.lua` is created in the same function a few hundred instructions earlier.
- Side effects of a script existing in non-scripted battles (`prepare_for_fade_in` stays at its default; script-only UI flows), and singleton lifetime on battle exit (the destructor was not analysed).
- Run order: the battle-script chunk runs on the first env update tick (`0x420cb0`), which is after battle_scripted.lua was created. The exact ordering against battle_scripted.lua's own execution was not checked.
- The debug tweak "Saves battle xml to logs/battle.xml" (string `0x2187150`) could dump a custom battle's setup for inspection.
