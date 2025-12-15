// DOM Elements
const launcherPathInput = document.getElementById('launcherPath');
const browseBtn = document.getElementById('browseBtn');
const accountSelect = document.getElementById('accountSelect');
const addAccountBtn = document.getElementById('addAccountBtn');
const deleteAccountBtn = document.getElementById('deleteAccountBtn');
const accountNameInput = document.getElementById('accountName');
const accountEmailInput = document.getElementById('accountEmail');
const accountPasswordInput = document.getElementById('accountPassword');
const togglePasswordBtn = document.getElementById('togglePasswordBtn');
const secretKeyInput = document.getElementById('secretKey');
const toggleKeyBtn = document.getElementById('toggleKeyBtn');
const otpDisplay = document.getElementById('otpDisplay');
const progressBar = document.getElementById('progressBar');
const timeRemaining = document.getElementById('timeRemaining');
const copyBtn = document.getElementById('copyBtn');
const saveAccountBtn = document.getElementById('saveAccountBtn');
const autoCheckUpdate = document.getElementById('autoCheckUpdate');
const autoLaunch = document.getElementById('autoLaunch');
const autoInputCredentials = document.getElementById('autoInputCredentials');
const credentialsInfoBtn = document.getElementById('credentialsInfoBtn');
const autoInputOtp = document.getElementById('autoInputOtp');
const autoPressEnter = document.getElementById('autoPressEnter');
const autoClickPlay = document.getElementById('autoClickPlay');
const startBtn = document.getElementById('startBtn');
const infoBtn = document.getElementById('infoBtn');
const themeOptions = document.querySelectorAll('.theme-option');
const brightnessSlider = document.getElementById('brightnessSlider');
const statusText = document.getElementById('statusText');
const qrScanBtn = document.getElementById('qrScanBtn');

// Tab 和快捷鍵元素
const tabAutomation = document.getElementById('tabAutomation');
const tabOther = document.getElementById('tabOther');
const tabContentAutomation = document.getElementById('tabContentAutomation');
const tabContentOther = document.getElementById('tabContentOther');
const enableResetHotkey = document.getElementById('enableResetHotkey');
const hotkeyInput = document.getElementById('hotkeyInput');
// Dialogs
const qrDialog = document.getElementById('qrDialog');
const qrDialogClose = document.getElementById('qrDialogClose');
const qrUploadArea = document.getElementById('qrUploadArea');
const qrFileInput = document.getElementById('qrFileInput');
const qrPreview = document.getElementById('qrPreview');
const qrPreviewImg = document.getElementById('qrPreviewImg');
const qrResult = document.getElementById('qrResult');
const qrSecretKeyDisplay = document.getElementById('qrSecretKey');
const qrError = document.getElementById('qrError');
const qrErrorMsg = document.getElementById('qrErrorMsg');
const qrApplyBtn = document.getElementById('qrApplyBtn');
const qrResetBtn = document.getElementById('qrResetBtn');

const infoDialog = document.getElementById('infoDialog');
const infoDialogClose = document.getElementById('infoDialogClose');
const configPathDisplay = document.getElementById('configPathDisplay');

const credentialsDialog = document.getElementById('credentialsDialog');
const credentialsDialogClose = document.getElementById('credentialsDialogClose');

const settingsBtn = document.getElementById('settingsBtn');
const settingsDialog = document.getElementById('settingsDialog');
const settingsDialogClose = document.getElementById('settingsDialogClose');

const otpTutorialBtn = document.getElementById('otpTutorialBtn');
const otpTutorialDialog = document.getElementById('otpTutorialDialog');
const otpTutorialDialogClose = document.getElementById('otpTutorialDialogClose');

const deleteConfirmDialog = document.getElementById('deleteConfirmDialog');
const deleteConfirmDialogClose = document.getElementById('deleteConfirmDialogClose');
const deleteConfirmBtn = document.getElementById('deleteConfirmBtn');
const deleteCancelBtn = document.getElementById('deleteCancelBtn');

const gameRunningDialog = document.getElementById('gameRunningDialog');
const gameRunningDialogClose = document.getElementById('gameRunningDialogClose');
const gameRunningConfirmBtn = document.getElementById('gameRunningConfirmBtn');
const gameRunningCancelBtn = document.getElementById('gameRunningCancelBtn');

