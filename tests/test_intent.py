import unittest

from host.intent import normalize, parse_local, route, route_persona


def L(text):
    i = parse_local(text)
    return (i.action, i.params) if i else None


class LocalIntentTests(unittest.TestCase):
    def test_volume_english(self):
        self.assertEqual(L("set volume to 40"), ("set_volume", {"level": 40}))
        self.assertEqual(L("volume 30%"), ("set_volume", {"level": 30}))
        self.assertEqual(L("volume up"), ("change_volume", {"delta": 10}))
        self.assertEqual(L("turn the volume down"), ("change_volume", {"delta": -10}))
        self.assertEqual(L("increase volume by 20"), ("change_volume", {"delta": 20}))
        self.assertEqual(L("volume 500"), ("set_volume", {"level": 100}))

    def test_volume_urdu_and_roman(self):
        self.assertEqual(L("awaz kam karo"), ("change_volume", {"delta": -10}))
        self.assertEqual(L("awaz barhao"), ("change_volume", {"delta": 10}))
        self.assertEqual(L("آواز کم کرو"), ("change_volume", {"delta": -10}))
        self.assertEqual(L("آواز زیادہ کرو"), ("change_volume", {"delta": 10}))
        self.assertEqual(L("آواز ٥٠ کرو"), ("set_volume", {"level": 50}))  # Arabic-Indic digits
        self.assertEqual(L("آواز ۷۰ کرو"), ("set_volume", {"level": 70}))  # Urdu digits

    def test_brightness(self):
        self.assertEqual(L("brightness 60"), ("set_brightness", {"level": 60}))
        self.assertEqual(L("dim the brightness"), ("change_brightness", {"delta": -10}))
        self.assertEqual(L("roshni kam karo"), ("change_brightness", {"delta": -10}))
        self.assertEqual(L("روشنی کم کرو"), ("change_brightness", {"delta": -10}))

    def test_mute_lock(self):
        for t in ["mute", "unmute", "mute the volume", "khamosh", "خاموش", "میوٹ"]:
            self.assertEqual(L(t)[0], "toggle_mute", t)
        for t in ["lock screen", "lock the computer", "lock", "لاک کرو"]:
            self.assertEqual(L(t)[0], "lock_workstation", t)

    def test_windows(self):
        self.assertEqual(L("maximize chrome"), ("manage_window", {"app_name": "chrome", "action": "maximize"}))
        self.assertEqual(L("minimize the notepad window"), ("manage_window", {"app_name": "notepad", "action": "minimize"}))
        self.assertEqual(L("snap chrome left"), ("manage_window", {"app_name": "chrome", "action": "snap_left"}))
        self.assertEqual(L("snap the vs code window to the right"),
                         ("manage_window", {"app_name": "vs code", "action": "snap_right"}))

    def test_stats_and_clear(self):
        self.assertEqual(L("battery"), ("get_system_telemetry", {}))
        self.assertEqual(L("cpu usage"), ("get_system_telemetry", {}))
        self.assertEqual(L("clear the overlay"), ("clear_annotations", {}))
        self.assertEqual(L("hide arrows"), ("clear_annotations", {}))

    def test_questions_go_to_llm(self):
        for t in ["how do I change the volume in davinci resolve", "why is my brightness stuck",
                  "kaise volume barhaon", "explain this slide", "what is the lock icon in this app"]:
            self.assertIsNone(parse_local(t), t)

    def test_urdu_substring_not_matched(self):
        self.assertIsNone(L("کمپیوٹر آواز"))   # 'کم' inside 'کمپیوٹر' must not count as "down"
        self.assertEqual(L("کمپیوٹر لاک کرو")[0], "lock_workstation")

    def test_long_text_goes_to_llm(self):
        self.assertIsNone(parse_local("please lower the volume of the audio track in the timeline of my video project now"))

    def test_normalize(self):
        self.assertEqual(normalize("  Volume,  UP!! "), "volume up")
        self.assertEqual(normalize("٤٠"), "40")


class PersonaRouteTests(unittest.TestCase):
    def test_routes(self):
        cases = {
            "help me here": "screencoach",
            "guide me how to add a transition in davinci resolve": "screencoach",
            "explain this slide": "screencoach",
            "there is a syntax error here": "screencoach",
            "go to google.com and search for cats": "webpilot",
            "open https://example.com": "webpilot",
            "check my university portal": "webpilot",
            "summarize my notes about physics": "fileclerk",
            "save this as a note in my folder": "fileclerk",
            "اس فائل کا خلاصہ بتاو": "fileclerk",
            "یہاں کیا غلطی ہے": "screencoach",
            "something totally random": "screencoach",
        }
        for text, want in cases.items():
            self.assertEqual(route_persona(text), want, text)

    def test_route_kinds(self):
        self.assertEqual(route("volume 20").kind, "local")
        i = route("guide me how to render")
        self.assertEqual((i.kind, i.persona), ("llm", "screencoach"))
        self.assertEqual(route("آواز کم کرو").lang, "ur")


if __name__ == "__main__":
    unittest.main()
