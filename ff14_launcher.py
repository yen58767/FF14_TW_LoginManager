"""
FF14 Login Manager
使用 pywebview 介面，搭配 UI Automation API 操作 FF14 Launcher
"""

VERSION = "1.0.6"
VERSION_CHECK_URL = "https://raw.githubusercontent.com/yen58767/FF14_TW_LoginManager/main/version.json"

import sys
import os
import time
import json
import subprocess
import threading
import urllib.request
from pathlib import Path
import base64
import traceback

# ============ Log 路徑（只在錯誤時寫入） ============
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "FF14LoginManager"
LOG_FILE = LOG_DIR / "error.log"

# 設定 Per-Monitor DPI Awareness，確保多螢幕座標正確
import ctypes
from ctypes import wintypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# 設定 Windows AppUserModelID，讓程式可以正確釘選到工作列
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FF14LoginManager.App")
except Exception:
    pass

import webview
import pyotp
import pystray
from PIL import Image as PILImage

# ============ 單一實例機制 ============
MUTEX_NAME = "Global\\FF14LoginManager_SingleInstance"
EVENT_NAME = "Global\\FF14LoginManager_ShowWindow"

_instance_mutex = None  # 持有 mutex 參考，防止 GC 釋放

def _try_acquire_single_instance():
    """嘗試取得單一實例 mutex。若已有實例在執行，發送喚醒信號並回傳 False。"""
    global _instance_mutex
    ERROR_ALREADY_EXISTS = 183

    # 必須用 use_last_error=True 的 WinDLL，否則 ctypes 內部會覆蓋 last error
    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _instance_mutex = _kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        # 已有實例，發信號讓它顯示視窗
        evt = _kernel32.OpenEventW(0x0002, False, EVENT_NAME)  # EVENT_MODIFY_STATE
        if evt:
            _kernel32.SetEvent(evt)
            _kernel32.CloseHandle(evt)
        if _instance_mutex:
            _kernel32.CloseHandle(_instance_mutex)
            _instance_mutex = None
        return False
    return True

def _start_show_window_listener():
    """背景執行緒：監聽喚醒事件，收到後顯示主視窗。"""
    evt = ctypes.windll.kernel32.CreateEventW(None, False, False, EVENT_NAME)
    if not evt:
        return

    def listener():
        WAIT_OBJECT_0 = 0
        INFINITE = 0xFFFFFFFF
        while True:
            result = ctypes.windll.kernel32.WaitForSingleObject(evt, INFINITE)
            if result == WAIT_OBJECT_0:
                if tray_manager:
                    try:
                        tray_manager._show_window()
                    except Exception:
                        pass

    t = threading.Thread(target=listener, daemon=True)
    t.start()

# Windows UI Automation
import comtypes.client
from comtypes import COMError

# 初始化 UI Automation
UIAutomationClient = comtypes.client.GetModule("UIAutomationCore.dll")
uia = comtypes.client.CreateObject(
    "{ff48dba4-60ef-4201-aa87-54103eef594e}",
    interface=UIAutomationClient.IUIAutomation
)


# ============ DPAPI 加密/解密功能 ============