// State
let isRunning = false;
let otpUpdateInterval = null;
let detectInterval = null;
let accounts = [];
let selectedAccountIndex = -1;
let currentTheme = 'tsuyukusa';
let brightness = 50;
let extractedSecretKey = '';

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
    await loadConfig();
    startOtpUpdate();
    startDetection();
    initTheme();
    initDialogs();
    initQrScanner();
    checkForUpdates();
});

// ========== 偵測 Launcher 和遊戲狀態 ==========

function startDetection() {
    updateDetectionStatus();
    detectInterval = setInterval(updateDetectionStatus, 3000);
}

async function updateDetectionStatus() {
    try {
        if (!window.pywebview || !window.pywebview.api) return;

        const launcherIndicator = document.getElementById('launcherIndicator');
        const gameIndicator = document.getElementById('gameIndicator');

        // 偵測 Launcher
        const launcherResult = await window.pywebview.api.detect_launcher();
        if (launcherResult.running) {
            launcherIndicator.classList.remove('bg-gray-500');
            launcherIndicator.classList.add('bg-green-500');
        } else {
            launcherIndicator.classList.remove('bg-green-500');
            launcherIndicator.classList.add('bg-gray-500');
        }

        // 偵測遊戲
        const gameResult = await window.pywebview.api.detect_game();
        if (gameResult.running) {
            gameIndicator.classList.remove('bg-gray-500');
            gameIndicator.classList.add('bg-green-500');
        } else {
            gameIndicator.classList.remove('bg-green-500');
            gameIndicator.classList.add('bg-gray-500');
        }
    } catch (error) {
        console.error('偵測狀態失敗:', error);
    }
}

// ========== 更新檢查 ==========

async function checkForUpdates(showNoUpdateMsg = false) {
    try {
        // 等待 API 就緒
        while (!window.pywebview || !window.pywebview.api) {
            await new Promise(resolve => setTimeout(resolve, 100));
        }

        // 顯示版本號
        const version = await window.pywebview.api.get_version();
        document.getElementById('versionText').textContent = 'v' + version;

        // 如果不是手動檢查，且沒有啟用自動檢查更新，則跳過
        if (!showNoUpdateMsg && !autoCheckUpdate.checked) {
            return;
        }

        const result = await window.pywebview.api.check_update();

        if (result.has_update) {
            // 顯示「有可用的更新」連結
            document.getElementById('updateAvailable').classList.remove('hidden');

            // 顯示更新對話框
            document.getElementById('currentVersion').textContent = result.current_version;
            document.getElementById('newVersion').textContent = result.new_version;
            document.getElementById('downloadLink').href = result.download_url;

            // 顯示更新內容
            const changelogEl = document.getElementById('changelog');
            if (result.changelog) {
                changelogEl.textContent = result.changelog;
                changelogEl.classList.remove('hidden');
            } else {
                changelogEl.classList.add('hidden');
            }

            document.getElementById('updateDialog').showModal();
        } else if (showNoUpdateMsg) {
            // 手動檢查時，如果沒有更新，顯示提示
            updateStatus('目前已是最新版本');
        }
    } catch (error) {
        console.error('檢查更新失敗:', error);
        if (showNoUpdateMsg) {
            updateStatus('檢查更新失敗');
        }
    }
}

// 點擊版本號手動檢查更新
document.getElementById('versionText').addEventListener('click', () => {
    checkForUpdates(true);
});

// ========== 主題切換 ==========

function initTheme() {
    // 套用已儲存的主題和明暗度
    applyTheme(currentTheme);
    applyBrightness(brightness);
    brightnessSlider.value = brightness;

    // 主題選項點擊
    themeOptions.forEach(option => {
        option.addEventListener('click', () => {
            const theme = option.dataset.theme;
            if (theme) {
                applyTheme(theme);
                currentTheme = theme;
                saveConfig();
            }
        });
    });

    // 給我驚喜按鈕 - 完全隨機顏色
    const surpriseBtn = document.getElementById('surpriseThemeBtn');
    if (surpriseBtn) {
        surpriseBtn.addEventListener('click', () => {
            // 各部分獨立隨機
            const randomColors = {
                primaryHue: Math.floor(Math.random() * 360),
                bgHue: Math.floor(Math.random() * 360),
                bgSat: Math.floor(Math.random() * 30) + 25,
                brightness: Math.floor(Math.random() * 101)
            };

            applyRandomColor(randomColors);
            applyBrightness(randomColors.brightness);
            brightnessSlider.value = randomColors.brightness;

            currentTheme = `random-${randomColors.primaryHue}-${randomColors.bgHue}-${randomColors.bgSat}`;
            brightness = randomColors.brightness;
            saveConfig();
        });
    }

    // 明暗滑桿變更
    brightnessSlider.addEventListener('input', (e) => {
        brightness = parseInt(e.target.value);
        applyBrightness(brightness);
    });

    brightnessSlider.addEventListener('change', () => {
        saveConfig();
    });
}

