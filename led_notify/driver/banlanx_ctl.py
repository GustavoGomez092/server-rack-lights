#!/usr/bin/env python3
"""
banlanx_ctl.py - control BanlanX / SPLED Bluetooth LED controllers (PAUTIX strips, etc.)

Works on macOS, Linux and Windows using the computer's built-in Bluetooth (BLE).
Protocol details come from the reverse-engineering work in monty68/uniled
(Home Assistant integration) and xydac/sp542e-ha-bridge.

Two protocol families are supported:
  v2   : SP611E, SP617E, SP620E, SP621E   (commands start with 0xA0)
  6xx  : SP630E ... SP64xE, SP54xE (e.g. SP542E, SP548E)
                                           (commands framed as 0x53 <cmd> ...)

IMPORTANT: these controllers accept only ONE Bluetooth connection at a time.
Close the BanlanX app on your phone (or turn the phone's Bluetooth off) first.

Install:   pip install bleak
Usage:
  python banlanx_ctl.py scan                    # find your controller
  python banlanx_ctl.py probe   <ADDR>          # connect, dump GATT, read status
  python banlanx_ctl.py on      <ADDR>
  python banlanx_ctl.py color   <ADDR> 255 0 0
  python banlanx_ctl.py effect  <ADDR> 1        # 1 = Rainbow (see `effects`)
  python banlanx_ctl.py sound   <ADDR> 1        # music-reactive effect (uses mic)
  python banlanx_ctl.py demo    <ADDR>
  python banlanx_ctl.py shell   <ADDR>          # stay connected, type commands
  python banlanx_ctl.py --family 6xx effects   # list effect numbers

On macOS, <ADDR> is a UUID like 1A2B3C4D-... (macOS hides MAC addresses);
on Linux/Windows it's a MAC like FF:23:06:03:21:F3.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("Missing dependency: run  pip install bleak")

UUID_FMT = "0000{}-0000-1000-8000-00805f9b34fb"
WRITE_CHAR = UUID_FMT.format("ffe1")   # write + notify characteristic
BANLANX_MFG_IDS = {20563, 5053}       # 20563 == 0x5053 ("SP")

# --- model detection -------------------------------------------------------
# v2 family: first byte of manufacturer data
V2_MODELS = {
    0x04: "SP611E", 0x10: "SP611E", 0x11: "SP611E", 0x12: "SP611E",
    0x13: "SP611E", 0x14: "SP611E", 0x15: "SP611E",
    0x17: "SP617E", 0x1B: "SP620E", 0x0D: "SP621E", 0x16: "SP621E",
}
# 6xx family: first byte of manufacturer data, second byte is 0x10
SIXXX_MODELS = {
    0x1F: "SP630E", 0x20: "SP631E", 0x21: "SP632E", 0x22: "SP633E",
    0x23: "SP634E", 0x24: "SP635E", 0x25: "SP636E", 0x26: "SP637E",
    0x27: "SP638E", 0x28: "SP639E", 0x29: "SP63AE", 0x2C: "SP641E",
    0x2D: "SP642E", 0x2E: "SP643E", 0x2F: "SP644E", 0x30: "SP645E",
    0x31: "SP646E", 0x32: "SP647E", 0x33: "SP648E", 0x34: "SP649E",
    0x35: "SP64AE",
    0x5D: "SP542E",   # confirmed by xydac/sp542e-ha-bridge
}


def identify(name: str | None, mfg: dict[int, bytes]) -> tuple[str | None, str | None]:
    """Return (family, model) from advertisement data, or (None, None)."""
    n = (name or "").upper()
    if n.startswith(("SP54", "SP55")):       # e.g. SP542E, SP548E -> 6xx protocol
        return "6xx", name
    for mid, data in mfg.items():
        if mid not in BANLANX_MFG_IDS or len(data) < 1:
            continue
        if len(data) > 1 and data[1] == 0x10 and data[0] in SIXXX_MODELS:
            return "6xx", SIXXX_MODELS[data[0]]
        if data[0] in V2_MODELS:
            return "v2", V2_MODELS[data[0]]
        if len(data) > 1 and data[1] == 0x10:
            # Newer BanlanX models (e.g. SP548E) use the 6xx protocol with ids
            # that aren't catalogued yet.
            return "6xx", f"{name or 'BanlanX'} (id 0x{data[0]:02x}, assumed 6xx)"
        return "unknown", f"BanlanX id 0x{data[0]:02x}"
    if n.startswith(("SP63", "SP64", "SP54", "SP55")):
        return "6xx", n
    if n.startswith(("SP61", "SP62")):
        return "v2", n
    return None, None


# --- v2 effect names (from uniled) ------------------------------------------
V2_EFFECTS = {
    0x01: "Rainbow",
    0x02: "Rainbow Meteor",
    0x03: "Rainbow Stars",
    0x04: "Rainbow Spin",
    0x05: "Red/Yellow Fire",
    0x06: "Red/Purple Fire",
    0x07: "Green/Yellow Fire",
    0x08: "Green/Cyan Fire",
    0x09: "Blue/Purple Fire",
    0x0A: "Blue/Cyan Fire",
    0x0B: "Red Comet",
    0x0C: "Green Comet",
    0x0D: "Blue Comet",
    0x0E: "Yellow Comet",
    0x0F: "Cyan Comet",
    0x10: "Purple Comet",
    0x11: "White Comet",
    0x12: "Red Meteor",
    0x13: "Green Meteor",
    0x14: "Blue Meteor",
    0x15: "Yellow Meteor",
    0x16: "Cyan Meteor",
    0x17: "Purple Meteor",
    0x18: "White Meteor",
    0x19: "Red/Green Gradual Snake",
    0x1A: "Red/Blue Gradual Snake",
    0x1B: "Red/Yellow Gradual Snake",
    0x1C: "Red/Cyan Gradual Snake",
    0x1D: "Red/Purple Gradual Snake",
    0x1E: "Red/White Gradual Snake",
    0x1F: "Green/Blue Gradual Snake",
    0x20: "Green/Yellow Gradual Snake",
    0x21: "Green/Cyan Gradual Snake",
    0x22: "Green/Purple Gradual Snake",
    0x23: "Green/White Gradual Snake",
    0x24: "Blue/Yellow Gradual Snake",
    0x25: "Blue/Cyan Gradual Snake",
    0x26: "Blue/Purple Gradual Snake",
    0x27: "Blue/White Gradual Snake",
    0x28: "Yellow/Cyan Gradual Snake",
    0x29: "Yellow/Purple Gradual Snake",
    0x2A: "Yellow/White Gradual Snake",
    0x2B: "Cyan/Purple Gradual Snake",
    0x2C: "Cyan/White Gradual Snake",
    0x2D: "Purple/White Gradual Snake",
    0x2E: "Red Wave",
    0x2F: "Green Wave",
    0x30: "Blue Wave",
    0x31: "Yellow Wave",
    0x32: "Cyan Wave",
    0x33: "Purple Wave",
    0x34: "White Wave",
    0x35: "Red/Green Wave",
    0x36: "Red/Blue Wave",
    0x37: "Red/Yellow Wave",
    0x38: "Red/Cyan Wave",
    0x39: "Red/Purple Wave",
    0x3A: "Red/White Wave",
    0x3B: "Green/Blue Wave",
    0x3C: "Green/Yellow Wave",
    0x3D: "Green/Cyan Wave",
    0x3E: "Green/Purple Wave",
    0x3F: "Green/White Wave",
    0x40: "Blue/Yellow Wave",
    0x41: "Blue/Cyan Wave",
    0x42: "Blue/Purple Wave",
    0x43: "Blue/White Wave",
    0x44: "Yellow/Cyan Wave",
    0x45: "Yellow/Purple Wave",
    0x46: "Yellow/White Wave",
    0x47: "Cyan/Purple Wave",
    0x48: "Cyan/White Wave",
    0x49: "Purple/White Wave",
    0x4A: "Red Stars",
    0x4B: "Green Stars",
    0x4C: "Blue Stars",
    0x4D: "Yellow Stars",
    0x4E: "Cyan Stars",
    0x4F: "Purple Stars",
    0x50: "White Stars",
    0x51: "Red Background Stars",
    0x52: "Green Background Stars",
    0x53: "Blue Background Stars",
    0x54: "Yellow Background Stars",
    0x55: "Cyan Background Stars",
    0x56: "Purple Background Stars",
    0x57: "Red/White Background Stars",
    0x58: "Green/White Background Stars",
    0x59: "Blue/White Background Stars",
    0x5A: "Yellow/White Background Stars",
    0x5B: "Cyan/White Background Stars",
    0x5C: "Purple/White Background Stars",
    0x5D: "White/White Background Stars",
    0x5E: "Red Breath",
    0x5F: "Green Breath",
    0x60: "Blue Breath",
    0x61: "Yellow Breath",
    0x62: "Cyan Breath",
    0x63: "Purple Breath",
    0x64: "White Breath",
    0x65: "Red Stacking",
    0x66: "Green Stacking",
    0x67: "Blue Stacking",
    0x68: "Yellow Stacking",
    0x69: "Cyan Stacking",
    0x6A: "Purple Stacking",
    0x6B: "White Stacking",
    0x6C: "Full Color Stack",
    0x6D: "Red to Green Stack",
    0x6E: "Green to Blue Stack",
    0x6F: "Blue to Yellow Stack",
    0x70: "Yellow to Cyan Stack",
    0x71: "Cyan to Purple Stack",
    0x72: "Purple to White Stack",
    0x73: "Red/Blue/White Snake",
    0x74: "Green/Yellow/White Snake",
    0x75: "Red/Green/White Snake",
    0x76: "Red/Yellow Snake",
    0x77: "Red/White Snake",
    0x78: "Green/White Snake",
    0x79: "Red Comet Spin",
    0x7A: "Green Comet Spin",
    0x7B: "Blue Comet Spin",
    0x7C: "Yellow Comet Spin",
    0x7D: "Cyan Comet Spin",
    0x7E: "Purple Comet Spin",
    0x7F: "White Comet Spin",
    0x80: "Red Dot Spin",
    0x81: "Green Dot Spin",
    0x82: "Blue Dot Spin",
    0x83: "Yellow Dot Spin",
    0x84: "Cyan Dot Spin",
    0x85: "Purple Dot Spin",
    0x86: "White Dot Spin",
    0x87: "Red Segment Spin",
    0x88: "Green Segment Spin",
    0x89: "Blue Segment Spin",
    0x8A: "Yellow Segment Spin",
    0x8B: "Cyan Segment Spin",
    0x8C: "Purple Segment Spin",
    0x8D: "White Segment Spin",
    0x8E: "Gradient",
    0xC9: "Sound - Full Color Rhythm Spectrum",
    0xCA: "Sound - Single Color Rhythm Spectrum",
    0xCB: "Sound - Full Color Rhythm Stars",
    0xCC: "Sound - Single Color Rhythm Stars",
    0xCD: "Sound - Gradient Energy",
    0xCE: "Sound - Single Color Energy",
    0xCF: "Sound - Gradient Pulse",
    0xD0: "Sound - Single Color Pulse",
    0xD1: "Sound - Full Color Ejection Forward",
    0xD2: "Sound - Single Color Ejection Forward",
    0xD3: "Sound - Full Color Ejection Backward",
    0xD4: "Sound - Single Color Ejection Backward",
    0xD5: "Sound - Full Color VuMeter",
    0xD6: "Sound - Single Color VuMeter",
    0xD7: "Sound - Love & Peace",
    0xD8: "Sound - Christmas",
    0xD9: "Sound - Heartbeat",
    0xDA: "Sound - Party",
}
V2_SOLID = 0xBE
V2_SOUND_BASE = 0xC8      # v2 sound effects are 0xC9.. (sound 1 == 0xC9)

# --- 6xx effect names for SPI RGB strips (from uniled) -----------------------
SIXXX_EFFECTS_DYNAMIC = {
    0x01: "Rainbow",
    0x02: "Rainbow Meteor",
    0x03: "Rainbow Comet",
    0x04: "Rainbow Segment",
    0x05: "Rainbow Wave",
    0x06: "Rainbow Jump",
    0x07: "Rainbow Stars",
    0x08: "Rainbow Spin",
    0x09: "Red/Yellow Fire",
    0x0A: "Red/Purple Fire",
    0x0B: "Green/Yellow Fire",
    0x0C: "Green/Cyan Fire",
    0x0D: "Blue/Purple Fire",
    0x0E: "Blue/Cyan Fire",
    0x0F: "Red Comet",
    0x10: "Green Comet",
    0x11: "Blue Comet",
    0x12: "Yellow Comet",
    0x13: "Cyan Comet",
    0x14: "Purple Comet",
    0x15: "White Comet",
    0x16: "Red Meteor",
    0x17: "Green Meteor",
    0x18: "Blue Meteor",
    0x19: "Yellow Meteor",
    0x1A: "Cyan Meteor",
    0x1B: "Purple Meteor",
    0x1C: "White Meteor",
    0x1D: "Red/Green Gradual Snake",
    0x1E: "Red/Blue Gradual Snake",
    0x1F: "Red/Yellow Gradual Snake",
    0x20: "Red/Cyan Gradual Snake",
    0x21: "Red/Purple Gradual Snake",
    0x22: "Red/White Gradual Snake",
    0x23: "Green/Blue Gradual Snake",
    0x24: "Green/Yellow Gradual Snake",
    0x25: "Green/Cyan Gradual Snake",
    0x26: "Green/Purple Gradual Snake",
    0x27: "Green/White Gradual Snake",
    0x28: "Blue/Yellow Gradual Snake",
    0x29: "Blue/Cyan Gradual Snake",
    0x2A: "Blue/Purple Gradual Snake",
    0x2B: "Blue/White Gradual Snake",
    0x2C: "Yellow/Cyan Gradual Snake",
    0x2D: "Yellow/Purple Gradual Snake",
    0x2E: "Yellow/White Gradual Snake",
    0x2F: "Cyan/Purple Gradual Snake",
    0x30: "Cyan/White Gradual Snake",
    0x31: "Purple/White Gradual Snake",
    0x32: "Red Wave",
    0x33: "Green Wave",
    0x34: "Blue Wave",
    0x35: "Yellow Wave",
    0x36: "Cyan Wave",
    0x37: "Purple Wave",
    0x38: "White Wave",
    0x39: "Red/Green Wave",
    0x3A: "Red/Blue Wave",
    0x3B: "Red/Yellow Wave",
    0x3C: "Red/Cyan Wave",
    0x3D: "Red/Purple Wave",
    0x3E: "Red/White Wave",
    0x3F: "Green/Blue Wave",
    0x40: "Green/Yellow Wave",
    0x41: "Green/Cyan Wave",
    0x42: "Green/Purple Wave",
    0x43: "Green/White Wave",
    0x44: "Blue/Yellow Wave",
    0x45: "Blue/Cyan Wave",
    0x46: "Blue/Purple Wave",
    0x47: "Blue/White Wave",
    0x48: "Yellow/Cyan Wave",
    0x49: "Yellow/Purple Wave",
    0x4A: "Yellow/White Wave",
    0x4B: "Cyan/Purple Wave",
    0x4C: "Cyan/White Wave",
    0x4D: "Purple/White Wave",
    0x4E: "Red Stars",
    0x4F: "Green Stars",
    0x50: "Blue Stars",
    0x51: "Yellow Stars",
    0x52: "Cyan Stars",
    0x53: "Purple Stars",
    0x54: "White Stars",
    0x55: "Red Background Stars",
    0x56: "Green Background Stars",
    0x57: "Blue Background Stars",
    0x58: "Yellow Background Stars",
    0x59: "Cyan Background Stars",
    0x5A: "Purple Background Stars",
    0x5B: "Red/White Background Stars",
    0x5C: "Green/White Background Stars",
    0x5D: "Blue/White Background Stars",
    0x5E: "Yellow/White Background Stars",
    0x5F: "Cyan/White Background Stars",
    0x60: "Purple/White Background Stars",
    0x61: "White/White Background Stars",
    0x62: "Red Breath",
    0x63: "Green Breath",
    0x64: "Blue Breath",
    0x65: "Yellow Breath",
    0x66: "Cyan Breath",
    0x67: "Purple Breath",
    0x68: "White Breath",
    0x69: "Red Stacking",
    0x6A: "Green Stacking",
    0x6B: "Blue Stacking",
    0x6C: "Yellow Stacking",
    0x6D: "Cyan Stacking",
    0x6E: "Purple Stacking",
    0x6F: "White Stacking",
    0x70: "Full Color Stack",
    0x71: "Red to Green Stack",
    0x72: "Green to Blue Stack",
    0x73: "Blue to Yellow Stack",
    0x74: "Yellow to Cyan Stack",
    0x75: "Cyan to Purple Stack",
    0x76: "Purple to White Stack",
    0x77: "Red/Blue/White Snake",
    0x78: "Green/Yellow/White Snake",
    0x79: "Red/Green/White Snake",
    0x7A: "Red/Yellow Snake",
    0x7B: "Red/White Snake",
    0x7C: "Green/White Snake",
    0x7D: "Red Comet Spin",
    0x7E: "Green Comet Spin",
    0x7F: "Blue Comet Spin",
    0x80: "Yellow Comet Spin",
    0x81: "Cyan Comet Spin",
    0x82: "Purple Comet Spin",
    0x83: "White Comet Spin",
    0x84: "Red Dot Spin",
    0x85: "Green Dot Spin",
    0x86: "Blue Dot Spin",
    0x87: "Yellow Dot Spin",
    0x88: "Cyan Dot Spin",
    0x89: "Purple Dot Spin",
    0x8A: "White Dot Spin",
    0x8B: "Red Segment Spin",
    0x8C: "Green Segment Spin",
    0x8D: "Blue Segment Spin",
    0x8E: "Yellow Segment Spin",
    0x8F: "Cyan Segment Spin",
    0x90: "Purple Segment Spin",
    0x91: "White Segment Spin",
    0x92: "Gradient",
}
SIXXX_EFFECTS_SOUND = {
    0x01: "Sound - Full Color Rhythm Spectrum",
    0x02: "Sound - Single Color Rhythm Spectrum",
    0x03: "Sound - Full Color Rhythm Stars",
    0x04: "Sound - Single Color Rhythm Stars",
    0x05: "Sound - Gradient Energy",
    0x06: "Sound - Single Color Energy",
    0x07: "Sound - Gradient Pulse",
    0x08: "Sound - Single Color Pulse",
    0x09: "Sound - Full Color Ejection Forward",
    0x0A: "Sound - Single Color Ejection Forward",
    0x0B: "Sound - Full Color Ejection Backward",
    0x0C: "Sound - Single Color Ejection Backward",
    0x0D: "Sound - Full Color VuMeter",
    0x0E: "Sound - Single Color VuMeter",
    0x0F: "Sound - Love & Peace",
    0x10: "Sound - Christmas",
    0x11: "Sound - Heartbeat",
    0x12: "Sound - Party",
}


# --- command builders -------------------------------------------------------
class ProtoV2:
    """SP611E / SP617E / SP620E / SP621E."""
    name = "v2"

    @staticmethod
    def state_query():          return [bytes([0xA0, 0x70, 0x00])]
    @staticmethod
    def power(on: bool):        return [bytes([0xA0, 0x62, 0x01, 1 if on else 0])]
    @staticmethod
    def brightness(level: int): return [bytes([0xA0, 0x66, 0x01, level & 0xFF])]
    @staticmethod
    def speed(v: int):          return [bytes([0xA0, 0x67, 0x01, clamp(v, 1, 10)])]
    @staticmethod
    def length(v: int):         return [bytes([0xA0, 0x68, 0x01, clamp(v, 1, 150)])]

    @staticmethod
    def effect(fx: int):
        return [
            bytes([0xA0, 0x6A, 0x01, 0x00]),       # light mode: single effect (no auto-loop)
            bytes([0xA0, 0x63, 0x01, fx & 0xFF]),
        ]

    @staticmethod
    def sound(n: int):
        return ProtoV2.effect(V2_SOUND_BASE + n)

    @staticmethod
    def color(r: int, g: int, b: int, level: int = 255):
        return ProtoV2.effect(V2_SOLID) + [
            bytes([0xA0, 0x69, 0x04, r & 0xFF, g & 0xFF, b & 0xFF, level & 0xFF])
        ]


class Proto6xx:
    """SP630E ... SP64xE.  Frame: 53 <cmd> <key=0> 01 00 <len> <payload>."""
    name = "6xx"
    MODE_STATIC_COLOR = 0x01
    MODE_DYNAMIC_COLOR = 0x03
    MODE_SOUND_COLOR = 0x05

    @staticmethod
    def _f(cmd: int, payload: list[int]) -> bytes:
        return bytes([0x53, cmd, 0x00, 0x01, 0x00, len(payload), *[p & 0xFF for p in payload]])

    @classmethod
    def state_query(cls):          return [cls._f(0x02, [0x01])]
    @classmethod
    def power(cls, on: bool):      return [cls._f(0x50, [1 if on else 0])]
    @classmethod
    def brightness(cls, level):    return [cls._f(0x51, [0x00, level])]   # 0x00 = color channel
    @classmethod
    def speed(cls, v):             return [cls._f(0x54, [clamp(v, 1, 10)])]
    @classmethod
    def length(cls, v):            return [cls._f(0x55, [clamp(v, 1, 150)])]
    @classmethod
    def effect(cls, fx):           return [cls._f(0x53, [cls.MODE_DYNAMIC_COLOR, fx])]
    @classmethod
    def sound(cls, n):             return [cls._f(0x53, [cls.MODE_SOUND_COLOR, n])]

    @classmethod
    def color(cls, r, g, b, level=255):
        return [
            cls._f(0x53, [cls.MODE_STATIC_COLOR, 0x01]),
            cls._f(0x52, [r, g, b, level]),
        ]


PROTOCOLS = {"v2": ProtoV2, "6xx": Proto6xx}


def clamp(v, lo, hi) -> int:
    return max(lo, min(hi, int(v)))


def hexs(b: bytes) -> str:
    return " ".join(f"{x:02x}" for x in b)


# --- v2 status decoding -----------------------------------------------------
class V2StatusAssembler:
    """v2 replies arrive as 1+ packets: 53 43 <pkt#> <msg_len> <payload_len> <payload>."""

    def __init__(self):
        self.buf = bytearray()
        self.msg_len = 0

    def feed(self, data: bytes) -> dict | None:
        if len(data) < 5 or data[0] != 0x53 or data[1] != 0x43:
            return None
        if data[2] == 1:
            self.buf = bytearray(data[5:])
            self.msg_len = data[3]
        else:
            self.buf += data[5:]
        if self.msg_len and len(self.buf) >= self.msg_len:
            d = self.buf
            self.buf = bytearray()
            return {
                "power": "on" if d[0] == 1 else "off",
                "auto_loop": d[1],
                "effect": f"0x{d[2]:02X} ({V2_EFFECTS.get(d[2], 'Solid' if d[2] == V2_SOLID else '?')})",
                "chip_order": d[3],
                "brightness": d[4],
                "speed": d[5],
                "length": d[6],
                "rgb": (d[7], d[8], d[9]),
            }
        return None


