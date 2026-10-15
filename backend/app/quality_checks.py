import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import io
import re

RULE_VERSION = "fields-files-time-v2"
MAX_SEQUENCE_BYTES = 16 * 1024 * 1024
MAX_SEQUENCE_ROWS = 100000
MAX_ISSUES = 200
INCOMPLETE_CODES = {"sequence_limit", "issue_limit", "file_changed"}


class IssueLimit(Exception):
    pass


class Issues(list):
    def add(self, code, field, asset_id=None, row=None):
        if len(self) >= MAX_ISSUES:
            self.append({"code": "issue_limit", "field": "checks", "asset_id": None})
            raise IssueLimit()
        item = {"code": code, "field": field, "asset_id": asset_id}
        if row is not None:
            item["row"] = row
        self.append(item)


def record_duration(episode: dict, issues: Issues) -> Decimal | None:
    times = {}
    for field in ("started_at", "ended_at"):
        value = episode[field]
        if value is None or not str(value).strip():
            continue  # Missing values are already reported by the required-field checks.
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError()
            times[field] = parsed.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            issues.add("invalid_datetime", field)
    if len(times) != 2:
        return None
    delta = times["ended_at"] - times["started_at"]
    duration = Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000
    if duration <= 0:
        issues.add("invalid_time_range", "ended_at")
        return None
    return duration


def check_sequence(stream, duration: Decimal | None, asset_id: str, issues: Issues):
    data = stream.read(MAX_SEQUENCE_BYTES + 1)
    if len(data) > MAX_SEQUENCE_BYTES:
        issues.add("sequence_limit", "timestamp_s", asset_id)
        return
    try:
        reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True)
        columns = next(reader, [])
        if (not columns or len(columns) > 64 or any(not value.strip() for value in columns)
                or len(set(columns)) != len(columns)):
            issues.add("invalid_csv", "assets", asset_id)
            return
        if "timestamp_s" not in columns:
            issues.add("missing_timestamp", "timestamp_s", asset_id)
            return
        position = columns.index("timestamp_s")
        previous = None
        count = 0
        for row in reader:
            if not row:
                continue
            count += 1
            if count > MAX_SEQUENCE_ROWS:
                issues.add("sequence_limit", "timestamp_s", asset_id, reader.line_num)
                return
            if len(row) != len(columns):
                issues.add("invalid_csv", "assets", asset_id, reader.line_num)
                continue
            text = row[position].strip()
            try:
                if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text):
                    raise InvalidOperation()
                value = Decimal(text)
                if not value.is_finite():
                    raise InvalidOperation()
            except InvalidOperation:
                issues.add("invalid_timestamp", "timestamp_s", asset_id, reader.line_num)
                continue
            if value < 0:
                issues.add("negative_timestamp", "timestamp_s", asset_id, reader.line_num)
            if previous is not None and value <= previous:
                issues.add("timestamp_not_increasing", "timestamp_s", asset_id, reader.line_num)
            if duration is not None and value > duration:
                issues.add("timestamp_out_of_range", "timestamp_s", asset_id, reader.line_num)
            previous = value
        if not count:
            issues.add("empty_sequence", "timestamp_s", asset_id)
    except (UnicodeError, csv.Error):
        issues.add("invalid_csv", "assets", asset_id)
