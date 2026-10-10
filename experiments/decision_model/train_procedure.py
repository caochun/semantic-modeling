"""Train the Procedure Five typed dataset with Unsloth's official DecisionTrainer.

This runner is separate from train.py because train.py preserves the older
choice-only pilot contract. The model, dataset builder, loss, trainer and
adapter export all come from the installed Unsloth implementation.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
from pathlib import Path
from types import SimpleNamespace

from .contract_check import LOCK, assert_no_truncation
from .data import SPLITS, decode_row, digest, read_jsonl, write_json, write_jsonl
from .download import verify_model
from .procedure_state_dataset import DEFAULT_OUTPUT, verify as verify_procedure_dataset
from .explicit_state_dataset import verify as verify_explicit_state_dataset
from .runtime_check import update_probe
from .train import runtime_source_hashes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=float, default=2)
    parser.add_argument("--max-steps", type=int, default=-1)
    parser.add_argument("--max-length", type=int, default=4096)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument(
        "--dataset-kind", choices=("procedure", "explicit"), default="procedure",
        help="dataset verifier to use; explicit is the natural-language branch seed dataset",
    )
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    if args.smoke_test:
        args.max_steps = 1
    if args.epochs <= 0 or args.max_steps == 0 or args.max_steps < -1:
        parser.error("epochs must be positive; max-steps must be -1 or positive")
    verify_dataset = verify_explicit_state_dataset if args.dataset_kind == "explicit" else verify_procedure_dataset
    validation = verify_dataset(args.data)
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("output must be a new, empty run directory")
    if args.preflight_only:
        print(json.dumps({"data": validation, "runtime_training_tested": False}, ensure_ascii=False, indent=2))
        return

    import unsloth
    from unsloth import DecisionTrainer, FastDecisionModel, is_bfloat16_supported
    import torch
    from transformers import TrainingArguments
    from unsloth.models._decision_common import _parsed
    from unsloth.models.clef import encode_record

    if not torch.cuda.is_available():
        raise RuntimeError("This runner targets CUDA")
    lock = json.loads(LOCK.read_text())
    model_manifest = verify_model(args.model_dir, lock["pilot_model"])
    source_hashes = runtime_source_hashes(Path(unsloth.__file__).resolve().parent, lock)
    args.output.mkdir(parents=True, exist_ok=True)
    run_config = {
        **vars(args), "data": str(args.data), "output": str(args.output),
        "model_dir": str(args.model_dir.resolve()), "model": lock["pilot_model"],
        "audited_unsloth_revision": lock["revision"], "local_model_manifest": model_manifest,
        "installed_decision_source_sha256": source_hashes,
        "runner_source_sha256": {"train_procedure.py": digest(Path(__file__))},
        "dataset_manifest_sha256": digest(args.data / "manifest.json"),
        "versions": {name: importlib.metadata.version(name) for name in ("unsloth", "torch", "transformers", "peft")},
        "cuda": {"torch_cuda_version": torch.version.cuda, "gpu": torch.cuda.get_device_name(0),
                 "memory_bytes": torch.cuda.get_device_properties(0).total_memory,
                 "bf16_supported": is_bfloat16_supported()},
        "review_status": "source_checked_synthetic_pilot_not_expert_approved",
        "baseline_interpretation": "plain LM plus newly initialized Clef head; not original LM QA capability",
        "export_format": "official_trainer_adapter_with_float32_head",
        "question_types": ["noul", "choice", "score"],
    }
    write_json(args.output / "run_config.json", run_config)
    write_json(args.output / "status.json", {"stage": "loading_model", "training_completed": False})
    try:
        model, tokenizer = FastDecisionModel.from_pretrained(
            model_name=str(args.model_dir.resolve()), revision=lock["pilot_model"]["revision"],
            local_files_only=True, decision_head="clef", max_seq_length=args.max_length,
            load_in_4bit=True, random_state=3407,
        )
        model = FastDecisionModel.get_peft_model(
            model, r=16, lora_alpha=16, lora_dropout=0,
            use_gradient_checkpointing="unsloth", random_state=3407,
        )
        rows = {split: read_jsonl(args.data / f"{split}.jsonl") for split in SPLITS}
        plain_tokenizer = getattr(tokenizer, "tokenizer", tokenizer)
        items, reports = {}, {}
        config_only = SimpleNamespace(is_clef=True, decision_config={"max_len": args.max_length})
        for split in SPLITS:
            assert_no_truncation(rows[split], plain_tokenizer, encode_record, _parsed, args.max_length)
            items[split], reports[split] = FastDecisionModel.build_dataset(rows[split], tokenizer, model)
            expected_decisions = sum(len(decode_row(row)["questions"]) for row in rows[split])
            if reports[split]["skipped"] or reports[split]["truncated"] or reports[split]["total"] != expected_decisions or len(items[split]) != len(rows[split]):
                raise ValueError(f"Upstream data loss: {split} {reports[split]}")
        write_json(args.output / "build_reports.json", reports)
        if not args.smoke_test:
            baseline = FastDecisionModel.evaluate(model, tokenizer, items["test"], batch_size=1)
            write_json(args.output / "baseline.json", baseline)
        write_json(args.output / "status.json", {"stage": "training", "training_completed": False})
        probe = update_probe(model)
        total_steps = args.max_steps if args.max_steps > 0 else math.ceil(math.ceil(len(items["train"]) / 4) * args.epochs)
        warmup_steps = 0 if args.smoke_test else math.ceil(total_steps * 0.1)
        run_config["scheduler"] = {"planned_optimizer_steps": total_steps, "warmup_steps": warmup_steps}
        write_json(args.output / "run_config.json", run_config)
        trainer = DecisionTrainer(
            model=model, processing_class=tokenizer, train_dataset=items["train"],
            head_learning_rate=1e-4,
            args=TrainingArguments(
                output_dir=str(args.output / "checkpoints"), per_device_train_batch_size=1,
                gradient_accumulation_steps=4, num_train_epochs=args.epochs, max_steps=args.max_steps,
                learning_rate=2e-4, lr_scheduler_type="cosine", warmup_steps=warmup_steps,
                weight_decay=0.01, bf16=is_bfloat16_supported(), fp16=not is_bfloat16_supported(),
                eval_strategy="no", save_strategy="no", logging_steps=1, report_to="none",
                seed=3407, data_seed=3407,
            ),
            callbacks=[probe],
        )
        trained = trainer.train()
        update_report = probe.report(trained.training_loss)
        write_json(args.output / "update_verification.json", update_report)
        if not update_report["passed"]:
            raise RuntimeError("Finite loss/gradients and head/LoRA parameter updates were not all verified")
        if not args.smoke_test:
            uncalibrated = FastDecisionModel.evaluate(model, tokenizer, items["test"], batch_size=1)
            calibration = FastDecisionModel.calibrate(model, tokenizer, items["calibration"], batch_size=1)
            after = FastDecisionModel.evaluate(model, tokenizer, items["test"], batch_size=1)
        trainer.save_model(str(args.output / "adapter"))
        FastDecisionModel.for_inference(model)
        probes = [decode_row(row) for row in rows["train"][:6]]
        write_json(args.output / "reload_reference.json", {
            "absolute_tolerance": 0.02,
            "probes": [{"id": row["id"], "state": row["state"], "questions": row["questions"]} for row in probes],
            "predictions": [FastDecisionModel.predict(model, tokenizer, row["state"], row["questions"]) for row in probes],
        })
        predictions = []
        if not args.smoke_test:
            for raw in rows["test"]:
                row = decode_row(raw)
                predictions.append({"id": row["id"], "answers": FastDecisionModel.predict(model, tokenizer, row["state"], row["questions"]), "gold": row["gold"]})
            write_jsonl(args.output / "test_predictions.jsonl", predictions)
            write_json(args.output / "metrics.json", {
                "baseline_fresh_head": baseline, "after_uncalibrated": uncalibrated,
                "calibration": calibration, "after_calibrated": after,
                "training": trained.metrics, "test_cases": len(rows["test"]),
                "field_validity_claimed": False,
            })
        write_json(args.output / "status.json", {"stage": "awaiting_reload_verification", "training_completed": True, "reload_verified": False})
    except Exception as exc:
        write_json(args.output / "status.json", {"stage": "failed", "training_completed": False,
                                                  "error_type": type(exc).__name__, "error": str(exc)})
        raise


if __name__ == "__main__":
    main()
