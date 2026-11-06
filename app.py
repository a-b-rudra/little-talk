from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

MAX_MESSAGES = 50
MAX_HISTORY_MESSAGES = 20
MAX_MESSAGE_LENGTH = 1_000
MAX_TYPING_DURATION = 30.0
DEFAULT_REPLY_DELAY = 0.9
MAX_REPLY_DELAY = 30.0
MAX_VIDEO_DURATION = 180.0
SIGNIFICANT_TIME_GAP_MINUTES = 60
INTRO_DURATION = 0.65
SEND_DELAY = 0.16
BUBBLE_IN_DURATION = 0.28
CHAT_SCROLL_DURATION = 0.28
OUTRO_DURATION = 1.6


@dataclass(frozen=True)
class ChatMessage:
    sender: str
    text: str
    typing_duration: float
    outgoing: bool
    reply_delay: float = DEFAULT_REPLY_DELAY
    timestamp: str | None = None


@dataclass(frozen=True)
class HistoryMessage:
    message: ChatMessage
    timestamp: str


@dataclass(frozen=True)
class TimelineEntry:
    message: ChatMessage
    typing_start: float
    typing_end: float
    reveal_at: float
    timestamp: str


def _validate_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must use 24-hour HH:MM format.")
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError(f"{label} must use 24-hour HH:MM format.") from exc
    if parsed.strftime("%H:%M") != value:
        raise ValueError(f"{label} must use 24-hour HH:MM format.")
    return value


def _time_gap_minutes(previous: str, current: str) -> int:
    previous_time = datetime.strptime(previous, "%H:%M")
    current_time = datetime.strptime(current, "%H:%M")
    return int((current_time - previous_time).total_seconds() // 60) % (24 * 60)


def _has_significant_time_gap(previous: str, current: str) -> bool:
    return _time_gap_minutes(previous, current) >= SIGNIFICANT_TIME_GAP_MINUTES


def _status_time_at(timeline: list[TimelineEntry], elapsed: float, start_time: str) -> str:
    return next(
        (entry.timestamp for entry in reversed(timeline) if elapsed >= entry.reveal_at),
        start_time,
    )


def parse_script(source: str, script_format: str) -> dict[str, Any]:
    """Parse and validate a JSON or YAML conversation script."""
    if script_format == "json":
        try:
            data = json.loads(source)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    elif script_format == "yaml":
        try:
            import yaml
        except ModuleNotFoundError as exc:
            raise RuntimeError("YAML support is unavailable. Install the requirements from this app's folder.") from exc
        try:
            data = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML: {exc}") from exc
    else:
        raise ValueError("Script format must be 'json' or 'yaml'.")

    if not isinstance(data, dict):
        raise ValueError("The script must be an object with a 'messages' list.")
    raw_messages = data.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ValueError("Add at least one message to the 'messages' list.")
    if len(raw_messages) > MAX_MESSAGES:
        raise ValueError(f"A script can contain at most {MAX_MESSAGES} messages.")

    start_time = _validate_timestamp(data.get("start_time", "09:41"), "'start_time'")
    messages: list[ChatMessage] = []
    for index, item in enumerate(raw_messages, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Message {index} must be an object.")
        sender = item.get("sender")
        text = item.get("text", item.get("message"))
        if not isinstance(sender, str) or not sender.strip():
            raise ValueError(f"Message {index} needs a non-empty 'sender'.")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Message {index} needs non-empty 'text'.")
        if len(text) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"Message {index} exceeds the {MAX_MESSAGE_LENGTH}-character limit.")

        typing_duration = item.get("typing_duration", item.get("typing", 1.2))
        if isinstance(typing_duration, bool) or not isinstance(typing_duration, (int, float)):
            raise ValueError(f"Message {index} 'typing_duration' must be a number of seconds.")
        if not math.isfinite(typing_duration) or not 0 <= typing_duration <= MAX_TYPING_DURATION:
            raise ValueError(
                f"Message {index} 'typing_duration' must be between 0 and {MAX_TYPING_DURATION:g} seconds."
            )

        reply_delay = item.get("reply_delay", item.get("delay_before_typing", DEFAULT_REPLY_DELAY))
        if isinstance(reply_delay, bool) or not isinstance(reply_delay, (int, float)):
            raise ValueError(f"Message {index} 'reply_delay' must be a number of seconds.")
        if not math.isfinite(reply_delay) or not 0 <= reply_delay <= MAX_REPLY_DELAY:
            raise ValueError(
                f"Message {index} 'reply_delay' must be between 0 and {MAX_REPLY_DELAY:g} seconds."
            )

        clean_sender = sender.strip()
        timestamp = item.get("timestamp")
        if timestamp is not None:
            timestamp = _validate_timestamp(timestamp, f"Message {index} 'timestamp'")
        messages.append(
            ChatMessage(
                sender=clean_sender,
                text=text.strip(),
                typing_duration=float(typing_duration),
                outgoing=clean_sender.casefold() in {"you", "user", "me"},
                reply_delay=float(reply_delay),
                timestamp=timestamp,
            )
        )

    raw_history = data.get("history", [])
    if not isinstance(raw_history, list):
        raise ValueError("'history' must be a list when provided.")
    if len(raw_history) > MAX_HISTORY_MESSAGES:
        raise ValueError(f"History can contain at most {MAX_HISTORY_MESSAGES} messages.")
    history: list[HistoryMessage] = []
    start_clock = datetime.strptime(start_time, "%H:%M")
    for index, item in enumerate(raw_history, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"History message {index} must be an object.")
        sender = item.get("sender")
        text = item.get("text", item.get("message"))
        if not isinstance(sender, str) or not sender.strip():
            raise ValueError(f"History message {index} needs a non-empty 'sender'.")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"History message {index} needs non-empty 'text'.")
        if len(text) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"History message {index} exceeds the {MAX_MESSAGE_LENGTH}-character limit.")
        timestamp = item.get("timestamp")
        if timestamp is not None:
            timestamp = _validate_timestamp(timestamp, f"History message {index} 'timestamp'")
        else:
            timestamp = (start_clock - timedelta(minutes=len(raw_history) - index + 1)).strftime("%H:%M")
        clean_sender = sender.strip()
        history.append(
            HistoryMessage(
                ChatMessage(
                    sender=clean_sender,
                    text=text.strip(),
                    typing_duration=0,
                    outgoing=clean_sender.casefold() in {"you", "user", "me"},
                    reply_delay=0,
                    timestamp=timestamp,
                ),
                timestamp,
            )
        )

    contact = data.get("contact")
    if contact is None:
        contact = next((message.sender for message in messages if not message.outgoing), None)
    if contact is None:
        contact = next((item.message.sender for item in history if not item.message.outgoing), None)
    if contact is not None and (not isinstance(contact, str) or not contact.strip()):
        raise ValueError("'contact' must be a non-empty name when provided.")
    contact_has_name = contact is not None
    contact = contact.strip() if contact_has_name else "New Message"

    avatar = data.get("avatar")
    if avatar is not None:
        if not isinstance(avatar, str) or not avatar.strip():
            raise ValueError("'avatar' must be non-empty text when provided.")
        if len(avatar.strip()) > 8:
            raise ValueError("'avatar' must be 8 characters or fewer.")
        avatar = avatar.strip()
    avatar_display = avatar or (
        "".join(part[0] for part in contact.split()[:2]).upper() if contact_has_name else "👻"
    )

    entries, duration = build_timeline(messages, start_time)
    if duration > MAX_VIDEO_DURATION:
        raise ValueError(f"The conversation is too long. Keep the video under {MAX_VIDEO_DURATION:g} seconds.")

    return {
        "contact": contact,
        "contact_has_name": contact_has_name,
        "avatar": avatar,
        "avatar_display": avatar_display,
        "start_time": start_time,
        "history": history,
        "messages": messages,
        "timeline": entries,
        "duration": duration,
    }


