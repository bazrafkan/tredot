"""The app bundle must not depend on this Mac: build_app.self_contained refuses outside links."""

import os
import sys
import tempfile
import unittest

os.environ["TREDOT_LANG"] = "en"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_app  # noqa: E402


class SelfContainedTest(unittest.TestCase):
    def test_links_inside_are_fine(self):
        with tempfile.TemporaryDirectory() as folder:
            os.makedirs(os.path.join(folder, "bin"))
            open(os.path.join(folder, "bin", "python3.12"), "w").close()
            os.symlink("python3.12", os.path.join(folder, "bin", "python3"))
            build_app.self_contained(folder)

    def test_link_outside_is_refused(self):
        # a virtual environment's bin/python points to the installation on this Mac
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outside:
            os.makedirs(os.path.join(folder, "bin"))
            open(os.path.join(outside, "python3.12"), "w").close()
            os.symlink(os.path.join(outside, "python3.12"), os.path.join(folder, "bin", "python"))
            with self.assertRaisesRegex(RuntimeError, "points outside the app"):
                build_app.self_contained(folder)


if __name__ == "__main__":
    unittest.main()
