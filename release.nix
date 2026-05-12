let
  flake_lock = builtins.fromJSON (builtins.readFile ./flake.lock);

  pkgs-src = builtins.fetchTarball {
    url = flake_lock.nodes.nixpkgs.locked.url
      or "https://github.com/NixOS/nixpkgs/archive/${flake_lock.nodes.nixpkgs.locked.rev}.tar.gz";
    sha256 = flake_lock.nodes.nixpkgs.locked.narHash;
  };

  pkgs = import pkgs-src {
    overlays = [
      (import ./nix/overlay/default.nix)
      (import ./nix/overlay/development.nix)
    ];
  };
in
{
  inherit (pkgs) asus-dialpad-driver;
  default = pkgs.asus-dialpad-driver;
  shell = pkgs.asus-dialpad-driver-shell;
}
