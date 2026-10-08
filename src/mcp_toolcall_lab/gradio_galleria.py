"""Optional local galleria, adapted from Ironmate gradio_galleria.py.

Source: myon-bioinformatics/Ironmate@1281553 (MIT; vendor/Ironmate-LICENSE).
Run from a source checkout with PYTHONPATH=src python -m mcp_toolcall_lab.gradio_galleria.
"""
from __future__ import annotations
import csv, io, json
from pathlib import Path
from typing import Dict, Mapping, Optional

from mcp_toolcall_lab.markdown_lib import REPO_ROOT, load_ascii_artist, load_markdown

# Reuse the lab's existing pinned sources and loaders, never pip's markdown package.
_artist = load_ascii_artist()
_markdown = load_markdown()
if _artist is None or _markdown is None:
    raise RuntimeError("Galleria requires the checkout's vendored ASCII and Markdown sources")
generate_diamond = _artist.generate_diamond
generate_square = _artist.generate_square
generate_triangle = _artist.generate_triangle
get_template = _artist.get_template
list_templates = _artist.list_templates
extract_sections = _markdown.extract_sections
read_markdown = _markdown.read_markdown
save_markdown = _markdown.save_markdown

__all__ = ["build_ui"]


BASE_DIR = REPO_ROOT

TERMINAL_CSS = """
:root { --lime: #c9ffb6; --cyan: #48a8ff; --ink: #080d13; --panel: #0d1720; }
.gradio-container { max-width: 1120px !important; background: var(--ink) !important; color: var(--lime) !important; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important; }
.gradio-container::before { content: ''; display: block; border-top: 4px dashed var(--cyan); margin-bottom: 1rem; }
h1, h2, h3, p, label, .prose { color: var(--lime) !important; }
.block, .form, .wrap, .gr-box, .gr-panel, .tabitem { background: var(--panel) !important; border: 1px solid var(--cyan) !important; box-shadow: 4px 4px 0 #132b42 !important; }
button { background: #102b22 !important; border: 1px solid var(--lime) !important; color: var(--lime) !important; font-family: inherit !important; }
textarea, input, select { background: #081118 !important; color: var(--lime) !important; border-color: var(--cyan) !important; font-family: inherit !important; }
.tab-nav button { box-shadow: none !important; border: 0 !important; }
footer { display: none !important; }
"""


# ---------------------------------------------------------------------------
# ASCII Art helpers
# ---------------------------------------------------------------------------

def _get_template(name: str | None) -> str:
    """Return a predefined ASCII template."""
    if not name:
        return ""
    return get_template(name)


def _render_shape(shape: str, size: int, char: str) -> str:
    """Generate the requested shape."""
    char = char.strip() or "*"

    if shape == "Square":
        return generate_square(size, char)

    if shape == "Triangle":
        return generate_triangle(size, char)

    if shape == "Diamond":
        return generate_diamond(size, char)

    return ""


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def _extract_sections_ui(content: str) -> str:
    """Extract Markdown sections."""
    if not content.strip():
        return "No content to analyze."

    sections = extract_sections(content)
    if not sections:
        return "No headings found."

    return "\n".join(
        f"{'  ' * (sec['level'] - 1)}{'#' * sec['level']} {sec['title']}"
        for sec in sections
    )


def _read_md(filepath: str, count_hashtags: bool) -> tuple[str, str]:
    """Read Markdown file."""
    filepath = filepath.strip()

    if not filepath:
        return "", "Please specify a file path."

    result = read_markdown(filepath, count_hashtags=count_hashtags)

    if not result["success"]:
        return "", result["content"]

    info = ""
    if count_hashtags:
        info = f"Total heading '#' characters: {result['hashtag_count']}"

    return result["content"], info


def _save_md(content: str, filepath: str) -> str:
    """Save Markdown file."""
    filepath = filepath.strip()

    if not filepath:
        return "Please specify a file path."

    return save_markdown(content, filepath)


# ---------------------------------------------------------------------------
# File Viewer helpers
# ---------------------------------------------------------------------------

def _detect_kind(path: Path) -> str:
    """Detect file display type."""
    suffix = path.suffix.lower()

    if suffix == ".md":
        return "markdown"

    if suffix == ".csv":
        return "csv"

    if suffix == ".json":
        return "json"

    return "text"


