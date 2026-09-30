# Repository Guidelines

## Project Overview

Linux userspace driver for ASUS DialPad touchpads: interprets dial gestures, selects application-specific shortcuts, emits virtual input events, and controls activation through I2C. An optional PySide6 floating overlay displays feedback. Hardware support, X11/Wayland integration, and distribution packaging are part of correctness.

`docs/issue-31-layout-manager-plan.md` records the approved design and implementation evidence for issue #31. JSON layouts, the standalone manager, and safe hot reload are implemented; historical design observations are not current APIs. Read its verification limits before making hardware/platform claims.

## Architecture & Data Flow

- Source is flat: `dialpad.py` is the driver, `dialpad_runtime.py` owns runtime snapshots, `dialpad_layout.py` is the portable model/CLI, and `dialpad_layout_linux.py` provides Linux adaptation/persistence/status. The optional manager/editor and floating overlay are separate applications. There is no `src/` package or dependency-injection framework.
- `dialpad.py` initializes sessions and devices at module scope. **Do not import it from tests or tools**: importing can open hardware/display connections and exit the process. Extract pure helpers when isolation is needed.
- Flow: touchpad libevdev events → geometry/gesture state → ordered application mapping and shortcut selection → uinput key/relative events or shell commands. Activation additionally sends I2C commands. Keyboard and window/keymap listeners maintain modifier and application state.
- One `RuntimeOwner` publishes prepared snapshots at complete no-contact input boundaries. Window/keymap callbacks and file watchers queue updates; a complete output sequence pins one device. Some legacy activation/session state remains global. There is no asyncio event-loop architecture.
- `RuntimeOwner` requires explicit `dispose_device` ownership: retire replaced/stale candidates only after pinned output sequences finish, and release the current device on shutdown. Keep the python-libevdev native-context workaround isolated in `close_virtual_device`; cyclic GC is not timely device teardown.
- `dialpad_dev` is INI configuration. All writers patch the latest file under `.dialpad_dev.lock`; never replace it from a stale in-memory parser. A nonempty `layout` overrides the non-persisted argv default. Configuration and layout changes are watched for safe reload.
- Driver feedback is JSON over a Unix datagram socket at `/tmp/dialpad.sock`. The overlay binds it and polls with a Qt `QTimer`. Do not launch competing overlay/socket listeners unintentionally.
- Per-instance read-only status uses a separate current-user Unix stream socket under `$XDG_RUNTIME_DIR/asus-dialpad-driver/`. Activation requests are not acknowledgments. Recovery lives in `<config_dir>/.layout-state/last-successful.json`; do not confuse saved, requested, applied, and durable revisions.

## Key Directories

- `layouts/`: bundled hardware-coordinate geometry and application shortcut examples; not keyboard-language layouts. User JSON/Python copies live in `<config_dir>/layouts/`; `bundled-layouts.json` identifies protected built-ins independently of permissions.
- `nix/`: package derivation, NixOS module, overlays, development shell, and `check/` VM test.
- `docs/`: design handoffs; distinguish plans from implemented behavior.
- Repository root: runtime scripts, sourced installer stages, requirement groups, and systemd templates. Run installer scripts from this directory because they use relative `source` paths.

## Development Commands

Python run commands require the prepared driver environment, native dependencies, appropriate device permissions, and an active graphical session. Do not run hardware commands merely to inspect the code.

| Purpose | Command |
| --- | --- |
| Python syntax only; does not import the driver | `python3 -m py_compile dialpad*.py layouts/*.py` |
| Bash syntax only | `for f in *.sh; do bash -n "$f" || exit 1; done` |
| Behavioral regressions; optional Qt tests need PySide6 | `QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -v` |
| Nix development shell | `nix develop` (legacy alternative: `nix-shell`) |
| Nix package build | `nix build .#asus-dialpad-driver` |
| Nix checks | `nix flake check` |
| Explicit x86_64 VM check | `nix build .#checks.x86_64-linux.module-setup` |
| Driver from checkout, on supported hardware | `LOG=DEBUG python3 dialpad.py proartp16 /absolute/config/` |
| Optional floating overlay | `python3 dialpad_ui.py` |
| Optional standalone manager | `python3 dialpad_layout_manager.py --config-dir /absolute/config --offline` |
| Installer-created driver logs | `journalctl --user -u "asus_dialpad_driver@$USER.service" -f` |

Use an explicit configuration directory; a trailing `/` is no longer required. Nix commands require Nix and supported Linux tooling; VM checks additionally need virtualization support. No repository-wide formatter or lint command is configured.

Explicit provisioning uses `bash install.sh` as a **normal user**, not `sudo bash install.sh`. It invokes sudo internally, installs packages, changes groups/udev/module configuration, performs network reporting, and can restart services or prompt for reboot. Installation/uninstallation is not a smoke test. Data is preserved by default; explicit `PURGE` is destructive, so back up configuration and custom layouts first. `install_layout_manager.sh` independently installs the editor without device/service provisioning.

