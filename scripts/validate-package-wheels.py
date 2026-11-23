"""Build and validate the standalone IG wheels in separate clean environments."""

import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import venv
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("ig-core", "ig-queue", "ig-storage")
APP_IMPORTS = {"app", "fastapi", "sqlalchemy", "is", "nexus"}
CORE_IMPORTS = {"redis", "rq", "vercel", "vercel_queue", "ig_queue", "ig_storage"}

CHECK_IMPORT = """
import importlib
from importlib.util import find_spec
from pathlib import Path
import sys
import sysconfig

name, repo = sys.argv[1:]
module = importlib.import_module(name)
location = Path(module.__file__).resolve()
installed = Path(sysconfig.get_path("purelib")).resolve()
assert location.is_relative_to(installed), location
assert not any(Path(path).resolve().is_relative_to(repo)
               and not Path(path).resolve().is_relative_to(sys.prefix)
               for path in sys.path if path), sys.path
assert find_spec("app") is None, "IG application is importable"
print(f"{name} imports from {location}", flush=True)
"""


def check_contents(wheel: Path, source: Path, module: str) -> None:
    forbidden = APP_IMPORTS | (CORE_IMPORTS if module == "ig_core" else set())
    with ZipFile(wheel) as archive:
        contents = set(archive.namelist())
        expected = {path.relative_to(source).as_posix() for path in (source / module).rglob("*.py")}
        missing = expected - contents
        if not expected or missing:
            raise RuntimeError(f"{wheel.name} is missing package sources: {sorted(missing)}")
        for name in sorted(contents):
            if name.startswith("app/"):
                raise RuntimeError(f"{wheel.name} contains IG application code: {name}")
            if not name.startswith(module + "/") or not name.endswith(".py"):
                continue
            for node in ast.walk(ast.parse(archive.read(name), filename=name)):
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    imports = [node.module or ""]
                else:
                    continue
                for imported in imports:
                    if imported.split(".")[0] in forbidden:
                        raise RuntimeError(f"{name}:{node.lineno} imports forbidden module {imported}")


def verify_package(package: str, directory: Path, env: dict[str, str]) -> None:
    def run(python, *arguments):
        subprocess.run([str(python), "-I", *arguments], cwd=directory, env=env, check=True)

    source = ROOT / "packages" / package
    module = package.replace("-", "_")
    print(f"\nValidating {package}", flush=True)
    # Build from a scratch copy so setuptools writes no build products to the checkout.
    build_source = directory / "source"
    shutil.copytree(source, build_source, ignore=shutil.ignore_patterns(
        "build", "dist", "*.egg-info", "__pycache__", "*.pyc", ".venv",
    ))
    wheels = directory / "wheels"
    run(sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(wheels), str(build_source))
    (wheel,) = wheels.glob("*.whl")
    check_contents(wheel, source, module)

    environment = directory / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run(python, "-m", "pip", "install", str(wheel))
    run(python, "-m", "pip", "check")
    run(python, "-c", CHECK_IMPORT, module, str(ROOT))

    # Only tests are made importable here. The source copy is never on sys.path.
    tests = directory / "tests"
    shutil.copytree(source / "tests", tests, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    run(python, "-m", "unittest", "discover", "-s", str(tests), "-v")


def main() -> None:
    env = {key: value for key, value in os.environ.items()
           if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__"}}
    for package in PACKAGES:
        with TemporaryDirectory(prefix=f"{package}-wheel-") as scratch:
            verify_package(package, Path(scratch), env)


if __name__ == "__main__":
    main()
