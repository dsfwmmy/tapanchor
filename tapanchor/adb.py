"""TapAnchor · ADB 层
最小实现，零第三方依赖。只负责「把命令发出去」和「把界面拿回来」。
"""
import os
import re
import subprocess

CANDIDATES = [
    r"C:\Users\chenactivity\platform-tools\adb.exe",
    r"C:\Users\chenactivity\lzs\platform-tools\adb.exe",
    r"C:\Users\chenactivity\lzs\pt34\platform-tools\adb.exe",
    "adb",
]


class AdbError(RuntimeError):
    pass


class Adb:
    def __init__(self, serial=None, adb=None, timeout=25):
        self.adb = adb or self._find()
        self.serial = serial
        self.timeout = timeout

    @staticmethod
    def _find():
        for c in CANDIDATES:
            if c == "adb":
                return c
            if os.path.exists(c):
                return c
        return "adb"

    def _base(self):
        b = [self.adb]
        if self.serial:
            b += ["-s", self.serial]
        return b

    def run(self, *args, timeout=None, check=True, binary=False):
        cmd = self._base() + [str(a) for a in args]
        p = subprocess.run(cmd, capture_output=True, timeout=timeout or self.timeout)
        if check and p.returncode != 0:
            err = p.stderr.decode("utf-8", "replace")[:400]
            raise AdbError("%s -> rc=%s: %s" % (" ".join(cmd), p.returncode, err))
        return p.stdout if binary else p.stdout.decode("utf-8", "replace")

    def devices(self):
        out = self.run("devices")
        out = out[out.find("List of devices"):]
        found = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                found.append(parts[0])
        return found

    def dump_xml(self, remote="/sdcard/_ta_dump.xml"):
        self.run("shell", "uiautomator", "dump", remote)
        xml = self.run("exec-out", "cat", remote)
        if "<?xml" not in xml:
            raise AdbError("dump 返回异常: %r" % xml[:200])
        return xml

    def tap(self, x, y):
        self.run("shell", "input", "tap", int(x), int(y))

    def longpress(self, x, y, ms=800):
        self.run("shell", "input", "swipe", int(x), int(y), int(x), int(y), int(ms))

    def swipe(self, x1, y1, x2, y2, ms=300):
        self.run("shell", "input", "swipe",
                 int(x1), int(y1), int(x2), int(y2), int(ms))

    def text(self, s):
        safe = s.replace(" ", "%s")
        try:
            safe.encode("ascii")
        except UnicodeEncodeError:
            raise AdbError("中文输入需要 ADBKeyboard 或剪贴板通道，见 README")
        self.run("shell", "input", "text", safe)

    def keyevent(self, code):
        self.run("shell", "input", "keyevent", code)

    def screencap(self, local_path):
        data = self.run("exec-out", "screencap", "-p", binary=True)
        with open(local_path, "wb") as f:
            f.write(data)
        return local_path

    def current_focus(self):
        out = self.run("shell", "dumpsys", "window", "displays", check=False)
        m = re.search(r"mCurrentFocus=Window\{[0-9a-fA-F]+ u\d+ ([^/]+)/([^} ]+)", out)
        if m:
            return m.group(1), m.group(2)
        return None, None

    def version(self):
        return self.run("version").strip()
