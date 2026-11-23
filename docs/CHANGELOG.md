# ModemDiag progress and verification

## 1.2 — Linux release, 2026-11-23

Version 1.2 ships a Linux x86_64 release and matching source archive. A Windows 1.2
binary will follow separately; no signing-service approval or app-store submission is claimed.

### Interface and everyday use

- Separated navigation tabs with visible borders and a continuous divider.
- Simplified Help → Updates to show the app version and update information, with technical
  and privacy information behind Show Details. Automatic checking, offline behavior and
  signed release restrictions remain unchanged.
- Reduced the dashboard's network, radio, received-power and data-session summaries.
- Retained the network overview, cell exploration, signal history and AI Diag beta.
- Added configuration ZIP export/import, validation, automatic backups and immediate reload.
  Saved command edits, favorites, automations, theme and AI preferences transfer between PCs.
  Logs, AI API keys and chats are excluded. Modem-internal settings are not captured.
- Made continuous logging visible, with start/stop controls and access to the logs folder.
  Traffic, partial replies, payloads and step outcomes save during execution. Logging write
  failures are reported without stopping modem work.

### Automation and serial reliability

- Added read-only device inventory, APN/data-session and IoT power-saving examples.
- Removed configuration writes from the LTE example, inspected all data contexts rather than
  assuming CID 1, and corrected the LTE-registration assumption for standalone 5G.
  Fibocom-provided examples were left unchanged.
- Fixed manually typed ports retaining a previous port selection and scans replacing explicit choices.
- Added CR, CRLF and LF send endings, serial framing and initial DTR/RTS controls. The default
  command ending is now CR. Raw payload bytes remain exact.
- Joined fragmented receive data, preserved split UTF-8 characters, and accepted CR-only final results.
- Closed stale handles after USB read/write failures and observed port removal; stopped active
  work and ignored old-session events after reconnect. Interrupted commands are not resent.
- Prevented Auto-Detect from competing with Connect for the same serial device.

### Startup and distribution

- Added a loading window, faster safe YAML loading and fewer unnecessary legacy-library scans.
  A local measurement of interface construction fell from approximately 0.88 s to 0.61 s;
  this excludes executable extraction and is not a Windows benchmark.
- Changed Windows builds to a folder bundle to avoid repeated extraction, disabled UPX and
  added product/version metadata. Linux still offers a single-file build and optional folder build.
- Added Windows Authenticode signing/verification support and complete folder-bundle inventory checks.
- Documented free SignPath Foundation and Microsoft Store MSIX distribution routes.
  No signing identity has been obtained; no SignPath approval or Store submission is in place.

### Verification and outstanding work

- The preceding implementation passed 264 automated tests, including configuration rollback,
  abrupt-process-exit log persistence, serial compatibility and USB-removal handling.
- The version 1.2 follow-up passed all 266 automated tests, including the simplified Updates
  dialog, retained release restrictions and inclusion of this progress record in source packages.
- The standalone Linux executable was rebuilt, its embedded version was verified as 1.2,
  and it passed an isolated GUI startup check.
- Linux single-file and folder bundles were built and passed isolated GUI startup checks.
- Dashboard, terminal/command, automation, AI and the simplified Updates dialog have
  dark and light previews in `docs/`.
- Windows startup timing, actual Authenticode signing and real Windows USB driver behavior
  still require verification on Windows.
- The reported modem-specific command failure is not yet reproduced. Compare the exact TX/RX
  and serial settings with a working terminal when hardware is available; no proprietary modem
  implementation details were added to the project.