CHUNK = 14  # bytes of payload per status chunk on SP548E firmware V3.x

# Field offsets inside the reassembled status payload, mapped on an SP548E
# (firmware V3.0.19) by changing one setting at a time and diffing.
V3_FIELDS = {
    "light_type": 14,    # 0x06 = SPI RGB
    "power": 27,         # 0 off / 1 on
    "mode": 30,          # 1 static color, 3 dynamic color, 5 sound color, ...
    "effect": 31,
    "playing": 32,
    "brightness": 33,    # 0-255
    "white_level": 34,
    "red": 35, "green": 36, "blue": 37,
    "speed": 40,         # 1-10
    "length": 41,        # 1-150
    "direction": 42,
    "sensitivity": 43,   # 1-16
    "audio_input": 44,   # 0 internal mic, 1 player, 2 external mic
}
MODES_6XX = {1: "static color", 2: "static white", 3: "dynamic color", 4: "dynamic white",
             5: "sound color", 6: "sound white", 7: "custom"}


def status_complete(packets: list[bytes]) -> bool:
    """True once every chunk announced in byte 3 of the header has arrived."""
    pk = [p for p in packets if len(p) >= 6 and p[0] == 0x53 and p[1] == 0x02]
    if not pk:
        return False
    total = pk[0][3]
    return len({p[4] for p in pk}) >= total


