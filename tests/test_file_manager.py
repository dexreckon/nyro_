import os
import tempfile
import time
import unittest
from pathlib import Path

from host import desktop_icons as DI
from host import filemanager as FM
from host import organizer as O
from host.fileindex import FileIndex
from host.intent import route

OLD = time.time() - 3600


def touch(p: Path, text: str = "x", age: float = 3600) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    t = time.time() - age
    os.utime(p, (t, t))
    return p


class CategorizeTests(unittest.TestCase):
    def test_extensions_and_name_rules(self):
        self.assertEqual(O.categorize("photo.JPG"), "Images")
        self.assertEqual(O.categorize("setup.exe"), "Installers")
        self.assertEqual(O.categorize("movie.mkv"), "Videos")
        self.assertEqual(O.categorize("main.cpp"), "Code")
        self.assertEqual(O.categorize("mystery.xyz"), "Other")
        self.assertEqual(O.categorize("Lab Assignment 2.docx"), "University")
        self.assertEqual(O.categorize("midterm paper.pdf"), "University")
        self.assertEqual(O.categorize("Invoice-4471.pdf"), "Bills & Receipts")
        self.assertEqual(O.categorize("holiday.jpg"), "Images")             # name rules only apply to document-like files
        self.assertEqual(O.categorize("a.xyz", {"xyz": "Mine"}), "Mine")

    def test_underscored_study_files_and_lecture_slides_go_to_university(self):
        for n in ("Lab_Task_Solutions_BCS-5D.docx", "CSC211_DSA_Exam_Notes.docx", "Web_Technologies_Exam_Notes (2).docx",
                  "Week 5.docx", "Lec 01.pptx", "Lec-12-Stack_ADT_Implementation.pptx", "Lab_Mid_BCS_5D.pdf",
                  "DSA_Lab_Mid_Solution.docx", "COAL_Study_Notes.docx", "Theory_Assignment_2_Solution.docx"):
            self.assertEqual(O.categorize(n), "University", n)
        for n in ("Cart.pdf", "Quickbites-pamphlet-A4.pdf", "HANDOVER.md", "Smartwatch page.pdf", "Part 1.pptx",
                  "Flutter_WebView_Redesign.docx", "personal statement.docx"):
            self.assertNotEqual(O.categorize(n), "University", n)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_what_is_moved_and_what_is_left_alone(self):
        touch(self.root / "report.pdf")
        touch(self.root / "pic.png")
        touch(self.root / "setup.exe")
        touch(self.root / "video.crdownload")                  # still downloading
        touch(self.root / "fresh.pdf", age=5)                  # just modified
        touch(self.root / ".hidden.txt")
        touch(self.root / "desktop.ini")
        touch(self.root / "MyFolder" / "inside.pdf")           # existing folders are untouched
        plan = O.build_plan(self.root)
        moved = {Path(m.src).name: m.category for m in plan.moves}
        self.assertEqual(moved, {"report.pdf": "Documents", "pic.png": "Images", "setup.exe": "Installers"})
        reasons = dict(plan.skipped)
        self.assertEqual(reasons["video.crdownload"], "download in progress")
        self.assertEqual(reasons["fresh.pdf"], "modified a moment ago")
        self.assertNotIn("inside.pdf", moved)

    def test_desktop_shortcuts_stay(self):
        touch(self.root / "Chrome.lnk")
        touch(self.root / "Steam.url")
        touch(self.root / "notes.txt")
        plan = O.build_plan(self.root, is_desktop=True)
        self.assertEqual([Path(m.src).name for m in plan.moves], ["notes.txt"])
        self.assertEqual({n for n, r in plan.skipped}, {"Chrome.lnk", "Steam.url"})

    def test_existing_matching_folder_is_reused(self):
        (self.root / "images").mkdir()
        touch(self.root / "a.png")
        plan = O.build_plan(self.root)
        self.assertEqual(plan.new_folders, [])
        self.assertTrue(plan.moves[0].dst.endswith(os.path.join("images", "a.png")))


class ApplyUndoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_moves_never_overwrites_and_undo_restores_everything(self):
        touch(self.root / "a.pdf", "A")
        touch(self.root / "b.png", "B")
        touch(self.root / "Documents" / "a.pdf", "already here")     # collision at the destination
        plan = O.build_plan(self.root)
        res = O.apply_plan(plan)
        self.assertEqual(len(res.moved), 2)
        self.assertEqual((self.root / "Documents" / "a.pdf").read_text(), "already here")   # untouched
        self.assertEqual((self.root / "Documents" / "a (1).pdf").read_text(), "A")
        self.assertEqual((self.root / "Images" / "b.png").read_text(), "B")
        self.assertFalse((self.root / "a.pdf").exists())

        back = O.undo_last(self.root)
        self.assertEqual(len(back.moved), 2)
        self.assertEqual((self.root / "a.pdf").read_text(), "A")
        self.assertEqual((self.root / "b.png").read_text(), "B")
        self.assertEqual((self.root / "Documents" / "a.pdf").read_text(), "already here")
        self.assertFalse((self.root / "Images").exists())            # a folder WE created and emptied is removed
        self.assertTrue((self.root / "Documents").exists())          # a folder that was already there stays
        with self.assertRaises(O.OrganizerError):
            O.undo_last(self.root)                                   # nothing left to undo

    def test_nothing_is_deleted_total_bytes_preserved(self):
        for i in range(10):
            touch(self.root / f"f{i}.txt", "x" * (i + 1))
        before = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
        O.apply_plan(O.build_plan(self.root))
        after = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file() and p.name != O.JOURNAL)
        self.assertEqual(before, after)

    def test_file_that_changed_since_preview_is_left(self):
        p = touch(self.root / "x.pdf")
        plan = O.build_plan(self.root)
        os.utime(p, None)                                            # touched again: now "in use"
        self.assertEqual(O.apply_plan(plan).moved, [])
        self.assertTrue(p.exists())

    def test_tampered_journal_cannot_move_files_outside_the_folder(self):
        import json
        outside = Path(self.tmp.name).parent / "nyro_outside_test.txt"
        outside.write_text("secret")
        try:
            (self.root / O.JOURNAL).write_text(json.dumps({"batches": [
                {"id": "x", "created": [], "moves": [{"from": str(self.root / "pwned.txt"), "to": str(outside)}]}]}))
            res = O.undo_last(self.root)
            self.assertEqual(res.moved, [])
            self.assertTrue(outside.exists())
        finally:
            outside.unlink(missing_ok=True)


