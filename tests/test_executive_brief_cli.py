from __future__ import annotations

import io
from pathlib import Path
from tempfile import TemporaryDirectory

from pptx import Presentation

from scripts.build_executive_brief import main


def test_executive_brief_cli_writes_five_slide_deck() -> None:
    with TemporaryDirectory(dir=Path.cwd()) as directory:
        output = Path(directory) / "brief.pptx"
        exit_code = main(["--output", str(output)])
        assert exit_code == 0
        assert output.exists()
        presentation = Presentation(io.BytesIO(output.read_bytes()))
        assert len(presentation.slides) == 5
