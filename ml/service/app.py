"""HTTP-сервис поиска вина по фотографии этикетки.

Контракт скрипта оценки кейсодержателя (`eval/participant_test.sh`):
принять `multipart/form-data` с полем `image` на `/v1/eval/predict`, ответить
`{"slug": "..."}`. Запросы идут последовательно; скрипт меряет время сам,
таймаут соединения 5 с, общий 10 с. Невалидный slug (в том числе null) скрипт
пишет в predictions.jsonl как `predicted_slug: null`; метрику и уверенность
считают организаторы.

Бэкенд (WINE_BACKEND):
  qwen   (по умолчанию) — Qwen3-VL-Embedding-8B + LoRA v2, несколько эталонов на вино,
         запрос 2560 токенов; нужен GPU. См. service/qwen_finder.py.
  siglip — прежний пайплайн SigLIP2 (retrieval/predict.py), работает на CPU.

«На фото нет вина» (NO_WINE_THRESHOLD): если задан и схожесть кадра с лучшим эталоном
ниже порога, /v1/eval/predict отвечает `{"slug": null}`. По умолчанию выключен: пока
организаторы не подтвердили, что в тесте есть фото без вина и null там засчитывается
как верный ответ, отказ только теряет балл. Для Qwen рекомендованный порог 0.60
(0 из 379 фото вина отклонено, 147 из 152 фото без вина отсечено). Для SigLIP2 порог
не калиброван. /v1/search всегда отдаёт признак `no_wine` по тому же порогу (0.60 для
Qwen, если не задан), чтобы интерфейс мог показать «вина не найдено».

Запуск:
    uvicorn service.app:app --host 0.0.0.0 --port 8080
    QWEN_DIR=/workspace/qwen_hires uvicorn service.app:app --host 0.0.0.0 --port 8080
    NO_WINE_THRESHOLD=0.60 uvicorn service.app:app ...
"""

import io
import logging
import os
import time

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image, ImageOps

BACKEND = os.environ.get("WINE_BACKEND", "qwen").lower()
_thr = os.environ.get("NO_WINE_THRESHOLD", "").strip()
NO_WINE_THRESHOLD = float(_thr) if _thr else None
UI_NO_WINE_THRESHOLD = NO_WINE_THRESHOLD if NO_WINE_THRESHOLD is not None else (0.60 if BACKEND == "qwen" else None)

# SigLIP2 ужимает вход до 512px, поэтому снимок 3024x4032 уменьшаем сразу — декод дешевле.
# Qwen берёт кадр целиком (2560 токенов ~ 2.6 Мп, ужимает сам): уменьшение заранее
# меняет пиксели относительно измеренного пайплайна, поэтому режем только гигантов.
MAX_SIDE = 1280 if BACKEND == "siglip" else 4096
TOP_K = 5

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("wine-scanner")

app = FastAPI(title="Сканер российских вин", version="2.0")
# Фронт команды ходит в сервис из браузера с другого домена — разрешаем CORS.
# CORS_ORIGINS="https://site1,https://site2" сужает список; по умолчанию — любой источник.
_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET", "POST", "OPTIONS"],
                   allow_headers=["*"])
_finder = None


def get_finder():
    global _finder
    if _finder is None:
        logger.info("загружаем индекс и модель (%s)...", BACKEND)
        t0 = time.perf_counter()
        if BACKEND == "qwen":
            from service.qwen_finder import QwenFinder
            _finder = QwenFinder()
            size = len(_finder.slugs)
        else:
            from retrieval.predict import WineFinder
            _finder = WineFinder()
            size = len(_finder.lookup)
        logger.info("готово за %.1f с, вин в индексе: %d, порог «нет вина»: %s",
                    time.perf_counter() - t0, size, NO_WINE_THRESHOLD)
    return _finder


@app.on_event("startup")
def warmup() -> None:
    """Прогреваем на старте: первый инференс тяжелее последующих, а скрипт
    оценки меряет время с первого же запроса."""
    finder = get_finder()
    for size in ((512, 512), (3024, 4032)):
        finder.predict(Image.new("RGB", size, "white"), top_k=1)
    logger.info("прогрев завершён")


def load_image(payload: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(payload))
    image = ImageOps.exif_transpose(image)  # фото с телефона часто «лежит на боку»
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGBA", image.size, (255, 255, 255, 255))
        image = Image.alpha_composite(background, image)
    image = image.convert("RGB")
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    return image


async def read_and_predict(image: UploadFile):
    payload = await image.read()
    try:
        picture = load_image(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"не удалось прочитать изображение: {exc}")
    t0 = time.perf_counter()
    results = get_finder().predict(picture, top_k=TOP_K)
    elapsed = (time.perf_counter() - t0) * 1000
    if not results:
        raise HTTPException(status_code=500, detail="пустой результат поиска")
    return results, elapsed


@app.get("/health")
def health() -> dict:
    finder = get_finder()
    size = len(finder.slugs) if BACKEND == "qwen" else len(finder.lookup)
    return {"status": "ok", "backend": BACKEND, "catalog_size": size,
            "no_wine_threshold": NO_WINE_THRESHOLD}


@app.post("/v1/eval/predict")
async def eval_predict(image: UploadFile = File(...)) -> dict:
    """Эндпоинт скрипта оценки: плоский ответ с одним лучшим slug."""
    results, elapsed = await read_and_predict(image)
    best = results[0]
    if NO_WINE_THRESHOLD is not None and best.score < NO_WINE_THRESHOLD:
        logger.info("%s -> нет вина (лучший %s %.3f < %.2f, %.0f мс)",
                    image.filename, best.slug, best.score, NO_WINE_THRESHOLD, elapsed)
        return {"slug": None}
    logger.info("%s -> %s (%.3f, %.0f мс)", image.filename, best.slug, best.score, elapsed)
    return {"slug": best.slug}


@app.post("/v1/search")
async def search(image: UploadFile = File(...)) -> dict:
    """Расширенный ответ для интерфейса: карточка вина, конкуренты и уверенность."""
    results, elapsed = await read_and_predict(image)
    best = results[0]
    gap = best.score - results[1].score if len(results) > 1 else best.score
    no_wine = UI_NO_WINE_THRESHOLD is not None and best.score < UI_NO_WINE_THRESHOLD

    def card(prediction) -> dict:
        record = prediction.record or {}
        return {
            "slug": prediction.slug,
            "name": record.get("name"),
            "winery": record.get("winery"),
            "region": record.get("region"),
            "grape": record.get("grape"),
            "category": record.get("category_color"),
            "color": record.get("color_desc"),
            "description": record.get("description"),
            "score": round(prediction.score, 4),
        }

    confidence = {"top1_score": round(best.score, 4), "gap_top1_top2": round(gap, 4)}
    if hasattr(best, "embedding_score"):
        confidence["embedding_score"] = round(best.embedding_score, 4)
        confidence["orb_score"] = round(best.orb_score, 4)
    return {
        "slug": None if no_wine else best.slug,
        "no_wine": no_wine,
        "card": None if no_wine else card(best),
        "alternatives": [card(p) for p in (results if no_wine else results[1:])],
        "confidence": confidence,
        "latency_ms": round(elapsed),
    }