def build_timeline(
    messages: list[ChatMessage],
    start_time: str = "09:41",
) -> tuple[list[TimelineEntry], float]:
    entries: list[TimelineEntry] = []
    cursor = INTRO_DURATION
    start_clock = datetime.strptime(_validate_timestamp(start_time, "'start_time'"), "%H:%M")
    for index, message in enumerate(messages):
        typing_start = cursor + message.reply_delay
        typing_end = typing_start + message.typing_duration
        reveal_at = typing_end + (SEND_DELAY if message.outgoing else 0)
        timestamp = message.timestamp or (start_clock + timedelta(minutes=index)).strftime("%H:%M")
        entries.append(TimelineEntry(message, typing_start, typing_end, reveal_at, timestamp))
        cursor = reveal_at
    return entries, cursor + OUTRO_DURATION


@lru_cache(maxsize=64)
def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = (
        (
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/SFNS.ttf",
        )
        if bold
        else (
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/SFNS.ttf",
        )
    )
    candidates += (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default(size=size)


@lru_cache(maxsize=32)
def _emoji_font(size: int) -> ImageFont.ImageFont | None:
    candidates = (
        "/System/Library/Fonts/Apple Color Emoji.ttc",
        "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
        "/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf",
        "C:/Windows/Fonts/seguiemj.ttf",
    )
    sizes = (20, 32, 40, 48, 64, 96, 109, 128, 160)
    for candidate in candidates:
        if not Path(candidate).is_file():
            continue
        for candidate_size in sorted(sizes, key=lambda value: abs(value - size)):
            try:
                return ImageFont.truetype(candidate, size=candidate_size, index=0)
            except OSError:
                continue
    return None


def _is_emoji_base(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x1F000 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x27BF
        or 0x2300 <= codepoint <= 0x23FF
        or 0x1F1E6 <= codepoint <= 0x1F1FF
    )


def _emoji_cluster_end(text: str, start: int) -> int:
    index = start + 1
    if 0x1F1E6 <= ord(text[start]) <= 0x1F1FF and index < len(text):
        if 0x1F1E6 <= ord(text[index]) <= 0x1F1FF:
            index += 1
    while index < len(text):
        codepoint = ord(text[index])
        if codepoint in (0xFE0E, 0xFE0F, 0x20E3) or 0x1F3FB <= codepoint <= 0x1F3FF:
            index += 1
        elif codepoint == 0x200D and index + 1 < len(text):
            index += 2
        else:
            break
    return index


def _text_runs(text: str) -> list[tuple[str, bool]]:
    runs: list[tuple[str, bool]] = []
    index = 0
    while index < len(text):
        is_emoji = _is_emoji_base(text[index])
        end = _emoji_cluster_end(text, index) if is_emoji else index + 1
        run = text[index:end]
        if runs and runs[-1][1] == is_emoji:
            runs[-1] = (runs[-1][0] + run, is_emoji)
        else:
            runs.append((run, is_emoji))
        index = end
    return runs


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> float:
    emoji_font = _emoji_font(font.size) if isinstance(font, ImageFont.FreeTypeFont) else None
    return sum(
        draw.textlength(run, font=emoji_font if is_emoji and emoji_font is not None else font)
        for run, is_emoji in _text_runs(text)
    )


def _draw_rich_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
) -> None:
    emoji_font = _emoji_font(font.size) if isinstance(font, ImageFont.FreeTypeFont) else None
    x, y = xy
    for run, is_emoji in _text_runs(text):
        run_font = emoji_font if is_emoji and emoji_font is not None else font
        draw.text(
            (x, y),
            run,
            font=run_font,
            fill=fill,
            embedded_color=is_emoji and emoji_font is not None,
        )
        x += draw.textlength(run, font=run_font)


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: float,
) -> list[str]:
    result: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            result.append("")
            continue
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            if _text_width(draw, candidate, font) <= max_width:
                line = candidate
                continue
            if line:
                result.append(line)
                line = ""
            while _text_width(draw, word, font) > max_width:
                part = ""
                for character in word:
                    if part and _text_width(draw, part + character, font) > max_width:
                        result.append(part)
                        part = character
                    else:
                        part += character
                word = part
            line = word
        result.append(line)
    return result


