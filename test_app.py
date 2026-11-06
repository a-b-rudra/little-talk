from io import BytesIO
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app import (
    DEFAULT_REPLY_DELAY,
    MAX_VIDEO_DURATION,
    SEND_DELAY,
    ChatMessage,
    _emoji_font,
    _draw_chrome,
    _draw_composer,
    _has_significant_time_gap,
    _render_frame,
    _render_phone_scene,
    _scroll_offset,
    _status_time_at,
    _typed_text,
    _video_dimensions,
    build_timeline,
    parse_script,
    render_video,
)


class ParseScriptTests(unittest.TestCase):
    def test_parser_accepts_conversation_over_two_minutes_but_enforces_new_limit(self):
        long_messages = [
            {"sender": "Maya", "text": f"Message {index}", "reply_delay": 30, "typing_duration": 1}
            for index in range(4)
        ]
        script = parse_script(json.dumps({"messages": long_messages}), "json")

        self.assertGreater(script["duration"], 120)
        self.assertLessEqual(script["duration"], MAX_VIDEO_DURATION)

        too_long_messages = [
            {"sender": "Maya", "text": f"Message {index}", "reply_delay": 30, "typing_duration": 1}
            for index in range(6)
        ]
        with self.assertRaisesRegex(ValueError, "180 seconds"):
            parse_script(json.dumps({"messages": too_long_messages}), "json")

    def test_video_dimensions_include_exact_phone_preview_ratio(self):
        self.assertEqual(_video_dimensions("preview"), (1080, 1620))
        self.assertEqual(_video_dimensions("iphone"), (1170, 2532))
        self.assertEqual(_video_dimensions("portrait"), (1080, 1920))
        self.assertEqual(_video_dimensions("square"), (1080, 1080))
        with self.assertRaisesRegex(ValueError, "Aspect ratio must be"):
            _video_dimensions("unsupported")

    def test_significant_time_gap_threshold_and_midnight_wrap(self):
        self.assertFalse(_has_significant_time_gap("21:00", "21:59"))
        self.assertTrue(_has_significant_time_gap("21:00", "22:00"))
        self.assertFalse(_has_significant_time_gap("23:50", "00:10"))
        self.assertTrue(_has_significant_time_gap("23:50", "00:50"))

    def test_phone_clock_tracks_latest_revealed_message_timestamp(self):
        script = parse_script(
            '{"start_time":"23:40","messages":['
            '{"sender":"Maya","text":"First","timestamp":"23:41","reply_delay":0,'
            '"typing_duration":0},'
            '{"sender":"you","text":"Second","timestamp":"23:42","reply_delay":0,'
            '"typing_duration":0}]}',
            "json",
        )
        first, second = script["timeline"]

        self.assertEqual(_status_time_at(script["timeline"], first.reveal_at - 0.01, "23:40"), "23:40")
        self.assertEqual(_status_time_at(script["timeline"], first.reveal_at, "23:40"), "23:41")
        self.assertEqual(_status_time_at(script["timeline"], second.reveal_at - 0.01, "23:40"), "23:41")
        self.assertEqual(_status_time_at(script["timeline"], second.reveal_at, "23:40"), "23:42")

    def test_outgoing_typing_reveals_message_text_progressively(self):
        self.assertEqual(_typed_text("hello", 0), "")
        self.assertEqual(_typed_text("hello", 0.4), "he")
        self.assertEqual(_typed_text("hello", 1), "hello")
        self.assertEqual(_typed_text("hello", 2), "hello")

    def test_json_script_maps_senders_and_defaults(self):
        script = parse_script(
            '{"contact":"Maya","messages":[{"sender":"you","text":"Hi","typing_duration":0}]}',
            "json",
        )

        message = script["messages"][0]
        self.assertEqual(message.sender, "you")
        self.assertTrue(message.outgoing)
        self.assertEqual(message.typing_duration, 0)
        self.assertEqual(message.reply_delay, DEFAULT_REPLY_DELAY)
        self.assertEqual(script["contact"], "Maya")
        self.assertEqual(script["avatar_display"], "M")

    def test_avatar_customization_and_unnamed_contact_fallback(self):
        named = parse_script(
            '{"contact":"Maya Chen","avatar":"🌙","messages":[{"sender":"Maya","text":"Hi"}]}',
            "json",
        )
        unnamed = parse_script(
            '{"messages":[{"sender":"you","text":"Hi"}]}',
            "json",
        )
        inferred = parse_script(
            '{"messages":[{"sender":"Alex Rivera","text":"Hi"}]}',
            "json",
        )

        self.assertEqual(named["avatar"], "🌙")
        self.assertEqual(named["avatar_display"], "🌙")
        self.assertFalse(unnamed["contact_has_name"])
        self.assertEqual(unnamed["avatar_display"], "👻")
        self.assertEqual(inferred["contact"], "Alex Rivera")
        self.assertEqual(inferred["avatar_display"], "AR")

    def test_avatar_custom_text_is_rendered_in_the_contact_circle(self):
        script = parse_script(
            '{"contact":"Maya Chen","avatar":"🌙","messages":[{"sender":"Maya","text":"Hi"}]}',
            "json",
        )

        frame = _render_frame(
            script["contact"],
            script["timeline"],
            0,
            1080,
            1920,
            avatar=script["avatar_display"],
        )
        initials_frame = _render_frame(
            script["contact"],
            script["timeline"],
            0,
            1080,
            1920,
            avatar="MC",
        )

        self.assertNotEqual(frame.crop((500, 105, 580, 190)).tobytes(),
                            initials_frame.crop((500, 105, 580, 190)).tobytes())

    def test_rejects_empty_or_oversized_custom_avatar(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            parse_script(
                '{"avatar":" ","messages":[{"sender":"Maya","text":"Hi"}]}',
                "json",
            )
        with self.assertRaisesRegex(ValueError, "8 characters"):
            parse_script(
                '{"avatar":"123456789","messages":[{"sender":"Maya","text":"Hi"}]}',
                "json",
            )

    def test_json_script_accepts_custom_reply_delay(self):
        script = parse_script(
            '{"messages":[{"sender":"Maya","text":"Hi","reply_delay":2.4}]}',
            "json",
        )

        self.assertEqual(script["messages"][0].reply_delay, 2.4)
        self.assertEqual(script["timeline"][0].typing_start, 3.05)

    def test_json_script_loads_history_and_assigns_clock_times(self):
        script = parse_script(
            '{"contact":"Maya","start_time":"21:14","history":['
            '{"sender":"Maya","text":"Earlier"},{"sender":"you","text":"Thanks"}],'
            '"messages":[{"sender":"Maya","text":"Hi"},{"sender":"you","text":"Hello","timestamp":"21:18"}]}',
            "json",
        )

        self.assertEqual(script["start_time"], "21:14")
        self.assertEqual([item.timestamp for item in script["history"]], ["21:12", "21:13"])
        self.assertEqual([item.timestamp for item in script["timeline"]], ["21:14", "21:18"])

    def test_rejects_invalid_clock_time(self):
        with self.assertRaisesRegex(ValueError, "HH:MM"):
            parse_script('{"start_time":"9:41","messages":[{"sender":"Maya","text":"Hi"}]}', "json")

    def test_yaml_script_accepts_message_and_typing_aliases(self):
        script = parse_script(
            'messages:\n  - sender: Alex\n    typing: 2\n    delay_before_typing: 1.7\n    message: "See you soon"\n',
            "yaml",
        )

        message = script["messages"][0]
        self.assertEqual(message.text, "See you soon")
        self.assertEqual(message.typing_duration, 2)
        self.assertEqual(message.reply_delay, 1.7)
        self.assertFalse(message.outgoing)
        self.assertEqual(script["contact"], "Alex")

    def test_rejects_boolean_typing_duration(self):
        with self.assertRaisesRegex(ValueError, "must be a number"):
            parse_script(
                '{"messages":[{"sender":"you","text":"Hi","typing_duration":true}]}',
                "json",
            )

    def test_rejects_invalid_reply_delay(self):
        with self.assertRaisesRegex(ValueError, "reply_delay"):
            parse_script(
                '{"messages":[{"sender":"Maya","text":"Hi","reply_delay":-1}]}',
                "json",
            )

    def test_rejects_missing_messages(self):
        with self.assertRaisesRegex(ValueError, "at least one message"):
            parse_script('{"messages":[]}', "json")

    def test_timeline_places_bubble_after_typing(self):
        first = ChatMessage("Alex", "Hi", 2.0, False, 1.5)
        second = ChatMessage("you", "Hello", 1.0, True, 2.0)

        timeline, duration = build_timeline([first, second])

        self.assertEqual(timeline[0].typing_start, 2.15)
        self.assertEqual(timeline[0].reveal_at, 4.15)
        self.assertAlmostEqual(timeline[1].typing_start, 6.15)
        self.assertAlmostEqual(timeline[1].typing_end, 7.15)
        self.assertAlmostEqual(timeline[1].reveal_at, 7.31)
        self.assertAlmostEqual(duration, 8.91)


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for the video export test")
class RenderVideoTests(unittest.TestCase):
    def test_composer_action_switches_from_mic_to_send_arrow(self):
        idle = Image.new("RGB", (1080, 1920), "#FFFFFF")
        typing = Image.new("RGB", (1080, 1920), "#FFFFFF")

        _draw_composer(idle, 1)
        _draw_composer(typing, 1, "Hello")

        action_box = (1000, 1700, 1070, 1790)
        idle_action = idle.crop(action_box)
        typing_action = typing.crop(action_box)

        self.assertNotIn((10, 132, 255), set(idle_action.getdata()))
        self.assertIn((10, 132, 255), set(typing_action.getdata()))

    def test_composer_grows_for_multiline_text_without_shrinking_font(self):
        short = Image.new("RGB", (1080, 1920), "#FFFFFF")
        long = Image.new("RGB", (1080, 1920), "#FFFFFF")
        _draw_composer(short, 1, "M")
        _draw_composer(long, 1, "MMMM " * 70)

        def field_bounds(image):
            border_pixels = [
                (x, y)
                for y in range(1550, 1800)
                for x in range(100, 1000)
                if image.getpixel((x, y)) == (210, 210, 215)
            ]
            return (
                min(y for _, y in border_pixels),
                max(y for _, y in border_pixels),
            )

        def tallest_text_line(image):
            active_rows = [
                y
                for y in range(1550, 1800)
                if sum(
                    1
                    for x in range(125, 950)
                    if max(image.getpixel((x, y))) < 100
                )
                > 2
            ]
            groups = []
            for row in active_rows:
                if not groups or row > groups[-1][-1] + 1:
                    groups.append([row])
                else:
                    groups[-1].append(row)
            return max(map(len, groups))

        short_bounds = field_bounds(short)
        long_bounds = field_bounds(long)

        self.assertLess(long_bounds[0], short_bounds[0])
        self.assertEqual(long_bounds[1], short_bounds[1])
        self.assertEqual(tallest_text_line(long), tallest_text_line(short))

    def test_video_composer_expansion_moves_chat_viewport_up(self):
        long_text = "A readable message that keeps its font size while the composer grows. " * 4
        short_script = parse_script(
            '{"messages":[{"sender":"you","text":"Hi","typing_duration":10}]}',
            "json",
        )
        long_script = parse_script(
            json.dumps(
                {"messages": [{"sender": "you", "text": long_text, "typing_duration": 10}]}
            ),
            "json",
        )
        short_time = short_script["timeline"][0].typing_start + 6
        long_time = long_script["timeline"][0].typing_start + 6
        short_frame = _render_frame(
            short_script["contact"], short_script["timeline"], short_time, 1080, 1920
        )
        long_frame = _render_frame(
            long_script["contact"], long_script["timeline"], long_time, 1080, 1920
        )

        def composer_divider_y(frame):
            return min(
                y
                for y in range(1500, 1750)
                if frame.getpixel((540, y)) == (230, 230, 232)
            )

        self.assertLess(composer_divider_y(long_frame), composer_divider_y(short_frame))

    def test_outgoing_typing_frame_shows_text_in_composer_without_typing_dots(self):
        script = parse_script(
            '{"messages":[{"sender":"you","text":"I am typing","typing_duration":2}]}',
            "json",
        )

        frame = _render_frame("Maya", script["timeline"], 1.65, 1080, 1920)
        idle_frame = _render_frame("Maya", script["timeline"], 0.5, 1080, 1920)
        chat_area = frame.crop((0, round(1920 * 0.205), 1080, round(1920 * 0.86)))
        composer_area = frame.crop((90, round(1920 * 0.88), 1080, 1820))
        idle_composer_area = idle_frame.crop((90, round(1920 * 0.88), 1010, 1820))

        self.assertNotIn((10, 132, 255), set(chat_area.getdata()))
        self.assertNotEqual(composer_area.tobytes(), idle_composer_area.tobytes())

    def test_preloaded_history_is_visible_before_animated_messages(self):
        script = parse_script(
            '{"start_time":"21:14","history":[{"sender":"Maya","text":"Earlier"}],'
            '"messages":[{"sender":"Maya","text":"Next","typing_duration":0}]}',
            "json",
        )

        frame = _render_frame(
            "Maya",
            script["timeline"],
            0,
            1080,
            1920,
            script["history"],
            script["start_time"],
        )

        self.assertEqual(frame.getpixel((50, round(1920 * 0.205) + 10)), (233, 233, 235))
        self.assertEqual(script["timeline"][0].timestamp, "21:14")

    def test_start_time_stamp_follows_preloaded_history(self):
        script = parse_script(
            '{"start_time":"21:14","history":[{"sender":"Maya","text":"Earlier",'
            '"timestamp":"21:12"}],"messages":[{"sender":"Maya","text":"Next"}]}',
            "json",
        )
        frame = _render_frame(
            "Maya",
            script["timeline"],
            0,
            1080,
            1920,
            script["history"],
            script["start_time"],
        )
        top = round(1920 * 0.205)
        centered_gray_rows = [
            y
            for y in range(top, round(1920 * 0.86))
            if any(frame.getpixel((x, y)) == (142, 142, 147) for x in range(360, 720))
        ]

        self.assertTrue(centered_gray_rows)
        self.assertGreater(min(centered_gray_rows), top + 60)

    def test_video_adds_one_centered_marker_for_significant_gap(self):
        script = parse_script(
            '{"start_time":"10:00","messages":['
            '{"sender":"Maya","text":"Start","timestamp":"10:00","typing_duration":0,"reply_delay":0},'
            '{"sender":"you","text":"Later","timestamp":"11:15","typing_duration":0,"reply_delay":0}]}',
            "json",
        )
        frame = _render_frame(
            script["contact"],
            script["timeline"],
            script["timeline"][-1].reveal_at + 0.2,
            1080,
            1920,
            script["history"],
            script["start_time"],
        )
        top = round(1920 * 0.205)
        centered_gray_rows = [
            y
            for y in range(top, round(1920 * 0.86))
            if any(frame.getpixel((x, y)) == (142, 142, 147) for x in range(360, 720))
        ]
        marker_groups = []
        for row in centered_gray_rows:
            if not marker_groups or row > marker_groups[-1][-1] + 2:
                marker_groups.append([row])
            else:
                marker_groups[-1].append(row)

        self.assertEqual(len(marker_groups), 2)

    def test_video_scene_includes_phone_bezel_and_background(self):
        script = parse_script(
            '{"messages":[{"sender":"Maya","text":"Hello","typing_duration":0}]}',
            "json",
        )

        frame = _render_phone_scene("Maya", script["timeline"], 0, 1080, 1920)

        self.assertEqual(frame.size, (1080, 1920))
        self.assertEqual(frame.getpixel((0, 0)), (233, 237, 242))
        self.assertEqual(frame.getpixel((540, 60)), (37, 39, 44))

    def test_preview_card_scene_includes_stage_background_around_phone(self):
        script = parse_script(
            '{"messages":[{"sender":"Maya","text":"Hello","typing_duration":0}]}',
            "json",
        )
        frame = _render_phone_scene(
            script["contact"],
            script["timeline"],
            0,
            1080,
            1620,
            script["history"],
            script["start_time"],
            script["avatar_display"],
            preview_card=True,
        )

        self.assertEqual(frame.size, (1080, 1620))
        self.assertEqual(frame.getpixel((0, 0)), (233, 237, 242))
        self.assertNotEqual(frame.getpixel((40, 800)), (233, 237, 242))
        self.assertEqual(frame.getpixel((865, 1000)), (37, 39, 44))

    def test_overflowing_conversation_scrolls_latest_bubble_into_view(self):
        script = parse_script(
            json.dumps(
                {
                    "messages": [
                        {
                            "sender": "you" if index == 23 else "Maya",
                            "text": f"Message {index}",
                            "typing_duration": 0,
                            "reply_delay": 0,
                        }
                        for index in range(24)
                    ]
                }
            ),
            "json",
        )

        frame = _render_frame(
            script["contact"],
            script["timeline"],
            script["timeline"][-1].reveal_at + 0.5,
            1080,
            1920,
        )
        latest_bubble_pixels = [
            (x, y)
            for y in range(1450, 1620)
            for x in range(100, 1000)
            if frame.getpixel((x, y)) == (10, 132, 255)
        ]

        self.assertGreater(len(latest_bubble_pixels), 100)

    def test_start_time_stamp_scrolls_away_with_long_conversation(self):
        script = parse_script(
            json.dumps(
                {
                    "start_time": "23:40",
                    "messages": [
                        {
                            "sender": "Maya",
                            "text": f"Message {index}",
                            "typing_duration": 0,
                            "reply_delay": 0,
                        }
                        for index in range(24)
                    ],
                }
            ),
            "json",
        )

        initial_frame = _render_frame(
            script["contact"], script["timeline"], 0, 1080, 1920
        )
        final_frame = _render_frame(
            script["contact"],
            script["timeline"],
            script["timeline"][-1].reveal_at + 0.5,
            1080,
            1920,
        )
        date_band = (360, round(1920 * 0.205) + 10, 720, round(1920 * 0.205) + 70)
        initial_stamp_pixels = sum(
            1 for pixel in initial_frame.crop(date_band).getdata() if pixel == (142, 142, 147)
        )
        final_stamp_pixels = sum(
            1 for pixel in final_frame.crop(date_band).getdata() if pixel == (142, 142, 147)
        )

        self.assertGreater(initial_stamp_pixels, 0)
        self.assertEqual(final_stamp_pixels, 0)

    def test_video_chat_scroll_eases_to_new_bottom_position(self):
        events = [(1.0, 40)]

        offsets = [
            _scroll_offset(80, events, elapsed, 100)
            for elapsed in (0.99, 1.0, 1.14, 1.28)
        ]

        self.assertEqual(offsets, [0, 0, 17, 20])

    def test_status_icons_share_a_common_baseline(self):
        frame = Image.new("RGB", (1080, 1920), "#FFFFFF")
        _draw_chrome(frame, "Maya", 1, "09:41")

        def ink_bounds(left, right):
            points = [
                (x, y)
                for y in range(20, 66)
                for x in range(left, right)
                if max(frame.getpixel((x, y))) < 80
            ]
            return (
                min(x for x, _ in points),
                min(y for _, y in points),
                max(x for x, _ in points),
                max(y for _, y in points),
            )

        signal = ink_bounds(810, 880)
        wifi = ink_bounds(880, 940)
        battery = ink_bounds(940, 1000)

        self.assertLessEqual(abs((wifi[0] - signal[2]) - (battery[0] - wifi[2])), 1)
        self.assertEqual(signal[3], wifi[3])
        self.assertEqual(wifi[3], battery[3])

    def test_video_header_divider_matches_phone_preview_layout(self):
        frame = Image.new("RGB", (390, 844), "#FFFFFF")
        _draw_chrome(frame, "Maya", 390 / 1080, "09:41")

        divider_y = round(844 * 0.18)

        self.assertEqual(frame.getpixel((0, divider_y)), (230, 230, 232))

    def test_outgoing_message_waits_in_composer_for_send_press(self):
        script = parse_script(
            '{"messages":[{"sender":"you","text":"Sending now","typing_duration":2}]}',
            "json",
        )

        frame = _render_frame(
            "Maya",
            script["timeline"],
            script["timeline"][0].typing_end + SEND_DELAY / 2,
            1080,
            1920,
        )
        chat_area = frame.crop((0, round(1920 * 0.205), 1080, round(1920 * 0.86)))
        composer_area = frame.crop((90, round(1920 * 0.88), 1080, 1820))

        self.assertNotIn((10, 132, 255), set(chat_area.getdata()))
        self.assertIn((10, 132, 255), set(composer_area.getdata()))

    def test_renders_a_playable_mp4(self):
        script = parse_script(
            '{"contact":"Maya","messages":[{"sender":"Maya","text":"Hello","typing_duration":0}]}',
            "json",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "conversation.mp4"

            render_video(script, output, aspect_ratio="preview", fps=12)

            self.assertGreater(output.stat().st_size, 0)
            self.assertEqual(output.read_bytes()[4:8], b"ftyp")

    @unittest.skipUnless(_emoji_font(37), "A color emoji font is required for the emoji export test")
    def test_encoded_mp4_keeps_color_emoji(self):
        script = parse_script(
            '{"messages":[{"sender":"Maya","text":"Hello 😊","typing_duration":0}]}',
            "json",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "emoji.mp4"
            render_video(script, output, aspect_ratio="portrait", fps=12)
            decoded = subprocess.run(
                [
                    shutil.which("ffmpeg"),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    "2.0",
                    "-i",
                    str(output),
                    "-frames:v",
                    "1",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "png",
                    "-",
                ],
                check=True,
                capture_output=True,
            )
            frame = Image.open(BytesIO(decoded.stdout)).convert("RGB")
            colors = frame.crop((220, 380, 300, 420)).getdata()

            self.assertTrue(
                any(max(pixel) - min(pixel) > 80 and max(pixel) > 150 for pixel in colors),
                "The decoded MP4 frame should contain saturated emoji pixels.",
            )


if __name__ == "__main__":
    unittest.main()
