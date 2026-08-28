"""CLI entrypoint: run the pipeline once, render the report, and send it
(or print it in --dry-run mode)."""
import argparse
import logging
import sys
from datetime import datetime

from . import config, mailer, pipeline, report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Maine Oceanfront Listing Agent")
    parser.add_argument("--dry-run", action="store_true", help="Print the report to stdout instead of emailing.")
    parser.add_argument(
        "--limit-new", type=int, default=config.MAX_NEW_ASSESSMENTS_PER_RUN,
        help=(
            "Cap the number of newly-seen listings assessed this run (bounds run duration; "
            f"default {config.MAX_NEW_ASSESSMENTS_PER_RUN}, matching config.MAX_NEW_ASSESSMENTS_PER_RUN). "
            "Pass 0 for no cap (processes the entire backlog in one run -- can take hours)."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if not args.verbose else logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("main")

    max_new = None if args.limit_new == 0 else args.limit_new

    try:
        diff = pipeline.run(max_new_assessments=max_new)
    except Exception as e:  # noqa: BLE001
        log.error("Pipeline run failed: %s", e, exc_info=True)
        # Fail loudly rather than silently: non-zero exit fails the Actions
        # job (which notifies via GitHub's own workflow-failure emails). If
        # email creds are configured, also try to send a failure notice.
        try:
            mailer.send_report(
                subject="Maine Oceanfront Listing Agent — RUN FAILED",
                html_body=f"<p>The pipeline run failed with an error:</p><pre>{e}</pre>",
                dry_run=args.dry_run,
            )
        except Exception as mail_err:  # noqa: BLE001
            log.error("Additionally failed to send failure-notification email: %s", mail_err)
        sys.exit(1)

    if not report.has_changes(diff):
        log.info("No changes since last run; not sending anything.")
        return

    html_body = report.render_report(diff, run_dt=datetime.now())
    subject_bits = []
    if diff.new_under_main:
        subject_bits.append(f"{len(diff.new_under_main)} new")
    if diff.price_cuts_under_main:
        subject_bits.append(f"{len(diff.price_cuts_under_main)} price cut")
    if diff.above_budget_passes:
        subject_bits.append(f"{len(diff.above_budget_passes)} above-budget")
    if diff.removed:
        subject_bits.append(f"{len(diff.removed)} removed")
    if diff.unverified:
        subject_bits.append(f"{len(diff.unverified)} unverified")
    subject = "Maine Oceanfront: " + ", ".join(subject_bits)

    mailer.send_report(subject, html_body, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