function applyTheme(theme) {
    // 檢查是否為隨機主題
    if (theme && theme.startsWith('random-')) {
        const parts = theme.split('-');
        const randomColors = {
            primaryHue: parseInt(parts[1]),
            bgHue: parseInt(parts[2]),
            bgSat: parseInt(parts[3])
        };
        applyRandomColor(randomColors);
        return;
    }

    document.body.setAttribute('data-theme', theme);

    // 清除隨機顏色的 inline styles
    const root = document.documentElement;
    root.style.removeProperty('--primary');
    root.style.removeProperty('--primary-dark');
    root.style.removeProperty('--bg-body-h');
    root.style.removeProperty('--bg-body-s');
    root.style.removeProperty('--bg-card-h');
    root.style.removeProperty('--bg-card-s');
    root.style.removeProperty('--bg-input-h');
    root.style.removeProperty('--bg-input-s');
    root.style.removeProperty('--text-primary');
    root.style.removeProperty('--text-secondary');

    // 更新選中狀態
    themeOptions.forEach(option => {
        if (option.dataset.theme === theme) {
            option.classList.add('active');
        } else {
            option.classList.remove('active');
        }
    });
}

function applyRandomColor(colors) {
    const root = document.documentElement;
    const { primaryHue, bgHue, bgSat } = colors;

    // 移除預設主題屬性，避免衝突
    document.body.removeAttribute('data-theme');

    // 主色調 - 獨立色相
    root.style.setProperty('--primary', `hsl(${primaryHue}, 65%, 55%)`);
    root.style.setProperty('--primary-dark', `hsl(${primaryHue}, 60%, 45%)`);

    // 背景色 - 獨立色相
    root.style.setProperty('--bg-body-h', bgHue);
    root.style.setProperty('--bg-body-s', `${bgSat}%`);
    root.style.setProperty('--bg-card-h', bgHue);
    root.style.setProperty('--bg-card-s', `${bgSat - 5}%`);
    root.style.setProperty('--bg-input-h', bgHue);
    root.style.setProperty('--bg-input-s', `${bgSat - 8}%`);

    // 文字顏色 - 中性，不跟隨任何色相
    root.style.setProperty('--text-primary', '#E0E6ED');
    root.style.setProperty('--text-secondary', '#8899A6');

    // 清除主題選中狀態
    themeOptions.forEach(option => {
        option.classList.remove('active');
    });
}

function applyBrightness(value) {
    // 0 = 最暗, 50 = 原始, 100 = 最亮
    // 調整背景色的明度
    const lightness = value / 100; // 0 ~ 1
    document.documentElement.style.setProperty('--brightness-adjust', lightness);

    // 當亮度 > 50 時切換為深色文字
    if (value > 35) {
        document.body.classList.add('light-mode');
    } else {
        document.body.classList.remove('light-mode');
    }
}

// ========== Dialog 初始化 ==========

