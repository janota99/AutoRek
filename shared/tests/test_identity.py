from types import SimpleNamespace

from shared.auth import AccessRules
from shared.identity import UNSIGNED_NAME, identity_from_user

RULES = AccessRules(emails=frozenset({"pat@example.com"}), domains=frozenset({"example.org"}))


def user(**kw):
    return SimpleNamespace(**{"is_logged_in": True, "email": "pat@example.com", "name": "Pat", **kw})


def test_an_allowed_signed_in_user_is_verified_and_prefers_email():
    who = identity_from_user(user(), RULES)
    assert (who.name, who.verified, who.label) == ("pat@example.com", True, "pat@example.com")


def test_a_domain_on_the_allow_list_counts():
    assert identity_from_user(user(email="sam@example.org"), RULES).verified


def test_signed_in_but_not_allowed_keeps_the_name_but_is_unverified():
    for rules in (RULES, AccessRules()):  # off the list, and no list at all
        who = identity_from_user(user(email="eve@other.com"), rules)
        assert (who.name, who.verified) == ("eve@other.com", False) and who.label.endswith("(unverified)")
    assert identity_from_user(user(), None).verified is False  # no rules passed: nobody is verified


def test_an_unverified_email_is_never_verified_even_on_the_list():
    assert identity_from_user(user(email_verified=False), RULES).verified is False
    assert identity_from_user(user(email_verified=True), RULES).verified is True


def test_name_is_used_when_there_is_no_email():
    who = identity_from_user(user(email=""), RULES)
    assert (who.name, who.verified) == ("Pat", False)  # no email to check against the list


def test_logged_out_or_nameless_is_unsigned_and_unverified():
    for u in (SimpleNamespace(is_logged_in=False, email="pat@example.com"), SimpleNamespace(is_logged_in=True),
              SimpleNamespace(), None):
        who = identity_from_user(u, RULES)
        assert (who.name, who.verified) == (UNSIGNED_NAME, False) and who.label.endswith("(unverified)")


def test_an_object_that_raises_is_treated_as_unsigned():
    class Boom:
        @property
        def is_logged_in(self):
            raise RuntimeError("no authentication configured")

    assert identity_from_user(Boom(), RULES).verified is False
