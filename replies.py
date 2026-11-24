"""Streaming comparison shared by the serial tester and offline profile preview."""


class ReplyMatch:
    def __init__(self, test):
        self.hex = test.get("encoding") == "hex"
        encode = bytes.fromhex if self.hex else str.encode
        self.expect = encode(test["expect"])
        self.reject = encode(test.get("reject", ""))
        self.mode = test.get(
            "match", "line" if not self.hex and self.expect == b"OK" else "contains"
        )
        if self.mode not in ("line", "contains", "frame") or (
            self.mode == "frame" and not self.hex
        ):
            raise ValueError("match 支持 line、contains 和 HEX 定长 frame。")
        if not self.expect or (
            self.mode == "frame"
            and self.reject
            and len(self.reject) != len(self.expect)
        ):
            raise ValueError("应答不能为空，定长帧的成功与失败应答须等长。")
        if self.mode == "line" and any(
            c in self.expect + self.reject for c in (b"\n", b"\r")
        ):
            raise ValueError("整行应答无需填写换行符。")
        self.buffer = bytearray()
        self.discard_line = False

    def compare(self, unit, contains=False):
        def matches(target):
            return target and (target in unit if contains else target == unit)

        if matches(self.reject):
            return "失败"
        if matches(self.expect):
            return "通过"
        return None

    def feed(self, data, datagram=False):
        if self.mode == "frame" and datagram:
            return self.compare(data)
        self.buffer.extend(data)
        if self.mode == "contains":
            verdict = self.compare(self.buffer, contains=True)
            del self.buffer[: -max(len(self.expect), len(self.reject), 1)]
            return verdict
        if self.mode == "frame":
            n = len(self.expect)
            end = len(self.buffer) // n * n
            for offset in range(0, end, n):
                verdict = self.compare(self.buffer[offset : offset + n])
                if verdict:
                    return verdict
            del self.buffer[:end]
        else:
            lines = self.buffer.replace(b"\r", b"\n").split(b"\n")
            self.buffer = lines.pop()
            for line in lines:
                if not self.discard_line and len(line) <= 8192:
                    verdict = self.compare(line)
                    if verdict:
                        return verdict
                self.discard_line = False
            if len(self.buffer) > 8192:
                self.buffer.clear()
                self.discard_line = True
        return None


def try_sample(test, sample):
    if not isinstance(sample, str) or len(sample) > 8192:
        raise ValueError("试跑样例限 8192 字符。")
    matcher = ReplyMatch(test)
    data = bytes.fromhex(sample) if matcher.hex else sample.encode()
    if matcher.mode == "line":
        data += b"\n"
    return matcher.feed(data, datagram=matcher.mode == "frame") or "未匹配"