def _composer_layout(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: float,
    scale: float,
) -> tuple[list[str], int]:
    lines = _wrap_text(draw, text, font, max_width) if text else []
    lines = lines[-4:]
    field_height = max(
        round(67 * scale),
        len(lines) * round(31 * scale) + round(24 * scale),
    )
    return lines, field_height


def _measure_bubble(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: float,
    scale: float,
) -> tuple[list[str], int, int]:
    padding_x = round(26 * scale)
    padding_y = round(19 * scale)
    line_height = round(47 * scale)
    max_text_width = max(50, max_width - 2 * padding_x)
    lines = _wrap_text(draw, text, font, max_text_width)
    text_width = max((_text_width(draw, line, font) for line in lines), default=0)
    bubble_width = min(max_width, math.ceil(text_width + 2 * padding_x))
    bubble_height = len(lines) * line_height + 2 * padding_y
    return lines, max(round(92 * scale), bubble_width), bubble_height


def _typed_text(text: str, progress: float) -> str:
    character_count = round(len(text) * min(1.0, max(0.0, progress)))
    return text[:character_count]


def _draw_message(
    draw: ImageDraw.ImageDraw,
    message: ChatMessage,
    y: int,
    width: int,
    scale: float,
    font: ImageFont.ImageFont,
    *,
    cursor: bool = False,
) -> int:
    left_margin = round(38 * scale)
    max_width = min(round(690 * scale), round(width * 0.76))
    lines, bubble_width, bubble_height = _measure_bubble(draw, message.text, font, max_width, scale)
    x = width - left_margin - bubble_width if message.outgoing else left_margin
    radius = round(27 * scale)
    color = "#0A84FF" if message.outgoing else "#E9E9EB"
    text_color = "#FFFFFF" if message.outgoing else "#17171A"

    draw.rounded_rectangle(
        (x, y, x + bubble_width, y + bubble_height),
        radius=radius,
        fill=color,
    )
    text_x = x + round(26 * scale)
    text_y = y + round(17 * scale)
    line_height = round(47 * scale)
    for index, line in enumerate(lines):
        _draw_rich_text(draw, (text_x, text_y), line, font, text_color)
        if cursor and index == len(lines) - 1:
            cursor_width = max(2, round(3 * scale))
            cursor_height = round(32 * scale)
            cursor_x = text_x + math.ceil(_text_width(draw, line, font)) + round(2 * scale)
            cursor_y = text_y + (line_height - cursor_height) // 2
            draw.rounded_rectangle(
                (cursor_x, cursor_y, cursor_x + cursor_width, cursor_y + cursor_height),
                radius=cursor_width,
                fill=text_color,
            )
        text_y += line_height
    return bubble_height