def _format_json(text: str) -> str:
    """Pretty print JSON."""
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except json.JSONDecodeError:
        return text


def _load_for_display(
    label: str,
    files: Mapping[str, Path],
) -> tuple[str, str, Optional[dict]]:
    """Load file for UI display."""
    path = files.get(label)

    if path is None:
        return "text", f"[error] unknown label: {label}", None

    kind = _detect_kind(path)

    if kind == "csv":
        csv_value = _read_csv_as_table(path)

        if isinstance(csv_value, str):
            return "text", csv_value, None

        return "csv", "", csv_value

    raw = _read_text(path)

    if kind == "markdown":
        return "markdown", raw, None

    if kind == "json":
        return "text", _format_json(raw), None

    return "text", raw, None


def _read_csv_as_table(path: Path) -> str | dict:
    """Read CSV file as table."""
    raw = _read_text(path)

    if raw.startswith(("[error]", "[missing]", "[too_large]")):
        return raw

    try:
        rows = list(csv.reader(io.StringIO(raw)))
    except Exception as e:
        return f"[error] csv parse failed: {e}"

    if not rows:
        return {"headers": [], "data": []}

    return {
        "headers": rows[0],
        "data": rows[1:],
    }


def _read_text(path: Path, size_limit_bytes: int = 5 * 1024 * 1024) -> str:
    """Read text file safely."""
    if not path.exists():
        return f"[missing] {path}"

    try:
        size = path.stat().st_size
    except OSError as e:
        return f"[error] stat failed: {e}"

    if size > size_limit_bytes:
        return f"[too_large] {path} ({size} bytes)"

    for enc in ("utf-8", "utf-8-sig", "cp932", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
        except OSError as e:
            return f"[error] read failed: {e}"

    return "[error] decode failed"


def _scan_repo_files() -> Dict[str, Path]:
    """Scan repository files."""
    excluded = {"vendor", "build", "dist", "node_modules", "__pycache__"}
    return {
        path.relative_to(BASE_DIR).as_posix(): path
        for path in sorted(BASE_DIR.rglob("*"))
        if path.is_file() and not path.is_symlink()
        and path.suffix.lower() in {".txt", ".md", ".yaml", ".yml", ".sh", ".json", ".csv", ".log"}
        and not any(part.startswith(".") or part in excluded
                    for part in path.relative_to(BASE_DIR).parts)
        and path.resolve().is_relative_to(BASE_DIR.resolve())
    }


def _to_view_updates(kind: str, text_value: str = "", csv_value: Optional[dict] = None):
    """Convert file content into Gradio update objects."""
    import gradio as gr
    if kind == "markdown":
        return (
            gr.update(visible=False, value=""),
            gr.update(visible=True, value=text_value),
            gr.update(visible=False, headers=None, value=None),
        )

    if kind == "csv" and csv_value is not None:
        return (
            gr.update(visible=False, value=""),
            gr.update(visible=False, value=""),
            gr.update(
                visible=True,
                headers=csv_value["headers"],
                value=csv_value["data"],
            ),
        )

    return (
        gr.update(visible=True, value=text_value),
        gr.update(visible=False, value=""),
        gr.update(visible=False, headers=None, value=None),
    )


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def build_ui():
    """Build the optional local Gradio demo (no model or MCP server required)."""
    import gradio as gr
    files = _scan_repo_files()
    labels = list(files) or ["(no files)"]
    default_label = labels[0]

    templates = list_templates()
    default_template = templates[0] if templates else None

    def on_file_change(label: str):
        if label == "(no files)":
            return _to_view_updates("text", "ファイルが見つかりません。")

        kind, text_value, csv_value = _load_for_display(label, files)
        return _to_view_updates(kind, text_value, csv_value)

    with gr.Blocks(title="Toolcall Lab — Galleria", css=TERMINAL_CSS) as demo:
        gr.Markdown("# Toolcall Lab — Galleria")
        gr.Markdown(
            "Local tools for ASCII art, Markdown management, and checkout file viewing."
        )

        with gr.Tabs():
            with gr.Tab("ASCII Art"):
                gr.Markdown("## Dynamic Shape Generator")

                with gr.Row():
                    char_input = gr.Textbox(
                        value="*",
                        label="Character",
                        max_lines=1,
                    )
                    shape_choice = gr.Radio(
                        choices=["Square", "Triangle", "Diamond"],
                        value="Square",
                        label="Shape",
                    )
                    size_slider = gr.Slider(
                        minimum=1,
                        maximum=20,
                        value=5,
                        step=1,
                        label="Size / Height",
                    )

                shape_output = gr.Textbox(
                    label="Generated Shape",
                    lines=10,
                    interactive=False,
                    value=_render_shape("Square", 5, "*"),
                )

                shape_choice.change(
                    fn=_render_shape,
                    inputs=[shape_choice, size_slider, char_input],
                    outputs=shape_output,
                )
                size_slider.change(
                    fn=_render_shape,
                    inputs=[shape_choice, size_slider, char_input],
                    outputs=shape_output,
                )
                char_input.change(
                    fn=_render_shape,
                    inputs=[shape_choice, size_slider, char_input],
                    outputs=shape_output,
                )

                gr.Markdown("## Pre-defined Templates")

                template_choice = gr.Dropdown(
                    choices=templates,
                    value=default_template,
                    label="Template",
                )
                template_output = gr.Textbox(
                    label="Template Art",
                    lines=10,
                    interactive=False,
                    value=_get_template(default_template),
                )

                template_choice.change(
                    fn=_get_template,
                    inputs=template_choice,
                    outputs=template_output,
                )

            with gr.Tab("Markdown Manager"):
                gr.Markdown("## Save Markdown")

                md_filepath_save = gr.Textbox(
                    label="File Path (e.g. notes/my_doc.md)",
                    max_lines=1,
                )
                md_content_save = gr.Textbox(
                    label="Markdown Content",
                    lines=10,
                    placeholder="# Hello\nWrite your markdown here...",
                )
                save_status = gr.Textbox(label="Status", interactive=False)

                save_btn = gr.Button("Save")
                save_btn.click(
                    fn=_save_md,
                    inputs=[md_content_save, md_filepath_save],
                    outputs=save_status,
                )

                gr.Markdown("## Read Markdown")

                md_filepath_read = gr.Textbox(
                    label="File Path to Read",
                    max_lines=1,
                )
                count_hashtags_cb = gr.Checkbox(
                    label="Count heading '#' characters",
                    value=False,
                )
                md_content_read = gr.Textbox(
                    label="File Content",
                    lines=10,
                    interactive=False,
                )
                read_info = gr.Textbox(label="Info", interactive=False)

                read_btn = gr.Button("Read")
                read_btn.click(
                    fn=_read_md,
                    inputs=[md_filepath_read, count_hashtags_cb],
                    outputs=[md_content_read, read_info],
                )

                gr.Markdown("## Extract Sections")

                sections_input = gr.Textbox(
                    label="Paste Markdown Content",
                    lines=8,
                    placeholder="# Section 1\n## Sub-section\n...",
                )
                sections_output = gr.Textbox(
                    label="Sections Found",
                    lines=8,
                    interactive=False,
                )

                extract_btn = gr.Button("Extract Sections")
                extract_btn.click(
                    fn=_extract_sections_ui,
                    inputs=sections_input,
                    outputs=sections_output,
                )

            with gr.Tab("File Viewer"):
                gr.Markdown("## File Viewer")
                gr.Markdown(
                    "このタブは、リポジトリ内の文書をスキャンして、"
                    "プルダウンでファイル内容を表示します。"
                )

                selector = gr.Dropdown(
                    choices=labels,
                    value=default_label,
                    label="ファイル選択",
                    interactive=True,
                )
                text_view = gr.Textbox(
                    label="Text",
                    lines=28,
                    max_lines=40,
                    visible=True,
                )
                markdown_view = gr.Markdown(visible=False)
                csv_view = gr.Dataframe(
                    label="CSV",
                    visible=False,
                    wrap=True,
                    interactive=False,
                )

                selector.change(
                    fn=on_file_change,
                    inputs=selector,
                    outputs=[text_view, markdown_view, csv_view],
                )

                demo.load(
                    fn=lambda: on_file_change(default_label),
                    outputs=[text_view, markdown_view, csv_view],
                )

    return demo


if __name__ == "__main__":
    ui = build_ui()
    ui.launch(server_name="127.0.0.1", share=False)
