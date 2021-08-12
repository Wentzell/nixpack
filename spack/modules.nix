packs:
{ name ? "modules"
, modtype ? "lmod" /* lmod or tcl */
, config ? {}
, pkgs /* packages to include */
}:
packs.spackBuilder {
  args = [./modules.py];
  inherit name modtype;
  config = builtins.toJSON (packs.lib.recursiveUpdate {
    core_compilers = ["gcc@0" (packs.lib.specName packs.pkgs.compiler.spec)];
  } config);
  pkgs = builtins.toJSON (map (p: p.spec // { prefix = p.out; }) pkgs);
  passAsFile = ["config" "pkgs"];
}
