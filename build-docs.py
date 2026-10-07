"""Build the observatory and documentation into a single static page."""

import re
from pathlib import Path

ROOT = Path(__file__).parent


def build(root: Path = ROOT) -> None:
    source = (root / "observatory.html").read_text()
    docs = (root / "docs/docs-content.html").read_text()
    source = source.replace(
        "<!--COGNITION_VIEW-->", (root / "cognition-view.html").read_text()
    )
    source = source.replace(
        "<!--DISCOVERY_VIEW-->", (root / "discovery-view.html").read_text()
    )
    source = source.replace(
        "</style>",
        (root / "cognition-view.css").read_text()
        + "\n"
        + (root / "discovery-view.css").read_text()
        + "\n"
        + (root / "observatory-design.css").read_text()
        + "\n</style>",
        1,
    )
    source = source.replace(
        "/*COGNITION_CLIENT*/", (root / "cognition-view.js").read_text()
    )
    source = source.replace(
        "/*DISCOVERY_CLIENT*/", (root / "discovery-view.js").read_text()
    )
    source = source.replace("/*NEURAL_MATH*/", (root / "neural-math.js").read_text())
    source = source.replace("/*NEURAL_FIELD*/", (root / "neural-field.js").read_text())
    css = (root / "docs/docs.css").read_text()
    client = (root / "docs/docs.js").read_text()

    logo_match = re.search(r'<a\b(?=[^>]*\bclass="logo")[^>]*>.*?</a\s*>', source, re.S)
    if logo_match is None:
        raise ValueError("Observatory logo is missing")
    footer_logo = (
        logo_match.group()
        .replace('id="leaf"', 'id="footer-leaf"')
        .replace("url(#leaf)", "url(#footer-leaf)")
    )
    source = source.replace("{{FOOTER_LOGO}}", footer_logo)
    docs_logo = (
        logo_match.group()
        .replace('href="#overview"', 'href="/"')
        .replace('id="leaf"', 'id="docs-leaf"')
        .replace("url(#leaf)", "url(#docs-leaf)")
    )
    docs = docs.replace("{{LOGO}}", docs_logo)
    source = source.replace("</style>", css + "\n</style>", 1)
    source = source.replace(
        "<script>",
        '<template id="nuriaDocsTemplate">\n' + docs + "\n</template>\n<script>",
        1,
    )
    old_js = source.split("<script>", 1)[1].split("</script>", 1)[0]
    new_js = (
        "'use strict';\n"
        + client
        + "\nif(new URLSearchParams(location.search).get('page')==='docs'){\n"
        + "  const content=document.getElementById('nuriaDocsTemplate').content.cloneNode(true);\n"
        + "  document.body.classList.add('docs-page');\n"
        + "  document.body.replaceChildren(content);\n"
        + "  initNuriaDocs();\n"
        + "}else{\n"
        + old_js
        + "\n}\n"
    )
    source = source.replace(
        "<script>" + old_js + "</script>", "<script>\n" + new_js + "\n</script>", 1
    )
    (root / "index.html").write_text(source)
    (root / "docs-script-check.js").write_text(new_js)


if __name__ == "__main__":
    build()