def parse_6xx_status(packets: list[bytes]) -> dict | None:
    """
    Reassemble a 6xx status reply.

    SP548E (firmware V3.x) splits the reply into numbered chunks:
        53 02 <key> <total_chunks> <chunk#> <chunk_len> <chunk bytes>
    Each chunk is placed at chunk# * 14 so a lost chunk can't shift later fields.
    """
    pk = [p for p in packets if len(p) >= 6 and p[0] == 0x53 and p[1] == 0x02]
    if not pk:
        return None
    if pk[0][2] != 0x00:
        return {"note": "encrypted reply (key != 0) - not supported", "raw": [hexs(p) for p in pk]}
    total = pk[0][3]
    got = {p[4]: p[6:6 + p[5]] for p in pk}
    payload = bytearray(max(total, max(got) + 1) * CHUNK)
    for i, chunk in got.items():
        payload[i * CHUNK:i * CHUNK + len(chunk)] = chunk
    payload = bytes(payload)
    st = {"payload": payload, "chunks": f"{len(got)}/{total}"}
    if len(got) < total:
        st["missing_chunks"] = sorted(set(range(total)) - set(got))

    m = re.search(rb"V(\d+)\.\d+\.\d+", payload)
    if m:
        st["firmware"] = m.group().decode()
    m = re.search(rb"SP\d{3}E[\w-]*", payload[100:])
    if m:
        st["device_name"] = m.group().decode()

    if st.get("firmware", "").startswith("V3") and 0 in got and 3 in got:
        f = {k: payload[v] for k, v in V3_FIELDS.items()}
        mode = f["mode"]
        names = SIXXX_EFFECTS_SOUND if mode == 5 else SIXXX_EFFECTS_DYNAMIC
        st.update({
            "power": "on" if f["power"] else "off",
            "mode": MODES_6XX.get(mode, mode),
            "effect": f["effect"] if mode in (1, 2) else f"{f['effect']} ({names.get(f['effect'], '?')})",
            "brightness": f["brightness"],
            "rgb": (f["red"], f["green"], f["blue"]),
            "speed": f["speed"],
            "length": f["length"],
            "sensitivity": f["sensitivity"],
            "light_type": f"0x{f['light_type']:02x}" + (" (SPI RGB)" if f["light_type"] == 6 else ""),
        })
    return st


