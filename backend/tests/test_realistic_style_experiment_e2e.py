from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from epiphany.checkpoint_e2e import BACKEND_DIR
from epiphany.config import Settings
from epiphany.realistic_style_experiment_e2e import (
    DEFAULT_FIXTURE_PATH,
    build_realistic_provider,
    load_realistic_style_fixture,
    main,
)
from epiphany.runtime.providers import DeepSeekProvider


def _writing_sample(index: int) -> dict[str, object]:
    scenes = (
        "厨房的灯坏了一半，我把半颗柠檬放回冰箱，关门以后又重新打开看了一眼。",
        "视频电话里只看得见阳台上的葱，我妈把镜头举得很近，叶子边缘有一点发黄。",
        "修伞师傅把一根断掉的伞骨放在掌心，我站在旁边等，鞋边慢慢沾上雨水。",
        "那条消息在屏幕上停了三天，我每次点开都先看时间，然后把手机扣回桌面。",
    )
    text = (
        f"{scenes[index]} 我当时没有马上解释这件事，只记住桌面很凉，水杯外面留下一圈印子。"
        "后来我试着写一个结论，写到第二行又删掉，因为那句话听起来比当天的我确定得多。"
        "我确实有一点难过，也有一点想笑，这两种感觉放在一起并不整齐。"
        "第二天早上事情没有突然变好，不过我终于回了那条一直绕开的消息。"
        "现在再看，我仍然不知道应该给它起什么名字。"
        "先把这个动作留下来就够了，其他的以后再说。"
    )
    return {
        "sample_kind": "spoken_transcript" if index == 3 else "written_prose",
        "source": {
            "title": f"合成风格样本{index + 1}",
            "source_type": "writing_sample",
            "text": text,
            "metadata": {
                "synthetic": True,
                "contains_personal_data": False,
                "role": "style_only",
            },
        },
    }


def _write_fixture(path: Path) -> dict[str, object]:
    base_path = BACKEND_DIR / "fixtures/e2e/m3-3-quality-contract.zh-CN.json"
    payload = json.loads(base_path.read_text(encoding="utf-8"))
    payload.update(
        {
            "fixture_id": "test-realistic-style-persona",
            "persona": {
                "name": "林澄",
                "age": 30,
                "profile": "虚构的上海内容运营；偏爱具体场景、克制表达和未封闭结尾。",
            },
            "writing_samples": [_writing_sample(index) for index in range(4)],
            "style_reference": {
                "ownership_attested": True,
                "model_processing_consent": True,
                "usage": "style_only",
            },
            "expected": {
                **payload["expected"],
                "workflow_version": "v8",
                "initial_task_count": 4,
                "initial_artifact_count": 6,
                "initial_model_call_count": 3,
                "final_task_count": 6,
                "final_artifact_count": 12,
                "final_model_call_count": 5,
            },
        }
    )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def test_committed_realistic_fixture_is_modular_coherent_and_crosses_readiness() -> None:
    manifest = json.loads(DEFAULT_FIXTURE_PATH.read_text(encoding="utf-8"))
    fixture = load_realistic_style_fixture(DEFAULT_FIXTURE_PATH)

    def non_whitespace(value: str) -> int:
        return len("".join(value.split()))

    style_texts = [item["source"]["text"] for item in fixture["writing_samples"]]
    factual_text = "\n".join(
        [
            *(source["text"] for source in fixture["initial_sources"]),
            fixture["supplemental_source"]["text"],
        ]
    )

    assert fixture["fixture_id"] == "m3-7-realistic-persona-moving-out"
    assert fixture["persona"]["name"] == "林澄"
    assert len(style_texts) == 4
    assert all(non_whitespace(text) >= 900 for text in style_texts)
    assert all("text_file" in item["source"] for item in manifest["writing_samples"])
    assert all("text" not in item["source"] for item in manifest["writing_samples"])
    assert fixture["raw_fixture_readiness"]["initial"]["status"] == "needs_more_material"
    assert fixture["raw_fixture_readiness"]["final"]["status"] == "ready"
    assert (
        fixture["raw_fixture_readiness"]["initial"]["counts"]["available_source_char_count"]
        < fixture["raw_fixture_readiness"]["final"]["target_script_chars_min"]
    )
    assert (
        fixture["raw_fixture_readiness"]["final"]["counts"]["available_source_char_count"]
        >= fixture["raw_fixture_readiness"]["final"]["target_script_chars_min"]
    )
    for episode_marker in ("透明自封袋", "浅色方块", "四十分钟"):
        assert episode_marker in factual_text
        assert all(episode_marker not in text for text in style_texts)


