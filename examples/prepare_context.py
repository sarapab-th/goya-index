"""Prepare context over the synthetic example; no model/network calls."""

import json
from pathlib import Path
import tempfile

from goya_index import build, context


def main():
    source = Path(__file__).resolve().parent / "source"
    with tempfile.TemporaryDirectory(prefix="goya-index-example-") as temporary:
        data = Path(temporary) / "catalog"
        build(source, data)
        result = context(data, "calculate_stock_level", max_chars=2000)
        if not result["candidates"]:
            raise RuntimeError("No verified source was available")
        print(json.dumps({
            "packet": result["packet"],
            "packet_chars": result["packet_chars"],
            "packet_sha256": result["packet_sha256"],
            "source_paths": [row["source_path"] for row in result["candidates"]],
            "model_called": False,
        }, indent=2))


if __name__ == "__main__":
    main()
