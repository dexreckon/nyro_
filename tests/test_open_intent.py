import unittest

from host.intent import route


class OpenIntentTests(unittest.TestCase):
    def check(self, text, target, browser=None, query=None):
        i = route(text)
        self.assertEqual((i.kind, i.action), ("local", "open_target"), text)
        self.assertEqual(i.params["target"], target, text)
        self.assertEqual(i.params.get("browser"), browser, text)
        self.assertEqual(i.params.get("query"), query, text)

    def test_english(self):
        self.check("open youtube in chrome", "youtube", "chrome")
        self.check("Open YouTube in Chrome.", "youtube", "chrome")  # trailing period from speech-to-text
        self.check("launch google chrome", "chrome")
        self.check("open notepad", "notepad")

    def test_roman_urdu_hindi(self):
        self.check("youtube kholo chrome me", "youtube", "chrome")

    def test_urdu_script(self):
        self.check("یوٹیوب کھولو کروم میں", "youtube", "chrome")

    def test_devanagari(self):
        self.check("यूट्यूब खोलो क्रोम में", "youtube", "chrome")
        self.check("क्रोम खोलो", "chrome")

    def test_search(self):
        self.check("play lofi music on youtube in chrome", "youtube", "chrome", "lofi music")

    def test_explain_goes_to_screencoach(self):
        i = route("explain this")
        self.assertEqual((i.kind, i.persona), ("llm", "screencoach"))

    def close(self, text, target):
        i = route(text)
        self.assertEqual((i.kind, i.action), ("local", "close_tab"), text)
        self.assertEqual(i.params["target"], target, text)

    def test_close_tab(self):
        self.close("close youtube tab", "youtube")
        self.close("Close the YouTube tab.", "youtube")
        self.close("close this tab", None)
        self.close("close the chrome tab", None)
        self.close("close stack overflow tab please", "stack overflow")
        self.close("youtube tab band karo", "youtube")
        self.close("یوٹیوب ٹیب بند کرو", "youtube")
        self.close("यूट्यूब टैब बंद करो", "youtube")

    def test_other_actions_use_mouse_and_keyboard(self):
        for t in ("close the popup", "click the subscribe button", "scroll down a bit", "refresh the page"):
            i = route(t)
            self.assertEqual((i.kind, i.persona), ("llm", "desktopctl"), t)

    def test_unknown_site_is_not_opened(self):
        self.assertNotEqual(route("open evil-site").action, "open_target")


if __name__ == "__main__":
    unittest.main()
