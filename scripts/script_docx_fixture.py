"""Small real Word packages for approval-gate and completion-recording tests."""
from html import escape
from zipfile import ZIP_DEFLATED, ZipFile


def write_script_docx(path, text="Approved script.", *, rows=None,
                      headers=("ID", "Spoken Dialog", "Stage Directions"),
                      heading="Spoken script", paragraph_body=False, table_before_heading=False):
    def paragraph(value):
        return f'<w:p><w:r><w:t xml:space="preserve">{escape(value)}</w:t></w:r></w:p>'

    rows = [("L01", text, "")] if rows is None else rows
    if paragraph_body:
        body = paragraph(text)
    else:
        grid = "<w:gridCol/>" * len(headers)
        body = f"<w:tbl><w:tblGrid>{grid}</w:tblGrid>"
        for row in [headers, *rows]:
            body += "<w:tr>" + "".join(f"<w:tc>{paragraph(value)}</w:tc>" for value in row) + "</w:tr>"
        body += "</w:tbl>"
    title = "" if heading is None else paragraph(heading)
    body = body + title if table_before_heading else title + body
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:body>{body}<w:sectPr/></w:body></w:document>")
    content_types = ('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" '
                     'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '</Types>')
    relationships = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" '
                     'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                     'Target="word/document.xml"/></Relationships>')
    with ZipFile(path, "w", ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", relationships)
        package.writestr("word/document.xml", document)
    return path
