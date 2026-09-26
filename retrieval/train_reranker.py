"""Обучение LambdaRank-реранкера top-10 и его честная оценка.

Протокол разделения, ради которого стоит читать этот файл целиком:

- **Обучение и подбор гиперпараметров** идут только на промо/каталожных
  запросах (`rerank_dataset.json`), с группировкой фолдов ПО SLUG — иначе
  два снимка одного вина попали бы в train и valid одновременно и оценка
  была бы завышена.
- **Честные 261 полевой кадр** используются ровно один раз, в конце, уже
  выбранной моделью. Ни одно решение не принимается по ним.

Это прямое следствие §11.25: проект уже один раз принял решение по разнице
в один кадр и был вынужден его отменить.

    python -m retrieval.train_reranker
"""

import json

import numpy as np

from data_prep import config
from .rerank_features import FEATURE_NAMES

BLENDS = (None, 0.002, 0.005, 0.01, 0.02, 0.05)

# Обе библиотеки умеют ранжирование с группами; какая лучше на ~1600 группах —
# вопрос эмпирический, поэтому обе идут через одну и ту же кросс-валидацию.
# Априорный довод за CatBoost: упорядоченный бустинг придуман против
# переобучения на малых выборках. Довод за LightGBM: LambdaRank прямо
# оптимизирует порядок внутри группы. Решает замер, не предпочтение.
CANDIDATE_MODELS = (
    ("lgbm", {"num_leaves": 7, "learning_rate": 0.05, "n_estimators": 150, "min_child_samples": 30}),
    ("lgbm", {"num_leaves": 15, "learning_rate": 0.05, "n_estimators": 200, "min_child_samples": 20}),
    ("lgbm", {"num_leaves": 7, "learning_rate": 0.10, "n_estimators": 100, "min_child_samples": 30}),
    ("lgbm", {"num_leaves": 31, "learning_rate": 0.05, "n_estimators": 300, "min_child_samples": 10}),
    ("catboost", {"depth": 4, "learning_rate": 0.05, "iterations": 300, "loss_function": "YetiRank"}),
    ("catboost", {"depth": 6, "learning_rate": 0.05, "iterations": 400, "loss_function": "YetiRank"}),
    ("catboost", {"depth": 4, "learning_rate": 0.05, "iterations": 300, "loss_function": "PairLogit"}),
    ("catboost", {"depth": 6, "learning_rate": 0.10, "iterations": 200, "loss_function": "QuerySoftMax"}),
)
N_FOLDS = 4
SEED = 42


def load_groups(name: str) -> tuple[list[dict], list[str]]:
    with open(config.OUTPUTS_DIR / name, encoding="utf-8") as f:
        data = json.load(f)
    return data["groups"], data["feature_names"]


def to_matrices(groups: list[dict]) -> tuple[np.ndarray, np.ndarray, list[int]]:
    features = np.array([row for g in groups for row in g["features"]], dtype=np.float32)
    labels = np.array([label for g in groups for label in g["labels"]], dtype=np.int32)
    sizes = [len(g["labels"]) for g in groups]
    return features, labels, sizes


def slug_folds(groups: list[dict], n_folds: int, seed: int) -> list[np.ndarray]:
    """Фолды по истинному slug: все снимки одного вина попадают в один фолд."""
    slugs = sorted({g["true_slug"] for g in groups})
    rng = np.random.default_rng(seed)
    assignment = {slug: int(i) for slug, i in zip(slugs, rng.integers(0, n_folds, len(slugs)))}
    return [np.array([i for i, g in enumerate(groups) if assignment[g["true_slug"]] == fold])
            for fold in range(n_folds)]


def fit(groups: list[dict], library: str, params: dict):
    features, labels, sizes = to_matrices(groups)
    if library == "lgbm":
        import lightgbm as lgb
        model = lgb.LGBMRanker(objective="lambdarank", random_state=SEED, verbose=-1,
                               label_gain=[0, 1], **params)
        model.fit(features, labels, group=sizes)
        return model

    from catboost import CatBoostRanker, Pool
    group_ids = np.repeat(np.arange(len(sizes)), sizes)
    model = CatBoostRanker(random_seed=SEED, verbose=False, allow_writing_files=False, **params)
    model.fit(Pool(features, labels, group_id=group_ids))
    return model


def predict_scores(model, group: dict) -> np.ndarray:
    return np.asarray(model.predict(np.array(group["features"], dtype=np.float32))).ravel()


