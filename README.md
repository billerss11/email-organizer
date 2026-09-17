# Email Organizer

A compact Codex skill for a flat email-file workflow:

- Extract attachments into the same folder, keeping original filenames.
- Convert email chains to UTF-8 TXT beside their EML files; PDF only when requested.
- Process attached emails recursively, including their text/PDF and attachments, in that same folder.
- Remove proven older duplicate chains after preserving unique information.

No conversation folders, attachment folders, backup copies, manifests, or organization reports. TXT preserves conversation text and headers; original EMLs retain images and formatting. Identical attachments and nested emails are reused; filename conflicts preserve both versions. ZIPs stay intact. Codex reviews duplicate chains and uses the system Recycle Bin for cleanup; the helper never deletes inputs.

## Use

Place this repository at `~/.codex/skills/email-organizer`, then invoke:

> Use $email-organizer to extract attachments and make TXT files in this same folder. Clean up older duplicate email chains. Do not organize anything into folders.

Requires Python 3.10+ and the packages in `requirements.txt`. Chrome/Edge is needed only for `--format pdf`. Prefer an existing non-base environment, such as `codex_env`. Run `python scripts/email_files.py --help` for commands. Synthetic checks: `python tests/text.py` (default TXT), `python tests/smoke.py` and `python tests/nested.py` (PDF).

Keep real email data and runtime output out of this repository. See [SKILL.md](SKILL.md) for the workflow.