function initDialogs() {
    // Settings Dialog
    settingsBtn.addEventListener('click', () => settingsDialog.showModal());
    settingsDialogClose.addEventListener('click', () => settingsDialog.close());
    settingsDialog.addEventListener('click', (e) => {
        if (e.target === settingsDialog) settingsDialog.close();
    });

    // Info Dialog
    infoDialogClose.addEventListener('click', () => infoDialog.close());
    infoDialog.addEventListener('click', (e) => {
        if (e.target === infoDialog) infoDialog.close();
    });

    // Credentials Dialog
    credentialsDialogClose.addEventListener('click', () => credentialsDialog.close());
    credentialsDialog.addEventListener('click', (e) => {
        if (e.target === credentialsDialog) credentialsDialog.close();
    });

    // OTP Tutorial Dialog
    otpTutorialBtn.addEventListener('click', () => otpTutorialDialog.showModal());
    otpTutorialDialogClose.addEventListener('click', () => otpTutorialDialog.close());
    otpTutorialDialog.addEventListener('click', (e) => {
        if (e.target === otpTutorialDialog) otpTutorialDialog.close();
    });

    // Delete Confirm Dialog
    deleteConfirmDialogClose.addEventListener('click', () => deleteConfirmDialog.close());
    deleteCancelBtn.addEventListener('click', () => deleteConfirmDialog.close());
    deleteConfirmDialog.addEventListener('click', (e) => {
        if (e.target === deleteConfirmDialog) deleteConfirmDialog.close();
    });
    deleteConfirmBtn.addEventListener('click', () => {
        if (selectedAccountIndex >= 0 && selectedAccountIndex < accounts.length) {
            accounts.splice(selectedAccountIndex, 1);
            if (accounts.length === 0) {
                selectedAccountIndex = -1;
            } else if (selectedAccountIndex >= accounts.length) {
                selectedAccountIndex = accounts.length - 1;
            }
            refreshAccountList();
            loadSelectedAccount();
            saveConfig();
        }
        deleteConfirmDialog.close();
    });

    // Game Running Dialog
    gameRunningDialogClose.addEventListener('click', () => gameRunningDialog.close());
    gameRunningCancelBtn.addEventListener('click', () => gameRunningDialog.close());
    gameRunningDialog.addEventListener('click', (e) => {
        if (e.target === gameRunningDialog) gameRunningDialog.close();
    });
    gameRunningConfirmBtn.addEventListener('click', () => {
        gameRunningDialog.close();
        doStartAutomation();
    });

    // QR Dialog
    qrDialogClose.addEventListener('click', () => {
        qrDialog.close();
        resetQrDialog();
    });
    qrDialog.addEventListener('click', (e) => {
        if (e.target === qrDialog) {
            qrDialog.close();
            resetQrDialog();
        }
    });

    // Update Dialog
    const updateDialog = document.getElementById('updateDialog');
    const updateDialogClose = document.getElementById('updateDialogClose');
    const updateLaterBtn = document.getElementById('updateLaterBtn');

    updateDialogClose.addEventListener('click', () => updateDialog.close());
    updateLaterBtn.addEventListener('click', () => updateDialog.close());
    updateDialog.addEventListener('click', (e) => {
        if (e.target === updateDialog) updateDialog.close();
    });
}

// ========== QR Code 掃描 ==========

function initQrScanner() {
    // 開啟 QR Dialog
    qrScanBtn.addEventListener('click', () => {
        resetQrDialog();
        qrDialog.showModal();
    });

    // 點擊上傳區域
    qrUploadArea.addEventListener('click', () => qrFileInput.click());

    // 檔案選擇
    qrFileInput.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) handleQrFile(file);
    });

    // 拖曳上傳
    qrUploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        qrUploadArea.classList.add('border-white/60');
    });

    qrUploadArea.addEventListener('dragleave', () => {
        qrUploadArea.classList.remove('border-white/60');
    });

    qrUploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        qrUploadArea.classList.remove('border-white/60');
        const file = e.dataTransfer.files[0];
        if (file && file.type.startsWith('image/')) {
            handleQrFile(file);
        }
    });

    // 套用按鈕
    qrApplyBtn.addEventListener('click', () => {
        if (extractedSecretKey) {
            secretKeyInput.value = extractedSecretKey;
            qrDialog.close();
            resetQrDialog();
        }
    });

    // 重新掃描按鈕
    qrResetBtn.addEventListener('click', resetQrDialog);
}

function resetQrDialog() {
    qrFileInput.value = '';
    qrUploadArea.classList.remove('hidden');
    qrPreview.classList.add('hidden');
    qrResult.classList.add('hidden');
    qrError.classList.add('hidden');
    qrApplyBtn.classList.add('hidden');
    qrResetBtn.classList.add('hidden');
    extractedSecretKey = '';
}

function handleQrFile(file) {
    const reader = new FileReader();
    reader.onload = (e) => {
        qrPreviewImg.src = e.target.result;
        qrPreview.classList.remove('hidden');
        qrUploadArea.classList.add('hidden');
        scanQrCode(e.target.result);
    };
    reader.readAsDataURL(file);
}

function scanQrCode(imageData) {
    const img = new Image();
    img.onload = () => {
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        canvas.width = img.width;
        canvas.height = img.height;
        ctx.drawImage(img, 0, 0);

        const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
        const code = jsQR(imgData.data, imgData.width, imgData.height);

        if (code) {
            parseOtpUrl(code.data);
        } else {
            showQrError('無法讀取 QR Code，請確認圖片清晰可見');
        }
    };
    img.src = imageData;
}

