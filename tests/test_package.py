"""Regression checks operate only on temporary files, never a live OpenFang."""
from contextlib import redirect_stdout
import importlib.util
from importlib.machinery import SourceFileLoader
import io
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent


def load_script(name):
    loader = SourceFileLoader(name, str(ROOT / "scripts" / name))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


scrub = load_script("ofscrub")
package = load_script("validate_package.py")
builder = load_script("build_plugin.py")


class ScrubTests(unittest.TestCase):
    def test_identity_slot_rejects_real_hostname(self):
        key = "OPENFANG_URL"
        self.assertTrue(scrub.scan_line(f'{key}="https://operator-host.invalid-domain"'))

    def test_reserved_documentation_address_is_allowed(self):
        self.assertEqual(scrub.scan_line("host: 192.0.2.42"), [])

    def test_unrelated_addresses_are_still_rejected(self):
        public = ".".join(map(str, (168, 63, 129, 17)))
        tailnet = ".".join(map(str, (100, 64, 12, 34)))
        for address in (public, tailnet):
            with self.subTest(address=address):
                self.assertTrue(scrub.scan_line(f"host: {address}"))

    def test_scan_reports_location_without_repeating_value(self):
        address = ".".join(map(str, (168, 63, 129, 17)))
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "sample.md").write_text(f"host: {address}\n")
            output = io.StringIO()
            with redirect_stdout(output):
                result = scrub.main(["ofscrub", directory])
            self.assertEqual(result, 1)
            self.assertIn("sample.md:1", output.getvalue())
            self.assertNotIn(address, output.getvalue())


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "package"
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns(".git", "__pycache__"))

    def test_valid_package(self):
        package.validate(self.root)

    def test_invalid_skill_yaml_is_rejected(self):
        (self.root / "SKILL.md").write_text(
            "---\nname: fang-upgrade\ndescription: notes: malformed YAML\n---\n"
        )
        with self.assertRaises(package.yaml.YAMLError):
            package.validate(self.root)

    def test_version_mismatch_is_rejected(self):
        manifest = self.root / ".codex-plugin/plugin.json"
        contents = package.json.loads(manifest.read_text())
        contents["version"] = "999.0.0"
        manifest.write_text(package.json.dumps(contents))
        with self.assertRaisesRegex(ValueError, "versions differ"):
            package.validate(self.root)

    def test_auto_discovered_hook_is_rejected(self):
        (self.root / "hooks").mkdir()
        with self.assertRaisesRegex(ValueError, "auto-discover"):
            package.validate(self.root)

    def check_generated(self):
        with patch.object(builder, "ROOT", self.root), \
             patch.object(builder, "TARGET", self.root / "skills/fang-upgrade"), \
             patch("sys.argv", ["build_plugin.py", "--check"]):
            with redirect_stdout(io.StringIO()):
                builder.main()

    def test_generated_payload_matches(self):
        self.check_generated()

    def test_modified_payload_is_rejected(self):
        script = self.root / "skills/fang-upgrade/scripts/oftarget.py"
        script.write_text(script.read_text() + "\n# unexpected edit\n")
        with self.assertRaises(SystemExit):
            self.check_generated()

    def test_lost_executable_bit_is_rejected(self):
        script = self.root / "skills/fang-upgrade/scripts/ofctl"
        script.chmod(script.stat().st_mode & ~0o100)
        with self.assertRaises(SystemExit):
            self.check_generated()


if __name__ == "__main__":
    unittest.main()