class DPAPIEncryption:
    """使用 Windows DPAPI 加密/解密敏感資料"""

    @staticmethod
    def encrypt(plaintext: str) -> str:
        """
        使用 DPAPI 加密字串
        返回 base64 編碼的加密資料，格式：dpapi:base64data
        """
        if not plaintext:
            return ""

        try:
            # 將字串轉為 bytes
            plaintext_bytes = plaintext.encode('utf-8')

            # 定義 DATA_BLOB 結構
            class DATA_BLOB(ctypes.Structure):
                _fields_ = [
                    ('cbData', wintypes.DWORD),
                    ('pbData', ctypes.POINTER(ctypes.c_char))
                ]

            # 準備輸入資料
            buffer = ctypes.create_string_buffer(plaintext_bytes)
            blob_in = DATA_BLOB(len(plaintext_bytes), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
            blob_out = DATA_BLOB()

            # 調用 CryptProtectData
            if ctypes.windll.crypt32.CryptProtectData(
                ctypes.byref(blob_in),
                None,  # 描述
                None,  # 可選的額外熵
                None,  # 保留
                None,  # 提示結構
                0,     # 標誌
                ctypes.byref(blob_out)
            ):
                # 讀取加密後的資料
                encrypted_bytes = ctypes.string_at(blob_out.pbData, blob_out.cbData)

                # 釋放記憶體
                ctypes.windll.kernel32.LocalFree(blob_out.pbData)

                # 轉為 base64 並加上前綴
                encrypted_b64 = base64.b64encode(encrypted_bytes).decode('ascii')
                return f"dpapi:{encrypted_b64}"
            else:
                # 加密失敗，返回原始值（向下相容）
                return plaintext

        except Exception as e:
            print(f"加密失敗: {e}")
            return plaintext

    @staticmethod
    def decrypt(encrypted: str) -> str:
        """
        使用 DPAPI 解密字串
        如果不是 dpapi: 前綴，視為明文直接返回（向下相容）
        """
        if not encrypted:
            return ""

        # 檢查是否為加密格式
        if not encrypted.startswith("dpapi:"):
            # 明文格式，直接返回
            return encrypted

        try:
            # 移除前綴並解碼 base64
            encrypted_b64 = encrypted[6:]  # 移除 "dpapi:"
            encrypted_bytes = base64.b64decode(encrypted_b64)

            # 定義 DATA_BLOB 結構
            class DATA_BLOB(ctypes.Structure):
                _fields_ = [
                    ('cbData', wintypes.DWORD),
                    ('pbData', ctypes.POINTER(ctypes.c_char))
                ]

            # 準備輸入資料
            buffer = ctypes.create_string_buffer(encrypted_bytes)
            blob_in = DATA_BLOB(len(encrypted_bytes), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
            blob_out = DATA_BLOB()

            # 調用 CryptUnprotectData
            if ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(blob_in),
                None,
                None,
                None,
                None,
                0,
                ctypes.byref(blob_out)
            ):
                # 讀取解密後的資料
                decrypted_bytes = ctypes.string_at(blob_out.pbData, blob_out.cbData)

                # 釋放記憶體
                ctypes.windll.kernel32.LocalFree(blob_out.pbData)

                # 轉回字串
                return decrypted_bytes.decode('utf-8')
            else:
                # 解密失敗，返回空字串
                print("DPAPI 解密失敗")
                return ""

        except Exception as e:
            print(f"解密失敗: {e}")
            return ""


class ConfigManager:
    """設定檔管理"""
    def __init__(self):
        # 新路徑：使用 Windows AppData\Local
        appdata_local = Path(os.getenv('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
        self.config_dir = appdata_local / "FF14LoginManager"
        self.config_path = self.config_dir / "config.json"

        # 舊路徑（用於遷移）
        self.legacy_config_path = Path.home() / ".ff14_login_config.json"

        # 確保資料夾存在
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # 自動遷移舊檔案
        self._migrate_legacy_config()

        self.config = self.load()

    def _migrate_legacy_config(self):
        """自動遷移舊版設定檔到新位置"""
        if self.legacy_config_path.exists() and not self.config_path.exists():
            try:
                import shutil
                shutil.copy2(self.legacy_config_path, self.config_path)
                print(f"已遷移設定檔：{self.legacy_config_path} → {self.config_path}")
            except Exception as e:
                print(f"遷移設定檔失敗: {e}")

    def load(self) -> dict:
        default = {
            "launcher_path": "",
            "accounts": [],
            "selected_account": -1,
            "theme": "tsuyukusa",
            "brightness": 50,
            "auto_check_update": True,
            "auto_launch": True,
            "auto_input_credentials": False,
            "auto_input_otp": True,
            "auto_press_enter": True,
            "auto_click_play": True,
            "auto_select_character": False,
            "character_select_key": "Numpad0",
            "character_select_key_vk": 96,  # VK_NUMPAD0
            "character_select_delay": 20,
            "character_select_press_count": 6,
            "character_select_interval": 5,
            "launcher_monitor": -1,
            "launcher_monitor_device": "",
            "close_action": "minimize_to_tray",  # minimize_to_tray | quit
            "minimize_action": "taskbar",          # taskbar | tray
            "minimize_after_launch": False,
            "window_x": None,
            "window_y": None,
            "encryption_enabled": True  # 標記是否啟用加密
        }
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)

                    # 清理舊版遺留欄位
                    legacy_keys = ["secret_key", "email", "password"]
                    for key in legacy_keys:
                        if key in loaded:
                            del loaded[key]

                    # 解密帳號資料
                    if "accounts" in loaded:
                        needs_upgrade = False
                        for account in loaded["accounts"]:
                            # 檢查是否需要升級（明文 → 加密）
                            # 排除空字串，只有非空且不是 dpapi: 開頭的才需要升級
                            for field in ["email", "password", "secret_key"]:
                                if field in account and account[field] and not account[field].startswith("dpapi:"):
                                    needs_upgrade = True
                                    break

                            # 解密敏感欄位
                            if "email" in account:
                                account["email"] = DPAPIEncryption.decrypt(account["email"])
                            if "password" in account:
                                account["password"] = DPAPIEncryption.decrypt(account["password"])
                            if "secret_key" in account:
                                account["secret_key"] = DPAPIEncryption.decrypt(account["secret_key"])

                        # 如果偵測到明文資料，自動升級並儲存
                        if needs_upgrade:
                            print("偵測到舊版明文設定，正在升級為加密格式...")
                            loaded["encryption_enabled"] = True
                            # 暫存解密後的資料
                            temp_config = loaded.copy()
                            self.config = temp_config
                            # 重新加密並儲存
                            self.save()

                    # 合併預設值（補齊新增的設定項）
                    for key, value in default.items():
                        if key not in loaded:
                            loaded[key] = value

                    return loaded
            except Exception as e:
                print(f"載入設定失敗: {e}")
                pass
        return default

    def save(self):
        # 深拷貝一份用於加密儲存
        config_to_save = json.loads(json.dumps(self.config))

        # 加密帳號資料
        if "accounts" in config_to_save and config_to_save.get("encryption_enabled", True):
            for account in config_to_save["accounts"]:
                # 加密敏感欄位
                if "email" in account:
                    account["email"] = DPAPIEncryption.encrypt(account["email"])
                if "password" in account:
                    account["password"] = DPAPIEncryption.encrypt(account["password"])
                if "secret_key" in account:
                    account["secret_key"] = DPAPIEncryption.encrypt(account["secret_key"])

        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config_to_save, f, ensure_ascii=False, indent=2)

    def get(self, key: str, default=None):
        return self.config.get(key, default)

    def set(self, key: str, value):
        self.config[key] = value
        self.save()

    def get_all(self) -> dict:
        return self.config.copy()

    def update(self, data: dict):
        self.config.update(data)
        self.save()


class LauncherAutomation:
    """FF14 Launcher 自動化操作"""

    def __init__(self, config: ConfigManager):
        self.config = config
        self.running = False
        self._stop_flag = False

    def stop(self):
        """停止自動化流程"""
        self._stop_flag = True

    @staticmethod
    def get_monitors() -> list[dict]:
        """取得所有螢幕資訊（含型號名稱，從 EDID 解析）"""
        import winreg
        monitors = []

        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(wintypes.RECT),
            ctypes.POINTER(ctypes.c_int),
        )

        CCHDEVICENAME = 32

        class MONITORINFOEX(ctypes.Structure):
            _fields_ = [
                ('cbSize', wintypes.DWORD),
                ('rcMonitor', wintypes.RECT),
                ('rcWork', wintypes.RECT),
                ('dwFlags', wintypes.DWORD),
                ('szDevice', wintypes.WCHAR * CCHDEVICENAME),
            ]

        class DISPLAY_DEVICE(ctypes.Structure):
            _fields_ = [
                ('cb', wintypes.DWORD),
                ('DeviceName', wintypes.WCHAR * 32),
                ('DeviceString', wintypes.WCHAR * 128),
                ('StateFlags', wintypes.DWORD),
                ('DeviceID', wintypes.WCHAR * 128),
                ('DeviceKey', wintypes.WCHAR * 128),
            ]

        def parse_edid_name(edid: bytes) -> str:
            """從 EDID 二進位資料解析螢幕型號（descriptor tag 0xFC）"""
            for i in range(4):
                offset = 54 + i * 18
                if offset + 18 > len(edid):
                    break
                # 檢查 descriptor tag: 00 00 00 FC
                if edid[offset:offset+4] == b'\x00\x00\x00\xfc':
                    name_bytes = edid[offset+5:offset+18]
                    name = name_bytes.split(b'\x0a')[0].decode('ascii', errors='ignore').strip()
                    if name:
                        return name
            return ''

        def build_edid_map() -> dict:
            """從 registry 讀取所有螢幕的 EDID，建立 device_id → 型號 的對應"""
            edid_map = {}
            reg_path = r"SYSTEM\CurrentControlSet\Enum\DISPLAY"
            try:
                display_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path)
                i = 0
                while True:
                    try:
                        monitor_id = winreg.EnumKey(display_key, i)
                        i += 1
                        monitor_key = winreg.OpenKey(display_key, monitor_id)
                        j = 0
                        while True:
                            try:
                                instance = winreg.EnumKey(monitor_key, j)
                                j += 1
                                try:
                                    param_key = winreg.OpenKey(
                                        monitor_key, f"{instance}\\Device Parameters"
                                    )
                                    edid, _ = winreg.QueryValueEx(param_key, "EDID")
                                    winreg.CloseKey(param_key)
                                    if edid:
                                        name = parse_edid_name(bytes(edid))
                                        if name:
                                            full_id = f"MONITOR\\{monitor_id}\\{instance}"
                                            edid_map[full_id.upper()] = name
                                except (FileNotFoundError, OSError):
                                    pass
                            except OSError:
                                break
                        winreg.CloseKey(monitor_key)
                    except OSError:
                        break
                winreg.CloseKey(display_key)
            except OSError:
                pass
            return edid_map

        edid_map = build_edid_map()

        def get_monitor_name(device_name):
            """透過 EnumDisplayDevices 取得 DeviceID，再從 EDID map 查型號"""
            dd = DISPLAY_DEVICE()
            dd.cb = ctypes.sizeof(DISPLAY_DEVICE)
            if ctypes.windll.user32.EnumDisplayDevicesW(device_name, 0, ctypes.byref(dd), 0):
                device_id = dd.DeviceID.strip().upper()
                # DeviceID 格式: MONITOR\XXX\{GUID}，取前兩段比對
                for key, name in edid_map.items():
                    # registry key: MONITOR\XXX\instance
                    # DeviceID:     MONITOR\XXX\{guid}
                    # 比對 MONITOR\XXX 部分
                    if key.split('\\')[1] == device_id.split('\\')[1]:
                        return name
            return ''

        def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
            info = MONITORINFOEX()
            info.cbSize = ctypes.sizeof(MONITORINFOEX)
            ctypes.windll.user32.GetMonitorInfoW(hMonitor, ctypes.byref(info))

            name = get_monitor_name(info.szDevice)

            monitors.append({
                'index': len(monitors),
                'device': info.szDevice.strip(),
                'x': info.rcWork.left,
                'y': info.rcWork.top,
                'width': info.rcWork.right - info.rcWork.left,
                'height': info.rcWork.bottom - info.rcWork.top,
                'primary': bool(info.dwFlags & 1),
                'name': name,
            })
            return True

        ctypes.windll.user32.EnumDisplayMonitors(
            None, None, MONITORENUMPROC(callback), 0
        )
        return monitors

    @staticmethod
    def move_window_to_monitor(launcher_element, monitor_index: int, monitor_device: str = ""):
        """將 Launcher 視窗移到指定螢幕中央（優先用 device name 比對，fallback 用 index）"""
        monitors = LauncherAutomation.get_monitors()

        # 優先用 device name 找到正確螢幕
        m = None
        if monitor_device:
            for mon in monitors:
                if mon['device'] == monitor_device:
                    m = mon
                    break

        # fallback: 用 index
        if m is None:
            if monitor_index < 0 or monitor_index >= len(monitors):
                return
            m = monitors[monitor_index]
        try:
            hwnd = launcher_element.CurrentNativeWindowHandle
            if not hwnd:
                return

            # 取得視窗目前大小
            rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            win_w = rect.right - rect.left
            win_h = rect.bottom - rect.top

            # 計算螢幕中央位置
            x = m['x'] + (m['width'] - win_w) // 2
            y = m['y'] + (m['height'] - win_h) // 2

            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            ctypes.windll.user32.SetWindowPos(hwnd, 0, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER)
        except Exception as e:
            print(f"移動視窗失敗: {e}")

    def find_window_by_keywords(self, root, keywords: list[str]):
        """模糊搜尋視窗"""
        condition = uia.CreatePropertyCondition(
            UIAutomationClient.UIA_ControlTypePropertyId,
            UIAutomationClient.UIA_WindowControlTypeId
        )

        windows = root.FindAll(
            UIAutomationClient.TreeScope_Children,
            condition
        )

        for i in range(windows.Length):
            window = windows.GetElement(i)
            name = window.CurrentName or ""
            name_upper = name.upper()

            if all(kw.upper() in name_upper for kw in keywords):
                return window

        return None

    def launch_game(self) -> tuple[bool, str]:
        """啟動 Launcher"""
        launcher_path = self.config.get("launcher_path", "")

        if not launcher_path or not os.path.exists(launcher_path):
            return False, "啟動器路徑無效"

        try:
            subprocess.Popen(launcher_path, shell=True)
            return True, "啟動器已啟動"
        except Exception as e:
            return False, f"啟動失敗: {str(e)}"

    def wait_for_window(self, timeout: int = 30) -> tuple[bool, str, any]:
        """等待 Launcher 視窗出現"""
        search_keywords = ["FANTASY", "XIV"]
        start_time = time.time()

        while time.time() - start_time < timeout:
            if self._stop_flag:
                return False, "已取消", None

            root = uia.GetRootElement()
            launcher = self.find_window_by_keywords(root, search_keywords)

            if launcher:
                return True, "找到 Launcher 視窗", launcher

            time.sleep(0.5)

        return False, "等待 Launcher 視窗逾時", None

    def find_credential_inputs(self, launcher, timeout: int = 30) -> tuple[bool, str, any, any]:
        """尋找信箱和密碼輸入框"""
        edit_condition = uia.CreatePropertyCondition(
            UIAutomationClient.UIA_ControlTypePropertyId,
            UIAutomationClient.UIA_EditControlTypeId
        )

        start_time = time.time()

        while time.time() - start_time < timeout:
            if self._stop_flag:
                return False, "已取消", None, None

            edits = launcher.FindAll(
                UIAutomationClient.TreeScope_Descendants,
                edit_condition
            )

            # 需要至少2個輸入框（信箱、密碼）
            if edits.Length >= 2:
                # 假設第一個是信箱，第二個是密碼
                email_edit = edits.GetElement(0)
                password_edit = edits.GetElement(1)
                return True, "找到登入輸入框", email_edit, password_edit

            time.sleep(0.5)

        return False, "找不到登入輸入框", None, None

    def input_credentials(self, email_edit, password_edit, email: str, password: str) -> tuple[bool, str]:
        """輸入信箱和密碼"""
        try:
            # 輸入信箱
            email_pattern = email_edit.GetCurrentPattern(
                UIAutomationClient.UIA_ValuePatternId
            ).QueryInterface(UIAutomationClient.IUIAutomationValuePattern)
            email_pattern.SetValue(email)

            time.sleep(0.2)

            # 輸入密碼
            password_pattern = password_edit.GetCurrentPattern(
                UIAutomationClient.UIA_ValuePatternId
            ).QueryInterface(UIAutomationClient.IUIAutomationValuePattern)
            password_pattern.SetValue(password)

            return True, "已輸入帳號密碼"
        except COMError:
            return False, "無法輸入帳號密碼"

    def find_otp_input(self, launcher, timeout: int = 30) -> tuple[bool, str, any]:
        """尋找 OTP 輸入框"""
        edit_condition = uia.CreatePropertyCondition(
            UIAutomationClient.UIA_ControlTypePropertyId,
            UIAutomationClient.UIA_EditControlTypeId
        )

        start_time = time.time()

        while time.time() - start_time < timeout:
            if self._stop_flag:
                return False, "已取消", None

            edits = launcher.FindAll(
                UIAutomationClient.TreeScope_Descendants,
                edit_condition
            )

            if edits.Length > 0:
                # 嘗試找到 OTP 輸入框
                otp_edit = None
                for i in range(edits.Length):
                    edit = edits.GetElement(i)
                    name = edit.CurrentName or ""
                    if "一次性" in name or "驗證碼" in name or "otp" in name.lower():
                        otp_edit = edit
                        break

                # 如果找不到特定名稱的，使用最後一個 Edit
                if not otp_edit:
                    otp_edit = edits.GetElement(edits.Length - 1)

                return True, "找到 OTP 輸入框", otp_edit

            time.sleep(0.5)

        return False, "找不到 OTP 輸入框", None

    def input_otp(self, otp_edit, otp: str) -> tuple[bool, str]:
        """輸入 OTP"""
        try:
            value_pattern = otp_edit.GetCurrentPattern(
                UIAutomationClient.UIA_ValuePatternId
            ).QueryInterface(UIAutomationClient.IUIAutomationValuePattern)

            value_pattern.SetValue(otp)
            return True, f"已輸入 OTP: {otp}"
        except COMError:
            return False, "無法寫入輸入框"

    def press_enter(self, element) -> tuple[bool, str]:
        """按下 Enter"""
        try:
            # 使用 SendKeys 發送 Enter
            import ctypes
            from ctypes import wintypes

            # 設定焦點到元素
            try:
                element.SetFocus()
            except:
                pass

            time.sleep(0.1)

            # 發送 Enter 鍵
            user32 = ctypes.windll.user32
            VK_RETURN = 0x0D
            KEYEVENTF_KEYUP = 0x0002

            user32.keybd_event(VK_RETURN, 0, 0, 0)
            user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)

            return True, "已按下 Enter"
        except Exception as e:
            return False, f"按 Enter 失敗: {str(e)}"

    def find_play_button(self, launcher, timeout: int = 60) -> tuple[bool, str, any]:
        """尋找 PLAY 按鈕"""
        start_time = time.time()

        while time.time() - start_time < timeout:
            if self._stop_flag:
                return False, "已取消", None

            # 尋找按鈕
            button_condition = uia.CreatePropertyCondition(
                UIAutomationClient.UIA_ControlTypePropertyId,
                UIAutomationClient.UIA_ButtonControlTypeId
            )

            buttons = launcher.FindAll(
                UIAutomationClient.TreeScope_Descendants,
                button_condition
            )

            for i in range(buttons.Length):
                button = buttons.GetElement(i)
                name = (button.CurrentName or "").upper()
                if "PLAY" in name or "開始" in name or "START" in name:
                    return True, "找到 PLAY 按鈕", button

            time.sleep(0.5)

        return False, "找不到 PLAY 按鈕", None

    def click_play_button(self, button) -> tuple[bool, str]:
        """點擊 PLAY 按鈕"""
        try:
            invoke_pattern = button.GetCurrentPattern(
                UIAutomationClient.UIA_InvokePatternId
            ).QueryInterface(UIAutomationClient.IUIAutomationInvokePattern)

            invoke_pattern.Invoke()
            return True, "已點擊 PLAY 按鈕"
        except COMError:
            # 嘗試使用滑鼠點擊
            try:
                import ctypes

                # 取得按鈕位置
                rect = button.CurrentBoundingRectangle
                x = int((rect.left + rect.right) / 2)
                y = int((rect.top + rect.bottom) / 2)

                # 移動滑鼠並點擊
                ctypes.windll.user32.SetCursorPos(x, y)
                time.sleep(0.1)

                MOUSEEVENTF_LEFTDOWN = 0x0002
                MOUSEEVENTF_LEFTUP = 0x0004

                ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

                return True, "已點擊 PLAY 按鈕"
            except Exception as e:
                return False, f"點擊 PLAY 失敗: {str(e)}"

    def select_character(self, key_vk: int, delay: int, press_count: int, interval: int, status_callback) -> tuple[bool, str]:
        """等待遊戲啟動後模擬按鍵進入角色"""
        import ctypes

        user32 = ctypes.windll.user32
        KEYEVENTF_KEYUP = 0x0002

        # 1. 等待 ffxiv_dx11.exe 出現（最長 120 秒）
        status_callback("等待遊戲啟動...")
        start_time = time.time()
        game_found = False

        while time.time() - start_time < 120:
            if self._stop_flag:
                return False, "已取消"

            # 偵測遊戲進程
            try:
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                EnumProcesses = ctypes.windll.psapi.EnumProcesses
                GetProcessImageFileNameW = ctypes.windll.psapi.GetProcessImageFileNameW
                OpenProcess = ctypes.windll.kernel32.OpenProcess
                CloseHandle = ctypes.windll.kernel32.CloseHandle

                process_ids = (ctypes.c_ulong * 2048)()
                bytes_returned = ctypes.c_ulong()
                EnumProcesses(process_ids, ctypes.sizeof(process_ids), ctypes.byref(bytes_returned))
                num_processes = bytes_returned.value // ctypes.sizeof(ctypes.c_ulong)

                for i in range(num_processes):
                    pid = process_ids[i]
                    if pid == 0:
                        continue
                    handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                    if handle:
                        try:
                            image_name = ctypes.create_unicode_buffer(512)
                            if GetProcessImageFileNameW(handle, image_name, 512):
                                exe_name = image_name.value.split("\\")[-1].lower()
                                if exe_name in ["ffxiv_dx11.exe", "ffxiv.exe"]:
                                    game_found = True
                                    break
                        finally:
                            CloseHandle(handle)

                if game_found:
                    break
            except Exception:
                pass

            time.sleep(1)

        if not game_found:
            return False, "等待遊戲啟動逾時"

        # 2. 等待初始載入
        if delay > 0:
            status_callback("遊戲已啟動，等待載入...")
            for i in range(delay):
                if self._stop_flag:
                    return False, "已取消"
                remaining = delay - i
                status_callback(f"等待遊戲載入... ({remaining}s)")
                time.sleep(1)

        # 3. 按 key_vk，可設定次數與間隔
        for i in range(press_count):
            if self._stop_flag:
                return False, "已取消"

            status_callback(f"模擬按鍵 ({i + 1}/{press_count})...")
            user32.keybd_event(key_vk, 0, 0, 0)
            user32.keybd_event(key_vk, 0, KEYEVENTF_KEYUP, 0)

            if i < press_count - 1:  # 最後一次不需要等待
                time.sleep(interval)

        return True, "已完成角色選擇"

    def run_automation(self, secret_key: str, email: str, password: str, status_callback) -> tuple[bool, str]:
        """執行完整自動化流程"""
        self.running = True
        self._stop_flag = False
        launcher = None

        try:
            # 產生 OTP
            try:
                secret = secret_key.strip().replace(" ", "")
                totp = pyotp.TOTP(secret)
                otp = totp.now()
            except Exception as e:
                return False, f"OTP 產生失敗: {str(e)}"

            # 步驟 1: 啟動 Launcher
            if self.config.get("auto_launch"):
                status_callback("正在啟動 Launcher...")
                success, msg = self.launch_game()
                if not success:
                    return False, msg
                status_callback(msg)
                time.sleep(2)

            # 步驟 2: 等待視窗
            status_callback("正在等待 Launcher 視窗...")
            success, msg, launcher = self.wait_for_window()
            if not success:
                return False, msg
            status_callback(msg)

            # 移動到指定螢幕
            monitor_index = self.config.get("launcher_monitor", -1)
            monitor_device = self.config.get("launcher_monitor_device", "")
            if monitor_index >= 0 or monitor_device:
                status_callback("正在移動視窗到指定螢幕...")
                self.move_window_to_monitor(launcher, monitor_index, monitor_device)
                time.sleep(0.5)

            # 步驟 2.5: 輸入帳號密碼
            login_found = False
            if self.config.get("auto_input_credentials") and email and password:
                status_callback("正在尋找登入輸入框...")
                success, msg, email_edit, password_edit = self.find_credential_inputs(launcher, timeout=5)
                if success:
                    login_found = True
                    status_callback(msg)
                    success, msg = self.input_credentials(email_edit, password_edit, email, password)
                    if not success:
                        return False, msg
                    status_callback(msg)
                    time.sleep(0.5)

            # 步驟 3: 尋找並輸入 OTP
            if self.config.get("auto_input_otp"):
                status_callback("正在尋找 OTP 輸入框...")
                success, msg, otp_edit = self.find_otp_input(launcher, timeout=5)
                if success:
                    status_callback(msg)
                    success, msg = self.input_otp(otp_edit, otp)
                    if not success:
                        return False, msg
                    status_callback(msg)

                    # 步驟 4: 按 Enter
                    if self.config.get("auto_press_enter"):
                        time.sleep(0.3)
                        status_callback("正在按下 Enter...")
                        success, msg = self.press_enter(otp_edit)
                        if not success:
                            return False, msg
                        status_callback(msg)
                else:
                    # 找不到 OTP 輸入框，可能已經登入，嘗試找 PLAY
                    status_callback("找不到 OTP 輸入框，嘗試尋找 PLAY...")

            # 步驟 5: 點擊 PLAY
            if self.config.get("auto_click_play"):
                time.sleep(1)
                status_callback("正在等待 PLAY 按鈕...")
                success, msg, play_button = self.find_play_button(launcher)
                if not success:
                    return False, msg
                status_callback(msg)

                success, msg = self.click_play_button(play_button)
                if not success:
                    return False, msg
                status_callback(msg)

            # 步驟 6: 自動進入角色
            if self.config.get("auto_select_character"):
                key_vk = self.config.get("character_select_key_vk", 96)
                delay = self.config.get("character_select_delay", 20)
                press_count = self.config.get("character_select_press_count", 6)
                interval = self.config.get("character_select_interval", 5)
                success, msg = self.select_character(key_vk, delay, press_count, interval, status_callback)
                if not success:
                    return False, msg
                status_callback(msg)

            return True, "自動化完成"

        except Exception as e:
            return False, f"發生錯誤: {str(e)}"
        finally:
            self.running = False


