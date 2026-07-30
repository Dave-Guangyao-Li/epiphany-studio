from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import secrets
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from epiphany.draft_quality_schemas import (
    DeterministicDraftQualityResult,
    DraftQualityReport,
)
from epiphany.editor_schemas import PodcastDraftOutput
from epiphany.episode_markdown import contains_internal_source_identifier
from epiphany.writing_style_ab_execute import EXECUTION_RESULT_VERSION

BLIND_MANIFEST_VERSION = "writing_style_ab_blind_v1"
BLIND_RATING_VERSION = "writing_style_ab_blind_rating_v1"
BLIND_REVEAL_VERSION = "writing_style_ab_blind_reveal_v1"
ARMS = ("without_sample", "with_sample")
CANDIDATES = ("A", "B")
_MARKDOWN_CONTROL = re.compile(r"([\\`*_[\]{}()#+!|>\-])")


class BlindExperimentError(RuntimeError):
    code = "writing_style_ab_blind_error"


class BlindSourceInvalid(BlindExperimentError):
    code = "writing_style_ab_blind_source_invalid"


class BlindOutputExists(BlindExperimentError):
    code = "writing_style_ab_blind_output_exists"


class BlindCandidateTampered(BlindExperimentError):
    code = "writing_style_ab_candidate_tampered"


class BlindRatingRequired(BlindExperimentError):
    code = "writing_style_ab_rating_required"


class BlindRatingInvalid(BlindExperimentError):
    code = "writing_style_ab_rating_invalid"


class BlindRatingConflict(BlindExperimentError):
    code = "writing_style_ab_rating_conflict"


