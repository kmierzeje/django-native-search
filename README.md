# django-native-search

[![PyPI](https://img.shields.io/pypi/v/django-native-search.svg)](https://pypi.org/project/django-native-search/)

django-native-search implements basic full-text search engine for Django models.

The engine itself uses Django ORM to manage its index, so no additional backend is needed for searching to work. Just create a model for index, run `makemigrations` and `migrate` and you are ready to feed it with data and search.

## Installation

Install the package from PyPi:
```
pip install django-native-search
```

The package will be installed with all its dependencies including `django-expression-index`.

## Setup
Setting up the search in basic configuration is quite simple.
### 1. Register the app
Add `django_native_search` to `INSTALLED_APPS` in your settings:

```python
INSTALLED_APPS = [
    ...
    'django_native_search.apps.DjangoNativeSearch',
    ...
]
```
### 2. Define your Index Model
Create a new app or in existing app, in your `models.py`, define an index model. In this example we are creating a simple index for `books.Book` model:
```python
from django_native_search.models import IndexEntry

class BookIndexEntry(IndexEntry):
    object = models.OneToOneField('books.Book', on_delete=models.CASCADE)
    search_template='search_index/book.txt'
```
The `object` field defines a relation to a model which is being indexed. 
The engine uses `search_template` to render the text with `object` variable in template context. 
By default the rendered text is tokenized with by `re.searchall(r'[^\s"]+', text)`. 
You can change this behavior by overriding `tokenize` class method in your index model. 
All extracted tokens are stored in the index of respective indexed model instance.
```python
import re

class BookIndexEntry(IndexEntry):
    ...
    @classmethod
    def tokenize(self, text):
        return re.findall(r"[^\W_]+(['_]?[^\W_]+)*", text)
```
#### Index for multiple models
It is also possible to create index for multiple models by using model inheritance. Create a single concrete descendant model of `IndexEntry` with multiple descendants for each indexed model. 

You can add some common fields to this model to be used for filtering the entries, but do not add `objects` field. Then create descendants of your IndexEntry model. Each of the derived classes should have `object` field which points to a model to be indexed and a `search_template`.

I would advise to put some additional fields to your root index model, to be able to filter entries of any kind or display the results without additional query for descendant models. You can fill the fields with data by overriding your `save` method in your index model.

It should also be possible to use `GenericForeignKey` to define the `object` field, but I haven't tried it.

#### Multiple indexes
Each direct descendant of `IndexEntry` is a separate index, so you can have multiple independent 
indexes in your site.
### 3. Prepare the database
Run the well known commands:
```
manage.py makemigrations
manage.py migrate
```
The index was tested with `sqlite` and `PostreSQL`.
## Usage
Usually you use your index to do full-text seach within your data. Just remember to fill it with data first.
### Feeding the index with data
The only thing you need to do is to create your `IndexEntry` descendant model instance and save it. 
```python
from book_index.models import BookIndexEntry
from books.models import Book

for book in Book.objects.all():
    BookIndexEntry(object=book).save()
```

There is a convenient shortcut for indexing querysets:
```python
from book_index.models import BookIndexEntry
BookIndexEntry.objects.rebuild()
```
You can override `get_index_queryset` method in your class to do `select_related` or `filter` 
or anything you need, before passing the queryset for indexing. 

You can call the `rebuild` method on your index model root class manager, to rebuild all descendant 
index models.

Probably you would like to create you own management command to run the indexing, but actually 
you would not use it...

#### Runtime index updates
The indexing should be fast enough to be executed in runtime on every save of the indexed model. 
Just connect a handler to `post_save` signal:
```python
from django.db.models.signals import post_save

class BookIndexEntry(IndexEntry):
    ...
    @classmethod
    def update_index(cls, instance, **kwargs):
        cls.objects.refresh([instance])
        
post_save.connect(BookIndexEntry.update_index, sender=Book)
```
Now your index will be always up-to-date.

### Searching
You can search the index by calling the manager's `search` method. The query is tokenized using 
the same `tokenize` method as when indexing. All tokens must be found in a document to consider it 
matched:
```python
qs = BookIndexEntry.objects.search('Monty Python')
```
This will return a `QuerySet` of `BookIndexEntry` which contain both "Monty" and "Python" case 
sensitively. If you want your search to be case-insensitive, then provide the query in lowercase:
```python
qs = BookIndexEntry.objects.search('circus')
```
You can filter the search results, just as any other `QuerySet`:
```python
qs = BookIndexEntry.objects.search('circus').filter(object__release_date__year__gt=1970)
```
By default search returns matches only for whole words. If there is a single keyword in a query, 
the engine does a substring search, so search results may contain documents with words matching 
the keyword or containing it.

For example searching for "yth" may return documents containing "python", "pythonic", "myth", 
"demythologization".

Substring search works fine in `sqlite`. In `PostgreSQL` there is a problem with using the db index,
so the searching might be too slow.

Putting multiple words inside quotes forces searching for colocation of these words.
```python
qs = BookIndexEntry.objects.search('"Monty Python\'s Flying Circus"')
```
This will return a `QuerySet` of `BookIndexEntry` which contain word "Monty" followed by "Python's", 
followed by "Flying", followed by "Circus".

### Search form
There is `SearchFormMixin` available to easily to create your search view:
```python
from django.views.generic.base import TemplateView
from book_index.models import BookIndexEntry
from django_native_search.forms import SearchFormMixin, searchform_factory

class SearchView(SearchFormMixin, TemplateView):
    template_name = "books_index/search.html"
    form_class = searchform_factory(BookIndexEntry)
```
The `searchform_factory` function will use all fields with `db_index = True` in `BookIndexEntry` 
to create `MultipleChoiceField` in your form. The fields can be used to filter the results. 
Each filtering field in your form will contain all possible values of the field in the database.

### Search template
The templated referred by `template_name` is rendered with `form` containing the form instance and 
`results` containing the queryset of search results if form is valid. 
```django
{% block content %}
    <h2>Search</h2>
    <form method="get" action=".">
        <table>
            {{ form }}
            <tr>
                <td>&nbsp;</td>
                <td>
                    <input type="submit" value="Search" class="btn"/>
                </td>
            </tr>
        </table>
    </form>
    {% if form.is_valid %}
        <br/>
        <h3>Found {{ results.count }} results</h3>
        <ul>
            {% for result in results %}
                <li class = "search-result">
                    <ul>
                        <li class="result-link">
                            <a href="{{result.object.get_absolute_url}}">{{ result.object.title }}</a>
                        </li>
                        <li class="result-excerpt'>
                            {{result.excerpt}}
                        </li>
                    </ul>
                </li>
            {% endfor %}
        </ul>
    {% endif %}
{% endblock %}
```
The `excerpt` member of index entry instance returns a fragment of the indexed document with 
occurrences of search keywords hihghted with `<em>`.
### Settings
There are serveral settings to tweak the search engine.
#### `SEARCH_MIN_SUBSTR_LENGTH`
Default : `2`

Minimum number of characters in keyword to run substring search.
#### `SEARCH_MAX_SUBTSTR_COUNT_IN_QUERY`
Default : `300`

Maximum number of indexed words containing the substring to run substring search.
#### `SEARCH_MAX_EXCERPT_FRAGMENTS`
Default : `5`

Maximum number of fragments containing keywords to be returned in excerpt.
#### `SEARCH_EXCERPT_FRAGMENT_START_OFFSET`
Default : `-2`

Offset of excerpt fragment start.
#### `SEARCH_EXCERPT_FRAGMENT_END_OFFSET`
Default : `5`

Offset of excerpt fragment end.
#### `SEARCH_MAX_RANKING_KEYWORDS_COUNT`
Default : `3`

Maximum number of keywords to be used for ranking the results. If the query contains more keywords, 
only the first ones will be used to calculate the ranking of results. 
### Search API
To be described...

Look into the code to check what you can do with it.
### Performance
Despite the naive design, the index performs surpsisingly well, even with quite large datasets. 
It can search through 100k documents containing 10M words in a fraction of a second.