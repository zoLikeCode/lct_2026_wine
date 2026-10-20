"""Транслитерация кириллицы в латиницу по правилам, которые применяет Strapi
при сохранении загруженного файла.

Нужно, чтобы сопоставить значение колонки «Название фото» из дампа CSV
(например `Агора_Блэк Стоун.webp`) с тем, как файл реально лежит в uploads/
(`Agora_Blek_Stoun_e98339ae1b.webp`).
"""

import re

_CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def transliterate(text: str) -> str:
    return "".join(_CYRILLIC_TO_LATIN.get(ch.lower(), ch) for ch in text)


def normalize_filename(text: str) -> str:
    """Приводит имя файла к сравнимому виду: транслитерация, всё не
    буквенно-цифровое -> `_`, нижний регистр.

    `Агора Резерв Яхтинг Шардоне.webp` -> `agora_rezerv_yahting_shardone`
    """
    stem = re.sub(r"\.(webp|jpg|jpeg|png)$", "", text, flags=re.I)
    return re.sub(r"[^A-Za-z0-9]+", "_", transliterate(stem)).strip("_").lower()
