"""Exercise counter conversion through a small, checksum-verified PA fixture."""

import contextlib
import gzip
import hashlib
import io
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import validate_pa


META = b"""<archive version="6.2"><system>
<hostname>fixture-node</hostname><base_time>1000000</base_time></system>
<preset><object><name>volume</name><id>22</id>
<counter is_key="1" ctype="8192"><name>instance_name</name><id>1</id>
<base_id>0</base_id><size>8</size></counter>
<counter is_key="0" ctype="8"><name>read_data</name><id>2</id>
<base_id>0</base_id><size>8</size><period>300000</period></counter>
<counter is_key="0" ctype="8"><name>write_ops</name><id>3</id>
<base_id>0</base_id><size>8</size><period>300000</period></counter>
<counter is_key="0" ctype="8"><name>write_latency</name><id>4</id>
<base_id>3</base_id><size>8</size><period>300000</period></counter>
<counter is_key="0" ctype="8"><name>wvblk_zombie_blks</name><id>5</id>
<base_id>0</base_id><size>8</size><period>300000</period></counter>
</object></preset></archive>"""


def fixture_tar(bad_checksum=False):
    data = bytearray()
    for timestamp, byte_count, ops, latency in [
        (0, 0, 100, 1000),
        (300000, 3 * 2**20, 100, 1000),
        (600000, 9 * 2**20, 105, 11000),
        (900000, 18 * 2**20, 120, 56000),
    ]:
        zombie = {0: 100, 300000: 0, 600000: 50, 900000: 25}[timestamp]
        payload = b"vol1\0\0\0\0" + struct.pack("<QQQQ", byte_count, ops, latency, zombie)
        data += struct.pack("<QIIQ", timestamp, len(payload), 22, 300000) + payload
    prefix = "mroot/etc/log/stats/ccma/kernel/0001.ccma"
    files = {prefix + ".meta": META, prefix + ".gz": gzip.compress(data)}
    checksum = "\n".join(
        f"{hashlib.md5(content).hexdigest()}  {Path(name).name}"
        for name, content in files.items()
    ).encode()
    files[prefix + ".md5"] = b"0" + checksum[1:] if bad_checksum else checksum
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return result.getvalue()


class CalculationTests(unittest.TestCase):
    def decode(self, bad_checksum=False):
        info = {
            "file": "fixture.7z", "node": "fixture-node", "cluster": "fixture",
            "requested_start_ms": 1000000, "requested_end_ms": 1900000,
        }
        with tempfile.TemporaryDirectory(prefix="pa-decoder-test-") as folder:
            source = Path(folder) / "fixture.7z"
            source.write_bytes(b"test input")
            with (
                patch.object(validate_pa, "headers", return_value=info),
                patch.object(validate_pa, "read_member", return_value=fixture_tar(bad_checksum)),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                return validate_pa.parse_archive(source, Path(folder) / "output")

    def test_rate_units_weighted_latency_and_zero_denominator(self):
        decoded = self.decode()
        metrics = {m["metric"]: m for m in decoded["metrics"]}
        self.assertAlmostEqual(metrics["read_mib_s"]["avg"], 0.02)
        self.assertAlmostEqual(metrics["read_mib_s"]["min"], 0.01)
        self.assertAlmostEqual(metrics["read_mib_s"]["max"], 0.03)
        self.assertAlmostEqual(metrics["write_iops"]["avg"], 20 / 900)
        self.assertAlmostEqual(metrics["write_latency_ms"]["avg"], 2.75)
        self.assertEqual(metrics["write_latency_ms"]["zero_base_intervals"], 1)
        self.assertEqual(metrics["write_latency_ms"]["valid_intervals"], 2)
        self.assertEqual(decoded["stats"]["verified_files"], 2)
        self.assertEqual(decoded["stats"]["record_blocks"], 4)

    def test_modified_checksum_rejected(self):
        with self.assertRaisesRegex(ValueError, "MD5 mismatch"):
            self.decode(bad_checksum=True)

    def test_zombie_is_an_absolute_gauge_not_a_counter_delta(self):
        decoded = self.decode()
        self.assertEqual([s["value"] for s in decoded["gauge_samples"]], [100, 0, 50, 25])
        self.assertFalse(any(m["metric"] == "wvblk_zombie_blks" for m in decoded["metrics"]))

    def test_source_timezone_is_not_assumed(self):
        header = b"""X-Netapp-asup-subject: PERFORMANCE ARCHIVE UPLOAD: 11/9/2026 11:30:00 to 11/9/2026 15:30:00
X-Netapp-asup-generated-on: Mon Nov 09 15:44:54 -0500 2026
X-Netapp-asup-hostname: fixture-node
X-Netapp-asup-cluster-name: fixture
X-Netapp-asup-os-version: fixture
X-Netapp-asup-model-name: fixture
X-Netapp-asup-serial-num: 123
"""
        with patch.object(validate_pa, "read_member", return_value=header):
            result = validate_pa.headers(Path("fixture.7z"))
        self.assertEqual(result["source_timezone"], "UTC-05:00")
        self.assertEqual(
            validate_pa.time_text(result["requested_start_ms"]),
            "2026-11-10T00:30:00+08:00",
        )


if __name__ == "__main__":
    unittest.main()
