from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from apps.search_ocr.search_index import rebuild_document_search_index


class Command(BaseCommand):
    help = (
        "Rebuild persisted Smart Search index. Default is online UPSERT; "
        "--cold truncates the read model first and is intended for acceptance only."
    )

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--require-count", type=int)
        parser.add_argument("--max-seconds", type=int, default=3600)
        parser.add_argument(
            "--cold",
            action="store_true",
            help="Destructive acceptance mode: start from an empty search read model.",
        )
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        if options["cold"] and options.get("require_count") is None:
            raise CommandError("--cold requires --require-count")
        try:
            result = rebuild_document_search_index(
                batch_size=options["batch_size"],
                require_count=options.get("require_count"),
                cold=options["cold"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        elapsed = float(result["elapsed_seconds"])
        max_seconds = options["max_seconds"]
        payload = {
            "mode": result["mode"],
            "documents": int(result["documents"]),
            "elapsed_seconds": round(elapsed, 3),
            "max_seconds": max_seconds,
            "result": "PASS" if elapsed <= max_seconds else "FAIL",
        }
        if options["as_json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write(
                f"mode={payload['mode']} documents={payload['documents']} "
                f"elapsed_seconds={payload['elapsed_seconds']} "
                f"limit_seconds={max_seconds} result={payload['result']}"
            )
        if payload["result"] != "PASS":
            raise CommandError(
                f"{payload['mode']} reindex exceeded acceptance limit: "
                f"{elapsed:.3f}s > {max_seconds}s"
            )
