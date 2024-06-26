import spack.pkg.builtin.lmod as builtin

class Lmod(builtin.Lmod):
    patch("no-sys-tcl.patch")

    def configure_args(self):
        args = super().configure_args()
        args.append('--with-availExtensions=no')
        args.append('--with-cachedLoads=yes')
        return args
