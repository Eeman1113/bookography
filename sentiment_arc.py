"""Vonnegut-style narrative sentiment arc, following the 7-step recipe.

For each book in ./books:
  1. Word-tokenize
  2. Slide ~10k-word window, take 100 evenly-spaced samples
  3. Score each window with AFINN → clamp(comparative × 4, -1, +1)
  4. Low-pass filter via naive DFT (keep lowest 12% of frequencies)
  5. Cross-correlate against 6 canonical Vonnegut shapes (Pearson)
  6. Detect top-3 peaks and valleys; annotate driving AFINN words
  7. Volatility = mean(|Δ smoothed|)

Writes ./graphs/<slug>/sentiment_arc.png + sentiment_arc.json.
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
import numpy as np
from afinn import Afinn
from tqdm import tqdm


ROOT = Path(__file__).resolve().parent
BOOKS_DIR = ROOT / "books"
GRAPHS_DIR = ROOT / "graphs"
GRAPHS_DIR.mkdir(exist_ok=True)

N_SAMPLES = 100
IDEAL_WINDOW = 10_000
MIN_WINDOW = 200
MAX_WINDOW_RATIO = 0.33
DFT_CUTOFF_FRAC = 0.12
WPM = 250

AFINN_EN = Afinn(language="en", emoticons=False)
# afinn also ships French; German is not in the package but we still process — words just don't score.
try:
    AFINN_FR = Afinn(language="fr", emoticons=False)
except Exception:
    AFINN_FR = AFINN_EN


def strip_gutenberg_boilerplate(text: str) -> str:
    start_re = re.compile(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", re.IGNORECASE)
    end_re = re.compile(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", re.IGNORECASE)
    m = start_re.search(text)
    if m:
        text = text[m.end():]
    m = end_re.search(text)
    if m:
        text = text[:m.start()]
    return text.strip()


def detect_language(text: str) -> str:
    sample = text[:20_000].lower()
    fr_hits = sum(sample.count(c) for c in "àâçéèêëîïôùûüÿœæ")
    de_hits = sum(sample.count(c) for c in "äöüß")
    total = max(len(sample), 1)
    if de_hits / total > 0.005:
        return "de"
    if fr_hits / total > 0.008:
        return "fr"
    return "en"


WORD_RE = re.compile(r"[A-Za-zÀ-ÿ']+")


def tokenize(text: str) -> list[str]:
    return WORD_RE.findall(text)


def afinn_word_score(afinn: Afinn, word: str) -> float:
    return afinn.score(word)


def window_sentiment(afinn: Afinn, words: list[str]) -> tuple[float, list[tuple[str, float]]]:
    """Return (raw signal in [-1,+1], list of (word, score) contributions)."""
    if not words:
        return 0.0, []
    contribs: list[tuple[str, float]] = []
    total = 0.0
    for w in words:
        s = afinn.score(w.lower())
        if s != 0.0:
            total += s
            contribs.append((w.lower(), s))
    comparative = total / len(words)
    raw = max(-1.0, min(1.0, comparative * 4.0))
    return raw, contribs


def naive_dft_lowpass(signal: np.ndarray, cutoff_frac: float = DFT_CUTOFF_FRAC) -> np.ndarray:
    """Naive O(N^2) DFT → zero out top (1-cutoff_frac) of bins → inverse DFT."""
    N = len(signal)
    if N == 0:
        return signal.copy()
    # Forward
    n = np.arange(N)
    k = n.reshape(-1, 1)
    W = np.exp(-2j * math.pi * k * n / N)
    X = W @ signal
    # Zero all but the lowest `cutoff` bins (and their symmetric high-frequency mirrors).
    cutoff = max(2, int(math.floor(N * cutoff_frac)))
    mask = np.zeros(N, dtype=bool)
    mask[:cutoff] = True
    mask[-cutoff + 1:] = True  # keep symmetric mirror so inverse is real
    X_filt = np.where(mask, X, 0)
    # Inverse
    W_inv = np.exp(2j * math.pi * k * n / N)
    x_smooth = (W_inv @ X_filt) / N
    return np.real(x_smooth)


def canonical_shapes(N: int) -> dict[str, np.ndarray]:
    t = np.linspace(0.0, 1.0, N)
    return {
        "Rags to Riches": -0.7 + 1.4 * t,
        "Tragedy": 0.7 - 1.4 * t,
        "Man in a Hole": 0.4 - 1.3 * np.cos(math.pi * t) * (1 - t) - 0.2,
        "Icarus": np.sin(math.pi * t) * 0.85 - 0.15,
        "Cinderella": -0.7 + 1.2 * t + 0.55 * np.sin(2 * math.pi * t - math.pi / 2),
        "Oedipus": 0.6 - 1.0 * t - 0.5 * np.sin(2 * math.pi * t - math.pi / 2),
    }


def pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    if denom == 0.0:
        return 0.0
    return float((a * b).sum() / denom)


def local_extrema(signal: np.ndarray, guard: int = 5, top_k: int = 3) -> tuple[list[int], list[int]]:
    peaks: list[tuple[int, float]] = []
    valleys: list[tuple[int, float]] = []
    N = len(signal)
    for i in range(1, N - 1):
        if signal[i] > signal[i - 1] and signal[i] > signal[i + 1]:
            peaks.append((i, float(signal[i])))
        elif signal[i] < signal[i - 1] and signal[i] < signal[i + 1]:
            valleys.append((i, float(signal[i])))

    def suppress(cands: list[tuple[int, float]], reverse: bool) -> list[int]:
        cands.sort(key=lambda t: t[1], reverse=reverse)
        chosen: list[int] = []
        for idx, _ in cands:
            if all(abs(idx - c) > guard for c in chosen):
                chosen.append(idx)
            if len(chosen) >= top_k:
                break
        return sorted(chosen)

    return suppress(peaks, reverse=True), suppress(valleys, reverse=False)


def reliability_label(window_size: int) -> str:
    if window_size >= 10_000:
        return "high"
    if window_size >= 3_333:
        return "medium"
    return "low"


def analyze_book(text: str, lang: str) -> dict:
    afinn = {"fr": AFINN_FR, "en": AFINN_EN, "de": AFINN_EN}.get(lang, AFINN_EN)
    words = tokenize(text)
    total = len(words)
    if total < 200:
        return {"status": "too_short", "n_words": total}

    window_size = max(MIN_WINDOW, min(IDEAL_WINDOW, int(total * MAX_WINDOW_RATIO)))
    span = total - window_size
    if span <= 0:
        window_size = max(MIN_WINDOW, total // 2)
        span = total - window_size

    raw = np.zeros(N_SAMPLES)
    driving: list[list[tuple[str, float]]] = []
    step_positions: list[int] = []
    for i in range(N_SAMPLES):
        start = round((i / (N_SAMPLES - 1)) * span) if N_SAMPLES > 1 else 0
        chunk = words[start:start + window_size]
        step_positions.append(start)
        r, contribs = window_sentiment(afinn, chunk)
        raw[i] = r
        driving.append(contribs)

    smoothed = naive_dft_lowpass(raw)

    shapes = canonical_shapes(N_SAMPLES)
    correlations = sorted(
        ((name, pearson(smoothed, shape)) for name, shape in shapes.items()),
        key=lambda t: t[1], reverse=True,
    )

    peaks, valleys = local_extrema(smoothed, guard=5, top_k=3)

    def driving_words(sample_i: int, want_positive: bool) -> list[tuple[str, float]]:
        contribs = driving[sample_i]
        contribs_sorted = sorted(contribs, key=lambda t: t[1], reverse=want_positive)
        selected: list[tuple[str, float]] = []
        seen = set()
        for w, s in contribs_sorted:
            if want_positive and s <= 0:
                break
            if not want_positive and s >= 0:
                break
            if w in seen:
                continue
            seen.add(w)
            selected.append((w, s))
            if len(selected) >= 6:
                break
        return selected

    volatility = float(np.mean(np.abs(np.diff(smoothed)))) if len(smoothed) > 1 else 0.0

    def moment(sample_i: int, positive: bool) -> dict:
        word_offset = step_positions[sample_i] + window_size // 2
        return {
            "sample": int(sample_i),
            "position": float(sample_i / (N_SAMPLES - 1)),
            "smoothed": float(smoothed[sample_i]),
            "word_offset": int(word_offset),
            "read_time_min": round(word_offset / WPM, 1),
            "driving_words": [{"word": w, "score": s} for w, s in driving_words(sample_i, positive)],
        }

    return {
        "status": "ok",
        "n_words": total,
        "window_size": window_size,
        "reliability": reliability_label(window_size),
        "raw": raw.tolist(),
        "smoothed": smoothed.tolist(),
        "shape_matches": [{"shape": n, "correlation": round(c, 4)} for n, c in correlations],
        "best_shape": correlations[0][0],
        "best_shape_correlation": round(correlations[0][1], 4),
        "peaks": [moment(i, True) for i in peaks],
        "valleys": [moment(i, False) for i in valleys],
        "volatility": round(volatility, 4),
    }


def plot_arc(result: dict, title: str, out_path: Path) -> None:
    raw = np.array(result["raw"])
    smoothed = np.array(result["smoothed"])
    xs = np.linspace(0.0, 1.0, len(raw))
    best_name = result["best_shape"]

    fig, ax = plt.subplots(figsize=(14, 6.5))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#f6f7fb")

    # Auto-scale Y to the actual signal range with generous headroom so the curves fill the frame.
    y_lo = min(smoothed.min(), raw.min())
    y_hi = max(smoothed.max(), raw.max())
    y_span = max(y_hi - y_lo, 0.02)
    pad = y_span * 0.30
    ax.set_ylim(y_lo - pad, y_hi + pad)
    ax.set_xlim(0, 1)

    # Faint raw signal behind everything.
    ax.plot(xs, raw, color="#9aa0a8", lw=0.9, alpha=0.55, zorder=1, label="raw signal")

    # Zero baseline.
    ax.axhline(0, color="#6a6a6a", lw=0.6, linestyle="--", alpha=0.55, zorder=1)

    # Gradient fill — warm above zero, cool below.
    ax.fill_between(xs, 0, smoothed, where=(smoothed >= 0), interpolate=True,
                    color="#f2b26b", alpha=0.35, zorder=2, linewidth=0)
    ax.fill_between(xs, 0, smoothed, where=(smoothed < 0), interpolate=True,
                    color="#6c82d6", alpha=0.35, zorder=2, linewidth=0)

    # Line coloured per segment: orange when positive, blue when negative.
    points = np.column_stack([xs, smoothed]).reshape(-1, 1, 2)
    segments = np.concatenate([points[:-1], points[1:]], axis=1)
    seg_vals = (smoothed[:-1] + smoothed[1:]) / 2
    seg_colors = ["#d97e2f" if v >= 0 else "#3f57b2" for v in seg_vals]
    lc = LineCollection(segments, colors=seg_colors, linewidths=3.2, zorder=4,
                        capstyle="round", joinstyle="round")
    ax.add_collection(lc)

    # Peak markers (warm) and valley markers (cool) with driving-word annotations.
    for p in result["peaks"]:
        ax.scatter([p["position"]], [p["smoothed"]], s=110, zorder=6,
                   color="#e6883a", edgecolor="white", linewidth=1.6)
        words = ", ".join(w["word"] for w in p["driving_words"][:3]) if p["driving_words"] else ""
        ax.annotate(words, (p["position"], p["smoothed"]),
                    textcoords="offset points", xytext=(0, 14),
                    ha="center", fontsize=8.5, color="#7a3f13",
                    fontweight="semibold")
    for v in result["valleys"]:
        ax.scatter([v["position"]], [v["smoothed"]], s=110, zorder=6,
                   color="#3f57b2", edgecolor="white", linewidth=1.6)
        words = ", ".join(w["word"] for w in v["driving_words"][:3]) if v["driving_words"] else ""
        ax.annotate(words, (v["position"], v["smoothed"]),
                    textcoords="offset points", xytext=(0, -18),
                    ha="center", fontsize=8.5, color="#25316a",
                    fontweight="semibold")

    ax.set_xticks([0.0, 0.25, 0.5, 0.75, 1.0])
    ax.set_xticklabels(["0%", "25%", "50%", "75%", "100%"])
    ax.set_xlabel("Narrative progress")
    ax.set_ylabel("Sentiment")

    reliab = result["reliability"]
    corr = result.get("best_shape_correlation", 0.0)
    ax.set_title(
        f"Narrative Sentiment Arc — {title}\n"
        f"best-fit shape: {best_name} (r = {corr:+.2f})  ·  volatility {result['volatility']:.3f}"
        f"  ·  window {result['window_size']:,}w  ·  reliability: {reliab}",
        fontsize=12, pad=12,
    )
    ax.grid(True, alpha=0.25, linestyle="--", linewidth=0.5, zorder=0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#888")
    ax.spines["bottom"].set_color("#888")
    fig.tight_layout()
    fig.savefig(out_path, dpi=220, facecolor="white")
    plt.close(fig)


def process_book(book_path: Path) -> dict:
    slug = book_path.stem
    out_dir = GRAPHS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    marker = out_dir / "sentiment_arc.json"
    png_path = out_dir / "sentiment_arc.png"
    if marker.exists() and png_path.exists():
        return {"slug": slug, "status": "skipped"}
    # Fast path: JSON already exists, only need to (re)draw the PNG.
    if marker.exists() and not png_path.exists():
        cached = json.loads(marker.read_text())
        if cached.get("status") == "ok":
            title = slug.replace("_", " ")
            plot_arc(cached, title, png_path)
            return {"slug": slug, "status": "replotted"}

    raw_text = book_path.read_text(encoding="utf-8", errors="ignore")
    text = strip_gutenberg_boilerplate(raw_text)
    lang = detect_language(text)

    result = analyze_book(text, lang)
    result["slug"] = slug
    result["language"] = lang
    if result.get("status") != "ok":
        (out_dir / "sentiment_arc.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
        return result

    title = slug.replace("_", " ")
    plot_arc(result, title, out_dir / "sentiment_arc.png")
    (out_dir / "sentiment_arc.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main(argv: list[str]) -> int:
    only = set(argv[1:]) if len(argv) > 1 else None
    books = sorted(BOOKS_DIR.glob("*.txt"))
    if only:
        books = [b for b in books if b.stem in only or b.name in only]
    summary_path = GRAPHS_DIR / "_sentiment_summary.jsonl"
    with summary_path.open("a", encoding="utf-8") as summary_f:
        for book in tqdm(books, desc="sentiment"):
            t0 = time.time()
            try:
                res = process_book(book)
                res["elapsed_s"] = round(time.time() - t0, 2)
                summary_f.write(json.dumps(res, ensure_ascii=False) + "\n")
                summary_f.flush()
                status = res.get("status", "?")
                extra = f"  best={res.get('best_shape', '?')}" if status == "ok" else ""
                tqdm.write(f"{status:>10}  {book.stem}{extra}  ({res['elapsed_s']}s)")
            except Exception as e:
                tqdm.write(f"    ERROR  {book.stem}: {e}")
                summary_f.write(json.dumps({"slug": book.stem, "status": "error", "error": str(e)}, ensure_ascii=False) + "\n")
                summary_f.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