# 全域物件
config = ConfigManager()
automation = LauncherAutomation(config)
window = None
tray_manager = None
_force_quit = False


def resolve_shortcut(lnk_path: str) -> str:
    """
    解析 .lnk 捷徑檔案，取得實際目標路徑
    支援一般捷徑和 Windows Installer 廣告捷徑
    """
    if not lnk_path.lower().endswith('.lnk'):
        return lnk_path

    if not os.path.exists(lnk_path):
        return lnk_path

    target_path = None

    try:
        # 方法1: 使用 COM Shell 物件解析一般捷徑
        import comtypes.client

        # 建立 Shell 物件
        shell = comtypes.client.CreateObject("WScript.Shell")
        shortcut = shell.CreateShortcut(lnk_path)
        target_path = shortcut.TargetPath

        # 如果取得的路徑有效且存在，直接返回
        if target_path and os.path.exists(target_path):
            return target_path

    except Exception as e:
        print(f"WScript.Shell 解析失敗: {e}")

    # 方法2: 嘗試解析 Windows Installer 廣告捷徑
    # 這類捷徑的 TargetPath 可能是空的或指向 Installer 快取
    try:
        # 使用 MsiGetShortcutTarget API
        msi = ctypes.windll.msi

        # 準備緩衝區
        product_code = ctypes.create_unicode_buffer(39)  # GUID + null
        feature_id = ctypes.create_unicode_buffer(256)
        component_code = ctypes.create_unicode_buffer(39)

        result = msi.MsiGetShortcutTargetW(
            lnk_path,
            product_code,
            feature_id,
            component_code
        )

        if result == 0:  # ERROR_SUCCESS
            # 取得元件的安裝路徑
            path_buffer = ctypes.create_unicode_buffer(512)
            path_len = ctypes.c_uint(512)

            # INSTALLSTATE_LOCAL = 3
            state = msi.MsiGetComponentPathW(
                product_code.value,
                component_code.value,
                path_buffer,
                ctypes.byref(path_len)
            )

            if state >= 1:  # INSTALLSTATE_LOCAL or better
                resolved_path = path_buffer.value
                if resolved_path and os.path.exists(resolved_path):
                    return resolved_path
    except Exception as e:
        print(f"MSI 解析失敗: {e}")

    # 方法3: 使用 PowerShell 作為備用方案
    try:
        import subprocess
        # 使用 PowerShell 解析捷徑
        ps_command = f'''
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut("{lnk_path}")
$shortcut.TargetPath
'''
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', ps_command],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            ps_target = result.stdout.strip()
            if ps_target and os.path.exists(ps_target):
                return ps_target
    except Exception as e:
        print(f"PowerShell 解析失敗: {e}")

    # 如果所有方法都失敗，返回原始 TargetPath（如果有的話）
    if target_path and target_path != lnk_path:
        return target_path

    return lnk_path


