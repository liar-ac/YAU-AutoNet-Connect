# -*- mode: python ; coding: utf-8 -*-

_EXCLUDES = [
    # numpy (biggest win, pulled in by PIL but not needed for Image/ImageDraw)
    'numpy',
    # unused PIL image format plugins (keep Ico/Png/Bmp for pystray icon)
    'PIL.BlpImagePlugin', 'PIL.BufrStubImagePlugin', 'PIL.CurImagePlugin',
    'PIL.DcxImagePlugin', 'PIL.DdsImagePlugin', 'PIL.EpsImagePlugin',
    'PIL.FitsStubImagePlugin', 'PIL.FliImagePlugin', 'PIL.FpxImagePlugin',
    'PIL.FtexImagePlugin', 'PIL.GbrImagePlugin', 'PIL.GifImagePlugin',
    'PIL.GribStubImagePlugin', 'PIL.Hdf5StubImagePlugin', 'PIL.IcnsImagePlugin',
    'PIL.ImImagePlugin', 'PIL.ImtImagePlugin',
    'PIL.IptcImagePlugin', 'PIL.Jpeg2KImagePlugin', 'PIL.JpegImagePlugin',
    'PIL.JpegPresets', 'PIL.McIdasImagePlugin', 'PIL.MicImagePlugin',
    'PIL.MpegImagePlugin', 'PIL.MpoImagePlugin', 'PIL.MspImagePlugin',
    'PIL.PalmImagePlugin', 'PIL.PcdImagePlugin',
    'PIL.PcxImagePlugin', 'PIL.PdfImagePlugin', 'PIL.PdfParser',
    'PIL.PixarImagePlugin', 'PIL.PpmImagePlugin',
    'PIL.PsdImagePlugin', 'PIL.QoiImagePlugin', 'PIL.SgiImagePlugin',
    'PIL.SpiderImagePlugin', 'PIL.SunImagePlugin', 'PIL.TgaImagePlugin',
    'PIL.TiffImagePlugin', 'PIL.WebPImagePlugin',
    'PIL.WmfImagePlugin', 'PIL.XVThumbImagePlugin', 'PIL.XbmImagePlugin',
    'PIL.XpmImagePlugin',
    # standard library we never touch
    'unittest', 'pydoc', 'doctest',
    'tkinter.ttk', 'tkinter.filedialog',
    'tkinter.colorchooser',
    'tkinter.font',
    'logging.config', 'logging.handlers',
    'distutils', 'setuptools', 'pkg_resources',
    'multiprocessing', 'concurrent',
    # additional unused stdlib (safe — not imported by the app or its deps)
    'sqlite3', 'lib2to3', 'pydoc_data', 'http.server',
    'pdb', 'profile', 'cProfile', 'pstats', 'tabnanny',
    'turtle', 'turtledemo', 'curses', 'ensurepip', 'venv',
]

a = Analysis(
    ['campus_auto_login.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['pystray', 'pystray._win32', 'PIL', 'PIL.Image', 'PIL.ImageDraw', 'tkinter', 'tkinter.scrolledtext', 'tkinter.simpledialog', 'tkinter.messagebox', 'tempfile'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDES,
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='campus_auto_login',
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
