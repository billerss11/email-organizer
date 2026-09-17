"""Synthetic flat-output end-to-end check; requires Chrome/Edge and requirements.txt."""
from argparse import Namespace
from email.message import EmailMessage
from email import policy
from pathlib import Path
import base64
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import email_files as ef


def message(mid, body):
    m = EmailMessage()
    for key, value in {"Subject": "Project / 项目", "From": "a@example.test",
                       "To": "b@example.test", "Date": "Thu, 17 Sep 2026 10:00:00 +0800",
                       "Message-ID": mid}.items():
        m[key] = value
    m.set_content(body)
    return m


def run(root):
    old = message("<old@example.test>", "Original decision: budget 12345.67 USD.")
    old.add_attachment(b"old budget", maintype="text", subtype="csv", filename="budget.csv")
    old.add_attachment(b"identical", maintype="application", subtype="octet-stream", filename="same.dat")
    new = message("<new@example.test>", "Ship Friday.\nOriginal decision: budget 12345.67 USD.\nUnique plain alternative fact.")
    new["References"] = "<old@example.test>"
    new.add_alternative('<p>Ship Friday. 中文内容。</p><table><tr><td>Budget</td><td>12345.67 USD</td></tr></table>'
                        '<blockquote>Original decision: budget 12345.67 USD.</blockquote>'
                        '<img src="cid:chart"><img src="https://tracking.invalid/image">'
                        '<script>document.body.innerHTML="EXECUTED"</script>', subtype="html")
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
    new.get_payload()[1].add_related(png, maintype="image", subtype="png", cid="<chart>")
    new.add_attachment(b"new budget", maintype="text", subtype="csv", filename="budget.csv")
    new.add_attachment(b"identical", maintype="application", subtype="octet-stream", filename="renamed.dat")
    new.add_attachment(b"safe path", maintype="application", subtype="octet-stream", filename="../../CON.dat")
    new.add_attachment(b"attached PDF bytes", maintype="application", subtype="pdf", filename="new.pdf", cid="<document>")
    for name, m in [("old.eml", old), ("new.eml", new)]:
        (root / name).write_bytes(m.as_bytes(policy=policy.SMTP))
    originals = {p: p.read_bytes() for p in root.glob("*.eml")}
    (root / "existing-copy.bin").write_bytes(b"identical")
    args = Namespace(emls=[str(root / "new.eml")], attachments_from=[str(root / "old.eml")],
                     out=None, pdf_name=None, browser=None, include_inline_images=False,
                     omit_redundant_plain=False)
    result = ef.export(args)
    assert result["unique_attachments"] == 5
    json.dumps(result)
    assert Path(result["pdf"]) == root / "new.pdf"
    assert not any(p.is_dir() for p in root.iterdir())
    assert not (root / "manifest.json").exists()
    for attachment in result["attachments"]:
        path = Path(attachment["path"])
        assert path.parent == root and ef.digest(path.read_bytes()) == attachment["sha256"]
    for source in result["sources"]:
        assert ef.digest(Path(source["path"]).read_bytes()) == source["sha256"]
    budgets = [Path(a["path"]).name for a in result["attachments"] if "budget.csv" in a["aliases"]]
    assert "budget.csv" in budgets and any(name.startswith("budget-") for name in budgets)
    identical = [a for a in result["attachments"] if set(a["aliases"]) == {"renamed.dat", "same.dat"}]
    assert len(identical) == 1 and Path(identical[0]["path"]).name == "existing-copy.bin"
    reserved = [a for a in result["attachments"] if a["aliases"] == ["new.pdf"]]
    assert len(reserved) == 1 and Path(reserved[0]["path"]).name.startswith("new-")
    assert not list(root.glob("*.png"))
    pdf = ef.PdfReader(root / "new.pdf")
    text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    for needle in ("Ship Friday.", "Original decision:", "12345.67", "中文", "Image unavailable offline", "Unique plain alternative fact."):
        assert needle in text, needle
    assert "EXECUTED" not in text and "<new@example.test>" not in text and "<old@example.test>" not in text
    assert not any(name in text for name in ("budget.csv", "same.dat", "renamed.dat"))
    assert any(list(page.images) for page in pdf.pages), "CID image was not embedded in the PDF"
    assert all(p.read_bytes() == raw for p, raw in originals.items())

    later = message("<later@example.test>", "Later")
    later.add_attachment(b"must not appear", maintype="text", subtype="plain", filename="new-later.txt")
    (root / "later.eml").write_bytes(later.as_bytes(policy=policy.SMTP))
    args.attachments_from.append(str(root / "later.eml"))
    try:
        ef.export(args)
        raise AssertionError("Existing destination PDF was overwritten")
    except FileExistsError:
        pass
    assert not (root / "new-later.txt").exists()

    result = ef.scan(Namespace(inputs=[str(root / "old.eml"), str(root / "new.pdf")], recursive=False, out=str(root / "scan")))
    assert result["files"] == 2 and result["errors"] == 0
    malformed = root / "malformed.eml"
    malformed.write_bytes(b'MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nBody\r\n--b\r\nContent-Type: application/octet-stream\r\nContent-Disposition: attachment; filename=x.bin\r\nContent-Transfer-Encoding: base64\r\n\r\naGVsbG8=!!!!\r\n--b--\r\n')
    assert any("InvalidBase64" in w for w in ef.read_eml(malformed)["warnings"])
    args.attachments_from = [str(root / "old.eml")]
    args.pdf_name = "reviewed.pdf"
    args.omit_redundant_plain = True
    ef.export(args)
    reviewed = "\n".join(p.extract_text() or "" for p in ef.PdfReader(root / "reviewed.pdf").pages)
    assert "Ship Friday." in reviewed and "Unique plain alternative fact." not in reviewed

    args.pdf_name = "with-inline.pdf"
    args.include_inline_images = True
    inline_result = ef.export(args)
    assert any(Path(a["path"]).suffix.lower() == ".png" for a in inline_result["attachments"])


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="email-organizer-test-") as folder:
        run(Path(folder))
    print("PASS: flat extraction, byte deduplication, revisions, inline-image policy, source integrity, Unicode PDF, safe HTML, and overwrite refusal.")
