from functools import partial

from django import forms
from django.views.generic.edit import FormMixin
from django.core.paginator import Paginator
from django.utils.text import capfirst

ALL_KEY = "All"

class SearchForm(forms.Form):
    q = forms.CharField(
        required=True,
        label="Keywords",
        widget=forms.TextInput(attrs={"type": "search", 'style': "width:400px"}),
    )
    
    def search(self):
        filters = dict([(key, val) for key, val in self.cleaned_data.items()
                        if key != 'q' and val != ALL_KEY])
        
        return self.index.objects.filter(**filters).search(self.cleaned_data["q"])

def field_choices(index, name):
    yield ALL_KEY, ALL_KEY
    for item in index.objects.all().values_list(name, flat=True).distinct():
        yield item, item if item else "Global"

def serchform_choice_field(field, widget_attrs={},**kwargs):
    attrs = dict(label=capfirst(field.verbose_name),
                 widget = forms.Select(**widget_attrs), 
                 choices = partial(field_choices, field.model, field.name), 
                 required = False)
    attrs.update(**kwargs)
    return forms.ChoiceField(**attrs)
    
def searchform_factory(index, base=SearchForm, 
                       formfield_callback=serchform_choice_field,
                       formfield_attrs={}):
    class_name = index.__name__ + 'SearchForm'
    attrs={
        'index':index
        }
    for field in index._meta.fields:
        if field.db_index:
            if formfield_callback:
                attrs[field.name]=formfield_callback(field, **formfield_attrs)
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
