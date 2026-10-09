import struct
import unittest

from cartridges.xcloud_native import input as xinput


class HeaderTests(unittest.TestCase):
    def test_header_layout(self):
        raw = xinput.header(0x002, 7, timestamp_ms=1234.5)
        self.assertEqual(len(raw), 14)
        report, seq, ts = struct.unpack("<HI d", raw)
        self.assertEqual((report, seq, ts), (0x002, 7, 1234.5))

    def test_first_packet(self):
        raw = xinput.first_packet(max_touch_points=2, sequence=3)
        self.assertEqual(len(raw), 15)
        self.assertEqual(struct.unpack("<H", raw[:2])[0], 0x008)
        self.assertEqual(raw[14], 2)


class GamepadTests(unittest.TestCase):
    def test_frame_size_and_fields(self):
        state = xinput.GamepadState(
            index=1,
            buttons=xinput.BTN_A | xinput.BTN_DPAD_UP,
            left_x=1.0,
            left_y=-1.0,
            right_x=0.5,
            right_y=0.0,
            left_trigger=1.0,
            right_trigger=0.25,
        )
        raw = xinput.encode_gamepad(state, sequence=9)
        self.assertEqual(len(raw), 14 + 1 + 23)
        body = raw[14:]
        self.assertEqual(body[0], 1)
        self.assertEqual(body[1], 1)
        self.assertEqual(struct.unpack("<H", body[2:4])[0], 0x0010 | 0x0100)
        lx, ly, rx, ry = struct.unpack("<hhhh", body[4:12])
        self.assertEqual((lx, ly, rx, ry), (32767, 32767, 16383, 0))
        lt, rt = struct.unpack("<HH", body[12:16])
        self.assertEqual((lt, rt), (65535, 16383))
        self.assertEqual(struct.unpack("<I", body[16:20])[0], 1)
        self.assertEqual(struct.unpack(">I", body[20:24])[0], 1)

    def test_clamping(self):
        state = xinput.GamepadState(left_x=9.0, left_trigger=-3.0)
        raw = xinput.encode_gamepad(state, sequence=0)
        body = raw[14:]
        lx = struct.unpack("<h", body[4:6])[0]
        lt = struct.unpack("<H", body[12:14])[0]
        self.assertEqual((lx, lt), (32767, 0))


class KeyboardMousePointerTests(unittest.TestCase):
    def test_keyboard(self):
        raw = xinput.encode_keyboard(
            xinput.KeyEvent(source=xinput.KEY_SOURCE_VKEY, pressed=True, code=13),
            sequence=1,
        )
        self.assertEqual(len(raw), 18)
        self.assertEqual(raw[14:], bytes([1, 2, 1, 13]))

    def test_mouse(self):
        raw = xinput.encode_mouse(
            xinput.MouseEvent(x=10, y=20, wheel_y=1, buttons=1, relative=True),
            sequence=2,
        )
        self.assertEqual(len(raw), 14 + 1 + 18)
        body = raw[14:]
        self.assertEqual(body[0], 1)
        self.assertEqual(struct.unpack("<IIII", body[1:17]), (10, 20, 0, 1))
        self.assertEqual(body[17:], bytes([1, 1]))

    def test_pointer_event_size(self):
        raw = xinput.encode_pointer(
            [xinput.PointerEvent(x=100, y=200, pressure=0.5, kind=xinput.PTR_DOWN)],
            sequence=3,
        )
        self.assertEqual(len(raw), 14 + 2 + 20)
        body = raw[14:]
        self.assertEqual(body[:2], bytes([1, 1]))
        self.assertEqual(struct.unpack("<H", body[2:4])[0], 100)
        self.assertEqual(body[6], 127)
        self.assertEqual(body[-1], xinput.PTR_DOWN)


class DecodeTests(unittest.TestCase):
    def test_server_metadata(self):
        raw = struct.pack("<HII", 0x010, 1080, 1920)
        report, meta = xinput.decode_server_frame(raw)
        self.assertEqual(report, 0x010)
        self.assertEqual((meta.width, meta.height), (1920, 1080))

    def test_vibration(self):
        raw = (
            struct.pack("<H", 0x080)
            + bytes([1, 0, 100, 50, 10, 20])
            + struct.pack("<HHB", 300, 100, 2)
        )
        report, vib = xinput.decode_server_frame(raw)
        self.assertEqual(report, 0x080)
        self.assertEqual(
            (vib.left_motor, vib.right_motor, vib.duration_ms, vib.delay_ms, vib.repeat),
            (100, 50, 300, 100, 2),
        )

    def test_short_and_unknown_raise(self):
        with self.assertRaises(ValueError):
            xinput.decode_server_frame(b"\x00")
        with self.assertRaises(ValueError):
            xinput.decode_server_frame(struct.pack("<H", 0x200) + bytes(14))

    def test_describe_and_sequence(self):
        self.assertEqual(
            xinput.describe_reports(0x002 | 0x040), ["gamepad", "keyboard"]
        )
        seq = xinput.Sequence(0xFFFFFFFE)
        self.assertEqual((seq.next(), seq.next(), seq.next()), (0xFFFFFFFE, 0xFFFFFFFF, 0))
        self.assertTrue(xinput.queue_limit_ok(29))
        self.assertFalse(xinput.queue_limit_ok(30))


if __name__ == "__main__":
    unittest.main()
