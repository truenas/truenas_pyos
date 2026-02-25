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
        'src/open_tree.c',
        'src/move_mount.c',
        'src/mount_setattr.c',
        'src/fsmount.c',
        'src/umount2.c',
        'src/renameat2.c',
        'src/fsiter.c',
        'src/acl.c',
        'src/util_enum.c',
        'src/nfs4acl.c',
        'src/posixacl.c',
    ],
    include_dirs=['src']
)

setup(
    ext_modules=[truenas_os_ext],
    packages=['truenas_os'],
    package_dir={
        'truenas_os': 'stubs',
    },
    package_data={
        'truenas_os': ['*.pyi', 'py.typed'],
    }
)
