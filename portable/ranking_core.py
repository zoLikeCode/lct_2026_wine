# Exact runtime functions extracted from the selected Mac pipeline; no Torch dependency.
import re, unicodedata
import numpy as np
import cv2
from sklearn.feature_extraction.text import TfidfVectorizer


TRANSLIT = str.maketrans(dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a","b","v","g","d","e","e","zh","z","i","y","k","l","m","n","o","p","r","s","t","u","f","h","ts","ch","sh","sch","","y","","e","yu","ya"])))

def normalize_text(text):
    text = unicodedata.normalize("NFKD", text.lower().replace("ё", "е"))
    text = "".join(c for c in text if not unicodedata.combining(c)).translate(TRANSLIT)
    # Both label and catalogue use these common language variants.
    replacements = {"cabernet": "kaberne", "sauvignon": "sovinon", "pinot": "pino", "noir": "nuar",
        "chardonnay": "shardone", "merlot": "merlo", "riesling": "risling", "syrah": "sira",
        "shiraz": "sira", "shiraz": "sira", "muscat": "muskat", "brut": "bryut", "reserve": "rezerv",
        "blanc": "blan", "franc": "fran", "chateau": "shato", "massandra": "massandra"}
    for a, b in replacements.items():
        text = re.sub(r"\b" + a + r"\b", b, text)
    return " ".join(re.findall(r"[a-z0-9]+", text))

def ocr_text(lines):
    return normalize_text(" ".join(l["text"] for l in lines if l["confidence"] >= .3))

GRAPES = ("kaberne sovinon", "kaberne fran", "pino nuar", "pino gri", "sovinon blan", "shardone",
          "merlo", "saperavi", "risling", "aligote", "kokur", "muskat", "krasnostop", "sira", "rkatsiteli",
          "vione", "malbek", "peti verdo", "sibir", "golubok", "traminer", "mersen", "malvaziya")

def grapes(text):
    return {g for g in GRAPES if g in text}

def years(text):
    return {int(x) for x in re.findall(r"\b(?:19|20)\d{2}\b", text) if 1950 <= int(x) <= 2026}

def attribute_features(query, reference):
    qg, rg, qy, ry = grapes(query), grapes(reference), years(query), years(reference)
    return [len(qg & rg)/max(1, len(qg | rg)), float(bool(qg and rg and not qg & rg)),
            float(bool(qy & ry)), float(bool(qy and ry and not qy & ry))]

class TextBank:
    def __init__(self, slugs, catalog, references, reference_lines):
        self.slugs = slugs
        self.reference_texts = [ocr_text(reference_lines[r["id"]]) for r in references]
        self.catalog_texts = [normalize_text(" ".join(str(catalog[s].get(k, "")) for k in
            ["Название вина", "Винодельня", "Сорт винограда"]) + " " + s) for s in slugs]
        self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3,5), min_df=1, sublinear_tf=True,
                                        dtype=np.float32)
        all_texts = self.reference_texts+self.catalog_texts
        matrix = self.vectorizer.fit_transform(all_texts)
        self.ref_matrix, self.catalog_matrix = matrix[:len(references)], matrix[len(references):]
        self.ref_slug_indices = np.array([slugs.index(r["slug"]) for r in references])
        self.attribute_texts = []
        for i, s in enumerate(slugs):
            self.attribute_texts.append(self.catalog_texts[i]+" "+" ".join(
                text for text, j in zip(self.reference_texts, self.ref_slug_indices) if j == i))

    def scores(self, lines):
        text = ocr_text(lines)
        q = self.vectorizer.transform([text])
        refs = (self.ref_matrix @ q.T).toarray().ravel()
        reference = np.zeros(len(self.slugs), np.float32)
        np.maximum.at(reference, self.ref_slug_indices, refs)
        catalog = (self.catalog_matrix @ q.T).toarray().ravel()
        return text, reference, catalog

