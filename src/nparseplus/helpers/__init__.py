import math
import os
import sys
from datetime import timedelta


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS  # pylint: disable=E1101,W0212
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


def to_range(number, min_number, max_number):
    """Returns number of within min/max, else min/max."""
    return min(max_number, max(min_number, number))


def to_real_xy(x, y):
    """Convert Everquest 'x, y' to standard 'x, y'."""
    return -y, -x


def to_eq_xy(x, y):
    """Convert standard x, y to Everquest x, y."""
    return -y, -x


def get_degrees_from_line(x1, y1, x2, y2):
    return -math.degrees(math.atan2((x2 - x1), (y2 - y1)))


def format_time(time_delta):
    """Returns a string from a timedelta '#d #h #m #s', but only 's' if d, h, m are all 0."""
    time_string = ""
    days = time_delta.days
    hours, remainder = divmod(time_delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if sum([days, hours, minutes]):
        time_string += f"{days}d" if days else ""
        time_string += f"{hours}h" if hours else ""
        time_string += f"{minutes}m" if minutes else ""
        time_string += f"{seconds}s" if seconds else ""
        return time_string
    return str(seconds)


def text_time_to_seconds(text_time):
    """Returns string 'hh:mm:ss' -> seconds"""
    parts = text_time.split(":")
    seconds, minutes, hours = 0, 0, 0
    try:
        seconds = int(parts[-1])
        minutes = int(parts[-2])
        hours = int(parts[-3])
    except IndexError:
        pass
    except ValueError:
        pass

    return timedelta(hours=hours, minutes=minutes, seconds=seconds).total_seconds()
