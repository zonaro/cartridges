import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class CompletedStatusTests(unittest.TestCase):
    def test_beaten_status_moves_game_to_completed_archive(self):
        tree = ast.parse(
            (ROOT / "cartridges" / "game.py").read_text(encoding="utf-8")
        )
        game_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Game"
        )
        methods = [
            node
            for node in game_class.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"zerado", "definir_status"}
        ]
        test_class = ast.ClassDef(
            name="TestGame",
            bases=[],
            keywords=[],
            body=methods,
            decorator_list=[],
        )
        module = ast.fix_missing_locations(ast.Module(body=[test_class], type_ignores=[]))
        namespace = {}
        exec(compile(module, "game.py", "exec"), namespace)  # pylint: disable=exec-used

        game = namespace["TestGame"]()
        game.removed = False
        game.blacklisted = False
        game.executable = "/games/example"
        game.status = "playing"

        game.definir_status("beaten")
        self.assertTrue(game.removed)
        self.assertTrue(game.zerado)

        game.definir_status("playing")
        self.assertFalse(game.removed)
        self.assertFalse(game.zerado)

    def test_editing_same_archived_object_does_not_clean_it_up(self):
        tree = ast.parse(
            (ROOT / "cartridges" / "store" / "store.py").read_text(
                encoding="utf-8"
            )
        )
        method = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "add_game"
        )
        identity_guard = next(
            (
                node
                for node in ast.walk(method)
                if isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and any(isinstance(operator, ast.Is) for operator in node.test.ops)
            ),
            None,
        )

        self.assertIsNotNone(
            identity_guard,
            "add_game must distinguish an edited object from a replacement",
        )


if __name__ == "__main__":
    unittest.main()
