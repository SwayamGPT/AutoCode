# PyInstaller spec for the AutoCode desktop app. Build with: scripts/build_desktop.sh
import sys
from pathlib import Path

root = Path(SPECPATH).parent
static = root / "agent_platform" / "static"
if not (static / "index.html").exists():
    raise SystemExit("UI not built. Run: npm --prefix ui ci && npm --prefix ui run build")

a = Analysis(
    [str(root / "app.py")],
    pathex=[str(root)],
    datas=[(str(static), "agent_platform/static")],
    # psycopg loads its compiled backend dynamically; PyInstaller cannot see that import.
    hiddenimports=["psycopg_binary"],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AutoCode",
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="AutoCode")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="AutoCode.app",
        bundle_identifier="io.autocode.desktop",
        info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": "1.0.0"},
    )
