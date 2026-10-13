"""Browser-tab titles and the favicon, which app.py resolves from the URL before navigation."""
from PIL import Image

from shared.layout import APPS, DASHBOARD_URL, FAVICON_PATH, browser_title, title_for_path


def test_every_page_has_its_own_title_with_the_brand():
    titles = {title_for_path(p) for p in ("", DASHBOARD_URL, "feedback", *(a.url_path for a in APPS))}
    assert len(titles) == 2 + 1 + len(APPS)
    assert title_for_path("recon") == "Data Reconciliation Studio | Janota FIN"
    assert title_for_path("/workspace/") == "Workspace | Janota FIN"
    assert title_for_path("") == browser_title("", is_home=True) and title_for_path("").startswith("Janota FIN |")


def test_an_unknown_path_still_gets_a_sensible_title():
    assert title_for_path("no-such-page") == "Janota FIN"


def test_the_favicon_is_a_square_png_big_enough_to_scale_down():
    with Image.open(FAVICON_PATH) as image:
        assert image.format == "PNG" and image.width == image.height >= 64
