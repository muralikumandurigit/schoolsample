# app/start_mcp.py
import json
import sys
from pathlib import Path
from app.generic_mcp_server_di import serve

def main():
    if len(sys.argv) < 2:
        print("Usage: python app/start_mcp.py /path/to/school_tools_spec.json")
        sys.exit(1)

    spec_path = Path(sys.argv[1])
    if not spec_path.exists():
        print(f"Spec file not found: {spec_path}")
        sys.exit(1)

    with open(spec_path, "r", encoding="utf-8") as fh:
        spec = json.load(fh)

    host = spec.get("config", {}).get("host", "0.0.0.0")
    port = spec.get("config", {}).get("port", 8765)
    serve(spec, host=host, port=port)

if __name__ == "__main__":
    main()
