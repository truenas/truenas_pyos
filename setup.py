from setuptools import setup, Extension

truenas_os_ext = Extension(
    'truenas_os',
    sources=[
        'src/cext/os/open_by_mnt.c',
        'src/cext/os/truenas_os.c',
        'src/cext/os/fhandle.c',
        'src/cext/os/mount.c',
        'src/cext/os/iter_mount.c',
        'src/cext/os/statx.c',
        'src/cext/os/openat2.c',
        'src/cext/os/open_tree.c',
        'src/cext/os/move_mount.c',
        'src/cext/os/mount_setattr.c',
        'src/cext/os/fsmount.c',
        'src/cext/os/umount2.c',
        'src/cext/os/renameat2.c',
        'src/cext/os/fsiter.c',
        'src/cext/os/acl.c',
        'src/cext/os/util_enum.c',
        'src/cext/os/nfs4acl.c',
        'src/cext/os/posixacl.c',
        'src/cext/os/xattr.c',
    ],
    include_dirs=['src/cext/os']
)

truenas_pyfilter_ext = Extension(
    'truenas_pyfilter',
    sources=[
        'src/cext/filter_utils/truenas_pyfilter.c',
        'src/cext/filter_utils/filter_list.c',
        'src/cext/filter_utils/filter_options.c',
    ],
    include_dirs=['src/cext/filter_utils'],
    extra_compile_args=['-O2', '-Wall', '-Wextra', '-Wno-unused-parameter'],
)

setup(
    ext_modules=[truenas_os_ext, truenas_pyfilter_ext],
    packages=[
        'truenas_os',
        'truenas_pyfilter',
        '_truenas_os_scripts',
        'truenas_os_pyutils',
        'truenas_os_pyutils.truenas_shutil',
    ],
    package_dir={
        'truenas_os': 'stubs/truenas_os',
        'truenas_pyfilter': 'stubs/truenas_pyfilter',
        '_truenas_os_scripts': 'scripts',
        'truenas_os_pyutils': 'src/truenas_os_pyutils',
        'truenas_os_pyutils.truenas_shutil': 'src/truenas_os_pyutils/truenas_shutil',
    },
    package_data={
        'truenas_os': ['*.pyi', 'py.typed'],
        'truenas_pyfilter': ['*.pyi', 'py.typed'],
        'truenas_os_pyutils': ['py.typed'],
    },
    entry_points={
        'console_scripts': [
            'truenas_getfacl=_truenas_os_scripts._getfacl:main',
            'truenas_setfacl=_truenas_os_scripts._setfacl:main',
        ],
    },
)
