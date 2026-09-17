"""Synthetic end-to-end check; requires Chrome/Edge and requirements.txt."""
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
    for name, m in [("old.eml", old), ("new.eml", new)]:
        (root / name).write_bytes(m.as_bytes(policy=policy.SMTP))
    originals = {p: p.read_bytes() for p in root.glob("*.eml")}
    out = root / "export"
    args = Namespace(emls=[str(root / "new.eml")], attachments_from=[str(root / "old.eml")], out=str(out), browser=None)
    result = ef.export(args)
    assert result["unique_attachments"] == 5
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        assert ef.digest((out / source["saved_source"]).read_bytes()) == source["sha256"]
        for attachment in source["attachments"]:
            path = out / attachment["saved_as"]
            assert path.resolve().is_relative_to(out.resolve())
            assert ef.digest(path.read_bytes()) == attachment["sha256"]
    text = "\n".join(p.extract_text() for p in ef.PdfReader(out / "conversation.pdf").pages)
    for needle in ("Ship Friday.", "Original decision:", "12345.67", "中文", "Image unavailable offline", "Unique plain alternative fact."):
        assert needle in text, needle
    assert "EXECUTED" not in text
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    try:
        ef.export(args)
        raise AssertionError("Existing output was overwritten")
    except FileExistsError:
        pass
    result = ef.scan(Namespace(inputs=[str(root / "old.eml"), str(out / "conversation.pdf")], recursive=False, out=str(root / "scan")))
    assert result["files"] == 2 and result["errors"] == 0
    malformed = root / "malformed.eml"
    malformed.write_bytes(b'MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nBody\r\n--b\r\nContent-Type: application/octet-stream\r\nContent-Disposition: attachment; filename=x.bin\r\nContent-Transfer-Encoding: base64\r\n\r\naGVsbG8=!!!!\r\n--b--\r\n')
    assert any("InvalidBase64" in w for w in ef.read_eml(malformed)["warnings"])


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="email-organizer-test-") as folder:
        run(Path(folder))
    print("PASS: extraction, byte deduplication, filename collisions, source integrity, searchable Unicode PDF, safe HTML, and overwrite refusal.")
