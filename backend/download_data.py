"""Загрузка и распаковка датасета MovieLens 32M (скрипт бэкенда).

Скачивает архив https://files.grouplens.org/datasets/movielens/ml-32m.zip
(~250 МБ в сжатом виде, ~1.1 ГБ после распаковки) и распаковывает СТРОГО три
файла — ``movies.csv``, ``links.csv``, ``ratings.csv`` — в корневую папку
``data/`` проекта. Архив удаляется сразу после успешной распаковки.

Путь к ``data/`` вычисляется от расположения этого файла
(``backend/download_data.py`` -> корень репозитория), поэтому скрипт можно
запускать из любого рабочего каталога::

    python backend/download_data.py

Особенности:
* потоковая загрузка через ``requests`` с прогресс-баром ``tqdm``;
* докачка после обрыва связи (HTTP Range) и авто-повторы на 429/5xx;
* распаковка тоже потоковая (``ratings.csv`` ~836 МБ — в память не грузим);
* повторный запуск не скачивает то, что уже распаковано.

Флаги::

    python backend/download_data.py --help
    python backend/download_data.py --force          # скачать заново
    python backend/download_data.py --keep-archive   # не удалять .zip
    python backend/download_data.py --no-resume      # начать загрузку с нуля
"""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

#: Корень репозитория: backend/download_data.py -> parents[1].
REPO_ROOT = Path(__file__).resolve().parent.parent

DATASET_URL = "https://files.grouplens.org/datasets/movielens/ml-32m.zip"
DATA_DIR = REPO_ROOT / "data"
ARCHIVE_NAME = "ml-32m.zip"

#: Файлы, которые реально нужны проекту (остальное из архива не распаковываем).
REQUIRED_FILES = ("movies.csv", "links.csv", "ratings.csv")

CHUNK_SIZE = 1024 * 1024  # 1 МиБ
CONNECT_TIMEOUT = 30.0    # секунд на установку соединения
READ_TIMEOUT = 300.0      # секунд на чтение очередного чанка


def _build_session() -> requests.Session:
    """Сессия requests с автоматическими повторами временных ошибок."""
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        raise_on_status=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "rexter-downloader/1.0"})
    return session


def download_archive(
    url: str,
    dest: Path,
    *,
    session: requests.Session | None = None,
    resume: bool = True,
) -> Path:
    """Скачать архив ``url`` в файл ``dest`` с прогресс-баром.

    Если ``dest`` уже существует и ``resume=True`` — докачивает остаток через
    заголовок ``Range`` (если сервер поддерживает ``206 Partial Content``),
    иначе перезаписывает файл с нуля.
    """
    session = session or _build_session()
    dest.parent.mkdir(parents=True, exist_ok=True)

    resume_from = dest.stat().st_size if (resume and dest.exists()) else 0
    headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}

    with session.get(
        url, headers=headers, stream=True, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)
    ) as response:
        response.raise_for_status()

        # Сервер не умеет докачку и вернул 200 на запрос с Range — начинаем заново.
        if resume_from and response.status_code != 206:
            resume_from = 0
        mode = "ab" if resume_from else "wb"

        content_length = response.headers.get("Content-Length")
        total = resume_from + int(content_length) if content_length else None

        with open(dest, mode) as file, tqdm(
            total=total,
            initial=resume_from,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc="Downloading",
            disable=None,  # без бара, если вывод перенаправляется в файл/пайп
        ) as progress:
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if not chunk:
                    continue
                file.write(chunk)
                progress.update(len(chunk))

    return dest


def extract_required(
    archive_path: Path,
    output_dir: Path,
    required: tuple[str, ...] = REQUIRED_FILES,
) -> list[Path]:
    """Извлечь из архива СТРОГО ``required`` файлы в ``output_dir``.

    Потоковая распаковка: файлы по байтам копируются из архива на диск, поэтому
    гигабайтный ``ratings.csv`` не поднимается в память целиком.

    Returns:
        Список путей к извлечённым файлам.

    Raises:
        RuntimeError: Если в архиве нет какого-то из обязательных файлов.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    wanted = {name.lower() for name in required}

    with zipfile.ZipFile(archive_path) as archive:
        members: dict[str, zipfile.ZipInfo] = {}
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if name.lower() in wanted and name.lower() not in members:
                members[name.lower()] = info

        missing = wanted - set(members)
        if missing:
            raise RuntimeError(
                f"В архиве отсутствуют обязательные файлы: {', '.join(sorted(missing))}"
            )

        total = sum(info.file_size for info in members.values())
        extracted: list[Path] = []

        with tqdm(
            total=total,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc="Extracting",
            disable=None,
        ) as progress:
            for info in sorted(members.values(), key=lambda item: item.filename):
                target = output_dir / Path(info.filename).name
                with archive.open(info) as source, open(target, "wb") as destination:
                    shutil.copyfileobj(source, destination, length=CHUNK_SIZE)
                    progress.update(info.file_size)
                extracted.append(target)

    return extracted


def _already_extracted(data_dir: Path) -> bool:
    """Все обязательные файлы уже на месте?"""
    return all((data_dir / name).exists() for name in REQUIRED_FILES)


def run(
    url: str = DATASET_URL,
    data_dir: Path | str = DATA_DIR,
    *,
    force: bool = False,
    resume: bool = True,
    keep_archive: bool = False,
) -> int:
    """Скачать и распаковать датасет. Возвращает код выхода (0 — успех)."""
    data_dir = Path(data_dir)
    archive_path = data_dir / ARCHIVE_NAME

    if not force and _already_extracted(data_dir):
        files = ", ".join(str(data_dir / name) for name in REQUIRED_FILES)
        print(f"Датасет уже распакован: {files}")
        print("Используйте --force, чтобы скачать заново.")
        return 0

    data_dir.mkdir(parents=True, exist_ok=True)
    session = _build_session()
    try:
        print(f"Downloading... {url}")
        download_archive(url, archive_path, session=session, resume=resume)
        size_mb = archive_path.stat().st_size / 1024 ** 2
        print(f"Downloaded: {archive_path} ({size_mb:.1f} MB)")

        print("Extracting...")
        extracted = extract_required(archive_path, data_dir)
        for path in extracted:
            print(f"  - {path} ({path.stat().st_size / 1024 ** 2:.1f} MB)")
    finally:
        session.close()

    if keep_archive:
        print(f"Archive kept: {archive_path}")
    elif archive_path.exists():
        archive_path.unlink()
        print(f"Archive removed: {archive_path}")

    print("Done!")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Скачать и распаковать MovieLens 32M (строго movies/links/ratings.csv) в data/."
    )
    parser.add_argument("--url", default=DATASET_URL, help="URL архива с датасетом")
    parser.add_argument(
        "--output-dir", default=str(DATA_DIR), help="куда распаковывать (по умолчанию <repo>/data)"
    )
    parser.add_argument("--force", action="store_true", help="скачать и распаковать заново")
    parser.add_argument(
        "--no-resume", action="store_true", help="не докачивать, начать загрузку с нуля"
    )
    parser.add_argument("--keep-archive", action="store_true", help="не удалять .zip после распаковки")
    args = parser.parse_args(argv)

    return run(
        url=args.url,
        data_dir=args.output_dir,
        force=args.force,
        resume=not args.no_resume,
        keep_archive=args.keep_archive,
    )


if __name__ == "__main__":
    raise SystemExit(main())
