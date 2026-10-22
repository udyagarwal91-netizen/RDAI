# PyInstaller build for RDAI.exe:  pyinstaller rdai.spec
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

datas = [("app.py", "."), ("data/seed", "data/seed")]
binaries = []
# Listed by hand: app.py is run by Streamlit at runtime, so PyInstaller can't see its imports.
hiddenimports = ["rdai", "rdai.agent", "rdai.db", "rdai.io_excel", "rdai.masters", "rdai.physical",
                 "rdai.production", "rdai.seed", "rdai.stock", "rdai.units", "openpyxl"]
for pkg in ("streamlit", "anthropic"):
    d, b, h = collect_all(pkg)
    datas += d; binaries += b; hiddenimports += h
for pkg in ("streamlit", "anthropic", "pandas", "openpyxl"):
    datas += copy_metadata(pkg)

a = Analysis(["launcher.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hiddenimports)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="RDAI", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="RDAI")
