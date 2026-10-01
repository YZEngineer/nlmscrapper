import re
from scraper.post_comments_dom import collect_post_comments_dom_data


_ACTIONS = {
    "like", "reply", "share", "comment", "see more", "view more", "more",
    "all comments", "most relevant", "newest", "older comments", "view all comments",
    "رد", "مشاركة", "أعجبني", "اعجبني", "تعليق", "كل التعليقات",
    "إعجاب", "اعجاب", "المزيد", "عرض المزيد", "إظهار المزيد",
    "متابعة", "إرسال رسالة", "مراسلة",
}
_TIME = re.compile(
    r"^(?:"
    r"(?:\d+\s*(?:s|sec|seconds?|m|mins?|minutes?|h|hrs?|hours?|d|days?|w|weeks?|mo|months?|y|years?)(?:\s+ago)?)"
    r"|(?:just now|yesterday|today)"
    r"|(?:منذ\s*.+|قبل\s*.+)"
    r"|(?:(?:\d+|[٠-٩]+)\s*(?:ث|د|س|ي|أ|أيام?|أشهر?|سنوات?))"
    r")$",
    re.IGNORECASE,
)
_DATED_TIME = re.compile(
    r"^(?:"
    r"\d{1,2}\s+[\u0600-\u06ff]+\s+(?:الساعة\s+)?\d{1,2}:\d{2}(?:\s*[مAMPM]+)?"
    r"|[\u0600-\u06ff]+\s+\d{1,2}(?:،|,)?\s*(?:الساعة\s+)?\d{1,2}:\d{2}(?:\s*[مAMPM]+)?"
    r"|[A-Za-z]{3,9}\s+\d{1,2}(?:,)?\s+\d{1,2}:\d{2}\s*(?:AM|PM)"
    r")$",
    re.IGNORECASE,
)
_COUNTER = re.compile(
    r"^(?:\d+|[٠-٩]+)(?:\s*(?:likes?|replies|comments?|إعجاب|ردود|تعليقات))?$",
    re.IGNORECASE,
)
_SEPARATORS = {"·", "•", "|"}
_AUTHOR_LABEL = " اسم المستخدم -"
_FILTERED_LABEL = re.compile(
    r"^[·•\s]*(?:"
    r"عرض الترجمة|عرض الأصل|تقييم هذه الترجمة"
    r")[·•\s]*$"
)
_INVISIBLE = re.compile(r"[\u200e\u200f\u202a-\u202e\ufeff]")


def _is_filtered_label(line):
    return bool(_FILTERED_LABEL.fullmatch(line))


def _normalize(value):
    return " ".join(_INVISIBLE.sub("", str(value or "")).split()).casefold()


def _is_timestamp(line):
    return bool(_TIME.fullmatch(line) or _DATED_TIME.fullmatch(line))


def _clean_lines(block):
    lines = []
    for raw in str(block or "").splitlines():
        line = _INVISIBLE.sub("", raw)
        line = re.sub(r"\s+", " ", line).strip()
        if not line or line in _SEPARATORS:
            continue
        if (
            line.casefold() in _ACTIONS
            or _is_timestamp(line)
            or _COUNTER.fullmatch(line)
            or _is_filtered_label(line)
        ):
            continue
        if line.startswith(("عرض المزيد", "إظهار المزيد", "See more", "View more")):
            continue
        lines.append(line)
    return lines


def _author_boundary(lines):
    for index, line in enumerate(lines[:-1]):
        next_line = lines[index + 1]
        if _is_timestamp(next_line):
            return index, index + 1
        if next_line in _SEPARATORS and index + 2 < len(lines) and _is_timestamp(lines[index + 2]):
            return index, index + 2
    return (0, -1) if lines else (-1, -1)