def test_loader_validates_four_independent_style_only_sources(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    _write_fixture(fixture_path)

    fixture = load_realistic_style_fixture(fixture_path)

    assert fixture["persona"]["name"] == "林澄"
    assert len(fixture["initial_sources"]) == 3
    assert len(fixture["writing_samples"]) == 4
    assert {item["sample_kind"] for item in fixture["writing_samples"]} == {
        "written_prose",
        "spoken_transcript",
    }
    assert fixture["expected"]["workflow_version"] == "v8"
    assert fixture["expected"]["final_artifact_count"] == 12


def test_dry_run_is_read_only_and_never_prints_key_or_sample_text(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    fixture_path = tmp_path / "fixture.json"
    payload = _write_fixture(fixture_path)
    database_path = tmp_path / "dry-run.db"
    output_dir = tmp_path / "dry-run-output"
    secret = "synthetic-deepseek-key-never-print"
    monkeypatch.setenv("EPIPHANY_DEEPSEEK_API_KEY", secret)

    exit_code = main(
        [
            "--provider",
            "deepseek",
            "--editor-model",
            "deepseek-v4-flash",
            "--reviewer-model",
            "deepseek-v4-pro",
            "--fixture",
            str(fixture_path),
            "--database",
            str(database_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()
    preflight = json.loads(captured.out)
    sample_text = payload["writing_samples"][0]["source"]["text"]
    assert exit_code == 0
    assert preflight["event"] == "realistic_style_experiment_e2e.preflight"
    assert preflight["mode"] == "dry-run"
    assert preflight["network_enabled"] is False
    assert preflight["paid_api_call_possible"] is False
    assert preflight["factual_source_count"] == 3
    assert preflight["writing_sample_source_count"] == 4
    assert preflight["source_run_model_call_ceiling"] == 5
    assert preflight["effective_provider_limits"] == {
        "editor_bundle_chars": 48_000,
        "editor_output_tokens": 20_000,
    }
    assert secret not in captured.out
    assert sample_text not in captured.out
    assert not database_path.exists()
    assert not output_dir.exists()


def test_realistic_provider_uses_configured_editor_limits() -> None:
    settings = Settings(
        deepseek_max_editor_bundle_chars=48_321,
        deepseek_editor_max_tokens=12_345,
    )
    provider = build_realistic_provider(
        provider_name="deepseek",
        settings=settings,
        api_key="synthetic-key",
        model="deepseek-v4-flash",
    )
    assert isinstance(provider, DeepSeekProvider)
    assert provider.max_editor_bundle_chars == 48_321
    assert provider.editor_max_tokens == 12_345


def test_fake_e2e_produces_v8_run_exports_redacted_logs_and_ab_contract(
    tmp_path: Path,
    capsys: object,
) -> None:
    fixture_path = tmp_path / "fixture.json"
    payload = _write_fixture(fixture_path)
    database_path = tmp_path / "realistic.db"
    output_dir = tmp_path / "realistic-output"

    exit_code = main(
        [
            "--provider",
            "fake",
            "--execute",
            "--fixture",
            str(fixture_path),
            "--database",
            str(database_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()
    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    ab_preflight = json.loads(
        (output_dir / "writing-style-ab-preflight.json").read_text(encoding="utf-8")
    )
    log_text = (output_dir / "runtime.jsonl").read_text(encoding="utf-8")
    with sqlite3.connect(database_path) as connection:
        source_count = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]

    assert exit_code == 0, captured.err
    assert report["passed"] is True
    assert report["failures"] == []
    assert report["waiting_run"]["workflow_version"] == "v8"
    assert report["waiting_run"]["artifact_count"] == 6
    assert report["final_run"]["task_count"] == 6
    assert report["final_run"]["artifact_count"] == 12
    assert report["final_run"]["model_calls_recorded"] == 5
    assert report["quality"]["synthetic_feedback"]["final_artifact_count"] == 13
    assert report["checks"]["writing_samples_imported"] is True
    assert report["checks"]["writing_style_reference_persisted"] is True
    assert report["checks"]["controlled_ab_input_frozen"] is True
    assert ab_preflight["only_variable_is_writing_sample"] is True
    assert ab_preflight["treatment_reaches_editor_prompt"] is True
    assert ab_preflight["writing_style_source_count"] == 4
    assert ab_preflight["writing_style_readiness"] == "ready"
    assert source_count == 8
    assert (output_dir / "interview-scaffold.md").exists()
    assert (output_dir / "podcast-draft.md").exists()
    assert (output_dir / "show-notes.md").exists()
    assert (output_dir / "draft-quality-report.json").exists()
    assert (output_dir / "draft-quality-report.md").exists()
    safety_report = json.loads((output_dir / "safety-report.json").read_text(encoding="utf-8"))
    assert safety_report["passed"] is True
    assert safety_report["redaction"]["contains_source_or_style_text"] is False
    assert safety_report["redaction"]["contains_prompt_text"] is False
    assert safety_report["redaction"]["contains_api_key"] is False
    for sample in payload["writing_samples"]:
        assert sample["source"]["text"] not in log_text
