"""HTTP-сервис поиска вина по фотографии этикетки.

Контракт скрипта оценки кейсодержателя (`eval/participant_test.sh`):
принять `multipart/form-data` с полем `image`, ответить `{"slug": "..."}`.
Скрипт ждёт полный ответ и меряет время сам; таймаут соединения 5 с, общий 10 с.

Ответ всегда содержит slug: метрика — accuracy без штрафа за ошибку, поэтому
отказ «не найдено» только теряет балл. Уверенность отдаётся отдельными полями,
чтобы интерфейс мог решать сам, показывать ли карточку сразу.

Запуск:
    uvicorn service.app:app --host 127.0.0.1 --port 8080
"""

import io
import logging
import time

from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image, ImageOps

from retrieval.predict import WineFinder

# Снимок с телефона приходит в 3024x4032 и декодируется сотни миллисекунд.
# Для эмбеддинга столько не нужно — модель всё равно ужимает вход до 512px.
MAX_SIDE = 1280
TOP_K = 5

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("wine-scanner")

app = FastAPI(title="Сканер российских вин", version="1.0")
_finder: WineFinder | None = None


def get_finder() -> WineFinder:
    global _finder
    if _finder is None:
        logger.info("загружаем индекс и модель...")
        t0 = time.perf_counter()
        _finder = WineFinder()
        logger.info("готово за %.1f с, позиций в индексе: %d",
                    time.perf_counter() - t0, len(_finder.lookup))
    return _finder


@app.on_event("startup")
def warmup() -> None:
    """Прогреваем на старте: первый инференс тяжелее последующих, а скрипт
    оценки меряет время с первого же запроса."""
    finder = get_finder()
    finder.predict(Image.new("RGB", (512, 512), "white"), top_k=1)
    logger.info("прогрев завершён")


def load_image(payload: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(payload))
    image = ImageOps.exif_transpose(image)  # фото с телефона часто «лежит на боку»
    image = image.convert("RGB")
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    return image


@app.get("/health")
def health() -> dict:
    finder = get_finder()
    return {"status": "ok", "catalog_size": len(finder.lookup)}


@app.post("/v1/eval/predict")
async def eval_predict(image: UploadFile = File(...)) -> dict:
    """Эндпоинт скрипта оценки: плоский ответ с одним лучшим slug."""
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

    best = results[0]
    logger.info("%s -> %s (%.0f мс)", image.filename, best.slug, elapsed)
    return {"slug": best.slug}


@app.post("/v1/search")
async def search(image: UploadFile = File(...)) -> dict:
    """Расширенный ответ для интерфейса: карточка вина, конкуренты и уверенность."""
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

    best = results[0]
    gap = best.score - results[1].score if len(results) > 1 else best.score

    def card(prediction) -> dict:
        record = prediction.record
        return {
            "slug": prediction.slug,
            "name": record["name"],
            "winery": record["winery"],
            "region": record["region"],
            "grape": record["grape"],
            "category": record["category_color"],
            "color": record["color_desc"],
            "description": record["description"],
            "score": round(prediction.score, 4),
        }

    return {
        "slug": best.slug,
        "card": card(best),
        "alternatives": [card(p) for p in results[1:]],
        "confidence": {
            "top1_score": round(best.score, 4),
            "gap_top1_top2": round(gap, 4),
            "embedding_score": round(best.embedding_score, 4),
            "orb_score": round(best.orb_score, 4),
        },
        "latency_ms": round(elapsed),
    }
