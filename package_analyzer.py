import argparse
import os
import sys
from urllib.parse import urlparse


def validate_args(args: argparse.Namespace):
    errors = []

    # 1. Имя пакета
    if not args.package_name:
        errors.append("Имя анализируемого пакета не может быть пустым.")
    elif not args.package_name.replace("-", "").replace("_", "").isalnum():
        errors.append(
            "Имя пакета содержит недопустимые символы. "
            "Разрешены только буквы, цифры, дефис (-) и подчёркивание (_)."
        )

    # 2. Репозиторий + режим
    repo = args.repo
    mode = args.mode

    if mode == "remote":
        parsed = urlparse(repo)
        if not parsed.scheme or parsed.scheme not in ("http", "https", "git"):
            errors.append(
                "В режиме 'remote' репозиторий должен быть валидным URL"
            )
    elif mode == "local-dir":
        if not os.path.isdir(repo):
            errors.append(
                f"В режиме 'local-dir' указанный путь '{repo}' не является существующей директорией."
            )
    elif mode == "local-file":
        if not os.path.isfile(repo):
            errors.append(
                f"В режиме 'local-file' указанный путь '{repo}' не является существующим файлом."
            )

    # 3. Фильтр
    if hasattr(args, "filter_substring") and args.filter_substring is not None:
        if not isinstance(args.filter_substring, str):
            errors.append("Подстрока фильтрации должна быть строкой.")

    if errors:
        print("Ошибки в параметрах:", file=sys.stderr)
        for err in errors:
            print(f"  • {err}", file=sys.stderr)
        sys.exit(1)

parser = argparse.ArgumentParser()

parser.add_argument(
    "-p", "--package-name",
    type=str,
    required=True
)

parser.add_argument(
    "-r", "--repo",
    type=str,
    required=True
)

parser.add_argument(
    "-m", "--mode",
    choices=["remote", "local-dir", "local-file"],
    required=True
)

parser.add_argument(
    "-f", "--filter",
    dest="filter_substring",
    type=str,
    default=""
)

args = parser.parse_args()

# Валидация после парсинга
validate_args(args)

# Вывод всех параметров в формате ключ-значение
print("Настроенные параметры:")
print(f"Имя анализируемого пакета: {args.package_name}")
print(f"Репозиторий              : {args.repo}")
print(f"Режим работы             : {args.mode}")
print(f"Подстрока фильтрации     : {args.filter_substring or '(пусто)'}")

# python package_analyzer.py -p "requests" -r "https://github.com/psf/requests.git" -m remote -f "http"
# python package_analyzer.py -p "mypackage" -r "./test_repo" -m local-dir -f "test"
# python package_analyzer.py -p "mypackage" -r "./test_file.txt" -m local-file
# python package_analyzer.py -p "numpy" -r "https://github.com/numpy/numpy.git" -m remote