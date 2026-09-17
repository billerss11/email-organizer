#!/usr/bin/env python3
"""Inspect local EML/PDF files; export EMLs and attachments without changing sources."""
import argparse
import base64
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
import hashlib
import html
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import unquote

from bs4 import BeautifulSoup
from pypdf import PdfReader


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_name(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .")[:120]
    name = name or "unnamed"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", name, re.I):
        name = "_" + name
    return name


def decode_text(part, warnings):
    raw = part.get_payload(decode=True)
    if raw is None:
        return str(part.get_payload() or "")
    try:
        return raw.decode(part.get_content_charset() or "utf-8")
    except (UnicodeError, LookupError):
        warnings.append("Text decoding needed replacements; inspect source before cleanup.")
        return raw.decode("utf-8", errors="replace")


def read_eml(path):
    raw = path.read_bytes()
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    warnings, attachments, part_scopes, text_parts = [], [], {}, []

    def collect(part, location="1", scope="", in_attachment=False):
        mime = part.get_content_type()
        if mime == "multipart/related":
            scope = location
        part_scopes[id(part)] = scope
        if mime in {"application/ms-tnef", "application/pkcs7-mime", "application/x-pkcs7-mime", "multipart/encrypted", "multipart/signed"}:
            warnings.append(f"Special MIME {mime}: retain original until independently verified.")
        embedded = mime == "message/rfc822"
        is_file = bool(part.get_filename()) or part.get_content_disposition() == "attachment"
        if mime in {"text/plain", "text/html"} and not in_attachment and not is_file:
            text_parts.append(part)
        if embedded or is_file or (not part.is_multipart() and mime not in {"text/plain", "text/html"}):
            data = part.get_payload(decode=True)
            if data is None and isinstance(part.get_payload(), list):
                data = b"\r\n".join(p.as_bytes(policy=policy.SMTP) for p in part.get_payload())
                warnings.append(f"Attached message/container {location} serialized from MIME; original retained.")
            if data is None:
                data = str(part.get_payload() or "").encode("utf-8")
                warnings.append(f"Undecoded attachment {location}; inspect source.")
            ext = ".eml" if embedded else (mimetypes.guess_extension(mime) or ".bin")
            attachments.append({"name": str(part.get_filename() or f"part-{location}{ext}"),
                                "mime": mime, "sha256": digest(data), "bytes": len(data),
                                "cid": str(part.get("Content-ID", "")).strip("<>"),
                                "location": location, "inline": part.get_content_disposition() == "inline",
                                "_scope": scope, "_body_resource": not in_attachment,
                                "data": data})
        if part.is_multipart():
            for i, child in enumerate(part.iter_parts(), 1):
                collect(child, f"{location}.{i}", scope, in_attachment or embedded or is_file)

    collect(msg)
    selected = msg.get_body(preferencelist=("html", "plain"))
    body = decode_text(selected, warnings) if selected is not None else ""
    is_html = selected is not None and selected.get_content_type() == "text/html"
    plain_part = msg.get_body(preferencelist=("plain",))
    plain = decode_text(plain_part, warnings) if plain_part is not None else ""
    if any(p is not selected and p is not plain_part for p in text_parts):
        warnings.append("Additional text MIME parts outside selected alternatives; inspect source and preserve their content before cleanup.")
    text = BeautifulSoup(body, "html.parser").get_text("\n") if is_html else body
    if not body.strip():
        warnings.append("No readable email body; inspect source.")
    compact_html = re.sub(r"\s+", "", text)
    plain_has_extra = is_html and any(re.sub(r"\s+", "", line) not in compact_html for line in plain.splitlines() if line.strip())
    if plain_has_extra:
        warnings.append("Plain MIME alternative contains additional/different text; review both representations.")
    # Base64 defects may only appear after decoding; inspect after all decoding.
    for part in msg.walk():
        if part.defects:
            warnings.append("MIME defects: " + ", ".join(type(d).__name__ for d in part.defects) + "; retain original.")
    headers = {k: str(msg.get(k, "")) for k in ("Subject", "From", "To", "Cc", "Bcc", "Date", "Message-ID", "In-Reply-To", "References")}
    try:
        date = parsedate_to_datetime(headers["Date"]).isoformat()
    except (TypeError, ValueError, OverflowError):
        date = None
        warnings.append("Missing/unparseable Date; do not infer chronology from filename.")
    return {"path": str(path), "kind": "eml", "sha256": digest(raw), "headers": headers,
            "date": date, "warnings": warnings, "attachments": attachments,
            "body": body, "is_html": is_html, "text": text, "plain_alternative": plain,
            "plain_has_extra": bool(plain_has_extra), "_body_scope": part_scopes.get(id(selected), ""), "raw": raw}


def read_pdf(path):
    raw = path.read_bytes()
    reader = PdfReader(path)
    warnings = []
    if reader.is_encrypted:
        warnings.append("Encrypted PDF: retain until decryption and verification.")
    text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    if not text.strip():
        warnings.append("No searchable text: local OCR/visual comparison required.")
    if any(page.get("/Annots") for page in reader.pages):
        warnings.append("PDF annotations/links present; inspect before retiring.")
    if reader.attachments:
        warnings.append("Embedded PDF files present; preserve/extract before retiring.")
    return {"path": str(path), "kind": "pdf", "sha256": digest(raw), "pages": len(reader.pages),
            "warnings": warnings, "text": text, "attachments": []}


def public_record(record):
    result = {k: v for k, v in record.items() if not k.startswith("_") and k not in {"body", "is_html", "text", "plain_alternative", "raw", "attachments"}}
    result["attachments"] = [{k: v for k, v in a.items() if k != "data" and not k.startswith("_")} for a in record.get("attachments", [])]
    return result


def input_files(inputs, recursive):
    seen = set()
    for name in inputs:
        root = Path(name).resolve(strict=True)
        paths = sorted(root.rglob("*") if recursive else root.iterdir()) if root.is_dir() else [root]
        for path in paths:
            if path.is_symlink() or any(p in {".git", ".email-organizer-recovery"} for p in path.parts):
                continue
            if not path.is_file() or path.suffix.lower() not in {".eml", ".pdf"}:
                continue
            path = path.resolve()
            if path not in seen:
                seen.add(path)
                yield path


def scan(args):
    paths = list(input_files(args.inputs, args.recursive))
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    records = []
    for index, path in enumerate(paths, 1):
        try:
            record = read_eml(path) if path.suffix.lower() == ".eml" else read_pdf(path)
            text_path = out / f"{index:04d}-{record['sha256'][:12]}.txt"
            text = record["text"]
            if record.get("plain_alternative") and record["plain_alternative"] != text:
                text += "\n\n--- PLAIN MIME ALTERNATIVE (not an additional message) ---\n" + record["plain_alternative"]
            text_path.write_text(text, encoding="utf-8")
            item = public_record(record)
            item["text_file"] = str(text_path)
            records.append(item)
        except Exception as exc:
            records.append({"path": str(path), "error": str(exc), "warnings": ["Could not inspect; do not retire."]})
    save_json(out / "inventory.json", {"files": records})
    return {"inventory": str(out / "inventory.json"), "files": len(records),
            "errors": sum("error" in r for r in records), "files_with_warnings": sum(bool(r.get("warnings")) for r in records)}


ALLOWED_TAGS = set("p div span pre blockquote br hr b strong i em u s strike sub sup ul ol li table thead tbody tfoot tr td th caption colgroup col a img h1 h2 h3 h4 h5 h6 center font dl dt dd".split())
RASTER_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}


