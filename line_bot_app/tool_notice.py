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


_CMD_ABBREV_4: dict[str, str] = {
    "SPEAK": "SPEK",
    "SEARCH": "SRCH",
    "NEWS": "NEWS",
    "WEATHER": "WEAT",
    "READ-PAGE": "RDPG",
    "LIST-FILES": "LIST",
    "READ-TEXT": "READ",
    "WRITE-TEXT": "WRTX",
    "APPEND-TEXT": "APPX",
    "SAVE-LOG": "SAVE",
    "GET-SETTING": "GETS",
    "SET-SETTING": "SETS",
}


def abbrev_command(cmd: str) -> str:
    c = (cmd or "SPEAK").strip().upper()
    if c in _CMD_ABBREV_4:
        return _CMD_ABBREV_4[c]
    base = c.replace("-", "")[:4]
    return (base + "    ")[:4]


def abbrev_detail(detail: str, *, max_keep: int = 5) -> str:
    d = (detail or "").strip()
    if len(d) <= max_keep:
        return d
    return d[:max_keep] + "…"


def usage_suffix_full(usage: dict | None) -> str:
    if not usage:
        return "tok=—"
    tt = usage.get("total_tokens")
    if tt is not None:
        return f"tt={tt}"
    pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if pt is not None and ct is not None:
        return f"pt={pt} ct={ct}"
    return "tok=—"


def usage_suffix_abbrev(usage: dict | None) -> str:
    if not usage:
        return "t:—"
    tt = usage.get("total_tokens")
    if tt is not None:
        return f"t:{tt}"
    pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if pt is not None and ct is not None:
        return f"t:{pt}+{ct}"
    return "t:—"


def format_retry_notice_line(
    retry_round: int,
    cmd: str,
    detail: str,
    usage: dict | None,
    *,
    abbreviated: bool,
) -> str:
    if abbreviated:
        return (
            f"[R{retry_round}] {abbrev_command(cmd)}:{abbrev_detail(detail)} "
            f"{usage_suffix_abbrev(usage)}"
        )
    return f"[RT#{retry_round}]{cmd}:{detail} {usage_suffix_full(usage)}"


def format_normal_footer_line(cmd: str, detail: str, usage: dict | None, *, abbreviated: bool) -> str:
    if abbreviated:
        return f"[N1] {abbrev_command(cmd)}:{abbrev_detail(detail)} {usage_suffix_abbrev(usage)}"
    return f"[N1]{cmd}:{detail} {usage_suffix_full(usage)}"


ALLOWED_TOOL_NOTICE_DISPLAY_HINT = "full | abbrev | minimal | hidden"
