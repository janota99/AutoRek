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
from typing import Callable, Iterable, Optional

import streamlit as st

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class SampleUpload(io.BytesIO):
    """A sample file that quacks like Streamlit's UploadedFile (name, size, type, file_id,
    getvalue), so downstream code cannot tell it from a real upload."""

    def __init__(self, filename: str, data: bytes):
        super().__init__(data)
        self.name = f"Sample - {filename}"
        self.size = len(data)
        self.type = mimetypes.guess_type(filename)[0] or XLSX_MIME
        self.file_id = f"sample:{filename}"
        self.is_sample = True


def _flag(key: str) -> str:
    return f"_sample_loaded_{key}"


def _seed_demo(key: str) -> None:
    """The Dashboard's "Try demo" link opens an app at ?demo=1. Load that uploader's sample once per
    session, so the workflow opens populated; "Clear sample" and real uploads still work as usual."""
    seeded = f"_demo_seeded_{key}"
    if st.query_params.get("demo") == "1" and not st.session_state.get(seeded):
        st.session_state[seeded] = True
        st.session_state[_flag(key)] = True


def _sample_bytes(key: str, sample_file, sample_builder, cache_token):
    """(filename, bytes) for a sample. A built sample is cached in session state under `cache_token`,
    so its bytes (and therefore the file digest downstream code keys on) stay identical across
    reruns; it is rebuilt only when the token changes."""
    if sample_builder is None:
        return sample_file, (SAMPLE_DIR / sample_file).read_bytes()
    slot = f"_sample_bytes_{key}"
    cached = st.session_state.get(slot)
    if cached is None or cached[0] != cache_token:
        filename, data = sample_builder()
        cached = (cache_token, filename, data)
        st.session_state[slot] = cached
    return cached[1], cached[2]


def sample_uploader(
    label: str,
    *,
    key: str,
    types: list[str],
    sample_file: Optional[str] = None,
    sample_builder: Optional[Callable[[], tuple[str, bytes]]] = None,
    cache_token: Optional[str] = None,
    on_clear: Optional[Callable[[], None]] = None,
    help: Optional[str] = None,
    label_visibility: str = "collapsed",
    show_badge: bool = True,
):
    """st.file_uploader plus a "Use Sample Data" button.

    The sample is either a file in sample_data/ (`sample_file`) or built on demand
    (`sample_builder` returning (filename, bytes), with `cache_token` naming its inputs).
    Returns (file, is_sample). `file` is the real upload if there is one, else the sample when it
    has been loaded, else None. A real upload replaces the sample. `on_clear` runs whenever a
    sample is cleared or replaced, for callers whose page state absorbed the sample's contents.
    """
    uploaded = st.file_uploader(
        label, type=types, key=key, help=help, label_visibility=label_visibility
    )
    flag = _flag(key)

    def clear():
        st.session_state.pop(flag, None)
        if on_clear is not None:
            on_clear()

    if uploaded is not None:
        if st.session_state.get(flag):
            clear()
        return uploaded, False

    _seed_demo(key)
    if st.session_state.get(flag):
        filename, data = _sample_bytes(key, sample_file, sample_builder, cache_token)
        sample = SampleUpload(filename, data)
        if show_badge:
            st.markdown(
                f"<span class='sample-badge'>&#9679; Demo data</span> "
                f"<span class='sample-badge-name'>Sample loaded: {sample.name}</span>",
                unsafe_allow_html=True,
            )
        st.button(
            "Clear sample", key=f"{key}_clear_sample", type="secondary", width="content",
            icon=":material/close:", on_click=clear,
        )
        return sample, True

    st.button(
        "Use Sample Data", key=f"{key}_use_sample", type="secondary", width="content",
        icon=":material/science:", help="Loads a synthetic demo file in place of an upload.",
        on_click=st.session_state.__setitem__, args=(flag, True),
    )
    return None, False


def sample_downloads(files: Iterable[tuple], *, key: str) -> None:
    """A "Download Sample Templates" drawer. Each entry is (button label, filename in sample_data/),
    or (button label, filename, bytes) for a sample built on the fly."""
    with st.expander("Download Sample Templates"):
        st.caption("Synthetic files in the expected layout, for trying the tool out. They contain no real data.")
        for i, entry in enumerate(files):
            label, filename = entry[0], entry[1]
            data = entry[2] if len(entry) > 2 else (SAMPLE_DIR / filename).read_bytes()
            st.download_button(
                label, data=data, file_name=filename,
                mime=mimetypes.guess_type(filename)[0] or XLSX_MIME,
                key=f"{key}_dl_{i}", icon=":material/download:", width="content",
            )
