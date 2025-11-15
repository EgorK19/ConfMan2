import argparse
import ast
import configparser
import os
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlparse
from contextlib import contextmanager, nullcontext
from typing import List
import tomllib


def validate_args(args):
    errors = []

    # 1. Имя пакета
    if not args.package_name:
        errors.append("Имя анализируемого пакета не может быть пустым.")
    elif not re.match(r'^[A-Za-z0-9_-]+$', args.package_name.replace("-", "").replace("_", "")):
        errors.append(
            "Имя пакета содержит недопустимые символы. "
            "Разрешены буквы, цифры, дефис (-) и подчёркивание (_)."
        )

    # 2. Репозиторий + режим
    repo = args.repo
    mode = args.mode
    if mode == "remote":
        parsed = urlparse(repo)
        if not parsed.scheme or parsed.scheme not in ("http", "https", "https", "git", "git+http", "git+https"):
            errors.append("В режиме 'remote' репозиторий должен быть валидным URL (http/https/git).")
    elif mode == "local-dir":
        if not os.path.isdir(repo):
            errors.append(f"В режиме 'local-dir' путь '{repo}' не является директорией.")
    elif mode == "local-file":
        if not os.path.isfile(repo):
            errors.append(f"В режиме 'local-file' путь '{repo}' не является файлом.")
        supported = (".zip", ".tar.gz", ".tar", ".tar.bz2", ".whl")
        if not repo.lower().endswith(supported):
            errors.append(f"Для режима 'local-file' поддерживаются только архивы {', '.join(supported)}")

    # 3. Фильтр
    if args.filter_substring is not None and not isinstance(args.filter_substring, str):
        errors.append("Подстрока фильтрации должна быть строкой.")

    if errors:
        print("Ошибки в параметрах:", file=sys.stderr)
        for err in errors:
            print(f" • {err}", file=sys.stderr)
        sys.exit(1)


def get_dep_name(req):
    name = req.split("[")[0].split(";")[0].strip()
    name = re.split(r"[~=><=!~]", name)[0].strip()
    return name


def extract_direct_dependencies(repo_path):
    requirements: List[str] = []

    # 1. pyproject.toml — PEP 621 и Poetry
    pp_path = os.path.join(repo_path, "pyproject.toml")
    if os.path.exists(pp_path) and tomllib is not None:
        with open(pp_path, "rb") as f:
            try:
                data = tomllib.load(f)

                # PEP 621
                if "project" in data and "dependencies" in data["project"]:
                    requirements = data["project"].get("dependencies", [])

                # Poetry
                if not requirements and "tool" in data and "poetry" in data["tool"]:
                    deps = data["tool"]["poetry"].get("dependencies", {})
                    for name, spec in deps.items():
                        if name.lower() == "python":
                            continue
                        if isinstance(spec, str):
                            requirements.append(f"{name}{spec}")
                        elif isinstance(spec, dict):
                            version = spec.get("version", "")
                            extras = ",".join(spec.get("extras", []))
                            extras_str = f"[{extras}]" if extras else ""
                            requirements.append(f"{name}{extras_str}{version}")
            except Exception as e:
                print(f"Ошибка чтения pyproject.toml: {e}", file=sys.stderr)

    # 2. setup.cfg
    cfg_path = os.path.join(repo_path, "setup.cfg")
    if os.path.exists(cfg_path) and not requirements:
        cp = configparser.ConfigParser()
        cp.read(cfg_path, encoding="utf-8")
        if cp.has_section("options") and cp.has_option("options", "install_requires"):
            req_str = cp.get("options", "install_requires")
            for line in req_str.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    requirements.append(line)

    # 3. setup.py (через AST)
    sp_path = os.path.join(repo_path, "setup.py")
    if os.path.exists(sp_path) and not requirements:
        with open(sp_path, "r", encoding="utf-8") as f:
            code = f.read()
        try:
            tree = ast.parse(code)

            class SetupVisitor(ast.NodeVisitor):
                def visit_Call(self, node):
                    func = node.func
                    if isinstance(func, (ast.Name, ast.Attribute)):
                        func_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                        if func_name == "setup":
                            for kw in node.keywords:
                                if kw.arg == "install_requires" and isinstance(kw.value, ast.List):
                                    for elt in kw.value.elts:
                                        val = elt.value if hasattr(elt, "value") else elt.s
                                        requirements.append(val)

            SetupVisitor().visit(tree)
        except Exception as e:
            print(f"Ошибка чтения setup.py: {e}", file=sys.stderr)

    return requirements


@contextmanager
def repo_context(mode: str, repo: str):
    if mode == "local-dir":
        yield repo
        return

    with tempfile.TemporaryDirectory() as temp_dir:
        if mode == "remote":
            try:
                subprocess.check_call(["git", "clone", "--depth", "1", "--", repo, temp_dir], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            except subprocess.CalledProcessError as e:
                print(f"Не удалось клонировать репозиторий {repo}: {e}", file=sys.stderr)
                sys.exit(1)
            except FileNotFoundError:
                print("Команда git не найдена. Установите git для режима remote.", file=sys.stderr)
                sys.exit(1)

            # если в корне архива вложенная папка (sdist)
            contents = os.listdir(temp_dir)
            if len(contents) == 1 and os.path.isdir(os.path.join(temp_dir, contents[0])):
                yield os.path.join(temp_dir, contents[0])
            else:
                yield temp_dir

        elif mode == "local-file":
            try:
                shutil.unpack_archive(repo, temp_dir)
            except Exception as e:
                print(f"Не удалось распаковать архив {repo}: {e}", file=sys.stderr)
                sys.exit(1)

            contents = os.listdir(temp_dir)
            nested_dirs = [d for d in contents if os.path.isdir(os.path.join(temp_dir, d))]
            if nested_dirs:
                yield os.path.join(temp_dir, nested_dirs[0])
            else:
                yield temp_dir


parser = argparse.ArgumentParser(description="Анализатор зависимостей Python-пакета (Этап 2)")
parser.add_argument("-p", "--package-name", type=str, required=True, help="Имя анализируемого пакета")
parser.add_argument("-r", "--repo", type=str, required=True, help="URL или путь к репозиторию/архиву")
parser.add_argument("-m", "--mode", choices=["remote", "local-dir", "local-file"], required=True, help="Режим работы")
parser.add_argument("-f", "--filter", dest="filter_substring", type=str, default="", help="Подстрока для фильтрации")

args = parser.parse_args()
validate_args(args)

print("Настроенные параметры:")
print(f"Имя анализируемого пакета: {args.package_name}")
print(f"Репозиторий: {args.repo}")
print(f"Режим работы: {args.mode}")
print(f"Подстрока фильтрации: {args.filter_substring or '(пусто)'}")

with repo_context(args.mode, args.repo) as repo_path:
    requirements = extract_direct_dependencies(repo_path)

    filtered = requirements
    if args.filter_substring:
        filtered = [req for req in requirements if args.filter_substring.lower() in get_dep_name(req).lower()]

    print(f"\nПрямые зависимости пакета «{args.package_name}»"
          f"{' (фильтр «' + args.filter_substring + '»)' if args.filter_substring else ''}:")
    if filtered:
        for req in filtered:
            print(f"  - {req}")
    else:
        print("  Нет зависимостей, соответствующих фильтру.")

# python package_analyzer.py -p fastapi -r https://github.com/tiangolo/fastapi.git -m remote
# python package_analyzer.py -p pendulum -r https://github.com/sdispater/pendulum.git -m remote