def _filter_block(block):
    raw_lines = [
        _INVISIBLE.sub("", line).strip()
        for line in str(block or "").splitlines()
    ]
    raw_lines = [line for line in raw_lines if line]
    lines = _clean_lines(block)
    if not lines:
        return None

    # Facebook article text places the author immediately before its timestamp.
    # Use that boundary when available; otherwise retain the first cleaned line.
    author_index, time_index = _author_boundary(raw_lines)
    if author_index < 0:
        author_index = 0
        body_start = 1
    else:
        body_start = time_index + 1
        while body_start < len(raw_lines) and raw_lines[body_start] in _SEPARATORS:
            body_start += 1
        while body_start < len(raw_lines) and (
            _is_timestamp(raw_lines[body_start]) or _COUNTER.fullmatch(raw_lines[body_start])
        ):
            body_start += 1

    author = raw_lines[author_index]
    if author in _SEPARATORS or author.casefold() in _ACTIONS or _is_timestamp(author):
        author = ""

    body_end = len(raw_lines)
    for index in range(body_start + 1, len(raw_lines) - 1):
        next_line = raw_lines[index + 1]
        if _is_timestamp(next_line) or (
            next_line in _SEPARATORS
            and index + 2 < len(raw_lines)
            and _is_timestamp(raw_lines[index + 2])
        ):
            body_end = index
            break

    body_lines = []
    for raw in raw_lines[body_start:body_end]:
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or line in _SEPARATORS:
            continue
        if (
            line.casefold() in _ACTIONS
            or _is_timestamp(line)
            or _COUNTER.fullmatch(line)
            or _is_filtered_label(line)
        ):
            continue
        if line.startswith(("عرض المزيد", "إظهار المزيد", "See more", "View more")):
            continue
        body_lines.append(line)

    if not author and lines:
        author = lines[0]
        if body_lines and body_lines[0] == author:
            body_lines.pop(0)
    text = "\n".join(dict.fromkeys(body_lines)).strip()
    if not author and not text:
        return None
    return {"author": author, "text": text}


def _clean_comment_entry(comment):
    author = str(comment.get("author") or "").strip()
    base_author = author
    if base_author.endswith(_AUTHOR_LABEL):
        base_author = base_author[:-len(_AUTHOR_LABEL)].rstrip()

    lines = [line.strip() for line in str(comment.get("text") or "").splitlines()]
    lines = [line for line in lines if line]
    while lines and base_author and _normalize(lines[0]) == _normalize(base_author):
        lines.pop(0)

    comment["text"] = "\n".join(
        line
        for line in lines
        if not _is_timestamp(line)
        and not _COUNTER.fullmatch(line)
        and not _is_filtered_label(line)
    ).strip()
    if author and not author.endswith(_AUTHOR_LABEL):
        comment["author"] = author + _AUTHOR_LABEL

    for reply in comment.get("replies", []):
        if isinstance(reply, dict):
            _clean_comment_entry(reply)
    return comment


def filter_comment_data(raw_data):
    blocks = raw_data.get("blocks", [])
    entries = []
    seen = set()
    for block in blocks:
        entry = _filter_block(block)
        if not entry:
            continue
        signature = (entry["author"], entry["text"])
        if signature in seen:
            continue
        seen.add(signature)
        entries.append(entry)

    post_entry = entries[0] if entries else {"author": "", "text": ""}
    comments = []
    for item in entries[1:]:
        comment = _clean_comment_entry(
            {"author": item["author"], "text": item["text"], "replies": []}
        )
        if comment["text"]:
            comments.append(comment)
    return {
        "schema_version": 1,
        "post_url": raw_data.get("post_url", ""),
        "post": post_entry,
        "comments": comments,
        "scrolls": raw_data.get("scrolls", 0),
        "reply_clicks": raw_data.get("reply_clicks", 0),
    }


def collect_filtered_comments_data(page, post_url, max_scrolls=1000, idle_seconds=5):
    # Keep page navigation, comment sorting, reply expansion, scrolling, and
    # Keep extraction aligned with the shared post-comment DOM collector.
    raw_data = collect_post_comments_dom_data(
        page,
        post_url,
        max_scrolls=max_scrolls,
        idle_seconds=idle_seconds,
    )
    return filter_comment_data(raw_data)
