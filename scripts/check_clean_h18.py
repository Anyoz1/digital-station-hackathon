"""H18 clean migration into an additional DB, previous databases preserved."""

import argparse
import contextlib
import io
import json
import re
from pathlib import Path

import check_clean_h12

check_clean_h12.TARGET = "digital_station_h18_clean_test"

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=check_clean_h12.TARGET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"digital_station_h18_clean_[a-z0-9_]{1,30}", args.database):
        parser.error("Only additive H18 clean-test database names are permitted")
    check_clean_h12.TARGET = args.database
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        check_clean_h12.main()
    result = json.loads(captured.getvalue())
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(serialized + "\n")
    print(serialized)
