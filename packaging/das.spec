# -*- mode: python ; coding: utf-8 -*-
"""One PyInstaller spec for Windows, macOS and Linux.

Run it from the repository root:

    pyinstaller packaging/das.spec --noconfirm

The important part is the assets mapping. config.py derives ASSETS_DIR from
__file__, so inside a frozen build the fonts must land at
    <bundle>/digital_assets_studio/assets
or every cover, banner and slide render dies on a missing font. `run.py
--selftest` checks exactly that, and CI runs it on the built binary.
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH).resolve().parent
APP_NAME = "Digital Assets Studio"

# flet_desktop carries the Flutter binary that *is* the window. It is imported
# lazily at runtime, so PyInstaller never sees it and collects none of it - and a
# build without it does not fail: it starts, cannot find the view, relaunches
# itself through sys.executable, and does that forever without ever drawing a
# window. Collect it explicitly, and keep --selftest honest about it.
FLET_VIEW = collect_data_files("flet_desktop", include_py_files=False)

sys.path.insert(0, str(ROOT))
from digital_assets_studio.config import APP_VERSION  # noqa: E402

icon = None
if sys.platform == "win32":
    icon = str(ROOT / "packaging" / "icon.ico")
elif sys.platform == "darwin":
    icns = ROOT / "packaging" / "icon.icns"
    icon = str(icns) if icns.exists() else None

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "digital_assets_studio" / "assets"),
            "digital_assets_studio/assets")] + FLET_VIEW,
    hiddenimports=[
        "digital_assets_studio",
        "flet_desktop",
        "keyring.backends.Windows",
        "keyring.backends.macOS",
        "keyring.backends.SecretService",
        "keyring.backends.chainer",
        "keyring.backends.fail",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest", "numpy.testing"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME if sys.platform == "darwin" else "DigitalAssetsStudio",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DigitalAssetsStudio",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=icon,
        bundle_identifier="com.aidiginext.digitalassetsstudio",
        version=APP_VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            # the KDP step drives a browser and the suite talks to publishing APIs
            "NSAppleEventsUsageDescription":
                "Digital Assets Studio opens your browser to sign in to publishing services.",
        },
    )
