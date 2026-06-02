import os
import re
import sys
import subprocess
import importlib
import site


APP_NAME = 'PolicyTraySwitch'
APP_NAME_SHORT = 'PTS'
APP_NAME_LONG = 'Policy Tray Switch'
APP_DESCRIPTION = "Policy Tray Switch Application for Keenetic Router"

APP_VERSION = '1.0.0'
APP_PUBLISHER = "IVN-git"

APP_ICON = "icon-app.png"
APP_ICON_ICO = "app_icon.ico"
APP_ICON_DIR = "icons"
APP_ICON_TRAY = "network-outline.png"#"icon_tray.png"
APP_ICON_TRAY_LINK_DOWN = "network-off-outline.png"

CONFIG_FILE = "config.json"
IP_TRAY_ENDPOINTS = "my_ip_tray_endpoints.json"

# =================== НАСТРОЙКИ ЛОГИРОВАНИЯ ===============
LOG_LEVEL_DEF = "INFO"
LOG_FILE = f"{APP_NAME}.log"
LOG_FILE_MAX_BYTES = 1 * 1024 * 1024
LOG_BACKUP_COUNT = 1
LOG_FORMAT = '%(space)s%(asctime)s %(levelname)s %(name)s: %(message)s'
LOG_NOISY_LIBRARES = ['PIL', 'urllib3', 'pystray', 'requests']

# ==================== НАСТРОЙКИ СБОРКИ ====================
ONE_FILE = False 
APP_SCRIPT = f"{APP_NAME}.py"
VERSION_FILE = 'version_info.txt'
APP_EXE_NAME = f"{APP_NAME}.exe"
SPEC_FILE = f"{APP_NAME}.spec"
ISS_FILE = f"{APP_NAME}.iss"


# =======================================================
# =================== СБОРЩИК ПРОЕКТА ===================
# =======================================================


