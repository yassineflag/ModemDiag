"""Read-only dashboard queries and conservative AT response decoding.

Standard fields follow 3GPP TS 27.007. Cell extensions follow Quectel's
RG520N/RM5x0N manual and Fibocom's FM350 manual; other layouts stay in the log.
"""
import csv
from dataclasses import dataclass, field
import ipaddress
import re


NOT_REPORTED = "Not reported"
TECHNOLOGIES = {
    0: "GSM", 1: "GSM Compact", 2: "UMTS", 3: "EDGE", 4: "HSDPA",
    5: "HSUPA", 6: "HSPA", 7: "LTE", 8: "EC-GSM-IoT", 9: "NB-IoT",
    10: "LTE / 5G", 11: "5G NR", 12: "5G NG-RAN", 13: "LTE / 5G NSA",
}
REGISTRATION = {
    0: "Not registered", 1: "Registered · home", 2: "Searching",
    3: "Registration denied", 4: "Unknown", 5: "Registered · roaming",
    6: "SMS only · home", 7: "SMS only · roaming", 8: "Emergency only",
    9: "Registered · CSFB not preferred", 10: "Registered · roaming, CSFB not preferred",
    11: "Limited service",
}
REGISTRATION_QUERIES = {
    "AT+CREG?": "CS", "AT+CGREG?": "PS", "AT+CEREG?": "EPS / LTE",
    "AT+C5GREG?": "5GS", "AT+CIREG?": "IMS",
}


@dataclass
class Measurement:
    text: str = NOT_REPORTED
    value: float | None = None


@dataclass
class CellInfo:
    role: str
    technology: str = NOT_REPORTED
    cell_id: str = "—"
    area: str = "—"
    pci: str = "—"
    channel: str = "—"
    band: str = "—"
    rsrp: Measurement = field(default_factory=Measurement)
    rsrq: Measurement = field(default_factory=Measurement)
    sinr: Measurement = field(default_factory=Measurement)
    source: str = ""
    plmn: str = "—"
    state: str = "—"
    duplex: str = "—"
    rssi: Measurement = field(default_factory=Measurement)


@dataclass
class DataContext:
    cid: str
    protocol: str = "—"
    apn: str = "—"
    active: bool | None = None
    addresses: list[str] = field(default_factory=list)


@dataclass
class NetworkSnapshot:
    device: str = NOT_REPORTED
    vendor: str = ""
    operator: str = NOT_REPORTED
    technology: str = NOT_REPORTED
    sim: str = NOT_REPORTED
    attached: str = NOT_REPORTED
    registrations: dict = field(default_factory=lambda: {
        key: NOT_REPORTED for key in REGISTRATION_QUERIES.values()})
    signals: dict = field(default_factory=lambda: {
        key: Measurement() for key in ("RSSI", "RSRP", "RSRQ", "SINR")})
    cells: list[CellInfo] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    selection: str = NOT_REPORTED
    plmn: str = NOT_REPORTED
    functionality: str = NOT_REPORTED
    contexts: list[DataContext] = field(default_factory=list)


def _steps(commands):
    return [{"command": command, "description": description, "expect": prefix,
             "timeout_ms": 3000, "delay_ms": 150, "on_failure": "continue"}
            for command, description, prefix in commands]


def overview_steps():
    return _steps([
        ("ATI", "Modem identity", "OK"),
        ("AT+CPIN?", "SIM readiness", "+CPIN:"),
        ("AT+COPS?", "Operator and radio technology", "+COPS:"),
        ("AT+CSQ", "Signal strength", "+CSQ:"),
        ("AT+CESQ", "Extended signal quality", "+CESQ:"),
        ("AT+CREG?", "Circuit-switched registration", "+CREG:"),
        ("AT+CGREG?", "Packet-switched registration", "+CGREG:"),
        ("AT+CEREG?", "LTE registration", "+CEREG:"),
        ("AT+C5GREG?", "5G registration", "+C5GREG:"),
        ("AT+CGATT?", "Packet-service attachment", "+CGATT:"),
        ("AT+CIREG?", "IMS registration", "+CIREG:"),
        ("AT+CFUN?", "Radio functionality", "+CFUN:"),
        ("AT+CGDCONT?", "APN and data contexts", "+CGDCONT:"),
        ("AT+CGACT?", "Data context activation", "+CGACT:"),
        ("AT+CGPADDR", "Assigned IP addresses", "+CGPADDR:"),
    ])


