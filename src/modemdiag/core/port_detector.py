"""
Cross-platform port detector.

Linux  : reads /dev/ttyUSB* + sysfs interface numbers — no udevadm needed.
Windows: 1) Registry read of Modem device class for AT port (fast, reliable)
          2) pyserial descriptions + VID:PID from hwid string for the rest.

Quectel 4-port standard mapping (EC25, EM12, EM20 etc.):
  Interface 0 → DM / Diagnostics
  Interface 1 → NMEA / GNSS
  Interface 2 → Modem / PPP  (serial)
  Interface 3 → AT commands
"""
import os
import re
import platform
import serial.tools.list_ports
from PyQt6.QtCore import QThread, pyqtSignal

# Known Vendor IDs (lower-case hex, no 0x)
KNOWN_VIDS = {"2c7c", "05c6", "1199"}   # Quectel, Qualcomm, Sierra

# EC25 confirmed layout: interface 2 = Modem/PPP, interface 3 = AT
QUECTEL_IFACE_MAP = {0: "dm", 1: "nmea", 2: "serial", 3: "at"}

# Windows keyword fallback — ordered SPECIFIC → GENERIC.
# No generic manufacturer names (they match every port from the same maker).
KEYWORDS_WIN = [
    # (category, [keywords])  first match wins
    ("nmea",   ["nmea", "gnss", "gps port", "quectel gnss"]),
    ("dm",     ["dm diagnostics", "dm port", "diagnostics port",
                "diag port", "hs-usb diagnostics", "qualcomm hs-usb diagnostics"]),
    ("at",     ["wwan modem", "wwan at", "at port",
                "hs-usb modem", "qualcomm hs-usb modem", "quectel at"]),
    ("serial", ["usb serial port", "uart port", "usb-serial",
                "cdc serial", "quectel serial", "hs-usb serial"]),
]

# Windows Modem device-class GUID (all modems live here in the registry)
_MODEM_CLASS_GUID = "{4D36E96D-E325-11CE-BFC1-08002BE10318}"


# ── Linux helpers ─────────────────────────────────────────────────────────────

def _sysfs_read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return ""


def _linux_iface(device: str):
    """Return USB interface number for a ttyUSBx device via sysfs, or None."""
    dev_name = os.path.basename(device)
    link = f"/sys/class/tty/{dev_name}/device"
    if not os.path.exists(link):
        return None
    try:
        real = os.path.realpath(link)
        m = re.search(r':\d+\.(\d+)', real)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def _linux_vid(device: str) -> str:
    """Walk sysfs upward from ttyUSBx to find idVendor."""
    dev_name = os.path.basename(device)
    link = f"/sys/class/tty/{dev_name}/device"
    if not os.path.exists(link):
        return ""
    try:
        path = os.path.realpath(link)
        for _ in range(10):
            path = os.path.dirname(path)
            vendor_file = os.path.join(path, "idVendor")
            if os.path.exists(vendor_file):
                return _sysfs_read(vendor_file).lower()
    except Exception:
        pass
    return ""


def _classify_linux(device: str):
    # Only apply the interface map to known modem vendors — a generic
    # USB-serial adapter also exposes interface 0 but is not a DM port.
    if _linux_vid(device) not in KNOWN_VIDS:
        return None
    iface = _linux_iface(device)
    if iface is not None:
        return QUECTEL_IFACE_MAP.get(iface)
    return None


# ── Windows helpers ───────────────────────────────────────────────────────────

def _win_modem_registry() -> list[str]:
    """
    Fast registry read — returns COM ports registered under the Windows
    Modem device class (GUID 4D36E96D-…).  No subprocess needed.
    This is the equivalent of the PowerShell 'Win32_POTSModem' query but
    50× faster.  Returns list of strings like ['COM13'].
    """
    try:
        import winreg  # Windows-only
        ports: list[str] = []
        key_path = f"SYSTEM\\CurrentControlSet\\Control\\Class\\{_MODEM_CLASS_GUID}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as cls_key:
            idx = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(cls_key, idx)
                    if sub_name.lower() == "properties":
                        idx += 1
                        continue
                    with winreg.OpenKey(cls_key, sub_name) as sub:
                        try:
                            port, _ = winreg.QueryValueEx(sub, "AttachedTo")
                            if isinstance(port, str) and re.match(r'COM\d+', port, re.I):
                                ports.append(port.upper())
                        except OSError:
                            pass
                    idx += 1
                except OSError:
                    break
        return ports
    except Exception:
        return []


