from django.views.generic import TemplateView


class StyleguideView(TemplateView):
    """Живой каталог компонентов — веб-порт правил из DESIGN.md (см. дополнение к ТЗ, п.3)."""

    template_name = "styleguide.html"
