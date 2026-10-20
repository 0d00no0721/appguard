#!/usr/bin/env python3
"""极简 Windows 托盘（ctypes，无 pystray/Pillow 依赖）。

只实现 AppGuard 需要的：隐藏窗口、Shell_NotifyIcon、右键菜单、双击、气泡通知。
"""
from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_APP = 0x8000
WM_TRAY = WM_APP + 1

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIIF_INFO = 0x00000001

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_CHECKED = 0x00000008
MF_POPUP = 0x00000010
MF_GRAYED = 0x00000001
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
IDI_APPLICATION = 32512

WM_NULL = 0x0000
SW_RESTORE = 9


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT,
                               wintypes.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                  wintypes.LPVOID]
user32.TrackPopupMenu.restype = wintypes.INT
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                              wintypes.UINT, ctypes.c_int, ctypes.c_int,
                              wintypes.UINT]
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
user32.LoadIconW.restype = wintypes.HICON
user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
user32.LoadCursorW.restype = wintypes.HANDLE
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL


class TrayIcon:
    """一个隐藏窗口 + 托盘图标。

    build_menu() 返回：
      ("item", id, text, checked, enabled)
      ("sep",)
      ("sub", text, [items...])
    """

    def __init__(self, title: str, icon_path: str, build_menu, on_command,
                 on_double_click=None):
        self.title = title
        self.icon_path = icon_path
        self.build_menu = build_menu
        self.on_command = on_command
        self.on_double_click = on_double_click
        self.hwnd = None
        self.hicon = None
        self._class_name = "AppGuardTray-" + str(os.getpid())
        self._wndproc = WNDPROC(self._handle_message)
        self._visible = False
        self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self._lock = threading.Lock()

    # 兼容 pystray 风格接口 -------------------------------------------------
    @property
    def visible(self) -> bool:
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        if value:
            self._add_icon()
        else:
            self._remove_icon()

    def update_menu(self) -> None:
        return None

    def notify(self, message: str, title: str = "AppGuard") -> None:
        if not self.hwnd:
            return
        nid = self._nid(NIF_INFO)
        nid.szInfo = str(message)[:255]
        nid.szInfoTitle = str(title)[:63]
        nid.dwInfoFlags = NIIF_INFO
        nid.uTimeoutOrVersion = 5000
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def stop(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    # 内部 -----------------------------------------------------------------
    def _nid(self, flags: int) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        return nid

    def _add_icon(self) -> None:
        if not self.hwnd or self._visible:
            return
        if not self.hicon:
            if self.icon_path and os.path.isfile(self.icon_path):
                self.hicon = user32.LoadImageW(None, self.icon_path, IMAGE_ICON,
                                               0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if not self.hicon:
                self.hicon = user32.LoadIconW(None, wintypes.LPCWSTR(IDI_APPLICATION))
        nid = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP)
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.hicon
        nid.szTip = self.title[:127]
        if shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            self._visible = True

    def _remove_icon(self) -> None:
        if not self.hwnd or not self._visible:
            return
        nid = self._nid(0)
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        self._visible = False

    def _add_menu_items(self, menu, items) -> None:
        for item in items:
            kind = item[0]
            if kind == "sep":
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            elif kind == "sub":
                sub = user32.CreatePopupMenu()
                self._add_menu_items(sub, item[2])
                user32.AppendMenuW(menu, MF_POPUP, int(sub), item[1])
            else:
                _, item_id, text, checked, enabled = item
                flags = MF_STRING
                if checked:
                    flags |= MF_CHECKED
                if not enabled:
                    flags |= MF_GRAYED
                user32.AppendMenuW(menu, flags, item_id, text)

    def _show_menu(self) -> None:
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            self._add_menu_items(menu, self.build_menu())
            point = POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.SetForegroundWindow(self.hwnd)
            cmd = user32.TrackPopupMenu(
                menu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
                point.x, point.y, 0, self.hwnd, None)
            if cmd:
                self.on_command(int(cmd))
            user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(menu)

    def _handle_message(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            event = int(lparam) & 0xFFFF
            if event == WM_RBUTTONUP:
                self._show_menu()
            elif event == WM_LBUTTONDBLCLK:
                if self.on_double_click:
                    self.on_double_click()
            return 0
        if msg == WM_COMMAND:
            self.on_command(int(wparam) & 0xFFFF)
            return 0
        if msg == self._taskbar_created:
            self._visible = False
            self._add_icon()
            return 0
        if msg == WM_CLOSE:
            self._remove_icon()
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def run(self, setup=None) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.hCursor = user32.LoadCursorW(None, wintypes.LPCWSTR(32512))
        wc.lpszClassName = self._class_name
        if not user32.RegisterClassW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err != 1410:                 # class already exists
                raise OSError(err, "RegisterClassW failed")
        self.hwnd = user32.CreateWindowExW(
            0, self._class_name, "AppGuardTray", 0, 0, 0, 0, 0,
            None, None, hinst, None)
        if not self.hwnd:
            raise OSError(ctypes.get_last_error(), "CreateWindowExW failed")
        self._add_icon()
        if setup is not None:
            setup(self)
        msg = wintypes.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._remove_icon()
        try:
            user32.UnregisterClassW(self._class_name, hinst)
        except Exception:                                       # noqa: BLE001
            pass