def candidate_features(channels, query_lines, text_bank, query_local, ref_local, ref_ids, topk=20):
    # Candidate generation is independent of the query's ground-truth slug.
    g, multi, focus, cross = channels
    blend = .5*g+.5*focus
    text, text_ref, text_catalog = text_bank.scores(query_lines)
    proposals = set(np.argsort(-g, kind="stable")[:topk])
    proposals.update(np.argsort(-blend, kind="stable")[:topk])
    proposals.update(np.argsort(-focus, kind="stable")[:topk])
    proposals.update(np.argsort(-multi, kind="stable")[:5])
    # Text can recover a missed visual candidate, but never adds a hard label constraint.
    if len(text) >= 5:
        proposals.update(np.argsort(-(text_ref+text_catalog), kind="stable")[:5])
    indices = np.array(sorted(proposals), dtype=int)
    base = np.stack([g, multi, focus, cross, blend, text_ref, text_catalog], axis=1)[indices]
    attr = np.array([attribute_features(text, text_bank.attribute_texts[j]) for j in indices], np.float32)
    geom = []
    for j in indices:
        candidates = [geometry(query_local, ref_local[image_id]) for image_id in ref_ids[j]]
        # Same chosen geometric pair supplies all geometric features.
        geom.append(max(candidates, key=lambda z: z[0]*min(1., 5*min(z[2], z[3]))))
    return indices, np.column_stack([base, attr, geom]).astype(np.float32)

def visual_channels(query_vectors, reference_vectors, reference_slug_indices, primary_count):
    g = reference_vectors[:,0] @ query_vectors[0]
    f = reference_vectors[:,1] @ query_vectors[1]
    cross = np.maximum(reference_vectors[:,0] @ query_vectors[1], reference_vectors[:,1] @ query_vectors[0])
    channels = []
    for values in (g, f, cross):
        scores = np.full(primary_count, -1., np.float32)
        np.maximum.at(scores, reference_slug_indices, values)
        channels.append(scores)
    return np.stack([g[:primary_count], *channels])

def scores_for(features, recipe):
    f = np.asarray(features)
    if recipe["kind"] == "linear":
        return f @ np.array(recipe["weights"])
    if recipe["kind"] == "pairwise_linear":
        return f @ np.array(recipe["weights"])
    raise ValueError("Unknown rerank recipe")

def guard_label_source(original, crop, info):
    """Reject a neck-only detection on a single isolated studio bottle.

    The legacy bottle embedding remains unchanged for a faithful v1 control.
    OCR and the extra label view use the recovered full image.
    """
    unchanged = {"guarded": False, "source_bbox": info["bbox"]}
    if info.get("selection") != "central_bottle" or info.get("detections") != 1:
        return crop, unchanged
    small = original.copy(); small.thumbnail((800,800))
    a = np.asarray(small)
    border = np.concatenate([a[0],a[-1],a[:,0],a[:,-1]])
    if np.mean(np.min(border,axis=1)>240) < .7:
        return crop, unchanged
    mask = np.any(a < 230,axis=2)
    yy,xx = np.where(mask)
    if len(xx)<100 or mask.mean()>.82 or (yy.max()-yy.min())/max(1,xx.max()-xx.min())<1.8:
        return crop, unchanged
    sy = original.height/small.height
    foreground_top,foreground_bottom = yy.min()*sy, (yy.max()+1)*sy
    b = info["bbox"]
    overlap=max(0,min(b[3],foreground_bottom)-max(b[1],foreground_top))
    if overlap/(foreground_bottom-foreground_top)>=.75:
        return crop, unchanged
    return original, {"guarded": True, "source_bbox": [0,0,original.width,original.height],
                      "reason": "single_studio_bottle_detection_cuts_off_body", "legacy_bbox": b}

