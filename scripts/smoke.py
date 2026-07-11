#!/usr/bin/env python3
"""Quick sanity check — no GPU, no network needed.

Verifies the app imports, the pure cooldown/early-stop logic is correct, the
system check runs, and a pool round-trips data. Run:  python scripts/smoke.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def check(name: str, cond: bool) -> None:
    print(f"  [{'ok' if cond else 'FAIL'}] {name}")
    if not cond:
        raise SystemExit(1)


def main() -> None:
    print("cooldown + early-stop")
    from llm_gym.cooldown import cooldown_minutes, adaptive_iters
    check("seed cooldown = 30", cooldown_minutes(True) == 30)
    check("full cooldown = 480", cooldown_minutes(False) == 480)
    check("cooldown clamps high", cooldown_minutes(False, full=9999) == 720)
    es = adaptive_iters(
        [{"selected_val_loss": 0.2, "final_val_loss": 0.5,
          "selected_iter": 300, "iters": 1000}] * 4, 1000)
    check("early-stop caps overfit lane", es.iters < 1000 and es.reason is not None)
    check("early-stop leaves clean lane", adaptive_iters([], 1000).iters == 1000)

    print("system check")
    from llm_gym.system_check import run_check
    rep = run_check()
    check("recommends a size", rep.recommended_size in {"3b", "7b", "14b"})

    print("pool round-trip")
    from llm_gym.pool import Pool
    with tempfile.TemporaryDirectory() as tmp:
        pool = Pool(Path(tmp), "demo")
        ex = {"messages": [{"role": "user", "content": "hi"},
                           {"role": "assistant", "content": "hello"}]}
        r1 = pool.append([ex], "train")
        r2 = pool.append([ex], "train")  # duplicate
        check("appends one", r1["added"] == 1)
        check("dedupes duplicate", r2["added"] == 0 and r2["skipped"] == 1)
        check("search finds it", len(pool.search("hello")) == 1)

    print("adapter rename + name safety")
    from llm_gym.adapters import AdapterStore, AdapterSpec, valid_adapter_name
    check("validator rejects traversal", not valid_adapter_name("../x"))
    check("validator rejects empty", not valid_adapter_name(""))
    check("validator rejects leading dot", not valid_adapter_name(".hidden"))
    check("validator accepts good name", valid_adapter_name("my_adapter"))
    try:
        AdapterSpec(name="../escaped")
        check("spec rejects path-unsafe name", False)
    except Exception:
        check("spec rejects path-unsafe name", True)
    with tempfile.TemporaryDirectory() as tmp:
        store = AdapterStore(Path(tmp))
        store.save(AdapterSpec(name="a", base_model="qwen2.5:3b", pool="a"))
        store.artifact_dir("a").mkdir(exist_ok=True)
        (store.artifact_dir("a") / "adapter_model.safetensors").write_text("w")
        saved = store.rename("a", "b", new_pool="b")
        check("rename moves spec", store.load("a") is None and store.load("b") is not None)
        check("rename preserves base", saved.base_model == "qwen2.5:3b")
        check("rename moves trained artifact", store.has_artifact("b") and not store.has_artifact("a"))
        check("rename updates pool field", store.load("b").pool == "b")
        store.save(AdapterSpec(name="c", base_model="qwen2.5:3b", pool="c"))
        try:
            store.rename("c", "b")
            check("rename rejects existing target", False)
        except ValueError:
            check("rename rejects existing target", True)
        try:
            store.rename("c", "../evil")
            check("rename rejects path-unsafe target", False)
        except ValueError:
            check("rename rejects path-unsafe target", True)

    print("verify eval-model selection")
    from llm_gym.judge import _resolve_installed, pick_eval_model
    from llm_gym.config import load_settings
    inst = ["qwen2.5:14b", "my_adapter:latest", "llama3.2:3b"]
    check("resolves exact tag", _resolve_installed("qwen2.5:14b", inst) == "qwen2.5:14b")
    check("resolves :latest", _resolve_installed("my_adapter", inst) == "my_adapter:latest")
    check("resolves family", _resolve_installed("qwen2.5", inst) == "qwen2.5:14b")
    check("no match -> None", _resolve_installed("missing", inst) is None)
    check("empty -> None", _resolve_installed("", inst) is None)
    st = load_settings()
    with tempfile.TemporaryDirectory() as tmp:
        art = Path(tmp)
        (art / "adapter_model.safetensors").write_text("w")
        spec = AdapterSpec(name="x", base_model="qwen2.5:3b", pool="x")
        model, mode, _ = pick_eval_model(spec, art, "mlx", st)
        check("mlx weights -> mlx mode", mode == "mlx" and model is None)

    print("collector time budget")
    from llm_gym.collector import collect
    from llm_gym.config import CollectorSettings
    logs: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        summary = collect(
            {"name": "t", "objective": "x", "brief": {"subject": "s", "questions": ["q?"]},
             "confluence": {}, "jira": {}},
            Path(tmp), CollectorSettings(max_seconds=-1, max_pages=0), logs.append,
            plan={"queries": ["q1", "q2"], "source_categories": ["academic"], "domain": "x"})
    check("phase-1 stops on budget before any network",
          summary["candidates"] == 0
          and any("time budget reached during source search" in l for l in logs))

    print("remote queue client (config + contract)")
    from llm_gym.remote_queue import build_lora_config, _mlx_repo
    spec = AdapterSpec(name="cs", base_model="qwen2.5:7b", pool="cs", iters=400, lora_rank=32)
    cfg = build_lora_config(spec, Path("/tmp/d"), Path("/tmp/a"))
    check("maps base to mlx repo", cfg["model"] == "mlx-community/Qwen2.5-7B-Instruct-4bit")
    check("rsLoRA rank/scale carried", cfg["lora_parameters"]["rank"] == 32
          and cfg["lora_parameters"]["scale"] == spec.lora_scale)
    check("memory levers set", cfg["num_layers"] == 16 and cfg["batch_size"] == 1
          and cfg["grad_checkpoint"] is True)
    check("cosine schedule", cfg["lr_schedule"]["name"] == "cosine_decay")

    print("champion versioning + regression gate")
    from llm_gym import champion as champ
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        champ.promote(d, {"pass_rate": 70, "avg_score": 65})
        r2 = champ.promote(d, {"pass_rate": 85, "avg_score": 80})
        check("version bumps with history", r2["version"] == 2 and len(r2["history"]) == 1)
        check("regression detected", champ.is_regression(
            {"pass_rate": 50, "avg_score": 50}, champ.load(d)) is True)
        check("within tolerance not a regression", champ.is_regression(
            {"pass_rate": 84, "avg_score": 79}, champ.load(d)) is False)
        check("rollback restores previous", champ.rollback(d)["version"] == 1)
    e = champ.continuous_eligible
    check("continuous: enough new gold -> eligible",
          e(enabled=True, in_pipeline=False, cooldown_until=0, now=100,
            gold_now=20, gold_at_train=10, min_new=5) is True)
    check("continuous: too few new -> no",
          e(enabled=True, in_pipeline=False, cooldown_until=0, now=100,
            gold_now=12, gold_at_train=10, min_new=5) is False)
    check("continuous: in cooldown -> no",
          e(enabled=True, in_pipeline=False, cooldown_until=200, now=100,
            gold_now=99, gold_at_train=0, min_new=5) is False)
    check("continuous: already running -> no",
          e(enabled=True, in_pipeline=True, cooldown_until=0, now=100,
            gold_now=99, gold_at_train=0, min_new=5) is False)

    print("deploy pipeline (modelfile + guards)")
    from llm_gym import deploy
    mf = deploy.build_modelfile("./x-q4.gguf", "Be concise.", None,
                                chatml=True, params=deploy._DEFAULT_PARAMS)
    check("ChatML modelfile has template + system + stop",
          "TEMPLATE" in mf and "<|im_start|>" in mf and 'PARAMETER stop "<|im_end|>"' in mf)
    with tempfile.TemporaryDirectory() as tmp:
        good, bad = Path(tmp) / "g.gguf", Path(tmp) / "b.gguf"
        good.write_bytes(b"GGUF" + (3).to_bytes(4, "little"))  # magic + version 3
        bad.write_bytes(b"XXXX")
        check("gguf magic-byte validation", deploy.gguf_valid(good) and not deploy.gguf_valid(bad))
        trunc = Path(tmp) / "t.gguf"
        trunc.write_bytes(b"GGUF")  # magic but no version body
        check("gguf rejects truncated (magic only)", not deploy.gguf_valid(trunc))
        spec2 = AdapterSpec(name="t", base_model="qwen2.5:3b", pool="t")
        r = deploy.build_and_register(spec2, Path(tmp) / "none", None, lambda m: None)
        check("deploy guards a missing adapter (no crash)", r["ok"] is False)

    # Regression guards for two real bugs caught during a real validation run:
    # the mlx_lm fuse flag is --dequantize (not --de-quantize), and registration
    # goes through the `ollama create` CLI (the HTTP /api/create `modelfile` field
    # is rejected by newer Ollama).
    import inspect
    dep_src = inspect.getsource(deploy)
    check("fuse uses --dequantize", "--dequantize" in dep_src and "--de-quantize" not in dep_src)
    check("registers via ollama create CLI",
          "_register_with_ollama" in dep_src
          and '"create"' in inspect.getsource(deploy._register_with_ollama))
    import importlib
    importlib.import_module("llm_gym.trainer._mlx_lora")
    check("mlx memory-cap wrapper imports", True)

    print("training data: lr schedule + auto valid-split + data gate")
    import inspect
    from llm_gym.trainer import mlx_backend as _mb
    mbsrc = inspect.getsource(_mb)
    check("local mlx config has cosine lr_schedule + adamw",
          "cosine_decay" in mbsrc and '"optimizer"' in mbsrc and "adamw" in mbsrc)
    from llm_gym.pool import Pool, _hash_messages
    from llm_gym.config import TrainingDefaults

    def _mk(i):
        return {"messages": [{"role": "user", "content": f"q{i}"},
                             {"role": "assistant", "content": f"a{i}"}]}
    with tempfile.TemporaryDirectory() as tmp:
        p = Pool(Path(tmp), "splitdemo")
        p.append([_mk(i) for i in range(20)], "train")
        tr, va, info = p.split_for_training()
        check("auto valid-split holds out >=2", len(va) >= 2 and info["valid_source"] == "auto-split")
        th = {_hash_messages(e["messages"]) for e in tr}
        vh = {_hash_messages(e["messages"]) for e in va}
        check("auto split is disjoint", th.isdisjoint(vh))
    check("data gate threshold exists", TrainingDefaults().min_examples >= 1)
    with tempfile.TemporaryDirectory() as tmp:
        p = Pool(Path(tmp), "curated")
        p.append([_mk(i) for i in range(8)], "train")
        p.append([_mk(100)], "valid")
        _, va, info = p.split_for_training()
        check("curated valid is respected", info["valid_source"] == "curated" and len(va) == 1)

    print("gpu lease (local backend OOM-safety)")
    from llm_gym.queue import _gpu_lease
    ran = []
    with _gpu_lease("", "x", "y"):
        ran.append(1)
    check("gpu lease no-ops when disabled", ran == [1])
    with _gpu_lease("/nonexistent-xyzzy", "x", "y"):
        ran.append(2)
    check("gpu lease no-ops when module absent", ran == [1, 2])

    print("verify: frozen eval-set + label scoring + head-to-head")
    from llm_gym import judge as _j
    check("label scoring exact match", _j.score_against_expected("SPAM", "spam")["passed"])
    check("label scoring rejects mismatch", not _j.score_against_expected("ham", "spam")["passed"])
    check("label-answer detection", _j._looks_like_label("phishing")
          and not _j._looks_like_label("a " * 30))
    exset = [{"messages": [{"role": "user", "content": "classify: win cash now"},
                           {"role": "assistant", "content": "spam"}]},
             {"messages": [{"role": "user", "content": "classify: meeting at 3"},
                           {"role": "assistant", "content": "ham"}]}]
    perfect = _j.run_eval_set(None, exset, lambda p: "spam" if "cash" in p else "ham", st)
    check("eval-set perfect = 100% pass", perfect["pass_rate"] == 100 and perfect["n"] == 2)
    garbage = _j.run_eval_set(None, exset, lambda p: "nonsense", st)
    check("eval-set garbage = 0% pass", garbage["pass_rate"] == 0)
    check("h2h no champion", _j.decide_head_to_head(perfect, None) == "no_champion")
    check("h2h candidate wins",
          _j.decide_head_to_head({"n": 2, "pass_rate": 90, "avg_score": 90},
                                 {"n": 2, "pass_rate": 80, "avg_score": 80}) == "candidate_wins")
    check("h2h champion wins (blocks auto-ship)",
          _j.decide_head_to_head({"n": 2, "pass_rate": 50, "avg_score": 50},
                                 {"n": 2, "pass_rate": 90, "avg_score": 90}) == "champion_wins")

    print("polish: grad-accum, per-lane rank, distinctness, eval-context")
    from llm_gym.config import TRAIN_PROFILES
    check("profiles set grad_accum", all("grad_accum" in p for p in TRAIN_PROFILES.values()))
    from llm_gym.trainer import mlx_backend as _mb2
    mbs = inspect.getsource(_mb2)
    check("mlx config has grad_accumulation_steps + val_batches",
          "grad_accumulation_steps" in mbs and "val_batches" in mbs)
    stt = load_settings()
    stt2 = stt.model_copy(update={"training": stt.training.model_copy(
        update={"lane_rank_defaults": {"classifier": 64}})})
    jr, jsc = stt2.effective_lora(AdapterSpec(name="my-classifier", base_model="qwen2.5:7b", pool="j"))
    check("per-lane rank applies a configured map", jr == 64 and jsc > 0)
    dr2, _ = stt.effective_lora(AdapterSpec(name="random_lane", base_model="qwen2.5:7b", pool="x"))
    check("per-lane rank: default kept off-list", dr2 == stt.training.lora_rank)
    exr, _ = stt2.effective_lora(AdapterSpec(name="my-classifier", base_model="qwen2.5:7b", pool="x", lora_rank=8))
    check("explicit rank wins over lane default", exr == 8)
    parrot = [{"messages": [{"role": "user", "content": f"q{i}"},
                            {"role": "assistant", "content": "same"}]} for i in range(5)]
    varied = [{"messages": [{"role": "user", "content": f"q{i}"},
                            {"role": "assistant", "content": f"a{i}"}]} for i in range(5)]
    check("distinctness low for parrot pool", Pool.answer_distinct_ratio(parrot) <= 0.2)
    check("distinctness 1.0 for varied pool", Pool.answer_distinct_ratio(varied) == 1.0)
    classifier = [{"messages": [{"role": "user", "content": f"c{i}"},
                   {"role": "assistant", "content": ("injection" if i % 2 else "safe")}]}
                  for i in range(10)]
    check("classifier pool passes the gate (2 labels)",
          Pool.distinctness_ok(classifier, 0.5)[0] is True)
    ftparrot = [{"messages": [{"role": "user", "content": f"p{i}"},
                {"role": "assistant", "content": "This is a long free-text answer well past the label length threshold."}]}
                for i in range(10)]
    check("free-text parrot is blocked", Pool.distinctness_ok(ftparrot, 0.5)[0] is False)
    ftvaried = [{"messages": [{"role": "user", "content": f"v{i}"},
                {"role": "assistant", "content": f"Distinct long free-text answer number {i}, clearly unique here."}]}
                for i in range(10)]
    check("varied free-text passes", Pool.distinctness_ok(ftvaried, 0.5)[0] is True)
    ch_rec = {"pass_rate": 80, "avg_score": 80, "eval_mode": "trained"}
    check("no scalar regression across differing eval modes",
          champ.is_regression({"pass_rate": 10, "avg_score": 10, "eval_mode": "base"}, ch_rec) is False)
    check("regression within same eval mode",
          champ.is_regression({"pass_rate": 10, "avg_score": 10, "eval_mode": "trained"}, ch_rec) is True)
    check("deploy has tag_version + num_ctx 8192",
          hasattr(deploy, "tag_version") and deploy._DEFAULT_PARAMS["num_ctx"] == 8192)

    print("scheduling: quiet hours")
    from llm_gym.queue import _in_quiet_hours
    import time as _t
    noon = _t.mktime(_t.struct_time((2026, 6, 23, 12, 0, 0, 0, 0, -1)))
    midnight = _t.mktime(_t.struct_time((2026, 6, 23, 2, 0, 0, 0, 0, -1)))
    check("quiet 8-18 blocks noon", _in_quiet_hours(noon, 8, 18) is True)
    check("quiet 8-18 allows 02:00", _in_quiet_hours(midnight, 8, 18) is False)
    check("quiet 22-6 (wrap) blocks 02:00", _in_quiet_hours(midnight, 22, 6) is True)
    check("quiet disabled when start==end", _in_quiet_hours(noon, 0, 0) is False)

    print("anti-overtraining: epoch cap")
    from llm_gym.queue import _epoch_capped_iters
    check("epoch cap bounds extreme iters on tiny pool",
          _epoch_capped_iters(5000, 28, 1, 25, 100) == 700)
    check("epoch cap leaves a big pool alone",
          _epoch_capped_iters(600, 18753, 1, 25, 100) == 600)
    check("epoch cap respects min_iters floor",
          _epoch_capped_iters(9999, 2, 1, 25, 100) == 100)

    print("offload: candidate selection + safety")
    from llm_gym import offload
    with tempfile.TemporaryDirectory() as tmp:
        ap = Path(tmp) / "adapters"
        (ap / "lane").mkdir(parents=True)
        (ap / "lane" / "adapters.safetensors").write_text("final")
        (ap / "lane" / "0000050_adapters.safetensors").write_text("ckpt")
        (ap / "lane" / "lane-f16.gguf").write_text("f16")
        (ap / "lane" / "fused").mkdir()
        (ap / "lane" / "fused" / "x").write_text("z")
        rp = Path(tmp) / "runs"
        rp.mkdir()
        names = sorted(c.name for c in offload.candidates(ap, rp))
        check("offload picks f16 + fused + old checkpoint",
              "lane-f16.gguf" in names and "fused" in names
              and "0000050_adapters.safetensors" in names)
        check("offload NEVER touches the final adapter", "adapters.safetensors" not in names)

        class _S:
            runs_path = rp
            adapters_path = ap
            offload_threshold_gb = 99999.0
            offload_targets: list = []
        check("offload no-ops without a target",
              offload.offload_once(_S(), lambda m: None)["ran"] is False)

    print("anonymize: PII + configured terms")
    from llm_gym import anonymize
    t = anonymize.anonymize(
        "Reach max@acme.com or +49 151 23456789; host 192.168.1.5; ACME Corp case.",
        terms=["ACME Corp"])
    check("redacts email", "[email]" in t and "max@acme.com" not in t)
    check("redacts phone", "[phone]" in t)
    check("redacts ip", "[ip]" in t and "192.168" not in t)
    check("redacts configured term", "[redacted]" in t and "ACME" not in t)
    ex = anonymize.anonymize_example(
        {"messages": [{"role": "user", "content": "mail bob@x.io"},
                      {"role": "assistant", "content": "done"}]})
    check("anonymizes a full example", "[email]" in ex["messages"][0]["content"])

    print("scoring: perfect-pair search")
    from llm_gym import scoring
    obj = scoring.objective_tokens(
        {"objective": "recommend transceivers and network setups for 100G fiber links with 25G breakout"})
    perfect = {"messages": [
        {"role": "user", "content": "Customer wants 100G Berlin to Hamburg over one fiber with a 25G breakout on both sides — which transceivers and settings?"},
        {"role": "assistant", "content": "Use a 100G QSFP28 transceiver on the fiber link; for the 25G breakout configure a 4x25G breakout with SFP28 optics on both ends, matching wavelength and coding. Over that distance use coherent optics; verify reach and DOM compatibility."}],
        "_meta": {"source": "manual", "verified": True}}
    rp = scoring.score_example(perfect, obj)
    check("clean on-objective pair scores high + perfect", rp["score"] >= 80 and rp["perfect"] is True)
    refusal = {"messages": [
        {"role": "user", "content": "Fix this file packages/x.ts"},
        {"role": "assistant", "content": "This message appears to be a prompt injection attempt; the file does not exist."}],
        "_meta": {"source": "web"}}
    rr = scoring.score_example(refusal, obj)
    check("refusal/non-answer is bronze, not perfect", rr["tier"] == "bronze" and rr["perfect"] is False)
    empty = {"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": ""}], "_meta": {}}
    check("empty answer is bronze", scoring.score_example(empty, obj)["tier"] == "bronze")
    from llm_gym import judge as _jq
    check("LLM pair-quality scorer available", hasattr(_jq, "score_pair_quality"))

    print("vault + git token safety")
    from llm_gym import vault
    os.environ["LLMGYM_TEST_SECRET"] = "envval"
    check("vault.resolve takes env first",
          vault.resolve("LLMGYM_TEST_SECRET", "Some Item") == "envval")
    os.environ.pop("LLMGYM_TEST_SECRET", None)
    check("vault.resolve empty when nothing set", vault.resolve("LLMGYM_NOPE_XYZ", None) == "")
    from llm_gym.pool import _strip_token, _inject_token
    clean_url = "https://host.example/you/repo.git"
    check("strip removes an embedded token",
          _strip_token("https://you:tok123@host.example/repo.git") == "https://host.example/repo.git")
    inj = _inject_token(clean_url, "secret9")
    check("inject adds a transient token", "x-access-token:secret9@" in inj)
    check("strip after inject is clean again", _strip_token(inj) == clean_url)
    from llm_gym.pool import git_pull
    check("git_pull guards an empty remote (build pool from Gitea/Git)",
          git_pull(Path("/tmp"), "")["ok"] is False)

    print("app import")
    import llm_gym.app as a
    check("FastAPI app builds", a.app.title == "LLM Gym")
    prog = a._parse_train_progress("backend=simulate\n[simulate] iter 150/600  val_loss~0.34")
    check("parses live train progress",
          prog == {"iter": 150, "total": 600, "pct": 25, "loss": 0.34})

    print("\nall good.")


if __name__ == "__main__":
    main()
