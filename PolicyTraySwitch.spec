# -*- mode: python ; coding: utf-8 -*-

import sys
import os
sys.path.insert(0, os.getcwd())

# Корректные импорты из внутренних модулей PyInstaller для Pylance
if '_' not in globals():
    from PyInstaller.building.build_main import Analysis
    from PyInstaller.building.api import PYZ, EXE, COLLECT

from build import APP_NAME, APP_ICON_DIR, APP_ICON_ICO, VERSION_FILE, ONE_FILE, APP_SCRIPT, IP_TRAY_ENDPOINTS

a = Analysis(
    [APP_SCRIPT],
    pathex=[],
    binaries=[],
    datas=[(APP_ICON_DIR, APP_ICON_DIR), (IP_TRAY_ENDPOINTS,".")], # Автоматически берем всю папку с иконками и конфигурационный файл
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyInstaller'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries if ONE_FILE else [],
    a.zipfiles if ONE_FILE else [],
    a.datas if ONE_FILE else [],
    exclude_binaries=not ONE_FILE,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=f"{APP_ICON_DIR}/{APP_ICON_ICO}",
    version=VERSION_FILE,
)

if not ONE_FILE:
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name=APP_NAME,
    )
