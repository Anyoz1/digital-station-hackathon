import json
from pathlib import Path

from digital_station.api import create_app


def main():
    target = Path(__file__).resolve().parents[1] / "contracts/openapi.json"
    target.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n")
    print("Exported actual implemented OpenAPI (v1.0)")


if __name__ == "__main__":
    main()
