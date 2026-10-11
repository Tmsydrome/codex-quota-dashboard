"""Codex quota dashboard v1.0.0."""

import ctypes
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageTk


VERSION = "1.0.0"
POLL_SECONDS = 30
GITHUB_URL = "https://github.com/Tmsydrome"
GITHUB_AVATAR = Path(__file__).with_name("v1.0.0_GitHub头像.png")
FONT_DIR = Path(os.environ["WINDIR"]) / "Fonts"
FONT_TEXT = FONT_DIR / "msyh.ttc"
FONT_BOLD = FONT_DIR / "msyhbd.ttc"
FONT_LATIN = FONT_DIR / "seguisb.ttf"
BG = "#07111e"
PANEL = "#101d2d"
TEXT = "#ffffff"
LABEL = "#f3fbff"
MUTED = "#c3ddeb"
CYAN = "#70ffe9"
BLUE = "#b7caff"


def codex_running():
    """Find the desktop package process, whose executable is ChatGPT.exe."""
    from ctypes import wintypes

    ctypes.windll.kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    ctypes.windll.kernel32.OpenProcess.restype = wintypes.HANDLE
    ctypes.windll.kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    ctypes.windll.kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    )
    pids = (wintypes.DWORD * 4096)()
    count = wintypes.DWORD()
    if not ctypes.windll.psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(count)):
        return False
    for pid in pids[: count.value // ctypes.sizeof(wintypes.DWORD)]:
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            continue
        try:
            path = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(path))
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
                name = path.value.lower()
                if "openai.codex_" in name and name.endswith("\\chatgpt.exe"):
                    return True
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    return False


def codex_binary():
    candidate = shutil.which("codex")
    if candidate:
        return candidate
    base = Path(os.environ["LOCALAPPDATA"]) / "OpenAI" / "Codex" / "bin"
    matches = sorted(base.glob("*/codex.exe"), key=lambda path: path.stat().st_mtime, reverse=True)
    if matches:
        return str(matches[0])
    raise FileNotFoundError("找不到 Codex CLI")


def windows(snapshot):
    """Use the durations returned by Codex, independent of field order."""
    limits = (snapshot.get("rateLimitsByLimitId") or {}).get("codex") or snapshot.get("rateLimits") or {}
    found = {}
    for value in (limits.get("primary"), limits.get("secondary")):
        if isinstance(value, dict):
            minutes = value.get("windowDurationMins")
            if minutes == 300:
                found["5h"] = value
            elif minutes == 10080:
                found["week"] = value
    return found


def format_reset(timestamp):
    if not timestamp:
        return "重置时间待更新"
    return "重置 " + datetime.fromtimestamp(timestamp).strftime("%m-%d %H:%M")


