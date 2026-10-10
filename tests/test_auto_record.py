import ast
import json
import shlex
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parents[1]


def _parse(relative: str) -> ast.Module:
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _class_node(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _serialized_names(function: ast.FunctionDef) -> set[str]:
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "attrs"
            for target in node.targets
        ):
            return {
                item.value
                for item in node.value.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
    return set()


class _FakeGame:
    """Stand-in game whose missing fields serialize as ``None``."""

    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)

    def __getattr__(self, _name: str) -> None:
        return None


class FileManagerSerializationTests(unittest.TestCase):
    def setUp(self) -> None:
        tree = _parse("cartridges/store/managers/file_manager.py")
        self.main = next(
            node
            for node in _class_node(tree, "FileManager").body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )

    def test_auto_record_is_serialized(self) -> None:
        self.assertIn("auto_record", _serialized_names(self.main))

    def test_round_trip_preserves_true(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            games_dir = Path(directory)
            namespace: dict[str, Any] = {
                "json": json,
                "shared": types.SimpleNamespace(games_dir=games_dir),
                "Game": object,
            }
            module = ast.fix_missing_locations(
                ast.Module(body=[self.main], type_ignores=[])
            )
            exec(compile(module, "file_manager.py", "exec"), namespace)  # pylint: disable=exec-used
            namespace["main"](
                None, _FakeGame(auto_record=True, game_id="rt-1"), {"skip_save": False}
            )

            saved = json.loads((games_dir / "rt-1.json").read_text(encoding="utf-8"))
            self.assertIs(saved["auto_record"], True)


class GameDefaultTests(unittest.TestCase):
    def _build_game_class(self) -> type:
        tree = _parse("cartridges/game.py")
        game_class = _class_node(tree, "Game")
        field = next(
            node
            for node in game_class.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "auto_record"
        )
        self.assertIsInstance(field.value, ast.Constant)
        self.assertIs(field.value.value, False)

        update_values = next(
            node
            for node in game_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "update_values"
        )
        synthetic = ast.ClassDef(
            name="TestGame",
            bases=[],
            keywords=[],
            body=[field, update_values],
            decorator_list=[],
        )
        module = ast.fix_missing_locations(ast.Module(body=[synthetic], type_ignores=[]))
        namespace: dict[str, Any] = {"shlex": shlex, "Any": Any}
        exec(compile(module, "game.py", "exec"), namespace)  # pylint: disable=exec-used
        return namespace["TestGame"]

    def test_old_json_without_field_defaults_to_false(self) -> None:
        game = self._build_game_class()()
        game.update_values({"name": "Legacy Game"})
        self.assertIs(game.auto_record, False)

    def test_present_field_is_applied(self) -> None:
        game = self._build_game_class()()
        game.update_values({"auto_record": True})
        self.assertIs(game.auto_record, True)


if __name__ == "__main__":
    unittest.main()
