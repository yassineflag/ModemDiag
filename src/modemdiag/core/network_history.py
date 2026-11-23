"""Bounded, session-local signal samples and observed network changes."""
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from modemdiag.core.network_status import NOT_REPORTED


def cell_key(cell):
    return cell.technology, cell.cell_id, cell.pci, cell.channel


def cell_name(cell):
    identifier = "PSC" if cell.technology == "UMTS" else "PCI"
    return f"{cell.technology} · {identifier} {cell.pci}" if cell.pci != "—" else f"{cell.technology} · cell {cell.cell_id}"


@dataclass
class SignalSample:
    at: datetime
    values: dict


@dataclass
class NetworkEvent:
    at: datetime
    message: str


class NetworkHistory:
    def __init__(self):
        self.samples = deque(maxlen=120)
        self.events = deque(maxlen=40)
        self.cell_changes = 0
        self._last_operator = None
        self._last_radio = None
        self._last_cells = None
        self._last_registrations = {}
        self._last_attachment = None
        self._last_contexts = {}

    def add(self, snapshot, at=None):
        at = at or datetime.now().astimezone()
        self.samples.append(SignalSample(at, {key: reading.value for key, reading in snapshot.signals.items()}))

        def event(message):
            self.events.appendleft(NetworkEvent(at, message))

        for attribute, key, label in (("operator", "_last_operator", "Operator"),
                                      ("technology", "_last_radio", "Radio")):
            value = getattr(snapshot, attribute)
            if value != NOT_REPORTED:
                previous = getattr(self, key)
                if value != previous:
                    event(f"{label}: {value}" if previous is None else f"{label}: {previous} → {value}")
                setattr(self, key, value)
        serving = {cell_key(cell): cell_name(cell) for cell in snapshot.cells if cell.role == "Serving"}
        if serving:
            if self._last_cells is not None and serving.keys() != self._last_cells.keys():
                self.cell_changes += 1
                event("Serving cells changed: " + ", ".join(serving.values()))
            elif self._last_cells is None:
                event("Serving cells observed: " + ", ".join(serving.values()))
            self._last_cells = serving
        # Missing data never implies a lost registration or a cell handover.
        for domain, value in snapshot.registrations.items():
            if value == NOT_REPORTED:
                continue
            previous = self._last_registrations.get(domain)
            if previous is not None and previous != value:
                event(f"{domain}: {previous} → {value}")
            self._last_registrations[domain] = value
        if snapshot.attached != NOT_REPORTED:
            if self._last_attachment is not None and snapshot.attached != self._last_attachment:
                event(f"Packet service: {self._last_attachment} → {snapshot.attached}")
            self._last_attachment = snapshot.attached
        for context in snapshot.contexts:
            if context.active is None:
                continue
            previous = self._last_contexts.get(context.cid)
            if previous is not None and previous != context.active:
                event(f"Data context {context.cid}: " + ("active" if context.active else "inactive"))
            self._last_contexts[context.cid] = context.active


def stronger_neighbour(snapshot):
    """Compare only the same radio and channel, using reported RSRP."""
    comparisons = []
    for serving in snapshot.cells:
        if serving.role != "Serving" or serving.rsrp.value is None or serving.channel == "—":
            continue
        for neighbour in snapshot.cells:
            if (neighbour.role == "Neighbour" and neighbour.rsrp.value is not None
                    and neighbour.technology == serving.technology and neighbour.channel == serving.channel):
                difference = neighbour.rsrp.value - serving.rsrp.value
                if difference >= 3:
                    comparisons.append((difference, neighbour))
    return max(comparisons, key=lambda pair: pair[0]) if comparisons else None