def summarize(st: dict) -> str:
    keys = ("power", "mode", "effect", "brightness", "rgb", "speed", "length",
            "sensitivity", "light_type", "firmware", "device_name", "chunks", "missing_chunks")
    return "  " + "\n  ".join(f"{k:12s} {st[k]}" for k in keys if k in st)


def hexdump(b: bytes, prev: bytes | None = None) -> str:
    """Indexed dump; bytes that changed since `prev` are marked with *."""
    lines = []
    for row in range(0, len(b), 16):
        cells = []
        for i in range(row, min(row + 16, len(b))):
            mark = "*" if prev is not None and i < len(prev) and prev[i] != b[i] else " "
            cells.append(f"{b[i]:02x}{mark}")
        lines.append(f"  {row:3d}: " + " ".join(cells))
    return "\n".join(lines)


def diff(prev: bytes, cur: bytes) -> list[str]:
    return [f"[{i}] {prev[i]} -> {cur[i]}   (0x{prev[i]:02x} -> 0x{cur[i]:02x})"
            for i in range(min(len(prev), len(cur))) if prev[i] != cur[i]]


# --- high-level API you can import into your own event code ----------------
class Strip:
    """
    Example:
        async with Strip("FF:23:06:03:21:F3") as s:
            await s.on()
            await s.color(255, 0, 0)
            await s.effect(1)
    """

    def __init__(self, address: str, family: str | None = None, verbose: bool = False):
        self.address = address
        self.family = family
        self.verbose = verbose
        self.proto = None
        self.client: BleakClient | None = None
        self.last_status: dict | None = None
        self._v2asm = V2StatusAssembler()
        self._packets: list[bytes] = []
        self.level = 255             # remembered brightness, reused by color()
        self._last_packet_time = 0.0
        self._status_event = asyncio.Event()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()

    async def connect(self, timeout: float = 20.0):
        print(f"Looking for {self.address} ...")
        found = await BleakScanner.discover(timeout=8.0, return_adv=True)
        dev = adv = None
        for d, a in found.values():
            if d.address.upper() == self.address.upper():
                dev, adv = d, a
                break
        if dev is None:
            raise SystemExit(
                "Device not found. Is it powered, in range, and NOT connected to the phone app?"
            )
        fam, model = identify(adv.local_name or dev.name, adv.manufacturer_data)
        if self.family is None:
            if fam not in PROTOCOLS:
                raise SystemExit(
                    f"Couldn't auto-detect protocol (got {fam!r}, {model!r}). "
                    "Re-run with --family v2 or --family 6xx."
                )
            self.family = fam
        self.proto = PROTOCOLS[self.family]
        print(f"Found {adv.local_name or dev.name!r}  model={model}  protocol={self.family}  RSSI={adv.rssi}")

        self.client = BleakClient(dev, timeout=timeout)
        await self.client.connect()
        print("Connected.")
        try:
            await self.client.start_notify(WRITE_CHAR, self._on_notify)
        except Exception as e:  # noqa: BLE001
            print(f"(couldn't subscribe to notifications: {e})")
        if self.family == "6xx":
            st = await self.status()
            if st and isinstance(st.get("brightness"), int) and st["brightness"] > 0:
                self.level = st["brightness"]

    async def disconnect(self):
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            print("Disconnected.")

    def _on_notify(self, _handle, data: bytearray):
        data = bytes(data)
        if self.verbose:
            print(f"  <- {hexs(data)}")
        if self.family == "v2":
            st = self._v2asm.feed(data)
            if st:
                self.last_status = st
                self._status_event.set()
        else:
            self._packets.append(data)
            self._last_packet_time = asyncio.get_running_loop().time()

    async def _send(self, frames: list[bytes]):
        for f in frames:
            if self.verbose:
                print(f"  -> {hexs(f)}")
            # These controllers ignore writes that aren't acknowledged: response=True
            await self.client.write_gatt_char(WRITE_CHAR, f, response=True)
            await asyncio.sleep(0.05)

    # public commands
    async def on(self):                     await self._send(self.proto.power(True))
    async def off(self):                    await self._send(self.proto.power(False))
    async def color(self, r, g, b, level=None):
        if level is not None:
            self.level = clamp(level, 0, 255)
        await self._send(self.proto.color(r, g, b, self.level))

    async def brightness(self, level):
        self.level = clamp(level, 0, 255)
        await self._send(self.proto.brightness(self.level))
    async def effect(self, fx):             await self._send(self.proto.effect(fx))
    async def sound(self, n):               await self._send(self.proto.sound(n))
    async def speed(self, v):               await self._send(self.proto.speed(v))
    async def length(self, v):              await self._send(self.proto.length(v))

    async def status(self, timeout: float = 3.0) -> dict | None:
        if self.family == "v2":
            self._status_event.clear()
            await self._send(self.proto.state_query())
            try:
                await asyncio.wait_for(self._status_event.wait(), timeout)
            except asyncio.TimeoutError:
                return None
            return self.last_status
        # 6xx: collect chunks until the controller goes quiet for 0.5 s
        loop = asyncio.get_running_loop()
        self._packets.clear()
        sent = loop.time()
        await self._send(self.proto.state_query())
        while loop.time() - sent < timeout:
            await asyncio.sleep(0.05)
            if status_complete(self._packets):
                break
        self.last_status = parse_6xx_status(self._packets)
        return self.last_status


