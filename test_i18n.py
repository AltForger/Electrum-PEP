import ast
import json
from pathlib import Path
from string import Formatter
import unittest

from pep_i18n import select_language, set_ui_language, tr


class LanguageTests(unittest.TestCase):
    def test_display_language_and_english_fallback(self):
        self.addCleanup(set_ui_language, ["en"])
        for tag, expected in (("ru-RU", "Отправка"), ("en_US", "Send"),
                              ("de-DE", "Senden"), ("fr-CA", "Envoyer"), ("und", "Send")):
            with self.subTest(tag=tag):
                set_ui_language([tag])
                self.assertEqual(tr("Send"), expected)

    def test_preference_order_and_script_variants(self):
        languages = {"en", "ru", "de", "pt_BR", "pt_PT", "zh_Hans", "zh_Hant", "sr_Latn", "sr_Cyrl"}
        for tags, expected in ((["en-AU", "ru"], "en"), (["xx", "de-AT"], "de"),
                               (["pt-Latn-BR"], "pt_BR"), (["pt-PT"], "pt_PT"),
                               (["zh-HK"], "zh_Hant"), (["zh-Hans-CN"], "zh_Hans"),
                               (["sr-Latn-RS"], "sr_Latn"), (["sr"], "sr_Cyrl"), ([], "en")):
            self.assertEqual(select_language(tags, languages), expected)

    def test_all_catalogs_cover_messages_and_preserve_fields(self):
        root = Path(__file__).resolve().parent
        messages = set()
        for source in root.glob("*.py"):
            if source.name.startswith("test"):
                continue
            for node in ast.walk(ast.parse(source.read_bytes())):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr":
                    messages.add(node.args[0].value)
        catalogs = list((root / "assets/languages").glob("*.json"))
        notice = json.loads((root / "assets/startup-notice.json").read_text(encoding="utf-8"))
        self.assertEqual({"en", *(p.stem for p in catalogs)}, set(notice))
        def fields(text):
            return sorted(name for _, name, _, _ in Formatter().parse(text) if name is not None)
        for path in catalogs:
            catalog = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(messages, set(catalog), path.stem)
            for key, value in catalog.items():
                with self.subTest(language=path.stem, key=key):
                    self.assertTrue(value.strip())
                    self.assertEqual(fields(key), fields(value))
                    self.assertEqual(key.count("\n"), value.count("\n"))
                    self.assertNotRegex(value, r"ZX\s*\d|\d\s*XZ|◆|\[7\d\d\]")
