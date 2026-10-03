"""Sample-data helpers shared by the apps: a "Use Sample Data" button beside an upload,
and a "Download Sample Templates" drawer.

The sample files live in the repo-level `sample_data/` folder and are synthetic (see
`sample_data/build_samples.py`). Choosing a sample never touches the user's real uploads:
a real upload always wins, and the sample is held only as a session-state flag.
"""

from __future__ import annotations

import io
import mimetypes
from pathlib import Path
from typing import Iterable, Optional

import streamlit as st

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class SampleUpload(io.BytesIO):
    """A sample file that quacks like Streamlit's UploadedFile (name, size, type, file_id,
    getvalue), so downstream code cannot tell it from a real upload."""

    def __init__(self, path: Path):
        super().__init__(path.read_bytes())
        self.name = f"Sample - {path.name}"
        self.size = len(self.getvalue())
        self.type = mimetypes.guess_type(path.name)[0] or XLSX_MIME
        self.file_id = f"sample:{path.name}"
        self.is_sample = True


def _flag(key: str) -> str:
    return f"_sample_loaded_{key}"


def sample_uploader(
    label: str,
    *,
    key: str,
    sample_file: str,
    types: list[str],
    help: Optional[str] = None,
    show_badge: bool = True,
):
    """st.file_uploader plus a "Use Sample Data" button.

    Returns (file, is_sample). `file` is the real upload if there is one, else the sample
    when it has been loaded, else None. A real upload clears the sample.
    """
    uploaded = st.file_uploader(
        label, type=types, key=key, help=help, label_visibility="collapsed"
    )
    flag = _flag(key)
    if uploaded is not None:
        st.session_state.pop(flag, None)
        return uploaded, False

    if st.session_state.get(flag):
        sample = SampleUpload(SAMPLE_DIR / sample_file)
        if show_badge:
            st.markdown(
                f"<span class='sample-badge'>&#9679; Sample Loaded</span> "
                f"<span class='sample-badge-name'>{sample.name}</span>",
                unsafe_allow_html=True,
            )
        st.button(
            "Clear sample", key=f"{key}_clear_sample", type="secondary", width="content",
            icon=":material/close:", on_click=st.session_state.pop, args=(flag, None),
        )
        return sample, True

    st.button(
        "Use Sample Data", key=f"{key}_use_sample", type="secondary", width="content",
        icon=":material/science:", help=f"Loads the synthetic file {sample_file}.",
        on_click=st.session_state.__setitem__, args=(flag, True),
    )
    return None, False


def sample_downloads(files: Iterable[tuple[str, str]], *, key: str) -> None:
    """A "Download Sample Templates" drawer. `files` is (button label, filename in sample_data/)."""
    with st.expander("Download Sample Templates"):
        st.caption("Synthetic files in the expected layout, for trying the tool out. They contain no real data.")
        for i, (label, filename) in enumerate(files):
            path = SAMPLE_DIR / filename
            st.download_button(
                label, data=path.read_bytes(), file_name=filename,
                mime=mimetypes.guess_type(filename)[0] or XLSX_MIME,
                key=f"{key}_dl_{i}", icon=":material/download:", width="content",
            )
