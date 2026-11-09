import sys; from pe_util import *
img=Img(CE)
print(img.secs, hex(img.base))
for s in ["battle_script",".battle_script","reload_battle_script","BATTLE_SETUP_INFO","empire_battle","battle_scripted.lua","battle.unit_controller","battle_vector","prepare_for_fade_in"]:
    for a in img.find_cstr(s):
        refs=img.riprefs(a)
        print(s, hex(a), [ (hex(r),hex(img.func_of(r) or 0)) for r,_ in refs][:20])
    for a in img.find_wstr(s):
        refs=img.riprefs(a)
        print('W',s, hex(a), [ (hex(r),hex(img.func_of(r) or 0)) for r,_ in refs][:20])
