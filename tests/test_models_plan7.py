"""Plan 7 contract additions (ndb#132): per-page scores, links to a case's files, files_base.

All three are optional, so every case published before plan 7 stays valid.
"""

import pytest
from pydantic import ValidationError

from tests.test_models_cases import make_case, make_index

HF = "https://huggingface.co/datasets/arthrod/neurotic_docx_bench/resolve/7fce414d543ed33b209c6e49277f6667dee7f7b4/"


def test_old_cases_need_none_of_the_new_fields() -> None:
    c = make_case()
    assert c.page_scores == {} and c.files == {}
    assert make_index().files_base is None


def test_page_scores_one_per_shown_page() -> None:
    c = make_case(page_scores={"jubarte": [98.2]})
    assert c.page_scores["jubarte"] == [98.2]


def test_page_scores_may_be_null_for_a_page_that_could_not_be_scored() -> None:
    two = {
        "reference": ["~/d/ref/page_001.webp", "~/d/ref/page_002.webp"],
        "jubarte": ["~/d/jub/page_001.webp", "~/d/jub/page_002.webp"],
    }
    c = make_case(shown=[1, 2], pages=two, page_scores={"jubarte": [98.2, None]})
    assert c.page_scores["jubarte"][1] is None


def test_page_scores_longer_than_the_shown_pages_are_refused() -> None:
    with pytest.raises(ValidationError, match="page_scores"):
        make_case(shown=[1], page_scores={"jubarte": [98.2, 97.0]})


def test_page_scores_out_of_range_are_refused() -> None:
    with pytest.raises(ValidationError):
        make_case(page_scores={"jubarte": [101.0]})


def test_page_scores_lane_needs_a_version_label() -> None:
    with pytest.raises(ValidationError):
        make_case(page_scores={"docxodus": [50.0]})


def test_files_are_relative_asset_paths() -> None:
    c = make_case(files={"jubarte": "~/outputs/convert_860/jubarte_0.17.0/doc-001.pdf"})
    assert c.files["jubarte"].endswith("doc-001.pdf")
    with pytest.raises(ValidationError):
        make_case(files={"jubarte": "https://elsewhere.example/doc-001.pdf"})
    with pytest.raises(ValidationError):
        make_case(files={"jubarte": "~/../escape.pdf"})


def test_files_lane_needs_a_version_label() -> None:
    with pytest.raises(ValidationError):
        make_case(files={"docxodus": "~/outputs/x.pdf"})


def test_files_base_is_an_https_directory() -> None:
    assert make_index(files_base=HF).files_base == HF
    with pytest.raises(ValidationError, match="files_base"):
        make_index(files_base="http://insecure.example/")
    with pytest.raises(ValidationError, match="files_base"):
        make_index(files_base="https://huggingface.co/no-trailing-slash")
