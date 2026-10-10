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
    source = source.replace("<!--WORK_VIEW-->", (root / "work-view.html").read_text())
    source = source.replace(
        "</style>",
        (root / "cognition-view.css").read_text()
        + "\n"
        + (root / "discovery-view.css").read_text()
        + "\n"
        + (root / "observatory-design.css").read_text()
        + "\n"
        + (root / "observatory-polish.css").read_text()
        + "\n"
        + (root / "commerce-view.css").read_text()
        + "\n"
        + (root / "ui-controls.css").read_text()
        + "\n"
        + (root / "work-view.css").read_text()
        + "\n</style>",
        1,
    )
    source = source.replace(
        "/*COGNITION_CLIENT*/", (root / "cognition-view.js").read_text()
    )
    source = source.replace(
        "/*DISCOVERY_CLIENT*/", (root / "discovery-view.js").read_text()
    )
    source = source.replace(
        "/*COMMERCE_CLIENT*/", (root / "commerce-view.js").read_text()
    )
    source = source.replace("/*WORK_CLIENT*/", (root / "work-view.js").read_text())
    source = source.replace("/*NEURAL_MATH*/", (root / "neural-math.js").read_text())
    source = source.replace("/*NEURAL_FIELD*/", (root / "neural-field.js").read_text())
    source = source.replace(
        "{{FLOW_NEURAL}}", (root / "brand/flows/neural-loop.svg").read_text()
    )
    css = (root / "docs/docs.css").read_text()
    client = (root / "docs/docs.js").read_text()

    logo_match = re.search(r'<a\b(?=[^>]*\bclass="logo")[^>]*>.*?</a\s*>', source, re.S)
    if logo_match is None:
        raise ValueError("Observatory logo is missing")
    footer_logo = (
        logo_match.group()
        .replace('id="neural-mark"', 'id="footer-neural-mark"')
        .replace("url(#neural-mark)", "url(#footer-neural-mark)")
    )
    source = source.replace("{{FOOTER_LOGO}}", footer_logo)
    docs_logo = (
        logo_match.group()
        .replace('href="#overview"', 'href="/"')
        .replace('id="neural-mark"', 'id="docs-neural-mark"')
        .replace("url(#neural-mark)", "url(#docs-neural-mark)")
    )
    docs = docs.replace("{{LOGO}}", docs_logo)
    docs = docs.replace(
        "{{FLOW_NEURAL}}", (root / "brand/flows/neural-loop.svg").read_text()
    )
    for name in ("experience", "authority", "evidence", "commissioning"):
        docs = docs.replace(
            "{{FLOW_" + name.upper() + "}}",
            (root / "brand/flows" / (name + ".svg")).read_text(),
        )
    acquisition = (root / "docs/acquisition-results.svg").read_text()
    acquisition = acquisition[acquisition.index("<svg") :]
    docs = docs.replace("{{ACQUISITION_RESULTS}}", acquisition)
    source = source.replace(
        "</style>",
        css
        + "\n"
        + (root / "experience.css").read_text()
        + "\n"
        + (root / "lab-design.css").read_text()
        + "\n</style>",
        1,
    )
    source = source.replace(
        "<script>",
        '<template id="nuriaDocsTemplate">\n' + docs + "\n</template>\n<script>",
        1,
    )
    old_js = source.split("<script>", 1)[1].split("</script>", 1)[0]
    new_js = (
        "'use strict';\n"
        + (root / "ui-controls.js").read_text()
        + "\n"
        + client
        + "\nif(new URLSearchParams(location.search).get('page')==='docs'){\n"
        + "  const content=document.getElementById('nuriaDocsTemplate').content.cloneNode(true);\n"
        + "  document.body.classList.add('docs-page');\n"
        + "  document.body.replaceChildren(content);\n"
        + "  initNuriaControls();\n  initNuriaDocs();\n"
        + "}else{\n"
        + "initNuriaControls();\n"
        + old_js
        + "\n}\n"
    )
    source = source.replace(
        "<script>" + old_js + "</script>", "<script>\n" + new_js + "\n</script>", 1
    )
    github_mark = re.sub(
        r"<svg\b[^>]*>",
        '<svg class="github-mark" viewBox="0 0 16 16" aria-hidden="true">',
        (root / "brand/github-mark.svg").read_text(),
        count=1,
    )

    def github_link(match: re.Match) -> str:
        attrs, content = match.groups()
        if 'class="' in attrs:
            attrs = attrs.replace('class="', 'class="github-link ', 1)
        else:
            attrs += ' class="github-link"'
        return "<a" + attrs + ">" + github_mark + "<span>" + content + "</span></a>"

    source = re.sub(
        r'<a\b([^>]*href="https://github\.com/[^>]+)>(.*?)</a\s*>',
        github_link,
        source,
        flags=re.S,
    )
    (root / "index.html").write_text(source)
    (root / "docs-script-check.js").write_text(new_js)


if __name__ == "__main__":
    build()
