#!/bin/env python3
from typing import TYPE_CHECKING, Union, Optional, Type, List, Any
if not TYPE_CHECKING:
    # hack for __future__.annotatinons (python<3.7)
    Path = Any
    Inode = Any

import os
import sys
import stat
import errno
import fnmatch

def pathstr(s: bytes) -> str:
    return s.decode('ISO-8859-1')

srcPaths = os.environb[b'pkgs'].split()
dstPath = os.environb[b'out']
forceSrcs = [srcPaths.index(p) if p else None for p in os.environb[b'forcePkgs'].split(b' ')]

def getOpt(opt: bytes):
    v = os.environb[opt]
    if v == b'':
        return lambda x: None
    if v == b'1':
        return lambda x: True
    l = [ fnmatch._compile_pattern(x) for x in v.split(b' ') ]
    def check(x: bytes):
        for i, m in enumerate(l):
            if m(x):
                return i
        return None
    return check

# path handling options
opts = {o: getOpt(o) for o in 
        # in order of precedece:
        [ b'exclude' # paths not to link
        , b'shbang' # paths to translate #!
        , b'wrap' # paths to wrap executables
        , b'copy' # paths to copy
        , b'force' # paths to process only from corresponding forcePkgs
        ] }

maxSrcLen = max(len(p) for p in srcPaths)

class Path:
    """
    Keep track of a rooted path, including relative path, open parent dir_fd,
    open state, and other operations.
    """
    def __init__(self, dir: Union[Path, bytes], ent: Union[os.DirEntry,bytes]=b''):
        self.dir = dir # parent or root
        self.ent = ent # relative
        if isinstance(dir, Path):
            path = dir.path
            self.relpath: bytes = os.path.join(dir.relpath, self.name) if ent else dir.relpath
        else:
            path = dir
            self.relpath = self.name
        self.path: bytes = os.path.join(path, self.name) if ent else path # full path
        self.fd: Optional[int] = None

    def __str__(self) -> str:
        return pathstr(self.path)

    @property
    def root(self) -> bytes:
        "Root path.  os.path.join(self.root, self.relpath) == self.path"
        if isinstance(self.dir, Path):
            return self.dir.root
        return self.dir

    @property
    def dirfd(self) -> Optional[int]:
        "Parent open dir fd.  file(self.dirfd, self.name) == self.path"
        if isinstance(self.dir, Path):
            return self.dir.fd
        return None

    @property
    def name(self) -> bytes:
        "Name relative to dirfd"
        if isinstance(self.ent, os.DirEntry):
            return self.ent.name
        return self.ent

    def sub(self, ent: Union[os.DirEntry,bytes]):
        "Create a child path"
        return Path(self, ent)

    def _dirop(self, fun, *args, **kwargs):
        if self.dirfd is not None:
            return fun(self.name, *args, dir_fd=self.dirfd, **kwargs)
        else:
            return fun(self.path, *args, **kwargs)

    def optsrc(self, opt: bytes) -> Optional[int]:
        "Check whether this path matches the given option and return the index"
        return opts[opt](self.relpath)

    def opt(self, opt: bytes) -> bool:
        "Check whether this path matches the given option"
        return self.optsrc(opt) is not None

    def _dostat(self):
        if self.fd is not None:
            return os.fstat(self.fd)
        try:
            if isinstance(self.ent, os.DirEntry):
                return self.ent.stat(follow_symlinks=False)
            else:
                return self._dirop(os.lstat)
        except OSError as e:
            if e.errno == errno.ENOENT:
                return None
            raise

    def stat(self):
        "really lstat"
        try:
            return self._stat
        except AttributeError:
            self._stat = self._dostat()
            return self._stat

    def isdir(self):
        if isinstance(self.ent, os.DirEntry):
            return self.ent.is_dir(follow_symlinks=False)
        else:
            return stat.S_ISDIR(self.stat().st_mode)

    def islnk(self):
        if isinstance(self.ent, os.DirEntry):
            return self.ent.is_symlink()
        else:
            return stat.S_ISLNK(self.stat().st_mode)

    def isexe(self):
        return self.stat().st_mode & 0o111

    def readlink(self):
        return self._dirop(os.readlink)

    def symlink(self, target: Union[bytes,Path]):
        if isinstance(target, Path):
            target = target.path
        if self.dirfd is not None:
            return os.symlink(target, self.name, dir_fd=self.dirfd)
        else:
            return os.symlink(target, self.path)

    def link(self, old: Union[bytes,Path]):
        args = {}
        if self.dirfd is not None:
            args['dst_dir_fd'] = self.dirfd
            dst = self.name
        else:
            dst = self.path
        if isinstance(old, Path):
            if old.dirfd is not None:
                args['src_dir_fd'] = old.dirfd
                src = old.name
            else:
                src = old.path
        else:
            src = old
        return os.link(src, dst, **args)

    def open(self):
        "set the mode to open for reading. must be used as 'with path.open()'"
        self.mode = os.O_RDONLY|os.O_NOFOLLOW;
        return self

    def opendir(self):
        "set the mode to open directory for reading. must be used as 'with path.opendir()'"
        self.mode = os.O_RDONLY|os.O_NOFOLLOW|os.O_DIRECTORY;
        return self

    def create(self, perm):
        "set the mode to open and create. must be used as 'with path.create()'"
        self.mode = os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW
        if isinstance(perm, Path):
            perm = perm.stat().st_mode
        self.perm = perm
        return self

    def mkdir(self):
        "create a directory and set the mode to open for reading. should be used as 'with path.mkdir()'"
        try:
            self._dirop(os.mkdir)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
        self.mode = os.O_RDONLY|os.O_NOFOLLOW|os.O_DIRECTORY|os.O_PATH;
        return self

    def __enter__(self):
        self.fd = self._dirop(os.open, self.mode, getattr(self, 'perm', 0o777))
        self.mode = None
        return self

    def __exit__(self, *args):
        os.close(self.fd)
        self.fd = None

    def read(self, len: int):
        assert self.fd is not None
        return os.read(self.fd, len)

    def write(self, data: bytes):
        assert self.fd is not None
        l = 0
        while l < len(data):
            l += os.write(self.fd, data[l:])

    def readInterp(self) -> Optional[bytes]:
        "extract the interpreter from #! script, if any"
        hb = self.read(maxSrcLen+4)
        if hb[0:2] != b'#!':
            return None
        return hb[2:].lstrip()

    def copyfile(self, src):
        "write the contents of this open file from the open src file"
        z = src.stat().st_size
        while os.sendfile(self.fd, src.fd, None, z) > 0:
            pass

    def _scandir(self):
        if self.fd is not None:
            try:
                return os.scandir(self.fd)
            except TypeError:
                pass
        return os.scandir(self.path)

    def scandir(self):
        "return an iterator of child Paths"
        return map(self.sub, self._scandir())

