{ config, lib, pkgs, ... }:

let
  cfg = config.hardware.asus-dialpad-driver;

  configFileDir = pkgs.writeTextFile {
    name = "asus-dialpad-driver-config";
    text = lib.generators.toINI {} cfg.config;
    destination = "/dialpad_dev";
  };

  package =
    cfg.package.override
      (lib.optionalAttrs cfg.wayland { waylandSupport = true; });
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
      description = "Whether to start the Asus DialPad Driver daemon as a systemd user service.";
    };

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.asus-dialpad-driver.override { waylandSupport = cfg.wayland; };
      description = "The package to use for the Asus DialPad Driver.";
    };

    layout = lib.mkOption {
      type = lib.types.str;
      default = "proartp16";
      description = "The layout identifier for the DialPad driver (e.g. proart16). This value is required.";
    };

    wayland = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Enable this option to run under Wayland. Disable it for X11.";
    };

    config = lib.mkOption {
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
      default = {};
      description = "Configuration options for the Asus DialPad Driver.";
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ package ];

    # Ensure the writable directories exists
    systemd.tmpfiles.rules = [
      "d /var/log/asus-dialpad-driver 0755 root root -"
    ];

    # Enable i2c
    hardware.i2c.enable = true;

    # Add groups for dialpad
    users.groups = {
      uinput = { };
      input = { };
      i2c = { };
    };

    # Add root to the necessary groups
    users.users.root.extraGroups = [ "i2c" "input" "uinput" ];

    # Add the udev rule to set permissions for uinput and i2c-dev
    services.udev.extraRules = /* udev */ ''
      # Set uinput device permissions
      KERNEL=="uinput", GROUP="uinput", MODE="0660"
      # Set i2c-dev permissions
      SUBSYSTEM=="i2c-dev", GROUP="i2c", MODE="0660"
    '';

    # Load specific kernel modules
    boot.kernelModules = [ "uinput" "i2c-dev" ];

    systemd.services.asus-dialpad-driver = lib.mkIf cfg.daemon.enable {
      description = "Asus DialPad Driver";
      wantedBy = [ "default.target" ];
      startLimitBurst=20;
      startLimitIntervalSec=300;
      serviceConfig = {
        Type = "simple";
        ExecStart = "${package}/share/asus-dialpad-driver/dialpad.py ${cfg.layout} ${configFileDir}/";
        StandardOutput = null;
        StandardError = null;
        Restart = "on-failure";
        RestartSec = 1;
        TimeoutSec = 5;
        WorkingDirectory = "${package}/share/asus-dialpad-driver";
        Environment = [
          "XDG_SESSION_TYPE=${if cfg.wayland then "wayland" else "x11"}"
          "XDG_RUNTIME_DIR=/run/user/1000/"
          "DISPLAY=:0"
          "LOG=WARNING"
        ] ++ lib.optional cfg.wayland "WAYLAND_DISPLAY=wayland-0";
      };
    };

  };
}
