from setuptools import setup, Extension

truenas_os_ext = Extension(
    'truenas_os',
    sources=[
        'src/open_by_mnt.c',
        'src/truenas_os.c',
        'src/fhandle.c',
        'src/mount.c',
        'src/iter_mount.c',
        'src/statx.c',
        'src/openat2.c',
        'src/move_mount.c',
        'src/mount_setattr.c'
    ],
    include_dirs=['src']
)

setup(
    ext_modules=[truenas_os_ext]
)
