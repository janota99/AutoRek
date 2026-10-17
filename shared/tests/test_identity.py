from types import SimpleNamespace

from shared.identity import UNSIGNED_NAME, identity_from_user


def test_logged_in_user_is_verified_and_prefers_email():
    who = identity_from_user(SimpleNamespace(is_logged_in=True, email="pat@example.com", name="Pat"))
    assert (who.name, who.verified, who.label) == ("pat@example.com", True, "pat@example.com")


def test_name_is_used_when_there_is_no_email():
    assert identity_from_user(SimpleNamespace(is_logged_in=True, email="", name="Pat")).name == "Pat"


def test_logged_out_or_nameless_is_unsigned_and_unverified():
    for user in (SimpleNamespace(is_logged_in=False, email="x@y.z"), SimpleNamespace(is_logged_in=True),
                 SimpleNamespace(), None):
        who = identity_from_user(user)
        assert (who.name, who.verified) == (UNSIGNED_NAME, False) and who.label.endswith("(unverified)")


def test_an_object_that_raises_is_treated_as_unsigned():
    class Boom:
        @property
        def is_logged_in(self):
            raise RuntimeError("no authentication configured")

    assert identity_from_user(Boom()).verified is False