class RateClient:
    def __init__(self, output):
        self.output = output
        self.process = None
        self.inbox = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def send(self, message):
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def await_id(self, request_id, timeout=12):
        deadline = time.monotonic() + timeout
        while not self.stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Codex 响应超时")
            try:
                response = self.inbox.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if response is None:
                raise ConnectionError("Codex 连接已断开")
            if response.get("id") == request_id:
                if "error" in response:
                    raise RuntimeError(response["error"].get("message", "Codex 读取失败"))
                return response.get("result", {})
        raise InterruptedError()

    def read_stdout(self):
        try:
            for line in self.process.stdout:
                try:
                    self.inbox.put(json.loads(line))
                except json.JSONDecodeError:
                    pass
        finally:
            self.inbox.put(None)

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.inbox = queue.Queue()
                self.process = subprocess.Popen(
                    [codex_binary(), "app-server", "--stdio"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                threading.Thread(target=self.read_stdout, daemon=True).start()
                self.send({"id": 1, "method": "initialize", "params": {
                    "clientInfo": {"name": "codex-quota-dashboard", "title": "Codex Quota Dashboard", "version": VERSION}
                }})
                self.await_id(1)
                self.send({"method": "initialized", "params": {}})
                while not self.stop_event.is_set():
                    self.send({"id": 2, "method": "account/rateLimits/read"})
                    result = self.await_id(2)
                    self.output.put(("data", windows(result)))
                    self.stop_event.wait(POLL_SECONDS)
            except InterruptedError:
                break
            except Exception as exc:
                self.output.put(("error", str(exc)))
                self.stop_event.wait(POLL_SECONDS)
            finally:
                if self.process and self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                self.process = None

    def stop(self):
        self.stop_event.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()


class Dashboard:
    def text_image(self, value, font_path, pixels, color):
        # Rasterize at 2x, then sharpen the alpha edge at display size.
        scale = 2
        font = ImageFont.truetype(str(font_path), round(pixels * self.dpi_scale) * scale)
        box = font.getbbox(value)
        pad = 2 * scale
        mask = Image.new("L", (box[2] - box[0] + 2 * pad, box[3] - box[1] + 2 * pad))
        ImageDraw.Draw(mask).text((pad - box[0], pad - box[1]), value, font=font, fill=255)
        mask = mask.resize((max(1, round(mask.width / scale)), max(1, round(mask.height / scale))), Image.Resampling.LANCZOS)
        mask = mask.filter(ImageFilter.UnsharpMask(radius=0.6, percent=170, threshold=0))
        image = Image.new("RGBA", mask.size, color)
        image.putalpha(mask)
        return ImageTk.PhotoImage(image)

    def make_text(self, x, y, value, color, font_path, pixels, anchor="w"):
        photo = self.text_image(value, font_path, pixels, color)
        item = self.canvas.create_image(x, y, anchor=anchor, image=photo)
        self.photos[item] = photo
        self.text_styles[item] = (color, font_path, pixels)
        return item

    def set_text(self, item, value):
        photo = self.text_image(value, *self.text_styles[item][1:], self.text_styles[item][0])
        self.photos[item] = photo
        self.canvas.itemconfigure(item, image=photo)

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Codex 额度")
        self.dpi_scale = self.root.winfo_fpixels("1i") / 96
        width, height = round(274 * self.dpi_scale), round(180 * self.dpi_scale)
        self.root.geometry(f"{width}x{height}+{max(0, self.root.winfo_screenwidth() - width - round(20 * self.dpi_scale))}+{round(80 * self.dpi_scale)}")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.minimize)
        self.canvas = tk.Canvas(self.root, width=width, height=height, bg=BG, highlightthickness=0)
        self.canvas.pack()
        self.photos = {}
        self.text_styles = {}
        self.canvas.create_rectangle(4, 5, 270, 177, fill="#050c16", outline="")
        self.canvas.create_rectangle(2, 2, 268, 174, fill=PANEL, outline="#2d465a", width=1)
        self.canvas.create_rectangle(3, 3, 267, 43, fill="#17283a", outline="")
        self.canvas.create_line(14, 3, 79, 3, fill=CYAN, width=round(2 * self.dpi_scale))
        self.make_text(17, 23, "CODEX", TEXT, FONT_LATIN, 15)
        self.make_text(78, 23, "USAGE", MUTED, FONT_LATIN, 10)
        self.canvas.create_rectangle(115, 10, 189, 36, fill="#20384b", outline="#41637a", tags="github")
        avatar_size = round(18 * self.dpi_scale)
        avatar = Image.open(GITHUB_AVATAR).convert("RGBA").resize((avatar_size, avatar_size), Image.Resampling.LANCZOS)
        mask = Image.new("L", (avatar_size, avatar_size))
        ImageDraw.Draw(mask).ellipse((0, 0, avatar_size - 1, avatar_size - 1), fill=255)
        avatar.putalpha(mask)
        self.avatar_photo = ImageTk.PhotoImage(avatar)
        self.canvas.create_image(119, 14, anchor="nw", image=self.avatar_photo, tags="github")
        self.canvas.addtag_withtag("github", self.make_text(143, 23, "GitHub", TEXT, FONT_LATIN, 10))
        self.canvas.tag_bind("github", "<Button-1>", lambda _event: webbrowser.open(GITHUB_URL))
        self.canvas.tag_bind("github", "<Enter>", lambda _event: self.canvas.configure(cursor="hand2"))
        self.canvas.tag_bind("github", "<Leave>", lambda _event: self.canvas.configure(cursor=""))
        self.canvas.create_oval(193, 20, 199, 26, fill=CYAN, outline="")
        self.make_text(205, 23, "LIVE", CYAN, FONT_LATIN, 10)
        self.canvas.create_rectangle(240, 10, 266, 36, fill="#17283a", outline="", tags="minimize")
        self.canvas.create_line(247, 24, 259, 24, fill=MUTED, width=round(2 * self.dpi_scale), tags="minimize")
        self.canvas.tag_bind("minimize", "<Button-1>", lambda _event: self.minimize())
        self.canvas.create_line(17, 106, 253, 106, fill="#213348", width=1)
        self.rows = {}
        for key, y, label, color, glow in (
            ("5h", 61, "5 小时", CYAN, "#214b54"),
            ("week", 121, "周额度", BLUE, "#344363"),
        ):
            self.make_text(17, y, label, LABEL, FONT_BOLD, 13)
            number = self.make_text(253, y, "--%", color, FONT_LATIN, 21, anchor="e")
            bar_y = y + 19
            self.canvas.create_line(19, bar_y, 251, bar_y, fill="#293b50", width=round(6 * self.dpi_scale), capstyle=tk.ROUND)
            halo = self.canvas.create_line(19, bar_y, 19, bar_y, fill=glow, width=round(9 * self.dpi_scale), capstyle=tk.ROUND)
            bar = self.canvas.create_line(19, bar_y, 19, bar_y, fill=color, width=round(5 * self.dpi_scale), capstyle=tk.ROUND)
            reset = self.make_text(17, y + 36, "正在读取", MUTED, FONT_TEXT, 11)
            self.rows[key] = (number, bar, halo, reset, bar_y)
        self.status = self.make_text(253, 157, "30s 刷新", MUTED, FONT_TEXT, 11, anchor="e")
        self.canvas.scale("all", 0, 0, self.dpi_scale, self.dpi_scale)
        self.canvas.bind("<ButtonPress-1>", self.drag_start)
        self.canvas.bind("<B1-Motion>", self.drag_move)
        self.drag = (0, 0)
        self.output = queue.Queue()
        self.client = None
        self.visible = False
        self.minimized = False
        self.root.withdraw()
        self.root.after(100, self.consume)
        self.root.after(100, self.watch)

    def drag_start(self, event):
        if event.x >= 238 * self.dpi_scale and event.y <= 42 * self.dpi_scale:
            return
        if 115 * self.dpi_scale <= event.x <= 189 * self.dpi_scale and event.y <= 42 * self.dpi_scale:
            return
        self.drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def drag_move(self, event):
        self.root.geometry(f"+{event.x_root - self.drag[0]}+{event.y_root - self.drag[1]}")

    def window_handle(self):
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        user32.GetAncestor.restype = ctypes.c_void_p
        return user32.GetAncestor(ctypes.c_void_p(self.root.winfo_id()), 2)

    def apply_taskbar_style(self):
        if not self.visible:
            return None
        user32 = ctypes.windll.user32
        hwnd = self.window_handle()
        user32.GetWindowLongW.argtypes = (ctypes.c_void_p, ctypes.c_int)
        user32.SetWindowLongW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_long)
        user32.SetWindowPos.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint)
        style = user32.GetWindowLongW(hwnd, -20)
        desired = (style | 0x40000) & ~0x80
        if style != desired:
            user32.SetWindowLongW(hwnd, -20, desired)
        user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x23 if style != desired else 0x03)
        return hwnd

    def minimize(self):
        if not self.visible or self.minimized:
            return
        hwnd = self.apply_taskbar_style()
        self.minimized = True
        ctypes.windll.user32.ShowWindow(ctypes.c_void_p(hwnd), 6)

    def hide(self):
        self.root.withdraw()
        self.visible = False
        self.minimized = False

    def watch(self):
        active = codex_running()
        if active and self.client is None:
            self.client = RateClient(self.output)
            self.root.deiconify()
            self.visible = True
            self.root.after(100, self.apply_taskbar_style)
        elif not active and self.client is not None:
            self.client.stop()
            self.client = None
            self.hide()
        elif self.minimized:
            hwnd = self.window_handle()
            if not ctypes.windll.user32.IsIconic(ctypes.c_void_p(hwnd)):
                self.minimized = False
        self.root.after(3000, self.watch)

    def consume(self):
        try:
            while True:
                kind, value = self.output.get_nowait()
                if self.client is None:
                    continue
                if kind == "error":
                    self.set_text(self.status, "30s 后重试")
                    continue
                for key, (number, bar, halo, reset, bar_y) in self.rows.items():
                    item = value.get(key)
                    if item is None:
                        self.set_text(number, "--%")
                        self.canvas.coords(bar, 19 * self.dpi_scale, bar_y * self.dpi_scale, 19 * self.dpi_scale, bar_y * self.dpi_scale)
                        self.canvas.coords(halo, 19 * self.dpi_scale, bar_y * self.dpi_scale, 19 * self.dpi_scale, bar_y * self.dpi_scale)
                        self.set_text(reset, "额度数据待更新")
                        continue
                    left = max(0, min(100, 100 - float(item.get("usedPercent", 0))))
                    self.set_text(number, f"{left:g}%")
                    end = 19 + 232 * left / 100
                    self.canvas.coords(bar, 19 * self.dpi_scale, bar_y * self.dpi_scale, end * self.dpi_scale, bar_y * self.dpi_scale)
                    self.canvas.coords(halo, 19 * self.dpi_scale, bar_y * self.dpi_scale, end * self.dpi_scale, bar_y * self.dpi_scale)
                    self.set_text(reset, format_reset(item.get("resetsAt")))
                self.set_text(self.status, "30s · " + datetime.now().strftime("%H:%M:%S"))
        except queue.Empty:
            pass
        self.root.after(250, self.consume)

    def run(self):
        self.root.mainloop()