class RootSafetyTests(unittest.TestCase):
    def test_only_configured_folders_and_never_system_places(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = {"organizer": {"roots": [d, "C:\\Windows", "C:\\"]}, "firewall": {
                "protected_path_prefixes": ["C:\\Windows"], "protected_path_fragments": ["system32"]}}
            roots = O.allowed_roots(cfg)
            self.assertEqual(list(roots.values()), [Path(d).resolve()])
            with self.assertRaises(O.OrganizerError):
                O.resolve_root("windows", cfg)
            with self.assertRaises(O.OrganizerError):
                O.resolve_root("documents", cfg)                      # not enabled in this config


class OrganizeFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Stuff"
        self.root.mkdir()
        self.cfg = {"data_dir": str(Path(self.tmp.name) / "data"), "organizer": {"roots": [str(self.root)]},
                    "index": {"roots": [str(self.root)]}, "firewall": {}}
        touch(self.root / "a.pdf")
        touch(self.root / "b.png")

    def tearDown(self):
        self.tmp.cleanup()

    def test_refused_approval_moves_nothing(self):
        out = FM.organize(self.cfg, "stuff", lambda a, s, p: False)
        self.assertIn("left everything", out)
        self.assertTrue((self.root / "a.pdf").exists())

    def test_approved_runs_and_summary_asked_for_includes_counts(self):
        asked = []
        out = FM.organize(self.cfg, "stuff", lambda a, s, p: asked.append((a, s, p)) or True)
        self.assertEqual(asked[0][0], "organize_folder")
        self.assertIn("2 files", asked[0][1])
        self.assertIn("Moved 2 files", out)
        self.assertTrue((self.root / "Documents" / "a.pdf").exists())
        self.assertIn("Put 2 files back", FM.undo(self.cfg, "stuff"))
        self.assertTrue((self.root / "a.pdf").exists())

    def test_preview_changes_nothing(self):
        txt = FM.preview_organize(self.cfg, "stuff")
        self.assertIn("2 files", txt)
        self.assertTrue((self.root / "a.pdf").exists())


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "docs"
        touch(self.root / "Lab Assignment 2.docx", "binary-ish")
        touch(self.root / "notes" / "sorting_algorithms.py", "def bubble_sort(items):\n    return sorted(items)")
        touch(self.root / "Invoice 4471.txt", "Total due: 250 dollars for the laptop repair", age=86400 * 30)
        touch(self.root / "holiday.jpg", "img")
        self.idx = FileIndex(Path(self.tmp.name) / "i.sqlite3", [self.root])
        self.stats = self.idx.update()

    def tearDown(self):
        self.tmp.cleanup()

    def names(self, q):
        return [Path(r["path"]).name for r in self.idx.search(q)]

    def test_find_by_name_words_and_prefix(self):
        self.assertEqual(self.names("assignment")[0], "Lab Assignment 2.docx")
        self.assertIn("Lab Assignment 2.docx", self.names("the assignment docx please"))
        self.assertIn("sorting_algorithms.py", self.names("sorting"))
        self.assertIn("holiday.jpg", self.names("holiday photo jpg"))

    def test_find_by_file_contents(self):
        self.assertEqual(self.names("laptop repair")[0], "Invoice 4471.txt")
        self.assertIn("[bubble]", " ".join(r["snip"] for r in self.idx.search("bubble")))   # match is highlighted

    def test_newer_files_and_updates_are_picked_up_and_deleted_ones_dropped(self):
        touch(self.root / "thesis draft.txt", "quantum computing")
        (self.root / "holiday.jpg").unlink()
        stats = self.idx.update()
        self.assertEqual((stats["added"], stats["removed"]), (1, 1))
        self.assertIn("thesis draft.txt", self.names("quantum"))
        self.assertNotIn("holiday.jpg", self.names("holiday"))

    def test_noting_moves_keeps_search_working(self):
        old = self.root / "Invoice 4471.txt"
        new = self.root / "Bills" / "Invoice 4471.txt"
        new.parent.mkdir()
        old.rename(new)
        self.idx.note_moves([(str(old), str(new))])
        self.assertEqual(self.idx.search("laptop repair")[0]["path"], str(new))

    def test_empty_and_symbol_queries_do_not_crash(self):
        for q in ("", "   ", "the my a", "!!!", '"; DROP TABLE files; --', "a" * 500):
            self.idx.search(q)
        self.assertEqual(self.idx.status()["files"], 4)


class DesktopLayoutTests(unittest.TestCase):
    def test_classification(self):
        c = DI.classify
        self.assertEqual(c("Google Chrome", "shortcut", ""), "Browsers")
        self.assertEqual(c("Claude", "shortcut", "c:\\chrome_proxy.exe --app-id=x"), "Office & Docs")   # name beats target
        self.assertEqual(c("Chrome Remote Desktop", "shortcut"), "Utilities")
        self.assertEqual(c("NIUBI Partition Editor Free Edition", "shortcut"), "Utilities")
        self.assertEqual(c("Dev-C++", "shortcut"), "Development")
        self.assertEqual(c("Play Halo Infinite", "shortcut"), "Games")
        self.assertEqual(c("Discord", "shortcut"), "Communication")
        self.assertEqual(c("lab tasks UI", "file"), "Files")
        self.assertEqual(c("Projects", "folder"), "Folders")
        self.assertEqual(c("Recycle Bin", "virtual"), "System")
        self.assertEqual(c("Totally Unknown App", "shortcut", ""), "Other apps")

    def test_layout_groups_start_new_columns_and_nothing_overlaps(self):
        items = [{"index": i, "name": n, "group": g} for i, (n, g) in enumerate(
            [("A", "Browsers"), ("B", "Browsers"), ("C", "Games"), ("D", "Games"), ("E", "Games"), ("F", "Files")])]
        pos = DI.plan_layout(items, (100, 100), (1000, 350), "category")
        self.assertEqual(len(set(pos.values())), 6)                    # no two icons on the same spot
        self.assertEqual(pos[0][0], pos[1][0])                         # same group, same column
        self.assertNotEqual(pos[1][0], pos[2][0])                      # next group starts a new column
        self.assertTrue(all(0 <= x < 1000 and 0 <= y < 350 for x, y in pos.values()))

    def test_layout_falls_back_to_compact_when_screen_is_too_narrow(self):
        items = [{"index": i, "name": f"n{i}", "group": f"G{i}"} for i in range(8)]
        pos = DI.plan_layout(items, (100, 100), (300, 200), "category")
        self.assertEqual(len(set(pos.values())), 8)

    def test_name_mode_is_alphabetical(self):
        items = [{"index": 0, "name": "zeta", "group": "x"}, {"index": 1, "name": "alpha", "group": "y"}]
        pos = DI.plan_layout(items, (100, 100), (1000, 1000), "name")
        self.assertLess(pos[1][1], pos[0][1])


class RoutingTests(unittest.TestCase):
    def action(self, t):
        i = route(t)
        return (i.kind, i.persona, i.action, i.params)

    def test_instant_commands(self):
        self.assertEqual(self.action("organize my downloads"), ("local", "filemanager", "organize_folder", {"folder": "downloads"}))
        self.assertEqual(self.action("Clean up downloads.")[2], "organize_folder")
        self.assertEqual(self.action("tidy my desktop")[2], "organize_desktop")
        self.assertEqual(self.action("arrange desktop icons")[2], "arrange_icons")
        self.assertEqual(self.action("arrange my desktop shortcuts")[2], "arrange_icons")
        self.assertEqual(self.action("restore desktop layout")[2], "restore_icons")
        self.assertEqual(self.action("undo")[2], "undo_organize")
        self.assertEqual(self.action("undo the cleanup")[2], "undo_organize")
        self.assertEqual(self.action("open downloads")[2:], ("open_folder", {"folder": "downloads"}))
        self.assertEqual(self.action("find my assignment pdf")[2:], ("find_files", {"query": "assignment pdf"}))
        self.assertEqual(self.action("downloads saaf karo")[2], "organize_folder")

    def test_not_swallowed(self):
        self.assertNotEqual(self.action("find lofi music")[2], "find_files")       # no file-ish word
        self.assertNotEqual(self.action("search lofi on youtube")[2], "find_files")
        self.assertEqual(self.action("open youtube")[2], "open_target")

    def test_natural_language_goes_to_file_manager_persona(self):
        for t in ("where is my assignment pdf", "can you find the invoice that I downloaded last week and open it",
                  "could you organize my downloads but keep invoices in their own folder",
                  "could you tidy up my desktop and sort the icons by name",
                  "could you search my files for the resume"):
            i = route(t)
            self.assertEqual((i.kind, i.persona), ("llm", "filemanager"), t)

    def test_existing_routes_unchanged(self):
        self.assertEqual(route("make a solution word file for the assignment pdf").persona, "fileclerk")
        self.assertEqual(route("explain this").persona, "screencoach")
        self.assertEqual(route("where is the subscribe button").persona, "screencoach")
        self.assertEqual(route("make a python file in vs code").persona, "coder")


if __name__ == "__main__":
    unittest.main()
