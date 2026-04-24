import click

from bidiq.enrich import enrich_pdfs
from bidiq.ingest import ingest_cmd


@click.group()
def main():
    """Bid IQ - intelligent bid processing toolkit."""
    pass


@main.group()
def kb():
    """Knowledge base commands."""
    pass


kb.add_command(ingest_cmd)


@kb.command()
@click.option(
    "--input-dir",
    default=".",
    type=click.Path(exists=True),
    help="Directory containing PDFs to process.",
)
@click.option(
    "--output-dir",
    default="kb_output",
    type=click.Path(),
    help="Directory to write extracted JSON files.",
)
@click.option(
    "--model",
    default="claude-sonnet-4-20250514",
    help="Claude model to use for vision extraction.",
)
@click.option(
    "--max-concurrent",
    default=2,
    type=int,
    help="Max concurrent PDF processing tasks.",
)
@click.option(
    "--dpi",
    default=150,
    type=int,
    help="DPI for rendering PDF pages to images.",
)
def enrich(input_dir, output_dir, model, max_concurrent, dpi):
    """Extract data from image-only PDFs using Claude vision."""
    enrich_pdfs(
        input_dir=input_dir,
        output_dir=output_dir,
        model=model,
        max_concurrent=max_concurrent,
        dpi=dpi,
    )


@main.command()
@click.option("--port", default=5000, type=int, help="Port to run the web server on.")
def ui(port):
    """Launch the Bid IQ web interface."""
    from bidiq.web import app

    click.echo(f"Starting Bid IQ UI at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
