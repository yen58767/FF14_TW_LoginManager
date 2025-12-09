"""
FF14 Login Manager
使用 pywebview 介面，搭配 UI Automation API 操作 FF14 Launcher
"""

VERSION = "1.0.2"
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

# 設定 Windows AppUserModelID，讓程式可以正確釘選到工作列
import ctypes
from ctypes import wintypes
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FF14LoginManager.App")
except Exception:
    pass

import webview
import pyotp

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
        self.config_path = Path.home() / ".ff14_login_config.json"
        self.config = self.load()

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
                            if "email" in account and not account["email"].startswith("dpapi:"):
                                needs_upgrade = True

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

            return True, "自動化完成"

        except Exception as e:
            return False, f"發生錯誤: {str(e)}"
        finally:
            self.running = False


# 全域物件
config = ConfigManager()
automation = LauncherAutomation(config)
window = None


class Api:
    """pywebview API - 提供給 JavaScript 呼叫的方法"""

    def get_config(self):
        """取得設定"""
        return config.get_all()

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

    def browse_launcher_path(self):
        """開啟檔案選擇對話框"""
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)

        file_path = filedialog.askopenfilename(
            title="選擇 FF14 Launcher",
            filetypes=[("執行檔", "*.exe"), ("所有檔案", "*.*")]
        )

        root.destroy()
        return file_path if file_path else ""

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

    def check_update(self):
        """檢查更新"""
        return check_for_updates()

    def get_version(self):
        """取得當前版本"""
        return VERSION

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
    """視窗關閉時儲存位置"""
    save_window_position()


def check_for_updates():
    """檢查是否有新版本"""
    try:
        req = urllib.request.Request(
            VERSION_CHECK_URL,
            headers={'User-Agent': 'FF14LoginManager'}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            remote_version = data.get("version", "0.0.0")
            download_url = data.get("download_url", "")

            # 比較版本
            if remote_version > VERSION:
                return {
                    "has_update": True,
                    "current_version": VERSION,
                    "new_version": remote_version,
                    "download_url": download_url
                }
    except Exception as e:
        print(f"檢查更新失敗: {e}")

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
    global window

    # 取得腳本所在目錄
    script_dir = os.path.dirname(os.path.abspath(__file__))
    web_dir = os.path.join(script_dir, "web")
    index_path = os.path.join(web_dir, "index.html")
    icon_path = os.path.join(web_dir, "favicon.ico")

    # 建立 API 實例
    api = Api()

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

    # 如果有儲存的位置，套用之
    if saved_x is not None and saved_y is not None:
        window_params["x"] = saved_x
        window_params["y"] = saved_y

    window = webview.create_window(**window_params)

    # 註冊關閉事件
    window.events.closing += on_closing

    # 視窗顯示後設定圖示
    def on_shown():
        time.sleep(0.1)  # 等待視窗完全顯示
        set_window_icon(icon_path)

    window.events.shown += on_shown

    # 啟動應用程式
    webview.start()


if __name__ == "__main__":
    main()