class WINDOWPLACEMENT(ctypes.Structure):
    """Windows API WINDOWPLACEMENT 結構"""
    _fields_ = [
        ("length", wintypes.UINT),
        ("flags", wintypes.UINT),
        ("showCmd", wintypes.UINT),
        ("ptMinPosition", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("rcNormalPosition", wintypes.RECT),
    ]


class SystemTrayManager:
    """系統匣圖示管理"""

    def __init__(self, icon_path):
        self.icon_path = icon_path
        self.tray_icon = None
        self._window = None
        self._placement = None
        self._tk_root = None

    def set_window(self, win):
        self._window = win

    # 主題色對應表（與 CSS 同步）
    THEME_COLORS = {
        'tsuyukusa': {'primary': '#2EA9DF', 'primary_dark': '#1E7EAB', 'card_h': 210, 'card_s': 35, 'text': '#E0E6ED', 'text2': '#8899A6'},
        'shu':       {'primary': '#F75C2F', 'primary_dark': '#C44A25', 'card_h': 15,  'card_s': 40, 'text': '#F5E6E0', 'text2': '#A08070'},
        'koke':      {'primary': '#4B4E2A', 'primary_dark': '#3A3D20', 'card_h': 70,  'card_s': 20, 'text': '#E5E8D8', 'text2': '#8A8D70'},
        'wakatake':  {'primary': '#A8D8B9', 'primary_dark': '#7BBF95', 'card_h': 145, 'card_s': 30, 'text': '#E0F0E8', 'text2': '#70A088'},
        'fuji':      {'primary': '#8B81C3', 'primary_dark': '#6A5FA0', 'card_h': 250, 'card_s': 30, 'text': '#E8E6F0', 'text2': '#9088A0'},
        'sakura':    {'primary': '#FEDFE1', 'primary_dark': '#F5B2B8', 'card_h': 355, 'card_s': 45, 'text': '#F5E8E8', 'text2': '#A08888'},
        'gunjou':    {'primary': '#465DAA', 'primary_dark': '#354788', 'card_h': 225, 'card_s': 40, 'text': '#E0E4F0', 'text2': '#8090A8'},
        'ukon':      {'primary': '#EFBB24', 'primary_dark': '#D4A520', 'card_h': 45,  'card_s': 40, 'text': '#F0EBE0', 'text2': '#A09878'},
    }

    @staticmethod
    def _hsl_to_hex(h, s, l):
        """HSL (h=0-360, s=0-100, l=0-100) → #RRGGBB"""
        import colorsys
        r, g, b = colorsys.hls_to_rgb(h / 360, l / 100, s / 100)
        return f'#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}'

    def _get_menu_colors(self):
        """根據當前主題和亮度產生選單配色"""
        theme_name = config.get("theme", "tsuyukusa")
        brightness = config.get("brightness", 50)
        t = self.THEME_COLORS.get(theme_name, self.THEME_COLORS['tsuyukusa'])
        br = brightness / 100  # 0~1

        bg_l = 6 + br * 89  # 同 CSS card-l
        bg = self._hsl_to_hex(t['card_h'], t['card_s'], bg_l)
        hover_l = min(bg_l + 8, 95)
        hover_bg = self._hsl_to_hex(t['card_h'], t['card_s'], hover_l)
        sep_l = min(bg_l + 4, 90)
        sep_color = self._hsl_to_hex(t['card_h'], t['card_s'], sep_l)

        # 同 CSS: brightness > 35 切換深色文字
        if brightness > 35:
            fg = '#1a1a2e'
            hover_fg = '#000000'
        else:
            fg = t['text']
            hover_fg = '#FFFFFF'

        return {
            'bg': bg, 'fg': fg, 'hover_bg': hover_bg,
            'hover_fg': hover_fg, 'sep': sep_color, 'primary': t['primary'],
        }

    def _show_custom_menu(self):
        """顯示自訂右鍵選單（跟隨視窗主題配色）"""
        import tkinter as tk

        if not self._tk_root:
            self._tk_root = tk.Tk()
            self._tk_root.withdraw()

        c = self._get_menu_colors()
        BG = c['bg']
        FG = c['fg']
        HOVER_BG = c['hover_bg']
        HOVER_FG = c['hover_fg']
        SEP_COLOR = c['sep']
        FONT = ('Microsoft JhengHei UI', 11)
        RADIUS = 14

        popup = tk.Toplevel(self._tk_root)
        popup.overrideredirect(True)
        popup.attributes('-topmost', True)
        popup.configure(bg=BG)

        # 外層容器提供上下留白
        container = tk.Frame(popup, bg=BG)
        container.pack(fill='both', expand=True, padx=0, pady=6)

        items = [
            ('啟動遊戲', self._launch_game),
            None,
            ('開啟視窗', self._show_window),
            None,
            ('結束程式', self._quit),
        ]

        for item in items:
            if item is None:
                tk.Frame(container, bg=SEP_COLOR, height=1).pack(fill='x', padx=8, pady=2)
            else:
                label_text, command = item
                row = tk.Frame(container, bg=BG, cursor='hand2')
                row.pack(fill='x')
                lbl = tk.Label(
                    row, text=label_text, font=FONT,
                    bg=BG, fg=FG, anchor='center',
                    padx=24, pady=8,
                )
                lbl.pack(fill='both', expand=True)

                def make_handlers(frame, label, cmd):
                    def on_enter(e):
                        frame.configure(bg=HOVER_BG)
                        label.configure(bg=HOVER_BG, fg=HOVER_FG)
                    def on_leave(e):
                        frame.configure(bg=BG)
                        label.configure(bg=BG, fg=FG)
                    def on_click(e):
                        popup.destroy()
                        cmd()
                    return on_enter, on_leave, on_click

                enter, leave, click = make_handlers(row, lbl, command)
                row.bind('<Enter>', enter)
                row.bind('<Leave>', leave)
                row.bind('<Button-1>', click)
                lbl.bind('<Button-1>', click)

        # 取得游標位置（在 update 之前先抓，避免延遲）
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))

        popup.update_idletasks()
        w = popup.winfo_width()
        h = popup.winfo_height()

        # 定位在游標上方
        x = pt.x - w // 2
        y = pt.y - h - 4

        # 確保不超出游標所在螢幕的工作區域
        MONITOR_DEFAULTTONEAREST = 2
        hmon = ctypes.windll.user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
        from ctypes import sizeof as ct_sizeof
        class _MONITORINFO(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.DWORD),
                        ('rcMonitor', wintypes.RECT),
                        ('rcWork', wintypes.RECT),
                        ('dwFlags', wintypes.DWORD)]
        mi = _MONITORINFO()
        mi.cbSize = ct_sizeof(_MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        mon_left, mon_top = mi.rcWork.left, mi.rcWork.top
        mon_right, mon_bottom = mi.rcWork.right, mi.rcWork.bottom
        if x < mon_left:
            x = mon_left
        if x + w > mon_right:
            x = mon_right - w
        if y < mon_top:
            y = pt.y + 10

        popup.geometry(f'+{x}+{y}')
        popup.update()

        # 圓角 — frame() 回傳真正的 OS 視窗 HWND
        try:
            hwnd = int(popup.frame(), 16)
            rgn = ctypes.windll.gdi32.CreateRoundRectRgn(
                0, 0, w + 1, h + 1, RADIUS, RADIUS
            )
            ctypes.windll.user32.SetWindowRgn(hwnd, rgn, True)
        except Exception:
            pass

        # 點擊外部關閉
        popup.bind('<FocusOut>', lambda e: popup.destroy())
        popup.focus_force()
        popup.wait_window(popup)

    def _is_game_running(self):
        """檢查 ffxiv_dx11.exe / ffxiv.exe 是否正在執行"""
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            OpenProcess = ctypes.windll.kernel32.OpenProcess
            CloseHandle = ctypes.windll.kernel32.CloseHandle
            EnumProcesses = ctypes.windll.psapi.EnumProcesses
            GetProcessImageFileNameW = ctypes.windll.psapi.GetProcessImageFileNameW

            pids = (ctypes.c_ulong * 2048)()
            cb = ctypes.c_ulong()
            EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(cb))

            for i in range(cb.value // ctypes.sizeof(ctypes.c_ulong)):
                pid = pids[i]
                if pid == 0:
                    continue
                handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if handle:
                    try:
                        name = ctypes.create_unicode_buffer(512)
                        if GetProcessImageFileNameW(handle, name, 512):
                            exe = name.value.split("\\")[-1].lower()
                            if exe in ("ffxiv_dx11.exe", "ffxiv.exe"):
                                return True
                    finally:
                        CloseHandle(handle)
        except Exception:
            pass
        return False

    def _confirm_launch(self):
        """遊戲已啟動時彈出確認對話框，回傳 True 表示繼續"""
        import tkinter as tk
        from tkinter import messagebox

        if not self._tk_root:
            self._tk_root = tk.Tk()
            self._tk_root.withdraw()

        result = messagebox.askyesno(
            "遊戲已在執行中",
            "偵測到遊戲已經在執行中，確定要再次啟動嗎？",
            parent=self._tk_root,
        )
        return result

    def _launch_game(self, *args):
        """從系統匣啟動遊戲（完整自動化流程）"""
        if self._is_game_running():
            if not self._confirm_launch():
                return

        accounts = config.get("accounts", [])
        selected = config.get("selected_account", -1)

        if isinstance(selected, int) and 0 <= selected < len(accounts):
            account = accounts[selected]
            secret_key = account.get("secret_key", "")
            email = account.get("email", "")
            password = account.get("password", "")

            if secret_key:
                def run():
                    automation.run_automation(
                        secret_key, email, password,
                        lambda msg: None
                    )
                    # 自動化完成後確保主視窗不會跑出來
                    if self._window:
                        self._window.hide()

                thread = threading.Thread(target=run, daemon=True)
                thread.start()
                # 啟動後立刻隱藏，避免主視窗被帶出來
                if self._window:
                    self._window.hide()

    def save_placement(self):
        """用 GetWindowPlacement 儲存視窗的正常位置（即使已最小化也能取得）"""
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "FF14 Login Manager")
        if hwnd:
            placement = WINDOWPLACEMENT()
            placement.length = ctypes.sizeof(WINDOWPLACEMENT)
            if user32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
                self._placement = placement

    def _show_window(self, *args):
        """用 SetWindowPlacement 一步還原視窗到原位，不抖動，並強制置頂"""
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "FF14 Login Manager")
        if hwnd and self._placement:
            self._placement.showCmd = 1  # SW_SHOWNORMAL
            user32.SetWindowPlacement(hwnd, ctypes.byref(self._placement))
            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
            user32.SetForegroundWindow(hwnd)
        elif hwnd:
            # 沒有 placement 但 hwnd 存在：用 ShowWindow 顯示並置頂
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
            user32.SetForegroundWindow(hwnd)
        elif self._window:
            self._window.show()
            self._window.restore()

    def _quit(self, *args):
        """結束程式"""
        global _force_quit
        _force_quit = True
        save_window_position()
        if self._tk_root:
            try:
                self._tk_root.destroy()
            except Exception:
                pass
        if self.tray_icon:
            self.tray_icon.stop()
        if self._window:
            self._window.destroy()

    def start(self):
        """啟動系統匣圖示"""
        try:
            image = PILImage.open(self.icon_path)
        except Exception:
            image = PILImage.new('RGB', (64, 64), color=(70, 130, 180))

        self.tray_icon = pystray.Icon(
            "FF14LoginManager",
            image,
            "FF14 Login Manager",
            menu=pystray.Menu(
                pystray.MenuItem("啟動遊戲", self._launch_game),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("開啟視窗", self._show_window, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("結束程式", self._quit),
            )
        )

        # 替換原生右鍵選單為自訂櫻花奶茶選單
        WM_LBUTTONUP = 514
        WM_RBUTTONUP = 517
        WM_NOTIFY = 1035  # pystray 自訂的 notification message
        original_on_notify = self.tray_icon._message_handlers[WM_NOTIFY]
        tray_mgr = self

        def patched_on_notify(wparam, lparam):
            if lparam == WM_RBUTTONUP:
                tray_mgr._show_custom_menu()
                return
            if lparam == WM_LBUTTONUP:
                tray_mgr._show_window()
                return
            return original_on_notify(wparam, lparam)

        self.tray_icon._message_handlers[WM_NOTIFY] = patched_on_notify

        tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        tray_thread.start()

    def stop(self):
        """停止系統匣圖示"""
        if self.tray_icon:
            self.tray_icon.stop()


class Api:
    """pywebview API - 提供給 JavaScript 呼叫的方法"""

    def get_config(self):
        """取得設定"""
        return config.get_all()

    def get_monitors(self):
        """取得所有螢幕資訊"""
        return LauncherAutomation.get_monitors()

    def save_config(self, data: dict):
        """儲存設定"""
        config.update(data)
        return True

    def get_otp(self, secret_key: str):
        """取得當前 OTP"""
        if not secret_key:
            return {"otp": "------", "remaining": 0, "error": "請輸入 Secret Key"}

        try:
            secret = secret_key.strip().replace(" ", "")
            totp = pyotp.TOTP(secret)
            otp = totp.now()
            remaining = totp.interval - (int(time.time()) % totp.interval)
            return {"otp": otp, "remaining": remaining, "error": None}
        except Exception as e:
            return {"otp": "------", "remaining": 0, "error": "Secret Key 格式錯誤"}

    def get_config_path(self):
        """取得設定檔路徑"""
        return str(config.config_path)

    def open_config_folder(self):
        """開啟設定檔資料夾"""
        try:
            folder_path = str(config.config_dir)
            os.startfile(folder_path)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def browse_launcher_path(self):
        """開啟檔案選擇對話框（使用 Windows 原生對話框）"""
        try:
            import ctypes
            from ctypes import wintypes

            # 使用 Windows API GetOpenFileName
            OFN_FILEMUSTEXIST = 0x00001000
            OFN_PATHMUSTEXIST = 0x00000800
            OFN_NOCHANGEDIR = 0x00000008
            MAX_PATH = 260

            class OPENFILENAME(ctypes.Structure):
                _fields_ = [
                    ("lStructSize", wintypes.DWORD),
                    ("hwndOwner", wintypes.HWND),
                    ("hInstance", wintypes.HINSTANCE),
                    ("lpstrFilter", wintypes.LPCWSTR),
                    ("lpstrCustomFilter", wintypes.LPWSTR),
                    ("nMaxCustFilter", wintypes.DWORD),
                    ("nFilterIndex", wintypes.DWORD),
                    ("lpstrFile", wintypes.LPWSTR),
                    ("nMaxFile", wintypes.DWORD),
                    ("lpstrFileTitle", wintypes.LPWSTR),
                    ("nMaxFileTitle", wintypes.DWORD),
                    ("lpstrInitialDir", wintypes.LPCWSTR),
                    ("lpstrTitle", wintypes.LPCWSTR),
                    ("Flags", wintypes.DWORD),
                    ("nFileOffset", wintypes.WORD),
                    ("nFileExtension", wintypes.WORD),
                    ("lpstrDefExt", wintypes.LPCWSTR),
                    ("lCustData", wintypes.LPARAM),
                    ("lpfnHook", ctypes.c_void_p),
                    ("lpTemplateName", wintypes.LPCWSTR),
                    ("pvReserved", ctypes.c_void_p),
                    ("dwReserved", wintypes.DWORD),
                    ("FlagsEx", wintypes.DWORD),
                ]

            file_buffer = ctypes.create_unicode_buffer(MAX_PATH)

            ofn = OPENFILENAME()
            ofn.lStructSize = ctypes.sizeof(OPENFILENAME)
            ofn.hwndOwner = None
            # 支援 .exe 和 .lnk 捷徑檔案
            ofn.lpstrFilter = "執行檔與捷徑 (*.exe;*.lnk)\0*.exe;*.lnk\0執行檔 (*.exe)\0*.exe\0捷徑 (*.lnk)\0*.lnk\0所有檔案 (*.*)\0*.*\0\0"
            ofn.lpstrFile = ctypes.cast(file_buffer, wintypes.LPWSTR)
            ofn.nMaxFile = MAX_PATH
            ofn.lpstrTitle = "選擇 FF14 Launcher（可選擇捷徑或執行檔）"
            ofn.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR

            if ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
                selected_path = file_buffer.value

                # 如果選擇的是 .lnk 捷徑，解析出實際路徑
                if selected_path.lower().endswith('.lnk'):
                    resolved_path = resolve_shortcut(selected_path)
                    print(f"捷徑解析: {selected_path} -> {resolved_path}")

                    # 確保解析後的路徑有效
                    if resolved_path and os.path.exists(resolved_path):
                        return resolved_path
                    else:
                        # 解析失敗，返回空字串並提示錯誤
                        print(f"無法解析捷徑目標: {selected_path}")
                        return ""

                return selected_path
            return ""
        except Exception as e:
            print(f"開啟檔案對話框失敗: {e}")
            return ""

    def start_automation(self, secret_key: str, email: str = "", password: str = ""):
        """開始自動化流程"""
        if not secret_key:
            return {"success": False, "message": "請輸入 Secret Key"}

        def status_callback(msg):
            if window:
                window.evaluate_js(f'updateStatus("{msg}")')

        def run():
            success, msg = automation.run_automation(secret_key, email, password, status_callback)
            if window:
                window.evaluate_js(f'automationComplete({str(success).lower()}, "{msg}")')

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        return {"success": True, "message": "自動化流程已啟動"}

    def stop_automation(self):
        """停止自動化流程"""
        automation.stop()
        return {"success": True, "message": "已停止"}

    def minimize_to_tray(self):
        """隱藏到系統匣"""
        if window and tray_manager:
            tray_manager.save_placement()
            window.hide()
        return {"success": True}

    def check_update(self):
        """檢查更新"""
        return check_for_updates()

    def get_version(self):
        """取得當前版本"""
        return VERSION
    def reset_window_position(self):
        """重置視窗位置到視窗所在螢幕的中央"""
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, "FF14 Login Manager")

            # 取得視窗所在螢幕的工作區域
            MONITOR_DEFAULTTONEAREST = 2
            hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) if hwnd else None

            if hmon:
                class _MONITORINFO(ctypes.Structure):
                    _fields_ = [('cbSize', wintypes.DWORD),
                                ('rcMonitor', wintypes.RECT),
                                ('rcWork', wintypes.RECT),
                                ('dwFlags', wintypes.DWORD)]
                mi = _MONITORINFO()
                mi.cbSize = ctypes.sizeof(_MONITORINFO)
                user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
                mon_x, mon_y = mi.rcWork.left, mi.rcWork.top
                screen_width = mi.rcWork.right - mi.rcWork.left
                screen_height = mi.rcWork.bottom - mi.rcWork.top
            else:
                mon_x, mon_y = 0, 0
                screen_width = user32.GetSystemMetrics(0)
                screen_height = user32.GetSystemMetrics(1)

            # 計算螢幕中央位置
            window_width = 680
            window_height = 580
            x = mon_x + (screen_width - window_width) // 2
            y = mon_y + (screen_height - window_height) // 2

            # 移動視窗
            window.move(x, y)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def save_hotkey_config(self, hotkey_config: dict):
        """儲存快捷鍵設定"""
        try:
            config.set("enable_reset_hotkey", hotkey_config.get("enable_reset_hotkey", True))
            config.set("reset_hotkey", hotkey_config.get("reset_hotkey", "F5"))
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def detect_launcher(self):
        """偵測 Launcher 是否已啟動"""
        try:
            root = uia.GetRootElement()
            launcher = automation.find_window_by_keywords(root, ["FINAL FANTASY XIV 繁體中文版"])
            return {"running": launcher is not None}
        except Exception:
            return {"running": False}

    def detect_game(self):
        """偵測遊戲是否已開啟（透過進程名稱）"""
        try:
            import ctypes
            from ctypes import wintypes

            # 使用 Windows API 列舉進程
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

            # 取得所有進程 ID
            EnumProcesses = ctypes.windll.psapi.EnumProcesses
            GetProcessImageFileNameW = ctypes.windll.psapi.GetProcessImageFileNameW
            OpenProcess = ctypes.windll.kernel32.OpenProcess
            CloseHandle = ctypes.windll.kernel32.CloseHandle

            # 列舉進程
            process_ids = (ctypes.c_ulong * 2048)()
            bytes_returned = ctypes.c_ulong()
            EnumProcesses(process_ids, ctypes.sizeof(process_ids), ctypes.byref(bytes_returned))

            num_processes = bytes_returned.value // ctypes.sizeof(ctypes.c_ulong)

            game_executables = ["ffxiv_dx11.exe", "ffxiv.exe"]

            for i in range(num_processes):
                pid = process_ids[i]
                if pid == 0:
                    continue

                handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                if handle:
                    try:
                        image_name = ctypes.create_unicode_buffer(512)
                        if GetProcessImageFileNameW(handle, image_name, 512):
                            exe_name = image_name.value.split("\\")[-1].lower()
                            if exe_name in game_executables:
                                return {"running": True}
                    finally:
                        CloseHandle(handle)

            return {"running": False}
        except Exception:
            return {"running": False}


