"""Transport fault peer, used only by the .NET test executable."""
import json
import sys

scenario = sys.argv[1]

def write(message, fragmented=False):
    raw = (json.dumps(message, ensure_ascii=False) + "\n").encode()
    if fragmented:
        for byte in raw:
            sys.stdout.buffer.write(bytes([byte]))
            sys.stdout.buffer.flush()
    else:
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()

def event(seq, name, data, operation="op"):
    write({"v": 1, "type": "event", "operation_id": operation, "seq": seq, "event": name, "data": data}, scenario == "fragmented")

for line in sys.stdin.buffer:
    request = json.loads(line)
    reply = {"v": 1, "type": "response", "id": request["id"], "ok": True, "result": {"operation_id": "op"}}
    if request["method"] == "shutdown":
        write(reply)
        break
    if scenario == "unacknowledged":
        continue
    if scenario == "partial":
        sys.stdout.buffer.write(b'{"v":1')
        break
    if scenario == "wrong_version":
        reply["v"] = 99
    if scenario == "malformed":
        reply["result"] = {}
    write(reply, scenario == "fragmented")
    if scenario in {"wrong_version", "malformed"}:
        continue
    if scenario == "cancelled":
        event(1, "operation.cancelled", {"result": {}})
        continue
    event(1, "download.progress", {})
    if scenario == "bad_sequence":
        event(1, "download.progress", {})
    else:
        event(1, "download.progress", {}, "stale")
        event(2, "operation.completed", {"result": {"title": "中文分 P"}})
        event(3, "download.progress", {})