## Code Conventions & Common Patterns

- Keep code, comments, and documentation in English. Follow surrounding indentation; legacy Python mixes four- and two-space blocks. Avoid unrelated whole-file reformatting.
- Functions use `snake_case`; configuration names follow `CONFIG_*` and `CONFIG_*_DEFAULT`. Preserve public historical spellings such as `treshold`, `default_treshold`, and `config_supress_app_specifics_shortcuts` unless all consumers are deliberately migrated.
- Portable layouts use catalog event-name strings; the Linux adapter resolves actual `libevdev` objects. `center`, `clockwise`, and `counterclockwise` are reserved regions; `none` is the fallback application. Other profile keys represent named functions, including valid command-only functions.
- `title` can be a display label rather than a function reference. External `value` can be numeric event values or a shell query; compiled `event_values` and `value_query` are distinct. Preserve application, function, and shortcut ordering; modifier-specific alternatives sort stably before unmodified ones. Static loading/editing never executes layout commands.
- Optional backends use availability flags and logged degradation. Preserve intentional fallbacks, but make new failures visible through the existing logger rather than adding silent broad exception handlers.
- Treat `send_key_event`, runtime publication, `window_was_changed`, keymap updates, and gesture-release handling as coupled state transitions. Do not bypass the runtime owner, split press/release across devices, or execute metadata queries under output/publication locks.
- Installer stages are sourced Bash scripts sharing variables such as `INSTALL_DIR_PATH`, `CONFIG_FILE_DIR_PATH`, and `LAYOUT_NAME`, with defaults applied only when unset. Preserve this contract for standalone and orchestrated usage.

## Important Files

- `README.md`: installation, manual startup, configuration, and debugging; check actual code when documented defaults disagree. `CHANGELOG.md` release headings are also parsed by reporting scripts.
- `requirements*.txt`: dependency groups. `install.sh` and `nix/default.nix` explicitly copy runtime files: **adding an importable file to the checkout does not install it**. Update both install paths, and the Nix source fileset for new data files.
- `asus_dialpad_driver*.service`, `install_service.sh`, `install_common.sh`: explicitly escaped user-service templates and shared path/transaction helpers. Installer units use `asus_dialpad_driver@<user>.service`; Nix uses `asus-dialpad-driver.service`.
- `nix/module.nix`: `hardware.asus-dialpad-driver` options, session environment, startup defaults, and configuration directory. Ordinary installation defaults to `/usr/share/asus-dialpad-driver/dialpad_dev`; Nix uses `%E/asus-dialpad-driver/dialpad_dev`. Respect configured path overrides.
- `laptop_touchpad_dialpad_layouts.csv` and `install_layout_auto_suggestion.sh`: hardware-to-layout selection. Update detection data and selectors when changing shipped layout identities.

## Runtime/Tooling Preferences

Use Python **3.10+**, Bash, pip, and optional Nix. The installer creates `<install_dir>/.env` with `virtualenv --system-site-packages`; native distribution libraries remain necessary. Do not introduce a JavaScript toolchain for existing workflows.

`requirements.txt` supplies core dependencies; `requirements.x11.txt`, `requirements.wayland.txt`, `requirements.systemd.txt`, and `requirements.ui.txt` add their respective integrations. Keep PySide6 optional. The Nix package supports the manager through `layoutManagerSupport`; it does not package the floating overlay.

Preserve X11/Wayland capability detection and session variables such as `XDG_SESSION_TYPE`, `DISPLAY`, and `WAYLAND_DISPLAY`. Do not change device permissions, user groups, services, or installed user data without authorization.

## Testing & QA

- `tests/` uses stdlib `unittest` for model, persistence/status, runtime transitions, optional Qt editing, and uninstall preservation. No coverage target, formatter configuration, or GitHub Actions workflow is configured. Do not claim pytest/Ruff/coverage as established tooling.
- `nix/check/module-setup.nix` is the existing automated check: it boots Sway, waits for the user driver service, and checks uinput presence. It does **not** verify physical DialPad I2C behavior, gesture mappings, X11 operation, or the GUI.
- Syntax checks are useful but are not runtime proof. Exercise changed driver behavior on supported hardware under the relevant display server; visually verify overlay/GUI changes on the actual application.
- Use temporary configuration directories and isolated pure-function tests where possible. Never execute layout commands or inject real input merely to inspect fixtures. Keep regression tests for observable state transitions and error cases, not source-text assertions.
- Report unavailable Nix, hardware, display-server, or non-Linux verification explicitly. Mock-device checks prove sequencing, not compositor recognition or real hardware behavior.
