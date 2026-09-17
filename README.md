# Email Organizer

A compact Codex skill for local email archives:

- Extract attachments from EML files; deduplicate identical bytes.
- Create searchable PDFs with email headers, quotations, tables, and embedded images.
- Compare specified EML/PDF files and retain the newest complete conversation.
- Preserve unique older attachments and separate reply branches.
- Move confirmed redundant files to a recovery folder; leave uncertain matches intact.

Codex evaluates thread relationships and performs requested cleanup. The helper only scans and exports; it never deletes inputs. Original EMLs are preserved in each export's `sources/` folder. External images are not downloaded. HTML is reflowed for readability.

## Use

Place this repository at `~/.codex/skills/email-organizer`, then invoke:

> Use $email-organizer on these EML files. Compare with this archive folder, save unique attachments, convert complete chains to PDF, and clean up older redundant copies.

Requires Python 3.10+, the packages in `requirements.txt`, and Chrome or Edge. Prefer an existing non-base environment, such as `codex_env`. Run `python scripts/email_files.py --help` for helper commands. Run `python tests/smoke.py` for the synthetic smoke test.

Only skill instructions, code, and synthetic tests belong in this repository. Store real emails, attachments, generated PDFs, and manifests elsewhere. See [SKILL.md](SKILL.md) for the workflow.
