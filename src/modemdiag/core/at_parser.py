"""
core/at_parser.py
Classifies individual lines from modem serial output.
"""
from enum import Enum, auto


class LineType(Enum):
    OK        = auto()
    ERROR     = auto()
    CME_ERROR = auto()
    CMS_ERROR = auto()
    URC       = auto()
    DATA      = auto()


# Prefixes that identify Unsolicited Result Codes (URCs)
_URC_PREFIXES = (
    "+CREG:", "+CEREG:", "+CGREG:", "+C5GREG:",
    "+CIEV:", "+CRING:", "+CLIP:", "+CLCC:",
    "+CMT:", "+CDS:", "+CMTI:", "+CUSD:", "+CGEV:",
    "+QIND:", "+QIURC:", "+QSSLURC:",
    "+CPIN:",                        # SIM state change URC
    "NO CARRIER", "RING", "BUSY",
)


def classify(line: str) -> LineType:
    """Return the semantic type of a single trimmed modem output line."""
    s = line.strip()
    if s == "OK":
        return LineType.OK
    if s == "ERROR":
        return LineType.ERROR
    if s.startswith("+CME ERROR:"):
        return LineType.CME_ERROR
    if s.startswith("+CMS ERROR:"):
        return LineType.CMS_ERROR
    for prefix in _URC_PREFIXES:
        if s.startswith(prefix):
            return LineType.URC
    return LineType.DATA


def split_lines(raw: str) -> list[tuple[str, LineType]]:
    """
    Split raw serial bytes into (stripped_line, LineType) pairs,
    skipping blank lines.
    """
    lines  = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [(s, classify(s)) for line in lines if (s := line.strip())]