# --- CLI ----------------------------------------------------------------------
async def cmd_scan(args):
    print(f"Scanning for {args.timeout:.0f}s ...\n")
    found = await BleakScanner.discover(timeout=args.timeout, return_adv=True)
    hits = 0
    for d, a in sorted(found.values(), key=lambda x: -x[1].rssi):
        fam, model = identify(a.local_name or d.name, a.manufacturer_data)
        if fam is None and not args.all:
            continue
        hits += 1
        mfg = ", ".join(f"0x{k:04x}:{hexs(v)}" for k, v in a.manufacturer_data.items()) or "-"
        print(f"{d.address}  RSSI {a.rssi:>4}  name={a.local_name or d.name!r}")
        print(f"    protocol={fam}  model={model}  mfg=[{mfg}]")
    if not hits:
        print("No BanlanX controllers found. Tips: plug the strip in, close the phone app /"
              " turn off phone Bluetooth, then try `scan --all`.")


async def cmd_probe(args):
    async with Strip(args.address, args.family, verbose=True) as s:
        print("\nGATT services:")
        for svc in s.client.services:
            print(f"  [service] {svc.uuid}")
            for ch in svc.characteristics:
                print(f"      {ch.uuid}  {','.join(ch.properties)}")
        print("\nRequesting status ...")
        st = await s.status()
        if not st:
            print("No status reply (commands may still work - try `demo`).")
        elif "payload" in st:
            print(summarize(st))
            print("\nRaw payload:")
            print(hexdump(st["payload"]))
        else:
            print(f"Status: {st}")


