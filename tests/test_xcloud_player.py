import json
import struct
import threading
import unittest
from unittest.mock import patch

from cartridges.xcloud_native import input
from cartridges.xcloud_player import XCloudPlayerController


class FakeStreaming:
    gs_token = "GS-TOKEN"
    market = "pt-BR"
    offering = "xgpuweb"
    regions = [{"name": "BR", "baseUri": "https://base", "isDefault": True}]
    expires_at = None


class FakeLogin:
    streaming = FakeStreaming()
    lpt = "LPT"
    refresh_token = "RT"

    def __init__(self, falha=False):
        self.falha = falha

    def refresh_streaming_tokens(self):
        if self.falha:
            raise Exception("Sem sessão ativa")
        return self


class FakeConfiguration:
    def __init__(self, keepalive=0):
        self.keepalive_pulse_in_seconds = keepalive


class FakePrepared:
    base = "https://base"
    platform = "cloud"
    session_id = "SID"
    session_path = "cloud/SID"
    state = "Provisioned"

    def __init__(self, keepalive=0):
        self.configuration = FakeConfiguration(keepalive)


class FakePeer:
    def __init__(self, candidates=None):
        self.candidates = candidates if candidates is not None else [(0, "candidate:local-ice")]
        self.started = False
        self.channels = False
        self.added = []

    def start(self):
        self.started = True

    def poll_outgoing_ice(self, max_count=10):
        return list(self.candidates)

    def add_remote_ice(self, mline, candidate):
        self.added.append((mline, candidate))

    def create_data_channels(self):
        self.channels = True


class FakePlayer:
    def __init__(self, candidates=None):
        self.peer = FakePeer(candidates)
        self.sent = []
        self.answer = None
        self.closed = False
        self.authorization = []
        self.gamepad_changes = []
        self.dc_handler = None
        self.dc_open_handler = None
        self.sent_strings = []

    def set_data_channel_handler(self, cb):
        self.dc_handler = cb

    def set_data_channel_open_handler(self, cb):
        self.dc_open_handler = cb

    def start(self):
        self.peer.start()
        self.peer.create_data_channels()
        for label in ("input", "control", "message", "chat"):
            self.dc_open_handler(label)

    def poll_outgoing_ice(self, max_count=10):
        return self.peer.poll_outgoing_ice(max_count)

    def add_remote_ice(self, mline, candidate):
        self.peer.add_remote_ice(mline, candidate)

    def create_offer(self):
        return "OFFER-SDP"

    def set_remote_answer(self, sdp):
        self.answer = sdp

    def send_data(self, label, data):
        self.sent.append((label, data))
        return True

    def send_string(self, label, data):
        self.sent_strings.append((label, data))
        message = json.loads(data)
        if label == "message" and message.get("type") == "Handshake":
            self.dc_handler("message", b'{"type":"HandshakeAck"}')
        return True

    def send_authorization_request(self, access_key):
        self.authorization.append(access_key)
        return True

    def send_gamepad_changed(self, gamepad_index, was_added):
        self.gamepad_changes.append((gamepad_index, was_added))
        return True

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self):
        self.calls = []
        self.remotes = ["a=candidate:remote-1", "candidate:remote-2"]

    def resolve_base_uri(self, streaming, region_name=""):
        self.calls.append("resolve_base_uri")
        return "https://base"

    def resolve_title_id(self, base, gs_token, product_id):
        self.calls.append("resolve_title_id")
        return getattr(self, "resolved_id", "")

    def open_session_for_login(self, login, title_id="", sleep=None):
        self.calls.append("open_session_for_login")
        return FakePrepared()

    def exchange_sdp(self, base, gs_token, session_path, offer_sdp, sleep=None):
        self.calls.append("exchange_sdp")
        self.sent_offer = offer_sdp
        return type("Answer", (), {"sdp": "ANSWER-SDP"})()

    def exchange_ice(self, base, gs_token, ice_path, candidates, sleep=None):
        self.calls.append("exchange_ice")
        self.posted_candidates = candidates
        return list(self.remotes)

    def ice_exchange_path(self, prepared):
        self.calls.append("ice_exchange_path")
        return "cloud/SID/ice"

    def keepalive(self, base, gs_token, session_path):
        self.calls.append("keepalive")

    def stop(self, base, gs_token, session_path):
        self.calls.append("stop")
        return True


