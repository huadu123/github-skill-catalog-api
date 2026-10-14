#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


API_URL = "https://api.github.com/search/repositories"
API_VERSION = "2026-03-10"
PER_PAGE = 100
MAX_RESULTS_PER_QUERY = 1000
USER_AGENT = "Codex-GitHub-Skill-Catalog-Batch"


def parse_time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def isoformat(value):
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class GitHubSearch:
    def __init__(self, token):
        self.token = token
        self.last_request = 0.0
        self.min_interval = 2.1 if token else 6.1
        self.query_count = 0

    def fetch_page(self, query, page):
        params = urllib.parse.urlencode({"q": query, "sort": "updated", "order": "desc", "per_page": PER_PAGE, "page": page})
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        for attempt in range(5):
            wait = self.min_interval - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            request = urllib.request.Request(f"{API_URL}?{params}", headers=headers)
            self.last_request = time.monotonic()
            self.query_count += 1
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                try:
                    message = json.loads(body).get("message", body)
                except json.JSONDecodeError:
                    message = body
                if exc.code in (403, 429):
                    retry_after = exc.headers.get("Retry-After")
                    reset_at = exc.headers.get("X-RateLimit-Reset")
                    if retry_after:
                        delay = max(1, int(retry_after))
                    elif reset_at and exc.headers.get("X-RateLimit-Remaining") == "0":
                        delay = max(1, int(reset_at) - int(time.time()) + 1)
                    else:
                        delay = min(60, 5 * (attempt + 1))
                    if attempt < 4:
                        print(f"GitHub rate limit response; waiting {delay}s before retry.", file=sys.stderr, flush=True)
                        time.sleep(delay)
                        continue
                elif exc.code >= 500 and attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"GitHub API returned HTTP {exc.code}: {message}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"GitHub request failed: {exc}") from exc
        raise RuntimeError("GitHub request failed after retries")


def repository_record(item):
    license_info = item.get("license") or {}
    return {
        "id": item.get("id"),
        "full_name": item.get("full_name"),
        "html_url": item.get("html_url"),
        "description": item.get("description"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "pushed_at": item.get("pushed_at"),
        "stars": item.get("stargazers_count"),
        "forks": item.get("forks_count"),
        "language": item.get("language"),
        "license": license_info.get("spdx_id"),
        "topics": item.get("topics", []),
        "default_branch": item.get("default_branch"),
        "archived": item.get("archived"),
    }


def in_window(item, start, end):
    pushed_at = item.get("pushed_at")
    if not pushed_at:
        return False
    pushed = datetime.fromisoformat(pushed_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    return start <= pushed < end


def collect_interval(client, start, end, records, counts):
    query = f"topic:agent-skills pushed:{isoformat(start)}..{isoformat(end)}"
    first = client.fetch_page(query, 1)
    total = int(first.get("total_count", 0))
    if first.get("incomplete_results") or total > MAX_RESULTS_PER_QUERY:
        span = int((end - start).total_seconds())
        if span < 2:
            raise RuntimeError(
                f"GitHub cannot return a complete result for the {isoformat(start)} to {isoformat(end)} interval."
            )
        midpoint = start + timedelta(seconds=span // 2)
        collect_interval(client, start, midpoint, records, counts)
        collect_interval(client, midpoint, end, records, counts)
        return

    counts["reported_matches"] += total
    pages = (total + PER_PAGE - 1) // PER_PAGE
    items = list(first.get("items", []))
    for page in range(2, pages + 1):
        result = client.fetch_page(query, page)
        if result.get("incomplete_results"):
            raise RuntimeError(f"GitHub returned incomplete page data for query: {query}")
        items.extend(result.get("items", []))

    for item in items:
        if not in_window(item, start, end):
            continue
        record = repository_record(item)
        key = record.get("id") or record.get("full_name")
        current = records.get(key)
        if current is None or (record.get("pushed_at") or "") > (current.get("pushed_at") or ""):
            records[key] = record


def main():
    parser = argparse.ArgumentParser(description="Collect public GitHub repositories tagged agent-skills with recent push activity.")
    parser.add_argument("--since", type=parse_time, help="UTC or timezone-aware start timestamp; defaults to 24 hours ago")
    parser.add_argument("--until", type=parse_time, help="UTC or timezone-aware end timestamp; defaults to now")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "daily")
    args = parser.parse_args()

    end = args.until or datetime.now(timezone.utc).replace(microsecond=0)
    start = args.since or end - timedelta(days=1)
    if start >= end:
        parser.error("--since must be earlier than --until")

    client = GitHubSearch(os.environ.get("GITHUB_TOKEN"))
    records = {}
    counts = {"reported_matches": 0}
    collect_interval(client, start, end, records, counts)

    repositories = sorted(
        records.values(),
        key=lambda item: (item.get("pushed_at") or "", item.get("full_name") or ""),
        reverse=True,
    )
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0)
    payload = {
        "schema_version": 1,
        "fetched_at": isoformat(fetched_at),
        "source": API_URL,
        "query": "topic:agent-skills pushed within the time window; local filter uses [window_start, window_end)",
        "window_start": isoformat(start),
        "window_end": isoformat(end),
        "reported_matches_across_inclusive_partitions": counts["reported_matches"],
        "returned_count": len(repositories),
        "request_count": client.query_count,
        "repositories": repositories,
        "note": "Public repository metadata only. pushed_at indicates repository activity, not a confirmed SKILL.md change.",
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"skill-repos-{fetched_at.astimezone().strftime('%Y-%m-%d')}.json"
    output = args.output_dir / filename
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({"path": str(output), "returned_count": len(repositories), "window_start": payload["window_start"], "window_end": payload["window_end"], "request_count": client.query_count}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as exc:
        print(f"Collection failed: {exc}", file=sys.stderr)
        sys.exit(1)
