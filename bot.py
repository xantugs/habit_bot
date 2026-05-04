import os
import math
import csv
import io
import random
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont


# ======================
# Config
# ======================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))

# Simplified server layout:
# HABIT_CHANNEL_ID  -> #habit-tracker
# REPORT_CHANNEL_ID -> #progress-feed
HABIT_CHANNEL_ID = int(os.getenv("HABIT_CHANNEL_ID", "0"))
REPORT_CHANNEL_ID = int(os.getenv("REPORT_CHANNEL_ID", "0"))

# Optional. If left empty, streak/fact posts go to REPORT_CHANNEL_ID.
STREAK_CHANNEL_ID = int(os.getenv("STREAK_CHANNEL_ID", "0"))
FACTS_CHANNEL_ID = int(os.getenv("FACTS_CHANNEL_ID", "0"))

DISCORD_PROXY = os.getenv("DISCORD_PROXY")

TZ = ZoneInfo(os.getenv("TZ", "Asia/Shanghai"))
DB_PATH = "habit_bot.sqlite3"

VALID_DAY_RATE = 0.60


# ======================
# Database helpers
# ======================

def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                points INTEGER NOT NULL DEFAULT 10,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS habit_logs (
                user_id TEXT NOT NULL,
                habit_id INTEGER NOT NULL,
                log_date TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, habit_id, log_date)
            );

            CREATE TABLE IF NOT EXISTS daily_messages (
                user_id TEXT NOT NULL,
                log_date TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                PRIMARY KEY (user_id, log_date)
            );

            CREATE TABLE IF NOT EXISTS daily_reports (
                report_date TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                posted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS daily_streak_posts (
                post_date TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                posted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS daily_fact_posts (
                post_date TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                posted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


def today_str():
    return datetime.now(TZ).date().isoformat()


def date_range_for_days(days: int):
    end_date = datetime.now(TZ).date()
    start_date = end_date - timedelta(days=days - 1)
    return start_date, end_date


def add_habit(user_id: str, name: str, points: int):
    name = name.strip()

    with connect_db() as conn:
        existing = conn.execute(
            """
            SELECT id FROM habits
            WHERE user_id = ?
              AND lower(name) = lower(?)
              AND active = 1
            """,
            (user_id, name),
        ).fetchone()

        if existing:
            return False

        conn.execute(
            """
            INSERT INTO habits (user_id, name, points)
            VALUES (?, ?, ?)
            """,
            (user_id, name, points),
        )

    return True


def remove_habit(user_id: str, name: str):
    with connect_db() as conn:
        cur = conn.execute(
            """
            UPDATE habits
            SET active = 0
            WHERE user_id = ?
              AND lower(name) = lower(?)
              AND active = 1
            """,
            (user_id, name.strip()),
        )

    return cur.rowcount > 0


def get_habits(user_id: str):
    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, name, points, active
            FROM habits
            WHERE user_id = ?
              AND active = 1
            ORDER BY id
            """,
            (user_id,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_active_user_ids():
    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT user_id
            FROM habits
            WHERE active = 1
            """
        ).fetchall()

    return [row["user_id"] for row in rows]


def is_completed(user_id: str, habit_id: int, log_date: str):
    with connect_db() as conn:
        row = conn.execute(
            """
            SELECT completed
            FROM habit_logs
            WHERE user_id = ?
              AND habit_id = ?
              AND log_date = ?
            """,
            (user_id, habit_id, log_date),
        ).fetchone()

    return bool(row and row["completed"] == 1)


def toggle_habit(user_id: str, habit_id: int, log_date: str):
    current = is_completed(user_id, habit_id, log_date)
    new_value = 0 if current else 1

    with connect_db() as conn:
        conn.execute(
            """
            INSERT INTO habit_logs (user_id, habit_id, log_date, completed, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, habit_id, log_date)
            DO UPDATE SET
                completed = excluded.completed,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, habit_id, log_date, new_value),
        )

    return bool(new_value)


def daily_summary(user_id: str, log_date: str):
    habits = get_habits(user_id)
    total = len(habits)

    completed_count = 0
    score = 0
    habit_rows = []

    for habit in habits:
        done = is_completed(user_id, habit["id"], log_date)

        if done:
            completed_count += 1
            score += habit["points"]

        habit_rows.append(
            {
                "id": habit["id"],
                "name": habit["name"],
                "points": habit["points"],
                "done": done,
            }
        )

    rate = completed_count / total if total else 0
    valid_day = total > 0 and rate >= VALID_DAY_RATE

    return {
        "total": total,
        "completed": completed_count,
        "rate": rate,
        "score": score,
        "valid_day": valid_day,
        "habits": habit_rows,
    }


def save_daily_message(user_id: str, log_date: str, channel_id: int, message_id: int):
    with connect_db() as conn:
        conn.execute(
            """
            INSERT INTO daily_messages (user_id, log_date, channel_id, message_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, log_date)
            DO UPDATE SET
                channel_id = excluded.channel_id,
                message_id = excluded.message_id
            """,
            (user_id, log_date, str(channel_id), str(message_id)),
        )


def daily_message_exists(user_id: str, log_date: str):
    with connect_db() as conn:
        row = conn.execute(
            """
            SELECT message_id
            FROM daily_messages
            WHERE user_id = ?
              AND log_date = ?
            """,
            (user_id, log_date),
        ).fetchone()

    return row is not None


def get_todays_daily_messages():
    log_date = today_str()

    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT user_id, log_date, channel_id, message_id
            FROM daily_messages
            WHERE log_date = ?
            """,
            (log_date,),
        ).fetchall()

    return [dict(row) for row in rows]


def _already_posted(table_name: str, date_col: str, date_value: str):
    with connect_db() as conn:
        row = conn.execute(
            f"SELECT {date_col} FROM {table_name} WHERE {date_col} = ?",
            (date_value,),
        ).fetchone()

    return row is not None


def _mark_posted(table_name: str, date_col: str, date_value: str, channel_id: int):
    with connect_db() as conn:
        conn.execute(
            f"""
            INSERT OR IGNORE INTO {table_name} ({date_col}, channel_id)
            VALUES (?, ?)
            """,
            (date_value, str(channel_id)),
        )


def report_already_posted(report_date: str):
    return _already_posted("daily_reports", "report_date", report_date)


def mark_report_posted(report_date: str, channel_id: int):
    _mark_posted("daily_reports", "report_date", report_date, channel_id)


def streak_post_already_posted(post_date: str):
    return _already_posted("daily_streak_posts", "post_date", post_date)


def mark_streak_posted(post_date: str, channel_id: int):
    _mark_posted("daily_streak_posts", "post_date", post_date, channel_id)


def fact_post_already_posted(post_date: str):
    return _already_posted("daily_fact_posts", "post_date", post_date)


def mark_fact_posted(post_date: str, channel_id: int):
    _mark_posted("daily_fact_posts", "post_date", post_date, channel_id)


def stats_for_user(user_id: str, days: int):
    start_date, end_date = date_range_for_days(days)

    habits = get_habits(user_id)
    result = []

    with connect_db() as conn:
        for habit in habits:
            row = conn.execute(
                """
                SELECT COUNT(*) AS completed_count
                FROM habit_logs
                WHERE user_id = ?
                  AND habit_id = ?
                  AND completed = 1
                  AND log_date BETWEEN ? AND ?
                """,
                (
                    user_id,
                    habit["id"],
                    start_date.isoformat(),
                    end_date.isoformat(),
                ),
            ).fetchone()

            completed = row["completed_count"]
            percentage = completed / days if days else 0

            result.append(
                {
                    "habit": habit["name"],
                    "completed": completed,
                    "days": days,
                    "percentage": percentage,
                }
            )

    return result


def get_daily_completion_counts(user_id: str, days: int):
    start_date, _ = date_range_for_days(days)
    habits = get_habits(user_id)

    results = []

    for i in range(days):
        current_date = start_date + timedelta(days=i)
        current_iso = current_date.isoformat()

        completed = 0
        for habit in habits:
            if is_completed(user_id, habit["id"], current_iso):
                completed += 1

        total = len(habits)
        rate = completed / total if total else 0

        results.append(
            {
                "date": current_date,
                "completed": completed,
                "total": total,
                "rate": rate,
            }
        )

    return results


def get_streak_info(user_id: str, days: int = 365):
    daily = get_daily_completion_counts(user_id, days)

    current_streak = 0
    best_streak = 0
    running = 0

    for day in daily:
        valid = day["total"] > 0 and day["rate"] >= VALID_DAY_RATE

        if valid:
            running += 1
            best_streak = max(best_streak, running)
        else:
            running = 0

    for day in reversed(daily):
        valid = day["total"] > 0 and day["rate"] >= VALID_DAY_RATE

        if valid:
            current_streak += 1
        else:
            break

    return {
        "current_streak": current_streak,
        "best_streak": best_streak,
    }


def valid_days_count(user_id: str, days: int = 30) -> int:
    daily = get_daily_completion_counts(user_id, days)
    return sum(1 for day in daily if day["total"] > 0 and day["rate"] >= VALID_DAY_RATE)


def perfect_days_count(user_id: str, days: int = 30) -> int:
    daily = get_daily_completion_counts(user_id, days)
    return sum(1 for day in daily if day["total"] > 0 and day["rate"] == 1)


def get_best_and_worst_habits(user_id: str, days: int = 30):
    habits = get_habits(user_id)

    if not habits:
        return None, None

    start_date, _ = date_range_for_days(days)
    rows = []

    for habit in habits:
        completed = 0

        for i in range(days):
            current_date = start_date + timedelta(days=i)

            if is_completed(user_id, habit["id"], current_date.isoformat()):
                completed += 1

        rate = completed / days if days else 0

        rows.append(
            {
                "name": habit["name"],
                "completed": completed,
                "days": days,
                "rate": rate,
            }
        )

    best = max(rows, key=lambda x: x["rate"])
    worst = min(rows, key=lambda x: x["rate"])

    return best, worst


def get_most_dangerous_weekday(user_id: str, days: int = 60):
    daily = get_daily_completion_counts(user_id, days)

    weekday_data = {}

    for day in daily:
        weekday = day["date"].strftime("%A")

        if weekday not in weekday_data:
            weekday_data[weekday] = {"total": 0, "failed": 0}

        weekday_data[weekday]["total"] += 1

        valid = day["total"] > 0 and day["rate"] >= VALID_DAY_RATE

        if not valid:
            weekday_data[weekday]["failed"] += 1

    if not weekday_data:
        return None

    weekday, data = max(
        weekday_data.items(),
        key=lambda item: item[1]["failed"] / item[1]["total"] if item[1]["total"] else 0,
    )

    fail_rate = data["failed"] / data["total"] if data["total"] else 0

    return {
        "weekday": weekday,
        "failed": data["failed"],
        "total": data["total"],
        "fail_rate": fail_rate,
    }


def get_user_summary(user_id: str, days: int = 30):
    habits = get_habits(user_id)
    daily = get_daily_completion_counts(user_id, days)
    streak = get_streak_info(user_id, max(days, 365))

    total_possible = sum(day["total"] for day in daily)
    total_completed = sum(day["completed"] for day in daily)
    overall_rate = total_completed / total_possible if total_possible else 0

    best_habit = "—"
    best_rate = -1

    for habit in habits:
        completed = 0

        for day in daily:
            if is_completed(user_id, habit["id"], day["date"].isoformat()):
                completed += 1

        rate = completed / days if days else 0

        if rate > best_rate:
            best_rate = rate
            best_habit = habit["name"]

    weekly_rates = []

    for start_idx in range(0, days, 7):
        chunk = daily[start_idx:start_idx + 7]

        if not chunk:
            continue

        chunk_possible = sum(day["total"] for day in chunk)
        chunk_completed = sum(day["completed"] for day in chunk)
        rate = chunk_completed / chunk_possible if chunk_possible else 0
        weekly_rates.append(rate)

    return {
        "habits": habits,
        "daily": daily,
        "overall_rate": overall_rate,
        "total_completed": total_completed,
        "total_possible": total_possible,
        "current_streak": streak["current_streak"],
        "best_streak": streak["best_streak"],
        "best_habit": best_habit,
        "weekly_rates": weekly_rates,
    }


async def get_display_name(guild: discord.Guild, user_id: str) -> str:
    try:
        member = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id))
        return member.display_name
    except Exception:
        return f"User {user_id}"