function parseOtpUrl(url) {
    try {
        // Google Authenticator 匯出格式
        if (url.startsWith('otpauth-migration://')) {
            const accounts = parseGoogleAuthMigration(url);
            if (!accounts || accounts.length === 0) {
                throw new Error('無法解析 Google Authenticator 資料');
            }
            showQrSuccess(accounts[0].secret);
            return;
        }

        // 標準 OTP 格式
        if (!url.startsWith('otpauth://')) {
            throw new Error('不是有效的 OTP QR Code');
        }

        const urlObj = new URL(url);
        const params = new URLSearchParams(urlObj.search);
        const secret = params.get('secret');

        if (!secret) {
            throw new Error('未找到 Secret Key');
        }

        showQrSuccess(secret);
    } catch (error) {
        showQrError(error.message || '解析失敗');
    }
}

// Base64 解碼 (支援 URL-safe)
function base64Decode(str) {
    str = str.replace(/-/g, '+').replace(/_/g, '/');
    const pad = str.length % 4;
    if (pad) str += '='.repeat(4 - pad);
    const binary = atob(str);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
}

// Base32 編碼
function base32Encode(bytes) {
    const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
    let bits = 0, value = 0, output = '';
    for (let i = 0; i < bytes.length; i++) {
        value = (value << 8) | bytes[i];
        bits += 8;
        while (bits >= 5) {
            output += alphabet[(value >>> (bits - 5)) & 31];
            bits -= 5;
        }
    }
    if (bits > 0) output += alphabet[(value << (5 - bits)) & 31];
    return output;
}

// 解析 Google Authenticator Migration Payload
function parseGoogleAuthMigration(url) {
    try {
        const urlObj = new URL(url);
        const params = new URLSearchParams(urlObj.search);
        const encodedData = params.get('data');
        if (!encodedData) throw new Error('未找到資料');

        const decoded = base64Decode(encodedData);
        const accounts = [];
        let i = 0;

        while (i < decoded.length) {
            if (i + 1 >= decoded.length) break;
            const fieldTag = decoded[i++];

            if (fieldTag === 0x0A) {
                const length = decoded[i++];
                const accountData = decoded.slice(i, i + length);
                const account = parseOtpParameters(accountData);
                if (account) accounts.push(account);
                i += length;
            } else {
                const wireType = fieldTag & 0x07;
                if (wireType === 0) {
                    while (i < decoded.length && decoded[i] & 0x80) i++;
                    i++;
                } else if (wireType === 2) {
                    if (i < decoded.length) i += 1 + decoded[i];
                } else {
                    i++;
                }
            }
        }
        return accounts;
    } catch (error) {
        return null;
    }
}

function parseOtpParameters(data) {
    const account = { secret: '', name: '', issuer: '' };
    let i = 0;

    while (i < data.length) {
        if (i + 1 >= data.length) break;
        const fieldTag = data[i++];
        const fieldNum = fieldTag >> 3;
        const wireType = fieldTag & 0x07;

        if (wireType === 2) {
            if (i >= data.length) break;
            const length = data[i++];
            if (i + length > data.length) break;
            const value = data.slice(i, i + length);

            if (fieldNum === 1) account.secret = base32Encode(value);
            else if (fieldNum === 2) account.name = new TextDecoder().decode(value);
            else if (fieldNum === 3) account.issuer = new TextDecoder().decode(value);

            i += length;
        } else if (wireType === 0) {
            while (i < data.length && data[i] & 0x80) i++;
            i++;
        } else {
            i++;
        }
    }
    return account.secret ? account : null;
}

function showQrSuccess(secret) {
    extractedSecretKey = secret;
    qrSecretKeyDisplay.textContent = secret;
    qrResult.classList.remove('hidden');
    qrError.classList.add('hidden');
    qrApplyBtn.classList.remove('hidden');
    qrResetBtn.classList.remove('hidden');
}

function showQrError(message) {
    qrErrorMsg.textContent = message;
    qrError.classList.remove('hidden');
    qrResult.classList.add('hidden');
    qrApplyBtn.classList.add('hidden');
    qrResetBtn.classList.remove('hidden');
}

// ========== 設定載入/儲存 ==========

