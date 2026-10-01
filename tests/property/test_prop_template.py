import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.services.template_service import TemplateVariableMismatchError, render_template

names = st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=8)
values = st.text(alphabet=st.characters(blacklist_characters="{}"), max_size=20)


@given(variables=st.dictionaries(names, values, min_size=1, max_size=10))
def test_prop_render_substitutes_all_placeholders(variables):
    """Feature: alert-system, Property 8: 템플릿 플레이스홀더 치환 라운드트립"""
    body = " | ".join(f"{{{{{name}}}}}" for name in variables)
    template = {"title": None, "body": body}

    rendered = render_template(template, variables)["body"]

    assert rendered == " | ".join(variables.values())
    assert "{{" not in rendered


@given(
    placeholders=st.sets(names, max_size=8),
    provided=st.sets(names, max_size=8),
)
def test_prop_mismatched_variables_are_rejected(placeholders, provided):
    """Feature: alert-system, Property 9: 변수 불일치 시 알림 전송 거부"""
    template = {"title": None, "body": " ".join(f"{{{{{p}}}}}" for p in placeholders)}
    variables = {name: "v" for name in provided}

    if placeholders == provided:
        render_template(template, variables)
        return

    with pytest.raises(TemplateVariableMismatchError) as exc:
        render_template(template, variables)
    assert set(exc.value.missing) == placeholders - provided
    assert set(exc.value.extra) == provided - placeholders
