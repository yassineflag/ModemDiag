from datetime import datetime, timedelta, timezone
import unittest

from modemdiag.core.network_status import NetworkSnapshot, CellInfo, Measurement, DataContext
from modemdiag.core.network_history import NetworkHistory, stronger_neighbour


class NetworkHistoryTests(unittest.TestCase):
    def setUp(self):
        self.history = NetworkHistory()
        self.at = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)

    def snapshot(self, pci="123", power=-95):
        snapshot = NetworkSnapshot(operator="Example", technology="LTE", attached="Attached")
        snapshot.signals["RSRP"] = Measurement(f"{power} dBm", power)
        snapshot.cells = [CellInfo("Serving", "LTE", "12345", pci=pci, channel="1650", rsrp=snapshot.signals["RSRP"])]
        snapshot.registrations["IMS"] = "Registered"
        return snapshot

    def test_cell_changes_are_stable_under_reordering_and_missing_observations(self):
        first = self.snapshot()
        first.cells.append(CellInfo("Serving", "5G NSA", pci="747", channel="627264"))
        self.history.add(first, self.at)
        reordered = self.snapshot()
        reordered.cells = list(reversed(first.cells))
        self.history.add(reordered, self.at + timedelta(seconds=10))
        self.history.add(NetworkSnapshot(), self.at + timedelta(seconds=20))
        self.history.add(reordered, self.at + timedelta(seconds=30))
        self.assertEqual(self.history.cell_changes, 0)
        changed = self.snapshot(pci="124")
        self.history.add(changed, self.at + timedelta(seconds=40))
        self.assertEqual(self.history.cell_changes, 1)
        self.assertIn("Serving cells changed", self.history.events[0].message)
        self.assertIsNone(self.history.samples[2].values["RSRP"])

    def test_registration_and_context_changes_are_recorded_without_inventing_loss(self):
        first = self.snapshot()
        first.contexts = [DataContext("1", active=False)]
        self.history.add(first, self.at)
        count = len(self.history.events)
        self.history.add(NetworkSnapshot(), self.at + timedelta(seconds=10))
        self.assertEqual(len(self.history.events), count)
        changed = self.snapshot()
        changed.registrations["IMS"] = "Not registered"
        changed.contexts = [DataContext("1", active=True)]
        self.history.add(changed, self.at + timedelta(seconds=20))
        messages = [event.message for event in self.history.events]
        self.assertIn("IMS: Registered → Not registered", messages)
        self.assertIn("Data context 1: active", messages)

    def test_history_and_events_are_bounded(self):
        for index in range(150):
            self.history.add(self.snapshot(pci=str(index)), self.at + timedelta(seconds=index * 10))
        self.assertEqual(len(self.history.samples), 120)
        self.assertEqual(len(self.history.events), 40)
        self.assertEqual(self.history.samples[0].at, self.at + timedelta(seconds=300))

    def test_neighbour_comparison_requires_matching_radio_and_channel(self):
        snapshot = self.snapshot()
        snapshot.cells.extend([
            CellInfo("Neighbour", "LTE", pci="201", channel="2850", rsrp=Measurement("-70 dBm", -70)),
            CellInfo("Neighbour", "5G NR", pci="747", channel="1650", rsrp=Measurement("-60 dBm", -60)),
        ])
        self.assertIsNone(stronger_neighbour(snapshot))
        snapshot.cells.append(CellInfo("Neighbour", "LTE", pci="124", channel="1650", rsrp=Measurement("-88 dBm", -88)))
        difference, neighbour = stronger_neighbour(snapshot)
        self.assertEqual((difference, neighbour.pci), (7, "124"))


if __name__ == "__main__":
    unittest.main()
