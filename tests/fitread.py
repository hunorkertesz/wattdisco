"""Read a .fit file back with fit-tool, for tests: data messages grouped by name ("record", "lap", ...)."""

from collections import defaultdict

from fit_tool import FitFile
from fit_tool.data_message import DataMessage


def read_fit(data: bytes) -> dict[str, list]:
    messages = defaultdict(list)
    for record in FitFile.from_bytes(data).records:  # checks header and CRC, raises on a broken file
        if isinstance(record.message, DataMessage):
            messages[record.message.name].append(record.message)
    return messages