def top1_accuracy(model, groups: list[dict], blend: float | None = None) -> float:
    """Доля запросов, где после реранкинга на первом месте верный slug.
    Запросы без верного ответа в списке остаются в знаменателе.

    `blend=None` — порядок полностью задаёт модель. Иначе скор модели
    используется как ПОПРАВКА к косинусу: `косинус + blend * z(скор)`,
    где z — нормировка внутри группы. Так косинусный порядок сохраняется
    везде, кроме мест, где модель уверена, и слабая модель не может
    разрушить всю выдачу.
    """
    cosine_col = FEATURE_NAMES.index("score")
    hits = 0
    for g in groups:
        scores = predict_scores(model, g)
        if blend is not None:
            std = float(np.std(scores))
            normalized = (scores - float(np.mean(scores))) / (std if std > 1e-9 else 1.0)
            cosine = np.array([row[cosine_col] for row in g["features"]], dtype=np.float64)
            scores = cosine + blend * normalized
        hits += int(g["slugs"][int(np.argmax(scores))] == g["true_slug"])
    return hits / len(groups)


def baseline_top1(groups: list[dict]) -> float:
    return sum(g["slugs"][0] == g["true_slug"] for g in groups) / len(groups)


def main() -> None:
    train_groups, feature_names = load_groups("rerank_dataset.json")
    print(f"Обучающих групп: {len(train_groups)}, признаков: {len(feature_names)}")
    print(f"Базовый top-1 на обучающих запросах: {baseline_top1(train_groups):.1%}\n")

    folds = slug_folds(train_groups, N_FOLDS, SEED)
    cv_baseline = float(np.mean([baseline_top1([train_groups[i] for i in fold])
                                 for fold in folds if len(fold)]))
    print(f"Базовый top-1 по тем же фолдам: {cv_baseline:.1%}\n")

    best = None
    for library, params in CANDIDATE_MODELS:
        fold_models = []
        for fold in folds:
            valid_idx = set(fold.tolist())
            train_part = [g for i, g in enumerate(train_groups) if i not in valid_idx]
            valid_part = [train_groups[i] for i in fold]
            if valid_part and train_part:
                fold_models.append((fit(train_part, library, params), valid_part))

        label = ", ".join(f"{k}={v}" for k, v in params.items())
        for blend in BLENDS:
            mean = float(np.mean([top1_accuracy(m, v, blend) for m, v in fold_models]))
            tag = "замена" if blend is None else f"поправка w={blend}"
            print(f"  {library:9s} {tag:18s} {label[:52]:54s} "
                  f"CV {mean:.1%} ({(mean - cv_baseline) * 100:+.1f}пп)")
            if best is None or mean > best[0]:
                best = (mean, library, params, blend)

    print(f"\nЛучшее по CV: {best[1]}, blend={best[3]}, {best[2]} -> {best[0]:.1%} "
          f"({(best[0] - cv_baseline) * 100:+.1f}пп к косинусу)")
    model = fit(train_groups, best[1], best[2])

    importances = np.ravel(np.asarray(model.feature_importances_, dtype=float))
    if importances.size == len(feature_names):
        print("\nВажность признаков:")
        for name, importance in sorted(zip(feature_names, importances), key=lambda x: -x[1]):
            print(f"  {name:26s} {importance:.1f}")

    print("\n" + "=" * 62)
    print("ЧЕСТНАЯ ОЦЕНКА НА ПОЛЕВЫХ КАДРАХ (данные ни разу не влияли на выбор)")
    print("=" * 62)
    combined: list[dict] = []
    for name, title in (("rerank_field_own.json", "205 фото команды"),
                        ("rerank_field_org.json", "56 фото организатора")):
        try:
            groups, _ = load_groups(name)
        except FileNotFoundError:
            print(f"  нет {name} — сначала build_rerank_dataset --queries ...")
            continue
        combined.extend(groups)
        base, ranked = baseline_top1(groups), top1_accuracy(model, groups, best[3])
        print(f"  {title:22s} n={len(groups):>3d}  было {base:>6.1%}  стало {ranked:>6.1%}"
              f"  ({(ranked - base) * 100:+.1f}пп)")
    if combined:
        base, ranked = baseline_top1(combined), top1_accuracy(model, combined, best[3])
        print(f"  {'ВСЕГО':22s} n={len(combined):>3d}  было {base:>6.1%}  стало {ranked:>6.1%}"
              f"  ({(ranked - base) * 100:+.1f}пп)")

        fixed = broken = 0
        for g in combined:
            scores = predict_scores(model, g)
            was = g["slugs"][0] == g["true_slug"]
            now = g["slugs"][int(np.argmax(scores))] == g["true_slug"]
            fixed += now and not was
            broken += was and not now
        print(f"  исправлено {fixed}, сломано {broken}, чистый эффект {fixed - broken:+d} кадров")
        print("\n  Напоминание §11.25: чистый эффект меньше 3-4 кадров — не результат.")

    suffix = "lgbm" if best[1] == "lgbm" else "catboost"
    out_path = config.OUTPUTS_DIR / f"reranker_{suffix}.cbm"
    if best[1] == "lgbm":
        out_path = config.OUTPUTS_DIR / "reranker_lgbm.txt"
        model.booster_.save_model(str(out_path))
    else:
        model.save_model(str(out_path))
    print(f"\nМодель: {out_path}")


if __name__ == "__main__":
    main()
