"""«Избранное»: личная пометка пользователя на документе.

Проверяется не только то, что звезда ставится, но и три границы, на
которых такая функция обычно и ломается: гриф ДСП, чужие закладки и
поле возврата.
"""
from django.test import Client, TestCase
from django.urls import reverse

from apps.documents.models import DocumentBookmark, NormativeDocument
from apps.iam.models import User

from .factories import make_document
from .test_permissions import PASSWORD, make_user


class BookmarkToggleTests(TestCase):
    def setUp(self):
        self.user = make_user(personnel_number="0910")
        self.client_ = Client()
        self.client_.login(personnel_number="0910", password=PASSWORD)
        self.document = make_document(
            reg_number="301-п", status=NormativeDocument.Status.ACTIVE
        )
        self.url = reverse("documents:bookmark_toggle", args=[self.document.pk])

    def test_first_post_adds_the_bookmark(self):
        self.client_.post(self.url)

        self.assertTrue(
            DocumentBookmark.objects.filter(user=self.user, document=self.document).exists()
        )

    def test_second_post_removes_it(self):
        self.client_.post(self.url)
        self.client_.post(self.url)

        self.assertFalse(
            DocumentBookmark.objects.filter(user=self.user, document=self.document).exists()
        )

    def test_get_does_not_change_anything(self):
        """Изменение состояния по GET — это звёзды, расставленные
        предзагрузкой ссылок в браузере."""
        response = self.client_.get(self.url)

        self.assertEqual(response.status_code, 405)
        self.assertFalse(DocumentBookmark.objects.exists())

    def test_anonymous_is_redirected_to_login(self):
        response = Client().post(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("iam:login"), response["Location"])
        self.assertFalse(DocumentBookmark.objects.exists())


class BookmarkRespectsRestrictedAccessTests(TestCase):
    """Закладка не должна становиться способом нащупать документ «ДСП».

    Ответ 404 здесь — то же решение, что и у карточки: 403 подтвердил бы
    существование документа с этим идентификатором.
    """

    def setUp(self):
        self.restricted = make_document(
            reg_number="907-и",
            status=NormativeDocument.Status.ACTIVE,
            access_level=NormativeDocument.AccessLevel.RESTRICTED,
        )
        self.url = reverse("documents:bookmark_toggle", args=[self.restricted.pk])

    def test_user_without_clearance_gets_404_and_no_bookmark(self):
        make_user(personnel_number="0911", dsp_access=False)
        client = Client()
        client.login(personnel_number="0911", password=PASSWORD)

        response = client.post(self.url)

        self.assertEqual(response.status_code, 404)
        self.assertFalse(DocumentBookmark.objects.exists())

    def test_user_with_clearance_can_bookmark_it(self):
        make_user(personnel_number="0912", dsp_access=True)
        client = Client()
        client.login(personnel_number="0912", password=PASSWORD)

        client.post(self.url)

        self.assertTrue(DocumentBookmark.objects.filter(document=self.restricted).exists())

    def test_losing_clearance_hides_the_document_but_keeps_the_bookmark(self):
        """Закладка переживает отзыв допуска намеренно.

        Удалять её при отзыве нельзя: возврат допуска молча терял бы
        работу пользователя. Из выдачи документ при этом исчезает — её
        фильтрует visible_documents(), а не наличие закладки.
        """
        user = make_user(personnel_number="0913", dsp_access=True)
        client = Client()
        client.login(personnel_number="0913", password=PASSWORD)
        client.post(self.url)

        user.dsp_access = False
        user.save(update_fields=["dsp_access"])
        client.login(personnel_number="0913", password=PASSWORD)
        listing = client.get(reverse("documents:list"), {"only_bookmarked": "on"})

        self.assertTrue(DocumentBookmark.objects.filter(user=user).exists())
        self.assertNotContains(listing, "907-и")


class BookmarkIsPersonalTests(TestCase):
    def test_one_users_star_is_not_visible_to_another(self):
        document = make_document(reg_number="205-п", status=NormativeDocument.Status.ACTIVE)
        make_user(personnel_number="0920")
        make_user(personnel_number="0921")

        first = Client()
        first.login(personnel_number="0920", password=PASSWORD)
        first.post(reverse("documents:bookmark_toggle", args=[document.pk]))

        second = Client()
        second.login(personnel_number="0921", password=PASSWORD)
        listing = second.get(reverse("documents:list"), {"only_bookmarked": "on"})

        self.assertNotContains(listing, "205-п")

    def test_only_bookmarked_filter_narrows_the_registry(self):
        starred = make_document(reg_number="206-р", status=NormativeDocument.Status.ACTIVE)
        make_document(reg_number="207-р", status=NormativeDocument.Status.ACTIVE)
        make_user(personnel_number="0922")
        client = Client()
        client.login(personnel_number="0922", password=PASSWORD)
        client.post(reverse("documents:bookmark_toggle", args=[starred.pk]))

        listing = client.get(reverse("documents:list"), {"only_bookmarked": "on"})

        self.assertContains(listing, "206-р")
        self.assertNotContains(listing, "207-р")


class BookmarkReturnUrlTests(TestCase):
    """Поле next — классическое место для открытого редиректа."""

    def setUp(self):
        self.user = make_user(personnel_number="0930", role=User.Role.METHODIST)
        self.client_ = Client()
        self.client_.login(personnel_number="0930", password=PASSWORD)
        self.document = make_document(
            reg_number="143-п", status=NormativeDocument.Status.ACTIVE
        )
        self.url = reverse("documents:bookmark_toggle", args=[self.document.pk])

    def test_internal_next_is_honoured(self):
        target = reverse("documents:list") + "?status=active&page=2"

        response = self.client_.post(self.url, {"next": target})

        self.assertEqual(response["Location"], target)

    def test_external_next_is_ignored(self):
        response = self.client_.post(self.url, {"next": "https://example.invalid/phish"})

        self.assertEqual(response["Location"], reverse("documents:list"))

    def test_protocol_relative_next_is_ignored(self):
        """`//host` — адрес на чужой домен, хотя и выглядит как путь."""
        response = self.client_.post(self.url, {"next": "//example.invalid/phish"})

        self.assertEqual(response["Location"], reverse("documents:list"))
