import ast
import pathlib
import re


def scan(root: pathlib.Path) -> None:
    for p in sorted(root.rglob("*.py")):
        src = p.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            print("SYNTAX", p, exc)
            continue
        imported = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported[(a.asname or a.name).split(".")[0]] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name == "*":
                        continue
                    imported[a.asname or a.name] = node.lineno
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                cur = node
                while isinstance(cur, ast.Attribute):
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    used.add(cur.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                used.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", node.value))
        unused = {k: v for k, v in imported.items() if k not in used and k != "annotations"}
        if unused:
            print(p.as_posix(), unused)


if __name__ == "__main__":
    scan(pathlib.Path("src"))