class FakePlayerWithoutHandshake(FakePlayer):
    def send_string(self, label, data):
        self.sent_strings.append((label, data))
        return True


def chamar_na_hora(func, *args):
    func(*args)
    return 0


class XCloudPlayerTests(unittest.TestCase):
    def montar(self, **kwargs):
        auth_fake = kwargs.pop("auth_fake", None) or FakeLogin()
        session_fake = kwargs.pop("session_fake", None) or FakeSession()
        player_fake = kwargs.pop("player_fake", None) or FakePlayer()
        return (
            XCloudPlayerController(
                "PROD-1",
                auth_module=auth_fake,
                session_module=session_fake,
                player_factory=lambda: player_fake,
                sleep=lambda _s: None,
                **kwargs,
            ),
            session_fake,
            player_fake,
        )

    @patch("cartridges.utils.na_tela.entregar_na_tela", side_effect=chamar_na_hora)
    def test_fluxo_completo_ate_o_ready(self, _entregar):
        controller, session_fake, player_fake = self.montar()
        ready = threading.Event()
        erros = []

        def ao_ready(ctrl):
            ready.set()
            ctrl.stop()

        def ao_erro(_ctrl, message):
            erros.append(message)

        controller.on_ready = ao_ready
        controller.on_error = ao_erro

        controller.start()
        self.assertTrue(ready.wait(5), "on_ready não disparou")
        controller._thread.join(timeout=5)
        self.assertFalse(controller._thread.is_alive(), "thread não terminou")

        # Ordem do contrato: canais antes do SDP; handshake depois do ICE.
        primeiro_sdp = session_fake.calls.index("exchange_sdp")
        primeiro_ice = session_fake.calls.index("exchange_ice")
        self.assertLess(
            session_fake.calls.index("open_session_for_login"), primeiro_sdp
        )
        self.assertLess(primeiro_sdp, primeiro_ice)
        self.assertEqual(player_fake.answer, "ANSWER-SDP")
        self.assertTrue(player_fake.peer.started)
        self.assertEqual(
            player_fake.peer.added,
            [(0, "candidate:remote-1"), (0, "candidate:remote-2")],
        )
        self.assertTrue(player_fake.peer.channels)
        self.assertEqual(
            player_fake.authorization, ["4BDB3609-C1F1-4195-9B37-FEFF45DA8B8E"]
        )
        self.assertEqual(player_fake.gamepad_changes, [(0, True)])
        self.assertEqual(json.loads(player_fake.sent_strings[0][1])["type"], "Handshake")
        self.assertEqual(player_fake.sent[0][0], "input")
        # first_packet carrega timestamp monotônico: valida tipo e payload.
        _, primeiro = player_fake.sent[0]
        self.assertEqual(
            struct.unpack_from("<H", primeiro, 0)[0], input.REPORT_CLIENT_METADATA
        )
        self.assertEqual(primeiro[-1], 1)
        self.assertIn("stop", session_fake.calls)
        self.assertTrue(player_fake.closed)
        self.assertFalse(erros)

    @patch("cartridges.utils.na_tela.entregar_na_tela", side_effect=chamar_na_hora)
    def test_erro_sem_candidato_ice(self, _entregar):
        player_fake = FakePlayer(candidates=[])
        controller, session_fake, player_fake = self.montar(
            player_fake=player_fake, ice_collect_timeout=0.0
        )
        erro = threading.Event()
        mensagens = []

        def ao_erro(_ctrl, message):
            mensagens.append(message)
            erro.set()

        controller.on_error = ao_erro

        controller.start()
        self.assertTrue(erro.wait(5), "on_error não disparou")
        controller._thread.join(timeout=5)
        self.assertFalse(controller._thread.is_alive())
        self.assertTrue(any("candidato" in m for m in mensagens))
        self.assertIn("stop", session_fake.calls)
        self.assertTrue(player_fake.closed)

    @patch("cartridges.utils.na_tela.entregar_na_tela", side_effect=chamar_na_hora)
    def test_erro_quando_message_channel_nao_confirma_handshake(self, _entregar):
        player_fake = FakePlayerWithoutHandshake()
        controller, session_fake, player_fake = self.montar(
            player_fake=player_fake, channel_handshake_timeout=0.0
        )
        erro = threading.Event()
        mensagens = []
        controller.on_error = lambda _ctrl, message: (
            mensagens.append(message),
            erro.set(),
        )

        controller.start()
        self.assertTrue(erro.wait(5), "on_error não disparou")
        controller._thread.join(timeout=5)
        self.assertTrue(any("handshake" in message for message in mensagens))
        self.assertIn("stop", session_fake.calls)
        self.assertTrue(player_fake.closed)

    @patch("cartridges.utils.na_tela.entregar_na_tela", side_effect=chamar_na_hora)
    def test_erro_ao_renovar_tokens(self, _entregar):
        controller, session_fake, player_fake = self.montar(
            auth_fake=FakeLogin(falha=True)
        )
        erro = threading.Event()
        mensagens = []

        def ao_erro(_ctrl, message):
            mensagens.append(message)
            erro.set()

        controller.on_error = ao_erro

        controller.start()
        self.assertTrue(erro.wait(5), "on_error não disparou")
        controller._thread.join(timeout=5)
        self.assertNotIn("stop", session_fake.calls)
        self.assertFalse(player_fake.closed)

    def test_pacote_gamepad_do_tick(self):
        controller, _session, player_fake = self.montar()
        controller.player = player_fake
        controller.feed_button(input.BTN_A, True)
        controller.feed_axis(left_x=1.0, left_y=0.0)
        controller.feed_trigger(left=1.0, right=0.0)

        controller._send_input_tick()

        rotulo, pacote = player_fake.sent[0]
        self.assertEqual(rotulo, "input")
        self.assertEqual(struct.unpack_from("<H", pacote, 0)[0], input.REPORT_GAMEPAD)
        self.assertEqual(pacote[14], 1)
        botoes = struct.unpack_from("<H", pacote, 16)[0]
        self.assertTrue(botoes & input.BTN_A)
        self.assertEqual(struct.unpack_from("<H", pacote, 26)[0], 65535)

    def test_hat_mapeia_dpad(self):
        controller, _session, player_fake = self.montar()
        controller.player = player_fake
        controller.feed_hat(dx=0, dy=1)
        controller._send_input_tick()
        botoes = struct.unpack_from("<H", player_fake.sent[0][1], 16)[0]
        self.assertTrue(botoes & input.BTN_DPAD_UP)
        self.assertFalse(botoes & input.BTN_DPAD_DOWN)

    def test_mouse_fracoes_usam_server_metadata(self):
        controller, _session, player_fake = self.montar()
        controller.player = player_fake
        # ServerMetadata: u16 report + u32 height + u32 width (LE).
        frame = struct.pack("<HII", input.REPORT_SERVER_METADATA, 720, 1280)
        controller._on_data_channel("input", frame)

        controller.feed_mouse(x_fraction=0.5, y_fraction=0.5)
        controller._send_input_tick()

        # O tick manda o frame de gamepad primeiro; o evento vem depois.
        rotulo, pacote = player_fake.sent[-1]
        self.assertEqual(rotulo, "input")
        self.assertEqual(struct.unpack_from("<H", pacote, 0)[0], input.REPORT_MOUSE)
        self.assertEqual(pacote[14], 1)
        self.assertEqual(struct.unpack_from("<I", pacote, 15)[0], 640)
        self.assertEqual(struct.unpack_from("<I", pacote, 19)[0], 360)

    def test_teclado_vai_ao_tick(self):
        controller, session_fake, player_fake = self.montar()
        controller.player = player_fake
        controller.feed_keyboard(code=65, pressed=True)
        controller._send_input_tick()

        _, pacote = player_fake.sent[-1]
        self.assertEqual(struct.unpack_from("<H", pacote, 0)[0], input.REPORT_KEYBOARD)
        self.assertEqual(pacote[14], 1)
        self.assertEqual(pacote[15], input.KEY_SOURCE_VKEY)
        self.assertEqual(pacote[16], 1)
        self.assertEqual(pacote[17], 65)

    @patch("cartridges.utils.na_tela.entregar_na_tela", side_effect=chamar_na_hora)
    def test_titulo_fora_do_gpu_tenta_f2p(self, _entregar):
        from cartridges.xcloud_native import session as real_session

        class LoginF2p(FakeLogin):
            streaming = type(
                "StreamingF2p",
                (),
                {
                    "gs_token": "GS-F2P",
                    "market": "pt-BR",
                    "offering": "xgpuwebf2p",
                    "regions": FakeStreaming.regions,
                },
            )()

        class AuthRetry(FakeLogin):
            def __init__(self):
                super().__init__()
                self.directed = []

            def login_offering(self, refresh_token, offering):
                self.directed.append((refresh_token, offering))
                return LoginF2p()

        class SessionRetry(FakeSession):
            def __init__(self):
                super().__init__()
                self.attempts = 0

            def open_session_for_login(self, login, title_id="", sleep=None):
                self.attempts += 1
                if login.streaming.offering == "xgpuweb":
                    raise real_session.TitleNotInOfferingError(
                        400, "OfferingDoesNotContainTitle"
                    )
                return FakePrepared()

        auth_fake = AuthRetry()
        session_fake = SessionRetry()
        controller, _, player_fake = self.montar(
            auth_fake=auth_fake, session_fake=session_fake
        )
        ready = threading.Event()

        def ao_ready(ctrl):
            ready.set()
            ctrl.stop()

        controller.on_ready = ao_ready
        controller.on_error = lambda _c, _m: ready.set()
        controller.start()
        self.assertTrue(ready.wait(5), "on_ready não disparou no retry")
        controller._thread.join(timeout=5)
        self.assertEqual(auth_fake.directed, [("RT", "xgpuwebf2p")])
        self.assertEqual(session_fake.attempts, 2)

    @patch("cartridges.utils.na_tela.entregar_na_tela", side_effect=chamar_na_hora)
    def test_store_id_resolvido_reusa_offering(self, _entregar):
        from cartridges.xcloud_native import session as real_session

        class SessionResolve(FakeSession):
            def __init__(self):
                super().__init__()
                self.play_ids = []
                self.resolved_id = "GENSHINIMPACT"

            def open_session_for_login(self, login, title_id="", sleep=None):
                self.play_ids.append(title_id)
                if title_id != "GENSHINIMPACT":
                    raise real_session.TitleNotInOfferingError(
                        400, "OfferingDoesNotContainTitle"
                    )
                return FakePrepared()

        class AuthNoFallback(FakeLogin):
            def login_offering(self, refresh_token, offering):
                raise AssertionError("não deveria cair no f2p")

        controller, session_fake, _ = self.montar(
            auth_fake=AuthNoFallback(), session_fake=SessionResolve()
        )
        ready = threading.Event()
        controller.on_ready = lambda c: (ready.set(), c.stop())
        controller.on_error = lambda _c, _m: ready.set()
        controller.start()
        self.assertTrue(ready.wait(5), "on_ready não disparou com resolve")
        controller._thread.join(timeout=5)
        self.assertEqual(session_fake.play_ids[0], "PROD-1")
        self.assertEqual(session_fake.play_ids[-1], "GENSHINIMPACT")

    def test_mensagem_fora_do_canal_ignorada(self):
        controller, _session, _player = self.montar()
        controller._on_data_channel("control", b"\xff")
        self.assertEqual(
            controller._server_size, (1920, 1080),
        )


if __name__ == "__main__":
    unittest.main()
