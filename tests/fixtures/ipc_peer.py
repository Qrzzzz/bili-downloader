"""Transport fault peer, used only by the .NET test executable."""

import json
import sys


scenario = sys.argv[1]
operation_count = 0
accepted: list[str] = []


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
    write(
        {"v": 1, "type": "event", "operation_id": operation, "seq": seq, "event": name, "data": data},
        scenario == "fragmented",
    )


def terminal_data(method, result):
    return {"method": method, "result": result}


for line in sys.stdin.buffer:
    request = json.loads(line)
    method = request["method"]
    if method == "shutdown":
        write({"v": 1, "type": "response", "id": request["id"], "ok": True, "result": {"state": "draining"}})
        break
    if scenario == "unacknowledged":
        continue
    if scenario == "partial":
        sys.stdout.buffer.write(b'{"v":1')
        break

    operation_count += 1
    operation = "op" if scenario in {"fragmented", "reuse_operation_id"} else f"op-{operation_count}"
    reply = {"v": 1, "type": "response", "id": request["id"], "ok": True, "result": {"operation_id": operation}}
    if scenario == "wrong_version":
        reply["v"] = 99
    if scenario == "malformed":
        reply["result"] = {}
    if scenario == "accepted_and_pending_malformed" and method == "hold":
        event(1, "operation.completed", {"method": "work"}, accepted[0])
        continue
    write(reply, scenario == "fragmented")
    if scenario in {"wrong_version", "malformed"}:
        continue

    accepted.append(operation)
    if scenario in {"accepted_eof", "connection_disconnect"}:
        break
    if scenario in {"shutdown_with_accepted", "accepted_and_pending_malformed"}:
        continue
    if scenario == "multiple_accepted_malformed":
        if len(accepted) == 2:
            event(1, "operation.completed", {"method": "work"}, accepted[0])
        continue
    if scenario == "cancelled":
        event(1, "operation.cancelled", terminal_data(method, {}), operation)
        continue
    if scenario == "normal_failed":
        event(
            1,
            "operation.failed",
            terminal_data(
                method,
                {"error": {"code": "fixture", "message": "fixture failure", "retryable": False, "detail": ""}},
            ),
            operation,
        )
        continue
    if scenario == "terminal_completed_missing_result":
        event(1, "operation.completed", {"method": method}, operation)
        continue
    if scenario == "terminal_completed_wrong_result_type":
        event(1, "operation.completed", terminal_data(method, "wrong"), operation)
        continue
    if scenario == "terminal_failed_missing_error":
        event(1, "operation.failed", terminal_data(method, {}), operation)
        continue
    if scenario == "terminal_failed_wrong_error_type":
        event(1, "operation.failed", terminal_data(method, {"error": "wrong"}), operation)
        continue
    if scenario == "terminal_failed_wrong_error_field_type":
        event(
            1,
            "operation.failed",
            terminal_data(
                method,
                {"error": {"code": "fixture", "message": "failure", "retryable": "no", "detail": ""}},
            ),
            operation,
        )
        continue
    if scenario == "terminal_cancelled_missing_result":
        event(1, "operation.cancelled", {"method": method}, operation)
        continue
    if scenario == "terminal_cancelled_wrong_result_type":
        event(1, "operation.cancelled", terminal_data(method, []), operation)
        continue
    if scenario == "invalid_seq":
        event(0, "operation.completed", terminal_data(method, {}), operation)
        continue
    if scenario == "duplicate_terminal":
        event(1, "operation.completed", terminal_data(method, {"title": "once"}), operation)
        event(2, "operation.completed", terminal_data(method, {"title": "twice"}), operation)
        event(3, "download.progress", {}, operation)
        continue
    if scenario == "reuse_operation_id":
        event(1, "operation.completed", terminal_data(method, {"ordinal": operation_count}), operation)
        continue

    event(1, "download.progress", {}, operation)
    if scenario == "bad_sequence":
        event(1, "download.progress", {}, operation)
    else:
        event(1, "download.progress", {}, "stale")
        event(2, "operation.completed", terminal_data(method, {"title": "中文分 P"}), operation)
        event(3, "download.progress", {}, operation)
