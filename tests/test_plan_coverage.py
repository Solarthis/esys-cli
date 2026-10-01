from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from fixtures import setup, part_xml
from esys_cli.core import CliError, create_plan


class PlanCoverageTests(unittest.TestCase):
    def complete_fixture(self, root):
        _, data = setup(root)
        for name, version in [('ist.xml', '001_000_000'), ('soll.xml', '002_000_000')]:
            tree = ET.parse(root / name)
            parent = next(node for node in tree.iter() if node.tag.endswith('standardSVK'))
            for pc, ident in [('BTLD', '00000011'), ('CAFD', '00000022')]:
                parent.append(ET.fromstring('<partIdentification>' + part_xml(pc, ident, version) + '</partIdentification>'))
            tree.write(root / name)
        tree = ET.parse(root / 'tal.xml')
        for op, pc, ident in [('blFlash', 'BTLD', '00000011'), ('cdDeploy', 'CAFD', '00000022')]:
            tree.getroot().append(ET.fromstring(f'<talLine baseVariant="DSC" diagAddress="29"><{op}><sgbmid>{part_xml(pc, ident, "002_000_000")}</sgbmid></{op}></talLine>'))
            folder = data / 'psdzdata/swe' / pc.lower()
            folder.mkdir()
            for extension in (['caf'] if pc == 'CAFD' else ['bin', 'xml', 'bsw']):
                (folder / f'{pc.lower()}_{ident.lower()}.{extension}.002_000_000').write_bytes(b'synthetic test content')
        tree.write(root / 'tal.xml')
        return data

    def plan(self, root, data):
        return create_plan(*(root / name for name in ['profile.json', 'fa.xml', 'ist.xml', 'soll.xml', 'tal.xml']), ['29'], data)

    def test_every_changed_target_requires_its_operation(self):
        for omitted in ['blFlash', 'swDeploy', 'cdDeploy']:
            with self.subTest(omitted=omitted), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                data = self.complete_fixture(root)
                self.assertEqual(len(self.plan(root, data)['payloads']), 7)
                tree = ET.parse(root / 'tal.xml')
                for line in list(tree.getroot()):
                    if line.find(omitted) is not None:
                        tree.getroot().remove(line)
                tree.write(root / 'tal.xml')
                with self.assertRaisesRegex(CliError, 'cover'):
                    self.plan(root, data)

    def test_operation_cannot_borrow_a_sibling_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = self.complete_fixture(root)
            tree = ET.parse(root / 'tal.xml')
            lines = tree.getroot().findall('talLine')
            software = lines[0][0]
            bootloader = lines[1][0]
            software.tag, bootloader.tag = bootloader.tag, software.tag
            lines[0].append(bootloader)
            tree.getroot().remove(lines[1])
            tree.write(root / 'tal.xml')
            with self.assertRaises(CliError):
                self.plan(root, data)

    def test_removed_only_component_is_not_a_supported_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = self.complete_fixture(root)
            tree = ET.parse(root / 'ist.xml')
            parent = next(node for node in tree.iter() if node.tag.endswith('standardSVK'))
            parent.append(ET.fromstring('<partIdentification>' + part_xml('FLSL', '00000033', '001_000_000') + '</partIdentification>'))
            tree.write(root / 'ist.xml')
            with self.assertRaises(CliError):
                self.plan(root, data)
