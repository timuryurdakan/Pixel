"""F4-3a: what the browser Boss (web/js/browser_boss.js) must share with the connector (pixel_connector/boss.py).

`rules()` is the data web/js/boss_rules.js must hold, built from boss.py; `plan_vectors()` / `block_vectors()` are
test cases whose expected outcome boss.py computes. tests/test_f43_browser_boss.py checks both files against this
module; tests/js/browser_boss.test.js runs the same vectors through the JS port. To refresh after a deliberate change
to boss.py:  python3 tests/_boss_shared.py --write
"""

import copy
import json
import sys
from pathlib import Path

import _helpers  # noqa: F401  (sys.path)

from pixel_connector import boss as B
from pixel_connector import workflows as W

ROOT = Path(__file__).resolve().parent.parent
RULES_JS = ROOT / "web" / "js" / "boss_rules.js"
VECTORS = ROOT / "tests" / "boss_plan_vectors.json"
RULES_PREFIX = ("// GENERATED from pixel_connector/boss.py by `python3 tests/_boss_shared.py --write`; do not edit.\n"
                "// F4-3a: the browser Boss shares the connector's limits, roles and prompt texts "
                "(tests/test_f43_browser_boss.py).\n"
                "export const BOSS_RULES = ")


def rules() -> dict:
    return {
        "planVersion": B.PLAN_VERSION, "maxOrders": B.MAX_ORDERS, "maxFixOrders": B.MAX_FIX_ORDERS,
        "maxFixRounds": B.MAX_FIX_ROUNDS, "summaryMax": B.SUMMARY_MAX, "titleMax": B.TITLE_MAX,
        "goalMaxBytes": B.GOAL_MAX_BYTES, "acceptMaxItems": B.ACCEPT_MAX_ITEMS, "acceptMax": B.ACCEPT_MAX,
        "filesMaxItems": B.FILES_MAX_ITEMS, "fileMax": B.FILE_MAX, "fileBadChars": "".join(sorted(B.FILE_BAD_CHARS)),
        "promptMax": W.PROMPT_MAX, "resultMax": B.RESULT_MAX, "planJsonMax": B.PLAN_JSON_MAX,
        "briefMax": W.BRIEF_MAX, "approvalTimeoutS": B.APPROVAL_TIMEOUT_S, "planRetries": B.PLAN_RETRIES,
        "defaultDepartment": B.DEFAULT_DEPARTMENT,
        "departments": B.departments_public(),
        "roles": {rid: {k: r[k] for k in ("id", "name", "badge", "profile", "instruction")}
                  for rid, r in B.ALL_ROLES.items()},
        "boss": {"id": B.BOSS_ROLE["id"], "name": B.BOSS_NAME, "instruction": B.BOSS_ROLE["instruction"]},
        "profileText": dict(B.PROFILE_TEXT),
        "text": {"orderHead": B.ORDER_HEAD, "orderRules": B.ORDER_RULES, "filesNone": B.FILES_NONE,
                 "planHead": B.PLAN_HEAD, "planRules": B.PLAN_RULES, "planSchema": B.PLAN_SCHEMA,
                 "planOutput": B.PLAN_OUTPUT, "planBriefHead": B.PLAN_BRIEF_HEAD, "retryPrompt": B.RETRY_PROMPT,
                 "planMark": B.PLAN_MARK, "reviewMark": B.REVIEW_MARK, "reviewHead": B.REVIEW_HEAD,
                 "reviewPrompt": B.REVIEW_PROMPT, "reviewFix": B.REVIEW_FIX, "reviewNoFix": B.REVIEW_NO_FIX,
                 "reviewReport": B.REVIEW_REPORT, "reportPatron": B.REPORT_PATRON},
        "extractReasons": dict(B.EXTRACT_REASONS),
        "noteText": dict(B.NOTE_TEXT),
    }


def rules_js() -> str:
    return RULES_PREFIX + json.dumps(rules(), ensure_ascii=False, indent=2, sort_keys=True) + ";\n"


