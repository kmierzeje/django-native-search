# django-native-search

django-native-search implements basic search engine for Django models.

The engine itself uses Django ORM to manage its index, so no additional backend is needed for searching to work. Just create a model for index, run `makemigrations` and `migrate` and you are ready to feed it with data and search.