def main():
    from ctypes import wintypes

    if "--check" in sys.argv:
        output = queue.Queue()
        client = RateClient(output)
        try:
            kind, value = output.get(timeout=30)
            if kind != "data":
                raise RuntimeError(value)
            assert "5h" in value and "week" in value, "Codex 未返回两个额度窗口"
            print(json.dumps({"codex_running": codex_running(), "remaining": {
                key: 100 - item["usedPercent"] for key, item in value.items()
            }}, ensure_ascii=False))
        finally:
            client.stop()
        return
    # ponytail: one watcher handles login startup and later Codex launches.
    kernel = ctypes.windll.kernel32
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    mutex = kernel.CreateMutexW(None, False, "Local\\CodexQuotaDashboard_v1")
    if kernel.GetLastError() == 183:
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.ShowWindow.argtypes = (ctypes.c_void_p, ctypes.c_int)
        user32.SetForegroundWindow.argtypes = (ctypes.c_void_p,)
        user32.SetWindowPos.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint)
        hwnd = user32.FindWindowW(None, "Codex 额度")
        if hwnd and codex_running():
            user32.ShowWindow(hwnd, 9)
            user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x43)
            user32.SetForegroundWindow(hwnd)
        kernel.CloseHandle(mutex)
        return
    try:
        kernel_user = ctypes.windll.user32
        kernel_user.SetProcessDpiAwarenessContext.argtypes = (ctypes.c_void_p,)
        kernel_user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        Dashboard().run()
    finally:
        kernel.CloseHandle(mutex)


if __name__ == "__main__":
    main()