def sanitized_body(record):
    if not record["is_html"]:
        return '<pre class="plain">' + html.escape(record["body"]) + "</pre>"
    soup = BeautifulSoup(record["body"], "html.parser")
    if soup.find("style"):
        record["warnings"].append("Stylesheet rules removed; check whether formatting conveys unique information.")
    unsupported = soup.find_all(["iframe", "object", "embed", "svg", "input"])
    if unsupported:
        record["warnings"].append("Unsupported active/vector/form content omitted; inspect and retain source.")
    for tag in list(soup.find_all(["script", "style", "iframe", "object", "embed", "link", "meta", "base", "svg", "input", "head"])):
        tag.decompose()
    inline = {a["cid"]: a for a in record["attachments"] if a["cid"] and a["_body_resource"] and a["_scope"] == record["_body_scope"]}
    for tag in list(soup.find_all(True)):
        if tag.name not in ALLOWED_TAGS:
            tag.unwrap()
            continue
        attrs = dict(tag.attrs)
        tag.attrs = {}
        # Preserve common meaning-bearing inline formatting, without active CSS.
        styles = []
        for declaration in attrs.get("style", "").split(";"):
            key, separator, value = declaration.partition(":")
            key, value = key.strip().lower(), value.strip()
            if separator and key in {"color", "background-color", "font-weight", "font-style", "text-decoration", "text-decoration-line", "text-align", "vertical-align", "white-space", "direction"} and re.fullmatch(r"[a-zA-Z0-9 #(),.%+-]+", value):
                styles.append(f"{key}:{value}")
        if styles:
            tag["style"] = ";".join(styles)
        if tag.name in {"td", "th"}:
            for attr in ("colspan", "rowspan"):
                if str(attrs.get(attr, "")).isdigit():
                    tag[attr] = str(min(int(attrs[attr]), 100))
        if tag.name == "a" and re.match(r"^(https?://|mailto:)", attrs.get("href", ""), re.I):
            tag["href"] = attrs["href"]
        if tag.name == "img":
            src = attrs.get("src", "")
            attachment = inline.get(unquote(src[4:]).strip("<>")) if src.lower().startswith("cid:") else None
            if attachment and attachment["mime"] in RASTER_MIMES:
                tag["src"] = f"data:{attachment['mime']};base64," + base64.b64encode(attachment["data"]).decode("ascii")
            elif re.match(r"^data:image/(png|jpeg|gif|webp|bmp);base64,", src, re.I):
                tag["src"] = src
            else:
                tag.replace_with(soup.new_string("[Image unavailable offline: " + (attrs.get("alt") or src or "unknown image") + "]"))
                record["warnings"].append("Unresolved/external/unsupported image in HTML; inspect source before cleanup.")
                continue
            tag["alt"] = attrs.get("alt", "")
    return str(soup)