def _win_serialcomm_all() -> list[str]:
    """
    Read HARDWARE\\DEVICEMAP\\SERIALCOMM — Windows updates this at runtime
    with every physical COM port that is currently present, regardless of
    whether the device driver class is 'Ports' or 'Modems'.  This is the
    authoritative source of active ports and avoids returning stale entries
    from historical device-class registry keys.
    """
    try:
        import winreg
        ports = []
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM"
        ) as key:
            i = 0
            while True:
                try:
                    _, value, _ = winreg.EnumValue(key, i)
                    if re.match(r'COM\d+', str(value), re.I):
                        ports.append(str(value).upper())
                    i += 1
                except OSError:
                    break
        return sorted(ports, key=lambda p: int(re.search(r'\d+', p).group()))
    except Exception:
        return []


def _win_modem_ports_active() -> list[str]:
    """
    Return only CURRENTLY CONNECTED modem-class COM ports.
    Cross-references the Modem class registry (which keeps historic entries)
    with HARDWARE\DEVICEMAP\SERIALCOMM (only live ports).
    """
    active = set(_win_serialcomm_all())
    return [p for p in _win_modem_registry() if p in active]


def _classify_windows(port_info) -> str | None:
    desc = (port_info.description  or "").lower()
    hwid = (port_info.hwid         or "").lower()
    mfg  = (port_info.manufacturer or "").lower()

    # Priority 1 — VID + interface number from hwid (most precise)
    vid_m = re.search(r'vid[_:]+([0-9a-f]{4})', hwid)
    if vid_m and vid_m.group(1) in KNOWN_VIDS:
        iface_m = re.search(r'&mi[_:]0*(\d+)', hwid)
        if iface_m:
            return QUECTEL_IFACE_MAP.get(int(iface_m.group(1)))

    # Priority 2 — keyword matching (specific → generic order)
    for category, kws in KEYWORDS_WIN:
        if any(k in desc or k in mfg for k in kws):
            return category

    return None


# ── Public API ────────────────────────────────────────────────────────────────

class PortDetector(QThread):
    ports_updated    = pyqtSignal(dict)   # {'at': 'COM13', ...}
    all_ports_signal = pyqtSignal(list)   # all available serial devices

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True

    def run(self):
        while self._running:
            self.ports_updated.emit(self._scan())
            self.all_ports_signal.emit(self._all())
            self.msleep(2000)

    def stop(self):
        self._running = False
        self.wait(3000)

    @staticmethod
    def _all() -> list:
        if platform.system() == "Linux":
            try:
                return sorted(
                    f"/dev/{f}" for f in os.listdir("/dev")
                    if f.startswith("ttyUSB") or f.startswith("ttyACM")
                )
            except Exception:
                return []
        # Windows: use SERIALCOMM hardware map — includes Ports AND Modems class
        ports = _win_serialcomm_all()
        if not ports:
            # Fallback to pyserial if registry unreadable
            ports = sorted(p.device.upper() for p in serial.tools.list_ports.comports())
        return ports

    @staticmethod
    def _scan() -> dict:
        result = {"at": None, "serial": None, "nmea": None, "dm": None}

        if platform.system() == "Linux":
            try:
                devices = sorted(
                    f"/dev/{f}" for f in os.listdir("/dev")
                    if f.startswith("ttyUSB") or f.startswith("ttyACM")
                )
            except Exception:
                return result
            for dev in devices:
                cat = _classify_linux(dev)
                if cat and result[cat] is None:
                    result[cat] = dev
        else:
            # ── Windows ──────────────────────────────────────────
            # Step 1: AT port from Modem device class, filtered to ACTIVE only
            #         (_win_modem_ports_active cross-checks SERIALCOMM hardware map
            #          so disconnected/historic modems are never returned)
            modem_ports = _win_modem_ports_active()
            if modem_ports:
                result["at"] = modem_ports[0]

            # Step 2: remaining ports classified by description
            for p in serial.tools.list_ports.comports():
                cat = _classify_windows(p)
                if cat is None:
                    continue
                if cat == "at" and result["at"]:
                    continue   # already have AT from modem class
                if result.get(cat) is None:
                    result[cat] = p.device

        return result
