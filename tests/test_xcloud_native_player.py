import json
import os
import unittest

from cartridges.xcloud_native import player


SYNTH_SDP = (
    "v=0\r\n"
    "o=- 1 1 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "m=video 9 UDP/TLS/RTP/SAVPF 96 97\r\n"
    "c=IN IP4 0.0.0.0\r\n"
    "a=rtpmap:96 VP8/90000\r\n"
    "a=rtpmap:97 H264/90000\r\n"
    "a=fmtp:97 packetization-mode=1;profile-level-id=42002a\r\n"
    "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
    "c=IN IP4 0.0.0.0\r\n"
    "a=rtpmap:111 opus/48000/2\r\n"
    "a=fmtp:111 minptime=10;useinbandfec=1\r\n"
)


class FakeChannel:
    def __init__(self):
        self.sent_strings = []

    def emit(self, signal, *args):
        if signal == "send-string":
            self.sent_strings.append(args[0])
            return True
        raise AssertionError(f"sinal inesperado: {signal}")


def _peer_without_pipeline():
    peer = player.WebRTCPeer.__new__(player.WebRTCPeer)
    peer._data_channels = {}
    return peer


class PlayerTablesTests(unittest.TestCase):
    def test_h264_caps_exact(self):
        self.assertIn("payload=127", player.H264_CAPS)
        self.assertIn("packetization-mode=(string)1", player.H264_CAPS)
        self.assertIn("profile-level-id=(string)42002a", player.H264_CAPS)
        self.assertIn("clock-rate=90000", player.H264_CAPS)

    def test_opus_caps_exact(self):
        self.assertIn("payload=111", player.OPUS_CAPS)
        self.assertIn("clock-rate=48000", player.OPUS_CAPS)
        self.assertIn("encoding-params=(string)2", player.OPUS_CAPS)

    def test_data_channel_table(self):
        expected = [
            ("input", "1.0"),
            ("control", "controlV1"),
            ("message", "messageV1"),
            ("chat", "chatV1"),
        ]
        got = [(c["label"], c["protocol"]) for c in player.DATA_CHANNELS]
        self.assertEqual(got, expected)
        self.assertTrue(all(c["ordered"] for c in player.DATA_CHANNELS))


class SdpMungeTests(unittest.TestCase):
    def test_empty_passthrough(self):
        self.assertEqual(player.munge_sdp(""), "")

    def test_keeps_h264_video_and_audio(self):
        out = player.munge_sdp(SYNTH_SDP)
        self.assertIn("m=video", out)
        self.assertIn("m=audio", out)
        self.assertIn("H264/90000", out)

    def test_drops_non_h264_video_section(self):
        vp8_only = (
            "v=0\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            "a=rtpmap:96 VP8/90000\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
            "a=rtpmap:111 opus/48000/2\r\n"
        )
        out = player.munge_sdp(vp8_only)
        self.assertNotIn("m=video", out)
        self.assertIn("m=audio", out)

    def test_injects_bandwidth(self):
        out = player.munge_sdp(SYNTH_SDP, bandwidth_kbps=8000)
        self.assertIn("b=AS:8000", out)

    def test_audio_fmtp_gets_stereo(self):
        out = player.munge_sdp(SYNTH_SDP)
        self.assertIn("stereo=1", out)

    def test_parse_media_lines(self):
        self.assertEqual(
            player._parse_sdp_media(SYNTH_SDP),
            ["m=video 9 UDP/TLS/RTP/SAVPF 96 97", "m=audio 9 UDP/TLS/RTP/SAVPF 111"],
        )
        self.assertEqual(player._parse_sdp_media(""), [])


class ControlMessageTests(unittest.TestCase):
    def test_message_keyed_format(self):
        peer = _peer_without_pipeline()
        fake = FakeChannel()
        peer._data_channels["control"] = fake
        self.assertTrue(peer.send_authorization_request("KEY-123"))
        self.assertTrue(peer.send_gamepad_changed(0, True))
        self.assertTrue(peer.send_video_keyframe_requested())
        msgs = [json.loads(s) for s in fake.sent_strings]
        self.assertEqual(
            msgs[0], {"message": "authorizationRequest", "accessKey": "KEY-123"}
        )
        self.assertEqual(
            msgs[1],
            {"message": "gamepadChanged", "gamepadIndex": 0, "wasAdded": True},
        )
        self.assertEqual(
            msgs[2], {"message": "videoKeyframeRequested", "ifrRequested": True}
        )

    def test_send_without_channel_returns_false(self):
        peer = _peer_without_pipeline()
        self.assertFalse(peer.send_string("control", "hi"))
        self.assertFalse(peer.send_data("input", b"\x00"))


