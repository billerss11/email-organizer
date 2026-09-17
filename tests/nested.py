"""Synthetic checks for recursive attached-email PDF export."""
from argparse import Namespace
from email import policy
from email.message import EmailMessage
from pathlib import Path
import base64
import sys
import tempfile

from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import email_files as ef


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)


def message(mid, subject, body):
    msg = EmailMessage()
    for key, value in {
        "Subject": subject,
        "From": "sender@example.test",
        "To": "recipient@example.test",
        "Date": "Thu, 17 Sep 2026 10:00:00 +0800",
        "Message-ID": mid,
    }.items():
        msg[key] = value
    msg.set_content(body)
    return msg


def export_args(eml, root, pdf_name, omit_plain=True):
    return Namespace(
        emls=[str(eml)], attachments_from=[], out=str(root), pdf_name=pdf_name,
        browser=None, include_inline_images=False,
        omit_redundant_plain=omit_plain, format="pdf",
    )


def pdf_text(path):
    return "\n".join(page.extract_text() or "" for page in ef.PdfReader(path).pages)


def nested_pdf_containing(result, needle):
    matches = [item for item in result["nested_pdfs"] if needle in pdf_text(item["pdf"])]
    assert len(matches) == 1, (needle, result["nested_pdfs"])
    return matches[0]


def run(root):
    grandchild = message(
        "<grandchild@example.test>", "Grandchild", "GRANDCHILD DEPTH MARKER"
    )
    grandchild.add_attachment(
        b"grandchild ordinary", maintype="application", subtype="octet-stream",
        filename="same.bin",
    )

    child = message(
        "<native-child@example.test>", "Native child",
        "NATIVE CHILD MARKER\nUNIQUE CHILD PLAIN FACT",
    )
    child.add_alternative(
        '<p>NATIVE CHILD MARKER</p><img src="cid:child-image">', subtype="html"
    )
    child.get_payload()[1].add_related(
        PNG, maintype="image", subtype="png", cid="<child-image>",
        filename="child-inline.png", disposition="inline",
    )
    child.add_attachment(
        b"native child ordinary", maintype="application", subtype="octet-stream",
        filename="same.bin",
    )
    child.add_attachment(grandchild, filename="grandchild.eml")

    opaque = message(
        "<opaque-child@example.test>", "Opaque child", "OPAQUE CHILD MARKER"
    )
    opaque.add_attachment(
        b"opaque child ordinary", maintype="application", subtype="octet-stream",
        filename="same.bin",
    )
    opaque_bytes = opaque.as_bytes(policy=policy.SMTP)

    parent = message("<parent@example.test>", "Parent", "PARENT MARKER")
    parent.add_alternative("<p>PARENT MARKER</p>", subtype="html")
    parent.add_attachment(
        b"parent ordinary", maintype="application", subtype="octet-stream",
        filename="same.bin",
    )
    parent.add_attachment(child, filename="native-child.eml")
    parent.add_attachment(
        opaque_bytes, maintype="application", subtype="octet-stream",
        filename="opaque-one.eml",
    )
    parent.add_attachment(
        opaque_bytes, maintype="application", subtype="octet-stream",
        filename="opaque-two.eml",
    )
    parent_path = root / "parent.eml"
    parent_path.write_bytes(parent.as_bytes(policy=policy.SMTP))

    unrelated = root / "native-child.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with unrelated.open("wb") as destination:
        writer.write(destination)
    unrelated_bytes = unrelated.read_bytes()

    first_args = export_args(parent_path, root, "parent-output.pdf")
    first = ef.export(first_args)
    assert Path(first["pdf"]) == root / "parent-output.pdf"
    assert len(first["nested_pdfs"]) == 3
    for item in first["nested_pdfs"]:
        assert {"eml", "pdf", "pdf_sha256", "pages", "reused"} <= item.keys()
        eml_path, nested_pdf = Path(item["eml"]), Path(item["pdf"])
        assert eml_path.is_file() and nested_pdf.is_file()
        assert eml_path.parent == root and nested_pdf.parent == root
        assert ef.digest(nested_pdf.read_bytes()) == item["pdf_sha256"]
        assert item["pages"] == len(ef.PdfReader(nested_pdf).pages)

    native_pdf = nested_pdf_containing(first, "NATIVE CHILD MARKER")
    grandchild_pdf = nested_pdf_containing(first, "GRANDCHILD DEPTH MARKER")
    opaque_pdf = nested_pdf_containing(first, "OPAQUE CHILD MARKER")
    assert "UNIQUE CHILD PLAIN FACT" in pdf_text(native_pdf["pdf"])
    assert any(
        list(page.images) for page in ef.PdfReader(native_pdf["pdf"]).pages
    ), "The child's CID image was not embedded in its PDF"
    assert grandchild_pdf["pdf"] != native_pdf["pdf"]
    assert unrelated.read_bytes() == unrelated_bytes
    assert Path(native_pdf["pdf"]) != unrelated
    assert not list(root.glob("*.png")), "Inline image was dumped as a separate file"

    duplicate_opaque = [
        item for item in first["attachments"]
        if set(item.get("aliases", [])) == {"opaque-one.eml", "opaque-two.eml"}
    ]
    assert len(duplicate_opaque) == 1

    ordinary_payloads = {
        b"parent ordinary", b"native child ordinary", b"grandchild ordinary",
        b"opaque child ordinary",
    }
    ordinary_paths = {
        Path(item["path"])
        for item in first["attachments"]
        if Path(item["path"]).read_bytes() in ordinary_payloads
    }
    assert len(ordinary_paths) == len(ordinary_payloads)
    assert {path.read_bytes() for path in ordinary_paths} == ordinary_payloads
    assert all(path.parent == root and path.name.startswith("same") for path in ordinary_paths)
    assert all(not path.is_dir() for path in root.iterdir())

    second_parent = message(
        "<second-parent@example.test>", "Second parent", "SECOND PARENT MARKER"
    )
    second_parent.add_attachment(
        opaque_bytes, maintype="application", subtype="octet-stream",
        filename="renamed-shared-child.eml",
    )
    second_path = root / "second-parent.eml"
    second_path.write_bytes(second_parent.as_bytes(policy=policy.SMTP))
    second_args = export_args(second_path, root, "second-parent.pdf", omit_plain=False)
    second = ef.export(second_args)
    assert len(second["nested_pdfs"]) == 1
    reused = second["nested_pdfs"][0]
    assert reused["reused"] is True
    assert Path(reused["pdf"]) == Path(opaque_pdf["pdf"])
    assert reused["pdf_sha256"] == opaque_pdf["pdf_sha256"]

    before = {path: path.read_bytes() for path in root.iterdir() if path.is_file()}
    try:
        ef.export(second_args)
        raise AssertionError("Existing primary destination PDF was overwritten")
    except FileExistsError:
        pass
    assert before == {path: path.read_bytes() for path in root.iterdir() if path.is_file()}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="email-organizer-nested-") as folder:
        run(Path(folder))
    print(
        "PASS: recursive native/opaque EML export, deep nesting, deduplication, "
        "flat collision-safe output, nested content fidelity, and PDF reuse."
    )