# ======================
# Visual helpers
# ======================

def progress_bar(rate: float, width: int = 10) -> str:
    rate = max(0, min(rate, 1))
    filled = round(rate * width)
    empty = width - filled
    return "🟩" * filled + "⬜" * empty


def load_font(size: int, bold: bool = False):
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/calibrib.ttf" if bold else "C:/Windows/Fonts/calibri.ttf",
    ]

    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass

    return ImageFont.load_default()


def create_progress_table_image(display_name: str, user_id: str, days: int = 30) -> io.BytesIO:
    start_date, end_date = date_range_for_days(days)
    habits = get_habits(user_id)

    if not habits:
        width, height = 900, 260
        bg = (24, 26, 32)
        card = (31, 34, 42)
        text = (245, 247, 250)
        muted = (170, 176, 190)

        img = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)

        title_font = load_font(34, bold=True)
        body_font = load_font(20, bold=False)

        draw.rounded_rectangle((28, 28, width - 28, height - 28), radius=24, fill=card)
        draw.text((56, 56), f"{display_name}'s Last {days} Days", font=title_font, fill=text)
        draw.text((56, 118), "No habits found yet. Use /addhabit first.", font=body_font, fill=muted)

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    margin = 28
    inner_pad = 18

    title_area_h = 70
    stats_area_h = 74
    header_gap = 20
    header_h = 42
    row_h = 40
    row_gap = 6
    footer_h = 44

    date_w = 118
    cell_w = 138 if len(habits) <= 4 else 122

    table_w = date_w + len(habits) * cell_w
    width = margin * 2 + inner_pad * 2 + table_w
    height = (
        margin * 2
        + title_area_h
        + stats_area_h
        + header_gap
        + header_h
        + days * (row_h + row_gap)
        + footer_h
        + 18
    )

    bg = (24, 26, 32)
    card = (31, 34, 42)
    panel = (39, 42, 51)
    panel_2 = (45, 49, 60)

    row_a = (42, 45, 54)
    row_b = (37, 40, 49)

    text = (245, 247, 250)
    muted = (170, 176, 190)
    subtle = (115, 122, 136)

    green = (95, 164, 105)
    green_dark = (76, 135, 86)

    gray_box = (95, 102, 116)
    gray_box_dark = (83, 89, 102)

    blue_soft = (72, 99, 153)
    separator = (58, 63, 74)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    title_font = load_font(36, bold=True)
    subtitle_font = load_font(18, bold=False)
    header_font = load_font(20, bold=True)
    body_font = load_font(19, bold=False)
    small_font = load_font(16, bold=False)
    cell_font = load_font(22, bold=True)
    stat_value_font = load_font(22, bold=True)
    stat_label_font = load_font(16, bold=False)

    draw.rounded_rectangle(
        (margin, margin, width - margin, height - margin),
        radius=24,
        fill=card,
    )

    content_x = margin + inner_pad
    content_y = margin + inner_pad

    draw.text(
        (content_x, content_y),
        f"{display_name}'s Last {days} Days",
        font=title_font,
        fill=text,
    )
    draw.text(
        (content_x, content_y + 42),
        "Daily habit history",
        font=subtitle_font,
        fill=muted,
    )

    total_possible = days * len(habits)
    total_done = 0
    today_done = 0

    for i in range(days):
        current_date = start_date + timedelta(days=i)
        current_iso = current_date.isoformat()

        for habit in habits:
            done = is_completed(user_id, habit["id"], current_iso)

            if done:
                total_done += 1

                if current_date == end_date:
                    today_done += 1

    overall_rate = total_done / total_possible if total_possible else 0

    best_habit_name = habits[0]["name"]
    best_habit_rate = -1

    for habit in habits:
        completed = 0

        for i in range(days):
            current_date = start_date + timedelta(days=i)

            if is_completed(user_id, habit["id"], current_date.isoformat()):
                completed += 1

        rate = completed / days if days else 0

        if rate > best_habit_rate:
            best_habit_rate = rate
            best_habit_name = habit["name"]

    stat_y = content_y + title_area_h
    stat_w = 168
    stat_h = 58
    stat_gap = 12

    stats = [
        ("Overall", f"{overall_rate:.0%}"),
        ("Today", f"{today_done}/{len(habits)}"),
        ("Best Habit", best_habit_name[:12] + ("…" if len(best_habit_name) > 12 else "")),
    ]

    for idx, (label, value) in enumerate(stats):
        x1 = content_x + idx * (stat_w + stat_gap)
        y1 = stat_y
        x2 = x1 + stat_w
        y2 = y1 + stat_h

        draw.rounded_rectangle((x1, y1, x2, y2), radius=16, fill=panel)
        draw.text((x1 + 14, y1 + 9), label, font=stat_label_font, fill=muted)
        draw.text((x1 + 14, y1 + 29), value, font=stat_value_font, fill=text)

    header_y = stat_y + stats_area_h + header_gap
    rows_y = header_y + header_h + 10

    draw.rounded_rectangle(
        (content_x, header_y, content_x + date_w - 10, header_y + header_h),
        radius=14,
        fill=panel_2,
    )
    draw.text((content_x + 16, header_y + 10), "Date", font=header_font, fill=text)

    for j, habit in enumerate(habits):
        hx = content_x + date_w + j * cell_w

        name = habit["name"]

        if len(name) > 14:
            name = name[:13] + "…"

        draw.rounded_rectangle(
            (hx + 4, header_y, hx + cell_w - 8, header_y + header_h),
            radius=14,
            fill=panel_2,
        )

        bbox = draw.textbbox((0, 0), name, font=header_font)
        tw = bbox[2] - bbox[0]
        tx = hx + (cell_w - tw) / 2
        draw.text((tx, header_y + 10), name, font=header_font, fill=text)

    for i in range(days):
        current_date = start_date + timedelta(days=i)
        current_iso = current_date.isoformat()
        is_today = current_date == end_date

        y = rows_y + i * (row_h + row_gap)
        row_fill = row_a if i % 2 == 0 else row_b

        if i > 0 and i % 7 == 0:
            draw.line(
                (content_x + 2, y - 5, width - margin - inner_pad - 2, y - 5),
                fill=separator,
                width=2,
            )

        draw.rounded_rectangle(
            (content_x, y, width - margin - inner_pad, y + row_h),
            radius=14,
            fill=row_fill,
            outline=blue_soft if is_today else row_fill,
            width=2 if is_today else 1,
        )

        date_text = current_date.strftime("%m-%d")
        weekday_text = current_date.strftime("%a")

        draw.text((content_x + 14, y + 9), date_text, font=body_font, fill=text if is_today else muted)
        draw.text((content_x + 68, y + 11), weekday_text, font=small_font, fill=subtle)

        for j, habit in enumerate(habits):
            x = content_x + date_w + j * cell_w
            done = is_completed(user_id, habit["id"], current_iso)

            box_size = 28
            bx1 = x + (cell_w - box_size) // 2
            by1 = y + (row_h - box_size) // 2
            bx2 = bx1 + box_size
            by2 = by1 + box_size

            fill = green if done else gray_box
            symbol = "✓" if done else "·"
            symbol_fill = (255, 255, 255) if done else (230, 234, 240)

            draw.rounded_rectangle(
                (bx1, by1, bx2, by2),
                radius=9,
                fill=fill,
            )

            bbox = draw.textbbox((0, 0), symbol, font=cell_font)
            sw = bbox[2] - bbox[0]
            sh = bbox[3] - bbox[1]

            sx = bx1 + (box_size - sw) / 2
            sy = by1 + (box_size - sh) / 2 - 2

            draw.text((sx, sy), symbol, font=cell_font, fill=symbol_fill)

    footer_y = height - margin - 20
    legend_x = content_x + 4

    draw.rounded_rectangle(
        (legend_x, footer_y - 8, legend_x + 18, footer_y + 10),
        radius=6,
        fill=green_dark,
    )
    draw.text((legend_x + 26, footer_y - 12), "Done", font=subtitle_font, fill=muted)

    legend_x += 95
    draw.rounded_rectangle(
        (legend_x, footer_y - 8, legend_x + 18, footer_y + 10),
        radius=6,
        fill=gray_box_dark,
    )
    draw.text((legend_x + 26, footer_y - 12), "Not done", font=subtitle_font, fill=muted)

    legend_x += 138
    draw.rounded_rectangle(
        (legend_x, footer_y - 8, legend_x + 18, footer_y + 10),
        radius=6,
        fill=blue_soft,
    )
    draw.text((legend_x + 26, footer_y - 12), "Today", font=subtitle_font, fill=muted)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def create_compare_dashboard_image(
    display_name_1: str,
    user_id_1: str,
    display_name_2: str,
    user_id_2: str,
    days: int = 30,
) -> io.BytesIO:
    s1 = get_user_summary(user_id_1, days)
    s2 = get_user_summary(user_id_2, days)

    width = 1280
    height = 900

    bg = (24, 26, 32)
    card = (31, 34, 42)
    panel = (39, 42, 51)
    panel2 = (45, 49, 60)

    text = (245, 247, 250)
    muted = (170, 176, 190)

    green = (95, 164, 105)
    blue = (100, 145, 225)
    orange = (220, 160, 75)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    title_font = load_font(38, bold=True)
    section_font = load_font(24, bold=True)
    header_font = load_font(20, bold=True)
    body_font = load_font(18, bold=False)
    stat_value_font = load_font(28, bold=True)
    stat_label_font = load_font(16, bold=False)

    draw.rounded_rectangle((24, 24, width - 24, height - 24), radius=26, fill=card)

    draw.text((50, 40), f"{display_name_1} vs {display_name_2}", font=title_font, fill=text)
    draw.text((50, 88), f"{days}-day comparison dashboard", font=body_font, fill=muted)

    winner = display_name_1 if s1["overall_rate"] >= s2["overall_rate"] else display_name_2
    draw.rounded_rectangle((470, 92, 810, 126), radius=14, fill=(52, 57, 68))
    draw.text((495, 99), f"Current leader: {winner}", font=header_font, fill=orange)

    def draw_user_block(x, y, w, h, name, user_id, summary, accent):
        draw.rounded_rectangle((x, y, x + w, y + h), radius=20, fill=panel)

        draw.text((x + 20, y + 18), name, font=section_font, fill=text)
        draw.text((x + 20, y + 48), "Summary", font=body_font, fill=muted)

        stats = [
            ("Overall", f"{summary['overall_rate']:.0%}"),
            ("Streak", str(summary["current_streak"])),
            ("Best", str(summary["best_streak"])),
            ("Best Habit", summary["best_habit"][:12] + ("…" if len(summary["best_habit"]) > 12 else "")),
        ]

        stat_w = 118
        stat_h = 74
        gap = 10
        sy = y + 88

        for i, (label, value) in enumerate(stats):
            sx = x + 20 + i * (stat_w + gap)
            draw.rounded_rectangle((sx, sy, sx + stat_w, sy + stat_h), radius=14, fill=panel2)
            draw.text((sx + 10, sy + 10), label, font=stat_label_font, fill=muted)

            value_font = header_font if i == 3 else stat_value_font
            draw.text((sx + 10, sy + 34), value, font=value_font, fill=text)

        draw.text((x + 20, sy + 98), "Weekly completion", font=header_font, fill=text)

        bar_x = x + 20
        bar_y = sy + 132
        bar_w = 360
        bar_h = 18
        bar_gap_y = 34

        for i, rate in enumerate(summary["weekly_rates"][:5]):
            yy = bar_y + i * bar_gap_y
            draw.text((bar_x, yy - 4), f"W{i + 1}", font=body_font, fill=muted)

            draw.rounded_rectangle((bar_x + 42, yy, bar_x + 42 + bar_w, yy + bar_h), radius=9, fill=(58, 62, 74))
            fill_w = int(bar_w * rate)

            if fill_w > 0:
                draw.rounded_rectangle((bar_x + 42, yy, bar_x + 42 + fill_w, yy + bar_h), radius=9, fill=accent)

            draw.text((bar_x + 42 + bar_w + 12, yy - 4), f"{rate:.0%}", font=body_font, fill=text)

        draw.text((x + 20, y + h - 170), "Habit breakdown", font=header_font, fill=text)

        hy = y + h - 138

        for habit in summary["habits"][:6]:
            completed = 0

            for day in summary["daily"]:
                if is_completed(user_id, habit["id"], day["date"].isoformat()):
                    completed += 1

            rate = completed / days if days else 0

            draw.text((x + 20, hy), habit["name"][:18], font=body_font, fill=muted)

            bx1 = x + 190
            bx2 = x + w - 70
            by1 = hy + 4
            by2 = hy + 18

            draw.rounded_rectangle((bx1, by1, bx2, by2), radius=7, fill=(58, 62, 74))
            fill_w = int((bx2 - bx1) * rate)

            if fill_w > 0:
                draw.rounded_rectangle((bx1, by1, bx1 + fill_w, by2), radius=7, fill=accent)

            draw.text((bx2 + 10, hy - 2), f"{rate:.0%}", font=body_font, fill=text)
            hy += 34

    draw_user_block(50, 135, 565, 710, display_name_1, user_id_1, s1, green)
    draw_user_block(665, 135, 565, 710, display_name_2, user_id_2, s2, blue)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def create_heatmap_image(display_name: str, user_id: str, days: int = 90) -> io.BytesIO:
    daily = get_daily_completion_counts(user_id, days)

    cell = 24
    gap = 7
    top_grid_y = 150
    left_grid_x = 78

    first_date = daily[0]["date"] if daily else datetime.now(TZ).date()
    last_date = daily[-1]["date"] if daily else datetime.now(TZ).date()

    start_monday = first_date - timedelta(days=first_date.weekday())
    end_sunday = last_date + timedelta(days=(6 - last_date.weekday()))
    total_grid_days = (end_sunday - start_monday).days + 1
    weeks = math.ceil(total_grid_days / 7)

    width = max(900, left_grid_x + weeks * (cell + gap) + 80)
    height = 420

    bg = (24, 26, 32)
    card = (31, 34, 42)
    panel = (39, 42, 51)
    text = (245, 247, 250)
    muted = (170, 176, 190)
    subtle = (115, 122, 136)

    c0 = (62, 66, 78)
    c1 = (70, 100, 78)
    c2 = (83, 130, 90)
    c3 = (95, 164, 105)
    c4 = (125, 200, 132)
    blue = (100, 145, 225)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    title_font = load_font(34, bold=True)
    body_font = load_font(18, bold=False)
    small_font = load_font(15, bold=False)
    stat_font = load_font(22, bold=True)

    draw.rounded_rectangle((24, 24, width - 24, height - 24), radius=24, fill=card)

    draw.text((48, 44), f"{display_name}'s Heatmap", font=title_font, fill=text)
    draw.text((48, 90), f"{days}-day consistency view", font=body_font, fill=muted)

    summary = get_user_summary(user_id, min(days, 365))
    stat_cards = [
        ("Overall", f"{summary['overall_rate']:.0%}"),
        ("Current Streak", str(summary["current_streak"])),
        ("Best Streak", str(summary["best_streak"])),
    ]

    sx = 420
    sy = 50

    for label, value in stat_cards:
        draw.rounded_rectangle((sx, sy, sx + 135, sy + 58), radius=14, fill=panel)
        draw.text((sx + 12, sy + 8), label, font=small_font, fill=muted)
        draw.text((sx + 12, sy + 28), value, font=stat_font, fill=text)
        sx += 150

    daily_map = {day["date"]: day for day in daily}

    weekday_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for i, label in enumerate(weekday_labels):
        y = top_grid_y + i * (cell + gap) + 4
        draw.text((32, y), label, font=small_font, fill=muted)

    current = start_monday

    for week in range(weeks):
        for weekday in range(7):
            x1 = left_grid_x + week * (cell + gap)
            y1 = top_grid_y + weekday * (cell + gap)
            x2 = x1 + cell
            y2 = y1 + cell

            day = daily_map.get(current)
            is_today = current == datetime.now(TZ).date()

            if day is None:
                fill = (48, 51, 61)
            else:
                rate = day["rate"]

                if rate == 0:
                    fill = c0
                elif rate < 0.34:
                    fill = c1
                elif rate < 0.67:
                    fill = c2
                elif rate < 1.0:
                    fill = c3
                else:
                    fill = c4

            draw.rounded_rectangle((x1, y1, x2, y2), radius=6, fill=fill)

            if is_today:
                draw.rounded_rectangle((x1 - 2, y1 - 2, x2 + 2, y2 + 2), radius=8, outline=blue, width=2)

            current += timedelta(days=1)

    legend_y = height - 62
    draw.text((48, legend_y), "Less", font=small_font, fill=muted)

    lx = 92

    for fill in [c0, c1, c2, c3, c4]:
        draw.rounded_rectangle((lx, legend_y - 2, lx + 18, legend_y + 16), radius=5, fill=fill)
        lx += 26

    draw.text((lx + 4, legend_y), "More", font=small_font, fill=muted)

    draw.text((width - 170, legend_y), "Blue outline = today", font=small_font, fill=subtle)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def export_user_progress_csv(user_id: str, days: int) -> io.StringIO:
    start_date, _ = date_range_for_days(days)
    habits = get_habits(user_id)

    output = io.StringIO()
    writer = csv.writer(output)

    header = ["Date"] + [habit["name"] for habit in habits] + [
        "Completed",
        "Total",
        "Completion Rate",
    ]
    writer.writerow(header)

    for i in range(days):
        current_date = start_date + timedelta(days=i)
        date_text = current_date.isoformat()

        row = [date_text]
        completed_count = 0

        for habit in habits:
            done = is_completed(user_id, habit["id"], date_text)
            row.append("YES" if done else "NO")

            if done:
                completed_count += 1

        total = len(habits)
        rate = completed_count / total if total else 0

        row += [completed_count, total, f"{rate:.0%}"]
        writer.writerow(row)

    output.seek(0)
    return output