def newpath(path: bytes) -> bytes:
    "rewrite a path pointing to a src to the dst"
    if not os.path.isabs(path):
        return path
    for sp in srcPaths:
        if path.startswith(sp):
            return os.path.join(dstPath, os.path.relpath(path, sp))

class Conflict(Exception):
    def __init__(self, path: Path, *nodes: Inode):
        self.path = path.relpath
        self.nodes = nodes

    def __str__(self):
        srcs = ', '.join(pathstr(srcPaths[n.src]) for n in self.nodes if n.src is not None)
        return f'Conflicting file {pathstr(self.path)} from {srcs}'

class Inode:
    "An abstract class representing a node of a file tree"
    def __init__(self, node: Inode, src: int, path: Path):
        self.src: Optional[int] = src # index into srcPaths
        if node is not None:
            if self != node:
                raise Conflict(path, self, node)
            if self.src != node.src:
                self.src = None # set?

    @property
    def needed(self):
        "does this node need special processing/copying/translaton?"
        return self.src == None

    def __eq__(self, other) -> bool:
        "is this node compatible with the other"
        return type(self) == type(other)

    def srcpath(self, path: Path) -> Path:
        "translate the given path to the specific src path for this node"
        assert self.src is not None
        return Path(srcPaths[self.src], path.relpath)

    def create(self, dst: Path) -> None:
        "actually copy/link/populate dst path"
        dst.symlink(self.srcpath(dst))

class Symlink(Inode):
    def __init__(self, node: Inode, src: int, path: Path):
        targ = path.readlink()
        self.targ = newpath(targ)
        super().__init__(node, src, path)

    @property
    def needed(self):
        # for recursion -- don't bother creating directories just for symlinks
        return False

    def __eq__(self, other) -> bool:
        return super().__eq__(other) and self.targ == other.targ

    def __repr__(self):
        return f'Symlink({self.src}, {self.targ!r})'

    def create(self, dst: Path):
        dst.symlink(self.targ)

class File(Inode):
    shbang = False
    wrap = False
    copy = False

    def __init__(self, node: Inode, src: int, path: Path):
        super().__init__(node, src, path)
        if path.isexe():
            if path.opt(b'shbang'):
                with path.open():
                    interp = path.readInterp()
                    if interp and any(interp.startswith(p) for p in srcPaths):
                        self.shbang = True
                        return
            if path.opt(b'wrap'):
                self.wrap = True
        if path.opt(b'copy'):
            self.copy = True

    @property
    def needed(self):
        return self.shbang or self.wrap or self.copy

    def __eq__(self, other) -> bool:
        return super().__eq__(other) and False

    def __repr__(self):
        return f'File({self.src}{", needed" if self.needed else ""})'

    def create(self, dst: Path):
        src = self.srcpath(dst)
        if self.shbang:
            with src.open():
                interp = src.readInterp()
                assert interp
                new = newpath(interp)
                with dst.create(src):
                    dst.write(b'#!'+new)
                    dst.copyfile(src)
        elif self.wrap:
            with dst.create(src):
                dst.write(b'#!/bin/sh\nexec -a "$0" '+src.path+b' "$@"\n')
        elif self.copy:
            try:
                dst.link(src)
            except PermissionError:
                with src.open():
                    with dst.create(src):
                        dst.copyfile(src)
        else:
            dst.symlink(src)

class Dir(Inode):
    def __init__(self, node: Inode, src: int, path: Path):
        super().__init__(node, src, path)
        self.dir = node.dir if node else dict()
        with path.opendir():
            for ent in path.scandir():
                n = scan(self.dir.get(ent.name), src, ent)
                if n:
                    self.dir[ent.name] = n

    def __repr__(self):
        return f'Dir({self.src}, {self.dir!r})'

    def create(self, dst: Path):
        if self.needed:
            with dst.mkdir():
                for n, f in self.dir.items():
                    f.create(dst.sub(n))
        else:
            super().create(dst)
    
def scan(node, src: int, path: Path):
    if path.opt(b'exclude'):
        return node
    force = path.optsrc(b'force')
    if force is not None and forceSrcs[force] != src:
        return node
    if path.isdir():
        cls: Type[Inode] = Dir
    elif path.islnk():
        cls = Symlink
    else:
        cls = File
    return cls(node, src, path)

print(f"Creating view {pathstr(dstPath)} from...")
# scan and merge all source paths
top = None
for i, src in enumerate(srcPaths):
    print(f"    {pathstr(src)}")
    top = scan(top, i, Path(src))

# populate the destination with the result
assert top, "No paths found"
top.create(Path(dstPath))
