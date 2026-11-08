"""CIDR /9 planning regression tests: no network access or root required."""
from pathlib import Path
import importlib.util
import unittest

PATH = Path(__file__).resolve().parents[2] / "backend" / "discovery" / "ranges.py"
spec = importlib.util.spec_from_file_location("discovery_ranges_staged", PATH)
ranges = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ranges)


class CIDR9Tests(unittest.TestCase):
    def test_requested_public_cidr_is_supported_for_definition(self):
        x = ranges.plan_range("128.0.0.0/9")
        self.assertEqual(x["canonical_cidr"], "128.0.0.0/9")
        self.assertEqual(x["address_count"], 8_388_608)
        self.assertEqual(x["batch_count"], 32_768)
        self.assertEqual(x["last_address"], "128.127.255.255")
        self.assertFalse(x["rfc1918_scope"])
        self.assertEqual(x["execution_status"], "DEFINITION_ONLY_PUBLIC_OR_RESERVED")
        self.assertFalse(x["scan_started"])

    def test_private_9_is_plannable_but_not_implicitly_authorized(self):
        x = ranges.plan_range("10.0.0.0/9")
        self.assertTrue(x["rfc1918_scope"])
        self.assertEqual(x["execution_status"], "APPROVAL_REQUIRED")
        self.assertFalse(x["scan_started"])

    def test_8_too_broad(self):
        with self.assertRaises(ranges.RangeValidationError):
            ranges.plan_range("10.0.0.0/8")

    def test_normalizes_misaligned_cidr(self):
        self.assertEqual(ranges.plan_range("128.10.30.50/9")["canonical_cidr"], "128.0.0.0/9")

    def test_pagination_and_final_block_of_full_9(self):
        first = ranges.preview_batches("128.0.0.0/9", offset=0, limit=16)
        self.assertEqual(first["batches"][0], "128.0.0.0/24")
        self.assertEqual(first["batches"][-1], "128.0.15.0/24")
        self.assertEqual(first["next_offset"], 16)
        last = ranges.preview_batches("128.0.0.0/9", offset=32767, limit=64)
        self.assertEqual(last["batches"], ["128.127.255.0/24"])
        self.assertIsNone(last["next_offset"])

    def test_hosts_and_subnets_are_not_enumerated(self):
        self.assertFalse(hasattr(ranges, "scan_range"))
        x = ranges.preview_batches("10.0.0.0/9", offset=1000, limit=64)
        self.assertEqual(len(x["batches"]), 64)
        self.assertEqual(x["offset"], 1000)

    def test_32_and_25_stay_exact(self):
        self.assertEqual(ranges.preview_batches("10.2.4.7/32")["batches"], ["10.2.4.7/32"])
        self.assertEqual(ranges.preview_batches("10.2.4.128/25")["batches"], ["10.2.4.128/25"])

    def test_validation_bounds_and_ipv6(self):
        for value in ("", "gibberish", "2001:db8::/32", "0.0.0.0/0"):
            with self.assertRaises(ranges.RangeValidationError, msg=value):
                ranges.plan_range(value)
        for bad in (-1, 32769):
            with self.assertRaises(ranges.RangeValidationError):
                ranges.preview_batches("128.0.0.0/9", offset=bad)
        for bad in (0, 65, 10_000_000):
            with self.assertRaises(ranges.RangeValidationError):
                ranges.preview_batches("128.0.0.0/9", limit=bad)
