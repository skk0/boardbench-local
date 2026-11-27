"""Decode length-delimited JPEG frames carried by the LBSNAP serial protocol."""

import base64
import re

from boardbench.devices.screen_mirror import MAX_FRAME, jpeg_dimensions


class LbSnapDecoder:
    def __init__(self):
        self.clear()

    def clear(self):
        self.expected = 0
        self.data = bytearray()

    def consume(self, payload):
        if payload.startswith("BEGIN "):
            self.clear()
            size = payload[6:]
            if not re.fullmatch(r"[0-9]{1,7}", size) or not 0 < int(size) <= MAX_FRAME:
                raise ValueError("截图声明的长度无效。")
            self.expected = int(size)
        elif not self.expected:
            return None  # Attached partway through a frame.
        elif payload == "END":
            if len(self.data) != self.expected:
                raise ValueError("截图长度与设备声明不符。")
            jpeg_dimensions(self.data)
            image = bytes(self.data)
            self.clear()
            return image
        else:
            if not 0 < len(payload) <= 1024:
                raise ValueError("截图分段长度无效。")
            part = base64.b64decode(payload, validate=True)
            if len(self.data) + len(part) > self.expected:
                raise ValueError("截图超出设备声明的长度。")
            self.data.extend(part)
        return None