def _draw_message_pop(
    layer: Image.Image,
    message: ChatMessage,
    y: int,
    width: int,
    scale: float,
    font: ImageFont.ImageFont,
    progress: float,
) -> None:
    probe = ImageDraw.Draw(layer)
    max_width = min(round(690 * scale), round(width * 0.76))
    _, bubble_width, bubble_height = _measure_bubble(probe, message.text, font, max_width, scale)
    bubble_x = width - round(38 * scale) - bubble_width if message.outgoing else round(38 * scale)
    bubble = Image.new("RGBA", (width, bubble_height + 1), (0, 0, 0, 0))
    _draw_message(ImageDraw.Draw(bubble), message, 0, width, scale, font)
    crop_left = max(0, bubble_x - round(7 * scale))
    crop_right = min(width, bubble_x + bubble_width + round(7 * scale))
    bubble = bubble.crop((crop_left, 0, crop_right, bubble_height + 1))

    eased = 1 - (1 - progress) ** 3
    pop_scale = 0.88 + 0.12 * eased
    new_size = (
        max(1, round(bubble.width * pop_scale)),
        max(1, round(bubble.height * pop_scale)),
    )
    bubble = bubble.resize(new_size, Image.Resampling.LANCZOS)
    opacity = round(255 * min(1.0, progress * 5))
    bubble.putalpha(bubble.getchannel("A").point(lambda alpha: alpha * opacity // 255))
    x = crop_right - bubble.width if message.outgoing else crop_left
    y = y + bubble_height - bubble.height
    layer.paste(bubble, (x, y), bubble)


def _draw_typing(
    draw: ImageDraw.ImageDraw,
    y: int,
    width: int,
    scale: float,
    phase: float,
) -> int:
    left = round(38 * scale)
    bubble_width = round(132 * scale)
    bubble_height = round(70 * scale)
    x = left
    draw.rounded_rectangle(
        (x, y, x + bubble_width, y + bubble_height),
        radius=round(26 * scale),
        fill="#E9E9EB",
    )
    for index in range(3):
        pulse = 0.58 + 0.42 * (0.5 + 0.5 * math.sin(phase * 7 - index * 1.6))
        shade = round(125 + 50 * pulse)
        dot = round(10 * scale)
        center = (x + round((36 + index * 29) * scale), y + bubble_height // 2)
        draw.ellipse(
            (center[0] - dot, center[1] - dot, center[0] + dot, center[1] + dot),
            fill=(shade, shade, shade + 5),
        )
    return bubble_height


def _draw_chrome(
    image: Image.Image,
    contact: str,
    scale: float,
    start_time: str,
    avatar: str | None = None,
) -> None:
    draw = ImageDraw.Draw(image)
    width, height = image.size
    ink = "#111114"
    muted = "#8E8E93"
    blue = "#007AFF"
    draw.rectangle((0, 0, width, height), fill="#FFFFFF")

    icon_bottom = round(64 * scale)
    status_font = _font(max(12, round(31 * scale)), bold=True)
    draw.text(
        (round(56 * scale), icon_bottom),
        start_time,
        font=status_font,
        fill=ink,
        anchor="ls",
    )

    bar_width = max(2, round(7 * scale))
    bar_step = round(10 * scale)
    signal_width = bar_step * 3 + bar_width
    wifi_width = round(40 * scale)
    icon_gap = round(20 * scale)
    battery_x = width - round(133 * scale)
    battery_width = round(38 * scale)
    battery_cap_width = round(3 * scale)
    wifi_left = battery_x - icon_gap - wifi_width
    wifi_center_x = wifi_left + wifi_width // 2
    signal_x = wifi_left - icon_gap - signal_width
    for index, bar_height in enumerate((11, 17, 24, 31)):
        x = signal_x + index * bar_step
        draw.rounded_rectangle(
            (x, icon_bottom - round(bar_height * scale), x + bar_width, icon_bottom),
            radius=round(3 * scale),
            fill=ink,
        )

    for radius in (round(20 * scale), round(13 * scale), round(6 * scale)):
        points = [
            (
                round(wifi_center_x + math.cos(math.radians(angle)) * radius),
                round(icon_bottom - math.sin(math.radians(angle)) * radius),
            )
            for angle in range(25, 156, 5)
        ]
        draw.line(points, fill=ink, width=max(2, round(3 * scale)), joint="curve")
    wifi_dot = max(2, round(3 * scale))
    draw.ellipse(
        (
            wifi_center_x - wifi_dot,
            icon_bottom - wifi_dot,
            wifi_center_x + wifi_dot,
            icon_bottom,
        ),
        fill=ink,
    )

    battery_height = round(19 * scale)
    battery_y = icon_bottom - battery_height
    draw.rounded_rectangle(
        (battery_x, battery_y, battery_x + battery_width, icon_bottom),
        radius=round(5 * scale),
        outline=ink,
        width=max(2, round(2 * scale)),
    )
    draw.rounded_rectangle(
        (battery_x + round(3 * scale), battery_y + round(3 * scale), battery_x + round(29 * scale), icon_bottom - round(3 * scale)),
        radius=round(3 * scale),
        fill=ink,
    )
    draw.rectangle(
        (battery_x + battery_width, battery_y + round(6 * scale), battery_x + battery_width + battery_cap_width, battery_y + round(13 * scale)),
        fill=ink,
    )

    nav_y = round(height * 0.061)
    center_y = round(height * 0.103)
    draw.line(
        (round(60 * scale), center_y, round(78 * scale), center_y - round(19 * scale)),
        fill=blue,
        width=round(6 * scale),
    )
    draw.line(
        (round(60 * scale), center_y, round(78 * scale), center_y + round(19 * scale)),
        fill=blue,
        width=round(6 * scale),
    )
    count_font = _font(max(12, round(28 * scale)))
    draw.text((round(81 * scale), center_y - round(20 * scale)), "12", font=count_font, fill=blue)

    avatar_size = round(74 * scale)
    avatar_x = (width - avatar_size) // 2
    avatar_y = nav_y + round(5 * scale)
    draw.ellipse(
        (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
        fill="#D9E4F2",
    )
    avatar_text = avatar or "".join(part[0] for part in contact.split()[:2]).upper() or "?"
    avatar_font_size = max(15, round(24 * scale))
    avatar_font = _font(avatar_font_size, bold=True)
    avatar_width = _text_width(draw, avatar_text, avatar_font)
    _draw_rich_text(
        draw,
        (
            avatar_x + (avatar_size - avatar_width) / 2,
            avatar_y + (avatar_size - avatar_font_size) / 2,
        ),
        avatar_text,
        avatar_font,
        "#50627B",
    )
    name_font = _font(max(14, round(27 * scale)), bold=True)
    name = contact if len(contact) <= 23 else contact[:21] + "…"
    name_width = _text_width(draw, name, name_font)
    draw.text(((width - name_width) / 2, avatar_y + avatar_size + round(3 * scale)), name, font=name_font, fill=ink)
    arrow_y = avatar_y + avatar_size + round(18 * scale)
    draw.line(
        ((width // 2 + round(77 * scale), arrow_y), (width // 2 + round(85 * scale), arrow_y + round(8 * scale))),
        fill=muted,
        width=max(2, round(2 * scale)),
    )
    draw.line(
        ((width // 2 + round(85 * scale), arrow_y + round(8 * scale)), (width // 2 + round(93 * scale), arrow_y)),
        fill=muted,
        width=max(2, round(2 * scale)),
    )

    video_x = width - round(91 * scale)
    video_y = center_y - round(16 * scale)
    draw.rounded_rectangle(
        (video_x, video_y, video_x + round(38 * scale), video_y + round(28 * scale)),
        radius=round(6 * scale),
        outline=blue,
        width=max(2, round(3 * scale)),
    )
    draw.polygon(
        (
            (video_x + round(39 * scale), video_y + round(7 * scale)),
            (video_x + round(51 * scale), video_y + round(1 * scale)),
            (video_x + round(51 * scale), video_y + round(27 * scale)),
            (video_x + round(39 * scale), video_y + round(21 * scale)),
        ),
        fill=blue,
    )

    divider_y = round(height * 0.18)
    draw.line((0, divider_y, width, divider_y), fill="#E6E6E8", width=max(1, round(1 * scale)))


def _scroll_offset(
    initial_content_height: int,
    scroll_events: list[tuple[float, int]],
    elapsed: float,
    viewport_height: int,
) -> int:
    content_height = initial_content_height
    target_offset = max(0, content_height - viewport_height)
    animation_start_offset = target_offset
    animation_start_time = 0.0

    def offset_at(time: float) -> float:
        progress = min(1.0, max(0.0, (time - animation_start_time) / CHAT_SCROLL_DURATION))
        eased = 1 - (1 - progress) ** 3
        return animation_start_offset + (target_offset - animation_start_offset) * eased

    for event_time, content_delta in scroll_events:
        if event_time > elapsed:
            break
        current_offset = offset_at(event_time)
        content_height += content_delta
        next_target = max(0, content_height - viewport_height)
        if next_target != target_offset:
            animation_start_offset = current_offset
            animation_start_time = event_time
            target_offset = next_target

    return round(offset_at(elapsed))


def _draw_composer(image: Image.Image, scale: float, typing_text: str = "") -> None:
    draw = ImageDraw.Draw(image)
    width, height = image.size
    muted = "#8E8E93"
    accent = "#0A84FF"
    base_top = height - round(228 * scale)
    field_left = round(102 * scale)
    field_right = width - round(77 * scale)
    placeholder_font = _font(max(12, round(29 * scale)))
    lines, field_height = _composer_layout(
        draw,
        typing_text,
        placeholder_font,
        field_right - field_left - round(46 * scale),
        scale,
    )
    field_bottom = base_top + round(98 * scale)
    field_top = field_bottom - field_height
    top = base_top - (field_height - round(67 * scale))
    line_height = round(31 * scale)
    draw.line((0, top, width, top), fill="#E6E6E8", width=max(1, round(1 * scale)))

    field_center_y = (field_top + field_bottom) // 2
    plus_center = (round(59 * scale), field_center_y)
    plus_radius = round(24 * scale)
    draw.ellipse(
        (
            plus_center[0] - plus_radius,
            plus_center[1] - plus_radius,
            plus_center[0] + plus_radius,
            plus_center[1] + plus_radius,
        ),
        fill="#F2F2F7",
    )
    draw.line(
        (
            plus_center[0] - round(11 * scale),
            plus_center[1],
            plus_center[0] + round(11 * scale),
            plus_center[1],
        ),
        fill=muted,
        width=max(2, round(3 * scale)),
    )
    draw.line(
        (
            plus_center[0],
            plus_center[1] - round(11 * scale),
            plus_center[0],
            plus_center[1] + round(11 * scale),
        ),
        fill=muted,
        width=max(2, round(3 * scale)),
    )

    draw.rounded_rectangle(
        (field_left, field_top, field_right, field_bottom),
        radius=round(34 * scale),
        outline="#D2D2D7",
        width=max(1, round(2 * scale)),
        fill="#FFF",
    )
    if typing_text:
        text_y = field_top + max(round(10 * scale), (field_bottom - field_top - len(lines) * line_height) // 2)
        text_x = field_left + round(23 * scale)
        for index, line in enumerate(lines):
            _draw_rich_text(draw, (text_x, text_y), line, placeholder_font, "#25252A")
            text_y += line_height
        cursor_width = max(2, round(2 * scale))
        cursor_height = round(27 * scale)
        cursor_x = text_x + math.ceil(_text_width(draw, lines[-1], placeholder_font)) + round(2 * scale)
        cursor_y = text_y - line_height + (line_height - cursor_height) // 2
        draw.rounded_rectangle(
            (cursor_x, cursor_y, cursor_x + cursor_width, cursor_y + cursor_height),
            radius=cursor_width,
            fill="#25252A",
        )
    else:
        draw.text(
            (field_left + round(23 * scale), field_top + round(17 * scale)),
            "iMessage",
            font=placeholder_font,
            fill="#A0A0A5",
        )
    action_x = width - round(48 * scale)
    action_y = field_center_y
    has_typed_text = bool(typing_text.strip())
    if has_typed_text:
        action_radius = round(22 * scale)
        draw.ellipse(
            (
                action_x - action_radius,
                action_y - action_radius,
                action_x + action_radius,
                action_y + action_radius,
            ),
            fill=accent,
        )
        arrow_color = "#FFFFFF"
        arrow_width = max(2, round(3 * scale))
        draw.line(
            (
                action_x,
                action_y + round(10 * scale),
                action_x,
                action_y - round(10 * scale),
            ),
            fill=arrow_color,
            width=arrow_width,
        )
        draw.line(
            (
                action_x - round(8 * scale),
                action_y - round(3 * scale),
                action_x,
                action_y - round(11 * scale),
                action_x + round(8 * scale),
                action_y - round(3 * scale),
            ),
            fill=arrow_color,
            width=arrow_width,
            joint="curve",
        )
    else:
        mic_top = action_y - round(13 * scale)
        icon_width = max(2, round(3 * scale))
        draw.rounded_rectangle(
            (
                action_x - round(5 * scale),
                mic_top,
                action_x + round(5 * scale),
                mic_top + round(17 * scale),
            ),
            radius=round(5 * scale),
            outline=muted,
            width=icon_width,
        )
        draw.arc(
            (
                action_x - round(11 * scale),
                mic_top + round(7 * scale),
                action_x + round(11 * scale),
                mic_top + round(27 * scale),
            ),
            0,
            180,
            fill=muted,
            width=icon_width,
        )
        draw.line(
            (action_x, mic_top + round(26 * scale), action_x, mic_top + round(30 * scale)),
            fill=muted,
            width=icon_width,
        )
        draw.line(
            (
                action_x - round(6 * scale),
                mic_top + round(30 * scale),
                action_x + round(6 * scale),
                mic_top + round(30 * scale),
            ),
            fill=muted,
            width=icon_width,
        )

    home_width = round(270 * scale)
    home_height = max(4, round(10 * scale))
    draw.rounded_rectangle(
        (
            (width - home_width) // 2,
            height - round(26 * scale),
            (width + home_width) // 2,
            height - round(26 * scale) + home_height,
        ),
        radius=home_height // 2,
        fill="#111114",
    )


def _render_frame(
    contact: str,
    timeline: list[TimelineEntry],
    elapsed: float,
    width: int,
    height: int,
    history: list[HistoryMessage] | None = None,
    start_time: str = "09:41",
    avatar: str | None = None,
) -> Image.Image:
    scale = width / 1080
    history = history or []
    image = Image.new("RGB", (width, height), "#FFFFFF")
    _draw_chrome(image, contact, scale, _status_time_at(timeline, elapsed, start_time), avatar)

    top = round(height * 0.205)
    body_font = _font(max(15, round(37 * scale)))
    active = next(
        (
            entry
            for entry in timeline
            if entry.message.typing_duration > 0 and entry.typing_start <= elapsed < entry.typing_end
        ),
        None,
    )
    outgoing_typing_text = ""
    if active is not None and active.message.outgoing:
        progress = (elapsed - active.typing_start) / active.message.typing_duration
        outgoing_typing_text = _typed_text(active.message.text, progress)
    else:
        sending = next(
            (
                entry
                for entry in timeline
                if entry.message.outgoing and entry.typing_end <= elapsed < entry.reveal_at
            ),
            None,
        )
        if sending is not None:
            outgoing_typing_text = sending.message.text
    composer_font = _font(max(12, round(29 * scale)))
    _, composer_field_height = _composer_layout(
        ImageDraw.Draw(image),
        outgoing_typing_text,
        composer_font,
        width - round(225 * scale),
        scale,
    )
    composer_growth = composer_field_height - round(67 * scale)
    bottom = height - round(228 * scale) - composer_growth
    layer = Image.new("RGBA", (width, bottom - top), (255, 255, 255, 0))
    draw = ImageDraw.Draw(layer)
    gap = round(18 * scale)
    max_width = min(round(690 * scale), round(width * 0.76))
    visible = [entry for entry in timeline if elapsed >= entry.reveal_at]
    timestamp_height = round(24 * scale)
    separator_height = round(48 * scale)
    separator_font = _font(max(11, round(20 * scale)), bold=True)
    start_label = f"Today · {start_time}"
    measured: list[tuple[ChatMessage, str, int, TimelineEntry | None]] = []
    entry_heights: dict[TimelineEntry, int] = {}
    entry_has_time_gap: dict[TimelineEntry, bool] = {}
    timestamp_font = _font(max(10, round(18 * scale)))
    history_has_time_gap: list[bool] = []
    previous_timestamp: str | None = None
    history_content_height = 0
    for history_item in history:
        message = history_item.message
        _, _, bubble_height = _measure_bubble(draw, message.text, body_font, max_width, scale)
        has_time_gap = (
            previous_timestamp is not None
            and _has_significant_time_gap(previous_timestamp, history_item.timestamp)
        )
        history_has_time_gap.append(has_time_gap)
        if has_time_gap:
            history_content_height += separator_height + gap
        history_content_height += bubble_height + timestamp_height + gap
        measured.append((message, history_item.timestamp, bubble_height, None))
        previous_timestamp = history_item.timestamp
    previous_timestamp = start_time
    for entry in timeline:
        _, _, bubble_height = _measure_bubble(draw, entry.message.text, body_font, max_width, scale)
        entry_heights[entry] = bubble_height
        entry_has_time_gap[entry] = _has_significant_time_gap(previous_timestamp, entry.timestamp)
        previous_timestamp = entry.timestamp
    for entry in visible:
        measured.append((entry.message, entry.timestamp, entry_heights[entry], entry))
    initial_content_height = history_content_height + separator_height + gap
    typing_height = round(70 * scale) + gap
    scroll_events: list[tuple[float, int]] = []
    for entry in timeline:
        message_height = entry_heights[entry] + timestamp_height + gap
        separator_space = separator_height + gap if entry_has_time_gap[entry] else 0
        is_incoming_typing = not entry.message.outgoing and entry.message.typing_duration > 0
        if is_incoming_typing:
            scroll_events.append((entry.typing_start, separator_space + typing_height))
            message_height -= typing_height
            separator_space = 0
        scroll_events.append((entry.reveal_at, separator_space + message_height))
    scroll = _scroll_offset(initial_content_height, scroll_events, elapsed, bottom - top)
    y = -scroll
    history_index = 0
    for history_item in history:
        message = history_item.message
        bubble_height = measured[history_index][2]
        if history_has_time_gap[history_index]:
            label = f"Today · {history_item.timestamp}"
            label_width = _text_width(draw, label, separator_font)
            if y + separator_height >= 0 and y < bottom - top:
                draw.text(
                    ((width - label_width) / 2, y + round(12 * scale)),
                    label,
                    font=separator_font,
                    fill="#8E8E93",
                )
            y += separator_height + gap
        if y + bubble_height >= 0 and y < bottom - top:
            _draw_message(draw, message, y, width, scale, body_font)
            timestamp_width = _text_width(draw, history_item.timestamp, timestamp_font)
            timestamp_x = (
                width - round(38 * scale) - timestamp_width
                if message.outgoing
                else round(38 * scale)
            )
            draw.text(
                (timestamp_x, y + bubble_height + round(5 * scale)),
                history_item.timestamp,
                font=timestamp_font,
                fill="#8E8E93",
            )
        y += bubble_height + timestamp_height + gap
        history_index += 1

    start_width = _text_width(draw, start_label, separator_font)
    if y + separator_height >= 0 and y < bottom - top:
        draw.text(
            ((width - start_width) / 2, y + round(12 * scale)),
            start_label,
            font=separator_font,
            fill="#8E8E93",
        )
    y += separator_height + gap

    for entry in visible:
        message = entry.message
        timestamp = entry.timestamp
        bubble_height = entry_heights[entry]
        if entry_has_time_gap[entry]:
            label = f"Today · {timestamp}"
            label_width = _text_width(draw, label, separator_font)
            if y + separator_height >= 0 and y < bottom - top:
                draw.text(
                    ((width - label_width) / 2, y + round(12 * scale)),
                    label,
                    font=separator_font,
                    fill="#8E8E93",
                )
            y += separator_height + gap
        if y + bubble_height >= 0 and y < bottom - top:
            animation_progress = (
                (elapsed - entry.reveal_at) / BUBBLE_IN_DURATION
                if elapsed >= entry.reveal_at
                else 0.0
            )
            if animation_progress < 1:
                _draw_message_pop(layer, message, y, width, scale, body_font, animation_progress)
            else:
                _draw_message(draw, message, y, width, scale, body_font)
            timestamp_width = _text_width(draw, timestamp, timestamp_font)
            timestamp_x = (
                width - round(38 * scale) - timestamp_width
                if message.outgoing
                else round(38 * scale)
            )
            draw.text(
                (timestamp_x, y + bubble_height + round(5 * scale)),
                timestamp,
                font=timestamp_font,
                fill="#8E8E93",
            )
        y += bubble_height + timestamp_height + gap

    if active is not None and not active.message.outgoing:
        if not active.message.outgoing and entry_has_time_gap[active]:
            label = f"Today · {active.timestamp}"
            label_width = _text_width(draw, label, separator_font)
            if y + separator_height >= 0 and y < bottom - top:
                draw.text(
                    ((width - label_width) / 2, y + round(12 * scale)),
                    label,
                    font=separator_font,
                    fill="#8E8E93",
                )
            y += separator_height + gap
        typing_height = _draw_typing(draw, y, width, scale, elapsed - active.typing_start)
        y += typing_height + gap
    image.paste(layer, (0, top), layer)
    _draw_composer(image, scale, outgoing_typing_text)
    return image


def _render_phone_scene(
    contact: str,
    timeline: list[TimelineEntry],
    elapsed: float,
    width: int,
    height: int,
    history: list[HistoryMessage] | None = None,
    start_time: str = "09:41",
    avatar: str | None = None,
    preview_card: bool = False,
) -> Image.Image:
    if preview_card:
        scene = Image.new("RGB", (width, height), "#E9EDF2")
        card_inset = round(width * 0.018)
        card_radius = round(width * 0.035)
        card_bounds = (
            card_inset,
            card_inset,
            width - card_inset - 1,
            height - card_inset - 1,
        )
        card_mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(card_mask).rounded_rectangle(card_bounds, radius=card_radius, fill=255)
        backdrop = Image.new("RGBA", (width, height), (247, 249, 248, 255))
        glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.ellipse(
            (width * 0.05, height * 0.24, width * 0.95, height * 0.88),
            fill=(214, 230, 219, 150),
        )
        glow_draw.ellipse(
            (width * 0.4, height * -0.15, width * 1.15, height * 0.45),
            fill=(225, 233, 240, 130),
        )
        glow = glow.filter(ImageFilter.GaussianBlur(round(width * 0.12)))
        backdrop = Image.alpha_composite(backdrop, glow)
        scene.paste(backdrop.convert("RGB"), (0, 0), card_mask)
        ImageDraw.Draw(scene).rounded_rectangle(
            card_bounds,
            radius=card_radius,
            outline="#EDF0EF",
            width=max(1, round(width * 0.002)),
        )
        card_height = height - card_inset * 2
        phone_height = round(card_height * 0.9)
        phone_y = card_inset + round((card_height - phone_height) * 0.08)
    else:
        scene = Image.new("RGB", (width, height), "#E9EDF2")
        phone_height = round(height * 0.94)
        phone_y = (height - phone_height) // 2
    phone_width = round(phone_height * 390 / 844)
    if phone_width > width * 0.94:
        phone_width = round(width * 0.94)
        phone_height = round(phone_width * 844 / 390)
    phone_x = (width - phone_width) // 2
    if not preview_card:
        phone_y = (height - phone_height) // 2
    bezel = max(3, round(phone_width * 0.0175))
    screen_left = phone_x + bezel
    screen_top = phone_y + bezel
    screen_width = phone_width - 2 * bezel
    screen_height = phone_height - 2 * bezel
    corner_radius = round(phone_width * 0.12)

    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(
        (phone_x + 2, phone_y + 12, phone_x + phone_width + 2, phone_y + phone_height + 12),
        radius=corner_radius,
        fill=(26, 30, 38, 105),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(4, round(phone_width * 0.025))))
    scene = Image.alpha_composite(scene.convert("RGBA"), shadow)
    scene_draw = ImageDraw.Draw(scene)
    scene_draw.rounded_rectangle(
        (phone_x, phone_y, phone_x + phone_width, phone_y + phone_height),
        radius=corner_radius,
        fill="#25272C",
        outline="#45484F",
        width=max(1, round(phone_width * 0.003)),
    )

    screen = _render_frame(
        contact,
        timeline,
        elapsed,
        screen_width,
        screen_height,
        history,
        start_time,
        avatar,
    )
    screen_mask = Image.new("L", (screen_width, screen_height), 0)
    ImageDraw.Draw(screen_mask).rounded_rectangle(
        (0, 0, screen_width - 1, screen_height - 1),
        radius=max(1, corner_radius - bezel),
        fill=255,
    )
    scene.paste(screen, (screen_left, screen_top), screen_mask)
    return scene.convert("RGB")


def _video_dimensions(aspect_ratio: str) -> tuple[int, int]:
    if aspect_ratio == "preview":
        return 1080, 1620
    if aspect_ratio == "iphone":
        return 1170, 2532
    if aspect_ratio == "portrait":
        return 1080, 1920
    if aspect_ratio == "square":
        return 1080, 1080
    raise ValueError("Aspect ratio must be 'preview', 'iphone', 'portrait', or 'square'.")


def render_video(
    script: dict[str, Any],
    output_path: str | Path,
    *,
    aspect_ratio: str = "iphone",
    fps: int = 24,
) -> None:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Node.js is required to render MP4 with Remotion. Install Node.js 20 or newer.")
    width, height = _video_dimensions(aspect_ratio)
    if isinstance(fps, bool) or not isinstance(fps, int) or not 12 <= fps <= 30:
        raise ValueError("Frame rate must be an integer between 12 and 30 FPS.")

    output = Path(output_path)
    app_directory = Path(__file__).parent
    render_script = app_directory / "src" / "render.mjs"
    if not render_script.is_file():
        raise RuntimeError("The Remotion render script is missing from this app's src directory.")
    input_props = {
        "contact": script["contact"],
        "avatar_display": script["avatar_display"],
        "start_time": script["start_time"],
        "duration": script["duration"],
        "fps": fps,
        "aspect_ratio": aspect_ratio,
        "history": [
            {
                "sender": item.message.sender,
                "text": item.message.text,
                "timestamp": item.timestamp,
                "outgoing": item.message.outgoing,
            }
            for item in script["history"]
        ],
        "messages": [
            {
                "sender": entry.message.sender,
                "text": entry.message.text,
                "timestamp": entry.timestamp,
                "outgoing": entry.message.outgoing,
                "typing_duration": entry.message.typing_duration,
                "reply_delay": entry.message.reply_delay,
                "typing_start": entry.typing_start,
                "typing_end": entry.typing_end,
                "reveal_at": entry.reveal_at,
            }
            for entry in script["timeline"]
        ],
    }
    try:
        with tempfile.TemporaryDirectory(prefix="little-talk-remotion-") as directory:
            props_path = Path(directory) / "props.json"
            props_path.write_text(json.dumps(input_props, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                [node, str(render_script), str(props_path), str(output.resolve())],
                cwd=app_directory,
                capture_output=True,
                text=True,
                check=False,
            )
    except OSError as exc:
        raise RuntimeError(f"Could not start Remotion: {exc}") from exc
    if result.returncode != 0:
        error_output = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Remotion could not encode the video: {error_output or f'exit code {result.returncode}'}")
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("Remotion finished without producing a video file.")
