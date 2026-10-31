"""F4-3b: what the browser workflow runner (web/js/browser_workflows.js) must share with the connector
(pixel_connector/workflows.py, tasks.handoff_prompt).

`rules()` is the data web/js/workflow_rules.js must hold; `vectors()` are test cases whose expected outcome the
connector's own code computes (template validation, the user's template list, step prompts with the previous step's
output fenced as data). tests/test_f43b_browser_workflows.py checks both files against this module;
tests/js/browser_workflows.test.js runs the same vectors through the JS port. To refresh after a deliberate change to
the connector:  python3 tests/_workflow_shared.py --write
"""

import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import _helpers  # noqa: F401  (sys.path)

from pixel_connector import tasks as T
from pixel_connector import workflows as W
from pixel_connector.config import DEFAULT_LIMITS
from pixel_connector.workspaces import MAX_SUB

ROOT = Path(__file__).resolve().parent.parent
RULES_JS = ROOT / "web" / "js" / "workflow_rules.js"
VECTORS = ROOT / "tests" / "workflow_vectors.json"
RULES_PREFIX = ("// GENERATED from pixel_connector/workflows.py by `python3 tests/_workflow_shared.py --write`; do not edit.\n"
                "// F4-3b: the browser runs templates with the connector's limits, texts and state machine "
                "(tests/test_f43b_browser_workflows.py).\n"
                "export const WORKFLOW_RULES = ")
FENCE = "abcd1234"


def rules() -> dict:
    return {
        "maxSteps": W.MAX_STEPS, "maxTemplates": W.MAX_TEMPLATES, "nameMax": W.NAME_MAX,
        "descriptionMax": W.DESCRIPTION_MAX, "roleMax": W.ROLE_MAX, "titleMax": W.TITLE_MAX, "promptMax": W.PROMPT_MAX,
        "briefMax": W.BRIEF_MAX, "maxSub": MAX_SUB, "costBounds": list(W.COST_BOUNDS), "turnBounds": list(W.TURN_BOUNDS),
        "timeoutBounds": list(W.TIMEOUT_BOUNDS), "approvalTimeoutBounds": list(W.APPROVAL_TIMEOUT_BOUNDS),
        "defaultGuardrails": dict(W.DEFAULT_GUARDRAILS), "defaultApprovalTimeoutS": W.DEFAULT_APPROVAL_TIMEOUT_S,
        "keepFinished": W.KEEP_FINISHED, "stepEngines": list(W.STEP_ENGINES), "profiles": list(W.PROFILES),
        "maxPromptBytes": DEFAULT_LIMITS["maxPromptBytes"],
        "text": {"briefHead": W.BRIEF_HEAD, "handoffDefaultNote": T.HANDOFF_DEFAULT_NOTE,
                 "handoffDataHead": T.HANDOFF_DATA_HEAD, "handoffCutNote": T.HANDOFF_CUT_NOTE},
        "handoffOutputMax": T.HANDOFF_OUTPUT_MAX,
        "stepTransitions": {k: sorted(v) for k, v in W.STEP_TRANSITIONS.items()},
        "runTransitions": {k: sorted(v) for k, v in W.RUN_TRANSITIONS.items()},
        "taskOutcome": {k: list(v) for k, v in W.TASK_OUTCOME.items()},
        "builtinTemplates": [W.validate_template(t, False) for t in W.BUILTIN_TEMPLATES],
    }


def rules_js() -> str:
    return RULES_PREFIX + json.dumps(rules(), ensure_ascii=False, indent=2, sort_keys=True) + ";\n"


# -- template vectors -------------------------------------------------------------------------------------------------
def _agent(sid="a1", **kw):
    s = {"id": sid, "role": "Yazar", "engine": "claude", "prompt": "Kısa bir not yaz."}
    s.update(kw)
    return s


def _tpl(*steps, **kw):
    t = {"id": "deneme", "name": "Deneme", "steps": list(steps) or [_agent()]}
    t.update(kw)
    return t


def _without(d, *keys):
    d = copy.deepcopy(d)
    for k in keys:
        d.pop(k, None)
    return d


