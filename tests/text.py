"""Synthetic checks for the default recursive UTF-8 text export."""
from argparse import Namespace
from email import policy
from email.message import EmailMessage
from pathlib import Path
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import email_files as ef


def message(mid, subject, body=None, html=None):
    msg = EmailMessage()
    for key, value in {
        "Subject": subject,
        "From": "Sender Person <sender@example.test>",
        "To": "recipient@example.test",
        "Date": "Thu, 17 Sep 2026 10:00:00 +0800",
        "Message-ID": mid,
    }.items():
        msg[key] = value
    if body is not None:
        msg.set_content(body)
        if html is not None:
            msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(html, subtype="html")
    return msg


def export_args(eml, root):
    # Deliberately omit ``format`` to exercise the default and pass an invalid
    # browser path to prove text export does not try to start a browser.
    return Namespace(
        emls=[str(eml)], attachments_from=[], out=str(root), pdf_name=None,
        browser=str(root / "browser-does-not-exist.exe"),
        include_inline_images=False, omit_redundant_plain=False,
    )


def text_at(path):
    return Path(path).read_bytes().decode("utf-8")


def nested_text_containing(result, needle):
    matches = [item for item in result["nested_texts"] if needle in text_at(item["text"])]
    assert len(matches) == 1, (needle, result["nested_texts"])
    return matches[0]


def run(root):
    grandchild = message(
        "<grandchild-text@example.test>", "HTML-only grandchild",
        html="<p>HTML ONLY GRANDCHILD MARKER</p>",
    )

    child = message(
        "<child-text@example.test>", "Child subject",
        "CHILD SHARED MARKER\nCHILD UNIQUE PLAIN FACT",
        "<p>CHILD SHARED MARKER</p><p>CHILD UNIQUE HTML FACT</p>",
    )
    child.add_attachment(
        b"child attachment", maintype="application", subtype="octet-stream",
        filename="same.bin",
    )
    child.add_attachment(grandchild, filename="grandchild.eml")
    child_bytes = child.as_bytes(policy=policy.SMTP)

    parent = message(
        "<root-text@example.test>", "Root subject / 根邮件",
        "ROOT SHARED MARKER\nROOT UNIQUE PLAIN FACT",
        "<p>ROOT SHARED MARKER</p><p>ROOT UNIQUE HTML FACT 中文</p>",
    )
    parent.add_attachment(
        b"parent attachment", maintype="application", subtype="octet-stream",
        filename="same.bin",
    )
    parent.add_attachment(
        child_bytes, maintype="application", subtype="octet-stream",
        filename="child-one.eml",
    )
    parent.add_attachment(
        child_bytes, maintype="application", subtype="octet-stream",
        filename="child-two.eml",
    )
    parent_path = root / "root.eml"
    parent_path.write_bytes(parent.as_bytes(policy=policy.SMTP))

    args = export_args(parent_path, root)
    first = ef.export(args)
    assert {"text", "text_sha256", "reused", "sources", "attachments", "nested_texts"} <= first.keys()
    assert Path(first["text"]) == root / "root.txt"
    assert first["reused"] is False
    assert ef.digest(Path(first["text"]).read_bytes()) == first["text_sha256"]

    root_text = text_at(first["text"])
    for needle in (
        "Root subject / 根邮件", "Sender Person <sender@example.test>",
        "Thu, 17 Sep 2026 10:00:00 +0800", "ROOT UNIQUE PLAIN FACT",
        "ROOT UNIQUE HTML FACT 中文",
    ):
        assert needle in root_text, needle
    assert root_text.count("ROOT SHARED MARKER") == 1

    assert len(first["nested_texts"]) == 2
    child_text = nested_text_containing(first, "CHILD SHARED MARKER")
    grandchild_text = nested_text_containing(first, "HTML ONLY GRANDCHILD MARKER")
    for item in first["nested_texts"]:
        assert {"eml", "text", "text_sha256", "reused"} <= item.keys()
        eml_path, text_path = Path(item["eml"]), Path(item["text"])
        assert eml_path.is_file() and text_path.is_file()
        assert eml_path.parent == root and text_path.parent == root
        assert ef.digest(text_path.read_bytes()) == item["text_sha256"]
    rendered_child = text_at(child_text["text"])
    assert "CHILD UNIQUE PLAIN FACT" in rendered_child
    assert "CHILD UNIQUE HTML FACT" in rendered_child
    assert rendered_child.count("CHILD SHARED MARKER") == 1
    assert text_at(grandchild_text["text"]).count("HTML ONLY GRANDCHILD MARKER") == 1

    duplicate_child = [
        item for item in first["attachments"]
        if set(item.get("aliases", [])) == {"child-one.eml", "child-two.eml"}
    ]
    assert len(duplicate_child) == 1
    payloads = {b"parent attachment", b"child attachment"}
    extracted = {
        Path(item["path"])
        for item in first["attachments"]
        if Path(item["path"]).read_bytes() in payloads
    }
    assert {path.read_bytes() for path in extracted} == payloads
    assert all(path.parent == root and path.name.startswith("same") for path in extracted)
    assert all(not path.is_dir() for path in root.iterdir())

    scan = ef.scan(Namespace(
        inputs=[first["text"]], recursive=False, out=str(root / "scan")
    ))
    assert scan["files"] == 1 and scan["errors"] == 0
    inventory = json.loads(Path(scan["inventory"]).read_text(encoding="utf-8"))
    scanned = inventory["files"][0]
    assert scanned["kind"] == "txt"
    assert scanned["headers"]["Subject"] == "Root subject / 根邮件"
    assert scanned["headers"]["From"] == "Sender Person <sender@example.test>"
    assert "ROOT UNIQUE HTML FACT 中文" in text_at(scanned["text_file"])

    before = {path: path.read_bytes() for path in root.iterdir() if path.is_file()}
    second = ef.export(args)
    assert second["reused"] is True
    assert second["text"] == first["text"]
    assert second["text_sha256"] == first["text_sha256"]
    assert all(item["reused"] is True for item in second["nested_texts"])
    assert before == {path: path.read_bytes() for path in root.iterdir() if path.is_file()}

    collision = message(
        "<collision-text@example.test>", "Collision", "COLLISION BODY"
    )
    collision_path = root / "collision.eml"
    collision_path.write_bytes(collision.as_bytes(policy=policy.SMTP))
    unrelated = root / "collision.txt"
    unrelated.write_text("unrelated existing text", encoding="utf-8")
    try:
        ef.export(export_args(collision_path, root))
        raise AssertionError("Unrelated TXT output was overwritten")
    except FileExistsError:
        pass
    assert unrelated.read_text(encoding="utf-8") == "unrelated existing text"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="email-organizer-text-") as folder:
        run(Path(folder))
    print(
        "PASS: default UTF-8 text export, recursive EMLs, MIME fidelity, "
        "deduplication, flat attachments, safe reuse, and collision refusal."
    )
