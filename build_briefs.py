"""Build one compact JSON brief per book, merging entity metadata + sentiment arc.

Written to graphs/<slug>/brief.json so a subagent can read a single file instead
of two.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GRAPHS_DIR = ROOT / "graphs"


def build_one(slug_dir: Path) -> None:
    meta_path = slug_dir / "metadata.json"
    sent_path = slug_dir / "sentiment_arc.json"
    if not meta_path.exists() or not sent_path.exists():
        return
    meta = json.loads(meta_path.read_text())
    sent = json.loads(sent_path.read_text())

    brief = {
        "slug": meta.get("slug"),
        "title_display": meta.get("title", slug_dir.name).replace("_", " "),
        "language": meta.get("language"),
        "length_tokens": meta.get("n_tokens"),
        "length_words": sent.get("n_words"),
        "unique_entities": meta.get("n_unique_entities"),
        "entity_mentions": meta.get("n_entity_mentions"),
        "scene_count": meta.get("n_scenes"),
        "top_entities": meta.get("top_entities", [])[:15],
        "peak_activity_positions": meta.get("peak_activity_positions", []),
        "narrative_flow": meta.get("narrative_flow", {}),
        "sentiment": {
            "reliability": sent.get("reliability"),
            "window_size": sent.get("window_size"),
            "volatility": sent.get("volatility"),
            "best_shape": sent.get("best_shape"),
            "best_shape_correlation": sent.get("best_shape_correlation"),
            "shape_matches": sent.get("shape_matches"),
            "peaks": [
                {
                    "position": p["position"],
                    "smoothed": p["smoothed"],
                    "read_time_min": p["read_time_min"],
                    "driving_words": [w["word"] for w in p.get("driving_words", [])[:6]],
                }
                for p in sent.get("peaks", [])
            ],
            "valleys": [
                {
                    "position": v["position"],
                    "smoothed": v["smoothed"],
                    "read_time_min": v["read_time_min"],
                    "driving_words": [w["word"] for w in v.get("driving_words", [])[:6]],
                }
                for v in sent.get("valleys", [])
            ],
        },
        "graph_paths": {
            "scatter": str(slug_dir / "scatter.png"),
            "activity": str(slug_dir / "activity.png"),
            "heatmap": str(slug_dir / "heatmap.png"),
            "combined": str(slug_dir / "combined.png"),
            "narrative_flow": str(slug_dir / "narrative_flow.png"),
            "sentiment_arc": str(slug_dir / "sentiment_arc.png"),
        },
    }
    (slug_dir / "brief.json").write_text(json.dumps(brief, indent=2, ensure_ascii=False))


def main() -> None:
    for d in sorted(GRAPHS_DIR.iterdir()):
        if d.is_dir():
            build_one(d)


if __name__ == "__main__":
    main()