async function loadConfig() {
    try {
        // 等待 pywebview API 就緒
        while (!window.pywebview || !window.pywebview.api) {
            await new Promise(resolve => setTimeout(resolve, 100));
        }

        const config = await window.pywebview.api.get_config();

        launcherPathInput.value = config.launcher_path || '';
        accounts = config.accounts || [];
        selectedAccountIndex = config.selected_account ?? -1;
        currentTheme = config.theme || 'tsuyukusa';
        brightness = config.brightness ?? 50;

        autoCheckUpdate.checked = config.auto_check_update !== false;
        autoLaunch.checked = config.auto_launch !== false;
        autoInputCredentials.checked = config.auto_input_credentials === true;
        autoInputOtp.checked = config.auto_input_otp !== false;
        autoPressEnter.checked = config.auto_press_enter !== false;
        autoClickPlay.checked = config.auto_click_play !== false;

        refreshAccountList();
        loadSelectedAccount();
    } catch (error) {
        console.error('Failed to load config:', error);
    }
}

async function saveConfig() {
    try {
        await window.pywebview.api.save_config({
            launcher_path: launcherPathInput.value,
            accounts: accounts,
            selected_account: selectedAccountIndex,
            theme: currentTheme,
            brightness: brightness,
            auto_check_update: autoCheckUpdate.checked,
            auto_launch: autoLaunch.checked,
            auto_input_credentials: autoInputCredentials.checked,
            auto_input_otp: autoInputOtp.checked,
            auto_press_enter: autoPressEnter.checked,
            auto_click_play: autoClickPlay.checked
        });
    } catch (error) {
        console.error('Failed to save config:', error);
    }
}

// ========== 帳號管理 ==========

function refreshAccountList() {
    accountSelect.innerHTML = '';

    if (accounts.length === 0) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = '-- 請新增帳號 --';
        accountSelect.appendChild(option);
    } else {
        accounts.forEach((account, index) => {
            const option = document.createElement('option');
            option.value = index;
            option.textContent = account.name || `帳號 ${index + 1}`;
            accountSelect.appendChild(option);
        });
    }

    if (selectedAccountIndex >= 0 && selectedAccountIndex < accounts.length) {
        accountSelect.value = selectedAccountIndex;
    }
}

function loadSelectedAccount() {
    if (selectedAccountIndex >= 0 && selectedAccountIndex < accounts.length) {
        const account = accounts[selectedAccountIndex];
        accountNameInput.value = account.name || '';
        accountEmailInput.value = account.email || '';
        accountPasswordInput.value = account.password || '';
        secretKeyInput.value = account.secret_key || '';
    } else {
        accountNameInput.value = '';
        accountEmailInput.value = '';
        accountPasswordInput.value = '';
        secretKeyInput.value = '';
    }
}


// ========== OTP 更新 ==========

function startOtpUpdate() {
    updateOtp();
    otpUpdateInterval = setInterval(updateOtp, 1000);
}

async function updateOtp() {
    const secretKey = secretKeyInput.value;

    if (!secretKey) {
        otpDisplay.textContent = '------';
        timeRemaining.textContent = '請輸入 Secret Key';
        progressBar.style.width = '0%';
        return;
    }

    try {
        if (!window.pywebview || !window.pywebview.api) return;
        const data = await window.pywebview.api.get_otp(secretKey);

        if (data.error) {
            otpDisplay.textContent = '------';
            timeRemaining.textContent = data.error;
            progressBar.style.width = '0%';
            return;
        }

        // Format OTP with space in middle
        const otp = data.otp;
        otpDisplay.textContent = otp.slice(0, 3) + ' ' + otp.slice(3);

        // Update progress bar
        const remaining = data.remaining;
        const percentage = (remaining / 30) * 100;
        progressBar.style.width = percentage + '%';

        // Update color based on remaining time
        if (remaining <= 5) {
            progressBar.classList.add('danger');
        } else {
            progressBar.classList.remove('danger');
        }

        timeRemaining.textContent = '剩餘 ' + remaining + ' 秒';
    } catch (error) {
        console.error('Failed to update OTP:', error);
    }
}

// ========== 事件監聽 ==========

// 瀏覽啟動器路徑
browseBtn.addEventListener('click', async () => {
    try {
        const path = await window.pywebview.api.browse_launcher_path();
        if (path) {
            launcherPathInput.value = path;
            await saveConfig();
        }
    } catch (error) {
        console.error('Failed to browse:', error);
    }
});

// 帳號選擇變更
accountSelect.addEventListener('change', () => {
    const value = accountSelect.value;
    if (value !== '') {
        selectedAccountIndex = parseInt(value);
        loadSelectedAccount();
        saveConfig();
    }
});