# -- vectors --------------------------------------------------------------------------------------------------------
def _order(oid="o1", role="frontend", **kw):
    o = {"id": oid, "role": role, "title": "Koyu tema renkleri", "goal": "Koyu tema renk değişkenlerini ekle.",
         "accept": ["Renkler değişir"], "files": ["web/css/style.css"], "after": []}
    o.update(kw)
    return o


def _plan(*orders, **kw):
    p = {"v": 1, "summary": "Koyu tema ekle.", "orders": list(orders) or [_order()]}
    p.update(kw)
    return p


def _without(d, *keys):
    d = copy.deepcopy(d)
    for k in keys:
        d.pop(k, None)
    return d


def _plan_inputs():
    """(name, plan, options) — options: department, enabledProfiles, fixRound."""
    soft = ("readonly", "edit")
    out = [("valid", _plan(), {}),
           ("valid_two_with_after", _plan(_order("o1"), _order("o2", "docs", after=["o1"], files=[])), {}),
           ("valid_whitespace_trimmed", _plan(_order(title="  Başlık  ")), {}),
           ("valid_content_department", _plan(_order(role="yazar", files=["yazilar/a.md"])),
            {"department": "icerik"}),
           ("valid_wildcard_file", _plan(_order(files=["web/*.css"])), {}),
           ("valid_goal_newlines", _plan(_order(goal="bir\nüç\n\tgirinti")), {}),
           ("valid_goal_at_byte_limit", _plan(_order(goal="ş" * (B.GOAL_MAX_BYTES // 2))), {}),
           ("valid_title_at_limit_astral", _plan(_order(title="😀" * B.TITLE_MAX)), {}),
           ("unknown_plan_field", _plan(extra=1), {}),
           ("missing_summary", _without(_plan(), "summary"), {}),
           ("version_true", _plan(v=True), {}), ("version_float", _plan(v=1.0), {}), ("version_2", _plan(v=2), {}),
           ("summary_too_long", _plan(summary="a" * (B.SUMMARY_MAX + 1)), {}),
           ("summary_newline", _plan(summary="a\nb"), {}),
           ("summary_bidi", _plan(summary="ad‮gizli"), {}),
           ("summary_zero_width_at_end", _plan(summary="özet​"), {}),
           ("no_orders", _plan(orders=[]), {}),
           ("five_orders", _plan(*[_order("o%d" % i) for i in range(1, 6)]), {}),
           ("fix_round_three", _plan(_order("o1"), _order("o2"), _order("o3")), {"fixRound": True}),
           ("orders_not_list", _plan(orders={"o1": _order()}), {}),
           ("order_unknown_field", _plan(_order(cost=1)), {}),
           ("order_missing_goal", _plan(_without(_order(), "goal")), {}),
           ("order_id_o0", _plan(_order("o0")), {}), ("order_id_o10", _plan(_order("o10")), {}),
           ("order_id_cyrillic", _plan(_order("о1")), {}),
           ("order_id_duplicate", _plan(_order("o1"), _order("o1")), {}),
           ("role_unknown", _plan(_order(role="admin")), {}), ("role_patron", _plan(_order(role="patron")), {}),
           ("role_other_department", _plan(_order(role="frontend")), {"department": "icerik"}),
           ("role_dev_unavailable", _plan(_order(role="test")), {"enabledProfiles": soft}),
           ("title_empty", _plan(_order(title="   ")), {}),
           ("title_too_long_astral", _plan(_order(title="😀" * (B.TITLE_MAX + 1))), {}),
           ("title_tab", _plan(_order(title="a\tb")), {}),
           ("goal_over_bytes", _plan(_order(goal="ş" * (B.GOAL_MAX_BYTES // 2 + 1))), {}),
           ("goal_cr", _plan(_order(goal="a\rb")), {}),
           ("accept_empty_list", _plan(_order(accept=[])), {}),
           ("accept_six", _plan(_order(accept=["a"] * 6)), {}),
           ("accept_item_too_long", _plan(_order(accept=["a" * (B.ACCEPT_MAX + 1)])), {}),
           ("accept_not_string", _plan(_order(accept=[1])), {}),
           ("files_absolute", _plan(_order(files=["/etc/passwd"])), {}),
           ("files_dotdot", _plan(_order(files=["a/../b"])), {}),
           ("files_home", _plan(_order(files=["~/x"])), {}),
           ("files_backslash", _plan(_order(files=["a\\b"])), {}),
           ("files_glob_brackets", _plan(_order(files=["a[1].js"])), {}),
           ("files_double_slash", _plan(_order(files=["a//b"])), {}),
           ("files_padded", _plan(_order(files=[" a.js"])), {}),
           ("files_thirteen", _plan(_order(files=["f%d" % i for i in range(13)])), {}),
           ("after_forward", _plan(_order("o1", after=["o2"]), _order("o2")), {}),
           ("after_self", _plan(_order("o1", after=["o1"])), {}),
           ("after_duplicate", _plan(_order("o1"), _order("o2", after=["o1", "o1"])), {}),
           ("after_not_list", _plan(_order("o1"), _order("o2", after="o1")), {}),
           ("order_prompt_too_long", _plan(_order(goal="a" * B.GOAL_MAX_BYTES, accept=["b" * B.ACCEPT_MAX] * 5,
                                                  files=["d/" + "c" * 150 + str(i) for i in range(12)])), {}),
           ("plan_not_object", [1, 2], {})]
    return out


def _expect_plan(plan, opts):
    roles = B.department_roles(opts.get("department", B.DEFAULT_DEPARTMENT))
    try:
        got = B.validate_plan(copy.deepcopy(plan), opts.get("enabledProfiles", B.DEFAULT_ENABLED_PROFILES),
                              opts.get("fixRound", False), roles)
        return {"ok": got}
    except B.BossError as e:
        return {"code": e.code, "detail": str(e)}


def plan_vectors() -> list:
    out = []
    for name, plan, opts in _plan_inputs():
        o = dict(opts)
        if "enabledProfiles" in o:
            o["enabledProfiles"] = list(o["enabledProfiles"])
        out.append({"name": name, "plan": plan, "options": o, "expect": _expect_plan(plan, opts)})
    return out


def _block_inputs():
    n = "abcd1234"
    s, e = B.plan_markers(n)
    good = json.dumps(_plan(), ensure_ascii=False)
    return [("one_block", "Okudum.\n%s\n%s\n%s\nbitti" % (s, good, e)),
            ("fenced", "%s\n```json\n%s\n```\n%s" % (s, good, e)),
            ("no_block", "Plan yazmayı unuttum."),
            ("two_blocks", "%s\n{}\n%s\n%s\n{}\n%s" % (s, e, s, e)),
            ("end_before_start", "%s\n{}\n%s" % (e, s)),
            ("other_nonce", "%s\n%s\n%s" % (B.plan_markers("ffff0000")[0], good, B.plan_markers("ffff0000")[1])),
            ("not_json", "%s\n{bozuk\n%s" % (s, e)),
            ("duplicate_key", '%s\n{"v": 1, "v": 1}\n%s' % (s, e)),
            ("nan", '%s\n{"v": NaN}\n%s' % (s, e)),
            ("array", "%s\n[1]\n%s" % (s, e)),
            ("empty", "   "),
            ("too_big_block", "%s\n{\"x\": \"%s\"}\n%s" % (s, "a" * (B.PLAN_JSON_MAX + 1), e)),
            ("too_big_reply", "%s\n{}\n%s%s" % (s, e, "z" * (B.RESULT_MAX + 1)))]


def _expect_block(output):
    try:
        return {"ok": B._json_block(output, *B.plan_markers("abcd1234"))}
    except B.BossError as e:
        return {"code": e.code, "detail": str(e)}


def block_vectors() -> list:
    return [{"name": name, "nonce": "abcd1234", "output": out, "expect": _expect_block(out)}
            for name, out in _block_inputs()]


def vectors() -> dict:
    return {"plans": plan_vectors(), "blocks": block_vectors()}


def write() -> None:
    RULES_JS.write_text(rules_js(), encoding="utf-8")
    VECTORS.write_text(json.dumps(vectors(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if "--write" in sys.argv:
        write()
        print("wrote", RULES_JS, VECTORS)
