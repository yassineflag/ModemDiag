"""Check emitted wire syntax against the protocol cases recorded in the audit."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import unittest

import yaml
from PyQt6.QtWidgets import QApplication, QComboBox
from modemdiag.ui.command_panel import _ParamBar, COMMANDS_DIR

APP = QApplication.instance() or QApplication([])


class CommandDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.bar = _ParamBar()
        self.addCleanup(self.bar.deleteLater)

    def command(self, library, name):
        data = yaml.safe_load((Path(COMMANDS_DIR) / (library + ".yaml")).read_text())
        return next(c for cat in data["categories"] for c in cat["commands"] if c["name"] == name)

    def assemble(self, library, name, **values):
        command = self.command(library, name)
        self.bar.load(name, command["command"], command.get("params", []),
                      command.get("get", ""), command.get("caps", ""))
        for key, value in values.items():
            field = self.bar._fields[key]
            if isinstance(field, QComboBox):
                field.setCurrentText(value)
            else:
                field.setText(value)
        return self.bar._assemble()

    def test_pdp_type_labels_are_not_transmitted_and_strings_are_quoted(self):
        for selected, wire in (("IP=IPv4", "IP"), ("IPV6=IPv6", "IPV6"),
                               ("IPV4V6=dual stack", "IPV4V6")):
            with self.subTest(selected=selected):
                self.assertEqual(self.assemble("3gpp", "AT+CGDCONT", type=selected, apn="test.apn"),
                                 f'AT+CGDCONT=1,"{wire}","test.apn"')

    def test_authentication_preserves_equals_and_empty_string_values(self):
        self.assertEqual(self.assemble("3gpp", "AT+CGAUTH", type="1=PAP", user="test", **{"pass": "a=b"}),
                         'AT+CGAUTH=1,1,"test","a=b"')
        self.assertEqual(self.assemble("3gpp", "AT+CGAUTH"), 'AT+CGAUTH=1,0,"",""')

    def test_storage_and_clock_strings_are_quoted(self):
        self.assertEqual(self.assemble("3gpp", "AT+CPMS"), 'AT+CPMS="SM","SM","SM"')
        self.assertEqual(self.assemble("3gpp", "AT+CCLK", time="26/11/04,12:00:00+04"),
                         'AT+CCLK="26/11/04,12:00:00+04"')

    def test_power_saving_timers_are_transmitted_as_strings(self):
        self.assertEqual(self.assemble("3gpp", "AT+CPSMS"),
                         'AT+CPSMS=1,"00100001","00100001","00000100","00001000"')
        self.assertEqual(self.assemble("3gpp", "AT+CEDRXS"), 'AT+CEDRXS=2,4,"0010"')

    def test_operator_is_quoted_and_access_technology_uses_correct_value(self):
        self.assertEqual(self.assemble("3gpp", "AT+COPS", mode="1=manual", oper="00101", AcT="9=NB-IoT"),
                         'AT+COPS=1,2,"00101",9')

    def test_quectel_urc_port_has_exactly_one_pair_of_quotes(self):
        for port in ("usbat", "usbmodem", "uart1", "uart2", "all"):
            with self.subTest(port=port):
                self.assertEqual(self.assemble("quectel", "AT+QURCCFG Port", port=port),
                                 f'AT+QURCCFG="urcport","{port}"')

    def test_quectel_ping_timeout_precedes_request_count(self):
        self.assertEqual(self.assemble("quectel", "AT+QPING", host="192.0.2.1", timeout="8", count="3"),
                         'AT+QPING=1,"192.0.2.1",8,3')

    def test_quectel_legacy_masks_and_scan_sequence_have_family_specific_syntax(self):
        self.assertEqual(self.assemble("quectel", "AT+QCFG band"), 'AT+QCFG="band",0,0,0,1')
        self.assertEqual(self.assemble("quectel", "AT+QCFG nwscanseq"), 'AT+QCFG="nwscanseq",00')
        self.assertEqual(self.assemble("quectel", "AT+QCFG iotopmode", mode="2=LTE-M and NB-IoT"),
                         'AT+QCFG="iotopmode",2,1')

    def test_quectel_file_operations_use_read_only_mode_and_returned_handle(self):
        self.assertEqual(self.assemble("quectel", "AT+QFOPEN", file="test.txt"), 'AT+QFOPEN="test.txt",2')
        self.assertEqual(self.assemble("quectel", "AT+QFREAD", handle="4", length="16"), 'AT+QFREAD=4,16')
        self.assertEqual(self.assemble("quectel", "AT+QFCLOSE", handle="4"), 'AT+QFCLOSE=4')

    def test_new_diagnostic_queries_have_no_configuration_side_effect_template(self):
        self.assertEqual(self.command("3gpp", "AT")["command"], "AT")
        self.assertEqual(self.command("3gpp", "AT+CGSN=2")["command"], "AT+CGSN=2")
        self.assertEqual(self.assemble("3gpp", "AT+CGCONTRDP", cid="2"), "AT+CGCONTRDP=2")

    def test_existing_vendor_shortcuts_are_kept_for_compatibility(self):
        expected = {
            "fibocom": {"AT+GTSDK", "AT+GTHWVER", "AT+GTBANDSEL?", "AT+GTNWSCAN",
                        "AT+GTNRINFO", "AT+GTRESET", "AT+GTUSBMODE=0"},
            "sierra_wireless": {"AT!VER", "AT!LEAVECND", "AT!LTECA?", "AT!NR5GWWAN?",
                                "AT!CELLOCK?", "AT!ROAM?", "AT!PLMNMODE?", "AT!GPSCONFIG?"},
            "telit": {"AT#IMEILOCK?", "AT#LTEBND?", "AT#NR5GBND?", "AT#CCED?",
                      "AT#NR5GINFO", "AT#OTADELTA=?", "AT#PGPS", "AT#LSCFG?"},
        }
        for library, forms in expected.items():
            data = yaml.safe_load((Path(COMMANDS_DIR) / (library + ".yaml")).read_text())
            available = {entry["command"] for category in data["categories"]
                         for entry in category["commands"]}
            self.assertTrue(forms <= available, (library, forms - available))

    def test_corrected_fibocom_parameter_shapes_and_labels(self):
        self.assertEqual(self.assemble('fibocom','AT+GTPREDNSCFG'),
                         'AT+GTPREDNSCFG=1,"8.8.8.8","8.8.4.4"')
        self.assertEqual(self.assemble('fibocom','AT+MGAUTH',type='1=PAP',user='alice',pwd='a=b'),
                         'AT+MGAUTH=1,1,"alice","a=b"')
        self.assertEqual(self.assemble('fibocom','AT+WRIM'), 'AT+WRIM=1,120')
        self.assertEqual(self.assemble('fibocom','AT+MTSM'), 'AT+MTSM=1')
        self.assertEqual(self.assemble('fibocom','AT+MTSM',report='3=Outside thresholds',rate='10',low='-10',high='50'),
                         'AT+MTSM=3,10,-10,50')
        self.assertIn('0=PS mode 2',self.command('fibocom','AT+CEMODE')['params'][0]['options'])

    def test_new_vendor_wire_syntax_and_profile_ids_are_valid(self):
        self.assertEqual(self.assemble('simcom','AT+CNCFG',apn='test.apn'), 'AT+CNCFG=0,1,"test.apn"')
        self.assertEqual(self.assemble('simcom','AT+SMPUB'), 'AT+SMPUB="modemdiag/test",5,0,0')
        for path in Path(COMMANDS_DIR).glob('*.yaml'):
            library = yaml.safe_load(path.read_text())
            profiles = set(library.get('profiles',{}))
            for category in library['categories']:
                for command in category['commands']:
                    self.assertTrue(set(command.get('profiles',[])) <= profiles, (path,command['name']))


if __name__ == "__main__":
    unittest.main()
