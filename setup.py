from setuptools import setup, Extension

truenas_os_ext = Extension(
    'truenas_os',
    sources=[
        'src/cext/open_by_mnt.c',
        'src/cext/truenas_os.c',
        'src/cext/fhandle.c',
        'src/cext/mount.c',
        'src/cext/iter_mount.c',
        'src/cext/statx.c',
        'src/cext/openat2.c',
        'src/cext/open_tree.c',
        'src/cext/move_mount.c',
        'src/cext/mount_setattr.c',
        'src/cext/fsmount.c',
        'src/cext/umount2.c',
        'src/cext/renameat2.c',
        'src/cext/fsiter.c',
        'src/cext/acl.c',
        'src/cext/util_enum.c',
        'src/cext/nfs4acl.c',
        'src/cext/posixacl.c',
    ],
    include_dirs=['src/cext']
)

setup(
    ext_modules=[truenas_os_ext],
    packages=['truenas_os', '_truenas_os_scripts', 'truenas_os_pyutils'],
    package_dir={
        'truenas_os': 'stubs',
        '_truenas_os_scripts': 'scripts',
        'truenas_os_pyutils': 'src/truenas_os_pyutils',
    },
    package_data={
        'truenas_os': ['*.pyi', 'py.typed'],
    },
    entry_points={
        'console_scripts': [
            'truenas_getfacl=_truenas_os_scripts._getfacl:main',
            'truenas_setfacl=_truenas_os_scripts._setfacl:main',
        ],
    },
)
