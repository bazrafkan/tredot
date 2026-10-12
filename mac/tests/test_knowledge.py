"""The knowledge graph of the notes (knowledge.py): entities, links, the page."""

import os
import sys
import tempfile
import unittest

os.environ["TREDOT_LANG"] = "en"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import knowledge  # noqa: E402

NOTE = """# {title}

*2026-10-07 12:30 · 0:10 of audio · [audio](x.wav)*

## Summary
Talk to Davide about production.

## Transcript

text

{graph}
"""


class GraphTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        knowledge.NOTES = self.folder.name
        knowledge.PAGE = os.path.join(self.folder.name, "out", "graph.html")

    def tearDown(self):
        self.folder.cleanup()

    def write(self, name, title, graph):
        with open(os.path.join(self.folder.name, name), "w", encoding="utf-8") as f:
            f.write(NOTE.format(title=title, graph=graph))

    def test_shared_entity_links_two_notes(self):
        self.write("2026-10-07 1230 A.md", "A", "<!-- graph: Davide | Production -->")
        self.write("2026-10-08 0900 B.md", "B", "<!-- graph: davide -->")
        data = knowledge.graph(knowledge.notes())
        entities = [n for n in data["nodes"] if n["kind"] == "entity"]
        self.assertEqual(sorted(e["name"] for e in entities), ["Davide", "Production"])
        self.assertEqual(next(e for e in entities if e["name"] == "Davide")["count"], 2)
        self.assertEqual(len(data["links"]), 3)

    def test_note_without_line_has_unknown_entities(self):
        self.write("2026-10-07 1230 A.md", "A", "")
        self.assertIsNone(knowledge.notes()[0]["entities"])

    def test_entities_line_cleans_names(self):
        self.assertEqual(knowledge.entities_line(["Anna", "anna", "a|b", "--> x", ""]),
                         "<!-- graph: Anna | a b | x -->")

    def test_layout_fits_the_round_screen_and_drops_loners(self):
        self.write("2026-10-07 1230 A.md", "A", "<!-- graph: Davide | Production -->")
        self.write("2026-10-08 0900 B.md", "B", "<!-- graph: Davide -->")
        self.write("2026-10-09 0900 C.md", "C", "<!-- graph:  -->")
        nodes, edges, names = knowledge.layout(knowledge.graph(knowledge.notes()))
        self.assertEqual(len(nodes), 4)  # the note without entities is not there
        self.assertEqual(len(edges), 3)
        self.assertEqual(sorted(names), ["A", "B", "Davide", "Production"])
        for x, y, r, kind in nodes:
            self.assertLessEqual(((x - 120) ** 2 + (y - 108) ** 2) ** 0.5 + r, 63)

    def test_page_is_one_file_with_the_data(self):
        self.write("2026-10-07 1230 A.md", "A </script>", "<!-- graph: Anna -->")
        knowledge.build()
        with open(knowledge.PAGE, encoding="utf-8") as f:
            page = f.read()
        self.assertIn("ForceGraph", page)
        self.assertIn('"Anna"', page)
        self.assertNotIn("A </script>", page)


if __name__ == "__main__":
    unittest.main()
