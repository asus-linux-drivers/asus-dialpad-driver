{ config, lib, pkgs, ... }:

let
  cfg = config.hardware.asus-dialpad-driver;

  defaultConfigFile =
    pkgs.writeText "asus-dialpad-driver-default-config.ini" /* ini */ ''
      ; vim: filetype=dosini
      ; Asus DialPad configuration
      ${lib.generators.toINI { } cfg.defaultConfig}
    '';

  package =
    cfg.package.override
      (lib.optionalAttrs (lib.elem "wayland" cfg.sessionTypes) { waylandSupport = true; });
in {
  imports = [
    (lib.mkRenamedOptionModule
      [ "services" "asus-dialpad-driver" ]
      [ "hardware" "asus-dialpad-driver" ])
  ];

  options.hardware.asus-dialpad-driver = {
    enable = lib.mkOption {
      default = false;
      type = lib.types.bool;
      description = "Enable the Asus DialPad Driver module (udev rules, i2c, groups).";
    };

    daemon.enable = lib.mkOption {
      default = true;
      type = lib.types.bool;
      description = ''
        Whether to start the Asus DialPad Driver daemon as a systemd user service.
        Note that the user *must* be enrolled in these groups: i2c, input, uinput.
      '';
    };

    package = lib.mkPackageOption pkgs "asus-dialpad-driver" { };

    sessionTypes = lib.mkOption {
      type = lib.types.uniq (lib.types.nonEmptyListOf (lib.types.enum [ "wayland" "x11" ]));
      default = [ "wayland" "x11" ];
      description = ''
        The display server session types to support.
        All listed types will be built into the package.
      '';
    };

    layout = lib.mkOption {
      type = lib.types.str;
      default = "proartp16";
      description = "The layout identifier for the DialPad driver (e.g. proart16). This value is required.";
    };

    defaultConfig = lib.mkOption {
      type = with lib.types;
        let
          valueType = nullOr (oneOf [
            bool
            int
            float
            str
            path
            (attrsOf valueType)
            (listOf valueType)
          ]) // {
            description = "Asus DialPad Driver configuration value";
          };
        in valueType;
      example = {
        main = {
          enabled = false;
          slices_count = 4;
          disable_due_inactivity_time = 0;
          touchpad_disables_dialpad = true;
          activation_time = 1;
          config_supress_app_specifics_shortcuts = 0;
        };
      };
      default = { };
      description = ''
        Default configuration options for the Asus DialPad Driver on first run.
        It’s recommended to use a user-level configuration manager for this file or manually define with `lib.generators.toINI { } { /* your config */ }`.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ package ];

    # Enable i2c
    hardware.i2c.enable = true;

    # Add groups for dialpad
    users.groups = {
      uinput = { };
      input = { };
      i2c = { };
    };

    # Add the udev rule to set permissions for uinput and i2c-dev
    services.udev.extraRules = /* udev */ ''
      # Set uinput device permissions
      KERNEL=="uinput", GROUP="uinput", MODE="0660"
      # Set i2c-dev permissions
      SUBSYSTEM=="i2c-dev", GROUP="i2c", MODE="0660"
    '';

    # Load specific kernel modules
    boot.kernelModules = [ "uinput" "i2c-dev" ];

    systemd.user.services.asus-dialpad-driver = lib.mkIf cfg.daemon.enable {
      description = "Asus DialPad Driver";
      wantedBy = [ "graphical-session.target" ];
      partOf = [ "graphical-session.target" ];
      serviceConfig = {
        Type = "simple";
        ConfigurationDirectory = "asus-dialpad-driver";
        # Create a default config from the Nix config if missing
        ExecStartPre = "${lib.getExe pkgs.dash} -c 'if [ ! -s %E/asus-dialpad-driver/dialpad_dev ]; then ${lib.getBin pkgs.coreutils}/bin/install -m 644 ${defaultConfigFile} %E/asus-dialpad-driver/dialpad_dev; fi'";
        ExecStart = "${package}/share/asus-dialpad-driver/dialpad.py ${cfg.layout} %E/asus-dialpad-driver/";
        Restart = "on-failure";
        RestartSec = 1;
        TimeoutSec = 5;
        WorkingDirectory = "${package}/share/asus-dialpad-driver";
        Environment = [
          "LOG=INFO"
        ];
      };
    };

  };
}
