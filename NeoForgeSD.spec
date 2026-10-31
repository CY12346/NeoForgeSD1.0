# -*- mode: python ; coding: utf-8 -*-
"""NeoForgeSD 1.3.0 打包：只打壳，不含后端与模型权重。

    pyinstaller NeoForgeSD.spec

产物 dist/NeoForgeSD.exe 应 < 10 MB。
"""

a = Analysis(
    ['launcher/neoforge_launcher.py'],
    pathex=['.'],                       # 让 core/ 能被分析到
    binaries=[],
    datas=[
        ('kernels', 'kernels'),         # 随包带 fake 内核（管道自测用）
        ('SPEC-kernel.md', '.'),
        ('INSTALL.md', '.'),
        ('CHANGELOG.md', '.'),
    ],
    hiddenimports=['psutil'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'PIL', 'torch'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='NeoForgeSD',
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
)