def focus_image(image, lines):
    """Conservative extra view; always retain the original bottle view as a candidate."""
    w, h = image.size
    arr = np.asarray(image)
    mask = np.any(arr < 235, axis=2)
    yy, xx = np.where(mask)
    bounds = [0, 0, w, h]
    # Remove blank studio margins; no generative reconstruction of label content.
    if len(xx) > 100 and mask.mean() < .82:
        bounds = [max(0, int(xx.min())-4), max(0, int(yy.min())-4),
                  min(w, int(xx.max())+5), min(h, int(yy.max())+5)]
    x0, y0, x1, y1 = bounds
    bw, bh = x1-x0, y1-y0
    if bh / max(bw, 1) > 1.8:
        candidates = []
        for line in lines:
            b = line.get("bbox")
            if not b or line["confidence"] < .3 or len(line["text"].strip()) < 2:
                continue
            b = [b[0]*w, b[1]*h, b[2]*w, b[3]*h]
            cy = (b[1]+b[3])/2
            if y0+.32*bh < cy < y0+.94*bh and b[2] > x0 and b[0] < x1:
                candidates.append(b)
        # Whole bottle body is safer when text is not detected.
        top, bottom = y0+.34*bh, y0+.96*bh
        if len(candidates) >= 2:
            top = max(y0+.27*bh, min(b[1] for b in candidates)-.08*bh)
            bottom = min(y1, max(b[3] for b in candidates)+.08*bh)
            if bottom-top < .24*bh:
                top, bottom = y0+.34*bh, y0+.96*bh
        bounds = [x0, int(top), x1, int(bottom)]
    return image.crop(tuple(bounds)), {"bbox_in_bottle": bounds, "method": "text_body_focus_v2"}

def local_features(image):
    cv2.setNumThreads(1)
    im = image.copy()
    im.thumbnail((800, 800))
    gray = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2GRAY)
    kp, des = cv2.SIFT_create(nfeatures=650, contrastThreshold=.025).detectAndCompute(gray, None)
    xy = np.asarray([k.pt for k in kp], np.float32).reshape(-1, 2)
    des = np.zeros((0, 128), np.float32) if des is None else np.sqrt(des/(des.sum(axis=1, keepdims=True)+1e-7))
    return {"xy": xy, "des": des, "shape": np.array(gray.shape),
            "scales": np.asarray([k.size for k in kp], np.float32),
            "responses": np.asarray([k.response for k in kp], np.float32),
            "oris": np.deg2rad(np.asarray([k.angle for k in kp], np.float32))}

def geometry(a, b):
    if min(len(a["des"]), len(b["des"])) < 8:
        return np.zeros(4, np.float32)
    matches = cv2.BFMatcher(cv2.NORM_L2).knnMatch(a["des"], b["des"], k=2)
    good = [pair[0] for pair in matches if len(pair) == 2 and pair[0].distance < .73*pair[1].distance]
    if len(good) < 8:
        return np.zeros(4, np.float32)
    src = np.float32([a["xy"][m.queryIdx] for m in good])
    dst = np.float32([b["xy"][m.trainIdx] for m in good])
    cv2.setRNGSeed(42)
    h, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4., maxIters=1500, confidence=.995)
    if h is None or mask is None or not np.isfinite(h).all():
        return np.zeros(4, np.float32)
    ok = mask.ravel().astype(bool)
    n = int(ok.sum())
    coverage = []
    for points, shape in [(src[ok], a["shape"]), (dst[ok], b["shape"])]:
        area = cv2.contourArea(cv2.convexHull(points)) if len(points) > 2 else 0
        coverage.append(min(1., area/float(np.prod(shape))))
    return np.array([np.log1p(n), n/len(good), *coverage], np.float32)

def choose_bottle(boxes, width, height):
    """Geometry only. Never choose another bottle using recognition scores."""
    if not boxes:
        return None
    def key(box):
        x1, y1, x2, y2 = box[:4]
        distance = ((x1+x2)/2/width-.5)**2 + ((y1+y2)/2/height-.5)**2
        return (round(distance, 8), -(x2-x1)*(y2-y1))
    return min(boxes, key=key)
