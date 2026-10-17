from types import SimpleNamespace

from shared import auth
from shared.auth import AccessRules, evaluate, load_access, missing_auth_settings

FULL_AUTH = {k: "x" for k in auth.REQUIRED_AUTH_KEYS}


def test_allow_list_is_read_case_and_at_sign_insensitively():
    rules = load_access({"access": {"emails": ["Pat@Example.com "], "domains": "@Example.org"}})
    assert rules.configured and rules.allows("PAT@example.COM") and rules.allows("x@example.org")
    assert not rules.allows("pat@example.org.evil.com") and not rules.allows("nobody") and not rules.allows("")


def test_no_access_block_or_an_unreadable_one_means_nobody_is_allowed():
    for secrets in (None, {}, {"access": {}}, {"access": {"emails": []}}):
        assert not load_access(secrets).configured

    class Broken:
        def __contains__(self, key):
            raise OSError("no secrets file")

    assert not load_access(Broken()).configured


def test_sign_in_is_off_without_an_auth_block_and_names_missing_keys_otherwise():
    assert missing_auth_settings(None) is None and missing_auth_settings({}) is None
    assert missing_auth_settings({"auth": FULL_AUTH}) == []
    partial = {"auth": {**FULL_AUTH, "client_secret": "", "cookie_secret": None}}
    assert missing_auth_settings(partial) == ["cookie_secret", "client_secret"]


def test_every_state_is_reported_with_the_reason():
    rules = AccessRules(emails=frozenset({"pat@example.com"}))
    u = lambda **kw: SimpleNamespace(**{"is_logged_in": True, "email": "pat@example.com", "name": "Pat", **kw})  # noqa: E731
    assert evaluate(u(), rules).status == auth.VERIFIED
    assert evaluate(u(email="eve@x.com"), rules).status == auth.NOT_ALLOWED
    assert evaluate(u(), AccessRules()).status == auth.NO_ALLOW_LIST
    assert evaluate(u(email_verified=False), rules).status == auth.EMAIL_UNVERIFIED
    assert evaluate(SimpleNamespace(is_logged_in=False), rules).status == auth.SIGNED_OUT
    assert not evaluate(u(email="eve@x.com"), rules).verified and evaluate(u(email="eve@x.com"), rules).signed_in
    assert set(auth.EXPLANATIONS) == {auth.NOT_ALLOWED, auth.NO_ALLOW_LIST, auth.EMAIL_UNVERIFIED}