def save_window_position():
    """儲存視窗位置"""
    global window
    if window:
        try:
            x, y = window.x, window.y
            config.set("window_x", x)
            config.set("window_y", y)
        except:
            pass


def on_closing():
    """按 X 時依設定決定關閉或隱藏到系統匣"""
    if _force_quit:
        save_window_position()
        if tray_manager:
            tray_manager.stop()
        return True

    close_action = config.get("close_action", "minimize_to_tray")
    if close_action == "quit":
        save_window_position()
        if tray_manager:
            tray_manager.stop()
        return True

    # 最小化到系統匣
    save_window_position()
    tray_manager.save_placement()
    window.hide()
    return False


def check_for_updates():
    """檢查是否有新版本"""
    try:
        cache_bust = f"?t={int(time.time())}"
        req = urllib.request.Request(
            VERSION_CHECK_URL + cache_bust,
            headers={'User-Agent': 'FF14LoginManager', 'Cache-Control': 'no-cache'}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            remote_version = data.get("version", "0.0.0")
            download_url = data.get("download_url", "")
            changelog = data.get("changelog", "")

            # 比較版本（用 tuple 比較避免 "1.0.10" < "1.0.9" 的問題）
            def ver_tuple(v):
                return tuple(int(x) for x in v.split('.'))

            if ver_tuple(remote_version) > ver_tuple(VERSION):
                return {
                    "has_update": True,
                    "current_version": VERSION,
                    "new_version": remote_version,
                    "download_url": download_url,
                    "changelog": changelog
                }
    except Exception as e:
        return {"has_update": False, "current_version": VERSION, "error": str(e)}

    return {"has_update": False, "current_version": VERSION}


def set_window_icon(icon_path):
    """使用 Windows API 設定視窗圖示"""
    try:
        import ctypes
        from ctypes import wintypes

        # 載入圖示
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x00000010
        LR_DEFAULTSIZE = 0x00000040

        hicon = ctypes.windll.user32.LoadImageW(
            None,
            icon_path,
            IMAGE_ICON,
            0, 0,
            LR_LOADFROMFILE | LR_DEFAULTSIZE
        )

        if hicon:
            # 找到視窗
            hwnd = ctypes.windll.user32.FindWindowW(None, "FF14 Login Manager")
            if hwnd:
                # 設定圖示
                ICON_SMALL = 0
                ICON_BIG = 1
                WM_SETICON = 0x0080

                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
    except Exception as e:
        print(f"設定圖示失敗: {e}")


def main():
    global window, tray_manager

    # 單一實例檢查：若已有實例在執行，喚醒它並退出
    if not _try_acquire_single_instance():
        return

    # 取得腳本所在目錄
    script_dir = os.path.dirname(os.path.abspath(__file__))
    web_dir = os.path.join(script_dir, "web")
    index_path = os.path.join(web_dir, "index.html")
    icon_path = os.path.join(web_dir, "favicon.ico")

    # 建立 API 實例
    api = Api()

    # 建立系統匣管理器
    tray_manager = SystemTrayManager(icon_path)

    # 讀取已儲存的視窗位置
    saved_x = config.get("window_x")
    saved_y = config.get("window_y")

    # 建立視窗（如果有儲存位置則使用）
    window_params = {
        "title": "FF14 Login Manager",
        "url": index_path,
        "width": 680,
        "height": 580,
        "resizable": False,
        "js_api": api
    }

    # 如果有儲存的位置，驗證座標仍在可見螢幕範圍內才套用
    if saved_x is not None and saved_y is not None:
        SM_XVIRTUALSCREEN = 76
        SM_YVIRTUALSCREEN = 77
        SM_CXVIRTUALSCREEN = 78
        SM_CYVIRTUALSCREEN = 79
        vx = ctypes.windll.user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = ctypes.windll.user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = ctypes.windll.user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vh = ctypes.windll.user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        # 只要視窗左上角在虛擬螢幕範圍內（留 50px 容差）就套用
        if vx - 50 <= saved_x <= vx + vw - 50 and vy - 50 <= saved_y <= vy + vh - 50:
            window_params["x"] = saved_x
            window_params["y"] = saved_y

    window = webview.create_window(**window_params)
    tray_manager.set_window(window)

    # 註冊關閉事件
    window.events.closing += on_closing

    # 最小化時：依設定決定縮到工作列或隱藏到系統匣
    def on_minimized():
        if config.get("minimize_action") == "tray":
            tray_manager.save_placement()
            window.hide()

    window.events.minimized += on_minimized

    # 視窗顯示後設定圖示並啟動系統匣
    def on_shown():
        time.sleep(0.1)  # 等待視窗完全顯示
        set_window_icon(icon_path)
        tray_manager.start()
        _start_show_window_listener()

    window.events.shown += on_shown

    # 啟動應用程式
    webview.start()


def show_error_dialog(log_path: str, error_text: str):
    """顯示可複製的錯誤視窗"""
    import tkinter as tk

    root = tk.Tk()
    root.title("FF14 Login Manager 啟動失敗")
    root.geometry("500x220")
    root.resizable(False, False)
    root.attributes('-topmost', True)

    # 說明文字
    tk.Label(root, text="程式發生錯誤，請將以下檔案傳給開發者：", pady=10).pack()

    # 可複製的路徑框
    path_frame = tk.Frame(root)
    path_frame.pack(fill='x', padx=20, pady=5)
    path_entry = tk.Entry(path_frame, width=60)
    path_entry.insert(0, str(log_path))
    path_entry.config(state='readonly')
    path_entry.pack(side='left', fill='x', expand=True)

    def copy_path():
        root.clipboard_clear()
        root.clipboard_append(str(log_path))
        copy_btn.config(text="已複製!")

    copy_btn = tk.Button(path_frame, text="複製", command=copy_path, width=8)
    copy_btn.pack(side='right', padx=(5, 0))

    # 錯誤訊息
    tk.Label(root, text=f"錯誤訊息：{error_text}", wraplength=450, pady=10, fg='red').pack()

    # 按鈕區
    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=10)

    def open_folder():
        # 開啟資料夾並選取檔案
        subprocess.run(['explorer', '/select,', str(log_path)])

    tk.Button(btn_frame, text="開啟資料夾", command=open_folder, width=12).pack(side='left', padx=5)
    tk.Button(btn_frame, text="確定", command=root.destroy, width=10).pack(side='left', padx=5)

    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        error_msg = traceback.format_exc()

        # 只在錯誤時寫入 log
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG_FILE, 'w', encoding='utf-8') as f:
                f.write(f"FF14 Login Manager v{VERSION} 錯誤報告\n")
                f.write(f"時間: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Python: {sys.version}\n")
                f.write(f"{'='*50}\n\n")
                f.write(error_msg)
        except:
            pass

        # 彈出錯誤視窗（可複製路徑）
        show_error_dialog(LOG_FILE, str(e))
        sys.exit(1)
