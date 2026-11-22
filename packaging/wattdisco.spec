# PyInstaller build of the app: `uv run --group build pyinstaller packaging/wattdisco.spec`
# macOS: dist/Watt Disco.app (a folder app). Windows: dist/WattDisco.exe (one file).
import sys
import sysconfig
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "packaging"))
from wattdisco import __version__  # noqa: E402
import licenses  # noqa: E402

MAC = sys.platform == "darwin"
WINDOWS = sys.platform == "win32"
# The oldest macOS the app runs on: 12, or what the Python that builds it was made for.
MAC_MIN = max(12, int(float(sysconfig.get_config_var("MACOSX_DEPLOYMENT_TARGET") or 12)))
ICON = str(ROOT / "packaging" / "icon.png")  # PyInstaller turns it into .icns / .ico (needs Pillow)

datas = collect_data_files("wattdisco")  # the pages, fonts and their licences in wattdisco/web
datas += [(str(ROOT / "LICENSE"), "."), (str(ROOT / "THIRD_PARTY_NOTICES.md"), ".")]
# uvicorn picks its event loop, HTTP and WebSocket code at run time.
hiddenimports = collect_submodules("uvicorn") + collect_submodules("wattdisco")

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
# Every bundled package's licence, Python's and the C libraries' (see packaging/licenses.py).
bundled = {name.split(".")[0] for name, *_ in a.pure} | {Path(name).parts[0].split(".")[0] for name, *_ in a.binaries}
for source, folder in licenses.datas(bundled):
    a.datas.append((f"{folder}/{Path(source).name}", source, "DATA"))
pyz = PYZ(a.pure)

if WINDOWS:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="WattDisco",
        icon=ICON,
        console=False,  # a window app; output goes to wattdisco.log in the data folder
        upx=False,
    )
else:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="Watt Disco",
        icon=ICON,
        console=False,
        upx=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="Watt Disco", upx=False)
    if MAC:
        app = BUNDLE(
            coll,
            name="Watt Disco.app",
            icon=ICON,
            bundle_identifier="io.github.hunorkertesz.wattdisco",
            version=__version__,
            info_plist={
                "CFBundleName": "Watt Disco",
                "CFBundleDisplayName": "Watt Disco",
                "CFBundleShortVersionString": __version__,
                "CFBundleVersion": __version__,
                "LSMinimumSystemVersion": f"{MAC_MIN}.0",
                "NSHighResolutionCapable": True,
                # Without these macOS blocks Bluetooth for the app (or kills it on the first scan).
                "NSBluetoothAlwaysUsageDescription":
                    "Watt Disco connects to your smart trainer, power meter and heart rate strap over Bluetooth.",
                "NSBluetoothPeripheralUsageDescription":
                    "Watt Disco connects to your smart trainer, power meter and heart rate strap over Bluetooth.",
            },
        )
