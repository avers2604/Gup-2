"""Small HTTP views for version-relation mutations with race-aware feedback."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views import View

from . import permissions, services
from .models import DocumentRelation


class DocumentRelationDeleteView(LoginRequiredMixin, View):
    """Remove a relation and distinguish a concurrent prior removal."""

    def post(self, request, pk, relation_id):
        document = get_object_or_404(
            permissions.visible_documents(request.user).select_related("issuer_dept"),
            pk=pk,
        )
        if not permissions.can_manage_relations(request.user, document):
            raise Http404

        relation = get_object_or_404(
            DocumentRelation,
            pk=relation_id,
            from_document=document,
        )
        try:
            removed = services.remove_relation(actor=request.user, relation=relation)
        except PermissionDenied as error:
            messages.error(request, str(error))
        else:
            if removed:
                messages.success(request, "Связь версионности снята.")
            else:
                messages.warning(
                    request,
                    "Связь уже была снята другим пользователем.",
                )

        return HttpResponseRedirect(reverse("documents:detail", args=[document.pk]))