class CandidateRating(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_match_rating: int = Field(ge=1, le=5)
    recordability_rating: int = Field(ge=1, le=5)
    comments: str | None = Field(default=None, max_length=2_000)

    @field_validator("comments")
    @classmethod
    def normalize_comments(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class BlindRatingSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_ratings: dict[Literal["A", "B"], CandidateRating]
    forced_choice_voice_match: Literal["A", "B"]
    forced_choice_reason: str | None = Field(default=None, max_length=2_000)

    @field_validator("candidate_ratings")
    @classmethod
    def require_both_candidates(
        cls,
        value: dict[Literal["A", "B"], CandidateRating],
    ) -> dict[Literal["A", "B"], CandidateRating]:
        if set(value) != set(CANDIDATES):
            raise ValueError("candidate_ratings must contain exactly A and B")
        return value

    @field_validator("forced_choice_reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BlindSourceInvalid("required JSON could not be read") from error
    if not isinstance(value, dict):
        raise BlindSourceInvalid("required JSON must contain an object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    _atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
    )


def _atomic_write_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _safe_markdown_text(value: str) -> str:
    return _MARKDOWN_CONTROL.sub(r"\\\1", html.escape(value, quote=False))


def _experiment_paths(experiment_dir: Path) -> dict[str, Path]:
    return {
        "manifest": experiment_dir / "manifest.json",
        "without_sample_draft": experiment_dir / "without-sample-draft.json",
        "without_sample_quality": experiment_dir / "without-sample-quality.json",
        "with_sample_draft": experiment_dir / "with-sample-draft.json",
        "with_sample_quality": experiment_dir / "with-sample-quality.json",
    }


def _blind_paths(blind_dir: Path) -> dict[str, Path]:
    return {
        "candidate_A": blind_dir / "candidate-A.md",
        "candidate_B": blind_dir / "candidate-B.md",
        "manifest": blind_dir / "blind-manifest.json",
        "template": blind_dir / "rating-template.json",
        "rating": blind_dir / "blind-rating.json",
        "mapping": blind_dir / "private" / "mapping.json",
        "reveal": blind_dir / "reveal.json",
    }


def _load_experiment(
    experiment_dir: Path,
) -> tuple[dict[str, Any], dict[str, PodcastDraftOutput], dict[str, dict[str, Any]]]:
    paths = _experiment_paths(experiment_dir)
    manifest = _read_json(paths["manifest"])
    if (
        manifest.get("schema_version") != EXECUTION_RESULT_VERSION
        or manifest.get("passed") is not True
        or manifest.get("status") != "succeeded"
        or manifest.get("protocol", {}).get("provider_call_count") != 4
        or set(manifest.get("arms", {})) != set(ARMS)
    ):
        raise BlindSourceInvalid("M3.7b manifest is not one successful four-call pair")

    drafts: dict[str, PodcastDraftOutput] = {}
    qualities: dict[str, dict[str, Any]] = {}
    try:
        for arm in ARMS:
            arm_manifest = manifest["arms"][arm]
            draft_json = _read_json(paths[f"{arm}_draft"])
            quality_json = _read_json(paths[f"{arm}_quality"])
            if _json_sha256(draft_json) != arm_manifest["draft_sha256"]:
                raise BlindCandidateTampered("M3.7b Draft hash no longer matches its manifest")
            if _json_sha256(quality_json) != arm_manifest["quality_sha256"]:
                raise BlindCandidateTampered("M3.7b quality hash no longer matches its manifest")
            if (
                quality_json.get("schema_version") != EXECUTION_RESULT_VERSION
                or quality_json.get("arm") != arm
            ):
                raise BlindSourceInvalid("M3.7b quality Artifact has the wrong arm identity")
            drafts[arm] = PodcastDraftOutput.model_validate(draft_json)
            DeterministicDraftQualityResult.model_validate(quality_json["deterministic"])
            DraftQualityReport.model_validate(quality_json["report"])
            qualities[arm] = quality_json
    except (KeyError, TypeError, ValidationError, ValueError) as error:
        if isinstance(error, BlindExperimentError):
            raise
        raise BlindSourceInvalid("M3.7b candidate artifacts are inconsistent") from error
    return manifest, drafts, qualities


def _render_script(candidate: str, draft: PodcastDraftOutput) -> str:
    script = draft.podcast_script
    lines = [
        f"# Candidate {candidate}",
        "",
        "## 开场",
        "",
        _safe_markdown_text(script.opening.text),
        "",
    ]
    for index, section in enumerate(script.sections, start=1):
        lines.extend([f"## {index}. {_safe_markdown_text(section.title)}", ""])
        for paragraph in section.paragraphs:
            lines.extend([_safe_markdown_text(paragraph.text), ""])
    lines.extend(["## 收束", "", _safe_markdown_text(script.closing.text), ""])
    markdown = "\n".join(lines)
    if contains_internal_source_identifier(markdown) or any(marker in markdown for marker in ARMS):
        raise BlindSourceInvalid("candidate Markdown contains experiment metadata")
    return markdown


def _commitment_payload(
    *,
    mapping: Mapping[str, str],
    salt: str,
    source_manifest_sha256: str,
    candidate_sha256: Mapping[str, str],
) -> dict[str, Any]:
    return {
        "mapping": dict(mapping),
        "salt": salt,
        "source_manifest_sha256": source_manifest_sha256,
        "candidate_sha256": dict(candidate_sha256),
    }


def prepare_blind_experiment(
    *,
    experiment_dir: Path,
    blind_dir: Path,
) -> dict[str, Any]:
    source_manifest, drafts, _ = _load_experiment(experiment_dir)
    paths = _blind_paths(blind_dir)
    prepare_paths = [
        paths["candidate_A"],
        paths["candidate_B"],
        paths["manifest"],
        paths["template"],
        paths["mapping"],
    ]
    if blind_dir.exists() or any(path.exists() for path in prepare_paths):
        raise BlindOutputExists("blind preparation never overwrites existing files")

    first_arm, second_arm = ARMS if secrets.randbelow(2) == 0 else tuple(reversed(ARMS))
    mapping = {"A": first_arm, "B": second_arm}
    markdown = {
        candidate: _render_script(candidate, drafts[arm]) for candidate, arm in mapping.items()
    }
    candidate_hashes = {
        candidate: hashlib.sha256(text.encode("utf-8")).hexdigest()
        for candidate, text in markdown.items()
    }
    source_manifest_hash = _json_sha256(source_manifest)
    salt = secrets.token_hex(32)
    commitment = _json_sha256(
        _commitment_payload(
            mapping=mapping,
            salt=salt,
            source_manifest_sha256=source_manifest_hash,
            candidate_sha256=candidate_hashes,
        )
    )
    public_manifest = {
        "schema_version": BLIND_MANIFEST_VERSION,
        "event": "writing_style_ab.blind.prepared",
        "candidates": {
            candidate: {
                "file": paths[f"candidate_{candidate}"].name,
                "sha256": candidate_hashes[candidate],
            }
            for candidate in CANDIDATES
        },
        "mapping_commitment_sha256": commitment,
        "source_manifest_sha256": source_manifest_hash,
        "treatment_hidden": True,
    }
    private_mapping = {
        "schema_version": BLIND_MANIFEST_VERSION,
        "mapping": mapping,
        "salt": salt,
        "mapping_commitment_sha256": commitment,
        "source_manifest_sha256": source_manifest_hash,
        "candidate_sha256": candidate_hashes,
    }
    rating_template = {
        "candidate_ratings": {
            candidate: {
                "voice_match_rating": None,
                "recordability_rating": None,
                "comments": None,
            }
            for candidate in CANDIDATES
        },
        "forced_choice_voice_match": None,
        "forced_choice_reason": None,
    }

    blind_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        blind_dir.mkdir(mode=0o700)
    except FileExistsError as error:
        raise BlindOutputExists(
            "another process already claimed this blind output directory"
        ) from error
    os.chmod(blind_dir, 0o700)
    (blind_dir / "private").mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(blind_dir / "private", 0o700)
    for candidate in CANDIDATES:
        _atomic_write_text(
            paths[f"candidate_{candidate}"],
            markdown[candidate],
        )
    _write_json(paths["mapping"], private_mapping)
    _write_json(paths["template"], rating_template)
    _write_json(paths["manifest"], public_manifest)
    return public_manifest


def _load_public_state(blind_dir: Path) -> tuple[dict[str, Any], dict[str, str]]:
    paths = _blind_paths(blind_dir)
    manifest = _read_json(paths["manifest"])
    if manifest.get("schema_version") != BLIND_MANIFEST_VERSION:
        raise BlindSourceInvalid("blind manifest has an unsupported version")
    hashes: dict[str, str] = {}
    try:
        for candidate in CANDIDATES:
            expected = manifest["candidates"][candidate]["sha256"]
            actual = _file_sha256(paths[f"candidate_{candidate}"])
            if expected != actual:
                raise BlindCandidateTampered(
                    f"Candidate {candidate} changed after blind preparation"
                )
            hashes[candidate] = actual
    except (KeyError, TypeError) as error:
        raise BlindSourceInvalid("blind manifest candidate metadata is invalid") from error
    return manifest, hashes


def submit_blind_rating(
    *,
    blind_dir: Path,
    submission: Mapping[str, Any],
) -> dict[str, Any]:
    manifest, hashes = _load_public_state(blind_dir)
    try:
        rating = BlindRatingSubmission.model_validate(submission)
    except (ValidationError, ValueError, TypeError) as error:
        raise BlindRatingInvalid("blind rating did not match the strict schema") from error
    stored = {
        "schema_version": BLIND_RATING_VERSION,
        "mapping_commitment_sha256": manifest["mapping_commitment_sha256"],
        "candidate_sha256": hashes,
        "rating": rating.model_dump(mode="json"),
    }
    path = _blind_paths(blind_dir)["rating"]
    if path.exists():
        if _read_json(path) == stored:
            return stored
        raise BlindRatingConflict("a different blind rating is already stored")
    _write_json(path, stored)
    return stored


def _verify_private_mapping(
    *,
    public: Mapping[str, Any],
    private: Mapping[str, Any],
    candidate_hashes: Mapping[str, str],
) -> dict[str, str]:
    mapping = private.get("mapping")
    if (
        not isinstance(mapping, dict)
        or set(mapping) != set(CANDIDATES)
        or set(mapping.values()) != set(ARMS)
    ):
        raise BlindSourceInvalid("private candidate mapping is invalid")
    payload = _commitment_payload(
        mapping=mapping,
        salt=str(private.get("salt", "")),
        source_manifest_sha256=str(public.get("source_manifest_sha256", "")),
        candidate_sha256=candidate_hashes,
    )
    commitment = _json_sha256(payload)
    if commitment != public.get("mapping_commitment_sha256") or commitment != private.get(
        "mapping_commitment_sha256"
    ):
        raise BlindCandidateTampered("private mapping commitment verification failed")
    return {str(candidate): str(arm) for candidate, arm in mapping.items()}


def reveal_blind_experiment(
    *,
    experiment_dir: Path,
    blind_dir: Path,
) -> dict[str, Any]:
    paths = _blind_paths(blind_dir)
    if not paths["rating"].exists():
        raise BlindRatingRequired("a blind rating must be stored before reveal")
    public, candidate_hashes = _load_public_state(blind_dir)
    private = _read_json(paths["mapping"])
    mapping = _verify_private_mapping(
        public=public,
        private=private,
        candidate_hashes=candidate_hashes,
    )
    source_manifest, _, qualities = _load_experiment(experiment_dir)
    if _json_sha256(source_manifest) != public.get("source_manifest_sha256"):
        raise BlindCandidateTampered("M3.7b manifest changed after blind preparation")
    rating = _read_json(paths["rating"])
    if (
        rating.get("schema_version") != BLIND_RATING_VERSION
        or rating.get("mapping_commitment_sha256") != public.get("mapping_commitment_sha256")
        or rating.get("candidate_sha256") != candidate_hashes
    ):
        raise BlindCandidateTampered("stored rating does not match the frozen candidates")
    try:
        human_rating = BlindRatingSubmission.model_validate(rating["rating"]).model_dump(
            mode="json"
        )
    except (KeyError, ValidationError, ValueError, TypeError) as error:
        raise BlindRatingInvalid("stored blind rating is invalid") from error

    summaries: dict[str, Any] = {}
    for candidate, arm in mapping.items():
        deterministic = DeterministicDraftQualityResult.model_validate(
            qualities[arm]["deterministic"]
        )
        report = DraftQualityReport.model_validate(qualities[arm]["report"])
        dimensions = report.model_self_review.dimensions if report.model_self_review else []
        style_card = next(
            (card for card in dimensions if card.dimension == "personal_style_match"),
            None,
        )
        summaries[candidate] = {
            "arm": arm,
            "deterministic": {
                "score": deterministic.deterministic_score,
                "estimated_duration_minutes": (deterministic.metrics.estimated_duration_minutes),
                "blocker_count": sum(
                    finding.status == "blocker" for finding in deterministic.findings
                ),
                "warning_count": sum(
                    finding.status == "warning" for finding in deterministic.findings
                ),
            },
            "reviewer": {
                "personal_style_match_score": (None if style_card is None else style_card.score),
                "model_score": report.experimental_model_score,
                "overall_score": report.experimental_overall_score,
                "decision": report.decision,
            },
        }
    reveal = {
        "schema_version": BLIND_REVEAL_VERSION,
        "event": "writing_style_ab.blind.revealed",
        "mapping": mapping,
        "human_rating": human_rating,
        "candidate_summaries": summaries,
        "winner_selected": False,
    }
    if paths["reveal"].exists():
        if _read_json(paths["reveal"]) == reveal:
            return reveal
        raise BlindOutputExists("a different reveal is already stored")
    _write_json(paths["reveal"], reveal)
    return reveal


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare, rate, or reveal one local blind A/B.")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--experiment-dir", type=Path, required=True)
    prepare.add_argument("--blind-dir", type=Path, required=True)
    rate = commands.add_parser("rate")
    rate.add_argument("--blind-dir", type=Path, required=True)
    rate.add_argument("--input", type=Path, required=True)
    reveal = commands.add_parser("reveal")
    reveal.add_argument("--experiment-dir", type=Path, required=True)
    reveal.add_argument("--blind-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_blind_experiment(
                experiment_dir=args.experiment_dir.expanduser().resolve(),
                blind_dir=args.blind_dir.expanduser().resolve(),
            )
        elif args.command == "rate":
            result = submit_blind_rating(
                blind_dir=args.blind_dir.expanduser().resolve(),
                submission=_read_json(args.input.expanduser().resolve()),
            )
        else:
            result = reveal_blind_experiment(
                experiment_dir=args.experiment_dir.expanduser().resolve(),
                blind_dir=args.blind_dir.expanduser().resolve(),
            )
    except Exception as error:
        result = {
            "event": "writing_style_ab.blind.blocked",
            "error_code": getattr(error, "code", "writing_style_ab_blind_error"),
            "error_type": type(error).__name__,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
