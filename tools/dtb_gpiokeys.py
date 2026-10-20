#!/usr/bin/env python3
"""Make the RG34XX-SP / RG35XX-SP buttons work without the ROCKNIX-only joypad driver.

Reads a .dtb, copies every button (GPIO + evdev code) of the `rocknix-singleadc-joypad` node into a new
standard `gpio-keys` node (same pins, same pull-up pinctrl, same key codes) and disables the joypad node.
The analog sticks (ADC mux) are NOT converted here: the joypad node stays in the tree (disabled), and the userspace
hub overlay/opt/rgos/rgos-sticks reads its amux/io-channels/invert properties from /proc/device-tree and drives the
mux + GPADC itself, so keep this node's properties intact. (The RG35XX-SP has no sticks: its joypad node simply has no
amux properties and the hub runs with `"sticks": {"mode": "none"}`.)

The joypad node is found by name: `rocknix-singleadc-joypad*` first, then any other `rocknix-*joypad*` node. If the
DTB has no such node but already carries a plain `gpio-keys` node with gamepad buttons, it is copied unchanged (nothing
to convert); with neither, the tool stops with an error.

usage: dtb_gpiokeys.py in.dtb out.dtb
"""
import struct, sys

CH = "__children__"


def parse(d):
    magic, _tot, o_st, o_str, o_rsv, _ver, _lc, boot, _s_str, _s_st = struct.unpack_from(">10I", d, 0)
    assert magic == 0xD00DFEED, "not a dtb"
    rsv = d[o_rsv:o_st]
    root = {CH: {}}
    cur, stack, pos = root, [], o_st
    while True:
        t = struct.unpack_from(">I", d, pos)[0]; pos += 4
        if t == 1:
            e = d.index(b"\0", pos); name = d[pos:e].decode(); pos = (e + 1 + 3) & ~3
            n = {CH: {}}; cur[CH][name] = n; stack.append(cur); cur = n
        elif t == 2:
            cur = stack.pop()
        elif t == 3:
            ln, no = struct.unpack_from(">II", d, pos); pos += 8
            e = d.index(b"\0", o_str + no)
            cur[d[o_str + no:e].decode()] = d[pos:pos + ln]; pos = (pos + ln + 3) & ~3
        elif t == 4:
            continue
        elif t == 9:
            break
    return root[CH][""], rsv, boot


def emit(tree, rsv, boot):
    st, strs, offs = bytearray(), bytearray(), {}

    def sid(name):
        if name not in offs:
            offs[name] = len(strs); strs.extend(name.encode() + b"\0")
        return offs[name]

    def pad():
        while len(st) % 4:
            st.append(0)

    def walk(name, n):
        st.extend(struct.pack(">I", 1)); st.extend(name.encode() + b"\0"); pad()
        for k, v in n.items():
            if k == CH:
                continue
            st.extend(struct.pack(">III", 3, len(v), sid(k))); st.extend(v); pad()
        for k, c in n[CH].items():
            walk(k, c)
        st.extend(struct.pack(">I", 2))

    walk("", tree)
    st.extend(struct.pack(">I", 9))
    o_rsv = 40
    o_st = o_rsv + len(rsv)
    o_str = o_st + len(st)
    total = o_str + len(strs)
    hdr = struct.pack(">10I", 0xD00DFEED, total, o_st, o_str, o_rsv, 17, 16, boot, len(strs), len(st))
    return bytes(hdr) + rsv + bytes(st) + bytes(strs)


def cstr(s):
    return s.encode() + b"\0"


def find_joypad(tree):
    """(name, node) of the ROCKNIX joypad node, or (None, None)."""
    for k, n in tree[CH].items():
        if k.startswith("rocknix-singleadc-joypad"):
            return k, n
    for k, n in tree[CH].items():
        if k.startswith("rocknix-") and "joypad" in k:
            return k, n
    return None, None


def has_plain_pad(tree):
    """True if the DTB already has a gpio-keys node with gamepad-range buttons (BTN_* codes 0x100..0x2ff)."""
    for k, n in tree[CH].items():
        if n.get("compatible") != cstr("gpio-keys") or k in ("gpio-keys-volume", "gpio-keys-lid"):
            continue
        for sw in n[CH].values():
            code = sw.get("linux,code")
            if code and len(code) == 4 and 0x100 <= struct.unpack(">I", code)[0] < 0x300:
                return True
    return False


def patch(src, dst):
    raw = open(src, "rb").read()
    tree, rsv, boot = parse(raw)
    jname, joy = find_joypad(tree)
    if joy is None:
        if has_plain_pad(tree):
            open(dst, "wb").write(raw)
            print("dtb_gpiokeys: no ROCKNIX joypad node, but the DTB already has a plain gpio-keys pad: copied unchanged -> %s" % dst)
            return
        raise SystemExit("no rocknix joypad node and no gpio-keys pad in " + src)
    keys = {"compatible": cstr("gpio-keys"), "pinctrl-names": cstr("default"), CH: {}}
    if "pinctrl-0" in joy:
        keys["pinctrl-0"] = joy["pinctrl-0"]
    count = 0
    for name, sw in joy[CH].items():
        if "gpios" not in sw or "linux,code" not in sw:
            continue
        keys[CH]["button-%s" % name] = {
            "label": sw.get("label", cstr(name)),
            "gpios": sw["gpios"],
            "linux,code": sw["linux,code"],
            "debounce-interval": struct.pack(">I", 10),
            CH: {},
        }
        count += 1
    if not count:
        raise SystemExit("joypad node %s has no GPIO buttons (children with gpios + linux,code) in %s" % (jname, src))
    tree[CH]["gpio-keys-joypad"] = keys
    joy["status"] = cstr("disabled")
    out = emit(tree, rsv, boot)
    open(dst, "wb").write(out)
    # self-check: the output must parse back to exactly what we built
    t2, _, _ = parse(out)
    assert t2[CH]["gpio-keys-joypad"][CH].keys() == keys[CH].keys()
    assert t2[CH][jname]["status"] == cstr("disabled")
    print("dtb_gpiokeys: %d buttons moved to gpio-keys (from %s), joypad driver node disabled -> %s" % (count, jname, dst))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    patch(sys.argv[1], sys.argv[2])