def find_browser(explicit):
    if explicit:
        return str(Path(explicit).resolve(strict=True))
    paths = [shutil.which(n) for n in ("chromium", "chromium-browser", "google-chrome", "msedge")]
    for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = Path(os.environ.get(key, "C:/nonexistent"))
        paths.extend(str(base / p) for p in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe"))
    for path in paths:
        if path and Path(path).is_file():
            return path
    raise RuntimeError("Chrome/Edge not found; pass --browser PATH.")


def render_pdf(html_path, pdf_path, browser):
    with tempfile.TemporaryDirectory(prefix="email-pdf-") as profile:
        command = [browser, "--headless=new", "--disable-gpu", "--disable-extensions",
                   "--disable-background-networking", "--disable-sync", "--no-first-run",
                   "--no-default-browser-check", "--host-resolver-rules=MAP * ~NOTFOUND",
                   "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
                   "--virtual-time-budget=3000", f"--user-data-dir={profile}",
                   f"--print-to-pdf={pdf_path}", html_path.as_uri()]
        result = subprocess.run(command, capture_output=True, timeout=90,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or not pdf_path.is_file():
            raise RuntimeError("Browser PDF export failed: " + result.stderr.decode("utf-8", errors="replace")[-1000:])
    if not PdfReader(pdf_path).pages:
        raise RuntimeError("PDF has no pages.")


def export(args):
    browser = find_browser(args.browser)
    records, by_path = [], {}
    for name in args.emls + args.attachments_from:
        path = Path(name).resolve(strict=True)
        if path.suffix.lower() != ".eml":
            raise ValueError("Export accepts EML sources only.")
        if path not in by_path:
            by_path[path] = read_eml(path)
            records.append(by_path[path])
    included = [by_path[Path(name).resolve()] for name in args.emls]
    omit_plain = getattr(args, "omit_redundant_plain", False)
    if omit_plain:
        for record in included:
            record["warnings"] = [w for w in record["warnings"] if not w.startswith("Plain MIME alternative contains")]
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / "attachments").mkdir()
    (out / "sources").mkdir()
    saved = {}
    for record in records:
        source = out / "sources" / (record["sha256"][:16] + "--" + safe_name(Path(record["path"]).name))
        if source.exists() and digest(source.read_bytes()) != record["sha256"]:
            raise RuntimeError("Source filename collision.")
        source.write_bytes(record["raw"])
        record["saved_source"] = str(source.relative_to(out))
        for attachment in record["attachments"]:
            sha = attachment["sha256"]
            if sha not in saved:
                target = out / "attachments" / (sha[:16] + "--" + safe_name(attachment["name"]))
                if target.exists() and digest(target.read_bytes()) != sha:
                    raise RuntimeError("Attachment filename collision.")
                target.write_bytes(attachment["data"])
                saved[sha] = str(target.relative_to(out))
            attachment["saved_as"] = saved[sha]
    sections = []
    for record in included:
        header_rows = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>" for k, v in record["headers"].items() if v)
        attachment_rows = "".join("<li>" + html.escape(a["name"]) + f" ({a['bytes']} bytes) - " + html.escape(a["saved_as"]) + "</li>" for a in record["attachments"])
        body = sanitized_body(record)
        if record["plain_has_extra"] and not omit_plain:
            body += '<h2>Plain-text alternative with additional content</h2><pre>' + html.escape(record["plain_alternative"]) + '</pre>'
        sections.append('<section><h1>' + html.escape(record["headers"]["Subject"] or "Email") + '</h1><table class="headers">' + header_rows + '</table><article>' + body + '</article>' + ('<h2>Files extracted from this email</h2><ul>' + attachment_rows + '</ul>' if attachment_rows else '') + '</section>')
    all_files = "".join("<li>" + html.escape(name) + "</li>" for name in saved.values())
    if all_files:
        sections.append('<section><h1>All preserved attachments</h1><p>Includes attachments from earlier source emails.</p><ul>' + all_files + '</ul></section>')
    document = '''<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<style>@page{size:A4;margin:16mm}body{font:11pt Arial,'Microsoft YaHei',sans-serif;color:#171717;line-height:1.45;overflow-wrap:anywhere}
h1{font-size:17pt}h2{font-size:12pt}section+section{break-before:page}table{border-collapse:collapse;max-width:100%;table-layout:fixed}
td,th{border:1px solid #bbb;padding:5px;vertical-align:top;overflow-wrap:anywhere}th{text-align:left}.headers{width:100%;font-size:9pt;margin-bottom:20px}.headers th{width:85px}
pre{white-space:pre-wrap;font-family:inherit;overflow-wrap:anywhere}blockquote{border-left:2px solid #ccc;margin:10px 0;padding-left:12px}img{max-width:100%;max-height:235mm;object-fit:contain}li{overflow-wrap:anywhere}
</style></head><body>''' + "\n".join(sections) + "</body></html>"
    pdf_path = out / "conversation.pdf"
    with tempfile.TemporaryDirectory(prefix="email-html-") as temp:
        html_path = Path(temp) / "email.html"
        html_path.write_text(document, encoding="utf-8")
        render_pdf(html_path, pdf_path, browser)
    for record in records:
        if digest((out / record["saved_source"]).read_bytes()) != record["sha256"]:
            raise RuntimeError("Source copy hash verification failed.")
    for sha, name in saved.items():
        if digest((out / name).read_bytes()) != sha:
            raise RuntimeError("Attachment hash verification failed.")
    reader = PdfReader(pdf_path)
    if not "".join(p.extract_text() or "" for p in reader.pages).strip():
        raise RuntimeError("PDF contains no searchable text.")
    manifest = {"version": 1, "created_utc": datetime.now(timezone.utc).isoformat(),
                "pdf": "conversation.pdf", "pdf_sha256": digest(pdf_path.read_bytes()), "pages": len(reader.pages),
                "included_sources": [r["path"] for r in included], "sources": [public_record(r) for r in records],
                "plain_alternative_policy": "omitted_after_content_review" if omit_plain else "preserve_additional_text",
                "unique_attachments": len(saved), "status": "exported; content/visual review required before cleanup"}
    save_json(out / "manifest.json", manifest)
    return {"pdf": str(pdf_path), "manifest": str(out / "manifest.json"), "pages": len(reader.pages),
            "unique_attachments": len(saved), "warnings": sorted(set(w for r in records for w in r["warnings"]))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    s = commands.add_parser("scan", help="Read specified EML/PDF files; save evidence without changing sources.")
    s.add_argument("inputs", nargs="+")
    s.add_argument("--recursive", action="store_true", help="Include subfolders of specified directories.")
    s.add_argument("--out", required=True, help="New directory for inventory and extracted text.")
    e = commands.add_parser("export", help="Export selected EML bodies, unique attachments, and original sources.")
    e.add_argument("emls", nargs="+", help="EMLs whose bodies belong in the PDF, in desired order.")
    e.add_argument("--attachments-from", nargs="*", default=[], help="Older EMLs to preserve sources/attachments from.")
    e.add_argument("--out", required=True, help="New directory; existing directories are never overwritten.")
    e.add_argument("--browser", help="Optional Chrome/Edge executable.")
    e.add_argument("--omit-redundant-plain", action="store_true", help="Omit plain MIME alternative only after reviewing that HTML preserves its content and link/image targets.")
    args = parser.parse_args()
    try:
        print(json.dumps(scan(args) if args.command == "scan" else export(args), ensure_ascii=False))
    except Exception as exc:
        parser.exit(1, f"Error: {exc}\nNo input files were modified. Do not clean up sources after this failure.\n")


if __name__ == "__main__":
    main()
