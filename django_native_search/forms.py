from functools import partial

from django import forms
from django.views.generic.edit import FormMixin
from django.core.paginator import Paginator
from django.utils.text import capfirst

ALL_VALUE = "all"

class SearchForm(forms.Form):
    q = forms.CharField(
        required=True,
        label="Keywords",
        widget=forms.TextInput(attrs={"type": "search", 'style': "width:400px"}),
    )
    
    def clean(self):
        for key in list(self.cleaned_data):
            if key not in self.data:
                del self.cleaned_data[key]
        return super().clean()
    
    def search(self):
        filters = dict([(key if isinstance(val, str) else f"{key}__in", val) 
                        for key, val in self.cleaned_data.items()
                        if key != 'q' and val != ALL_VALUE])
        
        return self.index.objects.filter(**filters).search(self.cleaned_data["q"])

def field_values(field):
    return list(field.model.objects.all().values_list(field.name, flat=True).distinct())

def field_choices(field, empty_label, all_label):
    if all_label:
        yield ALL_VALUE, all_label
    for item in field_values(field):
        yield item, item if item else empty_label

def searchform_choice_field(field, formfield_class, 
                           empty_label="---------", all_label="All", 
                           widget_attrs={}, **kwargs):
    attrs = dict(label=capfirst(field.verbose_name),
                 widget = formfield_class.widget(**widget_attrs), 
                 choices = partial(field_choices, field, empty_label, all_label), 
                 required = False)
    
    if all_label:
        attrs.setdefault("initial", ALL_VALUE)
    attrs.update(**kwargs)
    return formfield_class(**attrs)

def searchform_single_choice_field(field, **kwargs):
    return searchform_choice_field(field, forms.TypedChoiceField, **kwargs)

def searchform_multiple_choice_field(field, **kwargs):
    kwargs.setdefault("all_label", None)
    kwargs["empty_value"] = None
    return searchform_choice_field(field, forms.TypedMultipleChoiceField, **kwargs)


def searchform_factory(index, base=SearchForm, 
                       formfield_callback=searchform_multiple_choice_field,
                       formfield_attrs={}):
    class_name = index.__name__ + 'SearchForm'
    attrs={
        'index':index
        }
    for field in index._meta.fields:
        if field.db_index:
            if formfield_callback:
                formfield = formfield_callback(field, **formfield_attrs)
                if formfield:
                    attrs[field.name]= formfield
            else: 
                attrs[field.name]=field.formfield(**formfield_attrs)
    return type(class_name, (base,), attrs)

class GetFormMixin(FormMixin):
    def get(self, request, *args, **kwargs):
        form=self.get_form()
        
        if not form.is_bound:
            return super().get(request, *args, **kwargs)
        if form.is_valid():
            return self.form_valid(form)
        else:
            return self.form_invalid(form)
        
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.request.GET:
            kwargs["data"]=self.request.GET
        return kwargs

class SearchFormMixin(GetFormMixin):
    def form_valid(self, form):
        results=form.search().prefetch_matches()
        
        paginator = Paginator(results, 25)
        paginator.current_page = paginator.get_page(self.request.GET.get('page'))
        
        return self.render_to_response({
            **self.get_context_data(),
            'paginator':paginator
            })
