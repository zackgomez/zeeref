# -*- mode: python ; coding: utf-8 -*-

import os
import tomllib
from os.path import join
import sys


# Read version from pyproject.toml (single source of truth)
with open('pyproject.toml', 'rb') as f:
    _pyproject = tomllib.load(f)
APPNAME = _pyproject['project']['name']
VERSION = _pyproject['project']['version']

block_cipher = None
appname = f'{APPNAME}-{VERSION}'

if sys.platform.startswith('win'):
    icon = 'logo.ico'
else:
    icon = 'logo.icns'  # For OSX; param gets ignored on Linux


a = Analysis(
    [join('zeeref', '__main__.py')],
    pathex=[os.getcwd()],
    binaries=[],
    datas=[
        (join('zeeref', 'documentation'), join('zeeref', 'documentation')),
        (join('zeeref', 'assets', '*.png'), join('zeeref', 'assets'))],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=appname,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None ,
    icon=join('zeeref', 'assets', icon))

if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name=f'{APPNAME}.app',
        icon=join('zeeref', 'assets', icon),
        bundle_identifier='org.zeeref.app',
        version=f'{VERSION}',
        info_plist={
            'CFBundleDocumentTypes': [
                {
                    'CFBundleTypeExtensions': [ 'zref' ],
                    'CFBundleTypeRole': 'Viewer'
                }
            ]
        })
