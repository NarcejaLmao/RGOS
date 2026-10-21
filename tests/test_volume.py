#!/usr/bin/env python3
"""Endpoint tests for the RG OS ALSA volume daemon."""
import importlib.machinery
import importlib.util
import os
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "overlay", "opt", "rgos", "rgos-volume")
LOADER = importlib.machinery.SourceFileLoader("rgos_volume_test_module", SCRIPT)
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
volume = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(volume)


class VolumeEndpoints(unittest.TestCase):
    def calls_for(self, percent):
        calls = []
        with mock.patch.object(volume, "amixer", side_effect=lambda *args: calls.append(args) or ""):
            self.assertTrue(volume.apply(percent, ["Speaker", "Headphone"]))
        return calls

    def test_zero_is_set_to_minimum_then_muted(self):
        self.assertEqual(self.calls_for(0), [
            ("-q", "sset", "Speaker", "0%"),
            ("-q", "sset", "Speaker", "mute"),
            ("-q", "sset", "Headphone", "0%"),
            ("-q", "sset", "Headphone", "mute"),
        ])

    def test_hundred_is_set_to_maximum_then_unmuted(self):
        self.assertEqual(self.calls_for(100), [
            ("-q", "sset", "Speaker", "100%"),
            ("-q", "sset", "Speaker", "unmute"),
            ("-q", "sset", "Headphone", "100%"),
            ("-q", "sset", "Headphone", "unmute"),
        ])

    def test_intermediate_volume_is_unmuted(self):
        self.assertEqual(self.calls_for(45), [
            ("-q", "sset", "Speaker", "45%"),
            ("-q", "sset", "Speaker", "unmute"),
            ("-q", "sset", "Headphone", "45%"),
            ("-q", "sset", "Headphone", "unmute"),
        ])

    def test_no_controls_reports_failure(self):
        self.assertFalse(volume.apply(100, []))


if __name__ == "__main__":
    unittest.main()
