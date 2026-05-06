"""ツール実行のユーザー向けシステム行（リトライ時・通常返答末尾）の表示モードと整形。"""

from __future__ import annotations

from enum import Enum

_TOOL_NOTICE_VALUES = frozenset({"full", "abbrev", "minimal", "hidden"})


class ToolNoticeMode(str, Enum):
    FULL = "full"
    ABBREV = "abbrev"
    MINIMAL = "minimal"
    HIDDEN = "hidden"


def parse_tool_notice_mode(raw: str, *, legacy_show_ri_text: str) -> ToolNoticeMode:
    r = (raw or "").strip().lower()
    if r in _TOOL_NOTICE_VALUES:
        return ToolNoticeMode(r)
    v = (legacy_show_ri_text or "true").strip().lower()
    legacy_off = v in ("0", "false", "no", "off")
    return ToolNoticeMode.HIDDEN if legacy_off else ToolNoticeMode.FULL


def show_retry_notice(mode: ToolNoticeMode) -> bool:
    return mode is not ToolNoticeMode.HIDDEN


def retry_line_abbreviated(mode: ToolNoticeMode) -> bool:
    return mode in (ToolNoticeMode.ABBREV, ToolNoticeMode.MINIMAL)


def show_normal_footer(mode: ToolNoticeMode) -> bool:
    return mode in (ToolNoticeMode.FULL, ToolNoticeMode.ABBREV)


# コマンド種別の表示（各 2 文字・日本語）
_CMD_LABEL_JP: dict[str, str] = {
    "SPEAK": "発話",
    "SEARCH": "検索",
    "NEWS": "報道",
    "WEATHER": "天気",
    "READ-PAGE": "参照",
    "LIST-FILES": "一覧",
    "READ-TEXT": "読取",
    "WRITE-TEXT": "記述",
    "APPEND-TEXT": "追記",
    "SAVE-LOG": "保存",
    "GET-SETTING": "取得",
    "SET-SETTING": "更新",
}


def command_label_jp(cmd: str) -> str:
    c = (cmd or "SPEAK").strip().upper()
    return _CMD_LABEL_JP.get(c, "実行")


# ARGS の要約が「無いのと同義」とみなすもの（ラベルのみで表示）
_MEANINGLESS_ARG_SUMMARIES = frozenset(
    {
        "",
        "-",
        "(一覧)",
        "(queryなし)",
        "(filenameなし)",
        "(地名なし)",
        "(urlなし)",
        "(key)",
    }
)


def abbrev_detail(detail: str, *, max_keep: int = 5) -> str:
    d = (detail or "").strip()
    if len(d) <= max_keep:
        return d
    return d[:max_keep] + "…"


def _args_fragment_for_display(detail: str, *, abbreviated: bool) -> str:
    raw = (detail or "").strip()
    if raw in _MEANINGLESS_ARG_SUMMARIES:
        return ""
    if abbreviated:
        return abbrev_detail(raw, max_keep=5)
    return (raw[:120] + "…") if len(raw) > 120 else raw


def usage_suffix_tokens(usage: dict | None) -> str:
    """末尾のトークン表示（例: t:5902）。"""
    if not usage:
        return "t:—"
    tt = usage.get("total_tokens")
    if tt is not None:
        return f"t:{tt}"
    pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if pt is not None and ct is not None:
        return f"t:{pt}+{ct}"
    return "t:—"


def format_notice_line(
    bracket_tag: str,
    cmd: str,
    detail: str,
    usage: dict | None,
    *,
    abbreviated: bool,
) -> str:
    """例: ``[N1]発話 t:5902`` / ``[R2]検索 キーワード… t:123``（ARGS が無いときはラベルのみ）。"""
    label = command_label_jp(cmd)
    frag = _args_fragment_for_display(detail, abbreviated=abbreviated)
    tok = usage_suffix_tokens(usage)
    tag = (bracket_tag or "").strip()
    if frag:
        return f"{tag}{label} {frag} {tok}"
    return f"{tag}{label} {tok}"


def format_retry_notice_line(
    retry_round: int,
    cmd: str,
    detail: str,
    usage: dict | None,
    *,
    abbreviated: bool,
) -> str:
    return format_notice_line(f"[R{retry_round}]", cmd, detail, usage, abbreviated=abbreviated)


def format_normal_footer_line(cmd: str, detail: str, usage: dict | None, *, abbreviated: bool) -> str:
    return format_notice_line("[N1]", cmd, detail, usage, abbreviated=abbreviated)


ALLOWED_TOOL_NOTICE_DISPLAY_HINT = "full | abbrev | minimal | hidden"