# ======================
# Random facts
# ======================

async def generate_random_fact(guild: discord.Guild, days: int = 30) -> str:
    user_ids = get_active_user_ids()

    if not user_ids:
        return "No active users yet. Add habits first with `/addhabit`."

    facts = []

    for user_id in user_ids:
        name = await get_display_name(guild, user_id)

        streak = get_streak_info(user_id, 365)
        summary = get_user_summary(user_id, days)
        best, worst = get_best_and_worst_habits(user_id, days)
        dangerous_day = get_most_dangerous_weekday(user_id, 60)

        facts.append(
            f"🔥 **{name}** is currently on a **{streak['current_streak']}-day streak**. "
            f"Best streak: **{streak['best_streak']}**."
        )

        facts.append(
            f"📊 **{name}** completed **{summary['total_completed']}/{summary['total_possible']}** habits "
            f"in the last **{days} days** — **{summary['overall_rate']:.0%}** overall."
        )

        facts.append(
            f"💯 **{name}** had **{perfect_days_count(user_id, days)} perfect days** in the last **{days} days**."
        )

        facts.append(
            f"✅ **{name}** had **{valid_days_count(user_id, days)} valid days** in the last **{days} days**."
        )

        if best:
            facts.append(
                f"🏆 **{name}'s** strongest habit is **{best['name']}**: "
                f"**{best['completed']}/{best['days']}** — **{best['rate']:.0%}**."
            )

        if worst:
            facts.append(
                f"🧊 **{name}'s** weakest habit is **{worst['name']}**: "
                f"**{worst['completed']}/{worst['days']}** — **{worst['rate']:.0%}**. Suspicious behavior."
            )

        if dangerous_day:
            facts.append(
                f"⚠️ **{name}'s** most dangerous weekday is **{dangerous_day['weekday']}**: "
                f"failed **{dangerous_day['failed']}/{dangerous_day['total']}** times."
            )

        next_milestone = ((streak["current_streak"] // 5) + 1) * 5
        remaining = next_milestone - streak["current_streak"]

        facts.append(
            f"🎯 **{name}** is **{remaining} valid day(s)** away from a **{next_milestone}-day streak**."
        )

    if len(user_ids) >= 2:
        summaries = []

        for user_id in user_ids:
            name = await get_display_name(guild, user_id)
            summary = get_user_summary(user_id, days)

            summaries.append(
                {
                    "name": name,
                    "rate": summary["overall_rate"],
                    "completed": summary["total_completed"],
                    "possible": summary["total_possible"],
                }
            )

        leader = max(summaries, key=lambda x: x["rate"])

        facts.append(
            f"⚔️ Current **{days}-day leader** is **{leader['name']}** with "
            f"**{leader['completed']}/{leader['possible']}** habits completed — **{leader['rate']:.0%}**."
        )

    return random.choice(facts)


# ======================
# Discord UI
# ======================

def build_daily_embed(display_name: str, user_id: str, log_date: str):
    summary = daily_summary(user_id, log_date)

    embed = discord.Embed(
        title=f"{display_name}'s Daily Habits — {log_date}",
        color=discord.Color.green() if summary["valid_day"] else discord.Color.orange(),
    )

    if summary["total"] == 0:
        embed.description = "No habits added yet. Use `/addhabit` first."
        return embed

    lines = []

    for habit in summary["habits"]:
        mark = "✅" if habit["done"] else "⬜"
        lines.append(f"{mark} **{habit['name']}** · {habit['points']} pts")

    threshold = math.ceil(summary["total"] * VALID_DAY_RATE)
    needed = max(0, threshold - summary["completed"])

    status = "VALID DAY ✅" if summary["valid_day"] else f"NOT VALID YET ⚠️ — need {needed} more"

    embed.description = "\n".join(lines)

    embed.add_field(
        name="Progress",
        value=(
            f"{summary['completed']}/{summary['total']} — {summary['rate']:.0%}\n"
            f"{progress_bar(summary['rate'])}"
        ),
        inline=False,
    )

    embed.add_field(
        name="Score",
        value=f"{summary['score']} pts",
        inline=True,
    )

    embed.add_field(
        name="Status",
        value=status,
        inline=False,
    )

    return embed


class HabitButton(discord.ui.Button):
    def __init__(self, user_id: str, habit_id: int, habit_name: str, log_date: str, done: bool):
        self.user_id = user_id
        self.habit_id = habit_id
        self.habit_name = habit_name
        self.log_date = log_date

        label = f"{'✅' if done else '⬜'} {habit_name}"
        style = discord.ButtonStyle.success if done else discord.ButtonStyle.secondary

        super().__init__(
            label=label,
            style=style,
            custom_id=f"habit:{user_id}:{habit_id}:{log_date}",
        )

    async def callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "Nah, this is not your habit panel 😭",
                ephemeral=True,
            )
            return

        new_state = toggle_habit(self.user_id, self.habit_id, self.log_date)

        self.label = f"{'✅' if new_state else '⬜'} {self.habit_name}"
        self.style = discord.ButtonStyle.success if new_state else discord.ButtonStyle.secondary

        embed = build_daily_embed(
            interaction.user.display_name,
            self.user_id,
            self.log_date,
        )

        await interaction.response.edit_message(embed=embed, view=self.view)


class DailyHabitView(discord.ui.View):
    def __init__(self, user_id: str, log_date: str):
        super().__init__(timeout=None)

        habits = get_habits(user_id)

        # Discord allows max 25 buttons/components in one message.
        for habit in habits[:25]:
            done = is_completed(user_id, habit["id"], log_date)
            self.add_item(
                HabitButton(
                    user_id=user_id,
                    habit_id=habit["id"],
                    habit_name=habit["name"],
                    log_date=log_date,
                    done=done,
                )
            )


# ======================
# Bot setup
# ======================

class HabitBot(commands.Bot):
    async def setup_hook(self):
        init_db()

        for row in get_todays_daily_messages():
            self.add_view(
                DailyHabitView(row["user_id"], row["log_date"]),
                message_id=int(row["message_id"]),
            )

        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


intents = discord.Intents.default()

bot = HabitBot(
    command_prefix="!",
    intents=intents,
    proxy=DISCORD_PROXY,
)


# ======================
# Slash commands
# ======================

@bot.tree.command(name="addhabit", description="Add a habit to your daily panel.")
@app_commands.describe(name="Habit name", points="Points earned when completed")
async def addhabit(interaction: discord.Interaction, name: str, points: int = 10):
    if points < 1 or points > 100:
        await interaction.response.send_message(
            "Points should be between 1 and 100.",
            ephemeral=True,
        )
        return

    created = add_habit(str(interaction.user.id), name, points)

    if not created:
        await interaction.response.send_message(
            f"You already have an active habit called `{name}`.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"✅ Added habit: **{name}** · {points} pts",
        ephemeral=True,
    )


@bot.tree.command(name="removehabit", description="Remove one of your habits.")
@app_commands.describe(name="Habit name")
async def removehabit(interaction: discord.Interaction, name: str):
    removed = remove_habit(str(interaction.user.id), name)

    if not removed:
        await interaction.response.send_message(
            f"I couldn't find an active habit called `{name}`.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"🗑️ Removed habit: **{name}**",
        ephemeral=True,
    )


@bot.tree.command(name="panel", description="Post your daily habit button panel.")
async def panel(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    log_date = today_str()

    habits = get_habits(user_id)

    if not habits:
        await interaction.response.send_message(
            "You have no habits yet. Add one with `/addhabit`.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    embed = build_daily_embed(interaction.user.display_name, user_id, log_date)
    view = DailyHabitView(user_id, log_date)

    msg = await interaction.channel.send(
        content=interaction.user.mention,
        embed=embed,
        view=view,
    )

    save_daily_message(user_id, log_date, msg.channel.id, msg.id)

    await interaction.followup.send(
        "✅ Daily habit panel posted.",
        ephemeral=True,
    )


@bot.tree.command(name="today", description="See your current progress today.")
async def today(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    log_date = today_str()

    embed = build_daily_embed(interaction.user.display_name, user_id, log_date)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="stats", description="See your habit stats.")
@app_commands.describe(days="Number of days to analyze")
async def stats(interaction: discord.Interaction, days: int = 30):
    if days < 1 or days > 365:
        await interaction.response.send_message(
            "Choose between 1 and 365 days.",
            ephemeral=True,
        )
        return

    user_id = str(interaction.user.id)
    rows = stats_for_user(user_id, days)

    if not rows:
        await interaction.response.send_message(
            "No habits found. Add habits with `/addhabit`.",
            ephemeral=True,
        )
        return

    lines = []

    for row in rows:
        pct = row["percentage"] * 100
        lines.append(
            f"**{row['habit']}**: {row['completed']}/{row['days']} — {pct:.0f}%"
        )

    embed = discord.Embed(
        title=f"{interaction.user.display_name}'s Stats — Last {days} Days",
        description="\n".join(lines),
        color=discord.Color.blurple(),
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="dashboard", description="See a visual habit progress dashboard.")
@app_commands.describe(days="Number of days to analyze")
async def dashboard(interaction: discord.Interaction, days: int = 30):
    if days < 1 or days > 365:
        await interaction.response.send_message(
            "Choose between 1 and 365 days.",
            ephemeral=True,
        )
        return

    user_id = str(interaction.user.id)
    rows = stats_for_user(user_id, days)

    if not rows:
        await interaction.response.send_message(
            "No habits found. Add habits with `/addhabit` first.",
            ephemeral=True,
        )
        return

    total_completed = sum(row["completed"] for row in rows)
    total_possible = days * len(rows)
    overall_rate = total_completed / total_possible if total_possible else 0

    lines = [
        "**Overall**",
        f"{progress_bar(overall_rate)} **{overall_rate:.0%}**",
        f"`{total_completed}/{total_possible}` total habit completions",
        "",
    ]

    for row in rows:
        pct = row["percentage"]
        lines.append(f"**{row['habit']}**")
        lines.append(f"{progress_bar(pct)} `{row['completed']}/{row['days']}` — **{pct:.0%}**")
        lines.append("")

    embed = discord.Embed(
        title=f"{interaction.user.display_name}'s {days}-Day Dashboard",
        description="\n".join(lines),
        color=discord.Color.green() if overall_rate >= VALID_DAY_RATE else discord.Color.orange(),
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="table", description="Show your habit progress as a visual table.")
@app_commands.describe(days="Number of days to show")
async def table(interaction: discord.Interaction, days: int = 30):
    if days < 1 or days > 60:
        await interaction.response.send_message(
            "Choose between 1 and 60 days for the image table.",
            ephemeral=True,
        )
        return

    user_id = str(interaction.user.id)
    habits = get_habits(user_id)

    if not habits:
        await interaction.response.send_message(
            "No habits found. Add habits with `/addhabit` first.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()

    buffer = create_progress_table_image(
        interaction.user.display_name,
        user_id,
        days,
    )

    file = discord.File(
        buffer,
        filename=f"habit_table_{interaction.user.name}_{days}_days.png",
    )

    await interaction.followup.send(
        content=f"📈 **{interaction.user.display_name}'s last {days} days table**",
        file=file,
    )


@bot.tree.command(name="export", description="Export your habit progress as a CSV file.")
@app_commands.describe(days="Number of days to export")
async def export(interaction: discord.Interaction, days: int = 30):
    if days < 1 or days > 365:
        await interaction.response.send_message(
            "Choose between 1 and 365 days.",
            ephemeral=True,
        )
        return

    user_id = str(interaction.user.id)
    habits = get_habits(user_id)

    if not habits:
        await interaction.response.send_message(
            "No habits found. Add habits with `/addhabit` first.",
            ephemeral=True,
        )
        return

    csv_buffer = export_user_progress_csv(user_id, days)
    file_bytes = io.BytesIO(csv_buffer.getvalue().encode("utf-8-sig"))

    filename = f"habit_progress_{interaction.user.name}_{days}_days.csv"
    file = discord.File(file_bytes, filename=filename)

    await interaction.response.send_message(
        content=f"📊 Here is your last **{days} days** habit progress table.",
        file=file,
    )


@bot.tree.command(name="compare", description="Compare your progress with another user.")
@app_commands.describe(user="The user to compare with", days="Number of days to compare")
async def compare(interaction: discord.Interaction, user: discord.Member, days: int = 30):
    if days < 7 or days > 90:
        await interaction.response.send_message(
            "Choose between 7 and 90 days.",
            ephemeral=True,
        )
        return

    user_id_1 = str(interaction.user.id)
    user_id_2 = str(user.id)

    if user_id_1 == user_id_2:
        await interaction.response.send_message(
            "Bro you can't compare yourself with yourself 😭",
            ephemeral=True,
        )
        return

    if not get_habits(user_id_1):
        await interaction.response.send_message(
            "You have no habits yet. Add some with `/addhabit` first.",
            ephemeral=True,
        )
        return

    if not get_habits(user_id_2):
        await interaction.response.send_message(
            f"{user.display_name} has no habits yet.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()

    buffer = create_compare_dashboard_image(
        interaction.user.display_name,
        user_id_1,
        user.display_name,
        user_id_2,
        days,
    )

    file = discord.File(buffer, filename=f"compare_{interaction.user.name}_{user.name}_{days}.png")

    await interaction.followup.send(
        content=f"⚔️ **{interaction.user.display_name} vs {user.display_name}**",
        file=file,
    )


@bot.tree.command(name="heatmap", description="Show a GitHub-style consistency heatmap.")
@app_commands.describe(days="Number of days to show")
async def heatmap(interaction: discord.Interaction, days: int = 90):
    if days < 7 or days > 180:
        await interaction.response.send_message(
            "Choose between 7 and 180 days.",
            ephemeral=True,
        )
        return

    user_id = str(interaction.user.id)

    if not get_habits(user_id):
        await interaction.response.send_message(
            "No habits found. Add habits with `/addhabit` first.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()

    buffer = create_heatmap_image(
        interaction.user.display_name,
        user_id,
        days,
    )

    file = discord.File(buffer, filename=f"heatmap_{interaction.user.name}_{days}.png")

    await interaction.followup.send(
        content=f"🔥 **{interaction.user.display_name}'s consistency heatmap**",
        file=file,
    )


@bot.tree.command(name="streak", description="Show your current and best streak.")
@app_commands.describe(user="Optional user to check")
async def streak(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    user_id = str(target.id)

    if not get_habits(user_id):
        await interaction.response.send_message(
            f"{target.display_name} has no habits yet.",
            ephemeral=True,
        )
        return

    streak_info = get_streak_info(user_id, 365)
    today_summary = daily_summary(user_id, today_str())

    status = "Alive 🔥" if today_summary["valid_day"] else "Not safe yet ⚠️"

    embed = discord.Embed(
        title=f"🔥 {target.display_name}'s Streak",
        color=discord.Color.orange(),
    )

    embed.add_field(
        name="Current Streak",
        value=f"**{streak_info['current_streak']}** days",
        inline=True,
    )

    embed.add_field(
        name="Best Streak",
        value=f"**{streak_info['best_streak']}** days",
        inline=True,
    )

    embed.add_field(
        name="Today",
        value=(
            f"{today_summary['completed']}/{today_summary['total']} habits — "
            f"{today_summary['rate']:.0%}\n"
            f"Status: **{status}**"
        ),
        inline=False,
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="fact", description="Post a random habit fact.")
@app_commands.describe(days="Number of days to analyze")
async def fact(interaction: discord.Interaction, days: int = 30):
    if days < 7 or days > 180:
        await interaction.response.send_message(
            "Choose between 7 and 180 days.",
            ephemeral=True,
        )
        return

    fact_text = await generate_random_fact(interaction.guild, days=days)

    embed = discord.Embed(
        title="🎲 Random Habit Fact",
        description=fact_text,
        color=discord.Color.blurple(),
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="report", description="Post the daily progress report manually.")
@app_commands.describe(days="Number of days to show")
async def report(interaction: discord.Interaction, days: int = 30):
    if days < 1 or days > 60:
        await interaction.response.send_message(
            "Choose between 1 and 60 days for the report.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    await post_daily_progress_report(interaction.channel, days=days, force=True)

    await interaction.followup.send(
        "✅ Progress report posted.",
        ephemeral=True,
    )


@bot.tree.command(name="streakreport", description="Post the streak report manually.")
async def streakreport(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await post_daily_streak_report(interaction.channel, force=True)
    await interaction.followup.send("✅ Streak report posted.", ephemeral=True)


@bot.tree.command(name="factreport", description="Post the random fact manually.")
@app_commands.describe(days="Number of days to analyze")
async def factreport(interaction: discord.Interaction, days: int = 30):
    if days < 7 or days > 180:
        await interaction.response.send_message(
            "Choose between 7 and 180 days.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    await post_daily_random_fact(interaction.channel, days=days, force=True)
    await interaction.followup.send("✅ Random fact posted.", ephemeral=True)


# ======================
# Auto daily panel/report/streak/fact
# ======================

async def post_daily_panel_for_user(channel: discord.TextChannel, user_id: str):
    log_date = today_str()

    if daily_message_exists(user_id, log_date):
        return

    habits = get_habits(user_id)

    if not habits:
        return

    try:
        member = channel.guild.get_member(int(user_id)) or await channel.guild.fetch_member(int(user_id))
        display_name = member.display_name
        mention = member.mention
    except Exception:
        display_name = f"User {user_id}"
        mention = f"<@{user_id}>"

    embed = build_daily_embed(display_name, user_id, log_date)
    view = DailyHabitView(user_id, log_date)

    msg = await channel.send(
        content=mention,
        embed=embed,
        view=view,
    )

    save_daily_message(user_id, log_date, msg.channel.id, msg.id)


async def post_daily_progress_report(
    channel: discord.TextChannel,
    days: int = 30,
    force: bool = False,
):
    report_date = today_str()

    if not force and report_already_posted(report_date):
        return

    user_ids = get_active_user_ids()

    if not user_ids:
        await channel.send("No active users/habits found yet.")
        return

    await channel.send(f"📊 **Daily Progress Report — {report_date}**")

    for user_id in user_ids:
        habits = get_habits(user_id)

        if not habits:
            continue

        try:
            member = channel.guild.get_member(int(user_id)) or await channel.guild.fetch_member(int(user_id))
            display_name = member.display_name
            username = member.name
        except Exception:
            display_name = f"User {user_id}"
            username = f"user_{user_id}"

        buffer = create_progress_table_image(
            display_name,
            user_id,
            days,
        )

        file = discord.File(
            buffer,
            filename=f"habit_table_{username}_{days}_days.png",
        )

        await channel.send(
            content=f"📈 **{display_name}'s last {days} days**",
            file=file,
        )

    if not force:
        mark_report_posted(report_date, channel.id)


async def post_daily_streak_report(
    channel: discord.TextChannel,
    force: bool = False,
):
    post_date = today_str()

    if not force and streak_post_already_posted(post_date):
        return

    user_ids = get_active_user_ids()

    if not user_ids:
        await channel.send("No active users/habits found yet.")
        return

    lines = []

    for user_id in user_ids:
        name = await get_display_name(channel.guild, user_id)
        streak_info = get_streak_info(user_id, 365)
        today_summary = daily_summary(user_id, post_date)

        if today_summary["valid_day"]:
            status = "alive 🔥"
        else:
            needed_total = math.ceil(today_summary["total"] * VALID_DAY_RATE)
            needed = max(0, needed_total - today_summary["completed"])
            status = f"not safe yet — needs {needed} more ⚠️"

        lines.append(
            f"**{name}** — Current: **{streak_info['current_streak']}** | "
            f"Best: **{streak_info['best_streak']}** | Today: **{status}**"
        )

    embed = discord.Embed(
        title=f"🔥 Daily Streak Check — {post_date}",
        description="\n".join(lines),
        color=discord.Color.orange(),
    )

    await channel.send(embed=embed)

    if not force:
        mark_streak_posted(post_date, channel.id)


async def post_daily_random_fact(
    channel: discord.TextChannel,
    days: int = 30,
    force: bool = False,
):
    post_date = today_str()

    if not force and fact_post_already_posted(post_date):
        return

    fact_text = await generate_random_fact(channel.guild, days=days)

    embed = discord.Embed(
        title="🎲 Daily Random Habit Fact",
        description=fact_text,
        color=discord.Color.blurple(),
    )

    await channel.send(embed=embed)

    if not force:
        mark_fact_posted(post_date, channel.id)


@tasks.loop(minutes=1)
async def morning_panels():
    now = datetime.now(TZ)

    # Posts once daily at 09:00 according to TZ.
    if now.hour != 9 or now.minute != 0:
        return

    if not HABIT_CHANNEL_ID:
        return

    channel = bot.get_channel(HABIT_CHANNEL_ID)

    if channel is None:
        return

    for user_id in get_active_user_ids():
        await post_daily_panel_for_user(channel, user_id)


@tasks.loop(minutes=1)
async def daily_random_fact():
    now = datetime.now(TZ)

    # Game loading-screen fact at noon.
    if now.hour != 12 or now.minute != 0:
        return

    channel_id = FACTS_CHANNEL_ID or REPORT_CHANNEL_ID

    if not channel_id:
        return

    channel = bot.get_channel(channel_id)

    if channel is None:
        return

    await post_daily_random_fact(channel, days=30, force=False)


@tasks.loop(minutes=1)
async def daily_streak_report():
    now = datetime.now(TZ)

    # Warning before close. Gives you 29 minutes to save the day.
    if now.hour != 23 or now.minute != 30:
        return

    channel_id = STREAK_CHANNEL_ID or REPORT_CHANNEL_ID

    if not channel_id:
        return

    channel = bot.get_channel(channel_id)

    if channel is None:
        return

    await post_daily_streak_report(channel, force=False)


@tasks.loop(minutes=1)
async def daily_progress_report():
    now = datetime.now(TZ)

    # Final daily visual report.
    if now.hour != 23 or now.minute != 59:
        return

    if not REPORT_CHANNEL_ID:
        return

    channel = bot.get_channel(REPORT_CHANNEL_ID)

    if channel is None:
        return

    await post_daily_progress_report(channel, days=30, force=False)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    if not morning_panels.is_running():
        morning_panels.start()

    if not daily_random_fact.is_running():
        daily_random_fact.start()

    if not daily_streak_report.is_running():
        daily_streak_report.start()

    if not daily_progress_report.is_running():
        daily_progress_report.start()


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing in .env")

bot.run(TOKEN)
