# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

# 收集 Pillow 的所有内容
datas_pil, binaries_pil, hiddenimports_pil = collect_all('PIL')

# 如果你装了 pillow-heif，一并收集（可选）
try:
    datas_heif, binaries_heif, hiddenimports_heif = collect_all('pillow_heif')
except Exception:
    datas_heif, binaries_heif, hiddenimports_heif = [], [], []


a = Analysis(
    ['transfer.py'],
    pathex=[],
    binaries=binaries_pil + binaries_heif,
    datas=[('web', 'web')] + datas_pil + datas_heif,
    hiddenimports=hiddenimports_pil + hiddenimports_heif,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除 tkinter 之外用不到的大模块，减小体积
        'numpy', 'scipy', 'pandas', 'matplotlib',
        'PyQt5', 'PySide2', 'wx',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='lanshare',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # Windows 下 UPX 有时会误伤 Pillow 的 DLL，关掉更稳
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # 无控制台窗口（Windows GUI）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)