@unittest.skipUnless(
    player.Gst is not None, "GStreamer/gi indisponível neste ambiente"
)
class PeerConstructionTests(unittest.TestCase):
    def test_transceivers_have_expected_directions(self):
        from gi.repository import GstWebRTC

        peer = player.WebRTCPeer()
        try:
            kind = GstWebRTC.WebRTCRTPTransceiverDirection
            t0 = peer.webrtcbin.emit("get-transceiver", 0)
            t1 = peer.webrtcbin.emit("get-transceiver", 1)
            self.assertIsNotNone(t0)
            self.assertIsNotNone(t1)
            self.assertEqual(t0.get_property("direction"), kind.RECVONLY)
            self.assertEqual(t1.get_property("direction"), kind.SENDRECV)
        finally:
            peer.close()

    def test_data_channels_created_in_band(self):
        peer = player.WebRTCPeer()
        try:
            ok, reason = player.webrtc_supported()
            if not ok:
                with self.assertRaises(player.PlayerError):
                    peer.create_data_channels()
                self.assertIn("nice", reason)
                return
            peer.create_data_channels()
            self.assertEqual(
                sorted(peer._data_channels.keys()),
                ["chat", "control", "input", "message"],
            )
            by_label = {c["label"]: c for c in player.DATA_CHANNELS}
            for label, chan in peer._data_channels.items():
                self.assertEqual(
                    chan.get_property("protocol"), by_label[label]["protocol"]
                )
                self.assertFalse(chan.get_property("negotiated"))
        finally:
            peer.close()

    def test_webrtc_supported_reports_tuple(self):
        ok, reason = player.webrtc_supported()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(reason, str)
        self.assertTrue(reason)

    def test_real_offer_announces_media_and_data_channels(self):
        ok, reason = player.webrtc_supported()
        if not ok:
            self.skipTest(reason)
        peer = player.WebRTCPeer()
        try:
            peer.create_data_channels()
            offer = peer.create_offer()
            media = player._parse_sdp_media(offer)
            self.assertTrue(any(line.startswith("m=video") for line in media))
            self.assertTrue(any(line.startswith("m=audio") for line in media))
            self.assertTrue(any(line.startswith("m=application") for line in media))
        finally:
            peer.close()


class FakePromiseReply:
    def __init__(self, reply=None, fire=True):
        self._reply = reply
        self._fire = fire

    def get_reply(self):
        return self._reply


class PromiseWaitTests(unittest.TestCase):
    def _peer(self):
        peer = player.WebRTCPeer.__new__(player.WebRTCPeer)
        peer._name = "teste"
        return peer

    def _patch_new(self, promise):
        def fake_new_with_change_func(cb):
            promise._cb = cb
            return promise

        return unittest.mock.patch.object(
            player.Gst.Promise, "new_with_change_func", fake_new_with_change_func
        )

    def test_run_resolve_com_timeout(self):
        peer = self._peer()
        promise = FakePromiseReply(reply="RESPOSTA")
        with self._patch_new(promise):
            def emit(p):
                p._cb(p)

            self.assertEqual(peer._run_promise(emit, "teste", timeout=5), "RESPOSTA")

    def test_run_estoura_timeout(self):
        peer = self._peer()
        promise = FakePromiseReply(reply=None, fire=False)
        with self._patch_new(promise):
            with self.assertRaises(player.PlayerError):
                peer._run_promise(lambda p: None, "nunca-responde", timeout=1)


@unittest.skipUnless(
    os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"),
    "sem display para instanciar widget GTK",
)
class WidgetTests(unittest.TestCase):
    def test_widget_class_exists(self):
        self.assertTrue(hasattr(player, "XCloudVideoWidget"))


if __name__ == "__main__":
    unittest.main()
