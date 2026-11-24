# ModemDiag progress and verification

## 1.2 — Linux and Windows, 2026-11-23 / 2026-11-24

Linux x86_64 was released on 2026-11-23. The Windows x64 package was added on
2026-11-24. The changes below compare v1.2 with v1.1; no signing-service approval
or app-store submission is claimed.

### Interface and everyday use

- Separated navigation tabs with visible borders and a continuous divider.
- Simplified Help → Updates to show the app version and update information, with technical
  and privacy information behind Show Details. Automatic checking, offline behavior and
  signed release restrictions remain unchanged.
- Added the Dashboard as the first tab: operator and radio details, signal history,
  SIM and CS/PS/LTE/5G/IMS registration, data contexts and an interactive serving/neighbour
  cell view. Cells are schematic; reported detail depends on modem support.
- Added AI Diag ★ Beta as the fourth tab, with OpenAI/OpenAI-compatible API connections,
  diagnostic chat, reviewed command plans and bounded automatic read queries. API keys
  and conversations stay in memory; configuration changes require review.
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
  command ending changed from **CRLF (v1.1) to CR (v1.2)**. Choose CRLF in Serial…
  if your modem needs the earlier behavior. Raw payload bytes remain exact.
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
- Windows users must extract the complete ZIP and run `ModemDiag-app/ModemDiag.exe`;
  keep `_internal` alongside the executable instead of copying only the EXE.
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
- The supplied Windows x64 ZIP passed archive integrity checks; the embedded application
  reports 1.2, includes the new feature modules, and matches all 55 reviewed native-library
  hashes. The existing source ZIP includes the verified Windows bootstrap build inputs.
- Windows startup timing, actual Authenticode signing and real Windows USB driver behavior
  still require verification on Windows.
- The reported modem-specific command failure is not yet reproduced. Compare the exact TX/RX
  and serial settings with a working terminal when hardware is available; no proprietary modem
  implementation details were added to the project.
