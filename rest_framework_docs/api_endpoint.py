import json
import inspect

from django.contrib.admindocs.views import simplify_regex
from rest_framework.viewsets import ModelViewSet
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.encoding import force_str, force_text
from django.utils.functional import Promise

from rest_framework.serializers import BaseSerializer
from rest_framework.serializers import PrimaryKeyRelatedField

VIEWSET_METHODS = {
    'List': ['get', 'post'],
    'Instance': ['get', 'put', 'patch', 'delete'],
}


class LazyEncoder(DjangoJSONEncoder):
    def default(self, obj):
        if isinstance(obj, Promise):
            return force_text(obj)
        return super(LazyEncoder, self).default(obj)


class ApiEndpoint(object):

    def __init__(self, pattern, parent_regex=None, drf_router=None):
        self.drf_router = drf_router
        self.pattern = pattern
        self.callback = pattern.callback
        # self.name = pattern.name
        self.docstring = self.__get_docstring__()
        self.name_parent = simplify_regex(parent_regex).strip('/') if parent_regex else None
        self.path = self.__get_path__(parent_regex)
        self.allowed_methods = self.__get_allowed_methods__()
        # self.view_name = pattern.callback.__name__
        self.errors = None
        self.serializer_class = self.__get_serializer_class__()
        if self.serializer_class:
            self.serializer = self.__get_serializer__()
            self.fields = self.__get_serializer_fields__(self.serializer)
            self.fields_json = self.__get_serializer_fields_json__()
            self.read_only_fields = self.__get_serializer_read_only_fields__()
            self.write_only_fields = self.__get_serializer_write_only_fields__()

        self.permissions = self.__get_permissions_class__()

    def __get_path__(self, parent_regex):
        if parent_regex:
            return "/{0}{1}".format(self.name_parent, simplify_regex(self.pattern.regex.pattern))
        return simplify_regex(self.pattern.regex.pattern)

    def is_method_allowed(self, callback_cls, method_name):
        has_attr = hasattr(callback_cls, method_name)
        viewset_method = (issubclass(callback_cls, ModelViewSet) and
                          method_name in VIEWSET_METHODS.get(self.callback.suffix, []))

        return has_attr or viewset_method

    def __get_allowed_methods__(self):
        viewset_methods = []
        if self.drf_router:
            for prefix, viewset, basename in self.drf_router.registry:
                if self.callback.cls != viewset:
                    continue

                lookup = self.drf_router.get_lookup_regex(viewset)
                routes = self.drf_router.get_routes(viewset)

                for route in routes:

                    # Only actions which actually exist on the viewset will be bound
                    mapping = self.drf_router.get_method_map(viewset, route.mapping)
                    if not mapping:
                        continue

                    # Build the url pattern
                    regex = route.url.format(
                        prefix=prefix,
                        lookup=lookup,
                        trailing_slash=self.drf_router.trailing_slash
                    )
                    if self.pattern.regex.pattern == regex:
                        funcs, viewset_methods = zip(
                            *[(mapping[m], m.upper())
                              for m in self.callback.cls.http_method_names
                              if m in mapping]
                        )
                        viewset_methods = list(viewset_methods)
                        if len(set(funcs)) == 1:
                            self.docstring = inspect.getdoc(getattr(self.callback.cls, funcs[0]))

        view_methods = [force_str(m).upper()
                        for m in self.callback.cls.http_method_names
                        if self.is_method_allowed(self.callback.cls, m)]
        return sorted(viewset_methods + view_methods)

    def __get_docstring__(self):
        return inspect.getdoc(self.callback)

    def __get_permissions_class__(self):
        for perm_class in self.pattern.callback.cls.permission_classes:
            return perm_class.__name__

    def __get_serializer__(self):
        try:
            return self.serializer_class()
        except KeyError as e:
            self.errors = e

    def __get_serializer_class__(self):
        if hasattr(self.callback.cls, 'serializer_class'):
            return self.callback.cls.serializer_class

        if hasattr(self.callback.cls, 'get_serializer_class'):
            return self.callback.cls.get_serializer_class(self.pattern.callback.cls())

    def __get_serializer_fields__(self, serializer):
        fields = []

        if hasattr(serializer, 'get_fields'):
            for key, field in serializer.get_fields().items():
                to_many_relation = True if hasattr(field, 'many') else False
                sub_fields = []

                if to_many_relation:
                    sub_fields = self.__get_serializer_fields__(field.child) if isinstance(field, BaseSerializer) else None
                else:
                    sub_fields = self.__get_serializer_fields__(field) if isinstance(field, BaseSerializer) else None

                fields.append({
                    "name": key,
                    "type": str(field.__class__.__name__),
                    "sub_fields": sub_fields,
                    "required": field.required,
                    "to_many_relation": to_many_relation,
                    "help_text": field.help_text or '',
                    "choices": self.__get_field_choices(field),
                })
            # FIXME:
            # Show more attibutes of `field`?

        return fields

    def __get_field_choices(self, field):
        """If it's a related field returns an one element list with the
        related model. If the field has choices returns the
        choices. Else returns `None`

        """
        if isinstance(field, PrimaryKeyRelatedField):
            return ['Related field ' + str(field.queryset.model)]

        if hasattr(field, 'choices'):
            # Force choice keys to be a string or `json.dumps` fails
            # This happens when using django-timezone-field where
            # choices are a <pytz.timezone> object.
            if isinstance(field.choices, dict):
                if not all(map(lambda x: isinstance(x, str), field.choices.keys())):
                    choices = dict([(str(key), field.choices[key]) for key in field.choices.keys()])
                    return choices
            return field.choices

    def __get_serializer_fields_json__(self):
        # FIXME:
        # Return JSON or not?
        return json.dumps(self.fields, cls=LazyEncoder)

    def __get_serializer_read_only_fields__(self):
        fields = []
        meta = getattr(self.serializer, 'Meta', None)

        if meta:
            fields = getattr(meta, 'read_only_fields', None)
            return fields

    def __get_serializer_write_only_fields__(self):
        fields = []
        meta = getattr(self.serializer, 'Meta', None)

        if meta:
            for key, value in getattr(meta, 'extra_kwargs', {}).items():
                if 'write_only' in value:
                    fields.append(key)
            return fields
