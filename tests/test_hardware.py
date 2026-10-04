import tempfile
import unittest
from pathlib import Path

from cartridges.hardware import drm_connectors, parse_wpctl_nodes, vrr_supported


class HardwareTests(unittest.TestCase):
    def test_wpctl_nodes_are_split_by_direction(self):
        output = """
 ├─ Sinks:
 │  *   50. alsa_output.pci-main [vol: 1.00]
 │      72. hdmi_output [vol: 0.90]
 ├─ Sources:
 │  *   51. alsa_input.pci-main [vol: 0.25]
 ├─ Filters:
"""
        self.assertEqual(
            parse_wpctl_nodes(output),
            (["alsa_output.pci-main", "hdmi_output"], ["alsa_input.pci-main"]),
        )

    def test_drm_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            connector = root / "card1-HDMI-A-1"
            connector.mkdir()
            (connector / "status").write_text("connected\n", encoding="utf-8")
            (connector / "vrr_capable").write_text("1\n", encoding="utf-8")
            self.assertEqual(drm_connectors(root), ["HDMI-A-1"])
            self.assertTrue(vrr_supported(root))


if __name__ == "__main__":
    unittest.main()
