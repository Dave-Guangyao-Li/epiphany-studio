from __future__ import annotations

import json
import stat
from copy import deepcopy
from pathlib import Path

import pytest
from test_writing_style_ab_execute import (
    MAX_EDITOR_TOKENS,
    ScriptedProvider,
    _contract_hash,
    _editor_draft,
    _execute,
)

from epiphany.writing_style_ab_blind import (
    BlindCandidateTampered,
    BlindRatingConflict,
    BlindRatingRequired,
    prepare_blind_experiment,
    reveal_blind_experiment,
    submit_blind_rating,
)


def _rating() -> dict[str, object]:
    return {
        "candidate_ratings": {
            "A": {
                "voice_match_rating": 4,
                "recordability_rating": 5,
                "comments": "有几句话比较像我。",
            },
            "B": {
                "voice_match_rating": 3,
                "recordability_rating": 4,
                "comments": None,
            },
        },
        "forced_choice_voice_match": "A",
        "forced_choice_reason": "A 的停顿和句子长度更接近我的口吻。",
    }


async def _prepared_pair(tmp_path: Path) -> tuple[Path, Path]:
    first_draft = _editor_draft("第一份")
    first_draft["podcast_script"]["opening"]["text"] = (  # type: ignore[index]
        "<script>alert('x')</script>\n# 模型注入的标题"
    )
    editor = ScriptedProvider(
        model="deepseek-v4-flash",
        outputs=[first_draft, _editor_draft("第二份")],
    )
    reviewer = ScriptedProvider(model="deepseek-v4-pro")
    await _execute(
        tmp_path=tmp_path,
        editor=editor,
        reviewer=reviewer,
        expected_contract_sha256=_contract_hash(
            database_path=tmp_path / "source.db",
            max_editor_tokens=MAX_EDITOR_TOKENS,
        ),
    )
    experiment_dir = tmp_path / "results"
    blind_dir = tmp_path / "blind"
    prepare_blind_experiment(
        experiment_dir=experiment_dir,
        blind_dir=blind_dir,
    )
    return experiment_dir, blind_dir


async def test_prepare_is_anonymous_and_tampering_blocks_rating_and_reveal(
    tmp_path: Path,
) -> None:
    experiment_dir, blind_dir = await _prepared_pair(tmp_path)
    public_files = [
        blind_dir / "candidate-A.md",
        blind_dir / "candidate-B.md",
        blind_dir / "blind-manifest.json",
        blind_dir / "rating-template.json",
    ]
    public_text = "\n".join(path.read_text(encoding="utf-8") for path in public_files)

    assert "# Candidate A" in public_text
    assert "# Candidate B" in public_text
    assert "<script>" not in public_text
    assert "&lt;script&gt;" in public_text
    assert "\n# 模型注入的标题" not in public_text
    assert "\\# 模型注入的标题" in public_text
    for forbidden in (
        "without_sample",
        "with_sample",
        "deepseek-v4",
        "experimental_model_score",
        "src_",
        "seg_",
    ):
        assert forbidden not in public_text
    private = json.loads((blind_dir / "private" / "mapping.json").read_text(encoding="utf-8"))
    assert set(private["mapping"].values()) == {"without_sample", "with_sample"}
    assert stat.S_IMODE(blind_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((blind_dir / "private").stat().st_mode) == 0o700
    for path in [*public_files, blind_dir / "private" / "mapping.json"]:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(BlindRatingRequired):
        reveal_blind_experiment(
            experiment_dir=experiment_dir,
            blind_dir=blind_dir,
        )

    with (blind_dir / "candidate-A.md").open("a", encoding="utf-8") as stream:
        stream.write("\n篡改")
    with pytest.raises(BlindCandidateTampered):
        submit_blind_rating(blind_dir=blind_dir, submission=_rating())


async def test_rating_is_idempotent_conflicts_are_blocked_and_reveal_is_verified(
    tmp_path: Path,
) -> None:
    experiment_dir, blind_dir = await _prepared_pair(tmp_path)
    first = submit_blind_rating(blind_dir=blind_dir, submission=_rating())
    assert submit_blind_rating(blind_dir=blind_dir, submission=_rating()) == first

    conflicting = deepcopy(_rating())
    conflicting["candidate_ratings"]["A"]["voice_match_rating"] = 1  # type: ignore[index]
    with pytest.raises(BlindRatingConflict):
        submit_blind_rating(blind_dir=blind_dir, submission=conflicting)

    mapping_path = blind_dir / "private" / "mapping.json"
    original_mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    tampered_mapping = deepcopy(original_mapping)
    tampered_mapping["salt"] = "tampered"
    mapping_path.write_text(
        json.dumps(tampered_mapping, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(BlindCandidateTampered):
        reveal_blind_experiment(
            experiment_dir=experiment_dir,
            blind_dir=blind_dir,
        )
    mapping_path.write_text(
        json.dumps(original_mapping, ensure_ascii=False),
        encoding="utf-8",
    )

    reveal = reveal_blind_experiment(
        experiment_dir=experiment_dir,
        blind_dir=blind_dir,
    )
    assert reveal["mapping"] == original_mapping["mapping"]
    assert reveal["human_rating"] == _rating()
    assert set(reveal["candidate_summaries"]) == {"A", "B"}
    assert {
        candidate: summary["arm"] for candidate, summary in reveal["candidate_summaries"].items()
    } == original_mapping["mapping"]
    assert reveal["winner_selected"] is False
    assert "winner" not in reveal
    assert (
        reveal_blind_experiment(
            experiment_dir=experiment_dir,
            blind_dir=blind_dir,
        )
        == reveal
    )
