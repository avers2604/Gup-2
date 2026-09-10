from django.test import RequestFactory, SimpleTestCase, override_settings
from apps.core.limits import client_ip


class TrustedProxyTests(SimpleTestCase):
    @override_settings(TRUSTED_PROXIES=[])
    def test_untrusted_client_cannot_override_ip(self):
        request = RequestFactory().get("/", REMOTE_ADDR="192.0.2.1", HTTP_X_FORWARDED_FOR="1.2.3.4")
        self.assertEqual(client_ip(request), "192.0.2.1")

    @override_settings(TRUSTED_PROXIES=["10.0.0.0/8"])
    def test_walks_from_trusted_edge_to_first_untrusted_hop(self):
        request = RequestFactory().get("/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="1.2.3.4, 192.0.2.2, 10.0.0.2")
        self.assertEqual(client_ip(request), "192.0.2.2")

    @override_settings(TRUSTED_PROXIES=["10.0.0.0/8"])
    def test_malformed_forwarding_falls_back_to_peer(self):
        request = RequestFactory().get("/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="invalid")
        self.assertEqual(client_ip(request), "10.0.0.1")