def _builder_shaped():
    """What web/js/template_builder.js stores and sends (every field explicit)."""
    return {"id": "kendi-sablonum", "name": "Kendi şablonum", "description": None, "folder": None,
            "guardrails": {"maxCostUsd": 1.5, "maxTurns": 30, "timeoutS": 900},
            "steps": [{"id": "adim-1", "type": "agent", "role": "Araştırmacı", "engine": "claude",
                       "engines": ["claude", "ollama"], "profile": "readonly", "model": None, "prompt": "Araştır.",
                       "usePrevious": False, "useBrief": True},
                      {"id": "onay-2", "type": "approval", "title": "Devam edilsin mi?", "timeoutS": 900},
                      {"id": "adim-3", "type": "agent", "role": "Uzman 3", "engine": "claude",
                       "engines": ["claude", "ollama"], "profile": "edit", "model": None, "prompt": "Yaz.",
                       "usePrevious": True, "useBrief": True}]}


def _template_inputs():
    """(name, raw, autoEnabled)"""
    return [
        ("minimal", _tpl(), False),
        ("builder_shaped", _builder_shaped(), False),
        ("builder_empty_description", dict(_builder_shaped(), description=""), False),
        ("all_fields", _tpl(_agent("a1", engines=["ollama", "claude"], profile="edit", model="qwen2.5-coder:7b",
                                   useBrief=True),
                            {"id": "ok", "type": "approval", "title": "Onay?", "timeoutS": 30},
                            _agent("a2", usePrevious=True), description="  Açıklama  ", folder="./a//b/",
                            guardrails={"maxCostUsd": 2, "maxTurns": 10.0, "timeoutS": 60}), False),
        ("name_stripped", _tpl(name="  Ad  "), False),
        ("name_python_whitespace", _tpl(name="\x1c\x85Ad　"), False),
        ("name_bom_kept", _tpl(name="﻿Ad"), False),
        ("name_too_long_astral", _tpl(name="😀" * (W.NAME_MAX + 1)), False),
        ("name_at_limit_astral", _tpl(name="😀" * W.NAME_MAX), False),
        ("name_empty", _tpl(name="   "), False),
        ("name_missing", _without(_tpl(), "name"), False),
        ("name_bidi", _tpl(name="a‮b"), False),
        ("name_line_separator", _tpl(name="a b"), False),
        ("unknown_field", _tpl(extra=1), False),
        ("not_object", [1], False),
        ("id_upper", _tpl(id="Deneme"), False),
        ("id_trailing_newline", _tpl(id="deneme\n"), False),
        ("id_33", _tpl(id="a" * 33), False),
        ("description_empty", _tpl(description=""), False),
        ("description_null", _tpl(description=None), False),
        ("description_number", _tpl(description=5), False),
        ("folder_absolute", _tpl(folder="/etc"), False),
        ("folder_drive", _tpl(folder="C:x"), False),
        ("folder_dotdot", _tpl(folder="a/../b"), False),
        ("folder_backslash", _tpl(folder="a\\b"), False),
        ("folder_only_dots", _tpl(folder="./."), False),
        ("folder_control", _tpl(folder="a\x01"), False),
        ("folder_number", _tpl(folder=1), False),
        ("folder_too_long", _tpl(folder="a" * (MAX_SUB + 1)), False),
        ("steps_empty", _tpl(steps=[]), False),
        ("steps_nine", _tpl(*[_agent("a%d" % i) for i in range(9)]), False),
        ("steps_not_list", _tpl(steps={"a": 1}), False),
        ("no_agent_step", _tpl({"id": "ok", "type": "approval", "title": "Onay?"}), False),
        ("step_id_duplicate", _tpl(_agent("a1"), _agent("a1")), False),
        ("step_id_missing", _tpl(_without(_agent(), "id")), False),
        ("step_type_null", _tpl(_agent(type=None)), False),
        ("step_type_unknown", _tpl(_agent(type="script")), False),
        ("step_unknown_field", _tpl(_agent(cmd="ls")), False),
        ("engine_missing", _tpl(_without(_agent(), "engine")), False),
        ("engine_openai", _tpl(_agent(engine="openai")), False),
        ("engines_duplicate", _tpl(_agent(engines=["claude", "claude"])), False),
        ("engines_without_engine", _tpl(_agent(engines=["ollama"])), False),
        ("engines_null", _tpl(_agent(engines=None)), False),
        ("profile_null", _tpl(_agent(profile=None)), False),
        ("profile_root", _tpl(_agent(profile="root")), False),
        ("profile_dev", _tpl(_agent(profile="dev")), False),
        ("profile_auto_off", _tpl(_agent(profile="auto")), False),
        ("profile_auto_on", _tpl(_agent(profile="auto")), True),
        ("model_bad", _tpl(_agent(model="-x")), False),
        ("model_64", _tpl(_agent(model="m" * 64)), False),
        ("model_65", _tpl(_agent(model="m" * 65)), False),
        ("use_previous_first", _tpl(_agent(usePrevious=True)), False),
        ("use_previous_string", _tpl(_agent(), _agent("a2", usePrevious="yes")), False),
        ("use_brief_null", _tpl(_agent(useBrief=None)), False),
        ("use_brief_number", _tpl(_agent(useBrief=1)), False),
        ("role_and_engine_bad", _tpl(_agent(role="", engine="x")), False),
        ("role_bad_after_flags", _tpl(_agent(role="", usePrevious=True)), False),
        ("role_too_long", _tpl(_agent(role="r" * (W.ROLE_MAX + 1))), False),
        ("prompt_newlines_tabs", _tpl(_agent(prompt="  bir\n\tiki  ")), False),
        ("prompt_cr", _tpl(_agent(prompt="bir\r\niki")), False),
        ("prompt_at_bytes", _tpl(_agent(prompt="ş" * (W.PROMPT_MAX // 2))), False),
        ("prompt_over_bytes", _tpl(_agent(prompt="ş" * (W.PROMPT_MAX // 2) + "a")), False),
        ("prompt_zero_width", _tpl(_agent(prompt="a​b")), False),
        ("approval_unknown_field", _tpl(_agent(), {"id": "ok", "type": "approval", "title": "x", "role": "y"}), False),
        ("approval_timeout_29", _tpl(_agent(), {"id": "ok", "type": "approval", "title": "x", "timeoutS": 29}), False),
        ("approval_timeout_float", _tpl(_agent(), {"id": "ok", "type": "approval", "title": "x", "timeoutS": 60.5}),
         False),
        ("approval_title_bad_and_timeout_bad", _tpl(_agent(), {"id": "ok", "type": "approval", "title": "",
                                                               "timeoutS": 1}), False),
        ("guard_unknown", _tpl(guardrails={"maxCost": 1}), False),
        ("guard_cost_bool", _tpl(guardrails={"maxCostUsd": True}), False),
        ("guard_cost_low", _tpl(guardrails={"maxCostUsd": 0.001}), False),
        ("guard_cost_float", _tpl(guardrails={"maxCostUsd": 0.25}), False),
        ("guard_turns_float", _tpl(guardrails={"maxTurns": 2.5}), False),
        ("guard_timeout_high", _tpl(guardrails={"timeoutS": W.TIMEOUT_BOUNDS[1] + 1}), False),
        ("guard_null", _tpl(guardrails=None), False),
        ("guard_list", _tpl(guardrails=[]), False),
        ("name_bad_and_guard_bad", _tpl(name="", guardrails={"maxTurns": 0}), False),
    ]


def _expect_template(raw, auto):
    try:
        return {"ok": W.validate_template(copy.deepcopy(raw), auto)}
    except W.WorkflowError as e:
        return {"code": e.code, "detail": str(e)}


def template_vectors() -> list:
    return [{"name": n, "template": raw, "autoEnabled": auto, "expect": _expect_template(raw, auto)}
            for n, raw, auto in _template_inputs()]


def _library_inputs():
    """(name, the user's list) → load_templates over a workflows.json holding {"v": 1, "templates": list}."""
    many = [_tpl(id="t%d" % i) for i in range(W.MAX_TEMPLATES + 2)]
    return [("empty", []),
            ("two_good_one_bad", [_tpl(id="a"), _tpl(id="b", name=""), _tpl(id="c")]),
            ("duplicate_id", [_tpl(id="a"), _tpl(id="a", name="İkinci")]),
            ("builtin_id_taken", [_tpl(id="statik-site")]),
            ("too_many", many),
            ("dev_profile_kept", [_tpl(_agent(profile="dev"))])]


def _expect_library(items):
    with tempfile.TemporaryDirectory() as home:
        with open(os.path.join(home, W.TEMPLATES_FILE), "w", encoding="utf-8") as f:
            json.dump({"v": 1, "templates": items}, f, ensure_ascii=False)
        tpls, invalid = W.load_templates(home, False)
    return {"ids": [t["id"] for t in tpls], "invalid": invalid}


def library_vectors() -> list:
    return [{"name": n, "templates": items, "expect": _expect_library(items)} for n, items in _library_inputs()]


# -- step prompts (tasks.handoff_prompt and WorkflowEngine._step_prompt, with the fence fixed) -------------------------
def _prompt_inputs():
    """(name, step spec changes, brief, previous output or None, previous name, limit)"""
    big = "ç" * (T.HANDOFF_OUTPUT_MAX // 2 + 7)
    return [("note_only", {}, "", None, "Mimar", 32768),
            ("with_brief", {"useBrief": True}, "Bir site yap", None, "Mimar", 32768),
            ("with_previous", {"usePrevious": True}, "", "Plan:\n- a\n- b", "Mimar", 32768),
            ("previous_and_brief", {"usePrevious": True, "useBrief": True}, "İstek", "çıktı", "Mimar", 32768),
            ("previous_empty", {"usePrevious": True}, "", "", "Mimar", 32768),
            ("previous_name_cleaned", {"usePrevious": True}, "", "x", "Ad‮ Soyad" + "z" * 30, 32768),
            ("previous_cut_at_output_max", {"usePrevious": True}, "", big, "Mimar", 32768),
            ("previous_cut_by_limit", {"usePrevious": True}, "", "ş" * 900, "Mimar", 1200),
            ("prompt_too_large", {"useBrief": True}, "b" * 400, None, "Mimar", 300),
            ("handoff_too_large", {"usePrevious": True}, "", "x", "Mimar", 100)]


class _Run(SimpleNamespace):
    def previous_agent(self, index):
        return self.prev


def _expect_prompt(spec_changes, brief, output, prev_name, limit):
    spec = dict(W.validate_template(_tpl(_agent("a1"), _agent("a2")), False)["steps"][1])
    spec.update(spec_changes)
    if not spec["usePrevious"]:
        spec["usePrevious"] = False
    prev = SimpleNamespace(result=output, agent_name=prev_name, spec={"role": prev_name}) \
        if output is not None else None
    run = _Run(brief=brief, prev=prev)
    eng = W.WorkflowEngine.__new__(W.WorkflowEngine)
    eng.app = SimpleNamespace(cfg={"limits": {"maxPromptBytes": limit}})
    st = SimpleNamespace(spec=spec, index=1, truncated=False)
    real = T.secrets.token_hex
    T.secrets.token_hex = lambda n: FENCE
    try:
        return {"ok": eng._step_prompt(run, st), "truncated": st.truncated}
    except T.TaskError as e:
        return {"code": e.code}
    finally:
        T.secrets.token_hex = real


def prompt_vectors() -> list:
    out = []
    for name, ch, brief, output, prev_name, limit in _prompt_inputs():
        out.append({"name": name, "note": _agent()["prompt"], "step": ch, "brief": brief, "previous": output, "previousName": prev_name,
                    "limit": limit, "fence": FENCE, "expect": _expect_prompt(ch, brief, output, prev_name, limit)})
    return out


def vectors() -> dict:
    return {"templates": template_vectors(), "libraries": library_vectors(), "prompts": prompt_vectors()}


def write() -> None:
    RULES_JS.write_text(rules_js(), encoding="utf-8")
    VECTORS.write_text(json.dumps(vectors(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if "--write" in sys.argv:
        write()
        print("wrote", RULES_JS, VECTORS)
