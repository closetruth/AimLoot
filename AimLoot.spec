# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

# 只收集实际用到的 PySide6 子模块（QtCore/QtGui/QtWidgets/QtMultimedia）。
# 不要用 collect_submodules('PySide6')——它会连带 WebEngine/QML/3D 等
# 全部打包（WebEngineCore 一个 dll 就 195 MB）。
pyside_modules = [
    'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'PySide6.QtMultimedia',
]

hiddenimports = [
    'pyvda', 'win32api', 'win32con', 'winreg',
    'pygame', 'games', 'games.pet_arena', 'games.pixel_tactics',
] + pyside_modules
hiddenimports += collect_submodules('pynput')
try:
    hiddenimports += collect_submodules('pygame')
except Exception:
    pass

# 开发态备用：把 games / assets 目录一并打进包。
datas = [('games', 'games'), ('assets', 'assets')]

# 只带简体中文翻译，去掉 designer_ / qtwebengine_ 等用不到的 .qm（约省 50+ MB）。
import os as _os
import PySide6 as _pyside6
_qt_translations = _os.path.join(_os.path.dirname(_pyside6.__file__), 'translations')
if _os.path.isdir(_qt_translations):
    for _name in sorted(_os.listdir(_qt_translations)):
        if _name.startswith('designer_') or _name.startswith('qtwebengine_'):
            continue
        if _name.endswith('.qm') and ('zh' in _name or 'en' in _name):
            datas.append((_os.path.join(_qt_translations, _name), 'PySide6/translations'))

# 排除整个 Qt 生态里用不到的重型模块。
excludes = [
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngine',
    'PySide6.QtWebChannel', 'PySide6.QtQml', 'PySide6.QtQuick',
    'PySide6.QtQuickWidgets', 'PySide6.Qt3DCore', 'PySide6.Qt3DRender',
    'PySide6.Qt3DInput', 'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras',
    'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs',
    'PySide6.QtLocation', 'PySide6.QtPositioning', 'PySide6.QtMultimediaWidgets',
    'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'PySide6.QtSvgWidgets',
    'PySide6.QtSerialPort', 'PySide6.QtSerialBus', 'PySide6.QtBluetooth',
    'PySide6.QtNfc', 'PySide6.QtSensors', 'PySide6.QtWebSockets',
    'PySide6.QtRemoteObjects', 'PySide6.QtDesigner', 'PySide6.QtUiTools',
    'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtNetworkAuth',
]


a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AimLoot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AimLoot',
)
