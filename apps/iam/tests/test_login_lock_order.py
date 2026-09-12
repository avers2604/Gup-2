from unittest import mock

from django.test import TestCase

from apps.iam import services


class CredentialLockOrderTests(TestCase):
    """Password work must not serialize all users behind account/IP advisory locks."""

    def test_locked_attempt_short_circuits_before_advisory_lock_or_password_hash(self):
        with (
            mock.patch("apps.iam.services.is_locked_out", return_value=True),
            mock.patch("apps.iam.services.is_ip_locked_out", return_value=False),
            mock.patch("apps.iam.services._lock_login_identities") as lock_identities,
            mock.patch("apps.iam.services.authenticate") as authenticate,
        ):
            with self.assertRaises(services.LoginBlocked):
                services.check_credentials(
                    None,
                    personnel_number="0802",
                    password="irrelevant",
                )

        lock_identities.assert_not_called()
        authenticate.assert_not_called()

    def test_password_verification_happens_before_advisory_lock(self):
        events = []

        def authenticate_without_match(*args, **kwargs):
            events.append("authenticate")
            return None

        def lock_identities(*args, **kwargs):
            events.append("lock")

        with (
            mock.patch("apps.iam.services.is_locked_out", return_value=False),
            mock.patch("apps.iam.services.is_ip_locked_out", return_value=False),
            mock.patch(
                "apps.iam.services.authenticate",
                side_effect=authenticate_without_match,
            ),
            mock.patch(
                "apps.iam.services._lock_login_identities",
                side_effect=lock_identities,
            ),
            mock.patch("apps.iam.services._record_login_failure"),
        ):
            result = services.check_credentials(
                None,
                personnel_number="0803",
                password="wrong",
            )

        self.assertIsNone(result)
        self.assertEqual(events, ["authenticate", "lock"])
