"""Phone voice: audio goes phone -> relay -> host, and the transcript comes back (never replayed from history)."""
import base64
import unittest
from pathlib import Path

from tests.test_relay import RelayBase
from vps import relay


class RelayAudioTests(RelayBase):
    def test_audio_is_forwarded_to_host_with_clean_fields(self):
        m = self.mobile()
        h = self.host(m)
        b64 = base64.b64encode(b"x" * 150_000).decode()           # far above the 8 KB limit for normal messages
        m.send({"type": "audio", "b64": b64, "rid": 7, "mime": "audio/webm;codecs=opus", "lang": "ur", "evil": "x"})
        got = h.recv()
        self.assertEqual((got["type"], got["rid"], got["lang"], got["mime"]), ("audio", 7, "ur", "audio/webm;codecs=opus"))
        self.assertEqual(got["b64"], b64)
        self.assertNotIn("evil", got)

    def test_oversize_audio_and_oversize_commands_are_rejected(self):
        m = self.mobile()
        h = self.host(m)
        m.send({"type": "audio", "b64": "A" * (relay.MAX_AUDIO_MSG + 10), "rid": 1})
        self.assertEqual(m.recv()["error"], "message too large")
        m.send({"type": "command", "text": "x" * (relay.MAX_MOBILE_MSG + 10)})
        self.assertEqual(m.recv()["error"], "message too large")
        self.assertTrue(h.silent())

    def test_audio_needs_host_and_valid_fields(self):
        m = self.mobile()
        m.send({"type": "audio", "b64": "AAAA", "rid": 1})
        self.assertEqual(m.recv()["error"], "Laptop is offline")
        h = self.host(m)
        m.send({"type": "audio", "rid": 1})                        # no b64
        m.send({"type": "audio", "b64": "AAAA"})                   # no rid
        self.assertTrue(h.silent())

    def test_audio_rate_limit(self):
        m = self.mobile()
        h = self.host(m)
        for i in range(relay.AUDIO_LIMIT):
            m.send({"type": "audio", "b64": "AAAA", "rid": i})
            self.assertEqual(h.recv()["rid"], i)
        m.send({"type": "audio", "b64": "AAAA", "rid": 99})
        self.assertEqual(m.recv()["error"], "Slow down")

    def test_transcript_reaches_mobile_but_is_not_kept_in_history(self):
        m = self.mobile()
        h = self.host(m)
        h.send({"type": "transcript", "rid": 3, "text": "open youtube in chrome"})
        self.assertEqual(m.recv(), {"type": "transcript", "rid": 3, "text": "open youtube in chrome"})
        h.send({"type": "reply", "text": "ok", "source": "local"})
        m.recv()
        late = self.conn("mobile")                                 # a phone connecting later gets a snapshot
        late.recv()                                                # auth_ok
        snap = late.recv()
        types = [x["type"] for x in snap["history"]]
        self.assertIn("reply", types)
        self.assertNotIn("transcript", types)


class HostPhoneAudioTests(unittest.TestCase):
    def make(self, tb):
        from host.app import NyroApp
        sent = []
        app = NyroApp({}, Path("."), object(), send_phone=sent.append, transcribe_bytes=tb)
        return app, sent

    def test_transcribes_and_replies_with_same_rid(self):
        calls = []
        app, sent = self.make(lambda data, lang: (calls.append((data, lang)) or ("volume thirty", "en")))
        app._phone_audio({"type": "audio", "b64": base64.b64encode(b"audio-bytes").decode(), "rid": 5, "lang": "ur"})
        self.assertEqual(calls, [(b"audio-bytes", "ur")])
        self.assertEqual(sent, [{"type": "transcript", "rid": 5, "text": "volume thirty", "lang": "en"}])

    def test_bad_input_gives_error_transcript_not_a_crash(self):
        app, sent = self.make(lambda d, l: ("x", "en"))
        app._phone_audio({"type": "audio", "b64": "not base64!!", "rid": 1})
        app._phone_audio({"type": "audio", "b64": base64.b64encode(b"x" * 400_000).decode(), "rid": 2})
        self.assertEqual([(s["rid"], s["text"], bool(s.get("error"))) for s in sent], [(1, "", True), (2, "", True)])

    def test_no_transcriber_is_reported(self):
        app, sent = self.make(None)
        app._phone_audio({"type": "audio", "b64": "AAAA", "rid": 9})
        self.assertTrue(sent[0]["error"])

    def test_unknown_language_hint_is_ignored(self):
        calls = []
        app, _ = self.make(lambda d, l: (calls.append(l) or ("a", "en")))
        app._phone_audio({"type": "audio", "b64": "AAAA", "rid": 1, "lang": "fr"})
        self.assertEqual(calls, [None])


if __name__ == "__main__":
    unittest.main()