// 新增帳號
addAccountBtn.addEventListener('click', () => {
    const newAccount = {
        name: '新帳號 ' + (accounts.length + 1),
        email: '',
        password: '',
        secret_key: ''
    };
    accounts.push(newAccount);
    selectedAccountIndex = accounts.length - 1;
    refreshAccountList();
    loadSelectedAccount();
    saveConfig();
    accountNameInput.focus();
});

// 刪除帳號
deleteAccountBtn.addEventListener('click', () => {
    if (selectedAccountIndex >= 0 && selectedAccountIndex < accounts.length) {
        deleteConfirmDialog.showModal();
    }
});

// 帳號欄位變更 - 不再自動儲存，改為手動點儲存按鈕

// 儲存帳號按鈕
saveAccountBtn.addEventListener('click', () => {
    if (selectedAccountIndex >= 0 && selectedAccountIndex < accounts.length) {
        accounts[selectedAccountIndex].name = accountNameInput.value;
        accounts[selectedAccountIndex].email = accountEmailInput.value;
        accounts[selectedAccountIndex].password = accountPasswordInput.value;
        accounts[selectedAccountIndex].secret_key = secretKeyInput.value;
        refreshAccountList();
        saveConfig();

        // 顯示儲存成功
        const originalText = saveAccountBtn.textContent;
        saveAccountBtn.textContent = '已儲存!';
        setTimeout(() => {
            saveAccountBtn.textContent = originalText;
        }, 2000);
    } else {
        alert('請先新增或選擇帳號');
    }
});

// 切換密碼顯示
togglePasswordBtn.addEventListener('click', () => {
    if (accountPasswordInput.type === 'password') {
        accountPasswordInput.type = 'text';
        togglePasswordBtn.textContent = '隱藏';
    } else {
        accountPasswordInput.type = 'password';
        togglePasswordBtn.textContent = '顯示';
    }
});

// 切換 Secret Key 顯示
toggleKeyBtn.addEventListener('click', () => {
    if (secretKeyInput.type === 'password') {
        secretKeyInput.type = 'text';
        toggleKeyBtn.textContent = '隱藏';
    } else {
        secretKeyInput.type = 'password';
        toggleKeyBtn.textContent = '顯示';
    }
});

// 自動化選項變更
autoCheckUpdate.addEventListener('change', saveConfig);
autoLaunch.addEventListener('change', saveConfig);
autoInputCredentials.addEventListener('change', saveConfig);
autoInputOtp.addEventListener('change', saveConfig);
autoPressEnter.addEventListener('change', saveConfig);
autoClickPlay.addEventListener('change', saveConfig);

// 帳號密碼提示按鈕
credentialsInfoBtn.addEventListener('click', () => {
    credentialsDialog.showModal();
});

// 資訊按鈕 - 顯示資料儲存位置
infoBtn.addEventListener('click', async () => {
    const configPath = await window.pywebview.api.get_config_path();
    configPathDisplay.textContent = configPath;
    infoDialog.showModal();
});

// 開啟設定檔資料夾
document.getElementById('openConfigFolderBtn').addEventListener('click', async () => {
    await window.pywebview.api.open_config_folder();
});

// 複製 OTP
copyBtn.addEventListener('click', async () => {
    const otp = otpDisplay.textContent.replace(' ', '');
    if (otp && otp !== '------') {
        try {
            await navigator.clipboard.writeText(otp);
            copyBtn.textContent = '已複製!';

            setTimeout(() => {
                copyBtn.textContent = '複製 OTP';
            }, 2000);
        } catch (error) {
            console.error('Failed to copy:', error);
        }
    }
});

// 啟動自動化
startBtn.addEventListener('click', async () => {
    if (isRunning) {
        try {
            await window.pywebview.api.stop_automation();
            setRunningState(false);
            updateStatus('已停止');
        } catch (error) {
            console.error('Failed to stop:', error);
        }
        return;
    }

    // 驗證
    if (!launcherPathInput.value && autoLaunch.checked) {
        updateStatus('請選擇啟動器路徑');
        return;
    }

    if (!secretKeyInput.value) {
        updateStatus('請輸入 Secret Key');
        return;
    }

    // 檢查遊戲是否正在執行
    try {
        const gameResult = await window.pywebview.api.detect_game();
        if (gameResult.running) {
            gameRunningDialog.showModal();
            return;
        }
    } catch (error) {
        console.error('Failed to detect game:', error);
    }

    // 啟動
    doStartAutomation();
});

