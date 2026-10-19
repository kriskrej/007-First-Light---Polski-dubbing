import argparse
import json
import sys


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="007 First Light Polish mod tooling")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("doctor", "index", "api-check", "estimate", "sources", "prepare-poc", "encoder-check", "prepare-mission", "campaign-status", "coverage-status", "audit-sync", "repair-sync"):
        commands.add_parser(command)
    extraction = commands.add_parser("extract")
    reactive = commands.add_parser("prepare-reactive")
    reactive.add_argument("--scope", default="late-bond-reactive")
    native_library = commands.add_parser("prepare-native-library")
    native_library.add_argument("--scope", required=True)
    extraction.add_argument("--limit", type=int)
    extraction.add_argument("--baseline", action="store_true")
    sample = commands.add_parser("poc")
    sample.add_argument("--lines", type=int, default=10)
    campaign = commands.add_parser("campaign")
    campaign.add_argument("--lines", type=int, default=25)
    campaign.add_argument("--chapter")
    first_pass = commands.add_parser("campaign-first-pass")
    first_pass.add_argument("--chapter", required=True)
    first_pass.add_argument("--prefetch", action="store_true",
                            help="Overlap one next TTS request with local fitting; no automatic retakes")
    campaign_install = commands.add_parser("campaign-install")
    campaign_install.add_argument("--dry-run", action="store_true")
    installation = commands.add_parser("install")
    installation.add_argument("--dry-run", action="store_true")
    restoration = commands.add_parser("rollback")
    restore_target = restoration.add_mutually_exclusive_group(required=True)
    restore_target.add_argument("--install-id")
    restore_target.add_argument("--latest", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            from .doctor import run
        elif args.command == "api-check":
            from .elevenlabs_api import check as run
        elif args.command == "estimate":
            from .estimate import run
        elif args.command == "coverage-status":
            from .coverage import run
        elif args.command == "audit-sync":
            from .sync_repair import audit as run
        elif args.command == "repair-sync":
            from .sync_repair import repair as run
        elif args.command == "sources":
            from .dependencies import run
        elif args.command == "prepare-poc":
            from .selection import run
        elif args.command == "encoder-check":
            from .encoding import roundtrip as run
        elif args.command == "prepare-mission":
            from .mission import run
        elif args.command == "prepare-reactive":
            from .reactive import run
            print(json.dumps(run(args.scope), ensure_ascii=False, indent=2))
            return 0
        elif args.command == "prepare-native-library":
            from .native import prepare_library
            print(json.dumps(prepare_library(args.scope), ensure_ascii=False, indent=2))
            return 0
        elif args.command == "poc":
            from .poc import run
        elif args.command == "campaign":
            from .campaign import run
        elif args.command == "campaign-first-pass":
            if args.prefetch:
                from .bulk import run
            else:
                from .batch import run
            print(json.dumps(run(args.chapter), ensure_ascii=False, indent=2))
            return 0
        elif args.command == "campaign-status":
            from .campaign import database, progress
            store = database()
            try:
                print(json.dumps(progress(store), ensure_ascii=False, indent=2))
            finally:
                store.close()
            return 0
        elif args.command == "campaign-install":
            from .deployment import run
            report = run(dry_run=args.dry_run)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        elif args.command == "install":
            from .common import ARTIFACTS
            if (ARTIFACTS / "campaign" / "tts.sqlite3").exists():
                from .deployment import run
                print(json.dumps(run(dry_run=args.dry_run), ensure_ascii=False, indent=2))
                return 0
            from .installer import install
            bundle = ARTIFACTS / "poc" / "bundle" / "bundle.json"
            if not bundle.is_file():
                raise RuntimeError("No validated Polish bundle exists yet; finish encoder roundtrip and PoC generation first")
            report = install(bundle, dry_run=args.dry_run)
            if not args.dry_run:
                from .common import save_json
                save_json(ARTIFACTS / "poc" / "current-install.json", report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        elif args.command == "rollback":
            from .installer import rollback
            identifier = args.install_id
            if args.latest:
                from .common import ARTIFACTS
                identifier = json.loads((ARTIFACTS / "poc" / "current-install.json").read_text(encoding="utf-8"))["install_id"]
            report = rollback(identifier)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        elif args.command == "extract":
            from .extract import run
        else:
            from .archive import ensure_index
            database, report = ensure_index()
            database.close()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        report = run(args.limit, baseline=args.baseline) if args.command == "extract" else run(args.lines, chapter=args.chapter) if args.command == "campaign" else run(args.lines) if args.command == "poc" else run()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, RuntimeError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
