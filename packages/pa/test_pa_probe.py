"""Focused tests for inferred PA framing and metadata compatibility."""

from pathlib import Path
import struct
import unittest
import xml.etree.ElementTree as ET

from pa_probe import BinaryReader, metadata_root, records


def fixture():
    return ET.fromstring("""
    <archive version="6.2">
      <system><base_time>1000000</base_time></system>
      <preset><object><name>volume</name><id>22</id>
        <counter is_key="1" ctype="8192">
          <name>instance_name</name><id>1</id><base_id>0</base_id><size>8</size>
        </counter>
        <counter is_key="0" ctype="8">
          <name>total_ops</name><id>2</id><base_id>0</base_id><size>8</size>
          <period>60000</period>
        </counter>
        <counter is_key="0" ctype="8">
          <name>read_ops</name><id>3</id><base_id>0</base_id><size>8</size>
          <period>300000</period>
        </counter>
      </object></preset>
    </archive>
    """)


def block(timestamp, payload, period=60000):
    return struct.pack("<QIIQ", timestamp, len(payload), 22, period) + payload


class RecordTests(unittest.TestCase):
    def test_arrays_are_opt_in_and_labels_must_match_width(self):
        root = fixture()
        counter = root.find("preset/object/counter[name='total_ops']")
        counter.find("size").text = "16"
        ET.SubElement(counter, "x_name").text = "total"
        ET.SubElement(counter, "x_name").text = "success"
        data = block(1, b"vol1\0\0\0\0" + struct.pack("<QQ", 120, 119))
        self.assertIsNone(next(records(root, data))["raw"]["total_ops"])
        row = next(records(root, data, array_counters={("volume", "total_ops")}))
        self.assertEqual(row["raw"]["total_ops"], {"total": 120, "success": 119})
        counter.findall("x_name")[1].text = "total"
        with self.assertRaisesRegex(ValueError, "Unsupported array layout"):
            list(records(root, data, array_counters={("volume", "total_ops")}))

    def test_instance_stride_period_filter_and_base_time(self):
        payload = b"vol1\0\0\0\0" + struct.pack("<Q", 120)
        payload += b"vol2\0\0\0\0" + struct.pack("<Q", 240)
        rows = list(records(fixture(), block(60000, payload), {"volume"}))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["timestamp_ms"], 1060000)
        self.assertEqual(rows[1]["raw"], {"instance_name": "vol2", "total_ops": 240})

    def test_other_period_uses_its_own_counter(self):
        data = block(300000, b"vol1\0\0\0\0" + struct.pack("<Q", 50), 300000)
        row, = list(records(fixture(), data, {"volume"}))
        self.assertEqual(row["raw"], {"instance_name": "vol1", "read_ops": 50})

    def test_schema_mismatch_rejected_even_for_untargeted_object(self):
        with self.assertRaisesRegex(ValueError, "Schema mismatch"):
            list(records(fixture(), block(1, bytes(17)), {"system"}))

    def test_truncated_header_and_payload_rejected(self):
        for data in [bytes(23), block(1, bytes(16))[:-1]]:
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                list(records(fixture(), data))

    def test_xml_trailer_is_not_mistaken_for_xml(self):
        raw = ET.tostring(fixture()) + b"\x00\xff\x80"
        self.assertEqual(metadata_root(raw).get("version"), "6.2")


class BinaryMetadataTests(unittest.TestCase):
    def test_string_length_and_terminator(self):
        reader = BinaryReader(b"\x03\x00abc\x00\x00\x00\x00")
        self.assertEqual(reader.text(), "abc")
        self.assertEqual(reader.text(), "")
        self.assertEqual(reader.pos, 9)
        with self.assertRaises(ValueError):
            BinaryReader(b"\x01\x00a!").text()

    def test_unknown_binary_version_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported metadata version"):
            metadata_root(b"\x01" + struct.pack("<II", 7, 0))

    def test_real_version_fixtures_when_available(self):
        cache = Path(__file__).parent / ".pa_probe_cache"
        paths = list(cache.glob("*/kernel_opm.meta"))
        if not paths:
            self.skipTest("Local customer fixtures are intentionally not bundled")
        versions = set()
        for path in paths:
            root = metadata_root(path.read_bytes())
            versions.add(root.get("version"))
            self.assertTrue(root.findtext("system/hostname"))
            self.assertTrue(root.findall("preset/object"))
            if root.get("version") == "6.3":
                with self.assertRaises(ValueError):
                    metadata_root(path.read_bytes() + b"\x00")
            data = path.with_suffix(".bin").read_bytes()
            rows = list(records(root, data, {"system"}))
            self.assertTrue(rows)
        self.assertEqual(versions, {"6.2", "6.3"})


if __name__ == "__main__":
    unittest.main()
