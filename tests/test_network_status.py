import unittest

from modemdiag.core.network_status import (
    NOT_REPORTED, cell_steps, overview_steps, parse_snapshot,
)


def result(command, response, success=True):
    return {"command": command, "response": response, "success": success}


class NetworkStatusTests(unittest.TestCase):
    def test_standard_snapshot_keeps_registration_domains_separate(self):
        snapshot = parse_snapshot([
            result("ATI", "Manufacturer: Fibocom\nFM160\nOK"),
            result("AT+COPS?", '+COPS: 0,0,"Example, Mobile",7\nOK'),
            result("AT+CPIN?", "+CPIN: READY\nOK"),
            result("AT+CSQ", "+CSQ: 23,99\nOK"),
            result("AT+CESQ", "+CESQ: 99,99,255,255,20,60\nOK"),
            result("AT+CREG?", "+CREG: 0,0\nOK"),
            result("AT+CGREG?", "+CGREG: 0,0\nOK"),
            result("AT+CEREG?", '+CEREG: 2,5,"1234","00123456",7\nOK'),
            result("AT+C5GREG?", "ERROR", False),
            result("AT+CGATT?", "+CGATT: 1\nOK"),
            result("AT+CIREG?", "+CIREG: 2,1,1\nOK"),
        ])
        self.assertEqual(snapshot.vendor, "Fibocom")
        self.assertEqual(snapshot.operator, "Example, Mobile")
        self.assertEqual(snapshot.technology, "LTE")
        self.assertEqual(snapshot.sim, "READY")
        self.assertEqual(snapshot.attached, "Attached")
        self.assertEqual(snapshot.registrations["IMS"], "Registered")
        self.assertEqual(snapshot.registrations["CS"], "Not registered")
        self.assertEqual(snapshot.registrations["PS"], "Not registered")
        self.assertEqual(snapshot.registrations["EPS / LTE"], "Registered · roaming")
        self.assertEqual(snapshot.registrations["5GS"], NOT_REPORTED)
        self.assertEqual(snapshot.signals["RSSI"].value, -67)
        self.assertEqual(snapshot.signals["RSRP"].value, -80.5)
        self.assertEqual(snapshot.signals["RSRQ"].value, -9.75)
        self.assertEqual(snapshot.cells[0].cell_id, "00123456")
        self.assertEqual(snapshot.cells[0].area, "1234")
        self.assertEqual(snapshot.unavailable, ["AT+C5GREG?"])
        self.assertEqual(cell_steps(snapshot), [])  # FM160 encodings are not assumed.

    def test_numeric_operator_codes_keep_leading_zeros_and_are_not_invented_names(self):
        snapshot = parse_snapshot([result("AT+COPS?", '+COPS: 0,2,"00101",9\nOK')])
        self.assertEqual(snapshot.operator, "PLMN 00101")
        self.assertEqual(snapshot.technology, "NB-IoT")

    def test_unavailable_and_malformed_responses_never_become_zero_signal_or_false_registration(self):
        for response in ("ERROR", "TIMEOUT", "+CSQ: 99,99\n+CESQ: 99,99,255,255,255,255\nOK",
                         "+CSQ: garbage\n+CESQ: 1,2\nOK", "+CSQ: -1,0\n+CESQ: 1,2,3,4,100,200\nOK"):
            with self.subTest(response=response):
                snapshot = parse_snapshot([result("AT+CSQ", response), result("AT+CESQ", response),
                                           result("AT+CIREG?", "ERROR", False)])
                self.assertIsNone(snapshot.signals["RSSI"].value)
                self.assertIsNone(snapshot.signals["RSRP"].value)
                self.assertIsNone(snapshot.signals["RSRQ"].value)
                self.assertEqual(snapshot.registrations["IMS"], NOT_REPORTED)

    def test_signal_limit_codes_are_displayed_as_bounds(self):
        for code, expected in ((0, "≤ -113 dBm"), (31, "≥ -51 dBm")):
            self.assertEqual(parse_snapshot([result("AT+CSQ", f"+CSQ: {code},99")]).signals["RSSI"].text, expected)
        for quality, power, expected_quality, expected_power in (
                (0, 0, "< -19.5 dB", "< -140 dBm"), (34, 97, "≥ -3 dB", "≥ -44 dBm")):
            snapshot = parse_snapshot([result("AT+CESQ", f"+CESQ: 99,99,255,255,{quality},{power}")])
            self.assertEqual(snapshot.signals["RSRQ"].text, expected_quality)
            self.assertEqual(snapshot.signals["RSRP"].text, expected_power)

    def test_registration_urcs_and_deregistered_old_cells_do_not_create_serving_cells(self):
        snapshot = parse_snapshot([
            result("AT+CEREG?", '+CEREG: 5,"1234","1234567",7\n+CEREG: 2,0,"1234","1234567",7\nOK'),
            result("AT+CIREG?", "+CIREG: 1\nOK"),
        ])
        self.assertEqual(snapshot.registrations["EPS / LTE"], "Not registered")
        self.assertEqual(snapshot.registrations["IMS"], NOT_REPORTED)
        self.assertEqual(snapshot.cells, [])

    def test_quectel_lte_nsa_and_neighbour_cell_layouts(self):
        snapshot = parse_snapshot([
            result("ATI", "Quectel\nRM520N-GL\nOK"),
            result('AT+QENG="servingcell"',
                   '+QENG: "servingcell","NOCONN"\n'
                   '+QENG: "LTE","FDD",001,01,1234567,123,1650,3,5,5,1234,-91,-10,-67,16,9,230,-\n'
                   '+QENG: "NR5G-NSA",001,01,747,-71,13,-11,627264,78,12,1\nOK'),
            result('AT+QENG="neighbourcell"',
                   '+QENG: "neighbourcell intra","LTE",1650,124,-12,-99,-70,0,37,7,16,6,44\n'
                   '+QENG: "neighbourcell inter","LTE",2850,-,-,-,-,-,0,0,30,6\nOK'),
            result("AT+CEREG?", '+CEREG: 2,1,"1234","1234567",7\nOK'),
        ])
        self.assertEqual(len(snapshot.cells), 3)  # duplicate registration and invalid PCI omitted
        self.assertEqual(snapshot.technology, "LTE + 5G NSA")
        lte, nr, neighbour = snapshot.cells
        self.assertEqual((lte.technology, lte.channel, lte.band, lte.area), ("LTE", "1650", "3", "1234"))
        self.assertEqual(lte.rsrp.value, -91)
        self.assertEqual(lte.sinr.value, 16)
        self.assertEqual((nr.technology, nr.pci, nr.band), ("5G NSA", "747", "78"))
        self.assertEqual(nr.rsrp.value, -71)
        self.assertEqual(neighbour.role, "Neighbour")
        self.assertEqual(neighbour.rsrp.value, -99)
        self.assertEqual(neighbour.rsrq.value, -12)
        self.assertEqual(neighbour.sinr.value, 0)
        self.assertEqual(neighbour.rssi.value, -70)
        self.assertEqual(lte.rssi.value, -67)
        self.assertEqual(len(cell_steps(snapshot)), 2)

    def test_quectel_sa_layout_and_missing_values(self):
        snapshot = parse_snapshot([result('AT+QENG="servingcell"',
            '+QENG: "servingcell","NOCONN","NR5G-SA","TDD",001,01,9013B004,299,690E0F,633984,78,12,-107,-13,2,1,-\nOK')])
        cell = snapshot.cells[0]
        self.assertEqual((cell.technology, cell.cell_id, cell.area, cell.channel),
                         ("5G SA", "9013B004", "690E0F", "633984"))
        self.assertEqual((cell.rsrp.value, cell.rsrq.value, cell.sinr.value), (-107, -13, 2))
        empty = parse_snapshot([result('AT+QENG="servingcell"', '+QENG: "servingcell","SEARCH"\nOK')])
        self.assertEqual(empty.cells, [])

    def test_fibocom_fm350_encoded_measurements_and_unknown_layout(self):
        snapshot = parse_snapshot([
            result("ATI", "Fibocom\nFM350-GL\nOK"),
            result("AT+GTCCINFO?", "+GTCCINFO:\n"
                   "1,4,001,01,4660,1234567,1650,123,3,20,32,60,60,20\n"
                   "2,4,001,01,4660,7654321,1650,124,20,48,48,15\n"
                   "1,9,001,01,4660,1234568,627264,747,78,100,73,80,80,90\n"
                   "1,4,unknown,layout\nOK"),
        ])
        self.assertEqual(len(snapshot.cells), 3)
        lte, neighbour, nr = snapshot.cells
        self.assertEqual(lte.rsrp.value, -80.5)  # encoded, never displayed as +60 dBm
        self.assertEqual(lte.rsrq.value, -9.75)
        self.assertEqual(lte.sinr.value, 15.75)
        self.assertEqual(neighbour.rsrp.value, -92.5)
        self.assertEqual(nr.technology, "5G NR")
        self.assertEqual(nr.rsrp.value, -76.5)
        self.assertEqual(nr.rsrq.value, 1.75)
        self.assertEqual(cell_steps(snapshot)[0]["command"], "AT+GTCCINFO?")

    def test_overview_queries_do_not_change_registration_reporting_or_operator_selection(self):
        commands = [step["command"] for step in overview_steps()]
        self.assertTrue(all("=" not in command for command in commands))
        self.assertNotIn("AT+COPS=?", commands)  # no slow operator scan
        self.assertIn("AT+CIREG?", commands)

    def test_data_contexts_merge_by_cid_and_separate_configuration_from_active_addresses(self):
        snapshot = parse_snapshot([
            result("AT+CGPADDR", '+CGPADDR: 2,"2001:db8::24"\n+CGPADDR: 1,"198.51.100.24","0.0.0.0"\n+CGPADDR: 3,"::"\nOK'),
            result("AT+CGACT?", "+CGACT: 1,1\n+CGACT: 2,0\nOK"),
            result("AT+CGDCONT?", '+CGDCONT: 2,"IPV6","ims.example","::"\n+CGDCONT: 1,"IPV4V6","internet.example","198.51.100.10"\n+CGDCONT: 3,"IP","","0.0.0.0"\nOK'),
            result("AT+CFUN?", "+CFUN: 4\nOK"),
        ])
        first, second, third = snapshot.contexts
        self.assertEqual((first.cid, first.apn, first.protocol, first.active), ("1", "internet.example", "IPV4V6", True))
        self.assertEqual(first.addresses, ["198.51.100.24"])
        self.assertFalse(second.active)
        self.assertEqual(second.addresses, ["2001:db8::24"])
        self.assertEqual(third.addresses, [])
        self.assertIsNone(third.active)
        self.assertEqual(snapshot.functionality, "Radio disabled")
        configured_only = parse_snapshot([result("AT+CGDCONT?", '+CGDCONT: 1,"IP","example","198.51.100.10"\nOK')])
        self.assertEqual(configured_only.contexts[0].addresses, [])

    def test_decimal_octet_ipv6_and_invalid_addresses(self):
        snapshot = parse_snapshot([result("AT+CGPADDR", '+CGPADDR: 1,"32.1.13.184.0.0.0.0.0.0.0.0.0.0.0.36","invalid"\nOK')])
        self.assertEqual(snapshot.contexts[0].addresses, ["2001:db8::24"])

    def test_vendor_radio_details_include_plmn_duplex_and_rrc_state(self):
        snapshot = parse_snapshot([result('AT+QENG="servingcell"',
            '+QENG: "servingcell","CONNECT","LTE","FDD",1,01,1234567,123,1650,3,5,5,1234,-91,-10,-67,16,9,230,-\nOK')])
        cell = snapshot.cells[0]
        self.assertEqual((cell.plmn, cell.duplex, cell.state), ("00101", "FDD", "RRC connected"))
        self.assertEqual(snapshot.plmn, "00101")


if __name__ == "__main__":
    unittest.main()
