import argparse
import copy
import csv
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_api_workflow(path):
    workflow_path = Path(path)
    with workflow_path.open("r", encoding="utf-8") as f:
        workflow = json.load(f)

    if not isinstance(workflow, dict):
        raise ValueError("Workflow JSON must be an object.")

    if "nodes" in workflow or "links" in workflow:
        raise ValueError(
            "This is a normal canvas workflow, not API format. "
            "In ComfyUI enable Dev mode, then use Save API Format."
        )

    for node_id, node in workflow.items():
        if not isinstance(node, dict) or "class_type" not in node or "inputs" not in node:
            raise ValueError(f"Node {node_id!r} does not look like API workflow format.")

    return workflow


def parse_value(value):
    text = str(value)
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def apply_set(prompt, assignment):
    try:
        left, value = assignment.split("=", 1)
        node_id, input_name = left.split(".", 1)
    except ValueError as exc:
        raise ValueError(f"Invalid --set {assignment!r}. Use node_id.input_name=value") from exc

    if node_id not in prompt:
        raise KeyError(f"Node id {node_id!r} not found.")

    prompt[node_id].setdefault("inputs", {})[input_name] = parse_value(value)


def patch_prompt(prompt, args, run_index):
    patched = copy.deepcopy(prompt)

    for assignment in args.set:
        apply_set(patched, assignment)

    if args.loader_node:
        if args.loader_node not in patched:
            raise KeyError(f"Loader node id {args.loader_node!r} not found.")
        loader_inputs = patched[args.loader_node].setdefault("inputs", {})
        loader_inputs[args.index_input] = args.start + run_index
        if args.input and args.directory_input:
            loader_inputs[args.directory_input] = args.input
        if args.filetype_input:
            loader_inputs[args.filetype_input] = args.filetype
        if args.include_subfolders_input:
            loader_inputs[args.include_subfolders_input] = args.include_subfolders

    if args.save_node and args.output:
        if args.save_node not in patched:
            raise KeyError(f"Save node id {args.save_node!r} not found.")
        save_inputs = patched[args.save_node].setdefault("inputs", {})
        if args.output_input:
            save_inputs[args.output_input] = args.output
        if args.filename_prefix_input:
            save_inputs[args.filename_prefix_input] = f"{args.filename_prefix}_{args.start + run_index:05d}"

    return patched


def wait_for_prompt(server, prompt_id, poll_seconds):
    history_url = f"{server}/history/{prompt_id}"
    while True:
        history = get_json(history_url)
        if prompt_id in history:
            item = history[prompt_id]
            status = item.get("status", {})
            if status.get("completed"):
                messages = status.get("messages", [])
                errors = [
                    m for m in messages
                    if isinstance(m, list) and len(m) > 0 and m[0] == "execution_error"
                ]
                if errors:
                    return False, json.dumps(errors[-1], ensure_ascii=False)
                return True, "completed"
            return False, json.dumps(status, ensure_ascii=False)
        time.sleep(poll_seconds)


def main():
    parser = argparse.ArgumentParser(
        description="Run any ComfyUI API-format workflow, optionally in a loop."
    )
    parser.add_argument("--workflow", required=True, help="ComfyUI API-format workflow JSON.")
    parser.add_argument("--server", default="http://127.0.0.1:8188")
    parser.add_argument("--runs", type=int, default=1, help="How many times to submit the workflow.")
    parser.add_argument("--start", type=int, default=0, help="Start index for batch loaders.")
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument("--logs", default=r"D:\soft\obsidian--ai\day28_workflows\logs")

    parser.add_argument(
        "--set",
        action="append",
        default=[],
        help="Patch any input. Format: node_id.input_name=value. Can be used multiple times.",
    )

    parser.add_argument("--loader-node", help="Optional node id to patch as a folder/index loader.")
    parser.add_argument("--directory-input", default="directory")
    parser.add_argument("--index-input", default="index")
    parser.add_argument("--filetype-input", default="filetype")
    parser.add_argument("--include-subfolders-input", default="include_subfolders")
    parser.add_argument("--input", help="Input folder path for loader node.")
    parser.add_argument("--filetype", default="all")
    parser.add_argument("--include-subfolders", action="store_true")

    parser.add_argument("--save-node", help="Optional node id to patch as an output/save node.")
    parser.add_argument("--output-input", default="output_path")
    parser.add_argument("--filename-prefix-input", default="filename_prefix")
    parser.add_argument("--output", help="Output folder path for save node.")
    parser.add_argument("--filename-prefix", default="batch")

    args = parser.parse_args()

    workflow = load_api_workflow(args.workflow)
    log_dir = Path(args.logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"run_any_workflow_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    client_id = str(uuid.uuid4())

    with log_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["run", "status", "prompt_id", "message"])
        writer.writeheader()

        for run_index in range(args.runs):
            prompt_id = ""
            try:
                prompt = patch_prompt(workflow, args, run_index)
                response = post_json(f"{args.server}/prompt", {"prompt": prompt, "client_id": client_id})
                prompt_id = response["prompt_id"]
                ok, message = wait_for_prompt(args.server, prompt_id, args.poll)
                writer.writerow({
                    "run": args.start + run_index,
                    "status": "success" if ok else "failed",
                    "prompt_id": prompt_id,
                    "message": message,
                })
                print(f"[{'OK' if ok else 'FAIL'}] run={args.start + run_index} prompt_id={prompt_id}")
            except (ValueError, KeyError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
                writer.writerow({
                    "run": args.start + run_index,
                    "status": "failed",
                    "prompt_id": prompt_id,
                    "message": repr(exc),
                })
                print(f"[FAIL] run={args.start + run_index} -> {exc}")

    print(f"Log saved to: {log_path}")


if __name__ == "__main__":
    main()