async function doStartAutomation() {
    try {
        setRunningState(true);
        const email = accountEmailInput.value || '';
        const password = accountPasswordInput.value || '';
        const result = await window.pywebview.api.start_automation(secretKeyInput.value, email, password);

        if (!result.success) {
            updateStatus(result.message);
            setRunningState(false);
        }
    } catch (error) {
        console.error('Failed to start:', error);
        updateStatus('啟動自動化失敗');
        setRunningState(false);
    }
}

// ========== 狀態管理 ==========

function setRunningState(running) {
    isRunning = running;

    if (running) {
        startBtn.textContent = '停止';
        startBtn.classList.add('stopping');
    } else {
        startBtn.textContent = '啟動遊戲';
        startBtn.classList.remove('stopping');
    }
}

function updateStatus(message) {
    if (statusText) {
        statusText.textContent = message;
    }
    console.log('Status:', message);
}

function resetToReady() {
    setRunningState(false);
    updateStatus('就緒');
}

// ========== Python 回呼函數 (全域函數供 pywebview 呼叫) ==========

// updateStatus 已在上方定義

function automationComplete(success, message) {
    updateStatus(success ? '完成' : '失敗: ' + message);
    // 5秒後恢復原狀
    setTimeout(() => {
        resetToReady();
    }, 5000);
}

// ========== Tab 切換 ==========

tabAutomation.addEventListener('click', () => {
    tabAutomation.classList.add('active', 'border-current');
    tabAutomation.classList.remove('border-transparent');
    tabOther.classList.remove('active', 'border-current');
    tabOther.classList.add('border-transparent');
    tabContentAutomation.classList.remove('hidden');
    tabContentOther.classList.add('hidden');
});

tabOther.addEventListener('click', () => {
    tabOther.classList.add('active', 'border-current');
    tabOther.classList.remove('border-transparent');
    tabAutomation.classList.remove('active', 'border-current');
    tabAutomation.classList.add('border-transparent');
    tabContentOther.classList.remove('hidden');
    tabContentAutomation.classList.add('hidden');
});

// ========== 重置視窗位置快捷鍵 ==========

let currentHotkey = 'F5';
let isRecordingHotkey = false;

// 載入快捷鍵設定
async function loadHotkeyConfig() {
    try {
        while (!window.pywebview || !window.pywebview.api) {
            await new Promise(resolve => setTimeout(resolve, 100));
        }
        const config = await window.pywebview.api.get_config();
        if (config.reset_hotkey) {
            currentHotkey = config.reset_hotkey;
            hotkeyInput.textContent = currentHotkey;
        }
        if (config.enable_reset_hotkey !== undefined) {
            enableResetHotkey.checked = config.enable_reset_hotkey;
        }
    } catch (e) {
        console.error('載入快捷鍵設定失敗:', e);
    }
}

// 儲存快捷鍵設定
async function saveHotkeyConfig() {
    try {
        await window.pywebview.api.save_hotkey_config({
            enable_reset_hotkey: enableResetHotkey.checked,
            reset_hotkey: currentHotkey
        });
    } catch (e) {
        console.error('儲存快捷鍵設定失敗:', e);
    }
}

// 快捷鍵錄製
hotkeyInput.addEventListener('click', () => {
    isRecordingHotkey = true;
    hotkeyInput.textContent = '按下按鍵...';
    hotkeyInput.classList.add('border-yellow-400');
});

// 監聽按鍵
document.addEventListener('keydown', async (e) => {
    // 錄製快捷鍵模式
    if (isRecordingHotkey) {
        e.preventDefault();
        currentHotkey = e.key;
        hotkeyInput.textContent = currentHotkey;
        hotkeyInput.classList.remove('border-yellow-400');
        isRecordingHotkey = false;
        await saveHotkeyConfig();
        return;
    }

    // 觸發重置視窗位置
    if (enableResetHotkey.checked && e.key === currentHotkey) {
        e.preventDefault();
        try {
            await window.pywebview.api.reset_window_position();
        } catch (err) {
            console.error('重置視窗位置失敗:', err);
        }
    }
});

// 啟用/停用快捷鍵變更時儲存
enableResetHotkey.addEventListener('change', saveHotkeyConfig);

// 頁面載入時載入快捷鍵設定
loadHotkeyConfig();