def check_and_install_dependencies():
    """Автоматически проверяет pip, ставит зависимости и чинит пути для MS Store/pywin32"""
    print("--- Шаг 0: Проверка окружения и установка зависимостей ---")
    
    # 1. Проверка pip
    try:
        subprocess.run([sys.executable, "-m", "pip", "--version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[ ! ] Модуль pip не найден. Пытаюсь установить...")
        try:
            subprocess.run([sys.executable, "-m", "ensurepip", "--default-pip"], check=True)
        except subprocess.CalledProcessError:
            print("[ Критическая ошибка ] Не удалось запустить ensurepip.")
            sys.exit(1)

    # 2. Проверка файла зависимостей
    requirements_file = "requirements.txt"
    if not os.path.exists(requirements_file):
        print(f"[ Критическая ошибка ] Файл {requirements_file} не найден!")
        sys.exit(1)
        
    # 3. Установка пакетов
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", requirements_file], check=True)
        print("[ OK ] Все зависимости проверены и установлены!")
    except subprocess.CalledProcessError as e:
        print(f"[ Критическая ошибка ] Не удалось установить библиотеки: {e}")
        sys.exit(1)

    # 4. Исправление путей среды (Магия для Microsoft Store)
    user_site_paths = []
    if hasattr(site, 'getusersitepackages'):
        user_site_paths.append(site.getusersitepackages())
    user_site_paths.append(os.path.join(os.environ.get('APPDATA', ''), 'Python', 'Python313', 'site-packages'))
    
    for site_path in user_site_paths:
        if site_path and site_path not in sys.path:
            sys.path.append(site_path)
            
        # Исправление путей к DLL для pywin32
        pywin32_path = os.path.join(site_path, 'win32')
        pywin32_lib = os.path.join(site_path, 'win32', 'lib')
        pywin32_system32 = os.path.join(site_path, 'pywin32_system32')
        
        if os.path.exists(pywin32_path) and pywin32_path not in sys.path:
            sys.path.append(pywin32_path)
        if os.path.exists(pywin32_lib) and pywin32_lib not in sys.path:
            sys.path.append(pywin32_lib)
            
        if os.path.exists(pywin32_system32):
            os.environ['PATH'] = pywin32_system32 + os.path.pathsep + os.environ.get('PATH', '')
            if hasattr(os, 'add_dll_directory'):
                try:
                    os.add_dll_directory(pywin32_system32)
                except Exception:
                    pass

    importlib.invalidate_caches()

def update_pyinstaller_version_file():
    """Синхронизирует данные из Python прямо в ваш файл версий info.txt"""
    if not os.path.exists(VERSION_FILE):
        print(f"Ошибка: Файл версий '{VERSION_FILE}' не найден!")
        return False

    with open(VERSION_FILE, "r", encoding="utf-8") as f:
        text = f.read()

    version_parts = APP_VERSION.split(".")
    while len(version_parts) < 4:
        version_parts.append("0")
    ver_tuple = ", ".join(version_parts)

    text = re.sub(r"filevers=\(\d+,\s*\d+,\s*\d+,\s*\d+\)", f"filevers=({ver_tuple})", text)
    text = re.sub(r"prodvers=\(\d+,\s*\d+,\s*\d+,\s*\d+\)", f"prodvers=({ver_tuple})", text)
    text = re.sub(r"StringStruct\(u?'FileVersion',\s*u?'.*?'\)", f"StringStruct(u'FileVersion', u'{APP_VERSION}')", text)
    text = re.sub(r"StringStruct\(u?'ProductVersion',\s*u?'.*?'\)", f"StringStruct(u'ProductVersion', u'{APP_VERSION}')", text)
    text = re.sub(r"StringStruct\(u?'ProductName',\s*u?'.*?'\)", f"StringStruct(u'ProductName', u'{APP_NAME_LONG}')", text)
    text = re.sub(r"StringStruct\(u?'CompanyName',\s*u?'.*?'\)", f"StringStruct(u'InternalName', u'{APP_NAME}')", text)
    text = re.sub(r"StringStruct\(u?'CompanyName',\s*u?'.*?'\)", f"StringStruct(u'FileDescription', u'{APP_DESCRIPTION}')", text)
    text = re.sub(r"StringStruct\(u?'CompanyName',\s*u?'.*?'\)", f"StringStruct(u'CompanyName', u'{APP_PUBLISHER}')", text)
    text = re.sub(r"StringStruct\(u?'CompanyName',\s*u?'.*?'\)", f"StringStruct(u'OriginalFilename', u'{APP_EXE_NAME}')", text)

    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(text)

    print(f"[ OK ] Данные успешно записаны in {VERSION_FILE}")
    return True


def sync_iss():
    """Синхронизирует константы в блок #define в .iss файле"""
    if not os.path.exists(ISS_FILE):
        print(f"Ошибка: Файл Inno Setup '{ISS_FILE}' не найден!")
        return False

    block_start_mark = "//--const-start"
    block_end_mark = "//--const-end"

    with open(ISS_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    pattern = rf'({re.escape(block_start_mark)}\r?\n)(.*?)(\r?\n{re.escape(block_end_mark)})'
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        print(f"Ошибка: Маркеры {block_start_mark} не найдены в {ISS_FILE}!")
        return False

    define_names = re.findall(r'#define\s+(\w+)', match.group(2))
    if not define_names:
        print(f"[ INFO ] Блок #define в {ISS_FILE} пуст. Нечего обновлять.")
        return True

    defines = [f'#define {name} "{globals().get(name, "")}"' for name in define_names]
    new_block = match.group(1) + '\n'.join(defines) + match.group(3)
    content = content.replace(match.group(0), new_block)

    with open(ISS_FILE, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"[ OK ] Константы успешно синхронизированы с {ISS_FILE}")
    return True

def find_inno_setup_compiler():
    """Динамически находит путь к компилятору Inno Setup (ISCC.exe)"""
    import winreg
    registry_paths = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 7_is1",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 7_is1",
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1",
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 5_is1",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 5_is1"
    ]
    
    for subkey in registry_paths:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
                try:
                    install_path, _ = winreg.QueryValueEx(key, "Inno Setup: App Path")
                except FileNotFoundError:
                    install_path, _ = winreg.QueryValueEx(key, "InstallLocation")
                
                iscc_path = os.path.join(install_path, "ISCC.exe")
                if os.path.exists(iscc_path):
                    return iscc_path
        except WindowsError:
            continue

    program_files_directories = [
        os.environ.get("ProgramFiles", "C:\\Program Files"),
        os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
    ]
    
    for pf in program_files_directories:
        if not os.path.exists(pf):
            continue
        for folder_name in os.listdir(pf):
            if folder_name.lower().startswith("inno setup"):
                fallback_path = os.path.join(pf, folder_name, "ISCC.exe")
                if os.path.exists(fallback_path):
                    return fallback_path
    return None

def compile_inno_setup():
    """Автоматически компилирует установщик через консольную утилиту Inno Setup"""
    iscc_bin = find_inno_setup_compiler()

    if not iscc_bin:
        print("[ Критическая ошибка ] Inno Setup не найден на этом компьютере!")
        print("Пожалуйста, установите Inno Setup (версии 5, 6 или 7) перед запуском сборщика.")
        sys.exit(1)

    print("\n--- Шаг 4: Запуск компиляции Inno Setup (ISCC.exe) ---")
    print(f"[ INFO ] Используется компилятор: {iscc_bin}")
    try:
        subprocess.run([iscc_bin, ISS_FILE], check=True)
        print("\n[ SUCCESS ] Итоговый инсталлятор успешно собран!")
    except subprocess.CalledProcessError as e:
        print(f"\n[ ERROR ] Ошибка при компиляции Inno Setup: {e}")

# ==============================================================================
# ГЛАВНЫЙ ПРОЦЕСС СБОРКИ
# ==============================================================================
if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    print("=== НАЧАЛО АВТОМАТИЧЕСКОЙ СБОРКИ ПРОЕКТА ===")

    # Шаг 0: Скачивание, проверка зависимостей и фикс путей
    check_and_install_dependencies()

    # Шаг 1: Версии
    print("\n--- Шаг 1: Обновление метаданных PyInstaller ---")
    if not update_pyinstaller_version_file():
        sys.exit(1)

    # Шаги для режима One-Folder (Inno Setup собирает установщик)
    if not ONE_FILE:
        print("\n--- Шаг 2: Синхронизация скрипта Inno Setup ---")
        if not sync_iss():
            sys.exit(1)

    # Шаг 3: Компиляция PyInstaller
    print("\n--- Шаг 3: Запуск компиляции PyInstaller (.spec) ---")
    if not os.path.exists(SPEC_FILE):
        print(f"Критическая ошибка: Файл конфигурации '{SPEC_FILE}' не найден!")
        sys.exit(1)

    import PyInstaller.__main__
    PyInstaller.__main__.run([SPEC_FILE, '--clean', '-y'])
    print("[ OK ] Компиляция PyInstaller успешно завершена.")

    # Шаг 4: Сборка инсталлятора
    if not ONE_FILE:
        compile_inno_setup()