async def cmd_simple(args):
    async with Strip(args.address, args.family, verbose=args.verbose) as s:
        if args.cmd == "on":
            await s.on()
        elif args.cmd == "off":
            await s.off()
        elif args.cmd == "color":
            await s.on()
            await s.color(args.r, args.g, args.b)
        elif args.cmd == "brightness":
            await s.brightness(args.level)
        elif args.cmd == "effect":
            await s.on()
            await s.effect(args.fx)
        elif args.cmd == "sound":
            await s.on()
            await s.sound(args.fx)
        print("Done.")


async def cmd_demo(args):
    async with Strip(args.address, args.family, verbose=True) as s:
        steps = [
            ("power on", s.on()),
            ("red", s.color(255, 0, 0)),
            ("green", s.color(0, 255, 0)),
            ("blue", s.color(0, 0, 255)),
            ("50% brightness", s.brightness(128)),
            ("100% brightness", s.brightness(255)),
            ("effect 1 (rainbow)", s.effect(1)),
            ("speed 10", s.speed(10)),
            ("music effect 1 (make some noise!)", s.sound(1)),
            ("power off", s.off()),
        ]
        for label, coro in steps:
            print(f"\n== {label}")
            await coro
            await asyncio.sleep(2.5)


async def cmd_shell(args):
    help_txt = ("commands: on | off | color R G B | bright N(0-255) | effect N | "
                "sound N | speed N(1-10) | length N(1-150) | status | raw (bytes + changes) | quit")
    async with Strip(args.address, args.family, verbose=args.verbose) as s:
        print(help_txt)
        prev_payload = None
        loop = asyncio.get_running_loop()
        while True:
            line = (await loop.run_in_executor(None, input, "led> ")).strip().split()
            if not line:
                continue
            c, *p = line
            try:
                if c in ("quit", "exit", "q"):
                    break
                elif c == "on":       await s.on()
                elif c == "off":      await s.off()
                elif c == "color":    await s.color(*map(int, p[:3]))
                elif c == "bright":   await s.brightness(int(p[0]))
                elif c == "effect":   await s.effect(int(p[0], 0))
                elif c == "sound":    await s.sound(int(p[0], 0))
                elif c == "speed":    await s.speed(int(p[0]))
                elif c == "length":   await s.length(int(p[0]))
                elif c in ("status", "raw"):
                    st = await s.status()
                    if st and "payload" in st:
                        cur = st["payload"]
                        print(summarize(st))
                        if c == "raw":
                            print(hexdump(cur, prev_payload))
                        if prev_payload is not None:
                            changes = diff(prev_payload, cur)
                            print("changed since last status:" if changes else "no changes")
                            for ch in changes:
                                print("  " + ch)
                        prev_payload = cur
                    else:
                        print(st)
                else:                 print(help_txt)
            except (ValueError, IndexError):
                print(help_txt)


