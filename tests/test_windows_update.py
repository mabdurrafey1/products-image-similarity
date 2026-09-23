"""The update swap, run for real on Windows.

The batch script cannot be exercised on the machine this project is developed on, so it is tested
where it actually runs. It is the riskiest code in the app -- it copies over a live installation --
and the thing it must never do is take the user's fetched store listings with it.

The swap waits for a process to exit before it touches anything, so the call has to come from a
process that then exits: a harness is spawned, calls install(), and quits, exactly as the app does.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HARNESS = '''
import sys, os
sys.path.insert(0, {repo!r})
import updater
print(updater.install({new!r}, {app!r}, {exe!r}, {log!r}), flush=True)
'''


@unittest.skipUnless(sys.platform.startswith("win"), "the swap is a Windows batch script")
class WindowsUpdateSwapTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.app = os.path.join(self.root, "app")
        self.new = os.path.join(self.root, "new")
        os.makedirs(os.path.join(self.app, "input_data"))
        os.makedirs(os.path.join(self.new, "input_data"))

        # The installation as the user has it: a version, a file this release drops, and the
        # listing they spent an afternoon fetching.
        self._write(self.app, "version.txt", "old")
        self._write(self.app, "stale.txt", "left over from the old version")
        self._write(self.app, os.path.join("input_data", "my_store.xlsx"), "hours of fetching")

        # Relaunched instead of a real exe, and it leaves proof it ran.
        self.marker = os.path.join(self.root, "relaunched.txt")
        self.log = os.path.join(self.root, "update_log.txt")
        relaunch = f'@echo off\r\necho yes > "{self.marker}"\r\n'
        self._write(self.app, "relaunch.bat", relaunch)

        # The new release: a new version, and its own empty input_data, which must not win.
        self._write(self.new, "version.txt", "new")
        self._write(self.new, "added.txt", "new in this release")
        self._write(self.new, "relaunch.bat", relaunch)
        self._write(self.new, os.path.join("input_data", "from_build.xlsx"), "the build's copy")

    def _write(self, base, name, text):
        path = os.path.join(base, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)

    def _ran(self):
        """Assert the swap happened, quoting the script's own log when it did not."""
        for _ in range(4):
            if os.path.exists(self.marker):
                return
            time.sleep(0.5)
        told = "the script wrote no log at all, so it never started"
        if os.path.exists(self.log):
            with open(self.log) as handle:
                told = "the script's log says:\n" + handle.read()
        self.fail("the app was never started again -- " + told)

    def _read(self, *parts):
        with open(os.path.join(self.app, *parts)) as handle:
            return handle.read().strip()

    def _swap(self):
        """Run the update from a process that exits, and wait for the app to be started again."""
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        source = HARNESS.format(repo=repo, new=self.new, app=self.app,
                                exe=os.path.join(self.app, "relaunch.bat"), log=self.log)
        script = os.path.join(self.root, "harness.py")
        with open(script, "w") as handle:
            handle.write(source)
        done = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        batch = done.stdout.strip().splitlines()[-1]
        for _ in range(120):
            if os.path.exists(self.marker):
                break
            time.sleep(0.5)
        return batch

    def test_new_files_arrive_and_the_app_is_started_again(self):
        self._swap()
        self._ran()
        self.assertEqual(self._read("version.txt"), "new", "the new version was not copied in")
        self.assertTrue(os.path.exists(os.path.join(self.app, "added.txt")))

    def test_the_users_fetched_listings_survive(self):
        """The whole reason the copy does not mirror. If this ever fails, do not ship it."""
        self._swap()
        self._ran()
        self.assertEqual(self._read("input_data", "my_store.xlsx"), "hours of fetching")
        self.assertFalse(os.path.exists(os.path.join(self.app, "input_data", "from_build.xlsx")),
                         "the build's own input_data was copied over the user's")

    def test_nothing_in_the_app_folder_is_deleted(self):
        self._swap()
        self._ran()
        self.assertTrue(os.path.exists(os.path.join(self.app, "stale.txt")),
                        "the copy deleted a file the new release does not contain")

    def test_the_script_removes_itself(self):
        batch = self._swap()
        self._ran()
        for _ in range(20):
            if not os.path.exists(batch):
                break
            time.sleep(0.5)
        self.assertFalse(os.path.exists(batch), "the update script was left behind in temp")


if __name__ == "__main__":
    unittest.main()
