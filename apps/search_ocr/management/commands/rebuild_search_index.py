from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.search_ocr.search_index import rebuild_document_search_index


class Command(BaseCommand):
    help = "Cold rebuild persisted Smart Search index; intended for Stage 4 acceptance measurements."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--require-count", type=int)
        parser.add_argument("--max-seconds", type=int, default=3600)
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        try:
            result = rebuild_document_search_index(
                batch_size=options["batch_size"],
                require_count=options.get("require_count"),
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        elapsed = float(result["elapsed_seconds"])
        max_seconds = options["max_seconds"]
        payload = {
            "documents": int(result["documents"]),
            "elapsed_seconds": round(elapsed, 3),
            "max_seconds": max_seconds,
            "result": "PASS" if elapsed <= max_seconds else "FAIL",
        }
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write(
                f"documents={payload['documents']} elapsed_seconds={payload['elapsed_seconds']} "
                f"limit_seconds={max_seconds} result={payload['result']}"
            )
        if payload["result"] != "PASS":
            raise CommandError(
                f"cold reindex exceeded acceptance limit: {elapsed:.3f}s > {max_seconds}s"
            )
