"""Plot extraction and narrative-flow visualization for Project Gutenberg books.

Implements the methodology from DeBuse & Warnick (2024) "Plot extraction and the
visualization of narrative flow":
  1. Scatter plot of entities (token position × entity index)
  2. Entity activity line via Gaussian summation (sigma = n_tokens / 400)
  3. Heatmap of the activity line
  4. Scene partitioning via 1-D mean-shift clustering (high-activity hypothesis)
  5. Best-effort narrative flow multi-DAG (scenes as vertices, shared entities as edges)

Reads plain-text Gutenberg files from ./books and writes per-book output under
./graphs/<slug>/.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import spacy
from sklearn.cluster import MeanShift, estimate_bandwidth
from tqdm import tqdm


ROOT = Path(__file__).resolve().parent
BOOKS_DIR = ROOT / "books"
GRAPHS_DIR = ROOT / "graphs"
GRAPHS_DIR.mkdir(exist_ok=True)

# Interesting spaCy NER labels for plot-relevant entities (per paper §3.2.1).
ENTITY_LABELS = {"PERSON", "ORG", "GPE", "LOC", "FAC", "NORP", "PER", "MISC"}

MIN_ENTITY_MENTIONS = 3  # paper §3.2.2 — drop entities appearing ≤2 times
MAX_TOKENS_HARD = 400_000  # cap NER processing for very large books
SIGMA_DIVISOR = 400  # paper's tuned d for σ = n/d
ACTIVITY_GRID = 2000  # samples along the activity curve

_MODEL_CACHE: dict[str, "spacy.Language"] = {}


def load_model(lang: str) -> "spacy.Language":
    """Cache and return the spaCy pipeline for the given language."""
    if lang not in _MODEL_CACHE:
        model = {
            "en": "en_core_web_sm",
            "fr": "fr_core_news_sm",
            "de": "de_core_news_sm",
        }.get(lang, "en_core_web_sm")
        nlp = spacy.load(model, disable=["parser", "lemmatizer", "attribute_ruler"])
        nlp.max_length = 2_500_000
        _MODEL_CACHE[lang] = nlp
    return _MODEL_CACHE[lang]


def strip_gutenberg_boilerplate(text: str) -> str:
    """Remove standard PG header and footer, leaving only the work itself."""
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
    """Cheap character-frequency heuristic. Only distinguishes en/fr/de."""
    sample = text[:20_000].lower()
    fr_hits = sum(sample.count(c) for c in "àâçéèêëîïôùûüÿœæ")
    de_hits = sum(sample.count(c) for c in "äöüß")
    total = max(len(sample), 1)
    if de_hits / total > 0.005:
        return "de"
    if fr_hits / total > 0.008:
        return "fr"
    return "en"


@dataclass
class EntityHit:
    token_index: int
    label: str  # canonical entity label
    ent_type: str


def canonicalize(text: str) -> str:
    t = re.sub(r"\s+", " ", text.strip())
    # Strip common titles so "Mr. Darcy" and "Darcy" merge.
    t = re.sub(r"^(mr|mrs|ms|miss|dr|sir|lady|lord|monsieur|madame|mademoiselle|herr|frau)\.?\s+", "", t, flags=re.IGNORECASE)
    return t.lower()


def extract_entities(nlp, text: str) -> tuple[list[EntityHit], int]:
    """Run spaCy over the text in chunks. Return (hits, total_tokens).

    hits carries the token index of each entity mention plus a canonical label.
    """
    # Split into chunks under nlp.max_length by paragraph boundary.
    chunk_size = 900_000  # characters
    chunks = []
    i = 0
    while i < len(text):
        end = min(i + chunk_size, len(text))
        if end < len(text):
            nl = text.rfind("\n\n", i, end)
            if nl > i + chunk_size // 2:
                end = nl
        chunks.append(text[i:end])
        i = end

    hits: list[EntityHit] = []
    token_offset = 0

    for chunk in chunks:
        doc = nlp(chunk)
        for ent in doc.ents:
            if ent.label_ not in ENTITY_LABELS:
                continue
            if len(ent.text.strip()) < 2:
                continue
            hits.append(EntityHit(
                token_index=token_offset + ent.start,
                label=canonicalize(ent.text),
                ent_type=ent.label_,
            ))
        token_offset += len(doc)
        if token_offset >= MAX_TOKENS_HARD:
            break

    return hits, token_offset


def filter_and_index(hits: list[EntityHit]) -> tuple[list[EntityHit], list[str], dict[str, str]]:
    """Keep entities with ≥ MIN_ENTITY_MENTIONS and assign stable indices.

    Returns (filtered hits, ordered label list, label→entity_type map).
    """
    counts = Counter(h.label for h in hits)
    keep = {lbl for lbl, c in counts.items() if c >= MIN_ENTITY_MENTIONS}
    filtered = [h for h in hits if h.label in keep]

    # Index in order of first appearance.
    order: list[str] = []
    seen = set()
    ent_type: dict[str, str] = {}
    for h in filtered:
        if h.label not in seen:
            seen.add(h.label)
            order.append(h.label)
            ent_type[h.label] = h.ent_type
    return filtered, order, ent_type


def gaussian_activity(entity_x: np.ndarray, n_tokens: int, sigma: float, grid_size: int = ACTIVITY_GRID) -> tuple[np.ndarray, np.ndarray]:
    """Compute the activity line as the sum of Gaussians centered on entity positions."""
    xs = np.linspace(0, max(n_tokens - 1, 1), grid_size)
    if len(entity_x) == 0:
        return xs, np.zeros_like(xs)
    # Vectorized: for each grid point, sum exp(-(x-e)^2 / 2σ^2)
    # Chunk to avoid huge memory when many entities.
    activity = np.zeros_like(xs)
    ex = entity_x.astype(float)
    chunk = 2000
    two_sigma_sq = 2 * sigma * sigma
    for i in range(0, len(ex), chunk):
        block = ex[i:i + chunk]
        diff = xs[:, None] - block[None, :]
        activity += np.exp(-(diff * diff) / two_sigma_sq).sum(axis=1)
    # Normalize so activity is per-token density.
    activity /= (sigma * np.sqrt(2 * np.pi))
    return xs, activity


def mean_shift_scenes(entity_x: np.ndarray, n_tokens: int, activity_xs: np.ndarray, activity: np.ndarray, target_scenes: int) -> list[float]:
    """Cluster entity x-coordinates and pick partition boundaries using
    the high-activity hypothesis from the paper (§3.2.3).

    Returns a list of scene-boundary x-positions (in token space).
    """
    if len(entity_x) < 4 or target_scenes < 2:
        return []

    X = entity_x.reshape(-1, 1).astype(float)
    best_boundaries: list[float] = []
    best_score = -np.inf

    # Try a spread of bandwidths that produce a range of scene counts.
    span = float(n_tokens)
    bandwidths = np.geomspace(span / (target_scenes * 6), span / 2, 12)
    for bw in bandwidths:
        try:
            ms = MeanShift(bandwidth=bw, bin_seeding=True, cluster_all=True)
            ms.fit(X)
        except Exception:
            continue
        labels = ms.labels_
        centers = ms.cluster_centers_.flatten()
        order = np.argsort(centers)
        centers_sorted = centers[order]
        if len(centers_sorted) < 2:
            continue
        # Boundaries midway between consecutive cluster centers.
        boundaries = ((centers_sorted[:-1] + centers_sorted[1:]) / 2).tolist()
        # Score = average activity value at boundaries (paper's best-performing hypothesis).
        act_vals = np.interp(boundaries, activity_xs, activity)
        score = float(act_vals.mean())
        # Prefer partitions whose count is near the target.
        penalty = abs(len(boundaries) + 1 - target_scenes) * 0.02 * float(activity.max())
        score -= penalty
        if score > best_score:
            best_score = score
            best_boundaries = boundaries

    return best_boundaries


def scene_target_count(n_tokens: int) -> int:
    """Rough heuristic: ~1 scene per 3000 tokens, clamped."""
    return max(3, min(60, n_tokens // 3000))


def plot_scatter(entity_x_by_id: dict[int, list[int]], labels: list[str], scene_bounds: list[float],
                 n_tokens: int, title: str, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(14, 7))
    for eid, xs in entity_x_by_id.items():
        ax.scatter(xs, [eid] * len(xs), s=7, c="black", alpha=0.6)
    for b in scene_bounds:
        ax.axvline(b, color="red", alpha=0.6, lw=1.0)
    ax.set_xlabel("Location in text (token index)")
    ax.set_ylabel("Entity index (by first appearance)")
    ax.set_title(f"Scatter Plot of Entities — {title}")
    ax.set_xlim(0, max(n_tokens, 1))
    ax.set_ylim(-1, max(len(labels), 1))
    fig.tight_layout()
    fig.savefig(out_path, dpi=220)
    plt.close(fig)


def plot_activity(activity_xs: np.ndarray, activity: np.ndarray, scene_bounds: list[float],
                  title: str, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 3))
    ax.plot(activity_xs, activity, color="tab:blue", lw=0.9)
    ax.fill_between(activity_xs, 0, activity, color="tab:blue", alpha=0.15)
    for b in scene_bounds:
        ax.axvline(b, color="red", alpha=0.55, lw=0.9)
    ax.set_xlabel("Location in text (token index)")
    ax.set_ylabel("Entity activity")
    ax.set_title(f"Entity Activity Line — {title}")
    ax.set_xlim(0, activity_xs[-1] if len(activity_xs) else 1)
    fig.tight_layout()
    fig.savefig(out_path, dpi=220)
    plt.close(fig)


def plot_heatmap(activity_xs: np.ndarray, activity: np.ndarray, title: str, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 1.6))
    strip = activity[np.newaxis, :]
    ax.imshow(strip, aspect="auto", cmap="viridis",
              extent=(activity_xs[0], activity_xs[-1], 0, 1))
    ax.set_yticks([])
    ax.set_xlabel("Location in text (token index)")
    ax.set_title(f"Plot-Importance Heatmap — {title}")
    fig.tight_layout()
    fig.savefig(out_path, dpi=220)
    plt.close(fig)


def plot_combined(entity_x_by_id: dict[int, list[int]], labels: list[str],
                  activity_xs: np.ndarray, activity: np.ndarray, scene_bounds: list[float],
                  n_tokens: int, title: str, out_path: Path) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(12, 10), gridspec_kw={"height_ratios": [4, 1.4, 0.9]}, sharex=True)
    ax_sc, ax_act, ax_hm = axes

    for eid, xs in entity_x_by_id.items():
        ax_sc.scatter(xs, [eid] * len(xs), s=6, c="black", alpha=0.55)
    for b in scene_bounds:
        for ax in (ax_sc, ax_act, ax_hm):
            ax.axvline(b, color="red", alpha=0.5, lw=0.8)
    ax_sc.set_ylabel("Entity index")
    ax_sc.set_title(title)
    ax_sc.set_ylim(-1, max(len(labels), 1))

    ax_act.plot(activity_xs, activity, color="tab:blue", lw=0.9)
    ax_act.fill_between(activity_xs, 0, activity, color="tab:blue", alpha=0.15)
    ax_act.set_ylabel("Activity")

    strip = activity[np.newaxis, :]
    ax_hm.imshow(strip, aspect="auto", cmap="viridis",
                 extent=(activity_xs[0], activity_xs[-1], 0, 1))
    ax_hm.set_yticks([])
    ax_hm.set_xlabel("Location in text (token index)")

    ax_sc.set_xlim(0, max(n_tokens, 1))
    fig.tight_layout()
    fig.savefig(out_path, dpi=220)
    plt.close(fig)


def build_narrative_flow(filtered: list[EntityHit], labels: list[str], ent_type: dict[str, str],
                         scene_bounds: list[float], n_tokens: int, title: str, out_path: Path) -> dict:
    """Build a lightweight narrative-flow multi-DAG:
       - vertices = scenes (segments between boundaries)
       - edges = each plot-important entity appearing in scene s_a and s_b
                 with s_a being the entity's most-recent prior scene (paper §4.1 restriction 5)
    """
    if not filtered:
        return {"scenes": 0, "edges": 0}

    label_to_id = {lbl: i for i, lbl in enumerate(labels)}
    # Assemble scene ranges [0, b0), [b0, b1), ..., [b_{k-1}, n_tokens)
    bounds = [0.0, *scene_bounds, float(n_tokens)]
    scenes = list(zip(bounds[:-1], bounds[1:]))
    n_scenes = len(scenes)

    # For each entity hit, find its scene.
    scene_entities: list[set[int]] = [set() for _ in range(n_scenes)]
    entity_scene_hits: dict[int, list[int]] = defaultdict(list)  # eid -> ordered list of scenes it appears in
    for h in filtered:
        eid = label_to_id[h.label]
        # binary search for scene
        s = 0
        for si, (lo, hi) in enumerate(scenes):
            if lo <= h.token_index < hi:
                s = si
                break
        scene_entities[s].add(eid)
        if not entity_scene_hits[eid] or entity_scene_hits[eid][-1] != s:
            entity_scene_hits[eid].append(s)

    # Build multi-graph
    G = nx.MultiDiGraph()
    for si, ents in enumerate(scene_entities):
        G.add_node(si, entities=sorted(ents))
    edges: list[tuple[int, int, int]] = []
    for eid, scene_list in entity_scene_hits.items():
        for a, b in zip(scene_list[:-1], scene_list[1:]):
            if a != b:
                G.add_edge(a, b, entity=eid)
                edges.append((a, b, eid))

    # Draw with a left-to-right layout.
    fig_w = min(30, 4 + n_scenes * 1.1)
    fig, ax = plt.subplots(figsize=(fig_w, 9))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#fafbfd")

    pos = {si: (si, 0) for si in range(n_scenes)}
    node_sizes = [400 + 55 * len(scene_entities[si]) for si in range(n_scenes)]
    nx.draw_networkx_nodes(G, pos, node_size=node_sizes,
                           node_color="#d0e6ff", edgecolors="#1c4b6b", linewidths=1.4, ax=ax)
    nx.draw_networkx_labels(G, pos, labels={i: str(i) for i in range(n_scenes)},
                            font_size=10, font_weight="bold", ax=ax)

    # Group edges by (a,b) so we can offset arcs.
    grouped: dict[tuple[int, int], list[int]] = defaultdict(list)
    for a, b, eid in edges:
        grouped[(a, b)].append(eid)
    cmap = matplotlib.colormaps.get_cmap("tab20")
    for (a, b), eids in grouped.items():
        for k, eid in enumerate(eids):
            rad = 0.22 * (k - (len(eids) - 1) / 2)
            color = cmap(eid % 20)
            ax.annotate(
                "", xy=pos[b], xytext=pos[a],
                arrowprops=dict(arrowstyle="-|>", color=color, lw=1.4,
                                connectionstyle=f"arc3,rad={rad}", alpha=0.75,
                                mutation_scale=14),
            )

    ax.set_title(f"Narrative Flow Graph — {title}\n({n_scenes} scenes, {len(edges)} entity-edges)",
                 fontsize=13, pad=10)
    ax.axis("off")
    ax.set_xlim(-0.5, n_scenes - 0.5)
    ax.set_ylim(-4, 4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=220, facecolor="white")
    plt.close(fig)

    return {
        "scenes": n_scenes,
        "edges": len(edges),
        "scene_entity_counts": [len(s) for s in scene_entities],
    }


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:120]


def process_book(book_path: Path) -> dict:
    slug = book_path.stem
    out_dir = GRAPHS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    marker = out_dir / "done.json"
    if marker.exists():
        return {"slug": slug, "status": "skipped"}

    raw = book_path.read_text(encoding="utf-8", errors="ignore")
    text = strip_gutenberg_boilerplate(raw)
    if len(text) < 2000:
        return {"slug": slug, "status": "too_short"}

    lang = detect_language(text)
    nlp = load_model(lang)

    hits, n_tokens = extract_entities(nlp, text)
    filtered, labels, ent_type = filter_and_index(hits)

    if not filtered or n_tokens < 500:
        return {"slug": slug, "status": "no_entities"}

    # Map entities to numeric IDs; group x-positions per entity for scatter.
    label_to_id = {lbl: i for i, lbl in enumerate(labels)}
    entity_x_by_id: dict[int, list[int]] = defaultdict(list)
    xs_all = []
    for h in filtered:
        eid = label_to_id[h.label]
        entity_x_by_id[eid].append(h.token_index)
        xs_all.append(h.token_index)

    entity_x = np.array(xs_all, dtype=float)
    sigma = max(1.0, n_tokens / SIGMA_DIVISOR)
    activity_xs, activity = gaussian_activity(entity_x, n_tokens, sigma)

    target_scenes = scene_target_count(n_tokens)
    scene_bounds = mean_shift_scenes(entity_x, n_tokens, activity_xs, activity, target_scenes)

    title = slug.replace("_", " ")
    plot_scatter(entity_x_by_id, labels, scene_bounds, n_tokens, title, out_dir / "scatter.png")
    plot_activity(activity_xs, activity, scene_bounds, title, out_dir / "activity.png")
    plot_heatmap(activity_xs, activity, title, out_dir / "heatmap.png")
    plot_combined(entity_x_by_id, labels, activity_xs, activity, scene_bounds, n_tokens, title, out_dir / "combined.png")
    flow_stats = build_narrative_flow(filtered, labels, ent_type, scene_bounds, n_tokens, title, out_dir / "narrative_flow.png")

    counts = Counter(h.label for h in filtered)
    top_entities = counts.most_common(25)
    # Peak activity zones (top-5 local maxima).
    peaks = []
    if len(activity) > 5:
        d = np.diff(np.sign(np.diff(activity)))
        peak_idx = np.where(d < 0)[0] + 1
        peak_vals = list(zip(activity_xs[peak_idx].tolist(), activity[peak_idx].tolist()))
        peak_vals.sort(key=lambda t: t[1], reverse=True)
        peaks = peak_vals[:5]

    meta = {
        "slug": slug,
        "title": title,
        "language": lang,
        "status": "ok",
        "n_tokens": int(n_tokens),
        "n_unique_entities": len(labels),
        "n_entity_mentions": len(filtered),
        "sigma": sigma,
        "target_scenes": target_scenes,
        "detected_scene_boundaries": [float(b) for b in scene_bounds],
        "n_scenes": len(scene_bounds) + 1,
        "top_entities": [{"label": lbl, "count": c, "type": ent_type.get(lbl, "?")} for lbl, c in top_entities],
        "peak_activity_positions": [{"token": float(t), "activity": float(v)} for t, v in peaks],
        "narrative_flow": flow_stats,
    }
    (out_dir / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    marker.write_text(json.dumps({"ok": True}))
    return meta


def main(argv: list[str]) -> int:
    only = set(argv[1:]) if len(argv) > 1 else None
    books = sorted(BOOKS_DIR.glob("*.txt"))
    if only:
        books = [b for b in books if b.stem in only or b.name in only]
    summary_path = GRAPHS_DIR / "_summary.jsonl"
    with summary_path.open("a", encoding="utf-8") as summary_f:
        for book in tqdm(books, desc="books"):
            t0 = time.time()
            try:
                result = process_book(book)
                elapsed = time.time() - t0
                result["elapsed_s"] = round(elapsed, 2)
                summary_f.write(json.dumps(result, ensure_ascii=False) + "\n")
                summary_f.flush()
                tqdm.write(f"{result.get('status', '?'):>10}  {book.stem}  ({elapsed:.1f}s)")
            except Exception as e:
                tqdm.write(f"    ERROR  {book.stem}: {e}")
                traceback.print_exc()
                summary_f.write(json.dumps({"slug": book.stem, "status": "error", "error": str(e)}, ensure_ascii=False) + "\n")
                summary_f.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