def cmd_effects(args):
    if args.family == "6xx":
        print("6xx-family (SP54xE/SP63xE/SP64xE) SPI RGB effects - use with `effect N`:")
        for k, v in SIXXX_EFFECTS_DYNAMIC.items():
            print(f"  {k:3d}  {v}")
        print("\nMusic effects - use with `sound N`:")
        for k, v in SIXXX_EFFECTS_SOUND.items():
            print(f"  {k:3d}  {v}")
        return
    print("v2-family (SP611E/SP620E/SP621E) effects - use with `effect N`:")
    for k, v in V2_EFFECTS.items():
        if k < V2_SOUND_BASE:
            print(f"  {k:3d}  {v}")
    print("\nMusic effects - use with `sound N`:")
    for k, v in V2_EFFECTS.items():
        if k > V2_SOUND_BASE:
            print(f"  {k - V2_SOUND_BASE:3d}  {v}")
    print("\n(For SP548E and other 6xx controllers run:  --family 6xx effects)")


def main():
    ap = argparse.ArgumentParser(description="Control BanlanX/SPLED BLE LED controllers")
    ap.add_argument("--family", choices=PROTOCOLS, help="force protocol instead of auto-detect")
    ap.add_argument("-v", "--verbose", action="store_true", help="print raw bytes")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan"); p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--all", action="store_true", help="show every BLE device")
    sub.add_parser("effects")
    for name in ("probe", "on", "off", "demo", "shell"):
        sub.add_parser(name).add_argument("address")
    p = sub.add_parser("color"); p.add_argument("address")
    for c in "rgb":
        p.add_argument(c, type=int)
    p = sub.add_parser("brightness"); p.add_argument("address"); p.add_argument("level", type=int)
    for name in ("effect", "sound"):
        p = sub.add_parser(name); p.add_argument("address"); p.add_argument("fx", type=lambda x: int(x, 0))

    args = ap.parse_args()
    if args.cmd == "effects":
        return cmd_effects(args)
    handler = {"scan": cmd_scan, "probe": cmd_probe, "demo": cmd_demo, "shell": cmd_shell}.get(
        args.cmd, cmd_simple)
    try:
        asyncio.run(handler(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
