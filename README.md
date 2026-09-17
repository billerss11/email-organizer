# Email Organizer

A compact Codex skill for a flat email-file workflow:

- Extract attachments into the same folder, keeping original filenames.
- Convert email chains to PDFs beside their EML files.
- Remove proven older duplicate chains after preserving unique information.

No conversation folders, attachment folders, backup copies, manifests, or organization reports. Inline signature images stay inside PDFs. Identical attachments are reused; filename conflicts preserve both versions. ZIPs stay intact. Current EMLs remain in place. Codex reviews duplicate chains and uses the system Recycle Bin for cleanup; the helper never deletes inputs.

## Use

Place this repository at `~/.codex/skills/email-organizer`, then invoke:

> Use $email-organizer to extract attachments and make PDFs in this same folder. Clean up older duplicate email chains. Do not organize anything into folders.

Requires Python 3.10+, the packages in `requirements.txt`, and Chrome or Edge. Prefer an existing non-base environment, such as `codex_env`. Run `python scripts/email_files.py --help` for helper commands. Run `python tests/smoke.py` for the synthetic smoke test.

Keep real email data and runtime output out of this repository. See [SKILL.md](SKILL.md) for the workflow.
