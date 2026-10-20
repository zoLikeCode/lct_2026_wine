"""Пути к сырым данным. Переопределяются переменными окружения, чтобы пайплайн
не был жёстко привязан к одной машине."""

import os
from pathlib import Path

DATASET_DIR = Path(os.environ.get(
    "WINE_DATASET_DIR",
    os.path.expanduser("~/Downloads/Датасет"),
))
CSV_PATH = Path(os.environ.get(
    "WINE_CSV_PATH",
    DATASET_DIR / "strapi_output0709.csv",
))
UPLOADS_DIR = Path(os.environ.get(
    "WINE_UPLOADS_DIR",
    DATASET_DIR / "prod-svoe-vino/strapi/uploads",
))
HAS_WINE_DIR = Path(os.environ.get(
    "WINE_HAS_WINE_DIR",
    os.path.expanduser("~/Downloads/has_wine"),
))
NO_WINE_DIR = Path(os.environ.get(
    "WINE_NO_WINE_DIR",
    os.path.expanduser("~/Downloads/no_wine"),
))

OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "outputs"

SIZE_PREFIXES = ("thumbnail_", "small_", "medium_", "large_")
FUZZY_THRESHOLD = 82