def cell_steps(snapshot):
    if snapshot.vendor == "Quectel":
        return _steps([
            ('AT+QENG="servingcell"', "Serving cells", "+QENG:"),
            ('AT+QENG="neighbourcell"', "Neighbour cells", "+QENG:"),
        ])
    # GTCCINFO layouts and encodings vary; only decode the documented FM350 one.
    if snapshot.vendor == "Fibocom" and re.search(r"\bFM350[\w-]*", snapshot.device, re.I):
        return _steps([("AT+GTCCINFO?", "Serving and neighbour cells", "+GTCCINFO:")])
    return []


def _fields(text):
    return [value.strip() for value in next(csv.reader([text], skipinitialspace=True))]


def _integer(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _value(value):
    return value if value and value.upper() not in ("-", "--", "FFFF", "FFFFFFFF", "FFFFFFFFF") else "—"


def _plmn(mcc, mnc):
    return mcc.zfill(3) + mnc.zfill(2) if mcc.isdigit() and mnc.isdigit() else "—"


def _address(value):
    try:
        address = ipaddress.ip_address(value)
        return str(address) if not address.is_unspecified else None
    except ValueError:
        # Some modems encode an IPv6 address as sixteen decimal octets.
        octets = value.split(".")
        if len(octets) == 16 and all(part.isdigit() and 0 <= int(part) <= 255 for part in octets):
            address = ipaddress.IPv6Address(bytes(int(part) for part in octets))
            return str(address) if not address.is_unspecified else None
        return None


def _direct(value, unit):
    try:
        number = float(value)
    except (ValueError, TypeError):
        return Measurement()
    if not -200 <= number <= 100:
        return Measurement()
    return Measurement(f"{number:g} {unit}", number)


def _level(value, maximum, lower, width, unit, saturated=True):
    """Represent quantized measurements by their midpoint, with explicit limits."""
    code = _integer(value)
    if code is None or not 0 <= code <= maximum:
        return Measurement()
    if code == 0:
        return Measurement(f"< {lower:g} {unit}", lower)
    if code == maximum and saturated:
        limit = lower + (maximum - 1) * width
        return Measurement(f"≥ {limit:g} {unit}", limit)
    number = lower + (code - 0.5) * width
    return Measurement(f"≈ {number:g} {unit}", number)


def _quectel_cells(response):
    cells = []
    state = "—"
    for line in response.splitlines():
        if not line.strip().startswith("+QENG:"):
            continue
        values = _fields(line.split(":", 1)[1])
        if not values:
            continue
        kind = values[0]
        if kind.startswith("neighbourcell"):
            # LTE intra/inter layouts have RSRQ before RSRP. The shorter
            # inter-RAT layout reverses these fields, so handle it separately.
            if len(values) < 6 or values[1] != "LTE" or _integer(values[3]) is None:
                continue
            cell = CellInfo("Neighbour", "LTE", pci=_value(values[3]),
                            channel=_value(values[2]), source=line)
            quality, power = (values[4], values[5]) if kind != "neighbourcell" else (values[5], values[4])
            cell.rsrp, cell.rsrq = _direct(power, "dBm"), _direct(quality, "dB")
            if kind in ("neighbourcell intra", "neighbourcell inter") and len(values) >= 8:
                cell.rssi = _direct(values[6], "dBm")
                cell.sinr = _direct(values[7], "dB")
        else:
            if kind == "servingcell":
                state = {"NOCONN": "RRC idle", "CONNECT": "RRC connected", "LIMSRV": "Limited service",
                         "SEARCH": "Searching"}.get(values[1] if len(values) > 1 else "", "—")
                values = values[2:]  # remove record type and RRC state
            if not values:
                continue
            radio = values[0]
            if radio == "LTE" and len(values) >= 15:
                cell = CellInfo("Serving", "LTE", _value(values[4]), _value(values[10]),
                                _value(values[5]), _value(values[6]), _value(values[7]),
                                _direct(values[11], "dBm"), _direct(values[12], "dB"),
                                _direct(values[14], "dB"), line)
            elif radio == "NR5G-SA" and len(values) >= 15:
                cell = CellInfo("Serving", "5G SA", _value(values[4]), _value(values[6]),
                                _value(values[5]), _value(values[7]), _value(values[8]),
                                _direct(values[10], "dBm"), _direct(values[11], "dB"),
                                _direct(values[12], "dB"), line)
            elif radio == "NR5G-NSA" and len(values) >= 10:
                cell = CellInfo("Serving", "5G NSA", pci=_value(values[3]),
                                channel=_value(values[7]), band=_value(values[8]),
                                rsrp=_direct(values[4], "dBm"), rsrq=_direct(values[6], "dB"),
                                sinr=_direct(values[5], "dB"), source=line)
            elif radio == "WCDMA" and len(values) >= 10:
                cell = CellInfo("Serving", "UMTS", _value(values[4]), _value(values[3]),
                                _value(values[6]), _value(values[5]), source=line)
            else:
                continue
            cell.state = state
            if radio in ("LTE", "NR5G-SA"):
                cell.duplex = values[1]
                cell.plmn = _plmn(values[2], values[3])
                if radio == "LTE":
                    cell.rssi = _direct(values[13], "dBm")
            elif radio in ("NR5G-NSA", "WCDMA"):
                cell.plmn = _plmn(values[1], values[2])
        cells.append(cell)
    return cells


def _fibocom_cells(response):
    cells = []
    for line in response.splitlines():
        data = line.split(":", 1)[1] if line.strip().startswith("+GTCCINFO:") else line
        values = _fields(data)
        if len(values) < 12 or values[0] not in ("1", "2"):
            continue
        serving = values[0] == "1"
        rat = values[1]
        if rat not in ("2", "4", "9"):
            continue
        if len(values) != (15 if rat == "2" else 14 if serving else 12):
            continue
        radio = {"2": "UMTS", "4": "LTE", "9": "5G NR"}[rat]
        cell = CellInfo("Serving" if serving else "Neighbour", radio,
                        _value(values[5]), _value(values[4]), _value(values[7]),
                        _value(values[6]), _value(values[8]) if serving else "—", source=line)
        cell.plmn = _plmn(values[2], values[3])
        if rat in ("4", "9"):
            cell.rsrp = _level(values[-2], 97 if rat == "4" else 126,
                               -140 if rat == "4" else -156, 1, "dBm")
            cell.rsrq = _level(values[-1], 34 if rat == "4" else 126,
                               -19.5 if rat == "4" else -43, 0.5, "dB", saturated=rat == "4")
            if serving:
                code = _integer(values[10])
                if rat == "9":
                    cell.sinr = _level(values[10], 127, -23, 0.5, "dB")
                elif code is not None and -100 <= code <= 100:
                    value = code / 2
                    text = ("≤ -50 dB" if code == -100 else "> 50 dB" if code == 100
                            else f"≈ {value - 0.25:g} dB")
                    cell.sinr = Measurement(text, value if abs(code) == 100 else value - 0.25)
        cells.append(cell)
    return cells


def parse_snapshot(results):
    """Build a fresh snapshot, so failed queries never retain older good values."""
    snapshot = NetworkSnapshot()
    registration_cells = []
    contexts = {}
    for result in results:
        command, response = result["command"], result["response"]
        if not result.get("success"):
            snapshot.unavailable.append(command)
            continue
        if command == "ATI":
            lines = [line.strip() for line in response.splitlines()
                     if line.strip() and line.strip().upper() not in ("ATI", "OK")]
            if lines:
                snapshot.device = " · ".join(lines)
                upper = snapshot.device.upper()
                snapshot.vendor = next((vendor for vendor in ("Quectel", "Fibocom", "SIMCom", "Telit")
                                        if vendor.upper() in upper), "")
            continue
        if command.startswith('AT+QENG='):
            snapshot.cells.extend(_quectel_cells(response))
            continue
        if command == "AT+GTCCINFO?":
            snapshot.cells.extend(_fibocom_cells(response))
            continue
        prefix = command.removeprefix("AT").rstrip("?") + ":"
        for line in response.splitlines():
            if not line.strip().startswith(prefix):
                continue
            try:
                values = _fields(line.split(":", 1)[1])
                if command == "AT+CPIN?" and values:
                    snapshot.sim = values[0] or NOT_REPORTED
                elif command == "AT+COPS?" and len(values) >= 3:
                    name = values[2]
                    snapshot.operator = (f"PLMN {name}" if values[1] == "2" and name else name) or NOT_REPORTED
                    snapshot.selection = {"0": "Automatic selection", "1": "Manual selection",
                                          "2": "Deregistered", "4": "Manual with automatic fallback"}.get(values[0], NOT_REPORTED)
                    if values[1] == "2" and name:
                        snapshot.plmn = name
                    if len(values) >= 4:
                        snapshot.technology = TECHNOLOGIES.get(_integer(values[3]), NOT_REPORTED)
                elif command == "AT+CGATT?" and values:
                    snapshot.attached = {"0": "Detached", "1": "Attached"}.get(values[0], NOT_REPORTED)
                elif command == "AT+CFUN?" and values:
                    snapshot.functionality = {"0": "Minimum functionality", "1": "Full functionality",
                                              "4": "Radio disabled"}.get(values[0], f"Mode {values[0]}")
                elif command in ("AT+CGDCONT?", "AT+CGACT?", "AT+CGPADDR") and len(values) >= 2:
                    if not values[0].isdigit():
                        continue
                    context = contexts.setdefault(values[0], DataContext(values[0]))
                    if command == "AT+CGDCONT?" and len(values) >= 3:
                        context.protocol = _value(values[1])
                        context.apn = _value(values[2])
                        # CGDCONT's configured address is not proof of an assignment.
                    elif command == "AT+CGACT?":
                        context.active = {"0": False, "1": True}.get(values[1])
                    elif command == "AT+CGPADDR":
                        context.addresses = list(dict.fromkeys(address for value in values[1:]
                                                              if (address := _address(value))))
                elif command == "AT+CSQ" and values:
                    code = _integer(values[0])
                    if code is not None and 0 <= code <= 31:
                        number = -113 + 2 * code
                        sign = "≤ " if code == 0 else "≥ " if code == 31 else ""
                        snapshot.signals["RSSI"] = Measurement(f"{sign}{number} dBm", number)
                elif command == "AT+CESQ" and len(values) >= 6:
                    snapshot.signals["RSRQ"] = _level(values[4], 34, -19.5, 0.5, "dB")
                    snapshot.signals["RSRP"] = _level(values[5], 97, -140, 1, "dBm")
                elif command in REGISTRATION_QUERIES and len(values) >= 2:
                    # Read responses contain <n>,<stat>. Ignore URCs (whose
                    # second field is an area code) mixed into a query response.
                    if not re.match(r"^\s*\d+\s*,\s*\d+\s*(?:,|$)", line.split(":", 1)[1]):
                        continue
                    status = _integer(values[1])
                    key = REGISTRATION_QUERIES[command]
                    snapshot.registrations[key] = ({0: "Not registered", 1: "Registered"}.get(status, NOT_REPORTED)
                                                   if key == "IMS" else REGISTRATION.get(status, NOT_REPORTED))
                    if key != "IMS" and status in (1, 5, 6, 7, 9, 10) and len(values) >= 4:
                        radio = TECHNOLOGIES.get(_integer(values[4]), NOT_REPORTED) if len(values) >= 5 else {
                            "EPS / LTE": "LTE", "5GS": "5G NR"}.get(key, NOT_REPORTED)
                        if snapshot.technology == NOT_REPORTED and radio != NOT_REPORTED:
                            snapshot.technology = radio
                        if _value(values[3].upper()) != "—":
                            registration_cells.append(CellInfo("Serving", radio, values[3],
                                                               _value(values[2]), source=line))
            except (ValueError, IndexError, csv.Error):
                continue
    radios = list(dict.fromkeys(cell.technology for cell in snapshot.cells if cell.role == "Serving"))
    snapshot.contexts = sorted(contexts.values(), key=lambda context: int(context.cid))
    if snapshot.plmn == NOT_REPORTED:
        snapshot.plmn = next((cell.plmn for cell in snapshot.cells
                              if cell.role == "Serving" and cell.plmn != "—"), NOT_REPORTED)
    if radios:
        snapshot.technology = " + ".join(radios)
    # Vendor measurements refer to specific serving radios; keep CESQ labelled
    # separately in the overview, using a serving cell only when CESQ is absent.
    for cell in snapshot.cells:
        if cell.role == "Serving":
            for key, reading in (("RSRP", cell.rsrp), ("RSRQ", cell.rsrq), ("SINR", cell.sinr)):
                if snapshot.signals[key].value is None and reading.value is not None:
                    snapshot.signals[key] = reading
    def identity(cell):
        radio = "5G" if cell.technology.startswith("5G") else cell.technology
        return radio, cell.cell_id.upper()

    known = {identity(cell) for cell in snapshot.cells if cell.cell_id != "—"}
    for cell in registration_cells:
        key = identity(cell)
        if key not in known:
            known.add(key)
            snapshot.cells.append(cell)
    return snapshot
