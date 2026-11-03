import ast
from pathlib import Path

from django.test import SimpleTestCase

PACKAGE = Path(__file__).resolve().parent.parent


class TranslationImportTests(SimpleTestCase):
    def test_every_module_calling_gettext_imports_it(self):
        """A module that calls _() must import it; a merge once dropped the import."""
        missing = []
        for path in PACKAGE.rglob('*.py'):
            tree = ast.parse(path.read_text())
            calls = any(
                isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_'
                for node in ast.walk(tree)
            )
            imported = any(
                isinstance(node, ast.ImportFrom) and any((alias.asname or alias.name) == '_' for alias in node.names)
                for node in tree.body
            )
            if calls and not imported:
                missing.append(str(path.relative_to(PACKAGE)))
        self.assertEqual(missing, [])
