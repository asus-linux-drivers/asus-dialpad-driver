# Issue #31: Layout Manager Implementation Plan

## Status and authority

This document is the implementation handoff for [issue #31: Layout manager with GUI](https://github.com/asus-linux-drivers/asus-dialpad-driver/issues/31). It records the user-approved direction and incorporates the subsequent technical review. It supersedes earlier conversational implementation sketches.

**Status: implemented, with platform/hardware verification limits below.** Phases A–E are present in this checkout. This document retains the approved design and its pre-implementation observations; use [README.md](../README.md#layout-manager-and-json-presets) for current user-facing behavior and the source for API details.

The issue asks for graphical shortcut editing and convenient layout switching without manually editing Python files or systemd units. The maintainer requested a cross-platform technology and a reasonably separable GUI. Hot reload is an explicitly selected enhancement, not an inherent prerequisite for a GUI.

### Implementation and verification record

| Area | Implemented components |
| --- | --- |
| Portable model and compatibility | `dialpad_layout.py`, `dialpad_events.json`, `bundled-layouts.json`, `tools/generate_event_catalog.py` |
| Linux adapter, persistence, status, recovery | `dialpad_layout_linux.py` |
| Single-owner runtime and driver cutover | `dialpad_runtime.py`, `dialpad.py` |
| Independent editor and geometry/action controls | `dialpad_layout_manager.py`, `dialpad_layout_editor.py` |
| Installation and preservation | `install_common.sh`, `install_layout_manager.sh`, existing installer stages, service templates, `uninstall.sh` |
| Optional Nix integration | `nix/default.nix`, `nix/module.nix` |
| Behavioral regressions | `tests/test_layout_model.py`, `tests/test_layout_linux.py`, `tests/test_layout_runtime.py`, `tests/test_layout_editor.py`, `tests/test_installation.py` |

Observed verification on Linux with Python 3.14.7 and PySide6 6.11.2:

- The session/hardware follow-ups passed 97 behavioral regressions. The shared-overlay follow-up below passed all 103 with `QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -v`; Qt tests were not skipped. Coverage includes native-device lifecycle, physical-keyboard selection, desktop detection, GNOME input-source parsing, and optional overlay rendering/preview boundaries.
- Python sources compiled and parsed with Python 3.10 grammar. Root shell scripts passed `bash -n`; embedded Python helpers also parsed. This does not substitute for execution on Python 3.10.
- A temporary installed tree, including paths containing spaces, `$`, and `%`, ran the installed CLI, compiled all three bundled layouts with native `libevdev`, preserved custom data on reinstall, and propagated an explicitly changed configuration directory through launchers.
- All four rendered X11/Wayland driver/overlay service templates passed `systemd-analyze --user verify`; the installed manager desktop entry passed `desktop-file-validate`. No service was started.
- The actual manager ran on Wayland against the real directory watcher, runtime owner, recovery writer, and per-instance status server, using fake output devices. Copy/edit/save/activate acknowledgment, raw JSON, canvas interaction, external conflict cancellation, active rename/delete, and manager shutdown were exercised. The existing floating UI rendered feedback on an isolated test socket.
- The offline GUI imported/exported, reordered rules/functions, edited geometry, and saved while imports of Linux-specific Python dependencies were denied. Static editing/preview did not execute embedded commands.
- Actual driver functions were executed without importing hardware-initializing module scope. Held-gesture release/device pinning, command-only/control-only behavior without an overlay, display-title separation, numeric relative events, empty mappings, multitouch resynchronization, and idle selector wakeup were observed.
- The actual startup selection/recovery block recovered the exact last-successful revision from a corrupt request without rewriting selection; a corrupt request plus corrupt recovery failed clearly.
- Removal/reinstallation helpers preserved custom, shared install/config, and nested configuration paths. Explicit purge removed only selected data; modified launchers and unrelated files survived. Provisioning scripts and installed services were not run.

### Physical hardware follow-up (2026-09-25)

The user subsequently authorized and performed physical testing on an Arch Linux **ASUS ProArt P16 H7606WP** in **Hyprland/Wayland**. This supersedes the earlier lack of real-hardware evidence for this specific machine/session:

- The actual driver ran unprivileged with isolated configuration. Its `ASCP1A01:00 093A:3014 Touchpad` reported bounds `0..4645 × 0..3023` and five MT slots. The driver connected to the Wayland session, and Hyprland recognized the uinput keyboard.
- Activation and shutdown transfers succeeded on I2C bus 0, address `0x15`; the user confirmed the center indicator lit after activation.
- Real center touches and both rotation directions reached both the kernel output device and a native Qt window through Hyprland. Excluding separate synthetic counter checks, all **78 physical press/release pairs** matched: `F13=4`, `F14=15`, `F15=19`, `F16=1`, `F17=19`, `F18=20`.
- While a finger remained on the center, the replacement was visibly pending and the old layout stayed applied. Release emitted the old `F13` pair before the replacement became applied; subsequent gestures used the new device/bindings. The user confirmed normal counting and switching.
- Four additional capability-changing idle reloads recreated real uinput devices, with exactly one DialPad keyboard remaining in Hyprland after each application.
- This uncovered delayed uinput destruction caused by python-libevdev reference cycles. The owner now explicitly disposes retired/unpublished devices and the active device at shutdown. Native smoke checks proved immediate removal even with cyclic GC disabled and retained `Device` references.
- The first diagnostic window incorrectly equated evdev `KEY_F13..KEY_F18` with Qt `Key_F13..Key_F18`. The current XKB map produces `XF86Tools` / `XF86Launch5..9`; counting native scan codes fixed the test instrument. It was not an absence of kernel output.
- The user observed roughly ten increments per full turn with the test's `30°` threshold. This run did not establish angular calibration or a precise increments-per-revolution guarantee.
- The test driver/window were stopped, deactivation transfers succeeded, and no DialPad test keyboards remained. No installed service, device permission, or group membership was changed.

**Coverage limits at that stage:** physical multitouch/drop recovery, actual X11 operation, runtime keymap switching/modifier conditions, Windows/macOS, and Nix evaluation/build/VM checks were unverified (`nix` was unavailable). The optional keyboard selector did not recognize the ITE keyboard, and Hyprland repeatedly logged errors parsing empty GNOME input sources (`@a(ss) []`). The following session addresses those two findings.

### Session compatibility and manager-to-hardware acceptance (2026-09-29)

The same H7606WP was tested after a reboot, still in Hyprland/Wayland. Input event numbers changed: the touchpad moved to `event6`, while the ITE keyboard remained `event3`; these numbers are observations, not persistent device identities.

- Keyboard selection now checks typing-key capabilities, preserves priority for historically recognized keyboards, and excludes the virtual input subtree used by uinput. The actual driver opened the ITE keyboard without privilege changes.
- Physical left `Ctrl`, `Shift`, and `Alt` each selected their configured center binding twice (`F14`, `F15`, and `F16`). The unmodified center emitted five `F13` pairs, including successful returns to the unmodified mapping after releasing each modifier. Kernel output and native Qt delivery agreed.
- GNOME input-source polling is gated by desktop identity. Real Hyprland startup no longer produced the previous GNOME parsing error. Reading the machine's typed empty input-source list returned no selected source correctly; regressions cover typed/nonempty lists, MRU/current precedence, invalid indices, and input-method engines. This is not a real GNOME session test; the native Wayland/X11 keymap listeners were not replaced.
- The production manager, with its real Linux adapter and status socket, copied bundled `proartp16`, edited the copy through its raw JSON controls, saved it, and activated it. The actual driver acknowledged the exact saved revision. The bundled copy was never activated before its commands were replaced with harmless test-key bindings.
- Saving through the manager while a center contact remained down showed the new saved/requested revision as pending and not yet running. Release completed the old `F13` action; the subsequent applied revision produced two `F17` center pairs.
- An external file update produced two `F18` center pairs. The manager correctly distinguished its older loaded revision from the newly applied revision. A subsequent invalid JSON file was rejected visibly while the last valid mapping continued to deliver `F18`. Restoring valid content returned status to applied.
- The first application-specific test rule mistakenly used Hyprland's `class` (`dialpad-hardware-peer`), which is not the documented binary-path/window-title matching contract. The test was corrected to match `dialpad application b` in the actual title; production matching semantics were unchanged.
- The corrected physical application-switching run delivered **seven balanced pairs**: three `F13` pairs in A and four `F18` pairs in B, with repeated A/B switches and successful returns to A. Kernel and Qt records matched exactly. The user confirmed normal switching.
- The test windows, driver, private status endpoint, and temporary test files were removed after acceptance. I2C deactivation succeeded; no DialPad test keyboards remained. Touchpad preferences retained their initial `send-events='enabled'` and `tap-to-click=true` values. No service was installed and no groups or device permissions were changed.

**Verification limits at that stage:** deliberate physical multitouch/drop recovery, runtime keymap switching, actual GNOME/X11 sessions, Windows/macOS, and Nix evaluation/build/VM checks. The modifier proof covers the configured physical left Ctrl/Shift/Alt conditions, not every keyboard, right-side modifier, input-method engine, or co-activation configuration. Installed-service restart was verified in the deployment below; login startup and suspend/resume remain untested.

### Installed services and real floating-overlay acceptance (2026-09-29)

- The user authorized driver, manager, and floating-overlay installation on the same Arch/Hyprland machine, explicitly excluding reporting. A temporary local runner sourced the existing installation stages without invoking the installation-statistics scripts or DSDT collection/upload. Package downloads still used the distribution and Python package repositories; this was not an offline installation.
- Arch dependency resolution initially stopped because `python-pyatspi` is not a package in the configured repositories. Changing the installer to `python-atspi` allowed the actual dependency transaction and installation to complete.
- Runtime files and the virtual environment were installed under `/usr/share/asus-dialpad-driver`; the manager and CLI launchers were installed under `~/.local/bin`. The existing group, udev, and module stages completed. No reboot or fresh login was performed.
- Both `asus_dialpad_driver@particleg.service` and `asus_dialpad_driver_ui@particleg.service` were enabled and observed active/running. An explicit user-service restart succeeded, with `NRestarts=0`. The driver detected the ITE keyboard and touchpad, and acknowledged bundled `proartp16` at revision `283e27aef66d4cbcd501a65f96f7badc207abc8d89baa3d9d91586ee90939efc`. Service-file verification and `bash -n install.sh` passed.
- A native Wayland window using the installed production manager copied the bundled layout, then replaced its application mappings with a temporary `none` profile containing three control-only functions and no commands, queries, or key events. Through the actual metadata dialog and ordering controls, `Volume` acquired the display title `Volume Preview` and unit `%`, then moved before `Scroll` and `Brightness`. Save and activation produced the exact driver acknowledgment `0bcf61aabe6c9312e6ee923ceca0864a7b1c7046b8d457a2940ba1ff61864170`.
- The already-running installed overlay, not a competing listener or test window, displayed the driver's feedback for that profile. Hyprland reported its real 275-by-275 floating surface; a cropped compositor screenshot confirmed the three tool labels in their updated order, with the fourth slice left as padding. No feedback datagram was fabricated. This verifies metadata publication and rendering, not physical tool selection or numeric-value feedback.
- Configuration-only activation initially expired against the driver's existing inactivity timestamp before the screenshot. The smoke temporarily set `disable_due_inactivity_time=0` to capture the surface, then restored `120`. This observation is distinct from physical activation gestures, which were not repeated in this deployment smoke.
- The screenshot exposed clipped long labels, including `Volume Preview` and `Brightness`. Separately, all six absolute Elementary icon paths used by the bundled example were absent on this Arch machine. At that stage, the renderer suppressed labels for nonempty icon fields even when icon resolution failed, and the manager's static ring did not exercise that rendering path. The shared-renderer follow-up below addresses both findings.
- The temporary layout and manager were removed. The requested, applied, and durable recovery state returned to the original `proartp16` revision, with `enabled=0`, `socket_enabled=1`, and the original inactivity setting. Both installed services remained running; the overlay was correctly hidden, only one DialPad keyboard remained in Hyprland, and touchpad preferences were unchanged.

**Overlay editing conclusion:** named functions already provide the tool-ring content model, ordered editing, and display metadata. Adding a second tool-ring data model would duplicate existing state. The shared-renderer follow-up below implements clearer entry points, static icon resolution/fallback, a representative non-executing overlay preview, and label fitting. Window size/colors and minimum slice count remain separate presentation/configuration concerns, not missing shortcut-model fields. The bundled `proartp16` fallback is intentionally single-function; its named-function example belongs to the `/usr/share/code/code` application rule.

**Deployment limits at that stage:** fresh-login startup and group/session-environment propagation, suspend/resume, and the earlier platform/hardware limits remained unverified. That first GUI-to-overlay smoke did not test physical ring selection, value queries, missing-icon rendering on the real overlay, or full pixel-level agreement between the static preview and floating renderer.

### Shared overlay renderer and tool-ring preview follow-up (2026-09-29)

- `dialpad_overlay.py` now owns the optional Qt canvas used by both `FloatingWindow` and the manager's `RingPreview`. It paints supplied feedback and resolves local file/theme icons; it has no driver/Linux imports, socket listener, command execution, or query evaluation. The live overlay retains its existing socket/window ownership and feedback-state transitions.
- The manager identifies named functions as tool-ring entries and exposes **Preview tool ring** beside their metadata controls. The existing geometry tab scrolls to the fixed-size preview without changing tab order or hardware-coordinate semantics. Preview-only neutral backing prevents transparent painting from clearing the enclosing editor; the live overlay remains transparent.
- The previous renderer was reproduced without binding a socket: a missing icon removed the otherwise visible label, and a five-slice active function did not highlight a point ten degrees clockwise from the top. The replacement falls back to text for missing/unreadable icons, wraps labels with explicit overflow ellipses, and uses the same clockwise-from-top angles for sector fills and icons.
- All **103 regressions passed**. Six new cases cover missing/corrupt/theme icons, actual SVG/theme rendering and center preservation, non-quadrant highlighted-sector boundaries, bounded Unicode wrapping/elision, center/value feedback transitions, and non-executing/non-mutating preview behavior including explicitly empty titles. The existing uninstall preservation test now also checks removal of the shared code file. After native preview background/scroll refinements, the six overlay and ten editor cases passed again.
- A native Wayland manager using the checkout showed the new preview and actual metadata dialog. The preview displayed the missing-icon text fallback and a real local SVG without executing command/query marker fixtures.
- Only the four GUI/rendering files were updated in the existing local installation, and only the overlay service was restarted. No provisioning/reporting script ran; the driver instance remained unchanged. Both ordinary optional install paths and the Nix manager-only install set include the shared module; the existing Nix Python fileset already includes it, and Qt remains optional.
- A native window using the installed manager copied the protected preset and replaced its mappings with five harmless named functions, then edited the first function through the real metadata dialog. Activation acknowledged revision `da9c1ca28a9be0a140d5b5ca9879c844296c4f96411dca6cd39ad59abf369230`. A cropped compositor screenshot of the installed 275-by-275 floating surface showed `Volume Preview` and `Brightness` in full despite missing icon paths, a real SVG in its correct slice, and a longer notification label wrapping and ending in an ellipsis. The installed manager preview showed matching labels/icon positions on its neutral background. These were real driver datagrams, not synthetic feedback.
- The temporary layout was removed, and the original `proartp16` requested/applied revision and durable recovery state were confirmed restored. Configuration-only activation used a temporary zero inactivity timeout during capture, then restored `enabled=0` and the original `120` seconds. Both user services remained active/running with no abnormal restarts; Hyprland retained one DialPad keyboard and touchpad preferences were unchanged.

**Remaining limits:** Nix was unavailable, so its package build/VM checks remain unverified. No fresh-login, suspend/resume, X11/GNOME session, physical tool-selection, or live numeric/conditional-query feedback acceptance was added. The preview intentionally does not execute queries or reproduce compositor transparency; desktop background contrast and compositor effects can differ from the neutral preview.

### Built-in help and three-language manager follow-up (2026-09-29)

- `dialpad_help.py` provides 25 searchable offline topics, a visible help entry, contextual question buttons, and focused-control F1 navigation in the manager and editing dialogs. Help explains field meanings, precedence, revision states, and execution/trust boundaries without invoking a driver adapter, shell query, socket listener, or external browser.
- The initial `dialpad_i18n.py` implementation loaded English-source JSON catalogs for Simplified Chinese and Traditional Chinese, with English fallback. Each Chinese catalog set contained 341 messages across manager, editor, help, and common interface text. A structural catalog audit found no missing static GUI messages or inconsistent named placeholders. Qt's supplied standard-dialog translations loaded on this machine. The path-key migration below supersedes that resource format.
- The per-user `ui/language` preference uses a separate Qt INI settings file; it never changes a preset or `dialpad_dev`. System language, explicit overrides, Chinese region/script mapping, next-launch behavior, and unmodified user-authored values were exercised. The existing `release`/`immediate` values remain canonical combo-box data rather than translated labels.
- All **110 behavioral regressions passed**, with Qt tests enabled. Seven new cases cover language precedence, preserving an invalid raw draft during preference changes, canonical action data in both Chinese interfaces, help filtering/no-result recovery, safe read-only help/preview access, focused F1 selection inside an editor dialog, and one-time formatting of opaque error details. Uninstall checks also cover the new modules/catalogs while retaining an unrelated user translation file.
- Native Hyprland/Wayland windows were launched separately in English, Simplified Chinese, and Traditional Chinese. The main window, geometry/preview tab, action editor, metadata editor, and help center were exercised and captured. F1 selected the focused field's topic; searching `REL_WHEEL_HI_RES` found the relevant explanation; nearby question buttons opened metadata help. The screenshots showed translated Qt standard buttons and unchanged user titles/event names/paths. No command/query marker fixture ran.
- The installed ordinary launcher was then executed in three separate processes, controlled by a temporary, explicitly scoped Qt smoke hook. In an isolated settings directory, the first process changed the preference from Simplified to Traditional Chinese without changing the active language; the next process opened in Traditional Chinese. A third process using the real user environment selected Simplified Chinese through the unchanged `system` preference. Read-only built-in help and live driver status were verified in these installed windows.
- Installation updated only manager/editor/help/i18n files and the eight JSON catalogs. No provisioning or reporting script ran, and neither driver nor overlay was restarted. Both services retained their original PIDs, reported active/running with `NRestarts=0`, and the same driver instance still reported the original `proartp16` revision as requested, applied, and durably recorded. Driver configuration bytes were unchanged.
- Ordinary manager and optional Nix manager installation include help/translation resources; the existing Python/JSON source fileset includes them. The modified Bash installers passed syntax checks and managed-file removal was exercised in isolated directories. Nix itself remains unavailable, so this is not a Nix build claim.

**Help/i18n verification limits:** native presentation was checked on this Hyprland machine, not on GNOME/X11, Windows/macOS, other fonts/scales/themes, or assistive screen readers. User-authored layout content and backend error details intentionally remain untranslated. CLI/log translation and live in-place language switching are not implemented; the documented contract is next-launch interface selection. The hardware/platform checklist below remains open.

### Stable path-key translation migration (2026-09-30)

- All manager, editor, and help messages now use semantic dot-separated paths, such as `manager.actions.save`, `editor.action.relative_value_tooltip`, and `help.topics.events.body`. Paths contain no spaces; language resources store actual nested objects rather than flat dotted properties. English wording is a value, never an identifier.
- `locales/en_US.json`, `locales/zh_CN.json`, and `locales/zh_TW.json` each contain the same 366 leaf paths and matching named placeholders. The language identifier is the catalog file name without `.json`, so the English interface, the selector entry, and the stored preference all use `en_US`. A preference still holding the earlier bare `en` resolves to `en_US` instead of silently reverting to the system default. English is an independent required catalog. Missing or incompatible translated entries fall back to English; invalid key syntax and missing English keys fail explicitly. The former eight source-text catalogs and their runtime lookup convention were removed.
- Canonical driver states and trigger values use explicit presentation mappings. Unknown protocol values, user-authored data, commands, paths, and backend details are not translation keys. Interface preferences still apply on the next launch, without reconstructing open drafts.
- Numeric `relativeEventValue` inputs now open the `events` topic through F1. Metadata display-query inputs retain the distinct `value` topic. Both paths were exercised without executing fixture commands or queries.
- All **115 behavioral regressions passed**, including nested lookup, stable identity after English wording changes, required English resources, invalid key/catalog shapes, incompatible-placeholder fallback, single-pass opaque formatting, draft preservation, canonical values, contextual help, and managed removal with unrelated translation-file preservation. Modified Bash installers passed syntax checks.
- Native Wayland windows were exercised separately in all three languages: manager, geometry/preview, relative-action dialog, function metadata, and searchable help. Captures were visually inspected. `REL_WHEEL_HI_RES` search selected event help, relative event/value pairs survived acceptance, and changing the preference preserved an invalid raw draft and configuration/layout bytes. Changed Python modules also parsed with Python 3.10 grammar; the actual runtime was the installed Python 3.14 environment.
- Ordinary installation copies exactly the three language resources and retires only known old section catalogs. Optional Nix installation uses the same explicit resource list; no Nix executable is available, so evaluation/build/VM checks remain unverified. Existing hardware/platform and presentation limits below remain unchanged.
- The local installed manager received the four changed Python modules and three nested catalogs; the eight retired catalogs were removed. Its actual launcher opened successfully in English, Simplified Chinese, and Traditional Chinese with isolated language settings, read live status, and selected event help through numeric-field F1. Installed captures were visually inspected. No provisioning/reporting script ran and neither service was restarted: driver/overlay PIDs remained `5248`/`5249`, with each pre-existing `NRestarts=1` unchanged. Configuration, original layout, recovery-file, and real-user UI preference bytes matched their pre-update hashes; the same driver instance retained the original requested/applied `proartp16` revision and current durable recovery.
- The English resource was then renamed to `en_US.json` so the identifier matches the `language_REGION` form already used by the Chinese catalogs. The selector entry, the active language, and the stored preference all use `en_US`; a preference still holding the earlier bare `en` resolves to `en_US` instead of silently reverting to the system default, and the next selection writes the standard identifier. Ordinary installation and uninstallation retire only the pre-rename `en.json`, while a bare `en` no longer names a catalog.
- Verification after the rename: **116 behavioral regressions passed**, including legacy-preference resolution, the selector identifier set, and rejection of a bare `en` catalog name. Native Wayland windows were re-run in `en_US`, `zh_CN`, and `zh_TW`, auditing catalog path set, placeholder equality, every static `tr()` key, and both F1 topic paths. Three separate installs of the actual launcher - legacy `en`, explicit `en_US`, and `zh_TW` - selected `en_US`, `en_US`, and Traditional Chinese respectively while reading live read-only status and leaving configuration, layouts, recovery, and the real-user preference byte-identical; the driver and overlay were not restarted.

### PR #55 Copilot review follow-up (2026-09-30)

- Exclude the reserved `none` profile from both binary and window-title substring passes; fallback still applies after every ordered application rule. A focused regression covers binary and title strings containing `none`.
- Driver feedback now carries `selected_index` from the pinned snapshot's function identity. The shared renderer uses it instead of searching display titles, preserving the chosen sector for duplicate or empty titles across center, rotation, and metadata updates; profile and unconfirmed-gesture resets clear it. Static preview sends an unselected state. Driver feedback was exercised by AST-extracting pure portions of `dialpad.py`, without importing hardware module scope.
- Translation signatures compare each placeholder's name, format specification, conversion, and multiplicity, independent of field ordering. Incompatible localized entries fall back to English before formatting.
- Partial-install removal detects exact DialPad service, udev-rule, and module-load artifact names independently of `dialpad.py`; service and privileged cleanup are separately gated. Sandboxed uninstaller tests intercept `systemctl`, `sudo`, and `udevadm`, preserving unrelated files and editor-only installations without running host cleanup.
- All **127 behavioral regressions passed** with Qt enabled, including the four reviewed failure modes. Changed Python files parsed with Python 3.10 grammar, and modified shell files passed `bash -n`. A standalone `OverlayCanvas` was launched on native Hyprland/Wayland with a 2× scale; captured surfaces and device-pixel samples confirmed the second sector highlights for duplicate and empty titles, and explicit clearing removes the highlight. No physical ring-selection acceptance or installed-driver/overlay restart was performed for these review fixes; existing platform limits remain open.


### Outstanding verification checklist

The following items remain unverified. They are acceptance work, not claims of current support proven by the tests above. Completing one item must record the platform, procedure, observed result, and restored state.

| Area | Still to verify | Acceptance evidence required |
| --- | --- | --- |
| Fresh graphical login | Enabled user-service startup, updated supplementary groups, and display/session environment propagation without a manual restart | Driver and overlay start in the intended session, the physical devices open without ad hoc permission changes, and the status endpoint reports the expected applied revision |
| Suspend/resume | Device reconnection, I2C activation, output-device lifetime, and overlay recovery | Physical activation/gestures work after resume; no stuck keys, duplicate DialPad keyboards, stale visible overlay, or unexpected layout change |
| Physical tool-ring interaction | Tool selection, rotation, center feedback, and highlighted sector alignment with three/five/more functions after the shared-renderer change | Real touch gestures select the intended ordered functions and complete balanced output; screenshots agree with the selected sector |
| Live display metadata | Numeric value queries, units, conditional icons, and stale-result fencing during real application/layout changes | Trusted query results reach the live overlay; delayed results cannot replace the new selection, and an unavailable icon remains identifiable |
| Contact recovery | Deliberate physical multitouch and `SYN_DROPPED` recovery | Publication waits for a complete no-contact boundary and no stuck output survives resynchronization; simulated regression results are not physical proof |
| Keyboard/session variants | Runtime keymap changes, right-side modifiers, input-method engines, co-activation variants, and actual GNOME/X11 sessions | Observe the intended mapping and state transitions in each tested session; existing left Ctrl/Shift/Alt and Hyprland evidence must not be generalized |
| Nix | Evaluation, optional manager package build, and existing VM module check | Run the documented Nix commands on a capable host; verify the shared Qt module is installed only where required. Nix is unavailable on the current workstation |
| Other operating systems | Actual Windows/macOS offline manager operation | Launch and exercise portable editing/import/export there; do not infer Linux runtime support from Qt portability |
| Desktop presentation | Other compositor/scale/theme combinations and background contrast | Inspect actual native windows, label fit, file/theme icons, and transparency on the target setup. The neutral preview intentionally does not reproduce the desktop background |

The installed shared-renderer smoke covers real driver feedback and real overlay rendering, but not physical selection or query execution. Existing automated tests cover many error/state boundaries; none replace the platform and hardware observations listed here.

## 1. Approved decisions and scope

| Decision | Required direction |
| --- | --- |
| Storage | Add JSON layouts; retain supported Python layouts. |
| Runtime | Implement safe hot reload, not just GUI-driven service restarts. |
| GUI ownership | Keep the manager in this repository as an independent, optionally installed application. |
| User layout directory | Use `<config_dir>/layouts/`. |
| GUI technology | Reuse PySide6, already used by the optional floating UI. |
| Built-in editing | Treat bundled layouts as read-only in the manager; edit a user copy instead. |
| Dependencies | Keep Qt out of the headless driver and portable layout library. |

The complete feature includes discovery, copying, editing, validation, saving, activation, renaming, deletion, import/export, application-specific mappings, geometry editing, and confirmation of the actually applied revision. Preserve both single-function and multifunction operation.

Distinguish three concepts in the model and UI:

1. **Hardware template:** DialPad geometry and activation region in touchpad absolute coordinates.
2. **Shortcut preset:** a complete named set of bindings that the user can activate.
3. **Application rule:** an ordered matching rule within a preset.

These distinctions do not require separate files, inheritance, or a preset-overlay system. Geometry remains part of the complete layout document; ordinary shortcut editing should not require recalibration.

### Non-goals

- Rewriting the entire driver or introducing a general plugin framework.
- Keyboard macro recording, cloud synchronization, or a local HTTP server.
- Renaming historical configuration keys such as `treshold` as part of this feature.
- Replacing the existing floating UI or merging its renderer with the editor as a prerequisite.
- Converting all bundled Python layouts in place.
- Automatically calibrating hardware geometry.
- Promising that Linux-specific commands, icons, or hardware operations work on other operating systems.
- Sandboxing arbitrary Python or shell code, or rolling back their external side effects.

Geometry canvas interaction and a raw JSON editor are included in the full editor scope, but should follow the core copy/edit/save/activate workflow. Their implementation must not block independently useful backend changes. Deferring them beyond the complete feature would require an explicit scope decision rather than silently omitting them.

## 2. Pre-implementation code and integration points

This table records the original integration points, not current runtime behavior. The implementation record above and current source supersede removed symbols and startup-only loading assumptions. Use symbols and file contents as the reference; historical line numbers may move.

| Existing location | Relevant behavior |
| --- | --- |
| [dialpad.py](../dialpad.py) startup | Imports `layouts.<argv[1]>`; reads geometry and `app_shortcuts` into globals. The second positional argument selects the configuration directory. |
| `config_get`, `config_set`, `config_save`, `read_config_file` | Read and write `dialpad_dev`; saving currently rewrites an in-memory `ConfigParser`. |
| `load_all_config_values`, `check_config_values_changes` | Reload ordinary settings through an inotify watch on the configuration directory. |
| `initialize_virtual_device`, `enable_key`, `reset_udev_device` | Collect capabilities and create/recreate the uinput device. X11 and Wayland keymap paths also trigger recreation. |
| `listen_touchpad_events` | Owns cross-event gesture locals and computes some geometry only at entry. Finger release can execute center actions. |
| `send_key_event` | Reads global `udev` separately while sending press and release events. |
| `window_was_changed`, `is_multifunction`, `get_current_value` | Resolve mappings and display metadata; metadata evaluation can execute shell commands. |
| [layouts/](../layouts) | Three bundled Python layouts, including command-only functions and standalone display titles. |
| [dialpad_ui.py](../dialpad_ui.py) | Optional PySide6 floating feedback UI; its existing datagram socket is not a multi-client control API. |
| [install.sh](../install.sh), [install_service.sh](../install_service.sh), [install_layout_select.sh](../install_layout_select.sh) | Install selected files and embed the layout name in the service command. |
| [install_user_interface.sh](../install_user_interface.sh), [uninstall.sh](../uninstall.sh) | Optional Qt installation and preservation/removal of installed configuration and layouts. |
| [nix/default.nix](../nix/default.nix), [nix/module.nix](../nix/module.nix) | Explicit package file installation, optional display dependencies, and user configuration under `%E/asus-dialpad-driver/`. |

Do not import `dialpad.py` from the editor or validation tools: its top-level code performs session and hardware initialization.

## 3. Architecture and platform boundaries

Use the existing flat project organization. The following responsibilities are required; split additional files only where they have a clear owner.

### `dialpad_layout.py`: portable model and storage

- Pure-data layout representation, JSON parsing, normalization, validation, serialization, and discovery.
- A safe static importer for the supported literal subset of Python layouts.
- A small CLI for listing, validating, converting, and exporting layouts.
- Portable JSON saving using a unique temporary file in the target directory and atomic replacement.
- No import-time dependency on PySide6, `libevdev`, `fcntl`, pyinotify, systemd, a display server, or device access.

The shared model contains event names and ordinary Python data, not `EventCode` objects. Do not put Linux locking inside an otherwise portable `save_layout` implementation.

### Linux adapter, proposed as `dialpad_layout_linux.py`

- Convert normalized event names to the actual `EventCode` objects supported by the installed `libevdev`.
- Load explicitly trusted dynamic Python layouts.
- Supply locked configuration updates and Linux activation/status integration.
- Validate hardware-specific constraints using actual device information.
- Keep device creation and replacement coordinated with the driver runtime owner, not with GUI callbacks.

The CLI may expose Linux-only `activate` and `status` operations through lazy imports of this adapter. Listing and offline JSON editing must remain usable without it.

### `dialpad.py`: runtime owner

- Own the active compiled layout, effective application mapping, gesture state, and output-device lifecycle.
- Consume prepared candidates and other runtime update requests at defined boundaries.
- Publish accurate application status to the manager and structural feedback to the floating UI.

### `dialpad_layout_manager.py`: optional application

- Use PySide6 for forms, previews, dialogs, and desktop integration.
- Use the same portable model and validator as the driver loader.
- Connect to Linux runtime integration only for activation, device information, and live status.
- Do not directly import or initialize the driver.

Ship a platform-independent event-name catalog for the key picker and structural validation. Document how it is generated; the GUI must not need to load `libevdev.so` to enumerate keys. The Linux adapter remains authoritative about capabilities supported by the installed runtime. Ensure new catalogs and manifests are included in both ordinary and Nix installations.

## 4. Layout document and compatibility contract

### 4.1 JSON envelope

Use `schema_version: 1`, `geometry`, and `app_shortcuts`. An optional `name` is a display label, not a path or authoritative layout identifier. Initially retain existing shortcut field names at the external format boundary and normalize them internally; do not expose the driver's ambiguous dictionary handling throughout the GUI.

The following is a complete example of the proposed format, not a claim that the current driver can load it:

```json
{
  "schema_version": 1,
  "name": "My ProArt",
  "geometry": {
    "top_right_icon_width": 250,
    "top_right_icon_height": 250,
    "circle_diameter": 919,
    "center_button_diameter": 364,
    "circle_center_x": 586,
    "circle_center_y": 573
  },
  "app_shortcuts": {
    "code": {
      "center": [
        {"trigger": "release", "duration": 0.5}
      ],
      "Notifications": {
        "command": "notify-send 'DialPad action'"
      }
    },
    "none": {
      "center": [
        {"key": "KEY_MUTE", "trigger": "release", "duration": 1}
      ],
      "clockwise": [
        {
          "key": ["REL_WHEEL", "REL_WHEEL_HI_RES"],
          "value": [1, 120],
          "trigger": "immediate",
          "title": "Scroll"
        }
      ],
      "counterclockwise": [
        {
          "key": ["REL_WHEEL", "REL_WHEEL_HI_RES"],
          "value": [-1, -120],
          "trigger": "immediate",
          "title": "Scroll"
        }
      ]
    }
  }
}
```

### 4.2 Normalize semantics explicitly

Represent the following concepts distinctly inside the shared model:

- Key combinations.
- Relative-axis events paired with numeric event values.
- Shell-command actions.
- Selection/confirmation controls without an output action.
- Trigger, duration, and modifier conditions.
- Display labels, units, icons, and value-query commands.
- Selected function identity, separate from display text.

The external `value` field is historically overloaded: numeric arrays accompany relative events, while strings are display-value queries. Resolve this at parsing time. Do not let every execution or UI path rediscover its meaning independently.

Likewise, distinguish event names from any legacy character/keysym representation. Verify actual existing behavior before extending or claiming support; unsupported conversion must fail explicitly instead of silently changing a binding. Preserve the original Python file when a lossless conversion is unavailable.

### 4.3 Required compatibility cases

- Command-only functions such as the bundled `Notifications` do not need clockwise or counterclockwise actions.
- Single-function entry titles such as `Scroll` and `Volume` are labels, not required references to function dictionaries.
- A center entry containing only trigger/duration can be a valid control action.
- An explicit empty application mapping, such as `firefox`, is valid. Do not silently replace it with the default mapping.
- Preserve the `none` fallback mapping and the actual application's matching precedence.
- Preserve function order, application-rule order, and action-list order. Use the shared JSON serializer without sorting object keys. The manager must not reorder dictionaries incidentally through another JSON implementation.
- Preserve modifier matching and action precedence. In the current `emulate_shortcuts`, entries with a `modifier` sort before entries without one; an earlier discussion incorrectly described the opposite order.
- Keep `treshold` and existing configuration-key spellings at the compatibility boundary.
- Fix the runtime distinction between a display title and a selected function. Do not paper over incorrect lookups with a validator that rejects bundled examples.

### 4.4 Validation

Produce structured issues with severity, field path, and message. Use the same structural rules in CLI, GUI, and driver loading.

Errors include unsupported schema versions, duplicate JSON keys, invalid structures, non-finite numeric values, incompatible event/value combinations, invalid trigger or modifier types, unresolved required event names, and missing required fallback data. Do not silently discard unsupported fields during an editor round trip; reject an unsupported document clearly rather than saving a damaged subset.

Geometry uses touchpad absolute coordinates, not screen pixels. Validate finite values, positive circle diameters, a center diameter no larger than the outer diameter, and valid activation-region dimensions. Device-dependent checks belong in the Linux adapter. Missing hardware information must not prevent offline editing; do not invent touchpad bounds and present them as measured values.

Missing optional icons or unavailable platform-specific commands can be warnings. Validation is not a shell security scanner and must not execute a command to decide whether the document is valid.

Acceptance starts with all three bundled layouts preserving their behavior after normalization and JSON round trips, but must extend beyond those examples.

## 5. Discovery, identity, and Python loading

### 5.1 Paths and precedence

Search `<config_dir>/layouts/` before `<driver_install_dir>/layouts/`, deduplicating equivalent directories. Within a directory, prefer a JSON layout over a same-stem Python layout. Directory priority precedes extension priority.

A discovered higher-priority file that is invalid must produce an error; do not silently select a lower-priority file and report success.

Keep the selected layout identifier, display name, resolved path, format, provenance, and content revision distinct. A user-controlled JSON name must never determine an arbitrary write path. Validate identifiers and enforce destination containment for manager-created files. Importing an explicitly selected external file is a separate operation from resolving an installed identifier.

Do not infer built-in identity from file permissions or directory membership. In ordinary installations the user and bundled directories can coincide. Ship an installation/build catalog of bundled layout identities, outside the discoverable layout-file set, so the manager can protect built-in files and explain overrides. Create user copies with a distinct identifier by default.

The installed GUI and CLI launchers must receive the same configuration directory as the driver. Preserve custom `CONFIG_FILE_DIR_PATH` installations and Nix paths. Do not silently choose the first existing directory from a heuristic search when multiple instances could exist. Show the effective configuration path in the manager.

### 5.2 Trusted Python compatibility

Directory listing, selection, static preview, and validation must not automatically execute arbitrary Python files. Support a restricted AST reader for the shipped literal assignments and known event references. Unsupported dynamic files remain Python layouts; offer explicit trusted conversion or retain them unchanged rather than guessing their data.

For trusted dynamic loading and hot reload:

1. Read the selected source bytes once.
2. Derive the source revision from those bytes.
3. Compile and execute those exact bytes in a fresh, correctly initialized module namespace.
4. Extract and normalize the supported layout data before creating a runtime candidate.

Do not rely on `spec_from_file_location` plus omission from `sys.modules` to bypass bytecode caching. `SourceFileLoader` can reuse timestamp/size-based `.pyc` files for same-second, same-size edits. Preserve required module metadata and supported import context, but explicitly exclude automatic reload of arbitrary imported helper modules from the guarantee.

Python execution is trusted code execution. A failed candidate can leave external side effects from that code; runtime rollback does not undo them.

## 6. Configuration and persistence

### 6.1 Active selection

Use the `layout` setting in the existing `[main]` section of `dialpad_dev`.

Precedence is:

1. An explicitly configured nonempty `layout`.
2. The existing first positional driver argument as the default.
3. A clear startup error if neither provides a selection.

Keep the existing positional startup interface working. Do not automatically persist the argv fallback as an explicit layout override. Otherwise subsequent Nix `cfg.layout` changes or installer defaults can be masked indefinitely.

The installer must make an explicitly chosen new layout effective through the same configuration-update path, while respecting the user's choice to preserve existing settings. It must not silently replace an explicit GUI selection with an automatic hardware suggestion during an unrelated reinstall. Explain any explicit selection change.

### 6.2 Patch-based configuration transactions

Replace whole stale-snapshot saves with a shared operation conceptually named `update_config(patch, defaults_if_missing)`:

1. Acquire a stable sidecar-file lock in the Linux adapter.
2. Read the latest on-disk configuration while holding the lock.
3. Merge only the caller's changed keys; insert defaults only for still-missing keys.
4. Write a unique temporary file in the same directory, preserve appropriate permissions, and atomically replace the configuration file.
5. Release the lock and return the resulting configuration.

Lock the stable sidecar, not the data-file inode that will be replaced. Preserve unrelated keys and sections. Report malformed configuration or I/O errors rather than overwriting the file with defaults.

Driver activation/deactivation submits only `enabled`. The manager submits only its explicit changes, such as `layout`. Startup default initialization submits missing defaults, not the complete old in-memory parser. Any deferred-save behavior must accumulate a true dirty-key patch rather than later writing a stale full snapshot.

Migrate `config_set`, startup saving, GUI/CLI writes, and relevant installer writes to the same transaction rules. An atomic rename and a lock around only the write are insufficient.

### 6.3 Layout saves and revisions

Save documents atomically with unique same-directory temporary files. Preserve source and destination on pre-replacement failure, report permission errors, and reject accidental overwrites of bundled files. Detect an external modification of an open document before overwriting it; let the user reload or save a new copy instead of silently losing changes.

Persist a complete new layout file before updating the active selection to reference it. Keep dirty editor drafts separate from saved files. Rename or deletion of an active layout must first select and successfully apply a replacement, or explicitly stop using that layout; do not leave a dangling active identifier.

## 7. Safe hot-reload protocol

### 7.1 Ownership and state

The input/runtime owner is the single committer of effective layout, application mapping, keymap-dependent state, and output-device replacement. Watchers and window/keymap callbacks submit updates rather than partially changing those shared structures themselves.

Use a stable runtime snapshot containing compiled mappings, precomputed geometry, relevant capability state, and a generation/revision identity. Keep mutable gesture state separate. A gesture captures the snapshot and application mapping it uses; display titles are not function identifiers.

Migrate existing X11/Wayland keymap-triggered device resets and all relevant publication paths into this ownership rule. Fixing only the new reload path leaves the old races intact.

### 7.2 Prepare, queue, and wake

- Watch the configuration directory and the actual layout search directories; account for atomic replacement and active-file removal.
- Filter events by relevant files. Ignore lock files, temporary files, bytecode caches, and recovery metadata.
- Treat inotify as a notification to examine current state, not as a transaction log.
- Parse and validate candidate data without changing the active runtime or executing value-query commands.
- Queue a candidate with its request generation, identifier, resolved source, and revision. Reject stale preparation results when a newer request has superseded them.
- Wake the event loop through a notification fd or equivalent mechanism. Multiplex it with nonblocking device input; wrapping a still-blocking generator in `selectors` is not enough.
- Do not discard an external change merely because an internal configuration lock happens to be held. Defer/coalesce and re-examine the latest state instead.

### 7.3 Safe boundary and commit

A candidate can commit only after the current input frame is complete, all active contacts have ended, and release actions for the old gesture have completed. Check actual contact state, including multitouch slots; a single boolean is not a universal no-contact proof. After dropped events, resynchronize before assuming a safe boundary.

At commit:

1. Resolve the candidate against the current keymap/device context. If that context changed since preparation, do not publish a stale compilation.
2. Prepare necessary output capabilities and any replacement device without destroying the still-usable old device. Reuse it when capabilities do not require replacement.
3. Ensure that no output sequence is in progress.
4. Publish the complete runtime snapshot as one owner-controlled transition.
5. Reset the complete gesture/selection state, including former listener locals, not just the fields cleared by `reset_center`.
6. Refresh structural floating-UI state and publish the actual applied revision.

A failed parse, validation, compilation, or device preparation must leave the old usable runtime intact. Do not publish new mappings first and attempt to repair the device afterward.

A full key-combination press/release sequence must pin one device reference and be mutually exclusive with device replacement. Establish a documented lock order. Never wait for a queued runtime operation while holding a configuration/file/device lock needed to complete it. Avoid allocation and recomputation of immutable layout data on every input event.

### 7.4 Commands and responsiveness

Do not directly invoke existing metadata helpers inside the commit critical section without separating their effects. `is_multifunction` can call `get_current_value`, which executes a shell command.

Pure profile selection and structural metadata publication belong in the commit. Dynamic value/icon queries happen afterward, outside publication and device locks, and results are associated with the revision that requested them. A late result must not overwrite a newer layout's display.

Do not promise bounded application latency while a finger remains down or an existing synchronous command is still running. Report a pending state honestly. A wholesale command-execution redesign is not a prerequisite for this feature.

## 8. Recovery across restarts

Distinguish the requested layout from the applied revision and the last durable, successfully applied snapshot.

After a successful runtime commit, atomically persist the normalized, executable-code-free layout data and provenance needed to restore that applied version. Store recovery data outside the discoverable layout set, in a dedicated subdirectory of the selected configuration directory. Validate recovery data before use; never persist live Python objects or bytecode.

On startup:

- Attempt to load the requested selection.
- If it is invalid or missing, attempt the validated last-successful snapshot.
- Report recovery explicitly, including the requested selection, recovered revision, and error. Do not rewrite the requested selection behind the user's back.
- If neither is usable, report a clear startup failure and do not silently activate an unrelated bundled layout.

Runtime commit and disk persistence are not a single atomic transaction. If recovery persistence fails after a successful live commit, status must report both facts accurately: the new revision is active, but durable recovery is not updated. Do not falsely report that the previous revision is still active or that recovery persistence succeeded. Define flush/sync behavior for recovery writes and do not claim stronger crash durability than the implementation provides.

## 9. Manager status and control boundary

Use a driver-owned, current-user-only Linux endpoint under `$XDG_RUNTIME_DIR`, with separate identities for different configuration directories. Do not add a shared `/tmp/dialpad_manager.sock`, reuse the floating UI's single-consumer socket as a control channel, or require a systemd installation for status to work.

The minimal control surface is read-only status/subscription; persisted file changes remain the source of activation requests. Associate responses with a driver instance identity, request generation, and source revision. Validate client access and endpoint ownership. The manager must not delete another process's endpoint during startup.

Expose at least:

- Requested selection and revision, when available.
- Applied selection and revision.
- Pending, applied, rejected, and recovered states with an error/reason where relevant.
- Whether recovery persistence is current.
- Device geometry information already known to the driver, when available.

The GUI separately distinguishes an unsaved draft, a saved document, and a driver-applied revision. Absence of a reachable endpoint means offline/unavailable status, not successful application.

A service restart can be an explicit diagnostic action only when the correct service instance is identified. Never automatically restart to conceal a rejected candidate or claim hot reload succeeded. Support both existing service naming conventions where relevant, but do not restart an unrelated instance merely because a familiar unit name exists.

## 10. GUI behavior

### Core workflow

1. List bundled layouts and user copies with clear provenance, override relationships, and the actual active revision.
2. Copy a bundled layout to a new user identifier.
3. Edit application rules and shortcuts through structured controls.
4. Validate and atomically save without activating implicitly.
5. Activate explicitly by updating the requested selection.
6. Show pending/applied/rejected status for that exact revision.

An edit to an already active saved file is a runtime reload request, so the Save action must make that consequence clear. Keep draft-only changes local. A rejected version remains visibly different from the running version.

### Editors and preview

- Provide application-rule ordering and explicit single-function/multifunction editing.
- Include searchable event selection, supported modifier conditions, trigger/duration controls, command fields, labels, icons, units, and historical threshold values.
- Present event values and display-value commands as distinct controls despite their legacy external field name.
- Preserve command-only and control-only entries without forcing dummy rotation bindings.
- Provide numeric geometry editing and a canvas for moving/resizing the relevant regions. Use driver-reported extents when available; otherwise label the view as schematic/unknown-bounds rather than measured.
- Provide a static function-ring preview honoring actual order and slice padding. Do not execute commands for preview.
- Provide a raw JSON editor with an explicit valid-draft-to-form synchronization boundary. Invalid JSON must remain an editable draft, not overwrite the last valid form or saved document.
- Handle dirty-state navigation, overwrite conflicts, cancellation, rename, deletion, import, and export without losing edits.

The editor can run without a driver, display session integration, or `libevdev`. Linux activation controls are unavailable in offline/non-Linux mode, but pure JSON editing and export remain usable. Cross-platform toolkit selection does not by itself prove cross-platform execution; verify the actual import boundary.

## 11. Implementation phases and deliverables

All phases are part of the complete feature. Do not stop at a parser-only implementation and describe the issue as solved.

### Phase A: model, compatibility, and portable loader

- Implement the portable data model, normalization, JSON codec, issue reporting, safe static Python import, discovery, and event-name catalog.
- Add the Linux event-code/trusted-Python adapter without import-time leakage into the portable layer.
- Establish explicit compatibility fixtures for the three bundled layouts and their special cases.
- Expose listing, validation, and conversion through the CLI without importing `dialpad.py`.

**Exit evidence:** order-preserving JSON round trips, accepted command-only/control-only/label-only cases, rejected malformed documents with field paths, no command execution during static operations, and same-second/equal-size trusted Python reload behavior.

### Phase B: startup selection, transactional persistence, and package integration

- Replace the current import-only startup loader with the shared model/adapter.
- Implement configuration patch transactions and migrate every relevant writer, including startup defaults and installer-selected layout changes.
- Implement explicit configuration-directory propagation and deterministic discovery/provenance.
- Install every runtime-required module and data file through `install.sh` and `nix/default.nix`; update the Nix source fileset as needed for non-Python data.
- Preserve positional startup compatibility and document config-versus-argv precedence, including Nix behavior.

**Exit evidence:** ordinary installed and Nix-packaged paths contain the new dependencies; CLI/startup selection works without unit editing; concurrent layout/enabled updates do not lose either change; preserved configuration remains effective after reinstall.

### Phase C: safe hot reload, status, and recovery

- Introduce the owner-controlled snapshot and gesture boundary described above.
- Add candidate preparation, generation tracking, watcher dispatch, event-loop wakeup, and input resynchronization handling.
- Migrate existing output-device and window/keymap publication paths into the same ownership contract.
- Separate structural metadata from command-backed value retrieval in the commit path.
- Add per-instance status and last-successful recovery persistence.
- Update affected installation/package files and documentation in this phase, not in the later GUI change.

**Exit evidence:** old gestures cannot execute new bindings; press/release cannot split across devices; idle application does not require another touch; failed candidates retain a usable runtime; status identifies the correct revision; restart recovery follows the documented policy.

### Phase D: manager and optional desktop integration

- Implement the complete manager workflow and editors described in Section 10.
- Add an independent optional manager installation path, proposed as `install_layout_manager.sh`, reusing `requirements.ui.txt` where appropriate.
- Allow manager installation without requiring the floating overlay service, and vice versa. Share dependency installation without coupling their enablement settings.
- Install a desktop entry and launcher that propagate the actual configuration directory. No always-running manager service is required.
- Add optional Nix GUI support without putting PySide6 into the headless package by default. Proposed option names are `layoutManagerSupport` for packaging and `layoutManager.enable` in the module; follow existing option conventions when implementing.
- Update uninstall behavior to remove launchers/application files while preserving user data by default, including custom configuration directories.
- Update the layout selector to enumerate actual supported files rather than all directory entries; supporting JSON requires more than changing suffix stripping.

**Exit evidence:** launch the actual GUI, copy a bundled layout, edit/save/activate it, observe driver acknowledgment, exercise dirty-state and error paths, and verify that closing the manager does not stop the driver.

### Phase E: integrated acceptance and documentation

- Complete the matrix below on available environments and report unavailable hardware/platform coverage explicitly.
- Update [README.md](../README.md) with schema semantics, paths, precedence, installation, trust boundaries, offline behavior, recovery, and troubleshooting; update [CHANGELOG.md](../CHANGELOG.md).
- Preserve compatibility of the existing floating overlay and headless/non-systemd usage.
- Confirm that user layouts and recovery data survive the documented reinstall/uninstall flows.

### Suggested PR boundaries

1. Phases A and B: portable data layer, startup support, transactions, and complete backend packaging.
2. Phase C: safe runtime reload, status, and recovery, with its own package/documentation changes.
3. Phase D and final integration: manager and optional desktop packaging.

Two PRs are also acceptable if the first combines A through C. In either arrangement, every PR must install its own required modules and data. Moving driver-required installation changes into a GUI-only follow-up makes the earlier PR unshippable.

## 12. Verification matrix

Use deterministic regression tests for real behavioral hazards. Use throwaway scripts and actual application smoke runs for straightforward integration. Do not test source text or count wrappers as proof of behavior.

| Scenario | Required observable result |
| --- | --- |
| Bundled layouts converted to JSON and loaded again | Values, rule/function order, and supported action behavior are preserved. |
| Command-only function, control-only center, standalone display title, empty app mapping | Accepted and executed/interpreted according to existing intended semantics; no invented bindings. |
| Unsupported schema, duplicate keys, non-finite numbers, invalid event/value types | Precise validation failure without partial application or lossy save. |
| Modifier alternatives and application-rule precedence | The intended matching action wins; serialization does not change priority. |
| Trusted Python file changed within one second without changing byte length | New source takes effect even if a valid-looking `.pyc` exists. |
| Candidate requested during a held gesture | The old gesture, including release actions, remains on its captured mapping; application waits for a safe boundary. |
| Device/keymap update during a key combination | All presses and releases in the sequence target one device; no stuck keys from replacement. |
| Multitouch or dropped input events during reload | No false no-contact boundary; state is resynchronized before commit. |
| Layout changed while input is idle | The loop wakes and can commit without waiting for an unrelated future touch. |
| Several rapid revisions with slow preparation or metadata queries | A stale candidate or result cannot overwrite the newest requested/applied state. |
| Parse, compilation, or candidate-device creation failure | Old runtime and device remain usable; status reports rejection accurately. |
| GUI selects a layout while driver changes `enabled` | Both updates survive; unrelated keys and sections remain intact. |
| Corrupt requested layout followed by driver restart | A valid last-successful snapshot is explicitly recovered, or a clear failure is reported if none is usable. |
| Recovery persistence fails after a live commit | Actual active revision and failed durability are reported separately and truthfully. |
| Imported document contains shell commands | Open, validation, conversion, and static preview execute none of them. |
| Higher-priority layout is invalid | Error is visible; no silent fallback to a same-name lower-priority file. |
| Rename/delete active layout or edit externally while GUI is open | No dangling active reference and no silent loss of external changes. |
| Multiple configuration directories or GUI clients | Correct per-instance status; no endpoint takeover or cross-instance activation. |
| Normal and Nix installation | All required modules/catalogs are present; manager dependency remains optional; launchers use the correct config directory. |
| Real GUI workflow and existing floating UI | Editing and application feedback work on the actual surface; closing the manager leaves driver/overlay behavior intact. |
| Portable editor/library without Linux dependencies | JSON editing and static preview work; activation is clearly unavailable rather than failing during import. |

Real hardware validation is needed for input delivery and compositor recognition of recreated uinput devices. Mock-device tests can prove state and sequencing invariants but do not replace X11/Wayland hardware smoke checks. Do not claim Windows/macOS compatibility solely from Linux tests or from PySide6's platform support.

## 13. Evidence already collected during design

These observations justify the contracts above; they are not tests of a future implementation:

- A restricted AST conversion of all three bundled layouts followed by a JSON round trip preserved values and dictionary/list ordering without executing layout code or commands.
- All three bundled layouts contain a command-only `Notifications` function, standalone `Scroll`/`Volume` display titles, an empty `firefox` mapping, and a control-only center entry.
- Extracting the existing `send_key_event` function and using controlled device substitutes reproduced a press on the old device followed by release on a replacement device.
- Extracting the existing `config_save` function into a temporary-directory experiment reproduced a GUI-written `layout-b` being overwritten by the driver's stale `layout-a` while saving `enabled`.
- On Python 3.14.7, a temporary layout changed from `circle_diameter = 919` to `920` within the same integer timestamp second and with equal source size still loaded as `919` through a fresh `exec_module` invocation without insertion into `sys.modules`. Compiling the freshly read source bytes produced `920`.

No real-device hot reload, completed GUI, or cross-platform execution was demonstrated by those design experiments.

## 14. Original instructions for the implementation session

1. Read this document and inspect the current affected symbols and installation conventions; source may have changed since the design review.
2. Keep the four approved architectural decisions. Do not silently replace hot reload with service restarts or omit the GUI.
3. Create phase-level execution tasks from Section 11 and implement the complete requested scope. A phase boundary is not completion of the feature.
4. Establish the portable-model/runtime boundary before parallelizing work. Keep one integration owner for `dialpad.py`; do not let GUI and runtime work invent competing schemas or persistence conventions.
5. Verify each changed behavior with the appropriate tests or smoke scenario. For GUI work, run the actual surface rather than relying only on imports or compilation.
6. Include runtime installation, Nix packaging, and relevant documentation in the same change that introduces a new dependency or module.
7. Use temporary configuration directories and isolated driver instances for experiments. Do not overwrite an installed user's layouts or manipulate their running service as an incidental test. Obtain authorization for disruptive system/device operations when required.
8. Report exactly which hardware, display-server, installation, and non-Linux scenarios were exercised, and which were unavailable. Keep claims narrower than the evidence.
